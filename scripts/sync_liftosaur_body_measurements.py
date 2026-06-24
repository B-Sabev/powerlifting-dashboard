"""
Liftosaur body measurements -> SQLite sync
-------------------------------------------
Fetches every body-measurement series from the Liftosaur API (weight,
bodyfat, and all custom length measurements) and upserts them into the
`daily_measurements` table in data/powerlifting.db, one row per calendar
date. New measurement keys logged in the Liftosaur app show up as new
columns automatically (ALTER TABLE) -- no code change needed here.

Usage:
    python scripts/sync_liftosaur_body_measurements.py
    python scripts/sync_liftosaur_body_measurements.py --db /path/to/other.db

Safe to re-run: existing dates are updated column-by-column
(ON CONFLICT DO UPDATE), not wholesale-replaced. A naive INSERT OR REPLACE
would null out every column not included in *this* run's data for that
date -- e.g. if a key's request fails, or you only re-sync one key --
silently destroying previously-synced measurements for that day.
"""

import argparse
import os
import re
import sqlite3
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

API_KEY = os.environ.get("LIFTOSAUR_API_KEY")
BASE_URL = "https://www.liftosaur.com/api/v1"
DATA_DIR = Path(__file__).parent.parent / "data"
DEFAULT_DB_PATH = DATA_DIR / "powerlifting.db"
TABLE_NAME = "daily_measurements"

LB_TO_KG = 0.453592
IN_TO_CM = 2.54

VALUE_RE = re.compile(r"^\s*([\d.]+)\s*([a-zA-Z%]+)\s*$")


def camel_to_snake(name: str) -> str:
    """Convert a camelCase (or acronym-bearing) API key to snake_case."""
    s = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", name)
    s = re.sub(r"(?<=[A-Z])(?=[A-Z][a-z])", "_", s)
    return s.lower().replace("-", "_").replace(" ", "_")


def column_for_key(key: str) -> tuple[str, str]:
    """Return (column_name, target_unit) for a Liftosaur measurement key."""
    base = camel_to_snake(key)
    if key == "weight":
        return f"{base}_kg", "kg"
    if key == "bodyfat":
        return "body_fat_percent", "percent"
    return f"{base}_cm", "cm"


def parse_and_convert(raw_value: str, target_unit: str, key: str) -> float | None:
    """Parse a 'NUMBER+UNIT' string and convert to target_unit; None (with a warning) if unparseable/unsupported."""
    match = VALUE_RE.match(raw_value or "")
    if not match:
        print(f"  [WARN] Could not parse value '{raw_value}' for key '{key}' -- skipping.")
        return None

    num = float(match.group(1))
    unit = match.group(2).lower()

    if target_unit == "kg":
        if unit == "kg":
            return num
        if unit in ("lb", "lbs"):
            return num * LB_TO_KG
    elif target_unit == "percent":
        if unit == "%":
            return num
    elif target_unit == "cm":
        if unit == "cm":
            return num
        if unit in ("in", "inch", "inches"):
            return num * IN_TO_CM

    print(f"  [WARN] Unsupported unit '{unit}' for key '{key}' (value '{raw_value}') -- skipping.")
    return None


def iso_to_date(iso_str: str) -> str:
    """Extract the YYYY-MM-DD date part from a Liftosaur ISO-8601 timestamp."""
    return iso_str.split("T")[0]


def _auth_headers() -> dict:
    """Build the Bearer-auth header Liftosaur expects."""
    return {"Authorization": f"Bearer {API_KEY}"}


def fetch_measurement_keys() -> list[str]:
    """Fetch the list of all measurement keys the user has logged in Liftosaur."""
    resp = requests.get(f"{BASE_URL}/measurements", headers=_auth_headers(), timeout=10)
    resp.raise_for_status()
    raw = resp.json().get("data", {}).get("measurements", [])
    return [m["key"] if isinstance(m, dict) else m for m in raw]


def fetch_key_history(key: str) -> list[dict]:
    """Fetch the full paginated time series for one measurement key."""
    values = []
    cursor = None
    while True:
        params = {"cursor": cursor} if cursor else {}
        resp = requests.get(f"{BASE_URL}/measurements/{key}", headers=_auth_headers(), params=params, timeout=10)
        resp.raise_for_status()
        payload = resp.json().get("data", {})
        values.extend(payload.get("values", []))
        if not payload.get("hasMore"):
            break
        cursor = payload.get("cursor")
        if not cursor:
            break
    return values


def ensure_columns(conn: sqlite3.Connection, columns: set) -> None:
    """Add any measurement columns that don't exist yet on daily_measurements (all REAL)."""
    existing = {row[1] for row in conn.execute(f"PRAGMA table_info({TABLE_NAME})")}
    for col in columns - existing:
        conn.execute(f"ALTER TABLE {TABLE_NAME} ADD COLUMN {col} REAL")
        print(f"  [INFO] Added column: {col}")


def upsert_day(conn: sqlite3.Connection, date: str, values: dict) -> None:
    """Insert or update one date's row, touching only the columns provided (others left untouched)."""
    cols = ["date"] + list(values.keys())
    placeholders = ", ".join("?" for _ in cols)
    update_clause = ", ".join(f"{c}=excluded.{c}" for c in values)
    sql = (
        f"INSERT INTO {TABLE_NAME} ({', '.join(cols)}) VALUES ({placeholders}) "
        f"ON CONFLICT(date) DO UPDATE SET {update_clause}"
    )
    conn.execute(sql, [date] + list(values.values()))


def main():
    parser = argparse.ArgumentParser(description="Sync Liftosaur body measurements into the SQLite warehouse.")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH, help="Path to the SQLite database file.")
    parser.add_argument("--dry-run", action="store_true", help="Fetch and print what would be written, but don't touch the DB.")
    args = parser.parse_args()

    if not API_KEY:
        raise SystemExit("LIFTOSAUR_API_KEY not set. Copy .env.example to .env and fill it in.")

    keys = fetch_measurement_keys()
    if not keys:
        raise SystemExit("No measurement keys returned by the API.")
    print(f"Found {len(keys)} measurement key(s): {', '.join(keys)}")

    by_date: dict = {}
    for key in keys:
        column, target_unit = column_for_key(key)
        print(f"-> Fetching '{key}' -> column '{column}'")
        try:
            records = fetch_key_history(key)
        except requests.RequestException as e:
            print(f"  [WARN] Request failed for key '{key}': {e} -- skipping this key.")
            continue

        for rec in records:
            raw_date = rec.get("date")
            if not raw_date:
                continue
            date = iso_to_date(raw_date)
            value = parse_and_convert(rec.get("value", ""), target_unit, key)
            if value is None:
                continue
            by_date.setdefault(date, {})[column] = value
        print(f"  {len(records)} record(s) processed.")

    if not by_date:
        print("No usable data fetched -- nothing to write.")
        return

    if args.dry_run:
        all_columns = sorted({col for row in by_date.values() for col in row})
        print(f"\n[DRY RUN] Would upsert {len(by_date)} date(s) across {len(all_columns)} column(s): {', '.join(all_columns)}")
        for date in sorted(by_date)[:5]:
            print(f"  {date}: {by_date[date]}")
        if len(by_date) > 5:
            print(f"  ... and {len(by_date) - 5} more date(s)")
        print("[DRY RUN] No changes written to the database.")
        return

    args.db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(args.db)
    try:
        conn.execute(f"CREATE TABLE IF NOT EXISTS {TABLE_NAME} (date TEXT PRIMARY KEY)")
        all_columns = {col for row in by_date.values() for col in row}
        ensure_columns(conn, all_columns)
        for date, values in by_date.items():
            upsert_day(conn, date, values)
        conn.commit()
        total_rows = conn.execute(f"SELECT COUNT(*) FROM {TABLE_NAME}").fetchone()[0]
    finally:
        conn.close()

    print(f"Synced {len(by_date)} date(s) -> {args.db.name} ({total_rows} total rows in {TABLE_NAME}).")


if __name__ == "__main__":
    main()
