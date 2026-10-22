# Synthetic offline fixtures

These two tasks are handwritten for EviWeave and contain no DataSpace records or historical predictions. `task_1` selects records by `rules.groups`, orders identifiers as strings, and exports `id,note,value`; `task_2` selects no records and exports only the same header. Both use source text without numeric conversion. `expected/` is used only by the host validator and is never mounted into the task sandbox.

Run `tools/smoke_runtime.py` from an installed EviWeave environment with a prepared workbench and the placeholder endpoint configuration. It wraps the original Codex invocation factory only within that diagnostic process, binds `tools/synthetic_executor.py`, adds `--unshare-net`, and replaces the final executable with workbench Python. Production `run`/`batch` and the frozen adapters are unchanged. Run metadata and the resume identity explicitly identify `synthetic/mock`; model usage remains absent and there are zero model calls.
