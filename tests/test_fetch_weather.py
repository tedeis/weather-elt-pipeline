"""Unit tests for the bronze ingestion logic, mocking the Open-Meteo API so
the test suite doesn't depend on network access (and can run in CI)."""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import duckdb
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ingestion"))

import fetch_weather  # noqa: E402

FAKE_RESPONSE = {
    "hourly": {
        "time": ["2026-09-02T00:00", "2026-09-02T01:00"],
        "temperature_2m": [20.1, 19.8],
        "relative_humidity_2m": [55, 57],
        "precipitation": [0.0, 0.1],
        "wind_speed_10m": [12.3, 11.9],
    }
}

TEST_CITIES = [
    {"city": "Testville", "country": "US", "latitude": 1.0, "longitude": 2.0},
]


def test_fetch_city_returns_json():
    session = MagicMock()
    session.get.return_value.json.return_value = FAKE_RESPONSE
    session.get.return_value.raise_for_status.return_value = None

    result = fetch_weather.fetch_city(session, TEST_CITIES[0])

    assert result == FAKE_RESPONSE
    session.get.assert_called_once()


def test_land_bronze_creates_table_and_inserts_rows(tmp_path):
    db_path = tmp_path / "test.duckdb"
    con = duckdb.connect(str(db_path))

    rows = [
        {
            "ingested_at": "2026-09-02T00:00:00",
            "city": "Testville",
            "country": "US",
            "latitude": 1.0,
            "longitude": 2.0,
            "raw_response": FAKE_RESPONSE,
        }
    ]

    count = fetch_weather.land_bronze(con, rows)
    result = con.execute("SELECT city FROM bronze.weather_raw").fetchall()

    con.close()
    assert count == 1
    assert result == [("Testville",)]


def test_run_end_to_end_with_mocked_api(tmp_path):
    db_path = tmp_path / "run.duckdb"

    with patch.object(fetch_weather.requests, "Session") as mock_session_cls:
        mock_session = MagicMock()
        mock_session.__enter__.return_value = mock_session
        mock_session.get.return_value.json.return_value = FAKE_RESPONSE
        mock_session.get.return_value.raise_for_status.return_value = None
        mock_session_cls.return_value = mock_session

        count = fetch_weather.run(db_path=db_path, cities=TEST_CITIES)

    assert count == 1
    assert db_path.exists()


def test_run_returns_zero_when_all_cities_fail(tmp_path):
    db_path = tmp_path / "empty.duckdb"

    with patch.object(fetch_weather.requests, "Session") as mock_session_cls:
        mock_session = MagicMock()
        mock_session.__enter__.return_value = mock_session
        mock_session.get.side_effect = fetch_weather.requests.RequestException("boom")
        mock_session_cls.return_value = mock_session

        count = fetch_weather.run(db_path=db_path, cities=TEST_CITIES)

    assert count == 0
    assert not db_path.exists()
