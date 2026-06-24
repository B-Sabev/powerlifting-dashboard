"""
Liftosaur training log (SBD lifts) -> SQLite sync
---------------------------------------------------
Fetches workout history from the Liftosaur API and upserts completed SBD
(squat/bench/deadlift) sets into the `training_log` table in
data/powerlifting.db, one row per set.

The Liftosaur API has no structured per-set endpoint -- the only training
data available is GET /api/v1/history, which returns each workout as a
compact text summary, e.g.:

    2025-11-27 11:43:26 +00:00 / program: "..." / duration: 17647s / exercises: {
      Bench Press / 2x2 90kg, 3x3 80kg / warmup: 1x5 20kg, 1x5 40kg, 1x5 70kg / target: 5x1-5 90kg
    }

This script parses that text: per exercise line, the segment before
"warmup:"/"target:" is the completed sets (always clean "NxM weight+unit
[@rpe]" tokens, validated against real data -- rep ranges and AMRAP "+"
markers only ever appear in the target segment). Warmup sets are
intentionally not stored: the dashboard already excludes them, and since
Liftosaur's API (not this DB) is the source of truth, they're recoverable
later via --full if ever needed.

Incremental by default: paginates /history newest-first and stops once a
record's id is <= the high-water mark stored in `sync_state` (key=
'training_log'), reusing the same table/pattern as
sync_liftosaur_body_measurements.py. --full ignores it and re-pulls
everything -- the API holds the complete history, so this also doubles as
the one-time historical backfill (no separate backfill script needed).

There's no natural per-set unique key (identical sets within e.g. "3x3
100kg" are indistinguishable from each other), so re-syncs are made
idempotent per *workout*: every run deletes and reinserts all rows for each
workout_id it touches, rather than trying to dedupe individual sets.

Usage:
    python scripts/sync_liftosaur_training_log.py
    python scripts/sync_liftosaur_training_log.py --dry-run
    python scripts/sync_liftosaur_training_log.py --full   # ignore state, re-pull everything
    python scripts/sync_liftosaur_training_log.py --db /path/to/other.db
"""

import argparse
import os
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

API_KEY = os.environ.get("LIFTOSAUR_API_KEY")
BASE_URL = "https://www.liftosaur.com/api/v1"
DATA_DIR = Path(__file__).parent.parent / "data"
DEFAULT_DB_PATH = DATA_DIR / "powerlifting.db"
TABLE_NAME = "training_log"
STATE_TABLE_NAME = "sync_state"
STATE_KEY = "training_log"

# Matches the existing dashboard's SBD_EXERCISES exactly -- exercise names are
# stored verbatim as Liftosaur reports them (e.g. "Deadlift", not "Conventional
# Deadlift") so no other code needs to change.
SBD_EXERCISES = ["Bench Press", "Squat", "Deadlift", "Sumo Deadlift"]

LB_TO_KG = 0.453592

HEADER_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}:\d{2}) ([+-]\d{2}:\d{2})")
SET_TOKEN_RE = re.compile(r"(\d+)x(\d+)\s+([\d.]+)(kg|lb)(?:\s*@([\d.]+))?")


def _auth_headers() -> dict:
    """Build the Bearer-auth header Liftosaur expects."""
    return {"Authorization": f"Bearer {API_KEY}"}


def fetch_history_since(since_id: int) -> list[dict]:
    """
    Fetch /history records newer than since_id (0 = full history).

    The API returns workouts newest-first by "id". We page forward via
    "cursor"/"hasMore"/"nextCursor" and stop as soon as a page contains a
    record at or before since_id -- everything beyond that point has
    already been synced.
    """
    new_records = []
    cursor = None
    while True:
        params = {"cursor": cursor} if cursor else {}
        resp = requests.get(f"{BASE_URL}/history", headers=_auth_headers(), params=params, timeout=15)
        resp.raise_for_status()
        payload = resp.json().get("data", {})
        page = payload.get("records", [])

        hit_known_data = False
        for rec in page:
            if rec.get("id", 0) <= since_id:
                hit_known_data = True
                break
            new_records.append(rec)

        if hit_known_data or not payload.get("hasMore"):
            break
        cursor = payload.get("nextCursor")
        if not cursor:
            break
    return new_records


def _parse_header_date(text: str) -> str | None:
    """Extract the workout's UTC calendar date (YYYY-MM-DD) from the header line."""
    match = HEADER_RE.match(text)
    if not match:
        return None
    date_part, time_part, offset = match.groups()
    dt = datetime.fromisoformat(f"{date_part}T{time_part}{offset}")
    return dt.astimezone(timezone.utc).date().isoformat()


def _parse_completed_sets(segment: str) -> list[tuple[int, float, float | None]]:
    """Expand 'NxM weight+unit[@rpe]' tokens into (reps, weight_kg, rpe) rows, one per set."""
    rows = []
    for count, reps, weight, unit, rpe in SET_TOKEN_RE.findall(segment):
        if reps == "0":
            continue  # failed attempt (0 completed reps) -- not a real set, matches dashboard's Completed Reps > 0 filter
        weight_kg = float(weight) * LB_TO_KG if unit == "lb" else float(weight)
        rpe_val = float(rpe) if rpe else None
        rows.extend([(int(reps), weight_kg, rpe_val)] * int(count))
    return rows


def parse_workout(record: dict) -> tuple[str, list[dict]] | None:
    """
    Parse one /history record into (date, [{"exercise","reps","weight_kg","rpe"}, ...]).
    Returns None if the workout has no date or no SBD exercises logged.
    """
    text = record.get("text", "")
    date = _parse_header_date(text)
    if not date:
        print(f"  [WARN] Could not parse date from workout {record.get('id')} -- skipping.")
        return None

    rows = []
    for line in text.split("\n"):
        line = line.strip()
        for exercise in SBD_EXERCISES:
            if not line.startswith(f"{exercise} / "):
                continue
            rest = line[len(exercise) + 3:]
            # Completed sets are always the first segment, before an optional
            # "warmup:"/"target:" marker (whichever appears first). Warmup sets
            # are deliberately not parsed/stored -- see module docstring.
            completed_segment = re.split(r" / (?:warmup|target):", rest, maxsplit=1)[0]
            for reps, weight_kg, rpe in _parse_completed_sets(completed_segment):
                rows.append({"exercise": exercise, "reps": reps, "weight_kg": weight_kg, "rpe": rpe})
            break

    if not rows:
        return None
    return date, rows


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(f"""
        CREATE TABLE IF NOT EXISTS {TABLE_NAME} (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            workout_id INTEGER NOT NULL,
            date       TEXT NOT NULL,
            exercise   TEXT NOT NULL,
            reps       INTEGER NOT NULL,
            weight_kg  REAL NOT NULL,
            rpe        REAL
        )
    """)
    conn.execute(f"CREATE INDEX IF NOT EXISTS idx_{TABLE_NAME}_workout_id ON {TABLE_NAME}(workout_id)")
    conn.execute(f"CREATE INDEX IF NOT EXISTS idx_{TABLE_NAME}_date ON {TABLE_NAME}(date)")
    conn.execute(f"CREATE TABLE IF NOT EXISTS {STATE_TABLE_NAME} (key TEXT PRIMARY KEY, last_timestamp INTEGER)")


def upsert_workout(conn: sqlite3.Connection, workout_id: int, date: str, rows: list[dict]) -> None:
    """Replace all rows for one workout: delete-then-reinsert, since individual sets have no stable identity."""
    conn.execute(f"DELETE FROM {TABLE_NAME} WHERE workout_id = ?", (workout_id,))
    conn.executemany(
        f"INSERT INTO {TABLE_NAME} (workout_id, date, exercise, reps, weight_kg, rpe) VALUES (?, ?, ?, ?, ?, ?)",
        [(workout_id, date, r["exercise"], r["reps"], r["weight_kg"], r["rpe"]) for r in rows],
    )


def get_last_synced_id(conn: sqlite3.Connection) -> int:
    row = conn.execute(f"SELECT last_timestamp FROM {STATE_TABLE_NAME} WHERE key = ?", (STATE_KEY,)).fetchone()
    return row[0] if row else 0


def set_last_synced_id(conn: sqlite3.Connection, workout_id: int) -> None:
    conn.execute(
        f"INSERT INTO {STATE_TABLE_NAME} (key, last_timestamp) VALUES (?, ?) "
        f"ON CONFLICT(key) DO UPDATE SET last_timestamp = excluded.last_timestamp",
        (STATE_KEY, workout_id),
    )


def main():
    parser = argparse.ArgumentParser(description="Sync Liftosaur training history (SBD lifts) into the SQLite warehouse.")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH, help="Path to the SQLite database file.")
    parser.add_argument("--dry-run", action="store_true", help="Fetch and print what would be written, but don't touch the DB.")
    parser.add_argument("--full", action="store_true", help="Ignore sync_state and re-pull the full workout history.")
    args = parser.parse_args()

    if not API_KEY:
        raise SystemExit("LIFTOSAUR_API_KEY not set. Copy .env.example to .env and fill it in.")

    args.db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(args.db)
    ensure_schema(conn)

    since_id = 0 if args.full else get_last_synced_id(conn)
    print(f"Fetching workout history since_id={since_id}...")
    records = fetch_history_since(since_id)
    print(f"Fetched {len(records)} new workout(s).")

    parsed = []
    for record in records:
        result = parse_workout(record)
        if result is None:
            continue
        date, rows = result
        parsed.append((record["id"], date, rows))

    n_sets = sum(len(rows) for _, _, rows in parsed)
    print(f"Parsed {len(parsed)} workout(s) with SBD sets ({n_sets} total sets).")

    if args.dry_run:
        for workout_id, date, rows in parsed[:5]:
            print(f"  {date} (workout {workout_id}): {rows}")
        if len(parsed) > 5:
            print(f"  ... and {len(parsed) - 5} more workout(s)")
        print("[DRY RUN] No changes written to the database, sync_state left untouched.")
        conn.close()
        return

    try:
        for workout_id, date, rows in parsed:
            upsert_workout(conn, workout_id, date, rows)
        if records:
            set_last_synced_id(conn, max(r["id"] for r in records))
        conn.commit()
        total_rows = conn.execute(f"SELECT COUNT(*) FROM {TABLE_NAME}").fetchone()[0]
    finally:
        conn.close()

    print(f"Synced {len(parsed)} workout(s) -> {args.db.name} ({total_rows} total rows in {TABLE_NAME}).")


if __name__ == "__main__":
    main()
