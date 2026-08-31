#!/usr/bin/env python3
"""host/fn-tp2-guards.py — the TP=2 fail-closed gate, BEFORE the first launch.

RUN3-BRIEF §15.4 (the eight parity and divisibility assertions) and §14.5
(selection-time expert sharding on both MoE paths). Every check here is
ARITHMETIC over the checkpoint header and the serve argv: no GPU, no weight
load, no engine import, no network. It runs in under a second, and it runs
before fn-cluster-up.sh spends twenty-five minutes in LLVM to find out the
same thing the hard way — or, far worse, does not find it out at all.

WHY THIS FILE EXISTS. The precedent is exact: the reference implementation
computes ``n_groups // 2`` with no parity check, so an odd head count silently
drops a group. Nothing raises. The text stays fluent. Their own record of the
same defect class dropped the shared expert from every decode layer and the
completions remained plausible — the failure mode of TP=2 on this stack is not
a crash, it is CONFIDENT WRONG OUTPUT, and confident wrong output is not
something a receipt can catch after the fact. So every split this
configuration performs is asserted here, ahead of the launch, and a violation
is a refusal to serve rather than a warning in a log nobody reads at 03:00.

THE EIGHT (each fails closed; the report names every one, pass or fail):

  A1 moe-routed-expert-parity   the routed expert count divides by TP
  A2 moe-shared-expert-policy   the shared expert is ODD: replicate, fold once
  A3 qsa-head-parity            QSA query heads divide by TP, block-aligned
  A4 qsa-gqa-group-integrity    KV heads divide by TP; no group straddles
  A5 gdn-head-parity            GDN key/value heads divide by TP, groups whole
  A6 fp8-block-alignment        every per-rank slice boundary is a block edge
  A7 gate-schedule-from-tensors the QSA/GDN schedule comes from TENSOR
                                PRESENCE, never from a layer-index formula
  A8 rank-derivation-parity     both twins derive byte-identical guard inputs

THEN §14.5, the expert-sharding block:

  S1 selection-time-sharding    the serve argv shards at SELECTION, not by
                                masking full-width expert weights afterwards
  S2 both-moe-paths-sharded     EVERY MoE stack in the checkpoint is covered —
                                the main stack and the one-token draft stack
  S3 distinct-shard-checksums   the two ranks' shard digests DIFFER
  S4 predicted-rank-residency   per-rank bytes predicted from the header,
                                asserted against the P11 bound before loading

Exit status is 0 only when every check passes. The JSON report goes to
--report (default $FN_STATE_DIR/tp2-guards.json), where scripts/run-tp2.sh
folds it into the tp2 receipt.
"""

import argparse
import hashlib
import json
import os
import re
import struct
import subprocess
import sys

# safetensors dtype -> bytes per element. The FP8 pair is what this checkpoint
# stores its routed experts in; the rest is the bf16 remainder.
DTYPE_BYTES = {
    "BOOL": 1, "U8": 1, "I8": 1, "F8_E4M3": 1, "F8_E5M2": 1,
    "I16": 2, "U16": 2, "F16": 2, "BF16": 2,
    "I32": 4, "U32": 4, "F32": 4,
    "I64": 8, "U64": 8, "F64": 8,
}

GIB = 2 ** 30

# A tensor whose leading dimension is split across ranks (column-parallel) vs
# one whose trailing dimension is (row-parallel). Named by the suffixes this
# checkpoint actually carries — see the header walk, not a guess.
COLUMN_PARALLEL = ("q_proj", "k_proj", "v_proj", "gate_proj", "up_proj",
                   "in_proj_qkv", "in_proj_a", "in_proj_b", "in_proj_z",
                   "index_qk_proj")
ROW_PARALLEL = ("o_proj", "down_proj", "out_proj")

EXPERT_RE = re.compile(r"^(?P<stack>.*?)\.layers\.(?P<layer>\d+)"
                       r"\.mlp\.experts\.(?P<expert>\d+)\.")
LAYER_RE = re.compile(r"^(?P<stack>.*?)\.layers\.(?P<layer>\d+)\.(?P<rest>.*)$")


class GuardFailure(Exception):
    """A guard could not be EVALUATED. Unevaluable is a failure, never a skip."""


# --- reading the checkpoint, header only --------------------------------------

def read_config(model_dir):
    """config.json, with the text tower unwrapped.

    Every key this file reads is resolved through an alias list and a missing
    key is fatal: a guard that silently defaults an unknown head count is the
    exact silence this file exists to remove.
    """
    path = os.path.join(model_dir, "config.json")
    try:
        with open(path) as fh:
            raw = json.load(fh)
    except OSError as exc:
        raise GuardFailure(f"cannot read {path}: {exc}") from exc
    cfg = dict(raw)
    cfg.update(raw.get("text_config") or {})
    quant = raw.get("quantization_config") or cfg.get("quantization_config") or {}
    cfg["_quantization_config"] = quant
    return cfg


def require(cfg, names, what):
    for name in names:
        if cfg.get(name) is not None:
            return cfg[name]
    raise GuardFailure(
        f"{what}: none of {list(names)} is present in the checkpoint config; "
        "refusing to guess a split factor")


def read_headers(model_dir):
    """Every tensor's (bytes, dtype, shape), from the safetensors HEADERS.

    This is the whole point of predicting residency offline: a safetensors
    file opens with an 8-byte little-endian header length followed by that
    many bytes of JSON. 131 shard files answer in well under a second and not
    one weight byte is read, so the prediction lands BEFORE the load it is
    predicting.
    """
    tensors = {}
    files = sorted(f for f in os.listdir(model_dir) if f.endswith(".safetensors"))
    if not files:
        raise GuardFailure(f"no .safetensors shards under {model_dir}")
    for name in files:
        path = os.path.join(model_dir, name)
        with open(path, "rb") as fh:
            raw_len = fh.read(8)
            if len(raw_len) != 8:
                raise GuardFailure(f"{name}: truncated safetensors header")
            (header_len,) = struct.unpack("<Q", raw_len)
            header = json.loads(fh.read(header_len))
        for tensor, meta in header.items():
            if tensor == "__metadata__":
                continue
            start, end = meta["data_offsets"]
            tensors[tensor] = (end - start, meta["dtype"], tuple(meta["shape"]))
    return tensors


# --- derived facts -------------------------------------------------------------

def is_expert(name):
    return EXPERT_RE.match(name)


def is_table(name):
    """The engram table: mmap'd from NVMe, zero bytes GPU-resident (P11)."""
    return "ngram_embedding" in name


def expert_stacks(tensors):
    """{stack -> sorted expert ids}. There is more than one MoE stack.

    The main stack serves BOTH prefill and decode; the draft stack is the
    one-token entry point. Their first fix touched only the one-token path and
    prefill went unsharded — which they themselves record would have been
    'silently wrong'. Enumerating the stacks from the header is what makes
    "both paths" a countable claim instead of a hope.
    """
    stacks = {}
    for name in tensors:
        match = is_expert(name)
        if match:
            stacks.setdefault(match.group("stack"), set()).add(
                int(match.group("expert")))
    return {stack: sorted(ids) for stack, ids in sorted(stacks.items())}


def schedule_from_tensor_presence(tensors):
    """The QSA/GDN schedule, derived from WHICH TENSORS EXIST.

    Not from ``layer_idx % full_attention_interval``. The layout is
    heterogeneous, both ranks must agree on it exactly, and an index formula
    is a second source of truth that can drift from the checkpoint without
    anything noticing. A layer is QSA because it carries self_attn tensors and
    GDN because it carries linear_attn tensors; carrying both, or neither, is
    fatal here rather than mysterious later.
    """
    kinds = {}
    for name in tensors:
        match = LAYER_RE.match(name)
        if not match:
            continue
        key = (match.group("stack"), int(match.group("layer")))
        rest = match.group("rest")
        if rest.startswith("self_attn."):
            kinds.setdefault(key, set()).add("full_attention")
        elif rest.startswith("linear_attn."):
            kinds.setdefault(key, set()).add("linear_attention")
    schedule = {}
    for key, seen in sorted(kinds.items()):
        if len(seen) != 1:
            raise GuardFailure(
                f"layer {key[0]}.{key[1]} carries {sorted(seen) or 'no'} "
                "attention tensors; the gate schedule is not derivable from "
                "tensor presence")
        schedule[f"{key[0]}.{key[1]}"] = next(iter(seen))
    if not schedule:
        raise GuardFailure("no attention tensors found; schedule underivable")
    return schedule


def guard_inputs_digest(cfg, tensors, schedule, tp):
    """The digest A8 byte-compares across the twins.

    It covers everything the other seven assertions read — config scalars,
    every tensor name with its dtype and shape, the derived schedule, and the
    split factor — so a divergence anywhere in the guard's own inputs is one
    comparison, not seven.
    """
    payload = {
        "tp": tp,
        "config": {k: v for k, v in sorted(cfg.items())
                   if not k.startswith("_") and isinstance(
                       v, (int, float, str, bool, type(None)))},
        "tensors": [[name, tensors[name][1], list(tensors[name][2])]
                    for name in sorted(tensors)],
        "schedule": schedule,
    }
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(blob).hexdigest()


# --- the shard plan (selection-time expert range split) ------------------------

def shard_plan(tensors, stacks, tp):
    """Which tensors each rank owns, under a SELECTION-time expert split.

    Selection-time sharding means rank r owns whole experts
    [r*n/tp, (r+1)*n/tp) and the router's selected ids are mapped into that
    local range before any expert weight is touched. The alternative — keep
    every expert addressable on every rank and mask after selection —
    CONTRADICTS a rank holding only half, and is what produced their
    'arena alloc failed for moe_gate' and then the illegal memory access.

    Everything that is not a routed expert and not the mmap'd table is
    replicated: that is the conservative direction, and under-predicting
    residency is the one error this function must not make.
    """
    per_rank = len(next(iter(stacks.values()))) // tp if stacks else 0
    owned = [[] for _ in range(tp)]
    replicated = []
    table = []
    for name in sorted(tensors):
        if is_table(name):
            table.append(name)
            continue
        match = is_expert(name)
        if not match:
            replicated.append(name)
            continue
        expert = int(match.group("expert"))
        total = len(stacks[match.group("stack")])
        rank = expert // (total // tp)
        owned[min(rank, tp - 1)].append(name)
    return {"owned": owned, "replicated": replicated, "table": table,
            "experts_per_rank": per_rank}


def shard_digest(names, tensors):
    """One line of proof that a rank owns what it claims to own."""
    digest = hashlib.sha256()
    for name in names:
        size, dtype, shape = tensors[name]
        digest.update(f"{name}\t{dtype}\t{list(shape)}\t{size}\n".encode())
    return digest.hexdigest()


# --- the assertions -------------------------------------------------------------

def check(results, ident, ok, detail):
    results.append({"id": ident, "ok": bool(ok), "detail": detail})
    return bool(ok)


def run_assertions(cfg, tensors, tp, serve_argv, peer_digest, bound_gib,
                   kv_bytes):
    results = []
    quant = cfg["_quantization_config"]
    block = quant.get("weight_block_size")
    stacks = expert_stacks(tensors)
    schedule = schedule_from_tensor_presence(tensors)

    n_routed = int(require(cfg, ("num_experts", "n_routed_experts",
                                 "num_local_experts"), "routed expert count"))
    top_k = int(require(cfg, ("num_experts_per_tok", "top_k"), "router top-k"))
    n_heads = int(require(cfg, ("num_attention_heads",), "QSA query heads"))
    n_kv = int(require(cfg, ("num_key_value_heads",), "QSA key/value heads"))
    gdn_k = int(require(cfg, ("linear_num_key_heads",), "GDN key heads"))
    gdn_v = int(require(cfg, ("linear_num_value_heads",), "GDN value heads"))
    idx_heads = int(require(cfg, ("indexer_n_heads",), "QSA indexer heads"))
    idx_kv = int(require(cfg, ("indexer_kv_heads",), "QSA indexer kv heads"))
    moe_inter = int(require(cfg, ("moe_intermediate_size",),
                            "routed expert intermediate size"))
    shared_inter = int(require(cfg, ("shared_expert_intermediate_size",),
                               "shared expert intermediate size"))

    # --- A1 ---------------------------------------------------------------
    # The n_groups/2 precedent, made loud: an odd count silently drops a
    # group, and the completions stay fluent while it does.
    check(results, "A1-moe-routed-expert-parity", n_routed % tp == 0,
          {"routed_experts": n_routed, "tp": tp,
           "per_rank": n_routed // tp if n_routed % tp == 0 else None,
           "stacks": {stack: len(ids) for stack, ids in stacks.items()},
           "note": "an odd routed-expert count range-splits into unequal "
                   "halves and drops a group with no raise"})

    # --- A2 ---------------------------------------------------------------
    # THE HIGHEST-RISK ITEM. One shared expert per layer. 1 is ODD and cannot
    # be range-split, so the policy has to be decided rather than inherited:
    #
    #   DECIDED: the shared expert is REPLICATED whole on every rank and
    #   folded into the layer output EXACTLY ONCE (by the rank-0 contribution,
    #   or equivalently by folding before the all-reduce on one rank only).
    #   It is NEVER K-split with the halves summed to reconstruct it.
    #
    # And the arithmetic PROVES that decision rather than asserting it: a
    # K-split would slice the shared expert's intermediate dimension, and
    # shared_inter/tp is not a multiple of the FP8 block size, so a K-split is
    # not even representable in these bytes. Replication is the only sound
    # policy here, and now it is written down and checked.
    moe_layers = {(m.group("stack"), int(m.group("layer")))
                  for m in (is_expert(n) for n in tensors) if m}
    shared_layers = set()
    for name in tensors:
        if ".mlp.shared_expert." not in name:
            continue
        match = LAYER_RE.match(name)
        if match:
            shared_layers.add((match.group("stack"), int(match.group("layer"))))
    missing_shared = sorted(moe_layers - shared_layers)
    split_would_align = bool(block) and shared_inter % tp == 0 \
        and (shared_inter // tp) % int(block[0]) == 0
    check(results, "A2-moe-shared-expert-policy",
          bool(moe_layers) and not missing_shared and not split_would_align,
          {"shared_experts_per_layer": 1,
           "moe_layers": len(moe_layers),
           "layers_carrying_a_shared_expert": len(shared_layers & moe_layers),
           "moe_layers_missing_their_shared_expert": missing_shared[:8],
           "intermediate": shared_inter, "tp": tp,
           "k_split_would_be_block_aligned": split_would_align,
           "policy": "replicated whole on every rank, folded EXACTLY ONCE; "
                     "never K-split, never summed twice",
           "note": "1 is odd and cannot be range-split, and shared_inter/tp "
                   "is not a whole number of FP8 blocks either, so a K-split "
                   "is not representable in these bytes — replication is the "
                   "only sound policy and it is now asserted, not assumed. "
                   "Their defect dropped the shared expert from every decode "
                   "layer and the text stayed plausible: this is the "
                   "fluent-wrong-output item, so the count is checked "
                   "layer by layer"})

    # --- A3 ---------------------------------------------------------------
    head_dim = int(require(cfg, ("head_dim",), "QSA head dim"))
    check(results, "A3-qsa-head-parity",
          n_heads % tp == 0 and (n_heads // tp) > 0,
          {"num_attention_heads": n_heads, "tp": tp,
           "heads_per_rank": n_heads // tp if n_heads % tp == 0 else None,
           "head_dim": head_dim})

    # --- A4 ---------------------------------------------------------------
    # GQA groups must not straddle a rank boundary: each rank needs whole
    # groups, so the KV heads divide by TP AND the query heads divide by the
    # KV heads. The indexer rides the same path — and its KV head count is 1,
    # odd and unsplittable, so it is replicated on the A2 rule.
    idx_dim = int(require(cfg, ("indexer_head_dim",), "QSA indexer head dim"))
    gqa_ok = (n_kv % tp == 0 and n_heads % n_kv == 0
              and (n_heads // tp) % (n_kv // tp) == 0)
    # The indexer's query and key projections are FUSED into one tensor whose
    # leading dimension is (n_heads + kv_heads) * head_dim. kv_heads is 1 —
    # odd, unsplittable, replicated on the A2 rule — so slicing that fused
    # dimension EVENLY cuts the single KV head in half. It must be split
    # component-wise: the query block divides by TP, the KV block is copied.
    fused_expected = (idx_heads + idx_kv) * idx_dim
    fused_observed = sorted({shape[0] for name, (_s, _d, shape) in tensors.items()
                             if name.endswith("indexer.index_qk_proj.weight")
                             and len(shape) >= 1})
    fused_matches = fused_observed == [fused_expected]
    component_ok = (idx_heads * idx_dim) % tp == 0
    naive_even_split_is_wrong = idx_kv % tp != 0
    check(results, "A4-qsa-gqa-group-integrity",
          gqa_ok and idx_heads % tp == 0 and fused_matches and component_ok,
          {"num_key_value_heads": n_kv,
           "kv_heads_per_rank": n_kv // tp if n_kv % tp == 0 else None,
           "queries_per_kv_head": n_heads // n_kv if n_kv else None,
           "indexer_n_heads": idx_heads, "indexer_kv_heads": idx_kv,
           "indexer_head_dim": idx_dim,
           "indexer_kv_policy": ("replicated (odd, unsplittable)"
                                 if idx_kv % tp else "range-split"),
           "fused_index_qk_dim": fused_observed,
           "fused_index_qk_dim_expected": fused_expected,
           "fused_split_must_be_component_wise": naive_even_split_is_wrong,
           "fused_per_rank_width": (idx_heads * idx_dim) // tp + idx_kv * idx_dim,
           "naive_even_slice_width": fused_expected / tp,
           "note": "an uneven KV split straddles a GQA group across the rank "
                   "boundary and each rank then attends against the wrong "
                   "keys; and the fused indexer projection must be sliced "
                   "component-wise, because an even slice of its leading "
                   "dimension cuts the single replicated KV head in two"})

    # --- A5 ---------------------------------------------------------------
    gdn_ok = (gdn_k % tp == 0 and gdn_v % tp == 0 and gdn_v % gdn_k == 0
              and (gdn_v // tp) % (gdn_k // tp) == 0)
    check(results, "A5-gdn-head-parity", gdn_ok,
          {"linear_num_key_heads": gdn_k, "linear_num_value_heads": gdn_v,
           "key_heads_per_rank": gdn_k // tp if gdn_k % tp == 0 else None,
           "value_heads_per_rank": gdn_v // tp if gdn_v % tp == 0 else None,
           "values_per_key_head": gdn_v // gdn_k if gdn_k else None,
           "gdn_layers": sum(1 for kind in schedule.values()
                             if kind == "linear_attention")})

    # --- A6 ---------------------------------------------------------------
    # A misaligned slice mixes two neighbouring blocks' scales and is SILENTLY
    # WRONG — no raise, no NaN, just a quietly rescaled half of every row. So
    # walk the REAL shapes out of the header rather than recomputing dims from
    # config, and require every sharded boundary to land on a block edge.
    #
    # This is also the assertion that decides the MoE sharding mode for us:
    # under a tensor-parallel MoE the routed experts' intermediate dimension
    # would be sliced tp-ways, and it does not divide into whole blocks. There
    # is no legal tensor-parallel split of these experts. Selection-time
    # sharding is not a preference here, it is the only representable choice.
    if not block or len(block) != 2:
        raise GuardFailure(
            "quantization_config.weight_block_size is absent; the FP8 block "
            "size cannot be assumed")
    block_out, block_in = int(block[0]), int(block[1])
    fp8_dtypes = {"F8_E4M3", "F8_E5M2"}

    # (a) CORROBORATE THE BLOCK GRID FIRST. Every alignment claim below is
    # vacuous unless the declared block size is the grid the checkpoint's own
    # scale tensors were written on: a block-quantized weight of shape (d0,d1)
    # carries a scale of shape (ceil(d0/b0), ceil(d1/b1)). If that does not
    # hold, the declared block size is fiction and nothing else here means
    # anything.
    grid_checked, grid_bad = 0, []
    for name in sorted(tensors):
        size, dtype, shape = tensors[name]
        if dtype not in fp8_dtypes or len(shape) != 2:
            continue
        scale = tensors.get(name.replace(".weight", ".weight_scale_inv"))
        if scale is None:
            continue
        grid_checked += 1
        want = (-(-shape[0] // block_out), -(-shape[1] // block_in))
        if tuple(scale[2]) != want:
            grid_bad.append({"tensor": name, "weight_shape": list(shape),
                             "scale_shape": list(scale[2]),
                             "expected_scale_shape": list(want)})

    # (b) EVERY SLICED BOUNDARY. A misaligned FP8 slice mixes two neighbouring
    # blocks' scales and is SILENTLY WRONG — no raise, no NaN, just a quietly
    # rescaled half of every row. Walk the REAL shapes out of the header
    # rather than recomputing dims from config, because it is the bytes that
    # get sliced, not the config.
    misaligned = []
    sliced_checked = 0
    for name in sorted(tensors):
        size, dtype, shape = tensors[name]
        if len(shape) < 2 or is_table(name):
            continue
        if is_expert(name):
            # Selection-time sharding moves WHOLE experts: no dimension of a
            # routed expert is sliced, so none can straddle a block edge.
            continue
        leaf = name.rsplit(".", 1)[0].rsplit(".", 1)[-1]
        if leaf in COLUMN_PARALLEL:
            dim, axis, blk = shape[0], 0, block_out
        elif leaf in ROW_PARALLEL:
            dim, axis, blk = shape[-1], len(shape) - 1, block_in
        else:
            continue
        sliced_checked += 1
        bad = dim % tp != 0
        if dtype in fp8_dtypes and not bad:
            bad = (dim // tp) % blk != 0
        if bad:
            misaligned.append({"tensor": name, "axis": axis, "dim": dim,
                               "dtype": dtype, "per_rank": dim / tp,
                               "block": blk})

    # (c) THE FORCING FACT. Under a tensor-parallel MoE the routed experts'
    # intermediate dimension would be sliced TP ways — and it is not a whole
    # number of blocks, so that split is not representable in these bytes at
    # all. This is why S1 demands selection-time sharding: it is forced by the
    # checkpoint, not chosen by preference.
    moe_split_aligned = moe_inter % tp == 0 and (moe_inter // tp) % block_out == 0

    check(results, "A6-fp8-block-alignment",
          not misaligned and not grid_bad and grid_checked > 0
          and sliced_checked > 0,
          {"weight_block_size": [block_out, block_in],
           "block_grid_corroborated_on_tensors": grid_checked,
           "block_grid_mismatches": grid_bad[:8],
           "sliced_tensors_checked": sliced_checked,
           "misaligned": misaligned[:8],
           "misaligned_total": len(misaligned),
           "routed_expert_intermediate": moe_inter,
           "tensor_parallel_moe_would_align": moe_split_aligned,
           "note": "moe_intermediate/tp is not a whole number of FP8 blocks, "
                   "so there is NO legal tensor-parallel split of the routed "
                   "experts; selection-time sharding is forced, not chosen. "
                   "A zero-tensor alignment check is itself a failure here — "
                   "an assertion that examined nothing has proven nothing"})

    # --- A7 ---------------------------------------------------------------
    # Tensor presence is the source of truth; the index formula, if the config
    # even carries one, is cross-checked against it and may not disagree.
    declared = cfg.get("layer_types")
    main_stack = max(
        (s for s in {k.rsplit(".", 1)[0] for k in schedule}),
        key=lambda s: sum(1 for k in schedule if k.rsplit(".", 1)[0] == s))
    derived = [schedule[f"{main_stack}.{i}"]
               for i in range(len(
                   [k for k in schedule if k.rsplit(".", 1)[0] == main_stack]))]
    interval = cfg.get("full_attention_interval")
    formula = ([("full_attention" if (i + 1) % int(interval) == 0
                 else "linear_attention") for i in range(len(derived))]
               if interval else None)
    counts = {"full_attention": derived.count("full_attention"),
              "linear_attention": derived.count("linear_attention")}
    check(results, "A7-gate-schedule-from-tensors",
          derived == list(declared or derived)
          and (formula is None or formula == derived)
          and counts["full_attention"] > 0 and counts["linear_attention"] > 0,
          {"source": "tensor presence (self_attn.* vs linear_attn.*)",
           "counts": counts, "layers": len(derived),
           "matches_declared_layer_types": derived == list(declared or derived),
           "matches_index_formula": (None if formula is None
                                     else formula == derived),
           "full_attention_interval": interval,
           "note": "both ranks derive the gate schedule from the checkpoint's "
                   "own tensors; the index formula is a cross-check only and "
                   "may never be the source"})

    # --- A8 ---------------------------------------------------------------
    local_digest = guard_inputs_digest(cfg, tensors, schedule, tp)
    check(results, "A8-rank-derivation-parity",
          peer_digest is not None and peer_digest == local_digest,
          {"local": local_digest, "peer": peer_digest,
           "note": ("the twin did not answer; TP=2 cannot be gated on one "
                    "rank's arithmetic" if peer_digest is None else
                    "both twins derive identical guard inputs")})

    # --- §14.5, the expert-sharding block ---------------------------------
    plan = shard_plan(tensors, stacks, tp)
    argv_text = " ".join(serve_argv)
    selection_flags = [f for f in ("--enable-expert-parallel",
                                   "--enable-eplb")
                       if f in argv_text]
    tp_in_argv = re.search(r"--tensor-parallel-size\s+(\d+)", argv_text)
    check(results, "S1-selection-time-sharding",
          bool(selection_flags) and tp_in_argv is not None
          and int(tp_in_argv.group(1)) == tp,
          {"serve_flags": selection_flags,
           "tensor_parallel_size_in_argv": (int(tp_in_argv.group(1))
                                            if tp_in_argv else None),
           "note": "sharding by masking full-width expert weights AFTER "
                   "selection requires every expert to be addressable on "
                   "every rank, which contradicts a rank holding only half — "
                   "that is the 'arena alloc failed for moe_gate' path"})

    stack_cover = {}
    for stack, ids in stacks.items():
        per_rank = [sum(1 for n in plan["owned"][r]
                        if is_expert(n).group("stack") == stack)
                    for r in range(tp)]
        stack_cover[stack] = {"experts": len(ids), "tensors_per_rank": per_rank}
    check(results, "S2-both-moe-paths-sharded",
          len(stacks) >= 2 and all(all(c > 0 for c in v["tensors_per_rank"])
                                   and len(set(v["tensors_per_rank"])) == 1
                                   for v in stack_cover.values()),
          {"stacks": stack_cover,
           "note": "the main stack serves prefill AND decode; the draft stack "
                   "is the one-token entry point. Their first fix sharded "
                   "only the one-token path and prefill went unsharded — "
                   "which their own record calls silently wrong"})

    digests = [shard_digest(plan["owned"][r], tensors) for r in range(tp)]
    union = set().union(*(set(plan["owned"][r]) for r in range(tp)))
    overlap = set(plan["owned"][0]).intersection(*(set(plan["owned"][r])
                                                   for r in range(1, tp)))
    check(results, "S3-distinct-shard-checksums",
          len(set(digests)) == tp and not overlap
          and len(union) == sum(len(plan["owned"][r]) for r in range(tp)),
          {"per_rank_sha256": digests, "overlap_tensors": len(overlap),
           "experts_per_rank": plan["experts_per_rank"],
           "note": "distinct digests are the one-line proof the ranks own "
                   "DIFFERENT halves rather than two copies of the same half"})

    expert_bytes = sum(tensors[n][0] for r in range(tp) for n in plan["owned"][r])
    replicated_bytes = sum(tensors[n][0] for n in plan["replicated"])
    table_bytes = sum(tensors[n][0] for n in plan["table"])
    per_rank_bytes = expert_bytes // tp + replicated_bytes
    predicted_gib = round((per_rank_bytes + kv_bytes) / GIB, 3)
    check(results, "S4-predicted-rank-residency", predicted_gib <= bound_gib,
          {"weights_gib_per_rank": round(per_rank_bytes / GIB, 3),
           "kv_pool_gib": round(kv_bytes / GIB, 3),
           "predicted_gib_per_rank": predicted_gib,
           "bound_gib": bound_gib,
           "headroom_gib": round(bound_gib - predicted_gib, 3),
           "table_gib_host_page_cache": round(table_bytes / GIB, 3),
           "checkpoint_gib_total": round(
               sum(v[0] for v in tensors.values()) / GIB, 3),
           "note": "predicted from the safetensors HEADER before a weight "
                   "byte is read: the mmap'd table is excluded (zero bytes "
                   "GPU-resident, P11) and everything that is not a routed "
                   "expert is counted as replicated, which is the safe "
                   "direction to be wrong in"})

    return results, {"schedule_counts": counts, "digest": local_digest,
                     "shard_digests": digests,
                     "predicted_gib_per_rank": predicted_gib,
                     "routed_experts": n_routed, "top_k": top_k}


# --- the peer half of A8 --------------------------------------------------------

def peer_digest_over_ssh(host, model_dir, tp):
    """Ask the twin to derive the guard inputs from ITS OWN copy of the
    checkpoint. Shipping this file over stdin is the estate's existing
    cross-node probe shape (scripts/run-tp2.sh's rank_metrics.py)."""
    try:
        proc = subprocess.run(
            ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", host,
             "python3", "-", "--digest-only", "--model-dir", model_dir,
             "--tp", str(tp)],
            input=open(__file__, "rb").read(),
            capture_output=True, timeout=300)
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"fn-tp2-guards: peer digest unavailable: {exc}", file=sys.stderr)
        return None
    if proc.returncode != 0:
        sys.stderr.write(proc.stderr.decode(errors="ignore"))
        return None
    out = proc.stdout.decode(errors="ignore").strip().splitlines()
    return out[-1].strip() if out else None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--tp", type=int,
                        default=int(os.environ.get("FN_TP_SIZE", "2")))
    parser.add_argument("--model-dir",
                        default=os.environ.get(
                            "FN_MODEL_DIR",
                            "/var/lib/local-models/flashnext-fp8"))
    parser.add_argument("--serve-argv", default=os.environ.get("FN_SERVE_ARGV", ""),
                        help="the exact argv fn-cluster-up.sh is about to run")
    parser.add_argument("--peer", default=os.environ.get("FN_WORKER_HOST", ""),
                        help="the twin to byte-compare guard inputs with")
    parser.add_argument("--no-peer", action="store_true",
                        help="A8 fails closed instead of dialling the twin")
    parser.add_argument("--bound-gib", type=float,
                        default=float(os.environ.get("FN_RESIDENCY_BOUND_GIB",
                                                     "80")))
    parser.add_argument("--kv-bytes", type=int,
                        default=int(os.environ.get("FN_KV_CACHE_BYTES",
                                                   "12884901888")))
    parser.add_argument("--report", default=os.environ.get(
        "FN_GUARD_REPORT",
        os.path.join(os.environ.get("FN_STATE_DIR",
                                    os.path.expanduser("~/.local/state/flashnext")),
                     "tp2-guards.json")))
    parser.add_argument("--digest-only", action="store_true",
                        help="print the guard-input digest and exit (A8 peer)")
    args = parser.parse_args(argv)

    cfg = read_config(args.model_dir)
    tensors = read_headers(args.model_dir)

    if args.digest_only:
        schedule = schedule_from_tensor_presence(tensors)
        print(guard_inputs_digest(cfg, tensors, schedule, args.tp))
        return 0

    peer = None
    if not args.no_peer and args.peer:
        peer = peer_digest_over_ssh(args.peer, args.model_dir, args.tp)

    results, summary = run_assertions(
        cfg, tensors, args.tp, args.serve_argv.split(), peer,
        args.bound_gib, args.kv_bytes)

    failed = [r for r in results if not r["ok"]]
    report = {"step": "tp2-guards",
              "status": "pass" if not failed else "fail",
              "tp": args.tp, "model_dir": args.model_dir,
              "assertions": results, "summary": summary}
    try:
        os.makedirs(os.path.dirname(args.report), exist_ok=True)
        with open(args.report, "w") as fh:
            json.dump(report, fh, indent=1, sort_keys=True)
    except OSError as exc:
        print(f"fn-tp2-guards: could not write {args.report}: {exc}",
              file=sys.stderr)

    for result in results:
        mark = "ok  " if result["ok"] else "FAIL"
        print(f"fn-tp2-guards: {mark} {result['id']}", file=sys.stderr)
    if failed:
        print("fn-tp2-guards: FATAL: "
              + ", ".join(r["id"] for r in failed)
              + " — refusing to launch TP=2 into a split that cannot be "
                "proven sound", file=sys.stderr)
        print(json.dumps([r for r in failed], indent=1), file=sys.stderr)
        return 1
    print(f"fn-tp2-guards: all {len(results)} assertions pass; report: "
          f"{args.report}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except GuardFailure as exc:
        print(f"fn-tp2-guards: FATAL: {exc}", file=sys.stderr)
        print("fn-tp2-guards: a guard that cannot be EVALUATED is a failure, "
              "never a skip", file=sys.stderr)
        sys.exit(1)
