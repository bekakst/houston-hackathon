"""The `claude -p` headless bridge.

This is the ONE place we shell out to Claude Code CLI. Every customer reply,
every routing decision, every brand-voice rewrite goes through `run_json` here.

The brief's hard rule (HACKATHON_BRIEF.md §4): no Anthropic SDK, no other LLM
providers — `claude -p "<prompt>"` is the only allowed bridge. The prior
submission's 43/100 root cause was bypassing this with regex.

Contract:
- Caller passes a system_prompt + envelope (any JSON-serialisable dict).
- We prepend a JSON-output instruction, append the envelope as JSON, and shell
  out to `claude -p` with stdin=None (the prompt is on the command line).
- We parse the stdout as JSON. On parse failure we retry once with an
  even stricter "respond with ONLY valid JSON, no prose, no code fences"
  appended. On second failure we raise CLIError — callers MUST fall back to
  a deterministic templated reply that ESCALATES TO OWNER. We never invent.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import subprocess
import sys
from dataclasses import dataclass
from typing import Any

from happycake.settings import settings

log = logging.getLogger(__name__)


class CLIError(RuntimeError):
    """Raised when claude -p fails twice or returns non-JSON output."""


@dataclass
class CLIResult:
    parsed: dict
    raw_stdout: str
    duration_s: float
    retries: int


# Patterns for stripping common LLM wrappers around JSON.
_FENCE_RE = re.compile(r"^```(?:json)?\s*(.*?)\s*```\s*$", re.S | re.I)
_TRAILING_TEXT_RE = re.compile(r"(\{.*\}|\[.*\])", re.S)


def _build_command(prompt: str) -> list[str]:
    return [settings.claude_cli, "-p", prompt]


def _strip_to_json(text: str) -> str:
    """Try to recover JSON from a stdout that may contain prose / fences."""
    text = text.strip()
    if not text:
        return text
    fence = _FENCE_RE.match(text)
    if fence:
        return fence.group(1).strip()
    # If output starts with prose, find the first { or [ and read to the last } or ].
    first = min((i for i in (text.find("{"), text.find("[")) if i >= 0), default=-1)
    if first <= 0:
        return text
    last_brace = max(text.rfind("}"), text.rfind("]"))
    if last_brace > first:
        return text[first:last_brace + 1]
    return text


def _build_prompt(system_prompt: str, envelope: dict[str, Any], *,
                  strict_suffix: bool = False) -> str:
    """Render the prompt body sent to `claude -p`."""
    pieces: list[str] = []
    pieces.append(system_prompt.strip())
    pieces.append("")
    pieces.append("INPUT (JSON):")
    pieces.append(json.dumps(envelope, ensure_ascii=False, indent=2))
    pieces.append("")
    if strict_suffix:
        pieces.append(
            "RESPOND WITH ONLY ONE LINE OF VALID JSON. No prose, no markdown, "
            "no code fences. The first character must be `{`. Your previous "
            "response failed to parse as JSON — this is the retry."
        )
    else:
        pieces.append(
            "RESPOND WITH A SINGLE JSON OBJECT matching the OUTPUT SCHEMA above. "
            "No prose around it, no code fences."
        )
    return "\n".join(pieces)


async def _invoke_subprocess(prompt: str, *, timeout_s: int) -> tuple[str, str, int]:
    """Run claude -p as a subprocess. Returns (stdout, stderr, returncode)."""
    proc = await asyncio.create_subprocess_exec(
        *_build_command(prompt),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        # On Windows, attach to a console group separate from this Python process
        # so signals don't propagate.
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0,
    )
    try:
        stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
    except asyncio.TimeoutError as exc:
        proc.kill()
        await proc.wait()
        raise CLIError(f"claude -p timed out after {timeout_s}s") from exc
    return (
        stdout_b.decode("utf-8", errors="replace"),
        stderr_b.decode("utf-8", errors="replace"),
        proc.returncode or 0,
    )


async def run_json(system_prompt: str, envelope: dict[str, Any], *,
                   timeout_s: int | None = None) -> CLIResult:
    """Invoke `claude -p` and parse its stdout as JSON.

    Args:
        system_prompt: The complete prompt body (loaded from ops/prompts/*.md).
            It MUST already declare the OUTPUT SCHEMA.
        envelope: A JSON-serialisable dict describing the customer turn,
            grounded MCP facts, and any prior conversation history.
        timeout_s: Override the per-call timeout. Defaults to settings.

    Returns:
        CLIResult with parsed JSON, raw stdout, duration, and retry count.

    Raises:
        CLIError if both attempts fail to produce parseable JSON. Callers
        should treat this as "escalate to owner" — never as "make something up".
    """
    timeout_s = timeout_s or settings.claude_timeout_seconds

    loop = asyncio.get_event_loop()
    start = loop.time()
    retries = 0

    last_error: str = ""
    last_stdout: str = ""
    for attempt in range(2):
        prompt = _build_prompt(
            system_prompt, envelope, strict_suffix=(attempt == 1),
        )
        try:
            stdout, stderr, rc = await _invoke_subprocess(prompt, timeout_s=timeout_s)
        except CLIError:
            raise
        except Exception as exc:  # noqa: BLE001
            last_error = f"subprocess error: {exc}"
            log.warning("claude -p attempt %d errored: %s", attempt + 1, exc)
            retries = attempt + 1
            continue

        last_stdout = stdout
        if rc != 0:
            last_error = f"non-zero exit {rc}: {stderr[:200]}"
            log.warning("claude -p exit %d (attempt %d): %s", rc, attempt + 1, stderr[:200])
            retries = attempt + 1
            continue

        candidate = _strip_to_json(stdout)
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError as exc:
            last_error = f"json decode failed: {exc}"
            log.warning(
                "claude -p attempt %d returned non-JSON (first 240 chars): %s",
                attempt + 1, candidate[:240].replace("\n", " "),
            )
            retries = attempt + 1
            continue

        if not isinstance(parsed, dict):
            last_error = f"top-level JSON is {type(parsed).__name__}, expected object"
            retries = attempt + 1
            continue

        return CLIResult(
            parsed=parsed,
            raw_stdout=stdout,
            duration_s=loop.time() - start,
            retries=retries,
        )

    raise CLIError(
        f"claude -p failed twice. last_error={last_error!r}. "
        f"stdout snippet: {last_stdout[:200]!r}"
    )


async def health_check() -> dict:
    """One-liner used by `make doctor` and the gateway's /health endpoint."""
    try:
        proc = await asyncio.create_subprocess_exec(
            settings.claude_cli, "--version",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        stdout_b, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
        return {"ok": proc.returncode == 0, "version": stdout_b.decode().strip()}
    except FileNotFoundError:
        return {"ok": False, "error": f"binary not found: {settings.claude_cli}"}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}


__all__ = ["run_json", "CLIResult", "CLIError", "health_check"]
