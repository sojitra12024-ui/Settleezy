import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture()
def cfg(tmp_path, monkeypatch):
    monkeypatch.setenv("SZ_HOME", str(tmp_path))
    monkeypatch.setenv("SZ_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    (tmp_path / "config.toml").write_text(
        Path(__file__).resolve().parents[1].joinpath("config.example.toml").read_text(encoding="utf-8").replace(
            'emails = ["you@settleezy.de"]', 'emails = ["me@settleezy.de"]'
        ),
        encoding="utf-8",
    )
    from settleezy_cofounder.config import load_config

    return load_config()


@pytest.fixture()
def db(cfg):
    from settleezy_cofounder.db import DB

    d = DB(cfg.db_path)
    yield d
    d.close()
