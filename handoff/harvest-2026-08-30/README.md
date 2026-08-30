# Harvest: flashnext campaign registration 01a050a1-77a7-7483-8f11-139f1d7805ff (2026-08-30)

Byte-exact copies of the durable tally state behind the 2026-08-30 overnight run —
the run that reached arm serial 10 and ended on a network blip, not on capacity.
Taken from `~/.local/state/tally` on 2026-08-31 **before arming run 3**, because
run 3 writes into the same store and `handoff/RUN3-BRIEF.md` §3 records that no
harvest of run 2 existed. Lock files omitted; nothing else was filtered.
The live campaign state was not mutated by this copy.

Companion to `handoff/harvest-2026-08-29/`, which covers run 1
(campaign `01a04c1f-4605-7823-9eac-475e99b842ae`).

Contents:

- `campaigns/armed/3fbba…json` — the armed registration as wedged: armSerial 10,
  lease acquired 2026-08-29T06:05Z (during run 1), still armed at harvest time.
- `campaigns/attempt-receipts/flashnext/attempt-receipts-v1.jsonl` — **61 rows**:
  27 diagnosis, 23 retry, 6 escalation, 5 pardon. Per-task lifetime attempt counts
  at harvest: **cp-weights 13, proxy-tooling 10**, cp-smoke 6, morning-ledger 5,
  catalog-handoff 3, rdma-package 3, cp-build 3, instruments 3, container-recipe 2,
  cp-proxy 1, cp-tp2 1. The two at/over `MAX_TASK_LIFETIME_ATTEMPTS = 10` are the
  escalation contributors that wedge `campaign poll` — see RUN3-BRIEF §1.1 and
  tally.nix#642. Escalations at seq 48/50/53/57/61 all carry
  `{cp-weights, proxy-tooling}`; the only pardon scoped to that exact set is seq 44,
  which precedes all of them.
- `campaigns/steering/…` — operator steering journals and dispatch cursors.
- `campaigns/host-tuning/01a050a1-…host-v1.json` — the run's host tuning record.
- `campaigns/lease/…`, `campaigns/releases/…`, and the sibling campaigns' pass
  state (`epsilon`, `epsilon-extension`, `eta`, `theta`) — swept in whole rather
  than filtered, so the store's cross-campaign shape is preserved.
- `capture/` — 2,103 captures with the `01a05*` id prefix (run-2 flow passes).
- `unit-exit/` — 3,096 matching unit-exit records.

Index of what each artifact proves: `handoff/RUN3-BRIEF.md` §1–§3.
Run-1 comparison and the issue evidence base: `handoff/TALLY-FINDINGS.md`.
