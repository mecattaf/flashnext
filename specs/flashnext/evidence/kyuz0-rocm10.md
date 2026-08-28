# kyuz0 lineage — ROCm 10.0 update (2026-08-28) and the container-base decision

Investigated 2026-08-28. Analysis-only; no working tree was modified. All three
clones were `git fetch origin`'d; every claim below carries a file:line or a
literal command output.

Grades: **[S]** read from source · **[M]** measured (command output) · **[CL]** claimed by docs, unverified.

---

## 0. Headline

kyuz0 did ship a **ROCm 10.0** migration today — but **only for the llama.cpp / ds4
(GGUF) toolboxes**. The **vLLM lineage was not touched today and cannot be moved to
ROCm 10 right now**, because the wheel index that build auto-discovers from
(`repo.amd.com/rocm/whl-multi-arch/`) tops out at **rocm7.14.0**. There is a newer
`vllm-therock-gfx1151` image than the one ds4-vllm pins, but it is **ROCm 7.14 +
vLLM 0.28.0**, not ROCm 10, and it is a **different distro and build method** than
the pinned digest.

So the campaign premise "pin a base that has ROCm 10 + a vllm close to the PR
merge-base" is **not currently satisfiable from kyuz0's registry**. Those are two
different image families.

---

## 1. Fetch results and what landed

### 1.1 Fetch output [M]

```
$ cd /home/tom/Downloads/amd-strix-halo-toolboxes && git fetch origin
(no new refs; local main already at origin/main)
origin  https://github.com/kyuz0/amd-strix-halo-toolboxes (fetch)

$ cd /home/tom/Downloads/strix-halo-ds4-toolbox && git fetch origin
From https://github.com/kyuz0/strix-halo-ds4-toolbox
   10e9941..43a7fa5  main -> origin/main
 * [new tag]         cockpit-v2026.8.28.1814 -> cockpit-v2026.8.28.1814
 * [new tag]         cockpit-v2026.8.28.1707 -> cockpit-v2026.8.28.1707

$ cd /home/tom/Downloads/ds4-kyuz0 && git fetch origin
From https://github.com/kyuz0/ds4
   971110a..7eb2058  perf/rocm-gfx1151-mmq-kernel-lab -> origin/perf/rocm-gfx1151-mmq-kernel-lab
```

Note `ds4-kyuz0` tracks **only** `perf/rocm-gfx1151-mmq-kernel-lab` from
`origin` (`kyuz0/ds4.git`); it also carries a second remote `up` →
`../ds4-antirez`. [M]

### 1.2 Commit volume [M]

```
$ git log --all --since=2026-08-21 --oneline | wc -l   (and today's window)
amd-strix-halo-toolboxes    7d=7   today=2
strix-halo-ds4-toolbox      7d=9   today=7
ds4-kyuz0                   7d=53  today=13
```

### 1.3 `amd-strix-halo-toolboxes` — origin/main, last 7 days [M]

| Commit | Date (iso) | Subject |
|---|---|---|
| `9ff3c5e7791ce70d89a1d8fa2ffcd54268b5f7b4` | 2026-08-28 08:16:02 +0100 | Retire deprecated ROCm and AMDVLK toolboxes |
| `d3cef36e858bd97a780e470fa5ceba5198638788` | 2026-08-28 08:06:56 +0100 | **Replace stable ROCm 7.14 toolbox with ROCm 10.0** |
| `aa13f7e4aa13d6590769ae4ffe182add3795c4c2` | 2026-08-27 12:04:03 +0100 | Update Qwen3.8 Flash Next toolbox PR source |
| `dcda4fcfbf186878162d120b4a0c3a529834b30e` | 2026-08-27 08:45:42 +0100 | Fix Qwen3.8 Flash Next toolbox source |
| `9a6edb9074e2e8cd60c076dbb17b1ee1610b2429` | 2026-08-26 16:06:08 +0100 | Add experimental Qwen3.8 Flash Next ROCm toolbox |
| `c8777f2aab3997b2f1ec653a2c5c27b2b1fdc2d3` | 2026-08-26 11:26:22 +0100 | feat: add ROCm 7.14 performance toolbox |
| `e3c240aa2caf93e16926a68ce6dfb2a2ff7d7c72` | 2026-08-24 09:02:11 +0100 | feat: enable ROCmI4 W4A4 in ROCmFPX toolbox |

### 1.4 `strix-halo-ds4-toolbox` — origin/main, last 7 days [M]

| Commit | Date (iso) | Subject |
|---|---|---|
| `43a7fa5b06ba6147c8f82963250c1d7c43074b29` | 2026-08-28 18:14:02 +0000 | chore(cockpit): bump version to 2026.8.28.1814 |
| `a3731de89dd32c42a21cb9d3a51f17f9c0c1f94f` | 2026-08-28 18:10:13 +0100 | fix |
| `b332244dd2634f1e085fcd969450f36dbf1f1a97` | 2026-08-28 17:07:28 +0000 | chore(cockpit): bump version to 2026.8.28.1707 |
| `7e23f647d634184fd8f873b833a3abda16c6072f` | 2026-08-28 18:07:15 +0100 | deprecation of cockpit |
| `10e99417cc7c24011a2c2809abf1ea230b2d8c5a` | 2026-08-28 11:57:55 +0000 | chore(cockpit): bump version to 2026.8.28.1157 |
| `41b6e82da0e68d9af8bc32f886eac543df2fa25f` | 2026-08-28 12:57:27 +0100 | **feat: migrate DS4 toolbox to ROCm 10.0** |
| `07c355ec51bee3da72c6c55ca43384b02947a550` | 2026-08-28 08:16:33 +0100 | fix(rocm): include cmp in runtime image |
| `5e5bb82e6aa94a1cb449d5af6f88e575766ed449` | 2026-08-26 10:17:23 +0000 | chore(cockpit): bump version to 2026.8.26.1017 |
| `2a4bf26396b814d16d34b91514a60aeeccf37e7b` | 2026-08-26 11:16:02 +0100 | chore: retire merged DS4 ROCm variants |

### 1.5 `ds4-kyuz0` (`kyuz0/ds4`, branch `perf/rocm-gfx1151-mmq-kernel-lab`) — today [M]

13 commits today. The ones that matter for a container pin:

| Commit | Time | Subject |
|---|---|---|
| `7eb20587bcb89bc8ac07005e79ba634073a42436` | 19:11:43 | fix(rocm): adapt DSpark scheduling on gfx1151 (**branch tip**) |
| `84f10509b7f19bc7d50ff10472d9ecbae0c6e3f8` | 17:53:13 | fix(rocm): preserve target cache with DSpark support |
| `63a82204e9ba1196bcdf9369effa905ec8ceda06` | 16:38:30 | feat(bench): add basic DSpark throughput support |
| `971110a3f615d8aa5bbe080d39f92949d70b3063` | 15:06:17 | **perf(rocm): retune DSpark F16 solutions for rocBLAS 5.6** |
| `ae169b2cccfbb455857d66841280db81129c9960` | 14:24:51 | fix(rocm): stop retrying rejected DSpark GEMM solutions |
| `d9f6ed5def538c908c4c19c3c928bcfd5e7d0153` | 12:21:13 | perf(dspark): fuse gfx1151 seed and draft verification |
| `d18b8b521951d63a738cd6b824f3a43ab3adefcd` | 10:58:33 | perf(dspark): skip zero-threshold confidence probe |
| `4bf09143b446780548415a8c82d2120874887bbe` | 10:50:35 | perf(rocm): accelerate gfx1151 DSpark verification |
| `78ceec06d666e776a0a855b53e90c7710ad4f97b` | 08:09:13 | perf(rocm): specialize tiny Q8 verifier batches |
| `a307fb51d9b140461ed2dae04fe2a00c510d913d` | 02:52:56 | perf(rocm): accelerate tiny-batch DSpark verification |

The local working tree of `ds4-kyuz0` sits at `971110a` (15:06), i.e. **4 commits
behind** `origin/perf/rocm-gfx1151-mmq-kernel-lab` = `7eb2058`. [M]

---

## 2. What the ROCm 10.0 update actually changed

### 2.1 Exact ROCm version and repository move [S]

Both migrations swap the AMD package repo **host and layout**, not just a version
string. From `git show d3cef36 -- toolboxes/` (`Dockerfile.rocm-7.14` →
`Dockerfile.rocm-10.0`, similarity 76%):

```
-[rocm]
-name=ROCm 7.14.0
-baseurl=https://repo.amd.com/rocm/packages-multi-arch/rhel10/x86_64
+[amdrocm-stable]
+name=ROCm 10.0.0
+baseurl=https://stable.repo.amd.com/rocm/core/packages/rhel10/x86_64
 enabled=1
 priority=50
 gpgcheck=1
-gpgkey=https://repo.amd.com/rocm/packages-multi-arch/gpg/rocm.gpg
+gpgkey=https://stable.repo.amd.com/rocm/gpg/packages.gpg
```

Exact version string: **ROCm 10.0.0**, served from a *new* host
`stable.repo.amd.com` under `/rocm/core/packages/rhel10/x86_64`, with a new GPG
key path `/rocm/gpg/packages.gpg`. [S]

### 2.2 Package-set change [S]

Build stage (both repos):
```
-  amdrocm-core-devel7.14-gfx1151 \
+  amdrocm-core-devel10.0-gfx1151 \
```

Runtime stage — **two packages collapse into one meta-package**:
```
-  amdrocm-runtime7.14 amdrocm-blas7.14-gfx1151 \
+  amdrocm10.0-gfx1151 \
```
`amd-strix-halo-toolboxes` annotates the reason in the same hunk:
> "Runtime components required by llama.cpp's HIP backend. AMD's gfx1151 base
> meta-package supplies the runtime libraries while avoiding developer tools."
(replacing the older "These remain official AMD packages while avoiding unrelated
SDK libraries and developer tools.") [S]

The eight `/opt/rocm` compatibility symlinks all retarget `core-7.14` → `core-10.0`
(`/opt/rocm/core`, `/bin`, `/include`, `/lib`, `/libexec`, `/lib/llvm`, `/share`,
`/lib/llvm/amdgcn`). [S]

Base OS is **unchanged**: `registry.fedoraproject.org/fedora:44` (builder) and
`registry.fedoraproject.org/fedora-minimal:44` (runtime) in both repos. [S]

### 2.3 Which images/tags

**`amd-strix-halo-toolboxes`** — `.github/workflows/build_and_publish.yml` default
matrix, across the two commits [S]:
```
d3cef36: -  JSON='["rocm-6.4.4","rocm-7.14","therock-nightly","vulkan-amdvlk","vulkan-radv"]'
         +  JSON='["rocm-6.4.4","rocm-10.0","therock-nightly","vulkan-amdvlk","vulkan-radv"]'
9ff3c5e: -  JSON='["rocm-6.4.4","rocm-10.0","therock-nightly","vulkan-amdvlk","vulkan-radv"]'
         +  JSON='["rocm-10.0","therock-nightly","vulkan-radv"]'
```
Net: default build set is now exactly `rocm-10.0`, `therock-nightly`, `vulkan-radv`.

`9ff3c5e` **deletes three Dockerfiles** (311 deletions) [M/S]:
- `toolboxes/Dockerfile.rocm-6.4.4` (78 lines)
- `toolboxes/Dockerfile.rocm-7.14-qwen-3.8-flash-next` (132 lines) ← see §5
- `toolboxes/Dockerfile.vulkan-amdvlk` (82 lines)

README, origin/main [S]:
- `README.md:58` — `` | `rocm-10.0` | ROCm 10.0 (Fedora 44) | Latest stable ROCm Core SDK build, using AMD's supported gfx1151 package set. | ``
- `README.md:79` — "The `rocm-10.0`, `rocm-7.14-performance`, `rocm-7.14-pr26592`, and `therock-nightly` images currently apply a temporary workaround for llama.cpp issue #25992, based on pull request #25863." (prevents ROCm host buffers on iGPUs)

Surviving ROCm 7.14 images are all *experimental/custom*, not the stable lane:
`rocm-7.14-performance` (README:66), `rocm-7.14-pr26592` (README:67),
`rocm-7.14-rocmfpx` (README:69). [S]

**`strix-halo-ds4-toolbox`** [S]:
- `.github/workflows/build_and_publish.yml:31` — `JSON='["rocm-10.0","gfx1201-rocm-7.14","therock-nightly"]'`
- `.github/workflows/build_and_publish.yml:96` — `if [[ "${B}" == "rocm-10.0" || "${B}" == "therock-nightly" ]]; then`
- `README.md:54` — ``- `docker.io/kyuz0/strix-halo-ds4-toolbox:rocm-10.0` (Tracks `kyuz0/ds4:perf/rocm-gfx1151-mmq-kernel-lab`)``
- `ds4-strix-halo-cockpit/src/assets/toolboxes.json` diff:
  `"name": "ds4-rocm-7.14"` → `"ds4-rocm-10.0"`, `"tag": "rocm-7.14"` → `"rocm-10.0"`,
  description `"antirez upstream — Recommended for most users"` → `"ROCm 10.0 MMQ kernel lab — Recommended for most users"`.

### 2.4 The branch switch is the bigger change than the ROCm bump [S]

In `strix-halo-ds4-toolbox`, commit `41b6e82`, `toolboxes/Dockerfile.rocm-10.0`:

```
 ARG REPO=https://github.com/kyuz0/ds4.git
-ARG BRANCH=main
+ARG BRANCH=perf/rocm-gfx1151-mmq-kernel-lab
```

The **stable, "recommended for most users"** DS4 toolbox no longer tracks `main`;
it tracks the **MMQ kernel-lab perf branch** — the same branch as
`/home/tom/Downloads/ds4-kyuz0`, which received 13 commits today alone. This is a
material stability regression in the pin, and it is the pin the QA doc now
gates on.

### 2.5 TheRock nightly / rocBLAS — no direct change today

`Dockerfile.therock-nightly` is untouched by either ROCm 10 commit (not in either
`--stat`). [M] It still resolves the newest tarball at build time:
`toolboxes/Dockerfile.therock-nightly:15` — `| grep -o "therock-dist-linux-${GFX}-[0-9.a]*\.tar\.gz"`. [S]

No explicit rocBLAS version pin exists in either toolbox Dockerfile — rocBLAS now
arrives inside `amdrocm10.0-gfx1151`. [S] The rocBLAS version is however pinned
*by observation* in ds4 source — see §4.1.

---

## 3. Does kyuz0 publish a newer `vllm-therock-gfx1151` / a vllm+ROCm10 image?

### 3.1 The toolbox repos are the wrong place to look — measured negative [M]

```
$ cd /home/tom/Downloads/amd-strix-halo-toolboxes && git log --all -S'vllm-therock' --oneline
(no output)
$ cd /home/tom/Downloads/strix-halo-ds4-toolbox && git log --all -S'vllm-therock' --oneline
(no output)
```
The string `vllm-therock` **has never existed in either toolbox repo's history**.
Every `vllm` hit in those repos is incidental: process-name matching in
`systemd/gpu-workload-watch/gpu-workload-watch:56,68,78,86` (`*vllm*` glob), a
README mention at `systemd/gpu-workload-watch/README.md:7`, and a comment at
`refresh-toolboxes.sh:49` ("Match the known-good RDMA setup used by the vLLM
Toolbx project."). [S/M] These repos are llama.cpp/ds4 (GGUF) only.

### 3.2 The vLLM image has its own repo — found and read [M]

```
$ git ls-remote --exit-code -h https://github.com/kyuz0/amd-strix-halo-vllm-toolboxes.git
EXISTS
```
(probed alongside `kyuz0/vllm-therock-gfx1151`, `kyuz0/vllm-therock`,
`kyuz0/vllm-toolbox`, `kyuz0/strix-halo-vllm-toolbox` — all unreachable).

Cloned read-only to
`/tmp/claude-1000/-home-tom-mecattaf-notes-qwen-next/c4262a6d-5cde-494b-9143-685dd896a84f/scratchpad/probe/vllm-tb`.
Confirmed as the publisher: `.github/workflows/build-ubuntu-stable.yml:54` —
`REPO="${OWNER}/vllm-therock-gfx1151"`; `.github/workflows/promote-latest.yml:12`
and `build-and-publish.yml:20` — `IMAGE_REPO: kyuz0/vllm-therock-gfx1151`. [S]

**Its most recent commit is `23cb726435dcd9146e92012a7fd39b4f2f82af3a`,
2026-08-17 19:54:39 +0100, "more patches to support deepseek v4". Nothing today.** [M]

### 3.3 Registry state — SETTLED, not undetermined

Read via the public Docker Hub tags API (read-only HTTP; **no `podman pull` was
run**):

```
$ curl -fsSL "https://hub.docker.com/v2/repositories/kyuz0/vllm-therock-gfx1151/tags/?page_size=40&ordering=last_updated"
count= 126
2026-08-25T07:31:31  buildcache-repoamd
2026-08-25T07:27:14  20260825-065811
2026-08-25T07:27:12  latest
2026-08-25T07:27:10  rocm7.14.0-torch2.11.0-vllm0.28.0
2026-08-17T09:02:22  dev
2026-08-17T09:02:19  rocm7.14.0-torch2.11.0-vllm79f3183f86b89c3bda05d467
2026-08-16T15:55:32  rocm7.14.0-torch2.11.0-vllm0.27.2rc0
2026-08-16T13:28:43  rocm7.14.0-torch2.11.0-vllm0.27.1
2026-08-11T12:46:40  rocm7.14.0-torch2.11.0-vllm0.27.0rc1
2026-08-11T07:47:51  rocm7.14.0-torch2.11.0-vllm0.27.0
...
```

Digests for the tags that matter [M]:

| Tag | Pushed | Digest | Compressed size |
|---|---|---|---|
| `latest` | 2026-08-25T07:27:12 | `sha256:fa54dbc95805b506e38b36370ef0af5aff6044d2ab70a56a7eed5d1e22d496b4` | 3,071,399,324 |
| `rocm7.14.0-torch2.11.0-vllm0.28.0` | 2026-08-25T07:27:10 | `sha256:fa54dbc9…` (same) | 3,071,399,324 |
| `dev` | 2026-08-17T09:02:22 | `sha256:da7759e2de39b6fc70f570bc7ff8fc7502bcb49162a254177092a296da2630c3` | 3,639,153,709 |
| `rocm7.14.0-torch2.11.0-vllm0.27.1` | 2026-08-16T13:28:43 | `sha256:66f296fc28a717fa8f6539f567d97b9e3e1c962dbdcb8e40c783b541d55f9118` | 3,638,325,827 |
| **`20260613-141141` / `dev-vllm-470229c` / `sha-67ccb24`** | **2026-06-13T14:53:12** | **`sha256:25fd294fde9f729d1e75f109022ab4496c78190c0a6dc0142440529f7af20e4d`** | 10,016,602,361 |

**The digest ds4-vllm pins is confirmed**: `sha256:25fd294f…` is tagged
`dev-vllm-470229c` — the tag literally names the vLLM commit — pushed
**2026-06-13**. [M]

**Answers:**
- **Is there a newer image? YES.** `:latest` = `sha256:fa54dbc9…`, pushed
  2026-08-25, tag `rocm7.14.0-torch2.11.0-vllm0.28.0`.
- **Is there a vllm + ROCm 10 image? NO.** Zero tags with `rocm10`. The newest
  ROCm any `vllm-therock-gfx1151` tag advertises is **7.14.0**. [M]

### 3.4 Why no vLLM ROCm 10 image can exist yet — root cause [M]

The Ubuntu build auto-discovers its ROCm from AMD's wheel index rather than a pin
(`.github/workflows/build-ubuntu-stable.yml:14-17`: "Fully AUTO-DISCOVERED — no
manual pins"; `:40` `ROCM_WHL: https://repo.amd.com/rocm/whl-multi-arch/`;
`:68-84` iterate torch newest-first for a complete aligned set). That index has
no ROCm 10:

```
$ curl -fsSL "https://repo.amd.com/rocm/whl-multi-arch/amd-torch-device-gfx1151/" \
    | grep -oE 'amd_torch_device_gfx1151-[0-9][0-9.]*\+rocm[0-9.]+' | sed -E 's/.*\+rocm//' | sort -Vr | uniq -c
     31 7.14.0
     29 7.13.0

$ curl -fsSL "https://repo.amd.com/rocm/whl-multi-arch/torch/" | ... same pipeline
     31 7.14.0
     29 7.13.0

$ curl -fsS -o /dev/null -w "%{http_code}" "https://stable.repo.amd.com/rocm/whl-multi-arch/"
404
```

The ROCm 10.0 **system packages** live at `stable.repo.amd.com/rocm/core/packages/…`
(the new host the toolboxes moved to, §2.1), but the **Python/torch wheel index**
at that host does not exist (404), and `repo.amd.com/rocm/whl-multi-arch/` is
still capped at rocm7.14.0. **No ROCm 10 gfx1151 torch wheels are published.**
Since the vLLM image is pip-first and torch-driven
(`Dockerfile.ubuntu-repoamd:5-8`: "the whole gfx1151 ROCm/torch stack is STABLE
release wheels from https://repo.amd.com/rocm/whl-multi-arch/ … torch brings its
own matched ROCm (`_rocm_sdk_core`) → no system-vs-pip ABI mismatch"), it
structurally cannot go to ROCm 10 until AMD ships the wheels.

Independent confirmation the pipeline is still on 7.14: I re-ran the workflow's
own resolution logic by hand and it reproduces the published tag exactly.
`torchaudio` must match torch's version exactly (`build-ubuntu-stable.yml:81`),
and for rocm7.14.0 torchaudio maxes at 2.11.0.2 / 2.11.0 while torch reaches
2.13.0 — so the newest *complete aligned set* is torch **2.11.0**, giving
`VERSION_TAG=rocm7.14.0-torch2.11.0-vllm0.28.0` (`build-ubuntu-stable.yml:99`).
That is precisely the tag on the registry. [M]

The daily cron is `build-ubuntu-stable.yml:21` — `cron: "0 6 * * *"` (06:00 UTC).
It rebuilds only when the resolved combo changes (`:17`). So `:latest` will keep
resolving ROCm 7.14 every morning until AMD publishes rocm10 wheels. [S]

---

## 4. ROCm / TheRock version carried by each candidate base

| Candidate | ROCm / TheRock | vLLM | Torch | Evidence |
|---|---|---|---|---|
| `kyuz0/strix-halo-ds4-toolbox:rocm-10.0` | **ROCm 10.0.0** (`stable.repo.amd.com`, pkg `amdrocm10.0-gfx1151`) | none (GGUF/ds4) | none | `toolboxes/Dockerfile.rocm-10.0` via `git show 41b6e82` |
| `kyuz0/amd-strix-halo-toolboxes:rocm-10.0` | **ROCm 10.0.0**, same repo/pkg | none (llama.cpp) | none | `toolboxes/Dockerfile.rocm-10.0:4,74`; `git show d3cef36` |
| `…:therock-nightly` (both repos) | TheRock nightly, **resolved at build time**, unpinned | none | none | `Dockerfile.therock-nightly:15,19,21` |
| `kyuz0/vllm-therock-gfx1151@sha256:25fd294f…` (**ds4-vllm's current pin**) | Fedora 43 + TheRock tarball, `ARG ROCM_MAJOR_VER=7`; torch `2.13.0a0+rocm7.14.0a20260608` (nightly) | **470229c** (2026-06-13) | 2.13.0a0 nightly | `Dockerfile:1,10,31` @ `23cb726`; Docker Hub tag `dev-vllm-470229c` |
| `kyuz0/vllm-therock-gfx1151:latest` = `sha256:fa54dbc9…` | **ROCm 7.14.0** stable wheels (`ARG ROCM_VERSION=7.14.0`) | **v0.28.0** | **2.11.0** | `Dockerfile.ubuntu-repoamd:66,67`; registry tag name |
| `nix-strix-halo` | **TheRock 7.15** (see §6) | v0.25.1 | 2.11.0 | `pkgs/therock/sources/rocm.json`, `flake.nix:369` |

**No candidate has ROCm 10 + vLLM. The two properties are disjoint across kyuz0's
entire published surface today.**

### 4.1 rocBLAS: the concrete ROCm 10 breakage [S]

This is the most load-bearing finding for anyone pinning ROCm 10.

`ds4-kyuz0` commit `971110a3f615d8aa5bbe080d39f92949d70b3063`
("perf(rocm): retune DSpark F16 solutions for rocBLAS 5.6", 2026-08-28 15:06),
`rocm/ds4_rocm_runtime.cuh`:

```c
-/* rocBLAS solution indices are library-version specific. Disable the tuned
- * DSpark F16 path for the process if the active rocBLAS rejects an index. */
+/* rocBLAS solution indices are library-version specific. Unknown versions use
+ * the library default; a rejected known solution disables the tuned path. */
+enum {
+    DS4_ROCBLAS_F16_SOLUTIONS_NONE = 0,
+    DS4_ROCBLAS_F16_SOLUTIONS_5_5_CD957402,
+    DS4_ROCBLAS_F16_SOLUTIONS_5_6_8D1AE90E,
+};
...
+        if (rocblas_get_version_string(version, sizeof(version)) == rocblas_status_success) {
+            if (strcmp(version, "5.5.0.cd957402") == 0) {
+                g_rocblas_f16_solution_set = DS4_ROCBLAS_F16_SOLUTIONS_5_5_CD957402;
+            } else if (strcmp(version, "5.6.0.8d1ae90e") == 0) {
+                g_rocblas_f16_solution_set = DS4_ROCBLAS_F16_SOLUTIONS_5_6_8D1AE90E;
+            }
+        }
```

and `rocm/ds4_rocm_matmul.cuh`:
```c
             if (in_dim == 4096u && (out_dim == 64u || out_dim == 256u || out_dim == 512u || out_dim == 1024u)) {
-                solution = -217;
+                if (… == DS4_ROCBLAS_F16_SOLUTIONS_5_5_CD957402) solution = -217;
+                if (… == DS4_ROCBLAS_F16_SOLUTIONS_5_6_8D1AE90E) solution = -50;
             } else if (in_dim == 1024u && out_dim == 8192u) {
-                solution = -216;
+                if (… == DS4_ROCBLAS_F16_SOLUTIONS_5_5_CD957402) solution = -216;
+                if (… == DS4_ROCBLAS_F16_SOLUTIONS_5_6_8D1AE90E) solution = -49;
             }
```

**Reading:** ROCm 7.14 ships **rocBLAS `5.5.0.cd957402`**; ROCm 10.0 ships
**rocBLAS `5.6.0.8d1ae90e`** [inferred from the same-day sequence — see caveat].
The hand-tuned GEMM solution indices are **not portable across the bump**:
`-217/-216` become `-50/-49`. Anything carrying hardcoded rocBLAS solution indices
tuned on 7.14 will have them **rejected** on ROCm 10.

The immediately preceding commit `ae169b2` (14:24, two hours *after* the toolbox
migrated at 12:57) is the discovery: it replaced a per-call
`fprintf(stderr, "ds4: rocBLAS q4 F16 solution %d failed for %llux%llux%llu: status %d\n", …)`
with a latching `__atomic_store_n(&g_rocblas_f16_solutions_disabled, 1, …)` —
i.e. the ROCm 10 image was spamming stderr with rejected-solution errors on every
matmul. [S]

**Caveat [CL→inference]:** the commits never write the literal string "ROCm 10.0"
next to "rocBLAS 5.6". The 5.5→5.6 mapping to 7.14→10.0 is inferred from the
same-day ordering (toolbox migrates 12:57 → rejection fix 14:24 → retune 15:06)
and from `g_rocblas_f16_solution_set` handling exactly two versions. Running
`rocblas_get_version_string` (or `rocblas-bench --version`) inside
`kyuz0/strix-halo-ds4-toolbox:rocm-10.0` would settle it definitively.

### 4.2 hipBLASLt and gfx1151 [S]

ds4 links all three math libs unconditionally —
`Makefile:59`: `ROCM_LDLIBS ?= -lm -pthread -lhipblas -lhipblaslt -lrocblas`.
hipBLASLt is used through a plan cache in `rocm/ds4_rocm_hipblaslt.cuh` (handle
`g_hipblaslt`, `g_hipblaslt_gemm_plans`, `hipblaslt_gemm_plan_get`). No hipBLASLt
version gating was added today — only rocBLAS got version-keyed. So hipBLASLt
paths are assumed portable across the bump; **unverified**. [S/CL]

### 4.3 gfx1151-specific work today [S]

`ds4-kyuz0:speed-bench/gfx1151-prefill-results.md` has a new section — the only
place in the ds4 source that names ROCm 10:

- `:45` `## ROCm 10.0 DSpark scheduler follow-up`
- `:47` "The adaptive scheduler is enabled by default on gfx1151. It probes four speculative cycles and requires an average of four accepted tokens per cycle. Below that floor, the rest of the request bypasses the support model and uses ordinary target decode; the next prompt sync resets the decision."
- `:51-52` measured: 2K low-acceptance 15.40 tok/s aggregate / 15.91 steady → *bypass after four cycles*; 16K 100%-acceptance 25.71 / 26.77 → *keep DSpark active*
- `:54` "The ordinary 2K control is 15.57 tok/s. The low-acceptance bypass therefore limits DSpark overhead to 1.1% while preserving the high-acceptance speedup."
- `:56` **"The experimental compact IQ2 worklist was removed after a small-context GPU memory fault."** Retained stock x64/y64 tile: 294.38 tok/s @4K, 268.51 tok/s @16K.
- `:58` `ds4-bench` snapshot restore: restored vs pure prefill frontier logits byte-identical at 2K/4K; 295.26 vs 295.27 tok/s.

**Document inconsistency [S]:** the same file's header still says
`:6` `- Runtime: ROCm 7.14` and `:41` "ROCm 7.14 builds and links `ds4`, …", while
`:45` is the ROCm 10.0 section. The validation evidence at `:36-43` was gathered on
**7.14**, not 10.0. Do not read that validation as a ROCm 10 result.

---

## 5. The Qwen3.8-Flash-Next toolbox was RETIRED today — directly campaign-relevant

`amd-strix-halo-toolboxes` added an experimental flash-next toolbox on 2026-08-26
(`9a6edb9`), fixed it twice (`dcda4fc` 08-27 08:45, `aa13f7e` 08-27 12:04), then
**deleted it today** in `9ff3c5e` (08-28 08:16). [M]

Last content before deletion —
`git show aa13f7e:toolboxes/Dockerfile.rocm-7.14-qwen-3.8-flash-next` [S]:

```dockerfile
1:  # Experimental ROCm 7.14 Qwen3.8-Flash-Next build from upstream PR #27793.
34: ARG REPO=https://github.com/danielhanchen/llama.cpp.git
35: ARG BRANCH=qwen4exp/qwen3.8-flash-next
43: # Qwen3.8-Flash-Next (qwen4exp) support is provided by upstream PR #27793.
57:   -DAMDGPU_TARGETS=gfx1151 \
```

**This is llama.cpp, not vLLM.** kyuz0's flash-next experiment was a GGUF path
against `danielhanchen/llama.cpp` PR #27793, on ROCm **7.14**, and it lived
48 hours before being retired without ever being ported to ROCm 10.0. If the
campaign's "flashnext build" was expected to inherit anything from kyuz0's
flash-next toolbox, **that inheritance no longer exists upstream** — it must be
reconstructed from `aa13f7e` history or built independently.

---

## 6. `nix-strix-halo` — ROCm 7.x, not 10

| Pin | Value | Evidence |
|---|---|---|
| TheRock source | rev `1de3171d00f6de55e9ed517dc6ca6e825d1e4b55`, **version `7.15`**, `refs/heads/main` | `pkgs/therock/sources/rocm-source.json` (`"version": "7.15"`) |
| TheRock binary tarball (gfx1151) | `therock-dist-linux-gfx1151-7.15.0a20260719.tar.gz`, version `7.15.0a20260719`, updated `2026-07-19T15:05:53` | `pkgs/therock/sources/rocm.json` |
| Python wheels | `"series": "7.15"`, `"rocmVersion": "7.15.0a20260719"`, `"pythonTag": "cp313"` | `pkgs/therock/sources/python-wheels.json` |
| torch | `torch-2.11.0+rocm7.15.0a20260719-cp313-cp313-linux_x86_64.whl` | `pkgs/therock/sources/python-wheels.json` |
| vLLM version | `vllmVersion = "0.25.1"` | `flake.nix:369` |
| vLLM source | `url = "github:vllm-project/vllm/v0.25.1"` | `flake.nix:47-50` |
| vLLM locked rev | `752a3a504485790a2e8491cacbb35c137339ad34`, lastModified 1783899612 | `flake.lock` node `vllm-src` |

**Verdict: nix-strix-halo is on ROCm/TheRock 7.15 (a nightly-alpha dated
2026-07-19), NOT ROCm 10.** [S]

Note it is on a *different* 7.x line than the toolboxes were: TheRock **7.15**
nightly (`rocm.nightlies.amd.com`) vs the toolboxes' **7.14** stable
(`repo.amd.com`) — and now vs **10.0** stable (`stable.repo.amd.com`). Three
distinct ROCm streams across the candidate set.

Also relevant: `overlays/therock-vllm.nix` builds vLLM from source with
`VLLM_VERSION_OVERRIDE = vllmVersion` (`:296`),
`-DCMAKE_HIP_ARCHITECTURES=${gpuTargets}` (`:308`), and hard asserts
`tritonSupport` / `tritonKernelsSupport` / `otelSupport`
(`:234-241`). Triton kernels are pinned separately at
`rev = "0263a6a6203cf27c441c57a6c808ea87ffb8f654"` (`:44-45`). [S]

---

## 7. The rebase question: 470229c → what?

### 7.1 Placing 470229c precisely [M]

Resolved via a blobless clone of vllm-project/vllm
(`git clone --filter=tree:0 --no-checkout --bare`) at
`…/scratchpad/vllmtree`:

```
$ git rev-parse 470229c
470229c37efaf69c86e8bc97482b0b1ff7551c65
$ git log -1 --format='%H|%ad|%s' --date=iso 470229c
470229c37efaf69c86e8bc97482b0b1ff7551c65|2026-06-13 12:17:38 +0200|[Security] Fix DoS via prompt_embeds on M-RoPE models (#45252)
$ git tag --contains 470229c… | grep -E '^v[0-9]+\.[0-9]+\.[0-9]+$' | sort -V | head -3
v0.24.0
v0.25.0
v0.25.1
$ git merge-base --is-ancestor 470229c… v0.28.0 && echo YES
YES
$ git rev-list --count 470229c…..v0.28.0
2455
```

Release anchors [M]:
| Tag | Commit | Date |
|---|---|---|
| v0.25.1 | `752a3a504485790a2e8491cacbb35c137339ad34` | 2026-07-12 |
| v0.27.1 | `6e448d0ea9bf3d88d898b65449ca6dc2aec170ac` | 2026-08-11 |
| v0.28.0 | `2cf0a6915ce544dc493a0990f2ea38d81601128a` | 2026-08-23 |

So: **470229c is pre-v0.24.0, dated 2026-06-13, and sits 2455 commits behind
v0.28.0.** The ds4-vllm base is ~2.5 months and four minor releases stale.

Incidentally, `Dockerfile.ubuntu-repoamd:118` `ARG VLLM_REF=6e448d0ea9bf3d88d898b65449ca6dc2aec170ac`
is *exactly* the v0.27.1 tag commit — kyuz0's in-repo default already moved past
470229c on 2026-08-16 (`83d4545`, per `git log -L118,118:Dockerfile.ubuntu-repoamd`). [M]

`nix-strix-halo`'s v0.25.1 sits **between** the two: newer than 470229c, older
than the published `:latest`.

### 7.2 The patch overlay is all-modify, zero-add — the rebase risk is total [M]

```
$ cd /home/tom/Downloads/ds4-vllm
$ grep -c '^+++ ' container/patches/vllm-upstream.patch      → 31
$ grep -c '^--- /dev/null' container/patches/vllm-upstream.patch → 0
$ grep -c '^--- a/' container/patches/vllm-upstream.patch    → 31
$ grep '^+++ ' … | sed -E 's#.*site-packages/##' | cut -d/ -f1 | sort | uniq -c
     31 vllm
```

**All 31 patched files modify pre-existing files; none are new.** Every one must
exist with matching context in whatever base you rebase onto. The list includes
deep, fast-moving internals:

`vllm/v1/core/sched/scheduler.py`, `vllm/v1/worker/gpu_model_runner.py`,
`vllm/platforms/rocm.py`, `vllm/v1/core/kv_cache_utils.py`,
`vllm/compilation/breakable_cudagraph.py`, `vllm/v1/spec_decode/llm_base_proposer.py`,
`vllm/distributed/device_communicators/cuda_communicator.py`,
`vllm/model_executor/layers/sparse_attn_indexer.py`,
`vllm/third_party/triton_kernels/{target_info.py,matmul_ogs_details/opt_flags.py,routing_details/_routing_compute.py}`,
plus 7 files under `vllm/models/deepseek_v4/` and 5 under `vllm/v1/kv_offload/`
and `vllm/distributed/kv_transfer/kv_connector/v1/offloading/`.

Rebasing that across **2455 commits** is not a patch refresh; it is a port.

### 7.3 Good news: the filesystem contract survives the distro change [S]

The patch applies at the root with `-p1`:
`container/Dockerfile:76` — `RUN cd / && git apply -p1 --whitespace=nowarn /tmp/vllm-upstream.patch`
and every target is under `opt/venv/lib/python3.12/site-packages/vllm/`.

The new Ubuntu base keeps that exact layout:
`Dockerfile.ubuntu-repoamd:50-51` — `python3.12 -m venv /opt/venv`,
`ENV VIRTUAL_ENV=/opt/venv PATH=/opt/venv/bin:$PATH`;
`:268` `COPY --from=builder /opt/venv /opt/venv`;
`:181,196` reference `/opt/venv/lib/python3.12/site-packages/…` and
`_rocm_sdk_core`. The old Fedora base did the same (`Dockerfile:21-23,71-72`).

So `/opt/venv/lib/python3.12/site-packages/vllm` and `_rocm_sdk_core` (needed by
`container/Dockerfile:83` for `libhsa-runtime64.so.1`) both still exist. [S]

**But note `Dockerfile.ubuntu-repoamd:198` — `rm -rf /opt/vllm /root/.cache/pip`.**
The new base deletes the vLLM *source* tree. ds4-vllm doesn't touch `/opt/vllm`
(all patch targets are in site-packages), so this is survivable — but
`container/verify-patches.sh` and the `DS4_PATCH_SRC` regeneration flow should be
re-checked against a base with no `/opt/vllm`. [S/CL]

Other structural deltas old→new base [S]:
- distro: `FROM registry.fedoraproject.org/fedora:43` → `FROM docker.io/ubuntu:24.04` (×3 stages)
- ROCm delivery: TheRock tarball via `scripts/install_rocm_sdk.sh` + nightly torch
  `2.13.0a0+rocm7.14.0a20260608` from `rocm.nightlies.amd.com/v2-staging/gfx1151/`
  → pip stable wheels `torch==2.11.0+rocm7.14.0` from `repo.amd.com/rocm/whl-multi-arch/`
- ds4-vllm's `dnf install` steps (`container/Dockerfile:28,60`) **will not run on
  Ubuntu** — the rocr-idle-fix and provider-build stages need `apt` there.
  The rdma provider is also already built into the new base
  (`Dockerfile.ubuntu-repoamd:22-23` rdma-builder, `ARG RDMA_CORE_REF=v62.0`),
  versus ds4-vllm's `rdma-core v57.0` / `rdmav57` ABI (`README.md:149`,
  `container/Dockerfile:56`). **ABI mismatch: v57 → v62.** [S]

---

## 8. What the QA doc says the ROCm 10 migration changed or broke

`strix-halo-ds4-toolbox/QA_BEFORE_RELEASES_ROCM.md`, 529 lines. [M]

**Finding: the ROCm 10 commit changed only identifiers in this doc — it added no
new breakage, no new gate, and no ROCm-10-specific acceptance criterion.**
`git show 41b6e82 -- QA_BEFORE_RELEASES_ROCM.md` is 28 lines changed (14 `+` / 14
`-`), and every one is a `rocm-7.14` → `rocm-10.0` string swap plus the branch
change: [S]

- `:22` toolbox matrix row: `` | `rocm-10.0` | `Dockerfile.rocm-10.0` | `kyuz0/ds4` | `perf/rocm-gfx1151-mmq-kernel-lab` | gfx1151, including DeepSeek and GLM distributed inference | `` (was `` `rocm-7.14` | … | `main` ``)
- `:38-43` all six test IDs (`DS-IQ2-R`, `DS-IQ2-S`, `GLM-S`, `DS-Q4-S`, `DS-Q4-D`, `GLM-D`) retargeted to `rocm-10.0`
- `:51` `rg '^ARG (REPO|BRANCH)=' toolboxes/Dockerfile.rocm-10.0`
- `:52` (in diff) `git ls-remote https://github.com/kyuz0/ds4.git refs/heads/perf/rocm-gfx1151-mmq-kernel-lab`
- `:63` `-f backends=rocm-10.0`
- `:79`, `:104`, `:128` `IMAGE=`/`ROCM_IMAGE=docker.io/kyuz0/strix-halo-ds4-toolbox:rocm-10.0`
- `:444` cockpit gate now selects the `rocm-10.0` toolbox

Pre-existing gates that were **not** relaxed or amended for ROCm 10 [S]:
- `:189-192` every run must show `ROCm backend initialized`, no CPU fallback; no crash/OOM/ROCm error/NaN/infinity/KV mismatch/failed prefill; coherent output at every context; all CSV rows present.
- `:194-223` "DeepSeek IQ2 long-context decode regression" sweep (ctx 2048→65536, step 2048, 128 gen tokens) — required "after changes to ROCm Q8 projections, attention output, hidden-state mixing, or decode kernel selection". `:221-223`: "A candidate that improves early-context decode but regresses materially as the KV cache grows is not a pass."
- `:330` log grep gate: `'segmentation fault|ROCm error|prefill failed|evaluation failed|route (failed|incomplete)|KV.*mismatch|nan|infinity'`
- `:415-427` frontier-logit comparison: raw/centered cosine, relative RMSE, KL divergence, top-1 IDs; fail on non-finite logits, frontier top-1 mismatch, NLL/first-token/API-top-1/pair-order regression, or unexplained drift from last accepted.
- `:501-502` "Treat the figures as regression indicators, not universal pass thresholds."

**So: the QA doc documents no ROCm 10 breakage.** The only recorded ROCm-10
breakage in the whole lineage is the rocBLAS solution-index rejection in ds4
source (§4.1), which is *not* mentioned in the QA doc at all. The doc also still
gates on a `main`-tracking discipline that no longer applies now that the stable
tag follows a perf branch (§2.4).

---

## 9. Bottom line for the container-base decision

1. **ROCm 10.0 is real and shipped today**, but only in the GGUF/llama.cpp/ds4
   lane: `kyuz0/strix-halo-ds4-toolbox:rocm-10.0` and
   `kyuz0/amd-strix-halo-toolboxes:rocm-10.0`, Fedora 44, pkg `amdrocm10.0-gfx1151`
   from `stable.repo.amd.com`. Neither contains vLLM or PyTorch.
2. **There is no ROCm 10 vLLM image and cannot be one yet** — AMD has published no
   ROCm 10 gfx1151 torch wheels (`repo.amd.com/rocm/whl-multi-arch/` caps at
   7.14.0; `stable.repo.amd.com/rocm/whl-multi-arch/` 404s). The vLLM image's
   daily 06:00 UTC cron will keep resolving `rocm7.14.0`.
3. **A newer vLLM base does exist**: `:latest` / `rocm7.14.0-torch2.11.0-vllm0.28.0`
   = `sha256:fa54dbc95805b506e38b36370ef0af5aff6044d2ab70a56a7eed5d1e22d496b4`
   (2026-08-25). If the campaign wants a *pinned, current* vLLM base, that digest
   is the one — accepting ROCm 7.14, and accepting a Fedora→Ubuntu and
   nightly→stable-wheel migration plus a 2455-commit vLLM port of 31
   all-modify patch hunks.
4. **A "ROCm 10 + vllm near the merge-base" pin does not exist.** The campaign must
   choose: ROCm 10 *or* vLLM. If flashnext needs ROCm 10, it needs the ds4/GGUF
   lane (and kyuz0's own flash-next llama.cpp toolbox was deleted today, §5).
5. **If ROCm 10 is chosen anywhere with tuned GEMM:** rocBLAS moves
   5.5.0.cd957402 → 5.6.0.8d1ae90e and hardcoded solution indices are rejected
   (§4.1). Version-key them or drop them.
6. **The `rocm-10.0` DS4 tag is not a stable pin** — it tracks
   `perf/rocm-gfx1151-mmq-kernel-lab`, which took 13 commits today. Pin the
   *image digest*, never the tag.

## 10. Open / UNDETERMINED

- **rocBLAS 5.6 ⇔ ROCm 10.0 mapping** is inferred, not read (§4.1). Settle with
  `rocblas_get_version_string` inside `kyuz0/strix-halo-ds4-toolbox:rocm-10.0`.
- **Whether `sha256:25fd294f…` is still pullable.** The tag row exists in the
  Docker Hub API listing with a size, which is strong evidence, but I ran no pull.
  `podman manifest inspect docker.io/kyuz0/vllm-therock-gfx1151@sha256:25fd294f…`
  would settle it.
- **Actual vLLM commit inside `:latest`.** The tag *name* says `vllm0.28.0` and
  `build-ubuntu-stable.yml:87-99` derives the tag from the resolved `VLLM_REF`, so
  the name is machine-generated, not hand-typed. But I did not inspect the image's
  `/tmp/vllm-upstream-version` (`Dockerfile.ubuntu-repoamd:126-127`) or run
  `pip show vllm` in it.
- **hipBLASLt behaviour under ROCm 10** — no version gating was added, so it is
  assumed portable. Unverified (§4.2).
- **Whether ds4-vllm's `verify-patches.sh` still functions** against a base with
  `/opt/vllm` removed (§7.3).
- I did **not** enumerate `origin/pr-20` or `origin/agent/vulkan-radv-perfromance`
  in `amd-strix-halo-toolboxes`; `git log --all --since=2026-08-21` showed no
  commits on them in the window. [M]

## Artifacts created this session (read-only probes, safe to delete)

- `…/scratchpad/probe/vllm-tb` — clone of `kyuz0/amd-strix-halo-vllm-toolboxes`
- `…/scratchpad/vllmtree` — blobless bare clone of `vllm-project/vllm`
- `…/scratchpad/vllmprobe` — scratch repo used for single-commit fetches

No file in `/home/tom/Downloads/*` was modified; only `git fetch` was run there.
