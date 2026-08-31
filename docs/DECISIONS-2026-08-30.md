# Decision record — flashnext round-2 crystallization, 2026-08-30

Why this file exists: the reasoning below was produced by a ~1.4M-token
multi-agent analysis pass whose transcripts are ephemeral. The conclusions
shipped into the worklist and the code; the *logic* would otherwise have been
lost. This is the durable record — every decision with its evidence, the
dissent it overrode, the alternatives it rejected and why, and the exact
trigger that would flip it. Written for the operator and for whoever picks
this up cold.

**Status of these decisions:** they govern the overnight run armed on the
night of 2026-08-30. Several are explicitly *scoped to an unattended night*
and are expected to be revisited with attended hands and real numbers. Where
that is true, the trigger criteria are stated. Nothing here is doctrine
forever; all of it is doctrine tonight.

**Evidence labels:** MEASURED = observed on these two machines this session.
READ-IN-SOURCE = read in the actual source file, cited. INFERRED = reasoned
from the two above, labeled so it can be challenged.

---

## 0. The decisions in one breath

> Sockets on rail 0 tonight (the operator's RDMA-first instinct overridden on
> measured grounds — no ibverbs device even exists on either node since the
> 7.2.2 kernel move, and verbs failures wedge TCP on the same cable; RDMA is
> the attended morning A/B it always had to be). MTP is in — the head is
> in-checkpoint (3,101 `mtp.*` tensors, 2.51 GiB, verified in the shard index)
> and cp-bench's spec-on arm runs it at n=3 with acceptance telemetry and an
> identity oracle in the receipt. USB4STREAM gets a wedge-safe bench + the
> plugin-vs-port decision memo (the ncclNet plugin route was rejected in the
> second deliberation — the real path is a 2–4 day attended port of the
> doorbell allreduce, triggered by tonight's numbers). Both cable options were
> rejected (4-rail is physically impossible on this NHI; the ds4 split serves a
> patch we don't carry).

Everything below is the long form of that paragraph.

---

## 1. How these decisions were produced

Five parallel deep-analysis threads, each with its own evidence mandate and a
hard read-only rule toward live device state (no `/dev/tbstream*` opens, no
configfs writes, no reboots, no serves — the wedge hazard in §5 is why):

| thread | question |
|---|---|
| coverage | what actually remains between `main` and a green overnight run; what constrains new worklist tasks |
| transport | which transport carries the TP=2 allreduce; which cable configuration |
| usb4stream-depth | the operator-demanded *second, independent* deliberation, instructed to attack the first one rather than defer to it |
| mtp | how the multi-token-prediction head lands tonight, factually |
| ciru-repo | the CIRU/IU4 quant estate and the "int4 WMMA + Hadamard/FP4 QAT" art |

Then a synthesis pass, then **two adversarial verifiers** (feasibility and
worklist mechanics) instructed to *refute* the plan, then a revision pass that
folded their findings back in. They returned 19 defects, 12 of them blocking
or major. §9 records what they caught, because those catches are as load-bearing
as the decisions themselves — two of them would have cost the entire night.

Two follow-up investigations ran after the main pass, on operator request:
the ROCm 10 basis question (§6) and the kingjones qwen4exp integration patch
(§7).

---

## 2. D1 — Transport of record: RCCL sockets on rail 0, cable A

**Decision:** cp-tp2 and cp-bench ride RCCL over TCP sockets on `thunderbolt0`
(10.99.0.x/30), single rail, cable A. `NCCL_IB_DISABLE=1` stays unconditional.
No verbs rung exists anywhere in the unattended ladder.

**Dissent overridden:** the operator's stated instinct — "decide the shape and
do it right away, not TCP-first-RDMA-later" — and his preference for RDMA-first
with TCP fallback. That instinct is *architecturally* right and is exactly how
the morning should proceed. It was overridden for tonight on five measured or
read-in-source grounds, any one of which is sufficient:

**2.1 There is no ibverbs device on either node.** MEASURED: `/sys/class/infiniband`
is empty on both. Round 1's pre-arm bake produced `usb4_rdma0`/`usb4_rdma5` on
kernel 7.1.4; the fleet has since moved to 7.2.2 and the staged out-of-tree
module sets cover only 7.1.4 and 7.2.0. `host/rdma/fetch-and-build.sh:90,107-118`
hard-gates on the *running* kernel being 7.2.2 — and the coordinator was still
on 7.2.0 pre-reboot. There is nothing to enable. This single fact is the
out-of-tree treadmill demonstrating itself on schedule: a kernel point release
silently retired the entire RDMA capability.

**2.2 The RDMA package's own Gate 0 forbids it.** `host/rdma/attended-bringup.md:24-38`
requires a *committed TP=2-over-TCP benchmark under `results/`* before bring-up
may begin. None exists — `results/receipts/` held only build and weights
receipts. TCP-first is not a preference we are choosing; it is the adoption
gate this repo wrote for itself in round 1, and tonight's socket bench is
literally the artifact that unlocks tomorrow's verbs A/B.

**2.3 Bring-up is 2–4 attended hours and two more reboots, not "reboot and sleep."**
Beyond the coordinator's pending reboot it needs: the worker deploy-path
repoint tested with rail 0 down, a scoped firewall + boot-config delta
deployed, then the worker rebooted onto a patched-module boot unit that *is not
authored or deployed yet*, then the coordinator. Operator physically present
for each. RCCL-over-`usb4_rdma` has additionally **never once initialized on
this pair** — `host/rdma/SCOPING-REPORT.md:128-130` calls the kernel half
"genuinely unbuilt today." What goes wrong at 1am: the rail doesn't retrain (PD
reset is rate-limited to 1/1800 s), a vermagic/ABI mismatch surfaces only at
`insmod`, or the open question of whether the store's `thunderbolt-ibverbs-0.3.4`
userspace matches the `76ba39b` kernel pin (SCOPING-REPORT.md:51-58) appears as
an undebuggable `ibv_devices` anomaly with nobody awake.

**2.4 The fallback ladder is itself the hazard.** This is the argument that
would stand even if 2.1–2.3 were all solved. An RDMA transmit toward a peer
with no open receive ring does not error — it stalls on zero end-to-end credits
and **wedges the whole XDomain, including TCP on the same cable**; recovery is
reboot-only (`host/rdma/ab-protocol.md:44-49`, independently reproduced in
`strix-rdma`'s own log, 2026-08-24: "the TB link wedge is reproducible"). So an
unattended verbs→sockets-on-thunderbolt0 ladder **can have its fallback rung
destroyed by the failure it is falling back from.** A fallback that shares a
failure domain with the thing it backs up is not a fallback. There is no way to
write that ladder safely for an unattended night.

**2.5 The prize is small, and it is not where the headroom is.** The 105 µs
reference bar is real but it is not RCCL and not reproducible on our stack —
see §2.6. The only measured end-to-end precedent for RDMA-over-USB4 on this
hardware class is **+3.4% decode over held TCP** (`ab-protocol.md:22-41`), and
`strix-rdma`'s author calls NHI verbs "effectively identical to TCP v3" and
ships TCP in production. Meanwhile MTP (§4) multiplies tokens per step by
2.5–4×. Spending the night's risk budget on a 3% transport delta while a 250%
decoding delta sits in serve-config is a bad trade.

**2.6 What the 105 µs bar actually is** (READ-IN-SOURCE — this matters, because
it was being used as the target to beat). There is no `tb2_ar.py`; the artifact
is `tbv_ar2.py` (69 lines) + `tbv_ar2.hip` (411 lines) in the ds4 estate. It is:

- **ibverbs RDMA, not RCCL** — RC queue pairs, TCP rendezvous exchanging
  `qpn/rkey/gid` (`tbv_ar2.hip:95-120`), then one-sided RDMA writes. It
  *bypasses the collective library entirely*, hooked into vLLM through a
  ~51-line `cuda_communicator.all_reduce` dispatch patch.
- **Architecturally: doorbell + progress thread.** The stream enqueues [D2H
  copy into a pinned send slot] → [doorbell kernel writes `(seq<<24)|nbytes`] →
  [GPU wait-and-add kernel spins on the peer's flag landing in pinned memory
  and adds the receive slot directly — UMA zero-copy read, no H2D copy]; a CPU
  progress thread polls the doorbell and posts the writes. Removing the host
  from the critical path is what took v1's ~228 µs down to ~105 µs.
- **It rode two components we deliberately do not carry**: a ~3,450-line
  unpublished local RC-write zero-copy patch, and a hand-tuned NHI IRQ-throttle
  helper at 8 µs versus the stock ~128 µs moderation (SCOPING-REPORT.md:186-197).
  INFERRED: our staged verbs build, lacking both, lands at v1-class (~230 µs) or
  worse on an interrupt-driven receive path.
- **It is capture-ineligible on our serve.** Its eligibility check includes
  `not torch.cuda.is_current_stream_capturing()` (`tbv_ar2.py:44-51`); the
  reference ran `--enforce-eager`. Our serve **cannot** run plain eager — the
  fork's cudagraph-safety guard refuses it under `VLLM_PLE_MMAP=1`, so we run
  compiled PIECEWISE. Inside captured decode segments the fast path would
  silently never fire. Porting it requires making the allreduce a splitting op
  first.

**2.7 The latency budget, so the stakes are numeric.** Config (hidden 2560, 48
layers, `full_attention_interval: 4`): ~96 allreduces per decode step at TP=2,
~5 KB each at batch 1 (INFERRED from standard megatron layout). At 40 Gbps
serialization is ~1 µs — **decode allreduces are latency-bound, not
bandwidth-bound**, which is the single most important structural fact in this
document. The per-step allreduce bill:

| transport | per-op | bill/step (96 ops) | basis |
|---|---|---|---|
| tbv_ar2 reference (zero-copy + 8 µs throttle) | 105 µs | 10.1 ms | measured, their stack |
| tbv_ar v1 (host in critical path) | ~228 µs | 21.9 ms | measured, their stack |
| RCCL verbs, *our* staged build | ~100–250 µs | 9.6–24 ms | INFERRED, wide |
| RCCL sockets over thunderbolt0 | ~150–300 µs | 14.4–28.8 ms | INFERRED — TCP-over-TB has **never been measured** |
| RCCL sockets over 5GbE | similar at 5 KB | 14–30+ ms | 4 KB TCP RTT 137.8 µs measured |
| USB4STREAM plugin (hypothetical) | ~40–80 µs | 3.8–7.7 ms | INFERRED from 21.8 µs 4 KB RTT |

Against ~12–20 ms/step of TP=2 compute, the honest spread between our best and
worst *available* transports tonight is single-digit milliseconds on a 26–49 ms
step. That is real and worth chasing — attended, with numbers, tomorrow.

**2.8 The reframe that settles it.** 185.5 GB of fp8 weights against 128 GB per
box: **TP=2 is existential for this checkpoint, not a speed play.** The
single-node community numbers (27–50 tok/s) are Q4/Q5 quants that fit one box;
we are not competing with them, we are doing something they cannot. cp-tp2
therefore needs a transport that *works unattended*, not one that is 3% faster.

**The ladder that shipped** (`host/fn-env.sh`):

1. **rail0-sockets** — `thunderbolt0`, listed only if its /30 peer answers.
2. **wire-fallback** — the 5GbE `enp191s0`, terminal, loud, receipted.

Never the second TB rail (a wedge takes every transport on that cable down
together), never verbs. The transport is decided **once on the coordinator** and
injected into the worker's environment as literals, so the two ranks cannot
disagree because one node dropped a packet.

**What flips this decision:** tomorrow's attended bring-up on 7.2.2 (both
nodes, worker first) plus the A-B-B-A A/B in `ab-protocol.md`, adopted only on
a fingerprint-clean majority-depth win — *and only if* tonight's bench receipt
records `fn_transport_rung: rail0-sockets`, because a wire-fallback bench is a
5GbE artifact and does not satisfy Gate 0.

---

## 3. D2 — Cables: single rail, cable A. Both proposed topologies rejected.

**3.1 The "hellas 4-rail aggregate" (2 cables → 4 rails → 4 IB devices, ~48 Gb/s/dir)
is physically unavailable on this silicon.** The Strix Halo NHI has **exactly 3
DMA rings per controller** — verified in round 1 via the driver's own debugfs
and independently corroborated by the only other known cross-host Strix
transport project. Those three are: control, thunderbolt-net, and **one** RDMA
lane. The advertised *second* native lane per cable permanently fails its
boot-time probe with a cosmetic `-12` (a laundered `-EINVAL` from
`nhi_alloc_hop`, "invalid hop: -1"); it is permanent, harmless, cleans up
fully, and never retries (`attended-bringup.md:338-351`). Two cables therefore
yield at most **two** usable RDMA lanes, not four. The 48 Gb/s figure comes from
different hardware or counts lanes that cannot allocate here.

Three further objections, any of which would independently sink it: both cables
train at only 20 Gb/s × 2 = 40 Gbps regardless (TB5 buys nothing on these USB4
hosts); **decode is latency-bound, so aggregation buys nothing where it hurts** —
it is a prefill/bandwidth lever aimed at a decode problem; and it consumes both
USB4 ports on both boxes for zero decode benefit.

**3.2 The ds4-vllm 2-cable split (cable 1 = control + TB-IP, cable 2 = NHI
zero-copy RX train) is rejected because we don't carry the thing it exists to
serve.** Cable 2's role in the reference is the receive-side zero-copy train
for that ~3,450-line unpublished patch (SCOPING-REPORT.md:186-199: "no safe
adaptation exists, and none is present here"); their `tbv-second-cable-prep` is
deliberately absent from our package. Engaging cable B would buy a second wedge
surface for no payload — and cable B additionally has a healed-but-once-defective
history (a worker-side config-space read timeout on its first-ever tunnel
activation).

**3.3 Chosen:** cable A only (coordinator domain0 ↔ worker domain1, carrying
`thunderbolt0`/10.99.0.x). USB4STREAM's `fn0`/`fn1` are provisioned on this same
cable, so the stream bench needs no cable B either. `thunderbolt1` stays out of
`NCCL_SOCKET_IFNAME` — it has no peer IP, and a peerless rail in that list hangs
RCCL bootstrap. Cable B stays parked as a physical spare.

**Deserved test, deferred to attended morning:** TB-IP on A alone versus adding
B as a second *socket* rail. Cheap, wedge-free, and it answers the aggregation
question with numbers instead of arguments.

---

## 4. D3 — MTP: in-checkpoint, pure serve-config, first light in the bench arm

The operator's hardest requirement. It settled better than expected: **no
artifact work at all.**

**4.1 In-checkpoint, measured.** From `model.safetensors.index.json` (152,089
tensors) and the shard headers: **3,101 `mtp.*` tensors** — `mtp.fc_embedding`,
`mtp.fc_hidden`, `mtp.hyper_connection_mixer.*`, `mtp.layers.0.*` including all
512 experts with `weight_scale_inv` (same block-fp8 scheme as the trunk). No
`nextn`/`draft` names. **Actual size 2,698,026,496 B = 2.51 GiB** (2.34 GiB
fp8 + 0.17 GiB bf16) across 28 of the 131 shards. The operator's "~3–4 GB" was
the community's **4.1 GB Q8_0 GGUF re-export** of this same head — the native
fp8 head is 2.51 GiB. `config.json` carries `mtp: {hybrid: true, layer_types:
["full_attention"], num_hidden_layers: 1}` and `mtp_num_hidden_layers: 1`.
Issue #1's closure holds, re-verified independently against the library source.

**4.2 The fork supports it natively, end-to-end** (READ-IN-SOURCE, fork
`bdb6f04`). This was the biggest open risk going in — the community found spec
decoding on this architecture "broken, not useless" (seven stacked bugs), and
the deepest of those was that the recurrent state got serialized through host
RAM (~750 ms per speculative step) because the architecture was never
whitelisted for the rollback ring. **That failure class cannot occur here:**

- `vllm/config/speculative.py:820-841` maps `qwen4_exp` → `qwen4_exp_mtp`, reads
  `n_predict` from `mtp_num_hidden_layers`; `:1086-1104` auto-points the draft
  at the target checkpoint with inherited fp8 quantization.
- `vllm/v1/spec_decode/qwen4_exp.py` — a dedicated `Qwen4ExpMTPProposer` (179
  lines) handling the multi-group cache topology and `hc_count*H` feedback
  streams; requires exactly `mtp_num_hidden_layers == 1`, which is what we have.
- `vllm/models/qwen4_exp/amd/mtp.py` (452 lines) implements the head reading the
  backbone's **pre-final-mixer 4-stream hidden state through its own
  hyper-connection combiner** — precisely the "combiner fidelity" the community
  measured as load-bearing (0.87–0.96 acceptance for the faithful variant vs
  0.47 for a naive mean). The fork implements the faithful one.
- **Recurrent-state rollback is in the allocation contract, not a whitelist**:
  `gdn_attn.py:47-66,107-148` carries `spec_state_indices_tensor [batch,
  num_spec+1]` and `num_accepted_tokens` — one GDN state slot per draft
  candidate, committed on acceptance; `abstract.py:81-85` sets
  `num_speculative_blocks`; `single_type_kv_cache_manager.py:1599-1603` reserves
  the extra blocks explicitly "for speculative decoding (MTP/EAGLE) with linear
  attention." There is no whitelist to miss.
- QSA under spec: `qsa_cache.py:764-769` sizes the ring at `compress_ratio +
  num_speculative_tokens` "so speculative rows cannot alias."
- Six unit tests exist — **all CPU-level. Zero end-to-end GPU receipts exist
  anywhere**, which is exactly why tonight's arm is framed as the first proof.

**4.3 ds4 precedent confirms the shape:** they ran MTP *through vLLM proper*,
enabled purely by serve flags, TP=2 under ray. Their `DS4_MTP_*` env family is
their fork's private machinery — not needed here, because our fork's path is the
native upstream-lineage equivalent.

**4.4 TP=2 interaction — and the fact that reframes the transport question.**
The head **shards** (same TP-parallel MoE/attention classes; `tie_word_embeddings:
false` so the draft allocates its own `ParallelLMHead`): ~2.4–2.5 GiB/rank
incremental against the ~61 GiB/rank weight budget. Affordable.

The real budget item is GDN state: mamba pages scale ×(1 + n) per request, and
doctrine is ~54 MiB/seq/rank (independently recomputed at ~57 MiB from config
dims). At n=3 that is **~216 MiB/seq/rank** — an unpinned 256-slot default
becomes ~54 GiB/rank of pool, an instant budget kill. Hence `--max-num-seqs 32`
pinned in *both* serve lines (§8.6).

And then the counterintuitive part: **speculative decoding *reduces* allreduce
pressure per emitted token.** Spec-on at n=3 adds ~9% collectives per step (3
serial one-layer draft passes) and widens the verify pass to 4 token columns
(~20 KB — negligible at these latencies), but at ~0.9 per-position acceptance
the mean acceptance length is ~2.5–3.4, so collectives *per emitted token* drop
from ~100 to ~35–45. **MTP relaxes the transport-latency ceiling ~2.5×; it does
not tighten it.** That makes sockets-first more viable tonight — while
simultaneously *raising* the marginal value of a lower-latency transport, since
the n draft steps are serial latency-bound round trips (draft overhead ≈1 ms/step
at the 105 µs bar versus ≈4.5 ms/step at a 500 µs socket allreduce). Both things
are true at once, and they point the same way: ship MTP tonight, chase transport
attended.

**4.5 What ships tonight.** `--speculative-config '{"method":"mtp","num_speculative_tokens":3}'`
as the `SPEC_ON_EXTRA` default in `bench/run-matrix.sh` (replacing an n-gram
drafter default, which would have measured the wrong thing entirely). n=3
because our head is a *single* layer that chains — unlike llama.cpp's native
depth-6 head — upstream warns acceptance decays with chaining depth, and the
community measured n-max 3 working well on this hardware. The n∈{1,2,4,6} sweep,
plus `index_share_for_mtp_iteration` and `disable_padded_drafter_batch`, are
morning menu items (UNKNOWN-5).

**cp-tp2 first light stays spec-off** — the identity oracle needs a baseline.
cp-bench's spec-on arm is the first end-to-end proof, and the receipt carries
acceptance telemetry (`Mean acceptance length` / per-position rates grepped out
of the serve log) plus a per-depth cross-arm identity oracle: at temperature 0,
speculative output must match plain decode byte-for-byte. **Spec-on numbers are
not quotable on a dirty oracle.**

**4.6 The honest fallback, written into the lane loudly.** If the spec-on serve
fails to boot or diverges, the matrix banks the failure log, restores the
baseline arm behind a one-shot guard, and finishes as an explicitly-labeled
single-arm receipt (`arms=["spec_off"], counterbalanced=false,
spec_on_failed=true`) — a shape the receipts gate now accepts, because
counterbalancing protects two-arm comparisons and with one arm there is no
comparison to protect. Promotion to the standing serve is then an attended
`FN_SPEC_ARGS` env flip, not a lost night. Risk R1 remains: e2e MTP is unproven
on gfx1151/ROCm, and issue #4's HIP multi-sequence gather question overlaps the
width-4 verify path — which is why spec-arm concurrency stays at 1.

---

## 5. D4 — USB4STREAM: measured tonight, ported attended. Two deliberations.

The operator asked for this to be deliberated **twice**, and it was: the first
deliberation (the kernel/dotfiles investigation from the previous night) and a
second, independent pass explicitly instructed to *attack* the first rather
than defer to it. The second pass overturned the first's central framing.

**5.1 The primitive, verified on this machine.** `thunderbolt_stream` is
in-tree (`intree: Y`), authored by the Thunderbolt maintainer, with a formal
ABI document shipped at kernel 7.2. Driver source read (`stream.c`, 1698 lines):
4 KiB DATA frames; **one kernel copy per direction** (`copy_page_from_iter` on
write, `copy_page_to_iter` on read) — no zero-copy, no GPU pointers, a userspace
byte pipe; **rings are allocated and paths enabled only on FIRST open, and CLOSE
is sent and paths disabled only on LAST close**; peer close is cleanly
detectable (reader gets EOF, writer gets `-ENXIO`) *without retrying an open*;
read and write serialize on one per-device mutex, so a high-rate bidirectional
transport wants `fn0`/`fn1` as a unidirectional pair. Measured previously:
14.3 µs RTT at 64 B, 21.8/25.3 µs p50/p99 at 4 KB, ~841 MB/s per stream at ring
4096 (we run 1024, so expect less).

**5.2 Two device-numbering traps, MEASURED — these would have wedged a rail.**
Configfs on the coordinator: cable A's service carries `fn0` **index=2 →
/dev/tbstream2**; cable B's parked service carries `fn0` index=0, `fn1` index=1.
On the worker, cable A is its domain1 and its `fn0` is **/dev/tbstream0**. So:

1. **The same logical stream is `/dev/tbstream2` on one box and `/dev/tbstream0`
   on the other.** Any hardcoded device path is wrong on at least one end.
2. **The coordinator's `/dev/tbstream0` and `1` are PEERLESS** — they belong to
   cable B, whose worker counterpart has no configfs groups at all. A blocking
   open there waits on a stream that can never become valid, and lands precisely
   on the mismatched-peer surface the wedge hazard describes.

Hence the bench's non-negotiable resolution chain: rail netdev holding the
10.99.0.0/30 → `readlink /sys/class/net/$rail/device` → parent xdomain →
sibling service with `key == stream` → that group's `index` attribute →
`/dev/tbstream$index`, with **both ends' `fn0` groups required to exist before
any open.** The lane's acceptance argv asserts the source contains no
`/dev/tbstream[0-9]` literal anywhere.

**5.3 The ncclNet plugin route — rejected, and the first deliberation's framing
corrected.** RCCL *does* load net plugins (documented `ncclNet_vX` interface,
`librccl-net.so`; aws-ofi-rccl is the existence proof). The plugin is
nonetheless the **wrong vehicle for the stated goal**, for three reasons:

- **It cannot approach the 105 µs bar, because that bar was set by *bypassing*
  the collective library.** tbv_ar2 hooks in above RCCL entirely. A net plugin
  sits *under* RCCL's proxy and protocol machinery (LL/Simple protocols, host
  staging, proxy-thread hops), which adds its own tens-to-hundreds of µs on tiny
  messages. It would beat TCP-socket NCCL modestly and miss the point entirely.
- **HopID scarcity forces a multiplexer.** NCCL wants one listen/connect/accept
  triplet per peer *per channel*, and vLLM creates multiple communicators; the
  stream primitive offers ~10 usable HopIDs, **shared with thunderbolt_net's own
  paths**, provisioned as kernel state rather than connect()-on-demand. So the
  plugin cannot map connections onto streams 1:1 — it must multiplex every
  channel over one or two long-lived streams with its own flow control and
  head-of-line management. That is a real mux/demux engine, not a shim.
- **Its lifecycle is the wedge pattern.** NCCL comm teardown and re-init — every
  serve restart — would drive stream open/close cycles, exactly what must never
  happen, unless the plugin holds fds open for process lifetime and multiplexes.
  Which it therefore must, compounding the previous point.

Estimate corrected in both directions: the first deliberation's "1–2 focused
weeks" is neither floor nor ceiling. With this deployment's simplifications
(2 ranks, one peer, host pointers only, pin `NCCL_MAX_NCHANNELS=1–2`) a
competent author gets a first working allreduce as a fragile prototype. But
the mux layer, 8-outstanding request semantics under RCCL's proxy threads,
collective hang debugging, p99 validation, and wedge-safe lifecycle across
restarts put "trustworthy enough to leave serving overnight" at **2–4 attended
weeks**.

**5.4 The real path: port tbv_ar2's architecture onto the stream device.**
Keep the doorbell + spin-kernel design; replace ~150–200 lines of ibverbs with
an ~100-line fd pump, with the progress thread now also running the receive path
(poll/read peer bytes into pinned UMA buffers, then store the flag the GPU spins
on). Cost versus RDMA: +2 syscalls, +2 kernel copies (sub-µs at these sizes),
+progress-thread wakeup on RX. The wrapper and the ~51-line vLLM communicator
hook port nearly verbatim. **INFERRED landing zone: ~120–160 µs against the
105 µs RDMA bar** at batch-1 decode payloads, degrading at MTP verify widths
where the ~841 MB/s wire term bites. **Scope, not schedule:** ~100 lines of fd pump replacing ~150-200 lines of ibverbs; the wrapper and the ~51-line engine communicator hook port near-verbatim. This is what "USB4STREAM as the actual tensor
transport" means, and the first deliberation never scoped it.

Two alternatives dismissed: a torch.distributed custom ProcessGroup backend
(touches vLLM's distributed init far more invasively,
delivers nothing the communicator hook doesn't); and using tbstream for the
bootstrap/control plane only (bootstrap isn't latency-critical, 5GbE sockets are
proven, and it would put wedge-hazard device opens in the serve path for zero
performance).

**5.5 The unattended-safety analysis that decides tonight.** Each FIRST open
enables router paths; each LAST close disables them. Cycling that against a
half-configured or mismatched peer corrupts hop tables, kills thunderbolt_net's
paths **on the same cable**, and needs a reboot — forbidden overnight, and it
would take cp-tp2's own NCCL rail down with it, since `thunderbolt0` rides the
same cable A and the same NHI. Exposure by candidate:

| candidate | wedge exposure | verdict |
|---|---|---|
| bench-only, one open per side, post-cp-bench | LOW — one path setup, one teardown, never retried, everything that matters already banked | **ship** |
| plugin build, live-tested | HIGH — plugin testing *is* open/close-per-iteration against a peer whose state the test is debugging: the literal storm pattern | reject for tonight |
| custom allreduce port, overnight | HIGHEST — needs serve processes, comm teardown/reopen on every failure, iterative debugging nobody is watching, and it would gate THE milestone on experimental transport | reject; this is the flagship attended follow-up |
| full transport | union of the above | reject |

**5.6 What shipped:** one authoring lane (`usb4stream-bench`) plus a dead-last
checkpoint (`cp-usb4stream`, after cp-close) with three hard skip preconditions
checked *before any device access*, each producing a typed outcome and exit 0:
a serve is up (`skipped:serve-up-on-shared-cable`), the rail peer is unreachable,
or either end's `fn0` group is missing. Exactly one open attempt ever, under a
30 s alarm; an idempotence guard that exits immediately if the receipt already
exists (making harness retries storm-free *by construction*); a fixed schedule
(RTT at 64/4K/16K/64K, the **allreduce-shaped simultaneous exchange at
8/16/64 KB** — the decision-relevant number — and throughput), under 90 s; and a
receipt whose status is **always `pass`**, because this is evidence-gathering,
not a campaign claim: a mid-run abort is typed as `data.outcome =
"aborted:PHASE:ERRNO"`.

**A position reversed during revision:** the synthesis initially had the bench
record co-existence data while the pair served. The second deliberation's
dissent was accepted on its own terms — the co-existence datum is readable in
the morning anyway, while a wedge on the shared cable is the single overnight
act capable of destroying the headline deliverable (a pair still serving at
07:00). So a live serve is now a typed *skip*. After a healthy night the
expected outcome is `skipped:serve-up-on-shared-cable`, pre-declared as such in
the morning ledger, and the first real numbers come from the attended morning
run after teardown.

**5.7 The strategic argument, split by op class.** For bulk/prefill/weights the
stream primitive loses on bandwidth (≤841 MB/s per stream, less at ring 1024) to
both patched RDMA and plausibly to plain TCP-over-thunderbolt0 — it will never
be the bulk transport of record. But for **the decode allreduce — the op that is
the ceiling** — an in-tree, maintainer-shipped, rebuild-free primitive landing
within ~1.5× of the out-of-tree RDMA bar is a defensible transport of record for
a two-box fleet, precisely because the RDMA treadmill is a recurring attended tax
(and §2.1 is that tax coming due).

**Morning decision rule — build the port if all hold:** (1) tonight's/morning's
exchange p50 at 8–16 KiB ≤ ~40 µs with a tight p99; (2) cp-bench plus the sync
tracer show TP=2 decode is allreduce-dominated; (3) the attended RDMA A/B either
fails or wins by a margin that doesn't justify its per-kernel-update rebuild
treadmill. If projected stream-AR lands within ~1.5× of measured RDMA-AR, make
the in-tree stream the decode transport of record and retire the RDMA stack to a
benchmark reference. **If (1) fails — fat exchange latencies or unstable p99 —
the stream stays a bench curiosity and RDMA remains the only sub-socket path.**

Two cheap attended checks worth two minutes each in the morning: confirm the
shipped RCCL honors `NCCL_NET_PLUGIN` (`strings librccl.so | grep librccl-net`)
before anyone re-litigates the plugin route; and benchmark TCP-over-thunderbolt0
now that the firewall is open, to close the "does the stream even beat TCP on
this wire" question.

---

## 6. D5 — ROCm 10: the operator was right, the project's ruling was wrong, the night still doesn't gamble

**6.1 The standing ruling was false.** `IMPORTS.md` and the evidence dossier
both asserted that AMD publishes no ROCm 10 gfx1151 torch wheels. The probe
behind that checked `stable.repo.amd.com/rocm/whl-multi-arch/` (404) and
`repo.amd.com/rocm/whl-multi-arch/` (capped at 7.14) — and **never checked
`stable.repo.amd.com/rocm/whl-next/`**, which has carried the complete aligned
set since **2026-08-26 11:52 GMT, two days before the dossier was written**:
`torch 2.13.0+rocm10.0.0`, `torchvision 0.28.0+rocm10.0.0`, `triton
3.8.0+git4cff872c.rocm10.0.0`, `rocm-sdk-{core,devel,libraries} 10.0.0`, plus
the gfx1151 device wheels. Verified by enumerating the index and reading the
torch wheel's METADATA over HTTP range requests: **same upstream versions and
the same triton git hash** as our pins, isomorphic dependency graph. The
migration is a version-literal substitution, not a port. The fork's
triton-version fp8 gates evaluate identically.

**6.2 The second stated blocker is also inapplicable.** The rocBLAS 5.5→5.6
solution-index breakage is ds4-lineage-specific (hardcoded indices in
llama.cpp-lineage HIP code). Our fork carries **no tuned solution indices** —
the only `solution_index` reference is `-1`, meaning "library default"
(`vllm/_aiter_ops.py:714`).

Both corrections are now committed (`IMPORTS.md`, and a dated §11 in
`specs/flashnext/evidence/kyuz0-rocm10.md`) — a stale ruling in a cited document
is worse than no document, because a future lane re-derives the wrong conclusion
from it.

**6.3 What is genuinely unknown, and why it stays off the critical path.**
**Nobody anywhere has run vLLM on ROCm 10 on gfx1151.** kyuz0's auto-discovery
pipeline — the downstream consumer that would show it — still resolves 7.14 and
has zero rocm10 tags after five days. Two failure modes are unmeasured and both
are unattended-overnight killers: whether ROCm 10's `hipcc` compiles the fork's
HIP sources, and whether its HSA runtime binds the in-tree KFD on this kernel.
The second can fail **asymmetrically** across a kernel-split pair (coordinator
7.2.0 pre-reboot, worker 7.2.2) — which is a TP=2-shaped failure that would
first surface at cp-tp2, the milestone.

There is also a sharp contract edge: cp-build re-runs the container build and
diffs the new receipt against the committed one on `(torch, triton,
fork_commit)`, and the banked `container-recipe` lane's own acceptance greps for
the literal `torch==2.13.0+rocm7.14.0`. A pin change re-opens a DONE lane and
requires a re-trued, committed build receipt *before* the run, or the ladder
never starts.

**6.4 What shipped instead:** a `rocm10-probe` lane scheduled **after cp-bench**,
which builds `flashnext:rocm10` from the **unmodified** Containerfile using its
already-existing build ARGs (`ROCM_WHL`, `TORCH_PIN`, … — verified present at
`container/Containerfile:48-65`), so there is **zero recipe edit and zero receipt-contract
impact**, then runs a minimal GPU binding check on the coordinator only. Its
receipt goes to `results/rocm10-probe.json` — **deliberately outside
`results/receipts/`**, because the gate fails on any fail-status receipt in that
directory and a red probe must be *data*, never a campaign failure. Cost ~30–45
min entirely behind the milestone; the wheel set (1.9 GB) is prefetched to
`~/.cache/flashnext-wheels/rocm10/`, byte-verified against the index listing.

If green, the morning promotion is mechanical and reviewable in daylight: swap
six ARG default literals, update the `recipe-pins` acceptance greps, re-run the
build and **commit the re-trued receipt** (the step people forget), amend ruling
P4 and note F.11 (`whl-next` is a vendor index on AMD's stable host but is by
name a forward channel — make that an explicit ruling rather than an
assumption), then re-run the ladder from cp-build, keeping the 7.14 image as
rollback until a ROCm 10 cp-tp2 is green.

**The bottom line the operator should hold onto:** platform-specificity was
never the objection. Being the first person anywhere to run this engine on this
ROCm on this GPU, unattended, between 2am and morning, was.

---

## 7. D6 — Quantization side quests: three investigations, zero lanes

**7.1 CIRU / "IU4" decoded.** `IU4` is **not a quant format on disk** — the
shipped GGUF stores its 144 routed-expert tensors as plain **Q4_1** (75.5 GB).
IU4 names a gfx1151 *execution path* built on the RDNA3.5 intrinsic
`__builtin_amdgcn_wmma_i32_16x16x16_iu4_w32`: activations quantized to int8
G128 then nibble-split into two u4 planes, weights u4 affine, two WMMAs
recombined. Three tiers exist and **only one is publicly reachable**:

1. Public prefill is **MMQ over Q4_1** with a forced tile-J tuning for the exact
   MoE shapes — not custom int4 WMMA at all.
2. An IU4_A640 MMVQ decode lane exists but no shipped code ever creates such a
   tensor.
3. **E3.QR05, the actual int4 WMMA MoE lane** (1539 lines of HIP), reads a
   **precomputed 60.94 GiB expert bank** — and its only entry point is a C API
   that **no shipped tool calls**, with no bank builder in the repo. The
   README's headline describes the author's private evaluation lane, not what
   his own `run-server.sh` runs. It additionally requires single-device
   (`split_mode none`), which is structurally incompatible with TP=2.

**7.2 The "Hadamard rotation / FP4 QAT in the DS4 indexer" is not transplantable
— and would be actively harmful.** It reproduces **DeepSeek-V4's official
indexer QAT graph**: `x → hadamard128(x)/√128 → fp4_act_quant` applied to
indexer Q/K post-RoPE, because vLLM's fp8 indexer path skips both steps and
top-k "is not the model's graph" without it. It is a **correctness-of-top-k fix
for a model trained with that quantized indexer** — not a speed feature. Our
workload's QSA indexer is bf16, Triton, **weight-free, no rotation, no QAT**
(READ-IN-SOURCE: `amd/indexer_qsa.py` asserts bf16, zero hits for
hadamard/fp4/e2m1/qat; the NVIDIA tree is likewise rotation-free — strong
evidence this architecture's indexer was never trained with such a graph).
Transplanting it would **corrupt our top-k selection**, not improve it. There is
also no int4 WMMA indexer anywhere in this corpus — the operator's phrase
bundled two separate arts from two different models.

**7.3 "But on dense Q8" — confirmed in substance.** No piece literally requires
dense-Q8 *weights*, but every piece presupposes integer-quantized dense formats
we don't run: E3.QR05 needs int8 activations + int4 affine weights; public CIRU
prefill is Q4_1-MMQ over Q8_1 activations; the Hadamard-KV trick activates only
when the KV cache type is quantized (we run bf16 KV); the MTP sidecar art is
Q8_0. Our stack is block-FP8 weights with bf16 activations and KV. The
preconditions are absent across the board. **The operator's instinct here was
correct.**

**7.4 The kingjones qwen4exp-on-ROCmFPX patch — zero code adopted.** Three
independent sufficient reasons: it contains **no FP4 hunks at all** (zero
`ggml/` diffs — the FP4 types live in its base tree, so there was nothing to
discard); wrong engine and topology; and it **deliberately drops the MTP head**
(`supports_mtp_export = False`, commented "the MTP block is a separate draft
head") — the exact thing we are enabling. What was banked instead
(`specs/flashnext/evidence/kingjones-qwen4exp/`): the five concrete coupling
mechanisms that make "cherry-picking will not work" true in any llama.cpp-lineage
tree (six-plus parallel registries that must agree by index and string; memory-type
"registration" being a `new`-site switch, not an enum; loader/saver symmetry
requiring explicit template instantiations, whose asymmetry fails *silently*; a
shared shape formula three places depend on; a changed batch-splitter signature
because the trailing recurrent tokens must land in one ubatch or rollback
snapshots are invalid); the large-table conversion doctrine (**positional memmap
assembly** is the load-bearing technique, and the model card's "cast to BF16"
contradicts its own code, which casts F32 — the placement is what matters, not
the dtype; peak RSS one shard instead of ~300 GB); the exact-output-sizing and
row-local panel-quantization arguments; and three operational facts measured on
this hardware class (no-mmap ⇒ silent cgroup OOM visible only in dmesg; forcing
the table to CPU made decode *worse*, 23 → 13.4 tok/s, "the kernel already
streams it better" — an argument against ever adding a manual placement knob;
the table never enters GPU memory, mincore-verified).

Most valuable of all, it **independently confirms our fork's QSA-cache
separation design** — reached in a different engine, after hitting the exact
drift bug ("allocating separately let the two drift once context was rewritten
between turns, pointing QSA top-k at the wrong cells") that forced his stricter
cell-for-cell coupling. That became a concrete regression test on issue #4, and
his PLE `next_pos` guard became issue #7 (PLE n-gram history must invalidate on
speculative rollback, or drafter and verifier silently disagree and it shows up
as **degraded acceptance, not a crash**).

**7.5 Recorded as rejected-with-reason** in the morning ledger's menu, so none
of this is ever re-investigated from scratch. The genuinely stealable items
became menu entries instead: the drop-behind page-cache pattern for issue #2,
`ROCBLAS_USE_HIPBLASLT=1` (now with a shipping gfx1151 precedent), per-shape MoE
kernel block-size tuning for the 2560×640 shapes, and CIRU's benchmark
disclosure discipline (cold exact-count ladder, cache-state per row,
machine-readable results, SHA-256 artifact identity).

---

## 8. The findings that reshaped the plan

Six discoveries that had nothing to do with the questions being asked, and
several of which would have killed the night on their own.

**8.1 The staged weights were gone from both nodes.** MEASURED independently by
three of the five analysts. Round 1's receipts (`pass, 131 shards,
185,563,854,698 bytes, ts 2026-08-29T12:47:30`) were true when written — then
both `/var/lib/local-models` directories were rewritten at ~20:07 (worker) and
~20:14 (coordinator) the same evening. Cause: the fleet's local-models sync
retires any staged artifact **that has no catalog row**, and it runs at every
boot, every rebuild, and every start of the sync-triggering service. The NAS
source is intact. Consequences: cp-weights' title was revised to force a
re-stage (a title revision changes the task's completion revision, so the
banked pass cannot satisfy it), its budget raised to 14400 s (the source path
measures 86–87 MB/s sequential → ~75–80 min/node), `catalog-handoff` now states
that applying its patch is what ends the hazard permanently, and the overnight
red lines forbid rebuilds and sync-service starts. **This is why "stop
llama-swap on both nodes" is in the operator checklist.**

**8.2 The worker carries zero podman images.** MEASURED. Nothing in the estate
ever shipped `flashnext:dev` across; cp-tp2 would have died at its
worker-container step after the weights restaged and everything else went right
— i.e. at ~06:00, with no time to recover. New `host/fn-image-ship.sh`
(idempotent by image Id, ~18 GB over the wire) now runs inside `fn-cluster-up.sh`
before the worker container starts.

**8.3 The bench matrix's own serve line still carried the cp-tp2 killers.**
Commit e91f517 fixed them in `fn-cluster-up.sh`; `bench/run-matrix.sh`'s
arm-reconfiguration serve was never updated. It still passed `--enforce-eager`
(the fork's cudagraph-safety guard **refuses** plain eager under
`VLLM_PLE_MMAP=1`) and lacked `--limit-mm-per-prompt` (the 256 GiB
vision-encoder profiling OOM). **Both arms would have failed to boot at the
first arm flip** — independent of MTP, this alone would have destroyed cp-bench.
Now fixed and pinned by `tests/test_bench_matrix.py`, which asserts the eager
flag appears **zero times in the file, comments included**.

**8.4 No ibverbs device survives on 7.2.2** — see §2.1. The README's previous
"present by design" claim is now historical.

**8.5 The dark rail is asymmetric.** MEASURED: the coordinator's `thunderbolt0`
is UP with its address but the peer is unreachable; **the worker's `thunderbolt0`
reads NO-CARRIER even after its own clean reboot.** So the coordinator's reboot
alone may not heal rail 0 — a premise the earlier handoff had asserted and which
is now withdrawn. The operator checklist gained a branch (replug cable A →
reboot the worker → accept a wire night), and the wire-night consequence is
written into the bench receipt, the RDMA docs, and the ledger so a 5GbE bench
can never be mistaken for a rail bench.

Also measured en route: `fn_choose_rails()` checked only *address presence*, not
peer reachability — in tonight's exact state it would have **listed a dead rail
on both nodes and hung RCCL bootstrap.** Now gated on a 3-packet ping (a
1-packet gate flaps ~10% of the time on a cold neighbour cache, which would
punish exactly the just-healed rail we most want listed).

**8.6 The memory arithmetic was over the bound.** `FN_GPU_UTIL=0.83` ×
125.1 GiB of GTT (fork patch 0004 re-points reporting at GTT; MEASURED
`mem_info_gtt_total = 134309519360`) ≈ **104 GiB/rank** — over the 80 GiB
residency bound the receipts gate enforces, *and* eating the ~40 GiB/node page
cache the mmap'd engram table faults through **by design**. Now: util 0.62
(≈77.6 GiB), `--kv-cache-memory-bytes` pinned at 12 GiB, `--max-num-seqs 32`.
Expected ~76–78 GiB/rank. The fork was verified to accept both flags
(`vllm serve --help=all` inside the image) before they shipped.

---

## 9. What the adversarial verifiers caught

Two Opus verifiers were instructed to refute the plan. They returned 19 defects,
12 blocking or major. The three that mattered most:

**9.1 Array position is completion-identity-bearing.** The synthesis had
proposed reordering the worklist so the cheap lanes ran first. The verifier read
the harness source and found that task `issue` numbers are assigned **by array
position at arm time**, hashed into the task's completion revision, and
completions are matched on `task_id + revision`. The reorder would have
**silently discarded cp-build's banked completion** — a 14400 s checkpoint on a
serial graph — and spared the four done lanes only by accident. The plan was
reversed to strictly append-only. (The desired property was obtained for free
anyway: the dependency-free doc lanes already sit at positions 4–7, so the
morning package banks before any checkpoint can fail.)

**9.2 A fail receipt would have poisoned the entire campaign.** `receipts-verify.py`
flags *any* status-fail receipt and exits 2; it runs as a campaign gate on
**every attempt**, is re-run inside the repo's unit tests, and those tests are
the first step of `container/build.sh`. So one failed sub-step at 03:00 would
have permanently reddened every later lane, including cp-tp2. This is the origin
of the whole quarantine design (§10) — and it also nearly bit the ROCm 10 probe,
which is precisely why that lane writes outside `results/receipts/`.

**9.3 A 349-character title would have refused the arm outright.** The proposed
cp-weights title exceeded the harness's hard 300-**byte** cap (each em dash
costs 3 bytes), and `tally campaign arm` refuses it at the CLI. The entire night
would have failed at step 5 of the operator checklist with a validation error.
Now machine-checked: every title is verified under 300 bytes before commit.

Others folded in: the residency bound had to be graded by the runner itself (a
`status:pass` receipt that fails the gate's bound is an unfixable permanent
gate failure); the degraded single-arm bench receipt must *derive* its
`arms`/`counterbalanced` fields from rows actually measured rather than stamping
a design it didn't run; the recovery path had to move out of `wait_ready` into
the phase loop to avoid mutual recursion; the per-arm reap had to go cross-node
(the in-container pkill pattern never matches the worker's ray actor, and a rank
holding 60–100 GiB of GTT OOMs the next arm); and the bench needed an interim
receipt after its measurement sweep so a runtime kill can't erase the night's
numbers.

---

## 10. D7 — Receipt discipline: one graded failure costs one step, never the night

Arising from 9.2. Every step that can fail now writes its fail receipt to
**`results/receipts/failed/`** — committed, ledger-reviewed, a typed blocker the
operator reads first — while `receipts-verify.py` lists them as loud WARN lines
**without counting them as violations** (the top-level glob is non-recursive, so
they are outside the grading walk by construction). Applied to: the four run-tp2
steps, preflight, smoke, and both weight-staging receipts.

Two refinements: `run-tp2.sh` now grades the 80 GiB residency bound *itself*, so
the receipt and the gate can never disagree; and a sub-0.9 full-context decode
ratio is **deferral-typed** — the honest measurement lands in quarantine as a
performance finding and the runner exits 0 for that sub-step, so a known-plausible
falloff (12 full-attention layers walking 256K KV; the community shows ~2× by
50–73k) cannot cost cp-bench its night. Hard failures — byte-compare divergence,
residency bound trip, transport errors, prompt undershoot — still fail their step
and their checkpoint. **Quarantine changes where a receipt lands, never whether
the step failed.**

The deviation worth recording: the verifier suggested lowering
`FN_CONTEXT_TARGET` to make the bound achievable. Rejected — that discards the
very number the morning needs *and* still couldn't guarantee the bound.
Quarantine plus non-fatal typing achieves gate safety, keeps the full-depth
measurement, and routes it to the optimization menu.

---

## 11. Reference bars to be judged against tomorrow

| number | value | source |
|---|---|---|
| custom verbs allreduce, full TP=2 op | 105 µs | ds4 `tbv_ar2`, their stack with zero-copy + 8 µs throttle |
| same, host in critical path (v1) | ~228 µs | ds4 `tbv_ar` |
| stream primitive RTT, 64 B / 4 KB p50 | 14.3 µs / 21.8 µs | measured, twins, Python syscall loop |
| stream throughput, 1 stream @ ring 4096 | ~841 MB/s | measured (we run ring 1024 — expect less) |
| TCP over 5GbE, 64 B / 4 KB p50 | 60.4 µs / 137.8 µs | measured |
| TCP over thunderbolt0 | **never measured** | the gap the morning should close first |
| RDMA vs held TCP, end-to-end decode | +3.4% | the one community precedent |
| unheld vs held C-state RTT | 577 µs → 63–90 µs (at budget 0) | measured, round 1 — SUPERSEDED, see below |
| single-node community decode (Q4/Q5, llama.cpp) | 27–50 tok/s | not our comparison class — those quants fit one box |
| single-node CIRU ROCm, 8K cold prefill / decode @ MTP depth 3 | 359 tok/s / 30.8 tok/s | their published results |
| our TP=2 fp8 pair | **unknown — that is what tonight is for** | |

---

## 12. What is honestly still open

1. **MTP end-to-end on gfx1151/ROCm at TP=2 is unproven.** The fork's support is
   real and unit-tested; zero e2e GPU receipts exist anywhere. Decided by
   tonight's spec-on arm, read through acceptance telemetry and the identity
   oracle. Fallback is banked and honest.
2. **Whether rail 0 heals at all** — asymmetric dark end, branch in the operator
   checklist, consequence (no Gate 0 on a wire night) written into three places.
3. **Night wall-clock versus the serial graph.** Doc lanes first pushes cp-tp2
   toward 08:00–10:00 and cp-bench possibly into the afternoon. Deliberate:
   total serial work is order-invariant at maxParallel=1, so running the
   dependency-free lanes first costs nothing and guarantees the morning package
   survives any checkpoint failure. **If lanes are still running at wake, steer;
   do not re-arm.**
4. **Whether cross-arm fingerprints are comparable at concurrency 1** — the
   oracle field will show it; a dirty oracle with a clean per-arm serial replay
   is the QSA-gather signature (#4), not a spec-decode bug.
5. **The full-context decode ratio at 262144** — genuinely uncertain; the QSA
   sparse path argues yes, 12 full-attention layers and the community falloff
   argue no. Decided by which file exists in the morning: `context.json` or
   `failed/context.json`.
6. **USB4STREAM's worth as a transport** — decided against §5.7's trigger rule,
   with first numbers most likely from the attended morning run.
7. **The RDMA userspace/kernel pin match on 7.2.2** — unanswerable tonight by
   design; surfaces only in the attended bring-up.
8. **ROCm 10 viability** — answered by `results/rocm10-probe.json`, with the
   kernel caveat that a probe on the coordinator is strong but not conclusive
   for the pair.

---

## 13. The one-line version, for the tally

TP=2 is *existential* for this checkpoint — 185.5 GB against 128 GB per box —
so tonight optimizes for a transport that cannot wedge while nobody is watching,
puts the night's ambition into the decoding path (MTP, where the headroom
actually is, and which happens to *relax* the transport ceiling ~2.5×), and
banks the measurements that unlock every faster transport tomorrow. The socket
benchmark this run produces is not a consolation prize; it is literally the
ticket that opens the verbs A/B and prices the stream port.

---

## Appendix A — a community prediction, and what our evidence says about it

Posted by another operator running the same dual Strix Halo setup, 2026-08-30:

> it remains to be seen how much of a performance difference there is between
> the various RDMA methods.
> My guess is: No difference between RoCEv2 and Infiniband.
> Small difference to RMDA via USB4 (both implementations, thunderbolt-stream
> and odinlink)

**Verdict: well-calibrated, and our evidence quantifies it — with one taxonomy
correction that changes what the second sentence is even asking.**

**A.1 "No difference between RoCEv2 and Infiniband" — agreed, and on this
hardware the question is close to moot.** Both encapsulations ride the *same*
NHI: the same three DMA rings per controller, the same HopID paths, the same
interrupt moderation. RoCEv2's UDP/IP framing costs header bytes and a little
CPU; at our 5 KB decode payloads serialization is ~1 µs on a 40 Gbps link
(§2.7), so the delta lands far below the noise of the doorbell and interrupt
path that actually dominates. The verb encapsulation is not where the
microseconds live on this silicon.

**A.2 The correction: `thunderbolt_stream` is not an RDMA implementation.**
Grouping it with odinlink as "both implementations" of RDMA-via-USB4 conflates
two genuinely different primitives, and the difference is the whole reason it
earned a separate deliberation here (§5):

> **CORRECTED 2026-08-31 (RUN3-BRIEF §4.6).** The left column below groups
> `strix-rdma` with odinlink as ibverbs. **It is not ibverbs**, and its authors
> explicitly reject soft-RoCE. It belongs on the *right* of this table, with
> `thunderbolt_stream` — it is the project that gets USB4STREAM, it measured
> **29.0 µs/exchange**, and its 15 patches apply to our kernel and our tree. This
> single miscategorisation is what suppressed the best transport option we have.
> Read the row below as **odinlink only**.

| | odinlink (~~/ strix-rdma~~ — see correction above) | thunderbolt_stream |
|---|---|---|
| primitive | ibverbs RDMA — queue pairs, memory registration, one-sided writes | a reliable, ordered, 4 KiB-framed **byte pipe** over raw NHI DMA rings |
| copies | zero-copy into registered memory | **one kernel copy per direction** (`copy_page_from_iter` / `copy_page_to_iter`) |
| ceiling | wire-bound | ~841 MB/s per stream at ring 4096 (less at our 1024) |
| latency measured | — | 14.3 µs RTT @ 64 B, 21.8/25.3 µs p50/p99 @ 4 KB |
| provenance | out-of-tree, rebuilt per kernel | in-tree, maintainer-owned, documented ABI |

So the honest three-way question is not "which RDMA method" but "verbs versus a
copying byte pipe versus TCP" — and each has a different answer per op class
(§5.7): for bulk/prefill the stream loses on bandwidth to both; for the *decode
allreduce*, which is the actual ceiling, a copying pipe at ~22 µs round trip is
competitive precisely because that op is latency-bound, not bandwidth-bound.

**A.3 "Small difference" — our evidence agrees, and puts numbers on "small."**
The one measured end-to-end precedent for RDMA over held TCP on this hardware
class is **+3.4% decode**; `strix-rdma`'s own author calls NHI verbs
"effectively identical to TCP v3" and ships TCP in production. That is the
single most under-appreciated fact in this whole space, and it is why tonight's
run bets its risk budget on MTP rather than on transport.

**A.4 The reframe worth passing back to that thread: the RDMA method is
second-order. Three other variables each dominate it.**

1. **C-state hold** — 577 µs → 63–90 µs RTT, an ~8× effect, **[CORRECTED 2026-08-31 — dotfiles#257: the budget is 100 µs, not 0, and it is NOT free. Holding 0 pins the cores at POLL: ~60 W/box for the last ~62 µs. The C3 block — ~7× of the ~8× effect — is already had at 100 (0.116 ms vs 0.829 ms unheld). Verify with `sudo fleet-postboot-verify`.]** and it
   applies to *every* transport including plain TCP. Anyone benchmarking
   transports without holding `/dev/cpu_dma_latency` at the configured budget on **both** ends is
   measuring their idle governor, not their interconnect.
2. **Whether the host sits in the critical path** — the same ibverbs stack
   measured 228 µs with a host callback and **105 µs** once a doorbell kernel
   plus a GPU spin-and-add moved the host off it. A 2.2× swing with the
   transport held constant.
3. **NHI interrupt moderation** — 8 µs hand-tuned versus ~128 µs stock.

All three are software architecture around the transport, and each is larger
than the encapsulation choice the prediction is about. The practical
implication: a shootout between RoCEv2, IB, and USB4 RDMA that doesn't control
for these three will produce differences that are mostly artifacts of which
stack happened to be tuned.

**A.5 On the baseline being the consolation prize — it is actually the
deliverable.** Nobody has published a TP=2 baseline on a dual Strix Halo pair,
and more pointedly: **TCP-over-thunderbolt0 has never been measured by anyone,
including us** (§11) — the firewall that blocked it was only just opened. Every
comparison in the prediction above is currently being made against a number
that does not exist. Tonight's socket matrix produces it, and it is
simultaneously the Gate 0 artifact that unlocks our own verbs A/B and the
denominator the stream port must beat. A measured baseline is not the
consolation for missing the optimization; it is the precondition that turns
every subsequent optimization from a guess into a delta.
