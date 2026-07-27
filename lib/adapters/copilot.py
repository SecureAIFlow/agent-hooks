"""GitHub Copilot adapter (Copilot CLI / cloud agent — not the VS Code Chat panel).

preToolUse fires before any agent tool runs. ``toolArgs`` shape is unspecified,
so we scan every string it contains.

  input   {"toolName": "bash", "toolArgs": {...}}
  reply   {"permissionDecision": "allow" | "deny", "permissionDecisionReason"?: "…"}

(Copilot also supports ``modifiedArgs`` to rewrite tool input — a future path to
redact-in-place instead of block.)
"""
from __future__ import annotations

import json

from .base import Adapter, canonical, collect_strings


class CopilotAdapter(Adapter):
    name = "copilot"

    def matches(self, payload, env):
        return "toolName" in payload and "toolArgs" in payload

    def normalize(self, payload):
        tool = payload.get("toolName") or "tool"
        args = payload.get("toolArgs")
        text = "\n".join(collect_strings(args))
        return canonical(self.name, f"preToolUse:{tool}", text, payload, tool_args=args)

    def allow(self, event):
        return '{"permissionDecision":"allow"}'

    def deny(self, event, message):
        # deny requires permissionDecisionReason; the core's exit 2 also denies.
        return ('{"permissionDecision":"deny","permissionDecisionReason":%s}' % json.dumps(message), 0)

    def rewrite(self, event, modified_args):
        # Allow the tool but with the secret redacted out of its args.
        return ('{"permissionDecision":"allow","modifiedArgs":%s}'
                % json.dumps(modified_args, ensure_ascii=False), 0)
