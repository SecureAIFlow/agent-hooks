#!/bin/bash
# Thin launcher: find a working Python, then hand the whole job to lib/saf_hook.py
# in a single process. All logic (parse, detect, normalize, file read, API call,
# response dialect, fail-closed) lives in the Python core — on Windows that is
# ~0.3s versus ~7s for the old bash-orchestrated pipeline.
#
# Usage: bin/run-hook.sh <hook-name>   (hook name reserved; the core keys off event)

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
CORE="${REPO_ROOT}/lib/saf_hook.py"

# Deny in a shape every supported IDE understands, for the rare case where we
# cannot even start Python. exit 2 also denies in clients that read the code.
launcher_deny() {
    if [[ "${SAF_FAIL_OPEN:-0}" == "1" ]]; then
        printf '{"continue":true,"permission":"allow"}\n'; exit 0
    fi
    printf '{"continue":false,"permission":"deny","user_message":"SecureAIFlow could not verify this action: %s"}\n' "$1"
    exit 2
}

# Reuse an interpreter the parent already resolved; else find one that RUNS
# (on Windows `python3` is often a Store stub that only errors).
PY="${SAF_PY:-}"
if [[ -z "$PY" ]]; then
    for c in python3 python py; do
        if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import json,http.client' >/dev/null 2>&1; then
            PY="$c"; break
        fi
    done
fi
[[ -z "$PY" ]] && launcher_deny "python 3 is required on PATH"
[[ -f "$CORE" ]] || launcher_deny "core not found"

exec "$PY" "$CORE" "${1:-saf-guard-prompt}"
