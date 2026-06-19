"""
Smoke test for the Forecast Comparison prediction pipeline.

Run from the repo root with env vars loaded:
    set -a && source .env && set +a
    python test_comparison_predictions.py

Checks:
  1. Catalog load (planned + actual datasets)
  2. ASSOCIATION_ID construction
  3. Prediction API call
  4. Prediction column detection (_find_prediction_col)
  5. _preds_to_fc_df shapes and no NaN predictions
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "frontend"))

import datarobot as dr
import pandas as pd

PLANNED_DATASET_ID = "6a326e0a76da3420b0d4e6e1"
ACTUAL_DATASET_ID  = "6a326f4d347b28ea2e55f572"

DEPLOYMENT_ID = os.environ.get("FORECAST_DEPLOYMENT_ID", "6a0ec47bf306847615758fa3")
DR_ENDPOINT   = os.environ.get("DATAROBOT_ENDPOINT", "https://app.eu.datarobot.com/api/v2/")
DR_TOKEN      = os.environ.get("DATAROBOT_API_TOKEN", "")


def _init_dr() -> None:
    token = DR_TOKEN
    # Decode if base64-encoded (codespace pattern)
    import base64
    try:
        decoded = base64.b64decode(token).decode("utf-8")
        if ":" in decoded:
            token = decoded
    except Exception:
        pass
    dr.Client(token=token, endpoint=DR_ENDPOINT)
    print(f"  DR client: {DR_ENDPOINT}")


def _step(label: str) -> None:
    print(f"\n{'─'*60}\n▶  {label}")


def main() -> None:
    _step("1 / Initialise DR client")
    _init_dr()

    # -- import here so DR client is initialised first --
    from forecastic.comparison_api import (
        _ensure_association_id,
        _find_prediction_col,
        _preds_to_fc_df,
        load_from_catalog,
        run_predictions,
    )

    _step("2 / Load planned dataset from Catalog")
    planned_df = load_from_catalog(PLANNED_DATASET_ID)
    print(f"  shape: {planned_df.shape}  columns: {list(planned_df.columns)[:8]}...")

    _step("3 / Load actual dataset from Catalog")
    actual_df = load_from_catalog(ACTUAL_DATASET_ID)
    print(f"  shape: {actual_df.shape}  columns: {list(actual_df.columns)[:8]}...")

    _step("4 / ASSOCIATION_ID check")
    for label, df in [("planned", planned_df), ("actual", actual_df)]:
        has = "ASSOCIATION_ID" in df.columns
        print(f"  {label}: ASSOCIATION_ID present={has}")
        if not has:
            patched = _ensure_association_id(df)
            sample = patched["ASSOCIATION_ID"].iloc[0]
            print(f"    → constructed sample: {sample!r}")

    _step("5 / Run predictions — PLANNED")
    planned_preds = run_predictions(planned_df)
    print(f"  records: {len(planned_preds)}")
    first = pd.DataFrame(planned_preds[:1])
    pred_col = _find_prediction_col(pd.DataFrame(planned_preds))
    print(f"  prediction column detected: {pred_col!r}")
    pred_cols = [c for c in first.columns if "PRED" in c.upper() or "EXPL" in c.upper()]
    print(f"  PREDICTION / EXPLANATION cols: {pred_cols}")

    _step("6 / Run predictions — ACTUAL")
    actual_preds = run_predictions(actual_df)
    print(f"  records: {len(actual_preds)}")

    _step("7 / _preds_to_fc_df — shape and nulls")
    for label, preds in [("planned", planned_preds), ("actual", actual_preds)]:
        fc = _preds_to_fc_df(preds)
        nulls = fc["prediction"].isna().sum()
        print(f"  {label}: rows={len(fc)}  null_predictions={nulls}")
        print(f"    date range: {fc['date_id'].min()} → {fc['date_id'].max()}")
        print(f"    prediction range: {fc['prediction'].min():.1f} → {fc['prediction'].max():.1f}")

    _step("✅ All checks passed")


if __name__ == "__main__":
    main()
