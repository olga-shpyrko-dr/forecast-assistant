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
_ACTUALS_FILE = _find_data_file("actuals_lookup.csv")

import datarobot as dr
from forecastic.api import LLMNotAvailableException, get_app_settings, scoring_dataset_id
from forecastic.comparison_api import (
    append_scoring_week_to_cache,
    build_comparison_chart,
    build_feature_timeseries_chart,
    build_input_diff_table,
    build_weather_panel,
    build_xemp_color_map,
    build_xemp_combined,
    get_available_series,
    get_comparison_llm_summary,
    get_forecast_dates,
    get_forecast_distances,
    get_missing_weeks,
    load_from_catalog,
    run_predictions,
    update_actuals_from_scoring,
)
from forecastic.resources import ActualFeaturesDataset, PlannedFeaturesDataset

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


@st.cache_data(show_spinner=False)
def _read_actuals() -> pd.DataFrame | None:
    if not _ACTUALS_FILE.exists():
        return None
    return pd.read_csv(_ACTUALS_FILE)


@st.cache_data(ttl=300, show_spinner=False)
def _get_latest_scoring_df() -> pd.DataFrame | None:
    """Download the latest scoring dataset, cached for 5 minutes."""
    try:
        return dr.Dataset.get(scoring_dataset_id).get_as_dataframe()
    except Exception:
        return None


@st.cache_data(ttl=300, show_spinner=False)
def _get_planned_features_df() -> pd.DataFrame | None:
    try:
        dataset_id = PlannedFeaturesDataset().id
        if dataset_id:
            return dr.Dataset.get(dataset_id).get_as_dataframe()
    except Exception:
        pass
    return None


@st.cache_data(ttl=300, show_spinner=False)
def _get_actual_features_df() -> pd.DataFrame | None:
    try:
        dataset_id = ActualFeaturesDataset().id
        if dataset_id:
            return dr.Dataset.get(dataset_id).get_as_dataframe()
    except Exception:
        pass
    return None


def _load_from_cache(
    target_weeks: list[str], cache_df: pd.DataFrame
) -> tuple[list[dict], list[dict], pd.DataFrame, pd.DataFrame]:
    """Load all rows whose forecast target date (START_OF_WEEK) is in target_weeks."""
    w = cache_df[cache_df["START_OF_WEEK"].astype(str).str[:10].isin(target_weeks)]

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
        # ── Forecast period selector (by forecast TARGET weeks) ────────────
        _section_label("FORECAST PERIOD", "#81FBA5")
        cache_df = _read_cache()
        if cache_df is not None:
            # Options are forecast TARGET dates (START_OF_WEEK), not prediction points
            cached_target_weeks = sorted(
                cache_df["START_OF_WEEK"].astype(str).str[:10].unique().tolist()
            )
            # Default: end = last available week; start = ~13 weeks before that
            _default_end   = cached_target_weeks[-1]
            _horizon_start = (pd.Timestamp(_default_end) - pd.Timedelta(weeks=13)).strftime("%Y-%m-%d")
            _default_start = next((w for w in cached_target_weeks if w >= _horizon_start), cached_target_weeks[0])
            col_s, col_e = st.columns(2)
            start_sel = col_s.selectbox("From", options=cached_target_weeks,
                                        index=cached_target_weeks.index(_default_start),
                                        key="horizon_start")
            end_sel   = col_e.selectbox("To",   options=cached_target_weeks,
                                        index=cached_target_weeks.index(_default_end),
                                        key="horizon_end")
            selected_target_weeks = [w for w in cached_target_weeks if start_sel <= w <= end_sel]
            use_cache = bool(selected_target_weeks)
            if not use_cache:
                st.caption("'From' must be ≤ 'To'")
            else:
                st.caption(f"{len(selected_target_weeks)} forecast week(s)")
            st.markdown(
                "<p style='font-size:0.65rem;color:#6C6A6B;margin:2px 0 0;'>"
                "For dates outside the cache select Custom below</p>",
                unsafe_allow_html=True,
            )

            # ── New-data detection ─────────────────────────────────────────
            _scoring_df = _get_latest_scoring_df()
            _planned_features_df = _get_planned_features_df()
            _actual_features_df = _get_actual_features_df()
            if _scoring_df is not None:
                if update_actuals_from_scoring(_scoring_df, _CACHE_FILE):
                    st.cache_data.clear()
                    st.rerun()
                _missing = get_missing_weeks(cache_df, _scoring_df)
                if _missing:
                    st.info(
                        f"{len(_missing)} week(s) not yet in cache "
                        f"({_missing[0]} → {_missing[-1]})",
                        icon="🔔",
                    )
                    if st.button(
                        f"Load {len(_missing)} missing week(s)",
                        key="load_new_week_btn",
                        use_container_width=True,
                    ):
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
                with st.expander("↻ Refresh a specific week"):
                    _cached_pred_weeks = sorted(
                        cache_df["prediction_week"].astype(str).str[:10].unique(), reverse=True
                    )
                    _refresh_week = st.selectbox(
                        "Prediction week", options=_cached_pred_weeks, key="refresh_week_p2"
                    )
                    if st.button("Force refresh", key="force_refresh_btn_p2", use_container_width=True):
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

            use_live = st.checkbox("Custom (run live predictions)", key="use_live_cb")
            if use_live:
                use_cache = False
                selected_target_weeks = []
        else:
            st.caption("No cache found — will run live predictions")
            use_cache = False
            selected_target_weeks = []
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
        show_llm = st.checkbox("Show AI commentary", value=False, key="show_llm_drivers")
        run_btn = st.button("Run Comparison", type="primary", use_container_width=True)

    # ── Run predictions ───────────────────────────────────────────────────────
    if run_btn:
        if use_cache and cache_df is not None and selected_target_weeks:
            # ── Cache path: load pre-computed results ──────────────────────
            with st.spinner("Loading pre-computed forecast…"):
                planned_preds, actual_preds, planned_input_df, actual_input_df = (
                    _load_from_cache(selected_target_weeks, cache_df)
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
    actuals_df_s: pd.DataFrame | None = _read_actuals()
    available_series: list[str] = st.session_state.get("available_series", [])
    forecast_dates: list[str] = st.session_state.get("forecast_dates", [])

    # ── Pre-set selector defaults on first render / when forecast data changes ─
    _run_key = tuple(forecast_dates[:3]) if forecast_dates else ()
    if st.session_state.get("_selector_run_key") != _run_key:
        st.session_state["_selector_run_key"] = _run_key
        if forecast_dates:
            st.session_state["selected_week"] = [forecast_dates[0]]
        st.session_state["selected_distance"] = "4 weeks ahead"

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
        selected_weeks: list[str] = st.multiselect(
            "Forecast Week (for analysis of drivers)", options=forecast_dates, key="selected_week",
            placeholder="All weeks",
            help="Filter to specific forecast target dates (leave empty = show all)",
        )
        selected_week: list[str] | None = selected_weeks if selected_weeks else None

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

    # ── LLM analysis (cached per series+week+distance, only when enabled) ───────
    if "comparison_summaries" not in st.session_state:
        st.session_state["comparison_summaries"] = {}

    summary = None
    if show_llm:
        summary_key = (selected_series, tuple(sorted(selected_week)) if selected_week else None, selected_distance)
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
                except LLMNotAvailableException as e:
                    st.warning(f"AI commentary unavailable: {e}")
                    st.session_state["comparison_summaries"][summary_key] = None
        summary = st.session_state["comparison_summaries"].get(summary_key)

    # ── Overlay forecast chart ────────────────────────────────────────────────
    if selected_distance is not None and planned_preds_v:
        fp_date = str(planned_preds_v[0].get("prediction_week", ""))[:10]
        if fp_date:
            st.caption(
                f"Forecast point: **{fp_date}** — both lines show predictions made on this date, "
                f"{selected_distance} week{'s' if selected_distance > 1 else ''} ahead. "
                "Planned uses expected inputs; Actual uses retrospectively known inputs. "
                "Predictions differ across distances because each forecast point uses a different historical window."
            )
    chart_json = build_comparison_chart(
        planned_preds_v, actual_preds_v, planned_df_s,
        n_history=int(n_history),
        series_id=selected_series,
        selected_week=selected_week,
        whatif_preds=whatif_preds,
        weather_df=weather_df_s,
        actuals_df=actuals_df_s,
    )
    st.plotly_chart(go.Figure(chart_json), config=CHART_CONFIG, use_container_width=True)

    # ── Weather panel ─────────────────────────────────────────────────────────
    if weather_df_s is not None:
        weather_fig = build_weather_panel(weather_df_s, forecast_dates=forecast_dates)
        st.plotly_chart(go.Figure(weather_fig), config=CHART_CONFIG, use_container_width=True)

    # ── XEMP feature impact (combined two-subplot figure, single union legend) ─
    xemp_color_map = build_xemp_color_map(planned_preds_v, actual_preds_v, series_id=selected_series)
    xemp_fig = build_xemp_combined(
        planned_preds_v, actual_preds_v,
        series_id=selected_series,
        selected_week=selected_week,
        color_map=xemp_color_map,
    )
    st.plotly_chart(go.Figure(xemp_fig), config=CHART_CONFIG, use_container_width=True)

    # ── Input feature differences table ──────────────────────────────────────
    dist_label_str = f", distance {selected_distance}w" if selected_distance else ""
    if selected_week and len(selected_week) == 1:
        week_label_str = f"  —  Week: {selected_week[0]}{dist_label_str}"
    elif selected_week:
        week_label_str = f"  —  {len(selected_week)} weeks selected{dist_label_str}"
    else:
        week_label_str = f"  —  All weeks{dist_label_str}"
    diff_df = build_input_diff_table(
        planned_df_s, actual_df_s,
        series_id=selected_series,
        selected_week=selected_week,
    )
    with st.expander(f"Input feature differences{week_label_str}"):
        if not diff_df.empty:
            st.markdown(_diff_table_html(diff_df), unsafe_allow_html=True)
        else:
            st.write("No comparable numeric feature columns found in both files.")

    # ── Feature time-series chart ─────────────────────────────────────────────
    features_with_delta = (
        diff_df[diff_df["abs_delta"] > 0]["feature"].tolist()
        if not diff_df.empty else []
    )
    if features_with_delta:
        feat_col, _ = st.columns([2, 3])
        selected_feature = feat_col.selectbox(
            "Feature detail",
            options=features_with_delta,
            index=0,
            key="feature_detail_select",
            label_visibility="collapsed",
        )
        feat_chart = build_feature_timeseries_chart(
            planned_df_s, actual_df_s,
            feature=selected_feature,
            series_id=selected_series,
            selected_week=selected_week,
        )
        st.plotly_chart(go.Figure(feat_chart), config=CHART_CONFIG, use_container_width=True)

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

        st.write(summary.why_forecasts_differ)
        if summary.insights_to_explore:
            st.markdown(
                "<p style='font-family:\"Fragment Mono\",monospace;font-size:0.65rem;"
                "text-transform:uppercase;letter-spacing:0.08em;"
                "color:#44BFFC;margin-top:12px;margin-bottom:4px;'>Insights to Explore</p>",
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
    elif show_llm and summary is None and "comparison_summaries" in st.session_state:
        st.caption("AI analysis unavailable — check the warning above for details.")


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
