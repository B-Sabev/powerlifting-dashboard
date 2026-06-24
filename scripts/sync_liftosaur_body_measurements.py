"""
Liftosaur body measurements -> SQLite sync
-------------------------------------------
Fetches body-measurement series from the Liftosaur API (weight, bodyfat,
and all custom length measurements) and upserts them into the
`daily_measurements` table in data/powerlifting.db, one row per calendar
date. New measurement keys logged in the Liftosaur app show up as new
columns automatically (ALTER TABLE) -- no code change needed here.

Incremental by default: a `sync_state` table tracks the newest timestamp
already synced per key. The Liftosaur API returns each key's history
newest-first, so a run only pages forward until it reaches a record at or
before that high-water mark, then stops -- no full historical re-pull.
First run for a key (no state yet) pulls its full history once.

Usage:
    python scripts/sync_liftosaur_body_measurements.py
    python scripts/sync_liftosaur_body_measurements.py --dry-run
    python scripts/sync_liftosaur_body_measurements.py --full   # ignore state, re-pull everything
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
STATE_TABLE_NAME = "sync_state"

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


def fetch_key_history_since(key: str, since_ts: int) -> list[dict]:
    """
    Fetch records for one key newer than since_ts (0 = full history).

    The API returns each key's values newest-first. We page forward and stop
    as soon as a page contains a record at or before since_ts -- everything
    beyond that point has already been synced, so there's no need to fetch it.
    """
    new_records = []
    cursor = None
    while True:
        params = {"cursor": cursor} if cursor else {}
        resp = requests.get(f"{BASE_URL}/measurements/{key}", headers=_auth_headers(), params=params, timeout=10)
        resp.raise_for_status()
        payload = resp.json().get("data", {})
        page = payload.get("values", [])

        hit_known_data = False
        for rec in page:
            if rec.get("timestamp", 0) <= since_ts:
                hit_known_data = True
                break
            new_records.append(rec)

        if hit_known_data or not payload.get("hasMore"):
            break
        cursor = payload.get("cursor")
        if not cursor:
            break
    return new_records


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


def get_last_synced_ts(conn: sqlite3.Connection, key: str) -> int:
    """Return the newest timestamp already synced for this key, or 0 if never synced."""
    row = conn.execute(f"SELECT last_timestamp FROM {STATE_TABLE_NAME} WHERE key = ?", (key,)).fetchone()
    return row[0] if row else 0


def set_last_synced_ts(conn: sqlite3.Connection, key: str, ts: int) -> None:
    """Record the newest timestamp synced for this key, so the next run can skip everything up to it."""
    conn.execute(
        f"INSERT INTO {STATE_TABLE_NAME} (key, last_timestamp) VALUES (?, ?) "
        f"ON CONFLICT(key) DO UPDATE SET last_timestamp = excluded.last_timestamp",
        (key, ts),
    )


def main():
    parser = argparse.ArgumentParser(description="Sync Liftosaur body measurements into the SQLite warehouse.")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH, help="Path to the SQLite database file.")
    parser.add_argument("--dry-run", action="store_true", help="Fetch and print what would be written, but don't touch the DB.")
    parser.add_argument("--full", action="store_true", help="Ignore sync_state and re-pull each key's full history.")
    args = parser.parse_args()

    if not API_KEY:
        raise SystemExit("LIFTOSAUR_API_KEY not set. Copy .env.example to .env and fill it in.")

    keys = fetch_measurement_keys()
    if not keys:
        raise SystemExit("No measurement keys returned by the API.")
    print(f"Found {len(keys)} measurement key(s): {', '.join(keys)}")

    args.db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(args.db)
    conn.execute(f"CREATE TABLE IF NOT EXISTS {TABLE_NAME} (date TEXT PRIMARY KEY)")
    conn.execute(f"CREATE TABLE IF NOT EXISTS {STATE_TABLE_NAME} (key TEXT PRIMARY KEY, last_timestamp INTEGER)")

    by_date: dict = {}
    max_ts_per_key: dict = {}
    for key in keys:
        column, target_unit = column_for_key(key)
        since_ts = 0 if args.full else get_last_synced_ts(conn, key)
        print(f"-> Fetching '{key}' -> column '{column}' (since_ts={since_ts})")
        try:
            records = fetch_key_history_since(key, since_ts)
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
            ts = rec.get("timestamp", 0)
            if ts > max_ts_per_key.get(key, since_ts):
                max_ts_per_key[key] = ts
        print(f"  {len(records)} new record(s).")

    if not by_date:
        print("Nothing new to sync.")
        conn.close()
        return

    if args.dry_run:
        all_columns = sorted({col for row in by_date.values() for col in row})
        print(f"\n[DRY RUN] Would upsert {len(by_date)} date(s) across {len(all_columns)} column(s): {', '.join(all_columns)}")
        for date in sorted(by_date)[:5]:
            print(f"  {date}: {by_date[date]}")
        if len(by_date) > 5:
            print(f"  ... and {len(by_date) - 5} more date(s)")
        print("[DRY RUN] No changes written to the database, sync_state left untouched.")
        conn.close()
        return

    try:
        all_columns = {col for row in by_date.values() for col in row}
        ensure_columns(conn, all_columns)
        for date, values in by_date.items():
            upsert_day(conn, date, values)
        for key, ts in max_ts_per_key.items():
            set_last_synced_ts(conn, key, ts)
        conn.commit()
        total_rows = conn.execute(f"SELECT COUNT(*) FROM {TABLE_NAME}").fetchone()[0]
    finally:
        conn.close()

    print(f"Synced {len(by_date)} date(s) -> {args.db.name} ({total_rows} total rows in {TABLE_NAME}).")


if __name__ == "__main__":
    main()
