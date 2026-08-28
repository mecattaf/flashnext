# Brief for the spec author — flashqwen as a **nix-native** package

*Written 2026-08-28 for the Fable session drafting the Tally-steered overnight spec.
Tom's ruling this session: **flashqwen is built for NixOS specifically and must be
packaged for nix.** This brief states what that ruling changes, what it costs, and
what must be pre-cleared before an 8-hour window opens.*

Evidence grades: **[S]** read from source · **[M]** measured this session (command +
output) · **[CL]** claimed by docs, unverified.

---

## 0. The one-paragraph version

The ratified plan in `innovation-ledger.md` §1 serves flashqwen from an **OCI
container** (podman + kyuz0's `vllm-toolboxes` recipe, Ubuntu 24.04, AMD *stable*
wheels rocm7.14.0/torch2.11.0), with NixOS owning only the host systemd units.
Tom's ruling replaces that leg: the engine itself becomes a **nix derivation**,
built as `nix-strix-halo`'s `vllm-rocm` with `src` repointed at our fork and our
`.patch` files injected. This is *mechanically supported* — the injection points are
verified — and it is **not a substrate downgrade**: the nix route runs ROCm
`7.15.0a20260719` (AMD nightly) against the same torch 2.11.0, on cp313. The
substrate is also **already fully realized in this machine's nix store**, so the
feared "expensive parts" are largely pre-paid. The real cost of the night is a
single repeated item — recompiling vLLM's few-hundred HIP kernels every time the
fork src or patch list changes — and the real risk is one hard-coded `--replace-fail`
on `"torch == 2.11.0"` that our fork's pyproject will trip.

---

## 1. What is ALREADY PAID — measured on this box tonight [M]

This is the finding that should reshape the schedule. All four are realized outputs
(not just `.drv`s) in `/nix/store` right now:

| Store path | Size | What it is |
|---|---|---|
| `q5k9g6m1…-therock-rocm-sdk-gfx1151-7.15.0a20260719` | **8.4 G** | the whole ROCm SDK |
| `9q2882vv…-python3.13-therock-python-wheels-gfx1151-7.15.0a20260719` | — | torch 2.11.0 / torchvision / torchaudio / triton 3.7.1, cp313 |
| `83aayvxk…-python3.13-therock-amdsmi-7.15.0a20260719` | — | amdsmi |
| `pdi2imlf…-python3.13-vllm-0.25.1` | **198 M** | **a fully compiled TheRock gfx1151 vLLM** |

The last row is the important one. `nix-store -q --references` on it returns
`therock-rocm-sdk-gfx1151`, `therock-python-wheels-gfx1151`, and `therock-amdsmi`
[M] — i.e. this is `vllm-rocm`, not nixpkgs' generic vllm. It contains built HIP
extensions (`vllm/_C.abi3.so`, `_C_stable_libtorch.abi3.so`, `cumem_allocator.abi3.so`,
`fs_io_C.abi3.so`, `spinloop.abi3.so`) [M].

**Consequence: the nix-native path starts from a warm substrate, not a cold one.**
Free disk on `/nix/store`: 262 G of 915 G [M]. Box: 32 threads, 125 GiB RAM [M].

### 1.1 Correcting a likely misreading of the #237 ruling

`modules/strix-ai.nix:55-64` in dotfiles removed `ds4-rocm`/`vllm-rocm` from
`environment.systemPackages` because each pulled `therock-rocm-sdk-gfx1151` — *"8.3
GiB NAR, narinfo-404 on both upstream caches, so locally unique"* — into every cold
nightly closure push. It is easy to read that as "the SDK is an expensive build."
**It is not a build at all.** `pkgs/therock/rocm-sdk/default.nix` is `fetchurl` +
`tar -xzf` + `cp -R`, with `dontConfigure = true; dontBuild = true; dontPatchELF =
true; dontStrip = true` [S]. The expense is **bytes and cache pressure**, not
CPU-hours. Do not budget hours for it.

And the pinned nightly URL is **still alive** — `HEAD
https://rocm.nightlies.amd.com/tarball-multi-arch/therock-dist-linux-gfx1151-7.15.0a20260719.tar.gz`
→ `HTTP/2 200`, `content-length: 1752337361` (1.75 GB), `last-modified: Sun, 19 Jul
2026` [M]. AMD has not expired it. Even a fully cold machine pays ~1.75 GB of
download, not a ROCm compile.

### 1.2 The one thing that IS expensive, and it repeats

`overlays/therock-vllm.nix:317-321` [S]:

> *"vllm-rocm compiles a few hundred HIP kernels through the TheRock toolchain — heavy
> enough to need a big-parallel builder."*
> `requiredSystemFeatures = (old.requiredSystemFeatures or [ ]) ++ [ "big-parallel" ];`

**Every change to the fork `src` or to the injected patch list invalidates
`python3.13-vllm-0.25.1` and pays that compile again.** Only vLLM is built from
source; torch/triton/rocm all arrive as prebuilt AMD wheels
(`overlays/therock-python.nix:53-61` [S]).

**Scheduling rule for the night:** start the *first* fork build at minute zero, in the
background, before the spec prose is finalized. Freeze the patch set before it
starts. Budget for **2–3 such rebuilds maximum** in 8 hours, and design the
iteration loop so patch experimentation happens in a devshell against the built store
path — not through `nix build` round-trips. The exact wall-clock is unmeasured; the
first rebuild tonight *is* the measurement, so log it.

---

## 2. Mechanism — how the fork actually gets in

Verified against `/home/tom/Downloads/nix-strix-halo` @ `f0f2048f` (2026-08-18).

`vllm-src` is a top-level input (`flake.nix:47-50`) threaded straight through at
`flake.nix:365-371` into `overlays/therock-vllm.nix`, which does **not** define vLLM —
it mutates *nixpkgs'* `python313Packages.vllm` (`therock-vllm.nix:242-251` [S]).
The unsuffixed alias resolves through the overlay fixpoint
(`overlays/pkgs.nix:332`: `vllm-rocm = final."vllm-rocm-therock-${suffix}";` [S]), so a
later overlay redefining the suffixed attr is picked up automatically.

**Use Mechanism A + Mechanism C together.**

**A — `follows` for the src** (moves the whole chain including `vllmPairBenchEnv`):
```nix
inputs.vllm-fork = { url = "github:mecattaf/vllm/flashnext"; flake = false; };
inputs.nix-strix-halo.url = "github:hellas-ai/nix-strix-halo";
inputs.nix-strix-halo.inputs.vllm-src.follows = "vllm-fork";
```

**C — `overridePythonAttrs` for patches and an honest version:**
```nix
(final: prev: {
  vllm-rocm = prev.vllm-rocm.overridePythonAttrs (old: {
    src      = inputs.vllm-fork;
    version  = "0.26.0.dev-flashnext";
    patches  = (old.patches or [ ]) ++ [ ./patches/… ];
    postPatch = (old.postPatch or "") + ''…'';
    env = (old.env or { }) // { VLLM_VERSION_OVERRIDE = "0.26.0.dev-flashnext"; };
  });
})
```

Three non-negotiable rules, each grounded [S]:
1. **`(old.patches or [ ]) ++ …`** — assignment discards inherited nixpkgs patches the build needs.
2. **`(old.postPatch or "") + …`** — `therock-vllm.nix:257` *assigns* postPatch and `:288-291` is load-bearing.
3. **Set `VLLM_VERSION_OVERRIDE` alongside `version`** — `therock-vllm.nix:296` is what the running server reports. `vllmVersion = "0.25.1"` is a *string literal* at `flake.nix:369`, decoupled from the src tag; `follows` alone leaves the server announcing 0.25.1.

**Not available [S]:** `mkVllmTherock` takes **16 booleans and nothing else**
(`therock-vllm.nix:138-156`) — no `extraPatches`, no `src`, no `version`, no
`postPatch`. `lib` exports `mkRocmOverlay`/`mkPythonOverlay`/`mkPkgsOverlay`/
`mkMtuneOverlay` but **no vLLM overlay builder** (`flake.nix:480-499`; repo-wide grep
for `therock-vllm` returns exactly one hit, the internal call site [M]). The vLLM
overlay is the one piece of the stack with no supported parameterisation point —
which is precisely why C is required alongside A.

*Fallback only if a different `target` is ever needed:* Mechanism B, re-importing
`"${nix-strix-halo}/overlays/therock-vllm.nix"` from the fetched input's store path
with our own args (signature at `:1-8`). Path-into-input is input use, not vendoring.

---

## 3. ⚠ Gate 0 — the torch pin. Check this FIRST, tonight, before anything else.

`therock-vllm.nix:269` runs `--replace-fail '"torch == 2.11.0"'` against
`pyproject.toml` [S]. Every `--replace-fail` in that postPatch (`:259, 263, 268, 271,
279, 288`) is a **hard abort on a missed match**.

`innovation-ledger.md` §1 records that the fork's pyproject says **`torch == 2.13.0`**.
That mismatch **will abort the build**, deterministically, and it cannot be fixed by
appending to postPatch (rule 2) because the failing `--replace-fail` lives inside the
*assigned* postPatch that runs first.

Two outcomes, and they are not equal:

- **If the fork's torch 2.13 pin is aspirational** (nothing in the PR heads actually
  calls a torch ≥2.12 API): fix it **in our fork** — carry `torch == 2.11.0` in the
  flashnext branch as one of the reviewable patches. Clean, ours, keeps rule 2 intact,
  no upstream expression is touched. **This is the recommended resolution.**
- **If the fork genuinely requires torch ≥ 2.12**: the nix-native path is **blocked at
  the substrate**. `pkgs/therock/sources/python-wheels.json` pins
  `torch-2.11.0+rocm7.15.0a20260719-cp313` [S]; AMD has published no gfx1151 torch
  2.13 wheels, and per `evidence/kyuz0-rocm10.md` no ROCm 10 gfx1151 vLLM substrate
  exists or can exist yet. There is no override that conjures a wheel. The container
  plan would have to come back.

**This is a ~20-minute source check that decides whether the night's plan is viable.
It must be Gate 0.** Everything downstream is wasted if it fails.

---

## 4. Constraints the spec must encode

**4.1 License — input-only, no exceptions.** `nix-strix-halo` has **no `LICENSE`,
`COPYING`, `COPYRIGHT`, or `NOTICE` file at any depth**, no README license statement,
no SPDX headers [M, two searches + full file inventory + grep]. No license = all
rights reserved. Declaring it as a flake input and consuming
`packages`/`overlays`/`nixosModules`/`lib` is fine; importing a path *inside* the
fetched input is fine. **Copying any `.nix`, `.patch`, `.sh`, or `.py` into the
flashqwen tree is not.** Neither is transcribing the shape of an expression from
memory. Every mechanism above is deliberately expressed as override / follows /
import-from-store.

**4.2 Do NOT `follows` nixpkgs into it.** `vllm-rocm` is built by mutating nixpkgs'
vllm, and depends on: a `.override` signature accepting `{rocmSupport, cudaSupport,
gpuTargets, rocmPackages, amdsmi}`; a patch file literally named
`0006-drop-rocm-extra-reqs.patch` in that package's `patches` (`:253-255`); and six
`--replace-fail` string literals [S]. Upstream's own `flake.lock` **has not moved
since 2026-07-21 while HEAD advanced to 2026-08-18** [M] — ~28 days of merged code has
never been evaluated against a fresh pin. Let it keep its own 2026-07-18 nixpkgs and
accept closure duplication. Buildability against a *current* nixpkgs is
**UNDETERMINED** — no builds were run to establish it.

**4.3 `nixosModules.tuning` is forbidden, and must be enforced by assertion.**
`modules/tuning.nix` (33 lines) appends `ttm.pages_limit=20971520` (**80 GiB**) to
`boot.kernelParams` with **no option and no `mkDefault`** (`:4-7` [S]) — it is the only
GTT setter in that repo [M]. Dotfiles deliberately hold **128 GiB**
(`modules/strix-ai.nix:81-82`; `/proc/cmdline` shows `ttm.pages_limit=33554432` [M]).
Duplicate kernel params **merge silently — the collision will not error.** The trap is
that `README.md:196` lists the module without caveat and
`examples/configuration.nix:21` imports it. Use `nixosModules.default`
(`flake.nix:514-521`), which is tuning-inert, and add an assertion, not a convention.

**4.4 No vLLM serving module exists upstream.** `grep -rn 'vllm' modules/` returns
**nothing** [M]. The coordinator/worker Ray pair exists only as SSH-driven shell in
`lib/bench/vllm-transport-matrix.sh:586-599`, synchronised with a `sleep 2` (`:590`)
[S]. **flashqwen writes its own systemd units.** It may freely reuse the
empirically-tuned NCCL/RCCL *values* (`pkgs/strix-halo-vllm-pair-bench/default.nix:223-239`;
`vllm-transport-matrix.sh:188-243`) — those are measurements, not copyrightable
expression.

**4.5 Ray + vLLM is a `symlinkJoin`, not a shared env.** `flake.nix:685-691` [S] joins
`pkgs.vllm-rocm` with `python313Packages.ray` by symlink-merging their `bin/` and
`site-packages/`. It works because each resolves deps through its own wrappers, but it
is a fragile seam the moment Ray and vLLM must agree on a shared transitive dep
version. A TP=2 pair service leans on this seam harder than a bench driver does.
Flag it; don't assume it.

**4.6 Features hard-refused.** `aiter` (`:127`) and `rixl` (`:132`) trip the assertion
at `:239-241`; `tritonSupport`, `tritonKernelsSupport`, `otelSupport` must all be true
(`:234-238`) [S]. `rixl` is KV-transfer/disaggregated serving only — irrelevant at
TP=2. 22 dependency names are stripped from vLLM's closure (`:92-114`) including
`datasets`, `outlines`, `peft`, `timm`, `xformers` — check none of our patches import
one.

---

## 5. Blockers in the *dotfiles* tree that will silently eat the night

**5.1 The dotfiles flake cannot be evaluated in place.** A unix socket present under
`home/dot_config/cliamp/` makes Nix refuse to copy the tree; **every** `nix eval` /
`nix build` / `nix flake check` against `/home/tom/mecattaf/dotfiles` fails before
reaching any expression [M, `evidence/dotfiles-observed.md` §7.6].

Two consequences the spec must absorb:
- **flashqwen must be its own flake/repo.** Any worklist step of the form
  `nix build /home/tom/mecattaf/dotfiles#…` dies instantly. This is already the
  ledger's intent (`mecattaf/flashnext` as the estate) — now it is also a hard
  requirement, not a preference.
- **`nix build .#ds4-rocm` — the escape hatch `modules/strix-ai.nix:61` advertises —
  does not work from that checkout.** Live contradiction in committed bytes. Worth a
  dotfiles issue; out of scope tonight.

**5.2 `nix flake check` is not an available gate there.** `localStore.packages`
doesn't exist: `flake.nix:585` and `:1638` reference a missing attribute, so
`nix build .#models.<id>` and the `local-model-routing` check both throw
(§7.5). Collateral from the 2026-08-21 "weights leave nix" rewrite. **It also
invalidates the recorded weights-download command** baked into
`lib/local-models.nix:774`. Weight staging for flashqwen must go through the NAS
`library-fetch` → `local-models-sync` path, never through `nix build .#models.*`.

**5.3 Deploy addressing.** deploy-rs still dials the worker at `10.99.0.2` over
Thunderbolt (`flake.nix:536`, guarded by asserts at `:1194`/`:1227`); #240 moved the
ssh nickname and route metrics, not the deploy target (§7.1). Any BELIEVE line about
deploy addressing must cite those and say `10.99.0.2`, not `10.99.9.2`.

---

## 6. Gate 1 — the actual engineering problem is cheap to test

From `evidence/moe-dispatch.md`:

> **On stock vLLM at both PR heads, a 512-expert block-FP8 MoE on gfx1151 dispatches to
> NO kernel class at all. The load aborts loudly in `Fp8MoEMethod.__init__` with
> `NotImplementedError: No FP8 MoE backend supports the deployment configuration.`
> before a single expert weight byte is read.**

`RocmPlatform.supports_fp8()` is `on_cdna() or on_rdna4()` → False for gfx1151. The
central deliverable is **admitting gfx1151 into the FP8 MoE oracle with a working
Triton path** — nobody has this, and it is also the highest-value upstream
contribution.

The scheduling gift here: **it fails loudly at layer construction, before any weight
load.** So the bring-up gate costs *seconds*, not a full model load, and needs no
weights staged. Put it immediately after the first successful build. Two corollaries
already established: the feared ~125 GiB BF16 expert twin is **not** a stock risk (no
reachable upconvert path exists), and the genuine unrecorded finding is elsewhere —
the **PLE ngram FP8 embedding table has no FP8 handling at all in the AMD tree**
(§5 of that dossier), which will either die on an orphan `weight_scale` or bare-cast
and silently discard the block scale.

---

## 7. Recommended shape of the night

0. **Gate 0 — torch pin** (§3). ~20 min, source-only, decides viability. Blocking.
1. **Minute zero: kick the first fork `vllm-rocm` build in the background** with the
   patch set frozen (§1.2). Log its wall-clock — it is the night's unit of currency.
2. Concurrently, all CPU-free work: flake skeleton, the pair-service NixOS module and
   systemd units (§4.4), firewall admissions, the patch MANIFEST + `verify-patches.sh`
   discipline, the packaging-invariants test suite, catalog row and weight staging
   (§5.2), the `tuning`-prohibition assertion (§4.3).
3. **Gate 1 — FP8 MoE oracle admission** the moment the build lands (§6). Seconds, no
   weights.
4. Only then: weights, load, TP=2.

**Hold the line on rebuild count.** Two or three HIP recompiles is the realistic
ceiling. If the loop starts wanting a fourth, that is the signal to stop editing
patches and start iterating in a devshell against the built store path.

---

## 8. Open items this brief does not settle

- Wall-clock of a `vllm-rocm` fork rebuild on 32 threads. **Unmeasured.** Tonight's
  first build is the measurement.
- Whether nixpkgs at the pinned 2026-07-18 rev still satisfies every `.override`
  signature and `--replace-fail` literal. `nix eval .#packages.x86_64-linux.vllm-rocm.drvPath`
  settles the evaluation half; only a build settles the rest.
- Whether the realized `pdi2imlf…-python3.13-vllm-0.25.1` was built from upstream's
  own lock or from a local variant — its provenance was not traced this session, only
  its closure. Cheap to confirm, and worth confirming before treating it as a warm
  baseline.
- Whether `src.tag` is consumed by nixpkgs' vllm expression at the pinned rev
  (§1.3 second-order gotcha). Practical risk assessed as cosmetic; `VLLM_VERSION_OVERRIDE`
  is the belt to that suspenders.
