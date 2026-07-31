#!/usr/bin/env bash
# PreToolUse hook for Write|Edit. Guards against editing dangerous/generated files.
# onFailure is "warn" in settings.json, so a non-zero exit surfaces a warning but does
# not hard-block. Keep this defensive and fast.
set -uo pipefail

# The tool input JSON arrives on stdin. Extract the target file path robustly.
INPUT="$(cat 2>/dev/null || true)"
FILE=""
if command -v jq >/dev/null 2>&1; then
  FILE="$(printf '%s' "$INPUT" | jq -r '.tool_input.file_path // .tool_input.path // empty' 2>/dev/null)"
fi
if [ -z "$FILE" ]; then
  FILE="$(printf '%s' "$INPUT" | grep -oE '"file_path"[[:space:]]*:[[:space:]]*"[^"]+"' | head -1 | sed -E 's/.*"file_path"[[:space:]]*:[[:space:]]*"([^"]+)".*/\1/')"
fi

[ -z "$FILE" ] && exit 0

base="$(basename "$FILE")"

warn() { echo "⚠ QA-PRECHECK: $1"; }

# 1. Secrets / credentials
case "$base" in
  .env|.env.*|*credentials*.json|*token.json|client_secret*.json)
    warn "You are about to edit '$FILE' which may contain secrets. Confirm with the user; never commit credentials."
    exit 1
    ;;
esac

# 2. Generated artifacts — should be produced by notebooks/Pulumi, not hand-edited
case "$FILE" in
  *_output.*.yaml|*/output/*|*/deployment_*/*|*.tfstate|*Pulumi.*.yaml)
    warn "'$FILE' looks generated (notebook/Pulumi output). Hand-editing it will be overwritten. Confirm this is intentional."
    exit 1
    ;;
esac

# 3. Notebook cell outputs
case "$base" in
  *.ipynb)
    warn "Editing a notebook ('$base') directly is error-prone (cell outputs, metadata). Prefer editing the source and re-running via papermill."
    exit 1
    ;;
esac

# 4. High-blast-radius infra/config files
case "$base" in
  Pulumi.yaml|requirements.txt|pyproject.toml)
    warn "'$base' is high blast-radius (deps/infra config). Make sure this change was planned and approved."
    ;;
esac

exit 0
