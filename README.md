# flashnext — Qwen3.8-Flash-Next (FP8) on a dual Strix Halo pair, overnight

**What this is:** a rebuild kit + Nix-flake-shaped repo that serves
[`Qwen/Qwen3.8-Flash-Next-FP8`](https://huggingface.co/Qwen/Qwen3.8-Flash-Next-FP8)
(125B trunk + 51.2B engram table, 6B active, 262K context) with **vLLM at
tensor-parallel size 2 across two Framework Desktops** (Ryzen AI MAX+ 395 /
gfx1151 / 128 GB each), connected by **two Thunderbolt 4 cables (the inference
plane, RCCL over TCP)** and a direct **5 GbE link (the control plane)**.

Being built overnight 2026-08-28 → 2026-08-29, governed by a
[tally](https://github.com/mecattaf/tally.nix) campaign spec
([`specs/flashnext/spec.md`](specs/flashnext/spec.md)). Benchmarks land in
[`results/`](results/) as they are produced. If it dies instead, the blocker
and drafted upstream issues land in [`handoff/`](handoff/).

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
| torch | `torch 2.13.0+rocm7.14.0` — AMD's **stable** gfx1151 wheels (the fork's exact pin) | repo.amd.com multi-arch |
| Container | Ubuntu 24.04 + stable wheels + fork built from source, recipe after [kyuz0/amd-strix-halo-vllm-toolboxes](https://github.com/kyuz0/amd-strix-halo-vllm-toolboxes) (MIT) | `container/` |
| Engram | `VLLM_PLE_MMAP=1` — table served from each node's NVMe via mmap page faults, 0 bytes GPU-resident, no per-token collective at the lookup site | fork |
| MTP | in-checkpoint multi-token prediction (no external drafter), vLLM v1 spec-decode | fork |
| Transport | RCCL over TCP, both Thunderbolt rails (PM QoS held both ends: 77 µs avg RTT); 5 GbE = ssh/control. No RDMA in this campaign. | dotfiles |
| Discipline | patch overlay + MANIFEST + verify script + packaging tests (after [AlexKGwyn/ds4-vllm](https://github.com/AlexKGwyn/ds4-vllm), Apache-2.0) | `patches/`, `tests/` |

## Status

- [x] Evidence sweep (7 dossiers, `specs/flashnext/evidence/`)
- [x] Estate + spec bootstrap
- [ ] Fork assembly (admission patch + PLE port + cherry-picks)
- [ ] Container build
- [ ] Weights staged on both nodes (NAS → NVMe, hash-verified)
- [ ] Proxy first light (single node)
- [ ] TP=2 first light (the real thing)
- [ ] Warmed-decode residency verdict
- [ ] Fidelity baseline + counterbalanced benchmarks

*Everything here is Apache-2.0 except where `THIRD_PARTY_NOTICES.md` says
otherwise. Model weights are not included and are governed by their own license.*
