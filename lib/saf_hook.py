#!/usr/bin/env python3
"""SecureAIFlow agent-hook core — the whole hook in one process.

The launcher (bin/run-hook.sh) or the IDE config execs this once. It parses the
payload, asks the adapter registry which IDE called, then drives that adapter:
read the text to scan, call the SecureAIFlow API, and reply in the IDE's dialect.
Per-IDE logic lives in lib/adapters/*.py; this file is IDE-agnostic.

One process instead of ~30 bash subshells (on Windows: ~0.6s vs ~7s) and no
curl/jq — http.client only.

Contract:
  argv[1]   hook name (reserved; logic keys off the event, not this)
  stdin     raw IDE JSON payload
  stdout    the IDE's expected response JSON
  exit 2    additionally denies for clients that read the exit code

FAILS CLOSED. Any failure to reach a verdict denies in the caller's dialect.
SAF_FAIL_OPEN=1 inverts this for local debugging only.
"""
from __future__ import annotations

import http.client
import json
import os
import sys
import time

# Make `adapters` importable however this script is launched.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import adapters  # noqa: E402
from adapters import GENERIC  # noqa: E402

# ── Config ────────────────────────────────────────────────────────────────
SAF_CONFIG_DIR = os.environ.get(
    "SAF_CONFIG_DIR", os.path.join(os.path.expanduser("~"), ".secureaiflow")
)
# Production backend by default. Devs override with SAF_API_URL, but IDEs don't
# pass env to a hook command — so the installer bakes the chosen URL into
# ~/.secureaiflow/config.json. Precedence: env > config.json > deployed default.
#
# To point the hook at a LOCAL backend during development, do ONE of:
#   1. Bake it via the installer (recommended — persists for every IDE):
#        SAF_API_URL=http://localhost:8000 bash install.sh <ide>
#      (writes {"api_url": "http://localhost:8000"} to ~/.secureaiflow/config.json)
#   2. Edit ~/.secureaiflow/config.json directly and set "api_url".
#   3. Export SAF_API_URL when running the hook yourself (won't reach the IDE):
#        SAF_API_URL=http://localhost:8000 python lib/saf_hook.py x
# Revert to production by re-running the installer with no SAF_API_URL set.
SAF_DEFAULT_API_URL = "https://api.secureaiflow.com"


def _resolve_api_url() -> str:
    env = os.environ.get("SAF_API_URL")
    if env:
        return env.rstrip("/")
    try:
        with open(os.path.join(SAF_CONFIG_DIR, "config.json"), "rb") as fh:
            url = json.loads(fh.read().decode("utf-8-sig")).get("api_url")
            if url:
                return str(url).rstrip("/")
    except Exception:
        pass
    return SAF_DEFAULT_API_URL


def _resolve_redact() -> bool:
    """Redact-in-place instead of block, for IDEs whose tool hooks can rewrite
    args (Codex updatedInput / Copilot modifiedArgs). Precedence: env > config >
    off. Off by default — blocking is the safe baseline; opt in to redact.
      enable:  SAF_REDACT=1 bash install.sh <ide>   (baked into config.json)
    """
    env = os.environ.get("SAF_REDACT")
    if env is not None:
        return env == "1" or env.strip().lower() == "true"
    try:
        with open(os.path.join(SAF_CONFIG_DIR, "config.json"), "rb") as fh:
            return bool(json.loads(fh.read().decode("utf-8-sig")).get("redact", False))
    except Exception:
        return False


SAF_API_URL = _resolve_api_url()
SAF_REDACT = _resolve_redact()
SAF_TIMEOUT = float(os.environ.get("SAF_TIMEOUT", "10"))
SAF_MAX_FILE_BYTES = int(os.environ.get("SAF_MAX_FILE_BYTES", str(1024 * 1024)))
SAF_FAIL_OPEN = os.environ.get("SAF_FAIL_OPEN", "0") == "1"
SAF_LOG_FILE = os.environ.get("SAF_LOG_FILE", os.path.join(SAF_CONFIG_DIR, "hooks.log"))

# Read-only / interaction tools we never gate (blocking them just breaks the
# agent). Matched case-insensitively against the tool name in a preToolUse event.
SAFE_TOOLS = {
    "ask_user", "askuserquestion", "ask",
    "view", "read", "read_file", "cat_file", "open",
    "grep", "search", "codebase_search", "file_search",
    "glob", "list", "list_dir", "ls",
    "update_todo", "todowrite", "update_plan", "todo",
    "notebook_read",
    # Antigravity read-only / interaction tools
    "view_file", "find_by_name", "grep_search", "ask_question", "list_permissions",
}


def log(msg: str) -> None:
    try:
        os.makedirs(os.path.dirname(SAF_LOG_FILE), exist_ok=True)
        with open(SAF_LOG_FILE, "a", encoding="utf-8") as fh:
            fh.write(f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} "
                     f"[saf_hook] {msg}\n")
    except Exception:
        pass


# ── Token ─────────────────────────────────────────────────────────────────
def load_token() -> str:
    tok = os.environ.get("SAF_TOKEN", "")
    if tok:
        return tok
    try:
        with open(os.path.join(SAF_CONFIG_DIR, "credentials.json"), "rb") as fh:
            return json.loads(fh.read().decode("utf-8-sig")).get("access_token", "") or ""
    except Exception:
        return ""


# ── API (http.client — ~290ms lighter to import than urllib) ──────────────
class HttpError(Exception):
    def __init__(self, status: int):
        self.status = status
        super().__init__(f"HTTP {status}")


def _split_url(url: str) -> tuple:
    """(is_https, host, port) from SAF_API_URL — no urllib.parse import."""
    https = url.startswith("https://")
    rest = url.split("://", 1)[1] if "://" in url else url
    rest = rest.split("/", 1)[0]
    if ":" in rest:
        host, port_s = rest.rsplit(":", 1)
        return https, host, int(port_s)
    return https, rest, (443 if https else 80)


def _post(path: str, body: dict, token: str) -> dict:
    https, host, port = _split_url(SAF_API_URL)
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    cls = http.client.HTTPSConnection if https else http.client.HTTPConnection
    conn = cls(host, port, timeout=SAF_TIMEOUT)
    try:
        conn.request("POST", path, body=data, headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        })
        resp = conn.getresponse()
        status = resp.status
        raw = resp.read().decode("utf-8", "replace")
    finally:
        conn.close()
    if status >= 400:
        raise HttpError(status)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}


def api_scan(text: str, client: str, event: str, token: str) -> dict:
    return _post("/api/hooks/scan", {
        "text": text, "client": client, "event": event, "file_extension": "generic",
    }, token)


def api_track(text: str, client: str, event: str, token: str) -> None:
    try:
        _post("/api/hooks/track", {
            "text": text, "client": client, "event": event, "role": "output",
        }, token)
    except Exception as exc:
        log(f"track failed (non-fatal): {exc}")


# ── Emit via the matched adapter ──────────────────────────────────────────
def apply_replacements(obj, pairs):
    """Recursively replace each secret substring with its placeholder in every
    string nested in obj (a copy of the tool args)."""
    if isinstance(obj, str):
        for p in pairs:
            find, repl = p.get("find"), p.get("replace")
            if find:
                obj = obj.replace(find, repl if repl is not None else "")
        return obj
    if isinstance(obj, dict):
        return {k: apply_replacements(v, pairs) for k, v in obj.items()}
    if isinstance(obj, list):
        return [apply_replacements(v, pairs) for v in obj]
    return obj


def emit_allow(adapter, event: str) -> int:
    sys.stdout.write(adapter.allow(event) + "\n")
    return 0


def emit_deny(adapter, event: str, message: str) -> int:
    line, code = adapter.deny(event, message)
    sys.stdout.write(line + "\n")
    return code


def fail_closed(adapter, event: str, reason: str) -> int:
    if SAF_FAIL_OPEN:
        log(f"FAIL-OPEN (debug): {reason}")
        return emit_allow(adapter, event)
    log(f"FAIL-CLOSED: {reason}")
    return emit_deny(adapter, event, f"SecureAIFlow could not verify this: {reason}")


# ── Main ──────────────────────────────────────────────────────────────────
def main() -> int:
    # Before an adapter is known, speak the generic (all-dialects) shape.
    def early_deny(reason: str) -> int:
        if SAF_FAIL_OPEN:
            return emit_allow(GENERIC, "")
        log(f"FAIL-CLOSED (early): {reason}")
        return emit_deny(GENERIC, "", f"SecureAIFlow could not verify this action: {reason}")

    raw = sys.stdin.buffer.read()
    if not raw:
        return early_deny("empty payload on stdin")
    try:
        payload = json.loads(raw.decode("utf-8-sig"))  # tolerate a Windows BOM
    except Exception:
        return early_deny("unreadable payload from the IDE")
    if not isinstance(payload, dict):
        return early_deny("payload is not a JSON object")

    adapter = adapters.detect(payload)
    canonical = adapter.normalize(payload)
    event = canonical["event"]
    kind = adapter.event_kind(event)
    log(f"client={adapter.name} event={event}")

    # Never gate read-only / interaction tools. Blocking "ask the user a
    # question", a file read, or a search doesn't stop exfiltration — it just
    # breaks the agent (e.g. it can't ask for confirmation). We only scan tools
    # that EXECUTE, WRITE, or SEND. The tool name is the suffix of the event.
    if ":" in event:
        tool = event.split(":", 1)[1].strip().lower()
        if tool in SAFE_TOOLS:
            log(f"skip safe tool={tool}")
            return emit_allow(adapter, event)

    # read_file: the content to scan is the file on disk, not the payload.
    if kind == "read_file":
        fp = canonical["file_path"]
        if not fp or not os.path.isfile(fp):
            return emit_allow(adapter, event)
        try:
            with open(fp, "rb") as fh:
                canonical["text"] = fh.read(SAF_MAX_FILE_BYTES).decode("utf-8", "replace")
        except Exception as exc:
            log(f"read_file unreadable {fp}: {exc}")
            return emit_allow(adapter, event)

    text = canonical["text"]
    if not text:
        return emit_allow(adapter, event)

    token = load_token()
    if not token:
        return fail_closed(adapter, event, "not signed in (run: saf-hooks login)")

    # track: record output tokens, never block.
    if kind == "track":
        api_track(text, adapter.name, event, token)
        return emit_allow(adapter, event)

    # scan (default, and read_file after loading the content).
    try:
        result = api_scan(text, adapter.name, event, token)
    except HttpError as exc:
        if exc.status in (401, 403):
            return fail_closed(adapter, event, "session expired (run: saf-hooks login)")
        return fail_closed(adapter, event, f"SecureAIFlow returned HTTP {exc.status}")
    except Exception as exc:
        return fail_closed(adapter, event, f"SecureAIFlow is unreachable ({type(exc).__name__})")

    decision = result.get("decision")
    if decision not in ("allow", "deny"):
        return fail_closed(adapter, event, "unexpected response from SecureAIFlow")
    if decision == "allow":
        log(f"decision=allow event={event}")
        return emit_allow(adapter, event)

    # Redact-in-place: if enabled and this is a tool call whose IDE can rewrite
    # args, swap the secret out and ALLOW instead of blocking.
    message = result.get("message") or "SecureAIFlow blocked this: sensitive values detected."
    replacements = result.get("replacements") or []
    if SAF_REDACT and canonical.get("tool_args") is not None and replacements:
        modified = apply_replacements(canonical["tool_args"], replacements)
        rw = adapter.rewrite(event, modified)
        if rw is not None:
            line, code = rw
            sys.stdout.write(line + "\n")
            log(f"decision=redact detections={result.get('detections')} event={event}")
            return code

    log(f"decision=deny detections={result.get('detections')} event={event}")
    return emit_deny(adapter, event, message)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # never crash without a verdict
        log(f"unhandled: {exc}")
        if os.environ.get("SAF_FAIL_OPEN", "0") == "1":
            sys.stdout.write(GENERIC.allow("") + "\n")
            sys.exit(0)
        line, code = GENERIC.deny("", "SecureAIFlow could not verify this action: internal error")
        sys.stdout.write(line + "\n")
        sys.exit(code)
