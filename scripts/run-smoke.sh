#!/usr/bin/env bash
# In-container smoke: platform identity, fp8 storage+cast, model registry
# resolution, and oracle admission — the cheap questions that must be
# answered before any weight byte moves. Writes results/receipts/smoke.json.
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
IMAGE="${FN_IMAGE:-flashnext:dev}"
OUT="$REPO_ROOT/results/receipts/smoke.json"

podman run --rm --device /dev/kfd --device /dev/dri \
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
    from vllm.model_executor.layers.fused_moe.experts.triton_moe import TritonExperts
    from vllm.model_executor.layers.quantization.utils.quant_utils import (
        kFp8Static128BlockSym, kFp8Dynamic128Sym)
    ok, why = TritonExperts._supports_quant_scheme(
        kFp8Static128BlockSym, kFp8Dynamic128Sym) \
        if hasattr(TritonExperts, "_supports_quant_scheme") else (False, "no hook")
    return {"fp8_block_moe_admitted": bool(ok), "reason": str(why)}

try_("arch", arch)
try_("fp8_cast", fp8_cast)
try_("registry", registry)
try_("aperture", aperture)
try_("admission", admission)
json.dump(r, open("/results/receipts/smoke.json", "w"), indent=1)
print(json.dumps(r, indent=1))
PY
echo "smoke receipt: $OUT"
python3 -c "import json,sys; sys.exit(0 if json.load(open('$OUT'))['status']=='pass' else 1)"
