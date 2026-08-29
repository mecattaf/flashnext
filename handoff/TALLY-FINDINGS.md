# Tally findings index — flashnext campaign (day run, 2026-08-29)

One line per friction point with the harness, per OVERSEER.md. Product-side
defects found *through* tally are noted here too when they shaped the run.

- 08:05 — worklist defect (product, not tally): spec-lint gate argv ran
  `cargo run -p spec-lint` with cwd in the flashnext checkout; cargo exits 101
  ("could not find Cargo.toml"), not the absorbed lint-exit 1. Failed the
  arm-1 preflight witness. Fixed forward on main (`42ae5cd`,
  `--manifest-path /home/tom/mecattaf/tally.nix/Cargo.toml`), readmitted as
  arm 2 via `tally campaign poll --once`. No tally issue: the harness graded
  correctly.
- 08:09 — W-316 corroboration (known, documented in campaign-operator skill;
  no new issue): `campaign status` rendered "queued, awaiting reconciliation /
  no pass has reconciled yet" while `poll --once` reported "1 node(s) live
  under this lease" and the container-recipe pi process was verifiably
  running. Corroborate with `pgrep -af pi` / capture files before concluding
  nothing ran.
- 09:15 — **tally.nix#620 filed**: `campaign poll --once` reports
  "readmitted" with fresh digests when origin/main moves, but the executing
  graph keeps arm-time gate argv and base rev — two lane attempts burned on
  the pre-fix spec-lint gate. Recovery that worked: full re-arm (armSerial 3,
  new payloadHash, same registration → steers preserved) + `queue cancel` of
  the stale arm-2 flow pass. Secondary in the same issue: cancel's
  `ok:true, affected:0, was:running` receipt is ambiguous.
- doc drift (minor, watch): ROUTING.md's escalation ladder names
  `tally campaign resume <master-issue-url>` — no `resume` verb exists under
  `tally campaign` on the deployed pin (6f1ce03); recovery verbs are
  `poll --once` (readmit changed authority) and the `tally queue` family.
  Will file on mecattaf/tally.nix only if a real pardon-the-counters need
  arises and no verb covers it.
- 13:0x — **tally.nix#622 filed**: two qwen lanes (container-recipe,
  proxy-tooling) independently exited without committing and burned an
  attempt each at ownership validation. Brief needs a closing
  definition-of-done block, and/or the driver should salvage a dirty
  worktree whose diff passes acceptance instead of discarding it.
  Workaround: overseer commit-discipline steers (seq 4, 7).
- 14:30 — operator-called stop. Full issue set filed: #620 (poll
  authority + cancel no-op), #621 (stale-pass promotion), #622 (uncommitted
  ownership pattern), #623 (status render desync), #624 (model-override
  admission denial), #625 (checkpoint stderr masking), #626 (inbox
  answer/pardon verbs), #627 (usage aggregation), #628 (steer/retry race +
  resume-verb docs drift). Stop-state and second-flow inputs:
  docs/MORNING.md. pi-era usage for the record: 311 calls, ~1.02M fresh
  input, 24.3M cache reads, 347K output — exhausted the qwen 1-week plan.
