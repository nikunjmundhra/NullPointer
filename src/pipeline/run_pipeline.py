"""
run_pipeline.py

The integration glue: takes Team A's raw drops (data/raw/**), runs Team B's
existing analysis code (src/team_b_analysis/04_hotspot_detection.py)
end-to-end for both the historical and live windows, and writes everything
into the shared data/processed/** layout described in docs/DATA_SCHEMA.md.
Then hands off to export_for_dashboard.py for Team C's bundle.

This script does not reimplement any analysis logic -- it only wires
together file paths, run modes, and output locations so the three teams'
work composes without anyone having to remember CLI flags or copy files
around by hand.

USAGE
-----
    python3 src/pipeline/run_pipeline.py            # runs both modes
    python3 src/pipeline/run_pipeline.py --mode historical
    python3 src/pipeline/run_pipeline.py --mode live
    python3 src/pipeline/run_pipeline.py --skip-dashboard-export
"""

from __future__ import annotations
import argparse
import importlib.util
import json
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "team_b_analysis"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import config  # noqa: E402


def _load_team_b_module():
    """
    04_hotspot_detection.py can't be `import`-ed by its literal filename
    (Python module names can't start with a digit), and Team B's file isn't
    being renamed just for our convenience -- so load it by path instead.
    """
    path = config.TEAM_B_SRC_DIR / "04_hotspot_detection.py"
    spec = importlib.util.spec_from_file_location("team_b_hotspot_detection", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run_historical(hotspot_mod, log: list[str]) -> dict:
    log.append("=== HISTORICAL MODE ===")
    satellite_paths = [str(p) for p in config.satellite_paths("historical")]

    hotspots, model, r2, pairs, grid, offset_pairs = hotspot_mod.run_pipeline(
        ground_path=str(config.GROUND_HISTORICAL),
        satellite_paths=satellite_paths,
        max_match_distance_km=config.MAX_MATCH_DISTANCE_KM,
        hotspot_threshold=config.HOTSPOT_AQI_THRESHOLD,
        cluster_degrees=config.CLUSTER_DEGREES,
        use_cloud_fraction=config.USE_CLOUD_FRACTION,
        per_region=config.PER_REGION_MODEL,
    )

    out_dir = config.PROCESSED_HISTORICAL_DIR
    hotspots.to_csv(out_dir / "hotspots_historical.csv", index=False)
    pairs.to_csv(out_dir / "hotspots_historical_calibration_pairs.csv", index=False)
    grid.to_csv(out_dir / "hotspots_historical_estimated_grid.csv", index=False)

    log.append(f"  hotspots: {len(hotspots)} rows -> {out_dir/'hotspots_historical.csv'}")
    log.append(f"  calibration pairs: {len(pairs)} rows, CV R^2 = {r2:.4f}")
    log.append(f"  estimated grid: {len(grid)} rows")

    return {
        "cross_season": False,
        "cv_r2": round(float(r2), 4),
        "n_calibration_pairs": int(len(pairs)),
        "n_offset_correction_pairs": 0,
        "n_hotspots": int(len(hotspots)),
        "n_grid_cells": int(len(grid)),
    }


def run_live(hotspot_mod, log: list[str]) -> dict:
    """
    Live mode uses cross-season calibration (per Team B's documented
    Option A in 04_hotspot_detection.py): the regression is trained on the
    historical (stubble-season) ground+satellite data, then applied to the
    live satellite grid, then corrected for the HCHO seasonal offset using
    whatever live ground truth exists. This mirrors the CLI usage already
    documented in that script's module docstring -- the glue layer just
    calls the same programmatic entry point instead of shelling out.
    """
    log.append("=== LIVE MODE (cross-season calibration) ===")
    satellite_paths = [str(p) for p in config.satellite_paths("live")]
    calibration_satellite_paths = [str(p) for p in config.satellite_paths("historical")]

    hotspots, model, r2, pairs, grid, offset_pairs = hotspot_mod.run_pipeline(
        ground_path=str(config.GROUND_LIVE),
        satellite_paths=satellite_paths,
        max_match_distance_km=config.MAX_MATCH_DISTANCE_KM,
        hotspot_threshold=config.HOTSPOT_AQI_THRESHOLD,
        cluster_degrees=config.CLUSTER_DEGREES,
        use_cloud_fraction=config.USE_CLOUD_FRACTION,
        per_region=config.PER_REGION_MODEL,
        calibration_ground_path=str(config.GROUND_HISTORICAL),
        calibration_satellite_paths=calibration_satellite_paths,
    )

    out_dir = config.PROCESSED_LIVE_DIR
    hotspots.to_csv(out_dir / "hotspots_live.csv", index=False)
    pairs.to_csv(out_dir / "hotspots_live_calibration_pairs.csv", index=False)
    grid.to_csv(out_dir / "hotspots_live_estimated_grid.csv", index=False)
    n_offset_pairs = 0
    if offset_pairs is not None:
        offset_pairs.to_csv(out_dir / "hotspots_live_offset_correction_pairs.csv", index=False)
        n_offset_pairs = len(offset_pairs)

    log.append(f"  hotspots: {len(hotspots)} rows -> {out_dir/'hotspots_live.csv'}")
    log.append(f"  calibration pairs (training/historical season): {len(pairs)} rows, CV R^2 = {r2:.4f}")
    log.append(f"  offset-correction pairs (live season): {n_offset_pairs} rows")
    log.append(f"  estimated grid: {len(grid)} rows")

    return {
        "cross_season": True,
        "trained_on": "historical",
        "cv_r2": round(float(r2), 4),
        "n_calibration_pairs": int(len(pairs)),
        "n_offset_correction_pairs": int(n_offset_pairs),
        "n_hotspots": int(len(hotspots)),
        "n_grid_cells": int(len(grid)),
    }


def main():
    parser = argparse.ArgumentParser(description="End-to-end AQI/HCHO pipeline glue")
    parser.add_argument("--mode", choices=["historical", "live", "both"], default="both")
    parser.add_argument("--skip-dashboard-export", action="store_true",
                         help="Run analysis only; don't build data/dashboard_ready/ for Team C")
    args = parser.parse_args()

    config.PROCESSED_HISTORICAL_DIR.mkdir(parents=True, exist_ok=True)
    config.PROCESSED_LIVE_DIR.mkdir(parents=True, exist_ok=True)
    config.DASHBOARD_READY_DIR.mkdir(parents=True, exist_ok=True)
    config.LOG_DIR.mkdir(parents=True, exist_ok=True)

    log: list[str] = [f"Pipeline run started {datetime.now(timezone.utc).isoformat()}"]
    run_meta = {}
    failures = []

    hotspot_mod = _load_team_b_module()

    if args.mode in ("historical", "both"):
        try:
            run_meta["historical"] = run_historical(hotspot_mod, log)
        except Exception as e:
            failures.append(("historical", e))
            log.append(f"  FAILED: {e}")
            log.append(traceback.format_exc())

    if args.mode in ("live", "both"):
        try:
            run_meta["live"] = run_live(hotspot_mod, log)
        except Exception as e:
            failures.append(("live", e))
            log.append(f"  FAILED: {e}")
            log.append(traceback.format_exc())

    log_path = config.LOG_DIR / f"run_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.log"
    log_path.write_text("\n".join(log))
    print("\n".join(log))
    print(f"\nFull log written to {log_path}")

    run_meta_path = config.PROCESSED_DIR / "last_run_meta.json"
    run_meta_path.write_text(json.dumps(run_meta, indent=2))

    if failures:
        print(f"\n{len(failures)} mode(s) FAILED: {[m for m, _ in failures]}")
        sys.exit(1)

    if not args.skip_dashboard_export:
        import export_for_dashboard
        export_for_dashboard.export_all(run_meta)

    print("\nPipeline complete.")


if __name__ == "__main__":
    main()
