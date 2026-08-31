# What flashnext needs from NixOS

Written 2026-08-31, after the Gloo blocker was closed and the USB4STREAM bench was run twice
and stopped twice by host configuration rather than by anything in this repository.

The twins' NixOS config lives at `~/mecattaf/dotfiles`. This file is the flashnext-side view
of it: for each host-level defect, what it costs *here*, what it unblocks, and how to verify
it once the change lands. The dotfiles-side index is **mecattaf/dotfiles#271**.

The short version: **two host defects have been the binding constraint on this project's
progress, not anything in the engine or the fork.** One cost a full night of TP=2 bring-up. The
other is why the USB4STREAM transport decision still has no numbers.

---

## 1. The dependency map

| dotfiles | what it is | what it costs flashnext | status here |
|---|---|---|---|
| **#272** | Cable B's Thunderbolt controllers leaked a DMA ring on 08-30; reboot needed | `bench/usb4stream-bench.py` cannot open a stream on cable B at all — `ENOMEM` in 1 ms on both `fn0` and `fn1`. The §4 trigger criteria are unevaluable. | **Blocking.** Tool side is done and committed; it runs the moment the rings are back. |
| **#273** | Hostname resolves to `127.0.0.2`, so hostname-derived binds land on loopback | Cost the entire 08-30 night. TP=2 died at `Gloo connectFullMesh`. | **Worked around** by `GLOO_SOCKET_IFNAME` (commit `2e62d12`). The workaround covers torch only. |
| **#274** | `thunderbolt1` unaddressed, i.e. addressed-but-peerless | `fn_choose_rails()` must keep a peer-reachability gate and a `%%,*` trim purely to avoid a silent infinite hang. Blocks cable B as a second socket rail. | **Guarded here.** The guard is load-bearing and must not be relaxed. |
| **#275** | `usb4-stream.nix` provisions the wrong cable (name-vs-cable anchor) | Root cause of #272. Also the reason cable B looked like a safe, deliberately provisioned target when it was an accident. | **Blocking, indirectly.** Fixing it is what stops #272 recurring. |
| **#276** | Cable B's `fn0` sits on hopid 8, which `thunderbolt_net` holds | `--function fn0` on cable B must never be opened. Halves the available stream pairs on that cable. | **Guarded here** via `--function fn1`. Needs a configfs write to fix, which this repo is forbidden to do. |
| **#277** | `worker` resolves to the house WiFi (8.9 ms vs 0.139 ms) | Latent. Nothing here resolves the worker by name — `FN_WORKER_HOST=10.99.9.2` is numeric. A future by-name lookup would silently take a 64× slower path. | **Not currently hit.** Keep it that way: never resolve fleet peers by name. |
| **#279** | `fn-rdma` stages 7.1.4/7.2.0 modules against a 7.2.2 fleet | No verbs device on either node. `host/rdma/attended-bringup.md`'s A/B **cannot be run at all**, so the RDMA-vs-sockets question is undecidable on evidence, not merely undecided. | **Blocking** the RDMA lane entirely. `NCCL_IB_DISABLE=1` is correct and should stay. |
| **#280** | `ttm.pages_limit` = all 128 GiB, nothing reserves page cache | `FN_GPU_UTIL=0.62` is the *only* thing protecting the ~40 GiB page cache the mmap'd engram table needs. A second GPU consumer or one raised knob evicts it, and the symptom is a decode-latency collapse that looks like a model or transport bug. | **Honour-system.** The most expensive failure mode in the estate, because it misattributes. |
| **#267** | Coordinator's NHI rebind hardcodes the worker's PCI functions | Plausibly upstream of #275 — it is why the coordinator's enumeration can settle with `thunderbolt0` naming the other cable. | Not directly hit here; fix it before trusting #275's fix. |
| **#270** | llama-swap should be the single gateway; today nothing arbitrates | `fn-cluster-up.sh` stops llama-swap and records an arrival state, but that protocol is one-directional and imperative. A reboot brings llama-swap back and not the pair; a `nixos-rebuild switch` restarts it underneath a live run and invalidates our arrival record. | **Partially handled here**, and it cannot be fully handled here. |

---

## 2. What is unblocked today, with no NixOS change

Everything on the TP=2 critical path except first light itself:

- **Gloo is fixed and proven.** Failure reproduced first (`local=[127.0.0.1] → remote=[127.0.0.2]`,
  rank 0 fails in 6.3 s, rank 1 hangs), then the pin succeeds, then vLLM's own first
  post-world-group collectives run across machines: `_node_count == 2`, `broadcast_object_list`
  crosses, a 4 MiB CPU all-reduce in 11 ms. Delivered through `--env-file` with no `-e`
  overrides. 5/5 on repeats.
- **`VLLM_HOST_IP` is pinned per node**, so the TP `MessageQueue` no longer binds the house WiFi.
  The next wall was pre-empted rather than discovered.
- **Transport is measured** for the first time: TCP-over-`thunderbolt0` at 130.4 µs p50 flat from
  64 B to 16 KiB against a 34 µs floor, and the 5GbE wire at 56.6 µs / 138.3 µs.
- **The bench is cable- and function-selectable**, with a mismatch guard that has already caught
  a real cross-function error before any device was touched.

So the next flashnext action does **not** depend on dotfiles: attempt TP=2 first light. The
forecast's remaining candidates are the cold PIECEWISE compile against the 2700 s serve poll, and
the first-ever execution of the EP>1 + block-FP8 MoE forward. Gloo and `get_ip()` are no longer
suspects.

---

## 3. What is blocked, and on exactly what

**The transport decision.** All three of `docs/USB4STREAM-TRANSPORT.md` §4's criteria are
currently *unevaluable*, not failed:

1. exchange p50 at 8–16 KiB — needs `results/receipts/usb4stream.json`, which needs **#272**.
2. "is decode actually all-reduce-dominated" — needs `results/bench/`, which needs first light.
3. the attended RDMA A/B — needs a verbs device, which needs **#279**.

Two of the three are host-side. Criterion 2 is ours and is the highest-value unmeasured quantity
in the project: it decides whether *any* transport work returns anything at all.

**RDMA Gate 0** is separately not satisfied, on two grounds: what we measured is a raw-socket and
CPU-Gloo proxy rather than a TP=2 benchmark, and the gate's own verification command
(`jq '.data.transport.fn_transport_rung' results/receipts/bench.json`) targets a file that does
not exist. Both are ours to fix, but even a satisfied Gate 0 unblocks a *protocol*, not a
*capability*, until #279 lands.

---

## 4. What this repo should change regardless — do not wait for the host

The host fixes remove the causes. These reduce the blast radius when the next one appears.

1. **Never resolve a fleet peer by name.** `FN_WORKER_HOST` and `FN_HEAD_IP` are numeric and must
   stay numeric. #277 is only latent because of that discipline.
2. **Keep the `%%,*` trim and the peer-reachability gate in `fn_choose_rails()`.** Measured
   2026-08-31: `GLOO_SOCKET_IFNAME=thunderbolt0,thunderbolt1` **hangs forever** with no exception
   and no log line — torch creates one transport device per comma-separated name and every name
   must resolve. The guard prevents a silent infinite hang, not a clean error. Two design
   documents stated this wrongly, in the direction that would tempt someone to remove it. The
   comment at `host/fn-env.sh` now says so.
3. **Audit every export in `ds4-cluster-env.sh` against `fn-env.sh`.** The Gloo bug was one line
   below a line we did port. Each variable should be either ported or explicitly declined *in
   writing*. Anything present there and absent here without a stated reason is the next blocker.
   This is the single highest-value preventive task in the repo and it is not started.
4. **Stamp `data.transport.fn_transport_rung` into `results/receipts/socket-transport.json`** and
   re-point `attended-bringup.md`'s `jq`, so Gate 0 becomes verifiable rather than permanently
   unverifiable.
5. **Bank `netdev_delta` and full histograms in the Gloo all-reduce bench.** Its numbers currently
   have no on-disk proof of which interface they used, which is why the "ethernet beats
   thunderbolt on all-reduce" signal cannot be relied on for magnitude.
6. **Treat `FN_GPU_UTIL` as a safety interlock, not a tuning knob** until #280 is resolved. Raising
   it evicts the engram table's page cache, and the symptom is a latency collapse that looks like
   a model or transport problem.

---

## 5. Verification after each host change

Run these from a flashnext worktree on the coordinator.

**After #272 (reboot):**
```bash
# the rings should be back — probe first, it opens nothing
python3 bench/usb4stream-bench.py --role probe --cable B --function fn1
ssh 10.99.9.2 'python3 - --role probe --cable B --function fn1' < bench/usb4stream-bench.py
# expect /dev/tbstream1 both sides, hopids 10/9 and 9/10
mv results/receipts/usb4stream.json results/receipts/usb4stream.aborted-enomem-0927.json 2>/dev/null
python3 bench/usb4stream-bench.py --cable B --function fn1 --dry-run   # expect would-proceed
python3 bench/usb4stream-bench.py --cable B --function fn1
```
Do **not** use `--function fn0` on cable B until #276 lands. Also expect `llama-swap` to be
running again after the reboot — `fn-cluster-up.sh` will stop it, but check the arrival record is
honest.

**After #273 (hostname → fleet identity):**
```bash
getent hosts "$(hostname)"           # expect 10.99.9.1, not 127.0.0.2
podman exec flashnext-pair python3 -c \
  'import socket; print(socket.gethostbyname(socket.gethostname()))'
```
Then re-run the two-rank Gloo probe **without** `GLOO_SOCKET_IFNAME` set. If it now succeeds, the
class is fixed at the source. Keep the pin regardless — it is correct and it is what makes the
CPU group's fate independent of rail health — but its removal should no longer be catastrophic.

**After #274 (`thunderbolt1` addressed):**
```bash
source host/fn-env.sh; echo "$NCCL_SOCKET_IFNAME"
```
Expect `thunderbolt0,thunderbolt1` once rail 1 has a routable peer. **Verify the `%%,*` trim still
applies to the gloo fallback branch** — this is the moment the comma hazard becomes reachable.
Then re-run `host/fn-preflight.sh` and confirm the byte-diff still passes on both ranks.

**After #279 (RDMA re-bake):**
```bash
ls /sys/class/infiniband/            # expect a device, on both nodes
```
`NCCL_IB_DISABLE=1` stays pinned unconditionally regardless — the point of that pin is that RCCL
must never silently start riding unproven RDMA the moment a device appears. Changing it is an
attended decision gated on `host/rdma/ab-protocol.md`, not a consequence of the device existing.

**After #280 (whatever is decided):**
```bash
free -g                              # watch buff/cache during a serve
```
The engram table needs its ~40 GiB. If `buff/cache` collapses during a run, the table is being
served from NVMe on every gather and the decode numbers are meaningless.

---

## 6. The pattern worth naming

Both host defects share a shape, and it is the same shape as the Gloo bug itself: **a wrong value
that is nonetheless valid.**

- `127.0.0.2` is a real, bindable address, so the library's loopback fallback never fired.
- `169.254.x` on an unaddressed cable is a real, resolvable address, so torch's "name does not
  resolve" error never fired.
- A netdev *name* is a real, present anchor, so the provisioner's carrier gate never fired.

In every case the software had a correct fallback and never reached it, because something
plausible was available first. Configurations where wrong is **absent** fail loudly and cheaply.
Configurations where wrong is **plausible** fail silently and cost nights.

That is the argument for fixing these at the host layer rather than adding another guard here.
Guards accumulate, and each one is a thing a future maintainer can remove without knowing what it
was for.
