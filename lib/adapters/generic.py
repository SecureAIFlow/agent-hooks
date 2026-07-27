"""Fallback adapter for an unrecognized client.

Fail-closed policy: if we cannot tell which IDE called us, we cannot speak its
one true dialect, so we reply with EVERY known shape in a single object (each
IDE ignores the keys it doesn't know) and return exit code 2 for clients that
read the code instead. Also used by the core for pre-detection failures.
"""
from __future__ import annotations

import json

from .base import Adapter, canonical


class GenericAdapter(Adapter):
    name = "generic"

    def normalize(self, payload):
        text = payload.get("prompt") or payload.get("command") or payload.get("text") or ""
        return canonical(self.name, "unknown", text, payload)

    def allow(self, event):
        return '{"continue":true,"permission":"allow","decision":"allow","permissionDecision":"allow"}'

    def deny(self, event, message):
        # Every known dialect in one object; each IDE ignores the keys it does
        # not know. Exit code 2 is the universal fallback (Codex/Copilot honor
        # it). `decision` stays "deny" for Antigravity; Codex's UserPromptSubmit
        # wants "block" — a value conflict we can't win in JSON, so exit 2 covers
        # that case. hookSpecificOutput covers Codex PreToolUse.
        msg = json.dumps(message)
        return ('{"continue":false,"permission":"deny","decision":"deny",'
                '"permissionDecision":"deny","user_message":%s,"reason":%s,'
                '"permissionDecisionReason":%s,'
                '"hookSpecificOutput":{"hookEventName":"PreToolUse",'
                '"permissionDecision":"deny","permissionDecisionReason":%s}}'
                % (msg, msg, msg, msg), 2)
