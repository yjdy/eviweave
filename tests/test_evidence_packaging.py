"""Artificial 410-task evidence fixtures; never reads historical data or calls models."""

from __future__ import annotations
import copy
import csv
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "tools"))
import evidence_support as support
import evidence_verify as verifier
import package_evidence as package
from evidence_support import (
    digest,
    json_bytes,
    Source,
    EvidenceError,
    Redactor,
    TASKS,
    TRACE_ATTEMPTS,
    selected,
    expected_attempts,
)


def fixture(root):
    source = root / "source"
    source.mkdir()
    official = root / "official"
    official.mkdir()
    identity = {
        "prompt_contract": "relation-v6",
        "model": {
            "model": "qwen3.8-flash",
            "key": "qwen3.8-flash",
            "openai_base_url": "https://gateway.private.test/v1",
            "anthropic_base_url": "",
            "context_window": 983616,
            "max_output_tokens": 32768,
        },
        "execution_policy": {
            "simple_fixes": {"version": "simple-fixes-v1"},
            "auto_compact_token_limit": 942656,
        },
    }
    identity.update(
        wall_time_seconds=1800,
        harness={
            "options": {
                "version": "0.145.0",
                "wire_api": "responses",
                "simple_fixes": True,
            }
        },
    )
    fingerprint = digest(
        json.dumps(
            identity, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
    )
    scored = [
        {
            "task_id": f"task_{n}",
            "passed": n <= 45,
            "score": int(n <= 45),
            "error_code": None if n <= 45 else "synthetic_mismatch",
        }
        for n in range(1, 61)
    ]
    score = {
        "evaluator": "DataSpace Evaluator",
        "evaluator_version": "1.0.0",
        "config_set_sha256": support.CONFIG_SHA,
        "config_schema_version": "1.0",
        "metric": "task_accuracy",
        "task_count": 60,
        "passed_task_count": 45,
        "failed_task_count": 15,
        "task_accuracy": 0.75,
        "unscored_prediction_tasks": list(TASKS[60:]),
        "tasks": scored,
    }

    def write(rel, value):
        p = source / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(json_bytes(value) if not isinstance(value, bytes) else value)
        return p

    # A local scoring stub tests process isolation/protocol; real frozen scoring is a separate mandatory local acceptance.
    evaluator = (
        'import argparse,json\nfrom pathlib import Path\np=argparse.ArgumentParser()\nfor name in ["prediction-root","gold-root","config-root","output"]:p.add_argument("--"+name,required=True)\na=p.parse_args()\nPath(a.output).write_text('
        + repr(json.dumps(score, sort_keys=True))
        + '+"\\n")\n'
    ).encode()
    write("evaluation/evaluate.py", evaluator)
    for n in range(1, 61):
        write(
            f"evaluation/configs/task_{n}.json",
            {"task_id": f"task_{n}", "synthetic": True},
        )
        write(f"evaluation/output/task_{n}/gold.csv", b"value\nfixture\n")
    write("protocol.json", {"task_ids": list(TASKS)})
    for n in range(45):
        write(f"frozen_src/artificial_{n}.txt", b"handwritten synthetic source\n")
    fm = {
        p.relative_to(source).as_posix(): digest(p.read_bytes())
        for p in source.rglob("*")
        if p.is_file()
    }
    assert len(fm) == 167
    write("frozen_manifest.json", fm)
    manifest_sha = digest((source / "frozen_manifest.json").read_bytes())
    write(
        "retry_four_protocol.json",
        {
            "task_ids": [f"task_{n}" for n in [22, 23, 207, 273]],
            "experiment_fingerprint": fingerprint,
            "manifest_sha256": manifest_sha,
        },
    )
    write("run/experiment.json", {"fingerprint": fingerprint, "identity": identity})
    records = []
    attempts = []
    predictions = []
    selected_records = []
    for task, attempt in expected_attempts():
        failed = attempt == "run_1" and int(task[5:]) in [22, 23, 207, 273]
        status = (
            ("no_prediction" if task == "task_273" else "runtime_error")
            if failed
            else "submitted"
        )
        missing = failed and task != "task_273"
        usage = {} if missing else {"input_tokens": 1, "output_tokens": 2}
        error = (
            "transport failed at https://gateway.private.test/v1; request 01234567-89ab-cdef-0123-456789abcdef"
            if status == "runtime_error"
            else None
        )
        record = {
            "task_id": task,
            "harness": "codex",
            "model": "qwen3.8-flash",
            "status": status,
            "return_code": 1 if status == "runtime_error" else 0,
            "usage": usage,
            "wall_time_seconds": 1.25,
            "model_turns": 0 if failed else 1,
            "tool_actions": 1,
            "error": error,
            "prediction_path": (
                "/mnt/output/prediction.csv" if status == "submitted" else None
            ),
        }
        start = {
            "event": "run_started",
            "task_id": task,
            "harness": "codex",
            "model": "qwen3.8-flash",
            "experiment_fingerprint": fingerprint,
            "question": "Artificial task.",
            "invocation": {
                "metadata": {
                    "simple_fixes": identity["execution_policy"]["simple_fixes"],
                    "runtime_version": "0.145.0",
                },
                "command": ["synthetic-command", str(source / "private-home")],
                "environment_keys": ["ARTIFICIAL_API_KEY"],
            },
        }
        end = dict(
            event="run_finished",
            **{
                k: record.get(k)
                for k in [
                    "status",
                    "return_code",
                    "error",
                    "prediction_path",
                    "wall_time_seconds",
                ]
            },
        )
        events = [start, end]
        prefix = f"run/tasks/{task}/{attempt}"
        run = write(prefix + "/run.json", record)
        event_file = write(
            prefix + "/events.jsonl",
            "".join(json.dumps(e) + "\n" for e in events).encode(),
        )
        aggregate = dict(record, attempt=attempt)
        records.append(aggregate)
        attempts.append(
            {
                "task_id": task,
                "attempt": attempt,
                "status": status,
                "usage": usage or None,
                "run_sha256": digest(run.read_bytes()),
                "events_sha256": digest(event_file.read_bytes()),
            }
        )
        if attempt == selected(task):
            data = f"value\n{task}\n".encode()
            write(prefix + "/output/prediction.csv", data)
            write(f"run/predictions/{task}/prediction.csv", data)
            predictions.append(
                {
                    "task_id": task,
                    "attempt": attempt,
                    "sha256": digest(data),
                    "bytes": len(data),
                    "data_rows": 1,
                }
            )
            selected_records.append(aggregate)
        if (int(task[5:]), int(attempt[4:])) in TRACE_ATTEMPTS:
            stdout = [
                {
                    "type": "thread.started",
                    "thread_id": "fedcba98-7654-3210-fedc-ba9876543210",
                },
                {
                    "type": "item.started",
                    "item": {
                        "type": "command_execution",
                        "id": "item_1",
                        "command": "cat /mnt/workspace/example.csv",
                        "status": "in_progress",
                    },
                },
                {
                    "type": "item.completed",
                    "item": {
                        "type": "command_execution",
                        "id": "item_1",
                        "command": "cat /mnt/workspace/example.csv",
                        "status": "completed",
                        "aggregated_output": "000001，中文（标点）\n1.230000000000000000001",
                    },
                },
            ]
            stdout.append(
                {"type": "turn.failed", "error": {"message": error}}
                if failed
                else {"type": "turn.completed", "usage": usage}
            )
            write(
                prefix + "/stdout.log",
                "".join(
                    json.dumps(e, ensure_ascii=False) + "\n" for e in stdout
                ).encode(),
            )
            write(prefix + "/stderr.log", b"artificial stderr\n")
    write("results.json", {"all_attempts": records, "tasks": selected_records})
    write("run/batch_summary.json", {"tasks": selected_records})
    write("public60-eval.json", score)
    first = copy.deepcopy(score)
    first.update(passed_task_count=44, failed_task_count=16, task_accuracy=44 / 60)
    write("before_failed_four_retry/public60-eval.json", first)
    write(
        "before_failed_four_retry/results.json",
        {"public_task_count": 60, "public_passed": 44},
    )
    baseline = root / "baseline.json"
    baseline.write_bytes(
        json_bytes(
            {
                "status": "PASS",
                "manifest_sha256": manifest_sha,
                "experiment_fingerprint": fingerprint,
                "attempts": attempts,
                "predictions": predictions,
            }
        )
    )
    html = b"Artificial snapshot; no external source data."
    script = b"{id:`yjdy-codex-controlled-qwen38-flash`,method:`codex-controlled`,backbone:`Qwen3.8-Flash`,accuracy:81.22,correct:333,evaluated:`2026-10-21`,description:`relation-v6, simple-fixes-v1, 414 attempts`},{id:`synthetic-tie`,correct:333}"
    (official / "leaderboard.html").write_bytes(html)
    (official / "leaderboard.js").write_bytes(script)
    (official / "checks.json").write_bytes(
        json_bytes({"status": "PASS", "verified_date": "2026-10-22"})
    )
    return (
        source,
        baseline,
        official,
        manifest_sha,
        fingerprint,
        digest(evaluator),
        {"leaderboard.html": digest(html), "leaderboard.js": digest(script)},
    )


class EvidencePackagingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        (PROJECT / ".audit/p4/tmp").mkdir(parents=True, exist_ok=True)
        cls.temporary = tempfile.TemporaryDirectory(dir=PROJECT / ".audit/p4/tmp")
        cls.root = Path(cls.temporary.name)
        cls.source, cls.baseline, cls.official, msha, fp, esha, hashes = fixture(
            cls.root
        )
        cls.patches = []
        for module in [support, package, verifier]:
            for name, value in [
                ("MANIFEST_SHA", msha),
                ("FINGERPRINT", fp),
                ("EVALUATOR_SHA", esha),
                ("OFFICIAL_HASHES", hashes),
            ]:
                p = patch.object(module, name, value)
                p.start()
                cls.patches.append(p)
        cls.output = cls.root / "valid"
        cls.output.mkdir()
        cls.report = package.build(cls.source, cls.baseline, cls.official, cls.output)
        cls.files = verifier.read_archive(cls.output / support.ARCHIVE_NAME)

    @classmethod
    def tearDownClass(cls):
        for p in reversed(cls.patches):
            p.stop()
        cls.temporary.cleanup()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=self.root)
        self.addCleanup(self.tmp.cleanup)
        self.work = Path(self.tmp.name)

    def repack(self, files):
        files = {
            k: v for k, v in files.items() if k not in ["manifest.json", "SHA256SUMS"]
        }
        old = json.loads(self.files["manifest.json"])
        package.assemble_manifest(
            files,
            {
                name: item["source_sha256"]
                for name, item in old["files"].items()
                if "source_sha256" in item
            },
            old["traces"],
        )
        return files

    def reject(self, files, reason=None):
        with self.assertRaises(EvidenceError) as error:
            verifier.verify_payload(self.repack(files))
        if reason:
            self.assertIn(reason, str(error.exception))

    def test_complete_synthetic_build_and_independent_verify(self):
        report = verifier.verify_payload(self.files)
        self.assertEqual(
            (report["predictions"], report["attempts"], report["selected_traces"]),
            (410, 414, 6),
        )
        self.assertEqual(report["file_count"], 445)
        self.assertEqual(len(json.loads(self.files["manifest.json"])["files"]), 443)
        self.assertEqual(
            self.files["predictions/task_33/prediction.csv"], b"value\ntask_33\n"
        )
        self.assertEqual(
            (self.source / "run/predictions/task_33/prediction.csv").read_bytes(),
            b"value\ntask_33\n",
        )

    def test_archive_determinism_and_unpack_integrity(self):
        a = self.work / "a.zip"
        b = self.work / "b.zip"
        package.write_zip(self.files, a)
        package.write_zip(dict(reversed(list(self.files.items()))), b)
        self.assertEqual(a.read_bytes(), b.read_bytes())
        self.assertEqual(verifier.read_archive(a), self.files)

    def test_prediction_missing_extra_or_changed(self):
        for mode in ["missing", "extra", "changed"]:
            with self.subTest(mode=mode):
                files = dict(self.files)
                if mode == "missing":
                    del files["predictions/task_1/prediction.csv"]
                elif mode == "extra":
                    files["predictions/task_411/prediction.csv"] = b"value\nextra\n"
                else:
                    files["predictions/task_1/prediction.csv"] = b"value\nchanged\n"
                self.reject(files)

    def test_attempt_duplicate_missing_identity_and_status(self):
        original = [json.loads(x) for x in self.files["attempts.jsonl"].splitlines()]
        for mode in ["duplicate", "missing", "selected", "status", "exit"]:
            with self.subTest(mode=mode):
                rows = copy.deepcopy(original)
                if mode == "duplicate":
                    rows.append(rows[0])
                elif mode == "missing":
                    rows.pop()
                elif mode == "selected":
                    rows[0]["selected"] = False
                elif mode == "status":
                    rows[0]["status"] = "runtime_error"
                else:
                    rows[0]["return_code"] = 1
                files = dict(self.files)
                files["attempts.jsonl"] = "".join(
                    json.dumps(x) + "\n" for x in rows
                ).encode()
                self.reject(files)
        files = dict(self.files)
        x = json.loads(files["experiment.json"])
        x["fingerprint"] = "wrong"
        files["experiment.json"] = json_bytes(x)
        self.reject(files)

    def test_missing_usage_not_zero_or_counted_as_complete(self):
        rows = [json.loads(x) for x in self.files["attempts.jsonl"].splitlines()]
        target = next(
            x for x in rows if x["task_id"] == "task_23" and x["attempt"] == "run_1"
        )
        target["usage"] = {"input_tokens": 0}
        target["usage_present"] = True
        files = dict(self.files)
        files["attempts.jsonl"] = "".join(json.dumps(x) + "\n" for x in rows).encode()
        self.reject(files, "missing usage")
        files = dict(self.files)
        x = json.loads(files["experiment.json"])
        x["observed_usage_all_attempts"]["input_tokens"] += 1
        files["experiment.json"] = json_bytes(x)
        self.reject(files, "usage aggregate")

    def test_selection_policy_and_prediction_hash(self):
        files = dict(self.files)
        files["final-selection.csv"] = files["final-selection.csv"].replace(
            b"task_22,run_2", b"task_22,run_1"
        )
        self.reject(files, "selection policy")

    def test_trace_events_usage_and_status_are_preserved(self):
        for mode in ["event", "usage", "status"]:
            with self.subTest(mode=mode):
                files = dict(self.files)
                prefix = "selected-traces/task_33/run_1/"
                if mode == "event":
                    files[prefix + "events.jsonl"] = (
                        b"\n".join(files[prefix + "events.jsonl"].splitlines()[:-1])
                        + b"\n"
                    )
                elif mode == "usage":
                    x = [
                        json.loads(line)
                        for line in files[prefix + "stdout.log"].splitlines()
                    ]
                    x[-1]["usage"]["input_tokens"] += 1
                    files[prefix + "stdout.log"] = "".join(
                        json.dumps(v) + "\n" for v in x
                    ).encode()
                else:
                    x = json.loads(files[prefix + "run.json"])
                    x["status"] = "runtime_error"
                    files[prefix + "run.json"] = json_bytes(x)
                self.reject(files)

    def test_score_denominator_version_and_historical_label(self):
        for key, value in [
            ("task_count", 410),
            ("passed_task_count", 46),
            ("config_set_sha256", "bad"),
        ]:
            files = dict(self.files)
            x = json.loads(files["evaluation/public60.json"])
            x[key] = value
            files["evaluation/public60.json"] = json_bytes(x)
            self.reject(files)
        files = dict(self.files)
        x = json.loads(files["evaluation/historical-first-pass.json"])
        x["kind"] = "recomputed"
        files["evaluation/historical-first-pass.json"] = json_bytes(x)
        self.reject(files)

    def test_redaction_nested_escaped_stderr_and_identifiers(self):
        secret = "synthetic-secret-value-67890"
        uuid = "01234567-89ab-cdef-0123-456789abcdef"
        r = Redactor(
            self.source,
            {"model": {"openai_base_url": "https://gateway.private.test/v1"}},
        )
        raw = {
            "nested": [{"api_key": secret, "request_id": uuid}],
            "text": r"{\"api_key\":\"" + secret + r"\"}",
            "stderr": "Authorization: Bearer "
            + secret
            + " /home/artificial/private "
            + uuid,
            "command": "API_KEY=" + secret + " python -c example",
            "url": "https://gateway.private.test/v1/responses",
        }
        clean = r.value(raw)
        text = json.dumps(clean)
        self.assertNotIn(secret, text)
        self.assertNotIn(uuid, text)
        self.assertNotIn("/home/artificial", text)
        self.assertEqual(clean["nested"][0]["request_id"], r.identifier(uuid))
        verifier.scan(clean, r.known)

    def test_business_values_public_urls_and_sandbox_paths_survive(self):
        original = {
            "text": "000001，中文（标点）。\n1.230000000000000000001",
            "url": "https://example.org/report",
            "paths": ["/mnt/workspace/data.csv", "/mnt/output/prediction.csv"],
            "input_tokens": 123,
            "max_output_tokens": 32768,
        }
        self.assertEqual(Redactor(self.source, {}).value(original), original)

    def test_independent_residual_scanner_rejects_leaks_in_all_text(self):
        for text in [
            "Authorization: Bearer synthetic-token-value",
            "/home/artificial/workspace/data",
            "sk-syntheticcredential123",
            "01234567-89ab-cdef-0123-456789abcdef",
            "API_KEY=synthetic-secret-value",
        ]:
            with self.subTest(text=text), self.assertRaises(EvidenceError):
                verifier.scan({"nested": [{"text": text}]})
        files = dict(self.files)
        files["selected-traces/task_33/run_1/stderr.log"] = (
            b"Authorization: Bearer synthetic-token-value\n"
        )
        self.reject(files)
        # Even immutable predictions are scanned rather than silently exempted.
        with self.assertRaises(EvidenceError):
            verifier.scan("/home/artificial/private\n")

    def test_malformed_json_and_jsonl_fail(self):
        for raw in [b"{", b'{"x":1,"x":2}', b'{"x":NaN}']:
            with self.assertRaises(EvidenceError):
                support.loads(raw)
        with self.assertRaises(EvidenceError):
            support.json_lines(b"{}\nnot-json\n")
        files = dict(self.files)
        files["selected-traces/task_23/run_1/stdout.log"] = b"bad\n"
        self.reject(files)

    def test_output_escape_links_nonempty_and_source_overlap(self):
        for path in [PROJECT, PROJECT.parent, self.source / "new", self.work.parent]:
            with self.assertRaises(EvidenceError):
                support.output_path(path, [self.source])
        link = self.work / "link"
        link.symlink_to(PROJECT.parent, target_is_directory=True)
        with self.assertRaises(EvidenceError):
            support.output_path(link / "new")

    def test_source_changes_and_links_rejected(self):
        source = self.work / "source"
        source.mkdir()
        f = source / "file"
        f.write_text("original")
        tracker = Source(source)
        tracker.read("file")
        f.write_text("modified")
        with self.assertRaises(EvidenceError):
            tracker.unchanged()
        (source / "link").symlink_to(f)
        with self.assertRaises(EvidenceError):
            Source(source).read("link")

    def test_frozen_material_corruption_stops_before_release(self):
        for rel in [
            "evaluation/evaluate.py",
            "evaluation/configs/task_1.json",
            "evaluation/output/task_1/gold.csv",
            "run/tasks/task_1/run_1/run.json",
            "run/predictions/task_1/prediction.csv",
        ]:
            with self.subTest(rel=rel):
                p = self.source / rel
                old = p.read_bytes()
                p.write_bytes(old + b"changed")
                try:
                    target = self.work / digest(rel.encode())[:8]
                    target.mkdir()
                    with self.assertRaises(EvidenceError):
                        package.build(self.source, self.baseline, self.official, target)
                    self.assertFalse((target / support.ARCHIVE_NAME).exists())
                finally:
                    p.write_bytes(old)

    def test_scoring_timeout_and_inconsistent_result_stop_publication(self):
        source = Source(self.source)
        with patch.object(
            package.subprocess,
            "run",
            side_effect=subprocess.TimeoutExpired(["artificial"], 300),
        ):
            with self.assertRaisesRegex(EvidenceError, "timed out"):
                package.score(source, self.output / "payload/predictions", self.work)
        first = self.source / "public60-eval.json"
        old = first.read_bytes()
        x = json.loads(old)
        x["passed_task_count"] = 46
        first.write_bytes(json_bytes(x))
        try:
            target = self.work / "wrong-score"
            target.mkdir()
            with self.assertRaisesRegex(EvidenceError, "differs"):
                package.score(
                    Source(self.source), self.output / "payload/predictions", target
                )
        finally:
            first.write_bytes(old)

    def test_archive_traversal_duplicates_links_and_damage(self):
        for mode in ["traversal", "absolute", "duplicate", "link", "extra"]:
            with self.subTest(mode=mode):
                path = self.work / (mode + ".zip")
                with zipfile.ZipFile(path, "w") as archive:
                    for n, (name, data) in enumerate(self.files.items()):
                        if n == 0 and mode in ["traversal", "absolute", "extra"]:
                            name = {
                                "traversal": "../outside",
                                "absolute": "/outside",
                                "extra": "extra.txt",
                            }[mode]
                        info = zipfile.ZipInfo(name)
                        info.create_system = 3
                        info.external_attr = (
                            0o120777 if n == 0 and mode == "link" else 0o100644
                        ) << 16
                        archive.writestr(info, data)
                    if mode == "duplicate":
                        archive.writestr("manifest.json", self.files["manifest.json"])
                with self.assertRaises(EvidenceError):
                    verifier.read_archive(path)
        path = self.work / "broken.zip"
        path.write_bytes(b"not a ZIP")
        with self.assertRaises(zipfile.BadZipFile):
            verifier.read_archive(path)
        files = dict(self.files)
        files["README.md"] += b"changed"
        with self.assertRaises(EvidenceError):
            verifier.verify_payload(files)

    def test_official_snapshot_cannot_be_replaced(self):
        p = self.official / "leaderboard.js"
        old = p.read_bytes()
        p.write_bytes(old + b"changed")
        try:
            with self.assertRaises(EvidenceError):
                package.official_record(self.official)
        finally:
            p.write_bytes(old)

    def test_quoted_credentials_and_basic_authorization(self):
        r = Redactor(self.source, {})
        for text in [
            'password="two secret words"',
            r"{\"password\":\"two secret words\"}",
            "Authorization: Basic YXJ0aWZpY2lhbDpwYXNzd29yZA==",
        ]:
            with self.subTest(text=text):
                clean = r.text(text, "synthetic")
                self.assertNotIn("two secret words", clean)
                self.assertNotIn("YXJ0aWZpY2lhbDpwYXNzd29yZA==", clean)
                verifier.scan(clean)
                with self.assertRaises(EvidenceError):
                    verifier.scan(text)

    def test_text_request_identifiers_keep_cross_field_links(self):
        r = Redactor(self.source, {})
        clean = r.value(
            {
                "request_id": "synthetic-request-constant",
                "text": "x-request-id: synthetic-request-constant",
            }
        )
        self.assertIn(clean["request_id"], clean["text"])
        self.assertNotIn("synthetic-request-constant", json.dumps(clean))
        verifier.scan(clean)
        with self.assertRaises(EvidenceError):
            verifier.scan("request_id=synthetic-request-constant")

    def test_sensitive_dictionary_keys_and_unsupported_fields(self):
        r = Redactor(self.source, {})
        cleaned = r.value({"/home/artificial/private": "value"})
        self.assertNotIn("/home/artificial", json.dumps(cleaned))
        verifier.scan(cleaned)
        with self.assertRaises(EvidenceError):
            verifier.scan({"/home/artificial/private": "value"})
        with self.assertRaises(EvidenceError):
            r.value({"authorization": {"value": "artificial secret"}})

    def test_cli_exit_codes_private_reports_and_no_release_on_failure(self):
        bad = self.work / "bad.zip"
        bad.write_bytes(b"not a ZIP")
        with patch("sys.stdout", new_callable=io.StringIO):
            self.assertEqual(
                package.main(
                    [
                        "verify",
                        "--archive",
                        str(bad),
                        "--output-root",
                        str(self.work / "failed"),
                    ]
                ),
                1,
            )
            self.assertEqual(
                package.main(
                    [
                        "verify",
                        "--archive",
                        str(self.work / "missing"),
                        "--output-root",
                        str(self.work / "missing-output"),
                    ]
                ),
                2,
            )
        self.assertFalse((self.work / "failed" / support.ARCHIVE_NAME).exists())
        self.assertEqual(
            json.loads((self.work / "failed/report.json").read_text())["status"], "FAIL"
        )
        with (
            patch("sys.stderr", new_callable=io.StringIO),
            self.assertRaises(SystemExit) as error,
        ):
            package.main(
                [
                    "verify",
                    "--archive",
                    str(bad),
                    "--output-root",
                    str(PROJECT.parent / "outside"),
                ]
            )
        self.assertEqual(error.exception.code, 2)

    def test_source_extra_task_and_baseline_record_duplicates_rejected(self):
        extra = self.source / "run/tasks/task_411"
        extra.mkdir()
        try:
            with self.assertRaisesRegex(EvidenceError, "directories"):
                package.source_records(
                    Source(self.source), json.loads(self.baseline.read_text())
                )
        finally:
            extra.rmdir()
        baseline = json.loads(self.baseline.read_text())
        baseline["attempts"].append(baseline["attempts"][0])
        with self.assertRaisesRegex(EvidenceError, "duplicate"):
            package.source_records(Source(self.source), baseline)
