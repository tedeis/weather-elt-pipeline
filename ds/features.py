"""Feature engineering for the next-day temperature forecasting model.

Task framing: for each (city, date), predict tomorrow's mean temperature
using only information available today or earlier -- lags of the target,
rolling statistics, and calendar/seasonal signals. This mirrors what a real
forecasting deployment would have available at inference time.

The one rule that matters most in time-series ML and is the easiest to get
wrong: never let a feature or a train/test split leak information from the
future into the past. Every function here is written, and tested in
tests/test_features.py, to respect that.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

LAGS = (1, 2, 3, 7)
ROLLING_WINDOW = 7
TARGET_COL = "target_temperature_next_day_c"


def build_features(df: pd.DataFrame) -> pd.DataFrame:
    """Add lag, rolling, and seasonal features plus the next-day target.

    Expects columns: city, observed_date, temperature_mean_c,
    temperature_max_c, temperature_min_c, precipitation_mm, wind_speed_max_kmh.

    All lag/rolling features for a given row use only that row's own city's
    past values (groupby("city") + shift before rolling), and the target is
    tomorrow's temperature -- so a model trained on row t never sees
    anything dated later than day t's inputs, only t's own future outcome
    as the label, which is the whole point of supervised forecasting.
    """
    df = df.sort_values(["city", "observed_date"]).reset_index(drop=True)
    g = df.groupby("city", group_keys=False)

    for lag in LAGS:
        df[f"temp_lag_{lag}"] = g["temperature_mean_c"].shift(lag)

    # shift(1) first so the rolling window is strictly over prior days,
    # never including the current row's own temperature
    df["temp_rolling_mean_7"] = g["temperature_mean_c"].transform(
        lambda s: s.shift(1).rolling(ROLLING_WINDOW, min_periods=ROLLING_WINDOW).mean()
    )
    df["temp_rolling_std_7"] = g["temperature_mean_c"].transform(
        lambda s: s.shift(1).rolling(ROLLING_WINDOW, min_periods=ROLLING_WINDOW).std()
    )

    df["precip_lag_1"] = g["precipitation_mm"].shift(1)
    df["wind_lag_1"] = g["wind_speed_max_kmh"].shift(1)

    day_of_year = pd.to_datetime(df["observed_date"]).dt.dayofyear
    df["doy_sin"] = np.sin(2 * np.pi * day_of_year / 365.25)
    df["doy_cos"] = np.cos(2 * np.pi * day_of_year / 365.25)

    # target: TOMORROW's temperature, i.e. this row's features predict the
    # next row (per city) -- shift(-1) is the only forward-looking touch in
    # this file, and it's the label, not a feature
    df[TARGET_COL] = g["temperature_mean_c"].shift(-1)

    return df


FEATURE_COLS = [
    "temp_lag_1", "temp_lag_2", "temp_lag_3", "temp_lag_7",
    "temp_rolling_mean_7", "temp_rolling_std_7",
    "precip_lag_1", "wind_lag_1",
    "doy_sin", "doy_cos",
]


def train_test_split_chronological(
    df: pd.DataFrame, test_days: int = 90
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Chronological split: the most recent `test_days` calendar dates
    (shared across all cities) become the test set, everything earlier is
    train. A random shuffle would leak future rows into training for a
    time-series problem -- this never does that.
    """
    df = df.dropna(subset=FEATURE_COLS + [TARGET_COL])
    cutoff = df["observed_date"].max() - pd.Timedelta(days=test_days)
    train = df[df["observed_date"] <= cutoff]
    test = df[df["observed_date"] > cutoff]
    return train, test
