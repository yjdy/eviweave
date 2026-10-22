# EviWeave — Evidence-Guided Data Agents

Historical `relation-v6 + simple-fixes-v1 + Codex` baseline, prepared by **袁珺 / Jun Yuan** ([yjdy](https://github.com/yjdy)) from DataSpace. Version `0.1.0`; the internal Python package remains `dataspace_baselines`.

[中文说明](README.zh-CN.md) · [Provenance](provenance/README.zh-CN.md)

P0 baseline verification and P1 extraction passed. P2 provides historical runtime migration, a locked workbench build, offline checks and synthetic sandbox execution. P3 adds offline behavior regression, frozen-reference comparison and historical sandbox checks; P4 provides local experiment-attachment preparation, selected-trace redaction and frozen offline scoring; public release remains P5 work. The formal technical report is deferred.

## Independent configuration and resources

Use the historical V6 Responses endpoint and `qwen3.8-flash` model with an EviWeave-dedicated API key. **Users must set `EVIWEAVE_API_KEY` themselves**; never store it in YAML or borrow DataSpace's credential file. The standalone wrapper accepts only that key variable, with no provider-key fallback. The public offline template keeps its invalid placeholder endpoint; the actual endpoint belongs in a private configuration.

Obtain the dataset separately from the [official Hugging Face release](https://huggingface.co/datasets/HKUSTDial/DataSpace), following the [upstream download instructions](https://github.com/HKUSTDial/DataSpace#dataset). Use a project-local optional downloader and cache, pin the chosen full repository revision, check the supplied SHA-256, and extract locally:

```sh
mkdir -p .local/download .audit/tmp .audit/cache
export TMPDIR="$PWD/.audit/tmp"
export PIP_CACHE_DIR="$PWD/.audit/cache/pip"
export HF_HOME="$PWD/.local/hf-cache"
python3 -m venv .local/download-env
.local/download-env/bin/python -m pip install huggingface_hub
export DATA_REVISION='<full Hugging Face dataset commit ID>'
.local/download-env/bin/hf download HKUSTDial/DataSpace \
  release/DataSpace-Benchmark.zip release/DataSpace-Benchmark.zip.sha256 \
  --repo-type dataset --revision "$DATA_REVISION" --local-dir "$PWD/.local/download"
(cd .local/download/release && sha256sum --check DataSpace-Benchmark.zip.sha256)
unzip .local/download/release/DataSpace-Benchmark.zip -d data
```

The dataset root must contain `input/task_N/task.json` and `context/`. There is no neighbouring-directory discovery or fallback. The current private settings explicitly use the project-local `data/DataSpace-Benchmark/`, with no reference to the neighbouring DataSpace project. The `data/` directory is excluded from Git and Python distributions. Reconfigure to a fresh output directory to change the dataset location:

```sh
export EVIWEAVE_DATASET_ROOT="$PWD/data/DataSpace-Benchmark"
export EVIWEAVE_BASE_URL='<the V6 Responses endpoint>'
export EVIWEAVE_API_KEY='<your EviWeave-only API key>'
.venv/bin/python -B tools/standalone.py configure \
  --dataset-root "$EVIWEAVE_DATASET_ROOT" --base-url "$EVIWEAVE_BASE_URL" \
  --model qwen3.8-flash --output-root .local/config
# This command invokes a real model; run it only for an intended experiment:
.venv/bin/python -B tools/standalone.py run --settings .local/config/settings.json \
  --task 33 --output-root .audit/independent-task33
```

The local frozen evaluator, 60 configs and 60 references are managed separately in `.local/evaluation-v6-public60/`, locked by [121 per-file hashes](provenance/evaluation-lock.json). Necessary historical evidence is an independent copy of the accepted P4 archive in `.local/evidence-v6/`. These resources are private and excluded from Git and both Python distributions. On a new machine, explicitly import matching frozen material; do not substitute a newer evaluator:

```sh
python3 -B tools/standalone.py import-evaluation \
  --source /path/to/frozen-evaluation --output-root .local/evaluation-v6-public60
python3 -B tools/standalone.py verify-evaluation --evaluation-root .local/evaluation-v6-public60
python3 -B tools/standalone.py score --evaluation-root .local/evaluation-v6-public60 \
  --prediction-root /path/to/predictions --output-root .audit/independent-score
```

The import source contains `evaluate.py`, `configs/` and `output/`; every filename and hash must match. Scoring strips credentials and user Python paths, executes a local evaluator copy, and has a 300-second outer timeout. Add `--task 33` to score only that public task. Use Python 3.12.3 for formal historical revalidation. Successful evaluator execution does not mean all answers passed.

With build/pip installed, independent runtimes prepared and the P4 archive available:

```sh
python3 -B tools/verify_isolation.py --block-path /absolute/path/to/DataSpace \
  --evaluation-root .local/evaluation-v6-public60 \
  --archive .local/evidence-v6/eviweave-v0.1.0-v6-evidence.zip \
  --config configs/codex.example.yaml --output-root .audit/isolation-check
```

This masks the specified directory with an unreadable filesystem in new mount/PID namespaces, without changing the real source. It builds and installs independently, tests, runs offline doctor and synthetic sandbox tasks, checks lossless export, and scores synthetic predictions and historical evidence. No real credentials or model calls are used. Live authentication and independently acquired real inputs require separate acceptance. The tools/tests ship in the source archive; the wheel retains the same 22 business modules and PyYAML dependency. See [isolation provenance and boundaries](provenance/isolation.zh-CN.md).

For three serial task-33 trials, run `./scripts/check_task33.sh` in a terminal with your dedicated `EVIWEAVE_API_KEY` exported. The optional `--check-only` checks key visibility, inputs and local runtime without contacting a model; it cannot authenticate the key. Defaults are `.local/config-v6/settings.json`, `.local/evaluation-v6-public60/` and `.venv/bin/python`; override the interpreter with `EVIWEAVE_PYTHON` if needed. Verified source is used directly, so PyYAML is required but a wheel installation is not. Every trial has a new HOME/session and the unchanged V6 30-minute limit. Three trials can take 90 minutes plus checking/scoring and incur inference charges. Infrastructure/authentication/missing-output failures stop; wrong answers continue through the three planned trials. Ctrl+C cancels the active task.

Each invocation writes a fresh private `.audit/task33-three-runs-*/` with logs, original predictions, recorded usage, frozen single-task scores and `summary.json`/`summary.csv`. Inputs/config/source/evaluation hashes are checked before and after trials. Key values are not deliberately printed or saved, and previous predictions/gold/feedback are never given to the model. The summary distinguishes readable credentials from recorded completed model turns and compares pass counts, row counts and CSV SHA-256. Exit 0 means all trials were executed/scored (or check-only passed), even if answers failed. Three trials can reveal output differences but cannot establish purely random causation or an immutable backend model identity.

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
mkdir -p .audit/tmp .audit/cache
export TMPDIR="$PWD/.audit/tmp"
export PIP_CACHE_DIR="$PWD/.audit/cache/pip"
python3 -m venv .venv
.venv/bin/python -m pip install .
.venv/bin/eviweave --version
.venv/bin/eviweave list
.venv/bin/eviweave run --help
.venv/bin/eviweave batch --help
```

If the system lacks `ensurepip`, an existing `uv` installation can create the environment with `UV_CACHE_DIR="$PWD/.audit/cache/uv" uv --offline venv --python python3 .venv` and install with `UV_CACHE_DIR="$PWD/.audit/cache/uv" uv pip install --python .venv/bin/python .`.

Use `configs/codex.example.yaml` as a template. The endpoint is a non-working placeholder; provide your own Responses-compatible endpoint and set `EVIWEAVE_API_KEY` in the environment. Keep credentials out of YAML. Host runtime/workbench paths resolve relative to the configuration file, including paths containing spaces and `{version}` in the Codex runtime path. `anthropic_base_url` is optional and unused by Codex.

```sh
.venv/bin/eviweave doctor --config configs/codex.example.yaml
```

`doctor` checks dependencies without model calls and returns a nonzero status while credentials, Docker, Bubblewrap, Data Workbench 1.0 or Codex CLI 0.145.0 are unavailable. Both `run` and `batch` default to Codex; explicit `--harness codex` is accepted. Other harnesses are rejected. `dataspace-baselines` remains a compatible command. Actual `run`/`batch` execution needs a prepared runtime and can incur inference charges; no such experiments were executed during P0–P3.

## Runtime preparation and offline diagnostic

Linux x86_64, GNU `cp`/`tar`, Docker, Bubblewrap and Python are required. Keep the repository or source archive: preparation scripts and synthetic fixtures are in the source distribution; the wheel contains the Python package.

```sh
scripts/install_codex_runtime.sh --help
scripts/install_codex_runtime.sh
python3 -B scripts/project_docker.py start
scripts/prepare_workbench.sh build --docker-host "unix://$PWD/.runtimes/build/docker.sock"
scripts/prepare_workbench.sh export --docker-host "unix://$PWD/.runtimes/build/docker.sock" \
  --runtime-root .runtimes/data-workbench-rebuilt
```

The Codex installer downloads only version `0.145.0` and checks npm SHA-512 integrity before extraction. It also accepts `--from-dir` with `--manifest`, or `--from-archive` with `--sha256`. All output/cache/temp paths default to this project and may be customized within it. A second verified import reuses the installation; content conflicts fail without replacement. Only `codex --version` is executed by preparation.

The independent Docker and containerd instances use project-local storage and require existing `sudo -n` access. Build uses the historical base-image digest and all recorded Python/system package versions; unavailable pinned packages fail rather than upgrade silently. The rebuilt image is `eviweave-workbench:p2-rebuilt`. For that export, set the configuration's workbench image and rootfs accordingly, and select the local daemon with `DOCKER_HOST="unix://$PWD/.runtimes/build/docker.sock"` when running doctor. Stop it after use with `python3 -B scripts/project_docker.py stop`. Never build against the system daemon with these scripts.

Historical runtime import is separate from rebuilding. Given a separately obtained runtime directory and a trusted inventory, use `prepare_workbench.sh import --from-dir ... --manifest ...`. A portable archive requires `--from-archive`, `--sha256` and `--manifest`. Generate a current inventory with `python3 -B scripts/runtime_support.py --root ... --output .audit/runtime-files.json`; such a new snapshot does not authenticate history. `prepare_workbench.sh verify` checks every entry. Historical binaries/rootfs are not shipped and have no public download attachment yet. See [runtime provenance](provenance/runtime-changes.zh-CN.md) for identity and rebuild differences.

With the example configuration pointing to verified historical runtime copies:

```sh
.venv/bin/eviweave doctor --offline --config configs/codex.example.yaml
.venv/bin/python -B tools/smoke_runtime.py --config configs/codex.example.yaml \
  --output-root .audit/synthetic-smoke
```

Use a fresh output directory. Offline doctor prints `[skip]` for credentials and fails if any required local check fails; default doctor still fails when the configured key is missing. Neither mode authenticates a service or sends inference requests. Actual package versions, file inventories, OCR English/Chinese data, read-only input/runtime/exporter mounts and writable output are checked.

The diagnostic accepts only the placeholder endpoint, passes an invalid test key, disables network in an extra namespace, and executes a deterministic local Python process through the original Bubblewrap/runner/exporter/batch chain. It verifies single-task output, both batch predictions, exporter byte hashes and resume without extra attempts. Expected CSVs are never mounted in the sandbox. These checks establish offline execution, not real Codex inference or a reproduced benchmark score.

For local offline checks, install build tools in a local environment and use:

```sh
mkdir -p .audit/tmp
TMPDIR="$PWD/.audit/tmp" .venv/bin/python -m unittest discover -s tests -v
TMPDIR="$PWD/.audit/tmp" .venv/bin/python -m build
```

## Offline regression

From a checkout or source distribution with the package installed:

```sh
.venv/bin/python -B tools/run_regression.py --suite unit --output-root .audit/unit
.venv/bin/python -B tools/run_regression.py --suite all --output-root .audit/regression \
  --frozen-source /path/to/frozen_src --config configs/codex.example.yaml
```

Each output directory must be fresh. `unit` needs no historical source/runtime. `reference` requires the explicitly supplied frozen source; `runtime` requires verified historical runtime copies and the placeholder endpoint. Missing required material fails. The reference comparison runs separate processes and checks independent synthetic expectations. See [regression provenance and limits](provenance/regression.zh-CN.md).

The original outer jail is tested without an added network namespace; no external connection is attempted. A separate fixed-CLI config/read diagnostic compares initial and trusted configuration without inference, while the P2 smoke adds network isolation. Real Codex inner command execution remains untested. The lightweight CI matrix covers Python 3.10/3.12/3.13; hosted execution remains pending until publication. To run its local equivalent, install `build` and `pip` in a project-local environment, then run `python -B tools/ci_check.py --output-root .audit/ci-result`.

The read-only baseline verifier is `tools/verify_baseline.py`. It takes historical experiment, existing benchmark and private output directories as three positional arguments. All new audit outputs must stay inside this project; never point output at a source directory. It hashes files in four threads and fails on missing, changed, unparseable or inconsistent evidence. No datasets, predictions, credentials or private audit records are distributed.

MIT licensed, including documentation. See [LICENSE](LICENSE) for retained DataSpace copyright and EviWeave contributions. Source hashes and allowed changes are recorded under `provenance/`. Datasets retain their original distribution terms and are obtained separately.

## Historical experiment attachment

From a checkout or source distribution, use Python 3.12.3 for the formal frozen offline score:

```sh
python3 -B tools/package_evidence.py build \
  --experiment-root /path/to/historical-experiment \
  --baseline-report /path/to/private-P0-result.json \
  --official-evidence-root /path/to/verified-official-snapshot \
  --output-root .audit/evidence-build
python3 -B tools/package_evidence.py verify \
  --archive .audit/evidence-build/eviweave-v0.1.0-v6-evidence.zip \
  --output-root .audit/evidence-check
```

Use fresh project-local output directories. The attachment contains 410 unchanged predictions, 414 attempt summaries and **selected traces** for tasks 33, 328, 35, 391 and 23 (both failure and recovery for 23). Complete original traces remain private. A copied frozen evaluator scores existing predictions only: 45/60; the first-pass 44/60 is a historical record. No new inference or benchmark task execution occurs. The tool performs independent residual scanning and rejects incomplete or changed evidence. Verify uses only the standard library and can run without the historical source, installed package or runtime. Source inputs and the attachment are separate from Git/wheel/sdist. No attachment has been uploaded; publication remains P5. See [evidence provenance and limits](provenance/evidence.zh-CN.md).
