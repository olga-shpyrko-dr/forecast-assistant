import os
import sys
from pathlib import Path

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
from forecastic.api import LLMNotAvailableException, get_app_settings

CHART_CONFIG = {"displayModeBar": False, "responsive": True}

sys.setrecursionlimit(10000)
app_settings = get_app_settings()


@st.cache_data(show_spinner=False)
def _load_data():
    if not _CACHE_FILE.exists():
        return None, None
    cache_df, actuals_df = load_accuracy_data(_CACHE_FILE, _ACTUALS_FILE)
    return cache_df, actuals_df


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

cache_df, actuals_df = _load_data()

if cache_df is None:
    st.error(f"Forecast cache not found at `{_CACHE_FILE}`. Run the cache preparation notebook first.")
    st.stop()

target_weeks = get_target_weeks(cache_df, actuals_df)
if not target_weeks:
    # Fall back to all weeks with ≥2 distances if no actuals overlap
    target_weeks = get_target_weeks(cache_df)
if not target_weeks:
    st.warning("No target weeks with multiple forecast distances found in the cache.")
    st.stop()

all_distances = sorted(cache_df["FORECAST_DISTANCE"].dropna().astype(int).unique(), reverse=True)

# ── Selectors ─────────────────────────────────────────────────────────────────

col_week, col_series, col_all = st.columns([3, 2, 2])
with col_week:
    target_week = st.selectbox("Target week", options=target_weeks, index=0)
with col_series:
    st.selectbox("Series ID", options=["TECH"], index=0, disabled=True)
with col_all:
    show_all = st.checkbox("Show all distances", value=False)

default_distances = [d for d in [13, 8, 5, 1] if d in all_distances]
selected_distances = st.multiselect(
    "Show forecast distances",
    options=all_distances,
    default=default_distances,
    format_func=lambda d: f"FD {d}",
    disabled=show_all,
)
if show_all:
    selected_distances = all_distances

# ── Data for selected week ────────────────────────────────────────────────────

week_df = filter_to_week(cache_df, target_week)
actual_value = get_actual_value(actuals_df, target_week) if actuals_df is not None else None

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
)
st.plotly_chart(go.Figure(chart1), config=CHART_CONFIG, use_container_width=True)

# ── Chart 2: XEMP by distance ─────────────────────────────────────────────────

xemp_distances = selected_distances if not show_all else available_in_week
active_xemp = [d for d in xemp_distances if d in available_in_week]

if active_xemp:
    st.markdown(
        "<p style='font-family:\"Fragment Mono\",monospace;font-size:0.65rem;"
        "text-transform:uppercase;letter-spacing:0.08em;color:#909BF5;"
        "margin:24px 0 4px 0;'>FEATURE DRIVERS BY DISTANCE</p>",
        unsafe_allow_html=True,
    )
    color_map = build_accuracy_color_map(week_df)
    chart2 = build_xemp_by_distance(
        week_df=week_df,
        selected_distances=active_xemp,
        color_map=color_map,
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
