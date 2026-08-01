# Powerlifting Dashboard

[![CI](https://github.com/B-Sabev/powerlifting-dashboard/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/B-Sabev/powerlifting-dashboard/actions/workflows/ci.yml)

A personal [Streamlit](https://streamlit.io/) dashboard for tracking powerlifting progress,
recovery, and nutrition — fed by an automated pipeline that pulls daily from Cronometer, the
Liftosaur API, and a Google Sheet into a local SQLite warehouse and a CSV file.

I built it to answer questions my training apps couldn't on their own: *How is my strength trending up? Which recovery
factors — sleep, stress, soreness — actually predict a good session? Am I eating the right amount of food for my goal, dynamically adjusting for changes in metabolism? How are my sleep patterns and how my interventions for better sleep improve them?* Each tab is one of those questions.

**[▶ Live demo](https://powerlifting-dashboard-pzcygf9wzdwcceiryenr6b.streamlit.app/)** · runs on synthetic demo data so the public
app shows the full feature set without exposing my personal logs.

![SBD progression tab](docs/screenshot-progression.png)

## Features

- **SBD Progression** — estimated 1-rep max (e1RM) per lift over time, DOTS score, and an
  all-time PR table.
- **Recovery Correlations** — relates daily check-in metrics (sleep, mood, stress, soreness)
  plus engineered predictors (rolling sleep, acute:chronic workload ratio) to session
  performance, via Spearman correlation, a correlation heatmap, and standardized ridge
  regression to isolate each factor's unique contribution.
- **Weight & Nutrition** — bulk/cut tracking: rolling-average bodyweight with target-rate
  projection alongside calorie intake and estimated TDEE.
- **Physique Calculators** — FFMI, target body-composition planner, Casey Butt maximum
  muscular potential, and Nuckols powerlifting-efficiency estimates.
- **Sleep Consistency** — nightly sleep-schedule chart, rolling consistency metrics (sleep
  regularity index, social jetlag), and before/after comparison around logged interventions.

## Tech stack

Python · Streamlit · SQLite · pandas · NumPy · SciPy · statsmodels · Plotly · pytest

## Architecture

Four independent sync scripts feed the data layer; the dashboard only ever reads from it.
Three sources land in a local SQLite database, and the daily check-in sheet — which is
already the full history, append-only, in Google Sheets — is instead pulled straight into a
CSV file that fully overwrites on each sync. All syncs are idempotent and chained together in
a single daily cron job.

```
Cronometer (nutrition)       ─┐
Liftosaur API (training log) ─┼─► sync scripts ──► data/powerlifting.db ──┐
Liftosaur API (measurements) ─┘   (idempotent)     (SQLite warehouse)     │
                                                                          ├─► Streamlit dashboard
Google Sheet (daily check-in) ──► sync script ───► data/daily_checkin.csv ┘   (read-only)
                                  (full overwrite)
```

Liftosaur's API has no structured per-set endpoint — the only training-data endpoint,
`GET /history`, returns each workout as a compact text summary, so the training-log sync
script regex-parses it rather than reading a clean schema. That's a deliberate trade-off:
it's the only way to get the data at all, and since no natural per-set unique key exists to
support a column-level upsert, re-syncs stay idempotent via delete-then-reinsert keyed on
`workout_id` instead.

The codebase is split by altitude rather than kept as one monolith:

- `lib/calculations.py` — pure, framework-free functions (e1RM estimation, DOTS, the physique
  formulas, recovery features, sleep metrics), covered by a `pytest` suite.
- `lib/data.py` — cached data loaders.
- `tabs/` — one `render()` module per dashboard tab.
- `scripts/` — the standalone sync scripts.

See [`CLAUDE.md`](CLAUDE.md) for a full architecture reference.

## Running locally

```bash
# Clone and set up a project-local virtual environment
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# Run the dashboard (uses bundled demo data out of the box)
.venv/bin/streamlit run powerlifting_dashboard.py
```

The app falls back to synthetic demo data (`data/demo_*`) when no personal data is present,
so it runs immediately after cloning. To regenerate the demo data:

```bash
.venv/bin/python scripts/generate_demo_data.py
```

Run the tests with:

```bash
.venv/bin/python -m pytest
```

## Status

Actively maintained as both a personal tool and a portfolio project. Current ideas on the
backlog: an overreaching/undertraining detector and a go/no-go session predictor (logistic
regression on morning check-in features). See [`PROJECT_LOG.md`](PROJECT_LOG.md).

## License

[MIT](LICENSE)
