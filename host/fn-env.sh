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

# --- gloo: the CPU process group ----------------------------------------------
# Every rank stands up TWO process groups, not one: the device group on RCCL
# (the NCCL_ block above) and a CPU group on gloo. The fork builds the world
# group as backend="cpu:gloo,cuda:nccl" (vllm/distributed/parallel_state.py
# :1504) and every GroupCoordinator carries both handles (:403-404), so a CPU
# group that cannot form kills engine-core init before a single weight loads.
#
# WITHOUT THIS PIN THE CPU GROUP NEVER FORMS ON A NIXOS BOX. torch's
# ProcessGroupGloo::createDefaultDevice() reads GLOO_SOCKET_IFNAME first;
# unset, it falls back to gethostname() and binds whatever that name resolves
# to, warning ONLY if the name resolves to nothing at all. NixOS writes each
# machine's own name into /etc/hosts on 127.0.0.2 — measured 2026-08-31 inside
# the serve image itself: `socket.gethostbyname("coordinator")` -> 127.0.0.2
# on this node and `worker` -> 127.0.0.2 on the other. The name DOES resolve,
# so torch prints no warning whatsoever and both ranks silently advertise
# LOOPBACK; the failure surfaces minutes later, deep in engine-core init, as
#   Gloo connectFullMesh failed with [gloo/transport/tcp/pair.cc:152] timed
#   out connecting: SO_ERROR: Connection refused, remote=[127.0.0.2]:2485,
#   rank=0, size=2
# — an address no peer can ever reach across the pair. Reproduced and then
# cleared ON DEMAND, both directions, with a two-rank gloo all-reduce inside
# the existing containers (no ray, no vLLM, no model, no GPU): run
# `torch.distributed.init_process_group("gloo")` + `all_reduce` under
# MASTER_ADDR=10.99.1.1 WORLD_SIZE=2 with RANK=0 on the coordinator and
# RANK=1 on the worker. Unpinned it fails in ~35 s with exactly the string
# above; pinned it all-reduces 3.0 on both ranks in ~20 s. Note that BOTH
# ranks still print `gethostname() -> 127.0.0.2` when the pin is applied —
# the /etc/hosts mapping is untouched, the pin simply stops gloo consulting
# it. That is the signature to look for.
#
# THE WIRE, NOT A RAIL — the one place the ds4 estate's env is deliberately
# NOT copied. ds4-cluster-env.sh:30 pins GLOO_SOCKET_IFNAME=thunderbolt0, the
# same interface as its tensor transport, with no rationale recorded. Three
# measured reasons to put ours on the 5GbE wire instead:
#
#   1. The CPU group carries NO tensor volume on this serve. The TP all-reduce
#      runs on the DEVICE group through pynccl / the custom all-reduce
#      (vllm/distributed/device_communicators/cuda_communicator.py:278ff).
#      The CPU group carries pickled metadata (broadcast_object), barriers
#      (parallel_state.py:1202-1207, which explicitly refuses the device
#      group), and the one-shot handle exchanges that set up the RCCL
#      communicator and the TP message queue (:518-520). The only tensor path
#      that would ride it — broadcast_tensor_dict's is_cpu branch — belongs to
#      the PIPELINE group, which is world_size 1 here and returns immediately
#      (gpu_model_runner.py:4753 is its sole caller). Kilobytes at bring-up,
#      not gigabytes per decode step.
#   2. Wire and rail are the same latency tonight anyway: measured 2026-08-31,
#      ping to the wire peer 10.99.1.2 is 97 us against 100 us to the rail
#      peer 10.99.0.2. Metadata on the wire costs nothing measurable, and it
#      keeps gloo's barriers off a rail whose RCCL transport of record is
#      plain sockets sharing that same single TCP path.
#   3. AVAILABILITY, the decisive one. NCCL_SOCKET_IFNAME above is COMPUTED
#      and can drop to the enp191s0 wire-fallback rung on a night when no rail
#      has a reachable peer. A gloo pin hardcoded to thunderbolt0 would then
#      leave the CPU group dialling a dark rail while RCCL ran on the wire —
#      and gloo has no verbs alternative and no second transport, so that
#      failure is another silent connectFullMesh timeout with no diagnostic.
#      The wire is the interface the whole bring-up already depends on: every
#      `ssh $FN_WORKER_HOST` in fn-cluster-up.sh routes 10.99.9.2 via
#      10.99.1.2 dev enp191s0. Pinning gloo there makes the CPU group's fate
#      independent of rail health, and honours fn-env.sh's own doctrine above
#      (dotfiles-observed.md 2.3: the rails carry tensors only).
#
# A NAME, NOT AN ADDRESS: gloo binds the interface's own IPv4 (10.99.1.1 here,
# 10.99.1.2 there — a directly connected /30), so ONE interface name is
# correct on BOTH ranks and byte-diffs clean in fn-preflight.sh.
#
# EXACTLY ONE NAME: unlike NCCL_SOCKET_IFNAME, which takes a comma list,
# GLOO_SOCKET_IFNAME is a single interface-name lookup — a comma in it is not
# a list, it is a name no interface has, and gloo fails on it. So the
# rail-fallback branch below must take ${VAR%%,*}: fn_choose_rails above
# accumulates `chosen="${chosen:+$chosen,}$rail"` and WILL emit
# `thunderbolt0,thunderbolt1` the day rail 1 gets a /30 (dotfiles
# modules/lowlat-cluster.nix parks it today; the moment it is unparked this
# is live). NEVER point this at `lo` either: the fleet identity 10.99.9.x/32
# lives there beside 127.0.0.1 and gloo would be free to pick the loopback all
# over again — fn-cluster-up.sh rejects `lo` explicitly for that reason.
export FN_WIRE_IFNAME="${FN_WIRE_IFNAME:-enp191s0}"
if [ -n "${GLOO_SOCKET_IFNAME:-}" ]; then
  # Already decided: fn-cluster-up.sh and fn-preflight.sh inject the
  # COORDINATOR's choice into the worker's sourcing as a pre-set literal, the
  # same way they inject NCCL_SOCKET_IFNAME, so one interface decision governs
  # both ranks. Honour it and probe nothing — re-deriving here is how the two
  # ranks come to disagree.
  export GLOO_SOCKET_IFNAME
elif [ -e "/sys/class/net/$FN_WIRE_IFNAME" ]; then
  export GLOO_SOCKET_IFNAME="$FN_WIRE_IFNAME"
else
  # No wire on this node: fall back to the first rail the chooser above
  # already proved carries a reachable peer. A stat, never a probe — the host
  # tooling tests source this file with the rail chooser short-circuited
  # (tests/test_host_tooling.py's _run_reap presets NCCL_SOCKET_IFNAME so
  # sourcing never shells out to ip/ping) and must not acquire a second such
  # dependency here.
  echo "fn-env: WARNING: wire $FN_WIRE_IFNAME absent on this node; GLOO_SOCKET_IFNAME falls back to the first listed rail" >&2
  export GLOO_SOCKET_IFNAME="${NCCL_SOCKET_IFNAME%%,*}"
fi

# --- ray ---------------------------------------------------------------------
# Propagate FN_* AND GLOO_* to the worker rank through ray. ESSENTIAL: without
# this the two TP ranks diverge on every FN_ knob above -- and on the gloo pin.
# vllm/ray/ray_env.py:36-44 hardcodes the prefixes get_env_vars_to_copy()
# replays into a ray actor: {VLLM_, FLASH_ATTENTION_, LMCACHE_, NCCL_, UCX_,
# HF_, HUGGING_FACE_}. NCCL_ is in that set; GLOO_ IS NOT, so the gloo pin
# needs the same explicit carry FN_ already has. The value is parsed as CSV
# (ray_env.py:51-53, 83-85), hence the comma.
#
# READ THIS BEFORE REASONING FROM THIS LINE: it is a BACKSTOP here, not the
# carrier. VLLM_USE_RAY_V2_EXECUTOR_BACKEND defaults to 1 (vllm/envs.py
# :920-922), so `--distributed-executor-backend ray` selects RayExecutorV2
# (v1/executor/abstract.py:61-67) and vllm/v1/executor/ray_executor.py is DEAD
# CODE on this stack — do not cite it. RayExecutorV2 does NOT consult this
# prefix list at all: it copies ALL of os.environ minus WORKER_SPECIFIC_ENV_VARS
# (v1/executor/ray_env_utils.py get_driver_env_vars, called at
# ray_executor_v2.py:361-363) and applies it with os.environ.setdefault
# (:153-155), so a worker that already carries the pin keeps its own value and
# a worker that does not gets the driver's. The prefix list still governs the
# RayDistributedExecutor path (ray_executor.py:322-326, one env flip away) and
# the ray core-engine actor manager (v1/engine/utils.py:437-443), and both
# would silently drop GLOO_ without this.
#
# What ACTUALLY carries the pin to rank 1 tonight is the container env file:
# fn-cluster-up.sh's ENV_FILTER -> podman --env-file -> `ray start` -> the
# RayWorkerProc actor. That is why fn-cluster-up.sh asserts the pin's presence
# in the built file rather than trusting this line.
#
# Bare export, not ${VAR:-...}: this is doctrine, not a tuning knob, and
# tests/test_host_tooling.py greps for the literal prefix of this line.
export VLLM_RAY_EXTRA_ENV_VAR_PREFIXES_TO_COPY=FN_,GLOO_
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
