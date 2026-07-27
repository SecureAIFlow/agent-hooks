#!/bin/bash
# Sign in only (refresh the SecureAIFlow token). Shim over install.py --login.
set -uo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
PY=""
for c in python3 python py; do
    if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import json,http.client' >/dev/null 2>&1; then
        PY="$c"; break
    fi
done
[[ -z "$PY" ]] && { echo "Python 3 is required on PATH." >&2; exit 1; }
exec "$PY" "${REPO_ROOT}/install.py" --login
