#!/usr/bin/env bash
# scripts/run-proxy.sh — single-node first light on the proxy checkpoint.
#
# Spec claim 4.1: "the proxy checkpoint serves on one node with the mmap path
# engaged -> a proxy receipt records finite output and a clean shutdown."
#
# Sequence: serve /var/tmp/flashnext-proxy from flashnext:dev on ONE GPU with
# VLLM_PLE_MMAP=1 -> wait for the models endpoint to answer -> one greedy
# completion whose per-token logprobs are asserted finite through the API ->
# stop the container and confirm it exited -> write results/receipts/proxy.json.
#
# The receipt's data carries the SERVE ENV CHOICES verbatim, because cp-proxy
# and the later TP=2 serve must reproduce them, not re-derive them.
#
# THE THREE SERVE FACTS THIS SCRIPT EXISTS TO PIN (docs/DAYRUN-STOP-STATE-
# 2026-08-29.md, "Engine knowledge the second flow MUST carry"):
#
#  1. --limit-mm-per-prompt '{"image":0,"video":0}' on EVERY serve. The vision
#     encoder's profiling pass materializes a 65536^2 fp32 SDPA matrix on
#     gfx1151 — 274877906944 bytes, exactly 256 GiB, math fallback, no flash
#     ViT kernel — and the serve dies in profile_run before it ever answers.
#     Text-only is operator-ratified for this campaign. Deliberately NOT the
#     multimodal-profiling bypass flag: that only defers the wall to the first
#     real image at serve time, which is worse, because it fails in front of a
#     request instead of at boot.
#
#  2. gpu_memory_utilization stays 0.6 here. The proxy is tiny; the headroom
#     is what the mmap'd engram table wants as page cache, BY DESIGN.
#
#  3. VLLM_ROCM_APU_UNIFIED_MEMORY is NOT set, at any value. These hosts log
#     integrated_gpu=False (the gttsize parameters make HIP report the full
#     128 GiB), so the fork's APU patches are inert and stock accounting is
#     already correct. The earlier steer to set it was superseded.
#
# EXECUTION MODE — read this before changing FN_PROXY_EXEC_MODE.
#     The task asks for eager execution, and --enforce-eager is the flag that
#     would deliver it. Under VLLM_PLE_MMAP=1 the fork REFUSES it: the second
#     of check_cudagraph_safety's three guards raises at construction time
#     with "enforce-eager does not fully suppress CUDA graph capture on this
#     model", because the guard requires CompilationMode.VLLM_COMPILE. The
#     first guard separately refuses full CUDA graphs and names its remedy,
#     -cc.cudagraph_mode=PIECEWISE. So graph-capture-free serving under the
#     mmap table is spelled PIECEWISE here, not --enforce-eager; that is the
#     mode the successful proxy boot used and the mode host/fn-cluster-up.sh
#     now serves TP=2 in. FN_PROXY_EXEC_MODE=eager is kept as a real, working
#     branch for a VLLM_PLE_MMAP=0 control run, and refuses to arm itself
#     alongside the mmap table rather than failing deep inside the engine.
#     Whichever branch runs is recorded in the receipt.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
IMAGE="${FN_IMAGE:-flashnext:dev}"
PROXY_DIR="${FN_PROXY_DIR:-/var/tmp/flashnext-proxy}"
CONTAINER="${FN_PROXY_CONTAINER:-flashnext-proxy}"
# A port of its own: the pair service owns FN_PORT (1234) and a proxy run must
# never be mistaken for it, nor collide with it on an operator's box.
PORT="${FN_PROXY_PORT:-1236}"
SERVED_NAME="${FN_PROXY_SERVED_NAME:-flashnext-proxy}"
RECEIPTS="$REPO_ROOT/results/receipts"
OUT="$RECEIPTS/proxy.json"
SERVE_TIMEOUT="${FN_PROXY_SERVE_TIMEOUT:-1800}"
STOP_TIMEOUT="${FN_PROXY_STOP_TIMEOUT:-90}"
MAX_LEN="${FN_PROXY_MAX_LEN:-4096}"
MAX_SEQS="${FN_PROXY_MAX_SEQS:-8}"
MAX_BATCHED_TOKENS="${FN_PROXY_MAX_BATCHED_TOKENS:-2048}"
GPU_UTIL="${FN_PROXY_GPU_UTIL:-0.6}"
MAX_TOKENS="${FN_PROXY_MAX_TOKENS:-16}"

log() { echo "run-proxy: $*" >&2; }

TMP="$(mktemp -d)"
SERVE_LOG="$TMP/serve.log"
cleanup() {
  # Always leave the box as we found it: no stranded container, no held GPU.
  podman rm -f "$CONTAINER" >/dev/null 2>&1 || true
  rm -rf "$TMP"
}
trap cleanup EXIT

mkdir -p "$RECEIPTS"

# --- the serve environment, in one place --------------------------------------
# Recorded verbatim into the receipt; nothing below reads an engine knob that
# is not named here. NOTE the F.9 hard rule from host/fn-env.sh: never export a
# default for an engine knob read through an is-set probe. Every variable in
# this map is read by value, not by presence.
VLLM_PLE_MMAP=1                      # the table serves from NVMe via mmap
PYTHONHASHSEED=0                     # prefix-cache seed; greedy repeats stay stable
# Optional per the lane's brief; on by default because the load path fragments
# a unified-memory allocator badly without it.
PYTORCH_HIP_ALLOC_CONF="${FN_PROXY_ALLOC_CONF:-expandable_segments:True}"

FN_PROXY_EXEC_MODE="${FN_PROXY_EXEC_MODE:-piecewise}"
case "$FN_PROXY_EXEC_MODE" in
  piecewise)
    EXEC_ARGS=(-cc.cudagraph_mode=PIECEWISE)
    ;;
  eager)
    if [ "$VLLM_PLE_MMAP" = "1" ]; then
      log "FATAL: FN_PROXY_EXEC_MODE=eager needs VLLM_PLE_MMAP=0."
      log "  The fork's check_cudagraph_safety refuses --enforce-eager while the"
      log "  mmap table is armed ('enforce-eager does not fully suppress CUDA"
      log "  graph capture on this model'). Use FN_PROXY_EXEC_MODE=piecewise for"
      log "  claim 4.1; eager is a VLLM_PLE_MMAP=0 control only."
      exit 2
    fi
    EXEC_ARGS=(--enforce-eager)
    ;;
  *)
    log "FATAL: FN_PROXY_EXEC_MODE must be 'piecewise' or 'eager', got '$FN_PROXY_EXEC_MODE'"
    exit 2
    ;;
esac

# --- preconditions -------------------------------------------------------------
if [ ! -f "$PROXY_DIR/config.json" ]; then
  log "FATAL: no proxy checkpoint at $PROXY_DIR; run scripts/make-proxy.sh first"
  exit 2
fi

# Do not add comments inside the backslash-continued argument list below: a '#'
# there silently swallows every remaining argument and `bash -n` still calls
# the file clean (the trap host/fn-cluster-up.sh documents at its own serve).
SERVE_ARGS=(
  vllm serve /proxy
  --served-model-name "$SERVED_NAME"
  --host 0.0.0.0
  --port "$PORT"
  --tensor-parallel-size 1
  --gpu-memory-utilization "$GPU_UTIL"
  --max-model-len "$MAX_LEN"
  --limit-mm-per-prompt '{"image":0,"video":0}'
  --max-num-batched-tokens "$MAX_BATCHED_TOKENS"
  --max-num-seqs "$MAX_SEQS"
  "${EXEC_ARGS[@]}"
)

log "serving $PROXY_DIR as '$SERVED_NAME' on :$PORT (single node, $FN_PROXY_EXEC_MODE)"
podman rm -f "$CONTAINER" >/dev/null 2>&1 || true
podman run -d --name "$CONTAINER" \
  --device /dev/kfd --device /dev/dri \
  --security-opt seccomp=unconfined --ipc=host \
  -p "127.0.0.1:$PORT:$PORT" \
  -e VLLM_PLE_MMAP="$VLLM_PLE_MMAP" \
  -e PYTHONHASHSEED="$PYTHONHASHSEED" \
  -e PYTORCH_HIP_ALLOC_CONF="$PYTORCH_HIP_ALLOC_CONF" \
  -v "$PROXY_DIR:/proxy:ro" "$IMAGE" "${SERVE_ARGS[@]}" >/dev/null

# --- wait for reality, not for the target's word --------------------------------
ready=0
deadline=$(( SERVE_TIMEOUT / 10 ))
for _ in $(seq 1 "$deadline"); do
  if curl -fsS "http://127.0.0.1:$PORT/v1/models" 2>/dev/null \
      | grep -q "\"$SERVED_NAME\""; then
    ready=1
    break
  fi
  if [ -z "$(podman ps -q --filter "name=^${CONTAINER}$")" ]; then
    log "serve container exited before answering"
    break
  fi
  sleep 10
done
podman logs "$CONTAINER" > "$SERVE_LOG" 2>&1 || true

if [ "$ready" -ne 1 ]; then
  log "FATAL: serve did not answer on :$PORT inside ${SERVE_TIMEOUT}s"
  tail -n 40 "$SERVE_LOG" >&2 || true
fi

# --- one greedy completion, logprobs asserted finite ----------------------------
export FN_PROXY_API="http://127.0.0.1:$PORT"
export FN_PROXY_SERVED_NAME="$SERVED_NAME"
export FN_PROXY_MAX_TOKENS="$MAX_TOKENS"
export FN_PROXY_CLIENT_OUT="$TMP/client.json"
if [ "$ready" -eq 1 ]; then
  python3 - <<'PY' || true
"""One greedy completion; every returned logprob must be a finite float."""
import json, math, os, sys, urllib.request

API = os.environ["FN_PROXY_API"]
PROMPT = ("The proxy checkpoint answers before any real weight byte is "
          "loaded, and the receipt records: ")
out = {"requested": True}
try:
    body = {"model": os.environ["FN_PROXY_SERVED_NAME"], "prompt": PROMPT,
            "max_tokens": int(os.environ["FN_PROXY_MAX_TOKENS"]),
            "temperature": 0, "logprobs": 1}
    req = urllib.request.Request(API + "/v1/completions",
                                 data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=900) as resp:
        answer = json.loads(resp.read())
    choice = answer["choices"][0]
    text = choice["text"]
    logprobs = (choice.get("logprobs") or {}).get("token_logprobs") or []
    values = [v for v in logprobs if v is not None]
    out["completion_tokens"] = answer.get("usage", {}).get("completion_tokens")
    out["completion_bytes"] = len(text.encode())
    out["logprobs_returned"] = len(values)
    # Finite output is the claim: no NaN, no +/-inf anywhere in the returned
    # per-token logprobs, and at least one token actually generated.
    out["finite_logits"] = bool(values) and all(
        isinstance(v, (int, float)) and math.isfinite(v) for v in values)
    out["logprob_min"] = min(values) if values else None
    out["logprob_max"] = max(values) if values else None
    out["greedy"] = True
except Exception as e:  # noqa: BLE001 - any client failure is a graded failure
    out["finite_logits"] = False
    out["error"] = f"{e.__class__.__name__}: {e}"
json.dump(out, open(os.environ["FN_PROXY_CLIENT_OUT"], "w"), indent=1)
PY
fi

# --- clean shutdown -------------------------------------------------------------
# `podman stop` sends SIGTERM and waits; the container must be gone by itself
# before the timeout escalates to SIGKILL, which is what "clean" means here.
stop_rc=0
podman stop -t "$STOP_TIMEOUT" "$CONTAINER" >/dev/null 2>&1 || stop_rc=$?
serve_exit="$(podman inspect -f '{{.State.ExitCode}}' "$CONTAINER" 2>/dev/null || echo "")"
container_state="$(podman inspect -f '{{.State.Status}}' "$CONTAINER" 2>/dev/null || echo "")"
port_free=0
curl -fsS --max-time 5 "http://127.0.0.1:$PORT/v1/models" >/dev/null 2>&1 || port_free=1
podman logs "$CONTAINER" > "$SERVE_LOG" 2>&1 || true
podman rm -f "$CONTAINER" >/dev/null 2>&1 || true

# --- the receipt -----------------------------------------------------------------
export FN_PROXY_RECEIPTS="$RECEIPTS"
export FN_PROXY_READY="$ready"
export FN_PROXY_STOP_RC="$stop_rc"
export FN_PROXY_SERVE_EXIT="$serve_exit"
export FN_PROXY_CONTAINER_STATE="$container_state"
export FN_PROXY_PORT_FREE="$port_free"
export FN_PROXY_SERVE_LOG="$SERVE_LOG"
export FN_PROXY_DIR_ABS="$PROXY_DIR"
export FN_PROXY_IMAGE="$IMAGE"
export FN_PROXY_EXEC_MODE
export FN_PROXY_VLLM_PLE_MMAP="$VLLM_PLE_MMAP"
export FN_PROXY_ALLOC_CONF_USED="$PYTORCH_HIP_ALLOC_CONF"
export FN_PROXY_GPU_UTIL="$GPU_UTIL"
printf '%s\n' "${SERVE_ARGS[@]}" > "$TMP/serve-args.txt"
export FN_PROXY_SERVE_ARGS_FILE="$TMP/serve-args.txt"

rc=0
python3 - <<'PY' || rc=$?
"""Write results/receipts/proxy.json — or the quarantined failure twin.

RECEIPT QUARANTINE (D12, mirrored from scripts/run-smoke.sh and run-tp2.sh):
a status=fail receipt lands under results/receipts/failed/ — a typed blocker
the morning operator reads first, but outside receipts-verify's non-recursive
grading walk, so one failed proxy run cannot redden every later gate.

IDEMPOTENCE (overseer standing note 2): the graded facts below are
deterministic — greedy decode under a fixed PYTHONHASHSEED over a fixed
checkpoint. A re-run that re-measures the SAME facts leaves the committed
receipt byte-for-byte alone, reusing its ts rather than stamping a fresh one.
A drift in any graded fact rewrites the receipt, which is exactly the tracked
change an operator should see.
"""
import hashlib, json, os, sys, time

RECEIPTS = os.environ["FN_PROXY_RECEIPTS"]
ready = os.environ["FN_PROXY_READY"] == "1"

client = {}
client_path = os.environ.get("FN_PROXY_CLIENT_OUT", "")
if client_path and os.path.isfile(client_path):
    try:
        client = json.load(open(client_path))
    except ValueError as e:
        client = {"finite_logits": False, "error": f"unreadable client output: {e}"}

log_text = ""
try:
    log_text = open(os.environ["FN_PROXY_SERVE_LOG"], errors="ignore").read()
except OSError:
    pass

# The table path must have ENGAGED, not merely been requested: the env flag
# plus the fork's own module name in this run's serve log.
lowered = log_text.lower()
ple_engaged = ("ple_mmap" in lowered
               and os.environ["FN_PROXY_VLLM_PLE_MMAP"] == "1")

stop_rc = os.environ["FN_PROXY_STOP_RC"]
serve_exit = os.environ["FN_PROXY_SERVE_EXIT"]
clean_shutdown = (stop_rc == "0"
                  and os.environ["FN_PROXY_CONTAINER_STATE"] == "exited"
                  and os.environ["FN_PROXY_PORT_FREE"] == "1")

serve_args = []
try:
    serve_args = open(os.environ["FN_PROXY_SERVE_ARGS_FILE"]).read().splitlines()
except OSError:
    pass

data = {
    "checkpoint": os.environ["FN_PROXY_DIR_ABS"],
    "image": os.environ["FN_PROXY_IMAGE"],
    "tensor_parallel_size": 1,
    "served": ready,
    "finite_logits": bool(client.get("finite_logits")),
    "clean_shutdown": bool(clean_shutdown),
    "serve_exit_code": serve_exit,
    "completion_tokens": client.get("completion_tokens"),
    "logprobs_returned": client.get("logprobs_returned"),
    "greedy": bool(client.get("greedy")),
    "table_gpu_resident_bytes": 0 if ple_engaged else None,
    "ple_mmap_engaged": ple_engaged,
    "ple_mmap_log_lines": lowered.count("ple_mmap"),
    # The serve env choices, recorded so cp-proxy and the TP=2 serve reproduce
    # them instead of re-deriving them. Read together with serve_args.
    "serve_env": {
        "VLLM_PLE_MMAP": os.environ["FN_PROXY_VLLM_PLE_MMAP"],
        "PYTHONHASHSEED": "0",
        "PYTORCH_HIP_ALLOC_CONF": os.environ["FN_PROXY_ALLOC_CONF_USED"],
        "VLLM_ROCM_APU_UNIFIED_MEMORY": None,
    },
    "serve_args": serve_args,
    "exec_mode": os.environ["FN_PROXY_EXEC_MODE"],
    "gpu_memory_utilization": float(os.environ["FN_PROXY_GPU_UTIL"]),
    "multimodal_limit": {"image": 0, "video": 0},
}
if client.get("error"):
    data["client_error"] = client["error"]
if not ready:
    data["serve_log_tail"] = log_text.splitlines()[-20:]

status = "pass" if (ready and data["finite_logits"] and data["clean_shutdown"]
                    and ple_engaged) else "fail"
receipt = {"step": "proxy", "status": status,
           "ts": time.strftime("%FT%T"), "data": data}

base = RECEIPTS if status == "pass" else os.path.join(RECEIPTS, "failed")
os.makedirs(base, exist_ok=True)
path = os.path.join(base, "proxy.json")
try:
    old = json.load(open(path))
    if old.get("status") == status and old.get("data") == data:
        print("run-proxy: facts unchanged; committed receipt left untouched",
              file=sys.stderr)
        print(json.dumps(receipt, indent=1))
        sys.exit(0 if status == "pass" else 1)
except (OSError, ValueError):
    pass
with open(path, "w") as fh:
    json.dump(receipt, fh, indent=1)
print(json.dumps(receipt, indent=1))
print(f"run-proxy: proxy receipt status={status}", file=sys.stderr)
sys.exit(0 if status == "pass" else 1)
PY

log "proxy receipt: $OUT (fail receipts land under results/receipts/failed/)"
exit $rc
