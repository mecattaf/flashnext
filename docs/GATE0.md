# GATE 0 — nix substrate compatibility audit

**Verdict: GO for the nix packaging lane.**

Audited 2026-08-28 against:

- overlay — `/home/tom/Downloads/nix-strix-halo/overlays/therock-vllm.nix`
- nixpkgs vllm — `/nix/store/llgwlxshmy0ifvxh7f8wq53vk5x7vd13-source/pkgs/development/python-modules/vllm/`
  (`default.nix` declares `pname = "vllm"; version = "0.16.0"` at `:337-338`)
- fork tree — clone of `8e4e036a311604800334989485b4ee23925956da`
  at `.../scratchpad/work-compat`, plus this lane's two commits.
- torch — `/nix/store/9q2882vvzshyqa5vmpqvl22g15s1cq48-python3.13-therock-python-wheels-gfx1151-7.15.0a20260719`,
  `torch/version.py` → `__version__ = '2.11.0+rocm7.15.0a20260719'`.

Ordering that matters for reading this table: nixpkgs `patches`
(`default.nix:348-353`, minus `0006` which the overlay filters at
`therock-vllm.nix:253-255`) run in `patchPhase`; the overlay's **assigned**
`postPatch` (`therock-vllm.nix:257-292`) runs after. So a literal may be absent
from a clean checkout and still match, if a nixpkgs patch introduces it.

---

## 1. `--replace-fail` literal audit

Six `--replace-fail` calls, at `therock-vllm.nix:260, 264, 269, 272, 280, 289`.
Plus one `rm` at `:258` that is equally fatal if its target is gone.

| # | Overlay line | Target file | Literal searched | Fork tree | Status |
|---|---|---|---|---|---|
| 0 | `:258` (`rm`) | `vllm/third_party/pynvml.py` | *(file must exist)* | file present | **PRESENT** |
| 1 | `:259-262` | `tests/utils.py` | `from vllm.third_party.pynvml import` | `tests/utils.py:110` | **PRESENT** |
| 2 | `:263-266` | `vllm/utils/import_utils.py` | `import vllm.third_party.pynvml as pynvml` | `vllm/utils/import_utils.py:53` | **PRESENT** |
| 3 | `:268-269` | `pyproject.toml` | `"torch == 2.11.0"` | was `"torch == 2.13.0"` at `pyproject.toml:10` | **PATCHED** — commit `db5d1df` |
| 4 | `:271-274` | `CMakeLists.txt` | `set(PYTHON_SUPPORTED_VERSIONS` | `CMakeLists.txt:49` | **PRESENT** |
| 5 | `:279-282` | `setup.py` | `rust_extensions=rust_extensions,` | `setup.py:1511` | **PRESENT** |
| 6 | `:288-291` | `vllm/model_executor/models/registry.py` | `env={'PYTHONPATH': ':'.join(sys.path)},` | absent from a clean tree — **introduced by nixpkgs `0003-propagate-pythonpath.patch`**, which applies at `registry.py:1511` | **PRESENT (post-patch)** |

### Note on #6 — do not "fix" this fork-side

`0003-propagate-pythonpath.patch` rewrites

```
_SUBPROCESS_COMMAND, input=input_bytes, capture_output=True
```

into

```
_SUBPROCESS_COMMAND, input=input_bytes, capture_output=True, env={'PYTHONPATH': ':'.join(sys.path)},
```

and the overlay's sixth `--replace-fail` then rewrites *that* into
`env={**os.environ, ...}`. The literal is therefore supposed to be missing
from our source. Adding it to the fork would make `0003` fail to apply and
would also introduce single-quoted strings that `ruff format` rejects.
`tools/check_nix_substrate.py` guards the **pre-image** line instead.

### Note on #3 — the torch pin is not a downgrade of anything real

`requirements/build/rocm.txt:6` in this same tree already pins
`torch==2.11.0` (with `torchvision==0.26.0`, `torchaudio==2.11.0`). The
`2.13.0` in `pyproject.toml` mirrored `requirements/build/cuda.txt:8`, i.e. the
CUDA number. The ROCm build path at this commit was already designed around
torch 2.11.

Independent confirmation that upstream still supports 2.11 at this commit:
`vllm/env_override.py:525`, `:563`, `:728` carry explicit
`torch >= 2.11 and < 2.12` backports, and `vllm/config/compilation.py:934`,
`vllm/compilation/backends.py:600` branch on `is_torch_equal_or_newer("2.12.0.dev")`
rather than requiring it.

### torch >= 2.12 requirement scan — none found

Python surface scanned: `vllm/models/qwen4_exp/**` (34 files),
`vllm/v1/spec_decode/qwen4_exp.py`, `vllm/transformers_utils/configs/qwen4_exp.py`.
Complete set of `torch.*` symbols used:

`allclose arange bfloat16 bitwise_xor bool broadcast_to cat compile cummax
cumsum device diff div dtype empty empty_like equal float16 float32
float8_e4m3fn frombuffer from_numpy full full_like int32 int64 long
nn nn.functional nn.functional.linear nn.Module no_grad remainder rsqrt
searchsorted sigmoid split tensor Tensor uint8 vstack where zeros zeros_like`

plus `torch.ops._C.{cooperative_topk,persistent_topk}` and ten
`torch.ops.vllm.qwen4_exp_*` entries registered through vLLM's own
`vllm.utils.torch_utils.direct_register_custom_op`. The newest of these is
`torch.float8_e4m3fn` (torch 2.1). **Nothing here needs 2.12+.**

C++ surface: PR #54129 / #53896 touch only
`csrc/libtorch_stable/gdn/fused_gdn_decode_kernel.cu`,
`csrc/libtorch_stable/ops.h`, `csrc/libtorch_stable/torch_bindings.cpp`, and the
change is adding a `const std::string& output_gate_activation` parameter plus a
`bool SigmoidGate` template parameter to an existing CUDA-only kernel. No new
libtorch API. Belt and braces: all 54 distinct `torch::stable::*` /
`torch::headeronly::*` symbols used anywhere under `csrc/` resolve inside torch
2.11.0's shipped `torch/include/torch/csrc/stable/` and
`torch/include/torch/headeronly/` (`STD_TORCH_CHECK`,
`STABLE_TORCH_LIBRARY_FRAGMENT`, `torch::stable::Tensor`,
`torch::headeronly::ScalarType` all present).

**No genuine torch >= 2.12 requirement exists. Gate 0 is not blocked.**

---

## 2. nixpkgs patch-apply results

`patches` list is `default.nix:348-353`; the overlay drops
`0006-drop-rocm-extra-reqs.patch` at `therock-vllm.nix:253-255`, leaving three.

| Patch | `git apply --check` | `patch -p1` (what nixpkgs actually runs) | Action |
|---|---|---|---|
| `0002-setup.py-nix-support-respect-cmakeFlags.patch` | **OK** — hunk 1 at 22 (offset +2), hunk 2 at 265 (offset +105) | OK | none |
| `0003-propagate-pythonpath.patch` | **OK** — hunk 1 at 1511 (offset +390) | OK | none |
| `0005-drop-intel-reqs.patch` | **FAILS** — `requirements/cpu.txt:14`, `patch does not apply` | **OK with fuzz 2** at line 18 (offset +4) | none required; see below |

### `0005` detail — fails `git apply`, succeeds under nixpkgs

nixpkgs' `patchPhase` runs GNU `patch -p1`, whose default fuzz factor is 2; it
does not run `git apply`, and it does not pass `-F0`. The hunk is:

```
@@ -14,8 +14,5 @@
 # required for the image processor of phi3v, this must be updated alongside torch
 torchvision; platform_machine != "s390x"

-# Intel Extension for PyTorch, only for x86_64 CPUs
-intel-openmp==2024.2.1; platform_machine == "x86_64"
-
 # Use this to gather CPU info and optimize based on ARM Neoverse cores
 py-cpuinfo; platform_machine == "aarch64"
```

Two upstream edits broke the exact context in our tree:
`requirements/cpu.txt:16` now reads
`torchvision; platform_machine != "s390x"  and platform_machine != "riscv64"`,
and a new `torchcodec >= 0.14` block was inserted at `:18-19` between
`torchvision` and the intel-openmp block. Fuzz 2 discards exactly those
context lines and the three lines the hunk deletes still match verbatim at
`requirements/cpu.txt:21-23`.

**Verified, not assumed:** applying all three with `patch -p1` produced exactly
the intended result — the resulting `requirements/cpu.txt` diff removes the two
intel-openmp lines and its comment and nothing else. No `.rej`. The tree was
then reverted; none of this is committed (nix applies these, we must not).

**Where the fix belongs:** nowhere. Making it apply at fuzz 0 would require
reverting the riscv64 marker and deleting the torchcodec block — regressing
real upstream content for a file the ROCm build never reads (`setup.py`
`get_requirements()` selects `requirements/rocm.txt` when
`VLLM_TARGET_DEVICE=rocm`). No replacement patch needs to be authored, so no
nixpkgs MIT text is copied anywhere. The residual is that fuzz 2 is the maximum
GNU patch allows — one more upstream edit near those lines and it stops applying.
`tools/check_nix_substrate.py` asserts the deleted line still exists, which is
the early-warning signal.

---

## 3. Stripped-dependency audit

`therock-vllm.nix:92-114` removes 22 dependency names from the closure:
`amd-quark apache-tvm-ffi bitsandbytes conch-triton-kernels datasets
fastsafetensors mistral-common mistral_common mistralai opencv-python-headless
outlines peft pyarrow pytest-asyncio runai-model-streamer runai_model_streamer
tensorizer tilelang timm torchcodec xformers`.

Scanned `vllm/models/qwen4_exp/**`, `vllm/v1/spec_decode/qwen4_exp.py`,
`vllm/transformers_utils/configs/qwen4_exp.py` for `import`/`from` statements
naming any of them (import-module spellings: `quark`, `tvm_ffi`, `cv2`,
`conch_triton_kernels`, `pytest_asyncio`, `runai_model_streamer`,
`outlines_core`, …).

**Result: zero hits.** A case-insensitive scan for any *textual* mention of
those names across the same files also returns zero. **No 3am hard stop here.**

Import reachability is also clean: `vllm/models/__init__.py` is a bare SPDX
stub, and `vllm/models/qwen4_exp/__init__.py` gates the model classes behind a
module `__getattr__` that dispatches on `current_platform` — the registry
(`vllm/model_executor/models/registry.py:114-116, 579-581, 669`) only names the
module path, so `pythonImportsCheck = [ "vllm" ]` never touches this code.

---

## 4. `.override` signature check

`therock-vllm.nix:242-248` calls `py.vllm.override { rocmSupport; cudaSupport;
gpuTargets; rocmPackages; amdsmi; }`. The local nixpkgs vllm expression accepts
all five:

| Argument | `default.nix` line | Declaration |
|---|---|---|
| `amdsmi` | `:30` | `amdsmi,` |
| `cudaSupport` | `:101` | `cudaSupport ? torch.cudaSupport,` |
| `rocmSupport` | `:103` | `rocmSupport ? torch.rocmSupport,` |
| `rocmPackages` | `:104` | `rocmPackages ? { },` |
| `gpuTargets` | `:105` | `gpuTargets ? [ ],` |

All five are consumed (`:227-229`, `:237`, `:295-301`, `:320-322`, `:384-385`,
`:421-422`, `:512-514`, `:554-558`). **Signature is satisfied.**

---

## 5. Verdict

**GO.** With commit `db5d1df` in the branch, every `--replace-fail` literal
matches, all three surviving nixpkgs patches apply under the patch program
nixpkgs actually uses, no stripped dependency is imported by the fork-added
code, and the `.override` signature is intact. Nothing in the fork requires a
torch API newer than the 2.11.0 the gfx1151 substrate ships.

Re-run `python3 tools/check_nix_substrate.py` after every other engineer's
commits land, before the HIP rebuild is kicked. It is seconds; the rebuild is
hours.
