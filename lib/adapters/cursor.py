"""Cursor adapter.

Events:
  beforeSubmitPrompt    the typed prompt      -> {"continue": …}
  beforeShellExecution  the agent's command   -> {"permission": …}
  beforeReadFile        a file on disk        -> {"permission": …}  (content read by the core)
  afterAgentResponse    the model's reply     -> observe-only, records output tokens
"""
from __future__ import annotations

import json

from .base import Adapter, canonical

PERMISSION_EVENTS = {"beforeShellExecution", "beforeReadFile"}
TRACK_EVENTS = {"afterAgentResponse"}


class CursorAdapter(Adapter):
    name = "cursor"

    def matches(self, payload, env):
        return bool(env.get("CURSOR_TRACE_ID")) or "cursor_version" in payload

    def normalize(self, payload):
        event = payload.get("hook_event_name") or "beforeSubmitPrompt"
        if event == "beforeShellExecution":
            text = payload.get("command") or ""
        elif event == "beforeReadFile":
            text = ""  # the core reads the file content
        elif event in TRACK_EVENTS:
            text = payload.get("response") or payload.get("text") or ""
        else:  # beforeSubmitPrompt
            text = payload.get("prompt") or ""
        return canonical(self.name, event, text, payload,
                         file_path=payload.get("file_path") or "")

    def event_kind(self, event):
        if event in TRACK_EVENTS:
            return "track"
        if event == "beforeReadFile":
            return "read_file"
        return "scan"

    def allow(self, event):
        if event in PERMISSION_EVENTS:
            return '{"permission":"allow"}'
        return '{"continue":true}'

    def deny(self, event, message):
        msg = json.dumps(message)
        if event in PERMISSION_EVENTS:
            agent = json.dumps("Blocked by SecureAIFlow: sensitive values detected.")
            return ('{"permission":"deny","user_message":%s,"agent_message":%s}' % (msg, agent), 0)
        return ('{"continue":false,"user_message":%s}' % msg, 0)
