"""Pure text helpers for email: strip quoted history, detect language, spot automated mail."""

from __future__ import annotations

import re

_QUOTE_MARKERS = [
    re.compile(r"^\s*On .{5,200}wrote:\s*$", re.M),
    re.compile(r"^\s*Am .{5,200}schrieb .{0,200}:\s*$", re.M),
    re.compile(r"^\s*-{2,}\s*(Original Message|Ursprüngliche Nachricht)\s*-{2,}", re.M | re.I),
    re.compile(r"^\s*(From|Von):\s.+\n\s*(Sent|Gesendet|Date|Datum):\s", re.M),
    re.compile(r"^_{10,}\s*$", re.M),
]

_DE_WORDS = set(
    "und der die das ich nicht ist mit sie für auf ein eine wir ihr bitte danke vielen gerne "
    "viele grüße liebe lieber sehr geehrte hallo zu den dem von auch noch wie kann können haben "
    "wäre würde freundlichen gruß beste".split()
)
_EN_WORDS = set(
    "the and to of you for is that with your this we i be on are it have please thanks thank "
    "regards best hi hello would could will can let know looking forward kind".split()
)

_AUTOMATED_SENDER = re.compile(
    r"(no-?reply|do-?not-?reply|notification|notifications|mailer-daemon|postmaster|newsletter|"
    r"news@|info@.*(linkedin|facebook|instagram|google|microsoft)|calendly|billing|invoice|receipt|"
    r"support@(stripe|paypal|hostinger)|marketing@|updates@|digest)",
    re.I,
)
_AUTOMATED_HEADERS = {"list-unsubscribe", "list-id", "auto-submitted", "x-auto-response-suppress", "precedence"}


def strip_quoted(text: str) -> str:
    """Return only the author's new text (drop quoted history and '>' lines)."""
    if not text:
        return ""
    cut = len(text)
    for rx in _QUOTE_MARKERS:
        m = rx.search(text)
        if m:
            cut = min(cut, m.start())
    lines = [ln for ln in text[:cut].splitlines() if not ln.lstrip().startswith(">")]
    out = "\n".join(lines)
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out.strip()


def detect_language(text: str) -> str:
    words = re.findall(r"[a-zäöüß]+", text.lower())
    de = sum(w in _DE_WORDS for w in words) + 2 * sum(ch in "äöüß" for ch in text.lower()[:2000])
    en = sum(w in _EN_WORDS for w in words)
    if de == en == 0:
        return "unknown"
    return "de" if de > en else "en"


def is_automated(from_addr: str, subject: str = "", headers: list[dict] | None = None) -> bool:
    if _AUTOMATED_SENDER.search(from_addr or ""):
        return True
    for h in headers or []:
        name = (h.get("name") or "").lower()
        if name in _AUTOMATED_HEADERS:
            if name == "precedence" and (h.get("value") or "").lower() not in {"bulk", "list", "junk"}:
                continue
            if name == "auto-submitted" and (h.get("value") or "").lower() == "no":
                continue
            return True
    return bool(re.search(r"^(automatic reply|autoreply|abwesenheitsnotiz|out of office)", subject or "", re.I))


def first_line(text: str) -> str:
    for ln in text.splitlines():
        if ln.strip():
            return ln.strip()
    return ""


def greeting(text: str) -> str:
    """Normalised opening, e.g. 'Hi {name},' / 'Hallo {name},'."""
    line = first_line(text)
    if len(line) > 60:
        return ""
    words = line.rstrip(",!:").split()
    if not words:
        return ""
    head = words[0]
    if head.lower() in {"sehr", "dear", "liebe", "lieber", "good", "guten"} and len(words) > 1:
        head = " ".join(words[:2])
    rest = "{name}" if len(words) > len(head.split()) else ""
    return f"{head} {rest}".strip() + ("," if line.endswith(",") else "")


def sign_off(text: str) -> str:
    """The closing phrase line before the signature name, e.g. 'Best,' / 'Viele Grüße'."""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    for ln in reversed(lines[-6:]):
        if re.match(
            r"^(best|cheers|thanks|thank you|kind regards|best regards|warm regards|regards|talk soon|"
            r"viele grüße|beste grüße|liebe grüße|lg|vg|mit freundlichen grüßen|danke|grüße|bis bald)\b",
            ln,
            re.I,
        ):
            return ln.rstrip()
    return ""
