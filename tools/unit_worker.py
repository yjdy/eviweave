"""Record every unittest result and preserve failure logs."""

import argparse
import json
from pathlib import Path
import sys
import unittest

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "tools"))


class RecordingResult(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.cases = []

    def addSuccess(self, test):
        super().addSuccess(test)
        self.cases.append({"case": test.id(), "status": "PASS"})

    def addFailure(self, test, err):
        super().addFailure(test, err)
        self.cases.append(
            {
                "case": test.id(),
                "status": "FAIL",
                "reason": self._exc_info_to_string(err, test),
            }
        )

    def addError(self, test, err):
        super().addError(test, err)
        self.cases.append(
            {
                "case": test.id(),
                "status": "FAIL",
                "reason": self._exc_info_to_string(err, test),
            }
        )

    def addSkip(self, test, reason):
        super().addSkip(test, reason)
        self.cases.append({"case": test.id(), "status": "SKIP", "reason": reason})


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    suite = unittest.defaultTestLoader.discover(
        str(PROJECT / "tests"), pattern="test_*.py"
    )
    result = unittest.TextTestRunner(verbosity=2, resultclass=RecordingResult).run(
        suite
    )
    args.output.write_text(
        json.dumps(
            {
                "status": "PASS" if result.wasSuccessful() else "FAIL",
                "tests": result.testsRun,
                "cases": result.cases,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    )
    raise SystemExit(0 if result.wasSuccessful() and not result.skipped else 1)
