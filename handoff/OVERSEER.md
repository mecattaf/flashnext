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

## Nix discipline (Tom's directive: nix-caused failure risk = zero)

1. **The one big build**: `nix build --cores 32 --max-jobs 1` — the host's
   global `cores = 8, max-jobs = 4` would give the HIP compile a quarter of
   the machine. Kick it the moment the fork is pushed; log wall-clock (it is
   UNKNOWN-6 and the night's unit of currency). Budget **at most 3 recompiles**.
2. **Never use `nix build` as the inner loop.** Iterate engine patches in a
   devshell with the built store path on PYTHONPATH plus a writable overlay
   dir; seal into a derivation only what already works.
3. **Gate 0 before anything**: the packaging expression's `--replace-fail`
   literals (six of them, incl. `"torch == 2.11.0"`) against the fork's
   bytes; the fork carries a substrate-compat commit that *satisfies* them —
   you cannot append around them.
4. **Stripped-deps audit**: the overlay drops 22 dep names (datasets,
   outlines, peft, timm, xformers, pyarrow, …). Grep the fork's new code for
   imports of any of them BEFORE the build window; a 3am missing-dep is a
   hard stop under nix.
5. **Mirror the substrate tarball tonight** (fixed-output rot): the pinned
   AMD nightly `therock-dist-linux-gfx1151-7.15.0a20260719.tar.gz` (1.75 GB)
   still answers 200 but nobody promises retention. Mirror to the NAS
   (`/mnt/nas/mirrors/`) with its sha256 recorded.
6. **Do NOT `nix flake update nix-strix-halo` anywhere** — it moves the
   engine onto a ROCm 10 SDK that has no gfx1151 torch wheels. The warm
   substrate is GC-rooted at `~/.cache/flashnext-rocm/` (vllm-rocm AND a
   realized ds4-rocm — the latter was actually built, not merely declared).
7. The dotfiles flake is un-evaluable in place (a committed unix socket) —
   never gate anything on `nix build ~/mecattaf/dotfiles#…`.
8. Fallback trigger: two nix-lane aborts on *different* causes, or one abort
   whose fix needs upstream-expression surgery → switch to the container lane
   (`container/build.sh`, kyuz0 recipe, torch 2.13 stable wheels) and record
   the pivot in the build receipt. The morning repo must still ship the nix
   packaging as the *intended* path with the abort documented.

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
