# EviWeave — Evidence-Guided Data Agents

Historical `relation-v6 + simple-fixes-v1 + Codex` baseline, prepared by **袁珺 / Jun Yuan** ([yjdy](https://github.com/yjdy)) from DataSpace. Version `0.1.0`; the internal Python package remains `dataspace_baselines`.

[中文说明](README.zh-CN.md) · [Provenance](provenance/README.zh-CN.md)

P0 baseline verification and P1 extraction passed. P2 provides historical runtime migration, a locked workbench build, offline checks and synthetic sandbox execution. P3 behavior regression and P4–P5 experiment attachments/public release remain pending. The formal technical report is deferred.

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

`doctor` checks dependencies without model calls and returns a nonzero status while credentials, Docker, Bubblewrap, Data Workbench 1.0 or Codex CLI 0.145.0 are unavailable. Both `run` and `batch` default to Codex; explicit `--harness codex` is accepted. Other harnesses are rejected. `dataspace-baselines` remains a compatible command. Actual `run`/`batch` execution needs a prepared runtime and can incur inference charges; no such experiments were executed during P0–P2.

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

The read-only baseline verifier is `tools/verify_baseline.py`. It takes historical experiment, existing benchmark and private output directories as three positional arguments. All new audit outputs must stay inside this project; never point output at a source directory. It hashes files in four threads and fails on missing, changed, unparseable or inconsistent evidence. No datasets, predictions, credentials or private audit records are distributed.

MIT licensed, including documentation. See [LICENSE](LICENSE) for retained DataSpace copyright and EviWeave contributions. Source hashes and allowed changes are recorded under `provenance/`. Datasets retain their original distribution terms and are obtained separately.
