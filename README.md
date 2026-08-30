# flashnext — Qwen3.8-Flash-Next (FP8) on a dual Strix Halo pair, overnight

**What this is:** a rebuild kit + Nix-flake-shaped repo that serves
[`Qwen/Qwen3.8-Flash-Next-FP8`](https://huggingface.co/Qwen/Qwen3.8-Flash-Next-FP8)
(125B trunk + 51.2B engram table, 6B active, 262K context) with **vLLM at
tensor-parallel size 2 across two Framework Desktops** (Ryzen AI MAX+ 395 /
gfx1151 / 128 GB each), connected by **one TB5 cable + one TB3 cable (both
train at 40 Gb/s on these USB4 hosts; the tensor plane is RCCL sockets on
rail 0 — see the transport ladder below)** and a direct **5 GbE link (the
control plane)**.

Built by two overnight [tally](https://github.com/mecattaf/tally.nix)
campaign runs governed by a ratified spec
([`specs/flashnext/spec.md`](specs/flashnext/spec.md)). **Round 1
(2026-08-28 → 08-29)** banked the engine, the instruments, the host tooling,
and the bench harness, then was deliberately halted to fix nine harness
defects it exposed (tally.nix #620–#628, all since fixed upstream).
**Round 2 (armed 2026-08-30, this README's revision)** is the crystallized
completion run: the remaining lanes, the TP=2 first light, the
counterbalanced benchmark matrix with the native MTP head, and the first
measured numbers for the in-tree USB4 stream primitive. Benchmarks land in
[`results/`](results/) as they are produced; failures land as **typed,
graded receipts** (quarantined under `results/receipts/failed/`), never
narration.

## Why this needs a repo at all

Four findings from the original source sweep (full evidence with file:line
pins in [`specs/flashnext/evidence/`](specs/flashnext/evidence/)):

1. **vLLM PR [#53896](https://github.com/vllm-project/vllm/pull/53896) is good.**
   The QSA sparse-attention implementation is a genuine gather (O(topk), never
   O(kv_length)) with GPU top-k, and it ships a dedicated `amd/` platform tree.
   The two long-context killer bugs that both the llama.cpp and DeepSeek-V4
   communities independently hit are *not* present.
2. **Stock vLLM still cannot start this model on gfx1151.** `supports_fp8()`
   admits only CDNA and RDNA4, so the FP8 MoE kernel oracle raises
   `NotImplementedError` at layer construction. RDNA3.5 has no FP8 matrix unit —
   FP8 must be upcast in-register — and nobody has enabled that for the fused-MoE
   path. AMD's own open PR
   [#52970](https://github.com/vllm-project/vllm/pull/52970) does exactly this
   for the *linear* block-scaled GEMM (`FORCE_FP8_DOT_UPCAST`, gated
   `on_gfx1151()`); **this repo's fork extends that mechanism to the MoE oracle.**
3. **The engram-on-SSD path (PR
   [#54129](https://github.com/vllm-project/vllm/pull/54129), `VLLM_PLE_MMAP`)
   is wired into the `nvidia/` tree only**, and the `amd/` tree has no FP8
   handling for the 51.2B lookup table at all — on this checkpoint the stock AMD
   path cannot load it correctly. The fork ports the mmap path + the FP8
   embedding stack to `amd/` (~80–150 lines, exactly specified in the evidence).
4. **Nobody has ever run this model on this hardware at TP=2.** Zero
   measurements exist anywhere. That's what these runs are for.

## The stack

| Layer | Choice | Pin |
|---|---|---|
| Model | Qwen3.8-Flash-Next-FP8 (expert-only block-FP8; attention/GDN/trunk bf16 as shipped) | HF rev `970c569` |
| Engine | fork of vLLM: [mecattaf/vllm branch `flashnext`](https://github.com/mecattaf/vllm/tree/flashnext) — base = PR #54129 head (carries #53896's model code) + gfx1151 patches | base `8e4e036` |
| Cherry-picks | vLLM PRs #46012 (wave32 LDS fix in `top_k_per_row_decode`), #40963 (APU/UMA memory accounting), #51511 (skinny-GEMM disable on gfx1151), #46110 (KFD platform detection) | see `IMPORTS.md` |
| torch | `torch 2.13.0+rocm7.14.0` — AMD's **stable** gfx1151 wheel channel publishes 2.10 through 2.13, so the fork's exact pin is satisfied natively (ROCm 10 is a graded day-2 migration, not tonight's basis — see below) | repo.amd.com multi-arch |
| Container | Ubuntu 24.04 + stable wheels + fork built from source, recipe after [kyuz0/amd-strix-halo-vllm-toolboxes](https://github.com/kyuz0/amd-strix-halo-vllm-toolboxes) (MIT); image `flashnext:dev`, banked and receipt-graded (cp-build) | `container/` |
| Engram | `VLLM_PLE_MMAP=1` — table served from each node's NVMe via mmap page faults, 0 bytes GPU-resident, no per-token collective at the lookup site. This is also why `--gpu-memory-utilization` is pinned LOW (0.62): the ~40 GiB/node page cache the table faults through is a design element, not free memory | fork, `host/fn-env.sh` |
| MTP | **in-checkpoint** multi-token prediction — verified against the staged shard index: 3,101 `mtp.*` tensors, 2.51 GiB (2.34 GiB fp8 experts + 0.17 GiB bf16) across 28 of the 131 shards; the fork carries a native proposer for it. No external drafter artifact exists or is needed | fork, cp-bench spec-on arm |
| Transport | RCCL over TCP sockets on rail 0 (`thunderbolt0`, 10.99.0.x/30), **single rail, cable A**. `NCCL_IB_DISABLE=1` unconditional. Terminal fallback rung: the 5 GbE wire, loudly receipted. RDMA is the attended morning A/B behind Gate 0; the in-tree USB4 stream primitive is measured (not ridden) tonight — full ladder below | `host/fn-env.sh`, `host/rdma/` |
| Packaging | **Podman tonight, Nix at graduation** — you don't nixify a moving target. The engine builds in a container with a bind-mounted, ccache'd build dir; the complete nix wiring ships in the evidence as the graduation spec. No VMs: the iGPU can't be VFIO-passed on Strix Halo | `container/`, `flake.nix` |
| Discipline | patch overlay + MANIFEST + verify script + packaging tests (after [AlexKGwyn/ds4-vllm](https://github.com/AlexKGwyn/ds4-vllm), Apache-2.0) | `patches/`, `tests/` |

## Status

**Round 1 — banked on main, receipt-graded:**

- [x] Evidence sweep (9 dossiers, `specs/flashnext/evidence/`)
- [x] Estate + spec bootstrap (spec-lint clean; campaign spec ratified)
- [x] **Fork assembly** — [`mecattaf/vllm@flashnext`](https://github.com/mecattaf/vllm/tree/flashnext): 12 commits on the PR base, mirrored in [`patches/`](patches/) with MANIFEST
- [x] **container-recipe** — `flashnext:dev` (18.2 GB), fork `bdb6f042` from source, torch/triton pins exact, build receipt committed (cp-build validated)
- [x] **instruments** — `fn_synctrace` / `fn_offload_batch` / `fn_expert_union` in the container overlay, audited sync-free
- [x] **host-tooling** — env doctrine, cluster up/down, preflight, first-light runner
- [x] **bench-harness** — honest client (queue/prefill separated), counterbalanced matrix, QSA serial-replay hardening
- [x] **cp-weights** (round 1) — 131 shards / 185,563,854,698 bytes verified on both nodes… *and then pruned by the fleet itself; see the round-2 findings below*

**Round 2 — the crystallized completion run (this is what runs tonight):**

- [ ] proxy-tooling (author the proxy build/serve scripts; cp-proxy executes)
- [ ] morning-ledger (archives the 08-29 stop-state, then renders the ledger)
- [ ] catalog-handoff (the patch that permanently stops the weight pruning)
- [ ] rdma-package close-out (pin test + odinlink fold + kernel-truth preamble)
- [ ] cp-weights **re-stage** (~185.6 GB/node — see finding 1 below)
- [ ] cp-smoke → cp-proxy → **cp-tp2 (the milestone)** → cp-bench (MTP spec-on arm) → cp-close
- [ ] usb4stream-bench + cp-usb4stream (dead-last, wedge-safe, idle-cable-only)
- [ ] rocm10-probe (after cp-bench, separate image tag, outcome-typed — see the ROCm 10 section)

## Round 2: what the crystallization pass found and decided (2026-08-30)

The second run's shape was settled by a multi-agent analysis pass (five
deep-analysis threads, a synthesis, and two adversarial verifiers whose
blocking findings were folded back in). Everything below is measured or
read-in-source, not assumed.

> **The full reasoning — every decision with its evidence chain, the dissent
> it overrode, the alternatives rejected and why, and the exact trigger that
> would flip it — is preserved in
> [`docs/DECISIONS-2026-08-30.md`](docs/DECISIONS-2026-08-30.md).** This
> section is the summary; that file is the record.

### Findings that reshaped the plan

1. **The staged weights were gone from BOTH nodes.** Round 1's cp-weights
   receipts (08-29 12:47) were true when written — then the fleet's
   local-models sync service *retired* the artifact from both nodes at
   ~20:07/20:14 the same evening, because it has no catalog row. It re-runs
   at every boot, rebuild, and sync-service start. Consequences: cp-weights
   is revised to force a re-stage (14400 s budget; the library source path
   measures 86–87 MB/s sequential, ~75–80 min/node), `catalog-handoff` now
   states plainly that applying `handoff/catalog-row.patch` is what ends
   this hazard permanently, and the overnight red lines forbid rebuilds and
   sync-service starts while the campaign runs.
2. **The worker carries zero podman images.** Nothing in the estate ever
   shipped `flashnext:dev` across the wire — cp-tp2 would have died at its
   worker-container step. New `host/fn-image-ship.sh` (idempotent by image
   Id) runs inside `fn-cluster-up.sh` before the worker container starts.
3. **The bench matrix's own serve line still carried the cp-tp2 killers**
   that e91f517 fixed in `fn-cluster-up.sh` — the plain-eager flag (the
   fork's cudagraph-safety guard *refuses* it under `VLLM_PLE_MMAP=1`) and
   no text-only multimodal limit (the 256 GiB vision-profiling OOM). Both
   arms would have failed to boot at the first arm flip. Fixed pre-arm,
   pinned by `tests/test_bench_matrix.py`.
4. **No ibverbs device exists on either node tonight.** The round-1 pre-arm
   bake's `usb4_rdma0`/`usb4_rdma5` devices died with the kernel move to
   7.2.2 — the staged patched-module sets cover only 7.1.4/7.2.0. The
   README's previous "present by design" claim is now historical;
   `host/rdma/` gains a dated truth preamble, and the attended morning
   fetch-and-build (running-kernel gate: 7.2.2, both nodes, worker first)
   precedes any A/B.
5. **The dark Thunderbolt rail is asymmetric.** The worker's `thunderbolt0`
   reads NO-CARRIER even after its own clean reboot — so the coordinator's
   pending reboot alone may not heal rail 0. The operator checklist carries
   a branch: coordinator reboot → replug cable A → worker reboot → accept a
   wire night. A wire night produces valid *degraded* receipts and does
   **not** open the verbs Gate 0.
6. **The memory arithmetic was broken.** `FN_GPU_UTIL=0.83` × 125.1 GiB GTT
   (fork patch 0004 points reporting at GTT) ≈ 104 GiB/rank — over the
   80 GiB residency bound the receipts gate enforces AND eating the page
   cache the mmap'd table needs. Now: util 0.62,
   `--kv-cache-memory-bytes 12 GiB` (holds the GDN slot pool: 32 slots ×
   54 MiB × (1+n) ≈ 6.9 GiB at n=3, plus paged KV), `--max-num-seqs 32`
   (the engine default of 256 would preallocate ~14 GiB/rank of GDN state).
   Expected residency: ~76–78 GiB/rank, under the bound the runner now
   grades *itself*.

### The transport decision (the operator's RDMA-first instinct, overridden with cause)

**cp-tp2 and cp-bench ride RCCL sockets on rail 0, single rail, cable A.**
The RDMA-first preference was overridden on measured grounds, not doctrine:
no verbs device exists tonight (finding 4); the RDMA package's own Gate 0
requires a banked TCP TP=2 benchmark *before* bring-up may start (TCP-first
is the gate, not a preference); verbs bring-up needs ~2–4 attended hours and
two more reboots onto a not-yet-deployed module set; and an unattended
verbs→sockets fallback ladder is itself the hazard — a verbs failure wedges
the whole XDomain *including TCP on the same cable* (reboot-only recovery).
The honest measured expectation from the only community precedent is ≈+3.4%
decode over held TCP — real, worth the attended morning, never worth the
night.

The unattended ladder that *does* exist (in `host/fn-env.sh`):

1. **rail0-sockets** — thunderbolt0, listed only if its /30 peer answers a
   3-packet ping (a 1-packet gate flaps on a cold neighbour cache);
2. **wire-fallback** — the 5 GbE wire, terminal, loudly logged, and stamped
   into every receipt as `fn_transport_rung` so a wire night can never be
   mistaken for a rail night. Never the second rail, never verbs.

The transport is decided **once, on the coordinator**, and injected into the
worker's environment as literals — two ranks can no longer disagree because
one node dropped one ICMP packet.

**Cable topology — both operator options rejected:** the "4-rail aggregate"
(2 cables → 4 rails → 4 verbs devices, ~48 Gb/s aggregate) is physically
unavailable on this NHI — 3 DMA rings per controller means exactly ONE RDMA
lane per cable (the second lane's `-12` probe error is permanent), and
decode is *latency*-bound, where aggregation buys nothing. The ds4-vllm
2-cable split exists to serve a 3,450-line unpublished zero-copy patch this
repo deliberately does not carry. Cable B stays parked; a two-socket-rail
aggregation test is a cheap attended morning item.

**Why the latency war matters less than it looks tonight:** at hidden size
2560 × 48 layers, a TP=2 decode step issues ~96 small allreduces (~5 KB).
Against the reference 105 µs custom-verbs bar (`tbv_ar2` in the ds4 estate),
socket transport costs ~10–29 ms of a 25–50 ms step — second-order next to
what the MTP arm can win. The structural answer to the allreduce war is the
stream-primitive port (below), evaluated with numbers in the morning.

### MTP (the operator's hard requirement — settled as pure serve-config)

The head ships **in the checkpoint**: 3,101 `mtp.*` tensors, 2.51 GiB,
arriving with the 131 shards cp-weights stages; repo issue #1's closure
verified against the shard index. The fork supports it natively end-to-end
(dedicated proposer + spec-aware state banking for the hybrid GDN arch).
The night's shape:

- **cp-tp2 first light runs spec-off** — it is the identity oracle's
  baseline (greedy spec output must match plain decode byte-for-byte).
- **cp-bench's spec-on arm is the first end-to-end MTP proof**:
  `--speculative-config '{"method":"mtp","num_speculative_tokens":3}'`
  (n=3 tonight; the n=1..4 depth sweep is a morning-tuning surface). The
  receipt embeds acceptance telemetry from the serve logs and a per-depth
  cross-arm identity oracle; spec-on numbers are quotable only on a clean
  oracle.
- **Degrade-not-die:** if the spec-on serve fails to boot, the matrix banks
  the failure log, restores the baseline arm behind a one-shot guard, and
  finishes as an honestly-labeled single-arm receipt
  (`arms=["spec_off"], counterbalanced=false, spec_on_failed=true`) — the
  gate accepts that shape, and promotion becomes an attended morning
  iteration instead of a lost night.
- Promotion to the standing serve is a morning env flip (`FN_SPEC_ARGS` in
  `host/fn-cluster-up.sh`), never an overnight edit.

Caveat the morning must audit before the speculative profile is declared
production: drafter hidden-state vs prefix-cache position sync (a community
v1.1 fix records the multi-turn failure mode).

### USB4STREAM (deliberated twice, as demanded)

The in-tree stream primitive (`thunderbolt_stream`, by the Thunderbolt
maintainer, kernel 7.2+, `/dev/tbstreamN` over raw NHI DMA rings — no IP
stack, measured 21.8 µs p50 RTT at 4 KiB vs 137.8 µs for TCP-over-5GbE) is
strategically important precisely because everything RDMA here rides an
out-of-tree patched stack that dies at every kernel bump (finding 4 is the
treadmill demonstrating itself). Two independent deliberations converged:

- **The ncclNet-plugin route is REJECTED for the latency goal** — it sits
  under the collective library's proxy/protocol stack (which would eat most
  of the latency win), HopID scarcity forces a multiplexer over one or two
  long-lived streams, and comm teardown on every serve restart is exactly
  the open/close pattern the wedge hazard forbids. 3–5 attended days to a
  fragile prototype; 2–4 attended *weeks* to overnight-trustworthy.
- **The real path is a 2–4 attended-day port of the reference doorbell
  allreduce** (the 105 µs bar) from verbs onto the stream device's
  read/write. Trigger criteria, decided by tonight's numbers: banked
  exchange p50 at 8–16 KiB ≤ ~40 µs AND a bench matrix showing decode is
  allreduce-dominated. The full decision memo lands as
  `docs/USB4STREAM-TRANSPORT.md` (authored by the `usb4stream-bench` lane).
- **Tonight gets measurement only, wedge-safe by construction**: a
  single-open, fixed-schedule, retry-proof bench (`bench/usb4stream-bench.py`)
  plus a dead-last checkpoint (`cp-usb4stream`, after cp-close) that runs
  ONLY on an idle cable — a live pair serve on the shared cable is a typed
  SKIP, because an open/close storm against a mismatched peer wedges router
  hop tables (that exact hazard darkened rail 0 once already). After a
  healthy night it skips and the first real numbers come from the attended
  morning run after teardown; that expected outcome is pre-declared in the
  ledger.

### Receipt discipline: one graded failure costs one step, never the night

New this round (D12): every step that can fail writes its fail receipt to
**`results/receipts/failed/`** — committed, ledger-reviewed, a typed
blocker — while `scripts/receipts-verify.py` (which runs as a campaign gate
on *every* attempt) lists quarantined receipts as loud WARNs without
counting them as violations. Previously one failed sub-step would have
permanently reddened every later gate run. Additionally: `run-tp2.sh` grades
the 80 GiB residency bound itself (receipt and gate can never disagree); a
sub-0.9 full-context decode ratio is *deferral-typed* (the honest number
lands in quarantine as a performance finding and cp-bench still runs); and
the bench writes an interim receipt after its measurement sweep so a
runtime kill can no longer erase the night's numbers.

### Quantization side-quests (investigated, resolved, not lanes)

- **The CIRU "IU4" estate** (community Strix-specific quant work): decoded —
  IU4 is not an on-disk format; the shipped GGUF stores routed experts as
  plain Q4_1, and IU4 names a gfx1151 *execution path* built on the RDNA3.5
  int4 WMMA intrinsic (int8-G128 activations nibble-split into two u4
  planes, weights u4 affine, two WMMAs recombined). It is bound to a
  different runtime's artifact and single-device only — a
  rejected-with-reason register entry and an optimization-menu pointer for
  the future 4-bit expert lane, not an overnight lane.
- **Hadamard rotation / FP4 QAT in the DS4 indexer**: fidelity art for a
  *trained* indexer graph in a different model family. Our engine's QSA
  indexer is weight-free bf16 — transplanting the rotation would corrupt
  top-k selection. Rejected with reason; recorded so it is never
  re-litigated.
- **The qwen4exp-on-ROCmFPX integration patch** (community, 25 files,
  provenance-verified against its upstream merge commit): **zero code
  adopted** — it contains no actual FP4 hunks (those live in its base
  tree), it targets the wrong engine and topology, and it deliberately
  *drops* the MTP head we are enabling. What it yields is archived
  knowledge, now under
  [`specs/flashnext/evidence/kingjones-qwen4exp/`](specs/flashnext/evidence/kingjones-qwen4exp/):
  the five concrete coupling mechanisms that make "cherry-picking
  individual files will not work" true in any llama.cpp-lineage tree, the
  large-table conversion doctrine (positional memmap assembly — and a
  correction: the model card's "cast to BF16" contradicts its own code,
  which casts F32; the technique that matters is the placement, not the
  dtype), the row-local panel-quantization argument, and — most useful —
  an **independent confirmation** of our fork's QSA-cache separation
  design, plus the exact drift bug that sharpens issue #4 into a concrete
  regression test, plus the PLE-history-must-invalidate-on-rollback rule
  that the MTP profile must be audited against before production.

### ROCm 10 (the operator was right; the bootstrap ruling was wrong; the night still doesn't gamble)

The project's standing ruling — "AMD publishes no ROCm 10 gfx1151 torch
wheels" — is **factually wrong and now corrected**
(`specs/flashnext/evidence/kyuz0-rocm10.md` §11, `IMPORTS.md`): a complete
aligned gfx1151/cp312 set (`torch 2.13.0+rocm10.0.0`,
`triton 3.8.0+git4cff872c.rocm10.0.0` — the **same triton git hash** as our
pins — plus torchvision/sdk/device wheels) has been live at
`stable.repo.amd.com/rocm/whl-next/` since 2026-08-26; the bootstrap probe
looked one directory too shallow. The other stated blocker (rocBLAS
solution-index breakage) is ds4-lineage-specific and inapplicable — the
fork carries no tuned solution indices.

What keeps ROCm 10 off tonight's critical path is one fact: **nobody
anywhere has run vLLM on ROCm 10 on gfx1151** (kyuz0's auto-discovery
pipeline: zero rocm10 tags). Whether ROCm 10's hipcc compiles the fork's
HIP sources and whether its HSA runtime binds the in-tree KFD on 7.2.x are
both unmeasured — unknown-compile-at-2am, on a kernel-split pair, is the
textbook overnight killer. So the run stays on the banked, receipt-graded
7.14 image, and a new **`rocm10-probe` lane runs after cp-bench**: it
builds `flashnext:rocm10` from the *unmodified* Containerfile via its
existing build ARGs (zero recipe edits, receipt contract untouchable),
runs a minimal GPU binding check on the coordinator only, and writes its
outcome — green or red — to `results/rocm10-probe.json`, deliberately
*outside* the graded receipts directory. The wheel set is prefetched to
`~/.cache/flashnext-wheels/rocm10/`. If the probe is green, the morning
promotion is a five-minute pin swap (Containerfile ARG defaults + the
`recipe-pins` acceptance greps + a re-trued build receipt + a P4/F.11
ruling note) against a measured result instead of a guess.

### The night graph and what the morning reads

With `maxParallel=1` and the append-only worklist order, the cheap
dependency-free doc lanes (proxy-tooling, morning-ledger, catalog-handoff,
rdma-package) run **before** the first checkpoint can bank a failure — the
morning package is durably committed early by design. Then: cp-weights
re-stage (~2.5–3 h), cp-build (banked, skipped), cp-smoke, cp-proxy,
cp-tp2, cp-bench (21600 s budget), cp-close, cp-usb4stream (dead-last).
cp-tp2 may land 08:00–10:00 and cp-bench can run into the afternoon — that
trade was chosen deliberately: no checkpoint failure can cost the morning
package.

Morning order: read `docs/MORNING.md` → review anything under
`results/receipts/failed/` (typed blockers, possibly including a deferred
context probe) → apply `handoff/catalog-row.patch` (permanently ends the
weight-prune hazard) → only then any rebuild or model-sync restart →
attended RDMA fetch-and-build on 7.2.2 (worker first) + A/B per
`host/rdma/ab-protocol.md`, ONLY if bench.json's transport rung is
rail0-sockets → tear the pair down, run the stream bench attended, and
apply the decision rule in `docs/USB4STREAM-TRANSPORT.md`.

## Decisions the dual-Strix community may find useful

Even before round-2 benchmarks land, these are settled from source or
measurement (file:line pins in the [evidence](specs/flashnext/evidence/)):

- **The wire is not your TP=2 limiter** — but an unheld CPU C-state is. Hold
  `/dev/cpu_dma_latency` at 0 on **both** ends (577 µs → 63–90 µs RTT, free).
  RDMA measures ≈+3.4% decode over held TCP here — worth taking, but it means
  unsigned kernel-pinned modules and a coordinated dual reboot, so it's a
  day-2 attended lane in this repo (`host/rdma/`), never the overnight path.
  One rail only: two RDMA rails cross-match Thunderbolt HELLOs and poison
  HopID state.
- **Your fleet's artifact sync will eat your staged weights** if the staged
  copy has no catalog row. 185.6 GB × 2, twice. Declare artifacts before
  staging them, or gate the sync service while a campaign runs.
- **`gpu-memory-utilization` lies on this APU** without vLLM PR #40963: HIP
  reports the small VRAM aperture as "total". The fix reads sysfs GTT — and
  then you must *budget* GTT: on a UMA box with an mmap-served table, page
  cache is part of the serving design, so high utilization values are wrong
  even when they "fit".
- **`top_k_per_row_decode` overflows the 64 KB LDS on wave32** at its stock
  1024-thread merge (PR #46012) — this op is on the sparse-attention hot path.
- **FP8 on RDNA3.5 is a storage format, not a compute format** — every FP8
  weight is upcast in-register (AMD's own `FORCE_FP8_DOT_UPCAST` pattern from
  PR #52970). Memory-bandwidth-wise that's the *ideal* case for a 220 GB/s
  machine: 1 byte/param off DRAM. The only primitive *above* fp16 rate on
  gfx1151 is int4 WMMA (2.03×) — the future 4-bit expert lane.
- **Never `export` a vLLM env default**: several knobs are read via
  "is-set" probes (`VLLM_USE_DEEP_GEMM`, the AITER family) — exporting the
  value it already has *changes control flow*.
- **The engram table wants your SSD, not your RAM**: `VLLM_PLE_MMAP` serves
  51.2B parameters of factual-recall memory as ~2.5 KB/token of page-cache
  faults — and at TP=2 it also deletes a per-token all-reduce.
- **The in-tree USB4 stream primitive is real and fast** (14.3 µs 64 B RTT,
  ~841 MB/s/stream through a Python loop) — but treat every stream open as
  long-lived pair state. Open/close storms against a mismatched peer wedge
  the router hop tables, take thunderbolt-net down with them, and recover
  only by reboot.

And four more from the round-1 pre-arm host bake (2026-08-29, evidence in
`host/rdma/` and `handoff/PREARM-REBOOT.md`):

- **The Strix Halo NHI has exactly 3 DMA rings per controller** (verified via
  the driver's own debugfs; independently corroborated by the only other known
  cross-host Strix transport project): control + thunderbolt-net + ONE RDMA
  lane. The advertised *second* native lane per cable fails one boot-time
  probe with a cosmetic `-12` — permanent, harmless, never retries. Don't
  chase it.
- **Every ibverbs device advertises rail 0's GID** (one global roce_netdev for
  every rail), so `NCCL_IB_HCA` must be the **EXACT** string `usb4_rdma0` — a
  prefix match, or `usb4_rdma5`, silently routes RDMA onto the wrong wire.
- **The XDomain wedge hazard**: RDMA DMA TX toward a peer with no open RX ring
  stalls on zero E2E credits and can take TCP *on the same cable* down with
  it — reboot-only recovery. Discipline: out-of-band TCP barrier (over
  ethernet) before the first verbs transmit; never both sides' rings down
  simultaneously; worker-first teardown. Full protocol in
  `host/rdma/ab-protocol.md`.
- **The amdgpu ISM/SSO `dc_lock` ABBA shutdown deadlock**: a Strix Halo node
  driving real panels hangs on EVERY reboot inside `device_shutdown` — fixed
  upstream in 7.1.6/7.2 (mainline `3714fe242592`).
  `watchdog.stop_on_reboot=0` makes a wedged box self-reset in 2 min instead
  of hanging forever.

*Everything here is Apache-2.0 except where `THIRD_PARTY_NOTICES.md` says
otherwise. Model weights are not included and are governed by their own license.*
