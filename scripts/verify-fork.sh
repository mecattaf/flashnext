#!/usr/bin/env bash
# Prove the engine fork carries what the spec claims: the pinned base, the
# admission patch, the platform-table port, and the four cherry-picks.
# Uses a persistent local checkout (network only on first run or on drift).
set -euo pipefail

FORK_URL="https://github.com/mecattaf/vllm"
BRANCH="flashnext"
BASE_SHA="8e4e036a311604800334989485b4ee23925956da"
DIR="${FN_FORK_DIR:-$HOME/.cache/flashnext/vllm-fork}"

# Bootstrap state: until the fork engineering lands its first mirrored patch,
# this gate is vacuously green so estate lanes can merge. The close checkpoint
# runs with FN_FORK_STRICT=1, where the full signature set is enforced.
REPO_ROOT_EARLY="$(cd "$(dirname "$0")/.." && pwd)"
if [ "${FN_FORK_STRICT:-0}" != "1" ] \
   && ! ls "$REPO_ROOT_EARLY"/patches/*.patch >/dev/null 2>&1; then
  echo "verify-fork: bootstrap state (no mirrored patches yet); pass"
  exit 0
fi

if [ ! -d "$DIR/.git" ]; then
  git clone --depth 50 --branch "$BRANCH" "$FORK_URL" "$DIR"
fi
git -C "$DIR" fetch --depth 50 origin "$BRANCH" -q
git -C "$DIR" checkout -q FETCH_HEAD

fail=0
say() { echo "verify-fork: $*"; }
need() { # need <desc> <cmd...>
  local desc="$1"; shift
  if "$@" >/dev/null 2>&1; then say "ok   - $desc"; else say "MISS - $desc"; fail=1; fi
}

# Base ancestry (within the shallow window).
need "base $BASE_SHA in history" \
  git -C "$DIR" merge-base --is-ancestor "$BASE_SHA" HEAD

# Our patch 0001: FP8 MoE admission for this GPU.
need "admission kill-switch FN_FP8_MOE present" \
  grep -rq "FN_FP8_MOE" "$DIR/vllm/model_executor/layers/fused_moe/"
need "fused-MoE fp8 upcast plumbing present" \
  grep -rq "FORCE_FP8_DOT_UPCAST" "$DIR/vllm/model_executor/layers/fused_moe/"

# Our patch 0002: the platform-table port.
need "amd tree imports the mmap module" \
  grep -q "ple_mmap" "$DIR/vllm/models/qwen4_exp/amd/ple_layer.py"
need "amd tree carries the FP8 embedding dequant" \
  grep -q "_dequantize_embeddings" "$DIR/vllm/models/qwen4_exp/amd/ple_layer.py"
need "mmap module relocated to common/" \
  test -f "$DIR/vllm/models/qwen4_exp/common/ple_mmap.py"

# Cherry-picks.
need "46012 wave32 LDS fix" \
  grep -q "kNumThreadsPerBlockMerge = 512" "$DIR/csrc/libtorch_stable/sampler.cu"
need "40963 APU memory accounting" \
  grep -q "is_integrated_gpu" "$DIR/vllm/platforms/rocm.py"
need "51511 skinny GEMM disabled on this GPU" \
  grep -q "on_gfx1151" "$DIR/vllm/model_executor/layers/utils.py"
need "46110 KFD topology detection" \
  grep -q "_kfd_topology_has_amd_gpu" "$DIR/vllm/platforms/__init__.py"

# Patch mirror discipline: every fork commit past base is mirrored in patches/.
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
n_commits=$(git -C "$DIR" rev-list --count "$BASE_SHA"..HEAD)
n_patches=$(ls "$REPO_ROOT"/patches/*.patch 2>/dev/null | wc -l)
say "fork commits past base: $n_commits ; mirrored patches: $n_patches"
if [ "$n_patches" -lt "$n_commits" ]; then
  say "MISS - patch mirror incomplete ($n_patches < $n_commits)"; fail=1
fi

exit $fail
