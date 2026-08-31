#!/usr/bin/env bash
# bench/gloo-allreduce-run.sh <iface> <master_ip_on_that_iface> <worker_ip_unused> <port> [extra args]
# Launches rank 0 in the coordinator's flashnext-pair container and rank 1 in the
# worker's, streaming bench/gloo-allreduce-bench.py in over stdin (never a copy
# on the worker). GLOO_SOCKET_IFNAME is passed with -e into BOTH containers.
set -uo pipefail
IFACE="$1"; MASTER="$2"; PORT="$3"; shift 3
EXTRA=("$@")
SRC="$(dirname "$(readlink -f "$0")")/gloo-allreduce-bench.py"
OUT="${FN_GLOO_OUT:-/tmp/fn-gloo}"; mkdir -p "$OUT"

ssh -o BatchMode=yes 10.99.9.2 \
  "podman exec -i -e GLOO_SOCKET_IFNAME=$IFACE -e TP_SOCKET_IFNAME=$IFACE flashnext-pair \
   python3 - --rank 1 --master-addr $MASTER --master-port $PORT --iface $IFACE ${EXTRA[*]}" \
  < "$SRC" > "$OUT/rank1.$IFACE.log" 2>&1 &
W=$!
podman exec -i -e GLOO_SOCKET_IFNAME="$IFACE" -e TP_SOCKET_IFNAME="$IFACE" flashnext-pair \
  python3 - --rank 0 --master-addr "$MASTER" --master-port "$PORT" --iface "$IFACE" "${EXTRA[@]}" \
  < "$SRC" > "$OUT/rank0.$IFACE.log" 2>&1
R0=$?
wait $W; R1=$?
echo "rank0=$R0 rank1=$R1"
grep -h GLOOBENCH "$OUT/rank0.$IFACE.log" "$OUT/rank1.$IFACE.log" || tail -20 "$OUT/rank0.$IFACE.log" "$OUT/rank1.$IFACE.log"
