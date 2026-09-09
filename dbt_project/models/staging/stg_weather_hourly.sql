-- Silver layer: unpack the bronze JSON blob into one typed row per
-- (city, forecast hour), keeping only the most recent ingestion run per
-- city so re-running the pipeline doesn't duplicate overlapping forecast
-- windows.

with bronze as (
    select *
    from {{ source('bronze', 'weather_raw') }}
),

latest_run_per_city as (
    select
        city,
        max(ingested_at) as latest_ingested_at
    from bronze
    group by city
),

bronze_latest as (
    select b.*
    from bronze b
    inner join latest_run_per_city l
        on b.city = l.city
        and b.ingested_at = l.latest_ingested_at
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
    from bronze_latest
)

select
    city,
    country,
    latitude,
    longitude,
    strptime(forecast_time_raw, '%Y-%m-%dT%H:%M') as forecast_time,
    temperature_c,
    relative_humidity_pct,
    precipitation_mm,
    wind_speed_kmh,
    ingested_at
from unnested
-- Not every location Open-Meteo covers uses a weather model with a full
-- forecast_days-length horizon (see ingestion/fetch_weather.py) -- some
-- regions default to a shorter-range model, so the tail end of the
-- requested window comes back with null temperature/wind for those
-- hours rather than an error. Drop those hours here instead of letting
-- nulls reach gold: a city's forecast window simply ends where its
-- model's coverage ends, it isn't broken data.
where temperature_c is not null
    and wind_speed_kmh is not null
