"""Explicit mandatory runtime suite: absent runtime is an error, never a skip."""

import argparse
import json
import os
from pathlib import Path
import sys
import unittest
import shutil

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "tools"))
from unit_worker import RecordingResult

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    os.environ["EVIWEAVE_REGRESSION_OUTPUT"] = str(output)
    os.environ["EVIWEAVE_REGRESSION_CONFIG"] = str(args.config.resolve())
    from dataspace_baselines.config import load_harness_experiment_config
    from dataspace_baselines.harnesses.native_common import configured_runtime_binds

    try:
        config = load_harness_experiment_config(args.config)
        roots = [
            config.workbench.rootfs_path,
            configured_runtime_binds(config.harnesses["codex"])[0][0],
        ]
        if not shutil.which("bwrap") or not all(p.is_dir() for p in roots):
            raise ValueError("historical runtime or Bubblewrap unavailable")
    except (OSError, ValueError, KeyError, IndexError) as exc:
        (output / "runtime-report.json").write_text(
            json.dumps(
                {
                    "status": "FAIL",
                    "reason": str(exc),
                    "kind": "required environment unavailable",
                }
            )
            + "\n"
        )
        raise SystemExit(2)
    suite = unittest.defaultTestLoader.discover(str(PROJECT / "tests/integration"))
    result = unittest.TextTestRunner(verbosity=2, resultclass=RecordingResult).run(
        suite
    )
    report = {
        "status": "PASS" if result.wasSuccessful() and not result.skipped else "FAIL",
        "cases": result.cases,
        "model_calls": 0,
        "production_outer": "original; no additional network namespace",
        "inner_execution": "NOT_RUN; config/read diagnostic only",
        "diagnostic_network": "additional unshare-net for lifecycle and P2 smoke",
        "config": str(args.config.resolve()),
    }
    (output / "runtime-report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    )
    raise SystemExit(0 if report["status"] == "PASS" else 1)
