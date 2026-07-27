"""Adapter registry: recognize the calling IDE and hand back its adapter.

Add an IDE = drop a new module here with an Adapter subclass and register it in
ADAPTERS. The core never changes.
"""
from __future__ import annotations

import os

from .antigravity import AntigravityAdapter
from .base import Adapter
from .codex import CodexAdapter
from .copilot import CopilotAdapter
from .cursor import CursorAdapter
from .generic import GenericAdapter

# Detection order. The signals are mutually exclusive, but cursor is checked
# first because it can be forced by an env var alone.
ADAPTERS = [CursorAdapter(), AntigravityAdapter(), CopilotAdapter(), CodexAdapter()]
GENERIC = GenericAdapter()
_BY_NAME = {a.name: a for a in ADAPTERS + [GENERIC]}


def detect(payload: dict, env=None) -> Adapter:
    """Return the adapter for this payload. SAF_CLIENT forces one by name."""
    env = os.environ if env is None else env
    forced = env.get("SAF_CLIENT")
    if forced:
        return _BY_NAME.get(forced, GENERIC)
    for adapter in ADAPTERS:
        if adapter.matches(payload, env):
            return adapter
    return GENERIC
