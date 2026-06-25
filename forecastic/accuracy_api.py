from __future__ import annotations

import functools
import re
from pathlib import Path
from typing import Any, Optional

import datarobot as dr
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from forecastic.api import (
    BAR_COLORS,
    LLMNotAvailableException,
    _AXIS_STYLE,
    _LAYOUT_BASE,
    _get_completion,
    app_settings,
    time_series_deployment_id,
)
from forecastic.schema import AccuracySummary, WhatIfFeature

_PRED_COL = "SKILL_OFFERED_SUM (actual)_PREDICTION"
_SKILL_GREY = "#606060"
_ACTUAL_GREEN = "#81FBA5"
_FORECAST_BLUE = "#44BFFC"
_FORECAST_PURPLE = "#909BF5"


# ── Data helpers ──────────────────────────────────────────────────────────────

def load_accuracy_data(
    cache_path: Path, actuals_path: Path
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Return (planned_df, actual_inputs_df, actuals_df)."""
    cache_full = pd.read_csv(cache_path)
    for col in ["START_OF_WEEK", "prediction_week"]:
        cache_full[col] = cache_full[col].astype(str).str[:10]
    cache_full["FORECAST_DISTANCE"] = pd.to_numeric(cache_full["FORECAST_DISTANCE"], errors="coerce")
    planned_df = cache_full[cache_full["scenario"] == "planned"].copy()
    actual_inputs_df = cache_full[cache_full["scenario"] == "actual"].copy()
    actuals_df = pd.read_csv(actuals_path) if actuals_path.exists() else pd.DataFrame()
    if not actuals_df.empty:
        actuals_df["START_OF_WEEK"] = actuals_df["START_OF_WEEK"].astype(str).str[:10]
    return planned_df, actual_inputs_df, actuals_df


def get_target_weeks(
    cache_df: pd.DataFrame,
    actuals_df: pd.DataFrame | None = None,
) -> list[str]:
    """Target weeks sorted most-recent-first.

    Only includes weeks with ≥2 distinct forecast distances (needed for a
    meaningful accuracy curve). If actuals_df is provided, further restricts
    to weeks that have an observed actual value.
    """
    counts = cache_df.groupby("START_OF_WEEK")["FORECAST_DISTANCE"].nunique()
    candidates = set(counts[counts >= 2].index.tolist())
    if actuals_df is not None and not actuals_df.empty:
        actual_weeks = set(actuals_df["START_OF_WEEK"].astype(str).str[:10].unique())
        candidates &= actual_weeks
    return sorted(candidates, reverse=True)  # most recent first


def filter_to_week(cache_df: pd.DataFrame, target_week: str) -> pd.DataFrame:
    df = cache_df[cache_df["START_OF_WEEK"] == target_week].copy()
    return df.sort_values("FORECAST_DISTANCE", ascending=False).reset_index(drop=True)


def get_actual_value(actuals_df: pd.DataFrame, target_week: str) -> float | None:
    if actuals_df.empty:
        return None
    row = actuals_df[actuals_df["START_OF_WEEK"] == target_week]
    if row.empty:
        return None
    return float(row["SKILL_OFFERED_SUM"].iloc[0])


# ── Color map ─────────────────────────────────────────────────────────────────

def build_accuracy_color_map(week_df: pd.DataFrame) -> dict[str, str]:
    features: list[str] = []
    for i in range(1, 11):
        col = f"EXPLANATION_{i}_FEATURE_NAME"
        if col in week_df.columns:
            for f in week_df[col].dropna().unique():
                cleaned = re.sub(r"\s*\(actual\)\s*$", "", str(f)).strip()
                if cleaned not in features:
                    features.append(cleaned)

    color_map: dict[str, str] = {}
    palette_idx = 0
    for feat in features:
        if "SKILL_OFFERED_SUM" in feat:
            color_map[feat] = _SKILL_GREY
        else:
            color_map[feat] = BAR_COLORS[palette_idx % len(BAR_COLORS)]
            palette_idx += 1
    return color_map


# ── Chart 1: Accuracy line chart ──────────────────────────────────────────────

def build_accuracy_line_chart(
    week_df: pd.DataFrame,
    actual_value: float | None,
    selected_distances: list[int],
    show_all: bool = False,
    actual_inputs_week_df: pd.DataFrame | None = None,
) -> dict[str, Any]:
    distances_in_data = sorted(week_df["FORECAST_DISTANCE"].dropna().astype(int).unique(), reverse=True)
    if show_all:
        active_distances = distances_in_data
    else:
        active_distances = [d for d in distances_in_data if d in selected_distances]

    plot_df = week_df[week_df["FORECAST_DISTANCE"].astype(int).isin(active_distances)].copy()
    plot_df = plot_df.sort_values("FORECAST_DISTANCE", ascending=False)

    x_labels_fc = [f"FD {int(d)}" for d in plot_df["FORECAST_DISTANCE"]]
    y_vals_fc = plot_df[_PRED_COL].tolist()
    hover_fc = [
        f"Forecast point: {row['prediction_week']}<br>Prediction: {row[_PRED_COL]:,.0f}"
        for _, row in plot_df.iterrows()
    ]

    fig = go.Figure()

    # Planned forecast line (blue, solid)
    fig.add_trace(go.Scatter(
        x=x_labels_fc,
        y=y_vals_fc,
        mode="lines+markers",
        name="Forecast — planned inputs",
        line=dict(color=_FORECAST_BLUE, width=2),
        marker=dict(color=_FORECAST_BLUE, size=9),
        hovertext=hover_fc,
        hovertemplate="%{hovertext}<extra></extra>",
    ))

    # Actual inputs forecast line (purple, dashed)
    if actual_inputs_week_df is not None and not actual_inputs_week_df.empty:
        ai_plot = actual_inputs_week_df[
            actual_inputs_week_df["FORECAST_DISTANCE"].astype(int).isin(active_distances)
        ].sort_values("FORECAST_DISTANCE", ascending=False)
        if not ai_plot.empty:
            ai_x = [f"FD {int(d)}" for d in ai_plot["FORECAST_DISTANCE"]]
            ai_y = ai_plot[_PRED_COL].tolist()
            ai_hover = [
                f"Forecast point: {row['prediction_week']}<br>Prediction (actual inputs): {row[_PRED_COL]:,.0f}"
                for _, row in ai_plot.iterrows()
            ]
            fig.add_trace(go.Scatter(
                x=ai_x,
                y=ai_y,
                mode="lines+markers",
                name="Forecast — actual inputs",
                line=dict(color=_FORECAST_PURPLE, width=2, dash="dash"),
                marker=dict(color=_FORECAST_PURPLE, size=9, symbol="circle-open"),
                hovertext=ai_hover,
                hovertemplate="%{hovertext}<extra></extra>",
            ))

    # Actual observed point (green diamond)
    if actual_value is not None:
        fig.add_trace(go.Scatter(
            x=["Actual"],
            y=[actual_value],
            mode="markers",
            name="Actual observed",
            marker=dict(color=_ACTUAL_GREEN, size=14, symbol="diamond"),
            hovertemplate=f"Actual: {actual_value:,.0f}<extra></extra>",
        ))
        fig.add_hline(
            y=actual_value,
            line_dash="dash",
            line_color=_SKILL_GREY,
            line_width=1,
            annotation_text=f"Actual: {actual_value:,.0f}",
            annotation_position="top left",
            annotation_font_color=_ACTUAL_GREEN,
        )

    # Extend x-axis to include "Actual" tick even when actual_value is None
    all_x = x_labels_fc + (["Actual"] if actual_value is not None else [])
    axis = {**_AXIS_STYLE, "type": "category", "categoryorder": "array", "categoryarray": all_x}
    fig.update_layout(
        **_LAYOUT_BASE,
        xaxis={**axis, "title": "Forecast Distance (most distant → nearest → Actual)"},
        yaxis={**_AXIS_STYLE, "title": app_settings.graph_y_axis},
        legend=dict(orientation="h", yanchor="top", y=-0.18, xanchor="left", x=0),
        margin=dict(l=60, r=20, t=30, b=80),
    )
    return fig.to_dict()


# ── Chart 2: XEMP by distance ─────────────────────────────────────────────────

def _extract_xemp_features(row: pd.Series, df_cols: list[str]) -> list[tuple[str, float]]:
    """Extract (feature, strength) pairs from one cache row."""
    result = []
    for i in range(1, 11):
        feat_col = f"EXPLANATION_{i}_FEATURE_NAME"
        str_col = f"EXPLANATION_{i}_STRENGTH"
        if feat_col not in df_cols or pd.isna(row.get(feat_col)):
            continue
        feat = re.sub(r"\s*\(actual\)\s*$", "", str(row[feat_col])).strip()
        strength = float(row[str_col]) if not pd.isna(row.get(str_col)) else 0.0
        result.append((feat, strength))
    return result


def build_xemp_by_distance(
    week_df: pd.DataFrame,
    selected_distances: list[int],
    color_map: dict[str, str] | None = None,
    actual_week_df: pd.DataFrame | None = None,
) -> dict[str, Any]:
    distances_in_data = sorted(week_df["FORECAST_DISTANCE"].dropna().astype(int).unique(), reverse=True)
    active_distances = [d for d in distances_in_data if d in selected_distances]
    if not active_distances:
        fig = go.Figure()
        fig.update_layout(**_LAYOUT_BASE)
        return fig.to_dict()

    show_actual = actual_week_df is not None and not actual_week_df.empty
    n_cols = len(active_distances)
    subplot_titles = []
    for d in active_distances:
        row = week_df[week_df["FORECAST_DISTANCE"].astype(int) == d]
        if not row.empty:
            pw = row["prediction_week"].iloc[0]
            subplot_titles.append(f"FD {d} — made {pw}")
        else:
            subplot_titles.append(f"FD {d}")

    fig = make_subplots(
        rows=1, cols=n_cols,
        horizontal_spacing=0.12 if n_cols <= 2 else 0.08,
        subplot_titles=subplot_titles,
    )

    all_dfs = [week_df] + ([actual_week_df] if show_actual else [])
    combined = pd.concat(all_dfs, ignore_index=True)
    if color_map is None:
        color_map = build_accuracy_color_map(combined)

    planned_legend_added: set[str] = set()
    df_cols = list(week_df.columns)
    ai_cols = list(actual_week_df.columns) if show_actual else []

    for col_idx, dist in enumerate(active_distances, start=1):
        p_row_df = week_df[week_df["FORECAST_DISTANCE"].astype(int) == dist]
        if p_row_df.empty:
            continue
        p_row = p_row_df.iloc[0]
        planned_fs = _extract_xemp_features(p_row, df_cols)
        # Sort planned bars by strength ascending (most negative at bottom)
        planned_fs.sort(key=lambda x: x[1])

        # Planned bars
        for feat, strength in planned_fs:
            color = color_map.get(feat, BAR_COLORS[0])
            show_legend = feat not in planned_legend_added
            if show_legend:
                planned_legend_added.add(feat)
            fig.add_trace(go.Bar(
                x=[strength],
                y=[feat],
                orientation="h",
                name=feat,
                legendgroup=feat,
                showlegend=show_legend,
                marker=dict(color=color),
                hovertemplate="<b>%{y}</b> — planned<br>Strength: %{x:,.0f}<extra></extra>",
            ), row=1, col=col_idx)

        # Actual inputs bars (hatched, same color, grouped alongside planned)
        if show_actual:
            ai_row_df = actual_week_df[actual_week_df["FORECAST_DISTANCE"].astype(int) == dist]
            if not ai_row_df.empty:
                ai_row = ai_row_df.iloc[0]
                ai_fs = _extract_xemp_features(ai_row, ai_cols)
                ai_dict = dict(ai_fs)
                for feat, _ in planned_fs:
                    ai_strength = ai_dict.get(feat, 0.0)
                    color = color_map.get(feat, BAR_COLORS[0])
                    fig.add_trace(go.Bar(
                        x=[ai_strength],
                        y=[feat],
                        orientation="h",
                        name=feat,
                        legendgroup=feat,
                        showlegend=False,
                        marker=dict(
                            color=color,
                            opacity=0.45,
                            pattern=dict(shape="/", fgcolor="rgba(255,255,255,0.25)", size=4),
                        ),
                        hovertemplate="<b>%{y}</b> — actual inputs<br>Strength: %{x:,.0f}<extra></extra>",
                    ), row=1, col=col_idx)

    # Scenario key: two invisible scatter traces as legend anchors
    if show_actual:
        fig.add_trace(go.Scatter(
            x=[None], y=[None], mode="markers",
            marker=dict(color="#A2A2A2", size=10, symbol="square"),
            name="▪ Planned inputs", showlegend=True, legendgroup="_scenario_p",
        ))
        fig.add_trace(go.Scatter(
            x=[None], y=[None], mode="markers",
            marker=dict(color="#A2A2A2", size=10, symbol="square-open"),
            name="▫ Actual inputs (hatched)", showlegend=True, legendgroup="_scenario_a",
        ))

    fig.update_layout(
        **_LAYOUT_BASE,
        barmode="group",
        height=460,
        legend=dict(
            orientation="v",
            yanchor="middle",
            y=0.5,
            xanchor="left",
            x=1.01,
            font=dict(size=10, family="DM Sans"),
            bgcolor="rgba(0,0,0,0)",
            tracegroupgap=4,
        ),
        margin=dict(l=20, r=220, t=50, b=20),
    )
    for i in range(1, n_cols + 1):
        fig.update_xaxes(**_AXIS_STYLE, title_text="XEMP Strength" if i == 1 else "", row=1, col=i)
        fig.update_yaxes(**_AXIS_STYLE, row=1, col=i)

    return fig.to_dict()


# ── Model metadata ───────────────────────────────────────────────────────────

@functools.lru_cache(maxsize=1)
def _get_model_context_str() -> str:
    """Fetch champion model metrics from DR once per session; return formatted prompt text."""
    lines = [
        f"Champion model: {app_settings.model_name}",
        f"Feature derivation window: {app_settings.feature_derivation_window_start} to "
        f"{app_settings.feature_derivation_window_end} weeks relative to forecast point",
        f"Forecast horizon: {app_settings.forecast_window_start}–{app_settings.forecast_window_end} weeks ahead",
    ]
    try:
        model = dr.Model.get(app_settings.project_id, app_settings.model_id)
        metrics = model.metrics or {}
        for metric_name in ["MASE", "MAE", "RMSE"]:
            val = metrics.get(metric_name, {}).get("backtesting")
            if val is not None:
                lines.append(f"Backtesting {metric_name}: {val:.4f}")
        # Training period from the deployment's champion
        try:
            deployment = dr.Deployment.get(time_series_deployment_id)
            champ = deployment.model or {}
            if champ.get("trainingStartDate") and champ.get("trainingEndDate"):
                lines.append(
                    f"Champion training period: {champ['trainingStartDate'][:10]} → "
                    f"{champ['trainingEndDate'][:10]}"
                )
        except Exception:
            pass
    except Exception:
        pass
    return "\n".join(f"- {l}" for l in lines)


@functools.lru_cache(maxsize=1)
def _get_feature_impact_str() -> str:
    """Return all features from the champion model's feature impact list.

    Sorted by normalized impact descending. (actual) suffix stripped.
    Cached once per session — used so the LLM knows which features already exist
    in the training project and does not suggest adding them.
    """
    _clean = lambda n: re.sub(r"\s*\(actual\)\s*$", "", str(n)).strip()
    try:
        model = dr.Model.get(app_settings.project_id, app_settings.model_id)
        impacts = model.get_feature_impact()
        # impacts is a list of dicts: {featureName, impactNormalized, impactUnnormalized}
        # Sort by normalized impact descending
        impacts_sorted = sorted(
            impacts, key=lambda x: float(x.get("impactNormalized") or 0), reverse=True
        )
        entries = [
            f"{_clean(x['featureName'])} ({float(x['impactNormalized']):.2f})"
            for x in impacts_sorted
            if x.get("featureName")
        ]
        return f"All {len(entries)} features in training project (name, normalised impact):\n" + ", ".join(entries)
    except Exception:
        # Fall back to the important_features from app_settings (already loaded)
        entries = [
            f"{_clean(f['featureName'])} ({f['impactNormalized']:.2f})"
            for f in sorted(
                app_settings.important_features,
                key=lambda x: float(x.get("impactNormalized") or 0),
                reverse=True,
            )
            if f.get("featureName")
        ]
        note = f"Top {len(entries)} features by impact (full DR feature impact unavailable):\n"
        return note + ", ".join(entries)


# ── LLM summary ───────────────────────────────────────────────────────────────

def _build_distance_table(week_df: pd.DataFrame) -> str:
    cols = ["FORECAST_DISTANCE", "prediction_week", _PRED_COL]
    available = [c for c in cols if c in week_df.columns]
    df = week_df[available].sort_values("FORECAST_DISTANCE", ascending=False).copy()
    df[_PRED_COL] = df[_PRED_COL].map(lambda v: f"{v:,.0f}" if pd.notna(v) else "N/A")
    return df.to_string(index=False)


def _build_xemp_table(week_df: pd.DataFrame, top_n: int = 5) -> str:
    rows = []
    for _, r in week_df.sort_values("FORECAST_DISTANCE", ascending=False).iterrows():
        dist = int(r["FORECAST_DISTANCE"])
        features = []
        for i in range(1, 11):
            fn = f"EXPLANATION_{i}_FEATURE_NAME"
            fs = f"EXPLANATION_{i}_STRENGTH"
            if fn not in r or pd.isna(r[fn]):
                continue
            feat = re.sub(r"\s*\(actual\)\s*$", "", str(r[fn])).strip()
            strength = float(r[fs]) if not pd.isna(r.get(fs)) else 0.0
            features.append((feat, strength))
        features.sort(key=lambda x: abs(x[1]), reverse=True)
        for feat, strength in features[:top_n]:
            rows.append({"FORECAST_DISTANCE": dist, "feature": feat, "XEMP_strength": f"{strength:+,.0f}"})
    if not rows:
        return "No XEMP data available."
    return pd.DataFrame(rows).to_string(index=False)


def get_accuracy_llm_summary(
    week_df: pd.DataFrame,
    actual_value: float | None,
    target_week: str,
    what_if_features: list,
) -> AccuracySummary:
    # Support both WhatIfFeature Pydantic objects and plain dicts
    def _name(f: Any) -> str:
        return f.feature_name if hasattr(f, "feature_name") else f["feature_name"]

    def _known(f: Any) -> bool:
        v = f.known_in_advance if hasattr(f, "known_in_advance") else f.get("known_in_advance")
        return v is True

    known_features = [_name(f) for f in what_if_features if _known(f)]
    known_in_advance = ", ".join(known_features) if known_features else "none configured"

    model_context = _get_model_context_str()
    feature_list = _get_feature_impact_str()

    system_prompt = (
        "You are a workforce management analyst for a Netherlands-based contact center.\n\n"
        "Deployed model metadata:\n"
        f"{model_context}\n\n"
        "Forecast context:\n"
        "- Target: SKILL_OFFERED_SUM — total call volume offered to agents per week (TECH skill group).\n"
        "- XEMP strength = feature contribution to the prediction vs baseline. "
        "Positive = pushes forecast up; negative = pushes it down. Larger |strength| = stronger influence.\n"
        f"- Known-in-advance features (operational plans set before the forecast week): {known_in_advance}.\n"
        "  These are inputs the WFM team controls or can observe before the forecast week arrives. "
        "  If their XEMP strength is low or absent, the forecast is driven by patterns the team cannot proactively adjust.\n\n"
        f"{feature_list}\n"
        "IMPORTANT: Do NOT suggest adding features that already appear in the list above. "
        "Only recommend adding a genuinely new signal, adjusting how an existing feature is derived "
        "(e.g. window length, lag depth), or operational process changes.\n\n"
        "Be concise and specific — plain language for a business audience. "
        "Never use vague time references like 'periodically' or 'every few weeks'."
    )

    dist_table = _build_distance_table(week_df)
    xemp_table = _build_xemp_table(week_df)
    actual_str = f"{actual_value:,.0f}" if actual_value is not None else "not available"

    base_context = (
        f"Target week: {target_week}. Actual call volume: {actual_str}.\n\n"
        f"Forecast at each distance (most distant first):\n{dist_table}\n\n"
        f"Top-5 XEMP features at each distance:\n{xemp_table}"
    )

    narrative_prompt = (
        f"{base_context}\n\n"
        "In 4–5 sentences:\n"
        "- How does the forecast change as it gets closer to the target week? Does it converge, diverge, or stay stable?\n"
        "- At which distance does the forecast make its biggest change?\n"
        "- Which features shift most in importance across distances? "
        "Are any known-in-advance features (listed in your context) influencing how the forecast evolves?\n"
        "Write as plain prose. Do not use numbered sections or headers."
    )

    # Determine FD=1 prediction for the gap calculation
    fd1_rows = week_df[week_df["FORECAST_DISTANCE"].astype(int) == 1]
    fd1_val = float(fd1_rows[_PRED_COL].iloc[0]) if not fd1_rows.empty else None
    gap_str = ""
    if fd1_val is not None and actual_value is not None:
        gap = actual_value - fd1_val
        gap_str = f"\nFinal forecast (FD=1): {fd1_val:,.0f}. Actual: {actual_str}. Remaining gap: {gap:+,.0f} calls."

    mitigation_prompt = (
        f"{base_context}{gap_str}\n\n"
        "Suggest 2–3 most likely reasons the FD=1 forecast still differs from the actual, "
        "and one concrete mitigation action per reason "
        "(e.g. add a new feature, adjust the feature derivation window, flag certain weeks for manual override). "
        "Write as a short bullet list. No intro sentence, no headers."
    )

    headline_prompt = (
        f"Target week: {target_week}. Actual: {actual_str}.\n"
        f"Forecast at each distance:\n{dist_table}\n"
        "Write exactly one sentence summarising how the forecast evolves across distances "
        "and its final gap to the actual for a business audience. "
        "Be specific — mention direction and approximate magnitude. No quotation marks."
    )

    headline = _get_completion(headline_prompt, system_prompt=system_prompt, temperature=0.2)
    accuracy_narrative = _get_completion(narrative_prompt, system_prompt=system_prompt, temperature=0)
    mitigation_actions = _get_completion(mitigation_prompt, system_prompt=system_prompt, temperature=0)

    return AccuracySummary(
        headline=headline,
        accuracy_narrative=accuracy_narrative,
        mitigation_actions=mitigation_actions,
    )
