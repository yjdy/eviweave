# EviWeave — Evidence-Guided Data Agents

[简体中文](README.zh-CN.md)

EviWeave runs data analysis tasks over heterogeneous local workspaces: inspect evidence, execute analysis in a sandbox, and submit a lossless CSV. This first version preserves the historical **relation-v6 + simple-fixes-v1** baseline with **Codex 0.145.0**, **Qwen3.8-Flash** and the **Responses** protocol. It includes explicit resource preparation, environment profiles, experiment run/resume, offline verification and offline cost analysis.

**v0.1.0 is pending release.** Documentation preparation is complete; final clean-checkout acceptance, final commit/build binding and online publication remain pending. See [experiments](EXPERIMENTS.md), [release notes](RELEASE_NOTES.md), [candidate assets](provenance/release-assets.json) and [roadmap](ROADMAP.md). EviWeave Controlled is future work outside this release.

## Support and distribution scope

| Scope | Supported / verified boundary |
|---|---|
| Full sandbox workflow | Linux x86_64; Docker/dockerd, containerd, Bubblewrap and permission to create required namespaces/mounts; project-local Docker storage, no system socket |
| Independent host profiles | CPython 3.12 / 3.13; actual patch version, interpreter bytes, ABI, OS/kernel and host tools are measured and checked |
| Local acceptance | CPython 3.12.3 and 3.13.16 on one physical host; 228 source and 228 installed tests per version in P5.2 |
| Package metadata | Python >=3.10, PyYAML>=6,<7; Python 3.10 execution and hosted CI remain unverified; CI configuration is not a passing hosted run |
| Other platforms | macOS, Windows, ARM and arbitrary kernels have not been accepted |
| Wheel | 22 whitelisted business modules, metadata and two CLI aliases: `eviweave`, `dataspace-baselines` |
| Source checkout / sdist | Also supplies `scripts/`, `tools/`, locks, synthetic examples and public documentation; needed for the workflow below |

Installing a wheel alone does not provide repository tools, datasets or runtimes. A package-only user may install a locally obtained wheel in a project-local venv with `python -m pip install /project/path/eviweave-0.1.0-py3-none-any.whl`, then use `eviweave --help`; the locked source workflow below is the reproduction path. No PyPI publication is claimed.

The two original exact [profiles](locks/README.md) register the verified host on 2026-10-23, not the historical V6 machine. An independent public rebuild has its own full file inventory and identity. Matching 329 system packages, 21 Python packages and TOOLS does not prove historical rootfs equality. Historical runtime imports remain optional and require their original inventories; no private binary/rootfs download is offered.

## Installation and resource preparation

Run from the source root, using the intended CPython 3.12 or 3.13 as `python3` throughout collection and bootstrap. Initial preparation needs only the Python standard library plus the host tools above. All downloads, temporary files, HOME, environments and Docker/containerd state must stay inside this project. Targets must be new and disjoint; keep sufficient disk space for artifacts, Docker vfs layers and exported rootfs. The dataset ZIP is 2,878,651,222 bytes; its extracted contents total 15,010,873,064 bytes, before runtime/build storage.

These commands download public resources and build a separate workbench. They do not invoke a model. `workbench` requires passwordless permission for the project-local daemon launcher; it does not install missing host system tools.

```sh
mkdir -p .audit/tmp .audit/cache .local/profiles
export TMPDIR="$PWD/.audit/tmp"
export XDG_CACHE_HOME="$PWD/.audit/cache"
export PYTHONDONTWRITEBYTECODE=1
python3 -B tools/public_resources.py codex --target .runtimes/public
python3 -B tools/public_resources.py workbench --target .runtimes/public/workbench \
  --storage .runtimes/public-build
python3 -B tools/public_resources.py evaluation --target .local/evaluation-v6-public60
python3 -B tools/host_profile.py collect --name public-host \
  --codex-root .runtimes/public/codex-0.145.0/platform-package \
  --workbench-root .runtimes/public/workbench --output .local/profiles/public-host.json
python3 -B tools/host_profile.py check --profile .local/profiles/public-host.json \
  --output .audit/public-profile-check
./scripts/install.sh --python "$PWD/.venv-experiment/bin/python" \
  --environment-profile .local/profiles/public-host.json
.venv-experiment/bin/eviweave --help
.venv-experiment/bin/dataspace-baselines --help
```

The installer authenticates exact dependency wheels and contents before using locked pip, then installs the package. PyYAML is not required by the bootstrap interpreter. A repeated installation validates the same profile; it does not upgrade or overwrite a conflicting venv. An existing `.local/config-v6/codex.yaml` is preserved. For a fresh installation it is generated with selected runtime paths and `https://example.invalid/v1`.

Resource URLs, full revisions, lengths and hashes are fixed in [the public resource lock](provenance/public-resources-lock.json). The evaluator is pinned to GitHub `6491caa4c70cc06cacb6103ba73cefb00746abfe`; dataset/scoring members to Hugging Face `96be19257d7aa251dbce4771d46a26fc36f6dc52`. Evaluation preparation fetches 120 ZIP members plus the evaluator and checks the original 121-file lock without downloading all task data. Full data preparation is separate:

```sh
python3 -B tools/public_resources.py dataset --target data/DataSpace-Benchmark
.venv-experiment/bin/python -B tools/standalone.py verify-evaluation \
  --evaluation-root .local/evaluation-v6-public60
```

Full ZIP SHA-256 must be `68f221fafcf0b50396eca55f8298af1ad73d586a6cac6995916afca535a560af` before extraction. The dataset root contains `input/task_N/task.json` and `input/task_N/context/`; `--dataset` takes the parent of `input/`. Existing complete data may be reused after verification; the tool refuses a conflicting destination. Nothing discovers neighbouring DataSpace directories or reads their credentials.

`artifacts --cache ...` can fetch the 329 exact debs and 21 workbench wheels without building. Cache entries are rehashed; corrupt cache is rejected rather than silently replaced. The fixed old `libexpat1` hash was observed from official HTTPS; its historical signed Packages entry was unavailable. The other 328 package hashes came from verified signed indices. This trust boundary is retained, not described as a verified historical Debian snapshot. See [preparation details](provenance/public-preparation.md).

The build leaves the project-local daemon available for image checks. Use `python3 -B scripts/project_docker.py info --storage-root .runtimes/public-build`; stop it with `stop` instead of `info` after checks/runs. Restart with `start` using the same storage before later image checks. Do not substitute the system daemon.

## Environment profiles and private configuration

Collection writes a candidate; successful collection/check does not activate it. Explicit CLI selection wins over nonempty `.local/env.sh`, then inherited environment, then the original exact default. For shell wrappers, nonempty private values override inherited values; blank values inherit. The private file is created from [the template](configs/env.sh.example) on first wrapper use.

Use the same settings throughout:

| Setting | Value in this guide |
|---|---|
| `EVIWEAVE_PYTHON` | `$PWD/.venv-experiment/bin/python` |
| `EVIWEAVE_ENVIRONMENT_PROFILE` | `$PWD/.local/profiles/public-host.json` |
| `EVIWEAVE_CONFIG` | `$PWD/.local/config-v6/codex.yaml` |
| `EVIWEAVE_DATASET` | `$PWD/data/DataSpace-Benchmark` |
| `EVIWEAVE_EVALUATION_ROOT` | `$PWD/.local/evaluation-v6-public60` |
| `EVIWEAVE_API_KEY` | Dedicated key set by the user in the terminal; leave the private template blank to inherit |

Review existing private settings before use. `EVIWEAVE_BOOTSTRAP_PYTHON` chooses the installer interpreter, not the target venv. `EVIWEAVE_AUDIT_ROOT` optionally locates preparation/check diagnostics inside the project. There is no `EVIWEAVE_BASE_URL` or `EVIWEAVE_DATASET_ROOT` setting for the unified wrapper; put the endpoint in YAML and use the settings above.

For a new public runtime with an existing historical configuration, generate a **fresh** offline file, inspect its runtime paths, and deliberately select it. This does not overwrite `codex.yaml`:

```sh
.venv-experiment/bin/python -B tools/host_profile.py config \
  --profile .local/profiles/public-host.json --output .local/config-v6/offline.yaml
```

## No-model checks and synthetic execution

No API key is needed here. Use the generated placeholder configuration for offline doctor. These checks validate local runtime contents and capabilities, not service authentication. Choose a fresh audit output for each independent check.

```sh
.venv-experiment/bin/python -B tools/host_profile.py doctor \
  --profile .local/profiles/public-host.json --config .local/config-v6/offline.yaml \
  --output .audit/public-offline-doctor
.venv-experiment/bin/python -B tools/unified_smoke.py \
  --environment-profile .local/profiles/public-host.json \
  --evaluation-root .local/evaluation-v6-public60 --output-root '.audit/public synthetic'
.venv-experiment/bin/python -B tools/run_regression.py \
  --suite unit --output-root .audit/public-unit
```

The synthetic entry forcibly substitutes the local executor and adds network isolation. It checks CSV bytes, collection, interruption/resume and completed resume with zero additional attempts; 2/2 is an artificial test score. It does not execute benchmark inference or the real model client. The package also provides `eviweave doctor --offline --config ...`; unlike the profile doctor it does not select/check an independent host profile. Ordinary doctor requires a key; neither mode authenticates the service.

## Unified workflow

**The `run` commands in this section invoke a real model and incur charges.** They are examples only; no model run is part of document preparation. First prepare the data/runtime/evaluator, select the checked profile, and set a dedicated `EVIWEAVE_API_KEY` yourself. Do not put the key in YAML, input data, command arguments or logs. No fallback to another key is supported.

For real use, privately set `experiment.model.openai_base_url` in `.local/config-v6/codex.yaml` to the V6-compatible HTTPS Responses base URL, without embedded credentials, query or fragment. Keep `qwen3.8-flash`, Responses, relation-v6/simple-fixes-v1, 32,768 output tokens, 983,616 context tokens, 1,800 seconds per task and concurrency 1. Confirm runtime paths match the selected profile. A new endpoint/model/host is a new recorded environment, not proof of historical model identity.

After reviewing `.local/env.sh`, check one task without inference. A key must be present; success still reports `service_auth=NOT_TESTED`, `model_calls=0`. Without a key it returns 2 with `MISSING_KEY` after other preconditions pass. Placeholder endpoints are refused by this production path.

```sh
./scripts/experiment.sh check --tasks 33 \
  --config .local/config-v6/codex.yaml --dataset data/DataSpace-Benchmark \
  --evaluation-root .local/evaluation-v6-public60 \
  --environment-profile .local/profiles/public-host.json
```

Real single task, public batch, and resume of that batch:

```sh
./scripts/experiment.sh run --tasks 33 --output .audit/task33-v6 \
  --config .local/config-v6/codex.yaml --dataset data/DataSpace-Benchmark \
  --evaluation-root .local/evaluation-v6-public60 \
  --environment-profile .local/profiles/public-host.json
./scripts/experiment.sh run --scope public60 --output .audit/public60-v6 \
  --config .local/config-v6/codex.yaml --dataset data/DataSpace-Benchmark \
  --evaluation-root .local/evaluation-v6-public60 \
  --environment-profile .local/profiles/public-host.json
./scripts/experiment.sh run --output .audit/public60-v6 --resume --yes
```

Omitting `--tasks`/`--scope` selects **public60**, with `--repeat 1`. `--tasks 23 35` selects explicit IDs 1–410; `--scope all` selects all 410 and can cost substantially more. `--repeat N` schedules N rounds, not best-of-N selection. Do not run an unqualified batch to test installation.

Resume restores the saved tasks/repeat/configuration/data/profile and rejects explicit replacements, including `--environment-profile`; external profile drift is not imported. Original config, registered inputs, source/environment identity and runtime checks still apply. Source changes may require the original frozen tool checkout or a new experiment; do not edit old metadata to bypass identity. `--yes` explicitly permits reuse without an interactive prompt.

Every newly allocated attempt replaces that task's current answer, even if it fails. Resume addresses unstarted, interrupted, execution-failed or raw-prediction-damaged items once per resume; a normally submitted wrong answer is skipped. A damaged collected copy can be restored without model calls. Ctrl+C cancels the active process group and records interruption (130). A completed resume makes zero new attempts.

Confirmed final 401/402/403 failures stop further dispatch and cancel in-flight work after the current task returns. Frozen client reconnect behavior is retained; this is not an instant per-HTTP circuit breaker. 429/500 are ordinary task failures. See [fix boundaries](provenance/bugfix.zh-CN.md).

## Offline verification and historical evidence

Unified verification reads saved configuration/input hashes/profile and the experiment's independent 121-file evaluator copy. It needs no key, original data/configuration or model runtime; it refuses concurrent writes. It writes versioned `summary.json`, `score.json`, `summary.csv` with one report ID. `submitted` means valid execution/CSV, not a correct answer. Accuracy counts distinct public task IDs, including missing predictions; all410 has 60 scored and 350 unscored tasks.

```sh
./scripts/experiment.sh verify --experiment .audit/public60-v6 \
  --output .audit/public60-v6-verification
```

The historical evidence ZIP and its external `.sha256` are **candidate Release assets, not yet uploaded**. Place both in `.local/evidence-v6/` after obtaining them. Verify before extracting; the verifier needs only the standard library and source tools. Then optionally score existing predictions with CPython 3.12.3:

```sh
python3 -B tools/package_evidence.py verify \
  --archive .local/evidence-v6/eviweave-v0.1.0-v6-evidence.zip \
  --output-root .audit/evidence-verification
unzip -q .local/evidence-v6/eviweave-v0.1.0-v6-evidence.zip -d .local/evidence-v6/extracted
.venv-experiment/bin/python -B tools/standalone.py score \
  --evaluation-root .local/evaluation-v6-public60 \
  --prediction-root .local/evidence-v6/extracted/predictions \
  --output-root .audit/historical-score
```

Expected historical offline result: **45/60**, 15 failed and 350 unscored; the first-pass record is **44/60**. This is separate from the official historical **333/410, 81.22%**, evaluated 2026-10-21, verified 2026-10-22 and rechecked 2026-10-24. The page still listed the result tied first at that recheck; this is a dated observation, not a live ranking guarantee. See [the official leaderboard](https://dataspace-bench.github.io/#leaderboard) and [experiment boundaries](EXPERIMENTS.md).

The ZIP contains 410 unchanged predictions, 414 attempt summaries, fixed selection and **selected traces** for 5 tasks / 6 attempts. Full traces, reference answers, private runtimes and redaction mappings are excluded. Three missing usage records stay unknown; recorded tokens do not establish complete cost. Checksums establish byte integrity, not an author signature.

## Offline cost analysis

Complete online collection remains **UNSUPPORTED_G1**: fixed-client telemetry loses usage/amount extensions and does not establish persistence before each request. Enabling an observation profile is refused before model launch. Executable unified experiment v2 is not implemented; analysis v2 is an offline container, not a run/resume format.

Provide your own explicit project-local redacted evidence and reviewed pricing. The [pricing template](configs/pricing.example.json) has null rates; it is not a quotation. Imports do not contact a service or account console.

```sh
./scripts/experiment.sh cost import --source .local/import/calls.json \
  --source-type calls --pricing .local/pricing/reviewed.json --output .audit/call-analysis
./scripts/experiment.sh verify --experiment .audit/call-analysis
./scripts/experiment.sh cost revalue --experiment .audit/call-analysis \
  --pricing .local/pricing/reviewed.json --output .audit/call-revaluation
./scripts/experiment.sh cost submission-check --experiment .audit/call-analysis
```

`submission-check` is expected to fail for incomplete enumeration: observed requests are a lower bound and `cost_complete=false`; imports cannot certify total requests or formal submission cost. Provider-reported, public-list-calculated and actual-paid amounts are separate paths. Same response bytes with different physical request IDs count separately; repeated import of the same evidence is idempotent. Token subfields are not added to their parent totals, and unknown values are not zero.

New analyses use `schema_version=2`, `import_identity_version=2`, request contract v1. Old analysis v1 is read-only and requires an existing lock; verify/revalue must use a fresh, independent output. Old reports are not silently recomputed:

```sh
./scripts/experiment.sh verify --experiment .audit/old-v1-analysis \
  --output .audit/old-v1-verification-new
./scripts/experiment.sh cost revalue --experiment .audit/old-v1-analysis \
  --pricing .local/pricing/reviewed.json --output .audit/old-v1-revaluation-new
```

New calculations record `calculation_policy_version=decimal-quantum-v2`. Each physical request sums its components, rounds once to an exact quantum (including 0.05), then contributes to cross-request totals. Precision loss before rounding is rejected. See [contracts, JSON/SSE/billing imports and legacy snapshots](provenance/observability.zh-CN.md) and [rounding policy](provenance/config-rounding.zh-CN.md). Real service fields and account billing remain unverified.

## Troubleshooting

| Symptom | Action / meaning |
|---|---|
| Unsupported OS, architecture or ABI | Use Linux x86_64 / CPython 3.12 or 3.13 for the full workflow; do not weaken a lock |
| Missing runtime/evaluator/data | Prepare the explicit target above; verify 121 scoring files; point `--dataset` at the directory containing `input/` |
| Profile/interpreter/content mismatch | Collect/check/select a fresh independent profile and `.venv*`; preserve original exact locks; do not mix profiles in a venv or experiment |
| Wrong runtime paths after installation | Existing YAML was intentionally preserved; generate a fresh offline config and select it deliberately |
| Namespace, mount or daemon permission failure | Supply required host permissions and project-local daemon; no host-execution fallback |
| Hash mismatch or conflicting cache/target | Preserve failure receipt; inspect source and choose a fresh project-local cache/target; do not overwrite historical resources |
| `MISSING_KEY` or placeholder endpoint | Set only the dedicated key and valid private Responses URL; offline doctor is the key-free preparation path |
| Final 401/402/403 | Batch stops after the current task confirms failure and cancels in-flight work; fix access/funding before deliberate resume |
| 429/500 | Ordinary execution failure; inspect evidence; no automatic expansion of tasks/retries |
| `no_prediction` / missing raw CSV | Execution failure; retain logs and use deliberate resume; a wrong submitted answer is different and is not retried by resume |
| Integrity conflict | Verify fails, records `attempt_integrity`, and excludes conflicted usage; never repair raw records into a success |
| Lock occupied | Wait for the writer to finish; do not run concurrent verify/revalue/run against the same directory |
| Old experiment source mismatch | Use its original frozen tool version or create a new experiment; documentation does not authorize bypass |
| Old cost report / v1 analysis | Revalue with reviewed pricing into fresh output; preserve old bytes and existing source lock |

Exit 0 means the requested operation completed, not that every answer passed; 1 means execution/evidence/scoring/report failure; 2 means invalid arguments or missing prerequisites; 130 means interruption. Offline verify may successfully report existing execution failures without retrying them.

## More information and attribution

[Data format and custom single tasks](DATA_FORMAT.md) (Chinese; custom scoring is not registered in the unified workflow), [synthetic examples](examples/synthetic/README.md), [source provenance](provenance/README.zh-CN.md), [publication preparation and sources](provenance/p5-documentation.md), [CITATION.cff](CITATION.cff).

The separate `scripts/check_task33.sh` terminal helper remains available; `--check-only` checks local inputs/key visibility, not authentication. It uses `.local/config-v6/settings.json` from `tools/standalone.py configure` and defaults to `.venv/bin/python`, unlike the unified workflow. It is not needed above. Creating that standalone config requires a fresh output directory; do not overwrite the unified config. Actual use launches exactly three serial paid trials with independent HOME/sessions, stopping on environment/authentication/missing-prediction failure and continuing on wrong answers. Do not start it as an installation check.

Code and documentation: [MIT](LICENSE), with DataSpace Authors' notice and EviWeave contributions by **袁珺 / Jun Yuan** retained. The business source comes only from the historical frozen V6 snapshot, not current DataSpace code. DataSpace datasets, predictions and third-party runtime components retain their original source terms; MIT here does not relicense them. Obtain data from [the official release](https://huggingface.co/datasets/HKUSTDial/DataSpace/tree/96be19257d7aa251dbce4771d46a26fc36f6dc52/release) and consult [upstream](https://github.com/HKUSTDial/DataSpace). Private `docs/`, audit, data and runtime directories are excluded from Git and distributions.
