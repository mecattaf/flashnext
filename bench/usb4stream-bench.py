#!/usr/bin/env python3
"""bench/usb4stream-bench.py — wedge-safe first light on the in-tree USB4 stream primitive.

Authored by the ``usb4stream-bench`` lane; RUN by the dead-last checkpoint
``cp-usb4stream`` (after ``cp-close``), never during the night's serving work.

What this measures
------------------
``thunderbolt_stream`` is the in-tree stream primitive (kernel 7.2+, formal
ABI, authored by the Thunderbolt maintainer): 4 KiB DATA frames over raw NHI
DMA rings, one kernel copy per direction, a userspace byte pipe with no IP
stack under it. This bench banks the numbers the morning's transport decision
needs (docs/USB4STREAM-TRANSPORT.md): per-size RTT, the **allreduce-shaped
simultaneous exchange** at 8/16/64 KiB — the decision-relevant number — and
throughput in both directions.

Why every mechanic below is non-negotiable
------------------------------------------
Rings are allocated and router paths enabled on the FIRST open; CLOSE is sent
and paths disabled on the LAST close. Cycling that against a half-configured
or mismatched peer corrupts router hop tables, takes ``thunderbolt_net``'s
paths down **on the same cable**, and needs a reboot to clear. That hazard
already darkened rail 0 once. Rail 0 (``thunderbolt0``, 10.99.0.x) rides
cable A alongside this device, so a wedge here would take the campaign's
headline deliverable — a pair still serving at 07:00 — down with it.

Hence, and none of these are style choices:

* **No hardcoded device node.** Numbering is asymmetric across the twins: the
  same logical stream is index 2 on the coordinator and index 0 on the
  worker, and the coordinator's low indices belong to cable B, whose worker
  counterpart has no configfs groups at all — **peerless**, where a blocking
  open waits forever on a stream that can never become valid. The node is
  resolved on EACH node, per the chain in ``resolve_stream_device``. This
  file contains no numbered device literal anywhere, and the lane's
  acceptance argv asserts that.
* **Exactly one open attempt per side, ever**, blocking, under a 30 s alarm.
  Any failure after that point: close what is open, kill the ssh child, write
  the receipt, exit. Never reopen, never retry.
* **Never write configfs.** ``ring_size`` and ``throttling`` are read; nothing
  in this file opens a configfs attribute for writing.
* **Idempotence guard.** If the receipt already exists we exit 0 immediately,
  before touching anything. That is what makes a harness retry storm-free by
  construction rather than by luck.
* **Three skip preconditions**, checked in order BEFORE any device access,
  each a typed ``pass`` receipt and exit 0 — a live serve on the shared cable
  first of all.
* **Fixed schedule, no adaptivity, no retries.** Under 90 s of measurement,
  the whole run under a global alarm.

The receipt's ``status`` is ALWAYS ``pass``: this is evidence-gathering, not a
campaign claim. A mid-run abort is typed as ``data.outcome =
"aborted:PHASE:ERRNO"`` rather than a fail status that would redden every
later gate run.

Roles
-----
``--role coordinator``  (default) drives the schedule and writes the receipt.
``--role peer``         mirrors the schedule; launched by streaming THIS FILE
                        over the control wire (``ssh worker python3 -`` with
                        the source on stdin) — never a copy on the worker.
``--role probe``        read-only resolution report: what device this node
                        would use and whether its fn0 configfs group exists.
                        Touches no device. Run on the worker before any open.

Stdlib only.
"""

from __future__ import annotations

import argparse
import errno
import ipaddress
import json
import os
import pathlib
import shlex
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
RECEIPT_PATH = ROOT / "results" / "receipts" / "usb4stream.json"

# The device node is ALWAYS this prefix plus the index read from configfs. No
# numbered literal is written anywhere in this file (see the module docstring).
DEVICE_PREFIX = "/dev/tbstream"

# The tensor rail's /30 (dotfiles-observed.md §2.1): coordinator 10.99.0.1,
# worker 10.99.0.2. The rail netdev is found BY THIS ADDRESS, never by name —
# the name is what the asymmetry lives in.
RAIL_NET = ipaddress.ip_network("10.99.0.0/30")

# configfs roots the stream primitive's groups live under. The group is
# <root>/<service>/fn0 and we only ever READ from it. Observed on the
# coordinator 2026-08-30 (kernel 7.2.2):
#
#   /sys/kernel/config/thunderbolt/stream/1-2.1/{fn0,fn1}
#     fn0: index=0 in_hopid=9 out_hopid=8 ring_size=1024 throttling=2048
#
# The later roots are fallbacks in case the worker's kernel lays the subsystem
# out differently; FN_USB4_CONFIGFS_ROOT overrides all of them.
CONFIGFS_ROOTS = tuple(
    p for p in (
        os.environ.get("FN_USB4_CONFIGFS_ROOT"),
        "/sys/kernel/config/thunderbolt/stream",
        "/sys/kernel/config/thunderbolt",
        "/sys/kernel/config/thunderbolt_stream",
    ) if p
)
FUNCTION_GROUP = "fn0"

# Control-wire and serve-detection knobs; defaults mirror host/fn-env.sh.
# Worker-side actions ride the 5 GbE stable fleet identity, NEVER a 10.99.0.x
# rail — the rails carry tensors only.
WORKER_HOST = os.environ.get("FN_WORKER_HOST", "10.99.9.2")
SERVE_PORT = os.environ.get("FN_PORT", "1234")
SERVE_CONTAINER = os.environ.get("FN_CONTAINER", "flashnext-pair")

# --- the fixed schedule (no adaptivity, no retries, both sides identical) ----
WARMUP_ITERS = 50
WARMUP_BYTES = 4096
RTT_SIZES = (64, 4096, 16384, 65536)
RTT_ITERS = 500
# The allreduce-shaped simultaneous exchange: both sides write N then read N.
# Ring 1024 × 4 KiB frames buffers 64 KiB in flight per direction safely, so
# the symmetric write-then-read cannot deadlock at these sizes.
EXCHANGE_SIZES = (8192, 16384, 65536)
EXCHANGE_ITERS = 500
THROUGHPUT_BYTES = 256 * 1024 * 1024
THROUGHPUT_CHUNK = 1024 * 1024
HANDSHAKE_BYTES = 64
HANDSHAKE_MAGIC = b"FLASHNEXT-USB4STREAM-FIRST-LIGHT"

OPEN_ALARM_S = 30
GLOBAL_TIMEOUT_S = 240
SSH_OPTS = ("-o", "BatchMode=yes", "-o", "ConnectTimeout=5",
            "-o", "StrictHostKeyChecking=yes")

SKIP_SERVE_UP = "skipped:serve-up-on-shared-cable"
SKIP_PEER_UNREACHABLE = "skipped:rail-peer-unreachable"
SKIP_CONFIGFS_MISSING = "skipped:configfs-group-missing"


class BenchTimeout(Exception):
    """The global or open alarm fired."""


class ResolveError(Exception):
    """The device-resolution chain could not complete on this node.

    ``configfs`` is True when the chain got as far as the service but the fn0
    group is absent — the typed ``skipped:configfs-group-missing`` case.
    """

    def __init__(self, message: str, configfs: bool = False):
        super().__init__(message)
        self.configfs = configfs


class Phase(Exception):
    """Carries the phase name an abort happened in."""


# ---------------------------------------------------------------------------
# receipt
# ---------------------------------------------------------------------------

def already_banked(path: pathlib.Path = RECEIPT_PATH) -> bool:
    """The idempotence guard. A retry that finds a receipt touches NOTHING."""
    return path.is_file()


def write_receipt(outcome: str, data: dict, path: pathlib.Path = RECEIPT_PATH) -> None:
    """Write the one receipt. status is ALWAYS 'pass' — see the docstring."""
    body = dict(data)
    body["outcome"] = outcome
    body.setdefault("loop", "python")
    receipt = {
        "step": "usb4stream",
        "status": "pass",
        "ts": time.strftime("%FT%T"),
        "data": body,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(receipt, indent=1) + "\n")
    print(f"usb4stream-bench: receipt {path} outcome={outcome}", flush=True)


def schedule_description() -> dict:
    return {
        "warmup_iters": WARMUP_ITERS,
        "warmup_bytes": WARMUP_BYTES,
        "rtt_sizes": list(RTT_SIZES),
        "rtt_iters": RTT_ITERS,
        "exchange_sizes": list(EXCHANGE_SIZES),
        "exchange_iters": EXCHANGE_ITERS,
        "throughput_bytes_each_direction": THROUGHPUT_BYTES,
    }


# ---------------------------------------------------------------------------
# resolution — the chain, run on EACH node, reading sysfs and configfs only
# ---------------------------------------------------------------------------

def _read_attr(path: str) -> str | None:
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return None


def rail_netdev() -> tuple[str, str]:
    """The netdev carrying the rail /30 address. Never matched by name."""
    proc = subprocess.run(["ip", "-o", "-4", "addr", "show"],
                          capture_output=True, text=True)
    for line in proc.stdout.splitlines():
        fields = line.split()
        if len(fields) < 4 or fields[2] != "inet":
            continue
        dev, cidr = fields[1], fields[3]
        try:
            addr = ipaddress.ip_address(cidr.split("/")[0])
        except ValueError:
            continue
        if addr in RAIL_NET:
            return dev, str(addr)
    raise ResolveError("no netdev carries a " + str(RAIL_NET) + " address")


def rail_peer_address(addr: str) -> str:
    """The other host on the /30: .1 <-> .2."""
    octets = addr.split(".")
    octets[-1] = "2" if octets[-1] == "1" else "1"
    return ".".join(octets)


def stream_service(rail: str) -> tuple[str, str]:
    """rail netdev -> its thunderbolt service -> parent xdomain -> the sibling
    service whose ``key`` attribute is ``stream``. Returns (xdomain, service)."""
    link = f"/sys/class/net/{rail}/device"
    if not os.path.exists(link):
        raise ResolveError(f"{link} does not exist")
    net_service = os.path.realpath(link)
    xdomain = os.path.dirname(net_service)
    if not os.path.isdir(xdomain):
        raise ResolveError(f"parent xdomain {xdomain} is not a directory")
    for entry in sorted(os.listdir(xdomain)):
        candidate = os.path.join(xdomain, entry)
        if not os.path.isdir(candidate):
            continue
        if _read_attr(os.path.join(candidate, "key")) == "stream":
            return xdomain, candidate
    raise ResolveError(f"no sibling service with key=stream under {xdomain}")


def configfs_group(service_path: str) -> str:
    """The fn0 configfs group for this service. READ-ONLY, always."""
    name = os.path.basename(service_path)
    tried = []
    for root in CONFIGFS_ROOTS:
        group = os.path.join(root, name, FUNCTION_GROUP)
        tried.append(group)
        if os.path.isdir(group):
            return group
    raise ResolveError(
        f"{FUNCTION_GROUP} configfs group absent for service {name} "
        f"(looked under: {', '.join(tried)})", configfs=True)


def resolve_stream_device() -> dict:
    """The whole chain on THIS node. Reads sysfs/configfs; opens nothing.

    rail netdev holding the /30 -> readlink /sys/class/net/RAIL/device ->
    parent xdomain -> sibling service with key=stream -> that service's fn0
    configfs group -> its ``index`` attribute -> the device node.
    """
    rail, addr = rail_netdev()
    xdomain, service = stream_service(rail)
    group = configfs_group(service)
    index = _read_attr(os.path.join(group, "index"))
    if index is None or not index.isdigit():
        raise ResolveError(f"{group}/index is unreadable or not an index",
                           configfs=True)
    return {
        "rail": rail,
        "rail_addr": addr,
        "xdomain": xdomain,
        "service": service,
        "configfs_group": group,
        "index": int(index),
        "dev": DEVICE_PREFIX + index,
        "ring_size": _read_attr(os.path.join(group, "ring_size")),
        "throttling": _read_attr(os.path.join(group, "throttling")),
    }


# ---------------------------------------------------------------------------
# skip preconditions — all of this runs BEFORE any device access
# ---------------------------------------------------------------------------

def _ssh_argv(command: str) -> list:
    return ["ssh", *SSH_OPTS, WORKER_HOST, command]


def api_answers() -> bool:
    """The coordinator's serve endpoint. Any answer at all counts as up."""
    url = f"http://127.0.0.1:{SERVE_PORT}/v1/models"
    try:
        with urllib.request.urlopen(url, timeout=3) as resp:
            return resp.status < 500
    except urllib.error.HTTPError:
        return True   # it answered; an error status is still a live serve
    except Exception:
        return False


def _podman_running(argv: list) -> bool:
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return False
    return SERVE_CONTAINER in proc.stdout


def serve_is_up() -> bool:
    """API on the coordinator, or the serving container running on EITHER node.

    The worker is checked over the CONTROL WIRE (5 GbE fleet identity), never
    over the rail this bench is about to touch.
    """
    if api_answers():
        return True
    local = ["podman", "ps", "--filter", f"name={SERVE_CONTAINER}",
             "--format", "{{.Names}}"]
    if _podman_running(local):
        return True
    remote = ("podman ps --filter name=" + shlex.quote(SERVE_CONTAINER)
              + " --format '{{.Names}}'")
    return _podman_running(_ssh_argv(remote))


def rail_peer_answers(peer: str) -> bool:
    try:
        proc = subprocess.run(["ping", "-c1", "-W2", peer],
                              capture_output=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return False
    return proc.returncode == 0


def probe_peer(source: bytes) -> dict:
    """Run THIS FILE on the worker in probe role over the control wire.

    Streamed on stdin — never copied to the worker's disk. Read-only there.
    """
    proc = subprocess.run(_ssh_argv("python3 - --role probe"),
                          input=source, capture_output=True, timeout=60)
    text = proc.stdout.decode(errors="replace").strip()
    for line in reversed(text.splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                continue
    return {"ok": False,
            "error": f"peer probe returned no JSON (rc={proc.returncode}): "
                     f"{proc.stderr.decode(errors='replace').strip()[:200]}"}


# ---------------------------------------------------------------------------
# the single open, and the transfer primitives
# ---------------------------------------------------------------------------

_OPEN_COUNT = 0


def open_stream_once(dev: str) -> int:
    """THE open. One attempt per side per process lifetime, blocking, 30 s.

    A second call is a programming error, not a retry: it raises.
    """
    global _OPEN_COUNT
    if _OPEN_COUNT:
        raise RuntimeError("refusing a second open: one attempt per side, ever")
    _OPEN_COUNT += 1
    signal.alarm(OPEN_ALARM_S)
    try:
        return os.open(dev, os.O_RDWR)
    finally:
        signal.alarm(0)


def open_count() -> int:
    return _OPEN_COUNT


def write_all(fd: int, buf: memoryview) -> None:
    off = 0
    total = len(buf)
    while off < total:
        off += os.write(fd, buf[off:off + THROUGHPUT_CHUNK])


def read_exact(fd: int, size: int) -> None:
    """Read exactly ``size`` bytes. A peer close is EOF on read (-ENXIO on
    write) and is reported as such — never as a reason to reopen."""
    remaining = size
    while remaining:
        chunk = os.read(fd, remaining)
        if not chunk:
            raise OSError(errno.ENXIO, "peer closed the stream")
        remaining -= len(chunk)


def percentile(samples: list, pct: float) -> float:
    """Nearest-rank percentile; samples need not be pre-sorted."""
    if not samples:
        return 0.0
    ordered = sorted(samples)
    rank = max(1, min(len(ordered), int(round(pct / 100.0 * len(ordered)))))
    return ordered[rank - 1]


# ---------------------------------------------------------------------------
# the schedule — identical constants on both sides, so no negotiation happens
# ---------------------------------------------------------------------------

def coordinator_schedule(fd: int, payload: bytes) -> dict:
    view = memoryview(payload)
    results = {"rtt_us": {}, "exchange_us": {}, "throughput_mb_s": {}}

    set_phase("handshake")
    write_all(fd, view[:HANDSHAKE_BYTES])
    read_exact(fd, HANDSHAKE_BYTES)

    set_phase("warmup")
    for _ in range(WARMUP_ITERS):
        write_all(fd, view[:WARMUP_BYTES])
        read_exact(fd, WARMUP_BYTES)

    set_phase("rtt")
    for size in RTT_SIZES:
        samples = []
        for _ in range(RTT_ITERS):
            t0 = time.perf_counter_ns()
            write_all(fd, view[:size])
            read_exact(fd, size)
            samples.append((time.perf_counter_ns() - t0) / 1000.0)
        results["rtt_us"][str(size)] = {
            "p50": round(percentile(samples, 50), 3),
            "p99": round(percentile(samples, 99), 3),
            "iters": RTT_ITERS,
        }

    set_phase("exchange")
    for size in EXCHANGE_SIZES:
        samples = []
        for _ in range(EXCHANGE_ITERS):
            t0 = time.perf_counter_ns()
            write_all(fd, view[:size])
            read_exact(fd, size)
            samples.append((time.perf_counter_ns() - t0) / 1000.0)
        results["exchange_us"][str(size)] = {
            "p50": round(percentile(samples, 50), 3),
            "p99": round(percentile(samples, 99), 3),
            "iters": EXCHANGE_ITERS,
        }

    set_phase("throughput")
    chunk = view[:THROUGHPUT_CHUNK]
    chunks = THROUGHPUT_BYTES // THROUGHPUT_CHUNK
    t0 = time.perf_counter_ns()
    for _ in range(chunks):
        write_all(fd, chunk)
    elapsed = (time.perf_counter_ns() - t0) / 1e9
    results["throughput_mb_s"]["coordinator_to_peer"] = round(
        THROUGHPUT_BYTES / 1e6 / elapsed, 1) if elapsed else None
    t0 = time.perf_counter_ns()
    read_exact(fd, THROUGHPUT_BYTES)
    elapsed = (time.perf_counter_ns() - t0) / 1e9
    results["throughput_mb_s"]["peer_to_coordinator"] = round(
        THROUGHPUT_BYTES / 1e6 / elapsed, 1) if elapsed else None

    set_phase("teardown")
    write_all(fd, view[:HANDSHAKE_BYTES])
    read_exact(fd, HANDSHAKE_BYTES)
    return results


def peer_schedule(fd: int, payload: bytes) -> None:
    """The mirror. The exchange phase is symmetric (write N, then read N);
    every other phase answers."""
    view = memoryview(payload)

    set_phase("handshake")
    read_exact(fd, HANDSHAKE_BYTES)
    write_all(fd, view[:HANDSHAKE_BYTES])

    set_phase("warmup")
    for _ in range(WARMUP_ITERS):
        read_exact(fd, WARMUP_BYTES)
        write_all(fd, view[:WARMUP_BYTES])

    set_phase("rtt")
    for size in RTT_SIZES:
        for _ in range(RTT_ITERS):
            read_exact(fd, size)
            write_all(fd, view[:size])

    set_phase("exchange")
    for size in EXCHANGE_SIZES:
        for _ in range(EXCHANGE_ITERS):
            write_all(fd, view[:size])
            read_exact(fd, size)

    set_phase("throughput")
    read_exact(fd, THROUGHPUT_BYTES)
    chunk = view[:THROUGHPUT_CHUNK]
    for _ in range(THROUGHPUT_BYTES // THROUGHPUT_CHUNK):
        write_all(fd, chunk)

    set_phase("teardown")
    read_exact(fd, HANDSHAKE_BYTES)
    write_all(fd, view[:HANDSHAKE_BYTES])


# ---------------------------------------------------------------------------
# abort typing
# ---------------------------------------------------------------------------

_PHASE = "startup"


def set_phase(phase: str) -> None:
    global _PHASE
    _PHASE = phase


def current_phase() -> str:
    return _PHASE


def errno_name(exc: BaseException) -> str:
    if isinstance(exc, BenchTimeout):
        return errno.errorcode[errno.ETIMEDOUT]
    number = getattr(exc, "errno", None)
    if isinstance(number, int) and number in errno.errorcode:
        return errno.errorcode[number]
    if isinstance(number, int):
        return str(number)
    return "EUNKNOWN"


def aborted(exc: BaseException, phase: str | None = None) -> str:
    return f"aborted:{phase or current_phase()}:{errno_name(exc)}"


def _alarm(_signum, _frame):
    raise BenchTimeout(f"alarm fired in phase {current_phase()}")


def payload_buffer() -> bytes:
    """One fixed buffer, allocated once: the biggest thing we ever send.

    Content is a constant marker — this bench measures the wire, not a codec.
    """
    body = (HANDSHAKE_MAGIC * (THROUGHPUT_CHUNK // len(HANDSHAKE_MAGIC) + 1))
    return body[:max(THROUGHPUT_CHUNK, HANDSHAKE_BYTES, max(RTT_SIZES))]


# ---------------------------------------------------------------------------
# roles
# ---------------------------------------------------------------------------

def run_probe() -> int:
    """Read-only. Resolves, reports, opens nothing."""
    report: dict = {"ok": False, "host": os.uname().nodename}
    try:
        resolved = resolve_stream_device()
    except ResolveError as exc:
        report["error"] = str(exc)
        report["configfs_missing"] = exc.configfs
    else:
        report.update(resolved)
        report["ok"] = True
    print(json.dumps(report), flush=True)
    return 0


def run_peer(dev: str | None) -> int:
    """The mirror side. Its own alarms, its own one-open rule, its own JSON."""
    signal.signal(signal.SIGALRM, _alarm)
    report = {"role": "peer", "outcome": "ok", "open_count": 0, "dev": dev}
    fd = None
    try:
        if not dev:
            resolved = resolve_stream_device()
            dev = resolved["dev"]
            report["dev"] = dev
            report["ring_size"] = resolved["ring_size"]
            report["throttling"] = resolved["throttling"]
        set_phase("open")
        fd = open_stream_once(dev)
        report["open_count"] = open_count()
        signal.alarm(GLOBAL_TIMEOUT_S)
        peer_schedule(fd, payload_buffer())
    except BaseException as exc:                     # noqa: BLE001 — typed below
        report["outcome"] = aborted(exc)
        report["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        signal.alarm(0)
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
    print(json.dumps(report), flush=True)
    return 0


def launch_peer(source: bytes, dev: str) -> subprocess.Popen:
    """Stream THIS FILE to the worker's python on stdin. Never a copy."""
    command = f"python3 - --role peer --dev {shlex.quote(dev)}"
    proc = subprocess.Popen(_ssh_argv(command), stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert proc.stdin is not None
    proc.stdin.write(source)
    proc.stdin.close()
    return proc


def kill_peer(proc: subprocess.Popen | None) -> dict:
    """Reap the ssh child and read whatever JSON it managed to print."""
    if proc is None:
        return {}
    try:
        out, _err = proc.communicate(timeout=30)
    except subprocess.TimeoutExpired:
        proc.kill()
        try:
            out, _err = proc.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            return {}
    for line in reversed(out.decode(errors="replace").splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                continue
    return {}


def run_coordinator(source: bytes) -> int:
    # (c) IDEMPOTENCE GUARD — before anything else, device or otherwise.
    if already_banked():
        print(f"usb4stream-bench: {RECEIPT_PATH} already exists; "
              "exiting without touching any device", flush=True)
        return 0

    base = {"serve_up": None, "loop": "python", "schedule": schedule_description(),
            "device": {"coordinator": None, "peer": None},
            "open_count": {"coordinator": 0, "peer": 0}}

    # (d)(i) A serve on the shared cable. THE first check: a wedge here takes
    # the night's headline deliverable down with it, so an idle cable is a
    # precondition, not a preference.
    serve_up = serve_is_up()
    base["serve_up"] = serve_up
    if serve_up:
        base["note"] = ("the stream device shares cable A with the serving "
                        "rails; first light runs attended in the morning "
                        "AFTER the pair is torn down")
        write_receipt(SKIP_SERVE_UP, base)
        return 0

    # (d)(ii) The rail peer must answer before we consider a device at all.
    try:
        local = resolve_stream_device()
    except ResolveError as exc:
        # The chain can fail before the ping is even addressable (no rail
        # netdev) or at the configfs group. Both are typed skips, in the
        # order the lane specifies.
        if exc.configfs:
            probe = probe_peer(source)
            base["local_resolve_error"] = str(exc)
            base["peer_probe"] = probe
            write_receipt(SKIP_CONFIGFS_MISSING, base)
            return 0
        base["local_resolve_error"] = str(exc)
        write_receipt(SKIP_PEER_UNREACHABLE, base)
        return 0

    peer_addr = rail_peer_address(local["rail_addr"])
    base["rail"] = {"dev": local["rail"], "addr": local["rail_addr"],
                    "peer": peer_addr}
    if not rail_peer_answers(peer_addr):
        write_receipt(SKIP_PEER_UNREACHABLE, base)
        return 0

    # (d)(iii) BOTH ends' fn0 groups must exist before any open. The local
    # end is proven by resolve_stream_device having returned; the worker's is
    # proven by the read-only probe over the control wire.
    probe = probe_peer(source)
    base["device"]["coordinator"] = local["dev"]
    base["configfs"] = {"coordinator": local["configfs_group"]}
    base["ring_size"] = local["ring_size"]
    base["throttling"] = local["throttling"]
    if not probe.get("ok"):
        base["peer_probe"] = probe
        write_receipt(SKIP_CONFIGFS_MISSING, base)
        return 0
    base["device"]["peer"] = probe.get("dev")
    base["configfs"]["peer"] = probe.get("configfs_group")
    base["peer_ring_size"] = probe.get("ring_size")
    base["peer_throttling"] = probe.get("throttling")

    # --- past this line a device is opened exactly once, and never again ---
    signal.signal(signal.SIGALRM, _alarm)
    proc = None
    fd = None
    started = time.perf_counter()
    try:
        proc = launch_peer(source, probe["dev"])
        set_phase("open")
        fd = open_stream_once(local["dev"])
        base["open_count"]["coordinator"] = open_count()
        signal.alarm(GLOBAL_TIMEOUT_S)
        results = coordinator_schedule(fd, payload_buffer())
    except BaseException as exc:                     # noqa: BLE001 — typed below
        outcome = aborted(exc)
        signal.alarm(0)
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
            fd = None
        if proc is not None and proc.poll() is None:
            proc.kill()
        base["error"] = f"{type(exc).__name__}: {exc}"
        base["wall_s"] = round(time.perf_counter() - started, 3)
        peer_report = kill_peer(proc)
        base["peer_report"] = peer_report
        base["open_count"]["peer"] = peer_report.get("open_count", 0)
        write_receipt(outcome, base)
        return 0
    finally:
        signal.alarm(0)
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass

    base.update(results)
    base["wall_s"] = round(time.perf_counter() - started, 3)
    peer_report = kill_peer(proc)
    base["peer_report"] = peer_report
    base["open_count"]["peer"] = peer_report.get("open_count", 0)
    if peer_report.get("outcome") != "ok" or base["open_count"]["peer"] != 1:
        # The wire numbers stand, but the run is not a clean 'ok': say so in
        # the type rather than in a footnote nobody reads.
        write_receipt(aborted(OSError(errno.EPROTO, "peer report"), "peer"), base)
        return 0
    write_receipt("ok", base)
    return 0


def main(argv: list | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--role", choices=("coordinator", "peer", "probe"),
                        default="coordinator")
    parser.add_argument("--dev", default=None,
                        help="peer role only: the device node the PEER's own "
                             "probe resolved on the peer. Absent, the peer "
                             "resolves it locally.")
    args = parser.parse_args(argv)

    if args.role == "probe":
        return run_probe()
    if args.role == "peer":
        return run_peer(args.dev)
    return run_coordinator(pathlib.Path(__file__).read_bytes())


if __name__ == "__main__":
    sys.exit(main())
