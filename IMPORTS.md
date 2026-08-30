# IMPORTS — every external artifact, pinned

*The authoritative manifest of what this repo consumes, from where, at which
revision, under which license, and why. Every row was verified against source
on 2026-08-28 (evidence: `specs/flashnext/evidence/`). Anything not listed
here is original work under this repo's Apache-2.0 LICENSE.*

## 1. torch / wheel substrate — the versioning decision

| Component | Pin | Source | Why |
|---|---|---|---|
| torch | `2.13.0+rocm7.14.0` (cp312/cp313 per image python) — **container lane only** | `https://repo.amd.com/rocm/whl-multi-arch/` (AMD **stable** index) | AMD's stable multi-arch index carries this wheel for gfx1151 (verified 2026-08-28 — the index tops out at torch 2.13.0+rocm7.14.0). No nightly needed. Note the fork's `pyproject.toml` `[build-system]` pin is now `torch == 2.11.0` (Gate 0: the **nix** lane's TheRock substrate ships only 2.11.0, and the overlay's `--replace-fail` matches that literal; see `patches/0001-*`). This does not constrain the container lane: vLLM builds `--no-build-isolation` (and `[tool.uv] no-build-isolation-package = ["torch"]`), so `build-system.requires` is never resolved — the container's torch is whatever the image installs, i.e. this row. `requirements/build/cuda.txt` stays at 2.13.0. |
| torchvision | `0.28.0+rocm7.14.0` | same index | torch 2.13-aligned. |
| torchaudio | **omitted** | — | Only needed for vLLM audio extras; it is what capped kyuz0's auto-resolved set at torch 2.11.0. We drop the extra instead of downgrading torch. |
| triton | wheel accompanying the torch set on the same index; fallback: the `pytorch-triton-rocm` wheel torch 2.13.0 declares | same index | Must be < 3.8.0 awareness: the fork's fp8-upcast gate keys on `triton < 3.8` (see PR #52970 pattern below). Record the resolved version in the build receipt. |
| ROCm userspace | rides inside the torch wheel set (`_rocm_sdk_core`, rocm 7.14.0) | same index | Stable. **ROCm 10 is available but not the overnight substrate**: a complete aligned gfx1151/cp312 set (`torch 2.13.0+rocm10.0.0`, `torchvision 0.28.0+rocm10.0.0`, `triton 3.8.0+git4cff872c.rocm10.0.0`, `rocm-sdk-devel 10.0.0` + device wheels) is published at `https://stable.repo.amd.com/rocm/whl-next/` (verified 2026-08-30; wheels dated 2026-08-26). Same upstream versions and the SAME triton git hash as the 7.14 set, so the swap is a version-literal substitution. Held off the overnight ladder only because no upstream has run this engine on ROCm 10 on gfx1151 and the hipcc-compile and KFD/HSA-binding outcomes are unmeasured — the `rocm10-probe` lane answers both as data behind the bench. |

## 2. The vLLM fork — `mecattaf/vllm` branch `flashnext`

**Base: `8e4e036a311604800334989485b4ee23925956da`** — the head of upstream PR
[#54129](https://github.com/vllm-project/vllm/pull/54129) (`Trosfy:ple-mmap-upstream`),
which already carries the complete model support of PR
[#53896](https://github.com/vllm-project/vllm/pull/53896) (`peakcrosser7:release/qwen38next`)
plus a fresh upstream-main merge. #53896's later head commit is CI-only and is
not taken. Verified: the two branches' model trees are byte-identical except
upstream drift in 6 files where #54129 is newer, and `nvidia/ple_mmap.py` which
only #54129 has. (Apache-2.0 throughout.)

### 2.1 Cherry-picks from open upstream PRs (committed with `Cherry-picked-from:` trailers)

| PR | Title | Size | Why we need it |
|---|---|---|---|
| [#46012](https://github.com/vllm-project/vllm/pull/46012) | Fix Wave32 LDS overflow in top-k merge launch | 5 lines | **Load-bearing.** `top_k_per_row_decode` — the exact custom op the `amd/` QSA indexer calls — launches its merge at 1024 threads, exceeding the 64 KB LDS on wave32. 512 on ROCm. |
| [#40963](https://github.com/vllm-project/vllm/pull/40963) | Detect AMD APU, fix VRAM reporting for unified memory | 87 lines | **Load-bearing.** On this APU, hipMemGetInfo reports the small VRAM aperture as total; vLLM's KV-cache sizing then miscomputes. Reads sysfs GTT totals instead (8 GiB reserve). |
| [#51511](https://github.com/vllm-project/vllm/pull/51511) | Disable skinny GEMM on gfx1151 | 96 lines | wvSplitK is "pathologically slow" on gfx1151 per the PR; falls back to torch GEMM. |
| [#46110](https://github.com/vllm-project/vllm/pull/46110) | ROCm detection via KFD topology when amdsmi fails | 170 lines | Robustness: platform + `_GCN_ARCH` detection without HIP init, straight from `/sys/class/kfd`. |

### 2.2 Pattern adoption (not a cherry-pick)

| PR | What we take |
|---|---|
| [#52970](https://github.com/vllm-project/vllm/pull/52970) (amd-xavierwang, "aiter Triton kernels + dsv4 on RDNA3") | The **`FORCE_FP8_DOT_UPCAST` mechanism**: in the block-scaled w8a8 Triton GEMM, load fp8 A/B tiles then `.to(tl.bfloat16)` before `tl.dot`, gated `on_gfx1151() and triton < 3.8`. This is AMD's own sanctioned answer to "no FP8 unit on RDNA3.5". Our fork applies the same transform to the **fused-MoE** Triton kernel and admits `(kFp8Static128BlockSym, kFp8Dynamic128Sym)` for gfx1151 in `TritonExperts._supports_quant_scheme`, behind `FN_FP8_MOE` (default on; `=0` restores the stock loud refusal). The aiter-library parts of #52970 are NOT taken (aiter excluded from the container this campaign). |
| [#44331](https://github.com/vllm-project/vllm/pull/44331) (tuned MoE configs for Radeon 8060S) | Deferred to the 4-bit lane (its configs are `int4_w4a16`, E=256). What we take now is the *mechanism knowledge*: fused-MoE reads per-device tuned-config JSONs — generating `E=512,N=640,device_name=Radeon_8060S_Graphics,dtype=fp8_w8a8` is a named optimization task once first light lands. |
| [#46186](https://github.com/vllm-project/vllm/pull/46186), [#46676](https://github.com/vllm-project/vllm/pull/46676) | The future 4-bit expert lane (W4A16 GEMM on gfx1151; native HIP MXFP4 for RDNA3). Not in this campaign's scope; recorded so nobody re-finds them. |

### 2.3 Our two original patches (the world-first pieces)

*(As landed, the mirror in `patches/` carries these as `0008`/`0012` and
`0009`/`0010` respectively — see `patches/MANIFEST.md` for the full 12-patch
map.)*

| Patch | Content | Upstream destination |
|---|---|---|
| `patches/0008-*` + `patches/0012-*` | Oracle admission for block-FP8 fused-MoE on gfx1151 + fp8→bf16 in-kernel upcast in the fused-MoE Triton kernel (the #52970 pattern applied to MoE) + `FN_FP8_MOE` kill-switch + a loud log line at admission; plus the same upcast in the block-scaled **linear** Triton kernel (`FN_FP8_LINEAR`), without which the dense projections still hand fp8 to `tl.dot` | PR to vllm-project/vllm, referencing #52970 |
| `patches/0009-*` + `patches/0010-*` | The `amd/` PLE port: mmap wiring (5 sites mirroring `nvidia/ple_layer.py`), the FP8 embedding stack (`Qwen4ExpPLEFp8EmbeddingMethod`, gather-time dequant, `weight_scale` interception) the AMD tree lacks entirely, `ple_mmap.py` relocated to `common/` | PR against #54129's head branch (its author has no gfx1151 hardware) |

## 3. Method and code lifted from the community (per the innovation ledger)

| Source | License | What | Mode |
|---|---|---|---|
| [AlexKGwyn/ds4-vllm](https://github.com/AlexKGwyn/ds4-vllm) @ `a8f620d` | Apache-2.0 | Patch-overlay discipline (MANIFEST + verify + 12 packaging invariants, extended); host orchestration shape (husk-reaping, ray pool caps, 2-GPU gate, ExecStopPost); env doctrine (PYTHONHASHSEED=0, expandable_segments, HSA_ENABLE_INTERRUPT=1, caches off tmpfs, ray env-prefix propagation); instruments `ds4_synctrace.py`/`ds4_expert_union.py`/`ds4_offload_batch.py` → adapted as `fn_*` with notices | copy + adapt |
| [kyuz0/amd-strix-halo-vllm-toolboxes](https://github.com/kyuz0/amd-strix-halo-vllm-toolboxes) @ `23cb726` | MIT | Container recipe shape (`Dockerfile.ubuntu-repoamd`): Ubuntu 24.04 + AMD stable wheels + vLLM from source at arbitrary ref | adapt with notice |
| kyuz0 toolbox QA docs | no license | Release-gate *method* only (log-grep lexicon, frontier-logit equivalence, depth-regression rule) | method only, no code |
| [hellas-ai/nix-strix-halo](https://github.com/hellas-ai/nix-strix-halo) @ `f0f2048` | **no license** | Flake **input only** — nothing copied. NCCL/RCCL tuning *values* read as data. Its `tuning` module is explicitly forbidden (would fight our 128 GiB GTT ceiling). Its pair-bench client's `prefill_mean_s` is a TTFT duplicate — we ship our own client. | input use only |
| llama.cpp-lane research (Nathan, EngramHalo) | MIT / research notes | Silicon facts (int4 2.03×, no fp8 unit, LLC no-allocate, wave32-native WMMA), q8_0-KV > fp8-KV, MoE-wants-large-ubatch, MTP k=3 prediction for 1-layer MTP, load-phase page-cache transient | knowledge |

## 4. What is deliberately NOT imported

- **RDMA/tbv anything** (GPL boundary + excluded scope; TCP is the transport of record).
  > **CORRECTED 2026-08-31 (RUN3-BRIEF §4.5).** The GPL half of this exclusion is
  > misapplied to the collective. ds4's own `THIRD_PARTY_NOTICES` places
  > `container/native/tbv_ar2.hip` under **Apache-2.0**. What is GPL-2.0 is the
  > verbs/kernel stack *underneath* it, not the all-reduce itself. The
  > **excluded-scope** half stands on its own merits — that is a scheduling
  > decision, not a licensing one, and it is the one to argue about. Do not cite
  > a GPL boundary as the reason `tbv_ar2` is not lifted.
- **aiter** (no CK build for RDNA; its Triton-only path unproven on gfx1151 for our kernels; revisit post-first-light).
- **nix-strix-halo `.nix` code** (no license), **kyuz0 toolbox shell scripts** (no license on those two repos — method only).
- **ROCm 10 as the overnight substrate** (wheels *do* exist at `stable.repo.amd.com/rocm/whl-next/` — the earlier "no wheels" ruling probed one directory too shallow, corrected 2026-08-30 in `specs/flashnext/evidence/kyuz0-rocm10.md` §11; and the rocBLAS 5.5→5.6 solution-index breakage is ds4-specific — this fork carries no tuned solution indices, only `solution_index=-1` at `vllm/_aiter_ops.py:714`). Measured by the `rocm10-probe` lane; promotion is a morning act against a known-good probe receipt.
- **Community W4A16/AWQ quants** of this model (they quantize attention + GDN — exactly what the vendor kept precise).
- **torchaudio** (caps torch at 2.11; not needed).
