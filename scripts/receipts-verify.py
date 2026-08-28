#!/usr/bin/env python3
"""Validate every overnight receipt under results/receipts/.

A receipt is one JSON object: {"step": str, "status": "pass"|"fail",
"ts": str, "data": {...}}. Missing receipts are legal (the step has not
run); a present receipt must be well-formed, and hard bounds are enforced
per step. Any violation exits 2 — this is the ladder gate that makes the
overnight claims graded rather than narrated.
"""

import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
RECEIPTS = ROOT / "results" / "receipts"

# Per-step hard bounds: step-name prefix -> checker(data) -> error or None.
def _residency(d):
    for rank, gib in (d.get("gtt_gib_per_rank") or {}).items():
        if gib > 80:
            return f"rank {rank} GTT {gib} GiB exceeds the 80 GiB bound"
    if d.get("table_gpu_resident_bytes", 0) != 0:
        return "table has GPU-resident bytes; the mmap path is not engaged"
    if not d.get("read_after_warmed_decode", False):
        return "residency was not read after a warmed decode"
    return None

def _tp2(d):
    if not d.get("byte_identical_repeat", False):
        return "greedy repeat was not byte-identical"
    return None

def _weights(d):
    if d.get("shards", 0) < 131:
        return f"only {d.get('shards')} shards staged"
    return None

def _bench(d):
    if d.get("loads_per_arm", 0) < 3:
        return "fewer than 3 loads per arm"
    if not d.get("counterbalanced", False):
        return "arms were not counterbalanced"
    return None

def _context(d):
    ratio = d.get("decode_ratio_vs_short_context")
    if ratio is not None and ratio < 0.9:
        return f"full-context decode ratio {ratio} below the 0.9 bound"
    return None

def _smoke(d):
    ap = (d.get("aperture") or {}).get("ttm_pages_limit")
    if ap is not None and ap != 33554432:
        return f"aperture {ap} diverges from the 33554432-page ceiling"
    return None

BOUNDS = {
    "residency": _residency,
    "tp2": _tp2,
    "weights": _weights,
    "bench": _bench,
    "context": _context,
    "smoke": _smoke,
}

def _handoff_patch_ok() -> str | None:
    """When the catalog patch exists it must parse as a git patch."""
    import subprocess
    patch = ROOT / "handoff" / "catalog-row.patch"
    if not patch.is_file():
        return None
    proc = subprocess.run(["git", "apply", "--stat", str(patch)],
                          capture_output=True, text=True)
    if proc.returncode != 0:
        return f"handoff/catalog-row.patch does not parse: {proc.stderr.strip()}"
    return None


def main() -> int:
    bad = 0
    err = _handoff_patch_ok()
    if err:
        print(f"receipts-verify: {err}")
        bad += 1
    if not RECEIPTS.is_dir():
        print("receipts-verify: no receipts directory; nothing ran")
        return 2 if bad else 0
    seen = 0
    for path in sorted(RECEIPTS.glob("*.json")):
        seen += 1
        try:
            r = json.loads(path.read_text())
        except json.JSONDecodeError as e:
            print(f"receipts-verify: {path.name}: invalid JSON: {e}")
            bad += 1
            continue
        for key in ("step", "status", "ts"):
            if key not in r:
                print(f"receipts-verify: {path.name}: missing '{key}'")
                bad += 1
        if r.get("status") == "fail":
            print(f"receipts-verify: {path.name}: step '{r.get('step')}' FAILED")
            bad += 1
        for prefix, check in BOUNDS.items():
            if str(r.get("step", "")).startswith(prefix):
                err = check(r.get("data") or {})
                if err:
                    print(f"receipts-verify: {path.name}: {err}")
                    bad += 1
    print(f"receipts-verify: {seen} receipts checked, {bad} violations")
    return 2 if bad else 0

if __name__ == "__main__":
    sys.exit(main())
