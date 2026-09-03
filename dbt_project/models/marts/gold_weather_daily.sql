-- Gold layer: daily, per-city aggregates for reporting/dashboarding.
-- This is the layer a BI tool or the Streamlit dashboard should read from.

select
    md5(city || '|' || cast(cast(forecast_time as date) as varchar)) as city_date_key,
    city,
    country,
    cast(forecast_time as date)      as forecast_date,
    round(avg(temperature_c), 1)     as avg_temperature_c,
    round(min(temperature_c), 1)     as min_temperature_c,
    round(max(temperature_c), 1)     as max_temperature_c,
    round(avg(relative_humidity_pct), 1) as avg_humidity_pct,
    round(sum(precipitation_mm), 2)  as total_precipitation_mm,
    round(max(wind_speed_kmh), 1)    as max_wind_speed_kmh,
    max(ingested_at)                 as last_ingested_at
from {{ ref('stg_weather_hourly') }}
group by city, country, cast(forecast_time as date)
order by forecast_date, city
