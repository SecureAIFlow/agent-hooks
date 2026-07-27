#!/bin/bash
# Test suite for the hook bundle. Runs against the mock API in test/mock_saf.py.
#   python test/mock_saf.py &   then   bash test/run-tests.sh
#
# Every case asserts BOTH that stdout is valid JSON and that the verdict is
# what the IDE contract requires.

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT" || exit 1

export SAF_LOG_FILE="${SAF_LOG_FILE:-/tmp/saf-hooks-test.log}"
export SAF_API_URL="${SAF_API_URL:-http://127.0.0.1:8899}"

PASS=0; FAIL=0

# Pick an interpreter that actually RUNS: `command -v python3` succeeds on
# Windows even when it is the Microsoft Store stub, which executes nothing.
PY=""
for _c in python3 python py; do
    if command -v "$_c" >/dev/null 2>&1 && "$_c" -c 'import json' >/dev/null 2>&1; then
        PY="$_c"; break
    fi
done
[[ -z "$PY" ]] && { echo "no working python found"; exit 1; }

# check <name> <payload> <jq-ish key> <expected> [env assignments...]
check() {
    local name="$1" payload="$2" key="$3" expected="$4"; shift 4
    local out
    out=$(printf '%s' "$payload" | env "$@" bash bin/run-hook.sh saf-guard-prompt 2>/dev/null)

    local actual
    actual=$(printf '%s' "$out" | "$PY" -c "
import json,sys
try: o=json.load(sys.stdin)
except Exception as e: print('INVALID_JSON'); sys.exit()
for p in '$key'.split('.'):          # dotted path for nested keys
    o = o.get(p) if isinstance(o, dict) else None
print(json.dumps(o))
" 2>/dev/null)

    if [[ "$actual" == "$expected" ]]; then
        printf '  PASS  %s\n' "$name"; PASS=$((PASS+1))
    else
        printf '  FAIL  %s\n        expected %s got %s\n        raw: %s\n' \
            "$name" "$expected" "$actual" "$out"; FAIL=$((FAIL+1))
    fi
}

echo "Fail-closed behaviour (no verdict must ever mean allow):"
check "no token -> deny" \
    '{"hook_event_name":"beforeSubmitPrompt","prompt":"hi","cursor_version":"1"}' \
    continue false SAF_TOKEN= SAF_CONFIG_DIR=/nonexistent
check "backend down -> deny" \
    '{"hook_event_name":"beforeSubmitPrompt","prompt":"hi","cursor_version":"1"}' \
    continue false SAF_TOKEN=t SAF_API_URL=http://127.0.0.1:59999 SAF_TIMEOUT=2
check "bad token -> deny" \
    '{"hook_event_name":"beforeSubmitPrompt","prompt":"hi","cursor_version":"1"}' \
    continue false SAF_TOKEN= SAF_CONFIG_DIR=/nonexistent

echo "Normal verdicts:"
check "clean prompt -> allow" \
    '{"hook_event_name":"beforeSubmitPrompt","prompt":"refactor my login","cursor_version":"1"}' \
    continue true SAF_TOKEN=test-token
check "secret prompt -> deny" \
    '{"hook_event_name":"beforeSubmitPrompt","prompt":"key AKIAIOSFODNN7EXAMPLE","cursor_version":"1"}' \
    continue false SAF_TOKEN=test-token
check "empty prompt -> allow" \
    '{"hook_event_name":"beforeSubmitPrompt","prompt":"","cursor_version":"1"}' \
    continue true SAF_TOKEN=test-token

echo "Payload robustness (regression: sed truncated at the first quote):"
check "quotes do not hide a secret" \
    '{"hook_event_name":"beforeSubmitPrompt","prompt":"he said \"hi\" then AKIAIOSFODNN7EXAMPLE","cursor_version":"1"}' \
    continue false SAF_TOKEN=test-token
check "backslashes+newlines survive" \
    '{"hook_event_name":"beforeSubmitPrompt","prompt":"path C:\\\\temp\\nthen AKIAIOSFODNN7EXAMPLE","cursor_version":"1"}' \
    continue false SAF_TOKEN=test-token
check "unicode prompt -> allow" \
    '{"hook_event_name":"beforeSubmitPrompt","prompt":"refactorise ma fonction café ☕","cursor_version":"1"}' \
    continue true SAF_TOKEN=test-token

echo "Per-event response dialect:"
check "shell exec clean -> permission allow" \
    '{"hook_event_name":"beforeShellExecution","command":"ls -la","cwd":"/tmp","cursor_version":"1"}' \
    permission '"allow"' SAF_TOKEN=test-token
check "shell exec secret -> permission deny" \
    '{"hook_event_name":"beforeShellExecution","command":"aws --key AKIAIOSFODNN7EXAMPLE","cwd":"/tmp","cursor_version":"1"}' \
    permission '"deny"' SAF_TOKEN=test-token

echo "Unknown client falls back to the generic adapter:"
check "generic deny shape" \
    '{"prompt":"key AKIAIOSFODNN7EXAMPLE","mystery":"client"}' \
    continue false SAF_TOKEN=test-token

echo "beforeReadFile closes the file-content bypass:"
printf 'aws_key = "AKIAIOSFODNN7EXAMPLE"\n' > /c/temp/saf_secret.py 2>/dev/null || printf 'aws_key = "AKIAIOSFODNN7EXAMPLE"\n' > /tmp/saf_secret.py
printf 'def hello():\n    return 42\n' > /c/temp/saf_clean.py 2>/dev/null || printf 'def hello():\n    return 42\n' > /tmp/saf_clean.py
SECRET_PATH="C:/temp/saf_secret.py"; CLEAN_PATH="C:/temp/saf_clean.py"
[[ -f /tmp/saf_secret.py ]] && SECRET_PATH="/tmp/saf_secret.py" && CLEAN_PATH="/tmp/saf_clean.py"
check "file with secret -> deny read" \
    "{\"hook_event_name\":\"beforeReadFile\",\"file_path\":\"${SECRET_PATH}\",\"cursor_version\":\"1\"}" \
    permission '"deny"' SAF_TOKEN=test-token
check "clean file -> allow read" \
    "{\"hook_event_name\":\"beforeReadFile\",\"file_path\":\"${CLEAN_PATH}\",\"cursor_version\":\"1\"}" \
    permission '"allow"' SAF_TOKEN=test-token
check "missing file -> allow (nothing to leak)" \
    '{"hook_event_name":"beforeReadFile","file_path":"/no/such/file.xyz","cursor_version":"1"}' \
    permission '"allow"' SAF_TOKEN=test-token

echo "afterAgentResponse is observe-only (never blocks):"
check "response -> continue true" \
    '{"hook_event_name":"afterAgentResponse","response":"some model output","cursor_version":"1"}' \
    continue true SAF_TOKEN=test-token

echo "Antigravity PreToolUse (decision dialect):"
check "run_command secret -> decision deny" \
    '{"toolCall":{"name":"run_command","args":{"CommandLine":"aws key AKIAIOSFODNN7EXAMPLE"}},"artifactDirectoryPath":"/a"}' \
    decision '"deny"' SAF_TOKEN=test-token
check "run_command clean -> decision allow" \
    '{"toolCall":{"name":"run_command","args":{"CommandLine":"ls -la"}},"artifactDirectoryPath":"/a"}' \
    decision '"allow"' SAF_TOKEN=test-token
check "write_to_file secret in CodeContent -> decision deny" \
    '{"toolCall":{"name":"write_to_file","args":{"TargetFile":"cfg.py","CodeContent":"K=AKIAIOSFODNN7EXAMPLE"}},"workspacePaths":["/w"]}' \
    decision '"deny"' SAF_TOKEN=test-token
check "antigravity backend down -> decision deny (fail closed)" \
    '{"toolCall":{"name":"run_command","args":{"CommandLine":"ls"}},"artifactDirectoryPath":"/a"}' \
    decision '"deny"' SAF_TOKEN=t SAF_API_URL=http://127.0.0.1:59999 SAF_TIMEOUT=2
check "antigravity replace_file_content secret -> deny" \
    '{"toolCall":{"name":"replace_file_content","args":{"TargetFile":"a.py","ReplacementContent":"K=AKIAIOSFODNN7EXAMPLE"}},"workspacePaths":["/w"]}' \
    decision '"deny"' SAF_TOKEN=test-token
check "antigravity search_web secret in query -> deny" \
    '{"toolCall":{"name":"search_web","args":{"query":"what is AKIAIOSFODNN7EXAMPLE"}},"artifactDirectoryPath":"/a"}' \
    decision '"deny"' SAF_TOKEN=test-token
check "antigravity view_file -> allow (safe tool, not scanned)" \
    '{"toolCall":{"name":"view_file","args":{"AbsolutePath":"/secrets/AKIAIOSFODNN7EXAMPLE.txt"}},"artifactDirectoryPath":"/a"}' \
    decision '"allow"' SAF_TOKEN=test-token

echo "GitHub Copilot preToolUse (permissionDecision dialect):"
check "copilot bash secret -> permissionDecision deny" \
    '{"toolName":"bash","toolArgs":{"command":"aws key AKIAIOSFODNN7EXAMPLE"}}' \
    permissionDecision '"deny"' SAF_TOKEN=test-token
check "copilot bash clean -> permissionDecision allow" \
    '{"toolName":"bash","toolArgs":{"command":"ls -la"}}' \
    permissionDecision '"allow"' SAF_TOKEN=test-token
check "copilot edit secret in nested args -> deny" \
    '{"toolName":"str_replace_editor","toolArgs":{"path":"a.py","new_str":"K=AKIAIOSFODNN7EXAMPLE"}}' \
    permissionDecision '"deny"' SAF_TOKEN=test-token
check "copilot backend down -> deny (fail closed)" \
    '{"toolName":"bash","toolArgs":{"command":"ls"}}' \
    permissionDecision '"deny"' SAF_TOKEN=t SAF_API_URL=http://127.0.0.1:59999 SAF_TIMEOUT=2

echo "Codex UserPromptSubmit (decision dialect) + PreToolUse (hookSpecificOutput):"
check "prompt secret -> decision block" \
    '{"hook_event_name":"UserPromptSubmit","prompt":"my key is AKIAIOSFODNN7EXAMPLE","permission_mode":"auto","session_id":"s1"}' \
    decision '"block"' SAF_TOKEN=test-token
check "prompt clean -> allow (no decision)" \
    '{"hook_event_name":"UserPromptSubmit","prompt":"refactor my login","permission_mode":"auto","session_id":"s1"}' \
    decision 'null' SAF_TOKEN=test-token
check "PreToolUse Bash secret -> nested permissionDecision deny" \
    '{"hook_event_name":"PreToolUse","tool_name":"Bash","tool_input":{"command":"echo AKIAIOSFODNN7EXAMPLE"},"permission_mode":"auto"}' \
    hookSpecificOutput.permissionDecision '"deny"' SAF_TOKEN=test-token
check "codex backend down -> block (fail closed)" \
    '{"hook_event_name":"UserPromptSubmit","prompt":"ls","permission_mode":"auto"}' \
    decision '"block"' SAF_TOKEN=t SAF_API_URL=http://127.0.0.1:59999 SAF_TIMEOUT=2

echo "Safe/interaction tools are never gated (no ask_user cascade):"
check "copilot ask_user -> allow (not scanned)" \
    '{"toolName":"ask_user","toolArgs":{"question":"Run echo AKIAIOSFODNN7EXAMPLE?","options":["Cancel"]}}' \
    permissionDecision '"allow"' SAF_TOKEN=test-token
check "codex view -> allow (not scanned)" \
    '{"hook_event_name":"PreToolUse","tool_name":"view","tool_input":{"path":"secrets/AKIAIOSFODNN7EXAMPLE.txt"},"permission_mode":"auto"}' \
    decision 'null' SAF_TOKEN=test-token
check "copilot bash secret still scanned -> deny" \
    '{"toolName":"bash","toolArgs":{"command":"echo AKIAIOSFODNN7EXAMPLE"}}' \
    permissionDecision '"deny"' SAF_TOKEN=test-token

echo "Redact-in-place (SAF_REDACT=1): Codex/Copilot tool calls rewrite instead of block:"
check "copilot tool secret -> allow with modifiedArgs" \
    '{"toolName":"bash","toolArgs":{"command":"echo AKIAIOSFODNN7EXAMPLE"}}' \
    permissionDecision '"allow"' SAF_TOKEN=test-token SAF_REDACT=1
check "copilot modifiedArgs contains redaction" \
    '{"toolName":"bash","toolArgs":{"command":"echo AKIAIOSFODNN7EXAMPLE"}}' \
    modifiedArgs.command '"echo __REDACTED__"' SAF_TOKEN=test-token SAF_REDACT=1
check "codex PreToolUse secret -> allow with updatedInput" \
    '{"hook_event_name":"PreToolUse","tool_name":"Bash","tool_input":{"command":"echo AKIAIOSFODNN7EXAMPLE"},"permission_mode":"auto"}' \
    hookSpecificOutput.permissionDecision '"allow"' SAF_TOKEN=test-token SAF_REDACT=1
check "codex updatedInput contains redaction" \
    '{"hook_event_name":"PreToolUse","tool_name":"Bash","tool_input":{"command":"echo AKIAIOSFODNN7EXAMPLE"},"permission_mode":"auto"}' \
    hookSpecificOutput.updatedInput.command '"echo __REDACTED__"' SAF_TOKEN=test-token SAF_REDACT=1
check "codex PROMPT secret still BLOCKS in redact mode (no rewrite)" \
    '{"hook_event_name":"UserPromptSubmit","prompt":"key AKIAIOSFODNN7EXAMPLE","permission_mode":"auto"}' \
    decision '"block"' SAF_TOKEN=test-token SAF_REDACT=1
check "cursor shell still BLOCKS in redact mode (no rewrite support)" \
    '{"hook_event_name":"beforeShellExecution","command":"echo AKIAIOSFODNN7EXAMPLE","cursor_version":"1"}' \
    permission '"deny"' SAF_TOKEN=test-token SAF_REDACT=1

echo "Malformed payloads must DENY, never be read as 'nothing to scan':"
check "BOM-prefixed payload -> deny" \
    "$(printf '\xEF\xBB\xBF{"hook_event_name":"beforeSubmitPrompt","prompt":"key AKIAIOSFODNN7EXAMPLE","cursor_version":"1"}')" \
    continue false SAF_TOKEN=test-token
check "not-json payload -> deny" \
    'this is not json' \
    continue false SAF_TOKEN=test-token
check "json array (not object) -> deny" \
    '["nope"]' \
    continue false SAF_TOKEN=test-token

printf '\n%d passed, %d failed\n' "$PASS" "$FAIL"
[[ "$FAIL" -eq 0 ]]
