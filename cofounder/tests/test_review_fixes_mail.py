"""Regression tests for the mail/Graph/CLI review fixes (follow-up recipients, duplicate drafts, doctor scopes...)."""

import base64
import json

import pytest

from settleezy_cofounder import cli, drafts, mail_sync
from settleezy_cofounder.connections import _jwt_scopes
from settleezy_cofounder.msgraph import Graph, GraphError
from settleezy_cofounder.triage import Item

ME = "me@settleezy.de"


def _msg(mid, folder, to, cc=(), sent_at="2026-10-01T09:00:00+00:00"):
    return {"id": mid, "conversation_id": "c1", "folder": folder, "from_addr": ME, "from_name": "", "to_addrs": list(to),
            "cc_addrs": list(cc), "subject": "Partnership", "sent_at": sent_at, "is_read": 1, "body_text": "Interested?",
            "language": "en", "automated": 0}


class FakeLLM:
    def cloud(self, *a, **kw):
        return "Just following up."


class FakeGraph:
    def __init__(self):
        self.calls = []

    def create_reply_draft(self, mid, text, reply_all=False, **kw):
        self.calls.append((mid, text, kw))
        return {"id": "d1", "conversationId": "c1"}


def test_followup_draft_addresses_original_external_recipients(cfg, db):
    db.upsert_message(_msg("m1", "sent", [ME, "lea@brewlab.de"], cc=["Me@Settleezy.de", "tom@brewlab.de"]))
    db.conn.commit()
    g = FakeGraph()
    item = Item("followup", "c1", "m1", "lea@brewlab.de", "", "Partnership", 5.0, followup_number=1)
    drafts.draft_for_item(cfg, db, FakeLLM(), g, item)
    assert g.calls[0][2] == {"to": ["lea@brewlab.de"], "cc": ["tom@brewlab.de"]}
    assert db.one("SELECT COUNT(*) n FROM drafts WHERE source_message_id='m1' AND kind='followup'")["n"] == 1


def test_reply_draft_keeps_default_recipients(cfg, db):
    db.upsert_message(_msg("m2", "inbox", [ME]))
    db.conn.commit()
    g = FakeGraph()
    drafts.draft_for_item(cfg, db, FakeLLM(), g, Item("reply", "c1", "m2", "lea@brewlab.de", "", "Partnership", 1.0))
    assert g.calls[0][2] == {"to": None, "cc": None}


def _bare_graph():
    g = object.__new__(Graph)
    g.category = "Settleezy AI"
    return g


def test_create_reply_draft_payload_and_patch_failure_does_not_raise():
    g = _bare_graph()
    calls = []

    def send(method, path, body=None):
        calls.append((method, path, body))
        if method == "PATCH":
            raise GraphError("PATCH -> 503")
        return {"id": "d9"}

    g._send_json = send
    draft = g.create_reply_draft("m1", "hi", to=["lea@brewlab.de"])
    assert draft == {"id": "d9"}
    assert calls[0][2] == {"comment": "hi", "message": {"toRecipients": [{"emailAddress": {"address": "lea@brewlab.de"}}],
                                                        "ccRecipients": []}}
    assert calls[1][0] == "PATCH"
    calls.clear()
    g.create_reply_draft("m1", "hi")
    assert calls[0][2] == {"comment": "hi"}


def test_token_says_permission_changed_when_silent_refresh_fails():
    g = _bare_graph()

    class App:
        def __init__(self, accounts):
            self.accounts = accounts

        def get_accounts(self):
            return self.accounts

        def acquire_token_silent(self, scopes, account):
            return None

    g.scopes = ["User.Read"]
    g._save_cache = lambda: None
    g.app = App([{"username": ME}])
    with pytest.raises(GraphError, match="permission changed: re-run `sz auth outlook`"):
        g.token()
    g.app = App([])
    with pytest.raises(GraphError, match="Not signed in"):
        g.token()


def test_jwt_scopes_reads_scp_claim():
    payload = base64.urlsafe_b64encode(json.dumps({"scp": "Mail.ReadWrite Mail.Send User.Read"}).encode()).decode().rstrip("=")
    assert _jwt_scopes(f"eyJhbGciOiJub25lIn0.{payload}.sig") == "Mail.ReadWrite Mail.Send User.Read"
    assert _jwt_scopes("opaque-token") == ""
    assert _jwt_scopes("a.!!!notbase64.c") == ""


@pytest.mark.parametrize("choice", ["0", "-1", "3"])
def test_meetings_book_rejects_out_of_range_option(cfg, monkeypatch, choice):
    from settleezy_cofounder import scheduling as sch

    booked = []
    monkeypatch.setattr(sch, "requests", lambda cfg, db: [{"id": 7, "proposed_free": ["2026-10-14T15:00"],
                                                           "slots": [{"start": "2026-10-15T10:00"}]}])
    monkeypatch.setattr(sch, "book", lambda *a, **kw: booked.append(a) or {})
    with pytest.raises(SystemExit) as e:
        cli.main(["meetings", "book", "7", choice])
    assert "1-2" in str(e.value.code) and not booked
    cli.main(["meetings", "book", "7", "2"])
    assert booked[0][3] == "2026-10-15T10:00"


def test_main_reports_value_errors_without_traceback(cfg, capsys):
    with pytest.raises(SystemExit) as e:
        cli.main(["meetings", "draft", "not-a-number"])
    assert str(e.value.code).startswith("error: ")


def test_sync_commits_every_page(cfg, db):
    seen_in_tx = []

    class PagedGraph:
        def messages(self, folder, since, limit=5000):
            for i in range(120):
                if i in (50, 100):   # a new Graph page is being fetched: nothing may be left uncommitted
                    seen_in_tx.append(db.conn.in_transaction)
                yield {"id": f"{folder}{i}", "conversationId": "c", "subject": "s", "from": {"emailAddress": {"address": "a@b.de"}},
                       "receivedDateTime": "2026-10-01T09:00:00Z", "sentDateTime": "2026-10-01T09:00:00Z",
                       "body": {"content": "hello"}}

    counts = mail_sync.sync(cfg, db, PagedGraph())
    assert counts == {"inbox": 120, "sent": 120}
    assert seen_in_tx == [False] * 4
