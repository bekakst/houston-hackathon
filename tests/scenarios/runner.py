"""YAML scenario runner. Exercises the agent stack end-to-end with mocked
outbound. Used by `make smoke` and by the evaluator hook.

A scenario file looks like:

    # tests/scenarios/public/whatsapp_birthday.yaml
    name: WhatsApp birthday cake
    channel: whatsapp
    sender: "+15551234567"
    sender_name: Maria
    thread_id: scn_wa_birthday
    turns:
      - customer: "Hi, I need a birthday cake for Saturday for 10 people."
        expect:
          intent: intake
          reply_contains_any: ["Saturday", "Honey", "Milk Maiden"]
          owner_approval: false
      - customer: "Cake Honey whole, pickup Saturday."
        expect:
          intent: intake
          reply_contains_any: ["55", "Honey"]
          owner_approval: true

Each turn is replayed via dispatcher.handle_customer_message; the assertion
block is loose by design (we check substrings, not exact text, because
claude -p output varies).
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from dataclasses import dataclass, field
from pathlib import Path

# Force UTF-8 stdout on Windows so we can print Unicode marks safely.
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass

import yaml

from happycake.agents.dispatcher import handle_customer_message
from happycake.schemas import Channel
from happycake.storage import init_db


@dataclass
class TurnResult:
    customer: str
    reply: str
    intent: str | None
    needs_owner_approval: bool
    suggested_action: str | None
    passed: bool
    failures: list[str] = field(default_factory=list)


@dataclass
class ScenarioResult:
    name: str
    file: str
    turns: list[TurnResult]
    passed: bool

    @property
    def turn_count(self) -> int:
        return len(self.turns)

    @property
    def pass_count(self) -> int:
        return sum(1 for t in self.turns if t.passed)


def _channel(value: str) -> Channel:
    return Channel(value)


async def _run_one_turn(scenario: dict, turn: dict, scenario_thread: str) -> TurnResult:
    text = turn["customer"]
    reply = await handle_customer_message(
        channel=_channel(scenario["channel"]),
        sender=scenario.get("sender", "scn_sender"),
        sender_name=scenario.get("sender_name", "guest"),
        text=text,
        thread_id=scenario_thread,
    )
    expect = turn.get("expect", {})
    failures: list[str] = []

    if "intent" in expect:
        actual = reply.intent.value if reply.intent else None
        if actual != expect["intent"]:
            failures.append(f"intent: expected {expect['intent']!r}, got {actual!r}")

    if "owner_approval" in expect:
        if bool(reply.needs_owner_approval) != bool(expect["owner_approval"]):
            failures.append(
                f"owner_approval: expected {expect['owner_approval']!r}, "
                f"got {reply.needs_owner_approval!r}"
            )

    if "suggested_action_contains" in expect:
        action = reply.suggested_action or ""
        if expect["suggested_action_contains"] not in action:
            failures.append(
                f"suggested_action_contains: expected substring "
                f"{expect['suggested_action_contains']!r} not in {action!r}"
            )

    if "reply_contains" in expect:
        for needle in expect["reply_contains"]:
            if needle.lower() not in reply.reply_to_customer.lower():
                failures.append(f"reply_contains: missing {needle!r}")

    if "reply_contains_any" in expect:
        candidates = expect["reply_contains_any"]
        if not any(c.lower() in reply.reply_to_customer.lower() for c in candidates):
            failures.append(
                f"reply_contains_any: none of {candidates!r} found in reply"
            )

    if "reply_does_not_contain" in expect:
        for needle in expect["reply_does_not_contain"]:
            if needle.lower() in reply.reply_to_customer.lower():
                failures.append(f"reply_does_not_contain: {needle!r} present")

    return TurnResult(
        customer=text,
        reply=reply.reply_to_customer,
        intent=reply.intent.value if reply.intent else None,
        needs_owner_approval=reply.needs_owner_approval,
        suggested_action=reply.suggested_action,
        passed=not failures,
        failures=failures,
    )


async def run_scenario(path: Path) -> ScenarioResult:
    scenario = yaml.safe_load(path.read_text(encoding="utf-8"))
    scenario.setdefault("channel", "web")
    thread = scenario.get("thread_id") or f"scn_{path.stem}"
    turn_results: list[TurnResult] = []
    for i, turn in enumerate(scenario.get("turns", []), start=1):
        result = await _run_one_turn(scenario, turn, thread)
        turn_results.append(result)
    passed = all(t.passed for t in turn_results)
    return ScenarioResult(
        name=scenario.get("name", path.stem),
        file=str(path),
        turns=turn_results,
        passed=passed,
    )


async def run_directory(root: Path) -> list[ScenarioResult]:
    init_db()
    files = sorted(root.glob("*.yaml"))
    if not files:
        return []
    results: list[ScenarioResult] = []
    for f in files:
        print(f"  > {f.name}", flush=True)
        results.append(await run_scenario(f))
    return results


def _print_results(results: list[ScenarioResult]) -> bool:
    if not results:
        print("No scenarios found.")
        return False
    all_passed = True
    print()
    print("=" * 78)
    for r in results:
        mark = "PASS" if r.passed else "FAIL"
        print(f"  [{mark}] {r.name} - {r.pass_count}/{r.turn_count} turns")
        if not r.passed:
            all_passed = False
            for t in r.turns:
                if not t.passed:
                    print(f"     turn: {t.customer[:80]!r}")
                    for f in t.failures:
                        print(f"        - {f}")
    print("=" * 78)
    total_turns = sum(r.turn_count for r in results)
    total_passed = sum(r.pass_count for r in results)
    pct = round(100 * total_passed / total_turns) if total_turns else 0
    print(f"  Scenarios: {sum(1 for r in results if r.passed)} / {len(results)} pass")
    print(f"  Turns:     {total_passed} / {total_turns} pass ({pct}%)")
    return all_passed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", help="Scenario directory or single YAML file")
    args = parser.parse_args(argv)
    target = Path(args.path).resolve()
    if not target.exists():
        print(f"path not found: {target}", file=sys.stderr)
        return 2
    if target.is_file():
        results = [asyncio.run(run_scenario(target))]
    else:
        results = asyncio.run(run_directory(target))
    ok = _print_results(results)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
