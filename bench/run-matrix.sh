#!/usr/bin/env bash
# bench/run-matrix.sh — the counterbalanced flashnext bench matrix.
#
# Spec claim 6.3 + ruling P12 (specs/flashnext/spec.md): three loads per arm,
# interleaved (counterbalanced) arms, medians, token fingerprints, depth series
# 0 / 10240 / 102400, speculative-decoding on and off arms, rows committed
# under results/, and a bench.json receipt the receipts gate grades.
#
# Doctrine this matrix is built to make true:
#   * F.8 — no benchmark number from a single uncounterbalanced run. The two
#     speculative arms are interleaved A-B-B-A (the leading arm alternates per
#     load), never blocked, so a time-drift confound cannot masquerade as an
#     arm effect.
#   * "A matrix whose every number carries its protocol" — every CSV row names
#     its arm, load, depth, and in-flight concurrency; the receipt re-states the
#     whole schedule.
#   * Steering (flashnext#4): gfx1151 multi-sequence gathers can produce wrong
#     outputs. So (1) every row records its concurrency, and (2) if a given
#     (arm, depth) yields divergent completion fingerprints across its loads,
#     the harness replays that arm serially (concurrency=1), byte-compares the
#     token sequences via their fingerprints, and flags the arm SUSPECT instead
#     of averaging over the divergence. Divergent-at-concurrency>1 with a clean
#     serial replay is the QSA-gather signature: it is recorded, never papered
#     over.
#
# The client (bench/fn-stream-client.py) supplies per-request TTFT and the
# separated queue/prefill columns; this script owns the arm schedule, the serve
# reconfiguration between speculative arms, the median reduction, and the
# receipt.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
HOST_DIR="$REPO_ROOT/host"
CLIENT="$SCRIPT_DIR/fn-stream-client.py"
RESULTS="$REPO_ROOT/results"
BENCH_DIR="$RESULTS/bench"
RECEIPTS="$RESULTS/receipts"

# shellcheck source=host/fn-env.sh
source "$HOST_DIR/fn-env.sh"

log() { echo "run-matrix: $*" >&2; }

# --- protocol knobs (all override-able via the environment) ------------------
LOADS_PER_ARM="${FN_BENCH_LOADS_PER_ARM:-3}"
CONCURRENCY="${FN_BENCH_CONCURRENCY:-1}"
REQUESTS_PER_LOAD="${FN_BENCH_REQUESTS:-8}"
# Deep-cell trim: 48 × 102400-token prefills was the night budget's biggest
# single line; the deep depth carries fewer requests per load.
REQUESTS_PER_LOAD_DEEP="${FN_BENCH_REQUESTS_DEEP:-4}"
MAX_TOKENS="${FN_BENCH_MAX_TOKENS:-128}"
DEPTHS=(0 10240 102400)
API="${FN_API:-http://127.0.0.1:$FN_PORT}"
MODEL="${FN_SERVED_NAME:-flashnext}"

# Speculative arm's extra `vllm serve` arguments. The workload's own
# multi-token-prediction head ships IN-CHECKPOINT (3,101 mtp.* tensors,
# ~2.5 GiB, sharded under TP=2 — repo issue 1's closure verified against the
# staged shard index), so the default arm is the native head at n=3; the
# depth sweep n=1..4 is a morning-tuning surface (UNKNOWN-5), and the n-gram
# drafter remains the recorded fallback via FN_BENCH_SPEC_ARGS. The JSON is
# written WITHOUT whitespace and kept single-quoted so it survives the
# `bash -c` re-parse in the container as one argument.
SPEC_ON_EXTRA="${FN_BENCH_SPEC_ARGS:---speculative-config '{\"method\":\"mtp\",\"num_speculative_tokens\":3}'}"

mkdir -p "$BENCH_DIR" "$RECEIPTS"
MATRIX_CSV="$BENCH_DIR/matrix.csv"
MEDIANS_CSV="$BENCH_DIR/medians.csv"
DIVERGENT_TXT="$BENCH_DIR/divergent.txt"
SERIAL_JSONL="$BENCH_DIR/serial-replay.jsonl"
SUMMARIES_JSONL="$BENCH_DIR/load-summaries.jsonl"
rm -f "$MATRIX_CSV" "$MEDIANS_CSV" "$DIVERGENT_TXT" "$SERIAL_JSONL" "$SUMMARIES_JSONL" \
      "$BENCH_DIR/acceptance.log" "$BENCH_DIR/spec-on-failure.log"

# --- the counterbalanced schedule: arms interleaved A-B-B-A -------------------
# For each load index i, odd i leads arm A then B, even i leads B then A. With
# three loads the run order is A B B A A B — the leading arm alternates, which
# is the counterbalance; positions 1..4 are literally A-B-B-A.
SCHED_ARM=()
SCHED_LOAD=()
for ((i = 1; i <= LOADS_PER_ARM; i++)); do
  if (( i % 2 == 1 )); then
    SCHED_ARM+=(spec_off spec_on); SCHED_LOAD+=("$i" "$i")
  else
    SCHED_ARM+=(spec_on spec_off); SCHED_LOAD+=("$i" "$i")
  fi
done
SCHEDULE_DESC=""
for idx in "${!SCHED_ARM[@]}"; do
  SCHEDULE_DESC+="${SCHED_ARM[$idx]}:${SCHED_LOAD[$idx]} "
done
log "counterbalanced schedule: ${SCHEDULE_DESC% }"

arm_label() { case "$1" in spec_on) echo "spec-on" ;; *) echo "spec-off" ;; esac; }

# --- make sure the pair is answering -----------------------------------------
ensure_pair_up() {
  if curl -fsS "$API/v1/models" 2>/dev/null | grep -q "\"$MODEL\""; then
    log "pair already answering '$MODEL' on $API"
    return 0
  fi
  log "pair not answering; standing it up via host/fn-cluster-up.sh"
  bash "$HOST_DIR/fn-cluster-up.sh"
}

# --- serve reconfiguration between speculative arms --------------------------
CURRENT_ARM=""
if [ "${FN_BENCH_FORCE_RECONFIG:-0}" != "1" ]; then
  # fn-cluster-up / first light serve the baseline (non-speculative) config,
  # so a freshly-answered pair is arm spec_off. Set FN_BENCH_FORCE_RECONFIG=1
  # if a prior run may have left the serve in a different arm.
  CURRENT_ARM="spec_off"
fi

# wait_ready RETURNS non-zero instead of exiting: the phase loops own the
# recovery decision (a spec_on boot failure degrades the matrix to a single
# honest arm; a spec_off failure is fatal because the baseline must exist).
# No call from inside wait_ready back into serve_arm — recovery lives in the
# phase loops behind a one-shot guard, so there is no mutual recursion.
wait_ready() {
  local attempts=$(( ${FN_SERVE_TIMEOUT:-2700} / 20 )) ready=0 i
  for ((i = 0; i < attempts; i++)); do
    if curl -fsS "$API/v1/models" 2>/dev/null | grep -q "\"$MODEL\""; then
      ready=1; break
    fi
    sleep 20
  done
  if [ "$ready" -ne 1 ]; then
    log "serve did not answer for arm '$CURRENT_ARM' inside the timeout"
    tail -n 40 "$FN_STATE_DIR/serve.log" >&2 || true
    return 1
  fi
  return 0
}

# Cross-node reap + residue gate per arm flip. The in-container pkill pattern
# never matches the WORKER's ray actor, and a rank still holding 60–100 GiB
# of GTT OOMs the next arm's boot — so after killing the API server we reap
# host-side on BOTH nodes (reap_serve_node from fn-env.sh), then poll ray
# until it reports every GPU released, before the next serve is launched.
reap_arm_residue() {
  reap_serve_node >/dev/null || true
  ssh "$FN_WORKER_HOST" "$(declare -f reap_serve_node); reap_serve_node" >/dev/null || true
  local i used total
  for ((i = 0; i < 60; i++)); do
    read -r used total < <(podman exec "$FN_CONTAINER" ray status 2>/dev/null \
      | awk '$2 == "GPU" { split($1, a, "/"); print a[1], a[2]; exit }') || true
    if [ "${used:-1}" = "0.0" ] || [ "${used:-1}" = "0" ]; then
      return 0
    fi
    sleep 2
  done
  log "ray still holds GPU after reap (${used:-?}/${total:-?}); force-killing ray workers on both nodes"
  podman exec "$FN_CONTAINER" bash -c "pkill -f 'ray::[R]ayWorkerWrapper' || true"
  ssh "$FN_WORKER_HOST" "pkill -f 'ray::[R]ayWorkerWrapper' || true"
  for ((i = 0; i < 15; i++)); do
    read -r used total < <(podman exec "$FN_CONTAINER" ray status 2>/dev/null \
      | awk '$2 == "GPU" { split($1, a, "/"); print a[1], a[2]; exit }') || true
    if [ "${used:-1}" = "0.0" ] || [ "${used:-1}" = "0" ]; then
      return 0
    fi
    sleep 2
  done
  log "FATAL: a rank still holds GPU after force-kill; refusing to race a stranded rank into the next arm"
  exit 1
}

# Serve pins mirror host/fn-cluster-up.sh (rationale comments live there and
# stay OUT of the continued command below — a '#' inside it silently comments
# out every remaining argument):
#   * no plain-eager flag: fn-env's VLLM_PLE_MMAP=1 + the fork's
#     check_cudagraph_safety guard REFUSE it — the serve must run
#     VLLM_COMPILE + PIECEWISE, the mode the successful proxy boot used;
#   * --limit-mm-per-prompt image/video 0: text-only serve; the vision
#     encoder profiling pass materializes 256 GiB on gfx1151 otherwise;
#   * --max-num-batched-tokens: QSA indexer workspace bound;
#   * --kv-cache-memory-bytes / --max-num-seqs: the P11 residency budget
#     (GDN slot pool + paged KV pinned, not floating).
serve_arm() {
  local arm="$1"
  if [ "$CURRENT_ARM" = "$arm" ]; then
    return 0
  fi
  log "reconfiguring serve for arm '$arm'"
  podman exec "$FN_CONTAINER" bash -c "pkill -TERM -f 'bin/[v]llm serve' || true"
  sleep 4
  podman exec "$FN_CONTAINER" bash -c "pkill -KILL -f 'bin/[v]llm serve' || true" 2>/dev/null || true
  reap_arm_residue

  local spec_args=""
  if [ "$arm" = "spec_on" ]; then
    spec_args="$SPEC_ON_EXTRA"
  fi
  local serve_cmd="exec vllm serve $FN_MODEL_DIR"
  serve_cmd+=" --served-model-name $FN_SERVED_NAME"
  serve_cmd+=" --host 0.0.0.0 --port $FN_PORT"
  serve_cmd+=" --tensor-parallel-size 2"
  serve_cmd+=" --distributed-executor-backend ray"
  serve_cmd+=" --gpu-memory-utilization $FN_GPU_UTIL"
  serve_cmd+=" --max-model-len $FN_MAX_CTX"
  serve_cmd+=" --limit-mm-per-prompt '{\"image\":0,\"video\":0}'"
  serve_cmd+=" --max-num-batched-tokens ${FN_MAX_BATCHED_TOKENS:-2048}"
  serve_cmd+=" --kv-cache-memory-bytes ${FN_KV_CACHE_BYTES:-12884901888}"
  serve_cmd+=" --max-num-seqs ${FN_MAX_SEQS:-32}"
  if [ -n "$spec_args" ]; then
    serve_cmd+=" $spec_args"
  fi
  serve_cmd+=" > '$FN_STATE_DIR/serve.log' 2>&1"
  podman exec -d "$FN_CONTAINER" bash -c "$serve_cmd"

  CURRENT_ARM="$arm"
  wait_ready || return 1
  log "arm '$arm' is serving"
}

# --- one measured cell: the client at (arm, load, depth) ----------------------
measure_cell() {  # $1=arm $2=load $3=depth
  local arm="$1" load="$2" depth="$3" label requests
  label="$(arm_label "$arm")"
  requests="$REQUESTS_PER_LOAD"
  if [ "$depth" -ge 102400 ]; then
    requests="$REQUESTS_PER_LOAD_DEEP"
  fi
  log "measure arm=$label load=$load depth=$depth concurrency=$CONCURRENCY requests=$requests"
  python3 "$CLIENT" \
    --api "$API" \
    --model "$MODEL" \
    --arm "$label" \
    --load "$load" \
    --depth "$depth" \
    --concurrency "$CONCURRENCY" \
    --requests "$requests" \
    --max-tokens "$MAX_TOKENS" \
    --spec-label "$label" \
    --csv "$MATRIX_CSV" \
    >> "$SUMMARIES_JSONL"
}

# Degrade-not-die, one-shot: the first spec_on serve failure banks the serve
# log, flips SPEC_ON_DEAD, restores the baseline FROM THE LOOP (never from
# inside wait_ready — no recursion), and every later spec_on cell in both
# phases is skipped. A spec_off failure stays fatal: the baseline must exist.
SPEC_ON_DEAD=0
degrade_spec_on() {
  SPEC_ON_DEAD=1
  tail -n 200 "$FN_STATE_DIR/serve.log" > "$BENCH_DIR/spec-on-failure.log" 2>/dev/null || true
  log "spec_on serve FAILED; degrading to a single-arm matrix (log: $BENCH_DIR/spec-on-failure.log)"
  CURRENT_ARM=""
  serve_arm spec_off
}

# =============================================================================
# Phase A — the counterbalanced measurement sweep.
# =============================================================================
ensure_pair_up
for idx in "${!SCHED_ARM[@]}"; do
  arm="${SCHED_ARM[$idx]}"
  load="${SCHED_LOAD[$idx]}"
  if [ "$arm" = "spec_on" ] && [ "$SPEC_ON_DEAD" = "1" ]; then
    log "skipping spec_on load $load (spec-on serve is dead; single-arm matrix)"
    continue
  fi
  if ! serve_arm "$arm"; then
    if [ "$arm" = "spec_on" ]; then
      degrade_spec_on
      continue
    fi
    log "FATAL: baseline arm '$arm' failed to serve"
    exit 1
  fi
  for depth in "${DEPTHS[@]}"; do
    measure_cell "$arm" "$load" "$depth"
  done
  if [ "$arm" = "spec_on" ]; then
    # Acceptance telemetry: the engine logs draft acceptance on its own
    # cadence; bank every line so Phase D can embed the deduplicated set.
    grep -hi 'acceptance' "$FN_STATE_DIR/serve.log" >> "$BENCH_DIR/acceptance.log" || true
  fi
done

# INTERIM RECEIPT: bank a graded bench.json immediately after the sweep, so a
# runtimeMaxSec kill during reduction/replay still leaves receipts. Phase D
# overwrites it with the full record.
export RECEIPTS SUMMARIES_JSONL LOADS_PER_ARM CONCURRENCY SCHEDULE_DESC SPEC_ON_DEAD
export DEPTH_LIST="${DEPTHS[*]}"
python3 - <<'PY'
import json, os, time
summaries = []
p = os.environ["SUMMARIES_JSONL"]
if os.path.exists(p):
    with open(p) as f:
        summaries = [json.loads(ln) for ln in f if ln.strip()]
arms = sorted({s["arm"] for s in summaries})
loads = {}
for s in summaries:
    loads.setdefault(s["arm"], set()).add(s.get("load"))
loads_per_arm = min((len(v) for v in loads.values()), default=0)
receipt = {
    "step": "bench", "status": "pass", "ts": time.strftime("%FT%T"),
    "data": {
        "interim": True,
        "arms": arms,
        "loads_per_arm": loads_per_arm,
        "counterbalanced": len(arms) == 2,
        "spec_on_failed": os.environ.get("SPEC_ON_DEAD") == "1",
        "depth_series": [int(x) for x in os.environ["DEPTH_LIST"].split()],
        "concurrency": int(os.environ["CONCURRENCY"]),
        "schedule": os.environ["SCHEDULE_DESC"].split(),
        "note": "interim receipt written after Phase A; Phase D overwrites with the full record",
    },
}
path = os.path.join(os.environ["RECEIPTS"], "bench.json")
json.dump(receipt, open(path, "w"), indent=1)
print(f"run-matrix: interim receipt written to {path}", flush=True)
PY

# =============================================================================
# Phase B — median reduction + fingerprint-divergence detection.
# =============================================================================
export MATRIX_CSV MEDIANS_CSV DIVERGENT_CSV="$DIVERGENT_TXT"
python3 - <<'PY'
import csv, os
from statistics import median

matrix_csv = os.environ["MATRIX_CSV"]
medians_csv = os.environ["MEDIANS_CSV"]
divergent_txt = os.environ["DIVERGENT_CSV"]

# Strip the '#' column-semantics comment before handing lines to csv.
with open(matrix_csv) as f:
    lines = [ln for ln in f if not ln.startswith("#")]
rows = list(csv.DictReader(lines))

def med(vals):
    nums = [float(v) for v in vals if v not in ("", None)]
    return median(nums) if nums else ""

cells = {}
for r in rows:
    key = (r["arm"], r["depth_target"])
    cells.setdefault(key, []).append(r)

median_fields = ("ttft_s", "queue_wait_s", "prefill_s", "decode_s", "total_s")
with open(medians_csv, "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["arm", "depth_target", "concurrency", "n_requests",
                *("median_" + x for x in median_fields),
                "distinct_fingerprints", "suspect"])
    divergent = []
    for (arm, depth), rs in sorted(cells.items()):
        fp = set(r["fingerprint"] for r in rs if r["fingerprint"])
        is_suspect = len(fp) > 1
        if is_suspect:
            divergent.append((arm, depth))
        conc = rs[0]["concurrency"] if rs else ""
        w.writerow([arm, depth, conc, len(rs),
                    *(med([r[x] for r in rs]) for x in median_fields),
                    len(fp), "yes" if is_suspect else "no"])

with open(divergent_txt, "w") as f:
    for arm, depth in divergent:
        f.write(f"{arm}\t{depth}\n")

print(f"run-matrix: reduced {len(rows)} rows into {len(cells)} (arm,depth) cells; "
      f"{len(divergent)} divergent", flush=True)
PY

# =============================================================================
# Phase C — serial (concurrency=1) replay of every divergent arm.
# Steering §2: fingerprint divergence at concurrency>1 with a clean serial
# replay is the QSA-gather signature. Record it; never average over it.
# =============================================================================
if [ -s "$DIVERGENT_TXT" ]; then
  log "divergence detected; replaying divergent arms serially (concurrency=1)"
  # Replay each divergent (arm, depth) serially, grouped by arm so the serve is
  # reconfigured at most once per arm. DIVERGENT_TXT carries the CSV arm LABEL
  # (spec-off / spec-on); map it back to the internal arm id for serve_arm.
  mapfile -t uniq_labels < <(cut -f1 "$DIVERGENT_TXT" | awk '!seen[$0]++')
  for label in "${uniq_labels[@]}"; do
    case "$label" in
      spec-on) arm="spec_on" ;;
      *) arm="spec_off" ;;
    esac
    if [ "$arm" = "spec_on" ] && [ "$SPEC_ON_DEAD" = "1" ]; then
      log "skipping serial replay for dead spec_on arm"
      continue
    fi
    mapfile -t label_depths < <(awk -F'\t' -v a="$label" '$1==a {print $2}' "$DIVERGENT_TXT")
    [ "${#label_depths[@]}" -eq 0 ] && continue
    if ! serve_arm "$arm"; then
      if [ "$arm" = "spec_on" ]; then
        degrade_spec_on
        continue
      fi
      log "FATAL: baseline arm failed to serve for serial replay"
      exit 1
    fi
    for depth in "${label_depths[@]}"; do
      log "serial replay arm=$label depth=$depth"
      serial_json="$(python3 "$CLIENT" \
        --api "$API" --model "$MODEL" \
        --arm "$label" --load 0 \
        --depth "$depth" \
        --concurrency 1 \
        --requests "$REQUESTS_PER_LOAD" \
        --max-tokens "$MAX_TOKENS" \
        --spec-label "$label" \
        --csv "$BENCH_DIR/serial-$label-$depth.csv")"
      printf '%s\n' "$serial_json" \
        | python3 -c '
import json, sys
rec = json.loads(sys.stdin.read())
fps = rec.get("fingerprints", [])
distinct = sorted(set(fps))
print(json.dumps({
    "arm": rec.get("arm"),
    "depth_target": rec.get("depth_target"),
    "concurrency": rec.get("concurrency"),
    "serial_clean": len(distinct) <= 1,
    "serial_fingerprint": distinct[0] if distinct else None,
    "serial_distinct_count": len(distinct),
}))' >> "$SERIAL_JSONL"
    done
  done
else
  log "no fingerprint divergence across loads; no serial replay needed"
fi

# =============================================================================
# Phase D — the bench.json receipt the receipts gate grades.
#
# HONESTY RULE (spec F.8): arms, loads_per_arm, and counterbalanced are
# DERIVED from the rows actually measured, never stamped from the design.
# When spec_on died the receipt says so: arms=["spec_off"],
# counterbalanced=false, spec_on_failed=true, with the failure-log path.
#
# TRANSPORT PROVENANCE: this bench.json is the Gate 0 socket-transport
# artifact host/rdma/attended-bringup.md requires ONLY when
# data.transport.fn_transport_rung is rail0-sockets — a wire-fallback rung
# is a 5GbE artifact and does NOT unlock the verbs A/B.
# =============================================================================
TP_IFNAME="$(podman exec "$FN_CONTAINER" printenv NCCL_SOCKET_IFNAME 2>/dev/null || true)"
TP_IBDIS="$(podman exec "$FN_CONTAINER" printenv NCCL_IB_DISABLE 2>/dev/null || true)"
TP_RUNG="$(podman exec "$FN_CONTAINER" printenv FN_TRANSPORT_RUNG 2>/dev/null || true)"
export RECEIPTS BENCH_DIR SERIAL_JSONL SUMMARIES_JSONL DIVERGENT_TXT
export LOADS_PER_ARM CONCURRENCY SCHEDULE_DESC SPEC_ON_DEAD
export TP_IFNAME TP_IBDIS TP_RUNG
export DEPTH_LIST="${DEPTHS[*]}"
python3 - <<'PY'
import csv, json, os, time

bench_dir = os.environ["BENCH_DIR"]
receipts = os.environ["RECEIPTS"]
concurrency = int(os.environ["CONCURRENCY"])
schedule = os.environ["SCHEDULE_DESC"].split()
depths = [int(x) for x in os.environ["DEPTH_LIST"].split()]
spec_on_failed = os.environ.get("SPEC_ON_DEAD") == "1"

def read_jsonl(path):
    if not os.path.exists(path):
        return []
    out = []
    with open(path) as f:
        for ln in f:
            ln = ln.strip()
            if ln:
                out.append(json.loads(ln))
    return out

summaries = read_jsonl(os.environ["SUMMARIES_JSONL"])
serials = read_jsonl(os.environ["SERIAL_JSONL"])
serial_by_key = {(s.get("arm"), str(s.get("depth_target"))): s for s in serials}

# Per (arm, depth) fingerprint sets from the measured loads.
fingerprints = {}
suspect_cells = []
for s in summaries:
    key = f"{s['arm']}/{s['depth_target']}"
    entry = fingerprints.setdefault(key, {"fingerprints": set(), "loads": set(),
                                          "concurrency": s.get("concurrency")})
    entry["fingerprints"].update(s.get("fingerprints", []))
    entry["loads"].add(s.get("load"))

for key, entry in fingerprints.items():
    arm, depth = key.split("/", 1)
    distinct = sorted(entry["fingerprints"])
    divergent = len(distinct) > 1
    serial = serial_by_key.get((arm, depth))
    serial_clean = serial.get("serial_clean") if serial else None
    # Suspect when loads diverge. A clean serial replay alongside concurrent
    # divergence is precisely the QSA-gather signature: recorded, not averaged.
    suspect = divergent
    qsa_signature = bool(divergent and serial_clean)
    entry["distinct_count"] = len(distinct)
    entry["divergent"] = divergent
    entry["suspect"] = suspect
    entry["qsa_signature"] = qsa_signature
    entry["serial_clean"] = serial_clean
    entry["serial_fingerprint"] = serial.get("serial_fingerprint") if serial else None
    entry["fingerprints"] = distinct
    entry["loads"] = sorted(entry["loads"])
    if suspect:
        suspect_cells.append(key)

# --- derived schedule facts: what the run actually measured -------------------
measured_arms = sorted({s["arm"] for s in summaries})
loads_by_arm = {}
for s in summaries:
    loads_by_arm.setdefault(s["arm"], set()).add(s.get("load"))
measured_loads_per_arm = min((len(v) for v in loads_by_arm.values()), default=0)
counterbalanced = len(measured_arms) == 2

# --- identity oracle: per-depth cross-arm fingerprint-set equality ------------
# Spec-on numbers must not be quoted on a dirty oracle: greedy spec output
# must match plain decode. Dirty oracle + clean per-arm serial replay is the
# known QSA-gather signature vs a real spec-decode divergence — a morning
# read, deliberately not an overnight gate.
by_arm_depth = {}
for s in summaries:
    by_arm_depth.setdefault(str(s["depth_target"]), {}).setdefault(
        s["arm"], set()).update(s.get("fingerprints", []))
identity_oracle = {}
for depth, arm_fps in by_arm_depth.items():
    if len(arm_fps) == 2:
        a, b = sorted(arm_fps)
        identity_oracle[depth] = {"cross_arm_equal": arm_fps[a] == arm_fps[b]}
identity_oracle_clean = all(v["cross_arm_equal"] for v in identity_oracle.values()) \
    if identity_oracle else None

# --- acceptance telemetry from the spec-on serve logs -------------------------
acceptance = []
acc_path = os.path.join(bench_dir, "acceptance.log")
if os.path.exists(acc_path):
    seen = set()
    for ln in open(acc_path, errors="ignore"):
        ln = ln.strip()
        if ln and ln not in seen:
            seen.add(ln)
            acceptance.append(ln)
acceptance = acceptance[:64]

data = {
    "loads_per_arm": measured_loads_per_arm,
    "counterbalanced": counterbalanced,
    "arms": measured_arms,
    "spec_on_failed": spec_on_failed,
    "depth_series": depths,
    "concurrency": concurrency,
    "schedule": schedule,
    "fingerprints": fingerprints,
    "suspect_cells": suspect_cells,
    "identity_oracle": identity_oracle,
    "identity_oracle_clean": identity_oracle_clean,
    "acceptance_telemetry": acceptance,
    "transport": {
        "nccl_socket_ifname": os.environ.get("TP_IFNAME", ""),
        "nccl_ib_disable": os.environ.get("TP_IBDIS", ""),
        "fn_transport_rung": os.environ.get("TP_RUNG", ""),
    },
    "matrix_csv": "results/bench/matrix.csv",
    "medians_csv": "results/bench/medians.csv",
    "note": ("prefill is a server-metrics measurement, never a duplicate "
             "of the ttft column (spec 6.2; evidence/nix-strix-halo.md "
             "§4.4). Suspect cells diverged across loads and must not be "
             "quoted as clean numbers. Spec-on numbers must not be quoted "
             "on a dirty identity oracle. This receipt satisfies the "
             "rail-sockets Gate 0 of host/rdma/attended-bringup.md ONLY "
             "when transport.fn_transport_rung is rail0-sockets."),
}
if spec_on_failed:
    data["spec_on_failure_log"] = "results/bench/spec-on-failure.log"

receipt = {"step": "bench", "status": "pass", "ts": time.strftime("%FT%T"),
           "data": data}
path = os.path.join(receipts, "bench.json")
with open(path, "w") as f:
    json.dump(receipt, f, indent=1)
print(f"run-matrix: receipt written to {path} "
      f"({len(fingerprints)} cells, {len(suspect_cells)} suspect, "
      f"arms={measured_arms}, spec_on_failed={spec_on_failed})", flush=True)
PY

log "matrix complete: rows=$MATRIX_CSV medians=$MEDIANS_CSV receipt=$RECEIPTS/bench.json"
