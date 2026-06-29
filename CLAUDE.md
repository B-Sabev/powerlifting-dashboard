# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A personal Streamlit dashboard (`powerlifting_dashboard.py`) for tracking powerlifting
progress, recovery, and nutrition/weight, fed by a set of standalone sync scripts that pull
from various sources (Cronometer, Liftosaur API, a Google Sheet) into a local SQLite
warehouse (`data/powerlifting.db`). This project also serves as a porfolio project in applying for jobs related to AI, Data Science, LLM-assisted development, when appropriate suggest how to make the project better for discussing during interviews and demonstrate skills in the aforementioned areas.

Three docs, no overlap by design — keep it that way when editing them:
- **This file (`CLAUDE.md`)** = *how it works* — the architecture/mechanism reference.
- **`PROJECT_LOG.md`** = *why it's locked + what's next* — compressed locked-decision rationale
  (don't re-litigate without a strong reason) and the backlog. Read it first in any new session.
  Keep it rationale-and-backlog only; don't re-describe mechanisms that live here.
- **`CHANGE_LOG.md`** = append-only one-line-per-session history; add an entry after non-trivial
  work, most recent on top.

## Commands

```bash
# Setup (uses a project-local venv at .venv)
.venv/bin/pip install -r requirements.txt

# Run the dashboard
.venv/bin/streamlit run powerlifting_dashboard.py

# Run a sync script manually
.venv/bin/python3 scripts/sync_cronometer.py [--csv path] [--db path]
.venv/bin/python3 scripts/sync_checkin.py [--sheet-id ID] [--gid GID]
.venv/bin/python3 scripts/sync_liftosaur_body_measurements.py [--dry-run] [--full] [--db path]
.venv/bin/python3 scripts/sync_liftosaur_training_log.py [--dry-run] [--full] [--db path]
```

```bash
# Run tests (pure functions in lib/calculations.py)
.venv/bin/python -m pytest
```

There is a small pytest suite for `lib/calculations.py`'s pure functions (`tests/`); no
linter or CI config in this repo.

## Data flow architecture

**Real vs demo data:** `data/powerlifting.db` and `data/daily_checkin.csv` are gitignored
(real personal data, local only). `lib/constants.py` falls back to `data/demo_powerlifting.db`
and `data/demo_daily_checkin.csv` when the real files are absent — this is what the public
Streamlit Cloud app uses. Regenerate demo data with `scripts/generate_demo_data.py`.

**Backups:** The two gitignored local-only files are backed up daily to a private GitHub repo
(`B-Sabev/powerlifting-data-backup`, cloned at `~/Projects/powerlifting-data-backup`) via
`scripts/backup_data.sh`, run as the last step of the daily cron job. Push uses the
`github-pl-backup` SSH host alias in `~/.ssh/config` with a deploy key scoped to that repo
only. Each day's sync produces one new commit if any data changed; no-op if unchanged.

*Restore:* `git clone git@github-pl-backup:B-Sabev/powerlifting-data-backup.git` then copy
`powerlifting.db` + `daily_checkin.csv` into `data/`. To roll back a corrupting sync:
`cd ~/Projects/powerlifting-data-backup && git checkout <commit> -- powerlifting.db`
then copy the file across.

Four independent ingestion paths feed `data/powerlifting.db`, each with its own sync
script under `scripts/`. The dashboard itself never writes to the DB — it only reads.

1. **Nutrition** — `cronometer-export` binary (in `scripts/`, pulls from Cronometer's
   service) → CSV → `scripts/sync_cronometer.py` upserts into the
   `cronometer_daily_nutrition` table, keyed on `date`. Column names are derived from
   `COLUMN_MAP` in that script (Cronometer's raw headers → snake_case); if Cronometer
   changes its export headers, update `COLUMN_MAP` there.
2. **Body measurements** (weight, body fat %, and arbitrary length measurements like
   waist/chest/limbs) — Liftosaur API → `scripts/sync_liftosaur_body_measurements.py` →
   `daily_measurements` table. New measurement keys logged in the Liftosaur app appear as
   new columns automatically via `ALTER TABLE`. This script is incremental by default
   (a `sync_state` table tracks the last-synced timestamp per measurement key); `--full`
   forces a complete re-pull. Requires `LIFTOSAUR_API_KEY` in `.env` (copy from
   `.env.example`).
3. **Daily check-in** (sleep, mood, stress, soreness, session quality, notes, and an optional
   `Intervention` free-text column) — a Google Sheet shared as "Anyone with the link" →
   `scripts/sync_checkin.py` downloads it via the public CSV export URL and **fully overwrites**
   `data/daily_checkin.csv` (no DB, no upsert — the sheet already holds full history,
   append-only, in Google Sheets). The `Intervention` column (18th column) is free-text: when
   non-blank on a given date, Tab 5 draws an annotated vertical marker on that date across all
   its charts. Log an intervention's *start date* here (e.g. "5 min meditation before bed").
4. **Training log** (SBD working sets — squat/bench/deadlift, for now) — Liftosaur API →
   `scripts/sync_liftosaur_training_log.py` → `training_log` table. Liftosaur's API has no
   structured per-set endpoint; the only training-data endpoint (`GET /history`) returns
   each workout as a compact text summary that the script regex-parses (see its module
   docstring for the exact format). No natural per-set unique key exists, so re-syncs are
   idempotent via delete-then-reinsert keyed on `workout_id`, not a column-level upsert.
   Incremental by default via the same `sync_state` table as body measurements
   (`key='training_log'`); `--full` re-pulls everything (also doubles as the historical
   backfill — the API holds full history, no separate backfill script needed). Warmup sets
   and failed/0-rep attempts are dropped during parsing, matching what the dashboard already
   filtered out of the old CSV export.
   The same sync script also writes a **`workout_completion`** table (one row per workout,
   `workout_id` PK) with `planned_sets` and `completed_sets` for the Tab 2 "% of planned
   workout completed" metric. Planned sets use a **hybrid model**: (A) the history text's
   `target:` segment for any exercise that was started — this already resolves Liftoscript
   `update:` logic (dynamic add-sets at runtime) — plus (B) base set-counts from the program
   definition (`GET /api/v1/programs/{id}`) for exercises completely skipped. The script
   fetches all programs once per sync run via `GET /api/v1/programs` and matches them to
   workouts via the `program:` / `week:` / `dayName:` metadata in each workout's header.
   Freeform and imported workouts (no program metadata) fall back to target-only (part A).

All sync scripts are idempotent (safe to re-run / re-import overlapping data) and are
chained together in a single daily run by `/etc/cron.daily/powerlifting-dashboard-sync`
(outside this repo) — logs go to `logs/powerlifting_cron_job_exports.log`.

**Upsert pattern**: scripts writing single-row-per-date tables use `INSERT ... ON
CONFLICT(date) DO UPDATE SET col=excluded.col` for only the columns they have data for in
that run — never `INSERT OR REPLACE`, which would null out unrelated columns for a date if
a single fetch fails or you re-sync just one source/key. `training_log` is multi-row-per-date
(autoincrement `id` PK) so it instead deletes and reinserts all rows for a given
`workout_id` on each sync.

## Dashboard structure

`powerlifting_dashboard.py` is a thin entrypoint (page config, data loading, `st.tabs`
wiring) — it used to be a ~1,000-line monolith; the actual logic was split out by altitude:

- **`lib/constants.py`** — data paths, table names, exercise/check-in column lists, and the
  physique-calculator parameters (`HEIGHT_CM`/`WRIST_CM`/`ANKLE_CM`, `NUCKOLS_COEF`,
  `RTS_TABLE`).
- **`lib/calculations.py`** — pure functions, no Streamlit/DB: `estimate_e1rm` (RTS RPE table
  for 1-rep sets, Epley for 2–5 reps, 6+ reps ignored), `dots_score`, `trend_line`, the
  FFMI/Casey-Butt/Nuckols physique formulas (`ffm`, `ffmi_raw`, `ffmi_normalized`,
  `ffmi_rating`, `target_ffm_for_ffmi`, `casey_butt_max_ffm`, `nuckols_predicted`), the
  Tab 2 recovery features: `relative_e1rm` (each session's e1RM ÷ trailing mean of that same
  lift's previous sessions), `acwr` (acute:chronic training-load ratio), and
  `ridge_standardized_coefs` (standardized ridge-regression coefficients via `statsmodels`);
  and the Tab 5 sleep functions: `sleep_timing` (parses bed/wake clock times, noon-anchors
  bedtimes across midnight), `sleep_regularity_index` (SRI: epoch-concordance between
  consecutive nights, 0–100), `sleep_consistency_metrics` (full metric bundle: SRI, social
  jetlag, SD of mid-sleep/bedtime/wake/duration, mean efficiency, SD efficiency).
  Covered by `tests/test_calculations.py`.
- **`lib/data.py`** — `@st.cache_data`-decorated loaders (`load_training`, `load_checkin`,
  `load_weight`, `load_nutrition`, `load_latest_measurements`, `load_workout_completion`,
  `build_totals_df`). All read from `data/powerlifting.db` except `load_checkin` (reads
  `data/daily_checkin.csv` directly).
  The DB-backed loaders rename SQL columns back to the dashboard's original display-column
  names at load time (e.g. `exercise` → `"Exercise"`, `reps` → `"Completed Reps"`) so the
  `tabs/` modules are unaffected by the underlying storage. Because these are
  `@st.cache_data`-cached, after re-syncing the DB you must clear Streamlit's cache (rerun
  from the app menu, or restart) to see new data.
- **`tabs/`** — one module per tab, each exposing a single `render(...)` function called from
  `powerlifting_dashboard.py` inside its `with tabN:` block. Each independently gated on its
  data being available (falls back to an `st.info`/`st.stop()` placeholder rather than
  crashing):
  - **`tabs/progression.py` (Tab 1 — SBD Progression)**: e1RM over time per lift, DOTS score
    over time (via `build_totals_df()` — current-snapshot totals: for each day a lift was
    trained, take the most recent session's heaviest set per lift, sum, attach nearest
    bodyweight; higher of conventional/sumo deadlift used), and an all-time PR table.
  - **`tabs/recovery.py` (Tab 2 — Recovery Correlations)**: scatter + Spearman r + correlation
    heatmap + a standardized-ridge-regression chart, relating daily check-in metrics (plus
    engineered rolling-sleep, ACWR, and workout-completion predictors) to three selectable
    outcomes: relative e1RM, session quality, or workout completion %. Relative e1RM
    (`lib.calculations.relative_e1rm`) — each session's e1RM divided by that same lift's
    trailing baseline — is the primary outcome, not raw e1RM: raw e1RM is dominated by
    *which* lift was trained that day and drifts upward over a training cycle, both of which
    this normalizes away. "Workout completion %" is also selectable as an outcome (backed by
    `workout_completion` table — see Data flow §4), with a **self-correlation guard**: when
    it's chosen as the outcome it's excluded from the predictor list so it can't appear on
    both axes. The heatmap and ridge chart build `active_preds` per render (= `CORR_PREDICTORS`
    minus the selected outcome column) and use `{v: k for k, v in active_preds.items()}` for
    label lookup, keyed off the *correlation series' own index* — not insertion order — since
    bars are sorted by value.
  - **`tabs/weight_nutrition.py` (Tab 3 — Weight & Nutrition)**: bulk/cut tracking — dual view
    of bodyweight (rolling average + target-rate projection) and calorie intake (rolling
    average + estimated TDEE), merged on `date` (inner join, so only days with both weight
    and nutrition logged appear). Unlogged nutrition days are skipped, not interpolated, by
    design (irregular days aren't representative).
  - **`tabs/physique.py` (Tab 4 — Physique Calculators)**: FFMI, target body-composition
    planner, Casey Butt max muscular potential, gains-to-ceiling, Nuckols powerlifting
    efficiency, and projected lifts at target/max FFM — ported from
    `data/body_measurement_calculators.xlsx`. Height/wrist/ankle are hardcoded constants
    (not logged regularly); current weight, body fat %, and S/B/D 1RM (all-time best e1RM per
    lift) are pre-filled from the DB via `load_latest_measurements()` and `session_df` but
    left editable for what-if scenarios; target FFMI and target body fat % are plain user
    inputs (the latter also drives Calculator 3, collapsing the spreadsheet's duplicate
    input).
  - **`tabs/sleep.py` (Tab 5 — Sleep Consistency)**: three sections — (a) a nightly sleep
    schedule chart (bedtime + wake time as line/scatter, Y-axis labelled with clock times;
    bedtimes noon-anchored so 00:30 plots at 24.5 not 0.5, keeping the scale continuous
    across midnight); (b) a rolling consistency metric line chart (selectbox for metric,
    window slider, default 14 nights); (c) a Period A vs Period B comparison table (two
    date-range pickers, auto-defaults to equal-length windows before/after the latest
    intervention). All three charts draw annotated dashed vertical lines for any date in the
    check-in sheet where the `Intervention` column is non-blank. Gated on ≥ 7 nights with
    bed/wake times logged.
