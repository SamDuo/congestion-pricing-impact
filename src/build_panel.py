"""Run the SQL over the raw TLC Parquet files and write the small tables the analysis uses.

    DATA_DIR=/path/to/tlc python src/build_panel.py

Writes results/crz_zones.csv (zone classification) and results/daily_panel.csv
(about 30k rows covering 2022-11-01 to 2025-03-31).
"""
from __future__ import annotations

import os
import time
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[1]
DATA = Path(os.environ.get("DATA_DIR", ROOT / "data"))
RESULTS = ROOT / "results"
START, END = "2022-11-01", "2025-03-31"


def sql(name: str, **params: str) -> str:
    return (ROOT / "sql" / name).read_text().format(**params)


def main() -> None:
    RESULTS.mkdir(exist_ok=True)
    con = duckdb.connect()
    con.execute(f"SET temp_directory = '{DATA / 'duckdb_tmp'}'")
    con.execute("SET memory_limit = '20GB'")
    con.execute(f"CREATE TABLE zone_lookup AS SELECT * FROM read_csv_auto('{DATA / 'taxi_zone_lookup.csv'}')")

    # 1. Classify zones from the fee the trips actually paid.
    zones = con.sql(sql("01_crz_zones.sql", fhvhv_feb_2025=str(DATA / "fhvhv_tripdata_2025-02.parquet"))).df()
    zones.to_csv(RESULTS / "crz_zones.csv", index=False)
    con.register("crz_zones", zones)
    con.execute("""
        CREATE TABLE zone_area AS
        SELECT l.LocationID AS zone,
               CASE WHEN c.crz_status = 'crz' THEN 'crz'
                    WHEN c.crz_status = 'buffer' THEN 'buffer'
                    WHEN l.Borough = 'Manhattan' THEN 'manhattan_north'
                    WHEN l.Borough IN ('Unknown', 'N/A') OR l.Borough IS NULL THEN 'unknown'
                    ELSE 'outer' END AS area
        FROM zone_lookup l LEFT JOIN crz_zones c ON c.zone = l.LocationID
    """)
    counts = con.sql("SELECT area, count(*) n FROM zone_area GROUP BY area ORDER BY area").fetchall()
    print("zones by area:", dict(counts))

    # 2. Daily panel over every downloaded month.
    t0 = time.time()
    panel = con.sql(sql("02_daily_panel.sql",
                        yellow_glob=str(DATA / "yellow_tripdata_*.parquet"),
                        fhvhv_glob=str(DATA / "fhvhv_tripdata_*.parquet"),
                        start=START, end=END)).df()
    panel.to_csv(RESULTS / "daily_panel.csv", index=False)
    print(f"daily panel: {len(panel):,} rows, {int(panel.loc[(panel.daypart == 'all') & (panel.company == 'all'), 'trips'].sum()):,} trips, "
          f"{time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
