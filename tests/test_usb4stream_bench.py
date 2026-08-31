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


# ---------------------------------------------------------------------------
# The cable selection, added when the bench was pointed at cable B.
#
# The bench was written against ONE cable — the tensor rail — and every part
# of the resolution chain was anchored to it: the netdev was found by its
# RAIL_NET address, the peer was derived by swapping the last octet of a /30,
# and the cable never travelled to the worker because there was nothing to
# say. Pointing it at the spare cable breaks all three assumptions at once,
# and getting any of them wrong opens two ends against DIFFERENT cables —
# the mismatched open that corrupts router hop tables. These are the
# invariants that make that unreachable.
# ---------------------------------------------------------------------------

def _resolution(cable, netdev, addr, peer, in_hopid, out_hopid, index,
                service, domain, nhi, **extra):
    """A synthetic resolve_stream_device() result, as either node would see it."""
    data = {
        "ok": True, "cable": cable, "rail": netdev, "rail_addr": addr,
        "peer_addr": peer, "in_hopid": in_hopid, "out_hopid": out_hopid,
        "index": index, "dev": "/dev/" + DEVICE_WORD + str(index),
        "configfs_group": service, "domain": domain, "nhi": nhi,
        "ring_size": "1024", "throttling": "2048",
    }
    data.update(extra)
    return data


# The four resolutions actually observed on the twins on 2026-08-30, which is
# what makes the cross-cable cases below real rather than imagined. Note two
# facts the guard is built around: the device INDEX is equal on both nodes for
# a given cable (so index agreement proves nothing), and the configfs service
# basenames CROSS between the nodes (so basename agreement would be wrong).
COORD_A = _resolution("A", "thunderbolt0", "10.99.0.1", "10.99.0.2",
                      "9", "9", 2, "1-2.1", "domain1", "0000:c5:00.6")
WORKER_A = _resolution("A", "thunderbolt0", "10.99.0.2", "10.99.0.1",
                       "9", "9", 2, "0-2.1", "domain0", "0000:c4:00.5")
COORD_B = _resolution("B", "thunderbolt1", "169.254.17.133", "169.254.53.173",
                      "9", "8", 0, "0-2.1", "domain0", "0000:c5:00.5")
WORKER_B = _resolution("B", "thunderbolt1", "169.254.53.173", "169.254.17.133",
                       "8", "9", 0, "1-2.1", "domain1", "0000:c4:00.6")


class CableSelection(unittest.TestCase):
    def test_cable_a_is_the_default_so_absent_flags_change_nothing(self):
        self.assertEqual(BENCH.DEFAULT_CABLE, BENCH.CABLE_A)
        self.assertEqual(BENCH.CABLES, ("A", "B"))
        self.assertEqual(BENCH.run_coordinator.__defaults__,
                         (BENCH.CABLE_A, BENCH.DEFAULT_FUNCTION))
        self.assertEqual(BENCH.resolve_stream_device.__defaults__,
                         (BENCH.CABLE_A, BENCH.DEFAULT_FUNCTION))
        # fn0 stays the default: naming no --function changes nothing either.
        self.assertEqual(BENCH.DEFAULT_FUNCTION, "fn0")
        self.assertEqual(BENCH.FUNCTIONS, ("fn0", "fn1"))

    def test_the_flag_parses_and_defaults_to_a(self):
        import argparse
        parser = argparse.ArgumentParser()
        parser.add_argument("--cable", choices=BENCH.CABLES,
                            default=BENCH.DEFAULT_CABLE)
        self.assertEqual(parser.parse_args([]).cable, "A")
        self.assertEqual(parser.parse_args(["--cable", "B"]).cable, "B")

    def test_no_netdev_name_service_basename_or_index_is_hardcoded(self):
        """The rule must be a rule. Any literal here selects the WRONG cable on
        one of the two boxes, because the basenames cross between them."""
        src = BENCH_PATH.read_text()
        rule = src.split("def cable_netdev", 1)[1].split("\ndef ", 1)[0]
        # The prose may quote the observed basenames to explain WHY they must
        # not be used; the executable rule may not contain them.
        rule = rule.split('"""', 2)[-1]
        for banned in ("thunderbolt0", "thunderbolt1", "0-2.1", "1-2.1"):
            self.assertNotIn(banned, rule,
                             f"cable_netdev names {banned!r}; selection must "
                             "be derived per node, not named")

    def test_cable_selection_travels_in_argv_to_both_remote_roles(self):
        """An env var does not survive ssh. A one-sided switch is the wedge."""
        recorded = {}

        class _Fake:
            stdin = None
            def __init__(self, *a, **k):
                pass

        def fake_run(argv, **kwargs):
            recorded["probe"] = argv[-1]
            class R:
                stdout = b'{"ok": false}'
                stderr = b""
                returncode = 0
            return R()

        class FakePopen:
            def __init__(self, argv, **kwargs):
                recorded["peer"] = argv[-1]
                import io
                self.stdin = io.BytesIO()

        real_run, real_popen = BENCH.subprocess.run, BENCH.subprocess.Popen
        try:
            BENCH.subprocess.run = fake_run
            BENCH.subprocess.Popen = FakePopen
            BENCH.probe_peer(b"src", "B")
            BENCH.launch_peer(b"src", "/dev/" + DEVICE_WORD + "0", "B")
        finally:
            BENCH.subprocess.run, BENCH.subprocess.Popen = real_run, real_popen

        self.assertIn("--role probe", recorded["probe"])
        self.assertIn("--cable B", recorded["probe"])
        self.assertIn("--role peer", recorded["peer"])
        self.assertIn("--cable B", recorded["peer"])


class PeerReachability(unittest.TestCase):
    """The old check derived the peer by swapping the last octet of a /30.

    Off the /30 that is not a peer derivation, it is arithmetic — and a check
    that can be satisfied without a second machine is not a safety check.
    """

    def test_the_octet_swap_is_meaningless_off_the_rail_net(self):
        swapped = BENCH.rail_peer_address("169.254.17.133")
        self.assertEqual(swapped, "169.254.17.1")
        self.assertNotEqual(swapped, "169.254.53.173",
                            "the swap does not name the cable-B peer")

    def test_an_address_this_node_holds_can_never_satisfy_the_check(self):
        real = BENCH.local_addresses
        try:
            BENCH.local_addresses = lambda: {"169.254.17.133"}
            report = BENCH.peer_reachable("tbnet", "169.254.17.133", "aa:bb")
        finally:
            BENCH.local_addresses = real
        self.assertFalse(report["reachable"])
        self.assertIn("THIS node", report["error"])

    def test_a_missing_peer_address_is_a_refusal_not_a_pass(self):
        self.assertFalse(BENCH.peer_reachable("tbnet", None, "aa:bb")["reachable"])

    def test_our_own_link_layer_address_is_not_a_peer(self):
        real_local, real_neigh, real_run = (BENCH.local_addresses,
                                            BENCH.neighbours, BENCH.subprocess.run)
        try:
            BENCH.local_addresses = lambda: set()
            BENCH.neighbours = lambda dev: [("169.254.1.1", "aa:bb", "REACHABLE")]
            BENCH.subprocess.run = lambda *a, **k: type("R", (), {"returncode": 0})()
            report = BENCH.peer_reachable("tbnet", "169.254.1.1", "aa:bb")
        finally:
            (BENCH.local_addresses, BENCH.neighbours,
             BENCH.subprocess.run) = real_local, real_neigh, real_run
        self.assertFalse(report["reachable"])
        self.assertIn("OWN link-layer", report["error"])


class CableMismatchGuard(unittest.TestCase):
    """Before any open, both ends must be proven to be on the SAME cable."""

    def test_the_real_same_cable_pairings_agree(self):
        self.assertIsNone(BENCH.cable_disagreement(COORD_A, WORKER_A))
        self.assertIsNone(BENCH.cable_disagreement(COORD_B, WORKER_B))

    def test_every_cross_cable_pairing_is_refused(self):
        for local, peer in ((COORD_A, WORKER_B), (COORD_B, WORKER_A)):
            self.assertIsNotNone(BENCH.cable_disagreement(local, peer))

    def test_the_hopid_interlock_catches_a_cross_cable_pair_on_its_own(self):
        """Witness 2 must not depend on the label: forge the labels to agree
        and the router's own view of the path still refuses."""
        for local, peer in ((COORD_A, WORKER_B), (COORD_B, WORKER_A)):
            forged_local = dict(local, cable="X")
            forged_peer = dict(peer, cable="X")
            reason = BENCH.cable_disagreement(forged_local, forged_peer)
            self.assertIsNotNone(reason)
            self.assertIn("hopid interlock", reason)

    def test_the_index_is_never_what_agreement_rests_on(self):
        """Both nodes report the SAME index for a given cable, so an index
        comparison would wave a cross-cable pairing through."""
        self.assertEqual(COORD_A["index"], WORKER_A["index"])
        self.assertEqual(COORD_B["index"], WORKER_B["index"])
        crossed = BENCH.cable_disagreement(dict(COORD_B, cable="X"),
                                           dict(WORKER_A, cable="X",
                                                index=COORD_B["index"]))
        self.assertIsNotNone(crossed,
                             "equal indices must not make a cross-cable pair agree")

    def test_the_service_basenames_cross_and_are_not_compared(self):
        self.assertEqual(COORD_A["configfs_group"], WORKER_B["configfs_group"])
        self.assertEqual(COORD_B["configfs_group"], WORKER_A["configfs_group"])
        self.assertIsNone(BENCH.cable_disagreement(COORD_A, WORKER_A),
                          "differing basenames on the same cable must still agree")

    def test_wire_peers_that_do_not_face_each_other_are_refused(self):
        reason = BENCH.cable_disagreement(
            COORD_B, dict(WORKER_B, rail_addr="169.254.99.99"))
        self.assertIsNotNone(reason)
        self.assertIn("wire peer mismatch", reason)

    def test_a_failed_peer_probe_is_a_refusal(self):
        self.assertIsNotNone(BENCH.cable_disagreement(COORD_B, {"ok": False}))

    def test_the_mismatch_skip_is_typed_and_passes_the_receipts_bound(self):
        self.assertTrue(BENCH.SKIP_CABLE_MISMATCH.startswith("skipped:"))
        self.assertIn(BENCH.SKIP_CABLE_MISMATCH, BENCH_PATH.read_text())
        self.assertIsNone(VERIFY.BOUNDS["usb4stream"](
            {"outcome": BENCH.SKIP_CABLE_MISMATCH, "serve_up": True}))


class ServePreconditionIsCableAware(unittest.TestCase):
    """The serve check exists because a wedge would take the serve down, not
    because a serve exists. On cable A those coincide; on cable B they do not,
    and the difference is decided on hardware rather than on a label."""

    def test_cable_a_is_blocked_unconditionally_and_the_note_is_unchanged(self):
        base = {}
        self.assertTrue(BENCH.serve_blocks_run(BENCH.CABLE_A, base))
        self.assertIn("shares cable A with the serving rails", base["note"])

    def test_cable_b_is_blocked_when_it_shares_the_serving_router(self):
        real_resolve, real_rail, real_router = (BENCH.resolve_stream_device,
                                                BENCH.rail_netdev,
                                                BENCH.router_identity)
        try:
            BENCH.resolve_stream_device = lambda c, f=BENCH.DEFAULT_FUNCTION: {"domain": "domain1",
                                                     "nhi": "0000:c5:00.6"}
            BENCH.rail_netdev = lambda: ("tbnet", "10.99.0.1")
            BENCH.router_identity = lambda p: ("domain1", "0000:c5:00.6")
            base = {}
            self.assertTrue(BENCH.serve_blocks_run(BENCH.CABLE_B, base))
            self.assertIn("shared", base["note"])
        finally:
            (BENCH.resolve_stream_device, BENCH.rail_netdev,
             BENCH.router_identity) = real_resolve, real_rail, real_router

    def test_cable_b_proceeds_only_on_a_demonstrably_disjoint_router(self):
        real_resolve, real_rail, real_router = (BENCH.resolve_stream_device,
                                                BENCH.rail_netdev,
                                                BENCH.router_identity)
        try:
            BENCH.resolve_stream_device = lambda c, f=BENCH.DEFAULT_FUNCTION: {"domain": "domain0",
                                                     "nhi": "0000:c5:00.5"}
            BENCH.rail_netdev = lambda: ("tbnet", "10.99.0.1")
            BENCH.router_identity = lambda p: ("domain1", "0000:c5:00.6")
            base = {}
            self.assertFalse(BENCH.serve_blocks_run(BENCH.CABLE_B, base))
            self.assertFalse(base["serve_shares_bench_hardware"])
        finally:
            (BENCH.resolve_stream_device, BENCH.rail_netdev,
             BENCH.router_identity) = real_resolve, real_rail, real_router

    def test_unmeasurable_hardware_is_treated_as_shared(self):
        real_resolve, real_rail, real_router = (BENCH.resolve_stream_device,
                                                BENCH.rail_netdev,
                                                BENCH.router_identity)
        try:
            BENCH.resolve_stream_device = lambda c, f=BENCH.DEFAULT_FUNCTION: {"domain": None, "nhi": None}
            BENCH.rail_netdev = lambda: ("tbnet", "10.99.0.1")
            BENCH.router_identity = lambda p: (None, None)
            base = {}
            self.assertTrue(BENCH.serve_blocks_run(BENCH.CABLE_B, base))
        finally:
            (BENCH.resolve_stream_device, BENCH.rail_netdev,
             BENCH.router_identity) = real_resolve, real_rail, real_router


class DryRunCannotArmTheIdempotenceGuard(unittest.TestCase):
    """A spurious skip receipt is not a small problem: it arms the guard
    against EVERY future run. --dry-run exists so the whole precondition chain
    can be rehearsed with no way to bank one."""

    def test_the_precondition_chain_opens_nothing(self):
        import inspect
        chain = inspect.getsource(BENCH.coordinator_preconditions)
        for banned in ("open_stream_once", "os.open", "write_receipt"):
            self.assertNotIn(banned, chain)

    def test_the_dry_run_never_writes_a_receipt(self):
        import inspect
        dry = inspect.getsource(BENCH.run_dry_run)
        for banned in ("write_receipt", "open_stream_once", "launch_peer"):
            self.assertNotIn(banned, dry)

    def test_skips_carry_their_evidence_so_the_receipt_is_readable(self):
        skip = BENCH.Skip(BENCH.SKIP_CABLE_MISMATCH, {"serve_up": True})
        self.assertEqual(skip.outcome, BENCH.SKIP_CABLE_MISMATCH)
        self.assertEqual(skip.base, {"serve_up": True})


if __name__ == "__main__":
    unittest.main()
