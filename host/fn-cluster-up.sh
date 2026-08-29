#!/usr/bin/env bash
# host/fn-cluster-up.sh — stand the flashnext pair service with one command.
#
# Order is load-bearing (the reap-then-gate doctrine of the ds4 estate's
# ds4-cluster-restart.sh, specs/flashnext/evidence/ds4-vllm-manifest.md §5.5):
#
#   1. REAP stranded serve processes on BOTH nodes first, then GATE on zero
#      residue. Stopping a supervising unit does not kill the `vllm serve`
#      behind it; repeated restarts strand one husk each, holding the port.
#   2. Containers on both nodes — the worker over ssh on the WIRE
#      (10.99.9.2); the rails carry tensors only, never control traffic.
#   3. Ray head on the coordinator, worker join; the worker python pool is
#      capped small (RAY_NUM_CPUS, host/fn-env.sh).
#   4. A hard two-GPU gate BEFORE serve — if ray never reports 2.0 GPU, we
#      abort loud rather than serve at TP=1.
#   5. The TP=2 eager serve, then a wait-for-reality API poll.
#
# Failure cannot strand ranks: flashnext-pair.service runs this as a oneshot
# with ExecStopPost=fn-cluster-down.sh, so even a failed bring-up tears down.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=host/fn-env.sh
source "$SCRIPT_DIR/fn-env.sh"

log() { echo "fn-cluster-up: $*" >&2; }
worker() { ssh "$FN_WORKER_HOST" "$@"; }

# --- 1. reap stranded serve processes, then gate on zero residue -------------
# The serve process tree is visible from the host /proc even when it runs
# inside a podman container, so we reap host-side on both nodes. The bracket
# in the pattern keeps pgrep from matching itself.
reap_serve_node() {
  local pids
  pids="$(pgrep -f 'bin/[v]llm serve' || true)"
  if [ -n "$pids" ]; then
    echo "fn-cluster-up: reaping stranded serve pids: $(echo $pids | tr '\n' ' ')" >&2
    kill -TERM $pids 2>/dev/null || true
    sleep 3
    pids="$(pgrep -f 'bin/[v]llm serve' || true)"
    [ -z "$pids" ] || kill -KILL $pids 2>/dev/null || true
    sleep 1
  fi
  pgrep -f 'bin/[v]llm serve' | wc -l
}

log "reap: coordinator"
residue="$(reap_serve_node)"
if [ "$residue" -ne 0 ]; then
  log "FATAL: $residue stranded serve process(es) on the coordinator after reap"
  exit 1
fi
log "reap: worker ($FN_WORKER_HOST over the wire)"
residue="$(worker "$(declare -f reap_serve_node); reap_serve_node")"
if [ "$residue" -ne 0 ]; then
  log "FATAL: $residue stranded serve process(es) on the worker after reap"
  exit 1
fi
log "reap gate passed: zero residue on both nodes"

# --- 2. containers on both nodes ---------------------------------------------
# The env file is built PER NODE by sourcing fn-env.sh there: the rail list
# is computed from each node's own `ip -br addr`, never copied across.
ENV_FILTER='^(FN_|NCCL_|RAY_|TORCH_NCCL_|VLLM_|PYTHONHASHSEED=|HSA_|PYTORCH_HIP_|TORCHINDUCTOR_|TRITON_|HF_)'

mkdir -p "$FN_STATE_DIR"
LOCAL_ENV_FILE="$FN_STATE_DIR/container-env.list"
( set -a; source "$SCRIPT_DIR/fn-env.sh" >/dev/null; env ) \
  | grep -E "$ENV_FILTER" | LC_ALL=C sort > "$LOCAL_ENV_FILE"

REMOTE_TMP="$(worker 'mktemp -d')"
worker "cat > '$REMOTE_TMP/fn-env.sh'" < "$SCRIPT_DIR/fn-env.sh"
worker "mkdir -p '$FN_STATE_DIR' \
  && ( set -a; source '$REMOTE_TMP/fn-env.sh' >/dev/null; env ) \
     | grep -E '$ENV_FILTER' | LC_ALL=C sort > '$REMOTE_TMP/env.list'"

run_container() {  # $1 = env-file path; runs on the local node
  podman rm -f "$FN_CONTAINER" >/dev/null 2>&1 || true
  podman run -d --name "$FN_CONTAINER" \
    --network host --ipc host \
    --device /dev/kfd --device /dev/dri \
    --security-opt seccomp=unconfined \
    --group-add keep-groups --ulimit memlock=-1:-1 \
    -v /var/lib/local-models:/var/lib/local-models:ro \
    -v "$FN_STATE_DIR:$FN_STATE_DIR" \
    --env-file "$1" \
    "$FN_IMAGE" sleep infinity >/dev/null
}

log "container: coordinator"
run_container "$LOCAL_ENV_FILE"
log "container: worker"
worker "podman rm -f '$FN_CONTAINER' >/dev/null 2>&1 || true \
  && podman run -d --name '$FN_CONTAINER' \
    --network host --ipc host \
    --device /dev/kfd --device /dev/dri \
    --security-opt seccomp=unconfined \
    --group-add keep-groups --ulimit memlock=-1:-1 \
    -v /var/lib/local-models:/var/lib/local-models:ro \
    -v '$FN_STATE_DIR:$FN_STATE_DIR' \
    --env-file '$REMOTE_TMP/env.list' \
    '$FN_IMAGE' sleep infinity >/dev/null"

# --- 3. ray: distributed head on the coordinator, worker join -----------------
log "ray head on the coordinator"
podman exec "$FN_CONTAINER" ray start --head \
  --port "$FN_RAY_PORT" \
  --num-cpus "$RAY_NUM_CPUS" \
  --include-dashboard=false >/dev/null
log "ray join from the worker (dials the fleet identity $FN_HEAD_IP on the wire)"
# --include-dashboard is head-only: ray PANICs if it is passed to a worker.
worker "podman exec '$FN_CONTAINER' ray start \
  --address='$FN_HEAD_IP:$FN_RAY_PORT' \
  --num-cpus '$RAY_NUM_CPUS'" >/dev/null

# --- 4. hard two-GPU gate before serve ----------------------------------------
gpus_total=""
for _ in $(seq 1 120); do
  gpus_total="$(podman exec "$FN_CONTAINER" ray status 2>/dev/null \
    | awk '$2 == "GPU" { split($1, a, "/"); print a[2]; exit }')" || true
  case "$gpus_total" in
    2|2.0) break ;;
  esac
  sleep 2
done
case "$gpus_total" in
  2|2.0) log "two-GPU gate passed: ray reports $gpus_total GPU" ;;
  *)
    log "FATAL: ray never reported 2.0 GPU (last reading: '$gpus_total'); refusing to serve"
    podman exec "$FN_CONTAINER" ray status >&2 || true
    exit 1
    ;;
esac

# --- 5. the serve: TP=2, eager -------------------------------------------------
# Do not add comments inside the backslash-continued `vllm serve` command
# below: a '#' there silently comments out every remaining argument, and
# `bash -n` still reports the file as valid.
log "serving $FN_MODEL_DIR as '$FN_SERVED_NAME' at TP=2 (eager)"
podman exec -d "$FN_CONTAINER" bash -c "exec vllm serve $FN_MODEL_DIR \
  --served-model-name $FN_SERVED_NAME \
  --host 0.0.0.0 \
  --port $FN_PORT \
  --tensor-parallel-size 2 \
  --distributed-executor-backend ray \
  --enforce-eager \
  --gpu-memory-utilization $FN_GPU_UTIL \
  --max-model-len $FN_MAX_CTX \
  > '$FN_STATE_DIR/serve.log' 2>&1"

# Wait for reality, not for the target's word (the fleet's library-reachable
# pattern): poll the models endpoint until the served name answers.
attempts=$(( ${FN_SERVE_TIMEOUT:-2700} / 20 ))
ready=0
for _ in $(seq 1 "$attempts"); do
  if curl -fsS "http://127.0.0.1:$FN_PORT/v1/models" 2>/dev/null \
      | grep -q "\"$FN_SERVED_NAME\""; then
    ready=1
    break
  fi
  sleep 20
done
if [ "$ready" -ne 1 ]; then
  log "FATAL: serve did not answer on :$FN_PORT inside the timeout"
  tail -n 40 "$FN_STATE_DIR/serve.log" >&2 || true
  exit 1
fi
log "pair service is up: $FN_SERVED_NAME answering on :$FN_PORT"
