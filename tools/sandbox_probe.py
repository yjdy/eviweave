"""Artificial local probes for the unchanged outer jail; no network connections."""

import errno
import json
import os
from pathlib import Path
import subprocess

settings = json.loads(Path("/mnt/workspace/probe.json").read_text())
checks = {}
assert Path("/mnt/workspace/data.txt").read_text() == settings["marker"]
checks["context_read"] = True
for name in ["context", "rootfs", "runtime", "exporter"]:
    path = {
        "context": "/mnt/workspace/data.txt",
        "rootfs": "/opt/dataspace/TOOLS.md",
        "runtime": "/mnt/runtime/codex/bin/codex",
        "exporter": "/mnt/runtime/dataspace-export.py",
    }[name]
    try:
        fd = os.open(path, os.O_WRONLY)
    except OSError as exc:
        assert exc.errno in (errno.EROFS, errno.EACCES, errno.EPERM), (path, exc)
        checks[name + "_readonly"] = True
    else:
        os.close(fd)
        raise AssertionError("write-open unexpectedly allowed: " + path)
for path in ["/mnt/output/writable", "/mnt/home/writable"]:
    Path(path).write_text(settings["marker"])
    checks[path] = True
for path in settings["blocked"]:
    try:
        Path(path).read_bytes()
    except (FileNotFoundError, PermissionError, IsADirectoryError):
        checks["blocked:" + path] = True
    else:
        raise AssertionError("private sentinel readable: " + path)
assert not Path("/proc/" + str(settings["host_pid"])).exists()
checks["host_pid_hidden"] = True
assert os.readlink("/proc/self/ns/pid") != settings["host_pidns"]
checks["pid_namespace_isolated"] = True
assert os.readlink("/proc/self/ns/net") == settings["host_netns"]
checks["outer_network_namespace_preserved"] = True
fd = os.open("/dev/tty", os.O_RDWR)
os.close(fd)
checks["tty_openable"] = True
payload = {"columns": ["task", "value"], "rows": [[settings["marker"], "000001"]]}
Path("/mnt/output/final_table.json").write_text(json.dumps(payload))
subprocess.run(
    [
        "/usr/local/bin/python3",
        "/mnt/runtime/dataspace-export.py",
        "--input",
        "/mnt/output/final_table.json",
    ],
    check=True,
    timeout=10,
)
Path("/mnt/output/probe-report.json").write_text(
    json.dumps(
        {
            "status": "PASS",
            "checks": checks,
            "inner_bwrap_version": subprocess.check_output(
                ["/usr/bin/bwrap", "--version"], text=True
            ).strip(),
            "pid": os.getpid(),
        },
        indent=2,
    )
    + "\n"
)
