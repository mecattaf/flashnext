"""usb4stream first-light bench: the invariants that keep it from wedging a rail.

`bench/usb4stream-bench.py` is authored by one lane and RUN by a dead-last
checkpoint on an idle cable, unattended. Nothing between those two moments
looks at it, so the safety mechanics are asserted here instead:

  * no numbered device literal anywhere in the source — numbering is
    asymmetric across the twins and the coordinator's low indices are
    PEERLESS, where a blocking open waits forever (docs/DECISIONS-2026-08-30
    §5.2). This mirrors the lane's acceptance argv;
  * the idempotence guard — a harness retry that finds the receipt must touch
    no device, which is what makes retries storm-free by construction;
  * the serve-up skip — the stream device shares cable A with the serving
    rails, so a live pair is a typed skip, never a co-existence experiment;
  * one open attempt per side, ever, and no configfs write;
  * and the three outcome branches of the receipts checker's bound, exercised
    against synthetic receipt data.
"""

import importlib.util
import json
import pathlib
import re
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
BENCH_PATH = ROOT / "bench" / "usb4stream-bench.py"
VERIFY_PATH = ROOT / "scripts" / "receipts-verify.py"

# The forbidden shape: the device word with a digit welded to it. Assembled
# from parts so this test file never carries the literal it forbids.
DEVICE_WORD = "tb" + "stream"
NUMBERED_DEVICE_RE = re.compile(DEVICE_WORD + r"[0-9]")


def _load(path, name):
    """Import a dash-named script as a module. Both files are import-safe:
    everything effectful sits under a __main__ guard."""
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


BENCH = _load(BENCH_PATH, "usb4stream_bench")
VERIFY = _load(VERIFY_PATH, "receipts_verify")


def _ok_receipt_data(**overrides):
    """A synthetic 'the bench actually ran' receipt payload."""
    data = {
        "outcome": "ok",
        "serve_up": False,
        "loop": "python",
        "open_count": {"coordinator": 1, "peer": 1},
        "device": {"coordinator": "/dev/" + DEVICE_WORD + "2",
                   "peer": "/dev/" + DEVICE_WORD + "0"},
        "ring_size": "1024",
        "throttling": "0",
        "rtt_us": {str(s): {"p50": 14.3, "p99": 25.3}
                   for s in (64, 4096, 16384, 65536)},
        "exchange_us": {str(s): {"p50": 31.2, "p99": 44.0}
                        for s in (8192, 16384, 65536)},
        "throughput_mb_s": {"coordinator_to_peer": 780.0,
                            "peer_to_coordinator": 774.5},
    }
    data.update(overrides)
    return data


class SourceCompiles(unittest.TestCase):
    def test_both_python_files_compile(self):
        for path in (BENCH_PATH, VERIFY_PATH):
            subprocess.run(["python3", "-m", "py_compile", str(path)],
                           check=True, capture_output=True)


class WedgeSafetyInvariants(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.src = BENCH_PATH.read_text()

    def test_no_numbered_device_node_anywhere(self):
        """The lane's headline invariant: comments and docstrings included."""
        hit = NUMBERED_DEVICE_RE.search(self.src)
        self.assertIsNone(
            hit,
            f"bench source carries a numbered device literal "
            f"({hit.group(0) if hit else ''}): numbering is asymmetric across "
            "the twins and the coordinator's low indices are peerless — the "
            "node must be resolved through the configfs index on EACH node.")

    def test_device_node_is_built_from_the_resolved_index(self):
        self.assertEqual(BENCH.DEVICE_PREFIX, "/dev/" + DEVICE_WORD)
        for needle in ("configfs", "index", "key", "xdomain"):
            self.assertIn(needle, self.src,
                          f"resolution chain step '{needle}' is not in the source")

    def test_idempotence_guard_is_present_and_works(self):
        self.assertIn("already_banked", self.src)
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "usb4stream.json"
            self.assertFalse(BENCH.already_banked(path))
            path.write_text("{}")
            self.assertTrue(BENCH.already_banked(path))

    def test_receipt_path_is_the_graded_one(self):
        self.assertEqual(BENCH.RECEIPT_PATH,
                         ROOT / "results" / "receipts" / "usb4stream.json")

    def test_serve_up_skip_exists_and_is_the_first_precondition(self):
        self.assertEqual(BENCH.SKIP_SERVE_UP, "skipped:serve-up-on-shared-cable")
        for skip in (BENCH.SKIP_SERVE_UP, BENCH.SKIP_PEER_UNREACHABLE,
                     BENCH.SKIP_CONFIGFS_MISSING):
            self.assertTrue(skip.startswith("skipped:"))
            self.assertIn(skip, self.src)
        # Order matters: the serve check must be reached before the ping and
        # the configfs check, since a wedge on the shared cable is the one
        # overnight act that can take the headline deliverable down.
        self.assertLess(self.src.index(BENCH.SKIP_SERVE_UP),
                        self.src.index(BENCH.SKIP_PEER_UNREACHABLE))
        self.assertLess(self.src.index(BENCH.SKIP_PEER_UNREACHABLE),
                        self.src.index(BENCH.SKIP_CONFIGFS_MISSING))

    def test_exactly_one_open_attempt_per_side(self):
        self.assertEqual(BENCH.open_count(), 0)
        with tempfile.TemporaryDirectory() as tmp:
            fake = pathlib.Path(tmp) / "node"
            fake.write_bytes(b"")
            fd = BENCH.open_stream_once(str(fake))
            try:
                self.assertEqual(BENCH.open_count(), 1)
                with self.assertRaises(RuntimeError):
                    BENCH.open_stream_once(str(fake))
                self.assertEqual(BENCH.open_count(), 1)
            finally:
                import os
                os.close(fd)

    def test_configfs_is_never_opened_for_writing(self):
        # ring_size and throttling are READ. A write to a configfs attribute
        # is a router-state change this bench must never make.
        self.assertNotIn('"w"', self.src.split("def _read_attr", 1)[1][:400])
        for banned in ("configfs_group, \"w\"", "open(group"):
            self.assertNotIn(banned, self.src)

    def test_status_is_always_pass(self):
        data = BENCH.schedule_description()
        self.assertEqual(data["rtt_sizes"], [64, 4096, 16384, 65536])
        self.assertEqual(data["exchange_sizes"], [8192, 16384, 65536])
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "usb4stream.json"
            BENCH.write_receipt("aborted:rtt:EIO", {"serve_up": False}, path)
            receipt = json.loads(path.read_text())
        self.assertEqual(receipt["status"], "pass")
        self.assertEqual(receipt["step"], "usb4stream")
        self.assertEqual(receipt["data"]["outcome"], "aborted:rtt:EIO")
        self.assertEqual(receipt["data"]["loop"], "python")

    def test_peer_is_launched_by_streaming_this_file_never_a_copy(self):
        self.assertIn("python3 - --role peer", self.src)
        self.assertIn("python3 - --role probe", self.src)
        for banned in ("scp ", "rsync "):
            self.assertNotIn(banned, self.src)

    def test_abort_typing_carries_phase_and_errno(self):
        BENCH.set_phase("exchange")
        self.assertEqual(BENCH.aborted(OSError(5, "io")), "aborted:exchange:EIO")
        self.assertEqual(BENCH.aborted(BENCH.BenchTimeout("x"), "open"),
                         "aborted:open:ETIMEDOUT")
        BENCH.set_phase("startup")

    def test_rail_peer_address_flips_the_last_octet(self):
        self.assertEqual(BENCH.rail_peer_address("10.99.0.1"), "10.99.0.2")
        self.assertEqual(BENCH.rail_peer_address("10.99.0.2"), "10.99.0.1")

    def test_percentiles_are_nearest_rank(self):
        samples = [float(x) for x in range(1, 101)]
        self.assertEqual(BENCH.percentile(samples, 50), 50.0)
        self.assertEqual(BENCH.percentile(samples, 99), 99.0)
        self.assertEqual(BENCH.percentile([], 50), 0.0)


class ReceiptsBound(unittest.TestCase):
    """The checker's three outcome branches, on synthetic receipt dicts."""

    def setUp(self):
        self.check = VERIFY.BOUNDS["usb4stream"]

    def test_the_bound_is_registered(self):
        self.assertIn("usb4stream", VERIFY.BOUNDS)

    # --- branch 1: ok ------------------------------------------------------
    def test_ok_receipt_passes(self):
        self.assertIsNone(self.check(_ok_receipt_data()))

    def test_ok_requires_one_open_per_side(self):
        err = self.check(_ok_receipt_data(
            open_count={"coordinator": 1, "peer": 2}))
        self.assertIsNotNone(err)
        self.assertIn("open_count", err)

    def test_ok_requires_the_whole_schedule(self):
        for key in ("rtt_us", "exchange_us", "throughput_mb_s", "device"):
            self.assertIsNotNone(self.check(_ok_receipt_data(**{key: {}})),
                                 f"a receipt with no {key} must not pass as ok")
        thin_rtt = {"64": {"p50": 1.0, "p99": 2.0}}
        self.assertIsNotNone(self.check(_ok_receipt_data(rtt_us=thin_rtt)))
        thin_exchange = {"8192": {"p50": 1.0, "p99": 2.0}}
        self.assertIsNotNone(self.check(_ok_receipt_data(
            exchange_us=thin_exchange)))
        one_way = {"coordinator_to_peer": 780.0}
        self.assertIsNotNone(self.check(_ok_receipt_data(
            throughput_mb_s=one_way)))
        data = _ok_receipt_data()
        del data["ring_size"]
        self.assertIsNotNone(self.check(data))
        self.assertIsNotNone(self.check(_ok_receipt_data(loop="c")))

    # --- branch 2: skipped: ------------------------------------------------
    def test_every_skip_reason_passes(self):
        for reason in ("skipped:serve-up-on-shared-cable",
                       "skipped:rail-peer-unreachable",
                       "skipped:configfs-group-missing"):
            self.assertIsNone(
                self.check({"outcome": reason, "serve_up": reason.endswith(
                    "serve-up-on-shared-cable")}),
                f"{reason} must pass: a typed skip is the design working")

    def test_bare_skip_without_reason_text_is_a_violation(self):
        err = self.check({"outcome": "skipped:", "serve_up": False})
        self.assertIsNotNone(err)
        self.assertIn("reason text", err)

    # --- branch 3: aborted: ------------------------------------------------
    def test_aborted_receipt_passes_with_phase_and_errno(self):
        self.assertIsNone(self.check(
            {"outcome": "aborted:open:ETIMEDOUT", "serve_up": False}))
        self.assertIsNone(self.check(_ok_receipt_data(
            outcome="aborted:throughput:ENXIO")))

    def test_bare_abort_without_reason_text_is_a_violation(self):
        err = self.check({"outcome": "aborted:", "serve_up": False})
        self.assertIsNotNone(err)
        self.assertIn("reason text", err)

    # --- shape ------------------------------------------------------------
    def test_missing_outcome_is_a_violation(self):
        self.assertIsNotNone(self.check({"serve_up": False}))

    def test_serve_up_must_be_recorded_in_every_receipt_including_skips(self):
        err = self.check({"outcome": "skipped:serve-up-on-shared-cable"})
        self.assertIsNotNone(err)
        self.assertIn("serve_up", err)

    def test_unknown_outcome_is_a_violation(self):
        self.assertIsNotNone(self.check({"outcome": "fine", "serve_up": False}))


if __name__ == "__main__":
    unittest.main()
