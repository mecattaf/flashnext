# MORNING — the operator's first ten minutes

> ## ⚠ CORRECTED 2026-08-31 — READ BEFORE ACTING ON THIS DOCUMENT
>
> **`handoff/catalog-row.patch` is ALREADY APPLIED. Do not apply it.** Verified
> directly: the `flashnext-fp8` anti-prune row is live at
> `dotfiles/lib/local-models.nix:1116` — its own `notes` field reads *"THIS ROW IS
> THE ANTI-PRUNE"* — and `git apply --check handoff/catalog-row.patch` now fails.
> `llama-swap` is `active` on both twins. **The prune hazard is closed.** Every
> instruction below ordering the operator to apply this patch as a first act is
> stale, and two of six forensic agents were misled by it.
>
> **The TP=2 pre-arm patch is also already applied** (drop `--enforce-eager`, add
> `--limit-mm-per-prompt`, `--max-num-batched-tokens`). Do not re-apply.
>
> **New consequence that is NOT closed:** the deployed prune strips files that
> `scripts/stage-weights.sh` rsyncs back and byte-counts (`README.md`, `LICENSE`,
> `.gitattributes`), which makes **cp-weights permanently non-idempotent** — it will
> re-trigger the checkpoint-purity failure class that ate run 2's budget. Reconcile
> that before anything forces cp-weights to re-run. See `handoff/RUN3-BRIEF.md` §4.1
> and §10.0 item 5.


Rendered overnight by the `morning-ledger` lane on 2026-08-30, **before** the
GPU checkpoints ran. That ordering is deliberate (`maxParallel=1`, doc lanes
first): no checkpoint failure can cost you this file. It also means half the
receipt table below is a *forecast* — every row is labelled with which kind it
is, and nothing here is a claim about a measurement that had not happened when
these bytes were written.

Yesterday's halt record is not gone: it was moved verbatim to
[`docs/DAYRUN-STOP-STATE-2026-08-29.md`](DAYRUN-STOP-STATE-2026-08-29.md)
before this file was written. Nothing was clobbered.

Governing spec: [`specs/flashnext/spec.md`](../specs/flashnext/spec.md) claim
7.1 (this ledger), 7.2 (typed blockers), 7.3 (your disposition), ruling P13
(promotion is a human act), ruling P16 (blocker protocol), F.12 (no silent
skips). The full reasoning behind the night's shape is
[`docs/DECISIONS-2026-08-30.md`](DECISIONS-2026-08-30.md).

## Do these in this order

1. **Read the receipt review** below — top table (banked before the night),
   then the pre-declared table (what should have landed while you slept).
2. **`ls results/receipts/failed/`.** Every file there is a typed blocker.
   Read them before anything else. An empty directory is the good morning.
3. **Check the transport rung** — `jq -r '.data.transport.fn_transport_rung'
   results/receipts/bench.json`. If it reads `wire-fallback`, see
   [Transport-rung caveat](#transport-rung-caveat) before you plan the day.
4. **Apply `handoff/catalog-row.patch`** — see
   [Promotion checklist](#promotion-checklist). This precedes *any* rebuild and
   *any* restart of the model-sync trigger service. It is the act that stops
   the weights being deleted again.
5. **Then, and only then**, the attended work: RDMA fetch-and-build + A/B, the
   stream bench after teardown, the optimization menu.

Do not re-arm the campaign if lanes are still running at wake — steer.

## Receipt review

Grading authority is [`scripts/receipts-verify.py`](../scripts/receipts-verify.py).
Two rules to hold in your head while reading:

- A **missing** receipt is legal to the gate — it means the step did not run.
  It is *not* legal to the spec: F.12 says an overnight step recorded as
  skipped needs a typed blocker here. If a row below is missing with no fail
  receipt and no blocker entry, that is the thing to chase.
- A **quarantined** receipt (`results/receipts/failed/<step>.json`) is a
  typed blocker, printed by the gate as a loud `WARN` and deliberately *not*
  counted as a violation. One graded failure costs one step, never the night.

### Banked at render time (observed, not forecast)

| receipt | status | what it says | bound it was graded against |
|---|---|---|---|
| `results/receipts/build.json` | **pass** (`2026-08-29T07:07:21Z`) | container lane, `flashnext:dev`, fork `bdb6f042`, torch `2.13.0+rocm7.14.0`, triton `3.8.0+git4cff872c.rocm7.14.0`, 839 s wall | claim 3.1 / ruling P4: well-formed, `status != fail`, and the lane named. `cp-build` re-runs the build and re-checks `torch`/`triton`/`fork_commit` for drift rather than trusting this file. |
| `results/receipts/weights-coordinator.json` | **pass** (`2026-08-29T12:47:30`) | 131 shards, 185,563,854,698 bytes | claim 5.1: `shards >= 131`. |
| `results/receipts/weights-worker.json` | **pass** (`2026-08-29T12:47:30`) | 131 shards, 185,563,854,698 bytes | claim 5.1: `shards >= 131`. |

**Read the two weight receipts with suspicion until cp-weights overwrites
them.** They were true when written and then became false: the fleet's
local-models sync retired the staged artifact from *both* nodes at ~20:07
(worker) and ~20:14 (coordinator) the same evening, because it has no catalog
row. `cp-weights` re-stages (~185.6 GB/node, ~75–80 min/node) and rewrites
them. If the timestamps above are still `2026-08-29T12:47:30` when you read
this, cp-weights did not complete — check `results/receipts/failed/` and the
lane log.

### Pre-declared — receipts that land after this file was rendered

Each row names the path, the bound `scripts/receipts-verify.py` enforces, and
how to read the result. A row with no bound listed is graded on shape only:
well-formed JSON carrying `step`/`status`/`ts`, and `status != "fail"`.

| receipt | producer | bound | how to read it |
|---|---|---|---|
| `results/receipts/smoke.json` | `scripts/run-smoke.sh` (cp-smoke) | `data.aperture.ttm_pages_limit == 33554432` (U.1) | Claim 3.2, the cheap questions before any weight byte moves: GPU architecture string, a finite fp8 storage cast, the registered architecture identifier, and the admission verdict. Fail receipt → `failed/smoke.json`. |
| `results/receipts/preflight.json` | `host/fn-preflight.sh` | shape only | The hard host gates before the pair is stood up. `data.transport_rung` is the first place the night's transport is stated. The record is also folded into `tp2.json` for U.2 (the latency hold and its tripwire, both ends). Fail receipt → `failed/preflight.json`. |
| `results/receipts/proxy.json` | `scripts/run-proxy.sh` (cp-proxy) | shape only | Claim 4.1: the synthetic checkpoint served single-node with the mmap path engaged, finite output, clean shutdown. `data` records the serve env choices the TP=2 serve then reproduces. |
| `results/receipts/tp2.json` | `scripts/run-tp2.sh` (cp-tp2) | `data.byte_identical_repeat == true` | **The milestone.** Claim 4.2: a greedy 300-token completion byte-identical across two runs, plus the per-rail link speeds and the folded preflight record. First light runs **spec-off** — it is the identity oracle's baseline. Fail receipt → `failed/tp2.json`. |
| `results/receipts/residency.json` | `scripts/run-tp2.sh` (cp-tp2) | per-rank `gtt_gib_per_rank <= 80`; `table_gpu_resident_bytes == 0`; `read_after_warmed_decode == true` | Ruling P11 / claim 4.3, read after 50 warmed decode tokens on both ranks, never at load (F.7). Expected ~76–78 GiB/rank at util 0.62. The runner grades this bound itself, so receipt and gate can never disagree. |
| `results/receipts/fidelity.json` | `scripts/run-tp2.sh` (cp-tp2) | shape only | Claim 4.4: reference losses and frontier logits stored under `results/` beside the receipt. This is the yardstick every later change is measured against — if it is missing, no optimization from the menu below is verifiable. |
| `results/receipts/context.json` | `scripts/run-tp2.sh` (cp-tp2) | `data.decode_ratio_vs_short_context >= 0.9` | Claim 4.5 at 262144 context. **See the quarantine row below** — a sub-bound ratio is deferral-typed and lands in `failed/`, on purpose. |
| `results/receipts/bench.json` | `bench/run-matrix.sh` (cp-bench) | `data.loads_per_arm >= 3`; `data.counterbalanced == true` — *unless* `data.spec_on_failed`, which then requires `data.arms == ["spec_off"]` | Claim 6.3 / ruling P12: three loads per arm, interleaved arms, medians, token fingerprints, depths 0 / 10240 / 102400, spec-on and spec-off arms. Rows land under `results/`. An interim receipt is banked right after the measurement sweep, so a runtime kill cannot erase the numbers. `data.transport.fn_transport_rung` is this receipt's Gate 0 provenance. If `spec_on_failed` is true the night is still honest — a labelled single-arm result and an attended `FN_SPEC_ARGS` iteration, not a lost night. |
| `results/receipts/usb4stream.json` | `bench/usb4stream-bench.py` (cp-usb4stream) | added by the `usb4stream-bench` lane: `outcome == "ok"` requires `open_count == 1` and every schedule field present; `skipped:` / `aborted:` outcomes require their reason text and pass | **See the dedicated row below.** |

#### `results/receipts/usb4stream.json` — read `data.outcome`, not `status`

`cp-usb4stream` runs **dead-last, after `cp-close`**, and only when the pair
serve is already down. The stream device shares cable A with the serving rails,
and an open/close storm against a mismatched peer wedges the router hop tables
(that exact hazard darkened rail 0 once already) — so a live serve is a typed
skip by construction, never a race.

`status` is **always `pass`**: this bench is evidence-gathering for the
transport decision, not a campaign claim, so a mid-run abort must not redden
every later gate run. The whole answer is in `data.outcome`, which is exactly
one of three typed shapes:

| `data.outcome` | meaning |
|---|---|
| `ok` | The bench ran. `data` carries per-size RTT p50/p99 at 64 / 4096 / 16384 / 65536 B, the allreduce-shaped simultaneous exchange at 8192 / 16384 / 65536 B, throughput both directions, `ring_size` and `throttling` as read from configfs, resolved device paths on both ends, and `open_count` (assert exactly 1 per side). `loop: "python"` is stated for honesty about syscall-loop overhead. |
| `skipped:REASON` | One of `skipped:serve-up-on-shared-cable`, `skipped:rail-peer-unreachable`, `skipped:configfs-group-missing`. Checked before any device access. |
| `aborted:PHASE:ERRNO` | The single open attempt or the fixed schedule failed. Everything open was closed, the ssh child killed, the receipt written. Never reopened, never retried. |

**`skipped:serve-up-on-shared-cable` is the EXPECTED outcome after a healthy
night** — a pair still serving at 07:00 is the headline deliverable, and this
bench refuses to gamble it. Do not read that skip as a failure.

Per F.12, a `skipped:` **or** `aborted:` outcome is the typed blocker for that
step: file it under [Blocker template](#blocker-template) below, then get the
real numbers from the attended morning run after you tear the pair down.

#### `results/receipts/failed/` — the quarantine directory, read it first

Any step that fails its own grading writes its fail receipt **there** instead of
into the graded directory. The gate's top-level glob is non-recursive by
construction, so one graded failure cannot redden every later gate run — it
costs one step, not the night. `receipts-verify.py` still prints each one as a
loud `WARN` naming this file.

**Every file present in `results/receipts/failed/` on your arrival is a typed
blocker, and reviewing them is the first thing you do.** Quarantine changes
where a receipt lands, never whether the step failed: byte-compare divergence,
a residency bound trip, transport errors and prompt undershoot still fail their
step and their checkpoint.

Steps wired for quarantine: the four `run-tp2` steps (tp2, residency, fidelity,
context), preflight, smoke, and both weight stagings.

#### `results/receipts/failed/context.json` — a performance finding, not a failure

If this file exists, the honest full-context decode ratio undershot the 0.9
bound. That is a **deliberately deferral-typed** outcome: the runner exits 0 for
that sub-step, cp-bench still runs, and the campaign does **not** fail.

Why it is plausible rather than alarming: 12 full-attention layers walk the full
256K KV cache, and the community precedent for this shape shows roughly a **2×
falloff by 50–73k**. The QSA sparse path argues the other way. Which file exists
in the morning — `context.json` or `failed/context.json` — is the answer.

Read `data.decode_ratio_vs_short_context` for the honest number, then route it
to [the optimization menu](#optimization-menu) as the full-context decode probe
follow-up. The measurement was kept at full depth on purpose: lowering
`FN_CONTEXT_TARGET` to make the bound reachable was proposed and rejected,
because it discards the very number you need.

## Transport-rung caveat

Every receipt carries the night's transport rung so a wire night can never be
mistaken for a rail night. Check it:

```
jq -r '.data.transport.fn_transport_rung' results/receipts/bench.json
jq -r '.data.transport_rung'              results/receipts/preflight.json
```

Two rungs exist in the unattended ladder, and only two: `rail0-sockets`
(thunderbolt0, cable A, listed only if its /30 peer answers a 3-packet ping)
and `wire-fallback` (the 5 GbE control wire, terminal, loudly logged). Never
the second rail, never verbs.

**If any receipt records `wire-fallback`, the night ran on the 5 GbE wire.**
The consequences are exact:

- The receipts are **valid but degraded**. The numbers are real measurements of
  a real configuration — quote them with the rung attached, never bare.
- `bench.json` then **does NOT satisfy the rail-sockets Gate 0** for verbs
  bring-up. `host/rdma/attended-bringup.md` requires a banked *socket-transport
  benchmark measured over the Thunderbolt rail*; a wire artifact is not it.
- **Healing rail 0 is the morning's first transport act.** The dark rail is
  asymmetric — the worker's `thunderbolt0` read NO-CARRIER even after its own
  clean reboot, so a coordinator reboot alone may not fix it. Branch:
  coordinator reboot → replug cable A → worker reboot → if it still will not
  come up, accept a wire day and re-plan. Re-bank a `rail0-sockets` bench
  before any verbs work.

## Open items

Every UNKNOWN from the spec with its drain state, plus what the crystallization
pass left honestly open.

| item | drained by | state at render |
|---|---|---|
| **UNKNOWN-1** — is the fused-mixture block-FP8 kernel numerically sound on this GPU once admitted? | claims 4.1, 4.4 | Open at render. Read `proxy.json` (finite output) then `fidelity.json` (reference losses, frontier logits). Both green ⇒ drained. |
| **UNKNOWN-2** — does fp8 storage plus the widening cast work on the pinned torch build? | claim 3.2 | Open at render. Read `smoke.json`: the finite fp8 storage cast and the admission verdict. |
| **UNKNOWN-3** — does the collective library cross the pair at TP=2 over socket transport? | claim 4.2 | Open at render. Read `tp2.json` — and read `fn_transport_rung` with it, since the answer differs per rung. |
| **UNKNOWN-4** — the fork's runtime behavior on the ruling P4 wheel set | claims 3.1, 4.1 | **Half drained.** `build.json` is banked pass. Runtime half waits on `proxy.json`. |
| **UNKNOWN-5** — speculative-decode acceptance on real prompts and the draft-length optimum | recorded by `bench.json`; tuning is morning work | Open by design. n=3 ships tonight; the sweep is [menu item 4](#optimization-menu). Read the acceptance telemetry and the per-depth identity oracle in `bench.json`. |
| **UNKNOWN-6** — wall-clock of the first full container engine build on this machine | the build receipt | **Drained: 839 s.** Iteration after it rides the cached, ccache'd build directory. |
| **UNKNOWN-7** — does the pinned packaging expression satisfy every override signature and literal against the fork tree? | the substrate-compat audit | **Drained: GO** — [`docs/GATE0.md`](GATE0.md). Blocks nothing; graduation-lane question. Re-run `tools/check_nix_substrate.py` after other commits land, before any HIP rebuild. |
| **DECISION-1** — does the container carry the aiter library? | proposed: no | **Settled: no.** The admission path is self-contained; the aiter half of the pattern donor stays out this campaign. Revisit post-first-light. |

Still honestly open beyond the spec's list:

1. **MTP end-to-end on gfx1151/ROCm at TP=2 is unproven anywhere.** The fork's
   support is real and unit-tested; zero end-to-end GPU receipts exist. Decided
   by tonight's spec-on arm through acceptance telemetry and the identity
   oracle. Fallback banked and honest.
2. **Whether rail 0 heals at all** — asymmetric dark end; see the caveat above.
3. **Night wall-clock versus the serial graph.** cp-tp2 may land 08:00–10:00
   and cp-bench can run into the afternoon. Deliberate. If lanes are still
   running at wake: **steer, do not re-arm.**
4. **Whether cross-arm fingerprints are comparable at concurrency 1.** A dirty
   oracle with a clean per-arm serial replay is the QSA-gather signature
   (repo issue #4), not a spec-decode bug.
5. **The full-context decode ratio at 262144** — see the quarantine row.
6. **The stream primitive's worth as a transport** — decided against the rule
   in [menu item 6](#optimization-menu); first numbers most likely attended.
7. **The RDMA userspace/kernel pin match on 7.2.2** — unanswerable overnight by
   design; surfaces only in the attended bring-up.
8. **ROCm 10 viability** — answered by `results/rocm10-probe.json` (deliberately
   *outside* the graded receipts directory). A green probe on the coordinator is
   strong but not conclusive for the pair; a red probe attributable to KFD must
   be re-checked on 7.2.2 before ROCm 10 is written off. Green ⇒ promotion is a
   five-minute pin swap against a measured result instead of a guess.

## Promotion checklist

**Ruling P13: promotion is a morning human act. Nothing overnight changed any
fleet roster or default.** This checklist is what you verify before any fleet
change. Work it top to bottom.

### 0. Apply the catalog row — before any rebuild, before any sync restart

State the causality plainly, because it already cost this campaign a full
re-stage:

> The fleet's local-models sync **retires any staged artifact that has no
> catalog row.** It runs at every boot, every rebuild, and every start of the
> sync-triggering service. On 2026-08-29 it pruned the staged checkpoint from
> **BOTH** nodes at ~20:07 (worker) and ~20:14 (coordinator) — 185.6 GB × 2,
> gone, hours after receipts recorded it verified.

**Applying `handoff/catalog-row.patch` is the act that makes staging durable.**
It is not cleanup and it is not optional, and it **must precede any rebuild and
any restart of the model-sync trigger service** — either of those re-runs the
sync, and the sync deletes what has no row.

```
git apply --stat handoff/catalog-row.patch    # receipts-verify runs this too
git apply --check handoff/catalog-row.patch
```

Then apply it in the fleet configuration per `handoff/` instructions, rebuild,
and only then let the sync service run again. Confirm the staged directory
survives one full sync cycle before you trust it.

### 1. Verify before any fleet change

- [ ] `results/receipts/failed/` reviewed — every file dispositioned, none left
      unexplained.
- [ ] `python3 scripts/receipts-verify.py` exits 0 (WARN lines for quarantined
      receipts are expected and are not violations).
- [ ] `tp2.json` present and `byte_identical_repeat: true` — the pair actually
      served, greedy output reproducible.
- [ ] `residency.json` inside the 80 GiB/rank bound with
      `table_gpu_resident_bytes: 0` and `read_after_warmed_decode: true`.
- [ ] `fidelity.json` present, with its reference losses and frontier logits
      under `results/` — no optimization below is verifiable without it.
- [ ] `bench.json` present, `loads_per_arm >= 3`, counterbalanced (or honestly
      labelled `spec_on_failed` single-arm), fingerprints recorded.
- [ ] `fn_transport_rung` read and recorded in your disposition.
- [ ] Weight receipts re-stamped by cp-weights (not the `2026-08-29T12:47:30`
      round-1 timestamps).
- [ ] Catalog row applied and survived a sync cycle (step 0).

### 2. Record the disposition (claim 7.3)

Write promotion **or** blocker. Promotion to the standing serve is an env flip
in `host/fn-cluster-up.sh` (`FN_SPEC_ARGS` for the speculative profile), never
an overnight edit. Nothing is promoted on an unreviewed receipt, and no
benchmark number is quoted from a single uncounterbalanced run (F.8).

### 3. Then the attended lanes, in this order

1. Attended RDMA fetch-and-build on kernel 7.2.2, **worker first, never both at
   once**, then the A/B per `host/rdma/ab-protocol.md` — **only if**
   `bench.json`'s rung is `rail0-sockets`.
2. Tear the pair down, run the stream bench attended, apply the decision rule
   in `docs/USB4STREAM-TRANSPORT.md`.
3. The optimization menu.

## Blocker template

Ruling P16: **an unresolvable upstream defect ends as drafted issue text under
`handoff/upstream-issues/` plus an entry here — never as a silent skip.** F.12
extends that to any step recorded as skipped, including a `skipped:` or
`aborted:` outcome on the stream bench. F.14 stands: a lane never opens an
upstream pull request or issue; you file it, attended.

Copy this block into the section below, one per blocker:

```markdown
### BLOCKER-<n> — <one-line symptom>

- **Step:** <checkpoint or lane id, e.g. cp-tp2 / cp-usb4stream>
- **Receipt:** <results/receipts/failed/<step>.json, or "absent — step did not run">
- **Typed outcome:** <status=fail | skipped:REASON | aborted:PHASE:ERRNO>
- **Observed:** <what the receipt and logs actually say — no narration>
- **Upstream cause:** <the defect, named, with file:line or PR/issue id>
- **Drafted issue:** handoff/upstream-issues/<slug>.md   (drafted, NOT filed — F.14)
- **Blast radius:** <which later steps this invalidates, which it does not>
- **Disposition:** <retry attended / defer to menu item N / file upstream and park>
```

### Blockers recorded

*None at render time — this lane ran before the checkpoints. Anything the night
produced is in `results/receipts/failed/`; transcribe each one here as you
disposition it.*

## Optimization menu

Nothing here is on the overnight path. Each item is a morning-or-later act,
measured against `fidelity.json` and the counterbalanced protocol of ruling P12
— a number from a single uncounterbalanced run is not a result (F.8).

1. **Tuned mixture-kernel config generation for this GPU.** The fused-mixture
   path reads per-device tuned-config JSONs; generate
   `E=512,N=640,device_name=Radeon_8060S_Graphics,dtype=fp8_w8a8`. The
   mechanism knowledge is banked in `IMPORTS.md` §2.2 (the community's tuned
   configs themselves are `int4_w4a16`, E=256 — wrong shape, right mechanism).
2. **Per-shape kernel block-size tuning for the 2560×640 and 640×2560 expert
   shapes.** Community precedent: a *forced* tile choice beat the
   auto-selector on exactly these shapes. Cheap, self-contained, and directly
   under item 1's umbrella — do it with item 1, not instead of it.
3. **Graph-capture matrix.** First light runs the sanctioned piecewise mode the
   table path's guard demands (ruling P10) with the mmap operation as a split
   boundary; plain eager is refused. Full-graph capture is explicitly morning
   work — sweep capture modes against decode throughput and load time.
4. **Speculative-head depth sweep, n ∈ {1,2,3,4}** (UNKNOWN-5), plus the
   `index_share_for_mtp_iteration` and `disable_padded_drafter_batch` knobs.
   n=3 shipped tonight because the head is a *single* layer that chains and
   acceptance decays with chaining depth. Every arm needs the per-depth
   identity oracle clean: at temperature 0, speculative output must match plain
   decode byte-for-byte. **Spec-on numbers are not quotable on a dirty oracle.**
5. **Attended RDMA go/no-go.** Honest cost: **2–4 attended hours, two more
   reboots**, the worker's deploy path must move off the fast rail *first*, and
   **nothing is staged for kernel 7.2.2 yet** — the staged patched-module sets
   cover only 7.1.4 and 7.2.0, so the fetch-and-build on both nodes (worker
   first) comes before any A/B. Honest expectation: **≈ +3.4 % measured
   precedent over held TCP.** Real, worth an attended morning, never worth an
   unattended night. Gate 0 stands: a committed **rail-sockets** benchmark must
   exist first (a `wire-fallback` bench does not open it). One rail only,
   never both. Adopt only on a fingerprint-clean counterbalanced majority win.
6. **Stream-primitive port — the morning decision rule.** Do the 2–4
   attended-day port of the reference doorbell allreduce (the 105 µs bar) from
   verbs onto the in-tree stream device's read/write **only if BOTH hold**:
   - the banked exchange p50 at **8–16 KiB is ≤ ~40 µs**, and
   - the bench matrix shows decode is **allreduce-dominated**.

   Note the sequencing: the stream bench **skips while the pair serves**, so
   after a healthy night it will have banked
   `skipped:serve-up-on-shared-cable` and the first real numbers come from the
   attended morning run *after teardown*. Run it, then apply the rule. The
   collective-library net-plugin route is already rejected for the latency goal
   — see `docs/USB4STREAM-TRANSPORT.md` for both deliberations.
7. **Full-context decode probe follow-up (when deferred).** If
   `results/receipts/failed/context.json` exists, take the honest ratio from it
   and work the falloff: 12 full-attention layers over 256K KV is the suspect.
   Candidate levers — fp8 KV (doubles stream count), the
   `--kv-cache-memory-bytes` budget, capture mode from item 3, and the QSA
   gather path's behaviour at depth. Re-measure at full depth; do not lower
   `FN_CONTEXT_TARGET` to make the bound reachable.
8. **Jumbo-frame A/B on the transport.** MTU sweep on the rail (and the wire,
   on a wire night), counterbalanced, decode-median scored. Cheap; do it before
   anything requiring a reboot.
9. **Draft-length / batch-shape sweep for the mixture path.** Large ubatch is
   what a mixture model wants; pair it with `--max-num-seqs` (pinned at 32
   tonight because GDN state is ~54 MiB/seq/rank and scales ×(1+n)).
10. **Load-phase page-cache drop-behind — remedy pointer for repo issue #2.**
    The silent memory-saturated load is real (confirmed 2026-08-29). The
    community remedy worth porting: **release file pages in chunks behind the
    loader** as it walks the shards, so the load phase stops fighting the page
    cache the mmap'd table needs. Check disk read throughput before killing a
    slow load; a mid-request hang retries with `HSA_ENABLE_SDMA=0`.
11. **The four-bit expert lane.** Pointers are already recorded in `IMPORTS.md`
    §2.2 so nobody re-finds them: upstream PRs #46186 (W4A16 GEMM on gfx1151)
    and #46676 (native HIP MXFP4 for RDNA3), plus #44331's tuned-config
    mechanism. int4 WMMA is the only primitive above fp16 rate on this silicon
    (2.03×) — this is where the next real win lives, and it is a campaign of
    its own, not a menu afternoon.
12. **Audit drafter hidden-state vs prefix-cache position sync — before the
    speculative profile is declared production.** A community v1.1 fix records
    the exact failure mode: a **cached target prefix is reused while the
    drafter's state timeline sits elsewhere**, which breaks multi-turn. It
    shows up as *degraded acceptance, not a crash*, so it will not announce
    itself. Audit it, then **file the repo issue** (attended — F.14 forbids a
    lane doing it).
13. **`ROCBLAS_USE_HIPBLASLT=1`**, now with a shipping gfx1151 precedent. One
    env flip, counterbalanced A/B, cheap to test and cheap to revert.

### Rejected with reason — do not re-litigate

Recorded so none of this is ever re-investigated from scratch. Each entry is
closed on its own merits, not on preference.

| item | why it is closed |
|---|---|
| **The community int4-WMMA expert lane ("IU4")** | It is not an on-disk format — the shipped artifact stores routed experts as plain Q4_1, and "IU4" names an execution path. The actual int4 WMMA lane reads a **precomputed 60.94 GiB expert bank** whose only entry point is a C API **no shipped tool calls**, with no bank builder in the repo — **unreachable even in its own public release**. It is bound to a **different runtime's artifact** and is **single-device only** (`split_mode none`), structurally incompatible with TP=2. The *general* int4 direction stays open as menu item 11; this particular estate does not. |
| **Indexer Hadamard rotation + FP4 QAT** | Fidelity art for a **different model family's trained indexer graph** — it reproduces that model's official indexer QAT graph and is a correctness-of-top-k fix *for a model trained with it*, not a speed feature. **Our engine's indexer is weight-free bf16** with no such trained graph (read in source: the indexer asserts bf16; zero hits for hadamard/fp4/e2m1/qat). **Transplanting it would corrupt top-k selection**, not improve it. |
| **The community qwen4exp-on-ROCmFPX integration patch** | Zero code adopted: it contains no FP4 hunks at all, targets the wrong engine and topology, and **deliberately drops the MTP head** we are enabling. Knowledge banked under `specs/flashnext/evidence/kingjones-qwen4exp/` — including an independent confirmation of our fork's QSA-cache separation design. |
| **The ncclNet-plugin route for the stream primitive** | Rejected **for the latency goal**: it sits under the collective library's proxy/protocol stack, which eats most of the win; HopID scarcity forces a multiplexer over one or two long-lived streams; and comm teardown on every serve restart is exactly the open/close pattern the wedge hazard forbids. 3–5 attended days to a fragile prototype. The doorbell-allreduce port (item 6) is the real path. |
| **The 4-rail aggregate and the 2-cable split** | Physically unavailable on this NHI: 3 DMA rings per controller means exactly **one** RDMA lane per cable (the second lane's `-12` probe error is permanent and cosmetic). Decode is latency-bound, where aggregation buys nothing. Cable B stays parked; a two-socket-rail aggregation test is a cheap attended item if you want the datum. |
| **ROCm 10 as the overnight substrate** | Not rejected — **deferred to a measured probe** (`results/rocm10-probe.json`, after cp-bench). The wheels do exist; what is missing is any evidence that anyone has run this engine on ROCm 10 on gfx1151. Promotion is a morning act against a probe receipt, never a guess. |
| **Manually placing the engram table in CPU memory** | Measured elsewhere on this hardware class: forcing the table to CPU made decode *worse* (23 → 13.4 tok/s) — "the kernel already streams it better." An argument against ever adding a manual placement knob. |
