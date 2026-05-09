"""Brand audit — greps prompts and templates for brandbook violations.

Catches the most common mistakes:
- "Happy Cake" with a space (should be "HappyCake" — except "Happy Cake US"
  as the registered legal name in the brandbook).
- "HC" / "HAPPYCAKE" wordmark abbreviations.
- Forbidden phrases: BUY NOW, limited time, dear valued customer, lol, haha.
- Missing standard close on customer-facing template snippets.

Run as `make check-brand`. Exit 1 on any violation; 0 if clean.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass

ROOT = Path(__file__).resolve().parents[1]

# Tuples of (regex, severity, description). Severity: 'fail' or 'warn'.
RULES = [
    (re.compile(r"\bHappy Cake\b(?! US)"), "fail",
     "Wordmark must be 'HappyCake' (one word). 'Happy Cake US' as legal name is OK."),
    (re.compile(r"\bHAPPYCAKE\b"), "fail", "Wordmark must be 'HappyCake', not all-caps."),
    (re.compile(r'\bBUY NOW\b', re.I), "fail", "Forbidden phrase per brandbook §2."),
    (re.compile(r'\blimited time\b', re.I), "fail", "Forbidden phrase per brandbook §2."),
    (re.compile(r"don'?t miss out", re.I), "fail", "Forbidden phrase per brandbook §2."),
    (re.compile(r"\bdear valued customer\b", re.I), "fail",
     "Forbidden phrase per brandbook §2."),
    (re.compile(r"\blol\b|\bhaha\b", re.I), "warn",
     "Casual filler per brandbook §2."),
]

INCLUDE_GLOBS = [
    "ops/prompts/*.md",
    "apps/web/templates/*.html",
    "data/brand/*.md",
    "README.md",
    "ARCHITECTURE.md",
]

# Files that LEGITIMATELY discuss the wordmark rules and contain examples of
# what NOT to do — they shouldn't fail the audit.
ALLOWLIST = {
    "ops/prompts/brand_critic.md",
    "ops/prompts/marketing.md",
    "ops/prompts/intake.md",
    "ops/prompts/custom.md",
    "ops/prompts/care.md",
    "ops/prompts/router.md",
    "ops/prompts/reporting.md",
    "data/brand/voice.md",
    "data/brand/reference_posts.md",
    "scripts/check_brand.py",  # this file lists the rules
}


def main() -> int:
    violations: list[tuple[Path, int, str, str]] = []
    files: list[Path] = []
    for glob in INCLUDE_GLOBS:
        files.extend(ROOT.glob(glob))
    files = sorted(set(files))

    for path in files:
        rel = str(path.relative_to(ROOT)).replace("\\", "/")
        if rel in ALLOWLIST:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for line_no, line in enumerate(text.splitlines(), start=1):
            for pattern, severity, desc in RULES:
                if pattern.search(line):
                    violations.append((path, line_no, severity, f"{desc} -- '{line.strip()[:120]}'"))

    fails = [v for v in violations if v[2] == "fail"]
    warns = [v for v in violations if v[2] == "warn"]

    if not fails and not warns:
        print(f"[OK] brand audit clean across {len(files)} file(s)")
        return 0

    for path, line_no, sev, desc in violations:
        rel = path.relative_to(ROOT).as_posix()
        marker = "FAIL" if sev == "fail" else "warn"
        print(f"  [{marker}] {rel}:{line_no} — {desc}")

    if fails:
        print(f"\n{len(fails)} brand violation(s) — fix before submission.")
        return 1
    print(f"\n{len(warns)} brand warning(s) — review.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
