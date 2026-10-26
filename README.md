# EviWeave

**Data agents that find evidence in local workspaces, perform analysis, and deliver verifiable results.**

[简体中文](README.zh-CN.md) · [Experiments](EXPERIMENTS.md) · [Download](https://github.com/yjdy/eviweave/releases/tag/v0.1.0)

## 1. About the project

Practical data analysis often combines tables, databases, long documents, and multimedia. EviWeave organizes these materials into independent tasks. An agent inspects evidence, performs analysis in an isolated workbench, and returns a complete CSV result.

The project provides:

- **Evidence-guided analysis**: discover, extract, join, and compute over task-local materials.
- **Isolated execution**: a fixed tool workbench and sandbox limit accessible files and the execution environment.
- **Experiment management**: single-task and batch runs, interruption recovery, and prediction collection.
- **Offline verification**: check prediction integrity and evaluate saved answers against frozen scoring materials.
- **Offline cost analysis**: import usage or billing evidence, then calculate and reconcile costs using reviewed pricing.

### DataSpace results

The first release publishes the historical V6 method and reproduction materials. Its official DataSpace leaderboard name is **codex-controlled + Qwen3.8-Flash**.

| Result | Score | Meaning |
|---|---:|---|
| Historical official full-benchmark evaluation | **333/410, 81.22%** | Evaluated on 2026-10-21; tied first when checked on 2026-10-24 |
| Historical public-set first pass | **44/60** | Preserved initial run record |
| Historical public set after fixed recovery | **45/60** | Offline scoring of the fixed historical prediction selection |

See the [DataSpace leaderboard](https://dataspace-bench.github.io/#leaderboard) for official results. Rankings change; the table records a dated historical observation.

**v0.1.0 was released on 2026-10-24.** It freezes the historical `relation-v6 + simple-fixes-v1` baseline with Codex **0.145.0**, **Qwen3.8-Flash**, and the Responses protocol. EviWeave Controlled will be developed separately and is outside this release. The historical leaderboard name and the future Controlled implementation are recorded separately.

Public materials allow verification of historical predictions and the public-set score. Calling the model again creates a new experiment. No immutable service snapshot of the historical model was saved, so a new run is not guaranteed to reproduce the same answers or achieve 81.22% again.

### This guide and the released version

This guide describes the **v0.1.1 maintenance candidate**. Its source checkout and source distribution include the three preparation scripts, outer daemon fixes, current-source CI and updated documentation. The wheel supplies the business package and both CLI aliases. Publication status and dates are authoritative in the online [Releases](https://github.com/yjdy/eviweave/releases). The [original v0.1.0 Release](https://github.com/yjdy/eviweave/releases/tag/v0.1.0), tag, packages and provenance retain their original bytes; the three scripts are absent from those original packages. Historical scoring continues to download the original v0.1.0 evidence. See [maintenance scope and provenance](provenance/v0.1.1.md).

Main pushes and pull requests to main run lightweight checks of their event commit: package-scope tests, ordinary installation, sdist-to-wheel builds, installed tests, provenance and bilingual documentation. The package matrix uses CPython **3.10.22, 3.12.3, 3.13.16**; the seven host-profile cases do not apply to 3.10 and remain mandatory on 3.12/3.13. Manually run the preparation workflow with `full=true` for serial bilingual workbench, historical-score and synthetic-recovery acceptance after the package matrix passes. The default is `full=false`; a lightweight pass does not certify full runtime acceptance.

## 2. Installation and preparation

Use the **v0.1.1 candidate source checkout or source distribution** for the scripts below. Install the wheel when only the business package and `eviweave` / `dataspace-baselines` CLI aliases are needed; scripts, resource locks and verification tools require the source distribution. Until the candidate is published, obtain it from the reviewed candidate branch and cloud artifacts, not from an assumed release URL. To reproduce **v0.1.0**, use its original source and [original preparation tools](provenance/public-preparation.md). Released downloads are listed on [GitHub Releases](https://github.com/yjdy/eviweave/releases); this project has not been published to PyPI.

### 2.1 Environment preparation

The full workflow requires:

| Dependency | Requirement |
|---|---|
| Operating system | Linux x86_64 |
| Python | CPython 3.12 or 3.13 for the full environment |
| Host tools | Docker/dockerd, containerd, Bubblewrap |
| Permissions | Required namespace and mount capabilities, plus passwordless permission for the project-local daemon launcher |
| Model service | A compatible HTTPS Responses endpoint and dedicated API key for real experiments |

Cloud acceptance covered the full applicable checks on CPython **3.12.3 and 3.13.16**, and applicable package checks on **3.10.22**. Python 3.10 results do not establish support for the full workbench workflow. macOS, Windows, and ARM have not been accepted.

Prepare the environment in four steps:

1. Obtain the source containing the new scripts; use the intended CPython 3.12 or 3.13 as `python3` below.
2. Have a developer or administrator install the host dependencies above.
3. Check static prerequisites, then prepare the workbench, register a profile, and install:

```sh
./scripts/prepare_environment.sh --python python3 --check-only
```

```sh
./scripts/prepare_environment.sh --python python3
```

4. On success, inspect the reported `.audit/preparation/.../preparation-report.json`, then explicitly select the profile and model configuration as described in section 2.3.

The v0.1.1 source provides these preparation entries; the workflow records the actual scope of acceptance. `--check-only` neither connects to the network nor writes files, and does not certify passwordless privileges or sandbox capability. Actual preparation checks those capabilities.

Defaults are `.venv-experiment`, `.local/profiles/public-host.json`, and `.local/config-v6/offline-public-host.yaml`, with Codex, workbench, and independent daemon storage under `.runtimes/prepared/`. Preparation runs only the offline doctor; it does not download scoring materials or the complete dataset.

Existing targets require explicit `--reuse`. Conflicts or corruption are rejected without replacing environments. Select independent directories with `--venv`, `--profile-name`, `--runtime-root`, and `--cache`; see `--help`. Existing `.local/config-v6/codex.yaml` is preserved. Offline configuration is reused only when unchanged since its script-recorded checksum.

The script does not install system tools, activate profiles, edit `.local/env.sh`, or call a model. A profile records this rebuilt environment, not the original historical experiment host. Downloads, HOME, caches, temporary files, and Docker/containerd storage remain inside the project, without the system Docker socket.

The initial check requires at least 8GiB free; peak usage may be higher. The script stops daemons it started and preserves previously running instances. For later image checks, restart with the same storage as described in the [script reference](provenance/preparation-scripts.md). After preparing the environment and scoring files, follow that reference for synthetic checks without model calls.

### 2.2 Data preparation

Prepare DataSpace task data and scoring materials separately:

| Material | Purpose | Needed for |
|---|---|---|
| 121 frozen scoring files | Evaluate saved predictions | Offline scoring |
| Complete task dataset | Supply questions and evidence to the agent | New model runs |
| Historical evidence archive | Verify released predictions and historical results | Historical result verification |

The complete task ZIP is about **2.88GB**, with about **15.01GB** of extracted content, before workbench and output storage. Verifying historical results requires neither the complete task dataset nor a model execution workbench.

The default prepares only scoring materials. Start with static preflight:

```sh
./scripts/prepare_dataspace.sh --python python3 --check-only
```

```sh
./scripts/prepare_dataspace.sh --python python3
```

Explicitly select complete data only for new model experiments:

```sh
./scripts/prepare_dataspace.sh --python python3 --mode full
```

**Acceptance of the new full-data wrapper is limited to artificial small archives; this work does not download the approximately 15GB complete dataset again.** `--reuse` explicitly verifies existing targets. Select scoring, data, and cache directories with `--evaluation-root`, `--dataset`, and `--cache`. Complete-data reuse requires the retained, checksum-matching original ZIP in the cache for per-file comparison; a missing ZIP does not trigger an implicit download.

Recommended directory conventions:

| Directory | Contents |
|---|---|
| `data/DataSpace-Benchmark/` | Complete task dataset, containing `input/` |
| `.local/evaluation-v6-public60/` | Frozen scoring materials |
| `.local/evidence-v6/` | Downloaded historical evidence assets |
| `.local/profiles/` | Host environment profiles |
| `.local/config-v6/` | Private execution configuration |
| `.audit/` | Check records, experiment outputs, and reports |

Existing data can be reused after verification. Stop on a checksum mismatch or conflicting target and preserve the original material. Evaluator and dataset revisions are pinned in the [public resource lock](provenance/public-resources-lock.json); all 121 files are also checked against the [evaluation lock](provenance/evaluation-lock.json). Keep reference answers out of the solving workspace.

### 2.3 Model access configuration

Offline verification and synthetic tests do not require an API key.

Before running a real experiment:

1. Set a compatible HTTPS Responses base address in `experiment.model.openai_base_url` in `.local/config-v6/codex.yaml`.
2. Set the dedicated `EVIWEAVE_API_KEY` in your terminal.
3. Review `.local/env.sh` and select the prepared host profile and data paths. Existing private configuration is preserved by design; runtime paths must match the selected profile.

The unified entry uses these settings:

| Setting | Purpose / convention in this guide |
|---|---|
| `EVIWEAVE_PYTHON` | Project-local `.venv-experiment/bin/python` |
| `EVIWEAVE_ENVIRONMENT_PROFILE` | A collected and checked profile under `.local/profiles/` |
| `EVIWEAVE_CONFIG` | `.local/config-v6/codex.yaml` |
| `EVIWEAVE_DATASET` | `data/DataSpace-Benchmark/`, the parent of `input/` |
| `EVIWEAVE_EVALUATION_ROOT` | `.local/evaluation-v6-public60/` |
| `EVIWEAVE_API_KEY` | Dedicated key; blank private-template values can inherit the terminal value |

Prefer absolute paths inside the project for path settings. Nonempty `.local/env.sh` values override inherited environment variables; explicit CLI arguments take precedence. The installation bootstrap interpreter is selected separately with `EVIWEAVE_BOOTSTRAP_PYTHON`.

Do not put the API key in model YAML, task materials, or public logs. The project uses only the dedicated `EVIWEAVE_API_KEY` and does not search other projects for credentials. The service address must not embed credentials, a query, or a fragment.

For V6 reproduction, retain the frozen model name, prompts, 32,768 output tokens, 983,616 context tokens, 1,800 seconds per task, and concurrency 1. See the [preparation reference](provenance/public-preparation.md) for environment and resource identities.

<a id="unified-experiment-workflow"></a>

## 3. Reproducing experiments with DataSpace

Verify historical materials first, then decide whether to pay for a new experiment. Run from the source root; real-experiment commands below assume `.local/env.sh` has been configured as above.

### 3.1 Verify historical results offline

Download these assets from the [v0.1.0 Release](https://github.com/yjdy/eviweave/releases/tag/v0.1.0):

- `eviweave-v0.1.0-v6-evidence.zip`
- `eviweave-v0.1.0-v6-evidence.zip.sha256`

Place both under `.local/evidence-v6/`. The archive contains **410 predictions, 414 attempt summaries, a fixed selection record, and selected redacted traces for 5 tasks and 6 attempts**.

Historical scoring requires CPython 3.12.3. If `python3` selects another version, first set the Shell variable `HISTORY_PYTHON` to an already prepared 3.12.3 interpreter's absolute path; the script does not install it.

Explicitly allow downloads from the fixed public Release, then verify and score:

```sh
./scripts/verify_history.sh --python "${HISTORY_PYTHON:-python3}" --download
```

Omit `--download` for existing assets; the default is fully offline. Select inputs and a fresh output using `--archive`, `--evaluation-root`, and `--output`, or first run read-only static preflight with `--check-only`. Missing ZIP/checksum files, mismatching digests, or different per-task scoring results fail.

The script requires only source tools, the standard library, and the prepared 121 scoring files, without installing the business package, full workbench, or task dataset. Its report records the original archive SHA-256 and per-task match. Execution records are available in the preparation workflow.

Expected results:

- **45/60** on the public set.
- 15 tasks not passed.
- The remaining 350 tasks have no public references and are not scored locally.

This verifies the historical public-set result. The full **333/410** comes from official evaluation and requires private scoring materials held by the organizers; it cannot be recomputed locally using public data alone.

Historical tasks **22, 23, 207, and 273** each received one recovery attempt after missing output, with the second attempt selected by the fixed policy. All other tasks use their first attempt. The historical **44/60** first pass remains separate; predictions are not reselected using scores.

### 3.2 Check the environment, then run a new experiment

The synthetic check after preparation uses a local executor without model calls. It checks the sandbox, CSV output, collection, and recovery. Its 2/2 score belongs to artificial examples.

Before a real run, check a single task's prerequisites:

```sh
./scripts/experiment.sh check --tasks 33
```

Success establishes local prerequisites only: **service authentication is not tested, and model calls remain zero**. Production checks require a key and valid endpoint; a missing key returns `MISSING_KEY`. Use the offline doctor for checks without a key.

After reviewing configuration and budget, run one task:

```sh
./scripts/experiment.sh run --tasks 33 --output .audit/task33-v6
```

Run the 60 public-reference tasks:

```sh
./scripts/experiment.sh run --scope public60 --output .audit/public60-v6
```

To prepare predictions for full official evaluation, explicitly select all 410 tasks with a separate output:

```sh
./scripts/experiment.sh run --scope all --output .audit/all410-v6
```

**These `run` commands call a real model and incur costs.** Omitting the task or scope selects public60 by default, with `--repeat 1`. Always select the scope explicitly and start with a single-task check; do not test installation with an unscoped run.

Report new runs separately with their model, environment, date, task scope, and score. Do not combine them with the historical 333/410 or 45/60.

### 3.3 Interruption and recovery

Resume an interrupted experiment in its original directory:

```sh
./scripts/experiment.sh run --output .audit/public60-v6 --resume --yes
```

Resume restores the saved tasks, configuration, and environment identity, completing unfinished or failed executions. Valid submissions with incorrect answers are skipped. Resuming an already completed experiment should create zero new attempts. Resume rejects explicit replacement of frozen settings such as the environment profile. Each newly allocated attempt becomes that task's current selection, even if it fails.

Do not improve reported scores by editing old configuration, replacing predictions, or choosing the best attempt. Ctrl+C returns 130. A final 401/402/403 stops the batch and cancels in-flight work after the current task's failure is confirmed. Fix access before deliberately resuming. A 429/500 is handled as an ordinary execution failure.

### 3.4 Verify and organize results

Verify a new experiment offline:

```sh
./scripts/experiment.sh verify --experiment .audit/public60-v6 \
  --output .audit/public60-v6-verification
```

Verification checks experiment identity, prediction integrity, and public-task scores, producing `summary.json`, `score.json`, and `summary.csv`. Verification of an existing unified experiment needs no key, original data, original configuration, or model runtime, and makes no new model calls.

Distinguish:

- `submitted`: execution and CSV format checks passed.
- Correct answer: the prediction passed the frozen scoring rules.

Even after running all 410 tasks, only the 60 public-reference tasks can be scored locally. Preserve missing predictions as failures instead of removing them from the denominator.

### 3.5 Calculate and reconcile costs

This version supports **offline cost analysis**. Explicitly supply redacted request usage evidence and reviewed pricing to import an analysis:

```sh
./scripts/experiment.sh cost import \
  --source .local/import/calls.json --source-type calls \
  --pricing .local/pricing/reviewed.json --output .audit/call-analysis
```

Check whether the evidence supports a submission cost claim:

```sh
./scripts/experiment.sh cost submission-check \
  --experiment .audit/call-analysis
```

Revalue with another pricing file into a new directory:

```sh
./scripts/experiment.sh cost revalue \
  --experiment .audit/call-analysis \
  --pricing .local/pricing/reviewed.json --output .audit/call-revaluation
```

Fill the empty rates in the [pricing template](configs/pricing.example.json) from official provider prices and record the source and applicable date. Keep public-price calculations, provider-returned amounts, and actual account payments distinct.

Incomplete request records must produce an incomplete-cost result; unknown usage is not zero. Three historical attempts lack usage, so the complete historical cost cannot be reconstructed from these materials. Full online collection remains `UNSUPPORTED_G1`; finishing a run does not automatically produce a certified complete request-cost record.

New analysis uses v2. Old v1 analysis stays read-only, with verification and revaluation written to new independent outputs. Sum components per physical request, round once to an exact step including 0.05, then sum across requests. See the [cost analysis reference](provenance/observability.zh-CN.md) and [rounding policy](provenance/config-rounding.zh-CN.md) for details.

### 3.6 Submit results to DataSpace

Follow the [official DataSpace submission guidelines](https://dataspace-bench.github.io/submission-guidelines.html) for a new leaderboard application. The main requirements include:

1. One final prediction and a matching complete execution trace for each of the 410 tasks.
2. Metadata covering the method, model, execution date, and predefined final-result selection policy.
3. Usage and cost records covering failed calls and retries, with official pricing sources.
4. Local evaluation results for the 60 public-reference tasks.

Use `predictions/task_N/prediction.csv`. The official package also requires `metadata.json` and `traces/`. Unified experiments collect predictions, but there is currently no tool that automatically creates and sends a complete official submission package. Review traces, costs, and metadata before submitting through the official channel.

The public historical evidence ZIP contains selected traces and cannot serve directly as a new complete submission package. A cost-completeness check is not official submission approval. Disclose gaps in historical usage and immutable model identity instead of reconstructing unsupported evidence.

### 3.7 Reproduction scope and common problems

| Situation | Action |
|---|---|
| Only verifying historical results | Obtain the evidence archive and scoring materials, then verify offline |
| Limited disk space | Avoid duplicate complete datasets; start with historical offline verification |
| Missing tools or sandbox permissions | Provide the required host capabilities; do not fall back to direct host execution |
| Host profile mismatch | Check the actual interpreter and runtime, then register an independent environment |
| Runtime paths remain old | Existing YAML is preserved by design; generate and explicitly select a fresh offline configuration |
| Missing prediction | Preserve failure records and deliberately resume after diagnosis |
| Checksum mismatch or cache conflict | Stop using the material, check its source, and do not overwrite historical resources |
| Integrity conflict | Fail verification and preserve evidence; do not edit original records to manufacture success |
| Lock contention | Wait for the writer; do not run concurrent run, verify, or revalue operations on the same directory |
| Incomplete cost evidence | State the unknown scope; do not claim certified complete costs |
| Old experiment source identity mismatch | Use its original frozen tool version or create a new experiment; do not bypass checks |

The historical model alias has no saved immutable service snapshot, and rebuilt host environments have independent identities. Record code, environment checks, historical prediction verification, and new experiment scores separately. See [Experiments](EXPERIMENTS.md) for historical recovery, later independent runs, and evidence limits.

For the experiment commands above, exit 0 means the operation completed, not that all answers are correct. Exit 1 indicates execution, evidence, scoring, or report failure; 2 indicates an argument or prerequisite failure; 130 indicates interruption.

## 4. Future work

Planned directions:

- **Better entry points**: improve diagnostics and preparation based on actual use of the three scripts.
- **Continued compatibility checks**: verify additional Python patch versions and hosts based on evidence, and organize regular CI for future main changes.
- **Complete cost observation**: establish per-request persistence, usage, and amount evidence, then enable full online collection only after acceptance.
- **EviWeave Controlled**: develop separately from frozen V6, retaining clear version and experiment boundaries.
- **Research materials**: prepare a formal technical report and fuller method analysis.

These are future work, not capabilities delivered by v0.1.0. The historical cloud workflow still verifies fixed v0.1.0; the new preparation workflow tests its actual candidate commit, with execution records kept separate from historical v0.1.0.

---

EviWeave author: **袁珺 / Jun Yuan**. Code and documentation use [MIT](LICENSE), retaining DataSpace Authors notices and source attribution. Data, predictions, and third-party runtime components retain their own terms; this project's MIT license does not relicense them. Data comes from the [fixed official release](https://huggingface.co/datasets/HKUSTDial/DataSpace/tree/96be19257d7aa251dbce4771d46a26fc36f6dc52/release), and business source comes from the historical frozen V6 snapshot.

Private `docs/`, audit, data, configuration, and runtime directories are excluded from Git and distribution packages.

[Cite this project](CITATION.cff) · [Source provenance](provenance/README.zh-CN.md) · [Custom data format](DATA_FORMAT.md) · [Release notes](RELEASE_NOTES.md)
