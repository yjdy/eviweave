#!/usr/bin/env bash
set -euo pipefail
project_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
python_bin="${EVIWEAVE_PYTHON:-$project_root/.venv/bin/python}"
if [[ ! -x "$python_bin" ]]; then
  echo '请准备项目内Python环境和PyYAML，或用EVIWEAVE_PYTHON指定解释器。' >&2
  exit 2
fi
export PYTHONDONTWRITEBYTECODE=1
exec "$python_bin" -B "$project_root/tools/check_task33.py" "$@"
