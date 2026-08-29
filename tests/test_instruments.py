"""Instrument overlay tests — compile, notices, and budget invariants.

The offline half for container/rootfs/fn_*.py: py_compile each module (cfile
into a tempdir, so no bytecode lands in the overlay), check every adapted
module carries its upstream notice header, and check fn_offload_batch's
budget arithmetic against the reference invariants (floor at the max group
size; promote counted in blocks). fn_offload_batch is torch-free by design,
so that module alone is imported and exercised directly; the other two are
checked at the text/compile level so this suite runs on a host without
torch or vllm.
"""

import importlib.util
import os
import pathlib
import py_compile
import subprocess
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parent.parent
ROOTFS = ROOT / "container" / "rootfs"

# name -> (upstream file the notice must cite, env vars the module must expose)
MODULES = {
    "fn_synctrace.py": ("ds4_synctrace.py", ("FN_PROFILE",)),
    "fn_offload_batch.py": (
        "ds4_offload_batch.py",
        ("FN_OFFLOAD_STORE_BATCH_FRAC", "FN_OFFLOAD_PROMOTE_FRAC"),
    ),
    "fn_expert_union.py": (
        "ds4_expert_union.py",
        ("FN_EXPERT_UNION", "FN_EU_START", "FN_EU_CALLS", "FN_EU_OUT"),
    ),
}

# Stock env names the rename must have retired; none may survive in an
# adapted module (the notice headers cite file names like ds4_synctrace.py,
# which do not contain these).
_RETIRED = ("DS4_PROFILE", "DS4_OFFLOAD_STORE_BATCH_FRAC",
            "DS4_OFFLOAD_PROMOTE_FRAC", "DS4_EXPERT_UNION", "DS4_EU_START",
            "DS4_EU_CALLS", "DS4_EU_OUT")


def _load_offload_batch():
    path = ROOTFS / "fn_offload_batch.py"
    spec = importlib.util.spec_from_file_location("fn_offload_batch", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class InstrumentPackaging(unittest.TestCase):
    def test_every_module_compiles(self):
        with tempfile.TemporaryDirectory() as td:
            for name in MODULES:
                path = ROOTFS / name
                self.assertTrue(path.is_file(), f"{path} missing from the overlay")
                py_compile.compile(str(path),
                                   cfile=str(pathlib.Path(td) / (name + "c")),
                                   doraise=True)

    def test_every_module_carries_the_notice(self):
        for name, (upstream, _) in MODULES.items():
            text = (ROOTFS / name).read_text()
            self.assertIn("Adapted from", text, f"{name} lacks an adaptation notice")
            self.assertIn(upstream, text, f"{name} does not name {upstream}")
            self.assertIn("ds4-vllm", text, f"{name} does not name the upstream repo")
            self.assertIn("Apache License 2.0", text, f"{name} does not name the license")

    def test_env_surface_is_renamed_to_fn(self):
        for name, (_, envs) in MODULES.items():
            text = (ROOTFS / name).read_text()
            for env in envs:
                self.assertIn(env, text, f"{name} does not expose {env}")
            for retired in _RETIRED:
                self.assertNotIn(retired, text,
                                 f"{name} still carries the stock env {retired}")

    def test_no_bytecode_in_the_overlay(self):
        # What matters is what is TRACKED: COPY bakes tracked files into the
        # image, while stray local __pycache__ (e.g. from a bare py_compile)
        # is gitignored and never ships.
        out = subprocess.run(["git", "ls-files", str(ROOTFS)],
                             capture_output=True, text=True,
                             cwd=ROOT, check=True).stdout
        for rel in out.splitlines():
            self.assertFalse(rel.endswith(".pyc"), f"bytecode tracked: {rel}")
            self.assertNotIn("__pycache__", pathlib.PurePosixPath(rel).parts,
                             f"bytecode dir tracked: {rel}")


class OffloadBudgetInvariants(unittest.TestCase):
    """fn_offload_batch arithmetic must match the reference invariants."""

    @classmethod
    def setUpClass(cls):
        cls.mod = _load_offload_batch()

    def test_constants_match_the_reference(self):
        self.assertEqual(self.mod.FRAC_ENV, "FN_OFFLOAD_STORE_BATCH_FRAC")
        self.assertEqual(self.mod.PROMOTE_FRAC_ENV, "FN_OFFLOAD_PROMOTE_FRAC")
        self.assertEqual(self.mod.DEFAULT_FRAC, 0.25)
        self.assertEqual(self.mod.DEFAULT_PROMOTE_FRAC, 0.5)

    def test_store_budget_arithmetic(self):
        # 0.25 of 256 blocks = 64 blocks; key rate 1/128 + 1/16 per token
        # -> 64 / 0.0703125 = 910 tokens (floor does not bite).
        self.assertEqual(
            self.mod.resolve_store_batch_tokens(256, (128, 16), frac=0.25), 910)

    def test_store_floor_is_the_max_group_size(self):
        # The floor is load-bearing: at frac 0.001 the raw budget is 14 tokens,
        # under the coarsest group's 128-token block; the result must clamp to
        # exactly max(sizes) so no group's cursor can rewind.
        budget = self.mod.resolve_store_batch_tokens(256, (128, 16), frac=0.001)
        self.assertEqual(budget, max(128, 16))
        self.assertGreaterEqual(budget, 128)

    def test_store_disabled_paths_return_none(self):
        r = self.mod.resolve_store_batch_tokens
        self.assertIsNone(r(None, (128, 16), frac=0.25))
        self.assertIsNone(r(0, (128, 16), frac=0.25))
        self.assertIsNone(r(256, (), frac=0.25))
        self.assertIsNone(r(256, (0,), frac=0.25))
        # frac <= 0 is the kill switch: unbounded == stock behaviour.
        self.assertIsNone(r(256, (128, 16), frac=0))
        self.assertIsNone(r(256, (128, 16), frac=-1))

    def test_store_frac_reads_the_fn_env(self):
        with mock.patch.dict(os.environ, {self.mod.FRAC_ENV: "0.5"}):
            # 128 blocks / 0.0703125 keys-per-token = 1820 tokens.
            self.assertEqual(
                self.mod.resolve_store_batch_tokens(256, (128, 16)), 1820)
        # Garbage in the env falls back to the default, never raises.
        with mock.patch.dict(os.environ, {self.mod.FRAC_ENV: "not-a-number"}):
            self.assertEqual(
                self.mod.resolve_store_batch_tokens(256, (128, 16)), 910)

    def test_promote_is_counted_in_blocks(self):
        p = self.mod.resolve_promote_block_budget
        # Blocks, not tokens: only num_blocks and frac enter the arithmetic.
        self.assertEqual(p(258, frac=0.5), 129)
        self.assertEqual(p(258, frac=0.0001), 1)       # floor of 1 block
        self.assertEqual(p(258, frac=2.0), 258)        # capped at the tier
        # Kill switch and degenerate specs restore stock behaviour.
        self.assertIsNone(p(258, frac=0))
        self.assertIsNone(p(0))
        self.assertIsNone(p(None))

    def test_promote_frac_reads_the_fn_env(self):
        with mock.patch.dict(os.environ, {self.mod.PROMOTE_FRAC_ENV: "0.25"}):
            self.assertEqual(self.mod.resolve_promote_block_budget(256), 64)


if __name__ == "__main__":
    unittest.main()
