# ds4-vllm lift manifest — exact, pinned

**Source tree:** `/home/tom/Downloads/ds4-vllm`
**Revision:** `a8f620d3032b23271b1969123168a561d0fd882a` (`git rev-parse HEAD`, 2026-08-28)
**Working tree:** clean (`git status --porcelain` → empty output) [M]
**Upstream license:** Apache-2.0 (`LICENSE:1-4` = Apache License Version 2.0) [S]
**Tree size:** 57 files, 13,351 lines total (`find … | xargs wc -l`) [M]

Evidence grades: **[S]** read from source this session · **[M]** measured (command + literal
output) · **[CL]** claimed by a doc in-tree, not independently verified.

All patch line numbers below are line offsets **into**
`container/patches/vllm-upstream.patch` (2,717 lines) unless a `b/` target path is named.
All rootfs paths are abbreviated `SITE = container/rootfs/opt/venv/lib/python3.12/site-packages`.

---

## 0. Executive corrections to the campaign record

Three record claims do **not** survive re-pinning. Details in the numbered sections.

| # | Record said | On-disk at `a8f620d` | Grade |
|---|---|---|---|
| 10 | "piecewise-cudagraph eager-break around the TP all-reduce, commit `671e659`" | **No such hunk, no such commit.** `grep -rn 671e659 .` → zero hits. The only `breakable_cudagraph.py` change is a `DS4_BREAKABLE_SYNC` stream-sync loop (patch:1777-1802). The "eager break" text is an *unchanged upstream context line* (patch:204). And production runs `--enforce-eager` (`host/ds4-vllm-manual-serve.sh:115`), so `replay()` is inert. | [M]/[S] |
| 4 | MANIFEST is authoritative on how patches apply | `MANIFEST.md:8` says "The Dockerfile does **not** apply these; it `COPY`s the final files from `../rootfs`." **False.** `container/Dockerfile:76` runs `git apply -p1 … vllm-upstream.patch`. MANIFEST.md:8 is stale. | [S] |
| 7 | "`build-modules.sh` pin lines for thunderbolt-ibverbs **and rdma-core**" | `build-modules.sh` pins **no** rdma-core. rdma-core lives only in `container/Dockerfile:61` and is pinned to a **branch tag `v57.0`**, not a commit SHA. | [M]/[S] |

Plus: **3 of 12 MANIFEST "new (N)" line counts are wrong** (§4.4), and `AGENTS.md` §0.2
twice cites a "§5" that does not exist in the file (§6).

---

## 1. The routing power-of-2 fix (the one MANDATORY patch)

### 1.1 Location — re-pinned, record confirmed

Record cited "~line 1353". **Exact.**

```
$ grep -n "_routing_compute" container/patches/vllm-upstream.patch
1353:--- a/opt/venv/lib/python3.12/site-packages/vllm/third_party/triton_kernels/routing_details/_routing_compute.py
1354:+++ b/opt/venv/lib/python3.12/site-packages/vllm/third_party/triton_kernels/routing_details/_routing_compute.py
```
[M]

- Target file: `vllm/third_party/triton_kernels/routing_details/_routing_compute.py` [S]
- Single hunk header: `@@ -55,9 +55,11 @@` at patch:**1355** [S]
- Hunk body: patch:**1356-1368** [S]
- Δ per MANIFEST: `+4/-2` (`MANIFEST.md:47`); verified by
  `tests/test_patchset_packaging.py::test_manifest_deltas_match_their_patches` which
  **passes** (§4.5) [M]

### 1.2 The hunk, quoted verbatim (patch:1355-1368)

```diff
@@ -55,9 +55,11 @@
 
     tl.static_assert(N_EXPTS_ACT * BLOCK_M <= 32768)
 
-    local_offs = tl.arange(0, N_EXPTS_ACT * BLOCK_M)
+    N_GATES_PAD: tl.constexpr = triton.next_power_of_2(N_EXPTS_ACT * BLOCK_M)
+    local_offs = tl.arange(0, N_GATES_PAD)
+    in_block = local_offs < N_EXPTS_ACT * BLOCK_M
     offs = pid_m * BLOCK_M * N_EXPTS_ACT + local_offs
-    expert = tl.load(ExptIndx + offs, mask=(offs < n_gates), other=-1).to(tl.uint32)
+    expert = tl.load(ExptIndx + offs, mask=(offs < n_gates) & in_block, other=-1).to(tl.uint32)
 
     # stable-sort by expert ID:
     kv_pairs = ((expert << 16) | local_offs).to(tl.uint32)
```
[S]

**Mechanism.** Triton's `tl.arange(0, X)` requires `X` to be a power of two. When
`N_EXPTS_ACT * BLOCK_M` is not a power of two (top-k routing where `N_EXPTS_ACT` is e.g.
6 or 10), the stock line is a compile-time failure. The fix rounds the lane count *up* to
`N_GATES_PAD = next_power_of_2(...)` and re-introduces the true bound as an explicit
predicate `in_block`, AND-ed into the load mask so padded lanes read `other=-1`. The
subsequent `kv_pairs = ((expert << 16) | local_offs)` stable-sort is unchanged and safe
because `-1 → 0xFFFFFFFF` as uint32 sorts to the tail. [S, inference from the quoted code]

**Why it is mandatory for top-10 routing:** exactly because `10 * BLOCK_M` is never a
power of two for any power-of-two `BLOCK_M`. [S — reasoning from `tl.arange` semantics
visible in the diff; the *claim* that it is mandatory is the campaign's, graded CL]

### 1.3 Adjacent hunks it depends on (the MoE "latching" set)

The MoE/GEMM section of `MANIFEST.md:42-50` groups five files. `_routing_compute.py` is
one of them. Its true build-order dependency is on `target_info.py`, because
`opt_flags.py` and `opt_flags_amd.py` both import `get_cdna_version` **from**
`triton_kernels.target_info` and the patch *renames* the originals.

**(a) `target_info.py` — patch:1369-1433** (Δ `+39/-4`, `MANIFEST.md:48`) [S]
Two hunks: `@@ -23,8 +23,30 @@` (patch:1371) and `@@ -41,14 +63,27 @@` (patch:1403).
It renames the three `@triton.constexpr_function` originals to `_*_uncached`
(patch:1399, 1408, 1414) and republishes plain-Python memoised wrappers
(patch:1418-1433) over a `_DS4_CACHE` dict + `_DS4_MISS` sentinel (patch:1384-1394).
The sentinel is load-bearing and the code says so: *"has_tma_gather/has_native_mxfp
return False on ROCm, and a falsy cached value must still count as a hit or nothing
memoises"* (patch:1389-1390). [S]

> **Hard dependency for a fork.** Anything importing `get_cdna_version` now gets a
> *non-constexpr* function. The patch's own comment (patch:1381-1383) states the
> originals are retained "in case anything needs the constexpr form from inside a
> `@triton.jit` body -- nothing in this tree does (verified by grep)". A Qwen fork
> that adds a jit body calling `get_cdna_version()` would break. [S]

**(b) `opt_flags.py` — patch:1236-1352** (Δ `+72/-3`, `MANIFEST.md:45`) [S]
Five hunks: `@@ -1,11 +1,54 @@` (1238), `@@ -55,7 +98,7 @@` (1293),
`@@ -95,7 +138,7 @@` (1302), `@@ -104,6 +147,32 @@` (1311), `@@ -295,7 +364,7 @@` (1344).
Latches three per-call invariants — `_ds4_cdna_version()` (1266-1270), `_ds4_n_cu()`
(1273-1277), `_ds4_backend()` (1280-1284) — plus a **single dict comprehension read of
all seven `DS4_MOE_*` env vars at import** (patch:1287-1288):

```python
_DS4_MOE = {k: os.environ.get("DS4_MOE_" + k)
            for k in ("BM", "BN", "NW", "NS", "BK", "KEEP_MFMA", "WPE")}
```
Rationale quoted at patch:1249-1256: `make_opt_flags` runs "~92x per decode step" and
"The seven `DS4_MOE_*` lookups go through `os._Environ.get` -> `Mapping.get` ->
`decodevalue`, ~644 times per step." [S]

The gfx1151 tile override is at patch:1315-1340, gated `if not is_cdna4 and
_ds4_cdna_version() == -1` (1316) then `if block_m < 128` (1321) → forces
`block_m=16; block_n=64; num_warps=4; num_stages=3` (1322), with the comment
"16x64 beats stock 32x256 by 1.2-2.1x across m=6..2048 on gfx1151 (WMMA 16x16 + small
LDS)" (1318-1319). [S/CL — the perf figure is CL]

**(c) `opt_flags_amd.py` — NEW, `SITE/vllm/third_party/triton_kernels/matmul_ogs_details/opt_flags_details/opt_flags_amd.py`, 57 lines** [M]
Not in the patch (whole file new). Mirrors the same latching locally:
`_ds4_cdna_version()` at :17-21, `_ds4_n_cu()` at :24-28, used at :36, :47, :55.
Its header comment states it is "kept local rather than imported from there, which
would be circular" (:12). Note `compute_block_nk` at :31 hard-forces `block_k = 128`
at :55-56 — this is the value `DS4_MOE_BK` overrides from `opt_flags.py:1335-1336`. [S]

**Lift verdict:** `_routing_compute.py` is a standalone 13-line hunk with **no code
dependency** on the latching set. The latching set is a *performance* adjacency, not a
correctness one. Lift the routing hunk alone if you only need top-10 routing to compile.
[S — derived from reading all four hunks]

---

## 2. The two ADAPT files

### 2.1 `SITE/ds4_topk.py` — 457 lines [M] (MANIFEST "new (457)" ✅ matches)

Full path: `/home/tom/Downloads/ds4-vllm/container/rootfs/opt/venv/lib/python3.12/site-packages/ds4_topk.py`

**Public interface** (all [S]):

| Symbol | Line | Signature |
|---|---|---|
| `select_topk` | :441-457 | `(logits, topk_tokens, row_starts=None, row_ends=None, out=None) -> Tensor` — **the entry point**, docstring at :448 says "Entry point used by `rocm_aiter_mla_sparse`. Honours `DS4_TOPK=0`." |
| `topk_indices_ascending` | :280-354 | `(logits, topk_tokens, row_starts=None, row_ends=None, out=None) -> Tensor` — the Triton path |
| `topk_indices_ascending_reference` | :367-402 | pure-torch definitional reference, tests only ("slow", :359) |
| `topk_indices_torch_fallback` | :408-438 | "The current production two-sort path, verbatim, for A/B and fallback" (:413) |
| `_topk_rows_kernel` | :111-268 | `@triton.jit`, one workgroup per row |
| `_okey` / `_okey_torch` | :65-83 / :360-364 | order-preserving fp32→int32 key |

**What it replaces in vLLM.** Module docstring :3-11: replaces *both*
`_topk_indices_torch` (full stable descending sort) **and** `_canonicalize_topk_indices_`
(second sort) in `vllm/v1/attention/ops/rocm_aiter_mla_sparse.py`. [S]

**Wiring — not a monkeypatch.** It is a plain import behind an `lru_cache`d resolver
inserted by the patch into `rocm_aiter_mla_sparse.py`:

```python
# patch:783-795
+@functools.lru_cache
+def _sparse_topk_impl():
+    """The gfx1151 deterministic top-k kernel, or None if it will not build."""
+    if os.environ.get("DS4_TOPK") == "0":
+        return None
+    try:
+        import ds4_topk
+
+        return ds4_topk.select_topk
+    except Exception as exc:  # pragma: no cover - deployment guard
+        print(f"[ds4_topk] unavailable, using the torch two-sort path: {exc}",
+              flush=True)
+        return None
```
Consumer wrapper `_sparse_topk(...)` at patch:798. [S]
Note the **double kill-switch**: `DS4_TOPK == "0"` is checked both at patch:786 and
independently at `ds4_topk.py:40`. [S]

**gfx1151-specific bits** [S]:
- `_TILE = 2048`, `_NUM_WARPS = 8` (:54-55) with the wave-size reasoning at :51-53:
  "2048 fp32 = 8 KiB per iteration; with `num_warps=8` (**512 lanes at wave64**) that
  is 4 elements/lane, which holds the `tl.cumsum` in the emit pass to 11 shuffle steps."
  → **wave64 is an AMD/RDNA assumption baked into the tile size.**
- `_HIST_MODE` / `DS4_TOPK_HIST` (:49, :43-48): `"hist"` = 8-bit digits/256 bins via
  `tl.histogram`; `"sum"` = 4-bit/16 bins via masked `tl.sum`, existing because
  "`tl.histogram`'s lowering is the only part of this kernel whose **ROCm-backend
  support** is not obvious from the Python source" (:45-46).
- `_PAD_TILE = 1024  # >= any topk we serve (512)` (:56) — hard-codes the DS4 top-512.

**JIT-stall discipline (critical to replicate).** :27-30: "no shape-dependent compile
key. Every size is a runtime argument marked `do_not_specialize`, so at most two kernel
variants are ever built (prefill with row bounds, decode without) and no decode step can
trigger a JIT stall." Enforced at :103-110:
`do_not_specialize=["width", "topk", "logit_stride_row", "out_stride_row"]`. [S]

**Determinism contract** :13-24 and :235-243: no float accumulation, no atomics; output
slot is an exact integer prefix count, "a bijection onto `[0, n_sel)` that no scheduling
choice can perturb". [S]

### 2.2 `SITE/ds4_tl_indexer.py` — 594 lines [M] (⚠ MANIFEST:38 says "new (476)" — **STALE by 118 lines**)

Full path: `/home/tom/Downloads/ds4-vllm/container/rootfs/opt/venv/lib/python3.12/site-packages/ds4_tl_indexer.py`

**Public interface** (all [S]):

| Symbol | Line | Role |
|---|---|---|
| `fp8_mqa_logits_tl` | :314 | `(q, kv, weights, cu_seqlen_ks, cu_seqlen_ke)` — **prefill** indexer entry |
| `fp8_paged_mqa_logits_tl` | :486 | `(q, kv_cache, weights, context_lens, block_tables, max_model_len)` — **decode** indexer entry |
| `cached_bf16_weight` | :343 | `(weight, weight_scale, block_size, m)` — the hook (§2.3) |
| `w8a8_block_bf16_direct` | :370 | `(x, weight, weight_scale, block_size, m)` — `DS4_W8A8_BF16_DIRECT` path |
| `w8a8_block_fp8_bf16` | :390 | `(qx, weight, x_scale, weight_scale, block_size, output_dtype)` — `DS4_W8A8_BF16` path |
| `_idx_qat` | :49 | hadamard128 + fp4 QAT round-trip |
| `_build` / `_kernel` | :177 / :232 | prefill TileLang kernel + cache |
| `_build_decode` / `_decode_kernel` | :259 / :292 | decode TileLang kernel + cache |
| `_maybe_profile` / `_maybe_expert_union` | :95 / :84 | instrument activation hooks |

**What it replaces in vLLM.** Docstring :2-4: "Drop-in replacements for
`fp8_mqa_logits_torch` / `fp8_paged_mqa_logits_torch` (next_n==1), running the O(M*N)
compute on a bf16 WMMA TileLang kernel instead of a Python loop. Validated: prefill rel
~1.6e-3, decode rel ~1e-7 vs the torch fallbacks." [S; the validation figures are CL]

**Wiring — plain import, two env-gated early-returns in the patch** [S]:
- decode: patch:717-722, `DS4_TL_DECODE` **default "1"** →
  `return _tl.fp8_paged_mqa_logits_tl(...)`
- prefill: patch:730-733, `DS4_TL_PREFILL` **default "1"** →
  `return _tl.fp8_mqa_logits_tl(...)`
- w8a8 GEMM: patch:1541-1543, `_DS4_W8A8_BF16` → `_tl.w8a8_block_fp8_bf16(...)`
- w8a8 direct: patch:1496-1513, `_DS4_W8A8_BF16_DIRECT` → `_tl.w8a8_block_bf16_direct(...)`

Note the patch **overrides `apply_weights`** on `TritonFp8BlockScaledMMKernel`
(patch:1471-1532) — that is the one place this behaves like a subclass override rather
than a plain call, and it falls through with `return super().apply_weights(...)` at
patch:1532. [S]

**gfx1151-specific bits** [S]:
- `_build(...)`: `block_Q = 1  # gfx1151: block_Q=1 + threads=256 validated correct at
  H=64,D=128` (:179).
- `_fit_config(block_Q, heads, D, budget=56*1024)` (:221): *"Pick (block_N, num_stages)
  so Q+K shared buffers fit **gfx1151's 64KB LDS**"* (:222). Ladder `ns in (2,1)` ×
  `bn in (128,64,32,16)` (:225-228), fallback `(16, 1)` (:229).
- Decode constants `_DEC_BLOCK_N = 64`, `_DEC_THREADS = 256` (:255-256) with a
  **correctness** warning at :251-254: *"threads MUST be 256 here: at 128 threads
  (2 warps) the block_N x heads(64) score tile is only partially computed -> WRONG
  logits (verified: threads=128 gave max_abs_err ~3e2 vs 0.0 at 256; the sweep that
  favored 128 measured only speed). block_N>=256 overflows the 64KB LDS."*
- Bucketing to avoid hipcc recompiles: `_KV_BUCKET = 512` (:67, rationale :63-66) and
  `_PREFILL_KV_BUCKET = 8192` (:312, rationale :305-311: *"a ~2s hipcc recompile per
  chunk with the GPU idle"*).
- `rocm_unquantized_gemm_impl` selected over `F.linear` for the wvSplitK skinny GEMM
  (:407-412), with the profile note *"Profiled 2026-07-12: aten::mm via Cijk_*MT16x16
  = ~22% of decode GPU time"* (:410) [CL].

### 2.3 `cached_bf16_weight` hook — RE-PINNED

Record cited `:343-350`. **Re-pin: `ds4_tl_indexer.py:343-367`.**
- `def cached_bf16_weight(weight, weight_scale, block_size, m):` — **:343** ✅ (record exact)
- docstring — **:344-351** (record said 350; the docstring closes on **351**)
- body — :352-367
[M — `grep -n "cached_bf16_weight"` → 343, 383, 395]

**Skip-during-cold-cache logic, quoted (:344-357):**

```python
    """The bf16 dequantised copy of a block-scaled fp8 weight, or None.

    None means "do not take the bf16 path". That happens when the cache is cold
    and M is large: the startup memory-profiling forward runs at large M with an
    empty cache, and building the copy there inflates the measured footprint and
    shrinks the KV cache allocation. Decode (M <= 32) populates the cache;
    prefill reuses entries that already exist unless DS4_W8A8_BF16_PREFILL=0.
    """
    key = weight.data_ptr()
    wb = _BF16_WCACHE.get(key)
    if wb is not None:
        return wb if (m <= 32 or _BF16_PREFILL) else None
    if m > 32:
        return None
```
[S]

**Decision table** (derived from :352-357):

| cache state | `m <= 32` (decode) | `m > 32` (prefill/profiling) |
|---|---|---|
| **warm** (hit) | return `wb` | return `wb` if `_BF16_PREFILL` else `None` |
| **cold** (miss) | build + cache + return | **return `None`** ← the skip |

Supporting state:
- `_BF16_WCACHE = {}` at :339, keyed on `weight.data_ptr()` (:352) — *storage pointer*,
  not module identity.
- `_BF16_PREFILL = os.environ.get("DS4_W8A8_BF16_PREFILL", "1") != "0"` at :342,
  comment :340-341.
- The `M <= 32` threshold appears **twice as a bare literal 32** (:355, :356) — no named
  constant. A Qwen fork with different decode batch geometry must change both. [S]
- Both callers honour `None`: `w8a8_block_bf16_direct` :384-385 returns `None` (caller
  falls back), `w8a8_block_fp8_bf16` :396-399 falls back to `w8a8_triton_block_scaled_mm`.
- The `M > 32` guard's purpose is restated independently in the patch at
  patch:1493-1495: *"Returns None (-> stock path) when the bf16 weight is not cached and
  M is large, which is what keeps the startup memory-profiling forward on the fp8 path
  and the KV cache allocation unchanged."* [S]

### 2.4 hadamard128 / fp4_act_quant warning comments — TWO locations

**Location A — `ds4_tl_indexer.py:9-21`** (the `DS4_IDX_OFFICIAL` header block) [S]:

```python
# --- DS4_IDX_OFFICIAL=1: reproduce the official DeepSeek-V4 indexer QAT graph ---
# The official inference graph (HF snapshot inference/model.py Indexer.forward and
# Compressor.forward with rotate=True) applies, to BOTH indexer Q rows and indexer
# compressor K rows, after RoPE:
#     x -> hadamard128(x) / sqrt(128) -> fp4_act_quant(x, block=32, inplace)   (QAT sim)
# before the relu(q.k)*w scoring. ds4.c implements the same (dsv4_indexer_qat_*),
# and its comment warns the top-k selection "is not the model's graph" without it.
# vLLM's FP8 indexer path skips both steps. ...
```
The literal warning phrase — *its comment warns the top-k selection "is not the model's
graph" without it* — is at **:15**. `fp4_act_quant` is named at **:13** and again at
**:51**. The equivalence argument (power-of-two fp8 storage scale ⇒ score-time rotation
is equivalent to pre-quant) is at :16-20. [S]

**Location B — `ds4_tl_indexer.py:320-323`** (inside `fp8_mqa_logits_tl`) [S]:

```python
    # NOTE: the indexer QAT rotation (Hadamard-128) is applied PRE-fp8-quant in
    # the fused q/compressor-K kernels (gated by DS4_IDX_OFFICIAL), not here — a
    # score-time rotation is a no-op for the dot product (H is orthonormal). See
    # ds4-vllm-indexer-qat-missing memory.
```
⚠ This **contradicts** Location A's :16-20 claim that applying it "HERE, to the
dequantized-unscaled rows at score time, is exactly equivalent". Location B says it is
applied in the fused kernels instead. Location B is the later/operative statement (the
function body). Two more copies of the flag live in the patch at **patch:306** and
**patch:532** (`_DS4_IDX_OFFICIAL = __import__("os").environ.get("DS4_IDX_OFFICIAL","0") == "1"`),
targeting `fused_compress_quant_cache.py` and `fused_indexer_q.py` — consistent with
Location B. Debug prints at patch:361 and patch:626 (`[DS4_QAT] indexer-K/Q APPLY_QAT=…`). [S]

Implementation: `_hadamard128_matrix` :27-36 (7× `torch.kron` of the 2×2 base, scaled
`128**-0.5`), `_fp4_consts` :39-46 (levels `[0,.5,1,1.5,2,3,4,6]`, mids for
`bucketize`), `_idx_qat` :49-61 (`amax` over `[-1,4,32]` blocks, `s = exp2(ceil(log2(amax/6)))`,
clamp ±6, bucketize, rescale, → bf16). [S]

---

## 3. The three VENDOR instruments

All three are venv-top-level modules under `SITE/`, i.e. importable as bare names inside
the container. None uses an import hook or `sitecustomize`; all are lazily imported from
a call site. [S]

### 3.1 `SITE/ds4_synctrace.py` — 93 lines [M] (MANIFEST "new (93)" ✅)

**Activation: NO env var of its own.** Driven from
`ds4_tl_indexer._maybe_profile` and therefore shares the `DS4_PROFILE` window.
Docstring :22-23: *"Driven from `ds4_tl_indexer._maybe_profile` so it shares the
DS4_PROFILE window (no new env var, hence no ray restart to propagate one)."* [S]

Call chain [S]:
- `ds4_tl_indexer.py:97` — `if os.environ.get("DS4_PROFILE") != "1": return`
- `ds4_tl_indexer.py:100` — `if _PCALLS == _PSTART` (default 60, :74)
- `ds4_tl_indexer.py:104-105` — `import ds4_synctrace; ds4_synctrace.reset(); ds4_synctrace.install()`
- `ds4_tl_indexer.py:119` — `elif _PCALLS == _PSTOP` (default 160, :75)
- `ds4_tl_indexer.py:152-155` — `report(_steps)` then `uninstall()`;
  `_steps = max((_PSTOP - _PSTART) / 21.0, 1e-9)   # 21 indexer calls/step` (:153)

**Interface** [S]: `install()` :54 · `uninstall()` :66 · `reset()` :77 ·
`report(steps=1.0) -> str` :81 (top-30 by frequency, :90).

**Mechanism.** Monkeypatches `torch.Tensor` methods in place (`setattr(torch.Tensor,
name, wrapper)` at :51; originals saved in `_orig` :50 and restored at :70).
Targets, :35: `("item", "tolist", "cpu", "numpy", "nonzero", "__float__", "__int__")`.
Each wrapper records one `sys._getframe(1)` keyed
`f"{filename-after-site-packages}:{lineno} {co_name}() [{method}]"` (:43-45). [S]

**Stated limitation (:18-20)** — replicate this caveat verbatim: *"boolean-mask indexing
(`x[mask]`) reaches nonzero() inside C++ `at::index`, not through the python method, so
it is NOT counted here."* [S]

Design justification :8-16: `with_stack=True` captures "~3,200 ops per step, which pegged
the worker at 94% CPU"; this is "~25 events per step". [CL]

### 3.2 `SITE/ds4_expert_union.py` — 128 lines [M] (MANIFEST "new (128)" ✅)

**Activation: `DS4_EXPERT_UNION=1`, plus a lazy install from the decode path.** [S]

Env surface, self-documented at :24-30 and implemented :38-41:

| Var | Line | Default | Effect |
|---|---|---|---|
| `DS4_EXPERT_UNION` | :38 | unset ⇒ **off** | `== "1"` enables |
| `DS4_EU_START` | :39 | `200` | calls to skip (warmup + prefill) |
| `DS4_EU_CALLS` | :40 | `2000` | window size in MoE calls (~46/decode step) |
| `DS4_EU_OUT` | :41 | `~/vllm-prof` | output dir |

Two-stage gate — the module-level `_ENABLED` (:38, :99) **and** the caller's own check at
`ds4_tl_indexer.py:86` (`if _EU_TRIED or os.environ.get("DS4_EXPERT_UNION") != "1": return`).
Install is deliberately lazy; `ds4_tl_indexer.py:80-82` explains: *"wrapping the fused_moe
experts module at ds4_tl_indexer import time would be circular, and by the first paged
decode the MoE layers are long since built."* [S]

**Interface** [S]: `install()` :96 (idempotent, :99) · `_record(topk_ids,
num_local_experts)` :49 · `_flush()` :76. `atexit.register(_flush)` at :123.

**What it monkeypatches** [S]: `vllm.model_executor.layers.fused_moe.experts.
gpt_oss_triton_kernels_moe.make_routing_data` — rebound at :119
(`m.make_routing_data = wrapped`), original stashed on `wrapped._ds4_orig` :118.
Wrapper signature :111: `(topk_ids, topk_weights, num_local_experts, *a, **kw)`.

**Why this choke point** (:10-14): *"It is NOT reachable through vLLM's own
`--enable-return-routed-experts`: that capturer hangs off `BaseRouter.route()`, which
this model never calls (routing happens inside the triton_kernels path)."* [S]

**No-sync discipline** (:50, :66-73): device-only accumulation into a preallocated
`[_CALLS, 3] int32` buffer (:64); slot 0 absorbs the `-1` padding *"so no boolean mask
(and therefore no size-dependent sync) is needed"* (:66-67). Single `.cpu()` at
`_flush` :83. Output TSV `expert_union_<pid>.tsv`, header
`# n_rows\tn_distinct\tn_local_experts` (:85-90). [S]

Every failure path swallows the exception (:92, :105, :114-115) — *"a probe must never
take the engine down"* (:92). [S]

### 3.3 `SITE/ds4_offload_batch.py` — 154 lines [M] (⚠ MANIFEST:78 says "new (102)" — **STALE by 52 lines**)

**Activation: no env gate on the module — it is imported unconditionally by the patched
connector and configured by two env vars.** [S]

Import site, `vllm/distributed/kv_transfer/kv_connector/v1/offloading/scheduler.py`
via patch:1934-1940:
```python
+        try:
+            from ds4_offload_batch import (
+                resolve_promote_block_budget,
+                resolve_store_batch_tokens,
+            )
+        except ImportError:
+            logger.warning(
+                "ds4_offload_batch not importable; store batches stay unbounded "
```
Called at patch:1948-1952. Comment at patch:1930-1932: *"Imported inside the function,
like every other ds4_* helper: a missing module then degrades this one behaviour to stock
instead of breaking the import of the whole offloading connector."* [S]

**Interface** [S] (`__all__` at :37-44):

| Symbol | Line | Signature / value |
|---|---|---|
| `resolve_store_batch_tokens` | :82 | `(num_blocks, offloaded_block_sizes, frac=None) -> int \| None` |
| `resolve_promote_block_budget` | :114 | `(num_blocks, frac=None) -> int \| None` |
| `FRAC_ENV` | :46 | `"DS4_OFFLOAD_STORE_BATCH_FRAC"` |
| `PROMOTE_FRAC_ENV` | :47 | `"DS4_OFFLOAD_PROMOTE_FRAC"` |
| `DEFAULT_FRAC` | :52 | `0.25` |
| `DEFAULT_PROMOTE_FRAC` | :67 | `0.5` |
| `_read_frac` / `_budget` | :70 / :139 | internal |

Both env reads go through `os.getenv(env)` at :73; `frac <= 0` ⇒ `None` ⇒ unbounded ⇒
**stock vLLM** (:78-79 doc, :147-148 and :134-135 code). [S]

**Torch-free by design** (:31-32): *"This lives outside `vllm` so the budget arithmetic
is unit-testable without importing torch or the connector's dependency graph."* Confirmed
— the only import is `os` (:35). [M — `grep "^import"` → `import os` only]

Two non-obvious invariants worth lifting verbatim [S]:
- **Store floor** (:100-109): the returned budget is `max(budget_tokens, max(sizes))`
  (:154) and that floor "is load-bearing, not a rounding nicety" — below it a group's
  target lands under its own cursor and `_build_store_jobs` assigns
  `next_stored_block_idx = num_blocks` outright ("it never takes a max"), rewinding the
  cursor and skipping blocks "for good".
- **Promote counted in blocks, not tokens** (:119-127): a sliding-window group's tiny
  numerous blocks "dominate the rate and collapse the budget to a few hundred tokens --
  about 6% of the prompt".

---

## 4. Container discipline

### 4.1 `container/Dockerfile` — 122 lines [M]

**Base image pin — `container/Dockerfile:21`** [S]:
```dockerfile
ARG DS4_BASE="docker.io/kyuz0/vllm-therock-gfx1151@sha256:25fd294fde9f729d1e75f109022ab4496c78190c0a6dc0142440529f7af20e4d"
```
- Digest-pinned (not a tag). Drift guidance at :17-19.
- Base ships vLLM commit `470229c` (:6, and label `ds4.vllm_commit="470229c"` at :121).
- Restated at `MANIFEST.md:3-4`, `THIRD_PARTY_NOTICES.md:14`, `AGENTS.md:161`. [S]

**Three build stages, all `FROM ${DS4_BASE}`** [S]:

| Stage | Line | Purpose |
|---|---|---|
| `rocr-idle-fix` | :26 | rebuild ROCr from `rocm-systems` @ `d34cbb64…` (:35) + cherry-picks `c06ea68a…`, `933596e9…`, `78b874d4…` (:37-39) + `rocr-force-block-indefinite-active-wait.patch` (:46-47) |
| `provider-build` | :58 | rdma-core `-b v57.0` (:61) + thunderbolt-ibverbs @ `76ba39b6…` (:63); applies `packaging/rdma-core-patches/0*.patch` with `git apply -C1` (:65); asserts `libusb4_rdma-rdmav57.so` exists (:68) |
| final | :70 | patch + overlay + copies |

Cherry-picks are made **reproducible** by pinning the committer date (:40-44):
`GIT_COMMITTER_DATE="$(git show -s --format=%cI "$commit")"` plus fixed
`user.name`/`user.email`. Worth replicating. [S]

### 4.2 How patches are applied at build — the two mechanisms

```dockerfile
# Dockerfile:75-77
COPY container/patches/vllm-upstream.patch /tmp/
RUN cd / && git apply -p1 --whitespace=nowarn /tmp/vllm-upstream.patch && rm /tmp/vllm-upstream.patch
COPY --chown=root:root container/rootfs/ /
```
[S]

1. **Modified files (31):** `git apply -p1` with CWD `/`. The patch paths are
   container-absolute (`a/opt/venv/lib/python3.12/site-packages/…`), so `-p1` strips
   `a/` and resolves against `/`. Enforced by
   `tests/test_patchset_packaging.py::test_patches_use_container_absolute_paths` (:185-194). [S]
2. **New files (12):** `COPY container/rootfs/ /` — rootfs mirrors `/`. [S]

Ordering matters and is invariant-checked: `test_modified_files_are_not_also_in_rootfs`
(:99-103) — *"A modified file present in rootfs would COPY over the patched one and
silently pin an older content."* [S]

Post-steps: ROCr DSO overwrite (:81-83), usb4_rdma provider `.so` + `.driver` file
(:89-91, rationale :85-88 — "libibverbs matches a provider to a device BY NAME"),
hipcc/gcc build of `libtbv_ar2.so`/`libtbv_ar.so` (:99-106), stale-`__pycache__` purge
(:111-113), OCI labels incl. `com.github.containers.toolbox="true"` (:117). [S]

⚠ **`MANIFEST.md:8` is stale and contradicts `Dockerfile:76`.** Do not lift the MANIFEST
sentence "The Dockerfile does not apply these; it COPYs the final files from ../rootfs."
The Dockerfile's own comment at :72-74 is correct. [S]

### 4.3 `container/patches/MANIFEST.md` — 100 lines [M], structure to replicate

- **:1-18 header.** Base digest + vLLM commit (:3-4); the three-way count claim
  (:6 "31 modified", :10 "12 new", :12-14 one deliberately excluded byte-identical file
  `aiter_meta/csrc/cpp_itfs/utils.py`); path convention note (:16-18).
- **:20-99 eight themed tables**, each `| file | Δ | purpose |`:

| § | Line | Theme | Rows |
|---|---|---|---|
| DeepSeek-V4 model (AMD/gfx1151) | :20 | model path | 8 |
| Sparse indexer / mid-context retrieval | :32 | **contains `_routing`'s siblings + ds4_topk/tl_indexer/synctrace/expert_union** | 6 |
| MoE / GEMM kernel tuning | :42 | **contains `_routing_compute.py` @ :47** | 6 |
| Distributed all-reduce over TB4 RDMA | :52 | tbv | 3 |
| Scheduler / KV / cudagraph / MTP | :59 | | 5 |
| Disk KV cache (`fs_lru`, distributed) | :68 | | 10 |
| OpenAI API: reasoning + tools | :82 | | 4 |
| Kernel config lookup | :90 | | 1 |
| ROCr / HIP idle CPU fixes | :96 | build-stage replacement | 1 |

- **Δ column grammar:** `+N` when no deletions, `+N/-M` otherwise, or `**new (LINES)**`.
  Machine-checked for modified rows (§4.5); **not** checked for new rows (§4.4).
- Purpose cells carry the *engineering rationale in prose* — e.g. :71 records the
  measured failure the `fs_lru` tier fixes ("0/8 needle recall vs 8/8 fresh at 100%
  reported hits"). This is the density the campaign should replicate. [S]

### 4.4 ⚠ Three "new (N)" counts are wrong — MEASURED

`wc -l` on every rootfs file vs the MANIFEST cell:

| File | MANIFEST says | `wc -l` says | |
|---|---|---|---|
| `ds4_tl_indexer.py` (:38) | `new (476)` | **594** | ✗ off by 118 |
| `ds4_offload_batch.py` (:78) | `new (102)` | **154** | ✗ off by 52 |
| `vllm/models/deepseek_v4/amd/dspark_mtp.py` (:26) | `new (885)` | **932** | ✗ off by 47 |
| `ds4_topk.py` (:37) | `new (457)` | 457 | ✓ |
| `ds4_synctrace.py` (:39) | `new (93)` | 93 | ✓ |
| `ds4_expert_union.py` (:40) | `new (128)` | 128 | ✓ |
| `lru_manager.py` (:71) | `new (485)` | 485 | ✓ |
| `distributed.py` (:72) | `new (345)` | 345 | ✓ |
| `opt_flags_amd.py` (:46) | `new (57)` | 57 | ✓ |
| `gfx1151-GEMM-A8W8_BLOCKSCALE.json` (:50) | `new (15)` | 15 | ✓ |
| `tbv_ar.py` (:56) | `new (255)` | 255 | ✓ |
| `tbv_ar2.py` (:57) | `new (69)` | 69 | ✓ |
[M — literal `wc -l` output]

**Root cause, pinned:** `tests/test_patchset_packaging.py:166-183`
(`test_manifest_deltas_match_their_patches`) short-circuits on `if "new" in delta:
continue` (:173). The Δ audit covers modified rows only. **A Qwen fork replicating this
discipline should extend the test to assert new-row line counts against `wc -l`** — it is
a ~4-line addition and closes a live drift. [S]

### 4.5 `container/verify-patches.sh` — 95 lines [M]: how it proves base → rootfs

**It proves base → *patched*, not base → rootfs.** The two halves are separate. [S]

Mechanism, in order:
1. :29 — reads the base digest **out of the Dockerfile** with
   `sed -n 's/^ARG DS4_BASE="\(.*\)"$/\1/p' container/Dockerfile`, "so the two cannot
   diverge" (:14). Single source of truth.
2. :33-37 — `podman image exists "$BASE"` or bail (exit 2) with the pull hint (~35 GB).
3. :55 — file list derived **from the patch itself**:
   `sed -n "s|^--- a/$SITE/||p" "$COMBINED" | sort -u`.
4. :65-67 — **one** container invocation extracts all base originals:
   `podman run --rm --entrypoint sh "$BASE" -c "cd /$SITE && tar cf - <paths>" | tar xf - -C "$WORK/$SITE"`.
   Comment :64: "per-file `podman run` is ~1s each".
5. :90-93 — the proof:
   `git -C "$WORK" apply -p1 "$COMBINED"` — *"exactly as the Dockerfile will"* (:88).
   `-C "$WORK"` is required "so the stripped paths resolve inside the work tree rather
   than this repo (itself a git checkout)" (:88-89).
6. :95 — success line: `vllm-upstream.patch applies cleanly to the base (N files)`.

**Regeneration mode `--write`** (:43-52, :70-85): file list comes from MANIFEST instead
(regex `\| \`([^`]+)\` \| (?!\*\*new)` at :48), diffs `$WORK` (base) against
`$DS4_PATCH_SRC` (desired final files) with explicit `--label a/… --label b/…` (:77) so
the emitted paths stay container-absolute. Prints a reminder to update the Δ column
(:82-83). [S]

**Division of labour, stated at :15-16:** *"The offline half of these checks -- manifest
vs overlay vs Dockerfile counts -- lives in tests/test_patchset_packaging.py and needs no
image."* [S]

### 4.6 `tests/test_patchset_packaging.py` — 233 lines, 12 tests, all passing [M]

```
$ python3 -m unittest discover -s tests -p 'test_patchset_packaging.py' -v
… Ran 12 tests in 0.030s
OK
```
[M — literal output]

The 12 invariants (the reusable discipline) [S]:

| Test | Line | Invariant |
|---|---|---|
| `test_overlay_is_not_empty` | :63 | guards vacuous passes (`>5` overlay, `>20` rows) |
| `test_no_bytecode_in_the_overlay` | :69 | no `.pyc` / `__pycache__` — COPY would bake stale bytecode |
| `test_every_overlay_file_is_documented` | :79 | overlay ⊆ manifest |
| `test_every_documented_file_ships` | :88 | manifest ⊆ (overlay ∪ patch sections ∪ `NOT_IN_OVERLAY`) |
| `test_modified_files_are_not_also_in_rootfs` | :99 | patch ∩ overlay = ∅ |
| `test_manifest_header_counts_match_the_tables` | :105 | "31 modified"/"12 new" headers vs row counts |
| `test_dockerfile_file_count_matches_the_overlay` | :126 | Dockerfile's *prose* counts (`overlays the (\d+) new files`, `vllm-upstream\.patch, (\d+) files`) vs reality |
| `test_every_modified_file_has_a_reviewable_diff` | :153 | every modified row has a patch section |
| `test_manifest_deltas_match_their_patches` | :166 | **Δ cells recomputed from the diff** (:178-180) — comment :169 notes "how 11 of these came to disagree at once" |
| `test_patches_use_container_absolute_paths` | :185 | all `--- ` lines start `--- a/opt/venv/…` |
| `test_every_patch_file_names_a_shipped_file` | :196 | no orphan `vllm__*.patch`; "Map forwards, never backwards" (:197-199) |
| `test_overlay_python_compiles` | :214 | `py_compile` every overlay `.py`, **cfile into a tempdir** (:218-219) so it does not break the bytecode test |

`NOT_IN_OVERLAY = {"_rocm_sdk_core/lib/libhsa-runtime64.so.1"}` (:31-34) — the one
manifest row built by a Dockerfile stage. [S]

`container/build.sh:13-17` runs this suite **before** `podman build` and aborts on
failure. [S]

**Measured counts** [M]:
- `grep -c "^--- a/" container/patches/vllm-upstream.patch` → **31** (unique: 31)
- `find container/rootfs -type f | wc -l` → **12**
- `grep -c '\*\*new' container/patches/MANIFEST.md` → **12**

---

## 5. `host/` — every file, one line each, plus the env/perf inventory

### 5.1 File inventory

| Path | Lines | Purpose (pinned) |
|---|---|---|
| `host/ds4-config.yaml` | 18 | Site config; flat `key: value` only, "parsed by ds4-config with the Python stdlib, no pyyaml" (:3-4) |
| `host/ds4-config` | 27 | `#!/usr/bin/python3`; each `key: value` → `export DS4_<KEY>=<shlex.quote(value)>` (:23-27). Used as `eval "$("$HOME/ds4-config")"` (:4) |
| `host/ds4-cluster-env.sh` | 148 | **Canonical env**, sourced by ray head, box2 ray worker, and `vllm serve` (:2-3) |
| `host/ds4-cluster-env.rdma.sh` | 25 | `source`s base then pins one unambiguous `NCCL_IB_HCA` (:8-9) |
| `host/ds4-cluster-env.tcp.sh` | 13 | `source`s base, disables IB + both tbv all-reduces (:7-13) — correctness/fallback profile |
| `host/ds4-cluster-restart.sh` | 145 | Full bringup: teardown → containers → ray → serve → verify (:2) |
| `host/ds4-cluster-down.sh` | 35 | The stop half, idempotent (:2-5) |
| `host/container-heal.sh` | 29 | Reconciles "podman says Up but crun is dead (conmon SIGKILLed)" (:3-4); always exits 0 (:7) |
| `host/ds4-vllm-manual-serve.sh` | 130 | The `vllm serve` launcher; runs **inside** the container (:2). "THIS FILE IS THE SOURCE OF TRUTH" (:6) |
| `host/ds4-vllm-warmup.py` | 77 | 2-phase post-start warmup (tiny request, then one `DS4_WARMUP_CTX` prefill) |
| `host/systemd/ds4-vllm.service` | 23 | `Type=oneshot`, `RemainAfterExit=yes`, ExecStart/Stop/StopPost |

### 5.2 `ds4-config.yaml` keys — all 14 (`:5-18`) [S]

| Key | Line | Value | Note |
|---|---|---|---|
| `model` | :5 | `deepseek-ai/DeepSeek-V4-Flash-0731` | "weights needed on BOTH boxes" |
| `transport` | :6 | `rdma` | `rdma \| tcp` — selects `ds4-cluster-env.<transport>.sh` |
| `head_ip` | :7 | `192.168.100.1` | |
| `worker_ip` | :8 | `192.168.100.2` | |
| `container` | :9 | `vllm` | podman container name, same both boxes |
| `rdma_hca` | :10 | `usb4_rdma0` | `NCCL_IB_HCA` pin, rdma only |
| `api_port` | :11 | `1234` | |
| `disk_kv` | :12 | `true` | fs_lru NVMe prefix-KV tier |
| `disk_kv_gib` | :13 | `30` | **per-NODE** disk cap |
| `max_ctx` | :14 | `524288` | `--max-model-len`, "the validated profile" |
| `kv_pin_gib` | :15 | `6` | pinned GPU KV pool |
| `gpu_mem_util` | :16 | `0.83` | **"IGNORED while the KV pin above is set"** |
| `warmup_ctx` | :17 | `2048` | 0 disables |
| `cables` | :18 | `1` | TB cables; 2 dedicates second NHI to RX zero-copy rail |

### 5.3 The env/perf settings inventory the campaign asked for — each pinned

| Setting | Pin | Value | Why (quoted/paraphrased from the file) |
|---|---|---|---|
| **`PYTHONHASHSEED`** | `host/ds4-cluster-env.sh:13` | `0` | :9-12 — "vLLM derives NONE_HASH (the prefix-cache chain seed) from it, so without a fixed value identical token content produces different block filenames every run and the disk cache can never hit across a restart" |
| **`PYTORCH_HIP_ALLOC_CONF` expandable_segments** | `host/ds4-cluster-env.sh:27` | `expandable_segments:True,garbage_collection_threshold:0.85` | :14-26 — GC off by default on a UMA box; 0.85 of ~124 GiB ≈ reclaim at ~105 GiB; expandable_segments is "the fragmentation half"; "Safe with `--enforce-eager` (no graph capture)" |
| **`HSA_ENABLE_INTERRUPT`** | `host/ds4-cluster-env.sh:83` | `${DS4_HSA_INTERRUPT:-1}` | :79-82 — "TheRock ROCm busy-polls (rocr InterruptSignal::WaitRelaxed) -> ~2-3 cores spinning during inference -> CPU thermal throttle. Set 0 to revert to spin" |
| **Cache dirs off tmpfs (Triton/Inductor)** | `host/ds4-cluster-env.sh:49-50` | `TORCHINDUCTOR_CACHE_DIR=$HOME/.cache/torchinductor`, `TRITON_CACHE_DIR=$HOME/.triton/cache` | :45-48 — "the defaults land under /tmp (tmpfs), and the first bringup after a boot then recompiles every kernel -- ~25 min CPU-bound in LLVM before the API can answer" |
| **Cache dir off tmpfs (KV staging)** | `host/ds4-vllm-manual-serve.sh:104` | `DS4_OFFLOAD_MMAP_DIR=${KVDIR%/}-stage` | :99-103 — must be a **SIBLING** of the cache dir, never inside it: "fs_lru owns root_dir exclusively… will `os.remove()` it when it becomes the LRU victim. Nesting staging under root_dir therefore hands the evictor the live mmap." Implementation: `patch:2443` |
| **Per-call-invariant latching (MoE)** | `patch:1261-1288` + `opt_flags_amd.py:13-28` + `patch:1384-1433` | 3 device queries + 7 env reads + 4 target queries | patch:1249-1260; justified by "must be exported before `ray start` so both TP ranks agree, so a per-call read cannot observe anything a first-use read would not" |
| **Per-call-invariant latching (W8A8 flags)** | `patch:1449-1459` | `_DS4_W8A8_BF16`, `_DS4_W8A8_BF16_DIRECT` at import | patch:1449-1451 — "a per-call lookup buys nothing and costs ~255 os.environ hits per decode step" |
| `LD_LIBRARY_PATH` surgery | `host/ds4-cluster-env.sh:89-96` | strips **only** `/opt/rocm/llvm/lib` | :84-88 — that path's runtimes "change ROCr's initialization order and reproduce the AsyncEventsLoop spin even with the patched DSO preloaded" |
| `LD_PRELOAD` of patched ROCr | `host/ds4-cluster-env.sh:103-116` | prepends `…/_rocm_sdk_core/lib/libhsa-runtime64.so.1` | :97-102; `DS4_HSA_PRELOAD=0` disables only the preload |
| `HSA_ENABLE_MWAITX` | `host/ds4-cluster-env.sh:121` | `${DS4_HSA_MWAITX:-0}` | :117-120 — optional fallback for *finite* active waits |
| `PYTHONWARNINGS` | `host/ds4-cluster-env.sh:28` | append `ignore::FutureWarning` | :5-8 — per-step `all_gather_into_tensor` deprecation → journal spam |
| `VLLM_RAY_EXTRA_ENV_VAR_PREFIXES_TO_COPY` | `host/ds4-cluster-env.sh:78` | `DS4_` | :77 — "propagate DS4_* to box2 ray workers (not in ray's default copy prefixes)" — **essential**, without it the two TP ranks diverge |
| NCCL/RDMA block | `host/ds4-cluster-env.sh:29-41` | `NCCL_SOCKET_IFNAME=thunderbolt0`, `NCCL_IB_HCA=usb4_rdma`, `NCCL_IB_GID_INDEX=1`, `NCCL_IB_DISABLE=0`, `NCCL_NET_GDR_LEVEL=0`, `NCCL_IB_TIMEOUT=23`, `NCCL_PROTO=LL`, `NCCL_ALGO=Ring`, `NCCL_IB_RETRY_CNT=7`, `TORCH_NCCL_HEARTBEAT_TIMEOUT_SEC=2400`, `TORCH_NCCL_ENABLE_MONITORING=0`, `NCCL_TIMEOUT_MS=2400000` | — |
| Ray block | `host/ds4-cluster-env.sh:42-44` | `RAY_EXPERIMENTAL_NOSET_ROCR_VISIBLE_DEVICES=1`, `RAY_memory_monitor_refresh_ms=0`, `RAY_memory_usage_threshold=0.99` | — |
| Misc | `host/ds4-cluster-env.sh:51-53` | `HIP_VISIBLE_DEVICES=0`, `VLLM_ROCM_USE_AITER=0`, `VLLM_EXECUTE_MODEL_TIMEOUT_SECONDS=1800` | — |

### 5.4 ⚠ `ds4-cluster-env.sh` MoE knobs — record cite `:127` RE-PINNED

Record cited ":127 MoE knobs". At `:127` sits the **comment header**, not the exports:

- `:127-130` — comment: *"MXFP4 matmul_ogs DECODE kernel config (tuned on this hardware;
  block_k 256 + num_stages 2 is the bandwidth lever the stock heuristic never picks).
  Decode-scoped in opt_flags (only under block_m<128) so prefill is untouched. MUST live
  here so BOTH TP ranks match. Unset the 5 vars to revert."*
- `:144-148` — the **actual exports** (five of them) [S]:

```bash
export DS4_MOE_BN=${DS4_MOE_BN:-32}
export DS4_MOE_NW=${DS4_MOE_NW:-2}
export DS4_MOE_NS=${DS4_MOE_NS:-2}
export DS4_MOE_BK=${DS4_MOE_BK:-256}
export DS4_MOE_WPE=${DS4_MOE_WPE:-1}
```

`opt_flags.py` reads **seven** `DS4_MOE_*` keys (patch:1288: `BM, BN, NW, NS, BK,
KEEP_MFMA, WPE`) but the env file exports only **five**. `DS4_MOE_BM` and
`DS4_MOE_KEEP_MFMA` are implemented-but-never-deployed. The comment at :130 saying
"the 5 vars" is consistent with the env file, not with the reader. [M/S]

Also note :131-135 places the `DS4_IDX_OFFICIAL` comment **inside** the MoE comment
block — the two are textually interleaved and easy to misread when lifting.

### 5.5 Cluster restart script — the two things it encodes (`ds4-cluster-restart.sh:8-23`)

Both are quoted verbatim in the header and both are non-obvious [S]:

1. **REAPING** (:10-15). `ds4-vllm-manual` is a transient `systemd-run` unit supervising
   a `podman exec` **wrapper**, not the process inside the container. Stopping the unit
   "leaves `vllm serve` running, holding its port and ~0.4 GB. Repeated restarts strand
   one husk each." Implementation :63-69: `ps` + bracket-grep `bin/[v]llm serve deepseek`
   (:63, trick documented at :62), SIGTERM → 3s → SIGKILL, then a **hard gate**:
   `[ "$residual" -eq 0 ] || { … exit 1; }` (:69).
2. **RAY WORKER POOL** (:17-23). `ray start` passes
   `--num_prestart_python_workers=<num_cpus>` through with "no env override
   (ray/_private/services.py:1980)"; unbounded that is "32 idle Python workers at ~40 MB
   each, ~1.28 GB that vLLM never touches". Fix: `RAY_NUM_CPUS=${RAY_NUM_CPUS:-4}` (:38)
   → `--num-cpus=$RAY_NUM_CPUS` (:92). Dashboard off for the same reason (:93).

Other pinned structure [S]: config load :31 · `box2()`/`inbox()` ssh/podman helpers
:54-55 · memory drain loop with `<45G` gate :74-80 · `container-heal.sh` on both boxes
:84-85 · head-only `--include-dashboard=false` (:93, "ray PANICs if it is passed to a
worker" :91) · **2-GPU gate** :103-108 (aborts if `ray status` never reports `2.0 GPU`)
· `systemd-run --user --unit=ds4-vllm-manual` :112-115 · API poll 105×20s ≈ 35 min
:119-125 (warm ≈4 min, cold-kernel-cache ≈+25 min, :117-118) · warmup dispatched as a
separate `--collect` transient unit :130-135 · verify block :137-145 incl. the RDMA
readiness grep `tbv_ar2: rank[0-9] ready \(qpn=…\)` (:141) which prints
`!! tbv_ar2 NOT ready -- decode all-reduce is not on RDMA` on miss (:142).

### 5.6 Teardown (`ds4-cluster-down.sh`) [S]

Idempotent (:5). Tolerates a broken config: `eval … 2>/dev/null || true` with hardcoded
fallbacks (:12-16). Same reap loop (:23-27), then `ray stop --force` locally (:29-30) and
over ssh (:31-32). `exit 0` unconditionally (:34).

### 5.7 Warmup (`ds4-vllm-warmup.py`) [S]

Env: `DS4_VLLM_PORT` (:13, dflt `1234`), `DS4_WARMUP_MODEL` (:14, dflt
`deepseek-v4-flash`), `DS4_WARMUP_CTX` (:17, dflt `2048`), `DS4_WARMUP` (:50, dflt `1`;
`0` disables). `wait_ready(timeout_s=1200)` :24. Two phases: (1) `fire("Say ACK.", 12,
300)` :60 — "compiles the ~50 vLLM Triton kernels (topk/w8a8/sparse-attn/metadata) +
decode + MTP drafter" (:55-57); (2) one `WARMUP_CTX`-sized prefill :69 — "one long
prefill grows context through every 8192-bucket" (:64), which matches
`_PREFILL_KV_BUCKET = 8192` at `ds4_tl_indexer.py:312`. Every phase wrapped in
try/except (:58-62, :67-72) — "Best-effort: never fails the service" (:9).

### 5.8 systemd unit (`host/systemd/ds4-vllm.service`) [S]

`Type=oneshot` + `RemainAfterExit=yes` (:14-15); `TimeoutStartSec=45min` (:16),
`TimeoutStopSec=5min` (:17). `ExecStart=%h/ds4-cluster-restart.sh` (:18),
`ExecStop=%h/ds4-cluster-down.sh` (:19), **`ExecStopPost=%h/ds4-cluster-down.sh`** (:23)
with the reason at :20-22: *"ExecStop does not run when ExecStart itself fails; StopPost
does, so a failed bringup still tears the cluster down instead of leaving the serve unit
and ray daemons running behind a 'failed' wrapper."*
**No `[Install]` section** — deliberate, :9-11: "the model takes minutes to load and
claims both GPUs, so the stack comes up only on an explicit start, never at boot.
hy3-llamacpp declares `Conflicts=ds4-vllm.service`".

### 5.9 Serve flags (`ds4-vllm-manual-serve.sh:111-130`) [S]

`--tensor-parallel-size 2` :113 · `--distributed-executor-backend ray` :114 ·
**`--enforce-eager` :115** · `--kv-cache-dtype fp8` :116 ·
`--gpu-memory-utilization ${DS4_GPU_UTIL:-0.83}` :117 ·
`--kv-cache-memory-bytes ${DS4_KV_BYTES:-6442450944}` :118 ·
`--max-model-len ${DS4_MAX_CTX:-524288}` :119 · **`--max-num-batched-tokens 512`** :120 ·
`--tokenizer-mode/--reasoning-parser/--tool-call-parser deepseek_v4` :122-127 ·
`--speculative-config '{"method":"deepseek_mtp","num_speculative_tokens":5,
"disable_padded_drafter_batch":true,"enforce_eager":true}'` :128.

Three memory notes worth lifting (:9-20): `--kv-cache-memory-bytes` must be pinned
because "the bf16 weight cache and the RDMA buffers are allocated AFTER the profiling
pass"; `--gpu-memory-utilization` is "INERT while the pin above is set … (gpu_worker.py:384)";
`--max-num-batched-tokens 512` **not** 2048 because "the indexer/top-k workspace scales
with batch x context and 2048 costs ~10 GiB more at 256K".

⚠ Syntax landmine, :22-24: *"Do not add comments inside the backslash-continued `vllm
serve` command below: a '#' there silently comments out every remaining argument, and
`bash -n` still reports the file as valid."*

Pre-flight degrade check :86-92: if the image lacks `lru_manager.py` **or**
`distributed.py`, warn loudly and set `DS4_DISK_KV=0` rather than let `vllm serve` die on
`"Unknown secondary tier type: 'fs_lru'"` (:81-85).

---

## 6. `AGENTS.md` bring-up doctrine

### 6.1 §0.1 prerequisites boundary — quoted verbatim (`AGENTS.md:35-45`)

> ## 0.1 Prerequisites (verify these exist; do NOT try to synthesize them)
>
> - **2× AMD Strix Halo / gfx1151**, ~128 GB unified memory each, on the same LAN.
> - A **Thunderbolt-4 / USB4 cable** physically connecting the two boxes.
> - Linux with **kernel headers/devel** for the running kernel on each box, `podman`,
>   `distrobox`, `rdma-core`/`libibverbs`, `git`, build toolchain.
> - The model weights **`deepseek-ai/DeepSeek-V4-Flash-0731`** (~150 GB) downloaded
>   on **both** boxes (`hf download deepseek-ai/DeepSeek-V4-Flash-0731`).
> - Root/sudo on both boxes (kernel modules, systemd units).
> - **Secure Boot disabled on both boxes** — the tbv modules are unsigned;
>   a Secure Boot kernel will refuse every `insmod` in §1.

[S] The boundary is in the heading itself: **"verify these exist; do NOT try to
synthesize them"** (:35). Followed by the role-fixing paragraph :47-51 ("Pick roles now
and keep them consistent everywhere").

### 6.2 §1.5 RDMA gate — quoted verbatim (`AGENTS.md:134-150`)

> ### 1.5 Verify RDMA (gate)
>
> ```bash
> ls /sys/class/infiniband/                 # -> usb4_rdma0 (or usb4_rdma5)
> rdma link                                 # port state ACTIVE / PHYS_STATE LinkUp
> cat /sys/class/infiniband/usb4_rdma0/ports/1/gids/1   # NON-zero (RoCEv2 IPv4 GID = index 1)
> ibv_devices                               # lists usb4_rdma0
> ```
> If `gids/1` is all-zero, `thunderbolt0` has no `192.168.100.x` IP yet — fix the IP
> first. If `usb4_rdma*` never appears after a kernel change, the `.ko` vermagic no
> longer matches: rebuild (§1.1–1.2) and `sudo systemctl restart tbv-roce.service`
> on both boxes.
>
> **De-risk option:** RDMA is a performance layer, not a correctness gate — set
> `transport: tcp` in `~/ds4-config.yaml` to run the same cluster over sockets
> (much slower decode). If you want to validate the model path first, skip to
> §2–§4 on TCP now and return to finish RDMA once tokens are flowing.

[S] **The de-risk clause (:147-150) is what makes the campaign's RDMA exclusion legal:
the document itself declares RDMA non-load-bearing for correctness.**

### 6.3 Full gate order

Declared layer order, `AGENTS.md:27-33` [S]:
> Three independent layers, build/verify them in this order:
> 1. **tbv RDMA** (`tbv/`) … "Foundational and the riskiest; do it first."
> 2. **vLLM engine** (`container/`)
> 3. **Host orchestration** (`host/`)

Enumerated gates in document order [S]:

| # | Gate | Pin | Pass condition |
|---|---|---|---|
| G0 | Prerequisites present (not synthesized) | :35-45 | all six bullets, incl. Secure Boot **disabled** |
| G0.2 | Recall/context integrity | :55-68 | `DS4_IDX_OFFICIAL=1` on **both** ranks; `deepseek_v4_encoding.py` patch present |
| G1.1 | matched core+net+ibverbs built | :78-96 | `build-modules.sh` then `sudo install-modules.sh`; "must be one matched set or the box **panics on cable connect**" (:80-82) |
| G1.2 | out-of-tree modules | :98-101 | no separate step (folded into 1.1) |
| G1.3 | userspace provider | :103-112 | container builds its own; host build only for diagnostics |
| G1.4 | stage/load/bring-up **COORDINATED** | :114-132 | then **"reboot BOTH boxes ~together"** (:126-127) |
| G1.5 | **RDMA verify gate** | :134-145 | 4 commands; `gids/1` non-zero |
| G2 | engine build | :154-173 | `distrobox enter vllm -- vllm --version` (:171) **and** `-- ibv_devices` (:172) |
| G3 | host orchestration | :175-184 | env files **byte-identical on both boxes** (:181-182); passwordless ssh box1→box2 (:183-184) |
| G4 | serve | :186-199 | `systemctl --user start ds4-vllm`; the script's own 2-GPU gate + API gate + RDMA verify |

**Hard prohibition, `AGENTS.md:129-132`** (quoted):
> 🛑 **Do NOT** live-reload the core, and **do NOT** stagger per-box ibverbs
> reloads. Both wedge the Thunderbolt HopID/tunnel allocator and require a
> **coordinated reboot of both boxes** to recover. Live-swapping only
> `thunderbolt_net` is the one safe hot operation.

⚠ **Dangling reference.** `AGENTS.md:67` says "must re-pass needle/recall probes at your
target context depth before it ships (**see §5**)". The file's last section is **§4**
(`## 4. Start serving`, :186) and ends at :199. `grep -n "^## " AGENTS.md` yields no §5.
The recall-probe gate is *asserted but not specified* in this repo. [M/S]

---

## 7. `tbv/` — pin structure and the GPL boundary (SCOPE: EXCLUDED)

The campaign **excludes RDMA transport from scope.** For the spec to state the exclusion
precisely, here is exactly what is being excluded and where the pins live.

### 7.1 Pin lines — `tbv/build-modules.sh` (117 lines)

```
25:BASE=503c5ae1e72aa9ed91925dafa3d82ee2e992747f
26:REMOTE=https://git.kernel.org/pub/scm/linux/kernel/git/westeri/thunderbolt.git
27:IBV_BASE=76ba39b630a70accb72f19388eefe48844b50eb8
28:IBV_REMOTE=https://github.com/hellas-ai/thunderbolt-ibverbs
```
[S]

| Component | Pin | Line | Kind |
|---|---|---|---|
| Linux thunderbolt (westeri maintainer tree) | `503c5ae1e72aa9ed91925dafa3d82ee2e992747f` | `tbv/build-modules.sh:25` | commit SHA |
| **thunderbolt-ibverbs** | `76ba39b630a70accb72f19388eefe48844b50eb8` | `tbv/build-modules.sh:27` | commit SHA |
| **thunderbolt-ibverbs** (again, container) | `76ba39b630a70accb72f19388eefe48844b50eb8` | `container/Dockerfile:63` | commit SHA — **same pin, duplicated, no single source of truth** |
| **rdma-core** | `v57.0` | `container/Dockerfile:61` (`git clone --depth 1 -b v57.0`) | **branch/tag, NOT a SHA** |

⚠ **Record correction (item 7).** rdma-core is **not** pinned in `build-modules.sh` —
`grep -rn "rdma-core\|v57" tbv/` (excluding `ibverbs-local.patch`) returns exactly one
hit, `tbv/README.md:42`, and it is prose. [M] The only rdma-core fetch in the tree is
`container/Dockerfile:61`, and `-b v57.0` is a mutable ref. **If the spec claims
"pinned", it must say "pinned by commit for the kernel modules and thunderbolt-ibverbs;
pinned only by release tag `v57.0` for rdma-core."** [S]

Also pinned in `build-modules.sh`: a 10-patch `LOCAL_SERIES` (:29-40) applied with
`git apply -C1` (:74) — the patches are **fetched from the ibverbs clone**
(`SERIES_DIR=$IBVERBS/kernel-workflow/patches`, :47), *not* vendored. The `-C1` fuzz is
deliberate: ":20-21 … applied with `git apply -C1` (the vendored patches carry drifted
context, like nixpkgs' fuzzy patch)". [S]

Local diffs this repo does own: `tbv/ibverbs-local.patch` (3,453 lines — the single
largest file in the tree [M]), applied at :64 with a hard failure gate.

### 7.2 The GPL boundary — the precise statement to replicate

From `THIRD_PARTY_NOTICES.md:3-10` [S]:
> Their sources are **fetched at pinned revisions at build time, not redistributed
> here** — what this repository itself ships is at most a derivative patch against them
> (each patch is licensed like the code it modifies). Original code in this repository
> (the `host/` orchestration, `container/` build tooling, the new engine files under
> `container/rootfs/`, `tbv/` build/bringup scripts, and docs) is licensed under
> [Apache-2.0](LICENSE). The local `tbv/nhi-throttle-mod/` kernel module is
> GPL-2.0 (`MODULE_LICENSE("GPL")`), as kernel modules must be.

**The boundary in one sentence:** Apache-2.0 covers everything the repo authors, **except**
(a) `tbv/nhi-throttle-mod/` which is GPL-2.0 because it is a kernel module, and (b) the
patch files, which inherit the license of what they patch. GPL-2.0 enters the tree **only**
through `tbv/`. [S]

GPL-touching artifacts, exhaustively [S/M]:
- `tbv/nhi-throttle-mod/nhi_throttle.c` (69 lines) + `Makefile` (6) — this repo's own
  code, GPL-2.0
- `tbv/ibverbs-local.patch` (3,453 lines) — derivative of GPL-2.0 thunderbolt-ibverbs
- `LICENSES/GPL-2.0.txt` — 117 lines, GPL v2 June 1991 text [M]
- Nothing else. `container/`, `host/`, `tests/` are Apache-2.0 and GPL-free.

**Spec language for the exclusion (proposed, all facts pinned above):**
> The Qwen campaign lifts `container/` and `host/` only. It **excludes** the entire
> `tbv/` subtree (Thunderbolt-4/USB4 RoCE-RDMA transport: 15 files — build scripts,
> `ibverbs-local.patch`, `nhi-throttle-mod/`, bringup rules, systemd units), the
> `container/native/` all-reduce natives (`tbv_ar.c` 364 lines, `tbv_ar2.hip` 411
> lines), their python wrappers (`SITE/tbv_ar.py` 255, `SITE/tbv_ar2.py` 69), the
> `cuda_communicator.py` hook (`vllm-upstream.patch:1547-1606`), the `provider-build`
> Dockerfile stage (`container/Dockerfile:58-68`) and the provider install
> (`container/Dockerfile:89-91`), and `AGENTS.md` §1–§1.5.
> Excluding RDMA is sanctioned by the source itself: `AGENTS.md:147` states "RDMA is a
> performance layer, not a correctness gate", and `host/ds4-cluster-env.tcp.sh` is the
> supported sockets fallback (`transport: tcp`).
> **Consequence:** with `tbv/` excluded, no GPL-2.0 code enters the derived work; the
> lift is wholly Apache-2.0.

### 7.3 `tbv/` file inventory (for the exclusion list) [M]

`build-modules.sh` 117 · `install-modules.sh` 92 · `ibverbs-local.patch` 3453 ·
`README.md` 103 · `nhi-throttle-mod/nhi_throttle.c` 69 · `nhi-throttle-mod/Makefile` 6 ·
`bringup/tbv-reload-roce.sh` 84 · `bringup/tbv-second-cable-prep.sh` 49 ·
`bringup/tbv-roce-boot.sh` 36 · `bringup/fix-memlock.sh` 16 ·
`bringup/60-rdma-persistent-naming.rules` 6 · `bringup/99-tbv-zc-second-link.conf` 2 ·
`systemd/tbv-thunderbolt-patched.service` 19 · `systemd/tbv-roce.service` 17 ·
`systemd/tbv-thunderbolt-patched.service.d/10-wait-var.conf` 6.

---

## 8. Kill-switch inventory — every `DS4_*` env var

Enumerated by `grep -rhon "DS4_[A-Z0-9_]*"` over the whole tree (excluding `.git`,
`*.pyc`), then each candidate re-pinned to its actual read site. **83 distinct tokens**
matched; the table below keeps only real environment variables, dropping module-internal
names (`_DS4_CACHE`, `_DS4_MISS`, `_DS4_BACKEND`, `_DS4_N_CU`, `_DS4_CDNA_VERSION`,
`_DS4_MOE`, `_DS4_DIRECT_HIT`, `_DS4_QAT_DBG(K)`, `_DS4_PREFILL_MIN_TOKENS`,
`_DS4_PREFILL_INTERVAL`, `_DS4_HSA_RUNTIME`), log-prefix strings (`[DS4_ATTNW]`
patch:173, `[DS4_QAT]` patch:361/626, `[DS4_DBG2]` patch:869, `[DS4_DBG]`
ds4_tl_indexer.py:590), the doc filename `DS4_DECODE_CEILING.md`
(ds4_expert_union.py:5), and the Dockerfile build ARG `DS4_BASE`. [M]

### 8.1 Engine kill-switches (read inside the container)

| Var | Read at | Code default | Deployed value | Effect |
|---|---|---|---|---|
| `DS4_TOPK` | `ds4_topk.py:40`; `patch:786` | `"1"` (on) | `:-1` @ `env.sh:143` | `=0` → torch two-sort fallback. Two independent checks |
| `DS4_TOPK_HIST` | `ds4_topk.py:49` | `"hist"` | — | `"sum"` → 4-bit/16-bin masked-sum radix (portable, slower) |
| `DS4_TL_DECODE` | `patch:718` | `"1"` (on) | — | `!=1` → stock eager paged indexer ("dequantizes the ENTIRE paged cache per call", patch:720) |
| `DS4_TL_PREFILL` | `patch:731` | `"1"` (on) | — | `!=1` → stock torch prefill indexer |
| `DS4_IDX_OFFICIAL` | `ds4_tl_indexer.py:21`; `patch:306`; `patch:532` | `"0"` (**off**) | `:-1` @ `env.sh:135` | `=1` → hadamard128+fp4 QAT scoring graph. **Must match on both ranks** (`AGENTS.md:60-62`) |
| `DS4_W8A8_BF16` | `patch:1452` | unset (off) | `=1` @ `env.sh:55` | `=1` → `w8a8_block_fp8_bf16` bf16 GEMM path |
| `DS4_W8A8_BF16_DIRECT` | `patch:1458` | `"0"` (off) | `:-1` @ `env.sh:64` | `=1` (requires `_BF16`) → skip caller-side fp8 quantisation. **Changes token output** (patch:1489-1491) |
| `DS4_W8A8_BF16_PREFILL` | `ds4_tl_indexer.py:342` | `"1"` (on) | — | `=0` → bf16 path restricted to decode |
| `DS4_KV_BF16` | `patch:190` | `"0"` (off) | — | `=1` → forces plain bf16 contiguous KV layout over fp8_ds_mla |
| `DS4_MOE_BM` | `patch:1288` | unset | **never exported** | `block_m` override (decode-scoped) |
| `DS4_MOE_BN` | `patch:1288` | unset | `:-32` @ `env.sh:144` | `block_n` override |
| `DS4_MOE_NW` | `patch:1288` | unset | `:-2` @ `env.sh:145` | `num_warps` override |
| `DS4_MOE_NS` | `patch:1288` | unset | `:-2` @ `env.sh:146` | `num_stages` override |
| `DS4_MOE_BK` | `patch:1288` | unset | `:-256` @ `env.sh:147` | `block_k` override (stock hard-forces 128) |
| `DS4_MOE_KEEP_MFMA` | `patch:1288` | unset | **never exported** | `="1"` → restore `matrix_instr_nonkdim:16` kwargs (patch:1339) |
| `DS4_MOE_WPE` | `patch:1288` | unset | `:-1` @ `env.sh:148` | `waves_per_eu` override (patch:1340) |
| `DS4_BREAKABLE_SYNC` | `patch:1790` | `"0"` (off) | — | `=1` sync after **every** segment; `=2` after graph segments only. **Inert under `--enforce-eager`** |
| `DS4_MTP_CAPTURE` | `patch:48` | unset (off) | `=1` @ `env.sh:70` | `=1` → allocate the DSpark target-hidden capture buffer |
| `DS4_MTP_MAXSEQS` | `dspark_mtp.py:257` | `"8"` | `:-64` @ `env.sh:76` | Above this the drafter **stops speculating entirely** (`env.sh:71-75`) |
| `DS4_MTP_STAGES` | `dspark_mtp.py:224` | `"3"` | — | drafter stage count |
| `DS4_MTP_POS` | `dspark_mtp.py:81` | `"unshifted"` | — | `=="shifted"` → shifted positions |
| `DS4_MTP_DEBUG` | `dspark_mtp.py:74` | `"0"` | — | debug prints |
| `DS4_TBV_AR` | `patch:1586` | unset (off) | `=1` @ `env.sh:65`; `=0` @ `tcp.sh:12` | v1 TB4-RDMA all-reduce. Patch comment :1580-1581 says it is "**currently inert** -- it fails init with 'slot tensor not page-aligned'" |
| `DS4_TBV_AR2` | `patch:1561` | unset (off) | `:-1` @ `env.sh:69`; `=0` @ `tcp.sh:13` | v2 GPU-poll+progress-thread AR; **takes precedence over v1** |
| `DS4_TBV_AR_GPU` | `tbv_ar.py:77` | `"0"` (off) | `:-1` @ `env.sh:126` | v1 data slots in device memory as dma-buf MRs |
| `DS4_TBV_AR_CHECK` | `tbv_ar.py:129` | `"0"` (off) | — | correctness cross-check |
| `DS4_TBV_AR_DEV` | `container/native/tbv_ar.c:126` | unset | — | explicit RDMA device override (C `getenv`) |
| `DS4_OFFLOAD_MMAP_DIR` | `patch:2443` | `""` → `/dev/shm` | set @ `manual-serve.sh:104` | non-empty → file-backed staging: no `MADV_POPULATE_WRITE`, no `cudaHostRegister` |
| `DS4_OFFLOAD_STORE_BATCH_FRAC` | `ds4_offload_batch.py:46,73` | `0.25` (`DEFAULT_FRAC` :52) | — | `<=0` → unbounded = stock vLLM |
| `DS4_OFFLOAD_PROMOTE_FRAC` | `ds4_offload_batch.py:47,73` | `0.5` (`DEFAULT_PROMOTE_FRAC` :67) | — | `=0` → promote whole matched prefix (stock, thrashes) |
| `DS4_HSA_INTERRUPT` | `env.sh:83` | `1` | `1` | → `HSA_ENABLE_INTERRUPT`; `0` reverts to busy-poll |
| `DS4_HSA_MWAITX` | `env.sh:121` | `0` | `0` | → `HSA_ENABLE_MWAITX` |
| `DS4_HSA_PRELOAD` | `env.sh:111` | `1` | `1` | `=0` → skip the patched-ROCr `LD_PRELOAD` only |

### 8.2 Instrumentation / debug (all default OFF)

| Var | Read at | Default | Effect |
|---|---|---|---|
| `DS4_PROFILE` | `ds4_tl_indexer.py:97` | unset (off) | `=1` → torch profiler over an indexer-call window; **also drives ds4_synctrace** |
| `DS4_PROFILE_START` | `ds4_tl_indexer.py:74` | `60` | window open (indexer calls) |
| `DS4_PROFILE_STOP` | `ds4_tl_indexer.py:75` | `160` | window close |
| `DS4_PROFILE_STACK` | `ds4_tl_indexer.py:77` | `"1"` (on) | `=0` → drop `with_stack` |
| `DS4_EXPERT_UNION` | `ds4_expert_union.py:38`; `ds4_tl_indexer.py:86` | unset (off) | `=1` → wrap `make_routing_data`, count distinct experts |
| `DS4_EU_START` | `ds4_expert_union.py:39` | `200` | calls skipped |
| `DS4_EU_CALLS` | `ds4_expert_union.py:40` | `2000` | window size |
| `DS4_EU_OUT` | `ds4_expert_union.py:41` | `~/vllm-prof` | dump dir |
| `DS4_DBG_NEEDLE` | `ds4_tl_indexer.py:572-573`; `patch:134`, `patch:151`, `patch:668` | unset | needle-recall row probe |
| `DS4_DBG_TOPK` | `ds4_tl_indexer.py:582` | `"512"` | probe top-k |

### 8.3 Orchestration (host-side; config-derived, not kill-switches)

Emitted by `host/ds4-config` from the yaml (`ds4-config:24-27`): `DS4_MODEL`,
`DS4_TRANSPORT`, `DS4_HEAD_IP`, `DS4_WORKER_IP`, `DS4_CONTAINER`, `DS4_RDMA_HCA`,
`DS4_API_PORT`, `DS4_DISK_KV`, `DS4_DISK_KV_GIB`, `DS4_MAX_CTX`, `DS4_KV_PIN_GIB`,
`DS4_GPU_MEM_UTIL`, `DS4_WARMUP_CTX`, `DS4_CABLES`. Derived in
`ds4-cluster-restart.sh:46-52`: `DS4_DISK_KV_BYTES`, `DS4_KV_BYTES`, `DS4_GPU_UTIL`.
Read in `manual-serve.sh`: `DS4_DISK_KV_DIR` (:96), `DS4_DISK_KV_CPU_BYTES` (:107),
`DS4_DISK_KV_STAGE_ON_DISK` (:98). Warmup: `DS4_VLLM_PORT`, `DS4_WARMUP`,
`DS4_WARMUP_MODEL`, `DS4_WARMUP_CTX`. Tooling: `DS4_PATCH_SRC` (`verify-patches.sh:73`),
`DS4_BASE` (Dockerfile ARG :21). [S]

### 8.4 Propagation invariant

`VLLM_RAY_EXTRA_ENV_VAR_PREFIXES_TO_COPY=DS4_` at `host/ds4-cluster-env.sh:78` is what
makes every `DS4_*` reach the box2 ray workers ("not in ray's default copy prefixes",
:77). This is the mechanical basis for the repeated *"must match on BOTH boxes"* claims
(`env.sh:130`, `:133-134`, `:142`; `AGENTS.md:61-62`, `:181-182`) and for the latching
argument at `patch:1258-1260`. **A fork that drops this line silently desynchronises the
two TP ranks.** [S]

---

## 9. `THIRD_PARTY_NOTICES.md` + `LICENSES/` — structure to replicate

### 9.1 `THIRD_PARTY_NOTICES.md` — 22 lines [M]

Structure [S]:
1. **:1** `# Third-party code and licenses`
2. **:3-10** the boundary paragraph (quoted in §7.2) — three claims: *fetched at pinned
   revisions, not redistributed*; *patches inherit the license of what they patch*;
   *original code is Apache-2.0, except the kernel module which is GPL-2.0*.
3. **:12-19** a 4-column table — `| Component | Upstream | License | What this repo ships |`
4. **:21-22** the model-weights carve-out: *"**Model weights**
   (`deepseek-ai/DeepSeek-V4-Flash-0731`) are not included and are governed by their own
   license on Hugging Face."*

The six rows [S]:

| Row | Line | Upstream + pin | License | "What this repo ships" |
|---|---|---|---|---|
| vLLM | :14 | vllm-project/vllm @ `470229c` | Apache-2.0 | derivative patch (31 files) + 12 new rootfs files; links MANIFEST |
| Linux thunderbolt | :15 | westeri/thunderbolt.git | GPL-2.0 | **Nothing redistributed**; links `LICENSES/GPL-2.0.txt` |
| thunderbolt-ibverbs | :16 | hellas-ai @ `76ba39b` | GPL-2.0 | **Nothing redistributed** + this repo's `tbv/ibverbs-local.patch` (GPL-2.0, derivative) |
| rdma-core | :17 | linux-rdma/rdma-core | GPL-2.0 OR Linux-OpenIB | **Nothing redistributed**; built in-image from `v57.0` |
| amd-strix-halo-vllm-toolboxes | :18 | kyuz0 | MIT | **Nothing redistributed**; base pulled by digest |
| ROCR-Runtime | :19 | ROCm/ROCR-Runtime | MIT (NCSA-style) | one patch applied at image build |

**Replicable pattern:** every row's fourth column begins either "Nothing redistributed"
or names the exact derivative artifact and links it. Four of six say "Nothing
redistributed". Every pinned revision in the table is cross-checkable against a build
script (`Dockerfile:21/61/63`, `build-modules.sh:25/27`). [S]

### 9.2 `LICENSES/` — one file [M]

```
$ ls LICENSES/
GPL-2.0.txt      (17,337 bytes, 117 lines)
```
Head: `GNU GENERAL PUBLIC LICENSE / Version 2, June 1991 / Copyright (C) 1989, 1991 Free
Software Foundation, Inc. / 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301, USA`. [M]

Convention: `LICENSES/` holds full texts of licenses **other than** the project's own.
The project's own Apache-2.0 sits at the repo root as `LICENSE` (201 lines, "Apache
License / Version 2.0, January 2004"). SPDX-style filename (`GPL-2.0.txt`). Referenced
by relative link from `THIRD_PARTY_NOTICES.md:15`. [S/M]

**For the Qwen fork:** if `tbv/` is excluded per §7.2, `LICENSES/GPL-2.0.txt` becomes
unnecessary and `LICENSES/` can be omitted entirely — the only GPL-2.0 rows in the table
(:15, :16, and the GPL half of :17) all trace to `tbv/`. Keep the root `LICENSE`
(Apache-2.0) and a reduced `THIRD_PARTY_NOTICES.md` with the vLLM, base-image, and ROCR
rows. [S — derived from the row-to-subtree mapping]

---

## 10. The piecewise-cudagraph eager-break (item 10) — NOT FOUND AS DESCRIBED

**Record claim:** "the piecewise-cudagraph eager-break around the TP all-reduce (commit
`671e659`) — find it in the patch, pin it — it is a cherry-pick candidate."

### 10.1 What was searched, and the literal results [M]

```
$ grep -rn "671e659" . --exclude-dir=.git
(no output)

$ grep -n "eager" container/patches/vllm-upstream.patch
204:         # compressor, MLA attention) runs in the eager break.
720:+        # the eager fallback below dequantizes the ENTIRE paged cache per call.
1785:+        # gfx1151/RDNA fix: hip graph launches and eager kernels interleaved
1788:+        # every segment; =2 syncs only after graph segments (not eager fns).

$ grep -rn "splitting_ops\|breakable\|BREAKABLE" --exclude-dir=.git .
container/patches/vllm-upstream.patch:1777,1778,1787,1790
container/patches/MANIFEST.md:65
```

**Findings:**
- **Commit `671e659` does not appear anywhere in the tree.** The repo has no vendored
  vLLM git history to cherry-pick from; `git log --oneline -5` shows only README edits
  (`bdfc34c…`-style DS4 history is absent; HEAD's five commits are all "Update
  README.md"). [M]
- **patch:204 is an unchanged CONTEXT line**, not an addition. Its hunk
  (`@@ -333,10 +342,29 @@` at patch:201, target
  `vllm/models/deepseek_v4/attention.py`) reads at patch:202-204:
  ```
      # Metadata-independent input GEMMs + RMSNorm stay in the captured
      # graph; the metadata-dependent rest (q up-proj + kv-insert, indexer,
      # compressor, MLA attention) runs in the eager break.
  ```
  Lines begin with a single space (context marker). **The eager-break split already
  exists upstream in the base image; DS4 did not add it.** The DS4 addition in that hunk
  (patch:209-231) is DSpark MTP cross-attention Q/KV sourcing, unrelated to all-reduce. [S]

### 10.2 What IS there — `breakable_cudagraph.py`, patch:1777-1802

The **only** `breakable_cudagraph.py` change. Δ `+18/-2` (`MANIFEST.md:65`). Quoted in
full:

```diff
--- a/opt/venv/lib/python3.12/site-packages/vllm/compilation/breakable_cudagraph.py
+++ b/opt/venv/lib/python3.12/site-packages/vllm/compilation/breakable_cudagraph.py
@@ -210,8 +210,24 @@
     # --- replay ----------------------------------------------------------
 
     def replay(self) -> None:
-        for r in self.segments:
-            r()
+        # gfx1151/RDNA fix: hip graph launches and eager kernels interleaved
+        # on one stream lack write-visibility guarantees between segments
+        # (works on CDNA). DS4_BREAKABLE_SYNC=1 inserts a stream sync after
+        # every segment; =2 syncs only after graph segments (not eager fns).
+        import os
+        sync_mode = os.environ.get("DS4_BREAKABLE_SYNC", "0")
+        if sync_mode == "0":
+            for r in self.segments:
+                r()
+        else:
+            stream = torch.cuda.current_stream()
+            for r in self.segments:
+                r()
+                if sync_mode == "1" or (
+                    sync_mode == "2"
+                    and getattr(r, "__self__", None).__class__.__name__ == "CUDAGraph"
+                ):
+                    stream.synchronize()
```
[S — patch:1777-1802]

This is a **stream-synchronisation** fix for write-visibility between interleaved graph
and eager segments — **not** an eager-break, and it does not mention all-reduce.
It is env-gated **off by default** (`"0"`, patch:1790).

### 10.3 Where the TP all-reduce actually is

The only all-reduce modification is `cuda_communicator.py`, **patch:1547-1606**, Δ `+51`
(`MANIFEST.md:55`), single hunk `@@ -252,6 +252,57 @@` at patch:1549. It prepends two
env-gated RDMA fast paths to `CudaCommunicator.all_reduce` — `DS4_TBV_AR2`
(patch:1561-1578) then `DS4_TBV_AR` (patch:1586-1603) — each falling through to the stock
chain (patch:1604+) when ineligible. It is a *dispatch* change, not a graph/eager change. [S]

`MANIFEST.md:65`'s purpose cell — *"piecewise cudagraph, keeps attention + custom
all-reduce eager"* — is the likely origin of the record's claim. **The diff does not
implement that.** Either the MANIFEST prose describes base-image behaviour the DS4 patch
merely preserves, or it is stale like `MANIFEST.md:8`. [S]

### 10.4 Cherry-pick verdict

Both candidates are **not worth cherry-picking as described**:
- The eager-break split is already upstream at the base image's vLLM `470229c`
  (evidenced by patch:202-204 being context). Nothing to lift.
- `DS4_BREAKABLE_SYNC` is defaulted off (patch:1790) **and inert in production**, because
  `host/ds4-vllm-manual-serve.sh:115` passes `--enforce-eager` (and the speculative
  config re-asserts `"enforce_eager":true` at :128), so no cudagraph is ever captured and
  `replay()` is never reached. It is a debugging lever for a graph-capture regime the
  deployment does not use. [S]

**UNDETERMINED:** whether an upstream vLLM commit `671e659` exists that adds an eager
break around the TP all-reduce. Settling it requires a vLLM git checkout —
`git -C <vllm-checkout> show 671e659` — which is not present on this machine and is
outside the analysis-only boundary of this task. What I looked at: every occurrence of
"eager", "breakable", "splitting_ops", "671e659", and every all-reduce hunk in
`vllm-upstream.patch`; plus `git log` of this repo. Nothing in `ds4-vllm` at `a8f620d`
corresponds to the described change.

---

## Appendix A — commands run this session (reproduction)

```
git -C /home/tom/Downloads/ds4-vllm rev-parse HEAD        -> a8f620d3032b23271b1969123168a561d0fd882a
git -C /home/tom/Downloads/ds4-vllm status --porcelain    -> (empty)
find . -path ./.git -prune -o -type f -print | xargs wc -l -> 13351 total, 57 files
grep -n "_routing_compute" container/patches/vllm-upstream.patch -> 1353, 1354
grep -n "^--- a/\|^@@ " container/patches/vllm-upstream.patch    -> full hunk map (§1-§10)
grep -c "^--- a/" container/patches/vllm-upstream.patch   -> 31
find container/rootfs -type f | wc -l                      -> 12
grep -c '\*\*new' container/patches/MANIFEST.md            -> 12
grep -rn "671e659" . --exclude-dir=.git                    -> (no output)
grep -n "eager" container/patches/vllm-upstream.patch      -> 204, 720, 1785, 1788
grep -rn "rdma-core\|v57" tbv/                             -> tbv/README.md:42 only
grep -n "cached_bf16_weight" .../ds4_tl_indexer.py         -> 343, 383, 395
python3 -m unittest discover -s tests -p 'test_patchset_packaging.py' -v -> Ran 12 tests, OK
wc -l on all 12 rootfs files                               -> §4.4 table
```

## Appendix B — open items for the spec author

1. **[Blocking]** Item 10 as briefed cannot be written into the spec. Either drop it or
   restate it as "adopt `--enforce-eager`; the DS4 `DS4_BREAKABLE_SYNC` lever is inert
   under that flag."
2. **[Correction]** Do not quote `MANIFEST.md:8`; quote `container/Dockerfile:72-77`.
3. **[Correction]** rdma-core is tag-pinned (`v57.0`), not commit-pinned, and lives in
   the Dockerfile, not `build-modules.sh`.
4. **[Improvement]** Extend `test_patchset_packaging.py` with a new-row `wc -l` assertion;
   3 of 12 counts are currently stale (§4.4).
5. **[Gap]** `AGENTS.md` §0.2 promises a "§5" needle/recall gate that does not exist. If
   the campaign wants a recall gate, it must author it — there is nothing to lift.
6. **[Watch]** `ds4_tl_indexer.py:9-21` and `:320-323` give contradictory accounts of
   where the Hadamard rotation is applied. Treat `:320-323` as operative.
7. **[Watch]** `DS4_MOE_BM` and `DS4_MOE_KEEP_MFMA` are implemented but never exported;
   the `M <= 32` bf16-cache threshold is a bare literal at two sites
   (`ds4_tl_indexer.py:355, :356`).
