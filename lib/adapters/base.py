"""Adapter base class + shared helpers.

An adapter encapsulates ONE IDE's hook contract: how to recognize its payload,
how to read the text to scan out of it, and how to phrase allow/deny in that
IDE's own response dialect. The core (saf_hook.py) stays IDE-agnostic and just
drives whichever adapter matched.
"""
from __future__ import annotations


def collect_strings(obj) -> list:
    """Every string nested anywhere in obj.

    Tool-call args differ per IDE and the docs often leave their exact shape
    unspecified, so to be safe we scan every string value — whichever field
    holds the secret is caught.
    """
    if isinstance(obj, str):
        return [obj]
    if isinstance(obj, dict):
        out = []
        for v in obj.values():
            out.extend(collect_strings(v))
        return out
    if isinstance(obj, list):
        out = []
        for v in obj:
            out.extend(collect_strings(v))
        return out
    return []


def canonical(client: str, event: str, text, payload: dict, file_path: str = "",
              tool_args=None) -> dict:
    """The IDE-agnostic envelope the core works with.

    tool_args is the ORIGINAL structured tool arguments (Copilot toolArgs / Codex
    tool_input / Antigravity toolCall.args), kept so the core can redact secrets
    in place and hand the IDE rewritten args instead of blocking. None for prompt
    events.
    """
    return {
        "event": event,
        "client": client,
        "text": text if isinstance(text, str) else str(text),
        "file_path": file_path if isinstance(file_path, str) else "",
        "tool_args": tool_args,
        "user_email": payload.get("user_email") or "",
        "model": payload.get("model") or "",
        "conversation_id": payload.get("conversation_id") or payload.get("conversationId") or "",
    }


class Adapter:
    name = "base"

    # ── detection ──
    def matches(self, payload: dict, env) -> bool:
        return False

    # ── payload -> canonical envelope ──
    def normalize(self, payload: dict) -> dict:
        raise NotImplementedError

    # ── how the core should treat this event ──
    #   "scan"      block on secret (default)
    #   "track"     record output tokens, never block
    #   "read_file" read canonical['file_path'] from disk, then scan
    def event_kind(self, event: str) -> str:
        return "scan"

    # ── response dialect ──
    def allow(self, event: str) -> str:
        raise NotImplementedError

    def deny(self, event: str, message: str) -> tuple:
        """Return (json_line, exit_code)."""
        raise NotImplementedError

    def rewrite(self, event: str, modified_args) -> tuple:
        """Redact-in-place: allow the tool call with `modified_args` substituted.

        Return (json_line, exit_code), or None if this IDE/event can't rewrite —
        the core then falls back to deny. Default: not supported.
        """
        return None
