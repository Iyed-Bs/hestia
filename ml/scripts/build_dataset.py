#!/usr/bin/env python3
"""
Build the training dataset from raw weather CSV.

Applies physical formulas for PV power and thermal demand.
Outputs a single dataset.csv with 9 columns.

Usage:
    python scripts/build_dataset.py
    python scripts/build_dataset.py --input data/raw/weather_20260521.csv
"""

import csv
import json
import argparse
import sys
from datetime import datetime
from pathlib import Path

# ── System parameters ──────────────────────────────────────────────────────
PV_PANEL_AREA_M2   = 10.0   # Total panel surface area (m²)
PV_EFFICIENCY      = 0.18   # Panel efficiency (18 % is typical for mono-Si)
THERMAL_SETPOINT_C = 22.0   # Comfort setpoint for heating/cooling (°C)
HVAC_W_PER_DEG     = 500.0  # Approximate HVAC load per °C deviation


def load_raw(path: Path) -> list[dict]:
    rows = []
    with open(path) as f:
        for i, row in enumerate(csv.DictReader(f), 1):
            try:
                rows.append({
                    "timestamp_utc":         row["timestamp_utc"],
                    "temperature_c":         float(row["temperature_c"]),
                    "humidity_pct":          float(row["humidity_pct"]),
                    "solar_irradiance_wpm2": float(row["solar_irradiance_wpm2"]),
                })
            except (ValueError, KeyError) as e:
                print(f"  Row {i} skipped: {e}")
    return rows


def validate(rows: list[dict]) -> list[dict]:
    clean = []
    for r in rows:
        if not (-50 <= r["temperature_c"] <= 60):
            continue
        if not (0 <= r["humidity_pct"] <= 100):
            continue
        if not (0 <= r["solar_irradiance_wpm2"] <= 1500):
            continue
        clean.append(r)
    print(f"  {len(clean)}/{len(rows)} rows passed validation")
    return clean


def add_features(rows: list[dict]) -> list[dict]:
    for r in rows:
        ts  = r["timestamp_utc"]
        irr = r["solar_irradiance_wpm2"]
        t   = r["temperature_c"]

        # PV power: P = G × η × A  (Watts)
        r["pv_power_w"] = round(irr * PV_EFFICIENCY * PV_PANEL_AREA_M2, 1)

        # Thermal demand: proportional to deviation from comfort setpoint
        r["thermal_demand_w"] = round(abs(t - THERMAL_SETPOINT_C) * HVAC_W_PER_DEG, 1)

        # Hour of day and day of year (used as model features)
        try:
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            r["hour"]       = dt.hour
            r["day_of_year"] = dt.timetuple().tm_yday
        except Exception:
            r["hour"]        = 0
            r["day_of_year"] = 0

        # Occupancy: simplified work-hours pattern
        is_workday  = dt.weekday() < 5 if "dt" in dir() else True
        r["occupancy"] = round(0.85 if (is_workday and 8 <= r["hour"] < 18) else 0.15, 2)

    return rows


COLUMNS = [
    "timestamp_utc", "hour", "day_of_year",
    "temperature_c", "humidity_pct", "solar_irradiance_wpm2",
    "pv_power_w", "thermal_demand_w", "occupancy",
]


def save(rows: list[dict], out_dir: Path):
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path  = out_dir / "dataset.csv"
    stat_path = out_dir / "dataset_stats.json"

    with open(csv_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)

    temps = [r["temperature_c"] for r in rows]
    pvs   = [r["pv_power_w"]    for r in rows]
    stats = {
        "records":        len(rows),
        "first":          rows[0]["timestamp_utc"],
        "last":           rows[-1]["timestamp_utc"],
        "temperature_c":  {"min": min(temps), "max": max(temps),
                           "mean": round(sum(temps)/len(temps), 2)},
        "pv_power_w":     {"min": min(pvs),   "max": max(pvs),
                           "mean": round(sum(pvs)/len(pvs), 2)},
    }
    with open(stat_path, "w") as f:
        json.dump(stats, f, indent=2)

    print(f"  Saved {csv_path}  ({len(rows)} rows)")
    print(f"  Saved {stat_path}")


def find_all_raw(raw_dir: Path) -> list[Path]:
    """Return all weather CSV files sorted chronologically (oldest first)."""
    files = sorted(raw_dir.glob("weather_*.csv"))
    # Exclude meta/non-data files (those ending in _meta.json handled by glob, but filter CSVs)
    return [f for f in files if not f.name.endswith("_meta.json")]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input",  default=None,
                    help="Specific raw CSV. If omitted, ALL weather_*.csv in rawdir are merged.")
    ap.add_argument("--rawdir", default="data/raw")
    ap.add_argument("--out",    default="data/processed")
    args = ap.parse_args()

    if args.input:
        raw_paths = [Path(args.input)]
    else:
        raw_paths = find_all_raw(Path(args.rawdir))

    if not raw_paths:
        print("No raw CSV found. Run: python scripts/download_data.py")
        sys.exit(1)

    print(f"Merging {len(raw_paths)} raw file(s):")
    all_rows = []
    seen_timestamps = set()
    for p in raw_paths:
        print(f"  {p.name}")
        rows = load_raw(p)
        rows = validate(rows)
        # Deduplicate by timestamp to avoid overlap between files
        for r in rows:
            ts = r["timestamp_utc"]
            if ts not in seen_timestamps:
                seen_timestamps.add(ts)
                all_rows.append(r)

    # Sort chronologically
    all_rows.sort(key=lambda r: r["timestamp_utc"])
    print(f"Total after dedup: {len(all_rows)} rows")

    all_rows = add_features(all_rows)
    save(all_rows, Path(args.out))
    print("Done. Next: python scripts/train.py")


if __name__ == "__main__":
    main()
