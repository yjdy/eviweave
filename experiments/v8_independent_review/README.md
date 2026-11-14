# V8 independent review

Run from the EviWeave checkout. This experiment uses the local V8 public-60
baseline and the saved pre-review candidate checkpoints. The baseline provides
the model configuration and the task manifest. The frozen evaluation bundle is
passed explicitly and verified against `provenance/evaluation-lock.json`.

```bash
python3 -B experiments/v8_independent_review/run.py prepare --evaluation "$EVALUATION_DIR"
python3 -B experiments/v8_independent_review/run.py check --evaluation "$EVALUATION_DIR"
python3 -B experiments/v8_independent_review/run.py run --evaluation "$EVALUATION_DIR"
```

The first two commands work offline. Export `EVIWEAVE_API_KEY` before running
the third. The default output is `.audit/v8-independent-review-public60-recovery/public60`.
Use the same output for subsequent invocations of `run` to resume completed
checkpoints. The existing experimental results directories keep their original
launcher and manifest.

A valid candidate with a missing extraction note gets one 60-second completion
turn in its original session. The candidate CSV is mounted read-only and
verified before handoff. A failed completion suspends the task, records the
reason in `pending.json`, and allows later tasks to proceed. Authentication,
quota exhaustion, and input integrity failures stop dispatch.
