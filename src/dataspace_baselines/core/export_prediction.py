"""Standalone, standard-library CSV exporter for the task-local sandbox."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import sys
import tempfile
from decimal import Decimal
from pathlib import Path
from typing import Any


EXPORTER_VERSION = "lossless-csv-v1"
OUTPUT_PATH = Path("/mnt/output/prediction.csv")


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON number is not supported: {value}")


def _cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, Decimal) and value.is_finite():
        return str(value)
    raise ValueError("cells must be strings, finite JSON numbers, booleans or null")


def export_prediction(
    input_path: Path, output_path: Path = OUTPUT_PATH
) -> dict[str, Any]:
    """Validate and preserve supplied values; no answer or source inference."""
    with input_path.open("r", encoding="utf-8") as handle:
        payload = json.load(
            handle, parse_float=Decimal, parse_constant=_reject_constant
        )
    if not isinstance(payload, dict):
        raise ValueError("input must be an object containing columns and rows")
    columns, rows = payload.get("columns"), payload.get("rows")
    if (
        not isinstance(columns, list)
        or not columns
        or not all(isinstance(column, str) for column in columns)
    ):
        raise ValueError("columns must be a nonempty list of strings")
    if not isinstance(rows, list):
        raise ValueError("rows must be a list")
    serialized = []
    for number, row in enumerate(rows, start=1):
        if not isinstance(row, list) or len(row) != len(columns):
            raise ValueError(f"data row {number} must have {len(columns)} cells")
        serialized.append([_cell(value) for value in row])

    # Finish validation before creating/replacing any output file.
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="",
            prefix=".prediction-",
            suffix=".tmp",
            dir=output_path.parent,
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            writer = csv.writer(handle, lineterminator="\n")
            writer.writerow(columns)
            writer.writerows(serialized)
            handle.flush()
            os.fsync(handle.fileno())
        csv.field_size_limit(min(sys.maxsize, 128 * 1024 * 1024))
        with temporary.open("r", encoding="utf-8", newline="") as handle:
            actual = list(csv.reader(handle))
        if actual != [columns, *serialized]:
            raise ValueError("CSV read-back did not preserve the supplied cells")
        metadata = {
            "exporter_version": EXPORTER_VERSION,
            "columns": len(columns),
            "data_rows": len(serialized),
            "bytes": temporary.stat().st_size,
            "sha256": hashlib.sha256(temporary.read_bytes()).hexdigest(),
        }
        os.replace(temporary, output_path)
        return metadata
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    args = parser.parse_args()
    try:
        metadata = export_prediction(args.input)
    except (ValueError, OSError, csv.Error) as exc:
        print(f"export failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(metadata, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
