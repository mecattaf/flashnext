#!/usr/bin/env bash
# host/fn-image-ship.sh — ensure the worker carries the serve image.
#
# The worker node carries ZERO podman images (measured 2026-08-30): nothing
# in the estate ships flashnext:dev across, so without this step cp-tp2 dies
# at the worker-container step. Idempotent by image Id compare — a re-run
# against a current worker is a no-op. The ~18 GB transfer rides the WIRE
# (FN_WORKER_HOST is the fleet identity on the 5GbE cable, never a rail).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=host/fn-env.sh
source "$SCRIPT_DIR/fn-env.sh"

log() { echo "fn-image-ship: $*" >&2; }

local_id="$(podman image inspect --format '{{.Id}}' "$FN_IMAGE")"
remote_id="$(ssh "$FN_WORKER_HOST" \
  "podman image inspect --format '{{.Id}}' '$FN_IMAGE' 2>/dev/null" || true)"

if [ -n "$remote_id" ] && [ "$remote_id" = "$local_id" ]; then
  log "worker image current ($FN_IMAGE @ ${local_id:0:19}); nothing to ship"
  exit 0
fi

log "shipping $FN_IMAGE to the worker over the wire (~18 GB; worker has '${remote_id:-none}')"
podman save "$FN_IMAGE" | ssh "$FN_WORKER_HOST" 'podman load'

remote_id="$(ssh "$FN_WORKER_HOST" \
  "podman image inspect --format '{{.Id}}' '$FN_IMAGE'")"
if [ "$remote_id" != "$local_id" ]; then
  log "FATAL: worker image Id mismatch after ship (local ${local_id:0:19}, worker ${remote_id:0:19})"
  exit 1
fi
log "worker image verified ($FN_IMAGE @ ${remote_id:0:19})"
