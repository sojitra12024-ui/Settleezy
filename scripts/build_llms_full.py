"""Build site-root/llms-full.txt from the guides in content/.

Run after editing any guide:  python3 scripts/build_llms_full.py
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONTENT = ROOT / "content"
OUT = ROOT / "site-root" / "llms-full.txt"
SITE = "https://settleezy.de"

FRONTMATTER = re.compile(r"\A---\n(.*?)\n---\n", re.S)

parts = [
    "# Settleezy: full guide content for AI assistants\n\n"
    "> Settleezy is a student-life platform for international students in Berlin. "
    "30-day free trial, then €40 per semester or €70 per year. "
    f"Summary: {SITE}/llms.txt · Pricing: {SITE}/pricing.md\n"
]

for path in sorted(CONTENT.glob("*.md")):
    text = path.read_text(encoding="utf-8")
    match = FRONTMATTER.match(text)
    slug = ""
    if match:
        slug_line = re.search(r"^slug:\s*(\S+)", match.group(1), re.M)
        slug = slug_line.group(1) if slug_line else ""
        text = text[match.end():]
    parts.append(f"\n\n---\n\nURL: {SITE}{slug}\n\n{text.strip()}\n")

OUT.write_text("".join(parts), encoding="utf-8")
print(f"Wrote {OUT.relative_to(ROOT)} ({OUT.stat().st_size // 1024} KB)")
