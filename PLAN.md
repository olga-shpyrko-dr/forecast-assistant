# PLAN — React frontend (FastAPI-served) + FRONTEND_TYPE toggle (from SPEC.md)

> Revised for the confirmed architecture: FastAPI serves `forecastic/build` + `_dr_env.js`;
> dev ports 8080 (backend) / 8081 (vite); config authored from the user's snippets;
> the reference `deploy/` (Node server.js) is NOT imported.

## Task 1 — Import React source from experimental/react
- Do: `git checkout experimental/react -- frontend_react/react_src` (source only; do NOT import `frontend_react/deploy`). Don't touch `frontend/` or `infra/`.
- Verify: `git status` shows additions only under `frontend_react/react_src/`; `git diff --stat -- frontend/` empty; `git ls-files --error-unmatch frontend_react/react_src/package.json` ok.
- AC: "react_src present; frontend/ untouched." · Approval: no. · [ ] done

## Task 2 — Gitignore the build output
- Do: add `forecastic/build/` to `.gitignore`. (Build will be generated into `forecastic/build/`.)
- Verify: `git check-ignore forecastic/build/index.html` prints the path.
- AC: "forecastic/build/ gitignored." · Approval: no. · [ ] done

## Task 3 — Apply target vite.config.ts + apiClient.ts (from user snippets)
- Files: `frontend_react/react_src/vite.config.ts`, `frontend_react/react_src/src/api/apiClient.ts`.
- Do:
  - vite.config.ts per the user's snippet: `outDir: '../../forecastic/build/'`, `emptyOutDir`, `rollupOptions.external: ['_dr_env.js']`, aliases `@`/`~` → ./src, `base` from NOTEBOOK_ID/codespace, `strip-base` serve middleware, `server.port: 8081`, `host: true`, `allowedHosts`, proxy `/api/` and `/_dr_env.js` → `http://localhost:8080`. **Define the missing vars** `VITE_DEFAULT_PORT`/`VITE_STATIC_DEFAULT_PORT` (e.g. `8081`/`8080`) explicitly.
  - apiClient.ts per the snippet: dev `apiClient` baseURL `http://localhost:8080`; prod = origin+path first 5 segs; `withCredentials: true`; add `drClient` (`window.ENV?.DATAROBOT_ENDPOINT || {origin}/api/v2`); export both (keep default export for existing imports).
  - Reconcile existing `src/api/*.ts` imports against the new exports (default import must still resolve).
- Verify: `yarn lint` + `yarn prettier` clean after edits (run in Task 4).
- AC: "vite builds to ../../forecastic/build; apiClient exposes apiClient+drClient." · Approval: no. · [ ] done

## Task 4 — Install + build/lint/prettier green (early gate)
- Do: `cd frontend_react/react_src && yarn install`; `yarn build` (→ `../../forecastic/build`); `yarn lint`; `yarn prettier`. Fix only what's needed (prefer `lint:fix`/`prettier:fix`).
- Verify (gate — no test runner): all exit 0; `forecastic/build/index.html` exists.
- AC: "yarn install/build/lint/prettier pass; build → forecastic/build." · Approval: no. · [ ] done

## Task 5 — Backend: serve the built SPA from FastAPI (mount forecastic/build)
- Files: `forecastic/rest_api.py` (Apache header already present). No new module — keep it here.
- Do:
  - Mount `StaticFiles(directory=Path(__file__).parent / "build", html=True)` on the FastAPI `app`
    at root `"/"`, registered **after** the 7 API routes so `/appSettings`, `/runtimeAttributes`,
    `/filters`, `/scoringData`, `/predictions`, `/llmSummary`, `/share` still resolve. `html=True`
    gives the SPA `index.html` fallback for client routes (`/explanations`, `/what-if`).
  - Resolve the build dir relative to this file (not CWD). Guard when `build/` is absent: the app
    must still import and the API routes still work (log a clear "run `yarn build`" message) so
    dev/tests don't crash before a build exists.
  - Add `CORSMiddleware` allowing the Vite dev origin `http://localhost:8081` (dev only) — the
    shared `apiClient.ts` calls `http://localhost:8080` cross-origin, and `withCredentials: true`,
    so set `allow_credentials=True` with an explicit origin (not `"*"`).
  - **No `_dr_env.js`** (dropped per decision). `drClient` falls back to `${origin}/api/v2`; the
    `_dr_env.js` proxy/external lines in the shared vite config stay inert (harmless).
- Verify: `make lint` (ruff + mypy strict) clean; `pytest` green; `uvicorn forecastic.rest_api:app
  --port 8080` starts; `curl :8080/appSettings` works; after `yarn build`, `curl :8080/` serves
  index.html; with no `build/`, import + API routes still work.
- AC: "rest_api.py serves the SPA from `forecastic/build` (html fallback); 7 routes still work;
  dev CORS for :8081; make lint + pytest green." · Approval: no (local). · [ ] done

## Task 6 — Local end-to-end vs mockups
- Do: backend `uvicorn …:app --port 8080` + `yarn dev` (:8081); open Explanations + What-If; confirm the 7 endpoints populate; compare to the 4 mockups.
- Verify: both pages render + match mockups; no proxy/console errors; data shows.
- AC: "pages render, match mockups, endpoints populate." · Approval: **YES** (needs `.env` creds + a deployment/stack; else verify on sample data and flag). · [ ] done

## Task 7 — Add FRONTEND_TYPE to .env.template
- Do: add (before LLM blocks): `# Selects which frontend Pulumi deploys: react | streamlit (default: streamlit)\nFRONTEND_TYPE=react`. Keep `.env.template` tracked; never commit `.env`.
- Verify: `grep -n FRONTEND_TYPE .env.template`; no `.env` staged.
- AC: ".env.template has FRONTEND_TYPE=react documented." · Approval: no. · [ ] done

## Task 8 — FRONTEND_TYPE core setting + conditional infra deploy
- Files: `infra/settings_main.py` (add setting), `infra/settings_app_infra.py` (conditional).
- Do:
  - `settings_main.py`: `import os`; `FRONTEND_TYPE = os.environ.get("FRONTEND_TYPE", "streamlit").lower()`; validate ∈ {react, streamlit} else raise.
  - `settings_app_infra.py`: import `FRONTEND_TYPE`. For `react`: `application_path = PROJECT_ROOT/"forecastic"` model — i.e. deploy the FastAPI app that serves `forecastic/build` (bundle `forecastic/**` incl. `build/` + `rest_api.py`, run uvicorn), Python base env (no Node). For `streamlit`: keep current `frontend/` path + base env **unchanged**. Make `get_app_files`/metadata/base-env conditional. Do NOT use the reference's absent `infra.common.*`/`app_settings_path`.
  - Confirm the start command for the React app (uvicorn serving SPA) via metadata.yaml.jinja or app source config.
- Verify: `make lint` clean; import assertion — unset → streamlit path; `FRONTEND_TYPE=react` → react path; `pytest` green.
- AC: "infra reads FRONTEND_TYPE (default streamlit); selects React vs Streamlit; Streamlit unchanged." · Approval: no (import+lint+pytest). · [ ] done

## Task 9 — Deploy-toggle verification + draft PR
- Do: default = import-level assertion (Task 8). Optional `pulumi preview` per FRONTEND_TYPE (**approval-gated**). Then open a **draft** PR, update `CHANGELOG.md`, reviewers `@datarobot/customer-engineering @datarobot/applications`.
- Verify: assertion passes both values; Streamlit deploy byte-for-byte unchanged when streamlit/unset.
- AC: deploy-toggle verification; draft PR. · Approval: **YES** for `pulumi`/PR push. · [ ] done

## Risks / open questions
- **Endpoint/CORS reconciliation** (open Q1 in SPEC) — apiClient dev hits `:8080` bare while proxy covers `/api/`; resolve in Task 5/6 (dev CORS vs prefix).
- **`_dr_env.js` keys** — derive from `react_src` usage (Task 5), don't guess.
- **React deploy via FastAPI** — Task 8 changes the deploy bundle vs the reference's Node model; confirm uvicorn-serves-SPA is the intended runtime (it is, per confirmed architecture).
- **Reference infra not portable** — Task 8 re-implements with this branch's conventions.
- **Backend data needs creds** (Task 6) — partial verify on sample data if unavailable.
- **Share/Export** — verify opportunistically; not a hard AC.
