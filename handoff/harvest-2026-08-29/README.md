# Harvest: flashnext campaign 01a04c1f-4605-7823-9eac-475e99b842ae (2026-08-29)

Byte-exact copies of the durable tally state behind the 2026-08-29 day run — the
evidence base for tally.nix issues #620–#628 — taken from `~/.local/state/tally`
before the coordinator reflash destroyed the originals. Lock files omitted;
nothing else was filtered. The live campaign state was not mutated by this copy.

Contents:

- `campaigns/armed/3fbba…json` — the armed registration (armSerial 9, pin 6f1ce03,
  approved graph `sha256:004e5218…`).
- `campaigns/attempt-receipts/flashnext/` — `attempt-receipts-v1.jsonl` (30 rows,
  arms 2→7: 12× FlowAdmissionDenied with empty details (#624), 8× result-projection-timeout
  (#625), the 11:01:30Z escalation whose body carries the cp-weights amendment diff)
  plus `receipt-authority-v1.json`.
- `campaigns/steering/01a04c1f-…/` — `steering-v1.jsonl` (10 operator steers) and the
  dispatch cursor (highWater 10).
- `campaigns/host-tuning/…host-v1.json` — projectionWaitMs 10000 (the OE-1 default).
- `campaigns/lease/3fbba620782ccc333fdb57f1/` — the campaign lease as wedged
  (held, renewing, orphan `diagnose-cp-build` job 01a04d7f-3f2f-7e30-8482-6b89358e7c5a).
- `capture/` — every `01a04c*`/`01a04d*` capture of the day, including the terminal
  pass `01a04d7f-22e4-7693-bc57-3219f0ceecd4.out` (checkpoint-cp-build banned-token
  failure, OE-3) and the cp-weights/cp-build checkpoint captures cited by #625.
- `unit-exit/` — matching unit-exit records, including the SIGTERM'd terminal flow run.

Index of what each artifact proves: `handoff/TALLY-FINDINGS.md` and
`handoff/DAYRUN-NOTES.md`. Stop-state ruling: `docs/MORNING.md`.
