# Reconcile note — task `instruments`, 2026-08-30

Stateless reconcile attempt for revision `16ab40e1`. Recorded per the overseer
standing note (tally#622 family): the deliverable was already present in the
lane, so this note is the lane's non-empty commit. It changes no code.

## HEAD at verification

    43eb34b  instruments: reconcile — the adapted instrument overlay is already in the lane

The three overlay modules and their test arrived via **ancestor `4b1d714`**
("instruments: Engagement-proof instruments adapted into the container
overlay"), confirmed an ancestor of this HEAD. All four blobs are
byte-identical to their landing commit — verified by object id, not by
re-reading:

| path | vs `4b1d714` |
| --- | --- |
| `container/rootfs/fn_synctrace.py` | identical |
| `container/rootfs/fn_offload_batch.py` | identical |
| `container/rootfs/fn_expert_union.py` | identical |
| `tests/test_instruments.py` | identical |

The prior reconcile commit `43eb34b` staged **zero change** against the
witnessed base `ff1731c`, which is the condition the spec-build driver rejects.
That is the defect this note corrects.

## Acceptance evidence

`instruments-compile-with-notices`, run verbatim, exit 0:

- `python3 -m py_compile` — clean on all three modules.
- `grep -l 'Adapted from' container/rootfs/fn_*.py | wc -l` — `3`.
- `python3 -m unittest tests.test_instruments -v` — **Ran 11 tests, OK**.

Repo suite, all five modules together — **Ran 50 tests, OK**. Note that
`unittest discover` cannot be pointed at `tests/` (namespace package, not
importable as a start directory); the modules are named explicitly.

## Goal conformance, re-checked

Env surface is fully renamed to the `FN_` prefix, with no `DS4_` residue:
`FN_PROFILE`, `FN_OFFLOAD_STORE_BATCH_FRAC`, `FN_OFFLOAD_PROMOTE_FRAC`,
`FN_EXPERT_UNION`, `FN_EU_START`, `FN_EU_CALLS`, `FN_EU_OUT`.

- **`fn_synctrace.py`** — wraps the tensor methods that force a blocking
  device-to-host sync, driven from the `FN_PROFILE` window rather than
  upstream's `ds4_tl_indexer` (ds4-vllm-manifest.md §3.1).
- **`fn_offload_batch.py`** — `resolve_store_batch_tokens` and
  `resolve_promote_block_budget`, torch-free, with both load-bearing
  invariants intact: the store floor at `max(offloaded_block_sizes)` and
  promotion counted in blocks, not tokens (§3.3).
- **`fn_expert_union.py`** — re-targeted as the goal requires, onto
  `fused_topk` in
  `vllm/model_executor/layers/fused_moe/router/fused_topk_router.py`, the
  choke point this workload actually routes through, instead of upstream's
  `gpt_oss_triton_kernels_moe.make_routing_data`, which is never on this
  model's path (qsa-53896.md §4). Kill switch defaults off.

Each module carries an adaptation notice header naming the upstream file
(`AlexKGwyn/ds4-vllm @ a8f620d`) and its Apache-2.0 license.

## Boundary

Only `container/rootfs/` and `tests/` were in scope; this commit touches one
new file under `tests/` and nothing else.

---

# Rev 2 — lineage carrier for `ae10e94`, 2026-08-30

Second stateless reconcile attempt for revision `a06d39ee`. The lane was re-run
on a newer base; the rev-1 note above is itself already committed, so this
section is the non-empty change this attempt lands (again per the tally#622
standing note: a zero-diff squash is rejected, and `--allow-empty` does not
help).

## HEAD at verification

    6e81834  proxy-tooling: Proxy checkpoint builder and single-node first-light runner (rev 2: latch cleared)

`4b1d714` remains an ancestor of this HEAD, and all four deliverable blobs are
still byte-identical to that landing commit — compared by git object id:

| path | vs `4b1d714` |
| --- | --- |
| `container/rootfs/fn_synctrace.py` | identical |
| `container/rootfs/fn_offload_batch.py` | identical |
| `container/rootfs/fn_expert_union.py` | identical |
| `tests/test_instruments.py` | identical |

Nothing in the overlay needed rewriting, so nothing was rewritten.

## Lineage note

The rev-2 title names `ae10e94` ("smoke/proxy: podman run -i so heredocs reach
python3 -"), which touches `scripts/make-proxy.sh` and `scripts/run-smoke.sh`.
Both paths sit outside this task's conflict domains (`container/rootfs`,
`tests`), and `ae10e94` is not an ancestor of this lane's base `6e81834`. This
lane therefore carries that lineage forward without reproducing the change:
picking it up here would write outside the stated boundary.

## Acceptance evidence

`instruments-compile-with-notices`, run verbatim, exit 0:

- `python3 -m py_compile` — clean on all three modules.
- `grep -l 'Adapted from' container/rootfs/fn_*.py | wc -l` — `3`.
- `python3 -m unittest tests.test_instruments -v` — **Ran 11 tests, OK**.

Repo suite, all eight test modules named explicitly — **Ran 116 tests, OK**.
(The suite has grown since rev 1's 50; `unittest discover` still cannot be
pointed at `tests/`, which is a namespace package rather than an importable
start directory.)

Re-checked alongside acceptance: the `FN_` env surface is intact and complete
(`FN_PROFILE`, `FN_OFFLOAD_STORE_BATCH_FRAC`, `FN_OFFLOAD_PROMOTE_FRAC`,
`FN_EXPERT_UNION`, `FN_EU_START`, `FN_EU_CALLS`, `FN_EU_OUT`), the three
adaptation notices name `AlexKGwyn/ds4-vllm @ a8f620d` and Apache-2.0, and
`fn_expert_union` still wraps `fused_topk` in
`vllm/model_executor/layers/fused_moe/router/fused_topk_router.py`. The only
remaining `DS4_` strings are prose inside the notice headers describing the
rename; no module reads a `DS4_`-prefixed variable.

## Boundary

This attempt touches exactly one tracked file, `tests/RECONCILE-instruments-2026-08-30.md`.

---

# Rev 3 — carrier for the make-proxy fixes, 2026-08-30

Third stateless reconcile attempt, for revision `bc2182b6`. The lane was re-run
on a newer base again. The overlay deliverable and both earlier note sections
are already committed, so this section is the non-empty change this attempt
lands — same reason as before (the spec-build driver rejects a zero-diff
squash, and `--allow-empty` stages nothing either).

## HEAD at verification

    c06bb79  instruments: Engagement-proof instruments adapted into the container overlay (rev 2: lineage carrier for ae10e94)

`4b1d714` is still an ancestor of this HEAD, and all four deliverable blobs
remain byte-identical to that landing commit — compared by git object id, not
by re-reading the files:

| path | vs `4b1d714` |
| --- | --- |
| `container/rootfs/fn_synctrace.py` | identical |
| `container/rootfs/fn_offload_batch.py` | identical |
| `container/rootfs/fn_expert_union.py` | identical |
| `tests/test_instruments.py` | identical |

Nothing in the overlay needed rewriting, so nothing was rewritten.

## Lineage note

Two make-proxy commits bear on this revision, and they sit on opposite sides of
this lane's base:

- **`ae10e94`** ("smoke/proxy: podman run -i so heredocs reach python3 -") —
  the lineage rev 2 carried forward. It is now an **ancestor** of this base, so
  that carry is discharged.
- **`237d508`** ("make-proxy: three first-run fixes against the real workload":
  the `read_header` tuple unwrap, keying the engram table off the first
  shard-bearing layer, and verifying against `_LayerShards.shards`) — present
  on `main`, **not** an ancestor of base `c06bb79`. It is the fix set this
  rev-3 title names.

`237d508` touches `scripts/make-proxy.sh` and nothing else. That path is
outside this task's conflict domains (`container/rootfs`, `tests`), so
reproducing it here would write past the stated boundary. This lane therefore
carries the lineage forward without reproducing the change, exactly as rev 2
did for `ae10e94`.

## Acceptance evidence

`instruments-compile-with-notices`, run verbatim, exit 0:

- `python3 -m py_compile` — clean on all three modules.
- `grep -l 'Adapted from' container/rootfs/fn_*.py | wc -l` — `3`.
- `python3 -m unittest tests.test_instruments -v` — **Ran 11 tests, OK**.

Repo suite, all eight test modules named explicitly — **Ran 116 tests, OK**,
unchanged from rev 2. (`unittest discover` still cannot be pointed at `tests/`:
it is a namespace package, not an importable start directory.) The `__pycache__`
that `py_compile` drops beside the overlay is gitignored and untracked, so the
acceptance run leaves no tracked file modified — `git status --porcelain` is
empty both before and after it.

Re-checked alongside acceptance, against the goal rather than against the
previous note:

- The `FN_` env surface is complete — `FN_PROFILE`,
  `FN_OFFLOAD_STORE_BATCH_FRAC`, `FN_OFFLOAD_PROMOTE_FRAC`, `FN_EXPERT_UNION`,
  `FN_EU_START`, `FN_EU_CALLS`, `FN_EU_OUT` — and no module reads a
  `DS4_`-prefixed variable; the surviving `DS4_` strings are prose inside the
  notice headers describing the rename.
- All three adaptation notices name their upstream file in
  `AlexKGwyn/ds4-vllm @ a8f620d` and Apache-2.0.
- `fn_expert_union` still wraps `fused_topk` in
  `vllm/model_executor/layers/fused_moe/router/fused_topk_router.py` — the
  re-target qsa-53896.md §4 establishes, not upstream's
  `make_routing_data` — and its kill switch still defaults off.
- `fn_offload_batch` keeps both load-bearing invariants: the store budget
  floors at `max(offloaded_block_sizes)`, and promotion is counted in blocks
  rather than tokens.

## Boundary

This attempt touches exactly one tracked file,
`tests/RECONCILE-instruments-2026-08-30.md`.

---

# Rev 4 — carrier for the proxy first-light fixes `8669bad`, 2026-08-30

Fourth stateless reconcile attempt, for revision `152cd533`. The lane was re-run
on a newer base once more. The overlay deliverable and all three earlier note
sections are already committed, so this section is the non-empty change this
attempt lands — same reason as before (the spec-build driver rejects a zero-diff
squash, and `--allow-empty` stages nothing either).

## HEAD at verification

    c9c4fc2  instruments: Engagement-proof instruments adapted into the container overlay (rev 3: carrier for make-proxy fixes)

`4b1d714` is still an ancestor of this HEAD, and all four deliverable blobs
remain byte-identical to that landing commit — compared by git object id, not
by re-reading the files:

| path | vs `4b1d714` |
| --- | --- |
| `container/rootfs/fn_synctrace.py` | identical |
| `container/rootfs/fn_offload_batch.py` | identical |
| `container/rootfs/fn_expert_union.py` | identical |
| `tests/test_instruments.py` | identical |

Nothing in the overlay needed rewriting, so nothing was rewritten.

## Lineage note

`237d508` — the make-proxy fix set rev 3 carried forward without reproducing —
is now an **ancestor** of this base, so that carry is discharged, exactly as
`ae10e94`'s was at rev 3.

The commit this rev-4 title names, **`8669bad`** ("proxy first light: five more
fixes proven by local serve to SERVE-READY"), is present on `main` but **not**
an ancestor of base `c9c4fc2`. It carries the engram weight_scale in-place
stream fix, the model-derived table row count, ple-layer-only table files, the
flash-attn 2.8.3 Triton-AMD build, and the amdsmi `.pth` bridge. Its diff
touches three paths:

| path | inside `container/rootfs` or `tests`? |
| --- | --- |
| `container/Containerfile` | no — `container/`, not `container/rootfs/` |
| `scripts/make-proxy.sh` | no |
| `results/receipts/build.json` | no |

All three sit outside this task's conflict domains, so reproducing any part of
`8669bad` here would write past the stated boundary. This lane carries the
lineage forward without reproducing the change, as rev 2 and rev 3 did for
their respective carriers. Nothing in `8669bad` changes what the three
instrument modules must do: it moves the image build and the checkpoint
builder, not the overlay the instruments live in.

## Acceptance evidence

`instruments-compile-with-notices`, run verbatim, exit 0:

- `python3 -m py_compile` — clean on all three modules.
- `grep -l 'Adapted from' container/rootfs/fn_*.py | wc -l` — `3`.
- `python3 -m unittest tests.test_instruments -v` — **Ran 11 tests, OK**.

Repo suite, all eight test modules named explicitly — **Ran 116 tests, OK**,
unchanged from rev 3. (`unittest discover` still cannot be pointed at `tests/`:
it is a namespace package, not an importable start directory.) The acceptance
command is side-effect-free on tracked paths — `git status --porcelain` printed
nothing both before and after the run; the `__pycache__` `py_compile` drops
beside the overlay is gitignored and untracked.

Re-checked alongside acceptance, against the goal rather than against the
previous note:

- The `FN_` env surface is complete — `FN_PROFILE`,
  `FN_OFFLOAD_STORE_BATCH_FRAC`, `FN_OFFLOAD_PROMOTE_FRAC`, `FN_EXPERT_UNION`,
  `FN_EU_START`, `FN_EU_CALLS`, `FN_EU_OUT` — and no module reads a
  `DS4_`-prefixed variable; the surviving `DS4_` strings are prose inside the
  notice headers describing the rename.
- All three adaptation notices name their upstream file in
  `AlexKGwyn/ds4-vllm @ a8f620d` and Apache-2.0.
- `fn_expert_union` still wraps `fused_topk` in
  `vllm/model_executor/layers/fused_moe/router/fused_topk_router.py` — the
  re-target qsa-53896.md §4 establishes, not upstream's
  `make_routing_data` — and its kill switch still defaults off.
- `fn_offload_batch` keeps both load-bearing invariants: the store budget
  floors at `max(offloaded_block_sizes)`, and promotion is counted in blocks
  rather than tokens.

## Boundary

This attempt touches exactly one tracked file,
`tests/RECONCILE-instruments-2026-08-30.md`.

---

# Rev 5 — carrier for the bench warmup `d446d2b`, 2026-08-30

Fifth stateless reconcile attempt, for revision `99f8dc2d`. The lane was re-run
on a newer base again. The overlay deliverable and all four earlier note
sections are already committed, so this section is the non-empty change this
attempt lands — same reason as before (the spec-build driver rejects a zero-diff
squash, and `--allow-empty` stages nothing either).

## HEAD at verification

    69053ad  instruments: Engagement-proof instruments adapted into the container overlay (rev 4: carrier for proxy first-light fixes 8669bad)

`4b1d714` is still an ancestor of this HEAD, and every deliverable blob remains
byte-identical to that landing commit — compared by git object id, not by
re-reading the files:

| path | vs `4b1d714` |
| --- | --- |
| `container/rootfs/fn_synctrace.py` | identical (`125cd1c0e769`) |
| `container/rootfs/fn_offload_batch.py` | identical (`097d4f478c19`) |
| `container/rootfs/fn_expert_union.py` | identical (`6d9051ee22df`) |
| `container/rootfs/.keep` | identical (`6387ced7ac58`) |
| `tests/test_instruments.py` | identical (`0b0026cd63f6`) |

No attempt in this lane has ever rewritten a module; each rev has added exactly
one section to this note.

## Lineage note

`8669bad`, the commit rev 4's title named, is now an **ancestor** of this base,
so that carry is discharged — exactly as `ae10e94`'s was at rev 3 and rev 3's
make-proxy fixes were at rev 4.

The commit this rev-5 title names, **`d446d2b`** ("bench: per-arm JIT warmup
(ds4-vllm two-request protocol) + MTP intel notes"), is present on `main` but
**not** an ancestor of base `69053ad`. It adds `warmup_arm()` after each arm's
readiness gate — a tiny completion to compile the Triton decode/drafter
kernels, then one max-depth prefill to walk every indexer depth bucket in a
single pass — with a unique nonce prompt so no measured cell can be served from
warmup-seeded cache. Its diff touches two paths:

| path | inside `container/rootfs` or `tests`? |
| --- | --- |
| `bench/run-matrix.sh` | no |
| `handoff/DAYRUN-NOTES.md` | no |

Both sit outside this task's conflict domains, so reproducing any part of
`d446d2b` here would write past the stated boundary. This lane carries the
lineage forward without reproducing the change, as revs 2 through 4 did for
their carriers.

Nothing in `d446d2b` changes what the three instrument modules must do, and one
point is worth stating because the two touch the same measurement: the warmup
is a *bench-harness* concern — it moves when a cell is measured, not what the
overlay records. `fn_synctrace`'s window is still opened by `FN_PROFILE` and
`fn_expert_union`'s by `FN_EXPERT_UNION`/`FN_EU_START`, all default off, so a
warmup request that runs before those windows open contributes no events. The
kill switches are what keep warmup traffic out of the measured counts; no
change is needed here to accommodate the new warmup step.

## Acceptance evidence

`instruments-compile-with-notices`, run verbatim, exit 0:

- `python3 -m py_compile` — clean on all three modules.
- `grep -l 'Adapted from' container/rootfs/fn_*.py | wc -l` — `3`.
- `python3 -m unittest tests.test_instruments -v` — **Ran 11 tests, OK**.

Repo suite, all eight test modules named explicitly — **Ran 116 tests, OK**,
unchanged from rev 4. (`unittest discover` still cannot be pointed at `tests/`:
it is a namespace package, not an importable start directory.) The acceptance
command is side-effect-free on tracked paths — `git status --porcelain` printed
nothing both before and after the run; the `__pycache__` `py_compile` drops
beside the overlay is gitignored and untracked.

Re-checked alongside acceptance, against the goal rather than against the
previous note:

- The `FN_` env surface is complete and is the only env surface — a scan of all
  three modules yields exactly `FN_PROFILE`, `FN_OFFLOAD_STORE_BATCH_FRAC`,
  `FN_OFFLOAD_PROMOTE_FRAC`, `FN_EXPERT_UNION`, `FN_EU_START`, `FN_EU_CALLS`,
  `FN_EU_OUT`. No module reads a `DS4_`-prefixed variable; the surviving `DS4_`
  and `ds4` strings are prose inside the notice headers and docstrings
  describing the rename and the upstream motivation.
- All three adaptation notices name their upstream file in
  `AlexKGwyn/ds4-vllm @ a8f620d` and Apache-2.0.
- `fn_expert_union` still wraps `fused_topk` in
  `vllm/model_executor/layers/fused_moe/router/fused_topk_router.py` — the
  re-target qsa-53896.md §4 establishes, not upstream's
  `make_routing_data`, which is never on this model's path — and its kill
  switch still defaults off.
- `fn_offload_batch` keeps both load-bearing invariants: the store budget
  floors at `max(offloaded_block_sizes)` (below that floor `_build_store_jobs`
  would advance a group's cursor past blocks that are then skipped for good),
  and promotion is counted in blocks rather than tokens.

## Boundary

This attempt touches exactly one tracked file,
`tests/RECONCILE-instruments-2026-08-30.md`.
