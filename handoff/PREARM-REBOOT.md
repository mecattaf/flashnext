# Pre-arm reboot pass — outcome

Executed attended 2026-08-29 00:00–00:50 via mecattaf/dotfiles#241 (Claude
Fable session, operator present). Status: **worker COMPLETE on the patched
set; coordinator staged and awaiting its operator-supervised reboot** — the
one remaining act. Everything below survives that reboot; the session that
wrote this does not, so the post-reboot verification is spelled out at the
bottom for whoever runs it (operator or fresh session).

## What baked (dotfiles, pushed to main)

- `3fd70065` — #241 step 1: deploy-rs dials the worker at the fleet identity
  `10.99.9.2` (5GbE metric 20, TB metric 50 behind it), never the raw TB
  address. **Rehearsed with rail 0 admin-down**: full deploy+activate+confirm
  with zero Thunderbolt connectivity. Also unblocked three stale flake checks
  that had made HEAD undeployable (paperless/secrets restatement, the
  deliberately-red local-model-routing corpse → parked as dotfiles#242,
  retired-executor token ban false positive).
- `53fafa08` — tally pin-bump 5 (see tally addendum below).
- `1a2d7415` + `47ce02c5` + `7b7285f0` — `modules/fn-rdma.nix`, twins-only:
  patched thunderbolt core+net+ibverbs first-bound at boot. **Four** loaders
  had to be silenced, one discovered per reboot: (1) initrd
  `availableKernelModules` — stock core bound BEFORE the root switch; (2)
  stage-2 udev modalias — blacklisted; (3) `boot.kernelModules`
  "thunderbolt-net" pins in tb-fleet.nix / worker default.nix — gated, they
  ignore blacklists and pull the core as a dependency; (4) **the typec/UCSI
  stack** — depends on `thunderbolt`, dependency loads ignore blacklists, so
  the core unit runs `Before=systemd-udev-trigger.service` and typec then
  resolves against the already-resident patched core. The verbs provider is
  a separate later unit (`fn-rdma-ibverbs`) off the boot-critical path — an
  in-path 7.6s netdev wait tripped an unrelated unit's start-rate limit on
  reboot 2 (suid-sgid-wrappers; state cleared, cause removed).
- Escape hatch per host: `touch /etc/fn-rdma-disable` + one attended reboot
  → stock pair. Firewall: rail 0 only, `interfaces.thunderbolt0`, UDP 4791.
  Rail 1 carries nothing RDMA, ever.

## Module build (route (a), both twins)

Pins as specified: westeri `503c5ae1` + ibverbs series/module `76ba39b6`,
built against stock 7.1.4 via `linux-7.1.4-dev` from the dotfiles flake,
staged at `~/.local/state/flashnext-rdma/7.1.4/out` on each twin. Vermagic
`7.1.4 SMP preempt mod_unload` — identical across all three modules on both
nodes; series verified applied (callback_xd + source-aware handler strings).
Secure Boot confirmed **disabled on both** (the worker's open question is
answered). `host/rdma/fetch-and-build.sh` needed three NixOS fixes to run at
all (missing `mkdir` of its work dir; the kdir "farm" assumed an RPM merged
kernel tree — rewritten for the nixpkgs split `build`/`source` layout with a
redirect Makefile; dangling-symlink tolerance). Per the "don't touch the
tree" red line those live only in `/tmp/fetch-and-build.sh` on both twins —
diff (24 added lines) saved for upstreaming; fold into the repo in the
morning lane.

## Reboot results

Worker (3 supervised reboots, coordinator held as lifeboat throughout):
final boot has **zero failed units**, core+net inserted at ~6.87s in ~90ms
before coldplug, `fn-rdma-ibverbs` loaded at 13.7s, `lowlat-cluster` active,
C-state hold reads 0, NFS `/mnt/library` mounted, `thunderbolt0`
`10.99.0.2/30`, both rails UP with XDomain peers visible. Patched core
provable by size (598016 vs stock 606208) and by typec binding against it.

## ⚠ The mismatch window — the one finding that contradicts the plan docs

attended-bringup.md claims cross-host core/net version skew is safe for
plain IP. **Measured tonight: it is not.** Patched worker ↔ stock
coordinator = 100% packet loss on rail 0 in both directions, link layer UP
and XDomain peers visible on both domains — the 10-patch XDomain handshake
changes evidently don't peer with a stock net. Consequences:

- Until the coordinator reboots, coordinator↔worker traffic rides the 5GbE
  (`10.99.1.x` / fleet ids). Deploys are immune (step-1 repoint).
- Rail 0/rail 1 IP should re-establish once both ends run the matched set.
  **If it does not, that is the first blocker of the night** — rollback is
  the flag file + attended reboots, back to stock on both.
- No `/sys/class/infiniband` device exists during the window (provider
  loaded, nothing registered). Expected to appear on both nodes post-reboot.

## Ambient state the overseer must know

- **NCCL_IB_DISABLE=1 is mandatory in the pair env** — an ibverbs device
  will exist on both nodes after the coordinator reboot. Sockets are
  tonight's transport of record; the A/B flip is the morning lane, gated on
  a banked TCP benchmark.
- TB MTU is 1500 **by design** (lowlat-cluster `jumbo` off; the bringup
  doc's "expect 65520" is stale). Do not flip MTU tonight.
- The fleet-latency tripwire is in refractory until **Sat 10:51** (episode
  rtt-1787946666 — root-caused to the 21:51 deploy pushing the worker
  closure over rail 0, the exact defect step 1 fixed, plus build load).
  Rail-RTT alerting is MUTED overnight; `fn-preflight.sh`'s latency-hold
  check is the real gate before serve.
- Held-band RTT reference: 63–90 µs; C-state hold must read 0 on both.

## Tally addendum (campaign infrastructure)

- Deployed pin is now `tally.nix@6f1ce03`: **#619 fixed** — the first live
  escalation no longer crashes the pass (escalate brief accepts
  `taskCompletionRevisions`, thread-through). Estate preflighted per #615
  against a copy of the live coordinator estate before deploy; daemon
  restarted clean on the new pin ("tally daemon ready", quiescent answers).
- **Red line: no tally pin bumps or tally deploys overnight.** An armed
  campaign keeps its store path for life — a mid-campaign bump can only
  wedge the daemon (#371/#616 estate class), never help.
- **Red line: nothing mutates `~/mecattaf/tally.nix` while the campaign is
  live.** The worklist's spec-lint gate runs `cargo run -p spec-lint` from
  that working tree; it is parked clean at `6f1ce03` with the cargo cache
  warm.
- If escalation still misbehaves: `tally campaign steer` is the escape, as
  in the #619 field notes. W-316 (empty own-run job pages) is documented in
  the campaign-operator skill — corroborate before concluding nothing ran.

## Post-coordinator-reboot verification (run this, then close #241)

```bash
# on the coordinator, after reboot:
systemctl --failed                                  # want: 0 units
journalctl -b -u fn-rdma-modules -u fn-rdma-ibverbs -o cat | head
lsmod | grep -E '^thunderbolt|^ib_'                 # patched core (598016), net, ibverbs
systemctl is-active lowlat-cluster                  # #238: first on-disk boot test
sudo od -An -tu4 /dev/cpu_dma_latency               # want: 0
ping -c5 10.99.0.2 && ssh 10.99.9.2 ping -c5 10.99.0.1   # rail 0 IP restored, both ways
ls /sys/class/infiniband/                           # usb4_rdma0 — and ONLY that
ssh 10.99.9.2 ls /sys/class/infiniband/             # same on the worker
readlink -f /sys/class/infiniband/usb4_rdma0/device # c4:00.5 (domain0), NOT c4:00.6
systemctl --user status tally-daemon | head -4      # "tally daemon ready" on 6f1ce03 pin
# 60s soak, both rails; then tb-link-heal + tripwire timers armed:
ping -c60 -i1 -q 10.99.0.2 | tail -2 ; systemctl list-timers 'tb-*' 'tripwire-*' --no-pager
```

If `usb4_rdma0` never appears on either node with both rebooted: re-check
vermagic on both (`MANIFEST` files), then suspect the userspace-provider
commit question flagged in fetch-and-build.sh's `check_userspace` — it is a
morning-lane problem, NOT an arm blocker (sockets are the transport).
If rail-0 IP stays dead with both patched: flag-file rollback, both nodes,
worker first — and that IS an arm consideration (rails carry TP=2 sockets;
the pair env's NCCL_SOCKET_IFNAME lists both TB rails).

---

## Final outcome (2026-08-29 ~02:10)

Chapter record: `~/post-reboot-latest-2.md` (coordinator) — authoritative
for everything below.

- **Matched set live and verified on both twins.** Coordinator reboot #2
  (gen 162) delivered the patched set: `thunderbolt` 598016, all ib modules
  loaded, fn-rdma-modules + fn-rdma-ibverbs clean, 0 failed units on both
  nodes. **#241 CLOSED.**
- **Rail soaks clean.** Rail 0 loss-free both directions on repeated 60 s
  soaks; RTT avg 96–112 µs — above the 63–90 µs earlier reference band,
  flagged but not blocking (C-state hold 0 both twins, MTU 1500 by design;
  `fn-preflight.sh`'s latency-hold at serve time is the real gate).
- **Both RDMA devices present BY DESIGN.** `usb4_rdma0` (domain 0, c4:00.5)
  AND `usb4_rdma5` (domain 1, c4:00.6) on BOTH nodes, ACTIVE/LINK_UP,
  MANIFESTs md5-identical. The verify block above's "usb4_rdma0 — and ONLY
  that" line is superseded: fixed-stride naming from the source-aware build
  (see `host/rdma/attended-bringup.md` step 9 note). The boot-time `-12`
  probe error on the second advertised lane is permanent and cosmetic.
- **The coordinator reboot hang is root-caused**: amdgpu ISM/SSO `dc_lock`
  ABBA deadlock in the display shutdown path — `dm_suspend()` holds
  `dc_lock` while sync-flushing ISM/SSO delayed work that itself takes
  `dc_lock`. Known upstream bug, fixed in 7.1.6 and 7.2 (NOT 7.1.5).
  **dotfiles#244** tracks the post-campaign fleet migration; until then all
  reboots stay attended (coordinator WILL hang at shutdown; power-button
  recovery is the protocol).
- **Generation cleanup + GC done pre-arm, both twins** (red-line compliant:
  before arming, never after): coordinator kept 162+161, ~290 G free;
  worker kept 55+54, ~342 G free.

State at close: arm-ready. Remaining run order: ratify spec → arm →
overseer launch (operator-driven).
