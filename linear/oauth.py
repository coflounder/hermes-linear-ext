"""Bring-your-own Linear OAuth (actor=app) — URL/exchange builders + token store.

No Cloudflare Worker: each deployment registers its own Linear OAuth application
and the plugin self-hosts the callback on the deployment's own public URL. The
URL/state builders + the on-volume token store here are pure and unit-tested; the
actual HTTP token exchange (``exchange_code``) is performed by the adapter with
aiohttp and needs live credentials (the final validation step).

Linear OAuth: https://linear.app/developers/oauth-2-0-authentication
"""
from __future__ import annotations

import json
import os
import secrets
import time
import urllib.parse
from pathlib import Path
from typing import Any, Dict, Optional

AUTHORIZE_URL = "https://linear.app/oauth/authorize"
TOKEN_URL = "https://api.linear.app/oauth/token"
# actor=app makes the token act AS the agent (mentionable/assignable workspace member).
DEFAULT_SCOPES = ("read", "write", "app:assignable", "app:mentionable")
CALLBACK_PATH = "/oauth/linear/callback"


def gen_state() -> str:
    """CSRF state for the authorize round-trip."""
    return secrets.token_urlsafe(24)


def redirect_uri(public_url: str) -> str:
    return public_url.rstrip("/") + CALLBACK_PATH


def build_authorize_url(client_id: str, public_url: str, state: str,
                        scopes: tuple[str, ...] = DEFAULT_SCOPES) -> str:
    """The consent URL an operator visits once to install the agent app."""
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri(public_url),
        "response_type": "code",
        "scope": ",".join(scopes),
        "state": state,
        "actor": "app",
        "prompt": "consent",
    }
    return AUTHORIZE_URL + "?" + urllib.parse.urlencode(params)


def build_token_exchange_params(code: str, client_id: str, client_secret: str,
                                public_url: str) -> Dict[str, str]:
    """Form body for the code->token POST (executed by the adapter via aiohttp)."""
    return {
        "code": code,
        "redirect_uri": redirect_uri(public_url),
        "client_id": client_id,
        "client_secret": client_secret,
        "grant_type": "authorization_code",
    }


class TokenStore:
    """Persist the app token on the volume at 0600. ``now`` is injectable for tests.

    (At-rest protection is the 0600 file perms on the private data volume; a
    future enhancement can wrap this with Hermes' key provider for encryption.)
    """

    def __init__(self, path: str | os.PathLike, now: Optional[Any] = None):
        self.path = Path(path)
        self._now = now or time.time

    def save(self, token: Dict[str, Any]) -> None:
        rec = dict(token)
        if "expires_in" in rec and "obtained_at" not in rec:
            rec["obtained_at"] = int(self._now())
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(rec))
        os.chmod(tmp, 0o600)
        os.replace(tmp, self.path)

    def load(self) -> Optional[Dict[str, Any]]:
        try:
            return json.loads(self.path.read_text())
        except (FileNotFoundError, ValueError):
            return None

    def access_token(self) -> Optional[str]:
        rec = self.load()
        return rec.get("access_token") if rec else None

    def is_expired(self, skew_s: int = 120) -> bool:
        rec = self.load()
        if not rec:
            return True
        if "expires_in" not in rec or "obtained_at" not in rec:
            return False  # non-expiring token
        return int(self._now()) >= int(rec["obtained_at"]) + int(rec["expires_in"]) - skew_s
