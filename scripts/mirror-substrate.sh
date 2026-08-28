#!/usr/bin/env bash
# Mirror the pinned TheRock gfx1151 ROCm tarball against fixed-output rot.
#
# The nix-native engine lane rests on ONE fetchurl whose URL lives on AMD's
# *nightlies* host. `pkgs/therock/sources/rocm.json` in the nix-strix-halo
# checkout pins:
#
#   url  https://rocm.nightlies.amd.com/tarball-multi-arch/
#          therock-dist-linux-gfx1151-7.15.0a20260719.tar.gz
#   hash sha256-04mWViYQujFAy+mbptQ8djGHgLFlmaQHFTfTW8LJ/MY=
#        (= sha256 hex d38996562610ba3140cbe99ba6d43c76318780b16599a4071537d35bc2c9fcc6)
#
# Nightly hosts expire. The realized 8.4 G SDK is in this box's /nix/store
# today, but a GC or a rebuild on the second node re-fetches from that URL and
# there is no substitute on either upstream cache (narinfo-404, per dotfiles
# `modules/strix-ai.nix:55-64`). This script takes a local copy so the engine
# lane survives the URL going away.
#
# The mirrored file is byte-identical to what the fixed-output derivation
# wants, so recovery is a one-liner:
#
#   nix-prefetch-url --type sha256 "file://$DEST/$FILENAME"
#   # or, to seed the store under the exact FOD hash nix expects:
#   nix store add-file --name "$FILENAME" "$DEST/$FILENAME"
#
# Kill-switch: FN_MIRROR=0 makes this script a loud no-op, for the case where
# the operator does not want 1.75 GB written tonight.

set -euo pipefail

URL="https://rocm.nightlies.amd.com/tarball-multi-arch/therock-dist-linux-gfx1151-7.15.0a20260719.tar.gz"
FILENAME="therock-dist-linux-gfx1151-7.15.0a20260719.tar.gz"
# Hex form of the SRI hash pinned in nix-strix-halo pkgs/therock/sources/rocm.json.
EXPECTED_SHA256="d38996562610ba3140cbe99ba6d43c76318780b16599a4071537d35bc2c9fcc6"
EXPECTED_SRI="sha256-04mWViYQujFAy+mbptQ8djGHgLFlmaQHFTfTW8LJ/MY="

PRIMARY="${FN_MIRROR_DIR:-/mnt/nas/mirrors}"
FALLBACK="${FN_MIRROR_FALLBACK:-$HOME/mirrors}"

say() { echo "mirror-substrate: $*"; }

if [ "${FN_MIRROR:-1}" = "0" ]; then
  say "KILL-SWITCH FN_MIRROR=0 — refusing to mirror; the engine lane keeps its"
  say "sole dependency on ${URL}"
  exit 0
fi

# --- pick a writable destination -------------------------------------------
pick_dest() {
  local d="$1"
  mkdir -p "$d" 2>/dev/null || return 1
  [ -w "$d" ] || return 1
  printf '%s' "$d"
}

DEST=""
if DEST="$(pick_dest "$PRIMARY")"; then
  say "destination: $DEST (NAS)"
else
  say "WARN - $PRIMARY is not writable (mkdir or write test failed);"
  say "WARN - falling back to $FALLBACK. The NAS copy is still owed."
  if ! DEST="$(pick_dest "$FALLBACK")"; then
    say "FAIL - neither $PRIMARY nor $FALLBACK is writable"
    exit 1
  fi
  say "destination: $DEST (local fallback)"
fi

TARGET="$DEST/$FILENAME"
SUMFILE="$TARGET.sha256"

# --- already mirrored? ------------------------------------------------------
verify() {
  local got
  got="$(sha256sum "$TARGET" | cut -d' ' -f1)"
  printf '%s  %s\n' "$got" "$FILENAME" > "$SUMFILE"
  if [ "$got" = "$EXPECTED_SHA256" ]; then
    say "ok   - sha256 $got matches the nix pin ($EXPECTED_SRI)"
    return 0
  fi
  say "FAIL - sha256 $got does NOT match the nix pin"
  say "FAIL - expected $EXPECTED_SHA256 ($EXPECTED_SRI)"
  say "FAIL - AMD re-cut the nightly under the same name; this mirror is NOT"
  say "FAIL - the substrate the flake pins. Do not use it."
  return 1
}

if [ -f "$TARGET" ]; then
  say "already present: $TARGET ($(stat -c %s "$TARGET") bytes) — verifying"
  verify
  exit $?
fi

# --- download ---------------------------------------------------------------
say "fetching $URL"
say "-> $TARGET (expect 1752337361 bytes)"
if command -v curl >/dev/null 2>&1; then
  curl -fL --retry 5 --retry-delay 5 -C - -o "$TARGET.part" "$URL"
elif command -v wget >/dev/null 2>&1; then
  wget -c -O "$TARGET.part" "$URL"
else
  say "FAIL - neither curl nor wget is available"
  exit 1
fi
mv "$TARGET.part" "$TARGET"
say "downloaded $(stat -c %s "$TARGET") bytes"

verify
