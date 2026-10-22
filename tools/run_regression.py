#!/usr/bin/env python3
"""Offline unit, frozen-reference and historical-runtime regression entry point."""

from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

PROJECT = Path(__file__).resolve().parents[1]


def safe_output(path):
    path = Path(path)
    # Resolve all existing ancestors, including dangling or escaping links.
    for p in [path, *path.parents]:
        if p.is_symlink():
            raise ValueError("output paths may not contain symlinks")
    path = path.resolve()
    if path == PROJECT or not path.is_relative_to(PROJECT):
        raise ValueError("output must be within the project")
    if path.exists() and (not path.is_dir() or any(path.iterdir())):
        raise ValueError("output must be new or empty")
    return path


def environment(output, pythonpath=None):
    env = {
        k: v
        for k, v in os.environ.items()
        if k in ["PATH", "LANG", "LC_ALL", "SYSTEMROOT"]
    }
    tmp = output / "tmp"
    tmp.mkdir(parents=True, exist_ok=True)
    env.update(
        TMPDIR=str(tmp),
        PYTHONDONTWRITEBYTECODE="1",
        PYTHONNOUSERSITE="1",
        PYTHONPATH=pythonpath or "",
        EVIWEAVE_API_KEY="invalid-no-model-call",
        PIP_CACHE_DIR=str(PROJECT / ".audit/p3/cache/pip"),
        UV_CACHE_DIR=str(PROJECT / ".audit/p3/cache/uv"),
    )
    return env


def execute(command, output, env, name, timeout=180):
    log = output / (name + ".log")
    with log.open("w") as handle:
        p = subprocess.run(
            command,
            cwd=output,
            env=env,
            stdout=handle,
            stderr=subprocess.STDOUT,
            timeout=timeout,
        )
    if p.returncode == 2:
        raise ValueError(f"{name} required environment unavailable; see {log}")
    if p.returncode:
        raise AssertionError(f"{name} failed ({p.returncode}); see {log}")
    return {
        "status": "PASS",
        "command": command,
        "log": str(log),
        "returncode": p.returncode,
    }


def reference(output, frozen, env):
    manifest = json.loads((PROJECT / "provenance/extraction-manifest.json").read_text())
    # Copy only whitelist; no original registries or excluded modules are imported.
    base = output / "frozen"
    base.mkdir()
    for entry in manifest["files"]:
        relative = Path(entry["destination"]).relative_to("src")
        src = frozen / relative
        if (
            not src.is_file()
            or hashlib.sha256(src.read_bytes()).hexdigest() != entry["source_sha256"]
        ):
            raise ValueError("missing or changed frozen module: " + str(relative))
        target = base / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, target)
    records = []
    for name, pythonpath in [("frozen", str(base)), ("installed", env["PYTHONPATH"])]:
        worker = output / (name + "-worker")
        worker.mkdir()
        child = dict(env, PYTHONPATH=pythonpath)
        records.append(
            execute(
                [
                    sys.executable,
                    "-B",
                    str(PROJECT / "tools/regression_cases.py"),
                    "--output",
                    str(worker),
                ],
                output,
                child,
                name + "-worker",
            )
        )
    left = json.loads((output / "frozen-worker/results.json").read_text())
    right = json.loads((output / "installed-worker/results.json").read_text())
    assert all(Path(p).is_relative_to(base) for p in left["modules"].values())
    assert all(not Path(p).is_relative_to(base) for p in right["modules"].values())
    differences = []
    for a, b in zip(left["cases"], right["cases"]):
        if a != b:
            differences.append({"frozen": a, "installed": b})
    assert len(left["cases"]) == len(right["cases"])
    (output / "differences.json").write_text(
        json.dumps(differences, ensure_ascii=False, indent=2) + "\n"
    )
    assert not differences, "unexplained frozen behavior difference"
    return {
        "status": "PASS",
        "case_count": len(left["cases"]),
        "differences": 0,
        "workers": records,
        "normalization": [
            "OSError compared by type, errno and strerror; full message retained in case error.log"
        ],
        "module_origins": {"frozen": left["modules"], "installed": right["modules"]},
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--suite", choices=["unit", "reference", "runtime", "all"], default="unit"
    )
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--frozen-source", type=Path)
    parser.add_argument("--config", type=Path)
    args = parser.parse_args(argv)
    try:
        output = safe_output(args.output_root)
        if args.suite in ["reference", "all"] and not args.frozen_source:
            raise ValueError("--frozen-source required")
        if args.suite in ["runtime", "all"] and not args.config:
            raise ValueError("--config required")
        if args.frozen_source and not args.frozen_source.is_dir():
            raise ValueError("frozen source missing")
        if args.config and not args.config.is_file():
            raise ValueError("runtime config missing")
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    output.mkdir(parents=True, exist_ok=True)
    # Preserve only an explicit project source path, or use the selected installed interpreter.
    original = os.environ.get("PYTHONPATH", "")
    pythonpath = str(PROJECT / "src") if original == str(PROJECT / "src") else ""
    env = environment(output, pythonpath)
    report = {
        "status": "FAIL",
        "suite": args.suite,
        "python": sys.version,
        "model_calls": 0,
        "suites": {
            name: {"status": "NOT_RUN", "reason": "not selected"}
            for name in ["unit", "reference", "runtime"]
        },
    }
    code = 1
    try:
        if args.suite in ["unit", "all"]:
            report["suites"]["unit"] = {"status": "RUNNING"}
            report["suites"]["unit"] = execute(
                [
                    sys.executable,
                    "-B",
                    str(PROJECT / "tools/unit_worker.py"),
                    "--output",
                    str(output / "unit-results.json"),
                ],
                output,
                env,
                "unit",
            )
        if args.suite in ["reference", "all"]:
            report["suites"]["reference"] = {"status": "RUNNING"}
            report["suites"]["reference"] = reference(
                output, args.frozen_source.resolve(), env
            )
        if args.suite in ["runtime", "all"]:
            report["suites"]["runtime"] = {"status": "RUNNING"}
            report["suites"]["runtime"] = execute(
                [
                    sys.executable,
                    "-B",
                    str(PROJECT / "tools/runtime_regression.py"),
                    "--config",
                    str(args.config.resolve()),
                    "--output",
                    str(output / "runtime"),
                ],
                output,
                env,
                "runtime",
                300,
            )
        report["status"] = "PASS"
        code = 0
    except (FileNotFoundError, ValueError) as exc:
        report["error"] = str(exc)
        code = 2
    except (AssertionError, subprocess.SubprocessError, OSError) as exc:
        report["error"] = str(exc)
    for record in report["suites"].values():
        if record["status"] == "RUNNING":
            record.update(status="FAIL", reason=report.get("error", "unknown failure"))
    (output / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    )
    print(
        json.dumps(
            {
                "status": report["status"],
                "suite": args.suite,
                "report": str(output / "report.json"),
                "error": report.get("error"),
            }
        )
    )
    return code


if __name__ == "__main__":
    raise SystemExit(main())
