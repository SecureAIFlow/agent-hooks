#!/bin/bash
# SecureAIFlow agent-hooks — macOS/Linux one-line installer.
#
#     bash install.sh cursor        (or antigravity | copilot | codex | --login)
#     curl -fsSL https://<host>/install.sh | bash -s -- <ide>
#
# Thin shim: finds a working Python 3 and hands off to install.py, which does
# everything (config, launcher, sign-in, IDE hook activation). When piped from
# curl with no clone present, it clones the bundle first (override the source
# with SAF_REPO_URL).

set -uo pipefail

SAF_REPO_URL="${SAF_REPO_URL:-https://github.com/SecureAIFlow/agent-hooks.git}"

die() { printf 'error: %s\n' "$*" >&2; exit 1; }

# ── Resolve the bundle (local clone, or clone when piped from curl) ───────
if [[ -n "${SAF_HOME:-}" && -f "${SAF_HOME}/install.py" ]]; then
    REPO_ROOT="$SAF_HOME"
else
    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd || true)"
    if [[ -n "$SCRIPT_DIR" && -f "${SCRIPT_DIR}/install.py" ]]; then
        REPO_ROOT="$SCRIPT_DIR"
    else
        REPO_ROOT="${HOME}/.secureaiflow/agent-hooks"
        if [[ -f "${REPO_ROOT}/install.py" ]]; then
            git -C "$REPO_ROOT" pull --quiet 2>/dev/null || true
        else
            command -v git >/dev/null 2>&1 || die "git is required to install from curl"
            git clone --quiet "$SAF_REPO_URL" "$REPO_ROOT" || die "clone failed"
        fi
    fi
fi

# ── Find a Python that actually runs (Windows Store stub exits non-zero) ──
PY=""
for c in python3 python py; do
    if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import json,http.client' >/dev/null 2>&1; then
        PY="$c"; break
    fi
done
[[ -z "$PY" ]] && die "Python 3 is required on PATH"

exec "$PY" "${REPO_ROOT}/install.py" "$@"
