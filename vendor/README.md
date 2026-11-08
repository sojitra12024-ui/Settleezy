# Vendored tools

Nine open-source projects, added as shallow git submodules. They are pinned references: a normal clone of Settleezy does not download them. Fetch one only when you need it:

```bash
git submodule update --init --depth 1 vendor/<name>
```

## Loaded into Claude Code (`.claude/`)

These work in every Claude Code session opened in this repo, with no extra setup.

| Submodule | What it adds | Why it helps Settleezy |
|---|---|---|
| `agent-skills` (MIT) | 25 engineering skills (spec, plan, TDD, review, security, performance, shipping) + 4 subagents (`code-reviewer`, `security-auditor`, `test-engineer`, `web-performance-auditor`) + checklists in `.claude/references/` | Structured workflow for building the platform; web-performance and accessibility audits for settleezy.de |
| `open-seo` (MIT) | 12 SEO skills (`ai-visibility-audit`, `ai-prompt-research`, `keyword-research`, `seo-audit`, `local-seo`, `competitor-analysis`, ...) + the OpenSEO MCP server in `.mcp.json` | Core to this kit: tracks and improves how Settleezy shows up in ChatGPT, Gemini and Google AI answers. The MCP tools need an openseo.so account (self-hosted or hosted, using your DataForSEO key) |
| `ponytail` (MIT) | 6 skills (`/ponytail`, `ponytail-review`, `ponytail-audit`, `ponytail-debt`, ...) | Keeps code changes small; opt-in, only active when invoked |

Refresh after upstream updates: `scripts/sync_agent_tools.sh --update`, then commit `.claude/`.

## Reference only (not loaded)

| Submodule | What it is | When to use it |
|---|---|---|
| `graphify` | Knowledge-graph builder for code/docs (Python CLI) | `pipx install graphifyy && graphify install --project` if the codebase grows large |
| `omniroute` | Self-hosted AI gateway routing to 60+ model providers | If Settleezy adds AI features and wants cheap fallback routing |
| `chatwoot` | Open-source customer support / live chat (Rails) | Self-host on a server for student support chat on settleezy.de |
| `openproject` | Open-source project management (Rails, GPL-3.0) | Self-host for team planning |
| `Cap` | Screen recording (Loom alternative), desktop app | Record onboarding/demo videos |
| `openwhispr` | Voice dictation + meeting notes, desktop app | Install locally from its releases page |

The server apps (chatwoot, openproject, omniroute) are deployed with their own Docker setups, not run from this repo. The desktop apps (Cap, openwhispr) are installed on your computer.
