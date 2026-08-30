# Reconcile note — task `instruments`, 2026-08-30

Stateless reconcile attempt for revision `16ab40e1`. Recorded per the overseer
standing note (tally#622 family): the deliverable was already present in the
lane, so this note is the lane's non-empty commit. It changes no code.

## HEAD at verification

    43eb34b  instruments: reconcile — the adapted instrument overlay is already in the lane

The three overlay modules and their test arrived via **ancestor `4b1d714`**
("instruments: Engagement-proof instruments adapted into the container
overlay"), confirmed an ancestor of this HEAD. All four blobs are
byte-identical to their landing commit — verified by object id, not by
re-reading:

| path | vs `4b1d714` |
| --- | --- |
| `container/rootfs/fn_synctrace.py` | identical |
| `container/rootfs/fn_offload_batch.py` | identical |
| `container/rootfs/fn_expert_union.py` | identical |
| `tests/test_instruments.py` | identical |

The prior reconcile commit `43eb34b` staged **zero change** against the
witnessed base `ff1731c`, which is the condition the spec-build driver rejects.
That is the defect this note corrects.

## Acceptance evidence

`instruments-compile-with-notices`, run verbatim, exit 0:

- `python3 -m py_compile` — clean on all three modules.
- `grep -l 'Adapted from' container/rootfs/fn_*.py | wc -l` — `3`.
- `python3 -m unittest tests.test_instruments -v` — **Ran 11 tests, OK**.

Repo suite, all five modules together — **Ran 50 tests, OK**. Note that
`unittest discover` cannot be pointed at `tests/` (namespace package, not
importable as a start directory); the modules are named explicitly.

## Goal conformance, re-checked

Env surface is fully renamed to the `FN_` prefix, with no `DS4_` residue:
`FN_PROFILE`, `FN_OFFLOAD_STORE_BATCH_FRAC`, `FN_OFFLOAD_PROMOTE_FRAC`,
`FN_EXPERT_UNION`, `FN_EU_START`, `FN_EU_CALLS`, `FN_EU_OUT`.

- **`fn_synctrace.py`** — wraps the tensor methods that force a blocking
  device-to-host sync, driven from the `FN_PROFILE` window rather than
  upstream's `ds4_tl_indexer` (ds4-vllm-manifest.md §3.1).
- **`fn_offload_batch.py`** — `resolve_store_batch_tokens` and
  `resolve_promote_block_budget`, torch-free, with both load-bearing
  invariants intact: the store floor at `max(offloaded_block_sizes)` and
  promotion counted in blocks, not tokens (§3.3).
- **`fn_expert_union.py`** — re-targeted as the goal requires, onto
  `fused_topk` in
  `vllm/model_executor/layers/fused_moe/router/fused_topk_router.py`, the
  choke point this workload actually routes through, instead of upstream's
  `gpt_oss_triton_kernels_moe.make_routing_data`, which is never on this
  model's path (qsa-53896.md §4). Kill switch defaults off.

Each module carries an adaptation notice header naming the upstream file
(`AlexKGwyn/ds4-vllm @ a8f620d`) and its Apache-2.0 license.

## Boundary

Only `container/rootfs/` and `tests/` were in scope; this commit touches one
new file under `tests/` and nothing else.
