"""Microsoft Graph client for Outlook mail + calendar.

Safety by construction: the app only ever requests ``Mail.ReadWrite`` (read mail, create drafts)
and ``Calendars.Read``. It never requests ``Mail.Send``, so Microsoft itself will refuse any attempt
to send email from this code. Every draft is tagged with an Outlook category so you can find them.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

import httpx

from .config import Config, secret

GRAPH = "https://graph.microsoft.com/v1.0"
SCOPES = ["User.Read", "Mail.ReadWrite", "Calendars.Read"]
assert not any(s.lower().startswith("mail.send") for s in SCOPES), "drafts-only: never request Mail.Send"


class GraphError(RuntimeError):
    pass


class Graph:
    def __init__(self, cfg: Config):
        import msal

        self.cfg = cfg
        self.category = cfg.get("outlook.draft_category", "Settleezy AI")
        self.cache_path = Path(cfg.data_dir) / "msal_cache.bin"
        self.cache = msal.SerializableTokenCache()
        if self.cache_path.exists():
            self.cache.deserialize(self.cache_path.read_text(encoding="utf-8"))
        self.app = msal.PublicClientApplication(
            secret("MS_CLIENT_ID", required=True),
            authority=f"https://login.microsoftonline.com/{secret('MS_TENANT_ID') or 'organizations'}",
            token_cache=self.cache,
        )
        self.http = httpx.Client(timeout=60)

    # -- auth -----------------------------------------------------------
    def _save_cache(self) -> None:
        if self.cache.has_state_changed:
            self.cache_path.write_text(self.cache.serialize(), encoding="utf-8")

    def login(self) -> str:
        """Interactive device-code login (run once: `sz auth outlook`)."""
        flow = self.app.initiate_device_flow(scopes=SCOPES)
        if "user_code" not in flow:
            raise GraphError(f"Could not start device login: {flow}")
        print(flow["message"], flush=True)
        result = self.app.acquire_token_by_device_flow(flow)
        self._save_cache()
        if "access_token" not in result:
            raise GraphError(f"Login failed: {result.get('error_description')}")
        return result.get("id_token_claims", {}).get("preferred_username", "")

    def token(self) -> str:
        accounts = self.app.get_accounts()
        result = self.app.acquire_token_silent(SCOPES, account=accounts[0]) if accounts else None
        self._save_cache()
        if not result or "access_token" not in result:
            raise GraphError("Not signed in to Outlook. Run: sz auth outlook")
        return result["access_token"]

    # -- http -----------------------------------------------------------
    def _headers(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        h = {"Authorization": f"Bearer {self.token()}", "Prefer": 'outlook.body-content-type="text"'}
        h.update(extra or {})
        return h

    def get(self, path: str, params: dict[str, Any] | None = None, headers: dict[str, str] | None = None) -> dict:
        r = self.http.get(path if path.startswith("http") else GRAPH + path, params=params, headers=self._headers(headers))
        if r.status_code >= 400:
            raise GraphError(f"GET {path} -> {r.status_code}: {r.text[:300]}")
        return r.json()

    def paged(self, path: str, params: dict[str, Any] | None = None, limit: int = 5000, headers: dict[str, str] | None = None) -> Iterator[dict]:
        data = self.get(path, params, headers)
        n = 0
        while True:
            for item in data.get("value", []):
                yield item
                n += 1
                if n >= limit:
                    return
            nxt = data.get("@odata.nextLink")
            if not nxt:
                return
            data = self.get(nxt, headers=headers)

    def _send_json(self, method: str, path: str, body: dict | None = None) -> dict:
        r = self.http.request(method, GRAPH + path, json=body, headers=self._headers({"Content-Type": "application/json"}))
        if r.status_code >= 400:
            raise GraphError(f"{method} {path} -> {r.status_code}: {r.text[:300]}")
        return r.json() if r.content else {}

    # -- mail -----------------------------------------------------------
    MESSAGE_FIELDS = (
        "id,conversationId,subject,from,toRecipients,ccRecipients,receivedDateTime,sentDateTime,"
        "isRead,body,isDraft,internetMessageHeaders"
    )

    def messages(self, folder: str, since: datetime | None, limit: int = 5000) -> Iterator[dict]:
        """folder: 'inbox' or 'sentitems'. Newest first."""
        date_field = "sentDateTime" if folder == "sentitems" else "receivedDateTime"
        params: dict[str, Any] = {"$select": self.MESSAGE_FIELDS, "$top": 50, "$orderby": f"{date_field} desc"}
        if since:
            params["$filter"] = f"{date_field} ge {since.strftime('%Y-%m-%dT%H:%M:%SZ')}"
        yield from self.paged(f"/me/mailFolders/{folder}/messages", params, limit)

    def me(self) -> dict:
        return self.get("/me", {"$select": "displayName,mail,userPrincipalName,proxyAddresses"})

    def create_reply_draft(self, message_id: str, text: str, reply_all: bool = False) -> dict:
        """Create a reply draft above the quoted thread. Not sent."""
        action = "createReplyAll" if reply_all else "createReply"
        draft = self._send_json("POST", f"/me/messages/{message_id}/{action}", {"comment": text})
        self._send_json("PATCH", f"/me/messages/{draft['id']}", {"categories": [self.category]})
        return draft

    def create_draft(self, to: list[str], subject: str, text: str) -> dict:
        """Create a new message in Drafts. Not sent."""
        body = {
            "subject": subject,
            "body": {"contentType": "Text", "content": text},
            "toRecipients": [{"emailAddress": {"address": a}} for a in to],
            "categories": [self.category],
        }
        return self._send_json("POST", "/me/messages", body)

    def count_drafts(self) -> int:
        data = self.get(
            "/me/mailFolders/drafts/messages",
            {"$filter": f"categories/any(c:c eq '{self.category}')", "$count": "true", "$top": 1},
            headers={"ConsistencyLevel": "eventual"},
        )
        return int(data.get("@odata.count", len(data.get("value", []))))

    # -- calendar -------------------------------------------------------
    def calendar_view(self, start: datetime, end: datetime) -> list[dict]:
        params = {
            "startDateTime": start.isoformat(),
            "endDateTime": end.isoformat(),
            "$select": "id,subject,start,end,location,attendees,onlineMeeting,webLink,isCancelled",
            "$orderby": "start/dateTime",
            "$top": 100,
        }
        tz = self.cfg.get("me.timezone", "Europe/Berlin")
        return [e for e in self.paged("/me/calendarView", params, headers={"Prefer": f'outlook.timezone="{tz}"'}) if not e.get("isCancelled")]
