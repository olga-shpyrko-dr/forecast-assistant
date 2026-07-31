# SPEC — React frontend (FastAPI-served) + FRONTEND_TYPE deploy toggle

**Feature:** Bring the React UI from branch `experimental/react` into `jm/react-app`, serve it
**from FastAPI** (built SPA mounted as static files), wire it to the
existing endpoints to match the mockups, and add a **`FRONTEND_TYPE`** config (`react` |
`streamlit`) selecting which frontend Pulumi deploys. Do **not** modify the Streamlit app code
(`frontend/`). **No new app features** beyond the reference React app / base app.

## Confirmed architecture (supersedes earlier "Vite-proxy-only / don't touch backend")
- **Build output:** `frontend_react/react_src` → `../../forecastic/build/` (Vite `outDir`).
- **Serving:** `forecastic/rest_api.py` (FastAPI) **mounts the built SPA (`forecastic/build`) as
  static files** (`html=True` SPA fallback) after the API routes. **No `_dr_env.js`** (dropped).
  **Backend is modified** (static mount + dev CORS).
- **Dev:** backend `uvicorn …:app` on **:8080**; Vite dev server on **:8081** (proxy/base per the
  shared config; its `_dr_env.js` proxy line stays inert). `apiClient` calls `:8080` cross-origin
  → dev CORS allows `http://localhost:8081`.
- **Clients (`apiClient.ts`):** `apiClient` → app's FastAPI endpoints; `drClient` →
  `window.ENV.DATAROBOT_ENDPOINT || {origin}/api/v2` (DataRobot API). `withCredentials: true`.
- **Deploy:** Pulumi bundles `forecastic/` (incl. `build/` + `rest_api.py`) and runs uvicorn —
  **no Node `server.js`** (the reference's `deploy/` is NOT imported).
- Config is authored from the user's provided `vite.config.ts` / `apiClient.ts` snippets
  (missing vars like `VITE_DEFAULT_PORT` defined during implementation).

## What exists (verified)
- React app (`experimental/react:frontend_react/react_src/`): Vite + React + TS + Tailwind +
  shadcn/ui + visx + axios + react-router-dom; pages, Sidebar, charts, modals, `state/AppState.tsx`,
  api clients in `src/api/`. Scripts: `dev`, `build`, `lint` (eslint, 0 warnings), `prettier`. No test runner.
- Backend `forecastic/rest_api.py` (this branch): bare FastAPI, routes `/appSettings`,
  `/runtimeAttributes`, `/filters`, `/scoringData`, `/predictions`, `/llmSummary`, **`/share` (PATCH)**.
  No static mount, no CORS — to be added (no `_dr_env.js`).
- Current infra: `application_path = PROJECT_ROOT / "frontend"` (Streamlit). The reference
  `infra/settings_app_infra.py` is NOT portable (imports `infra.common.*`, `app_settings_path`).
- Local toolchain: node v25.8.2, yarn 1.22.22.

## In scope
1. Import `frontend_react/react_src/` from `experimental/react` (source only; **not** `deploy/`).
2. Apply the target `vite.config.ts` (outDir `../../forecastic/build`, ports/proxy/base) and
   `apiClient.ts` (`apiClient` + `drClient`, `withCredentials`) from the user's snippets.
3. Modify `forecastic/rest_api.py` to mount `forecastic/build/` (StaticFiles, html fallback)
   after the API routes + add dev CORS for `:8081`. **No `_dr_env.js`.** (Integration glue, not a new feature.)
4. `yarn install/build/lint/prettier` green; local end-to-end (backend :8080 + vite :8081) with
   both pages rendering against the endpoints and matching the mockups.
5. `FRONTEND_TYPE` core config: add `FRONTEND_TYPE=react` to `.env.template`; read in infra
   (default `streamlit`); `infra/settings_app_infra.py` deploys the React FastAPI app when
   `react` (bundle `forecastic/` incl. `build/`, run uvicorn) and Streamlit `frontend/` when
   `streamlit` (unchanged).
6. Gitignore `forecastic/build/`.

## Out of scope (this cycle)
- Streamlit app code (`frontend/`) — untouched.
- New features/endpoints beyond the reference React app / base app.
- Node `server.js` deploy model (obsolete under FastAPI serving).
- Running `pulumi up` (approval-gated). A new test framework (reference has none).

## Acceptance criteria
- [ ] `frontend_react/react_src/` present from `experimental/react`; `frontend/` untouched.
- [ ] `vite.config.ts` builds to `../../forecastic/build`; `apiClient.ts` exposes `apiClient`+`drClient`.
- [ ] `rest_api.py` mounts the SPA (`forecastic/build`, html fallback) at the app root with dev
      CORS for `:8081`; the 7 existing routes still work; `make lint` + `pytest` green.
- [ ] `yarn install && yarn build` (→ `forecastic/build`) + `yarn lint` + `yarn prettier` pass.
- [ ] Dev (backend :8080 + vite :8081): Explanations & What-If pages render, match the mockups,
      endpoints populate charts/tables, no proxy errors.
- [ ] `.env.template` has `FRONTEND_TYPE=react` (documented react/streamlit); infra reads it
      (default `streamlit`) and selects React vs Streamlit deploy; Streamlit path unchanged.
- [ ] `forecastic/build/` gitignored; no build artifacts or `.env`/secrets committed.

## Open questions (track during implementation)
1. **Endpoint prefix / CORS:** `apiClient` dev baseURL is `http://localhost:8080` (bare paths) while
   the proxy covers `/api/` — reconcile (either add dev CORS on the backend, or route app
   endpoints under a proxied prefix). Resolve when wiring Task "dev integration".
2. **`drClient` in dev:** with no `_dr_env.js`, `window.ENV` is undefined so `drClient` falls back
   to `${origin}/api/v2`. Confirm whether any `drClient` (DataRobot API) calls are exercised in dev,
   and how they authenticate, when wiring dev integration.
3. **Share/Export:** `/share` (PATCH) exists + React client present → verify opportunistically; not a hard AC.
4. **React base env / runtime:** with FastAPI serving a prebuilt SPA, the React deploy can use a
   Python base env (no Node at runtime). Confirm base env in the toggle task.
5. **Backend data:** real `/scoringData`,`/predictions`,`/llmSummary` need creds + a deployment/stack.

## Verification (end-to-end)
Local: `uvicorn forecastic.rest_api:app --port 8080` + `yarn dev` (:8081) → both pages match
mockups, endpoints return data; `yarn build && yarn lint && yarn prettier` green; `make lint` +
`pytest` green for backend. Toggle: `FRONTEND_TYPE=react` → infra deploys the FastAPI+SPA app;
`streamlit` → `frontend/` unchanged (verified by import assertion / `pulumi preview`; real
`pulumi up` only with explicit approval).
