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

import sys

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

sys.path.append("..")

from forecastic.api import (
    LLMNotAvailableException,
    get_app_settings,
    get_chart_series_options,
    get_explain_df,
    get_filters,
    get_forecast_as_plotly_json,
    get_llm_summary,
    get_pred_ex_stacked_bar_df,
    get_predictions,
    get_scoring_data,
    get_scoring_dataset_versions,
    predictions_for_display_series,
)
from forecastic.i18n import gettext
from forecastic.schema import FilterSpec

CHART_CONFIG = {"displayModeBar": False, "responsive": True}


sys.setrecursionlimit(10000)
app_settings = get_app_settings()

# Configure the page title, favicon, layout, etc
st.set_page_config(
    page_title=app_settings.page_title,
    layout="wide",
    page_icon="./datarobot_favicon.png",
)

with open("./style.css") as f:
    css = f.read()

st.markdown(f"<style>{css}</style>", unsafe_allow_html=True)


def clean_column_headers(df: pd.DataFrame) -> pd.DataFrame:
    """Clean column headers for display"""
    return df.rename(columns=lambda x: gettext(x.replace("_", " ").title()))


def set_title() -> None:
    """Set the title of the page"""
    with st.container(key="datarobot-logo"):
        logo_col, title_col = st.columns([1, 4])
        with logo_col:
            st.image("./DataRobot_white.svg", width=160)
        with title_col:
            st.markdown(
                f"""
                <p style='font-family:"Fragment Mono",monospace;font-size:0.7rem;
                    text-transform:uppercase;letter-spacing:0.1em;
                    color:#81FBA5;margin-bottom:2px;'>FORECAST ANALYSIS</p>
                <h1 style='font-family:"DM Sans",sans-serif;font-weight:500;
                    font-size:1.5rem;color:#FFFFFF;margin:0;letter-spacing:-0.01em;'>
                    {app_settings.page_title}</h1>
                """,
                unsafe_allow_html=True,
            )
    st.markdown(
        "<hr style='border:none;border-top:1px solid #1e1e1e;margin:12px 0 20px;'/>",
        unsafe_allow_html=True,
    )


def _series_narrative_key(display_series: str | None) -> str:
    return display_series if display_series is not None else ""


def _build_series_narratives(
    forecast_raw: list[dict],
    chart_series_options: list[str],
) -> tuple[dict[str, dict[str, str]], dict[str, pd.DataFrame]]:
    """Generate LLM narrative and feature table per chart series."""
    series_keys = chart_series_options if chart_series_options else [None]
    narratives: dict[str, dict[str, str]] = {}
    explanations: dict[str, pd.DataFrame] = {}

    for series in series_keys:
        key = _series_narrative_key(series)
        series_predictions = predictions_for_display_series(forecast_raw, series)
        explanations[key] = clean_column_headers(get_explain_df(series_predictions))
        try:
            forecast_summary = get_llm_summary(series_predictions)
            narratives[key] = {
                "headline": forecast_summary.headline,
                "summary_body": forecast_summary.summary_body,
            }
        except LLMNotAvailableException:
            continue

    return narratives, explanations


def fpa() -> None:
    set_title()
    chartContainer = st.container()
    explanationContainer = st.container()

    if "filters" not in st.session_state:
        st.session_state["filters"] = get_filters()
    if "dataset_versions" not in st.session_state:
        st.session_state["dataset_versions"] = get_scoring_dataset_versions()

    with st.sidebar:
        with st.form(key="sidebar_form"):
            versions = st.session_state["dataset_versions"]
            version_labels = [v["label"] for v in versions]
            st.selectbox(
                label=gettext("Prediction Timestamp"),
                options=version_labels,
                index=0,
                key="selected_version_label",
                help=gettext("Select the dataset version to replay. Defaults to the latest."),
            )

            st.subheader(gettext("Select Filters for the Forecast"))

            for filter_widget in st.session_state["filters"]:
                column_name = filter_widget.column_name
                st.multiselect(
                    label=filter_widget.display_name,
                    options=filter_widget.valid_values,
                    key=f"filter_{column_name}",
                    placeholder=gettext("Choose an option"),
                )

            sidebarSubmit = st.form_submit_button(label=gettext("Run Forecast"))

        n_historical_records_to_display = st.number_input(
            gettext("Number of records to display"),
            min_value=10,
            max_value=200,
            value=min(200, app_settings.maximum_default_display_length),
            step=10,
        )
    if sidebarSubmit:
        with st.spinner(gettext("Processing forecast...")):
            selected_label = st.session_state.get("selected_version_label")
            selected_version = next(
                (v for v in st.session_state["dataset_versions"] if v["label"] == selected_label),
                st.session_state["dataset_versions"][0],
            )
            selected_version_id = (
                None if selected_version["is_latest"] else selected_version["version_id"]
            )

            series_selections = []
            for filter_widget in st.session_state["filters"]:
                column_name = filter_widget.column_name
                widget_value = st.session_state[f"filter_{column_name}"]
                series_selections.append(
                    FilterSpec(column=column_name, selected_values=widget_value)
                )
            try:
                scoring_data = get_scoring_data(
                    filter_selection=series_selections,
                    version_id=selected_version_id,
                )
                st.session_state["scoring_data"] = scoring_data
            except ValueError as e:
                st.error(str(e))
                st.stop()
            forecast_raw = get_predictions(scoring_data)
            st.session_state["forecast_raw"] = forecast_raw
            chart_label, chart_series_options = get_chart_series_options(
                series_selections
            )
            st.session_state["chart_series_label"] = chart_label
            st.session_state["chart_series_options"] = chart_series_options
            st.session_state["n_historical_records_to_display"] = (
                n_historical_records_to_display
            )

        with st.spinner(gettext("Generating explanation...")):
            narratives, explanations = _build_series_narratives(
                forecast_raw, chart_series_options
            )
            st.session_state["series_narratives"] = narratives
            st.session_state["series_explanations"] = explanations

    if "forecast_raw" in st.session_state:
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

        series_predictions = predictions_for_display_series(
            st.session_state["forecast_raw"], display_series
        )
        stacked_bar_df = get_pred_ex_stacked_bar_df(series_predictions)
        chart_json = get_forecast_as_plotly_json(
            st.session_state["scoring_data"],
            st.session_state["n_historical_records_to_display"],
            predictions=st.session_state["forecast_raw"],
            display_series=display_series,
            stacked_bar_df=stacked_bar_df,
        )
        chartContainer.plotly_chart(
            go.Figure(chart_json),
            config=CHART_CONFIG,
            use_container_width=True,
        )

        narrative_key = _series_narrative_key(display_series)
        with explanationContainer:
            narrative = st.session_state.get("series_narratives", {}).get(
                narrative_key
            )
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


def _main() -> None:
    hide_streamlit_style = """
    <style>
    # MainMenu {visibility: hidden;}
    header {visibility: hidden;}
    footer {visibility: hidden;}
    </style>
    """
    st.markdown(hide_streamlit_style, unsafe_allow_html=True)

    fpa()


if __name__ == "__main__":
    _main()
