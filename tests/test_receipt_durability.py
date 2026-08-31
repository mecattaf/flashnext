"""Receipt durability: evidence that outlives the lane, a gate that can count.

Two green nights banked zero engine evidence for one reason (RUN3-BRIEF
§18.3): a checkpoint task is contractually forbidden from carrying
conflictDomains, so it owns no path, nothing it writes is ever committed, and
purity is checked with --untracked-files=no — which makes its untracked
receipt perfectly legal and lets it die with the lane worktree. And the
closing gate could not see the loss (§2.1 defect 3): missing receipts are
legal, so "3 receipts checked, 0 violations" is what both "everything passed"
and "nothing ran" look like.

The two halves are asserted here against the real scripts, copied into a
throwaway repo that stands in for a lane worktree:

  * scripts/receipt-restore.py mirrors receipts into $FN_STATE_DIR/receipts/
    on its existing argv, writes the durable copy FIRST under --write, is
    idempotent (an unchanged measurement is never re-stamped), and can never
    fail the step it is protecting;
  * a receipt survives `rm -rf` of the worktree that produced it, and
    scripts/receipts-verify.py counts it — the case that matters;
  * --require fails on an absent receipt, is absent by default so the existing
    gate argv is unchanged, and leaves the results/receipts/failed/ quarantine
    (D12) exactly as loud and exactly as harmless as it was.
"""

import json
import os
import pathlib
import shutil
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
VERIFY_SRC = ROOT / "scripts" / "receipts-verify.py"
RESTORE_SRC = ROOT / "scripts" / "receipt-restore.py"

TS = "2026-08-31T00:00:00Z"


def _receipt(step, status="pass", ts=TS, **data):
    return {"step": step, "status": status, "ts": ts, "data": data}


class _Lane(unittest.TestCase):
    """A throwaway repo carrying both scripts, plus a durable state dir."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = pathlib.Path(self.tmp.name)
        self.state = self.base / "state"
        self.lane = self.new_lane("lane")

    def new_lane(self, name):
        """A fresh worktree stand-in: the scripts, a git repo, no receipts."""
        lane = self.base / name
        (lane / "scripts").mkdir(parents=True)
        (lane / "results" / "receipts").mkdir(parents=True)
        for src in (VERIFY_SRC, RESTORE_SRC):
            shutil.copy(src, lane / "scripts" / src.name)
        self.git(lane, "init", "-q")
        self.git(lane, "add", "-A")
        self.commit(lane, "lane base")
        return lane

    def git(self, lane, *argv):
        return subprocess.run(["git", "-C", str(lane), *argv],
                              capture_output=True, text=True, check=True)

    def commit(self, lane, message):
        self.git(lane, "-c", "user.email=lane@flashnext", "-c",
                 "user.name=lane", "commit", "-q", "-m", message)

    def env(self, state=True):
        env = dict(os.environ)
        env.pop("FN_STATE_DIR", None)
        if state:
            env["FN_STATE_DIR"] = str(self.state)
        return env

    def place(self, path, receipt):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n")
        return path

    def in_repo(self, receipt, lane=None, failed=False, name=None):
        lane = lane or self.lane
        base = (lane / "results" / "receipts")
        base = base / "failed" if failed else base
        return self.place(base / f"{name or receipt['step']}.json", receipt)

    def durable(self, receipt, failed=False, name=None):
        base = self.state / "receipts"
        base = base / "failed" if failed else base
        return self.place(base / f"{name or receipt['step']}.json", receipt)

    def verify(self, *argv, lane=None, state=True):
        lane = lane or self.lane
        return subprocess.run(
            ["python3", str(lane / "scripts" / "receipts-verify.py"), *argv],
            capture_output=True, text=True, env=self.env(state), cwd=str(lane))

    def restore(self, *argv, lane=None, state=True, stdin=None):
        lane = lane or self.lane
        return subprocess.run(
            ["python3", str(lane / "scripts" / "receipt-restore.py"),
             str(lane), *argv],
            capture_output=True, text=True, env=self.env(state),
            cwd=str(lane), input=stdin)


class DefaultArgvUnchanged(_Lane):
    """The existing gate argv keeps its meaning: missing receipts are legal."""

    def test_repo_gate_still_exits_zero(self):
        proc = subprocess.run(["python3", str(VERIFY_SRC)],
                              capture_output=True, text=True, cwd=str(ROOT))
        self.assertEqual(0, proc.returncode,
                         f"{proc.stdout}{proc.stderr}")
        self.assertIn("receipts checked", proc.stdout)

    def test_missing_receipts_are_still_legal(self):
        self.in_repo(_receipt("smoke"))
        proc = self.verify()
        self.assertEqual(0, proc.returncode, proc.stdout)
        self.assertIn("1 receipts checked, 0 violations", proc.stdout)

    def test_empty_receipts_dir_is_still_zero(self):
        proc = self.verify()
        self.assertEqual(0, proc.returncode, proc.stdout)
        self.assertIn("0 receipts checked", proc.stdout)

    def test_no_receipts_directory_at_all(self):
        shutil.rmtree(self.lane / "results" / "receipts")
        proc = self.verify()
        self.assertEqual(0, proc.returncode, proc.stdout)
        self.assertIn("nothing ran", proc.stdout)

    def test_durable_receipts_are_invisible_without_the_state_dir(self):
        self.durable(_receipt("smoke"))
        proc = self.verify(state=False)
        self.assertEqual(0, proc.returncode, proc.stdout)
        self.assertIn("0 receipts checked", proc.stdout)

    def test_a_failed_receipt_still_violates(self):
        self.in_repo(_receipt("smoke", status="fail"))
        proc = self.verify()
        self.assertEqual(2, proc.returncode, proc.stdout)
        self.assertIn("FAILED", proc.stdout)

    def test_bounds_still_grade_a_durable_receipt(self):
        self.durable(_receipt("weights-worker", shards=7))
        proc = self.verify()
        self.assertEqual(2, proc.returncode, proc.stdout)
        self.assertIn("only 7 shards staged", proc.stdout)
        self.assertIn("(durable)", proc.stdout)

    def test_the_same_receipt_in_both_roots_is_graded_once(self):
        self.in_repo(_receipt("smoke"))
        self.durable(_receipt("smoke", ts="2026-08-31T01:00:00Z"))
        proc = self.verify()
        self.assertEqual(0, proc.returncode, proc.stdout)
        self.assertIn("1 receipts checked, 0 violations", proc.stdout)


class ExpectedSet(_Lane):
    """--require turns 'nothing ran' into a violation, on demand only."""

    def test_absent_receipt_fails_the_gate(self):
        self.in_repo(_receipt("smoke"))
        proc = self.verify("--require", "smoke,tp2,bench")
        self.assertEqual(2, proc.returncode, proc.stdout)
        self.assertIn("required receipt 'tp2' is absent", proc.stdout)
        self.assertIn("required receipt 'bench' is absent", proc.stdout)
        self.assertIn("expected set: 1/3 present", proc.stdout)
        self.assertIn("2 violations", proc.stdout)

    def test_present_receipts_satisfy_the_set(self):
        self.in_repo(_receipt("smoke"))
        self.in_repo(_receipt("tp2", byte_identical_repeat=True))
        proc = self.verify("--require", "smoke", "--require", "tp2")
        self.assertEqual(0, proc.returncode, proc.stdout)
        self.assertIn("expected set: 2/2 present", proc.stdout)

    def test_a_durable_only_receipt_satisfies_the_set(self):
        """The case that matters: the lane copy is gone, the evidence is not."""
        self.durable(_receipt("smoke"))
        self.assertFalse(
            (self.lane / "results" / "receipts" / "smoke.json").exists())
        proc = self.verify("--require", "smoke")
        self.assertEqual(0, proc.returncode, proc.stdout)
        self.assertIn("expected set: 1/1 present", proc.stdout)

    def test_the_expect_set_spelling_is_the_same_flag(self):
        proc = self.verify("--expect-set", "smoke")
        self.assertEqual(2, proc.returncode, proc.stdout)
        self.assertIn("required receipt 'smoke' is absent", proc.stdout)

    def test_a_required_step_is_named_by_stem_or_by_step_field(self):
        self.in_repo(_receipt("weights-coordinator", shards=140),
                     name="weights-node-a")
        for wanted in ("weights-node-a", "weights-coordinator"):
            proc = self.verify("--require", wanted)
            self.assertEqual(0, proc.returncode, f"{wanted}: {proc.stdout}")

    def test_repeats_and_blanks_collapse(self):
        self.in_repo(_receipt("smoke"))
        proc = self.verify("--require", "smoke, ,smoke", "--require", "smoke")
        self.assertEqual(0, proc.returncode, proc.stdout)
        self.assertIn("expected set: 1/1 present", proc.stdout)

    def test_no_receipts_directory_fails_a_declared_set(self):
        shutil.rmtree(self.lane / "results" / "receipts")
        proc = self.verify("--require", "smoke")
        self.assertEqual(2, proc.returncode, proc.stdout)
        self.assertIn("nothing ran", proc.stdout)
        self.assertIn("required receipt 'smoke' is absent", proc.stdout)

    def test_a_corrupt_receipt_does_not_satisfy_the_set(self):
        path = self.lane / "results" / "receipts" / "smoke.json"
        path.write_text("{not json")
        proc = self.verify("--require", "smoke")
        self.assertEqual(2, proc.returncode, proc.stdout)
        self.assertIn("invalid JSON", proc.stdout)
        self.assertIn("required receipt 'smoke' is absent", proc.stdout)


class QuarantineUnchanged(_Lane):
    """D12: one graded failure costs one step, never every later gate run."""

    def test_a_quarantined_failure_warns_but_never_reddens(self):
        self.in_repo(_receipt("smoke"))
        self.in_repo(_receipt("proxy", status="fail"), failed=True)
        proc = self.verify()
        self.assertEqual(0, proc.returncode, proc.stdout)
        self.assertIn("WARN: quarantined failure receipt failed/proxy.json",
                      proc.stdout)
        self.assertIn("1 receipts checked, 0 violations", proc.stdout)

    def test_a_durable_quarantined_failure_also_only_warns(self):
        self.durable(_receipt("proxy", status="fail"), failed=True)
        proc = self.verify()
        self.assertEqual(0, proc.returncode, proc.stdout)
        self.assertIn("failed/proxy.json (durable)", proc.stdout)
        self.assertIn("0 receipts checked, 0 violations", proc.stdout)

    def test_a_quarantined_failure_does_not_satisfy_a_required_step(self):
        self.in_repo(_receipt("proxy", status="fail"), failed=True)
        proc = self.verify("--require", "proxy")
        self.assertEqual(2, proc.returncode, proc.stdout)
        self.assertIn("it ran and FAILED", proc.stdout)


class DurableSurvival(_Lane):
    """The receipt outlives the worktree that wrote it."""

    def test_a_written_receipt_survives_the_lane_worktree(self):
        receipt = self.base / "smoke-receipt.json"
        receipt.write_text(json.dumps(_receipt("smoke", aperture={
            "ttm_pages_limit": 33554432})))
        proc = self.restore("--write", str(receipt))
        self.assertEqual(0, proc.returncode, proc.stderr)
        self.assertTrue(
            (self.lane / "results" / "receipts" / "smoke.json").is_file())
        durable = self.state / "receipts" / "smoke.json"
        self.assertTrue(durable.is_file())

        shutil.rmtree(self.lane)  # the driver discards the lane worktree
        self.assertTrue(durable.is_file())

        # The closing gate, run from a later lane, still counts the evidence.
        later = self.new_lane("cp-close")
        proc = self.verify("--require", "smoke", lane=later)
        self.assertEqual(0, proc.returncode, proc.stdout)
        self.assertIn("expected set: 1/1 present", proc.stdout)

    def test_write_places_the_durable_copy_first(self):
        """A crash between the two copies must lose the disposable one."""
        text = json.dumps(_receipt("smoke"))
        (self.base / "r.json").write_text(text)
        source = (RESTORE_SRC).read_text()
        durable_at = source.index("_place(dest_root / rel, receipt, text)")
        in_repo_at = source.index(
            '_place(pathlib.Path(root) / "results" / "receipts" / rel')
        self.assertLess(durable_at, in_repo_at)

    def test_write_is_byte_identical_in_both_locations(self):
        text = json.dumps(_receipt("smoke"), indent=2) + "\n"
        (self.base / "r.json").write_text(text)
        self.restore("--write", str(self.base / "r.json"))
        self.assertEqual(
            text, (self.state / "receipts" / "smoke.json").read_text())
        self.assertEqual(
            text,
            (self.lane / "results" / "receipts" / "smoke.json").read_text())

    def test_write_reads_stdin(self):
        proc = self.restore("--write", "-",
                            stdin=json.dumps(_receipt("smoke")))
        self.assertEqual(0, proc.returncode, proc.stderr)
        self.assertTrue((self.state / "receipts" / "smoke.json").is_file())

    def test_write_never_restamps_an_unchanged_measurement(self):
        first = json.dumps(_receipt("weights-worker", shards=140))
        (self.base / "r.json").write_text(first)
        self.restore("--write", str(self.base / "r.json"))
        durable = self.state / "receipts" / "weights-worker.json"
        in_repo = self.lane / "results" / "receipts" / "weights-worker.json"
        before = (durable.read_text(), in_repo.read_text())

        (self.base / "r.json").write_text(json.dumps(
            _receipt("weights-worker", ts="2026-09-01T09:09:09Z", shards=140)))
        self.restore("--write", str(self.base / "r.json"))
        self.assertEqual(before, (durable.read_text(), in_repo.read_text()))

    def test_write_records_real_drift(self):
        (self.base / "r.json").write_text(json.dumps(
            _receipt("weights-worker", shards=140)))
        self.restore("--write", str(self.base / "r.json"))
        (self.base / "r.json").write_text(json.dumps(
            _receipt("weights-worker", shards=131)))
        self.restore("--write", str(self.base / "r.json"))
        durable = json.loads(
            (self.state / "receipts" / "weights-worker.json").read_text())
        self.assertEqual(131, durable["data"]["shards"])

    def test_write_quarantines_a_failure_in_both_locations(self):
        (self.base / "r.json").write_text(
            json.dumps(_receipt("proxy", status="fail")))
        proc = self.restore("--write", str(self.base / "r.json"))
        self.assertEqual(0, proc.returncode, proc.stderr)
        self.assertTrue(
            (self.state / "receipts" / "failed" / "proxy.json").is_file())
        self.assertTrue((self.lane / "results" / "receipts" / "failed"
                         / "proxy.json").is_file())
        self.assertFalse((self.state / "receipts" / "proxy.json").exists())

    def test_write_still_lands_in_repo_without_a_state_dir(self):
        (self.base / "r.json").write_text(json.dumps(_receipt("smoke")))
        proc = self.restore("--write", str(self.base / "r.json"), state=False)
        self.assertEqual(0, proc.returncode, proc.stderr)
        self.assertTrue(
            (self.lane / "results" / "receipts" / "smoke.json").is_file())
        self.assertIn("FN_STATE_DIR is unset", proc.stderr)

    def test_an_unwritable_state_dir_never_fails_the_step(self):
        wall = self.base / "wall"
        wall.write_text("not a directory\n")
        self.state = wall / "receipts"  # mkdir under a regular file: EEXIST
        (self.base / "r.json").write_text(json.dumps(_receipt("smoke")))
        proc = self.restore("--write", str(self.base / "r.json"))
        self.assertEqual(0, proc.returncode, proc.stderr)
        self.assertIn("the step is unaffected", proc.stderr)
        self.assertTrue(
            (self.lane / "results" / "receipts" / "smoke.json").is_file())

    def test_a_receipt_without_a_step_is_refused(self):
        (self.base / "r.json").write_text(json.dumps({"status": "pass"}))
        proc = self.restore("--write", str(self.base / "r.json"))
        self.assertEqual(2, proc.returncode, proc.stderr)
        self.assertIn("refusing to guess", proc.stderr)


class MirrorOnTheExistingArgv(_Lane):
    """Writers that only know `receipt-restore.py $REPO_ROOT` get durability."""

    def test_untracked_receipts_are_mirrored(self):
        self.in_repo(_receipt("smoke"))
        self.in_repo(_receipt("proxy", status="fail"), failed=True)
        proc = self.restore()
        self.assertEqual(0, proc.returncode, proc.stderr)
        self.assertEqual(
            (self.lane / "results" / "receipts" / "smoke.json").read_text(),
            (self.state / "receipts" / "smoke.json").read_text())
        self.assertTrue(
            (self.state / "receipts" / "failed" / "proxy.json").is_file())

    def test_the_mirror_is_idempotent_modulo_ts(self):
        self.in_repo(_receipt("smoke"))
        self.restore()
        durable = self.state / "receipts" / "smoke.json"
        before = durable.read_text()
        self.in_repo(_receipt("smoke", ts="2026-09-01T09:09:09Z"))
        proc = self.restore()
        self.assertEqual(0, proc.returncode, proc.stderr)
        self.assertEqual(before, durable.read_text())

    def test_the_mirror_never_fails_the_step(self):
        wall = self.base / "wall"
        wall.write_text("not a directory\n")
        self.state = wall / "receipts"
        self.in_repo(_receipt("smoke"))
        proc = self.restore()
        self.assertEqual(0, proc.returncode, proc.stderr)
        self.assertIn("the step is unaffected", proc.stderr)

    def test_a_missing_state_dir_is_only_a_warning(self):
        self.in_repo(_receipt("smoke"))
        proc = self.restore(state=False)
        self.assertEqual(0, proc.returncode, proc.stderr)
        self.assertIn("FN_STATE_DIR is unset", proc.stderr)

    def test_restoring_a_tracked_receipt_still_works(self):
        """The original job, unbroken: a ts-only re-run leaves no diff."""
        self.in_repo(_receipt("weights-worker", shards=140))
        self.git(self.lane, "add", "-A")
        self.commit(self.lane, "receipt")
        self.in_repo(_receipt("weights-worker", ts="2026-09-01T09:09:09Z",
                              shards=140))
        proc = self.restore()
        self.assertEqual(0, proc.returncode, proc.stderr)
        self.assertEqual("", self.git(self.lane, "status", "--porcelain",
                                      "--untracked-files=no").stdout)

    def test_real_drift_in_a_tracked_receipt_is_left_in_place(self):
        self.in_repo(_receipt("weights-worker", shards=140))
        self.git(self.lane, "add", "-A")
        self.commit(self.lane, "receipt")
        self.in_repo(_receipt("weights-worker", shards=131))
        self.restore()
        dirty = self.git(self.lane, "status", "--porcelain",
                         "--untracked-files=no").stdout
        self.assertIn("results/receipts/weights-worker.json", dirty)


if __name__ == "__main__":
    unittest.main()
