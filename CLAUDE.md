
# CLAUDE.md — Forecast Assistant (recipe-forecastic-reactic)

Read at the start of every session. Keep this short — if a rule here is being ignored,
the file has grown too long.

## What this is
A DataRobot **recipe template**: trains a time-series forecast (DataRobot AutoTS), serves
predictions, optionally generates LLM explanations, and ships a UI. Deployed to the DataRobot
platform via Pulumi. Python `>=3.9` (ruff & mypy target **3.11**).

## Layout
| Path | Purpose |
|------|---------|
| `forecastic/` | App logic: `schema.py` (Pydantic config + API models), `api.py` (predictions, LLM summary, plotly), `rest_api.py` (FastAPI for React), `credentials.py`, `resources.py` (Pulumi outputs), `i18n.py` |
| `frontend/` | Streamlit UI (`app.py`) — the primary, deployed frontend |
| `frontend_react/` | React/TS frontend — early scaffold (active on `jm/react-app`) |
| `infra/` | Pulumi IaC: `__main__.py` + `settings_*.py` (one concern each) |
| `notebooks/` | Papermill-run training/scoring notebooks → `*_output.{stack}.yaml` |
| `utils/`, `core/`, `tests/`, `assets/` | helpers · shared Taskfile vars · pytest suite · sample CSVs |

## Commands
```bash
make lint            # ruff format --check . && ruff check . && mypy --pretty . (strict) — gate before commit
make fix-lint        # auto-fix ruff (mypy errors fixed by hand)
make apply-copyright # add Apache header to new .py files (CI requires it)
pytest               # unit tests (tests/e2e excluded by default)
task dev             # run Streamlit on :8501
task infra:up        # pulumi up  → DEPLOYS to DataRobot (see rule 1)
```

## Rules (the few that matter)
1. **NEVER run `pulumi` / `task infra:*` / `task deploy` / `quickstart.py` without my approval** — they create/destroy real DataRobot resources and cost money. Verify with unit tests + local `task dev` instead.
2. **NEVER commit `.env` or secrets** (gitignored, live credentials). Secrets go via `.env` or DataRobot runtime params only.
3. **Don't hand-edit generated files**: `*_output.*.yaml`, `output/`, `deployment_*/`, Pulumi state, notebook cell outputs. Change the source and regenerate.
4. `make lint` must pass before commit; new `.py` files need the Apache header.
5. PRs open as **`--draft`**; never merge. Update `CHANGELOG.md` for user-facing changes.
6. For anything touching more than a couple of files or unfamiliar code: **explore + plan first, then implement** (use plan mode). Small obvious fixes — just do them.

## Conventions
- **Python:** `mypy --strict`; `from __future__ import annotations`; absolute imports; Pydantic
  for structured config/IO; no hardcoded deployment/model IDs (read from `resources.py`/env).
  ruff = 88 cols, double quotes.
- **Git:** branch `<initials>/<summary>`; commit `[APP-XXXX] summary (#PR)` (`[-]` if no ticket);
  reviewers `@datarobot/customer-engineering @datarobot/applications`; run `gh pr create` from repo root.
- **Verify:** show evidence (test/lint output), don't just assert success.

## Testing
Unit tests in `tests/` — **mock the DataRobot/`openai` boundary** (see `tests/test_datarobot_api_calls.py`).
`pytest --pulumi_up` runs integration against real resources → approval-gated. `tests/e2e` excluded by default.

## Workflow (non-trivial features) — spec-driven
Run **`/spec-dev <feature>`**: interview → `SPEC.md` → `planner` subagent writes `PLAN.md` →
implement task-by-task (TDD) → `reviewer` subagent on each diff → gate on lint+test+build →
loop. `SPEC.md`/`PLAN.md` are the durable state (survive `/clear`). Quick fixes: skip the loop.
Auto-discovered from `.claude/`: skills `/spec-dev`, `/interrogate-me`, `/lint`; subagents
`planner`, `reviewer` (both run in fresh context — they plan/judge, they don't grade their own work).

## Gotchas (append a line whenever something bites you — this is your lightweight memory)
- _(none yet)_
