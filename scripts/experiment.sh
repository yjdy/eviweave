#!/usr/bin/env bash
set -e
root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
source "$root/scripts/env.sh"
if [[ ! -x "$EVIWEAVE_PYTHON" ]]; then
    builtin printf '%s\n' 'Python environment missing; run scripts/install.sh first.' >&2
    exit 2
fi
exec "$EVIWEAVE_PYTHON" -B "$root/tools/experiment.py" "$@"
