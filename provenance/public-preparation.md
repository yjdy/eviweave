# P5.2 public preparation tools

This technical command reference covers Linux x86_64 and independent CPython
3.12/3.13 hosts. It does not certify model authentication, G1 collection, another
physical machine, or historical rootfs equality. Original exact profiles and all
dependency/package/evaluation locks remain unchanged. No preparation command
starts model inference or benchmark tasks.

Run commands from the source checkout. All targets, cache, HOME, temporary files
and Docker/containerd storage must stay inside that checkout. `python3` in the
first steps needs only the standard library and supported host tools. Keep
private directories excluded from Git and distribution packages.

```sh
python3 -B tools/public_resources.py evaluation --target .local/public-evaluation
python3 -B tools/public_resources.py codex --target .runtimes/public
python3 -B tools/public_resources.py workbench --target .runtimes/public/workbench
python3 -B tools/host_profile.py collect --name independent-312 \
  --codex-root .runtimes/public/codex-0.145.0/platform-package \
  --workbench-root .runtimes/public/workbench \
  --output .local/profiles/independent-312.json
python3 -B tools/host_profile.py check --profile .local/profiles/independent-312.json \
  --output .audit/public-profile-check
scripts/install.sh --environment-profile .local/profiles/independent-312.json
```

Use the intended host Python via `EVIWEAVE_BOOTSTRAP_PYTHON` when creating the
venv. `EVIWEAVE_PYTHON` selects a project-local `.venv*` target. Installation
authenticates exact reviewed wheels (including pip) before executing them, uses
the original ABI-specific requirements, then checks installed member contents.
Bootstrap Python does not need PyYAML: fresh configuration is written by the
installed target interpreter. Existing configuration is preserved. Repeating
installation validates the same profile and dependencies; a conflicting or
incomplete environment is rejected. No package is automatically upgraded.

Collection saves a candidate without activation. `check` compares the original
profile and executes a fixed local namespace/mount probe; packages are checked
after installation. Selection is explicit through `--environment-profile` or
`EVIWEAVE_ENVIRONMENT_PROFILE`. Command line wins over nonempty `.local/env.sh`,
then inherited environment, then the original exact default. Empty private
settings inherit. No candidate is selected by directory discovery.

Create a separate private configuration for the independent runtime, then run
the explicitly offline path with the installed interpreter:

```sh
.venv-experiment/bin/python -B tools/host_profile.py config \
  --profile .local/profiles/independent-312.json --output .local/public-config/codex.yaml
.venv-experiment/bin/python -B tools/host_profile.py doctor \
  --profile .local/profiles/independent-312.json --config .local/public-config/codex.yaml \
  --output .audit/public-offline-doctor
.venv-experiment/bin/python -B tools/unified_smoke.py \
  --environment-profile .local/profiles/independent-312.json \
  --evaluation-root .local/public-evaluation --output-root '.audit/public synthetic'
```

The synthetic tool accepts only shipped artificial contexts, forcibly replaces
the executable with the local synthetic executor, and adds network isolation.
It verifies CSV bytes, collection, interruption/recovery, and zero additional
runner construction on completed resume. It does not run benchmark inference.
Production `scripts/experiment.sh check` still fails on a missing dedicated
`EVIWEAVE_API_KEY` and reports `service_auth=NOT_TESTED`; it never claims that
presence of a key authenticates a service. Real runs require separate explicit
authorization. New experiments snapshot the full selected profile, including
runtime inventories and its digest. Resume restores that snapshot, ignores
inherited profile selection, and rejects an explicit `--environment-profile`.
Existing configuration/source/data/environment identity checks still apply.

`workbench` requires Docker, containerd, and passwordless permission for the
existing project-local daemon launcher. It never connects to the system socket.
It prints a distinct image tag, writes a separate rootfs, complete inventory and
source receipt, and leaves its daemon available for frozen doctor image checks.
Stop that daemon explicitly with:

```sh
python3 -B scripts/project_docker.py stop --storage-root .runtimes/public-build
```

The new build uses the original public base digest, all 329 exact Debian
artifacts and 21 exact wheels, with offline installation. The public lock records
official URLs, immutable dataset/evaluator revisions, artifact sizes and hashes.
The old `libexpat1` artifact is the same version, acquired through official HTTPS
and pinned to the first-stage observed SHA-256. Its old signed Packages entry
was unavailable; this is an explicitly reviewed HTTPS artifact trust boundary,
not authentication of a historical Debian snapshot. The other 328 package hashes
came from verified signed current indices. There is no version substitution.
The original Dockerfile and historical inventories remain unchanged.

`artifacts` downloads and verifies the full Debian/wheel set without building.
`evaluation` retrieves only the 120 pinned ZIP ranges and the pinned evaluator;
it checks header/path/size/CRC/SHA-256 and the original complete 121-file lock.
It does not execute source scripts or download the whole dataset. Optional data
preparation requires space for the 2,878,651,222-byte ZIP and extracted contents:

```sh
python3 -B tools/public_resources.py dataset --target data/public-DataSpace-Benchmark
```

The complete ZIP must match the official SHA-256 before extraction. Data and
reference answers remain private and are not distributed by this project.
Corrupt cache, missing version, failed download, linked/escaping paths,
permissions, conflicting destinations and namespace failure stop preparation.
There is no fallback to DataSpace, developer copies, newer packages or host
execution. Failed receipts remain in the chosen private cache. Consult each
command's `--help` for separate cache/storage/target selection.

Code and this explanation: MIT; EviWeave contributions by 袁珺 / Jun Yuan.
Upstream resource licenses remain their own. Source records are extended by
`p5-baseline.json`; previous provenance JSON is retained byte for byte.
