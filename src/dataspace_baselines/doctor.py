from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .config import HarnessExperimentConfig
from .runners.filesystem_jail import BubblewrapJail
from .harnesses.native_common import configured_runtime_binds
from .workbench import load_export_manifest


@dataclass(frozen=True)
class DoctorCheck:
    name: str
    ok: bool
    detail: str


def _command_check(name: str, command: list[str]) -> DoctorCheck:
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=False,
        )
    except Exception as exc:
        return DoctorCheck(name, False, f"{type(exc).__name__}: {exc}")
    output = (result.stdout or result.stderr).strip().splitlines()
    detail = output[-1] if output else f"exit code {result.returncode}"
    return DoctorCheck(name, result.returncode == 0, detail)


def _image_check(name: str, image: str) -> DoctorCheck:
    check = _command_check(
        name,
        ["docker", "image", "inspect", image, "--format", "{{.Id}}"],
    )
    return DoctorCheck(check.name, check.ok, f"{image}: {check.detail}")


def _jailed_version(
    name: str,
    executable: str,
    runtime_binds: tuple[tuple[Path, str], ...] = (),
    rootfs_root: Path | None = None,
    version_args: tuple[str, ...] = ("--version",),
) -> DoctorCheck:
    if shutil.which("bwrap") is None:
        return DoctorCheck(name, False, "bubblewrap is unavailable")
    try:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for directory in ("context", "output", "home"):
                (root / directory).mkdir()
            command = BubblewrapJail().command(
                task_context=root / "context",
                output_root=root / "output",
                home_root=root / "home",
                inner_command=(executable, *version_args),
                runtime_binds=runtime_binds,
                rootfs_root=rootfs_root,
            )
            return _command_check(name, list(command))
    except Exception as exc:
        return DoctorCheck(name, False, f"{type(exc).__name__}: {exc}")


def _workbench_rootfs_check(config: HarnessExperimentConfig) -> DoctorCheck:
    rootfs = config.workbench.rootfs_path
    tools = rootfs / "opt" / "dataspace" / "TOOLS.md"
    capabilities = rootfs / "opt" / "dataspace" / "capabilities.json"
    manifest = load_export_manifest(config.workbench)
    recorded_image = manifest.get("image")
    if not rootfs.is_dir():
        return DoctorCheck("Data Workbench rootfs", False, f"missing: {rootfs}")
    if not tools.is_file() or not capabilities.is_file():
        return DoctorCheck(
            "Data Workbench rootfs",
            False,
            "TOOLS.md or capabilities.json is missing",
        )
    if recorded_image != config.workbench.image:
        return DoctorCheck(
            "Data Workbench rootfs",
            False,
            f"manifest image {recorded_image!r}; expected {config.workbench.image!r}",
        )
    image_check = _command_check(
        "Data Workbench image",
        ["docker", "image", "inspect", config.workbench.image, "--format", "{{.Id}}"],
    )
    current_image_id = image_check.detail if image_check.ok else ""
    recorded_image_id = manifest.get("image_id")
    if not image_check.ok or current_image_id != recorded_image_id:
        return DoctorCheck(
            "Data Workbench rootfs",
            False,
            f"exported image id {recorded_image_id!r}; current image id "
            f"{current_image_id or 'unavailable'!r}",
        )
    return DoctorCheck(
        "Data Workbench rootfs",
        True,
        f"{rootfs}; image id {manifest.get('image_id', 'unrecorded')}",
    )


def _workbench_tools_check(config: HarnessExperimentConfig) -> DoctorCheck:
    if shutil.which("bwrap") is None:
        return DoctorCheck("Data Workbench tools", False, "bubblewrap is unavailable")
    try:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for directory in ("context", "output", "home"):
                (root / directory).mkdir()
            python_check = (
                "import cv2, fitz, numpy, pandas, pdfplumber, PIL, pyarrow, "
                "pypdf, pytesseract"
            )
            shell_check = (
                "command -v bwrap >/dev/null && "
                "command -v ffmpeg >/dev/null && "
                "command -v ffprobe >/dev/null && "
                "command -v jq >/dev/null && "
                "command -v pdftotext >/dev/null && "
                "command -v socat >/dev/null && "
                "command -v sqlite3 >/dev/null && "
                "command -v tesseract >/dev/null && "
                f"python3 -c {python_check!r}"
            )
            command = BubblewrapJail().command(
                task_context=root / "context",
                output_root=root / "output",
                home_root=root / "home",
                inner_command=("/bin/sh", "-c", shell_check),
                rootfs_root=config.workbench.rootfs_path,
            )
            check = _command_check("Data Workbench tools", list(command))
            if check.ok:
                return DoctorCheck(check.name, True, "all required tools are available")
            return check
    except Exception as exc:
        return DoctorCheck(
            "Data Workbench tools", False, f"{type(exc).__name__}: {exc}"
        )


def run_doctor(config: HarnessExperimentConfig) -> list[DoctorCheck]:
    checks: list[DoctorCheck] = []
    checks.append(
        DoctorCheck(
            "inference credential",
            bool(os.environ.get(config.model.api_key_env)),
            f"environment variable {config.model.api_key_env} "
            + ("is set" if os.environ.get(config.model.api_key_env) else "is unset"),
        )
    )
    checks.append(
        _command_check("Docker daemon", ["docker", "version", "--format", "{{.Server.Version}}"])
    )
    checks.append(
        DoctorCheck(
            "Bubblewrap",
            shutil.which("bwrap") is not None,
            shutil.which("bwrap") or "executable is unavailable",
        )
    )
    checks.append(_image_check("Data Workbench image", config.workbench.image))
    checks.append(_workbench_rootfs_check(config))
    checks.append(_workbench_tools_check(config))

    for key, harness in config.harnesses.items():
        if key != "codex":
            checks.append(DoctorCheck(key, False, "unsupported harness; only codex is supported"))
            continue
        if not harness.enabled:
            continue
        if not harness.executable:
            checks.append(DoctorCheck(key, False, "executable is not configured"))
            continue
        expected = str(harness.options.get("version", "")).strip()
        try:
            runtime_binds = configured_runtime_binds(harness)
        except (ValueError, OSError) as exc:
            checks.append(DoctorCheck(key, False, str(exc)))
            continue
        check = _jailed_version(
            key, harness.executable, runtime_binds, config.workbench.rootfs_path,
        )
        if check.ok and expected and expected not in check.detail:
            check = DoctorCheck(check.name, False, f"{check.detail}; expected version {expected}")
        checks.append(check)
    return checks
