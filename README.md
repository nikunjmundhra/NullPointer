# Surface AQI & Formaldehyde Hotspot Detection — Delhi NCR / Kanpur / Punjab

Estimates surface AQI from satellite HCHO (formaldehyde) columns, calibrated
against ground CPCB stations, and flags formaldehyde/pollution hotspots
across three regions (Delhi NCR, Kanpur industrial belt, Punjab stubble-burning
belt), for two windows: a historical stubble-burning window
(2025-10-15 → 2025-11-15) and a live feed.

## How the three teams connect

```
Team A (data collection)          Team B (this repo's analysis)         Team C
─────────────────────────         ──────────────────────────────       ──────────
ground station readings   ─┐                                       ┌─▶ dashboard
ground station coords      ├─▶  data/raw/  ─▶  src/team_b_analysis/ ─┤   (map, charts,
satellite HCHO grids      ─┘         │              (AQI calc,        │    status panel)
                                     │            calibration,        │
                                     │           hotspot detection)   │
                                     ▼                                │
                            src/pipeline/run_pipeline.py              │
                                     │                                │
                                     ▼                                │
                          data/processed/{historical,live}/           │
                                     │                                │
                                     ▼                                │
                    src/pipeline/export_for_dashboard.py              │
                                     │                                │
                                     ▼                                │
                          data/dashboard_ready/  ──────────────────────┘
                    (GeoJSON + JSON — the ONLY thing Team C reads)
```

- **Team A** drops raw CSVs into `data/raw/ground/` and `data/raw/satellite/`
  following the naming convention in `docs/DATA_SCHEMA.md` §1. Nothing else
  needs to change on Team A's side to add a new date range — just match the
  filename pattern.
- **Team B** (this repo) owns `src/team_b_analysis/` (the actual AQI/HCHO
  fusion + hotspot logic — unmodified from what's already been built) and
  `src/pipeline/` (the glue that wires it together end-to-end, added here).
- **Team C** reads only `data/dashboard_ready/manifest.json` and the
  GeoJSON/JSON files it points to (`docs/DATA_SCHEMA.md` §3). It should never
  need to open a raw or processed CSV directly — that boundary is what lets
  Team B change internal analysis details without breaking the dashboard.

## Repo layout

```
data/
  raw/ground/          Team A: ground station readings + coordinates
  raw/satellite/        Team A: satellite HCHO grids (per region, per window)
  processed/historical/ Team B output: calibration pairs, estimated grid, hotspots
  processed/live/        Team B output (cross-season calibrated)
  dashboard_ready/       Team B → Team C handoff: GeoJSON + summary JSON + manifest
src/
  team_b_analysis/       aqi_calculator.py, fix_station_coords.py, 04_hotspot_detection.py
  pipeline/               config.py, run_pipeline.py, export_for_dashboard.py  (the glue)
docs/
  DATA_SCHEMA.md          the full column-by-column contract between all three teams
logs/                     one timestamped log per pipeline run
```

## Running the full pipeline

```bash
pip install -r requirements.txt

# One command, both modes, writes data/processed/** and data/dashboard_ready/**
python3 src/pipeline/run_pipeline.py

# Or one mode at a time:
python3 src/pipeline/run_pipeline.py --mode historical
python3 src/pipeline/run_pipeline.py --mode live

# Re-export the dashboard bundle without re-running the analysis
python3 src/pipeline/export_for_dashboard.py
```

### What happens on a run

1. **Historical mode**: loads `ground_aqi_data1_historical_fixed.csv` +
   the three historical satellite grids, computes CPCB AQI per station-date,
   builds nearest-neighbor (ground station ↔ satellite cell, ≤5km, same date)
   calibration pairs, fits `hcho_value + cloud_fraction → AQI` (linear
   regression, 5-fold CV reported), applies it to the full satellite grid,
   and clusters cells with `estimated_aqi ≥ 200` into hotspots (0.3° grid
   buckets).
2. **Live mode**: the live ground truth is monsoon-season and too narrow an
   AQI range to calibrate on its own, so — per the existing design in
   `04_hotspot_detection.py` — the model is *trained* on the historical
   window and *applied* to the live satellite grid, then corrected for the
   HCHO seasonal scale shift using whatever live ground truth exists
   (`estimated_aqi_uncorrected` is kept alongside the corrected value for
   QA). This mode was already documented and supported in the analysis
   script; the glue layer just calls it programmatically instead of via CLI
   flags, so it can't drift out of sync with the historical run.
3. **Dashboard export**: converts both modes' hotspot + grid CSVs into
   GeoJSON, writes a per-mode run summary, and a top-level `manifest.json`
   Team C fetches once to discover everything else.

### Current run results (this data)

| | historical | live |
|---|---|---|
| calibration pairs | 209 | 209 (trained on historical) + 62 (offset correction) |
| CV R² | 0.14 | 0.14 (pre-offset) |
| hotspot clusters | 548 | 8 |
| grid cells scored | 11,538 | 3,581 |

The CV R² is genuinely weak (~0.14) — this is flagged loudly by
`04_hotspot_detection.py` itself as a real physical/resolution ceiling
(ground stations a few km apart differ by ~50 AQI points, which HCHO alone
at ~5.5km satellite resolution can't resolve), not a bug to silently paper
over. Treat `estimated_aqi` as directional/hotspot-screening output, not a
precise per-cell AQI reading — this caveat is carried into
`summary_{mode}.json` for Team C to surface in the dashboard if useful.

## Adding a new region or date window

1. Team A drops the new `ground_aqi_data1_*` / `satellite_hcho_{region}_*`
   files into `data/raw/` following the naming convention.
2. Add the region to `REGIONS` in `src/pipeline/config.py` (one line).
3. Re-run `run_pipeline.py`. No other file needs to change.
