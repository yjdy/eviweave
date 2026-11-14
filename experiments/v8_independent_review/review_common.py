"""Validate prior V8 experiments and prepare candidate handoffs."""

from decimal import Decimal
import importlib.util
import json
from pathlib import Path
import shutil
import sys
import tempfile

from dataspace_baselines.core.task import TaskSpec

import candidate as shared

ROOT = Path(__file__).resolve().parents[2]
AUDIT = ROOT / ".audit"
BASELINE = AUDIT / "v8-public60-pdf-runs/20261113-122510-cm7sq0gf"
DEFAULT_RUN = AUDIT / "v8-independent-review-pilot-20261113/paired-public60"
HISTORICAL = AUDIT / "v8-semantic-public60-results-20261113/score.json"
REVIEW = shared.run_v8.semantic_review
RATES = {"uncached_input_tokens": Decimal("0.8"), "cached_input_tokens": Decimal("0.1"),
         "output_tokens": Decimal("2.7")}


def first_event(path):
    with path.open(encoding="utf-8") as handle:
        return json.loads(next(handle))


def ids(lock):
    return sorted(int(Path(name).stem.removeprefix("task_")) for name in lock["files"]
                  if name.startswith("configs/task_"))


def input_hashes(task):
    root = task.context_dir.parent
    return {str(path.relative_to(root)): shared.digest(path)
            for path in sorted(root.rglob("*")) if path.is_file()}


def settings(evaluation):
    baseline = shared.read_json(BASELINE / "progress.json")
    selection = shared.read_json(BASELINE / "selection.json")
    config_path = Path(baseline["config_path"])
    config = shared.run_v8.load_harness_experiment_config(config_path)
    fingerprint = shared.run_v8.build_v8_harness(config).resume_fingerprint
    lock = shared.read_json(ROOT / "provenance/evaluation-lock.json")
    if shared.digest(evaluation / "evaluate.py") != lock["evaluator_sha256"]:
        raise ValueError("评分器摘要与冻结记录不一致")
    for name, expected in lock["files"].items():
        if shared.digest(evaluation / name) != expected:
            raise ValueError(f"评分资源摘要不一致 {name}")
    pdf = {item["id"]: item for item in selection["tasks"]}
    numbers = ids(lock)
    if len(numbers) != 60 or len(pdf) != 54 or not set(pdf) <= set(numbers):
        raise ValueError("公开题清单应包含 54 道 PDF 和 6 道无 PDF 题")
    tasks = {}
    baseline_predictions = {path.parent.name for path in (BASELINE / "predictions").glob("task_*/prediction.csv")}
    if baseline["status"] != "complete" or baseline_predictions != {f"task_{n}" for n in numbers}:
        raise ValueError("现行 V8 的 60 题基线不完整")
    for n in numbers:
        name = f"task_{n}"
        path = Path(selection["dataset"]) / "input" / name
        task = TaskSpec.load(path)
        start = first_event(BASELINE / "tasks" / name / "events.jsonl")
        if (task.task_id != name or start["experiment_fingerprint"] != fingerprint
                or start["question"] != task.question or Path(start["workspace"]) != task.context_dir):
            raise ValueError(f"原批次输入或运行身份不同 {name}")
        if n in pdf:
            item = pdf[n]
            expected = {Path(selection["dataset"]) / file["path"]: file["sha256"] for file in item["pdfs"]}
            actual = {p for p in task.context_dir.rglob("*") if p.is_file() and p.suffix.casefold() == ".pdf"}
            if actual != set(expected) or any(shared.digest(p) != sha for p, sha in expected.items()):
                raise ValueError(f"PDF 与原清单不一致 {name}")
            if shared.digest(path / "task.json") != item["question_sha256"]:
                raise ValueError(f"题干摘要不一致 {name}")
        elif shared.run_v8.has_pdf(task):
            raise ValueError(f"无 PDF 分组出现 PDF {name}")
        tasks[n] = task
    return config, config_path, fingerprint, tasks, set(pdf)


def candidate_check(directory, task, config):
    saved = shared.read_json(directory / "checkpoint.json")
    expected = shared.checkpoint(directory)
    if any(saved.get(key) != value for key, value in expected.items()):
        raise ValueError(f"共享候选检查点摘要不一致 {directory}")
    event = first_event(directory / "events.jsonl")
    if (event["experiment_fingerprint"] != shared.candidate_runner(config).resume_fingerprint
            or event["task_id"] != task.task_id or event["question"] != task.question
            or Path(event["workspace"]) != task.context_dir):
        raise ValueError(f"共享候选运行身份不一致 {directory}")
    return saved


def archive(path):
    history = path.parent / "incomplete"
    history.mkdir(exist_ok=True)
    target = Path(tempfile.mkdtemp(prefix=path.name + "-", dir=history)) / path.name
    path.rename(target)
    print(f"保留未完成尝试 {target}", flush=True)


def session_id(path):
    ids = set()
    last = None
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            event = json.loads(line)
            if event.get("type") == "thread.started":
                ids.add(str(shared.UUID(event["thread_id"])))
            if event.get("type") in {"turn.completed", "turn.failed"}:
                last = event["type"]
    if len(ids) != 1 or last != "turn.completed":
        raise ValueError(f"预期恰好一个完成的 Codex 会话 {path}")
    return ids.pop()


def codex_home(directory):
    return directory / "runtime/home/.codex"


def copy_session(source, target, session):
    # Keep the source session and its local database isolated from later writes.
    shutil.copytree(codex_home(source), codex_home(target), symlinks=True)
    session_file(target, session)


def session_file(directory, session):
    files = list((codex_home(directory) / "sessions").rglob(f"*{session}.jsonl"))
    if len(files) != 1 or first_event(files[0]).get("payload", {}).get("id") != session:
        raise ValueError(f"找不到待接续会话 {session}")
    return files[0]


def package_candidate(candidate, saved, path):
    path.mkdir(parents=True)
    for source, name, digest in ((saved["candidate_csv"], "prediction.csv", saved["candidate"]["sha256"]),
                                 (saved["extraction_path"], "extraction.md", saved["extraction_sha256"])):
        shutil.copyfile(candidate / source, path / name)
        if shared.digest(path / name) != digest:
            raise ValueError(f"候选交接摘要不一致 {name}")
    notes = (path / "extraction.md").read_text(encoding="utf-8")
    for source in (candidate / "output").rglob("*"):
        if source.suffix not in {".py", ".sql", ".sh", ".R", ".r"} or not source.is_file() or source.is_symlink():
            continue
        relative = source.relative_to(candidate / "output")
        if relative.as_posix() in notes:
            target = path / "scripts" / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)


def load_scorer(evaluation):
    spec = importlib.util.spec_from_file_location("paired_frozen_evaluator", evaluation / "evaluate.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module
