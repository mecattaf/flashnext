# Reconcile note — task `bench-harness`, 2026-08-30

Stateless reconcile attempt for revision `dbb1fd96`. Recorded per the overseer
standing note (tally#622 family): every deliverable of claims 6.2 and 6.3 was
already present and correct in the lane, so this note is the lane's non-empty
commit. It changes no code, moves no measurement, and stamps no fresh
timestamp onto any receipt.

## HEAD at verification

    ec3b464  host-tooling: Pair-service orchestration: env doctrine, cluster up/down, first-light runner

The working tree was clean at entry (`git status --porcelain` printed nothing).
The deliverables arrived across three commits, all confirmed ancestors of this
HEAD by `git merge-base --is-ancestor`:

| path | last touched by |
| --- | --- |
| `bench/fn-stream-client.py` | `d618da6` bench-harness |
| `tests/test_bench_client.py` | `d618da6` bench-harness |
| `bench/run-matrix.sh` | `d446d2b` bench: per-arm JIT warmup (MTP protocol) |
| `tests/test_bench_matrix.py` | `520f24b` round-2 crystallization |

Two earlier lane commits (`71f2beb`, `09a976b`) are *not* ancestors of HEAD —
their content was carried forward by `d618da6`, which is. Nothing in the lane
needed repair.

## Acceptance evidence

`bench-honest-client`, run verbatim, exit 0:

    python3 -m py_compile bench/fn-stream-client.py \
      && bash -n bench/run-matrix.sh \
      && python3 -m unittest tests.test_bench_client -v

- `py_compile` on the client — clean.
- `bash -n` on the matrix — syntax-clean.
- `python3 -m unittest tests.test_bench_client -v` — **Ran 12 tests, OK**.

Beyond the criterion, re-checked at the same HEAD: the repo suite with all
eight modules named explicitly — **Ran 116 tests, OK**; and
`scripts/receipts-verify.py` — 3 receipts checked, 0 violations.

## Goal conformance, re-checked

**Claim 6.2 — the prefill column is a measurement, not a proxy.** The reference
harness assigned `prefill_mean_s` from the TTFT series, character-identical to
`ttft_mean_s` (evidence/nix-strix-halo.md §4.4, confirmed at
`lib/bench/vllm-stream-client.py:297`). Ours does not:

- `ttft_s` is stamped client-side (request release → first streamed token) and
  the CSV header comment states outright that it includes queue + prefill +
  first decode + network + SSE framing, and **is not prefill**.
- `queue_wait_s` and `prefill_s` are independent server-side numbers, scraped
  as bracketing deltas of the `vllm:request_queue_time_seconds` and
  `vllm:request_prefill_time_seconds` histograms on `/metrics`, with an
  `inference − decode` fallback when the direct prefill histogram is absent.
- Absence is honest, never a proxy: a missing histogram emits an empty column,
  and a failed scrape records `prefill_source = scrape-unavailable` rather than
  a lifetime mean. Both facts have their own test.
- The fixture test the goal names is `test_parser_separates_queue_and_prefill`:
  it feeds `parse_metrics`/`split_first_token` a before/after fixture with
  injected queue (2.0 s) and processing (0.5 s) times and asserts each column
  recovers its own value and that the two are distinct.
  `test_prefill_is_not_the_first_token_column` and
  `test_csv_header_denies_the_reference_defect` guard the §4.4 defect directly.

**Explicit column semantics and fingerprints.** `CSV_HEADER_COMMENT` is written
ahead of the header row (`bench/fn-stream-client.py:513`) and documents all
fourteen columns, naming the clock behind each. `fingerprint_of()` is a
deterministic sha256 over every completion's token ids, with an empty sequence
returning the empty string rather than the sha256 of nothing; four tests cover
determinism, divergence detection, empty completions, and the ids-versus-strings
collision.

**Claim 6.3 / ruling P12 — the matrix carries its protocol.** `run-matrix.sh`
builds the counterbalanced schedule at lines 76–93: the leading arm alternates
per load index, so positions 1..4 are literally A-B-B-A. `LOADS_PER_ARM`
defaults to 3, `DEPTHS=(0 10240 102400)`, and the two arms are speculative-off
and speculative-on. Phase B reduces to medians (`medians.csv`, `median_*`
columns) with per-cell fingerprint-divergence detection that flags an arm
SUSPECT rather than averaging over divergent token sequences. Rows land under
`results/bench/`, and Phase D writes the `results/receipts/bench.json` receipt
carrying `loads_per_arm`, `counterbalanced`, `arms`, and `fingerprints`. Per
F.8, those fields are measured rather than asserted: a dead speculative-on
serve degrades the receipt to one arm with `counterbalanced=false` and the
failure-log path, instead of publishing a number from an uncounterbalanced run.

## Boundaries

Only `bench/` and `tests/` were read for repair and only `tests/` was written —
this note. No path outside the task's conflict domains was touched.
