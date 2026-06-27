"""
Powerlifting Analytics Dashboard
---------------------------------
Data is loaded from the data/ folder next to this script:
  - data/daily_checkin.csv
  - data/powerlifting.db

This file is the thin entrypoint: page config, data loading, and tab wiring.
Pure math lives in lib/calculations.py, cached DB loaders in lib/data.py,
shared constants in lib/constants.py, and each tab's Streamlit/Plotly UI in
its own module under tabs/.
"""

import streamlit as st

from lib.constants import CHECKIN_PATH, DB_PATH
from lib.data import (
    load_checkin,
    load_latest_measurements,
    load_nutrition,
    load_training,
    load_weight,
    load_workout_completion,
    build_totals_df,
)
from tabs import physique, progression, recovery, weight_nutrition

# ── Page config ──────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Powerlifting Analytics",
    page_icon="🏋️",
    layout="wide",
)

# ── App shell ─────────────────────────────────────────────────────────────────
st.title("Powerlifting Analytics")
st.caption("SBD progression · Recovery correlations · Session quality")

# ── Load data ─────────────────────────────────────────────────────────────────
if not DB_PATH.exists():
    st.error(f"Database not found at `{DB_PATH}`. Run `python scripts/sync_liftosaur_training_log.py` to sync from Liftosaur.")
    st.stop()

session_df, sets_df = load_training(DB_PATH)
if session_df.empty:
    st.error("No training data found in the database. Run `python scripts/sync_liftosaur_training_log.py` to sync from Liftosaur.")
    st.stop()
checkin_df = load_checkin(CHECKIN_PATH) if CHECKIN_PATH.exists() else None
weight_df = load_weight(DB_PATH) if DB_PATH.exists() else None
if weight_df is not None and weight_df.empty:
    weight_df = None
nutrition_df = load_nutrition(DB_PATH) if DB_PATH.exists() else None
completion_df = load_workout_completion(DB_PATH) if DB_PATH.exists() else None
totals_df = build_totals_df(sets_df, weight_df) if weight_df is not None and sets_df is not None else None
latest_weight, latest_weight_date, latest_bf, latest_bf_date = load_latest_measurements(DB_PATH)

# ── Tab layout ────────────────────────────────────────────────────────────────
tab1, tab2, tab3, tab4 = st.tabs(
    ["SBD Progression", "Recovery Correlations", "Weight & Nutrition", "Physique Calculators"]
)

with tab1:
    progression.render(session_df, sets_df, totals_df)

with tab2:
    recovery.render(session_df, sets_df, checkin_df, completion_df)

with tab3:
    weight_nutrition.render(weight_df, nutrition_df)

with tab4:
    physique.render(session_df, latest_weight, latest_weight_date, latest_bf, latest_bf_date)
