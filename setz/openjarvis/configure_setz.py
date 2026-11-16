"""Configure an installed OpenJarvis as Setz: brain, model, system prompt and Setz skills.

Run with the OpenJarvis environment, e.g.  uv run --project <OpenJarvis src> python configure_setz.py --brain claude
Edits ~/.openjarvis/config.toml in place (a backup is kept) and copies the Setz skills.
Never writes keys: the Anthropic key stays in the ANTHROPIC_API_KEY user variable,
the access key is created by `jarvis auth create-key`.
"""
from __future__ import annotations

import argparse
import re
import shutil
import tomllib
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
HOME = Path.home() / ".openjarvis"
MODELS = {"claude": "claude-sonnet-5-5", "local": "qwen3.5:4b"}


def set_key(text: str, section: str, key: str, value: str) -> str:
    """Set `key = "value"` inside [section], adding the section or key if missing."""
    line = f'{key} = "{value}"'
    m = re.search(rf"(?m)^\[{re.escape(section)}\]\s*$", text)
    if not m:
        return text.rstrip() + f"\n\n[{section}]\n{line}\n"
    start = m.end()
    nxt = re.search(r"(?m)^\[", text[start:])
    end = start + nxt.start() if nxt else len(text)
    body = text[start:end]
    if re.search(rf"(?m)^{re.escape(key)}\s*=", body):
        body = re.sub(rf'(?m)^{re.escape(key)}\s*=.*$', line.replace("\\", "\\\\"), body, count=1)
    else:
        body = "\n" + line + body
    return text[:start] + body + text[end:]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--brain", choices=["claude", "local"], default="claude")
    ap.add_argument("--model", default="")
    args = ap.parse_args()

    cfg = HOME / "config.toml"
    if not cfg.exists():
        raise SystemExit("No ~/.openjarvis/config.toml. Run `jarvis init --preset chat-simple --force` first.")
    text = cfg.read_text(encoding="utf-8")
    shutil.copy2(cfg, cfg.with_suffix(".toml.before-setz"))

    prompt = HOME / "setz-system-prompt.md"
    shutil.copy2(HERE / "setz-system-prompt.md", prompt)

    text = set_key(text, "engine", "default", "cloud" if args.brain == "claude" else "ollama")
    text = set_key(text, "intelligence", "default_model", args.model or MODELS[args.brain])
    text = set_key(text, "agent", "default_agent", "orchestrator" if args.brain == "claude" else "simple")
    text = set_key(text, "agent", "system_prompt_path", prompt.as_posix())
    text = set_key(text, "server", "host", "127.0.0.1")  # loopback only; the phone comes in through Tailscale
    tomllib.loads(text)  # refuse to write a broken file
    cfg.write_text(text, encoding="utf-8")

    skills = HOME / "skills"
    skills.mkdir(parents=True, exist_ok=True)
    copied = []
    for src in sorted((REPO / ".claude" / "skills").glob("setz-*")):
        dst = skills / src.name
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(src, dst)
        copied.append(src.name)
    print(f"Brain: {args.brain} ({args.model or MODELS[args.brain]}). Skills: {', '.join(copied)}")
    print(f"Config: {cfg} (backup: {cfg.name}.before-setz)")


if __name__ == "__main__":
    main()
