#!/usr/bin/env python3
"""bench/socket-transport-run.py — driver for socket-transport-bench.py.

Runs the identical harness over each named rail, ROUND-ROBIN across rounds so
that any thermal/scheduler drift hits both transports equally instead of
landing entirely on whichever went second. Writes results/receipts/
socket-transport.json.

The worker-side server is launched by streaming bench/socket-transport-bench.py
over the control wire (``ssh 10.99.9.2 python3 - --role server``) — never a copy
on the worker, same discipline as bench/usb4stream-bench.py.
"""

from __future__ import annotations

import json
import pathlib
import platform
import subprocess
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
BENCH = ROOT / "bench" / "socket-transport-bench.py"
RECEIPT = ROOT / "results" / "receipts" / "socket-transport.json"
WORKER = "10.99.9.2"
SSH = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", WORKER]

RAILS = [
    # label,        client src,  server bind,  server dev,     client dev
    ("thunderbolt0", "10.99.0.1", "10.99.0.2", "thunderbolt0", "thunderbolt0"),
    ("enp191s0",     "10.99.1.1", "10.99.1.2", "enp191s0",     "enp191s0"),
]
SIZES = "64,4096,8192,16384,65536"
ITERS = 20000
WARMUP = 1000
TPUT_BYTES = 1 << 30
ROUNDS = 2
PORT_BASE = 45611


def run_rail(label, src, dst, port, throughput_bytes):
    src_text = BENCH.read_text()
    server_cmd = SSH + [
        "python3", "-", "--role", "server", "--bind", dst, "--port", str(port),
    ]
    srv = subprocess.Popen(
        server_cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True,
    )
    srv.stdin.write(src_text)
    srv.stdin.close()
    listen = srv.stdout.readline()
    if '"listen"' not in listen:
        srv.kill()
        raise RuntimeError(f"server did not listen: {listen!r} {srv.stderr.read()!r}")
    client_cmd = [
        sys.executable, str(BENCH), "--role", "client",
        "--bind-src", src, "--connect", dst, "--port", str(port),
        "--sizes", SIZES, "--iters", str(ITERS), "--warmup", str(WARMUP),
        "--throughput-bytes", str(throughput_bytes), "--label", label,
    ]
    cp = subprocess.run(client_cmd, capture_output=True, text=True, timeout=900)
    if cp.returncode != 0:
        srv.kill()
        raise RuntimeError(f"client failed: {cp.stderr}")
    client = json.loads(cp.stdout.strip().splitlines()[-1])
    srv_out = srv.stdout.read()
    srv.wait(timeout=60)
    server = json.loads(srv_out.strip().splitlines()[-1]) if srv_out.strip() else {}
    return {
        "rail": label,
        "server_listen": json.loads(listen)["listen"],
        "client": client,
        "server": server,
        "client_cmd": " ".join(client_cmd),
        "server_cmd": " ".join(server_cmd[:-8]) + " python3 - --role server --bind "
                      + dst + " --port " + str(port) + "  (< bench/socket-transport-bench.py)",
    }


def main() -> int:
    rounds = []
    port = PORT_BASE
    for r in range(ROUNDS):
        for label, src, dst, _sdev, _cdev in RAILS:
            print(f"[{time.strftime('%T')}] round {r} rail {label} port {port}",
                  file=sys.stderr, flush=True)
            # throughput only on the second round, to keep round 0 purely latency
            tb = TPUT_BYTES if r == ROUNDS - 1 else 0
            res = run_rail(label, src, dst, port, tb)
            res["round"] = r
            rounds.append(res)
            port += 1
    out = ROOT / "results" / "socket-transport-raw.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"rounds": rounds,
                               "host": platform.node(),
                               "kernel": platform.release()}, indent=1) + "\n")
    print(f"raw -> {out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
