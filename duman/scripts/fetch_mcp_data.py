"""Fetch the canonical seeded data from the hackathon MCP.

Writes:
  data/mcp_sales_history.json     — marketing_get_sales_history
  data/mcp_margins.json           — marketing_get_margin_by_product
  data/mcp_budget.json            — marketing_get_budget
  data/mcp_catalog.json           — square_list_catalog
  data/mcp_kitchen.json           — kitchen_get_capacity + kitchen_get_menu_constraints
  data/mcp_pos_summary.json       — square_get_pos_summary
  data/mcp_recent_sales.csv       — square_recent_sales_csv (the canonical 6-month CSV)

The notebook reads these files (committed to git, deterministic) so a fresh
clone with the team token produces identical numbers.
"""

from __future__ import annotations

import asyncio
import csv
import io
import json
import sys
from pathlib import Path

# Force UTF-8 stdout on Windows so we can print Unicode marks safely.
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass

from happycake.mcp.hosted import MCPError, hosted_mcp
from happycake.settings import settings

DATA = settings.project_root / "data"


async def _save(name: str, content: dict | list) -> None:
    out = DATA / name
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(content, indent=2, ensure_ascii=False),
                   encoding="utf-8")
    print(f"  wrote {name}")


async def main() -> None:
    h = hosted_mcp()
    if not h.is_configured():
        print("MCP_TEAM_TOKEN is not set; skipping fetch.")
        return

    pulls = [
        ("mcp_budget.json",         "marketing_get_budget",            None),
        ("mcp_sales_history.json",  "marketing_get_sales_history",     None),
        ("mcp_margins.json",        "marketing_get_margin_by_product", None),
        ("mcp_catalog.json",        "square_list_catalog",             {"limit": 50}),
        ("mcp_kitchen_capacity.json", "kitchen_get_capacity",          None),
        ("mcp_kitchen_constraints.json", "kitchen_get_menu_constraints", None),
        ("mcp_pos_summary.json",    "square_get_pos_summary",          None),
    ]
    for filename, tool, args in pulls:
        try:
            r = await h.call_tool(tool, args)
            await _save(filename, r)
        except MCPError as exc:
            print(f"  ! {tool} failed: {exc}")

    # square_recent_sales_csv returns the CSV body inside a JSON object.
    try:
        r = await h.call_tool("square_recent_sales_csv")
        if isinstance(r, dict) and "csv" in r:
            csv_text = r["csv"]
        elif isinstance(r, dict) and "text" in r:
            csv_text = r["text"]
        elif isinstance(r, str):
            csv_text = r
        else:
            csv_text = json.dumps(r)
        out = DATA / "mcp_recent_sales.csv"
        out.write_text(csv_text, encoding="utf-8")
        # also count rows for the user
        rdr = csv.reader(io.StringIO(csv_text))
        row_count = sum(1 for _ in rdr) - 1
        print(f"  wrote mcp_recent_sales.csv ({row_count} rows)")
    except MCPError as exc:
        print(f"  ! square_recent_sales_csv failed: {exc}")

    await h.close()


if __name__ == "__main__":
    asyncio.run(main())
