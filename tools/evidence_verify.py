"""Independent, standard-library checks for a published EviWeave evidence archive."""

from __future__ import annotations
import csv
import io
import json
import math
from pathlib import PurePosixPath
import re
import stat
import zipfile
from collections import Counter

from evidence_support import (
    EvidenceError,
    require,
    digest,
    loads,
    json_lines,
    csv_rows,
    TASKS,
    TRACE_ATTEMPTS,
    MISSING_USAGE,
    expected_attempts,
    selected,
    FINGERPRINT,
    MANIFEST_SHA,
    CONFIG_SHA,
    EVALUATOR_SHA,
    OFFICIAL_HASHES,
    POLICY,
    indexed,
    json_bytes,
)

FIXED = {
    "attempts.jsonl",
    "final-selection.csv",
    "experiment.json",
    "evaluation/public60.json",
    "evaluation/historical-first-pass.json",
    "official/entry.json",
    "redaction-report.json",
    "README.md",
    "README.zh-CN.md",
    "manifest.json",
    "SHA256SUMS",
}


def allowed_names():
    return (
        FIXED
        | {f"predictions/{t}/prediction.csv" for t in TASKS}
        | {
            f"selected-traces/task_{n}/run_{k}/{f}"
            for n, k in TRACE_ATTEMPTS
            for f in ["run.json", "events.jsonl", "stdout.log", "stderr.log"]
        }
    )


# Intentionally separate from substitution rules: scan final strings and field semantics.
def scan(value, forbidden=()):
    if isinstance(value, dict):
        for key, item in value.items():
            scan(key, forbidden)
            normalized = re.sub("[^a-z0-9]", "", key.lower())
            if normalized in {
                "apikey",
                "token",
                "password",
                "passwd",
                "secret",
                "clientsecret",
                "authorization",
                "accesstoken",
                "refreshtoken",
                "bearertoken",
            }:
                require(item in (None, "", "<REDACTED>"), "unredacted sensitive field")
            if (
                normalized
                in {
                    "requestid",
                    "responseid",
                    "threadid",
                    "sessionid",
                    "traceid",
                    "spanid",
                    "correlationid",
                }
                and item is not None
            ):
                require(
                    isinstance(item, str) and re.fullmatch(r"<ID_[0-9]{4,}>", item),
                    "unredacted identifier field",
                )
            scan(item, forbidden)
    elif isinstance(value, list):
        for item in value:
            scan(item, forbidden)
    elif isinstance(value, str):
        require(
            not re.search(r"/(?:home/[^/\s]+|root)(?:/|\b)", value),
            "host home path remains",
        )
        require(
            not re.search(
                r"\b[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}\b", value
            ),
            "UUID remains",
        )
        require(not re.search(r"\bsk-[\w-]{8,}\b", value), "credential token remains")
        for match in re.finditer(r"(?i)\bBearer\s+([^\s\x22\x27\\,;}\]]+)", value):
            require(match[1] == "<REDACTED>", "bearer credential remains")
        for match in re.finditer(
            r"""(?ix)\b(?:[a-z0-9_]*(?:api_key|access_token|refresh_token|client_secret)|api-?key|password|passwd|authorization|token|secret)[\\"']*\s*[:=]\s*[\\"']*([^\s\x22\x27\\,;}\]]+)""",
            value,
        ):
            require(
                match[1] in ("<REDACTED>", "Bearer", "Basic"),
                "sensitive assignment remains",
            )
        for match in re.finditer(
            r"""(?ix)\bAuthorization[\\"']*\s*[:=]\s*[\\"']*(?:(?:Bearer|Basic)\s+)?([^\s\x22\x27\\,;}\]]+)""",
            value,
        ):
            require(match[1] == "<REDACTED>", "authorization value remains")
        for match in re.finditer(
            r"""(?ix)\b(?:x[-_]request[-_]id|request[-_]id|session[-_]id|thread[-_]id|response[-_]id|trace[-_]id|span[-_]id)[\\"']*\s*[:=]\s*[\\"']*([^\s\x22\x27\\,;}\]]+)""",
            value,
        ):
            require(
                re.fullmatch(r"<ID_[0-9]{4,}>", match[1]), "identifier text remains"
            )
        require(
            not re.search(
                r"(?i)https?://(?:localhost|127\.0\.0\.1|[^/\s]+\.local)(?:[:/]|\b)",
                value,
            ),
            "local service endpoint remains",
        )
        for literal in forbidden:
            if literal in {"<REDACTED>"}:
                continue
            if len(literal) >= 8:
                require(literal not in value, "declared private value remains")
            elif literal:
                require(value != literal, "short private scalar remains")


def verify_payload(files, forbidden=()):
    require(set(files) == allowed_names(), "archive file whitelist mismatch")
    manifest = loads(files["manifest.json"])
    require(manifest["schema"] == 1, "unsupported evidence schema")
    require(
        manifest["experiment_fingerprint"] == FINGERPRINT
        and manifest["frozen_manifest_sha256"] == MANIFEST_SHA,
        "manifest experiment identity mismatch",
    )
    require(
        manifest["selection_policy"] == POLICY, "manifest selection policy mismatch"
    )
    payload = set(files) - {"manifest.json", "SHA256SUMS"}
    entries = manifest["files"]
    require(set(entries) == payload, "manifest payload mismatch")
    for name in payload:
        require(
            entries[name]["sha256"] == digest(files[name])
            and entries[name]["bytes"] == len(files[name]),
            "manifest digest mismatch: " + name,
        )
    expected = "".join(
        f"{digest(files[name])}  {name}\n"
        for name in sorted(set(files) - {"SHA256SUMS"})
    ).encode()
    require(files["SHA256SUMS"] == expected, "checksum file mismatch")
    attempts = json_lines(files["attempts.jsonl"])
    table = indexed(attempts, ("task_id", "attempt"))
    require(list(table) == expected_attempts(), "attempt set/order mismatch")
    require(
        Counter(a["status"] for a in attempts)
        == {"submitted": 410, "runtime_error": 3, "no_prediction": 1},
        "attempt status counts mismatch",
    )
    missing = set()
    for key, record in table.items():
        require(
            type(record["selected"]) is bool
            and record["selected"] == (key[1] == selected(key[0])),
            "attempt selected flag mismatch",
        )
        failed = key[1] == "run_1" and int(key[0][5:]) in {22, 23, 207, 273}
        expected_status = (
            ("no_prediction" if key[0] == "task_273" else "runtime_error")
            if failed
            else "submitted"
        )
        require(
            record["status"] == expected_status,
            "historical attempt status policy mismatch",
        )
        require(
            type(record["usage_present"]) is bool,
            "usage presence must be explicit boolean",
        )
        if record["status"] == "submitted":
            require(record["return_code"] == 0, "submitted attempt exit code mismatch")
        for field in ["source_run_sha256", "source_events_sha256"]:
            require(
                isinstance(record[field], str)
                and re.fullmatch(r"[0-9a-f]{64}", record[field]),
                "source hash field missing or invalid",
            )
        if not record["usage_present"]:
            require(record["usage"] is None, "missing usage must remain null")
            missing.add(key)
        else:
            require(
                isinstance(record["usage"], dict) and bool(record["usage"]),
                "recorded usage is missing",
            )
        require(
            type(record["wall_time_seconds"]) in (int, float)
            and math.isfinite(record["wall_time_seconds"])
            and record["wall_time_seconds"] >= 0,
            "invalid duration",
        )
    require(missing == MISSING_USAGE, "missing usage set mismatch")
    usage = {}
    for record in attempts:
        for key, value in (record["usage"] or {}).items():
            require(type(value) is int and value >= 0, "invalid usage value")
            usage[key] = usage.get(key, 0) + value
    experiment = loads(files["experiment.json"])
    require(experiment["fingerprint"] == FINGERPRINT, "experiment fingerprint mismatch")
    require(
        experiment["observed_usage_all_attempts"] == usage
        and experiment["usage_scope"] == "recorded-only; three attempts missing usage",
        "usage aggregate mismatch",
    )
    require(
        experiment["identity"]["prompt_contract"] == "relation-v6"
        and experiment["identity"]["model"]["model"] == "qwen3.8-flash",
        "experiment policy mismatch",
    )
    reader = csv.DictReader(io.StringIO(files["final-selection.csv"].decode()))
    require(
        reader.fieldnames == ["task_id", "attempt", "policy", "sha256"],
        "selection CSV columns mismatch",
    )
    identity = experiment["identity"]
    require(
        identity["model"]["context_window"] == 983616
        and identity["model"]["max_output_tokens"] == 32768
        and identity["execution_policy"]["auto_compact_token_limit"] == 942656,
        "historical budget mismatch",
    )
    require(
        identity["execution_policy"]["simple_fixes"]["version"] == "simple-fixes-v1"
        and identity["wall_time_seconds"] == 1800,
        "historical execution policy mismatch",
    )
    options = identity["harness"]["options"]
    require(
        options["version"] == "0.145.0"
        and options["wire_api"] == "responses"
        and options["simple_fixes"] is True,
        "historical harness policy mismatch",
    )
    selection = list(reader)
    require(
        [r["task_id"] for r in selection] == list(TASKS), "selection set/order mismatch"
    )
    for record in selection:
        task = record["task_id"]
        attempt = selected(task)
        require(
            record["attempt"] == attempt and record["policy"] == POLICY,
            "selection policy mismatch",
        )
        data = files[f"predictions/{task}/prediction.csv"]
        csv_rows(data)
        require(
            record["sha256"]
            == digest(data)
            == table[(task, attempt)]["prediction_sha256"],
            "prediction digest mismatch",
        )
        require(
            table[(task, attempt)]["status"] == "submitted",
            "selected attempt not submitted",
        )
    for task in TASKS:
        name = f"predictions/{task}/prediction.csv"
        require(
            entries[name].get("source_sha256") == digest(files[name]),
            "prediction source SHA mismatch",
        )
    score = loads(files["evaluation/public60.json"])
    require(
        score["task_count"] == 60
        and score["passed_task_count"] == 45
        and score["failed_task_count"] == 15
        and score["task_accuracy"] == 0.75,
        "offline score mismatch",
    )
    require(
        score["evaluator_version"] == "1.0.0"
        and score["config_set_sha256"] == CONFIG_SHA,
        "scoring version mismatch",
    )
    scored = [x["task_id"] for x in score["tasks"]]
    require(
        len(scored) == len(set(scored)) == 60 and set(scored).issubset(TASKS),
        "scored task set mismatch",
    )
    require(
        all(
            type(x["passed"]) is bool and x["score"] == int(x["passed"])
            for x in score["tasks"]
        ),
        "invalid per-task score fields",
    )
    require(
        sum(x["passed"] is True for x in score["tasks"]) == 45,
        "per-task score count mismatch",
    )
    require(
        set(score["unscored_prediction_tasks"]) == set(TASKS) - set(scored)
        and len(score["unscored_prediction_tasks"]) == 350,
        "unscored task set mismatch",
    )
    initial = loads(files["evaluation/historical-first-pass.json"])
    require(
        initial["task_count"] == 60
        and initial["passed_task_count"] == 44
        and initial["kind"] == "historical-record-not-recomputed",
        "historical score mismatch",
    )
    require(
        manifest["scoring"]["evaluator_sha256"] == EVALUATOR_SHA
        and manifest["scoring"]["config_set_sha256"] == CONFIG_SHA,
        "manifest scoring identity mismatch",
    )
    official = loads(files["official/entry.json"])
    require(
        official["entry_id"] == "yjdy-codex-controlled-qwen38-flash"
        and official["correct"] == 333
        and official["total"] == 410
        and official["task_accuracy_percent"] == 81.22
        and official["rank"] == 1
        and official["tied"] is True,
        "official record mismatch",
    )
    require(
        official["verified_date"] == "2026-10-22"
        and official["evaluation_date"] == "2026-10-21"
        and official["snapshot_sha256"] == OFFICIAL_HASHES,
        "official snapshot identity mismatch",
    )
    for n, k in TRACE_ATTEMPTS:
        root = f"selected-traces/task_{n}/run_{k}"
        run = loads(files[root + "/run.json"])
        events = json_lines(files[root + "/events.jsonl"])
        stdout = json_lines(files[root + "/stdout.log"])
        require(
            run["task_id"] == f"task_{n}"
            and run["status"] == table[(f"task_{n}", f"run_{k}")]["status"],
            "trace run status mismatch",
        )
        starts = [e for e in events if e.get("event") == "run_started"]
        ends = [e for e in events if e.get("event") == "run_finished"]
        require(len(starts) == len(ends) == 1, "trace lifecycle mismatch")
        require(
            starts[0]["task_id"] == f"task_{n}"
            and starts[0]["experiment_fingerprint"] == FINGERPRINT,
            "trace identity mismatch",
        )
        for key in [
            "status",
            "return_code",
            "error",
            "prediction_path",
            "wall_time_seconds",
        ]:
            require(run.get(key) == ends[0].get(key), "trace completion mismatch")
        require(
            (run.get("usage") or None) == table[(f"task_{n}", f"run_{k}")]["usage"],
            "trace usage mismatch",
        )
        trace_usage = {}
        for event in stdout:
            for candidate in [
                event.get("usage"),
                (
                    event.get("result", {}).get("usage")
                    if isinstance(event.get("result"), dict)
                    else None
                ),
                (
                    event.get("message", {}).get("usage")
                    if isinstance(event.get("message"), dict)
                    else None
                ),
            ]:
                if isinstance(candidate, dict):
                    for key, value in candidate.items():
                        if type(value) is int:
                            trace_usage[key] = trace_usage.get(key, 0) + value
        require(
            trace_usage == (run.get("usage") or {}),
            "stdout usage differs from attempt record",
        )

        evidence = manifest["traces"][f"task_{n}/run_{k}"]
        require(
            [x.get("event") for x in events] == evidence["events"]
            and [x.get("type") for x in stdout] == evidence["stdout_types"],
            "trace event count/order mismatch",
        )
        terminal = [
            x for x in stdout if x.get("type") in ["turn.completed", "turn.failed"]
        ]
        require(bool(terminal), "trace terminal event missing")
    for name, data in files.items():
        if name.startswith("predictions/"):
            scan(
                data.decode("utf-8-sig"), forbidden
            )  # Scan immutable predictions; never rewrite them.
            continue
        if name.endswith(".json"):
            scan(loads(data), forbidden)
        elif name.endswith(".jsonl") or name.endswith("/stdout.log"):
            scan(json_lines(data), forbidden)
        else:
            scan(data.decode("utf-8"), forbidden)
    return {
        "status": "PASS",
        "file_count": len(files),
        "predictions": 410,
        "attempts": 414,
        "selected_traces": 6,
        "missing_usage": 3,
        "public_score": "45/60",
        "historical_score": "44/60",
        "model_calls": 0,
    }


def read_archive(path):
    files = {}
    with zipfile.ZipFile(path) as archive:
        require(
            len(archive.infolist()) == len(allowed_names()),
            "unexpected archive member count",
        )
        require(
            sum(x.file_size for x in archive.infolist()) <= 128 * 1024 * 1024,
            "archive exceeds evidence size limit",
        )
        for item in archive.infolist():
            name = item.filename
            p = PurePosixPath(name)
            require(
                item.orig_filename == name and "\x00" not in item.orig_filename,
                "NUL archive path rejected",
            )
            require(
                not p.is_absolute()
                and ".." not in p.parts
                and "\\" not in name
                and ":" not in name
                and "\x00" not in name,
                "unsafe archive path",
            )
            require(
                name in allowed_names() and name not in files and not item.is_dir(),
                "unexpected or duplicate archive path",
            )
            require(
                stat.S_IFMT(item.external_attr >> 16) in [0, stat.S_IFREG],
                "archive links/special files rejected",
            )
            require(item.file_size <= 32 * 1024 * 1024, "archive member too large")
            try:
                files[name] = archive.read(item)
            except (zipfile.BadZipFile, RuntimeError, OSError) as exc:
                raise EvidenceError("corrupt archive member") from exc
    return files
