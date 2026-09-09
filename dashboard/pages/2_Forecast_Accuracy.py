"""DS page: how forecast error grows with lead time.

Reads gold.forecast_accuracy_by_lead_time, written by
ds/eval_forecast_accuracy.py. This table is expected to be sparse or empty
immediately after the project is first published -- it fills in as the
daily pipeline accumulates forecast snapshots and the historical archive
catches up (see the docstring in ds/eval_forecast_accuracy.py for why).
"""

from pathlib import Path

import duckdb
import streamlit as st

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DB_PATH = REPO_ROOT / "data" / "weather.duckdb"

st.set_page_config(page_title="Forecast Accuracy", page_icon=None, layout="wide")
st.title("Forecast Accuracy by Lead Time")
st.caption(
    "Does Open-Meteo's own forecast get less accurate the further out it "
    "predicts? Built from the DE pipeline's append-only forecast snapshots "
    "compared against actual observed weather."
)


@st.cache_data(ttl=3600)
def load_accuracy():
    con = duckdb.connect(str(DB_PATH), read_only=True)
    try:
        return con.execute("SELECT * FROM gold.forecast_accuracy_by_lead_time").df()
    finally:
        con.close()


if not DB_PATH.exists():
    st.error("No data found yet. Run the DE and DS pipelines first.")
    st.stop()

try:
    df = load_accuracy()
except duckdb.CatalogException:
    st.info(
        "This table hasn't been built yet. Run `python3 ds/eval_forecast_accuracy.py` "
        "(part of `ds/run_ds_pipeline.py`)."
    )
    st.stop()

if df.empty:
    st.info(
        "No overlap yet between forecast snapshots and confirmed actuals -- "
        "this is expected right after launch. The DE pipeline needs to run "
        "for a few days (building up forecast snapshots at different lead "
        "times) before this table has anything to show. Check back after "
        "the scheduled GitHub Actions runs accumulate some history."
    )
    st.stop()

by_lead_time = df.groupby("lead_time_days")["abs_error_c"].agg(["mean", "count"]).reset_index()
by_lead_time.columns = ["lead_time_days", "mean_absolute_error_c", "n_observations"]

st.subheader("Mean absolute error by lead time")
st.bar_chart(by_lead_time.set_index("lead_time_days")["mean_absolute_error_c"])
st.dataframe(by_lead_time, use_container_width=True)

st.subheader("Raw comparisons")
st.dataframe(df.sort_values(["forecast_date", "city", "lead_time_days"]), use_container_width=True)
