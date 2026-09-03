"""Train and evaluate next-day temperature forecasting models.

Three models, deliberately in increasing order of sophistication, so the
comparison itself is the point:

  1. Persistence baseline -- "tomorrow will be like today." No fitting.
     Any model that can't beat this isn't worth using; it's the bar every
     real forecasting system is implicitly judged against.
  2. Linear regression -- a simple, interpretable model on the engineered
     features (lags, rolling stats, seasonality).
  3. XGBoost -- a gradient-boosted tree model on the same features, usually
     the strongest of the three for this kind of tabular time-series setup.

Evaluation is on a chronological hold-out (see ds/features.py) -- never a
random split, since that would leak future information into training for
a time-series problem.

Writes metrics, per-row predictions, and feature importances to the gold
schema in DuckDB (so the dashboard can read them the same way it reads the
DE pipeline's gold tables), and saves the fitted linear/XGBoost models to
ds/models/ via joblib.
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import joblib
import numpy as np
import pandas as pd
from sklearn.linear_model import LinearRegression
from sklearn.metrics import mean_absolute_error, mean_squared_error
from xgboost import XGBRegressor

sys.path.insert(0, str(Path(__file__).resolve().parent))
from features import FEATURE_COLS, TARGET_COL, build_features, train_test_split_chronological  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = REPO_ROOT / "data" / "weather.duckdb"
MODELS_DIR = Path(__file__).resolve().parent / "models"


def load_silver(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    return con.execute(
        """
        SELECT city, country, observed_date, temperature_mean_c,
               temperature_max_c, temperature_min_c, precipitation_mm,
               wind_speed_max_kmh
        FROM silver.stg_weather_historical_daily
        """
    ).df()


def evaluate(name: str, y_true: np.ndarray, y_pred: np.ndarray) -> dict:
    mae = mean_absolute_error(y_true, y_pred)
    rmse = mean_squared_error(y_true, y_pred) ** 0.5
    return {"model_name": name, "mae_c": round(mae, 3), "rmse_c": round(rmse, 3), "n_test_rows": len(y_true)}


def write_gold(con: duckdb.DuckDBPyConnection, metrics: pd.DataFrame, predictions: pd.DataFrame, importance: pd.DataFrame) -> None:
    con.execute("CREATE SCHEMA IF NOT EXISTS gold")
    con.execute("CREATE OR REPLACE TABLE gold.model_evaluation AS SELECT * FROM metrics")
    con.execute("CREATE OR REPLACE TABLE gold.model_predictions AS SELECT * FROM predictions")
    con.execute("CREATE OR REPLACE TABLE gold.model_feature_importance AS SELECT * FROM importance")


def main() -> None:
    if not DB_PATH.exists():
        raise SystemExit(f"{DB_PATH} not found -- run ds/backfill_historical.py and `dbt run` first")

    con = duckdb.connect(str(DB_PATH))
    raw = load_silver(con)
    if raw.empty:
        con.close()
        raise SystemExit(
            "silver.stg_weather_historical_daily is empty -- run "
            "ds/backfill_historical.py then `dbt run` before training"
        )

    df = build_features(raw)
    train, test = train_test_split_chronological(df, test_days=90)

    print(f"train rows: {len(train)}, test rows: {len(test)}, cities: {df['city'].nunique()}")

    trained_at = datetime.now(timezone.utc)
    metrics_rows = []
    pred_frames = []

    # 1. persistence baseline: predicted next-day temp = today's temp
    baseline_pred = test["temperature_mean_c"].to_numpy()
    metrics_rows.append({**evaluate("persistence_baseline", test[TARGET_COL], baseline_pred), "trained_at": trained_at})
    pred_frames.append(pd.DataFrame({
        "model_name": "persistence_baseline", "city": test["city"], "observed_date": test["observed_date"],
        "actual_c": test[TARGET_COL].to_numpy(), "predicted_c": baseline_pred,
    }))

    X_train, y_train = train[FEATURE_COLS], train[TARGET_COL]
    X_test, y_test = test[FEATURE_COLS], test[TARGET_COL]

    # 2. linear regression
    lin = LinearRegression().fit(X_train, y_train)
    lin_pred = lin.predict(X_test)
    metrics_rows.append({**evaluate("linear_regression", y_test, lin_pred), "trained_at": trained_at})
    pred_frames.append(pd.DataFrame({
        "model_name": "linear_regression", "city": test["city"], "observed_date": test["observed_date"],
        "actual_c": y_test.to_numpy(), "predicted_c": lin_pred,
    }))
    joblib.dump(lin, MODELS_DIR / "linear_regression.joblib")

    # 3. xgboost
    xgb = XGBRegressor(
        n_estimators=300, max_depth=4, learning_rate=0.05,
        subsample=0.8, colsample_bytree=0.8, random_state=42,
    ).fit(X_train, y_train)
    xgb_pred = xgb.predict(X_test)
    metrics_rows.append({**evaluate("xgboost", y_test, xgb_pred), "trained_at": trained_at})
    pred_frames.append(pd.DataFrame({
        "model_name": "xgboost", "city": test["city"], "observed_date": test["observed_date"],
        "actual_c": y_test.to_numpy(), "predicted_c": xgb_pred,
    }))
    joblib.dump(xgb, MODELS_DIR / "xgboost.joblib")

    importance = pd.DataFrame({
        "model_name": "xgboost",
        "feature": FEATURE_COLS,
        "importance": xgb.feature_importances_,
    }).sort_values("importance", ascending=False)

    metrics = pd.DataFrame(metrics_rows)
    predictions = pd.concat(pred_frames, ignore_index=True)

    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    write_gold(con, metrics, predictions, importance)
    con.close()

    print("\n=== Evaluation (chronological hold-out) ===")
    print(metrics.to_string(index=False))
    print("\nTop features (xgboost):")
    print(importance.head(5).to_string(index=False))


if __name__ == "__main__":
    main()
