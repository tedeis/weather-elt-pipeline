"""DS project, bronze layer: backfill actual (observed) daily weather from
Open-Meteo's free Archive API (ERA5 reanalysis), for the same cities the DE
pipeline forecasts.

Unlike the DE pipeline's forecast ingestion, this is a one-time (or
occasional) backfill, not a daily job -- it pulls years of history in a
single run rather than accumulating one snapshot per day. Run it once to
build the DS training set:

    python3 ds/backfill_historical.py --years 2

No API key required. Same bronze-layer philosophy as fetch_weather.py:
land the raw JSON untouched, let dbt do the parsing/typing in the silver
layer (stg_weather_historical_daily).
"""

from __future__ import annotations

import argparse
import json
import sys
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


def fetch_city_history(session: requests.Session, city: dict, start: date, end: date) -> dict:
    params = {
        "latitude": city["latitude"],
        "longitude": city["longitude"],
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "daily": DAILY_FIELDS,
        "timezone": "auto",
    }
    resp = session.get(ARCHIVE_API_URL, params=params, timeout=60)
    resp.raise_for_status()
    return resp.json()


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


def run(years: int = 2, db_path: Path = DEFAULT_DB_PATH, cities: list[dict] | None = None) -> int:
    cities = cities if cities is not None else CITIES
    ingested_at = datetime.now(timezone.utc)
    end = date.today() - timedelta(days=ARCHIVE_LAG_DAYS)
    start = end - timedelta(days=365 * years)

    rows = []
    with requests.Session() as session:
        for city in cities:
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
    parser.add_argument("--years", type=int, default=2)
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)
    args = parser.parse_args()
    run(years=args.years, db_path=args.db_path)
