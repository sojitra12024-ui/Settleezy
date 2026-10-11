"""Configuration: config.toml for settings, environment variables (or .env) for secrets."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

PACKAGE_DIR = Path(__file__).resolve().parent
COFOUNDER_DIR = PACKAGE_DIR.parent
REPO_ROOT = COFOUNDER_DIR.parent


def _load_dotenv(path: Path) -> None:
    """Minimal .env loader (KEY=value lines). Real environment variables win."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


@dataclass
class Config:
    raw: dict[str, Any]
    home: Path
    data_dir: Path
    me: dict[str, Any] = field(default_factory=dict)

    def section(self, name: str) -> dict[str, Any]:
        return self.raw.get(name, {})

    def get(self, dotted: str, default: Any = None) -> Any:
        node: Any = self.raw
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    @property
    def my_addresses(self) -> set[str]:
        return {a.lower() for a in self.me.get("emails", [])}

    @property
    def db_path(self) -> Path:
        return self.data_dir / "cofounder.db"


def load_config(path: str | os.PathLike[str] | None = None) -> Config:
    home = Path(os.environ.get("SZ_HOME", COFOUNDER_DIR))
    _load_dotenv(home / ".env")
    cfg_path = Path(path) if path else home / "config.toml"
    if not cfg_path.is_file():
        cfg_path = COFOUNDER_DIR / "config.example.toml"
    raw = tomllib.loads(cfg_path.read_text(encoding="utf-8"))
    data_dir = Path(os.environ.get("SZ_DATA_DIR", home / "data"))
    data_dir.mkdir(parents=True, exist_ok=True)
    return Config(raw=raw, home=home, data_dir=data_dir, me=raw.get("me", {}))


def secret(name: str, required: bool = False) -> str:
    value = os.environ.get(name, "")
    if required and not value:
        raise RuntimeError(f"Missing {name}. Add it to cofounder/.env (see .env.example).")
    return value
