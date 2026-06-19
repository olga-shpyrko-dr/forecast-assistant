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
    """Search several candidate directories for a data file."""
    candidates = [
        _HERE.parent.parent / "data" / filename,           # repo root/data/ (local dev)
        Path("/home/notebooks/storage/forecast-assistant/data") / filename,  # DR codespace storage
        Path(os.getcwd()) / "data" / filename,             # cwd/data/
        Path("/opt/code/data") / filename,                 # deployed app container
    ]
    for p in candidates:
        if p.exists():
            return p
    return candidates[0]  # return first path even if missing (triggers "not found" message)

_CACHE_FILE   = _find_data_file("forecast_cache.csv")
_WEATHER_FILE = _find_data_file("nl_weekly_weather_2026.csv")

DEFAULT_PREDICTION_WEEKS = ["2026-04-06", "2026-04-13", "2026-04-20", "2026-04-27"]

from forecastic.api import LLMNotAvailableException, get_app_settings
from forecastic.comparison_api import (
    build_comparison_chart,
    build_input_diff_table,
    build_weather_panel,
    build_xemp_bar,
    build_xemp_color_map,
    get_available_series,
    get_comparison_llm_summary,
    get_forecast_dates,
    get_forecast_distances,
    load_from_catalog,
    run_predictions,
)

CHART_CONFIG = {"displayModeBar": False, "responsive": True}

sys.setrecursionlimit(10000)
app_settings = get_app_settings()


# ── Reusable dataset-input block ──────────────────────────────────────────────

def _dataset_input(label: str, key_prefix: str, default_catalog_id: str | None = None) -> pd.DataFrame | None:
    """Render upload / catalog-ID toggle. Returns cached DataFrame or None."""
    # Pre-initialize session state so st.text_input renders the default correctly
    catalog_key = f"{key_prefix}_catalog_id"
    mode_key = f"{key_prefix}_mode"
    if catalog_key not in st.session_state and default_catalog_id:
        st.session_state[catalog_key] = default_catalog_id
    if mode_key not in st.session_state and default_catalog_id:
        st.session_state[mode_key] = "Load from Catalog"

    mode = st.radio(
        f"{label} source",
        options=["Upload CSV", "Load from Catalog"],
        key=mode_key,
        horizontal=True,
        label_visibility="collapsed",
    )
    if mode == "Upload CSV":
        uploaded = st.file_uploader(
            f"Upload {label} CSV",
            type=["csv"],
            key=f"{key_prefix}_upload",
            label_visibility="collapsed",
        )
        if uploaded is not None:
            df = pd.read_csv(uploaded)
            st.session_state[f"{key_prefix}_df"] = df
    else:
        col_id, col_btn = st.columns([3, 1])
        dataset_id = col_id.text_input(
            "DataRobot Dataset ID",
            key=catalog_key,
            placeholder="e.g. 66a1b2c3...",
            label_visibility="collapsed",
        )
        # Auto-load on first render if a default ID is provided and not yet loaded
        auto_load = (
            default_catalog_id
            and f"{key_prefix}_df" not in st.session_state
            and f"{key_prefix}_autoloaded" not in st.session_state
        )
        if col_btn.button("Load", key=f"{key_prefix}_load_btn") or auto_load:
            if dataset_id.strip():
                try:
                    with st.spinner("Loading from Catalog…"):
                        df = load_from_catalog(dataset_id.strip())
                    st.session_state[f"{key_prefix}_df"] = df
                    st.session_state[f"{key_prefix}_autoloaded"] = True
                    st.success(f"Loaded {len(df):,} rows")
                except Exception as e:
                    st.session_state[f"{key_prefix}_autoloaded"] = True  # don't retry on error
                    st.error(f"Failed to load: {e}")

    cached = st.session_state.get(f"{key_prefix}_df")
    if cached is not None:
        st.caption(f"{len(cached):,} rows loaded")
    return cached


# ── Cache helpers ─────────────────────────────────────────────────────────────

@st.cache_data(show_spinner=False)
def _read_cache() -> pd.DataFrame | None:
    if not _CACHE_FILE.exists():
        return None
    return pd.read_csv(_CACHE_FILE)


@st.cache_data(show_spinner=False)
def _read_weather() -> pd.DataFrame | None:
    if not _WEATHER_FILE.exists():
        return None
    return pd.read_csv(_WEATHER_FILE)


def _load_from_cache(
    prediction_weeks: list[str], cache_df: pd.DataFrame
) -> tuple[list[dict], list[dict], pd.DataFrame, pd.DataFrame]:
    """Load all prediction weeks in the range; returns union of their forecast dates."""
    w = cache_df[cache_df["prediction_week"].isin(prediction_weeks)]

    meta_cols = {"prediction_week", "scenario", "forecast_step"}
    pred_cols = {c for c in cache_df.columns if "PREDICTION" in c.upper() or "PERCENTILE" in c.upper() or "EXPLANATION" in c.upper()}
    input_feature_cols = [c for c in cache_df.columns if c not in meta_cols and c not in pred_cols]

    planned_recs = (
        w[w["scenario"] == "planned"].drop(columns=["scenario"], errors="ignore").to_dict("records")
    )
    actual_recs = (
        w[w["scenario"] == "actual"].drop(columns=["scenario"], errors="ignore").to_dict("records")
    )
    planned_input_df = w[w["scenario"] == "planned"][input_feature_cols].copy()
    actual_input_df  = w[w["scenario"] == "actual"][input_feature_cols].copy()
    return planned_recs, actual_recs, planned_input_df, actual_input_df


# ── Main page ─────────────────────────────────────────────────────────────────

def feature_comparison_page() -> None:
    # ── Header ────────────────────────────────────────────────────────────────
    with st.container(key="dr-logo-comparison"):
        logo_col, title_col = st.columns([1, 4])
        with logo_col:
            st.image("./DataRobot_white.svg", width=160)
        with title_col:
            st.markdown(
                """
                <p style='font-family:"Fragment Mono",monospace;font-size:0.7rem;
                    text-transform:uppercase;letter-spacing:0.1em;
                    color:#81FBA5;margin-bottom:2px;'>FORECAST COMPARISON</p>
                <h1 style='font-family:"DM Sans",sans-serif;font-weight:500;
                    font-size:1.5rem;color:#FFFFFF;margin:0;letter-spacing:-0.01em;'>
                    Planned vs Actual Feature Impact</h1>
                """,
                unsafe_allow_html=True,
            )
    st.markdown(
        "<hr style='border:none;border-top:1px solid #1e1e1e;margin:12px 0 20px;'/>",
        unsafe_allow_html=True,
    )

    # ── Sidebar ───────────────────────────────────────────────────────────────
    with st.sidebar:
        # ── Forecast horizon selector ──────────────────────────────────────
        _section_label("FORECAST HORIZON", "#81FBA5")
        cache_df = _read_cache()
        if cache_df is not None:
            cached_weeks = sorted(cache_df["prediction_week"].unique().tolist())
            col_s, col_e = st.columns(2)
            start_sel = col_s.selectbox("Start week", options=cached_weeks,
                                        index=0, key="horizon_start")
            end_sel   = col_e.selectbox("End week",   options=cached_weeks,
                                        index=len(cached_weeks) - 1, key="horizon_end")
            # Weeks in the selected range that exist in the cache
            selected_weeks = [w for w in cached_weeks if start_sel <= w <= end_sel]
            use_cache = bool(selected_weeks)
            if not use_cache:
                st.caption("Start must be ≤ End week")
            else:
                st.caption(f"{len(selected_weeks)} prediction week(s) · {len(selected_weeks) + 12} forecast dates")
            st.markdown(
                "<p style='font-size:0.65rem;color:#6C6A6B;margin:2px 0 0;'>"
                "For other dates select Custom below</p>",
                unsafe_allow_html=True,
            )
            use_live = st.checkbox("Custom (run live predictions)", key="use_live_cb")
            if use_live:
                use_cache = False
                selected_weeks = []
        else:
            st.caption("No cache found — will run live predictions")
            use_cache = False
            selected_weeks = []
            use_live = True

        _divider()

        # ── Dataset upload (only shown for live / custom mode) ─────────────
        if not use_cache:
            _section_label("PLANNED FEATURES", "#81FBA5")
            planned_df = _dataset_input("Planned Features", "planned", default_catalog_id="6a326e0a76da3420b0d4e6e1")

            _divider()
            _section_label("ACTUAL FEATURES", "#81FBA5")
            actual_df = _dataset_input("Actual Features", "actual", default_catalog_id="6a326f4d347b28ea2e55f572")

            _divider()
            _section_label("WHAT-IF SCENARIO  (optional)", "#909BF5")
            whatif_df = _dataset_input("What-If", "whatif")

            _divider()
            _section_label("WEATHER DATA  (optional)", "#44BFFC")
            weather_file = st.file_uploader(
                "Upload Netherlands weather CSV",
                type=["csv"],
                key="weather_upload",
                label_visibility="collapsed",
                help="Expected columns: date, temp_avg_c, precipitation_mm, storm_flag",
            )
            if weather_file is not None:
                st.session_state["weather_df"] = pd.read_csv(weather_file)
            if st.session_state.get("weather_df") is not None:
                st.caption(f"{len(st.session_state['weather_df']):,} weather rows loaded")
        else:
            planned_df = None
            actual_df = None
            whatif_df = None

        _divider()
        n_history = st.number_input(
            "Historical records to show",
            min_value=10, max_value=200,
            value=min(52, app_settings.maximum_default_display_length),
            step=4,
        )
        run_btn = st.button("Run Comparison", type="primary", use_container_width=True)

    # ── Run predictions ───────────────────────────────────────────────────────
    if run_btn:
        if use_cache and cache_df is not None and selected_weeks:
            # ── Cache path: load pre-computed results ──────────────────────
            with st.spinner("Loading pre-computed forecast…"):
                planned_preds, actual_preds, planned_input_df, actual_input_df = (
                    _load_from_cache(selected_weeks, cache_df)
                )
            whatif_preds = None
            st.session_state["planned_preds"] = planned_preds
            st.session_state["actual_preds"] = actual_preds
            st.session_state["planned_df"] = planned_input_df
            st.session_state["actual_df"] = actual_input_df
            st.session_state["whatif_preds"] = whatif_preds
            st.session_state["available_series"] = get_available_series(planned_input_df, actual_input_df)
            st.session_state["forecast_dates"] = get_forecast_dates(planned_preds, actual_preds)
            st.session_state["comparison_summaries"] = {}
        else:
            # ── Live path: call DR prediction API ─────────────────────────
            if planned_df is None or actual_df is None:
                st.warning("Please load both **Planned** and **Actual** feature datasets before running.")
                st.stop()

            with st.spinner("Running forecasts for both scenarios…"):
                try:
                    planned_preds = run_predictions(planned_df)
                    actual_preds = run_predictions(actual_df)
                except Exception as e:
                    st.error(f"Prediction failed: {e}")
                    st.stop()

            whatif_preds = None
            if whatif_df is not None:
                with st.spinner("Running what-if forecast…"):
                    try:
                        whatif_preds = run_predictions(whatif_df)
                    except Exception as e:
                        st.warning(f"What-if prediction failed (skipped): {e}")

            st.session_state["planned_preds"] = planned_preds
            st.session_state["actual_preds"] = actual_preds
            st.session_state["planned_df"] = planned_df
            st.session_state["actual_df"] = actual_df
            st.session_state["whatif_preds"] = whatif_preds
            st.session_state["available_series"] = get_available_series(planned_df, actual_df)
            st.session_state["forecast_dates"] = get_forecast_dates(planned_preds, actual_preds)
            st.session_state["comparison_summaries"] = {}

    # ── Guard: nothing loaded yet ─────────────────────────────────────────────
    if "planned_preds" not in st.session_state:
        st.info(
            "Load your **Planned** and **Actual** feature files in the sidebar, "
            "then click **Run Comparison**."
        )
        return

    planned_preds = st.session_state["planned_preds"]
    actual_preds = st.session_state["actual_preds"]
    planned_df_s: pd.DataFrame = st.session_state["planned_df"]
    actual_df_s: pd.DataFrame = st.session_state["actual_df"]
    whatif_preds = st.session_state.get("whatif_preds")
    # Pre-loaded weather — falls back to user-uploaded if file not on disk
    weather_df_s: pd.DataFrame | None = _read_weather()
    if weather_df_s is None:
        weather_df_s = st.session_state.get("weather_df")
    available_series: list[str] = st.session_state.get("available_series", [])
    forecast_dates: list[str] = st.session_state.get("forecast_dates", [])

    # ── Selectors (series + forecast week + forecast distance) ───────────────
    sel_col1, sel_col2, sel_col3 = st.columns([1, 2, 2])
    with sel_col1:
        if available_series:
            if st.session_state.get("_series_options") != available_series:
                st.session_state["_series_options"] = available_series
                st.session_state["selected_series"] = available_series[0]
            selected_series: str | None = st.selectbox(
                "Series ID", options=available_series, key="selected_series"
            )
        else:
            selected_series = None
            st.caption("No multiseries ID column found")

    with sel_col2:
        week_options = ["All weeks"] + forecast_dates
        selected_week_label: str = st.selectbox(
            "Forecast Week", options=week_options, key="selected_week",
            help="The future date being forecasted",
        )
        selected_week: str | None = (
            None if selected_week_label == "All weeks" else selected_week_label
        )

    with sel_col3:
        distances = get_forecast_distances(planned_preds)
        if distances:
            dist_labels = ["All distances"] + [
                f"{d} week{'s' if d > 1 else ''} ahead" for d in distances
            ]
            selected_dist_label: str = st.selectbox(
                "Forecast Distance", options=dist_labels, key="selected_distance",
                help="How far in advance the forecast was made",
            )
            selected_distance: int | None = (
                None if selected_dist_label == "All distances"
                else int(selected_dist_label.split()[0])
            )
        else:
            selected_distance = None
            st.caption("forecast_step not in data")

    # Apply forecast distance filter before charts (pre-filter to avoid changing all chart APIs)
    if selected_distance is not None:
        planned_preds_v = [r for r in planned_preds if int(r.get("forecast_step", -1)) == selected_distance]
        actual_preds_v  = [r for r in actual_preds  if int(r.get("forecast_step", -1)) == selected_distance]
    else:
        planned_preds_v = planned_preds
        actual_preds_v  = actual_preds

    # ── LLM analysis (cached per series+week+distance) ───────────────────────
    if "comparison_summaries" not in st.session_state:
        st.session_state["comparison_summaries"] = {}

    summary_key = (selected_series, selected_week, selected_distance)
    if summary_key not in st.session_state["comparison_summaries"]:
        with st.spinner("Generating AI comparison analysis…"):
            try:
                summary = get_comparison_llm_summary(
                    planned_preds_v, actual_preds_v,
                    planned_df_s, actual_df_s,
                    series_id=selected_series,
                    selected_week=selected_week,
                    weather_df=weather_df_s,
                    whatif_preds=whatif_preds,
                )
                st.session_state["comparison_summaries"][summary_key] = summary
            except LLMNotAvailableException:
                st.session_state["comparison_summaries"][summary_key] = None

    summary = st.session_state["comparison_summaries"].get(summary_key)

    # ── Overlay forecast chart ────────────────────────────────────────────────
    chart_json = build_comparison_chart(
        planned_preds_v, actual_preds_v, planned_df_s,
        n_history=int(n_history),
        series_id=selected_series,
        selected_week=selected_week,
        whatif_preds=whatif_preds,
        weather_df=weather_df_s,
    )
    st.plotly_chart(go.Figure(chart_json), config=CHART_CONFIG, use_container_width=True)

    # ── Weather panel ─────────────────────────────────────────────────────────
    if weather_df_s is not None:
        weather_fig = build_weather_panel(weather_df_s, forecast_dates=forecast_dates)
        st.plotly_chart(go.Figure(weather_fig), config=CHART_CONFIG, use_container_width=True)

    # ── XEMP feature impact side by side ─────────────────────────────────────
    xemp_color_map = build_xemp_color_map(planned_preds_v, actual_preds_v, series_id=selected_series)
    xemp_col1, xemp_col2 = st.columns(2)
    with xemp_col1:
        xemp_planned = build_xemp_bar(
            planned_preds_v, series_id=selected_series,
            selected_week=selected_week, label="Planned",
            color_map=xemp_color_map,
        )
        st.plotly_chart(go.Figure(xemp_planned), config=CHART_CONFIG, use_container_width=True)
    with xemp_col2:
        xemp_actual = build_xemp_bar(
            actual_preds_v, series_id=selected_series,
            selected_week=selected_week, label="Actual",
            color_map=xemp_color_map,
        )
        st.plotly_chart(go.Figure(xemp_actual), config=CHART_CONFIG, use_container_width=True)

    # ── Input feature differences table ──────────────────────────────────────
    dist_label_str = f", distance {selected_distance}w" if selected_distance else ""
    week_label_str = (f"  —  Week: {selected_week}{dist_label_str}" if selected_week
                      else f"  —  All weeks{dist_label_str}")
    with st.expander(f"Input feature differences{week_label_str}"):
        diff_df = build_input_diff_table(
            planned_df_s, actual_df_s,
            series_id=selected_series,
            selected_week=selected_week,
        )
        if not diff_df.empty:
            st.markdown(_diff_table_html(diff_df), unsafe_allow_html=True)
        else:
            st.write("No comparable numeric feature columns found in both files.")

    # ── AI Analysis ───────────────────────────────────────────────────────────
    if summary:
        st.markdown(
            "<p style='font-family:\"Fragment Mono\",monospace;font-size:0.7rem;"
            "text-transform:uppercase;letter-spacing:0.1em;"
            "color:#81FBA5;margin-bottom:6px;margin-top:28px;'>AI GENERATED ANALYSIS</p>",
            unsafe_allow_html=True,
        )
        st.markdown(
            f"<p style='font-family:\"DM Sans\",sans-serif;font-size:1rem;font-weight:500;"
            f"color:#FFFF54;margin-bottom:14px;'>{summary.headline}</p>",
            unsafe_allow_html=True,
        )

        why_col, insights_col = st.columns(2)
        with why_col:
            st.markdown(
                "<p style='font-family:\"Fragment Mono\",monospace;font-size:0.65rem;"
                "text-transform:uppercase;letter-spacing:0.08em;"
                "color:#909BF5;margin-bottom:4px;'>Why Forecasts Differ</p>",
                unsafe_allow_html=True,
            )
            st.write(summary.why_forecasts_differ)
        with insights_col:
            st.markdown(
                "<p style='font-family:\"Fragment Mono\",monospace;font-size:0.65rem;"
                "text-transform:uppercase;letter-spacing:0.08em;"
                "color:#44BFFC;margin-bottom:4px;'>Insights to Explore</p>",
                unsafe_allow_html=True,
            )
            st.write(summary.insights_to_explore)

        if summary.weather_connection:
            st.markdown(
                "<p style='font-family:\"Fragment Mono\",monospace;font-size:0.65rem;"
                "text-transform:uppercase;letter-spacing:0.08em;"
                "color:#FFFF54;margin-top:16px;margin-bottom:4px;'>Weather Connection</p>",
                unsafe_allow_html=True,
            )
            st.write(summary.weather_connection)
    elif summary is None and "comparison_summaries" in st.session_state:
        st.caption("AI analysis unavailable — LLM deployment not configured.")


# ── Diff table HTML renderer ──────────────────────────────────────────────────

def _diff_table_html(diff_df: pd.DataFrame) -> str:
    """Render the input feature diff table as HTML with sign-colored bars."""
    max_abs = float(diff_df["abs_delta"].max()) or 1.0
    _th = (
        "padding:5px 10px;color:#6C6A6B;"
        "font-family:'Fragment Mono',monospace;font-size:0.62rem;"
        "text-transform:uppercase;letter-spacing:0.08em;font-weight:normal;"
        "border-bottom:1px solid #2a2a2a;"
    )
    rows_html = []
    for _, row in diff_df.iterrows():
        bar_w = min(row["abs_delta"] / max_abs * 100, 100)
        color = "#81FBA5" if float(row["delta"]) >= 0 else "#FF4B4B"
        sign = "+" if float(row["delta"]) > 0 else ""
        pct_str = f"{row['pct_change']:+.1f}%" if pd.notna(row["pct_change"]) else "—"
        rows_html.append(
            f"<tr>"
            f"<td style='padding:4px 10px;color:#E4E4E4;font-family:DM Sans,sans-serif;font-size:0.82rem;'>{row['feature']}</td>"
            f"<td style='padding:4px 10px;color:#A2A2A2;text-align:right;font-size:0.82rem;'>{row['planned_avg']:,.0f}</td>"
            f"<td style='padding:4px 10px;color:#A2A2A2;text-align:right;font-size:0.82rem;'>{row['actual_avg']:,.0f}</td>"
            f"<td style='padding:4px 10px;color:{color};text-align:right;font-size:0.82rem;font-weight:500;white-space:nowrap;'>{sign}{row['delta']:,.0f}</td>"
            f"<td style='padding:4px 10px;width:180px;'>"
            f"  <div style='background:{color};width:{bar_w:.1f}%;height:9px;border-radius:2px;min-width:3px;'></div>"
            f"</td>"
            f"<td style='padding:4px 10px;color:{color};text-align:right;font-size:0.82rem;'>{pct_str}</td>"
            f"</tr>"
        )
    return (
        "<table style='width:100%;border-collapse:collapse;'>"
        "<thead><tr>"
        f"<th style='{_th}text-align:left;'>Feature</th>"
        f"<th style='{_th}text-align:right;'>Planned avg</th>"
        f"<th style='{_th}text-align:right;'>Actual avg</th>"
        f"<th style='{_th}text-align:right;'>Delta</th>"
        f"<th style='{_th}'>Magnitude</th>"
        f"<th style='{_th}text-align:right;'>% Change</th>"
        "</tr></thead>"
        f"<tbody>{''.join(rows_html)}</tbody>"
        "</table>"
    )


# ── Small styling helpers ──────────────────────────────────────────────────────

def _section_label(text: str, color: str = "#81FBA5") -> None:
    st.markdown(
        f"<p style='font-family:\"Fragment Mono\",monospace;font-size:0.65rem;"
        f"text-transform:uppercase;letter-spacing:0.1em;color:{color};"
        f"margin-bottom:4px;margin-top:2px;'>{text}</p>",
        unsafe_allow_html=True,
    )


def _divider() -> None:
    st.markdown(
        "<hr style='border:none;border-top:1px solid #1e1e1e;margin:10px 0;'/>",
        unsafe_allow_html=True,
    )


# ── Entry point ───────────────────────────────────────────────────────────────

def _main() -> None:
    st.markdown(
        "<style>#MainMenu{visibility:hidden;}header{visibility:hidden;}"
        "footer{visibility:hidden;}</style>",
        unsafe_allow_html=True,
    )
    feature_comparison_page()


if __name__ == "__main__":
    _main()
