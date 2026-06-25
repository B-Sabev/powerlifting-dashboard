## Changelog
_Append-only. One line per session, most recent on top._

- 2026-06-25: Added `dev/screenshot_dashboard.py` (Playwright driver that screenshots every
  dashboard tab and exits non-zero on console errors) and the `run-dashboard` project skill
  documenting how to launch/verify/teardown the app — this environment has no `chromium-cli`,
  so the prior approach re-derived a Python Playwright setup from scratch each session.
- 2026-06-25: Refactored `powerlifting_dashboard.py` (the ~1,000-line monolith) into `lib/`
  (constants, pure calculations, cached DB loaders) + `tabs/` (one `render()` module per tab),
  with the main file now a thin entrypoint. Behavior-identical — verified via a new pytest
  suite (`tests/test_calculations.py`) over the extracted pure math, plus Playwright
  screenshots of all four tabs against a live server showing no console errors. Also fixed
  `.gitignore` (`lib/` was blanket-ignored as a presumed venv subdirectory, swallowing the
  new package) and added `pytest` to `requirements.txt`.
- 2026-06-25: Added a 4th tab, "Physique Calculators" — FFMI, target body-composition
  planner, Casey Butt max muscular potential, gains-to-ceiling, Nuckols efficiency, and
  projected lifts at target/max FFM, ported from `data/body_measurement_calculators.xlsx`
  and cross-checked against the sheet recalculated in LibreOffice headless. Height/wrist/
  ankle hardcoded; weight/BF%/S-B-D 1RM pre-filled from the DB but editable; target FFMI
  and target body fat % are user inputs.
- 2026-06-24: Added `scripts/sync_liftosaur_training_log.py` — pulls SBD training sets from
  Liftosaur's `/history` API (parsing its text-blob workout summaries) into a new
  `training_log` table, idempotent via delete-then-reinsert per `workout_id`. Repointed
  `load_training()` at the DB; `liftosaur_training_log.csv` is no longer read by the
  dashboard (manual export retired in favor of automated sync, like body measurements).
- 2026-06-24: Fixed `daily_measurements.weight_kg` corrupt value for 2026-05-09 (5122 →
  84.5 kg) and added `statsmodels`/`scipy`/`patsy` to `requirements.txt` — Tab 2's
  trendline was uncaught-exception-crashing the entire app on every load, silently
  blocking Tab 3 (and anything after it in script execution) from ever rendering.
- 2026-06-24: Repointed Tab 1/Tab 3 bodyweight at `daily_measurements` (`load_weight()`) —
  was still reading a `weight_data.xlsx` that no longer exists, silently disabling the
  DOTS section and all of Tab 3.
- 2026-06-23: Daily check-in sync automated — `scripts/sync_checkin.py` pulls the check-in
  Google Sheet via its public CSV export link, added to the same cron job as the
  Cronometer sync. Full overwrite each run, no upsert/DB needed (sheet is already full
  history).

- 2026-06-23: Widened Cronometer anacron job from a 1-day to a 7-day export window —
  absorbs late-logged entries/retroactive edits/missed runs for free via the existing
  idempotent upsert.
- 2026-06-23: Documented nutrition DB disaster-recovery plan (full re-export +
  `sync_cronometer.py` re-sync) and flagged the unmitigated Cronometer-account-loss gap.

- 2026-06-23: Cronometer pipeline automated — anacron runs `cronometer-export` daily,
  overwriting `chronometer_daily_nutrition.csv` with the latest day, then calls
  `sync_cronometer.py` to upsert into `data/powerlifting.db`. Historical backfill done
  once manually before automation started.
- 2026-06-23: Added `scripts/sync_cronometer.py` (idempotent CSV → SQLite upsert keyed on
  date) and repointed Tab 3's nutrition loader at `data/powerlifting.db` instead of the
  raw CSV. Other tabs/sources untouched.

- 2026-06-22: Created PROJECT_LOG.md, retired TODO.txt.
- 2026-06-22: Fixed unsourced lean-mass bound (1000 → 5500 kcal/kg) in
  body-composition-analysis SKILL.md; flagged dashboard/skill TDEE logic mismatch.
