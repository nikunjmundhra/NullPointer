# Shared Data Schema

This is the contract between Team A (data collection), Team B (AQI + HCHO
fusion / hotspot analysis), and Team C (dashboard). If a column name, unit,
or file-naming pattern needs to change, change it here first and notify the
other two teams — every script in `src/` reads paths and column names off
this contract, not off tribal knowledge.

`region_id` is the join key across every file in this project. Current
values: `delhi_ncr`, `kanpur_industrial`, `punjab_stubble_belt` (see
`src/pipeline/config.py::REGIONS`).

---

## 1. Team A → Team B handoff (`data/raw/`)

### 1.1 Ground station coordinates
`data/raw/ground/ground_station_coordinates.csv`

| column | type | notes |
|---|---|---|
| state | string | Indian state the station sits in |
| ground_station | string | **exact** match to `station_name` in the ground AQI files — this is the join key |
| latitude | float | degrees |
| longitude | float | degrees |

### 1.2 Ground AQI readings (long format)
`data/raw/ground/ground_aqi_data1_{historical,live}_fixed.csv`

| column | type | notes |
|---|---|---|
| region_id | string | one of `REGIONS` |
| station_id | int | CPCB/OpenAQ station id |
| station_name | string | must match `ground_station` in 1.1 exactly |
| parameter | string | one of `pm25, pm10, no2, so2, co, o3` |
| date | date (YYYY-MM-DD) | |
| value | float | raw reading |
| unit | string | `µg/m³` for pm25/pm10/o3, `ppb` for no2/so2/co — **enforced**, `aqi_calculator.py` raises if a parameter shows up in the wrong unit rather than silently converting |
| lat, lon | float | must be populated (run `fix_station_coords.py` first if not — do not fall back to region-center coordinates) |
| source | string | e.g. `OpenAQ` |

**File suffix convention:** `_fixed` marks a file that has already been
through `fix_station_coords.py` and has real per-station lat/lon. The
pipeline refuses to run on ground files with null lat/lon.

### 1.3 Satellite HCHO grid
`data/raw/satellite/satellite_hcho_{region_id}_{suffix}.csv`

Suffix is `oct15_to_nov15_2025-10-15_to_2025-11-15` for the historical
window and `live` for the live feed — see `config.satellite_paths()`.

| column | type | notes |
|---|---|---|
| region_id | string | |
| date | date | |
| lat, lon | float | satellite grid cell center, ~5.5km resolution |
| hcho_value | float | column HCHO, mol/m² (unscaled — do not confuse with ppb ground units) |
| cloud_fraction | float | 0–1 |

---

## 2. Team B analysis outputs (`data/processed/{historical,live}/`)

Produced by `src/team_b_analysis/04_hotspot_detection.py` via
`src/pipeline/run_pipeline.py`.

### 2.1 Calibration pairs — `hotspots_{mode}_calibration_pairs.csv`
Ground-truth (AQI) ↔ satellite (HCHO) pairs used to fit the regression.
One row per matched station-date.

| column | notes |
|---|---|
| region_id, station_id, station_name, date | |
| aqi | CPCB AQI computed from ground pollutants |
| hcho_value, cloud_fraction | from nearest satellite cell |
| match_distance_km | haversine distance to the matched cell; pairs >5km are already dropped upstream |

### 2.2 Offset-correction pairs — `hotspots_live_offset_correction_pairs.csv`
Only produced in **cross-season mode** (live mode trains on historical data,
then corrects for the HCHO seasonal scale shift using live ground truth).
Same columns as 2.1, but drawn from the live season.

### 2.3 Estimated grid — `hotspots_{mode}_estimated_grid.csv`
Full satellite grid with modeled AQI. **This is the primary input to the
dashboard** — it's what gets turned into map layers.

| column | notes |
|---|---|
| region_id, date, lat, lon | |
| hcho_value, cloud_fraction | passthrough from raw satellite |
| estimated_aqi | model output, clipped at 0, CPCB-scale |
| estimated_aqi_uncorrected | **live mode only** — pre-offset-correction value, kept for QA/transparency |

### 2.4 Hotspots — `hotspots_{mode}.csv`
Clustered cells where `estimated_aqi >= 200`. One row per cluster per date.

| column | notes |
|---|---|
| hotspot_id | `HS_{region_id}_{date}_{seq}` |
| region_id, date | |
| centroid_lat, centroid_lon | mean of clustered cells |
| avg_estimated_aqi, max_estimated_aqi | |
| severity | CPCB category label (`Poor`, `Very Poor`, `Severe`, ...) of the max cell |
| affected_cell_count | |

---

## 3. Team B → Team C handoff (`data/dashboard_ready/`)

Produced by `src/pipeline/export_for_dashboard.py`. This layer exists so
Team C never has to parse Team B's raw CSV conventions or re-derive
severity/units — it consumes one small, versioned, self-describing bundle.

### 3.1 `hotspots_{mode}.geojson`
Standard GeoJSON `FeatureCollection`. One `Point` feature per hotspot
cluster (`geometry.coordinates = [lon, lat]`), with all of section 2.4's
columns copied into `properties`, plus `mode` (`"historical"`/`"live"`).
Any GIS-aware front end (Leaflet, Mapbox GL, deck.gl, kepler.gl) can load
this directly.

### 3.2 `grid_{mode}.geojson`
Same idea for the full estimated-AQI grid (section 2.3) — one `Point`
feature per grid cell/date, `properties.estimated_aqi` drives the choropleth
color. This file is larger; the dashboard should filter by `date` client-side
rather than requesting a re-export per date.

### 3.3 `summary_{mode}.json`
Small run-summary object for a dashboard header / status panel:

```json
{
  "schema_version": "1.0.0",
  "mode": "live",
  "generated_at": "2026-08-19T12:00:00Z",
  "regions": ["delhi_ncr", "kanpur_industrial", "punjab_stubble_belt"],
  "date_range": ["2026-07-17", "2026-08-03"],
  "n_hotspots": 9,
  "n_grid_cells": 3582,
  "calibration": {
    "cross_season": true,
    "trained_on": "historical",
    "cv_r2": 0.42,
    "n_calibration_pairs": 210,
    "n_offset_correction_pairs": 63
  },
  "severity_counts": {"Poor": 6, "Very Poor": 2, "Severe": 1}
}
```

### 3.4 `manifest.json`
One combined index (both modes) so Team C's dashboard has a single file to
fetch on load to discover what's available — see
`data/dashboard_ready/manifest.json` after a pipeline run.

---

## 4. Units cheat-sheet (don't re-derive this, just read it)

| quantity | unit | where |
|---|---|---|
| pm25, pm10, o3 (ground) | µg/m³ | raw ground CSV |
| no2, so2, co (ground) | ppb | raw ground CSV → converted to µg/m³ / mg/m³ internally by `aqi_calculator.py` before AQI sub-index lookup |
| AQI | CPCB 0–500 scale | everywhere downstream of `aqi_calculator.py` |
| hcho_value | mol/m² (satellite column HCHO) | raw + all downstream files, never converted |
| cloud_fraction | 0–1 | raw + downstream |
| lat/lon | decimal degrees, WGS84 | everywhere |
