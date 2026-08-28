# Observed-tree census — /home/tom/mecattaf/dotfiles

Session date 2026-08-28. Analysis-only; nothing in the repo or on either host was modified.
Every claim below carries a `file:line` I opened this session or a command + its literal output.
Grades: **[S]** read from source · **[M]** measured/counted · **[CL]** claimed by docs, unverified.

---

## 0. Repo state — committed vs staged

```
$ cd /home/tom/mecattaf/dotfiles && git status
On branch main
Your branch is up to date with 'origin/main'.

nothing to commit, working tree clean
```
**[M] The tree is CLEAN. Nothing is staged, nothing is dirty. Every file cited below is committed bytes at `7f1072e2b3c9b92e6baeff859d3e0ea3b954336b` (HEAD = origin/main).**

```
$ git log --oneline -15
7f1072e2 hf: the appliance gets the token, because the appliance is what downloads
b8b63f41 strix: the 8.5 GiB SDK leaves the closure — no nightly pays for a paused bring-up (#237)
d16cbd2f fleet: thunderbolt carries tensors only — admin takes the wire, and the library mount waits for reality (#240)
b37dea0b fleet: the rail was up and 9x slow — every packet paid a C3 exit, a held fd is the cure (#238)
86638988 hf: the declaration finally gets its ciphertext
962f14e8 nas: the push was never throttled — chunk count was the clock (#234)
f15c8e53 print: render-verify gate — nothing reaches CUPS mid-revision (#227)
58435c46 shell: bash takes the fleet execution substrate; fish becomes a kitty choice (#236)
c54521ef paper: the scanner's ghost leaves the comments — the plane itself left on cutover day
f1e271a7 nas: wan0-watchdog grows a chip_reset rung — the driver's own SER before the USB hammer
a088ce45 nas: wan0-watchdog — software power-cycle for the mt7925u wedge
2d2ee906 coordinator: uplink-failover-watchdog — the dead-gateway case #235 left open
62ad255a nas: flush the build from ExecStopPost — the script's last line never runs
326e9460 nas: /nix moves to the M.2 — the eMMC was never going to be big enough
7516ba9c flake-checks: five stale asserts squared up; one left standing on purpose
```

**[M] Exact file touch-sets of the three named commits** (`git show --stat`):

| commit | issue | files touched |
|---|---|---|
| `b37dea0b` | #238 | `modules/lowlat-cluster.nix` (+218, new), `modules/strix.nix` (+18) |
| `d16cbd2f` | #240 | `home/ssh.nix`, `hosts/coordinator/eth-fleet.nix`, `hosts/worker/default.nix`, `modules/local-models.nix` |
| `b8b63f41` | #237 | `modules/strix-ai.nix` only (+11 −2) |

**⚠ Load-bearing: NONE of the three commits touched `flake.nix`.** Last commit to touch the deploy hostname line is `85e144d4` (2026-08-21) — see §7.

**[M] Both hosts are live on the post-#238/#240 closure:**
```
coordinator $ ls -l /run/current-system
 -> /nix/store/dbbwfr61crfn14g2cvrwl7vnhvlbvlki-nixos-system-coordinator-26.11.20260723.e2587ca   (Aug 28 21:50)
worker      $ ls -l /run/current-system
 -> /nix/store/h6pw2nyw71lb5vsg5yaf87s1cxwylw8i-nixos-system-worker-26.11.20260723.e2587ca        (Aug 28 21:37)
```

---

## 1. `modules/lowlat-cluster.nix` — full option surface

**Landed exactly where expected: `/home/tom/mecattaf/dotfiles/modules/lowlat-cluster.nix`, 218 lines** (`wc -l`) **[M]**. Created whole by `b37dea0b`; never edited since.

### 1.1 Option surface (`options.myLowLatCluster`, lines 78–142) **[S]**

| option | line | type | default | notes |
|---|---|---|---|---|
| `enable` | 79 | `mkEnableOption` | **false** | "PM QoS + MTU tuning for the coordinator↔worker fleet rails" |
| `peer` | 81–84 | `str` | *(no default — required)* | "The peer's fast-rail /30 address, watched by the latency tripwire." |
| `latencyBudgetMicros` | 86–95 | `int` | **200** | trip point in µs; doc says it "sits well above the measured 58-90 us held figure and well below the 577 us unheld one" |
| `jumbo` | 97–112 | `bool` | **false** | "DEFAULT OFF, DELIBERATELY" — MTU is a both-ends contract |
| `jumboInterfaces` | 114–141 | `listOf (submodule { name : str; mtu : int; })` | `[{thunderbolt0,65520} {thunderbolt1,65520} {enp191s0,9000}]` | lines 127–139 |

There is no `enableTripwire`, no `sensorInterval`, no per-rail toggle. Five options total.

### 1.2 The PM QoS holder — it is SHELL, not python **[S]**

`modules/lowlat-cluster.nix:60-64`:
```nix
pmqosHold = pkgs.writeShellScript "pmqos-hold" ''
  exec 3> /dev/cpu_dma_latency
  printf '\0\0\0\0' >&3
  exec ${pkgs.coreutils}/bin/sleep infinity
'';
```
The fd-holding mechanism is documented at `modules/lowlat-cluster.nix:27-31`: "A bash `exec 3>` fd is not close-on-exec, so it survives the `exec sleep infinity` that replaces the shell … That is why there is no python3 in this closure, unlike the throwaway transient unit this file replaces."

Unit at `modules/lowlat-cluster.nix:146-157` **[S]**:
`systemd.services.lowlat-cluster` · `wantedBy = multi-user.target` · `after = network-pre.target` · `ExecStart = pmqosHold` · `Restart = "always"` · `RestartSec = "5s"`. No `Type=` (defaults to simple), no hardening stanza.

**[M] Verified live on the coordinator:**
```
$ systemctl is-active lowlat-cluster        → active
$ systemctl show -p MainPID --value lowlat-cluster → 2247685
$ sudo ls -l /proc/2247685/fd/
  l-wx------ 1 root root 64 Aug 28 21:51 3 -> /dev/cpu_dma_latency
$ sudo od -An -td4 /dev/cpu_dma_latency
             0
```
The held fd is fd 3 pointing at `/dev/cpu_dma_latency`, and the device reads 0. **[M] worker side: `systemctl is-active lowlat-cluster` → `active`.**

Unit path proves the committed source is what runs:
`/etc/systemd/system/lowlat-cluster.service -> /nix/store/p6xcr9wyjzs8v9xkvksp7yvy2r5iyc79-unit-lowlat-cluster.service`, `ExecStart=/nix/store/qlmkc81x6dlnsivs49baxwz4sdypzk7c-pmqos-hold` **[M]**.

### 1.3 The MTU stanza — implemented but OFF **[S]**

- Script `mtuScript`, `modules/lowlat-cluster.nix:66-75`: `PATH=…iproute2`, then per interface `if [ -e /sys/class/net/<i> ]; then ip link set dev <i> mtu <n> 2>/dev/null || true; fi`. Idempotent, silent about down rails.
- `systemd.services.fleet-mtu` = `lib.mkIf cfg.jumbo` — `modules/lowlat-cluster.nix:160-166`, `Type = "oneshot"`.
- `systemd.timers.fleet-mtu` = `lib.mkIf cfg.jumbo` — `modules/lowlat-cluster.nix:167-174`, `OnBootSec = "1min"`, `OnUnitActiveSec = "2min"`, `AccuracySec = "30s"` (timer, not oneshot-at-boot, because "TB interfaces come and go with the link").

**What turns it on: `myLowLatCluster.jumbo = true`, and nothing sets it.** `modules/strix.nix:153-160` sets only `enable` and `peer`; lines 157–159 are a comment reading "jumbo stays off: see the two-step deploy note in the module." The module's own doc (`:101-111`) prescribes the flip as a TWO-STEP deploy — switch both boxes, then `ping -M do -s 60000 <peer>` from each end.

**[M] Confirmed OFF at runtime:**
```
coordinator $ systemctl list-units --all 'fleet-mtu*'   → 0 loaded units listed.
coordinator $ for i in thunderbolt0 thunderbolt1 enp191s0; do cat /sys/class/net/$i/mtu; done
1500 / 1500 / 1500
worker      $ thunderbolt0 mtu=1500   enp191s0 mtu=1500
```
No 65520 anywhere, on either box.

### 1.4 The fleet-latency tripwire **[S]** — `modules/lowlat-cluster.nix:183-215`

Declared through the house `myTripwire` submodule (`modules/tripwire.nix`, options at `:39-140`).

| field | value | line |
|---|---|---|
| `description` | "the fast rail's average RTT stays inside the PM QoS budget" | 184 |
| `intervalSeconds` | **900** (poll every 15 min) | 185 |
| `onBootSec` | `"10min"` | 186 |
| `threshold` | `cfg.latencyBudgetMicros` → **200 µs** | 187 |
| `comparison` | `"ge"` | 188 |
| `sustainSeconds` | **3600** (must hold ~1 h before firing) | 189 |
| `rearm` | `0` | 190 |
| `refractorySeconds` | **43200** (12 h quiet window) | 191 |
| `valueField` | `RTT_US` | 192 |
| `sensorPath` | `iputils`, `gawk`, `coreutils` | 193–197 |

Sensor, `:201-208`: `ping -q -i 0.01 -c 50 -W 2 <peer>`, tail -1, `awk -F'[/ ]' '{ printf "%d rtt 1\n", $8 * 1000 }'` (avg field × 1000 → µs). A dark rail deliberately reports **0**, not a huge number — `:198-200`: "darkness is tb-fleet-reachability's job and double-firing on one cable would only bury the signal that matters here."

**Alert path**, `:209-215`: writes a failure marker file — `mkdir -p /var/lib/failure-markers` then `printf … > /var/lib/failure-markers/fleet-latency`, with the recovery text naming `systemctl is-active lowlat-cluster` and `sudo od -An -td4 /dev/cpu_dma_latency` on BOTH boxes. Marker surfacing is the fleet-wide `modules/failure-surfacing.nix` plane (drop-in `10-fleet-onfailure.conf` → `OnFailure=failure-notify@%N.service`, observed on the live unit **[M]**).

**[M] Live on the coordinator:**
```
$ systemctl list-timers --all | grep fleet-latency
Fri 2026-08-28 22:21:09 CEST 11min  Fri 2026-08-28 22:06:09 CEST 3min 12s ago  tripwire-fleet-latency.timer → tripwire-fleet-latency.service
$ systemctl list-units --all | grep tripwire-fleet-latency
  tripwire-fleet-latency.service   loaded inactive dead   the fast rail's average RTT stays inside the PM QoS budget
```
15-minute cadence confirmed empirically.

### 1.5 Who imports it, and how the gate works **[S]**

`grep -rn "lowlat" --include=*.nix .` returns exactly one import site:
- `modules/strix.nix:35` — `./lowlat-cluster.nix`

and exactly one enable site:
- `modules/strix.nix:153-160`:
  ```nix
  myLowLatCluster = {
    enable = true;
    peer = if config.networking.hostName == "coordinator" then "10.99.0.2" else "10.99.0.1";
  };
  ```

`modules/strix.nix` is imported by exactly two hosts:
- `hosts/coordinator/default.nix:60` — `../../modules/strix.nix`
- `hosts/worker/default.nix:65` — `../../modules/strix.nix`

**The gate is twofold, exactly as the module header states (`modules/lowlat-cluster.nix:33-36`): (a) the option defaults OFF, (b) the sole importer is `modules/strix.nix`, which only coordinator and worker import. The zenbook/NAS cannot see the option at all.** The peer selection is a `networking.hostName` conditional, not a per-host `enable` — so the both-ends invariant is structural, per `modules/strix.nix:147-152`.

---

## 2. Deploy / SSH addressing — as committed

### 2.1 The three-layer addressing doctrine **[S]** — `hosts/coordinator/eth-fleet.nix:12-21`

Verbatim from committed bytes:
```
# Addressing doctrine, three layers:
#   - 10.99.0.0/30  tb-fleet   (thunderbolt0) — the tensor rail, unchanged.
#   - 10.99.1.0/30  eth-fleet  (enp191s0)     — this file, always-on.
#   - 10.99.9.x/32  fleet IPs  (lo)           — the STABLE identity.
```
So the third layer is `10.99.9.x/32` on **loopback**, and the ethernet rail is `10.99.1.x/30` — the campaign's guessed shape is correct.

Assignments **[S]**:
- coordinator: `10.99.0.1/30` tb (`modules/lowlat-cluster.nix:40`), `10.99.1.1/30` eth (`hosts/coordinator/eth-fleet.nix:67`), `10.99.9.1/32` lo (`hosts/coordinator/eth-fleet.nix:51`)
- worker: `10.99.0.2/30` tb, `10.99.1.2/30` eth (`hosts/worker/default.nix:312`), `10.99.9.2/32` lo (`hosts/worker/default.nix:299`)

Fleet identity is a dedicated oneshot, not `networking.localCommands` — `hosts/coordinator/eth-fleet.nix:42-53` and `hosts/worker/default.nix:292-301`, both `Type=oneshot`, `RemainAfterExit=true`, `ExecStart = ip addr replace 10.99.9.x/32 dev lo`, `after = network-pre.target`. Rationale at `eth-fleet.nix:42-43`: "networking.localCommands is masked under NetworkManager (found the hard way on deploy night)."

### 2.2 Route metrics — the #240 flip **[S]**

- **eth-fleet route: metric 20**, declared. `hosts/coordinator/eth-fleet.nix:75` → `route1 = "10.99.9.2/32,10.99.1.2,20";` and `hosts/worker/default.nix:317` → `route1 = "10.99.9.1/32,10.99.1.1,20";`
  Keyfile syntax note at `eth-fleet.nix:72-74`: "(routeN=dest,next-hop,metric) — a nmcli-style `routes` key is silently ignored by the keyfile parser."
- **Thunderbolt route to the fleet IP: metric 50, IMPERATIVE, not in the tree.** `hosts/coordinator/eth-fleet.nix:17-18`: "via TB at metric 50 (on the tb-fleet profile, added imperatively like the profile itself)". `:28-29`: "The imperative TB route stays at 50, untouched — only this profile's declared metric moved below it."
- The flip is dated in-file: `eth-fleet.nix:23-27` — "METRICS FLIPPED 2026-08-28 (dotfiles#240 ruling): ethernet used to sit at 200 behind TB's 50".

**[M] Live routing tables confirm it:**
```
coordinator $ ip route show | grep 10.99
10.99.0.0/30 dev thunderbolt0 proto kernel scope link src 10.99.0.1 metric 101
10.99.1.0/30 dev enp191s0     proto kernel scope link src 10.99.1.1 metric 103
10.99.9.2 via 10.99.1.2 dev enp191s0     proto static metric 20
10.99.9.2 via 10.99.0.2 dev thunderbolt0 proto static metric 50

worker $ ip route show | grep 10.99
10.99.0.0/30 dev thunderbolt0 proto kernel scope link src 10.99.0.2 metric 100
10.99.1.0/30 dev enp191s0     proto kernel scope link src 10.99.1.2 metric 102
10.99.9.1 via 10.99.1.1 dev enp191s0     proto static metric 20
10.99.9.1 via 10.99.0.1 dev thunderbolt0 proto static metric 50
```
`[M]` **Incidental**: the worker also carries a stale imperative `10.77.0.0/30 via 10.99.0.1 dev thunderbolt0 proto static metric 100` — the retired legacy /30 to the NAS. It exists in no committed file (`grep -rn "10.77" --include=*.nix .` hits only `modules/mesh-registry.nix:70` as an alias). Purely imperative residue.

### 2.3 The SSH nickname — `home/ssh.nix` **[S]**

`home/ssh.nix` is the "fourth SSH layer" — operator nickname → target (`:6-13`). 124 lines.

- `operatorAliases` at `:42-50`: `coordinator→coordinator`, `zenbook→zenbook-duo`, `nas→nas`, `worker→worker`.
- **`workerRail` at `home/ssh.nix:97`:**
  ```nix
  workerRail = if hostName == "coordinator" then "10.99.9.2" else "worker";
  ```
- `mkBlock` at `:99-112`: `HostName = if target == "worker" then workerRail else target;` plus `User = "tom"`, `IdentityFile = "~/.ssh/id_ed25519"`, `IdentitiesOnly = true`, `StrictHostKeyChecking = "yes"`, and `ProxyJump = "coordinator"` only for `nas` from a non-coordinator/non-nas host (`needsJump`, `:60`).
- Emitted via the new HM API: `programs.ssh.settings = lib.mapAttrs mkBlock operatorAliases;` (`:119-123`), `enableDefaultConfig = false` — API deprecation rationale at `:28-34`.
- Guard at `:114-117`: asserts every alias target exists in `mesh-registry.nix`.

**The measurement that justified the flip is committed in the comment** (`home/ssh.nix:71-76`), 200 samples each, PM QoS held:
```
#   TB   10.99.0.2 : rtt min/avg/max = 33/58/122 us, mdev 18 us
#   eth  10.99.1.2 : rtt min/avg/max = 58/72/142 us, mdev  9 us
```
Ruling text at `:62-66`: "Thunderbolt (10.99.0.x) is reserved for LLM-parallelism / tensor traffic ONLY; admin traffic … prefers the dedicated 5GbE cable (eth-fleet) and rides the stable fleet identity."

**Coordinator-only preference** (`:91-96`): from zenbook or NAS the nickname resolves to `worker` (the LAN identity 10.42.0.5).

### 2.4 Mesh/fleet registry **[S]** — `modules/mesh-registry.nix` (84 lines)

`worker` row at `:52-63`:
```nix
aliases = [ "worker" "10.42.0.5" "10.99.0.2" "10.99.1.2" "10.99.9.2" ];
hostKey = "ssh-ed25519 AAAA…CzwLO root@worker";
userKey = "ssh-ed25519 AAAA…VX8Z tom@mesh-20260729";
```
`coordinator` at `:18-27`: aliases `[ "coordinator" "10.99.1.1" "10.99.9.1" ]` — **note: the coordinator row does NOT list `10.99.0.1` or `10.42.0.2`.** All five worker addresses carry the same pinned host key, so no TOFU on any rail. `modules/mesh.nix:25-28` turns the registry into `programs.ssh.knownHosts`; `:30-36` authorizes the user keys for `tom` and `root`.

### 2.5 The deploy target — **still Thunderbolt** ⚠

`flake.nix:503-542` is the deploy-rs block. Global opts at `:504-512`: `sshUser/user = "root"`, `sshOpts = fleetDeploySshOpts` (defined `:299-334`, includes `-F /dev/null`, `StrictHostKeyChecking=yes`, `UserKnownHostsFile=/etc/ssh/ssh_known_hosts`, `AddressFamily=inet`, `-i /run/agenix/ssh-user-key`), `autoRollback`/`magicRollback` true, `remoteBuild = false`, `activationTimeout = 1200`.

**`flake.nix:536`:**
```nix
hostname = if host == "worker" then "10.99.0.2" else host;
```
See §7 — this is the single largest divergence from the campaign record.

---

## 3. Model catalog

### 3.1 Catalog row schema — `lib/local-models.nix` (2008 lines) **[S]**

Two typed attrsets, evaluated through `lib.evalModules` at `:367-379`: `artifacts : attrsOf artifactType` and `deployments : attrsOf deploymentType`.

**`artifactType`** (`:110-187`):
| field | line | type |
|---|---|---|
| `kind` | 112–121 | enum `model / mtp-head / draft / mmproj / tokenizer / template` |
| `maker` | 123 | str |
| `baseCheckpoint`, `fineTune` | 127, 131 | nullOr `{ url; revision; }` (`checkpointType`, `:76-87`) |
| `source.layout` | 136–147 | enum `flat / snapshot` (default `flat`) |
| `source.localName` | 148–156 | nullOr str — upstream-compatible dir name under `/etc/local-models/snapshots` |
| `source.hfUrl` | 157 | str |
| `source.revision` | 161 | str, pinned HF commit |
| `source.primary` | 165 | str |
| `source.files` | 169 | `nonEmptyListOf fileType` |
| `notes` | 174 | str |
| `quantization` | 178–185 | nullOr str |

**`fileType`** (`:89-108`): `path` (HF-repo-relative), `bytes` (ints.unsigned, EXACT), `oid` (content SHA-256 = the git-lfs OID), `hash` (Nix SRI).

**`deploymentType`** (`:262-365`): `model` (public ID), `role` (enum, 8 values), `status` (enum canonical/candidate/experimental/negative/retired), `archived` (nullOr str — retirement receipt), `backend` (enum from `lib/local-model-backends.nix`), `hosts` (`nonEmptyListOf (enum ["coordinator" "worker"])`), `ramTierGb`, `ttl` (default 600), `artifacts` (`artifactRefsType` — `model/mtpHead/draft/mmproj/tokenizer/template`, `:189-218`), `runtime` (`{repository; commit; args;}`, `:220-239`), `benchmark` (nullable, `:241-260`), `evidence` (enum matched-local/upstream-measured/api-only/unverified), `hardware`, `supersedes`, `supersededBy`, `notes`.

`lib/local-model-backends.nix` (10 lines) **[S]**:
```nix
{ local = [ "rocm" "vulkan" "ds4" "vllm" "mlx" ]; appliances = [ "npu" ]; }
```

**[M] Actual backend usage across the whole catalog** (`grep -o 'backend = "[a-z]*"' lib/local-models.nix | sort | uniq -c`):
```
  4 backend = "npu"
  7 backend = "rocm"
 11 backend = "vulkan"
```
**Zero deployments use `ds4`, `vllm`, or `mlx` today** — consistent with `modules/strix-ai.nix:55-64`.

Worked example, `lib/local-models.nix:1543-1581` (`qwen38-27b-mtp-q8-0`): `model = "qwen3.8-27b"`, `backend = "vulkan"`, `hosts = ["coordinator"]`, `artifacts = { model = "qwen38-27b-q8-0"; mtpHead = "qwen38-27b-mtp-q4-0"; }`, `runtime = llamaCppRuntime (commonLlamaArgs ++ [...])` with `@mtpHead@` token expansion.

### 3.2 The runtime-download assertion **[S]** — `modules/local-models.nix:276-281`
```nix
{
  assertion = lib.all (
    deployment: lib.all (arg: !(lib.hasInfix "-hf" arg)) deployment.runtime.args
  ) deploymentList;
  message = "Runtime model downloads (-hf) are forbidden; weights arrive only via the NAS Library flow (catalog row -> library-fetch -> local-models-sync).";
}
```
It is a substring ban on `-hf` in **every** deployment's `runtime.args` (all rows, not just allowed ones). Reinforced flake-side at `flake.nix:1799`: `assert !(nixpkgs.lib.hasInfix "-hf" (builtins.toJSON coordinatorSettings));`

Twenty-two catalog assertions live in `modules/local-models.nix:202-325` (list `catalogAssertions`), surfaced both as NixOS `assertions` (`:365`) and as an eval-time `throw` gate via `catalogValid` (`:326-328`, forced at `:376`).

### 3.3 Weights flow, and how the FP8 checkpoint would be declared

**Doctrine (2026-08-21 "weights leave nix" ruling)** — `lib/model-store.nix:5-25` **[S]**: nix computes only DATA (runtime paths + per-file facts); "Nix evaluates descriptions; systemd moves bytes." `runtimeRoot = "/var/lib/local-models"` (`:27`). `materializeArtifact` (`:34-45`) yields `directory`, `primary`, and `files[] = {name, path, bytes, oid, url}` where `url = "${hfUrl}/resolve/${revision}/${file.path}"`.

Three-hop pipeline **[S]**:
1. **HF → NAS Library.** `hosts/nas/models.nix:91-151` `library-fetch` (`writeShellApplication`), driven by `libraryManifest` = `modelStore.manifestFor (builtins.attrNames catalog.artifacts)` (`:87-89`) — **the Library wants EVERY catalog artifact** (`:84-86`). Auth via `/run/agenix/huggingface-token` in a 0600 header file (`:113-124`, added 2026-08-28 by `7f1072e2`). Verify-then-atomic-rename (`:138-147`). Unit `:203-217` (`Type=oneshot`, `TimeoutStartSec=12h`, `Nice=10`, `IOSchedulingClass=idle`), timer `:218-225` `OnCalendar=02:30`, `Persistent=false`.
2. **Library → node.** `modules/local-models.nix:60-128` `local-models-sync`, reading `/etc/local-models/wanted.json` (generated `:56-58` from `hostArtifactIds`). Size-check → skip; else copy from `cfg.libraryPath`, `sha256sum` against `oid`, `mv` atomically (`:78-101`); prune only after a fully clean pass (`:106-125`). Unit `:392-412`: `restartTriggers = [wantedManifest]`, `unitConfig.RequiresMountsFor = [cfg.libraryPath]`, `TimeoutStartSec = "2h"`.
3. **llama-swap ordering.** `modules/local-models.nix:417-420`: `wants`/`after` `local-models-sync.service` — deliberately `wants`, not `requires` (`:414-416`).

`libraryPath` (`modules/local-models.nix:352-361`): default `"/mnt/nas/models/weights"`; worker overrides to `"/mnt/library/weights"` at `hosts/worker/default.nix:213`.

**To declare the FP8 checkpoint you would add, to `lib/local-models.nix`'s `artifacts` block:**
- a `layout = "snapshot"` artifact (the flat/basename collapse at `lib/model-store.nix:31-32` is wrong for a 131-shard HF tree), with `localName` if a loader must see the upstream repo basename, `hfUrl`, pinned `revision`, `primary = "config.json"`, and one `fileType` entry per file with exact `bytes` + `oid` + `hash`. The closest committed template is `deepseek-v4-flash-0731-bf16` at `lib/local-models.nix:771-…` (`layout = "snapshot"`, `localName = "DeepSeek-V4-Flash-0731"`, `primary = "config.json"`).
- Then a `deployments` row (backend `vllm`) and/or a `services.local-models.artifacts` entry in `modules/strix.nix`, at which point `wanted.json` changes → `local-models-sync` re-runs → the bytes land at `/var/lib/local-models/<artifactId>/…`.
- Relevant assertions the row must satisfy: primary must be one of the files (`modules/local-models.nix:223-228`); paths must be safe relative paths (`:229-234`); no repeated paths (`:235-244`); snapshot layout exempts the unique-basename rule (`:245-255`).

**[S] `/mnt/nas/models/weights/qwen38-flash-next-fp8/` EXISTS ON DISK AND IS NOT IN THE CATALOG.**
```
$ grep -rn "flash-next\|qwen38-flash\|fp8\|FP8" --include=* .        (in dotfiles)   → NO MATCHES
$ ls -la /mnt/nas/models/weights/qwen38-flash-next-fp8/ | head
drwxr-xr-x 1 tom users 2564 Aug 28 22:06 .
-rw-r--r-- 1 tom users   8952 chat_template.jinja
-rw-r--r-- 1 tom users  72423 config.json
-rw-r--r-- 1 tom users    202 generation_config.json
-rw-r--r-- 1 tom users   3235 LICENSE
-rw-r--r-- 1 tom users 3353259 merges.txt
-rw-r--r-- 1 tom users 1040155912 model-00001-of-00131.safetensors
…
$ ls /mnt/nas/models/weights/qwen38-flash-next-fp8/ | grep -c safetensors   → 38
$ du -sh /mnt/nas/models/weights/qwen38-flash-next-fp8/                     → 57G
```
**[M] 38 of 131 shards present; 57 G on disk; mtimes 21:25–22:06 today; owner `tom:users` (library-fetch chowns to `tom:users`, `hosts/nas/models.nix:146` — but this dir was created BEFORE any catalog row exists, so it was NOT placed by library-fetch).** The download is in flight or partial. `config.json` reads `"architectures": ["Qwen4ExpForConditionalGeneration"]`, `"model_type": "qwen4_exp"`, with `layer_types` alternating `linear_attention` ×3 / `full_attention`, `full_attention_interval: 4`, `hidden_size: 2560`, `head_dim: 256`, `indexer_budget: 2048` **[S]**.

### 3.4 `modules/llama-swap.nix` — roster shape and the "default model" question **[S]**

125 lines. Owns only "the proxy package, lifecycle, state, and network boundary" (`:9-11`); `local-models.nix` owns the roster.

`services.llama-swap` settings (`:30-68`): `enable = true`, `package = pkgs.llama-swap` (pkgs/llama-swap.nix pins asset `llama-swap_240_linux_amd64.tar.gz`), `listenAddress = "0.0.0.0"`, `port = 9292`, `openFirewall = false`, `healthCheckTimeout = 900`, `store.path = /var/lib/llama-swap/activity.sqlite`, `captureBuffer = 0`, `performance.disabled = true`, `startPort = 10001`, `sendLoadingState = true`, **`globalTTL = 0`**, `unloadTimeout = 60`.

Roster shape is injected by `modules/local-models.nix:375-381`:
```nix
services.llama-swap.settings = assert catalogValid; {
  models = localModels;
  peers = { };
};
```
`localModels = lib.mapAttrs' renderModel selectedDeployments` (`:177`), keyed by `deployment.model` (`renderModel`, `:152-175`), each value `= renderer{…} // { name; cmd; ttl; }`. Renderers live in `lib/local-model-runtime.nix` (58 lines) — five: `rocm`, `vulkan`, `ds4`, `vllm`, `mlx`.

**On "current default model": there is NO default-model key in this module or in llama-swap's settings.** `globalTTL = 0` is the only global, and it is a residency fallback, not a model selection (`:61-66`). Selection is per-request by model ID. **UNDETERMINED as posed** — what I looked at: all 125 lines of `modules/llama-swap.nix`, the settings injection at `modules/local-models.nix:375-381`, and `grep -rn "defaultModel\|default model\|LLM_MODEL\|OPENAI_MODEL"` over `modules/` and `home/` (only hit: `modules/printing.nix:51` `model = "everywhere"`, unrelated). What would settle it: naming which consumer's default you mean — e.g. `pkgs/academic-ocr/driver.sh:4` defaults its endpoint to `http://localhost:9292`, and `flake.nix:1355` asserts `monthlySources.inference.url == "http://coordinator:9292"`.

**[M] The live roster on the coordinator** (`curl -fsS http://localhost:9292/v1/models`) returns, in order: `fara1.5-27b`, `fara1.5-9b`, `gemma4-26b-a4b-it`, `ornith-1.5-35b`, `qwen3-embedding-8b`, `qwen3-vl-8b-ocr`, … all `"status": {"value": "unloaded"}` at query time. This matches the asserted list at `flake.nix:1747-1759`: `fara1.5-27b, fara1.5-9b, gemma4-26b-a4b-it, ornith-1.5-35b, qwen3-embedding-8b, qwen3-vl-8b-ocr, qwen3-vl-embedding-8b, qwen3.6-27b, qwen3.6-35b-a3b, qwen3.8-27b` (10 rows).

Network boundary (`modules/llama-swap.nix:82-91`): two interface-scoped firewall doors only — `tailscale0` and `wlp192s0`. **Neither `thunderbolt0` nor `enp191s0` admits :9292.**

Service hardening (`:93-124`): `StateDirectory`/`CacheDirectory = llama-swap`, `WorkingDirectory = lib.mkForce /var/lib/llama-swap`, `UMask = 0077`, `LimitMEMLOCK = infinity`, `TimeoutStopSec = 2min`, env `LLAMA_MEDIA_MARKER = "<__media__>"`, `XDG_CACHE_HOME = /var/cache/llama-swap`. Upstream module uses `DynamicUser` + `ProtectSystem=strict` (`:109-110`).

---

## 4. Kernel / aperture settings per host

**[M] Exhaustive greps over the whole tree:**
```
$ grep -rn "gttsize\|gtt_size\|pages_limit\|amd_iommu\|iommu" --include=*.nix .
modules/strix.nix:164:    # (hardware.amd-npu additionally pins iommu.passthrough=0).
modules/strix.nix:166:      "amd_iommu=on"
modules/strix.nix:167:    "ttm.pages_limit=33554432"
flake.nix:1765:   assert nixpkgs.lib.elem "amd_iommu=on" coordinator.boot.kernelParams;

$ grep -rn "kernelPackages\|linuxPackages\|boot.kernelParams" --include=*.nix .
modules/strix.nix:165           boot.kernelParams = [
hosts/nas/router.nix:78         boot.kernelParams = [ "usbcore.autosuspend=-1" ];
modules/common.nix:45           boot.kernelPackages = lib.mkDefault pkgs.linuxPackages_latest;
hosts/zenbook-duo/default.nix:69  boot.kernelParams = [ "i915.enable_psr=0" ];
hosts/zenbook-duo/hardware.nix:27 (comment only)
```

### 4.1 `amdgpu.gttsize` — **ABSENT. It is set nowhere.** **[M]**

Not in dotfiles (grep above). Not in the pinned `nixos-hardware` either:
```
$ nix hash path --sri --type sha256 /nix/store/g82m9l2b7rnff5zgyk3kx8lg53r22p1y-source
sha256-1CfD8ZUjCkTgjsneLZ/lxCHhgDfqxxE7/GX0MmsgiqA=
   == flake.lock nixos-hardware narHash (rev a017f5b72210026af5b3ac5949f08d94380a6fbd)   ✓ exact match
$ grep -rn "gttsize\|gtt_size\|ttm.pages_limit" <that path>/      → NO MATCHES
```
The imported module `framework/desktop/amd-ai-max-300-series/default.nix` is 19 lines: imports `common/cpu/amd`, `common/cpu/amd/pstate.nix`, `common/gpu/amd`, `common/pc/ssd`, `framework-tool.nix`, and sets `boot.kernelPackages = lib.mkIf (versionOlder pkgs.linux.version "6.14") (mkDefault linuxPackages_latest)` **[S]**.

**The GTT ceiling is expressed entirely as `ttm.pages_limit`, not `amdgpu.gttsize`.** 33554432 pages × 4 KiB = **128 GiB**, matching `modules/strix-ai.nix:81-82` ("Importing that whole module would regress our 128 GiB GTT ceiling to upstream's 80 GiB default"). **Verified against upstream** — `nix-strix-halo`'s `modules/tuning.nix:6` sets `"ttm.pages_limit=20971520"` (= 80 GiB), and the dotfiles deliberately do NOT import that module (only `hardware.firmware = [ strixAi.strix-halo-mes-firmware ]` is adopted, `modules/strix-ai.nix:83`) **[S]**.

### 4.2 Shared kernel params (both Strix hosts) — `modules/strix.nix:162-168` **[S]**
```nix
boot.kernelParams = [
  "amd_iommu=on"
  "ttm.pages_limit=33554432"
];
```
Rationale `:162-164`: "amdxdna binds through IOMMU SVA/PASID and needs translated mode (hardware.amd-npu additionally pins iommu.passthrough=0)."

Also shared, same file: `boot.extraModprobeConfig = "options mt7925e disable_aspm=1"` (`:181`); `systemd.settings.Manager.RuntimeWatchdogSec = RebootWatchdogSec = "2m"` (`:196-199`); `nix.settings = { max-jobs = 4; cores = 8; }` (`:142-145`).

### 4.3 Kernel version pin **[S]/[M]**

- `modules/common.nix:45`: `boot.kernelPackages = lib.mkDefault pkgs.linuxPackages_latest;` — fleet-wide, no explicit version.
- The *effective* pin is the `nixpkgs` input: `flake.nix:6` `nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable"`, deliberately lagging per `flake.nix:10-12` — "That pin is deliberately lagging — the exact-candidate fleet deploy keeps it as the only door kernel/Mesa churn enters through, bumped manually."
- Neither host overrides `boot.kernelPackages`. `hosts/coordinator/*` and `hosts/worker/*` contain no `kernelParams` or `kernelPackages` of their own (grep above) — **all kernel/aperture policy is in `modules/strix.nix`, none per-host.**

**[M] Live, both boxes:**
```
coordinator $ uname -r → 7.1.4
coordinator $ cat /proc/cmdline
… init=/nix/store/8sgzd7wg…-nixos-system-coordinator-26.11.20260723.e2587ca/init
  amd_pstate=active amd_iommu=on ttm.pages_limit=33554432 root=fstab splash loglevel=4 lsm=landlock,yama,bpf
coordinator $ cat /sys/module/ttm/parameters/pages_limit → 33554432

worker $ uname -r → 7.1.4
worker $ cat /proc/cmdline
… nixos-system-worker-26.11.20260723.e2587ca/init
  amd_pstate=active amd_iommu=on ttm.pages_limit=33554432 root=fstab splash loglevel=4
  video=DP-1:e drm.edid_firmware=DP-1:edid/1920x1080.bin lsm=landlock,yama,bpf
```
Identical aperture on both; the worker adds only its headless-display params (`hosts/worker/headless-display.nix`). **`amd_pstate=active` comes from nixos-hardware's `common/cpu/amd/pstate.nix`, not from dotfiles.** **⚠ [M] `iommu.passthrough=0` is NOT on either live cmdline**, despite the claim at `modules/strix.nix:164` that `hardware.amd-npu` pins it — see §7.4.

`hosts/coordinator/*` also carries: `hardware.nix` (820 B), `disko.nix`, and per-service files; `hosts/worker/*`: `hardware.nix` (1054 B — its `:18` comment notes thunderbolt0 is the fallback rail), `disko.nix`, `headless-display.nix`, `immich-ml.nix`, `journal-upload.nix`, `default.nix` (388 lines).

---

## 5. What a new vLLM TP=2 pair module would plug into

### 5.1 Existing systemd patterns for model serving **[S]**

There is exactly **one** long-running model-serving unit pattern: **`services.llama-swap`** (upstream NixOS module, configured by `modules/llama-swap.nix`), plus its `systemd.services.llama-swap` overlay at `:93-124` (DynamicUser, ProtectSystem=strict, State/Cache dirs, `LimitMEMLOCK=infinity`).

**There is NO existing "pair-check" oneshot.** `grep -rni "pair-check\|pair_check\|tp=2\|tensor-parallel\|tensor_parallel" --include=*.nix --include=*.md .` returns only prose: `docs/local-ai/ds4-vllm-recon-2026-08-21.md:14`, `modules/lowlat-cluster.nix:52`, `modules/strix-ai.nix:60`, `lib/local-models.nix:774` **[M]**.

The reusable **oneshot idioms already in the tree** that a pair service would compose with **[S]**:
- `local-models-sync` — `Type=oneshot`, `RequiresMountsFor`, `restartTriggers`, `TimeoutStartSec=2h` (`modules/local-models.nix:392-412`)
- `library-reachable` — the #240 "wait for reality, not for a target's word" gate, `Type=oneshot`, `RemainAfterExit=true`, `TimeoutStartSec=3min`, body is a `bash -c 'exec 3<>/dev/tcp/nas/2049'` poll loop over 120 s (`hosts/worker/default.nix:189-212`). **This is the pattern a TP=2 rank-1 gate should copy.**
- `fleet-identity` — `Type=oneshot`, `RemainAfterExit=true`, `after=network-pre.target` (`hosts/coordinator/eth-fleet.nix:44-53`)
- `tb-link-heal` — `Type=oneshot` + timer + `StateDirectory` (§6)
- `myTripwire` — declarative sensor/threshold/sustain/act, `modules/tripwire.nix:39-140`; the recon doc already names a tripwire candidate (`docs/local-ai/ds4-vllm-recon-2026-08-21.md:84-86`: journal-grep `tbv_ar2: rank[0-9] ready`, absence after bring-up = silently running on TCP).

### 5.2 Is there a flake input for nix-strix-halo? **YES** **[S]/[M]**

`flake.nix:244` — `nix-strix-halo.url = "github:hellas-ai/nix-strix-halo";` (deliberately **no** `inputs.nixpkgs.follows`, rationale `:234-243`). Also in `rollingInputOverrides` at `flake.nix:292-295`.

`flake.lock` locked node **[M]**: `owner hellas-ai / repo nix-strix-halo / rev f0f2048ff842749b363ea3562a98fb0a04bb2e61 / narHash sha256-2HKCQel/UbZkxV6/IiLBhbXV32x8ImRvRmrBM0MOoAs= / lastModified 1787040186`.

I located and **cryptographically confirmed** the realized source:
```
$ nix hash path --sri --type sha256 /nix/store/9abiq2k7f7rkkmr1b96r7lrflg9w1jnz-source
sha256-2HKCQel/UbZkxV6/IiLBhbXV32x8ImRvRmrBM0MOoAs=      ✓ exact match to flake.lock
```

**What that locked input already carries — this is the single most useful finding for a TP=2 module [S]:**

- **Transitive locked inputs** (from `flake.lock`, `nodes["nix-strix-halo"].inputs`):
  - `vllm-src` → `vllm-project/vllm` rev `752a3a504485790a2e8491cacbb35c137339ad34`; upstream declares `url = "github:vllm-project/vllm/v0.25.1"` (its `flake.nix:47-48`), version string `"0.25.1"` at `:369`
  - `thunderbolt-ibverbs` → `hellas-ai/thunderbolt-ibverbs` rev `76ba39b630a70accb72f19388eefe48844b50eb8` — **the exact rev the recon doc names** (`docs/local-ai/ds4-vllm-recon-2026-08-21.md:26`)
  - `linux-src` → `git.kernel.org/…/westeri/thunderbolt.git`, ref `refs/heads/next`, rev `503c5ae1e72aa9ed91925dafa3d82ee2e992747f` — **the exact patched-core rev the recon doc names** (`:26`)
  - `ds4` → `antirez/ds4` rev `80ebbc39…`; `ds4-hip` → `ejpir/ds4-hip` rev `3490c2e4…`
- **`nixosModules`** (upstream `flake.nix:513-532`): `default` (imports `thunderbolt-ibverbs.nixosModules.default` + applies its overlay), `thunderbolt-ibverbs`, `rpc-server`, `benchmark-executor`, `benchmark-runner`, `ec-su-axb35`, `fastflowlm`, `smu-exporter`, `npu-exporter`, `ryzenadj`, `tuning`.
  ⚠ Note `modules/strix-ai.nix:80-83` deliberately adopts **only** `hardware.firmware = [strixAi.strix-halo-mes-firmware]` from upstream's `tuning` module, because importing it wholesale would regress the GTT ceiling (§4.1).
- **Packages exposing `linux-thunderbolt`, `linux-thunderbolt-dev`, `linux-thunderbolt-modules`, `thunderbolt-ibverbs`, `thunderbolt-ibverbs-linux-thunderbolt`, `thunderbolt-ibverbs-bench-tools`, `thunderbolt-ibverbs-perftest`** (upstream `flake.nix:305-318`, `:657-658`, `:701-706`).
- **A ready-made TP=2 pair harness**: `strix-halo-vllm-pair-bench-<s>` (upstream `flake.nix:712-714`, package at `pkgs/strix-halo-vllm-pair-bench/default.nix`, 9484 B), built on `vllmPairBenchEnv = symlinkJoin [ pkgs.vllm-rocm … ]` "so the vLLM closure exposes both `vllm` and `ray`" (`:683-689`). Its scenarios include `qwen-peak | llama-tp2-win | qwen35-122b-awq-capacity | qwen35-122b-awq-prime | minimax-m27-awq-strix-2h`; it takes `MASTER`/`WORKER`, `TRANSPORTS` including `usb4_rdma` and `lan_tcp`, and exports `TB_IFNAME=thunderbolt0`, `USB4_HCA`, and a full NCCL env block (`NCCL_IB_GID_INDEX=1`, `NCCL_MIN_NCHANNELS=4`, …) — `pkgs/strix-halo-vllm-pair-bench/default.nix:140-235` **[S]**.

### 5.3 The #237 escape hatches — what survived **[S]**

`modules/strix-ai.nix:55-64` (the whole diff of `b8b63f41`) removes `ds4-rocm` and `vllm-rocm` from `environment.systemPackages`, leaving a comment: *"when the DS4 TP=2 bring-up resumes (docs/local-ai/ds4-vllm-recon-2026-08-21.md), `nix build .#ds4-rocm` / `.#vllm-rocm` are the one-command escape hatches, and the renderer backends in modules/local-models.nix stay declared — they only enter a closure when a deployment actually selects them."*

Verified, all three legs:
1. **Flake outputs still exist.** `flake.nix:568-578` — `inherit (strixAi) ds4-rocm ec-su-axb35-monitor llama-cpp-rocm llama-cpp-vulkan mlx-lm mlx-rocm strix-halo-mes-firmware tokenizers-cpp vllm-rocm;` under `packages.${system}`, described at `:563-566` as "Explicit accelerator escape hatches."
2. **Renderers still declared.** `lib/local-model-runtime.nix:24-45` — `ds4` and `vllm` renderers intact. The `vllm` renderer emits:
   `${packages.vllm}/bin/vllm serve <modelDirectory> --host 127.0.0.1 --port ${PORT} --served-model-name <deployment.model>`
   with `env = offlineModelEnv ++ [TORCHINDUCTOR_CACHE_DIR, TRITON_CACHE_DIR, VLLM_CACHE_ROOT, VLLM_DO_NOT_TRACK=1, VLLM_NO_USAGE_STATS=1]` and `useModelName`.
   **⚠ This renderer is single-node: `--host 127.0.0.1`, no `--tensor-parallel-size`, no Ray address. A TP=2 pair service cannot reuse it as-is.**
3. **Backend enum still complete.** `lib/local-model-backends.nix:2-8`, asserted at `flake.nix:1800-1818` and again at `:1827-1831` (exact rendered `vllm` cmd string + `HF_HUB_OFFLINE=1` in env).
4. **Devshell** — `flake.nix:589+` `devShells.${system}.default` contains agenix + a `with pkgs` list; **no ds4/vllm devshell exists** (`grep -n "ds4\|vllm" flake.nix` shows no hit inside the devShells block) **[M]**.

### 5.4 What such a module would have to add itself **[S]**

- **Firewall.** `networking.firewall.trustedInterfaces = [ "enp191s0" ]` on both boxes (`hosts/coordinator/eth-fleet.nix:80`, `hosts/worker/default.nix:321`) — **`thunderbolt0` is NOT trusted on either host**, and `flake.nix:725-726` asserts `wlp192s0` is never trusted. Ray/NCCL/ar2 ports (recon doc names 18515/18531, `:70`) on 10.99.0.x would need explicit admissions.
- **A both-ends invariant.** The `modules/strix.nix` import-is-the-gate idiom (§1.5) is the committed precedent for anything that must be symmetric.
- **The MTU decision.** `modules/lowlat-cluster.nix:49-54` explicitly parks the second TB rail and jumbo frames: "a TP=2 decode all-reduce is a ~5 KB payload, i.e. latency-bound, not bandwidth-bound. When that changes, it belongs in tb-fleet.nix beside the rail it extends."
- **Weights.** `deepseek-v4-flash-0731-bf16` (`lib/local-models.nix:771-…`) is stocked in the Library only and named "REQUIRED IN FULL ON EACH BOX … Served by vLLM/Ray as NixOS services, never as a llama-swap row." **⚠ Its own stated download command is broken — see §7.5.**

---

## 6. `tb-link-heal` / `tb-fleet` units

### 6.1 Where defined — **DUPLICATED, not parameterized** **[S]**

| | coordinator | worker |
|---|---|---|
| file | `hosts/coordinator/tb-fleet.nix` (157 lines) | `hosts/worker/default.nix` |
| heal script | `:50-105` (`let healScript = pkgs.writeShellScript "tb-link-heal"`) | `:225-270` (inline `ExecStart = pkgs.writeShellScript "tb-link-heal"`) |
| service | `:113-120` | `:221-273` |
| timer | `:121-128` | `:274-281` |
| `boot.kernelModules = ["thunderbolt-net"]` | `:110` | `:220` |
| peer address | `peer = "10.99.0.2"` binding at `:49` | hardcoded `10.99.0.1` at `:236`, `:241` |
| NHI PCI list | `nhiDevices = ["0000:c4:00.5" "0000:c4:00.6"]` at `:45-48` | hardcoded twice, `:245` and `:249` |
| tripwire | `myTripwire.tb-fleet-reachability`, `:131-156` | **none — the loud tripwire is coordinator-side only** (`hosts/worker/default.nix:219`) |

**There is no shared module and no option.** The two heal scripts are near-identical text; the coordinator's uses `let`-bound `peer`/`nhiDevices`, the worker's inlines the constants. The worker's version drops the coordinator's `echo` for the rate-limited branch's wording ("holding" vs "holding (replug or peer boot may still land)") — otherwise byte-equivalent logic.

### 6.2 The escalation ladder (identical on both) **[S]** — `hosts/coordinator/tb-fleet.nix:60-104`

1. `ping -c 1 -W 3 <peer>` succeeds → `rm -f "$STATE/pd-reset-stamp"`, `exit 0`.
2. XDomain peer present (`ls /sys/bus/thunderbolt/devices/ | grep -qE '^[0-9]+-[1-9]'`) → `nmcli connection up tb-fleet || true`.
3. Retimers present but no peer (`grep -qE '^[0-9]+-[0-9]+:'`) → unbind + `sleep 2` + rebind both NHIs on `/sys/bus/pci/drivers/thunderbolt`.
4. No retimers at all (the PD-blind signature) → **rate-limited to one shot per 1800 s** via `stat -c %Y "$STATE/pd-reset-stamp"`: `framework_tool --pd-reset 2`, `sleep 5`, unbind/rebind `USBC000:00` on `ucsi_acpi`, `touch` the stamp.

`StateDirectory = "tb-link-heal"` on both (`:118`, `hosts/worker/default.nix:271`) — that is what makes `/var/lib/tb-link-heal` exist.

### 6.3 Cadences **[S]**

| unit | OnBootSec | OnUnitActiveSec | Accuracy | file:line |
|---|---|---|---|---|
| `tb-link-heal.timer` (both hosts) | 2min | **2min** | 30s | `tb-fleet.nix:124-126`, `worker/default.nix:277-279` |
| `fleet-mtu.timer` (inert) | 1min | 2min | 30s | `lowlat-cluster.nix:170-172` |

| tripwire | interval | onBoot | threshold/cmp | sustain | refractory | file:line |
|---|---|---|---|---|---|---|
| `tb-fleet-reachability` | 300 s (5 min) | 5min | 1 / ge | **900 s (~15 min)** | 21600 (6 h) | `tb-fleet.nix:133-139` |
| `eth-fleet-reachability` | 900 s (15 min) | 15min | 1 / ge | **3600 s (~1 h)** | 43200 (12 h) | `eth-fleet.nix:88-94` |
| `fleet-latency` | 900 s (15 min) | 10min | 200 / ge | **3600 s (~1 h)** | 43200 (12 h) | `lowlat-cluster.nix:185-191` |

Sensors: TB pings `10.99.0.2` (`tb-fleet.nix:143`), eth pings `10.99.1.2` (`eth-fleet.nix:98`) — both report `1` when dark. The TB tripwire deliberately keeps pinging the /30, not the fleet IP (`eth-fleet.nix:36-39`: "it watches the FAST rail specifically").

**[M] Live coordinator timers, all three present and cycling:**
```
Fri 2026-08-28 22:10:57 CEST 1min 35s  … tb-link-heal.timer                    → tb-link-heal.service
Fri 2026-08-28 22:11:53 CEST 2min 31s  … tripwire-tb-fleet-reachability.timer  → tripwire-tb-fleet-reachability.service
Fri 2026-08-28 22:17:04 CEST 7min      … tripwire-eth-fleet-reachability.timer → tripwire-eth-fleet-reachability.service
Fri 2026-08-28 22:21:09 CEST 11min     … tripwire-fleet-latency.timer          → tripwire-fleet-latency.service
```
All four services `inactive dead` (oneshots between runs); no failure markers observed. Worker: `systemctl is-active tb-link-heal` → `inactive` (correct for a timer-driven oneshot), `fleet-identity` → `active`.

---

## 7. Contradictions with the older campaign record

### 7.1 ⚠ "deploy-rs dials the worker at 10.99.0.2 over Thunderbolt at flake.nix:536 guarded by asserts at 1194/1227" — **THIS IS STILL THE CURRENT STATE. It was NOT replaced by #240.**

This is the most important correction in the dossier. The brief described it as the PRE-#240 state and asked me to confirm it is gone. **It is not gone.**

**[S] `flake.nix:536`, current committed bytes:**
```nix
hostname = if host == "worker" then "10.99.0.2" else host;
```
with the comment block `:525-535` still reading "except the worker, which is dialled by its THUNDERBOLT address … The cable measured 0.6ms rtt against 105ms (mdev 54ms) over the 6GHz LAN".

**[S] Both asserts are intact:**
- `flake.nix:1194` — `assert nixpkgs.lib.elem "10.99.0.2" meshRegistry.${strixWorker}.aliases;`
- `flake.nix:1227` — `assert self.deploy.nodes.${strixWorker}.hostname == "10.99.0.2";`
  preceded by `:1224-1226`: "Deliberately the cable, not the name — see the deploy node comment."
- plus `flake.nix:1228-1229` — `assert nixpkgs.lib.elem self.deploy.nodes.${strixWorker}.hostname meshRegistry.${strixWorker}.aliases;`

**[M] Provenance:** `git show --stat d16cbd2f` lists four files, none of them `flake.nix`. `git log -1 -S'hostname = if host == "worker"' -- flake.nix` → `85e144d4 2026-08-21 worker: gpu-cooldown deleted; coordinator dials the box over the cable`.

**What #240 actually changed** was the **operator SSH nickname** (`home/ssh.nix:97`, `workerRail = … "10.99.9.2"`) and the **route metrics** (`eth-fleet.nix:75` / `worker/default.nix:317`, metric 20). It did **not** move the deploy target.

**The result is a live doctrinal split in committed bytes:** `home/ssh.nix:62-66` states that admin traffic — and it explicitly enumerates "interactive SSH, reboots, **deploys**, health checks" — prefers the wire, while `flake.nix:536` still routes the single heaviest admin operation (a deploy, ~39 GB per `flake.nix:530`) over `10.99.0.2` on Thunderbolt, the rail `home/ssh.nix:79-82` calls out for owning "the whole … USB-C/PD stack" failure class. `deploy .#worker` never touches `~/.ssh/config` — `fleetDeploySshOpts` passes `-F /dev/null` (`flake.nix:300-301`, rationale at `home/ssh.nix:24-26`) — so the nickname flip cannot reach it.

**Anything a spec BELIEVE line asserts about deploy addressing must cite `flake.nix:536` + `flake.nix:1227` and say `10.99.0.2`, not `10.99.9.2`.**

### 7.2 ✅ "MTU 65520 raised via transient units" — now DECLARATIVE, and OFF

- **Declarative**: `modules/lowlat-cluster.nix:114-141` (`jumboInterfaces`, defaulting `thunderbolt0`/`thunderbolt1` to 65520 and `enp191s0` to 9000) + `:160-174` (oneshot + 2-minute timer, both under `lib.mkIf cfg.jumbo`) **[S]**.
- **Off**: `jumbo` defaults `false` (`:99`); no caller sets it; `modules/strix.nix:157-159` is a comment saying so **[S]**.
- **Transient predecessor confirmed and confirmed dead**: `git log --oneline -S'65520' --all` names only `b37dea0b` in the modern era **[M]**; the module header at `:13` records "Re-measured 2026-08-28 19:xx with the transient unit live", `:30-31` "unlike the throwaway transient unit this file replaces", and the #238 commit message says "The transient systemd-run unit that proved it died with today's worker reboot; this module is its declarative replacement." **[M]** `grep -rn "systemd-run" --include=*.nix .` → no hits.
- **Empirically off** — `systemctl list-units --all 'fleet-mtu*'` → 0 units; all six interfaces on both boxes read `mtu=1500` **[M]**.

So this claim is **half-superseded**: "declarative — yes; on — no." A spec line must not say the fleet runs jumbo frames.

### 7.3 ⚠ Note: `modules/strix-ai.nix` is coordinator-titled but imported by both hosts

`modules/strix-ai.nix:6` reads "Accelerated AI package plane for **the coordinator**." — but it is imported unconditionally from `modules/strix.nix:26`, which both hosts import. Same for `modules/llama-swap.nix:7` ("for the Strix Halo coordinator"). The bodies contain no hostname conditional. **[S]** Header prose is stale relative to the #229 worker reintegration; the code is fleet-wide on the twins. `modules/strix.nix:8-11` is the accurate statement.

### 7.4 ⚠ `iommu.passthrough=0` is claimed but not on either live cmdline

`modules/strix.nix:164` says "hardware.amd-npu additionally pins iommu.passthrough=0". **[M]** Neither `/proc/cmdline` contains it (§4.3). Possible explanations I did **not** rule out: it may be set via `boot.extraModprobeConfig`/a sysfs write rather than a kernel param, or the upstream module may have dropped it. **UNDETERMINED.** What I looked at: both `/proc/cmdline`s, `grep -rn "iommu" --include=*.nix .` (only `modules/strix.nix:164,166` and `flake.nix:1765`). What would settle it: reading `nix-amd-ai`'s `nixosModules.default` source (locked rev `09407d22301e04dd075e125c6bef5c57a0ed5536`) — I could not resolve its store path this session (see §7.6).

### 7.5 ⚠ `localModelStore.packages` does not exist — `flake.nix:585` and `:1638` reference a missing attribute

**[S]** `lib/model-store.nix:58-60` exports exactly three names:
```nix
{
  inherit runtimeRoot materialized manifestFor;
}
```
**[S]** `flake.nix` references a fourth:
- `:585` — `legacyPackages.${system}.models = localModelStore.packages;` (comment `:583-584`: "Artifact rows are individually buildable as `nix build .#models.<id>`")
- `:1638` — `modelPackagePaths = map toString (builtins.attrValues localModelStore.packages);` inside the `local-model-routing` flake check, consumed by the assert at `:1732` (`intersectLists … == 22`)

**[M] Empirically confirmed:**
```
$ nix eval --impure --expr 'let lib=…; catalog=import …/lib/local-models.nix {inherit lib;};
    ms=import …/lib/model-store.nix {inherit lib catalog;}; in builtins.attrNames ms'
[ "manifestFor" "materialized" "runtimeRoot" ]

$ … in ms.packages
error: attribute 'packages' missing
```
Consequences: `nix build .#models.<id>` and `nix flake check`'s `local-model-routing` derivation both throw. This is collateral from the 2026-08-21 "weights leave nix" rewrite (`lib/model-store.nix:5-25`) that `flake.nix` was never updated for — the same class of drift `flake.nix:1690-1695` documents for the `libraryPath` assert. **It also invalidates the download instruction baked into `lib/local-models.nix:774`** ("download explicitly via `nix build .#models.deepseek-v4-flash-0731-bf16`"), which is the recorded path for the TP=2 weights. `flake.nix:1732`'s `== 22` count is doubly stale, since `coordinator.system.extraDependencies` no longer holds weight paths at all.

### 7.6 Method note: the flake cannot be evaluated in place

```
$ nix eval --impure --expr '(builtins.getFlake "/home/tom/mecattaf/dotfiles").inputs.nixos-hardware.outPath'
error: … while fetching the input 'path:/home/tom/mecattaf/dotfiles'
       error: file '/home/tom/mecattaf/dotfiles/home/dot_config/cliamp/cliamp.sock' has an unsupported type
```
**[M]** A unix socket is committed/present under `home/dot_config/cliamp/`, and Nix refuses to copy the tree. Every `nix eval`/`nix build`/`nix flake check` against this path fails before reaching any expression. I therefore verified input contents by locating store paths and matching `nix hash path --sri` against `flake.lock` narHashes (exact matches recorded for `nixos-hardware` §4.1 and `nix-strix-halo` §5.2), and evaluated `lib/` files directly with `--expr` (§7.5). **This also means §7.5's eval failures cannot currently be observed via `nix flake check` on this checkout** — a spec that plans to lean on `nix flake check` as a gate should know the gate does not run here.

---

## Appendix A — file inventory touched this session

| path | lines | role |
|---|---|---|
| `/home/tom/mecattaf/dotfiles/flake.nix` | 1877 | inputs, deploy nodes, packages, ~15 flake checks |
| `/home/tom/mecattaf/dotfiles/flake.lock` | — | locked revs (verified by narHash) |
| `/home/tom/mecattaf/dotfiles/modules/lowlat-cluster.nix` | 218 | §1 |
| `/home/tom/mecattaf/dotfiles/modules/strix.nix` | 202 | importer + gate + kernel params |
| `/home/tom/mecattaf/dotfiles/modules/strix-ai.nix` | 93 | §5.3 (#237) |
| `/home/tom/mecattaf/dotfiles/modules/local-models.nix` | 422 | §3.2, §3.3 |
| `/home/tom/mecattaf/dotfiles/modules/llama-swap.nix` | 125 | §3.4 |
| `/home/tom/mecattaf/dotfiles/modules/mesh-registry.nix` | 84 | §2.4 |
| `/home/tom/mecattaf/dotfiles/modules/mesh.nix` | 37 | §2.4 |
| `/home/tom/mecattaf/dotfiles/modules/tripwire.nix` | (read :1-120) | tripwire option surface |
| `/home/tom/mecattaf/dotfiles/modules/common.nix` | (read :30-60) | `boot.kernelPackages` |
| `/home/tom/mecattaf/dotfiles/home/ssh.nix` | 124 | §2.3 |
| `/home/tom/mecattaf/dotfiles/hosts/coordinator/default.nix` | 78 | imports |
| `/home/tom/mecattaf/dotfiles/hosts/coordinator/tb-fleet.nix` | 157 | §6 |
| `/home/tom/mecattaf/dotfiles/hosts/coordinator/eth-fleet.nix` | 112 | §2.1, §2.2 |
| `/home/tom/mecattaf/dotfiles/hosts/worker/default.nix` | 388 | §2, §6 |
| `/home/tom/mecattaf/dotfiles/hosts/nas/models.nix` | (read :80-227) | library-fetch |
| `/home/tom/mecattaf/dotfiles/lib/local-models.nix` | 2008 | §3.1 |
| `/home/tom/mecattaf/dotfiles/lib/local-model-runtime.nix` | 58 | renderers |
| `/home/tom/mecattaf/dotfiles/lib/local-model-backends.nix` | 10 | backend enum |
| `/home/tom/mecattaf/dotfiles/lib/model-store.nix` | 60 | §3.3, §7.5 |
| `/home/tom/mecattaf/dotfiles/lib/mage-models.nix` | 559 | snapshot-artifact template |
| `/home/tom/mecattaf/dotfiles/docs/local-ai/ds4-vllm-recon-2026-08-21.md` | 109 | §5 |
| `/nix/store/g82m9l2b7rnff5zgyk3kx8lg53r22p1y-source` | — | nixos-hardware, narHash-verified |
| `/nix/store/9abiq2k7f7rkkmr1b96r7lrflg9w1jnz-source` | — | nix-strix-halo, narHash-verified |

## Appendix B — commands run, for reproduction

```
git status / git log --oneline -15 / git show --stat {b37dea0b,d16cbd2f,b8b63f41}
git log -1 -S'hostname = if host == "worker"' -- flake.nix
git log --oneline -S'65520' --all
grep -rn "lowlat\|myLowLatCluster" --include=*.nix .
grep -rn "tb-link-heal\|tb-fleet" --include=*.nix .
grep -rn "gttsize\|gtt_size\|pages_limit\|amd_iommu\|iommu" --include=*.nix .
grep -rn "kernelPackages\|linuxPackages\|boot.kernelParams" --include=*.nix .
grep -o 'backend = "[a-z]*"' lib/local-models.nix | sort | uniq -c
grep -rn "flash-next\|qwen38-flash\|fp8\|FP8" --include=* .
grep -rn "trustedInterfaces\|thunderbolt0" --include=*.nix .
grep -rni "pair-check\|tp=2\|tensor-parallel" --include=*.nix --include=*.md .
grep -rn "systemd-run\|transient" --include=*.nix .
systemctl is-active lowlat-cluster ; systemctl cat lowlat-cluster
sudo ls -l /proc/$(systemctl show -p MainPID --value lowlat-cluster)/fd/
sudo od -An -td4 /dev/cpu_dma_latency
systemctl list-units --all 'fleet-mtu*' ; systemctl list-timers --all
ip route show | grep 10.99 ; ip -4 addr show
for i in thunderbolt0 thunderbolt1 enp191s0; do cat /sys/class/net/$i/mtu; done
uname -r ; cat /proc/cmdline ; cat /sys/module/ttm/parameters/pages_limit
ssh worker '<same set>'
curl -fsS http://localhost:9292/v1/models
ls -la /mnt/nas/models/weights/ ; ls .../qwen38-flash-next-fp8/ ; du -sh
nix hash path --sri --type sha256 <store paths>   (vs flake.lock narHashes)
nix eval --impure --expr '… import lib/model-store.nix … builtins.attrNames ms'
```
