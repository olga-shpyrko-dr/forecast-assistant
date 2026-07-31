#!/usr/bin/env bash
# Session-start hook: surface git + tooling context. Non-fatal; always exits 0.
set -uo pipefail

ROOT="${CLAUDE_PROJECT_DIR:-$(git rev-parse --show-toplevel 2>/dev/null || pwd)}"
cd "$ROOT" 2>/dev/null || true

echo ""
echo "── Forecast Assistant — session ──"
echo "• Branch: $(git branch --show-current 2>/dev/null || echo '?')"
echo "• Recent:"
git log --oneline -3 2>/dev/null | sed 's/^/    /' || true
echo "• Uncommitted: $(git status --porcelain 2>/dev/null | wc -l | tr -d ' ') file(s)"

echo "• Tooling:"
command -v ruff   >/dev/null 2>&1 && echo "    ruff $(ruff --version 2>/dev/null | awk '{print $2}')" || echo "    ruff MISSING — pip install -r requirements.txt"
command -v mypy   >/dev/null 2>&1 && echo "    mypy ok"   || echo "    mypy MISSING"
command -v pytest >/dev/null 2>&1 && echo "    pytest ok" || echo "    pytest MISSING"
command -v pulumi >/dev/null 2>&1 && echo "    pulumi ok (deploy = approval required)" || true

if git check-ignore .env >/dev/null 2>&1; then
  echo "• .env gitignored ✓ (never commit it)"
else
  echo "• ⚠ .env NOT gitignored — check before committing"
fi
echo "──────────────────────────────────"
exit 0
