#!/usr/bin/env python3
"""Run one EviWeave task with V8 PDF reading and pre-export review."""
import argparse
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import sys

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "src"))
sys.path.insert(0, str(PROJECT / "tools"))

from dataspace_baselines.config import load_harness_experiment_config
from dataspace_baselines.core.batch import result_payload
from dataspace_baselines.core.task import TaskSpec
from dataspace_baselines.harnesses import build_harness
from dataspace_baselines.runners.filesystem_jail import JAIL_RUNTIME, JAIL_WORKSPACE
import v8_semantic_review as semantic_review

READER_PATH = PROJECT / "tools/pdf_reader.py"
REVIEW_PATH = Path(semantic_review.__file__).resolve()
READER_MOUNT = f"{JAIL_RUNTIME}/eviweave-pdf-reader.py"
IMPORTED_POPPLER_DATA = PROJECT / ".local/v8-poppler-data"
POPPLER_DATA_PATH = "/usr/share/poppler"
REQUIRED_CMAP = "cMap/Adobe-GB1/GB-EUC-H"
PDF_INSTRUCTIONS = f"""

V8 PDF reader (required for PDF extraction):
- Use the mounted V8 PDF reader for PDF text, coordinates, tables, and OCR.
  Do not invoke PDF parsers or OCR utilities directly, or write ad hoc PDF
  extraction scripts. Use shell or Python to work with the reader's JSON.
- Set PDF to a relevant original PDF under {JAIL_WORKSPACE}. Search by keyword,
  field name, or record label, then read only the relevant physical pages:
  python3 {READER_MOUNT} search "$PDF" 'keyword or record label'
  python3 {READER_MOUNT} parse "$PDF" --pages 4,9-10
  When the relevant page is already known, read that page directly.
- Use --coordinates for placement and --table-spec for a located table.
  Check page_issues, truncation, and OCR provenance. Use the reader's targeted
  --ocr-pages option when a relevant page needs an OCR check. If evidence is
  still unreadable, record the gap in the execution notes.
- For an exact excerpt used as evidence, verify it against the original PDF
  using the SHA-256, 1-based page, start, end, and exact match returned by search:
  python3 {READER_MOUNT} verify "$PDF" --sha256 "$SHA" --page "$PAGE" --start "$START" --end "$END" --quote="$QUOTE"
  Keep the original wording and physical page references in the evidence notes.
"""


def has_pdf(task):
    return any(path.suffix.casefold() == ".pdf" and path.is_file()
               and path.resolve().is_relative_to(task.context_dir)
               for path in task.context_dir.rglob("*"))


def poppler_mounts(workbench_share, imported_data):
    if (workbench_share / "poppler" / REQUIRED_CMAP).is_file():
        return []
    if not (imported_data / REQUIRED_CMAP).is_file():
        raise FileNotFoundError(f"V8 requires local Poppler mapping data at {imported_data}")
    # An older rootfs has no /usr/share/poppler mountpoint. Preserve each
    # existing child at its original path in a read-only reconstructed view.
    mounts = ["--tmpfs", "/usr/share"]
    for entry in sorted(workbench_share.iterdir()):
        if entry.name == "poppler":
            continue
        target = f"/usr/share/{entry.name}"
        if entry.is_symlink():
            mounts.extend(("--symlink", os.readlink(entry), target))
        else:
            mounts.extend(("--ro-bind", str(entry), target))
    mounts.extend(("--ro-bind", str(imported_data), POPPLER_DATA_PATH, "--remount-ro", "/usr/share"))
    return mounts


def extend_invocation(invocation, review_identity, *, pdf_identity=None, resource_mounts=()):
    command = list(invocation.command)
    boundary = command.index("--")
    inner = command[boundary + 1:]
    if len(inner) < 3 or inner[1] != "exec":
        raise ValueError("Unexpected Codex invocation shape")
    mounts = command[:boundary]
    metadata = {**invocation.metadata, "semantic_review": review_identity}
    # The frozen workbench builder adds bind options before the inner command.
    targets = [PurePosixPath(semantic_review.REVIEW_TOOL)]
    if pdf_identity is not None:
        targets.append(PurePosixPath(READER_MOUNT))
    for i, arg in enumerate(mounts):
        if arg in ("--ro-bind", "--bind", "--ro-bind-try", "--bind-try"):
            target = PurePosixPath(command[i + 2])
            for mount in targets:
                if (target == mount or mount in target.parents
                        or (target != PurePosixPath("/") and target in mount.parents)):
                    raise ValueError(f"The configured runtime bind overlaps {mount}")
    if not any(a == "--dir" and b == JAIL_RUNTIME for a, b in zip(mounts, mounts[1:])):
        mounts += ["--dir", JAIL_RUNTIME]
    mounts += ["--ro-bind", str(REVIEW_PATH), semantic_review.REVIEW_TOOL]
    if pdf_identity is not None:
        mounts += ["--ro-bind", str(READER_PATH), READER_MOUNT, *resource_mounts]
        metadata["pdf_reader"] = pdf_identity
        inner[-1] += PDF_INSTRUCTIONS
    inner[-1] += semantic_review.INSTRUCTIONS
    return replace(invocation, command=tuple([*mounts, "--", *inner]), metadata=metadata)


def build_v8_harness(config):
    if config.model.api_key_env != "EVIWEAVE_API_KEY":
        raise ValueError("V8 requires the EVIWEAVE_API_KEY credential variable")
    runner = build_harness("codex", config)
    original = runner.invocation_factory
    original_cleanup = runner.cleanup
    identity = {"sha256": hashlib.sha256(READER_PATH.read_bytes()).hexdigest(),
                "prompt_sha256": hashlib.sha256(PDF_INSTRUCTIONS.encode()).hexdigest(),
                "mount": READER_MOUNT}
    review_identity = {"version": semantic_review.VERSION,
                       "sha256": hashlib.sha256(REVIEW_PATH.read_bytes()).hexdigest(),
                       "prompt_sha256": hashlib.sha256(semantic_review.INSTRUCTIONS.encode()).hexdigest()}

    def invocation(task, run_root, output_root):
        base = original(task, run_root, output_root)
        if hashlib.sha256(REVIEW_PATH.read_bytes()).hexdigest() != review_identity["sha256"]:
            raise ValueError("V8 semantic review changed after the harness was built")
        if not has_pdf(task):
            return extend_invocation(base, review_identity)
        if hashlib.sha256(READER_PATH.read_bytes()).hexdigest() != identity["sha256"]:
            raise ValueError("V8 PDF reader changed after the harness was built")
        share = config.workbench.rootfs_path.resolve() / "usr/share"
        resources = poppler_mounts(share, IMPORTED_POPPLER_DATA)
        return extend_invocation(base, review_identity, pdf_identity=identity, resource_mounts=resources)

    def cleanup(run_root):
        try:
            semantic_review.record_review(run_root)
        finally:
            if original_cleanup is not None:
                original_cleanup(run_root)

    runner.invocation_factory = invocation
    runner.cleanup = cleanup
    runner.harness_key = "codex-v8"
    runner.resume_identity.update(runner="codex-v8", pdf_reader=identity, semantic_review=review_identity)
    harness = getattr(config, 'harnesses', {}).get('codex')
    if harness is not None and str(harness.options.get('version')) == '0.170.0':
        from codex_upgrade import attach
        return attach(runner, config)
    return runner


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--task-dir", required=True, type=Path)
    parser.add_argument("--run-dir", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        if args.run_dir.exists() and any(args.run_dir.iterdir()):
            raise ValueError(f"Run directory already exists and is not empty: {args.run_dir}")
        config = load_harness_experiment_config(args.config)
        task = TaskSpec.load(args.task_dir)
        result = build_v8_harness(config).run(task, args.run_dir)
        print(json.dumps(result_payload(result), ensure_ascii=False, indent=2))
        return 0 if result.status == "submitted" else 1
    except (OSError, ValueError, KeyError, RuntimeError) as exc:
        print(f"run-v8: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
