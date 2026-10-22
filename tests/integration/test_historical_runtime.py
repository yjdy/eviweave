"""Mandatory real historical-rootfs checks, explicitly excluded from ordinary unit discovery."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest

PROJECT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT / "tools"))
from config_lifecycle import read_config
from dataspace_baselines.config import load_harness_experiment_config
from dataspace_baselines.harnesses import build_harness
from dataspace_baselines.harnesses.codex import EXPORT_HELPER_PATH, EXPORT_HELPER_MOUNT
from dataspace_baselines.harnesses.native_common import configured_runtime_binds
from dataspace_baselines.core.task import TaskSpec
from dataspace_baselines.core.batch import BatchOptions, run_batch


class HistoricalRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.output = Path(os.environ["EVIWEAVE_REGRESSION_OUTPUT"])
        cls.output.mkdir(parents=True, exist_ok=True)
        cls.config = load_harness_experiment_config(
            Path(os.environ["EVIWEAVE_REGRESSION_CONFIG"])
        )
        assert (
            cls.config.model.openai_base_url == "https://example.invalid/v1"
            and not cls.config.model.anthropic_base_url
        )
        assert cls.config.harnesses["codex"].options["version"] == "0.145.0"
        baseline = json.loads(
            (PROJECT / "provenance/runtime-baseline.json").read_text()
        )
        assert cls.config.workbench.rootfs_path.resolve().is_relative_to(PROJECT)
        tools = cls.config.workbench.rootfs_path / "opt/dataspace/TOOLS.md"
        assert (
            hashlib.sha256(tools.read_bytes()).hexdigest()
            == baseline["historical_identity"]["tools_sha256"]
        )
        runtime = configured_runtime_binds(cls.config.harnesses["codex"])[0][0]
        assert runtime.is_relative_to(PROJECT)
        assert (
            hashlib.sha256((runtime / "bin/codex").read_bytes()).hexdigest()
            == baseline["historical_identity"]["codex_binary_sha256"]
        )
        # Complete inventories are verified before any runtime test, no fallback rootfs.
        sys.path.insert(0, str(PROJECT / "scripts"))
        from runtime_support import inventory, differences

        workbench = cls.config.workbench.rootfs_path.parent
        platform = runtime.parents[1]
        inventories = []
        for name, root, manifest in [
            (
                "workbench",
                workbench,
                workbench.parent / (workbench.name + "-files.json"),
            ),
            ("codex", platform, platform.parent / "runtime-files.json"),
        ]:
            expected = json.loads(manifest.read_text())
            actual = inventory(root)
            assert (
                hashlib.sha256(manifest.read_bytes()).hexdigest()
                == baseline["first_recorded_in_P2"][name + "_inventory_sha256"]
            )
            changes = differences(expected, actual)
            assert not changes, changes[:3]
            inventories.append(
                {
                    "name": name,
                    "root": str(root),
                    "manifest": str(manifest),
                    "entries": len(actual["entries"]),
                    "differences": len(changes),
                }
            )
        (cls.output / "runtime-inventories.json").write_text(
            json.dumps(inventories, indent=2) + "\n"
        )
        cls.runtime = runtime

    def runner(self):
        runner = build_harness("codex", self.config)
        runner.wall_time_seconds = 15
        original = runner.invocation_factory

        def factory(task, run, output):
            inv = original(task, run, output)
            # Create real neighboring attempt sentinels before either process starts.
            # The batch has already allocated both run_1 jobs, so this does not
            # influence attempt selection or use another task's real data.
            if run.name == "run_1":
                peer = (
                    run.parent.parent
                    / ("task_2" if task.task_id == "task_1" else "task_1")
                    / "run_1"
                )
                blocked = []
                for relative in [
                    "output/cross-task-private",
                    "runtime/home/cross-task-private",
                ]:
                    sentinel = peer / relative
                    sentinel.parent.mkdir(parents=True, exist_ok=True)
                    sentinel.write_text("other task private sentinel")
                    blocked.append(str(sentinel))
                settings_path = task.context_dir / "probe.json"
                settings = json.loads(settings_path.read_text())
                settings["blocked"].extend(blocked)
                settings["actual_peer_attempt_sentinels"] = blocked
                settings_path.write_text(json.dumps(settings))
            cmd = list(inv.command)
            i = cmd.index("--")
            assert cmd[i + 1 : i + 3] == [
                self.config.harnesses["codex"].executable,
                "exec",
            ]
            cmd[i:] = [
                "--ro-bind",
                str(PROJECT / "tools/sandbox_probe.py"),
                "/mnt/runtime/sandbox-probe.py",
                "--",
                "/usr/local/bin/python3",
                "/mnt/runtime/sandbox-probe.py",
            ]
            assert "--unshare-net" not in cmd
            (run / "outer-invocation.json").write_text(
                json.dumps(
                    {
                        "command": cmd,
                        "original_inner": list(inv.command[i + 1 :]),
                        "model_calls": 0,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n"
            )
            return replace(
                inv,
                command=tuple(cmd),
                environment={
                    self.config.model.api_key_env: "invalid-no-model-call",
                    "LANG": "C.UTF-8",
                    "CODEX_HOME": "/mnt/home/.codex",
                },
                inherit_environment=False,
            )

        runner.invocation_factory = factory
        return runner

    def inputs(self, root):
        root.mkdir(parents=True, exist_ok=True)
        outside = root / "private-sentinel"
        outside.write_text("private")
        for n in [1, 2]:
            t = root / f"task_{n}"
            context = t / "context"
            context.mkdir(parents=True)
            (t / "output").mkdir()
            (t / "output/private").write_text("other output")
            (t / "home").mkdir()
            (t / "home/private").write_text("other home")
            (t / "task.json").write_text(
                json.dumps({"task_id": t.name, "question": "Artificial sandbox probe."})
            )
            (context / "data.txt").write_text(t.name)
            (context / "escape-absolute").symlink_to(outside)
            (context / "escape-relative").symlink_to("../../private-sentinel")
            other = root / f"task_{3-n}"
            blocked = [
                str(outside),
                str(context / "data.txt"),
                str(other / "context/data.txt"),
                str(other / "output/private"),
                str(other / "home/private"),
                "/mnt/workspace/escape-absolute",
                "/mnt/workspace/escape-relative",
                "/mnt/workspace/../../" + str(outside).lstrip("/"),
                "/proc/1/root" + str(outside),
            ]
            (context / "probe.json").write_text(
                json.dumps(
                    {
                        "marker": t.name,
                        "blocked": blocked,
                        "host_pid": os.getpid(),
                        "host_pidns": os.readlink("/proc/self/ns/pid"),
                        "host_netns": os.readlink("/proc/self/ns/net"),
                    }
                )
            )
        return root

    def verify(self, attempt, n):
        report = json.loads((attempt / "output/probe-report.json").read_text())
        self.assertEqual(report["status"], "PASS")
        self.assertTrue(all(report["checks"].values()))
        self.assertEqual(
            (attempt / "output/prediction.csv").read_bytes(),
            f"task,value\ntask_{n},000001\n".encode(),
        )
        self.assertEqual((attempt / "runtime/home/writable").read_text(), f"task_{n}")
        self.assertEqual(json.loads((attempt / "run.json").read_text())["usage"], {})
        return report

    def test_original_outer_single(self):
        root = self.output / "original outer single"
        inputs = self.inputs(root / "input space")
        runner = self.runner()
        result = runner.run(TaskSpec.load(inputs / "task_1"), root / "single output")
        self.assertEqual(result.status, "submitted", result.error)
        self.verify(root / "single output", 1)

    def test_original_outer_sequential_and_concurrent_resume(self):
        for concurrency in [1, 2]:
            root = self.output / f"original outer batch {concurrency}"
            inputs = self.inputs(root / "input space")
            runner = self.runner()
            runroot = root / "batch output"
            self.assertEqual(
                run_batch(
                    BatchOptions(
                        input_root=inputs, run_root=runroot, concurrency=concurrency
                    ),
                    runner,
                ),
                0,
            )
            for n in [1, 2]:
                self.verify(runroot / f"tasks/task_{n}/run_1", n)
                self.assertEqual(
                    (runroot / f"predictions/task_{n}/prediction.csv").read_bytes(),
                    (
                        runroot / f"tasks/task_{n}/run_1/output/prediction.csv"
                    ).read_bytes(),
                )
            self.assertEqual(
                run_batch(
                    BatchOptions(
                        input_root=inputs,
                        run_root=runroot,
                        concurrency=concurrency,
                        resume=True,
                    ),
                    runner,
                ),
                0,
            )
            self.assertEqual(len(list(runroot.glob("tasks/task_*/run_*/run.json"))), 2)

    def test_inner_configuration_lifecycle(self):
        root = self.output / "configuration lifecycle"
        inputs = self.inputs(root / "inputs")
        configs = []
        for trusted in [False, True]:
            run = root / str(trusted)
            run.mkdir()
            (run / "output").mkdir()
            runner = build_harness("codex", self.config)
            inv = runner.invocation_factory(
                TaskSpec.load(inputs / "task_1"), run, run / "output"
            )
            command = list(inv.command)
            i = command.index("--")
            command[i + 1 :] = [
                self.config.harnesses["codex"].executable,
                "--strict-config",
                "app-server",
                "--listen",
                "stdio://",
            ]
            command.insert(1, "--unshare-net")
            f = run / "runtime/home/.codex/config.toml"
            if trusted:
                f.write_text(
                    f.read_text()
                    + '\n[projects."/mnt/output"]\ntrust_level = "trusted"\n'
                )
            before = hashlib.sha256(f.read_bytes()).hexdigest()
            response = read_config(command, dict(inv.environment), run / "diagnostic")
            after = hashlib.sha256(f.read_bytes()).hexdigest()
            self.assertEqual(before, after)
            config = response["config"]
            self.assertEqual(config["sandbox_mode"], "workspace-write")
            self.assertEqual(config["approval_policy"], "never")
            self.assertEqual(config["sandbox_workspace_write"]["network_access"], False)
            self.assertEqual(
                config["sandbox_workspace_write"]["writable_roots"], ["/mnt/output"]
            )
            configs.append(config)
        differences = {
            key: {"initial": configs[0].get(key), "trusted": configs[1].get(key)}
            for key in configs[0].keys() | configs[1].keys()
            if configs[0].get(key) != configs[1].get(key)
        }
        self.assertEqual(
            differences,
            {
                "projects": {
                    "initial": None,
                    "trusted": {"/mnt/output": {"trust_level": "trusted"}},
                }
            },
        )
        (root / "comparison.json").write_text(
            json.dumps(
                {
                    "status": "PASS",
                    "differences": differences,
                    "evidence": "local config/read; no inference turn or inner shell execution",
                    "historical_writer": "unproven",
                    "model_calls": 0,
                },
                indent=2,
            )
            + "\n"
        )

    def test_p2_additional_network_isolated_smoke(self):
        config = Path(os.environ["EVIWEAVE_REGRESSION_CONFIG"]).resolve()
        command = [
            sys.executable,
            "-B",
            str(PROJECT / "tools/smoke_runtime.py"),
            "--config",
            str(config),
            "--output-root",
            str(self.output / "additional-network-smoke"),
        ]
        with (self.output / "additional-network-smoke.log").open("w") as log:
            result = subprocess.run(
                command, stdout=log, stderr=subprocess.STDOUT, timeout=60
            )
        self.assertEqual(result.returncode, 0)
