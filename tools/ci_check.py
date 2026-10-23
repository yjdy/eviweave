"""Local equivalent of lightweight CI: build sdist, build wheel from it, install and test."""

from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile

from run_regression import safe_output, environment

PROJECT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--locked", action="store_true", help="also require this host to match the reproduction/compatibility profile")
    parser.add_argument('--wheel-cache', type=Path, help='verified project-local locked wheels; use no index for installation')
    args = parser.parse_args()
    if args.locked:
        from environment_lock import verify
        verify(runtime=False)
    output = safe_output(args.output_root)
    output.mkdir(parents=True)
    env = environment(output)
    cache = None
    if args.wheel_cache:
        from public_resources import private_path
        from environment_lock import profile, check_artifact
        cache = private_path(args.wheel_cache)
        for entry in profile()['packages'].values():
            check_artifact(cache / entry['filename'], entry)
    commands = []

    def run(cmd, name, cwd=None):
        with (output / (name + ".log")).open("w") as log:
            p = subprocess.run(
                cmd,
                cwd=cwd or output,
                env=env,
                stdout=log,
                stderr=subprocess.STDOUT,
                timeout=300,
            )
        commands.append(
            {
                "command": cmd,
                "returncode": p.returncode,
                "log": str(output / (name + ".log")),
            }
        )
        assert p.returncode == 0, name

    def prepare_python(py, name):
        run([sys.executable, '-B', '-m', 'pip', '--python', str(py), 'install',
             *(['--no-index', '--find-links', str(cache)] if cache else []),
             '--only-binary=:all:', '--require-hashes', '-r', str(PROJECT / 'locks/ci-tools.txt')], name)

    report = {
        "status": "FAIL",
        "python": sys.version,
        "hosted_ci": "NOT_RUN",
        "commands": commands,
    }
    try:
        # Ordinary installation is checked separately from the sdist-built wheel.
        ordinary = output / "ordinary-env"
        run(
            [sys.executable, "-B", "-m", "venv", "--without-pip", str(ordinary)],
            "ordinary-venv",
        )
        py = ordinary / "bin/python"
        prepare_python(py, "ordinary-locked-tools")
        run(
            [
                sys.executable,
                "-B",
                "-m",
                "pip",
                "--python",
                str(py),
                "install",
                "--no-build-isolation", "--no-deps",
                str(PROJECT),
            ],
            "ordinary-install",
        )
        run(
            [
                str(py),
                "-B",
                str(PROJECT / "tools/check_installation.py"),
                "--output",
                str(output / "ordinary-install.json"),
            ],
            "ordinary-check",
        )
        archives = output / "archives"
        run(
            [
                sys.executable,
                "-B",
                "-m",
                "build",
                "--sdist", "--no-isolation",
                "--outdir",
                str(archives),
                str(PROJECT),
            ],
            "sdist-build",
        )
        archive = next(archives.glob("*.tar.gz"))
        unpacked = output / "unpacked"
        unpacked.mkdir()
        with tarfile.open(archive) as tar:
            for member in tar.getmembers():
                assert (
                    not Path(member.name).is_absolute()
                    and ".." not in Path(member.name).parts
                    and not member.issym()
                    and not member.islnk()
                )
            tar.extractall(unpacked)
        source = next(unpacked.iterdir())
        run(
            [
                sys.executable,
                "-B",
                "-m",
                "build",
                "--wheel", "--no-isolation",
                "--outdir",
                str(archives),
                str(source),
            ],
            "wheel-from-sdist",
        )
        install = output / "wheel-env"
        run(
            [sys.executable, "-B", "-m", "venv", "--without-pip", str(install)],
            "wheel-venv",
        )
        py = install / "bin/python"
        prepare_python(py, "wheel-locked-tools")
        run(
            [
                sys.executable,
                "-B",
                "-m",
                "pip",
                "--python",
                str(py),
                "install", "--no-deps",
                str(next(archives.glob("*.whl"))),
            ],
            "wheel-install",
        )
        run(
            [
                str(py),
                "-B",
                str(PROJECT / "tools/check_installation.py"),
                "--output",
                str(output / "wheel-install.json"),
            ],
            "wheel-check",
        )
        # Run packaged tests/tools from the unpacked sdist, not the source worktree.
        run(
            [
                str(py),
                "-B",
                str(source / "tools/run_regression.py"),
                "--suite",
                "unit",
                "--output-root",
                str(source / ".audit/unit"),
            ],
            "installed-unit",
        )
        report["status"] = "PASS"
        report["archives"] = str(archives)
    finally:
        (output / "ci-report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n"
        )


if __name__ == "__main__":
    main()
