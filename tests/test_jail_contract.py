"""Mount-contract adaptations of historical filesystem-jail tests; execution is in integration."""

import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from dataspace_baselines.runners.filesystem_jail import BubblewrapJail

PROJECT = Path(__file__).resolve().parents[1]


class JailContractTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=os.environ["TMPDIR"])
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.context = self.root / "context"
        self.context.mkdir()
        self.rootfs = self.root / "rootfs"
        (self.rootfs / "opt/dataspace").mkdir(parents=True)
        (self.rootfs / "opt/dataspace/TOOLS.md").write_text("artificial")
        self.runtime = self.root / "runtime"
        self.runtime.mkdir()

    def command(self, **kwargs):
        args = dict(
            task_context=self.context,
            output_root=self.root / "output",
            home_root=self.root / "home",
            rootfs_root=self.rootfs,
            inner_command=("/bin/true",),
            runtime_binds=((self.runtime, "/mnt/runtime/codex"),),
        )
        args.update(kwargs)
        with patch("shutil.which", return_value="/usr/bin/bwrap"):
            return BubblewrapJail().command(**args)

    def test_historical_root_mounts_and_namespaces(self):
        command = self.command()
        triples = [command[n : n + 3] for n in range(len(command) - 2)]
        for triple in [
            ("--ro-bind", str(self.rootfs), "/"),
            ("--ro-bind", str(self.context), "/mnt/workspace"),
            ("--ro-bind", str(self.runtime), "/mnt/runtime/codex"),
            ("--bind", str(self.root / "output"), "/mnt/output"),
            ("--bind", str(self.root / "home"), "/mnt/home"),
            ("--dev-bind", "/dev/null", "/dev/tty"),
        ]:
            self.assertIn(triple, triples)
        for flag in [
            "--unshare-pid",
            "--unshare-ipc",
            "--unshare-uts",
            "--die-with-parent",
            "--new-session",
        ]:
            self.assertIn(flag, command)
        self.assertNotIn("--unshare-net", command)
        self.assertIn("PYTHONDONTWRITEBYTECODE", command)

    def test_runtime_mount_rejects_escape_or_root(self):
        for target in [
            "/mnt/runtime",
            "/mnt/runtime/../escape",
            "relative",
            "/outside",
        ]:
            with (
                self.subTest(target=target),
                self.assertRaisesRegex(ValueError, "runtime mount"),
            ):
                self.command(runtime_binds=((self.runtime, target),))

    def test_missing_runtime_and_rootfs_fail(self):
        with self.assertRaises(FileNotFoundError):
            self.command(runtime_binds=((self.root / "missing", "/mnt/runtime/codex"),))
        with self.assertRaises(FileNotFoundError):
            self.command(rootfs_root=self.root / "missing")
