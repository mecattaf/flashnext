# USB4STREAM as a tensor transport — the decision memo

**Status:** decided, twice, on 2026-08-30. This file exists so the morning
reads *one* memo and does not re-deliberate. The deliberation record is
`docs/DECISIONS-2026-08-30.md` §5; the numbers this memo waits on are banked
by `bench/usb4stream-bench.py` into `results/receipts/usb4stream.json`.

**One-line answer:** the collective-library net-plugin route is **rejected for
the latency goal**; the real path is a **2–4 attended-day port of the
reference doorbell + progress-thread allreduce** from verbs onto the stream
device's `read`/`write`, and it is built **only if the trigger criteria in §4
hold**.

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
1–2) a competent author gets a first working allreduce in **3–5 attended days**
— to a *fragile prototype*. The mux layer, the 8-outstanding-request semantics
under the proxy threads, collective hang debugging, p99 validation and
wedge-safe lifecycle across restarts put "trustworthy enough to leave serving
overnight" at **2–4 attended weeks**.

Two alternatives were dismissed with reasons, so they stay dismissed: a custom
`torch.distributed` ProcessGroup backend (~2–3 attended weeks, far more
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
  verbatim** — this is the reason the cost is days and not weeks.

Cost versus RDMA at decode payloads: +2 syscalls, +2 kernel copies (sub-µs at
these sizes), +progress-thread wakeup on RX. **Inferred landing zone: ~120–160
µs against the 105 µs RDMA bar** at batch-1 decode payloads, degrading at
speculative verify widths where the ~841 MB/s wire term bites.

**2–4 attended days to first light; ~1 attended week to trusted.** For
bulk/prefill/weights the primitive is *not* the answer and never will be — it
loses on bandwidth to both patched RDMA and plausibly to plain
TCP-over-`thunderbolt0`. The claim is scoped to **the decode allreduce, the op
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
TCP-over-`thunderbolt0` now that the firewall is open, to close the "does the
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
down `thunderbolt0`, the rail TP=2 depends on. A wedge on cable B costs a
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
> **Effort.** 2–4 attended days to first light; ~1 attended week to trusted.
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
