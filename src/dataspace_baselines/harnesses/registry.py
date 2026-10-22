from __future__ import annotations

from ..config import HarnessExperimentConfig
from ..core.interfaces import HarnessRunner
from .codex import build_codex


def available_harnesses() -> tuple[str, ...]:
    return ("codex",)


def build_harness(
    harness_key: str, config: HarnessExperimentConfig
) -> HarnessRunner:
    if harness_key != "codex":
        raise KeyError(f"unsupported harness: {harness_key}; only codex is supported")
    if harness_key not in config.harnesses:
        raise KeyError("harness 'codex' is not configured")
    harness = config.harnesses[harness_key]
    if not harness.enabled:
        raise ValueError("harness 'codex' is disabled")
    return build_codex(config, harness)
