"""Linear GraphQL client + query builders.

Builders are pure/testable; ``execute`` performs HTTP via aiohttp and needs a
live token (validated in the final credentials step). Agent activities are how an
Agent Session streams work back to the issue.
Docs: https://linear.app/developers/agents
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

API_URL = "https://api.linear.app/graphql"

# Linear AgentActivity content types.
THOUGHT = "thought"
ACTION = "action"
ELICITATION = "elicitation"
RESPONSE = "response"
ERROR = "error"

_AGENT_ACTIVITY_CREATE = (
    "mutation AgentActivityCreate($input: AgentActivityCreateInput!) {"
    " agentActivityCreate(input: $input) { success } }"
)
_COMMENT_CREATE = (
    "mutation CommentCreate($input: CommentCreateInput!) {"
    " commentCreate(input: $input) { success comment { id url } } }"
)
_VIEWER = "query Viewer { viewer { id name } }"


def agent_activity_input(agent_session_id: str, activity_type: str, body: str) -> Dict[str, Any]:
    """Build the AgentActivityCreateInput. ``activity_type`` ∈ thought/action/elicitation/response/error.

    NOTE: the exact ``content`` shape is confirmed against the live Agent API in
    the credentials step; this is the documented form.
    """
    return {"input": {"agentSessionId": agent_session_id, "content": {"type": activity_type, "body": body}}}


def comment_input(issue_id: str, body: str, *, parent_id: Optional[str] = None) -> Dict[str, Any]:
    inp: Dict[str, Any] = {"issueId": issue_id, "body": body}
    if parent_id:
        inp["parentId"] = parent_id
    return {"input": inp}


class LinearGraphQL:
    """Thin async GraphQL client over a persistent aiohttp session."""

    def __init__(self, token_provider, session=None):
        # token_provider: async () -> Optional[str] — awaited per call so a
        # freshly-refreshed token is always used (see adapter._ensure_token).
        self._token_provider = token_provider
        self._session = session

    async def _ensure_session(self):
        if self._session is None:
            import aiohttp
            self._session = aiohttp.ClientSession()
        return self._session

    async def execute(self, query: str, variables: Dict[str, Any]) -> Dict[str, Any]:
        token = await self._token_provider()
        if not token:
            return {"errors": [{"message": "no Linear token (OAuth not completed / API key unset)"}]}
        session = await self._ensure_session()
        headers = {"Authorization": token, "Content-Type": "application/json"}
        async with session.post(API_URL, json={"query": query, "variables": variables}, headers=headers) as resp:
            data = await resp.json()
            if data.get("errors"):
                logger.warning("[linear] graphql errors: %s", data["errors"])
            return data

    async def create_agent_activity(self, agent_session_id: str, activity_type: str, body: str) -> Dict[str, Any]:
        return await self.execute(_AGENT_ACTIVITY_CREATE, agent_activity_input(agent_session_id, activity_type, body))

    async def create_comment(self, issue_id: str, body: str, *, parent_id: Optional[str] = None) -> Dict[str, Any]:
        return await self.execute(_COMMENT_CREATE, comment_input(issue_id, body, parent_id=parent_id))

    async def viewer(self) -> Dict[str, Any]:
        return await self.execute(_VIEWER, {})

    async def close(self):
        if self._session is not None:
            await self._session.close()
            self._session = None
