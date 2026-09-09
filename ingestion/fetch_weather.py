"""Bronze-layer ingestion: pull raw hourly forecast data from the Open-Meteo
API for a fixed set of cities and land it, unmodified, into DuckDB.

Design notes (medallion architecture):
  - Bronze = raw API payloads + minimal ingestion metadata. No parsing,
    no type casting, no business logic. If the API changes shape tomorrow,
    this layer still succeeds and nothing downstream breaks until dbt runs.
  - Idempotent-ish: each run appends a new snapshot tagged with
    `ingested_at`, so the table is naturally append-only/history-preserving
    instead of overwriting prior pulls. Silver/gold decide what "latest" means.

Open-Meteo requires no API key and has a generous free tier, which is why
it was chosen for a project that needs to run unattended on a schedule
(GitHub Actions) without secrets management.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import requests

from cities import CITIES

API_URL = "https://api.open-meteo.com/v1/forecast"
HOURLY_FIELDS = "temperature_2m,relative_humidity_2m,precipitation,wind_speed_10m"

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = REPO_ROOT / "data" / "weather.duckdb"


def fetch_city(session: requests.Session, city: dict) -> dict:
    """Call Open-Meteo for one city and return the raw JSON response."""
    params = {
        "latitude": city["latitude"],
        "longitude": city["longitude"],
        "hourly": HOURLY_FIELDS,
        # Open-Meteo's free forecast API supports up to 16 days --
        # bumped up from 3 so the dashboard has a real range of dates
        # to filter/plot instead of just today + the next two days.
        "forecast_days": 16,
        "timezone": "auto",
    }
    resp = session.get(API_URL, params=params, timeout=30)
    resp.raise_for_status()
    return resp.json()


def land_bronze(con: duckdb.DuckDBPyConnection, rows: list[dict]) -> int:
    """Create the bronze table if needed and append this run's raw payloads."""
    con.execute("CREATE SCHEMA IF NOT EXISTS bronze")
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS bronze.weather_raw (
            ingested_at TIMESTAMP,
            city        VARCHAR,
            country     VARCHAR,
            latitude    DOUBLE,
            longitude   DOUBLE,
            raw_response JSON
        )
        """
    )
    con.executemany(
        """
        INSERT INTO bronze.weather_raw
        (ingested_at, city, country, latitude, longitude, raw_response)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        [
            (
                r["ingested_at"],
                r["city"],
                r["country"],
                r["latitude"],
                r["longitude"],
                json.dumps(r["raw_response"]),
            )
            for r in rows
        ],
    )
    return len(rows)


def run(db_path: Path = DEFAULT_DB_PATH, cities: list[dict] | None = None) -> int:
    cities = cities if cities is not None else CITIES
    ingested_at = datetime.now(timezone.utc)
    rows = []

    with requests.Session() as session:
        for city in cities:
            try:
                payload = fetch_city(session, city)
            except requests.RequestException as exc:
                print(f"[warn] failed to fetch {city['city']}: {exc}", file=sys.stderr)
                continue
            rows.append(
                {
                    "ingested_at": ingested_at,
                    "city": city["city"],
                    "country": city["country"],
                    "latitude": city["latitude"],
                    "longitude": city["longitude"],
                    "raw_response": payload,
                }
            )

    if not rows:
        print("[error] no cities were successfully fetched", file=sys.stderr)
        return 0

    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(db_path))
    try:
        count = land_bronze(con, rows)
    finally:
        con.close()

    print(f"[ok] landed {count} city payloads into {db_path} @ {ingested_at.isoformat()}")
    return count


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Fetch weather data into the bronze layer")
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)
    args = parser.parse_args()
    run(db_path=args.db_path)
