#!/usr/bin/env bash
# host/fn-cluster-down.sh — idempotent teardown of the flashnext pair service
# on BOTH nodes. This is the ExecStop AND the ExecStopPost of
# flashnext-pair.service: ExecStop does not run when ExecStart itself fails,
# StopPost does, so a failed bring-up still tears the pair down instead of
# leaving the serve process and ray daemons running behind a 'failed'
# wrapper. Tolerates broken config, dead ssh, and half-dead containers.
# ALWAYS exits 0 — a teardown that fails loud is still a teardown the unit
# must be able to complete.
#
# It is also the ONLY place the model-swap proxy on :9292 comes back. The
# bring-up stops that proxy on both twins so a swap-in cannot land mid-serve
# and allocate out of the same unified pool; this file puts it back to its
# ARRIVAL state on every exit path — clean stop, failed bring-up, or crash —
# because StopPost runs when ExecStart itself failed. A proxy that was already
# down when we arrived stays down; nothing here ever disables a unit.
set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Tolerate a broken env file; fall back to the doctrine defaults.
# shellcheck source=host/fn-env.sh
source "$SCRIPT_DIR/fn-env.sh" >/dev/null 2>&1 || true
: "${FN_WORKER_HOST:=10.99.9.2}"
: "${FN_CONTAINER:=flashnext-pair}"

log() { echo "fn-cluster-down: $*" >&2; }
worker() { ssh "$FN_WORKER_HOST" "$@" 2>/dev/null || true; }

# Reap the serve process tree host-side (visible in /proc even from inside a
# container): SIGTERM, a beat, SIGKILL. No gate here — teardown is best-effort.
reap_serve_node() {
  local pids
  pids="$(pgrep -f 'bin/[v]llm serve' || true)"
  if [ -n "$pids" ]; then
    echo "fn-cluster-down: reaping serve pids: $(echo $pids | tr '\n' ' ')" >&2
    kill -TERM $pids 2>/dev/null || true
    sleep 3
    pids="$(pgrep -f 'bin/[v]llm serve' || true)"
    [ -z "$pids" ] || kill -KILL $pids 2>/dev/null || true
  fi
  # Same pipefail-safe residue count as fn-env.sh's copy: teardown does not
  # run under `set -e` today, but the two helpers must not drift into
  # different failure semantics.
  pids="$(pgrep -f 'bin/[v]llm serve' || true)"
  if [ -z "$pids" ]; then echo 0; else echo "$pids" | wc -l; fi
}

log "reap serve: coordinator"
residue="$(reap_serve_node)"
[ "$residue" -eq 0 ] || log "WARNING: $residue serve process(es) survived on the coordinator"
log "reap serve: worker"
residue="$(worker "$(declare -f reap_serve_node); reap_serve_node")"
[ "${residue:-0}" -eq 0 ] || log "WARNING: ${residue:-?} serve process(es) survived on the worker"

log "ray stop: coordinator"
podman exec "$FN_CONTAINER" ray stop --force >/dev/null 2>&1 || true
log "ray stop: worker"
worker "podman exec '$FN_CONTAINER' ray stop --force >/dev/null 2>&1 || true"

log "container removal: coordinator"
podman rm -f "$FN_CONTAINER" >/dev/null 2>&1 || true
log "container removal: worker"
worker "podman rm -f '$FN_CONTAINER' >/dev/null 2>&1 || true"

# --- restore the model-swap proxy, last, on every exit path -------------------
# LAST on purpose: the containers are gone and the ranks have released the
# unified pool, so the proxy comes back to a machine that can actually serve
# a swap-in. fn-swap-arbitrate.sh reads the arrival record the bring-up wrote
# and starts the unit only on the twins where it was running when we arrived;
# it always exits 0, so it can never block this teardown from completing.
log "restore: model-swap proxy on :${FN_SWAP_PORT:-9292} to its arrival state, both twins"
bash "$SCRIPT_DIR/fn-swap-arbitrate.sh" restore || true

log "teardown complete"
exit 0
