-- Daily panel: one row per service x company x trip group x daypart x pickup date, plus pooled 'all' rows.
--
-- Trip groups, by where the trip starts and ends:
--   crz_internal     both ends inside the CRZ
--   crz_boundary     one end inside the CRZ, the other outside it (not in the buffer)
--   manhattan_north  both ends in Manhattan north of the CRZ (possible spillover)
--   outer            both ends outside Manhattan (the control group: these trips almost never pay)
--   other            everything else (buffer zones, unknown zones, upper Manhattan to outer boroughs)
--
-- Daypart follows the MTA passenger-car toll schedule, which is where the traffic effect should
-- come from: 'day' is weekdays 05:00-21:00 and weekends 09:00-21:00, 'night' is the rest.
--
-- Inputs: {yellow_glob}, {fhvhv_glob}, tables zone_area(zone, area) and nothing else.

WITH trips AS (
    SELECT 'yellow'                                                     AS service,
           'yellow'                                                     AS company,
           tpep_pickup_datetime                                         AS pickup_ts,
           CAST(NULL AS TIMESTAMP)                                      AS request_ts,
           CAST(NULL AS TIMESTAMP)                                      AS on_scene_ts,
           PULocationID                                                 AS pu,
           DOLocationID                                                 AS dropoff_zone,
           trip_distance                                                AS miles,
           date_diff('second', tpep_pickup_datetime, tpep_dropoff_datetime) AS secs,
           fare_amount                                                  AS fare,
           tip_amount                                                   AS tip,
           CAST(NULL AS DOUBLE)                                         AS driver_pay,
           coalesce(cbd_congestion_fee, 0)                              AS cbd_fee
    FROM read_parquet('{yellow_glob}', union_by_name = true)

    UNION ALL

    SELECT 'rideshare',
           CASE hvfhs_license_num WHEN 'HV0003' THEN 'uber' WHEN 'HV0005' THEN 'lyft' ELSE 'other' END,
           pickup_datetime, request_datetime, on_scene_datetime,
           PULocationID, DOLocationID, trip_miles, trip_time,
           base_passenger_fare, tips, driver_pay,
           coalesce(cbd_congestion_fee, 0)
    FROM read_parquet('{fhvhv_glob}', union_by_name = true)
),

clean AS (
    -- Plausible trips only: 1 minute to 3 hours, 0.1 to 60 miles, under 60 mph, fare not negative.
    SELECT *
    FROM trips
    WHERE secs BETWEEN 60 AND 10800
      AND miles BETWEEN 0.1 AND 60
      AND miles / (secs / 3600.0) < 60
      AND fare >= 0
      AND CAST(pickup_ts AS DATE) BETWEEN DATE '{start}' AND DATE '{end}'
),

labelled AS (
    SELECT c.*,
           CASE
             WHEN pa.area = 'crz' AND da.area = 'crz' THEN 'crz_internal'
             WHEN (pa.area = 'crz') <> (da.area = 'crz')
                  AND pa.area NOT IN ('buffer', 'unknown')
                  AND da.area NOT IN ('buffer', 'unknown') THEN 'crz_boundary'
             WHEN pa.area = 'manhattan_north' AND da.area = 'manhattan_north' THEN 'manhattan_north'
             WHEN pa.area = 'outer' AND da.area = 'outer' THEN 'outer'
             ELSE 'other'
           END AS trip_group,
           CASE
             WHEN isodow(pickup_ts) <= 5 AND hour(pickup_ts) BETWEEN 5 AND 20 THEN 'day'
             WHEN isodow(pickup_ts) >= 6 AND hour(pickup_ts) BETWEEN 9 AND 20 THEN 'day'
             ELSE 'night'
           END AS daypart,
           CAST(pickup_ts AS DATE) AS day,
           CASE WHEN on_scene_ts > request_ts
                     AND date_diff('second', request_ts, on_scene_ts) < 3600
                THEN date_diff('second', request_ts, on_scene_ts) END AS wait_secs
    FROM clean c
    LEFT JOIN zone_area pa ON pa.zone = c.pu
    LEFT JOIN zone_area da ON da.zone = c.dropoff_zone
)

SELECT service,
       coalesce(company, 'all')           AS company,   -- 'all' pools Uber and Lyft (Lyft credited the fee back in Jan 2025)
       trip_group,
       coalesce(daypart, 'all')           AS daypart,   -- 'all' rows pool the two dayparts
       day,
       count(*)                           AS trips,
       sum(miles)                         AS miles,
       sum(secs) / 3600.0                 AS hours,
       sum(fare)                          AS fare,
       sum(tip)                           AS tips,
       sum(driver_pay)                    AS driver_pay,
       sum(cbd_fee)                       AS cbd_fee,
       sum((cbd_fee > 0)::INTEGER)        AS trips_paying_fee,
       median(wait_secs)                  AS median_wait_secs,
       count(wait_secs)                   AS trips_with_wait
FROM labelled
GROUP BY GROUPING SETS ((service, trip_group, daypart, day), (service, trip_group, day),
                        (service, company, trip_group, daypart, day), (service, company, trip_group, day))
ORDER BY service, company, trip_group, daypart, day;
