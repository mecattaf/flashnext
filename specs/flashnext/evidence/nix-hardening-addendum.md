# Nix-hardening addendum — measured on `coordinator`, 2026-08-28 evening

*Companion to `nix-packaging-brief.md`. Purpose: ensure that no failure tomorrow
morning is attributable to the NixOS choice. Everything here is **[M]** measured
on this box tonight unless marked otherwise. Two items require an operator
decision **before** the window opens; they are §1 and §2.*

---

## 1. ⚠ DECISION REQUIRED — the torch pin already contradicts itself in `README.md`

The README's stack table asserts both of these:

| row | value |
|---|---|
| torch | `torch 2.13.0+rocm7.14.0` — AMD's **stable** gfx1151 wheels, *"the fork's exact pin"* |
| Packaging | **NixOS-native first** — fork source + patches injected into the `nix-strix-halo` vLLM expression |

**These two rows are incompatible as written.** The nix lane's wheel set is
`torch-2.11.0+rocm7.15.0a20260719-cp313` (`pkgs/therock/sources/python-wheels.json` [S]),
and `overlays/therock-vllm.nix:269` runs `--replace-fail '"torch == 2.11.0"'`
against `pyproject.toml` [S]. A `--replace-fail` is a **hard abort on a missed
match**, and it sits inside the *assigned* `postPatch` (`:257`) — appending
cannot route around it (see brief §2, rule 2).

So a fork pinned at `torch == 2.13.0` **will abort the nix build deterministically**,
in the postPatch phase, before any compile begins. This is not a risk to monitor;
it is a scheduled failure.

**Three exits, in preference order:**

- **(A) Repin the fork to torch 2.11.0** and carry it as one of the reviewable
  patches. Clean, ours, rule 2 intact. **Viable only if nothing at the PR heads
  actually calls a torch ≥2.12 API.** That is a source check, ~20 minutes, and it
  is Gate 0 of the night.
- **(B) Feed our own wheel set to the overlay.** `pkgs/therock/default.nix` takes
  `therockPythonWheelSources` as a *function argument* [S], so a Mechanism-B
  re-import could in principle supply AMD's stable rocm7.14.0 / torch 2.13.0
  wheels instead of the nightly set. Unverified, and it also drags ROCm 7.14 in
  where 7.15 is what is realized. **Do not attempt this first, at night.**
- **(C) The container lane is the plan and nix-native is the fallback** — the
  inverse of what the README currently declares.

**Whichever exit is taken, take it in the first hour.** Discovering this at 04:00
is the exact failure mode this addendum exists to prevent.

---

## 2. ⚠ DECISION REQUIRED — RDMA re-scoping: what it actually costs here

The operator has reverted the "no RDMA" ruling to capture the ~3%. The README's
own transport row says *"skip the kernel modules"* — that phrase is load-bearing.
Measured state on `coordinator` tonight:

| Fact | Measurement |
|---|---|
| Running kernel | **stock `linux-7.1.4`** — `/run/booted-system/kernel` → `/nix/store/74mvc45d…-linux-7.1.4/bzImage`. **Not** `nix-strix-halo`'s `linux-thunderbolt`. |
| Thunderbolt ibverbs kernel module | **absent** — no `tbv`/ibverbs module under `/run/booted-system/kernel-modules/lib/modules/7.1.4/`; `modinfo tbv` → *Module tbv not found* |
| RDMA devices registered | **none** — `/sys/class/infiniband/` empty, `/dev/infiniband` does not exist |
| Userspace | **already built**: `ds695sdf…-thunderbolt-ibverbs-0.3.4`, `p22hnyj…-rdma-core-usb4-63.0` (+`-dev`) realized in store |
| `linux-thunderbolt` kernel | **not in the store at all** — no realized output, no `.drv` |
| TB rails | `thunderbolt0` and `thunderbolt1` both `UP … LOWER_UP` — the **TCP** plane is live and healthy right now |

So the userspace half is free and the kernel half is entirely unpaid. Enabling
RDMA tonight is not a config flag; it is:

1. build `linux-thunderbolt` + `linux-thunderbolt-modules` (a **full kernel
   build**, unpaid, both must also enter each host's closure);
2. deploy the new kernel to **both** boxes;
3. **reboot both boxes**;
4. load the provider, admit the RDMA/CM ports on `thunderbolt0` —
   `networking.firewall.trustedInterfaces` is `[ "enp191s0" ]` on both hosts and
   **`thunderbolt0` is trusted on neither** (`hosts/coordinator/eth-fleet.nix:80`,
   `hosts/worker/default.nix:321` [S]);
5. re-verify the rail, then re-run the transport A/B to actually observe the 3%.

**The specific way this can lose the night — read this one twice.** deploy-rs
dials the worker at **`10.99.0.2`, over Thunderbolt** (`flake.nix:536`, guarded
by asserts at `:1194` and `:1227` [S]), and it passes `-F /dev/null`
(`flake.nix:300-301`), so it does **not** consult `~/.ssh/config` and will **not**
fall back to the 5 GbE wire. If the worker reboots into a kernel whose TB rail
does not come up, **the deploy path to the worker is severed by the deploy
itself.** That is a wake-up-to-a-bricked-worker scenario, unattended, at 03:00.

**Recommendation — do not gate the night on RDMA.** Concretely:

- Keep **RCCL over TCP on both rails as the committed transport** for the run
  that must succeed by morning. It is live, measured, and costs nothing.
- Scope RDMA as a **separate, non-blocking track** that may only proceed once
  TP=2-over-TCP has produced a committed benchmark — i.e. after the primary
  objective is banked, never before it.
- Make the worker reachable over 5 GbE for deploys **before** any kernel switch
  (this is the dotfiles doctrinal split already recorded at
  `evidence/dotfiles-observed.md` §7.1). Until that is true, a kernel reboot on
  the worker is a one-way door.
- Reboot **the worker first, never both at once**, and hold the coordinator
  (which hosts the overnight session) on the known-good kernel until the worker
  has come back and been verified.

The 3% is real and worth having. It is not worth having *tonight, first, on both
boxes, unattended*.

---

## 3. Build throughput — a free 4× on the critical path

`nix config show` [M]: `cores = 8`, `max-jobs = 4`, on a **32-thread** box.
`system-features` does advertise `big-parallel`, so the build will run locally —
but the single expensive derivation of the night (`vllm-rocm`, *"a few hundred HIP
kernels"*, `overlays/therock-vllm.nix:317-321` [S]) would get **8 threads of 32**.

**Run it as `nix build --cores 32 --max-jobs 1`.** Do not change `/etc/nix/nix.conf`
for this — the global setting is correct for general use; override per-invocation.

---

## 4. Pre-audit the fork's imports against the 22 stripped dependencies

`overlays/therock-vllm.nix:92-114` [S] strips 22 names from vLLM's dependency
closure, including `datasets`, `outlines`, `peft`, `timm`, `xformers`, `pyarrow`,
`bitsandbytes`, `fastsafetensors`, `tensorizer`, `torchcodec`, `mistral-common`.

In a container the 03:00 answer is `pip install`. In nix the 03:00 answer is
*write a derivation*, which for an unattended run is a hard stop.

**Do this before the window opens:** grep the fork's changed files for imports of
those 22 names. It is minutes of work and it converts the most likely
mid-night stall into a known quantity.

Related hard-refusals that will trip the assert at `:239-241` if touched:
`aiter` (`:127`) and `rixl` (`:132`). `rixl` is KV-transfer/disaggregated-serving
only — irrelevant at TP=2, but note it is also the RDMA-adjacent feature, so an
RDMA track must not reach for it.

---

## 5. Mirror the ROCm tarball while it is still free

`therock-rocm-sdk-gfx1151` is `fetchurl` + `tar -xzf` + `cp -R`
(`dontConfigure`/`dontBuild`/`dontPatchELF`/`dontStrip` all true [S]) — a
**download, not a build**. Verified live tonight [M]:

```
HEAD https://rocm.nightlies.amd.com/tarball-multi-arch/therock-dist-linux-gfx1151-7.15.0a20260719.tar.gz
→ HTTP/2 200 · content-length: 1752337361 (1.75 GB) · last-modified: Sun, 19 Jul 2026
```

Nix pins the hash, which means the day AMD garbage-collects that nightly the
derivation becomes **unbuildable forever on any cold machine**, with no fallback
path encoded. `rocm.nightlies.amd.com` promises no retention. Mirror the 1.75 GB
to storage under our control and record the URL beside the pin.

---

## 6. Keep `nix build` out of the inner loop

The night's actual engineering is editing vLLM **Python** — admitting gfx1151 to
the FP8 MoE oracle, porting the PLE mmap path to `amd/`. Every edit to fork `src`
or to the injected patch list invalidates the derivation and repays the full HIP
compile (§3). That is nix's worst case sitting directly on the critical path.

**The spec must provide an escape hatch, and the worklist must use it:** a
devshell that puts the built store path on `PYTHONPATH` alongside a writable
overlay directory, so patch iteration happens *outside* nix and a patch is only
sealed into a derivation once it works. Budget **2–3 sealed rebuilds for the
whole night**. A fourth is the signal to stop editing patches and go back to the
devshell — not to keep rebuilding.

---

## 7. Substrate already paid — do not re-derive it

Realized outputs, GC-rooted at `~/.cache/flashnext-rocm/` [M]:

| Path | Size | Root |
|---|---|---|
| `q5k9g6m1…-therock-rocm-sdk-gfx1151-7.15.0a20260719` | 8.4 G | (via consumers) |
| `pdi2imlf…-python3.13-vllm-0.25.1` (TheRock gfx1151 build, HIP `_C.abi3.so` present) | 198 M | `~/.cache/flashnext-rocm/vllm-rocm` |
| `jdgfzfg9…-ds4-rocm-gfx1151-experimental-unstable-3490c2e` | — | `~/.cache/flashnext-rocm/ds4-rocm` |
| `9q2882vv…-therock-python-wheels-gfx1151-…` (torch 2.11.0 / triton 3.7.1, cp313) | — | (via consumers) |
| `83aayvxk…-therock-amdsmi-…` | — | (via consumers) |

Free on `/nix/store`: 262 G of 915 G [M]. The manual GC roots are already in
place — **do not run `nix-collect-garbage` tonight**, and do not let any cleanup
step in the worklist do so either.

**Caveat carried forward from the brief:** the provenance of
`pdi2imlf…-python3.13-vllm-0.25.1` was not traced — only its closure, which does
reference the gfx1151 SDK and wheels. Confirm what lock it came from before
treating it as a trustworthy warm baseline.

---

## 8. Two dotfiles facts that will bite a worklist step

- **The dotfiles flake cannot be evaluated in place.** A unix socket under
  `home/dot_config/cliamp/` makes Nix refuse to copy the tree; every `nix eval` /
  `nix build` / `nix flake check` against `/home/tom/mecattaf/dotfiles` fails
  before reaching any expression (`evidence/dotfiles-observed.md` §7.6 [M]).
  Any worklist step of the form `nix build /home/tom/mecattaf/dotfiles#…` dies
  instantly — including `nix build .#ds4-rocm`, which `modules/strix-ai.nix:61`
  advertises as the escape hatch.
- **`nix flake check` is not an available gate there** either:
  `localModelStore.packages` does not exist, so `flake.nix:585`/`:1638` throw
  (§7.5). This also invalidates the recorded weights-download command at
  `lib/local-models.nix:774` — weight staging must go NAS `library-fetch` →
  `local-models-sync`, never `nix build .#models.*`.
