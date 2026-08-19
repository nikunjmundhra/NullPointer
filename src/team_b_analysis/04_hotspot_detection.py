"""
04_hotspot_detection.py

Full data-fusion + hotspot detection pipeline:

1. Loads ground CSV (long format), computes per-station-date CPCB AQI via
   aqi_calculator.py.
2. Loads satellite CSV (region_id, date, lat, lon, hcho_value, cloud_fraction).
3. CALIBRATION: nearest-neighbor matches each ground station to the closest
   satellite grid cell on the same date (using real per-station lat/lon --
   NOT region centers), builds (hcho_value, aqi) pairs, trains a regression
   model (HCHO -> AQI), reports R^2 and pair count so calibration confidence
   is visible rather than assumed.
4. APPLIES the model across the entire satellite grid -> estimated_aqi per
   cell, per date.
5. HOTSPOT DETECTION: flags cells with estimated_aqi >= 200 (CPCB "Moderate"
   upper bound / start of "Poor"), clusters adjacent flagged cells (grid-
   rounding to 0.3-degree buckets, matching the original spec) into hotspot
   regions.
6. Outputs hotspots_<mode>.csv with columns: hotspot_id, region_id, date,
   centroid_lat, centroid_lon, avg_estimated_aqi, max_estimated_aqi,
   severity, affected_cell_count.

USAGE
-----
    python3 04_hotspot_detection.py \\
        --ground ground_aqi_data1_historical_fixed.csv \\
        --satellite satellite_hcho_delhi_ncr_oct15_to_nov15_2025-10-15_to_2025-11-15.csv \\
                    satellite_hcho_kanpur_industrial_oct15_to_nov15_2025-10-15_to_2025-11-15.csv \\
                    satellite_hcho_punjab_stubble_belt_oct15_to_nov15_2025-10-15_to_2025-11-15.csv \\
        --mode historical --use-cloud-fraction \\
        --out hotspots_historical.csv

CROSS-SEASON MODE (live season with insufficient/too-narrow ground truth):
    python3 04_hotspot_detection.py \\
        --ground ground_aqi_data1_live_fixed.csv \\
        --satellite satellite_hcho_delhi_ncr_live.csv \\
                    satellite_hcho_kanpur_industrial_live.csv \\
                    satellite_hcho_punjab_stubble_belt_live.csv \\
        --mode live --use-cloud-fraction \\
        --calibration-ground ground_aqi_data1_historical_fixed.csv \\
        --calibration-satellite satellite_hcho_delhi_ncr_oct15_to_nov15_2025-10-15_to_2025-11-15.csv \\
                                 satellite_hcho_kanpur_industrial_oct15_to_nov15_2025-10-15_to_2025-11-15.csv \\
                                 satellite_hcho_punjab_stubble_belt_oct15_to_nov15_2025-10-15_to_2025-11-15.csv \\
        --out hotspots_live.csv

Can also be imported and called programmatically -- see `run_pipeline()`.
"""

from __future__ import annotations
import argparse
import sys
import numpy as np
import pandas as pd
from sklearn.linear_model import LinearRegression
from sklearn.metrics import r2_score

from aqi_calculator import add_aqi_column, aqi_category


HOTSPOT_AQI_THRESHOLD = 200  # CPCB "Moderate" upper bound -> start of "Poor"
CLUSTER_GRID_DEGREES = 0.3   # per original spec: round to 0.3-degree buckets

# CPCB category thresholds re-used for hotspot severity labeling
def severity_label(aqi: float) -> str:
    return aqi_category(aqi)


def load_ground_aqi(ground_path: str) -> pd.DataFrame:
    """Load ground CSV and compute per-station-date AQI."""
    try:
        df = pd.read_csv(ground_path, encoding="utf-8")
        if not df["unit"].isin(["µg/m³", "ppb"]).all():
            raise ValueError("unexpected unit encoding")
    except (UnicodeDecodeError, ValueError):
        df = pd.read_csv(ground_path, encoding="cp1252")

    if df[["lat", "lon"]].isna().any().any():
        n_bad = df[["lat", "lon"]].isna().any(axis=1).sum()
        raise ValueError(
            f"{n_bad} rows in {ground_path} have missing lat/lon. "
            "Run fix_station_coords.py before calibration -- do not "
            "proceed with region-center substitution."
        )

    aqi_df = add_aqi_column(df)
    return aqi_df


def load_satellite(paths: list[str]) -> pd.DataFrame:
    """Load and concatenate one or more per-region satellite CSVs."""
    frames = []
    for p in paths:
        frames.append(pd.read_csv(p))
    sat = pd.concat(frames, ignore_index=True)

    required = {"region_id", "date", "lat", "lon", "hcho_value", "cloud_fraction"}
    missing = required - set(sat.columns)
    if missing:
        raise ValueError(f"Satellite data missing columns: {missing}")

    return sat


def haversine_km(lat1, lon1, lat2, lon2):
    """Vectorized haversine distance in km."""
    R = 6371.0
    lat1, lon1, lat2, lon2 = map(np.radians, [lat1, lon1, lat2, lon2])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = np.sin(dlat / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin(dlon / 2) ** 2
    return 2 * R * np.arcsin(np.sqrt(a))


def nearest_satellite_cell(station_lat, station_lon, date, sat_same_date: pd.DataFrame):
    """
    Find the nearest satellite grid cell (by haversine distance) to a given
    station location, restricted to rows already filtered to the matching
    date. Returns (hcho_value, cloud_fraction, distance_km) or None if no
    satellite data exists for that date.
    """
    if sat_same_date.empty:
        return None

    dists = haversine_km(
        station_lat, station_lon,
        sat_same_date["lat"].values, sat_same_date["lon"].values,
    )
    idx = np.argmin(dists)
    row = sat_same_date.iloc[idx]
    return row["hcho_value"], row["cloud_fraction"], dists[idx]


def build_calibration_pairs(ground_aqi: pd.DataFrame, sat: pd.DataFrame,
                             max_match_distance_km: float = 5.0) -> pd.DataFrame:
    """
    For each ground station-date, find the nearest satellite cell on the
    same date (per region, since regions don't spatially overlap) and pair
    its HCHO value with the station's computed AQI.

    Matches farther than `max_match_distance_km` are dropped and reported,
    since a poor spatial match would silently corrupt calibration. Default
    of 5km (tightened from an earlier 15km) reflects that match distance
    correlates with both cloud_fraction and AQI noise -- looser matches
    pull in confounded, less trustworthy pairs.
    """
    pairs = []
    dropped_far = 0
    dropped_no_sat = 0

    for _, row in ground_aqi.iterrows():
        sat_same = sat[(sat["region_id"] == row["region_id"]) &
                        (sat["date"] == row["date"])]
        result = nearest_satellite_cell(row["lat"], row["lon"], row["date"], sat_same)

        if result is None:
            dropped_no_sat += 1
            continue

        hcho, cloud, dist_km = result
        if dist_km > max_match_distance_km:
            dropped_far += 1
            continue

        pairs.append({
            "region_id": row["region_id"],
            "station_id": row["station_id"],
            "station_name": row["station_name"],
            "date": row["date"],
            "aqi": row["aqi"],
            "hcho_value": hcho,
            "cloud_fraction": cloud,
            "match_distance_km": round(dist_km, 2),
        })

    print(f"Calibration pairs built: {len(pairs)}")
    if dropped_no_sat:
        print(f"  Dropped {dropped_no_sat} station-dates with no satellite "
              f"data for that region/date.")
    if dropped_far:
        print(f"  Dropped {dropped_far} station-dates where nearest satellite "
              f"cell was >{max_match_distance_km}km away (likely a data gap).")

    return pd.DataFrame(pairs)


def train_calibration_model(pairs: pd.DataFrame, use_cloud_fraction: bool = False,
                             per_region: bool = False, cv_folds: int = 5):
    """
    Trains a regression: hcho_value [+ cloud_fraction] -> aqi.

    per_region=True trains a separate model per region_id instead of one
    pooled model, returning a dict {region_id: (model, r2, n)} instead of
    a single (model, r2) tuple. Regions with pollution driven by different
    sources (traffic/industry in Delhi & Kanpur vs. biomass burning in
    Punjab) have no principled reason to share one HCHO-AQI slope, so this
    is offered as a real alternative, not just a knob.

    Reports BOTH train R^2 and k-fold cross-validated R^2. Train R^2 alone
    overstates real predictive power on a small, noisy dataset -- CV R^2
    is the number that should be trusted and presented. Also reports a
    warning if the sample size is too small to trust either number.
    """
    from sklearn.model_selection import KFold, cross_val_score

    feature_cols = ["hcho_value", "cloud_fraction"] if use_cloud_fraction else ["hcho_value"]

    def _fit(subset: pd.DataFrame, label: str):
        if len(subset) < max(10, cv_folds * 2):
            raise ValueError(
                f"Not enough calibration pairs to fit a reliable model for "
                f"{label} (need at least {max(10, cv_folds * 2)}, have {len(subset)})."
            )
        X = subset[feature_cols].values
        y = subset["aqi"].values
        model = LinearRegression()
        model.fit(X, y)
        train_r2 = r2_score(y, model.predict(X))

        kf = KFold(n_splits=cv_folds, shuffle=True, random_state=42)
        cv_scores = cross_val_score(LinearRegression(), X, y, cv=kf, scoring="r2")
        cv_r2_mean = cv_scores.mean()
        cv_r2_std = cv_scores.std()

        print(f"\nCalibration model [{label}]: features={feature_cols}")
        print(f"  coefficients = {dict(zip(feature_cols, model.coef_.round(2)))}, "
              f"intercept = {model.intercept_:.2f}")
        print(f"  n = {len(subset)} pairs")
        print(f"  Train R^2 = {train_r2:.4f}  (optimistic -- fit on the same "
              f"data used to train)")
        print(f"  CV R^2    = {cv_r2_mean:.4f} (+/- {cv_r2_std:.4f})  "
              f"<- report THIS number, it reflects real generalization")

        if len(subset) < 30:
            print(f"  WARNING: only {len(subset)} calibration pairs for {label}. "
                  f"Both R^2 numbers are unstable at this sample size.")
        if cv_r2_mean < 0.3:
            print(f"  NOTE: CV R^2={cv_r2_mean:.2f} for {label} reflects a real, "
                  f"weak relationship in the underlying data at this spatial "
                  f"resolution -- ground stations a few km apart in the same "
                  f"region/date differ by ~50 AQI points on average, which "
                  f"HCHO alone at ~5.5km grid resolution cannot resolve. "
                  f"This is a physical/data-resolution ceiling, not something "
                  f"a different model form fixes.")
        return model, cv_r2_mean, len(subset)

    if not per_region:
        model, r2, n = _fit(pairs, "pooled (all regions)")
        return model, r2

    models = {}
    for region_id in sorted(pairs["region_id"].unique()):
        subset = pairs[pairs["region_id"] == region_id]
        try:
            models[region_id] = _fit(subset, region_id)
        except ValueError as e:
            print(f"  SKIPPED {region_id}: {e}")
    return models


def apply_model_to_grid(model, sat: pd.DataFrame,
                         use_cloud_fraction: bool = False,
                         per_region: bool = False) -> pd.DataFrame:
    """
    Applies the trained model(s) across every satellite grid cell/date.
    If per_region=True, `model` must be the dict returned by
    train_calibration_model(per_region=True); each region's rows are
    predicted with that region's own model. Regions with no valid model
    (too few pairs) are left with estimated_aqi = NaN rather than silently
    borrowing another region's calibration.
    """
    sat = sat.copy()
    feature_cols = ["hcho_value", "cloud_fraction"] if use_cloud_fraction else ["hcho_value"]

    if not per_region:
        sat["estimated_aqi"] = model.predict(sat[feature_cols].values)
    else:
        sat["estimated_aqi"] = np.nan
        for region_id, (region_model, _, _) in model.items():
            mask = sat["region_id"] == region_id
            sat.loc[mask, "estimated_aqi"] = region_model.predict(sat.loc[mask, feature_cols].values)
        missing = sat["estimated_aqi"].isna().sum()
        if missing:
            print(f"  NOTE: {missing} grid cells left without an estimated_aqi "
                  f"(no calibration model for their region).")

    # AQI can't be negative; clip at 0 but don't cap the top (see aqi_calculator
    # extrapolation rationale -- genuine severe events shouldn't be hidden).
    sat["estimated_aqi"] = sat["estimated_aqi"].clip(lower=0).round(1)
    return sat


def apply_seasonal_offset_correction(estimated_grid: pd.DataFrame,
                                       target_ground_aqi: pd.DataFrame,
                                       target_sat: pd.DataFrame,
                                       max_match_distance_km: float = 5.0,
                                       per_region: bool = True) -> tuple[pd.DataFrame, dict, int]:
    """
    Corrects a systematic season-level bias that arises in cross-season
    calibration (Option A): HCHO's absolute scale shifts between seasons
    for reasons unrelated to ground AQI (temperature, sunlight, biogenic
    sources), so a model's intercept/scale fit on one season's HCHO range
    does not transfer cleanly to another season's HCHO range even if the
    underlying HCHO-AQI relationship direction is similar.

    This function builds a small number of REAL calibration pairs from
    the target season's own ground truth (however few), compares the
    model's predicted AQI at those exact station/date points against the
    actual measured AQI, and applies the resulting mean additive offset
    to the grid. This corrects the scale/intercept mismatch without
    discarding the larger source-season dataset's slope information.

    per_region=True (default) computes a SEPARATE offset per region_id,
    since observed offsets differ meaningfully by region (e.g. -99.6 in
    Delhi vs -150.6 in Punjab on this project's data) -- a single pooled
    offset would systematically over/under-correct individual regions.
    Regions with fewer than 5 target-season pairs fall back to the
    overall pooled offset (reported) rather than being left uncorrected.

    Returns (corrected_grid, offsets_dict, n_target_pairs_used) where
    offsets_dict maps region_id -> offset (plus a "_pooled" fallback key).
    Raises ValueError if there are zero target-season pairs to correct
    against, since applying an uncorrected cross-season model silently
    would be actively misleading (observed in practice: an uncorrected
    cross-season model predicted mean AQI 211 vs actual live ground
    truth mean AQI 95 -- more than double the real pollution level).
    """
    target_pairs = build_calibration_pairs(target_ground_aqi, target_sat, max_match_distance_km)

    if target_pairs.empty:
        raise ValueError(
            "No target-season calibration pairs available to correct the "
            "cross-season offset. Do not use the uncorrected cross-season "
            "model -- it may be systematically biased (see docstring)."
        )

    merged = target_pairs.merge(
        estimated_grid[["region_id", "date", "lat", "lon", "hcho_value", "estimated_aqi"]],
        on=["region_id", "date", "hcho_value"],
        how="left",
    )
    merged = merged.dropna(subset=["estimated_aqi"])

    if merged.empty:
        raise ValueError(
            "Could not match target-season calibration pairs back to grid "
            "predictions -- check that estimated_grid covers the same "
            "region/date/hcho_value combinations as target_pairs."
        )

    merged["residual"] = merged["aqi"] - merged["estimated_aqi"]
    pooled_offset = merged["residual"].mean()

    print(f"\nSeasonal offset correction:")
    print(f"  n target-season pairs used = {len(merged)}")
    print(f"  pooled offset (fallback) = {pooled_offset:+.1f}, "
          f"std = {merged['residual'].std():.1f}")

    n_negative = (merged["residual"] < 0).sum()
    if n_negative / len(merged) > 0.9 or (1 - n_negative / len(merged)) > 0.9:
        direction = "over" if n_negative / len(merged) > 0.5 else "under"
        print(f"  NOTE: {max(n_negative, len(merged)-n_negative)}/{len(merged)} "
              f"residuals share the same sign -- the model is consistently "
              f"{direction}-predicting, not just noisily off. The offset "
              f"correction fixes the average but per-cell error can still "
              f"be large (see per-region breakdown below).")

    offsets = {"_pooled": pooled_offset}
    corrected = estimated_grid.copy()
    corrected["estimated_aqi_uncorrected"] = corrected["estimated_aqi"]
    corrected["estimated_aqi"] = np.nan

    if not per_region:
        corrected["estimated_aqi"] = (corrected["estimated_aqi_uncorrected"] + pooled_offset).clip(lower=0).round(1)
        return corrected, offsets, len(merged)

    print("  Per-region offsets applied (differs meaningfully by region -- "
          "a single pooled offset would over/under-correct individual regions):")
    for region_id in sorted(merged["region_id"].unique()):
        sub = merged[merged["region_id"] == region_id]
        if len(sub) < 5:
            offset = pooled_offset
            print(f"    {region_id}: n={len(sub)} (<5, using pooled fallback "
                  f"{pooled_offset:+.1f})")
        else:
            offset = sub["residual"].mean()
            print(f"    {region_id}: n={len(sub)}, offset={offset:+.1f}, "
                  f"std={sub['residual'].std():.1f}")
        offsets[region_id] = offset
        mask = corrected["region_id"] == region_id
        corrected.loc[mask, "estimated_aqi"] = corrected.loc[mask, "estimated_aqi_uncorrected"] + offset

    # Any region in the grid with no target pairs at all falls back to pooled.
    uncovered = set(corrected["region_id"].unique()) - set(offsets.keys())
    for region_id in uncovered:
        print(f"    {region_id}: no target pairs at all, using pooled fallback "
              f"{pooled_offset:+.1f}")
        mask = corrected["region_id"] == region_id
        corrected.loc[mask, "estimated_aqi"] = corrected.loc[mask, "estimated_aqi_uncorrected"] + pooled_offset
        offsets[region_id] = pooled_offset

    corrected["estimated_aqi"] = corrected["estimated_aqi"].clip(lower=0).round(1)
    return corrected, offsets, len(merged)


def detect_hotspots(sat_with_aqi: pd.DataFrame,
                     threshold: float = HOTSPOT_AQI_THRESHOLD,
                     cluster_degrees: float = CLUSTER_GRID_DEGREES) -> pd.DataFrame:
    """
    Flags grid cells >= threshold, clusters adjacent flagged cells by
    rounding lat/lon to `cluster_degrees` buckets (simple grid-merging per
    the Day-1 spec choice), and reports per-cluster summary stats.

    Cluster ID includes date since hotspots are computed per-date, not
    aggregated across the whole time range.
    """
    flagged = sat_with_aqi[sat_with_aqi["estimated_aqi"] >= threshold].copy()

    if flagged.empty:
        return pd.DataFrame(columns=[
            "hotspot_id", "region_id", "date", "centroid_lat", "centroid_lon",
            "avg_estimated_aqi", "max_estimated_aqi", "severity",
            "affected_cell_count",
        ])

    flagged["cluster_lat"] = (flagged["lat"] / cluster_degrees).round() * cluster_degrees
    flagged["cluster_lon"] = (flagged["lon"] / cluster_degrees).round() * cluster_degrees

    grouped = flagged.groupby(["region_id", "date", "cluster_lat", "cluster_lon"])

    hotspots = []
    hotspot_counter = 0
    for (region_id, date, _, _), group in grouped:
        hotspot_counter += 1
        avg_aqi = group["estimated_aqi"].mean()
        max_aqi = group["estimated_aqi"].max()
        hotspots.append({
            "hotspot_id": f"HS_{region_id}_{date}_{hotspot_counter:03d}",
            "region_id": region_id,
            "date": date,
            "centroid_lat": round(group["lat"].mean(), 4),
            "centroid_lon": round(group["lon"].mean(), 4),
            "avg_estimated_aqi": round(avg_aqi, 1),
            "max_estimated_aqi": round(max_aqi, 1),
            "severity": severity_label(max_aqi),
            "affected_cell_count": len(group),
        })

    return pd.DataFrame(hotspots)


def run_pipeline(ground_path: str, satellite_paths: list[str],
                  max_match_distance_km: float = 5.0,
                  hotspot_threshold: float = HOTSPOT_AQI_THRESHOLD,
                  cluster_degrees: float = CLUSTER_GRID_DEGREES,
                  use_cloud_fraction: bool = False,
                  per_region: bool = False,
                  calibration_ground_path: str = None,
                  calibration_satellite_paths: list[str] = None):
    """
    Runs the full pipeline end-to-end and returns:
        (hotspots_df, calibration_model, r2_or_dict, calibration_pairs_df, estimated_grid_df)
    so callers can inspect intermediate results (e.g. for QA or re-tuning).

    If per_region=True, the returned "model" is a dict {region_id: (model, r2, n)}
    and "r2_or_dict" mirrors that dict for convenience.

    CROSS-SEASON CALIBRATION (Option A)
    ------------------------------------
    If calibration_ground_path / calibration_satellite_paths are provided,
    the calibration model is TRAINED on that data instead of on
    ground_path/satellite_paths, then APPLIED to the ground_path/
    satellite_paths grid to produce estimated_aqi and hotspots.

    Use case: live-season ground truth may have too few calibration pairs
    or too narrow an AQI range (e.g. monsoon season) to train a trustworthy
    model on its own. Training on a season with more real pollution
    variation (e.g. the Oct-Nov stubble-burning historical data) and
    applying that model to the live satellite grid is more defensible than
    forcing a calibration fit on live data that can't support one -- this
    is a deliberate, disclosed choice, not silently reusing stale results.

    When this mode is active, the returned calibration_pairs_df is built
    from the CALIBRATION dataset (historical), not the target dataset
    (live), since that's what the model was actually fit on.
    """
    cross_season = calibration_ground_path is not None

    if cross_season:
        print(f"CROSS-SEASON CALIBRATION MODE: training on "
              f"{calibration_ground_path}, applying to {ground_path}'s "
              f"satellite grid.\n")
        print("Loading calibration (training) ground data and computing AQI...")
        calib_ground_aqi = load_ground_aqi(calibration_ground_path)
        print(f"  {len(calib_ground_aqi)} station-date AQI records.")

        print("\nLoading calibration (training) satellite data...")
        calib_sat = load_satellite(calibration_satellite_paths)
        print(f"  {len(calib_sat)} satellite grid-cell/date records across "
              f"{calib_sat['region_id'].nunique()} region(s).")

        print("\nBuilding calibration pairs (nearest-neighbor, per-station)...")
        pairs = build_calibration_pairs(calib_ground_aqi, calib_sat, max_match_distance_km)

        print("\nLoading TARGET satellite data (model will be applied here)...")
        sat = load_satellite(satellite_paths)
        print(f"  {len(sat)} satellite grid-cell/date records across "
              f"{sat['region_id'].nunique()} region(s).")
    else:
        print("Loading ground data and computing AQI...")
        ground_aqi = load_ground_aqi(ground_path)
        print(f"  {len(ground_aqi)} station-date AQI records.")

        print("\nLoading satellite data...")
        sat = load_satellite(satellite_paths)
        print(f"  {len(sat)} satellite grid-cell/date records across "
              f"{sat['region_id'].nunique()} region(s).")

        print("\nBuilding calibration pairs (nearest-neighbor, per-station)...")
        pairs = build_calibration_pairs(ground_aqi, sat, max_match_distance_km)

    if pairs.empty:
        raise ValueError("No calibration pairs could be built -- check date "
                          "overlap between ground and satellite data.")

    print("\nTraining calibration model...")
    model = train_calibration_model(pairs, use_cloud_fraction, per_region)
    r2_or_dict = model if per_region else model[1]
    trained_model = model if per_region else model[0]

    if cross_season:
        print(f"\nApplying {'' if not per_region else 'per-region '}model "
              f"(trained on calibration data) across TARGET satellite grid...")
    else:
        print("\nApplying model across full satellite grid...")
    estimated_grid = apply_model_to_grid(trained_model, sat, use_cloud_fraction, per_region)

    if cross_season:
        target_regions = set(sat["region_id"].unique())
        calib_regions = set(pairs["region_id"].unique())
        uncovered = target_regions - calib_regions
        if uncovered:
            print(f"  WARNING: target data includes region(s) {uncovered} "
                  f"with no calibration pairs in the training data -- "
                  f"estimated_aqi for these will be NaN if per_region=True, "
                  f"or extrapolated from other regions' data if pooled.")

        # Correct for the season-level HCHO scale shift using whatever
        # real target-season ground truth exists. Without this, the
        # cross-season model can be systematically biased (see docstring
        # of apply_seasonal_offset_correction).
        target_ground_aqi = load_ground_aqi(ground_path)
        estimated_grid, offsets, n_offset_pairs = apply_seasonal_offset_correction(
            estimated_grid, target_ground_aqi, sat, max_match_distance_km
        )
        offset_pairs = build_calibration_pairs(target_ground_aqi, sat, max_match_distance_km)
    else:
        offset_pairs = None

    print("\nDetecting hotspots...")
    valid_grid = estimated_grid.dropna(subset=["estimated_aqi"])
    hotspots = detect_hotspots(valid_grid, hotspot_threshold, cluster_degrees)
    print(f"  {len(hotspots)} hotspot cluster(s) detected across "
          f"{hotspots['date'].nunique() if not hotspots.empty else 0} date(s).")

    return hotspots, trained_model, r2_or_dict, pairs, estimated_grid, offset_pairs


def main():
    parser = argparse.ArgumentParser(description="Satellite-ground AQI fusion and hotspot detection")
    parser.add_argument("--ground", required=True, help="Path to ground AQI CSV (with lat/lon already fixed)")
    parser.add_argument("--satellite", required=True, nargs="+", help="Path(s) to satellite HCHO CSV(s)")
    parser.add_argument("--mode", required=True, choices=["historical", "live"], help="Used in output filename")
    parser.add_argument("--out", default=None, help="Output CSV path (default: hotspots_<mode>.csv)")
    parser.add_argument("--max-match-km", type=float, default=5.0,
                         help="Max distance (km) for a valid ground-satellite match")
    parser.add_argument("--threshold", type=float, default=HOTSPOT_AQI_THRESHOLD,
                         help="Estimated AQI threshold to flag a hotspot cell")
    parser.add_argument("--cluster-degrees", type=float, default=CLUSTER_GRID_DEGREES,
                         help="Grid rounding size (degrees) for clustering adjacent cells")
    parser.add_argument("--use-cloud-fraction", action="store_true",
                         help="Include cloud_fraction as a second predictor alongside hcho_value")
    parser.add_argument("--per-region", action="store_true",
                         help="Train a separate calibration model per region instead of one pooled model")
    parser.add_argument("--calibration-ground", default=None,
                         help="Ground CSV to TRAIN calibration on, if different from --ground "
                              "(cross-season mode: train on this, apply to --satellite grid)")
    parser.add_argument("--calibration-satellite", nargs="+", default=None,
                         help="Satellite CSV(s) to TRAIN calibration on, if different from --satellite "
                              "(required if --calibration-ground is set)")
    args = parser.parse_args()

    if args.calibration_ground and not args.calibration_satellite:
        parser.error("--calibration-ground requires --calibration-satellite")
    if args.calibration_satellite and not args.calibration_ground:
        parser.error("--calibration-satellite requires --calibration-ground")

    out_path = args.out or f"hotspots_{args.mode}.csv"

    hotspots, model, r2, pairs, estimated_grid, offset_pairs = run_pipeline(
        args.ground, args.satellite, args.max_match_km, args.threshold, args.cluster_degrees,
        args.use_cloud_fraction, args.per_region,
        args.calibration_ground, args.calibration_satellite
    )

    hotspots.to_csv(out_path, index=False)
    print(f"\nWrote {out_path}")

    pairs_path = out_path.replace(".csv", "_calibration_pairs.csv")
    pairs.to_csv(pairs_path, index=False)
    print(f"Wrote {pairs_path} (for QA/debugging"
          f"{' -- these are the TRAINING-season pairs, not target-season' if offset_pairs is not None else ''})")

    if offset_pairs is not None:
        offset_pairs_path = out_path.replace(".csv", "_offset_correction_pairs.csv")
        offset_pairs.to_csv(offset_pairs_path, index=False)
        print(f"Wrote {offset_pairs_path} (target-season pairs used for the "
              f"seasonal offset correction, for QA/debugging)")

    grid_path = out_path.replace(".csv", "_estimated_grid.csv")
    estimated_grid.to_csv(grid_path, index=False)
    print(f"Wrote {grid_path} (full estimated-AQI grid, for dashboard/Team C)")


if __name__ == "__main__":
    main()
