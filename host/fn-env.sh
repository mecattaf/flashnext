#!/usr/bin/env bash
# host/fn-env.sh — the flashnext pair-service env doctrine.
#
# Sourced by every rank-side actor: the ray head, the ray worker, `vllm
# serve`, and the bring-up/teardown/preflight scripts. Both TP ranks MUST see
# the same doctrine — fn-preflight.sh byte-diffs the exported env across the
# pair before first light, and VLLM_RAY_EXTRA_ENV_VAR_PREFIXES_TO_COPY below
# is what carries FN_* across ray to the worker rank (not in ray's default
# copy prefixes; without it the two TP ranks diverge).
#
# Discipline lifted from the ds4-vllm estate's host/ds4-cluster-env.sh
# (specs/flashnext/evidence/ds4-vllm-manifest.md §5) and the fleet's
# lowlat-cluster doctrine (specs/flashnext/evidence/dotfiles-observed.md §1–2).
# Safe to source repeatedly; every default is override-able via the environment.

# ---------------------------------------------------------------------------
# HARD RULE — spec F.9: NEVER export an environment default the engine reads
# through an is-set probe. VLLM_USE_DEEP_GEMM, VLLM_MOE_USE_DEEP_GEMM and the
# VLLM_ROCM_USE_AITER* family are read via is_set() probes inside the fork:
# EXPORTING THE DEFAULT DIVERTS THE ORACLE INTO A HARD RAISE. If a knob here
# looks like "just pinning the default", it is not that — leave it unset.
# ---------------------------------------------------------------------------

# --- the FN_ configuration surface (propagated to the worker rank by ray) --
export FN_STATE_DIR="${FN_STATE_DIR:-$HOME/.local/state/flashnext}"
export FN_MODEL_DIR="${FN_MODEL_DIR:-/var/lib/local-models/flashnext-fp8}"
export FN_SERVED_NAME="${FN_SERVED_NAME:-flashnext}"
export FN_PORT="${FN_PORT:-1234}"
export FN_RAY_PORT="${FN_RAY_PORT:-6379}"
export FN_MAX_CTX="${FN_MAX_CTX:-262144}"
# Fork patch 0004 points memory reporting at GTT (mem_info_gtt_total =
# 125.1 GiB on these boxes): 0.83 × 125.1 ≈ 104 GiB/rank — over the 80 GiB
# residency bound receipts-verify grades (ruling P11) AND eating the
# ~40 GiB/node page cache the mmap'd engram table needs BY DESIGN.
# 0.62 × 125.1 ≈ 77.6 GiB, which lands the measured ~76–78 GiB/rank.
export FN_GPU_UTIL="${FN_GPU_UTIL:-0.62}"
# The KV/state budget holds the GDN slot pool — 32 slots × 54 MiB × (1+n)
# = 6.9 GiB at n=3 speculative — plus paged KV (~5 GiB). 12 GiB ≈ four
# concurrent 256K streams.
export FN_KV_CACHE_BYTES="${FN_KV_CACHE_BYTES:-12884901888}"
# The engine default of 256 sequence slots would preallocate ~14 GiB/rank of
# GDN state spec-off — and ×4 that spec-on. Cap the slots (memory bomb).
export FN_MAX_SEQS="${FN_MAX_SEQS:-32}"
export FN_IMAGE="${FN_IMAGE:-flashnext:dev}"
export FN_CONTAINER="${FN_CONTAINER:-flashnext-pair}"
# Worker-side actions ride the ethernet wire on the STABLE fleet identity,
# never a 10.99.0.x Thunderbolt rail — the rails carry tensors only
# (dotfiles-observed.md §2.3: admin traffic prefers the 5GbE cable).
export FN_WORKER_HOST="${FN_WORKER_HOST:-10.99.9.2}"
export FN_HEAD_IP="${FN_HEAD_IP:-10.99.9.1}"
# The fleet latency budget from modules/lowlat-cluster.nix: 200 us sits well
# above the measured held figure and well below the unheld one.
export FN_LATENCY_BUDGET_US="${FN_LATENCY_BUDGET_US:-200}"

# --- determinism -------------------------------------------------------------
# vLLM derives NONE_HASH (the prefix-cache chain seed) from PYTHONHASHSEED:
# without a fixed value identical token content produces different block
# filenames every run and the disk cache can never hit across a restart.
# The greedy byte-compare in run-tp2.sh leans on this too.
export PYTHONHASHSEED=0

# --- allocator ---------------------------------------------------------------
# GC is off by default on a UMA box; 0.85 of ~124 GiB means reclaim starts at
# ~105 GiB. expandable_segments is the fragmentation half. Safe with
# --enforce-eager (no graph capture), which is what first light runs.
export PYTORCH_HIP_ALLOC_CONF="${PYTORCH_HIP_ALLOC_CONF:-expandable_segments:True,garbage_collection_threshold:0.85}"

# --- HSA ---------------------------------------------------------------------
# TheRock ROCm busy-polls (rocr InterruptSignal::WaitRelaxed) otherwise, i.e.
# ~2-3 cores spinning during inference and CPU thermal throttling on a pair
# whose PM QoS is held for latency. Set FN_HSA_INTERRUPT=0 to revert to spin.
export HSA_ENABLE_INTERRUPT="${FN_HSA_INTERRUPT:-1}"

# --- compiler caches under the STATE directory, never tmpfs ------------------
# The defaults land under /tmp (tmpfs), and the first bring-up after a boot
# then recompiles every kernel — ~25 min CPU-bound in LLVM before the API can
# answer. The state directory is on-disk on both nodes.
mkdir -p "$FN_STATE_DIR/torchinductor" "$FN_STATE_DIR/triton" 2>/dev/null || true
export TORCHINDUCTOR_CACHE_DIR="${TORCHINDUCTOR_CACHE_DIR:-$FN_STATE_DIR/torchinductor}"
export TRITON_CACHE_DIR="${TRITON_CACHE_DIR:-$FN_STATE_DIR/triton}"

# --- NCCL: the transport-of-record block --------------------------------------
# Socket transport on both rails is the overnight transport of record (spec
# ruling P7); the RDMA package under host/rdma is operator-attended only, and
# only after a committed socket-transport benchmark is banked.
#
# NCCL_SOCKET_IFNAME is COMPUTED from `ip -br addr`: only Thunderbolt rails
# that actually carry a routable /30 IP qualify. Rail 0 (thunderbolt0,
# 10.99.0.x) is guaranteed; rail 1 (thunderbolt1) is trained but
# IP-unconfigured today (dotfiles modules/lowlat-cluster.nix parks it) and
# MUST NOT be listed until it has a peer IP — a peerless link-local rail in
# the list hangs RCCL bootstrap. We log which rails we chose rather than
# hardcoding both.
fn_choose_rails() {
  local rail addr peer chosen=""
  for rail in thunderbolt0 thunderbolt1; do
    if [ ! -e "/sys/class/net/$rail" ]; then
      echo "fn-env: rail $rail absent on this node; not listed" >&2
      continue
    fi
    addr="$(ip -br -4 addr show dev "$rail" 2>/dev/null \
      | grep -oE '([0-9]{1,3}\.){3}[0-9]{1,3}' \
      | grep -Ev '^(169\.254\.|127\.)' | head -n1 || true)"
    if [ -z "$addr" ]; then
      echo "fn-env: rail $rail NOT listed (no routable peer IP; a peerless rail in NCCL_SOCKET_IFNAME hangs RCCL bootstrap)" >&2
      continue
    fi
    # Peer-reachability gate: a rail can carry a configured /30 address while
    # its peer path is dark (measured 2026-08-30: coordinator thunderbolt0 UP
    # with 10.99.0.1/30 and the peer unreachable). A dark-but-addressed rail
    # in NCCL_SOCKET_IFNAME hangs RCCL bootstrap exactly like a peerless one.
    # Three packets, ANY reply accepts: a cold neighbour cache drops the
    # first probe ~10% of the time on a just-healed rail — a one-packet gate
    # would flap on exactly the rail we most want listed.
    peer="$(printf '%s' "$addr" | awk -F. '{ o=$4; $4=(o==1)?2:1; printf "%d.%d.%d.%d", $1,$2,$3,$4 }')"
    if ping -c3 -W1 "$peer" >/dev/null 2>&1; then
      chosen="${chosen:+$chosen,}$rail"
      echo "fn-env: rail $rail LISTED in NCCL_SOCKET_IFNAME ($addr is routable, peer $peer answers)" >&2
    else
      echo "fn-env: rail $rail NOT listed (addr configured but peer unreachable — a dark rail in NCCL_SOCKET_IFNAME hangs RCCL bootstrap)" >&2
    fi
  done
  printf '%s' "$chosen"
}
export NCCL_SOCKET_IFNAME="${NCCL_SOCKET_IFNAME:-$(fn_choose_rails)}"
if [ -z "$NCCL_SOCKET_IFNAME" ]; then
  if [ "${FN_ALLOW_WIRE_FALLBACK:-1}" = "1" ]; then
    # TERMINAL FALLBACK RUNG: the 5GbE wire. Degraded (~87 us RTT vs the
    # rail's ~15 us) but a working transport beats a phantom one; the rung is
    # recorded into every receipt so a wire night can never be mistaken for a
    # rail night (a wire-fallback bench.json does NOT satisfy the rail-sockets
    # Gate 0 in host/rdma/attended-bringup.md). Never the second rail, never
    # verbs — those rungs do not exist in the unattended ladder.
    echo "fn-env: WARNING: no Thunderbolt rail is listable; TERMINAL FALLBACK to the 5GbE wire enp191s0 (degraded, receipted)" >&2
    export NCCL_SOCKET_IFNAME=enp191s0
    export FN_TRANSPORT_RUNG=wire-fallback
  else
    echo "fn-env: FATAL: no Thunderbolt rail carries a reachable peer and FN_ALLOW_WIRE_FALLBACK=0; refusing to name a phantom transport" >&2
    return 1 2>/dev/null || exit 1
  fi
fi
export FN_TRANSPORT_RUNG="${FN_TRANSPORT_RUNG:-rail0-sockets}"

# NCCL_IB_DISABLE=1 UNCONDITIONALLY. Measured truth 2026-08-30: NO ibverbs
# device exists on either node tonight (/sys/class/infiniband is empty) and
# nothing is staged for the running 7.2.2 kernels. The pin stays
# unconditional anyway, so that WHEN a verbs device appears (the attended
# morning bring-up under host/rdma) RCCL still cannot silently ride unproven
# RDMA. Sockets stay the transport of record until the attended morning A/B
# lands a counterbalanced verdict (host/rdma/ab-protocol.md). Do NOT
# conditionalize this on device detection.
export NCCL_IB_DISABLE=1

# Cold-kernel-cache bring-up is ~25 min of LLVM before the first collective;
# the collective timeouts must survive that without a watchdog kill.
export TORCH_NCCL_HEARTBEAT_TIMEOUT_SEC="${TORCH_NCCL_HEARTBEAT_TIMEOUT_SEC:-2400}"
export TORCH_NCCL_ENABLE_MONITORING="${TORCH_NCCL_ENABLE_MONITORING:-0}"
export NCCL_TIMEOUT_MS="${NCCL_TIMEOUT_MS:-2400000}"

# --- ray ---------------------------------------------------------------------
# Propagate FN_* to the worker rank through ray. ESSENTIAL: without this the
# two TP ranks diverge on every FN_ knob above.
export VLLM_RAY_EXTRA_ENV_VAR_PREFIXES_TO_COPY=FN_
# Ray and ROCr visible-device handling fight on multi-rank boxes; opt out.
export RAY_EXPERIMENTAL_NOSET_ROCR_VISIBLE_DEVICES="${RAY_EXPERIMENTAL_NOSET_ROCR_VISIBLE_DEVICES:-1}"
# On a 128 GB unified-memory box the serve legitimately claims most memory;
# ray's OOM monitor must not reap the rank. Refresh 0 disables the monitor.
export RAY_memory_monitor_refresh_ms="${RAY_memory_monitor_refresh_ms:-0}"
# `ray start` pre-starts one idle python worker per CPU (~40 MB each); on a
# 32-thread box that is over a gigabyte vLLM never touches. Cap the pool
# small; fn-cluster-up.sh passes this as --num-cpus on both nodes.
export RAY_NUM_CPUS="${RAY_NUM_CPUS:-4}"
# One GPU per node, DECLARED rather than autodetected: ray's AMD accelerator
# probe does not enumerate this gfx1151 APU, so `ray start` without it brings up
# a cluster with no GPU resource at all and fn-cluster-up.sh's two-GPU gate
# refuses to serve. RAY_-prefixed so it rides the doctrine env both ranks
# byte-compare in fn-preflight.sh.
export RAY_NUM_GPUS="${RAY_NUM_GPUS:-1}"

# --- the table path ------------------------------------------------------------
# Serve the engram table from NVMe via mmap: page-cache faults serve gathers,
# zero table bytes GPU-resident (spec ruling P11's zero-GPU-residency bound
# leans on this). VLLM_PLE_MMAP=0 is the fork's kill switch.
export VLLM_PLE_MMAP=1

# --- shared helpers -----------------------------------------------------------
# Reap stranded serve processes on the CALLING node. Lives here (not in
# fn-cluster-up.sh) so bench/run-matrix.sh can reuse it for its per-arm
# cross-node reap: a rank holding 60–100 GiB of GTT OOMs the next arm. The
# serve process tree is visible from the host /proc even when it runs inside
# a podman container; the bracket keeps pgrep from matching itself.
reap_serve_node() {
  local pids
  pids="$(pgrep -f 'bin/[v]llm serve' || true)"
  if [ -n "$pids" ]; then
    echo "reap_serve_node: reaping stranded serve pids: $(echo $pids | tr '\n' ' ')" >&2
    kill -TERM $pids 2>/dev/null || true
    sleep 3
    pids="$(pgrep -f 'bin/[v]llm serve' || true)"
    [ -z "$pids" ] || kill -KILL $pids 2>/dev/null || true
    sleep 1
  fi
  # Count the residue WITHOUT letting the healthy case abort the caller:
  # `pgrep` exits 1 when nothing matches, and under the caller's
  # `set -o pipefail` a bare `pgrep | wc -l` propagates that 1 out of the
  # command substitution, so `set -e` killed fn-cluster-up.sh at its FIRST
  # reap on exactly the nights nothing was stranded — silently, before the
  # gate could log a word. Branch on the pid list instead of piping.
  pids="$(pgrep -f 'bin/[v]llm serve' || true)"
  if [ -z "$pids" ]; then echo 0; else echo "$pids" | wc -l; fi
}

# --- estate hygiene -------------------------------------------------------------
# Runtime hub downloads stay forbidden (spec F.6): the checkpoint is staged
# library-to-node, and the engine must never reach for the hub at runtime.
export HF_HUB_OFFLINE="${HF_HUB_OFFLINE:-1}"
export VLLM_NO_USAGE_STATS="${VLLM_NO_USAGE_STATS:-1}"
export VLLM_DO_NOT_TRACK="${VLLM_DO_NOT_TRACK:-1}"
