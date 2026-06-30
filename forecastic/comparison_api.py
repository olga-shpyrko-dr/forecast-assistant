# Copyright 2024 DataRobot, Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, List, Optional

import datarobot as dr
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from datarobot_predict.deployment import predict

from forecastic.api import (
    BAR_COLORS,
    LLMNotAvailableException,
    _AXIS_STYLE,
    _LAYOUT_BASE,
    _get_completion,
    app_settings,

)
from forecastic.resources import TimeSeriesDeployment
from forecastic.schema import ComparisonSummary

# Column in the scoring dataset that identifies the series (e.g. "SKILL").
# Override with COMPARISON_SERIES_COL env var if the column name differs.
_SERIES_COL: str = os.environ.get("COMPARISON_SERIES_COL", "SKILL")


# Features that are fixed by the calendar and cannot differ between planned/actual scenarios.
# Vacations is intentionally excluded from this set — vacation periods can shift.
_IMMUTABLE_FEATURES: frozenset[str] = frozenset({
    # Dutch public holidays
    "1e Kerstdag", "2e Kerstdag",
    "1e Paasdag",  "2e Paasdag",
    "1e Pinksterdag", "2e Pinksterdag",
    "Bevrijdingsdag", "Hemelvaartsdag", "Koningsdag", "Nieuwjaarsdag",
    # Shopping calendar
    "Black Friday", "Cyber Monday",
    # Holiday aggregates
    "Holidays", "SpecialDays", "Kerst",
    # Pure calendar numerics
    "DAY_OF_YEAR", "WEEK_OF_YEAR", "MONTH", "YEAR",
    # Fixed seasonal flags
    "Herfst", "Zomer", "Mei", "Voorjaar",
    # Model metadata
    "FORECAST_DISTANCE",
})

# Keywords that make a feature immutable regardless of suffix/transformation
# e.g. "WEEK_OF_YEAR (6 week mean)" should be filtered same as "WEEK_OF_YEAR"
_IMMUTABLE_KEYWORDS: tuple[str, ...] = (
    "DAY_OF_YEAR", "WEEK_OF_YEAR", "MONTH", "YEAR", "FORECAST_DISTANCE",
)


def _is_immutable(feature: str) -> bool:
    return feature in _IMMUTABLE_FEATURES or any(kw in feature for kw in _IMMUTABLE_KEYWORDS)


# ── Data loading ──────────────────────────────────────────────────────────────

def load_from_upload(uploaded_file: Any) -> pd.DataFrame:
    return pd.read_csv(uploaded_file)


def load_from_catalog(dataset_id: str) -> pd.DataFrame:
    return dr.Dataset.get(dataset_id).get_as_dataframe()


def _ensure_association_id(df: pd.DataFrame) -> pd.DataFrame:
    if "ASSOCIATION_ID" not in df.columns:
        skill = df["SKILL"].astype(str) if "SKILL" in df.columns else pd.Series(["ROW"] * len(df), index=df.index)
        date = df["START_OF_WEEK"].astype(str) if "START_OF_WEEK" in df.columns else pd.Series(range(len(df)), index=df.index).astype(str)
        df = df.copy()
        df["ASSOCIATION_ID"] = skill + "_" + date
    return df


def run_predictions(df: pd.DataFrame) -> list[dict[str, Any]]:
    deployment_id = TimeSeriesDeployment().id
    result = predict(
        deployment=dr.Deployment.get(deployment_id),
        data_frame=_ensure_association_id(df),
        max_explanations=10,
    )
    preds_df = result.dataframe.copy()

    # Compute forecast_step: number of weeks from the last FDW row to each forecast date.
    # FDW rows are those where the target column is not NaN in the scoring input.
    target_col = app_settings.target
    date_col = app_settings.datetime_partition_column
    if target_col in df.columns and date_col in df.columns:
        fdw_dates = pd.to_datetime(
            df[df[target_col].notna()][date_col].astype(str).str[:10],
            errors="coerce",
        )
        if not fdw_dates.empty:
            fdw_end = fdw_dates.max()
            pred_dates = pd.to_datetime(preds_df[date_col].astype(str).str[:10], errors="coerce")
            preds_df["forecast_step"] = ((pred_dates - fdw_end).dt.days / 7).round().astype("Int64")

    return preds_df.to_dict(orient="records")  # type: ignore[no-any-return]


# ── New-data detection and cache append ──────────────────────────────────────

def get_missing_weeks(cache_df: pd.DataFrame, scoring_df: pd.DataFrame) -> list[str]:
    """Return prediction weeks present in the scoring dataset but absent from the cache.

    Available weeks = all dates with a non-NaN target in the scoring dataset (i.e. weeks
    for which we have real observed history that could serve as the FDW end).
    Missing weeks = available - already cached, filtered to only weeks with enough FDW
    history in the scoring dataset (≥ |fdw_start_wks| rows), returned ascending.
    """
    target_col = app_settings.target
    date_col = app_settings.datetime_partition_column
    fdw_start_wks = app_settings.feature_derivation_window_start  # e.g. -6
    min_fdw_rows = abs(fdw_start_wks)

    df = scoring_df.copy()
    df[date_col] = df[date_col].astype(str).str[:10]

    available = set(df.loc[df[target_col].notna(), date_col].unique())
    cached = set(cache_df["prediction_week"].astype(str).str[:10].unique())
    candidates = sorted(available - cached)

    valid = []
    for week in candidates:
        fdw_start = (pd.Timestamp(week) + pd.Timedelta(weeks=fdw_start_wks)).strftime("%Y-%m-%d")
        fdw_count = df[
            (df[date_col] >= fdw_start) & (df[date_col] <= week) & df[target_col].notna()
        ].shape[0]
        if fdw_count >= min_fdw_rows:
            valid.append(week)
    return valid


def _build_scoring_input_for_week(
    scoring_df: pd.DataFrame,
    prediction_week: str,
) -> pd.DataFrame:
    """Slice scoring_df into the FDW + FW window for a specific prediction week.

    FDW rows (date <= prediction_week): keep real target values.
    FW rows  (date >  prediction_week): force target to NaN so the model treats them
    as unknowns, even if actuals have since become available in the dataset.
    """
    target_col = app_settings.target
    date_col = app_settings.datetime_partition_column
    fdw_start_wks = app_settings.feature_derivation_window_start  # e.g. -6
    fw_end_wks = app_settings.forecast_window_end                 # e.g. 13

    pred_dt = pd.Timestamp(prediction_week)
    fdw_start = (pred_dt + pd.Timedelta(weeks=fdw_start_wks)).strftime("%Y-%m-%d")
    fw_end = (pred_dt + pd.Timedelta(weeks=fw_end_wks)).strftime("%Y-%m-%d")

    window = scoring_df[
        (scoring_df[date_col] >= fdw_start) & (scoring_df[date_col] <= fw_end)
    ].copy()

    # Clear target for all FW rows so the model forecasts rather than uses actuals
    window.loc[window[date_col] > prediction_week, target_col] = float("nan")
    return window


def _update_actuals_from_scoring(scoring_df: pd.DataFrame, cache_path: Path) -> None:
    """Upsert actual values from the scoring dataset into actuals_lookup.csv.

    Reads all rows with a non-NaN target, groups by date, and merges into the
    existing actuals file (or creates it). Called automatically by
    append_scoring_week_to_cache so actuals stay current with each cache update.
    """
    target_col = app_settings.target
    date_col = app_settings.datetime_partition_column
    actuals_path = Path(cache_path).parent / "actuals_lookup.csv"

    df = scoring_df.copy()
    df[date_col] = df[date_col].astype(str).str[:10]

    new_actuals = (
        df[df[target_col].notna()]
        .groupby(date_col, as_index=False)[target_col]
        .sum()
        .rename(columns={date_col: "START_OF_WEEK", target_col: "SKILL_OFFERED_SUM"})
    )

    if actuals_path.exists():
        existing = pd.read_csv(actuals_path)
        existing["START_OF_WEEK"] = existing["START_OF_WEEK"].astype(str).str[:10]
        existing = existing[~existing["START_OF_WEEK"].isin(new_actuals["START_OF_WEEK"])]
        combined = pd.concat([existing, new_actuals], ignore_index=True)
    else:
        combined = new_actuals

    actuals_path.parent.mkdir(parents=True, exist_ok=True)
    combined.sort_values("START_OF_WEEK").to_csv(actuals_path, index=False)


def append_scoring_week_to_cache(
    scoring_df: pd.DataFrame,
    cache_path: Path,
    prediction_week: str,
    force: bool = False,
) -> None:
    """Build a planned-scenario cache entry for prediction_week and append to the cache CSV.

    Only the planned scenario is written (actual inputs are not yet known for new weeks).
    Idempotent: skips silently if this week+scenario is already present in the cache,
    unless force=True, which overwrites the existing entry with the latest scoring data.
    """
    target_col = app_settings.target
    date_col = app_settings.datetime_partition_column

    scoring_df = scoring_df.copy()
    scoring_df[date_col] = scoring_df[date_col].astype(str).str[:10]

    cache_path = Path(cache_path)
    if not force and cache_path.exists():
        existing = pd.read_csv(cache_path)
        already = existing[
            (existing["prediction_week"] == prediction_week) & (existing["scenario"] == "planned")
        ]
        if not already.empty:
            return

    scoring_input = _build_scoring_input_for_week(scoring_df, prediction_week)
    if scoring_input.empty:
        raise ValueError(f"No data found in scoring dataset for week {prediction_week}.")

    preds = run_predictions(scoring_input)
    preds_df = pd.DataFrame(preds)
    preds_df[date_col] = preds_df[date_col].astype(str).str[:10]

    pred_dt = pd.Timestamp(prediction_week)
    fw_rows = scoring_input[scoring_input[target_col].isna()].copy()
    fw_dates_dt = pd.to_datetime(fw_rows[date_col], errors="coerce")
    fw_rows["forecast_step"] = ((fw_dates_dt - pred_dt).dt.days / 7).round().astype(int)

    extra_cols = [
        c for c in fw_rows.columns
        if c not in preds_df.columns and c not in {target_col, "ASSOCIATION_ID"}
    ]
    merged = preds_df.merge(
        fw_rows[[date_col, "forecast_step"] + extra_cols],
        on=date_col,
        how="left",
    )
    merged.insert(0, "scenario", "planned")
    merged.insert(0, "prediction_week", prediction_week)

    if cache_path.exists():
        existing = pd.read_csv(cache_path)
        existing = existing[
            ~((existing["prediction_week"] == prediction_week) & (existing["scenario"] == "planned"))
        ]
        result = pd.concat([existing, merged], ignore_index=True)
    else:
        result = merged

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    result.to_csv(cache_path, index=False)
    _update_actuals_from_scoring(scoring_df, cache_path)


# ── Selector helpers ──────────────────────────────────────────────────────────

def get_available_series(planned_df: pd.DataFrame, actual_df: pd.DataFrame) -> list[str]:
    planned = set(planned_df[_SERIES_COL].unique()) if _SERIES_COL in planned_df.columns else set()
    actual  = set(actual_df[_SERIES_COL].unique())  if _SERIES_COL in actual_df.columns  else set()
    return sorted(planned | actual)


def get_forecast_distances(preds: list[dict]) -> list[int]:
    """Return sorted unique forecast steps (1–13) present in prediction records."""
    if not preds or "forecast_step" not in preds[0]:
        return []
    return sorted({int(r["forecast_step"]) for r in preds if r.get("forecast_step") is not None})


def get_forecast_dates(planned_preds: list[dict], actual_preds: list[dict]) -> list[str]:
    date_col = app_settings.datetime_partition_column
    dates = {str(r[date_col])[:10] for r in planned_preds} | {str(r[date_col])[:10] for r in actual_preds}
    return sorted(dates)


# ── Internal helpers ──────────────────────────────────────────────────────────

def _filter_preds(preds: list[dict], series_id: Optional[str]) -> list[dict]:
    if not series_id:
        return preds
    return [r for r in preds if r.get(_SERIES_COL) == series_id]


def _filter_df(df: pd.DataFrame, series_id: Optional[str]) -> pd.DataFrame:
    if not series_id or _SERIES_COL not in df.columns:
        return df
    return df[df[_SERIES_COL] == series_id]


def _find_prediction_col(preds_df: pd.DataFrame) -> str:
    """Return the prediction value column, trying several naming conventions."""
    target = app_settings.target
    candidates = [
        f"{target}_PREDICTION",
        "PREDICTION",
        "prediction",
    ]
    for c in candidates:
        if c in preds_df.columns:
            return c
    # Last resort: first column whose name ends with _PREDICTION
    for c in preds_df.columns:
        if c.upper().endswith("_PREDICTION"):
            return c
    raise KeyError(
        f"Could not find a prediction column in {list(preds_df.columns)}. "
        f"Expected '{target}_PREDICTION' or similar."
    )


def _preds_to_fc_df(preds: list[dict]) -> pd.DataFrame:
    """Convert raw DR prediction dicts to date_id / prediction / low / high DataFrame."""
    preds_df = pd.DataFrame(preds)
    date_col = app_settings.datetime_partition_column
    target_pred_col = _find_prediction_col(preds_df)

    result = (
        preds_df[[date_col, target_pred_col]]
        .rename(columns={date_col: "date_id", target_pred_col: "prediction"})
        .copy()
    )

    has_intervals = False
    pi_prefix = ""
    if app_settings.prediction_interval is not None:
        pi_str = f"{app_settings.prediction_interval:.0f}"
        pi_prefix = f"PREDICTION_{pi_str}_PERCENTILE"
        has_intervals = f"{pi_prefix}_LOW" in preds_df.columns

    if has_intervals:
        result["low"] = preds_df[f"{pi_prefix}_LOW"].values
        result["high"] = preds_df[f"{pi_prefix}_HIGH"].values
    else:
        result["low"] = None
        result["high"] = None

    if app_settings.lower_bound_forecast_at_0:
        result["prediction"] = result["prediction"].clip(lower=0)
        if has_intervals:
            result["low"] = result["low"].clip(lower=0)
            result["high"] = result["high"].clip(lower=0)

    return result.sort_values("date_id").reset_index(drop=True)


def _history_from_df(df: pd.DataFrame, n_history: int) -> pd.DataFrame:
    """Aggregate scoring DataFrame into history rows for plotting."""
    datetime_col = app_settings.datetime_partition_column
    date_format = app_settings.date_format
    return (
        df.groupby(datetime_col, dropna=True)
        .sum(numeric_only=True, min_count=1)
        .reset_index()
        .assign(
            timestamp=lambda x: pd.to_datetime(x[datetime_col], format="mixed")
        )
        .sort_values("timestamp")
        .tail(n_history)
    )


def _xemp_bar_df(preds: list[dict], selected_week: Optional[list[str]] = None) -> pd.DataFrame:
    preds_df = pd.DataFrame(preds)
    date_col = app_settings.datetime_partition_column
    rows = []
    for i in range(1, 11):
        feat_col = f"EXPLANATION_{i}_FEATURE_NAME"
        str_col  = f"EXPLANATION_{i}_STRENGTH"
        val_col  = f"EXPLANATION_{i}_ACTUAL_VALUE"
        if feat_col not in preds_df.columns:
            continue
        cols = [date_col, feat_col, str_col]
        rename = {date_col: "date_id", feat_col: "feature", str_col: "strength"}
        if val_col in preds_df.columns:
            cols.append(val_col)
            rename[val_col] = "actual_value"
        tmp = preds_df[cols].rename(columns=rename)
        if "actual_value" not in tmp.columns:
            tmp["actual_value"] = float("nan")
        rows.append(tmp)
    if not rows:
        return pd.DataFrame(columns=["date_id", "feature", "strength", "actual_value"])
    combined = pd.concat(rows, ignore_index=True)
    combined["feature"] = combined["feature"].str.replace(r"\s*\(actual\)\s*$", "", regex=True).str.strip()
    combined = combined[~combined["feature"].apply(_is_immutable)]
    result = combined.groupby(["date_id", "feature"], as_index=False).agg(
        strength=("strength", "sum"),
        actual_value=("actual_value", "mean"),
    )
    # Trim ISO timestamps to YYYY-MM-DD for readable axis labels
    result["date_id"] = result["date_id"].astype(str).str[:10]
    if selected_week:
        result = result[result["date_id"].isin([str(w)[:10] for w in selected_week])]
    return result


# ── Chart builders ────────────────────────────────────────────────────────────

def _aggregate_weather(weather_df: pd.DataFrame) -> pd.DataFrame:
    """Aggregate multi-city weather to one row per week_start."""
    df = weather_df.copy()
    df["week_start"] = df["week_start"].astype(str).str[:10]
    agg = (
        df.groupby("week_start")
        .agg(
            temp_avg_c=("temp_avg_c", "mean"),
            max_gust_kmh=("max_gust_kmh", "max"),
            precip_mm=("precip_mm", "mean"),
            adverse_events=("adverse_events", lambda x: "|".join(
                sorted({v.strip() for v in "|".join(x.dropna()).split("|") if v.strip()})
            )),
        )
        .reset_index()
    )
    return agg


def _weather_band_color(event: str) -> Optional[str]:
    if "storm" in event:
        return "rgba(255,140,0,0.12)"   # amber — storm
    if "wind" in event or "snow" in event:
        return "rgba(68,191,252,0.10)"  # blue — wind/snow
    if "heavy_rain" in event:
        return "rgba(144,155,245,0.10)" # purple — heavy rain
    return None


def build_comparison_chart(
    planned_preds: list[dict],
    actual_preds: list[dict],
    planned_df: pd.DataFrame,
    n_history: int,
    series_id: Optional[str] = None,
    selected_week: Optional[list[str]] = None,
    whatif_preds: Optional[list[dict]] = None,
    weather_df: Optional[pd.DataFrame] = None,
    actuals_df: Optional[pd.DataFrame] = None,
) -> dict[str, Any]:
    """Overlay chart: history baseline + planned forecast (blue dashed) + actual forecast (purple)
    + observed actuals for the forecast window (green dotted) when available."""
    target = app_settings.target
    datetime_col = app_settings.datetime_partition_column

    planned_fc = _preds_to_fc_df(_filter_preds(planned_preds, series_id))
    actual_fc = _preds_to_fc_df(_filter_preds(actual_preds, series_id))

    history_df = _filter_df(planned_df, series_id)
    history = _history_from_df(history_df, n_history)
    actual_col = f"{target} (actual)" if f"{target} (actual)" in history.columns else target
    history_actual = history[history[actual_col].notna()]

    fig = go.Figure()

    # History (green solid)
    fig.add_trace(go.Scatter(
        x=history_actual["timestamp"], y=history_actual[actual_col],
        mode="lines+markers", name=f"{target} History",
        line=dict(color="#81FBA5", width=1.5),
        marker=dict(color="#81FBA5", size=5),
    ))

    # Planned confidence band + line (blue dashed)
    if planned_fc["low"].notna().any():
        fig.add_trace(go.Scatter(
            x=planned_fc["date_id"], y=planned_fc["low"], mode="lines",
            line=dict(color="#44BFFC", width=0.8, dash="dot"), showlegend=False, name="Planned Low",
        ))
        fig.add_trace(go.Scatter(
            x=planned_fc["date_id"], y=planned_fc["high"], mode="lines",
            line=dict(color="#44BFFC", width=0.8, dash="dot"),
            fill="tonexty", fillcolor="rgba(68,191,252,0.07)", showlegend=False, name="Planned High",
        ))
    fig.add_trace(go.Scatter(
        x=planned_fc["date_id"], y=planned_fc["prediction"],
        mode="lines+markers", name="Forecast with Planned Inputs",
        line=dict(color="#44BFFC", width=1.8, dash="dash"),
        marker=dict(color="#44BFFC", size=5),
        hovertemplate="<b>%{x}</b><br>Forecast with Planned Inputs: %{y:.3s}<extra></extra>",
    ))

    # Playback forecast band + line (purple solid)
    if actual_fc["low"].notna().any():
        fig.add_trace(go.Scatter(
            x=actual_fc["date_id"], y=actual_fc["low"], mode="lines",
            line=dict(color="#909BF5", width=0.8, dash="dot"), showlegend=False, name="Playback Low",
        ))
        fig.add_trace(go.Scatter(
            x=actual_fc["date_id"], y=actual_fc["high"], mode="lines",
            line=dict(color="#909BF5", width=0.8, dash="dot"),
            fill="tonexty", fillcolor="rgba(144,155,245,0.07)", showlegend=False, name="Playback High",
        ))
    fig.add_trace(go.Scatter(
        x=actual_fc["date_id"], y=actual_fc["prediction"],
        mode="lines+markers", name="Playback Forecast (Actual Inputs)",
        line=dict(color="#909BF5", width=1.8),
        marker=dict(color="#909BF5", size=5),
        hovertemplate="<b>%{x}</b><br>Playback Forecast (Actual Inputs): %{y:.3s}<extra></extra>",
    ))

    # What-If line (yellow dotted) — Stage 2
    if whatif_preds:
        wi_fc = _preds_to_fc_df(_filter_preds(whatif_preds, series_id))
        fig.add_trace(go.Scatter(
            x=wi_fc["date_id"], y=wi_fc["prediction"],
            mode="lines+markers", name="What-If Forecast",
            line=dict(color="#FFFF54", width=1.5, dash="dot"),
            marker=dict(color="#FFFF54", size=5),
        ))

    # Observed actuals overlay (green dotted) — forecast window ground truth
    if actuals_df is not None and not actuals_df.empty:
        date_col_a = app_settings.datetime_partition_column
        ms_col_a = _SERIES_COL
        obs = actuals_df.copy()
        if ms_col_a and ms_col_a in obs.columns and series_id:
            obs = obs[obs[ms_col_a] == series_id]
        # Scope to the full forecast window — selected_week controls XEMP, not actuals
        fc_dates = set(planned_fc["date_id"].astype(str).str[:10]) | set(actual_fc["date_id"].astype(str).str[:10])
        obs = obs[obs[date_col_a].astype(str).str[:10].isin(fc_dates)]
        obs = obs[[date_col_a, app_settings.target]].dropna().sort_values(date_col_a)
        if not obs.empty:
            fig.add_trace(go.Scatter(
                x=obs[date_col_a], y=obs[app_settings.target],
                mode="lines+markers", name="Observed Actuals",
                line=dict(color="#81FBA5", width=2, dash="dot"),
                marker=dict(color="#81FBA5", size=6, symbol="diamond"),
            ))

    # Forecast-start vertical divider
    if len(history_actual):
        fig.add_vline(
            x=str(history_actual["timestamp"].max()),
            line_width=1, line_dash="dash", line_color="#2a2a2a",
        )

    # Selected-week highlights (one vline per selected week)
    if selected_week:
        for sw in selected_week:
            fig.add_vline(
                x=str(sw)[:10], line_width=2, line_dash="solid",
                line_color="#FFFF54", opacity=0.55,
            )

    # Weather event bands — only within the forecast window
    if weather_df is not None and not weather_df.empty:
        forecast_dates = (
            set(planned_fc["date_id"].astype(str).str[:10])
            | set(actual_fc["date_id"].astype(str).str[:10])
        )
        w_agg = _aggregate_weather(weather_df)
        for _, row in w_agg.iterrows():
            if str(row["week_start"])[:10] not in forecast_dates:
                continue
            color = _weather_band_color(str(row["adverse_events"]))
            if color:
                week_dt = pd.Timestamp(row["week_start"])
                fig.add_vrect(
                    x0=str(week_dt), x1=str(week_dt + pd.Timedelta(days=7)),
                    fillcolor=color, layer="below", line_width=0,
                )
        # Weather legend — three separate annotations so each uses its event colour
        _legend_items = [
            ("▐ storm",      "#FF8C00"),
            ("▐ wind/snow",  "#44BFFC"),
            ("▐ heavy rain", "#909BF5"),
        ]
        for _i, (_label, _color) in enumerate(reversed(_legend_items)):
            fig.add_annotation(
                text=_label, xref="paper", yref="paper",
                x=1.0 - _i * 0.13, y=1.04,
                xanchor="right", yanchor="bottom", showarrow=False,
                font=dict(family="Fragment Mono, monospace", size=10, color=_color),
            )

    fig.update_xaxes(**_AXIS_STYLE, title_text=datetime_col, type="date")
    fig.update_yaxes(**_AXIS_STYLE, title_text=app_settings.graph_y_axis)
    fig.update_layout(
        **_LAYOUT_BASE,
        height=480,
        showlegend=True,
        legend=dict(
            orientation="h", yanchor="top", y=-0.14,
            font=dict(family="DM Sans", size=12, color="#A2A2A2"),
            bgcolor="rgba(0,0,0,0)",
        ),
        margin=dict(l=50, r=30, b=80, t=50, pad=4),
    )
    fig.update_traces(connectgaps=False)

    # Eyebrow annotations
    fig.add_annotation(
        text="PLANNED vs ACTUAL FORECAST", xref="paper", yref="paper",
        x=0.0, y=1.04, xanchor="left", yanchor="bottom", showarrow=False,
        font=dict(family="Fragment Mono, monospace", size=9, color="#81FBA5"),
    )
    return fig.to_dict()  # type: ignore[no-any-return]


_SKILL_SUM_COLOR = "#606060"  # muted gray for SKILL_OFFERED_SUM lag/stat features


def build_xemp_color_map(
    planned_preds: list[dict],
    actual_preds: list[dict],
    series_id: Optional[str] = None,
) -> dict[str, str]:
    """Build a stable feature→color mapping from the union of both prediction sets.

    SKILL_OFFERED_SUM features all share a muted gray; remaining features cycle
    through BAR_COLORS so they're visually distinct.
    """
    planned_df = _xemp_bar_df(_filter_preds(planned_preds, series_id))
    actual_df = _xemp_bar_df(_filter_preds(actual_preds, series_id))
    all_features: list[str] = []
    seen: set[str] = set()
    for feat in list(planned_df["feature"].unique()) + list(actual_df["feature"].unique()):
        if feat not in seen:
            all_features.append(feat)
            seen.add(feat)
    color_idx = 0
    result: dict[str, str] = {}
    for feat in all_features:
        if "SKILL_OFFERED_SUM" in feat:
            result[feat] = _SKILL_SUM_COLOR
        else:
            result[feat] = BAR_COLORS[color_idx % len(BAR_COLORS)]
            color_idx += 1
    return result


def build_xemp_bar(
    preds: list[dict],
    series_id: Optional[str] = None,
    selected_week: Optional[list[str]] = None,
    label: str = "",
    color_map: Optional[dict[str, str]] = None,
) -> dict[str, Any]:
    """Single XEMP stacked bar chart for one prediction set."""
    bar_df = _xemp_bar_df(_filter_preds(preds, series_id), selected_week)

    fig = go.Figure()
    if bar_df.empty:
        fig.update_layout(**_LAYOUT_BASE, height=300, margin=dict(l=40, r=20, b=60, t=40))
        return fig.to_dict()  # type: ignore[no-any-return]

    for i, feat in enumerate(bar_df["feature"].unique()):
        feat_data = bar_df[bar_df["feature"] == feat]
        color = color_map.get(feat, BAR_COLORS[i % len(BAR_COLORS)]) if color_map else BAR_COLORS[i % len(BAR_COLORS)]
        fig.add_trace(go.Bar(
            x=feat_data["date_id"],
            y=feat_data["strength"],
            name=feat,
            marker_color=color,
            hovertemplate="<b>%{x}</b><br>%{fullData.name}: %{y:,.1f}<extra></extra>",
        ))

    eyebrow = label.upper() if label else "XEMP FEATURE IMPACT"
    fig.add_annotation(
        text=eyebrow, xref="paper", yref="paper",
        x=0.0, y=1.04, xanchor="left", yanchor="bottom", showarrow=False,
        font=dict(family="Fragment Mono, monospace", size=9, color="#81FBA5"),
    )

    xaxis_kw = {
        **_AXIS_STYLE,
        "type": "category",
        "tickangle": -40,
        "tickfont": dict(family="DM Sans", size=10, color="#A2A2A2"),
    }
    fig.update_layout(
        **_LAYOUT_BASE,
        height=480,
        barmode="relative",
        showlegend=True,
        legend=dict(
            orientation="h",
            yanchor="top", y=-0.35,
            xanchor="left", x=0,
            font=dict(family="DM Sans", size=11, color="#A2A2A2"),
            bgcolor="rgba(0,0,0,0)",
            tracegroupgap=4,
        ),
        margin=dict(l=50, r=20, b=180, t=50, pad=4),
        xaxis=xaxis_kw,
        yaxis=dict(**_AXIS_STYLE, title_text="XEMP Strength"),
    )
    return fig.to_dict()  # type: ignore[no-any-return]


def build_xemp_combined(
    planned_preds: list[dict],
    actual_preds: list[dict],
    series_id: Optional[str] = None,
    selected_week: Optional[list[str]] = None,
    color_map: Optional[dict[str, str]] = None,
) -> dict[str, Any]:
    """Two-subplot XEMP figure (Planned | Actual) with a single union legend.

    SKILL_OFFERED_SUM features are grouped at the bottom of the legend and
    start hidden (visible='legendonly') so the chart opens focused on real
    driver differences.  Clicking any legend item toggles it in both subplots.
    """
    planned_bar_df = _xemp_bar_df(_filter_preds(planned_preds, series_id), selected_week)
    actual_bar_df  = _xemp_bar_df(_filter_preds(actual_preds,  series_id), selected_week)

    # Union of features: non-SKILL first (by first-appearance order), SKILL last
    all_feats: list[str] = []
    seen: set[str] = set()
    for feat in (
        list(planned_bar_df["feature"].unique())
        + list(actual_bar_df["feature"].unique())
    ):
        if feat not in seen:
            all_feats.append(feat)
            seen.add(feat)
    ordered = [f for f in all_feats if "SKILL_OFFERED_SUM" not in f] + \
              [f for f in all_feats if "SKILL_OFFERED_SUM" in f]

    fig = make_subplots(
        rows=1, cols=2,
        horizontal_spacing=0.06,
    )

    for feat in ordered:
        is_skill = "SKILL_OFFERED_SUM" in feat
        color = _SKILL_SUM_COLOR if is_skill else (
            color_map.get(feat, BAR_COLORS[0]) if color_map else BAR_COLORS[0]
        )
        visibility: Any = "legendonly" if is_skill else True

        p_data = planned_bar_df[planned_bar_df["feature"] == feat]
        a_data = actual_bar_df[actual_bar_df["feature"] == feat]

        base = dict(name=feat, marker_color=color, legendgroup=feat, visible=visibility)
        fig.add_trace(go.Bar(**base, showlegend=True,
            x=p_data["date_id"], y=p_data["strength"],
            customdata=p_data["actual_value"].values,
            hovertemplate=(
                "<b>Planned — %{x}</b><br>"
                "%{fullData.name}: %{y:,.1f}<br>"
                "Value: %{customdata:,.2f}"
                "<extra></extra>"
            ),
        ), row=1, col=1)
        fig.add_trace(go.Bar(**base, showlegend=False,
            x=a_data["date_id"], y=a_data["strength"],
            customdata=a_data["actual_value"].values,
            hovertemplate=(
                "<b>Actual — %{x}</b><br>"
                "%{fullData.name}: %{y:,.1f}<br>"
                "Value: %{customdata:,.2f}"
                "<extra></extra>"
            ),
        ), row=1, col=2)

    xaxis_kw = {
        **_AXIS_STYLE,
        "type": "category",
        "tickangle": -40,
        "tickfont": dict(family="DM Sans", size=10, color="#A2A2A2"),
    }
    fig.update_xaxes(**xaxis_kw)
    fig.update_yaxes(**_AXIS_STYLE, title_text="XEMP Strength", col=1)
    fig.update_yaxes(**_AXIS_STYLE, col=2)

    fig.add_annotation(
        text="PLANNED", xref="paper", yref="paper",
        x=0.0, y=1.04, xanchor="left", yanchor="bottom", showarrow=False,
        font=dict(family="Fragment Mono, monospace", size=9, color="#81FBA5"),
    )
    fig.add_annotation(
        text="ACTUAL", xref="paper", yref="paper",
        x=0.52, y=1.04, xanchor="left", yanchor="bottom", showarrow=False,
        font=dict(family="Fragment Mono, monospace", size=9, color="#81FBA5"),
    )

    fig.update_layout(**_LAYOUT_BASE, height=500)
    fig.update_layout(
        hovermode="closest",  # override _LAYOUT_BASE's "x unified"; avoids cross-subplot bleed
        barmode="relative",
        showlegend=True,
        legend=dict(
            orientation="h", yanchor="top", y=-0.30, xanchor="left", x=0,
            font=dict(family="DM Sans", size=11, color="#A2A2A2"),
            bgcolor="rgba(0,0,0,0)",
            tracegroupgap=0,
        ),
        margin=dict(l=50, r=20, b=200, t=50, pad=4),
    )
    return fig.to_dict()  # type: ignore[no-any-return]


# ── Weather panel ────────────────────────────────────────────────────────────

def build_weather_panel(
    weather_df: pd.DataFrame,
    forecast_dates: Optional[list[str]] = None,
) -> dict[str, Any]:
    """
    Dual-axis weather chart: max gust bars (color-coded by event) + avg temp line.
    Filtered to forecast_dates when provided.
    """
    w = _aggregate_weather(weather_df)

    if forecast_dates:
        w = w[w["week_start"].isin([str(d)[:10] for d in forecast_dates])]

    if w.empty:
        fig = go.Figure()
        fig.update_layout(**_LAYOUT_BASE, height=200)
        return fig.to_dict()  # type: ignore[no-any-return]

    EVENT_COLORS = {
        "storm":      "#FF8C00",  # amber
        "wind":       "#44BFFC",  # blue (spring/summer)
        "snow":       "#44BFFC",  # blue (winter — same shade)
        "heavy_rain": "#909BF5",  # purple
        "":           "#2a2a2a",  # dark grey — no event
    }

    def _bar_color(event: str) -> str:
        for key in ("storm", "heavy_rain", "wind", "snow"):
            if key in event:
                return EVENT_COLORS[key]
        return EVENT_COLORS[""]

    bar_colors = [_bar_color(str(e)) for e in w["adverse_events"]]

    # Hover text
    hover = [
        f"<b>{row['week_start']}</b><br>"
        f"Max gust: {row['max_gust_kmh']:.0f} km/h<br>"
        f"Precip: {row['precip_mm']:.1f} mm<br>"
        f"Temp avg: {row['temp_avg_c']:.1f} °C"
        + (f"<br><b>{row['adverse_events'].upper()}</b>" if row["adverse_events"] else "")
        for _, row in w.iterrows()
    ]

    fig = go.Figure()

    # Gust bars (primary axis)
    fig.add_trace(go.Bar(
        x=w["week_start"], y=w["max_gust_kmh"],
        name="Max gust (km/h)",
        marker_color=bar_colors,
        hovertext=hover, hoverinfo="text",
        yaxis="y1",
    ))

    # Storm threshold line
    fig.add_hline(
        y=70, line_dash="dot", line_color="#FF8C00", line_width=1,
        annotation_text="storm threshold (70 km/h)",
        annotation_font=dict(family="Fragment Mono, monospace", size=8, color="#FF8C00"),
        annotation_position="top right",
    )

    # Temp line (secondary axis)
    fig.add_trace(go.Scatter(
        x=w["week_start"], y=w["temp_avg_c"],
        name="Avg temp (°C)",
        mode="lines+markers",
        line=dict(color="#81FBA5", width=1.5),
        marker=dict(size=5),
        yaxis="y2",
    ))

    fig.add_annotation(
        text="WEATHER — NETHERLANDS", xref="paper", yref="paper",
        x=0.0, y=1.08, xanchor="left", yanchor="bottom", showarrow=False,
        font=dict(family="Fragment Mono, monospace", size=9, color="#81FBA5"),
    )

    xaxis_kw = {**_AXIS_STYLE, "type": "category", "tickangle": -30,
                "tickfont": dict(family="DM Sans", size=10, color="#A2A2A2")}

    fig.update_layout(
        **_LAYOUT_BASE,
        height=280,
        barmode="overlay",
        showlegend=True,
        legend=dict(
            orientation="h", yanchor="top", y=-0.28,
            font=dict(family="DM Sans", size=11, color="#A2A2A2"),
            bgcolor="rgba(0,0,0,0)",
        ),
        xaxis=xaxis_kw,
        yaxis=dict(**_AXIS_STYLE, title_text="Max gust (km/h)"),
        yaxis2={
            **_AXIS_STYLE,
            "title_text": "Avg temp (°C)",
            "overlaying": "y",
            "side": "right",
            "showgrid": False,
        },
        margin=dict(l=50, r=60, b=80, t=40, pad=4),
    )
    return fig.to_dict()  # type: ignore[no-any-return]


# ── Input diff table ──────────────────────────────────────────────────────────

def build_input_diff_table(
    planned_df: pd.DataFrame,
    actual_df: pd.DataFrame,
    series_id: Optional[str] = None,
    selected_week: Optional[list[str]] = None,
) -> pd.DataFrame:
    """Numeric delta between planned and actual input DataFrames, sorted by |delta|."""
    date_col = app_settings.datetime_partition_column

    p = _filter_df(planned_df, series_id).copy()
    a = _filter_df(actual_df, series_id).copy()

    if selected_week:
        p = p[p[date_col].isin(selected_week)]
        a = a[a[date_col].isin(selected_week)]

    skip = {date_col, _SERIES_COL, app_settings.target, f"{app_settings.target} (actual)"}
    numeric_cols = [
        c for c in p.select_dtypes(include="number").columns
        if c not in skip and c in a.columns and not _is_immutable(c)
    ]

    if not numeric_cols:
        return pd.DataFrame(columns=["feature", "planned_avg", "actual_avg", "delta", "pct_change"])

    p_means = p[numeric_cols].mean()
    a_means = a[numeric_cols].mean()
    delta = a_means - p_means
    pct = (delta / p_means.replace(0, float("nan"))) * 100

    result = pd.DataFrame({
        "feature": numeric_cols,
        "planned_avg": p_means.values.round(1),
        "actual_avg": a_means.values.round(1),
        "delta": delta.values.round(1),
        "abs_delta": delta.abs().values.round(1),
        "pct_change": pct.values.round(1),
    })
    return result.reindex(
        result["abs_delta"].sort_values(ascending=False).index
    ).reset_index(drop=True)


# ── Feature time-series comparison chart ─────────────────────────────────────

def build_feature_timeseries_chart(
    planned_df: pd.DataFrame,
    actual_df: pd.DataFrame,
    feature: str,
    series_id: Optional[str] = None,
    selected_week: Optional[list[str]] = None,
) -> dict[str, Any]:
    """Line chart comparing planned vs actual values of a single feature over time."""
    date_col = app_settings.datetime_partition_column

    p = _filter_df(planned_df, series_id).copy()
    a = _filter_df(actual_df,  series_id).copy()

    if selected_week:
        p = p[p[date_col].isin(selected_week)]
        a = a[a[date_col].isin(selected_week)]

    p = p[[date_col, feature]].dropna(subset=[feature]).sort_values(date_col)
    a = a[[date_col, feature]].dropna(subset=[feature]).sort_values(date_col)

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=p[date_col], y=p[feature],
        mode="lines+markers", name="Planned",
        line=dict(color="#44BFFC", width=1.8, dash="dash"),
        marker=dict(color="#44BFFC", size=6),
        hovertemplate="<b>%{x}</b><br>Planned: %{y:,.2f}<extra></extra>",
    ))
    fig.add_trace(go.Scatter(
        x=a[date_col], y=a[feature],
        mode="lines+markers", name="Actual",
        line=dict(color="#909BF5", width=1.8),
        marker=dict(color="#909BF5", size=6),
        hovertemplate="<b>%{x}</b><br>Actual: %{y:,.2f}<extra></extra>",
    ))

    xaxis_kw = {
        **_AXIS_STYLE,
        "type": "category",
        "tickangle": -35,
        "tickfont": dict(family="DM Sans", size=10, color="#A2A2A2"),
    }
    fig.update_layout(
        **_LAYOUT_BASE,
        height=260,
        showlegend=True,
        legend=dict(
            orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1,
            font=dict(family="DM Sans", size=11, color="#A2A2A2"),
            bgcolor="rgba(0,0,0,0)",
        ),
        xaxis=xaxis_kw,
        yaxis=dict(**_AXIS_STYLE, title_text=feature),
        margin=dict(l=50, r=20, b=60, t=30, pad=4),
    )
    return fig.to_dict()  # type: ignore[no-any-return]


# ── LLM comparison summary ────────────────────────────────────────────────────

def get_comparison_llm_summary(
    planned_preds: list[dict],
    actual_preds: list[dict],
    planned_df: pd.DataFrame,
    actual_df: pd.DataFrame,
    series_id: Optional[str] = None,
    selected_week: Optional[list[str]] = None,
    weather_df: Optional[pd.DataFrame] = None,
    whatif_preds: Optional[list[dict]] = None,
) -> ComparisonSummary:
    """Generate LLM comparison narrative scoped to the selected series and/or week."""
    p_fc = _preds_to_fc_df(_filter_preds(planned_preds, series_id))
    a_fc = _preds_to_fc_df(_filter_preds(actual_preds, series_id))

    if selected_week:
        p_fc = p_fc[p_fc["date_id"].isin(selected_week)]
        a_fc = a_fc[a_fc["date_id"].isin(selected_week)]

    diff_table = build_input_diff_table(planned_df, actual_df, series_id, selected_week)
    top_diffs = diff_table.head(6)

    if selected_week and len(selected_week) == 1:
        scope_label = f" for week {selected_week[0]}"
    elif selected_week:
        scope_label = f" for weeks {selected_week[0]}–{selected_week[-1]}"
    else:
        scope_label = " across the full forecast horizon"
    series_label = f" (series: {series_id})" if series_id else ""

    system_prompt = (
        "You are a workforce management analyst for a Netherlands-based contact center.\n\n"
        "Context:\n"
        "- Skill group in this deployment: TECH (technical support).\n"
        "- SKILL_OFFERED_SUM is the weekly call volume offered to agents — the target being forecast.\n"
        "- The forecast is weekly (each data point = one calendar week), horizon 1–13 weeks ahead.\n"
        "- Feature importance (XEMP) values show each input's contribution to the prediction: "
        "a positive strength means the feature pushed the forecast up relative to baseline; "
        "negative means it pushed it down. Larger absolute values = stronger influence.\n"
        "- Key operational features: TECH SWAP (hardware migration campaigns), "
        "MIGRATION WITH CPE / WITHOUT CPE (broadband network migrations), "
        "NEW CUSTOMER (new activations), CHURN (cancellations), "
        "ASA (average speed of answer — a service-level proxy), "
        "ADDON (add-on service activations), MSTB (modem self-install troubleshooting).\n"
        "- Calendar features: Dutch public holidays (Kerst = Christmas, Mei = May holiday) "
        "and school seasons (Voorjaar = spring break, Zomer = summer holiday).\n"
        "- Weather: Dutch storms (wind gusts >70 km/h) and high precipitation "
        "consistently correlate with elevated TECH call volume, likely due to "
        "service outages and connection issues caused by weather damage.\n\n"
        "Be concise, specific, and use plain language suitable for a business audience."
    )

    planned_str = p_fc[["date_id", "prediction"]].to_string(index=False)
    actual_str = a_fc[["date_id", "prediction"]].to_string(index=False)
    diff_str = (
        top_diffs.to_string(index=False)
        if not top_diffs.empty
        else "No significant numeric differences found."
    )

    weather_block = ""
    if weather_df is not None and not weather_df.empty:
        w = weather_df.copy()
        if selected_week:
            date_col_w = next((c for c in w.columns if "date" in c.lower()), None)
            if date_col_w:
                w = w[w[date_col_w].isin(selected_week)]
        weather_block = f"\n\nWeather context (Netherlands){scope_label}:\n{w.to_string(index=False)}"

    whatif_block = ""
    if whatif_preds:
        wi_fc = _preds_to_fc_df(_filter_preds(whatif_preds, series_id))
        if selected_week:
            wi_fc = wi_fc[wi_fc["date_id"].isin(selected_week)]
        whatif_block = (
            f"\n\nScenario C — what-if forecast{scope_label}:\n"
            f"{wi_fc[['date_id', 'prediction']].to_string(index=False)}"
        )

    why_prompt = (
        f"Forecast A — planned inputs{scope_label}{series_label}:\n{planned_str}\n\n"
        f"Forecast B — actual inputs{scope_label}{series_label}:\n{actual_str}\n\n"
        f"Top input feature differences (planned → actual){scope_label}:\n{diff_str}"
        f"{weather_block}{whatif_block}\n\n"
        "In 3–4 sentences, explain why Forecast B differs from Forecast A. "
        "Focus on which features changed and how they affected the prediction. "
        "Do not use numbered sections or headers — write as plain prose."
    )
    why_body = _get_completion(why_prompt, system_prompt=system_prompt, temperature=0)

    insights_prompt = (
        f"Forecast A — planned inputs{scope_label}{series_label}:\n{planned_str}\n\n"
        f"Forecast B — actual inputs{scope_label}{series_label}:\n{actual_str}\n\n"
        f"Top input feature differences (planned → actual){scope_label}:\n{diff_str}"
        f"{weather_block}{whatif_block}\n\n"
        "Suggest 2–3 hypotheses about workforce demand drivers worth investigating, "
        "grounded in the feature differences and any weather context above. "
        "Write as a short bullet list. No intro sentence, no headers."
    )
    insights_body = _get_completion(insights_prompt, system_prompt=system_prompt, temperature=0)

    # Headline
    p_avg = p_fc["prediction"].mean() if len(p_fc) else 0.0
    a_avg = a_fc["prediction"].mean() if len(a_fc) else 0.0
    gap = a_avg - p_avg
    headline_prompt = (
        f"Planned forecast avg{scope_label}{series_label}: {p_avg:.0f}. "
        f"Actual forecast avg: {a_avg:.0f}. Gap: {gap:+.0f}. "
        "Write one sentence summarising this forecast gap for a business audience."
    )
    headline = _get_completion(headline_prompt, system_prompt=system_prompt, temperature=0.2)

    # Weather connection (Stage 3) — filter to selected weeks only to avoid hallucination
    weather_connection: Optional[str] = None
    if weather_df is not None and not weather_df.empty:
        w_scoped = weather_df.copy()
        if selected_week:
            date_col_w = next((c for c in w_scoped.columns if "date" in c.lower()), None)
            if date_col_w:
                w_scoped = w_scoped[w_scoped[date_col_w].isin(selected_week)]
        if not w_scoped.empty:
            w_summary = w_scoped.to_string(index=False)
            weather_conn_prompt = (
                f"Below is the EXACT weather data available for the Netherlands{scope_label}. "
                "Use ONLY the numbers in this table — do not add external knowledge, estimates, "
                "or ranges not present in the data. "
                f"The forecast gap is {gap:+.0f} calls (planned {p_avg:.0f} → actual {a_avg:.0f}). "
                "In 2 sentences, describe what the data shows about weather conditions and how "
                "those specific conditions may have contributed to TECH workload. "
                "If the data shows no severe weather (max gust below 70 km/h and low precipitation), "
                "say so — do not invent a weather explanation.\n\n"
                f"Weather data:\n{w_summary}"
            )
            weather_connection = _get_completion(
                weather_conn_prompt, system_prompt=system_prompt, temperature=0
            )

    return ComparisonSummary(
        headline=headline,
        why_forecasts_differ=why_body,
        insights_to_explore=insights_body,
        weather_connection=weather_connection,
    )
