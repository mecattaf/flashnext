# Reconcile note — task `host-tooling`, 2026-08-30

Stateless reconcile attempt for revision `57c614ae`. Recorded per the overseer
standing note (tally#622 family): every deliverable was already present and
correct in the lane, so this note is the lane's non-empty commit. It changes no
code and moves no measurement.

## HEAD at verification

    710ee52  instruments: Engagement-proof instruments adapted into the container overlay (rev 5)

The deliverables arrived across four commits, all confirmed ancestors of this
HEAD by `git merge-base --is-ancestor` — the two lane commits that authored the
task, plus two later cross-lane refinements that reached into `host/` and
`scripts/run-tp2.sh` after the fact:

| path | last touched by |
| --- | --- |
| `host/fn-env.sh` | `754e111` host-tooling |
| `host/fn-cluster-down.sh` | `754e111` host-tooling |
| `tests/test_host_tooling.py` | `754e111` host-tooling |
| `host/systemd/flashnext-pair.service` | `00c9bcf` host-tooling |
| `host/fn-cluster-up.sh` | `520f24b` round-2 crystallization (pre-arm serve/transport) |
| `host/fn-preflight.sh` | `520f24b` round-2 crystallization |
| `scripts/run-tp2.sh` | `9480958` checkpoint purity (receipt restore on re-run) |

The working tree was clean at entry; nothing in the lane needed repair.

## Acceptance evidence

`host-doctrine`, run verbatim, exit 0:

- `bash -n` over `host/*.sh` and `scripts/run-tp2.sh` — clean on all five.
- `PYTHONHASHSEED=0`, `NCCL_SOCKET_IFNAME`, `thunderbolt0`, `NCCL_IB_DISABLE=1`,
  `VLLM_RAY_EXTRA_ENV_VAR_PREFIXES_TO_COPY=FN_` — all present in `host/fn-env.sh`.
- `ExecStopPost` present and `WantedBy` absent in
  `host/systemd/flashnext-pair.service`.
- `python3 -m unittest tests.test_host_tooling -v` — **Ran 15 tests, OK**.

Repo suite, all eight modules named explicitly — **Ran 116 tests, OK**.
(`unittest discover` still cannot be pointed at `tests/`: namespace package,
not importable as a start directory.) `scripts/receipts-verify.py` — 3 receipts
checked, 0 violations.

## Goal conformance, re-checked

Env doctrine (`host/fn-env.sh`) carries every required line: the determinism
seed, the `expandable_segments:True,garbage_collection_threshold:0.85`
allocator, `HSA_ENABLE_INTERRUPT=1`, inductor and triton caches under
`FN_STATE_DIR` (never tmpfs), `VLLM_PLE_MMAP=1`, and the loud is-set-probe
prohibition block at the top of the file.

`NCCL_SOCKET_IFNAME` is computed by `fn_choose_rails()` from `ip -br -4 addr`,
not hardcoded, and logs each rail's verdict to stderr. It lists a rail only
when the rail carries a routable (non-link-local) address **and** its /30 peer
answers a three-packet probe — so `thunderbolt1`, trained but IP-unconfigured
per dotfiles `modules/lowlat-cluster.nix`, stays off the list and cannot hang
RCCL bootstrap. `NCCL_IB_DISABLE=1` is unconditional and is deliberately not
conditionalized on device detection, so sockets remain the transport of record
until the attended A/B in `host/rdma/ab-protocol.md` lands a verdict.

Bring-up reaps stranded serve processes on both nodes and gates on zero residue
*before* anything else, ships the image, stands the ray head on the coordinator
with the worker joining over the 5GbE wire (`FN_WORKER_HOST`, never a rail),
caps the worker python pool via `RAY_NUM_CPUS`, then holds a hard two-GPU gate
before serving `/var/lib/local-models/flashnext-fp8` as `flashnext` at
tensor-parallel size 2. Teardown is idempotent on both nodes and always exits 0.
The unit is `Type=oneshot` + `RemainAfterExit=yes` with teardown on both
`ExecStop` and `ExecStopPost` and no `[Install]` section, so a failed bring-up
cannot strand ranks and the pair never comes up at boot.

`scripts/run-tp2.sh` runs preflight, stands the pair, then writes the four
graded receipts — `tp2.json` (two greedy 300-token completions byte-compared),
`residency.json` (read after a >50-token warmed decode: per-rank GTT from
sysfs, process RSS, and table page-cache residency), `fidelity.json`
(fixed-prompt NLL and frontier top-5 logits under `results/fidelity/`), and
`context.json` (the 262144-token probe) — each shaped as the
`scripts/receipts-verify.py` schema expects, with failures quarantined under
`results/receipts/failed/`.

## One deliberate divergence from the goal prose, re-confirmed

The goal text asks for the serve to run "with eager execution".
`host/fn-cluster-up.sh` deliberately does **not** pass `--enforce-eager`, and
that is correct rather than a lapse. Spec ruling **P10**
(`specs/flashnext/spec.md:86`) states that the table path's own guard "demands
piecewise capture with the mmap operation as a split boundary and refuses plain
eager — first light runs that sanctioned mode". The guard is
`check_cudagraph_safety`, whose second refusal
(`specs/flashnext/evidence/ple-54129.md:259`) raises with *"enforce-eager does
not fully suppress CUDA graph capture on this model"* whenever
`compilation_config.mode != CompilationMode.VLLM_COMPILE`. Since `fn-env.sh`
sets `VLLM_PLE_MMAP=1` unconditionally, a serve carrying `--enforce-eager`
would hard-raise at construction and first light would never boot. The script
carries this reasoning inline, alongside the `#`-in-a-continued-command
landmine warning that `bash -n` cannot catch. Left as committed.
