#!/usr/bin/env python3
"""Restore tracked result JSONs identical modulo ts, and make receipts durable.

The campaign driver re-runs checkpoint commands as pure validators: a re-run
over an already-correct base must leave every tracked file byte-identical, or
the attempt fails with "checkpoint command changed tracked files instead of
validating the prepared base". Receipt writers stamp a fresh ts on every run,
so an unchanged measurement still dirties the tree. This guard restores any
MODIFIED tracked JSON under results/ whose content equals HEAD's modulo the
ts field — the same contract cp-build's checkpoint command applies inline to
build.json, generalized. Genuine drift (shards, bytes, status, data) is left
in place and surfaces loudly. New (untracked) receipts are never touched, so
first-run receipts and failed/ quarantine records (D12) still land.

They land in a directory about to be deleted, which is the OTHER half
(RUN3-BRIEF §18.3). A checkpoint task is contractually forbidden from carrying
conflictDomains, so it owns no path and nothing it writes is ever committed;
purity is checked with --untracked-files=no, so its untracked receipt is
perfectly legal and simply dies with the lane worktree. That is why two green
nights banked no engine evidence. So every plain run of this script ALSO
mirrors results/receipts/ — and its failed/ quarantine — into
$FN_STATE_DIR/receipts/, an absolute path bind-mounted into the containers at
the same location on both nodes (host/fn-cluster-up.sh:83,98), which costs
nothing and works from inside the serve container. The mirror is idempotent —
a durable copy equal modulo ts is left byte-identical, never re-stamped — and
it can never fail the step: every error is reported on stderr and the exit
status stays 0.

A writer that can call this script directly should use --write instead, which
places the DURABLE copy FIRST and the in-repo copy second, so a crash between
the two loses only the disposable one:

    scripts/receipt-restore.py --write receipt.json [ROOT]
    ... | scripts/receipt-restore.py --write - [ROOT]

Both copies are the writer's own bytes, both are skipped when an equal-modulo-ts
receipt is already there (purity), and a status=fail receipt lands under the
failed/ quarantine in both locations (D12).
"""
import argparse
import json
import os
import pathlib
import subprocess
import sys


def strip_ts(obj):
    if isinstance(obj, dict):
        return {k: strip_ts(v) for k, v in obj.items() if k != "ts"}
    if isinstance(obj, list):
        return [strip_ts(v) for v in obj]
    return obj


def durable_dir():
    """$FN_STATE_DIR/receipts, or None when the state dir is not declared."""
    state = os.environ.get("FN_STATE_DIR")
    return pathlib.Path(state) / "receipts" if state else None


def _place(path, receipt, text):
    """Write `text` to `path` unless an equal-modulo-ts receipt is there.

    Leaving an unchanged measurement alone is what keeps a re-run
    byte-identical; a fresh ts on identical facts is not a change.
    """
    if path.is_file():
        try:
            with open(path) as fh:
                if strip_ts(json.load(fh)) == strip_ts(receipt):
                    return False
        except (OSError, ValueError):
            pass  # unreadable or unparseable: overwrite it with the truth
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return True


def _relative(receipt, name=None):
    """Where a receipt belongs under a receipts root, quarantine included."""
    step = name or receipt.get("step")
    if not isinstance(step, str) or not step:
        return None
    if receipt.get("status") == "fail":
        return pathlib.Path("failed") / f"{step}.json"
    return pathlib.Path(f"{step}.json")


def restore(root):
    diff = subprocess.run(
        ["git", "-C", root, "diff", "--name-only", "--", "results/"],
        capture_output=True, text=True, check=True).stdout
    for rel in filter(None, diff.splitlines()):
        if not rel.endswith(".json"):
            continue
        try:
            head = json.loads(subprocess.run(
                ["git", "-C", root, "show", f"HEAD:{rel}"],
                capture_output=True, text=True, check=True).stdout)
            with open(f"{root}/{rel}") as fh:
                cur = json.load(fh)
        except (subprocess.CalledProcessError, ValueError, OSError):
            continue  # new, unparseable, or unreadable: leave for the driver
        if strip_ts(head) == strip_ts(cur):
            subprocess.run(["git", "-C", root, "checkout", "--", rel],
                           check=True)
            print(f"receipt-restore: {rel} unchanged modulo ts; restored",
                  file=sys.stderr)


def mirror(root):
    """Copy results/receipts/** to $FN_STATE_DIR/receipts/**. Never raises."""
    dest_root = durable_dir()
    if dest_root is None:
        print("receipt-restore: FN_STATE_DIR is unset; receipts will not "
              "survive this worktree", file=sys.stderr)
        return 0
    src_root = pathlib.Path(root) / "results" / "receipts"
    copied = 0
    for src in sorted(src_root.glob("*.json")) \
            + sorted((src_root / "failed").glob("*.json")):
        rel = src.relative_to(src_root)
        try:
            text = src.read_text()
            receipt = json.loads(text)
        except (OSError, ValueError):
            continue  # unreadable or unparseable: the gate will say so
        try:
            if _place(dest_root / rel, receipt, text):
                copied += 1
                print(f"receipt-restore: {rel} mirrored to {dest_root}",
                      file=sys.stderr)
        except OSError as e:
            print(f"receipt-restore: durable mirror of {rel} failed: {e}; "
                  f"the step is unaffected", file=sys.stderr)
    return copied


def write(root, source, name=None):
    """Durable copy first, in-repo copy second (§18.3)."""
    text = sys.stdin.read() if source == "-" \
        else pathlib.Path(source).read_text()
    receipt = json.loads(text)
    rel = _relative(receipt, name)
    if rel is None:
        print("receipt-restore: receipt declares no 'step' and no --name was "
              "given; refusing to guess", file=sys.stderr)
        return 2
    dest_root = durable_dir()
    if dest_root is None:
        print("receipt-restore: FN_STATE_DIR is unset; this receipt will not "
              "survive this worktree", file=sys.stderr)
    else:
        try:
            _place(dest_root / rel, receipt, text)
        except OSError as e:
            print(f"receipt-restore: durable write of {rel} failed: {e}; "
                  f"the step is unaffected", file=sys.stderr)
    _place(pathlib.Path(root) / "results" / "receipts" / rel, receipt, text)
    print(f"receipt-restore: wrote {rel}", file=sys.stderr)
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Keep receipts pure on re-run and durable past the lane.")
    parser.add_argument("root", nargs="?", default=".",
                        help="repository root (default: the cwd)")
    parser.add_argument("--write", metavar="FILE",
                        help="write this receipt JSON ('-' for stdin) to the "
                             "durable location first, then into the repo")
    parser.add_argument("--name", metavar="STEP",
                        help="receipt file stem, when it is not the step name")
    args = parser.parse_args(argv)
    if args.write:
        return write(args.root, args.write, args.name)
    restore(args.root)
    mirror(args.root)
    return 0


if __name__ == "__main__":
    sys.exit(main())
