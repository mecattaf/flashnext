# Round-2 arm checklist (2026-08-30 evening)

Audience: the operator for steps 1–3, then the fresh steering session for
steps 4–6. Everything repo-side is already committed and pushed; all five
campaign gates were verified locally green at push time (unittests,
spec-lint, flake check, fork-verify, receipts-verify). The decisions behind
this shape live in the README's "Round 2" section; the worklist is
`silent-factory-worklists/flashnext.json` (18 tasks, append-only revision).

## 1. Reboot the coordinator (operator, attended, ~10–15 min)

Power-button protocol if the shutdown hangs (this box is on 7.2.0; the
dc_lock deadlock fix arrives with the 7.2.2 kernel this reboot boots into).
The reboot must precede weight staging and arming.

## 2. Post-reboot verification (operator or fresh session, 5–10 min)

BRANCHED — the measured dark end is partly the WORKER side (its
thunderbolt0 read NO-CARRIER on an already-rebooted 7.2.2 node), so the
coordinator reboot alone may not heal rail 0:

- `uname -r` → 7.2.2; `systemctl is-active lowlat-cluster` active on both
  nodes; `/dev/cpu_dma_latency` reads 0; `/dev/tbstream*` re-provisioned.
  Expect the fn-rdma units FAILED — loud but harmless (nothing is staged
  for 7.2.2; that is the attended morning item).
- Rail check BOTH directions: `ping -c3 10.99.0.2` AND
  `ssh worker ping -c3 10.99.0.1`;
  `ssh worker cat /sys/class/net/thunderbolt0/carrier` must read 1.
- If rail 0 is dark: (a) replug cable A at the worker end, re-check;
  (b) still dark → reboot the WORKER (attended, allowed pre-arm),
  re-check; (c) STILL dark → accept the wire night: fn-env's ladder falls
  back to enp191s0 (`FN_TRANSPORT_RUNG=wire-fallback`, ~87 µs RTT —
  degraded but valid receipts). Note for the morning: a wire-fallback
  bench.json does NOT satisfy the rail-sockets Gate 0 for verbs bring-up.

## 3. Stop the model-swap service on BOTH nodes (operator, 2 min)

`sudo systemctl stop llama-swap.service` and
`ssh worker sudo systemctl stop llama-swap.service`.
Stopping is safe; a START is not — its start triggers the local-models
sync, which is what pruned the staged weights on 08-29. Do not start it
again until the morning catalog patch is applied. (Side effect: the local
journal-distill utility is offline overnight — fine.)

## 4. Pin the agent model (fresh session, 2 min)

`systemctl --user set-environment ANTHROPIC_MODEL=<pinned model id per
handoff/DAYRUN-NOTES.md item 7 / tally#624>` then
`systemctl --user restart tally-daemon`. Verify with
`systemctl --user show-environment | grep ANTHROPIC`.
Never via `agent.model` (tally#624).

## 5. Arm (fresh session, 3 min)

The repo is already pushed. A push can enqueue a reconcile pass despite the
paused pool (observed once, 08-29) — so FIRST `tally campaign status
mecattaf/flashnext silent-factory-worklists/flashnext.json` and sweep any
stale queued pass, THEN:

```
cd /home/tom/mecattaf/flashnext && git pull && \
tally campaign arm mecattaf/flashnext silent-factory-worklists/flashnext.json --wait
```

Same registration 01a04c1f-4605, NO disarm. Verify the arm verdict shows
FIVE completions preserved (container-recipe, instruments, host-tooling,
bench-harness, AND cp-build — the worklist is append-only precisely so
cp-build's banked 14400 s checkpoint survives) and cp-weights re-queued
(its title revision forces the re-stage). Expect the doc lanes
(proxy-tooling, morning-ledger, catalog-handoff, rdma-package) to schedule
before cp-weights — deliberate: the morning package banks before any
checkpoint can fail. If the scheduler deviates, observe, don't intervene.

## 6. Overnight red lines (fresh session enforces)

No reboots, no nixos-rebuild/deploys, no GC, no tally pin bumps, no
model-swap service starts. No MANUAL access to /dev/tbstream* or the
thunderbolt configfs — the only automated actor permitted there is
cp-usb4stream, which is single-open, dead-last, and exits without touching
any device unless the pair serve is already down. If the night runs late,
`rocm10-probe` is the first task to drop, `cp-usb4stream` the second.

## Morning expectations

Doc lanes done ~02:00–03:00; cp-weights ~03:00–06:00 (the library source
measures 86–87 MB/s sequential → ~75–80 min/node of pure transfer, serial
per node — a slow copy is bandwidth, not a hang; see flashnext#2 before
killing anything); cp-build skipped (banked); smoke/proxy; cp-tp2 possibly
08:00–10:00; cp-bench potentially into the afternoon (21600 s budget).
That trade is deliberate: no checkpoint failure can cost the morning
package. The serve at wake runs whatever arm the schedule reached.

Morning order: docs/MORNING.md → anything under results/receipts/failed/
(typed blockers, possibly a deferred context probe) → apply
handoff/catalog-row.patch (permanently ends the prune hazard) → only then
any rebuild/model-swap restart → read results/rocm10-probe.json and, if
green, the five-minute ROCm 10 promotion (README ROCm 10 section) →
attended RDMA fetch-and-build on 7.2.2 (both nodes, worker first) + A/B
per host/rdma/ab-protocol.md, ONLY if bench.json's transport rung is
rail0-sockets → tear the pair down, run the stream bench attended, apply
docs/USB4STREAM-TRANSPORT.md's decision rule.
