#!/usr/bin/env bash
# Stage the workload checkpoint on BOTH nodes and collect both receipts.
# Coordinator runs locally; the worker gets the script streamed over the
# wire (it does not carry this repo).
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
RECEIPTS="$REPO_ROOT/results/receipts"
mkdir -p "$RECEIPTS"

echo "== coordinator =="
bash "$REPO_ROOT/scripts/stage-weights.sh" "$RECEIPTS/weights-coordinator.json"

echo "== worker =="
ssh worker 'bash -s -- /tmp/fn-weights-receipt.json' \
  < "$REPO_ROOT/scripts/stage-weights.sh"
scp -q worker:/tmp/fn-weights-receipt.json "$RECEIPTS/weights-worker.json"

python3 - "$RECEIPTS" <<'PY'
import json, pathlib, sys
receipts = pathlib.Path(sys.argv[1])
for host in ("coordinator", "worker"):
    r = json.loads((receipts / f"weights-{host}.json").read_text())
    assert r["status"] == "pass", f"{host} staging failed"
    print(f"{host}: {r['data']['shards']} shards, {r['data']['bytes']} bytes")
print("both nodes staged")
PY
