# V8 default prompt (R2)

Author: 袁珺 / Jun Yuan. MIT; retained upstream notices apply.

V8 now uses the verified R2 calculation and pre-export review instructions by
default. The public entry remains `scripts/experiment.sh` (`tools/experiment.py`):
select `--version v8` on `check` or a new `run`. There is no `--v8-review` option
or separate R2 runner. V6 retains its original prompt.

```sh
bash scripts/experiment.sh check --version v8 \
  --config "$EVIWEAVE_CONFIG" --dataset "$EVIWEAVE_DATASET" \
  --evaluation-root "$EVIWEAVE_EVALUATION_ROOT" \
  --environment-profile "$EVIWEAVE_ENVIRONMENT_PROFILE" --scope public60
```

For an authorized new run, use `run` with the same arguments and a fresh
project-local `--output`. The unified configuration, task selection, repeat,
profile, budget and credential rules remain in effect. This documentation does
not authorize model calls. The check performs local validation only and retains
the unified credential-presence status semantics.

Use `run --resume --output PATH` for a run created with the same source and
runtime identity. Prompt bytes and source hashes remain part of that identity.
Historical runs from the legacy or opt-in R2 checkout must use their original
checkout for resume; this change does not migrate or relabel their identities.
The common `verify --experiment PATH` can still read historical reports offline.

The common scheduler, predictions, attempt history, frozen scoring and output
files (`summary.json`, `summary.csv`, `score.json`, `report-set.json`) are unchanged.
Temporary experiment launchers and data-processing scripts remain excluded.

`tools/v8_semantic_review.py` is now a byte-for-byte copy of the previously
verified `tools/v8_semantic_review_r2.py` at source commit
`fe5da2dfcde32a4f8ac09c2913729f9e2f6584dc`, SHA-256
`c756d5cc302af3aac6a9d8f92de2ce7bf66448452c1842d6831f4f581b352d6f`.
The duplicate R2 file is retired. Per-file predecessor and delivered digests,
including the retirement, are in `provenance/v8-r2-default-baseline.json`.
Earlier provenance tables and Git commits retain their original bytes and meaning.
