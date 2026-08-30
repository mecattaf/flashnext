"""Bench-client separation tests (spec claim 6.2).

The reference harness's client (nix-strix-halo lib/bench/vllm-stream-client.py)
shipped ``prefill_mean_s`` character-identical to ``ttft_mean_s`` — a proxy
dressed as a measurement (specs/flashnext/evidence/nix-strix-halo.md §4.4).
These tests feed the flashnext client's parser fixtures with known scheduler
queue and prompt-processing times and assert the two columns stay separate —
and that neither is ever a duplicate of the first-token column.

The client is a hyphenated script (bench/fn-stream-client.py), so it is loaded
by path with importlib, the same way test_instruments.py loads the overlay
modules. Nothing here needs a live engine: the parser and the split are pure.
"""

import importlib.util
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
CLIENT_PATH = ROOT / "bench" / "fn-stream-client.py"


def _load_client():
    spec = importlib.util.spec_from_file_location("fn_stream_client", CLIENT_PATH)
    mod = importlib.util.module_from_spec(spec)
    # Register before exec so dataclasses/typing can resolve the module (py3.14).
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _prom_text(hists):
    """Render ``{metric: (sum, count)}`` as Prometheus exposition text."""
    lines = []
    for metric, (total, count) in hists.items():
        lines.append(f"# TYPE {metric} histogram")
        lines.append(f'{metric}_bucket{{le="0.5"}} {count}')
        lines.append(f"{metric}_sum {total}")
        lines.append(f"{metric}_count {count}")
    return "\n".join(lines) + "\n"


class BenchClientSeparation(unittest.TestCase):
    """The prefill column is a measurement, never a first-token duplicate."""

    @classmethod
    def setUpClass(cls):
        cls.mod = _load_client()

    def _fixtures(self):
        """A batch of 4 requests lands between the two scrapes.

        Known per-request ground truth:
          queue wait  = (108 - 100) / 4 = 2.0 s
          prefill     = (27  - 25 ) / 4 = 0.5 s
          server ttft = (620 - 500) / 4 = 30.0 s   (deliberately DIFFERENT,
                                                    to prove prefill != ttft)
        """
        q, p, t = (self.mod.QUEUE_TIME_METRIC, self.mod.PREFILL_TIME_METRIC,
                   self.mod.TTFT_METRIC)
        before = _prom_text({q: (100.0, 50), p: (25.0, 50), t: (500.0, 50)})
        after = _prom_text({q: (108.0, 54), p: (27.0, 54), t: (620.0, 54)})
        return before, after

    def test_parser_separates_queue_and_prefill(self):
        before_text, after_text = self._fixtures()
        before = self.mod.parse_metrics(before_text)
        after = self.mod.parse_metrics(after_text)
        split = self.mod.split_first_token(before, after)

        # Each component recovers its own injected value…
        self.assertAlmostEqual(split.queue_wait_s, 2.0)
        self.assertAlmostEqual(split.prefill_s, 0.5)
        # …and the two columns are genuinely distinct measurements.
        self.assertNotAlmostEqual(split.queue_wait_s, split.prefill_s)
        # Both drew from direct samples of their own histograms.
        self.assertEqual(split.queue_samples, 4)
        self.assertEqual(split.prefill_samples, 4)
        self.assertEqual(split.prefill_source, self.mod.PREFILL_TIME_METRIC)

    def test_prefill_is_not_the_first_token_column(self):
        # The §4.4 regression guard: prefill must track the prefill histogram,
        # NOT the time-to-first-token series (which here is 30.0 per request).
        before_text, after_text = self._fixtures()
        before = self.mod.parse_metrics(before_text)
        after = self.mod.parse_metrics(after_text)
        split = self.mod.split_first_token(before, after)

        ttft_mean, _ = self.mod.hist_delta_mean(
            before, after, self.mod.TTFT_METRIC)
        self.assertAlmostEqual(ttft_mean, 30.0)
        self.assertNotAlmostEqual(split.prefill_s, ttft_mean)
        self.assertNotEqual(split.prefill_source, self.mod.TTFT_METRIC)

    def test_prefill_fallback_is_inference_minus_decode(self):
        # If the engine build lacks the direct prefill histogram, fall back to
        # inference - decode — still a server-side measurement, still not ttft.
        q = self.mod.QUEUE_TIME_METRIC
        inf, dec = (self.mod.INFERENCE_TIME_METRIC, self.mod.DECODE_TIME_METRIC)
        before = _prom_text({q: (100.0, 50), inf: (60.0, 50), dec: (50.0, 50)})
        after = _prom_text({q: (108.0, 54), inf: (68.0, 54), dec: (54.0, 54)})
        split = self.mod.split_first_token(
            self.mod.parse_metrics(before), self.mod.parse_metrics(after))
        # prefill = (68-60)/4 - (54-50)/4 = 2.0 - 1.0 = 1.0
        self.assertAlmostEqual(split.prefill_s, 1.0)
        self.assertAlmostEqual(split.queue_wait_s, 2.0)
        self.assertIn(self.mod.INFERENCE_TIME_METRIC, split.prefill_source)

    def test_absent_metrics_are_an_honest_absence(self):
        # No queue/prefill/inference metrics at all -> None, never a fabricated
        # proxy from the first-token series.
        t = self.mod.TTFT_METRIC
        before = _prom_text({t: (500.0, 50)})
        after = _prom_text({t: (620.0, 54)})
        split = self.mod.split_first_token(
            self.mod.parse_metrics(before), self.mod.parse_metrics(after))
        self.assertIsNone(split.queue_wait_s)
        self.assertIsNone(split.prefill_s)

    def test_failed_scrape_is_absence_not_a_lifetime_mean(self):
        # A failed bracketing scrape reaches the split as None. Differencing a
        # missing bracket against zero would return the engine's WHOLE-LIFETIME
        # mean under a column whose name promises this batch — the §4.4 defect
        # wearing a new column name. Both components must go empty instead.
        after = self.mod.parse_metrics(_prom_text({
            self.mod.QUEUE_TIME_METRIC: (100000.0, 5000),
            self.mod.PREFILL_TIME_METRIC: (4000.0, 5000),
        }))
        split = self.mod.split_first_token(None, after)
        self.assertIsNone(split.queue_wait_s)
        self.assertIsNone(split.prefill_s)
        self.assertEqual(split.prefill_source, self.mod.SCRAPE_UNAVAILABLE)
        # The lifetime means that a zero baseline would have published.
        self.assertIsNone(self.mod.hist_delta_mean(
            None, after, self.mod.QUEUE_TIME_METRIC)[0])
        # A failed POST-scrape is the same honest absence.
        self.assertIsNone(self.mod.split_first_token(after, None).prefill_s)

    def test_parse_metrics_ignores_gauges_and_buckets(self):
        text = (
            "# TYPE vllm:num_requests_running gauge\n"
            "vllm:num_requests_running 3.0\n"
            "# TYPE " + self.mod.QUEUE_TIME_METRIC + " histogram\n"
            + self.mod.QUEUE_TIME_METRIC + '_bucket{le="0.5"} 2.0\n'
            + self.mod.QUEUE_TIME_METRIC + "_sum 7.5\n"
            + self.mod.QUEUE_TIME_METRIC + "_count 3.0\n"
        )
        snap = self.mod.parse_metrics(text)
        self.assertIn(self.mod.QUEUE_TIME_METRIC, snap)
        self.assertAlmostEqual(snap[self.mod.QUEUE_TIME_METRIC].total, 7.5)
        self.assertAlmostEqual(snap[self.mod.QUEUE_TIME_METRIC].count, 3.0)
        self.assertNotIn("vllm:num_requests_running", snap)


class BenchClientFingerprint(unittest.TestCase):
    """The completion fingerprint must be reproducible and divergence-sensitive."""

    @classmethod
    def setUpClass(cls):
        cls.mod = _load_client()

    def test_fingerprint_is_deterministic(self):
        ids = [1, 2, 3, 58, 9001]
        self.assertEqual(self.mod.fingerprint_of(ids),
                         self.mod.fingerprint_of(list(ids)))

    def test_fingerprint_detects_divergence(self):
        # The QSA-gather signature: same prompt, one completion differs by a
        # single token id. The fingerprint must change.
        a = [1, 2, 3, 58]
        b = [1, 2, 3, 59]
        self.assertNotEqual(self.mod.fingerprint_of(a),
                            self.mod.fingerprint_of(b))

    def test_empty_completion_has_no_fingerprint(self):
        # A completion that produced no tokens has nothing to fingerprint.
        # Hashing the empty sequence would give every silent request one shared
        # constant digest, which the matrix's divergence analysis would read as
        # a set of agreeing witnesses that never spoke.
        self.assertEqual(self.mod.fingerprint_of([]), "")
        self.assertNotEqual(self.mod.fingerprint_of([1]), "")

    def test_fingerprint_never_collides_ids_with_strings(self):
        # Type-tagging: an id sequence cannot collide with a string sequence
        # that happens to spell the same digits.
        self.assertNotEqual(self.mod.fingerprint_of([1, 2]),
                            self.mod.fingerprint_of(["1", "2"]))


class BenchClientCSVContract(unittest.TestCase):
    """Every number carries its protocol: the CSV header says which clock is
    which, and the prefill column is declared independent of the ttft column."""

    @classmethod
    def setUpClass(cls):
        cls.mod = _load_client()

    def test_csv_has_distinct_ttft_queue_prefill_columns(self):
        fields = self.mod.CSV_FIELDS
        self.assertIn("ttft_s", fields)
        self.assertIn("queue_wait_s", fields)
        self.assertIn("prefill_s", fields)
        self.assertIn("concurrency", fields)   # steering §1: per-row concurrency
        self.assertIn("fingerprint", fields)
        self.assertEqual(len(fields), len(set(fields)))

    def test_csv_header_denies_the_reference_defect(self):
        header = self.mod.CSV_HEADER_COMMENT
        self.assertIn("queue_wait_s", header)
        self.assertIn("prefill_s", header)
        # The header must state prefill is NOT derived from the first-token
        # column, and it must name the metrics the split reads from.
        self.assertIn("NOT prefill", header)
        self.assertIn(self.mod.QUEUE_TIME_METRIC, header)
        self.assertIn(self.mod.PREFILL_TIME_METRIC, header)


if __name__ == "__main__":
    unittest.main()
