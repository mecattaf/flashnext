# RDMA bring-up package -- what's verified, what's assumed, what's dropped

Scope: `host/rdma/fetch-and-build.sh`, `host/rdma/attended-bringup.md`,
`host/rdma/ab-protocol.md`. No build, insmod, or modprobe was run producing
this package; no file outside this scratchpad output tree was written.

## Exact pins this package is built against

| component | pin | kind | source |
|---|---|---|---|
| westeri/thunderbolt.git (core+net) | `503c5ae1e72aa9ed91925dafa3d82ee2e992747f` | commit SHA | `tbv/build-modules.sh:25` in the reference tree |
| hellas-ai/thunderbolt-ibverbs | `76ba39b630a70accb72f19388eefe48844b50eb8` | commit SHA | `tbv/build-modules.sh:27`, also `container/Dockerfile:63` in the reference (duplicated, not a shared source of truth there either) |
| the 10-file kernel patch series | (no separate pin -- lives inside the ibverbs clone at that SHA, `kernel-workflow/patches/`) | fetched by cloning the pin above | `tbv/build-modules.sh:29-40` |
| rdma-core | `v57.0` | **mutable tag, not a SHA** | `container/Dockerfile:61`, confirmed by the campaign's own manifest (`ds4-vllm-manifest.md` section 7.1) as the one unpinned-by-commit component in the whole recipe |

## Verified vs. assumed

**Verified against source material actually read in this session:**
- The exact pins above, the gate order (kernel match -> Secure Boot ->
  fetch -> patch-apply-with-verify -> build), and the "matched set or the box
  panics on cable connect" rule, all read directly from `AGENTS.md` 1.1-1.5
  and `tbv/README.md`.
- The topology this package targets: `coordinator`/`worker`, `thunderbolt0`
  = rail 0 = `10.99.0.1`/`10.99.0.2` (`/30`), `thunderbolt1` = rail 1,
  link-local, and `enp191s0` = the 5GbE control wire at `10.99.1.1`/`.2` --
  read from `final-qwen-report.md` section 2 and `spec.md`'s vocabulary section,
  which agree with each other.
- The single-rail rule and its exact failure mode (source-blind control
  handler cross-matching HELLOs when both peers sit at route `0x2` in each
  other's domains, poisoning HopID state) -- quoted from
  `final-qwen-report.md` section 7 item 3, and independently corroborated by the
  reference's own `tbv-second-cable-prep.sh` comment describing the same
  failure for its two-cable case.
- The PD-wedge recovery ladder and its 1800s rate limit on the drastic step
  -- read directly from `specs/flashnext/evidence/dotfiles-observed.md` section 6.2,
  which documents the live `tb-link-heal` unit already running on both nodes
  (2-minute cadence, `framework_tool --pd-reset 2` as the last-resort step,
  stamped and rate-limited).
- That the main campaign already excludes `tbv/` from its own lift, on GPL
  grounds, and treats "prepared overnight, brought up only attended" as the
  sanctioned shape for RDMA -- `ds4-vllm-manifest.md` section 7 and `spec.md`
  ruling P7 and vocabulary entry "RDMA package" independently describe the
  same design this package implements. That agreement is reassuring: this
  package isn't inventing a new posture, it's filling in a slot the estate's
  own spec already reserved.
- The C-state/MTU finding and that both fixes are still **transient, not yet
  permanent** (`dotfiles#238` open) -- read from `final-qwen-report.md` section 2
  and section 7 item 1. This is why `attended-bringup.md` step 0.3 makes
  re-confirming the hold after the coordinated reboot a hard precondition,
  not a suggestion: a reboot is required by this bring-up, and a reboot is
  exactly the thing that can silently drop a transient PM QoS setting.

**Assumed / could not verify in this environment (no network access to
clone the actual pinned repos and inspect them):**
- That the 10-file patch series still applies cleanly at `kernel-workflow/patches/`
  inside the ibverbs clone at the pinned SHA. `fetch-and-build.sh` gates this
  at build time (hard failure if any patch doesn't apply, plus a
  `callback_xd` presence check afterward) rather than assuming it silently.
- The exact path of "the provider patches from the upstream ibverbs repo"
  that `AGENTS.md` 1.3 references for a host-side rdma-core build. Three
  plausible directory names are tried in `build_rdma_core()`; if none
  match, the script says so plainly and skips that step rather than failing
  the whole run -- it's diagnostics-only, the serving container builds its
  own provider independently.
- Whether the westeri `503c5ae` pin's target kernel generation is anywhere
  near NixOS 7.1.4. There is no way to check the commit's era without cloning
  it. `fetch-and-build.sh`'s vermagic gate will catch a mismatch at build
  time, but a build-time catch is a worse failure mode than knowing in
  advance -- flagging this as the thing to check first if the build fails in
  a way that looks like an ABI mismatch rather than a missing-header mismatch.
- Whether rdma-core v57.0's ABI vs. the newer v62 matters for our non-container
  host build. The reference pins v57.0 only inside the *container* build
  (`container/Dockerfile:61`); nothing in the source material validates that
  pin for a bare-host build outside a container, which is exactly the shape
  `fetch-and-build.sh`'s `build_rdma_core()` attempts. Treated as optional and
  non-fatal for that reason -- if it fails or mismatches, host-side
  `ibv_devices` diagnostics are degraded, not the RDMA path itself, since the
  serving container never depends on this host-side copy.

## Open questions carried into REPORT rather than guessed at

1. **Worker Secure Boot state.** Genuinely unknown before this package runs
   there. `fetch-and-build.sh`'s `check_secure_boot()` resolves this live,
   every run, on whichever node it executes on -- it does not trust a cached
   "coordinator is known-disabled" fact for the other node, and treats an
   unreadable/missing efivars state as a hard failure rather than an assumed
   pass.
2. **Kernel-generation drift between the westeri pin and NixOS 7.1.4.**
   Noted above; no way to resolve without network access to the actual repo.
3. **rdma-core v57.0 vs v62 ABI for a non-container host build.** Noted
   above; treated as non-fatal and diagnostics-only in the script.
4. **The provider-patch path for the host-side rdma-core build.** Noted
   above; the script guesses and fails soft, not silently.

## What was deliberately dropped from the reference recipe, and why

- **The local RC-write zero-copy patch on top of thunderbolt-ibverbs**
  (`ibverbs-local.patch` in the wider reference tree, ~3,450 lines). That
  patch is the *reference repo's own* unpublished diff, not something at a
  public pin -- there is nothing to fetch, and this package's mandate is
  fetch-only, never-vendor. Consequence: the built `thunderbolt_ibverbs.ko`
  cannot take the `native_rc_split_zcopy=1` module parameter.
  `attended-bringup.md` section 4 loads the module without it, which the
  reference's own comment names as the supported fallback ("needs no
  cross-box coordination") rather than a degraded mode to apologize for.
- **The NHI interrupt-throttle helper module** (`nhi_throttle`, ~70 lines in
  the reference tree). Same reasoning: it's that repo's own from-scratch
  module, not sourced from either public pin, so nothing to fetch. Effect is
  a latency-floor difference only -- stock ~128us NHI IRQ moderation instead
  of a hand-tuned 8us -- not a correctness one. Left out of scope for this
  package; a future add could vendor an equivalent as this package's own
  originally-authored module if the throttle floor turns out to matter after
  the A/B in `ab-protocol.md`.
- **The two-cable / RX-zero-copy topology entirely**
  (`tbv-second-cable-prep.sh`, `99-tbv-zc-second-link.conf`, the reference's
  `cables: 2` config path). Not a licensing question this time -- this
  pair's own rule (`final-qwen-report.md` section 7 item 3, restated in `spec.md`
  F.1) forbids RDMA on both rails outright, which is precisely what the
  two-cable topology is for. There is no safe adaptation of that feature
  here; it is not present anywhere in this package, and `attended-bringup.md`
  section 6 explicitly checks that rail 1 was never touched.
- **`install-modules.sh`'s Fedora/mutable-install install path** (blacklist
  via `/etc/modprobe.d`, `grubby --update-kernel`, permanently-enabled
  systemd units). This pair is NixOS on both nodes -- unlike the reference
  tree, whose own comments imply a *mixed* pair (one immutable-style box,
  one explicitly-Fedora box2 with its own `build-scripts/box2-*.sh` path).
  `grubby` and a mutable `/etc/modprobe.d` aren't the right primitives here.
  `attended-bringup.md` section 2 describes the NixOS-native equivalent instead: a
  temporary, deploy-rs-pushed config delta (blacklist + a oneshot unit
  ordered before `bolt.service`), reverted in the rollback section -- never
  a permanent fleet default, matching both the task's "operator-attended"
  framing and `spec.md` gate 6.4's "no unattended install path."
- **Kernel-devel auto-discovery beyond the reference's two RPM-style paths.**
  `fetch-and-build.sh` tries two additional NixOS-shaped candidate paths, but
  does not attempt to synthesize a Nix derivation for
  `boot.kernelPackages.kernel.dev` -- that's a real gap (see open questions
  above), not something safe to paper over with a guess in a script that's
  supposed to refuse loudly on uncertainty.

## Gate compliance

- `bash -n` passes clean on `fetch-and-build.sh`.
- No `insmod`/`modprobe -a <local .ko>`/boot-config write happens anywhere in
  this package; every load step is explicit, attended, and documented as
  attended in `attended-bringup.md`.
- Nothing under `/home/tom/mecattaf/flashnext` was read for writing, or
  written to, at any point -- only read from `specs/flashnext/evidence/` and
  `specs/flashnext/spec.md` for context.
- All third-party code is fetched at the pinned SHAs above at build time;
  nothing third-party is vendored into this package.
