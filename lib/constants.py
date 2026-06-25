"""Shared constants for the powerlifting dashboard: data paths, table names,
exercise/check-in column definitions, and the physique-calculator parameters.

These were previously module-level globals in powerlifting_dashboard.py.
"""

from pathlib import Path

# ── Data paths ───────────────────────────────────────────────────────────────
# This file lives in lib/, so the project root (and its data/ folder) is two
# levels up — resolve() before walking up so it works regardless of CWD.
DATA_DIR = Path(__file__).resolve().parent.parent / "data"
CHECKIN_PATH = DATA_DIR / "daily_checkin.csv"
# Training log, bodyweight, and nutrition all live in the SQLite warehouse, kept
# up to date by scripts/sync_liftosaur_training_log.py, scripts/sync_liftosaur_body_measurements.py,
# and scripts/sync_cronometer.py respectively (daily cron). Their raw exports
# are those scripts' inputs, not something the dashboard reads directly.
DB_PATH = DATA_DIR / "powerlifting.db"
TRAINING_TABLE = "training_log"
NUTRITION_TABLE = "cronometer_daily_nutrition"
MEASUREMENTS_TABLE = "daily_measurements"

# ── Exercise constants ───────────────────────────────────────────────────────
SBD_EXERCISES = ["Bench Press", "Squat", "Deadlift", "Sumo Deadlift"]
EXERCISE_COLORS = {
    "Squat":         "#4C9BE8",
    "Bench Press":   "#E8844C",
    "Deadlift":      "#4CE87A",
    "Sumo Deadlift": "#C44CE8",
}

# ── Daily check-in columns ───────────────────────────────────────────────────
CHECKIN_COLS = [
    "date", "bed_time", "awake_time", "sleep_hours", "sleep_quality", "nap_hours",
    "work_physical_load", "work_hours", "muscle_soreness", "joint_pain",
    "overall_fatigue", "mood", "life_stress", "training_motivation",
    "trained_today", "session_quality", "notes",
]
NUMERIC_CHECKIN = [
    "sleep_hours", "sleep_quality", "nap_hours", "work_physical_load",
    "work_hours", "muscle_soreness", "joint_pain", "overall_fatigue",
    "mood", "life_stress", "training_motivation", "session_quality",
]
CORR_PREDICTORS = {
    "Sleep Hours":         "sleep_hours",
    "Sleep Quality":       "sleep_quality",
    "Work Physical Load":  "work_physical_load",
    "Work Hours":          "work_hours",
    "Muscle Soreness":     "muscle_soreness",
    "Joint Pain":          "joint_pain",
    "Overall Fatigue":     "overall_fatigue",
    "Mood":                "mood",
    "Life Stress":         "life_stress",
    "Training Motivation": "training_motivation",
}

# ── Physique calculator constants (Tab 4) ────────────────────────────────────
# Hardcoded per data/body_measurement_calculators.xlsx — not logged regularly.
HEIGHT_CM = 175.0
WRIST_CM = 17.5
ANKLE_CM = 24.5
# lift -> (coefficient, intercept) for Nuckols' FFM-based elite-level prediction:
# predicted_lift = coef * (FFM / height_cm) + intercept
NUCKOLS_COEF = {
    "Squat":    (611.19, -10.43),
    "Bench":    (427.14, -14.75),
    "Deadlift": (410.2, 102.5),
    "Total":    (1448.53, 77.32),
}

# ── e1RM estimation ──────────────────────────────────────────────────────────
# RTS RPE table: (reps, RPE) -> fraction of 1RM
RTS_TABLE = {
    (1, 10.0): 1.000, (1, 9.5): 0.978, (1, 9.0): 0.955, (1, 8.5): 0.939,
    (1,  8.0): 0.922, (1, 7.5): 0.907, (1, 7.0): 0.892,
    (2, 10.0): 0.955, (2, 9.5): 0.939, (2, 9.0): 0.922, (2, 8.5): 0.907,
    (2,  8.0): 0.892, (2, 7.5): 0.878, (2, 7.0): 0.863,
    (3, 10.0): 0.922, (3, 9.5): 0.907, (3, 9.0): 0.892, (3, 8.5): 0.878,
    (3,  8.0): 0.863, (3, 7.5): 0.849, (3, 7.0): 0.835,
    (4, 10.0): 0.892, (4, 9.5): 0.878, (4, 9.0): 0.863, (4, 8.5): 0.849,
    (4,  8.0): 0.835, (4, 7.5): 0.820, (4, 7.0): 0.807,
}
