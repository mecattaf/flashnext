"""Bench-matrix serve-line and honesty-mechanism tests (raw-bytes asserts).

bench/run-matrix.sh reconfigures the serve between speculative arms with its
own serve command, which historically drifted from host/fn-cluster-up.sh's
doctrine: it still carried the plain-eager flag (the fork's
check_cudagraph_safety guard REFUSES plain eager under VLLM_PLE_MMAP=1) and
lacked the text-only multimodal limit (the 256 GiB vision-profiling OOM) —
so BOTH arms failed to boot on the first arm flip. These tests pin the
repaired serve line and the degrade/honesty machinery added 2026-08-30.
"""

import pathlib
import subprocess
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
MATRIX = ROOT / "bench" / "run-matrix.sh"

# The forbidden plain-eager serve flag, assembled so this test file itself
# never carries the literal it forbids.
EAGER_FLAG = "--enforce" + "-eager"


class BenchMatrix(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = MATRIX.read_text()

    def test_syntax_clean(self):
        subprocess.run(["bash", "-n", str(MATRIX)], check=True,
                       capture_output=True)

    def test_no_plain_eager_anywhere(self):
        # Zero occurrences, comments included: the flag must not be
        # reintroducible by uncommenting something.
        self.assertNotIn(EAGER_FLAG, self.text)

    def test_serve_line_carries_the_doctrine_pins(self):
        for needle in (
            "limit-mm-per-prompt",
            "max-num-batched-tokens",
            "max-num-seqs",
            "kv-cache-memory-bytes",
        ):
            self.assertIn(needle, self.text,
                          f"doctrine pin missing from the arm serve line: {needle}")

    def test_spec_on_default_is_the_native_mtp_head(self):
        self.assertIn(r'\"method\":\"mtp\"', self.text,
                      "the spec-on arm must default to the in-checkpoint "
                      "multi-token-prediction head")
        self.assertIn("FN_BENCH_SPEC_ARGS", self.text,
                      "the operator override for the speculative arm is gone")

    def test_cross_node_reap_between_arms(self):
        self.assertIn("reap_serve_node", self.text,
                      "arm flips must reap host-side on both nodes")
        self.assertIn("FN_WORKER_HOST", self.text)

    def test_interim_receipt_after_phase_a(self):
        self.assertIn('"interim": True', self.text,
                      "a runtimeMaxSec kill after Phase A must still leave "
                      "a graded bench receipt")

    def test_phase_d_reports_degraded_runs_honestly(self):
        self.assertIn("spec_on_failed", self.text,
                      "Phase D must derive the measured design, and say so "
                      "when the spec-on arm died")


if __name__ == "__main__":
    unittest.main()
