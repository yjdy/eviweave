from __future__ import annotations

import os
import hashlib
import json
import platform
import stat
from concurrent.futures import ThreadPoolExecutor
import shutil
import shlex
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
    skipped: bool = False


def _command_check(name: str, command: list[str], *, environment: dict[str, str] | None = None) -> DoctorCheck:
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=False,
            env=environment,
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
    temp_dir: Path | None = None,
) -> DoctorCheck:
    if shutil.which("bwrap") is None:
        return DoctorCheck(name, False, "bubblewrap is unavailable")
    try:
        with tempfile.TemporaryDirectory(dir=temp_dir) as temporary:
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
            return _command_check(name, list(command), environment=_sandbox_environment())
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
    pinned = {
        "TOOLS.md": "71faab0935ae2f9740c2a797816bae4d31c94065349519f5b80a9f7a20141c17",
        "capabilities.json": "4a0c3bed49e0227533694f8dbf96007a02b23626cc590423bfbed5976297aba0",
        "python-packages.txt": "3c562d1857234a304df875f9fb7b18cd7724e3d50260b1e3494aa6326e1bcbfe",
        "system-packages.txt": "965604c7db2b4e43d68c35734a060eaf67b1020fc787225051543afebe607935",
    }
    for filename, expected in pinned.items():
        path = tools.parent / filename
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            return DoctorCheck("Data Workbench rootfs", False, filename + " differs from historical baseline")
    if manifest.get("tools_sha256") != pinned["TOOLS.md"]:
        return DoctorCheck("Data Workbench rootfs", False, "export manifest TOOLS.md digest mismatch")
    return DoctorCheck(
        "Data Workbench rootfs",
        True,
        f"{rootfs}; image id {manifest.get('image_id', 'unrecorded')}",
    )


def _workbench_tools_check(config: HarnessExperimentConfig, temp_dir: Path | None = None) -> DoctorCheck:
    if shutil.which("bwrap") is None:
        return DoctorCheck("Data Workbench tools", False, "bubblewrap is unavailable")
    try:
        with tempfile.TemporaryDirectory(dir=temp_dir) as temporary:
            root = Path(temporary)
            for directory in ("context", "output", "home"):
                (root / directory).mkdir()
            python_check = (
                "import cv2, fitz, numpy, pandas, pdfplumber, PIL, pyarrow, "
                "pypdf, pytesseract, csv, json, sqlite3; "
                "assert list(csv.reader([\"a,b\"])) == [[\"a\",\"b\"]]; "
                "assert json.loads(\"{}\") == {}; "
                "assert sqlite3.connect(\":memory:\").execute(\"select 1\").fetchone() == (1,)"
            )
            python_check += (
                "; import importlib.metadata as md; from pathlib import Path; import subprocess; "
                "records=Path('/opt/dataspace/python-packages.txt').read_text().splitlines(); "
                "assert all(md.version(name)==version for name,version in (row.split('==') for row in records)); "
                "actual=subprocess.check_output(['dpkg-query','-W','-f=${Package}\\t${Version}\\n'],text=True); "
                "assert sorted(actual.splitlines()) == Path('/opt/dataspace/system-packages.txt').read_text().splitlines()"
            )
            shell_check = (
                "command -v bwrap >/dev/null && "
                "command -v ffmpeg >/dev/null && "
                "command -v ffprobe >/dev/null && "
                "command -v jq >/dev/null && "
                "command -v pdftotext >/dev/null && "
                "command -v pdfinfo >/dev/null && "
                "command -v pdftoppm >/dev/null && "
                "command -v rg >/dev/null && "
                "command -v socat >/dev/null && "
                "command -v sqlite3 >/dev/null && "
                "command -v tesseract >/dev/null && "
                "tesseract --list-langs 2>/dev/null | grep -qx eng && "
                "tesseract --list-langs 2>/dev/null | grep -qx chi_sim && "
                f"python3 -c {shlex.quote(python_check)}"
            )
            command = BubblewrapJail().command(
                task_context=root / "context",
                output_root=root / "output",
                home_root=root / "home",
                inner_command=("/bin/sh", "-c", shell_check),
                rootfs_root=config.workbench.rootfs_path,
            )
            check = _command_check("Data Workbench tools", list(command), environment=_sandbox_environment())
            if check.ok:
                return DoctorCheck(check.name, True, "all required tools are available")
            return check
    except Exception as exc:
        return DoctorCheck(
            "Data Workbench tools", False, f"{type(exc).__name__}: {exc}"
        )


def run_doctor(
    config: HarnessExperimentConfig, *, offline: bool = False, temp_dir: Path | None = None,
) -> list[DoctorCheck]:
    if temp_dir is None:
        candidates = [parent for parent in config.workbench.rootfs_path.parents
                      if (parent / "pyproject.toml").is_file()]
        if not candidates:
            raise ValueError("provide an explicit project-local temp_dir")
        temp_dir = candidates[0] / ".audit/tmp"
    temp_dir = temp_dir.resolve()
    temp_dir.mkdir(parents=True, exist_ok=True)
    checks: list[DoctorCheck] = []
    checks.append(DoctorCheck("platform", platform.system() == "Linux" and platform.machine() == "x86_64",
                              platform.system() + "/" + platform.machine()))
    checks.append(
        DoctorCheck(
            "inference credential",
            offline or bool(os.environ.get(config.model.api_key_env)),
            ("offline: credential check skipped; no authentication request" if offline else
             f"environment variable {config.model.api_key_env} "
             + ("is set (presence only; authentication untested)" if os.environ.get(config.model.api_key_env) else "is unset")),
            skipped=offline,
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
    runtime = config.workbench.rootfs_path.parent
    checks.append(_inventory_check("workbench content", runtime, runtime.parent/(runtime.name+"-files.json")))
    checks.append(_workbench_tools_check(config, temp_dir))
    checks.append(_mount_check(config, temp_dir))

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
            key, harness.executable, runtime_binds, config.workbench.rootfs_path, temp_dir=temp_dir,
        )
        if check.ok and check.detail != "codex-cli " + expected:
            check = DoctorCheck(check.name, False, f"{check.detail}; expected version {expected}")
        checks.append(check)
        if runtime_binds:
            mounted = runtime_binds[0][0]
            try:
                package = next(parent for parent in [mounted, *mounted.parents] if parent.name == "platform-package")
                checks.append(_inventory_check("Codex content", package, package.parent/"runtime-files.json"))
            except StopIteration:
                checks.append(DoctorCheck("Codex content", False, "runtime must be inside a verified platform-package"))
    return checks


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def inventory(root):
    """Never descend symlinks, including absolute container links."""
    root = Path(root).absolute()
    if root.is_symlink() or not root.is_dir():
        raise ValueError(f'inventory root must be a real directory: {root}')
    paths = [root]
    def walk_error(error):
        raise error
    for parent, directories, files in os.walk(root, followlinks=False, onerror=walk_error):
        directories.sort(); files.sort()
        paths.extend(Path(parent) / name for name in directories + files)
    paths.sort(key=lambda p: p.relative_to(root).as_posix())
    hardlinks = {}
    def entry(path):
        info = path.lstat()
        item = {'path': path.relative_to(root).as_posix(), 'mode': stat.S_IMODE(info.st_mode),
                'uid': info.st_uid, 'gid': info.st_gid}
        attrs = {name: os.getxattr(path, name, follow_symlinks=False).hex()
                 for name in sorted(os.listxattr(path, follow_symlinks=False))}
        item['xattrs'] = attrs
        if stat.S_ISLNK(info.st_mode):
            item.update(type='symlink', target=os.readlink(path))
        elif stat.S_ISDIR(info.st_mode):
            item['type'] = 'directory'
        elif stat.S_ISREG(info.st_mode):
            item.update(type='file', size=info.st_size, sha256=sha256(path))
            if info.st_nlink > 1:
                item['_inode'] = (info.st_dev, info.st_ino)
        else:
            raise ValueError(f'unsupported special file: {path}')
        return item
    with ThreadPoolExecutor(max_workers=4) as pool:
        entries = list(pool.map(entry, paths))
    for item in entries:
        inode = item.pop('_inode', None)
        if inode:
            item['hardlink_to'] = hardlinks.setdefault(inode, item['path'])
    return {'schema': 1, 'entries': entries}



def _inventory_check(name: str, root: Path, manifest: Path) -> DoctorCheck:
    try:
        expected = json.loads(manifest.read_text(encoding="utf-8"))
        actual = inventory(root)
        if actual != expected:
            wanted = {e["path"]: e for e in expected["entries"]}
            found = {e["path"]: e for e in actual["entries"]}
            changed = [p for p in sorted(wanted.keys() | found.keys()) if wanted.get(p) != found.get(p)]
            return DoctorCheck(name, False, f"{len(changed)} differing entries: {changed[:5]}")
        return DoctorCheck(name, True, f"{len(actual['entries'])} entries verified, including modes, links and xattrs")
    except Exception as exc:
        return DoctorCheck(name, False, f"{type(exc).__name__}: {exc}")


def _mount_check(config: HarnessExperimentConfig, temp_dir: Path) -> DoctorCheck:
    try:
        from .harnesses.codex import EXPORT_HELPER_PATH, EXPORT_HELPER_MOUNT
        harness = config.harnesses["codex"]
        binds = configured_runtime_binds(harness) + ((EXPORT_HELPER_PATH, EXPORT_HELPER_MOUNT),)
        with tempfile.TemporaryDirectory(dir=temp_dir, prefix="mount paths ") as temporary:
            root = Path(temporary)
            for directory in ("context", "output", "home"): (root/directory).mkdir()
            (root/"context/input.txt").write_text("read-only input")
            script = (
                "from pathlib import Path; import errno; "
                "p=Path('/mnt/workspace/input.txt'); assert p.read_text()=='read-only input'; "
                "assert Path('/mnt/runtime/dataspace-export.py').is_file(); "
                "assert Path('/mnt/runtime/codex/bin/codex').is_file(); "
                "Path('/mnt/output/probe.txt').write_text('writable output')\n"
                "try: p.write_text('unexpected')\n"
                "except OSError as e: assert e.errno in (errno.EROFS, errno.EACCES)\n"
                "else: raise AssertionError('context is writable')\n"
            )
            command = BubblewrapJail().command(task_context=root/"context", output_root=root/"output",
                home_root=root/"home", rootfs_root=config.workbench.rootfs_path, runtime_binds=binds,
                inner_command=("/usr/local/bin/python3", "-c", script))
            check = _command_check("sandbox mounts", list(command), environment=_sandbox_environment())
            if check.ok and (root/"output/probe.txt").read_text() == "writable output":
                return DoctorCheck(check.name, True, "read-only input/runtime/exporter and writable output verified")
            return check
    except Exception as exc:
        return DoctorCheck("sandbox mounts", False, f"{type(exc).__name__}: {exc}")



def _sandbox_environment() -> dict[str, str]:
    return {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8",
            "CODEX_HOME": "/mnt/home/.codex", "PIP_DISABLE_PIP_VERSION_CHECK": "1"}
