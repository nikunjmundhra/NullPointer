"""
fix_station_coords.py

Fixes the empty lat/lon columns in the ground AQI CSVs by joining in real
per-station coordinates from ground_station_coordinates.csv (sourced from
Member 2's OpenAQ /v3/locations pull).

Join key: station_name (ground CSV) == ground_station (coordinates CSV),
exact string match. No region-center fallback -- any station_name that
fails to match is reported loudly and left as NaN so the failure is visible
rather than silently masked.

Usage:
    python3 fix_station_coords.py
"""

import sys
import pandas as pd

GROUND_FILES = [
    "ground_aqi_data1_historical.csv",
    "ground_aqi_data1_live.csv",
]
COORDS_FILE = "ground_station_coordinates.csv"

# Ground AQI CSVs use cp1252 encoding (contains µg/m³ unit strings written
# as Latin-1 bytes, not UTF-8).
GROUND_ENCODING = "cp1252"


def load_coords(path: str) -> pd.DataFrame:
    coords = pd.read_csv(path)
    required = {"state", "ground_station", "latitude", "longitude"}
    missing = required - set(coords.columns)
    if missing:
        raise ValueError(f"{path} is missing expected columns: {missing}")

    # Defensive: strip whitespace in case of copy-paste artifacts, though
    # observed data matched exactly.
    coords["ground_station"] = coords["ground_station"].str.strip()

    dupes = coords[coords.duplicated("ground_station", keep=False)]
    if not dupes.empty:
        print("WARNING: duplicate ground_station entries in coords file:")
        print(dupes.to_string())

    return coords


def fix_file(path: str, coords: pd.DataFrame) -> pd.DataFrame:
    df = pd.read_csv(path, encoding=GROUND_ENCODING)
    required = {"region_id", "station_id", "station_name", "lat", "lon"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"{path} is missing expected columns: {missing}")

    before_rows = len(df)
    stations_in_ground = set(df["station_name"].str.strip().unique())
    stations_in_coords = set(coords["ground_station"].unique())

    unmatched = stations_in_ground - stations_in_coords
    if unmatched:
        print(f"\n[{path}] ERROR: {len(unmatched)} station_name value(s) "
              f"in the ground CSV have no match in {COORDS_FILE}:")
        for name in sorted(unmatched):
            print(f"    - {name!r}")
        print("  These rows will be LEFT WITH NaN lat/lon rather than "
              "silently defaulted to a region center. Fix the name "
              "mismatch (e.g. whitespace, punctuation, encoding) and "
              "re-run.")

    # Merge in real coordinates. Drop any pre-existing (empty) lat/lon
    # columns first so the merge cleanly supplies the real values.
    df = df.drop(columns=["lat", "lon"])
    merged = df.merge(
        coords[["ground_station", "latitude", "longitude"]],
        left_on=df["station_name"].str.strip(),
        right_on="ground_station",
        how="left",
    )
    merged = merged.rename(columns={"latitude": "lat", "longitude": "lon"})
    merged = merged.drop(columns=["ground_station", "key_0"], errors="ignore")

    # Restore original column order with lat/lon in their original spots.
    original_cols = ["region_id", "station_id", "station_name", "parameter",
                      "date", "value", "unit", "lat", "lon", "source"]
    merged = merged[[c for c in original_cols if c in merged.columns]]

    after_rows = len(merged)
    if after_rows != before_rows:
        raise AssertionError(
            f"Row count changed during merge for {path}: "
            f"{before_rows} -> {after_rows}. Check for duplicate join keys."
        )

    still_null = merged["lat"].isna().sum()
    matched = after_rows - still_null
    print(f"[{path}] {matched}/{after_rows} rows matched to real coordinates "
          f"({still_null} unmatched).")

    return merged


def main():
    coords = load_coords(COORDS_FILE)
    print(f"Loaded {len(coords)} station coordinates from {COORDS_FILE}.")

    any_unmatched = False
    for path in GROUND_FILES:
        fixed = fix_file(path, coords)
        if fixed["lat"].isna().any():
            any_unmatched = True

        out_path = path.replace(".csv", "_fixed.csv")
        fixed.to_csv(out_path, index=False, encoding="utf-8")
        print(f"  -> wrote {out_path}\n")

    if any_unmatched:
        print("DONE WITH WARNINGS: one or more files still have unmatched "
              "stations. Do not feed these into calibration until resolved.")
        sys.exit(1)
    else:
        print("DONE: all stations matched successfully in every file.")


if __name__ == "__main__":
    main()
