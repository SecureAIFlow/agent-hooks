"""Antigravity adapter.

Antigravity is agentic: we gate PreToolUse (the blockable event) right before a
tool runs. run_command puts the command in ``toolCall.args.CommandLine``; other
tools (write_to_file, …) carry the secret in some string arg, so we scan them all.

  input   {"toolCall": {"name", "args": {...}}, "artifactDirectoryPath", …}
  reply   {"decision": "allow" | "deny", "reason"?: "…"}
"""
from __future__ import annotations

import json

from .base import Adapter, canonical, collect_strings


class AntigravityAdapter(Adapter):
    name = "antigravity"

    def matches(self, payload, env):
        return ("toolCall" in payload
                or "artifactDirectoryPath" in payload
                or "workspacePaths" in payload)

    def normalize(self, payload):
        tc = payload.get("toolCall") if isinstance(payload.get("toolCall"), dict) else {}
        tool = tc.get("name") or "tool"
        args = tc.get("args") if isinstance(tc.get("args"), dict) else {}
        cmd = args.get("CommandLine")
        if isinstance(cmd, str) and cmd.strip():
            text = cmd
        else:
            text = "\n".join(collect_strings(args))
        return canonical(self.name, f"PreToolUse:{tool}", text, payload)

    def allow(self, event):
        return '{"decision":"allow"}'

    def deny(self, event, message):
        return ('{"decision":"deny","reason":%s}' % json.dumps(message), 0)
