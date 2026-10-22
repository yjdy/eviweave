# EviWeave — Evidence-Guided Data Agents

Historical `relation-v6 + simple-fixes-v1 + Codex` baseline, prepared by **袁珺 / Jun Yuan** ([yjdy](https://github.com/yjdy)) from DataSpace. Version `0.1.0`; the internal Python package remains `dataspace_baselines`.

[中文说明](README.zh-CN.md) · [Provenance](provenance/README.zh-CN.md)

This checkout covers P0 baseline verification and P1 standalone extraction. Full runtime setup, synthetic end-to-end execution, behavior regression, experiment attachments and public release belong to P2–P5. It is not yet a completed public release. The formal technical report is deferred.

## Official evaluation

EviWeave V6 achieved **333/410 correct tasks (81.22% Task Accuracy)** on the official DataSpace benchmark, **tied for first place** with Jitto Build + Claude Opus 5.5 as of **2026-10-22**. See the [official leaderboard](https://dataspace-bench.github.io/#leaderboard).

| Item | Official record |
|---|---|
| Registered method | `codex-controlled + Qwen3.8-Flash` |
| Evaluation date | 2026-10-21 |
| Benchmark | DataSpace, 410 tasks |
| Entry ID | `yjdy-codex-controlled-qwen38-flash` |

This historical entry corresponds to the V6 baseline released here: Codex CLI 0.145.0, `relation-v6` and `simple-fixes-v1`. Its registered name predates EviWeave; the later EviWeave Controlled implementation is outside this package. The submission retained all **414 attempts**, including one operator-triggered recovery attempt for each of tasks **22, 23, 207 and 273**. Those four use `run_2`; the other 406 use `run_1`, without selection by score.

The separate local public subset scored **44/60 initially and 45/60 after recovery**. See [provenance and selection details](provenance/README.zh-CN.md). The leaderboard was rechecked on 2026-10-22; these are historical submission results, with no new inference or benchmark rerun during packaging.

## Installation and checks

Python 3.10+ is required. The only runtime Python dependency is `PyYAML>=6,<7`. Install from the project root (or install the wheel generated in `dist/`):

```sh
python3 -m venv .venv
.venv/bin/python -m pip install .
.venv/bin/eviweave --version
.venv/bin/eviweave list
.venv/bin/eviweave run --help
.venv/bin/eviweave batch --help
```

If the system lacks `ensurepip`, an existing `uv` installation can create the environment with `uv venv .venv` and install with `uv pip install --python .venv/bin/python .`.

Use `configs/codex.example.yaml` as a template. The endpoint is a non-working placeholder; provide your own Responses-compatible endpoint and set `EVIWEAVE_API_KEY` in the environment. Keep credentials out of YAML. Host runtime/workbench paths resolve relative to the configuration file, including paths containing spaces and `{version}` in the Codex runtime path. `anthropic_base_url` is optional and unused by Codex.

```sh
.venv/bin/eviweave doctor --config configs/codex.example.yaml
```

`doctor` checks dependencies without model calls and returns a nonzero status while credentials, Docker, Bubblewrap, Data Workbench 1.0 or Codex CLI 0.145.0 are unavailable. Runtime installation instructions and a synthetic task will be completed in P2. Both `run` and `batch` default to Codex; explicit `--harness codex` is accepted. Other harnesses are rejected. `dataspace-baselines` remains a compatible command. Actual `run`/`batch` execution needs a prepared runtime and can incur inference charges; no such experiments were executed during P0–P1.

For local offline checks, install build tools in a local environment and use:

```sh
mkdir -p .audit/tmp
TMPDIR="$PWD/.audit/tmp" .venv/bin/python -m unittest discover -s tests -v
TMPDIR="$PWD/.audit/tmp" .venv/bin/python -m build
```

The read-only baseline verifier is `tools/verify_baseline.py`. It takes historical experiment, existing benchmark and private output directories as three positional arguments. All new audit outputs must stay inside this project; never point output at a source directory. It hashes files in four threads and fails on missing, changed, unparseable or inconsistent evidence. No datasets, predictions, credentials or private audit records are distributed.

MIT licensed, including documentation. See [LICENSE](LICENSE) for retained DataSpace copyright and EviWeave contributions. Source hashes and allowed changes are recorded under `provenance/`. Datasets retain their original distribution terms and are obtained separately.
