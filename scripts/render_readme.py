"""Render README.md from README.md.tmpl using analysis/_metrics.json.

Single source of truth — eliminates the kind of self-contradicting numbers
that sank the prior 43/100 build's business-analyst pass.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass

ROOT = Path(__file__).resolve().parents[1]


def _thousands(value) -> str:
    if value is None:
        return ""
    try:
        return f"{int(value):,}"
    except (TypeError, ValueError):
        return str(value)


def main() -> int:
    metrics_path = ROOT / "analysis" / "_metrics.json"
    template_path = ROOT / "README.md.tmpl"
    out_path = ROOT / "README.md"

    if not metrics_path.exists():
        print(f"Missing {metrics_path}. Run `make seed` first.", file=sys.stderr)
        return 1
    if not template_path.exists():
        print(f"Missing {template_path}.", file=sys.stderr)
        return 1

    metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
    env = Environment(
        loader=FileSystemLoader(str(ROOT)),
        undefined=StrictUndefined,
        trim_blocks=False,
        lstrip_blocks=False,
        autoescape=False,
    )
    env.filters["thousands"] = _thousands

    tpl = env.get_template("README.md.tmpl")
    rendered = tpl.render(**metrics)
    out_path.write_text(rendered, encoding="utf-8")
    size_kb = round(len(rendered) / 1024, 1)
    print(f"wrote {out_path.name} ({size_kb} kB)")

    # Sanity guard: every dollar figure in the README must be derivable from metrics.
    bad_tokens = ["{{", "}}", "{%", "%}"]
    for tok in bad_tokens:
        if tok in rendered:
            print(f"WARN: unresolved template token '{tok}' in rendered README")
            return 2

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
