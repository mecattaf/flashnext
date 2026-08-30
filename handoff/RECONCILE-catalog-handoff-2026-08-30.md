# RECONCILE — catalog-handoff

Stateless re-attempt of spec-build task `catalog-handoff` (revision
`sha256:b422114131625cfa85fb29e95745d104d38143f274e5c720687a8e586312d1d7`).
Verification run 2026-08-31 on `coordinator`.

**HEAD at verification:** `2704ab84d8ffa057ad8d8916b5fade118bd7c223`
(branch `tally-work/flashnext-3195d36881a5/catalog-handoff`).

The lane's deliverables were already present, landed by
`377ba26 catalog-handoff: Prepare the fleet catalog patch for the morning operator`
under the earlier revision `sha256:7f3033db…`, which is an ancestor of the
witnessed base. This attempt re-measured them rather than re-authoring them.

## Acceptance

```
$ bash -euc 'git apply --stat handoff/catalog-row.patch >/dev/null \
             && test -s handoff/README.md'
$ echo $?
0
```

`git apply --stat handoff/catalog-row.patch` parses as:

```
 lib/local-models.nix |  862 ++++++++++++++++++++++++++++++++++++++++++++++++++
 modules/strix.nix    |   13 +
 2 files changed, 874 insertions(+), 1 deletion(-)
```

Artifacts under verification:

| file | bytes | sha256 |
|---|---|---|
| `handoff/catalog-row.patch` | 51108 | `ed25c385c07f2d3bdca0ad686bf42e87e6fe8aa92092c62f7031f6df14eb82f2` |
| `handoff/README.md` | 8925 at verification; extended by this commit | — |

## Facts re-measured, not re-asserted

The task revision asks for per-file byte counts read from the staged copy at
`/var/lib/local-models/flashnext-fp8`. When the patch was authored, node-side
staging had not finished, so its numbers were read from the Library mount.
Staging has since completed, so the patch was checked against the source the
revision names:

- **141 files** in the staged copy; **141** `files` entries in the patch row;
  set difference in both directions is **empty**.
- **Zero** per-file `bytes` mismatches across all 141 entries.
- Totals agree exactly: **185,563,713,216 bytes** staged, 185,563,713,216
  declared, **131 shards**.
- All 141 `hash` fields are the correct base64 SRI of their own `oid`
  (0 mismatches).
- `sha256sum` recomputed on the 10 non-LFS files plus shards 00001 and 00131:
  **12/12 match** the declared `oid`.

Nothing in the patch needed correcting.

## State the morning operator inherits

Observed on the fleet at verification time, and folded into `README.md`:

- The row **is already applied upstream.** `mecattaf/dotfiles` `161fafb5`
  (PR #265, carrying #260) landed the patch on 2026-08-30 19:02; the current
  dotfiles HEAD is `5d47acde`. `git apply --check` of this patch against that
  tree now fails with `modules/strix.nix: patch does not apply`, because the
  change is present, not because it is wrong.
- The row **is deployed and working.** `/etc/local-models/wanted.json` on the
  coordinator names `flashnext-fp8`; both projections exist; the staged copy
  is intact at 141 files after the rebuilds the SSD transition performed.
  That is the anti-prune doing its job.
- **Step 0 is still open.** The Library directory is still
  `/mnt/nas/models/weights/qwen38-flash-next-fp8`; no `flashnext-fp8`
  directory exists there. `library-fetch` addresses the Library as
  `weights/<artifactId>/`, so until that rename happens the 02:30 timer can
  re-download ~173 GiB the NAS already holds.

The patch was prepared, and never applied, by this campaign.
