-- Gold layer: hourly, per-city temperature for the "temperature by hour"
-- dashboard chart. Unlike gold_weather_daily this doesn't aggregate --
-- stg_weather_hourly is already at the hourly grain -- it just curates
-- the columns the dashboard needs and adds a surrogate key, keeping the
-- dashboard reading only from gold like everything else it displays.

select
    md5(city || '|' || cast(forecast_time as varchar)) as city_hour_key,
    city,
    country,
    forecast_time,
    temperature_c,
    ingested_at as last_ingested_at
from {{ ref('stg_weather_hourly') }}
order by forecast_time, city
