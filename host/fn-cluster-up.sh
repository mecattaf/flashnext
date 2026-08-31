#!/usr/bin/env bash
# host/fn-cluster-up.sh — stand the flashnext pair service with one command.
#
# Order is load-bearing (the reap-then-gate doctrine of the ds4 estate's
# ds4-cluster-restart.sh, specs/flashnext/evidence/ds4-vllm-manifest.md §5.5):
#
#   1. ARBITRATE THE MODEL-SWAP PROXY on both twins before anything else. The
#      always-on proxy on :9292 holds no GPU at rest but spawns a backend on
#      ANY request to that port, out of the same 125 GB unified pool this
#      serve is about to hold at ~78 GiB/rank. It goes first because it can
#      race even the reap (host/fn-swap-arbitrate.sh).
#   2. THE TP=2 GUARDS — the eight parity and divisibility assertions plus the
#      selection-time expert-sharding checks, all arithmetic over the
#      checkpoint header, all fail-closed, all BEFORE the first launch
#      (host/fn-tp2-guards.py). A bad split on this stack does not crash: it
#      serves fluent wrong output, which no later receipt can catch.
#   3. REAP stranded serve processes on BOTH nodes, then GATE on zero
#      residue. Stopping a supervising unit does not kill the `vllm serve`
#      behind it; repeated restarts strand one husk each, holding the port.
#   4. Containers on both nodes — the worker over ssh on the WIRE
#      (10.99.9.2); the rails carry tensors only, never control traffic.
#   5. Ray head on the coordinator, worker join; the worker python pool is
#      capped small (RAY_NUM_CPUS, host/fn-env.sh).
#   6. A hard two-GPU gate BEFORE serve — if ray never reports 2.0 GPU, we
#      abort loud rather than serve at TP=1.
#   7. The TP=2 serve, then a wait-for-reality API poll.
#
# Failure cannot strand ranks: flashnext-pair.service runs this as a oneshot
# with ExecStopPost=fn-cluster-down.sh, so even a failed bring-up tears down —
# and the model-swap proxy is put back to its arrival state on every exit
# path, this script's own failure exits included (the trap below).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=host/fn-env.sh
source "$SCRIPT_DIR/fn-env.sh"

log() { echo "fn-cluster-up: $*" >&2; }
worker() { ssh "$FN_WORKER_HOST" "$@"; }

# --- 1. arbitrate the model-swap proxy on both twins --------------------------
# THE RACE THIS CLOSES: the always-on model-swap proxy fronting the single-node
# model pool listens on :9292 and is idle at rest — but a single request to
# that port (three live doors: the tailnet, the house LAN, and a local
# utility-model wrapper) spawns a backend that allocates out of the very same
# 125 GB unified pool this serve holds. Until now the two systems were
# mutually blind: this file carried no reference to that proxy or to :9292 and
# the proxy carries none to us.
#
# A STOP, NEVER A DISABLE — the unit stays enabled and the morning boots into
# its normal roster. The arrival state is recorded in
# $FN_STATE_DIR/swap-arbitration.json (folded into the tp2 receipt by
# scripts/run-tp2.sh) so the morning can tell whether the night took the
# roster down or found it down.
# The trap is armed BEFORE the stop, not after: a stop that fails halfway —
# coordinator down, worker unreachable — must still put back what it took.
# ExecStopPost covers the unit case, but this script is also run directly by
# scripts/run-tp2.sh, where a FATAL below would otherwise leave the morning
# roster down with nobody to put it back.
restore_swap_proxy() {
  local rc=$?
  if [ "$rc" -ne 0 ]; then
    log "bring-up failed (exit $rc); restoring the model-swap proxy to its arrival state"
    bash "$SCRIPT_DIR/fn-swap-arbitrate.sh" restore || true
  fi
  return "$rc"
}
trap restore_swap_proxy EXIT

log "arbitrate: model-swap proxy on :${FN_SWAP_PORT:-9292}, both twins"
bash "$SCRIPT_DIR/fn-swap-arbitrate.sh" stop

# --- 2. the TP=2 guards: fail loud and early, before the first launch ---------
# The serve argv is built ONCE, here, and handed to BOTH the guard and the
# serve, so the thing that is checked is the thing that runs. Same rule as the
# `vllm serve` line itself: NO '#' comments inside the backslash continuation
# below — one would silently comment out every remaining argument, and
# `bash -n` still calls the file valid.
#
# --enable-expert-parallel is LOAD-BEARING, not a tuning knob. The routed
# experts are block-FP8 at 128x128 with a 640-wide intermediate: a
# tensor-parallel MoE would slice that to 320 per rank, which is not a whole
# number of blocks, so each rank's slice would mix two neighbouring blocks'
# scales and be SILENTLY WRONG. Expert parallelism shards at SELECTION and
# moves whole experts instead, which is the only split these bytes admit.
# fn-tp2-guards.py asserts exactly that (A6 and S1) against this argv.
#
# --limit-mm-per-prompt image/video 0: text-only serve, operator-ratified.
# The vision encoder profiling pass materializes a 65536² fp32 SDPA matrix
# (= 256 GiB exactly) on gfx1151 — no flash ViT kernel, math fallback. NOT
# --skip-mm-profiling: a real image at serve time hits the same wall.
#
# No --enforce-eager: fn-env.sh's unconditional VLLM_PLE_MMAP=1 + the fork's
# check_cudagraph_safety guard REFUSE plain eager with PLE mmap — first light
# must run VLLM_COMPILE + PIECEWISE with the mmap op as a split boundary,
# the mode the successful proxy boot used (spec P10; DAYRUN-NOTES pre-arm).
#
# --max-num-batched-tokens 2048: QSA indexer workspace scales with
# batch × context (ds4 precedent: 512 at 512K ctx). Start 2048 at 256K,
# drop to 512 on OOM.
#
# --kv-cache-memory-bytes: pin the KV/state pool at 12 GiB (FN_KV_CACHE_BYTES,
# fn-env.sh) so the GDN slot pool + paged KV land deterministically inside
# the P11 residency bound instead of floating with gpu-memory-utilization.
#
# --max-num-seqs: cap the GDN state slots (fn-env.sh FN_MAX_SEQS; the engine
# default of 256 preallocates ~14 GiB/rank).
#
# FN_SPEC_ARGS is EMPTY by default: first light is spec-off (the identity
# oracle's baseline). Speculative promotion is a morning env flip, e.g.
# FN_SPEC_ARGS="--speculative-config '{\"method\":\"mtp\",\"num_speculative_tokens\":3}'".
SERVE_ARGS="--served-model-name $FN_SERVED_NAME \
  --host 0.0.0.0 \
  --port $FN_PORT \
  --tensor-parallel-size 2 \
  --enable-expert-parallel \
  --distributed-executor-backend ray \
  --gpu-memory-utilization $FN_GPU_UTIL \
  --max-model-len $FN_MAX_CTX \
  --limit-mm-per-prompt '{\"image\":0,\"video\":0}' \
  --max-num-batched-tokens ${FN_MAX_BATCHED_TOKENS:-2048} \
  --kv-cache-memory-bytes ${FN_KV_CACHE_BYTES:-12884901888} \
  --max-num-seqs ${FN_MAX_SEQS:-32} \
  -cc.cudagraph_mode=PIECEWISE \
  ${FN_SPEC_ARGS:-}"

log "guards: the eight parity assertions and the sharding checks (arithmetic only, no load)"
python3 "$SCRIPT_DIR/fn-tp2-guards.py" --serve-argv "$SERVE_ARGS"

# --- 3. reap stranded serve processes, then gate on zero residue -------------
# The serve process tree is visible from the host /proc even when it runs
# inside a podman container, so we reap host-side on both nodes.
# reap_serve_node is defined in fn-env.sh (shared with bench/run-matrix.sh's
# per-arm cross-node reap).

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

# --- 4. containers on both nodes ---------------------------------------------
# The env file is built PER NODE by sourcing fn-env.sh there — EXCEPT the
# transport decisions: NCCL_SOCKET_IFNAME, GLOO_SOCKET_IFNAME and
# FN_TRANSPORT_RUNG are decided ONCE, on the coordinator, and injected into
# the worker's sourcing as pre-set literals (fn-env's ${VAR:-...} form honours
# them). Without this a single lost ICMP packet on one node could bootstrap
# the two ranks on different interfaces.
#
# The GLOO_ prefix is LOAD-BEARING, not tidiness. This filter is the ONLY
# thing that decides what reaches the container: podman is handed the filtered
# file as --env-file below, so a GLOO_SOCKET_IFNAME exported perfectly in
# fn-env.sh but not matched here never reaches `vllm serve` at all, and the
# fix looks applied while the pair still dies on the loopback bind.
ENV_FILTER='^(FN_|GLOO_|NCCL_|RAY_|TORCH_NCCL_|VLLM_|PYTHONHASHSEED=|HSA_|PYTORCH_HIP_|TORCHINDUCTOR_|TRITON_|HF_)'

mkdir -p "$FN_STATE_DIR"
LOCAL_ENV_FILE="$FN_STATE_DIR/container-env.list"
( set -a; source "$SCRIPT_DIR/fn-env.sh" >/dev/null; env ) \
  | grep -E "$ENV_FILTER" | LC_ALL=C sort > "$LOCAL_ENV_FILE"

# VERIFY the compiler-cache pinning; do NOT rewrite it. ENV_FILTER above
# already carries TORCHINDUCTOR_ and TRITON_, fn-env.sh points both at
# $FN_STATE_DIR (never tmpfs, or the first bring-up after a boot spends ~25
# min in LLVM), and FN_STATE_DIR is bind-mounted at the SAME absolute path on
# both nodes below. That is correct today. What is checked here is that it
# STAYS correct: a filter edit that dropped either prefix would leave the two
# ranks compiling into different directories with nothing to say so.
for cache_var in TORCHINDUCTOR_CACHE_DIR TRITON_CACHE_DIR; do
  cache_val="$(grep -E "^${cache_var}=" "$LOCAL_ENV_FILE" | head -n1 | cut -d= -f2-)"
  if [ -z "$cache_val" ]; then
    log "FATAL: $cache_var is missing from the container env file; the ENV_FILTER no longer carries it and both ranks would recompile into a tmpfs default"
    exit 1
  fi
  case "$cache_val" in
    "$FN_STATE_DIR"/*) ;;
    *)
      log "FATAL: $cache_var is '$cache_val', outside the bind-mounted state directory $FN_STATE_DIR"
      exit 1 ;;
  esac
  log "cache pin verified: $cache_var=$cache_val (under the state dir, bind-mounted identically on both nodes)"
done

# VERIFY the gloo pin actually reached the container env file. This is the
# exact trap that turns a correct-looking fix into a no-op: fn-env.sh can
# export GLOO_SOCKET_IFNAME perfectly and the container still never sees it if
# ENV_FILTER above stops carrying the GLOO_ prefix. The symptom is not a
# missing variable — it is torch resolving gethostname() to NixOS's 127.0.0.2,
# binding loopback with NO warning, and dying twenty minutes into engine-core
# init on `Gloo connectFullMesh failed ... remote=[127.0.0.2]`. `lo` is
# rejected too: the fleet /32 shares that interface with 127.0.0.1 and gloo
# may pick either.
gloo_ifname="$(grep -E '^GLOO_SOCKET_IFNAME=' "$LOCAL_ENV_FILE" | head -n1 | cut -d= -f2-)"
case "$gloo_ifname" in
  ""|lo|lo,*)
    log "FATAL: GLOO_SOCKET_IFNAME is '$gloo_ifname' in the container env file; without a routable interface pin the CPU process group binds loopback and connectFullMesh can never cross the pair"
    exit 1 ;;
  *)
    log "gloo pin verified: GLOO_SOCKET_IFNAME=$gloo_ifname reaches the container (ENV_FILTER carries the GLOO_ prefix)" ;;
esac

log "image: ensure the worker carries $FN_IMAGE"
bash "$SCRIPT_DIR/fn-image-ship.sh"

REMOTE_TMP="$(worker 'mktemp -d')"
worker "cat > '$REMOTE_TMP/fn-env.sh'" < "$SCRIPT_DIR/fn-env.sh"
worker "mkdir -p '$FN_STATE_DIR' \
  && ( set -a; \
       export NCCL_SOCKET_IFNAME='$NCCL_SOCKET_IFNAME'; \
       export GLOO_SOCKET_IFNAME='$GLOO_SOCKET_IFNAME'; \
       export FN_TRANSPORT_RUNG='$FN_TRANSPORT_RUNG'; \
       source '$REMOTE_TMP/fn-env.sh' >/dev/null; env ) \
     | grep -E '$ENV_FILTER' | LC_ALL=C sort > '$REMOTE_TMP/env.list'"

# VLLM_HOST_IP IS A -e FLAG, NOT AN ENV-FILE LINE, AND IT DIFFERS PER NODE.
# vLLM's get_ip() (vllm/utils/network_utils.py:34-55) returns VLLM_HOST_IP when
# it is set and otherwise UDP-probes 8.8.8.8:80 and reads back the local
# sockname — i.e. it resolves to whatever the DEFAULT ROUTE is. Measured
# 2026-08-31: the default route on BOTH nodes is `via 10.42.0.1 dev wlp192s0`,
# so get_ip() returns 10.42.0.2 on the coordinator and 10.42.0.5 on the worker
# — the HOUSE WIFI, 8.9 ms RTT against 0.10 ms on the fleet.
#
# That address is not cosmetic. GroupCoordinator stands up a ZMQ MessageQueue
# for the TP group unconditionally at world_size > 1 (parallel_state.py
# :518-521), and its writer binds tcp://get_ip():0 (shm_broadcast.py:522-530)
# with no connect_ip supplied. Every rank then blocks in wait_until_ready()
# (:607-638), which is a bare recv() with NO timeout — so an AP roam, a new
# DHCP lease or client isolation at that instant hangs first light FOREVER,
# with an empty serve.log, until fn-cluster-up's serve poll below gives up
# 45 minutes later and reports nothing. Pinning it to the fleet /32s puts the
# TP control plane on the same interface as everything else in this file.
#
# WHY A FLAG AND NOT fn-env.sh, which is where every other knob lives: this
# value MUST differ per node. Setting it identically — which fn-env.sh would
# force, since fn-preflight.sh byte-compares that file's output across the
# pair — makes the worker call bind("tcp://10.99.9.1:0") on a machine where
# that address does not exist, i.e. ZMQError: Cannot assign requested address.
# A podman -e flag never enters the byte-diff stream (fn-preflight.sh sources
# fn-env.sh and greps ITS env, not the container's), so the per-node value
# cannot fail that gate. Upstream designed for exactly this: VLLM_HOST_IP is
# in WORKER_SPECIFIC_ENV_VARS (v1/executor/ray_utils.py:33-41), which
# get_driver_env_vars() excludes from the driver->actor copy
# (ray_executor_v2.py:361-363), and the vars that ARE copied land via
# os.environ.setdefault (:153-155) — so the worker's own 10.99.9.2 wins twice
# over. This supersedes the paragraph at the ray block below, which argued
# against VLLM_HOST_IP on the assumption it could only be set in fn-env.sh.
#
# The flag must stay AFTER --env-file, and does: `man podman-run`, Environment
# precedence — "--env: Any environment variables specified overrides previous
# settings", env-files included (verified against podman 5.8.4 on this box). So
# even an operator shell that leaked VLLM_HOST_IP into the sourced env cannot
# override the per-node value here; fn-preflight.sh's byte-diff would still
# flag that leak first, which is the wanted order.
run_container() {  # $1 = env-file path, $2 = this node's VLLM_HOST_IP
  podman rm -f "$FN_CONTAINER" >/dev/null 2>&1 || true
  podman run -d --name "$FN_CONTAINER" \
    --network host --ipc host \
    --device /dev/kfd --device /dev/dri \
    --security-opt seccomp=unconfined \
    --group-add keep-groups --ulimit memlock=-1:-1 \
    -v /var/lib/local-models:/var/lib/local-models:ro \
    -v "$FN_STATE_DIR:$FN_STATE_DIR" \
    --env-file "$1" \
    -e VLLM_HOST_IP="$2" \
    "$FN_IMAGE" sleep infinity >/dev/null
}

log "container: coordinator (VLLM_HOST_IP=$FN_HEAD_IP)"
run_container "$LOCAL_ENV_FILE" "$FN_HEAD_IP"
log "container: worker (VLLM_HOST_IP=$FN_WORKER_HOST)"
worker "podman rm -f '$FN_CONTAINER' >/dev/null 2>&1 || true \
  && podman run -d --name '$FN_CONTAINER' \
    --network host --ipc host \
    --device /dev/kfd --device /dev/dri \
    --security-opt seccomp=unconfined \
    --group-add keep-groups --ulimit memlock=-1:-1 \
    -v /var/lib/local-models:/var/lib/local-models:ro \
    -v '$FN_STATE_DIR:$FN_STATE_DIR' \
    --env-file '$REMOTE_TMP/env.list' \
    -e VLLM_HOST_IP='$FN_WORKER_HOST' \
    '$FN_IMAGE' sleep infinity >/dev/null"

# --- 5. ray: distributed head on the coordinator, worker join -----------------
# --num-gpus is DECLARED, not autodetected. Ray's AMD accelerator probe does
# not enumerate this gfx1151 APU: measured in the serve image with /dev/kfd and
# /dev/dri attached, `ray status` printed CPU/memory/object_store and NO GPU row
# at all, while torch in the same container reported cuda available, count 1.
# The two-GPU gate below then read '' and refused to serve. Declaring one GPU
# per node makes ray report 1.0 each (2.0 cluster-wide) and a num_gpus=1 remote
# task really does acquire the device (AMD Radeon 8060S Graphics).
# --node-ip-address is PINNED to the fleet identity, not autodetected. Ray's
# own probe picked wlp192s0 (10.42.0.2) — the HOUSE WIFI — as the head's node
# IP. The two-GPU gate still passed, because ray's own traffic reaches the head
# via the address the join dials; but vLLM then created the c10d TCPStore at
# ray's advertised head IP, on the house LAN, which the worker on the 10.99.x
# fleet network cannot route to. The serve died with
#   DistStoreError: Timed out after 601 seconds waiting for clients.
#                   1/2 clients joined
# Pinning both ends to the fleet /32s keeps the rendezvous on the fleet.
# SUPERSEDED NOTE — this paragraph used to end "this is deliberately NOT done
# with VLLM_HOST_IP: that is VLLM_-prefixed, so it rides the doctrine env
# fn-preflight.sh byte-compares, and it must differ per node — setting it would
# fail the byte-diff by construction." That reasoning holds only for setting it
# in fn-env.sh. It is now set per node as a `podman run -e` flag above, which
# never enters the byte-diff stream at all; see the long comment there. The two
# pins are complementary, not alternatives: --node-ip-address fixes what RAY
# advertises (and hence RayExecutorV2's TCPStore address, taken from
# bundle_assignments[0]["node_ip"], ray_executor_v2.py:330), while VLLM_HOST_IP
# fixes what vLLM's own get_ip() returns for the TP MessageQueue bind, which
# ray never sees.
log "ray head on the coordinator"
podman exec "$FN_CONTAINER" ray start --head \
  --node-ip-address "$FN_HEAD_IP" \
  --port "$FN_RAY_PORT" \
  --num-cpus "$RAY_NUM_CPUS" \
  --num-gpus "$RAY_NUM_GPUS" \
  --include-dashboard=false >/dev/null
log "ray join from the worker (dials the fleet identity $FN_HEAD_IP on the wire)"
# --include-dashboard is head-only: ray PANICs if it is passed to a worker.
worker "podman exec '$FN_CONTAINER' ray start \
  --address='$FN_HEAD_IP:$FN_RAY_PORT' \
  --node-ip-address='$FN_WORKER_HOST' \
  --num-cpus '$RAY_NUM_CPUS' \
  --num-gpus '$RAY_NUM_GPUS'" >/dev/null

# --- 6. hard two-GPU gate before serve ----------------------------------------
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

# --- 7. the serve: TP=2, compiled, expert-parallel ----------------------------
# SERVE_ARGS is built at step 2 above, together with the reasoning for every
# flag in it, and fn-tp2-guards.py has already asserted this exact argv
# against the checkpoint header. Do not inline a flag here: a flag the guard
# never saw is a split nobody checked.
log "serving $FN_MODEL_DIR as '$FN_SERVED_NAME' at TP=2 (compiled, expert-parallel, text-only)"
podman exec -d "$FN_CONTAINER" bash -c "exec vllm serve $FN_MODEL_DIR $SERVE_ARGS \
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
