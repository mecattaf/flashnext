# USB4STREAM as a tensor transport — the decision memo

**Status:** decided, twice, on 2026-08-30.

> **No wall-clock estimates in this memo.** Earlier revisions carried
> "2-4 attended days", "3-5 attended days" and "2-4 attended weeks" figures.
> They were authored without knowledge of the current codebase and kept
> resurfacing as if measured. Scope claims — line counts, which pieces port
> verbatim, which are genuinely hard — are verifiable and stay. Durations are
> not, and are deliberately absent. Do not reintroduce them. This file exists so the morning
reads *one* memo and does not re-deliberate. The deliberation record is
`docs/DECISIONS-2026-08-30.md` §5; the numbers this memo waits on are banked
by `bench/usb4stream-bench.py` into `results/receipts/usb4stream.json`.

**One-line answer:** the collective-library net-plugin route is **rejected for
the latency goal**; the real path is a **port of the reference doorbell +
progress-thread allreduce** from verbs onto the stream device's
`read`/`write`, and it is built **only if the trigger criteria in §4 hold**.

---

## 1. What the primitive is

`thunderbolt_stream` is in-tree (`intree: Y`), authored by the Thunderbolt
maintainer, with a formal ABI document shipped at kernel 7.2. It exposes a
character device per configured stream function over raw NHI DMA rings — no IP
stack, no socket layer. Driver facts that shape everything below, read from
`stream.c`:

- 4 KiB DATA frames; **one kernel copy per direction** (`copy_page_from_iter`
  on write, `copy_page_to_iter` on read). No zero-copy, no GPU pointers: it is
  a userspace byte pipe.
- **Rings are allocated and router paths enabled on FIRST open; CLOSE is sent
  and paths disabled on LAST close.** This single sentence is the origin of
  every safety mechanic in the bench.
- Peer close is cleanly detectable — reader gets EOF, writer gets `-ENXIO` —
  **without retrying an open**.
- Read and write serialize on one per-device mutex, so a high-rate
  bidirectional transport wants `fn0`/`fn1` as a unidirectional pair.

Previously measured on these twins: **14.3 µs RTT at 64 B**, **21.8 / 25.3 µs
p50/p99 at 4 KiB**, ~841 MB/s per stream at ring 4096 (we run ring 1024, so
expect less). Against TCP-over-5GbE's 137.8 µs p50 at 4 KiB, the wire is not
the question. The question is what sits on top of it.

Strategic weight: everything RDMA here rides an **out-of-tree patched stack
that dies at every kernel bump**. An in-tree, maintainer-shipped, rebuild-free
primitive that lands within ~1.5× of the RDMA bar is a defensible transport of
record for a two-box fleet, precisely because the RDMA treadmill is a
recurring attended tax.

## 2. The ncclNet-plugin route — REJECTED for the latency goal

The collective library *does* load net plugins: the `ncclNet_vX` interface is
documented, `librccl-net.so` is the hook, and `aws-ofi-rccl` is the existence
proof. Rejection is not "it cannot be built". It is that it cannot reach the
goal:

1. **It cannot approach the 105 µs reference bar, because that bar was set by
   *bypassing* the collective library.** The reference allreduce hooks in
   *above* the library entirely. A net plugin sits *under* its proxy and
   protocol machinery — LL/Simple protocols, host staging, proxy-thread hops —
   which adds its own tens-to-hundreds of microseconds on tiny messages. The
   plugin would beat TCP-socket transport modestly and miss the point.
2. **HopID scarcity forces a multiplexer.** The library wants a
   listen/connect/accept triplet per peer *per channel*, and the engine
   creates multiple communicators. The stream primitive offers ~10 usable
   HopIDs, **shared with `thunderbolt_net`'s own paths**, provisioned as
   kernel state rather than `connect()`-on-demand. Connections therefore
   cannot map onto streams 1:1: every channel must be multiplexed over one or
   two long-lived streams, with the plugin owning flow control and
   head-of-line management. That is a real mux/demux engine, not a shim.
3. **Its lifecycle is the wedge pattern.** Communicator teardown and re-init —
   which happens on *every serve restart* — would drive stream open/close
   cycles, exactly what must never happen. Avoiding that forces the plugin to
   hold fds open for process lifetime and multiplex, compounding (2).

**Honest cost, corrected in both directions:** with this deployment's
simplifications (2 ranks, one peer, host pointers only, channels pinned to
1–2) a competent author gets a first working allreduce as a *fragile prototype*.
The mux layer, the 8-outstanding-request semantics
under the proxy threads, collective hang debugging, p99 validation and
wedge-safe lifecycle across restarts put "trustworthy enough to leave serving
overnight" as the genuinely hard part.

Two alternatives were dismissed with reasons, so they stay dismissed: a custom
`torch.distributed` ProcessGroup backend (far more
invasive in distributed init, delivers nothing the communicator hook doesn't);
and using the stream for the bootstrap/control plane only (bootstrap is not
latency-critical, 5 GbE sockets are proven, and it would put wedge-hazard
device opens in the serve path for zero performance).

Cheap attended check before anyone re-litigates this: confirm the shipped
collective library honors `NCCL_NET_PLUGIN` at all
(`strings librccl.so | grep librccl-net`). Two minutes.

## 3. The real path — port the doorbell allreduce onto the stream device

Keep the reference architecture: **doorbell + spin kernel + progress thread**.
Replace ~150–200 lines of ibverbs with an ~100-line fd pump:

- The progress thread now also runs the **receive path**: poll/`read` peer
  bytes into pinned UMA buffers, then store the flag the GPU spins on.
- Send is `write` on the stream fd, from the same pinned buffers.
- The wrapper and the ~51-line engine communicator hook port **nearly
  verbatim** — this is the reason the port is small.

Cost versus RDMA at decode payloads: +2 syscalls, +2 kernel copies (sub-µs at
these sizes), +progress-thread wakeup on RX. **Inferred landing zone: ~120–160
µs against the 105 µs RDMA bar** at batch-1 decode payloads, degrading at
speculative verify widths where the ~841 MB/s wire term bites.

**Scope, not schedule:** ~100 lines of fd pump replacing ~150-200 lines of ibverbs; the wrapper and the ~51-line engine communicator hook port near-verbatim. For
bulk/prefill/weights the primitive is *not* the answer and never will be — it
loses on bandwidth to both patched RDMA and plausibly to plain
TCP-over-`rail0`. The claim is scoped to **the decode allreduce, the op
that is the ceiling**.

## 4. Morning trigger criteria — build the port only if ALL hold

1. **Banked exchange p50 at 8–16 KiB is at or under ~40 µs**, with a tight
   p99. Read it from `results/receipts/usb4stream.json`,
   `data.exchange_us["8192"].p50` and `["16384"].p50`.
2. **The bench matrix shows TP=2 decode is allreduce-dominated** —
   `results/bench/` plus the sync tracer. If decode is not allreduce-bound,
   a faster allreduce buys proportionally nothing.
3. **The attended RDMA A/B either fails, or wins by a margin that does not
   justify its per-kernel-update rebuild treadmill.**

If projected stream-AR lands within ~1.5× of measured RDMA-AR, make the
in-tree stream the **decode transport of record** and retire the RDMA stack to
a benchmark reference. **If (1) fails — fat exchange latencies or unstable p99
— the stream stays a bench curiosity and RDMA remains the only sub-socket
path.** Second cheap check worth two minutes: benchmark
TCP-over-`rail0` now that the firewall is open, to close the "does the
stream even beat TCP on this wire" question.

## 5. Why the numbers probably are not banked yet — and that is correct

`cp-usb4stream` runs **dead-last, after `cp-close`**, and **skips while the
pair is serving**. The stream device shares **cable A** with the serving rails
and the same NHI. Each first open enables router paths and each last close
disables them; cycling that against a half-configured or mismatched peer
corrupts hop tables, kills `thunderbolt_net`'s paths on that same cable, and
needs a reboot — which is forbidden overnight and would take the TP=2 rail
down with it. That hazard already darkened rail 0 once.

So a live serve is a **typed skip**, not a co-existence experiment. An earlier
synthesis had the bench record co-existence data while the pair served; the
dissent was accepted on its own terms — the co-existence datum is readable in
the morning anyway, while a wedge on the shared cable is the single overnight
act capable of destroying the headline deliverable (a pair still serving at
07:00).

**After a healthy night the expected outcome is
`skipped:serve-up-on-shared-cable`**, pre-declared as such in
`docs/MORNING.md`. **The first real run happens attended in the morning, AFTER
the pair is torn down.** Re-run it then, by hand:

```
# only after the pair is down
python3 bench/usb4stream-bench.py
```

The idempotence guard means the receipt from the skipped overnight run must be
moved aside first (it is the record of the skip; keep it), or the attended run
will exit immediately without touching a device — which is exactly the
behaviour that makes harness retries storm-free.

Two device-numbering traps the bench resolves and no human should re-derive at
07:00: **the same logical stream carries different indices on the two boxes**
(recorded 2026-08-30: index 2 on the coordinator against index 0 on the
worker), and low indices on one box can belong to **cable B, whose
counterpart has no configfs groups at all** — a blocking open there waits
forever on a stream that can never become valid. The indices are not stable
across re-enumeration either: on the coordinator the same cable-A stream
service (`1-2.1`, `key=stream` under xdomain `1-2` on `domain1`) presented
`fn0 index=0, ring_size=1024, throttling=2048` when this memo was written.
Read the resolved paths out of the receipt (`data.device`), never a
remembered number. That is why the
bench resolves the node on each side (rail netdev holding the 10.99.0.0/30 →
`readlink /sys/class/net/$rail/device` → parent xdomain → sibling service with
`key == stream` → that group's `index`) and requires **both** ends' `fn0`
groups to exist before any open.

## 5a. AMENDMENT 2026-08-31 — the first real run goes on cable B

§5 above says the bench rides **cable A**, the serving cable, and calls cable
B *peerless* (a cable "whose counterpart has no configfs groups at all"). Both
statements are superseded. Recorded here rather than edited away, because the
reasoning in §5 stays correct — it is the premises that were wrong.

**The peerless claim is refuted.** Verified twice on 2026-08-30 by independent
inspection of both nodes: cable B is a fully peered, provisioned stream pair.
Its `fn0` configfs groups exist on both boxes (`ring_size=1024`,
`throttling=2048`, like cable A's), its hopids interlock — coordinator out 8 →
worker in 8, worker out 9 → coordinator in 9 — and its two netdevs ping each
other clean at 0.12 ms. There is no peerless cable on these twins. §3.2's
separate rejection of cable B was about a zero-copy RX train this project does
not carry, and does not bear on the bench.

**Operator ruling: the bench targets cable B.** The hazard §5 describes is
real and unchanged — first open enables router paths, last close disables
them, and cycling that against a mismatched peer corrupts hop tables and needs
a reboot. The ruling is about *what a wedge costs*. A wedge on cable A takes
down `rail0`, the rail TP=2 depends on. A wedge on cable B costs a
parked spare carrying nothing but link-local. Same hazard, an order of
magnitude less blast radius, and the two cables land on **different PCI
functions and therefore different Thunderbolt domains** on both nodes, so a
cable-B wedge cannot reach the serving rail's hop tables at all. The bench
verifies that disjointness at runtime rather than trusting this paragraph.

**What changed in `bench/usb4stream-bench.py`:**

- `--cable {A,B}`, **default A**, so absent the flag every behaviour is
  bit-identical to what §5 describes. Cable A is still the netdev holding the
  10.99.0.0/30 address; cable B is the *other* carriered Thunderbolt netdev on
  that node. Still no netdev name, service basename or device index anywhere:
  the basenames **cross** between the twins (`0-2.1` is cable B on the
  coordinator and cable A on the worker), so any global constant would select
  different cables on the two boxes.
- The cable travels to the worker **in argv**, on both the probe and the peer
  launch. Nothing else crosses `ssh`, and a one-sided switch is precisely the
  cross-cable mismatched open the whole file exists to prevent.
- A **mismatch guard** runs before any open: the two ends must agree on the
  cable label, the hopid interlock must close, and each end's netdev must be
  the other's wire peer. Disagreement is a typed skip
  (`skipped:cable-mismatch-between-nodes`) with nothing opened. The device
  index is deliberately *not* compared — it is coincidentally equal on both
  nodes for a given cable and so proves nothing.
- The peer-reachability precondition no longer derives the peer by swapping
  the last octet of a /30. That swap is meaningless off the /30 (cable B's
  `169.254.17.133` swaps to `169.254.17.1`, nobody), so the check had silently
  become a no-op. It is now a neighbour-reachability test on the chosen
  interface that cannot be satisfied by this node itself.
- The serve precondition is **cable-aware**. Its rationale is shared hardware,
  not the existence of a serve: cable A is blocked unconditionally, exactly as
  before, while a cable-B run is blocked only if its stream router and NHI
  cannot be shown disjoint from the serving rail's. Anything unmeasurable is
  treated as shared.
- `--dry-run` rehearses the entire precondition chain, prints the decision,
  and writes **no receipt**. Run it first: a spurious `skipped:` receipt would
  arm the idempotence guard against every future run.

Read the resolved paths, hopids and cable label out of the receipt
(`data.cable`, `data.device`, `data.hopids`), never a remembered number.

## 5b. AMENDMENT 2026-08-31 — first light BANKED, on cable B, at PM QoS 100 µs

The receipt §4(1) waits on exists: `results/receipts/usb4stream.json`,
`outcome: ok`, 2026-08-31T14:37:40, cable B, `fn0` both ends (hopids 10/10
under the #276 pin), ring 1024, throttling 2048. **Power regime recorded in
the receipt: `/dev/cpu_dma_latency` read 100 µs on both nodes before and
after the run** — the retired 14.3 µs figure died for not naming its regime;
this one names it.

| metric | p50 µs | p99 µs |
|---|---|---|
| RTT 64 B | 16.19 | 24.86 |
| RTT 4 KiB | 24.73 | 36.95 |
| RTT 16 KiB | 49.78 | 55.36 |
| RTT 64 KiB | 141.61 | 157.15 |
| exchange 8 KiB | **13.67** | 23.13 |
| exchange 16 KiB | **20.06** | 25.59 |
| exchange 64 KiB | 58.47 | 68.88 |

Throughput: 1248 MB/s coordinator→peer, 1153 MB/s peer→coordinator
(ring 1024; §1's ~841 MB/s expectation was set at ring 4096).

**Trigger criterion §4(1) HOLDS**, with margin: exchange p50 at 8 and 16 KiB
is 13.67 and 20.06 µs against the ~40 µs bar, and the p99s are tight (23.1,
25.6). Criteria (2) allreduce-dominance and (3) the RDMA A/B remain open —
this amendment resolves (1) and only (1).

Against the same-day TCP-over-`rail0` baseline (PM QoS 100 µs, post-rename,
3000 iterations: 64 B p50 130.47 µs, 4 KiB p50 130.51 µs, 16 KiB p50
259.33 µs), the stream RTT wins **8.1× at 64 B, 5.3× at 4 KiB, 5.2× at
16 KiB** — and the stream's p50s sit *below TCP's own minima* (81.80 / 50.97 /
105.94 µs). The ~100 µs flat p50 TCP shows across a 64× payload range is
software cost in `thunderbolt_net`'s path, and bypassing the IP stack removes
it, as hypothesized.

Two constraints learned on the way to the number, both decision-relevant for
the §3 port:

1. **Exactly ONE stream may be open per NHI while that cable's
   `thunderbolt_net` netdev is up.** The two ENOMEM aborts (receipts
   `usb4stream.aborted-enomem-0927/0943.json`) were never a memory problem:
   the driver launders every ring-allocation failure into `-ENOMEM`, and the
   dominant real cause is NHI ring-slot exhaustion — these NHIs have
   `hop_count = 3`, hop 0 is the control channel, hop 1 is
   `thunderbolt_net`'s while the netdev is up, leaving hop 2 as the single
   stream slot. Verified live: with `fn0` held open, opening `fn1` fails
   ENOMEM at any ring_size while the kernel logs `invalid hop: -1`;
   `ring_size` is irrelevant to this. Consequence: §1's "a high-rate
   bidirectional transport wants `fn0`/`fn1` as a unidirectional pair" is
   **not buildable on a cable that keeps its netdev** — a two-stream design
   needs both cables (one stream each), or the cable's netdev taken down to
   free hop 1, which is untested and re-enters the #262 claim-window hazard.
2. **Frames written before the peer's open are silently dropped, not
   queued.** The first `ok` run required an open barrier in the bench (peer
   opens first and announces; the coordinator opens only after reading the
   announcement — see `wait_peer_open`). Receipt
   `usb4stream.aborted-etimedout-1433.json` is the deadlock that found it.
   Any transport built on this device must order its opens explicitly; it
   cannot assume the wire buffers pre-open writes.

## 5c. What the one-stream-per-NHI limit does to the §3 port — and the C-state answer

§5b's constraint 1 kills a design §1 and §3 assume. §1 records that *"read and
write serialize on one per-device mutex, so a high-rate bidirectional transport
wants `fn0`/`fn1` as a unidirectional pair."* **That pairing is impossible on one
cable while its IP rail is up** — the NHI has three ring-hop slots, the control
channel holds one, `thunderbolt_net` holds the second, and the stream gets the
third. Two opens on one NHI return ENOMEM at every ring size tested, including 32.

Three routes out, in the order they should be considered:

1. **One bidirectional stream, and check whether the mutex ever contends.**
   This is what §5b measured, and it already delivers the 8.1×/5.3× win. The
   mutex is held for the *copy*, not the wire transit — sub-µs against a 24.7 µs
   4 KiB round trip — and if the progress thread does write-then-read
   sequentially it is never contended at all. **Do this first.** The split below
   optimizes a bottleneck nobody has demonstrated.

2. **Split directions across cables** — write on cable A's stream, read on
   cable B's, mirrored between ranks. Two devices, two mutexes, no
   serialization; it is the §1 pairing rehomed onto hardware that can host it,
   and it fits an all-reduce's symmetric simultaneous shape well. Cable parity
   makes it free: both train 20 Gb/s × 2 lanes and ping within 1 µs, so a round
   trip crossing two different cables costs nothing.
   **The cost is availability, and it is easy to undersell.** Today the two
   cables are independent — one wedges, the other is a spare. Splitting welds
   them into ONE transport with TWO single points of failure: lose either and
   both directions die. It also spends cable B permanently, and cable B being
   free is the only reason the ENOMEM above was diagnosable at all (dynamic
   debug, forced open/close cycling, ring-size sweeps — none of which may touch
   a serving cable). And it reintroduces per-host asymmetric configuration,
   the exact class that produced dotfiles#267.
   **Verdict: a contingency, not the default.** Take it only if (1) measures
   real mutex contention.

3. **Free hop 1 by taking a cable's `thunderbolt_net` down**, giving two streams
   on one cable. UNTESTED, and it re-enters the tbnet hopid claim window that
   dotfiles#262 documents as wedge-prone. Concentrates both streams on one
   cable's bandwidth for no gain over (2). Not recommended.

**The C-state question §5b's regime line raises, answered.** Holding PM QoS at
100 µs permits C2, whose 18 µs exit latency *exceeds* the entire 16.19 µs 64 B
stream RTT — which looked like it should have swamped the measurement. It did
not: 16.19 µs at budget 100 against 14.3 µs at budget 0. The reason is that the
penalty only appears once a path idles long enough to reach C2. Measured on
rail0 ICMP the same day, mean RTT by inter-message gap: 2 ms → 0.087 ms,
5 ms → 0.112, 10 ms → 0.143, 50 ms → 0.150, with the *minimum* flat at ~0.08 ms
throughout — the floor never moves, only the share of samples paying a wake.
A tight exchange never idles that long.

Consequence for the port: **a progress thread that spins pays nothing, and one
that blocks pays only if gaps exceed a few ms.** Decode gates are ~1069 µs
apart (§14.1), inside the free region. So `pmqosLatencyUs = 100` is the right
fleet default for this transport — the ~60 W/box the POLL floor cost buys
nothing here. Pinning and spinning the progress thread is available via
`sched_setaffinity` without any kernel parameter; `isolcpus` is NOT indicated,
because the APU shares a package power budget and §14.1 puts own-GPU-compute at
~88% of the gate against exchange's ~11% — trading GPU boost to save a C-state
exit on the 11% component is a wash at best.

## 6. Drafted, NOT filed — issue body for the port

> **Title:** Port the doorbell + progress-thread allreduce from verbs onto the
> in-tree USB4 stream device (decode transport of record, two-box fleet)
>
> **Context.** Our sub-socket transport today is an out-of-tree patched
> Thunderbolt/verbs stack that must be rebuilt at every kernel bump — a
> recurring attended tax. The kernel now ships an in-tree stream primitive
> (`thunderbolt_stream`, 7.2+, formal ABI, Thunderbolt maintainer): 4 KiB DATA
> frames over raw NHI DMA rings, one kernel copy per direction, character
> device per configured function. Measured on our twins: 14.3 µs RTT at 64 B,
> 21.8/25.3 µs p50/p99 at 4 KiB, ~841 MB/s per stream at ring 4096.
>
> **Proposal.** Keep the reference allreduce architecture (doorbell + GPU spin
> kernel + host progress thread) and replace its ~150–200 lines of ibverbs
> with an ~100-line fd pump on the stream device: send is `write`, and the
> **progress thread runs the receive path** (`read` peer bytes into pinned UMA
> buffers, then store the flag the GPU spins on). The wrapper and the ~51-line
> engine communicator hook port nearly verbatim.
>
> **Scope — explicitly NOT a collective-library net plugin.** A plugin sits
> under the library's proxy/protocol stack (which eats the latency win),
> HopID scarcity (~10, shared with `thunderbolt_net`) forces a multiplexer
> over one or two long-lived streams, and communicator teardown on every serve
> restart is exactly the stream open/close pattern that wedges router hop
> tables. Rejected for the latency goal; see §2 of
> `docs/USB4STREAM-TRANSPORT.md`.
>
> **Scope — decode allreduce only.** Bulk/prefill/weights stay on the existing
> transport: at ≤841 MB/s per stream this primitive will never be the bulk
> transport of record.
>
> **Expected result.** ~120–160 µs against the 105 µs verbs bar at batch-1
> decode payloads (inferred: +2 syscalls, +2 kernel copies, +progress-thread
> wakeup on RX), degrading at speculative verify widths.
>
> **Effort.** a small port to first light; hardening is the real cost.
>
> **Preconditions (do not start until all three hold).** (1) banked exchange
> p50 at 8–16 KiB ≤ ~40 µs with a tight p99; (2) the bench matrix shows TP=2
> decode is allreduce-dominated; (3) the attended RDMA A/B fails or wins by a
> margin that does not justify its rebuild treadmill.
>
> **Hard safety constraints for any code that touches the device.** One open
> attempt per side per process, blocking, under an alarm; never reopen on
> failure; never write configfs (`ring_size` and `throttling` are read-only
> here); resolve the device node per side through the configfs `index` — no
> numbered device literal, ever, because numbering is asymmetric across the
> twins and the coordinator's low indices are peerless; and never run against
> a live serve on the shared cable. `bench/usb4stream-bench.py` is the
> reference implementation of all five.
