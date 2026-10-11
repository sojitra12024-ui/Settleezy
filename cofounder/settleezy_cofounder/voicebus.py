"""Tell the dashboard's hologram what the voice assistant is doing (idle / listening / thinking / speaking).

Fire-and-forget: if the dashboard isn't running, the voice assistant just carries on.
"""

from __future__ import annotations

import time

import httpx

from .config import Config


def publish(cfg: Config, state: str, text: str = "", envelope: list[float] | None = None, frame_ms: int = 40) -> None:
    port = int(cfg.get("dashboard.port", 8765))
    try:
        httpx.post(
            f"http://127.0.0.1:{port}/api/voice/event",
            json={"state": state, "text": text[:600], "envelope": envelope or [], "frame_ms": frame_ms, "at": time.time()},
            headers={"X-SZ": "1"},
            timeout=1.5,
        )
    except httpx.HTTPError:
        pass
