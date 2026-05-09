"""Safety pre-filter — runs BEFORE the router on every customer message.

The brand book makes allergen/dietary safety a hard rule:
    "every allergen question routes to a person on the team"

This module enforces that with a tokenised pre-filter so a router prompt
regression cannot silently let an allergy phrasing through. It is the OUTER
ring; the agent prompts contain the same hard rule as the INNER ring.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Tokens that always trigger owner escalation.
ALLERGEN_TOKENS = {
    "allergy", "allergic", "allergies",
    "anaphylactic", "anaphylaxis",
    "peanut", "peanuts",
    "nut", "nuts", "nut-free", "nut free",
    "almond", "almonds",
    "walnut", "walnuts",
    "cashew", "cashews",
    "pistachio-free", "pistachio free",
    "gluten", "gluten-free", "gluten free", "celiac", "coeliac",
    "dairy", "dairy-free", "dairy free", "lactose", "lactose-intolerant",
    "milk-free", "milk free",
    "egg", "eggless", "egg-free", "egg free",
    "soy", "soya", "soy-free",
    "wheat", "wheat-free",
    "sesame", "sesame-free",
}

# Multi-word phrases checked separately (so "lactose intolerant" matches even with a space).
ALLERGEN_PHRASES = {
    "lactose intolerant",
    "milk allergy",
    "peanut allergy",
    "tree nut",
    "nut allergy",
}

# Inputs we won't auto-process — needs human policy judgement.
ESCALATION_PHRASES = {
    "talk to a person", "talk to a human", "speak to a human", "talk to the owner",
    "speak to the owner", "human please", "talk to someone",
}

# Lightweight prompt-injection guardrail — the message is escalated for human
# review when it tries to override the system prompt. We do not silently ignore.
INJECTION_PATTERNS = [
    re.compile(r"\bignore (all|any) ?(previous|prior|the)? ?(instructions|prompt|rules)\b", re.I),
    re.compile(r"\bdisregard (all|any|previous|prior|the) (instructions|prompt|rules)\b", re.I),
    re.compile(r"\bsystem prompt\b", re.I),
    re.compile(r"\byou are now\b", re.I),
    re.compile(r"\b(reveal|show|print) (your|the) (prompt|instructions|system)\b", re.I),
]


@dataclass
class EscalationDecision:
    reason: str
    matched: list[str]


def _normalise(text: str) -> str:
    return " ".join(text.lower().split())


def safety_pre_filter(text: str) -> EscalationDecision | None:
    if not text:
        return None
    normalised = _normalise(text)

    matched_tokens = [t for t in ALLERGEN_TOKENS if _word_in(normalised, t)]
    if matched_tokens:
        return EscalationDecision(
            reason="allergen_question",
            matched=matched_tokens,
        )

    matched_phrases = [p for p in ALLERGEN_PHRASES if p in normalised]
    if matched_phrases:
        return EscalationDecision(
            reason="allergen_phrase",
            matched=matched_phrases,
        )

    if any(p in normalised for p in ESCALATION_PHRASES):
        return EscalationDecision(reason="human_handoff_request", matched=[])

    if any(p.search(normalised) for p in INJECTION_PATTERNS):
        return EscalationDecision(reason="prompt_injection_attempt", matched=[])

    return None


def _word_in(haystack: str, needle: str) -> bool:
    """Whole-word match for short tokens; substring for hyphenated phrases."""
    if "-" in needle or " " in needle:
        return needle in haystack
    pattern = rf"\b{re.escape(needle)}\b"
    return re.search(pattern, haystack) is not None


__all__ = ["safety_pre_filter", "EscalationDecision"]
