"""
config.py

Single source of truth for paths and run parameters used by the glue
pipeline. Team A drops raw files into data/raw/**, Team B's analysis
scripts (src/team_b_analysis/) turn those into data/processed/**, and
export_for_dashboard.py turns data/processed/** into data/dashboard_ready/**
for Team C. Nothing downstream should hardcode a path -- import it from here.
"""

from __future__ import annotations
from pathlib import Path

# ---------------------------------------------------------------------------
# Repo layout
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parents[2]

RAW_GROUND_DIR = REPO_ROOT / "data" / "raw" / "ground"
RAW_SATELLITE_DIR = REPO_ROOT / "data" / "raw" / "satellite"

PROCESSED_DIR = REPO_ROOT / "data" / "processed"
PROCESSED_HISTORICAL_DIR = PROCESSED_DIR / "historical"
PROCESSED_LIVE_DIR = PROCESSED_DIR / "live"

DASHBOARD_READY_DIR = REPO_ROOT / "data" / "dashboard_ready"
LOG_DIR = REPO_ROOT / "logs"

TEAM_B_SRC_DIR = REPO_ROOT / "src" / "team_b_analysis"

# ---------------------------------------------------------------------------
# Regions (must match `region_id` values used consistently by Team A across
# ground_station_coordinates.csv, satellite_hcho_*.csv, and downstream by
# Team B's hotspot output). Adding a region = add one entry here + drop the
# matching raw files using the naming convention below.
# ---------------------------------------------------------------------------
REGIONS = ["delhi_ncr", "kanpur_industrial", "punjab_stubble_belt"]

# ---------------------------------------------------------------------------
# Raw file naming convention (what Team A is expected to hand off)
# ---------------------------------------------------------------------------
GROUND_HISTORICAL = RAW_GROUND_DIR / "ground_aqi_data1_historical_fixed.csv"
GROUND_LIVE = RAW_GROUND_DIR / "ground_aqi_data1_live_fixed.csv"
GROUND_STATION_COORDS = RAW_GROUND_DIR / "ground_station_coordinates.csv"


def satellite_paths(mode: str) -> list[Path]:
    """
    mode: "historical" or "live".
    Returns the expected satellite CSV path per region for that mode,
    following Team A's naming convention. Raises if a region's file is
    missing rather than silently skipping a region.
    """
    if mode == "historical":
        suffix = "oct15_to_nov15_2025-10-15_to_2025-11-15"
    elif mode == "live":
        suffix = "live"
    else:
        raise ValueError(f"Unknown mode {mode!r}; expected 'historical' or 'live'.")

    paths = []
    missing = []
    for region in REGIONS:
        candidate = RAW_SATELLITE_DIR / f"satellite_hcho_{region}_{suffix}.csv"
        if candidate.exists():
            paths.append(candidate)
        else:
            missing.append(candidate.name)

    if missing:
        raise FileNotFoundError(
            f"Missing satellite file(s) for mode={mode!r}: {missing}. "
            f"Check Team A's data drop in {RAW_SATELLITE_DIR}."
        )
    return paths


# ---------------------------------------------------------------------------
# Model / detection parameters (kept here, not sprinkled across scripts, so
# Team B can tune once and both modes + the dashboard stay in sync)
# ---------------------------------------------------------------------------
MAX_MATCH_DISTANCE_KM = 5.0
HOTSPOT_AQI_THRESHOLD = 200.0
CLUSTER_DEGREES = 0.3
USE_CLOUD_FRACTION = True
PER_REGION_MODEL = False  # pooled model across regions; flip True to try per-region

# Schema/version tag stamped into dashboard-ready outputs so Team C can tell
# which pipeline run + schema shape produced a given file.
SCHEMA_VERSION = "1.0.0"
