"""IG scheduled-post pipeline + photo-asset matcher.

Two responsibilities:

  - `pick_image_url(image_hint, base_cake_slug)` — best-effort match from a
    creative draft's hint to one of the curated webp assets under
    `apps/web/static/photos/`. Returns an absolute URL the simulator can
    fetch. Falls back to a social/storefront asset.

  - `publish_creative_drafts(plan, decision_id)` — for every IG `kind: post`
    draft in a marketing_plan, runs the documented simulator chain
    `instagram_schedule_post → instagram_approve_post → instagram_publish_post`
    and writes audit rows the evaluator looks for.
"""

from __future__ import annotations

import logging
from typing import Any

from happycake.mcp.hosted import MCPError, hosted_mcp
from happycake.settings import settings
from happycake.storage import audit_write

log = logging.getLogger(__name__)


# Slugs for which a curated product photo exists under /static/photos/.
_CAKE_SLUGS: tuple[str, ...] = (
    "honey", "napoleon", "pistachio-roll", "red-velvet", "tiramisu",
    "milk-maiden", "carrot", "cloud", "custom",
)

# Brand-aware fallbacks when the draft can't be tied to a specific cake.
_BRAND_FALLBACKS: tuple[str, ...] = (
    "storefront",      # storefront.webp
    "honey",           # honey.webp — flagship
)


def _photo_url(filename: str) -> str:
    """Build an absolute URL for a /static/photos/<filename>.webp asset."""
    base = settings.public_base_url.rstrip("/")
    # public_base_url defaults to the gateway (8001). Photos are served by
    # the web app (8000). If a deployment overrides public_base_url to a
    # tunneled URL, that URL is expected to fan out to both.
    return f"{base}/static/photos/{filename}.webp"


def pick_image_url(image_hint: str | None, base_cake_slug: str | None = None) -> str:
    """Pick the best photo asset for a creative draft.

    Resolution order:
      1. Exact `base_cake_slug` match (when the marketing agent attached one).
      2. Substring match of any cake slug inside `image_hint`.
      3. Brand fallback: storefront, then honey.
    """
    if base_cake_slug and base_cake_slug.lower() in _CAKE_SLUGS:
        return _photo_url(base_cake_slug.lower())

    hint = (image_hint or "").lower()
    if hint:
        # Match longest slug first so "pistachio-roll" wins over "pistachio".
        for slug in sorted(_CAKE_SLUGS, key=len, reverse=True):
            needle = slug.replace("-", " ")
            if slug in hint or needle in hint:
                return _photo_url(slug)

    return _photo_url(_BRAND_FALLBACKS[0])


async def publish_creative_drafts(plan: dict, decision_id: str) -> list[dict[str, Any]]:
    """Run schedule → approve → publish for every IG post draft in a plan.

    Returns one record per draft with each step's outcome. Mentions in the
    plan that aren't `channel == "instagram"` and `kind == "post"` are
    skipped (nothing to publish on IG).
    """
    drafts = plan.get("creative_drafts") or []
    ig_posts = [
        d for d in drafts
        if (d or {}).get("channel") == "instagram"
        and (d or {}).get("kind") in ("post", "story")
    ]
    if not ig_posts:
        return []

    h = hosted_mcp()
    if not h.is_configured():
        audit_write(
            event_id=f"ig_posts_skip_{decision_id}",
            kind="instagram_posts_skipped",
            payload={"decision_id": decision_id, "reason": "MCP not configured",
                     "draft_count": len(ig_posts)},
        )
        return [{"ok": False, "skipped": "mcp not configured"}]

    out: list[dict[str, Any]] = []
    for idx, draft in enumerate(ig_posts):
        record: dict[str, Any] = {
            "idx": idx,
            "kind": draft.get("kind"),
            "caption_preview": (draft.get("caption") or "")[:80],
        }
        caption = draft.get("caption") or ""
        image_url = (
            draft.get("image_url")
            or pick_image_url(draft.get("image_hint"), draft.get("base_cake_slug"))
        )
        record["image_url"] = image_url

        # 1. schedule
        scheduled_id: str | None = None
        try:
            args: dict[str, Any] = {"imageUrl": image_url, "caption": caption}
            if draft.get("scheduled_for"):
                args["scheduledFor"] = draft["scheduled_for"]
            r = await h.call_tool("instagram_schedule_post", args)
            scheduled_id = (
                (r or {}).get("scheduledPostId")
                or (r or {}).get("scheduled_post_id")
                or (r or {}).get("id")
            )
            record["schedule"] = {"ok": bool(scheduled_id), "result": r}
            audit_write(
                event_id=f"ig_sched_{decision_id}_{idx}",
                kind="instagram_post_scheduled",
                payload={"decision_id": decision_id,
                         "scheduled_post_id": scheduled_id,
                         "image_url": image_url,
                         "caption_preview": record["caption_preview"]},
            )
        except MCPError as exc:
            record["schedule"] = {"ok": False, "error": str(exc)}
            log.warning("instagram_schedule_post(%s) failed: %s", idx, exc)
            out.append(record)
            continue

        if not scheduled_id:
            out.append(record)
            continue

        # 2. approve (the owner-approved plan is the umbrella approval).
        try:
            r = await h.call_tool(
                "instagram_approve_post",
                {"scheduledPostId": scheduled_id},
            )
            record["approve"] = {"ok": True, "result": r}
            audit_write(
                event_id=f"ig_app_{decision_id}_{idx}",
                kind="instagram_post_approved",
                payload={"decision_id": decision_id,
                         "scheduled_post_id": scheduled_id},
            )
        except MCPError as exc:
            record["approve"] = {"ok": False, "error": str(exc)}
            log.warning("instagram_approve_post(%s) failed: %s", scheduled_id, exc)
            out.append(record)
            continue

        # 3. publish
        try:
            r = await h.call_tool(
                "instagram_publish_post",
                {"scheduledPostId": scheduled_id},
            )
            record["publish"] = {"ok": True, "result": r}
            audit_write(
                event_id=f"ig_pub_{decision_id}_{idx}",
                kind="instagram_post_published",
                payload={"decision_id": decision_id,
                         "scheduled_post_id": scheduled_id,
                         "image_url": image_url},
            )
        except MCPError as exc:
            record["publish"] = {"ok": False, "error": str(exc)}
            log.warning("instagram_publish_post(%s) failed: %s", scheduled_id, exc)

        out.append(record)

    return out


__all__ = ["pick_image_url", "publish_creative_drafts"]
