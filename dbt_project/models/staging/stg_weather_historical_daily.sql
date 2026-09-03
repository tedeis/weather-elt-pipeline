{{ config(tags=['ds']) }}

-- Silver layer for the DS project: unpack the historical-archive JSON blob
-- into one typed row per (city, actual calendar date). Actual daily
-- observations (ERA5 reanalysis), not forecasts -- this is the DS
-- project's training data. Deduplicated to each city's most recent
-- backfill run per date, same pattern as stg_weather_hourly.

with bronze as (
    select *
    from {{ source('bronze_historical', 'weather_historical_raw') }}
),

unnested as (
    select
        city,
        country,
        ingested_at,
        unnest(cast(json_extract(raw_response, '$.daily.time') as varchar[]))                    as observed_date_raw,
        unnest(cast(json_extract(raw_response, '$.daily.temperature_2m_mean') as double[]))      as temperature_mean_c,
        unnest(cast(json_extract(raw_response, '$.daily.temperature_2m_max') as double[]))       as temperature_max_c,
        unnest(cast(json_extract(raw_response, '$.daily.temperature_2m_min') as double[]))       as temperature_min_c,
        unnest(cast(json_extract(raw_response, '$.daily.precipitation_sum') as double[]))        as precipitation_mm,
        unnest(cast(json_extract(raw_response, '$.daily.wind_speed_10m_max') as double[]))       as wind_speed_max_kmh
    from bronze
),

typed as (
    select
        city,
        country,
        cast(observed_date_raw as date) as observed_date,
        temperature_mean_c,
        temperature_max_c,
        temperature_min_c,
        precipitation_mm,
        wind_speed_max_kmh,
        ingested_at,
        row_number() over (
            partition by city, cast(observed_date_raw as date)
            order by ingested_at desc
        ) as rn
    from unnested
    -- the archive API finalizes ERA5 reanalysis with roughly a 5-day lag,
    -- so very recent dates can come back null -- drop those rather than
    -- feed nulls into feature engineering downstream
    where temperature_mean_c is not null
)

select
    city,
    country,
    observed_date,
    temperature_mean_c,
    temperature_max_c,
    temperature_min_c,
    precipitation_mm,
    wind_speed_max_kmh,
    ingested_at
from typed
where rn = 1
