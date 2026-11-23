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

V8 calculation and pre-export review ({VERSION}):
Before filtering, joining, converting or counting, settle the applicable rules
from the question and relevant evidence. Reuse evidence already read.
- Units: Identify source and requested units separately from display precision.
  Convert only when the units differ. A surprising magnitude warrants a check,
  but is not enough to override a documented unit for the field. If no unit is
  documented, infer it only from relevant evidence and retain uncertainty.
- Scope: A lookup for codes, names or attributes does not define the population.
  Drop unmatched objects only when the requested eligibility or grouping
  requires the missing attribute. Apply an attachment's filter when the question
  or applicable evidence ties it to the requested result. A tab, example or
  export setting alone does not restrict a broader question.
- Counting: Use the requested entity, kind or record as the counting unit.
  Deduplicate entities or kinds by a supported identifier and retain eligible
  rows for record counts. Do not merge by name without evidence of identity or
  switch counting units merely to match a chart.

Pre-export review:
After preparing the candidate rows, make one comparison of the question and original evidence
against the query, transformations and rows you actually used. Check:
- source_table: Name the source and what one row represents. When several
  tables have similar labels, match the requested entity or event before choosing.
- scope: Check population, period, thresholds, grouping and counting unit as applicable.
- fields: Compare the actual field, definition, units, join keys and requested
  display form. Check the source dictionary or report when multiple names fit.
- order: Check direction and tie-breakers where relevant. A field that defines
  a batch or filters rows does not by itself define archive order.
- output_columns: Map the requested values and row identifiers to the final
  columns in order. Check if rows need a date or other identifier to be readable,
  and whether a field used only for filtering or sorting belongs in the answer.

Once existing evidence supports an answer, reuse the work already done and
finish the review. If an unresolved conflict could change the answer, check
only the evidence that distinguishes the alternatives in one focused pass.
If that check leaves uncertainty, record it and use the best-supported choice.
Reopen a settled choice only when new, relevant evidence could change the result.
Correct calculations that conflict with the supported rule.
Before exporting prediction.csv, write /mnt/output/{REVIEW_FILE} as UTF-8 JSON.
Use a top-level "checks" object keyed by the five names above and a top-level
"csv_columns" array of exact final header strings in order. Each check has
"expected", "actual", and "evidence" keys. Describe the evidence requirement in
expected and the operation or result you used in actual. Use the same concise
wording only if equivalent. Do not rewrite them to obtain aligned. Use null for expected if evidence
cannot settle the choice. Use null for both only if the check does not apply.
In evidence, give a brief reason and a locator such as a question clause, source
table or field, PDF page or video time. For a conflict that changes the result,
briefly state the chosen rule and why the alternative was not used in scope
or fields evidence. Run this local check before exporting:
  python3 {REVIEW_TOOL}
Inspect the result, make any evidence-backed correction, and update the note.
Then export prediction.csv and end the task. Keep review notes out of the CSV.
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
