"""Map a Hermes agent run onto Linear agent activities + session correlation.

The pure mapping helpers (run-event -> (activity_type, body)) are unit-tested.
The ``SessionRegistry`` bridges Hermes session ids to the Linear Agent Session /
issue they belong to — necessary because Hermes' per-step hooks carry only a
``session_id`` string, not the originating Linear issue (see DESIGN.md "hook
correlation").
"""
from __future__ import annotations

from typing import Dict, Optional, Tuple

try:
    from . import graphql  # package context (Hermes loads `linear` as a package)
except ImportError:  # pragma: no cover - flat import in standalone unit tests
    import graphql


def thought(text: str) -> Tuple[str, str]:
    return graphql.THOUGHT, text


def tool_action(tool_name: str, args: Optional[dict] = None) -> Tuple[str, str]:
    detail = ""
    if args:
        # keep it short + non-sensitive: just the arg keys
        detail = " (" + ", ".join(sorted(str(k) for k in args)) + ")" if isinstance(args, dict) else ""
    return graphql.ACTION, f"Using **{tool_name}**{detail}"


def final_response(text: str) -> Tuple[str, str]:
    return graphql.RESPONSE, text


def error_activity(message: str) -> Tuple[str, str]:
    return graphql.ERROR, message


def ack_thought() -> Tuple[str, str]:
    """The placeholder activity posted immediately on session `created` so Linear's
    <10s first-activity contract is met regardless of agent latency."""
    return graphql.THOUGHT, "On it — looking into this now."


class SessionRegistry:
    """session_key/session_id -> binding for an open Linear Agent Session."""

    def __init__(self):
        self._by_key: Dict[str, Dict[str, str]] = {}

    def bind(self, key: str, *, agent_session_id: str, issue_id: str) -> None:
        self._by_key[key] = {"agent_session_id": agent_session_id, "issue_id": issue_id}

    def get(self, key: str) -> Optional[Dict[str, str]]:
        return self._by_key.get(key)

    def agent_session_id(self, key: str) -> Optional[str]:
        rec = self._by_key.get(key)
        return rec["agent_session_id"] if rec else None

    def unbind(self, key: str) -> None:
        self._by_key.pop(key, None)
