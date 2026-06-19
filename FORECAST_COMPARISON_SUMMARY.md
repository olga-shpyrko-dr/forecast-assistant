# Forecast Comparison Feature — Implementation Summary

**Branch:** `feat/forecast-comparison-page`  
**Date:** 2026-06-19

---

## Overview

Added a **Analysis of Drivers** page to the WFM Forecast Assistant that compares two time-series forecasts:

- **Planned scenario** — forecast made with planned (expected) input feature values
- **Actual scenario (Playback)** — forecast made with actual feature values, showing what the model would have predicted with perfect foresight

The page is backed by a pre-computed cache for April 2026, so default views load instantly without live DR API calls.

---

## Files Added / Changed

### New: `prepare_forecast_cache.py`

Standalone script that pre-computes forecast data and writes `data/forecast_cache.csv`.

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

**Output:** `data/forecast_cache.csv` — 104 rows × 86 columns  
(4 weeks × 2 scenarios × 13 forecast steps, with XEMP explanations and input features)

**Usage:**
```bash
# In DataRobot Codespace (no token needed):
python prepare_forecast_cache.py

# Local use:
set -a && source .env && set +a
python prepare_forecast_cache.py

# Custom date range:
python prepare_forecast_cache.py --start 2026-05-05 --end 2026-05-26

# Append to existing cache:
python prepare_forecast_cache.py --start 2026-05-05 --end 2026-05-26 --append
```

---

### Changed: `frontend/pages/2_Analysis_of_Drivers.py`

Previously `1_Feature_Comparison.py`. Significant additions:

**Sidebar — Forecast Horizon selector**
- Start/End week dropdowns populated from cached prediction weeks
- Caption shows how many unique forecast dates will be loaded (e.g. "4 prediction weeks · 16 forecast dates")
- "Custom (run live predictions)" checkbox reveals dataset upload UI for dates outside the cache

**Cache loading**
- `_find_data_file()` searches multiple paths: repo root, codespace storage (`/home/notebooks/storage/...`), cwd, `/opt/code/data/`
- `_read_cache()` / `_read_weather()` — `@st.cache_data` decorated, load once per session
- `_load_from_cache(prediction_weeks, cache_df)` — loads all weeks in the selected range, preserves `forecast_step` and `prediction_week` in records for downstream filtering

**Main area — three selectors**
| Selector | Purpose |
|---|---|
| Series ID | Filter to a specific SKILL series |
| Forecast Week | Which future date is being forecasted (16 options across April horizon) |
| Forecast Distance | How many weeks ahead the forecast was made (1–13) |

The Forecast Distance filter pre-filters prediction records before all charts and the LLM summary, without changing chart function signatures.

**Weather — auto-loaded**
- `data/nl_weekly_weather_2026.csv` is loaded automatically (no user upload required for April weeks)
- Passed to the comparison chart (weather bands) and LLM summary

---

### Changed: `forecastic/comparison_api.py`

| Function | Change |
|---|---|
| `get_forecast_distances()` | New — returns sorted unique `forecast_step` values from prediction records |
| `_aggregate_weather()` | New — aggregates 5-city weather to one row per `week_start` |
| `_weather_band_color()` | New — maps event type to RGBA fill color (storm=amber, snow=blue, heavy_rain=purple) |
| `build_weather_panel()` | New — 280px dual-axis chart: max gust bars (color-coded by event) + avg temp line + 70 km/h storm threshold |
| `build_comparison_chart()` | Added `weather_df` param — overlays colored vertical bands on forecast weeks with adverse events |
| `build_xemp_color_map()` | New — builds shared feature→color map across both scenarios for consistent XEMP colors |
| `build_xemp_bar()` | Added `color_map` param; uses shared palette |
| `_xemp_bar_df()` | Strips `(actual)` suffix from feature names; trims ISO timestamps to YYYY-MM-DD |
| `_find_prediction_col()` | Dynamic detection — tries `TARGET_PREDICTION`, `PREDICTION`, then `endswith("_PREDICTION")` |
| `_ensure_association_id()` | Constructs `SKILL_START_OF_WEEK` if `ASSOCIATION_ID` column is absent |

---

## Data Files

| File | Description | In Git |
|---|---|---|
| `data/forecast_cache.csv` | Pre-computed predictions for April 2026 (4 weeks × 2 scenarios × 13 steps) | ⚠️ Must be committed from codespace after running script |
| `data/nl_weekly_weather_2026.csv` | Netherlands weather, 5 cities, weekly Dec 2025 – Jun 2026 | ✅ |

**To commit the cache from the codespace:**
```bash
cd ~/storage/forecast-assistant
git add data/forecast_cache.csv data/nl_weekly_weather_2026.csv
git commit -m "data: add pre-computed forecast cache and NL weather data"
git push
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
