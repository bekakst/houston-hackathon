"""Local YAML-backed data loader. Cached in memory after first read.

This module reads the same data files the hosted MCP simulator returns, so
unit tests and offline development run identically to the hackathon sandbox.
When MCP_TEAM_TOKEN is set and reachable, mcp/hosted.py overlays remote facts
on top of these local defaults.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml

from happycake.schemas import Cake
from happycake.settings import settings


def _data_dir() -> Path:
    return settings.project_root / "data"


def _read_yaml(path: Path) -> dict:
    """Read UTF-8 YAML deterministically — never fall back to system codepage."""
    with path.open("rb") as fh:
        return yaml.safe_load(fh)


@lru_cache(maxsize=1)
def load_catalog() -> list[Cake]:
    raw = _read_yaml(_data_dir() / "catalog.yaml")
    return [Cake.model_validate(item) for item in raw["cakes"]]


@lru_cache(maxsize=1)
def load_policies() -> dict:
    return _read_yaml(_data_dir() / "policies.yaml")


@lru_cache(maxsize=1)
def load_kitchen_calendar() -> dict:
    return _read_yaml(_data_dir() / "kitchen_calendar.yaml")


def cake_by_slug(slug: str) -> Cake | None:
    for cake in load_catalog():
        if cake.slug == slug:
            return cake
    return None
