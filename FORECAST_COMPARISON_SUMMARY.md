# Forecast Comparison Feature — Implementation Summary

**Branch:** `feat/forecast-comparison-page`  
**Last updated:** 2026-06-19

---

## Overview

Added an **Analysis of Drivers** page to the WFM Forecast Assistant that compares two time-series forecasts:

- **Planned scenario** — forecast made with planned (expected) input feature values
- **Actual scenario (Playback)** — forecast made with actual feature values, showing what the model would have predicted with perfect foresight

The page is backed by a pre-computed cache covering April–June 2026, so default views load instantly without live DR API calls. The **Forecast — Main** page now also overlays observed actuals on top of the live forecast chart.

---

## Files Added / Changed

### New: `prepare_forecast_cache.py`

Standalone script that pre-computes forecast data and writes `frontend/data/forecast_cache.csv`.

**How it works:**
- For each prediction week W in the selected horizon (default: 4 weeks of April 2026):
  - Builds a 20-row DR time-series scoring input:
    - **FDW** (weeks −6 to 0): historical actual values from the ACTUAL file
    - **FW planned** (weeks +1 to +13): planned feature values, `SKILL_OFFERED_SUM = NaN`
    - **FW actual** (weeks +1 to +13): actual feature values, `SKILL_OFFERED_SUM = NaN`
  - Calls `datarobot_predict.deployment.predict()` with `max_explanations=10`
  - Joins predictions with FW input feature values
- Appends `prediction_week`, `scenario`, `forecast_step` metadata columns
- Falls back to DR AI Catalog download when local CSV files are absent
- Uses codespace ambient auth; falls back to token-based auth for local use

**`--all` flag (added 2026-06-19):** auto-detects the full valid prediction-week range from source data — first week with 6 weeks of history through the last week with observed actuals.

**Output:** `frontend/data/forecast_cache.csv`

**Usage:**
```bash
# Default (April 2026):
python prepare_forecast_cache.py

# Custom date range:
python prepare_forecast_cache.py --start 2026-05-05 --end 2026-05-26

# Full auto-detected range:
python prepare_forecast_cache.py --all

# Append to existing cache (skip already-cached weeks):
python prepare_forecast_cache.py --start 2026-05-05 --end 2026-06-08 --append
```

---

### New: `notebooks/nl_weekly_weather.py`

Fetches real historical weather from the Open-Meteo archive API for 5 NL cities and aggregates to ISO Mon–Sun weeks. Replaces earlier fabricated weather data.

```bash
python nl_weekly_weather.py --start 2024-01-01   # Jan 2024 → today
```

---

### Changed: `frontend/pages/2_Analysis_of_Drivers.py`

Previously `1_Feature_Comparison.py`.

**Sidebar — Forecast Horizon selector**
- Start/End week dropdowns populated from cached prediction weeks
- Default horizon pre-set to April–May 2026
- Caption shows "N prediction weeks · M forecast dates"

**Cache loading**
- `_find_data_file()` searches multiple paths: repo root, codespace storage, cwd, `/opt/code/data/`
- `_read_cache()` / `_read_weather()` / `_read_actuals()` — `@st.cache_data`, load once per session
- `_load_from_cache(prediction_weeks, cache_df)` — loads selected weeks, preserves `forecast_step` and `prediction_week` for downstream filtering

**Main area — selectors**

| Selector | Behaviour |
|---|---|
| Series ID | Filter to a specific SKILL series; populated via `get_available_series()` using `COMPARISON_SERIES_COL` env var (default `SKILL`) |
| Forecast Week | Multi-select; pre-selects the first week in range on load; resets when the cache range changes |
| Forecast Distance | Single-select; pre-selects "4 weeks ahead" on load |

**Charts**
- **Comparison chart** — planned vs playback forecast lines with weather event vertical bands; overlays observed actuals (green dotted diamond) from `actuals_lookup.csv`
- **XEMP combined chart** — two-subplot (Planned / Actual) with a single union legend; SKILL_OFFERED_SUM features grouped in gray and hidden by default; immutable calendar features filtered out; per-scenario tooltip label ("Planned — date" / "Actual — date"); `hovermode="closest"` prevents cross-subplot tooltip bleed
- **Weather panel** — 5-city aggregated gust bars (amber=storm, blue=wind/snow, purple=heavy rain) + avg temp line + 70 km/h threshold; prominent coloured legend annotations
- **Input diff table** — numeric delta between planned and actual feature averages; immutable calendar features excluded; Vacations retained
- **Feature detail chart** — below the diff table; defaults to the feature with the highest deviation; dropdown to select any feature with delta > 0

**Weather legend** — replaced single grey annotation with 3 coloured per-event annotations (Fragment Mono, size 10).

---

### Changed: `forecastic/comparison_api.py`

| Change | Detail |
|---|---|
| `_SERIES_COL` constant | `os.environ.get("COMPARISON_SERIES_COL", "SKILL")` — replaces `app_settings.multiseries_id_column` (which was `null`) throughout the file |
| `get_available_series()` | Uses `_SERIES_COL` |
| `_filter_preds()` / `_filter_df()` | Use `_SERIES_COL` |
| `build_comparison_chart()` | Accepts `actuals_df` param; adds observed actuals trace; fixed `multiseries_id_column` → `_SERIES_COL` |
| `build_input_diff_table()` | Filters `_IMMUTABLE_FEATURES` from skip set; removed dead `ms_col` assignment |
| `build_feature_timeseries_chart()` | New — 260px line chart comparing planned vs actual values for a single feature over time; blue dashed = planned, purple solid = actual |
| `build_xemp_combined()` | New — two-subplot XEMP with shared union legend via `legendgroup`; `hovermode="closest"`; per-scenario hover labels; SKILL features in gray, `visible="legendonly"` |
| `build_xemp_color_map()` | SKILL_OFFERED_SUM features → `#606060`; others cycle `BAR_COLORS` |
| `_xemp_bar_df()` | Filters `_IMMUTABLE_FEATURES` (DAY_OF_YEAR, WEEK_OF_YEAR, MONTH, etc.) from feature importance data |
| `_IMMUTABLE_FEATURES` | New frozenset — calendar holidays and derived date features that cannot vary between planned/actual |
| `_weather_band_color()` | Handles both `"wind"` and `"snow"` event labels → blue |
| Weather legend | 3 separate coloured annotations replacing single grey one |
| Forecast tooltip rounding | `hovertemplate` with `%{y:.3s}` on main forecast traces — shows `42.7k` not `42.7062k` |

---

### Changed: `forecastic/api.py`

| Change | Detail |
|---|---|
| `get_forecast_as_plotly_json()` | Added `actuals_df: Optional[pd.DataFrame] = None` parameter |
| `_build_combined_figure()` | Added `actuals_df` parameter; adds "Observed Actuals" green dotted diamond trace on forecast panel (`row=1, col=2`) |
| Fallback single-chart path | Same "Observed Actuals" trace added |

---

### Changed: `frontend/app.py`

- Added `_load_actuals()` — loads `frontend/data/actuals_lookup.csv` with `@st.cache_data`
- Filters actuals by user-selected series before passing to the chart
- Passes `actuals_df` to `get_forecast_as_plotly_json`

---

### New data files

| File | Description |
|---|---|
| `frontend/data/forecast_cache.csv` | Pre-computed predictions (prediction weeks Jan 2024 – Jun 2026 × 2 scenarios × 13 steps) |
| `frontend/data/nl_weekly_weather_2026.csv` | Real Open-Meteo weather, 5 NL cities, Jan 2024 – Jun 2026 (640 rows) |
| `frontend/data/actuals_lookup.csv` | Observed target values by week and SKILL, Jan 2024 – Jun 2026 (128 rows) |

---

### Changed: `.env.template`

Added two new env vars:

```bash
# Column in the scoring dataset that identifies the time-series (skill group).
# Defaults to "SKILL".
# COMPARISON_SERIES_COL=SKILL

# DR AI Catalog dataset containing observed actual target values.
# Falls back to frontend/data/actuals_lookup.csv when not set.
# ACTUALS_DATASET_ID=
```

---

## Key DR Configuration

| Setting | Value |
|---|---|
| Deployment | `6a0ec47bf306847615758fa3` (WFM FORECAST TIME SERIES TEST MONITORING) |
| Planned features dataset | `6a326e0a76da3420b0d4e6e1` |
| Actual features dataset | `6a326f4d347b28ea2e55f572` |
| Endpoint | `https://app.eu.datarobot.com/api/v2` |
| FDW | −6 to 0 weeks |
| FW | +1 to +13 weeks |
| Max explanations | 10 |

---

## Forecast Comparison Logic

```
Prediction Week W (e.g. 2026-04-06)
│
├── PLANNED scenario
│   ├── FDW rows [W−6 … W]    ← ACTUAL file  (history, target kept)
│   └── FW  rows [W+1 … W+13] ← PLANNED file (future features, target=NaN)
│
└── ACTUAL scenario (playback)
    ├── FDW rows [W−6 … W]    ← ACTUAL file  (history, target kept)
    └── FW  rows [W+1 … W+13] ← ACTUAL file  (future features, target=NaN)
```

The gap between the two forecast lines shows how much of the prediction variance is explained by the difference between planned and actual input features (e.g. weather events, operational changes).

---

## To redeploy

```bash
task deploy
```

Data files in `frontend/data/` are bundled automatically. If the cache needs rebuilding first:

```bash
set -a && source .env && set +a
python prepare_forecast_cache.py --all
```
