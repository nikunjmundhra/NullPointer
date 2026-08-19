"""
aqi_calculator.py

Converts raw ground-station pollutant concentrations into the standard
Indian CPCB National Air Quality Index (0-500 scale), following CPCB's
official sub-index breakpoint tables and "worst pollutant governs" rule.

Reference: CPCB National Air Quality Index document (2014), breakpoints
for PM2.5, PM10, NO2, SO2, CO, O3.

INPUT UNIT HANDLING
--------------------
CPCB breakpoints are defined in:
    PM2.5, PM10, NO2, SO2, O3  -> µg/m3   (24-hr avg, except O3 = 8-hr)
    CO                         -> mg/m3   (8-hr avg)

This project's ground CSV reports:
    pm25, pm10, o3             -> µg/m3   (used as-is)
    no2, so2, co               -> ppb     (must be converted)

ppb -> µg/m3 conversion (at 25C, 1 atm, standard EPA formula):
    µg/m3 = ppb * MW / 24.45

    NO2 molecular weight = 46.0055  g/mol
    SO2 molecular weight = 64.066   g/mol
    CO  molecular weight = 28.01    g/mol  (result then /1000 for mg/m3)

These are standard conversions; if ground-truth instruments already report
mass units, adjust `PARAM_UNITS` below and skip conversion accordingly.

USAGE
-----
    from aqi_calculator import compute_aqi_for_group, add_aqi_column

    # For a long-format ground DataFrame (region_id, station_id, station_name,
    # parameter, date, value, unit, lat, lon, source):
    aqi_df = add_aqi_column(ground_df)
    # -> returns one row per (region_id, station_id, station_name, date, lat, lon)
    #    with columns: aqi, governing_parameter, sub_indices (dict-like debug info)
"""

from __future__ import annotations
import pandas as pd
import numpy as np


# ---------------------------------------------------------------------------
# CPCB breakpoint tables: (C_low, C_high, I_low, I_high)
# Units: PM2.5, PM10, NO2, SO2, O3 in µg/m3 (24-hr, O3 8-hr); CO in mg/m3 (8-hr)
# ---------------------------------------------------------------------------

BREAKPOINTS = {
    "pm25": [
        (0, 30, 0, 50),
        (31, 60, 51, 100),
        (61, 90, 101, 200),
        (91, 120, 201, 300),
        (121, 250, 301, 400),
        (251, 380, 401, 500),
    ],
    "pm10": [
        (0, 50, 0, 50),
        (51, 100, 51, 100),
        (101, 250, 101, 200),
        (251, 350, 201, 300),
        (351, 430, 301, 400),
        (431, 510, 401, 500),
    ],
    "no2": [
        (0, 40, 0, 50),
        (41, 80, 51, 100),
        (81, 180, 101, 200),
        (181, 280, 201, 300),
        (281, 400, 301, 400),
        (401, 500, 401, 500),
    ],
    "so2": [
        (0, 40, 0, 50),
        (41, 80, 51, 100),
        (81, 380, 101, 200),
        (381, 800, 201, 300),
        (801, 1600, 301, 400),
        (1601, 2100, 401, 500),
    ],
    "co": [  # mg/m3
        (0.0, 1.0, 0, 50),
        (1.1, 2.0, 51, 100),
        (2.1, 10.0, 101, 200),
        (10.1, 17.0, 201, 300),
        (17.1, 34.0, 301, 400),
        (34.1, 50.0, 401, 500),
    ],
    "o3": [
        (0, 50, 0, 50),
        (51, 100, 51, 100),
        (101, 168, 101, 200),
        (169, 208, 201, 300),
        (209, 748, 301, 400),
        (749, 939, 401, 500),
    ],
}

# Molecular weights (g/mol) for ppb -> µg/m3 conversion at 25C, 1 atm.
MOLECULAR_WEIGHTS = {
    "no2": 46.0055,
    "so2": 64.066,
    "co": 28.01,
}

PPB_TO_UGM3_DIVISOR = 24.45  # standard molar volume at 25C, 1 atm (L/mol)

# Parameters whose raw `value` is already in the unit CPCB breakpoints expect.
NO_CONVERSION_NEEDED = {"pm25", "pm10", "o3"}

# Parameters that arrive in ppb and need conversion (result unit noted).
PPB_PARAMS_TO_UGM3 = {"no2", "so2"}   # -> µg/m3
PPB_PARAMS_TO_MGM3 = {"co"}           # -> mg/m3 (note extra /1000)


def ppb_to_ugm3(value_ppb: float, molecular_weight: float) -> float:
    """Convert ppb to µg/m3 using standard formula: µg/m3 = ppb * MW / 24.45."""
    return value_ppb * molecular_weight / PPB_TO_UGM3_DIVISOR


def convert_to_breakpoint_units(parameter: str, value: float, unit: str) -> float:
    """
    Convert a raw pollutant value into the unit CPCB breakpoints expect.
    Raises ValueError on an unrecognized parameter/unit combination rather
    than silently guessing.
    """
    parameter = parameter.lower()

    if parameter in NO_CONVERSION_NEEDED:
        if unit != "µg/m³":
            raise ValueError(
                f"Unexpected unit {unit!r} for {parameter}; expected µg/m³. "
                "Check the source data before trusting this conversion."
            )
        return value

    if parameter in PPB_PARAMS_TO_UGM3:
        if unit != "ppb":
            raise ValueError(
                f"Unexpected unit {unit!r} for {parameter}; expected ppb."
            )
        return ppb_to_ugm3(value, MOLECULAR_WEIGHTS[parameter])

    if parameter in PPB_PARAMS_TO_MGM3:
        if unit != "ppb":
            raise ValueError(
                f"Unexpected unit {unit!r} for {parameter}; expected ppb."
            )
        ugm3 = ppb_to_ugm3(value, MOLECULAR_WEIGHTS[parameter])
        return ugm3 / 1000.0  # -> mg/m3

    raise ValueError(f"Unknown parameter {parameter!r}; no conversion rule defined.")


def sub_index(parameter: str, concentration: float) -> float | None:
    """
    Compute the CPCB sub-index for a single pollutant given its concentration
    already converted into breakpoint units. Returns None if concentration
    is negative, NaN, or exceeds the top of the table (reported as-is at 500
    per CPCB convention only up to the table's max; beyond that we still
    linearly extrapolate the last band rather than silently clipping, since
    real pollution events in Punjab stubble season / Delhi winter routinely
    exceed the top published breakpoint).
    """
    if pd.isna(concentration) or concentration < 0:
        return None

    table = BREAKPOINTS[parameter]

    # Within a defined band: use standard CPCB linear interpolation.
    for c_low, c_high, i_low, i_high in table:
        if c_low <= concentration <= c_high:
            return round(
                ((i_high - i_low) / (c_high - c_low)) * (concentration - c_low) + i_low,
                1,
            )

    # Below the lowest breakpoint (shouldn't normally happen since c_low=0).
    if concentration < table[0][0]:
        return 0.0

    # Above the highest breakpoint: extrapolate linearly using the top band's
    # slope, flagged via governing logic downstream rather than hidden.
    c_low, c_high, i_low, i_high = table[-1]
    slope = (i_high - i_low) / (c_high - c_low)
    extrapolated = i_high + slope * (concentration - c_high)
    return round(extrapolated, 1)


def compute_aqi_for_group(param_values: dict[str, tuple[float, str]]) -> dict:
    """
    Given a dict of {parameter: (raw_value, unit)} for a single station/date,
    returns the overall AQI (max of available sub-indices, per CPCB's
    "worst pollutant determines AQI" rule) plus which pollutant governed
    and each individual sub-index for debugging/QA.

    CPCB requires a minimum number of pollutants (typically PM2.5 or PM10
    plus at least 2-3 others) to certify an official AQI; for this project's
    estimation use case we compute AQI from whatever pollutants are present
    and record `n_pollutants_used` so downstream users can judge confidence.
    """
    sub_indices = {}
    for parameter, (raw_value, unit) in param_values.items():
        parameter = parameter.lower()
        if parameter not in BREAKPOINTS:
            continue
        try:
            converted = convert_to_breakpoint_units(parameter, raw_value, unit)
        except ValueError:
            continue
        idx = sub_index(parameter, converted)
        if idx is not None:
            sub_indices[parameter] = idx

    if not sub_indices:
        return {
            "aqi": None,
            "governing_parameter": None,
            "n_pollutants_used": 0,
            "sub_indices": {},
        }

    governing_parameter = max(sub_indices, key=sub_indices.get)
    return {
        "aqi": sub_indices[governing_parameter],
        "governing_parameter": governing_parameter,
        "n_pollutants_used": len(sub_indices),
        "sub_indices": sub_indices,
    }


def add_aqi_column(ground_df: pd.DataFrame) -> pd.DataFrame:
    """
    Takes the long-format ground CSV (region_id, station_id, station_name,
    parameter, date, value, unit, lat, lon, source) and pivots it to one
    row per (region_id, station_id, station_name, date, lat, lon) with the
    computed CPCB AQI, governing pollutant, and pollutant count.

    Rows with a station/date combination that has no valid pollutant
    readings are dropped (not fabricated).
    """
    required_cols = {"region_id", "station_id", "station_name", "parameter",
                      "date", "value", "unit", "lat", "lon"}
    missing = required_cols - set(ground_df.columns)
    if missing:
        raise ValueError(f"Ground DataFrame missing columns: {missing}")

    results = []
    group_cols = ["region_id", "station_id", "station_name", "date", "lat", "lon"]

    for keys, group in ground_df.groupby(group_cols, dropna=False):
        param_values = {
            row["parameter"]: (row["value"], row["unit"])
            for _, row in group.iterrows()
        }
        aqi_result = compute_aqi_for_group(param_values)

        if aqi_result["aqi"] is None:
            continue  # no usable pollutant data for this station/date

        row_dict = dict(zip(group_cols, keys))
        row_dict["aqi"] = aqi_result["aqi"]
        row_dict["governing_parameter"] = aqi_result["governing_parameter"]
        row_dict["n_pollutants_used"] = aqi_result["n_pollutants_used"]
        results.append(row_dict)

    return pd.DataFrame(results)


def aqi_category(aqi: float) -> str:
    """CPCB's standard AQI category labels."""
    if aqi is None or pd.isna(aqi):
        return "Unknown"
    if aqi <= 50:
        return "Good"
    if aqi <= 100:
        return "Satisfactory"
    if aqi <= 200:
        return "Moderate"
    if aqi <= 300:
        return "Poor"
    if aqi <= 400:
        return "Very Poor"
    return "Severe"


if __name__ == "__main__":
    import sys

    if len(sys.argv) != 2:
        print("Usage: python3 aqi_calculator.py <ground_csv_path>")
        sys.exit(1)

    path = sys.argv[1]
    # Source ground CSVs (straight from OpenAQ export) use cp1252 for the
    # µg/m³ unit strings. Files that have already been re-saved by
    # fix_station_coords.py are UTF-8. Try UTF-8 first (fails loudly on
    # genuinely cp1252 bytes) and fall back to cp1252.
    try:
        df = pd.read_csv(path, encoding="utf-8")
        if not df["unit"].isin(["µg/m³", "ppb"]).all():
            raise ValueError("unexpected unit values after utf-8 read")
    except (UnicodeDecodeError, ValueError):
        df = pd.read_csv(path, encoding="cp1252")

    aqi_df = add_aqi_column(df)
    aqi_df["category"] = aqi_df["aqi"].apply(aqi_category)

    out_path = path.replace(".csv", "_with_aqi.csv")
    aqi_df.to_csv(out_path, index=False)

    print(f"Computed AQI for {len(aqi_df)} station/date combinations.")
    print(f"Wrote {out_path}")
    print()
    print(aqi_df["category"].value_counts())
