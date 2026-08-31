> **Not the entry point any more.** Start at **`handoff/START-HERE.md`** — the resume
> document for returning to flashnext after the NixOS work (dotfiles#271). This file is the
> catalog-row handoff, which is complete apart from Step 0 below, and is kept as the record.

# Morning handoff — the fleet catalog row

One file to apply, one review, one rebuild. `catalog-row.patch` is a
`git format-patch` against **mecattaf/dotfiles**, generated overnight against
`99d72cf5` and never applied by this campaign. Applying it is a morning
operator act (spec ruling P13, claim 7.3); this repository only prepared it.

---

## Status, re-checked 2026-08-31 — read this before you `git am`

**The row is already in `mecattaf/dotfiles`.** It landed on 2026-08-30 19:02 as
`161fafb5` (PR #265, carrying #260 "models: a catalog row for the FP8 TP=2
checkpoint"), and dotfiles HEAD has since moved to `5d47acde`. Consequently
`git apply --check` of this patch against that tree now reports
`modules/strix.nix: patch does not apply` — because the change is **present**,
not because it is wrong. The `lib/local-models.nix` row is present too.

**It is deployed, and it worked.** On the coordinator,
`/etc/local-models/wanted.json` names `flashnext-fp8`, both projections exist
under `/etc/local-models/{artifacts,snapshots}/`, and the staged copy survived
the several rebuilds the SSD transition performed — 141 files,
185,563,713,216 bytes, 131 shards, still there. That is the anti-prune holding.

**One step is still open: the Library rename (Step 0 below).** The NAS still
carries `/mnt/nas/models/weights/qwen38-flash-next-fp8`, and no
`flashnext-fp8` directory exists beside it. `library-fetch` addresses the
Library as `weights/<artifactId>/`, so until that `mv` happens the 02:30 timer
can reach for ~173 GiB the NAS already holds.

So the morning act is no longer *apply*: it is **Step 0, then the
verification block at the end of this file.** Everything between them is kept
as the record of what was applied and why.

---

## Why this is the first thing you do

The staged checkpoint on the twins has no catalog row, and the fleet's own
sync service deletes what the catalog does not claim.

`modules/local-models.nix` reconciles `/var/lib/local-models` against this
host's `wanted.json`, and its prune sweep is unconditional:

```
if ! jq -e --arg id "$id" 'any(.[]; .id == $id)' "$manifest" >/dev/null; then
  echo "local-models-sync: pruning retired artifact $id"
  rm -rf "$dir"
```

`local-models-sync` is a `oneshot` with `restartTriggers = [wantedManifest]`,
so that sweep runs **at boot, on every `nixos-rebuild switch`, and on every
start of the service itself**. Nothing about it is occasional.

That is not a hypothesis. On **2026-08-29** round 1 staged the checkpoint onto
both twins and banked its receipts at 12:47 (`results/receipts/weights-*.json`,
131 shards, 185,563,854,698 bytes per node). By ~20:07 on the coordinator and
~20:14 on the worker the artifact directory was gone from both — pruned,
correctly, because no row named it. Tonight's campaign paid for that twice: a
full re-stage of **185.6 GB per node**, at the Library's measured 86–87 MB/s,
~75–80 min per box.

**Applying this patch is what ends that hazard permanently.** An artifact the
manifest wants is an artifact the prune keeps — there is no other switch, no
flag, and no way to make the sweep skip an unclaimed directory.

> **Ordering, stated plainly: this patch must be in the deployed configuration
> BEFORE any rebuild of either twin and BEFORE any restart of
> `local-models-sync.service`.** A rebuild that lands without this row deletes
> 185.6 GB from that node, again, and the loss is silent apart from one
> `pruning retired artifact flashnext-fp8` line in the journal.

---

## What the patch changes

| File | Change |
|---|---|
| `lib/local-models.nix` | Adds the `flashnext-fp8` artifact row (+862 lines), immediately after the `deepseek-v4-flash-0731-bf16` row it is modelled on. |
| `modules/strix.nix` | Moves `services.local-models.artifacts` out of the coordinator-only `lib.optionals` so `"flashnext-fp8"` is declared for **both** twins (+13/−1). |

The row follows the committed snapshot-layout precedent exactly:
`layout = "snapshot"` (a 131-shard HF tree must not collapse to basenames the
way `flat` artifacts do), `localName = "Qwen3.8-Flash-Next-FP8"` for loaders
that select by repository basename, `hfUrl` plus pinned
`revision = 970c569adaca6b35532111fd6b27351b2baefe50`, `primary = "config.json"`,
and one `files` entry per file carrying exact `bytes`, the content sha256
(`oid`), and its SRI (`hash`).

**141 files, 131 shards, 185,563,713,216 bytes.** `README.md`, `LICENSE`, and
`.gitattributes` are excluded, as they are in the precedent row — see
*What the first sync does* below for the consequence.

There is deliberately **no `deployments` row**. Serving stays with the
flashnext pair service (`host/fn-cluster-up.sh`), which is a vLLM TP=2 process
across both boxes and not something llama-swap can start. Promotion onto a
fleet roster is a separate human act; this patch changes only which bytes the
twins are entitled to keep.

### Where the numbers came from

Read from the Library copy at
`/mnt/nas/models/weights/qwen38-flash-next-fp8`, because node-side staging had
not re-run when this was written. **Re-verified 2026-08-31 against the
completed staged copy at `/var/lib/local-models/flashnext-fp8`**, which is the
source of truth the spec names: 141 files on both sides with an empty set
difference either way, zero per-file `bytes` mismatches, totals agreeing at
185,563,713,216, all 141 `hash` fields the correct SRI of their own `oid`, and
`sha256sum` recomputed on the ten non-LFS files plus shards 00001 and 00131 —
twelve for twelve against the declared digests. Nothing needed correcting. `bytes` from `stat`. `oid` from the
`huggingface_hub` download metadata under `.cache/huggingface/download/` — for
LFS files the etag *is* the content sha256, which was confirmed by recomputing
`sha256sum` on eleven shards spread across the set (00001, 00013, 00027, 00041,
00055, 00069, 00083, 00097, 00111, 00125, 00130); all eleven matched. The
eleven small non-LFS files carry a git blob sha1 as their etag, so their
sha256 was computed directly. `hash` is the base64 SRI of the same digest.

---

## Apply it

**Step 0 — rename the Library directory, on the NAS, first.** *(Still
outstanding as of 2026-08-31 — this is the one action left.)*

```
mv /mnt/nas/models/weights/qwen38-flash-next-fp8 \
   /mnt/nas/models/weights/flashnext-fp8
```

Both `library-fetch` and `local-models-sync` address the Library as
`weights/<artifactId>/`. The directory currently carries the upstream-derived
name; the artifact id is `flashnext-fp8`, because that is the node-side path
every campaign script uses (`FN_MODEL_DIR`, `scripts/stage-weights.sh`,
`scripts/run-tp2.sh`). Without the rename the 02:30 `library-fetch` timer
re-downloads 173 GiB it already has.

**Step 1 — apply and review.**

```
cd ~/mecattaf/dotfiles
git am /path/to/catalog-row.patch      # or: git apply
```

*If this reports `patch does not apply`, stop and check `git log -S
flashnext-fp8 -- lib/local-models.nix modules/strix.nix` before forcing
anything: as of 2026-08-31 the row is already committed at `161fafb5`, so the
expected outcome is a refusal, and the review below is what you do instead.*

Three things to check, and they are the only three that matter:

1. The artifact id is `flashnext-fp8`, so `materializeArtifact` yields
   `/var/lib/local-models/flashnext-fp8` — the exact directory the twins are
   already staged to, and the exact directory the pair service reads. An id
   mismatch here would prune the staged copy and re-borrow 185.6 GB per node
   under a new name.
2. `"flashnext-fp8"` is outside the `hostName == "coordinator"` guard in
   `modules/strix.nix`. Tensor parallelism shards the compute, not the
   checkpoint: each box loads the whole thing from its own NVMe.
3. The `revision` matches the checkpoint the fork was built and benchmarked
   against: `970c569adaca6b35532111fd6b27351b2baefe50`.

**Step 2 — evaluate before deploying.**

```
nix eval --impure --expr 'let lib = (import <nixpkgs> {}).lib;
  c = import ./lib/local-models.nix { inherit lib; };
  s = import ./lib/model-store.nix { catalog = c; inherit lib; };
in { dir = s.materialized.flashnext-fp8.directory;
     files = builtins.length s.materialized.flashnext-fp8.files; }'
```

Expect `/var/lib/local-models/flashnext-fp8` and `141`. The catalog's own
22 assertions (`modules/local-models.nix`, forced through `catalogValid`)
run as part of the host build; the row was checked against the four that
apply to it — primary is one of the files, every path is a safe relative
path, no repeated paths, and the snapshot alias is a unique safe basename.
`nixfmt --check` is clean on both files.

**Step 3 — deploy the NAS, then the twins.**

Deploy the NAS first: its `library-fetch` manifest wants every catalog
artifact, and after step 0 every file is present at the right size, so the
fetch loop skips all 141 and downloads nothing.

Then the twins. `wanted.json` changes, `local-models-sync` re-runs — and this
is the run that makes the artifact permanent.

> **If a twin's staged copy is missing or incomplete when you deploy it**, that
> sync run will borrow the whole 185.6 GB from the Library *synchronously*,
> inside `TimeoutStartSec = 2h` (~36 min/node at 86–87 MB/s from the
> coordinator's mount; the worker reads `/mnt/library/weights` over NFS). The
> rebuild will look hung. It is not. Check both nodes before you start:
>
> ```
> ls /var/lib/local-models/flashnext-fp8/ | grep -c safetensors   # expect 131
> du -sb /var/lib/local-models/flashnext-fp8                      # ~185.6e9
> ```

---

## Verify, after the rebuild

```
# 1. The host now wants it — on BOTH twins.
jq -r '.[].id' /etc/local-models/wanted.json | grep flashnext-fp8

# 2. The sync converged and pruned nothing it should not have.
journalctl -u local-models-sync -b | tail -30
#    Expect no "pruning retired artifact flashnext-fp8".
#    Expect no "borrowing ..." lines at all if staging was already complete.

# 3. The bytes are still there.
ls /var/lib/local-models/flashnext-fp8/ | grep -c safetensors   # 131
du -sb /var/lib/local-models/flashnext-fp8

# 4. The projections exist.
ls -l /etc/local-models/artifacts/flashnext-fp8
ls -l /etc/local-models/snapshots/Qwen3.8-Flash-Next-FP8

# 5. The real proof: restart the sweep on purpose and watch it keep the bytes.
systemctl restart local-models-sync && \
  ls /var/lib/local-models/flashnext-fp8/ | grep -c safetensors   # still 131
```

Step 5 is the one that closes the incident. Before this patch, that command
deleted the checkpoint.

### What the first sync does to the node copies

The prune sweep also removes *stray files inside kept artifacts* — anything
under the artifact directory that the manifest does not name. On the first run
after this patch that means `README.md`, `LICENSE`, `.gitattributes`,
`.cache/huggingface/`, and `MANIFEST.sha256` (the digest manifest
`scripts/stage-weights.sh` writes) are deleted from the node copies. All are
expected, none are load-bearing for serving, and the Library keeps its own
copies. Re-running `scripts/stage-weights.sh` re-creates `MANIFEST.sha256`,
and the next sync deletes it again — that is the steady state, not a fault.
