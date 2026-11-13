"""Record V8's evidence comparison and check its declared CSV headers."""

import csv
import json
from pathlib import Path


VERSION = "v8-semantic-review-v1"
REVIEW_FILE = "semantic-review.json"
REPORT_FILE = "semantic-review-result.json"
REVIEW_TOOL = "/mnt/runtime/eviweave-semantic-review.py"
DIMENSIONS = ("source_table", "scope", "fields", "order", "output_columns")
MAX_REVIEW_BYTES = 32 * 1024

INSTRUCTIONS = f"""

V8 pre-export semantic review ({VERSION}):
After preparing the candidate rows, make one comparison of the question and original evidence
against the query, transformations and rows you actually used. Check:
- source_table: Name the source and what one row represents. When several
  tables have similar labels, match the requested entity or event before choosing.
- scope: Check population, period, thresholds, grouping and counting unit as
  applicable. Apply an attachment's example or UI filter only when the question
  or referenced evidence makes it part of the requested scope.
- fields: Compare the actual field, definition, units, join keys and requested
  display form. Check the source dictionary or report when multiple names fit.
- order: Check direction and tie-breakers where relevant. A field that defines
  a batch or filters rows does not by itself define archive order.
- output_columns: Map the requested values and row identifiers to the final
  columns in order. Check if rows need a date or other identifier to be readable,
  and whether a field used only for filtering or sorting belongs in the answer.

Correct a discrepancy when the evidence supports the change. If a choice is
ambiguous, record the evidence and actual choice, and keep the best supported
answer. Do this in the current session using the relevant evidence already read.
Before exporting prediction.csv, write /mnt/output/{REVIEW_FILE} as UTF-8 JSON.
Use a top-level "checks" object keyed by the five names above and a top-level
"csv_columns" array of exact final header strings in order. Each check has
"expected", "actual", and "evidence" keys. Use the same concise description in
expected and actual if they are equivalent. Use null for expected if evidence
cannot settle the choice. Use null for both only if the check does not apply.
In evidence, give a brief reason and a locator such as a question clause, source
table or field, PDF page or video time. Run this local check before exporting:
  python3 {REVIEW_TOOL}
Inspect its result. Resolve any evidence-backed conflict and update the note to
reflect the final decision. Export normally and keep review notes out of the CSV.
"""


def _nonempty_text(value):
    return isinstance(value, str) and bool(value.strip())


def compare_review(review, csv_header, *, before_export=False):
    """Compare recorded interpretations without inferring a business answer."""
    if not isinstance(review, dict):
        raise ValueError("expected a JSON object")
    checks = review.get("checks")
    if not isinstance(checks, dict) or set(checks) != set(DIMENSIONS):
        raise ValueError("expected all five semantic checks")
    columns = review.get("csv_columns")
    if not isinstance(columns, list) or not columns or not all(map(_nonempty_text, columns)):
        raise ValueError("expected an ordered list of nonempty CSV headers")

    results = {}
    for name in DIMENSIONS:
        check = checks[name]
        if not isinstance(check, dict) or set(check) != {"expected", "actual", "evidence"}:
            raise ValueError(f"invalid {name} check")
        expected, actual, evidence = (check[key] for key in ("expected", "actual", "evidence"))
        if (not _nonempty_text(evidence)
                or (actual is None and expected is not None)
                or any(value is not None and not _nonempty_text(value) for value in (expected, actual))):
            raise ValueError(f"invalid {name} values")
        if actual is None:
            status = "not_applicable"
        elif expected is None:
            status = "uncertain"
        else:
            status = "aligned" if expected.strip() == actual.strip() else "conflict"
        results[name] = {**check, "status": status}

    if before_export:
        header_status = "pending_export"
    elif csv_header is None:
        header_status = "unavailable"
    else:
        header_status = "aligned" if csv_header == columns else "conflict"
    statuses = {item["status"] for item in results.values()} | {header_status}
    if "conflict" in statuses:
        overall = "conflict"
    elif statuses & {"uncertain", "unavailable"}:
        overall = "needs_review"
    else:
        overall = "aligned"
    return {"version": VERSION, "status": overall, "checks": results,
            "csv_header": {"status": header_status, "expected": columns, "actual": csv_header}}


def review_files(review_path, prediction_path=None):
    if review_path.is_symlink():
        return {"version": VERSION, "status": "invalid", "reason": "symbolic link"}
    if not review_path.exists():
        return {"version": VERSION, "status": "missing"}
    try:
        if not review_path.is_file():
            raise ValueError("review must be a regular file")
        with review_path.open("rb") as handle:
            data = handle.read(MAX_REVIEW_BYTES + 1)
        if len(data) > MAX_REVIEW_BYTES:
            raise ValueError("review exceeds size limit")
        review = json.loads(data.decode("utf-8"))
        header = None
        if prediction_path is not None and not prediction_path.is_symlink() and prediction_path.is_file():
            try:
                with prediction_path.open(encoding="utf-8-sig", newline="") as handle:
                    header = next(csv.reader(handle), None)
            except (OSError, UnicodeError, csv.Error):
                pass
        return compare_review(review, header, before_export=prediction_path is None)
    except (OSError, ValueError, RecursionError) as exc:
        return {"version": VERSION, "status": "invalid", "reason": str(exc)}


def record_review(run_root):
    """Audit the canonical prediction after the frozen runner's recovery step."""
    output = run_root / "output"
    result = review_files(output / REVIEW_FILE, output / "prediction.csv")
    (run_root / REPORT_FILE).write_text(
        json.dumps(result, indent=2, ensure_ascii=True) + "\n", encoding="utf-8"
    )


def main():
    result = review_files(Path("/mnt/output") / REVIEW_FILE)
    print(json.dumps(result, indent=2, ensure_ascii=True))
    return 2 if result["status"] in {"missing", "invalid"} else 0


if __name__ == "__main__":
    raise SystemExit(main())
