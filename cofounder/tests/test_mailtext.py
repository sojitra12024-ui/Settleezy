from settleezy_cofounder.llm import redact
from settleezy_cofounder.mailtext import detect_language, greeting, is_automated, sign_off, strip_quoted


def test_strip_quoted_english():
    body = "Hi Anna,\n\nSounds great, Thursday works.\n\nBest,\nRaj\n\nOn Mon, 5 Oct 2026 at 10:00, Anna <a@x.de> wrote:\n> Can we meet?"
    assert strip_quoted(body) == "Hi Anna,\n\nSounds great, Thursday works.\n\nBest,\nRaj"


def test_strip_quoted_german_outlook_header():
    body = "Hallo Herr Weber,\nvielen Dank!\nViele Grüße\n\nVon: Weber <w@x.de>\nGesendet: Montag, 5. Oktober 2026\nBetreff: Kooperation"
    assert strip_quoted(body).endswith("Viele Grüße")


def test_detect_language():
    assert detect_language("Hallo, vielen Dank für die Rückmeldung. Viele Grüße") == "de"
    assert detect_language("Hi, thanks for getting back to me. Let me know if Thursday works.") == "en"


def test_is_automated():
    assert is_automated("no-reply@calendly.com")
    assert is_automated("anna@cafe.de", headers=[{"name": "List-Unsubscribe", "value": "<mailto:x>"}])
    assert not is_automated("anna@cafe.de", "Partnership", [{"name": "Auto-Submitted", "value": "no"}])
    assert is_automated("anna@cafe.de", "Automatic reply: away")


def test_greeting_and_sign_off():
    assert greeting("Hi Anna,\nthanks") == "Hi {name},"
    assert greeting("Sehr geehrte Frau Weber,\n...") == "Sehr geehrte {name},"
    assert sign_off("Thanks!\n\nBest,\nRaj\nSettleezy") == "Best,"
    assert sign_off("Bis dann\nViele Grüße\nRaj") == "Viele Grüße"


def test_redact():
    out = redact("Mail anna@cafe.de or call +49 30 1234 5678, IBAN DE89 3704 0044 0532 0130 00")
    assert "anna@" not in out and "1234" not in out and "DE89" not in out
