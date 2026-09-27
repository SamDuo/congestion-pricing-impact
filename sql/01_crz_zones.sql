-- Which taxi zones sit inside the Congestion Relief Zone (CRZ)?
--
-- The TLC files do not flag the zone, but from 2025 every trip carries the fee it paid.
-- A trip that starts and ends in the same taxi zone can only pay the CRZ fee if that zone is
-- inside the CRZ, so the share of such trips that paid identifies the zone from the data
-- itself. Zones with too few intra-zone trips (Battery Park, for one) fall back to the share
-- of all pickups that paid: every trip that starts inside the CRZ pays.
-- February 2025 is used because the fee started on 5 January.
--
-- Inputs: {fhvhv_feb_2025} (one month of Uber/Lyft trips), zone_lookup (TLC taxi zones).

WITH trips AS (
    SELECT PULocationID, DOLocationID, (cbd_congestion_fee > 0)::INTEGER AS paid
    FROM read_parquet('{fhvhv_feb_2025}')
),
intra AS (
    SELECT PULocationID AS LocationID, count(*) AS intra_zone_trips, avg(paid) AS intra_paid_share
    FROM trips WHERE PULocationID = DOLocationID GROUP BY 1
),
pickups AS (
    SELECT PULocationID AS LocationID, count(*) AS pickups, avg(paid) AS pickup_paid_share
    FROM trips GROUP BY 1
)
SELECT l.LocationID AS zone,
       l.Borough    AS borough,
       l.Zone       AS zone_name,
       coalesce(i.intra_zone_trips, 0) AS intra_zone_trips,
       i.intra_paid_share,
       coalesce(p.pickups, 0) AS pickups,
       p.pickup_paid_share,
       CASE
         WHEN coalesce(i.intra_zone_trips, 0) >= 30 THEN
           CASE WHEN i.intra_paid_share >= 0.75 THEN 'crz'
                WHEN i.intra_paid_share >= 0.10 THEN 'buffer'   -- straddles 60th St or pass-through heavy
                ELSE 'outside' END
         WHEN p.pickup_paid_share >= 0.95 THEN 'crz'
         ELSE 'outside'
       END AS crz_status
FROM zone_lookup l
LEFT JOIN intra i ON i.LocationID = l.LocationID
LEFT JOIN pickups p ON p.LocationID = l.LocationID
ORDER BY intra_paid_share DESC NULLS LAST;
