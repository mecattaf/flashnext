#!/usr/bin/env bash
# Stage the workload checkpoint from the NAS library onto this node's NVMe.
# Runs on either twin; source path differs per host (worker mounts the
# library at /mnt/library). Idempotent: rsync skips complete files.
# Writes a receipt to the path given as $1 (default: stdout summary only).
#
# Doctrine: weights arrive Library -> node, never from the hub at runtime.
set -euo pipefail

ARTIFACT="flashnext-fp8"
UPSTREAM_DIR="qwen38-flash-next-fp8"   # library directory name (upstream-derived)
DEST="/var/lib/local-models/${ARTIFACT}"
RECEIPT="${1:-}"

case "$(hostname)" in
  coordinator) SRC="/mnt/nas/models/weights/${UPSTREAM_DIR}" ;;
  worker)      SRC="/mnt/library/weights/${UPSTREAM_DIR}" ;;
  *) echo "stage-weights: unknown host $(hostname)" >&2; exit 2 ;;
esac

[ -d "$SRC" ] || { echo "stage-weights: library source $SRC absent" >&2; exit 2; }

expected_shards=$(ls "$SRC" | grep -c 'safetensors$' || true)
echo "stage-weights: $SRC -> $DEST (${expected_shards} shards visible at source)"

sudo mkdir -p "$DEST"
sudo chown "$(id -un):$(id -gn)" "$DEST"
rsync -a --partial --inplace --info=progress2 "$SRC/" "$DEST/"

# Local integrity manifest: sha256 of every file, plus count and byte totals.
local_count=$(find "$DEST" -type f ! -name '*.sha256' | wc -l)
local_shards=$(ls "$DEST" | grep -c 'safetensors$' || true)
local_bytes=$(du -sb "$DEST" | cut -f1)
src_bytes=$(du -sb "$SRC" | cut -f1)

( cd "$DEST" && find . -type f ! -name '*.sha256' -print0 \
    | sort -z | xargs -0 sha256sum ) > "$DEST/MANIFEST.sha256"

status="pass"
# The full release is 131 shards; source and destination byte totals must agree.
[ "$local_shards" -ge 131 ] || status="fail"
[ "$local_bytes" -ge "$src_bytes" ] || status="fail"

if [ -n "$RECEIPT" ]; then
  python3 - "$RECEIPT" "$status" "$local_shards" "$local_bytes" <<'PY'
import json, sys, time
path, status, shards, bytes_ = sys.argv[1:5]
json.dump({"step": "weights-" + __import__("socket").gethostname(),
           "status": status, "ts": time.strftime("%FT%T"),
           "data": {"shards": int(shards), "bytes": int(bytes_)}},
          open(path, "w"), indent=1)
PY
fi
echo "stage-weights: $status (${local_shards} shards, ${local_bytes} bytes)"
[ "$status" = "pass" ]
