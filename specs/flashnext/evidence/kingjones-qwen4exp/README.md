# kingjones qwen4exp-on-ROCmFPX patch — archived evidence (2026-08-30)

**Source:** `https://huggingface.co/kingjones777/Qwen3.8-Flash-Next-ROCmFP4-STRIX-GGUF`
(`qwen4exp-on-rocmfpx-d3ca537.patch`, 158,550 bytes, 25 files, +2766/−15) and
its `MODELCARD.md`, both archived verbatim in this directory.

**Provenance:** the patch is the diff of real merged commit `3bb23a5`
("arch: qwen4exp") in `kingjones30/ROCmFPX` (a fork of
`charlie12345/ROCmFPX`, which supplies the FP4 tensor types); its stated
base `d3ca537` is a verified ancestor and the diffstat reproduces exactly.
Same lineage as the CIRU tree (identical `llama-memory-hybrid-idx` class,
verbatim-shared comments); CIRU is a squashed superset richer on runtime
(block-summary cache, PLE pager, I8 expert bank, MTP state sync), while
this patch's distinct value is the readable minimal integration diff and
the conversion/quantization memory doctrine.

**NO CODE FROM THIS PATCH IS USED.** Unreviewed pseudonymous community
work, no upstream review, no fidelity validation. What this repo adopts is
knowledge — everything below is directly readable in the diff or sharpens a
test we already owe ourselves. Treat the model card's prose as a lead and
the code as the source of truth (the card contradicts its own patch at
least once — see the conversion doctrine).

## The integration recipe (arch X onto a llama.cpp-lineage tree Y)

Why "cherry-picking individual files will not work" — five concrete
coupling mechanisms, none discoverable from the arch file alone:

1. **Six parallel hand-maintained registries** that must agree by index and
   string (Python `MODEL_TENSOR` enum — order-sensitive `auto()` —,
   `TENSOR_NAMES`, per-arch tensor list, HF-name map, C++ `llm_tensor`
   enum, `LLM_TENSOR_NAMES`), plus a seventh (`LLM_TENSOR_INFOS`) declaring
   the ggml op per tensor, which drives quantization eligibility. Miss one:
   silent load failure or silently wrong quant type.
2. **Memory-type "registration" is a `new`-site switch**, not an enum: four
   separate edits in two files to wire one memory class.
3. **Loader/saver symmetry with explicit template instantiations**: a new
   GGUF value type (their PLE hash multipliers need UINT64 arrays) requires
   loader cases, saver overloads, and explicit instantiations naming exact
   array sizes — asymmetry fails silently (`--save-model` drops the key
   group).
4. **Shared shape formulas are load-bearing**: `n_embd_r()` grew
   `+ ple_conv_state()`, with the PLE conv history packed into the tail of
   the recurrent conv row — three places depend on one formula; change one
   and state corrupts silently.
5. **The batch splitter's signature changed** (`split_equal` +
   `n_keep_tail`): the trailing `(1 + n_rs_seq)` tokens of each sequence
   must land in the same ubatch or recurrent rollback snapshots are invalid
   — the same bug family as the community's "seven stacked spec-decoding
   bugs".

Build-system nuance: `models/*.cpp` is GLOB-picked, but the new memory
class outside `models/` needed an explicit `add_library` line — the model
card omits this. Conversion registers only in the newer `conversion/`
package (two dict entries in its `__init__`), not `convert_hf_to_gguf.py`.
Their arch test injects synthetic KV so QSA doesn't silently fall back to
dense and go uncovered — a test passing because the feature disabled itself
is the failure mode to guard against.

## Large-table conversion + quantization memory doctrine

- **Positional memmap assembly** is the load-bearing conversion technique
  (NOT the model card's "cast to BF16" — the shipped code casts to F32;
  the card is wrong about its own patch): each of 128 PLE shards is written
  at `idx * rows_per_shard` into an `np.memmap` and dropped, so peak RSS is
  one shard. The naive hold-and-concat path peaks near 300 GB RSS.
- **Size output buffers exactly** (`ggml_row_size(new_type, ne0) * ne1 *
  ne2`), never by loose upper bound — `nelements * 4` was 205 GB of dead
  address space on the 51.2 G-element table.
- **ggml block quantization is row-local**, so quantizing a >2^31-element
  tensor in ~512 MiB row panels streamed to the output is byte-identical to
  the full-tensor path (the whole-tensor F32 image would be 192 GiB).
- **Quant-recipe trap:** the PLE table shares the `TOKEN_EMBD` category
  with `token_embd.weight`, so a blanket `--token-embedding-type` silently
  sweeps a tensor that is ~46% of a 4-bit file; an explicit per-tensor
  regex must win.

## What it confirms / adds for the mecattaf/vllm fork

- **Confirms independently** the fork's QSA cache separation
  (`common/qsa_cache.py`): indexer state in its own cache with lifecycle
  tied to the main KV cache. kingjones reached the same design in a
  different engine — after hitting the drift bug ("allocating separately
  let the two drift once context was rewritten between turns, pointing QSA
  top-k at the wrong cells") and asserting cell-for-cell agreement at graph
  build.
- **Sharpens repo issue #4** with a concrete regression test: rewrite
  context between turns on one sequence, then verify QSA top-k still
  selects the right cells (our compressor state is per-request blocks, not
  shared slot layout — the weaker coupling is exactly what his bug hit).
- **MTP-relevant rule** (their host-side PLE hash, modeled on vLLM's
  `ngram_context`): n-gram history is trusted only when `next_pos` matches
  the incoming position, else EOS-padded — i.e., PLE history must
  invalidate on speculative rollback, or the drafter and verifier silently
  disagree and it shows up as DEGRADED ACCEPTANCE, not a crash. Audit this
  in the fork before the speculative profile is declared production.
- Operational (Strix Halo, measured by them, engine-independent):
  no-mmap ⇒ silent cgroup OOM kill visible only in dmesg; forcing the
  table to CPU made decode WORSE (23 → 13.4 tok/s — "the kernel already
  streams it better", an argument against a manual PLE-placement knob);
  the table never enters GPU memory (mincore-verified, 32–35 GiB
  page-cache-resident under sustained load).
