#!/usr/bin/env bash
set -e
root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
source "$root/scripts/env.sh"
exec "${EVIWEAVE_BOOTSTRAP_PYTHON:-python3}" -B "$root/tools/install_environment.py" --python "$EVIWEAVE_PYTHON" "$@"
