#!/usr/bin/env bash
# cp-build — the engine build checkpoint, two lanes, one receipt.
#
# Lane 1 (nix, of record): `nix build .#vllm-fork`. This recompiles vLLM's
# few-hundred HIP kernels through the TheRock toolchain and is the night's
# unit of currency (specs/flashnext/evidence/nix-packaging-brief.md §1.2).
# The wall-clock is UNMEASURED upstream — this script's first run IS the
# measurement, so it is logged into the receipt whether the build passes or
# fails.
#
# Lane 2 (container, fallback): `bash container/build.sh`. Taken ONLY when
# the nix lane aborts, and the nix abort reason is carried into the receipt
# rather than swallowed. Silent fallback is this platform's dominant failure
# mode; the fallback here is loud, recorded, and marked in the receipt's
# `lane` field so no downstream claim can pretend it came from nix.
#
# Exit status: 0 if EITHER lane produced an engine. Nonzero only if BOTH
# failed.
#
# Kill-switches:
#   FN_BUILD_LANE=nix        -- nix only, no fallback (fail hard)
#   FN_BUILD_LANE=container  -- skip nix entirely
#   FN_BUILD_LANE=both       -- default: nix, then container on failure
#   FN_BUILD_CORES=32        -- --cores
#   FN_BUILD_JOBS=1          -- --max-jobs

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
RECEIPTS="$REPO_ROOT/results/receipts"
RECEIPT="$RECEIPTS/build.json"
LOGDIR="$REPO_ROOT/results/logs"
NIX_LOG="$LOGDIR/build-nix.log"

# The substrate the nix lane resolves to. torch/triton/rocm all arrive as
# prebuilt AMD wheels (nix-strix-halo overlays/therock-python.nix:53-61);
# only vLLM is compiled. Recorded so the receipt names the substrate rather
# than implying it.
NIX_TORCH="2.11.0+rocm7.15.0a20260719"

LANE="${FN_BUILD_LANE:-both}"
CORES="${FN_BUILD_CORES:-32}"
JOBS="${FN_BUILD_JOBS:-1}"

mkdir -p "$RECEIPTS" "$LOGDIR"

say() { echo "cp-build: $*"; }
now_iso() { date -u +%Y-%m-%dT%H:%M:%SZ; }

write_receipt() { # write_receipt <status> <data-json>
  local status="$1" data="$2"
  python3 - "$RECEIPT" "$status" "$data" <<'PY'
import json, sys
path, status, data = sys.argv[1], sys.argv[2], sys.argv[3]
import datetime
receipt = {
    "step": "build",
    "status": status,
    "ts": datetime.datetime.now(datetime.timezone.utc)
          .strftime("%Y-%m-%dT%H:%M:%SZ"),
    "data": json.loads(data),
}
with open(path, "w") as fh:
    json.dump(receipt, fh, indent=2)
    fh.write("\n")
print(f"cp-build: receipt written -> {path}")
PY
}

# ---------------------------------------------------------------- nix lane
nix_status="skipped"
nix_wall=""
nix_store_path=""
nix_abort=""

if [ "$LANE" = "nix" ] || [ "$LANE" = "both" ]; then
  say "lane 1 (nix): nix build .#vllm-fork --cores $CORES --max-jobs $JOBS"
  say "lane 1 (nix): this is the HIP recompile. Budget 2-3 of these per night."
  say "lane 1 (nix): log -> $NIX_LOG"

  t0=$(date +%s)
  nix build "$REPO_ROOT#vllm-fork" \
    --cores "$CORES" \
    --max-jobs "$JOBS" \
    --print-build-logs \
    --out-link "$REPO_ROOT/result-engine" \
    >"$NIX_LOG" 2>&1
  nix_rc=$?
  t1=$(date +%s)
  nix_wall=$(( t1 - t0 ))

  say "lane 1 (nix): wall clock ${nix_wall}s (rc=$nix_rc)"

  if [ "$nix_rc" -eq 0 ]; then
    nix_status="pass"
    nix_store_path="$(readlink -f "$REPO_ROOT/result-engine" 2>/dev/null || true)"
    say "lane 1 (nix): PASS -> $nix_store_path"
  else
    nix_status="fail"
    # Carry the abort reason, not just the code. The known-hostile ones are
    # the six `--replace-fail` literals in therock-vllm.nix's assigned
    # postPatch (brief §3) and a patch that no longer applies.
    nix_abort="$(grep -E 'error:|--replace-fail|does not apply|Hunk #|cannot fetch|hash mismatch' "$NIX_LOG" \
                 | tail -40 | tr -d '\r' || true)"
    if [ -z "$nix_abort" ]; then
      nix_abort="$(tail -40 "$NIX_LOG" | tr -d '\r' || true)"
    fi
    say "lane 1 (nix): FAIL. Abort reason (tail):"
    printf '%s\n' "$nix_abort" | sed 's/^/cp-build:   /'
  fi
else
  say "lane 1 (nix): SKIPPED by FN_BUILD_LANE=$LANE"
fi

# ------------------------------------------------------- receipt (nix pass)
if [ "$nix_status" = "pass" ]; then
  data=$(python3 - "$NIX_TORCH" "$nix_wall" "$nix_store_path" <<'PY'
import json, sys
torch, wall, store = sys.argv[1], int(sys.argv[2]), sys.argv[3]
print(json.dumps({
    "lane": "nix",
    "torch": torch,
    "wall_clock_s": wall,
    "store_path": store,
}))
PY
)
  write_receipt pass "$data"
  say "PASS via the nix lane."
  exit 0
fi

# ---------------------------------------------------------- container lane
if [ "$LANE" = "nix" ]; then
  say "FAIL - nix lane failed and FN_BUILD_LANE=nix forbids the fallback."
  data=$(python3 - "$NIX_TORCH" "${nix_wall:-0}" "$nix_abort" <<'PY'
import json, sys
torch, wall, abort = sys.argv[1], int(sys.argv[2] or 0), sys.argv[3]
print(json.dumps({
    "lane": "nix",
    "torch": torch,
    "wall_clock_s": wall,
    "store_path": None,
    "nix_abort": abort[-4000:],
    "fallback": "forbidden by FN_BUILD_LANE=nix",
}))
PY
)
  write_receipt fail "$data"
  exit 1
fi

CONTAINER_BUILD="$REPO_ROOT/container/build.sh"
say "lane 2 (container): FALLING BACK. This is NOT the nix lane."
say "lane 2 (container): substrate differs (AMD stable wheels, torch 2.13.0+rocm7.14.0)."
say "lane 2 (container): the receipt will say lane=\"container\" — do not let any"
say "lane 2 (container): downstream claim read as if nix produced the engine."

if [ ! -f "$CONTAINER_BUILD" ]; then
  say "lane 2 (container): FAIL - $CONTAINER_BUILD does not exist."
  data=$(python3 - "$NIX_TORCH" "${nix_wall:-0}" "$nix_abort" <<'PY'
import json, sys
torch, wall, abort = sys.argv[1], int(sys.argv[2] or 0), sys.argv[3]
print(json.dumps({
    "lane": "none",
    "torch": torch,
    "wall_clock_s": wall,
    "store_path": None,
    "nix_abort": abort[-4000:],
    "container_abort": "container/build.sh missing",
}))
PY
)
  write_receipt fail "$data"
  exit 1
fi

# container/build.sh writes its own receipt; run it, then MERGE our nix abort
# and the lane marker into whatever it left behind.
bash "$CONTAINER_BUILD"
c_rc=$?
say "lane 2 (container): rc=$c_rc"

data=$(python3 - "$RECEIPT" "$NIX_TORCH" "${nix_wall:-0}" "$nix_abort" "$c_rc" <<'PY'
import json, os, sys
path, torch, wall, abort, crc = sys.argv[1:6]
existing = {}
if os.path.exists(path):
    try:
        existing = (json.load(open(path)) or {}).get("data") or {}
    except json.JSONDecodeError:
        existing = {}
merged = dict(existing)
merged.update({
    "lane": "container",
    "nix_lane": "fail",
    "nix_torch": torch,
    "nix_wall_clock_s": int(wall or 0),
    "nix_abort": abort[-4000:],
    "container_rc": int(crc),
})
print(json.dumps(merged))
PY
)

if [ "$c_rc" -eq 0 ]; then
  write_receipt pass "$data"
  say "PASS via the container fallback. The nix lane is still owed."
  exit 0
fi

write_receipt fail "$data"
say "FAIL - both lanes failed."
exit 1
