# Day-run stop-state — 2026-08-29, operator-called halt (~14:30 CEST)

Not a blocker exit: the operator stopped the flow deliberately at a clean
point to (1) fix tally.nix end-to-end from today's findings and reflash the
coordinator, (2) triage flashnext repo issues, (3) redesign and re-arm a
fresh tally flow wrapping today's completed work, (4) run it to completion.
This file is the input to all four steps.

## The tally.nix issue set (step 1 input — implement these)

Filed today, each with repro context and the workaround that unblocked us:

| issue | type | one-line |
|---|---|---|
| #620 | bug | `poll --once` "readmits" changed authority but executes the arm-time graph; `queue cancel` is a no-op on running jobs (`ok:true, affected:0`) |
| #621 | bug | re-arm does not supersede queued stale passes; they promote sequentially, each running the outdated graph |
| #622 | improvement | brief needs a closing definition-of-done; driver discards dirty-but-passing worktrees (3 lanes burned attempts exiting uncommitted) |
| #623 | bug | status render regresses at every reconcile boundary; registration/queue/poll give three different answers about the same pass; wedged campaign sweeps render as "pass" |
| #624 | bug | `agent.model` + `launch.model: null` → FlowAdmissionDenied with empty details at dispatch (11×); should be a typed arm-time refusal; Opus diagnosis mislabels it transient |
| #625 | bug | checkpoint validate-only refusals masked by result-projection-timeout; child stderr never reaches the record; 10 s projection wait hardcoded; validate-only contract undocumented |
| #626 | feat | no verb to answer inbox doubts or pardon exhausted caps; doubt-holding campaign wedges into no-op sweeps; 429 infra failures burn content-attempt caps |
| #627 | feat | adapter-captured usage never aggregated (1 of 17 attempts); provider quota exhaustion invisible until hard 429s |
| #628 | docs | steers race auto-retry dispatch (fix misses the retry it targets); `campaign resume` verb referenced in doctrine does not exist |

Local campaign state that informed these lives under
`~/.local/state/tally/` (captures, attempt-receipts, inbox of registration
01a04c1f-4605) — harvest anything needed BEFORE the reflash wipes it.
`handoff/TALLY-FINDINGS.md` is the chronological index;
`handoff/DAYRUN-NOTES.md` is the technical memory (steers, diagnoses,
pre-armed patches).

## Banked work to wrap into the second flow (step 3 input)

Durable in git, independent of tally state — completion refs
`tally/flashnext-campaign-01a04c1f-4605…/<task>-<hash>` :

- **container-recipe** (`5ee2002`): `flashnext:dev` 18.2 GB image; fork
  `bdb6f042` from source; torch 2.13.0+rocm7.14.0 exact; triton resolved
  3.8.0+git4cff872c.rocm7.14.0; receipt committed. Podman layer cache and
  `~/.cache/flashnext/{ccache,pip}` are warm (survive reflash only if /home
  survives — otherwise ~65 min rebuild).
- **instruments**: fn_synctrace / fn_offload_batch / fn_expert_union under
  container/rootfs/, spot-audited (expert-union re-target is correct and
  sync-free).
- **host-tooling**: host/fn-env.sh (audited: computed IFNAME, unconditional
  NCCL_IB_DISABLE=1, F.9 banner), fn-cluster-up.sh, run-tp2.sh.
- **bench-harness**: fingerprinting client + counterbalanced matrix with
  QSA serial-replay hardening (flashnext#4 steer implemented).
- **cp-weights**: both nodes verified 131 shards / 185,563,854,698 bytes
  (a ~53 KB library delta vs pre-arm staging was rsynced out); receipts
  trued-up on main; stage-weights.sh receipt writer now idempotent.
- **cp-build**: validated once under the receipt-restore wrapper (worklist
  argv carries it).
- On main besides the above: spec-lint gate `--manifest-path` fix
  (`42ae5cd`), L16 test exemption for the agent block, proxy-tooling
  re-scoped per the campaign's own amendment (boundary widened with
  results/receipts/proxy.json, GPU first-light moved to cp-proxy).

## Not done — the second flow's remaining graph

Lanes: proxy-tooling (author+commit two scripts — re-scoped, cheap),
morning-ledger, catalog-handoff, rdma-package (all light authoring).
Checkpoints: cp-smoke → cp-proxy → cp-tp2 → cp-bench → cp-close.

## Engine knowledge the second flow MUST carry (hard-won today)

1. **Serve text-only**: `--limit-mm-per-prompt '{"image":0,"video":0}'` on
   EVERY serve (proxy and TP=2). The vision encoder profiling pass
   materializes a 65536² fp32 SDPA matrix = exactly 256 GiB on gfx1151
   (no flash ViT kernel; math fallback). Operator-ratified: text-only this
   campaign. Not `--skip-mm-profiling` (a real image at serve time hits the
   same wall).
2. **`integrated_gpu=False` on these hosts** (gttsize params make HIP report
   the full 128 GiB): the fork APU patches 0004/0007/0011 are inert;
   `VLLM_ROCM_APU_UNIFIED_MEMORY=0` is a no-op; stock accounting is correct.
3. **fn-cluster-up.sh has two latent cp-tp2 killers** — exact patch in
   DAYRUN-NOTES ("PRE-ARMED" section): add the mm limit; DROP
   `--enforce-eager` (fn-env's VLLM_PLE_MMAP=1 + the fork guard demand
   VLLM_COMPILE/PIECEWISE — the mode the successful proxy boot used). Add
   `--max-num-batched-tokens 2048` (QSA indexer workspace, ds4 precedent).
4. **TP=2 memory budget checks out**: ~61 GiB/rank weights (engram table
   ~51 GiB stays on NVMe via mmap), KV at 256K is 12 KiB/token/rank →
   3 GiB per full-context stream; GDN state preallocates ~54 MiB × 
   max_num_seqs per rank — cap `--max-num-seqs 32-64`. ~40 GiB/node spare
   is page cache for the table BY DESIGN. Optional post-first-light:
   `--kv-cache-memory-bytes 12884901888`, fp8 KV doubles stream count.
5. **Silent memory-saturated load is expected** (flashnext#2): check disk
   read throughput before killing. Mid-request hang → retry with
   `HSA_ENABLE_SDMA=0`. "No kernel image" on unified pages → check HSA_XNACK.
6. **Bench integrity** (flashnext#4): concurrency recorded per arm;
   fingerprint divergence → serial replay byte-compare; flag, never average.
7. qwen-token-plan quota resets 09-04 11:50 UTC; claude-code adapter works
   once #624's lesson is applied (model via user-manager env or a fixed
   adapter preset, never `agent.model` on this pin).

## Flashnext repo issues (step 2 pointers)

- #1 separately-staged MTP/drafter artifact (parked pre-run, still open).
- #2 silent memory-saturated load — confirmed real today, folded into the
  serve doctrine above.
- #4 QSA multi-sequence gather — hardening implemented in bench-harness.
- #5 SDMA/XNACK diagnostics — in the doctrine above, untested (no TP=2 yet).
- Worth opening: "vision serving on gfx1151 blocked on chunked/flash ViT
  attention" (the 256 GiB analysis in DAYRUN-NOTES is the body).

## Residual local state to be aware of

- Campaign registration 01a04c1f-4605 still armed (pool `campaign-agent`
  paused, zero job units, monitor stopped). Reflash wipes it — nothing in
  it is needed beyond what this file and git already carry.
- `ANTHROPIC_MODEL` user-manager env: unset (debt cleared).
- `~/.cache/flashnext-wheels/`: torch/torchvision/triton wheels prefetched
  (the resolved triton differs: 3.8.0+git, from the index).
- `/var/lib/local-models/flashnext-fp8` staged and verified on BOTH nodes —
  the worker's copy survives the coordinator reflash.
