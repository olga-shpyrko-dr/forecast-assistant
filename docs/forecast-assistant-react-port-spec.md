# Forecast Assistant — React Port Spec

**Source of truth (Streamlit, fully implemented):** `olga-shpyrko-dr/forecast-assistant` @ `feat/flexible-ts-spec-and-enhancements-streamlit-only`
**Target (React, partially started):** `datarobot/recipe-forecastic-reactic` @ `jm/react-app`
**Date:** 2026-05-28

---

## 1. Scope Recap

The original spec covered 6 changes. All 6 are now fully implemented and shipped on the Streamlit branch, which also picked up several enhancements beyond the original scope along the way. The React branch currently implements a **variant of Change 5 only**, and needs testing. This document specs out porting everything else, plus flags the one architectural decision (charting) that changes how some fixes should be implemented in React.

| # | Change | Streamlit status | React status |
|---|---|---|---|
| 1 | Single-series TS support | ✅ Done | ❌ Not started |
| 2 | TS without calendar | ✅ Done | ❌ Not started |
| 3 | Historic chart `target (actual)` fix | ✅ Done | ❌ Not started (different root cause in React — see §4.3) |
| 4 | Predictions without intervals | ✅ Done | ❌ Not started |
| 5 | Registry dataset as scoring source | ✅ Done (env-var infra bypass + in-app dataset-version replay) | 🟡 Partial — in-app dataset picker + upload built, **not yet tested** |
| 6 | XEMP strength in PE chart | ✅ Done (stacked bar, signed, per-series) | ❌ Not started |
| — | Per-series chart selector | ✅ Enhancement | ❌ Not started |
| — | Direct Azure OpenAI LLM (bypass GenAI exec env) | ✅ Enhancement | ❌ Not started |
| — | LLM commentary on/off toggle | ✅ Enhancement | ❌ Not started |
| — | Brand redesign (dark theme, DM Sans/Fragment Mono) | ✅ Enhancement | 🟡 Different design system already in place (Tailwind + shadcn/ui) |

---

## 2. Architecture Comparison

Both apps share the same Python backend package (`forecastic/`), which is good — `schema.py` and `api.py` are the natural place to port the backend-side logic once, and both frontends consume it.

| | Streamlit | React |
|---|---|---|
| Backend | Calls `forecastic.api` functions directly in-process | FastAPI wrapper (`forecastic/rest_api.py`) exposes the same `forecastic.api` functions as REST endpoints |
| Charting | Server builds a Plotly figure (`get_forecast_as_plotly_json`), frontend just renders the JSON | Client builds its own charts from raw JSON using `visx`/`d3` (`ExplanationsLineChart.tsx`, `XEMPLineChart.tsx`) |
| Filtering | Server-side (`get_scoring_data(filter_selection=...)`) | **Client-side** — `useScoringData.tsx` fetches all rows unfiltered and filters in JS |
| State | `st.session_state` | React Context + `useReducer` (`state/AppState.tsx`) |
| Pages | Single page | Two routes: `/` (Explanations) and `/what-if-scenarios` (What-If Scenarios — not in original Streamlit scope) |

**Important implication:** because React does its own client-side charting and filtering, several of the Python-side fixes (Changes 1, 3, 4) need a **corresponding client-side fix**, not just a backend schema change. The backend fix is still necessary (it prevents `AppSettings` validation errors and `KeyError`s in `forecastic/api.py`), but the React components that consume `appSettings`/`scoringData`/`forecastDataResponse` also assume multiseries + intervals + a bare target column, and will break independently of the backend fix.

---

## 3. Backend Changes (shared `forecastic/` package)

These are the exact changes already validated on the Streamlit branch. Port them into `forecastic/schema.py` and `forecastic/api.py` on the React branch — this part is a near-direct copy, since both apps import the same package.

### 3.1 — Schema (`forecastic/schema.py`)
- `multiseries_id_column: str` → `Optional[str] = None`
- `calendar_id: str` → `Optional[str] = None`
- `prediction_interval: Optional[int] = None`
- `PredictionRow.low` / `.high` → `Optional[float] = None`
- `timestep_settings: dict[str, Any] = Field(default_factory=dict)` (avoid `KeyError` when `detectedMultiseriesIdColumns` is empty for single-series)
- `from_registered_model_version()`: guard the `multiseries_id_columns` check to set `None`/`[]` instead of raising; guard `get_timestamp_settings()` to return `{}` when `detectedMultiseriesIdColumns` is empty

### 3.2 — API (`forecastic/api.py`)
- `_process_predictions()`: conditional `series_id` inclusion, conditional interval column selection (`has_intervals` flag), synthesize `low`/`high` as `None` when absent
- `_format_predictions()`: conditional `seriesId` field, conditional `predictionIntervals` (set `None` when disabled)
- Historic chart column fix — **do this at the data layer, not just in the Plotly function**, since React needs the same corrected column too. Recommended: normalize in `_format_predictions()` / a new `get_scoring_data()` step so the API always returns a plain `target` field regardless of whether the underlying batch data used `"{target} (actual)"`. This means both `forecastic/api.py`'s Plotly path *and* the REST `/scoringData` response benefit from one fix instead of two.
- `get_pred_ex_df()` / new `get_pred_ex_stacked_bar_df()`: port as-is — used for Change 6

**Testing note:** these backend changes are already covered by real deployments on the Streamlit branch (single-series, no-calendar, no-interval projects). Re-run the same manual test matrix against the FastAPI endpoints once ported (see §6).

---

## 4. Frontend Changes (React)

### 4.1 — Change 1: Single-Series Support

**Files:** `state/AppState.tsx`, `components/Sidebar.tsx`, `api/calculatePredictions.ts`, `pages/Explanations.tsx`

- `ForecastData.seriesId: string` → `seriesId?: string`
- `calculatePredictions.ts`: `seriesIds.add(d.seriesId)` — guard for `undefined`; `forecastSeriesIdsCount` should be `1` (or omitted) when no series column exists
- `Sidebar.tsx` `ForecastSettings`: `filterOptions` will be `[]` when `filterable_categories` is empty (single-series) — this already degrades gracefully since it's a `.map()` over an empty array, but verify the "Update forecast" button and dataset selector still render sensibly with zero filters
- `Explanations.tsx` / `ExplanationsLineChart.tsx`: remove any implicit assumption that grouping/filtering by series is always applicable — check `groupedScoringData` logic doesn't need a `seriesId` key to group correctly (currently groups by `dateColumn` only, so this should already work)

### 4.2 — Change 2: No Calendar

No frontend changes anticipated — `calendar_id` isn't referenced anywhere in `frontend_react/react_src/src` (confirmed via grep). This is purely a backend schema fix (§3.1). Add to regression checklist only.

### 4.3 — Change 3: Historic Chart Column Fix

**Root cause differs from Streamlit.** In Streamlit the bug was in one Python function (`get_forecast_as_plotly_json`). In React, the equivalent logic lives client-side in `ExplanationsLineChart.tsx`:

```tsx
const getYValue = useCallback((d: ScoringData) => d[targetColumn], [targetColumn]);
```

If the recommended backend normalization (§3.2) is done — i.e., the `/scoringData` endpoint always returns a plain `target` key — **no React change is needed here**. This is the preferred fix: one place, not two.

If the team prefers *not* to touch the REST response shape, the fallback is a client-side fix in `ExplanationsLineChart.tsx` and `useScoringData.tsx`:
```tsx
const actualCol = targetColumn + " (actual)" in scoringData[0] ? `${targetColumn} (actual)` : targetColumn;
```
This is more fragile (duplicated in two frontends) — recommend the backend normalization instead.

### 4.4 — Change 4: Predictions Without Intervals

**Files:** `state/AppState.tsx`, `components/ExplanationsLineChart.tsx`, `components/Sidebar.tsx` (confidence interval toggle)

- `ForecastData.predictionIntervals` is typed as always having an `"80"` key:
  ```ts
  predictionIntervals: { "80": { low: number; high: number } };
  ```
  Change to `predictionIntervals: Record<string, { low: number; high: number }> | null`
- `getForecastYValueLow` / `getForecastYValueHigh` in `ExplanationsLineChart.tsx` hardcode `d.predictionIntervals["80"]` — guard for `null`/missing key, and don't render the confidence band/toggle when unavailable
- `confidenceIntervalEnabled` toggle in the UI (check `Sidebar.tsx` or wherever `toggleConfidenceInterval` is surfaced) should be disabled/hidden when the active model has no intervals — mirror the Streamlit `is_llm_commentary_available()`-style capability flag pattern; e.g. add `intervalsAvailable` derived from `appSettings.prediction_interval !== null`

### 4.5 — Change 5: Registry Dataset (Testing + Gap-Closing)

**Already built:** `DatasetSelectorModal.tsx`, `hooks/useRegistryDatasets.ts`, `api/getRegistryDatasets.ts`, `api/uploadDataset.ts`, backend `list_registry_datasets`/`upload_dataset_to_registry`/`_to_registry_dataset` in `forecastic/api.py`, `/registryDatasets` and `/datasets/upload` REST endpoints.

**Gap vs. the Streamlit implementation:** the Streamlit branch also added an **infra-level bypass** — `TRAINING_DATASET_ID` / `FORECAST_SCORING_DATASET_ID` env vars in `.env.template` that let `infra/__main__.py` skip the local-CSV-upload notebook path entirely at deploy time. This env-var bypass is **not present** in the React branch's `infra/__main__.py` or `.env.template`. Decide whether to port it — it's orthogonal to the in-app dataset picker (deploy-time default vs. runtime override) and both are useful together.

**Testing checklist for the existing in-app implementation:**
- [ ] `list_registry_datasets()`: confirm `Dataset.iterate(use_cases=[...])` returns datasets scoped to the app's use case only (not the full org catalog)
- [ ] Confirm `REGISTRY_DATASET_SIZE_CUTOFF` is defined and imported (not visible in the reviewed excerpt — verify it exists in `forecastic/api.py` and matches a sane limit given the whole dataset is downloaded into memory)
- [ ] Upload path: verify `Dataset.create_from_file` + `.modify(name=filename)` round-trip preserves the original filename in the UI dropdown
- [ ] `DatasetSelectorModal.tsx`: confirm switching datasets correctly resets filters, forecast data, and What-If scenarios (`SET_ACTIVE_DATASET` reducer case does this — confirm no stale `forecastData` renders during the loading gap)
- [ ] Verify `_get_scoring_data`'s `lru_cache(maxsize=4)` doesn't serve stale data after a re-upload of a dataset with the same filename (cache key is `active_dataset_id`, which will differ per upload since `create_from_file` mints a new dataset ID — confirm this holds)
- [ ] File type validation: `accept=".csv,.xlsx,.xls,.parquet"` in the `<input>` is a client hint only — confirm `upload_dataset_to_registry` / `Dataset.create_from_file` server-side reject unsupported types gracefully rather than 500ing
- [ ] Confirm behavior when `filterable_categories` is non-empty but the uploaded/selected dataset doesn't contain those columns (currently `Sidebar.tsx` hides filters entirely via `isCustomDatasetActive` — confirm this is the desired UX, or whether filters should still apply if the columns happen to exist)

### 4.6 — Change 6: XEMP Strength in PE Chart

**Files:** new — no stacked-bar PE chart currently exists in React (only `XEMPLineChart.tsx` / `XEMPModalLineChart.tsx`, which need review against what they currently plot)

- Verify what `XEMPLineChart.tsx` / `XEMPModalLineChart.tsx` currently render — from the `predictionExplanations` shape (`featureValue`, `strength`, `qualitativeStrength` all present in `PredictionExplanation` type), confirm whether `strength` (XEMP, additive-by-feature-but-not-globally-additive) or `featureValue` (raw value) currently drives the chart axis. Port the Streamlit fix if `featureValue` is used.
- If building fresh: mirror the Streamlit `get_pred_ex_stacked_bar_df()` grouping (per-timestep, per-feature, signed sum of `EXPLANATION_i_STRENGTH`) and render as a signed stacked bar (equivalent of `barmode="relative"` — in `visx`, this means separately stacking positive and negative values per category, since `visx`'s `BarStack` doesn't have a built-in signed/relative mode like Plotly)

> **⚠️ Known limitation — XEMP is not additive** (carried over from the original spec). XEMP strengths do not sum to the model's prediction; the chart is a directional/relative-importance visualization, not a mathematical decomposition. Migrate to SHAP once SHAP prediction explanations are fully supported for Time Series deployments without current platform limitations.

### 4.7 — Enhancements to Port (Streamlit → React)

These aren't in the original 6-change spec but shipped on the Streamlit branch and should be considered for parity:

| Enhancement | Streamlit location | React porting notes |
|---|---|---|
| Per-series chart selector | `get_chart_series_options`, `frontend/app.py` selectbox | React already has multi-series data via `forecastSeriesIdsCount`; needs a series-picker UI component + filtering of `forecastData`/`scoringData` by selected series before charting |
| Dataset version replay ("Prediction Timestamp" dropdown) | `get_scoring_dataset_versions`, `_get_scoring_data(version_id=...)` | Distinct from Change 5's dataset *swap* — this replays historical *versions* of the same dataset. Needs its own dropdown + `version_id` param threaded through `/scoringData` |
| Direct Azure OpenAI LLM bypass | `_get_direct_azure_completion`, `credentials.py::AzureOpenAICredentials` | Backend-only; port to `forecastic/api.py` `_get_completion()` — no React change needed beyond the existing `/llmSummary` call already working against whichever backend path resolves |
| LLM commentary on/off toggle | `is_llm_commentary_available()`, sidebar checkbox | Needs a capability-flag endpoint or field on `/appSettings`, plus a toggle in `Sidebar.tsx`/wherever the LLM summary is triggered |
| Brand redesign | `frontend/style.css`, dark palette, DM Sans/Fragment Mono | React already runs Tailwind + shadcn/ui with its own design tokens — this is a design-alignment task, not a functional port. Decide whether to match the Streamlit palette (`#81FBA5` green accent, `#0B0B0B`/`#111111` dark backgrounds, Fragment Mono for labels) or keep the React app's existing system |

---

## 5. Suggested Build Order

1. **Backend first** (§3): port schema + api.py changes. This unblocks both frontend work and testing, and is a near-copy from the working Streamlit branch.
2. **Change 5 testing** (§4.5): the work is done, test it before building more on top of an unverified data-loading path.
3. **Changes 1/2/4** (single-series, no-calendar, no-intervals): these are tightly coupled in the type system (`ForecastData`, `AppSettings`) — do together.
4. **Change 3** (historic chart fix): trivial once §3.2's backend normalization lands.
5. **Change 6** (XEMP chart): independent, can parallelize with 1/2/4.
6. **Enhancements** (§4.7): lowest priority — confirm with stakeholders which are in scope for the React parity milestone vs. deferred.

---

## 6. Regression / Test Matrix

Test each of the following deployment configurations against both the FastAPI endpoints directly (e.g., via `curl`/Postman) and the full React UI:

| Config | Backend check | Frontend check |
|---|---|---|
| Single-series, with calendar, with intervals | `AppSettings.multiseries_id_column is None` doesn't 500 | Sidebar renders no series filters; chart renders one line; PE panel works |
| Multiseries, no calendar | `calendar_id is None` doesn't fail `from_registered_model_version` | No visible change expected — confirm no crash |
| Multiseries, no intervals | `PredictionRow(low=None, high=None)` round-trips through Pydantic | Confidence band hidden; no `undefined` rendered in tooltip |
| Registry dataset swap mid-session | `/scoringData?active_dataset_id=...` returns correct rows | Filters reset, forecast recalculates, no stale series in What-If scenarios |
| Uploaded dataset (CSV) | `/datasets/upload` creates and returns new `DataRegistryDataset` | New dataset appears in dropdown immediately post-upload |
| Batch-prediction-style scoring data (column named `"{target} (actual)"`) | `/scoringData` returns plain `target` key | History line renders without gap/crash |

---

## Reference

- [DataRobot Datetime Partitioning](https://docs.datarobot.com/en/docs/modeling/special-workflows/ts/ts-index.html) — single-series vs. multiseries, calendars
- [`datarobot_predict.deployment.predict()`](https://datarobot.github.io/datarobot-predict/1.9/api_ref/#datarobot_predict.deployment.predict) — prediction intervals param
- [DataRobot AI Catalog / Dataset API](https://docs.datarobot.com/en/docs/api/reference/) — `Dataset.iterate()`, `Dataset.create_from_file()`
- [FastAPI docs](https://fastapi.tiangolo.com/) — used in `forecastic/rest_api.py`
- [visx](https://airbnb.io/visx/) — charting library used in the React app (`BarStack`, `LinePath`, `Brush`)
- [React Context + useReducer pattern](https://react.dev/reference/react/useReducer) — used in `state/AppState.tsx`
- [shadcn/ui](https://ui.shadcn.com/) — component library already in use (`components/ui/*`)
