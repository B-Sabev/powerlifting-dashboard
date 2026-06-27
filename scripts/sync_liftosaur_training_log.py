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

workout_completion table (hybrid model)
---------------------------------------
This script also writes a `workout_completion` table (one row per workout)
recording planned_sets and completed_sets so the dashboard can compute
"% of planned workout completed":

  planned_sets = (A) target: set-counts for exercises the user *started*
               + (B) base set-counts from the program day for exercises
                     the user *completely skipped*

  completed_sets = every logged set (including ad-hoc substitutes), 0-rep
                   attempts excluded (matching the training_log filter).

Part A resolves dynamic add-set Liftoscript logic correctly because
Liftosaur's API already bakes the update() result into the target: segment
of any started exercise (verified: a 5-Day-Hyp SBD set with numberOfSets+=3
at RPE≥9 shows 4 sets in target:, not 1). No Liftoscript interpreter needed.

Part B requires GET /api/v1/programs to know which exercises were planned
but never started.  Programs are fetched once per sync run and keyed by name.
Template-based exercises (e.g. "Bench Press / ...t1_upperbody") get the
template's base set count for skipped detection (1 set in this case, since
the dynamic additions are runtime-only). Skipped exercises with update logic
are thus conservatively counted at their base prescription.

Known limitations:
- Freeform workouts (no program metadata in header, ~10/158) get target-only.
- Workouts on imported programs ("Hevy", ~19/158) not in the account's /programs
  list also get target-only.
- Part B uses the *current* program structure (no historical snapshot in the API);
  set counts are more stable than weights across progressions, so this is minor.

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
COMPLETION_TABLE_NAME = "workout_completion"
STATE_TABLE_NAME = "sync_state"
STATE_KEY = "training_log"

# Matches the existing dashboard's SBD_EXERCISES exactly -- exercise names are
# stored verbatim as Liftosaur reports them (e.g. "Deadlift", not "Conventional
# Deadlift") so no other code needs to change.
SBD_EXERCISES = ["Bench Press", "Squat", "Deadlift", "Sumo Deadlift"]

LB_TO_KG = 0.453592

HEADER_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}:\d{2}) ([+-]\d{2}:\d{2})")
SET_TOKEN_RE = re.compile(r"(\d+)x(\d+)\s+([\d.]+)(kg|lb)(?:\s*@([\d.]+))?")
# Matches "NxM", "NxM-P" (rep range), "NxM+" (AMRAP) in target/program setspecs — captures N
SET_COUNT_RE = re.compile(r"(\d+)x[\d]")

# Workout header metadata (only present when workout was done under a program)
PROGRAM_META_RE = re.compile(r'program: "([^"]+)"')
DAY_NAME_META_RE = re.compile(r'dayName: "([^"]+)"')
WEEK_META_RE = re.compile(r"/ week: (\d+)")

# Program text structure (Liftoscript)
WEEK_HEADER_RE = re.compile(r"^# Week (\d+)\s*$")
DAY_HEADER_RE = re.compile(r"^## (.+)$")
LABEL_PREFIX_RE = re.compile(r"^\w+:\s*")  # strips "w1:", "w2:", etc.


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


def fetch_all_programs() -> dict[str, str]:
    """Returns {program_name: program_id} for all programs in the account."""
    resp = requests.get(f"{BASE_URL}/programs", headers=_auth_headers(), timeout=15)
    resp.raise_for_status()
    return {p["name"]: p["id"] for p in resp.json().get("data", {}).get("programs", [])}


def fetch_program_text(program_id: str) -> str:
    """Fetches the Liftoscript text for a single program by ID."""
    resp = requests.get(f"{BASE_URL}/programs/{program_id}", headers=_auth_headers(), timeout=15)
    resp.raise_for_status()
    return resp.json().get("data", {}).get("text", "")


def parse_program_days(text: str) -> tuple[dict[tuple[int, str], dict[str, int]], dict[str, int]]:
    """
    Parse a Liftoscript program into (day_map, template_map):

      day_map: {(week_num, day_name_lower): {exercise_name_lower: base_set_count}}
      template_map: {template_name_lower: base_set_count}

    Template definition lines ("ExerciseName / used: none / NxM / ...") extract the
    base set count for exercises that reference them via "...TemplateName". Base counts
    are pre-update: the runtime numberOfSets additions from update: scripts are not
    evaluated here -- those are correctly resolved via history's target: segment for
    any exercise the user actually started.

    Liftoscript code blocks ({~ ... ~}) are tracked by depth counter so their internal
    lines are skipped. The line that opens a block is still parsed for exercise data
    (the set spec precedes the {~).
    """
    template_map: dict[str, int] = {}
    day_map: dict[tuple[int, str], dict[str, int]] = {}

    current_week = 1
    current_day: str | None = None
    in_script = 0  # depth counter for {~ ... ~} blocks

    for raw_line in text.split("\n"):
        stripped = raw_line.strip()

        # Track whether we were inside a script block *before* this line
        was_in_script = in_script > 0
        opens = stripped.count("{~")
        closes = stripped.count("~}")
        in_script += opens - closes

        # Skip lines that are *inside* a script block (opened on a prior line)
        if was_in_script:
            continue
        # Skip the closing delimiter line itself (e.g. bare "~}")
        if closes > 0 and opens == 0:
            continue

        # If this line opens a block, only the part before {~ is exercise data
        parseable = stripped.split("{~")[0].strip() if "{~" in stripped else stripped

        # Skip blank lines and comments
        if not parseable or parseable.startswith("//"):
            continue

        # Week header: "# Week N"
        wm = WEEK_HEADER_RE.match(parseable)
        if wm:
            current_week = int(wm.group(1))
            current_day = None
            continue

        # Day header: "## Day N - Name"
        dm = DAY_HEADER_RE.match(parseable)
        if dm:
            current_day = dm.group(1).strip().lower()
            day_map.setdefault((current_week, current_day), {})
            continue

        # Not inside a named day yet (preamble before first ##)
        if current_day is None:
            continue

        if " / " not in parseable:
            continue

        parts = parseable.split(" / ")
        # Strip label prefix (e.g. "w1:", "w2:") from the exercise name
        name = LABEL_PREFIX_RE.sub("", parts[0]).strip().lower()
        set_spec = parts[1] if len(parts) > 1 else ""

        # Template definition: "TemplateName / used: none / NxM / ..."
        if set_spec.startswith("used: none"):
            base_spec = parts[2] if len(parts) > 2 else ""
            template_map[name] = _count_set_tokens(base_spec)
            continue

        key = (current_week, current_day)

        # Template reference: "ExerciseName / ...TemplateName"
        if set_spec.startswith("..."):
            tmpl_name = set_spec[3:].strip().lower()
            day_map[key][name] = template_map.get(tmpl_name, 0)
            continue

        # Regular exercise: count sets from setspec
        day_map[key][name] = _count_set_tokens(set_spec)

    return day_map, template_map


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


def _count_completed_sets(segment: str) -> int:
    """Total number of sets (not reps) in a completed segment, skipping 0-rep attempts."""
    return sum(int(count) for count, reps, *_ in SET_TOKEN_RE.findall(segment) if reps != "0")


def _count_set_tokens(segment: str) -> int:
    """
    Sum the N (set-count) part of each "NxM", "NxM-P", or "NxM+" token in a segment.
    Works for both history target: segments and program set-specs (e.g. '5x4-8 / 90kg' → 5,
    '1x10-15 145lb, 1x5 130lb, 1x5 115lb, 1x5 100lb, 1x10 100lb' → 5).
    """
    return sum(int(m.group(1)) for m in SET_COUNT_RE.finditer(segment))


def _parse_workout_header_meta(text: str) -> tuple[str | None, int | None, str | None]:
    """Extract (program_name, week_num, day_name) from the first line of a workout record."""
    header = text.split("\n")[0]
    prog = PROGRAM_META_RE.search(header)
    week = WEEK_META_RE.search(header)
    day = DAY_NAME_META_RE.search(header)
    return (
        prog.group(1) if prog else None,
        int(week.group(1)) if week else None,
        day.group(1) if day else None,
    )


def parse_workout_completion(
    record: dict,
    program_data: dict[str, tuple[dict, dict]] | None = None,
) -> tuple[str, int, int] | None:
    """
    Hybrid planned/completed set counting for one /history record.

    planned_sets =
      (A) target: set-counts for exercises that were started (dynamically resolved by
          Liftosaur — includes any sets added by update: scripts at runtime), plus
      (B) base set-counts from the program day for exercises completely skipped
          (requires program_data; falls back to A-only when unavailable).
    completed_sets = all logged sets across ALL exercises (incl. ad-hoc substitutes),
                     0-rep attempts excluded.

    Returns None if the workout has no date or no sets at all.
    """
    text = record.get("text", "")
    date = _parse_header_date(text)
    if not date:
        return None

    program_name, week_num, day_name = _parse_workout_header_meta(text)

    planned_A = 0
    completed_total = 0
    present_exercises: set[str] = set()  # normalized names of exercises that were started

    for line in text.split("\n"):
        stripped = line.strip()
        if " / " not in stripped:
            continue
        exercise_name = stripped.split(" / ", 1)[0]
        # Skip the header line (starts with a date → digit, or contains ':' before ' / ')
        if not exercise_name or ":" in exercise_name:
            continue

        rest = stripped[len(exercise_name) + 3:]  # everything after "Name / "
        completed_segment = re.split(r" / (?:warmup|target):", rest, maxsplit=1)[0]
        target_match = re.search(r" / target: (.+)$", rest)
        target_segment = target_match.group(1).strip() if target_match else ""

        completed_total += _count_completed_sets(completed_segment)
        planned_A += _count_set_tokens(target_segment)
        present_exercises.add(exercise_name.strip().lower())

    # Part B: add base set-counts for exercises in the program day that were never started
    planned_B = 0
    if program_data and program_name and week_num is not None and day_name:
        prog_entry = program_data.get(program_name.lower())
        if prog_entry:
            day_map, _ = prog_entry
            day_exercises = day_map.get((week_num, day_name.lower()), {})
            for ex_norm, base_sets in day_exercises.items():
                if ex_norm not in present_exercises:
                    planned_B += base_sets

    planned_total = planned_A + planned_B
    if planned_total == 0 and completed_total == 0:
        return None
    return date, planned_total, completed_total


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
    conn.execute(f"""
        CREATE TABLE IF NOT EXISTS {COMPLETION_TABLE_NAME} (
            workout_id     INTEGER PRIMARY KEY,
            date           TEXT NOT NULL,
            planned_sets   INTEGER NOT NULL,
            completed_sets INTEGER NOT NULL
        )
    """)


def upsert_workout(conn: sqlite3.Connection, workout_id: int, date: str, rows: list[dict]) -> None:
    """Replace all rows for one workout: delete-then-reinsert, since individual sets have no stable identity."""
    conn.execute(f"DELETE FROM {TABLE_NAME} WHERE workout_id = ?", (workout_id,))
    conn.executemany(
        f"INSERT INTO {TABLE_NAME} (workout_id, date, exercise, reps, weight_kg, rpe) VALUES (?, ?, ?, ?, ?, ?)",
        [(workout_id, date, r["exercise"], r["reps"], r["weight_kg"], r["rpe"]) for r in rows],
    )


def upsert_workout_completion(conn: sqlite3.Connection, workout_id: int, date: str, planned: int, completed: int) -> None:
    conn.execute(
        f"INSERT INTO {COMPLETION_TABLE_NAME} (workout_id, date, planned_sets, completed_sets) VALUES (?, ?, ?, ?)"
        f" ON CONFLICT(workout_id) DO UPDATE SET"
        f"   date=excluded.date, planned_sets=excluded.planned_sets, completed_sets=excluded.completed_sets",
        (workout_id, date, planned, completed),
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

    # Fetch program data once for the hybrid completion model (part B: skipped exercises).
    # Only needed when some records carry program metadata; skipped for freeform workouts.
    program_data: dict | None = None
    if any(PROGRAM_META_RE.search(r.get("text", "")) for r in records):
        print("Fetching program data for workout-completion (skipped-exercise recovery)...")
        try:
            program_data = {}
            all_progs = fetch_all_programs()
            for prog_name, prog_id in all_progs.items():
                prog_text = fetch_program_text(prog_id)
                day_map, tmpl_map = parse_program_days(prog_text)
                program_data[prog_name.lower()] = (day_map, tmpl_map)
            print(f"  Loaded {len(program_data)} program(s).")
        except Exception as exc:
            print(f"  [WARN] Could not load programs ({exc}); completion will use target: only.")
            program_data = None

    parsed = []
    completions = []
    for record in records:
        result = parse_workout(record)
        if result is not None:
            date, rows = result
            parsed.append((record["id"], date, rows))

        comp = parse_workout_completion(record, program_data=program_data)
        if comp is not None:
            comp_date, planned, completed = comp
            completions.append((record["id"], comp_date, planned, completed))

    n_sets = sum(len(rows) for _, _, rows in parsed)
    print(f"Parsed {len(parsed)} workout(s) with SBD sets ({n_sets} total sets).")
    print(f"Parsed {len(completions)} workout(s) with completion data.")

    if args.dry_run:
        for workout_id, date, rows in parsed[:5]:
            print(f"  {date} (workout {workout_id}): {rows}")
        if len(parsed) > 5:
            print(f"  ... and {len(parsed) - 5} more workout(s)")
        for workout_id, date, planned, completed in completions[:5]:
            print(f"  {date} (workout {workout_id}): {completed}/{planned} sets ({min(completed/planned*100, 100):.0f}%)" if planned else f"  {date}: no target data")
        if len(completions) > 5:
            print(f"  ... and {len(completions) - 5} more workout(s)")
        print("[DRY RUN] No changes written to the database, sync_state left untouched.")
        conn.close()
        return

    try:
        for workout_id, date, rows in parsed:
            upsert_workout(conn, workout_id, date, rows)
        for workout_id, date, planned, completed in completions:
            upsert_workout_completion(conn, workout_id, date, planned, completed)
        if records:
            set_last_synced_id(conn, max(r["id"] for r in records))
        conn.commit()
        total_rows = conn.execute(f"SELECT COUNT(*) FROM {TABLE_NAME}").fetchone()[0]
    finally:
        conn.close()

    print(f"Synced {len(parsed)} workout(s) -> {args.db.name} ({total_rows} total rows in {TABLE_NAME}).")


if __name__ == "__main__":
    main()
