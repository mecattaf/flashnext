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
