"""Linear platform adapter for Hermes Agent (registered via PluginContext).

Request flow
------------
1. Linear POSTs a webhook to ``/hooks/linear`` (public via the deployment's own
   Tailscale Funnel — no Cloudflare Worker).
2. ``_handle_webhook`` verifies the ``Linear-Signature`` HMAC + replay window,
   then for an ``AgentSessionEvent``:
     a. IMMEDIATELY posts a placeholder *thought* activity (the timing invariant:
        Linear marks a session unresponsive if no activity lands <10s), then
     b. returns ``200`` (<5s ack), and
     c. dispatches the prompt into the agent off the request path via
        ``build_source`` -> ``MessageEvent`` -> ``handle_message`` (the base runs
        the agent and calls :meth:`send` with the reply).
3. :meth:`send` posts the agent's final message as a *response* activity on the
   Agent Session (OAuth/actor=app) or, in the API-key fallback, a plain comment.

OAuth (bring-your-own app) is self-hosted at ``/oauth/linear/{authorize,callback}``.
Pure logic lives in sibling modules (signature/oauth/graphql/activities) and is
unit-tested without Hermes; this file is the Hermes-coupled glue.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from typing import Any, Dict, Optional

from gateway.config import Platform, PlatformConfig
from gateway.platforms.base import BasePlatformAdapter, MessageEvent, MessageType, SendResult

from . import activities, graphql, oauth, signature

try:
    from aiohttp import web
    _AIOHTTP = True
except ImportError:  # pragma: no cover - aiohttp ships in the Hermes image
    _AIOHTTP = False

logger = logging.getLogger(__name__)

PLATFORM_NAME = "linear"
DEFAULT_PORT = 8650
MAX_COMMENT_LENGTH = 10_000
TOKEN_FILENAME = "linear_oauth_token.json"

PLATFORM_HINT = (
    "You are responding inside a Linear Agent Session. Use Markdown (Linear "
    "renders it). Keep responses focused on the issue. Your tool use is streamed "
    "to the issue as agent activities; your final message becomes the session "
    "response."
)

# Set when an adapter instance connects, so module-level hooks (registered at
# plugin load, before any instance exists) can reach the live adapter. None until
# the platform is enabled + connected; hooks no-op when None.
_ACTIVE: "Optional[LinearAdapter]" = None


def check_linear() -> bool:
    if not os.getenv("LINEAR_WEBHOOK_SECRET"):
        return False
    has_oauth = bool(os.getenv("LINEAR_CLIENT_ID") and os.getenv("LINEAR_CLIENT_SECRET"))
    return has_oauth or bool(os.getenv("LINEAR_API_KEY"))


def is_linear_connected(config: "PlatformConfig") -> bool:
    return check_linear()


def linear_env_overrides() -> Optional[Dict[str, Any]]:
    if not check_linear():
        return None
    extra: Dict[str, Any] = {
        "webhook_secret": os.getenv("LINEAR_WEBHOOK_SECRET", ""),
        "client_id": os.getenv("LINEAR_CLIENT_ID", ""),
        "client_secret": os.getenv("LINEAR_CLIENT_SECRET", ""),
        "api_key": os.getenv("LINEAR_API_KEY", ""),
        "public_url": os.getenv("LINEAR_PUBLIC_URL", "").rstrip("/"),
        "port": int(os.getenv("LINEAR_PORT", str(DEFAULT_PORT))),
    }
    teams = os.getenv("LINEAR_TEAM_IDS", "")
    if teams:
        extra["team_ids"] = [t.strip() for t in teams.split(",") if t.strip()]
    home = os.getenv("LINEAR_HOME_CHANNEL", "")
    if home:
        extra["home_channel"] = {"chat_id": home, "name": "Home"}
    return extra


def _hermes_home() -> str:
    return os.getenv("HERMES_HOME", os.path.expanduser("~/.hermes"))


class LinearAdapter(BasePlatformAdapter):
    def __init__(self, config: "PlatformConfig"):
        super().__init__(config=config, platform=Platform(PLATFORM_NAME))
        extra = getattr(config, "extra", {}) or {}
        self._host = "127.0.0.1"
        self._port = int(extra.get("port", os.getenv("LINEAR_PORT", str(DEFAULT_PORT))))
        self._webhook_secret = extra.get("webhook_secret") or os.getenv("LINEAR_WEBHOOK_SECRET", "")
        self._client_id = extra.get("client_id") or os.getenv("LINEAR_CLIENT_ID", "")
        self._client_secret = extra.get("client_secret") or os.getenv("LINEAR_CLIENT_SECRET", "")
        self._api_key = extra.get("api_key") or os.getenv("LINEAR_API_KEY", "")
        self._public_url = (extra.get("public_url") or os.getenv("LINEAR_PUBLIC_URL", "")).rstrip("/")
        self.max_message_length = MAX_COMMENT_LENGTH

        self._tokens = oauth.TokenStore(os.path.join(_hermes_home(), TOKEN_FILENAME))
        self._gql = graphql.LinearGraphQL(token_getter=self._current_token)
        self._sessions = activities.SessionRegistry()
        self._oauth_state: Optional[str] = None
        self._runner = None  # aiohttp AppRunner

    # ----- identity -------------------------------------------------------
    @property
    def name(self) -> str:
        return "Linear"

    def _current_token(self) -> Optional[str]:
        # Prefer the OAuth actor=app token; fall back to a plain API key.
        return self._tokens.access_token() or (self._api_key or None)

    # ----- lifecycle ------------------------------------------------------
    async def connect(self) -> bool:
        global _ACTIVE
        if not _AIOHTTP:
            self._set_fatal_error("linear_no_aiohttp", "aiohttp not available", retryable=False)
            return False
        app = web.Application()
        app.router.add_post("/hooks/linear", self._handle_webhook)
        app.router.add_get("/oauth/linear/authorize", self._handle_oauth_authorize)
        app.router.add_get("/oauth/linear/callback", self._handle_oauth_callback)
        app.router.add_get("/hooks/linear/health", lambda r: web.json_response({"ok": True}))
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        await web.TCPSite(self._runner, self._host, self._port).start()
        _ACTIVE = self
        if not self._tokens.access_token() and not self._api_key and self._client_id and self._public_url:
            logger.warning(
                "[linear] no token yet — visit %s/oauth/linear/authorize to install the agent app",
                self._public_url,
            )
        logger.info("[linear] listening on %s:%s (webhook=/hooks/linear)", self._host, self._port)
        self._mark_connected()
        return True

    async def disconnect(self) -> None:
        global _ACTIVE
        if self._runner is not None:
            try:
                await self._runner.cleanup()
            except Exception:  # noqa: BLE001
                logger.warning("[linear] runner cleanup failed", exc_info=True)
            self._runner = None
        try:
            await self._gql.close()
        except Exception:  # noqa: BLE001
            pass
        if _ACTIVE is self:
            _ACTIVE = None
        self._mark_disconnected()

    # ----- inbound: webhook ----------------------------------------------
    async def _handle_webhook(self, request) -> "web.Response":
        raw = await request.read()
        if not signature.verify_signature(raw, request.headers.get("Linear-Signature"), self._webhook_secret):
            return web.json_response({"error": "bad signature"}, status=401)
        try:
            payload = json.loads(raw)
        except ValueError:
            return web.json_response({"error": "bad json"}, status=400)
        ts = payload.get("webhookTimestamp")
        if ts is not None and not signature.within_replay_window(ts, int(time.time() * 1000)):
            return web.json_response({"error": "stale"}, status=401)

        etype = payload.get("type", "")
        action = payload.get("action", "")
        try:
            if etype == "AgentSessionEvent":
                return await self._on_agent_session(action, payload)
            if etype in ("Comment", "Issue"):
                # bot-identity fallback (no Agent Session): treat as a one-shot prompt
                await self._dispatch_fallback(etype, action, payload)
            return web.json_response({"ok": True}, status=200)
        except Exception:  # noqa: BLE001 — never 500 to Linear; ack and log
            logger.exception("[linear] webhook handling error")
            return web.json_response({"ok": True}, status=200)

    async def _on_agent_session(self, action: str, payload: dict) -> "web.Response":
        data = payload.get("agentSession") or payload.get("data") or {}
        agent_session_id = data.get("id") or payload.get("agentSessionId") or ""
        issue = data.get("issue") or {}
        issue_id = issue.get("id") or data.get("issueId") or ""
        prompt = self._extract_prompt(payload, data)

        if action in ("created", "prompted") and agent_session_id:
            # TIMING INVARIANT: post the first activity immediately so Linear's
            # <10s contract is met regardless of agent latency, THEN dispatch.
            atype, body = activities.ack_thought()
            await self._gql.create_agent_activity(agent_session_id, atype, body)

            source = self.build_source(
                chat_id=agent_session_id,
                chat_name=issue.get("title") or "Linear",
                chat_type="channel",
                user_id=(data.get("creator") or {}).get("id") or "linear",
                thread_id=issue_id or None,
                message_id=data.get("id"),
            )
            self._sessions.bind(self._session_key(source), agent_session_id=agent_session_id, issue_id=issue_id)
            event = MessageEvent(text=prompt or "", message_type=MessageType.TEXT, source=source, raw_message=payload)
            asyncio.create_task(self.handle_message(event))
        return web.json_response({"ok": True}, status=200)

    async def _dispatch_fallback(self, etype: str, action: str, payload: dict) -> None:
        data = payload.get("data") or {}
        issue = data.get("issue") or {}
        issue_id = issue.get("id") or (data.get("id") if etype == "Issue" else "")
        text = data.get("body") or issue.get("title") or ""
        if not issue_id:
            return
        source = self.build_source(
            chat_id=issue_id, chat_name=issue.get("title") or "Linear",
            chat_type="channel", user_id=(data.get("user") or {}).get("id") or "linear",
            message_id=data.get("id"),
        )
        event = MessageEvent(text=text, message_type=MessageType.TEXT, source=source, raw_message=payload)
        asyncio.create_task(self.handle_message(event))

    def _session_key(self, source) -> str:
        # Stable per-issue key for the activity-streaming registry.
        return f"linear:{getattr(source, 'chat_id', '')}"

    @staticmethod
    def _extract_prompt(payload: dict, data: dict) -> str:
        for path in (("agentActivity", "content", "body"), ("comment", "body"), ("body",)):
            cur: Any = payload
            for k in path:
                cur = cur.get(k) if isinstance(cur, dict) else None
            if isinstance(cur, str) and cur.strip():
                return cur
        return (data.get("title") or "") if isinstance(data, dict) else ""

    # ----- outbound: the agent's reply -----------------------------------
    async def send(self, chat_id: str, content: str, reply_to: Optional[str] = None,
                   metadata: Optional[Dict[str, Any]] = None) -> "SendResult":
        if content and len(content) > MAX_COMMENT_LENGTH:
            content = content[: MAX_COMMENT_LENGTH - 80] + "\n\n…(truncated)"
        binding = self._sessions.get(f"linear:{chat_id}")
        try:
            if binding:  # Agent Session: post the final response activity
                atype, body = activities.final_response(content)
                res = await self._gql.create_agent_activity(binding["agent_session_id"], atype, body)
                ok = bool(res.get("data", {}).get("agentActivityCreate", {}).get("success"))
                return SendResult(success=ok, error=None if ok else str(res.get("errors")))
            # fallback: plain comment on the issue (chat_id == issue id)
            res = await self._gql.create_comment(chat_id, content)
            node = res.get("data", {}).get("commentCreate", {})
            ok = bool(node.get("success"))
            return SendResult(success=ok, message_id=(node.get("comment") or {}).get("id"),
                              error=None if ok else str(res.get("errors")))
        except Exception as exc:  # noqa: BLE001
            logger.exception("[linear] send failed")
            return SendResult(success=False, error=str(exc), retryable=True)

    async def get_chat_info(self, chat_id: str) -> Dict[str, Any]:
        return {"name": chat_id, "type": "channel"}

    # ----- OAuth (bring-your-own app) ------------------------------------
    async def _handle_oauth_authorize(self, request) -> "web.Response":
        if not (self._client_id and self._public_url):
            return web.json_response({"error": "LINEAR_CLIENT_ID / LINEAR_PUBLIC_URL not set"}, status=400)
        self._oauth_state = oauth.gen_state()
        return web.HTTPFound(oauth.build_authorize_url(self._client_id, self._public_url, self._oauth_state))

    async def _handle_oauth_callback(self, request) -> "web.Response":
        if request.query.get("state") != self._oauth_state:
            return web.json_response({"error": "state mismatch"}, status=400)
        code = request.query.get("code")
        if not code:
            return web.json_response({"error": "missing code"}, status=400)
        params = oauth.build_token_exchange_params(code, self._client_id, self._client_secret, self._public_url)
        session = await self._gql._ensure_session()  # reuse the client session
        async with session.post(oauth.TOKEN_URL, data=params) as resp:
            tok = await resp.json()
        if "access_token" not in tok:
            return web.json_response({"error": "token exchange failed", "detail": tok}, status=400)
        self._tokens.save(tok)
        logger.info("[linear] OAuth complete — actor=app token stored")
        return web.Response(text="Linear agent installed. You can close this tab.")


# ----- activity streaming hooks (best-effort; never break a run) ----------
async def _hook_post_tool_call(**kwargs):
    """Stream a Linear `action` activity per tool call. No-ops unless the run's
    session is bound to a Linear Agent Session (see DESIGN.md hook-correlation)."""
    if _ACTIVE is None:
        return None
    key = kwargs.get("session_key") or kwargs.get("session_id")
    binding = _ACTIVE._sessions.get(key) if key else None
    if not binding:
        return None
    atype, body = activities.tool_action(kwargs.get("tool_name", "tool"), kwargs.get("args"))
    try:
        await _ACTIVE._gql.create_agent_activity(binding["agent_session_id"], atype, body)
    except Exception:  # noqa: BLE001
        logger.debug("[linear] activity stream failed", exc_info=True)
    return None


def register(ctx) -> None:
    """Hermes plugin entrypoint."""
    ctx.register_platform(
        name=PLATFORM_NAME,
        label="Linear",
        adapter_factory=lambda cfg: LinearAdapter(cfg),
        check_fn=check_linear,
        is_connected=is_linear_connected,
        env_enablement_fn=linear_env_overrides,
        required_env=["LINEAR_WEBHOOK_SECRET", "LINEAR_CLIENT_ID", "LINEAR_CLIENT_SECRET", "LINEAR_PUBLIC_URL"],
        install_hint=(
            "Set LINEAR_WEBHOOK_SECRET + (LINEAR_CLIENT_ID & LINEAR_CLIENT_SECRET for "
            "Agent Sessions, or LINEAR_API_KEY for the bot fallback) + LINEAR_PUBLIC_URL."
        ),
        emoji="\U0001F4D0",  # 📐
        platform_hint=PLATFORM_HINT,
        max_message_length=MAX_COMMENT_LENGTH,
        allowed_users_env="LINEAR_ALLOWED_USERS",
        allow_all_env="LINEAR_ALLOW_ALL_USERS",
    )
    # Best-effort streaming; tolerate older cores without these hook names.
    for hook_name, cb in (("post_tool_call", _hook_post_tool_call),):
        try:
            ctx.register_hook(hook_name, cb)
        except Exception:  # noqa: BLE001
            logger.debug("[linear] hook %s not registrable on this core", hook_name)
    logger.info("[linear] platform + hooks registered")
