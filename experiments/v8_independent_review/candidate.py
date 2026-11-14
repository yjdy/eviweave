"""Shared candidate checkpoints for the V8 independent review experiment."""

from dataclasses import replace
import hashlib
import json
from uuid import UUID


TASKS = (35, 48, 103, 129, 279)
KEY = "EVIWEAVE_API_KEY"

from tools import run_v8
from dataspace_baselines.core.prediction import validate_prediction


STAGE = """

Pilot stage: generate the shared candidate only.
The prediction.csv required above is the pre-review candidate. Solve as usual
and verify its CSV format. Save /mnt/output/extraction.md with the source paths
and locators, exact SQL or equivalent executable calculation steps actually
used, joins, filters, grouping, sort order and output column mapping. Reference
any calculation scripts saved in /mnt/output and keep those scripts there.
Once both files exist, end this turn so the experiment can save the checkpoint.
The separate V8 pre-export semantic review is reserved for a later turn. Do not
call its script or produce a semantic review record in this candidate stage.
"""


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def digest(path):
    with path.open("rb") as handle:
        h = hashlib.sha256()
        for block in iter(lambda: handle.read(1 << 20), b""):
            h.update(block)
        return h.hexdigest()


def write_json(path, value):
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


def checkpoint(run_root):
    output = run_root / "output"
    candidate = output / "prediction.csv"
    extraction = output / "extraction.md"
    if not candidate.is_file() or candidate.is_symlink():
        raise ValueError("candidate CSV is missing or not a regular file")
    if not extraction.is_file() or extraction.is_symlink() or not extraction.read_text(encoding="utf-8").strip():
        raise ValueError("extraction.md is missing or empty")
    if (output / run_v8.semantic_review.REVIEW_FILE).exists():
        raise ValueError("a semantic review was written during candidate generation")
    ids = set()
    last_turn = None
    with (run_root / "stdout.log").open(encoding="utf-8") as handle:
        for line in handle:
            event = json.loads(line)
            if event.get("type") == "thread.started":
                ids.add(str(UUID(event["thread_id"])))
            if event.get("type") in {"turn.completed", "turn.failed"}:
                last_turn = event["type"]
    if len(ids) != 1 or last_turn != "turn.completed":
        raise ValueError("expected one completed candidate session")
    session_id = ids.pop()
    home = run_root / "runtime/home/.codex"
    sessions = list((home / "sessions").rglob(f"*{session_id}.jsonl"))
    if len(sessions) != 1 or sessions[0].stat().st_size == 0:
        raise ValueError(f"expected a persisted session for {session_id}")
    session = sessions[0]
    with session.open(encoding="utf-8") as handle:
        meta = json.loads(next(handle))
    if meta.get("type") != "session_meta" or meta.get("payload", {}).get("id") != session_id:
        raise ValueError("persisted session metadata does not match the CLI session ID")
    return {
        "session_id": session_id,
        "codex_home": str(home.relative_to(run_root)),
        "session_path": str(session.relative_to(run_root)),
        "session_sha256": digest(session),
        "candidate_csv": str(candidate.relative_to(run_root)),
        "candidate": validate_prediction(candidate),
        "extraction_path": str(extraction.relative_to(run_root)),
        "extraction_sha256": digest(extraction),
    }


def candidate_runner(config):
    runner = run_v8.build_v8_harness(config)
    original = runner.invocation_factory

    def invocation(task, run_root, output_root):
        base = original(task, run_root, output_root)
        command = list(base.command)
        boundary = command.index("--")
        inner = command[boundary + 1:]
        prompt = inner[-1]
        suffix = run_v8.semantic_review.INSTRUCTIONS
        if not prompt.endswith(suffix) or "--ephemeral" not in inner[:-1]:
            raise ValueError("unexpected V8 candidate invocation")
        inner.remove("--ephemeral")
        inner[-1] = prompt.removesuffix(suffix) + STAGE
        metadata = dict(base.metadata)
        del metadata["semantic_review"]
        metadata.update(stage="shared_candidate", session="task-local-persistent")
        return replace(base, command=tuple([*command[:boundary + 1], *inner]), metadata=metadata)

    runner.invocation_factory = invocation
    runner.run_validator = checkpoint
    runner.cleanup = None
    runner.resume_identity["pilot"] = {"stage": "shared_candidate", "prompt_sha256": hashlib.sha256(STAGE.encode()).hexdigest()}
    return runner
