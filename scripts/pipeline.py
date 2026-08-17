"""
aqi-hcho-project pipeline
Raw satellite + ground CSVs -> calibrated, filtered, CLUSTERED hotspot output.

Run from repo root: python scripts/pipeline.py
Reads:  data/raw/
Writes: data/processed/

Output schema (see docs/SCHEMA.md):
  hotspot_id, region_id, date, centroid_lat, centroid_lon,
  severity, hcho_value, affected_cell_count
"""
import pandas as pd
import numpy as np
import glob
import json
import os
from sklearn.cluster import DBSCAN

RAW_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "raw")
OUT_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "processed")
os.makedirs(OUT_DIR, exist_ok=True)

CLOUD_FRACTION_MAX = 0.3          # quality filter threshold
GRID_STEP_DEG = 0.05              # satellite grid spacing
CLUSTER_EPS_DEG = GRID_STEP_DEG * 1.1   # cluster cells that are grid-adjacent


# ---------- Load ----------

def load_satellite() -> pd.DataFrame:
    files = glob.glob(f"{RAW_DIR}/satellite_hcho_*_historical_*.csv") + \
            glob.glob(f"{RAW_DIR}/satellite_hcho_*_live_*.csv")
    frames = []
    for f in files:
        df = pd.read_csv(f, encoding="latin-1")
        df["period"] = "historical" if "_historical_" in f else "live"
        frames.append(df)
    sat = pd.concat(frames, ignore_index=True)
    sat["date"] = pd.to_datetime(sat["date"])
    return sat


def load_ground() -> pd.DataFrame:
    """Reads the LONG-format raw ground files currently in data/raw/
    (region_id, station_id, station_name, parameter, date, value, unit, lat, lon, source).
    NOTE: this differs from the wide schema in docs/SCHEMA.md -- see the flagged
    mismatch there. This loader adapts to what's actually on disk today."""
    files = glob.glob(f"{RAW_DIR}/ground_aqi_*_historical.csv") + \
            glob.glob(f"{RAW_DIR}/ground_aqi_*_live.csv")
    frames = []
    for f in files:
        df = pd.read_csv(f, encoding="latin-1")
        df["period"] = "historical" if "historical" in f else "live"
        frames.append(df)
    ground = pd.concat(frames, ignore_index=True)
    ground["date"] = pd.to_datetime(ground["date"])
    ground["unit"] = ground["unit"].str.strip()
    return ground


# ---------- Calibrate ----------

def calibrate(sat: pd.DataFrame, ground: pd.DataFrame) -> pd.DataFrame:
    """Bias-correct live-period satellite HCHO using ground PM2.5/NO2 as reference.
    Historical period stays as the uncorrected baseline."""
    snapshot_dates = sat.groupby(["region_id", "period"])["date"].first().reset_index()

    matched_rows = []
    for _, row in snapshot_dates.iterrows():
        sub = ground[(ground["region_id"] == row["region_id"]) & (ground["period"] == row["period"])].copy()
        sub["date_diff"] = (sub["date"] - row["date"]).abs()
        nearest_date = sub.loc[sub["date_diff"].idxmin(), "date"]
        matched_rows.append(sub[sub["date"] == nearest_date])
    ground_on_snapshot = pd.concat(matched_rows, ignore_index=True)

    ground_ref = (ground_on_snapshot[ground_on_snapshot["parameter"].isin(["pm25", "no2"])]
                  .groupby(["region_id", "period", "parameter"])["value"].mean().unstack())
    sat_ref = sat.groupby(["region_id", "period"])["hcho_value"].mean().unstack(level="period")

    scale = {}
    for region in sat["region_id"].unique():
        hist_hcho, live_hcho = sat_ref.loc[region, "historical"], sat_ref.loc[region, "live"]
        hist_pm25, live_pm25 = ground_ref.loc[(region, "historical"), "pm25"], ground_ref.loc[(region, "live"), "pm25"]
        hist_no2, live_no2 = ground_ref.loc[(region, "historical"), "no2"], ground_ref.loc[(region, "live"), "no2"]

        observed_ratio = live_hcho / hist_hcho
        expected_ratio = np.mean([live_pm25 / hist_pm25, live_no2 / hist_no2])
        bias_factor = observed_ratio / expected_ratio
        scale[region] = 1 / bias_factor

    sat = sat.copy()
    sat["calibration_scale_live"] = sat["region_id"].map(scale)
    sat["hcho_calibrated"] = np.where(
        sat["period"] == "live",
        sat["hcho_value"] * sat["calibration_scale_live"],
        sat["hcho_value"]
    )
    return sat.drop(columns=["calibration_scale_live"])


# ---------- Filter ----------

def filter_quality(sat: pd.DataFrame) -> pd.DataFrame:
    """Drop low-quality retrievals: cloud_fraction above threshold.
    (No QA column exists in the raw files -- flagged separately, cannot filter on it.)"""
    return sat[sat["cloud_fraction"] <= CLOUD_FRACTION_MAX].copy()


# ---------- Cluster into hotspots ----------

def severity_label(value: float, mean: float, std: float) -> str:
    if value > mean + 2 * std:
        return "severe"
    elif value > mean + 1.5 * std:
        return "high"
    else:
        return "moderate"


def cluster_hotspots(sat: pd.DataFrame) -> pd.DataFrame:
    """For the live period: flag cells above (mean + 1 std) per region, then
    merge grid-adjacent flagged cells into single hotspot clusters using DBSCAN."""
    live = sat[sat["period"] == "live"].copy()
    mean_v, std_v = live["hcho_calibrated"].mean(), live["hcho_calibrated"].std()
    threshold = mean_v + std_v
    flagged = live[live["hcho_calibrated"] > threshold].copy()

    hotspot_rows = []
    hotspot_counter = 1
    for region, sub in flagged.groupby("region_id"):
        if len(sub) == 0:
            continue
        coords = sub[["lat", "lon"]].values
        db = DBSCAN(eps=CLUSTER_EPS_DEG, min_samples=1).fit(coords)
        sub = sub.copy()
        sub["cluster"] = db.labels_

        for cluster_id, cluster_df in sub.groupby("cluster"):
            hotspot_rows.append({
                "hotspot_id": f"HS_{hotspot_counter:03d}",
                "region_id": region,
                "date": cluster_df["date"].iloc[0].strftime("%Y-%m-%d"),
                "centroid_lat": round(cluster_df["lat"].mean(), 5),
                "centroid_lon": round(cluster_df["lon"].mean(), 5),
                "severity": severity_label(cluster_df["hcho_calibrated"].mean(), mean_v, std_v),
                "hcho_value": round(cluster_df["hcho_calibrated"].mean(), 8),
                "affected_cell_count": len(cluster_df),
            })
            hotspot_counter += 1

    return pd.DataFrame(hotspot_rows).sort_values("hcho_value", ascending=False).reset_index(drop=True)


# ---------- Export ----------

def export(hotspots: pd.DataFrame, sat_full: pd.DataFrame):
    hotspots.to_csv(f"{OUT_DIR}/hotspots.csv", index=False)

    features = []
    for _, row in hotspots.iterrows():
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [row["centroid_lon"], row["centroid_lat"]]},
            "properties": {k: row[k] for k in
                            ["hotspot_id", "region_id", "date", "severity", "hcho_value", "affected_cell_count"]}
        })
    with open(f"{OUT_DIR}/hotspots.geojson", "w") as f:
        json.dump({"type": "FeatureCollection", "features": features}, f, indent=2)

    # keep full filtered+calibrated grid points too, for anyone who needs raw-cell detail
    sat_full.to_csv(f"{OUT_DIR}/gridpoints_calibrated_filtered.csv", index=False)


def run():
    sat = load_satellite()
    ground = load_ground()
    sat = calibrate(sat, ground)
    sat = filter_quality(sat)
    hotspots = cluster_hotspots(sat)
    export(hotspots, sat)
    print(f"{len(sat[sat.period=='live'])} live grid cells after filtering.")
    print(f"{len(hotspots)} clustered hotspots identified.")
    print(hotspots.head(10).to_string(index=False))


if __name__ == "__main__":
    run()
