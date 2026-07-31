---
name: spec-dev
description: Run the spec-driven development loop for a feature — interview to a SPEC, plan it in a fresh-context subagent, implement task-by-task with TDD, review each diff in a fresh-context subagent, and gate on tests/lint/build. Invoke with the feature name, e.g. /spec-dev react redesign.
disable-model-invocation: true
---

# /spec-dev — spec-driven development loop

Drive feature `$ARGUMENTS` through these phases. Do not skip the gates. Keep `SPEC.md` and
`PLAN.md` as the durable state so progress survives `/clear` and compaction.

## Phase 0 — SPEC
Run `/interrogate-me` for this feature. Produce **`SPEC.md`**: goal, in/out of scope, files &
interfaces involved, the design source (Figma/mock link or screenshots), acceptance criteria
as a checklist, and an end-to-end verification step. **Stop for human approval.**

## Phase 1 — PLAN
Dispatch the **`planner`** subagent (fresh context) with `SPEC.md`. It writes **`PLAN.md`**:
ordered atomic tasks, each with files + a verify command + which AC it covers. Ensure the
**first task establishes a verification gate** if the area has none yet (e.g. `frontend_react/`
has no `package.json`/test runner — task 1 sets up Vite + TS + ESLint + Vitest). **Stop for
human approval of the plan.**

## Phase 2 — IMPLEMENT (one task at a time)
For the next unchecked task in `PLAN.md`:
- Write the test first where practical (TDD). For React use Vitest/RTL; for Python use pytest
  (mock the DataRobot/`openai` boundary).
- Implement only that task's files. The PreToolUse/PostToolUse hooks guard secrets/generated
  files and lint Python edits automatically.

## Phase 3 — REVIEW
Dispatch the **`reviewer`** subagent on the diff. It returns `VERDICT: pass | needs-rework`
with blocking items. `needs-rework` → fix and re-review. Do not proceed on a fail.

## Phase 4 — VERIFY (the gate — show evidence, don't assert)
Run the task's verify step and the area gate:
- Python: `make lint` + `pytest`
- React: `npm run lint` + `npm test` + `npm run build` (+ a screenshot compared to the design for UI tasks)
Never run `pulumi`/`task infra:*`/deploy to verify without explicit approval.

## Phase 5 — LOOP
On green: check the task off in `PLAN.md`, commit (`[APP-XXXX] <task>`), move to the next task.
When all tasks are checked: open a **draft** PR, update `CHANGELOG.md`, tag
`@datarobot/customer-engineering @datarobot/applications`. Never merge.

## Ratchet
If something broke that a rule could have prevented, add one line to CLAUDE.md "Gotchas".

## Context hygiene
Between tasks, if context is heavy, `/clear` and resume from `SPEC.md` + `PLAN.md` (that's why
they're files). Use subagents for any wide codebase exploration so it doesn't fill main context.
