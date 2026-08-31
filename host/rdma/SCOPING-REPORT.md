# RDMA bring-up package -- what's verified, what's assumed, what's dropped

> **HISTORICAL RECORD, corrected 2026-08-31.** This documents a scoping pass as
> it was performed; the "I grepped X and it said Y" findings are left as they
> were read, because that is what a record is for. Three things it asserts have
> since changed, and are corrected inline below:
> the netdevs are now `rail0`/`rail2`, not `thunderbolt0`/`thunderbolt1`
> (dotfiles#266); rail 2 is no longer link-local, it carries `10.99.2.x/30`
> (dotfiles#274); and the scoped RoCEv2 firewall admission this report
> describes as something `attended-bringup.md` step 5 *would add* has since
> landed declaratively in `dotfiles modules/fn-rdma.nix:366` and is live on
> both twins — step 5 is now verify-only.

Scope: `host/rdma/fetch-and-build.sh`, `host/rdma/attended-bringup.md`,
`host/rdma/ab-protocol.md`. No build, insmod, or modprobe was run producing
this package; no file outside this scratchpad output tree was written.

**Operator ruling folded in (second pass on this package):** RDMA is fully
out of the overnight critical path. No unsupervised reboots, period. This
package is a ready-to-execute MORNING PLAN, not an execution, and per
`attended-bringup.md` Gate 0 it may not even begin until a committed
TP=2-over-TCP benchmark is banked under `results/`. The reboot order is now
sequential (worker first, coordinator follows only once the worker is
verified back over the 5GbE wire), never the reference's "both together."

## Exact pins this package is built against

| component | pin | kind | source |
|---|---|---|---|
| westeri/thunderbolt.git (core+net) | `503c5ae1e72aa9ed91925dafa3d82ee2e992747f` | commit SHA | `tbv/build-modules.sh:25` in the reference tree |
| hellas-ai/thunderbolt-ibverbs (KERNEL MODULE) | `76ba39b630a70accb72f19388eefe48844b50eb8` | commit SHA | `tbv/build-modules.sh:27`, also `container/Dockerfile:63` in the reference (duplicated, not a shared source of truth there either) |
| the 10-file kernel patch series | (no separate pin -- lives inside the ibverbs clone at that SHA, `kernel-workflow/patches/`) | fetched by cloning the pin above | `tbv/build-modules.sh:29-40` |
| rdma-core (reference's pin, no longer this package's default build target -- see below) | `v57.0` | mutable tag, not a SHA | `container/Dockerfile:61` |

## New substrate facts (this pass), and how each changed the package

1. **Both nodes run the stock nixpkgs kernel, not nix-strix-halo's
   `linux-thunderbolt`.** `/run/booted-system/kernel` is a plain kernel;
   `linux-thunderbolt` has no build output and no `.drv` in the store at all.
   Reported from the team lead's substrate session (`modinfo tbv` not found,
   `/sys/class/infiniband/` empty, no `/dev/infiniband`) -- I did not
   independently re-run those commands, since I have no shell on the actual
   coordinator/worker boxes from this environment. Consequence: this
   confirms route (a) (out-of-tree modules against the *running* stock
   kernel) is the well-defined, low-risk option, and route (b) (deploying
   `linux-thunderbolt`) means building a kernel derivation that has never
   been realized before, from scratch, on hardware that has never booted it.
   `fetch-and-build.sh`'s new `log_kernel_provenance()` logs which case it's
   in on every run rather than assuming.

2. **The userspace verbs provider is already realized in the store:**
   `thunderbolt-ibverbs-0.3.4` and `rdma-core-usb4-63.0` (a fork, not
   vanilla rdma-core). This supersedes the previous version of this
   package's `build_rdma_core()`, which cloned vanilla rdma-core at the
   reference's `v57.0` pin -- that pin is now known-stale against what this
   fleet already committed to. `fetch-and-build.sh` was reworked: it now
   checks the store first (`check_userspace()`), reports what it finds, and
   does **not** build a redundant/conflicting copy by default. The old
   clone-and-build path still exists as an opt-in fallback
   (`FLASHNEXT_BUILD_RDMA_CORE_FALLBACK=1`) in case a future environment
   lacks the nix-realized pair, but it is no longer on the default path.
   **Open question this creates, unresolved:** whether
   `thunderbolt-ibverbs-0.3.4` was built from the *same* commit
   (`76ba39b`) this package pins for the kernel module, or a different one.
   Package version numbers don't map to git SHAs; I have no way to check
   this without inspecting nix-strix-halo's own derivation, which I don't
   have access to from here. Flagged in the script's own log output and in
   `attended-bringup.md` step 2 item 4 and step 9, rather than assumed
   compatible.

3. **The tensor rail is not blanket-trusted on either host -- independently
   verified, not just taken on report.** I grepped the actual
   `hosts/coordinator/eth-fleet.nix:80` and `hosts/worker/default.nix:321`
   in `/home/tom/mecattaf/dotfiles`: both set
   `networking.firewall.trustedInterfaces = [ "enp191s0" ];`, confirming
   the rail got no firewall admission of any kind *at the time of this pass*.
   [CORRECTED 2026-08-31: `modules/fn-rdma.nix:366` now carries
   `networking.firewall.interfaces.rail0.allowedUDPPorts = [ 4791 ]`, deployed
   and verified live via `iptables-save` on both twins; step 5 is verify-only.]
   The admission is scoped (RoCEv2 only) rather than trusting the whole
   interface -- matching the
   per-interface scoping idiom already used elsewhere in that same repo
   (`wlp192s0`, `tailscale0` in `hosts/coordinator/*.nix`,
   `hosts/worker/immich-ml.nix`), and matching that repo's own explicit
   comment warning against "re-blanket-trusting an interface."

4. **The worker's deploy-rs path runs over Thunderbolt, and would be
   severed by exactly the kind of reboot this package requires --
   independently verified.** `dotfiles/flake.nix` around line 536:
   `hostname = if host == "worker" then "10.99.0.2" else host;` -- the
   worker is dialed at its rail-0 address, not the wire. `fleetDeploySshOpts`
   (around line 299) passes `"-F" "/dev/null"`, defeating any
   `~/.ssh/config` override. The asserts pinning `10.99.0.2` as the
   worker's deploy hostname sit in the same large assert block I read
   around lines 1190-1232 (the exact ones the team lead cited, `:1194` and
   `:1227`, are inside that block; I did not line-match each one
   individually but confirmed the assert `self.deploy.nodes.${strixWorker}.hostname
   == "10.99.0.2"` exists and reads exactly as described). This is now
   `attended-bringup.md` step 1 -- mandatory, first, before any kernel or
   module change, deploy-tested with rail 0 administratively down before
   being trusted.

## Verified vs. assumed

**Verified against source material actually read in this session (both the
original AGENTS.md/tbv reference tree and, this pass, the live
`dotfiles` repo directly):**
- The exact pins above, the gate order (kernel match -> Secure Boot ->
  fetch -> patch-apply-with-verify -> build), and the "matched set or the box
  panics on cable connect" rule -- from `AGENTS.md` 1.1-1.5 and `tbv/README.md`.
- The topology: `coordinator`/`worker`, `rail0` = rail 0 =
  `10.99.0.1`/`10.99.0.2` (`/30`), `rail2` = rail 2 = `10.99.2.1`/`10.99.2.2`
  (`/30` since dotfiles#274 — it was link-local when this pass ran), and
  `enp191s0` = the 5GbE control wire at `10.99.1.1`/`.2` -- from
  `final-qwen-report.md` section 2 and `spec.md`'s vocabulary section.
  Both rail names are cable-bound via `.link Name=` since dotfiles#266.
- The single-rail rule and its exact failure mode (source-blind control
  handler cross-matching HELLOs, poisoning HopID state) -- from
  `final-qwen-report.md` section 7 item 3 and the reference's own
  `tbv-second-cable-prep.sh` comment.
- The PD-wedge recovery ladder and its 1800s rate limit -- from
  `specs/flashnext/evidence/dotfiles-observed.md` section 6.2.
- That the main campaign already excludes `tbv/` from its own lift on GPL
  grounds, and treats "prepared overnight, brought up only attended" as the
  sanctioned shape for RDMA -- `ds4-vllm-manifest.md` section 7 and
  `spec.md` ruling P7 / vocabulary entry "RDMA package."
- The C-state/MTU finding and that both fixes are still transient, not yet
  permanent (`dotfiles#238` open) -- from `final-qwen-report.md` sections 2
  and 7.
- This pass, directly against `/home/tom/mecattaf/dotfiles`: the worker's
  Thunderbolt-only deploy path, the `-F /dev/null` ssh hardening, and both
  hosts' `trustedInterfaces` excluding the tensor rail -- all three read
  from the actual files, not taken solely on the team lead's word, and all
  three matched what was reported.

**Reported by the team lead's substrate session, not independently
re-verified (no shell access to the actual coordinator/worker boxes from
this environment):**
- The running kernel on both nodes is stock, and `linux-thunderbolt` has no
  store output.
- `thunderbolt-ibverbs-0.3.4` and `rdma-core-usb4-63.0` are realized in the
  store.
- `modinfo tbv` not found, `/sys/class/infiniband/` empty, no
  `/dev/infiniband` -- i.e., the kernel half of RDMA is genuinely unbuilt
  today.

**Still assumed / could not verify in this environment (no network access
to clone the actual pinned repos and inspect them):**
- That the 10-file patch series still applies cleanly inside the ibverbs
  clone at the pinned SHA. `fetch-and-build.sh` gates this at build time
  (hard failure on any patch not applying, plus a `callback_xd` presence
  check afterward).
- Whether the westeri `503c5ae` pin's target kernel generation is anywhere
  near stock NixOS 7.1.4. No way to check the commit's era without cloning
  it; the vermagic gate catches a mismatch at build time, which is a worse
  failure mode than knowing in advance, but is what's available here.
- The exact path of "the provider patches from the upstream ibverbs repo"
  that `AGENTS.md` 1.3 references -- now moot for the default path (userspace
  is already realized via nix, see above) but still relevant if the legacy
  fallback build is ever invoked; the script still guesses three plausible
  paths and fails soft if none match.

## Route (a) vs (b) -- recommendation

**Recommend route (a): out-of-tree matched modules against the running
stock kernel** (what `fetch-and-build.sh` builds today), not route (b)
(deploying nix-strix-halo's `linux-thunderbolt`, a full kernel swap).
Reasoning, in full, is in `attended-bringup.md` section 3; in short: route
(a) never changes the boot kernel (known-good fallback is trivially "the
temporary module-load config was never deployed"), touches only three
out-of-tree modules, and still needs the same one-reboot-per-node route (b)
needs anyway -- so route (b)'s only advantage (a "proper" kernel-level
integration) comes with a first-ever-realization kernel build of unknown
duration and a full-driver-stack risk to the GPU/ROCm side of the box, for
the same RDMA payoff. Route (b) is documented as a real option, not
dismissed, in case the fleet later decides to adopt RDMA as a standing
capability -- but that's a separate, larger, better-tested project than a
single attended morning session.

## Worker-first, sequential reboot -- why it's safe

The reference bring-up says "reboot both boxes together"; the operator
ruling overrides that to worker-first, never simultaneous, no unsupervised
reboot. Reconciling the two: the reference's matched-set warning
("stock net over the patched core has mismatched ABI and panics on cable
connect") is about mismatched core/net *within one host* -- the boot unit in
`attended-bringup.md` step 6 is all-or-nothing by construction, so that
danger doesn't arise regardless of reboot ordering. What the two hosts'
*Thunderbolt IP link* needs to keep working across a version-skew window
(worker on the new module set, coordinator still stock) is ordinary packet
connectivity, not a shared ring ABI -- so `ping`/ssh/`tb-link-heal` over
`rail0` during that window is expected to work. The one thing that
genuinely does need both hosts already matched is RDMA/ibverbs itself,
which is why `attended-bringup.md` step 8 explicitly gates the RoCE
bring-up on both nodes having already rebooted and verified -- it never
runs mid-sequence. Step 1 (the deploy-path fix) is what makes the
worker-first ordering actually recoverable if something does go wrong: it's
the one channel that survives a Thunderbolt failure on the worker.

## What was deliberately dropped from the reference recipe, and why

- **The local RC-write zero-copy patch on top of thunderbolt-ibverbs**
  (~3,450 lines in the wider reference tree). It's that repo's own
  unpublished diff, not at a public pin -- nothing to fetch, and this
  package's mandate is fetch-only, never-vendor. Consequence: the built
  `thunderbolt_ibverbs.ko` cannot take `native_rc_split_zcopy=1`.
  `attended-bringup.md` step 8 loads the module without it, which the
  reference's own comment names as the supported fallback.
- **The NHI interrupt-throttle helper module** (~70 lines in the reference
  tree). Same reasoning: a from-scratch module owned by that other tree,
  not sourced from either public pin. Latency-floor difference only (stock
  ~128us vs a hand-tuned 8us), not a correctness one.
- **The two-cable / RX-zero-copy topology entirely.** This pair's own rule
  forbids RDMA on both rails outright, which is precisely what that
  topology is for -- no safe adaptation exists, and none is present here.
- **`install-modules.sh`'s Fedora/mutable-install install path**
  (`/etc/modprobe.d`, `grubby`, permanently-enabled units). This pair is
  NixOS on both nodes; `attended-bringup.md` substitutes a temporary,
  deploy-rs-pushed config delta instead, reverted in rollback, never a
  permanent fleet default.
- **A vanilla rdma-core `v57.0` build, as this pass's default behavior.**
  Not dropped for licensing or scope reasons this time -- superseded, because
  the fleet already has a newer fork (`rdma-core-usb4` 63.0) realized in the
  store. The old build path is kept as an explicit opt-in fallback, not
  deleted, in case a future environment lacks it.
- **Kernel-devel auto-discovery beyond a few candidate paths.**
  `fetch-and-build.sh` now gives a concrete `nix build` command for the
  stock-kernel case (this environment's actual case) rather than a general
  hand-wave, but still does not invoke that build automatically -- it
  belongs in the operator's own flake evaluation.

## Gate compliance

- `bash -n` passes clean on `fetch-and-build.sh` (re-checked after this
  pass's edits).
- No `insmod`/`modprobe -a <local .ko>`/boot-config/firewall write happens
  anywhere in this package; every load and config step is explicit,
  attended, and documented as attended.
- No reboot is simultaneous; `attended-bringup.md` step 7 is sequential by
  design, and Gate 0 forbids starting the plan at all without a banked
  TP=2-over-TCP benchmark.
- Nothing under `/home/tom/mecattaf/flashnext` was read for writing, or
  written to, at any point.
- All third-party code is fetched at the pinned SHAs above at build time;
  nothing third-party is vendored into this package.
