from __future__ import annotations

import hashlib
import json
from pathlib import Path

from ..config import HarnessConfig, HarnessExperimentConfig
from ..core.prediction import NESTED_PREDICTION_RECOVERY_POLICY_VERSION
from ..runners.filesystem_jail import JAIL_HOME, JAIL_OUTPUT
from .native_common import (
    clean_environment,
    configured_runtime_binds,
    jailed_invocation,
    jsonl_trace_summary,
    native_runner,
    prompt_for,
    write_text,
)


SIMPLE_FIXES_VERSION = "simple-fixes-v1"
EXPORT_HELPER_PATH = (
    Path(__file__).resolve().parents[1] / "core" / "export_prediction.py"
)
EXPORT_HELPER_MOUNT = "/mnt/runtime/dataspace-export.py"


def _simple_fixes_instructions() -> str:
    return f"""

Additional output safeguards ({SIMPLE_FIXES_VERSION}):
- Prefer the preinstalled lossless CSV exporter for final output:
  python3 {EXPORT_HELPER_MOUNT} --input {JAIL_OUTPUT}/final_table.json
  Its input is a UTF-8 JSON object with a nonempty string list "columns" and
  a list of equally wide row lists "rows", for example:
  {{"columns": ["code", "objective"], "rows": [["000001", "保留原文句号。"]]}}
  It always writes {JAIL_OUTPUT}/prediction.csv and verifies the CSV read-back.
  Null becomes an empty cell; JSON numbers are read as exact decimals.
  Directly writing a valid CSV to that exact destination is also accepted.
- Extract text using the source field's actual boundaries. Retain terminal
  punctuation belonging to the value. Do not strip periods, convert full-width
  parentheses, remove suffixes, or append guessed punctuation to source values.
- Unless the question explicitly requests rounding, retain source numeric text
  or a round-trip-safe numeric representation. Do not use %.15g or another
  fixed significant-digit truncation merely to clean floating-point tails.
  The exporter cannot restore digits or punctuation lost before it receives data.
- Pass fund codes and other identifiers as strings to preserve leading zeroes.
  Decimal values may also be passed as their original numeric strings; do not
  convert Decimal to float just to serialize JSON. When building JSON from a
  data frame, avoid serializers that silently round numbers. Prefer original
  numeric strings or Python json.dump with round-trip-safe float values.
- The final destination is exactly {JAIL_OUTPUT}/prediction.csv. Do not add a
  task-ID directory or another outputs directory to this path.
"""


def _trace(run_root: Path) -> tuple[int, int, dict[str, int]]:
    return jsonl_trace_summary(
        run_root / "stdout.log",
        turn_events={"turn.completed"},
        action_item_types={
            "command_execution",
            "file_change",
            "mcp_tool_call",
            "web_search",
        },
    )


def _exit_classification(
    run_root: Path, _return_code: int
) -> tuple[str, str | None] | None:
    path = run_root / "stdout.log"
    if not path.is_file():
        return None
    content = path.read_text(encoding="utf-8", errors="replace")
    if (
        "reason: max_output_tokens" in content
        or "reason=max_output_tokens" in content
    ):
        return (
            "model_output_exhausted",
            "Codex model response was truncated by max_output_tokens",
        )
    return None


def build_codex(
    config: HarnessExperimentConfig, harness: HarnessConfig
):
    executable = harness.executable or f"{JAIL_HOME}/.local/node/bin/codex"
    simple_fixes = harness.options.get("simple_fixes", False)
    if not isinstance(simple_fixes, bool):
        raise ValueError("Codex simple_fixes must be a boolean")
    wire_api = str(harness.options.get("wire_api", "responses")).strip().lower()
    if wire_api not in {"chat", "responses"}:
        raise ValueError("Codex wire_api must be 'chat' or 'responses'")
    runtime_binds = configured_runtime_binds(harness)
    execution_policy = {
        "version": "native-context-v2",
        "context_window": config.model.context_window,
        "max_output_tokens": config.model.max_output_tokens,
    }
    auto_compact_limit = (
        config.model.context_window - config.model.max_output_tokens - 8192
    )
    if auto_compact_limit <= 0:
        raise ValueError(
            "Codex context window must exceed max output tokens plus an "
            "8192-token compaction reserve"
        )
    execution_policy["auto_compact_token_limit"] = auto_compact_limit
    simple_fixes_metadata = None
    if simple_fixes:
        runtime_binds += ((EXPORT_HELPER_PATH, EXPORT_HELPER_MOUNT),)
        simple_fixes_metadata = {
            "version": SIMPLE_FIXES_VERSION,
            "export_helper_sha256": hashlib.sha256(
                EXPORT_HELPER_PATH.read_bytes()
            ).hexdigest(),
            "prompt_sha256": hashlib.sha256(
                _simple_fixes_instructions().encode("utf-8")
            ).hexdigest(),
            "prediction_recovery_policy": NESTED_PREDICTION_RECOVERY_POLICY_VERSION,
        }
        execution_policy["simple_fixes"] = simple_fixes_metadata

    def invocation(task, run_root, output_root):
        codex_home = run_root / "runtime" / "home" / ".codex"
        model = json.dumps(config.model.model)
        base_url = json.dumps(config.model.openai_base_url)
        api_key_env = json.dumps(config.model.api_key_env)
        provider_options = f'wire_api = {json.dumps(wire_api)}'
        if wire_api == "responses":
            provider_options += "\nsupports_websockets = false"
        content = f"""model = {model}
model_provider = "vercel"
model_context_window = {config.model.context_window}
model_auto_compact_token_limit = {auto_compact_limit}
approval_policy = "never"
sandbox_mode = "workspace-write"
web_search = "disabled"
check_for_update_on_startup = false
allow_login_shell = false

[model_providers.vercel]
name = "Vercel AI Gateway"
base_url = {base_url}
env_key = {api_key_env}
{provider_options}

[sandbox_workspace_write]
network_access = false
writable_roots = [{json.dumps(JAIL_OUTPUT)}]

[shell_environment_policy]
inherit = "core"
exclude = [{json.dumps(config.model.api_key_env)}, "ANTHROPIC_*", "*TOKEN*", "*SECRET*"]

[features]
apps = false
remote_plugin = false
enable_request_compression = false

[history]
persistence = "none"
"""
        write_text(codex_home / "config.toml", content)
        inner_parts = [
            executable,
            "exec",
        ]
        if wire_api == "responses":
            inner_parts.append("--strict-config")
        inner_parts.append("--json")
        if wire_api == "responses":
            inner_parts.append("--ephemeral")
        inner_parts.append("--skip-git-repo-check")
        if wire_api == "responses":
            inner_parts.append("--ignore-rules")
        inner_parts.extend(
            (
                "--color",
                "never",
                "-C",
                JAIL_OUTPUT,
                "--model",
                config.model.model,
                prompt_for(task)
                + (_simple_fixes_instructions() if simple_fixes else ""),
            )
        )
        inner = tuple(inner_parts)
        environment = clean_environment(config)
        environment["CODEX_HOME"] = f"{JAIL_HOME}/.codex"
        metadata = {
            "web_search": "disabled",
            "command_network": "disabled",
            "command_credential_env": "filtered",
            "request_compression": "disabled",
            "session": "task-local",
            "context_window": config.model.context_window,
            "auto_compact_token_limit": auto_compact_limit,
            "wire_api": "chat_completions" if wire_api == "chat" else "responses",
            "runtime_version": str(harness.options.get("version", "")),
        }
        if simple_fixes_metadata is not None:
            metadata["simple_fixes"] = simple_fixes_metadata
        return jailed_invocation(
            config=config,
            task=task,
            run_root=run_root,
            output_root=output_root,
            inner_command=inner,
            environment=environment,
            metadata=metadata,
            runtime_binds=runtime_binds,
        )

    return native_runner(
        harness_key="codex",
        config=config,
        harness=harness,
        invocation_factory=invocation,
        trace_parser=_trace,
        exit_classifier=_exit_classification,
        execution_policy=execution_policy,
        recover_nested_prediction=simple_fixes,
    )
