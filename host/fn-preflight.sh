#!/usr/bin/env bash
# host/fn-preflight.sh — first-light readiness record for the pair.
#
#   1. byte-diff of both ranks' FN_/doctrine env — the two TP ranks must not
#      diverge (VLLM_RAY_EXTRA_ENV_VAR_PREFIXES_TO_COPY leans on the base
#      doctrine being identical on both nodes);
#   2. latency-hold device read on BOTH ends — /dev/cpu_dma_latency must read
#      0 with the lowlat-cluster unit active (dotfiles-observed.md §1.2);
#   3. per-rail link-speed record on both nodes;
#   4. round-trip probe on both rails from both ends, against the fleet
#      latency budget (200 us default).
#
# Writes results/receipts/preflight.json and exits non-zero on any hard
# failure. Rails without a routable peer IP are recorded as such, never
# failed over — rail 1 is trained-but-IP-unconfigured today.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
# shellcheck source=host/fn-env.sh
source "$SCRIPT_DIR/fn-env.sh"

RECEIPT="$REPO_ROOT/results/receipts/preflight.json"
mkdir -p "$(dirname "$RECEIPT")"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
REPORT="$TMP/report"
: > "$REPORT"

status=pass
note() { echo "fn-preflight: $*" >&2; }
fail() { note "FAIL: $*"; status=fail; }
record() { printf '%s\t%s\n' "$1" "$2" >> "$REPORT"; }

worker() { ssh "$FN_WORKER_HOST" "$@"; }

# --- 1. byte-diff of both ranks' exported doctrine env ------------------------
ENV_FILTER='^(FN_|NCCL_|RAY_|TORCH_NCCL_|VLLM_|PYTHONHASHSEED=|HSA_|PYTORCH_HIP_|TORCHINDUCTOR_|TRITON_|HF_)'
( set -a; source "$SCRIPT_DIR/fn-env.sh" >/dev/null; env ) \
  | grep -E "$ENV_FILTER" | LC_ALL=C sort > "$TMP/env.coordinator"

# The transport decision is made ONCE on the coordinator and injected into
# the worker's sourcing as pre-set literals (mirrors fn-cluster-up.sh): the
# byte-diff then verifies everything else AND that both ranks agree on the
# coordinator-decided transport.
REMOTE_TMP="$(worker 'mktemp -d')"
worker "cat > '$REMOTE_TMP/fn-env.sh'" < "$SCRIPT_DIR/fn-env.sh"
# Separate `export` statements, NOT prefix assignments on `source`: bash
# applies a prefix assignment only for the duration of the builtin and
# RESTORES the prior (unset) state when it returns, undoing the export
# fn-env.sh performs inside. The worker then carried neither variable and
# the byte-diff failed on exactly these two lines.
worker "( set -a; \
    export NCCL_SOCKET_IFNAME='$NCCL_SOCKET_IFNAME'; \
    export FN_TRANSPORT_RUNG='$FN_TRANSPORT_RUNG'; \
    source '$REMOTE_TMP/fn-env.sh' >/dev/null; env ) \
  | grep -E '$ENV_FILTER' | LC_ALL=C sort" > "$TMP/env.worker"

if cmp -s "$TMP/env.coordinator" "$TMP/env.worker"; then
  record "env_diff" "pass"
  note "env byte-diff: both ranks carry identical doctrine env"
else
  record "env_diff" "fail"
  fail "env byte-diff: the ranks' doctrine env diverges"
  diff "$TMP/env.coordinator" "$TMP/env.worker" >&2 || true
fi

# --- 2. latency-hold device read on both ends ---------------------------------
latency_hold_probe() {
  local unit val=""
  unit="$(systemctl is-active lowlat-cluster 2>/dev/null || true)"
  if [ -r /dev/cpu_dma_latency ]; then
    val="$(od -An -td4 /dev/cpu_dma_latency | tr -d ' \n')"
  else
    val="$(sudo -n od -An -td4 /dev/cpu_dma_latency 2>/dev/null | tr -d ' \n' || true)"
  fi
  printf '%s|%s' "$unit" "${val:-unreadable}"
}

check_hold() {  # $1 = node label, $2 = "<unit>|<device-value>"
  local node="$1" probe="$2" unit="${2%%|*}" val="${2##*|}"
  record "hold_$node" "$probe"
  case "$unit" in
    active) ;;
    *) fail "latency hold on $node: lowlat-cluster is '$unit' (must be active)"; return ;;
  esac
  case "$val" in
    0) note "latency hold on $node: held (device reads 0)" ;;
    unreadable) note "latency hold on $node: unit active but /dev/cpu_dma_latency unreadable here" ;;
    *) fail "latency hold on $node: device reads '$val' (held figure is 0)"; return ;;
  esac
}

check_hold coordinator "$(latency_hold_probe)"
check_hold worker "$(worker "$(declare -f latency_hold_probe); latency_hold_probe")"

# --- 3. per-rail link-speed record, both nodes ---------------------------------
rail_speeds() {  # emits "<rail> <speed-Mb/s>|absent" lines for the two rails
  local rail
  for rail in thunderbolt0 thunderbolt1; do
    if [ -r "/sys/class/net/$rail/speed" ]; then
      printf '%s %s\n' "$rail" "$(cat "/sys/class/net/$rail/speed" 2>/dev/null || echo down)"
    else
      printf '%s absent\n' "$rail"
    fi
  done
}
for node in coordinator worker; do
  if [ "$node" = coordinator ]; then
    speeds="$(rail_speeds)"
  else
    speeds="$(worker "$(declare -f rail_speeds); rail_speeds")"
  fi
  while read -r rail speed; do
    record "speed_${node}_${rail}" "$speed"
  done <<< "$speeds"
done

# --- 4. round-trip probe, from both ends ---------------------------------------
# Peer of a /30 endpoint flips the last octet (.1 <-> .2) — this holds on the
# wire too (10.99.1.1 <-> 10.99.1.2). A rail that does not carry a routable
# IP cannot be probed and is recorded as no-peer-ip. Only interfaces LISTED
# in NCCL_SOCKET_IFNAME can fail the preflight: a dark-but-addressed rail
# that fn-env already declined to list must not kill cp-tp2 after a clean
# wire fallback — it is recorded as probe-failed-unlisted instead.
rtt_probe_node() {  # $@ = interfaces to probe
  local rail addr peer rtt
  for rail in "$@"; do
    addr="$(ip -br -4 addr show dev "$rail" 2>/dev/null \
      | grep -oE '([0-9]{1,3}\.){3}[0-9]{1,3}' \
      | grep -Ev '^(169\.254\.|127\.)' | head -n1 || true)"
    if [ -z "$addr" ]; then
      printf '%s no-peer-ip\n' "$rail"
      continue
    fi
    peer="$(printf '%s' "$addr" | awk -F. '{ o=$4; $4=(o==1)?2:1; printf "%d.%d.%d.%d", $1,$2,$3,$4 }')"
    rtt="$(ping -q -c 20 -i 0.05 -W 2 "$peer" 2>/dev/null | tail -n1 \
      | awk -F'[/ ]' '{ printf "%d", $8 * 1000 }' || true)"
    printf '%s %s\n' "$rail" "${rtt:-probe-failed}"
  done
}
is_listed() { case ",$NCCL_SOCKET_IFNAME," in *",$1,"*) return 0 ;; *) return 1 ;; esac; }
PROBE_IFACES="thunderbolt0 thunderbolt1"
if is_listed enp191s0; then
  PROBE_IFACES="$PROBE_IFACES enp191s0"
fi
for node in coordinator worker; do
  if [ "$node" = coordinator ]; then
    rtts="$(rtt_probe_node $PROBE_IFACES)"
  else
    rtts="$(worker "$(declare -f rtt_probe_node); rtt_probe_node $PROBE_IFACES")"
  fi
  while read -r rail rtt; do
    case "$rtt" in
      no-peer-ip)
        record "rtt_us_${node}_${rail}" "$rtt"
        note "rtt on $node/$rail: no routable peer IP, not probed" ;;
      probe-failed|'')
        if is_listed "$rail"; then
          record "rtt_us_${node}_${rail}" "probe-failed"
          fail "rtt on $node/$rail: probe failed on a rail listed in NCCL_SOCKET_IFNAME"
        else
          record "rtt_us_${node}_${rail}" "probe-failed-unlisted"
          note "rtt on $node/$rail: probe failed but the rail is not listed in NCCL_SOCKET_IFNAME; recorded, not failed"
        fi ;;
      *)
        record "rtt_us_${node}_${rail}" "$rtt"
        if [ "$rtt" -gt "$FN_LATENCY_BUDGET_US" ]; then
          fail "rtt on $node/$rail: ${rtt} us exceeds the ${FN_LATENCY_BUDGET_US} us budget"
        else
          note "rtt on $node/$rail: ${rtt} us inside the ${FN_LATENCY_BUDGET_US} us budget"
        fi
        ;;
    esac
  done <<< "$rtts"
done

record "rails_chosen" "$NCCL_SOCKET_IFNAME"
record "transport_rung" "${FN_TRANSPORT_RUNG:-rail0-sockets}"
record "budget_us" "$FN_LATENCY_BUDGET_US"

# --- receipt --------------------------------------------------------------------
# Quarantine on failure (receipt discipline D12): a fail receipt lands under
# results/receipts/failed/ — committed, ledger-reviewed, a typed blocker —
# but outside receipts-verify's grading walk, so one failed preflight cannot
# permanently redden every later gate run.
if [ "$status" != "pass" ]; then
  RECEIPT="$REPO_ROOT/results/receipts/failed/preflight.json"
  mkdir -p "$(dirname "$RECEIPT")"
fi
python3 - "$RECEIPT" "$status" "$REPORT" <<'PY'
import json, sys, time
path, status, report = sys.argv[1:4]
kv = {}
for line in open(report):
    if line.strip():
        k, _, v = line.rstrip("\n").partition("\t")
        kv[k] = v
hold = {}
for n in ("coordinator", "worker"):
    u, _, v = kv.get("hold_" + n, "unknown|unreadable").partition("|")
    hold[n] = {"unit": u, "cpu_dma_latency": v}
rails = {}
for node in ("coordinator", "worker"):
    for rail in ("thunderbolt0", "thunderbolt1", "enp191s0"):
        if f"rtt_us_{node}_{rail}" in kv or f"speed_{node}_{rail}" in kv:
            rails.setdefault(rail, {})[node] = {
                "speed_mbps": kv.get(f"speed_{node}_{rail}"),
                "rtt_us": kv.get(f"rtt_us_{node}_{rail}"),
            }
json.dump({"step": "preflight", "status": status,
           "ts": time.strftime("%FT%T"),
           "data": {"env_byte_diff": kv.get("env_diff"),
                    "rails_chosen": kv.get("rails_chosen", "").split(","),
                    "transport_rung": kv.get("transport_rung", "rail0-sockets"),
                    "latency_budget_us": int(kv.get("budget_us", "200")),
                    "latency_hold": hold,
                    "rails": rails}},
          open(path, "w"), indent=1)
PY
note "receipt: $RECEIPT (status=$status)"
[ "$status" = "pass" ]
