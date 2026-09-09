"""Streamlit dashboard reading from the gold layer only.

This is deliberately thin: the dashboard's job is to display curated,
pre-aggregated data, not to compute anything. All the business logic
(deduping, typing, aggregating) lives in dbt models upstream. That
separation is the whole point of a medallion architecture -- the BI layer
never touches raw or intermediate data.
"""

from pathlib import Path

import math

import altair as alt
import duckdb
import pandas as pd
import streamlit as st

REPO_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = REPO_ROOT / "data" / "weather.duckdb"

st.set_page_config(page_title="Weather Dashboard", page_icon=None, layout="wide")

st.title("Weather Dashboard")
st.caption(
    "Bronze → Silver → Gold ELT pipeline on top of the Open-Meteo API. "
    "Ingestion runs daily via GitHub Actions"
    " from the gold layer."
)


@st.cache_data(ttl=3600)
def load_gold() -> pd.DataFrame:
    con = duckdb.connect(str(DB_PATH), read_only=True)
    try:
        return con.execute("SELECT * FROM gold.gold_weather_daily ORDER BY forecast_date, city").df()
    finally:
        con.close()


@st.cache_data(ttl=3600)
def load_gold_hourly() -> pd.DataFrame:
    con = duckdb.connect(str(DB_PATH), read_only=True)
    try:
        return con.execute("SELECT * FROM gold.gold_weather_hourly ORDER BY forecast_time, city").df()
    finally:
        con.close()


if not DB_PATH.exists():
    st.error(
        "No data found yet. Run `python run_pipeline.py` from the repo root "
        "first to populate data/weather.duckdb."
    )
    st.stop()

df = load_gold()
df_hourly = load_gold_hourly()

cities = sorted(df["city"].unique())

# Fixed, qualitative palette (D3 category20 + category20b) assigned once
# over the full city list, not just the current selection, so a given
# city's color never shifts around as you add/remove other cities from
# the picker -- Albuquerque is always the same color whether it's the
# only city selected or one of forty.
PALETTE = [
    "#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd", "#8c564b", "#e377c2", "#7f7f7f", "#bcbd22", "#17becf",
    "#aec7e8", "#ffbb78", "#98df8a", "#ff9896", "#c5b0d5", "#c49c94", "#f7b6d2", "#c7c7c7", "#dbdb8d", "#9edae5",
    "#393b79", "#5254a3", "#6b6ecf", "#9c9ede", "#637939", "#8ca252", "#b5cf6b", "#cedb9c", "#8c6d31", "#bd9e39",
    "#e7ba52", "#e7cb94", "#843c39", "#ad494a", "#d6616b", "#e7969c", "#7b4173", "#a55194", "#ce6dbd", "#de9ed6",
]
CITY_COLORS = {city: PALETTE[i % len(PALETTE)] for i, city in enumerate(cities)}
COLOR_SCALE = alt.Scale(domain=cities, range=[CITY_COLORS[c] for c in cities])

# With dozens of cities now tracked, defaulting the picker to "all of them"
# makes the charts unreadable, so start with a small, readable subset and
# put the picker in the sidebar so it doesn't compete for width with the
# charts.
DEFAULT_CITY_COUNT = 6
# DuckDB DATE columns come back through pandas as Timestamp, not plain
# datetime.date -- normalize here so what seeds the widget matches the
# plain dates st.date_input hands back on interaction.
min_forecast_date = df["forecast_date"].min().date()
max_forecast_date = df["forecast_date"].max().date()
with st.sidebar:
    st.header("Filter")
    select_all = st.checkbox("Select all cities", value=False)
    selected_cities = st.multiselect(
        "Cities",
        cities,
        default=cities if select_all else cities[:DEFAULT_CITY_COUNT],
    )

    st.divider()
    date_range = st.date_input(
        "Date range",
        value=(min_forecast_date, max_forecast_date),
        min_value=min_forecast_date,
        max_value=max_forecast_date,
    )

if not selected_cities:
    st.info("Select at least one city in the sidebar to see its charts.")
    st.stop()

# st.date_input returns a single date while the user has only picked one
# end of the range (mid-click), and a two-element tuple once both ends are
# set -- treat a lone date as a single-day range rather than erroring.
if isinstance(date_range, tuple) and len(date_range) == 2:
    start_date, end_date = date_range
else:
    single_date = date_range[0] if isinstance(date_range, tuple) else date_range
    start_date = end_date = single_date

filtered = df[
    df["city"].isin(selected_cities)
    # forecast_date comes back from DuckDB as datetime64, which pandas
    # won't compare directly against plain datetime.date objects from
    # st.date_input (raises TypeError) -- go through .dt.date on both
    # sides so it's a date-to-date comparison.
    & df["forecast_date"].dt.date.between(start_date, end_date)
]
filtered_hourly = df_hourly[
    df_hourly["city"].isin(selected_cities)
    & df_hourly["forecast_time"].dt.date.between(start_date, end_date)
]

col1, col2, col3 = st.columns(3)
col1.metric("Cities tracked", f"{df['city'].nunique()}")
col2.metric("Cities shown", f"{len(selected_cities)}")
col3.metric("Last ingested (UTC)", str(df["last_ingested_at"].max())[:16])


def _legend_columns(n_series: int) -> int:
    # Wrap the legend into a grid sized to the number of selected cities,
    # aiming for at most ~8 rows regardless of how many cities are picked
    # -- 3 columns is fine for a handful of cities, but pinning it at 3
    # forever means selecting a lot of cities produces a legend many rows
    # tall that clips against the chart's fixed-height container. More
    # cities selected -> more columns -> the legend grows wide instead of
    # tall.
    if n_series <= 3:
        return max(1, n_series)
    return max(3, math.ceil(n_series / 8))


st.subheader("Average temperature by day")
temp_chart = (
    alt.Chart(filtered)
    .mark_line(point=True)
    .encode(
        x=alt.X("forecast_date:T", title=None),
        y=alt.Y("avg_temperature_c:Q", axis=alt.Axis(title="°C", titleAngle=-90, titlePadding=10)),
        color=alt.Color(
            "city:N",
            title=None,
            scale=COLOR_SCALE,
            # Restrict the legend to just the cities currently selected --
            # the scale's domain stays the full city list (so colors never
            # shift as the selection changes), but without this the legend
            # renders every domain value, i.e. all 54+ cities, regardless
            # of what's actually plotted.
            legend=alt.Legend(
                orient="bottom",
                direction="horizontal",
                columns=_legend_columns(len(selected_cities)),
                values=selected_cities,
                # Vega-Lite caps legends at 30 entries by default and
                # collapses the rest into an "...N entries" summary --
                # harmless with a handful of cities selected, but with all
                # 54+ selected it silently hides most of them. Raise the
                # cap well above our city count so nothing gets collapsed.
                symbolLimit=500,
            ),
        ),
        tooltip=["forecast_date:T", "city:N", "avg_temperature_c:Q"],
    )
    .properties(height=320 + 22 * (math.ceil(len(selected_cities) / _legend_columns(len(selected_cities))) - 1))
)
st.altair_chart(temp_chart, use_container_width=True)

st.subheader("Temperature by hour")
hourly_chart = (
    alt.Chart(filtered_hourly)
    .mark_line()
    .encode(
        x=alt.X("forecast_time:T", title=None),
        y=alt.Y("temperature_c:Q", axis=alt.Axis(title="°C", titleAngle=-90, titlePadding=10)),
        color=alt.Color(
            "city:N",
            title=None,
            scale=COLOR_SCALE,
            legend=alt.Legend(
                orient="bottom",
                direction="horizontal",
                columns=_legend_columns(len(selected_cities)),
                values=selected_cities,
                symbolLimit=500,
            ),
        ),
        tooltip=["forecast_time:T", "city:N", "temperature_c:Q"],
    )
    .properties(height=320 + 22 * (math.ceil(len(selected_cities) / _legend_columns(len(selected_cities))) - 1))
)
st.altair_chart(hourly_chart, use_container_width=True)

st.subheader("Max wind speed by day")
wind_chart = (
    alt.Chart(filtered)
    .mark_bar()
    .encode(
        x=alt.X("forecast_date:T", title=None),
        y=alt.Y("max_wind_speed_kmh:Q", axis=alt.Axis(title="km/h", titleAngle=-90, titlePadding=10)),
        color=alt.Color(
            "city:N",
            title=None,
            scale=COLOR_SCALE,
            legend=alt.Legend(
                orient="bottom",
                direction="horizontal",
                columns=_legend_columns(len(selected_cities)),
                values=selected_cities,
                symbolLimit=500,
            ),
        ),
        tooltip=["forecast_date:T", "city:N", "max_wind_speed_kmh:Q"],
    )
    .properties(height=320 + 22 * (math.ceil(len(selected_cities) / _legend_columns(len(selected_cities))) - 1))
)
st.altair_chart(wind_chart, use_container_width=True)

st.subheader("Gold layer (daily, per city)")
st.dataframe(filtered, use_container_width=True)
