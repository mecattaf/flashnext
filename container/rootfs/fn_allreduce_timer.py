# SPDX-License-Identifier: Apache-2.0
# Original to flashnext. Its three neighbours in this overlay
# (fn_synctrace.py, fn_offload_batch.py, fn_expert_union.py) are adapted from
# AlexKGwyn/ds4-vllm @ a8f620d, Apache License 2.0; this one has no upstream
# ancestor to cite. The reference estate measured a custom transport's
# end-to-end latency (tbv_ar2, the 105 us bar); it never measured the STOCK
# collective's own split, which is the number this campaign is missing.
"""FN_ALLREDUCE_TIMER=1: split every TP all-reduce three ways, per layer type.

Why this exists
---------------
RUN3-BRIEF section 10.1: without this instrument the A/B criterion "the bench
shows decode is allreduce-dominated" CANNOT BE EVALUATED BY ANY ARTIFACT IN
THIS REPO. `results/bench/` gives tokens/s per arm and fn_synctrace gives
blocking-sync call sites; neither can say what fraction of a decode step is
the collective. A faster transport buys proportionally nothing if decode is
not allreduce-bound (docs/USB4STREAM-TRANSPORT.md:118-120), so the whole
attended follow-up hangs off a number nothing here produces.

It logs the three things section 13.7 asks for, and the first two are what
separate "the custom path was inert" from "the custom path was slow" -- the
exact failure mode of section 13.1, where an arm measures nothing and reads
like a null result:

(a) A fired/declined counter for any custom all-reduce path. If a custom
    path is configured but never eligible, every "custom transport" number is
    really a stock-transport number wearing its name.
(b) `torch.cuda.is_current_stream_capturing()` sampled AT THE CALL SITE. Not
    at install, not once per step -- at the call. This is not hypothetical
    here: the reference fast path's own eligibility check includes
    `not is_current_stream_capturing()` and the reference ran --enforce-eager,
    while our serve cannot (the fork's cudagraph-safety guard refuses eager
    under VLLM_PLE_MMAP=1, so we run compiled PIECEWISE). Inside captured
    decode segments a custom path silently never fires
    (docs/DECISIONS-2026-08-30.md, section 2.6).
(c) Staging time split from wire time. Without the split, the
    write-combining staging trap of section 13.6 is invisible: a path whose
    host-side staging costs more than the wire looks like a slow wire.

Bucketed by layer type, because the model is heterogeneous -- 48 layers with
`full_attention_interval: 4`, i.e. 12 QSA full-attention layers and 36 GDN
layers -- and an aggregate hides which one pays.

The three reads (section 14.15 step 1)
--------------------------------------
Per collective, three `perf_counter` reads and nothing else:

    t0  wrapper entry
    t1  after the call-site probe (custom-path eligibility + capture state)
    t2  after the underlying all_reduce returns

    detect  = t1 - t0        our probe plus the path decision at the call site
    exchange= t2 - t1        the collective call itself
    compute = t0 - t2(prev)  everything between two collectives: own-GPU work

That last column is the point. The equivalent measurement on the only
production gfx1151 two-node TP deployment OVERTURNED ITS AUTHORS' OWN LEADING
HYPOTHESIS -- they were certain the gate lockstep dominated, and it was 11%
transport against 88% own-GPU compute (section 14.1). If our split resembles
theirs we learn it in 30 minutes instead of spending the night on transport.

What this does NOT measure (read this before quoting a number)
--------------------------------------------------------------
`perf_counter` on the host, with NO device sync inserted anywhere. A sync in
the decode loop changes the thing being measured, and section 14.6 records a
documented non-deterministic stall from exactly that mistake. So `exchange`
is the HOST-VISIBLE cost of the collective call and `compute` is the
host-visible gap between two of them. On a host-blocking transport (RCCL over
TCP sockets, and any staging path that copies through the host) that is close
to the real split; on a fully device-async path the host returns after enqueue
and the split is enqueue-side, with the real wire cost pushed into a later
column. The receipt states this in `data.measures` so no reader can quote the
number without the caveat. It is still decisive for the question actually
asked: an exchange share near zero on the stock arm falsifies
"allreduce-dominated" regardless of where the async boundary sits.

Discipline
----------
* OFF unless FN_ALLREDUCE_TIMER=1. When off, nothing is wrapped, so the cost
  is not "small", it is absent.
* It must never raise into the serve path: every read is wrapped, and every
  failure degrades to not recording. The collective's result and exceptions
  pass through untouched.
* Its summary lands as a campaign receipt (step "allreduce") so cp-bench and
  scripts/receipts-verify.py read it the same way they read every other step.
* No device sync, no D2H copy, no tensor inspection that would force either.

Env:
    FN_ALLREDUCE_TIMER=1      enable (default: off)
    FN_AR_START=200           skip this many collectives first (warmup+prefill)
    FN_AR_CALLS=20000         window size in collectives (~200 decode steps)
    FN_AR_OUT=<dir>           receipt directory (default: $FN_STATE_DIR/receipts,
                              else ~/vllm-prof)
    FN_AR_FULL_ATTENTION_INTERVAL=4
                              every Nth layer is full attention (QSA); the
                              others are GDN. Only used when the layer object
                              does not declare its own layer_type.
    FN_AR_FRAME_DEPTH=12      how far up the python stack to look for the layer

Install is lazy, like fn_expert_union: the caller drives install() once the
engine is up, install() re-checks the env, and a stale call stays a no-op.
"""

import atexit
import json
import os
import re
import sys
import time
from time import perf_counter

try:  # torch is absent on the host that runs the offline tests
    import torch
except Exception:  # pragma: no cover - exercised only where torch is missing
    torch = None

__all__ = [
    "ENABLE_ENV", "START_ENV", "CALLS_ENV", "OUT_ENV", "INTERVAL_ENV",
    "DEPTH_ENV", "QSA", "GDN", "UNKNOWN", "STEP",
    "classify_layer", "reload_config", "install", "uninstall", "wrap",
    "reset", "summary", "report", "flush", "receipt_path",
]

ENABLE_ENV = "FN_ALLREDUCE_TIMER"
START_ENV = "FN_AR_START"
CALLS_ENV = "FN_AR_CALLS"
OUT_ENV = "FN_AR_OUT"
INTERVAL_ENV = "FN_AR_FULL_ATTENTION_INTERVAL"
DEPTH_ENV = "FN_AR_FRAME_DEPTH"

# Layer-type buckets. UNKNOWN is not a failure mode to hide: a collective the
# walk cannot attribute (PLE lookups, the trunk, anything called from C++)
# belongs in its own column rather than smeared across the two real ones.
QSA = "qsa"
GDN = "gdn"
UNKNOWN = "unknown"
BUCKETS = (QSA, GDN, UNKNOWN)

# Receipt step name; scripts/receipts-verify.py grades it on shape alone.
STEP = "allreduce"

# Sub-communicators that carry a custom all-reduce path in vLLM's device
# communicator. Order is dispatch order: the first eligible one wins.
CUSTOM_ATTRS = ("ca_comm", "qr_comm", "symm_mem_comm")

_LAYER_RE = re.compile(r"(?:^|\.)(?:layers|blocks)\.(\d+)(?:\.|$)")
_CACHE_MAX = 8192

ENABLED = False
START = 200
CALLS = 20000
OUT = None
INTERVAL = 4
DEPTH = 12


def _int_env(name, default):
    try:
        return int(os.environ[name])
    except (KeyError, TypeError, ValueError):
        return default


def _default_out():
    """$FN_STATE_DIR/receipts when declared -- the copy that outlives the lane
    worktree (RUN3-BRIEF section 18.3) and is bind-mounted into the serve
    container at the same absolute path on both nodes."""
    state = os.environ.get("FN_STATE_DIR")
    if state:
        return os.path.join(state, "receipts")
    return os.path.expanduser("~/vllm-prof")


def reload_config():
    """Re-read the env surface. Called at import; public for the driver."""
    global ENABLED, START, CALLS, OUT, INTERVAL, DEPTH
    ENABLED = os.environ.get(ENABLE_ENV) == "1"
    START = max(0, _int_env(START_ENV, 200))
    CALLS = max(0, _int_env(CALLS_ENV, 20000))
    OUT = os.path.expanduser(os.environ.get(OUT_ENV) or _default_out())
    INTERVAL = _int_env(INTERVAL_ENV, 4)
    DEPTH = max(1, _int_env(DEPTH_ENV, 12))


reload_config()

_rows = []          # (kind, detect_s, exchange_s, compute_s, fired, capturing)
_n = 0              # collectives seen, including the skipped warmup
_last_exit = None   # perf_counter at the previous collective's return
_done = False       # window closed: the wrapper degrades to a pass-through
_installed = []     # [(owner, name, original), ...] for uninstall()
_site_cache = {}    # id(obj) -> (obj, kind); the obj ref keeps the id unique
_candidates = []    # custom sub-communicators seen at wrap time


def classify_layer(idx, interval=None):
    """Layer index -> bucket, from `full_attention_interval`.

    Qwen3-Next's `layer_types` runs three linear-attention (GDN) layers then
    one full-attention (QSA) layer, so layer i is QSA when (i + 1) % 4 == 0:
    48 layers -> 12 QSA, 36 GDN, which is the split RUN3-BRIEF states.
    """
    n = INTERVAL if interval is None else interval
    if not isinstance(idx, int) or idx < 0 or not isinstance(n, int) or n <= 0:
        return UNKNOWN
    return QSA if (idx + 1) % n == 0 else GDN


def _classify_obj(obj):
    """Bucket for one candidate frame object, or None if it is not a layer."""
    declared = getattr(obj, "layer_type", None)
    if isinstance(declared, str) and declared:
        low = declared.lower()
        if "linear" in low or "gdn" in low or "mamba" in low:
            return GDN
        if "full" in low or "qsa" in low:
            return QSA
    for attr in ("prefix", "layer_name"):
        name = getattr(obj, attr, None)
        if isinstance(name, str):
            m = _LAYER_RE.search(name)
            if m:
                return classify_layer(int(m.group(1)))
    return None


def _site_kind(frame):
    """Walk up from the call site for the layer that owns this collective.

    Bounded by DEPTH frames and memoised per object, so the steady-state cost
    is a handful of dict lookups. Host metadata only -- no tensor is touched,
    so nothing here can force a device sync.
    """
    try:
        return _walk(frame)
    except Exception:
        return UNKNOWN


def _walk(frame):
    for _ in range(DEPTH):
        if frame is None:
            break
        obj = frame.f_locals.get("self")
        if obj is not None:
            key = id(obj)
            hit = _site_cache.get(key)
            if hit is not None:
                if hit[1]:
                    return hit[1]
            else:
                kind = _classify_obj(obj)
                if len(_site_cache) >= _CACHE_MAX:
                    _site_cache.clear()
                _site_cache[key] = (obj, kind)
                if kind:
                    return kind
        frame = frame.f_back
    return UNKNOWN


def _custom_fired(comm, tensor):
    """Tri-state: True fired, False declined, None not knowable.

    Asks each custom sub-communicator the same question the dispatch asks --
    `should_custom_ar(tensor)` -- which is a size/alignment check on host
    metadata. A sub-communicator that is present but `disabled` is a decline,
    and that decline is the whole point: it is how "the custom path was inert"
    stops looking like "the custom path was slow".
    """
    seen = False
    try:
        for name in CUSTOM_ATTRS:
            sub = getattr(comm, name, None)
            if sub is None:
                continue
            seen = True
            if getattr(sub, "disabled", False):
                continue
            should = getattr(sub, "should_custom_ar", None)
            if should is None:
                continue
            if should(tensor):
                return True
    except Exception:
        return None
    return False if seen else None


def _capturing():
    """Capture state sampled AT THE CALL SITE. None when unknowable."""
    if torch is None:
        return None
    try:
        return bool(torch.cuda.is_current_stream_capturing())
    except Exception:
        return None


def reset():
    """Drop everything recorded and reopen the window."""
    global _n, _last_exit, _done
    _rows.clear()
    _site_cache.clear()
    _n = 0
    _last_exit = None
    _done = False


def _record(kind, detect, exchange, compute, fired, capturing):
    global _done
    _rows.append((kind, detect, exchange, compute, fired, capturing))
    if len(_rows) >= CALLS:
        _done = True
        flush()


def wrap(comm, name="all_reduce"):
    """Wrap one communicator's all_reduce in place. Returns True if wrapped.

    Wrapping the INSTANCE, not the class: two process groups in one worker
    stay independently switchable, and uninstall() is an attribute delete.
    """
    global _candidates
    orig = getattr(comm, name, None)
    if orig is None or getattr(orig, "_fn_ar_orig", None) is not None:
        return False

    def wrapped(input_, *a, **kw):
        # The window is closed: pay one attribute test and get out of the way.
        if _done:
            return orig(input_, *a, **kw)
        global _n, _last_exit
        t0 = perf_counter()
        fired = capturing = None
        try:
            _n += 1
            recording = _n > START
            if recording:
                fired = _custom_fired(comm, input_)
                capturing = _capturing()
                kind = _site_kind(sys._getframe(1))
        except Exception:
            recording = False  # a probe must never take the engine down
        t1 = perf_counter()
        out = orig(input_, *a, **kw)
        t2 = perf_counter()
        try:
            # The cursor advances first: a recorder that fails must not make
            # the NEXT collective's compute gap span two collectives.
            prev, _last_exit = _last_exit, t2
            if recording:
                _record(kind, t1 - t0, t2 - t1,
                        None if prev is None else t0 - prev, fired, capturing)
        except Exception:
            pass
        return out

    wrapped._fn_ar_orig = orig
    # Was the attribute the instance's own, or inherited from the class? The
    # answer decides whether uninstall() restores it or deletes the shadow.
    try:
        owned = name in vars(comm)
    except TypeError:
        owned = True
    try:
        setattr(comm, name, wrapped)
    except Exception as exc:  # __slots__, a read-only proxy, anything
        print(f"[fn_allreduce_timer] cannot wrap {name}: {exc}", flush=True)
        return False
    _installed.append((comm, name, orig, owned))
    _candidates = [n for n in CUSTOM_ATTRS if getattr(comm, n, None) is not None]
    return True


def install(comm=None):
    """Idempotently wrap the TP all-reduce. Safe to call more than once.

    With no argument it resolves vLLM's tensor-parallel device communicator --
    the object whose all_reduce carries the custom-path dispatch. If that
    object is not reachable it falls back to the group coordinator, which is
    the same collective one frame out.
    """
    if not ENABLED or _installed:
        return False
    if comm is None:
        try:
            from vllm.distributed import parallel_state as ps
            group = ps.get_tp_group()
        except Exception as exc:
            print(f"[fn_allreduce_timer] not installed: {exc}", flush=True)
            return False
        comm = getattr(group, "device_communicator", None) or group
    if not wrap(comm):
        print("[fn_allreduce_timer] not installed: no all_reduce to wrap",
              flush=True)
        return False
    atexit.register(flush)
    print(f"[fn_allreduce_timer] installed on {type(comm).__name__}: skip "
          f"{START} collectives, window {CALLS}, custom paths "
          f"{_candidates or 'none'}, out {OUT}", flush=True)
    return True


def uninstall():
    """Restore every wrapped attribute. Recorded rows are kept."""
    while _installed:
        comm, name, orig, owned = _installed.pop()
        try:
            if owned:
                setattr(comm, name, orig)
            else:
                delattr(comm, name)
        except Exception:
            pass


def _pct(values, q):
    if not values:
        return None
    i = int(round((len(values) - 1) * q))
    return values[i]


def _stats(values):
    """Microseconds, rounded. `values` is a list of seconds."""
    if not values:
        return {"n": 0, "sum": 0.0, "p50": None, "p99": None, "max": None}
    us = sorted(v * 1e6 for v in values)
    return {
        "n": len(us),
        "sum": round(sum(us), 3),
        "p50": round(_pct(us, 0.50), 3),
        "p99": round(_pct(us, 0.99), 3),
        "max": round(us[-1], 3),
    }


def _tally(flags):
    t = {"true": 0, "false": 0, "unknown": 0}
    for f in flags:
        t["true" if f is True else "false" if f is False else "unknown"] += 1
    return t


def summary():
    """The receipt body: the split, the counters, and the caveat."""
    rows = list(_rows)
    detect = [r[1] for r in rows]
    exchange = [r[2] for r in rows]
    compute = [r[3] for r in rows if r[3] is not None]
    totals = {
        "detect": round(sum(detect) * 1e6, 3),
        "exchange": round(sum(exchange) * 1e6, 3),
        "compute": round(sum(compute) * 1e6, 3),
    }
    grand = sum(totals.values())
    share = {k: (round(v / grand, 4) if grand else None)
             for k, v in totals.items()}
    by_layer = {}
    for kind in BUCKETS:
        sel = [r for r in rows if r[0] == kind]
        by_layer[kind] = {
            "collectives": len(sel),
            "detect_us": _stats([r[1] for r in sel]),
            "exchange_us": _stats([r[2] for r in sel]),
            "compute_us": _stats([r[3] for r in sel if r[3] is not None]),
            "custom_path": _tally([r[4] for r in sel]),
            "capturing": _tally([r[5] for r in sel]),
        }
    return {
        "measures": (
            "host-side perf_counter around the communicator call; no device "
            "sync is inserted, so exchange_us is the host-visible cost of the "
            "collective call and compute_us the host-visible gap between two "
            "of them (RUN3-BRIEF 14.6: a sync in the decode loop changes the "
            "thing being measured)"),
        "enabled": ENABLED,
        "window": {"skip": START, "calls": CALLS,
                   "seen": _n, "recorded": len(rows), "closed": _done},
        "layer_types": {"full_attention_interval": INTERVAL,
                        "qsa": QSA, "gdn": GDN},
        "custom_path": {"candidates": list(_candidates),
                        **_tally([r[4] for r in rows])},
        "capturing": _tally([r[5] for r in rows]),
        "totals_us": totals,
        "share": share,
        "by_layer_type": by_layer,
    }


def report():
    """Operator-facing table, in the shape fn_synctrace prints."""
    s = summary()
    rows = s["window"]["recorded"]
    if not rows:
        return ("### ALLREDUCE SPLIT ###\n  (no collectives recorded -- either "
                f"{ENABLE_ENV} was not 1, install() never ran, or the window "
                f"skip of {START} was never reached)\n\n")
    out = [f"### ALLREDUCE SPLIT ({rows} collectives, "
           f"{s['custom_path']['true']} custom-path fired / "
           f"{s['custom_path']['false']} declined) ###",
           "  bucket     n      detect us      exchange us     compute us"]
    for kind in BUCKETS:
        b = s["by_layer_type"][kind]
        if not b["collectives"]:
            continue
        out.append(
            f"  {kind:<9}{b['collectives']:>6}  "
            f"{b['detect_us']['p50'] or 0:8.1f} p50  "
            f"{b['exchange_us']['p50'] or 0:8.1f} p50  "
            f"{b['compute_us']['p50'] or 0:8.1f} p50")
    out.append(f"  share: detect {s['share']['detect']} / exchange "
               f"{s['share']['exchange']} / compute {s['share']['compute']}")
    out.append("")
    return "\n".join(out) + "\n\n"


def receipt_path(rank=None):
    """Where this worker's receipt lands. Rank 0 owns the canonical name."""
    if rank is None:
        rank = _rank()
    if rank in (None, 0):
        return os.path.join(OUT, f"{STEP}.json")
    return os.path.join(OUT, f"{STEP}-rank{rank}.json")


def _rank():
    try:
        if torch is not None and torch.distributed.is_initialized():
            return int(torch.distributed.get_rank())
    except Exception:
        pass
    try:
        return int(os.environ["RANK"])
    except (KeyError, TypeError, ValueError):
        return None


def _strip_ts(obj):
    """The receipt minus its timestamps -- receipt-restore's comparison."""
    if isinstance(obj, dict):
        return {k: _strip_ts(v) for k, v in obj.items() if k != "ts"}
    if isinstance(obj, list):
        return [_strip_ts(v) for v in obj]
    return obj


def flush(path=None):
    """Write the receipt. Never raises; returns the path, or None.

    A re-run over an identical measurement leaves the file byte-identical --
    a fresh timestamp on unchanged facts is not a change (campaign checkpoint
    purity), and this is the same equal-modulo-ts rule
    scripts/receipt-restore.py applies.
    """
    try:
        rank = _rank()
        data = summary()
        data["rank"] = rank
        receipt = {"step": STEP, "status": "pass",
                   "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                   "data": data}
        text = json.dumps(receipt, indent=1) + "\n"
        target = path or receipt_path(rank)
        if os.path.isfile(target):
            try:
                with open(target) as fh:
                    if _strip_ts(json.load(fh)) == _strip_ts(receipt):
                        return target
            except (OSError, ValueError):
                pass  # unreadable or unparseable: overwrite it with the truth
        os.makedirs(os.path.dirname(target) or ".", exist_ok=True)
        with open(target, "w") as fh:
            fh.write(text)
        print(f"[fn_allreduce_timer] receipt {target} "
              f"({data['window']['recorded']} collectives, exchange share "
              f"{data['share']['exchange']})", flush=True)
        return target
    except Exception as exc:  # a probe must never take the engine down
        print(f"[fn_allreduce_timer] flush failed: {exc}", flush=True)
        return None
