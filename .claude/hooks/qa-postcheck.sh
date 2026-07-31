#!/usr/bin/env bash
# PostToolUse hook for Write|Edit. Auto-lints the just-edited Python file so problems
# surface immediately instead of at commit time. onFailure is "warn" — never blocks.
set -uo pipefail

ROOT="${CLAUDE_PROJECT_DIR:-$(git rev-parse --show-toplevel 2>/dev/null || pwd)}"

INPUT="$(cat 2>/dev/null || true)"
FILE=""
if command -v jq >/dev/null 2>&1; then
  FILE="$(printf '%s' "$INPUT" | jq -r '.tool_input.file_path // .tool_input.path // empty' 2>/dev/null)"
fi
if [ -z "$FILE" ]; then
  FILE="$(printf '%s' "$INPUT" | grep -oE '"file_path"[[:space:]]*:[[:space:]]*"[^"]+"' | head -1 | sed -E 's/.*"file_path"[[:space:]]*:[[:space:]]*"([^"]+)".*/\1/')"
fi

[ -z "$FILE" ] && exit 0
[ ! -f "$FILE" ] && exit 0

case "$FILE" in
  *.py) ;;
  *) exit 0 ;;  # only lint Python here
esac

command -v ruff >/dev/null 2>&1 || { echo "ℹ QA-POSTCHECK: ruff not installed; skipping lint of $FILE"; exit 0; }

cd "$ROOT" 2>/dev/null || true

OUT="$(ruff check "$FILE" 2>&1)"
FMT="$(ruff format --check "$FILE" 2>&1)"
RC=0
if printf '%s' "$OUT" | grep -qiE 'error|[0-9]+ error'; then RC=1; fi
if [ -n "$FMT" ] && printf '%s' "$FMT" | grep -qi 'would reformat'; then RC=1; fi

if [ "$RC" -ne 0 ]; then
  echo "⚠ QA-POSTCHECK: ruff flagged '$FILE':"
  printf '%s\n' "$OUT" | grep -vE '^$' | head -15
  printf '%s\n' "$FMT" | grep -i 'would reformat' | head -5
  echo "→ Fix before committing:  make fix-lint   (or  ruff format \"$FILE\" && ruff check \"$FILE\" --fix )"
  echo "→ Remember: mypy --strict must also pass (run: make lint)."
  exit 1
fi

echo "✓ QA-POSTCHECK: ruff clean on $FILE (still run 'make lint' for mypy before commit)."
exit 0
