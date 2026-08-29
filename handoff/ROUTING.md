# Compute routing — flashnext overnight campaign

One page: which model does which work tonight, why that is the only expressible
configuration, and the exact ladder when a lane fails. Grounded in tally.nix
source pins (full citations in the routing analyst's report); reality-checked
against the pre-arm readiness pass (all five worklist gates green, spec
ratified at `a066074`, weights pre-staging already running detached).

## The five compute tiers

| tier | what runs on it | bound where |
|---|---|---|
| qwen3.8-max (qwencloud) | all 8 implementation lanes | adapter `pi` launches `pi --mode json` with no model flag; pi's own defaults (`~/.pi/agent/settings.json`: qwen-token-plan / qwen3.8-max) answer — ruling P15's "host catalog". Credentials verified pre-arm (`pi auth check qwen-token-plan` exit 0). |
| no model | all 7 `cp-*` checkpoints, all 5 gates | direct argv subprocesses of the driver; no adapter in the loop |
| claude sonnet | steward narration (PR/squash prose; advisory, never blocking) | host catalog `narrator` adapter shim (`dotfiles/home/tally.nix`) |
| claude opus | steward diagnosis of every failed lane/checkpoint; ultracode takeover work | same shim (`role: diagnosis`); overseer-launched workflows |
| fable | oversight, steering, diff review, blocker/issue authoring, takeover synthesis | the overseer session |

## Why there is no per-task routing knob

schemaVersion 1 admits one campaign-level `agent` block; task references are
`deny_unknown_fields` and carry no agent keys (campaign_contract.rs:77-104,
161-177). Spec F.3 forbids adding worklist keys. The `pi` preset declares
`launch = {}`, so even a campaign-level `agent.model` would be REFUSED at
dispatch ("model override is not authorized by this adapter"). The committed
worklist is armed unchanged; routing is enforced by supervision, not
configuration. There is also no steer-time model override — the adapter and
model are fixed for the campaign's lifetime.

## Task assignments

| task | tier | posture |
|---|---|---|
| container-recipe | qwen | default ladder; graded for real by `cp-build` |
| instruments | qwen | default ladder; overseer spot-reads `fn_expert_union.py` post-merge |
| **host-tooling** | **qwen, watched** | **single-retry fuse** — see below |
| bench-harness | qwen | default ladder; behavioral test gates it |
| proxy-tooling | qwen | weak grep gate; overseer reads both scripts before `cp-proxy` |
| morning-ledger | qwen | default ladder; content reviewed at close |
| catalog-handoff | qwen | verify patch against precedent row before morning |
| rdma-package | qwen | verify-and-fix (v2 exists under `host/rdma/`); safety greps are real |
| cp-weights … cp-close (7) | no model | direct argv; a failure is a *product* defect, not a lane defect. `cp-weights` fast-paths: pre-staging launched pre-arm, idempotent rsync |

**host-tooling** is the judged case: five scripts + a unit + a test across two
hosts, and its grep gate is satisfiable by wrong code. A pre-dispatch
task-scoped steer (posted at arm time; the pre-dispatch re-read folds it into
the first brief) pins the semantic traps: NCCL_SOCKET_IFNAME COMPUTED from
`ip -br addr` (rail 1 has no peer IP — hardcoding both rails passes the grep
and hangs RCCL bootstrap); **NCCL_IB_DISABLE=1 unconditionally** (ibverbs
devices now exist on both nodes by design); never export a probe-read engine
default (F.9 — DEEP_GEMM / AITER family); receipts matching
`scripts/receipts-verify.py` exactly; worker actions over the ethernet wire,
never a 10.99.0.x rail; reap-then-gate in cluster-up. Fuse triggers →
takeover, skipping diagnosis round 2: acceptance fail on attempt 1; a passing
diff that trips a tripwire; retry failure; ~01:30 with no merge (cp-tp2's
slot at risk).

## The escalation ladder (memorize this)

1. Lane fails → automatic Opus diagnosis → one retry. Add a task-scoped
   append-only steer alongside when the overseer knows an estate fact the
   brief lacks: `tally campaign steer mecattaf/flashnext
   silent-factory-worklists/flashnext.json --task <id> --message '…'`.
   Steers land via the deterministic pre-dispatch re-read; a running node's
   brief never changes.
2. Retry fails (or a host-tooling fuse trigger) → TAKEOVER: Opus workers
   (ultracode) author the fix in a scratch checkout OUTSIDE the lane
   worktree; Fable reviews and squashes to ONE exact unified diff; the diff
   is delivered TO THE LANE as a steer ("apply exactly this patch,
   byte-for-byte, with `git apply`; then run the acceptance argv and
   commit"). If the task is already escalated/blocked,
   `tally campaign resume <master-issue-url> --reason … --wait` pardons the
   counters first. The lane applies, the gates grade, the lane merges —
   completion proof stays native to the armed graph. Very large diffs: split
   per-file across steers (task-thread read window is the newest 100
   comments).
3. Checkpoint fails → product defect: fix through the owning task (steer) or
   forward on base (hat 2 territory), then `resume` to re-run. Upstream
   cause → P16 typed blocker + drafted issue under
   `handoff/upstream-issues/` (fable work).

Never: hand-edit a lane worktree, receipts, or the armed graph; change the
adapter or model of an armed campaign; record a skip without a typed blocker
(F.12). If tally itself is the obstacle, that is the OVERSEER.md
fatal-defect clause: file the `mecattaf/tally.nix` issue, minimally patch,
resume.

## Operational notes

- A pi attempt whose stream never closes a valid `message_end` cannot be
  resumed — a qwencloud outage or context overflow costs a whole attempt
  (cheap in tokens, real in wall-clock). Watch the first dispatch: endpoint
  sluggishness shows there first.
- Read the automatic Opus diagnosis before authoring parallel steers; reserve
  ultracode for rung 2. Do not double-spend.
- Morning-lane hazard folded into `host/rdma/ab-protocol.md`: if/when the
  attended A/B flips to RDMA, NCCL_IB_HCA must be the EXACT string
  `usb4_rdma0`, and the XDomain wedge discipline applies before the first
  verbs transmit. Tonight is immune (NCCL_IB_DISABLE=1 pinned).
