# OUTLOOK — from `flashnext` to `dualflash`

Status: **direction, not scope.** Nothing in this file is in play for the current
campaign. flashnext's job is unchanged and unfinished: a functional Qwen3.8-Flash-Next
TP=2 serve on the pair, proven by receipts. This document exists so that the work we
do to get there is shaped by where it is going.

Author: operator. Recorded 2026-08-30.

---

## 1. The observation

There is now a *class* of open-weight models that do not fit on one AMD Strix Halo
box (Ryzen AI MAX+ 395, gfx1151, 128 GB unified) and are not so large as to be out
of reach of two. That class is the entire reason a dual-Strix rig is interesting,
and it is growing:

| model | engine that runs it today | status |
|---|---|---|
| **DeepSeek-V4 Flash** | `AlexKGwyn/ds4-vllm` (vLLM fork + overlay) and `antirez/ds4` / forks (native C/CUDA/HIP) | working, incl. a TB4 custom all-reduce for exactly our cabling |
| **Qwen3.8-Flash-Next** | **this repo** (vLLM fork + patches) — and `Baekpica/ds4` `dfm` (native C/CUDA, single-box, mixed-quant) | in bring-up |
| **GLM 5.3 Flash** | antirez, in progress | wait for the community to find the real optimizations, then absorb wholesale |

The pattern repeats. Each new model of this class arrives with a bespoke engine, a
bespoke quantization story, and a bespoke multi-node story — and each one re-solves
the same three problems from scratch.

## 2. The problem with the current shape

We have already lived the failure mode. This machine's NixOS config imports
**ds4-rocm** (the DwarfStar HIP build) via `hellas-ai/nix-strix-halo`, and this repo
carries a *separate* vLLM fork for Qwen. That is two engines, two build systems, two
operational manuals, two sets of transport code, two sets of SSD-streaming code — for
two models that differ far less than their engines do.

Extrapolated to three or four models, it is untenable. Nobody maintains four
inference engines.

Note the asymmetry that makes this worth fixing rather than tolerating: the *models*
diverge much less than the *engines* do. Concretely, `Baekpica/ds4` and this repo
serve **the same checkpoint** — same HF revision `f5d08274`, same 48 layers, same
512-expert top-10 MoE, same 12 QSA / 36 GDN split, and literally the same PLE table
(51,200,245,760 parameters, 128 shards under
`model.language_model.layers.1.ple.ple_embedding.ngram_embedding.shard_N.weight`).
Two completely independent engines were written to serve one artifact.

## 3. The proposal: `dualflash`

**One vLLM fork, carried on our GitHub, purpose-built for one hardware situation:
two Strix Halo boxes joined by two Thunderbolt/USB4 cables (plus a dedicated
ethernet wire for control), fronted by llama-swap.**

The framing is deliberately borrowed: llama.cpp is the single-node amalgamation that
made "one engine, many models" the default expectation, and llama-swap is how we
already front it. `dualflash` is that same bargain for the dual-Strix tier — with
llama-swap in front of it exactly as it sits in front of llama.cpp today, so a
dual-box model is just another catalog row.

The name states the constraint, which is the point. This is not a general
distributed-inference project. It is an engine for **two Strix Halo boxes and the
class of models that need exactly two.**

### What is genuinely shared (the substrate)

These are per-*hardware*, not per-model, and every model in the class pays for them:

- **gfx1151 enablement.** KFD topology detection, APU VRAM reporting, skinny-GEMM
  disable, Wave32 LDS overflow, fp8 MoE admission via in-kernel bf16 upcast, fp8 dot
  upcast, MemorySnapshot routing. Our `patches/0001..0012` are already this layer,
  and essentially none of it is Qwen-specific.
- **The TB4 transport and the two-rank collective.** A custom small-message
  all-reduce that bypasses RCCL's proxy stack on the cable. ds4-vllm's `tbv_ar2.hip`
  is already written for gfx1151 and is Apache-2.0; `jyatesdotdev/strix-rdma`'s
  zero-copy `thunderbolt_stream` path measures better still. Whatever wins, **one**
  implementation should serve every model.
- **Weight/embedding streaming from NVMe.** Qwen needs it for the 51.2 GB PLE table;
  DeepSeek-V4-class and GLM-class models need it for routed experts. The bounded
  cache, the prefetch discipline, and the zero-copy gather are one mechanism with
  two consumers.
- **TP=2 orchestration.** Pair bring-up, env doctrine, transport rungs, receipts,
  the bench matrix, the A/B protocol. Already ~all of `host/` and `bench/`.

### What is genuinely per-model

- Architecture module and weight loader (`qwen4_exp`, `deepseek_v4`, …).
- The MTP/draft-head wiring and its acceptance behavior.
- Quantization policy — and this is where the dual-box thesis pays off most visibly.
  Single-box engines quantize because they *must*: `Baekpica/ds4` runs Q4_K/Q5_K/Q6_K
  mixed-quant to fit 128 GB, a quality regression bought for memory. With two boxes
  and ~61 GiB/rank we keep **block-FP8**, which on gfx1151 is also the
  bandwidth-ideal format. **Dual-Strix is not merely "more memory" — it is a better
  quantization tier**, and that is the sharpest argument for the whole project.

### Shape

A single vLLM fork with per-model directories over a shared `dualflash/` substrate
(transport, collective, NVMe streaming, gfx1151 enablement), one container recipe,
one host toolchain, one llama-swap-facing service contract. Whether it is a genuine
merge of the two forks or a re-hosting of both model paths onto our vLLM base is an
open question — see below.

## 4. Sequencing

1. **Finish flashnext.** TP=2 first light, cp-bench, cp-close, receipts for R4. A
   generalization built before the first model works is a fantasy.
2. **Land the substrate pieces as substrate.** When the custom all-reduce and the
   bounded NVMe streaming go in, write them so they are not Qwen-shaped. This is the
   only thing OUTLOOK asks of the current campaign, and it costs nothing.
3. **Absorb DeepSeek-V4.** ds4-vllm is a vLLM fork already, already gfx1151, already
   dual-cable. This is the cheapest second model we will ever get.
4. **Wait on GLM 5.3 Flash.** Let the community find the real optimizations, then
   take them wholesale rather than re-deriving them.
5. **Rename and re-front.** `dualflash` with llama-swap in front.

## 5. Open questions

- **Merge or re-host?** ds4-vllm is an *overlay* (patch series + rootfs files) on a
  vLLM pin, not a checked-out fork. Reconciling its pin with ours is the first real
  engineering question, and it may be cheaper to re-host its model path than to merge
  its overlay.
- **Licensing.** Mixed. `tbv_ar2.hip` is Apache-2.0 (the collective), while the
  verbs/kernel stack under it is GPL-2.0, and `strix-rdma` is GPL-2.0 at the repo
  level with per-file MIT on userspace. A combined engine needs a licence map before
  it needs a merge, and the boundary must be drawn at the kernel/userspace seam.
- **How much stays out of tree?** Every out-of-tree kernel module is a vermagic
  treadmill. The in-tree `thunderbolt_stream` path is strategically preferable to any
  out-of-tree driver even at some latency cost, precisely because `dualflash` is
  meant to outlive a kernel bump.
- **Does llama-swap's model lifecycle survive a two-box service?** Loading a
  dual-node model is not a process spawn; it is a cluster bring-up. The catalog row
  we already carry (`flashnext-fp8`) is declared as an artifact and explicitly *not*
  a llama-swap row. That exception is the thing `dualflash` has to dissolve.
- **Two cables: what are they actually for?** Not bonding. ds4's tbv stack dedicates
  the second cable's NHI to the RX zero-copy rail; upstream OdinLink cannot use two
  at all without `max_devices=1`. Whether cable B becomes a real second rail or stays
  parked is an empirical question for the bench, not a design assumption.
