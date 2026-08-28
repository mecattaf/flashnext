# RDMA attended bring-up -- coordinator/worker, rail 0 only

Morning checklist — the operator is **physically present** for every ⚠ step;
nothing here runs unattended, and nothing here starts before a committed
TP=2-over-sockets benchmark exists under results/. Read it top to bottom
before starting; several steps are hard to reverse without a second
coordinated reboot. Wherever you see ⚠, physical presence at the machine (or
at minimum, a console that survives the Thunderbolt link dying) is not
optional.

Topology this checklist assumes (spec.md, final-qwen-report.md):

| interface | role | address | notes |
|---|---|---|---|
| `thunderbolt0` | rail 0 -- the ONLY rail RDMA is ever brought up on | `10.99.0.1` (coordinator) / `10.99.0.2` (worker), `/30` | USB4 `c4:00.5`, domain0 |
| `thunderbolt1` | rail 1 -- stays plain socket transport, always | link-local, no static address | USB4 `c4:00.6`, domain1 -- never touched by anything in this checklist |
| `enp191s0` | control wire | `10.99.1.1` / `10.99.1.2`, `/30` | 5GbE, ssh/orchestration/staging, lower latency and jitter than either TB rail -- do everything remote from here |

The overnight transport of record is sockets on both rails. This checklist
only ever changes rail 0. If anything here goes sideways, the rollback path
at the bottom gets you back to that overnight state.

---

## 0. Prerequisites re-check ⚠

Do this fresh, every session -- do not trust yesterday's run.

1. **Kernel match, both nodes.** `uname -r` on each must equal the pin
   `fetch-and-build.sh` was last run against. If either node took an update
   since the last build, rebuild before continuing -- vermagic mismatches
   fail silently at insmod time, not at build time.
2. **Secure Boot off, both nodes.** `fetch-and-build.sh` gates this locally
   on every run; re-run it (or `mokutil --sb-state`) on both nodes now if it
   has been more than a day. Coordinator has tested disabled before; worker
   has not been independently confirmed outside that script's own check --
   do not assume.
3. **⚠ The C-state / MTU hold is still transient, not yet permanent.**
   `dotfiles#238` (making `cpu_dma_latency=0` and MTU 65520 permanent) is
   still open. Both are currently applied by *transient* units. A reboot --
   which step 3 below requires -- can silently drop them. Before touching
   RDMA, confirm on both nodes:
   ```
   cat /dev/cpu_dma_latency | od -An -tu4   # expect 0
   ip link show thunderbolt0 | grep mtu     # expect mtu 65520
   ```
   If either is missing post-reboot, re-apply it **before** running the A/B
   in ab-protocol.md -- a crippled TCP baseline (577us RTT instead of the
   held 63-90us) invalidates the comparison in RDMA's favor and manufactures
   exactly the "RDMA-vs-crippled-TCP" measurement error the record already
   flags as a community-wide mistake.
4. **Module set staged and matched.** Confirm `MANIFEST` vermagic is
   identical between the two nodes' staged output (`fetch-and-build.sh`'s
   final printout tells you where). Do not proceed on a vermagic mismatch --
   see the README's "matched set or the box panics on cable connect" rule.
5. **Passwordless ssh, both directions, over `enp191s0`.** You will be
   running commands on both nodes in short order; confirm now, not mid-reboot.

---

## 1. Stage the modules (both nodes, can run in parallel)

```
host/rdma/fetch-and-build.sh          # on coordinator
host/rdma/fetch-and-build.sh          # on worker
```

This only stages files and prints a plan -- nothing is loaded or blacklisted
yet. Confirm both runs printed the same vermagic per module before continuing.

---

## 2. Put the matched core+net where it loads before anything else claims the device ⚠

The non-negotiable rule from the reference bring-up, unchanged by any of our
adaptations: **the patched `thunderbolt` core must be the first thunderbolt
driver bound at boot.** Loading it later, over a stock core the kernel
already bound at boot, or hot-swapping the core on a live box, wedges the
Thunderbolt HopID/tunnel allocator and needs a second coordinated reboot to
clear. Only `thunderbolt_net` (the leaf module) is safe to hot-swap.

Because this pair is NixOS and this bring-up is deliberately attended rather
than a permanent fleet default, do this as a **temporary, deploy-rs-pushed
config delta on both nodes**, not a permanent fleet change:

- Blacklist the stock `thunderbolt` and `thunderbolt_net` modules for this
  boot only.
- Add a oneshot unit, ordered before `bolt.service`, that inserts the staged
  `thunderbolt-patched.ko` then `thunderbolt_net.ko` from the path
  `fetch-and-build.sh` printed -- falling back to the stock pair only if the
  patched core itself fails to insert (never stock net over a patched core:
  the ring ABI mismatches and that is the panic-on-cable-connect case).

Push this to **both** nodes and confirm the deploy succeeded on both before
step 3. Both nodes carry the staged config before any reboot; the reboots
themselves are then sequenced worker first per section 3 — the rails stay
down through the mismatch window, so at no point do a patched and a stock
core negotiate.

---

## 3. The reboot sequence ⚠⚠ — operator ruling reconciled with matched-set physics

Two constraints meet here and both are real. The reference doctrine says the
two patched cores must never face each other mismatched — a mismatched set
"panics on cable connect", and staggered ibverbs reloads wedge the HopID
allocator. The operator ruling says **never both boxes down blind at once**
— always keep one reachable box, **worker first**, coordinator held until the
worker is verified back. The sequence below satisfies both by keeping the
Thunderbolt rails administratively down through the entire mismatch window,
so the patched and stock cores never negotiate with each other:

1. ⚠ On both boxes: `nmcli connection down tb-fleet` (rail 0) and confirm
   rail 1 carries no profile; the boxes now talk only over the 5GbE wire.
2. Reboot the **worker first** onto the staged config. The coordinator holds
   the known-good state and your session survives on the wire. Verify the
   worker back: ssh over `10.99.1.2`, new modules present (`modinfo`), zero
   failed units. If the worker does not come back clean, STOP — the
   coordinator was never touched and the fleet is one `deploy` from stock.
3. Only then reboot the coordinator onto the same staged config (you are
   physically present; the worker is your lifeboat over the wire).
4. With BOTH boxes verified on the matched set, bring rail 0 back up
   (`nmcli connection up tb-fleet` both ends, worker first) and let the
   cores negotiate — this is the moment the matched-set rule protects.

One node finishing a rail-up two minutes before the other is a plausible
trigger for the wedge described below — do step 4 from two terminals in
short order.

**What a wedged port controller looks like, once both nodes are back:**
- `thunderbolt0` never appears, or appears with no carrier and no
  `10.99.0.x` address after a couple of minutes.
- `ls /sys/bus/thunderbolt/devices/` on either box shows no peer XDomain
  entry (no `N-1`..`N-9`-shaped name) -- the "PD-blind" signature: the boxes
  cannot even see each other's Thunderbolt identity, not merely failing to
  negotiate RDMA.
- The existing `tb-link-heal.timer` (already running on both nodes,
  2-minute cadence) will attempt its own escalation ladder automatically:
  ping check, then XDomain reconnect, then NHI unbind/rebind, and only as a
  last resort a physical PD reset. **That last resort is rate-limited to one
  shot per 1800s** via a stamp file
  (`/var/lib/tb-link-heal/pd-reset-stamp`). If it just fired, it will not
  fire again for up to half an hour -- do not sit and wait for the timer.

**Recovery, at the affected machine:**
```
sudo framework_tool --pd-reset 2
sleep 5
```
If that alone doesn't bring the link back, unbind/rebind the USB-C PD
controller the same way the heal script's own last resort does
(`ucsi_acpi`'s `USBC000:00`), then give `tb-link-heal.timer`'s next
2-minute tick a chance to finish the reconnect rather than manually forcing
every step. If neither box's Thunderbolt identity reappears after a manual
PD reset plus one full heal cycle, do a second coordinated reboot of both
boxes before troubleshooting further -- do not try to talk one box's stack
back to life while the other is still up; that is the live-reload case the
non-negotiable rule above forbids.

---

## 4. RoCE bring-up (both nodes, after `thunderbolt0` has a `10.99.0.x` address)

Confirm first:
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

## 5. Verify gate -- must pass on BOTH nodes before any benchmark

```
ls /sys/class/infiniband/                              # -> usb4_rdma0, and ONLY usb4_rdma0
rdma link                                               # port state ACTIVE, PHYS_STATE LinkUp
cat /sys/class/infiniband/usb4_rdma0/ports/1/gids/1     # non-zero (RoCEv2 IPv4 GID)
ibv_devices                                             # lists usb4_rdma0
```

If `gids/1` is all-zero, `thunderbolt0` doesn't have its `10.99.0.x` address
yet -- fix that first, don't chase the GID table. If `usb4_rdma0` never
appears, re-check vermagic on both nodes before anything else.

---

## 6. Single-rail contract -- confirm rail 1 is untouched

This is the rule the task record is most emphatic about: **never bring RDMA
up on both rails.** With two native rails simultaneously carrying RDMA, both
peers sit at route `0x2` in each other's domains and the source-blind
control handler on either box cross-matches the other's HELLOs and poisons
its HopID state -- a corruption bug, not a slowdown, and one more coordinated
reboot to clear.

Confirm rail 1 stays clean, on both nodes:
```
ls /sys/class/infiniband/                 # exactly ONE entry -- usb4_rdma0
readlink -f /sys/class/infiniband/usb4_rdma0/device      # resolve back to c4:00.5 (domain0), NOT c4:00.6 (domain1)
ip -4 addr show thunderbolt1              # link-local only, unchanged from before this checklist started
```

Do not run anything resembling a "second cable" prep step. There is no such
step in this package, deliberately -- see REPORT.md. `thunderbolt1` stays
NetworkManager-managed, plain socket transport, exactly as it was overnight.

---

## 7. RCCL env flip -- one file, byte-identical on both ranks

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
nothing.

---

## 8. Rollback -- TCP restored, modules gone only via reboot

1. Delete or stop sourcing the env delta from step 7 on both nodes. Confirm
   both ranks are back to the shared socket-transport env, byte-identical.
2. Revert the temporary deploy-rs config from step 2 (undo the blacklist and
   the boot-time insmod unit) and push the stock config to both nodes.
3. **Reboot both boxes together, again.** Do not `rmmod` the core live to
   "save a reboot" -- that is the exact live-unload case the non-negotiable
   rule forbids, and it wedges the same allocator a live *load* does.
4. After both boxes are back, re-run the step 0.3 C-state/MTU check before
   trusting any subsequent TCP measurement.
