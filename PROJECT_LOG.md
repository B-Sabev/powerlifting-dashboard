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

- **DOTS over Wilks** — DOTS retained, Wilks dropped. Validated ~296 across 66 points.
- **Current-snapshot totals, not rolling peak** — for each day with ≥1 SBD lift trained,
  look backward to most recent session per lift, heaviest set, sum to total, attach
  nearest bodyweight. Higher of conv/sumo deadlift used. Chosen because it reflects
  genuine current strength including injury-related drops, rather than holding stale
  highs.
- **e1RM: RTS table for singles, Epley for 2–5 reps, ignore 6+** — locked, don't suggest
  alternatives without strong reason.
- **No file uploaders** — hardcoded `data/` paths + sidebar reload button. Keeps the
  single-file app simple and avoids handling arbitrary uploads.
- **Unlogged nutrition days skipped, not interpolated** — vacation/irregular days
  aren't representative of normal eating/expenditure patterns.
- **TDEE estimation** — fat-mass bound 7700 kcal/kg (standard, Wishnofsky), lean-mass
  bound 5500 kcal/kg (~2,500 kcal/lb to build muscle incl. synthesis inefficiency —
  RippedBody / common strength-nutrition heuristic; Rolling average pre-seeded from full history.
  See body-composition-analysis SKILL.md.
- **Single-file app, no multi-file refactor** unless explicitly asked.
- **Plotly only** — no matplotlib.

## Backlog / Ideas
_Unsorted brain dump. Either of us appends here; triage later. Check one off in place
when shipped, then move the line to Changelog._

- [ ] Sleep debt rolling metric
- [ ] Nutrition → next-day performance analysis (Cronometer CSV now available)
- [ ] Overreaching/undertraining detector
- [ ] Go/no-go session predictor — logistic regression on morning check-in features
- [ ] Combine body measurements data and Feelfit data to try to estimate how much lean mass am I gaining, add to Nutrition and Weight tab
- [ ] Add a README.md (currently missing — matters for hiring-manager portfolio review)
- [ ] Reconcile Tab 3's TDEE calc with the skill's logic — dashboard currently uses a
  single 7700 kcal/kg conversion both ways (bulk and cut), while the skill does a
  proper fat/lean dual-bound range. Worth deciding if Tab 3 should adopt the bound
  logic too, or if the simpler point-estimate is intentional.
- [ ] Figure out ways to reduce manual exporting and uploading -> Liftosaur premium, sync FeelFit to Liftosaur through Health connect, add body measurements in Liftosaur instead of a google sheet file, setup cron jobs to download the Cronometer and the daily check-in data

## Changelog
_Append-only. One line per session, most recent on top._

- 2026-06-22: Created PROJECT_LOG.md, retired TODO.txt.
- 2026-06-22: Fixed unsourced lean-mass bound (1000 → 5500 kcal/kg) in
  body-composition-analysis SKILL.md; flagged dashboard/skill TDEE logic mismatch.
