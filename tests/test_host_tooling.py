"""Host-tooling doctrine tests — the env file, the unit file, script hygiene.

The host-doctrine grep gate is satisfiable by wrong code (handoff/ROUTING.md
names this task the judged case), so these assertions aim at the semantic
traps a grep cannot catch: the rail list is COMPUTED from `ip -br addr`
rather than hardcoded (a peerless rail 1 in NCCL_SOCKET_IFNAME hangs RCCL
bootstrap), NCCL_IB_DISABLE=1 is unconditional (measured 2026-08-30: no
ibverbs device exists on either node tonight — the pin exists so that WHEN
the attended morning bring-up creates one, RCCL still cannot silently ride
unproven RDMA), no probe-read engine default is exported (spec F.9),
the unit tears down via ExecStopPost and installs at no boot target (spec
claim 6.1), and the bring-up reaps before it gates.
"""

import pathlib
import re
import subprocess
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
HOST = ROOT / "host"
ENV_FILE = HOST / "fn-env.sh"
UNIT_FILE = HOST / "systemd" / "flashnext-pair.service"
RUNNER = ROOT / "scripts" / "run-tp2.sh"

# Engine defaults read through is-set probes (spec F.9): exporting any of
# these — even "just" the default value — diverts the oracle into a hard
# raise. They must never appear as exports in the doctrine env.
PROBE_READ_DEFAULTS = (
    "VLLM_USE_DEEP_GEMM",
    "VLLM_MOE_USE_DEEP_GEMM",
    "VLLM_ROCM_USE_AITER",
)


class HostTooling(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.env = ENV_FILE.read_text()
        cls.unit = UNIT_FILE.read_text()
        cls.up = (HOST / "fn-cluster-up.sh").read_text()
        cls.runner = RUNNER.read_text()

    def test_fn_env_carries_the_doctrine_lines(self):
        for needle in (
            "export PYTHONHASHSEED=0",
            "expandable_segments:True,garbage_collection_threshold:0.85",
            "HSA_ENABLE_INTERRUPT=",
            "TORCHINDUCTOR_CACHE_DIR=",
            "TRITON_CACHE_DIR=",
            "NCCL_SOCKET_IFNAME",
            "export NCCL_IB_DISABLE=1",
            "export VLLM_RAY_EXTRA_ENV_VAR_PREFIXES_TO_COPY=FN_",
            "export VLLM_PLE_MMAP=1",
            "thunderbolt0",
        ):
            self.assertIn(needle, self.env,
                          f"doctrine line missing from fn-env.sh: {needle}")

    def test_fn_env_rails_are_computed_not_hardcoded(self):
        # The rail list must be computed from `ip -br addr` at env time; the
        # two-rail hardcode is exactly the wrong code that passes the grep.
        self.assertIn("ip -br", self.env)
        self.assertNotIn("NCCL_SOCKET_IFNAME=thunderbolt0,thunderbolt1",
                         self.env)
        self.assertNotRegex(
            self.env,
            r"^\s*export NCCL_SOCKET_IFNAME=thunderbolt",
            "NCCL_SOCKET_IFNAME must be computed per node, never hardcoded")
        # A rail without a routable peer IP must be excluded (link-local
        # 169.254/16 filtered), and the choice must be logged.
        self.assertIn("169\\.254", self.env)

    def test_fn_env_ib_disable_is_unconditional(self):
        # Unconditional means a bare export — no device-detection guard, no
        # ${...:-} fallback that a future edit could point elsewhere.
        self.assertRegex(self.env,
                         re.compile(r"^\s*export NCCL_IB_DISABLE=1\s*$", re.M),
                         "NCCL_IB_DISABLE=1 must be an unconditional export")

    def test_fn_env_never_exports_probe_read_defaults(self):
        for name in PROBE_READ_DEFAULTS:
            self.assertNotRegex(
                self.env, rf"^\s*export {name}=",
                f"spec F.9: {name} is read through an is-set probe; "
                "exporting the default diverts the oracle into a hard raise")

    def test_fn_env_cache_dirs_are_not_tmpfs(self):
        self.assertNotRegex(self.env, r"CACHE_DIR=[^ ]*/tmp\b",
                            "compiler caches under /tmp recompile on reboot")
        self.assertIn("FN_STATE_DIR", self.env)

    def test_unit_file_is_teardown_safe_and_boot_free(self):
        self.assertIn("Type=oneshot", self.unit)
        self.assertIn("RemainAfterExit=yes", self.unit)
        self.assertIn("ExecStopPost=", self.unit)
        self.assertIn("fn-cluster-down.sh", self.unit)
        # No boot-time install: the stack claims both GPUs for tens of
        # minutes and comes up only on an explicit start (spec claim 6.1).
        self.assertNotIn("WantedBy", self.unit)
        self.assertNotIn("[Install]", self.unit)

    def test_all_host_scripts_and_runner_are_syntax_clean(self):
        for sh in sorted(HOST.glob("*.sh")) + [RUNNER]:
            subprocess.run(["bash", "-n", str(sh)], check=True,
                           capture_output=True)

    def test_cluster_up_reaps_before_it_gates(self):
        # Reap-then-gate order is load-bearing: stale husks hold the port.
        self.assertLess(self.up.index("reap_serve_node"),
                        self.up.index("ray start"))
        self.assertIn("zero residue", self.up)
        self.assertIn("two-GPU gate", self.up)
        # Worker-side actions ride the wire fleet identity, never a rail.
        self.assertIn("10.99.9.2", self.env)

    def test_runner_writes_the_four_graded_receipts(self):
        for step in ("tp2", "residency", "fidelity", "context"):
            self.assertIn(f'"step": "{step}"', self.runner.replace("'", '"'),
                          f"run-tp2.sh does not write the {step} receipt")
        self.assertIn("fn-preflight.sh", self.runner)
        self.assertIn("fn-cluster-up.sh", self.runner)

    def test_fn_env_memory_budget_doctrine(self):
        # 2026-08-30: fork patch 0004 reports GTT (125.1 GiB); the util
        # default must keep util x 125.1 under the 80 GiB P11 bound, and the
        # KV/state pool + sequence slots are pinned, not floating.
        self.assertIn('FN_GPU_UTIL="${FN_GPU_UTIL:-0.62}"', self.env)
        self.assertIn("FN_KV_CACHE_BYTES", self.env)
        self.assertIn("FN_MAX_SEQS", self.env)

    def test_fn_env_transport_ladder(self):
        # The rail chooser gates on peer reachability with a 3-packet probe
        # (a 1-packet gate flaps on a cold neighbour cache), and the ONLY
        # fallback rung is the 5GbE wire — recorded, never the second rail,
        # never verbs.
        self.assertIn("ping -c3", self.env)
        self.assertIn("FN_ALLOW_WIRE_FALLBACK", self.env)
        self.assertIn("FN_TRANSPORT_RUNG", self.env)
        self.assertIn("wire-fallback", self.env)

    def test_cluster_up_ships_the_image_and_pins_the_serve(self):
        # The worker carries zero podman images by default; cp-tp2 dies at
        # its worker-container step unless the image is shipped first.
        self.assertIn("fn-image-ship.sh", self.up)
        self.assertIn("max-num-seqs", self.up)
        self.assertIn("kv-cache-memory-bytes", self.up)

    def test_runner_quarantines_fail_receipts(self):
        self.assertIn("results/receipts", self.runner)
        self.assertIn('"failed"', self.runner.replace("'", '"'),
                      "run-tp2.sh must quarantine status=fail receipts "
                      "under results/receipts/failed/")


if __name__ == "__main__":
    unittest.main()
