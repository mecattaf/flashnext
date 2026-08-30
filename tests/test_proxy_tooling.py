"""Proxy tooling: the invariants nothing between authoring and cp-proxy checks.

`scripts/make-proxy.sh` and `scripts/run-proxy.sh` are authored by one lane
and RUN by a checkpoint on a GPU this lane never touches. Nothing looks at
them in between, so the facts that would be expensive to learn at 3 a.m. are
asserted here instead:

  * the serve doctrine (docs/DAYRUN-STOP-STATE-2026-08-29.md, "Engine
    knowledge the second flow MUST carry") — the text-only multimodal limit
    that keeps the vision encoder's 274877906944-byte profiling pass out of
    the run, the absence of the profiling-bypass flag that only defers that
    wall to the first real image, gpu_memory_utilization held at 0.6, and no
    VLLM_ROCM_APU_UNIFIED_MEMORY at any value on hosts that log
    integrated_gpu=False;
  * that the checkpoint builder reuses the fork's own scaffolding rather than
    inventing a table layout, and that its shard-name derivation round-trips
    through the engine's real matcher;
  * the shard row arithmetic, exercised against the fork's formula;
  * the inventory filter that shrinks the workload's tensor set to the proxy's;
  * and that every embedded Python body in both scripts compiles.

The two shell scripts' embedded Python is extracted and imported here the way
tests/test_usb4stream_bench.py imports a dash-named script: the builder body
is written import-safe (everything effectful sits under a __main__ guard), so
its pure planning functions can be driven without a container.
"""

import importlib.util
import pathlib
import py_compile
import re
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
MAKE_PATH = ROOT / "scripts" / "make-proxy.sh"
RUN_PATH = ROOT / "scripts" / "run-proxy.sh"

HEREDOC_RE = re.compile(r"<<'PY'[^\n]*\n(.*?)\nPY\n", re.S)

# The fork's real shard matcher (vllm ple_mmap `_SHARD_RE`, re-pinned in
# specs/flashnext/evidence/ple-54129.md §3). The builder derives the tensor
# name it writes from this regex at build time; here we check the derivation
# against the same literal, so a drift in either direction is a test failure
# rather than a silent mismatch discovered at serve time.
FORK_SHARD_RE = re.compile(
    r"layers\.(\d+)\.ple\.ple_embedding\.ngram_embedding\.shard_(\d+)\.weight$")

# Assembled from parts so this file never carries the literal it forbids, and
# so a careless grep of the tests cannot look like a violation in the scripts.
FORBIDDEN_MM_FLAG = "--skip-" + "mm-profiling"


def _heredocs(path):
    return HEREDOC_RE.findall(path.read_text())


def _builder_module():
    """Import the largest embedded Python body of make-proxy.sh."""
    body = max(_heredocs(MAKE_PATH), key=len)
    tmp = pathlib.Path(tempfile.mkdtemp()) / "make_proxy_body.py"
    tmp.write_text(body)
    spec = importlib.util.spec_from_file_location("fn_make_proxy_body", tmp)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ProxyScriptsShape(unittest.TestCase):
    def test_both_scripts_are_syntax_clean(self):
        for path in (MAKE_PATH, RUN_PATH):
            subprocess.run(["bash", "-n", str(path)], check=True)

    def test_embedded_python_compiles(self):
        for path in (MAKE_PATH, RUN_PATH):
            bodies = _heredocs(path)
            self.assertTrue(bodies, f"{path.name} embeds no Python")
            for index, body in enumerate(bodies):
                tmp = pathlib.Path(tempfile.mkdtemp()) / f"body{index}.py"
                tmp.write_text(body)
                py_compile.compile(str(tmp), doraise=True)

    def test_scripts_are_executable(self):
        for path in (MAKE_PATH, RUN_PATH):
            self.assertTrue(path.stat().st_mode & 0o111,
                            f"{path.name} is not executable")


class ServeDoctrine(unittest.TestCase):
    """The three serve facts that were bought with a day of GPU time."""

    def setUp(self):
        self.text = RUN_PATH.read_text()

    def test_text_only_multimodal_limit(self):
        self.assertIn('--limit-mm-per-prompt', self.text)
        self.assertIn('{"image":0,"video":0}', self.text)

    def test_profiling_bypass_flag_is_absent(self):
        # Bypassing multimodal profiling only defers the encoder wall to the
        # first real image, i.e. to a live request instead of to boot.
        self.assertNotIn(FORBIDDEN_MM_FLAG, self.text)

    def test_apu_unified_memory_is_never_exported(self):
        # These hosts log integrated_gpu=False; the fork's APU patches are
        # inert and that steer was superseded. Naming it in the receipt as
        # explicitly-unset is fine; exporting it at any value is not.
        for line in self.text.splitlines():
            stripped = line.strip()
            if stripped.startswith("#") or "VLLM_ROCM_APU_UNIFIED_MEMORY" not in line:
                continue
            self.assertNotRegex(
                line, r"(?:export\s+|-e\s+)VLLM_ROCM_APU_UNIFIED_MEMORY=",
                f"APU unified-memory steer exported: {stripped}")

    def test_mmap_table_armed_and_single_node(self):
        self.assertIn("VLLM_PLE_MMAP=1", self.text)
        self.assertIn("--tensor-parallel-size 1", self.text)

    def test_gpu_memory_utilization_default_is_zero_point_six(self):
        found = re.search(r'GPU_UTIL="\$\{FN_PROXY_GPU_UTIL:-([0-9.]+)\}"',
                          self.text)
        self.assertIsNotNone(found, "run-proxy.sh names no default GPU utilization")
        self.assertEqual(found.group(1), "0.6")

    def test_eager_branch_refuses_to_arm_beside_the_mmap_table(self):
        # check_cudagraph_safety's second guard raises on enforce-eager while
        # VLLM_PLE_MMAP=1; the script must refuse before the engine does.
        self.assertIn("--enforce-eager", self.text)
        eager_block = self.text.split("  eager)", 1)
        self.assertEqual(len(eager_block), 2, "no eager branch in run-proxy.sh")
        guarded = eager_block[1].split("esac", 1)[0]
        self.assertIn('VLLM_PLE_MMAP" = "1"', guarded)
        self.assertIn("exit 2", guarded)

    def test_receipt_path_and_failure_quarantine(self):
        self.assertIn("results/receipts/proxy.json", self.text)
        self.assertIn('os.path.join(RECEIPTS, "failed")', self.text)

    def test_receipt_records_the_serve_env_choices(self):
        # cp-proxy and the TP=2 serve reproduce these; they must be data, not
        # prose in a comment.
        for key in ('"serve_env"', '"serve_args"', '"exec_mode"',
                    '"gpu_memory_utilization"', '"multimodal_limit"'):
            self.assertIn(key, self.text)

    def test_teardown_is_unconditional(self):
        self.assertIn("trap cleanup EXIT", self.text)
        self.assertIn("podman stop", self.text)


class CheckpointBuilder(unittest.TestCase):
    def setUp(self):
        self.text = MAKE_PATH.read_text()
        self.module = _builder_module()

    def test_builds_at_the_agreed_path(self):
        self.assertIn("/var/tmp/flashnext-proxy", self.text)

    def test_reuses_the_forks_test_scaffolding(self):
        self.assertIn("tests/models", self.text)
        self.assertIn("test_ple_mmap.py", self.text)
        self.assertIn("discover_shards", self.text)

    def test_block_fp8_geometry(self):
        self.assertIn("weight_block_size", self.text)
        found = re.search(r'FN_PROXY_BLOCK:-([0-9]+)', self.text)
        self.assertIsNotNone(found)
        self.assertEqual(found.group(1), "128")
        experts = re.search(r'FN_PROXY_EXPERTS:-([0-9]+)', self.text)
        self.assertIsNotNone(experts)
        self.assertEqual(experts.group(1), "8")

    def test_rebuild_is_skipped_when_the_signature_matches(self):
        self.assertIn("rebuild skipped", self.text)
        self.assertIn("FN_PROXY_FORCE", self.text)

    def test_shard_name_derives_from_the_forks_own_matcher(self):
        template = self.module.shard_name_template(FORK_SHARD_RE)
        self.assertEqual(
            template,
            "layers.{}.ple.ple_embedding.ngram_embedding.shard_{}.weight")
        for layer, shard in ((0, 0), (3, 2), (11, 511)):
            name = "model." + template.format(layer, shard)
            found = FORK_SHARD_RE.search(name)
            self.assertIsNotNone(found, f"{name} is invisible to the engine")
            self.assertEqual((int(found.group(1)), int(found.group(2))),
                             (layer, shard))

    def test_shard_name_template_refuses_a_drifted_matcher(self):
        with self.assertRaises(RuntimeError):
            self.module.shard_name_template(re.compile(r"shard_(\d+)\.weight$"))

    def test_shard_plan_matches_the_upstream_checkpoint_math(self):
        for vocab, parts in ((151936, 4), (151936, 512), (1000, 3), (128, 1)):
            expected_size = (vocab + parts - 1) // parts
            plan = self.module.shard_plan(vocab, parts)
            self.assertEqual(len(plan), parts)
            self.assertEqual([entry[0] for entry in plan], list(range(parts)))
            for index, start, rows in plan:
                # The runtime lookup is `shard = uniq // shard_size`, so every
                # block is full width and starts on its own multiple.
                self.assertEqual(rows, expected_size)
                self.assertEqual(start, index * expected_size)
            self.assertGreaterEqual(plan[-1][1] + plan[-1][2], vocab)

    def test_shard_plan_refuses_a_zero_split(self):
        with self.assertRaises(RuntimeError):
            self.module.shard_plan(1024, 0)

    def test_inventory_filter_shrinks_layers_and_experts(self):
        keep = self.module.keep_tensor
        self.assertTrue(keep("model.layers.3.self_attn.q_proj.weight", 4, 8))
        self.assertFalse(keep("model.layers.4.self_attn.q_proj.weight", 4, 8))
        self.assertTrue(keep("model.layers.1.mlp.experts.7.gate_proj.weight", 4, 8))
        self.assertFalse(keep("model.layers.1.mlp.experts.8.gate_proj.weight", 4, 8))
        self.assertTrue(keep("model.embed_tokens.weight", 4, 8))
        self.assertTrue(keep("lm_head.weight", 4, 8))
        # The vision tower is kept whole: the serve is text-only by flag, not
        # by amputating weights the loader still expects to find.
        self.assertTrue(keep("visual.blocks.31.attn.qkv.weight", 4, 8))

    def test_ignore_list_is_filtered_to_the_surviving_tree(self):
        survivors = self.module.surviving_ignore_list(
            ["model.layers.0.mlp.gate", "model.layers.9.mlp.gate",
             "model.layers.2.mlp.experts.9.down_proj"], 4, 8)
        self.assertEqual(survivors, ["model.layers.0.mlp.gate"])

    def test_config_key_setter_follows_a_nested_text_config(self):
        cfg = {"architectures": ["Qwen4ExpForConditionalGeneration"],
               "text_config": {"num_hidden_layers": 48}}
        self.assertEqual(self.module.set_config_key(cfg, "num_hidden_layers", 4), 1)
        self.assertEqual(cfg["text_config"]["num_hidden_layers"], 4)
        self.assertNotIn("num_hidden_layers", cfg)
        # An absent key is only created when the caller says it must exist.
        self.assertEqual(self.module.set_config_key(cfg, "split_ngram_parts", 4), 0)
        self.assertEqual(
            self.module.set_config_key(cfg, "split_ngram_parts", 4, force=True), 1)
        self.assertEqual(cfg["split_ngram_parts"], 4)
        self.assertEqual(self.module.get_config_key(cfg, "num_hidden_layers"), 4)

    def test_fixture_file_pattern_matches_the_forks_convention(self):
        # The fork's PLE fixture writes one file per (layer, shard); the
        # builder lifts that format string out of the fixture and falls back
        # to this documented literal only when the checkout cannot be read.
        self.assertIsNotNone(
            self.module.PLE_FILE_FMT_RE.search(self.module.PLE_FILE_FMT_FALLBACK))
        rendered = self.module.PLE_FILE_FMT_FALLBACK.format(layer_idx=3,
                                                            shard_index=2)
        self.assertEqual(rendered, "model-ple-3-00002.safetensors")

    def test_workload_is_read_header_only(self):
        # No real weight byte is loaded: the workload is mounted read-only and
        # only its safetensors headers and tokenizer files are opened.
        self.assertIn('-v "$WORKLOAD_DIR:/workload:ro"', self.text)
        self.assertIn('"weight_bytes_read": 0', self.text)


if __name__ == "__main__":
    unittest.main()
