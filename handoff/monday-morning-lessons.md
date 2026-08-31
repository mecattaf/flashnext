# Monday morning lessons — 2026-08-31

What the weekend actually established, what it overturned, and what is blocking.
Written after the Gloo blocker was closed and after the USB4STREAM bench was run
twice and failed twice for a reason that turned out to matter more than the
measurement would have.

Everything below is measured on the twins on 2026-08-30/31. Where a figure is
inferred, it says so. Where I was wrong earlier, it says that too.

---

## 1. The Thunderbolt estate, verbatim

This section exists because every serious mistake this weekend came from someone
— including me — reasoning about this hardware from a name instead of from its
identity. Names here are not stable. Indices are not stable. The only stable
anchors are the reciprocal neighbour entries and the hopid interlock.

### 1.1 The two boxes

```
coordinator   booted 2026-08-30 18:42:41   NixOS, kernel 7.2.2, gfx1151
worker        booted 2026-08-30 17:41:34   NixOS, kernel 7.2.2, gfx1151

/etc/hosts, coordinator:            /etc/hosts, worker:
  127.0.0.1  localhost               127.0.0.1  localhost
  ::1        localhost               ::1        localhost
  10.42.0.1  nas                     10.42.0.1  nas
  10.42.0.5  worker                  10.42.0.5  worker
  127.0.0.2  coordinator             127.0.0.2  worker
```

That last line on each box is the stock NixOS mapping and it cost a full night.
See §3.

### 1.2 Every interface, both nodes

```
COORDINATOR                                  WORKER
lo            127.0.0.1/8                    lo            127.0.0.1/8
              10.99.9.1/32   fleet identity                10.99.9.2/32   fleet identity
enp191s0      10.99.1.1/30   5GbE wire       enp191s0      10.99.1.2/30   5GbE wire
wlp192s0      10.42.0.2/24   house wifi      wlp192s0      10.42.0.5/24   house wifi
thunderbolt0  10.99.0.1/30   CABLE A         thunderbolt0  10.99.0.2/30   CABLE A
thunderbolt1  169.254.17.133/16  CABLE B     thunderbolt1  169.254.53.173/16  CABLE B
tailscale0    100.105.121.73/32              (none)
```

MTU targets (`modules/lowlat-cluster.nix`): `thunderbolt0` 65520, `thunderbolt1`
65520, `enp191s0` 9000. thunderbolt-net tops out at 65520; the 5GbE path is a
normal NIC and silently refuses 65520, so it takes the conventional 9000.

The `10.99.9.x/32` addresses live on `lo` and are the estate's declared stable
identity (`hosts/coordinator/eth-fleet.nix:15,45`). They are routed over the
5GbE wire: `ip route get 10.99.9.2` → `via 10.99.1.2 dev enp191s0 src 10.99.1.1`.
Because they sit on `lo` they are up regardless of any cable's link state.

The default route on **both** boxes is `via 10.42.0.1 dev wlp192s0` — the house
wifi. This matters enormously and is the cause of a whole class of bugs: any
library that discovers "its own" address by probing the default route gets the
wifi. See §3.3.

### 1.3 Cable A — the tensor rail

```
                    COORDINATOR                    WORKER
netdev              thunderbolt0                   thunderbolt0
address             10.99.0.1/30                   10.99.0.2/30
MAC                 02:39:21:e6:84:9f              02:3a:84:5a:c4:6d
NHI (PCI fn)        0000:c5:00.6                   0000:c4:00.5
domain              domain1                        domain0
xdomain             1-2                            0-2
key=network svc     1-2.0                          0-2.0
key=stream  svc     1-2.1                          0-2.1

stream functions on that service:
  fn0   index 2   in_hopid 9   out_hopid 9   -> /dev/tbstream2
  fn1   index 3   in_hopid 10  out_hopid 10  -> /dev/tbstream3
  (identical on both nodes)

ring_size 1024, throttling 2048 on every group.
```

Hopid interlock: coordinator out 9 → worker in 9; worker out 9 → coordinator in 9.
Symmetric, and neither end touches hop 8.

### 1.4 Cable B — the parked spare

```
                    COORDINATOR                    WORKER
netdev              thunderbolt1                   thunderbolt1
address             169.254.17.133/16 (link-local) 169.254.53.173/16 (link-local)
MAC                 02:c6:eb:a3:f4:e0              02:4e:e0:7d:e4:e9
NHI (PCI fn)        0000:c5:00.5                   0000:c4:00.6
domain              domain0                        domain1
xdomain             0-2                            1-2
key=network svc     0-2.0                          1-2.0
key=stream  svc     0-2.1                          1-2.1

stream functions on that service:
  COORDINATOR                       WORKER
  fn0  index 0  in 9  out 8   -> /dev/tbstream0     fn0  index 0  in 8  out 9   -> /dev/tbstream0
  fn1  index 1  in 10 out 9   -> /dev/tbstream1     fn1  index 1  in 9  out 10  -> /dev/tbstream1

ring_size 1024, throttling 2048 on every group.
```

Hopid interlock: coordinator out 8 → worker in 8; worker out 9 → coordinator in 9
for `fn0`. For `fn1`: coordinator out 9 → worker in 9; worker out 10 → coordinator
in 10. Both interlock correctly.

Cable B pings clean: `169.254.17.133 ↔ 169.254.53.173`, 3/3, 0.089–0.120 ms, and
the neighbour entries are `REACHABLE` with each other's MAC on both sides.

### 1.5 The three things about this topology that trap people

**(a) The domains CROSS between the twins.** Cable A is `domain1` on the
coordinator and `domain0` on the worker. Cable B is `domain0` on the coordinator
and `domain1` on the worker. Consequently **the same service basename means a
different physical cable on the two boxes**: `0-2.1` is cable B on the
coordinator and cable A on the worker. Any global override keyed on a service
name is therefore not merely fragile, it is arithmetically incapable of being
right on both nodes at once.

**(b) The device indices are not stable, and the documents that recorded them
are now wrong.** `docs/USB4STREAM-TRANSPORT.md` §5 records "index 2 on the
coordinator against index 0 on the worker" as the cross-twin asymmetry, and
`bench/usb4stream-bench.py`'s docstring says cable B's "worker counterpart has no
configfs groups at all — peerless". Neither holds. Today the numbering is
**symmetric per cable** — cable A is index 2 on both nodes, cable B is index 0 on
both — and cable B is a fully peered, hopid-matched pair on both nodes. The
memo's own warning that indices drift across re-enumeration is the thing that
came true.

**(c) Hopid 8 is taken.** `thunderbolt_net` occupies `in_hop_id 0x08` on every
host router in this fleet (`0x801c0801` on each `port2`). Cable B's `fn0` was
provisioned with `out_hopid 8` on the coordinator and `in_hopid 8` on the worker.
Cable A's `fn0` (9/9) and cable B's `fn1` (10/9 and 9/10) have no such overlap.
This turned out **not** to be the cause of the bench failure — but it is a live
hazard and `fn0` on cable B should not be opened until it is re-provisioned.

### 1.6 The hard resource limit that shapes everything

The Strix Halo NHI has **exactly 3 DMA rings per controller** — verified in round
1 through the driver's own debugfs and independently corroborated by the only
other known cross-host Strix transport project. Those three are: control,
thunderbolt-net, and one more. The advertised *second* native lane per cable
permanently fails its boot-time probe with a cosmetic `-12` (a laundered
`-EINVAL` from `nhi_alloc_hop`, "invalid hop: -1"); it is permanent, harmless,
cleans up fully, and never retries.

Two cables therefore yield at most **two** usable lanes, not four. Any "4-rail
aggregate" figure comes from different hardware. Both cables train at 20 Gb/s × 2
= 40 Gbps regardless; TB5 buys nothing on these USB4 hosts.

**This 3-ring budget is why a single leaked ring is fatal rather than merely
untidy.** See §5.

---

## 2. Every number measured this weekend

### 2.1 TCP round-trip, p50 (µs) — the first time TCP-over-Thunderbolt was ever measured here

| | 64 B | 4 KiB | 8 KiB | 16 KiB | 64 KiB |
|---|---|---|---|---|---|
| **thunderbolt0** | 130.42 | 130.42 | 130.44 | 130.44 | 329.43 |
| **enp191s0 (5GbE)** | 56.59 | 138.33 | 144.24 | 136.31 | 315.94 |

p99 (µs), from the receipt (2-round aggregates):

| | 64 B | 4 KiB | 8 KiB | 16 KiB | 64 KiB |
|---|---|---|---|---|---|
| thunderbolt0 | 191.45 | 274.75 | 345.37 | 225.86 | 411.25 |
| enp191s0 | 73.21 | 143.84 | 194.69 | 144.13 | 352.24 |

Minimum at 64 B: **thunderbolt0 34.41 µs**, enp191s0 54.84 µs.

Harness validated by reproducing the previously-recorded 5GbE 4 KiB figure of
137.8 µs at 138.13 / 138.54. Interface pinning proven by per-netdev counter
deltas on both nodes (tb0 round 0: `thunderbolt0 rx=402007` against
`enp191s0 rx=80`; server side agrees at 402012). 20 000 iterations × 2 rounds.

**The flatness is the finding.** Eight independent size×round measurements
agreeing to 0.03 µs across an eight-fold size range is not a fabric
characteristic. The fabric floor is 34 µs. The other ~100 µs is
`thunderbolt_net`'s wakeup/coalescing path — pure software overhead sitting on a
fast link. That is precisely the overhead USB4STREAM exists to bypass.

### 2.2 Throughput, single stream

| | TX | RX |
|---|---|---|
| thunderbolt0 | 8.81 Gb/s | 9.20 Gb/s |
| enp191s0 | 4.71 Gb/s | 4.71 Gb/s |

1 GiB transfers. The ethernet figure landing at 94 % of 5GbE line rate is a
strong internal self-check on the harness.

### 2.3 Ping latencies, for orientation

```
coordinator -> 10.99.1.2  (wire peer, enp191s0)      97 us
coordinator -> 10.99.0.2  (rail peer, thunderbolt0) 100 us
coordinator -> 169.254.53.173 (cable B)              89-120 us
worker      -> 10.99.9.1  (fleet identity)          139 us
worker      -> 10.42.0.2  (house wifi)             8862 us
preflight   -> coordinator/thunderbolt0             127 us  (inside the 200 us budget)
preflight   -> worker/thunderbolt0                  102 us
```

The house wifi is **64× the fleet wire**. Any code that accidentally uses it pays
that on every round trip.

### 2.4 Gloo all-reduce — measured, but DO NOT rely on the magnitudes

p50 (µs): thunderbolt0 379.8 / 628.5 / 522.1 / 528.0 / 664.0 against enp191s0
231.4 / 238.9 / 258.9 / 259.3 / 353.1 at 64 B / 4 K / 8 K / 16 K / 64 K.

**This measurement is not banked properly and contradicts the ping-pong data.**
Its `netdev_delta` was computed by the script and never written to the receipt,
so there is no on-disk proof of which interface it used. And at 4 KiB tb0 is
7.9 µs *faster* on raw TCP but 389.6 µs *slower* on all-reduce, which "two round
trips" cannot explain. tb0's all-reduce *minimum* (235.2 µs) is only 54 µs off
ethernet's (181.25 µs) while its p50 is 390 µs off — that profile says
interrupt/coalescing mode, i.e. a tunable nobody has touched, not a property of
the fabric.

Also: **Gloo is not RCCL.** vLLM's tensor all-reduce rides RCCL. Gloo was
substituted because torch lives only in the container and RCCL needs GPUs. It is
a fair instrument for *comparing two wires* (framework overhead cancels in the
difference) and is not a substitute for an RCCL number.

Treat the direction as a reproducible signal of unexplained origin. Re-measure
with counters banked and a loopback control before anything is decided on it.

---

## 3. The Gloo blocker — closed

### 3.1 What was broken

vLLM builds its world group as `backend="cpu:gloo,cuda:nccl"`
(`parallel_state.py:1504`). Every rank therefore stands up **two** process
groups, not one:

| group | library | env namespace | carries |
|---|---|---|---|
| device | RCCL | `NCCL_*` | tensors — every all-reduce |
| CPU | Gloo | `GLOO_*` | pickled metadata, barriers, the RCCL unique-id handshake |

The CPU group had never been configured. `GLOO_SOCKET_IFNAME` appeared **nowhere**
in the repository:

```
$ git grep -in gloo -- host scripts container bench tests probes patches docs specs README.md
$ echo $?
1
```

`fn-env.sh` is an explicit port of the ds4-vllm estate's `ds4-cluster-env.sh`. It
took line 29 (`NCCL_SOCKET_IFNAME=thunderbolt0`) and elaborated it heavily — the
rail chooser, the peer-reachability gate, the wire-fallback rung, the receipted
`FN_TRANSPORT_RUNG`. It did not take line 30
(`GLOO_SOCKET_IFNAME=thunderbolt0`).

### 3.2 Why the omission was silent instead of loud

Torch creates the CPU group with no explicit device, so
`ProcessGroupGloo::createDefaultDevice()` decides the bind address:

1. if `GLOO_SOCKET_IFNAME` is set, use that interface;
2. otherwise `gethostname()`, and **bind it if it resolves**;
3. otherwise fall back to loopback.

NixOS writes `127.0.0.2 <hostname>` into `/etc/hosts`, so step 2 *succeeds*.
`127.0.0.2` is a perfectly usable address — it is simply not reachable from the
other machine. Gloo never reaches its loopback fallback, never warns, and both
ranks publish loopback into the store. Reproduced verbatim in the pristine
environment before any fix was claimed:

```
[rank0] hostname coordinator -> 127.0.0.2 | GLOO_SOCKET_IFNAME=None
ERROR failed to connect, retry=1..4, rank=0, size=2,
      local=[127.0.0.1]:162, remote=[127.0.0.2]:9608, SO_ERROR: Connection refused
Gloo connectFullMesh failed ... timed out connecting
-> RuntimeError: Engine core initialization failed.
[rank0] FAIL after 6.3s        [rank1] HUNG until a 90 s hard kill
```

**Note the asymmetry, which the original log never showed: rank 0 errors in six
seconds and rank 1 hangs forever.** In a real serve that presents as a silent
engine-core hang, not a diagnosable crash.

Had these boxes been Ubuntu (`127.0.1.1`) or had the hostname not resolved, step
3 would have produced the same failure with the loopback address stated outright.
The NixOS mapping is what turned an omission into a mystery.

### 3.3 The fix, and the two paths that were both broken

`GLOO_SOCKET_IFNAME=enp191s0`, override-able. **Not** thunderbolt0, and this is
the one place where the wire genuinely is the right answer — for availability,
not speed. `NCCL_SOCKET_IFNAME` is *computed* at runtime with a documented
terminal fallback to the wire when no rail carries a reachable peer. A gloo pin
hardcoded to the rail would, on a dark-rail night, leave the CPU group dialling a
dead interface while RCCL ran on the wire — the same silent `connectFullMesh`
timeout, caused by the fix for it. Gloo has no verbs alternative and no second
transport. The group carries kilobytes at bring-up, so nothing is lost.

Exporting it was not enough. **Both delivery paths were broken:**

- `ENV_FILTER` is defined as an independent literal in *both* `fn-cluster-up.sh`
  and `fn-preflight.sh`, and is what builds the `podman --env-file`. It had no
  `GLOO_`, so the export alone was a **silent no-op**. Proven:
  `OLD filter -> <ABSENT>`, `NEW filter -> GLOO_SOCKET_IFNAME=enp191s0`.
- `vllm/ray/ray_env.py:36-44`'s `DEFAULT_ENV_VAR_PREFIXES` has `NCCL_` but not
  `GLOO_`. Set `VLLM_RAY_EXTRA_ENV_VAR_PREFIXES_TO_COPY=FN_,GLOO_`.

A third fix went in alongside, pre-empting the next wall rather than discovering
it: **per-node `VLLM_HOST_IP` on the podman command line.**
`parallel_state.py:518-521` stands up a `MessageQueue` for the TP group;
`shm_broadcast.py:522-530` binds `tcp://get_ip():0`; `get_ip()` UDP-probes
`8.8.8.8` and returns the default-route address — the **house wifi**.
`wait_until_ready()` is a bare `recv()` with no timeout, so an AP hiccup would
hang first light forever with an empty log. It must **differ per node**
(`10.99.9.1` / `10.99.9.2`); setting it identically makes the worker bind an
address that does not exist on it. A podman `-e` flag never enters the byte-diff
stream, and upstream excludes `VLLM_HOST_IP` from the driver→actor copy
(`ray_utils.py:32-40 WORKER_SPECIFIC_ENV_VARS`) precisely so it can differ.

### 3.4 Proof

```
(b) pinned: init_process_group OK 6.1s | allreduce [3,3,3,3] -> PASS  (8.1s total)
```

Delivered through the project's own path with **no `-e` overrides** — the absence
being the assertion that `ENV_FILTER` → `--env-file` carries it. vLLM's own first
post-world-group collectives run: `_node_count` correctly returns 2 across
machines, `broadcast_object_list` crosses, a 4 MiB CPU all-reduce completes in
11 ms. The pin reaches a real Ray actor on both nodes. 5/5 on repeat runs.
`fn-preflight.sh` passes non-vacuously with `GLOO_` in the graded stream.
201 repo tests unchanged.

### 3.5 An adversarial finding that overturned both design documents

```
GLOO_SOCKET_IFNAME=lo                          -> FAILS (loopback again)
GLOO_SOCKET_IFNAME=thunderbolt0                -> PASSES (the ds4-parity escape hatch is real)
GLOO_SOCKET_IFNAME=nosuchif0                   -> RuntimeError, names the interface
GLOO_SOCKET_IFNAME=thunderbolt0,nosuchif0      -> same RuntimeError (2nd name resolved)
GLOO_SOCKET_IFNAME=thunderbolt0,thunderbolt1   -> HANGS FOREVER, no exception, no log line
```

Torch splits on comma and creates **one transport device per name**; every name
must resolve. The design doc said "gloo honours only the first entry"; the
implementer's correction said "a comma value fails outright". Both wrong, and
both wrong in the direction that would tempt a maintainer to relax the guard that
prevents it.

**The dangerous state is addressed-but-peerless.** A name that does not resolve
fails loudly and names itself. `thunderbolt1` resolves — via its self-assigned
`169.254.x` — and then has nothing on the far end, so the connect blocks with no
timeout and prints nothing. That is the one configuration that hangs silently,
and cable B is currently in it.

---

## 4. Transport: Thunderbolt is the right choice. I said otherwise and I was wrong.

**The tensors are on Thunderbolt. They were never moved. They should not be
moved.** Live configuration:

```
NCCL_SOCKET_IFNAME=thunderbolt0     <- the tensor path. Unchanged all weekend.
GLOO_SOCKET_IFNAME=enp191s0         <- a kilobyte-scale setup channel. This is what was added.
```

I led an earlier write-up with "at 64 B the 5GbE wire is 2.3× faster than the
rail". That is true and it is **irrelevant** — 64 bytes is not a size tensor
parallelism uses. At the decode-relevant ~5 KB the ordering reverses: **130.4 µs
on thunderbolt0 against 138.3 µs on the wire.** Thunderbolt also carries roughly
double the bandwidth, which matters for prefill and for MTP verify widths.

And the asymmetry that actually decides it: **Thunderbolt's 130 µs is mostly
software; ethernet's is not.** The Thunderbolt fabric floor measured 34 µs. The
other ~100 µs is the `thunderbolt_net` stack, and it is removable — that is
exactly what USB4STREAM and RDMA do, and both only exist on Thunderbolt. 5 gigabit
copper is 5 gigabit copper forever. There is no upside path on the wire.

I also relayed a subagent's "consider flipping the collective to ethernet"
recommendation far too strongly. It rested on the Gloo proxy number that the same
agent had explicitly flagged as untrustworthy (§2.4). It should have been dropped,
not repeated. ds4-vllm putting the tensor path on `thunderbolt0` is correct and
nothing measured this weekend argues against it.

**What the measurements *do* legitimately overturn** is narrower: the ladder in
`DECISIONS-2026-08-30.md` describes `wire-fallback` as "degraded". For the
*metadata* channel it is not degraded, and for small messages the wire is
genuinely faster. That is a footnote about a control plane, not a change of
transport of record.

---

## 5. USB4STREAM — the bench ran twice, and NixOS is the blocker

### 5.1 What was wrong with the tool

It could not target cable B at all. `resolve_stream_device()` anchored on the
module constant `RAIL_NET = 10.99.0.0/30`, which is cable A *by definition* — so
`--role probe` resolved `/dev/tbstream2`, the tensor rail, on both nodes.
Two further defects:

- `rail_peer_address()` derived the peer by swapping the last octet 1↔2. Valid
  only on the /30. On cable B's `169.254.17.133` it produced `169.254.17.1`,
  which does not answer — so the check would have banked a spurious
  `skipped:rail-peer-unreachable` on a cable that pings clean, arming the
  idempotence guard forever. And on the /30 it could pass while proving nothing.
- `probe_peer()` and `launch_peer()` passed no environment to the worker, so a
  coordinator-side switch would produce a **cross-cable mismatched open** — the
  hop-table wedge the file exists to prevent, hitting the tensor rail.

All three are now fixed: `--cable {A,B}` and `--function {fn0,fn1}` threaded to
the peer **in argv**, a three-conjunct peer check that refuses a self-satisfying
result, and a mismatch guard with three independent witnesses (cable label, hopid
interlock, wire peer) that fires before any open. Cable A's resolution is proven
bit-identical to the previous behaviour. 224 tests pass.

The guard earned its keep immediately: the first `fn1` attempt left two
`probe_peer` call sites unthreaded, the worker resolved `fn0` against the
coordinator's `fn1`, and the hopid-interlock witness refused the run before any
device was touched.

### 5.2 What happened when it ran

```
cable B, fn0:  aborted:open:ENOMEM   OSError: [Errno 12] ... '/dev/tbstream0'   1 ms
cable B, fn1:  aborted:open:ENOMEM   OSError: [Errno 12] ... '/dev/tbstream1'   1 ms
```

No numbers. **No wedge** — both cables ping clean afterwards, all four host-router
hop tables byte-identical and clean, zero kernel messages from either run.

`fn1` failing identically **refutes the hopid-8 collision hypothesis.** It is not
the hop.

### 5.3 The actual cause, and it traces to a NixOS defect

```
Aug 30 18:42:41  coordinator boots
Aug 30 18:42:54  coordinator cable-B stream group created   <- 13 s after boot
Aug 30 18:42:56  worker cable-A stream group created
Aug 30 18:48:16  worker      kernel: thunderbolt 0000:c4:00.6: TX ring 1 already stopped
                 worker      kernel: WARNING: drivers/thunderbolt/nhi.c:760 at tb_ring_stop
                                     Workqueue: events_long tbnet_disconnect_work
                                     tbnet_tear_down+0x11f/0x180 [thunderbolt_net]
Aug 30 18:48:17  coordinator kernel: thunderbolt 0000:c5:00.5: 0:1: hop deactivation failed
                                     for hop 0, index 1
```

`0000:c4:00.6` is the worker's **cable B** NHI. `0000:c5:00.5` is the
coordinator's **cable B** NHI. Cable A's controllers (`c4:00.5`, `c5:00.6`)
logged nothing, and cable A's stream measured fine on Aug 30.

Neither node has rebooted since (coordinator up 15.0 h from 18:42:41, worker
16.1 h from 17:41:34; the failures are at 18:48, after both boots).

So: **cable B's controllers leaked a DMA ring on both nodes during a
`thunderbolt_net` teardown, and with 3 rings per controller as the hard budget
(§1.6), there is no longer a ring available for a stream.** Hence ENOMEM in one
millisecond with the kernel silent — ring exhaustion, not hop collision, which is
why the driver's `"TX hop %d already allocated"` warnings (unconditional
`dev_warn`, not dynamic-debug gated) never printed.

**And the trigger is the NixOS provisioning defect.** `modules/usb4-stream.nix`
provisions on `rail = "thunderbolt0"` and its header says it touches "the rail-0
cable only". It provisioned cable B anyway, 13 seconds after boot, because its
anchor — "the cable thunderbolt0 rides" — is stable in *name* but not in
*physical cable* across enumeration and the `tb-fleet.nix` PCI rebind. Its own
HAZARDS block describes exactly this window: *"HopIDs are a SHARED, RACEABLE
budget with thunderbolt_net, and the netdev existing does not mean it has claimed
its own yet — it claims in `tbnet_open`, not at probe. Provisioning inside that
window starves the IP rail (#262)."* The `#262` fix gates on CARRIER — but cable
B has no IP configured, so its carrier/claim sequence differs and the gate does
not cover it. Six minutes later `tbnet_tear_down` hit a ring that the stream
provisioning had disturbed, `tb_ring_stop` warned, and the ring was never
released.

### 5.4 What is needed

1. **Reboot both nodes.** That is the only way to clear a leaked NHI ring, and it
   is what stands between us and the measurement.
2. Fix the provisioning anchor so it cannot recur (dotfiles#271 item 3): record
   the resolved cable identity — xdomain path plus hopid pair, never the index —
   and refuse to provision when it differs from the recorded one.
3. Re-provision cable B's `fn0` off hop 8, or leave `fn0` alone and use `fn1`.
4. Then re-run: `python3 bench/usb4stream-bench.py --cable B --function fn1`.
   Move `results/receipts/usb4stream.json` aside first — the idempotence guard is
   armed by the abort record, which is deliberately kept.

### 5.5 What the measurement is worth, once it can be taken

`docs/USB4STREAM-TRANSPORT.md` §4 criterion 1 wants exchange p50 at 8–16 KiB at
or under ~40 µs. The bar is now anchored to something real for the first time:
TCP on the *same cable* is 130.4 µs. The module's recorded 14.3 µs stream RTT at
64 B would be a **~9× win over TCP on its own cable**, not the ~4× that comparing
against a different cable implied. And the ~100 µs between the fabric floor and
the netdev's p50 is a measured upper bound on what raw DMA can recover.

All three of §4's criteria are currently **unevaluable, not failed**: no
`usb4stream.json` numbers, no `results/bench/` (TP=2 has never reached first
light), and no RDMA A/B. RDMA Gate 0 is also not satisfied — the weekend's work
is a raw-socket and CPU-Gloo proxy, not a TP=2 benchmark, and the gate's own
verification command targets a `bench.json` that does not exist.

---

## 6. Where NixOS is the blocker

Filed as **mecattaf/dotfiles#271**. Four items, in order of what they cost:

1. **The hostname resolves to `127.0.0.2`** — cost a night, and the class is much
   wider than torch. Fix: point it at the fleet identity instead
   (`networking.hosts."127.0.0.2" = lib.mkForce [ ]` plus
   `"10.99.9.1" = [ "coordinator" ]` / `"10.99.9.2" = [ "worker" ]`). Because
   `10.99.9.x` lives on `lo`, it never goes down with a cable, and it is routable
   from the peer. Then *anything* that binds by hostname is correct with no
   application-level pin at all.
2. **`thunderbolt1` is unaddressed**, which is the uniquely dangerous
   addressed-but-peerless state (§3.5). Fix: give it a real `/30` on both ends
   together — `10.99.2.1/30` and `10.99.2.2/30` — in `tb-fleet.nix`. A one-sided
   `/30` recreates the bad state and is worse than today.
3. **`usb4-stream.nix` provisions the cable it says it never touches**, and that
   is what leaked the ring blocking the benchmark (§5.3). This is the direct
   blocker on USB4STREAM.
4. **`common.nix:153` points `worker` at the house wifi** — latent, 64× slower,
   and partially subsumed by item 1, but the two entries must then be reconciled
   deliberately rather than left to `/etc/hosts` first-match ordering (the NAS
   still needs `10.42.0.5` for Immich).

---

## 7. The lessons that generalise

**The plan modelled one transport; the system has two.** Every transport artifact
in this project — the rung ladder, the rail chooser, `FN_TRANSPORT_RUNG` in every
receipt, the RDMA A/B, the USB4STREAM memo, the cable analysis — reasons about
RCCL moving tensors. vLLM also stands up a CPU group on a different library with
a different env namespace and its own interface-selection logic. It moves
kilobytes, so it is invisible to every frame the planning used: bandwidth,
latency, decode budget, transport rungs. It is only visible in a *bring-up*
frame, and there wasn't one — `cp-build` → `cp-smoke` → `cp-tp2` treat bring-up
as a gate to pass, not a system with components to enumerate.

**We mined the reference estate for answers to questions we already had.** The
line we needed was directly below the line we took. `ds4-cluster-env.sh:29`
became an elaborately engineered rail chooser; `:30` was never read. The fix for
this is mechanical and should be done: reconcile every export in
`ds4-cluster-env.sh` (and its `.tcp.sh` / `.rdma.sh` variants) against
`fn-env.sh`, with each variable either ported or explicitly declined **in
writing**. Anything present there and absent here without a stated reason is the
next blocker.

**A distro default turned a loud failure into a silent one, twice.** The
`127.0.0.2` mapping made a missing env var look like a network fault. The
`169.254.x` self-assignment on an unaddressed cable makes a dead link look like a
live one. In both cases the software's *fallback* was correct and never fired,
because a bad-but-valid value was available. Prefer configurations where wrong is
absent rather than wrong is plausible.

**Reproduce the failure before believing the fix.** The Gloo fix is trusted
because configuration (a) was run first and failed in the documented way. Two
claims in the design documents that were never tested against hardware — the
comma-list behaviour, stated two different and both wrong ways — survived until
something adversarial was actually run.

**Stopping is a result.** The first USB4STREAM agent produced no measurement and
refused to bank a `skipped:` receipt that would have armed the idempotence guard
against every future run. That was worth more than a number, and the second run's
ENOMEM — traced to a leaked ring from a NixOS provisioning race — is worth more
than the latency figure would have been, because it is a cause rather than a
data point.

**Wall-clock estimates do not belong in this record.** Earlier drafts repeated
the memo's "2–4 attended days" for the doorbell port. What is load-bearing is the
*scope* — reimplement the reference doorbell and progress-thread all-reduce
against `read`/`write` on the stream chardev, touching no vLLM distributed init —
not a duration nobody can hold anyone to.
