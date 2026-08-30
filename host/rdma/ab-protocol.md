# RDMA adoption gate -- counterbalanced socket-vs-verbs A/B

RDMA does not get adopted because it built and passed the verify gate in
`attended-bringup.md`. It gets adopted only if it wins a counterbalanced
comparison against the socket transport, with byte-identical outputs, run
after the verify gate passes. Until this protocol produces that result,
sockets on both rails remain the transport of record on both ranks
(spec.md P7). This file is that protocol, and where its result gets recorded.

This A/B is downstream of `attended-bringup.md` Gate 0: it presupposes a
committed TP=2-over-TCP benchmark already exists under `results/` (the
comparison needs a baseline to compare against regardless), and it only runs
after that document's steps 1-10 have gotten both nodes to a passing verify
gate.

## Why counterbalanced, and why now specifically

The one existing data point for this pair's transport delta -- wkljohn's A/B
-- gives RDMA **+3.4% decode** over TCP. That number is real, but two things
about this pair's own measured record narrow how much of it to expect here:

1. **A meaningful share of the wider community's published RDMA-vs-TCP
   deltas were measured against TCP running with unheld C-states** -- a
   577us-RTT socket path being compared against a tuned RDMA path, which
   overstates RDMA's advantage because the comparison's TCP arm was crippled,
   not because RDMA itself is that much faster. This pair's own measured
   record found exactly that defect locally (577us RTT with C3 idle enabled,
   63-90us with `cpu_dma_latency=0` held) before it was fixed.
2. **This pair's TCP arm, in this A/B, is the held one.** `attended-bringup.md`
   step 2 item 3 makes re-confirming the C-state/MTU hold post-reboot a hard
   precondition specifically so this comparison is not the crippled-TCP
   version of itself. Our TCP is held at **~77us on both ends** (the measured
   range is 63-90us average RTT depending on direction; ~77us is the figure
   to plan around), not 577us. Expect the **honest** delta against that held
   baseline -- which may be smaller than +3.4%, and
   per this pair's own report, might be close to zero: the same record notes
   `strix-rdma`'s own author reports the NHI path "effectively identical to
   TCP v3" and ships TCP in production on his own rig, and that on this
   specific pair the 5GbE control wire already beats the 40Gb/s Thunderbolt
   rail on average latency. Go in expecting a real but small number, not a
   repeat of +3.4%.

## XDomain wedge discipline -- hard rules before any verbs traffic

Adopted 2026-08-29 from the strix-rdma recon (post-reboot chapter record,
`~/post-reboot-latest-2.md` §4): RDMA DMA TX toward a peer whose RX ring is
not open does not error out -- it stalls on zero end-to-end credits and can
wedge the entire XDomain, **including TCP on the same cable**. Recovery is
reboot-only. Therefore, whenever the verbs arm is live:

1. **Out-of-band TCP barrier before first RDMA transmit.** Both sides
   confirm, over the ethernet wire (`enp191s0`, never the rail itself), that
   the peer's RX ring is open before either side sends its first RDMA
   packet. No barrier, no transmit.
2. **Never take both sides' rings down simultaneously.** One side stays up
   until the other is confirmed down and quiescent.
3. **Worker-first teardown.** The worker's rings come down first; the
   coordinator follows only after confirming the worker is quiesced -- the
   mirror image of the worker-first reboot rule in `attended-bringup.md`.

**Exact-name hazard (verified in-source, `kernel/ibdev.c:2277-2290`):** both
ibverbs devices advertise rail 0's GID -- the driver registers one global
`roce_netdev` for every rail, so `usb4_rdma5`'s GID table looks identical to
`usb4_rdma0`'s. `NCCL_IB_HCA` must therefore be the **exact string**
`usb4_rdma0` -- a prefix match (`usb4_rdma`) or selecting `usb4_rdma5`
silently routes RDMA onto the wrong wire with no error. Both devices existing
on both nodes is by design (fixed-stride naming, one lane per TB domain);
their presence is not the hazard -- ambiguous selection is.

## Protocol

- **Depths:** reuse the main bench's depth series -- `0`, `10240`, `102400`
  -- rather than inventing a separate one. A transport delta that only shows
  up at one depth and not the others is itself a finding, not noise to
  average away.
- **Loads per arm:** 3.
- **Order:** counterbalanced, A-B-B-A per depth (A = socket, B = verbs) --
  not A-A-A-B-B-B. Thermal drift, cache warmth, and background load on a
  shared pair all trend across a session; A-B-B-A cancels a linear trend in
  a way a blocked order does not.
- **What's held constant across both arms:** everything except the transport
  env delta from `attended-bringup.md` step 11. Same weights, same context
  depth, same speculative-decode setting, same batch shape, same node roles.
  Swap only `NCCL_IB_DISABLE` / `NCCL_IB_HCA` / `NCCL_IB_GID_INDEX` between
  arms; nothing else in the shared cluster env changes.
- **Correctness check, every run:** capture a token fingerprint per load, both
  arms. A transport swap that changes output tokens is not a performance
  question anymore -- treat any fingerprint mismatch as a hard stop, file it,
  and do not report a performance number until it's resolved. RDMA winning on
  latency with a fingerprint mismatch is not a win.
- **Metric:** median decode tokens/s per arm per depth (3 loads -> take the
  median, not the mean, to blunt one bad load).

## Adoption rule

Adopt RDMA as the transport of record **only if**:
1. Verbs beats sockets on decode at a majority of the three depths, and
2. every fingerprint matches across both arms at every depth.

Otherwise, sockets stay the transport of record on both rails, exactly as
before this session -- a small or negative delta is a valid, useful result,
not a failed bring-up. The RDMA package stays staged and rebuildable for the
next attended session either way; nothing about a "no" result here requires
tearing anything down beyond the normal rollback in `attended-bringup.md`.

## Recording the result

Land the three-depth, two-arm result under `results/`, in the same receipt
shape the rest of the campaign's bench matrix uses (medians, fingerprints,
depth series -- see spec.md's bench-matrix ruling). Whichever way the
adoption rule falls, write the receipt: a documented "RDMA measured, not
adopted, delta was +N%" is exactly the kind of result the morning ledger is
for, and saves the next person from re-running this A/B from scratch.

---

## Appendix -- odinlink fold, 2026-08-30 (repo issue 6)

Dated appendix folding the OdinLink estate's evidence (`wkljohn/OdinLink-Five`
and its consumer `ds4-strix-halo-tp-odinlink`, plus the `strix-rdma` recon)
into this protocol. It changes no rule above; it prices the expectation and
hardens the failure path.

### What the numbers say before we measure anything

| figure | value |
|---|---|
| one-way latency, USB4 verbs on this hardware class | ~11 µs (versus ~1.4 µs for CX7 on a Spark) |
| round trip, same stack | ~22 µs, against ~286 µs on that rig's TCP |
| end-to-end decode delta, same rig and settings | **+3.4%** (8.29 -> 8.57 t/s) |

A ~13× improvement on the wire that yields +3.4% end to end is the single
most useful calibration in this file. The op assembly around the transport
-- staging copies, doorbell, progress-thread wakeup, GPU poll -- is what the
decode allreduce actually spends its time on, and it does not shrink when
the wire does. `strix-rdma`'s author reaches the same place independently:
zero-copy NHI DMA measured "effectively identical" to his own TCP v3, and he
ships TCP. Design the A/B to detect a small effect honestly rather than to
confirm a large one; that is what the counterbalancing and the three-load
medians above are for.

**Bandwidth is not the lever either.** A 40 Gb/s USB4v1 link does about
**20 Gb/s host-to-host p2p** (the 80 Gb/s number needs native TB5 silicon
this platform does not have), and the measured OdinLink figures are
**8.38 Gb/s unidirectional against 9.84 Gb/s full-duplex** -- the two
directions contend inside the same NHI, so full duplex is ~1.17×, not 2×.
Per-cable ceilings are asymmetric in exactly the way that punishes anyone
budgeting from the cable's label. Decode is latency-bound; expect any win to
show up there or nowhere.

### Verbs init failure is terminal -- the arm ends, the session does not

If the verbs arm fails to initialize at any point during this A/B:

1. **No retry of the verbs arm.** A failed init may already have posted
   descriptors on one side; a retry is how a half-open pair becomes a wedged
   cable.
2. **Restore the socket env byte-identical on both ranks** -- diff it, don't
   eyeball it. A one-sided restore silently measures nothing.
3. **One restart of the pair service, exactly one.** If sockets do not come
   back on it, go to `attended-bringup.md` step 12 rollback.
4. **The terminal fallback rung is always the ethernet wire** (`enp191s0`),
   **never rail 1** and never verbs anywhere. Record the arm as failed, keep
   the socket numbers you already have, and write the receipt -- a documented
   "verbs did not initialize" is a result, not a lost session.

This is the same reasoning as the wedge discipline above, in its operational
form: an RDMA transmit toward a closed peer receive ring stalls on zero
end-to-end credits and takes down the whole XDomain **including TCP on the
same cable**, recovery **reboot-only**. Because the socket rung shares that
cable, it cannot serve as the verbs rung's fallback -- which is precisely why
no verbs rung appears in the unattended ladder in `host/fn-env.sh`, and why
this comparison only ever runs with an operator present.
