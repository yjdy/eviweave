#!/usr/bin/env bash
# Source-safe: preserve caller options and inherited nonempty values.
_eviweave_load_env() {
    local tracing=0
    case $- in *x*) tracing=1; set +x ;; esac
    local root name value
    root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)" || return 2
    local -A inherited=()
    for name in EVIWEAVE_API_KEY EVIWEAVE_PYTHON EVIWEAVE_CONFIG EVIWEAVE_DATASET EVIWEAVE_EVALUATION_ROOT; do
        inherited[$name]="${!name-}"
    done
    if [[ -L "$root/.local" || -L "$root/.local/env.sh" ]]; then
        builtin printf '%s\n' 'Refusing linked private environment file.' >&2
        return 2
    fi
    if [[ ! -e "$root/.local/env.sh" ]]; then
        mkdir -p -- "$root/.local" || return 2
        (umask 077; set -o noclobber; cat -- "$root/configs/env.sh.example" > "$root/.local/env.sh") || return 2
    fi
    source "$root/.local/env.sh" || return 2
    for name in EVIWEAVE_API_KEY EVIWEAVE_PYTHON EVIWEAVE_CONFIG EVIWEAVE_DATASET EVIWEAVE_EVALUATION_ROOT; do
        value="${!name-}"
        if [[ -z "$value" ]]; then value="${inherited[$name]}"; fi
        if [[ -z "$value" ]]; then
            case "$name" in
                EVIWEAVE_PYTHON) value="$root/.venv-experiment/bin/python" ;;
                EVIWEAVE_CONFIG) value="$root/.local/config-v6/codex.yaml" ;;
                EVIWEAVE_DATASET) value="$root/data/DataSpace-Benchmark" ;;
                EVIWEAVE_EVALUATION_ROOT) value="$root/.local/evaluation-v6-public60" ;;
            esac
        fi
        builtin printf -v "$name" '%s' "$value"
        export "$name"
    done
    export TMPDIR="$root/.audit/tmp" PIP_CACHE_DIR="$root/.audit/cache/pip" UV_CACHE_DIR="$root/.audit/cache/uv"
    export PIP_DISABLE_PIP_VERSION_CHECK=1
    export XDG_CACHE_HOME="$root/.audit/cache/xdg" PYTHONNOUSERSITE=1 PYTHONDONTWRITEBYTECODE=1
    mkdir -p -- "$TMPDIR" "$PIP_CACHE_DIR" "$UV_CACHE_DIR" "$XDG_CACHE_HOME" || return 2
    if [[ "$tracing" = 1 ]]; then set -x; fi
}
_eviweave_load_env
_eviweave_env_status=$?
unset -f _eviweave_load_env
return "$_eviweave_env_status" 2>/dev/null || exit "$_eviweave_env_status"
