# RUN-3 BRIEF — consolidated state, intel, and plan

Written 2026-08-30 (late evening), before arming the third campaign.
Supersedes the stale instructions in `handoff/README.md`, `docs/MORNING.md` and
`handoff/DAYRUN-NOTES.md` **wherever they conflict** — those documents remain
accurate as history and are wrong in several present-tense claims, itemized in §4.

**Provenance.** Two all-Opus multi-agent sweeps, 2026-08-30 evening:
a 6-agent forensic sweep of the tally situation (harness state, worklist, run-1
harvest, upstream issues, product gaps, run-1-vs-run-2 delta), and an 8-agent
external-intel sweep (ds4-vllm `tbv_ar`/`tbv_ar2`, PR #4 + dual-cable, OdinLink
family, `jyatesdotdev/strix-rdma`, our own transport baseline, Baekpica SSD-PLE,
`qwen4exp` + quant + GLM-5.2 cross-check, MTP). ~2.0M subagent tokens, 798 tool
calls. Claims below carry their evidence; anything marked *measured* was measured
on this hardware during the sweep.

> **START AT §16, THEN §13.** §16 is the only section measured on *this* hardware:
> the §15.6 probe order, run 2026-08-31 before arming. It settles three things the rest
> of the document could only inherit — the ROCm 7.14 registration trap is **live on our
> stack** (§16.1), the in-kernel atomic doorbell is **reliable at 25,800 gates and ~10×
> cheaper than ds4's host callback** (§16.2, reversing §14.2), and the cudagraph is worth
> **≈0.1 µs/launch at the HIP layer** (§16.3, de-risking §13.1's escape hatch).
> Then §13: an adversarial sweep overturned two verdicts, caught a bug that would have
> made the whole Track B arm measure nothing, and found a root cause sitting on Track A's
> own baseline path. Order of authority: **§16 > §15 > §14 > §13 > everything above**.
> The work order is §14.15 as amended by §16.7.

This document is deliberately **not** tally-shaped. Converting it into a worklist,
gates, conflict domains and acceptance criteria is a separate specialized pass.

---

## 1. Tally harness state — two hard blockers

### 1.1 The escalation-fold wedge (campaign name is poisoned)

`tally-campaign-poll.service` has failed **every 60 s since 10:25 today**
(685+ consecutive failures, still failing) with
`multiple machine escalations claim this campaign`, against run-2 registration
`01a050a1-77a7`, which is **still armed at armSerial 10** holding a lease acquired
2026-08-29T06:05Z (during run 1).

Root cause, read from source and confirmed by replaying the 61-record receipt log:

- `crates/tally-core/src/attempt_receipts.rs:22` — `MAX_TASK_LIFETIME_ATTEMPTS = 10`.
- `crates/tally/src/cli/campaign.rs:6033` (`active_escalated_tasks_from_receipts`) —
  the latch branch adds any task at/over the cap as an escalation contributor,
  testing only `current_revisions.contains_key(task_id)`. **It is blind to task
  revision and to task completion.**
- `cp-weights` has **13** lifetime attempts, `proxy-tooling` has **10**. Both are
  `done`; both were title-revised. Neither fact helps.
- Escalations at seq 48, 50, 53, 57, 61 therefore all carry
  `{cp-weights, proxy-tooling}`. The only pardon scoped to that exact set is
  **seq 44**, which precedes all of them. Pardons 54/55/58/59 are scoped to
  `{instruments}` and cover nothing.
- `campaign.rs:6131` — `if escalations.len() > 1 { return Err(...) }` fires forever.

**It survives disarm.** The attempt-receipt log is keyed by campaign *name* alone
(`~/.local/state/tally/campaigns/attempt-receipts/flashnext/attempt-receipts-v1.jsonl`,
path built at `campaign.rs:4891`), while `run_campaign_disarm` (`:9617-9640`) removes
only the registry entry and graph snapshots. **It survives a tally rebuild** — the
same code is at HEAD, in both the CLI and the driver (`actions.rs:6330`).

The fold gates the **poll** path specifically (single call site `campaign.rs:6521`
inside `dispatchable_poll_liveness_arm`), which is why run 2 kept reaching new arm
serials all day while poll failed continuously — `tally campaign arm` stayed
functional. Filed upstream as **#642**.

> **FIX: run 3 uses a NEW campaign `name` in the worklist manifest.** Fresh receipt
> log, fresh ref namespace, zero inherited latches. Minutes of work. The alternative
> — hand-truncating the receipt log — destroys the durable record. Do not do it.

### 1.2 Stale remote quiescent ref

`refs/tally/spec-build/v1/9580b7cc868de0369023d414/344d41a8de18838e2902609aa1f4b9cbbcf66c476683aa645b0271681c668251/summary/quiescent`
(oid `612d4506`) is keyed to the **current** worklist digest. Per our own playbook
this kills passes with "summary disagrees" once a pass makes progress, and
`tally flow supersede` does **not** clear it. Three sibling stale refs (`2497fec`,
`63d45dd8`, `eaf42577`) are inert only because their worklist shapes are historical.

Delete with `git push origin :refs/tally/.../summary/quiescent` (consider all four).
**Mooted if the campaign name changes** — new name, new namespace.

### 1.3 Also true, lower severity

- One inbox entry open: **seq 60**, `blocked`, task `cp-tp2`, 2026-08-30T13:57:31Z,
  "second node physically unreachable". **Its premise is now false** — see §1.4.
- Installed pin `b4zr2yn0…` = `tally 0.1.0 (rev 9deec68a)`; daemon healthy, pid 1818,
  restarted 18:42:49 today. tally.nix working tree is 10 commits ahead on
  `fix/agent-model-review` (31709de).
- **A tally.nix self-repair sprint ran tonight 21:43–23:07** across 14 lane worktrees
  and fixed essentially the whole run-2 backlog — but every commit sits on **local,
  unpushed `wip/*` branches** merged into a local `integration/sweep-trial`
  (22 ahead of main, 0 behind). `dotfiles/flake.lock` still pins `9deec68`.
  **Unless that batch is merged, pushed, and rebuilt, run 3 inherits run 2's harness.**
- Upstream ledger: the "morning filing" **did** happen — 13 issues **#635–#647** filed
  11:16–11:23Z, three findings added as comments to #623/#626/#627 rather than
  duplicated. Of the run-1 set, **#620/#621/#622/#624/#625/#628 are closed with fixes
  inside the deployed pin**; **#623, #626, #627 remain open**.
- 14 stale local lane branches under
  `refs/heads/tally/flashnext-campaign-01a050a1-…/` plus `refs/heads/tally-work/…`.

### 1.4 The physical blocker is gone

The pair is up. `ssh 10.99.9.2` answers as `worker`; thunderbolt0 and enp191s0 carry
their fleet addresses. The whole `cp-tp2 → cp-bench → cp-close` chain is physically
runnable. **Verify again immediately before arming** — this is exactly what failed at
13:57Z on 2026-08-30.

---

## 2. What actually completed (worklist ledger)

The worklist (`silent-factory-worklists/flashnext.json`) is schemaVersion 1, campaign
`flashnext`, **18 tasks** (10 implementation + 8 checkpoint), `maxParallel: 1`,
maxTasks 20, adapter claude-code, five gates. It carries **no** completion or attempt
metadata — all run state lives in `~/.local/state/tally`.

At final quiescence (2026-08-30T13:57:54Z, arm 10): **13 of 18 credited DONE** —
including **cp-smoke** (passed 09:07:38Z, 09:24:48Z, 10:32:11Z, 10:47:18Z) and
**cp-proxy** (passed 13:50:36Z), both of which the handoff notes leave ambiguous.

**Never completed (5):** `cp-tp2`, `cp-bench`, `cp-close`, `cp-usb4stream`,
`rocm10-probe`.

`cp-tp2` did **not** fail on code. The execution host had lost thunderbolt0,
thunderbolt1 *and* enp191s0 (only lo/wifi/tailscale0), and `ssh 10.99.9.2` returned
"No route to host" (exit 255). The driver correctly declared frontier quiescent, and
no session was alive to answer — the overseer had written its FINAL RECORD and
compacted at 12:51.

### 2.1 Three structural worklist defects — fix these before anything else

1. **Receipts are being destroyed.** `results/receipts` is a conflictDomain of
   **container-recipe only**, which runs *first*. Every later checkpoint (smoke,
   proxy, tp2, residency, fidelity, context, bench) runs in an ephemeral lane
   worktree and writes `$REPO_ROOT/results/receipts` **there**; the worktree is then
   discarded. **cp-smoke's and cp-proxy's passing receipts are provably gone** — the
   lane-worktree dir is empty. tp2/residency/fidelity/context/bench would evaporate
   identically. *This is why two green nights produced zero engine evidence.*
2. **Unowned executables.** `scripts/run-smoke.sh`, `scripts/stage-weights-both.sh`,
   `scripts/verify-fork.sh`, `scripts/receipt-restore.py` and `patches/` are
   *executed* by checkpoints but owned by **no task's conflictDomains** — no lane
   could ever fix them. The podman `-i` defect in `run-smoke.sh` alone burned ~5 hours
   and 6 attempt receipts for this reason.
3. **The closing gate is blind.** `cp-close`'s argv exits 0 today with 3 receipts on
   disk, when `docs/MORNING.md` pre-declares 13. Likewise
   `scripts/receipts-verify.py` exits 0 ("3 receipts checked, 0 violations") because
   **missing receipts are legal** — the gate cannot distinguish "nothing ran" from
   "everything passed".

### 2.2 Other worklist facts

- `cp-weights`' title is **277 of 300 bytes** and already truncated mid-sentence.
  Almost no headroom left for another latch-clearing revision. (#647: the cap is 300
  *bytes* in the Rust validator but 300 *characters* in the flow schema.)
- Budget over-subscribes the night: ~19.8 h of checkpoint `runtimeMaxSec` plus up to
  ~40 h of implementation lanes at `maxParallel: 1`.
- `cp-usb4stream` will bank `skipped:serve-up-on-shared-cable` by construction —
  nothing in the graph tears the pair down before it runs.
- `rocm10-probe` was never started; its acceptance criterion is the only failing one
  on main.

---

## 3. Where the two nights went

**Run 1** (2026-08-29, campaign `01a04c1f`): 537 systemd units, 45 flow runs, 6h24m
→ **4 merges**. Of 40 task-scoped attempts, **only 9 ever caused a model to emit a
token**; **29/40 (72.5%)** died on a harness or provider defect. The largest single
bucket is **not on any issue list**: **10 attempts died on an HTTP 429 qwen token-plan
quota exhaustion at the first API call**, producing 3.4 KB zero-token transcripts
that the driver reported as "agent produced no commit relative to the prepared base"
and the judge classified as an ownership/conflict-domain failure. Second largest:
**11 attempts** of #624 `FlowAdmissionDenied` with empty details, no worktree ever
prepared.

**Run 2** (2026-08-30, registration `01a050a1-77a7`) did **not** end on capacity. No
quota was hit; tally reports "no attempt reported usage"; the model layer was
**100% reliable — $29.23, 16/16 opus runs succeeded**. It ended on the network blip
in §2. Its self-inflicted loss: **~7.5 of the first 8 hours** in harness/repo defect
loops (zero-diff squash, checkpoint purity, podman stdin, steward result-schema
mismatch) before the first checkpoint credit — 10 arm serials, 5 instruments carrier
revisions. **52% of run-2 receipts were the #625 result-projection defect**, filed
the day before.

**No harvest of run 2 exists.** 1,031 unit-exit records and 1,031 captures sit only in
`~/.local/state/tally`, unsnapshotted. Snapshot them before run 3 overwrites context.

**Conclusion: the bottleneck is the harness, not the model.**

---

## 4. Corrections to our own record — six factual errors that are actively steering us wrong

Each of these is present-tense wrong in a document we would otherwise follow.

1. **`catalog-row.patch` IS applied.** `dotfiles/lib/local-models.nix:1116` carries
   the `flashnext-fp8` anti-prune row and llama-swap is `active` on both twins
   (verified directly). **The prune hazard is closed.** `handoff/README.md`,
   `docs/MORNING.md` and the DAYRUN FINAL RECORD all still order the operator to
   apply it as the morning's first act. They are stale. *Two of six forensic agents
   were misled by this.*
   - **New consequence:** the deployed prune strips files that
     `scripts/stage-weights.sh` rsyncs back and byte-counts (README.md, LICENSE,
     .gitattributes), making **cp-weights permanently non-idempotent** — it will
     re-trigger the exact checkpoint-purity failure class that ate run 2's budget.
     Reconcile this before anything can force cp-weights to re-run.
2. **The TP=2 pre-arm patch IS applied** (drop `--enforce-eager`, add
   `--limit-mm-per-prompt`, `--max-num-batched-tokens`). Do not re-apply.
3. **`README.md:180-183` is wrong**: the "3,450-line unpublished zero-copy patch this
   repo deliberately does not carry" **is published, in-repo**, at
   `~/Downloads/ds4-vllm/tbv/ibverbs-local.patch` — 3,453 lines across 9 kernel files,
   against the **exact ibverbs pin we already run**. Our cable-B parking decision
   (`README.md:178-183`) follows from this error.
4. **`README.md:187-189` is wrong** to quote 105 µs as a bar. ds4's own in-patch
   comment (`vllm-upstream.patch:1556-1559`) says *"Per-op latency here is UNMEASURED
   on the current stack — do not quote a figure without re-running
   tbv/build-scripts/verify-tbv-perf.sh"*. Any go/no-go trigger computed against 105 µs
   is computed against a disclaimed number.
5. **The GPL exclusion is misapplied.** `IMPORTS.md` §4 excludes "RDMA/tbv anything
   (GPL boundary)" and `innovation-ledger.md:123` lists `tbv_ar`/`tbv_ar2` NOT LIFTED
   on that ground. **ds4's own THIRD_PARTY_NOTICES puts `container/native/tbv_ar2.hip`
   under Apache-2.0.** What is GPL is the verbs/kernel stack underneath, not the
   collective. Move the collective out of the exclusion list.
6. **`docs/DECISIONS-2026-08-30.md:876` miscategorizes `strix-rdma`**, grouping it
   with odinlink under "ibverbs RDMA — queue pairs, memory registration, one-sided".
   strix-rdma is **not** ibverbs and its authors explicitly reject soft-RoCE. This
   error is what suppressed the best transport option we have.
   - Related: **§5.3's rejection of the ncclNet plugin route** rests on "a plugin
     cannot approach the 105 µs bar". `wkljohn`'s measured corpus shows a plugin
     carrying a real 24 KB two-rank all-reduce at **100 µs vs TCP's 286 µs**. The
     stated ground is false; the *other* grounds (RCCL proxy stack, HopID scarcity,
     teardown wedges) still stand, so the conclusion may survive — but rewrite the
     reason.

---

## 5. Product milestone truth

Receipts exist for exactly **two** milestones — weights staged both nodes, container
built (`results/receipts/{weights-coordinator,weights-worker,build}.json`). Both are
**pre-GPU**. Spec claim group **R4 ("engine proof", 4.1–4.5) is 0-for-5.**

- **Achieved and real:** single-node proxy first light on `flashnext:dev` —
  SERVE-READY + completion, fp8 MoE admitted via in-kernel bf16 upcast, QSA on
  flash-attn 2.8.3 AMD-Triton, PLE table mmap'd from NVMe with 0 GPU-resident table
  bytes, PIECEWISE compile. Its receipt was destroyed (§2.1).
- **Never run:** TP=2 first light, cp-bench, RDMA A/B, spec-on (not once).
  `bench/run-matrix.sh` has never emitted a byte.
- `TRITON_CACHE_DIR` / `TORCHINDUCTOR_CACHE_DIR` are pinned **only** on the TP=2 path
  — not proxy, smoke, make-proxy, or the Containerfile — and both dirs are empty. The
  ds4 ~25-minute-recompile trap is live on every other path.
- **`llama-swap` is `active` on both nodes right now**, holding GPU. Must be stopped
  before any serve.
- `host/rdma/` staged modules are **vermagic-dead** on 7.2.2 — re-run
  `host/rdma/fetch-and-build.sh`.
- We never set `VLLM_PLE_MMAP_WORKERS` / `_CHUNK` / `_PREWARM` anywhere; the PLE path
  runs on library defaults (32 / 2048 / off).
- `docs/DECISIONS-2026-08-30.md` step 0 (rename NAS dir to `flashnext-fp8`) is
  unreconciled with `scripts/stage-weights.sh`'s hardcoded path. Settle explicitly.

---

## 6. Transport intel — the big one

### 6.1 `tbv_ar2` is real, ROCm, gfx1151, Apache-2.0 — and we already own the hard half

`AlexKGwyn/ds4-vllm` `origin/main` @ `a8f620d` (**the exact commit our prior deep-dive
read**) carries complete, buildable code that we missed:

- `container/native/tbv_ar.c` (364 lines, v1) — **inert in ds4's own deployment**;
  fails init with "slot tensor not page-aligned" (`vllm-upstream.patch:1580-1584`).
  Do not port v1.
- `container/native/tbv_ar2.hip` (411 lines, **v2 — the live one**)
- `container/rootfs/.../tbv_ar.py`, `tbv_ar2.py`
- Built in-image: `container/Dockerfile:100-105` →
  `hipcc -O2 --offload-arch=gfx1151 -shared -fPIC -o libtbv_ar2.so tbv_ar2.hip -libverbs -lpthread`

**Mechanism (v2).** RC QP (`IBV_QPT_RC`, `:294`, timeout=14 retry_cnt=7 rnr_retry=7).
Sockets appear only for a one-shot TCP rendezvous (port 18531) exchanging
`{qpn, rkey_data, rkey_flag, addr_data, addr_flag, gid[16]}`. Stream order:

```
hipMemcpyAsync D2D into pinned send slot
  → tbv2_doorbell_kernel  (:209)  1 thread: __hip_atomic_store(db, (seq<<24)|nbytes, RELEASE, SYSTEM)
  → [CPU progress_main (:173): acquire-load doorbell → post_round() → ibv_post_send
     data RDMA_WRITE + 8-byte flag RDMA_WRITE (QP ordering puts flag after data) → drain CQ.
     Adaptive: hot-spin within 5 ms of activity, usleep(200) otherwise.]
  → tbv2_wait_add_kernel  (:218, templated <T,ACC>)  thread 0 spins on peer flag with
     __hip_atomic_load(SYSTEM) + __builtin_amdgcn_s_sleep(8) backoff, bails after 2^31 iters
     into an error latch; __syncthreads(); 1024 threads do dst[i] = (T)((ACC)src[i] + (ACC)recv[i])
     reading the recv slot DIRECTLY.
```

**The UMA trick — why this is cheap on our hardware and expensive on anyone else's.**
Staging is `hipHostMalloc(hipHostMallocDefault)`. Their comment (`:262-283`):
*"hipHostMalloc memory is regular user VA (GTT-backed) so ibv_reg_mr's
get_user_pages pins it like any malloc"*. Then `hipHostGetDevicePointer()` (`:277`)
yields a device-side alias of the same physical DRAM. **The NIC RDMA-writes into a
page the GPU reads: zero copies, zero GPUDirect, zero IPC handles.** ds4 separately
confirms gfx1151 has **no** GPUDirect and that RCCL therefore host-stages large
all-reduces — precisely the cost v2 exists to bypass.

**vLLM hook.** `container/patches/vllm-upstream.patch:1547-1605`, hunk
`@@ -252,6 +252,57 @@` on
`vllm/distributed/device_communicators/cuda_communicator.py` — 51 lines prepended to
the **top of `CudaCommunicator.all_reduce`**, ahead of the symm-mem/pynccl chain. Not
a subclass, not a registry, not a monkeypatch. `DS4_TBV_AR2=1` beats `DS4_TBV_AR=1`;
constructed lazily only when world_size==2; latches `_tbv2_failed` on any exception
and never retries; ineligible tensors fall through to stock RCCL.
**Eligibility** (`tbv_ar2.py:44`): `is_cuda and is_contiguous() and dtype in
{bf16,fp16,fp32} and nbytes <= 1 MiB and not is_current_stream_capturing()`.
Cap is `TBV2_MAX_BYTES = 1<<20` (`:42`) — carries the **decode** collective only
(~5–48 KiB); prefill exceeds it and falls back. Our decode sizes are far inside.

**CORRECTED 2026-08-31 — the verbs stack is NOT live.** `handoff/PREARM-REBOOT.md:39`
records the patched matched set (westeri `503c5ae1` + ibverbs `76ba39b6`) first-bound
at boot with `usb4_rdma0`/`usb4_rdma5` ACTIVE/LINK_UP. **That was true at that boot and
is false now.** Verified directly:

- `/sys/class/infiniband/` is **empty**; `thunderbolt_ibverbs` is **not loaded**.
- Loaded instead: mainline in-tree `thunderbolt` / `thunderbolt_net` /
  `thunderbolt_stream`, all `filename: /run/booted-system/kernel-modules/.../7.2.2/...`,
  i.e. **the stock NixOS kernel modules, not our patched build**.
- `/var/lib/flashnext-rdma/` stages `thunderbolt_ibverbs.ko` for **7.1.4 and 7.2.0
  only**. We boot **7.2.2**. Vermagic-dead, confirmed.

Userspace *is* present (`rdma-core-usb4-63.0` with `libusb4_rdma-rdmav59.so`), but there
is **no device for it to open**. Standing up the verbs path therefore requires a module
build **plus** unloading and reloading the thunderbolt stack — which tears down the live
pair. That is an **attended, reboot-class operation, not an autonomous-run operation.**

**But the stream path IS live.** `thunderbolt_stream` is loaded with refcount 6 and
**`/dev/tbstream0..3` exist right now** on the stock kernel. See §6.5.

**Our A/B cannot see it.** `host/rdma/ab-protocol.md:84` swaps only
`NCCL_IB_DISABLE` / `NCCL_IB_HCA` / `NCCL_IB_GID_INDEX` — both arms still ride RCCL's
proxy/protocol stack, which is the layer `tbv_ar2` exists to bypass. As written the
A/B would likely return a small delta we would misread as "RDMA does not help here".
**Add a third arm: verbs + `FN_TBV_AR2=1`.**

**Standalone cheap lever we may not have applied:** mainline hardcodes NHI MSI-X IRQ
moderation at 128 µs, setting a ~65 µs RDMA floor. ds4's `nhi-throttle-mod` loads
`ns=8000` and measures **8.5 µs typical**. Transport-independent.

**Temper the prize honestly.** `host/rdma/ab-protocol.md:117-148` (our own odinlink
fold, dated today) already priced it: ~13× wire improvement → **+3.4% end-to-end**
(8.29 → 8.57 tok/s), because the op assembly around the transport — staging copies,
doorbell, progress-thread wakeup, GPU poll — is where the decode all-reduce actually
spends its time. `tbv_ar2`'s own header agrees. **Design the A/B to detect a small
effect truthfully, not to confirm a large one.**

### 6.5 THE TONIGHT PATH — doorbell all-reduce on the live stock stream device

**Discovered 2026-08-31 by direct inspection; this supersedes §10.3's "stage, do not
arm" recommendation for the stream path.**

The state of this machine inverts the obvious risk ordering:

| path | kernel work needed | device state right now |
|---|---|---|
| verbs (`tbv_ar2` as written) | build `thunderbolt_ibverbs` for 7.2.2 + **unload/reload the thunderbolt stack** | **no device** — `/sys/class/infiniband/` empty |
| **stream (`/dev/tbstream*`)** | **none** | **`/dev/tbstream0..3` live, `thunderbolt_stream` refcount 6** |

`docs/USB4STREAM-TRANSPORT.md` §5.4 scoped "port the reference doorbell allreduce from
verbs onto the stream device's read/write" at **2–4 attended days**. That estimate was
made without a reference implementation. We now have **two**, and the scope collapses:

- **`tbv_ar2.hip`** (ds4-vllm `main`, Apache-2.0, 411 lines, `hipcc --offload-arch=gfx1151`)
  — the architecture, on verbs.
- **`odl_ar2.hip`** (ds4-vllm PR #4, 441 lines) — **the same architecture already
  re-targeted onto a char-device byte pipe**, with only **~40 of 441 lines**
  transport-specific: `post_round():188` is one `odl_tb5_stream_send`,
  `poll_recv():203-220` is one `odl_tb5_stream_recv`.

Substituting `write()`/`read()` on `/dev/tbstream0` for those two calls is the whole
port. The GPU side — doorbell kernel, wait+add kernel, `hipHostMalloc` +
`hipHostGetDevicePointer` UMA staging, the progress thread, the eligibility gate, the
failure latch — is **unchanged and already written for gfx1151**.

**Why this is safe to attempt inside a run.** The hook's own eligibility gate and
`_failed` latch make it fail-closed: any exception latches the path off permanently and
every subsequent all-reduce falls through to stock RCCL. Worst case is **inert**, not
broken. Combined with an env gate (`FN_TBV_AR2=0` by default), it cannot perturb the
first-light baseline.

**Expected performance, honestly.** The stock stream device is a **copying** byte pipe —
this buys the RCCL-proxy bypass, not zero-copy. Against a measured RCCL-over-TCP baseline
of **130 µs p50 @ 4 KiB / 310–315 µs @ 8–16 KiB**, that is still likely a large relative
win, but it will not reach strix-rdma's 24.4 µs. **We have never measured stock
`/dev/tbstream` latency ourselves** — `bench/usb4stream-bench.py` was written for exactly
this and has never emitted a byte. Run it first; it is the cheapest measurement on the
list and it sets the expectation.

**And it upgrades in place.** strix-rdma's 15 patches extend *this same device* with
zero-copy TX/RX ioctls and a DMA-BUF importer. Swapping `write()/read()` for
`SUBMIT_TX`/`POST_RX`/`REAP` later is a change to the same ~40 lines — the collective,
the hook, and the GPU kernels do not move. **So the tonight port is not throwaway work;
it is the first half of the 24.4 µs path.**

**Open question that gates the win, and is cheap to answer:** under our
`-cc.cudagraph_mode=PIECEWISE` serve, is `all_reduce` inside or outside a captured
region? The eligibility gate declines when `torch.cuda.is_current_stream_capturing()`.
If collectives are graph breaks under PIECEWISE (which is the usual reason to choose it),
the custom path fires. If not, it is silently inert — which is exactly the failure mode
PR #4's `eager_break_functional_during_capture` patch exists to fix. **Log whether the
path fires; do not infer it from throughput.**

### 6.2 Our baseline, measured

Our TP=2 collective today is **stock vLLM**: Ray → `CudaCommunicator.all_reduce` →
`PyNcclCommunicator` → RCCL 2.30.4 over **TCP sockets on thunderbolt0**. Zero of our
12 patches touch `vllm/distributed/`. `host/fn-env.sh:152` sets `NCCL_IB_DISABLE=1`
unconditionally; `:142` defaults `FN_TRANSPORT_RUNG=rail0-sockets`.

**Measured tonight, never measured before:**

| path | 4 KiB | 8–16 KiB |
|---|---|---|
| TCP over `thunderbolt0` | **130 µs p50** | **310–315 µs** |
| 5 GbE `enp191s0` ("terminal fallback") | *better at every size* | |

**Our fast wire is ~2× worse than the wire we call the fallback.**
Also confirmed: RCCL 2.30.4 in our image **does** honor `NCCL_NET_PLUGIN`
(ncclNet_v6..v12 present).

**The `160 chained all-reduces` figure is unsourced.** The arithmetic the repo
actually derives is **~96** (48 layers × 2). Config confirms 48 layers / 12
full-attention / 36 GDN. Fix the number wherever it appears.

### 6.3 `jyatesdotdev/strix-rdma` — best measured numbers on the table

**Not RDMA.** Its README explicitly rejects soft-RoCE and soft-iWARP. It is a
**15-patch out-of-tree extension to the in-tree `thunderbolt_stream` (USB4STREAM)
driver** adding (a) a zero-copy mmap'd TX/RX frame-pool UAPI with
SUBMIT_TX/POST_RX/REAP ioctls and (b) a **DMA-BUF importer letting the NHI rings read
and write native `hipMalloc` GPU allocations directly** — GPU → NHI → cable → NHI →
GPU with zero CPU payload copies. Plus an MIT-licensed HIP reference
`tools/stale-cache/tbstream-tp-exchange.cu` (701 lines) implementing a two-node
all-reduce partial-sum exchange.

Measured on two gfx1151 boxes (`bench/results/2026-08-25-tp-exchange-probe.md`):

| metric | value |
|---|---|
| full-duplex 28 KiB exchange, transport-only | **29.0 µs** |
| with the reduce | **35.2 µs** |
| one-way p50 | **24.4 µs** |
| RTT @ 4 KiB | **9.8 µs** |
| per-direction bandwidth | ~1.6 GB/s |

**Better than every bar we calibrated against** — ds4's 105 µs TB soft-RDMA, the
59 µs InfiniBand bar, and our own USB4STREAM memo's projected 120–160 µs landing zone.
Promoted to **production 2026-08-30** for a two-node TP server (4096 tokens at
15.64 tok/s, zero NHI failures/drops/CRC errors).

**It fits our tree.** Verified mechanically: the 15-patch series applies to vanilla
**Linux 7.2.2 — our booted kernel** — after dropping one already-merged hunk (4 lines
of diff), **and** applies on top of **our own already-ibverbs-patched westeri tree**
at `~/.cache/flashnext-rdma-build/src/westeri-thunderbolt` with only **3 conflicting
one-line diagnostic-counter hunks in `nhi.c`**. `host/rdma/fetch-and-build.sh:331-337`
already builds `M=$WESTERI_DIR/drivers/thunderbolt` and **already stages a matched
`thunderbolt_stream.ko`**; `dotfiles/modules/fn-rdma.nix` already ships it. Every HIP
API the reference needs exists in our installed ROCm.

Caveats: **single-cable only** (no notion of a second link — zero hits for
bonding/striping/multi-link). **Never run with vLLM/RCCL/NCCL** — its only consumer is
a C/HIP engine. **Licence is contradictory**: repo LICENSE is GPL-2.0 but userspace
files carry per-file MIT SPDX — resolve before copying code. Our citation of it (their
"NHI verbs ≈ TCP v3" line) is **stale and about pipeline parallelism**, a different
workload shape than TP.

### 6.4 OdinLink and PR #4 — the dual-cable answer is not what the Discord message implies

**PR #4 (`origin/odinlink-stock-perf`)** is a 67-file, +6553/−5872 **DRAFT** opened
2026-08-25 **by the repo owner himself**, still open, zero reviews. It **deletes the
entire `tbv` stack** (`tbv_ar.c`, `tbv_ar2.hip`, both .py, the patched-thunderbolt
tree and `ibverbs-local.patch`) and replaces it with OdinLink on a stock kernel plus
`odinlink/ar2/odl_ar2.hip` (441 lines) behind `DS4_ODL_AR2=1`.

**It does not add two-cable support — it structurally forbids it.** Mainline
`tb_protocol_handler` lacks a `.callback_xd` field, so peers are demuxed by route, and
two point-to-point links put the peer at the **same** route
(`odinlink/README.md:105` "**Single cable only**"). Upstream OdinLink ships the
workaround as module param `max_devices=1` ("use 1 with two cables",
`driver/odl_tb5_params.c:52-60`) — which PR #4's loader does not pass.

**The two-cable topology lives on `main`, in the tbv stack**, and it **dedicates
rather than bonds**: `host/ds4-config.yaml` — *"cables: 1 … 2 dedicates the second NHI
to the RX zero-copy rail"*, with `tbv/bringup/tbv-second-cable-prep.sh`
NM-unmanaging thunderbolt1. One cable still works (TX stays zero-copy).

**PR #4 reports no transport microbenchmarks at all** — its own "Not proven yet"
section says perf vs TCP is unmeasured. Its only number is end-to-end (20–25 tok/s
prose, 35–40 code vs main's 23/32), inseparable from a simultaneous decode-kernel
rewrite.

**Its real value to us:** `odl_ar2.hip` is the *same doorbell all-reduce re-targeted
from verbs onto a non-verbs stream device*, with only **~40 of 441 lines**
transport-specific (`post_round():188` = one `odl_tb5_stream_send`;
`poll_recv():203-220` = one `odl_tb5_stream_recv`). **It is a worked example of
exactly the verbs→stream-device port our `docs/USB4STREAM-TRANSPORT.md` §5.4 scoped
as "2–4 attended days".** Also carries `eager_break_functional_during_capture` +
`VLLM_USE_BREAKABLE_*` — a prerequisite for running **any** custom all-reduce under
PIECEWISE that we had not identified.

**OdinLink itself: reject the driver, steal the evidence.** `Geramy/OdinLink-Five`
is canonical upstream (~7,800-line out-of-tree `odl_tb5.ko`, GPLv2 driver + MIT
userspace) exposing a char-device stream API, an LD_PRELOAD libibverbs shim, and an
`ncclNet_v7` plugin. `wkljohn/ds4-strix-halo-tp-odinlink` is a *consumer*, not a
second transport. Reasons to reject: it **cannot coexist with `thunderbolt_ibverbs`**
(same NHI) — the stack we already have live; GPL-2.0 out-of-tree, vermagic-locked,
Secure-Boot-hostile; single-cable; its plugin advertises `NCCL_PTR_HOST` only (every
collective pays a device↔host staging copy); and it has a documented silent
data-corruption class (`kmalloc(GFP_ATOMIC)` order-8 RX reassembly on a memory-tight
unified-memory host).

**The evidence worth stealing** is a third repo, `wkljohn/llama.cpp-strix-halo-RCCL-RDMA`,
whose `odinlink/` dir is a **29-defect ledger plus measured A/B on exactly our
hardware** (two Ryzen AI MAX+ 395, two USB4 cables, ROCm 7.2.0). Take:
- The **100 µs vs 286 µs** RCCL-net-plugin datapoint (reopens §5.3's stated reason).
- **`odl_rdma_stress.c`** — byte-verifying ibverbs conformance + latency tool; copy
  into `host/rdma/`.
- The **RCCL silent-fallback verification protocol** (`REPRODUCE-RCCL.md` §5) — fold
  into `run-matrix.sh` as a hard precondition on any RDMA arm.
- **BUG 29** — *"a faster wire that costs CPU is a net loss when the bottleneck is the
  host"* (their verbs TX worker busy-spun a core and made llama.cpp TP **slower** than
  TCP). Make **CPU cost a mandatory measurement axis** in the A/B, not just latency
  and bandwidth.

---

## 7. Engram / PLE intel — the cheapest wins we have

**`Baekpica/ds4` `dfm` serves the same artifact we serve.** Verified from their
`ple-manifest.json`: source `Qwen/Qwen3.8-Flash-Next`, revision **`f5d08274`**;
48 layers, n_embd 2560, vocab 248320, 512 experts top-10 + 1 shared, ff_exp 640,
full attention on `il%4==3` (**12 QSA / 36 GDN**), indexer 4 heads ×128 top-k 2048,
hyper-connections 4, ctx 262144, rope base 1e7. PLE table: **51,200,245,760
parameters, 128 shards** named
`model.language_model.layers.1.ple.ple_embedding.ngram_embedding.shard_N.weight` —
**the exact regex our `ple_mmap._SHARD_RE` matches**. They store BF16 (95.37 GiB);
we store FP8 (~47.7 GiB).
*(Correction to our notes: the real checkpoint has **128** ngram shards, not the 512
that `specs/flashnext/evidence/ple-54129.md:153` quotes as `split_ngram_parts`' default.)*

**Their design is the inverse of ours on four counts.** Bounded 2 GiB four-way
set-associative LRU over four **O_DIRECT** sidecar files, explicit 4 KiB `pread`,
32 async page workers, issue-early prefetch, `POSIX_FADV_DONTNEED` after every
non-direct read, and a `cudaHostRegister(Mapped|Portable)` zero-copy gather — **no
staging buffer, no H2D copy** — with row leases released by stream callback and reads
submitted **before token embedding** (`ds4.c:22778-22786`: *"the PLE rows are first
consumed at layer 1, so those GPU stages hide I/O"*).
Ours: OS page cache, pageable `np.empty` staging, effectively-synchronous H2D, and a
**blocking device→host sync of the ids at the PLE layer with no prefetch**.

### 7.1 MEASURED ON THIS BOX

| change | effect |
|---|---|
| **today** | gather reads **937× more bytes** off NVMe than the rows it returns; page cache grows **120×** faster than logical rows |
| **`madvise(MADV_RANDOM)`** — one line | device traffic → **26.0×**, the exact 4096/160 page floor (**36× reduction**); gather **35,819 → 184,207 rows/s (5.1×)**; page-cache growth ÷4.8 |
| decode-shaped (16 rows, cold), today | 0.344 ms mean / **2.350 ms p99**; +59.1 MiB page cache per 0.49 MiB of rows |
| decode-shaped, MADV_RANDOM | 0.271 / 0.529 ms; 12.3 MiB |
| + issue-early prefetch | **0.022 ms mean / 0.065 ms p99** |

`madvise` also makes the existing 32-worker pool **actually scale** — today's
readahead saturates the device and hides the parallelism.

**Their tuning curve is published** (README:235-247): cache size matters more than
worker count; **32 workers is the knee** (2048 MiB × {16,32,64} → 450.19 / 464.67 /
466.88 prefill tok/s). Their bounded 2 GiB working set sustains **277.49 tok/s
prefill over a 262,144-token window** against a 95.37 GiB SSD table, reading only
2.6967 GiB physical.

### 7.2 Take / don't take

**Take (portable, transport-agnostic):**
1. `madvise(MADV_RANDOM)` on every `np.memmap` in `MmapPleTable.__init__` — one line.
2. Pin the staging buffer — replace the per-call pageable `np.empty` in `ple_mmap.py`.
3. Two-phase gather: `mmap.madvise(MADV_WILLNEED, page_start, 4096)` per unique id
   before the copy loop.
4. Issue-early prefetch with real lead time — hash ids at model-forward entry (before
   `embed_tokens`/layer 0), block only at the PLE layer. Their `ds4_ple_hash_rows`
   (`ds4_ple.c:158-210`) is **semantically identical** to our `_hash_ngram_ids`
   (`ple_layer.py:367-437`) — same bigram/trigram construction — so this is a ~35-line
   rewrite, not a re-derivation.
5. Their statistics surface: log2-bucket read-latency histogram with p50/p95/p99 as
   conservative bucket upper bounds, plus hit / inflight-hit / miss / eviction counters
   and logical-vs-physical byte totals. Land **before** the bench matrix runs.
6. Their **A/B knob discipline**: every optimization ships with a named env kill
   switch restoring the previous path bit-for-bit.
7. Set `VLLM_PLE_MMAP_WORKERS` / `_CHUNK` explicitly instead of inheriting defaults.

**Don't take:** the full O_DIRECT sidecar repack (forfeits mmap's free page-cache hit
rate; needs a new artifact pipeline; their loader hardcodes BF16 aggregate sizes and
a reference sha256, so it would refuse our FP8 table). "Batch two-bank PLE gathers"
(+0.64%; vLLM already batches across sequences and `np.unique` dedups further).
`VLLM_PLE_MMAP_PREWARM` (**actively harmful after MADV_RANDOM** — it refills with
sequential readahead exactly what MADV_RANDOM stops). "Split asymmetric PLE Q8
projection" (+8.78% prefill, bit-identical logits — but targets a hand-written CUDA
pair kernel we do not have). Their gather is **CUDA-only**; there is no HIP variant,
so the zero-copy half is a port, not a build flag.

**`giannisan/GLM-5.2-ds4-gguf` is not the mechanism you want.** It streams routed MoE
**experts** (177 GiB IQ2_XXS) per token via io_uring + O_DIRECT with an LFU host
cache, at **0.40 tok/s on a 16 GB consumer GPU**. Different problem. File the
io_uring + O_DIRECT + LFU + cross-layer-router-prefetch pattern for a future
expert-offload question; it is not tonight's engram answer.

**Bonus:** `Baekpica/ds4` **`origin/main`** (not `dfm`) carries `ds4_tp.c/h` — a
two-rank TP transport doing **RDMA-over-Thunderbolt SEND/RECV on a registered slab
with TCP fallback** — plus a `rocm/` backend whose Makefile defaults
`ROCM_ARCH ?= gfx1151` (`make strix-halo`). Unexamined; worth a look.

---

## 8. MTP — settled

**The draft head is already in our staged checkpoint.** Verified byte-exactly from
the safetensors headers of all 28 mtp-bearing shards in
`/var/lib/local-models/flashnext-fp8`: **3,101 `mtp.*` tensors, exactly
2,698,026,496 B = 2.5127 GiB** (2,516,582,400 F8_E4M3 + 181,444,096 BF16) across 28
of 131 shards. `config.json` carries the wiring the fork reads
(`text_config.mtp = {hybrid:true, layer_types:["full_attention"],
mtp_use_hidden_state_from_layer:null, num_hidden_layers:1, rope_theta:1e7}`, consumed
at `vllm/config/speculative.py:826-840`). The draft's `embed_tokens` and `lm_head`
load from the target checkpoint and every tensor the remap needs exists.
**Incremental cost ~2.53 GiB/rank. Nothing to fetch.**

**Rejects.** `agentionai/…-MTP-Q8_0-GGUF` is a third-party **Q8_0 re-quant of the
same official head we already hold losslessly in fp8** (its own card says so), and
GGUF is llama.cpp-family — vLLM cannot consume it. The official Qwen repo ships **no**
separate MTP artifact ("MTP: 1 layer, trained with multi-steps"). ds4-vllm's MTP is
**DSpark (DeepSeek-V4)**, a different drafter for a different engine, on the unmerged
`origin/piecewise-cuda-graphs` branch — patterns only, not code.

**Four things the prior dive missed:**

1. **Wrong proposer audited.** Our default runner is **V2**
   (`ROCM_DEFAULT_MRV1_ARCHITECTURES` = `{DeepseekV32ForCausalLM, DeepseekV4ForCausalLM}`
   — Qwen is not in it), so the **generic `MTPSpeculator`** runs, not the
   `Qwen4ExpMTPProposer` that `DECISIONS §4.2` audited. *(The hc_mult 4× feedback
   widening — the most likely shape-crash — is implemented on **both** paths, so that
   is not the V2 risk.)* **Bank the runner-selection log line into the bench.**
2. **The most likely spec-on boot failure, with a one-line fix.** Spec-on at n=3 needs
   ~4× the GDN state slots — **~+5.4 GiB/rank at `--max-num-seqs 32`** — inside a
   `--kv-cache-memory-bytes` pool **hard-pinned at 12 GiB identically in both arms**
   (`abstract.py:81-85` sets `num_speculative_blocks = num_speculative_tokens`;
   `kv_cache_interface.py:891`). **Fix before the run:** pin `FN_MAX_SEQS=8` in both
   arms, or raise `FN_KV_CACHE_BYTES` for the spec-on arm.
3. **Acceptance telemetry is NOT greenfield.** `handoff/DAYRUN-NOTES.md:141-142` says
   it is — true of ds4's private `DS4_MTP_STATS`, **false of upstream vLLM**, which
   already logs "Mean acceptance length" and per-position rates every 10 s
   (`v1/spec_decode/metrics.py:82-137`, `metrics/loggers.py:315`,
   `VLLM_LOG_STATS_INTERVAL=10`). Our existing grep catches it.
4. **A flashnext-specific argument FOR MTP that nobody wrote down.** The MTP draft
   head **forces PLE off** (`amd/mtp.py:5-7`; zero `mtp.*ple*` tensors), so draft steps
   never touch the mmap'd table. Combined with our blocking D2H in the gather,
   **spec-on divides both the PLE blocking syncs and the NVMe engram traffic per
   *emitted* token by the mean acceptance length.**

**Also fix:** `run-matrix.sh:254/263/269` passes the **arm label** into
`--spec-label`, so the `spec_config` CSV column duplicates `arm` and never records
`n` or the speculative JSON. Pass the real config.

**Verdict: MTP stays IN — as a cp-bench arm only.** spec-off remains the cp-tp2
first-light baseline. Land the three cheap fixes above beforehand.

**Steal (harness-side, read-only):** Baekpica's **accept guard** — after 256 drafted
tokens, cumulative acceptance below **0.15** terminally disables MTP process-wide with
a loud line (`ds4.c:32234-32235`, trip site `:58298-58316`). Re-implement as a
**tripwire in `run-matrix.sh` Phase D**, not as an engine change. And heed ds4's
`DS4_MTP_MAXSEQS` lesson: above their cap the drafter **silently** stops speculating
and acceptance reads 1.00 — **any concurrency bail-out we add must be LOUD**.

**Caveat on the identity oracle:** temperature-0 spec output may differ from plain
decode for **numerical, not logical** reasons — the target's argmax in the width-(n+1)
verify pass reduces in a different order than the width-1 spec-off pass, and near-ties
can flip. Do not treat byte-divergence as automatic proof of a bug.

---

## 9. Do not re-litigate these

- **OdinLink driver / RCCL net plugin as our fabric** — costs us the patched stack we
  already have live; GPL out-of-tree; single-cable; `NCCL_PTR_HOST` staging.
- **`tbv_ar` v1** — inert in its author's own deployment; blocks the HIP stream in a
  host callback (~228 µs/op, ~150 µs of it dispatch + stall).
- **`odl_max_devices=1` / two-cable bonding for the collective** — cable B cannot be
  bonded, teamed, or multipathed. Its only proven role is a **dedicated RX zero-copy
  rail** in the tbv stack.
- **`odl_mq.py`** (control plane off TCP) — we already avoid that problem with a third
  wire.
- **strix-rdma's kernel backports** to 7.1.5 — we have USB4STREAM in-tree.
- **Replacing `bench/usb4stream-bench.py`** with their pingpong — theirs hardcodes
  `/dev/tbstream0`; ours handles our asymmetric numbering and peerless cable B, and
  has the idempotence guard and typed skip paths.
- **Baekpica's mixed-quant recipe** — solves "fit 180B in one 128 GB box". We have two
  boxes and ~61 GiB/rank; block-FP8 is also bandwidth-ideal on gfx1151. Adopting it
  trades FP8 for 5.17 bpw K-quants: a quality regression bought for memory we already
  have.
- **`agentionai` MTP GGUF** — see §8.
- **The ncclNet plugin route** — stays rejected, but **rewrite the reason** (§4.6).
- **strix-rdma's pipeline-parallel negative result** — real, but about **pipeline**
  parallelism (one boundary tensor per layer-group, compute-dominated). Says nothing
  about **tensor** parallelism.

---

## 10. The plan

### 10.0 Machine preparation — pure engineering, no harness dependency

These are physical/product preconditions. They are true regardless of how the run is
orchestrated, and every one of them is verifiable by a shell command.

1. **Stop `llama-swap` on both nodes** (`systemctl stop llama-swap.service` on
   coordinator and worker) and remove any stale `fnproxy-dbg` container. Both are
   `active` right now and hold GPU.
2. **Verify pair reachability, and re-verify immediately before serving:**
   `ping -c3 10.99.0.2 && ssh 10.99.9.2 ping -c3 10.99.0.1`. Losing this at 13:57Z is
   what ended the previous run.
3. **Ship `flashnext:dev` to the worker** (`podman save … | ssh 10.99.9.2 podman load`,
   or `host/fn-image-ship.sh`).
4. **Pin and pre-warm the compile caches on BOTH nodes.** Set
   `TRITON_CACHE_DIR` / `TORCHINDUCTOR_CACHE_DIR` to `$FN_STATE_DIR/{triton,torchinductor}`
   on the proxy/smoke/make-proxy paths and in `container/Containerfile` — currently
   pinned only on the TP=2 path, and both dirs are empty. Unpinned means a tmpfs
   default and a ~25-minute recompile per boot.
5. **Reconcile the weights contract with the deployed catalog prune.** The prune strips
   files `scripts/stage-weights.sh` rsyncs back and byte-counts (README.md, LICENSE,
   .gitattributes), so weight validation is not idempotent as written. Either re-stamp
   `results/receipts/weights-*.json` or stop counting the stripped files.
6. **Settle the NAS library path.** `docs/DECISIONS-2026-08-30.md` step 0 orders a
   rename to `flashnext-fp8`; `scripts/stage-weights.sh` hardcodes a path. Pick one.
7. **Land the §4 record corrections** — minutes of prose, and each one currently
   steers the work wrong.
8. **Do NOT re-run `host/rdma/fetch-and-build.sh` expecting it to help tonight.** Its
   staged modules are vermagic-dead (7.1.4 / 7.2.0 vs booted 7.2.2), and loading a
   rebuilt set means a thunderbolt stack reload — see §6.5 and §10.3. Build it if you
   want it ready for an attended window; do not load it inside the run.

### 10.1 Track A — get engine proof at all (the night's actual objective)

`cp-tp2 → cp-bench → cp-close` on the **stock RCCL/sockets path**, unperturbed.
This is the baseline and the R4 evidence. Do not introduce transport changes into it.

Cheap product landings that ride along, none of which need the harness:
- **`madvise(MADV_RANDOM)`** — one line, 5.1× gather throughput, measured. (§7.1)
- **Pin the ple_mmap staging buffer.** (§7.2)
- **PLE counters** — land before the bench so the matrix records them. (§7.2)
- **MTP KV headroom fix** (`FN_MAX_SEQS=8` both arms) and the **`--spec-label`** fix. (§8)
- **All-reduce timer** as a fourth `fn_*` instrument wrapping
  `CudaCommunicator.all_reduce` — without it, A/B criterion 2 ("the bench shows decode
  is allreduce-dominated") **cannot be evaluated by any artifact in this repo**.
- Consider pinning `VLLM_USE_RAY_V2_EXECUTOR_BACKEND=0` for first light, then flipping
  deliberately once TP=2 is proven.

### 10.2 Track B — the transport work, as a bench arm, never as first light

- **Port `tbv_ar2`** behind `FN_TBV_AR2=1` onto the verbs stack already live on both
  twins: lift `tbv_ar2.hip` + `tbv_ar2.py`, build with
  `hipcc --offload-arch=gfx1151 … -libverbs -lpthread`, and prepend the 51-line hook
  to `CudaCommunicator.all_reduce`. Apache-2.0. (§6.1)
- **Add a third A/B arm** (verbs + `FN_TBV_AR2=1`) to `host/rdma/ab-protocol.md`, and
  keep every existing safety rule: no verbs-arm retry, byte-identical socket restore
  on both ranks, `enp191s0` as the only terminal fallback rung. Note
  `fn-env.sh:152` sets `NCCL_IB_DISABLE=1` unconditionally, so the verbs arms need a
  profile that overrides it — and heed ds4's warning that a bare `export VAR=` after
  the source clobbers values injected via `podman exec --env`. (§6.1)
- **Add CPU cost as a measurement axis** (BUG 29). (§6.4)
- **Fold in the RCCL silent-fallback verification protocol** as a hard precondition on
  any RDMA arm — otherwise a silently-fallen-back arm reads as a null result. (§6.4)
- **Try the NHI IRQ-moderation fix** (`nhi_throttle ns=8000`) — cheap, standalone,
  transport-independent. (§6.1)
- Expect **+3.4%**, not a transformation, and design the experiment to detect a small
  effect honestly. (§6.1)

### 10.3 Staged, not armed

**strix-rdma.** Best numbers on the table (§6.3), applies to our kernel and our tree,
and we already stage a matched `thunderbolt_stream.ko`. But it has never been run with
vLLM/RCCL, it is single-cable, and its licence needs resolving. **Fourth arm for the
next run** — do not introduce it on a night when TP=2 has never booted once.

Also staged: `odl_ar2.hip` as the reference for the eventual stream-device port, its
`eager_break_functional_during_capture` prerequisite for custom all-reduce under
PIECEWISE, `odl_rdma_stress.c` into `host/rdma/`, and strix-rdma's
`docs/TP_TRANSPORT_CONTRACT.md` (17 numbered rules) as design input.

---

## 11. Local clones and paths

| path | what |
|---|---|
| `~/Downloads/ds4-vllm` | AlexKGwyn/ds4-vllm. `origin/main` @ a8f620d = tbv stack. `origin/piecewise-cuda-graphs` = DSpark MTP. `origin/odinlink-stock-perf` = PR #4 (**fetch it — not in the original clone**) |
| `~/Downloads/baekpica-ds4` | Baekpica/ds4, default `dfm`. Also `origin/main` (has `ds4_tp.c` + `rocm/`), `origin/feature/qwen38-ssd-ple-handoff` |
| `~/Downloads/strix-rdma` | jyatesdotdev/strix-rdma (clone if absent) |
| `~/Downloads/odinlink-five`, `~/Downloads/odinlink-strix-halo-tp`, `~/Downloads/wkljohn-rccl-rdma` | OdinLink family + the measurement corpus |
| `~/.cache/flashnext-rdma-build/src/westeri-thunderbolt` | our patched westeri tree (strix-rdma's series applies here, 3 trivial conflicts) |
| `/var/lib/local-models/flashnext-fp8` | staged checkpoint, 185,563,854,698 B, byte-exact both nodes |
| `~/.local/state/tally` | all tally run state — receipts, captures, unit-exits, registry |

## 12. Uncertainties worth carrying

- Whether the tally.nix fix batch gets merged and rebuilt before arming, and whether
  that changes the wedge behaviour (it does not — the fold bug is at HEAD too).
- Whether `tbv_ar2` binds cleanly against **our** rdma-core-usb4 userspace, which we
  built independently of ds4's image.
- Whether a custom all-reduce survives our **PIECEWISE** compile without the
  `eager_break_functional_during_capture` patch. Unknown; assume it does not.
- Whether the V2 generic `MTPSpeculator` behaves on `qwen4exp` — never exercised.
- Whether spec-on's temperature-0 divergence is numerical or a real defect (§8).
- The strix-rdma licence contradiction (GPL-2.0 repo, per-file MIT userspace).


---

## 13. ADVERSARIAL SWEEP RESULTS (2026-08-31, 2 opus agents) — READ THIS BEFORE §10

Two agents were tasked to refute the rejections and to read the ground nobody read.
**Two verdicts broke, one plan-killing bug was caught, and one root cause was found on
Track A's own baseline path.** Everything here supersedes the corresponding text above.

### 13.1 PLAN-KILLING: the Track B arm would have measured nothing

**Settled from our own fork's source:** under PIECEWISE, `vllm::all_reduce` is **NOT a
splitting op** — `splitting_ops` is `_attention_ops` only. So the all-reduce sits
**INSIDE a captured piece** for every cudagraph-captured decode size. Both reference
implementations gate eligibility on `not torch.cuda.is_current_stream_capturing()`
(`tbv_ar.py:167`, `tbv_ar2.py:44`), so a port of either is **SILENTLY INERT on the
decode path** and RCCL is baked into every replay.

ds4 hit this exact wall and wrote `eager_break_functional_during_capture` — whose
docstring **names tbv_ar2 by name**. But that fix needs `VLLM_USE_BREAKABLE_CUDAGRAPH=1`,
which our fork implements by forcing `compilation_config.mode = CompilationMode.NONE`
(`vllm/config/vllm.py:703`) — which our own PLE-mmap guard `check_cudagraph_safety`
(`ple_mmap.py:624-637`, clauses 2 and 3) then **rejects**. The ds4 escape hatch is
closed to us.

> **THE ONE LEGAL ESCAPE:** run the Track B arm with **`-cc.cudagraph_mode=NONE`,
> leaving compilation mode at `VLLM_COMPILE`.** All three `check_cudagraph_safety`
> clauses pass, nothing is captured, and the custom all-reduce fires every step.
> **Without this flag the arm reports the stock RCCL number under a Track B label.**

Consequence: bank a **stock-RCCL-on-NONE control arm** so the A/B is like-for-like. A
stock-RCCL PIECEWISE-vs-NONE pair is worth having anyway — nobody has priced what the
cudagraph is worth on this model.

### 13.2 The transport baseline we have been quoting is the wrong shape

Measured on this pair tonight (`scratchpad/pp.py`, TCP_NODELAY, 400 iters/size):

| shape | 4 KiB | 8–16 KiB | 64 KiB |
|---|---|---|---|
| ping-pong (what §6.2 quotes) | 130 µs | 310–315 µs | — |
| **full-duplex simultaneous exchange — what an all-reduce actually is** | **~118–133 µs p50, FLAT from 4 KiB to 64 KiB** | | |

**Use the exchange number.** It reprices the whole transport effort:

- `tbv_ar2` on verbs at ~105 µs is worth **~1.2×**. Barely anything.
- Stock `/dev/tbstream` at ~22.9 µs RTT @4 KiB (third-party, same driver family) is
  worth **4–5×**.

**Track B is aimed at the right target. The verbs family is aimed at a target that is
barely there.**

Also: **the `+3.4% for a 13× wire improvement` calibration is not our number** — it is
imported from a different rig, engine, and collective count, and is internally
inconsistent with its own figures (96 × 286 µs = 27.5 ms of a 120.6 ms step is 23%; a
13× cut to that cannot yield 3.4%). **Do not use it to price the work.** The all-reduce
timer instrument settles it; treat that instrument as the decision input, not as
telemetry.

### 13.3 OVERTURNED — R12: the verbs path is NOT reload-gated

`thunderbolt_ibverbs` needs **zero patched-core symbols or struct fields**:

- Its 26 thunderbolt-core imports (`tb_ring_alloc_tx`, `tb_xdomain_*`,
  `tb_register_protocol_handler`, …) **all resolve against the running stock 7.2.2
  module** — checked symbol-by-symbol against `/proc/kallsyms`. `tb_ring_flush` and
  everything else the westeri series adds is **absent from the import list**.
- The series adds exactly three things (`callback_xd`/`owner` on
  `struct tb_protocol_handler`, `debugfs_dir` on `struct tb_nhi`, eleven `debug_*`
  counters on `struct tb_ring`). **The module reads none of them.**
- It carries compile-time fallbacks: `kernel/native_control_xdomain.c:12` wraps the
  source-aware handler in `#ifdef TB_PROTOCOL_HANDLER_HAS_XDOMAIN` with an `#else`,
  and `kernel/native_control_legacy.c` is a complete source-blind handler built into
  every configuration. **The only capability lost against stock headers is multi-cable
  native rails** — which we are not using.
- **ds4's own bringup script never touches the core.** `tbv/bringup/tbv-reload-roce.sh`
  *requires* `thunderbolt_net` to already be up, rmmods only `thunderbolt_ibverbs`,
  then `modprobe -a configfs ib_core ib_uverbs` and a single `insmod`. The core swap
  lives in a different script (`tbv/install-modules.sh`), a boot-time blacklist install.

**Where our false belief came from:** `host/rdma/fetch-and-build.sh` overlays the
*patched* `include/linux/thunderbolt.h` into the build farm and then **hard-fails
unless the .ko contains the string "source-aware XDomain handler"**. That gate is our
build script's choice, not the module's requirement.

**The other half of R12 also breaks:** `thunderbolt_stream` has refcount 6 and **zero
module holders**, while `thunderbolt` (refcount 9) is held by `thunderbolt_net`,
`thunderbolt_stream` and `typec`. Swapping `thunderbolt_stream` **cannot disturb
`thunderbolt_net`** — rail 0 is not on that teardown path.

**Prerequisite, priced:** no `/lib/modules/7.2.2/build` on this box, but
`linux-7.2.2-dev` is **substitutable from cache.nixos.org at 717 MB — a download, not
a compile.** `ib_core` is already loaded; `ib_uverbs` is not.

**But see §13.2:** R12 is now *possible*, and simultaneously *not worth tonight's
hours*. **Verdict: technically available, low value. Spend the same kernel-dev
prerequisite on §13.4 instead.**

### 13.4 NEW ROOT CAUSE, on Track A's baseline: `TBNET_THROTTLING 128000`

Our "the fast wire is 2× worse than the 5 GbE fallback" conclusion is **an artifact of
one compile-time integer.**

`drivers/net/thunderbolt/main.c:37` — `#define TBNET_THROTTLING 128000` (ns) —
programmed onto **both** TX and RX NHI rings at `:960-961`. Measured signature
confirms it: thunderbolt0 RTT is a **hard, hardware-quantized 130 µs floor at ALL sizes
from 64 bytes up** (p10 129.2 / p50 130.3 / p99 139.1 over 350 samples at 64 B),
stepping to ~300 µs at ≥4 KiB.

At ~96 chained all-reduces per decode step: a 130 µs floor costs **12.5 ms/step**; the
≥4 KiB plateau costs **28.8 ms/step**.

**Fix is a ~70-line ITR module** (ds4's `nhi_throttle.c` pattern, `ns=2048`) — **no
unload of anything**. This is the highest-value hour available, it is on **Track A's own
baseline path**, and it must run **BEFORE first light** so the baseline is not measured
on a crippled wire. If the floor drops, the "enp191s0 is our better wire" conclusion has
to be retaken before the bench matrix.

**Corollary — the NHI throttle lever is a NO-OP for Track B.** USB4STREAM already runs
at **2048 ns** on this pair (live configfs on both nodes:
`/sys/kernel/config/thunderbolt/stream/*/fn*/throttling` = 2048, vs the in-tree default
`TBSTREAM_DEV_THROTTLING 8192` and ds4's "tuned" 8000). §6.1/§10.2 file this lever as
transport-independent; **it is not — its entire value is on the tbnet/RCCL path.**

### 13.5 OVERTURNED (in part) — R3: cable B is live and unclaimed

"No bonding, teaming or multipath for the collective" **stands** — nothing was found
against it. What breaks is "cable B's only proven role is a dedicated RX zero-copy rail".

Cable B is a **fully live, peered, 2×20 Gb/s link on its own NHI**, pinging the worker
at 0.107 ms, with an **unclaimed stream-device pair and an unclaimed IP interface**,
blocked only by a missing static /30 and a firewall trust entry.

**CRITICAL DEVICE-MAPPING CORRECTION** (from `readlink -f
/sys/class/net/thunderboltN/device` on both nodes, cross-referenced with configfs
indices):

| | coordinator | worker |
|---|---|---|
| **cable A (rail 0, the serving wire)** | `/dev/tbstream2` | `/dev/tbstream2` |
| **cable B (unclaimed)** | `/dev/tbstream0` | `/dev/tbstream0` |

**Symmetric on both nodes.** `bench/usb4stream-bench.py`'s docstring claims asymmetric
numbering and a *peerless* cable B — **both stale**. Worse,
`resolve_stream_device()` (`:275`) anchors on the netdev holding the /30, so it
**always yields the cable-A device and therefore always collides with rail 0** — which
is precisely why that file's dominant documented hazard is "a wedge here would take the
pair down."

**This is the finding that makes Track B safe.** Point it at `/dev/tbstream0` and the
doorbell all-reduce and the stream bench run on a cable sharing **no NHI, no rings, no
IRQ vectors** with the serving pair. The stream measurement becomes schedulable *while
serving*, instead of only after everything stops.

Second free win: give thunderbolt1 a static /30 + firewall trust and pin
`NCCL_SOCKET_IFNAME=thunderbolt0`. Image ship goes to **1134 vs 589 MB/s** (measured)
and Ray traffic stops confounding the collective wire.

### 13.6 THE STAGING TRAP — two rules the port must be written with, first time

The **only production gfx1151 two-node TP deployment** (`ds4-strix-halo-tp-odinlink`)
measured **RDMA vs TCP at PARITY** (8.29 vs 8.57 tok/s) and root-caused it: **64% of
big-gate time was a CPU memcpy out of `hipMalloc`'d memory running at ~200 MB/s**,
because that mapping is **write-combining** on this UMA APU. *The wire was never the
limiter.*

1. **Never CPU-memcpy out of a `hipMalloc`'d tensor.** Stage via torch `.copy_()` into
   pinned memory, or write the payload directly into `hipHostMalloc`'d memory.
   (`tbv_ar.py` already does the former; ds4's C engine did not, and paid for it.)
2. **Back off the progress thread on a miss STREAK, not a flag value** (~`1<<8`, with a
   yield), and **hoist `getenv` off the gate path** — worth ~25% of decode where it was
   measured.

### 13.7 The all-reduce instrument must answer the right question

It must log: **(a)** a fired/declined counter for the custom path, **(b)**
`torch.cuda.is_current_stream_capturing()` at the call site, and **(c)** staging time
split from wire time.

Without (a) and (b), **"inert" and "no speedup" are indistinguishable** — the exact
failure §13.1 describes. Without (c), the write-combining staging trap of §13.6 is
invisible.

### 13.8 Dead ground — closed, do not reopen

- **Baekpica `origin/main` (`ds4_tp.c` + `rocm/`)** — my biggest flagged gap, closed
  **negatively**: its RDMA path is `#if defined(__APPLE__)`-gated, its ROCm gate encoder
  is a literal stub printing *"tensor parallelism is Metal-only"*, and
  `ds4_tp_validate_engine_options` hard-rejects any non-Metal backend. **Not a third
  reference implementation.** Its three remaining branches are strict ancestors of
  `dfm` with zero new commits.
- **ds4-vllm `piecewise-cuda-graphs` beyond its one all-reduce hunk** — DeepSeek-V4/MLA
  specific fusion; does not transfer to QSA+GDN.

**The highest-density unread material left on this machine** is
`/home/tom/Downloads/ds4-strix-halo-tp-odinlink/docs/` — `BIG-GATE-BOTTLENECK`,
`PATCH17-SPIN`, `GATE-DEADLOCK`, `ODINLINK-CQ-OVERFLOW`, `WHY-VLLM-PREFILL-IS-6X`. It
is the real gfx1151 ROCm TP port and it supplied §13.6.

### 13.9 Upheld under attack

R1 (OdinLink driver), R2 (tbv_ar v1), **R4 (block-FP8 — stands)**, R5 (O_DIRECT repack),
R6 (PREWARM), R7 (two-bank gathers), R8 (agentionai GGUF), R9 (backports / bench
replacement), R10 (ncclNet plugin), R11 (**MTP stays off first light**), R13 — several
now on arithmetic or read-source grounds rather than categorical ones.

**One cheap take R5's rejection was hiding:** bounding the page cache and repacking the
artifact are **separable**. A periodic `fadvise(DONTNEED)` bound on the mmap — a few
lines on top of the planned `MADV_RANDOM`, behind the same named env kill switch —
keeps the free page-cache hit rate R5 correctly refuses to forfeit while closing the
growth hazard (59.1 MiB per 0.49 MiB of rows today; 12.3 with MADV_RANDOM alone).

### 13.10 Not closed

- **Never verified that `thunderbolt_ibverbs` actually compiles against stock 7.2.2
  headers** — no dev tree on the box to try it.
- **The 128 µs ITR value was never read back from hardware** — `mmap` of `resource0`
  returns EINVAL and `/dev/mem` is blocked by lockdown. The diagnosis rests on driver
  source plus the timing signature: strong, but inference.
- **`/dev/tbstream*` was NOT opened.** Deliberately. The safe procedure now runs on
  cable B (§13.5), which is what makes it schedulable at all.

### 13.11 Revised order for tonight's hours

1. **Fetch `linux-7.2.2-dev`** from cache.nixos.org (717 MB, substitutable). Commits to
   nothing, unblocks everything.
2. **Build + insmod the ~70-line NHI ITR module** (`ns=2048`) on both nodes; re-run the
   RTT sweep (`scratchpad/pp.py`). **Before first light.** No unload required. (§13.4)
3. **Static /30 + firewall trust on thunderbolt1; pin `NCCL_SOCKET_IFNAME=thunderbolt0`.**
   (§13.5)
4. **Re-point Track B at cable B** (`/dev/tbstream0` both nodes) and add a cable-B mode
   to `resolve_stream_device()`; fix the two stale docstring premises. (§13.5)
5. **Add `-cc.cudagraph_mode=NONE` to the Track B arm** + a stock-RCCL-on-NONE control.
   (§13.1)
6. **Write the port with the two staging rules** from §13.6, first time.
7. **Instrument per §13.7** before anything is judged.

Unchanged: Track A is primary and nothing here perturbs it. MTP off first light.
block-FP8 stays. No bonding, no OdinLink driver, no ncclNet plugin, no O_DIRECT repack,
no PREWARM.


---

## 14. ds4-strix-halo-tp-odinlink — the only production TP=2 on our silicon

Source: `/home/tom/Downloads/ds4-strix-halo-tp-odinlink` (`$R`). Full deep-read: 48 docs,
9 branches, `ds4_tp.c/h`, `ds4_rocm.cu`, `patches/`, `scripts/`, `tests/`, `deploy/`.
A C/HIP ds4-family engine — **not vLLM** — but it solved our transport and collective
problems on our exact chip and wrote down what went wrong.

**ROCm — SETTLED BY DIRECT QUERY OF THE RUNNING IMAGE, 2026-08-31.** Do not re-litigate;
this was got wrong twice tonight, in both directions.

```
torch                     2.13.0+rocm7.14.0      torch.version.hip = 7.14.60850
triton                    3.8.0+git4cff872c.rocm7.14.0
rocm-sdk-core / devel     7.14.0
rocm-sdk-device-gfx1151   7.14.0
vllm                      0.1.dev1+gbdb6f0420.rocm714
```
(`podman run --rm flashnext:dev`; `results/receipts/build.json` agrees.)

**The serving substrate is ROCm 7.14.0.** It is NOT ROCm 10, and it is not 7.2 —
7.2.2 is the *kernel*. The host nix store separately carries TheRock
`rocm_sdk_libraries-7.15.0a`, which is not what the container serves.

**ROCm 10 is a real, aligned, unrun option, not our basis.** A complete gfx1151/cp312
set exists at `stable.repo.amd.com/rocm/whl-next/` (verified 2026-08-30, wheels dated
08-26) carrying **the same triton git hash `4cff872c`** as our pins, so the swap is a
pure version-literal substitution through the Containerfile's existing build ARGs
(`container/Containerfile:48-65`) with **no recipe edit**. But `rocm10-probe` is one of
the five tasks that never ran, `README.md:312-314` records that **nobody anywhere has
run vLLM on ROCm 10 on gfx1151** (zero rocm10 tags in kyuz0's auto-discovery), and two
failure modes are unmeasured: whether ROCm 10's hipcc compiles the fork's HIP sources,
and whether its HSA runtime binds the in-tree KFD on this kernel. That is why it was
scoped as a **separate image tag off the critical path**.

**OPERATOR DECISION, 2026-08-31: NO ROCm 10 TONIGHT.** The bump happens after flashnext
is working, even if it is working on a sub-optimal mechanism. Do not build
`flashnext:rocm10`, do not pass the whl-next build ARGs, and do not treat `rocm10-probe`
as in scope. Reason: it is a substrate swap with two unmeasured failure modes (hipcc
compiling the fork's HIP sources; the HSA runtime binding the in-tree KFD on this
kernel) on a night whose primary objective — TP=2 first light — has never succeeded
once. The wheel set is aligned and will keep. Revisit as a graded day-2 migration on a
separate image tag, off the critical path, exactly as `README.md:299` already scopes it.

Consequences: we are **at or above ds4's build-time floor of ≥7.14.0**
(`check-rocm-strix`, commit 3633bd5), so their entire 19–20 t/s result set transfers —
**and the ROCm 7.14 overlapping-host-registration defect in §14.7 is LIVE for our
engram mmap**, not hypothetical. Host requirements are also already met here:
`amd_iommu=off` ✓ and `128087M of GTT memory ready` ✓ (achieved via
`ttm.pages_limit=33554432`, with `amdgpu.gttsize` unset, rather than their documented
`gttsize=126976`).

### 14.1 THE NUMBER THAT REPRICES TRACK B

Their production gate profile — measured with a zero-device-sync host clock, symmetric
on both ranks, 86 gates/token:

| | rank0 | rank1 | share |
|---|---|---|---|
| detect (poll latency) | 12.1 µs | 5.8 µs | ~1% |
| **exchange (full RDMA round trip)** | **111.8 µs** | **132.0 µs** | **~11%** |
| release→arrival (own GPU compute) | 958.4 µs | 937.6 µs | **~88%** |

Self-consistent: ~1069 µs/gate × 86 = 91.9 ms vs measured ~92 ms/token.
**This overturned their own leading hypothesis** — they were certain the 86-gate
lockstep dominated.

Their production RDMA 16 KiB full-duplex exchange is **112–132 µs**; near-side transport
floor ~50–65 µs. **Our TCP-on-thunderbolt0 full-duplex exchange is 118–133 µs. Same
number.** A tuned RDMA stack on the same silicon and cable is at parity with our
sockets.

> **Ceiling for a perfect custom-all-reduce arm: ~70 µs × 96 gates ≈ 6.7 ms/step.**
> Real, worth having, **not what makes TP=2 work.** Their verdict on further transport
> tuning: *"cannot plausibly recover 5% end to end."* **Timebox Track B accordingly.**

Their README now reports **233.04 t/s prefill / 19.17 decode** (OdinLink TB5) and
**275.58 / 20.43** (ConnectX-4 RoCE v2) against an Aug-3 baseline of 34.11 / 9.96.
**None of the 9.96 → 20.43 decode gain came from the wire** — all kernels and protocol
shape.

### 14.2 THE DOORBELL WE PLANNED TO PORT IS THE WRONG SHAPE

**The doorbell has a direction, and only one direction works on gfx1151.**

- **GPU → host** (`hipStreamWriteValue64`): **UNRELIABLE.** Never lands on the null
  stream at all; on a created stream it passes short probes but **loses arrivals in
  sustained decode**. Their comment (`ds4_rocm.cu:486-492`): *"sustained production
  decode has lost stream-write arrivals on either rank."*
- **host → GPU** (`hipStreamWaitValue64` on a CPU-written mapped word): **RELIABLE**,
  releases in 0.000 s.

`hipMallocSignalMemory` does not exist on gfx1151, so `g_tp_host_sync=1` and **the
free-running spin service thread is never created in production**
(`ds4_rocm.cu:1226-1245`). What ships is an **ordered HIP host callback** —
`hipLaunchHostFunc(NULL, cb, req)` on the legacy default stream, so all producer kernels
precede it and all consumers wait behind it. `DS4_TP_HOST_CALLBACK=1` is set by **both**
production launchers.

**The failure has exact coordinates and a runnable reproducer.**
`tests/test_tp_dual_stream_progress.cu`: sustained inference **lost progress at gate
sequences 1306 and 1482** — at 86 gates/token that is decode tokens ~15 and ~17.
Survives a smoke test, dies in production. **The harness hard-refuses iterations < 1307**
so you cannot run it short enough to miss the bug. On timeout the service thread **still
performs the exchange and publishes the release** — "records the failure without leaving
a GPU wait packet or peer QP stranded."

> **DECISION: build the gate as host-callback-ordered with a GPU wait on a host-written
> word. Do NOT ask the GPU to signal the host.** Open question I flagged to the agent
> and it could not close: ds4's evidence is about *stream-memory-op packets*;
> `tbv_ar2.hip` signals with an **in-kernel `__hip_atomic_store`**, a different
> mechanism. Their evidence does not directly indict in-kernel atomics — **but a
> ≥1500-exchange soak is mandatory before trusting either.**

### 14.3 THE UMA MEMORY RULES (measured, and they confirm our staging plan)

`BIG-GATE-BOTTLENECK`: 64% of big-gate time was a **CPU memcpy at ~200 MB/s**, because
CPU **reads** of `hipMalloc` memory on this UMA APU are **write-combining**
(`ds4_tp.h:142-153`). This retroactively explains their RDMA-vs-TCP parity — both
transports shared the same host copy. *The wire was never the limiter.*

| | verdict |
|---|---|
| CPU **read** of `hipMalloc` memory | **TRAP** — WC, ~200 MB/s. Writes are fine. |
| GPU DMA into NIC-registered memory | **TRAP** — not coherent with a third-party device; `hipStreamSynchronize` orders but does not publish |
| `hipHostMalloc(Mapped)` + `hipHostGetDevicePointer` | **SAFE** — host ptr == device ptr, measured on this box. **This is our plan; it is correct.** |
| **Producer writes directly into the pinned/registered region** | **BEST** — worth **2.7×** on their gate (14.4 s → 5.3 s), prefill **+28.5%** |
| forced to read WC memory | non-temporal/streaming SIMD loads — worth **+44.7% prefill** |

**In vLLM terms:** allocate the all-reduce buffer once as pinned+mapped, construct the
torch tensor **over that pointer**, and have the layer write its partial straight into
it rather than allocating a fresh device tensor and copying. Tens of lines. This is the
difference between their 29.29 and 37.64.

### 14.4 OVERLAPPING THE COLLECTIVE WITH COMPUTE — one shape is invalid

**"Overlap with next-layer attention" is RULED OUT, not deprioritized.** The next
layer's attention reads state swapped in only *after* this layer's HC expansion
completes (`ds4.c:29238`), so next-layer Q/K/V transitively depends on this layer's FFN
all-reduce. Starting it early **changes the model's output**. *We have this dependency
too.* (Ladder-Residual needs a different residual **topology**, not a scheduling change.)

**The valid shape** is row-chunk pipelining of the *same* layer's exchange against its
own residual expansion: 128/256-row chunks, queue all chunk exchanges up front without
an immediate wait, then per chunk wait → rank-ordered add → expand. Hazards they list,
all ours: row views must outlive the progress thread's dereference (this is exactly the
use-after-free that caused their row-split crash); never read a peer chunk before its
release word is observed; the rank-ordered add must stay rank0-then-rank1 for **every**
chunk or FP diverges; both ranks must compute identical chunk counts.

**Three correct, well-executed comm/compute overlap implementations on this exact
hardware each returned zero or negative:** producer-ready half-vector chunking (−0.65%;
provider calls 28,036 → 53,750/rank ate the overlap), raising the provider send ceiling
(−14.6%), and an attention row split (noise). Gate cost at prefill is ~39%, but *"an
overlap fix does not recover 39% — only the portion where GPU/CPU sit idle."*

### 14.5 EXPERT SHARDING — read before the first TP=2 launch

Their failure: VRAM 95.5/96 GiB, `arena alloc failed for moe_gate (1152.00 MiB)`, then
"illegal memory access". Root cause with an exact fingerprint: 145.12 GiB routed experts
/ 43 layers / 3 tensors = **1152 MiB = one layer's FULL expert tensor, all 256 experts** —
but the rank holds only its half. **Sharding was implemented as weight MASKING**
(compute over all experts, zero the unowned), which *requires every expert to be
addressable* and therefore contradicts TP mapping only half resident. A whole-layer
range lookup fell through to a from-disk arena **per layer**: +14.4 GiB, **scaling with
layer count**.

**Fix: shard at SELECTION, not after it.** Peak VRAM 95.52 → 85.65 GiB.

Two things they record that we must not repeat:

1. **"The batch path had no sharding at all."** The first fix touched only the
   one-token entry point; the prefill path went unsharded, double-counting experts
   *and* paging in the unowned half — **"prefill would have been silently wrong."**
   **We have separate prefill and decode MoE paths. Check both.**
2. **Selection rebasing:** unowned pairs get index 0 **and weight 0.0**. Unresolved
   interaction they flag: the zero-weight convention vs `norm_topk_prob=true` if the
   router normalises **after** selection. **We are top-10 of 512 with a shared expert —
   verify our router's normalisation point.**

Related correctness bug worth the same weight: a patch folded the routed-MoE addend
*before* a launch on the premise that it "accumulates into out". **The premise was
false** — every terminal write on ROCm is an assignment — so **the shared expert was
silently dropped from every decode layer** and the model still produced fluent text.
Upstream had fail-closed refused the case; the patch *"replaced a refusal with a wrong
answer."*

Also: their network-TP site computes `n_groups/2` **with no parity check** — an odd head
count silently drops a group. **We have 12 QSA + 36 GDN. Assert parity explicitly.**

And do what they did: **predict per-rank residency offline from the checkpoint header
before loading.** They predicted 153.32 GiB total / 80.76 GiB per rank and hit it
exactly. Converts a class of OOM debugging into arithmetic. Assert **distinct per-rank
shard checksums** as a one-line proof the ranks own different halves.

### 14.6 HIP GRAPH CAPTURE — confirms §13.1 from a different direction

Their design verdict (asserted from HIP docs + code reading, never measured; the
`q4k-hipgraph` branch **dead-ends in a revert**): *"HIP graphs can capture HIP operations
associated with streams. **Ordinary host work is not captured.**"* Therefore the only
plausible design is **graph segments between gates** — and **a host callback is host
work and will not be captured, so the collective must sit on a graph BOUNDARY, never
inside a captured region.**

That is independent confirmation of §13.1: under PIECEWISE, `all_reduce` is inside a
captured piece, so the Track B arm needs `-cc.cudagraph_mode=NONE`.

Two runnable probes they wrote and never published results for —
`scripts/hip_graph_default_stream_probe.cu` and `hip_graph_launch_ceiling_probe.cu`
(empty serialized kernels **maximize** what graph replay can remove, so it measures the
**upper bound**). Both compile standalone with hipcc. **~10 minutes to bound what our
cudagraphs are actually worth on gfx1151.**

Warning attached: `DECODE-PROFILER-STALL` — dense `cudaDeviceSynchronize()` makes
`hipStreamWriteValue64` return success **while its packet is never submitted**.
Non-deterministic, either rank. Two research rounds and two hardware experiments never
pinned it down. **UNRESOLVED.**

### 14.7 ROCm 7.14 overlapping host registration — LIVE for our engram path

Commit 8d45d16 (`rocm/ds4_rocm_runtime.cuh:5125-5220`): on 7.14, `hipHostRegister` of a
page range that **overlaps an already-registered range** returns
`hipErrorHostMemoryAlreadyRegistered` / `hipErrorAlreadyMapped` — **and a subsequent
plain `hipMemcpy(H2D)` from that pointer also fails.** Fix: detect those two codes and
route through a transient 64 MiB pinned bounce.

**We mmap ~51.2 GB and register slices of it, and we are on 7.14.** Their
`scripts/rocm_host_copy_probe.cu` is a standalone oracle (with an
`--expect-overlap-rejected` mode encoding both old and new behavior) sweeping
malloc-unregistered / malloc+register / two adjacent page-overlapping ranges /
posix_memalign+register / anonymous mmap+register / `hipHostMalloc(Mapped)`.
**Run it before touching the engram path. ~15 minutes.**

### 14.8 Invariants to code as the progress thread is written

Each is a bug they actually hit.

1. Doorbell write must not be on the null/default stream; verify the signalling
   primitive **in the exact stream configuration you ship**.
2. **Terminal failure releases every waiter.** Latch failure, store `UINT64_MAX` into
   *both* channels' release words, null all callbacks, stop the loop. *"Releasing only
   the failed sequence lets the service thread enter another network wait while both
   GPUs remain parked."* A per-request release must never overwrite the terminal value.
3. **Advance the sequence counter BEFORE the failure check** — an early return leaves
   this rank's counter permanently behind its peer's.
4. **Drain on shutdown:** release everything enqueued *and* push the encoder high-water
   mark, or shutdown deadlocks.
5. **Bound the in-flight window and fail loudly** — `seq - released >= RING` is an
   error, not a silent wrap.
6. **Snapshot raw device pointers at enqueue; never queue descriptor pointers.** Their
   worst bug: the encoder queued tensor *views* that callers freed immediately; the
   progress thread read `->ptr` from freed memory. Two unrelated SIGSEGVs, cost days.
7. **Exact-matched hello, compared bitwise.** Everything that changes schedule,
   arithmetic, or shard boundary lives in one word. Asymmetric launch fails closed
   instead of deadlocking or silently diverging.
8. **Peer-liveness poll inside every wait loop** plus an absolute deadline.

### 14.9 Benchmark methodology — free, and it protects every number we take

- **≥500 generated tokens.** Their 100-token runs had **±12% spread** and *"cannot
  distinguish changes below ~15%."* The "25% spin win" lead is soft for exactly this
  reason (3 runs 11.09/10.84/9.81 against **one** pre-patch sample of 8.57).
- **Never benchmark prefill on a short prompt on an MoE model.** At 6-of-256 a 50-token
  prompt averages 1.17 routed pairs/expert, below the WMMA engagement threshold — their
  "40 vs 90" gap was *"mostly a measurement artifact."* **We are top-10 of 512: worse
  threshold effects than theirs.**
- **Byte-identical A/B is INVALID** on repetitive prompts — FP non-associativity means
  partial-sum arrival order varies run to run and the argmax eventually flips. Use
  token-level **logprob diffs**, never "output looks coherent" (they shipped a silently
  dropped shared expert that produced fluent text).
- **Three-run median with a deterministic fingerprint**, binary SHA-256 pinned and
  verified **on both nodes** before every A/B.
- Occupancy is **not** the binding constraint on gfx1151 MoE tiles — narrower was worse
  (tile4 27.10 @ 75% vs tile8 30.00 @ 37.5%). Go **wider**, not narrower.

### 14.10 `WHY-VLLM-PREFILL-IS-6X` — the direction is the opposite of the title

The 6× is **vLLM being 6.6× FASTER** at prefill. Measured on the same two gfx1151 nodes:
vLLM TP=2 **198.8 t/s prefill / 3.28 decode**; llama.cpp 80–95 / 9.42; ds4 (Aug 5) ~30 /
~10.5. Mechanism: ds4's MoE ran at **1.56% of DP4A peak** — ISA disassembly showed only
**128 of 2686 body instructions (4.8%) do useful arithmetic**, and **zero `ds_read`**
despite 37 KB of LDS. *"vLLM's MoE is a grouped/ragged batched GEMM through tuned
Triton/AITER kernels… that difference is where the 6.6× lives."*

**We inherit that free. Do not write MoE kernels.** But note vLLM decode 3.28 vs their
19–20: **budget for TP=2 decode to be the hard part, not prefill.** (The title is also
obsolete — ds4 main now does 275.58 prefill, faster than the vLLM number in that doc.)

### 14.11 Transferable protocol trick

**`DS4_TP_GREEDY_TOP2`:** instead of shipping a 256 KiB FP32 vocabulary half per token,
send the best two `(id, value)` pairs = **16 bytes**. Two candidates suffice for argmax
and argmax-excluding-one. Negotiated in the hello, auto-disabled for speculative
sessions, **fails closed** for temperature sampling and logprob consumers. Measured
**+1.79%**. Applies to us **only if we row-shard the output head.**

### 14.12 Reusable probes — all standalone, no model needed

`scripts/`: `t0_hipmalloc_host_probe`, `t2_payload_visibility_probe` (20,000 iters, zero
stale reads), `t3_gate_signal_probe` (both directions; prints whether host ptr == device
ptr), `t4_null_stream_gate_probe`, `t5_gate_stream_fix_probe`, `t6_bandwidth_probe`
(223.9 GiB/s = 94% of peak), **`rocm_host_copy_probe.cu`**, `hip_host_callback_gate_probe`,
`hip_graph_{default_stream,launch_ceiling}_probe`.
`tests/`: **`test_tp_dual_stream_progress.cu`** (two-node, refuses <1307 iterations,
watchdog publishes release on timeout), `test_tp_completion_ordering.cu`,
`test_tp_big_gate_overlap.cu` — whose acceptance bar is worth stealing verbatim:
*"a candidate may move into the model only when this test is BIT-EXACT and hides AT
LEAST HALF of the measured wire time without transport fallback."*

### 14.13 Operational rules that are ours regardless of transport

- **Never SSH over the RDMA/data address.** They lost management access during recovery
  because SSH rode the data wire. *(We are fine — enp191s0 is dedicated. Keep it that
  way.)*
- **Do not `rmmod` or PCI-unbind as a link reset.** A Thunderbolt unbind can block
  inside the kernel and make SSH unresponsive **even over an independent network**.
  Their node-2 kernel panic came from unloading the module while TX completions were
  queued. Recovery was **reboot-only**.
- **Do not kill a rank while its GPU is waiting at a gate.** Allow the terminal drain;
  verify zero GPU activity and VRAM before changing anything. *(Directly relevant to how
  we stop bench arms.)*
- **Start the worker first**, launch both model loads concurrently; separate the connect
  timeout (1800 s) from the gate timeout (300 s) so peer discovery is never confused
  with failure detection.
- **Two nodes have independent filesystems.** Their hardest RDMA bug was simply a
  missing `.so` on the peer, presenting as a generic "no active device."
- **Never trust an advertised queue depth.** OdinLink advertises CQ 512, holds **63**,
  and **silently drops** on overflow. Treat a stalled completion loop as "a completion
  was dropped" before anything else.
- **Clear latched HIP errors immediately** after any probing allocation — a failed
  `hipExtMallocWithFlags` latches and the next unrelated `cudaGetLastError()` reports a
  spurious "invalid argument".

### 14.14 THE META-LESSON

> **Every optimization they argued for from reading code lost. Every one they measured
> first won.** Ledger items 21, 22 and 23 are three correct, well-executed
> communication/compute overlap implementations on this exact hardware that each
> returned zero or negative. **Our custom-all-reduce arm is in the same family.**

### 14.15 REVISED ORDER — supersedes §13.11

1. **Measure the collective's split on the STOCK RCCL/TCP arm first** (~30 min): three
   `perf_counter` reads per collective — detect / exchange / compute-between — bucketed
   by layer type. **Their equivalent overturned their own leading hypothesis.** If our
   split looks like theirs, Track B is worth far less than we think, and we learn that
   before spending the hours.
2. **Run `rocm_host_copy_probe.cu`** before touching the engram path (~15 min). §14.7.
3. **Expert-shard rules before the first TP=2 launch** (§14.5): predict residency
   offline; shard at selection; check **both** MoE paths; verify router normalisation;
   assert parity; distinct per-rank checksums.
4. **NHI ITR module** (`ns=2048`), then re-run the RTT sweep (§13.4). Still valid, still
   on Track A's baseline, no unload required.
5. **Static /30 + firewall trust on thunderbolt1**; pin `NCCL_SOCKET_IFNAME=thunderbolt0`
   (§13.5).
6. **If Track B proceeds:** host-callback-ordered gate, **not** a GPU doorbell (§14.2);
   pinned+mapped buffer with the producer writing into it (§14.3); the eight invariants
   (§14.8); `-cc.cudagraph_mode=NONE` on the arm (§13.1); a **≥1500-exchange watchdog
   soak** (~1 h) before trusting it (§14.2). **Timebox to the ~6.7 ms/step ceiling.**
7. **Benchmark methodology from the start** (§14.9) — free, and it protects every number.

Unchanged: Track A is primary. MTP off first light. block-FP8 stays. No bonding, no
OdinLink driver (out-of-tree, one maintainer, a CQ that lies, a kernel panic on unload —
and RoCE beat it on the same nodes anyway), no ncclNet plugin, no O_DIRECT repack, no
PREWARM, and **none of their MoE/WMMA/quant kernel work** — vLLM's grouped GEMM is the
thing their 6.6× gap was *against*.


---

## 15. ADDENDUM — the three answers that decide tonight's order

Follow-up from the same deep-read. Supersedes §14 where they differ.

### 15.1 The 7.14 registration trap is the COMMON case for us, not an edge case

`rocm/ds4_rocm_runtime.cuh:5513-5600`, `cuda_model_range_ptr`, commit 8d45d16. The
registration is **page-rounded**:

```c
page_sz   = sysconf(_SC_PAGESIZE);
reg_addr  = host_addr & ~(page_sz - 1);                          // round DOWN
reg_delta = host_addr - reg_addr;
reg_bytes = (reg_delta + bytes + page_sz - 1) & ~(page_sz - 1);   // round UP
err = cudaHostRegister((void*)reg_addr, reg_bytes,
                       cudaHostRegisterMapped | cudaHostRegisterReadOnly);
```

> **Two logically DISJOINT tensor slices of the same mmap collide whenever their
> page-rounded spans touch.** You do not need overlapping tensors — only two slices that
> share a page at either end, or whose rounded spans abut. **With a ~51.2 GB engram table
> sliced into many non-page-aligned spans, this is the common case.**

On 7.14 the second registration returns `hipErrorHostMemoryAlreadyRegistered` /
`hipErrorAlreadyMapped` — **and a subsequent plain `hipMemcpy(H2D)` from that host
pointer also fails.** Detecting the registration error is not enough; the fallback copy
must change too.

**Second, silent failure mode:** `cudaErrorNotSupported` or `cudaErrorInvalidValue`
latches `g_model_range_mapping_supported = 0` (`:5548`), **permanently disabling the
zero-copy mapping path for the whole process.** Watch for it.

**The fix** (`:5566` onward): on the overlap error *only*, allocate a transient pinned
bounce and stage in 64 MiB chunks —

```c
const uint64_t chunk = 64ull*1024*1024;
if (overlapping_host_registration) {
    pinned_stage_bytes = bytes < chunk ? bytes : chunk;
    err = cudaMallocHost(&pinned_stage, pinned_stage_bytes);   /* on failure: free dev, clear last error, return NULL */
}
for (uint64_t done = 0; done < bytes; done += chunk) {
    uint64_t n = bytes - done < chunk ? bytes - done : chunk;
    const void *copy_src = src + done;
    if (pinned_stage) { memcpy(pinned_stage, copy_src, n); copy_src = pinned_stage; }
    err = cudaMemcpy((char*)dev + done, copy_src, n, cudaMemcpyHostToDevice);
}
if (pinned_stage) cudaFreeHost(pinned_stage);
```

Three details: the bounce exists **only** on the overlap path (zero cost otherwise); it
is **capped at 64 MiB** regardless of tensor size, so extra RSS is bounded; it is freed
on success **and every error exit**. Note this is an mmap→pinned host memcpy — ordinary
cached memory both sides, **not** the 200 MB/s write-combining case.

**This is a model-LOAD path issue, not a hot path.** It shows up as a load failure or a
silent fallback, never as slow decode. Our analogue is anywhere we call
`cudaHostRegister` / torch pinning on slices of the engram mmap.

**Run the probe (single node, seconds, safe with the pair up — it has no Makefile target):**
```
hipcc -O3 --offload-arch=gfx1151 $R/scripts/rocm_host_copy_probe.cu -o /tmp/rocm_host_copy_probe
/tmp/rocm_host_copy_probe                           # expects overlap to SUCCEED (pre-7.14)
/tmp/rocm_host_copy_probe --expect-overlap-rejected # expects it to FAIL   (7.14+)
```
**Exactly one should print PASS** — that single bit tells us whether our stack has the
new behavior. It sweeps six allocation shapes (including *two adjacent page-overlapping
ranges*, the case that matters) and prints `hipPointerGetAttributes` type/device/managed
and `ptr_mod_4k` per case — exactly the diagnostic we want for engram slices. Only ever
`hipMalloc`s an 8 MiB destination.

### 15.2 The in-kernel atomic doorbell is NOT indicted — but it is unproven

**ds4's failure indicts stream-memory-op PACKETS, not in-kernel atomics.**
`hipStreamWriteValue64` enqueues a write *packet* into the HSA queue, executed by the
command processor between kernels; every failure they documented is a **packet-submission**
failure ("never lands on the null stream"; "returns `hipSuccess` but its packet is never
submitted"). An in-kernel `__hip_atomic_store(..., __ATOMIC_RELEASE,
__HIP_MEMORY_SCOPE_SYSTEM)` is a store executed by a wave inside an **already-dispatched,
running** kernel. There is no separate packet to lose.

**Positive evidence on this chip:** `t0_hipmalloc_host_probe.cpp` case 3 *is* an in-kernel
store, and `ds4_rocm.cu:173-176` records *"MEASURED, NOT ASSUMED (T0 probe, gfx1151): a
GPU kernel write becomes host-visible with no explicit sync (~1.6k spins)."*
`t2_payload_visibility_probe.cpp`: 20,000 iterations × 4096 floats, **zero stale reads**.

**Why I will not ship on that:** T0 case 3 is **one word, one iteration, a plain
`volatile` store** — not a system-scope atomic, and not sustained. Their actual failure
(lost arrivals at gate seq 1306/1482) appears **only under sustained load**, exactly the
regime T0 does not probe. And **nowhere in ds4 does a kernel signal the host with a
system-scope atomic** — every `__ATOMIC_RELEASE` in the tree is host-side; the one
GPU-side system-scope primitive (`__threadfence_system()`) is in the *consumer* kernel,
i.e. the reliable host→GPU direction. **ds4 has no evidence either way about a sustained
in-kernel GPU→host atomic doorbell.**

> **THE EXPERIMENT THAT SETTLES IT — do this before writing any transport code.**
> Fork `scripts/hip_host_callback_gate_probe.cpp` and swap the signalling mechanism:
> a producer kernel writes a payload then `__hip_atomic_store(&flag, seq, __ATOMIC_RELEASE,
> __HIP_MEMORY_SCOPE_SYSTEM)` into a `hipHostMalloc(hipHostMallocMapped)` word via its
> device pointer; a host thread spins with `__atomic_load_n(ACQUIRE)` plus a per-iteration
> deadline. **25,800 iterations** (300 tokens × 86 gates — their production schedule) with a
> **~57 µs synthetic delay**. Add a second arm validating the payload on arrival (T2's check).
> Report `arrivals_missed`, `first_miss_seq`, `max_detect_us`, `payload_mismatches`.
> **ACCEPTANCE: ZERO missed arrivals over 25,800.** Their failure showed at 1306, so
> anything under ~2000 iterations proves nothing — their own harness refuses to run below
> 1307 for that reason.
> **Run BOTH flag allocators** (`hipHostMalloc`-mapped vs `hipMalloc`) — their harness
> parameterizes exactly that axis (`FLAG_ALLOCATOR=device|mapped`), so they clearly
> suspected it mattered, and never published which won.
> Single node, no peer, no model, no serve. Minutes to write, seconds to run.

### 15.3 Where the decode gain actually came from — the attribution

Assembled across five docs; the individual deltas are measured, the decomposition is the
agent's, and the items are **not strictly additive** (moving baselines; the fixed
2048+300 workload replaced ad-hoc ones partway through). **Ordering is reliable; deltas
are indicative.**

| # | item | delta | class |
|---|---|---|---|
| | **baseline 9.96 t/s** | | |
| 1 | service-thread back-off (patch 17) | 8.57 → ~10.5 (**soft**: 3 runs vs 1 pre-sample, ±12%) | detection latency |
| 2 | **`ODL_VERBS_WC_STREAM_COPY=1`** | 9.96 → 11.23 median, **+12.8%** | **transport-adjacent — the ONLY one** |
| 3 | packed Q8 attn-output LOW projection | 11.15 → 11.78 (+5.7%) | compute kernel |
| 4 | packed Q8 attn-output EXPANSION | 11.93 → 12.93 (+8.4%) | compute kernel |
| 5 | packed Q8 Q-B projection | 12.72 → 13.34 | compute kernel |
| 6 | ordered TP host callback | *no isolated delta* | **correctness/stability** — makes 300-token runs complete at all |
| 7 | fixed-workload re-baselining | number moves, engine does not | methodology |
| 8 | greedy top-2 exchange | 12.32 → 12.54 (+1.79%) | payload shape |
| 9–11 | staged Q4_K activation, K-shard residency, stage-XQ, temporal-compressor, cooperative HC, top-k radix tree, compile-out of hot profiling branches | promoted together | compute + schedule |
| | **final 20.43 (RoCE) / 19.17 (OdinLink)** | | |

> **Of ~10.5 t/s of decode gain, exactly ONE item (~+12.8%) is transport-adjacent — and
> it is a write-combining READ fix, not a wire fix.** Everything else is attention and
> projection kernels, protocol payload shape, and a stability fix.

Consistent with their instrumentation: transport 11%, own-GPU compute 88% — and **within
the compute half, attention ~63% vs MoE ~37%.** If our split resembles theirs, our decode
work is **attention-kernel work and payload-shape work**, and the custom all-reduce is a
small, bounded side bet.

Tried and lost, so we do not re-derive: paired Q8 DP4A for decode (changed the numerical
trajectory — 11.95 t/s / 29.83% acceptance vs 14.92 / 51.43%; default-off even for
ordinary decode); LDS activation staging in routed-MoE gate/up (regressed); 128-thread
geometry (neutral, rank-asymmetric), 512 threads (regressed).

### 15.4 Parity / divisibility assertions — all eight, fail-closed, before first TP=2

Their hazard verbatim: the network-TP site computes `n_groups/2` **with no parity check**,
unlike the mgpu site. *"With an odd head count the two ranks would together cover
2*(n/2) < n groups and quietly drop one."*

1. **Routed experts:** `512 % 2 == 0` ✓ — but **also assert the shard boundary is
   identical on both ranks and carried in the handshake.** ds4 puts the first-rank-1
   expert index in bits 8–15 of the bitwise-compared hello word precisely so an
   asymmetric launch fails closed. Cheap; catches a whole class of split-brain.
2. **THE SHARED EXPERT — highest risk in our config.** 1 is **odd** and cannot be
   range-split. Decide and assert explicitly: replicate on both ranks and fold **exactly
   once** (rank-0 only, or after the combine), or K-split so each rank computes half and
   the sum reconstructs it (ds4 does the latter, `tp_split_shared`). Their
   `CORRUPTION-BISECT` warns shared-expert addend semantics differ across the ordinary,
   row-split and `shared_down_f16` paths. **This is the item that produces FLUENT WRONG
   OUTPUT** — their defect 7 dropped the shared expert from every decode layer and the
   text stayed plausible.
3. **QSA attention heads:** `n_heads % 2 == 0` **and** `n_kv_heads % 2 == 0` (GQA groups
   must not straddle a rank boundary), and `head_dim * (n_heads/2)` must land on our
   quantization alignment. Their analogue is a 32-element Q8_0 guard with the note that
   *"a misaligned slice mixes neighbouring blocks' scales and is SILENTLY WRONG."*
   **Assert the slice boundary is a multiple of our FP8 block size.**
4. **GDN layers:** if sharded, `n_gdn_heads % 2 == 0`. Precedent from their GLM5 branch:
   recurrent linear-attention state was **REPLICATED, not sharded** (145.56 MiB/rank,
   no TP exchange). Replicating needs no parity assert but must be budgeted on **both**
   ranks; sharding needs the assert *and* a correctness argument for the recurrence.
5. **Layer count vs gate schedule.** 48 layers but a heterogeneous 12 QSA / 36 GDN
   schedule. ds4 carries `{gate_slot_start, gate_slot_step, gates_per_token}` in the
   hello because GLM *"fires one FFN gate per sparse layer only."* **Assert both ranks
   derive the same schedule, and derive it from tensor presence rather than layer index**
   — their GLM5 branch does exactly that.
6. **FFN width:** `intermediate_size % 2 == 0` **and** `% FP8_block_size == 0` for any
   column/row split.
7. **Vocab**, only if we row-shard the output head: `vocab % 2 == 0`, or handle the
   remainder explicitly. Their fix for the analogous odd-remainder bug was to **REFUSE
   the split** rather than handle it — good pattern.
8. **Prefill chunk rows:** if ever row-split, assert **even** row count and that both
   ranks compute identical chunk counts/sizes, or the header protocol desyncs.

### 15.5 GTT — treat as met, do not file as a gap

No rationale exists anywhere in their repo; `gttsize`/`ttm` appear only in
`STRIXHALO.md:62-102` as an unexplained recipe with a dmesg verification string, with no
measurement either way. Our **128087M ready** with `gttsize=0` and
`ttm.pages_limit=33554432` **exceeds their 126976M target**. (Plausible reading, not
theirs and not measured: `gttsize` caps the amdgpu GTT domain while `ttm.pages_limit`
caps TTM's global page pool — the latter binds on a 128 GB box; their recipe sets both.)

### 15.6 PROBE ORDER FOR TONIGHT — all single node, all safe with the pair up

Build pattern: `hipcc -O3 --offload-arch=gfx1151 <file> -o /tmp/<name>`

1. **`rocm_host_copy_probe`** (both modes) → settles the engram registration risk (§15.1)
2. **`t4_null_stream_gate_probe`** then **`t5_gate_stream_fix_probe`** → settles
   null-stream vs dedicated-stream semantics on **our** ROCm
3. **the forked §15.2 experiment** → settles whether an in-kernel atomic doorbell is
   viable **at all**
4. **`t6_bandwidth_probe`** → our own roofline denominator (theirs: 223.9 GiB/s = 94% of peak)
5. **`hip_graph_launch_ceiling_probe`** → strict upper bound on what our cudagraphs can buy

> **Only after step 3 passes clean is it worth writing transport code.**

Also single-node and unmodified: `t0_hipmalloc_host_probe`, `t2_payload_visibility_probe`,
`t3_gate_signal_probe` (prints whether host ptr == device ptr),
`hip_host_callback_gate_probe` (production-scale ordered callback; the right skeleton to
fork for step 3), `hip_graph_default_stream_probe`, `hc_cooperative_grid_probe`.

Two-node and requiring the ds4 build — **port by shape, do not try to run against vLLM**:
`test_tp_dual_stream_progress.cu`, `test_tp_completion_ordering.cu`,
`test_tp_big_gate_overlap.cu`, `roce_v2_mr_probe.cpp`.

---

## 16. PROBE RESULTS — measured on this hardware, 2026-08-31, before arming run 3

Everything in §14 and §15 was *inherited* from ds4's docs. This section is **ours**:
the §15.6 probe order, actually run, inside `flashnext:dev` on the coordinator, with
the pair up and `llama-swap` still active. Per §14.14 — *every optimization they argued
from reading code lost; every one they measured first won* — these numbers outrank the
inherited ones wherever they disagree.

**Build.** All eleven ds4 probes compile clean against our stack, unmodified:
`hipcc -O3 --offload-arch=gfx1151 -lpthread <src> -o <bin>`, hipcc at
`/opt/venv/bin/hipcc`, HIP 7.14.60850. Sources staged from
`~/Downloads/ds4-strix-halo-tp-odinlink/scripts/`. Two corrections to §14.12/§15.6:
the `t3`–`t6` probes are **`.cpp`, not `.cu`**, and
`hip_graph_default_stream_probe.cu` / `hip_graph_launch_ceiling_probe.cu` are **not on
`main`** — they live only on `origin/research/q4k-hipgraph-20260818`, the branch §14.6
notes dead-ends in a revert. Extract with `git show <branch>:scripts/<file>`.

Runtime note: the probe binaries need
`LD_LIBRARY_PATH=/opt/venv/lib/python3.12/site-packages/_rocm_sdk_core/lib:/opt/venv/lib/python3.12/site-packages/_rocm_sdk_devel/lib`
or they die on `libamdhip64.so.7`. GPU flags:
`--device /dev/kfd --device /dev/dri --security-opt seccomp=unconfined --ipc=host --group-add keep-groups`.

### 16.1 SETTLED: the 7.14 registration trap is LIVE on our stack

`rocm_host_copy_probe` — **exactly one mode passes, and it is the 7.14 one:**

```
rocm_host_copy_probe=FAIL ... expect_overlap_rejected=0   (default mode)
rocm_host_copy_probe=PASS ... expect_overlap_rejected=1   (--expect-overlap-rejected)
```

The diagnostic rows are the ones §15.1 predicted, verbatim:

```
adjacent-malloc range=1 register_base_mod_4k=0 register_bytes=8392704
                        host_register=part or all of the requested memory range is already mapped
adjacent-malloc range=1 h2d_copy=invalid argument
```

**Both halves confirmed.** The second registration of a page-adjacent range is rejected,
**and the subsequent plain `hipMemcpy(H2D)` from that pointer also fails.** §15.1 is not
a hypothetical inherited from ds4 — it is live in the image that serves this model, and
per §15.1 a ~51.2 GB engram table sliced into non-page-aligned spans hits it as the
*common* case. **The 64 MiB pinned-bounce fallback is required work, not optional.**

Also banked from the same run: `hip-host-mapped` reports `type=host device=0
ptr_mod_4k=0` and its H2D copy succeeds — the `hipHostMalloc(Mapped)` row of §14.3's
table, confirmed here. `page-aligned-register` and `anonymous-mmap-register` both pass
cleanly, so **page-aligning our engram slices is a real mitigation**, not just a bounce.

### 16.2 REVERSED: the in-kernel atomic doorbell beats the host callback by ~10×

§14.2 inherited ds4's *"DECISION: build the gate as host-callback-ordered … do NOT ask
the GPU to signal the host."* §15.2 flagged that their evidence indicts **stream-memory-op
packets**, not in-kernel atomics, and demanded a 25,800-gate soak before trusting either.
**That soak has now been run, on both flag allocators.** New probe, written for this:
`scratchpad/probes/src/hip_atomic_doorbell_soak.cpp` (193 lines, forked in shape from
ds4's `hip_host_callback_gate_probe.cpp` so per-gate costs are directly comparable).

Producer kernel writes a 4096-word payload, `__threadfence_system()`, `__syncthreads()`,
then a single wave does `__hip_atomic_store(flag, seq, __ATOMIC_RELEASE,
__HIP_MEMORY_SCOPE_SYSTEM)`. Host spins `__atomic_load_n(ACQUIRE)` with a per-iteration
deadline. 25,800 gates = 300 tokens × 86, ds4's production schedule.

| mechanism | flag lives in | µs/gate | mean detect | missed arrivals | payload mismatches |
|---|---|---|---|---|---|
| **in-kernel atomic doorbell** | **`hipHostMalloc` mapped** | **4.49** | **3.45 µs** | **0 / 25,800** | **0 / 25,800** |
| in-kernel atomic doorbell | `hipMalloc` device | 119.1 | 113.2 µs | 0 / 25,800 | 0 / 25,800 |
| ds4's ordered host callback | mapped | 43.0 – 44.9 | — | — | 0 errors |

Three findings:

1. **ACCEPTANCE MET.** Zero missed arrivals over 25,800 gates, on both allocators, with
   the payload validated on arrival. ds4's failure surfaced at seq 1306; we ran 19.8×
   past it. **A GPU→host in-kernel system-scope atomic doorbell is viable on gfx1151.**
   Their defect really was specific to `hipStreamWriteValue64` packet submission.
2. **The doorbell is ~10× cheaper than the callback** — 4.49 vs 43–45 µs/gate. The gate
   mechanism floor for a custom all-reduce drops by ~40 µs/gate, i.e. **~3.8 ms/step at
   96 gates.** This is the single biggest number the probes produced.
3. **ds4's unpublished allocator axis, answered: MAPPED, by 27×.** Polling a `hipMalloc`'d
   word from the CPU costs **113 µs per detect** — §14.3's write-combining trap, showing
   up on the doorbell poll rather than on a bulk memcpy. `FLAG_ALLOCATOR=device` is a
   trap, and it is the one they parameterized and never reported.

> **REVISED DECISION, superseding §14.2:** build the gate as an **in-kernel system-scope
> atomic doorbell into `hipHostMalloc(Mapped)` memory**, not an ordered host callback.
> Keep every §14.8 invariant. The host callback stays as the fallback rung — it is proven
> and it is 44 µs.

Two honesty notes. **The producer must be ONE block** — `__syncthreads()` is only a
block-wide barrier, so a multi-block grid rings the doorbell while sibling blocks are
still writing the payload. Two of my own runs reported hundreds of payload mismatches
until the grid was fixed to `dim3(1)`; that was a probe bug, not a coherence finding, and
it is exactly the shape of §14.8 invariant 1. **The detect tail is long:** mean 3.45 µs
but `max_detect_us` ≈ 650 µs in *every* arm including the idle one, almost certainly host
scheduler preemption of the spinning thread. A production progress thread needs the
§13.6 rule-2 backoff and an absolute deadline; the mean is what to design for, the tail
is what to survive.

Scope: single node, one wave, no peer, no network. This validates the **signalling
mechanism**, not an all-reduce.

### 16.3 The cudagraph is worth almost nothing at the HIP layer

`hip_graph_launch_ceiling_probe` — empty serialized kernels, which **maximize** what
graph replay can remove, so this is a strict upper bound:

```
nodes=4   eager_us= 7.733  graph_us= 9.109  ceiling_saved_us=-1.376
nodes=8   eager_us=15.459  graph_us=15.973  ceiling_saved_us=-0.514
nodes=16  eager_us=31.310  graph_us=30.361  ceiling_saved_us= 0.949
nodes=32  eager_us=62.287  graph_us=58.839  ceiling_saved_us= 3.448
```

**At 32 serialized launches the ceiling is 3.4 µs, and at 4–8 nodes graphs are a net
loss.** Eager launch is ~1.9 µs/node and graph replay is ~1.84 µs/node — a ~3% edge that
only pays back after the capture overhead is amortized.

**This de-risks §13.1's "one legal escape."** Running the Track B arm with
`-cc.cudagraph_mode=NONE` was the only way to keep a custom all-reduce from being
silently inert; the open worry was what the cudagraph was worth. At the HIP layer:
**≈0.1 µs per launch, bounded.** §13.1 also asked for a stock-RCCL-on-NONE control arm to
keep the A/B like-for-like — still worth banking, but expect it to land near the
PIECEWISE arm.

**State this precisely and do not overclaim it.** The probe bounds the **HIP-runtime
launch-overhead** component only. vLLM's PIECEWISE cudagraphs also elide Python and
torch dispatch, which is a much larger cost and is **not** measured here. The correct
reading is: *whatever PIECEWISE is worth on this model, essentially none of it is HIP
launch overhead.* The stock-RCCL-on-NONE control arm is what prices the rest.

Related, from `hip_graph_default_stream_probe`:

```
default_stream_capture=unsupported error=operation not permitted when stream is capturing
```

The default stream **cannot be captured** on our stack — independent confirmation of
§14.6's *"the collective must sit on a graph BOUNDARY, never inside a captured region."*

### 16.4 ds4's null-stream defect does NOT reproduce here — and it changes nothing

`t4_null_stream_gate_probe`, all four cases, **including ds4's exact failing shape**:

```
A. created stream, no prior kernel, 1 pair              : ARRIVAL SEEN (0.00 s)
B. NULL stream,    no prior kernel, 1 pair              : ARRIVAL SEEN (0.00 s)
C. NULL stream,    long kernel queued first, 1 pair     : ARRIVAL SEEN (0.00 s)
D. NULL stream,    long kernel + 44 pairs (ds4's shape) : ARRIVAL SEEN (0.00 s)
```

§14.2 records `hipStreamWriteValue64` as *"never lands on the null stream at all."*
**On our ROCm 7.14 / gfx1151 it lands every time, on the null stream, in their shape.**

**Do not conclude the mechanism is safe.** t4 is a short probe, and ds4's real failure was
*sustained* loss at seq 1306/1482 — the "survives a smoke test, dies in production"
regime t4 does not enter. What we have shown is that the **short-probe half** of their
evidence does not reproduce; the **sustained half is untested for `hipStreamWriteValue64`
on our stack.** Since §16.2 gives us a mechanism that *is* soaked to 25,800 and is 10×
cheaper, there is no reason to spend the soak on the packet path. Recorded so nobody
re-derives it.

`t5_gate_stream_fix_probe` agrees the dedicated-stream fix is valid and ordering holds
(`E2 stamp BEFORE release = 0`, `AFTER = 42`).

### 16.5 The §14.3 memory rules, re-measured here

- `t0_hipmalloc_host_probe`: host write to a `hipMalloc` pointer OK; **GPU write seen by
  host, coherent** — but after **1,024,775 spins**, against ds4's recorded ~1.6k. Same
  verdict, ~640× the spin count. Raw spin counts are not directly comparable across
  builds, but it is one more reason the *device* flag allocator is the wrong choice
  (§16.2).
- `t2_payload_visibility_probe`: **20,000 iterations, 0 stale-on-arrival (0.0000%)**,
  worst settle scan 0. *"Flag arrival IMPLIES payload visible — gate design is sound."*
  Matches ds4 exactly. Our §16.2 soak independently reconfirms it at 25,800.
- `t3_gate_signal_probe`: `hipMallocSignalMemory` **UNAVAILABLE** (same as ds4, falls back
  to `hipHostMalloc`); **host ptr == device ptr, identical** — §14.3's "SAFE" row
  confirmed on this box; GPU stream-write → CPU poll SEEN (0.000 s);
  `hipStreamWaitValue64` accepted and released (0.000 s).

### 16.6 Our roofline denominator

`t6_bandwidth_probe`, AMD Radeon 8060S, 20 CUs:

```
BEST: 215.9 GiB/s = 231.8 GB/s      efficiency vs peak: 91%
(theoretical LPDDR5X-8000 x 256-bit = 256 GB/s = 238.4 GiB/s)
```

ds4 measured 223.9 GiB/s (94%) on their node. Ours is **3.6% below theirs, and the
contention excuse does not hold** — `llama-swap` was idle with no backend spawned and
525 MB of VRAM in use (the desktop). **Use 215.9 GiB/s as the denominator.** The 3.6% gap
is unexplained and is probably just silicon/thermal variation between the two rigs; do not
attribute it without measuring.

### 16.7 What this does to Track B's price

Assembling §16.2 with §13.2's corrected baseline:

| path | gate mechanism | wire | total/gate |
|---|---|---|---|
| **today: stock RCCL over TCP** | — | — | **118–133 µs** (measured, full-duplex exchange) |
| custom AR, host-callback gate | 44 µs | + wire | 44 + wire |
| **custom AR, atomic doorbell (mapped)** | **4.5 µs** | + wire | **4.5 + wire** |

At 96 gates/step, the mechanism choice alone is worth **~3.8 ms/step**. And it corrects a
number §13.2 got optimistic about: *"stock `/dev/tbstream` at ~22.9 µs RTT is worth
4–5×."* With a host-callback gate that would have been 22.9 + 44 ≈ 67 µs — a **1.8×**,
not 4–5×. **With the atomic doorbell it is 22.9 + 4.5 ≈ 27 µs — and the 4–5× claim
survives.** The claim was right; the reasoning under it was missing a term that would
have eaten most of it.

**This is the strongest argument yet for Track B — and it arrived from measurement, not
from reading code.** §14.14's ledger of three well-executed overlap implementations that
each returned zero or negative still applies to *overlap*; it does not apply to a
mechanism swap that is 10× on a measured hot path.

Unchanged: **Track A is still primary, and none of this perturbs it.** §14.15's order
stands, with two edits: step 2 (`rocm_host_copy_probe`) is **done — it failed, act on it**,
and step 6's *"host-callback-ordered gate, not a GPU doorbell"* is **reversed to the
atomic doorbell on mapped memory**, callback as fallback.

### 16.8 Still not settled

- **`VLLM_PLE_MMAP` has not been exercised against §16.1.** We know the trap is live; we
  have not yet observed our own engram path hit it. That is a product probe, not a ds4 one.
- **Two ranks.** Every result above is single-node, one wave, no peer. The doorbell is
  proven as a *signalling primitive*, not as an all-reduce.
- **The 650 µs detect tail** has no root cause. Suspect host scheduler preemption;
  unmeasured against a pinned/isolated thread.
- **`t6`'s 91% vs ds4's 94%** is unexplained. The obvious suspect — `llama-swap`
  contention — is ruled out: it was idle, no backend, 525 MB VRAM. Unattributed.

---

## 17. PREP LEDGER — mechanical work completed 2026-08-31, before arming

Machine and repo preparation done during the pre-arm window. Each line is verifiable
by the command shown.

### 17.1 Done

1. **Worker image shipped.** The worker carried **zero podman images**
   (`ssh 10.99.9.2 podman images` returned empty). `host/fn-image-ship.sh` ran to
   completion; worker now holds `flashnext:dev @ 277f0cb9a3575113889`, id-matched to the
   coordinator. *Correction to the alarm this first raised:* `fn-cluster-up.sh:64` invokes
   the ship script itself, so the run would have shipped it anyway — this pre-warms an
   ~18 GB transfer out of the night's clock, it does not rescue a would-be failure.
2. **`host/fn-image-ship.sh` mode fixed**, 644 → 755. It was the only script in `host/`
   without the execute bit and it failed `Permission denied` on direct invocation.
   *Scope honestly:* every in-tree caller uses `bash <script>`, and the worklist gates use
   `["bash", "scripts/…"]` argv form, so **no lane was broken by this** — the
   `scripts/*.sh` files are likewise non-executable by design. Operator-path hygiene only.
3. **Run 2 harvested** to `handoff/harvest-2026-08-30/` — 2,103 captures, 3,096 unit-exit
   records, the full campaigns tree, and a README indexing what each artifact proves.
   §3 recorded that no harvest of run 2 existed and that run 3 would overwrite it. The
   receipt log confirms §1.1 exactly: 61 rows, **cp-weights 13 attempts, proxy-tooling 10**,
   escalations at seq 48/50/53/57/61.
4. **The §15.6 probe order was run end to end.** Results in §16. Three inherited beliefs
   are now measured facts on this hardware, one of them reversed (§16.2).
5. **`probes/` added** — `hip_atomic_doorbell_soak.cpp` (ours) plus a README carrying the
   build/run recipe, the ds4 staging instructions, and the two mistakes that produce false
   failures in this probe class. ds4's sources are deliberately **not** vendored.

### 17.2 Verified, no action needed — do not re-open

- **The TP=2 compile-cache pinning is correct and complete.** `fn-cluster-up.sh:59-61`
  generates the env file through `ENV_FILTER`, which includes `TORCHINDUCTOR_|TRITON_`;
  `:83` and `:98` bind-mount `$FN_STATE_DIR` **at the same path on both nodes**; `:84`/`:99`
  pass the env file. §5's "pinned only on the TP=2 path" is precise — the TP=2 path is
  right, and the gap is real only for `run-smoke.sh` (never sources `fn-env.sh`),
  `make-proxy.sh` (same), `run-proxy.sh` (sources it but forwards only three `-e` vars and
  bind-mounts no state dir), and `container/Containerfile`.
- **Both cache dirs are still empty** (`~/.local/state/flashnext/{triton,torchinductor}`),
  so the **first** TP=2 serve pays the full compile regardless of pinning. Pre-warming
  them requires an actual serve, which requires stopping `llama-swap` — an operator call,
  not pre-arm work.
- **Pair reachability confirmed** at prep time: `10.99.0.2` answers at 0.149 ms avg;
  `ssh 10.99.9.2` answers as `worker`. **Re-verify immediately before arming** — losing
  this at 13:57Z is what ended run 2.
- **thunderbolt1 is still unconfigured** — `169.254.17.133/16` on the coordinator,
  `169.254.53.173/16` on the worker, both link-local, no /30. §13.5's free win
  (static /30 + firewall trust, `NCCL_SOCKET_IFNAME=thunderbolt0` pinned) is **not done**;
  it is a host network change, out of the repo's scope.
- **`llama-swap` is `active` on both nodes but holds NO GPU** — corrected 2026-08-31.
  It is a ~17 MB Go proxy (`modules/llama-swap.nix:13`: *"The proxy itself is a small,
  always-on Go process and consumes no GPU"*). Measured: `/running` → `{"running":[]}` on
  both, no `llama-server` process, coordinator RSS 18.4 MB / 45 ms CPU over 6 h, VRAM 525 MB
  (the desktop). **The hazard is not idle occupancy — it is a swap-in landing mid-run.**
  Port 9292 has two live doors (tailnet, house LAN) plus the local utility-model wrapper,
  and any of them dialling during a TP=2 serve spawns a backend allocating out of the same
  125 GB unified pool vLLM holds. Nothing arbitrates this: `fn-cluster-up.sh` contains zero
  references to llama-swap or :9292, so the two systems are mutually blind.
  **Correct prep: `systemctl stop llama-swap` on both twins for the duration of a TP=2
  serve, restart after. A stop, not a disable.**
- **One stale container**: `fnproxy-dbg`, `Exited (0) 36 hours ago`, on the coordinator.
  §10.0 item 1's second half, unexecuted.
- **Coordinator podman storage**: 128 images, 48.12 GB, 100% reclaimable, root at 74%
  (230 GB free). Not urgent; worth a `podman image prune` before a build-heavy night.

### 17.3 Still open from §10.0, untouched

Items 5 (weights contract vs the deployed catalog prune — cp-weights is non-idempotent),
6 (NAS library path: `docs/DECISIONS-2026-08-30.md` step 0 vs `stage-weights.sh`'s
hardcoded path), and 7 (land the §4 record corrections into the documents that carry them)
are unstarted. Item 8's guidance stands: build `host/rdma/` for an attended window if you
want it ready, **do not load it inside the run**.

§2.1's three structural worklist defects — receipts destroyed by the `results/receipts`
conflict-domain shape, unowned executables, and the blind closing gate — are **worklist
authoring** problems and belong to the pass that converts this brief into a run.

---

## 18. TALLY FLOW AUTHORING RULES — read from the source at 62fac87

Derived by reading `~/mecattaf/tally.nix` at **HEAD = 62fac87** (the pin now live on
both twins). Citations are file:line in that tree. This supersedes §1 and §2.1 wherever
they conflict — several of their conclusions were correct about the symptom and wrong
about the remedy.

### 18.1 The escalation-fold wedge is FIXED. Do not rename the campaign.

`101cd03` — *"campaign: a pardon releases the lifetime latch for the reader too (#642)"*.
Two folds over one append-only log disagreed: `#626` gave a pardon reach over the
ten-attempt lifetime backstop in the **driver's** fold and left the **CLI's** copy on the
older semantics. The driver only posts an escalation when its own fold says none is live,
so after a pardon it posted again every quiescent pass, while the CLI counted a pardoned
task as a contributor to every one of them.

The fix, at `campaign.rs:6124-6131` and `:6139-6142`: a campaign-wide pardon now does
`lifetime_attempts.clear()`, and a task-scoped pardon does `lifetime_attempts.remove(id)`
per task — matching `fold_attempt_receipts` on the driver side.

**Verified empirically on the live estate after the pin landed:** `tally-campaign-poll.service`
**succeeds**, same campaign, same state, new binary. The refusal also now names its
claimants instead of printing a bare count.

> **SUPERSEDES §1.1's FIX.** Run 3 does **not** need a new campaign name. Keep `flashnext`.
> The receipt log, the ref namespace and the durable record all stay. §1.2's stale
> `summary/quiescent` ref is **not** mooted by a rename any more — if it bites, delete it
> explicitly.

The ledger now reports it holds **`cp-tp2`**, with 14 of 18 admitted tasks carrying no
completion fact. That is the true remaining state, and it is answerable with
`tally campaign inbox` + `tally campaign steer`.

### 18.2 ALL-OPUS: how to declare it, and why it was refused before

Issue **#624** (`FlowAdmissionDenied`, empty details, 11 burned attempts) is now explained
and fixed. `flake.nix:4566-4568`: *"An adapter with a null `launch.model` authorizes no
override, which is what refused every worklist `agent.model`."* The `claude-code` adapter
now declares a model contract. From the **deployed** config (`~/.config/tally/config.json`):

```json
"claude-code": {
  "launch": {
    "model": { "allowedValues": ["claude-opus-5","claude-fable-5","claude-sonnet-5",
                                 "claude-opus-4-8","claude-haiku-4-5"],
               "argv": ["--model","%<value>%"] },
    "modelEnv": "ANTHROPIC_MODEL"
  }
}
```

**To make the night all-opus:** set `campaign.agent.model = "claude-opus-5"` with
`adapter: "claude-code"`. `agent.model` is a validated field on `CampaignAgent`
(`campaign_contract.rs:validate_agent`). A declaration reaches **three** places and #648's
whole claim is that they agree: the durable row, the rendered argv (`--model claude-opus-5`),
and the unit's own `--setenv ANTHROPIC_MODEL=claude-opus-5` (`3838e8b`).

Precedence and provenance (`exec_attestation.rs:63,155-160`): a task naming its own model
overrides the campaign declaration and stamps `modelProvenance: "task"`; the campaign
declaration stamps `"daemon-config"`. A lane on a *different* adapter takes no default and
carries no stamp claiming it did.

**Verify BEFORE attempts burn** — the model is stamped into each execution receipt
(`89d6313`), so read the attestation rather than trusting the config:
`jq -r 'select(.payload.model) | [.payload.model, .payload.modelProvenance] | @tsv' ~/.local/state/tally/exec-attestations.jsonl | sort -u | tail`.
Anything other than `claude-opus-5 daemon-config` on a lane means the declaration did not take.

### 18.3 RECEIPT SURVIVAL — §2.1's remedy is ILLEGAL; here is the real one

The two task kinds are contractually different (`campaign_contract.rs:451-490`):

| | `implementation` | `checkpoint` |
|---|---|---|
| `conflictDomains` | **required**, is the write boundary | **FORBIDDEN** — *"checkpoint task {id} must not carry conflictDomains"* (`:473`) |
| `argv` / `runtimeMaxSec` | **forbidden** | **required**, `runtimeMaxSec > 0` |
| runs | an agent lane | a command |

> **So "give each checkpoint its receipt path in conflictDomains" — the obvious reading of
> §2.1 defect 1 — is rejected by the schema.** Checkpoints VERIFY; implementations WRITE.

The purity rule is `git status --porcelain **--untracked-files=no**`
(`actions.rs:4794-4806`). **Only TRACKED changes are refused.** An untracked receipt is
legal — it simply dies with the worktree, because a checkpoint owns no paths and nothing
it writes is ever committed. That is the whole of why two green nights banked no evidence.

`scripts/receipt-restore.py` already solves the *other* half (a re-run re-stamps `ts` and
dirties a tracked receipt → purity refusal); its own docstring notes *"New (untracked)
receipts are never touched"* — they land, in a directory about to be deleted.

**THE PATTERN TO AUTHOR:**

1. **Every checkpoint writes its receipt to a durable absolute path outside any worktree** —
   `$FN_STATE_DIR/receipts/<step>.json`. `FN_STATE_DIR` is already bind-mounted into the
   containers at the same path on both nodes (`fn-cluster-up.sh:83,98`), so this costs
   nothing and works from inside the serve container.
2. **One final `implementation` task owns `results/receipts`** and copies
   `$FN_STATE_DIR/receipts/*` into the repo, then commits. It is the only task that may,
   and it must be the last one that writes there.
3. **Never let two tasks own `results/receipts`.** Run 2 had `container-recipe` owning the
   directory and `proxy-tooling` owning `results/receipts/proxy.json` — a parent/child
   overlap that `validate_conflict_domains` flags (`conflict-domains-parent` in the
   contract corpus).
4. Keep writing receipts **untracked-first**: a checkpoint that modifies a tracked file
   fails purity, so the collector task — not the checkpoint — is what makes them tracked.

### 18.4 Other contract facts worth having in hand

- Task fields (`CampaignTaskReference`, `campaign_contract.rs:158-176`): `id`, `kind`,
  `issue` (u64), `dependencies` (must name an **earlier** task, no repeats), optional
  `conflictDomains`, `argv`, `runtimeMaxSec`. `deny_unknown_fields` — a typo is a hard
  admission failure, not a warning.
- `conflictDomains` must be an **array when present** (`:179-187`); `null` is refused.
- Overlap rules only bind when `maxParallel > 1` (`:459`). At `maxParallel: 1` the domains
  are still the write boundary but never a scheduling constraint.
- Gates come in two kinds: `command` (`preflightArgv` + `argv`) and `forbidPaths`.
  `forbidPaths` is unused by us and is the cheap way to make "no lane may touch X" a
  campaign-level invariant.
- **Title cap:** `#647` is fixed — the cap counts **characters wherever it is declared**
  (`c2eba58`). §2.2's warning that `cp-weights`' 277-of-300-**byte** title had no headroom
  is stale.
- **`--wait` and lineage:** `8378193` — a lost `--wait` keeps its arm, and `main` is not
  the lineage (#644, #639).
- **Empty-diff lanes:** `3eabd6f` — *"a lane with nothing to change completes"* (#635).
  §2.1's zero-diff squash failure class is fixed; a task whose work is already done no
  longer burns an attempt.
- **Salvage:** `864cfb1` / `7fdc935` — a steward answer the result schema refused is now
  repaired before an operator is woken (#638), bounded to the refused answer.

### 18.5 What remains OUR problem, not the harness's

§2.1 defects 2 and 3 are unaffected by the pin and are pure authoring:

- **Unowned executables.** `scripts/run-smoke.sh`, `stage-weights.sh`,
  `stage-weights-both.sh`, `verify-fork.sh`, `receipt-restore.py` are *executed* by
  checkpoints and owned by **no** task's conflictDomains, so no lane could ever fix them.
  The podman `-i` defect in `run-smoke.sh` alone burned ~5 hours and 6 receipts.
  **Give them an owner.**
- **The closing gate is blind.** `scripts/receipts-verify.py` exits 0 on *"3 receipts
  checked, 0 violations"* because missing receipts are legal — verified again tonight, and
  13 were pre-declared. It cannot distinguish "nothing ran" from "everything passed".
  **The closing checkpoint must assert the expected receipt SET, not just the validity of
  whatever it finds.**
