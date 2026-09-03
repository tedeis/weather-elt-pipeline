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
import streamlit as st
import altair as alt

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
selected_cities = st.multiselect("Cities", cities, default=cities)
filtered = df[df["city"].isin(selected_cities)]

col1, col2, col3 = st.columns(3)
col1.metric("Cities tracked", f"{df['city'].nunique()}")
col2.metric("Forecast days", f"{df['forecast_date'].nunique()}")
col3.metric("Last ingested (UTC)", str(df["last_ingested_at"].max())[:16])

def _legend_columns(n_series: int) -> int:
    # wrap the legend into a grid instead of one long row so it never
    # clips on narrow (mobile) screens -- 3 per row reads fine down to
    # phone width for city-name-length labels
    return max(1, min(3, n_series))


st.subheader("Average temperature by day")
temp_chart = (
    alt.Chart(filtered)
    .mark_line(point=True)
    .encode(
        x=alt.X("forecast_date:T", title=None),
        y=alt.Y("avg_temperature_c:Q", title="°C"),
        color=alt.Color(
            "city:N",
            title=None,
            legend=alt.Legend(orient="bottom", direction="horizontal", columns=_legend_columns(len(selected_cities))),
        ),
        tooltip=["forecast_date:T", "city:N", "avg_temperature_c:Q"],
    )
    .properties(height=320)
)
st.altair_chart(temp_chart, use_container_width=True)

st.subheader("Max wind speed by day")
wind_chart = (
    alt.Chart(filtered)
    .mark_bar()
    .encode(
        x=alt.X("forecast_date:T", title=None),
        y=alt.Y("max_wind_speed_kmh:Q", title="km/h"),
        color=alt.Color(
            "city:N",
            title=None,
            legend=alt.Legend(orient="bottom", direction="horizontal", columns=_legend_columns(len(selected_cities))),
        ),
        tooltip=["forecast_date:T", "city:N", "max_wind_speed_kmh:Q"],
    )
    .properties(height=320)
)
st.altair_chart(wind_chart, use_container_width=True)

st.subheader("Gold layer (daily, per city)")
st.dataframe(filtered, use_container_width=True)
