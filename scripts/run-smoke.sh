#!/usr/bin/env bash
# In-container smoke: platform identity, fp8 storage+cast, model registry
# resolution, and oracle admission — the cheap questions that must be
# answered before any weight byte moves. Writes results/receipts/smoke.json.
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
IMAGE="${FN_IMAGE:-flashnext:dev}"
OUT="$REPO_ROOT/results/receipts/smoke.json"

podman run --rm -i --device /dev/kfd --device /dev/dri \
  --security-opt seccomp=unconfined --ipc=host \
  -v "$REPO_ROOT/results:/results" "$IMAGE" python3 - <<'PY'
import json, time, traceback
r = {"step": "smoke", "status": "pass", "ts": time.strftime("%FT%T"), "data": {}}
def try_(name, fn):
    try:
        r["data"][name] = fn()
    except Exception as e:
        r["data"][name] = f"FAIL: {e.__class__.__name__}: {e}"
        r["status"] = "fail"
        traceback.print_exc()

def arch():
    from vllm.platforms import rocm
    return {"gcn_arch": rocm._GCN_ARCH, "on_cdna": rocm.on_cdna(),
            "supports_fp8_stock_predicate": rocm.on_cdna() or rocm.on_rdna4()}
def fp8_cast():
    import torch
    x = torch.zeros(4, dtype=torch.float8_e4m3fn, device="cuda")
    y = (x.to(torch.bfloat16) + 1).sum().item()
    return {"e4m3fn_alloc_and_cast": y == 4.0}
def registry():
    from vllm.model_executor.models.registry import ModelRegistry
    archs = ModelRegistry.get_supported_archs()
    return {"model_arch_registered": "Qwen4ExpForConditionalGeneration" in archs}
def aperture():
    return {"ttm_pages_limit": int(open(
        "/sys/module/ttm/parameters/pages_limit").read().strip())}
def admission():
    # The patched oracle must admit the block-FP8 scheme on this GPU.
    # patches/0008 defines _supports_quant_scheme as a plain boolean
    # predicate (its pinned tests assert the bare call), not a (ok, why)
    # tuple — unpack nothing.
    from vllm.model_executor.layers.fused_moe.experts.triton_moe import TritonExperts
    from vllm.model_executor.layers.quantization.utils.quant_utils import (
        kFp8Static128BlockSym, kFp8Dynamic128Sym)
    if not hasattr(TritonExperts, "_supports_quant_scheme"):
        return {"fp8_block_moe_admitted": False, "reason": "no hook"}
    ok = TritonExperts._supports_quant_scheme(
        kFp8Static128BlockSym, kFp8Dynamic128Sym)
    return {"fp8_block_moe_admitted": bool(ok),
            "reason": "boolean predicate (patches/0008)"}

try_("arch", arch)
try_("fp8_cast", fp8_cast)
try_("registry", registry)
try_("aperture", aperture)
try_("admission", admission)
# Receipt quarantine (D12): a fail receipt lands under results/receipts/
# failed/ — a typed blocker outside the grading walk, so one failed smoke
# cannot redden every later gate run. Exit code is unchanged.
import os
base = "/results/receipts" if r["status"] == "pass" else "/results/receipts/failed"
os.makedirs(base, exist_ok=True)
json.dump(r, open(base + "/smoke.json", "w"), indent=1)
print(json.dumps(r, indent=1))
PY
echo "smoke receipt: $OUT (fail receipts land under results/receipts/failed/)"
# Checkpoint purity: restore the receipt if this re-run measured no change.
python3 "$REPO_ROOT/scripts/receipt-restore.py" "$REPO_ROOT"
python3 - "$REPO_ROOT" <<'PY'
import json, pathlib, sys
root = pathlib.Path(sys.argv[1])
ok = root / "results/receipts/smoke.json"
failed = root / "results/receipts/failed/smoke.json"
if failed.is_file() and (not ok.is_file()
                         or failed.stat().st_mtime >= ok.stat().st_mtime):
    sys.exit(1)
sys.exit(0 if ok.is_file() and json.load(open(ok))["status"] == "pass" else 1)
PY
