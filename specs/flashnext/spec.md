# flashnext — the vendor FP8 release at tensor-parallel two on the coordinator/worker pair

Status: proposed
Governs: silent-factory-worklists/flashnext.json
Consumers: the flashnext worklist readFirst anchors; the S1 boundary sitting; the S3 morning review
Supersedes: none

## Outcome

Today the pair serves nothing of this class: the stock engine refuses the FP8
mixture layer on this GPU generation, the platform tree cannot load the FP8
lookup table, and no container, units, or benchmarks exist for the pair. After
this campaign the estate carries a fork whose two patches admit the mixture
path and port the disk-backed table onto the platform tree, a container built
from that fork at an exactly pinned wheel set, host tooling that stands the
two-node service on both rails with the wire as control, staged weights on both
nodes, and a witnessed measurement record. Overnight checkpoints prove first
light on a proxy checkpoint, first light on the pair, warmed-decode residency,
a fidelity baseline, and counterbalanced benchmarks, each leaving a receipt the
ladder verifies. The morning operator arrives to a serving pair with numbers,
or to a typed blocker naming its upstream cause with a drafted issue — never to
a silent skip.

## Vocabulary

- pair — the hosts coordinator and worker, two identical 128 GB unified-memory
  desktops on the same fleet substrate.
- rails — the interfaces thunderbolt0 and thunderbolt1, the inference plane,
  socket transport only; one rail is cabled TB5 and one TB3, both trained at
  40 Gb/s in the measured record, re-read at first light.
- wire — the interface enp191s0, the control plane: ssh, orchestration,
  staging.
- workload checkpoint (NEW) — the vendor FP8 release at upstream revision
  970c569adaca6b35532111fd6b27351b2baefe50; node-side artifact directory
  /var/lib/local-models/flashnext-fp8; full naming lives in README.md and the
  evidence, never in this file or the worklist.
- engram table — the 51.2-billion-parameter hash-lookup embedding inside the
  workload checkpoint, read once per token at one layer.
- fork (NEW) — the engine fork published as github mecattaf/vllm branch
  flashnext; base commit 8e4e036a311604800334989485b4ee23925956da, the
  table pull request's head, which already carries the model-support pull
  request's model code.
- admission patch (NEW) — the fork change that admits the block-FP8 mixture
  scheme on this GPU and adds the in-kernel upcast, switch FN_FP8_MOE.
- table port (NEW) — the fork change that wires the mmap table path and the
  FP8 embedding stack into the platform tree the pair runs.
- mmap path (NEW) — the disk-backed table mechanism behind VLLM_PLE_MMAP:
  page-cache faults serve gathers, zero table bytes GPU-resident.
- proxy checkpoint (NEW) — a small synthetic checkpoint with the same
  architecture identifier and expert format, for single-node first light
  before the real weights are touched.
- container (NEW) — the image flashnext:dev built by container/build.sh from
  the fork at the ruling P4 wheel set.
- pair service (NEW) — the orchestration under host/ standing both ranks:
  container up on both nodes, distributed head and worker, then the serve
  process at tensor-parallel two.
- receipt (NEW) — one JSON record under results/receipts written by an
  overnight step; the ladder gate validates every receipt against its bounds.
- bench matrix (NEW) — the counterbalanced measurement protocol of ruling P12.
- fidelity baseline (NEW) — reference losses and frontier logits captured at
  first light, the yardstick for every later change.
- morning ledger (NEW) — docs/MORNING.md, rendered overnight: receipts, open
  items, promotion checklist, blocker entries.

## Rulings

| id | decision | ruling |
|---|---|---|
| P1 | identity and timing | identity flashnext; the worklist and every gate id below are authored at this S1 sitting; the Governs line resolves before ratification |
| P2 | workload naming | model-family names stay out of spec and worklist bytes (lint L16); the workload is named by revision and by the node-side artifact directory; full names live in README.md and evidence |
| P3 | fork base | the table pull request's head is the base — it carries the model-support pull request's model code; the model-support branch's later head commit is CI-only and is not taken |
| P4 | wheel pin | torch 2.13.0+rocm7.14.0 with torchvision 0.28.0+rocm7.14.0 and the matching triton from the vendor stable index — the fork's own torch pin satisfied exactly; no nightly; audio extras dropped rather than the pin lowered |
| P5 | admission shape | the admission extends the upcast mechanism of upstream pull request 52970 to the fused-mixture oracle; refusal stays loud; FN_FP8_MOE=0 restores the stock refusal; no silent fallback is added |
| P6 | cherry-picks | upstream pull requests 46012, 40963, 51511, 46110 enter the fork with Cherry-picked-from trailers; 44331, 46186, 46676 receive no task in this campaign and are recorded in IMPORTS.md for the four-bit lane |
| P7 | transport | socket transport on both rails, both named to the collective library; RDMA receives no requirement, no task, no code path |
| P8 | weights | staged library-to-node by scripts/stage-weights.sh with a per-file digest manifest; the fleet catalog row is a morning operator act prepared under handoff/; runtime hub downloads stay forbidden |
| P9 | unattended overnight | GPU checkpoints run unattended; a wedge needing physical presence ends the campaign with the blocker typed in the morning ledger; no overnight reboot of either node |
| P10 | first light mode | eager execution first; graph capture modes are morning work |
| P11 | residency verdict | read after 50 warmed decode tokens on both ranks, never at load; GTT, RSS, and table page-cache residency recorded; pass bound 80 GiB per rank with zero table bytes GPU-resident |
| P12 | bench protocol | three loads per arm, interleaved arms, medians, token fingerprints; depth series 0, 10240, 102400; speculative on and off arms; rows committed under results/ |
| P13 | promotion | a morning human act; nothing overnight changes any fleet roster or default |
| P14 | oracle provenance | the gate ids below are created in the governing worklist at this sitting; heavy overnight proofs bind through receipts validated by the receipts gate, and the trace joins each claim to its producing checkpoint |
| P15 | compute routing | campaign adapter pi with the model unset — the host catalog answers; the fork engineering is estate bootstrap authored before arming and reviewed in-session |
| P16 | blocker protocol | an unresolvable upstream defect ends as drafted issue text under handoff/upstream-issues plus a morning-ledger entry, never as a silent skip |

## Claims

### R1 — provenance and the public face
Why: every downstream judgment leans on the evidence corpus, and the repository is public from birth; a dangling citation is a fabricated fact.
1.1 the seven sweep dossiers land under `specs/flashnext/evidence/` → the tests suite counts at least 7 (given) dossiers. [gate: repo-tests]
1.2 `IMPORTS.md` names every external artifact with source, revision or pull-request id, license, and role → the notices file covers the same set, asserted by the tests suite. [gate: repo-tests]
1.3 `README.md` states the project, the topology, and the evidence trail → the tests suite's secret scan passes on the published tree. [gate: repo-tests]

### R2 — the fork
Why: the fork is the only path this class of workload can take on this GPU; its content must be provable from the estate, not narrated.
2.1 BELIEVE:IMPORTS.md — the fork base is the table pull request's head commit 8e4e036a311604800334989485b4ee23925956da → the fork-verify gate confirms base ancestry on the published fork branch. [gate: fork-verify]
2.2 the admission patch lands on the fork → the verify script finds the FN_FP8_MOE switch and the upcast plumbing in the fused-mixture layer tree. [gate: fork-verify]
2.3 the table port lands on the fork → the verify script finds the mmap import, the FP8 dequant, and the relocated mmap module in the platform tree. [gate: fork-verify]
2.4 the four ruling P6 cherry-picks land on the fork → the verify script finds each signature. [gate: fork-verify]
2.5 every fork commit past the base is mirrored under `patches/` → the verify script counts mirrored patches equal to fork commits. [gate: fork-verify]

### R3 — the container
Why: the pinned wheel set and the fork meet for the first time inside the image; the smoke step answers the cheap questions before any weight byte moves.
3.1 `container/build.sh` builds the image from the fork at the ruling P4 wheel set → a build receipt records status pass with the resolved torch and triton versions. [gate: receipts-verify]
3.2 `scripts/run-smoke.sh` runs against the image → a smoke receipt records the GPU architecture string, a finite fp8 storage cast, the registered architecture identifier, the aperture, and the admission verdict. [gate: receipts-verify]

### R4 — engine proof
Why: nobody anywhere has run this workload on this GPU at tensor-parallel two; each step below converts one unknown into a receipt.
4.1 the proxy checkpoint serves on one node with the mmap path engaged → a proxy receipt records finite output and a clean shutdown. [gate: receipts-verify]
4.2 the pair service reaches first light eager over both rails → a tp2 receipt records a greedy 300 (given)-token completion byte-identical across two (given) runs and the per-rail link speeds. [gate: receipts-verify]
4.3 residency is read per ruling P11 → a residency receipt lands inside the per-rank bound with zero engram table bytes GPU-resident. [gate: receipts-verify]
4.4 the fidelity baseline lands → reference losses and frontier logits are stored under `results/` beside their receipt. [gate: receipts-verify]
4.5 context rises to 262144 (given) → a context receipt records decode within 10 (given) percent of the short-context figure. [gate: receipts-verify]

### R5 — weights
Why: the pair serves the workload checkpoint from its own drives; staging is verified movement, not trust.
5.1 `scripts/stage-weights.sh` places the workload checkpoint on both nodes → weight receipts record at least 131 (given) shards per node with byte totals matching the library source. [gate: receipts-verify]
5.2 the fleet catalog patch is prepared under `handoff/` → the receipts gate parses it as a well-formed patch when present. [gate: receipts-verify]

### R6 — service and measurement
Why: a number without its protocol is an anecdote; the harness and its client are graded before any number is quoted.
6.1 the pair-service tooling lands under `host/` → unit files carry a stop-post teardown and no boot-time install, asserted by the tests suite. [gate: repo-tests]
6.2 the bench client separates queue wait from prefill → a client unit test with injected queueing passes in the tests suite. [gate: repo-tests]
6.3 the bench matrix runs per ruling P12 → a bench receipt records three (given) loads per arm, counterbalanced, with token fingerprints, and rows committed under `results/`. [gate: receipts-verify]

### R7 — the morning and the blocker protocol
Why: promotion is a human act and the campaign's failure mode is typed, never silent.
7.1 the morning ledger renders overnight → the operator reads receipts, open items, and the promotion checklist in one file. [HUMAN-ATTENDED]
7.2 an overnight step fails for an upstream cause → drafted issue text lands under `handoff/upstream-issues/` and the morning ledger names the blocker. [HUMAN-ATTENDED]
7.3 the morning operator reviews receipts and ledger at a keyboard → a promotion or blocker disposition is recorded, the catalog patch applied on promotion. [HUMAN-ATTENDED]

## Unchanged

U.1 the GTT page ceiling stays 33554432 (given) pages on both nodes → the smoke receipt records that value. [gate: receipts-verify]
U.2 the latency hold and its tripwire stay armed on both nodes → the tp2 receipt records held sub-budget round trips on both ends. [gate: receipts-verify]
U.3 runtime hub downloads stay forbidden → the tests suite rejects any worklist bytes carrying a hub-download flag. [gate: repo-tests]

## Unknowns

UNKNOWN-1 whether the fused-mixture block-FP8 kernel is numerically sound on this GPU once admitted — drained by claims 4.1 and 4.4.
UNKNOWN-2 whether fp8 storage plus the widening cast works on the pinned torch build — drained by claim 3.2.
UNKNOWN-3 whether the collective library crosses the pair at tensor-parallel two over socket transport — drained by claim 4.2.
UNKNOWN-4 the fork's runtime behavior on the ruling P4 wheel set — drained by claims 3.1 and 4.1.
UNKNOWN-5 speculative-decode acceptance on real prompts and the draft-length optimum — recorded by the bench receipt; tuning is morning work.
DECISION-1 does the container carry the aiter library? proposed: no — the admission path is self-contained and the aiter half of the pattern donor stays out (given)

## Stages

### S1 — bootstrap
This sitting: estate skeleton, evidence corpus, fork engineering, worklist
authored, both repositories published. Claims R1, R2; rulings P1 to P16.

### S2 — overnight
Order: weights staging beside container build, then smoke, then proxy, then
pair first light, then residency and fidelity, then the bench matrix; the
service and measurement tooling lands ahead of the checkpoints that call it.
Claims R3, R4, R5, R6; rulings P4 to P12.

### S3 — morning
Order: ledger review, disposition, catalog patch on promotion, then the
optimization menu in the morning ledger. Claims R7; ruling P13.

## Forbidden

F.1 Never bring RDMA up on either rail.
F.2 Do not import the upstream tuning module into any host closure.
F.3 Do not add keys to the worklist schema.
F.4 Never write under specs/flashnext/ from any lane.
F.5 Do not put model-family names in spec or worklist bytes.
F.6 Do not download weights at runtime.
F.7 Never decide the residency verdict from load-time readings.
F.8 Do not commit a benchmark number from a single uncounterbalanced run.
F.9 Do not export an environment default the engine reads through an is-set probe.
F.10 Do not vendor GPL code into the estate.
F.11 Do not adopt a nightly wheel while the stable set satisfies the torch pin.
F.12 Never record an overnight step as skipped without a typed blocker in the morning ledger.
F.13 Do not reboot either node overnight.
F.14 Do not open any upstream pull request or issue from a lane.
