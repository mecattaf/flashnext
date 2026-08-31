# patches/ — mirror of `mecattaf/vllm` branch `flashnext`

Base: `8e4e036a311604800334989485b4ee23925956da` (head of upstream PR #54129,
`Trosfy:ple-mmap-upstream`, which carries the full #53896 model support).
Branch head mirrored here: `a05fa983fb4699dc1dc704776e3fed014c215cc7` — 13
commits, one `.patch` each, numbered in apply order. `scripts/verify-fork.sh`
enforces `n_patches >= n_commits` against the live branch.

**These patches are provenance and review artifacts, not build inputs.** The
nix lane builds the fork *branch* (Mechanism A: `inputs.vllm-fork` follows
into `nix-strix-halo`'s `vllm-src`); feeding this mirror into the derivation's
`patches` would double-apply every hunk and abort `patchPhase`. Substrate-only
fixups that must ride outside the branch belong in a separate directory, not
here.

Delta grammar: `+N` (no deletions), `+N/-M`, or `new (LINES)`.

## Nix substrate compatibility (patches 0001–0002)

| file | Δ | purpose |
|---|---|---|
| `pyproject.toml` | +4/-1 | `[build-system] torch == 2.13.0` → `2.11.0`. The gfx1151 substrate ships exactly one torch (TheRock 2.11.0+rocm7.15.0a20260719, cp313), and `nix-strix-halo overlays/therock-vllm.nix:269` hard-aborts on `--replace-fail '"torch == 2.11.0"'` if the literal is absent — Gate 0. Audited before pinning: no fork code needs torch ≥ 2.12 (`requirements/build/rocm.txt` already pins 2.11.0; `env_override.py` still carries the `<2.12` backports; all 54 `torch::stable/headeronly` names under `csrc/` resolve in 2.11.0 headers — presence, not signatures). `requirements/build/cuda.txt` deliberately stays 2.13.0 for the container lane, which builds `--no-build-isolation` and never resolves `build-system.requires`. |
| `tools/check_nix_substrate.py` | new (210) | In-tree, seconds-fast guard for packaging invariants that live *outside* this repo: the 5 `--replace-fail` literals + `rm` target the nix overlay needs, the pre-image anchors of nixpkgs patches 0002/0003/0005, and a 22-name stripped-dependency import scan over the fork-added model surface. Without it a routine refactor breaks the nix lane with no local signal until a multi-hour HIP rebuild aborts. `--warn-only` is the kill switch; failure prints the exact missing literal or `file:line` to stderr and exits 1. Run clean against the assembled head. |

## Cherry-picks from open upstream PRs (patches 0003–0006, `Cherry-picked-from:` trailers)

| file | Δ | purpose |
|---|---|---|
| `vllm/platforms/__init__.py` | +30 | #46110: `_kfd_topology_has_amd_gpu()` — ROCm platform selection from `/sys/class/kfd` (vendor 0x1002 + non-zero `gfx_target_version`) when amdsmi cannot enumerate, no HIP init. |
| `vllm/platforms/rocm.py` (0003) | +36/-2 | #46110: `_query_gcn_arch_from_kfd_topology()` resolves `_GCN_ARCH` as `gfx<major><minor><stepping:x>` (hex stepping is what makes gfx90a come out right); the `torch.cuda` ultimate fallback now `warning_once`s instead of firing silently. |
| `tests/test_rocm_kfd_topology.py` | new (60) | #46110: fixture-driven pins on the topology parser (110501→gfx1151 path, NVIDIA-vendor and missing-dir rejection). Logic validated standalone on this box. |
| `vllm/platforms/rocm.py` (0004) | +63 | #40963: `is_integrated_gpu()` (sysfs VRAM > 4× HIP aperture ⇒ APU) and a `mem_get_info()` override serving (free, total) from amdgpu GTT counters with an 8 GiB reserve. On this tree the override is dead on the KV path (profiling moved to `torch.accelerator.get_memory_info`, #44825) — patch 0011 completes it; kept verbatim for provenance and for the one remaining caller. |
| `vllm/model_executor/layers/utils.py` | +11/-2 | #51511: `and not on_gfx1151()` in `use_skinny` — wvSplitK/LLMM1 are pathologically slow on RDNA3.5; fall back to torch GEMM. No log by design: the impl is a per-layer custom op and a logger call is a torch.compile graph break; `VLLM_ROCM_USE_SKINNY_GEMM` remains the whole-path kill switch. |
| `tests/model_executor/layers/test_rocm_unquantized_gemm.py` | +36 | #51511: new gfx1151-fallback test (verbatim upstream) + `on_gfx1151 → False` pinned in all six existing tests (upstream pinned its three; the three tests this tree grew since would otherwise read the *real* predicate and fail on the target hardware). |
| `csrc/libtorch_stable/sampler.cu` | +5 | #46012: merge launch of `top_k_per_row_decode` — the exact custom op the amd/ QSA indexer calls — drops 1024→512 threads under `USE_ROCM`; cub BlockRadixSort's ranking counters scale with threads/wave-size and 1024 overflows 64 KB LDS on wave32. `2048 % 512 == 0` keeps `kNumFinalItemsPerThread` integral. Three gfx950-gated 1024-thread launches above it are untouched (runtime-gated, never launched on gfx1151); a contingency patch dropping them to 512 exists at `docs/CONTINGENCY-sampler-cu-remaining-1024-launches.patch` — apply only on an LDS *build* error, it pessimises gfx950. |
| `tests/kernels/test_top_k_per_row.py` | +4/-1 | #46012: large-vocab decode test gate widened from CUDA-only to CUDA-or-ROCm. |

## Fork-local hardening + APU memory completion (patches 0007, 0011)

| file | Δ | purpose |
|---|---|---|
| `vllm/envs.py` (0007) | +7 | `VLLM_ROCM_APU_UNIFIED_MEMORY` (default on = #40963 behaviour; `=0` is the one-flag revert to stock discrete accounting). The quality bar demands a kill switch per mechanism; upstream #40963 ships none. |
| `vllm/platforms/rocm.py` (0007) | +50/-4 | Loud verdict log with both measured totals in `is_integrated_gpu()`, loud warnings on every sysfs failure path (upstream swallows them with bare `except: pass`), and `@lru_cache(maxsize=8)` — `release_device_memory_under_pressure` calls it per module during weight loading; globbing sysfs + `hipMemGetInfo` each time is not acceptable. |
| `vllm/utils/mem_utils.py` (0011) | +22/-7 | The commit that makes #40963 real on this tree. `MemorySnapshot.measure`'s integrated-GPU branch was NVIDIA-Tegra-shaped: `free = psutil` (host RAM) against `total =` HIP aperture drives `cuda_memory = total − free` **negative** (poisoning `non_torch_memory`) and sizes `request_memory` off the ~16 GiB aperture instead of the 128 GiB GTT. Now dispatches by platform: ROCm APUs take both free and total from the GTT-backed `mem_get_info()` override; the psutil free-only override stays byte-identical for the NVIDIA UMA devices it was written for. Same kill switch as 0007. |

## Block-FP8 admission on gfx1151 — the world-first pieces (patches 0008, 0012)

| file | Δ | purpose |
|---|---|---|
| `vllm/model_executor/layers/fused_moe/utils.py` | +36 | The `FN_FP8_MOE` latch (read once at import, default **on**) + `fp8_moe_gfx1151_admitted()` + cached `fp8_moe_gfx1151_dot_upcast()` (adds the `triton < 3.8.0` bound of #52970 — newer Triton lowers fp8 `tl.dot` on gfx1151 itself). `on_gfx1151` imported inside the function so `vllm.platforms.rocm` never imports on a non-ROCm box. |
| `vllm/model_executor/layers/fused_moe/experts/triton_moe.py` | +29/-1 | The oracle admission: `_supports_quant_scheme` gains an `elif fp8_moe_gfx1151_admitted():` appending **only** `(kFp8Static128BlockSym, kFp8Dynamic128Sym)` — this checkpoint's routed-expert scheme — with `info_once` at actual admission. Per-tensor/per-channel stay refused (they take the `fp8_fast_accum` dot with no block scales to validate against). `FN_FP8_MOE=0` restores the stock `NotImplementedError: No FP8 MoE backend supports the deployment configuration.` byte-for-byte; `supports_fp8()` itself is untouched, blast radius is the MoE oracle only. |
| `vllm/model_executor/layers/fused_moe/fused_moe.py` | +22 | The kernel half: `FORCE_FP8_DOT_UPCAST` `tl.constexpr` (default False) converting the loaded A/B tiles to bf16 before `tl.dot`; block scales still multiply in fp32, so it is the same block-scaled GEMM without FP8 WMMA. `invoke_fused_moe_triton_kernel` is the tree's **sole** launch of `fused_moe_kernel`, so one hunk covers gate/up, down, and the chunked non-modular path. |
| `tests/kernels/moe/test_gfx1151_fp8_admission.py` | new (130) | GPU-free monkeypatched pins: admission, kill switch, gfx1151-only scope, per-tensor refusal, Triton version gate, kernel-signature drift guard. |
| `vllm/model_executor/layers/quantization/utils/fp8_utils.py` (0012) | +46 | The **linear** twin, without which the MoE runs and every dense block-FP8 projection does not: `TritonFp8BlockScaledMMKernel.is_supported` admits any CUDA-alike device, so gfx1151 reaches `_w8a8_triton_block_scaled_mm` with fp8 operands in `tl.dot`. Same #52970 mechanism, same `ROCm + on_gfx1151() + triton < 3.8.0` gate, own kill switch `FN_FP8_LINEAR` (default on). |
| `tests/kernels/quantization/test_gfx1151_fp8_linear_upcast.py` | new (84) | Pins the linear gate: version bound, kill switch, gfx1151-only scope, signature drift guard. |

## The amd/ PLE port — VLLM_PLE_MMAP + FP8 stack (patches 0009–0010)

| file | Δ | purpose |
|---|---|---|
| `vllm/models/qwen4_exp/{nvidia→common}/ple_mmap.py` | move + +7/-4 | The mmap module is platform-neutral (its one platform touchpoint, `dispatch_key`, is `"CUDA"` on ROCm too) but lived under `nvidia/`, so a ROCm build never imported it and `VLLM_PLE_MMAP=1` was a **silent no-op** — the op never registered. Relocated beside `common/ple.py`; docstring names both importing trees. Both trees now reach the *same* module object (op registers exactly once). |
| `vllm/models/qwen4_exp/nvidia/{ple_layer,model}.py`, `tests/.../test_ple_mmap.py`, `vllm/envs.py` | +4/-4 | Import-path follow-ups of the move, plus the `VLLM_PLE_MMAP` doc pointer in `envs.py`. No nvidia behaviour change. |
| `vllm/models/qwen4_exp/amd/ple_layer.py` | +241/-13 | The five wiring sites of ple-54129.md mirrored into amd/ — W1 `cast` import; W2 module-scope `ple_mmap` import; W3 constructor swap behind `ple_mmap.enabled()` with `check_cudagraph_safety` + `validate_shards_for` and a loud log (the flag takes ROCm off FULL cudagraphs and off the AMD gather op; `VLLM_PLE_MMAP=0` default is the kill switch); W4 forward mmap branch placed *before* the AMD gather op (the placeholder has no `params_dtype`; the hashing must run inside the widened op boundary or `.numel()`-derived slicing specializes under torch.compile), with the trigram hashing factored into `_hash_ngram_ids` — byte-identical body, and the *name* is load-bearing (the op body probes it); W5a/W5b `load_weights` scale interception → `ple_mmap.set_weight_scale` and shard-drop with `weights_streamed = True`. Plus Blocker A at full nvidia parity (verified byte-identical): `Qwen4ExpPLEFp8EmbeddingMethod`, `_get_ple_embedding_quant_method`, `_get_embedding_weight_scale`/`_dequantize_embeddings` (fail-closed on a scale-less FP8 table), and `quant_config=`/`params_dtype=` plumbing — without it an FP8 checkpoint's `ngram_embedding.weight_scale` is an orphan (`ValueError`) and the table is bare-cast to bf16 with the block scale discarded. One deliberate AMD-only addition: `_embedding_output_dtype()` allocates the gather-op output at the table's real dtype, because a bf16 buffer would make `output.copy_()` a scale-discarding FP8→BF16 cast before `_dequantize_embeddings` ever sees fp8. No fnuz relabelling anywhere: gfx1151 is OCP e4m3 (`is_fp8_fnuz()` is gfx94-only) and the checkpoint bytes are e4m3 on every platform. |
| `vllm/models/qwen4_exp/amd/model.py` | +14/-3 | The two 5-line `build_tables` blocks after each `load_weights` + imports. `diff` against `nvidia/model.py` is now **empty**. |
| `tests/models/qwen4_exp/test_ple_mmap_amd.py` | new (352) | GPU-free pins on every site above: same-module identity, `_hash_ngram_ids` name, env-on/off constructor swap, both cudagraph guards firing from the AMD constructor, scale-keep/shard-drop loading, FP8 method selection, output-buffer dtype, dequant round-trip and the fail-closed no-scale raise. |

## ROCm 7.14 overlapping host registration on the engram path (patch 0013)

Measured, not inherited. RUN3-BRIEF §16.1: ds4's `rocm_host_copy_probe`, run in
`flashnext:dev` on this coordinator 2026-08-31, passes **only** in
`--expect-overlap-rejected` mode — `adjacent-malloc range=1 host_register=part
or all of the requested memory range is already mapped`, then `adjacent-malloc
range=1 h2d_copy=invalid argument`. Both halves reproduced again in-process
while writing the commit (register code 712, then `hipErrorInvalidValue` on a
plain H2D out of the rejected pointer). §15.1: the registration is page-rounded,
so two logically *disjoint* slices of one mmap collide once their rounded spans
share a page — with a ~51.2 GB engram table sliced on non-page boundaries that
is the common case. It is a model-**load** issue: a load failure or a silent
fallback, never slow decode.

| file | Δ | purpose |
|---|---|---|
| `vllm/models/qwen4_exp/common/ple_mmap.py` | +364/-7 | The port, by shape not by copy, of ds4-strix-halo-tp-odinlink `rocm/ds4_rocm_runtime.cuh:5513-5600` (`cuda_model_range_ptr`, commit 8d45d16). `page_rounded_span()` keeps ds4's `:5518-5523` arithmetic because the *rounding* is what manufactures the collision; `mapping_span()` is the mitigation §16.1 points at (`page-aligned-register` and `anonymous-mmap-register` both pass cleanly) — `np.memmap` already maps from a page-aligned start covering exactly the pages the array needs, so registering that extent is the same pages with none of the rounding and cannot pull a neighbour's page in. `MmapPleTable.host_register()` is **never fatal**: a rejected span is the expected 7.14 outcome, so it latches `overlapping`, says why, and the load continues. `to_device()` carries the half that is easy to miss — after a rejected registration the *plain* copy fails too, and it does not need this table to have registered anything, so an unlatched table tries the plain copy and latches on exactly that failure (anything else re-raises). `staged_copy_to_device()` is ds4's `:5566` loop and preserves its three load-bearing properties: the pinned bounce is allocated **only** on the overlap path so the ordinary path pays nothing, it is **capped at 64 MiB** whatever the tensor size so the extra RSS is bounded, and it is freed on success **and on every error exit** (`resize_(0)`, so the block is gone at that statement, not at the caching allocator's convenience). `_latch_mapping_unsupported()` guards ds4's second, silent failure mode at `:5548` — `cudaErrorNotSupported`/`cudaErrorInvalidValue` disables host mapping for the *whole process*, permanently, and upstream says nothing; here it logs at **error** level with the word `LATCHED`, because a warning is invisible in a load's log volume. `close()` unregisters every span before dropping the memmaps those addresses live in. |
| `vllm/envs.py` (0013) | +8 | `VLLM_PLE_MMAP_REGISTER` (default **0**). The registration is the new mechanism and stays opt-in — the quality bar is a kill switch per mechanism. The staged-copy fallback has no switch by design: a page-adjacent registration *anywhere* in the process poisons copies out of these pages, so the fallback must be armed whether or not we registered anything. Listed in `ignored_factors`: it changes load-time host registration, never the compiled graph. |
| `tests/models/qwen4_exp/test_ple_mmap_host_registration.py` | new (569) | GPU-free pins, `cudart` and the pinned allocator stood in so every real error code is drivable: the rounding arithmetic and §15.1's collision claim as an executable assertion, the alignment mitigation (two shards' mapping spans never overlap), rejection-is-survivable with the loop continuing past it, the process-wide latch plus its loud log, the bounce's overlap-only allocation / 64 MiB cap / free on both the success and the error exit, the poisoned-copy latch and its retry, the no-swallow path for unrelated `RuntimeError`s, and the knob's default-off. |

Ran on hardware, in `flashnext:dev` on gfx1151 (HIP 7.14.60850, torch
2.13.0+rocm7.14.0): 30 new tests pass; `test_ple_mmap.py` (82),
`test_ple_mmap_amd.py` (15) and `test_ple.py` (11) still pass; ruff check +
format clean; `tools/check_nix_substrate.py` passes. On the real device a
synthetic table registers its 4 shard mappings with **zero** collisions (the
alignment mitigation working), a deliberate colliding re-registration of one of
those spans returns 712, and the staged copy returns byte-exact rows.

## Not yet executed anywhere

Patches 0001–0012. No engineer box had torch when they were written; every
`.py` passed `py_compile` + repo-config ruff (check + format) and the C++ was
reviewed, but **zero pytest ran and nothing compiled for HIP**. Patch 0013 is
the first to land with its tests actually run (see above), against the
`flashnext:dev` image built from `bdb6f04`. First-hardware checklist: `_GCN_ARCH` one-liner, the
`float8_e4m3fn → bfloat16` cast probe, `pytest` on the four new test files +
`test_ple_mmap.py`, one tiny-shape MoE forward (LDS risk R1: default ROCm
config stages 2×32 KiB bf16 B-tiles = the whole gfx1151 LDS — a tuned
`BLOCK_SIZE_N=64` config JSON is the ready mitigation), and
`triton.__version__` PEP-440 sanity.
