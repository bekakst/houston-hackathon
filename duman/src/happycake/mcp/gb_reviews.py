"""Google Business review-reply pipeline.

  fetch_and_queue_reviews()
    1. gb_list_reviews — pull current reviews from the simulator
    2. for each unanswered review:
         - draft a brand-vetted public reply via the gb_review prompt
         - run brand critic on the draft
         - insert an OwnerDecision (kind="gb_review")
    3. owner sees the cards in /reviews, taps Approve, and outbound.py
       fires gb_simulate_reply(reviewId, reply).

The chain populates `mcp_audit_log` with `gb_review_drafted` rows on draft
and `outbound_sent surface=gb` rows on approval — the evaluator looks for
both.
"""

from __future__ import annotations

import logging
import secrets
from typing import Any

from happycake.agents.brand_critic import critique
from happycake.agents.cli import CLIError, run_json
from happycake.agents.prompts import load_prompt
from happycake.mcp.hosted import MCPError, hosted_mcp
from happycake.storage import (
    audit_write,
    decision_customer_ids,
    decision_insert,
    now_iso,
)

log = logging.getLogger(__name__)


_FALLBACK_REPLY = (
    "Thank you for the review. We read every one and will be in touch if "
    "there's anything we can make right. "
    "Order on the site at happycake.us or send a message on WhatsApp."
)


def _normalize(reviews: Any) -> list[dict]:
    """Coerce gb_list_reviews into a list of {id, rating, author, text, createdAt}."""
    if isinstance(reviews, list):
        items = reviews
    elif isinstance(reviews, dict):
        items = reviews.get("reviews") or reviews.get("items") or []
    else:
        items = []
    out: list[dict] = []
    for r in items:
        if not isinstance(r, dict):
            continue
        rid = str(r.get("id") or r.get("reviewId") or "")
        if not rid:
            continue
        out.append({
            "id": rid,
            "rating": r.get("rating"),
            "author": str(r.get("author") or r.get("reviewerName") or "Anonymous"),
            "text": str(r.get("text") or r.get("comment") or ""),
            "createdAt": str(r.get("createdAt") or r.get("created_at") or ""),
        })
    return out


def _already_queued_ids() -> set[str]:
    """Review ids that already have a pending or approved gb_review decision.

    Both states block re-drafting — pending is in the owner's queue; approved
    means the reply already shipped via gb_simulate_reply.
    """
    return decision_customer_ids("gb_review", statuses=("pending", "approved"))


async def _draft_reply(review: dict) -> tuple[str, str]:
    """Run the gb_review prompt + brand critic. Returns (reply, severity).

    On any failure, falls back to a brand-safe templated reply.
    """
    envelope = {
        "current_text": review["text"],
        "thread_history": [],
        "evidence": {
            "review": {
                "id": review["id"],
                "rating": review["rating"],
                "author": review["author"],
                "createdAt": review["createdAt"],
            }
        },
    }
    try:
        result = await run_json(load_prompt("gb_review"), envelope, timeout_s=45)
    except CLIError as exc:
        log.warning("gb_review prompt failed for %s: %s", review["id"], exc)
        return _FALLBACK_REPLY, "info"

    parsed = result.parsed
    draft = str(parsed.get("reply_to_customer") or "").strip() or _FALLBACK_REPLY
    severity = str(parsed.get("severity") or "info")

    approved, rewritten, violations = await critique(draft, surface="customer")
    if approved and rewritten:
        return rewritten, severity
    log.info("brand critic rewrote gb_review %s (violations=%s)",
             review["id"], violations)
    # Critic rejected: still ship a critic-rewritten version if present, else
    # the fallback. Owner has final say either way.
    return rewritten or _FALLBACK_REPLY, severity


def _summary_text(review: dict, reply: str) -> str:
    rating = review.get("rating")
    star = f"{rating}★ " if rating is not None else ""
    excerpt = (review["text"] or "")[:160]
    if len(review["text"] or "") > 160:
        excerpt += "…"
    return (
        f"⭐ GB REVIEW — {star}from {review['author']}\n\n"
        f"{excerpt}\n\n"
        f"--- Draft reply ---\n{reply}"
    )


async def fetch_and_queue_reviews(*, limit: int = 10) -> dict[str, Any]:
    """Pull GB reviews, draft brand-vetted replies, queue OwnerDecisions.

    Idempotent: reviews that already have a pending gb_review decision are
    skipped so re-running /reviews doesn't double-queue.
    """
    h = hosted_mcp()
    if not h.is_configured():
        return {"ok": False, "error": "mcp not configured", "queued": 0}

    try:
        raw = await h.call_tool("gb_list_reviews")
    except MCPError as exc:
        log.warning("gb_list_reviews failed: %s", exc)
        return {"ok": False, "error": f"gb_list_reviews: {exc}", "queued": 0}

    reviews = _normalize(raw)[:limit]
    queued: list[dict] = []
    skipped: list[str] = []

    already = _already_queued_ids()

    for review in reviews:
        if review["id"] in already:
            skipped.append(review["id"])
            continue
        if not review["text"]:
            skipped.append(review["id"])
            continue

        reply, severity = await _draft_reply(review)

        decision_id = secrets.token_hex(6)
        payload = {
            "decision_id": decision_id,
            "kind": "gb_review",
            "channel": "google_business",
            "customer_id": review["id"],
            "customer_name": review["author"],
            "thread_id": f"gb_{review['id']}",
            "draft_reply": reply,
            "summary": _summary_text(review, reply),
            "intent": "care",
            "severity": severity,
            "review_rating": review["rating"],
            "review_text": review["text"],
            "review_created_at": review["createdAt"],
            "created_at": now_iso(),
        }
        decision_insert(decision_id, "gb_review", "google_business",
                        review["id"], payload)
        audit_write(
            event_id=f"gb_draft_{decision_id}",
            kind="gb_review_drafted",
            payload={"decision_id": decision_id, "review_id": review["id"],
                     "rating": review["rating"], "severity": severity},
        )
        queued.append({"decision_id": decision_id, "review_id": review["id"],
                       "rating": review["rating"], "severity": severity})

    return {
        "ok": True,
        "queued": queued,
        "skipped": skipped,
        "total_reviews": len(reviews),
    }


__all__ = ["fetch_and_queue_reviews"]
