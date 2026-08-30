# The innovation ledger — what flashnext lifts from where, and the final desired state

*Written 2026-08-28 ~22:30, after a seven-agent source sweep over both vLLM PR head
branches (full checkouts), ds4-vllm at `a8f620d`, the kyuz0 lineage (fetched to today's
ROCm 10 release), nix-strix-halo at `f0f2048`, and the committed dotfiles at `7f1072e2`.
Full evidence with file:line pins for every claim: [`evidence/`](evidence/) — seven
dossiers, ~1.1M tokens of agent work condensed. This document supersedes the
corresponding sections of `style-transfer-map.md` and `final-report-pt2.md` where they
conflict; conflicts are marked **[CORRECTED TONIGHT]**.*

*This defines the **final desired state** of the model on this specific dual-Strix
setup — not an MVP, not a staged demo. Everything below is the destination; the tally
campaign is the vehicle.*

---

## 0. What tonight's sweep changed — read this first

Four load-bearing re-scopings, all verified from source at the actual PR heads
(`vllm-pr53896` @ `89d0bb7`, `vllm-pr54129` @ `8e4e036`):

| # | The record believed | What the source says | Consequence |
|---|---|---|---|
| 1 | PR #53896 likely inherits the two QSA reference bugs (dense-behind-a-mask, Python-loop top-k); fixing them via `ds4_topk.py` + `ds4_tl_indexer.py` is "the highest-value adaptation available" | **Neither bug exists.** The PR is a from-scratch paged rewrite: the sparse kernel gathers only the top-k KV rows (loop bounded by TOPK, never kv_length), the indexer top-k is a GPU op (`top_k_per_row_decode` on ROCm), zero `.item()`/`.cpu()` in the path, and a purpose-built `amd/` tree ships in the PR itself with ROCm-specific kernels (LDS-aware split-K, portable RMSNorm, no `tl.dot` in scoring). No hadamard/QAT transform exists in Qwen's indexer graph — the DS4 wrong-token-selection trap has no analogue here. [`evidence/qsa-53896.md`] | **The entire QSA adaptation workstream is deleted.** `ds4_topk.py`/`ds4_tl_indexer.py` drop from ADAPT to REFERENCE. |
| 2 | The `triton.next_power_of_2` routing fix is mandatory for top-10 | The model routes through vLLM's own `FusedMoE → FusedTopKRouter → fused_topk → topk_softmax` — **no `triton_kernels`, no power-of-2 constraint anywhere on the path**. The pow2 fix is also already upstream for the paths that need it. [`evidence/qsa-53896.md` §4] | **The routing-fix workstream is deleted.** |
| 3 | The risk is a silent ~125 GiB BF16 twin of the experts killing TP=2 | **Stock vLLM cannot start this model on gfx1151 at all**: `RocmPlatform.supports_fp8()` is `on_cdna() or on_rdna4()` → False for gfx1151, so the FP8 MoE oracle exhausts all 14 backends and raises `NotImplementedError` loudly **at layer construction, before any weight loads**. No reachable code path in stock upconverts block-FP8 expert weights to BF16 — the feared `cached_bf16_weight` hook exists only in the DS4 patch, and even there covers linear layers, never experts. [`evidence/moe-dispatch.md`] | **The central engineering problem is now precisely known and is ours to build: admit gfx1151 into the FP8 MoE oracle with a working Triton path.** Nobody on earth has this; it is also the highest-value upstream contribution. |
| 4 | Q0 is a "~15-line" `amd/ple_layer.py` mmap wiring port | The port is **80–150 lines**, because `amd/ple_layer.py` has **no FP8 handling at all** — no `weight_scale` interception, no dequant, embedding constructed without `quant_config`. On this FP8 checkpoint the stock AMD path either dies loudly on the orphan `ngram_embedding.weight_scale` tensor or bare-casts the table to bf16 **discarding the block scale** (numerically wrong) and doubling its bytes (23.9→47.7 GiB/rank — which breaks the 96 GiB aperture arithmetic). [`evidence/ple-54129.md`, `evidence/moe-dispatch.md` §5] | **AMD FP8-PLE support is enablement, not headroom** — contrary to Addendum B.1. The mmap port (which is FP8-native and skips the resident table entirely) is the *cheapest correct* fix, not a luxury. |

Also settled tonight: gfx1151's `fp8_dtype()` is `torch.float8_e4m3fn` (OCP, matching
the checkpoint's `F8_E4M3`) — no fnuz relabeling hazard [S, `rocm.py:976-985` at PR
head]. And the two PR branches have **already diverged** on six shared files
(`amd/{model,mtp}.py`, `nvidia/{model,mtp}.py`, `envs.py`, `compilation.py`) — the fork
assembly is a real merge with known conflicts, not a clean stack.

---

## 1. The final desired state

**One configuration.** `Qwen3.8-Flash-Next-FP8` (172.8 GiB, 131 shards, checkpoint
`970c569`) served by a vLLM fork at TP=2 across `coordinator` and `worker`, full 262,144
context, FP8-class precision end to end: experts block-FP8 read natively by the Triton
MoE kernel (1 byte/param off memory — ideal for a 220 GB/s-bound machine), attention/
GDN/trunk bf16 as shipped, engram table served from each node's NVMe via `VLLM_PLE_MMAP`
(0 bytes GPU-resident, ~1.5 GiB warm page cache), KV cache at full context, in-checkpoint
MTP speculative decoding on. Weights live on both nodes (NAS → node, catalog-declared,
hash-verified). Then optimized from there under the DS4 method — with the 4-bit expert
requant (int4 = the only above-fp16 primitive on this silicon) as the one named future
optimization, not a prerequisite.

**Topology (Tom's ruling, encoded):**
- **Both Thunderbolt rails are the inference plane** — RCCL over TCP,
  `NCCL_SOCKET_IFNAME=thunderbolt0,thunderbolt1`, PM QoS held both ends (live today at
  77 µs avg), fleet-latency tripwire armed (live today). Never RDMA on either rail in
  this campaign (excluded scope — sanctioned by DS4's own "RDMA is a performance layer,
  not a correctness gate", and by three independent measurements that the wire is not
  the TP=2 limiter). Jumbo MTU is implemented-but-off in the committed lowlat module;
  turning it on is a both-ends two-step deploy with its own A/B, an optimization-stage
  item.
- **5 GbE is the control plane** — ssh, ray control traffic pinning, health gates,
  NFS staging. Fleet identities 10.99.9.x on lo; committed route metrics already prefer
  the wire (metric 20 vs TB's imperative 50).
- **Known residual doctrinal split** [`evidence/dotfiles-observed.md` §7.1]: deploy-rs
  still dials the worker at 10.99.0.2 over Thunderbolt (`flake.nix:536` + two guarding
  asserts) — #240 moved the ssh nickname and metrics, not the deploy target. Left as a
  dotfiles decision outside this campaign's scope.

**Serving stack, concretely:**
- Fork: `mecattaf/vllm` branch `flashnext` = merge(#53896, #54129) + our patch set:
  (a) gfx1151 FP8-MoE oracle admission, (b) the AMD PLE port (mmap wiring + FP8
  embedding stack), (c) instruments and kill-switches. Every patch in the fork is also
  carried as a reviewable `.patch` in the flashnext repo with a MANIFEST and a
  verify script proving fork = merge-base + patches (the ds4-vllm discipline, including
  its 12-invariant packaging test suite — extended with the new-file line-count check
  that ds4's own suite skips).
- Container: image built from the fork source with kyuz0's vllm-toolboxes recipe
  (MIT; Ubuntu 24.04 + AMD stable wheels rocm7.14.0/torch2.11.0 — the newest published
  gfx1151 vLLM substrate; **no ROCm 10 vLLM exists or can exist yet** — AMD has
  published no ROCm 10 gfx1151 torch wheels; today's kyuz0 ROCm 10 release is the
  GGUF/llama.cpp lane only [`evidence/kyuz0-rocm10.md`]). Torch pin relaxed at build
  (fork pyproject says `torch == 2.13.0`; the recipe already handles this class of pin).
- Host: podman + ray head/worker + `vllm serve --tensor-parallel-size 2` as systemd
  units in the flashnext repo's NixOS module, composed from the committed dotfiles
  idioms (gate-on-reality oneshots like `library-reachable`, the tripwire submodule,
  the both-ends import-is-the-gate pattern). No boot-time autostart (DS4's ruling:
  explicit start only, `ExecStopPost` teardown so failed bringups can't strand ray).
  Firewall admissions for ray/NCCL ports scoped to the twins' rails only.
- Weights: catalog row `flashnext-fp8` (snapshot layout, both hosts) in the dotfiles
  catalog → NAS `library-fetch` → `local-models-sync` to `/var/lib/local-models` on
  both nodes, hash-verified per shard. Runtime `-hf` downloads stay forbidden by the
  committed assertion. (Node-side artifact name deliberately avoids the upstream string:
  tally's spec-lint L16 bans model-family names in spec/worklist bytes, so acceptance
  argv must be able to name the path without tripping it.)
- Flake: `mecattaf/flashnext` — definition of done. Outputs: the container build,
  the pair-service NixOS module, the bench/fidelity harness, checks (patch-verify,
  packaging invariants, module eval), devshell. `nix-strix-halo` stays a flake *input
  only* (no LICENSE upstream — nothing may be copied; its `tuning` module stays
  forbidden — it would silently fight our 128 GiB `ttm.pages_limit` with its 80 GiB).

**Verification planes (the Appendix E discipline, mechanized):**
every mechanism ships a state probe, a kill-switch, and a quality number —
enumerated per mechanism in §4.

---

## 2. Per-repo lift ledger

### 2.1 `AlexKGwyn/ds4-vllm` @ `a8f620d` (Apache-2.0) — the discipline donor

**[CORRECTED TONIGHT]** No longer the *code* donor it was mapped as (QSA files and
routing fix deleted from scope; the "commit 671e659 eager-break" cherry-pick does not
exist in the tree — `MANIFEST.md:65` prose describes base-image behavior). What we lift:

| Lift | What it is | Class |
|---|---|---|
| **Rebuild-kit distribution discipline** | Digest-pinned base + `container/patches/` overlay + `MANIFEST.md` (8 themed tables, Δ-audited) + `verify-patches.sh` (base→patched proof, `--write` regeneration mode) + `tests/test_patchset_packaging.py` (12 invariants, run before every build; we add the 13th: new-file `wc -l` audit — 3/12 of ds4's own counts are stale) | COPY + extend |
| **Host orchestration shape** | `ds4-config.yaml` (flat, stdlib-parsed) → env; cluster restart with husk-reaping (`podman exec` wrappers strand processes), ray worker-pool capping (`RAY_NUM_CPUS=4`), 2-GPU gate before serve, API poll, warmup unit; idempotent teardown; `ExecStopPost` teardown on failed starts; `Conflicts=` against the single-box roster | ADAPT (Apache-2.0, copy freely) |
| **The env doctrine, item by item** | `PYTHONHASHSEED=0` (prefix-cache chain seed), `PYTORCH_HIP_ALLOC_CONF=expandable_segments:True,garbage_collection_threshold:0.85`, `HSA_ENABLE_INTERRUPT=1` (ROCr busy-poll → 2-3 cores of thermal throttle), Triton/Inductor cache dirs off tmpfs (~25 min LLVM recompile otherwise), `VLLM_RAY_EXTRA_ENV_VAR_PREFIXES_TO_COPY=FN_` (without it the two TP ranks silently diverge — we rename the prefix), byte-identical env on both ranks as a hard gate | ADAPT |
| **Serve-flag wisdom** | Pin `--kv-cache-memory-bytes` (util knob is INERT while pinned); `--max-num-batched-tokens` sized against indexer workspace; the `#`-in-continuation landmine; `--enforce-eager` first light, graphs later | ADAPT |
| **Instruments** (§4) | `ds4_synctrace.py` (93 ln), `ds4_expert_union.py` (128 ln, re-targeted: its wrap point is the gpt-oss triton_kernels path — Qwen routes through `FusedMoE`, so the choke point moves), `ds4_offload_batch.py` (154 ln, fixes a live upstream defect, still applies) | VENDOR w/ notice + adapt |
| **Kill-switch grammar** | 40+ `DS4_*` vars enumerated in the dossier; the pattern: tuned path default-on, per-path env off-switch, latched once at import, propagated via the ray prefix | COPY-METHOD |
| **AGENTS.md bring-up doctrine** | §0.1 "verify these exist; do NOT synthesize them"; gates in order with pass conditions; the de-risk clause | COPY + rewrite for our stack |
| **THIRD_PARTY_NOTICES structure** | "Nothing redistributed" rows, pins cross-checkable against build scripts; with `tbv/` excluded our tree carries zero GPL | COPY |
| NOT lifted | `tbv/` (RDMA, GPL boundary), `tbv_ar*` all-reduces **[licence ground CORRECTED 2026-08-31: `tbv_ar2.hip` is Apache-2.0 per ds4's THIRD_PARTY_NOTICES, not GPL — see RUN3-BRIEF §4.5; it stays unlifted on scope grounds only]**, DSpark drafter plumbing, the QSA files (now reference-only), disk-KV tier (deferred — not in the campaign's first scope), `MANIFEST.md:8` (stale — contradicts the Dockerfile) | — |

### 2.2 vLLM PR #53896 (peakcrosser7, `release/qwen38next`) — the model, better than hoped

The whole model tree: `common/` (hyperconnection, PLE math, QSA cache), `nvidia/` +
`amd/` platform trees with a single `__getattr__` dispatch on `current_platform.is_rocm()`,
QSA as genuinely sparse paged attention, MTP integrated with vLLM v1 spec-decode
(including `skip_topk` index-reuse on speculative steps — the indexer runs once, drafts
reuse its selection), EPLB plumbing, and a test suite whose ROCm tests pin *which op* is
called (structural guards, not just numerics). **Lift verdict: this is the fork base's
crown; our job is to not break it.** Known gaps we own: the FP8-MoE oracle admission
(§0.3) and the AMD PLE FP8 stack (§0.4). The AMD tree also never got the fused
pre-indexer (nvidia-only) — an optimization-stage candidate, not first-light.

### 2.3 vLLM PR #54129 (Trosfy, `ple-mmap-upstream`) — the engram-on-SSD mechanism

`ple_mmap.py` (1010 lines, nvidia-tree-only today): `np.memmap` views over the
checkpoint's own safetensors shards discovered by header parse (shard_N = row-block
tensors, NOT TP shards), dedup via `np.unique`, ThreadPoolExecutor gather (workers=32,
chunk=2048, both env-tunable), pageable staging (UMA-correct: pages yield to reclaim),
`load_weights` drops the table tensors on the floor, `weight_scale` kept as a buffer,
gather-time FP8→bf16 dequant, optional `VLLM_PLE_MMAP_PREWARM` bounded by
`MemAvailable − 8 GiB`, p99 gather-latency logging (a built-in state probe), and a
three-clause cudagraph-safety guard (PIECEWISE-only, enforced at construction). The
TP semantics are the prize: a plain nn.Module mapping the whole table per rank —
**the per-token all-reduce at the PLE lookup site disappears** (the vocab-parallel path
does one all-reduce per lookup per layer; at TP=2 with MTP that is real interconnect
traffic on our bottleneck).

**Our port** (the campaign's core task #2): mirror the five wiring sites
(W1–W5b, exactly pinned in [`evidence/ple-54129.md`] §"Exact port specification") into
`amd/`, plus the FP8 embedding stack the AMD tree lacks (`Qwen4ExpPLEFp8EmbeddingMethod`,
`_dequantize_embeddings`, quant plumbing — the +80-line variant, for env-off parity as
well), plus relocating `ple_mmap.py` to `common/` (the clean upstream shape). One
AMD-specific decision the port must make explicitly: `VLLM_PLE_MMAP=1` forces
PIECEWISE cudagraphs, and the AMD short-conv path carries FULL-cudagraph assumptions
(`NULL_BLOCK_ID` remaps, plus a fresh `# TODO: need double-check` at `amd/ple_layer.py:522`)
— first light is `--enforce-eager` anyway; the graph-mode matrix is morning work.

### 2.4 kyuz0 lineage (MIT for vllm-toolboxes; no LICENSE on the two toolbox repos)

| Lift | Detail |
|---|---|
| **Container build recipe** | `amd-strix-halo-vllm-toolboxes` `Dockerfile.ubuntu-repoamd`: Ubuntu 24.04, AMD *stable* wheels (`repo.amd.com/rocm/whl-multi-arch/`, auto-resolved — today caps at rocm7.14.0/torch2.11.0), vLLM built from source at an arbitrary `VLLM_REF` → we point it at the fork. Newest published image `:latest` = `rocm7.14.0-torch2.11.0-vllm0.28.0` @ `sha256:fa54dbc9…` (2026-08-25) is the fallback base if building from our ref hits a wall overnight. The old ds4 base (`sha256:25fd294f`, June, vllm 470229c, TheRock nightly) is *retired* for our purposes. |
| **QA release gate** | `QA_BEFORE_RELEASES_ROCM.md` method: "ROCm backend initialized, no CPU fallback" as a grep gate; the log-grep failure lexicon (`segmentation fault\|ROCm error\|nan\|infinity\|KV.*mismatch`); frontier-logit equivalence between execution paths; long-context regression sweeps ("a candidate that improves early-context but regresses as KV grows is not a pass"); figures as regression indicators, not universal thresholds | COPY-METHOD (no license on the repo — method only, rewritten with our rows) |
| **ROCm 10 intelligence** | ROCm 10.0.0 shipped today for the GGUF lane only; rocBLAS 5.5→5.6 breaks hand-tuned GEMM solution indices (−217/−216 → −50/−49; kyuz0 version-keyed them same-day). **Campaign rule: ROCm 10 is out of scope; anything carrying tuned rocBLAS solution indices must be version-keyed from day one.** Also: kyuz0's own flash-next llama.cpp toolbox lived 48 hours and was deleted today — nothing to inherit there. |
| **Benchmark method** | `run_benchmarks.sh` / `mtp-bench.py` shapes; counterbalanced arms; the `.meta`/canary discipline (via Nathan's packs) | COPY-METHOD |

### 2.5 `hellas-ai/nix-strix-halo` @ `f0f2048` (NO LICENSE — input-use only, never copy)

**[SHARPENED TONIGHT]** [`evidence/nix-strix-halo.md`]:
- **What we consume as input**: `pkgs.vllm-rocm` exists but is anchored to vllm
  0.25.1 via nixpkgs' 0.16-era expression + six `--replace-fail` literals — pointing
  `vllm-src` at our fork risks a build break far from our code. **Ruling: the container
  path is primary; the nix source-build of the fork is a stretch goal**, attempted via
  the documented three-mechanism override (follows for src + `overridePythonAttrs` for
  patches/version — always `(old.patches or []) ++`, always *append* to `postPatch`,
  always set `VLLM_VERSION_OVERRIDE`). Do **not** `follows` nixpkgs into it.
- **Pair bench harness**: `strix-halo-vllm-pair-bench-gfx1151` — SSH-driven Ray pair +
  transport matrix (`lan_tcp`/`tb_tcp`/…) + 55 pass-through NCCL/RCCL tuning vars. Its
  client's `prefill_mean_s` is **confirmed at current bytes a duplicate of
  `ttft_mean_s`** (line 297 = line 294, docstring admits the proxy; file untouched
  since introduction — no upstream fix coming). The package exposes `vllmStreamClient`
  as an argument: we supply our own client (measuring prefill from server-side
  `/metrics`, separating queue from prefill) without copying a line. Their NCCL env
  *values* are data we may read and set in our own units.
- **The `tuning` module stays Forbidden** — 33 lines that would append a second
  `ttm.pages_limit=` (80 GiB vs our 128 GiB; last-token-wins, silently), force TuneD
  at boot, and inject MES firmware; upstream's own example imports it, which is the
  trap. Enforce with an eval assertion (at most one `ttm.pages_limit` token), not
  convention.
- **No vLLM serving module exists upstream** (`grep vllm modules/` → nothing): the pair
  topology lives only in a bench script with `sleep 2` as synchronization. flashnext
  writes its own units with a real readiness gate (the `library-reachable`
  wait-for-reality idiom from dotfiles).
- Lock intelligence: their `flake.lock` hasn't moved since 2026-07-21 while HEAD
  advanced — the vLLM/bench surface is stable-and-unmaintained. Plan for zero upstream
  fixes.

### 2.6 Dotfiles (the estate substrate — committed and live tonight)

Everything the campaign composes with, verified live [`evidence/dotfiles-observed.md`]:
lowlat module (shell fd-holder, tripwire at 200 µs/15 min/1 h sustain, jumbo off,
import-is-the-gate via `modules/strix.nix` — only the twins see it), three-layer
addressing with committed metrics, `ttm.pages_limit=33554432` (128 GiB) + `amd_iommu=on`
on both cmdlines, kernel 7.1.4 both, catalog machinery with the snapshot-layout
precedent (`deepseek-v4-flash-0731-bf16` row) and the `-hf` ban, `#237` escape hatches
(`nix build .#vllm-rocm` etc.) intact, llama-swap owning :9292 with TB/eth *not*
firewall-admitted (our units add their own scoped admissions). Two flaws to route
around: the dotfiles flake currently cannot be evaluated in place (a committed unix
socket under `home/dot_config/cliamp/` breaks `nix flake …` — so no gate may shell out
to the dotfiles flake), and `localModelStore.packages` is a dangling attribute (the
recorded `nix build .#models.<id>` download path is dead; the NAS pipeline is the real
path, which is fine — it's the doctrine anyway).

### 2.7 Reference-only (unchanged verdicts, now with less to do)

`EngramHalo.cpp` (SSD-cost measurements, load-phase page-cache lesson —
watch for the two-copies-resident transient on our ~62.5 GiB/rank load),
`llamacpp-nathan-discoveries` (silicon facts: int4 2.03×, no FP8 unit, LLC no-allocate
via HIP, wave32-native WMMA; the nine method rules; k=3 MTP prediction for a
1-layer-MTP model — first thing to test on the sidecar-free vLLM MTP), `strix-rdma`
(contract stays shelved with RDMA), `wkljohn` archaeology, `antirez/ds4` +
`ds4-kyuz0` (GGUF-lane kernels — the MMQ/int4 material returns if/when the 4-bit
expert requant experiment opens; plus today's DSpark adaptive-scheduler finding:
probe 4 cycles, bypass below 4-accepted/cycle — a shape worth copying if our MTP
acceptance is prompt-sensitive).

---

## 3. Innovations in flashnext that ds4-vllm does not have

1. **Engram-on-NVMe via mmap, on AMD** — #54129's mechanism (deduped, threaded,
   FP8-native, prewarm-bounded, self-probing) ported to the platform tree nobody
   tested, removing both ~23.9 GiB/rank of resident table *and* a per-token collective.
   DS4 has no engram at all; the DeepSeek lineage's only analogue is host-DRAM offload.
2. **In-checkpoint MTP** — no external drafter (DSpark is a separate 11.3 GB
   checkpoint; GGUF users need a sidecar extraction). The mtp.* block ships in the
   checkpoint, the PR wires it into vLLM v1 spec-decode with indexer-selection reuse
   on draft steps. Collectives drop ~3.7× per accepted token — on this cluster the
   interconnect optimization, for free.
3. **FP8-MoE on RDNA3.5** — the oracle admission + validated Triton block-FP8 expert
   path on a platform vLLM deliberately excludes today. This is the world-first piece;
   ds4's FP8 work was linear-layer-only on top of a natively-supported MXFP4 expert path.
4. **Genuinely sparse long-context attention out of the box** — QSA gather + GPU
   block-top-k arrived correct upstream; our contribution is *proving* it on gfx1151
   (the CUDA-gated test suite has never run on this hardware) rather than fixing it.
5. **Dual-rail TCP inference plane** — both TB cables carry tensor traffic (socket
   channels striped across rails), control on 5 GbE, PM QoS held both ends with a
   committed tripwire. DS4 ran one rail + RDMA and parked the second cable.
6. **Declarative estate integration** — catalog-declared weights with per-shard hashes
   and NAS staging, systemd units composed from committed fleet idioms, a nix flake as
   the deliverable, and (structurally new) **the build itself run as a tally campaign**:
   spec → worklist → gated lanes → witnessed receipts, with adversarial review tiers.
   ds4-vllm's 8 commits were a person; flashnext's overnight is a governed fleet.
7. **Engagement-proof plane as a first-class deliverable** (§4) — ds4 had the
   instruments; flashnext ships every mechanism with probe + kill-switch + quality
   number *as spec claims with oracles*, so "is it actually on" is a gate, not a habit.

---

## 4. The mechanism table — probe / kill-switch / quality number

| Mechanism | State probe (executed graph, not the gate) | Kill-switch | Quality number |
|---|---|---|---|
| FP8 MoE path (ours) | `VLLM_LOGGING_LEVEL=DEBUG` oracle rejection table at startup + our admission log line naming the kernel class; `_GCN_ARCH`/`on_cdna()` one-liner pre-flight | `FN_FP8_MOE=0` → refuse loudly (never silent-fallback to anything) | fidelity suite NLL/TVD vs first-light baseline; frontier logits pinned |
| PLE mmap (ported) | built-in p99 gather log (60 s cadence); `mincore` residency; RSS-vs-GTT after warmed decode; `weights_streamed` flag | `VLLM_PLE_MMAP=0` → vocab-parallel path (requires our FP8 embedding stack — env-off must also be correct) | table-read A/B on a factual-recall probe (the table is the factual store) |
| MTP | acceptance counters from the server; drafts-per-step distribution | speculative-config off | temp-0 byte-identical outputs vs MTP-off; t/s judged on throughput alone (F.13) |
| QSA sparsity | topk launch count per step (torch profiler window via synctrace); no `.item()` in path (already verified from source) | none needed (no fallback exists — single path) | long-context needle probe; the CUDA-gated reference tests run on gfx1151 |
| Dual-rail striping | `ss`/interface counters per rail during decode; RCCL channel log | `NCCL_SOCKET_IFNAME=thunderbolt0` (single rail) | per-op all-reduce µs at 5 KB (prices P9 exclusion) |
| PM QoS + tripwire | `od /dev/cpu_dma_latency` both ends; tripwire timer armed | systemctl stop (drill exists) | 200-sample RTT within budget (committed threshold 200 µs) |
| Ray/TP env parity | byte-diff of both ranks' env dumps as a gate | — | — |
| Expert-union roofline | adapted `expert_union` counting distinct experts/step at top-10/512 with MTP verify | `FN_EXPERT_UNION=0` (default off) | sets the decode roofline the plan is graded against |
| Env-default discipline | `envs.is_set()` footguns: **never export a default** (`VLLM_USE_DEEP_GEMM` explicitly set diverts the oracle into a hard raise) | — | launch env linted against an allowlist |

---

## 5. Build plan and compute routing (for the spec's stages)

**S1 — tonight, Claude-tier (fable orchestrating, opus lanes via ultracode workflows in
this session), committed as the estate bootstrap before the campaign arms:**
the hard core where a wrong line costs the morning — fork assembly (merge #53896+#54129,
resolve the six known conflict files), the gfx1151 FP8-MoE admission patch, the AMD PLE
port (both halves), instruments adaptation, and the patch-discipline scaffolding
(MANIFEST, verify script, packaging tests). Fable reviews every artifact against the
dossiers before commit.

**S2 — overnight, qwen-max via tally (`pi` adapter, `narrator` steward), the governed
bulk:** container build + smoke (CPU-side: import, registry resolve, oracle admission
unit test), flake outputs and checks, host units + module, bench harness + our stream
client, runbook rendering, catalog row + sync verification, docs. Every task carries
executable acceptance argv; the gate ladder stays cheap (build/verify only — no GPU
gates overnight).

**S3 — morning, human-attended:** weights land on both nodes (download completes
overnight; NAS→node sync is a catalog switch + timer run), `_GCN_ARCH` one-liner, proxy
FP8 checkpoint single-node first light, TP=2 first light enforce-eager over the rails,
warmed-decode residency + `mincore`, fidelity baseline, then 262K, then the counterbalanced
bench vs the promotion thresholds. The B5 discriminator is already precise: cold-vs-warmed
delta on GTT+RSS, never a load-time absolute.

**S4+ — optimization under the method:** graph-mode matrix (PIECEWISE vs eager),
ubatch tuning (MoE wants large), MTP k-sweep (k=3 predicted), jumbo-MTU A/B, LLC
no-allocate experiments, the 4-bit expert requant experiment (int4 2.03× silicon
argument; escape-hatch no longer needed for memory — it is pure upside now), the
table-precision sweep (the experiment nobody has run), upstreaming: the AMD PLE port
PR, the gfx1151 enablement PR, and the TP=2-on-gfx1151 measurement report.

---

## 6. What remains genuinely unknown (morning-testable, all with named one-liners)

1. Does the Triton fused-MoE block-FP8 kernel produce correct output on gfx1151 once
   admitted? (The class is proven on this silicon — DS4's w8a8 block-scaled linear runs
   through the same primitive family — but the MoE kernel itself has never executed
   here.) → proxy checkpoint, single node, greedy completion vs reference.
2. Does `torch.float8_e4m3fn` storage + `.to(bf16)` cast work on this ROCm build?
   → one-liner on the box.
3. RCCL end-to-end across the pair at TP=2 (socket transport, both rails). → first light.
4. The PR merge's runtime behavior at torch 2.11.0 (built against a relaxed pin).
   → container smoke.
5. MTP acceptance and the k=3 prediction on real prompts. → morning sweep.
6. `layer_types` full-attention count (12 expected from `full_attention_interval: 4`)
   → read from the downloaded config at load; sets the QSA-layers-per-step arithmetic.
