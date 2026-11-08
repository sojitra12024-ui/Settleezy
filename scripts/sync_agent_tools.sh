#!/usr/bin/env bash
# Refresh .claude/ skills, agents and references from the vendor/ submodules.
# Usage: scripts/sync_agent_tools.sh [--update]   (--update pulls the latest upstream first)
set -euo pipefail
cd "$(dirname "$0")/.."
subs="vendor/agent-skills vendor/open-seo vendor/ponytail"
if [ "${1:-}" = "--update" ]; then git submodule update --init --remote --depth 1 $subs
else git submodule update --init --depth 1 $subs; fi
mkdir -p .claude/skills .claude/agents .claude/references
cp -r vendor/agent-skills/skills/* .claude/skills/
cp vendor/agent-skills/agents/*.md .claude/agents/
cp vendor/agent-skills/references/*.md .claude/references/
cp -r vendor/open-seo/plugins/openseo/skills/* .claude/skills/
cp -r vendor/ponytail/skills/ponytail* .claude/skills/
echo "Synced $(ls .claude/skills | wc -l) skills and $(ls .claude/agents | wc -l) agents."
