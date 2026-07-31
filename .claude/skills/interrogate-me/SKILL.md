---
name: interrogate-me
description: Pre-planning interview for a non-trivial feature. Claude interviews the user to surface hidden requirements, constraints, and acceptance criteria, then writes a short SPEC before any code. Offer this before planning anything that touches more than a couple of files.
---

# /interrogate-me

Front-load alignment so you don't build the wrong thing. Ask in small batches, wait for
answers, and skip any question the codebase already answers (say what you found instead).

## 1. Outcome
- What user-visible outcome should exist that doesn't today? Which Jira ticket (`APP-XXXX`)?
- "Done" in one sentence.

## 2. Scope
- Which layer: Streamlit (`frontend/`), React (`frontend_react/`), backend (`forecastic/`),
  infra (`infra/`)? What's explicitly out of scope?
- Does it change prediction logic, the LLM summary, data prep, or just UI?

## 3. Deploy & data impact (decides whether approval gates fire)
- Needs a new deployment / retrain / runtime parameter, or works against existing deployments?
- Can it be verified with unit tests + local `task dev`, or does it truly need `pulumi up`
  (real resources, cost, approval-gated)?

## 4. Contract
- New/changed Pydantic models in `forecastic/schema.py`? New FastAPI endpoint in `rest_api.py`?
- Any change to what the frontend expects from the backend?

## 5. Acceptance & tests
- Concrete acceptance criteria as a checklist.
- Test plan: unit only, or integration (`--pulumi_up`, approval-gated)?

## 6. Risks
- What's most likely wrong about our current understanding? Anything blocked on someone else?

## Output
Write a short, self-contained **SPEC** (in chat, or `SPEC.md` if the user wants it) that names
the files/interfaces involved, states what's out of scope, and ends with an end-to-end
verification step. Then enter plan mode. Do not write code yet.
