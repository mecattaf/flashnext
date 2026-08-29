# SPDX-License-Identifier: Apache-2.0
# Adapted from ds4_expert_union.py in AlexKGwyn/ds4-vllm @ a8f620d
# (container/rootfs/opt/venv/lib/python3.12/site-packages/ds4_expert_union.py),
# Apache License 2.0. Changes:
#   - DS4_ env surface renamed to FN_ (kill switch FN_EXPERT_UNION, default off).
#   - The wrap is RE-TARGETED. Upstream wrapped the gpt-oss triton_kernels
#     routing entry (gpt_oss_triton_kernels_moe.make_routing_data), which the
#     flashnext workload never calls: it routes through the stock fused-mixture
#     FusedTopKRouter instead (specs/flashnext/evidence/qsa-53896.md section 4).
#     This wraps fused_topk in
#     vllm/model_executor/layers/fused_moe/router/fused_topk_router.py.
"""FN_EXPERT_UNION=1: measure how many DISTINCT experts a decode step touches.

The decode roof is dominated by routed-expert bytes, and those scale with the
*union* of experts the step's positions select, not with top-k-per-token.
Independent routing pushes that union toward num_experts per layer; perfectly
correlated routing keeps it at top-k. That spread moves the physics ceiling by
a large factor, and nothing static can settle it -- it has to be measured
against real traffic.

This wraps ``fused_topk`` in vLLM's stock fused-mixture router entry
(``vllm/model_executor/layers/fused_moe/router/fused_topk_router.py``), the
choke point the flashnext workload actually routes through (FusedMoEFactory ->
FusedTopKRouter -> fused_topk). The upstream ds4 target -- the gpt-oss
triton_kernels ``make_routing_data`` -- is never on this model's path, and
vLLM's own ``--enable-return-routed-experts`` capturer hangs off
``BaseRouter.route()``, which the fused path does not call either.

``num_local_experts`` is read from ``gating_output.shape[-1]`` (router logits
are ``[M, num_experts]``): tensor shape is host metadata, so that costs no
device sync.

Per call it records (n_rows, n_distinct, n_local_experts) into a preallocated
device buffer using only elementwise kernels -- no D2H copy, no sync, so it
does not serialise the step it is measuring. The buffer is copied out once
when the window closes. Slot 0 of the seen-mask absorbs any -1 padding rows so
no boolean mask (and therefore no size-dependent sync) is needed.

Cost: a few extra small kernel launches per routing call. Off unless
FN_EXPERT_UNION=1.

Env:
    FN_EXPERT_UNION=1         enable (default: off)
    FN_EU_START=200           skip this many calls first (warmup + prefill)
    FN_EU_CALLS=2000          window size in routing calls
    FN_EU_OUT=~/vllm-prof     output directory

Install is deliberately lazy: by the first decode the MoE layers are long
since built, and wrapping at import time of whatever loads this module would
risk a circular import. The caller drives install() once the engine is up;
install() itself re-checks FN_EXPERT_UNION, so a stale call stays a no-op.
A probe must never take the engine down: every failure path swallows the
exception.
"""

import atexit
import os

import torch

_ENABLED = os.environ.get("FN_EXPERT_UNION") == "1"
_START = int(os.environ.get("FN_EU_START", "200"))
_CALLS = int(os.environ.get("FN_EU_CALLS", "2000"))
_OUT = os.path.expanduser(os.environ.get("FN_EU_OUT", "~/vllm-prof"))

_buf = None          # [_CALLS, 3] int32 on device
_n = 0               # calls seen
_done = False
_installed = False


def _record(topk_ids, num_local_experts):
    """Accumulate one routing call. Device-only; never synchronises."""
    global _buf, _n, _done
    if _done or not num_local_experts:
        return
    _n += 1
    if _n <= _START:
        return
    i = _n - _START - 1
    if i >= _CALLS:
        _flush()
        return

    ids = topk_ids.reshape(-1).long()
    if _buf is None:
        _buf = torch.zeros(_CALLS, 3, dtype=torch.int32, device=ids.device)

    # Slot 0 absorbs the -1 padding rows so no boolean mask (and therefore no
    # size-dependent sync) is needed; real experts land at 1..num_local.
    seen = torch.zeros(num_local_experts + 1, dtype=torch.bool, device=ids.device)
    seen[ids + 1] = True

    _buf[i, 0] = topk_ids.shape[0]
    _buf[i, 1] = seen[1:].sum()
    _buf[i, 2] = num_local_experts


def _flush():
    """Copy the buffer out once and write the dump."""
    global _done
    if _done or _buf is None:
        return
    _done = True
    try:
        rows = _buf.cpu().tolist()
        os.makedirs(_OUT, exist_ok=True)
        path = os.path.join(_OUT, f"expert_union_{os.getpid()}.tsv")
        with open(path, "w") as fh:
            fh.write("# n_rows\tn_distinct\tn_local_experts\n")
            for n_rows, n_distinct, n_local in rows:
                if n_rows:
                    fh.write(f"{n_rows}\t{n_distinct}\t{n_local}\n")
        print(f"[fn_expert_union] wrote {path}", flush=True)
    except Exception as exc:  # a probe must never take the engine down
        print(f"[fn_expert_union] flush failed: {exc}", flush=True)


def install():
    """Idempotently wrap fused_topk. Safe to call more than once."""
    global _installed
    if not _ENABLED or _installed:
        return
    try:
        from vllm.model_executor.layers.fused_moe.router import (
            fused_topk_router as m,
        )
    except Exception as exc:
        print(f"[fn_expert_union] not installed: {exc}", flush=True)
        return

    orig = m.fused_topk

    def wrapped(*a, **kw):
        res = orig(*a, **kw)
        try:
            # fused_topk(hidden_states, gating_output, topk, renormalize)
            # -> (topk_weights, topk_ids). num_local_experts comes off
            # gating_output's shape -- host metadata, no device sync.
            gating = kw.get("gating_output")
            if gating is None and len(a) > 1:
                gating = a[1]
            topk_ids = res[1] if isinstance(res, tuple) and len(res) >= 2 else None
            n_local = gating.shape[-1] if gating is not None else None
            if topk_ids is not None:
                _record(topk_ids, n_local)
        except Exception:
            pass  # never let the probe break routing
        return res

    wrapped._fn_orig = orig
    m.fused_topk = wrapped
    _installed = True
    # Also flush on shutdown, so a run that ends before the window fills still
    # leaves a (shorter) usable dump rather than nothing.
    atexit.register(_flush)
    print(
        f"[fn_expert_union] installed: skip {_START} calls, window {_CALLS}, "
        f"out {_OUT}",
        flush=True,
    )
