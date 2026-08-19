"""
export_for_dashboard.py

Converts Team B's processed CSVs (data/processed/**) into the small,
self-describing bundle Team C's dashboard consumes (data/dashboard_ready/**):
    hotspots_{mode}.geojson   -- one Point feature per hotspot cluster
    grid_{mode}.geojson       -- one Point feature per satellite grid cell/date
    summary_{mode}.json       -- run stats for a dashboard header/status panel
    manifest.json             -- combined index Team C fetches once on load

See docs/DATA_SCHEMA.md section 3 for the exact shape of each file. Team C
should treat this directory as the ONLY thing it reads from -- never the
raw team_b_analysis CSVs directly -- so Team B can change internal CSV
layout without breaking the dashboard as long as this export stays in sync.

Can be run standalone (re-export without re-running the analysis):
    python3 src/pipeline/export_for_dashboard.py
"""

from __future__ import annotations
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import config  # noqa: E402


def _clean(v):
    """JSON can't represent NaN; convert to None so the file stays valid JSON."""
    if isinstance(v, float) and math.isnan(v):
        return None
    return v


def hotspots_to_geojson(hotspots_csv: Path, mode: str) -> dict:
    df = pd.read_csv(hotspots_csv)
    features = []
    for _, row in df.iterrows():
        props = {k: _clean(v) for k, v in row.items()
                  if k not in ("centroid_lat", "centroid_lon")}
        props["mode"] = mode
        features.append({
            "type": "Feature",
            "geometry": {
                "type": "Point",
                "coordinates": [row["centroid_lon"], row["centroid_lat"]],
            },
            "properties": props,
        })
    return {"type": "FeatureCollection", "features": features}


def grid_to_geojson(grid_csv: Path, mode: str) -> dict:
    df = pd.read_csv(grid_csv)
    features = []
    for _, row in df.iterrows():
        props = {k: _clean(v) for k, v in row.items() if k not in ("lat", "lon")}
        props["mode"] = mode
        features.append({
            "type": "Feature",
            "geometry": {
                "type": "Point",
                "coordinates": [row["lon"], row["lat"]],
            },
            "properties": props,
        })
    return {"type": "FeatureCollection", "features": features}


def build_summary(mode: str, hotspots_csv: Path, grid_csv: Path,
                   calibration_meta: dict) -> dict:
    hotspots = pd.read_csv(hotspots_csv)
    grid = pd.read_csv(grid_csv)

    date_range = [None, None]
    if not grid.empty:
        date_range = [str(grid["date"].min()), str(grid["date"].max())]

    severity_counts = {}
    if not hotspots.empty:
        severity_counts = hotspots["severity"].value_counts().to_dict()

    return {
        "schema_version": config.SCHEMA_VERSION,
        "mode": mode,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "regions": sorted(grid["region_id"].unique().tolist()) if not grid.empty else [],
        "date_range": date_range,
        "n_hotspots": int(len(hotspots)),
        "n_grid_cells": int(len(grid)),
        "calibration": calibration_meta,
        "severity_counts": {str(k): int(v) for k, v in severity_counts.items()},
    }


def export_mode(mode: str, run_meta: dict) -> dict:
    processed_dir = config.PROCESSED_HISTORICAL_DIR if mode == "historical" else config.PROCESSED_LIVE_DIR
    hotspots_csv = processed_dir / f"hotspots_{mode}.csv"
    grid_csv = processed_dir / f"hotspots_{mode}_estimated_grid.csv"

    if not hotspots_csv.exists() or not grid_csv.exists():
        raise FileNotFoundError(
            f"Missing processed output for mode={mode!r} in {processed_dir}. "
            f"Run run_pipeline.py first."
        )

    hs_geojson = hotspots_to_geojson(hotspots_csv, mode)
    grid_geojson = grid_to_geojson(grid_csv, mode)

    calibration_meta = run_meta.get(mode, {"note": "calibration meta unavailable "
                                                      "(export run standalone, not via run_pipeline.py)"})
    summary = build_summary(mode, hotspots_csv, grid_csv, calibration_meta)

    (config.DASHBOARD_READY_DIR / f"hotspots_{mode}.geojson").write_text(json.dumps(hs_geojson))
    (config.DASHBOARD_READY_DIR / f"grid_{mode}.geojson").write_text(json.dumps(grid_geojson))
    (config.DASHBOARD_READY_DIR / f"summary_{mode}.json").write_text(json.dumps(summary, indent=2))

    print(f"[export] {mode}: {len(hs_geojson['features'])} hotspot features, "
          f"{len(grid_geojson['features'])} grid features -> {config.DASHBOARD_READY_DIR}")

    return summary


def export_all(run_meta: dict | None = None):
    run_meta = run_meta or {}
    config.DASHBOARD_READY_DIR.mkdir(parents=True, exist_ok=True)

    summaries = {}
    for mode in ("historical", "live"):
        try:
            summaries[mode] = export_mode(mode, run_meta)
        except FileNotFoundError as e:
            print(f"[export] SKIPPED {mode}: {e}")

    manifest = {
        "schema_version": config.SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "regions": config.REGIONS,
        "modes": list(summaries.keys()),
        "files": {
            mode: {
                "hotspots_geojson": f"hotspots_{mode}.geojson",
                "grid_geojson": f"grid_{mode}.geojson",
                "summary_json": f"summary_{mode}.json",
            }
            for mode in summaries
        },
        "summaries": summaries,
    }
    (config.DASHBOARD_READY_DIR / "manifest.json").write_text(json.dumps(manifest, indent=2))
    print(f"[export] manifest.json written -> {config.DASHBOARD_READY_DIR / 'manifest.json'}")


if __name__ == "__main__":
    export_all()
