"""Webhook security helpers — HMAC verification + idempotency keys."""

from __future__ import annotations

import hashlib
import hmac

from happycake.settings import settings


def verify_meta_signature(*, app_secret: str, body: bytes, signature_header: str | None) -> bool:
    """Verify a Meta-style X-Hub-Signature-256 header.

    In dev, missing header is logged and accepted (settings.is_dev() == True).
    In production we always require it.
    """
    if not signature_header:
        return settings.is_dev()
    expected = signature_header.removeprefix("sha256=").strip()
    digest = hmac.new(app_secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(digest, expected)


def derive_external_id(*, channel: str, sender: str, received_at: str, text: str) -> str:
    """Idempotency key. Replays produce identical ids."""
    raw = f"{channel}|{sender}|{received_at}|{text}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:32]
