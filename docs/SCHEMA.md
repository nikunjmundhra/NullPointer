# Data Schema — aqi-hcho-project

Locked down Day 1. If anything changes, update this file and tell everyone in the daily sync.

## Naming conventions
- **Region IDs** (exact strings, lowercase, underscores): `delhi_ncr`, `punjab_stubble_belt`, `kanpur_industrial`
- **Dates**: `YYYY-MM-DD` (e.g. `2026-08-16`)
- **File locations**: Team A's raw outputs → `data/raw/`. Team B's processed outputs → `data/processed/`.

## 1. Satellite CSV (Team A output → Team B input)
Path: `data/raw/satellite_hcho_<region>_<period>_<daterange>.csv`

| column | type | notes |
|---|---|---|
| region_id | string | one of the 3 region IDs above |
| date | string | YYYY-MM-DD |
| lat | float | grid cell center latitude |
| lon | float | grid cell center longitude |
| hcho_value | float | HCHO column value, mol/m² |
| cloud_fraction | float | 0–1, used for quality filtering (drop > 0.3) |

## 2. Ground AQI CSV (Team A output → Team B input)
Path: `data/raw/ground_aqi_<period>.csv`

| column | type | notes |
|---|---|---|
| region_id | string | one of the 3 region IDs above |
| station_id | string/int | unique station identifier |
| date | string | YYYY-MM-DD |
| lat | float | station latitude |
| lon | float | station longitude |
| pm25 | float | µg/m³ |
| pm10 | float | µg/m³ |
| no2 | float | µg/m³ |
| source | string | e.g. CPCB, DPCC, UPPCB, PPCB |

> ⚠️ **Known mismatch (flagged Day 1):** the raw files provided so far are in *long* format (one row per pollutant, with a `parameter`/`value` pair and extra pollutants: so2, o3, co) rather than this *wide* format. Confirm in the schema meeting whether Team A converts to this wide format, or this schema gets revised to match what Team A can actually deliver.

## 3. Hotspot output CSV/GeoJSON (Team B output → Team C input)
Path: `data/processed/hotspots.csv` and `data/processed/hotspots.geojson`

| column | type | notes |
|---|---|---|
| hotspot_id | string | unique per cluster, e.g. `HS_001` |
| region_id | string | one of the 3 region IDs above |
| date | string | YYYY-MM-DD |
| centroid_lat | float | mean latitude of all grid cells in this cluster |
| centroid_lon | float | mean longitude of all grid cells in this cluster |
| severity | string | `moderate` / `high` / `severe` |
| hcho_value | float | mean calibrated HCHO across the cluster |
| affected_cell_count | int | number of grid cells merged into this hotspot |

A "hotspot" = one or more adjacent flagged grid cells (within 1 grid-step, 0.05°) merged into a single cluster. This is *not* one row per raw grid cell.

## Branch strategy
One branch per person, named after them. Merge to `main` at daily sync. Integration lead (Member 2) reviews and merges.
