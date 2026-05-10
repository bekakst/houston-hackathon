"""Drive a deterministic world scenario through the agent stack.

Flow:
    world_start_scenario(scenarioId)
        -> loop world_next_event up to N times, dispatch each through the agent
        -> world_get_scenario_summary
        -> evaluator_score_world_scenario
        -> write analysis/_world_score.json with the run summary

Run as:
    python scripts/run_world.py                 # picks the first scenario id
    python scripts/run_world.py birthday-rush   # explicit scenario id
    python scripts/run_world.py birthday-rush --max-events 30 --advance 15

The script writes evidence the evaluator's mcp_audit_log + our local audit
table can both pick up. If MCP_TEAM_TOKEN is missing, exits with a clear
error rather than running silently.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass

from happycake.agents.dispatcher import handle_customer_message
from happycake.mcp.hosted import MCPError, hosted_mcp
from happycake.schemas import Channel
from happycake.settings import settings
from happycake.storage import audit_write, init_db

log = logging.getLogger("world")


async def _list_scenarios(h) -> list[dict]:
    try:
        result = await h.call_tool("world_get_scenarios")
    except MCPError as exc:
        log.warning("world_get_scenarios failed: %s", exc)
        return []
    if isinstance(result, dict):
        return list(result.get("scenarios") or result.get("items") or [])
    if isinstance(result, list):
        return result
    return []


def _pick_scenario_id(scenarios: list[dict], requested: str | None) -> str | None:
    if requested:
        return requested
    for s in scenarios:
        sid = s.get("scenarioId") or s.get("id") or s.get("slug")
        if sid:
            return sid
    return None


def _channel_from_event(event: dict) -> Channel:
    raw = (event.get("channel") or event.get("type") or "").lower()
    if "whatsapp" in raw or raw == "wa":
        return Channel.whatsapp
    if "instagram" in raw or raw == "ig" or raw == "dm":
        return Channel.instagram
    return Channel.web


def _extract_message(event: dict) -> tuple[str, str, str]:
    """Return (sender_id, sender_name, text). Tolerant of varied event shapes."""
    payload = event.get("payload") or event.get("data") or event
    sender_id = (
        payload.get("from")
        or payload.get("senderId")
        or payload.get("sender")
        or event.get("from")
        or "world_sender"
    )
    sender_name = payload.get("name") or payload.get("customerName") or sender_id
    text = (
        payload.get("message")
        or payload.get("text")
        or payload.get("body")
        or event.get("message")
        or ""
    )
    return str(sender_id), str(sender_name), str(text)


async def _drive(scenario_id: str, *, max_events: int, advance_minutes: int) -> dict:
    h = hosted_mcp()
    if not h.is_configured():
        raise SystemExit(
            "MCP_TEAM_TOKEN is missing — set it in .env before running scenarios."
        )

    init_db()
    started_at = datetime.now(tz=timezone.utc).isoformat()

    try:
        start_result = await h.call_tool(
            "world_start_scenario", {"scenarioId": scenario_id},
        )
    except MCPError as exc:
        raise SystemExit(f"world_start_scenario failed: {exc}") from exc

    audit_write(
        event_id=f"world_start_{scenario_id}",
        kind="world_scenario_started",
        payload={"scenario_id": scenario_id, "result": start_result},
    )

    handled: list[dict] = []
    for i in range(max_events):
        try:
            event = await h.call_tool("world_next_event")
        except MCPError as exc:
            log.warning("world_next_event failed at i=%d: %s", i, exc)
            break

        if not event or (isinstance(event, dict) and event.get("done")):
            break

        if not isinstance(event, dict):
            continue

        # Some servers wrap the event in {"event": {...}}.
        evt = event.get("event") if isinstance(event.get("event"), dict) else event
        sender_id, sender_name, text = _extract_message(evt)
        if not text:
            audit_write(
                event_id=f"world_skip_{i}",
                kind="world_event_skipped",
                payload={"reason": "no_text", "event": evt},
            )
            continue

        channel = _channel_from_event(evt)
        thread_id = f"world_{scenario_id}_{sender_id}"
        try:
            reply = await handle_customer_message(
                channel=channel,
                sender=sender_id,
                sender_name=sender_name,
                text=text,
                thread_id=thread_id,
            )
            handled.append({
                "i": i,
                "channel": channel.value,
                "sender": sender_id,
                "text_snippet": text[:120],
                "intent": reply.intent.value if reply.intent else None,
                "needs_owner_approval": reply.needs_owner_approval,
            })
        except Exception as exc:  # noqa: BLE001
            log.warning("dispatch failed at i=%d: %s", i, exc)
            handled.append({"i": i, "error": str(exc), "text_snippet": text[:120]})

        if advance_minutes > 0:
            try:
                await h.call_tool("world_advance_time", {"minutes": advance_minutes})
            except MCPError as exc:
                log.warning("world_advance_time failed: %s", exc)

    summary: dict = {}
    try:
        summary = await h.call_tool("world_get_scenario_summary") or {}
    except MCPError as exc:
        log.warning("world_get_scenario_summary failed: %s", exc)

    score: dict = {}
    try:
        score = await h.call_tool("evaluator_score_world_scenario") or {}
    except MCPError as exc:
        log.warning("evaluator_score_world_scenario failed: %s", exc)

    audit_write(
        event_id=f"world_done_{scenario_id}",
        kind="world_scenario_scored",
        payload={"scenario_id": scenario_id, "events_handled": len(handled)},
    )

    return {
        "scenario_id": scenario_id,
        "started_at": started_at,
        "ended_at": datetime.now(tz=timezone.utc).isoformat(),
        "events_handled": handled,
        "world_summary": summary,
        "evaluator_score": score,
    }


def _write_report(report: dict) -> Path:
    out = settings.project_root / "analysis" / "_world_score.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    return out


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Drive a world scenario via MCP.")
    p.add_argument("scenario_id", nargs="?", default=None,
                   help="Scenario id (defaults to first from world_get_scenarios).")
    p.add_argument("--max-events", type=int, default=20,
                   help="Cap on world_next_event iterations.")
    p.add_argument("--advance", type=int, default=10,
                   help="Minutes to world_advance_time between events. 0 disables.")
    return p.parse_args(argv)


async def _main(args: argparse.Namespace) -> int:
    h = hosted_mcp()
    if not h.is_configured():
        print("MCP_TEAM_TOKEN missing — set it in .env first.", file=sys.stderr)
        return 2

    scenarios = await _list_scenarios(h)
    scenario_id = _pick_scenario_id(scenarios, args.scenario_id)
    if not scenario_id:
        print("No scenarios available from world_get_scenarios.", file=sys.stderr)
        return 2

    print(f"Driving scenario: {scenario_id}", flush=True)
    report = await _drive(scenario_id,
                          max_events=args.max_events,
                          advance_minutes=args.advance)
    out = _write_report(report)
    print(f"Wrote {out}", flush=True)
    score = report.get("evaluator_score") or {}
    if score:
        print(f"Evaluator score: {json.dumps(score, indent=2, default=str)[:400]}",
              flush=True)
    return 0


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    args = _parse_args(argv)
    return asyncio.run(_main(args))


if __name__ == "__main__":
    raise SystemExit(main())
