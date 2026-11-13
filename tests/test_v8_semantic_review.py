import json
import tempfile
import unittest

from tools import run_v8


review = run_v8.semantic_review


def example():
    return {
        "checks": {
            name: {"expected": "same", "actual": "same", "evidence": "source, row 1"}
            for name in review.DIMENSIONS
        },
        "csv_columns": ["编号", "数值"],
    }


class SemanticReviewTests(unittest.TestCase):
    def test_separates_conflicts_ambiguity_and_nonapplicable_checks(self):
        note = example()
        note["checks"]["source_table"]["actual"] = "different table"
        note["checks"]["fields"]["expected"] = None
        note["checks"]["order"].update(expected=None, actual=None)
        result = review.compare_review(note, ["编号", "数值"])
        self.assertEqual(result["status"], "conflict")
        self.assertEqual({name: item["status"] for name, item in result["checks"].items()}, {
            "source_table": "conflict", "scope": "aligned", "fields": "uncertain",
            "order": "not_applicable", "output_columns": "aligned",
        })
        self.assertEqual(result["csv_header"]["status"], "aligned")

    def test_csv_header_comparison_preserves_order(self):
        self.assertEqual(review.compare_review(example(), ["编号", "数值"])["status"], "aligned")
        self.assertEqual(review.compare_review(example(), ["数值", "编号"])["status"], "conflict")
        self.assertEqual(review.compare_review(example(), None)["status"], "needs_review")
        result = review.compare_review(example(), None, before_export=True)
        self.assertEqual(result["status"], "aligned")
        self.assertEqual(result["csv_header"]["status"], "pending_export")

    def test_missing_and_invalid_notes_are_reported_without_editing_csv(self):
        audit = run_v8.PROJECT / ".audit"
        audit.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=audit) as directory:
            root = audit / directory
            output = root / "output"
            output.mkdir()
            prediction = output / "prediction.csv"
            prediction.write_text("编号,数值\n0001,12\n", encoding="utf-8-sig")
            original = prediction.read_bytes()
            note = output / review.REVIEW_FILE
            for data, expected in ((None, "missing"), (b"\xff", "invalid"),
                                   (b"{}", "invalid"), (json.dumps(example()).encode(), "aligned")):
                if data is not None:
                    note.write_bytes(data)
                review.record_review(root)
                result = json.loads((root / review.REPORT_FILE).read_text())
                self.assertEqual(result["status"], expected)
                self.assertEqual(prediction.read_bytes(), original)
