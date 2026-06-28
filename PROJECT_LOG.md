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
  body-composition-analysis SKILL.md. Tab 3 now uses both bounds to show a maintenance
  and target-intake range; constants live in `lib/constants.py` (`KCAL_PER_KG_FAT/LEAN`).
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
- **Tab 2 outcome: per-lift relative e1RM, not raw e1RM** — raw e1RM mixed lift-identity
  (deadlift e1RM dwarfs bench e1RM) into what was meant to be a recovery-vs-performance signal,
  and drifted upward over a training cycle; dividing each session by that lift's own trailing
  baseline (`relative_e1rm`) fixes both at once. `session_quality` kept as a secondary,
  selectable outcome (cheap to keep, useful as a sanity cross-check against the objective one).
- **Tab 2 stats: Spearman, ridge regression for unique contribution** —
  check-in inputs are ordinal Likert scales (small sample, ties), so rank correlation is the
  right tool; ridge (standardized, L2-penalized) regression was added on top because the
  predictors are inter-correlated (sleep moves with fatigue/mood) and univariate correlation
  can't separate "this factor matters" from "this factor's correlated with the one that
  matters" — n≈44 makes an unregularized multiple regression unstable, hence the L2 penalty.


## Backlog / Ideas
_Unsorted. Check off in place when shipped, then move the line to CHANGE_LOG._

- [ ] Overreaching/undertraining detector
- [ ] Go/no-go session predictor — logistic regression on morning check-in features; reuse
  Tab 2's `relative_e1rm` outcome and the standardized ridge factor weights as its starting
  point rather than refitting from scratch.
- [ ] Combine body-measurement + FeelFit data to estimate lean-mass gain; add to Weight &
  Nutrition tab
- [ ] Database backup strategy
- [ ] README.md

