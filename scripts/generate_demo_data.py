#!/usr/bin/env python3
"""Generate synthetic demo data for the public Streamlit Cloud deployment.

Writes:
  data/demo_powerlifting.db   – SQLite warehouse (training, measurements, nutrition)
  data/demo_daily_checkin.csv – daily check-in file (same 18-column format as real one)

Run from the project root:
  .venv/bin/python scripts/generate_demo_data.py

Idempotent: overwrites any existing demo files on each run.

The schema exactly mirrors the real DB so all loaders in lib/data.py work unchanged.
The app (lib/constants.py) picks real files when they exist (local dev) and falls back
to these demo files when they're absent (Streamlit Cloud).
"""

import csv
import math
import random
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path

# ── Reproducible randomness ──────────────────────────────────────────────────
SEED = 42
rng = random.Random(SEED)

# ── Paths ────────────────────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
DEMO_DB = DATA_DIR / "demo_powerlifting.db"
DEMO_CHECKIN = DATA_DIR / "demo_daily_checkin.csv"

# ── Date range ───────────────────────────────────────────────────────────────
START_DATE = date(2025, 9, 1)
END_DATE   = date(2026, 6, 20)  # just before "today" on cloud

DAYS = (END_DATE - START_DATE).days + 1
ALL_DATES = [START_DATE + timedelta(days=i) for i in range(DAYS)]

# ── Athlete profile ──────────────────────────────────────────────────────────
# 85 kg → slow bulk to 88 kg over ~42 weeks, then a short cut back to 86 kg.
BW_START = 85.0
BW_PEAK  = 88.0
BW_END   = 86.5
PEAK_WEEK = 32   # week number (of ~42) where bulk peaks before cut

def bodyweight(d: date) -> float:
    """Simulated bodyweight with realistic noise (weekly fluctuation ±1 kg)."""
    week = (d - START_DATE).days / 7
    total_weeks = DAYS / 7
    # piecewise linear: bulk to PEAK_WEEK, then slight cut
    if week <= PEAK_WEEK:
        bw = BW_START + (BW_PEAK - BW_START) * (week / PEAK_WEEK)
    else:
        remaining = total_weeks - PEAK_WEEK
        bw = BW_PEAK + (BW_END - BW_PEAK) * ((week - PEAK_WEEK) / remaining)
    # weekly water-weight oscillation + daily noise
    noise = rng.gauss(0, 0.35) + 0.3 * math.sin(week * 2 * math.pi)
    return round(max(80.0, bw + noise), 1)

def body_fat_pct(d: date) -> float:
    """Body fat measured every ~14 days, slight upward trend during bulk."""
    week = (d - START_DATE).days / 7
    total_weeks = DAYS / 7
    bf_start, bf_peak, bf_end = 19.5, 22.0, 20.5
    if week <= PEAK_WEEK:
        bf = bf_start + (bf_peak - bf_start) * (week / PEAK_WEEK)
    else:
        remaining = total_weeks - PEAK_WEEK
        bf = bf_peak + (bf_end - bf_peak) * ((week - PEAK_WEEK) / remaining)
    return round(bf + rng.gauss(0, 0.3), 1)


# ── Training schedule ────────────────────────────────────────────────────────
# 4-day upper/lower:  Mon=Squat+Bench, Wed=DL, Fri=Bench+Sumo, Sat=Squat
# Monday=0, Tuesday=1 ... Sunday=6
TRAINING_DAY_LIFTS: dict[int, list[str]] = {
    0: ["Squat", "Bench Press"],          # Monday
    2: ["Deadlift"],                       # Wednesday
    4: ["Bench Press", "Sumo Deadlift"],  # Friday
    5: ["Squat"],                          # Saturday
}

# Progression model: e1RM target for each lift at week 0 and week total_weeks.
# We simulate 3 blocks (accumulation → intensification → peaking) then repeat.
E1RM_START = {"Squat": 130.0, "Bench Press": 95.0, "Deadlift": 162.5, "Sumo Deadlift": 157.5}
E1RM_END   = {"Squat": 157.5, "Bench Press": 112.5, "Deadlift": 197.5, "Sumo Deadlift": 185.0}

BLOCK_WEEKS = 6   # weeks per block phase
# Per phase: (reps_to_use, rpe_typical, sets_per_workout)
PHASES = [
    (4, 7.5, 4),  # accumulation
    (3, 8.0, 3),  # intensification
    (2, 8.5, 3),  # high intensification
    (1, 8.5, 3),  # near-peaking
    (1, 9.0, 2),  # peaking
]

def target_e1rm(lift: str, d: date) -> float:
    """Linearly interpolated e1RM target for a given lift on a given date."""
    progress = (d - START_DATE).days / max(DAYS - 1, 1)
    e1rm = E1RM_START[lift] + (E1RM_END[lift] - E1RM_START[lift]) * progress
    return e1rm

def current_phase(d: date) -> tuple[int, float, int]:
    """Return (reps, rpe, sets) for the training phase on date d."""
    week = (d - START_DATE).days // 7
    phase_idx = (week // BLOCK_WEEKS) % len(PHASES)
    return PHASES[phase_idx]

def weight_for_set(lift: str, d: date, reps: int, rpe: float) -> float:
    """Back-calculate the working weight from target e1RM and set scheme."""
    e1rm = target_e1rm(lift, d)
    if reps == 1:
        # weight = e1rm * RTS_fraction
        rts = {
            9.0: 0.955, 8.5: 0.939, 8.0: 0.922, 7.5: 0.907, 7.0: 0.892
        }.get(round(rpe * 2) / 2, 0.922)
        raw = e1rm * rts
    else:
        # Epley inverse: e1rm = weight * (1 + reps/30)  →  weight = e1rm / (1 + reps/30)
        raw = e1rm / (1 + reps / 30)
    # Round to nearest 2.5 kg
    rounded = round(raw / 2.5) * 2.5
    # Add day-to-day noise (±2.5 kg probability)
    noise_choices = [-2.5, 0.0, 0.0, 0.0, 2.5]
    return max(20.0, rounded + rng.choice(noise_choices))

def build_training_log() -> list[dict]:
    """Generate all training-log rows (SBD working sets)."""
    rows = []
    workout_counter = 0

    for d in ALL_DATES:
        weekday = d.weekday()
        if weekday not in TRAINING_DAY_LIFTS:
            continue

        # Occasional deload / missed session (~8% chance)
        if rng.random() < 0.08:
            continue

        lifts = TRAINING_DAY_LIFTS[weekday]
        reps, rpe, sets = current_phase(d)

        # Unique workout_id: millisecond-like large int
        workout_id = int(datetime(d.year, d.month, d.day, 10, 0).timestamp() * 1000) + workout_counter
        workout_counter += 1

        for lift in lifts:
            wt = weight_for_set(lift, d, reps, rpe)
            for s in range(sets):
                # Last set slightly heavier/harder in intensification
                set_rpe = rpe + (0.5 if s == sets - 1 and sets > 1 else 0.0)
                set_rpe = min(10.0, set_rpe)
                set_wt = wt + (2.5 if s == sets - 1 and sets > 2 else 0.0)
                rows.append({
                    "workout_id": workout_id,
                    "date":       d.isoformat(),
                    "exercise":   lift,
                    "reps":       reps,
                    "weight_kg":  set_wt,
                    "rpe":        set_rpe,
                })

    return rows

def build_workout_completion(training_rows: list[dict]) -> list[dict]:
    """One row per workout: planned ≈ completed (occasional partial sessions)."""
    seen: dict[int, dict] = {}
    for r in training_rows:
        wid = r["workout_id"]
        if wid not in seen:
            seen[wid] = {"workout_id": wid, "date": r["date"], "completed_sets": 0, "planned_sets": 0}
        seen[wid]["completed_sets"] += 1

    rows = []
    for wid, v in seen.items():
        completed = v["completed_sets"]
        # Planned is usually same; occasionally 1-2 extra sets were planned but skipped
        extra_planned = rng.choices([0, 1, 2], weights=[70, 20, 10])[0]
        planned = completed + extra_planned
        rows.append({
            "workout_id":    wid,
            "date":          v["date"],
            "planned_sets":  planned,
            "completed_sets": completed,
        })

    return rows


# ── Body measurements ────────────────────────────────────────────────────────

def build_measurements() -> list[dict]:
    """Daily bodyweight; body-fat and tape every ~14 days."""
    rows = []
    bf_dates = set()
    # body fat every ~14 days
    d = START_DATE
    while d <= END_DATE:
        bf_dates.add(d)
        d += timedelta(days=14 + rng.randint(-2, 2))

    for d in ALL_DATES:
        # Weight logged every day except ~10% of days
        if rng.random() > 0.10:
            bw = bodyweight(d)
        else:
            bw = None

        bf = body_fat_pct(d) if d in bf_dates else None

        # Tape measurements: every ~4 weeks from start
        week_num = (d - START_DATE).days // 7
        if week_num % 4 == 0 and d.weekday() == 0 and rng.random() > 0.3:
            # waist and chest only; others None
            waist = round(85.0 + rng.gauss(0, 1.5), 1)
            chest = round(105.0 + rng.gauss(0, 1.5), 1)
        else:
            waist = chest = None

        if bw is None and bf is None and waist is None:
            continue

        rows.append({
            "date":               d.isoformat(),
            "weight_kg":          bw,
            "body_fat_percent":   bf,
            "calf_left_cm":       None,
            "thigh_right_cm":     None,
            "shoulders_cm":       None,
            "bicep_left_cm":      None,
            "neck_cm":            None,
            "hips_cm":            None,
            "calf_right_cm":      None,
            "waist_cm":           waist,
            "thigh_left_cm":      None,
            "chest_cm":           chest,
            "forearm_left_cm":    None,
            "bicep_right_cm":     None,
            "forearm_right_cm":   None,
        })
    return rows


# ── Nutrition ────────────────────────────────────────────────────────────────

def build_nutrition() -> list[dict]:
    """Daily macro summary — only energy/protein/carbs/fat needed by the dashboard."""
    rows = []
    for d in ALL_DATES:
        # Log nutrition on ~80% of days (realistic for a tracker)
        if rng.random() > 0.80:
            continue
        is_training_day = d.weekday() in TRAINING_DAY_LIFTS
        # Calories: ~2900 on training days, ~2500 on rest days; slight bulk increase over time
        week_progress = (d - START_DATE).days / max(DAYS - 1, 1)
        base_kcal = (2900 if is_training_day else 2500) + week_progress * 200
        kcal = round(base_kcal + rng.gauss(0, 150), 0)
        protein_g = round(rng.gauss(195, 20), 1)
        fat_g     = round(rng.gauss(80, 10), 1)
        carbs_g   = round(max(50, (kcal - protein_g * 4 - fat_g * 9) / 4), 1)

        rows.append({
            "date":        d.isoformat(),
            "energy_kcal": kcal,
            "alcohol_g":   0.0,
            "caffeine_mg": round(rng.gauss(150, 40), 1),
            "oxalate_mg":  None,
            "phytate_mg":  None,
            "water_g":     round(rng.gauss(600, 100), 0),
            "b1_thiamine_mg":       round(rng.gauss(1.8, 0.3), 2),
            "b2_riboflavin_mg":     round(rng.gauss(2.5, 0.4), 2),
            "b3_niacin_mg":         round(rng.gauss(35, 5), 1),
            "b5_pantothenic_acid_mg": round(rng.gauss(8, 1.5), 2),
            "b6_pyridoxine_mg":     round(rng.gauss(3, 0.5), 2),
            "b12_cobalamin_ug":     round(rng.gauss(4, 1), 2),
            "folate_ug":            round(rng.gauss(500, 80), 1),
            "vitamin_c_mg":         round(rng.gauss(100, 30), 1),
            "vitamin_d_iu":         round(rng.gauss(2000, 500), 0),
            "vitamin_e_mg":         round(rng.gauss(12, 2), 1),
            "vitamin_k_ug":         round(rng.gauss(150, 30), 1),
            "calcium_mg":           round(rng.gauss(1200, 200), 0),
            "copper_mg":            round(rng.gauss(1.5, 0.3), 2),
            "iron_mg":              round(rng.gauss(18, 3), 1),
            "magnesium_mg":         round(rng.gauss(400, 60), 0),
            "manganese_mg":         round(rng.gauss(3, 0.5), 2),
            "phosphorus_mg":        round(rng.gauss(1500, 200), 0),
            "potassium_mg":         round(rng.gauss(4000, 500), 0),
            "selenium_ug":          round(rng.gauss(80, 15), 1),
            "sodium_mg":            round(rng.gauss(2500, 400), 0),
            "zinc_mg":              round(rng.gauss(14, 2), 1),
            "net_carbs_g":          round(carbs_g * 0.92, 1),
            "carbs_g":              carbs_g,
            "fiber_g":              round(rng.gauss(28, 5), 1),
            "insoluble_fiber_g":    round(rng.gauss(8, 2), 1),
            "soluble_fiber_g":      round(rng.gauss(5, 1), 1),
            "starch_g":             round(rng.gauss(120, 20), 1),
            "sugars_g":             round(rng.gauss(60, 15), 1),
            "fat_g":                fat_g,
            "cholesterol_mg":       round(rng.gauss(250, 50), 0),
            "monounsaturated_g":    round(fat_g * 0.35 + rng.gauss(0, 2), 1),
            "polyunsaturated_g":    round(fat_g * 0.25 + rng.gauss(0, 2), 1),
            "saturated_g":          round(fat_g * 0.30 + rng.gauss(0, 2), 1),
            "trans_fats_g":         0.0,
            "omega_3_g":            round(rng.gauss(1.5, 0.4), 2),
            "ala_g":                round(rng.gauss(0.8, 0.2), 2),
            "dha_g":                round(rng.gauss(0.4, 0.1), 2),
            "epa_g":                round(rng.gauss(0.3, 0.1), 2),
            "omega_6_g":            round(rng.gauss(12, 2), 1),
            "aa_g":                 round(rng.gauss(0.2, 0.05), 3),
            "la_g":                 round(rng.gauss(10, 2), 1),
            "cystine_g":            round(protein_g * 0.012, 2),
            "histidine_g":          round(protein_g * 0.027, 2),
            "isoleucine_g":         round(protein_g * 0.047, 2),
            "leucine_g":            round(protein_g * 0.082, 2),
            "lysine_g":             round(protein_g * 0.07, 2),
            "methionine_g":         round(protein_g * 0.025, 2),
            "phenylalanine_g":      round(protein_g * 0.044, 2),
            "protein_g":            protein_g,
            "threonine_g":          round(protein_g * 0.042, 2),
            "tryptophan_g":         round(protein_g * 0.011, 2),
            "tyrosine_g":           round(protein_g * 0.035, 2),
            "valine_g":             round(protein_g * 0.053, 2),
            "completed":            1,
        })
    return rows


# ── Daily check-in ───────────────────────────────────────────────────────────

# Intervention: a simple habit change logged at week 16 and week 30
INTERVENTIONS = {
    START_DATE + timedelta(weeks=16): "Pre-sleep routine started",
    START_DATE + timedelta(weeks=30): "Changed training time to mornings",
}

def build_checkin() -> list[list]:
    """Return rows (excluding the two header rows) for the check-in CSV."""
    # Baseline sleep changes: after intervention 1 (week 16) sleep improves slightly
    # After intervention 2 (week 30) wake time becomes more regular.
    rows = []
    for d in ALL_DATES:
        # Log ~90% of days (miss Sundays occasionally + ~5% other days)
        if d.weekday() == 6 and rng.random() < 0.4:
            continue
        if rng.random() < 0.05:
            continue

        week = (d - START_DATE).days / 7

        # Sleep: baseline 7h, improving after intervention 1
        if week < 16:
            sleep_mean = 7.0
            bed_hour_mean = 23.5   # 11:30 PM
            wake_hour_mean = 7.0
            quality_mean = 6.5
        elif week < 30:
            sleep_mean = 7.4
            bed_hour_mean = 23.0
            wake_hour_mean = 6.8
            quality_mean = 7.2
        else:
            # Morning training → earlier wake, earlier bed
            sleep_mean = 7.3
            bed_hour_mean = 22.5
            wake_hour_mean = 6.0
            quality_mean = 7.0

        sleep_hours = round(max(4.5, min(9.5, rng.gauss(sleep_mean, 0.5))), 1)
        sleep_quality = max(1, min(10, round(rng.gauss(quality_mean, 1.5))))

        # Bed time: stochastic around mean, with more variability before intervention
        variability = 0.75 if week < 16 else 0.45
        bed_hour = rng.gauss(bed_hour_mean, variability)
        # Wrap to 0-24 range for display; times like 23.5 = 23:30, 24.3 = 00:18
        bed_h = int(bed_hour) % 24
        bed_m = int((bed_hour % 1) * 60)
        bed_time = f"{bed_h:02d}:{bed_m:02d}"

        wake_hour = bed_hour + sleep_hours + rng.gauss(0, 0.15)
        wake_hour = wake_hour % 24
        wake_h = int(wake_hour) % 24
        wake_m = int((wake_hour % 1) * 60)
        awake_time = f"{wake_h:02d}:{wake_m:02d}"

        nap_hours = 0 if rng.random() > 0.15 else round(rng.uniform(0.3, 1.0), 1)

        is_training_day = d.weekday() in TRAINING_DAY_LIFTS and rng.random() > 0.08
        trained_today = "Yes" if is_training_day else "No"

        # Fatigue/recovery correlate with training load
        base_fatigue = rng.gauss(4.5, 1.5)
        if is_training_day:
            base_fatigue += 1.0
        soreness = max(1, min(10, round(base_fatigue + rng.gauss(0, 1))))
        fatigue   = max(1, min(10, round(base_fatigue + rng.gauss(0, 1))))
        joint_pain = max(1, min(5,  round(rng.gauss(2.0, 0.8))))
        mood = max(1, min(10, round(rng.gauss(7.0, 1.5))))
        life_stress = max(1, min(5, round(rng.gauss(2.5, 0.9))))
        motivation = max(1, min(10, round(rng.gauss(7.5, 1.5))))

        if d.weekday() >= 5:   # Saturday=5, Sunday=6
            work_hours = 0
            work_load = 1
        else:
            work_hours = 8
            work_load = max(1, min(10, round(rng.gauss(5.0, 1.2))))  
        session_quality = ""
        if is_training_day:
            session_quality = max(1, min(10, round(rng.gauss(7.5, 1.5))))

        # Generic notes — no real personal text
        generic_notes = [
            "", "", "", "", "",   # mostly blank
            "Good session", "Felt strong", "Tired today", "Solid training",
            "Recovery day", "Long work day", "Good sleep", "Felt sluggish",
        ]
        note = rng.choice(generic_notes)

        intervention = INTERVENTIONS.get(d, "")

        rows.append([
            d.strftime("%d/%m/%Y"),  # Date
            bed_time,                # Bed Time
            awake_time,              # Awake Time
            sleep_hours,             # Sleep Hours
            sleep_quality,           # Sleep Quality
            nap_hours if nap_hours else "",  # Nap Hours
            work_load,               # Work Physical Load
            work_hours,              # Work Hours
            soreness,                # Muscle Soreness
            joint_pain,              # Joint Pain/Tightness
            fatigue,                 # Overall Fatigue
            mood,                    # Mood
            life_stress,             # Life Stress
            motivation,              # Training Motivation
            trained_today,           # Trained Today?
            session_quality,         # Session Quality
            note,                    # Notes
            intervention,            # Intervention
        ])
    return rows


# ── Database writer ──────────────────────────────────────────────────────────

def write_db(training: list[dict], completion: list[dict],
             measurements: list[dict], nutrition: list[dict]) -> None:
    DEMO_DB.unlink(missing_ok=True)
    conn = sqlite3.connect(DEMO_DB)
    c = conn.cursor()

    # training_log
    c.execute("""
        CREATE TABLE training_log (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            workout_id INTEGER NOT NULL,
            date       TEXT    NOT NULL,
            exercise   TEXT    NOT NULL,
            reps       INTEGER NOT NULL,
            weight_kg  REAL    NOT NULL,
            rpe        REAL
        )
    """)
    c.executemany(
        "INSERT INTO training_log (workout_id,date,exercise,reps,weight_kg,rpe) "
        "VALUES (:workout_id,:date,:exercise,:reps,:weight_kg,:rpe)",
        training,
    )

    # workout_completion
    c.execute("""
        CREATE TABLE workout_completion (
            workout_id    INTEGER PRIMARY KEY,
            date          TEXT    NOT NULL,
            planned_sets  INTEGER NOT NULL,
            completed_sets INTEGER NOT NULL
        )
    """)
    c.executemany(
        "INSERT INTO workout_completion (workout_id,date,planned_sets,completed_sets) "
        "VALUES (:workout_id,:date,:planned_sets,:completed_sets)",
        completion,
    )

    # daily_measurements  (all columns that exist in real DB)
    cols = [
        "date TEXT PRIMARY KEY",
        "weight_kg REAL", "body_fat_percent REAL",
        "calf_left_cm REAL", "thigh_right_cm REAL", "shoulders_cm REAL",
        "bicep_left_cm REAL", "neck_cm REAL", "hips_cm REAL",
        "calf_right_cm REAL", "waist_cm REAL", "thigh_left_cm REAL",
        "chest_cm REAL", "forearm_left_cm REAL", "bicep_right_cm REAL",
        "forearm_right_cm REAL",
    ]
    c.execute(f"CREATE TABLE daily_measurements ({', '.join(cols)})")
    meas_cols = [col.split()[0] for col in cols]
    placeholders = ", ".join(f":{col}" for col in meas_cols)
    c.executemany(
        f"INSERT INTO daily_measurements ({', '.join(meas_cols)}) VALUES ({placeholders})",
        measurements,
    )

    # cronometer_daily_nutrition (all columns that exist in real DB)
    nutr_col_defs = """
        date TEXT PRIMARY KEY, energy_kcal REAL, alcohol_g REAL, caffeine_mg REAL,
        oxalate_mg REAL, phytate_mg REAL, water_g REAL, b1_thiamine_mg REAL,
        b2_riboflavin_mg REAL, b3_niacin_mg REAL, b5_pantothenic_acid_mg REAL,
        b6_pyridoxine_mg REAL, b12_cobalamin_ug REAL, folate_ug REAL,
        vitamin_c_mg REAL, vitamin_d_iu REAL, vitamin_e_mg REAL, vitamin_k_ug REAL,
        calcium_mg REAL, copper_mg REAL, iron_mg REAL, magnesium_mg REAL,
        manganese_mg REAL, phosphorus_mg REAL, potassium_mg REAL, selenium_ug REAL,
        sodium_mg REAL, zinc_mg REAL, net_carbs_g REAL, carbs_g REAL, fiber_g REAL,
        insoluble_fiber_g REAL, soluble_fiber_g REAL, starch_g REAL, sugars_g REAL,
        fat_g REAL, cholesterol_mg REAL, monounsaturated_g REAL, polyunsaturated_g REAL,
        saturated_g REAL, trans_fats_g REAL, omega_3_g REAL, ala_g REAL, dha_g REAL,
        epa_g REAL, omega_6_g REAL, aa_g REAL, la_g REAL, cystine_g REAL,
        histidine_g REAL, isoleucine_g REAL, leucine_g REAL, lysine_g REAL,
        methionine_g REAL, phenylalanine_g REAL, protein_g REAL, threonine_g REAL,
        tryptophan_g REAL, tyrosine_g REAL, valine_g REAL, completed INTEGER
    """
    c.execute(f"CREATE TABLE cronometer_daily_nutrition ({nutr_col_defs})")
    nutr_keys = [cd.strip().split()[0] for cd in nutr_col_defs.split(",")]
    n_ph = ", ".join(f":{k}" for k in nutr_keys)
    c.executemany(
        f"INSERT INTO cronometer_daily_nutrition ({', '.join(nutr_keys)}) VALUES ({n_ph})",
        nutrition,
    )

    # sync_state (placeholder so the app doesn't error if it queries it)
    c.execute("CREATE TABLE sync_state (key TEXT PRIMARY KEY, last_timestamp INTEGER)")
    c.execute("INSERT INTO sync_state VALUES ('weight', 0), ('training_log', 0)")

    conn.commit()
    conn.close()


# ── CSV writer ───────────────────────────────────────────────────────────────

# Exact two-row header matching the real Google Sheet export format
# (load_checkin uses skiprows=2, so rows 1-2 are skipped, row 3+ is data)
HEADER_ROW1 = [
    "Date", "", "", "Sleep", "", "", "Work", "", "Recovery", "", "", "Wellbeing", "",
    "Training", "", "", "Notes", "",
]
HEADER_ROW2 = [
    "Date", "Bed Time", "Awake Time", "Sleep\nHours", "Sleep\nQuality", "Nap Hours",
    "Work Physical\nLoad", "Work\nHours", "Muscle\nSoreness", "Joint Pain /\nTightness",
    "Overall\nFatigue", "Mood", "Life\nStress", "Training\nMotivation",
    "Trained\nToday?", "Session\nQuality", "Notes", "Intervention",
]

def write_checkin(data_rows: list[list]) -> None:
    with DEMO_CHECKIN.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(HEADER_ROW1)
        w.writerow(HEADER_ROW2)
        w.writerows(data_rows)


# ── Main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    DATA_DIR.mkdir(exist_ok=True)

    print("Generating training log …")
    training_rows = build_training_log()
    completion_rows = build_workout_completion(training_rows)
    print(f"  {len(training_rows)} training sets, {len(completion_rows)} workouts")

    print("Generating measurements …")
    measurement_rows = build_measurements()
    print(f"  {len(measurement_rows)} measurement rows")

    print("Generating nutrition …")
    nutrition_rows = build_nutrition()
    print(f"  {len(nutrition_rows)} nutrition rows")

    print(f"Writing {DEMO_DB} …")
    write_db(training_rows, completion_rows, measurement_rows, nutrition_rows)

    print("Generating check-in …")
    checkin_rows = build_checkin()
    print(f"  {len(checkin_rows)} check-in rows")
    print(f"Writing {DEMO_CHECKIN} …")
    write_checkin(checkin_rows)

    print("\nDone.")
    print(f"  Demo DB:      {DEMO_DB}")
    print(f"  Demo checkin: {DEMO_CHECKIN}")
    print("\nTest locally by temporarily renaming data/powerlifting.db and data/daily_checkin.csv")
    print("then running: .venv/bin/streamlit run powerlifting_dashboard.py")


if __name__ == "__main__":
    main()
