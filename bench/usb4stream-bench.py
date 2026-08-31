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

Which cable — and why the flag exists
-------------------------------------
Two USB4 cables join these twins.

* **Cable A** is the tensor rail: it carries ``thunderbolt0`` / 10.99.0.x, the
  wire TP=2 runs on. It is the default, and resolution on it is unchanged.
* **Cable B** is the parked spare: it carries the other Thunderbolt netdev,
  link-local only, and no serving traffic.

``--cable A|B`` selects between them. **A is the default**, so absent the flag
this file behaves exactly as it always did.

An earlier revision of this docstring called cable B *peerless* — that the
coordinator's low indices belonged to a cable whose worker counterpart had no
configfs groups at all. **That claim is stale and is refuted by the hardware.**
Verified twice on 2026-08-30 by independent inspection: cable B is a fully
peered, provisioned stream pair on both nodes, its ``fn0`` groups exist on both
sides, its hopids interlock (coordinator out 8 → worker in 8, worker out 9 →
coordinator in 9), and its two netdevs ping each other clean. Cable B is a
legitimate — and *safer* — target, because a wedge there costs a parked spare
while a wedge on cable A darkens the rail TP=2 depends on.

What has NOT changed is that nothing may be selected by name or by number.
The service basenames **cross** between the twins: ``0-2.1`` is cable B on the
coordinator and cable A on the worker. A single global override therefore
selects *different* cables on the two boxes, which is exactly the mismatched
open described below. Selection is resolved per node, from a rule, and the
selection travels in argv so both ends resolve the same cable.

Why every mechanic below is non-negotiable
------------------------------------------
Rings are allocated and router paths enabled on the FIRST open; CLOSE is sent
and paths disabled on the LAST close. Cycling that against a half-configured
or mismatched peer corrupts router hop tables, takes ``thunderbolt_net``'s
paths down **on the same cable**, and needs a reboot to clear. That hazard
already darkened rail 0 once. Rail 0 (``thunderbolt0``, 10.99.0.x) rides
cable A alongside this device, so a wedge on cable A would take the campaign's
headline deliverable — a pair still serving at 07:00 — down with it.

Hence, and none of these are style choices:

* **No hardcoded device node.** Numbering is asymmetric across the twins: the
  same logical stream is index 2 on the coordinator and index 0 on the
  worker for one cable and the reverse for the other, and neither index is
  stable across re-enumeration. The node is resolved on EACH node, per the
  chain in ``resolve_stream_device``. This file contains no numbered device
  literal anywhere, and the lane's acceptance argv asserts that. ``--cable``
  is a *label*, not a device: it chooses which netdev the chain starts from.
* **The selection travels in argv.** Both ``probe_peer`` and ``launch_peer``
  put ``--cable`` on the remote python's command line. An environment
  variable would not survive ``ssh``, and a one-sided switch is a CROSS-CABLE
  MISMATCHED OPEN — the precise hop-table wedge this file exists to prevent.
* **A mismatch guard before the open.** The coordinator holds its own
  resolution and the peer's probe report, and asserts they name the same
  cable — by the hopid interlock and by each end's netdev being the other's
  wire peer, never by the device index, which is coincidentally equal on both
  nodes for a given cable and so proves nothing. Disagreement is a typed skip
  and exit 0, with nothing opened.
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
  first of all. That first one is **cable-aware**: see ``serve_blocks_run``.
  Its rationale is *shared hardware*, not the existence of a serve anywhere,
  so on cable B it is decided by whether the chosen stream's router and NHI
  are the ones the serving rail rides — measured, not assumed.
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
                        would use, its hopids, its wire peer, and whether its
                        fn0 configfs group exists. Touches no device. Run on
                        the worker before any open.

``--dry-run``           coordinator-only: run the ENTIRE precondition chain —
                        serve check, resolution, peer reachability, remote
                        probe, mismatch guard — print the decision as JSON,
                        and stop. Opens no device and writes NO receipt, so it
                        cannot arm the idempotence guard. Run this first.

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

# The two cables, as labels. A label is NOT a device: it names which
# Thunderbolt netdev the resolution chain starts from on THIS node. See
# cable_netdev() for the rule and why a global constant cannot express it.
CABLE_A = "A"        # the tensor rail: holds a RAIL_NET address
CABLE_B = "B"        # the parked spare: the other carriered Thunderbolt netdev
CABLES = (CABLE_A, CABLE_B)
DEFAULT_CABLE = CABLE_A

# A Thunderbolt/USB4 XDomain service exposes a ``key`` attribute naming what
# it is. The netdevs we care about hang off services with key=network; the
# stream functions hang off sibling services with key=stream.
NET_SERVICE_KEY = "network"
STREAM_SERVICE_KEY = "stream"

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
# The stream FUNCTION group inside a service. Both twins provision fn0 and fn1
# on every cable; they are independent stream pairs on the SAME router, each
# with its own hopid pair. fn0 is the default because it is what the lane and
# the memo were written against.
#
# It is selectable because a function's hopids are assigned AT PROVISIONING
# TIME and are not all equally usable. Measured 2026-08-31: cable B's fn0
# holds out_hopid 8 on the coordinator and in_hopid 8 on the worker, and 8 is
# exactly the hop thunderbolt_net occupies on every host router in this fleet
# (in_hop_id 0x08 on each port2). Cable B's fn1 (9/10 and 10/9) and cable A's
# fn0 (9/9 both ends) have no such overlap. Opening a function whose hopid the
# netdev already holds on the same cable is the hop-table corruption this file
# exists to avoid, so the operator must be able to name the function without
# editing the file — and WITHOUT writing configfs to re-provision, which this
# file never does.
DEFAULT_FUNCTION = "fn0"
FUNCTIONS = ("fn0", "fn1")
FUNCTION_GROUP = DEFAULT_FUNCTION   # retained: the historical name/default

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
# The anti-wedge skip: the two ends did not agree on which cable they are on.
# Opening under that disagreement is the mismatched open that corrupts hop
# tables, so it is a typed skip with nothing opened, never a best effort.
SKIP_CABLE_MISMATCH = "skipped:cable-mismatch-between-nodes"


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


def _ipv4_addr_lines() -> list:
    """``ip -o -4 addr show`` rows as (netdev, dotted-address) pairs."""
    proc = subprocess.run(["ip", "-o", "-4", "addr", "show"],
                          capture_output=True, text=True)
    rows = []
    for line in proc.stdout.splitlines():
        fields = line.split()
        if len(fields) < 4 or fields[2] != "inet":
            continue
        try:
            addr = ipaddress.ip_address(fields[3].split("/")[0])
        except ValueError:
            continue
        rows.append((fields[1], str(addr)))
    return rows


def local_addresses() -> set:
    """Every IPv4 address THIS node holds, on any interface.

    Used by the peer check to make self-addressing structurally impossible.
    """
    return {addr for _dev, addr in _ipv4_addr_lines()}


def netdev_addresses(dev: str) -> list:
    return [addr for name, addr in _ipv4_addr_lines() if name == dev]


def rail_netdev() -> tuple[str, str]:
    """The netdev carrying the rail /30 address. Never matched by name."""
    for dev, addr in _ipv4_addr_lines():
        if ipaddress.ip_address(addr) in RAIL_NET:
            return dev, addr
    raise ResolveError("no netdev carries a " + str(RAIL_NET) + " address")


def thunderbolt_netdevs() -> list:
    """Every netdev whose sysfs device is an XDomain service with key=network.

    Identified by the service ``key`` attribute, never by the ``thunderboltN``
    name: the numbering is not stable across re-enumeration and the two boxes
    number the same physical cable differently.
    """
    base = "/sys/class/net"
    found = []
    try:
        names = sorted(os.listdir(base))
    except OSError:
        return found
    for name in names:
        service = os.path.join(base, name, "device")
        if not os.path.exists(service):
            continue
        if _read_attr(os.path.join(service, "key")) == NET_SERVICE_KEY:
            found.append(name)
    return found


def has_carrier(dev: str) -> bool:
    return _read_attr(f"/sys/class/net/{dev}/carrier") == "1"


def hardware_address(dev: str) -> str | None:
    return _read_attr(f"/sys/class/net/{dev}/address")


def router_identity(sysfs_path: str) -> tuple:
    """(domain, NHI PCI function) for any Thunderbolt sysfs node.

    A hop table belongs to a router tree, and a router tree hangs off one NHI.
    Two stream functions that share these two share the hardware a wedge would
    corrupt; two that do not, do not. This is what makes the serve
    precondition answerable by measurement instead of by label.
    """
    parts = os.path.realpath(sysfs_path).split("/")
    for i, part in enumerate(parts):
        if part.startswith("domain") and part[len("domain"):].isdigit():
            return part, (parts[i - 1] if i else None)
    return None, None


def cable_netdev(cable: str) -> tuple[str, str]:
    """Which Thunderbolt netdev names the cable we were asked to bench.

    THE RULE, and it is the only thing this file uses to tell the cables
    apart:

      cable A  is the netdev holding a RAIL_NET address — the tensor rail,
               found exactly as it always was, by address and never by name;
      cable B  is the OTHER Thunderbolt netdev on this node that has a
               carrier.

    Both halves are derived per node out of sysfs. Nothing here names a
    netdev, a configfs service basename, or a device index, because none of
    those is stable or even consistent between the twins: the numbering is
    asymmetric AND the service basenames CROSS (the coordinator's ``0-2.1``
    is cable B while the worker's ``0-2.1`` is cable A). A single global
    override would therefore select DIFFERENT cables on the two boxes — the
    mismatched open that wedges hop tables. Hence a rule, resolved per node,
    with the label carried in argv so both ends run the same rule.

    Cable A returns bit-identically to the pre-``--cable`` behaviour.
    """
    rail, rail_addr = rail_netdev()
    if cable == CABLE_A:
        return rail, rail_addr
    others = [dev for dev in thunderbolt_netdevs()
              if dev != rail and has_carrier(dev)]
    if not others:
        raise ResolveError(
            f"cable {cable}: no Thunderbolt netdev besides {rail} has a "
            "carrier, so there is no second cable to bench on this node")
    if len(others) > 1:
        raise ResolveError(
            f"cable {cable} is ambiguous: {len(others)} carriered Thunderbolt "
            f"netdevs besides {rail} ({', '.join(others)}); refusing to guess "
            "which cable was meant")
    other = others[0]
    addrs = netdev_addresses(other)
    if not addrs:
        raise ResolveError(
            f"cable {cable}: {other} carries no IPv4 address, so its wire "
            "peer cannot be established without opening the device")
    return other, addrs[0]


def rail_peer_address(addr: str) -> str:
    """The other host on the /30: .1 <-> .2.

    Valid ONLY inside RAIL_NET, which is why it is no longer the peer check.
    Off the /30 it is not a peer derivation at all, it is arithmetic: cable
    B's 169.254.17.133 has a last octet that is neither 1 nor 2, so the swap
    yields 169.254.17.1 — an address on the /16 that is nobody in particular.
    Measured 2026-08-30: it does not answer, so the old check would have
    banked a spurious ``rail-peer-unreachable`` skip on a cable that pings
    clean; and had some unrelated station on that /16 answered, the check
    would have PASSED while proving nothing whatever about this cable. Either
    way a safety check silently converted into a no-op. See peer_address()
    and peer_reachable(), which establish the peer on the chosen interface and
    cannot be satisfied by this node itself.
    """
    octets = addr.split(".")
    octets[-1] = "2" if octets[-1] == "1" else "1"
    return ".".join(octets)


def neighbours(dev: str) -> list:
    """IPv4 neighbour entries on one interface: (address, lladdr, state).

    The kernel never places THIS node's own address in its neighbour table,
    which is the property the peer check leans on.
    """
    proc = subprocess.run(["ip", "-o", "-4", "neigh", "show", "dev", dev],
                          capture_output=True, text=True)
    rows = []
    for line in proc.stdout.splitlines():
        fields = line.split()
        if not fields:
            continue
        try:
            addr = str(ipaddress.ip_address(fields[0]))
        except ValueError:
            continue
        lladdr = None
        if "lladdr" in fields:
            lladdr = fields[fields.index("lladdr") + 1]
        state = fields[-1] if fields[-1].isupper() else None
        rows.append((addr, lladdr, state))
    return rows


def peer_address(dev: str, addr: str, cable: str) -> str | None:
    """The address of the OTHER end of this cable, on this interface.

    Cable A keeps the /30 derivation it always had — inside RAIL_NET the swap
    is exact and can never name this node. Cable B has no /30 to derive from,
    so the peer is read out of the interface's own neighbour table: an entry
    there is, by construction, a remote station reached through this netdev.
    """
    if cable == CABLE_A and ipaddress.ip_address(addr) in RAIL_NET:
        return rail_peer_address(addr)
    mine = local_addresses()
    for candidate, lladdr, state in neighbours(dev):
        if candidate in mine or not lladdr:
            continue
        if state in ("FAILED", "INCOMPLETE"):
            continue
        return candidate
    return None


def peer_reachable(dev: str, peer: str | None, own_hwaddr: str | None) -> dict:
    """Establish that a DIFFERENT machine answers on THIS interface.

    The check the octet swap used to stand in for, done properly. Three
    conjuncts, and the run proceeds only if all three hold:

      1. the candidate is not an address this node holds — so the check can
         never be satisfied by pinging ourselves, which is exactly how the
         old swap passed vacuously off the /30;
      2. a ping bound to THIS interface answers — the cable carries traffic;
      3. the interface's neighbour table then holds that address with a link
         layer address that is not our own and is not FAILED/INCOMPLETE —
         so the answer came off the wire and from someone else.
    """
    report = {"netdev": dev, "peer": peer, "reachable": False}
    if not peer:
        report["error"] = f"no peer address could be established on {dev}"
        return report
    if peer in local_addresses():
        report["error"] = (f"candidate peer {peer} is an address of THIS node; "
                           "refusing a self-satisfying reachability check")
        return report
    try:
        proc = subprocess.run(["ping", "-c1", "-W2", "-I", dev, peer],
                              capture_output=True, timeout=10)
        report["ping"] = proc.returncode == 0
    except (OSError, subprocess.SubprocessError):
        report["ping"] = False
    entry = next(((a, ll, st) for a, ll, st in neighbours(dev) if a == peer),
                 None)
    if entry is None:
        report["error"] = f"{peer} has no neighbour entry on {dev}"
        return report
    _addr, lladdr, state = entry
    report["neigh_lladdr"] = lladdr
    report["neigh_state"] = state
    if not lladdr:
        report["error"] = f"{peer} resolved to no link-layer address on {dev}"
        return report
    if own_hwaddr and lladdr == own_hwaddr:
        report["error"] = (f"{peer} resolves to this interface's OWN link-layer "
                           f"address {lladdr}; that is not a peer")
        return report
    if state in ("FAILED", "INCOMPLETE"):
        report["error"] = f"{peer} neighbour state on {dev} is {state}"
        return report
    report["reachable"] = bool(report.get("ping"))
    if not report["reachable"]:
        report["error"] = f"ping to {peer} bound to {dev} did not answer"
    return report


def stream_service(rail: str) -> tuple[str, str]:
    """netdev -> its thunderbolt service -> parent xdomain -> the sibling
    service whose ``key`` attribute is ``stream``. Returns (xdomain, service).

    Starting from the netdev is what makes the cable selection physical: the
    sibling under the SAME xdomain is on the same cable as the netdev, by
    construction, on either node, whatever the basenames happen to be.
    """
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
        if _read_attr(os.path.join(candidate, "key")) == STREAM_SERVICE_KEY:
            return xdomain, candidate
    raise ResolveError(f"no sibling service with key=stream under {xdomain}")


def configfs_group(service_path: str,
                   function: str = DEFAULT_FUNCTION) -> str:
    """The named function's configfs group for this service. READ-ONLY."""
    name = os.path.basename(service_path)
    tried = []
    for root in CONFIGFS_ROOTS:
        group = os.path.join(root, name, function)
        tried.append(group)
        if os.path.isdir(group):
            return group
    raise ResolveError(
        f"{function} configfs group absent for service {name} "
        f"(looked under: {', '.join(tried)})", configfs=True)


def resolve_stream_device(cable: str = DEFAULT_CABLE,
                          function: str = DEFAULT_FUNCTION) -> dict:
    """The whole chain on THIS node. Reads sysfs/configfs; opens nothing.

    the chosen cable's netdev (cable_netdev: the /30 holder for A, the other
    carriered Thunderbolt netdev for B) -> readlink
    /sys/class/net/NETDEV/device -> parent xdomain -> sibling service with
    key=stream -> that service's fn0 configfs group -> its ``index``
    attribute -> the device node.

    With cable A — the default — this is byte for byte the chain it always
    was. ``ring_size``, ``throttling`` and the hopids are READ; nothing here
    opens a configfs attribute for writing.
    """
    rail, addr = cable_netdev(cable)
    xdomain, service = stream_service(rail)
    group = configfs_group(service, function)
    index = _read_attr(os.path.join(group, "index"))
    if index is None or not index.isdigit():
        raise ResolveError(f"{group}/index is unreadable or not an index",
                           configfs=True)
    domain, nhi = router_identity(service)
    peer = peer_address(rail, addr, cable)
    return {
        "cable": cable,
        "function": function,
        "rail": rail,
        "rail_addr": addr,
        "hwaddr": hardware_address(rail),
        "peer_addr": peer,
        "xdomain": xdomain,
        "service": service,
        "domain": domain,
        "nhi": nhi,
        "configfs_group": group,
        "index": int(index),
        "dev": DEVICE_PREFIX + index,
        "in_hopid": _read_attr(os.path.join(group, "in_hopid")),
        "out_hopid": _read_attr(os.path.join(group, "out_hopid")),
        "ring_size": _read_attr(os.path.join(group, "ring_size")),
        "throttling": _read_attr(os.path.join(group, "throttling")),
    }


def cable_disagreement(local: dict, probe: dict) -> str | None:
    """THE ANTI-WEDGE GUARD. Are both ends on the same cable? None if yes.

    Called with the coordinator's own resolution and the worker's read-only
    probe report, BEFORE anything is opened. Three independent witnesses,
    all of which must agree:

      1. the cable LABEL each side resolved — cheap, and catches the case the
         flag failed to travel in argv at all;
      2. the HOPID INTERLOCK — our out_hopid must be the peer's in_hopid and
         our in_hopid must be the peer's out_hopid. This is the router's own
         view of the path and is the thing a mismatched open corrupts. On
         these twins cable A interlocks 9->9 both ways and cable B interlocks
         out 8 -> in 8 and out 9 -> in 9, so a cross-cable pairing (our 8
         against their 9) is caught here even if a label lied;
      3. each end's netdev being the OTHER's wire peer — our peer address is
         their netdev address and theirs is ours. Independent of configfs
         entirely.

    Deliberately NOT compared: the device index and the configfs service
    basename. The index is coincidentally EQUAL on both nodes for a given
    cable, so agreement there proves nothing; the basenames CROSS between the
    twins, so equality there would be positively wrong.
    """
    if not probe.get("ok"):
        return "peer probe did not resolve a device"
    if probe.get("cable") != local.get("cable"):
        return (f"cable label mismatch: this node resolved cable "
                f"{local.get('cable')}, the peer resolved cable "
                f"{probe.get('cable')}")
    for mine_key, theirs_key in (("out_hopid", "in_hopid"),
                                 ("in_hopid", "out_hopid")):
        mine, theirs = local.get(mine_key), probe.get(theirs_key)
        if mine is None or theirs is None:
            return (f"hopid interlock unverifiable: local {mine_key}={mine}, "
                    f"peer {theirs_key}={theirs}")
        if mine != theirs:
            return (f"hopid interlock broken: local {mine_key}={mine} against "
                    f"peer {theirs_key}={theirs} — the two ends are not on the "
                    "same path")
    if local.get("peer_addr") and probe.get("rail_addr"):
        if local["peer_addr"] != probe["rail_addr"]:
            return (f"wire peer mismatch: this node's peer is "
                    f"{local['peer_addr']} but the probing node's netdev holds "
                    f"{probe['rail_addr']}")
    if probe.get("peer_addr") and local.get("rail_addr"):
        if probe["peer_addr"] != local["rail_addr"]:
            return (f"wire peer mismatch: the peer's peer is "
                    f"{probe['peer_addr']} but this node's netdev holds "
                    f"{local['rail_addr']}")
    return None


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


def serve_blocks_run(cable: str, base: dict,
                     function: str = DEFAULT_FUNCTION) -> bool:
    """Does a live serve forbid a run on THIS cable? Called only if one is up.

    The precondition is not "a serve exists somewhere"; it is "a wedge here
    would take the serve down". Those coincide on cable A and they do not on
    cable B, so the check is decided on hardware rather than on the fact of a
    running container.

    Cable A — unchanged, and unconditional. The stream functions on cable A
    hang off the same router and the same NHI as ``thunderbolt0``, the rail
    the serve's tensor traffic rides. First open enables that router's paths
    and last close disables them; cycling that against a half-configured peer
    corrupts the hop tables and takes thunderbolt_net's paths down on the same
    cable. So: a live serve is a typed skip, exactly as before.

    Cable B — judged, not assumed. The rationale above is a claim about shared
    hardware, and shared hardware is measurable: compare the chosen stream
    service's (domain, NHI) against the serving rail netdev's. On these twins
    they differ (the two cables land on different PCI functions and therefore
    different Thunderbolt domains), so a wedge on cable B cannot reach the
    rail's hop tables. If they turn out to MATCH — a different cabling, a
    re-enumeration that put both cables on one router — the shared-hardware
    rationale applies again and the run is skipped exactly as on cable A.
    Anything we cannot measure is treated as shared.
    """
    if cable == CABLE_A:
        base["note"] = ("the stream device shares cable A with the serving "
                        "rails; first light runs attended in the morning "
                        "AFTER the pair is torn down")
        return True
    try:
        local = resolve_stream_device(cable, function)
        rail, _addr = rail_netdev()
    except ResolveError:
        # We cannot establish the hardware relation. Do not skip on that
        # basis: fall through to the typed resolution skips below, which
        # refuse the run anyway and say why — without touching a device.
        return False
    rail_domain, rail_nhi = router_identity(f"/sys/class/net/{rail}/device")
    stream_hw = (local.get("domain"), local.get("nhi"))
    serve_hw = (rail_domain, rail_nhi)
    base["serve_hardware"] = {"serving_rail": rail, "serving_router": serve_hw,
                              "bench_router": stream_hw}
    if None in stream_hw or None in serve_hw or stream_hw == serve_hw:
        base["note"] = (f"a serve is up and cable {cable}'s stream router "
                        f"{stream_hw} could not be shown disjoint from the "
                        f"serving rail's {serve_hw}; treating the cable as "
                        "shared and skipping")
        return True
    base["serve_shares_bench_hardware"] = False
    base["note"] = (f"a serve is up, but cable {cable}'s stream functions sit "
                    f"on router {stream_hw} while the serving rail {rail} sits "
                    f"on {serve_hw}: different NHI, different router, so the "
                    "open/close cycle cannot reach the serving hop tables. "
                    "The shared-cable rationale does not apply and the run "
                    "proceeds")
    return False


def probe_peer(source: bytes, cable: str,
               function: str = DEFAULT_FUNCTION) -> dict:
    """Run THIS FILE on the worker in probe role over the control wire.

    Streamed on stdin — never copied to the worker's disk. Read-only there.
    The cable travels in ARGV: an environment variable set here would not
    survive ssh, and a peer that resolved the other cable is the mismatched
    open this whole file exists to prevent.
    """
    command = ("python3 - --role probe --cable " + shlex.quote(cable)
               + " --function " + shlex.quote(function))
    proc = subprocess.run(_ssh_argv(command),
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

def run_probe(cable: str, function: str = DEFAULT_FUNCTION) -> int:
    """Read-only. Resolves the named cable, reports, opens nothing.

    Also reports the wire peer it sees on that netdev, so the coordinator can
    cross-check that the two ends face each other before any open.
    """
    report: dict = {"ok": False, "host": os.uname().nodename, "cable": cable,
                    "function": function}
    try:
        resolved = resolve_stream_device(cable, function)
    except ResolveError as exc:
        report["error"] = str(exc)
        report["configfs_missing"] = exc.configfs
    else:
        report.update(resolved)
        report["peer_reachable"] = peer_reachable(
            resolved["rail"], resolved.get("peer_addr"), resolved.get("hwaddr"))
        report["ok"] = True
    print(json.dumps(report), flush=True)
    return 0


def run_peer(dev: str | None, cable: str,
             function: str = DEFAULT_FUNCTION) -> int:
    """The mirror side. Its own alarms, its own one-open rule, its own JSON.

    The cable arrives in argv. When the coordinator also passed the device it
    resolved for us, the two are cross-checked here: a disagreement means the
    hardware re-enumerated between the probe and this launch, and opening on
    a stale node is the mismatched open. Refuse, do not reopen, do not guess.
    """
    signal.signal(signal.SIGALRM, _alarm)
    report = {"role": "peer", "outcome": "ok", "open_count": 0, "dev": dev,
              "cable": cable, "function": function}
    fd = None
    try:
        resolved = resolve_stream_device(cable, function)
        report["ring_size"] = resolved["ring_size"]
        report["throttling"] = resolved["throttling"]
        report["in_hopid"] = resolved["in_hopid"]
        report["out_hopid"] = resolved["out_hopid"]
        report["resolved_dev"] = resolved["dev"]
        if dev and dev != resolved["dev"]:
            raise ResolveError(
                f"the coordinator asked this node to open {dev} for cable "
                f"{cable}, but cable {cable} resolves to {resolved['dev']} "
                "here; refusing a mismatched open")
        dev = resolved["dev"]
        report["dev"] = dev
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


def launch_peer(source: bytes, dev: str, cable: str,
                function: str = DEFAULT_FUNCTION) -> subprocess.Popen:
    """Stream THIS FILE to the worker's python on stdin. Never a copy.

    ``--cable`` rides in ARGV alongside ``--dev`` for the same reason it does
    in probe_peer: nothing else crosses ssh, and a peer on the other cable is
    the mismatched open. The peer re-resolves and refuses if the two disagree.
    """
    command = (f"python3 - --role peer --cable {shlex.quote(cable)} "
               f"--function {shlex.quote(function)} "
               f"--dev {shlex.quote(dev)}")
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


class Skip(Exception):
    """A typed precondition refusal: the outcome, the evidence, nothing opened.

    Raised only from ``coordinator_preconditions``, i.e. only from code that
    has not touched a device. The coordinator turns it into the one receipt;
    ``--dry-run`` prints it and writes nothing at all.
    """

    def __init__(self, outcome: str, base: dict):
        super().__init__(outcome)
        self.outcome = outcome
        self.base = base


def coordinator_preconditions(source: bytes, cable: str,
                              function: str = DEFAULT_FUNCTION) -> tuple:
    """Everything that must hold BEFORE a device is opened, and nothing else.

    Returns (local_resolution, peer_probe, base). Raises ``Skip`` carrying the
    typed outcome when a precondition refuses the run. Touches no device;
    ``--dry-run`` runs exactly this and then stops, which is how a run can be
    rehearsed in full without any chance of banking a receipt.
    """
    base = {"serve_up": None, "loop": "python", "cable": cable,
            "schedule": schedule_description(),
            "device": {"coordinator": None, "peer": None},
            "open_count": {"coordinator": 0, "peer": 0}}

    # (d)(i) A serve on the shared cable. THE first check: a wedge on the
    # cable the serve rides takes the night's headline deliverable down with
    # it, so an idle cable is a precondition, not a preference. What counts as
    # "the shared cable" is decided by serve_blocks_run, on hardware.
    serve_up = serve_is_up()
    base["serve_up"] = serve_up
    if serve_up and serve_blocks_run(cable, base, function):
        raise Skip(SKIP_SERVE_UP, base)

    # (d)(ii) The peer on the CHOSEN cable must answer before we consider a
    # device at all.
    try:
        local = resolve_stream_device(cable, function)
    except ResolveError as exc:
        # The chain can fail before the peer is even addressable (no netdev
        # for this cable) or at the configfs group. Both are typed skips, in
        # the order the lane specifies.
        base["local_resolve_error"] = str(exc)
        if exc.configfs:
            base["peer_probe"] = probe_peer(source, cable, function)
            raise Skip(SKIP_CONFIGFS_MISSING, base)
        raise Skip(SKIP_PEER_UNREACHABLE, base)

    reach = peer_reachable(local["rail"], local.get("peer_addr"),
                           local.get("hwaddr"))
    base["rail"] = {"dev": local["rail"], "addr": local["rail_addr"],
                    "peer": local.get("peer_addr"), "cable": cable}
    base["peer_reachable"] = reach
    if not reach.get("reachable"):
        raise Skip(SKIP_PEER_UNREACHABLE, base)

    # (d)(iii) BOTH ends' fn0 groups must exist before any open. The local
    # end is proven by resolve_stream_device having returned; the worker's is
    # proven by the read-only probe over the control wire, on the SAME cable
    # because the label went out in argv.
    probe = probe_peer(source, cable, function)
    base["device"]["coordinator"] = local["dev"]
    base["configfs"] = {"coordinator": local["configfs_group"]}
    base["ring_size"] = local["ring_size"]
    base["throttling"] = local["throttling"]
    base["hopids"] = {"coordinator": {"in": local["in_hopid"],
                                      "out": local["out_hopid"]}}
    if not probe.get("ok"):
        base["peer_probe"] = probe
        raise Skip(SKIP_CONFIGFS_MISSING, base)
    base["device"]["peer"] = probe.get("dev")
    base["configfs"]["peer"] = probe.get("configfs_group")
    base["peer_ring_size"] = probe.get("ring_size")
    base["peer_throttling"] = probe.get("throttling")
    base["hopids"]["peer"] = {"in": probe.get("in_hopid"),
                              "out": probe.get("out_hopid")}

    # (d)(iv) THE MISMATCH GUARD. Both ends have now resolved independently;
    # they must have landed on the same cable. A cross-cable open is the
    # hop-table wedge, so disagreement is a typed skip with nothing opened —
    # never a best effort, never a retry on the other cable.
    disagreement = cable_disagreement(local, probe)
    if disagreement:
        base["peer_probe"] = probe
        base["cable_mismatch"] = disagreement
        raise Skip(SKIP_CABLE_MISMATCH, base)
    return local, probe, base


def run_dry_run(source: bytes, cable: str,
                function: str = DEFAULT_FUNCTION) -> int:
    """Rehearse the whole precondition chain. Opens nothing, writes nothing.

    Deliberately does NOT write a receipt on any path, including the skip
    paths: a spurious ``skipped:`` receipt would arm the idempotence guard
    against every future run, and the point of this mode is to find out what
    the real run would decide WITHOUT taking that risk.
    """
    verdict = {"role": "dry-run", "cable": cable,
               "host": os.uname().nodename,
               "receipt_exists": already_banked(),
               "receipt_path": str(RECEIPT_PATH)}
    try:
        local, probe, base = coordinator_preconditions(source, cable, function)
    except Skip as skip:
        verdict["decision"] = "would-skip"
        verdict["outcome"] = skip.outcome
        verdict["base"] = skip.base
    except ResolveError as exc:
        verdict["decision"] = "would-skip"
        verdict["outcome"] = "resolve-error"
        verdict["detail"] = str(exc)
    else:
        verdict["decision"] = "would-proceed"
        verdict["coordinator"] = local
        verdict["peer"] = probe
        verdict["base"] = base
    verdict["receipt_written"] = False
    verdict["devices_opened"] = 0
    print(json.dumps(verdict, indent=1), flush=True)
    return 0


def run_coordinator(source: bytes, cable: str = DEFAULT_CABLE,
                    function: str = DEFAULT_FUNCTION) -> int:
    # (c) IDEMPOTENCE GUARD — before anything else, device or otherwise.
    if already_banked():
        print(f"usb4stream-bench: {RECEIPT_PATH} already exists; "
              "exiting without touching any device", flush=True)
        return 0

    try:
        local, probe, base = coordinator_preconditions(source, cable, function)
    except Skip as skip:
        write_receipt(skip.outcome, skip.base)
        return 0

    # --- past this line a device is opened exactly once, and never again ---
    signal.signal(signal.SIGALRM, _alarm)
    proc = None
    fd = None
    started = time.perf_counter()
    try:
        proc = launch_peer(source, probe["dev"], cable, function)
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
                             "probe resolved on the peer. The peer re-resolves "
                             "regardless and refuses if the two disagree.")
    parser.add_argument("--cable", choices=CABLES, default=DEFAULT_CABLE,
                        help="which USB4 cable to resolve on THIS node: A is "
                             "the tensor rail (the netdev holding the rail "
                             "/30) and is the default; B is the other "
                             "carriered Thunderbolt netdev. The label is "
                             "passed to the peer in argv so both ends resolve "
                             "the same cable.")
    parser.add_argument("--function", choices=FUNCTIONS,
                        default=DEFAULT_FUNCTION,
                        help="which stream function group inside the "
                             "service to resolve. Both twins provision fn0 "
                             "and fn1 on every cable, as independent pairs "
                             "with their own hopids; a function whose hopid "
                             "collides with thunderbolt_net's on the same "
                             "router must not be opened. Passed to the peer "
                             "in argv so both ends resolve the same one.")
    parser.add_argument("--dry-run", action="store_true",
                        help="coordinator only: run every precondition, print "
                             "the decision, open nothing and write no receipt.")
    args = parser.parse_args(argv)

    if args.role == "probe":
        return run_probe(args.cable, args.function)
    if args.role == "peer":
        return run_peer(args.dev, args.cable, args.function)
    source = pathlib.Path(__file__).read_bytes()
    if args.dry_run:
        return run_dry_run(source, args.cable, args.function)
    return run_coordinator(source, args.cable, args.function)


if __name__ == "__main__":
    sys.exit(main())
