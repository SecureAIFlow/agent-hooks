"""Codex adapter (OpenAI Codex CLI).

Codex gates TWO events, each with its own dialect:

  UserPromptSubmit  the typed prompt          {"prompt": "..."}
      block ->  {"decision":"block","reason":"..."}    (no output = allow)

  PreToolUse        an agent tool call        {"tool_name":"Bash","tool_input":{"command":"..."}}
      deny  ->  {"hookSpecificOutput":{"hookEventName":"PreToolUse",
                 "permissionDecision":"deny","permissionDecisionReason":"..."}}

We only ever BLOCK on a secret; on allow we emit `{}` (no opinion) so Codex's
own permission flow is untouched. Which event fired is read from
``hook_event_name``. Exit code 2 also blocks, which is our fallback.
"""
from __future__ import annotations

import json

from .base import Adapter, canonical, collect_strings


class CodexAdapter(Adapter):
    name = "codex"

    def matches(self, payload, env):
        return "permission_mode" in payload

    def normalize(self, payload):
        event = payload.get("hook_event_name") or "UserPromptSubmit"
        tool_args = None
        if event == "PreToolUse":
            tool = payload.get("tool_name") or "tool"
            ti = payload.get("tool_input") if isinstance(payload.get("tool_input"), dict) else {}
            tool_args = ti
            cmd = ti.get("command")
            if isinstance(cmd, str) and cmd.strip():
                text = cmd
            else:
                text = "\n".join(collect_strings(ti))
            event = f"PreToolUse:{tool}"
        else:  # UserPromptSubmit (or any prompt-ish event)
            text = payload.get("prompt") or ""
        return canonical(self.name, event, text, payload, tool_args=tool_args)

    def allow(self, event):
        # No opinion — let Codex proceed with its normal flow. We only block.
        return "{}"

    def deny(self, event, message):
        msg = json.dumps(message)
        if event.startswith("PreToolUse"):
            return ('{"hookSpecificOutput":{"hookEventName":"PreToolUse",'
                    '"permissionDecision":"deny","permissionDecisionReason":%s}}' % msg, 0)
        return ('{"decision":"block","reason":%s}' % msg, 0)

    def rewrite(self, event, modified_args):
        # Only PreToolUse can rewrite (updatedInput). UserPromptSubmit is
        # block-only, so return None there and let the core deny.
        if not event.startswith("PreToolUse"):
            return None
        return ('{"hookSpecificOutput":{"hookEventName":"PreToolUse",'
                '"permissionDecision":"allow","updatedInput":%s}}'
                % json.dumps(modified_args, ensure_ascii=False), 0)
