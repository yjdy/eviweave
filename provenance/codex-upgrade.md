# V8 experimental Codex 0.170.0

Author: 袁珺 / Jun Yuan. MIT; retained upstream notices and historical provenance apply.

This opt-in development branch retains the original Codex 0.145.0 installer,
example configuration, resource lock, exact host profiles and all 22 business
modules. It adds a separate official Linux x86_64 package lock, installer and
format-3 independent host profile. Format 1/2 keep their historical validation.
No real model, service, solver or benchmark is part of offline acceptance.

Prepare the experimental client (downloads a fixed package, runs only --version):

```sh
python3 -B scripts/install_codex_upgrade.py
```

Create a separate configuration from an explicitly selected 0.145.0 configuration:

```sh
python3 -B tools/codex_upgrade.py \
  --base-config configs/codex.example.yaml \
  --output .local/config-v8-codex-0170/codex.yaml
```

The public example retains its invalid endpoint and is not a live-service setup.
The converter preserves the resolved model, provider address, task budgets,
concurrency, Workbench and harness options. Only the Codex version and runtime
path change. V8 task prompts and export/review instructions remain byte-identical.
The experiment disables the newer default `features.goals` capability to retain
the pre-upgrade execution boundary. Codex's own built-in instructions, tools and defaults can differ by version; the
offline probe records the effective configuration and request differences.

For a prepared independent Workbench, collect a NEW profile with
`tools/host_profile.py collect --codex-version 0.170.0` and explicitly supply the
new package path, Workbench path, profile name and output under `.local/profiles/`.
The original Workbench/source/dependency checks remain mandatory. Select that
profile explicitly when using the unified V8 entry point. The package installer
does not install Workbench, task data, evaluation resources or credentials.
Keep old result directories and sessions with their original runtime identity;
create new experiment output directories for the upgraded runner.

Offline protocol validation:

```sh
python3 -B tools/codex_upgrade_probe.py \
  --output .audit/codex-upgrade-0170/probe-new \
  --rootfs /absolute/path/to/read-only/workbench/rootfs \
  --baseline-runtime /absolute/path/to/codex-0.145.0/platform-package/vendor/x86_64-unknown-linux-musl
```

The entire diagnostic has only a private loopback network, a dummy credential and
a finite Responses fixture. No forwarding or external service is available.
It checks both installed clients, unmodified V8 task prompts, tool execution,
input read-only mounts, hidden host paths, credential filtering, command network
denial, ephemeral/persisted sessions, same-version resume with source preservation,
cumulative usage accounting, 401/402/403 classification, timeout and cancellation.
The 2/20-second synthetic timeouts do not change production budgets. These checks
cannot establish real provider compatibility, model quality or business scores.
Historical online observability remains unsupported; this upgrade does not certify G1.

For the shared V8 R2 entry and output contract, see [v8-prompt-r2.md](v8-prompt-r2.md).
