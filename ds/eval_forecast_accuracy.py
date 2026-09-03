"""Forecast-accuracy-by-lead-time tracker.

This is the other half of the DS story: not "can we forecast weather" but
"how good are the forecasts we're already collecting, and does accuracy
degrade the further out they predict?"

The DE pipeline's bronze.weather_raw table is append-only -- every daily
run keeps its own snapshot rather than overwriting the last one. Because
each snapshot includes a 3-day-ahead hourly forecast, the same calendar
date ends up predicted multiple times at different lead times (e.g. a
Monday forecast for Wednesday has a 2-day lead time; Tuesday's forecast
for that same Wednesday has a 1-day lead time). Once the historical
archive backfill (or the daily accuracy job below) has the actual value
for that date, we can measure error as a function of lead time.

Important honesty note: this table starts EMPTY (or nearly empty) right
after this project is published, and only becomes meaningful after the
pipeline has been running daily for at least a lead-time's worth of days
(the free ERA5 archive also finalizes with ~5 days of lag -- see
ds/backfill_historical.py). That's expected, not a bug: run this daily via
the GitHub Actions workflow and the accuracy table fills in over time,
exactly the way the DE pipeline's own history does.
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = REPO_ROOT / "data" / "weather.duckdb"


EMPTY_ACCURACY_COLUMNS = [
    "city", "forecast_date", "forecast_made_on", "lead_time_days",
    "forecast_temperature_c", "actual_temperature_c", "abs_error_c",
]


def _table_exists(con: duckdb.DuckDBPyConnection, schema: str, table: str) -> bool:
    row = con.execute(
        "select count(*) from information_schema.tables where table_schema = ? and table_name = ?",
        [schema, table],
    ).fetchone()
    return bool(row and row[0] > 0)


def build_accuracy_table(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    # Both sides are optional depending on what's been run so far: the DE
    # pipeline (run_pipeline.py) creates stg_weather_hourly, the DS backfill
    # (ds/run_ds_pipeline.py) creates stg_weather_historical_daily. Either
    # can legitimately not exist yet -- treat that the same as "no overlap
    # yet" rather than crashing, since it's just as expected right after a
    # fresh clone as an empty join result is.
    if not _table_exists(con, "silver", "stg_weather_hourly"):
        print(
            "[info] silver.stg_weather_hourly doesn't exist yet -- run "
            "`python3 run_pipeline.py` (the DE pipeline) at least once so "
            "there are forecast snapshots to compare against."
        )
        return pd.DataFrame(columns=EMPTY_ACCURACY_COLUMNS)

    if not _table_exists(con, "silver", "stg_weather_historical_daily"):
        print(
            "[info] silver.stg_weather_historical_daily doesn't exist yet -- "
            "run `python3 ds/backfill_historical.py` (or ds/run_ds_pipeline.py) "
            "so there are confirmed actuals to compare forecasts against."
        )
        return pd.DataFrame(columns=EMPTY_ACCURACY_COLUMNS)

    forecasts = con.execute(
        """
        select
            city,
            cast(forecast_time as date) as forecast_date,
            cast(ingested_at as date)   as forecast_made_on,
            avg(temperature_c)          as forecast_temperature_c
        from silver.stg_weather_hourly
        group by city, cast(forecast_time as date), cast(ingested_at as date)
        """
    ).df()

    actuals = con.execute(
        """
        select city, observed_date, temperature_mean_c as actual_temperature_c
        from silver.stg_weather_historical_daily
        """
    ).df()

    if forecasts.empty or actuals.empty:
        return pd.DataFrame(columns=EMPTY_ACCURACY_COLUMNS)

    merged = forecasts.merge(
        actuals, left_on=["city", "forecast_date"], right_on=["city", "observed_date"], how="inner"
    )
    merged["lead_time_days"] = (
        pd.to_datetime(merged["forecast_date"]) - pd.to_datetime(merged["forecast_made_on"])
    ).dt.days
    merged["abs_error_c"] = (merged["forecast_temperature_c"] - merged["actual_temperature_c"]).abs()

    return merged[
        [
            "city", "forecast_date", "forecast_made_on", "lead_time_days",
            "forecast_temperature_c", "actual_temperature_c", "abs_error_c",
        ]
    ].sort_values(["forecast_date", "city", "lead_time_days"])


def main() -> None:
    if not DB_PATH.exists():
        print("[skip] no database yet -- run the DE pipeline and DS backfill first")
        return

    con = duckdb.connect(str(DB_PATH))
    try:
        accuracy = build_accuracy_table(con)
        con.execute("CREATE SCHEMA IF NOT EXISTS gold")
        con.execute("CREATE OR REPLACE TABLE gold.forecast_accuracy_by_lead_time AS SELECT * FROM accuracy")
    finally:
        con.close()

    if accuracy.empty:
        print(
            "[ok] wrote empty gold.forecast_accuracy_by_lead_time -- "
            "no overlap yet between forecast snapshots and actuals. "
            "This fills in as the daily pipeline accumulates history."
        )
    else:
        summary = accuracy.groupby("lead_time_days")["abs_error_c"].agg(["mean", "count"])
        print(f"[ok] wrote {len(accuracy)} rows to gold.forecast_accuracy_by_lead_time")
        print(summary.to_string())


if __name__ == "__main__":
    main()
