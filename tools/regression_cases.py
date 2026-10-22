"""Handwritten shared cases: each result is constrained by an independent expectation."""

from __future__ import annotations
import csv
import hashlib
import io
import json
import math
import os
from pathlib import Path
import sys
import threading
import time
from unittest.mock import patch

from dataspace_baselines.core.export_prediction import export_prediction
from dataspace_baselines.core.prediction import (
    validate_prediction,
    recover_nested_prediction,
)
from dataspace_baselines.core.task import TaskSpec
from dataspace_baselines.core.batch import (
    BatchOptions,
    run_batch,
    finalized_attempt,
    ensure_experiment_identity,
)
from dataspace_baselines.runners.native_cli import NativeCliInvocation, NativeCliRunner


def task(root, number=1):
    p = root / f"task_{number}"
    (p / "context").mkdir(parents=True)
    (p / "context/data.csv").write_text(f"value\n{number}\n")
    (p / "task.json").write_text(
        json.dumps({"task_id": p.name, "question": f"Return {number}."})
    )
    return TaskSpec.load(p)


def shared_cases(root):
    root = root.resolve()
    records = []

    def case(name, fn):
        p = root / name
        p.mkdir(parents=True)
        try:
            value = fn(p)
        except Exception as exc:
            (p / "case-result.json").write_text(
                json.dumps(
                    {
                        "case": name,
                        "status": "FAIL",
                        "type": type(exc).__name__,
                        "reason": str(exc),
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n"
            )
            raise
        record = {"case": name, "status": "PASS", "result": value}
        (p / "case-result.json").write_text(
            json.dumps(record, ensure_ascii=False, indent=2) + "\n"
        )
        records.append(record)

    def export(p):
        raw = '{"columns":["code","text","number","flag","null","decimal"],"rows":[["000001","中文，（全角）\\"引号\\"\\n第二行。","1.230000000000000000001",true,null,1.230000000000000000001]]}'
        source = p / "in.json"
        source.write_text(raw)
        out = p / "prediction.csv"
        meta = export_prediction(source, out)
        expected = [
            ["code", "text", "number", "flag", "null", "decimal"],
            [
                "000001",
                '中文，（全角）"引号"\n第二行。',
                "1.230000000000000000001",
                "true",
                "",
                "1.230000000000000000001",
            ],
        ]
        assert list(csv.reader(io.StringIO(out.read_text()))) == expected
        expected_bytes = 'code,text,number,flag,null,decimal\n000001,"中文，（全角）""引号""\n第二行。",1.230000000000000000001,true,,1.230000000000000000001\n'.encode()
        assert (
            out.read_bytes() == expected_bytes
            and meta["sha256"] == hashlib.sha256(expected_bytes).hexdigest()
        )
        return meta

    case("export_values", export)

    def empty(p):
        s = p / "in.json"
        s.write_text('{"columns":["answer"],"rows":[]}')
        o = p / "prediction.csv"
        meta = export_prediction(s, o)
        assert o.read_bytes() == b"answer\n"
        assert validate_prediction(o)["data_rows"] == 0
        return meta

    case("export_empty", empty)
    invalid = [
        "[]",
        "{}",
        '{"columns":[],"rows":[]}',
        '{"columns":[1],"rows":[]}',
        '{"columns":["a"],"rows":{}}',
        '{"columns":["a"],"rows":[[]]}',
        '{"columns":["a"],"rows":[[{}]]}',
        '{"columns":["a"],"rows":[[NaN]]}',
        '{"columns":["a"],"rows":[[Infinity]]}',
    ]
    for n, raw in enumerate(invalid):

        def bad(p, raw=raw):
            s = p / "in.json"
            s.write_text(raw)
            o = p / "prediction.csv"
            o.write_bytes(b"old\nkeep\n")
            try:
                export_prediction(s, o)
            except ValueError as e:
                error = {"type": type(e).__name__, "reason": str(e)}
            else:
                raise AssertionError("invalid exporter input accepted")
            assert o.read_bytes() == b"old\nkeep\n" and not list(
                p.glob(".prediction-*")
            )
            return error

        case(f"export_invalid_{n}", bad)

    def atomic(p):
        s = p / "in.json"
        s.write_text('{"columns":["a"],"rows":[[1]]}')
        o = p / "prediction.csv"
        o.write_bytes(b"old\n")
        with patch(
            "dataspace_baselines.core.export_prediction.os.replace",
            side_effect=OSError("publication conflict"),
        ):
            try:
                export_prediction(s, o)
            except OSError:
                pass
            else:
                raise AssertionError("publication failure swallowed")
        assert o.read_bytes() == b"old\n" and not list(p.glob(".prediction-*"))
        return "old preserved; temporary removed"

    case("export_publication_failure", atomic)
    for n, (raw, valid) in enumerate(
        [
            (b"", False),
            (b"\n", False),
            (b"a,b\n1\n", False),
            (b"a\n\xff\n", False),
            (b"\xef\xbb\xbfa\r\n1\r\n", True),
            (b"a\r1\r", True),
            (b"a\n", True),
        ]
    ):

        def validate(p, raw=raw, valid=valid):
            o = p / "prediction.csv"
            o.write_bytes(raw)
            try:
                result = validate_prediction(o)
            except (ValueError, UnicodeError) as e:
                assert not valid
                return {"type": type(e).__name__, "reason": str(e)}
            assert valid
            return result

        case(f"csv_contract_{n}", validate)
    for kind in [
        "unique",
        "invalid_csv",
        "multiple",
        "none",
        "file_link",
        "dir_link",
        "mixed_link",
        "canonical",
        "canonical_link",
        "changed",
        "conflict",
    ]:

        def recovery(p, kind=kind):
            out = p / "output"
            out.mkdir()
            outside = p / "sentinel"
            outside.write_bytes(b"answer\nprivate\n")
            q = out / "nested"
            q.mkdir()
            candidate = q / "prediction.csv"
            if kind not in ["none", "file_link", "dir_link"]:
                candidate.write_bytes(b"\xef\xbb\xbfanswer\r\n00001\r\n")
            if kind == "invalid_csv":
                candidate.write_bytes(b"a,b\n1\n")
            if kind == "multiple":
                (out / "other").mkdir()
                (out / "other/prediction.csv").write_bytes(b"a\n2\n")
            if kind in ["file_link", "mixed_link"]:
                (out / "prediction-link").mkdir()
                (out / "prediction-link/prediction.csv").symlink_to(outside)
            if kind == "dir_link":
                (out / "escape").symlink_to(p, target_is_directory=True)
            target = out / "prediction.csv"
            if kind == "canonical":
                target.write_bytes(b"existing\n")
            if kind == "canonical_link":
                target.symlink_to(outside)
            original_copy = __import__("shutil").copyfileobj
            original_link = os.link

            def changed(src, dst):
                original_copy(src, dst)
                dst.write(b"extra\n")

            def conflict(src, dst):
                Path(dst).write_bytes(b"competitor\n")
                original_link(src, dst)

            with (
                patch(
                    "dataspace_baselines.core.prediction.shutil.copyfileobj",
                    side_effect=changed if kind == "changed" else original_copy,
                ),
                patch(
                    "dataspace_baselines.core.prediction.os.link",
                    side_effect=conflict if kind == "conflict" else original_link,
                ),
            ):
                try:
                    result = recover_nested_prediction(out)
                except (ValueError, OSError) as e:
                    assert kind not in ["unique", "mixed_link"]
                    (p / "error.log").write_text(str(e) + "\n")
                    result = {
                        "type": type(e).__name__,
                        "reason": e.strerror if isinstance(e, OSError) else str(e),
                    }
                    if isinstance(e, OSError):
                        result["errno"] = e.errno
                else:
                    assert kind in ["unique", "mixed_link"]
                    assert target.read_bytes() == candidate.read_bytes()
                    assert result["source_sha256"] == result["target_sha256"]
            assert outside.read_bytes() == b"answer\nprivate\n" and not list(
                out.glob(".recovered-*")
            )
            if kind == "canonical":
                assert target.read_bytes() == b"existing\n"
            if kind == "canonical_link":
                assert target.is_symlink()
            if kind == "conflict":
                assert target.read_bytes() == b"competitor\n"
            return result

        case("recovery_" + kind, recovery)
    for mode, expected in [
        ("success", "submitted"),
        ("nonzero", "runtime_error"),
        ("timeout", "wall_time_exhausted"),
        ("missing", "no_prediction"),
        ("invalid", "invalid_prediction"),
        ("validator", "runtime_error"),
        ("budget", "model_output_exhausted"),
        ("absent", "runtime_error"),
        ("trace", "submitted"),
        ("cancel", "stopped"),
        ("nested", "submitted"),
    ]:

        def run(p, mode=mode, expected=expected):
            t = task(p)
            ready = p / "ready"
            pidfile = p / "pid"
            cleaned = []

            def factory(t, r, o):
                csvdata = b"a,b\n1\n" if mode == "invalid" else b"answer\n42\n"
                dest = (
                    o / "nested/prediction.csv"
                    if mode == "nested"
                    else o / "prediction.csv"
                )
                script = f"import os,time;from pathlib import Path;Path({str(pidfile)!r}).write_text(str(os.getpid()));"
                if mode != "missing":
                    script += f"p=Path({str(dest)!r});p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes({csvdata!r});"
                script += f"Path({str(ready)!r}).touch();"
                if mode == "budget":
                    script += 'print("reason: max_output_tokens");'
                if mode in ["timeout", "cancel"]:
                    script += "time.sleep(20);"
                script += (
                    f'raise SystemExit({1 if mode in ["budget","nonzero"] else 0})'
                )
                return NativeCliInvocation(
                    command=(
                        ("/missing-eviweave-executable",)
                        if mode == "absent"
                        else (sys.executable, "-B", "-c", script)
                    ),
                    cwd=p,
                    inherit_environment=False,
                    environment={"PATH": os.defpath},
                )

            def reject(r):
                raise ValueError("validator rejected")

            def broken_trace(r):
                raise ValueError("trace rejected")

            def _exit_classification(run, code):
                text = (run / "stdout.log").read_text()
                if "reason: max_output_tokens" in text:
                    return ("model_output_exhausted", "synthetic budget marker")
                return None

            runner = NativeCliRunner(
                harness_key="synthetic",
                model_key="no-model",
                wall_time_seconds=1 if mode == "timeout" else 5,
                invocation_factory=factory,
                termination_grace_seconds=0.1,
                cleanup=cleaned.append,
                run_validator=reject if mode == "validator" else None,
                trace_parser=broken_trace if mode == "trace" else None,
                exit_classifier=_exit_classification,
                recover_nested_prediction=mode == "nested",
            )
            if mode == "cancel":
                holder = []
                worker = threading.Thread(
                    target=lambda: holder.append(runner.run(t, p / "run"))
                )
                worker.start()
                deadline = time.monotonic() + 3
                try:
                    while not ready.exists() and time.monotonic() < deadline:
                        time.sleep(0.01)
                    assert ready.exists()
                    runner.cancel()
                    worker.join(3)
                    assert not worker.is_alive()
                    result = holder[0]
                finally:
                    runner.cancel()
                    worker.join(3)
            else:
                result = runner.run(t, p / "run")
            assert (
                result.status == expected
                and result.usage == {}
                and bool(result.prediction_path) == (expected == "submitted")
            )
            assert (
                len(cleaned) == 1
                and not runner._active_processes
                and math.isfinite(result.wall_time_seconds)
                and 0 <= result.wall_time_seconds < 8
            )
            if pidfile.exists():
                try:
                    os.kill(int(pidfile.read_text()), 0)
                except ProcessLookupError:
                    pass
                else:
                    raise AssertionError("test process survived")
            record = json.loads((p / "run/run.json").read_text())
            assert record["status"] == expected
            events = [
                json.loads(x) for x in (p / "run/events.jsonl").read_text().splitlines()
            ]
            assert (
                events[0]["event"] == "run_started"
                and events[-1]["event"] == "run_finished"
            )
            return {
                "status": result.status,
                "usage": result.usage,
                "error": (
                    result.error.replace(str(p), "<CASE>") if result.error else None
                ),
                "events": [e["event"] for e in events],
                "return_code": record.get("return_code"),
                "prediction_sha256": (
                    hashlib.sha256(result.prediction_path.read_bytes()).hexdigest()
                    if result.prediction_path
                    else None
                ),
            }

        case("runner_" + mode, run)
    for concurrency in [1, 2]:

        def batch(p, concurrency=concurrency):
            tasks = p / "inputs"
            task(tasks, 1)
            task(tasks, 2)

            def factory(t, r, o):
                content = "answer\n" + t.task_id + "\n"
                return NativeCliInvocation(
                    command=(
                        sys.executable,
                        "-B",
                        "-c",
                        f"from pathlib import Path;Path({str(o/'prediction.csv')!r}).write_text({content!r})",
                    ),
                    cwd=p,
                    inherit_environment=False,
                )

            runner = NativeCliRunner(
                harness_key="synthetic",
                model_key="none",
                wall_time_seconds=5,
                invocation_factory=factory,
                resume_identity={"model": "none", "budget": 32768},
            )
            runroot = p / "batch"
            options = BatchOptions(
                input_root=tasks, run_root=runroot, concurrency=concurrency
            )
            assert run_batch(options, runner) == 0
            assert (
                run_batch(
                    BatchOptions(
                        input_root=tasks,
                        run_root=runroot,
                        concurrency=concurrency,
                        resume=True,
                    ),
                    runner,
                )
                == 0
            )
            attempts = list(runroot.glob("tasks/task_*/run_*/run.json"))
            assert len(attempts) == 2
            for n in [1, 2]:
                assert (
                    runroot / f"predictions/task_{n}/prediction.csv"
                ).read_text() == f"answer\ntask_{n}\n"
            runner.resume_identity["budget"] = 1
            try:
                run_batch(
                    BatchOptions(input_root=tasks, run_root=runroot, resume=True),
                    runner,
                )
            except ValueError as e:
                assert "different experiment" in str(e)
            else:
                raise AssertionError("identity mismatch accepted")
            return {
                "attempts": 2,
                "statuses": json.loads((runroot / "batch_summary.json").read_text())[
                    "status_counts"
                ],
                "mismatch_rejected": True,
            }

        case(f"batch_{concurrency}", batch)
    for status in [
        "submitted",
        "runtime_error",
        "batch_error",
        "stopped",
        "wall_time_exhausted",
        "model_output_exhausted",
        "no_prediction",
        "invalid_prediction",
    ]:

        def finalized(p, status=status):
            r = p / "run_1"
            r.mkdir()
            (r / "run.json").write_text(json.dumps({"status": status}))
            answer = finalized_attempt(p)
            assert (answer is None) == (
                status in ["runtime_error", "batch_error", "stopped"]
            )
            return {"finalized": answer is not None}

        case("finalized_" + status, finalized)
    for first_status in [
        "runtime_error",
        "batch_error",
        "stopped",
        "wall_time_exhausted",
        "model_output_exhausted",
    ]:

        def resume(p, first_status=first_status):
            tasks = p / "inputs"
            task(tasks)

            def factory(t, r, o):
                if r.name == "run_1" and first_status == "batch_error":
                    raise ValueError("artificial batch failure")
                content = "answer\n42\n"
                destination = str(o / "prediction.csv")
                script = f'from pathlib import Path;Path({destination!r}).write_text({content!r});raise SystemExit({1 if r.name=="run_1" else 0})'
                return NativeCliInvocation(
                    command=(sys.executable, "-B", "-c", script),
                    cwd=p,
                    inherit_environment=False,
                )

            runner = NativeCliRunner(
                harness_key="synthetic",
                model_key="none",
                wall_time_seconds=5,
                invocation_factory=factory,
                resume_identity={"synthetic": "retry"},
                exit_classifier=lambda r, c: (first_status, "artificial first status"),
            )
            root = p / "batch"
            assert run_batch(BatchOptions(input_root=tasks, run_root=root), runner) == 1
            retry = first_status in ["runtime_error", "batch_error", "stopped"]
            assert run_batch(
                BatchOptions(input_root=tasks, run_root=root, resume=True), runner
            ) == (0 if retry else 1)
            attempts = sorted(x.name for x in (root / "tasks/task_1").glob("run_*"))
            assert attempts == (["run_1", "run_2"] if retry else ["run_1"])
            summary = json.loads((root / "batch_summary.json").read_text())
            assert summary["status_counts"] == (
                {"submitted": 1} if retry else {first_status: 1}
            )
            return {"attempts": attempts, "statuses": summary["status_counts"]}

        case("resume_" + first_status, resume)
    return records


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    result = shared_cases(args.output / "cases")
    (args.output / "results.json").write_text(
        json.dumps(
            {
                "cases": result,
                "modules": {
                    k: v.__file__
                    for k, v in sys.modules.items()
                    if k.startswith("dataspace_baselines")
                    and getattr(v, "__file__", None)
                },
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    )
