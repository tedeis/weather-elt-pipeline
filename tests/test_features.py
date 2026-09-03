"""Tests for ds/features.py, focused entirely on the thing that's easy to
get wrong in time-series ML: leakage. If these pass, no feature or split
uses information from the future."""

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ds"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ingestion"))

from features import FEATURE_COLS, TARGET_COL, build_features, train_test_split_chronological  # noqa: E402


def _make_city_series(city: str, n_days: int = 40, start_temp: float = 10.0) -> pd.DataFrame:
    dates = pd.date_range("2024-01-01", periods=n_days, freq="D")
    temps = start_temp + np.arange(n_days) * 0.5  # strictly increasing, easy to reason about
    return pd.DataFrame(
        {
            "city": city,
            "country": "XX",
            "observed_date": dates,
            "temperature_mean_c": temps,
            "temperature_max_c": temps + 2,
            "temperature_min_c": temps - 2,
            "precipitation_mm": 0.0,
            "wind_speed_max_kmh": 10.0,
        }
    )


def test_lag_1_equals_previous_day_temperature():
    df = build_features(_make_city_series("Testville"))
    row = df[df["observed_date"] == "2024-01-10"].iloc[0]
    prev_row = df[df["observed_date"] == "2024-01-09"].iloc[0]
    assert row["temp_lag_1"] == prev_row["temperature_mean_c"]


def test_target_is_next_day_temperature_not_same_day():
    df = build_features(_make_city_series("Testville"))
    row = df[df["observed_date"] == "2024-01-10"].iloc[0]
    next_row = df[df["observed_date"] == "2024-01-11"].iloc[0]
    assert row[TARGET_COL] == next_row["temperature_mean_c"]
    assert row[TARGET_COL] != row["temperature_mean_c"]


def test_rolling_mean_excludes_current_day():
    # strictly increasing series: if the rolling window included "today",
    # the mean would be pulled up by today's own (higher) value. Assert it
    # matches a manually computed prior-7-day mean instead.
    df = build_features(_make_city_series("Testville"))
    row = df[df["observed_date"] == "2024-01-15"].iloc[0]

    raw = _make_city_series("Testville")
    prior_7 = raw[
        (raw["observed_date"] < "2024-01-15") & (raw["observed_date"] >= "2024-01-08")
    ]["temperature_mean_c"]
    assert row["temp_rolling_mean_7"] == prior_7.mean()


def test_features_are_isolated_per_city():
    # two cities with very different temperature levels must not leak into
    # each other's lag/rolling features
    df = build_features(pd.concat([_make_city_series("Hot City", start_temp=30), _make_city_series("Cold City", start_temp=-10)]))
    hot_row = df[(df["city"] == "Hot City") & (df["observed_date"] == "2024-01-10")].iloc[0]
    cold_row = df[(df["city"] == "Cold City") & (df["observed_date"] == "2024-01-10")].iloc[0]
    assert hot_row["temp_lag_1"] > 20
    assert cold_row["temp_lag_1"] < 0


def test_chronological_split_has_no_date_overlap_and_test_is_strictly_later():
    df = build_features(_make_city_series("Testville", n_days=60))
    train, test = train_test_split_chronological(df, test_days=15)

    assert len(train) > 0 and len(test) > 0
    assert train["observed_date"].max() < test["observed_date"].min()


def test_split_drops_rows_with_incomplete_features_or_target():
    df = build_features(_make_city_series("Testville", n_days=20))
    train, test = train_test_split_chronological(df, test_days=5)
    combined = pd.concat([train, test])
    assert combined[FEATURE_COLS + [TARGET_COL]].isna().sum().sum() == 0
