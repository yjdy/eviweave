#!/usr/bin/env python3
"""Build or independently verify historical V6 evidence; never run tasks or models.

MIT; author 袁珺 / Jun Yuan. Historical DataSpace attribution is retained in LICENSE.
"""

from __future__ import annotations
import argparse
from collections import Counter
import csv
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import zipfile

from evidence_support import (
    PROJECT,
    EvidenceError,
    MissingMaterial,
    Source,
    Redactor,
    require,
    digest,
    loads,
    json_lines,
    json_bytes,
    csv_rows,
    output_path,
    TASKS,
    RETRIES,
    TRACE_ATTEMPTS,
    MISSING_USAGE,
    MANIFEST_SHA,
    FINGERPRINT,
    EVALUATOR_SHA,
    CONFIG_SHA,
    OFFICIAL_HASHES,
    ARCHIVE_NAME,
    POLICY,
    BASE_KEYS,
    expected_attempts,
    selected,
    indexed,
)
from evidence_verify import verify_payload, read_archive, scan


def official_record(root):
    source = Source(root)
    checks = source.json("checks.json")
    require(
        checks.get("status") == "PASS" and checks.get("verified_date") == "2026-10-22",
        "official evidence was not verified at the historical date",
    )
    for name, expected in OFFICIAL_HASHES.items():
        require(
            digest(source.read(name)) == expected, "official snapshot digest mismatch"
        )
    script = source.read("leaderboard.js").decode()
    entry_id = "yjdy-codex-controlled-qwen38-flash"
    start = script.index("{id:`" + entry_id + "`")
    end = script.index("},{id:", start)
    text = script[start:end]
    for token in [
        "method:`codex-controlled`",
        "backbone:`Qwen3.8-Flash`",
        "accuracy:81.22",
        "correct:333",
        "evaluated:`2026-10-21`",
        "relation-v6",
        "simple-fixes-v1",
        "414 attempts",
    ]:
        require(token in text, "official snapshot entry mismatch")
    scores = [int(n) for n in re.findall(r"\bcorrect:(\d+)", script)]
    require(
        max(scores) == 333 and scores.count(333) >= 2,
        "historical tied-first evidence mismatch",
    )
    source.unchanged()
    return {
        "entry_id": entry_id,
        "registered_method": "codex-controlled + Qwen3.8-Flash",
        "correspondence": "historical relation-v6 + simple-fixes-v1; excludes later Controlled implementation",
        "correct": 333,
        "total": 410,
        "task_accuracy_percent": 81.22,
        "rank": 1,
        "tied": True,
        "verified_date": "2026-10-22",
        "evaluation_date": "2026-10-21",
        "source": "https://dataspace-bench.github.io/#leaderboard",
        "snapshot_sha256": OFFICIAL_HASHES,
        "claim_scope": "historical snapshot; not a current ranking or local 410-task reevaluation",
    }, source


def source_records(source, baseline):
    fm = source.json("frozen_manifest.json")
    require(
        digest(source.read("frozen_manifest.json")) == MANIFEST_SHA,
        "frozen manifest digest mismatch",
    )
    require(
        baseline.get("status") == "PASS"
        and baseline.get("manifest_sha256") == MANIFEST_SHA
        and baseline.get("experiment_fingerprint") == FINGERPRINT,
        "P0 baseline identity mismatch",
    )
    # Recheck the bounded non-input set; do not reread the benchmark input corpus.
    non_inputs = [x for x in fm if not x.startswith("input/")]
    require(len(non_inputs) == 167, "non-input frozen set mismatch")
    for rel in sorted(non_inputs):
        require(
            digest(source.read(rel)) == fm[rel],
            "frozen material digest mismatch: " + rel,
        )
    experiment = source.json("run/experiment.json")
    identity = experiment["identity"]
    computed = digest(
        json.dumps(
            identity, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
    )
    require(
        computed == experiment.get("fingerprint") == FINGERPRINT,
        "experiment fingerprint mismatch",
    )
    require(
        identity["prompt_contract"] == "relation-v6"
        and identity["execution_policy"]["simple_fixes"]["version"]
        == "simple-fixes-v1",
        "frozen execution policy mismatch",
    )
    protocol = source.json("protocol.json")
    retry = source.json("retry_four_protocol.json")
    require(
        set(protocol["task_ids"]) == set(TASKS) and len(protocol["task_ids"]) == 410,
        "protocol tasks mismatch",
    )
    require(
        set(retry["task_ids"]) == {f"task_{n}" for n in RETRIES}
        and retry["experiment_fingerprint"] == FINGERPRINT
        and retry["manifest_sha256"] == MANIFEST_SHA,
        "retry protocol mismatch",
    )
    require(
        {p.name for p in (source.root / "run/tasks").iterdir()} == set(TASKS),
        "source task directories mismatch",
    )
    require(
        {p.name for p in (source.root / "run/predictions").iterdir()} == set(TASKS),
        "prediction directory set mismatch",
    )
    result = source.json("results.json")
    batch = source.json("run/batch_summary.json")
    all_index = indexed(result["all_attempts"], ("task_id", "attempt"))
    final_index = indexed(result["tasks"], ("task_id", "attempt"))
    batch_index = indexed(batch["tasks"], ("task_id", "attempt"))
    base_attempts = indexed(baseline["attempts"], ("task_id", "attempt"))
    base_predictions = indexed(baseline["predictions"], ("task_id",))
    require(
        set(all_index) == set(base_attempts) == set(expected_attempts()),
        "attempt index set mismatch",
    )
    chosen = {(t, selected(t)) for t in TASKS}
    require(
        set(final_index) == set(batch_index) == chosen
        and set(base_predictions) == {(t,) for t in TASKS},
        "selection index set mismatch",
    )
    records = {}
    event_records = {}
    predictions = {}
    for task in TASKS:
        required = {"run_1", "run_2"} if int(task[5:]) in RETRIES else {"run_1"}
        require(
            {p.name for p in (source.root / "run/tasks" / task).glob("run_*")}
            == required,
            "attempt directory mismatch",
        )
        for attempt in sorted(required):
            prefix = f"run/tasks/{task}/{attempt}"
            raw = source.read(prefix + "/run.json")
            events_data = source.read(prefix + "/events.jsonl")
            record = loads(raw)
            events = json_lines(events_data)
            base = base_attempts[(task, attempt)]
            require(
                digest(raw) == base["run_sha256"]
                and digest(events_data) == base["events_sha256"],
                "P0 run/event digest mismatch",
            )
            require(
                record["task_id"] == task
                and record["harness"] == "codex"
                and record["model"] == "qwen3.8-flash",
                "attempt identity mismatch",
            )
            require(
                all(
                    record.get(k) == all_index[(task, attempt)].get(k)
                    for k in BASE_KEYS
                ),
                "aggregate attempt mismatch",
            )
            starts = [e for e in events if e.get("event") == "run_started"]
            ends = [e for e in events if e.get("event") == "run_finished"]
            require(len(starts) == len(ends) == 1, "attempt lifecycle mismatch")
            start, end = starts[0], ends[0]
            require(
                start["task_id"] == task
                and start["experiment_fingerprint"] == FINGERPRINT
                and start["harness"] == "codex"
                and start["model"] == "qwen3.8-flash",
                "start identity mismatch",
            )
            metadata = start["invocation"]["metadata"]
            require(
                metadata["simple_fixes"] == identity["execution_policy"]["simple_fixes"]
                and metadata["runtime_version"] == "0.145.0",
                "attempt policy mismatch",
            )
            require(
                all(
                    record.get(k) == end.get(k)
                    for k in [
                        "status",
                        "return_code",
                        "error",
                        "prediction_path",
                        "wall_time_seconds",
                    ]
                ),
                "completion event mismatch",
            )
            require(
                (record.get("usage") or None) == base["usage"]
                and record["status"] == base["status"],
                "P0 attempt metadata mismatch",
            )
            records[(task, attempt)] = record
            event_records[(task, attempt)] = events
        attempt = selected(task)
        record = records[(task, attempt)]
        require(record["status"] == "submitted", "selected attempt failed")
        for table in [final_index, batch_index]:
            require(
                all(record.get(k) == table[(task, attempt)].get(k) for k in BASE_KEYS),
                "selected aggregate mismatch",
            )
        output = source.read(f"run/tasks/{task}/{attempt}/output/prediction.csv")
        final = source.read(f"run/predictions/{task}/prediction.csv")
        base = base_predictions[(task,)]
        require(
            digest(output) == digest(final) == base["sha256"]
            and base["attempt"] == attempt
            and len(final) == base["bytes"],
            "P0 prediction mismatch",
        )
        csv_rows(final)
        predictions[task] = final
    require(
        Counter(r["status"] for r in records.values())
        == {"submitted": 410, "runtime_error": 3, "no_prediction": 1},
        "historical status counts mismatch",
    )
    require(
        {key for key, r in records.items() if not r.get("usage")} == MISSING_USAGE,
        "historical missing usage mismatch",
    )
    return experiment, records, event_records, predictions


def score(source, prediction_root, output, python=sys.executable):
    evaluation = [
        k for k in source.json("frozen_manifest.json") if k.startswith("evaluation/")
    ]
    require(len(evaluation) == 121, "scoring frozen set mismatch")
    require(
        digest(source.read("evaluation/evaluate.py")) == EVALUATOR_SHA,
        "evaluator digest mismatch",
    )
    reference = output / "private-reference"
    reference.mkdir()
    script = reference / "evaluate.py"
    script.write_bytes(source.read("evaluation/evaluate.py"))
    require(
        (script.stat().st_dev, script.stat().st_ino)
        != (
            (source.root / "evaluation/evaluate.py").stat().st_dev,
            (source.root / "evaluation/evaluate.py").stat().st_ino,
        ),
        "evaluator copy shares inode",
    )
    env = {k: v for k, v in os.environ.items() if k in ["PATH", "LANG", "LC_ALL"]}
    tmp = output / "tmp"
    tmp.mkdir(exist_ok=True)
    env.update(
        PYTHONPATH="",
        PYTHONNOUSERSITE="1",
        PYTHONDONTWRITEBYTECODE="1",
        TMPDIR=str(tmp),
        HOME=str(output / "private-home"),
    )
    result_path = output / "offline-score.json"
    command = [
        str(python),
        "-B",
        str(script),
        "--prediction-root",
        str(prediction_root),
        "--gold-root",
        str(source.root / "evaluation/output"),
        "--config-root",
        str(source.root / "evaluation/configs"),
        "--output",
        str(result_path),
    ]
    (output / "score-command.json").write_bytes(
        json_bytes(
            {
                "command": command,
                "environment_keys": sorted(env),
                "model_calls": 0,
                "benchmark_tasks_executed": 0,
                "timeout_seconds": 300,
            }
        )
    )
    with (output / "score.log").open("w") as log:
        try:
            process = subprocess.run(
                command,
                cwd=output,
                env=env,
                stdout=log,
                stderr=subprocess.STDOUT,
                timeout=300,
            )
        except subprocess.TimeoutExpired as exc:
            raise EvidenceError("offline scoring timed out") from exc
    require(process.returncode == 0, "frozen evaluator failed")
    actual = loads(result_path.read_bytes())
    historical = source.json("public60-eval.json")
    require(actual == historical, "offline result differs from frozen per-task scoring")
    require(
        actual["config_set_sha256"] == CONFIG_SHA
        and actual["task_count"] == 60
        and actual["passed_task_count"] == 45
        and actual["failed_task_count"] == 15
        and len(actual["unscored_prediction_tasks"]) == 350,
        "offline score contract mismatch",
    )
    first = source.json("before_failed_four_retry/public60-eval.json")
    first_summary = source.json("before_failed_four_retry/results.json")
    require(
        first["task_count"] == first_summary["public_task_count"] == 60
        and first["passed_task_count"] == first_summary["public_passed"] == 44
        and first["config_set_sha256"] == CONFIG_SHA,
        "historical first pass mismatch",
    )
    initial = {
        "kind": "historical-record-not-recomputed",
        "task_count": 60,
        "passed_task_count": 44,
        "failed_task_count": 16,
        "config_set_sha256": CONFIG_SHA,
        "source_sha256": {
            "evaluation": digest(
                source.read("before_failed_four_retry/public60-eval.json")
            ),
            "results": digest(source.read("before_failed_four_retry/results.json")),
        },
    }
    return actual, initial


def write_zip(files, path):
    with zipfile.ZipFile(
        path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
    ) as archive:
        for name in sorted(files):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(
                info, files[name], compress_type=zipfile.ZIP_DEFLATED, compresslevel=9
            )


def assemble_manifest(files, source_hashes, traces):
    files["manifest.json"] = json_bytes(
        {
            "schema": 1,
            "experiment_fingerprint": FINGERPRINT,
            "frozen_manifest_sha256": MANIFEST_SHA,
            "selection_policy": POLICY,
            "scoring": {
                "evaluator_sha256": EVALUATOR_SHA,
                "config_set_sha256": CONFIG_SHA,
                "model_calls": 0,
            },
            "traces": traces,
            "files": {
                name: dict(
                    sha256=digest(data),
                    bytes=len(data),
                    **(
                        {"source_sha256": source_hashes[name]}
                        if name in source_hashes
                        else {}
                    ),
                )
                for name, data in sorted(files.items())
            },
        }
    )
    files["SHA256SUMS"] = "".join(
        f"{digest(files[name])}  {name}\n" for name in sorted(files)
    ).encode()


def build(experiment_root, baseline_path, official_root, output):
    source = Source(experiment_root)
    baseline_raw = Path(baseline_path).read_bytes()
    baseline = loads(baseline_raw)
    experiment, records, events, predictions = source_records(source, baseline)
    official, official_source = official_record(official_root)
    files = {}
    source_hashes = {}
    redaction_files = {}
    private_edits = {}
    forbidden = set()
    identity = experiment["identity"]
    staging = output / "payload"
    staging.mkdir()

    def put(name, data, raw=None):
        files[name] = data
        if raw is not None:
            source_hashes[name] = digest(raw)
        p = staging / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)

    def redacted(name, value, redactor, raw=None, lines=False):
        before = len(redactor.edits)
        clean = redactor.value(value)
        data = (
            "".join(
                json.dumps(x, ensure_ascii=False, sort_keys=True, allow_nan=False)
                + "\n"
                for x in clean
            ).encode()
            if lines
            else json_bytes(clean)
        )
        put(name, data, raw)
        changes = redactor.edits[before:]
        redaction_files[name] = dict(Counter(e["rule"] for e in changes))
        private_edits[name] = changes
        forbidden.update(redactor.known)

    summaries = []
    traces = {}
    summary_edits = []
    for task, attempt in expected_attempts():
        record = records[(task, attempt)]
        redactor = Redactor(source.root, identity)
        summary = {
            k: record.get(k)
            for k in [
                "task_id",
                "harness",
                "model",
                "status",
                "return_code",
                "wall_time_seconds",
                "model_turns",
                "tool_actions",
                "error",
            ]
        }
        summary.update(
            attempt=attempt,
            selected=attempt == selected(task),
            usage_present=bool(record.get("usage")),
            usage=record.get("usage") or None,
            prediction_sha256=(
                digest(predictions[task]) if attempt == selected(task) else None
            ),
            source_run_sha256=digest(
                source.read(f"run/tasks/{task}/{attempt}/run.json")
            ),
            source_events_sha256=digest(
                source.read(f"run/tasks/{task}/{attempt}/events.jsonl")
            ),
        )
        summaries.append(redactor.value(summary))
        summary_edits.extend(redactor.edits)
        if (int(task[5:]), int(attempt[4:])) in TRACE_ATTEMPTS:
            prefix = f"run/tasks/{task}/{attempt}"
            target = f"selected-traces/{task}/{attempt}"
            for name in ["run.json", "events.jsonl", "stdout.log", "stderr.log"]:
                raw = source.read(prefix + "/" + name)
                if name == "run.json":
                    redacted(target + "/" + name, loads(raw), redactor, raw)
                elif name in ["events.jsonl", "stdout.log"]:
                    redacted(
                        target + "/" + name, json_lines(raw), redactor, raw, lines=True
                    )
                else:
                    before = len(redactor.edits)
                    data = redactor.text(
                        raw.decode("utf-8"), target + "/" + name
                    ).encode()
                    put(target + "/" + name, data, raw)
                    changes = redactor.edits[before:]
                    redaction_files[target + "/" + name] = dict(
                        Counter(e["rule"] for e in changes)
                    )
                    private_edits[target + "/" + name] = changes
            traces[task + "/" + attempt] = {
                "events": [e.get("event") for e in events[(task, attempt)]],
                "stdout_types": [
                    e.get("type")
                    for e in json_lines(source.read(prefix + "/stdout.log"))
                ],
            }
        forbidden.update(redactor.known)
        private_edits[task + "/" + attempt + "/identifier_map"] = redactor.ids
    put(
        "attempts.jsonl",
        "".join(
            json.dumps(s, ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n"
            for s in summaries
        ).encode(),
    )
    redaction_files["attempts.jsonl"] = dict(Counter(e["rule"] for e in summary_edits))
    table = io.StringIO(newline="")
    writer = csv.DictWriter(
        table,
        fieldnames=["task_id", "attempt", "policy", "sha256"],
        lineterminator="\n",
    )
    writer.writeheader()
    for task in TASKS:
        put(f"predictions/{task}/prediction.csv", predictions[task], predictions[task])
        require(
            (
                (staging / f"predictions/{task}/prediction.csv").stat().st_dev,
                (staging / f"predictions/{task}/prediction.csv").stat().st_ino,
            )
            != (
                (source.root / f"run/predictions/{task}/prediction.csv").stat().st_dev,
                (source.root / f"run/predictions/{task}/prediction.csv").stat().st_ino,
            ),
            "prediction shares source inode",
        )
        writer.writerow(
            dict(
                task_id=task,
                attempt=selected(task),
                policy=POLICY,
                sha256=digest(predictions[task]),
            )
        )
    put("final-selection.csv", table.getvalue().encode())
    usage = {}
    for record in summaries:
        for key, value in (record["usage"] or {}).items():
            usage[key] = usage.get(key, 0) + value
    redacted(
        "experiment.json",
        dict(
            experiment,
            observed_usage_all_attempts=usage,
            usage_scope="recorded-only; three attempts missing usage",
        ),
        Redactor(source.root, identity),
        source.read("run/experiment.json"),
    )
    actual, initial = score(source, staging / "predictions", output)
    put("evaluation/public60.json", json_bytes(actual))
    put("evaluation/historical-first-pass.json", json_bytes(initial))
    put("official/entry.json", json_bytes(official))
    put(
        "redaction-report.json",
        json_bytes(
            {
                "schema": 1,
                "rules": [
                    "declared-source-or-endpoint",
                    "host-home",
                    "identifier",
                    "identifier-field",
                    "credential-token",
                    "credential-assignment",
                    "credential-field",
                    "bearer",
                    "credential-quoted",
                    "authorization",
                    "identifier-text",
                ],
                "files": redaction_files,
                "scope": "six complete selected attempts; no original-value map; prediction bytes untouched",
                "limitations": "Historical alias only; no new inference or model snapshot guarantee; complete original traces remain private",
            }
        ),
    )
    put(
        "README.md",
        b"# EviWeave V6 historical evidence\n\n410 unchanged predictions; 414 attempt summaries; six complete redacted selected attempts (tasks 33, 328, 35, 391 and 23; task 23 includes failure and recovery). This is a selection of traces, not the complete 414-attempt trace corpus. Fixed recovery selection is documented in final-selection.csv. Three attempts have missing usage; reported usage totals cover recorded observations only.\n\nLocal frozen offline scoring: 45/60; first-pass historical record: 44/60. Official historical result: 333/410, 81.22%, tied first as of 2026-10-22; evaluated 2026-10-21. The official registered method codex-controlled + Qwen3.8-Flash corresponds to this V6 baseline and excludes later Controlled code. No new inference or benchmark task execution occurred.\n\nVerify SHA256SUMS and use tools/package_evidence.py verify from the source distribution. The manifest covers payload files; SHA256SUMS also covers the manifest; the external archive digest covers the whole ZIP. Reference answers, datasets, private configuration and the full original traces are not distributed.\n\nCode and documentation: MIT, Jun Yuan / DataSpace attribution retained in the source LICENSE. Predictions and source data remain subject to their original distribution terms.\n",
    )
    put(
        "README.zh-CN.md",
        (
            "# EviWeave V6 历史实验证据\n\n410份原字节预测、414条尝试摘要和5题6次完整脱敏精选轨迹：33、328、35、391各run_1，23含失败run_1及恢复run_2。仅公开精选轨迹，完整原始414次trace私下保留。最终选择固定见final-selection.csv，不按分数重选。三次usage缺失保持缺失，汇总仅含已记录部分。\n\n冻结离线重评45/60；首轮44/60为历史记录。官方历史333/410、81.22%，截至2026-10-22并列第一，官方评测日期2026-10-21。登记名codex-controlled + Qwen3.8-Flash对应本次V6，与后续Controlled实现区分。没有新推理或benchmark任务执行，不保证未来新推理重现该成绩。\n\n用源码发行中的tools/package_evidence.py verify独立核验。载荷由manifest登记，SHA256SUMS再覆盖manifest，外部摘要覆盖整个ZIP。原始数据、参考答案、私有配置及完整原始轨迹不随包公开。\n\n代码和说明采用MIT，作者袁珺 / Jun Yuan；保留DataSpace来源与源代码LICENSE。预测及来源数据沿用原分发条款。\n"
        ).encode(),
    )
    assemble_manifest(files, source_hashes, traces)
    # Validate in memory, then reload an actual archive before publishing its final name.
    verification = verify_payload(files, forbidden)
    source.unchanged()
    official_source.unchanged()
    require(
        digest(Path(baseline_path).read_bytes()) == digest(baseline_raw),
        "P0 report changed during build",
    )
    (output / "source-inventory.json").write_bytes(
        json_bytes(
            {
                "experiment": source.files,
                "official": official_source.files,
                "baseline_sha256": digest(baseline_raw),
            }
        )
    )
    (output / "private-redactions.json").write_bytes(
        json_bytes(
            {"edits": private_edits, "declared_private_values": sorted(forbidden)}
        )
    )
    candidate = output / "candidate.zip"
    write_zip(files, candidate)
    verification = verify_payload(read_archive(candidate), forbidden)
    # Extraction for inspection only follows validated whitelist entries and never ZIP paths directly.
    unpacked = output / "verified-unpacked"
    unpacked.mkdir()
    for name, data in read_archive(candidate).items():
        p = unpacked / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    require(
        all((unpacked / name).read_bytes() == data for name, data in files.items()),
        "unpacked bytes differ",
    )
    final = output / ARCHIVE_NAME
    candidate.rename(final)
    sha = digest(final.read_bytes())
    (output / (ARCHIVE_NAME + ".sha256")).write_text(sha + "  " + ARCHIVE_NAME + "\n")
    return dict(
        verification,
        archive_sha256=sha,
        archive=ARCHIVE_NAME,
        source_files=len(source.files),
        scoring_python=sys.version,
        source_unchanged=True,
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    b = sub.add_parser("build")
    b.add_argument("--experiment-root", type=Path, required=True)
    b.add_argument("--baseline-report", type=Path, required=True)
    b.add_argument("--official-evidence-root", type=Path, required=True)
    b.add_argument("--output-root", type=Path, required=True)
    v = sub.add_parser("verify")
    v.add_argument("--archive", type=Path, required=True)
    v.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args(argv)
    sources = (
        [args.experiment_root, args.baseline_report, args.official_evidence_root]
        if args.command == "build"
        else [args.archive]
    )
    try:
        output = output_path(args.output_root, sources)
    except EvidenceError as exc:
        parser.error(str(exc))
    output.mkdir(parents=True, exist_ok=True)
    report = {
        "status": "FAIL",
        "command": args.command,
        "model_calls": 0,
        "benchmark_tasks_executed": 0,
    }
    code = 1
    try:
        if args.command == "build":
            for source in sources:
                if not source.exists():
                    raise MissingMaterial("required input missing")
            report.update(
                build(
                    args.experiment_root,
                    args.baseline_report,
                    args.official_evidence_root,
                    output,
                )
            )
        else:
            if not args.archive.is_file():
                raise MissingMaterial("archive missing")
            report.update(verify_payload(read_archive(args.archive)))
            report["archive_sha256"] = digest(args.archive.read_bytes())
            sidecar = args.archive.with_name(args.archive.name + ".sha256")
            if sidecar.exists():
                require(
                    sidecar.read_text()
                    == report["archive_sha256"] + "  " + args.archive.name + "\n",
                    "external archive digest mismatch",
                )
                report["external_sha256"] = "PASS"
            else:
                report["external_sha256"] = "NOT_SUPPLIED"
        code = 0
    except MissingMaterial as exc:
        report["error"] = str(exc)
        code = 2
    except (
        EvidenceError,
        OSError,
        zipfile.BadZipFile,
        KeyError,
        TypeError,
        UnicodeError,
        subprocess.SubprocessError,
    ) as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
    if code:
        # A failure never leaves a file bearing the release filename.
        for name in [ARCHIVE_NAME, ARCHIVE_NAME + ".sha256"]:
            (output / name).unlink(missing_ok=True)
    (output / "report.json").write_bytes(json_bytes(report))
    print(
        json.dumps(
            {
                "status": report["status"],
                "command": args.command,
                "output": str(output),
                "error": report.get("error"),
                "model_calls": 0,
            }
        )
    )
    return code


if __name__ == "__main__":
    raise SystemExit(main())
