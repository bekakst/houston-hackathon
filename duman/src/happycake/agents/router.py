"""Router agent — classifies a customer turn into one of five intents."""

from __future__ import annotations

import logging
from typing import Literal

from happycake.agents.cli import CLIError, run_json
from happycake.agents.prompts import load_prompt
from happycake.schemas import Intent

log = logging.getLogger(__name__)

_VALID_INTENTS = {i.value for i in Intent}


async def classify(text: str, *, history: list[dict] | None = None) -> tuple[Intent, float, str]:
    """Return (intent, confidence, reason). On any failure, return (escalate, 0.0, error).

    The wrapper guarantees that an intent always exists — never raises — because
    the dispatcher must always have a path forward, even if it's escalation.
    """
    envelope = {
        "current_text": text,
        "thread_history": (history or [])[-6:],
    }
    try:
        result = await run_json(load_prompt("router"), envelope)
        parsed = result.parsed
        intent_str = str(parsed.get("intent", "")).lower().strip()
        if intent_str not in _VALID_INTENTS:
            log.warning("router returned invalid intent %r — escalating", intent_str)
            return Intent.escalate, 0.0, f"invalid_intent:{intent_str}"
        confidence = float(parsed.get("confidence", 0.0))
        reason = str(parsed.get("reason", ""))
        if confidence < 0.6 and intent_str != "escalate":
            log.info("router confidence %.2f below 0.6, forcing escalate", confidence)
            return Intent.escalate, confidence, f"low_confidence:{reason}"
        return Intent(intent_str), confidence, reason
    except CLIError as exc:
        log.warning("router CLI error: %s", exc)
        return Intent.escalate, 0.0, "router_unreachable"
