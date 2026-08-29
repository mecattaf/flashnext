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

## Steer ledger (this campaign)

| seq | task | gist |
|---|---|---|
| 1 | host-tooling | six semantic traps (IFNAME computed, IB_DISABLE=1 unconditional, no probe-default exports, exact receipts, ethernet-only worker admin, reap-then-gate) |
| 2 | container-recipe | warm wheel cache at ~/.cache/flashnext-wheels + pulled base image; index URL still required in Containerfile; no torchaudio; ethernet only |
| 3 | bench-harness | QSA-gather hardening: concurrency per arm, serial-replay byte-compare on fingerprint divergence |
| 4 | container-recipe | commit-discipline insurance after ownership failure; layer cache exists |
| 5 | proxy-tooling | WRONG diagnosis (APU kill-switch) — superseded by 6 |
| 6 | proxy-tooling | real fix: --limit-mm-per-prompt image/video 0 (vision profiling OOM) |
