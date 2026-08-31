# Run-3 autonomous supervision state

Operator away 2026-08-30 23:40Z → ~07:40Z. Claude Opus oversees unattended.

## Armed
- campaign `flashnext`, arm serial 13, graph digest sha256:f600abca36c6b1a3...
- flow run 01a05506-a4c5-7fd3-9549-1ed9ba7f3c81
- authority commit 1a0bc12 on origin/main (pushed 23:34Z — the arm reads origin, not local HEAD)
- all-opus VERIFIED in dispatched unit argv: ANTHROPIC_MODEL=claude-opus-5,
  --model claude-opus-5 --model-provenance task, claude --model claude-opus-5

## Objective
cp-tp2 — TP=2 first light, block-FP8, both Strix Halo nodes.
Path: engram-registration + allreduce-instrument -> cp-build -> (+receipt-durability)
      cp-smoke -> (+tp2-guards) cp-tp2 -> cp-bench -> evidence-collector -> cp-close

## Standing facts
- Completion facts are keyed to the APPROVED GRAPH DIGEST, not per-task content.
  The rewrite reset all 17 tasks to pending. The 7 previously-done lanes re-run as
  fast reconciles (host-tooling: 2.4 min, $1.39) — this is expected, not a fault.
- llama-swap STOPPED on both twins (stop, not disable). MUST be restarted at end.
- exec-attestations.jsonl does NOT carry model fields; verify opus via unit argv.
- run-tp2.sh:43 -> fn-cluster-up.sh -> fn-image-ship.sh ships the image to worker.

## Authorized interventions while unattended
- pardon a lifetime-attempt latch (`tally campaign pardon`)
- re-poll / re-arm if a pass wedges
- kill a lane that is provably hung, let tally retry
- restart llama-swap once the serve is down
Do NOT redefine the graph mid-run: it resets every completion fact again.

## Event log

### 2026-08-30 23:52Z — brief availability (investigated, NO ACTION)
Lane worktrees branch from the campaign integration branch (tip 13e83be, descended
from 710ee52), which PREDATES the prep commits. `handoff/RUN3-BRIEF.md` is therefore
absent from every lane worktree, and all four new tasks cite it (§15.1/§16.1,
§10.1/§13.x/§14.x, §18.3, §14.5/§15.4).
RESOLVED BY THE LANE ITSELF: engram-registration searched, missed, then found and read
the brief at the absolute path /home/tom/mecattaf/flashnext/handoff/RUN3-BRIEF.md
(the main checkout) and pulled §14.7, §15.1, §16, §16.1.
=> Do NOT merge main into the integration branch (worklist-file conflict risk, and it
   would perturb a live run for no gain).
=> CONSEQUENCE: the main checkout at /home/tom/mecattaf/flashnext is load-bearing for
   lane context tonight. Do not move, rewrite, or git-clean handoff/ while lanes run.

### Reconcile lanes completed (fast, as designed)
host-tooling 2.4min $1.39 | bench-harness | catalog-handoff | rdma-package  — all exit 0

### 00:20Z — verified landings
- engram-registration: new 1167-line patch + patches/MANIFEST.md, cites measured §16.1.
- receipt-durability: receipts-verify.py --require IMPLEMENTED and FAILS CLOSED
  (exit 2 with receipts absent, exit 0 baseline). The cp-close vacuous-pass gap
  flagged pre-arm is now closed. Also receipt-restore.py + 428-line test module.
NOTE: unit-exit EXIT events fire for GATE jobs too and are NOT lane-completion
signals. The true completion signal is a commit landing on the integration branch;
the watcher was rewritten to key on that.

### 00:56–01:07Z — INTERVENTION: cp-build purity (the night's first real blocker)
SYMPTOM: cp-build failed 3 consecutive attempts, exit 1:
  "checkpoint command changed tracked files instead of validating the prepared
   base: results/receipts/build.json"
THE BUILD ITSELF SUCCEEDS — localhost/flashnext:dev, 18.2 GB, built fresh each time.
ROOT CAUSE (my authoring error): the OLD cp-build argv wrapped build.sh in an inline
purity guard (compare receipt to HEAD on contract fields, then git checkout the
receipt). When I rewrote the worklist I simplified the argv to
["bash","container/build.sh"] and dropped that guard. Note the old guard would ALSO
have failed tonight — it treats fork_commit as invariant, and engram-registration
deliberately moved the fork head.
WHY NOT STEER THE TASK: a checkpoint has no agent; it runs fixed argv. Steering
cannot change behaviour. WHY NOT AMEND THE WORKLIST: any graph change resets every
completion fact (8 landed lanes would re-run).
FIX (content, not graph): commit 3237869 on the campaign integration branch.
container/build.sh now, AFTER the existing pass assertion:
  1. exports FN_STATE_DIR using fn-env.sh:25's own default — build.sh does NOT
     source fn-env.sh, so FN_STATE_DIR was unset, receipt-restore.py would have
     skipped the mirror by design, and cp-close's `--require build` would have
     failed at the END of the night with the receipt nowhere on disk;
  2. mirrors results/receipts/ to $FN_STATE_DIR/receipts (durable truth);
  3. restores the disposable tracked results/receipts/build.json.
A failed build exits before reaching any of this, so it still fails loudly.
VERIFIED with FN_STATE_DIR unset (the real cp-build condition): tracked tree clean,
durable receipt lands. Gates green: 201 tests, receipts-verify 0, verify-fork 13/13.
Dispatch held to 01:35Z via `campaign steer --hold`; steer also refreshes the spent
attempt budget (3 burned; lifetime latch is 10 and untouched — `campaign pardon`
lifts that if ever needed).
CHECKED AND FINE: run-smoke.sh, run-tp2.sh and bench/run-matrix.sh ALREADY call
receipt-restore.py, so the later checkpoints' receipts are durable. build.sh was the
only unwired writer, because its owning lane (container-recipe) never re-ran.

### 01:35Z — cp-build PASSED (fix confirmed)
status=pass, wall_clock_s=15 (podman layer cache), durable receipt at
~/.local/state/flashnext/receipts/build.json carrying the NEW
fork_commit=bdb6f0420e797de9266de593326368091085cc3b (engram-registration's).
torch 2.13.0+rocm7.14.0, triton 3.8.0+git4cff872c.rocm7.14.0.
Tracked tree stayed pure. cp-smoke dispatched.

### 01:36Z — cp-smoke PASSED. 01:40–01:55Z — INTERVENTION 2: the cp-tp2 blocker
SYMPTOM: cp-tp2 exit 1 at PREFLIGHT (not the serve). env_byte_diff=fail, on
exactly two lines present on coordinator and absent on worker:
    FN_TRANSPORT_RUNG=rail0-sockets
    NCCL_SOCKET_IFNAME=thunderbolt0
Rails were healthy throughout: thunderbolt0 40 Gbps, RTT 84/63 us, PM QoS 0 both
ends, thunderbolt1 correctly excluded (no routable peer).
ROOT CAUSE (pre-existing, NOT from tonight's lanes — tp2-guards never touched
fn-preflight.sh): the coordinator-decided transport is injected into the worker's
sourcing of fn-env.sh as a PREFIX ASSIGNMENT on the `source` builtin:
    NCCL_SOCKET_IFNAME='..' FN_TRANSPORT_RUNG='..' source fn-env.sh
bash applies a prefix assignment only for the duration of the builtin and RESTORES
the prior (unset) state on return, undoing the export fn-env.sh does at :125/:142 —
both of which correctly honour an injected value via ${VAR:-default}. Worker ended
up with neither variable.
Reproduced by hand over ssh, then verified separate `export` statements make the
two ranks byte-identical.
fn-cluster-up.sh:192 CARRIED THE IDENTICAL DEFECT and is the more dangerous one: it
builds the WORKER CONTAINER'S ENV-FILE. Fixing only the preflight would have turned
a loud early failure into a silent RCCL bootstrap hang. Both fixed together.
FIX: commit cc2f018 on the integration branch.
VERIFIED against the live pair from the patched tree: "both ranks carry identical
doctrine env", exit 0, status=pass receipt. 201 tests green.
Dispatch held to 02:19Z, then cp-tp2 retries.

### 01:50Z — pre-retry verification for cp-tp2 (all green, no action)
- WEIGHTS: staged on BOTH nodes, /var/lib/local-models/flashnext-fp8, 173 GB,
  141 files each. Dropping the old cp-weights task from the run-3 graph was safe.
- IMAGE: worker's flashnext:dev was 15 h old (stale) vs coordinator's fresh build.
  fn-image-ship.sh compares image *Id* (not tag presence) and re-verifies after
  transfer, so it will ship the new one and fail loudly on mismatch. ~18 GB over
  the 5GbE wire, inside cp-tp2's 180 min budget. No action needed.
- llama-swap: still inactive on both twins.
- Rails: thunderbolt0 40 Gbps, RTT 137/59 us, PM QoS 0 both ends.

### 02:20–02:40Z — INTERVENTION 3: Ray absent from the image (cp-tp2 blocker #2)
cp-tp2 attempt 1 cleared: preflight (env fix CONFIRMED LIVE — "both ranks carry
identical doctrine env"), the eight parity guards, the reap gate, cache-pin checks,
18 GB image ship + Id verify, containers up on BOTH nodes. Died at the next step:
    fn-cluster-up: ray head on the coordinator
    Error: crun: executable file `ray` not found in $PATH        (exit 127)
CAUSE: Ray genuinely absent from flashnext:dev. `pip install -e /opt/vllm` does not
pull it (vLLM declares ray as an EXTRA, not a core dep) and nothing in container/
installed it. fn-cluster-up.sh is built entirely around a Ray head/worker topology,
so no two-node serve could ever have started. Two green nights never exposed this
because no run had reached the step.
FIX: commit df25c32 — ray[cgraph,default]==2.55.1, the FORK'S OWN ROCm pin
(requirements/test/rocm.in:24, "includes worker startup getenv/setenv race fix").
Added as the LAST Containerfile layer so the engine layers stay cached (rebuild was
incremental). The RUN asserts torch is undisturbed in the same layer, because
ray[default] pulls a broad dep set and silent numpy/protobuf churn under ROCm torch
would be worse than a loud build failure.
VERIFIED in the rebuilt image: /opt/venv/bin/ray, ray 2.55.1, import ok,
torch still 2.13.0+rocm7.14.0. build.sh receipt status=pass, fork_commit bdb6f042.
NOTE: holds take the LATER time; `steer --hold 1` cannot shorten an active hold.
cp-tp2 retries at 03:11Z.

### 03:18–03:35Z — INTERVENTION 4: ray reports no GPU (cp-tp2 blocker #3)
Ray fix worked — head started, worker joined, `ray status` showed BOTH nodes
Active, no failures. Serve refused by the two-GPU gate:
    FATAL: ray never reported 2.0 GPU (last reading: ''); refusing to serve
EMPTY, not 1.0 — the cluster formed; Ray reported no GPU RESOURCE at all. The
dumped Resources block lists CPU/memory/object_store and no GPU row.
MEASURED in the serve image with /dev/kfd + /dev/dri attached:
  ray status            -> no GPU row
  ray.cluster_resources -> {'CPU': 2.0}
  torch, same container -> cuda available, device_count 1
Ray's AMD accelerator probe does not enumerate this gfx1151 APU.
FIX: commit 03cfdca — declare it. RAY_NUM_GPUS=1 in fn-env.sh (beside
RAY_NUM_CPUS, RAY_-prefixed so it rides the byte-compared doctrine env), passed
as --num-gpus to BOTH ray start calls.
VERIFIED in-image: ray status 0.0/1.0 GPU; the gate's own awk parses 1.0; a
@ray.remote(num_gpus=1) task acquires the device -> "AMD Radeon 8060S Graphics".
Preflight re-run against the live pair with the new var: still byte-clean.
201 tests green. The GATE ITSELF WAS LEFT UNTOUCHED — it caught this correctly.
cp-tp2 retries 04:04Z. Note each failure costs a ~40 min hold cycle; deliberately
NOT pre-running a manual serve, which would hold the GPUs into the dispatch window.

### 04:05–04:35Z — INTERVENTION 5: ray advertised the HOUSE WIFI (cp-tp2 blocker #4)
Two-GPU gate PASSED (ray reports 2.0 GPU) — intervention 4 confirmed. Serve started,
loaded, then died after 10 min of rendezvous:
    torch.distributed.DistStoreError: Timed out after 601 seconds waiting for
    clients. 1/2 clients joined.          (failing worker at ip=10.42.0.2)
Coordinator interfaces:
    enp191s0      10.99.1.1/30    the wire
    thunderbolt0  10.99.0.1/30    the fast rail
    lo            10.99.9.1/32    the fleet identity
    wlp192s0      10.42.0.2/24    THE HOUSE WIFI   <-- ray picked this
Ray's node-IP autodetection chose wlp192s0. Ray itself stayed healthy (its traffic
reaches the head via the address the join dials — which is why the two-GPU gate was
happy), but vLLM builds the c10d TCPStore at ray's ADVERTISED head IP. The
rendezvous was published on the house LAN; the worker on 10.99.x had no route.
Only the local client joined.
FIX: commit 79f87fb — pin --node-ip-address on BOTH ray starts to the fleet /32s
(FN_HEAD_IP 10.99.9.1, FN_WORKER_HOST 10.99.9.2). Verified: flag exists in ray
2.55.1, 10.99.9.1 bindable on coordinator, worker reaches it in 0.098 ms.
NOT fixed via VLLM_HOST_IP: VLLM_-prefixed, so it rides the byte-compared doctrine
env AND must differ per node — it would fail the preflight byte-diff by construction.
201 tests green.

### 04:52Z — IMPORTANT MECHANISM NOTE (cost ~45 min if missed)
After landing 79f87fb at 04:18Z, the next cp-tp2 attempt dispatched at 04:50Z with
TALLY_WORKSPACE_BASE_REV=03cfdca — the PREVIOUS fix, not the new tip. The checkpoint's
prepared base is FROZEN AT PASS CREATION; retries WITHIN a pass replay the same base.
Landing a fix on the integration branch is NOT sufficient on its own.
REMEDY: kill the stale-base attempt (systemctl --user stop the tally-job unit), then
`tally campaign poll --once` to create a fresh pass. Verified the new attempt then
carried BASE_REV=79f87fb, matching the integration tip.
Check base rev on EVERY attempt after landing a fix:
    ps -eo args | grep -oP 'TALLY_WORKSPACE_BASE_REV=\K[0-9a-f]{40}' | sort -u
