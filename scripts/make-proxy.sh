#!/usr/bin/env bash
# scripts/make-proxy.sh — build the synthetic proxy checkpoint for claim 4.1.
#
# In the style of scripts/run-smoke.sh: everything effectful happens inside
# flashnext:dev, the host half only decides whether to run at all.
#
# WHAT THIS BUILDS
#   A tiny, architecturally FAITHFUL stand-in for the workload checkpoint at
#   /var/tmp/flashnext-proxy — same `architectures` identifier, a handful of
#   decoder layers, 8 routed experts in block-FP8 with weight_block_size
#   128 x 128, and a small per-layer engram lookup table written under the
#   exact shard tensor name the fork's mmap path matches. Every value is
#   synthetic; NO REAL WEIGHT BYTE IS EVER READ. The workload checkpoint is
#   opened header-only (safetensors headers are JSON at the head of the file)
#   purely to learn the tensor INVENTORY — names, shapes, dtypes — so this
#   script never has to invent an architecture it cannot see.
#
# WHY IT REUSES THE FORK INSTEAD OF INVENTING
#   Three things must agree exactly with the engine or the proxy proves
#   nothing, so all three are taken from the fork checkout at /opt/vllm
#   rather than restated here:
#     * the shard tensor NAME — derived mechanically from the fork's own
#       `_SHARD_RE` in ple_mmap, then validated back through that same regex;
#     * the shard FILE naming convention — lifted from the fork's
#       tests/models/qwen4_exp/test_ple_mmap.py fixture;
#     * the safetensors header parse — the fork's own `parse_safetensors_header`
#       when it is importable.
#   The build ends by calling the fork's `discover_shards()` on what it wrote:
#   if the engine cannot find the table, the build fails here rather than at
#   serve time.
#
# IDEMPOTENCE
#   A manifest (flashnext-proxy.json) records the shape signature and every
#   file written. A rerun whose signature matches and whose files are all
#   present SKIPS the rebuild and touches nothing. FN_PROXY_FORCE=1 overrides.
#
# NOT IN SCOPE: serving. scripts/run-proxy.sh owns first light.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
IMAGE="${FN_IMAGE:-flashnext:dev}"
PROXY_DIR="${FN_PROXY_DIR:-/var/tmp/flashnext-proxy}"
WORKLOAD_DIR="${FN_MODEL_DIR:-/var/lib/local-models/flashnext-fp8}"
# The fork checkout inside the image (container/Containerfile: cloned to
# /opt/vllm, or bind-mounted there by container/build.sh).
FORK_DIR="${FN_FORK_DIR:-/opt/vllm}"

# --- proxy shape -------------------------------------------------------------
# A handful of layers, not one: the workload alternates 3x linear_attention
# with 1x full_attention (full_attention_interval 4), so four layers is the
# smallest prefix that exercises BOTH attention kinds and still carries a
# MoE block. Eight experts is the agreed routed width; the block-FP8 tile is
# 128 x 128, matching the workload's quantization_config.weight_block_size.
PROXY_LAYERS="${FN_PROXY_LAYERS:-4}"
PROXY_EXPERTS="${FN_PROXY_EXPERTS:-8}"
PROXY_BLOCK="${FN_PROXY_BLOCK:-128}"
# split_ngram_parts for the proxy table. The workload splits the engram table
# into 512 row blocks; 4 keeps the proxy table to a few files per layer while
# still crossing shard boundaries at gather time (the row arithmetic the fork
# pins in test_shard_mapping_matches_upstream_checkpoint_math_at_boundaries).
PROXY_SPLIT="${FN_PROXY_SPLIT_NGRAM_PARTS:-4}"

MANIFEST_NAME="flashnext-proxy.json"
MANIFEST="$PROXY_DIR/$MANIFEST_NAME"
# Bump the trailing version when the generator's OUTPUT shape changes, so an
# older tree's checkpoint is rebuilt rather than silently reused.
SIGNATURE="layers=$PROXY_LAYERS experts=$PROXY_EXPERTS block=${PROXY_BLOCK}x${PROXY_BLOCK} split=$PROXY_SPLIT gen=1"

log() { echo "make-proxy: $*" >&2; }

# --- 1. rebuild detection ----------------------------------------------------
if [ "${FN_PROXY_FORCE:-0}" != "1" ] && [ -f "$MANIFEST" ]; then
  if python3 - "$MANIFEST" "$PROXY_DIR" "$SIGNATURE" <<'PY'
import json, os, sys
manifest, root, signature = sys.argv[1:4]
try:
    m = json.load(open(manifest))
except (OSError, ValueError) as e:
    print(f"make-proxy: manifest unreadable ({e}); rebuilding", file=sys.stderr)
    sys.exit(1)
if m.get("signature") != signature:
    print(f"make-proxy: signature drift "
          f"({m.get('signature')!r} -> {signature!r}); rebuilding", file=sys.stderr)
    sys.exit(1)
missing = [f for f in m.get("files", []) if not os.path.isfile(os.path.join(root, f))]
if missing:
    print(f"make-proxy: {len(missing)} manifest file(s) missing "
          f"(first: {missing[0]}); rebuilding", file=sys.stderr)
    sys.exit(1)
sys.exit(0)
PY
  then
    log "existing checkpoint at $PROXY_DIR matches the signature; rebuild skipped"
    log "  (FN_PROXY_FORCE=1 to rebuild)"
    exit 0
  fi
fi

# --- 2. preconditions --------------------------------------------------------
# The proxy's tensor inventory is DERIVED from the workload checkpoint's
# safetensors headers. Without it this script would have to invent an
# architecture, which is exactly what it refuses to do.
if [ ! -f "$WORKLOAD_DIR/config.json" ]; then
  log "FATAL: workload checkpoint absent at $WORKLOAD_DIR (no config.json)."
  log "  The proxy inventory is derived header-only from the staged workload;"
  log "  run scripts/stage-weights.sh first, or point FN_MODEL_DIR at a copy."
  exit 2
fi

mkdir -p "$PROXY_DIR"
log "building the proxy checkpoint at $PROXY_DIR ($SIGNATURE)"

# --- 3. the build, inside the image ------------------------------------------
# The workload is mounted READ-ONLY: this script reads its headers and its
# tokenizer, never its tensor data.
podman run --rm -i --device /dev/kfd --device /dev/dri \
  --security-opt seccomp=unconfined --ipc=host \
  -e FN_PROXY_LAYERS="$PROXY_LAYERS" \
  -e FN_PROXY_EXPERTS="$PROXY_EXPERTS" \
  -e FN_PROXY_BLOCK="$PROXY_BLOCK" \
  -e FN_PROXY_SPLIT_NGRAM_PARTS="$PROXY_SPLIT" \
  -e FN_PROXY_SIGNATURE="$SIGNATURE" \
  -e FN_PROXY_MANIFEST_NAME="$MANIFEST_NAME" \
  -e FN_FORK_DIR="$FORK_DIR" \
  -v "$WORKLOAD_DIR:/workload:ro" \
  -v "$PROXY_DIR:/proxy" "$IMAGE" python3 - <<'PY'
"""Synthesize the proxy checkpoint from the workload's tensor inventory.

Import-safe: every effectful step sits under the __main__ guard at the
bottom, so tests/test_proxy_tooling.py can extract this body and exercise
the pure planning functions without touching a filesystem.
"""
import hashlib
import importlib
import importlib.util
import json
import os
import re
import shutil
import struct
import sys

PROXY = "/proxy"
WORKLOAD = "/workload"
FORK = os.environ.get("FN_FORK_DIR", "/opt/vllm")
# The fork's test scaffolding lives here; tests/models/qwen4_exp/ carries the
# PLE fixture whose conventions this build reuses.
FORK_TESTS = os.path.join(FORK, "tests")
FORK_PLE_TEST = os.path.join(FORK_TESTS, "models", "qwen4_exp", "test_ple_mmap.py")

LAYERS = int(os.environ.get("FN_PROXY_LAYERS", "4"))
EXPERTS = int(os.environ.get("FN_PROXY_EXPERTS", "8"))
BLOCK = int(os.environ.get("FN_PROXY_BLOCK", "128"))
SPLIT = int(os.environ.get("FN_PROXY_SPLIT_NGRAM_PARTS", "4"))
SIGNATURE = os.environ.get("FN_PROXY_SIGNATURE", "")
MANIFEST_NAME = os.environ.get("FN_PROXY_MANIFEST_NAME", "flashnext-proxy.json")

# Roll a new safetensors file once the pending tensors pass this many bytes.
FILE_BYTES_TARGET = 1 << 30
# The fork's own fixture convention (tests/models/qwen4_exp/test_ple_mmap.py):
# one file per (layer, shard). Used only if the live fixture cannot be read.
PLE_FILE_FMT_FALLBACK = "model-ple-{layer_idx}-{shard_index:05d}.safetensors"
PLE_FILE_FMT_RE = re.compile(r"model-ple-\{[^{}]*\}-\{[^{}]*\}\.safetensors")
# Documented shape of the fork's shard matcher; used only to fail loudly if
# the fork's own regex has drifted out from under this generator.
SHARD_RE_FALLBACK = re.compile(
    r"layers\.(\d+)\.ple\.ple_embedding\.ngram_embedding\.shard_(\d+)\.weight$")

# Decoder-layer index. Anchored on a dot (or string start) so a vision tower's
# own `blocks.N.`/`layers.N.` naming is only matched when it truly shares the
# suffix; VISION_PREFIXES below carries the exclusion.
DECODER_LAYER_RE = re.compile(r"(?:^|\.)layers\.(\d+)\.")
EXPERT_RE = re.compile(r"(?:^|\.)experts\.(\d+)\.")
VISION_PREFIXES = ("visual.", "vision_tower.", "vision_model.")

SAFETENSORS_DTYPES = {
    "F64": "float64", "F32": "float32", "F16": "float16", "BF16": "bfloat16",
    "F8_E4M3": "float8_e4m3fn", "F8_E5M2": "float8_e5m2",
    "I64": "int64", "I32": "int32", "I16": "int16", "I8": "int8",
    "U8": "uint8", "BOOL": "bool",
}


# --- pure planning helpers (unit-tested in tests/test_proxy_tooling.py) ------

def shard_name_template(shard_re):
    """Turn the fork's `_SHARD_RE` into the tensor-name template it matches.

    The name the generator writes is DERIVED from the engine's own matcher,
    so the two can never drift apart silently: `\\.` becomes a literal dot,
    each `(\\d+)` becomes a substitution slot, the end anchor is dropped.
    """
    pattern = shard_re.pattern if hasattr(shard_re, "pattern") else str(shard_re)
    template = pattern[:-1] if pattern.endswith("$") else pattern
    template = template.replace(r"(\d+)", "{}").replace(r"\.", ".")
    if template.count("{}") != 2 or "\\" in template:
        raise RuntimeError(
            "make-proxy: the fork's shard regex is no longer a plain "
            f"two-index literal pattern ({pattern!r}); refusing to guess the "
            "tensor name the engine expects")
    return template


def shard_plan(vocab_size, split_parts):
    """Row block boundaries, in the fork's own checkpoint arithmetic.

    `shard_size = (org_vocab_size + split_ngram_parts - 1) // split_ngram_parts`
    is written verbatim twice in the fork (the ple_layer loader and
    ple_mmap._attach_table); reproducing it is what makes the proxy table
    discoverable and correctly indexed at gather time.

    Every block is full width. The runtime lookup is `shard = uniq //
    shard_size`, so coverage stops at `shard_size * split_parts` whatever the
    files hold — a short final shard would buy no extra reachable row, and an
    over-long one would index out of range.
    """
    if split_parts < 1:
        raise RuntimeError(f"make-proxy: split_ngram_parts must be >= 1, "
                           f"got {split_parts}")
    shard_size = (vocab_size + split_parts - 1) // split_parts
    return [(idx, idx * shard_size, shard_size) for idx in range(split_parts)]


def keep_tensor(name, layers, experts):
    """Is this workload tensor part of the proxy's (much smaller) inventory?

    Decoder layers past the proxy's depth and experts past its routed width
    are dropped; the vision tower and every non-layer tensor are kept whole.
    """
    if name.startswith(VISION_PREFIXES):
        return True
    layer = DECODER_LAYER_RE.search(name)
    if layer and int(layer.group(1)) >= layers:
        return False
    expert = EXPERT_RE.search(name)
    if expert and int(expert.group(1)) >= experts:
        return False
    return True


def set_config_key(cfg, key, value, force=False):
    """Set `key` wherever it already lives (top level or a nested text config).

    Returns the number of places written; 0 means the key is absent from this
    architecture and the caller decides whether that is fatal.
    """
    written = 0
    for scope in (cfg, cfg.get("text_config"), cfg.get("language_config")):
        if isinstance(scope, dict) and key in scope:
            scope[key] = value
            written += 1
    if not written and force:
        cfg[key] = value
        written = 1
    return written


def get_config_key(cfg, key, default=None):
    for scope in (cfg, cfg.get("text_config"), cfg.get("language_config")):
        if isinstance(scope, dict) and key in scope:
            return scope[key]
    return default


def surviving_ignore_list(modules, layers, experts):
    """Filter quantization_config.modules_to_not_convert to the proxy's tree.

    The workload's list is ~943 entries naming layers this proxy does not
    have; leaving them in would be harmless but dishonest about the shape.
    """
    return [m for m in modules if keep_tensor(m, layers, experts)]


# --- fork scaffolding -------------------------------------------------------

def load_ple_mmap():
    """The fork's mmap module, wherever the port put it.

    Patch 0010 relocates ple_mmap to `common/`; upstream #54129 has it under
    `nvidia/`. Try the ported location first.
    """
    tried = []
    for mod in ("vllm.models.qwen4_exp.common.ple_mmap",
                "vllm.models.qwen4_exp.nvidia.ple_mmap"):
        try:
            return importlib.import_module(mod), mod
        except Exception as e:  # noqa: BLE001 - any import failure is a miss
            tried.append(f"{mod}: {e.__class__.__name__}: {e}")
    raise RuntimeError("make-proxy: the fork's ple_mmap module is not "
                       "importable; tried\n  " + "\n  ".join(tried))


def ple_file_format():
    """The fork fixture's shard FILE naming convention.

    Read out of tests/models/qwen4_exp/test_ple_mmap.py as source text rather
    than imported: the fixture is a pytest module and importing it drags in
    the whole test collection machinery for one format string.
    """
    try:
        source = open(FORK_PLE_TEST, encoding="utf-8").read()
    except OSError as e:
        return PLE_FILE_FMT_FALLBACK, f"documented-fallback ({e.__class__.__name__})"
    found = PLE_FILE_FMT_RE.search(source)
    if not found:
        return PLE_FILE_FMT_FALLBACK, "documented-fallback (pattern not in fixture)"
    return found.group(0), f"fork-fixture:{os.path.relpath(FORK_PLE_TEST, FORK)}"


def read_header(path, parse):
    """Tensor inventory of one safetensors file. HEADER ONLY — never the data."""
    if parse is not None:
        # The fork's parse_safetensors_header returns (metadata, data_offset).
        header, _offset = parse(path)
        return header
    with open(path, "rb") as fh:
        (size,) = struct.unpack("<Q", fh.read(8))
        return json.loads(fh.read(size))


# --- synthesis --------------------------------------------------------------

def make_tensor(torch, dtype_str, shape, generator):
    """A synthetic tensor of the recorded dtype and shape.

    FP8 storage is filled by byte pattern rather than by casting: 0x00-0x37
    is the small-positive corner of e4m3, so the result is finite by
    construction (only 0x7F/0xFF are NaN) without depending on a CPU fp8
    cast kernel being present in the pinned torch build.
    """
    torch_dtype = getattr(torch, dtype_str, None)
    if torch_dtype is None:
        raise RuntimeError(f"make-proxy: torch has no dtype {dtype_str}")
    if dtype_str.startswith("float8"):
        raw = torch.randint(0, 0x38, shape, generator=generator, dtype=torch.uint8)
        return raw.view(torch_dtype)
    if dtype_str in ("float64", "float32", "float16", "bfloat16"):
        # Scales must be positive and near unity; everything else is a small
        # zero-mean weight so the kernels see real numbers, not zeros.
        return (torch.randn(shape, generator=generator, dtype=torch.float32)
                .mul_(0.02).to(torch_dtype))
    if dtype_str == "bool":
        return torch.zeros(shape, dtype=torch.bool)
    return torch.zeros(shape, dtype=torch_dtype)


def main():
    import torch
    from safetensors.torch import save_file

    ple_mmap, ple_mmap_module = load_ple_mmap()
    shard_re = getattr(ple_mmap, "_SHARD_RE", SHARD_RE_FALLBACK)
    parse_header = getattr(ple_mmap, "parse_safetensors_header", None)
    template = shard_name_template(shard_re)
    ple_file_fmt, ple_file_fmt_source = ple_file_format()

    generator = torch.Generator().manual_seed(20260830)

    # --- inventory: the workload's headers, never its data -------------------
    sources = sorted(f for f in os.listdir(WORKLOAD) if f.endswith(".safetensors"))
    if not sources:
        raise RuntimeError(f"make-proxy: no safetensors files under {WORKLOAD}")
    inventory, ple_shard_rows = {}, {}
    ple_prefix, ple_dtype, ple_layer = None, None, None
    for fname in sources:
        header = read_header(os.path.join(WORKLOAD, fname), parse_header)
        for name, entry in header.items():
            if name == "__metadata__" or not isinstance(entry, dict):
                continue
            found = shard_re.search(name)
            if found:
                # Regenerated at the proxy's split, so only the row width, the
                # dtype and the name prefix are taken from the workload.
                if ple_prefix is None:
                    ple_prefix, ple_dtype = name[:found.start()], entry["dtype"]
                    # The engram table lives on whichever layer carries the
                    # shards (layer 1 in this checkpoint), not necessarily 0.
                    ple_layer = int(found.group(1))
                if int(found.group(1)) == ple_layer:
                    ple_shard_rows[int(found.group(2))] = list(entry["shape"])
                continue
            if keep_tensor(name, LAYERS, EXPERTS):
                inventory[name] = (entry["dtype"], list(entry["shape"]))
    if not ple_shard_rows:
        raise RuntimeError(
            "make-proxy: no engram shard tensor in the workload headers "
            f"matched {shard_re.pattern!r}; the table cannot be reproduced")

    workload_rows = sum(shape[0] for shape in ple_shard_rows.values())
    row_width = next(iter(ple_shard_rows.values()))[1]

    # --- config: the workload's, shrunk --------------------------------------
    cfg = json.load(open(os.path.join(WORKLOAD, "config.json")))
    vocab_size = int(get_config_key(cfg, "vocab_size", workload_rows))
    set_config_key(cfg, "num_hidden_layers", LAYERS, force=True)
    layer_types = get_config_key(cfg, "layer_types")
    if isinstance(layer_types, list):
        set_config_key(cfg, "layer_types", layer_types[:LAYERS])
    experts_written = 0
    for key in ("num_experts", "n_routed_experts", "num_routed_experts"):
        experts_written += set_config_key(cfg, key, EXPERTS)
    if not experts_written:
        raise RuntimeError("make-proxy: the workload config names no routed "
                           "expert count; refusing to guess one")
    set_config_key(cfg, "split_ngram_parts", SPLIT, force=True)
    set_config_key(cfg, "num_nextn_predict_layers", 0)
    quant = get_config_key(cfg, "quantization_config") or {}
    quant["weight_block_size"] = [BLOCK, BLOCK]
    quant["modules_to_not_convert"] = surviving_ignore_list(
        quant.get("modules_to_not_convert") or [], LAYERS, EXPERTS)
    set_config_key(cfg, "quantization_config", quant, force=True)

    # --- write ---------------------------------------------------------------
    files, written_bytes = [], 0
    pending, pending_bytes = {}, 0

    def flush(index):
        nonlocal pending, pending_bytes, written_bytes
        if not pending:
            return index
        fname = f"model-{index:05d}.safetensors"
        save_file(pending, os.path.join(PROXY, fname))
        files.append(fname)
        written_bytes += os.path.getsize(os.path.join(PROXY, fname))
        pending, pending_bytes = {}, 0
        return index + 1

    index = 0
    for name, (dtype_str, shape) in sorted(inventory.items()):
        torch_dtype = SAFETENSORS_DTYPES.get(dtype_str)
        if torch_dtype is None:
            raise RuntimeError(f"make-proxy: unmapped safetensors dtype "
                               f"{dtype_str!r} on {name}")
        tensor = make_tensor(torch, torch_dtype, shape, generator)
        pending[name] = tensor
        pending_bytes += tensor.numel() * tensor.element_size()
        if pending_bytes >= FILE_BYTES_TARGET:
            index = flush(index)
    index = flush(index)

    # The engram table: one file per (layer, shard), the fork fixture's shape.
    ple_torch_dtype = SAFETENSORS_DTYPES.get(ple_dtype)
    if ple_torch_dtype is None or not ple_torch_dtype.startswith("float8"):
        raise RuntimeError(
            f"make-proxy: the workload engram table is {ple_dtype!r}; the "
            "fork's mmap path admits only F8_E4M3 (_FP8_DTYPES)")
    table_plan = shard_plan(vocab_size, SPLIT)
    table_rows = table_plan[-1][1] + table_plan[-1][2]
    for layer_idx in range(LAYERS):
        for shard_index, _start, rows in table_plan:
            tensor_name = ple_prefix + template.format(layer_idx, shard_index)
            if not shard_re.search(tensor_name):
                raise RuntimeError(
                    f"make-proxy: generated name {tensor_name!r} does not "
                    "match the fork's own shard matcher")
            fname = ple_file_fmt.format(layer_idx=layer_idx,
                                        shard_index=shard_index)
            save_file(
                {tensor_name: make_tensor(torch, ple_torch_dtype,
                                          [rows, row_width], generator)},
                os.path.join(PROXY, fname))
            files.append(fname)
            written_bytes += os.path.getsize(os.path.join(PROXY, fname))
        # The block scale the loader intercepts and hands to set_weight_scale().
        scale_name = ple_prefix + f"layers.{layer_idx}.ple.ple_embedding." \
                                  "ngram_embedding.weight_scale"
        pending[scale_name] = torch.ones([1], dtype=torch.float32)
    index = flush(index)

    # --- tokenizer and config -------------------------------------------------
    for entry in sorted(os.listdir(WORKLOAD)):
        if entry.endswith((".safetensors", ".sha256", ".bin", ".pt")):
            continue
        # A stale weight index would send the loader looking for the
        # workload's file names; the fork's discover_shards parses headers
        # directly and needs none.
        if entry in ("config.json", "MANIFEST.sha256",
                     "model.safetensors.index.json"):
            continue
        src = os.path.join(WORKLOAD, entry)
        if os.path.isfile(src):
            shutil.copyfile(src, os.path.join(PROXY, entry))
            files.append(entry)
    with open(os.path.join(PROXY, "config.json"), "w") as fh:
        json.dump(cfg, fh, indent=1)
    files.append("config.json")

    # --- self-check: can the ENGINE find what we wrote? ----------------------
    discovered = ple_mmap.discover_shards(PROXY)
    found_layers = sorted(discovered)
    expected_layers = list(range(LAYERS))
    if found_layers != expected_layers:
        raise RuntimeError(f"make-proxy: discover_shards found layers "
                           f"{found_layers}, expected {expected_layers}")
    for layer_idx, shards in discovered.items():
        # discover_shards yields _LayerShards dataclasses; the shard indices
        # are the keys of their .shards dict.
        if sorted(shards.shards) != list(range(SPLIT)):
            raise RuntimeError(f"make-proxy: layer {layer_idx} discovered "
                               f"shards {sorted(shards.shards)}, "
                               f"expected 0..{SPLIT - 1}")

    manifest = {
        "signature": SIGNATURE,
        "checkpoint": PROXY,
        "architectures": cfg.get("architectures"),
        "layers": LAYERS,
        "experts": EXPERTS,
        "weight_block_size": [BLOCK, BLOCK],
        "split_ngram_parts": SPLIT,
        "table": {
            "tensor_name_template": ple_prefix + template,
            "dtype": ple_dtype,
            "rows_total": table_rows,
            "row_width": row_width,
            "workload_rows_total": workload_rows,
            "file_format": ple_file_fmt,
            "file_format_source": ple_file_fmt_source,
            "shard_matcher_source": ple_mmap_module,
            "discovered_layers": len(discovered),
        },
        "bytes": written_bytes,
        "tensors": len(inventory) + LAYERS * (SPLIT + 1),
        "files": sorted(set(files)),
        "derived_from": {
            "workload_config": os.path.join(WORKLOAD, "config.json"),
            "header_only": True,
            "weight_bytes_read": 0,
        },
    }
    with open(os.path.join(PROXY, MANIFEST_NAME), "w") as fh:
        json.dump(manifest, fh, indent=1)
    print(json.dumps({k: v for k, v in manifest.items() if k != "files"},
                     indent=1))
    print(f"make-proxy: {len(manifest['files'])} files, "
          f"{written_bytes} bytes at {PROXY}", file=sys.stderr)


if __name__ == "__main__":
    sys.exit(main() or 0)
PY

log "proxy checkpoint ready: $PROXY_DIR (manifest: $MANIFEST)"
log "next: scripts/run-proxy.sh serves it single-node and writes results/receipts/proxy.json"
