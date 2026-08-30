#!/usr/bin/env bash
# Stage the workload checkpoint on BOTH nodes and collect both receipts.
# Coordinator runs locally; the worker gets the script streamed over the
# wire (it does not carry this repo).
#
# Receipt quarantine (D12): each node's receipt is staged via a temp path
# and installed to results/receipts/weights-<host>.json only when its status
# is pass; a fail receipt lands under results/receipts/failed/ instead — a
# typed blocker outside the grading walk, so a short staging can never
# permanently redden every later gate run. The final assert still exits
# non-zero on any fail.
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
RECEIPTS="$REPO_ROOT/results/receipts"
mkdir -p "$RECEIPTS"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

install_receipt() {  # $1 = staged receipt path, $2 = host label
  local src="$1" host="$2" dest
  if python3 -c "import json,sys; sys.exit(0 if json.load(open('$src'))['status']=='pass' else 1)"; then
    dest="$RECEIPTS/weights-$host.json"
  else
    mkdir -p "$RECEIPTS/failed"
    dest="$RECEIPTS/failed/weights-$host.json"
    echo "stage-weights-both: $host staging FAILED; receipt quarantined at $dest" >&2
  fi
  mv "$src" "$dest"
  printf '%s' "$dest"
}

echo "== coordinator =="
bash "$REPO_ROOT/scripts/stage-weights.sh" "$TMP/weights-coordinator.json"
COORD_RECEIPT="$(install_receipt "$TMP/weights-coordinator.json" coordinator)"

echo "== worker =="
ssh worker 'bash -s -- /tmp/fn-weights-receipt.json' \
  < "$REPO_ROOT/scripts/stage-weights.sh"
scp -q worker:/tmp/fn-weights-receipt.json "$TMP/weights-worker.json"
WORKER_RECEIPT="$(install_receipt "$TMP/weights-worker.json" worker)"

python3 - "$COORD_RECEIPT" "$WORKER_RECEIPT" <<'PY'
import json, sys
ok = True
for host, path in (("coordinator", sys.argv[1]), ("worker", sys.argv[2])):
    r = json.loads(open(path).read())
    if r["status"] != "pass":
        print(f"{host}: staging FAILED (quarantined receipt: {path})")
        ok = False
        continue
    print(f"{host}: {r['data']['shards']} shards, {r['data']['bytes']} bytes")
if not ok:
    sys.exit(1)
print("both nodes staged")
PY
