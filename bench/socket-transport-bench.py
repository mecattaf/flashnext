#!/usr/bin/env python3
"""bench/socket-transport-bench.py — the socket-transport benchmark the project never took.

Measures TCP round-trip latency and stream throughput between the twins on a
SPECIFIC, EXPLICITLY PINNED interface, so that the transport-of-record cell in
docs/DECISIONS-2026-08-30.md line 168 ("RCCL sockets over thunderbolt0 |
~150-300 us | INFERRED") can be replaced with a measurement.

Why a hand-rolled harness: neither node has iperf3, netperf, sockperf or qperf,
and nothing may be installed. Stdlib only.

Why latency and not bandwidth is the headline: the decision is TP=2 decode-step
allreduce, which at Qwen3.8-Flash-Next hidden sizes moves a few tens of KiB per
step. That regime is round-trip-bound, not bandwidth-bound.

Interface pinning
-----------------
The client binds its SOURCE address explicitly (``--bind-src``) and the server
binds its LISTEN address explicitly (``--bind``). Both rails are /30s, so a
bound source address selects the interface unambiguously. The harness does not
take that on faith: it samples ``/sys/class/net/<dev>/statistics/{rx,tx}_packets``
for EVERY netdev before and after each transport's sweep, and the receipt
carries the deltas. If the traffic had gone out the wifi or the wrong rail, the
counters would say so.

Roles
-----
``--role server``  echo/sink/source responder. Launched on the worker by
                   streaming THIS FILE over the control wire
                   (``ssh 10.99.9.2 python3 - --role server ...``).
``--role client``  drives the schedule and prints one JSON report on stdout.

Wire protocol (one persistent TCP connection, exact-length framing only — no
line buffering, so control frames can never eat payload bytes):

    frame  := 8-byte zero-padded ASCII length || JSON body
    ops    := {"op":"echo","size":N,"iters":K}    server: K x (read N, write N)
              {"op":"sink","bytes":B,"chunk":C}   server: read B, then write 8-byte ack
              {"op":"source","bytes":B,"chunk":C} server: write B
              {"op":"quit"}

Stdlib only. Touches no configfs, no Thunderbolt device node, no container, and
no serve. Read-only with respect to every piece of system state except the
sockets it opens.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import socket
import statistics
import struct
import sys
import time

HDR = 8
ACK = b"ACKACKAC"


# ---------------------------------------------------------------------------
# framing
# ---------------------------------------------------------------------------

def send_cmd(sock: socket.socket, obj: dict) -> None:
    body = json.dumps(obj, separators=(",", ":")).encode()
    sock.sendall(b"%08d" % len(body) + body)


def recv_exact(sock: socket.socket, n: int, buf: bytearray | None = None) -> bytes:
    if buf is None:
        buf = bytearray(n)
    view = memoryview(buf)[:n]
    got = 0
    while got < n:
        r = sock.recv_into(view[got:], n - got)
        if r == 0:
            raise ConnectionError("peer closed mid-frame")
        got += r
    return buf


def recv_cmd(sock: socket.socket) -> dict:
    n = int(bytes(recv_exact(sock, HDR)).decode())
    return json.loads(bytes(recv_exact(sock, n)).decode())


def tune(sock: socket.socket) -> None:
    """TCP_NODELAY is load-bearing: without it Nagle would fuse the ping-pong
    writes and the small-size numbers would be delayed-ACK artefacts."""
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)


# ---------------------------------------------------------------------------
# netdev counters — the interface-pinning proof
# ---------------------------------------------------------------------------

def netdev_counters() -> dict:
    out = {}
    for dev in sorted(os.listdir("/sys/class/net")):
        st = pathlib.Path("/sys/class/net") / dev / "statistics"
        try:
            out[dev] = {
                k: int((st / f"{k}_packets").read_text())
                for k in ("rx", "tx")
            }
            out[dev].update({
                f"{k}_bytes": int((st / f"{k}_bytes").read_text())
                for k in ("rx", "tx")
            })
        except OSError:
            continue
    return out


def counter_delta(before: dict, after: dict) -> dict:
    d = {}
    for dev in after:
        if dev not in before:
            continue
        row = {k: after[dev][k] - before[dev][k] for k in after[dev]}
        if any(v for v in row.values()):
            d[dev] = row
    return d


# ---------------------------------------------------------------------------
# server
# ---------------------------------------------------------------------------

def run_server(bind: str, port: int) -> None:
    lsock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    lsock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    lsock.bind((bind, port))
    lsock.listen(1)
    print(json.dumps({"role": "server", "listen": lsock.getsockname()}), flush=True)
    conn, peer = lsock.accept()
    tune(conn)
    lsock.close()
    before = netdev_counters()
    buf = bytearray(1 << 20)
    try:
        while True:
            cmd = recv_cmd(conn)
            op = cmd["op"]
            if op == "quit":
                break
            if op == "echo":
                n, k = cmd["size"], cmd["iters"]
                for _ in range(k):
                    recv_exact(conn, n, buf)
                    conn.sendall(memoryview(buf)[:n])
            elif op == "sink":
                total, chunk = cmd["bytes"], cmd["chunk"]
                got = 0
                view = memoryview(buf)[:chunk]
                while got < total:
                    r = conn.recv_into(view, min(chunk, total - got))
                    if r == 0:
                        raise ConnectionError("peer closed in sink")
                    got += r
                conn.sendall(ACK)
            elif op == "source":
                total, chunk = cmd["bytes"], cmd["chunk"]
                payload = bytes(chunk)
                sent = 0
                while sent < total:
                    m = min(chunk, total - sent)
                    conn.sendall(payload[:m])
                    sent += m
                recv_exact(conn, HDR, buf)
            else:
                raise ValueError(f"unknown op {op}")
    finally:
        after = netdev_counters()
        report = {
            "role": "server",
            "outcome": "ok",
            "local": conn.getsockname(),
            "peer": list(peer),
            "netdev_delta": counter_delta(before, after),
        }
        try:
            conn.close()
        except OSError:
            pass
        print(json.dumps(report), flush=True)


# ---------------------------------------------------------------------------
# client
# ---------------------------------------------------------------------------

def pct(xs: list[float], q: float) -> float:
    """Nearest-rank percentile on a pre-sorted list. No interpolation: with
    >=5000 samples the rank is what we want to report."""
    if not xs:
        return float("nan")
    i = min(len(xs) - 1, max(0, int(round(q / 100.0 * len(xs) + 0.5)) - 1))
    return xs[i]


def echo_phase(sock: socket.socket, size: int, iters: int, warmup: int) -> dict:
    send_cmd(sock, {"op": "echo", "size": size, "iters": iters + warmup})
    payload = os.urandom(size)
    buf = bytearray(size)
    perf = time.perf_counter_ns
    samples = []
    for i in range(iters + warmup):
        t0 = perf()
        sock.sendall(payload)
        recv_exact(sock, size, buf)
        t1 = perf()
        if i >= warmup:
            samples.append((t1 - t0) / 1000.0)  # microseconds
    samples.sort()
    return {
        "size_b": size,
        "iters": iters,
        "warmup": warmup,
        "rtt_us": {
            "min": round(samples[0], 2),
            "p50": round(pct(samples, 50), 2),
            "p90": round(pct(samples, 90), 2),
            "p99": round(pct(samples, 99), 2),
            "p999": round(pct(samples, 99.9), 2),
            "max": round(samples[-1], 2),
            "mean": round(statistics.fmean(samples), 2),
            "stdev": round(statistics.pstdev(samples), 2),
        },
        "one_way_us_p50": round(pct(samples, 50) / 2.0, 2),
        # Coarse 25 us histogram. The TB rail's RTT turned out to be strongly
        # MULTI-MODAL rather than a smooth distribution, which is the whole
        # story of this benchmark; a p50 alone would have hidden it.
        "hist_25us": _hist(samples, 25.0, 24),
    }


def _hist(samples: list[float], width: float, nbins: int) -> dict:
    bins: dict[str, int] = {}
    for s in samples:
        b = int(s // width)
        key = f"{int(b * width)}" if b < nbins else f"{int(nbins * width)}+"
        bins[key] = bins.get(key, 0) + 1
    return {k: v for k, v in bins.items() if v}


def throughput_tx(sock: socket.socket, total: int, chunk: int) -> dict:
    send_cmd(sock, {"op": "sink", "bytes": total, "chunk": chunk})
    payload = bytes(chunk)
    sent = 0
    t0 = time.perf_counter_ns()
    while sent < total:
        m = min(chunk, total - sent)
        sock.sendall(payload[:m])
        sent += m
    recv_exact(sock, HDR)
    t1 = time.perf_counter_ns()
    return _tput("tx_client_to_server", total, t1 - t0)


def throughput_rx(sock: socket.socket, total: int, chunk: int) -> dict:
    send_cmd(sock, {"op": "source", "bytes": total, "chunk": chunk})
    buf = bytearray(chunk)
    view = memoryview(buf)
    got = 0
    t0 = time.perf_counter_ns()
    while got < total:
        r = sock.recv_into(view, min(chunk, total - got))
        if r == 0:
            raise ConnectionError("peer closed in source")
        got += r
    t1 = time.perf_counter_ns()
    sock.sendall(ACK)
    return _tput("rx_server_to_client", total, t1 - t0)


def _tput(name: str, total: int, ns: int) -> dict:
    secs = ns / 1e9
    return {
        "direction": name,
        "bytes": total,
        "seconds": round(secs, 4),
        "gbps": round(total * 8 / secs / 1e9, 3),
        "MiBps": round(total / (1 << 20) / secs, 1),
    }


def run_client(args) -> dict:
    sizes = [int(s) for s in args.sizes.split(",")]
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind((args.bind_src, 0))
    tune(sock)
    sock.connect((args.connect, args.port))
    tune(sock)
    local = sock.getsockname()
    peer = sock.getpeername()
    before = netdev_counters()
    t_start = time.time()
    latency = []
    for size in sizes:
        iters = args.iters if size <= 16384 else max(2000, args.iters // 4)
        latency.append(echo_phase(sock, size, iters, args.warmup))
    tput = []
    if args.throughput_bytes:
        tput.append(throughput_tx(sock, args.throughput_bytes, args.chunk))
        tput.append(throughput_rx(sock, args.throughput_bytes, args.chunk))
    after = netdev_counters()
    send_cmd(sock, {"op": "quit"})
    sock.close()
    return {
        "label": args.label,
        "local_sockaddr": list(local),
        "peer_sockaddr": list(peer),
        "wall_s": round(time.time() - t_start, 2),
        "latency": latency,
        "throughput": tput,
        "client_netdev_delta": counter_delta(before, after),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--role", choices=("server", "client"), required=True)
    ap.add_argument("--bind", default="0.0.0.0")
    ap.add_argument("--bind-src", default="0.0.0.0")
    ap.add_argument("--connect", default="")
    ap.add_argument("--port", type=int, default=45601)
    ap.add_argument("--sizes", default="64,4096,8192,16384,65536")
    ap.add_argument("--iters", type=int, default=20000)
    ap.add_argument("--warmup", type=int, default=1000)
    ap.add_argument("--throughput-bytes", type=int, default=1 << 30)
    ap.add_argument("--chunk", type=int, default=1 << 20)
    ap.add_argument("--label", default="")
    args = ap.parse_args()
    if args.role == "server":
        run_server(args.bind, args.port)
        return 0
    print(json.dumps(run_client(args)), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
