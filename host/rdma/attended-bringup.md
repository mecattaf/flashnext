# RDMA attended bring-up -- coordinator/worker, rail 0 only

**This is a ready-to-execute morning plan, not an execution.** RDMA is fully
out of the overnight critical path (operator ruling). No step below runs
unsupervised, and no reboot in this document ever happens without an operator
physically present at the machine -- that rule applies for the whole
document, not just the steps marked ⚠. Read it top to bottom before starting.

Topology this checklist assumes (spec.md, final-qwen-report.md, and the
fleet's own `flake.nix`/host configs, cross-checked directly):

| interface | role | address | notes |
|---|---|---|---|
| `thunderbolt0` | rail 0 -- the ONLY rail RDMA is ever brought up on | `10.99.0.1` (coordinator) / `10.99.0.2` (worker), `/30` | USB4 `c4:00.5`, domain0. **Not** in either host's `networking.firewall.trustedInterfaces` (`hosts/coordinator/eth-fleet.nix:80`, `hosts/worker/default.nix:321` both list only `enp191s0`) -- step 5 opens it explicitly. |
| `thunderbolt1` | rail 1 -- stays plain socket transport, always | link-local, no static address | USB4 `c4:00.6`, domain1 -- never touched by anything in this checklist |
| `enp191s0` | control wire | `10.99.1.1` / `10.99.1.2`, `/30` | 5GbE, ssh/orchestration/staging, lower latency and jitter than either TB rail. **This is the wire everything in this checklist runs over** -- see step 1. |

The overnight transport of record is sockets on both rails. This checklist
only ever changes rail 0. If anything here goes sideways, the rollback path
at the bottom gets you back to that overnight state.

---

## Gate 0 -- this plan may not start yet

Two hard preconditions, checked before step 1, not negotiable:

1. **A committed TP=2-over-TCP benchmark is banked under `results/`.** This
   plan does not begin -- not even the deploy-path fix in step 1 -- until
   that number exists. RDMA is a performance layer on top of a working
   socket-transport cluster, never a substitute for proving the socket path
   first.
2. **No unsupervised reboots. Ever.** Every reboot in this document --
   there are exactly two, one per node, never simultaneous -- requires a
   physically-present operator who can reach the machine's console if the
   network doesn't come back. If you cannot be physically present for both
   reboots in the same session, stop here and come back when you can.

---

## 1. MANDATORY, first, before any kernel or module change: fix the worker's deploy path ⚠

This has nothing to do with RDMA modules and must happen before any of them.
Skipping it is how a worker that reboots into a kernel whose Thunderbolt rail
doesn't come up cleanly ends up **unreachable by the tool that would fix it.**

**The problem, verified directly against `dotfiles/flake.nix`:** deploy-rs
dials the worker at `10.99.0.2` -- rail 0, Thunderbolt -- not over the 5GbE
wire (`hostname = if host == "worker" then "10.99.0.2" else host;`, guarded
by asserts pinning that exact address). The deploy ssh options
(`fleetDeploySshOpts`) pass `-F /dev/null`, so `~/.ssh/config` cannot rescue
a stale route either -- deploy-rs's path to the worker and rail 0's path to
the worker are the same path. A worker that comes back from a reboot with
Thunderbolt down has severed the one channel that could push it a fix.

**The fix:**
1. Repoint the worker's deploy hostname to the 5GbE address (`10.99.1.2`) or
   the fleet identity, whichever the flake's other deploy targets already use
   for consistency.
2. Update the two asserts that currently pin `10.99.0.2` to pin the new
   address instead (`assert self.deploy.nodes.${strixWorker}.hostname == "10.99.0.2"`
   and the alias-membership assert beside it).
3. **Deploy-test this change with rail 0 administratively down**
   (`sudo ip link set thunderbolt0 down` on the worker) before trusting it --
   confirm deploy-rs can still reach and activate a config on the worker with
   zero Thunderbolt connectivity. This is the actual rehearsal for the
   scenario this fix exists to survive.
4. Bring rail 0 back up, confirm nothing else broke, and only then move to
   step 2.

Do not proceed past this step on the strength of "it should work" -- the
whole point is that this is the one prerequisite you cannot verify after the
fact if it's wrong.

---

## 2. Prerequisites re-check ⚠

Do this fresh, every session -- do not trust yesterday's run.

1. **Kernel match, both nodes, and know which kernel you're on.** As of the
   last substrate check, both nodes run the **stock** nixpkgs kernel
   (`/run/booted-system/kernel` resolves to a plain kernel, not
   nix-strix-halo's `linux-thunderbolt` -- that derivation has no build
   output or `.drv` in the store at all yet). `uname -r` on each must equal
   the pin `fetch-and-build.sh` was last run against. If either node took an
   update since the last build, rebuild before continuing.
2. **Secure Boot off, both nodes.** `fetch-and-build.sh` gates this locally
   on every run; re-run it (or `mokutil --sb-state`) on both nodes now if it
   has been more than a day. Coordinator has tested disabled before; worker
   has not been independently confirmed outside that script's own check --
   do not assume.
3. **⚠ Re-verify the C-state hold after every reboot regardless.** As of
   2026-08-28 the hold IS declarative and live on both nodes
   (`dotfiles#238` closed: `modules/lowlat-cluster.nix`, worker
   reboot-tested; the fleet-latency tripwire is armed but only fires after a
   one-hour sustain — too slow to protect a same-morning A/B). Jumbo MTU
   remains implemented-but-off by design. Before touching RDMA, confirm on
   both nodes:
   ```
   cat /dev/cpu_dma_latency | od -An -tu4   # expect 0
   ip link show thunderbolt0 | grep mtu     # expect mtu 65520
   ```
   If either is missing after a reboot, re-apply it **before** running the
   A/B in `ab-protocol.md` -- a crippled TCP baseline (577us RTT instead of
   the held ~77us) invalidates the comparison in RDMA's favor.
4. **Userspace verbs provider: already realized, don't rebuild it.**
   nix-strix-halo already has `thunderbolt-ibverbs-0.3.4` and
   `rdma-core-usb4-63.0` in the store on both nodes. `fetch-and-build.sh`
   checks for these and does not attempt a redundant build by default. What
   it cannot confirm is whether that already-realized pair was built from
   the same `thunderbolt-ibverbs` commit this package pins for the kernel
   module (`76ba39b`) or a different one -- see REPORT.md. Treat a version
   mismatch here as a plausible explanation if `ibv_devices` behaves oddly
   later, not a mystery.
5. **Passwordless ssh, both directions, over `enp191s0`.** Re-confirm after
   step 1's deploy-path change, specifically -- that step just modified the
   thing you're about to rely on.

---

## 3. Choose a route

Two ways to get a matched Thunderbolt/RDMA module set onto stock hardware.
Evaluate both; the recommendation is route (a).

### Route (a) -- out-of-tree matched modules against the running stock kernel

What `fetch-and-build.sh` does today: build `thunderbolt` (core),
`thunderbolt_net`, and `thunderbolt_ibverbs` as out-of-tree modules against
the **currently-booted stock kernel's** headers, at the pinned SHAs.

- **Boot kernel never changes.** The known-good fallback state is trivially
  "the temporary boot-time module load never gets deployed" -- a plain stock
  boot, zero kernel-version risk to anything else on the box (GPU/ROCm stack
  included).
- **Blast radius:** three out-of-tree modules. Nothing else about the
  running system changes.
- **Still requires one reboot per node** (see step 6) -- the patched core
  must be the first thunderbolt driver bound at boot, and live-swapping it
  over an already-bound stock driver is exactly the forbidden live-reload
  case. But the reboot returns to the *same* kernel either way.

### Route (b) -- nix-native: deploy nix-strix-halo's `linux-thunderbolt` kernel

Full kernel switch, both nodes, instead of out-of-tree modules.

- **`linux-thunderbolt` does not exist in the store yet.** No output, no
  `.drv`. Building it is a first-ever realization on this hardware -- unknown
  duration (a full kernel build), unknown breakage, no prior boot history to
  lean on.
- **Every driver on the box is now on a different kernel version, not just
  thunderbolt** -- most importantly the `amdgpu`/ROCm stack this box exists
  to serve on. A kernel swap that breaks GPU support is a much worse outcome
  than RDMA simply not coming up.
- **Effectively re-runs first-light.** Both nodes moving off the
  known-working stock kernel means re-verifying the whole serving stack, not
  just RDMA, before trusting the box again.
- **Rollback is a NixOS generation switch** (clean in principle -- the old
  generation stays selectable), but if the new kernel doesn't bring networking
  up at all, selecting it back still needs the same physical presence this
  plan already requires for every reboot -- so this isn't a new risk
  category, just a much bigger one within the same category route (a) barely
  touches at all.

**Recommendation: route (a).** Route (b) is a legitimate path if the fleet
ever formally adopts RDMA as a standing capability, but that's a separate,
larger, better-tested project -- not something to improvise inside a single
attended morning session on top of a benchmark-gated, reversible-by-default
plan. The rest of this checklist assumes route (a); where route (b) diverges,
it's called out explicitly.

---

## 4. Stage the modules (both nodes, can run in parallel)

```
host/rdma/fetch-and-build.sh          # on coordinator
host/rdma/fetch-and-build.sh          # on worker
```

This only stages files and prints a plan -- nothing is loaded, blacklisted,
or rebooted. Confirm both runs printed the same vermagic per module before
continuing.

---

## 5. Open the firewall for rail 0 -- scoped, not blanket trust ⚠

Neither host trusts `thunderbolt0` today (both firewalls list only
`enp191s0` in `trustedInterfaces`, confirmed directly against
`hosts/coordinator/eth-fleet.nix:80` and `hosts/worker/default.nix:321`).
RDMA/RoCEv2 traffic (and CM connection setup) needs an explicit admission on
that interface, not a blanket trust flip -- this fleet's own config already
has a comment warning against "re-blanket-trusting an interface," and the
existing pattern for scoped per-interface admission (used for `wlp192s0` and
`tailscale0` elsewhere in the same tree) is the right shape to follow here:

```nix
networking.firewall.interfaces.thunderbolt0.allowedUDPPorts = [ 4791 ];  # RoCEv2
```

Push this to both nodes (bundle it with step 6's temporary config delta,
same deploy). Do **not** add `thunderbolt0` to `trustedInterfaces` -- that
opens every port on the interface, not just the one RDMA needs, and is a
much bigger and more permanent-feeling change than this attended session
calls for.

---

## 6. Prepare the boot-time module load ⚠

The non-negotiable rule from the reference bring-up, unchanged by any
adaptation here: **the patched `thunderbolt` core must be the first
thunderbolt driver bound at boot.** Loading it later, over a stock core the
kernel already bound at boot, or hot-swapping the core on a live box, wedges
the Thunderbolt HopID/tunnel allocator and needs a second coordinated
recovery to clear. Only `thunderbolt_net` (the leaf module) is safe to
hot-swap.

Because this bring-up is deliberately attended rather than a permanent fleet
default, do this as a **temporary, deploy-rs-pushed config delta on both
nodes**, not a permanent fleet change:

- Blacklist the stock `thunderbolt` and `thunderbolt_net` modules for this
  boot only.
- Add a oneshot unit, ordered before `bolt.service`, that inserts the staged
  `thunderbolt-patched.ko` then `thunderbolt_net.ko` from the path
  `fetch-and-build.sh` printed -- falling back to the stock pair only if the
  patched core itself fails to insert (never stock net over a patched core
  *on the same host*: the ring ABI mismatches there and that is the
  panic-on-cable-connect case).
- Bundle step 5's firewall rule into the same deploy.

Push this to **both** nodes now and confirm the deploy succeeded on both.
Do not reboot either node onto this config yet -- see step 7 for why the
reboot itself is sequential, not simultaneous.

---

## 7. The reboot: WORKER FIRST, NEVER BOTH AT ONCE ⚠⚠

This inverts the reference bring-up's "reboot both together" instruction --
deliberately, per operator ruling: no unsupervised dual reboot, full stop.
The coordinator holds the known-good kernel/config until the worker is
verified back; only then does the coordinator follow, with the operator
physically present for both.

**Why this is safe despite the reference's own "matched set" warning:** that
warning is about mismatched core/net *on the same host* -- the boot unit in
step 6 is all-or-nothing by construction (patched core+net together, or
stock core+net together, never a mix, on either box individually). It says
nothing about the two hosts running *different* module versions from each
other during the window between reboots, because they don't have to agree:
`thunderbolt0`'s IP link (what `ping`, ssh, and the `tb-link-heal` reachability
check all use) is packets over a USB4 tunnel, not a shared DMA-ring ABI --
that ABI dependency is local to each host's own core+net pairing. The one
thing that **does** require both hosts to already be running the matched set
is RDMA/ibverbs itself, which is why step 8 explicitly waits for both nodes,
not just the rebooting one.

**Sequence:**
1. Reboot the **worker only** onto the step 6 config.
2. Verify it's back, entirely over the 5GbE wire (step 1 made this
   possible): ssh reachable at its wire address, `thunderbolt0` retrained
   with its `10.99.0.2` address (ordinary IP-link connectivity to the
   still-stock coordinator, safe per the reasoning above), and
   `tb-link-heal.timer` reporting green (no PD-reset stamp freshly written).
3. Only after all three are confirmed does the coordinator take the same
   config and reboot -- with the operator still physically present.
4. Do not load `thunderbolt_ibverbs` on either node until **both** have
   rebooted and been verified -- that's step 8, deliberately after this
   whole sequence, not folded into it.

**What a wedged port controller looks like, on whichever box is mid-reboot:**
- `thunderbolt0` never appears, or appears with no carrier and no
  `10.99.0.x` address after a couple of minutes.
- `ls /sys/bus/thunderbolt/devices/` on either box shows no peer XDomain
  entry (no `N-1`..`N-9`-shaped name) -- the "PD-blind" signature.
- `tb-link-heal.timer` (already running on both nodes, 2-minute cadence)
  attempts its own escalation ladder automatically: ping check, then
  XDomain reconnect, then NHI unbind/rebind, and only as a last resort a
  physical PD reset, **rate-limited to one shot per 1800s**
  (`/var/lib/tb-link-heal/pd-reset-stamp`). If it just fired, it will not
  fire again for up to half an hour -- do not sit and wait for the timer.

**Recovery, at the affected machine:**
```
sudo framework_tool --pd-reset 2
sleep 5
```
If that alone doesn't bring the link back, unbind/rebind the USB-C PD
controller the same way the heal script's own last resort does
(`ucsi_acpi`'s `USBC000:00`), then give `tb-link-heal.timer`'s next tick a
chance to finish the reconnect. If the worker's Thunderbolt identity still
doesn't reappear, you are not stuck -- step 1 means you can still reach it
over the wire, diagnose from there, and reboot it again without touching the
coordinator at all. That containment is the entire point of doing this
sequentially instead of together.

---

## 8. RoCE bring-up (both nodes, only after BOTH have rebooted and verified per step 7)

Confirm first, on both:
```
ip -4 addr show thunderbolt0   # expect inet 10.99.0.1/30 (coordinator) or 10.99.0.2/30 (worker)
```

Then, on each node, load the verbs provider by hand -- attended, not a boot
unit, per the "no unattended install path" rule:

```
sudo modprobe configfs ib_core ib_uverbs      # -a form if your modprobe needs it for multiple names
sudo insmod <staged>/thunderbolt_ibverbs.ko \
  profile=linux_perf bind_services=1 allocate_rings=1 start_rings=1 \
  negotiate_native=1 enable_tunnels=1 register_verbs=1 \
  native_tx_max_inflight=128 \
  roce_netdev=thunderbolt0
```

Note what is **not** in that command: `native_rc_split_zcopy=1`. That knob
belongs to a local zero-copy patch this package does not carry (see
`REPORT.md`), and the driver's own documentation names dropping it as the
supported fallback -- "needs no cross-box coordination." Do not add it
unless both nodes are rebuilt from an identical patched tree.

Rename the device if it did not come up as `usb4_rdma0`:
```
D=$(ls /sys/class/infiniband/ | head -1)
[ "$D" = usb4_rdma0 ] || sudo rdma dev set "$D" name usb4_rdma0
```

---

## 9. Verify gate -- must pass on BOTH nodes before any benchmark

```
ls /sys/class/infiniband/                              # -> usb4_rdma0, and ONLY usb4_rdma0
rdma link                                               # port state ACTIVE, PHYS_STATE LinkUp
cat /sys/class/infiniband/usb4_rdma0/ports/1/gids/1     # non-zero (RoCEv2 IPv4 GID)
ibv_devices                                             # lists usb4_rdma0
```

If `gids/1` is all-zero, `thunderbolt0` doesn't have its `10.99.0.x` address
yet -- fix that first, don't chase the GID table. If `usb4_rdma0` never
appears, re-check vermagic on both nodes before anything else. If
`ibv_devices` behaves oddly despite the kernel side looking healthy, revisit
step 2 item 4 -- the already-realized userspace's exact source commit is
unverified against this package's kernel-module pin.

---

## 10. Single-rail contract -- confirm rail 1 is untouched

This is the rule the task record is most emphatic about: **never bring RDMA
up on both rails.** With two native rails simultaneously carrying RDMA, both
peers sit at route `0x2` in each other's domains and the source-blind
control handler on either box cross-matches the other's HELLOs and poisons
its HopID state -- a corruption bug, not a slowdown, and needs a full recovery
to clear.

Confirm rail 1 stays clean, on both nodes:
```
ls /sys/class/infiniband/                 # exactly ONE entry -- usb4_rdma0
readlink -f /sys/class/infiniband/usb4_rdma0/device      # resolve back to c4:00.5 (domain0), NOT c4:00.6 (domain1)
ip -4 addr show thunderbolt1              # link-local only, unchanged from before this checklist started
```

Do not run anything resembling a "second cable" prep step. There is no such
step in this package, deliberately -- see REPORT.md. `thunderbolt1` stays
plain socket transport, exactly as it was overnight.

---

## 11. RCCL env flip -- one file, byte-identical on both ranks

Write a single env delta (analogous in spirit to how the base cluster env is
already split into a shared file plus a transport-specific override) that
sets, and nothing else:

```
export NCCL_IB_DISABLE=0
export NCCL_IB_HCA=usb4_rdma0        # exact device name, not a prefix -- an
                                      # ambiguous prefix match after any link
                                      # reset is a known cause of
                                      # ncclCommInitRank "internal error"
export NCCL_IB_GID_INDEX=1
```

Copy that file to both nodes byte-for-byte -- diff it, don't eyeball it. A
silently-diverged env between the two ranks is the "sweep a clobbered knob,
report no difference" failure mode; it will not error, it will just measure
nothing. Proceed to `ab-protocol.md` from here.

---

## 12. Rollback -- TCP restored, modules gone only via reboot

1. Delete or stop sourcing the env delta from step 11 on both nodes. Confirm
   both ranks are back to the shared socket-transport env, byte-identical.
2. Revert the temporary deploy-rs config from steps 5-6 (undo the blacklist,
   the boot-time insmod unit, and the firewall rule) and push the stock
   config to both nodes.
3. **Reboot both boxes to revert -- worker first, same as step 7, not
   simultaneous.** Do not `rmmod` the core live to "save a reboot" -- that is
   the exact live-unload case the non-negotiable rule forbids.
4. After both boxes are back, re-run the step 2 item 3 C-state/MTU check
   before trusting any subsequent TCP measurement.
5. Leave step 1's deploy-path fix in place -- it's a correctness fix for the
   fleet's deploy story generally, not an RDMA-specific change, and there's
   no reason to revert it.
