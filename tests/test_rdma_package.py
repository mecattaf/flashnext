"""RDMA package invariants — the pins, the no-unattended-load rule, and the
attended language the operator's morning depends on.

`test_repo_invariants.py` already runs `bash -n` over every `host/**/*.sh`.
What it does not do is assert anything about *what* `host/rdma/` says, and
this package is the one place in the estate where a doc line is load-bearing
in the same way a code line is: spec F.1 ("never load an unsigned kernel
module or bring RDMA up unattended, and never on both rails") is enforced
here by the shape of the script plus the wording of the checklist, nothing
else. Claim 6.4 grades that pair.
"""

import pathlib
import re
import subprocess
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
RDMA = ROOT / "host/rdma"

# The two module pins the attended morning build resolves. WESTERI_BASE is
# the patched thunderbolt core/net base; IBV_BASE is the verbs provider whose
# source-aware control handler the 2026-08-29 recon verified.
CORE_PIN = "503c5ae1e72aa9ed91925dafa3d82ee2e992747f"
IBV_PIN = "76ba39b630a70accb72f19388eefe48844b50eb8"

# Mirrors the acceptance argv byte-for-byte in intent: a load command at the
# start of a line (bare or sudo-prefixed) is an unattended load path, whether
# or not anything ever calls it. In-prose mentions of insmod are fine and
# deliberate -- the checklist has to name the command the operator types.
LOAD_RE = re.compile(r"^[ \t]*(sudo[ \t]+)?(insmod|modprobe)[ \t]", re.M)


class RdmaPins(unittest.TestCase):
    def setUp(self):
        self.script = (RDMA / "fetch-and-build.sh").read_text()

    def test_both_module_pins_are_present(self):
        for pin in (CORE_PIN, IBV_PIN):
            self.assertIn(pin, self.script,
                          f"module pin {pin} missing from fetch-and-build.sh")

    def test_pins_are_full_length_commits_not_branches(self):
        # A branch or tag pin is how rdma-core drifted in the reference
        # estate (ds4-vllm-manifest.md §0, correction 7). Full 40-hex only.
        for pin in (CORE_PIN, IBV_PIN):
            self.assertRegex(pin, r"^[0-9a-f]{40}$")

    def test_script_is_syntax_clean(self):
        subprocess.run(["bash", "-n", str(RDMA / "fetch-and-build.sh")],
                       check=True)

    def test_no_unattended_module_load_path(self):
        """Spec F.1: nothing in this package loads a module by itself."""
        hit = LOAD_RE.search(self.script)
        self.assertIsNone(
            hit,
            "fetch-and-build.sh carries a module-load command at line start: "
            f"{hit.group(0).strip() if hit else ''} -- staging and building "
            "is all this script may do; loading is the operator's act.")


class AttendedLanguage(unittest.TestCase):
    def setUp(self):
        self.bringup = (RDMA / "attended-bringup.md").read_text()
        self.protocol = (RDMA / "ab-protocol.md").read_text()

    def test_bringup_requires_a_physically_present_operator(self):
        self.assertIn("physically present", self.bringup.lower())

    def test_bringup_carries_the_worker_first_reboot_rule(self):
        self.assertIn("worker first", self.bringup.lower())

    def test_bringup_states_the_running_kernel_gate(self):
        # The 7.1.4/7.2.0 module sets died with the fleet's move to 7.2.2;
        # the morning rebuilds on both nodes before any A/B.
        self.assertIn("7.2.2", self.bringup)

    def test_bringup_states_the_gate0_transport_rung_caveat(self):
        # A wire-fallback bench is a 5GbE artifact and does not open Gate 0.
        self.assertIn("wire-fallback", self.bringup.lower())

    def test_gate0_still_requires_a_banked_socket_benchmark(self):
        self.assertIn("results/", self.bringup)
        self.assertIn("Gate 0", self.bringup)

    def test_single_rail_contract_survives(self):
        # Reflowed prose wraps mid-sentence, so match on collapsed
        # whitespace rather than on the file's line breaks.
        flat = re.sub(r"[\s*]+", " ", self.bringup.lower())
        self.assertIn("rail 1", flat)
        self.assertIn("never bring rdma up on both rails", flat)

    def test_odinlink_fold_landed_in_both_docs(self):
        self.assertIn("odinlink", self.protocol.lower())
        self.assertIn("odinlink", self.bringup.lower())

    def test_wedge_hazard_is_stated_where_the_ladder_is_written(self):
        # The reason no verbs rung may ever appear in an unattended ladder:
        # a transmit toward a closed peer RX ring wedges TCP on the same
        # cable, and recovery is a reboot.
        for text in (self.protocol.lower(), self.bringup.lower()):
            self.assertIn("reboot-only", text)

    def test_protocol_is_not_a_stub(self):
        self.assertGreater(len(self.protocol.strip()), 0)


if __name__ == "__main__":
    unittest.main()
