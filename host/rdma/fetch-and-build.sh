#!/usr/bin/env bash
# fetch-and-build.sh -- stage a matched Thunderbolt/USB4 RoCE-RDMA module set
# for the flashnext coordinator/worker pair, for an operator to install by
# hand. This is the MORNING, ATTENDED lane (spec.md P7): the overnight
# transport of record stays plain sockets on both rails. Nothing here loads a
# module, edits boot config, or touches /home/tom/mecattaf/flashnext.
#
# What this fetches and builds, and why:
#   - westeri/thunderbolt.git @ BASE           -- patched thunderbolt core + net
#   - hellas-ai/thunderbolt-ibverbs @ IBV_BASE -- the usb4_rdma verbs provider
#     KERNEL MODULE only (thunderbolt_ibverbs.ko). This is the one piece of
#     RDMA that is genuinely unpaid on this pair as of the last substrate
#     check: the running kernel on both nodes is stock linux-7.1.4
#     (/run/booted-system/kernel), not nix-strix-halo's linux-thunderbolt --
#     that derivation has no output and no .drv in the store at all.
#   - a 10-file kernel patch series that lives INSIDE the ibverbs clone
#     (kernel-workflow/patches/), applied to the westeri tree with `git apply
#     -C1` (matches the fuzz the series itself was cut against).
#
# What this does NOT build, deliberately: the userspace verbs provider.
# nix-strix-halo already has it realized in the store (thunderbolt-ibverbs
# 0.3.4, paired with an rdma-core fork it calls rdma-core-usb4 at 63.0) --
# building a second copy from the reference's v57.0 pin would just be a
# stale, redundant, possibly-conflicting duplicate. See check_userspace()
# below and REPORT.md for the open question that already-realized pair
# leaves: whether it was built from the SAME thunderbolt-ibverbs commit this
# script pins for the kernel module, or a different one.
#
# Deliberately NOT fetched -- see REPORT.md for the full reasoning:
#   - no vendor's-own local patch on top of thunderbolt-ibverbs. One exists in
#     the wider ds4-vllm reference tree, but it is that repo's own unpublished
#     diff, not something at a public pin -- there is nothing to fetch, and we
#     do not vendor other repos' un-pinned local patches. The upstream module
#     builds and loads fine without it; the one runtime knob it would have
#     unlocked is left off below (see build_matched_set's insmod comment).
#   - no fourth "IRQ moderation" helper module. Same reasoning: it is a small
#     from-scratch module owned by that other tree, not fetched from any
#     pinned public source. Its absence is a latency-floor difference only
#     (stock ~128us NHI IRQ moderation instead of a hand-tuned 8us), not a
#     correctness one.
#
# Usage:
#   host/rdma/fetch-and-build.sh [KVER]
#
# KVER defaults to the running kernel. Needs kernel headers/devel for KVER,
# git, a C toolchain, and network on first run (fetches the two pins). No
# sudo -- everything lands under $STAGE_DIR (default: see below).
set -euo pipefail

# ---------------------------------------------------------------------------
# 0. constants -- the exact pins this package is built against
# ---------------------------------------------------------------------------
WESTERI_BASE=503c5ae1e72aa9ed91925dafa3d82ee2e992747f
WESTERI_REMOTE=https://git.kernel.org/pub/scm/linux/kernel/git/westeri/thunderbolt.git
IBV_BASE=76ba39b630a70accb72f19388eefe48844b50eb8
IBV_REMOTE=https://github.com/hellas-ai/thunderbolt-ibverbs
# The reference recipe's rdma-core pin (v57.0, container-only, mutable tag not
# a SHA -- see REPORT.md). Kept only as the fallback build's target if the
# operator explicitly opts into it; the default path does not clone or build
# this at all, because nix-strix-halo already ships a newer, different fork
# (rdma-core-usb4 63.0) in this fleet's own store. See check_userspace().
RDMA_CORE_TAG=v57.0
RDMA_CORE_REMOTE=https://github.com/linux-rdma/rdma-core
NIX_USERSPACE_PROVIDER_GLOB='*thunderbolt-ibverbs-0.3.4*'
NIX_USERSPACE_RDMACORE_GLOB='*rdma-core-usb4-63.0*'

# The 10-file series lives at $IBVERBS_DIR/kernel-workflow/patches/ once the
# ibverbs repo is cloned -- it is fetched, not vendored, by cloning that repo.
LOCAL_SERIES="
0002-thunderbolt-tunnel-add-dma-priority-weight-params.patch
0003-thunderbolt-nhi-add-ring-debugfs-instrumentation.patch
0006-thunderbolt-xdomain-bound-response-copy.patch
0004-thunderbolt-nhi-clear-pending-before-unmask.patch
0005-thunderbolt-xdomain-log-unmatched-protocol-uuids.patch
0007-thunderbolt-xdomain-pass-source-to-protocol-handlers.patch
0008-thunderbolt-xdomain-pin-protocol-handler-owner.patch
0009-thunderbolt-xdomain-match-properties-by-identity.patch
0010-thunderbolt-xdomain-drain-protocol-callbacks-on-unr.patch
0009-thunderbolt-xdomain-lane-bonding-module-param.patch
"

TARGET_KVER="${TARGET_KVER:-7.1.4}"
KVER="${1:-$(uname -r)}"
CACHE_DIR="${FLASHNEXT_RDMA_CACHE:-$HOME/.cache/flashnext-rdma-build}"
STAGE_DIR="${FLASHNEXT_RDMA_STAGE:-$HOME/.local/state/flashnext-rdma}"
WORK="$CACHE_DIR/$KVER"
OUT="$STAGE_DIR/$KVER/out"
WESTERI_DIR="$CACHE_DIR/src/westeri-thunderbolt"
IBVERBS_DIR="$CACHE_DIR/src/thunderbolt-ibverbs"
RDMA_CORE_DIR="$CACHE_DIR/src/rdma-core"
CAP="nice -n 19 ionice -c3"

log()  { printf '== %s ==\n' "$*"; }
fail() { printf '\n!! %s\n' "$*" >&2; exit 1; }

# ---------------------------------------------------------------------------
# 1. gate: running kernel must be the pinned target, on THIS node
# ---------------------------------------------------------------------------
check_local_kernel() {
  local running="$1"
  case "$running" in
    "$TARGET_KVER"|"$TARGET_KVER"-*)
      log "kernel gate: $running matches target $TARGET_KVER" ;;
    *)
      fail "kernel gate FAILED: running kernel is '$running', package is vermagic-pinned to '$TARGET_KVER'.
   Building against the wrong kernel produces modules that silently refuse to
   load (vermagic mismatch) or, worse, load and panic on cable connect if the
   ABI drifted between kernel releases. Refusing to build. If $TARGET_KVER is
   stale, update TARGET_KVER deliberately -- do not build around this gate."
  esac
}

# ---------------------------------------------------------------------------
# 2. gate: kernel must ALSO match on the peer node -- checked over the 5GbE
#    control wire (enp191s0), never over Thunderbolt, since rail 0 may not be
#    RDMA-capable yet when this script runs.
# ---------------------------------------------------------------------------
check_peer_kernel() {
  local self peer
  self="$(hostname -s)"
  case "$self" in
    coordinator) peer="${PEER_HOST:-worker}" ;;
    worker)      peer="${PEER_HOST:-coordinator}" ;;
    *)
      log "!! hostname '$self' is neither 'coordinator' nor 'worker' -- set PEER_HOST explicitly to name the other node"
      peer="${PEER_HOST:-}"
      [ -n "$peer" ] || fail "cannot determine the peer node; set PEER_HOST and re-run"
      ;;
  esac

  log "peer kernel gate: checking $peer over the control wire (ssh)"
  local peer_kver
  if ! peer_kver="$(ssh -o BatchMode=yes -o ConnectTimeout=8 "$peer" uname -r 2>/dev/null)"; then
    if [ "${ALLOW_UNVERIFIED_PEER:-0}" = 1 ]; then
      log "!! could not reach $peer over ssh -- ALLOW_UNVERIFIED_PEER=1 set, proceeding anyway. The peer's kernel is UNVERIFIED; confirm by hand before the coordinated reboot (see attended-bringup.md)."
      return 0
    fi
    fail "could not ssh to $peer to verify its kernel version. This is a hard gate by design:
   building a vermagic-pinned module set for a mismatched peer is exactly how
   you get a coordinated reboot that wedges instead of converges. Fix ssh
   reachability over the control wire, or re-run with ALLOW_UNVERIFIED_PEER=1
   to proceed at your own risk (attended session only)."
  fi
  case "$peer_kver" in
    "$TARGET_KVER"|"$TARGET_KVER"-*)
      log "peer kernel gate: $peer reports $peer_kver, matches target" ;;
    *)
      fail "peer kernel gate FAILED: $peer reports '$peer_kver', target is '$TARGET_KVER'. Both nodes must run the vermagic-pinned kernel before either side builds. Bring the peer's kernel in line first."
      ;;
  esac
}

# ---------------------------------------------------------------------------
# 3. gate: Secure Boot must be OFF on the node this script is running on.
#    The task record notes coordinator is known-disabled; worker is NOT --
#    this check is what turns "known" and "unverified" into one fact for
#    both nodes, every run.
# ---------------------------------------------------------------------------
check_secure_boot() {
  local state="unknown"
  if command -v mokutil >/dev/null 2>&1; then
    if mokutil --sb-state 2>/dev/null | grep -qi 'SecureBoot enabled'; then
      state="enabled"
    elif mokutil --sb-state 2>/dev/null | grep -qi 'SecureBoot disabled'; then
      state="disabled"
    fi
  fi
  if [ "$state" = unknown ]; then
    # efivars heuristic: SecureBoot-<guid> is a 4-byte attribute header
    # followed by a single data byte; 1 = enabled, 0 = disabled.
    local var
    var=$(find /sys/firmware/efi/efivars -maxdepth 1 -name 'SecureBoot-*' 2>/dev/null | head -1)
    if [ -n "$var" ] && [ -r "$var" ]; then
      local last_byte
      last_byte=$(od -An -tu1 "$var" 2>/dev/null | awk '{print $NF}')
      case "$last_byte" in
        0) state="disabled" ;;
        1) state="enabled" ;;
      esac
    fi
  fi

  case "$state" in
    disabled)
      log "Secure Boot gate: disabled on $(hostname -s) -- unsigned modules will load" ;;
    enabled)
      fail "Secure Boot gate FAILED: ENABLED on $(hostname -s). Every module built here is
   unsigned; the kernel will refuse every insmod. Disable Secure Boot (or MOK-sign
   the four .ko files yourself) before building or installing on this node." ;;
    *)
      fail "Secure Boot gate: state UNKNOWN on $(hostname -s) (no mokutil, no readable
   /sys/firmware/efi/efivars/SecureBoot-*). This is exactly the 'worker is
   unverified' case the task called out -- do not assume disabled. Install
   mokutil and re-run, or confirm by hand, before building on this node."
  esac
}

# ---------------------------------------------------------------------------
# 3b. informational: log which kernel we're actually building against. As of
#     the last substrate check both nodes run the STOCK nixpkgs kernel, not
#     nix-strix-halo's linux-thunderbolt fork (which has no build output in
#     the store at all -- route (b) in attended-bringup.md would have to
#     build it from scratch, first-ever realization, unknown duration and
#     unknown breakage). This is informational, not a gate: if a future run
#     finds linux-thunderbolt already booted, that's route (b) already
#     landed, and this script's out-of-tree build below is then redundant
#     (harmless, but redundant) rather than wrong.
# ---------------------------------------------------------------------------
log_kernel_provenance() {
  local kpath
  kpath=$(readlink -f /run/booted-system/kernel 2>/dev/null || echo "(unknown)")
  case "$kpath" in
    *thunderbolt*)
      log "kernel provenance: $kpath -- looks like nix-strix-halo's patched kernel is ALREADY booted. Route (a)'s out-of-tree build below is redundant in that case; see attended-bringup.md route selection." ;;
    *)
      log "kernel provenance: $kpath -- stock kernel, as expected. This is route (a)'s target (out-of-tree modules against the running stock kernel), not route (b) (nix-strix-halo's linux-thunderbolt, a full kernel swap)." ;;
  esac
}

# ---------------------------------------------------------------------------
# 4. kernel-devel discovery -- the reference recipe assumes a conventional
#    /usr/src/kernels or /lib/modules/$KVER/build layout (RPM-style). This
#    pair runs NixOS end to end, which does not populate either path by
#    default. Try both, then a couple of NixOS-shaped fallbacks, and refuse
#    loudly with a concrete next step rather than guess further.
# ---------------------------------------------------------------------------
find_kdev() {
  local d
  for d in "/usr/src/kernels/$KVER" "/lib/modules/$KVER/build"; do
    [ -f "$d/Makefile" ] && { readlink -f "$d"; return 0; }
  done
  # NixOS: a booted generation sometimes exposes its module tree here.
  for d in "/run/booted-system/kernel-modules/lib/modules/$KVER/build" \
           "/run/current-system/kernel-modules/lib/modules/$KVER/build"; do
    [ -f "$d/Makefile" ] && { readlink -f "$d"; return 0; }
  done
  return 1
}

# ---------------------------------------------------------------------------
# 5. fetch + patch the westeri tree, fetch the ibverbs tree
# ---------------------------------------------------------------------------
fetch_sources() {
  mkdir -p "$(dirname "$IBVERBS_DIR")"

  log "thunderbolt-ibverbs @ ${IBV_BASE:0:12}"
  [ -d "$IBVERBS_DIR/.git" ] || git clone "$IBV_REMOTE" "$IBVERBS_DIR"
  git -C "$IBVERBS_DIR" cat-file -e "$IBV_BASE^{commit}" 2>/dev/null || git -C "$IBVERBS_DIR" fetch origin "$IBV_BASE"
  git -C "$IBVERBS_DIR" checkout -qf "$IBV_BASE"
  git -C "$IBVERBS_DIR" clean -qfdx

  log "westeri/thunderbolt @ ${WESTERI_BASE:0:12} + the 10-file series (fetched FROM the ibverbs clone above, not vendored)"
  [ -d "$WESTERI_DIR/.git" ] || git clone "$WESTERI_REMOTE" "$WESTERI_DIR"
  git -C "$WESTERI_DIR" cat-file -e "$WESTERI_BASE^{commit}" 2>/dev/null || git -C "$WESTERI_DIR" fetch origin "$WESTERI_BASE"
  git -C "$WESTERI_DIR" checkout -qf "$WESTERI_BASE"
  git -C "$WESTERI_DIR" clean -qfd

  local series_dir="$IBVERBS_DIR/kernel-workflow/patches"
  local p
  for p in $LOCAL_SERIES; do
    [ -f "$series_dir/$p" ] || fail "expected patch '$p' not found under $series_dir -- the ibverbs clone's series layout may have moved since this script was written; re-check IBV_BASE=${IBV_BASE:0:12}"
    git -C "$WESTERI_DIR" apply -C1 "$series_dir/$p" || fail "'$p' did not apply cleanly to the westeri tree at $WESTERI_BASE"
  done
  grep -q callback_xd "$WESTERI_DIR/include/linux/thunderbolt.h" \
    || fail "series applied but callback_xd is not in thunderbolt.h afterward -- the series did not actually take; do not build on top of this"
  log "series applied and verified (callback_xd present)"
}

# ---------------------------------------------------------------------------
# 6. build the matched core + net + ibverbs set
# ---------------------------------------------------------------------------
build_matched_set() {
  local kdev
  kdev="$(find_kdev)" || fail "no kernel-devel tree found for $KVER under any of:
     /usr/src/kernels/$KVER
     /lib/modules/$KVER/build
     /run/booted-system/kernel-modules/lib/modules/$KVER/build
     /run/current-system/kernel-modules/lib/modules/$KVER/build
   This pair is NixOS, and NixOS does not populate a conventional kernel-devel
   layout by default the way the RPM-style reference recipe assumes. The
   running kernel is a STOCK nixpkgs kernel though (confirmed: no
   linux-thunderbolt in the store), which makes this the easy case -- a plain
   nixpkgs kernel's .dev output is a normal, always-buildable derivation,
   unlike a hand-patched fork would be. Concretely:
     nix build '.#nixosConfigurations.<this-host>.config.boot.kernelPackages.kernel.dev'
   then symlink its result to /lib/modules/$KVER/build and re-run. Not
   attempting that build automatically here -- it belongs in the operator's
   own flake evaluation, not silently invoked by a fetch script; see REPORT.md."

  log "KDIR: symlink farm over $kdev + westeri thunderbolt.h overlay + CONFIG_USB4_CONFIGFS=y"
  rm -rf "$WORK"; mkdir -p "$OUT"
  cp -as "$kdev" "$WORK/kdir"
  rm -f "$WORK/kdir/include/linux/thunderbolt.h"
  cp "$WESTERI_DIR/include/linux/thunderbolt.h" "$WORK/kdir/include/linux/thunderbolt.h"
  local real_autoconf
  real_autoconf=$(readlink -f "$WORK/kdir/include/config/auto.conf")
  rm -f "$WORK/kdir/include/config/auto.conf"
  cp "$real_autoconf" "$WORK/kdir/include/config/auto.conf"
  grep -q '^CONFIG_USB4_CONFIGFS=y' "$WORK/kdir/include/config/auto.conf" \
    || echo 'CONFIG_USB4_CONFIGFS=y' >> "$WORK/kdir/include/config/auto.conf"

  log "1/3 thunderbolt core"
  $CAP make -j1 -C "$WORK/kdir" M="$WESTERI_DIR/drivers/thunderbolt" clean >/dev/null 2>&1 || true
  $CAP make -j"$(nproc)" -C "$WORK/kdir" M="$WESTERI_DIR/drivers/thunderbolt" 2>&1 | tail -3
  cp -f "$WESTERI_DIR/drivers/thunderbolt/thunderbolt.ko" "$OUT/thunderbolt-patched.ko"

  log "2/3 thunderbolt_net (against core symvers)"
  $CAP make -j1 -C "$WORK/kdir" M="$WESTERI_DIR/drivers/net/thunderbolt" clean >/dev/null 2>&1 || true
  $CAP make -j"$(nproc)" -C "$WORK/kdir" M="$WESTERI_DIR/drivers/net/thunderbolt" \
    KBUILD_EXTRA_SYMBOLS="$WESTERI_DIR/drivers/thunderbolt/Module.symvers" 2>&1 | tail -3
  cp -f "$WESTERI_DIR/drivers/net/thunderbolt/thunderbolt_net.ko" "$OUT/thunderbolt_net.ko"

  log "3/3 thunderbolt_ibverbs (plain upstream tree -- no local RC-write zero-copy patch, see header)"
  $CAP make -C "$IBVERBS_DIR/kernel" KDIR="$WORK/kdir" modules 2>&1 | tail -3
  cp -f "$IBVERBS_DIR/kernel/thunderbolt_ibverbs.ko" "$OUT/thunderbolt_ibverbs.ko"
  local aware
  aware=$(strings "$OUT/thunderbolt_ibverbs.ko" | grep -c 'source-aware XDomain handler' || true)
  [ "$aware" -ge 1 ] || fail "thunderbolt_ibverbs.ko built but the source-aware XDomain handler string is absent -- the 10-file series did not actually reach this build; do not stage it"

  # NOTE for the attended install plan: because we do not carry the other
  # tree's local RC-write zero-copy patch, this build does NOT expose the
  # native_rc_split_zcopy module param. Load thunderbolt_ibverbs WITHOUT that
  # param -- the driver's own comment in the wider reference documents this
  # exact fallback as the one that "needs no cross-box coordination", which
  # is one less thing to get byte-identical across coordinator and worker.

  log "module set for $KVER built:" | tee "$OUT/MANIFEST"
  local m
  for m in "$OUT"/*.ko; do
    printf "  %-28s %s\n" "$(basename "$m")" "$(modinfo -F vermagic "$m" 2>/dev/null || echo '(vermagic unreadable)')" | tee -a "$OUT/MANIFEST"
  done
}

# ---------------------------------------------------------------------------
# 7. userspace verbs provider -- CHECK FIRST, build only as an explicit
#    opt-in fallback. nix-strix-halo already realizes thunderbolt-ibverbs
#    0.3.4 + its own rdma-core-usb4 63.0 fork in this fleet's store (a
#    substrate fact, not this script's guess) -- building a second copy from
#    the reference's stale v57.0 pin would be redundant at best and a
#    conflicting ABI at worst. Default behavior: report what's in the store
#    and stop. Never gates the kernel module build above either way.
# ---------------------------------------------------------------------------
check_userspace() {
  log "userspace verbs provider -- checking the nix store before building anything"
  local tbv_pkg rdma_pkg
  tbv_pkg=$(find /nix/store -maxdepth 1 -name "$NIX_USERSPACE_PROVIDER_GLOB" 2>/dev/null | head -1)
  rdma_pkg=$(find /nix/store -maxdepth 1 -name "$NIX_USERSPACE_RDMACORE_GLOB" 2>/dev/null | head -1)

  if [ -n "$tbv_pkg" ] && [ -n "$rdma_pkg" ]; then
    log "found in store: $(basename "$tbv_pkg"), $(basename "$rdma_pkg") -- userspace is already realized, not building a second copy"
    log "!! OPEN QUESTION (see REPORT.md): this script's kernel module is built from thunderbolt-ibverbs @ ${IBV_BASE:0:12}. Whether the ALREADY-REALIZED userspace above was built from that same commit, or a different one at a different uverbs ABI revision, is unverified here. Confirm before trusting 'ibv_devices' output on the host -- not just its presence, its version against ${IBV_BASE:0:12}."
    return 0
  fi

  log "!! expected nix-realized userspace not found in /nix/store on this node ($NIX_USERSPACE_PROVIDER_GLOB / $NIX_USERSPACE_RDMACORE_GLOB)."
  if [ "${FLASHNEXT_BUILD_RDMA_CORE_FALLBACK:-0}" != 1 ]; then
    log "   Not building a from-scratch copy by default -- the reference's v57.0 pin is a known-stale target against what this fleet already committed to (rdma-core-usb4 63.0). Set FLASHNEXT_BUILD_RDMA_CORE_FALLBACK=1 to attempt the legacy vanilla-clone fallback anyway (host-diagnostics only; never gates the kernel module build)."
    return 0
  fi
  build_rdma_core_fallback
}

build_rdma_core_fallback() {
  log "rdma-core @ $RDMA_CORE_TAG (LEGACY fallback build -- host diagnostics only, best effort, non-fatal)"
  mkdir -p "$(dirname "$RDMA_CORE_DIR")"
  if ! { [ -d "$RDMA_CORE_DIR/.git" ] || git clone --depth 1 -b "$RDMA_CORE_TAG" "$RDMA_CORE_REMOTE" "$RDMA_CORE_DIR"; }; then
    log "!! rdma-core clone failed -- skipping host-side provider build (does not affect the kernel module set above)"
    return 0
  fi

  # NOTE: AGENTS.md 1.3 says the provider patches come "from the upstream
  # ibverbs repo" but does not give a path, and this environment has no
  # network access to browse hellas-ai/thunderbolt-ibverbs and confirm one.
  # Try the layout the kernel-side series uses as a template; if it is not
  # there, say so plainly and move on -- this build is diagnostics-only.
  local candidate found=0
  for candidate in "$IBVERBS_DIR/userspace/patches" "$IBVERBS_DIR/rdma-core-patches" "$IBVERBS_DIR/provider/patches"; do
    if [ -d "$candidate" ]; then
      log "applying provider patches from $candidate"
      local p
      for p in "$candidate"/*.patch; do
        [ -e "$p" ] || continue
        git -C "$RDMA_CORE_DIR" apply "$p" || fail "provider patch $(basename "$p") did not apply to rdma-core $RDMA_CORE_TAG"
      done
      found=1
      break
    fi
  done
  if [ "$found" = 0 ]; then
    log "!! no provider-patch directory found under the ibverbs clone at any of the guessed paths.
   This build step is UNVERIFIED against the real repo layout (see REPORT.md).
   Skipping the host-side provider; usb4_rdma will still work fine inside the
   serving container, which builds its own copy independently."
    return 0
  fi

  log "building rdma-core (host diagnostics only; not installed)"
  mkdir -p "$RDMA_CORE_DIR/build"
  ( cd "$RDMA_CORE_DIR/build" && cmake .. -DENABLE_STATIC=0 >/tmp/rdma-core-cmake.log 2>&1 && $CAP make -j"$(nproc)" >/tmp/rdma-core-build.log 2>&1 ) \
    || log "!! rdma-core build failed -- see /tmp/rdma-core-{cmake,build}.log. Non-fatal: this is a diagnostics convenience, not a gate."
}

# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------
main() {
  log "flashnext RDMA fetch-and-build -- target kernel $TARGET_KVER, building for $KVER"
  log "REMINDER: this stages a MORNING PLAN. It does not gate on, and cannot
   confirm, the one precondition that actually matters most -- that a
   committed TP=2-over-TCP benchmark is already banked under results/. Staging
   this package is harmless prep; attended-bringup.md refuses to let you go
   further than staging until that benchmark exists."
  log_kernel_provenance
  check_local_kernel "$(uname -r)"
  check_peer_kernel
  check_secure_boot
  fetch_sources
  build_matched_set
  check_userspace

  cat <<PLAN

== staged, nothing installed ==
Artifacts: $OUT
  thunderbolt-patched.ko
  thunderbolt_net.ko
  thunderbolt_ibverbs.ko
  MANIFEST  (vermagic per module -- diff this against the peer's before trusting a match)

Nothing was installed, blacklisted, loaded, or rebooted, and no boot config or
firewall rule was touched. This node's half of the attended plan:
  1. Copy $OUT/*.ko to the same path prefix on BOTH nodes (or re-run this
     script on each -- it is deterministic given the same KVER).
  2. Diff MANIFEST between coordinator and worker. Vermagic must match
     exactly, or the pair will not agree on the ABI.
  3. Follow host/rdma/attended-bringup.md from the top -- it starts with a
     hard gate (TP=2-over-TCP banked, or stop here) and a MANDATORY step
     that has nothing to do with RDMA modules at all: fixing the worker's
     deploy-rs path off Thunderbolt before anything below touches a kernel
     module. Do not skip ahead to insmod from this script's output, and do
     not reboot anything without reading that checklist's reboot-order
     section first -- it is sequential (worker first), not simultaneous.

PLAN
}

main "$@"
