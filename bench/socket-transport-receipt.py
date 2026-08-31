#!/usr/bin/env python3
"""bench/socket-transport-receipt.py — fold the raw sweeps into the one receipt.

House style follows bench/usb4stream-bench.py: ``status`` is ALWAYS ``pass``
(this is evidence-gathering, not a campaign claim) and the real verdict lives in
a typed ``data.outcome``.
"""

from __future__ import annotations

import json
import pathlib
import platform

import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
RAW = ROOT / "results" / "socket-transport-raw.json"
GLOO_DIRS = [pathlib.Path("/tmp/fn-gloo/r1"), pathlib.Path("/tmp/fn-gloo/r2")]
RECEIPT = ROOT / "results" / "receipts" / "socket-transport.json"
SIZES = [64, 4096, 8192, 16384, 65536]


def load_gloo():
    rows = []
    for i, d in enumerate(GLOO_DIRS, start=1):
        for f in sorted(d.glob("rank0.*.log")):
            iface = f.name.split(".")[1]
            for line in f.read_text().splitlines():
                if line.startswith("GLOOBENCH "):
                    rec = json.loads(line[len("GLOOBENCH "):])
                    rec["round"] = i
                    rec["iface"] = iface
                    rows.append(rec)
    return rows


def main() -> int:
    raw = json.loads(RAW.read_text())
    gloo = load_gloo()

    # ---- raw TCP ping-pong, per rail per size, both rounds ----------------
    tcp = {}
    for r in raw["rounds"]:
        rail = r["rail"]
        for L in r["client"]["latency"]:
            k = str(L["size_b"])
            tcp.setdefault(rail, {}).setdefault(k, []).append(L["rtt_us"])
    tcp_summary = {
        rail: {
            k: {
                "p50_us": [v["p50"] for v in vs],
                "p99_us": [v["p99"] for v in vs],
                "min_us": min(v["min"] for v in vs),
                "mean_us": [v["mean"] for v in vs],
            }
            for k, vs in sizes.items()
        }
        for rail, sizes in tcp.items()
    }

    # ---- gloo allreduce, per iface per size ------------------------------
    ar = {}
    for g in gloo:
        for res in g["results"]:
            ar.setdefault(g["iface"], {}).setdefault(str(res["size_b"]), []).append(
                res["allreduce_us"])
    ar_summary = {
        iface: {
            k: {
                "p50_us": [v["p50"] for v in vs],
                "p99_us": [v["p99"] for v in vs],
                "min_us": min(v["min"] for v in vs),
            }
            for k, vs in sizes.items()
        }
        for iface, sizes in ar.items()
    }

    def med(xs):
        xs = sorted(xs)
        return xs[len(xs) // 2] if len(xs) % 2 else round((xs[len(xs)//2-1]+xs[len(xs)//2])/2, 2)

    verdict_rows = []
    for s in SIZES:
        k = str(s)
        tb = med(tcp_summary["thunderbolt0"][k]["p50_us"])
        et = med(tcp_summary["enp191s0"][k]["p50_us"])
        gtb = med(ar_summary["thunderbolt0"][k]["p50_us"])
        get = med(ar_summary["enp191s0"][k]["p50_us"])
        verdict_rows.append({
            "size_b": s,
            "tcp_rtt_p50_us": {"thunderbolt0": tb, "enp191s0": et},
            "tcp_rtt_p99_us": {
                "thunderbolt0": med(tcp_summary["thunderbolt0"][k]["p99_us"]),
                "enp191s0": med(tcp_summary["enp191s0"][k]["p99_us"]),
            },
            "gloo_allreduce_p50_us": {"thunderbolt0": gtb, "enp191s0": get},
            "gloo_allreduce_p99_us": {
                "thunderbolt0": med(ar_summary["thunderbolt0"][k]["p99_us"]),
                "enp191s0": med(ar_summary["enp191s0"][k]["p99_us"]),
            },
            "tcp_winner": "enp191s0" if et < tb else "thunderbolt0",
            "tcp_ratio_tb_over_eth": round(tb / et, 2),
            "allreduce_winner": "enp191s0" if get < gtb else "thunderbolt0",
            "allreduce_ratio_tb_over_eth": round(gtb / get, 2),
        })

    tput = {}
    for r in raw["rounds"]:
        for t in r["client"]["throughput"]:
            tput.setdefault(r["rail"], {})[t["direction"]] = {
                "gbps": t["gbps"], "MiBps": t["MiBps"], "bytes": t["bytes"],
                "seconds": t["seconds"],
            }

    data = {
        "outcome": "ok:5gbe-wire-beats-thunderbolt-rail-at-decode-sizes",
        "loop": "python",
        "purpose": (
            "Replaces the INFERRED cell at docs/DECISIONS-2026-08-30.md:168 "
            "('RCCL sockets over thunderbolt0 | ~150-300 us | INFERRED') with a "
            "measurement, and clears RDMA Gate 0 "
            "(host/rdma/attended-bringup.md), which requires a committed "
            "socket-transport benchmark before the A/B may run."
        ),
        "headline": (
            "At every decode-relevant message size the 5 GbE control wire "
            "(enp191s0, 10.99.1.0/30) delivers a LOWER Gloo all-reduce latency "
            "than the Thunderbolt tensor rail (thunderbolt0, 10.99.0.0/30) — by "
            "1.6x to 2.7x. thunderbolt0 wins only bulk single-stream throughput (~1.9x). The "
            "transport of record FN_TRANSPORT_RUNG=rail0-sockets is therefore "
            "NOT the latency-optimal choice for TP=2 decode and should be "
            "re-opened."
        ),
        "hardware": {
            "coordinator": platform.node(),
            "worker": "10.99.9.2",
            "kernel": raw.get("kernel"),
            "rails": {
                "thunderbolt0": {
                    "cable": "A", "subnet": "10.99.0.0/30", "mtu": 1500,
                    "coordinator": "10.99.0.1", "worker": "10.99.0.2",
                    "driver": "thunderbolt_net", "parentdev": "1-2.0",
                    "link_trained_gbps": 40,
                },
                "enp191s0": {
                    "wire": "5 GbE control", "subnet": "10.99.1.0/30", "mtu": 1500,
                    "coordinator": "10.99.1.1", "worker": "10.99.1.2",
                    "pci": "0000:bf:00.0", "link_gbps": 5,
                },
            },
        },
        "method": {
            "tools_present": {
                "iperf3": False, "netperf": False, "sockperf": False,
                "qperf": False, "note": "none installed on either node; nothing was installed",
            },
            "harness": "bench/socket-transport-bench.py (stdlib only), driven by bench/socket-transport-run.py",
            "allreduce_harness": "bench/gloo-allreduce-bench.py via bench/gloo-allreduce-run.sh",
            "tcp": {
                "pattern": "blocking TCP ping-pong on one persistent connection, TCP_NODELAY on both ends",
                "sizes_b": SIZES,
                "iters_per_size": {"<=16384": 20000, "65536": 5000},
                "warmup_iters": 1000,
                "rounds": 2,
                "round_robin": "rails alternate within each round so drift hits both equally",
                "percentile": "nearest-rank on the sorted sample vector, no interpolation",
            },
            "allreduce": {
                "backend": "gloo", "world_size": 2, "dtype": "float32",
                "device": "cpu",
                "iters_per_size": {"<=16384": 2000, "65536": 500},
                "warmup_iters": 200, "rounds": 2,
                "where": "inside the already-running flashnext-pair container (host netns, torch 2.13.0+rocm7.14.0); torch is NOT installed on either host python",
                "no_serve_started": True,
            },
            "interface_pinning": {
                "how": "client binds its source address explicitly, server binds its listen address explicitly; both rails are /30 so the bound address selects the netdev unambiguously",
                "verified_by": "per-netdev rx/tx packet counters sampled on BOTH nodes around every timed phase; carried below",
                "caveat": "ssh to the worker (10.99.9.2) routes via 10.99.1.2 dev enp191s0, so the idle control connection shares the physical wire with the enp191s0 baseline. Its traffic during measurement is ~100 packets against ~1.5M, i.e. negligible, and it disadvantages enp191s0 rather than flattering it.",
            },
        },
        "tcp_rtt_us": tcp_summary,
        "gloo_allreduce_us": ar_summary,
        "throughput": tput,
        "comparison": verdict_rows,
        "findings": [
            {
                "id": "tb-fixed-floor",
                "claim": "thunderbolt0 has a FLAT ~130 us TCP RTT floor that is independent of payload from 64 B through 16 KiB (p50 130.4 us at every one of those sizes, 92-98% of samples in a single 25 us histogram bin), while its minimum observed RTT is 30-34 us.",
                "reading": "The ~100 us gap between the floor and the minimum is thunderbolt_net / IP-stack wakeup cost, not fabric time-of-flight. The fabric is fast; the netdev path on top of it is not.",
            },
            {
                "id": "eth-scales-tb-does-not",
                "claim": "enp191s0 RTT p50 rises with size as a real wire does (56.6 us at 64 B, 138 us at 4 KiB, 144 us at 8 KiB, 134-138 us at 16 KiB, 316 us at 64 KiB); thunderbolt0 is pinned at its floor until 64 KiB.",
                "reading": "At 64 B the wire is 2.3x faster. The two cross over around 4-16 KiB where thunderbolt0's flat floor happens to sit just under the wire's rising curve on p50 — but see tb-tail.",
            },
            {
                "id": "tb-tail",
                "claim": "thunderbolt0 carries a second latency mode near 275-300 us that captures 4-9% of samples at 4-8 KiB, so its p99 is 298-338 us against enp191s0's 144-238 us. On the very first cold sweep of the session that mode captured the MEDIAN at 8 and 16 KiB (p50 300.4 / 304.3 us).",
                "reading": "thunderbolt0's small-message p50 advantage at 8-16 KiB is 4-10% and is not robust; its tail is decisively worse and is bistable across runs.",
            },
            {
                "id": "allreduce-decides-it",
                "claim": "The Gloo all-reduce — the shape the decision is actually about — shows no crossover at all. enp191s0 wins at every size in both rounds: 64 B 225-238 vs 375-384 us, 4 KiB 234-244 vs 626-631 us, 8 KiB 251-267 vs 521-523 us, 16 KiB 259-260 vs 525-531 us, 64 KiB 350-356 vs 656-672 us.",
                "reading": "Gloo's 2-rank all-reduce costs roughly two round trips, which doubles thunderbolt0's per-RTT penalty and erases the narrow p50 crossover seen in the raw ping-pong. This is the number that should drive the decision.",
            },
            {
                "id": "throughput-is-the-only-tb-win",
                "claim": "thunderbolt0 sustains 8.81 Gb/s TX and 9.20 Gb/s RX on a 1 GiB single-stream transfer (an earlier identical sweep gave 9.19 / 9.36, so call it 8.8-9.4); enp191s0 sustains 4.71 Gb/s in both directions.",
                "reading": "thunderbolt0 delivers only ~22-23% of the 40 Gb/s the link trains at (20 Gb/s x 2), on a single TCP stream. enp191s0 delivers ~94% of its 5 Gb/s. The rail's ~1.9x bandwidth advantage matters for prefill and for weight/KV movement, not for decode-step all-reduce.",
            },
            {
                "id": "memo-baseline-reproduced",
                "claim": "The memo's TCP-over-5GbE figure of 137.8 us p50 at 4 KiB reproduces as 138.1 and 138.5 us p50 in the two rounds here.",
                "reading": "Independent confirmation that this harness is measuring the same quantity the memo's 5 GbE cell recorded, so the thunderbolt0 column is directly comparable to it.",
            },
            {
                "id": "gloo-ifname-proved",
                "claim": "With GLOO_SOCKET_IFNAME set explicitly in both ranks, TP=2-shaped Gloo rendezvous and all-reduce succeed over both rails. With it deliberately unset (--no-ifname control), Gloo resolved to loopback and died: 'failed to connect ... local=[127.0.0.1]:49866, remote=[127.0.0.2]:20365 ... SO_ERROR: Connection refused', then 'RuntimeError: Gloo connectFullMesh failed ... timed out connecting'.",
                "reading": "Independent reproduction of the blocker being repaired on fix/gloo-socket-ifname, and independent proof that setting GLOO_SOCKET_IFNAME is both necessary and sufficient for the rendezvous.",
            },
        ],
        "implications": {
            "transport_of_record": (
                "FN_TRANSPORT_RUNG=rail0-sockets is not justified by latency. For "
                "decode-step all-reduce, GLOO_SOCKET_IFNAME=enp191s0 is 1.6-2.7x "
                "faster than thunderbolt0 today. A split assignment — enp191s0 for "
                "the latency-bound collective, thunderbolt0 for bulk movement — is "
                "the configuration these numbers point at, and costs nothing to try."
            ),
            "rdma_gate_0": (
                "Gate 0's committed socket-transport benchmark now exists. The A/B's "
                "baseline to beat is enp191s0 at 225-356 us all-reduce, not "
                "thunderbolt0 at 375-672 us. Note no RDMA verbs device exists on "
                "either node today and NCCL_IB_DISABLE=1 is pinned unconditionally "
                "at host/fn-env.sh:152, so Gate 0 is unblocked on evidence but the "
                "hardware precondition is still unmet."
            ),
            "usb4stream": (
                "The ~100 us between thunderbolt0's 130 us RTT floor and its 30-34 us "
                "minimum is exactly the IP-stack overhead the raw-DMA stream primitive "
                "would bypass. That is a concrete, quantified budget for the "
                "docs/USB4STREAM-TRANSPORT.md section 4 trigger criteria: a stream "
                "path must land under ~225 us all-reduce-equivalent to beat the 5 GbE "
                "wire, and the fabric minimum says that is not obviously out of reach."
            ),
        },
        "scope_and_safety": {
            "vllm_serve_started": False,
            "tally_touched": False,
            "configfs_written": False,
            "thunderbolt_device_nodes_opened": False,
            "cable_b_touched": False,
            "packages_installed": False,
            "containers_started_or_stopped": False,
            "note": "cable B / thunderbolt1 belongs to the concurrent stream bench and was not used here; the only thunderbolt1 counters in the deltas are 2-4 stray multicast packets from the kernel's own 224.0.0.0/4 route.",
        },
        "commands": [
            "python3 bench/socket-transport-run.py",
            "ssh -o BatchMode=yes -o ConnectTimeout=5 10.99.9.2 python3 - --role server --bind 10.99.0.2 --port <p>  (< bench/socket-transport-bench.py)",
            "python3 bench/socket-transport-bench.py --role client --bind-src 10.99.0.1 --connect 10.99.0.2 --port <p> --sizes 64,4096,8192,16384,65536 --iters 20000 --warmup 1000 --throughput-bytes 1073741824 --label thunderbolt0",
            "python3 bench/socket-transport-bench.py --role client --bind-src 10.99.1.1 --connect 10.99.1.2 --port <p> --sizes 64,4096,8192,16384,65536 --iters 20000 --warmup 1000 --throughput-bytes 1073741824 --label enp191s0",
            "bash bench/gloo-allreduce-run.sh thunderbolt0 10.99.0.1 <p> --iters 2000 --warmup 200 --timeout-s 90",
            "bash bench/gloo-allreduce-run.sh enp191s0 10.99.1.1 <p> --iters 2000 --warmup 200 --timeout-s 90",
            "bash bench/gloo-allreduce-run.sh thunderbolt0 10.99.0.1 <p> --iters 20 --warmup 5 --sizes 64 --timeout-s 45 --no-ifname   # the failing control",
        ],
        "raw": "results/socket-transport-raw.json (full per-size histograms, per-netdev counter deltas from both nodes, both rounds)",
        "wall_clock_s": 57,
    }

    receipt = {
        "step": "socket-transport",
        "status": "pass",
        "ts": time.strftime("%FT%T"),
        "data": data,
    }
    RECEIPT.parent.mkdir(parents=True, exist_ok=True)
    RECEIPT.write_text(json.dumps(receipt, indent=1) + "\n")
    print(f"socket-transport: receipt {RECEIPT} outcome={data['outcome']}")
    for row in verdict_rows:
        print(f"  {row['size_b']:>6}B  tcp p50 tb={row['tcp_rtt_p50_us']['thunderbolt0']:>7} "
              f"eth={row['tcp_rtt_p50_us']['enp191s0']:>7} ({row['tcp_winner']}) | "
              f"allreduce p50 tb={row['gloo_allreduce_p50_us']['thunderbolt0']:>7} "
              f"eth={row['gloo_allreduce_p50_us']['enp191s0']:>7} "
              f"({row['allreduce_winner']}, {row['allreduce_ratio_tb_over_eth']}x)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
