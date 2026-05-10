"""Loader for ops/prompts/*.md system prompts. Cached after first read."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from happycake.settings import settings


def _prompts_dir() -> Path:
    return settings.project_root / "ops" / "prompts"


@lru_cache(maxsize=32)
def load_prompt(name: str) -> str:
    """Load `ops/prompts/<name>.md` as a string."""
    path = _prompts_dir() / f"{name}.md"
    if not path.exists():
        raise FileNotFoundError(f"prompt not found: {path}")
    return path.read_text(encoding="utf-8")
