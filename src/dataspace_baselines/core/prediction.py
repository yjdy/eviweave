from __future__ import annotations

import csv
import hashlib
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any


NESTED_PREDICTION_RECOVERY_POLICY_VERSION = "unique-exact-name-v1"


def recover_nested_prediction(output_root: Path) -> dict[str, Any]:
    """Publish one unambiguous nested prediction, without changing its bytes."""
    root = output_root.resolve(strict=True)
    target = root / "prediction.csv"
    if target.exists() or target.is_symlink():
        raise ValueError("canonical prediction path already exists")
    candidates = []

    def fail_walk(error: OSError) -> None:
        raise error

    for directory, directories, filenames in os.walk(
        root, followlinks=False, onerror=fail_walk
    ):
        parent = Path(directory)
        directories[:] = [
            name for name in directories if not (parent / name).is_symlink()
        ]
        if "prediction.csv" not in filenames:
            continue
        path = parent / "prediction.csv"
        if (
            not path.is_symlink()
            and path.is_file()
            and path.resolve(strict=True).is_relative_to(root)
        ):
            candidates.append(path)
    if not candidates:
        raise ValueError("no regular nested prediction.csv candidate found")
    if len(candidates) != 1:
        names = ", ".join(
            sorted(str(path.relative_to(root)) for path in candidates)
        )
        raise ValueError(
            f"multiple prediction.csv candidates; no selection made: {names}"
        )
    source = candidates[0]
    original = validate_prediction(source)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=".recovered-prediction-",
            suffix=".tmp",
            dir=root,
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            with source.open("rb") as incoming:
                shutil.copyfileobj(incoming, handle)
            handle.flush()
            os.fsync(handle.fileno())
        copied = validate_prediction(temporary)
        if copied["sha256"] != original["sha256"]:
            raise ValueError("candidate changed while it was being copied")
        # Linking a completed temporary copy publishes atomically without
        # overwriting a canonical file that might have appeared meanwhile.
        os.link(temporary, target)
        return {
            "policy": NESTED_PREDICTION_RECOVERY_POLICY_VERSION,
            "source_relative_path": str(source.relative_to(root)),
            "source_sha256": original["sha256"],
            "target_sha256": copied["sha256"],
        }
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def validate_prediction(path: Path) -> dict[str, Any]:
    """Validate only the released CSV contract, never answer correctness."""

    if not path.is_file():
        raise ValueError("submitted path is not a file")
    byte_size = path.stat().st_size
    digest = hashlib.sha256()
    with path.open("rb") as raw:
        for chunk in iter(lambda: raw.read(1024 * 1024), b""):
            digest.update(chunk)

    csv.field_size_limit(min(sys.maxsize, 128 * 1024 * 1024))
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle)
        try:
            header = next(reader)
        except StopIteration as exc:
            raise ValueError("prediction CSV is empty") from exc
        width = len(header)
        if width == 0:
            raise ValueError("prediction CSV has no columns")
        row_count = 0
        for row_number, row in enumerate(reader, start=2):
            if len(row) != width:
                raise ValueError(
                    f"row {row_number} has {len(row)} columns; expected {width}"
                )
            row_count += 1
    return {
        "columns": width,
        "data_rows": row_count,
        "bytes": byte_size,
        "sha256": digest.hexdigest(),
    }
