# SPDX-License-Identifier: Apache-2.0
# Adapted from ds4_synctrace.py in AlexKGwyn/ds4-vllm @ a8f620d
# (container/rootfs/opt/venv/lib/python3.12/site-packages/ds4_synctrace.py),
# Apache License 2.0. Changes: DS4_ surface renamed to FN_; the module is
# driven from the flashnext FN_PROFILE window instead of ds4_tl_indexer.
"""Attribute blocking device->host syncs to python call sites.

The upstream motivation (measured on ds4-vllm's 2026-08-07 profile):
hipMemcpyWithStream at 42% of decode self-CPU and hipStreamSynchronize at
16% -- together the dominant cost, once kernel launches are down. But
`hipMemcpyWithStream` in a profiler table is just a count; it does not say
which line caused it.

`with_stack=True` on the torch profiler answers that in principle and is
unusable in practice here: it captures a python stack for every one of
~3,200 ops per step, which pegged the worker at 94% CPU and stalled the
engine on shm_broadcast.

This is the cheap alternative. It wraps only the handful of Tensor methods
that can force a blocking D2H, and records one `sys._getframe` per call --
~25 events per step rather than ~3,200. Overhead is a dict increment on an
already-blocking operation, i.e. noise.

Limitation worth knowing (retained from upstream): boolean-mask indexing
(`x[mask]`) reaches nonzero() inside C++ `at::index`, not through the
python method, so it is NOT counted here. Anything unattributed after this
is a candidate for that.

Driven from the flashnext FN_PROFILE window driver so it shares that
window (no new env var of its own, hence no ray restart to propagate one).
Default off: nothing installs at import; install()/uninstall() bracket the
window and are the only switches.
"""

import collections
import sys

import torch

_counts = collections.Counter()
_installed = False
_orig = {}
# Tensor methods that block on a device->host transfer.
_TARGETS = ("item", "tolist", "cpu", "numpy", "nonzero", "__float__", "__int__")


def _wrap(name):
    orig = getattr(torch.Tensor, name)

    def wrapper(self, *a, **kw):
        try:
            f = sys._getframe(1)
            _counts[f"{f.f_code.co_filename.split('site-packages/')[-1]}:"
                    f"{f.f_lineno} {f.f_code.co_name}() [{name}]"] += 1
        except Exception:
            pass
        return orig(self, *a, **kw)

    _orig[name] = orig
    setattr(torch.Tensor, name, wrapper)


def install():
    global _installed
    if _installed:
        return
    for n in _TARGETS:
        try:
            _wrap(n)
        except Exception:
            pass
    _installed = True


def uninstall():
    global _installed
    for n, o in _orig.items():
        try:
            setattr(torch.Tensor, n, o)
        except Exception:
            pass
    _orig.clear()
    _installed = False


def reset():
    _counts.clear()


def report(steps=1.0):
    """Formatted table, per-decode-step, most frequent first."""
    if not _counts:
        return ("### BLOCKING-SYNC ATTRIBUTION ###\n  (no events -- either the "
                "tracer never installed, or every sync is bool-mask indexing / "
                "C++-internal and invisible to a python hook)\n\n")
    total = sum(_counts.values())
    out = [f"### BLOCKING-SYNC ATTRIBUTION ({total} events over {steps:.2f} "
           f"decode steps = {total / max(steps, 1e-9):.1f}/step) ###"]
    for site, n in _counts.most_common(30):
        out.append(f"  {n / max(steps, 1e-9):7.1f}/step  ({n:5d})  {site}")
    out.append("")
    return "\n".join(out) + "\n\n"
