# START HERE — resuming flashnext after the NixOS work

Written 2026-08-31. Read this first. It assumes the dotfiles issues **#272–#280** have landed
and the twins have been rebooted **once, at the end**.

---

## 0. Before anything: you are not on `main`

Every fix from the weekend lives on a branch, in a worktree. `main` has none of them.

```
worktree : /home/tom/mecattaf/flashnext-gloo
branch   : fix/gloo-socket-ifname
```

```
39e3cce  handoff: reboot at the END, not the start
e0c08fe  handoff: what flashnext needs from NixOS
7849544  handoff: monday-morning lessons
9bff4d9  usb4stream: --function selector, and why cable B cannot be opened
d56325e  usb4stream: make the bench targetable at cable B
1b2d124  tp2: give each rank its own VLLM_HOST_IP
2e62d12  gloo: pin the CPU process group to the wire
79f87fb  <- the campaign integration branch, which itself carries 5 fixes not on main
```

Running `bench/usb4stream-bench.py` from `~/mecattaf/flashnext` will use the version that is
welded to cable A and has the vacuous peer check. **Work in the worktree.** Merging the branch
to `main` is an open decision, not something the weekend did.

---

## 1. First: confirm the reboot did what it was supposed to

The reboot is the verification for dotfiles#275, not just the recovery for #272. Run on **both**
nodes:

```bash
for g in 0-2.1 1-2.1; do for f in fn0 fn1; do
  p=/sys/kernel/config/thunderbolt/stream/$g/$f
  [ -d "$p" ] && echo "$g/$f idx=$(cat $p/index) in=$(cat $p/in_hopid) out=$(cat $p/out_hopid)"
done; done
```

Three possible outcomes, and they mean different things:

| what you see | meaning | what to do |
|---|---|---|
| groups on **cable A only**, no hopid 8 | The anchor fix worked. Cable B is now unprovisioned **on purpose**. | Provision cable B deliberately (see §2), then run the bench. |
| groups on **both cables**, no hopid 8 | Anchor still drifting, but the hopid race is fixed. | Bench can run; reopen #275 with the evidence. |
| **any** group holding hopid 8 | The race with `thunderbolt_net` is not fixed. | Do **not** open that function. See #276. |

Cable identity is not the netdev name and not the index — both are unstable. Anchor on the
neighbour entries and the hopid interlock. The full verbatim topology is in
`handoff/monday-morning-lessons.md` §1.

---

## 2. Then: run the benchmark that has never run

This is the outstanding promise. It failed twice on 2026-08-30/31 with `ENOMEM` at the open,
because cable B's controllers had leaked a DMA ring and there are only three per NHI. The reboot
is what releases them. **Nothing else about the bench needs to change.**

```bash
cd /home/tom/mecattaf/flashnext-gloo

# the abort record is deliberately kept and it ARMS THE IDEMPOTENCE GUARD.
# move it aside or the bench exits 0 without touching anything.
ls results/receipts/usb4stream*.json

# read-only, opens nothing — run on BOTH nodes
python3 bench/usb4stream-bench.py --role probe --cable B --function fn1
ssh 10.99.9.2 'python3 - --role probe --cable B --function fn1' < bench/usb4stream-bench.py
# expect: /dev/tbstream1 both sides, hopids interlocking (coord out 9 -> worker in 9,
#         worker out 10 -> coord in 10), ring_size 1024, throttling 2048

python3 bench/usb4stream-bench.py --cable B --function fn1 --dry-run
# REQUIRE "decision": "would-proceed". If it says would-skip, read base and stop —
# a real run would bank that skip and arm the guard again.

python3 bench/usb4stream-bench.py --cable B --function fn1
```

**Do not pass `--function fn0` on cable B** until #276 is confirmed fixed — that function held
hopid 8, which `thunderbolt_net` occupies on every router in this fleet.

**Do not pass `--cable A`.** It resolves the tensor rail, and a wedge there costs the thing the
whole project is for.

**If the containers are up**, the serve precondition may skip the run. Cleanest is to take the
cluster down first (`bash host/fn-cluster-down.sh`) so the precondition is true on fact rather
than on a cable-awareness code path. Nothing is lost — no serve should be running.

### What the numbers mean when you get them

Read `data.exchange_us["8192"].p50` and `["16384"].p50` — the allreduce-shaped simultaneous
exchange is the decision-relevant figure, not the RTT. `docs/USB4STREAM-TRANSPORT.md` §4
criterion 1 wants **≤ ~40 µs**.

The bar is now anchored to something real for the first time. Measured 2026-08-31 on cable A:

```
TCP over thunderbolt0 : 130.4 us p50, FLAT from 64 B to 16 KiB, against a 34 us floor
TCP over the 5GbE wire:  56.6 us p50 @64 B, 138.3 us @4 KiB
```

That flatness is ~100 µs of `thunderbolt_net` software overhead sitting on a fast fabric — and it
is exactly what the stream primitive bypasses. The module's previously recorded 14.3 µs at 64 B
would be a **~9× win over TCP on the same cable**, not the ~4× that comparing against a different
cable implied.

Caveats when you compare: the historical figures were taken on **cable A** at ring 4096; you are
on **cable B** at ring 1024. A difference may be cable- or ring-specific rather than a failure to
reproduce. Say which you think it is.

---

## 3. RDMA: 7.2.2 is fine. The modules just have to be built against it.

This confused things once, so stating it plainly.

**The staged trees are not "for a better older kernel".** They are dead. Linux has no stable
module ABI: an out-of-tree `.ko` is compiled against one kernel's headers and stamped with a
vermagic string the kernel matches **exactly**, including across point releases. A module built
for 7.2.0 cannot load on 7.2.2 no matter how similar they are. That is why
`/sys/class/infiniband` is empty on both boxes and why `NCCL_IB_DISABLE=1` is not merely policy
but fact.

**The build script already targets 7.2.2** and has since 2026-08-30 — `TARGET_KVER=7.2.2` in
`host/rdma/fetch-and-build.sh`, whose own comment records that the 7.2.0 bake was *"the first
clean series apply on 7.2 headers"*. So the 10-file patch series already survived the hard jump
(7.1 → 7.2). 7.2.0 → 7.2.2 is a point release.

**So the remaining work is one attended build per node**, nothing more:

```bash
bash host/rdma/fetch-and-build.sh        # on each node, attended
ls /sys/class/infiniband/                # expect a device afterwards, both nodes
```

The script refuses to build unless the box actually runs `TARGET_KVER`, and it verifies the peer's
kernel over ssh as a hard gate — so it cannot silently produce another mismatched tree. The one
genuine unknown is whether some 7.2.2 backport moved code the series touches; `git apply` fails
loudly if so.

**`NCCL_IB_DISABLE=1` stays pinned unconditionally even after a device appears.** The point of
that pin is that RCCL must never silently start riding unproven RDMA the moment hardware shows
up. Turning it off is an attended decision gated on `host/rdma/ab-protocol.md`, not a consequence
of the device existing.

### The strategic point, which is why this is in the resume doc at all

This is a **treadmill**. Every kernel bump kills these modules — the build script's own word is
"vermagic-dead". `docs/USB4STREAM-TRANSPORT.md` names that as the argument for preferring the
in-tree stream primitive:

> everything RDMA here rides an **out-of-tree patched stack that dies at every kernel bump**. An
> in-tree, maintainer-shipped, rebuild-free primitive that lands within ~1.5× of the RDMA bar is a
> defensible transport of record for a two-box fleet, precisely because the RDMA treadmill is a
> recurring attended tax.

So §2's benchmark is not a side quest. It is the measurement that decides whether you ever have
to pay this tax again. Run it before deciding anything about RDMA.

---

## 4. Then: TP=2 first light

This does **not** depend on any NixOS change. It was ready before the reboot.

The Gloo blocker is closed and proven — failure reproduced first, then the pinned configuration
succeeded, delivered through the project's own `--env-file` path with no `-e` overrides, verified
reaching a real Ray actor on both nodes, 5/5 on repeats. `VLLM_HOST_IP` is pinned per node so the
TP message queue no longer binds the house WiFi.

```bash
cd /home/tom/mecattaf/flashnext-gloo
bash host/fn-preflight.sh      # expect: identical doctrine env, rtt inside budget, receipt pass
bash scripts/run-tp2.sh
```

**One thing to check first, if dotfiles#274 landed** (`thunderbolt1` given a real `/30`). That is
the moment `fn_choose_rails()` starts emitting `thunderbolt0,thunderbolt1`, and the moment the
comma-list hazard becomes reachable:

```bash
source host/fn-env.sh; echo "$NCCL_SOCKET_IFNAME"; echo "$GLOO_SOCKET_IFNAME"
```

Measured 2026-08-31: `GLOO_SOCKET_IFNAME=thunderbolt0,thunderbolt1` **hangs forever** — no
exception, no log line. Torch creates one transport device per comma-separated name and every one
must both resolve *and* have a peer. The `%%,*` trim in `fn-env.sh` prevents a silent infinite
hang, not a clean error. Do not relax it.

### What the forecast says will break next

Gloo and `get_ip()` are no longer suspects. In order of likelihood:

1. Cold PIECEWISE compile against `fn-cluster-up.sh`'s 2700 s serve poll — the first bring-up
   after a boot is ~25 min of LLVM before the API can answer, and you have just rebooted, so the
   compiler caches are cold. Budget for it; do not read a slow first light as a failure.
2. The first-ever execution of the EP>1 + block-FP8 MoE forward under TP=2. Never run.

Once first light lands, `results/bench/` becomes reachable — which is `docs/USB4STREAM-TRANSPORT.md`
§4 criterion 2, *"is decode actually all-reduce-dominated"*. That is the highest-value unmeasured
quantity in the project: it decides whether any transport work returns anything at all.

---

## 5. Where everything else is

| file | what it is |
|---|---|
| `handoff/monday-morning-lessons.md` | The full record. Thunderbolt estate verbatim (both cables, per-node netdev/PCI/domain/xdomain/service/configfs/index/hopids), every measured number with its harness, the Gloo root cause and proof, and the generalisable lessons. |
| `handoff/nixos-dependencies.md` | Per-issue map of what each host defect costs here, what is blocked vs worked around, and verification recipes. |
| `mecattaf/dotfiles#271` | The dotfiles index, #272–#280. |
| `mecattaf/flashnext#8` | The Gloo root-cause issue. |
| `handoff/README.md` | The catalog-row handoff. Done — kept as record. |

## 6. The one preventive task that is not started

The Gloo bug was **one line below a line we did port**. `fn-env.sh` took
`ds4-cluster-env.sh:29` and elaborated it into the rail chooser; it never read `:30`.

Reconcile **every** export in `ds4-cluster-env.sh` (and its `.tcp.sh` / `.rdma.sh` variants)
against `fn-env.sh`, with each variable either ported or explicitly declined **in writing**.
Anything present there and absent here without a stated reason is a candidate for the same
failure mode, and nothing in this repo would surface the next one.

This is the highest-value preventive work available and it costs an afternoon of reading.

---

## 7. ADDENDUM 2026-08-31 12:25 — the NixOS side is DONE; read this before §1–§4

The dotfiles work this doc waits on has landed, deployed to both twins, and the fleet is
mid-reboot-cycle (worker rebooted and verified 12:16; coordinator reboot is the last act of
that session). What changed relative to the expectations written above:

- **dotfiles #267, #273, #274, #275, #276, #277, #278, #279, #280 are CLOSED** with evidence;
  #270 got the arbitration half (flashnext-lane.target, see below) and stays open for the
  peers-based gateway design; #266 stays open with the residual exposure documented.
- **§1's outcome table: expect outcome (a), and BETTER hopids than this doc predicted.** Cable B
  unprovisioned is confirmed (the sweep released its groups, including the hop-8 fn0, on both
  twins). Cable A's groups are PINNED now: fn0 = 10/10, fn1 = 11/11 on both ends, interlocked,
  verified 12:22. Not 9/9 — the provisioner pins hopids at 10+N so hop 8 is structurally
  unreachable and 9 is headroom. `/var/lib/usb4-stream/rail-identity` on each twin records the
  anchor (nhi + peer_uid) and the hopids.
- **§2's bench on cable B: do NOT hand-provision configfs.** Run `sudo usb4-stream-bench-cable`
  on BOTH twins first — it provisions cable B by NHI (never by name), applies the same 10+N pin,
  and arms the keep-foreign marker so the provisioner's sweep cannot delete your groups
  mid-campaign. `sudo usb4-stream-bench-cable --release` on both when done. Cable B fn0 is safe
  now (no hop-8), but fn1 remains the conservative choice.
- **`GLOO_SOCKET_IFNAME` is now redundant, not wrong** (§4): both twins resolve their own and
  each other's hostnames to 10.99.9.x (verified live with getent, plus across the worker's
  reboot). The two-rank Gloo run without any pin is the acceptance test dotfiles#273 names.
  Retire the pin when convenient; the %%,* trim note still applies while it exists.
- **The rails are `rail0` and `rail2` now, and they are CABLES, not probe order** (dotfiles#266,
  landed 2026-08-31 ~13:30). Read this before you type `thunderbolt0` anywhere: that name no
  longer exists on either twin.
  - `rail0` = cable A = 10.99.0.x, the fast/tensor rail, the one usb4-stream provisions.
  - `rail2` = cable B = 10.99.2.x, the bench and aggregation rail.
  - Both are pinned by `.link Name=` on each NHI's PCI path, per host, so the name means one
    physical cable on both twins across every boot.
  - Verified live on both twins: `rail0` → `pci-0000:c5:00.6` (coord) / `pci-0000:c4:00.5`
    (worker); `rail2` → `pci-0000:c5:00.5` / `pci-0000:c4:00.6`. All three rails ping.
  - `GLOO_SOCKET_IFNAME`, `NCCL_SOCKET_IFNAME` and anything else naming an interface must use
    `rail0`/`rail2`.

  **What the 12:27 reboot actually did**, because the previous version of this bullet claimed
  rail 2 was live and it was not: the netdev names flipped on BOTH twins. `tb-fleet2` refused
  to activate (it required `thunderbolt1` AND cable B's path, which had become contradictory)
  and parked — that fail-safe worked. But `tb-fleet` was name-bound only, so 10.99.0.x came up
  on **cable B** while the streams stayed on cable A. Rail 2 was dark from 12:27 until the fix.
  Both rails are up now, ~0.10 ms each, TCP doors on the right interfaces.

- **Cable A vs cable B is not a TB5-vs-TB3 question.** Both cables train at 20.0 Gb/s per lane,
  2 lanes, 40 Gb/s — re-confirmed live 2026-08-31. Warm-vs-cold is the only difference you will
  measure between them; when both are warm they are within noise of each other (0.093 vs
  0.094 ms over 400 samples). Do not attribute bench deltas to the cable generation.

- **PM QoS now holds 100 µs, not 0** (dotfiles#257). The old 0 admitted POLL alone — a
  full-boost busy-wait that cost **70 W per box at idle**, on an APU where package power is
  shared with the GPU. Now 10 W (coord) / 6 W (worker), with rail RTT ~0.10 ms instead of
  0.054 ms. Nearly the whole latency win was always the C3 block, not the POLL floor (0.10 ms
  against 0.83 ms unconstrained). **This changes your baseline**: an RTT figure banked before
  today was measured on cores that never idled. Re-baseline before comparing transports, and
  expect more GPU boost headroom during a long run.
- **Before a TP=2 run**: `systemctl start flashnext-lane.target` on BOTH twins (it stops
  llama-swap via mutual Conflicts — tested live in both directions); run ranks as transient
  units with `BindsTo=flashnext-lane.target`. `fn-cluster-up.sh/down.sh` should adopt this and
  DROP the swap-arbitration.json arrival-restore — teardown no longer restarts llama-swap;
  it returns on boot or explicit start (a chosen decision, recorded in
  dotfiles modules/flashnext-lane.nix). This repo's script changes are the remaining half of
  dotfiles#270.
- **§3 unchanged**: RDMA still needs the one attended bake per node; the miss is now loud at
  every boot (`journalctl -b -p warning -g fn-rdma`) and `/run/fn-rdma-stock-fallback` is the
  greppable negative.
