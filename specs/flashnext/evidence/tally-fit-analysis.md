# Tally-fit analysis — draft flashnext spec (final-report-pt2.md Part Two) vs the real machinery

*Written 2026-08-28 by the authoring session, from a full read of specs/README.md (v2), specs/constitution.md (v2), skills/{author-spec,assign-tally,campaign-operator}/SKILL.md, specs/zeta/spec.md, specs/eta/spec.md, silent-factory-worklists/theta.json, spec-lint --help, and doc/src/operating/fleet-deployment.md. Every item cites its source.*

## A. Structural collisions (must be resolved before authoring)

1. **Estate choice governs everything.** `spec-lint --root` resolves BELIEVE paths, backticked paths, and `specs/**` pointers against ONE working-tree root [spec-lint --help]. The draft spec BELIEVEs into three trees (dotfiles, ds4-vllm checkout in ~/Downloads, nix-strix-halo) with absolute paths — none resolve. The campaign must pick one estate repo; facts from other trees enter as committed files under `specs/flashnext/evidence/` and are cited as evidence ids, or the claim becomes [HUMAN-ATTENDED]. Candidates: (a) the new flashnext repo (fork kit + flake) — natural home for R3–R7; (b) dotfiles — natural home for R1–R2, but most of R2 is already DONE there (issues #238/#240/#237 closed and deployed 2026-08-28). Proposed: the new repo is the estate; residual dotfiles edits are operator hand-commits (prose/authority-path class, per the theta merge-doctrine ruling) or a 2-line module import.

2. **Gate count: draft binds 23 distinct [gate:] ids; the cap is 16** ("1–16 command or forbidPaths gates", assign-tally §Write the worklist; L9 requires every [gate:] id to resolve in the governing worklist). Draft ids: m2-enumerate, fio-census, models-mount, coldwarm-ab, pair-substrate, tb-rtt, deploy-5gbe, pair-check, artifact-hashes, gguf-master, gguf-requant, mtp-sidecar, interim-bench, engine-build, fp8-proxy, tp2-first-light, tp2-residency, fidelity-suite, tp2-bench, allreduce-cost, table-sweep, promotion-bench, roster-flip = 23. Also note gates are the campaign-wide merge ladder — they run on EVERY lane merge, so a 2-hour TP=2 bench as a gate would grade every doc commit. Most draft "gates" are really per-task acceptance criteria (argv on the task) or stage checkpoints (kind: checkpoint). Restructure: keep a small ladder (lint/build/unit-ish, plus a pair-health preflight), move measurement protocols into task acceptanceCriteria + checkpoint argv, and bind the corresponding claims to the checkpoint gate ids or [HUMAN-ATTENDED]. The trace join (claim → task → acceptance id) carries the fine-grained mapping — that is what trace.json is FOR.

3. **Pools are host configuration, not worklist bytes.** assign-tally: "Do not put … pool names in it"; worklist schema is closed (A2). Pools exist as `services.tally.pools.<name> = { resource; capacity }` in the NixOS/HM module [doc/src/operating/fleet-deployment.md:28-58] with cooperative enforcement [faq.md:137-139], consumed by *flows* jobs, not campaign tasks. The draft's P15 (fetch-wan, nvme-*, gpu-*, gpu-pair with capacity 1) cannot be expressed in the worklist. Campaign-side serialization is `maxParallel` + `conflictDomains` (path-based); real GPU exclusivity across the pair is either maxParallel:1 for the bench stages or host pools if the work runs as flows. P15 must be re-ruled.

4. **The spec-machine boundary:** the machinery never writes spec.md or the worklist (A2); ratification is keyboard-only; a lane must never write `specs/flashnext/**` (Forbidden line, matching zeta F.3). The governing spec appears in no task's conflictDomains (assign-tally).

5. **Claims must be re-authored against the observed tree (A12).** Since the draft was written (earlier today), a parallel session landed and deployed: #238 lowlat-cluster module + fleet-latency tripwire live on BOTH boxes (worker reboot-tested; coordinator switch done, reboot untested); #240 ssh/deploy repoint to fleet identity 10.99.9.2 over 5GbE, metric 20, NFS mount gated on nas:2049; #237 ROCm SDK evicted. Consequences:
   - Draft claims 2.1–2.9 are largely landed behavior → they become Unchanged lines bound to existing oracles, or drop. The pair check (2.10–2.11 strix-pair-check CLI) and lockstep flake check (2.12–2.13) existence must be confirmed by the dotfiles census dossier.
   - P8 is stale: the repoint went to 10.99.9.2 (fleet identity), not 10.99.1.2.
   - The two BELIEVE lines into /tmp/claude-.../lowlat-cluster.nix (draft defects) are moot — the module landed; the scratchpad file is not authority (A1).

6. **The model-drives premise is contradictory in the record.** Part One §"decision already made": two 1 TB DRAM-cached drives into M.2 #2; final-qwen-report.md §6 (the decision doc): "Buy nothing yet. Measure first." — fio census on the existing SN7100s first, purchase only on thrash signature or BF16-table adoption. The freshest state (README 21:49, handoff) plans serving from existing drives + NAS staging and does not mention a purchase. A13 (one frozen scope, no conditional lanes) forces this to a DECISION line pre-ratification: either the campaign includes drive install claims or it excludes the purchase entirely (F.7 then reads "Do not purchase hardware", full stop). Proposed: exclude the purchase; keep the fio census + cold/warm A/B on existing hardware as the deciding evidence; a purchase, if the census forces it, is a successor amendment at a sitting.

7. **Numerals & grammar mechanics to carry:** every non-crossref numeral on claim lines needs (given)/(GUESS) or a BELIEVE carrier [L7]; `e.g.`/`etc.` banned document-wide [L6]; hedge lexicon banned in Claims/Unchanged/Forbidden [L5]; backticked identifiers must exist in tree/Vocabulary/(NEW) [L8]; Forbidden last, verb-first [L1/L4]; omission only via `Omitted: <reason>.` [L15]. L16 bans host-catalog model names in spec/worklist bytes — the workload name (Qwen…) is P2-ruled as a workload artifact; verify against spec-lint's lexicon before relying on this (check crates/spec-lint/src/lexicon.rs at authoring time).

8. **[check:] bindings must resolve in the ESTATE flake's checks set** [L9]. Draft's `checks.x86_64-linux.strix-pair-lockstep` lives (if anywhere) in dotfiles, not the new estate. Either the estate flake grows its own checks (flake-build of the fork, patch-verify check, spec-lint if adopted) or those claims re-bind.

9. **Sitting/lifecycle to plan for:** S1 boundary sitting authors the worklist + trace sitting rows + census report in ONE commit [author-spec §Sit]; ratification only at zero outstanding doubt [L10]; falsity pass (hand numbered claims to a fresh reader) before ratification; A22 freeze after. The S1 sitting also drains UNKNOWN-1 (Q0 dispatch) — tonight's PR dossiers should drain it EARLY, before authoring, which is exactly what the evidence sweep is doing.

10. **Gate rehearsal reality:** every preflight/gate/checkpoint argv must be rehearsed verbatim in a pristine worktree on the target host (assign-tally §Rehearse admission). Gates that ssh to the worker need the fleet identity + key availability from the tally user's context — a real constraint to design into gate argv (or run gates as the operator's user daemon). To settle at S1.

## A-bis. The L16 discovery (verified in source, changes the whole naming layer)

`crates/spec-lint/src/lexicon.rs:31-35` — `MODEL_NAMES` includes **"qwen", "llama", "deepseek"** (plus claude/gpt/opus/sonnet/haiku/fable/gemini/mistral/grok/chatgpt). `lint.rs:1174-1189` applies it to `line.trimmed()` — every raw line of spec.md AND the governing worklist; matches fire inside hyphenated identifiers (`lexicon.rs` test: `raw/instinct-fable.md` → fable). Severity blocking (`rules.rs:172-175`).

Consequences for flashnext:
- The spec/worklist can never write "Qwen3.8-Flash-Next", `qwen38-flash-next-fp8`, `llama-swap.nix`, "llama.cpp", "llama-server", "ds4"→fine but "deepseek"→banned. The draft's own title line trips it.
- The draft's P2 ruling anticipated the rule but the draft's bytes violate it in ~12 places (title, vocabulary rows for engram tensor names, roster, interim service, BF16 master conversion, MTP sidecar recipes).
- Fix pattern: neutral vocabulary terms ("the workload checkpoint", "the interim engine", "the roster module"), and **name node-side artifact paths ourselves**: the catalog row and /var/lib/models directory for the checkpoint can be `flashnext-fp8` (our naming authority), so acceptance argv stays cleanly greppable. The NAS staging dir keeps its name; only spec/worklist bytes are constrained.
- The HF tensor name `ple.ple_embedding.ngram_embedding.weight` and GGUF `per_layer_token_embd.weight` are safe (no lexicon hit). `strix-halo-llamacpp` is NOT safe (llama). `EngramHalo.cpp` is safe.

## B. What the draft got right (keep)

- Section order, Status block shape, P/R id namespaces, verb-first Forbidden lines, typed UNKNOWNs, (given) sourcing style — all house-conformant.
- The Outcome narrative and the claim decomposition R1–R7 map cleanly onto stages S1–S5.
- P2 (workload vs host-catalog model names), P14's honest deviation note (bind-to-not-yet-passing oracles recorded at the sitting), P18's warmed-decode rule.
- The Forbidden set F.1–F.16 is strong; extend with: never write specs/flashnext/** from a lane; do not add worklist keys.

## C. Open decisions queue for Tom (to become DECISION-n lines or rulings)

1. Estate repo for the campaign (proposed: the new flashnext repo; name TBD by Tom).
2. Model-drive purchase in or out of scope (proposed: out; census on existing drives stays in).
3. Gate ladder vs acceptance-criteria split (proposed: ladder ≤8 gates: patch-verify, flake-build, container-build, pair-health preflight, unit/fidelity fast suite; heavy measurements as checkpoints + task acceptance argv).
4. Pool realization (proposed: maxParallel 1 through S2–S5; host pools only if flows are used for fetch/requant).
5. Whether the interim GGUF lane (R4, Nathan v0.7.1 single-box) stays in scope for the campaign or is operator-manual — the handoff's "no interim product" language vs the draft's R4. NOTE the tension: A5.A14 absence-over-prohibition vs the record's Stage A. Tom's latest framing ("no interim product and no fallback in this plan") suggests R4 shrinks or leaves; but final-report-pt2 P16 keeps it as fallback tier. Needs his call.
6. Where the Q0 upstream contribution PR lands relative to the campaign (HUMAN-ATTENDED claim as drafted — keep).
