# Overnight overseer handoff — flashnext, 2026-08-28 → 08-29

*You are a fresh session taking the night shift. The operator (Tom) is asleep.
Your mission ends at one of exactly two states by morning: (a) the workload
serving at TP=2 on the pair with counterbalanced benchmarks committed, or
(b) a typed blocker in docs/MORNING.md naming the unresolvable cause, with
drafted upstream issue text under handoff/upstream-issues/. **"I couldn't
achieve X so I skipped it" and "preferred to wait for Y" are forbidden
outcomes.** If something fails: diagnose, steer, fix, or escalate to a typed
blocker — in that order.*

## Read first, in order

1. `README.md` — what this is.
2. `IMPORTS.md` — every pin, every cherry-pick, the torch story.
3. `specs/flashnext/spec.md` + `silent-factory-worklists/flashnext.json` —
   the contract you are shepherding.
4. `specs/flashnext/evidence/moe-dispatch.md` — why stock refuses this GPU
   (the central problem).
5. `specs/flashnext/evidence/ple-54129.md` — the exact AMD port spec
   (§"Exact port specification"), and the cudagraph guard subtlety below.
6. `specs/flashnext/evidence/nix-packaging-brief.md` — the nix lane: what is
   pre-paid, Gate 0, the mechanism (A+C), the six hazards.
7. `specs/flashnext/evidence/tally-fit-analysis.md` — how the harness maps
   onto this campaign.

## Your two hats

**Hat 1 — campaign overseer.** The tally campaign (adapter `pi` → qwen-max,
steward `narrator`) grinds the worklist. Observe with
`tally campaign status mecattaf/flashnext silent-factory-worklists/flashnext.json`,
steer with `tally campaign steer … --task <id> --message '…'`. Steering is
append-only and is the whole recovery path (epoch refresh). Never hand-edit a
lane's worktree, receipts, or the armed graph. **Every friction point with the
harness itself — a confusing failure, a missing verb, a gate that graded
wrong — goes into `handoff/TALLY-FINDINGS.md` with enough detail for Tom to
fix tally.nix.** He is heavily invested in that harness; findings are a
first-class deliverable of the night.

**Hat 2 — the hard-core engineer.** The fork engineering (admission patch,
PLE port, cherry-picks, nix wiring) was launched from the setup session as an
ultracode workflow (5 opus + 1 fable reviewer). If it completed: verify the
fork branch and `patches/` mirror are pushed, and move on. If it partially
failed: you own finishing it, with your own ultracode workflows. The exact
patch specifications live in IMPORTS.md §2.3 and the evidence dossiers —
do not re-derive them.

## The night's dependency spine

```
fork pushed ──► engine build (nix lane first) ──► smoke ──► proxy ──► TP=2 ──► bench
weights staging (independent, start early) ────────────────────┘
estate lanes (container fallback, host, bench, docs) — tally, in parallel
```

## Engine lane ruling (final, operator-confirmed): PODMAN TONIGHT, NIX AT GRADUATION

Tonight's engine is the **container** (`container/build.sh` → `flashnext:dev`,
Ubuntu 24.04 + AMD *stable* wheels `torch 2.13.0+rocm7.14.0` — the fork's
exact pin, published on the stable channel — fork built from source with a
bind-mounted, ccache'd build dir). Reasons, so nobody relitigates at 2am: the
stable channel satisfies the fork's real pin with no downgrade-on-faith; a
container iterates incrementally (shell in, edit, restart — one-line patches
rebuild only what changed) while nix charges a full few-hundred-kernel HIP
recompile per edit; and `pip install` at 03:00 closes the unbounded
missing-dep failure class. **Minute-zero expensive item: start the base image
pull + wheel prefetch immediately** (nothing is pulled yet; podman 5.8.4 and
/dev/kfd + /dev/dri access are verified clean).

The nix lane is the **graduation spec** — the fork-engineering workflow still
lands the flake wiring, the Gate-0 substrate audit, and `build-engine.sh`;
none of it is tonight's critical path. Ignore any workflow-report instruction
to kick `nix build .#vllm-fork` tonight. Graduation notes that stay true:
`--cores 32 --max-jobs 1` for the one big nix build; devshell inner loop;
the substrate tarball mirror (fixed-output rot — run
`scripts/mirror-substrate.sh` if the workflow didn't); **never
`nix flake update nix-strix-halo`** (moves onto a ROCm 10 SDK with no gfx1151
torch wheels); **never `nix-collect-garbage` tonight** — the warm substrate
is GC-rooted at `~/.cache/flashnext-rocm/`.
7. The dotfiles flake is un-evaluable in place (a committed unix socket) —
   never gate anything on `nix build ~/mecattaf/dotfiles#…`.
7b. **Never run `nix-collect-garbage` tonight**, and verify no cleanup step
   does — the warm substrate lives on the hand-made GC roots at
   `~/.cache/flashnext-rocm/`. One caveat to close cheaply: the realized
   `python3.13-vllm-0.25.1` store path's provenance was closure-checked but
   not lock-traced — confirm which lock built it before treating it as the
   warm baseline.
7c. **RDMA is a morning plan, never an overnight act** (operator ruling:
   "no unsupervised reboots, period"). The kernel half is entirely unpaid on
   this fleet (stock 7.1.4, no tbv module, `/sys/class/infiniband/` empty);
   the RDMA track may only *start* after a committed TP=2-over-TCP benchmark
   is banked, and its checklist's first step is moving the worker's deploy
   path off the fast rail (deploy-rs dials 10.99.0.2 over Thunderbolt with
   `-F /dev/null` — a worker rebooting into a bad-TB kernel severs its own
   deploy path). See `specs/flashnext/evidence/nix-hardening-addendum.md`.
8. If the *container* lane itself hits a wall (wheel resolution, fork build
   error), the fix lives inside the container: shell in, `pip install`,
   patch, ccache rebuild — iterate there, then seal the fix back into
   `container/Containerfile` and the fork branch. Do not pivot to the nix
   lane under pressure; it is strictly slower to iterate.

## Engine subtleties that will bite at 3am (all evidence-pinned)

- **VLLM_PLE_MMAP=1 refuses plain `--enforce-eager`** — its guard demands
  `VLLM_COMPILE` mode with PIECEWISE cudagraphs and the mmap op as a split
  boundary (`check_cudagraph_safety`, three clauses). First light runs that
  mode. Spec ruling P10 encodes this.
- **Residency is read after ≥50 warmed decode tokens, never at load**; read
  GTT (sysfs) + RSS + table `mincore` together; the pass bound is 80 GiB/rank
  with 0 table bytes GPU-resident.
- **Never export a vLLM env default**: `VLLM_USE_DEEP_GEMM`,
  `VLLM_MOE_USE_DEEP_GEMM`, `VLLM_ROCM_USE_AITER*` are read via `is_set()`
  probes — exporting the default *diverts the oracle into a hard raise*.
- **`_GCN_ARCH` one-liner first** inside the built engine:
  `python -c "import vllm.platforms.rocm as r; print(r._GCN_ARCH, r.on_cdna(), r.on_rdna4())"`.
- The oracle failure is **loud and at layer construction** — if a serve
  attempt gets past model construction, the admission patch is engaged; if it
  raises `No FP8 MoE backend…`, it is not. `VLLM_LOGGING_LEVEL=DEBUG` prints
  the per-backend elimination table.
- The two TP ranks must have **byte-identical FN_ env**
  (`VLLM_RAY_EXTRA_ENV_VAR_PREFIXES_TO_COPY=FN_` is what propagates them).
- Weights: coordinator source `/mnt/nas/models/weights/…`, worker source
  `/mnt/library/weights/…` (its own mount of the same library). 131 shards
  expected; download was in flight at handoff (~60G/173G at 22:30, fast).

## Hardware red lines

- **Never reboot either node** (spec F.13) — a dual-reboot has previously
  wedged the Thunderbolt PD controller, and recovery needs physical presence.
  A GPU wedge that survives process-kill + amdgpu recovery = typed blocker.
- Never touch RDMA (F.1). The PM QoS hold and tripwires are live; leave them.
- llama-swap owns :9292; the pair service uses its own ports.

## AMD ROCm.AI skills (shipped today — use them)

Install into this repo for your own session:
`npx skills add amd/skills --skill rocm-doctor --skill magpie-kernel-evaluator --agent claude-code`
— `rocm-doctor` for bring-up failures, `magpie-kernel-evaluator` for the
FP8-MoE Triton validation and the benchmark deliverable. Hyperloom is NOT for
tonight (Instinct-scoped optimizer; nothing to optimize until first tokens).

## Rough timeline (8h window, generous buffers)

| When | What |
|---|---|
| ~00:00 | fork verified pushed; Gate 0 clean; engine build kicked (`--cores 32`); weights staging running |
| ~01:30 | engine built (measure!); smoke receipt; proxy checkpoint |
| ~02:30 | TP=2 first light; residency; fidelity |
| ~04:00 | bench matrix (depths × spec on/off, 3 loads/arm, counterbalanced) |
| ~06:00 | ledger + ANNOUNCE numbers; close checkpoint; TALLY-FINDINGS written |
| buffer | every step has ≥2× slack; the order never inverts |

## The morning bar, verbatim from the operator

> "me coming to the desktop tomorrow with either a working and optimized
> [model] for my dual strix setup; or a real major blocker that was literally
> unresolvable or highlighted a major flaw. but not 'i couldn't achieve X so
> i skipped it' out of laziness or 'prefer to wait for Y to be done'. if the
> failure is due to an upstream problem, it ends with proposals for me to
> approve to write issues in the upstream repos."
