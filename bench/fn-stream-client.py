#!/usr/bin/env python3
"""fn-stream-client.py — the honest flashnext streaming benchmark client.

Spec claim 6.2 (specs/flashnext/spec.md#r6): the bench client must separate
scheduler queue wait from prompt processing. The reference harness's client
(nix-strix-halo ``lib/bench/vllm-stream-client.py``) shipped a ``prefill_mean_s``
column that was character-identical to its ``ttft_mean_s`` column — a proxy
dressed as a measurement (evidence/nix-strix-halo.md §4.4, confirmed at line
297 of that file). This client does not repeat that: prefill is read off the
engine's ``/metrics`` endpoint as an independent histogram, and the CSV header
spells out which number came from which clock.

Three clocks, three columns, never conflated:

  ttft_s        CLIENT-side time to first token. The interval from the moment
                this client releases a request until the first streamed token
                arrives. It necessarily includes queue wait + prefill + the
                first decode step + network + SSE framing. It is a real
                measurement, but it is NOT prefill.

  queue_wait_s  SERVER-side scheduler queue wait, scraped from the engine's
                Prometheus ``/metrics`` endpoint as the delta of the
                ``vllm:request_queue_time_seconds`` histogram across the batch.

  prefill_s     SERVER-side prompt processing, scraped from ``/metrics`` as
                the delta of the ``vllm:request_prefill_time_seconds``
                histogram across the batch. Independent of both ttft_s and
                queue_wait_s. This is the column the reference harness faked;
                here it is a measurement.

Only the last two are allowed to claim "queue" or "prefill". ttft_s is the
client-observed first-token latency and nothing more. F.8 doctrine: no number
leaves this client without its protocol attached, and no prefill figure is
ever derived from a first-token figure.

Stdlib only — the harness must run on any node that can reach the pair's API,
with no third-party dependencies.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Dict, List, Mapping, Optional

# ---------------------------------------------------------------------------
# The engine's Prometheus metric names. These are the load-bearing constants:
# prefill and queue wait are read from these two histograms, never from the
# first-token series. If the engine does not expose one, the corresponding
# column is emitted empty — an honest absence, never a fabricated proxy.
# ---------------------------------------------------------------------------
QUEUE_TIME_METRIC = "vllm:request_queue_time_seconds"
PREFILL_TIME_METRIC = "vllm:request_prefill_time_seconds"
TTFT_METRIC = "vllm:time_to_first_token_seconds"
# Fallback chain for prompt processing if the direct prefill histogram is
# absent on a given engine build: prefill ~= inference - decode.
INFERENCE_TIME_METRIC = "vllm:request_inference_time_seconds"
DECODE_TIME_METRIC = "vllm:request_decode_time_seconds"

# A short, neutral prompt for the depth-0 arm. No model-family names (F.5).
SHORT_PROMPT = "The ledger records what the night proved: "

# A fixed paragraph we repeat to synthesize a prompt of a target token depth.
# Tokenization is engine-side, so converge_prompt() verifies the actual prompt
# token count the server reports and rescales once if it undershot.
_DEPTH_PARAGRAPH = (
    "The fleet measures before it claims. Every receipt is a fact the morning "
    "operator can check. The pair serves from its own drives; the rails carry "
    "tensors and the wire carries control. Nothing is narrated that was not "
    "measured, and no column is published without its protocol attached. "
)

# CSV column order. The header comment written above these names documents the
# semantics of every column; a consumer must never have to guess which clock a
# number came from.
CSV_FIELDS = [
    "arm",              # bench arm label (e.g. spec-off / spec-on)
    "load",             # load index within the arm (1..loads_per_arm)
    "depth_target",     # requested prompt depth in tokens
    "depth_actual",     # server-reported prompt_tokens actually accepted
    "concurrency",      # in-flight sequences for this row (steering §1)
    "request_index",    # per-request index within the load
    "ttft_s",           # CLIENT-side first-token latency
    "queue_wait_s",     # SERVER-side scheduler queue wait (metrics delta)
    "prefill_s",        # SERVER-side prompt processing (metrics delta)
    "decode_s",         # CLIENT-side total minus ttft
    "total_s",          # CLIENT-side total request wall time
    "completion_tokens",  # server-reported completion token count
    "fingerprint",      # sha256 over the completion's token ids
    "spec_config",      # the speculative-decoding configuration of the arm
]

CSV_HEADER_COMMENT = """\
# fn-stream-client CSV — column semantics (spec claim 6.2; protocol per row).
#   arm               bench arm label (spec-off / spec-on).
#   load              load index within the arm, 1..loads_per_arm.
#   depth_target      requested prompt depth in tokens (the independent var).
#   depth_actual      prompt_tokens the server actually accepted.
#   concurrency       number of in-flight sequences when this row was measured.
#   request_index     per-request index within the load.
#   ttft_s            CLIENT-side time-to-first-token: request release -> first
#                     streamed token. Includes queue + prefill + first decode +
#                     network + SSE framing. It is NOT prefill.
#   queue_wait_s      SERVER-side scheduler queue wait, delta of the
#                     {q} histogram across this load's batch.
#   prefill_s         SERVER-side prompt processing, delta of the
#                     {p} histogram across this load's batch. Independent of
#                     ttft_s: the reference harness duplicated ttft into this
#                     column (evidence/nix-strix-halo.md §4.4); we do not.
#   decode_s          CLIENT-side total minus ttft.
#   total_s           CLIENT-side total request wall time.
#   completion_tokens server-reported completion token count.
#   fingerprint       sha256 over the completion's token ids (ints when the
#                     engine provides them, token pieces otherwise).
#   spec_config       speculative-decoding configuration active for the arm.
# queue_wait_s and prefill_s are load-level server measurements shared by every
# row of a load; ttft_s/total_s/decode_s/fingerprint are per-request.
# Empty means "the engine did not expose this metric" — an honest absence.
""".format(q=QUEUE_TIME_METRIC, p=PREFILL_TIME_METRIC)


# ---------------------------------------------------------------------------
# Prometheus metrics parsing.
# ---------------------------------------------------------------------------
@dataclass
class HistReading:
    """One histogram's cumulative ``_sum`` and ``_count`` at scrape time."""
    total: float
    count: float


def parse_metrics(text: str) -> Dict[str, HistReading]:
    """Parse Prometheus exposition text into histogram ``_sum``/``_count``.

    Returns a dict keyed by the histogram's base name (the metric name with any
    ``_sum``/``_count`` suffix stripped) to its cumulative reading. Bucket
    lines, gauges, and counters are ignored: the queue/prefill split only needs
    the cumulative sums and counts.
    """
    readings: Dict[str, HistReading] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        name = parts[0].split("{", 1)[0]
        try:
            value = float(parts[-1])
        except ValueError:
            continue
        if name.endswith("_sum"):
            base = name[: -len("_sum")]
            readings.setdefault(base, HistReading(0.0, 0.0)).total = value
        elif name.endswith("_count"):
            base = name[: -len("_count")]
            readings.setdefault(base, HistReading(0.0, 0.0)).count = value
    return readings


def scrape_metrics(metrics_url: str, timeout: float = 30.0) -> Dict[str, HistReading]:
    """Scrape the engine ``/metrics`` endpoint and parse it."""
    req = urllib.request.Request(metrics_url, headers={"Accept": "text/plain"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return parse_metrics(resp.read().decode(errors="ignore"))


def hist_delta_mean(before: Mapping[str, HistReading],
                    after: Mapping[str, HistReading],
                    metric: str) -> tuple:
    """Per-request mean of ``metric`` over the requests landed between scrapes.

    Returns ``(mean_or_None, sample_count)``. ``None`` means the engine never
    exposed the metric (honest absence) or no new sample landed.
    """
    a = after.get(metric)
    if a is None:
        return (None, 0)
    b = before.get(metric)
    d_sum = a.total - (b.total if b is not None else 0.0)
    d_count = a.count - (b.count if b is not None else 0.0)
    if d_count <= 0:
        return (None, 0)
    return (d_sum / d_count, int(d_count))


@dataclass
class FirstTokenSplit:
    """The separated first-token budget. queue_wait_s + prefill_s are the two
    server-side components; neither is derived from the client TTFT series."""
    queue_wait_s: Optional[float]
    prefill_s: Optional[float]
    queue_samples: int
    prefill_samples: int
    prefill_source: str  # which metric / derivation produced prefill_s


def split_first_token(before: Mapping[str, HistReading],
                      after: Mapping[str, HistReading],
                      queue_metric: str = QUEUE_TIME_METRIC,
                      prefill_metric: str = PREFILL_TIME_METRIC) -> FirstTokenSplit:
    """Separate scheduler queue wait from prompt processing.

    Both components come from the engine's own histograms, scraped before and
    after the batch — never from the client's first-token series. If the direct
    prefill histogram is absent we fall back to ``inference - decode`` means;
    if even that is unavailable, prefill is reported as an honest ``None``.
    """
    queue_wait, queue_samples = hist_delta_mean(before, after, queue_metric)
    prefill, prefill_samples = hist_delta_mean(before, after, prefill_metric)
    source = prefill_metric
    if prefill is None:
        infer, _ = hist_delta_mean(before, after, INFERENCE_TIME_METRIC)
        decode, _ = hist_delta_mean(before, after, DECODE_TIME_METRIC)
        if infer is not None and decode is not None:
            prefill = max(0.0, infer - decode)
            prefill_samples = -1  # derived, not a direct sample count
            source = f"{INFERENCE_TIME_METRIC} - {DECODE_TIME_METRIC}"
    return FirstTokenSplit(queue_wait_s=queue_wait, prefill_s=prefill,
                           queue_samples=queue_samples,
                           prefill_samples=prefill_samples,
                           prefill_source=source)


# ---------------------------------------------------------------------------
# Completion fingerprinting.
# ---------------------------------------------------------------------------
def fingerprint_of(sequence) -> str:
    """Deterministic sha256 over a completion's token sequence.

    Integer token ids and token strings are type-tagged so an id sequence can
    never collide with a string sequence. Under greedy sampling an identical
    completion reproduces an identical fingerprint; a divergent completion
    (the QSA-gather signature the matrix hunts for) changes it.
    """
    h = hashlib.sha256()
    for item in sequence:
        if isinstance(item, int):
            h.update(b"i:")
            h.update(str(item).encode("utf-8"))
        else:
            h.update(b"s:")
            h.update(str(item).encode("utf-8"))
        h.update(b";")
    return h.hexdigest()


# ---------------------------------------------------------------------------
# Streaming request.
# ---------------------------------------------------------------------------
def _extract_chunk_tokens(choice: Mapping) -> tuple:
    """Pull (token_strings, token_ids) out of one streamed choice, best-effort.

    vLLM's OpenAI layer reports per-token strings under ``logprobs.tokens`` and
    only exposes integer ids when the as-served fork adds them; we prefer the
    ids and fall back to the strings (deterministic under greedy sampling).
    """
    lp = choice.get("logprobs") or {}
    tok_strs = lp.get("tokens") or []
    tok_ids = lp.get("token_ids") or []
    # Some builds surface ids directly on the choice.
    if not tok_ids:
        direct = choice.get("token_ids")
        if isinstance(direct, int):
            tok_ids = [direct]
        elif isinstance(direct, list):
            tok_ids = [x for x in direct if isinstance(x, int)]
    if not tok_strs and not tok_ids and choice.get("text"):
        tok_strs = [choice["text"]]
    return (tok_strs, tok_ids)


def stream_one_request(api: str, model: str, prompt: str, max_tokens: int,
                       temperature: float, timeout: float) -> Dict:
    """Run one streaming completion and measure it client-side.

    Returns a dict with client-side ttft/decode/total, the server-reported
    token counts, and the completion fingerprint. Never scrapes /metrics: the
    queue/prefill split is a load-level concern handled by the caller.
    """
    url = api.rstrip("/") + "/v1/completions"
    body = {
        "model": model,
        "prompt": prompt,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "stream": True,
        "stream_options": {"include_usage": True},
        "logprobs": 1,
    }
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"})

    id_seq: List[int] = []
    str_seq: List[str] = []
    all_ids = True
    t_start = time.perf_counter()
    t_first: Optional[float] = None
    t_last: Optional[float] = None
    usage: Optional[Dict] = None

    with urllib.request.urlopen(req, timeout=timeout) as resp:
        for raw in resp:
            line = raw.decode(errors="ignore").strip()
            if not line.startswith("data:"):
                continue
            payload = line[len("data:"):].strip()
            if payload == "[DONE]":
                break
            try:
                chunk = json.loads(payload)
            except json.JSONDecodeError:
                continue
            if chunk.get("usage"):
                usage = chunk["usage"]
            for choice in chunk.get("choices") or []:
                tok_strs, tok_ids = _extract_chunk_tokens(choice)
                n = max(len(tok_strs), len(tok_ids))
                if n == 0:
                    continue
                now = time.perf_counter()
                if t_first is None:
                    t_first = now
                t_last = now
                for i in range(n):
                    s = tok_strs[i] if i < len(tok_strs) else ""
                    tid = tok_ids[i] if i < len(tok_ids) else None
                    if isinstance(tid, int):
                        id_seq.append(tid)
                    else:
                        all_ids = False
                    str_seq.append(s)

    total = time.perf_counter() - t_start
    ttft = None if t_first is None else t_first - t_start
    decode = None if ttft is None else max(0.0, total - ttft)
    seq = id_seq if (all_ids and id_seq) else str_seq
    return {
        "ttft": ttft,
        "decode": decode,
        "total": total,
        "prompt_tokens": (usage or {}).get("prompt_tokens"),
        "completion_tokens": (usage or {}).get("completion_tokens"),
        "fingerprint": fingerprint_of(seq),
        "token_ids": id_seq if all_ids and id_seq else None,
    }


# ---------------------------------------------------------------------------
# Concurrency.
# ---------------------------------------------------------------------------
def run_concurrent(api: str, model: str, prompt: str, max_tokens: int,
                   temperature: float, concurrency: int, requests: int,
                   timeout: float) -> List[Dict]:
    """Run ``requests`` completions at ``concurrency`` in-flight sequences.

    A barrier releases every worker at once so the in-flight count is exactly
    ``concurrency`` from the first instant; each worker then drains its slice
    sequentially. This is what makes the number on the ``concurrency`` column
    true, and what makes the QSA-gather divergence (steering §2) reachable.
    """
    if concurrency < 1:
        concurrency = 1
    if requests < 1:
        requests = 1
    # Slice the request indices across the workers.
    chunks: List[List[int]] = [[] for _ in range(concurrency)]
    for idx in range(requests):
        chunks[idx % concurrency].append(idx)

    barrier = threading.Barrier(concurrency + 1)  # workers + releaser
    results: List[Optional[Dict]] = [None] * requests
    lock = threading.Lock()
    errors: List[str] = []

    def worker(chunk: List[int]) -> None:
        try:
            barrier.wait()
        except threading.BrokenBarrierError:
            return
        for idx in chunk:
            try:
                rec = stream_one_request(api, model, prompt, max_tokens,
                                         temperature, timeout)
                with lock:
                    results[idx] = rec
            except Exception as e:  # noqa: BLE001 - record, keep going
                with lock:
                    errors.append(f"request {idx}: {e.__class__.__name__}: {e}")

    threads = [threading.Thread(target=worker, args=(c,), daemon=True)
               for c in chunks]
    for t in threads:
        t.start()
    barrier.wait()  # release all workers together
    for t in threads:
        t.join()

    done = [r for r in results if r is not None]
    if not done:
        raise RuntimeError(
            "no request completed; first errors: " + "; ".join(errors[:3]))
    if errors:
        print(f"fn-stream-client: {len(errors)}/{requests} requests errored "
              f"(first: {errors[0]})", file=sys.stderr)
    return done


# ---------------------------------------------------------------------------
# Prompt depth convergence.
# ---------------------------------------------------------------------------
def _probe_prompt_tokens(api: str, model: str, prompt: str,
                         timeout: float) -> Optional[int]:
    """Cheap non-streaming probe returning the server's prompt_tokens."""
    url = api.rstrip("/") + "/v1/completions"
    body = {"model": model, "prompt": prompt, "max_tokens": 1, "temperature": 0}
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())["usage"]["prompt_tokens"]
    except Exception:  # noqa: BLE001 - convergence is best-effort
        return None


def converge_prompt(api: str, model: str, depth: int, timeout: float,
                    attempts: int = 3) -> str:
    """Build a prompt the server reports as ~``depth`` tokens.

    Tokenization is engine-side, so we estimate, probe the actual prompt_tokens,
    and rescale once if we undershot. The returned prompt is what the measured
    batch uses; the CSV still records depth_actual per request, so an honest
    residual undershoot is visible rather than hidden.
    """
    if depth <= 0:
        return SHORT_PROMPT
    chars_per_token = 4.0
    reps = max(1, int((depth * chars_per_token) / len(_DEPTH_PARAGRAPH)))
    prompt = _DEPTH_PARAGRAPH * reps
    for _ in range(attempts):
        got = _probe_prompt_tokens(api, model, prompt, timeout)
        if got is None or got <= 0:
            break
        if abs(got - depth) <= max(64, depth * 0.02):
            return prompt
        scale = depth / got
        reps = max(1, int(reps * scale) + (1 if scale > 1 else 0))
        prompt = _DEPTH_PARAGRAPH * reps
    return prompt


# ---------------------------------------------------------------------------
# CSV output.
# ---------------------------------------------------------------------------
def fmt(x) -> str:
    """Render a number or an honest absence."""
    if x is None:
        return ""
    if isinstance(x, float):
        return f"{x:.6f}"
    return str(x)


def write_csv(path: str, rows: List[Dict]) -> None:
    """Append rows, writing the semantics header only into a fresh file."""
    import csv as _csv
    fresh = not os.path.exists(path) or os.path.getsize(path) == 0
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "a", newline="") as f:
        if fresh:
            f.write(CSV_HEADER_COMMENT)
        w = _csv.DictWriter(f, fieldnames=CSV_FIELDS)
        if fresh:
            w.writeheader()
        for row in rows:
            w.writerow({k: row.get(k, "") for k in CSV_FIELDS})


# ---------------------------------------------------------------------------
# CLI.
# ---------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="fn-stream-client.py",
        description="Honest streaming bench client: separates queue wait from "
                    "prefill (spec claim 6.2).")
    p.add_argument("--api", default=os.environ.get(
        "FN_API", f"http://127.0.0.1:{os.environ.get('FN_PORT', '1234')}"),
        help="OpenAI-compatible API base URL")
    p.add_argument("--metrics", default=None,
                   help="Metrics endpoint (default: <api>/metrics)")
    p.add_argument("--model", default=os.environ.get("FN_SERVED_NAME", "flashnext"))
    p.add_argument("--arm", default="unspecified", help="bench arm label")
    p.add_argument("--load", type=int, default=1, help="load index in the arm")
    p.add_argument("--depth", type=int, default=0,
                   help="target prompt depth in tokens")
    p.add_argument("--concurrency", type=int, default=int(os.environ.get(
        "FN_BENCH_CONCURRENCY", "1")), help="in-flight sequences")
    p.add_argument("--requests", type=int, default=int(os.environ.get(
        "FN_BENCH_REQUESTS", "8")), help="completions to measure this call")
    p.add_argument("--max-tokens", type=int, default=int(os.environ.get(
        "FN_BENCH_MAX_TOKENS", "128")))
    p.add_argument("--temperature", type=float, default=0.0,
                   help="greedy by default so fingerprints are reproducible")
    p.add_argument("--timeout", type=float, default=7200.0)
    p.add_argument("--spec-label", default=os.environ.get(
        "FN_BENCH_SPEC_LABEL", "unspecified"),
                   help="speculative-decoding configuration of the arm")
    p.add_argument("--csv", default=None,
                   help="append rows here (stdout summary if omitted)")
    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    metrics_url = args.metrics or (args.api.rstrip("/") + "/metrics")

    prompt = converge_prompt(args.api, args.model, args.depth, args.timeout)

    # Bracket the measured batch with metrics scrapes. Convergence probes ran
    # BEFORE the first scrape, so the delta covers only the measured requests.
    try:
        before = scrape_metrics(metrics_url)
    except Exception as e:  # noqa: BLE001 - honest absence, not a crash
        print(f"fn-stream-client: pre-scrape failed ({e}); queue/prefill will "
              f"be empty", file=sys.stderr)
        before = {}
    records = run_concurrent(args.api, args.model, prompt, args.max_tokens,
                             args.temperature, args.concurrency, args.requests,
                             args.timeout)
    try:
        after = scrape_metrics(metrics_url)
    except Exception as e:  # noqa: BLE001
        print(f"fn-stream-client: post-scrape failed ({e}); queue/prefill will "
              f"be empty", file=sys.stderr)
        after = {}

    split = split_first_token(before, after)

    rows = []
    for i, rec in enumerate(records):
        rows.append({
            "arm": args.arm,
            "load": args.load,
            "depth_target": args.depth,
            "depth_actual": rec.get("prompt_tokens"),
            "concurrency": args.concurrency,
            "request_index": i,
            "ttft_s": fmt(rec["ttft"]),
            "queue_wait_s": fmt(split.queue_wait_s),
            "prefill_s": fmt(split.prefill_s),
            "decode_s": fmt(rec["decode"]),
            "total_s": fmt(rec["total"]),
            "completion_tokens": rec.get("completion_tokens"),
            "fingerprint": rec["fingerprint"],
            "spec_config": args.spec_label,
        })

    if args.csv:
        write_csv(args.csv, rows)
    # Always emit the fingerprints + split on stdout for the matrix to consume.
    out = {
        "arm": args.arm,
        "load": args.load,
        "depth_target": args.depth,
        "concurrency": args.concurrency,
        "requests": len(rows),
        "queue_wait_s": split.queue_wait_s,
        "prefill_s": split.prefill_s,
        "prefill_source": split.prefill_source,
        "queue_samples": split.queue_samples,
        "prefill_samples": split.prefill_samples,
        "fingerprints": [r["fingerprint"] for r in rows],
    }
    print(json.dumps(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
