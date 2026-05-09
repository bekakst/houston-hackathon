"""Brand voice critic — second `claude -p` pass that rewrites drafts to comply
with the HappyCake brand book without changing facts.

Innovation lever: defense-in-depth. Wordmark / cake-name / emoji / standard
close are enforced both in the original agent prompt AND here. A prompt
regression in any specialist still gets caught before the customer sees it.
"""

from __future__ import annotations

import logging

from happycake.agents.cli import CLIError, run_json
from happycake.agents.prompts import load_prompt
from happycake.mcp.brand import voice_spec

log = logging.getLogger(__name__)


async def critique(draft_text: str, *, surface: str = "customer") -> tuple[bool, str, list[str]]:
    """Return (approved, rewritten_text, violations).

    `surface="customer"` means the standard close is required.
    `surface="telegram_owner"` skips the customer CTA.

    On CLI failure we return (False, "", ["critic_unreachable"]) so the
    dispatcher can fall back to escalation rather than sending unvetted text.
    """
    if not draft_text or not draft_text.strip():
        return True, draft_text, []
    envelope = {
        "draft_text": draft_text,
        "surface": surface,
        "voice_spec": voice_spec(),
    }
    try:
        result = await run_json(load_prompt("brand_critic"), envelope)
    except CLIError as exc:
        log.warning("brand_critic CLI error: %s", exc)
        return False, "", ["critic_unreachable"]

    parsed = result.parsed
    approved = bool(parsed.get("approved", False))
    rewritten = str(parsed.get("rewritten_text", "")).strip()
    violations = list(parsed.get("violations_found", []))

    if approved and not rewritten:
        # Critic approved but didn't echo back text — keep the original.
        rewritten = draft_text
    return approved, rewritten, violations
