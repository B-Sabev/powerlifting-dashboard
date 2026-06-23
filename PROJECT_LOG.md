# Project Log

Single source of truth for project state.
Read this first in any new chat instead of re-deriving context.

## Status
_Current state, kept short, overwritten not appended._

3 tabs implemented:
1. SBD progression - e1RM, DOTS, table with AT1RM PR - looks great for now
2. Recovery correlations - try to relate the daily check in with e1rm performance - currently it doesn't look useful, need to rethink the whole approach
3. Weight and Nutrition Tracking — bulk/cut tracking, dual-panel Plotly (weight+rolling avg+target
   projection / calories+avg+TDEE), unlogged days skipped not interpolated -> looks great for now

## Decisions
_Append-only. The "why" behind locked choices — don't re-litigate these._


- **Current-snapshot totals, not rolling peak** — for each day with ≥1 SBD lift trained, look backward to most recent session per lift, heaviest set, sum to total, attach nearest bodyweight. Higher of conv/sumo deadlift used. Chosen because it reflects genuine current strength. 
- **e1RM: RTS table for singles, Epley for 2–5 reps, ignore 6+** — locked, don't suggest alternatives without strong reason.
- **Unlogged nutrition days skipped, not interpolated** — vacation/irregular days aren't representative of normal eating/expenditure patterns.
- **TDEE estimation** — fat-mass bound 7700 kcal/kg, lean-mass bound 5500 kcal/kg (~2,500 kcal/lb to build muscle incl. synthesis inefficiency) Rolling average pre-seeded from full history. See body-composition-analysis SKILL.md.
- **Cronometer migrated to SQLite, other sources stay CSV/XLSX** — `data/powerlifting.db` is now the source of truth for nutrition (`cronometer_daily_nutrition` table, keyed on `date`, upserted by `scripts/sync_cronometer.py`). Chosen over CSV because this source is now written by an unattended daily job, and CSV has no clean idempotent-upsert story for incremental writes — a `UNIQUE(date)` constraint does. Liftosaur/FeelFit/check-in deliberately left as CSV/XLSX; no reason to migrate them until they're automated too. Long-term direction (not yet executed): one DB, one ETL layer, dashboard becomes a pure DB consumer — this is the first slice of that, not the whole migration.
- **Daily check-in: link-based CSV export, no DB, no auth** — `scripts/sync_checkin.py`
  downloads the check-in Google Sheet via its public CSV export URL (sheet shared as
  "Anyone with the link"). Chosen over a service account because GCP now gates project
  creation/API enablement behind attaching a billing account (payment method on file) even
  for free-tier usage — not worth the friction for a personal sheet. No upsert logic
  needed here unlike Cronometer: the sheet already holds full history, so every sync is a
  full, idempotent overwrite of `daily_checkin.csv`. Trade-off accepted: the sheet (sleep,
  mood, stress, notes) is reachable by anyone with the link, no login required.

## Backlog / Ideas
_Unsorted brain dump. Triage later. Check one off in place
when shipped, then move the line to Changelog.

- [ ] Sleep debt rolling metric
- [ ] Nutrition → next-day performance analysis (Cronometer CSV now available)
- [ ] Overreaching/undertraining detector
- [ ] Go/no-go session predictor — logistic regression on morning check-in features
- [ ] Combine body measurements data and Feelfit data to try to estimate how much lean mass am I gaining, add to Nutrition and Weight tab
- [ ] Add a README.md (currently missing — matters for hiring-manager portfolio review)
- [ ] Reconcile Tab 3's TDEE calc with the skill's logic — dashboard currently uses a single 7700 kcal/kg conversion both ways (bulk and cut), while the skill does a
  proper fat/lean dual-bound range. Worth deciding if Tab 3 should adopt the bound
  logic too, or if the simpler point-estimate is intentional.
- [ ] Figure out ways to reduce manual exporting and uploading -> Liftosaur premium, sync FeelFit to Liftosaur through Health connect, add body measurements in Liftosaur instead of a google sheet file
- [ ] implement the body measurements google sheet into the powerlifting dashboard



