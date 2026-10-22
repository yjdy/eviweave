from __future__ import annotations
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from dataspace_baselines.config import (
    DataWorkbenchConfig,
    ExperimentModelConfig,
    HarnessConfig,
    HarnessExperimentConfig,
)
from dataspace_baselines.core.task import TaskSpec
from dataspace_baselines.harnesses.codex import build_codex, _trace as codex_trace
from dataspace_baselines.harnesses.native_common import configured_runtime_binds


def _config(harness_key: str, executable: str, root: Path) -> HarnessExperimentConfig:
    rootfs = root / "workbench-rootfs"
    tools = rootfs / "opt" / "dataspace" / "TOOLS.md"
    tools.parent.mkdir(parents=True, exist_ok=True)
    tools.write_text("test tools\n", encoding="utf-8")
    bwrap = rootfs / "usr" / "bin" / "bwrap"
    bwrap.parent.mkdir(parents=True, exist_ok=True)
    bwrap.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    bwrap.chmod(0o755)
    socat = rootfs / "usr" / "bin" / "socat"
    socat.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    socat.chmod(0o755)
    return HarnessExperimentConfig(
        model=ExperimentModelConfig(
            key="mimo-v2.5",
            model="xiaomi/mimo-v2.5",
            api_key_env="TEST_GATEWAY_KEY",
            openai_base_url="https://gateway.test/v1",
            anthropic_base_url="https://gateway.test",
        ),
        wall_time_seconds=1800,
        concurrency=8,
        workbench=DataWorkbenchConfig(
            image="sandbox:test",
            rootfs_path=rootfs,
        ),
        harnesses={
            harness_key: HarnessConfig(
                key=harness_key,
                executable=executable,
            )
        },
    )


def _task(root: Path) -> TaskSpec:
    task_dir = root / "task_1"
    context = task_dir / "context"
    context.mkdir(parents=True)
    (context / "data.csv").write_text("answer\n42\n", encoding="utf-8")
    (task_dir / "task.json").write_text(
        json.dumps({"task_id": "task_1", "question": "Return the answer."}),
        encoding="utf-8",
    )
    return TaskSpec.load(task_dir)


class HistoricalCodexTests(unittest.TestCase):
    def test_runtime_path_expands_pinned_version(self) -> None:
        harness = HarnessConfig(
            key="codex",
            options={
                "version": "1.2.3",
                "runtime_path": "/tmp/codex-{version}",
                "runtime_mount": "/mnt/runtime/codex",
            },
        )
        self.assertEqual(
            configured_runtime_binds(harness),
            ((Path("/tmp/codex-1.2.3"), "/mnt/runtime/codex"),),
        )

    def _invocation(self, builder, key: str, executable: str):
        temporary = tempfile.TemporaryDirectory(dir=os.environ["TMPDIR"])
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        task = _task(root)
        run_root = root / "run"
        output_root = run_root / "output"
        run_root.mkdir()
        output_root.mkdir()
        config = _config(key, executable, root)
        harness = config.harnesses[key]
        with (
            patch("shutil.which", return_value="/usr/bin/bwrap"),
            patch.dict(os.environ, {"TEST_GATEWAY_KEY": "test-secret"}),
        ):
            runner = builder(config, harness)
            invocation = runner.invocation_factory(task, run_root, output_root)
        return run_root, invocation

    def test_codex_disables_web_and_command_network(self) -> None:
        run_root, invocation = self._invocation(build_codex, "codex", "/bin/codex")
        config = (run_root / "runtime/home/.codex/config.toml").read_text(
            encoding="utf-8"
        )
        self.assertIn('web_search = "disabled"', config)
        self.assertIn("network_access = false", config)
        self.assertIn("supports_websockets = false", config)
        self.assertIn("enable_request_compression = false", config)
        self.assertIn("model_context_window = 200000", config)
        self.assertIn("model_auto_compact_token_limit = 159040", config)
        self.assertIn('exclude = ["TEST_GATEWAY_KEY"', config)
        self.assertIn("allow_login_shell = false", config)
        self.assertIn("--ephemeral", invocation.command)
        self.assertNotIn("test-secret", " ".join(invocation.command))
        self.assertFalse(invocation.inherit_environment)
        self.assertEqual(invocation.metadata["request_compression"], "disabled")

    def test_codex_chat_uses_isolated_compatible_runtime(self) -> None:
        temporary = tempfile.TemporaryDirectory(dir=os.environ["TMPDIR"])
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        task = _task(root)
        run_root = root / "run"
        output_root = run_root / "output"
        runtime = root / "codex-runtime"
        runtime.mkdir()
        run_root.mkdir()
        output_root.mkdir()
        config = _config("codex", "/mnt/runtime/codex-0.80.0/codex/codex", root)
        harness = HarnessConfig(
            key="codex",
            executable="/mnt/runtime/codex-0.80.0/codex/codex",
            options={
                "version": "0.80.0",
                "wire_api": "chat",
                "runtime_path": str(runtime),
                "runtime_mount": "/mnt/runtime/codex-0.80.0",
            },
        )
        with (
            patch("shutil.which", return_value="/usr/bin/bwrap"),
            patch.dict(os.environ, {"TEST_GATEWAY_KEY": "test-secret"}),
        ):
            runner = build_codex(config, harness)
            invocation = runner.invocation_factory(task, run_root, output_root)

        codex_config = (run_root / "runtime/home/.codex/config.toml").read_text(
            encoding="utf-8"
        )
        self.assertIn('wire_api = "chat"', codex_config)
        self.assertNotIn("supports_websockets", codex_config)
        self.assertNotIn("--strict-config", invocation.command)
        self.assertNotIn("--ephemeral", invocation.command)
        self.assertNotIn("--ignore-rules", invocation.command)
        self.assertIn(str(runtime), invocation.command)
        self.assertIn("/mnt/runtime/codex-0.80.0", invocation.command)
        self.assertEqual(invocation.metadata["wire_api"], "chat_completions")
        self.assertEqual(invocation.metadata["runtime_version"], "0.80.0")

    def test_codex_trace_counts_each_tool_item_once(self) -> None:
        with tempfile.TemporaryDirectory(dir=os.environ["TMPDIR"]) as temporary:
            run_root = Path(temporary)
            events = [
                {
                    "type": "item.started",
                    "item": {
                        "id": "item_1",
                        "type": "command_execution",
                    },
                },
                {
                    "type": "item.completed",
                    "item": {
                        "id": "item_1",
                        "type": "command_execution",
                    },
                },
                {
                    "type": "item.started",
                    "item": {"id": "item_2", "type": "file_change"},
                },
                {
                    "type": "item.completed",
                    "item": {"id": "item_2", "type": "file_change"},
                },
                {
                    "type": "turn.completed",
                    "usage": {"input_tokens": 20, "output_tokens": 5},
                },
            ]
            (run_root / "stdout.log").write_text(
                "\n".join(json.dumps(event) for event in events) + "\n",
                encoding="utf-8",
            )
            turns, actions, usage = codex_trace(run_root)

        self.assertEqual(turns, 1)
        self.assertEqual(actions, 2)
        self.assertEqual(usage, {"input_tokens": 20, "output_tokens": 5})
