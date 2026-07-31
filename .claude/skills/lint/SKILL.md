---
name: lint
description: Lint and type-check the repo (or changed files) using the project's exact tooling — ruff format, ruff check, and mypy strict. Run before every commit and before pushing.
---

# /lint

The pre-commit / pre-push quality gate. Mirrors CI (`.github/workflows/python-static-checks.yml`).

## Full check (what CI runs)
```bash
make lint
# = ruff format --check .   (formatting)
#   ruff check .            (lint: E4,E7,E9,F,I)
#   mypy --pretty .         (strict typing, pydantic plugin)
```

## Auto-fix what's fixable, then re-verify
```bash
make fix-lint
# = ruff format . ; ruff check . --fix ; mypy --pretty .
```
`ruff` auto-fixes formatting + import order + simple lint. **mypy errors are not auto-fixed** —
resolve type issues by hand (add annotations, narrow types, fix real bugs).

## Scope to changed files (faster during iteration)
```bash
CHANGED=$(git diff --name-only --diff-filter=ACM HEAD | grep '\.py$' || true)
[ -n "$CHANGED" ] && ruff format --check $CHANGED && ruff check $CHANGED && mypy --pretty $CHANGED
```

## Before committing new files
```bash
make apply-copyright   # adds the Apache license header CI requires on new .py files
```

## Rules
- Do not push with outstanding lint or mypy failures.
- Do not silence mypy with blanket `# type: ignore` — use `# type: ignore[code]` only with a
  reason, and prefer fixing the root cause (`enable_error_code = "ignore-without-code"` is on).
- ruff target is py311, line length 88, double quotes — don't fight the formatter.
