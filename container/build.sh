#!/usr/bin/env bash
# Container engine build — flashnext:dev (the ruling P4 container lane).
#
# Order per the task: packaging tests first, then the image build, then the
# build receipt. Any error lands a receipt with status "fail" — the receipts
# gate grades this step, so failure is recorded, never silent.
#
# Overnight-iteration hooks (see container/Containerfile header):
#   FN_VLLM_SRC=/path/to/vllm-checkout   bind-mount the fork source over
#                                        /opt/vllm; the editable install's
#                                        build artifacts persist there, so a
#                                        one-line patch rebuild recompiles
#                                        only what changed.
#   FN_CCACHE_DIR=/path/to/ccache        host compiler cache (default
#                                        ~/.cache/flashnext/ccache — always
#                                        OUTSIDE the repo, so the worktree
#                                        stays clean), mounted at /root/.ccache.
#   FN_PIP_CACHE_DIR=/path/to/pip        host pip cache (default
#                                        ~/.cache/flashnext/pip), mounted at
#                                        /root/.cache/pip.
#   FN_IMAGE=name:tag                    image tag (default flashnext:dev).
#
# Enter the image for edit-and-restart iteration without a rebuild:
#   podman run --rm -it --device /dev/kfd --device /dev/dri \
#     --security-opt seccomp=unconfined --ipc=host \
#     -v "$FN_VLLM_SRC:/opt/vllm" flashnext:dev bash
#   # inside: edit, then `pip install -e /opt/vllm --no-build-isolation`
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

IMAGE="${FN_IMAGE:-flashnext:dev}"
OUT="$REPO_ROOT/results/receipts/build.json"
# Host cache defaults live OUTSIDE the repository so nothing outside the
# container/ and results/receipts/ write boundary is ever created or left
# untracked in the worktree. Override with FN_CCACHE_DIR / FN_PIP_CACHE_DIR.
HOST_CACHE_BASE="${XDG_CACHE_HOME:-$HOME/.cache}/flashnext"
CCACHE_DIR_HOST="${FN_CCACHE_DIR:-$HOST_CACHE_BASE/ccache}"
PIP_CACHE_HOST="${FN_PIP_CACHE_DIR:-$HOST_CACHE_BASE/pip}"

STATUS="pass"
TORCH="" TRITON="" FORK_COMMIT="" IMAGE_DIGEST=""
START_EPOCH="$(date +%s)"

write_receipt() {
    local status="$1"
    local wall=$(( $(date +%s) - START_EPOCH ))
    mkdir -p "$(dirname "$OUT")"
    FN_STATUS="$status" FN_TS="$(date -u +%FT%TZ)" FN_WALL="$wall" \
    FN_TORCH="$TORCH" FN_TRITON="$TRITON" FN_FORK="$FORK_COMMIT" \
    FN_DIGEST="$IMAGE_DIGEST" FN_IMAGE_NAME="$IMAGE" python3 - "$OUT" <<'PY'
import json, os, sys
r = {
    "step": "build",
    "status": os.environ["FN_STATUS"],
    "ts": os.environ["FN_TS"],
    "data": {
        "lane": "container",
        "image": os.environ["FN_IMAGE_NAME"],
        "torch": os.environ["FN_TORCH"] or None,
        "triton": os.environ["FN_TRITON"] or None,
        "fork_commit": os.environ["FN_FORK"] or None,
        "image_digest": os.environ["FN_DIGEST"] or None,
        "wall_clock_s": int(os.environ["FN_WALL"]),
    },
}
json.dump(r, open(sys.argv[1], "w"), indent=1)
print(f"build receipt ({os.environ['FN_STATUS']}): {sys.argv[1]}")
PY
}

on_error() {
    echo "container/build.sh: FAILED — writing fail receipt" >&2
    STATUS="fail"
    write_receipt "fail" || true
    exit 1
}
trap on_error ERR

echo "==> packaging tests first"
python3 -m unittest discover -s "$REPO_ROOT/tests"

echo "==> rootfs overlay stage (instruments land here when present)"
mkdir -p "$REPO_ROOT/container/rootfs"
[ -e "$REPO_ROOT/container/rootfs/.keep" ] || touch "$REPO_ROOT/container/rootfs/.keep"

echo "==> caches"
mkdir -p "$CCACHE_DIR_HOST" "$PIP_CACHE_HOST"

BUILD_ARGS=(--tag "$IMAGE" --file container/Containerfile
            --volume "$CCACHE_DIR_HOST:/root/.ccache"
            --volume "$PIP_CACHE_HOST:/root/.cache/pip")
if [ -n "${FN_VLLM_SRC:-}" ]; then
    echo "==> bind-mounting fork checkout: $FN_VLLM_SRC -> /opt/vllm"
    BUILD_ARGS+=(--volume "$FN_VLLM_SRC:/opt/vllm")
fi

echo "==> podman build $IMAGE"
podman build "${BUILD_ARGS[@]}" "$REPO_ROOT"

echo "==> reading build identity from the image"
BUILD_INFO="$(podman run --rm --entrypoint /usr/bin/env "$IMAGE" \
    cat /opt/flashnext/build-info.json)"
echo "$BUILD_INFO"
TORCH="$(echo "$BUILD_INFO" | python3 -c 'import json,sys; print(json.load(sys.stdin)["torch"])')"
TRITON="$(echo "$BUILD_INFO" | python3 -c 'import json,sys; print(json.load(sys.stdin)["triton"])')"
FORK_COMMIT="$(echo "$BUILD_INFO" | python3 -c 'import json,sys; print(json.load(sys.stdin)["fork_commit"])')"

IMAGE_DIGEST="$(podman inspect --format '{{.Digest}}' "$IMAGE" 2>/dev/null || true)"
if [ -z "$IMAGE_DIGEST" ]; then
    # Local builds carry no registry digest; the image id is the stable name.
    IMAGE_DIGEST="$(podman inspect --format '{{.Id}}' "$IMAGE")"
fi

write_receipt "$STATUS"
python3 -c "import json,sys; sys.exit(0 if json.load(open('$OUT'))['status']=='pass' else 1)"
echo "==> flashnext:dev built; receipt: $OUT"
