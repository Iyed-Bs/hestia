#!/usr/bin/env python3
"""
Download historical weather data from Open-Meteo (free, no API key needed).

Output: data/raw/weather_YYYYMMDD.csv
        data/raw/weather_YYYYMMDD_meta.json

Usage:
    python scripts/download_data.py --days 180
    python scripts/download_data.py --lat 48.86 --lon 2.35 --days 90
"""

import csv
import json
import argparse
import sys
from datetime import datetime, timedelta
from pathlib import Path

import requests

# Default location: Tunis, Tunisia
DEFAULT_LAT = 36.8
DEFAULT_LON = 10.2


def fetch(lat: float, lon: float, days: int) -> list[dict]:
    end   = datetime.utcnow().date()
    start = end - timedelta(days=days)

    resp = requests.get(
        "https://archive-api.open-meteo.com/v1/archive",
        params={
            "latitude":    lat,
            "longitude":   lon,
            "start_date":  start.isoformat(),
            "end_date":    end.isoformat(),
            "hourly":      "temperature_2m,relative_humidity_2m,shortwave_radiation",
            "temperature_unit": "celsius",
            "timezone":    "UTC",
        },
        timeout=30,
    )
    resp.raise_for_status()
    h = resp.json()["hourly"]

    records = []
    for i, ts in enumerate(h["time"]):
        records.append({
            "timestamp_utc":        ts + "Z",
            "temperature_c":        float(h["temperature_2m"][i]),
            "humidity_pct":         float(h["relative_humidity_2m"][i]),
            "solar_irradiance_wpm2": float(h["shortwave_radiation"][i]),
        })
    return records


def save(records: list[dict], out_dir: Path, lat: float, lon: float):
    out_dir.mkdir(parents=True, exist_ok=True)
    tag  = datetime.utcnow().strftime("%Y%m%d")
    csv_path  = out_dir / f"weather_{tag}.csv"
    meta_path = out_dir / f"weather_{tag}_meta.json"

    with open(csv_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=records[0].keys())
        writer.writeheader()
        writer.writerows(records)

    meta = {
        "downloaded":   datetime.utcnow().isoformat(),
        "source":       "Open-Meteo archive API",
        "lat":          lat,
        "lon":          lon,
        "record_count": len(records),
        "first":        records[0]["timestamp_utc"],
        "last":         records[-1]["timestamp_utc"],
    }
    with open(meta_path, "w") as f:
        json.dump(meta, f, indent=2)

    print(f"Saved {len(records)} records → {csv_path}")
    return csv_path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lat",  type=float, default=DEFAULT_LAT)
    ap.add_argument("--lon",  type=float, default=DEFAULT_LON)
    ap.add_argument("--days", type=int,   default=180)
    ap.add_argument("--out",  default="data/raw")
    args = ap.parse_args()

    print(f"Fetching {args.days} days  lat={args.lat}  lon={args.lon}")
    try:
        records = fetch(args.lat, args.lon, args.days)
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)

    save(records, Path(args.out), args.lat, args.lon)
    print("Done. Next: python scripts/build_dataset.py")


if __name__ == "__main__":
    main()
