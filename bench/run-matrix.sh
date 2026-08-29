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
MAX_TOKENS="${FN_BENCH_MAX_TOKENS:-128}"
DEPTHS=(0 10240 102400)
API="${FN_API:-http://127.0.0.1:$FN_PORT}"
MODEL="${FN_SERVED_NAME:-flashnext}"

# Speculative arm's extra `vllm serve` arguments. UNKNOWN-5 makes the draft and
# its length a morning-tuning surface, so this is an operator knob with a
# conservative n-gram default. The JSON is written WITHOUT whitespace and kept
# single-quoted so it survives the `bash -c` re-parse in the container as one
# argument.
SPEC_ON_EXTRA="${FN_BENCH_SPEC_ARGS:---speculative-config '{\"method\":\"ngram\",\"num_speculative_tokens\":8,\"prompt_lookup_max\":8,\"prompt_lookup_min\":2}'}"

mkdir -p "$BENCH_DIR" "$RECEIPTS"
MATRIX_CSV="$BENCH_DIR/matrix.csv"
MEDIANS_CSV="$BENCH_DIR/medians.csv"
DIVERGENT_TXT="$BENCH_DIR/divergent.txt"
SERIAL_JSONL="$BENCH_DIR/serial-replay.jsonl"
SUMMARIES_JSONL="$BENCH_DIR/load-summaries.jsonl"
rm -f "$MATRIX_CSV" "$MEDIANS_CSV" "$DIVERGENT_TXT" "$SERIAL_JSONL" "$SUMMARIES_JSONL"

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

wait_ready() {
  local attempts=$(( ${FN_SERVE_TIMEOUT:-2700} / 20 )) ready=0 i
  for ((i = 0; i < attempts; i++)); do
    if curl -fsS "$API/v1/models" 2>/dev/null | grep -q "\"$MODEL\""; then
      ready=1; break
    fi
    sleep 20
  done
  if [ "$ready" -ne 1 ]; then
    log "FATAL: serve did not answer for arm '$CURRENT_ARM' inside the timeout"
    tail -n 40 "$FN_STATE_DIR/serve.log" >&2 || true
    exit 1
  fi
}

serve_arm() {
  local arm="$1"
  if [ "$CURRENT_ARM" = "$arm" ]; then
    return 0
  fi
  log "reconfiguring serve for arm '$arm'"
  podman exec "$FN_CONTAINER" bash -c "pkill -TERM -f 'bin/[v]llm serve' || true"
  sleep 4
  podman exec "$FN_CONTAINER" bash -c "pkill -KILL -f 'bin/[v]llm serve' || true" 2>/dev/null || true

  local spec_args=""
  if [ "$arm" = "spec_on" ]; then
    spec_args="$SPEC_ON_EXTRA"
  fi
  local serve_cmd="exec vllm serve $FN_MODEL_DIR"
  serve_cmd+=" --served-model-name $FN_SERVED_NAME"
  serve_cmd+=" --host 0.0.0.0 --port $FN_PORT"
  serve_cmd+=" --tensor-parallel-size 2"
  serve_cmd+=" --distributed-executor-backend ray"
  serve_cmd+=" --enforce-eager"
  serve_cmd+=" --gpu-memory-utilization $FN_GPU_UTIL"
  serve_cmd+=" --max-model-len $FN_MAX_CTX"
  if [ -n "$spec_args" ]; then
    serve_cmd+=" $spec_args"
  fi
  serve_cmd+=" > '$FN_STATE_DIR/serve.log' 2>&1"
  podman exec -d "$FN_CONTAINER" bash -c "$serve_cmd"

  CURRENT_ARM="$arm"
  wait_ready
  log "arm '$arm' is serving"
}

# --- one measured cell: the client at (arm, load, depth) ----------------------
measure_cell() {  # $1=arm $2=load $3=depth
  local arm="$1" load="$2" depth="$3" label
  label="$(arm_label "$arm")"
  log "measure arm=$label load=$load depth=$depth concurrency=$CONCURRENCY"
  python3 "$CLIENT" \
    --api "$API" \
    --model "$MODEL" \
    --arm "$label" \
    --load "$load" \
    --depth "$depth" \
    --concurrency "$CONCURRENCY" \
    --requests "$REQUESTS_PER_LOAD" \
    --max-tokens "$MAX_TOKENS" \
    --spec-label "$label" \
    --csv "$MATRIX_CSV" \
    >> "$SUMMARIES_JSONL"
}

# =============================================================================
# Phase A — the counterbalanced measurement sweep.
# =============================================================================
ensure_pair_up
for idx in "${!SCHED_ARM[@]}"; do
  arm="${SCHED_ARM[$idx]}"
  load="${SCHED_LOAD[$idx]}"
  serve_arm "$arm"
  for depth in "${DEPTHS[@]}"; do
    measure_cell "$arm" "$load" "$depth"
  done
done

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
    mapfile -t label_depths < <(awk -F'\t' -v a="$label" '$1==a {print $2}' "$DIVERGENT_TXT")
    [ "${#label_depths[@]}" -eq 0 ] && continue
    serve_arm "$arm"
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
# =============================================================================
export RECEIPTS BENCH_DIR SERIAL_JSONL SUMMARIES_JSONL DIVERGENT_TXT
export LOADS_PER_ARM CONCURRENCY SCHEDULE_DESC
export DEPTH_LIST="${DEPTHS[*]}"
export ARM_LIST="spec_off spec_on"
python3 - <<'PY'
import csv, json, os, time

bench_dir = os.environ["BENCH_DIR"]
receipts = os.environ["RECEIPTS"]
loads_per_arm = int(os.environ["LOADS_PER_ARM"])
concurrency = int(os.environ["CONCURRENCY"])
schedule = os.environ["SCHEDULE_DESC"].split()
depths = [int(x) for x in os.environ["DEPTH_LIST"].split()]
arms = os.environ["ARM_LIST"].split()

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

receipt = {
    "step": "bench",
    "status": "pass",
    "ts": time.strftime("%FT%T"),
    "data": {
        "loads_per_arm": loads_per_arm,
        "counterbalanced": True,
        "arms": arms,
        "depth_series": depths,
        "concurrency": concurrency,
        "schedule": schedule,
        "fingerprints": fingerprints,
        "suspect_cells": suspect_cells,
        "matrix_csv": "results/bench/matrix.csv",
        "medians_csv": "results/bench/medians.csv",
        "note": ("prefill is a server-metrics measurement, never a duplicate "
                 "of the ttft column (spec 6.2; evidence/nix-strix-halo.md "
                 "§4.4). Suspect cells diverged across loads and must not be "
                 "quoted as clean numbers."),
    },
}
path = os.path.join(receipts, "bench.json")
with open(path, "w") as f:
    json.dump(receipt, f, indent=1)
print(f"run-matrix: receipt written to {path} "
      f"({len(fingerprints)} cells, {len(suspect_cells)} suspect)", flush=True)
PY

log "matrix complete: rows=$MATRIX_CSV medians=$MEDIANS_CSV receipt=$RECEIPTS/bench.json"
