#!/usr/bin/env bash
# Credential-free entry: never source private env.sh or inherit service access.
set -eu
root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
preparation_python="${EVIWEAVE_BOOTSTRAP_PYTHON:-python3}"
args=("$@")
for ((i=0; i<${#args[@]}; i++)); do
    case "${args[i]}" in
        --python)
            if ((i+1 >= ${#args[@]})); then
                builtin printf '%s\n' '--python requires an interpreter' >&2
                exit 2
            fi
            preparation_python="${args[i+1]}"
            ;;
        --python=*) preparation_python="${args[i]#--python=}" ;;
    esac
done
if [[ -z "$preparation_python" || "$preparation_python" == -* ]]; then
    builtin printf '%s\n' '--python requires an interpreter path or command name' >&2
    exit 2
fi
exec env -i PATH="$PATH" LANG="${LANG:-C.UTF-8}" \
    LD_LIBRARY_PATH="${LD_LIBRARY_PATH:-}" HOME="$root/.audit/preparation-home" \
    PYTHONNOUSERSITE=1 PYTHONDONTWRITEBYTECODE=1 \
    "$preparation_python" -I -B "$root/tools/preparation.py" dataspace "$@"
