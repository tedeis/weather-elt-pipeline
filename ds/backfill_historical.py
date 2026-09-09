"""DS project, bronze layer: backfill actual (observed) daily weather from
Open-Meteo's free Archive API (ERA5 reanalysis), for the same cities the DE
pipeline forecasts.

Unlike the DE pipeline's forecast ingestion, this pulls years of history in
a single run rather than accumulating one hourly snapshot per day. Run it
once by hand to build the DS training set:

    python3 ds/backfill_historical.py --years 2

It's also re-run daily by the scheduled GitHub Actions workflow (see
.github/workflows/pipeline.yml's "Update forecast-accuracy-by-lead-time
table" step) -- not to rebuild years of history from scratch each time, but
because end_date is always "today - ARCHIVE_LAG_DAYS": each re-run is what
advances the confirmed-actuals frontier forward as new days finalize, which
is what lets gold.forecast_accuracy_by_lead_time keep gaining overlap with
the DE pipeline's forecast snapshots instead of freezing at whatever date
the first backfill happened to run on.

The daily re-run passes --window-days (a short recent window, e.g. 14 days)
instead of re-requesting the full --years range: the full history is
already landed from the initial manual backfill, a daily run only needs to
extend the frontier a day or two further, and asking for less data per city
keeps each run fast and light. Both the years-based and window-based paths
also pace requests and retry on HTTP 429 -- firing 50+ archive-API requests
back-to-back with no delay reliably trips Open-Meteo's rate limit, which
silently drops most of a run's cities (see REQUEST_DELAY_SECONDS below).

No API key required. Same bronze-layer philosophy as fetch_weather.py:
land the raw JSON untouched, let dbt do the parsing/typing in the silver
layer (stg_weather_historical_daily).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import duckdb
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ingestion"))
from cities import CITIES  # noqa: E402

ARCHIVE_API_URL = "https://archive-api.open-meteo.com/v1/archive"
DAILY_FIELDS = "temperature_2m_mean,temperature_2m_max,temperature_2m_min,precipitation_sum,wind_speed_10m_max"

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = REPO_ROOT / "data" / "weather.duckdb"

# ERA5 reanalysis is finalized with roughly a 5-day lag; asking for more
# recent data than that reliably comes back with nulls.
ARCHIVE_LAG_DAYS = 5

# Firing 50+ requests at archive-api.open-meteo.com back-to-back with no
# delay reliably trips its rate limit partway through the city list (seen
# in practice: ~16 of 54 cities succeed, the rest come back 429). A small
# delay between requests plus a couple of backoff retries on 429 gets every
# city through instead of silently losing most of a run.
REQUEST_DELAY_SECONDS = 1.5
MAX_RETRIES_ON_429 = 3
RETRY_BACKOFF_SECONDS = 5


def fetch_city_history(session: requests.Session, city: dict, start: date, end: date) -> dict:
    params = {
        "latitude": city["latitude"],
        "longitude": city["longitude"],
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "daily": DAILY_FIELDS,
        "timezone": "auto",
    }
    for attempt in range(MAX_RETRIES_ON_429 + 1):
        resp = session.get(ARCHIVE_API_URL, params=params, timeout=60)
        if resp.status_code == 429 and attempt < MAX_RETRIES_ON_429:
            wait = RETRY_BACKOFF_SECONDS * (attempt + 1)
            print(f"[warn] rate limited on {city['city']}, retrying in {wait}s", file=sys.stderr)
            time.sleep(wait)
            continue
        resp.raise_for_status()
        return resp.json()
    raise RuntimeError(f"unreachable: retry loop for {city['city']} exited without returning")


def land_bronze(con: duckdb.DuckDBPyConnection, rows: list[dict]) -> int:
    con.execute("CREATE SCHEMA IF NOT EXISTS bronze")
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS bronze.weather_historical_raw (
            ingested_at TIMESTAMP,
            city        VARCHAR,
            country     VARCHAR,
            start_date  DATE,
            end_date    DATE,
            raw_response JSON
        )
        """
    )
    con.executemany(
        """
        INSERT INTO bronze.weather_historical_raw
        (ingested_at, city, country, start_date, end_date, raw_response)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        [
            (
                r["ingested_at"], r["city"], r["country"],
                r["start_date"], r["end_date"], json.dumps(r["raw_response"]),
            )
            for r in rows
        ],
    )
    return len(rows)


def run(
    years: int = 2,
    db_path: Path = DEFAULT_DB_PATH,
    cities: list[dict] | None = None,
    window_days: int | None = None,
) -> int:
    """Fetch each city's actuals history and land it into bronze.

    window_days, when given, overrides `years` and pulls just a short
    recent window ending at the usual ARCHIVE_LAG_DAYS-adjusted date --
    for the daily catch-up run, which only needs to extend the frontier a
    little further, not re-pull the whole archive. `years` still controls
    the one-time/manual full backfill.
    """
    cities = cities if cities is not None else CITIES
    ingested_at = datetime.now(timezone.utc)
    end = date.today() - timedelta(days=ARCHIVE_LAG_DAYS)
    span_days = window_days if window_days is not None else 365 * years
    start = end - timedelta(days=span_days)

    rows = []
    with requests.Session() as session:
        for i, city in enumerate(cities):
            if i > 0:
                time.sleep(REQUEST_DELAY_SECONDS)
            try:
                payload = fetch_city_history(session, city, start, end)
            except requests.RequestException as exc:
                print(f"[warn] failed to backfill {city['city']}: {exc}", file=sys.stderr)
                continue
            rows.append(
                {
                    "ingested_at": ingested_at,
                    "city": city["city"],
                    "country": city["country"],
                    "start_date": start,
                    "end_date": end,
                    "raw_response": payload,
                }
            )
            print(f"[ok] backfilled {city['city']} {start} -> {end}")

    if not rows:
        print("[error] no cities were successfully backfilled", file=sys.stderr)
        return 0

    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(db_path))
    try:
        count = land_bronze(con, rows)
    finally:
        con.close()

    print(f"[ok] landed {count} city histories ({start} to {end}) into {db_path}")
    return count


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Backfill actual historical weather into the bronze layer")
    parser.add_argument("--years", type=int, default=2, help="Full backfill window (ignored if --window-days is given).")
    parser.add_argument(
        "--window-days",
        type=int,
        default=None,
        help="Pull only the last N days instead of --years -- for the daily catch-up run.",
    )
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)
    args = parser.parse_args()
    run(years=args.years, db_path=args.db_path, window_days=args.window_days)
