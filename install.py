#!/usr/bin/env python3
"""SecureAIFlow agent-hooks installer — one script for every OS and IDE.

    python install.py <ide>          ide: cursor | antigravity | copilot | codex
    python install.py --login        sign in only (refresh the token)

Called by the platform shims (install.bat on Windows, install.sh on macOS/
Linux) so the user always has a one-line install. Steps, in order:

  1. bake backend URL + redact mode into ~/.secureaiflow/config.json
  2. write the launcher (~/.secureaiflow/saf-hook.cmd | .sh)
  3. sign in (browser flow) unless the stored token is still valid
  4. write THAT IDE's hook config, in its own format and location

Env overrides: SAF_API_URL (default production), SAF_REDACT=1,
SAF_ANTIGRAVITY_SCOPE=workspace.
"""
from __future__ import annotations

import base64
import http.client
import json
import os
import sys
import time
import webbrowser

IDES = ("cursor", "antigravity", "copilot", "codex")
DEFAULT_API_URL = "https://api.secureaiflow.com"

API_URL = (os.environ.get("SAF_API_URL") or DEFAULT_API_URL).rstrip("/")
REDACT = (os.environ.get("SAF_REDACT") or "0").strip().lower() in ("1", "true")
IS_WINDOWS = os.name == "nt"

# HOME can be overridden (tests, custom setups); expanduser otherwise.
def _resolve_home() -> str:
    home = os.environ.get("HOME")
    if home and IS_WINDOWS and home.startswith("/"):
        # Git Bash exports MSYS-style HOME (/c/Users/x) which Windows Python
        # cannot open. Translate /c/... -> C:\...; anything else, ignore it.
        parts = home.strip("/").split("/")
        if parts and len(parts[0]) == 1 and parts[0].isalpha():
            cand = parts[0].upper() + ":\\" + "\\".join(parts[1:])
            if os.path.isdir(cand):
                return cand
        home = None
    if home and os.path.isdir(home):
        return home
    return os.path.expanduser("~")


HOME = _resolve_home()
CONFIG_DIR = os.path.join(HOME, ".secureaiflow")
CRED_FILE = os.path.join(CONFIG_DIR, "credentials.json")

ROOT = os.path.dirname(os.path.abspath(__file__))
CORE = os.path.join(ROOT, "lib", "saf_hook.py")


def say(msg: str) -> None:
    print(f"  {msg}")


def die(msg: str) -> None:
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(1)


def _read_json(path: str) -> dict:
    try:
        with open(path, "rb") as fh:
            data = json.loads(fh.read().decode("utf-8-sig"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write_json(path: str, cfg: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, indent=2)


# ── HTTP (stdlib only, same approach as the hook core) ────────────────────
def _post(path: str, body: dict, timeout: float = 15.0) -> tuple[int, dict]:
    url = API_URL
    https = url.startswith("https://")
    rest = url.split("://", 1)[1] if "://" in url else url
    rest = rest.split("/", 1)[0]
    if ":" in rest:
        host, port_s = rest.rsplit(":", 1)
        port = int(port_s)
    else:
        host, port = rest, (443 if https else 80)
    cls = http.client.HTTPSConnection if https else http.client.HTTPConnection
    conn = cls(host, port, timeout=timeout)
    try:
        conn.request("POST", path, body=json.dumps(body).encode("utf-8"),
                     headers={"Content-Type": "application/json"})
        resp = conn.getresponse()
        raw = resp.read().decode("utf-8", "replace")
        try:
            return resp.status, json.loads(raw)
        except json.JSONDecodeError:
            return resp.status, {}
    finally:
        conn.close()


# ── Step 1: config.json ───────────────────────────────────────────────────
def write_config() -> None:
    cfg = _read_json(os.path.join(CONFIG_DIR, "config.json"))
    cfg["api_url"] = API_URL
    cfg["redact"] = REDACT
    _write_json(os.path.join(CONFIG_DIR, "config.json"), cfg)
    say(f"API:     {API_URL}")
    if REDACT:
        say("Redact:  ON (Codex/Copilot tool calls redact in place instead of blocking)")


# ── Step 2: launcher ──────────────────────────────────────────────────────
def write_launcher() -> str:
    """Single-path launcher: no nested quotes for IDE shells to mangle, and it
    self-logs so a broken IDE invocation is visible in hooks.log."""
    py = sys.executable
    if IS_WINDOWS:
        path = os.path.join(CONFIG_DIR, "saf-hook.cmd")
        log_path = os.path.join(CONFIG_DIR, "hooks.log")
        content = ("@echo off\r\n"
                   f">>\"{log_path}\" echo [launcher] invoked\r\n"
                   f"\"{py}\" \"{CORE}\" %*\r\n")
        os.makedirs(CONFIG_DIR, exist_ok=True)
        with open(path, "w", encoding="ascii", newline="") as fh:
            fh.write(content)
    else:
        path = os.path.join(CONFIG_DIR, "saf-hook.sh")
        content = ("#!/bin/sh\n"
                   'echo "[launcher] invoked" >> "$HOME/.secureaiflow/hooks.log"\n'
                   f'exec "{py}" "{CORE}" "$@"\n')
        os.makedirs(CONFIG_DIR, exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(content)
        os.chmod(path, 0o755)
    return os.path.normpath(path)


def launcher_cmd(launcher: str) -> str:
    """Bare path when space-free (executes in every shell); quoted otherwise."""
    return launcher if " " not in launcher else f'"{launcher}"'


# ── Step 3: sign in ───────────────────────────────────────────────────────
def token_valid() -> bool:
    """True when credentials.json holds a JWT whose exp is still in the future.
    A stale token is worse than none: the hook would fail closed on every call
    while the installer claims you're signed in."""
    tok = _read_json(CRED_FILE).get("access_token", "")
    try:
        part = tok.split(".")[1]
        part += "=" * (-len(part) % 4)
        exp = json.loads(base64.urlsafe_b64decode(part)).get("exp", 0)
        return exp > time.time() + 60
    except Exception:
        return False


def login() -> bool:
    # Preferred client type is "hooks" (shows hook-specific wording on the
    # sign-in page). Backends deployed before that type existed reject it with
    # 422 — fall back to "vscode" so installs keep working until the redeploy.
    client_type = "hooks"
    status, initiate = -1, {}
    try:
        status, initiate = _post("/api/auth/extension/initiate",
                                 {"client_type": client_type, "client_version": "saf-hooks/0.2.0"})
        if status == 422:
            client_type = "vscode"
            status, initiate = _post("/api/auth/extension/initiate",
                                     {"client_type": client_type, "client_version": "saf-hooks/0.2.0"})
    except Exception as exc:
        die(f"could not reach the backend at {API_URL} ({exc})")
    code, auth_url = initiate.get("code"), initiate.get("auth_url")
    if status != 200 or not code or not auth_url:
        die(f"unexpected response from the backend (HTTP {status}): {initiate}")

    print()
    say("1. Open this URL and sign in:")
    say(f"   {auth_url}")
    say("2. Waiting for you to finish (5 minutes max). Leave this window open.")
    print()
    try:
        webbrowser.open(auth_url)
    except Exception:
        pass

    deadline = time.time() + 300
    attempt = 0
    while time.time() < deadline:
        time.sleep(3)
        attempt += 1
        try:
            status, resp = _post("/api/auth/extension/token",
                                 {"code": code, "client_type": client_type})
        except Exception:
            status, resp = -1, {}
        if status == 200 and resp.get("access_token"):
            _write_json(CRED_FILE, resp)
            try:
                os.chmod(CRED_FILE, 0o600)
            except Exception:
                pass
            print()
            say(f"Signed in as {(resp.get('user') or {}).get('email', '?')}")
            say(f"Token saved to {CRED_FILE}")
            return True
        sys.stdout.write(".")
        sys.stdout.flush()
        if attempt % 20 == 0:
            print(" still waiting")
    print()
    say("Timed out after 5 minutes. The code is single-use — re-run to get a fresh URL.")
    return False


def ensure_signed_in() -> None:
    if token_valid():
        say(f"Already signed in, token still valid ({CRED_FILE}).")
        return
    if os.path.isfile(CRED_FILE):
        os.remove(CRED_FILE)
        say("Stored session missing or expired — signing in again.")
    say("Step 1/2 — sign in:")
    if not login():
        die("sign-in did not complete — hook NOT activated. Re-run the installer.")


# ── Step 4: per-IDE configs ───────────────────────────────────────────────
def _is_mine(command: str) -> bool:
    return any(m in (command or "") for m in ("saf_hook.py", "saf-hook", "secureaiflow"))


def write_cursor(launcher: str) -> None:
    path = os.path.join(HOME, ".cursor", "hooks.json")
    py = sys.executable
    # Cursor parses its command with proper quoting — full command works and
    # avoids the launcher indirection.
    cmd = f'"{py}" "{CORE}" saf-guard-prompt'
    events = ["beforeSubmitPrompt", "beforeShellExecution", "beforeReadFile", "afterAgentResponse"]
    cfg = _read_json(path)
    cfg.setdefault("version", 1)
    hooks = cfg.setdefault("hooks", {})
    entry = {"command": cmd, "timeout": 15, "failClosed": True}
    for ev in events:
        kept = [h for h in hooks.get(ev, []) if not _is_mine(h.get("command", ""))]
        kept.append(entry)
        hooks[ev] = kept
    _write_json(path, cfg)
    say(f"Config:  {path}")
    say(f"Events:  {', '.join(events)}")


def write_antigravity(launcher: str) -> None:
    # Docs: ~/.gemini/config/hooks.json (global) or <workspace>/.agents/.
    # Clean configs we once wrote into the app DATA dir by mistake.
    for stray in (os.path.join(HOME, ".gemini", "antigravity", "hooks.json"),
                  os.path.join(HOME, ".antigravity", "hooks.json")):
        cfg = _read_json(stray)
        if "secureaiflow" in cfg:
            cfg.pop("secureaiflow")
            if cfg:
                _write_json(stray, cfg)
            else:
                os.remove(stray)
            say(f"Cleaned: {stray}")

    if os.environ.get("SAF_ANTIGRAVITY_SCOPE") == "workspace":
        path = os.path.join(os.getcwd(), ".agents", "hooks.json")
    else:
        path = os.path.join(HOME, ".gemini", "config", "hooks.json")
    matcher = ("run_command|write_to_file|replace_file_content|"
               "multi_replace_file_content|search_web|read_url_content|"
               "send_message|generate_image")
    cfg = _read_json(path)
    cfg["secureaiflow"] = {
        "enabled": True,
        "PreToolUse": [
            {"matcher": matcher,
             "hooks": [{"type": "command", "command": launcher_cmd(launcher), "timeout": 15}]}
        ],
    }
    _write_json(path, cfg)
    say(f"Config:  {path}")
    say(f"Matcher: {matcher}")


def write_copilot(launcher: str) -> None:
    path = os.path.join(HOME, ".copilot", "hooks", "secureaiflow.json")
    py = sys.executable
    bash_cmd = f'"{py}" "{CORE}" saf-guard'
    ps_cmd = f'& "{py}" "{CORE}" saf-guard'
    cfg = _read_json(path)
    cfg.setdefault("version", 1)
    hooks = cfg.setdefault("hooks", {})
    kept = [h for h in hooks.get("preToolUse", [])
            if not _is_mine(h.get("bash", "") + h.get("powershell", "") + h.get("command", ""))]
    kept.append({"type": "command", "bash": bash_cmd, "powershell": ps_cmd, "timeoutSec": 15})
    hooks["preToolUse"] = kept
    _write_json(path, cfg)
    say(f"Config:  {path}")
    say("Hook:    preToolUse (every tool)")


def write_codex(launcher: str) -> None:
    path = os.path.join(HOME, ".codex", "hooks.json")
    # BARE launcher path: Codex (Windows) evaluates the command with PowerShell
    # semantics, where a quoted path alone is a string literal — it echoes
    # itself instead of executing, so the hook "completes" without running.
    cmd = launcher_cmd(launcher)
    cfg = _read_json(path)
    hooks = cfg.setdefault("hooks", {})

    def keep(groups):
        return [g for g in groups
                if not any(_is_mine(h.get("command", "")) for h in g.get("hooks", []))]

    handler = {"type": "command", "command": cmd, "statusMessage": "SecureAIFlow scan"}
    ups = keep(hooks.get("UserPromptSubmit", []))
    ups.append({"hooks": [dict(handler)]})
    hooks["UserPromptSubmit"] = ups
    ptu = keep(hooks.get("PreToolUse", []))
    ptu.append({"matcher": "*", "hooks": [dict(handler)]})
    hooks["PreToolUse"] = ptu
    _write_json(path, cfg)
    say(f"Config:  {path}")
    say("Events:  UserPromptSubmit + PreToolUse (matcher * = all tools)")
    say("ACTION:  in the Codex CLI run /hooks and TRUST the SecureAIFlow hook")


WRITERS = {
    "cursor": write_cursor,
    "antigravity": write_antigravity,
    "copilot": write_copilot,
    "codex": write_codex,
}


def main() -> None:
    arg = (sys.argv[1] if len(sys.argv) > 1 else "cursor").strip().lower()

    if arg in ("--login", "login"):
        print("SecureAIFlow agent-hooks - sign in\n")
        if os.path.isfile(CRED_FILE):
            os.remove(CRED_FILE)
        sys.exit(0 if login() else 1)

    if arg not in IDES:
        die(f"unsupported ide '{arg}'. Use: {' | '.join(IDES)}")
    if not os.path.isfile(CORE):
        die("cannot find lib/saf_hook.py next to install.py")

    print(f"SecureAIFlow agent-hooks installer  (ide={arg})\n")
    say(f"Python:  {sys.executable}")
    say(f"Core:    {CORE}")
    write_config()
    launcher = write_launcher()

    print()
    ensure_signed_in()

    print()
    say(f"Step 2/2 — activating {arg} hook:")
    WRITERS[arg](launcher)
    print(f"\nDone. Restart {arg} to load the hook.")


if __name__ == "__main__":
    main()
