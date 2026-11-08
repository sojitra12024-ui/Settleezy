"""Check the kit before publishing: python3 scripts/check_kit.py

Fails (exit 1) on broken internal links, invalid JSON-LD, missing front matter,
meta descriptions over 160 characters, or a stale site-root/llms-full.txt.
Warns about placeholders still waiting for real values (e.g. #signup).
"""

import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONTENT = ROOT / "content"
SITE = "https://settleezy.de"
FRONTMATTER = re.compile(r"\A---\n(.*?)\n---\n", re.S)

errors, warnings = [], []
slugs = {"/"}

for path in sorted(CONTENT.glob("*.md")):
    name = path.relative_to(ROOT)
    text = path.read_text(encoding="utf-8")
    match = FRONTMATTER.match(text)
    if not match:
        errors.append(f"{name}: missing front matter")
        continue
    fields = dict(re.findall(r'^(\w+):\s*"?(.*?)"?\s*$', match.group(1), re.M))
    for key in ("title", "slug", "meta_description"):
        if not fields.get(key):
            errors.append(f"{name}: front matter has no {key}")
    slugs.add(fields.get("slug", ""))
    desc = fields.get("meta_description", "")
    if len(desc) > 160:
        errors.append(f"{name}: meta_description is {len(desc)} chars (max 160, Google truncates)")
    if "Last updated" not in text:
        errors.append(f"{name}: no 'Last updated' line")

# Files published as-is in public_html/
slugs |= {f"/{p.name}" for p in (ROOT / "site-root").iterdir()}

def check_link(source, url):
    path = url.split("#")[0] or "/"
    if path not in slugs:
        errors.append(f"{source}: link to unknown page {url}")

for path in sorted(CONTENT.glob("*.md")):
    for url in re.findall(r"\]\((/[^)\s]*)\)", path.read_text(encoding="utf-8")):
        check_link(path.relative_to(ROOT), url)

for path in [ROOT / "site-root/llms.txt", ROOT / "site-root/pricing.md"]:
    for url in re.findall(re.escape(SITE) + r"(/[^\s)>\"]*)?", path.read_text(encoding="utf-8")):
        check_link(path.relative_to(ROOT), url or "/")

schema = (ROOT / "schema/sitewide-schema.html").read_text(encoding="utf-8")
for i, block in enumerate(re.findall(r'<script type="application/ld\+json">(.*?)</script>', schema, re.S), 1):
    try:
        json.loads(block)
    except json.JSONDecodeError as exc:
        errors.append(f"schema/sitewide-schema.html: JSON-LD block {i} is invalid ({exc})")

for path in [*CONTENT.glob("*.md"), ROOT / "schema/sitewide-schema.html"]:
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if "(#signup)" in line or "REPLACE" in line:
            warnings.append(f"{path.relative_to(ROOT)}:{line_no}: placeholder still to replace")

llms_full = ROOT / "site-root/llms-full.txt"
before = llms_full.read_bytes()
subprocess.run([sys.executable, str(ROOT / "scripts/build_llms_full.py")], check=True, capture_output=True)
if llms_full.read_bytes() != before:
    errors.append("site-root/llms-full.txt was stale; it has been rebuilt, commit it")

for w in warnings:
    print(f"warning: {w}")
for e in errors:
    print(f"error: {e}")
print(f"{len(errors)} errors, {len(warnings)} warnings")
sys.exit(1 if errors else 0)
