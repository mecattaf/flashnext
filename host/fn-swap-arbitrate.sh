#!/usr/bin/env bash
# host/fn-swap-arbitrate.sh — arbitrate the local model-swap proxy against the
# pair serve, on BOTH twins.
#
# THE RACE (newly identified, RUN3-BRIEF §15.4). The always-on Go proxy that
# fronts the single-node model pool listens on :9292 and holds NO GPU at rest
# (measured idle on this stack: empty /running, 525 MB VRAM — that is the
# desktop). But it spawns a backend process on ANY request to that port, and
# the port has three live doors: the tailnet, the house LAN, and a local
# utility-model wrapper. A swap-in landing mid-serve allocates out of the same
# 125 GB unified pool the serve is already holding at ~78 GiB/rank, and the
# two systems were mutually blind: fn-cluster-up.sh carried zero references to
# that proxy or to its port, and the proxy carries none to us. NOTHING
# ARBITRATED IT. This script is that arbitration.
#
# Doctrine:
#   * A STOP, NEVER A DISABLE. The unit stays enabled; the morning boots into
#     its normal roster whether or not anybody reads the ledger. Nothing here
#     runs `systemctl disable`, `mask`, or edits a unit file.
#   * ARRIVAL STATE IS RECORDED, and the teardown restores exactly that — a
#     proxy that was already down when we arrived is left down. The record
#     lands in $FN_STATE_DIR/swap-arbitration.json and is folded into the tp2
#     receipt by scripts/run-tp2.sh, so the morning can tell whether the night
#     took the roster down or found it down.
#   * THE UNIT NAME IS RESOLVED, NEVER HARDCODED. Two resolutions, in order of
#     authority: whoever actually holds the port, then a unit-file scan of
#     `systemctl list-units '*swap*'` narrowed to services whose fragment
#     names the port. The bare glob is NOT enough on its own — on this fleet
#     it also matches every kernel `.swap` device unit, `swap.target`, and a
#     `failure-notify@` template instance.
#   * FAIL CLOSED ON THE STOP, EXIT 0 ON THE RESTORE. A proxy we could not
#     stop is the race itself, and `stop` refuses to let the serve start over
#     it. A restore that cannot reach a twin is logged and swallowed: teardown
#     must always be able to complete (fn-cluster-down.sh contract).
#
# Usage:
#   fn-swap-arbitrate.sh probe     # print the resolved unit + both arrivals
#   fn-swap-arbitrate.sh stop      # record arrival, stop on both twins
#   fn-swap-arbitrate.sh restore   # put both twins back to their arrival state
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=host/fn-env.sh
source "$SCRIPT_DIR/fn-env.sh" >/dev/null 2>&1 || true
: "${FN_WORKER_HOST:=10.99.9.2}"
: "${FN_STATE_DIR:=$HOME/.local/state/flashnext}"
# The port is the identity here, not the unit name: the unit is resolved from
# whoever holds :9292 (or from the fragment that declares it).
FN_SWAP_PORT="${FN_SWAP_PORT:-9292}"

log() { echo "fn-swap-arbitrate: $*" >&2; }
worker() { ssh -o BatchMode=yes -o ConnectTimeout=10 "$FN_WORKER_HOST" "$@"; }

STATE_FILE="$FN_STATE_DIR/swap-arbitration.json"

# --- the node-side half (shipped to the worker verbatim by `declare -f`) ------
# Everything below runs on whichever node is asked, host-side, stdlib tools
# only. It prints ONE line: "<unit>|<active-state>|<how-it-was-resolved>".

fn_swap_unit_of_port() {  # $1 = port; the authoritative resolution
  local port="$1" pid line unit
  # ss is the only listener probe present on both twins by default. -H drops
  # the header; the users:(("cmd",pid=N,fd=M)) field carries the pid.
  line="$(ss -ltnpH "sport = :$port" 2>/dev/null | head -n1)" || true
  pid="$(printf '%s' "$line" | grep -oE 'pid=[0-9]+' | head -n1 | cut -d= -f2)"
  [ -n "$pid" ] || return 0
  # A systemd-managed listener names its unit in its own cgroup path.
  unit="$(sed -n 's#.*/\([^/]*\.service\)$#\1#p' "/proc/$pid/cgroup" 2>/dev/null | head -n1)"
  printf '%s' "$unit"
}

fn_swap_unit_by_scan() {  # $1 = port; the fallback when the port is quiet
  local port="$1" candidate names
  # `systemctl list-units '*swap*'` alone is a trap: on this fleet it returns
  # the kernel swap DEVICE units (dev-zram0.swap and friends), swap.target,
  # and a failure-notify@ template instance alongside the real proxy. Narrow
  # to plain (non-template) .service units, then require the fragment to
  # actually declare the port — that is what makes it OUR proxy and not some
  # other unit that happens to have "swap" in its name.
  names="$( { systemctl list-units '*swap*' --all --plain --no-legend 2>/dev/null | awk '{print $1}'
              systemctl list-unit-files '*swap*' --no-legend 2>/dev/null | awk '{print $1}'
            } | grep -E '\.service$' | grep -v '@' | LC_ALL=C sort -u )"
  for candidate in $names; do
    if systemctl cat "$candidate" 2>/dev/null | grep -q "$port"; then
      printf '%s' "$candidate"
      return 0
    fi
  done
}

fn_swap_probe() {  # $1 = port, $2 = optional unit override
  local port="$1" override="${2:-}" unit="" how="" state
  if [ -n "$override" ]; then
    unit="$override"; how="override"
  else
    unit="$(fn_swap_unit_of_port "$port")"
    [ -n "$unit" ] && how="port-holder"
  fi
  if [ -z "$unit" ]; then
    unit="$(fn_swap_unit_by_scan "$port")"
    [ -n "$unit" ] && how="unit-scan"
  fi
  if [ -z "$unit" ]; then
    printf 'none|absent|unresolved'
    return 0
  fi
  state="$(systemctl is-active "$unit" 2>/dev/null || true)"
  printf '%s|%s|%s' "$unit" "${state:-unknown}" "$how"
}

fn_swap_act() {  # $1 = stop|start, $2 = unit; prints "<verb-result>|<state-after>"
  local verb="$1" unit="$2" rc=ok
  if ! systemctl "$verb" "$unit" >/dev/null 2>&1; then
    # The proxy is a SYSTEM unit; an unprivileged overnight actor needs the
    # non-interactive sudo the fleet already grants (same shape as
    # fn-preflight.sh's /dev/cpu_dma_latency read).
    sudo -n systemctl "$verb" "$unit" >/dev/null 2>&1 || rc=denied
  fi
  printf '%s|%s' "$rc" "$(systemctl is-active "$unit" 2>/dev/null || true)"
}

# --- the coordinator-side half -------------------------------------------------
NODE_FUNCS="$(declare -f fn_swap_unit_of_port fn_swap_unit_by_scan fn_swap_probe fn_swap_act)"

probe_node() {  # $1 = coordinator|worker
  if [ "$1" = coordinator ]; then
    fn_swap_probe "$FN_SWAP_PORT" "${FN_SWAP_UNIT:-}"
  else
    worker "$NODE_FUNCS; fn_swap_probe '$FN_SWAP_PORT' '${FN_SWAP_UNIT:-}'" 2>/dev/null \
      || printf 'none|unreachable|unresolved'
  fi
}

act_node() {  # $1 = coordinator|worker, $2 = stop|start, $3 = unit
  if [ "$1" = coordinator ]; then
    fn_swap_act "$2" "$3"
  else
    worker "$NODE_FUNCS; fn_swap_act '$2' '$3'" 2>/dev/null || printf 'unreachable|unknown'
  fi
}

write_state() {  # $1..$n = key=value pairs, JSON-encoded by python3
  mkdir -p "$FN_STATE_DIR"
  python3 - "$STATE_FILE" "$@" <<'PY'
import json, sys
path, pairs = sys.argv[1], sys.argv[2:]
try:
    with open(path) as fh:
        doc = json.load(fh)
except Exception:
    doc = {}
for pair in pairs:
    key, _, value = pair.partition("=")
    node, _, field = key.partition(".")
    if field:
        doc.setdefault(node, {})[field] = value
    else:
        doc[key] = value
with open(path, "w") as fh:
    json.dump(doc, fh, indent=1, sort_keys=True)
PY
}

cmd_probe() {
  local node probe
  for node in coordinator worker; do
    probe="$(probe_node "$node")"
    log "$node: unit='${probe%%|*}' state='$(echo "$probe" | cut -d'|' -f2)' resolved-by='${probe##*|}'"
  done
}

cmd_stop() {
  local node probe unit state how result after failures=0 args=()
  args+=("port=$FN_SWAP_PORT")
  for node in coordinator worker; do
    probe="$(probe_node "$node")"
    unit="${probe%%|*}"; state="$(echo "$probe" | cut -d'|' -f2)"; how="${probe##*|}"
    args+=("$node.unit=$unit" "$node.arrival=$state" "$node.resolved_by=$how")
    case "$state" in
      absent)
        log "$node: no model-swap proxy unit declares :$FN_SWAP_PORT; nothing to arbitrate"
        args+=("$node.action=none")
        continue ;;
      unreachable)
        log "FATAL: $node is unreachable; cannot prove the model-swap proxy is not about to swap in under the serve"
        args+=("$node.action=unreachable")
        failures=$((failures + 1))
        continue ;;
      active)
        log "$node: '$unit' is ACTIVE on arrival (resolved by $how) — stopping it for the serve (stop, never disable)"
        result="$(act_node "$node" stop "$unit")"
        after="${result##*|}"
        args+=("$node.action=stopped" "$node.stop_result=${result%%|*}" "$node.state_after=$after")
        if [ "$after" = active ]; then
          log "FATAL: '$unit' is STILL active on $node after the stop (${result%%|*}); a swap-in mid-serve allocates out of the same unified pool"
          failures=$((failures + 1))
        fi ;;
      *)
        # Inactive/failed/unknown: the port is not being served, so no request
        # can spawn a backend. Record it and leave it alone — restoring an
        # arrival state of "down" means leaving it down.
        log "$node: '$unit' is '$state' on arrival (resolved by $how); nothing to stop, nothing to restore"
        args+=("$node.action=none") ;;
    esac
  done
  write_state "${args[@]}"
  if [ "$failures" -ne 0 ]; then
    log "FATAL: model-swap arbitration failed on $failures node(s); refusing to serve into an unarbitrated pool"
    return 1
  fi
  log "arbitration complete on both twins; record: $STATE_FILE"
}

cmd_restore() {
  local node unit arrival action result
  if [ ! -r "$STATE_FILE" ]; then
    log "no arbitration record at $STATE_FILE; nothing to restore"
    return 0
  fi
  for node in coordinator worker; do
    unit="$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1])).get(sys.argv[2],{}).get("unit",""))' "$STATE_FILE" "$node" 2>/dev/null)"
    arrival="$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1])).get(sys.argv[2],{}).get("arrival",""))' "$STATE_FILE" "$node" 2>/dev/null)"
    action="$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1])).get(sys.argv[2],{}).get("action",""))' "$STATE_FILE" "$node" 2>/dev/null)"
    if [ "$action" != stopped ] || [ -z "$unit" ] || [ "$unit" = none ]; then
      log "$node: arrival was '${arrival:-unknown}'; leaving the model-swap proxy as we found it"
      write_state "$node.restored=not-needed" || true
      continue
    fi
    log "$node: restarting '$unit' (it was $arrival when we arrived)"
    result="$(act_node "$node" start "$unit")"
    write_state "$node.restored=${result##*|}" || true
    if [ "${result##*|}" != active ]; then
      log "WARNING: '$unit' did not come back up on $node (${result%%|*}); the morning roster is DOWN — see docs/MORNING.md"
    fi
  done
  return 0
}

case "${1:-probe}" in
  probe)   cmd_probe ;;
  stop)    cmd_stop ;;
  restore) cmd_restore ;;
  *)       log "usage: $(basename "$0") {probe|stop|restore}"; exit 2 ;;
esac
