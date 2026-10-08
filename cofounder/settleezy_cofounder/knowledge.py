"""What Settleezy is. Every prompt describes the company from here, so it stays consistent."""

from __future__ import annotations

from .config import COFOUNDER_DIR

FACTS_PATH = COFOUNDER_DIR / "knowledge" / "settleezy.md"
PLAYBOOK_PATH = COFOUNDER_DIR / "playbook" / "berlin-growth-playbook.md"

ONE_LINER = (
    "Settleezy helps students in Berlin, especially international students, save money and settle in. "
    "Its membership app (30-day free trial, then €40 per semester or €70 per year) lets members save at restaurants, "
    "cafés, grocery stores, activities, events and more, and includes guides, an expense and savings tracker, "
    "a document vault and emergency numbers. Settleezy also runs workshops (portfolio, CV and more) and partners "
    "with Berlin universities through its Buddy platform, which gives new incoming students A-to-Z support "
    "including accommodation support."
)


def facts() -> str:
    """The facts file without unfilled TODO lines (so drafts never mention placeholders)."""
    if not FACTS_PATH.exists():
        return ONE_LINER
    lines = [ln for ln in FACTS_PATH.read_text(encoding="utf-8").splitlines() if "TODO" not in ln]
    return "\n".join(lines).strip()


def playbook() -> str:
    return PLAYBOOK_PATH.read_text(encoding="utf-8") if PLAYBOOK_PATH.exists() else ""
