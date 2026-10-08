"""Model router.

* ``local()``  -> Ollama on the laptop (RTX 3060). Used for anything that reads raw private mail:
                  triage, classification, extraction from scraped pages.
* ``cloud()``  -> Claude API. Used for drafting in the user's voice and for strategy/analysis.
                  Inputs are redacted (emails, phones, IBANs) before they leave the machine.

If cloud is disabled in config (or no key is set), ``cloud()`` falls back to the local model.
"""

from __future__ import annotations

import json
import re
from typing import Any

import httpx

from .config import Config, secret

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_PHONE = re.compile(r"(?:\+|00)\d[\d \-/()]{7,}\d")
_IBAN = re.compile(r"\b[A-Z]{2}\d{2}(?: ?[A-Z0-9]{4}){3,7}(?: ?[A-Z0-9]{1,4})?\b")


def redact(text: str) -> str:
    text = _EMAIL.sub("[email]", text)
    text = _IBAN.sub("[iban]", text)
    return _PHONE.sub("[phone]", text)


class LLMError(RuntimeError):
    pass


class LLM:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        llm = cfg.section("llm")
        self.ollama_url = llm.get("ollama_url", "http://localhost:11434")
        self.local_model = llm.get("local_model", "qwen2.5:7b-instruct")
        self.cloud_model = llm.get("cloud_model", "claude-opus-5-5")
        self.cloud_enabled = bool(llm.get("cloud_enabled", True)) and bool(secret("ANTHROPIC_API_KEY"))
        self._client: Any = None

    # -- local ----------------------------------------------------------
    def local(self, prompt: str, system: str = "", *, as_json: bool = False, timeout: float = 180) -> str:
        payload: dict[str, Any] = {
            "model": self.local_model,
            "stream": False,
            "messages": ([{"role": "system", "content": system}] if system else [])
            + [{"role": "user", "content": prompt}],
            "options": {"temperature": 0.2, "num_ctx": 8192},
        }
        if as_json:
            payload["format"] = "json"
        try:
            r = httpx.post(f"{self.ollama_url}/api/chat", json=payload, timeout=timeout)
            r.raise_for_status()
        except httpx.HTTPError as exc:
            raise LLMError(f"Ollama not reachable at {self.ollama_url} ({exc}). Is `ollama serve` running?") from exc
        return r.json()["message"]["content"]

    def local_json(self, prompt: str, system: str = "") -> Any:
        return parse_json(self.local(prompt, system, as_json=True))

    # -- cloud ----------------------------------------------------------
    def cloud(self, prompt: str, system: str = "", *, effort: str = "medium", max_tokens: int = 16000) -> str:
        prompt, system = redact(prompt), redact(system)
        if not self.cloud_enabled:
            return self.local(prompt, system, timeout=600)
        import anthropic

        if self._client is None:
            self._client = anthropic.Anthropic()
        try:
            with self._client.beta.messages.stream(
                model=self.cloud_model,
                max_tokens=max_tokens,
                system=system or anthropic.NOT_GIVEN,
                thinking={"type": "adaptive"},
                output_config={"effort": effort},
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
                messages=[{"role": "user", "content": prompt}],
            ) as stream:
                msg = stream.get_final_message()
        except anthropic.RateLimitError as exc:
            raise LLMError("Claude API rate limit hit; try again in a minute.") from exc
        except anthropic.APIStatusError as exc:
            raise LLMError(f"Claude API error {exc.status_code}: {exc.message}") from exc
        except anthropic.APIConnectionError as exc:
            raise LLMError(f"Could not reach the Claude API: {exc}") from exc
        if msg.stop_reason == "refusal":
            raise LLMError("Claude declined this request.")
        return "".join(b.text for b in msg.content if b.type == "text").strip()

    def cloud_json(self, prompt: str, system: str = "", *, effort: str = "medium") -> Any:
        return parse_json(self.cloud(prompt + "\n\nReturn only valid JSON, no prose.", system, effort=effort))


def parse_json(text: str) -> Any:
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = min((i for i in (text.find("{"), text.find("[")) if i >= 0), default=-1)
        if start < 0:
            raise LLMError(f"Model did not return JSON: {text[:200]}")
        end = max(text.rfind("}"), text.rfind("]"))
        return json.loads(text[start : end + 1])
