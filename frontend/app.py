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

from forecastic.api import (
    LLMNotAvailableException,
    get_app_settings,
    get_explain_df,
    get_filters,
    get_forecast_as_plotly_json,
    get_llm_summary,
    get_pred_ex_stacked_bar_df,
    get_predictions,
    get_scoring_data,
    get_scoring_dataset_versions,
    get_standardized_predictions,
)
from forecastic.i18n import gettext
from forecastic.schema import FilterSpec

CHART_CONFIG = {"displayModeBar": False, "responsive": True}

_DATA_DIR = Path(__file__).parent / "data"
_ACTUALS_CSV = _DATA_DIR / "actuals_lookup.csv"


@st.cache_data
def _load_actuals() -> pd.DataFrame:
    """Load observed actuals from local CSV (fallback for ACTUALS_DATASET_ID)."""
    if _ACTUALS_CSV.exists():
        return pd.read_csv(_ACTUALS_CSV)
    return pd.DataFrame()


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
        show_llm = st.checkbox("Show AI commentary", value=False, key="show_llm_main")
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
            forecast_processed = get_standardized_predictions(scoring_data)

            st.session_state["forecast_processed"] = forecast_processed

            stacked_bar_df = get_pred_ex_stacked_bar_df(forecast_raw)
            st.session_state["stacked_bar_df"] = stacked_bar_df

            actuals_df = _load_actuals()
            if not actuals_df.empty:
                for fs in series_selections:
                    if fs.selected_values and fs.column in actuals_df.columns:
                        actuals_df = actuals_df[actuals_df[fs.column].isin(fs.selected_values)]

            st.session_state["chart_json"] = get_forecast_as_plotly_json(
                scoring_data, n_historical_records_to_display,
                stacked_bar_df=stacked_bar_df,
                actuals_df=actuals_df if not actuals_df.empty else None,
            )

        if show_llm:
            with st.spinner(gettext("Generating explanation...")):
                try:
                    forecast_summary = get_llm_summary(forecast_raw)
                    st.session_state["headline"] = forecast_summary.headline
                    st.session_state["forecast_interpretation"] = (
                        forecast_summary.summary_body
                    )
                except LLMNotAvailableException:
                    pass
        st.session_state["explanations_df"] = clean_column_headers(
            get_explain_df(forecast_raw)
        )

    if "chart_json" in st.session_state:
        chartContainer.plotly_chart(
            go.Figure(st.session_state["chart_json"]),
            config=CHART_CONFIG,
            use_container_width=True,
        )

    with explanationContainer:
        if show_llm and "forecast_interpretation" in st.session_state:
            st.markdown(
                f"""
                <p style='font-family:"Fragment Mono",monospace;font-size:0.7rem;
                    text-transform:uppercase;letter-spacing:0.1em;
                    color:#81FBA5;margin-bottom:6px;'>AI GENERATED ANALYSIS</p>
                <p style='font-family:"DM Sans",sans-serif;font-size:1rem;font-weight:500;
                    color:#FFFF54;margin-bottom:10px;'>
                    {st.session_state['headline']}</p>
                """,
                unsafe_allow_html=True,
            )
            st.write(st.session_state["forecast_interpretation"])


st.markdown(
    "<style>#MainMenu{visibility:hidden;}header{visibility:hidden;}"
    "footer{visibility:hidden;}</style>",
    unsafe_allow_html=True,
)

pg = st.navigation(
    [
        st.Page(fpa, title="Forecast Key Data", default=True),
        st.Page("pages/2_Analysis_of_Drivers.py", title="Analysis of Drivers"),
    ]
)
pg.run()
