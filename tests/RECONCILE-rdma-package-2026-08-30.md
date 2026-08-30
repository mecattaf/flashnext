# Reconcile note — task `rdma-package`, 2026-08-30

Stateless reconcile attempt for revision `f37888e6`. Recorded per the overseer
standing note (tally#622 family): every deliverable of the re-scoped goal was
already present and correct in the lane, so this note is the lane's non-empty
commit. It changes no script, alters no pin, and rewrites none of the package
prose the goal explicitly told this attempt not to rewrite wholesale.

## HEAD at verification

    0871914  catalog-handoff: Prepare the fleet catalog patch for the morning operator

The working tree was clean at entry (`git status --porcelain` printed nothing).
The three re-scoped deliverables all arrived in one lane commit, confirmed an
ancestor of this HEAD by `git merge-base --is-ancestor`:

| path | last touched by |
| --- | --- |
| `tests/test_rdma_package.py` | `0a89657` rdma-package |
| `host/rdma/ab-protocol.md` | `0a89657` rdma-package |
| `host/rdma/attended-bringup.md` | `0a89657` rdma-package |
| `host/rdma/fetch-and-build.sh` | `e2bc2fc` rdma: TARGET_KVER 7.2.0 → 7.2.2 |

One earlier lane commit (`a7e523f`, "the pin test, plus the odinlink and
kernel-truth folds") is *not* an ancestor of HEAD — its content was carried
forward by `0a89657`, which is. Nothing in the lane needed repair.

## Acceptance evidence

Both criteria run verbatim, exit 0.

`rdma-package-sound`:

    bash -n host/rdma/fetch-and-build.sh \
      && grep -q '503c5ae1e72aa9ed91925dafa3d82ee2e992747f' host/rdma/fetch-and-build.sh \
      && grep -q '76ba39b630a70accb72f19388eefe48844b50eb8' host/rdma/fetch-and-build.sh \
      && ! grep -qE '^[[:space:]]*(sudo[[:space:]]+)?(insmod|modprobe)[[:space:]]' host/rdma/fetch-and-build.sh \
      && grep -qi 'physically present' host/rdma/attended-bringup.md \
      && grep -qi 'worker first' host/rdma/attended-bringup.md \
      && grep -qi 'deploy' host/rdma/attended-bringup.md \
      && test -s host/rdma/ab-protocol.md

`rdma-pin-test`:

    python3 -m unittest tests.test_rdma_package -v \
      && grep -qi 'odinlink' host/rdma/ab-protocol.md \
      && grep -q '7.2.2' host/rdma/attended-bringup.md \
      && grep -qi 'wire-fallback' host/rdma/attended-bringup.md

- `python3 -m unittest tests.test_rdma_package -v` — **Ran 13 tests, OK**.

Beyond the criteria, re-checked at the same HEAD: the repo suite with all eight
modules named explicitly — **Ran 116 tests, OK**. (`unittest discover` does not
work against `tests/` here; the directory carries no `__init__.py` and is
imported as a namespace package, so modules are named explicitly, as the
sibling lanes do.)

## Goal conformance, re-checked

**Deliverable 1 — the pin test the original goal promised.**
`tests/test_rdma_package.py` exists and is the test, not a placeholder. Both
pins are asserted present in `fetch-and-build.sh` by full 40-hex value
(`test_both_module_pins_are_present`), and a second test refuses a branch or
tag pin — the drift mode recorded in `ds4-vllm-manifest.md` §0, correction 7.
`test_no_unattended_module_load_path` encodes spec F.1 with a line-anchored
regex matching the acceptance argv in intent, `^[ \t]*(sudo[ \t]+)?(insmod|modprobe)[ \t]`,
so in-prose mentions of `insmod` stay legal — the checklist has to be able to
name the command the operator types — while a real load path at line start
fails. `test_script_is_syntax_clean` runs `bash -n`; the attended language
tests assert `physically present` and `worker first`.

**Deliverable 2 — the odinlink fold, in both docs, as dated appendix sections.**
`attended-bringup.md:486` "Appendix A -- odinlink fold, 2026-08-30 (repo issue 6)"
and `ab-protocol.md:117` "Appendix -- odinlink fold, 2026-08-30 (repo issue 6)".
All four named items landed:

- *Terminal-failure framing for verbs init* — `ab-protocol.md:151` and
  `attended-bringup.md:493` (A.1): no retry of the verbs arm, restore the
  socket env **byte-identical on both ranks** (diff it, don't eyeball it),
  **exactly one** restart of the pair service, and the terminal fallback rung
  is **always the ethernet wire** (`enp191s0`), never rail 1, never verbs.
- *The 11 µs one-way rail reference figure* — the A.2 figures table,
  `attended-bringup.md:522`, ~11 µs one-way against ~1.4 µs for CX7 on a
  Spark, with the ~22 µs / ~286 µs round-trip pair beside it.
- *The per-cable p2p ceiling asymmetry* — A.3, `attended-bringup.md:533`:
  ~20 Gb/s host-to-host p2p on a 40 Gb/s USB4v1 link, measured 8.38 Gb/s
  unidirectional versus 9.84 Gb/s full duplex (~1.17×, **not** 2×, because
  both directions contend inside the same NHI), and 3 DMA rings per
  controller meaning exactly one RDMA lane per cable — a second, independent
  corroboration of step 10's single-rail contract.
- *The wedge hazard* — A.4 and the protocol's wedge-discipline section: a
  verbs transmit toward a peer whose receive ring is not open **does not
  error**, it stalls on zero end-to-end credits and wedges the entire XDomain
  on that cable **including plain TCP on the same cable**; recovery is
  **reboot-only**. Both docs draw the operative conclusion — because the
  socket rung shares that cable it cannot back up the verbs rung, which is why
  no verbs rung ever appears in the unattended ladder in `host/fn-env.sh`.

**Deliverable 3 — the dated "state of tonight" preamble.**
`attended-bringup.md:24`, "State of tonight -- 2026-08-30, read this before
Gate 0", carrying every fact the goal names: no ibverbs device on either node
right now (`/sys/class/infiniband` empty on both, measured); round 1's devices
were baked on **7.1.4** and the staged module sets cover only **7.1.4 and
7.2.0** while the fleet runs **7.2.2**; therefore the attended morning
`fetch-and-build.sh` runs on **BOTH** nodes first, hard-gated on the *running*
kernel being 7.2.2 via `TARGET_KVER`, worker first, before any A/B; and the
honest expectation of **+3.4% decode over held TCP** from the one measured
community precedent (wkljohn's same-rig A/B, 8.29 → 8.57 t/s), adopted only on
a fingerprint-clean, counterbalanced, majority-of-depths win, with a measured
"no" written up the same as a "yes".

The Gate 0 transport-rung caveat is item 3 of that preamble: the banked
socket-transport bench carries a transport record, read via
`jq -r '.data.transport.fn_transport_rung' results/receipts/bench.json`.
`rail0-sockets` satisfies Gate 0; **`wire-fallback` does not** — that is the
5 GbE control wire (`enp191s0`), a valid but degraded receipt, and comparing
verbs-on-rail-0 against it measures two different cables rather than two
transports. On a wire-fallback night the attended morning's **first** transport
act is healing rail 0 and **re-banking a rail-sockets bench**; verbs work
starts after that receipt exists, not before.

**The hard rules survive.** Nothing GPL is vendored; the script stages and
builds and never loads (asserted, not assumed); the single-rail contract holds
at step 10, guarded by `test_single_rail_contract_survives` on collapsed
whitespace so a reflow cannot silently drop it; and Gate 0 still requires a
committed socket-transport benchmark under `results/`.

## Boundaries

Only `host/rdma/` and `tests/` were read for repair, and only `tests/` was
written — this note. No path outside the task's conflict domains was touched.
