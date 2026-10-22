"""P3 handwritten behavioral expectations, independent of historical data."""

import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "tools"))
from regression_cases import shared_cases, task
from run_regression import safe_output
from dataspace_baselines.config import load_harness_experiment_config
from dataspace_baselines.harnesses import build_harness
from dataspace_baselines.harnesses.codex import _trace
from dataspace_baselines.core.batch import finalized_attempt, ensure_experiment_identity


class RegressionTests(unittest.TestCase):
    def setUp(self):
        (PROJECT / ".audit/p3/tmp").mkdir(parents=True, exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(dir=PROJECT / ".audit/p3/tmp")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_shared_behavioral_cases(self):
        records = shared_cases(self.root / "cases")
        self.assertGreater(len(records), 40)
        # Keep evidence outside TemporaryDirectory while cleaning case data.
        evidence = Path(os.environ["TMPDIR"]) / "shared-unit-cases.json"
        evidence.write_text(json.dumps(records, ensure_ascii=False, indent=2) + "\n")

    def test_historical_budgets_and_environment_contract(self):
        config = load_harness_experiment_config(PROJECT / "configs/codex.example.yaml")
        rootfs = self.root / "rootfs"
        (rootfs / "opt/dataspace").mkdir(parents=True)
        (rootfs / "opt/dataspace/TOOLS.md").write_text("synthetic tools")
        runtime = self.root / "runtime"
        runtime.mkdir()
        from dataclasses import replace

        harness = replace(
            config.harnesses["codex"],
            options=dict(config.harnesses["codex"].options, runtime_path=str(runtime)),
        )
        config = replace(
            config,
            workbench=replace(config.workbench, rootfs_path=rootfs),
            harnesses={"codex": harness},
        )
        runner = build_harness("codex", config)
        t = task(self.root)
        run = self.root / "run"
        run.mkdir()
        (run / "output").mkdir()
        with (
            patch("shutil.which", return_value="/usr/bin/bwrap"),
            patch.dict(
                os.environ,
                {
                    config.model.api_key_env: "invalid-no-model",
                    "OTHER_SECRET": "must-not-pass",
                },
                clear=True,
            ),
        ):
            inv = runner.invocation_factory(t, run, run / "output")
        text = (run / "runtime/home/.codex/config.toml").read_text()
        for value in [
            "model_context_window = 983616",
            "model_auto_compact_token_limit = 942656",
            'sandbox_mode = "workspace-write"',
            'approval_policy = "never"',
            "network_access = false",
            'writable_roots = ["/mnt/output"]',
            'persistence = "none"',
            "check_for_update_on_startup = false",
            'wire_api = "responses"',
        ]:
            self.assertIn(value, text)
        self.assertNotIn("projects.", text)
        self.assertFalse(inv.inherit_environment)
        self.assertNotIn("OTHER_SECRET", inv.environment)
        self.assertEqual(inv.environment["CODEX_HOME"], "/mnt/home/.codex")
        self.assertNotIn("--unshare-net", inv.command)
        self.assertIn("--strict-config", inv.command)
        self.assertIn("--ephemeral", inv.command)
        self.assertIn("simple-fixes-v1", inv.command[-1])
        self.assertIn("/mnt/runtime/dataspace-export.py", inv.command)
        self.assertEqual(config.concurrency, 1)
        self.assertEqual(config.wall_time_seconds, 1800)
        baseline = runner.resume_fingerprint
        variants = [
            replace(config, model=replace(config.model, model="changed")),
            replace(
                config,
                model=replace(
                    config.model, openai_base_url="https://example.invalid/changed"
                ),
            ),
            replace(config, model=replace(config.model, max_output_tokens=123)),
            replace(config, wall_time_seconds=123),
            replace(config, workbench=replace(config.workbench, image="changed")),
            replace(
                config,
                harnesses={
                    "codex": replace(
                        harness, options=dict(harness.options, simple_fixes=False)
                    )
                },
            ),
            replace(
                config,
                harnesses={
                    "codex": replace(
                        harness, options=dict(harness.options, wire_api="chat")
                    )
                },
            ),
        ]
        for variant in variants:
            self.assertNotEqual(
                baseline, build_harness("codex", variant).resume_fingerprint
            )
        from dataspace_baselines.core.batch import ensure_experiment_identity

        (self.root / "identity").mkdir()
        ensure_experiment_identity(
            self.root / "identity", runner, resume=False, had_existing_artifacts=False
        )
        for variant in variants:
            with self.assertRaisesRegex(ValueError, "different experiment"):
                ensure_experiment_identity(
                    self.root / "identity",
                    build_harness("codex", variant),
                    resume=True,
                    had_existing_artifacts=True,
                )

    def test_trace_missing_usage_and_malformed_lines(self):
        events = [
            {
                "type": "item.started",
                "item": {"id": "one", "type": "command_execution"},
            },
            {
                "type": "item.completed",
                "item": {"id": "one", "type": "command_execution"},
            },
            {"type": "item.started", "item": {"type": "file_change"}},
            {"type": "item.completed", "item": {"type": "file_change"}},
            {"type": "turn.completed"},
        ]
        p = self.root / "stdout.log"
        p.write_text("bad JSON\n[]\n" + "\n".join(map(json.dumps, events)))
        self.assertEqual(_trace(self.root), (1, 2, {}))
        p.write_text(
            json.dumps(
                {
                    "type": "turn.completed",
                    "usage": {"input_tokens": 3, "output_tokens": 2},
                }
            )
        )
        self.assertEqual(
            _trace(self.root), (1, 0, {"input_tokens": 3, "output_tokens": 2})
        )

    def test_question_change_creates_pending_attempt(self):
        t = task(self.root)
        a = self.root / "attempts/run_1"
        a.mkdir(parents=True)
        (a / "run.json").write_text('{"status":"submitted"}')
        (a / "events.jsonl").write_text(
            json.dumps({"event": "run_started", "question": "older question"}) + "\n"
        )
        self.assertIsNone(finalized_attempt(a.parent, t))

    def test_output_root_rejects_escape_symlink_and_nonempty(self):
        for path in [PROJECT, PROJECT.parent, self.root / "../../../../../../escape"]:
            with self.assertRaises(ValueError):
                safe_output(path)
        link = self.root / "link"
        link.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(ValueError):
            safe_output(link / "child")
        (self.root / "occupied").touch()
        with self.assertRaises(ValueError):
            safe_output(self.root)

    def test_codex_budget_exit_markers(self):
        from dataspace_baselines.harnesses.codex import _exit_classification

        self.assertIsNone(_exit_classification(self.root, 1))
        path = self.root / "stdout.log"
        for text in ["reason: max_output_tokens", "reason=max_output_tokens"]:
            path.write_text(text)
            self.assertEqual(
                _exit_classification(self.root, 1),
                (
                    "model_output_exhausted",
                    "Codex model response was truncated by max_output_tokens",
                ),
            )
        path.write_text("transport failure; no budget marker")
        self.assertIsNone(_exit_classification(self.root, 1))
