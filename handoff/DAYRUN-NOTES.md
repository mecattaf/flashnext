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

## Steer ledger (this campaign)

| seq | task | gist |
|---|---|---|
| 1 | host-tooling | six semantic traps (IFNAME computed, IB_DISABLE=1 unconditional, no probe-default exports, exact receipts, ethernet-only worker admin, reap-then-gate) |
| 2 | container-recipe | warm wheel cache at ~/.cache/flashnext-wheels + pulled base image; index URL still required in Containerfile; no torchaudio; ethernet only |
| 3 | bench-harness | QSA-gather hardening: concurrency per arm, serial-replay byte-compare on fingerprint divergence |
