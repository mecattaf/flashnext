# flashnext — Qwen3.8-Flash-Next (FP8) on a dual Strix Halo pair, overnight

**What this is:** a rebuild kit + Nix-flake-shaped repo that serves
[`Qwen/Qwen3.8-Flash-Next-FP8`](https://huggingface.co/Qwen/Qwen3.8-Flash-Next-FP8)
(125B trunk + 51.2B engram table, 6B active, 262K context) with **vLLM at
tensor-parallel size 2 across two Framework Desktops** (Ryzen AI MAX+ 395 /
gfx1151 / 128 GB each), connected by **one TB5 cable + one TB3 cable as the
tensor plane (both training at 40 Gb/s on these USB4 hosts; RCCL over TCP
striped across both rails)** and a direct **5 GbE link (the control plane)**.

Being built overnight 2026-08-28 → 2026-08-29, governed by a
[tally](https://github.com/mecattaf/tally.nix) campaign spec
([`specs/flashnext/spec.md`](specs/flashnext/spec.md), ratified). Benchmarks
land in [`results/`](results/) as they are produced. If it dies instead, the
blocker and drafted upstream issues land in [`handoff/`](handoff/).

The overnight build itself is compute-routed: every implementation lane of the
campaign grinds on **qwencloud-hosted qwen3.8-max** via tally's `pi` adapter
(the model the repo is about, building its own serving stack); a **Claude
Fable** overseer session steers the armed campaign, and **Opus ultracode
fleets** are the escalation path when a lane fails twice. Checkpoints run
model-free as direct subprocesses. The full routing table, the reason no
per-task routing knob exists, and the exact escalation ladder are in
[`handoff/ROUTING.md`](handoff/ROUTING.md).

## Why this needs a repo at all

Four findings from tonight's source sweep (full evidence with file:line pins in
[`specs/flashnext/evidence/`](specs/flashnext/evidence/)):

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
   measurements exist anywhere. That's what tonight is for.

## The stack

| Layer | Choice | Pin |
|---|---|---|
| Model | Qwen3.8-Flash-Next-FP8 (expert-only block-FP8; attention/GDN/trunk bf16 as shipped) | HF rev `970c569` |
| Engine | fork of vLLM: [mecattaf/vllm branch `flashnext`](https://github.com/mecattaf/vllm/tree/flashnext) — base = PR #54129 head (carries #53896's model code) + gfx1151 patches | base `8e4e036` |
| Cherry-picks | vLLM PRs #46012 (wave32 LDS fix in `top_k_per_row_decode`), #40963 (APU/UMA memory accounting), #51511 (skinny-GEMM disable on gfx1151), #46110 (KFD platform detection) | see `IMPORTS.md` |
| torch | `torch 2.13.0+rocm7.14.0` — AMD's **stable** gfx1151 wheel channel now publishes 2.10 through 2.13, so the fork's exact pin is satisfied natively | repo.amd.com multi-arch |
| Container | Ubuntu 24.04 + stable wheels + fork built from source, recipe after [kyuz0/amd-strix-halo-vllm-toolboxes](https://github.com/kyuz0/amd-strix-halo-vllm-toolboxes) (MIT) | `container/` |
| Engram | `VLLM_PLE_MMAP=1` — table served from each node's NVMe via mmap page faults, 0 bytes GPU-resident, no per-token collective at the lookup site | fork |
| MTP | in-checkpoint multi-token prediction (no external drafter), vLLM v1 spec-decode | fork |
| Transport | RCCL over TCP sockets — the transport of record. Rail 0 (`thunderbolt0`, 10.99.0.x/30) carries the tensors; PM QoS held both ends (an unheld C3 sleep state costs 8.5× latency); 5 GbE = ssh/control. ibverbs devices now exist on both nodes from the pre-arm bake, so `NCCL_IB_DISABLE=1` is pinned **unconditionally** — RDMA is the attended morning A/B, never the overnight path. | dotfiles, `host/rdma/` |
| Packaging | **Podman tonight, Nix at graduation** — you don't nixify a moving target. Tonight's deliverable is a serving measurement, so the engine builds in a container with a bind-mounted, ccache'd build dir (a one-line patch rebuilds incrementally; nix charges a full few-hundred-kernel HIP recompile per edit). Once the patch set is frozen and proven, the repo graduates to NixOS-native — the complete nix wiring, substrate audit, and hazard ledger already ship in [`specs/flashnext/evidence/`](specs/flashnext/evidence/) as the graduation spec. No VMs: the iGPU can't be VFIO-passed on Strix Halo. | `container/`, `flake.nix` |
| Discipline | patch overlay + MANIFEST + verify script + packaging tests (after [AlexKGwyn/ds4-vllm](https://github.com/AlexKGwyn/ds4-vllm), Apache-2.0) | `patches/`, `tests/` |

## Status

- [x] Evidence sweep (9 dossiers, `specs/flashnext/evidence/`)
- [x] Estate + spec bootstrap (spec-lint clean; tally campaign spec ratifiable)
- [x] **Fork assembly** — [`mecattaf/vllm@flashnext`](https://github.com/mecattaf/vllm/tree/flashnext): 12 commits on the PR base, mirrored in [`patches/`](patches/) with MANIFEST. FP8-MoE admission + in-kernel upcast (MoE *and* linear), the AMD PLE/mmap port with the FP8 embedding stack, APU memory-accounting fix, four upstream cherry-picks
- [x] RDMA day-2 attended package (`host/rdma/`) — sockets stay tonight's transport
- [x] **Pre-arm host bake** — matched-set patched thunderbolt + ibverbs stack live and verified on **both** twins (`usb4_rdma0` + `usb4_rdma5` present by design, links up, rail soaks loss-free). Sockets remain the transport of record; `NCCL_IB_DISABLE=1` is now *unconditional* precisely because the verbs devices exist. RDMA itself is the attended morning A/B, never tonight.
- [x] Spec ratified; all five worklist gates green (unit tests, spec-lint, flake check, fork verify, receipts verify); weights pre-staging launched; **campaign arming**
- [ ] Container build (overnight, tally-governed)
- [ ] Weights staged on both nodes (NAS → NVMe, hash-verified)
- [ ] Proxy first light (single node)
- [ ] TP=2 first light (the real thing)
- [ ] Warmed-decode residency verdict
- [ ] Fidelity baseline + counterbalanced benchmarks

## Decisions the dual-Strix community may find useful

Even before benchmarks land, these are settled from source (file:line pins in
the [evidence](specs/flashnext/evidence/)):

- **The wire is not your TP=2 limiter** — but an unheld CPU C-state is. Hold
  `/dev/cpu_dma_latency` at 0 on **both** ends (577 µs → 63–90 µs RTT, free).
  RDMA measures ≈+3.4% decode over held TCP here — worth taking, but it means
  unsigned kernel-pinned modules and a coordinated dual reboot, so it's a
  day-2 attended lane in this repo (`host/rdma/`), never the overnight path.
  One rail only: two RDMA rails cross-match Thunderbolt HELLOs and poison
  HopID state.
- **`gpu-memory-utilization` lies on this APU** without vLLM PR #40963: HIP
  reports the small VRAM aperture as "total". The fix reads sysfs GTT.
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

And four more from the pre-arm host bake (2026-08-29, evidence in
`host/rdma/` and `handoff/PREARM-REBOOT.md`):

- **The Strix Halo NHI has exactly 3 DMA rings per controller** (verified via
  the driver's own debugfs; independently corroborated by the only other known
  cross-host Strix transport project): control + thunderbolt-net + ONE RDMA
  lane. The advertised *second* native lane per cable fails one boot-time
  probe with a cosmetic `-12` — a laundered `-EINVAL` from `nhi_alloc_hop`
  ("invalid hop: -1") — permanent, harmless, cleans up fully, never retries.
  Don't chase it.
- **Every ibverbs device advertises rail 0's GID** (one global roce_netdev for
  every rail), so `NCCL_IB_HCA` must be the **EXACT** string `usb4_rdma0` — a
  prefix match, or `usb4_rdma5`, silently routes RDMA onto the wrong wire.
  (`usb4_rdma5` itself is deliberate fixed-stride naming so both nodes compute
  identical names for the same physical lane; do not rename it.)
- **The XDomain wedge hazard**: RDMA DMA TX toward a peer with no open RX ring
  stalls on zero E2E credits and can take TCP *on the same cable* down with
  it — reboot-only recovery. Discipline: out-of-band TCP barrier (over
  ethernet) before the first verbs transmit; never both sides' rings down
  simultaneously; worker-first teardown. Full protocol in
  `host/rdma/ab-protocol.md`.
- **The amdgpu ISM/SSO `dc_lock` ABBA shutdown deadlock**: a Strix Halo node
  driving real panels (2×5K here) hangs on EVERY reboot inside
  `device_shutdown` — `dm_suspend()` holds `dc_lock` while sync-flushing
  ISM/SSO delayed work that itself takes `dc_lock` — while a headless twin
  with a fake EDID never arms the FSM and never hangs. Fixed upstream in
  7.1.6/7.2 (mainline `3714fe242592`; 7.1.5 still has it).
  `watchdog.stop_on_reboot=0` makes a wedged box self-reset in 2 min instead
  of hanging forever.

*Everything here is Apache-2.0 except where `THIRD_PARTY_NOTICES.md` says
otherwise. Model weights are not included and are governed by their own license.*
