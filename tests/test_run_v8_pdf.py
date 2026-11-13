import hashlib
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from tools import run_v8
from dataspace_baselines.runners.native_cli import NativeCliInvocation


class RunV8PdfTests(unittest.TestCase):
    def setUp(self):
        audit = run_v8.PROJECT / ".audit"
        audit.mkdir(exist_ok=True)
        directory = tempfile.TemporaryDirectory(dir=audit)
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.share = self.root / "workbench/usr/share"
        self.share.mkdir(parents=True)
        self.context = self.root / "context"
        self.context.mkdir()
        self.config = SimpleNamespace(model=SimpleNamespace(api_key_env="EVIWEAVE_API_KEY"),
                                      workbench=SimpleNamespace(rootfs_path=self.root / "workbench"))
        self.base = NativeCliInvocation(("bwrap", "--ro-bind", str(self.root/"workbench"), "/",
                                         "--chdir", "/mnt/output", "--", "codex", "exec", "V6 prompt"),
                                        self.root, metadata={"frozen": True})

    def wrapped(self):
        runner = SimpleNamespace(invocation_factory=Mock(return_value=self.base),
                                 resume_identity={}, cleanup=None)
        with patch.object(run_v8, "build_harness", return_value=runner):
            return run_v8.build_v8_harness(self.config)

    def test_pdf_task_has_mounted_reader_and_v8_instructions(self):
        cmap = self.share / "poppler" / run_v8.REQUIRED_CMAP
        cmap.parent.mkdir(parents=True)
        cmap.touch()
        (self.context / "attachment.PDF").touch()
        invocation = self.wrapped().invocation_factory(SimpleNamespace(context_dir=self.context), self.root, self.root)
        i = invocation.command.index(run_v8.READER_MOUNT)
        self.assertEqual(invocation.command[i-2:i], ("--ro-bind", str(run_v8.READER_PATH)))
        prompt = invocation.command[-1]
        self.assertEqual(prompt, "V6 prompt" + run_v8.PDF_INSTRUCTIONS + run_v8.semantic_review.INSTRUCTIONS)
        self.assertIn(run_v8.semantic_review.REVIEW_TOOL, invocation.command[:-1])
        self.assertIn("V8 PDF reader (required for PDF extraction)", prompt)
        self.assertIn("Do not invoke PDF parsers or OCR utilities directly", prompt)
        self.assertIn("record the gap in the execution notes", prompt)
        for action in ("search", "parse", "verify"):
            self.assertIn(f"{run_v8.READER_MOUNT} {action}", prompt)
        identity = invocation.metadata["pdf_reader"]
        self.assertIn("sha256", identity)
        self.assertEqual(identity["prompt_sha256"], hashlib.sha256(run_v8.PDF_INSTRUCTIONS.encode()).hexdigest())
        self.assertTrue(invocation.metadata["frozen"])
        self.assertEqual(self.base.command[-1], "V6 prompt")

    def test_non_pdf_task_gets_review_without_pdf_mount(self):
        (self.context / "attachment.txt").touch()
        runner = self.wrapped()
        invocation = runner.invocation_factory(SimpleNamespace(context_dir=self.context), self.root, self.root)
        boundary = invocation.command.index("--")
        base_boundary = self.base.command.index("--")
        self.assertEqual(invocation.command[:boundary], (*self.base.command[:base_boundary],
            "--dir", run_v8.JAIL_RUNTIME, "--ro-bind", str(run_v8.REVIEW_PATH),
            run_v8.semantic_review.REVIEW_TOOL))
        self.assertNotIn(run_v8.READER_MOUNT, invocation.command)
        self.assertEqual(invocation.command[-1], "V6 prompt" + run_v8.semantic_review.INSTRUCTIONS)
        self.assertEqual(invocation.metadata["semantic_review"], runner.resume_identity["semantic_review"])
        self.assertNotIn("pdf_reader", invocation.metadata)

    def test_poppler_data_overlay_preserves_read_only_workbench_resources(self):
        fonts = self.share / "fonts"
        fonts.mkdir()
        imported = self.root / "data"
        cmap = imported / run_v8.REQUIRED_CMAP
        cmap.parent.mkdir(parents=True)
        cmap.touch()
        self.assertEqual(run_v8.poppler_mounts(self.share, imported),
                         ["--tmpfs", "/usr/share", "--ro-bind", str(fonts), "/usr/share/fonts",
                          "--ro-bind", str(imported), "/usr/share/poppler", "--remount-ro", "/usr/share"])
        with self.assertRaises(FileNotFoundError):
            run_v8.poppler_mounts(self.share, self.root / "missing")


if __name__ == "__main__":
    unittest.main()
