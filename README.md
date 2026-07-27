# SecureAIFlow agent hooks

Stops secrets from reaching an AI IDE's model — the typed prompt, agent shell
commands, file contents read as context, and agent tool calls — and records
token usage per integration. Supported: **Cursor, Antigravity, GitHub Copilot,
and Codex** — one shared core, one adapter per IDE.

## Install (one line)

Requires **Python 3** only. Pass the IDE you want: `cursor`, `antigravity`,
`copilot`, or `codex`.

**Windows** — PowerShell (no Git Bash needed):

```powershell
iex "& { $(irm https://raw.githubusercontent.com/SecureAIFlow/agent-hooks/main/install.ps1) } cursor"
```

**macOS / Linux:**

```bash
curl -fsSL https://raw.githubusercontent.com/SecureAIFlow/agent-hooks/main/install.sh | bash -s -- cursor
```

Already have the repo cloned? Run the platform shim directly:

```bat
install.bat cursor          REM Windows (cmd or PowerShell)
```
```bash
bash install.sh cursor      # macOS / Linux / Git Bash
```

The installer signs you in (browser), then activates that IDE's hook. It's
re-runnable and never touches your other hooks. **Then restart the IDE.**

> Codex only: after install, open the Codex CLI and run `/hooks` → **trust** the
> SecureAIFlow hook. Codex skips untrusted hooks, and every reinstall changes the
> hook's hash, so you must re-trust after each install.

## What to try in each IDE

Use the AWS example key `AKIAIOSFODNN7EXAMPLE` — it's reliably detected and not a
real secret. After a test, confirm it fired:
`Get-Content ~/.secureaiflow/hooks.log -Tail 5` (or `tail -5 ~/.secureaiflow/hooks.log`).

**Cursor** — gates the prompt *and* tools:
- Type in chat: `my aws key is AKIAIOSFODNN7EXAMPLE` → **prompt blocked**
- `run this command: echo AKIAIOSFODNN7EXAMPLE` → **command blocked**
- Open/ask about a file that contains a key → **file read blocked**
- Clean prompt like `refactor my login function` → passes through

**Codex** (OpenAI Codex CLI) — gates the prompt *and* tools:
- `my password is AKIAIOSFODNN7EXAMPLE` → **prompt blocked** (`UserPromptSubmit`)
- `run the command: echo AKIAIOSFODNN7EXAMPLE` → **tool blocked** (`PreToolUse`)
- (remember to `/hooks` → trust first)

**GitHub Copilot** (Copilot CLI / cloud agent — *not* the VS Code Chat panel) —
gates tools only, so ask it to *do* something:
- `run this command: echo AKIAIOSFODNN7EXAMPLE` → **tool blocked**
- `create a file test.txt with content KEY=AKIAIOSFODNN7EXAMPLE` → **tool blocked**
- Typing the secret as plain chat won't block (no prompt hook on Copilot).

**Antigravity** — gates tools only:
- `run the command: echo AKIAIOSFODNN7EXAMPLE` → **tool blocked**
- `create a file with KEY=AKIAIOSFODNN7EXAMPLE` → **write blocked**
- `search the web for AKIAIOSFODNN7EXAMPLE` → **web search blocked**
- A secret typed in chat reaches the model (Antigravity has no prompt hook); the
  model may repeat it back to *you*, but the tool paths that could exfiltrate it
  are all gated.

## Sign in again later

The token expires; the installer detects that and re-logs on the next install,
but you can also refresh it directly:

```powershell
iex "& { $(irm https://raw.githubusercontent.com/SecureAIFlow/agent-hooks/main/install.ps1) } --login"   # Windows (web)
install.bat --login          REM Windows (clone)
```
```bash
bash install.sh --login      # macOS / Linux / Git Bash
```

Either way the browser opens; on success the token is saved to
`~/.secureaiflow/credentials.json` (what the hook reads). Keep the terminal open
while you sign in.

## How it works

The whole hook is one Python process (`lib/saf_hook.py`). The IDE pipes its
event JSON on stdin; the core detects the client, normalizes the event, calls
SecureAIFlow, and replies in that IDE's dialect.

```
IDE ──event JSON──▶ lib/saf_hook.py ──POST /api/hooks/scan──▶ SecureAIFlow
IDE ◀──allow / deny / redact──────┘
```

One process, not a chain of shell subprocesses: on Windows that's ~0.6s per
event instead of ~7s, and it removes the shell-quoting, BOM, and MSYS
argv-encoding failures that plague bash-orchestrated hooks. Python stdlib only —
no `curl`/`jq`.

### What each IDE can gate

| IDE | Gates prompt? | Gates tools? | Reply dialect |
|---|---|---|---|
| **Cursor** | ✅ `beforeSubmitPrompt` | ✅ shell + **file reads** | `{"continue"/"permission": …}` |
| **Codex** | ✅ `UserPromptSubmit` | ✅ `PreToolUse` (all tools) | `{"decision":"block"}` / `hookSpecificOutput` |
| **Copilot** | ❌ | ✅ `preToolUse` (all tools) | `{"permissionDecision": …}` |
| **Antigravity** | ❌ | ✅ `PreToolUse` (exec/write/send tools) | `{"decision": …}` |

Only Cursor and Codex can gate a raw prompt; Copilot and Antigravity are
agentic and gate what the agent *does*. Read-only/interaction tools (view, grep,
list, ask-user…) are never gated — blocking those just breaks the agent.

### Redact in place (Codex + Copilot)

By default a secret in a tool call is **blocked**. With redact mode on, Codex
and Copilot instead **run the tool with the secret swapped out** — the agent
isn't interrupted, and the secret never reaches the model or the command:

```powershell
$env:SAF_REDACT="1"    # then install codex or copilot; unset afterwards
```
```bash
SAF_REDACT=1 bash install.sh codex     # or copilot
```

Codex uses `updatedInput`, Copilot uses `modifiedArgs`:

```
in:  {"command": "aws configure set key AKIAIOSFODNN7EXAMPLE", "region": "us-east-1"}
out: {"command": "aws configure set key __REDACTED__",         "region": "us-east-1"}
```

Cursor/Antigravity and all prompt events still block (nothing to rewrite). Off
by default — blocking is the safe baseline. Toggle later via `redact` in
`~/.secureaiflow/config.json`.

## Where each config is written

| IDE | Config | Scope override |
|---|---|---|
| cursor | `~/.cursor/hooks.json` | — |
| antigravity | `~/.gemini/config/hooks.json` | `SAF_ANTIGRAVITY_SCOPE=workspace` → `<cwd>/.agents/hooks.json` |
| copilot | `~/.copilot/hooks/secureaiflow.json` (also honors repo `.github/hooks/`) | — |
| codex | `~/.codex/hooks.json` (also honors repo `.codex/hooks.json`) | — |

### Fleet rollout

Point the same config at a machine-level path so a developer can't remove it
(e.g. Cursor: `C:\ProgramData\Cursor\hooks.json`,
`/Library/Application Support/Cursor/hooks.json`, `/etc/cursor/hooks.json`).
Codex/Copilot also support enterprise policy-hook directories.

## Configuration

Set via env at install time (baked into `~/.secureaiflow/config.json` where the
IDE can't pass env), or exported when you run the hook yourself.

| Variable | Default | Meaning |
|---|---|---|
| `SAF_API_URL` | `https://api.secureaiflow.com` | Backend. Dev: `http://localhost:8000` |
| `SAF_REDACT` | `0` | `1` → Codex/Copilot redact tool calls instead of blocking |
| `SAF_TOKEN` | — | Access token (overrides the credentials file) |
| `SAF_CONFIG_DIR` | `~/.secureaiflow` | Holds `credentials.json` + `config.json` |
| `SAF_TIMEOUT` | `10` | API timeout (seconds) |
| `SAF_MAX_FILE_BYTES` | `1048576` | `beforeReadFile` read cap (1 MB) |
| `SAF_LOG_FILE` | `~/.secureaiflow/hooks.log` | Log destination |
| `SAF_ANTIGRAVITY_SCOPE` | `global` | `workspace` → write `<cwd>/.agents/hooks.json` |
| `SAF_CLIENT` | auto | Force a client (`cursor`, …) |
| `SAF_FAIL_OPEN` | `0` | **Debug only.** `1` allows on error |

Precedence for the API URL: env → `config.json` → production default.

## Fails closed, on purpose

Backend unreachable, missing/expired token, unparseable payload, or any
unreadable verdict → the action is **denied**. A gate that fails open ships the
secrets it exists to stop. `SAF_FAIL_OPEN=1` inverts this for local debugging
only — never on a machine you're protecting.

## What it can and cannot do

Hooks **allow, deny, or (Codex/Copilot) redact** — most cannot rewrite a prompt.
A prompt with a secret is **blocked with an explanation**; the API also returns a
redacted version. True silent in-flight prompt redaction needs to own the request
path (a custom API base URL), which is how the Claude Code proxy works — the IDEs
here don't expose that for agent traffic. And a secret typed into chat can only
be stopped on the two IDEs with a prompt hook (Cursor, Codex).

## Usage tracking

Every scan records one `usage_events` row with `source` = the real client
(`cursor` / `codex` / …), input tokens (tiktoken estimate), and detection counts;
`afterAgentResponse` records output tokens. Cost is `$0` — hooks scan, they don't
proxy an LLM, so there's no inference cost to attribute. Token counts are
**estimates**; don't compare them to the provider-billed numbers from the Claude
Code proxy.

## Tests

```bash
python test/mock_saf.py &      # mock backend on :8899
bash test/run-tests.sh
```

43 cases: fail-closed paths, every IDE's events and reply dialect, redact-in-place
for Codex/Copilot, safe-tool skipping, the generic fallback, `beforeReadFile`
allow/deny, and payloads with quotes, backslashes, newlines, unicode, and a UTF-8
BOM — each a regression test for a real bug hit during development (a quote once
truncated the scanned text; MSYS once re-encoded non-ASCII to the Windows
codepage; PowerShell prepends a BOM; a quoted launcher path once echoed itself
instead of executing).

## Files

```
install.py                 the installer (config, launcher, sign-in, activation)
install.ps1                Windows PowerShell one-line shim  -> install.py
install.bat                Windows cmd one-line shim         -> install.py
install.sh                 macOS/Linux/Git-Bash one-line shim -> install.py
lib/saf_hook.py            hook core: parse → detect → scan → reply (IDE-agnostic)
lib/adapters/
  base.py                  Adapter base class + shared helpers
  cursor.py                Cursor      (beforeSubmitPrompt / Shell / ReadFile / afterResponse)
  antigravity.py           Antigravity (PreToolUse)
  copilot.py               GitHub Copilot (preToolUse, + modifiedArgs redaction)
  codex.py                 Codex       (UserPromptSubmit + PreToolUse, + updatedInput redaction)
  generic.py               fallback / all-dialects reply
bin/saf-hooks-login.sh     sign-in shim -> install.py --login
bin/run-hook.sh            legacy bash launcher (fallback)
test/                      mock backend + suite
```

Generated at install time (not committed): `~/.secureaiflow/config.json`,
`~/.secureaiflow/credentials.json`, `~/.secureaiflow/saf-hook.{cmd,sh}` (launcher),
`~/.secureaiflow/hooks.log`.

**Add an IDE**: drop a `lib/adapters/<name>.py` with an `Adapter` subclass
(`matches` / `normalize` / `allow` / `deny`, optional `event_kind` / `rewrite`),
register it in `lib/adapters/__init__.py`, and add a `write_<name>` in
`install.py`. The core never changes.
