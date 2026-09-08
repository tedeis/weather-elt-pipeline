# Weather Medallion Pipeline

A batch ELT pipeline that pulls live hourly forecast data from the
[Open-Meteo](https://open-meteo.com/) API for eight cities worldwide and
runs it through a bronze/silver/gold medallion architecture using dbt and
DuckDB. Scheduled with GitHub Actions and served through a Streamlit
dashboard.

On top of the same setup, a **data science extension** (`ds/`) asks two
questions about the forecasts this pipeline collects: how good are they,
and can a purpose-built model beat them on next-day temperature? See
[DS project](#data-science-extension) below.

**Live dashboard:** _add your Streamlit Community Cloud URL here after deploying_

## Architecture

```mermaid
flowchart LR
    A[Open-Meteo API] -->|fetch_weather.py| B[(Bronze\nraw JSON + metadata)]
    B -->|dbt: stg_weather_hourly| C[(Silver\ntyped, deduped hourly rows)]
    C -->|dbt: gold_weather_daily| D[(Gold\ndaily per-city aggregates)]
    D --> E[Streamlit dashboard]
    D --> F[CSV export]

    G[GitHub Actions\ndaily cron] -.orchestrates.-> A
    G -.orchestrates.-> B
    G -.orchestrates.-> C
    G -.orchestrates.-> D

    H[Open-Meteo Archive API] -->|backfill_historical.py| I[(Bronze\nactual daily weather)]
    I -->|dbt: stg_weather_historical_daily| J[(Silver\ntyped actuals)]
    J -->|train.py: features + models| K[(Gold\nmodel eval + predictions)]
    B -.snapshots.-> L[(Gold\naccuracy by lead time)]
    J -.actuals.-> L
    K --> M[Dashboard: Forecast Model page]
    L --> N[Dashboard: Forecast Accuracy page]
```

**Bronze.** `ingestion/fetch_weather.py` calls the Open-Meteo forecast API
for each city in `ingestion/cities.py` and lands the raw JSON response,
untouched, into a DuckDB table (`bronze.weather_raw`) with ingestion
metadata (timestamp, city, coordinates). No parsing happens here on
purpose: if the API's schema shifts, ingestion still succeeds and nothing
breaks until the next layer.

**Silver.** `dbt_project/models/staging/stg_weather_hourly.sql` unnests
the raw JSON into one typed row per city per forecast hour, and
deduplicates to each city's latest ingestion run.

**Gold.** `dbt_project/models/marts/gold_weather_daily.sql` aggregates
silver into daily per-city metrics (avg/min/max temperature, humidity,
total precipitation, max wind speed). This is the layer a dashboard or BI
tool should read from.

**Orchestration.** `run_pipeline.py` runs ingestion, then `dbt run`, then
`dbt test`, in that order. `.github/workflows/pipeline.yml` runs that
script on a daily cron through GitHub Actions and commits the refreshed
DuckDB file back to the repo, so the pipeline actually re-runs on a
schedule instead of sitting as a one-time script.

**Serving.** `dashboard/app.py` is a Streamlit app that reads only from
the gold layer.

## Data science extension

The DE pipeline above answers "what's the forecast." The `ds/` extension
asks two follow-up questions using the same DuckDB warehouse and the same
medallion setup:

**1. Can a model beat a naive baseline at next-day temperature forecasting?**
`ds/backfill_historical.py` pulls actual (not forecast) daily weather for
the same eight cities from Open-Meteo's free Archive API (ERA5
reanalysis), so I get years of history in one call instead of waiting for
the daily pipeline to accumulate it. `dbt` parses that into a silver table
(`stg_weather_historical_daily`), then `ds/features.py` builds lag,
rolling-window, and seasonal features and `ds/train.py` trains and
compares three models: a persistence baseline ("tomorrow = today"), linear
regression, and XGBoost. All three are scored on a **chronological**
hold-out (the most recent 90 days) rather than a random split, because
shuffling a time series leaks future information into training. Results
(MAE/RMSE, predictions, feature importance) land in gold tables and show
up on the dashboard's "Forecast Model" page. `tests/test_features.py`
checks for leakage directly: that lag/rolling features never see the
current or a future row, and that the target is genuinely next-day.

**2. Does the forecast this pipeline collects get less accurate the
further out it predicts?** The DE pipeline's `bronze.weather_raw` table is
append-only, so the same calendar date accumulates predictions made at
different lead times as the daily job keeps running.
`ds/eval_forecast_accuracy.py` joins those snapshots against confirmed
actuals (once the archive API has finalized them, about 5 days later) and
aggregates mean absolute error by lead time on the dashboard's "Forecast
Accuracy" page. **This table starts empty right after launch.** It fills in
as the daily GitHub Actions run builds up history, the same way the DE
pipeline's own history does. That's expected, not a bug.

Run it:

```bash
python3 ds/run_ds_pipeline.py --years 2   # one-time: backfill + dbt + train
streamlit run dashboard/app.py             # then open the DS pages in the sidebar
```

The daily GitHub Actions workflow re-runs the accuracy tracker
automatically (best-effort: it no-ops gracefully on a repo where the
one-time backfill hasn't happened yet). Re-run `ds/train.py` now and then
as more history accumulates to retrain on a growing dataset.

## Why these choices

- **DuckDB** instead of a hosted warehouse: zero cost, zero setup, and
  anyone who clones the repo can run the whole thing locally without a
  cloud account.
- **dbt** for all silver/gold logic: SQL transforms that are version
  controlled, tested, and documented, instead of ad hoc pandas in a
  notebook.
- **GitHub Actions as the phase-1 orchestrator**: free, no infrastructure
  to stand up, and it leaves a verifiable run history (see the Actions tab
  and commit history for the daily runs) instead of something that only
  works on my machine.
- **A live public API** instead of a static CSV: this is a real ELT
  pipeline with incremental runs, not a one-off analysis of a dataset that
  never changes.
- **A chronological, not random, train/test split** for the DS models: the
  classic time-series mistake is a random shuffle that leaks future rows
  into training. The hold-out here is strictly the most recent N days.
- **A persistence baseline before any ML model**: reporting an XGBoost MAE
  with no baseline to compare against hides whether the model is actually
  worth its complexity.

## Running it locally

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

python3 run_pipeline.py         # ingests + runs dbt + tests
streamlit run dashboard/app.py  # view the dashboard locally
```

## Testing

- `pytest`: unit tests for the ingestion logic (API call, bronze landing,
  end-to-end run), with the API mocked so the suite runs offline and in CI,
  plus leakage tests for the DS feature engineering (lag/rolling features
  never see the current or a future row, the chronological split never
  overlaps, the target is genuinely next-day).
- `dbt test`: schema tests on the silver and gold models (not-null, and
  uniqueness of `(city, forecast_date)` in the gold table).

## Roadmap (in progress)

This project ships in phases; phase 1 (above) is complete and live. Next:

- [ ] Replace the GitHub Actions cron with a real Airflow DAG (containerized
      with Docker Compose) for task-level retries, backfills, and dependency
      graphs.
- [ ] Add a Dockerfile / docker-compose.yml so the whole stack (ingestion +
      dbt + Postgres) comes up with one command.
- [ ] Move DuckDB to a cloud warehouse (BigQuery or Snowflake free tier) for
      a cloud-native deployment.
- [ ] Add dbt freshness checks and Slack/email alerts on pipeline failure.
- [ ] Add a second data source (e.g. air quality) and a `dim_cities`
      dimension table for a small star schema.
- [ ] DS: once the accuracy-by-lead-time table has real history, retrain
      periodically and check whether the model's edge over persistence holds
      up out of sample, not just on one hold-out.
- [ ] DS: add a proper time-series model (e.g. Prophet) as a fourth
      comparison alongside persistence, linear, and XGBoost.

## Repo structure

```
ingestion/            bronze layer: API client + city reference list (DE)
dbt_project/          silver + gold dbt models, tests, sources (DE + DS, tag:ds separates them)
dashboard/             Streamlit app: main page (DE) + DS pages under pages/
ds/                    DS project: historical backfill, features, training, accuracy tracker
tests/                 pytest: ingestion (mocked API) + DS feature leakage tests
.github/workflows/     scheduled pipeline run (daily cron)
run_pipeline.py         DE orchestrator: ingest -> dbt run -> dbt test
ds/run_ds_pipeline.py   DS orchestrator: backfill -> dbt -> train -> accuracy tracker
data/                   DuckDB file + CSV export (committed by CI)
```
