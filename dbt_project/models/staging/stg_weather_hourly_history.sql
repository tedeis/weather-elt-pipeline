{{ config(tags=['ds']) }}

-- Silver layer (DS project): unpack the bronze JSON blob into one typed row
-- per (city, ingestion run, forecast hour) -- WITHOUT collapsing to the
-- latest ingestion run the way stg_weather_hourly does.
--
-- stg_weather_hourly is correct for the main dashboard, which only ever
-- wants "the current forecast." But ds/eval_forecast_accuracy.py needs
-- every historical snapshot -- the same calendar date predicted multiple
-- times from different lead times -- to measure how forecast error grows
-- the further out Open-Meteo predicts. bronze.weather_raw is already
-- append-only (see ingestion/fetch_weather.py: every run INSERTs a new
-- ingested_at snapshot, nothing is ever overwritten), so this model just
-- types and unnests it as-is, one row per (city, ingested_at,
-- forecast_time), instead of filtering down to one ingestion run per city
-- first. That filtering-too-early was the bug that kept
-- gold.forecast_accuracy_by_lead_time empty no matter how long the daily
-- pipeline had been running: stg_weather_hourly only ever exposed a single
-- forecast_made_on value, whose forecast dates are all in the future and
-- can never overlap with confirmed actuals.

with bronze as (
    select *
    from {{ source('bronze', 'weather_raw') }}
),

unnested as (
    select
        city,
        country,
        latitude,
        longitude,
        ingested_at,
        unnest(cast(json_extract(raw_response, '$.hourly.time') as varchar[]))            as forecast_time_raw,
        unnest(cast(json_extract(raw_response, '$.hourly.temperature_2m') as double[]))    as temperature_c,
        unnest(cast(json_extract(raw_response, '$.hourly.relative_humidity_2m') as double[])) as relative_humidity_pct,
        unnest(cast(json_extract(raw_response, '$.hourly.precipitation') as double[]))     as precipitation_mm,
        unnest(cast(json_extract(raw_response, '$.hourly.wind_speed_10m') as double[]))    as wind_speed_kmh
    from bronze
)

select
    city,
    country,
    latitude,
    longitude,
    ingested_at,
    strptime(forecast_time_raw, '%Y-%m-%dT%H:%M') as forecast_time,
    temperature_c,
    relative_humidity_pct,
    precipitation_mm,
    wind_speed_kmh
from unnested
-- Same reasoning as stg_weather_hourly: some cities' weather model doesn't
-- cover the full requested forecast window, so the tail comes back null
-- rather than erroring. Drop those hours here rather than let nulls reach
-- the accuracy comparison.
where temperature_c is not null
    and wind_speed_kmh is not null
