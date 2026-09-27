#!/usr/bin/env bash
# Download the NYC TLC trip records this analysis uses (about 8 GB) into $DATA_DIR.
# Three winters, November to March: 2022-23 (placebo reference), 2023-24, 2024-25.
set -euo pipefail
DATA_DIR="${DATA_DIR:-data}"
BASE=https://d37ci6vzurychx.cloudfront.net
mkdir -p "$DATA_DIR"
curl -sSf -o "$DATA_DIR/taxi_zone_lookup.csv" "$BASE/misc/taxi_zone_lookup.csv"
for ym in 2022-11 2022-12 2023-01 2023-02 2023-03 \
          2023-11 2023-12 2024-01 2024-02 2024-03 \
          2024-11 2024-12 2025-01 2025-02 2025-03; do
  for svc in yellow fhvhv; do
    f="$DATA_DIR/${svc}_tripdata_${ym}.parquet"
    [ -s "$f" ] || curl -sSf -o "$f" "$BASE/trip-data/${svc}_tripdata_${ym}.parquet"
    echo "ok $f"
  done
done
