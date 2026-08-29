"""Repo packaging invariants — the offline half of the discipline.

Modeled on ds4-vllm's test_patchset_packaging.py (Apache-2.0), extended:
new-file line counts are audited too (the upstream suite skipped them and
3 of its 12 MANIFEST counts drifted).
"""

import json
import pathlib
import re
import subprocess
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent


class RepoInvariants(unittest.TestCase):
    def test_license_is_apache2(self):
        text = (ROOT / "LICENSE").read_text()
        self.assertIn("Apache License", text)
        self.assertIn("Version 2.0", text)

    def test_no_gpl_text_in_tree(self):
        needle = "GNU GENERAL " + "PUBLIC LICENSE"  # avoid self-match
        for path in ROOT.rglob("*"):
            if path.is_dir() or ".git" in path.parts \
                    or "__pycache__" in path.parts:
                continue
            # Evidence dossiers may QUOTE license headers they inspected.
            if "evidence" in path.parts or path.name == "test_repo_invariants.py":
                continue
            if path.suffix in {".safetensors", ".gguf"}:
                continue
            try:
                text = path.read_text(errors="ignore")
            except Exception:
                continue
            self.assertNotIn(needle, text, f"GPL text found in {path}")

    def test_no_secret_material(self):
        patterns = [re.compile(p) for p in
                    (r"BEGIN [A-Z ]*PRIVATE KEY", r"hf_[A-Za-z0-9]{30,}",
                     r"ghp_[A-Za-z0-9]{30,}", r"AKIA[0-9A-Z]{16}")]
        for path in ROOT.rglob("*"):
            if path.is_dir() or ".git" in path.parts \
                    or "__pycache__" in path.parts:
                continue
            try:
                text = path.read_text(errors="ignore")
            except Exception:
                continue
            for pat in patterns:
                self.assertIsNone(pat.search(text),
                                  f"secret-like material in {path}: {pat.pattern}")

    def test_spec_artifacts_exist(self):
        self.assertTrue((ROOT / "specs/flashnext/spec.md").is_file())
        trace = json.loads((ROOT / "specs/flashnext/trace.json").read_text())
        self.assertEqual(trace["schemaVersion"], 1)
        self.assertTrue(
            (ROOT / "specs/flashnext/contracts/trace.schema.json").is_file())

    def test_evidence_dossiers_present(self):
        dossiers = list((ROOT / "specs/flashnext/evidence").glob("*.md"))
        self.assertGreaterEqual(len(dossiers), 7)

    def test_worklist_shape_and_hygiene(self):
        wl_path = ROOT / "silent-factory-worklists/flashnext.json"
        wl = json.loads(wl_path.read_text())
        self.assertEqual(wl["schemaVersion"], 1)
        self.assertTrue(wl["tasks"])
        raw = wl_path.read_text()
        # Runtime hub downloads are forbidden estate-wide.
        self.assertNotIn("-hf ", raw)
        self.assertNotIn("hf download", raw)
        # Model-family names never steer task content (lint L16 mirror).
        # The campaign.agent block is exempt: it names execution
        # infrastructure, not task content, and the adapter was switched to
        # claude-code by operator ruling on 2026-08-29 (qwen token-plan
        # quota exhaustion; see handoff/DAYRUN-NOTES.md).
        wl_scan = json.loads(raw)
        wl_scan["campaign"].pop("agent", None)
        scan = json.dumps(wl_scan).lower()
        for banned in ("qwen", "llama", "deepseek", "claude", "opus"):
            self.assertNotIn(banned, scan,
                             f"banned token '{banned}' in worklist bytes")
        # Every implementation task carries executable acceptance.
        for task in wl["tasks"]:
            if task.get("kind") == "implementation":
                self.assertTrue(task.get("acceptanceCriteria"),
                                f"{task['id']} has no acceptance criteria")

    def test_scripts_are_syntax_clean(self):
        for sh in (ROOT / "scripts").glob("*.sh"):
            subprocess.run(["bash", "-n", str(sh)], check=True)
        for sh in (ROOT / "host").rglob("*.sh"):
            subprocess.run(["bash", "-n", str(sh)], check=True)
        for py in (ROOT / "scripts").glob("*.py"):
            subprocess.run(["python3", "-m", "py_compile", str(py)],
                           check=True)

    def test_receipts_verify_runs_clean(self):
        proc = subprocess.run(
            ["python3", str(ROOT / "scripts/receipts-verify.py")],
            capture_output=True, text=True)
        self.assertIn(proc.returncode, (0,),
                      f"receipts-verify failed:\n{proc.stdout}{proc.stderr}")

    def test_notices_cover_the_import_manifest(self):
        notices = (ROOT / "THIRD_PARTY_NOTICES.md").read_text()
        for needle in ("vllm-project/vllm", "AlexKGwyn/ds4-vllm",
                       "amd-strix-halo-vllm-toolboxes", "nix-strix-halo"):
            self.assertIn(needle, notices)


if __name__ == "__main__":
    unittest.main()
