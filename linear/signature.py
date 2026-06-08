"""Linear webhook signature + replay verification (pure stdlib — no Hermes deps).

Linear signs each webhook with ``Linear-Signature``: the hex HMAC-SHA256 of the
RAW request body using the webhook signing secret. The body also carries
``webhookTimestamp`` (epoch ms); reject anything outside a replay window.
Docs: https://linear.app/developers/webhooks
"""
from __future__ import annotations

import hashlib
import hmac

DEFAULT_REPLAY_WINDOW_S = 60


def verify_signature(raw_body: bytes, signature_header: str | None, secret: str) -> bool:
    """Constant-time verify the ``Linear-Signature`` hex HMAC-SHA256 over the raw body."""
    if not secret or not signature_header:
        return False
    sig = signature_header.strip()
    if sig.startswith("sha256="):  # tolerate a prefixed form defensively
        sig = sig[len("sha256="):]
    expected = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, sig)


def within_replay_window(webhook_ts_ms: int | None, now_ms: int, window_s: int = DEFAULT_REPLAY_WINDOW_S) -> bool:
    """True if ``webhookTimestamp`` (epoch ms) is within ``window_s`` of ``now_ms``.

    A missing timestamp returns False (fail-closed). ``now_ms`` is injected so the
    check is deterministic and testable.
    """
    if webhook_ts_ms is None:
        return False
    return abs(now_ms - int(webhook_ts_ms)) <= window_s * 1000
