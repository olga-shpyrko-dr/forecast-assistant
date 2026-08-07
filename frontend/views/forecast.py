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
"""The main Forecast page: chart, AI commentary, and permutation-based explanations
(feature picker, full-forecast/single-date tables, per-feature drill-down)."""

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

# forecastic and common are importable because app.py (the process entry point)
# already put the repo root and frontend/ on sys.path before importing this module.
from common import CHART_CONFIG, app_settings, series_narrative_key

from forecastic.api import (
    get_forecast_as_plotly_json,
    get_permutation_detail_df,
    get_permutation_single_date_df,
    get_permutation_summary_df,
    get_pred_ex_stacked_bar_df,
    is_llm_commentary_available,
    predictions_for_display_series,
)
from forecastic.i18n import gettext

MAX_SELECTED_FEATURES = 15


def _selected_row_feature(event, df):
    if event is None or not getattr(event, "selection", None):
        return None
    rows = event.selection.rows
    if not rows:
        return None
    return df.iloc[rows[0]]["feature"]


@st.dialog("Permutation-based values over time")
def _show_drill_down(feature: str, series_predictions: list[dict]) -> None:
    detail_df = get_permutation_detail_df(series_predictions, feature)
    if detail_df.empty:
        st.info(gettext("No detail available for this feature."))
        return

    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=detail_df["date_id"],
            y=detail_df["prediction"],
            mode="lines+markers",
            name=gettext("Forecast"),
            line=dict(color="#44BFFC", width=1.5),
        )
    )
    fig.update_layout(
        height=220,
        margin=dict(l=40, r=20, t=30, b=20),
        plot_bgcolor="#111111",
        paper_bgcolor="#0B0B0B",
        font=dict(family="DM Sans", color="#E4E4E4"),
        title=gettext("Forecast"),
    )
    st.plotly_chart(fig, use_container_width=True, config=CHART_CONFIG)

    fig2 = go.Figure()
    fig2.add_trace(
        go.Scatter(
            x=detail_df["date_id"],
            y=detail_df["strength"],
            mode="lines+markers",
            name=gettext("Permutation-based value"),
            line=dict(color="#81FBA5", width=1.5),
        )
    )
    fig2.update_layout(
        height=220,
        margin=dict(l=40, r=20, t=30, b=20),
        plot_bgcolor="#111111",
        paper_bgcolor="#0B0B0B",
        font=dict(family="DM Sans", color="#E4E4E4"),
        title=gettext("Permutation-based value"),
    )
    st.plotly_chart(fig2, use_container_width=True, config=CHART_CONFIG)

    # feature_value is a string for categorical features - a "value over time"
    # line chart only makes sense for numeric ones.
    if pd.api.types.is_numeric_dtype(detail_df["feature_value"]):
        fig3 = go.Figure()
        fig3.add_trace(
            go.Scatter(
                x=detail_df["date_id"],
                y=detail_df["feature_value"],
                mode="lines+markers",
                name=gettext("Feature value"),
                line=dict(color="#909BF5", width=1.5),
            )
        )
        fig3.update_layout(
            height=220,
            margin=dict(l=40, r=20, t=30, b=20),
            plot_bgcolor="#111111",
            paper_bgcolor="#0B0B0B",
            font=dict(family="DM Sans", color="#E4E4E4"),
            title=gettext("Feature value"),
        )
        st.plotly_chart(fig3, use_container_width=True, config=CHART_CONFIG)

    with st.expander(gettext("Raw values")):
        st.dataframe(
            detail_df.rename(
                columns={
                    "date_id": gettext("Date"),
                    "prediction": gettext("Forecast value"),
                    "strength": gettext("Permutation-based value"),
                    "feature_value": gettext("Feature value"),
                }
            ),
            hide_index=True,
        )


def forecast_page() -> None:
    if "forecast_raw" not in st.session_state:
        st.info(gettext("Run a forecast from the sidebar to get started."))
        return

    chart_series_options = st.session_state.get("chart_series_options", [])
    chart_series_label = st.session_state.get("chart_series_label") or gettext(
        "Series"
    )

    if len(chart_series_options) > 1:
        display_series = st.selectbox(
            chart_series_label,
            options=chart_series_options,
            index=0,
            key="chart_series_select",
        )
    elif len(chart_series_options) == 1:
        display_series = chart_series_options[0]
    else:
        display_series = None

    show_confidence_interval = st.checkbox(
        gettext("Show confidence interval"),
        value=True,
        key="show_confidence_interval",
    )

    series_predictions = predictions_for_display_series(
        st.session_state["forecast_raw"], display_series
    )

    important_feature_names = [
        f["featureName"] for f in app_settings.important_features
    ]
    default_features = important_feature_names[
        : min(5, len(important_feature_names))
    ]
    selected_features = st.multiselect(
        gettext("Features to track"),
        options=important_feature_names,
        default=default_features,
        max_selections=MAX_SELECTED_FEATURES,
        key="selected_features",
        help=gettext("Up to {max} features.").format(max=MAX_SELECTED_FEATURES),
    )

    stacked_bar_df = get_pred_ex_stacked_bar_df(series_predictions)
    if selected_features:
        stacked_bar_df = stacked_bar_df[
            stacked_bar_df["feature"].isin(selected_features)
        ]

    chart_json = get_forecast_as_plotly_json(
        st.session_state["scoring_data"],
        st.session_state["n_historical_records_to_display"],
        predictions=st.session_state["forecast_raw"],
        display_series=display_series,
        stacked_bar_df=stacked_bar_df,
        feature_color_map=st.session_state.get("feature_color_map"),
        show_confidence_interval=show_confidence_interval,
    )
    st.plotly_chart(go.Figure(chart_json), config=CHART_CONFIG, use_container_width=True)

    narrative_key = series_narrative_key(display_series)
    show_commentary = is_llm_commentary_available() and st.session_state.get(
        "show_llm_commentary", False
    )
    if show_commentary:
        narrative = st.session_state.get("series_narratives", {}).get(narrative_key)
        if narrative:
            st.markdown(
                f"""
                <p style='font-family:"Fragment Mono",monospace;font-size:0.7rem;
                    text-transform:uppercase;letter-spacing:0.1em;
                    color:#81FBA5;margin-bottom:6px;'>AI GENERATED ANALYSIS</p>
                <p style='font-family:"DM Sans",sans-serif;font-size:1rem;font-weight:500;
                    color:#FFFF54;margin-bottom:10px;'>
                    {narrative['headline']}</p>
                """,
                unsafe_allow_html=True,
            )
            st.write(narrative["summary_body"])

    explanations_df = st.session_state.get("series_explanations", {}).get(
        narrative_key
    )
    if explanations_df is not None:
        with st.expander(gettext("Important Features"), expanded=False):
            st.write(explanations_df)

    st.markdown("---")
    st.markdown(
        f"""<p style='font-family:"Fragment Mono",monospace;font-size:0.7rem;
            text-transform:uppercase;letter-spacing:0.1em;
            color:#81FBA5;margin-bottom:2px;'>PERMUTATION-BASED EXPLANATIONS</p>
        <p style='color:#A2A2A2;font-size:0.9rem;margin-top:0;'>
            {gettext("See how individual features affected the forecast.")}</p>""",
        unsafe_allow_html=True,
    )

    tab_full, tab_single = st.tabs([gettext("Full forecast"), gettext("Single date")])

    with tab_full:
        summary_df = get_permutation_summary_df(
            series_predictions, feature_filter=selected_features or None
        )
        if summary_df.empty:
            st.info(gettext("No permutation-based explanations available."))
        else:
            event = st.dataframe(
                summary_df,
                column_config={
                    "feature": gettext("Feature"),
                    "avg_strength": st.column_config.NumberColumn(
                        gettext("Avg. value"), format="%.2f"
                    ),
                    "trend": st.column_config.LineChartColumn(gettext("Trend")),
                },
                column_order=["avg_strength", "trend", "feature"],
                hide_index=True,
                on_select="rerun",
                selection_mode="single-row",
                key="permutation_summary_table",
            )
            feature = _selected_row_feature(event, summary_df)
            if feature:
                _show_drill_down(feature, series_predictions)

    with tab_single:
        date_col = app_settings.datetime_partition_column
        available_dates = sorted(
            {p[date_col] for p in series_predictions if date_col in p}
        )
        if not available_dates:
            st.info(gettext("No forecast dates available."))
        else:
            selected_date = st.selectbox(
                gettext("Date"), options=available_dates, key="permutation_single_date"
            )
            single_date_df = get_permutation_single_date_df(
                series_predictions,
                selected_date,
                feature_filter=selected_features or None,
            )
            if single_date_df.empty:
                st.info(gettext("No permutation-based explanations for this date."))
            else:
                event2 = st.dataframe(
                    single_date_df,
                    column_config={
                        "feature": gettext("Feature"),
                        "strength": st.column_config.NumberColumn(
                            gettext("Permutation-based value"), format="%.2f"
                        ),
                        # feature_value can be a string for categorical features,
                        # so it isn't forced into a NumberColumn.
                        "feature_value": gettext("Feature value"),
                    },
                    hide_index=True,
                    on_select="rerun",
                    selection_mode="single-row",
                    key="permutation_single_date_table",
                )
                feature2 = _selected_row_feature(event2, single_date_df)
                if feature2:
                    _show_drill_down(feature2, series_predictions)


# st.Page("views/forecast.py", ...) executes this file as the page's script.
forecast_page()
