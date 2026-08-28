# Q1 dossier — 512-expert block-FP8 MoE dispatch on ROCm gfx1151

Source-side truth for the B5 gate. Every claim below carries a file:line I opened
this session, or a command + literal output. Grades: **[S]** read from source,
**[M]** measured/counted, **[CL]** claimed by docs/comments, unverified.

## 0. Provenance of the checkouts

| Checkout | HEAD | Subject |
|---|---|---|
| `…/scratchpad/vllm-pr53896` | `89d0bb71aeb2f3e15c16efc69d33c3fbe223a765` | `update CI` |
| `…/scratchpad/vllm-pr54129` | `8e4e036a311604800334989485b4ee23925956da` | `Merge remote-tracking branch 'upstream-main' into ple-mmap-upstream` |

`git rev-parse HEAD` in each tree. **[M]**

The two trees are near-identical: `diff -rq vllm-pr53896/vllm vllm-pr54129/vllm`
reports **20 differing entries** **[M]**. The ones that matter here:

- `vllm/model_executor/layers/fused_moe/oracle/fp8.py`
- `vllm/model_executor/layers/quantization/fp8.py`
- `vllm/model_executor/layers/fused_moe/experts/triton_moe.py`
- `vllm/models/qwen4_exp/{amd,nvidia}/{model,mtp,ple_layer}.py`
- `Only in vllm-pr54129/vllm/models/qwen4_exp/nvidia: ple_mmap.py`

Line numbers below are given per checkout where they diverge. Both were re-pinned
this session; the campaign record's numbers (fp8.py:466-468, rocm.py:870,
oracle/fp8.py:416-419) have all drifted.

---

## BOTTOM LINE (answers the primary question)

**On stock vLLM at both PR heads, a 512-expert block-FP8 MoE on gfx1151 dispatches
to NO kernel class at all. The load aborts loudly in `Fp8MoEMethod.__init__` with
`NotImplementedError: No FP8 MoE backend supports the deployment configuration.`
before a single expert weight byte is read.**

Consequently **the feared BF16 twin of the expert weights cannot be materialized**:
there is no reachable code path in stock vLLM at these heads that upconverts
block-FP8 expert weights to bf16 — not at load, not lazily per-forward, not via a
cache. The ~125 GiB/node blow-up that would kill TP=2 is **not** a stock risk. It is
a risk only under the DS4 patch, and even there only on the **linear** path, never
the experts.

The genuine, previously-unrecorded finding is elsewhere: **the PLE ngram FP8
embedding table has no FP8 handling at all in the AMD tree** (it does in the NVIDIA
tree), so on AMD it either dies loudly on an orphan `weight_scale` tensor or gets
bare-cast to bf16 with the block scale silently discarded. See §5.

---

## 1. Load path: which config claims it, which kernel is selected

### 1a. Quant config class

`Fp8Config` (`quantization/fp8.py`) claims it, not compressed-tensors.

- `get_name()` returns `"fp8"` — `quantization/fp8.py:137-138` (54129) **[S]**
- `from_config()` reads `weight_block_size` from the HF `quantization_config` at
  `quantization/fp8.py:163` and falls back to `modules_to_not_convert` as
  `ignored_layers` at `quantization/fp8.py:165-168` (54129) **[S]**:

```
163:        weight_block_size = cls.get_from_keys_or(config, ["weight_block_size"], None)
165:        if not ignored_layers:
166:            ignored_layers = cls.get_from_keys_or(
167:                config, ["modules_to_not_convert"], None
168:            )
```

This is exactly the checkpoint's shape: `weight_block_size [128,128]` + a 943-entry
`modules_to_not_convert`.

- Dispatch to the MoE method: `get_quant_method()` at `quantization/fp8.py:176-215`
  (54129). For a `RoutedExperts` layer it returns `UnquantizedFusedMoEMethod` if the
  prefix is skipped, else `Fp8MoEMethod(self, layer)` —
  `quantization/fp8.py:199-215` **[S]**. Since only `mlp.experts.*` survives the
  943-entry ignore list, the routed experts get `Fp8MoEMethod`.

- `block_quant` is set from the presence of `weight_block_size`:
  `quantization/fp8.py:481-482` (54129) **[S]**.

### 1b. Weight/activation quant keys

`Fp8MoEMethod.__init__`, `quantization/fp8.py:488-497` (54129) **[S]**:

```
488:        if self.block_quant:
489:            weight_key = kFp8Static128BlockSym
490:            activation_key = kFp8Dynamic128Sym
```

In **53896** this differs — the block shape is refined *before* the oracle and baked
into the key via `create_fp8_quant_key(...)` at `quantization/fp8.py:516-518`,
using `refine_fp8_moe_block_shape` (`oracle/fp8.py:272`) **[S]**. See §1e.

### 1c. Oracle entry point

`select_fp8_moe_backend()` is called at `quantization/fp8.py:500-505` (54129) /
`:529` (53896) **[S]**. Critically this is the **first** thing after the keys are
computed and **before** any refine logic in 54129 — ordering matters, see §1e.

### 1d. Candidate kernel classes enumerated

`_AVAILABLE_BACKENDS`, `oracle/fp8.py:80-95` (54129) / `:81` (53896) **[S]**, in
priority order, with the class each resolves to via `backend_to_kernel_cls()`
(`oracle/fp8.py:139-245`, 54129) **[S]**:

| # | Backend | Kernel class | gfx1151 verdict | Evidence (54129) |
|---|---|---|---|---|
| 1 | `AITER` | `AiterExperts` | **REJECT** — `rocm_aiter_ops.is_fused_moe_enabled()`; `_AITER_ENABLED and _FMOE_ENABLED`, and `VLLM_ROCM_USE_AITER` defaults False | `experts/rocm_aiter_moe.py:452-453`; `_aiter_ops.py:1896-1897`; `envs.py:134` |
| 2 | `FLASHINFER_TRTLLM` | `TrtLlmFp8ExpertsMonolithic/Modular` | **REJECT** — `p.is_cuda() and is_device_capability_family(100)` | `experts/trtllm_fp8_moe.py:140-147` |
| 3 | `FLASHINFER_CUTLASS` | `FlashInferExperts` | **REJECT** — `p.is_cuda() and (…90 / 100 / 120)` | `experts/flashinfer_cutlass_moe.py:129-137` |
| 4 | `DEEPGEMM` | `TritonOrDeepGemmExperts` | **REJECT** — `FallbackExperts` requires **both** members; `is_deep_gemm_supported()` needs `current_platform.support_deep_gemm()` (Hopper/Blackwell only) | `experts/fallback.py:52-58`; `utils/deep_gemm.py:105-110` |
| 5 | `VLLM_CUTLASS` | `TritonOrCutlassExperts` | **REMOVED from list** — `allow_vllm_cutlass=False` passed by `Fp8MoEMethod` | `oracle/fp8.py:389-391`; `quantization/fp8.py:504` |
| 6 | **`TRITON`** | **`TritonExperts`** | **REJECT** — device OK, expert count OK, but **quant scheme fails**: see §1f | `experts/triton_moe.py:148-156` |
| 7 | `MARLIN` | `MarlinExperts` | **REJECT** — `p.is_cuda() and has_device_capability((7,5))` | `experts/marlin_moe.py:605-607` |
| 8 | `HUMMING` | `Humming*Experts` | **REJECT** — `has_humming() and platform.is_cuda()` | `experts/fused_humming_moe.py:385-391` |
| 9 | `BATCHED_DEEPGEMM` | `BatchedDeepGemmExperts` | **REJECT** — `is_deep_gemm_supported()` | `experts/batched_deep_gemm_moe.py:329-330` |
| 10 | `BATCHED_VLLM_CUTLASS` | `CutlassBatchedExpertsFp8` | **REMOVED** — `allow_vllm_cutlass=False` | `oracle/fp8.py:389-391` |
| 11 | `BATCHED_TRITON` | `BatchedTritonExperts` | **REJECT** — activation format is `BatchedExperts`, we are TP (Standard); also same fp8 scheme gate | `experts/fused_batched_moe.py:805-806`; base check `modular_kernel.py:590-591` |
| 12 | `XPU` | `XPUExperts*` | **REJECT** — `current_platform.is_xpu()` | `experts/xpu_moe.py:84-85` |
| 13 | `CPU` | `CPUExpertsFp8` | **REJECT** — `current_platform.is_cpu()` | `experts/cpu_moe.py:232-233` |
| 14 | `HPC` | `HPCExperts` | **REJECT** — `p.is_cuda() and is_device_capability(90) and has_hpc()` | `hpc_moe.py:63-65` |

All 14 **[S]**.

Two classes present in `fused_moe/experts/` are **not reachable from this oracle**:

- `NaiveBatchedExperts` — the only stock fp8 class with a real per-forward weight
  dequant (`experts/fused_batched_moe.py:642`, called at `:693` and `:702`). Its
  support hooks deliberately raise: *"NaiveBatchedExperts is not yet used by an
  Oracle. This method should not be called."* — `experts/fused_batched_moe.py:581-585`
  **[S]**. It is never in `backend_to_kernel_cls`. **[M]** (grep of
  `backend_to_kernel_cls` body, `oracle/fp8.py:139-245`, shows no import of it).
- `Mxfp8EmulationTritonExperts` — see §1g.

### 1e. The 512-expert and top-10 shape checks pass

`TritonExperts.is_supported_config`, `experts/triton_moe.py:84-89` (54129) **[S]**:

```
85:        padded_num_experts = (moe_config.num_experts + 31) // 32 * 32
86:        if padded_num_experts >= 1024:
87:            return False, (
88:                "kernel's moe_align_block_size requires fewer than 1024 padded experts"
89:            )
```

512 experts → `padded_num_experts = 512` → `512 < 1024` → **passes**. **[S]**
Nothing in the selection path keys on top-k, so top-10 is not a constraint here.
So 512/top-10 is *not* what kills the dispatch — the fp8 capability gate is.

**Divergence between the two heads (block-shape refine):**

- **54129**: refine runs **after** the oracle, `quantization/fp8.py:506-546`, and on
  success does `self.fp8_backend = Fp8MoeBackend.TRITON` +
  `self.experts_cls = backend_to_kernel_cls(Fp8MoeBackend.TRITON)[0]`
  (`:530-531`) — i.e. it **force-assigns TritonExperts bypassing
  `is_supported_config`**. But it is **unreachable on gfx1151**, because
  `select_fp8_moe_backend` at `:500` already raised. **[S]**
- **53896**: refine runs **before** the oracle (`quantization/fp8.py:489-518`) and
  encodes the refined shape in the `weight_key`
  (`create_fp8_quant_key(static=True, group_shape=GroupShape(*self.moe_block_shape))`,
  `:516-518`). The comment states this exists so *"the oracle only selects kernels
  that support it"* **[CL]**. Net effect on gfx1151 is the same raise, and if a
  refine did occur the refined key would additionally fail the exact-tuple match in
  §1f. **[S]**

Worth flagging for the record: **54129's force-assign at
`quantization/fp8.py:530-531` is the one construct in either tree that installs a
kernel class without consulting `is_supported_config`.** On gfx1151 it is dead code
behind the raise, but it is the shape of thing that would produce a silent wrong
dispatch if the raise were ever removed or the ordering changed.

### 1f. THE decisive gate

`TritonExperts._supports_quant_scheme`, `experts/triton_moe.py:148-156` (54129) **[S]**:

```
148:        if current_platform.supports_fp8():
149:            supported += [
150:                (kFp8Static128BlockSym, kFp8Dynamic128Sym),
…
156:        return (weight_key, activation_key) in supported
```

Our pair is exactly `(kFp8Static128BlockSym, kFp8Dynamic128Sym)` (§1b), so
everything hinges on `current_platform.supports_fp8()`.

`platforms/rocm.py:971-972` (54129) and `platforms/rocm.py:972-973` (53896) —
identical bodies **[S]**:

```
971:    def supports_fp8(cls) -> bool:
972:        return on_cdna() or on_rdna4()
```

This is the campaign record's "rocm.py:870 returns False for gfx1151", **re-pinned to
rocm.py:971-972 (54129) / 972-973 (53896)**. The "some capability check" is now
resolved concretely:

- `_ON_CDNA = any(arch in _GCN_ARCH for arch in ["gfx9", "gfx1250"])` —
  `platforms/rocm.py:229` **[S]**. `"gfx9" not in "gfx1151"` and
  `"gfx1250" not in "gfx1151"` → **False**.
- `_ON_RDNA4 = any(arch in _GCN_ARCH for arch in ["gfx1200", "gfx1201"])` —
  `platforms/rocm.py:232` **[S]** → **False**.
- (For completeness, gfx1151 *is* recognised by the tree: `_ON_GFX1151` at
  `platforms/rocm.py:220`, and the Strix Halo PCI id `"0x1586": "AMD_Radeon_8060S",
  # gfx1151, Strix Halo` at `platforms/rocm.py:78` **[S]** — so this is a deliberate
  capability exclusion, not an unrecognised device.)

⇒ `supports_fp8() == False` on gfx1151 ⇒ the fp8 tuples are never appended ⇒
`(kFp8Static128BlockSym, kFp8Dynamic128Sym) not in supported` ⇒ TritonExperts
**rejects**. Confirmed identical in both checkouts. **[S]**

Note this is a *capability* statement (no MFMA/WMMA fp8 matrix path on RDNA3.5), not
a statement about whether Triton could emulate it — which is precisely why nothing
silently falls back.

### 1g. The BF16-emulation backend exists but is unreachable for e4m3 block-FP8

`Fp8MoeBackend.EMULATION` is defined at `oracle/fp8.py:57-60` (54129) with the
comment **[CL]**:

```
57:    # Dequantize-to-BF16 emulation for MXFP8 on devices without a native
58:    # MXFP8 MoE kernel (e.g. ROCm). Weights pass through unchanged here.
60:    EMULATION = "EMULATION"
```

This is the closest thing in stock to the feared twin. It is **not reachable** for
this checkpoint:

- It is **absent from `_AVAILABLE_BACKENDS`** (`oracle/fp8.py:80-95`) **[S]** —
  the fp8 oracle never considers it.
- It appears only in `oracle/mxfp8.py:30` `_SUPPORTED_BACKENDS` **[S]**, and
  `_mxfp8_backend_to_kernel_cls` resolves it to `Mxfp8EmulationTritonExperts`
  (`oracle/mxfp8.py:71-76`) **[S]**.
- `oracle/mxfp8.py:92-98` calls `is_supported_config` with hard-coded
  `kMxfp8Static` / `kMxfp8Dynamic` **[S]**. Our checkpoint is e4m3 + fp32
  `[128,128]` block scales (`kFp8Static128BlockSym`), **not** MXFP8 (e8m0 group-32).
  Different oracle, never entered.
- The user-facing escape hatch is closed too: `moe_backend` accepts `"emulation"`
  (`config/kernel.py:142`) **[S]**, but `map_fp8_backend`'s mapping dict
  (`oracle/fp8.py:250-260`) has **no `"emulation"` key** **[S]**, so
  `-O.moe_backend=emulation` raises
  `moe_backend='emulation' is not supported for FP8 MoE.` (`oracle/fp8.py:262-265`)
  **[S]**.

**⇒ There is no user-reachable dequant-to-BF16 MoE path for e4m3 block-FP8 at these
heads.**

---

## 2. Does the landed path materialize BF16 weights? — N/A, and nowhere else either

No path lands (§3), so there is nothing to materialize. Confirming the negative
directly:

**Sweep [M]** — `grep -rn "to(torch.bfloat16)|dequant|.to(orig_dtype)|to(layer.orig_dtype)"`
over `fused_moe/`, `quantization/fp8.py`, `quantization/utils/fp8_utils.py`,
excluding mxfp4/nvfp4/int4/wna16/mxfp8. Every surviving hit is unreachable here:

| Hit | Why it does not apply |
|---|---|
| `fused_batched_moe.py:642,692-702` (`dequant`, `w1_dq`, `w2_dq`) | `NaiveBatchedExperts`, unreachable from any oracle — `:581-585` **[S]** |
| `fused_moe/utils.py:301-306` `_fp8_quantize_dequantize` | **activation** QDQ, not weights **[S]** |
| `prepare_finalize/nixl_ep.py:35-39,182` `dequant_fp8` | activations in the NIXL EP all2all prepare path **[S]** |
| `ocp_mx_emulation_moe.py:9` "Weights are dequantized on the fly during each forward" | OCP-MX only **[CL]** |
| `fp8_utils.py:1431` `per_tensor_dequantize` | per-tensor, not block **[S]** |

**The campaign record's `fp8.py:466-468` re-pinned:** the `.to(torch.bfloat16)`
weight upconversion lives at `quantization/fp8.py:440-441` (54129) **[S]**, inside
`Fp8LinearMethod.apply` — and the record's two gates are both confirmed:

```
427:        if envs.VLLM_BATCH_INVARIANT:
428:            if self.block_quant:
…
431:                return self.fp8_linear.apply_weights(layer, x, bias)
432:            else:
…
439:                # per-tensor/channel: dequant to BF16 and run GEMM
440:                weight_fp8 = layer.weight.to(torch.bfloat16)
441:                weight_scale = layer.weight_scale.to(torch.bfloat16)
```

So: (a) gated behind `VLLM_BATCH_INVARIANT` (default `False`, `envs.py:92`,
`envs.py:622`) **[S]**; (b) explicitly in the **`else` (non-block-quant)** branch —
block-quant takes `apply_weights` with no dequant at `:429-431` **[S]**; and (c) it
is the **Linear** method, not the MoE method, so it never touches routed experts.
Campaign record **CONFIRMED and re-pinned**.

**The feared class, checked directly.** `TritonFp8BlockScaledMMKernel` **does exist
in stock** — `kernels/linear/scaled_mm/triton.py:159` **[S]** — but:

- It is a **linear** kernel (`class TritonFp8BlockScaledMMKernel(Fp8BlockScaledMMLinearKernel)`),
  not a `FusedMoEExperts`. It cannot serve the 512 routed experts.
- Its stock body is only `is_supported` + `apply_block_scaled_mm`, which calls
  `torch.ops.vllm.w8a8_triton_block_scaled_mm_func` — `triton.py:159-177` **[S]**.
  **It defines no `apply_weights` override and holds no weight cache.**
- **`grep -rn "cached_bf16|_bf16_cache|bf16_weight|TritonFp8BlockScaledMM"` over
  `vllm/` in BOTH checkouts returns 6 hits each, all of them the bare class name
  (1 definition + 5 references in `kernels/linear/__init__.py`). Zero hits for
  `cached_bf16`, `_bf16_cache`, or `bf16_weight`.** **[M]**

⇒ **The `cached_bf16_weight` hook does not exist in stock vLLM at either head.**

**Where it does exist (contrast, DS4 patch only):**

- `/home/tom/Downloads/ds4-vllm/container/patches/vllm-upstream.patch:1443-1454`
  adds `_DS4_W8A8_BF16` / `_DS4_W8A8_BF16_DIRECT` env latches and (`:1457+`) an
  `apply_weights` override onto `TritonFp8BlockScaledMMKernel` **[S]**.
- `…/container/rootfs/opt/venv/lib/python3.12/site-packages/ds4_tl_indexer.py:339-343`
  defines the module-level cache and the hook **[S]**:
  `_BF16_WCACHE = {}` (`:339`), `def cached_bf16_weight(weight, weight_scale, block_size, m)` (`:343`).
- Its only two call sites are `ds4_tl_indexer.py:383` (`w8a8_block_bf16_direct`) and
  `:395` (`w8a8_block_fp8_bf16`) — both described as *"gfx1151 fast path for
  block-scaled fp8 **linear**"* (`:392-394`) **[S]**. **Neither is a MoE path.**

⇒ Even under DS4, the twin covers **linear** weights, never the 512 experts. The
~125 GiB expert-twin scenario has no source basis in either tree.

---

## 3. Loud raise or silent fallback? — **LOUD**, and early

The oracle exhausts `AVAILABLE_BACKENDS` (loop at `oracle/fp8.py:394-411`, 54129 /
`:428` 53896) logging each rejection at `debug_once` only, then:

`oracle/fp8.py:413-416` (54129) / `:447-450` (53896) **[S]**:

```
413:    if current_platform.is_cuda() or current_platform.is_rocm():
414:        raise NotImplementedError(
415:            "No FP8 MoE backend supports the deployment configuration."
416:        )
```

ROCm is explicitly inside the raising branch. The silent
`return Fp8MoeBackend.NONE, None` at `oracle/fp8.py:418` (54129) **[S]** is reached
**only** on non-CUDA/non-ROCm platforms (the TODO above it names OOT/TPU plugins) —
**it is not the ROCm failure mode**. This is the good case and it directly answers
the campaign's worry about "the platform's dominant failure mode": here it does not
apply.

**Timing — this is the important part for B5.** `select_fp8_moe_backend` is called
from `Fp8MoEMethod.__init__` (`quantization/fp8.py:500-505`, 54129) **[S]**, which
runs during **layer construction**, i.e. while the model is being instantiated and
**before any expert weight tensor is read from the checkpoint**. So the failure is
not merely loud, it is *early*: you learn at startup, not after paging in ~121B FP8
params.

Secondary raise site, same message family — when a backend is explicitly requested,
`_return_or_raise` raises `ValueError(_make_log_unsupported(...))`,
`oracle/fp8.py:311-313` **[S]**, text from `:296-306`:
`"FP8 MoE backend {backend} does not support the deployment configuration since {reason}."`
The `reason` will be `"kernel does not support quantization scheme …"` formatted at
`modular_kernel.py:567-570` **[S]**.

Nothing in either path degrades silently to a bf16 emulation.

---

## 4. Does Qwen4Exp use vLLM's standard FusedMoE? — **YES**

No custom MoE module; §1–3 apply unchanged.

- `vllm/models/qwen4_exp/amd/model.py:44-47` imports
  `Qwen3NextAttention, Qwen3NextMLP, Qwen3NextSparseMoeBlock` from
  `vllm.model_executor.models.qwen3_next` **[S]**.
- `class Qwen4ExpSparseMoeBlock(Qwen3NextSparseMoeBlock)` —
  `qwen4_exp/amd/model.py:155-167` **[S]**. Its `__init__` only rejects
  sequence-parallel MoE, calls `super().__init__`, and sets `n_shared_experts`.
  **It overrides no expert construction and no forward.**
- Instantiated at `qwen4_exp/amd/model.py:241-243` when `is_moe_layer` **[S]**.
- The parent builds experts through the standard factory:
  `self.experts = FusedMoEFactory(` at
  `vllm/model_executor/models/qwen3_next.py:165`, imported from
  `vllm.model_executor.layers.fused_moe` at `qwen3_next.py:21` **[S]**.
- The AMD and NVIDIA trees agree here — both `model.py` files import the same
  `Qwen3NextSparseMoeBlock`. **[M]** (`diff -rq` lists `model.py` as differing, but
  the MoE class is the shared parent in both.)
- EPLB plumbing (`Qwen4ExpMixtureOfExperts`, `qwen4_exp/amd/model.py:327-374`)
  reaches into `layer.mlp.experts` **[S]**, i.e. the standard `FusedMoE` object —
  further confirming there is no bespoke expert container.

⇒ The Qwen4Exp routed experts go through `FusedMoE` → `Fp8MoEMethod` → the fp8
oracle. Redo of §1-3 not required.

---

## 5. The PLE / ngram FP8 embedding table (non-mmap path) — **the real finding**

**On the AMD tree there is no FP8 handling for the PLE table at all.**

`vllm/models/qwen4_exp/amd/ple_layer.py:195-200` **[S]**:

```
195:        self.ngram_embedding = VocabParallelEmbedding(
196:            padded_vocab_size,
197:            self.head_dim,
198:            padding_size=divisor,
199:            prefix=f"{prefix}.ngram_embedding",
200:        )
```

**No `quant_config=` and no `params_dtype=` argument.** In
`VocabParallelEmbedding.__init__` that means **[S]**:

```
286:        if quant_method is None and quant_config is not None:
287:            quant_method = quant_config.get_quant_method(self, prefix=prefix)
288:        if quant_method is None:
289:            quant_method = UnquantizedEmbeddingMethod()
```

(`vocab_parallel_embedding.py:286-289`; `quant_config` param declared at `:248`,
`params_dtype` at `:245`.) ⇒ `UnquantizedEmbeddingMethod`, weight allocated at the
model dtype = **bf16**. **[S]**

The loader then copies checkpoint shards through
`copy_ple_embedding_shard_`, `vllm/models/qwen4_exp/common/ple.py:71-75` **[S]**:

```
71:    source = loaded_weight.narrow(0, overlap.source_start, overlap.row_count)
72:    target = destination.narrow(0, overlap.destination_start, overlap.row_count)
73:    with torch.no_grad():
74:        target.copy_(source.to(device=target.device, dtype=target.dtype))
```

Called from `qwen4_exp/amd/ple_layer.py:383-389` with
`embedding.weight.data` as `destination` **[S]**.

Two consequences, both bad, both **[S]**:

1. **Byte doubling at load.** `target.dtype` is bf16, so an FP8 shard is upconverted
   1 byte → 2 bytes **at load time, eagerly, in `load_weights`**. This is a genuine
   "materialized twin" — the *only* one in the stock AMD path — but it applies to the
   PLE table, **not** the 121B expert weights.
2. **The block scale is silently dropped.** `.to(dtype=...)` is a raw numeric cast,
   not a dequant. **`grep -rn "scale" over `common/ple.py` and `amd/ple_layer.py`
   returns ZERO hits** **[M]** — there is no scale multiply anywhere in the AMD PLE
   path. An e4m3 tensor cast to bf16 yields values in ±448 with no scale applied.

**Contrast with the NVIDIA tree, which does it properly** —
`qwen4_exp/nvidia/ple_layer.py` **[S]**:
- imports `Fp8Config` (`:26`) and `create_fp8_scale_parameter`,
  `create_fp8_weight_parameter`, `is_fp8` (`:27-30`);
- `"""FP8 PLE embedding with one global checkpoint scale."""` (`:143`) **[CL]**;
- allocates the table **in FP8** (`create_fp8_weight_parameter`, `:157`) and
  registers a `weight_scale` parameter (`:162-170`);
- `"""Select global-scale FP8 only for quantized PLE checkpoint shards."""` (`:188`),
  guarded by `if not quant_config.is_checkpoint_fp8_serialized:` (`:192`);
- **gather-time** dequant: `if not is_fp8(embeddings): …` (`:643`) with
  `_get_embedding_weight_scale()` (`:632-634`).

⇒ **Answer to Q5: it depends which tree you run.** NVIDIA keeps the table FP8 with
gather-time dequant (no doubling). **AMD has no FP8 support: it would dequantize —
in fact bare-cast — to bf16 at load, doubling the bytes and discarding the scale.**

**Does AMD actually reach that, or die first?** The checkpoint's scale tensor is
named `ngram_embedding.weight_scale` (per the NVIDIA loader's explicit check,
`nvidia/ple_layer.py:478-480` **[S]**). Tracing that name through the **AMD** loader
(`amd/ple_layer.py:338-388`) **[S]**: it is not a `hashstats_`/`token_lookup` skip
(`:340-342`), not in `persistent_buffers` (`:343`), and does **not** match
`name.startswith("ngram_embedding.shard_") and name.endswith(".weight")` (`:352`)
— so it falls to `regular_weights` (`:388`) and into
`AutoWeightsLoader(self).load_weights(...)` (`:391`), which for an unmatched name
raises `ValueError(f"There is no module or parameter named {prefix!r} …")` —
`vllm/model_executor/models/utils.py:412-417` **[S]**.

⇒ Most likely the AMD PLE path **also fails loudly at load** on the orphan
`weight_scale`. I did not execute this, so: **the disposition of the AMD PLE FP8
table is UNDETERMINED between "loud ValueError" and "silent bare-cast to bf16"** —
it turns on the exact tensor names in the real checkpoint, which is not present on
this box. What would settle it: `ls`/safetensors-index inspection of
`Qwen/Qwen3.8-Flash-Next-FP8` for keys matching `*ngram_embedding*`. Either way it is
**not** a silent 125 GiB expert twin.

(The mmap path, `qwen4_exp/nvidia/ple_mmap.py`, exists **only in 54129** and **only
under `nvidia/`** — `diff -rq` **[M]**. It is fully FP8-aware:
`_FP8_DTYPES = {"F8_E4M3": torch.float8_e4m3fn, …}` at `ple_mmap.py:78-80` with
`_SCALE_TORCH_DTYPES` at `:85-87` **[S]**. Out of scope for the AMD run.)

---

## 6. Envs and flags that change the dispatch

All defaults read from `vllm/envs.py` this session **[S]**.

| Env | Default | Line(s) | Effect on this model @ gfx1151 |
|---|---|---|---|
| `VLLM_ROCM_USE_AITER` | `False` | `envs.py:134`, `:1238-1239` | Latches `_AITER_ENABLED`. **If explicitly set**, `envs.is_set(...)` at `oracle/fp8.py:374` diverts to the AITER branch (`:375-387`) — either removing AITER or hard `_return_or_raise`ing on it. Leave unset. |
| `VLLM_ROCM_USE_AITER_MOE` | **`True`** | `envs.py:138`, `:1258-1259` | Same `envs.is_set` trip-wire. Note the default is True but `is_set` tests *explicit* presence — exporting `=1` is not a no-op, it changes control flow. |
| `VLLM_USE_DEEP_GEMM` | **`True`** | `envs.py:196`, `:1549` | **Footgun.** `envs.is_set("VLLM_USE_DEEP_GEMM")` at `oracle/fp8.py:358`; if explicitly set truthy, `oracle/fp8.py:363-370` **returns `_return_or_raise(DEEPGEMM…)` immediately**, converting the graceful enumeration into a hard `ValueError` on DeepGEMM specifically. Also feeds `is_deep_gemm_supported()` (`utils/deep_gemm.py:110`). |
| `VLLM_MOE_USE_DEEP_GEMM` | **`True`** | `envs.py:197`, `:1551-1552` | Same trip-wire, same branch. |
| `VLLM_BATCH_INVARIANT` | `False` | `envs.py:92`, `:622` | Gates the linear bf16 dequant at `quantization/fp8.py:427-441`, and adds a kernel filter at `modular_kernel.py:592-593`. **Do not enable** — it is the one stock switch that turns on a `.to(torch.bfloat16)` weight upconversion (linear, non-block only). |
| `-O.moe_backend` (`MoEBackend`) | `"auto"` | `config/kernel.py:122-142`, `:235` | Non-`auto` skips enumeration and goes straight to `_return_or_raise` (`oracle/fp8.py:330-356`). `"emulation"` is accepted by the Literal (`:142`) but rejected by `map_fp8_backend` (`oracle/fp8.py:250-265`). |
| `VLLM_ROCM_USE_AITER_MOE_SITUV2_A8W4` | `False` | `envs.py:140`, `:1265-1266` | Not applicable (a8w4). |
| `VLLM_USE_TRITON_AWQ` | `False` | `envs.py:122`, `:1172` | Not applicable (AWQ). |

Also relevant, read but not an env: `rocm_aiter_ops.is_rdna_aiter_enabled()`
(`_aiter_ops.py:1867-1877`) returns `on_rdna4() and _AITER_ENABLED` **[S]** — it is
**gfx12-only by construction** (docstring: *"AITER on RDNA4 (gfx12) … The gfx12
analog of `is_enabled()`"* **[CL]**), so it is **False on gfx1151** and provides no
route in.

**Bottom line on envs: no combination flips the block-FP8 MoE onto a BF16 twin.**
The reachable outcomes are the `NotImplementedError` (auto) or a more specific
`ValueError` (forced backend).

---

## 7. B5 gate: what to observe tomorrow

### 7a. The gate will almost certainly not get to a memory measurement

Stock at these heads **cannot start this model on gfx1151**. Expect, during model
construction and **before any expert weights load**:

```
NotImplementedError: No FP8 MoE backend supports the deployment configuration.
```

from `oracle/fp8.py:414-416` (54129) / `:448-450` (53896), raised via
`quantization/fp8.py:500` (54129) / `:529` (53896). **[S]**

Treat "the server started at all" as the *first* signal. If it starts, something is
patched relative to these heads and the whole analysis must be re-run against what
actually ran.

**Recommended pre-flight (cheap, and it converts a hardware day into a source
answer):** run with `VLLM_LOGGING_LEVEL=DEBUG` and capture the `debug_once` lines
from `oracle/fp8.py:410` — `_make_log_unsupported` prints, per backend, exactly
which predicate failed (`oracle/fp8.py:296-306` **[S]**). That log *is* the
elimination table in §1d, produced by the machine rather than by me.

### 7b. If (and only if) it does start: the twin-vs-good discriminator

The distinguishing observable is **not** load-time RSS. Precisely:

- **The good case** (a native block-FP8 kernel consuming FP8 in place): expert
  bytes ≈ FP8 footprint, and that footprint is **flat from end-of-load through
  warmed decode**. Cold and warmed agree.
- **The twin case**: a **step increase after the first decode steps**, of order the
  expert-weight FP8 size again (~+125 GiB/node at 121B FP8 params — which on a TP=2
  node is fatal, hence the gate). The step appears **between cold and warmed**, not
  at load.

**Which source lines make load-time RSS lie.** In *stock* at these heads: **none** —
there is no lazy or cached weight materialization in the reachable fp8 MoE path
(§2), so a stock run's post-load footprint would be honest. The lying construct is
**DS4-patch-only**, and its docstring says so explicitly —
`ds4_tl_indexer.py:344-352` **[S]**:

> *"None means 'do not take the bf16 path'. That happens when the cache is cold and M
> is large: the startup memory-profiling forward runs at large M with an empty cache,
> and building the copy there inflates the measured footprint and shrinks the KV cache
> allocation. Decode (M <= 32) populates the cache; prefill reuses entries that
> already exist unless DS4_W8A8_BF16_PREFILL=0."*

So the cache is **deliberately engineered to be empty during startup memory
profiling** and to populate only at decode `M <= 32` (`ds4_tl_indexer.py:353-358`
**[S]**, keyed by `weight.data_ptr()` at `:353`). Under DS4, **load-time RSS is
guaranteed not to show the twin.**

Operationally, therefore:

1. Measure **after ≥1 real decode step at small batch (M ≤ 32)**, not after load and
   not after prefill-only. A prefill-only warmup can miss it entirely when
   `DS4_W8A8_BF16_PREFILL=0`.
2. Take **cold (post-load, pre-decode) and warmed (post-decode) readings and diff
   them.** A single absolute number cannot distinguish the cases.
3. **Read GTT/VRAM, not just process RSS.** The twin is built with
   `rocm_unquantized_gemm_impl` operands on device (`ds4_tl_indexer.py:386-387`
   **[S]**); a device-side allocation need not move process RSS proportionally.
   Use `amd-smi`/`rocm-smi` VRAM+GTT alongside RSS.
4. If DS4 is in play, `DS4_W8A8_BF16` / `DS4_W8A8_BF16_DIRECT` (latched at import,
   `vllm-upstream.patch:1444-1452` **[S]**) are the on/off switch — and
   `_DS4_DIRECT_HIT = [0, 0]  # [taken, fell-back]` (`:1454`) **[S]** is an existing
   in-process counter that answers "did the bf16 path actually run" directly,
   without inferring it from memory at all. Prefer that counter over any RSS
   inference.

### 7c. Scope correction for the gate

Even in the worst DS4 case, the twin covers **linear** weights (§2), not the 512
routed experts — so the "~125 GiB more per node" figure, which was computed from the
~121B FP8 *expert* params, **does not describe any code path found in either tree**.
The gate should be restated against linear-weight footprint, or against the PLE
table (§5), whichever DS4 actually enables.

---

## Surprises vs. the campaign record

1. **The record's three anchors all hold, but all three line numbers drifted.**
   fp8.py:466-468 → **`quantization/fp8.py:427-441`** (54129); rocm.py:870 →
   **`platforms/rocm.py:971-972`** (54129) / **`:972-973`** (53896);
   oracle/fp8.py:416-419 → **`oracle/fp8.py:413-416`** (54129) / **`:447-450`** (53896).
2. **The "some capability check" at rocm.py is now concrete:** `on_cdna() or
   on_rdna4()`, with gfx1151 excluded from both by string match
   (`platforms/rocm.py:229,232`) — while gfx1151 *is* otherwise a first-class
   recognised device in the same file (`:78`, `:220`). Deliberate exclusion, not an
   oversight.
3. **The feared class name is real but is a *linear* kernel** —
   `TritonFp8BlockScaledMMKernel` at `kernels/linear/scaled_mm/triton.py:159`, with
   **no `apply_weights` and no cache in stock**. The `cached_bf16_weight` hook has
   **zero occurrences in either checkout** **[M]**; it is entirely DS4-patch-local
   and, even there, linear-only. The expert-twin scenario is not supported by source.
4. **A dequant-to-BF16 MoE backend does exist in stock** (`Fp8MoeBackend.EMULATION`,
   `oracle/fp8.py:57-60`) — closer to the fear than the record anticipated — but it
   is MXFP8-only, absent from the fp8 `_AVAILABLE_BACKENDS`, and unreachable even via
   `-O.moe_backend=emulation`.
5. **NEW / not in the record: the two PR heads implement TP block-scale refine in
   different places, with different safety.** 53896 refines *before* the oracle and
   encodes it in the QuantKey (`oracle/fp8.py:272`, `quantization/fp8.py:516-518`).
   54129 refines *after* and **force-assigns `experts_cls` without calling
   `is_supported_config`** (`quantization/fp8.py:530-531`). On gfx1151 it is dead
   code behind the raise, but it is a latent silent-mis-dispatch shape and is the
   one place either tree installs a kernel unchecked.
6. **NEW / most actionable: the AMD PLE ngram embedding has no FP8 support
   whatsoever**, while the NVIDIA one has a purpose-built FP8 embedding method with
   a global scale and gather-time dequant. On AMD the table is a plain bf16
   `VocabParallelEmbedding` (`amd/ple_layer.py:195-200`) and the loader bare-casts
   with `.to(dtype=target.dtype)` and **no scale multiply anywhere**
   (`common/ple.py:74`; zero `scale` hits in the AMD PLE files **[M]**). This is a
   real correctness-and-bytes gap in the AMD tree independent of the MoE question.
7. **`VLLM_USE_DEEP_GEMM` / `VLLM_MOE_USE_DEEP_GEMM` default to `True` but are read
   via `envs.is_set()`** — explicitly exporting the value they already have is *not*
   a no-op; it diverts the oracle into a hard single-backend raise
   (`oracle/fp8.py:358-370`). Same trip-wire on the AITER pair.

## Explicitly UNDETERMINED

- **Whether the AMD PLE path raises on the orphan `weight_scale` or silently
  bare-casts.** Looked at: `amd/ple_layer.py:338-391`, `common/ple.py:44-75`,
  `models/utils.py:412-417`, `nvidia/ple_layer.py:478-480`. Settled by: the actual
  tensor-name list in `Qwen/Qwen3.8-Flash-Next-FP8` (not present on this box).
- **Byte size of the PLE table**, hence the cost of its bf16 doubling. The ngram
  geometry is config-driven (`ngram_vocab_size_base`, `heads_per_ngram`,
  `ple_embed_dim`, `make_ngram_vocab_size_divisible_by` — read at
  `amd/ple_layer.py:140-195`) and `grep` over
  `vllm/transformers_utils/configs/qwen4_exp.py` returned **no** hits for those
  field names **[M]**, so the defaults are not in-tree. Settled by the checkpoint's
  `config.json`.
- **Runtime confirmation of `_GCN_ARCH`.** The gfx1151 conclusion is derived from
  the string predicates at `platforms/rocm.py:217-232` and the documented device id
  at `:78`; I did not execute on the device. Settled by
  `python -c "import vllm.platforms.rocm as r; print(r._GCN_ARCH, r.on_cdna(), r.on_rdna4())"`
  on the box tomorrow — a one-liner worth running before anything else.
