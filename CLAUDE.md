# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A personal Streamlit dashboard (`powerlifting_dashboard.py`) for tracking powerlifting
progress, recovery, and nutrition/weight, fed by a set of standalone sync scripts that pull
from various sources (Cronometer, Liftosaur API, a Google Sheet) into a local SQLite
warehouse (`data/powerlifting.db`).

**Read `PROJECT_LOG.md` first in any new session** — it's the single source of truth for
current status, locked design decisions ("Decisions" section — don't re-litigate these
without a strong reason), and the backlog. `CHANGE_LOG.md` is the append-only one-line-per-session
history; add an entry there after non-trivial work, most recent on top.

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
```

There is no test suite, linter, or CI config in this repo.

## Data flow architecture

Three independent ingestion paths feed `data/powerlifting.db`, each with its own sync
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
3. **Daily check-in** (sleep, mood, stress, soreness, session quality, notes) — a Google
   Sheet shared as "Anyone with the link" → `scripts/sync_checkin.py` downloads it via the
   public CSV export URL and **fully overwrites** `data/daily_checkin.csv` (no DB, no
   upsert — the sheet already holds full history, append-only, in Google Sheets).
4. **Training log** — `data/liftosaur_training_log.csv` (Liftosaur training export) is read
   directly by the dashboard, not synced through a script.

All three sync scripts are idempotent (safe to re-run / re-import overlapping data) and are
chained together in a single daily run by `/etc/cron.daily/powerlifting-dashboard-sync`
(outside this repo) — logs go to `logs/powerlifting_cron_job_exports.log`.

**Upsert pattern**: every script that writes to SQLite uses `INSERT ... ON CONFLICT(date) DO
UPDATE SET col=excluded.col` for only the columns it has data for in that run — never
`INSERT OR REPLACE`, which would null out unrelated columns for a date if a single fetch
fails or you re-sync just one source/key.

## Dashboard structure (`powerlifting_dashboard.py`)

Single-file Streamlit app, three tabs, each independently gated on its data being available
(falls back to an `st.info`/`st.stop()` placeholder rather than crashing):

- **Tab 1 — SBD Progression**: e1RM over time per lift (RTS RPE table for 1-rep sets,
  Epley for 2–5 reps, 6+ reps ignored — see `estimate_e1rm()`), DOTS score over time
  (`build_totals_df()` — current-snapshot totals: for each day a lift was trained, take the
  most recent session's heaviest set per lift, sum, attach nearest bodyweight; higher of
  conventional/sumo deadlift used), and an all-time PR table.
- **Tab 2 — Recovery Correlations**: scatter + Pearson r + correlation heatmap relating
  daily check-in metrics to session quality / best e1RM on training days.
- **Tab 3 — Weight & Nutrition**: bulk/cut tracking — dual view of bodyweight (rolling
  average + target-rate projection) and calorie intake (rolling average + estimated TDEE),
  merged on `date` (inner join, so only days with both weight and nutrition logged appear).
  Unlogged nutrition days are skipped, not interpolated, by design (irregular days aren't
  representative).

Data loaders (`load_training`, `load_checkin`, `load_weight`, `load_nutrition`) are all
`@st.cache_data`-decorated; use the sidebar "Reload data" button (clears the cache) after
re-syncing data rather than restarting the app.

Note: `load_weight()` currently reads from a `weight_data.xlsx` path that no longer exists
in `data/` (weight/body-fat history has since migrated into the `daily_measurements` SQLite
table via the Liftosaur sync) — Tabs 1's DOTS section and Tab 3 are effectively disabled
until the dashboard is repointed at `daily_measurements` (tracked in `PROJECT_LOG.md`
backlog).
