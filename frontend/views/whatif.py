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
"""What-If Scenarios page: stage per-date overrides on known-in-advance features,
re-score against the same live deployment, and compare against the original
forecast. No dedicated what-if backend - re-scoring reuses get_predictions() with a
modified copy of scoring_data, exactly like the React port."""

import copy

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

# forecastic and common are importable because app.py (the process entry point)
# already put the repo root and frontend/ on sys.path before importing this module.
from common import CHART_CONFIG, app_settings

from forecastic.api import (
    apply_what_if_overrides,
    get_predictions,
    get_whatif_comparison_plotly_json,
)
from forecastic.i18n import gettext


def _active_series() -> tuple[str | None, bool]:
    """Mirrors the React port's isPageEnabled: usable when there's no multiseries
    concept at all, exactly one series, or the user picked one on the Forecast page."""
    options = st.session_state.get("chart_series_options", [])
    if len(options) <= 1:
        return (options[0] if options else None), True
    selected = st.session_state.get("chart_series_select")
    return selected, selected is not None


def _new_scenario_name(scenarios: dict) -> str:
    n = len(scenarios) + 1
    while f"Scenario {n}" in scenarios:
        n += 1
    return f"Scenario {n}"


def whatif_page() -> None:
    if "forecast_raw" not in st.session_state:
        st.info(gettext("Run a forecast from the sidebar to get started."))
        return

    eligible_features = [
        f for f in app_settings.what_if_features if f.known_in_advance
    ]
    if not eligible_features:
        st.info(
            gettext(
                "No known-in-advance features are configured for What-If scenarios."
            )
        )
        return

    display_series, is_single_series = _active_series()
    if not is_single_series:
        st.info(
            gettext(
                "Select a single series on the Forecast page to use What-If scenarios."
            )
        )
        return

    scenarios = st.session_state.setdefault("what_if_scenarios", {})

    col_select, col_new, col_dup, col_del = st.columns([3, 1, 1, 1])
    with col_select:
        active_name = (
            st.selectbox(
                gettext("Scenario"),
                options=list(scenarios.keys()),
                key="active_whatif_scenario",
            )
            if scenarios
            else None
        )
    with col_new:
        st.write("")
        if st.button(gettext("+ New"), use_container_width=True):
            new_name = _new_scenario_name(scenarios)
            scenarios[new_name] = {
                "description": "",
                "overrides": {},
                "forecast_data": None,
            }
            st.session_state["active_whatif_scenario"] = new_name
            st.rerun()
    with col_dup:
        st.write("")
        if active_name and st.button(gettext("Duplicate"), use_container_width=True):
            new_name = _new_scenario_name(scenarios)
            scenarios[new_name] = copy.deepcopy(scenarios[active_name])
            st.session_state["active_whatif_scenario"] = new_name
            st.rerun()
    with col_del:
        st.write("")
        if active_name and st.button(gettext("Delete"), use_container_width=True):
            del scenarios[active_name]
            st.session_state.pop("active_whatif_scenario", None)
            st.rerun()

    if not active_name:
        st.info(gettext("Add a scenario to get started."))
        return

    scenario = scenarios[active_name]

    scenario["description"] = st.text_area(
        gettext("Description"),
        value=scenario.get("description", ""),
        key=f"whatif_desc_{active_name}",
    )

    feature_names = [f.feature_name for f in eligible_features]
    chosen_features = st.multiselect(
        gettext("Features to override"),
        options=feature_names,
        default=[f for f in scenario["overrides"].keys() if f in feature_names],
        key=f"whatif_features_{active_name}",
    )

    for removed in set(scenario["overrides"].keys()) - set(chosen_features):
        del scenario["overrides"][removed]

    for feature_name in chosen_features:
        feature_spec = next(
            f for f in eligible_features if f.feature_name == feature_name
        )
        st.markdown(f"**{feature_name}**")
        existing = scenario["overrides"].get(feature_name, {})
        rows_df = pd.DataFrame(
            {
                "date": pd.to_datetime(list(existing.keys()), errors="coerce"),
                "value": list(existing.values()),
            }
        )

        value_column = (
            st.column_config.SelectboxColumn(gettext("Value"), options=feature_spec.values)
            if feature_spec.values
            else st.column_config.NumberColumn(gettext("Value"))
        )
        edited_df = st.data_editor(
            rows_df,
            num_rows="dynamic",
            hide_index=True,
            column_config={
                "date": st.column_config.DateColumn(gettext("Date")),
                "value": value_column,
            },
            key=f"whatif_editor_{active_name}_{feature_name}",
        )

        new_overrides = {}
        for _, row in edited_df.iterrows():
            if pd.isna(row["date"]) or row["value"] in (None, ""):
                continue
            new_overrides[row["date"].strftime(app_settings.date_format)] = row["value"]
        scenario["overrides"][feature_name] = new_overrides

    with st.expander(gettext("Or bulk-import overrides from a CSV")):
        st.caption(gettext("Columns: featureName, date, value"))
        uploaded = st.file_uploader(
            gettext("Upload CSV"),
            type="csv",
            key=f"whatif_bulk_{active_name}",
            label_visibility="collapsed",
        )
        if uploaded is not None:
            bulk_df = pd.read_csv(uploaded)
            for _, row in bulk_df.iterrows():
                feature_name = str(row["featureName"])
                date_str = str(pd.to_datetime(row["date"]).strftime(app_settings.date_format))
                scenario["overrides"].setdefault(feature_name, {})[date_str] = row["value"]
            st.success(gettext("Overrides imported - re-open this scenario's feature list to see them."))

    if st.button(gettext("Run scenario"), type="primary", key=f"whatif_run_{active_name}"):
        if not any(scenario["overrides"].values()):
            st.warning(gettext("Add at least one feature value override before running."))
        else:
            with st.spinner(gettext("Scoring scenario...")):
                modified_scoring_data = apply_what_if_overrides(
                    st.session_state["scoring_data"], scenario["overrides"]
                )
                scenario["forecast_data"] = get_predictions(modified_scoring_data)
            st.rerun()

    if scenario.get("forecast_data"):
        chart_json = get_whatif_comparison_plotly_json(
            st.session_state["scoring_data"],
            st.session_state["n_historical_records_to_display"],
            original_predictions=st.session_state["forecast_raw"],
            scenario_predictions=scenario["forecast_data"],
            display_series=display_series,
        )
        st.plotly_chart(
            go.Figure(chart_json), config=CHART_CONFIG, use_container_width=True
        )

        scenario_forecast_df = pd.DataFrame(scenario["forecast_data"])
        col_csv, col_json = st.columns(2)
        with col_csv:
            st.download_button(
                gettext("Export forecast as CSV"),
                data=scenario_forecast_df.to_csv(index=False),
                file_name=f"{active_name}.csv",
                mime="text/csv",
                use_container_width=True,
            )
        with col_json:
            st.download_button(
                gettext("Export forecast as JSON"),
                data=scenario_forecast_df.to_json(orient="records"),
                file_name=f"{active_name}.json",
                mime="application/json",
                use_container_width=True,
            )


# st.Page("views/whatif.py", ...) executes this file as the page's script.
whatif_page()
