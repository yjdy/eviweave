# Unified V8 review selection

Author: 袁珺 / Jun Yuan. MIT; retained upstream notices apply.

The public entry remains `scripts/experiment.sh` (`tools/experiment.py`).
Select the R2 semantic instructions with `--version v8 --v8-review r2` on
`check` or a new `run`. Omitting the review option retains the legacy V8 prompt.
V6 rejects this option. Configuration, task selection, repeat and environment
profile use the existing unified arguments; no model, dataset or production
budget is hard-coded by the R2 selection.

```sh
bash scripts/experiment.sh check --version v8 --v8-review r2 \
  --config "$EVIWEAVE_CONFIG" --dataset "$EVIWEAVE_DATASET" \
  --evaluation-root "$EVIWEAVE_EVALUATION_ROOT" \
  --environment-profile "$EVIWEAVE_ENVIRONMENT_PROFILE" --scope public60
```

For an authorized new run, use `run` with those same arguments and a fresh
project-local `--output`. Runtime setup and service authorization remain separate;
this documentation does not authorize model calls. The check performs local
validation only and retains the unified credential-presence status semantics.

Resume uses `run --resume --output PATH`; the saved review selection is restored,
and an explicitly conflicting `--v8-review` is rejected before dispatch. Source,
runtime, environment and input identities remain mandatory. Old experiments keep
their original source and runtime; use the original checkout to resume them when
its recorded source differs. No historical identity is rewritten or reauthorized.

The common scheduler and verifier retain attempt history, predictions, frozen
scoring, `summary.json`, `summary.csv`, `score.json` and `report-set.json`.
Use the existing `verify --experiment PATH` command for offline reports. R2 is
recorded in the experiment identity and the runner fingerprint; it does not
introduce another output schema or result conversion step.

The copied `tools/v8_semantic_review_r2.py` has SHA-256
`c756d5cc302af3aac6a9d8f92de2ce7bf66448452c1842d6831f4f581b352d6f`,
identical to source commit `33710f5be2c534281464ab87558501e258572fa3`.
The experiment-specific public60 wrapper `tools/experiment_v8_r2.py`, Terra
launchers and data-processing/aggregation scripts are not part of this integration.
The reusable offline client-protocol diagnostic `tools/codex_upgrade_probe.py`
remains a validation tool, not a business experiment launcher.

Per-file predecessor, upstream and delivered digests are recorded in
`provenance/v8-upgrade-integration-baseline.json`. Prior provenance tables are
retained byte for byte; this new layer replaces the imported upgrade table for
this integration only, without changing its source-branch original.
