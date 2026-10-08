"""`sz doctor`: check every integration end to end and say exactly how to fix what's broken.

Each check does a real, read-only call (sign-in, a profile read, one message, one calendar view...),
so "OK" means it actually works, not just that a key is present.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from typing import Callable

import httpx

from .config import Config, secret


@dataclass
class Check:
    name: str
    ok: bool
    detail: str
    fix: str = ""
    optional: bool = False


def _outlook(cfg: Config) -> Check:
    if not secret("MS_CLIENT_ID"):
        return Check("Outlook", False, "not configured", "Add MS_CLIENT_ID and MS_TENANT_ID to .env (README step 3), then run: sz auth outlook")
    from .msgraph import SCOPES, Graph, GraphError

    try:
        g = Graph(cfg)
        accounts = g.app.get_accounts()
        if not accounts:
            return Check("Outlook", False, "not signed in", "Run: sz auth outlook")
        result = g.app.acquire_token_silent(SCOPES, account=accounts[0])
        g._save_cache()
        if not result or "access_token" not in result:
            return Check("Outlook", False, "sign-in expired", "Run: sz auth outlook")
        granted = {s.lower().rsplit("/", 1)[-1] for s in (result.get("scope") or "").split()}
        missing = [s for s in SCOPES if s.lower() not in granted] if granted else []
        if any(s.startswith("mail.send") for s in granted):
            return Check("Outlook", False, "app has Mail.Send permission",
                         "Remove Mail.Send from the app registration's API permissions (drafts-only safety)")
        me = g.me()
        inbox = g.get("/me/mailFolders/inbox/messages", {"$top": 1, "$select": "id,receivedDateTime"}).get("value", [])
        start = datetime.now().astimezone().replace(hour=0, minute=0, second=0, microsecond=0)
        events = g.calendar_view(start, start + timedelta(days=1))
        detail = f"{me.get('mail') or me.get('userPrincipalName')} · inbox readable · {len(events)} events today"
        if missing:
            return Check("Outlook", False, detail + f" · missing permission {', '.join(missing)}",
                         "Add the missing delegated permission in Entra, grant admin consent, then: sz auth outlook")
        if inbox:
            detail += f" · newest mail {inbox[0]['receivedDateTime'][:10]}"
        return Check("Outlook", True, detail)
    except GraphError as exc:
        return Check("Outlook", False, str(exc)[:200], "Run: sz auth outlook  (if it persists, check the app registration in README step 3)")
    except Exception as exc:
        return Check("Outlook", False, f"{type(exc).__name__}: {exc}"[:200], "Check MS_CLIENT_ID / MS_TENANT_ID in .env")


def _instagram(cfg: Config) -> Check:
    if not secret("IG_ACCESS_TOKEN") or not secret("IG_USER_ID"):
        return Check("Instagram", False, "not configured", "Run: sz auth instagram  (README step 6)")
    from .instagram import Instagram

    try:
        ig = Instagram(cfg)
        prof = ig.profile()
        detail = f"@{prof.get('username')} · {prof.get('followers_count', 0):,} followers"
        insights = ig.daily_insights()
        detail += f" · insights: {', '.join(insights) or 'none (check instagram_manage_insights)'}"
        expiry = token_expiry(ig.token)
        if expiry == 0:
            detail += " · token never expires"
        elif expiry:
            days = (expiry - time.time()) / 86400
            detail += f" · token expires in {days:.0f} days"
            if days < 10:
                return Check("Instagram", False, detail, "Token expires soon. Run: sz auth instagram")
        return Check("Instagram", True, detail)
    except Exception as exc:
        msg = str(exc)
        fix = "Run: sz auth instagram"
        if "permission" in msg.lower():
            fix = "Regenerate the token with instagram_basic, instagram_manage_insights, instagram_manage_comments, pages_show_list, pages_read_engagement"
        return Check("Instagram", False, msg[:200], fix)


def token_expiry(token: str) -> float | None:
    """Unix expiry of a Meta token (0 = never), or None if it can't be checked (needs FB_APP_ID/FB_APP_SECRET)."""
    app_id, app_secret = secret("FB_APP_ID"), secret("FB_APP_SECRET")
    if not (app_id and app_secret):
        return None
    try:
        data = httpx.get(
            "https://graph.facebook.com/debug_token",
            params={"input_token": token, "access_token": f"{app_id}|{app_secret}"},
            timeout=20,
        ).json().get("data", {})
        return float(data.get("expires_at", 0) or 0) if data.get("is_valid") else time.time()
    except (httpx.HTTPError, ValueError):
        return None


def _calendly(cfg: Config) -> Check:
    if not secret("CALENDLY_TOKEN"):
        return Check("Calendly", False, "not configured", "Add CALENDLY_TOKEN to .env (README step 5)", optional=True)
    try:
        r = httpx.get("https://api.calendly.com/users/me", headers={"Authorization": f"Bearer {secret('CALENDLY_TOKEN')}"}, timeout=20)
        if r.status_code == 401:
            return Check("Calendly", False, "token rejected", "Generate a new personal access token in Calendly", optional=True)
        r.raise_for_status()
        return Check("Calendly", True, r.json()["resource"].get("email", "connected"), optional=True)
    except httpx.HTTPError as exc:
        return Check("Calendly", False, str(exc)[:200], "Check your internet connection / token", optional=True)


def _claude(cfg: Config) -> Check:
    if not secret("ANTHROPIC_API_KEY"):
        return Check("Claude API", False, "no key: drafts use the local model", "Add ANTHROPIC_API_KEY to .env (README step 4)")
    if not cfg.get("llm.cloud_enabled", True):
        return Check("Claude API", True, "disabled in config (local only)")
    import anthropic

    model = cfg.get("llm.cloud_model", "claude-opus-5-5")
    try:
        anthropic.Anthropic().models.retrieve(model)
        return Check("Claude API", True, f"key valid · {model}")
    except anthropic.AuthenticationError:
        return Check("Claude API", False, "key rejected", "Create a new key at platform.claude.com")
    except anthropic.NotFoundError:
        return Check("Claude API", False, f"model {model} not available to this key", "Set llm.cloud_model in config.toml")
    except anthropic.APIConnectionError as exc:
        return Check("Claude API", False, f"cannot reach API: {exc}"[:200], "Check your internet connection")


def _ollama(cfg: Config) -> Check:
    url = cfg.get("llm.ollama_url", "http://localhost:11434")
    model = cfg.get("llm.local_model", "qwen2.5:7b-instruct")
    try:
        tags = [m["name"] for m in httpx.get(f"{url}/api/tags", timeout=5).json().get("models", [])]
    except httpx.HTTPError:
        return Check("Local model (Ollama)", False, "Ollama not running", "Start Ollama (it runs in the tray), or run: ollama serve")
    if not any(t == model or t.startswith(model + ":") or t.split(":")[0] == model for t in tags):
        return Check("Local model (Ollama)", False, f"{model} not downloaded", f"Run: ollama pull {model}")
    return Check("Local model (Ollama)", True, model)


def _elevenlabs(cfg: Config) -> Check:
    if not secret("ELEVENLABS_API_KEY"):
        return Check("Voice (ElevenLabs)", False, "no key: using Windows voices", "Add ELEVENLABS_API_KEY to .env", optional=True)
    from . import tts

    try:
        return Check("Voice (ElevenLabs)", True, f"voice: {tts.check(cfg)}", optional=True)
    except Exception as exc:
        return Check("Voice (ElevenLabs)", False, str(exc)[:200], "See README: add the voice to My Voices", optional=True)


def _memory(cfg: Config) -> Check:
    from .brain import embedder

    e = embedder(cfg, retry=True)
    if e.name.startswith("fastembed:"):
        return Check("Memory embeddings", True, e.name.split(":", 1)[1].split("/")[-1] + " (English + German)", optional=True)
    if e.mode == "hash":
        return Check("Memory embeddings", True, "built-in offline embedder (brain.embedder = \"hash\")",
                     "For better English + German recall: pip install fastembed and set brain.embedder = \"auto\"", optional=True)
    fix = ("pip install fastembed  (the model downloads once, ~220 MB, then works offline)"
           if "not installed" in e.note else "Connect to the internet once so the fastembed model can download, then re-run")
    return Check("Memory embeddings", False, f"using {e.name}: {e.note or 'fallback'}"[:200], fix, optional=True)


def _vad(cfg: Config) -> Check:
    from .vad import make_vad

    try:
        v = make_vad(cfg)
    except Exception as exc:
        return Check("Speech detection", False, str(exc)[:200], 'pip install -e ".[voice]"', optional=True)
    if v.name == "silero":
        return Check("Speech detection", True, "Silero VAD (ignores background noise)", optional=True)
    return Check("Speech detection", False, "loudness threshold (Silero VAD not available)",
                 'pip install -e ".[voice]" (faster-whisper includes the Silero model)', optional=True)


CHECKS: dict[str, Callable[[Config], Check]] = {
    "outlook": _outlook,
    "instagram": _instagram,
    "calendly": _calendly,
    "claude": _claude,
    "ollama": _ollama,
    "elevenlabs": _elevenlabs,
    "memory": _memory,
    "vad": _vad,
}


def run_all(cfg: Config, db=None) -> list[dict]:
    results = []
    for fn in CHECKS.values():
        try:
            results.append(asdict(fn(cfg)))
        except Exception as exc:  # a broken check must never break the others
            results.append(asdict(Check(fn.__name__.strip("_"), False, f"check crashed: {exc}"[:200])))
    if db is not None:
        db.kv_set("connections", json.dumps({"at": datetime.now().isoformat(timespec="minutes"), "results": results}))
    return results


# -- Instagram token setup --------------------------------------------------

def instagram_setup(short_token: str, app_id: str, app_secret: str, http: httpx.Client | None = None) -> dict:
    """Short-lived user token -> long-lived user token -> Page token (doesn't expire) + IG business account id."""
    http = http or httpx.Client(timeout=30)
    base = "https://graph.facebook.com/v23.0"
    long = http.get(f"{base}/oauth/access_token", params={
        "grant_type": "fb_exchange_token", "client_id": app_id, "client_secret": app_secret, "fb_exchange_token": short_token,
    }).json()
    if "access_token" not in long:
        raise RuntimeError(f"Token exchange failed: {long.get('error', {}).get('message', long)}")
    pages = http.get(f"{base}/me/accounts", params={
        "fields": "name,access_token,instagram_business_account{id,username}", "access_token": long["access_token"],
    }).json().get("data", [])
    linked = [p for p in pages if p.get("instagram_business_account")]
    if not linked:
        raise RuntimeError("No Facebook Page with a linked Instagram professional account was found for this login. "
                           "Link your Instagram account to your Facebook Page (Instagram app: Settings → Account → Sharing / Accounts Center).")
    page = linked[0]
    return {
        "IG_ACCESS_TOKEN": page["access_token"],
        "IG_USER_ID": page["instagram_business_account"]["id"],
        "username": page["instagram_business_account"].get("username", ""),
        "page": page.get("name", ""),
    }


def write_env(path, values: dict[str, str]) -> None:
    """Update KEY=value lines in .env, keeping everything else."""
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    seen = set()
    for i, line in enumerate(lines):
        key = line.split("=", 1)[0].strip()
        if key in values:
            lines[i] = f"{key}={values[key]}"
            seen.add(key)
    lines += [f"{k}={v}" for k, v in values.items() if k not in seen]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
