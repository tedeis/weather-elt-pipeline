"""Streamlit dashboard reading from the gold layer only.

This is deliberately thin: the dashboard's job is to display curated,
pre-aggregated data, not to compute anything. All the business logic
(deduping, typing, aggregating) lives in dbt models upstream. That
separation is the whole point of a medallion architecture -- the BI layer
never touches raw or intermediate data.
"""

from pathlib import Path

import duckdb
import pandas as pd
import plotly.express as px
import streamlit as st

REPO_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = REPO_ROOT / "data" / "weather.duckdb"

st.set_page_config(page_title="Weather Medallion Pipeline", page_icon="🌦️", layout="wide")

st.title("🌦️ Weather Medallion Pipeline")
st.caption(
    "Bronze → Silver → Gold ELT pipeline on top of the Open-Meteo API. "
    "Ingestion runs daily via GitHub Actions; this dashboard reads only "
    "from the gold layer."
)


@st.cache_data(ttl=3600)
def load_gold() -> pd.DataFrame:
    con = duckdb.connect(str(DB_PATH), read_only=True)
    try:
        return con.execute("SELECT * FROM gold.gold_weather_daily ORDER BY forecast_date, city").df()
    finally:
        con.close()


if not DB_PATH.exists():
    st.error(
        "No data found yet. Run `python run_pipeline.py` from the repo root "
        "first to populate data/weather.duckdb."
    )
    st.stop()

df = load_gold()

cities = sorted(df["city"].unique())

# Give every city a fixed color, assigned once over the full city list (not
# just the current selection) so a city's color never shifts around as you
# add/remove other cities from the picker -- Albuquerque is always the same
# blue whether it's the only city selected or one of forty.
PALETTE = px.colors.qualitative.Alphabet + px.colors.qualitative.Dark24
CITY_COLORS = {city: PALETTE[i % len(PALETTE)] for i, city in enumerate(cities)}

# With dozens of cities now tracked, defaulting the multiselect to "all of
# them" makes the charts unreadable, so start with a small, readable subset.
# Use the sidebar (not the main column) so the picker doesn't compete for
# width with the charts, and swap Streamlit's native line/bar charts for
# Plotly: unlike the native charts -- which cut the legend off after ~20
# entries with a plain "...N entries" label -- Plotly's legend scrolls, and
# clicking a city name in it toggles that one city on/off directly on the
# chart without touching the picker.
DEFAULT_CITY_COUNT = 6
with st.sidebar:
    st.header("Filter")
    select_all = st.checkbox("Select all cities", value=False)
    selected_cities = st.multiselect(
        "Cities",
        cities,
        default=cities if select_all else cities[:DEFAULT_CITY_COUNT],
    )

if not selected_cities:
    st.info("Select at least one city in the sidebar to see its charts.")
    st.stop()

filtered = df[df["city"].isin(selected_cities)]

col1, col2, col3 = st.columns(3)
col1.metric("Cities tracked", f"{df['city'].nunique()}")
col2.metric("Cities shown", f"{len(selected_cities)}")
col3.metric("Last ingested (UTC)", str(df["last_ingested_at"].max())[:16])

st.subheader("Average temperature by day")
temp_fig = px.line(
    filtered.sort_values("forecast_date"),
    x="forecast_date",
    y="avg_temperature_c",
    color="city",
    color_discrete_map=CITY_COLORS,
    markers=True,
    labels={"forecast_date": "Forecast date", "avg_temperature_c": "Avg temp (°C)", "city": "City"},
)
temp_fig.update_layout(height=520, legend_title_text="City (click to toggle)")
st.plotly_chart(temp_fig, use_container_width=True)

st.subheader("Max wind speed by day")
wind_fig = px.bar(
    filtered.sort_values("forecast_date"),
    x="forecast_date",
    y="max_wind_speed_kmh",
    color="city",
    color_discrete_map=CITY_COLORS,
    barmode="group",
    labels={"forecast_date": "Forecast date", "max_wind_speed_kmh": "Max wind (km/h)", "city": "City"},
)
wind_fig.update_layout(height=520, legend_title_text="City (click to toggle)")
st.plotly_chart(wind_fig, use_container_width=True)

st.subheader("Gold layer (daily, per city)")
st.dataframe(filtered, use_container_width=True)
