#!/usr/bin/env python3
"""
Fetch all Liftosaur body measurements (weight, bodyfat, and lengths) and
store them in 'daily_measurements' table. New columns are added automatically.

Table schema (updated dynamically):
    date          TEXT PRIMARY KEY,
    weight_kg     REAL,
    body_fat_percent REAL,
    neck_cm       REAL,           -- added if present
    chest_cm      REAL,
    biceps_left_cm REAL,
    ... etc.
"""

import sys
import sqlite3
import re
import requests
from datetime import datetime
import os
from dotenv import load_dotenv


# ============ CONFIGURATION ============
load_dotenv(os.Path(__file__).parent.parent / ".env")
print(os.environ.get("LIFTOSAUR_API_KEY"))
API_KEY = os.environ.get("LIFTOSAUR_API_KEY")
if not API_KEY:
    raise SystemExit("LIFTOSAUR_API_KEY not set. Copy .env.example to .env and fill it in.")

BASE_URL = "https://www.liftosaur.com/api/v1"
DB_PATH = "data/powerlifting.db"
# =======================================


def snake_case(name):
    """Convert camelCase to snake_case and lower-case."""
    # Insert underscore before capital letters
    s = re.sub(r'(?<=[a-z])(?=[A-Z])', '_', name)
    s = re.sub(r'(?<=[A-Z])(?=[A-Z][a-z])', '_', s)  # handle acronyms
    return s.lower().replace('-', '_').replace(' ', '_')

def get_column_name(key):
    """Return the column name for a measurement key, without suffix."""
    return snake_case(key)

def get_suffix(key):
    """Return the suffix and column type for the measurement."""
    if key == "weight":
        return "_kg", "REAL"
    elif key == "bodyfat":
        return "_percent", "REAL"
    else:
        return "_cm", "REAL"

def parse_value(value_str):
    """Parse numeric value and unit from string like '180lb' or '37cm'."""
    match = re.match(r"([\d.]+)\s*([a-zA-Z%]+)", value_str.strip())
    if not match:
        return None, None
    return float(match.group(1)), match.group(1).lower()

def convert_to_target(value_str, target_unit):
    """
    Parse value_str and convert to target_unit (kg, percent, or cm).
    Returns float or None.
    """
    num, unit = parse_value(value_str)
    if num is None:
        return None
    if target_unit == "kg":
        if unit in ("lb", "lbs"):
            return num * 0.453592
        elif unit == "kg":
            return num
        else:
            return None
    elif target_unit == "percent":
        if unit == "%":
            return num
        else:
            return None
    elif target_unit == "cm":
        if unit in ("cm", "centimeter", "centimeters"):
            return num
        elif unit in ("in", "inch", "inches"):
            return num * 2.54
        else:
            return None
    return None

def ensure_columns(conn, columns):
    """Add missing columns to the daily_measurements table."""
    cursor = conn.cursor()
    # Get existing columns
    cursor.execute("PRAGMA table_info(daily_measurements)")
    existing = {row[1] for row in cursor.fetchall()}
    for col_name, col_type in columns.items():
        if col_name not in existing:
            cursor.execute(f"ALTER TABLE daily_measurements ADD COLUMN {col_name} {col_type}")
            print(f"  → Added column: {col_name} {col_type}")
    conn.commit()

def get_all_measurement_keys():
    """Fetch all measurement keys from API."""
    url = f"{BASE_URL}/measurements"
    headers = {"Authorization": f"Bearer {API_KEY}"}
    resp = requests.get(url, headers=headers)
    if resp.status_code != 200:
        print(f"✗ Failed to get measurement list: {resp.status_code}")
        return []
    data = resp.json()
    keys = [m["key"] for m in data.get("data", {}).get("measurements", [])]
    print(f"✓ Found keys: {', '.join(keys)}")
    return keys

def fetch_history(key, limit=200):
    """Fetch all records for a measurement key (handles pagination)."""
    url = f"{BASE_URL}/measurements/{key}"
    headers = {"Authorization": f"Bearer {API_KEY}"}
    all_records = []
    cursor = None
    while True:
        params = {"limit": limit}
        if cursor:
            params["cursor"] = cursor
        resp = requests.get(url, headers=headers, params=params)
        if resp.status_code != 200:
            break
        data = resp.json()
        records = data.get("data", {}).get("records", [])
        if not records:
            break
        all_records.extend(records)
        cursor = data.get("data", {}).get("cursor")
        if not cursor:
            break
    return all_records

def main():
    if API_KEY == "lftsk_your_key_here":
        print("✗ Please set your API_KEY in the script.")
        sys.exit(1)

    print("=" * 50)
    print("Liftosaur All Measurements → SQLite")
    print("=" * 50)

    # 1. Get all measurement keys
    keys = get_all_measurement_keys()
    if not keys:
        print("✗ No keys retrieved.")
        sys.exit(1)

    # 2. Fetch records for each key and build a dictionary: date -> {column: value}
    all_data = {}  # date -> dict of column:value
    for key in keys:
        print(f"\n→ Fetching '{key}'...")
        records = fetch_history(key)
        if not records:
            print("  ⚠ No records.")
            continue

        col_base = get_column_name(key)
        suffix, col_type = get_suffix(key)
        col_name = col_base + suffix

        # Determine target unit
        if key == "weight":
            target_unit = "kg"
        elif key == "bodyfat":
            target_unit = "percent"
        else:
            target_unit = "cm"

        # Process each record
        for rec in records:
            date = rec.get("date")
            if not date:
                continue
            val_str = rec.get("value", "")
            num = convert_to_target(val_str, target_unit)
            if num is None:
                continue
            if date not in all_data:
                all_data[date] = {}
            all_data[date][col_name] = num

        print(f"  ✓ {len(records)} records processed → {len(all_data)} dates so far")

    if not all_data:
        print("\n⚠ No usable data.")
        return

    # 3. Determine all columns that will be stored
    all_columns = set()
    for row in all_data.values():
        all_columns.update(row.keys())

    # Ensure base columns date is primary key, and we have at least weight_kg, body_fat_percent?
    # But if they don't exist, they might not be in all_columns, we still add them if present.
    # We'll add all columns from all_data.

    # 4. Connect to DB and add missing columns
    conn = sqlite3.connect(DB_PATH)
    # Ensure table exists with at least date primary key
    conn.execute("""
        CREATE TABLE IF NOT EXISTS daily_measurements (
            date TEXT PRIMARY KEY
        )
    """)
    conn.commit()

    # Build column definitions for ensure_columns
    col_defs = {}
    for col in all_columns:
        # Determine type: if ends with _cm or _kg or _percent, use REAL, else TEXT? But we only have numeric.
        if col.endswith(("_cm", "_kg", "_percent")):
            col_defs[col] = "REAL"
        else:
            col_defs[col] = "TEXT"   # fallback (shouldn't happen)
    ensure_columns(conn, col_defs)

    # 5. Insert or replace data
    cursor = conn.cursor()
    inserted = 0
    updated = 0
    for date, row_data in all_data.items():
        # Build column list and placeholders
        cols = list(row_data.keys())
        vals = [row_data[col] for col in cols]
        # Ensure date is included as first column for primary key
        cols_with_date = ["date"] + cols
        vals_with_date = [date] + vals
        placeholders = ", ".join(["?"] * len(cols_with_date))
        col_names = ", ".join(cols_with_date)
        sql = f"INSERT OR REPLACE INTO daily_measurements ({col_names}) VALUES ({placeholders})"
        cursor.execute(sql, vals_with_date)
        if cursor.rowcount == 1:
            # Check if it was an update (row already existed)
            # We can't easily tell, but we'll count as inserted for simplicity
            inserted += 1
        else:
            updated += 1  # not exactly, but okay

    conn.commit()
    conn.close()

    print("\n" + "=" * 50)
    print(f"✓ Done. Stored/updated {inserted + updated} rows.")
    print(f"  Columns added: {len(all_columns)} measurement columns.")
    print("=" * 50)

if __name__ == "__main__":
    main()