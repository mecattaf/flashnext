# Overseer day-run notes — 2026-08-29 (campaign 01a04c1f, arm 2)

Operator-relayed community intel (flashnext#4, #5 — advisories, no scope
change) plus overseer working notes. This file is overseer memory: it must
survive session context loss.

## Back-pocket diagnostics for cp-proxy / cp-tp2 (flashnext#5)

Apply on symptom, in this order — do not bake in preemptively:

1. **Serve hangs mid-request** → retry the serve with `HSA_ENABLE_SDMA=0`.
   SDMA engines stall on scattered mmap-paging copies — the engram/PLE-mmap
   path's exact access shape. Kernel-copy fallback is slower but correct.
2. **"no kernel image available" on unified pages** → check `HSA_XNACK`
   inside the container (unified-memory demand paging wants XNACK enabled;
   verify what the container env actually carries before deeper debugging).
3. **Silent, memory-saturated load is EXPECTED, not hung** (flashnext#2):
   the mmap-staged load saturates memory quietly. Check disk read throughput
   (e.g. `iostat`/`/proc/diskstats` delta) before killing any process.
   A loader still streaming bytes is working.

## Bench integrity (flashnext#4 — steered to bench-harness, seq 3)

Multi-sequence QSA gather on gfx1151 HIP: documented wrong-output/crash in
the llama.cpp stack. Our vLLM path differs, but: matrix records concurrency
per arm; any cross-run token-fingerprint divergence in an arm → byte-compare
a serial (concurrency=1) replay before trusting that arm's numbers; flag
suspect arms, never average over divergence.


## The 256 GiB proxy OOM — resolved diagnosis (fable deep-dive, log-verified)

NOT memory accounting. The vision encoder profiling pass: 16384-token encoder
budget = 65536 ViT patches as one sequence; no flash/mem-efficient SDPA kernel
for that shape on torch 2.13/gfx1151, so the math fallback materializes the
[1,16,65536,65536] fp32 attention matrix = 2^38 B = 256 GiB exactly.
`integrated_gpu=False` on these hosts (gttsize kernel params make HIP report
the full 128 GiB), so the fork APU patches 0004/0007/0011 are inert and
VLLM_ROCM_APU_UNIFIED_MEMORY=0 is a no-op — steer seq 5 was a wrong diagnosis,
superseded by seq 6.

FIX (proxy AND the TP=2 big serve): --limit-mm-per-prompt '{"image":0,"video":0}'
(text-only mode; encoder profiling skipped; API rejects images instead of
OOMing). Optionally PYTORCH_HIP_ALLOC_CONF=expandable_segments:True (ds4-proven).
NOT --skip-mm-profiling (real image at serve time would hit the same wall).
ds4 precedent: text-only model, never had this; their --kv-cache-memory-bytes
pin no longer skips profile_run in our tree (gpu_worker.py:494-497 still
compiles/profiles), so the pin alone would not have dodged this.
Morning note: vision serving on gfx1151 is blocked on a chunked/flash ViT
attention path — README/handoff item, not a tonight patch.


## PRE-ARMED: cp-tp2 will fail at serve start — two known defects in merged fn-cluster-up.sh

Verified against the host-tooling completion ref (merged 09:52, BEFORE the
vision-OOM diagnosis). The TP=2 `vllm serve` invocation (fn-cluster-up.sh
step 5) has two latent failures, both loud at engine init:

1. No `--limit-mm-per-prompt` → the big model's vision tower hits the same
   256 GiB encoder-profiling OOM as the proxy did.
2. `--enforce-eager` + fn-env.sh's unconditional `VLLM_PLE_MMAP=1` → the
   fork's check_cudagraph_safety guard REFUSES plain eager with PLE mmap
   (spec P10: first light must run VLLM_COMPILE + PIECEWISE with the mmap op
   as a split boundary — exactly the mode the successful proxy boot used).

Recovery when cp-tp2 fails (native ladder: steer owning task host-tooling,
then resume/poll). Exact patch to deliver, byte-for-byte:

```diff
--- a/host/fn-cluster-up.sh
+++ b/host/fn-cluster-up.sh
@@ serve step 5 @@
   --tensor-parallel-size 2 \
   --distributed-executor-backend ray \
-  --enforce-eager \
   --gpu-memory-utilization $FN_GPU_UTIL \
   --max-model-len $FN_MAX_CTX \
+  --limit-mm-per-prompt '{"image":0,"video":0}' \
+  --max-num-batched-tokens ${FN_MAX_BATCHED_TOKENS:-2048} \
   > '$FN_STATE_DIR/serve.log' 2>&1"
```

(--max-num-batched-tokens 2048: this model has a QSA indexer, budget 2048;
ds4 precedent says indexer/top-k workspace scales with batch x context —
they ran 512 at 512K ctx. Start 2048 at 256K, drop to 512 on OOM.)

## TP=2 memory budget (fable deep-dive addendum, config-verified)

Checkpoint 172.8 GiB; engram table ~51.2 GiB stays on NVMe (PLE mmap, 0 GPU
bytes) → ~121.6 GiB GPU weights → ~61 GiB/rank at TP=2. KV at 256K is tiny
by architecture (12 of 48 layers full-attn, GQA 2 kv heads → 12 KiB/token/rank
→ 3 GiB per full 256K seq bf16). GDN state ~54 MiB/seq/rank but PREALLOCATED
per scheduler slot: default --max-num-seqs 256 would reserve ~14 GiB/rank —
cap to 32-64 if first light is tight. Budget: 61 weights + 10-13 KV pool +
~4 activations + ~4 runtime ≈ 80 GiB/rank = the residency bound; the spare
~40 GiB/node is page cache for the mmap'd table BY DESIGN. Optional after a
good first light: pin --kv-cache-memory-bytes 12884901888 (12 GiB ≈ 4
concurrent 256K streams; --kv-cache-dtype fp8 doubles that) ds4-style.

## Steer ledger (this campaign)

| seq | task | gist |
|---|---|---|
| 1 | host-tooling | six semantic traps (IFNAME computed, IB_DISABLE=1 unconditional, no probe-default exports, exact receipts, ethernet-only worker admin, reap-then-gate) |
| 2 | container-recipe | warm wheel cache at ~/.cache/flashnext-wheels + pulled base image; index URL still required in Containerfile; no torchaudio; ethernet only |
| 3 | bench-harness | QSA-gather hardening: concurrency per arm, serial-replay byte-compare on fingerprint divergence |
| 4 | container-recipe | commit-discipline insurance after ownership failure; layer cache exists |
| 5 | proxy-tooling | WRONG diagnosis (APU kill-switch) — superseded by 6 |
| 6 | proxy-tooling | real fix: --limit-mm-per-prompt image/video 0 (vision profiling OOM) |
