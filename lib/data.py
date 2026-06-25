"""Cached data loaders for the powerlifting dashboard.

All functions here are @st.cache_data-decorated and read from data/powerlifting.db
(except load_checkin, which reads data/daily_checkin.csv directly). They rename
SQL columns back to the dashboard's original display-column names at load time
(e.g. exercise -> "Exercise", reps -> "Completed Reps") so the tabs/ modules are
unaffected by the underlying storage.
"""

import sqlite3
from pathlib import Path

import pandas as pd
import streamlit as st

from lib.calculations import estimate_e1rm, dots_score
from lib.constants import (
    CHECKIN_COLS,
    MEASUREMENTS_TABLE,
    NUMERIC_CHECKIN,
    NUTRITION_TABLE,
    TRAINING_TABLE,
)


@st.cache_data
def load_training(db_path: Path) -> pd.DataFrame:
    """Load SBD working sets from the SQLite warehouse (synced via scripts/sync_liftosaur_training_log.py)."""
    conn = sqlite3.connect(db_path)
    try:
        df = pd.read_sql_query(
            f"SELECT date, exercise, reps, weight_kg, rpe FROM {TRAINING_TABLE}",
            conn,
        )
    finally:
        conn.close()

    df = df.rename(columns={"exercise": "Exercise", "reps": "Completed Reps", "rpe": "Completed RPE"})
    df["date"] = pd.to_datetime(df["date"]).dt.normalize()

    # e1RM: RTS for singles, Epley for 2–4 reps, None for 5+
    df["e1rm"] = df.apply(estimate_e1rm, axis=1)

    # Best set per session (max ignores None/NaN automatically)
    session = (
        df.groupby(["date", "Exercise"])["e1rm"]
        .max()
        .reset_index()
        .dropna(subset=["e1rm"])
        .sort_values("date")
    )
    return session, df[["date", "Exercise", "weight_kg", "Completed Reps"]].copy()


@st.cache_data
def load_checkin(path: Path) -> pd.DataFrame:
    checkin = pd.read_csv(
        path,
        skiprows=2, header=None, names=CHECKIN_COLS,
    )
    checkin["date"] = pd.to_datetime(checkin["date"], format="%d/%m/%Y", errors="coerce")
    checkin = checkin[checkin["date"].notna()].copy()
    checkin = checkin[checkin["sleep_hours"].notna() | checkin["trained_today"].notna()]
    for c in NUMERIC_CHECKIN:
        checkin[c] = pd.to_numeric(checkin[c], errors="coerce")
    checkin["trained_today"] = checkin["trained_today"].map({"Yes": True, "No": False})
    checkin["date"] = checkin["date"].dt.normalize()
    return checkin


@st.cache_data
def load_weight(db_path: Path) -> pd.DataFrame:
    """Load daily bodyweight from the SQLite warehouse (synced via scripts/sync_liftosaur_body_measurements.py)."""
    conn = sqlite3.connect(db_path)
    try:
        weight = pd.read_sql_query(
            f"SELECT date, weight_kg FROM {MEASUREMENTS_TABLE} WHERE weight_kg IS NOT NULL",
            conn,
        )
    finally:
        conn.close()

    weight["date"] = pd.to_datetime(weight["date"]).dt.normalize()
    weight = weight.rename(columns={"weight_kg": "bodyweight"}).sort_values("date")
    return weight


@st.cache_data
def load_nutrition(db_path: Path) -> pd.DataFrame:
    """Load daily energy/macro totals from the SQLite warehouse (synced via scripts/sync_cronometer.py)."""
    conn = sqlite3.connect(db_path)
    try:
        df = pd.read_sql_query(
            f"SELECT date, energy_kcal, protein_g, carbs_g, fat_g FROM {NUTRITION_TABLE}",
            conn,
        )
    finally:
        conn.close()

    df["date"] = pd.to_datetime(df["date"]).dt.normalize()
    # Renamed back to the dashboard's existing display-column convention so
    # everything downstream (Tab 3) is untouched by the storage swap.
    df = df.rename(columns={
        "energy_kcal": "Energy (kcal)",
        "protein_g": "Protein (g)",
        "carbs_g": "Carbs (g)",
        "fat_g": "Fat (g)",
    })
    return df


@st.cache_data
def load_latest_measurements(db_path: Path):
    """Most recent logged weight and body-fat % (independently latest, since
    they may not be logged on the same day). Used to pre-fill Tab 4 inputs."""
    conn = sqlite3.connect(db_path)
    try:
        weight_row = conn.execute(
            f"SELECT date, weight_kg FROM {MEASUREMENTS_TABLE} "
            "WHERE weight_kg IS NOT NULL ORDER BY date DESC LIMIT 1"
        ).fetchone()
        bf_row = conn.execute(
            f"SELECT date, body_fat_percent FROM {MEASUREMENTS_TABLE} "
            "WHERE body_fat_percent IS NOT NULL ORDER BY date DESC LIMIT 1"
        ).fetchone()
    finally:
        conn.close()

    weight_date, weight = weight_row if weight_row else (None, None)
    bf_date, body_fat = bf_row if bf_row else (None, None)
    return weight, weight_date, body_fat, bf_date


@st.cache_data
def build_totals_df(sets_df: pd.DataFrame, weight_df: pd.DataFrame) -> pd.DataFrame:
    """
    For each day a SBD lift was trained, look up:
      - most recent squat session → heaviest set
      - most recent bench session → heaviest set
      - most recent deadlift session (conv or sumo, whichever heavier) → heaviest set
    Sum to total, attach nearest bodyweight, compute Wilks + DOTS.
    """
    session_max = sets_df.groupby(["date", "Exercise"])["weight_kg"].max().reset_index()
    training_days = sorted(session_max["date"].unique())

    rows = []
    for day in training_days:
        def best_lift(exercise: str):
            past = session_max[(session_max["Exercise"] == exercise) & (session_max["date"] <= day)]
            if past.empty:
                return None
            return past[past["date"] == past["date"].max()]["weight_kg"].max()

        squat = best_lift("Squat")
        bench = best_lift("Bench Press")
        conv  = best_lift("Deadlift")
        sumo  = best_lift("Sumo Deadlift")
        dl    = max(filter(None, [conv, sumo]), default=None)

        if None in (squat, bench, dl):
            continue  # haven't hit all 3 lifts yet

        total = squat + bench + dl

        bw_rows = weight_df[weight_df["date"] <= day]
        if bw_rows.empty:
            continue
        bw = bw_rows.iloc[-1]["bodyweight"]

        rows.append({
            "date":       day,
            "squat":      squat,
            "bench":      bench,
            "deadlift":   dl,
            "total":      total,
            "bodyweight": bw,
            "dots":       dots_score(total, bw),
        })

    return pd.DataFrame(rows)
