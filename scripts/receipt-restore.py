#!/usr/bin/env python3
"""Restore tracked result JSONs whose re-run content is identical modulo ts.

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
"""
import json
import subprocess
import sys


def strip_ts(obj):
    if isinstance(obj, dict):
        return {k: strip_ts(v) for k, v in obj.items() if k != "ts"}
    if isinstance(obj, list):
        return [strip_ts(v) for v in obj]
    return obj


def main():
    root = sys.argv[1] if len(sys.argv) > 1 else "."
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


if __name__ == "__main__":
    main()
