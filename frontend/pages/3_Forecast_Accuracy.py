import os
import sys
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

sys.path.append("..")

# ── Data file path resolution ─────────────────────────────────────────────────
_HERE = Path(__file__).parent


def _find_data_file(filename: str) -> Path:
    candidates = [
        _HERE.parent.parent / "data" / filename,
        Path("/home/notebooks/storage/forecast-assistant/data") / filename,
        Path(os.getcwd()) / "data" / filename,
        Path("/opt/code/data") / filename,
    ]
    for p in candidates:
        if p.exists():
            return p
    return candidates[0]


_CACHE_FILE = _find_data_file("forecast_cache.csv")
_ACTUALS_FILE = _find_data_file("actuals_lookup.csv")

from forecastic.accuracy_api import (
    build_accuracy_color_map,
    build_accuracy_line_chart,
    build_xemp_by_distance,
    filter_to_week,
    get_accuracy_llm_summary,
    get_actual_value,
    get_target_weeks,
    load_accuracy_data,
)
import datarobot as dr
from forecastic.api import LLMNotAvailableException, get_app_settings, scoring_dataset_id
from forecastic.comparison_api import append_scoring_week_to_cache, get_missing_weeks, update_actuals_from_scoring
from forecastic.resources import ActualFeaturesDataset, PlannedFeaturesDataset

CHART_CONFIG = {"displayModeBar": False, "responsive": True}

sys.setrecursionlimit(10000)
app_settings = get_app_settings()


@st.cache_data(show_spinner=False)
def _load_data():
    if not _CACHE_FILE.exists():
        return None, None, None
    planned_df, actual_inputs_df, actuals_df = load_accuracy_data(_CACHE_FILE, _ACTUALS_FILE)
    return planned_df, actual_inputs_df, actuals_df


@st.cache_data(ttl=300, show_spinner=False)
def _get_latest_scoring_df() -> "pd.DataFrame | None":
    """Download the latest scoring dataset, cached for 5 minutes."""
    try:
        return dr.Dataset.get(scoring_dataset_id).get_as_dataframe()
    except Exception:
        return None


@st.cache_data(ttl=300, show_spinner=False)
def _get_planned_features_df() -> "pd.DataFrame | None":
    try:
        dataset_id = PlannedFeaturesDataset().id
        if dataset_id:
            return dr.Dataset.get(dataset_id).get_as_dataframe()
    except Exception:
        pass
    return None


@st.cache_data(ttl=300, show_spinner=False)
def _get_actual_features_df() -> "pd.DataFrame | None":
    try:
        dataset_id = ActualFeaturesDataset().id
        if dataset_id:
            return dr.Dataset.get(dataset_id).get_as_dataframe()
    except Exception:
        pass
    return None


# ── Page header ───────────────────────────────────────────────────────────────

st.markdown(
    """
    <p style='font-family:"Fragment Mono",monospace;font-size:0.7rem;
        text-transform:uppercase;letter-spacing:0.1em;
        color:#81FBA5;margin-bottom:2px;'>FORECAST ACCURACY</p>
    <h1 style='font-family:"DM Sans",sans-serif;font-weight:500;
        font-size:1.5rem;color:#FFFFFF;margin:0 0 4px 0;'>
        How the forecast evolves as the target week approaches</h1>
    <p style='font-family:"DM Sans",sans-serif;font-size:0.85rem;color:#A2A2A2;margin:0 0 20px 0;'>
        Select a target week to see how the planned forecast changes at each distance
        (13 weeks ahead → 1 week ahead) and how it compares to the actual call volume.</p>
    """,
    unsafe_allow_html=True,
)

planned_df, actual_inputs_df, actuals_df = _load_data()

if planned_df is None:
    st.error(f"Forecast cache not found at `{_CACHE_FILE}`. Run the cache preparation notebook first.")
    st.stop()

# Use planned_df as the primary cache for week/distance discovery
cache_df = planned_df

# ── New-data detection ────────────────────────────────────────────────────────
_scoring_df = _get_latest_scoring_df()
_planned_features_df = _get_planned_features_df()
_actual_features_df = _get_actual_features_df()

if _scoring_df is not None:
    if update_actuals_from_scoring(_scoring_df, _CACHE_FILE):
        st.cache_data.clear()
        st.rerun()
    _missing = get_missing_weeks(cache_df, _scoring_df)
    if _missing:
        col_info, col_btn = st.columns([5, 2])
        col_info.info(
            f"{len(_missing)} week(s) not yet in cache "
            f"({_missing[0]} → {_missing[-1]})",
            icon="🔔",
        )
        if col_btn.button(f"Load {len(_missing)} missing week(s)", use_container_width=True):
            with st.spinner(f"Generating forecasts for {len(_missing)} week(s)…"):
                try:
                    for _week in _missing:
                        append_scoring_week_to_cache(
                            _scoring_df, _CACHE_FILE, _week,
                            planned_features_df=_planned_features_df,
                            actual_features_df=_actual_features_df,
                        )
                    st.cache_data.clear()
                    st.rerun()
                except Exception as _e:
                    st.error(f"Failed: {_e}")

if _scoring_df is not None:
    with st.expander("↻ Refresh a specific week with latest data"):
        _cached_weeks = sorted(cache_df["prediction_week"].astype(str).str[:10].unique(), reverse=True)
        _refresh_week = st.selectbox("Prediction week to refresh", options=_cached_weeks, key="refresh_week_p3")
        if st.button("Force refresh", key="force_refresh_btn_p3", use_container_width=True):
            with st.spinner(f"Re-running predictions for {_refresh_week}…"):
                try:
                    append_scoring_week_to_cache(
                        _scoring_df, _CACHE_FILE, _refresh_week, force=True,
                        planned_features_df=_planned_features_df,
                        actual_features_df=_actual_features_df,
                    )
                    st.cache_data.clear()
                    st.rerun()
                except Exception as _e:
                    st.error(f"Failed: {_e}")

target_weeks = get_target_weeks(cache_df)
if not target_weeks:
    st.warning("No target weeks with multiple forecast distances found in the cache.")
    st.stop()

# Sort: weeks with observed actuals first (most recent first), future weeks after.
# This ensures the default selection is a meaningful week for accuracy analysis.
if actuals_df is not None and not actuals_df.empty:
    _actual_week_set = set(actuals_df["START_OF_WEEK"].astype(str).str[:10].unique())
    target_weeks = (
        sorted([w for w in target_weeks if w in _actual_week_set], reverse=True)
        + sorted([w for w in target_weeks if w not in _actual_week_set], reverse=True)
    )

all_distances = sorted(cache_df["FORECAST_DISTANCE"].dropna().astype(int).unique(), reverse=True)

# ── Selectors ─────────────────────────────────────────────────────────────────

col_week, col_series, col_overlay = st.columns([3, 2, 3])
with col_week:
    target_week = st.selectbox("Target week", options=target_weeks, index=0, key="target_week_sel")
with col_series:
    st.selectbox("Series ID", options=["TECH"], index=0, disabled=True)
with col_overlay:
    show_actual_inputs = st.checkbox("Show actual inputs overlay", value=False)

default_distances = [d for d in [13, 8, 5, 1] if d in all_distances]

col_dist, col_all = st.columns([5, 2])
with col_dist:
    selected_distances = st.multiselect(
        "Forecast chart distances",
        options=all_distances,
        default=default_distances,
        format_func=lambda d: f"FD {d}",
        key="line_distances",
    )
with col_all:
    st.write("")  # vertical alignment nudge
    show_all = st.checkbox("Show all on forecast chart", value=False)
if show_all:
    selected_distances = all_distances

# ── Data for selected week ────────────────────────────────────────────────────

week_df = filter_to_week(cache_df, target_week)
actual_value = get_actual_value(actuals_df, target_week) if actuals_df is not None else None
actual_inputs_week_df = (
    filter_to_week(actual_inputs_df, target_week)
    if show_actual_inputs and actual_inputs_df is not None and not actual_inputs_df.empty
    else None
)

# Show forecast point date range as context
available_in_week = sorted(week_df["FORECAST_DISTANCE"].astype(int).unique(), reverse=True)
if available_in_week:
    fd_max = max(available_in_week)
    fd_min = min(available_in_week)
    fp_far = week_df[week_df["FORECAST_DISTANCE"].astype(int) == fd_max]["prediction_week"].iloc[0]
    fp_near = week_df[week_df["FORECAST_DISTANCE"].astype(int) == fd_min]["prediction_week"].iloc[0]
    st.caption(
        f"Forecast coverage: FD {fd_max} (made on **{fp_far}**) → FD {fd_min} (made on **{fp_near}**). "
        f"{len(available_in_week)} forecast point(s) available for week **{target_week}**."
    )

# ── Chart 1: Accuracy line ────────────────────────────────────────────────────

st.markdown(
    "<p style='font-family:\"Fragment Mono\",monospace;font-size:0.65rem;"
    "text-transform:uppercase;letter-spacing:0.08em;color:#44BFFC;"
    "margin:24px 0 4px 0;'>FORECAST VALUE BY DISTANCE</p>",
    unsafe_allow_html=True,
)

chart1 = build_accuracy_line_chart(
    week_df=week_df,
    actual_value=actual_value,
    selected_distances=selected_distances,
    show_all=show_all,
    actual_inputs_week_df=actual_inputs_week_df,
)
st.plotly_chart(go.Figure(chart1), config=CHART_CONFIG, use_container_width=True)

# ── Chart 2: XEMP by distance ─────────────────────────────────────────────────

st.markdown(
    "<p style='font-family:\"Fragment Mono\",monospace;font-size:0.65rem;"
    "text-transform:uppercase;letter-spacing:0.08em;color:#909BF5;"
    "margin:24px 0 4px 0;'>FEATURE DRIVERS BY DISTANCE</p>",
    unsafe_allow_html=True,
)
xemp_selected = st.multiselect(
    "Show distances",
    options=all_distances,
    default=default_distances,
    format_func=lambda d: f"FD {d}",
    key="xemp_distances",
)
active_xemp = [d for d in xemp_selected if d in available_in_week]

if active_xemp:
    combined_for_colors = (
        pd.concat([week_df, actual_inputs_week_df], ignore_index=True)
        if actual_inputs_week_df is not None and not actual_inputs_week_df.empty
        else week_df
    )
    color_map = build_accuracy_color_map(combined_for_colors)
    chart2 = build_xemp_by_distance(
        week_df=week_df,
        selected_distances=active_xemp,
        color_map=color_map,
        actual_week_df=actual_inputs_week_df,
    )
    st.plotly_chart(go.Figure(chart2), config=CHART_CONFIG, use_container_width=True)

# ── AI Analysis ───────────────────────────────────────────────────────────────

st.divider()
show_llm = st.checkbox("Show AI commentary", value=False, key="show_llm_accuracy")

if show_llm:
    with st.spinner("Generating AI analysis…"):
        try:
            summary = get_accuracy_llm_summary(
                week_df=week_df,
                actual_value=actual_value,
                target_week=target_week,
                what_if_features=app_settings.what_if_features,
            )

            st.markdown(
                f"<p style='font-family:\"DM Sans\",sans-serif;font-size:1.1rem;"
                f"font-weight:600;color:#FFFF54;margin:8px 0 16px 0;'>{summary.headline}</p>",
                unsafe_allow_html=True,
            )

            st.markdown(
                "<p style='font-family:\"Fragment Mono\",monospace;font-size:0.65rem;"
                "text-transform:uppercase;letter-spacing:0.08em;color:#81FBA5;"
                "margin:0 0 6px 0;'>Accuracy across distances</p>",
                unsafe_allow_html=True,
            )
            st.write(summary.accuracy_narrative)

            if summary.mitigation_actions:
                st.markdown(
                    "<p style='font-family:\"Fragment Mono\",monospace;font-size:0.65rem;"
                    "text-transform:uppercase;letter-spacing:0.08em;color:#44BFFC;"
                    "margin:16px 0 6px 0;'>Mitigation actions</p>",
                    unsafe_allow_html=True,
                )
                st.write(summary.mitigation_actions)

        except LLMNotAvailableException as e:
            st.warning(f"AI commentary unavailable: {e}")
