"""DS page: next-day temperature forecasting model results.

Reads only from the gold layer (gold.model_evaluation, gold.model_predictions,
gold.model_feature_importance), written by ds/train.py -- same
read-only-from-gold discipline as the main dashboard.
"""

from pathlib import Path

import duckdb
import pandas as pd
import streamlit as st

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DB_PATH = REPO_ROOT / "data" / "weather.duckdb"

st.set_page_config(page_title="Forecast Model", page_icon=None, layout="wide")
st.title("Next-Day Temperature Forecasting Model")
st.caption(
    "Persistence baseline vs. linear regression vs. XGBoost, evaluated on a "
    "chronological hold-out (to avoid leaking future "
    "data into training). Trained by `ds/train.py`."
)


@st.cache_data(ttl=3600)
def load_tables():
    con = duckdb.connect(str(DB_PATH), read_only=True)
    try:
        metrics = con.execute("SELECT * FROM gold.model_evaluation ORDER BY mae_c").df()
        predictions = con.execute("SELECT * FROM gold.model_predictions").df()
        importance = con.execute("SELECT * FROM gold.model_feature_importance ORDER BY importance DESC").df()
    finally:
        con.close()
    return metrics, predictions, importance


if not DB_PATH.exists():
    st.error("No data found yet. Run `python3 ds/run_ds_pipeline.py` from the repo root first.")
    st.stop()

try:
    metrics, predictions, importance = load_tables()
except duckdb.CatalogException:
    st.warning(
        "Model tables don't exist yet. Run `python3 ds/run_ds_pipeline.py` "
        "from the repo root to backfill history, build features, and train "
        "the models."
    )
    st.stop()

st.subheader("Model comparison")
best = metrics.iloc[0]["model_name"]
st.dataframe(
    metrics.style.apply(lambda r: ["font-weight: bold" if r["model_name"] == best else "" for _ in r], axis=1),
    use_container_width=True,
)
st.caption(
    f"Lowest MAE: **{best}**. "
)

col1, col2 = st.columns([2, 1])
with col1:
    st.subheader("Predicted vs. actual")
    city_options = sorted(predictions["city"].unique())
    city = st.selectbox("City", city_options)
    model_options = sorted(predictions["model_name"].unique())
    model = st.selectbox("Model", model_options, index=model_options.index("xgboost") if "xgboost" in model_options else 0)

    subset = predictions[(predictions["city"] == city) & (predictions["model_name"] == model)].sort_values("observed_date")
    chart_df = subset.set_index("observed_date")[["actual_c", "predicted_c"]]
    st.line_chart(chart_df)

with col2:
    st.subheader("Feature importance (XGBoost)")
    st.bar_chart(importance.set_index("feature")["importance"])
