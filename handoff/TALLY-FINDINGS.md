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
- doc drift (minor, watch): ROUTING.md's escalation ladder names
  `tally campaign resume <master-issue-url>` — no `resume` verb exists under
  `tally campaign` on the deployed pin (6f1ce03); recovery verbs are
  `poll --once` (readmit changed authority) and the `tally queue` family.
  Will file on mecattaf/tally.nix only if a real pardon-the-counters need
  arises and no verb covers it.
