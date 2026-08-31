#!/usr/bin/env python3
"""Validate every overnight receipt under results/receipts/.

A receipt is one JSON object: {"step": str, "status": "pass"|"fail",
"ts": str, "data": {...}}. Missing receipts are legal (the step has not
run); a present receipt must be well-formed, and hard bounds are enforced
per step. Any violation exits 2 — this is the ladder gate that makes the
overnight claims graded rather than narrated.

Receipts are read from TWO roots: the in-repo results/receipts/ and the
durable $FN_STATE_DIR/receipts/ that outlives a discarded lane worktree
(RUN3-BRIEF §18.3). A receipt that survived only in the durable location
still counts; when both roots carry the same file name the in-repo copy wins,
and the pair is graded once.

Missing receipts staying legal is why this gate could not tell "nothing ran"
from "everything passed" — it exits 0 on "3 receipts checked, 0 violations"
with 13 receipts pre-declared (§2.1 defect 3). `--require step,step` (alias
`--expect-set`) turns that off for one invocation: every named step must have
a receipt in the graded set, or the gate exits 2. It is absent by default, so
the existing gate argv keeps passing unchanged.
"""

import argparse
import json
import os
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
    if d.get("spec_on_failed"):
        # Degraded single-arm receipt: the spec-on serve failed and the
        # matrix honestly ran spec_off only. Counterbalancing protects
        # two-arm comparisons; with one arm there is no comparison to
        # protect — but the receipt must SAY it is single-arm.
        if d.get("arms") != ["spec_off"]:
            return f"spec_on_failed receipt must carry arms=['spec_off'], got {d.get('arms')}"
        return None
    if not d.get("counterbalanced", False):
        return "arms were not counterbalanced"
    return None

def _context(d):
    ratio = d.get("decode_ratio_vs_short_context")
    if ratio is not None and ratio < 0.9:
        return f"full-context decode ratio {ratio} below the 0.9 bound"
    return None

def _usb4stream(d):
    """The stream-primitive first-light receipt (bench/usb4stream-bench.py).

    This receipt is EVIDENCE, not a campaign claim: its status is always
    'pass' and the truth is in data.outcome, which is one of

      ok                     the bench ran; the schedule is fully present
      skipped:REASON         a precondition refused the run before any
                             device access (serve-up-on-shared-cable,
                             rail-peer-unreachable, configfs-group-missing)
      aborted:PHASE:ERRNO    the single open or a phase after it failed; the
                             bench closed, reaped, and typed it here rather
                             than reopening

    Skips and aborts PASS — they are the wedge-safe design working — but they
    must carry their reason text, so an empty type cannot pose as one.
    """
    outcome = d.get("outcome")
    if not isinstance(outcome, str) or not outcome:
        return "usb4stream receipt carries no data.outcome"
    if "serve_up" not in d:
        return "usb4stream receipt does not record data.serve_up"
    for prefix in ("skipped:", "aborted:"):
        if outcome.startswith(prefix):
            reason = outcome[len(prefix):].strip()
            if not reason:
                return f"usb4stream outcome '{outcome}' carries no reason text"
            return None
    if outcome != "ok":
        return f"usb4stream outcome '{outcome}' is not one of ok/skipped:/aborted:"
    opens = d.get("open_count")
    if isinstance(opens, dict):
        if not opens or any(v != 1 for v in opens.values()):
            return f"usb4stream open_count {opens} is not exactly 1 per side"
    elif opens != 1:
        return f"usb4stream open_count {opens} is not exactly 1"
    for key in ("rtt_us", "exchange_us", "throughput_mb_s", "device"):
        if not d.get(key):
            return f"usb4stream 'ok' receipt is missing {key}"
    for size in (64, 4096, 16384, 65536):
        if str(size) not in (d.get("rtt_us") or {}):
            return f"usb4stream 'ok' receipt has no rtt entry at {size} bytes"
    for size in (8192, 16384, 65536):
        if str(size) not in (d.get("exchange_us") or {}):
            return f"usb4stream 'ok' receipt has no exchange entry at {size} bytes"
    for stat in (d.get("rtt_us") or {}), (d.get("exchange_us") or {}):
        for size, cell in stat.items():
            for field in ("p50", "p99"):
                if (cell or {}).get(field) is None:
                    return f"usb4stream size {size} is missing {field}"
    tput = d.get("throughput_mb_s") or {}
    if len(tput) < 2 or any(v is None for v in tput.values()):
        return f"usb4stream throughput {tput} does not cover both directions"
    for key in ("ring_size", "throttling"):
        if key not in d:
            return f"usb4stream 'ok' receipt does not record {key} from configfs"
    if not (d.get("device") or {}).get("coordinator") \
            or not (d.get("device") or {}).get("peer"):
        return "usb4stream 'ok' receipt does not resolve a device on both ends"
    if d.get("loop") != "python":
        return "usb4stream 'ok' receipt does not state its measurement loop"
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
    "usb4stream": _usb4stream,
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


def _durable_receipts() -> pathlib.Path | None:
    """$FN_STATE_DIR/receipts — the copy that outlives the lane worktree.

    FN_STATE_DIR is bind-mounted into the containers at the same absolute path
    on both nodes (host/fn-cluster-up.sh:83,98), so a receipt written there
    from inside the serve container is still on disk after the worktree that
    ran the checkpoint is discarded.
    """
    state = os.environ.get("FN_STATE_DIR")
    return pathlib.Path(state) / "receipts" if state else None


def _receipt_dirs() -> list[pathlib.Path]:
    """Both graded receipt roots, in precedence order (in-repo first)."""
    return [d for d in (RECEIPTS, _durable_receipts()) if d is not None]


def _graded():
    """file name -> path across both roots; the in-repo copy wins a tie."""
    found = {}
    for base in _receipt_dirs():
        if not base.is_dir():
            continue
        for path in sorted(base.glob("*.json")):
            found.setdefault(path.name, path)
    return found


def _quarantined():
    """file name -> path for failed/ receipts across both roots (D12)."""
    found = {}
    for base in _receipt_dirs():
        failed_dir = base / "failed"
        if not failed_dir.is_dir():
            continue
        for path in sorted(failed_dir.glob("*.json")):
            found.setdefault(path.name, path)
    return found


def _durable(path) -> bool:
    return RECEIPTS not in (path.parent, path.parent.parent)


def _label(path) -> str:
    return f"{path.name} (durable)" if _durable(path) else path.name


def _names(path, receipt) -> set:
    """The names a receipt answers to: its file stem and its declared step."""
    names = {path.stem}
    step = (receipt or {}).get("step")
    if isinstance(step, str) and step:
        names.add(step)
    return names


def _read(path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def _expected(required, present, quarantined) -> int:
    """Report each required step with no receipt in the graded set.

    Defaulted OFF (an empty `required`), which is what keeps the plain gate
    argv meaning exactly what it meant before: missing receipts stay legal.
    """
    missing = 0
    for step in required:
        if step in present:
            continue
        missing += 1
        if step in quarantined:
            print(f"receipts-verify: required receipt '{step}' is absent from "
                  f"the graded set — it ran and FAILED, see failed/")
        else:
            print(f"receipts-verify: required receipt '{step}' is absent — "
                  f"the step did not run")
    if required:
        print(f"receipts-verify: expected set: {len(required) - missing}/"
              f"{len(required)} present")
    return missing


def _parse_args(argv):
    parser = argparse.ArgumentParser(
        description="Grade the overnight receipts; optionally assert a set.")
    parser.add_argument(
        "--require", "--expect-set", action="append", default=[],
        metavar="STEP[,STEP...]",
        help="steps that MUST have a receipt; repeatable and comma-separated. "
             "Absent by default, so the existing gate argv is unchanged.")
    args = parser.parse_args(argv)
    seen, required = set(), []
    for chunk in args.require:
        for name in chunk.split(","):
            name = name.strip()
            if name and name not in seen:
                seen.add(name)
                required.append(name)
    args.require = required
    return args


def main(argv=None) -> int:
    args = _parse_args(argv)
    bad = 0
    err = _handoff_patch_ok()
    if err:
        print(f"receipts-verify: {err}")
        bad += 1
    if not any(d.is_dir() for d in _receipt_dirs()):
        print("receipts-verify: no receipts directory; nothing ran")
        bad += _expected(args.require, set(), set())
        return 2 if bad else 0
    seen = 0
    present = set()
    for path in _graded().values():
        seen += 1
        try:
            r = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError) as e:
            print(f"receipts-verify: {_label(path)}: invalid JSON: {e}")
            bad += 1
            continue
        present |= _names(path, r)
        for key in ("step", "status", "ts"):
            if key not in r:
                print(f"receipts-verify: {_label(path)}: missing '{key}'")
                bad += 1
        if r.get("status") == "fail":
            print(f"receipts-verify: {_label(path)}: "
                  f"step '{r.get('step')}' FAILED")
            bad += 1
        for prefix, check in BOUNDS.items():
            if str(r.get("step", "")).startswith(prefix):
                err = check(r.get("data") or {})
                if err:
                    print(f"receipts-verify: {_label(path)}: {err}")
                    bad += 1
    # Quarantined failure receipts (results/receipts/failed/, and its durable
    # twin) are typed blockers the morning operator reviews first — listed
    # LOUDLY here but never counted as violations: one graded failure must
    # cost one step, never redden every later gate run. The globs above are
    # non-recursive, so these never enter the grading walk. They also do not
    # satisfy a --require name: the step ran and failed, which is not the
    # same claim as "the step produced a clean receipt".
    quarantined = set()
    for path in _quarantined().values():
        print(f"receipts-verify: WARN: quarantined failure receipt "
              f"failed/{_label(path)} — typed blocker, see docs/MORNING.md")
        quarantined |= _names(path, _read(path))
    bad += _expected(args.require, present, quarantined)
    print(f"receipts-verify: {seen} receipts checked, {bad} violations")
    return 2 if bad else 0

if __name__ == "__main__":
    sys.exit(main())
