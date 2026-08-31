#!/usr/bin/env python3
"""bench/gloo-allreduce-bench.py — the allreduce-shaped number, and an independent
proof of the GLOO_SOCKET_IFNAME fix.

Two ranks, one per node, CPU float32 tensors, ``torch.distributed`` GLOO
backend, ``dist.all_reduce`` timed at the same message sizes as the raw-socket
sweep. No GPU is touched, no vLLM is started, no model is loaded. Run inside the
already-running ``flashnext-pair`` container (host networking, torch 2.13.0)
because torch is not installed on either host python.

GLOO_SOCKET_IFNAME MUST be set in BOTH ranks' environments. Without it Gloo
resolves the hostname, finds loopback, and the rendezvous fails or binds the
wrong wire — the exact bug under repair in the fix/gloo-socket-ifname branch.
This bench sets it explicitly and then PROVES which wire carried the traffic by
sampling /sys/class/net/<dev>/statistics around the timed loop.

Argv is deliberately explicit (no env-derived rank) so the two sides can never
silently agree on the wrong thing.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import statistics
import time


def netdev() -> dict:
    out = {}
    base = pathlib.Path("/sys/class/net")
    if not base.is_dir():
        return out
    for dev in sorted(os.listdir(base)):
        st = base / dev / "statistics"
        try:
            out[dev] = {k: int((st / f"{k}_packets").read_text()) for k in ("rx", "tx")}
        except OSError:
            continue
    return out


def delta(a: dict, b: dict) -> dict:
    d = {}
    for k in b:
        if k in a:
            row = {m: b[k][m] - a[k][m] for m in b[k]}
            if any(row.values()):
                d[k] = row
    return d


def pct(xs, q):
    i = min(len(xs) - 1, max(0, int(round(q / 100.0 * len(xs) + 0.5)) - 1))
    return xs[i]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rank", type=int, required=True)
    ap.add_argument("--world-size", type=int, default=2)
    ap.add_argument("--master-addr", required=True)
    ap.add_argument("--master-port", type=int, required=True)
    ap.add_argument("--iface", required=True)
    ap.add_argument("--sizes", default="64,4096,8192,16384,65536")
    ap.add_argument("--iters", type=int, default=2000)
    ap.add_argument("--warmup", type=int, default=200)
    ap.add_argument("--timeout-s", type=int, default=90)
    ap.add_argument("--no-ifname", action="store_true",
                    help="deliberately UNSET GLOO_SOCKET_IFNAME to demonstrate the bug")
    args = ap.parse_args()

    if args.no_ifname:
        os.environ.pop("GLOO_SOCKET_IFNAME", None)
        os.environ.pop("TP_SOCKET_IFNAME", None)
    else:
        os.environ["GLOO_SOCKET_IFNAME"] = args.iface
        os.environ["TP_SOCKET_IFNAME"] = args.iface
    os.environ["MASTER_ADDR"] = args.master_addr
    os.environ["MASTER_PORT"] = str(args.master_port)

    import datetime
    import torch
    import torch.distributed as dist

    t0 = time.time()
    dist.init_process_group(
        backend="gloo", rank=args.rank, world_size=args.world_size,
        timeout=datetime.timedelta(seconds=args.timeout_s),
    )
    init_s = round(time.time() - t0, 3)

    before = netdev()
    results = []
    perf = time.perf_counter_ns
    for nbytes in (int(s) for s in args.sizes.split(",")):
        n = max(1, nbytes // 4)
        t = torch.ones(n, dtype=torch.float32)
        iters = args.iters if nbytes <= 16384 else max(300, args.iters // 4)
        for _ in range(args.warmup):
            dist.all_reduce(t)
        dist.barrier()
        samples = []
        for _ in range(iters):
            a = perf()
            dist.all_reduce(t)
            samples.append((perf() - a) / 1000.0)
        dist.barrier()
        samples.sort()
        results.append({
            "size_b": n * 4,
            "elems": n,
            "iters": iters,
            "allreduce_us": {
                "min": round(samples[0], 2),
                "p50": round(pct(samples, 50), 2),
                "p90": round(pct(samples, 90), 2),
                "p99": round(pct(samples, 99), 2),
                "max": round(samples[-1], 2),
                "mean": round(statistics.fmean(samples), 2),
            },
        })
    after = netdev()
    dist.destroy_process_group()
    print("GLOOBENCH " + json.dumps({
        "rank": args.rank,
        "iface_env": os.environ.get("GLOO_SOCKET_IFNAME", "<unset>"),
        "master": f"{args.master_addr}:{args.master_port}",
        "torch": torch.__version__,
        "init_s": init_s,
        "results": results,
        "netdev_delta": delta(before, after),
    }), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
