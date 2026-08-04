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

import colorsys
import datetime as dt
import functools
import io
import json
import sys
from importlib import resources
from typing import Any, List, Optional, Tuple
from urllib.parse import urljoin

import datarobot as dr
import pandas as pd
import plotly.graph_objects as go
import yaml
from datarobot.errors import ClientError
from datarobot_predict.deployment import predict
from openai import OpenAI
from plotly.subplots import make_subplots
from pydantic import ValidationError

sys.path.append("..")

from forecastic.i18n import gettext
from forecastic.resources import (
    Application,
    GenerativeDeployment,
    LLMGatewaySettings,
    ScoringDataset,
    TimeSeriesDeployment,
    app_settings_file_name,
)
from forecastic.schema import (
    AppRuntimeAttributes,
    AppSettings,
    AppUrls,
    FilterSpec,
    ForecastSummary,
    MultiSelectFilter,
    PredictionRow,
)

try:
    # Load static settings w/o making assumptions about working directory
    app_settings = AppSettings(
        **yaml.safe_load(
            resources.files(__name__.split(".")[0])
            .joinpath(app_settings_file_name)
            .read_text()
        )
    )

    time_series_deployment_id = TimeSeriesDeployment().id
    scoring_dataset_id = ScoringDataset().id

except (FileNotFoundError, ValidationError) as e:
    raise ValueError(
        gettext(
            "Unable to load Deployment IDs or Application Settings. "
            "If running locally, verify you have selected the correct "
            "stack and that it is active using `pulumi stack output`. "
            "If running in DataRobot, verify your runtime parameters have been set correctly."
        )
    ) from e


class LLMNotAvailableException(Exception):
    """Exception raised when the LLM is unavailable."""


def _get_completion(
    prompt: str,
    temperature: float = 0,
    system_prompt: Optional[str] = None,
    llm_model_name: Optional[str] = None,
) -> str:
    """Generate LLM completion."""
    gateway_model = LLMGatewaySettings().model
    try:
        dr_client = dr.client.get_client()
        if gateway_model:
            # Direct LLM Gateway: no Playground/Blueprint/Deployment chain was
            # provisioned — call the Gateway's OpenAI-compatible endpoint directly
            # with the catalog model id (see infra/settings_generative.py).
            client = OpenAI(
                base_url=dr_client.endpoint.rstrip("/") + "/genai/llmgw",
                api_key=dr_client.token,
            )
            model = gateway_model
        else:
            generative_deployment_id = GenerativeDeployment().id
            client = OpenAI(
                base_url=dr_client.endpoint.rstrip("/")
                + f"/deployments/{generative_deployment_id}",
                api_key=dr_client.token,
            )
            model = "datarobot-deployed-llm"
        if system_prompt:
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt},
            ]
        else:
            messages = [{"role": "user", "content": prompt}]
        resp = client.chat.completions.create(
            messages=messages,  # type: ignore[arg-type]
            model=model,
            temperature=temperature,
        )
        return str(resp.choices[0].message.content)
    except Exception as e:
        raise LLMNotAvailableException("LLM is unavailable.") from e


def get_app_settings() -> AppSettings:
    return app_settings


def is_llm_commentary_available() -> bool:
    """True when app config allows LLM commentary and a generative deployment
    (or a direct LLM Gateway model) is configured."""
    if not app_settings.llm_commentary_enabled:
        return False
    if LLMGatewaySettings().model:
        return True
    try:
        return bool(GenerativeDeployment().id)
    except ValidationError:
        return False


def _get_app_urls() -> AppUrls:
    base_url = urljoin(dr.Client().endpoint, "..")
    dataset_url = (
        f"usecases/{app_settings.use_case_id}/explore/dataset/{scoring_dataset_id}"
    )
    model_url = f"usecases/{app_settings.use_case_id}/model/leaderboard/{app_settings.project_id}/{app_settings.model_id}"
    deployment_url = f"console-nextgen/deployments/{time_series_deployment_id}/overview"
    return AppUrls(
        dataset=urljoin(base_url, dataset_url),
        model=urljoin(base_url, model_url),
        deployment=urljoin(base_url, deployment_url),
    )


def _get_app_metadata() -> Tuple[str, str]:
    client = dr.Client()
    try:
        application_id = Application().id
        url = f"customApplications/{application_id}/"
        resp = client.get(url).json()
        app_creator_email = resp["createdBy"]
        # Parse "2024-12-10 15:18:53.868000" into date
        app_latest_created_date = dt.datetime.strptime(
            resp["updatedAt"], "%Y-%m-%d %H:%M:%S.%f"
        ).strftime("%Y-%m-%d %H:%M:%S")
    except (ValidationError, ClientError):
        url = "account/info/"
        resp = client.get(url).json()
        app_creator_email = resp["email"]
        app_latest_created_date = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return app_creator_email, app_latest_created_date


def get_runtime_attributes() -> AppRuntimeAttributes:
    """Get relevant urls and application metadata."""
    app_urls = _get_app_urls()
    app_creator_email, app_latest_created_date = _get_app_metadata()

    return AppRuntimeAttributes(
        app_urls=app_urls,
        app_creator_email=app_creator_email,
        app_latest_created_date=app_latest_created_date,
    )


def get_scoring_dataset_versions() -> list[dict[str, Any]]:
    """Return all versions of the scoring dataset, newest first.

    Each entry: {version_id, created_at, label, is_latest}.
    """
    response = dr.Client().get(f"datasets/{scoring_dataset_id}/versions/").json()
    versions = sorted(
        response.get("data", []),
        key=lambda v: v.get("creationDate", ""),
        reverse=True,
    )
    result = []
    for v in versions:
        created = v.get("creationDate", "")
        label = created[:10] if created else v["versionId"]
        result.append(
            {
                "version_id": v["versionId"],
                "created_at": created,
                "label": label,
                "is_latest": v.get("isLatestVersion", False),
            }
        )
    return result or [
        {"version_id": scoring_dataset_id, "created_at": "", "label": "Latest", "is_latest": True}
    ]


@functools.lru_cache(maxsize=32)
def _get_scoring_data(version_id: Optional[str] = None) -> pd.DataFrame:
    """Get scoring data for a specific dataset version (or latest if None)."""
    if version_id is None:
        return dr.Dataset.get(scoring_dataset_id).get_as_dataframe()
    response = dr.Client().get(
        f"datasets/{scoring_dataset_id}/versions/{version_id}/file/", stream=True
    )
    return pd.read_csv(io.StringIO(response.text))


def get_chart_series_options(
    filter_selection: List[FilterSpec],
) -> tuple[str | None, list[str]]:
    """Label and sidebar selection order for the chart series picker."""
    multiseries_col = app_settings.multiseries_id_column
    display_name_by_column = {
        category.column_name: category.display_name
        for category in app_settings.filterable_categories
    }
    for spec in filter_selection:
        if spec.column == multiseries_col and spec.selected_values:
            return display_name_by_column.get(multiseries_col, multiseries_col), list(
                spec.selected_values
            )
    for spec in filter_selection:
        if spec.selected_values:
            return display_name_by_column.get(spec.column, spec.column), list(
                spec.selected_values
            )
    return None, []


def _filter_records_by_series(
    records: list[dict[str, Any]], series_value: str
) -> list[dict[str, Any]]:
    multiseries_col = app_settings.multiseries_id_column
    series_token = str(series_value)
    return [
        row
        for row in records
        if str(row.get(multiseries_col)) == series_token
    ]


def predictions_for_display_series(
    predictions: list[dict[str, Any]], display_series: str | None
) -> list[dict[str, Any]]:
    """Return prediction rows for one chart series, or all rows if unset."""
    if display_series is None:
        return predictions
    return _filter_records_by_series(predictions, display_series)


def get_scoring_data(
    filter_selection: Optional[List[FilterSpec]] = None,
    version_id: Optional[str] = None,
) -> list[dict[str, Any]]:
    """
    Get scoring data from DataRobot.

    Scoring data will be filtered based on the selected filters.
    An exception will be raised if no data is available for the selected series.

    Parameters
    ----------
    filter_selection : Optional[List[FilterSpec]]
        List of filters to apply to the data.
    """
    df = _get_scoring_data(version_id=version_id)
    if filter_selection is None:
        return df.to_dict(orient="records")  # type: ignore[no-any-return]
    for widget in filter_selection:
        widget_values = widget.selected_values
        column_name = widget.column
        if len(widget_values) > 0:
            df = df[df[column_name].isin(widget_values)]
    if len(df) == 0:
        raise ValueError(
            gettext(
                "No data available for the selected series. Try a different combination of filters."
            )
        )
    return df.to_dict(orient="records")  # type: ignore[no-any-return]


def get_filters() -> List[MultiSelectFilter]:
    """
    Get available options for each filter.

    Returns
    -------
    List[MultiSelectFilter]
        Available filters and displays.
    """

    scoring_data = _get_scoring_data()
    filters = []
    for category in app_settings.filterable_categories:
        filters.append(
            MultiSelectFilter(
                column_name=category.column_name,
                display_name=category.display_name,
                valid_values=scoring_data[category.column_name].unique().tolist(),
            )
        )
    return filters


def get_predictions(scoring_data: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Retrieve predictions in the format returned by DataRobot-Predict.

    Parameters
    ----------
    scoring_data : list[dict]
        A list of dictionaries containing the input data for generating predictions.

    Returns
    -------
    list[dict[str, Any]]
        List of predictions from deployed time series model.
    """

    predictions = _get_predictions_cached(json.dumps(scoring_data, sort_keys=True))

    return predictions.to_dict(orient="records")  # type: ignore[no-any-return]


@functools.lru_cache(maxsize=16)
def _get_predictions_cached(scoring_data_json: str) -> pd.DataFrame:
    predictions = predict(
        deployment=dr.Deployment.get(time_series_deployment_id),
        data_frame=pd.DataFrame(json.loads(scoring_data_json)),
        max_explanations=3,
    ).dataframe

    return predictions


def get_standardized_predictions(
    scoring_data: list[dict[str, Any]],
) -> list[PredictionRow]:
    """Retrieve predictions and process them into a standardized format.

    Format cast is independent of the scoring data.

    Parameters
    ----------
    data : list[dict]
        A list of dictionaries containing the input data for generating predictions.

    Returns
    -------
    list[PredictionRow]
        A list of PredictionRow objects representing the processed and standardized predictions.
    """
    predictions = get_predictions(scoring_data)
    processed_predictions = _process_predictions(predictions)

    return processed_predictions


def _resolve_target_pred_col(data: pd.DataFrame, target: str) -> str:
    """Find the target prediction column in a real-time predict() response.

    The exact column name isn't always byte-for-byte f"{target}_PREDICTION" - fall
    back to the one column ending in "_PREDICTION" if the direct guess isn't there.
    """
    guess = f"{target}_PREDICTION"
    if guess in data.columns:
        return guess
    candidates = [c for c in data.columns if c.endswith("_PREDICTION")]
    if len(candidates) == 1:
        return candidates[0]
    raise KeyError(
        f"Could not find a prediction column for target '{target}' in the response "
        f"(tried '{guess}', found candidates: {candidates})"
    )


def _process_predictions(predictions: list[dict[str, Any]]) -> list[PredictionRow]:
    """Translate predictions into standardized format."""

    data = pd.DataFrame(predictions)
    if data.empty:
        return []

    bound_at_zero = app_settings.lower_bound_forecast_at_0
    target = app_settings.target
    date_id = app_settings.datetime_partition_column
    series_id = app_settings.multiseries_id_column
    target_pred_col = _resolve_target_pred_col(data, target)

    if series_id is not None:
        slim_predictions = data[[series_id, date_id, target_pred_col]].rename(
            columns={date_id: "date_id"}
        )
    else:
        slim_predictions = data[[date_id, target_pred_col]].rename(
            columns={date_id: "date_id"}
        )

    has_intervals = False
    if app_settings.prediction_interval is not None:
        prediction_interval = f"{app_settings.prediction_interval:.0f}"
        percentile_prefix = f"PREDICTION_{prediction_interval}_PERCENTILE"
        has_intervals = f"{percentile_prefix}_LOW" in data.columns

    if has_intervals:
        intervals = data[[c for c in data.columns if percentile_prefix in c]]
        clean_predictions = pd.concat([slim_predictions, intervals], axis=1).rename(
            columns={
                target_pred_col: "prediction",
                f"{percentile_prefix}_LOW": "low",
                f"{percentile_prefix}_HIGH": "high",
            }
        )
    else:
        clean_predictions = slim_predictions.rename(columns={target_pred_col: "prediction"})

    clean_predictions = clean_predictions.groupby("date_id").sum().reset_index()

    if not has_intervals:
        clean_predictions["low"] = None
        clean_predictions["high"] = None

    if bound_at_zero:
        bounds = ["prediction"]
        if has_intervals:
            bounds += ["low", "high"]
        clean_predictions[bounds] = clean_predictions[bounds].clip(lower=0)

    return [PredictionRow(**i) for i in clean_predictions.to_dict(orient="records")]


def get_formatted_predictions(
    scoring_data: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Format predictions for the frontend."""
    predictions = get_predictions(scoring_data)
    formatted_predictions = _format_predictions(predictions)

    return formatted_predictions


def _format_predictions(predictions: list[dict[str, Any]]) -> list[dict[Any, Any]]:
    """Format predictions for the frontend."""

    data = pd.DataFrame(predictions)

    target = app_settings.target
    multiseries_id_column = app_settings.multiseries_id_column
    date_id = app_settings.datetime_partition_column

    data["timestamp"] = data[date_id]
    data["prediction"] = data[_resolve_target_pred_col(data, target)]
    if multiseries_id_column is not None:
        data["seriesId"] = data[multiseries_id_column]
    data["forecastDistance"] = data["FORECAST_DISTANCE"]
    data["forecastPoint"] = data["FORECAST_POINT"]

    has_intervals = False
    if app_settings.prediction_interval is not None:
        prediction_interval = f"{app_settings.prediction_interval:.0f}"
        percentile_prefix = f"PREDICTION_{prediction_interval}_PERCENTILE"
        has_intervals = f"{percentile_prefix}_LOW" in data.columns

    if has_intervals:
        data["predictionIntervals"] = data.apply(
            lambda x: {
                prediction_interval: {
                    "low": x[f"{percentile_prefix}_LOW"],
                    "high": x[f"{percentile_prefix}_HIGH"],
                }
            },
            axis=1,
        )
    else:
        data["predictionIntervals"] = None

    data["predictionExplanations"] = data.apply(
        lambda x: [
            {
                "feature": x[f"EXPLANATION_{i}_FEATURE_NAME"],
                "featureValue": x[f"EXPLANATION_{i}_ACTUAL_VALUE"],
                "label": target,
                "qualitativeStrength": x[f"EXPLANATION_{i}_QUALITATIVE_STRENGTH"],
                "strength": x[f"EXPLANATION_{i}_STRENGTH"],
            }
            for i in range(1, len(x.index))
            if f"EXPLANATION_{i}_FEATURE_NAME" in x.index
        ],
        axis=1,
    )

    return data.to_dict(orient="records")  # type: ignore[no-any-return]


BAR_COLORS = [
    "#81FBA5", "#44BFFC", "#909BF5", "#FFFF54",
    "#5C41FF", "#61DFCF", "#BFFD7E", "#8AC2D5",
]

_HOVERLABEL = dict(
    bgcolor="#141414",
    font=dict(family="DM Sans", size=13, color="#E4E4E4"),
    bordercolor="#2a2a2a",
    namelength=-1,
    align="left",
)


def _feature_group_name(feature: str) -> str:
    """Original feature name before the first derivation segment.

    DataRobot derived features use ``<Original> <derivation> ...`` — the group
    is the substring before the first `` (`` (space + opening parenthesis).
    """
    paren_idx = feature.find(" (")
    if paren_idx >= 0:
        return feature[:paren_idx].strip()
    return feature.strip()


def _hex_to_rgb(hex_color: str) -> tuple[float, float, float]:
    hex_color = hex_color.lstrip("#")
    return tuple(int(hex_color[i : i + 2], 16) / 255 for i in (0, 2, 4))  # type: ignore[return-value]


def _rgb_to_hex(red: float, green: float, blue: float) -> str:
    return f"#{int(red * 255):02x}{int(green * 255):02x}{int(blue * 255):02x}"


def _shade_hex(base_hex: str, shade_index: int, group_size: int) -> str:
    """Return a lighter/darker variant of ``base_hex`` for derivations in one group."""
    if group_size <= 1:
        return base_hex
    red, green, blue = _hex_to_rgb(base_hex)
    hue, lightness, saturation = colorsys.rgb_to_hls(red, green, blue)
    # Spread lightness across group members while keeping hue aligned.
    lightness = 0.32 + (shade_index / (group_size - 1)) * 0.36
    saturation = min(max(saturation, 0.45), 0.9)
    red2, green2, blue2 = colorsys.hls_to_rgb(hue, lightness, saturation)
    return _rgb_to_hex(red2, green2, blue2)


def build_feature_color_map(features: list[str] | set[str]) -> dict[str, str]:
    """Stable feature → color map; derivations of the same original share a hue."""
    unique_features = sorted(set(features))
    groups: dict[str, list[str]] = {}
    for feature in unique_features:
        groups.setdefault(_feature_group_name(feature), []).append(feature)

    color_map: dict[str, str] = {}
    for group_index, group_name in enumerate(sorted(groups)):
        base_color = BAR_COLORS[group_index % len(BAR_COLORS)]
        group_features = sorted(groups[group_name])
        for feature_index, feature in enumerate(group_features):
            color_map[feature] = _shade_hex(base_color, feature_index, len(group_features))
    return color_map


def _feature_bar_color(
    feature: str, feature_color_map: dict[str, str] | None
) -> str:
    if feature_color_map is not None and feature in feature_color_map:
        return feature_color_map[feature]
    return BAR_COLORS[abs(hash(feature)) % len(BAR_COLORS)]

_AXIS_STYLE = dict(
    color="#A2A2A2",
    showgrid=True,
    gridcolor="#1e1e1e",
    linecolor="#2a2a2a",
    tickfont=dict(family="DM Sans", size=11),
)

_LAYOUT_BASE = dict(
    hovermode="x unified",
    plot_bgcolor="#111111",
    paper_bgcolor="#0B0B0B",
    font=dict(family="DM Sans", color="#E4E4E4"),
    hoverlabel=_HOVERLABEL,
)


def get_forecast_as_plotly_json(
    scoring_data: list[dict[str, Any]],
    n_historical_records_to_display: int,
    stacked_bar_df: Optional[pd.DataFrame] = None,
    predictions: list[dict[str, Any]] | None = None,
    display_series: str | None = None,
    feature_color_map: dict[str, str] | None = None,
) -> dict[str, Any]:
    """
    Render the forecast chart as a Plotly figure.

    When ``display_series`` is set, history and forecast show that series only.
    When ``stacked_bar_df`` is provided, returns a combined 2×2 layout with XEMP
    stacked bar; otherwise a simple single-row chart.
    """

    datetime_partition_column = app_settings.datetime_partition_column
    target = app_settings.target

    chart_scoring_data = scoring_data
    chart_predictions = (
        predictions if predictions is not None else get_predictions(scoring_data)
    )
    if display_series is not None:
        chart_scoring_data = _filter_records_by_series(scoring_data, display_series)
        chart_predictions = _filter_records_by_series(chart_predictions, display_series)

    forecast = pd.DataFrame(
        [i.model_dump() for i in _process_predictions(chart_predictions)]
    )
    history = _aggregate_scoring_data(chart_scoring_data).tail(
        n_historical_records_to_display
    )

    actual_col = f"{target} (actual)" if f"{target} (actual)" in history.columns else target
    series_suffix = f" ({display_series})" if display_series is not None else ""
    history_name = gettext("{target} History{suffix}").format(
        target=target, suffix=series_suffix
    )
    forecast_name = (
        gettext("{target} Forecast{suffix}").format(target=target, suffix=series_suffix)
        if display_series is not None
        else gettext("Total {target} Forecast").format(target=target)
    )

    if stacked_bar_df is not None:
        return _build_combined_figure(
            history,
            forecast,
            stacked_bar_df,
            actual_col,
            target,
            datetime_partition_column,
            history_name=history_name,
            forecast_name=forecast_name,
            feature_color_map=feature_color_map,
        )

    # ── Fallback: simple single-row chart ────────────────────────────────
    fig = make_subplots(specs=[[{"secondary_y": False}]])

    fig.add_trace(go.Scatter(
        x=history.timestamp, y=history[actual_col],
        mode="lines+markers",
        name=history_name,
        line=dict(color="#81FBA5", width=1.5),
        marker=dict(color="#81FBA5", size=5, symbol="circle"),
    ))
    if forecast["low"].notna().any():
        fig.add_trace(go.Scatter(
            x=forecast["date_id"], y=forecast["low"], mode="lines",
            name=gettext("Low forecast"),
            line=dict(color="#909BF5", width=1, dash="dot"),
        ))
        fig.add_trace(go.Scatter(
            x=forecast["date_id"], y=forecast["high"], mode="lines",
            name=gettext("High forecast"),
            line=dict(color="#909BF5", width=1, dash="dot"),
        ))
    fig.add_trace(go.Scatter(
        x=forecast["date_id"], y=forecast["prediction"],
        mode="lines+markers",
        name=forecast_name,
        line=dict(color="#44BFFC", width=1.5),
        marker=dict(color="#44BFFC", size=5, symbol="circle"),
    ))
    fig.add_vline(
        x=history.loc[lambda x: ~pd.isna(x[actual_col]), "timestamp"].max(),
        line_width=1, line_dash="dash", line_color="#2a2a2a",
    )
    fig.update_xaxes(**_AXIS_STYLE, title_text=datetime_partition_column)
    fig.update_yaxes(**_AXIS_STYLE, title_font_size=13, title_text=app_settings.graph_y_axis)
    fig.update_layout(
        **_LAYOUT_BASE,
        height=520,
        showlegend=True,
        legend=dict(orientation="h", yanchor="top", y=-0.18,
                    font=dict(family="DM Sans", size=12, color="#A2A2A2"),
                    bgcolor="rgba(0,0,0,0)"),
        margin=dict(l=50, r=30, b=20, t=30, pad=4),
        xaxis=dict(rangeslider=dict(visible=True, bgcolor="#111111"), type="date"),
        uniformtext_mode="hide",
    )
    fig.update_layout(xaxis=dict(fixedrange=False), yaxis=dict(fixedrange=False))
    fig.update_traces(connectgaps=False)
    return fig.to_dict()  # type: ignore[no-any-return]


def _build_combined_figure(
    history: pd.DataFrame,
    forecast: pd.DataFrame,
    stacked_bar_df: pd.DataFrame,
    actual_col: str,
    target: str,
    datetime_partition_column: str,
    history_name: str | None = None,
    forecast_name: str | None = None,
    feature_color_map: dict[str, str] | None = None,
) -> dict[str, Any]:
    """2×2 combined layout: history | forecast / empty | stacked bar."""

    if history_name is None:
        history_name = gettext("{target} History").format(target=target)
    if forecast_name is None:
        forecast_name = gettext("Total {target} Forecast").format(target=target)

    n_history = max(history[actual_col].notna().sum(), 1)
    n_forecast = max(len(forecast), 1)
    forecast_frac = min(0.78, max(0.50, (n_forecast / (n_history + n_forecast)) * 4))
    history_frac = 1.0 - forecast_frac

    fig = make_subplots(
        rows=2, cols=2,
        column_widths=[history_frac, forecast_frac],
        row_heights=[0.58, 0.42],
        shared_xaxes="columns",
        shared_yaxes="rows",
        specs=[
            [{"secondary_y": False}, {"secondary_y": False}],
            [None, {"secondary_y": False}],
        ],
        vertical_spacing=0.04,
        horizontal_spacing=0.015,
    )

    # ── Top-left: history (trim to rows with actual values) ─────────────
    history_actual = history[history[actual_col].notna()]
    fig.add_trace(go.Scatter(
        x=history_actual.timestamp, y=history_actual[actual_col],
        mode="lines+markers",
        name=history_name,
        line=dict(color="#81FBA5", width=1.5),
        marker=dict(color="#81FBA5", size=5, symbol="circle"),
        legend="legend",
    ), row=1, col=1)

    # ── Top-right: forecast ──────────────────────────────────────────────
    if forecast["low"].notna().any():
        fig.add_trace(go.Scatter(
            x=forecast["date_id"], y=forecast["low"], mode="lines",
            name=gettext("Low forecast"),
            line=dict(color="#909BF5", width=1, dash="dot"),
            legend="legend",
        ), row=1, col=2)
        fig.add_trace(go.Scatter(
            x=forecast["date_id"], y=forecast["high"], mode="lines",
            name=gettext("High forecast"),
            line=dict(color="#909BF5", width=1, dash="dot"),
            legend="legend",
        ), row=1, col=2)

    fig.add_trace(go.Scatter(
        x=forecast["date_id"], y=forecast["prediction"],
        mode="lines+markers",
        name=forecast_name,
        line=dict(color="#44BFFC", width=1.5),
        marker=dict(color="#44BFFC", size=5, symbol="circle"),
        legend="legend",
    ), row=1, col=2)

    # ── Bottom-right: stacked bar (XEMP) ────────────────────────────────
    for feat in sorted(stacked_bar_df["feature"].unique()):
        feat_data = stacked_bar_df[stacked_bar_df["feature"] == feat]
        fig.add_trace(go.Bar(
            x=feat_data["date_id"],
            y=feat_data["strength"],
            name=feat,
            marker_color=_feature_bar_color(feat, feature_color_map),
            legend="legend2",
            showlegend=True,
            hoverlabel=_HOVERLABEL,
        ), row=2, col=2)

    # ── Axis styling ─────────────────────────────────────────────────────
    axis_kw = dict(
        showgrid=True, gridcolor="#1e1e1e", linecolor="#2a2a2a",
        color="#A2A2A2", tickfont=dict(family="DM Sans", size=10),
    )
    fig.update_xaxes(**axis_kw)
    fig.update_yaxes(**axis_kw)

    # Y-axis labels
    fig.update_yaxes(title_text=app_settings.graph_y_axis, title_font_size=12, row=1, col=1)
    fig.update_yaxes(title_text=gettext("XEMP Strength"), title_font_size=11, row=2, col=2)

    # X-axis types — limit history tick count to avoid cramped rotated labels
    fig.update_xaxes(type="date", nticks=6, tickangle=-30, row=1, col=1)
    fig.update_xaxes(type="date", title_text=datetime_partition_column,
                     title_font_size=11, row=1, col=2)

    # Eyebrow annotations
    for col, text, xref, xanchor in [
        (1, "HISTORY", "x domain", "left"),
        (2, "FORECAST", "x2 domain", "left"),
    ]:
        fig.add_annotation(
            text=text, xref=xref, yref="paper",
            x=0.01, y=1.01, xanchor=xanchor, yanchor="bottom",
            showarrow=False,
            font=dict(family="Fragment Mono, monospace", size=9, color="#81FBA5"),
        )

    _legend_style = dict(
        orientation="h",
        font=dict(family="DM Sans", size=10, color="#A2A2A2"),
        bgcolor="rgba(0,0,0,0)",
        tracegroupgap=2,
    )
    fig.update_layout(
        **_LAYOUT_BASE,
        height=760,
        barmode="relative",
        showlegend=True,
        # top legend: centered above the whole figure, inside the top margin
        legend=dict(
            **_legend_style,
            x=0.5,
            y=1.01,
            xanchor="center",
            yanchor="bottom",
        ),
        # bottom legend: centered below the stacked bar, with enough room for wrapped rows
        legend2=dict(
            **_legend_style,
            x=0.5,
            y=-0.04,
            xanchor="center",
            yanchor="top",
        ),
        margin=dict(l=50, r=30, b=140, t=55, pad=4),
        uniformtext_mode="hide",
    )
    fig.update_traces(connectgaps=False, selector=dict(type="scatter"))
    return fig.to_dict()  # type: ignore[no-any-return]


def _aggregate_scoring_data(scoring_data: list[dict[str, Any]]) -> pd.DataFrame:
    """Aggregate scoring data for plotting."""
    datetime_column = app_settings.datetime_partition_column
    date_format = app_settings.date_format

    return (
        pd.DataFrame(scoring_data)
        .groupby(datetime_column, dropna=True)
        .sum(min_count=1)
        .reset_index()
        .assign(
            timestamp=lambda x: pd.to_datetime(x[datetime_column], format=date_format)
        )
        .sort_values("timestamp")
    )


def get_pred_ex_df(preds: List[dict[str, Any]]) -> pd.DataFrame:
    preds_df = pd.DataFrame(preds)
    names = []
    strengths = []
    actual_values = []

    for i in range(1, 4):  # 1, 2, 3
        feature_col = f"EXPLANATION_{i}_FEATURE_NAME"
        if feature_col not in preds_df.columns:
            continue
        names.extend(preds_df[feature_col])
        strengths.extend(preds_df[f"EXPLANATION_{i}_STRENGTH"])
        actual_values.extend(preds_df[f"EXPLANATION_{i}_ACTUAL_VALUE"])

    pred_ex_df = pd.DataFrame(
        {"feature": names, "strength": strengths, "feature_value": actual_values}
    )
    # Rows with fewer meaningful drivers than max_explanations come back with NaN for
    # the unused ranks. Drop them, then force "feature" to string dtype explicitly -
    # a column that was entirely NaN stays float64 even after dropping every row,
    # which still breaks .str accessor usage downstream.
    pred_ex_df = pred_ex_df.dropna(subset=["feature"])
    pred_ex_df["feature"] = pred_ex_df["feature"].astype(str)
    return pred_ex_df


def get_pred_ex_stacked_bar_df(preds: List[dict[str, Any]]) -> pd.DataFrame:
    """Returns per-timestep per-feature XEMP strength, grouped for a stacked bar chart."""
    preds_df = pd.DataFrame(preds)
    date_col = app_settings.datetime_partition_column
    rows = []
    for i in range(1, 4):
        feature_col = f"EXPLANATION_{i}_FEATURE_NAME"
        strength_col = f"EXPLANATION_{i}_STRENGTH"
        if feature_col not in preds_df.columns:
            continue
        tmp = preds_df[[date_col, feature_col, strength_col]].rename(
            columns={date_col: "date_id", feature_col: "feature", strength_col: "strength"}
        )
        rows.append(tmp)
    if not rows:
        return pd.DataFrame(columns=["date_id", "feature", "strength"])
    combined = pd.concat(rows, ignore_index=True)
    return combined.groupby(["date_id", "feature"], as_index=False)["strength"].sum()


def get_llm_summary(predictions: List[dict[str, Any]]) -> ForecastSummary:
    """
    Generate summary and headline of the forecast from the LLM model.

    Processes a list of prediction dictionaries, extracts relevant
    features, strengths, and values, and then generates a summary and headline
    using a language model. It also creates an explanation dataset.

    Parameters
    ----------
    predictions : List[dict[str, Any]]
        A list of dictionaries containing prediction data. Each dictionary should
        have keys corresponding to feature names, strengths, and actual values.

    Returns
    -------
    ForecastSummary
        An object containing the headline, summary body, and explanation dataset.
    """

    # preds = pd.DataFrame(predictions)
    processed_preds = _process_predictions(predictions)
    pred_ex_df = get_pred_ex_df(predictions)

    # Create summary for target derived features
    include_target_summary = _summarize_dataframe(pred_ex_df, ex_target=False)

    # Create summary for exogenous features
    exclude_target_summary = _summarize_dataframe(pred_ex_df, ex_target=True)

    return ForecastSummary(
        headline=_make_headline(processed_preds),
        summary_body=include_target_summary + "\n\n\n" + exclude_target_summary,
    )


def get_explain_df(predictions: List[dict[str, Any]]) -> pd.DataFrame:
    pred_ex_df = get_pred_ex_df(predictions)
    include_target_prompt_df = assemble_prediction_explanations(
        pred_ex_df, ex_target=False
    )
    exclude_target_prompt_df = assemble_prediction_explanations(
        pred_ex_df, ex_target=True
    )

    explain_df = pd.concat((include_target_prompt_df, exclude_target_prompt_df)).rename(
        columns={
            "Relative Importance": "relative_importance",
            "index": "feature_name",
        }
    )
    explain_df = explain_df.sort_values(
        "relative_importance", ascending=False
    ).reset_index(drop=True)
    explain_df["Rank"] = range(1, len(explain_df) + 1)

    return explain_df


def assemble_prediction_explanations(
    explain_df: pd.DataFrame, ex_target: bool
) -> pd.DataFrame:
    target = app_settings.target
    if ex_target:
        explain_df = explain_df[
            ~explain_df["feature"].str.startswith(target + " (")
        ].copy()
    else:
        explain_df = explain_df[
            explain_df["feature"].str.startswith(target + " (")
        ].copy()
    prompt_df = get_top_features(explain_df)
    return prompt_df.assign(is_target_derived=not ex_target).reset_index()


def get_top_features(
    prediction_explanations_df: pd.DataFrame,
    top_feature_threshold: int = 75,
    top_n_features: int = 4,
) -> pd.DataFrame:
    total_strength = (
        prediction_explanations_df.groupby("feature")["strength"]
        .apply(lambda c: c.abs().sum())
        .sort_values(ascending=False)
    )
    total_strength = ((total_strength / total_strength.sum()) * 100).astype(int)
    total_strength.index.name = None
    cum_total_strength = total_strength.cumsum()
    top_features = pd.DataFrame(
        total_strength[cum_total_strength.shift(1).fillna(0) < top_feature_threshold][
            :top_n_features
        ],
    ).rename(columns={"strength": "relative_importance"})
    top_features["Rank"] = range(1, 1 + len(top_features))

    return top_features


def _summarize_dataframe(prompt_dataframe: pd.DataFrame, ex_target: bool) -> str:
    """
    Get the LLM sub-summary for the forecast.
    """
    target = app_settings.target
    if ex_target:
        prompt = gettext(
            "The following are the most important exogenous features "
            + "in the forecasting model's predictions of `{target}`. "
            + "Provide a 3-4 sentence summary of the exogenous "
            + "driver(s) for the forecast, explain any potential "
            + "intuitive, qualitative interpretation(s) "
            + "or explanation(s)."
        ).format(target=target)
        explain_df = prompt_dataframe[
            ~prompt_dataframe["feature"].str.startswith(target + " (")
        ].copy()
    else:
        prompt = gettext(
            "The following are the most important features in the "
            + "forecasting model's predictions. Provide a 3-4 sentence "
            + "summary of the key cyclical and/or trend drivers for the "
            + "forecast, explain any potential intuitive,qualitative "
            + "interpretations or explanations."
        )
        explain_df = prompt_dataframe[
            prompt_dataframe["feature"].str.startswith(target + " (")
        ].copy()
    prompt_string = _get_prompt(
        explain_df,
        prompt,
    )
    prompt_completion = _get_completion(prompt_string, temperature=0)
    return prompt_completion


def _get_prompt(
    prediction_explanations_df: pd.DataFrame,
    prompt: str,
) -> str:
    """Build prompt to summarize prediction explanations data."""
    top_features = get_top_features(
        prediction_explanations_df=prediction_explanations_df
    )
    top_features_string = top_features.drop(columns="relative_importance").to_string()

    return prompt + f"\n\n\n{top_features_string}"


def _make_headline(standardized_predictions: list[PredictionRow]) -> str:
    """Generate subheader for explanation."""
    if not standardized_predictions:
        return gettext("No forecast data available for the selected filters.")
    df = pd.DataFrame([i.model_dump() for i in standardized_predictions])
    return _get_completion(
        prompt=gettext("Forecast:") + str(df[["date_id", "prediction"]]),
        system_prompt=app_settings.headline_prompt,
        temperature=0.2,
    )


def share_access(emails: List[str]) -> None:
    """Share application with other users."""
    client = dr.Client()
    try:
        application_id = Application().id
    except ValidationError as e:
        raise ValidationError(
            "Application ID not found. Have you deployed it with pulumi up?"
        ) from e
    url = f"customApplications/{application_id}/sharedRoles"
    roles = [
        {"role": "CONSUMER", "shareRecipientType": "user", "username": email}
        for email in emails
    ]
    payload = {"operation": "updateRoles", "roles": roles}
    client.patch(url, json=payload)
