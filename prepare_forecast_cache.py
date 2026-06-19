"""
prepare_forecast_cache.py
─────────────────────────────────────────────────────────────────────────────
For each prediction week W in the selected horizon, builds two time-series
scoring inputs and runs predictions:

  PLANNED scenario
    FDW (−6…0 wks): actual historical values   ← always from ACTUAL file
    FW  (+1…+13 wks): PLANNED feature values    ← from PLANNED file, target=NaN

  ACTUAL scenario  (playback with perfect inputs)
    FDW (−6…0 wks): actual historical values   ← from ACTUAL file
    FW  (+1…+13 wks): ACTUAL feature values     ← from ACTUAL file, target=NaN

Output: frontend/data/forecast_cache.csv
  Columns: prediction_week, scenario, START_OF_WEEK, forecast_step,
           + all prediction columns (SKILL_OFFERED_SUM_PREDICTION, intervals,
             EXPLANATION_N_FEATURE_NAME, EXPLANATION_N_STRENGTH)
           + all FW input feature columns

Usage
─────
  # default: April 2026 (4 weeks)
  set -a && source .env && set +a
  python prepare_forecast_cache.py

  # custom range (any Mondays in the source files)
  python prepare_forecast_cache.py --start 2026-05-04 --end 2026-05-25

  # full range auto-detected from source data (all weeks with real actual history)
  python prepare_forecast_cache.py --all

  # append new weeks to existing cache (avoids re-running already-cached weeks)
  python prepare_forecast_cache.py --start 2026-05-05 --end 2026-06-08 --append
"""
from __future__ import annotations

import argparse
import base64
import os
import sys
from pathlib import Path

import datarobot as dr
import pandas as pd
from datarobot_predict.deployment import predict

# ── Paths ─────────────────────────────────────────────────────────────────────
ROOT      = Path(__file__).parent
# Write cache into frontend/data/ so it is included in the deployed app bundle
DATA_DIR  = ROOT / "frontend" / "data"
CACHE_FILE = DATA_DIR / "forecast_cache.csv"

# Source data lives at repo root/data/
_SRC_DATA_DIR = ROOT / "data"
PLANNED_FILE = _SRC_DATA_DIR / "FOR APP TEST WFM DATA PLANNED FEATURES SET TECH_ 31-03-2026_6a326e0a76da3420b0d4e6e2.csv"
ACTUAL_FILE  = _SRC_DATA_DIR / "FOR APP TEST WFM DATA ACTUAL FEATURES SET TECH_ 17-06-2026_6a326f4d347b28ea2e55f573.csv"

# ── DR AI Catalog fallback IDs (used when local files are absent) ─────────────
PLANNED_DATASET_ID = os.environ.get("PLANNED_DATASET_ID", "6a326e0a76da3420b0d4e6e1")
ACTUAL_DATASET_ID  = os.environ.get("ACTUAL_DATASET_ID",  "6a326f4d347b28ea2e55f572")

# ── DR settings ───────────────────────────────────────────────────────────────
DEPLOYMENT_ID    = os.environ.get("FORECAST_DEPLOYMENT_ID", "6a0ec47bf306847615758fa3")
DR_ENDPOINT      = os.environ.get("DATAROBOT_ENDPOINT",     "https://app.eu.datarobot.com/api/v2/")
DR_TOKEN_RAW     = os.environ.get("DATAROBOT_API_TOKEN",    "")

# ── Time-series model settings (must match training) ──────────────────────────
DATE_COL         = "START_OF_WEEK"
TARGET_COL       = "SKILL_OFFERED_SUM"
SERIES_COL       = "SKILL"
FDW_START_WKS    = -6    # feature derivation window start (weeks)
FDW_END_WKS      = 0     # feature derivation window end
FW_START_WKS     = 1     # forecast window start
FW_END_WKS       = 13    # forecast window end
MAX_EXPLANATIONS = 10

# ── Default horizon: 4 weeks of April 2026 ────────────────────────────────────
DEFAULT_START = "2026-04-06"
DEFAULT_END   = "2026-04-27"


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _init_dr_client() -> None:
    """Initialise DR client. In a Codespace, credentials are pre-configured so no token needed."""
    try:
        dr.Client()
        print(f"  DR client ready  (codespace auth)")
        return
    except Exception:
        pass
    # Fallback: explicit token from environment (local / non-codespace use)
    token = DR_TOKEN_RAW
    try:
        decoded = base64.b64decode(token).decode("utf-8")
        if ":" in decoded:
            token = decoded
    except Exception:
        pass
    dr.Client(token=token, endpoint=DR_ENDPOINT)
    print(f"  DR client ready  →  {DR_ENDPOINT}")


def _load_data() -> tuple[pd.DataFrame, pd.DataFrame]:
    """Load and normalise both source files. Falls back to DR AI Catalog if local files absent."""
    if PLANNED_FILE.exists() and ACTUAL_FILE.exists():
        print(f"  Loading from local files")
        planned = pd.read_csv(PLANNED_FILE, parse_dates=[DATE_COL])
        actual  = pd.read_csv(ACTUAL_FILE,  parse_dates=[DATE_COL])
    else:
        print(f"  Local files not found — downloading from DR AI Catalog")
        print(f"    Planned dataset ID: {PLANNED_DATASET_ID}")
        print(f"    Actual  dataset ID: {ACTUAL_DATASET_ID}")
        planned = dr.Dataset.get(PLANNED_DATASET_ID).get_as_dataframe()
        actual  = dr.Dataset.get(ACTUAL_DATASET_ID).get_as_dataframe()
        planned[DATE_COL] = pd.to_datetime(planned[DATE_COL])
        actual[DATE_COL]  = pd.to_datetime(actual[DATE_COL])

    # Ensure ASSOCIATION_ID exists in both
    for df in (planned, actual):
        if "ASSOCIATION_ID" not in df.columns:
            df["ASSOCIATION_ID"] = (
                df[SERIES_COL].astype(str) + "_"
                + df[DATE_COL].dt.strftime("%Y-%m-%d")
            )

    # Normalise date column to date-only string for clean joining later
    for df in (planned, actual):
        df[DATE_COL] = df[DATE_COL].dt.strftime("%Y-%m-%d")

    planned.set_index(DATE_COL, inplace=True)
    actual.set_index(DATE_COL,  inplace=True)

    print(f"  Planned: {len(planned)} rows  "
          f"({planned.index.min()} → {planned.index.max()})")
    print(f"  Actual:  {len(actual)} rows  "
          f"({actual.index.min()} → {actual.index.max()})")
    return planned, actual


def _weekly_dates_between(start: str, end: str, source_index: pd.Index) -> list[str]:
    """Return Mondays within [start, end] that exist in the source data."""
    all_dates = sorted(source_index)
    return [d for d in all_dates if start <= d <= end]


def _full_valid_range(
    actual_df: pd.DataFrame,
) -> tuple[str, str]:
    """
    Derive the widest meaningful prediction-week range from source data.

    First valid week: first source date + FDW_START_WKS weeks (need 6 weeks of history).
    Last valid week:  last date in actual_df that has a non-NaN target value
                      (so the FDW contains real observed history, not future planned rows).
    """
    all_dates = sorted(actual_df.index)
    first_date = pd.Timestamp(all_dates[0])
    first_valid = (first_date - pd.Timedelta(weeks=FDW_START_WKS)).strftime("%Y-%m-%d")

    # Last date with a real observed target
    has_target = actual_df[TARGET_COL].notna()
    actual_dates = sorted(actual_df[has_target].index)
    last_valid = actual_dates[-1] if actual_dates else all_dates[-1]

    # Clamp first_valid to dates actually present in the source
    first_valid = next((d for d in all_dates if d >= first_valid), all_dates[0])

    print(f"  Auto-detected range: {first_valid} → {last_valid}")
    return first_valid, last_valid


def _build_scoring_input(
    prediction_week: str,
    actual_df: pd.DataFrame,
    fw_df: pd.DataFrame,          # planned_df or actual_df for FW
) -> pd.DataFrame:
    """
    Assemble a single scoring payload for prediction_week.

    Returns a DataFrame with 7 FDW rows + up to 13 FW rows.
    FW rows have TARGET_COL = NaN (unknown future).
    """
    pred_dt  = pd.Timestamp(prediction_week)
    fdw_start = (pred_dt + pd.Timedelta(weeks=FDW_START_WKS)).strftime("%Y-%m-%d")
    fdw_end   = prediction_week
    fw_start  = (pred_dt + pd.Timedelta(weeks=FW_START_WKS)).strftime("%Y-%m-%d")
    fw_end    = (pred_dt + pd.Timedelta(weeks=FW_END_WKS)).strftime("%Y-%m-%d")

    # FDW: history from ACTUAL, keep target
    fdw_rows = actual_df.loc[
        (actual_df.index >= fdw_start) & (actual_df.index <= fdw_end)
    ].copy().reset_index()

    # FW: future features from fw_df, clear target
    fw_rows = fw_df.loc[
        (fw_df.index >= fw_start) & (fw_df.index <= fw_end)
    ].copy().reset_index()
    fw_rows[TARGET_COL] = float("nan")

    scoring = pd.concat([fdw_rows, fw_rows], ignore_index=True)

    # Refresh ASSOCIATION_ID after concat (index was reset)
    scoring["ASSOCIATION_ID"] = (
        scoring[SERIES_COL].astype(str) + "_"
        + scoring[DATE_COL].astype(str)
    )

    if len(fdw_rows) == 0:
        raise ValueError(
            f"No FDW rows found for prediction week {prediction_week}. "
            f"Need data from {fdw_start} onward."
        )
    if len(fw_rows) == 0:
        raise ValueError(
            f"No FW rows found after {prediction_week}. "
            f"Check that source files extend to {fw_end}."
        )

    print(f"    scoring input: {len(fdw_rows)} FDW rows + {len(fw_rows)} FW rows "
          f"= {len(scoring)} total")
    return scoring, fw_rows.reset_index(drop=True)


def _run_predict(scoring_input: pd.DataFrame, deployment: dr.Deployment) -> pd.DataFrame:
    """Call DR predict API and return result dataframe."""
    result = predict(
        deployment=deployment,
        data_frame=scoring_input,
        max_explanations=MAX_EXPLANATIONS,
    )
    return result.dataframe


def _find_prediction_col(df: pd.DataFrame) -> str:
    candidates = [f"{TARGET_COL}_PREDICTION", "PREDICTION", "prediction"]
    for c in candidates:
        if c in df.columns:
            return c
    for c in df.columns:
        if c.upper().endswith("_PREDICTION"):
            return c
    raise KeyError(f"No prediction column found in {list(df.columns)}")


def _enrich_predictions(
    preds_df: pd.DataFrame,
    fw_input_rows: pd.DataFrame,
    prediction_week: str,
    scenario: str,
) -> pd.DataFrame:
    """
    Merge predictions with FW input features and add metadata columns.
    """
    # Normalise date in preds
    preds_df = preds_df.copy()
    preds_df[DATE_COL] = preds_df[DATE_COL].astype(str).str[:10]

    fw_input_rows = fw_input_rows.copy()
    fw_input_rows[DATE_COL] = fw_input_rows[DATE_COL].astype(str).str[:10]

    # Add forecast step
    pred_dt = pd.Timestamp(prediction_week)
    fw_dates = pd.to_datetime(fw_input_rows[DATE_COL])
    fw_input_rows["forecast_step"] = ((fw_dates - pred_dt).dt.days / 7).astype(int)

    merged = preds_df.merge(
        fw_input_rows[[DATE_COL, "forecast_step"] +
                      [c for c in fw_input_rows.columns
                       if c not in preds_df.columns and c != "forecast_step"]],
        on=DATE_COL,
        how="left",
    )
    merged.insert(0, "scenario",        scenario)
    merged.insert(0, "prediction_week", prediction_week)
    return merged


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def prepare_cache(
    start_date: str | None,
    end_date: str | None,
    append_to_existing: bool = False,
) -> pd.DataFrame:
    print("\n── Initialising ─────────────────────────────────────────────")
    _init_dr_client()

    print("\n── Loading source files ──────────────────────────────────────")
    planned_df, actual_df = _load_data()

    if start_date is None or end_date is None:
        print("\n── Auto-detecting full valid range ───────────────────────────")
        start_date, end_date = _full_valid_range(actual_df)

    prediction_weeks = _weekly_dates_between(start_date, end_date, actual_df.index)
    if not prediction_weeks:
        raise ValueError(
            f"No weekly dates found in [{start_date}, {end_date}]. "
            "Check dates are Mondays present in the source files."
        )
    print(f"\n── Prediction weeks: {prediction_weeks}")

    deployment = dr.Deployment.get(DEPLOYMENT_ID)
    print(f"  Deployment: {deployment.label}  ({DEPLOYMENT_ID})")

    all_results: list[pd.DataFrame] = []

    for w_idx, pred_week in enumerate(prediction_weeks, 1):
        print(f"\n── Week {w_idx}/{len(prediction_weeks)}: {pred_week} ──────────────────────────")

        for scenario, fw_df in [("planned", planned_df), ("actual", actual_df)]:
            print(f"  [{scenario.upper()}]")
            try:
                scoring_input, fw_input_rows = _build_scoring_input(
                    pred_week, actual_df, fw_df
                )
                preds_df = _run_predict(scoring_input, deployment)
                print(f"    predictions returned: {len(preds_df)} rows  "
                      f"cols={list(preds_df.columns[:6])}...")
                enriched = _enrich_predictions(preds_df, fw_input_rows, pred_week, scenario)
                all_results.append(enriched)
            except Exception as exc:
                print(f"    ERROR: {exc}")
                raise

    cache_df = pd.concat(all_results, ignore_index=True)

    # ── Write cache ────────────────────────────────────────────────────────
    DATA_DIR.mkdir(exist_ok=True)
    if append_to_existing and CACHE_FILE.exists():
        existing = pd.read_csv(CACHE_FILE)
        # Drop rows that overlap with what we just computed
        overlap_mask = (
            existing["prediction_week"].isin(prediction_weeks)
        )
        existing = existing[~overlap_mask]
        cache_df = pd.concat([existing, cache_df], ignore_index=True)

    cache_df.to_csv(CACHE_FILE, index=False)
    print(f"\n── Cache written → {CACHE_FILE}")
    print(f"   Shape: {cache_df.shape}")
    print(f"   Prediction weeks: {sorted(cache_df['prediction_week'].unique())}")
    print(f"   Scenarios: {sorted(cache_df['scenario'].unique())}")
    pred_col = _find_prediction_col(cache_df)
    print(f"   Prediction column: {pred_col!r}")
    print(f"   Sample:\n{cache_df[[DATE_COL, 'prediction_week', 'scenario', pred_col]].head(8).to_string(index=False)}")
    return cache_df


def main() -> None:
    parser = argparse.ArgumentParser(description="Prepare WFM forecast cache")
    parser.add_argument(
        "--start", default=None,
        help=f"First prediction week (YYYY-MM-DD, Monday). Default: {DEFAULT_START}"
    )
    parser.add_argument(
        "--end", default=None,
        help=f"Last prediction week (YYYY-MM-DD, Monday). Default: {DEFAULT_END}"
    )
    parser.add_argument(
        "--all", dest="use_all", action="store_true",
        help="Auto-detect the full valid range from source data (all weeks with real history)."
    )
    parser.add_argument(
        "--append", action="store_true",
        help="Append to existing cache instead of overwriting."
    )
    args = parser.parse_args()

    if args.use_all:
        start, end = None, None
    else:
        start = args.start or DEFAULT_START
        end   = args.end   or DEFAULT_END

    prepare_cache(start, end, append_to_existing=args.append)


if __name__ == "__main__":
    main()
