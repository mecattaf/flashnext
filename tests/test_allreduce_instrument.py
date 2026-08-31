"""fn_allreduce_timer against a fake communicator.

The instrument is the only artifact that can answer "is decode
allreduce-dominated" (RUN3-BRIEF section 10.1), and it runs inside the serve
process, where a mistake is expensive: a probe that raises takes the engine
down, and a probe that syncs the device changes the thing it is measuring
(section 14.6). So this suite exercises the module directly against a fake
communicator -- no torch, no vllm, no device -- and covers the three claims
section 13.7 makes: the fired/declined counter, the capture state sampled at
the call site, and staging time split from wire time.

Timing assertions use a deliberately slack tolerance against a real sleep:
what is under test is that the right interval lands in the right column, not
the clock.
"""

import importlib.util
import json
import os
import pathlib
import py_compile
import tempfile
import time
import types
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parent.parent
MODULE = ROOT / "container" / "rootfs" / "fn_allreduce_timer.py"

# One fake collective blocks the caller for this long; assertions allow a
# generous shortfall so a coarse clock or a preempted thread cannot fail them.
SLEEP_S = 0.01
FLOOR_US = 3000.0


def load(**env):
    """A fresh module instance under a patched env (config is read at import)."""
    env.setdefault("FN_AR_OUT", tempfile.mkdtemp(prefix="fn-ar-"))
    with mock.patch.dict(os.environ, env, clear=False):
        spec = importlib.util.spec_from_file_location("fn_allreduce_timer", MODULE)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    return mod


def enabled(**env):
    env.setdefault("FN_ALLREDUCE_TIMER", "1")
    env.setdefault("FN_AR_START", "0")
    return load(**env)


class FakeCustom:
    """Stands in for CustomAllreduce / QuickAllReduce / the symm-mem path."""

    def __init__(self, eligible=True, disabled=False, raises=False):
        self.eligible = eligible
        self.disabled = disabled
        self.raises = raises
        self.asked = []

    def should_custom_ar(self, tensor):
        if self.raises:
            raise RuntimeError("eligibility probe exploded")
        self.asked.append(tensor)
        return self.eligible


class FakeComm:
    """The communicator under instrumentation."""

    def __init__(self, ca_comm=None, sleep=0.0, raises=None):
        self.sleep = sleep
        self.raises = raises
        self.seen = []
        if ca_comm is not None:
            self.ca_comm = ca_comm

    def all_reduce(self, input_, *a, **kw):
        self.seen.append((input_, a, kw))
        if self.sleep:
            time.sleep(self.sleep)
        if self.raises is not None:
            raise self.raises
        return ("reduced", input_)


class FakeLayer:
    """A vLLM-shaped caller: it carries `prefix`, which is how the walk
    attributes the collective to a layer index without touching a tensor."""

    def __init__(self, comm, prefix, layer_type=None):
        self.comm = comm
        self.prefix = prefix
        if layer_type is not None:
            self.layer_type = layer_type

    def forward(self, x):
        return self.comm.all_reduce(x)


class ExplodingLayer:
    @property
    def prefix(self):
        raise RuntimeError("prefix exploded")

    def __init__(self, comm):
        self.comm = comm

    def forward(self, x):
        return self.comm.all_reduce(x)


def fake_torch(capturing=True, raises=False):
    cuda = types.SimpleNamespace(
        is_current_stream_capturing=(
            _boom if raises else (lambda: capturing)))
    return types.SimpleNamespace(cuda=cuda, distributed=None)


def _boom():
    raise RuntimeError("capture probe exploded")


class OffByDefault(unittest.TestCase):
    def test_disabled_unless_the_kill_switch_is_one(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            mod = load()
        self.assertFalse(mod.ENABLED)

    def test_install_wraps_nothing_when_off(self):
        mod = load(FN_ALLREDUCE_TIMER="0")
        comm = FakeComm()
        self.assertFalse(mod.install(comm))
        # Not "cheap when off" -- absent. Nothing shadows the bound method.
        self.assertNotIn("all_reduce", vars(comm))
        self.assertEqual(comm.all_reduce(1), ("reduced", 1))
        self.assertEqual(mod.summary()["window"]["recorded"], 0)

    def test_env_surface_is_the_documented_one(self):
        mod = load()
        self.assertEqual(mod.ENABLE_ENV, "FN_ALLREDUCE_TIMER")
        self.assertEqual(mod.START_ENV, "FN_AR_START")
        self.assertEqual(mod.CALLS_ENV, "FN_AR_CALLS")
        self.assertEqual(mod.OUT_ENV, "FN_AR_OUT")
        self.assertEqual(mod.INTERVAL_ENV, "FN_AR_FULL_ATTENTION_INTERVAL")
        text = MODULE.read_text()
        for env in (mod.ENABLE_ENV, mod.START_ENV, mod.CALLS_ENV, mod.OUT_ENV,
                    mod.INTERVAL_ENV, mod.DEPTH_ENV):
            self.assertIn(env, text)

    def test_window_bounds_come_from_the_env(self):
        mod = load(FN_AR_START="7", FN_AR_CALLS="11",
                   FN_AR_FULL_ATTENTION_INTERVAL="6", FN_AR_FRAME_DEPTH="3")
        self.assertEqual((mod.START, mod.CALLS, mod.INTERVAL, mod.DEPTH),
                         (7, 11, 6, 3))
        # Garbage falls back to the default rather than raising at import.
        mod = load(FN_AR_START="not-a-number", FN_AR_CALLS="")
        self.assertEqual((mod.START, mod.CALLS), (200, 20000))


class LayerClassification(unittest.TestCase):
    def test_the_48_layer_split_is_12_qsa_and_36_gdn(self):
        mod = load()
        kinds = [mod.classify_layer(i) for i in range(48)]
        self.assertEqual(kinds.count(mod.QSA), 12)
        self.assertEqual(kinds.count(mod.GDN), 36)
        # full_attention_interval 4: layer 3 is the first full-attention layer.
        self.assertEqual(kinds[:4], [mod.GDN, mod.GDN, mod.GDN, mod.QSA])

    def test_the_interval_is_configurable(self):
        mod = load(FN_AR_FULL_ATTENTION_INTERVAL="2")
        self.assertEqual([mod.classify_layer(i) for i in range(4)],
                         [mod.GDN, mod.QSA, mod.GDN, mod.QSA])
        self.assertEqual(mod.classify_layer(3, interval=8), mod.GDN)

    def test_degenerate_input_is_unknown_not_a_guess(self):
        mod = load()
        for bad in (None, -1, "3", 3.0):
            self.assertEqual(mod.classify_layer(bad), mod.UNKNOWN)
        self.assertEqual(mod.classify_layer(3, interval=0), mod.UNKNOWN)


class WrapIsTransparent(unittest.TestCase):
    def setUp(self):
        self.mod = enabled()
        self.addCleanup(self.mod.uninstall)

    def test_the_result_and_the_arguments_pass_through(self):
        comm = FakeComm()
        self.assertTrue(self.mod.wrap(comm))
        out = comm.all_reduce("t", 1, key="v")
        self.assertEqual(out, ("reduced", "t"))
        self.assertEqual(comm.seen, [("t", (1,), {"key": "v"})])

    def test_wrapping_twice_is_a_no_op(self):
        comm = FakeComm()
        self.assertTrue(self.mod.wrap(comm))
        wrapped = comm.all_reduce
        self.assertFalse(self.mod.wrap(comm))
        self.assertIs(comm.all_reduce, wrapped)

    def test_uninstall_restores_the_original(self):
        comm = FakeComm()
        self.mod.wrap(comm)
        self.mod.uninstall()
        self.assertNotIn("all_reduce", vars(comm))
        self.assertEqual(comm.all_reduce(1), ("reduced", 1))

    def test_the_collectives_exception_is_not_swallowed(self):
        boom = ValueError("collective failed")
        comm = FakeComm(raises=boom)
        self.mod.wrap(comm)
        with self.assertRaises(ValueError):
            comm.all_reduce("t")


class ThreeWaySplit(unittest.TestCase):
    """detect / exchange / compute-between -- section 14.15 step 1."""

    def setUp(self):
        self.mod = enabled()
        self.addCleanup(self.mod.uninstall)

    def test_wire_time_lands_in_exchange_not_in_detect(self):
        comm = FakeComm(ca_comm=FakeCustom(), sleep=SLEEP_S)
        self.mod.wrap(comm)
        comm.all_reduce("t")
        kind, detect, exchange, compute, _, _ = self.mod._rows[0]
        self.assertGreaterEqual(exchange * 1e6, FLOOR_US)
        self.assertLess(detect, exchange)
        # Nothing preceded this collective, so there is no gap to report --
        # None, never a fabricated zero.
        self.assertIsNone(compute)

    def test_the_gap_between_collectives_is_compute(self):
        comm = FakeComm()
        self.mod.wrap(comm)
        comm.all_reduce("a")
        time.sleep(SLEEP_S)
        comm.all_reduce("b")
        self.assertGreaterEqual(self.mod._rows[1][3] * 1e6, FLOOR_US)
        totals = self.mod.summary()["totals_us"]
        self.assertGreaterEqual(totals["compute"], FLOOR_US)

    def test_the_share_is_the_headline_and_sums_to_one(self):
        comm = FakeComm(sleep=SLEEP_S)
        self.mod.wrap(comm)
        for _ in range(3):
            comm.all_reduce("t")
        share = self.mod.summary()["share"]
        self.assertAlmostEqual(sum(share.values()), 1.0, places=3)
        # The fake spends its time inside the collective, so exchange must
        # dominate -- the shape of the section 14.1 comparison.
        self.assertGreater(share["exchange"], share["compute"])
        self.assertGreater(share["exchange"], share["detect"])

    def test_an_empty_window_summarises_without_dividing_by_zero(self):
        s = self.mod.summary()
        self.assertEqual(s["window"]["recorded"], 0)
        self.assertIsNone(s["share"]["exchange"])
        for kind in self.mod.BUCKETS:
            self.assertEqual(s["by_layer_type"][kind]["collectives"], 0)
        self.assertIn("no collectives recorded", self.mod.report())


class LayerBuckets(unittest.TestCase):
    def setUp(self):
        self.mod = enabled()
        self.addCleanup(self.mod.uninstall)

    def counts(self):
        return {k: v["collectives"]
                for k, v in self.mod.summary()["by_layer_type"].items()}

    def test_collectives_bucket_by_layer_index(self):
        comm = FakeComm()
        self.mod.wrap(comm)
        FakeLayer(comm, "model.layers.3.self_attn.o_proj").forward("t")
        FakeLayer(comm, "model.layers.2.mlp.down_proj").forward("t")
        FakeLayer(comm, "model.layers.7.self_attn.o_proj").forward("t")
        self.assertEqual(self.counts(),
                         {self.mod.QSA: 2, self.mod.GDN: 1,
                          self.mod.UNKNOWN: 0})

    def test_a_declared_layer_type_beats_the_interval(self):
        # A checkpoint whose layer_types disagree with the interval must be
        # believed: the interval is only the fallback.
        comm = FakeComm()
        self.mod.wrap(comm)
        FakeLayer(comm, "model.layers.3.mixer",
                  layer_type="linear_attention").forward("t")
        FakeLayer(comm, "model.layers.2.mixer",
                  layer_type="full_attention").forward("t")
        self.assertEqual(self.counts(),
                         {self.mod.QSA: 1, self.mod.GDN: 1,
                          self.mod.UNKNOWN: 0})

    def test_an_unattributable_collective_gets_its_own_column(self):
        comm = FakeComm()
        self.mod.wrap(comm)
        comm.all_reduce("t")  # called from a frame that owns no layer
        self.assertEqual(self.counts()[self.mod.UNKNOWN], 1)

    def test_attribution_is_memoised_per_layer_object(self):
        comm = FakeComm()
        self.mod.wrap(comm)
        layer = FakeLayer(comm, "model.layers.3.self_attn.o_proj")
        for _ in range(4):
            layer.forward("t")
        self.assertEqual(self.counts()[self.mod.QSA], 4)
        self.assertIn(id(layer), self.mod._site_cache)

    def test_the_walk_is_bounded_by_the_frame_depth(self):
        mod = enabled(FN_AR_FRAME_DEPTH="1")
        self.addCleanup(mod.uninstall)
        comm = FakeComm()
        mod.wrap(comm)
        # forward() is one frame up and owns the layer, so depth 1 finds it.
        FakeLayer(comm, "model.layers.3.self_attn").forward("t")
        self.assertEqual(mod.summary()["by_layer_type"][mod.QSA]["collectives"], 1)


class CustomPathCounter(unittest.TestCase):
    """(a) of section 13.7 -- what separates 'inert' from 'slow'."""

    def setUp(self):
        self.mod = enabled()
        self.addCleanup(self.mod.uninstall)

    def tally(self):
        return self.mod.summary()["custom_path"]

    def test_an_eligible_custom_path_counts_as_fired(self):
        ca = FakeCustom(eligible=True)
        comm = FakeComm(ca_comm=ca)
        self.mod.wrap(comm)
        comm.all_reduce("t")
        self.assertEqual(self.tally()["true"], 1)
        self.assertEqual(ca.asked, ["t"])
        self.assertEqual(self.tally()["candidates"], ["ca_comm"])

    def test_an_ineligible_custom_path_counts_as_declined(self):
        comm = FakeComm(ca_comm=FakeCustom(eligible=False))
        self.mod.wrap(comm)
        comm.all_reduce("t")
        self.assertEqual(self.tally()["false"], 1)
        self.assertEqual(self.tally()["true"], 0)

    def test_a_disabled_custom_path_counts_as_declined(self):
        comm = FakeComm(ca_comm=FakeCustom(eligible=True, disabled=True))
        self.mod.wrap(comm)
        comm.all_reduce("t")
        self.assertEqual(self.tally()["false"], 1)

    def test_no_custom_path_at_all_is_unknown_not_declined(self):
        comm = FakeComm()
        self.mod.wrap(comm)
        comm.all_reduce("t")
        self.assertEqual(self.tally()["unknown"], 1)
        self.assertEqual(self.tally()["candidates"], [])

    def test_a_probe_that_raises_degrades_to_unknown(self):
        comm = FakeComm(ca_comm=FakeCustom(raises=True))
        self.mod.wrap(comm)
        self.assertEqual(comm.all_reduce("t"), ("reduced", "t"))
        self.assertEqual(self.tally()["unknown"], 1)
        self.assertEqual(self.mod.summary()["window"]["recorded"], 1)


class CaptureState(unittest.TestCase):
    """(b) of section 13.7 -- sampled AT THE CALL SITE, per collective."""

    def setUp(self):
        self.mod = enabled()
        self.addCleanup(self.mod.uninstall)

    def test_the_capture_flag_is_sampled_per_collective(self):
        comm = FakeComm()
        self.mod.wrap(comm)
        state = {"v": True}
        self.mod.torch = fake_torch()
        self.mod.torch.cuda.is_current_stream_capturing = lambda: state["v"]
        comm.all_reduce("t")
        state["v"] = False
        comm.all_reduce("t")
        self.assertEqual(self.mod.summary()["capturing"],
                         {"true": 1, "false": 1, "unknown": 0})

    def test_capture_state_is_bucketed_with_its_layer(self):
        comm = FakeComm()
        self.mod.wrap(comm)
        self.mod.torch = fake_torch(capturing=True)
        FakeLayer(comm, "model.layers.3.self_attn").forward("t")
        by = self.mod.summary()["by_layer_type"][self.mod.QSA]
        self.assertEqual(by["capturing"]["true"], 1)

    def test_a_capture_probe_that_raises_degrades_to_unknown(self):
        comm = FakeComm()
        self.mod.wrap(comm)
        self.mod.torch = fake_torch(raises=True)
        self.assertEqual(comm.all_reduce("t"), ("reduced", "t"))
        self.assertEqual(self.mod.summary()["capturing"]["unknown"], 1)

    def test_without_torch_the_flag_is_unknown_and_nothing_breaks(self):
        comm = FakeComm()
        self.mod.torch = None
        self.mod.wrap(comm)
        comm.all_reduce("t")
        self.assertEqual(self.mod.summary()["capturing"]["unknown"], 1)


class TheWindow(unittest.TestCase):
    def test_the_warmup_prefix_is_skipped_but_still_counted(self):
        mod = enabled(FN_AR_START="2")
        self.addCleanup(mod.uninstall)
        comm = FakeComm()
        mod.wrap(comm)
        for _ in range(3):
            comm.all_reduce("t")
        window = mod.summary()["window"]
        self.assertEqual((window["seen"], window["recorded"]), (3, 1))

    def test_the_window_closes_and_the_wrapper_gets_out_of_the_way(self):
        out = tempfile.mkdtemp(prefix="fn-ar-")
        mod = enabled(FN_AR_CALLS="2", FN_AR_OUT=out)
        self.addCleanup(mod.uninstall)
        comm = FakeComm()
        mod.wrap(comm)
        for _ in range(5):
            self.assertEqual(comm.all_reduce("t"), ("reduced", "t"))
        self.assertEqual(mod.summary()["window"]["recorded"], 2)
        self.assertTrue(mod.summary()["window"]["closed"])
        # A full window banks its receipt without waiting for shutdown.
        self.assertTrue(os.path.isfile(os.path.join(out, "allreduce.json")))
        self.assertEqual(len(comm.seen), 5)

    def test_reset_reopens_the_window(self):
        mod = enabled(FN_AR_CALLS="1")
        self.addCleanup(mod.uninstall)
        comm = FakeComm()
        mod.wrap(comm)
        comm.all_reduce("t")
        self.assertTrue(mod.summary()["window"]["closed"])
        mod.reset()
        comm.all_reduce("t")
        self.assertEqual(mod.summary()["window"]["recorded"], 1)


class NeverRaisesIntoTheServePath(unittest.TestCase):
    def setUp(self):
        self.mod = enabled()
        self.addCleanup(self.mod.uninstall)

    def test_a_layer_whose_attribute_explodes_still_serves(self):
        comm = FakeComm()
        self.mod.wrap(comm)
        self.assertEqual(ExplodingLayer(comm).forward("t"), ("reduced", "t"))
        self.assertEqual(
            self.mod.summary()["by_layer_type"][self.mod.UNKNOWN]["collectives"],
            1)

    def test_a_recorder_that_explodes_still_serves(self):
        comm = FakeComm()
        self.mod.wrap(comm)
        with mock.patch.object(self.mod, "_record",
                               side_effect=RuntimeError("recorder exploded")):
            self.assertEqual(comm.all_reduce("t"), ("reduced", "t"))
        self.assertEqual(self.mod.summary()["window"]["recorded"], 0)

    def test_a_failed_record_does_not_inflate_the_next_gap(self):
        # The cursor must advance even when recording fails, or the next
        # collective's compute column absorbs the dropped one's wire time.
        comm = FakeComm(sleep=SLEEP_S)
        self.mod.wrap(comm)
        comm.all_reduce("a")
        with mock.patch.object(self.mod, "_record",
                               side_effect=RuntimeError("recorder exploded")):
            comm.all_reduce("b")
        comm.all_reduce("c")
        self.assertEqual(self.mod.summary()["window"]["recorded"], 2)
        self.assertLess(self.mod._rows[1][3] * 1e6, FLOOR_US)

    def test_an_unwritable_receipt_target_is_reported_not_raised(self):
        comm = FakeComm()
        self.mod.wrap(comm)
        comm.all_reduce("t")
        with tempfile.NamedTemporaryFile() as fh:
            self.assertIsNone(
                self.mod.flush(path=os.path.join(fh.name, "allreduce.json")))

    def test_install_without_vllm_declines_quietly(self):
        self.assertFalse(self.mod.install())


class TheReceipt(unittest.TestCase):
    def setUp(self):
        self.out = tempfile.mkdtemp(prefix="fn-ar-")
        self.mod = enabled(FN_AR_OUT=self.out)
        self.addCleanup(self.mod.uninstall)
        self.comm = FakeComm(ca_comm=FakeCustom(eligible=False), sleep=SLEEP_S)
        self.mod.wrap(self.comm)
        FakeLayer(self.comm, "model.layers.3.self_attn.o_proj").forward("t")
        FakeLayer(self.comm, "model.layers.2.mlp.down_proj").forward("t")

    def read(self, path):
        return json.loads(pathlib.Path(path).read_text())

    def test_the_receipt_is_a_campaign_receipt(self):
        path = self.mod.flush()
        receipt = self.read(path)
        # The shape scripts/receipts-verify.py grades: step, status, ts, data.
        self.assertEqual(receipt["step"], "allreduce")
        self.assertEqual(receipt["status"], "pass")
        self.assertTrue(receipt["ts"])
        for key in ("measures", "window", "totals_us", "share",
                    "by_layer_type", "custom_path", "capturing", "rank"):
            self.assertIn(key, receipt["data"])

    def test_the_receipt_states_that_no_device_sync_backs_the_numbers(self):
        data = self.read(self.mod.flush())["data"]
        self.assertIn("host-side", data["measures"])
        self.assertIn("no device sync", data["measures"])

    def test_the_receipt_carries_both_layer_buckets(self):
        by = self.read(self.mod.flush())["data"]["by_layer_type"]
        self.assertEqual(set(by), {"qsa", "gdn", "unknown"})
        self.assertEqual(by["qsa"]["collectives"], 1)
        self.assertEqual(by["gdn"]["collectives"], 1)
        self.assertGreaterEqual(by["qsa"]["exchange_us"]["p50"], FLOOR_US)
        self.assertEqual(by["gdn"]["custom_path"]["false"], 1)

    def test_it_lands_under_the_named_output_directory(self):
        self.assertEqual(self.mod.flush(),
                         os.path.join(self.out, "allreduce.json"))

    def test_rank_zero_owns_the_canonical_name(self):
        self.assertTrue(self.mod.receipt_path(rank=0).endswith("allreduce.json"))
        self.assertTrue(
            self.mod.receipt_path(rank=1).endswith("allreduce-rank1.json"))

    def test_an_unchanged_measurement_rewrites_byte_identically(self):
        # Checkpoint purity: a fresh timestamp on unchanged facts is not a
        # change, so a re-run must leave the file alone.
        path = self.mod.flush()
        before = pathlib.Path(path).read_bytes()
        with mock.patch.object(self.mod.time, "strftime",
                               return_value="2099-01-01T00:00:00Z"):
            self.assertEqual(self.mod.flush(), path)
        self.assertEqual(pathlib.Path(path).read_bytes(), before)

    def test_a_changed_measurement_does_rewrite(self):
        path = self.mod.flush()
        before = pathlib.Path(path).read_bytes()
        self.comm.all_reduce("t")
        self.mod.flush()
        self.assertNotEqual(pathlib.Path(path).read_bytes(), before)

    def test_the_operator_table_names_the_split(self):
        text = self.mod.report()
        self.assertIn("ALLREDUCE SPLIT", text)
        self.assertIn("qsa", text)
        self.assertIn("gdn", text)
        self.assertIn("share:", text)


class SourceDiscipline(unittest.TestCase):
    """The offline half: what the module may not contain."""

    def setUp(self):
        self.text = MODULE.read_text()

    def test_the_module_is_in_the_container_overlay(self):
        self.assertTrue(MODULE.is_file())
        with tempfile.TemporaryDirectory() as td:
            py_compile.compile(str(MODULE),
                               cfile=str(pathlib.Path(td) / "fn.pyc"),
                               doraise=True)

    def test_no_device_sync_in_the_hot_path(self):
        # The acceptance gate greps for exactly this; a sync in the decode
        # loop changes the thing being measured (section 14.6).
        for needle in ("synchronize(", "cudaDeviceSynchronize",
                       "hipDeviceSynchronize"):
            self.assertNotIn(needle, self.text)

    def test_no_blocking_device_to_host_read(self):
        # The other way to serialise the step: a D2H copy on the tensor.
        for needle in (".item()", ".tolist()", ".cpu()", ".numpy()",
                       "nonzero()"):
            self.assertNotIn(needle, self.text)

    def test_it_times_with_perf_counter(self):
        self.assertIn("perf_counter", self.text)

    def test_it_imports_without_torch(self):
        # This suite runs on a host with no torch; reaching here at all proves
        # the import degrades, and the module records that it did.
        mod = load()
        self.assertTrue(hasattr(mod, "torch"))


if __name__ == "__main__":
    unittest.main()
