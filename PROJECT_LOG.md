# Project Log

Project *state* — locked decisions and what's next. Read first in any new session.
For **how the code/architecture works**, see `CLAUDE.md`; for chronological history,
`CHANGE_LOG.md`. Keep this file rationale-and-backlog only — don't re-describe mechanisms
that CLAUDE.md or a module docstring already cover.

## Decisions
_Locked choices, compressed to the "why." Don't re-litigate without a strong reason._

- **e1RM: RTS table for singles, Epley for 2–5 reps, ignore 6+** — locked; don't propose
  alternatives.
- **Current-snapshot totals, not rolling peak** (DOTS / `build_totals_df`) — reflects genuine
  *current* strength, not an all-time peak that may no longer hold.
- **Unlogged nutrition days skipped, not interpolated** — vacation/irregular days aren't
  representative of normal eating/expenditure.
- **TDEE bounds: 7700 kcal/kg fat, 5500 kcal/kg lean** (~2,500 kcal/lb to build muscle incl.
  synthesis inefficiency); rolling avg pre-seeded from full history. See
  body-composition-analysis SKILL.md. (Tab 3 still uses a single 7700 both ways — see backlog.)
- **Storage: SQLite for nutrition/measurements/training, CSV for check-in** — the first three
  are written by an unattended daily job, where a `UNIQUE(date)` upsert gives clean idempotent
  incremental writes; check-in stays a full-overwrite CSV because the Google Sheet already holds
  full history and a service account isn't worth it (GCP gates API enablement behind a billing
  account even for free tier). Trade-off: the check-in sheet is link-readable, no auth.
- **Training-log parse choices** — warmups and 0-rep/failed attempts dropped (matches the old
  CSV filter; recoverable via `--full`, the API is the source of truth); exercise names stored
  verbatim from Liftosaur (`"Deadlift"`, not "Conventional Deadlift") to match `SBD_EXERCISES`;
  delete-reinsert per `workout_id` because no natural per-set key exists.
- **Physique calculators: height/wrist/ankle hardcoded; weight/BF%/S-B-D pre-filled-but-editable**
  — the three circumferences aren't logged regularly (module constants); current weight/BF%/1RMs
  are DB-seeded but left editable for what-if scenarios; one target-BF% input drives both Calc 2
  and Calc 3 (the spreadsheet's duplicate added a field with no real use case).
- **Split monolith into `lib/` + `tabs/`, kept `st.tabs`** — rejected Streamlit native multipage
  (`pages/` + sidebar nav) because it would change the UX; this was a behavior-identical internal
  reorg. (`.gitignore`'s Python-template `lib/` rule had to be un-ignored to track the package.)
- **UI verification via `dev/screenshot_dashboard.py` + the `run-dashboard` skill, no baseline
  diffing** — script drives headless Chromium, screenshots every tab, exits non-zero on console
  errors → pass/fail without reading images back. Baseline diffing skipped as overkill (no CI);
  pytest + this console-error check is enough.

## Backlog / Ideas
_Unsorted. Check off in place when shipped, then move the line to CHANGE_LOG._

- [ ] **Recovery tab (Tab 2) rethink** — the check-in-vs-session-quality correlation approach
  isn't yielding anything useful; reconsider the whole framing.
- [ ] Sleep debt rolling metric
- [ ] Nutrition → next-day performance analysis
- [ ] Overreaching/undertraining detector
- [ ] Go/no-go session predictor — logistic regression on morning check-in features
- [ ] Combine body-measurement + FeelFit data to estimate lean-mass gain; add to Weight &
  Nutrition tab
- [ ] Reconcile Tab 3 TDEE with the skill's dual-bound logic (dashboard uses a single 7700 both
  ways — decide if the bound logic should apply or the point-estimate is intentional)
- [ ] Database backup strategy
- [ ] Widen training-log sync beyond SBD (table/parser already handle arbitrary names — just
  relax the `SBD_EXERCISES` filter)
- [ ] README.md (missing — matters for portfolio review)
- [ ] Publish the app online
