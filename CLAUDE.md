# Settleezy

Student-life platform for international students in Berlin (settleezy.de, WordPress on Hostinger). This repo is the **AI search (GEO) kit**: content and files that get Settleezy cited when students ask ChatGPT, Claude, Perplexity, Gemini or Copilot about life in Berlin. See `README.md` for the layout.

## Facts that must stay consistent

- Pricing: 30-day free trial, then €40 per semester or €70 per year. It appears in `content/01-pricing-free-trial.md`, `site-root/pricing.md`, `site-root/llms.txt`, `schema/sitewide-schema.html` and the closing section of every guide. Change all of them together.
- Main domain: settleezy.de (settleezy.com is the old domain, see `playbook/00-confirm-before-publishing.md`).
- Never invent features, partners, prices or testimonials. Open questions are listed in `playbook/00-confirm-before-publishing.md`; ask the user rather than guess.
- Guides cite sources for every number and carry a "Last updated" line. Update both when numbers change (prices change every January and often in summer).

## After editing

```bash
python3 scripts/check_kit.py
```

It rebuilds `site-root/llms-full.txt` and checks links, front matter, meta description length (max 160) and JSON-LD. CI runs the same check on every PR.

## Skills in `.claude/skills/`

- SEO / AI visibility (OpenSEO): `ai-visibility-audit`, `ai-prompt-research`, `keyword-research`, `keyword-clustering`, `seo-audit`, `local-seo`, `competitor-analysis`, `link-prospecting`. Most need the OpenSEO MCP server from `.mcp.json` and an openseo.so account.
- Building the platform (agent-skills): `spec-driven-development`, `planning-and-task-breakdown`, `frontend-ui-engineering`, `performance-optimization`, `security-and-hardening`, `shipping-and-launch`, plus the `web-performance-auditor`, `security-auditor`, `code-reviewer` and `test-engineer` subagents.
- Lean changes: `/ponytail` (opt-in).

Refresh them from upstream with `scripts/sync_agent_tools.sh --update`. Other tools (Chatwoot, OpenProject, OmniRoute, ...) are reference submodules in `vendor/`, see `vendor/README.md`.
