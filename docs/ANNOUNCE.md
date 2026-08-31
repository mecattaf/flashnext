# ANNOUNCE — community post draft

**Status: DRAFT. Do not post before the receipts land.** Every bracketed
`[FILL: …]` below is a number that only exists after `cp-bench` writes
`results/receipts/bench.json`. Posting with a placeholder still in it, or
quoting a figure without its transport rung, is exactly the anecdote this
repo exists to avoid (spec F.8: no number from a single uncounterbalanced
run). Read [`docs/MORNING.md`](MORNING.md) first; post after the disposition
is recorded.

---

## Qwen3.8-Flash-Next-FP8 at TP=2 across two Framework Desktops

**tl;dr —** we got a 125B-trunk / 51.2B-engram-table FP8 checkpoint serving at
tensor-parallel 2 across two consumer Strix Halo boxes, on a vLLM fork that
teaches the fused-MoE path to upcast FP8 in-register on RDNA3.5 and ports the
disk-backed engram-table path onto the AMD platform tree. As far as we can
tell nobody has run this model on this hardware at TP=2 before, so there were
no numbers to compare against. Now there are some.
Everything is in [`mecattaf/flashnext`](https://github.com/mecattaf/flashnext),
Apache-2.0, receipts included.

### The setup

- **Two Framework Desktops** — Ryzen AI MAX+ 395 (gfx1151 / RDNA3.5),
  **128 GB unified memory each**. Identical twins.
- **Two fast rails** — one TB5 cable and one TB3 cable, both training at
  40 Gb/s on these USB4 hosts. The tensor plane rides RCCL over TCP sockets on
  **rail 0, single rail, cable A**.
- **One control wire** — a direct 5 GbE link (`enp191s0`) carrying ssh,
  orchestration, and weight staging. It is also the terminal fallback rung for
  the tensor plane, and every receipt stamps which rung it actually used, so a
  wire night can never be quietly reported as a rail night.

The model does not fit in one box — 185.6 GB of shards against 128 GB of RAM —
so TP=2 here is existential, not an optimization.

### Why it needed a fork

Three things blocked it on stock vLLM, all now fixed in
[`mecattaf/vllm@flashnext`](https://github.com/mecattaf/vllm/tree/flashnext)
(12 commits on the base, each mirrored under `patches/` with a MANIFEST):

1. **The FP8 MoE oracle refuses RDNA3.5.** `supports_fp8()` admits CDNA and
   RDNA4 only, so the fused-MoE kernel raises at layer construction. There is
   no FP8 matrix unit on this silicon — FP8 has to be upcast in-register.
   AMD's own open PR #52970 does exactly that for the *linear* block-scaled
   GEMM (`FORCE_FP8_DOT_UPCAST`); **our fork extends the same mechanism to the
   MoE path**, behind an `FN_FP8_MOE` kill-switch. `FN_FP8_MOE=0` restores the
   stock loud refusal — no silent fallback was added.
2. **The engram-on-SSD path was wired into the `nvidia/` tree only**, and the
   `amd/` tree had no FP8 handling for the 51.2B lookup table at all. The fork
   ports the mmap wiring and the FP8 embedding stack across, so the table is
   served from each node's NVMe through page-cache faults — **zero table bytes
   GPU-resident**, and at TP=2 it also deletes a per-token all-reduce.
3. **Four upstream fixes this hardware needs** ride along as cherry-picks with
   provenance trailers: the wave32 LDS overflow in `top_k_per_row_decode`
   (#46012 — on the sparse-attention hot path), APU/UMA memory accounting
   (#40963), the skinny-GEMM disable on gfx1151 (#51511), and KFD-topology
   platform detection (#46110).

### What got built overnight

- A container built from the fork at an **exactly pinned wheel set** —
  `torch 2.13.0+rocm7.14.0` from AMD's stable multi-arch index, matching
  torchvision and triton, no nightlies, no relaxed pins. Build directory
  bind-mounted with ccache, so a one-line patch iteration recompiles only what
  changed.
- **Host tooling** that stands both ranks: env doctrine, image ship, cluster
  up/down, preflight gates, and a first-light runner that grades its own
  residency bound.
- **A measurement harness** that separates queue wait from prefill (a lot of
  published TTFT figures quietly include queueing), fingerprints every token
  stream, and runs the arms counterbalanced with three loads each.
- **The multi-token-prediction head**, which ships *inside* the checkpoint
  (3,101 `mtp.*` tensors, 2.51 GiB across 28 of the 131 shards) — no external
  drafter artifact exists or is needed. It runs as a pure serve-config arm.

### Where the evidence lives

This repo was built to be checkable, not admired:

- **`specs/flashnext/`** — the ratified spec, and nine source-sweep dossiers
  with file:line pins for every claim that came from reading someone else's
  code.
- **`IMPORTS.md`** — every external artifact with source, revision or PR id,
  license, and role. `THIRD_PARTY_NOTICES.md` covers the same set. Nothing is
  vendored without a row.
- **`patches/`** — every fork commit past the base, mirrored, with a verify
  script that counts them.
- **`results/receipts/`** — one JSON receipt per overnight step, each graded
  against hard bounds by `scripts/receipts-verify.py`. Failures land
  *committed* under `results/receipts/failed/` as typed blockers. The failure
  mode of this project is a receipt that says "no", never a silent skip.
- **`docs/DECISIONS-2026-08-30.md`** — every decision with its evidence chain,
  the dissent it overrode, and the trigger that would flip it.

### Where the benchmarks land

`results/` — the raw matrix rows, the medians, and `results/receipts/bench.json`
with the protocol stamped into it: arms, loads per arm, counterbalancing,
depths (0 / 10240 / 102400), token fingerprints, and the transport rung.

- Decode, TP=2, spec-off: `[FILL: tok/s @ depth 0]`
- Decode, TP=2, spec-on (MTP n=3): `[FILL: tok/s @ depth 0]`,
  mean acceptance length `[FILL]`
- Full-context behaviour at 262144: `[FILL: decode ratio vs short context]`
- Transport rung of record: `[FILL: rail0-sockets | wire-fallback]`

### Things we learned that may save you a night

- **The wire is not your TP=2 limiter — an unheld CPU C-state is.** Hold
  `/dev/cpu_dma_latency` at the configured budget on *both* ends. **[CORRECTED 2026-08-31 — dotfiles#257: the budget is 100 µs, not 0, and it is NOT free. Holding 0 pins the cores at POLL: ~60 W/box for the last ~62 µs. The C3 block — ~7× of the ~8× effect — is already had at 100 (0.116 ms vs 0.829 ms unheld). Verify with `sudo fleet-postboot-verify`.]**
- **Your fleet's artifact sync will eat your staged weights** if the staged
  copy has no catalog row. It ate ours off both nodes, 185.6 GB × 2, hours
  after the receipts said verified. Declare artifacts before staging them.
- **`gpu-memory-utilization` lies on this APU** without PR #40963 — HIP
  reports the small VRAM aperture as "total". And once you fix the reporting
  you still have to *budget* GTT: with an mmap-served table, page cache is
  part of the serving design, so high utilization values are wrong even when
  they fit.
- **Serve text-only on gfx1151** (`--limit-mm-per-prompt` with image and video
  at 0). The vision-encoder profiling pass materializes a 65536² fp32 SDPA
  matrix — exactly 256 GiB — because there is no flash ViT kernel and it falls
  back to math.
- **FP8 on RDNA3.5 is a storage format, not a compute format.** For a
  220 GB/s machine that is the *ideal* case: 1 byte per parameter off DRAM.
- **Never `export` a vLLM env default** — several knobs are read through
  "is-set" probes, so exporting the value it already has changes control flow.
- **The in-tree USB4 stream primitive is real and fast** (14.3 µs RTT at 64 B),
  but treat every stream open as long-lived pair state: open/close storms
  against a mismatched peer wedge the router hop tables, take thunderbolt-net
  down with them, and recover only by reboot.

### What is still open

RDMA over the rails is an **attended** follow-up, not an overnight path — the
one community precedent measures ≈ +3.4 % decode over held TCP, which is worth
a morning and never worth an unattended night on unsigned out-of-tree modules.
The stream-primitive allreduce port has a published go/no-go rule in
`docs/USB4STREAM-TRANSPORT.md`. Full-graph capture, per-shape kernel tuning,
the speculative depth sweep, and the four-bit expert lane are all in
[`docs/MORNING.md`](MORNING.md)'s optimization menu with what we know about
each.

Questions welcome — especially from anyone else running a Strix Halo pair.
