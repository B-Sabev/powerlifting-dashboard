"""
Powerlifting Analytics Dashboard
---------------------------------
Data is loaded from the data/ folder next to this script:
  - data/daily_checkin.csv
  - data/powerlifting.db
"""

import sqlite3

import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
import plotly.express as px
from plotly.subplots import make_subplots
from pathlib import Path

DATA_DIR = Path(__file__).parent / "data"
CHECKIN_PATH  = DATA_DIR / "daily_checkin.csv"
# Training log, bodyweight, and nutrition all live in the SQLite warehouse, kept
# up to date by scripts/sync_liftosaur_training_log.py, scripts/sync_liftosaur_body_measurements.py,
# and scripts/sync_cronometer.py respectively (daily cron). Their raw exports
# are those scripts' inputs, not something the dashboard reads directly.
DB_PATH = DATA_DIR / "powerlifting.db"
TRAINING_TABLE = "training_log"
NUTRITION_TABLE = "cronometer_daily_nutrition"
MEASUREMENTS_TABLE = "daily_measurements"

# ── Page config ──────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Powerlifting Analytics",
    page_icon="🏋️",
    layout="wide",
)

# ── Constants ────────────────────────────────────────────────────────────────
SBD_EXERCISES = ["Bench Press", "Squat", "Deadlift", "Sumo Deadlift"]
EXERCISE_COLORS = {
    "Squat":         "#4C9BE8",
    "Bench Press":   "#E8844C",
    "Deadlift":      "#4CE87A",
    "Sumo Deadlift": "#C44CE8",
}
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

# ── Physique calculator constants (Tab 4) ──────────────────────────────────
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

# ── e1RM estimation ───────────────────────────────────────────────────────────
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

def estimate_e1rm(row) -> float | None:
    reps = int(row["Completed Reps"])
    weight = row["weight_kg"]
    rpe = row["Completed RPE"]  # may be NaN

    if reps > 5:
        return None  # ignore — not estimating 1RM from 6+ reps

    if reps == 1:
        rpe = float(rpe) if pd.notna(rpe) else 9.0   # default RPE 9 if missing
        rpe = round(rpe * 2) / 2                      # snap to nearest 0.5
        rpe = max(7.0, min(rpe, 10.0))                # clamp to table range
        return weight / RTS_TABLE[(1, rpe)]

    # reps 2–5: Epley
    return weight * (1 + reps / 30)


# ── Data loading helpers ──────────────────────────────────────────────────────
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


# ── Competition score formulas ─────────────────────────────────────────
def dots_score(total: float, bw: float) -> float:
    a, b, c, d, e = -0.000001093, 0.0007391293, -0.1918209091, 24.0900756, -307.75076
    coeff = 500 / (a*bw**4 + b*bw**3 + c*bw**2 + d*bw + e)
    return round(total * coeff, 1)
 
 
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


def trend_line(dates, values, window=4):
    """Rolling mean for trend overlay."""
    s = pd.Series(values.to_numpy(), index=dates.to_numpy()).sort_index()
    return s.rolling(window, min_periods=1).mean()


# ── Physique calculator formulas ───────────────────────────────────────────
# Ported 1:1 from data/body_measurement_calculators.xlsx (Calculators 1–6).
def ffm(weight_kg: float, body_fat_pct: float) -> float:
    return weight_kg * (1 - body_fat_pct / 100)


def ffmi_raw(ffm_kg: float, height_cm: float) -> float:
    return ffm_kg / (height_cm / 100) ** 2


def ffmi_normalized(ffm_kg: float, height_cm: float) -> float:
    return ffmi_raw(ffm_kg, height_cm) + 6.1 * (1.8 - height_cm / 100)


def ffmi_rating(norm_ffmi: float) -> str:
    if norm_ffmi < 18:
        return "Below average"
    if norm_ffmi < 20:
        return "Average"
    if norm_ffmi < 22:
        return "Above average"
    if norm_ffmi < 23:
        return "Excellent"
    if norm_ffmi < 26:
        return "Superior (likely natural limit)"
    return "Exceptional (potential enhancement)"


def target_ffm_for_ffmi(target_ffmi: float, height_cm: float) -> float:
    return (target_ffmi - 6.1 * (1.8 - height_cm / 100)) * (height_cm / 100) ** 2


def casey_butt_max_ffm(height_cm: float, wrist_cm: float, ankle_cm: float, body_fat_pct: float) -> float:
    h_in, wrist_in, ankle_in = height_cm / 2.54, wrist_cm / 2.54, ankle_cm / 2.54
    return (h_in ** 1.5) * (np.sqrt(wrist_in) / 22.667 + np.sqrt(ankle_in) / 17.0104) * (body_fat_pct / 224 + 1) * 0.453592


def nuckols_predicted(ffm_kg: float, height_cm: float, lift: str) -> float:
    coef, intercept = NUCKOLS_COEF[lift]
    return coef * (ffm_kg / height_cm) + intercept




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
totals_df = build_totals_df(sets_df, weight_df) if weight_df is not None and sets_df is not None else None
latest_weight, latest_weight_date, latest_bf, latest_bf_date = load_latest_measurements(DB_PATH)

# ── Tab layout ────────────────────────────────────────────────────────────────
tab1, tab2, tab3, tab4 = st.tabs(
    ["SBD Progression", "Recovery Correlations", "Weight & Nutrition", "Physique Calculators"]
)
# ════════════════════════════════════════════════════════════════════════════
# TAB 1 — SBD Progression
# ════════════════════════════════════════════════════════════════════════════
with tab1:
    st.subheader("Estimated 1RM over time")
    st.caption("1 rep set -> infer e1RM from weight and RPE\n" + \
               "\n2-5 reps set -> Epley formula: e1RM = weight × (1 + reps/30) · best working set per session\n" + \
               "\n6+ reps sets -> ignore, not reliable for estimating 1RM")

    col_ctrl1, col_ctrl2 = st.columns([2, 1])
    with col_ctrl1:
        exercises_shown = st.multiselect(
            "Exercises",
            options=SBD_EXERCISES,
            default=[e for e in SBD_EXERCISES if e in session_df["Exercise"].unique()],
        )
    with col_ctrl2:
        show_trend = st.toggle("Show trend line", value=True)

    # Date filter
    min_date = session_df["date"].min().date()
    max_date = session_df["date"].max().date()
    date_range = st.slider(
        "Date range",
        min_value=min_date,
        max_value=max_date,
        value=(min_date, max_date),
        format="MMM YYYY",
    )

    filtered = session_df[
        session_df["Exercise"].isin(exercises_shown)
        & (session_df["date"].dt.date >= date_range[0])
        & (session_df["date"].dt.date <= date_range[1])
    ]

    fig = go.Figure()
    for ex in exercises_shown:
        sub = filtered[filtered["Exercise"] == ex].sort_values("date")
        if sub.empty:
            continue
        color = EXERCISE_COLORS.get(ex, "#888")
        # Scatter points
        fig.add_trace(go.Scatter(
            x=sub["date"], y=sub["e1rm"].round(1),
            mode="markers",
            name=ex,
            marker=dict(color=color, size=8, opacity=0.7),
            hovertemplate=f"<b>{ex}</b><br>%{{x|%d %b %Y}}<br>e1RM: %{{y:.1f}} kg<extra></extra>",
        ))

        # Trend line
        if show_trend and len(sub) >= 2:
            trend = trend_line(sub["date"], sub["e1rm"])
            fig.add_trace(go.Scatter(
                x=trend.index, y=trend.values.round(1),
                mode="lines",
                name=f"{ex} trend",
                line=dict(color=color, width=2, dash="dot"),
                hoverinfo="skip",
            ))

    fig.update_layout(
        xaxis_title="Date",
        yaxis_title="Estimated 1RM (kg)",
        hovermode="x unified",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        height=480,
        margin=dict(l=40, r=20, t=10, b=40),
    )
    st.plotly_chart(fig, width='stretch')

    # ── DOTS over time ────────────────────────────────────────────────
    st.divider()
    st.subheader("DOTS over time")
    
    if totals_df is not None and len(totals_df) > 0:
        st.caption("DOTS (Dynamic Objective Team Scoring) is a " + 
                   "body weight adjusted scoring system for powerlifting. "
                   "It uses the highest lifted squat, bench press, deadlift at a given weight, " + ""
                   "plugged into a biological sex (male/female) specific formula, allowing for comparison of lifters of different body weights.")
        
    
        with st.columns([2, 1])[0]:
            show_score_trend = st.toggle("Show trend line", value=True, key="score_trend_toggle")
        
        # Date filter for score plot
        score_min_date = totals_df["date"].min().date()
        score_max_date = totals_df["date"].max().date()
        score_date_range = st.slider(
            "Date range (scores)",
            min_value=score_min_date,
            max_value=score_max_date,
            value=(score_min_date, score_max_date),
            format="MMM YYYY",
            key="score_date_slider"
        )
        
        filtered_totals = totals_df[
            (totals_df["date"].dt.date >= score_date_range[0])
            & (totals_df["date"].dt.date <= score_date_range[1])
        ].sort_values("date")
        
        fig_scores = go.Figure()
        
        
        
        fig_scores.add_trace(go.Scatter(
            x=filtered_totals["date"],
            y=filtered_totals["dots"],
            mode="markers",
            name="DOTS",
            marker=dict(color="#4CE87A", size=8, opacity=0.7),
            hovertemplate="<b>DOTS</b><br>%{x|%d %b %Y}<br>Score: %{y:.1f}<extra></extra>",
        ))
            
        if show_score_trend and len(filtered_totals) >= 2:
            dots_trend = trend_line(filtered_totals["date"], filtered_totals["dots"])
            fig_scores.add_trace(go.Scatter(
                x=dots_trend.index,
                y=dots_trend.values.round(1),
                mode="lines",
                name="DOTS trend",
                line=dict(color="#4CE87A", width=2, dash="dot"),
                hoverinfo="skip",
            ))
        
        fig_scores.update_layout(
            xaxis_title="Date",
            yaxis_title="Competition Score",
            hovermode="x unified",
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
            height=480,
            margin=dict(l=40, r=20, t=10, b=40),
        )
        st.plotly_chart(fig_scores, width='stretch')
        
        # Display latest scores
        if len(filtered_totals) > 0:
            latest = filtered_totals.iloc[-1]
            col1, col2, col3 = st.columns(3)
            with col1:
                st.metric("Total", f"{latest['total']:.0f} kg")
            with col2:
                st.metric("Bodyweight", f"{latest['bodyweight']:.1f} kg")
            with col3:
                st.metric("DOTS", f"{latest['dots']:.1f}")
    else:
        st.info("📦 Sync bodyweight via `scripts/sync_liftosaur_body_measurements.py` to see Wilks & DOTS scores over time.")

    # ── PRs table ────────────────────────────────────────────────────────────
    st.subheader("All-time 1RM PRs")
    pr_rows = []
    for ex in SBD_EXERCISES:
        sub = sets_df[sets_df["Exercise"] == ex]
        if sub.empty:
            continue
        best = sub[sub["weight_kg"] == sub["weight_kg"].max()].sort_values("date").iloc[0]
        pr_rows.append({
            "Exercise": ex,
            "Weight (kg)": round(best["weight_kg"], 1),
            "Date": best["date"].strftime("%d %b %Y"),
        })
    if pr_rows:
        st.dataframe(pd.DataFrame(pr_rows).set_index("Exercise"), width='stretch')


# ════════════════════════════════════════════════════════════════════════════
# TAB 2 — Recovery Correlations
# ════════════════════════════════════════════════════════════════════════════
with tab2:
    if checkin_df is None:
        st.info("👈 Upload your daily check-in CSV to unlock this tab.")
        st.stop()

    st.subheader("How recovery metrics relate to session quality")

    # Join: check-in training days only, merge with e1rm on same date
    training_days = checkin_df[checkin_df["trained_today"] == True].copy()
    # Also pull the e1rm for that date (any lift — use max as "overall performance")
    daily_e1rm = session_df.groupby("date")["e1rm"].max().reset_index()
    daily_e1rm.columns = ["date", "best_e1rm"]

    joined = training_days.merge(daily_e1rm, on="date", how="left")
    joined = joined[joined["session_quality"].notna()]

    st.caption(
        f"Based on {len(joined)} training days with both check-in and session quality logged."
        + (f" · {joined['best_e1rm'].notna().sum()} days also matched to a lift in Liftosaur." if not daily_e1rm.empty else "")
    )

    col_a, col_b = st.columns(2)
    with col_a:
        x_var_label = st.selectbox("X axis (predictor)", list(CORR_PREDICTORS.keys()), index=6)
    with col_b:
        y_var_label = st.selectbox(
            "Y axis (outcome)",
            ["Session Quality", "Best e1RM (kg)"],
            index=0,
        )

    x_col = CORR_PREDICTORS[x_var_label]
    y_col = "session_quality" if y_var_label == "Session Quality" else "best_e1rm"

    plot_data = joined[[x_col, y_col, "date", "notes"]].dropna(subset=[x_col, y_col])

    if len(plot_data) < 3:
        st.warning("Not enough overlapping data yet to plot. Keep logging! 📝")
    else:
        r = np.corrcoef(plot_data[x_col], plot_data[y_col])[0, 1]

        fig2 = px.scatter(
            plot_data,
            x=x_col,
            y=y_col,
            trendline="ols",
            hover_data={"date": "|%d %b %Y", "notes": True},
            labels={
                x_col: x_var_label,
                y_col: y_var_label,
                "date": "Date",
                "notes": "Notes",
            },
            color_discrete_sequence=["#4C9BE8"],
        )
        fig2.update_traces(marker=dict(size=9, opacity=0.75))
        fig2.update_layout(
            height=420,
            margin=dict(l=40, r=20, t=10, b=40),
        )
        st.plotly_chart(fig2, width='stretch')

        direction = "positive" if r > 0 else "negative"
        strength = "strong" if abs(r) > 0.5 else "moderate" if abs(r) > 0.3 else "weak"
        st.metric("Pearson r", f"{r:.2f}", help=f"{strength.capitalize()} {direction} correlation (n={len(plot_data)})")

    # ── Correlation heatmap ───────────────────────────────────────────────────
    st.divider()
    st.subheader("Correlation matrix — all predictors vs session quality")

    corr_cols = list(CORR_PREDICTORS.values()) + ["session_quality"]
    corr_data = joined[corr_cols].dropna()

    if len(corr_data) >= 5:
        corr_matrix = corr_data.corr()
        sq_corr = corr_matrix["session_quality"].drop("session_quality").sort_values()

        fig3 = go.Figure(go.Bar(
            x=sq_corr.values.round(2),
            y=[k for k, v in CORR_PREDICTORS.items() if v in sq_corr.index],
            orientation="h",
            marker_color=["#E8474C" if v < 0 else "#4CE87A" for v in sq_corr.values],
            text=[f"{v:.2f}" for v in sq_corr.values.round(2)],
            textposition="outside",
        ))
        fig3.update_layout(
            xaxis=dict(title="Pearson r with Session Quality", range=[-1.1, 1.1]),
            height=350,
            margin=dict(l=10, r=60, t=10, b=40),
        )
        st.plotly_chart(fig3, width='stretch')
        st.caption("Green = higher value → better session. Red = higher value → worse session.")
    else:
        st.info("Need at least 5 training days with complete data for the heatmap.")

# ════════════════════════════════════════════════════════════════════════════
# TAB 3 — Weight & Nutrition (Bulk / Cut Progress)
# ════════════════════════════════════════════════════════════════════════════
with tab3:
    if weight_df is None:
        st.info("👈 Sync bodyweight via `scripts/sync_liftosaur_body_measurements.py` to see weight tracking.")
        st.stop()

    if nutrition_df is None:
        st.info("👈 Run `python scripts/sync_cronometer.py` to populate the nutrition database and unlock this tab.")
        st.stop()

    st.subheader("Weight & Nutrition Tracking")
    st.caption("Track your bulk/cut progress by combining daily weight and calorie intake.")

    # Merge weight and nutrition on date (inner join keeps only days with both)
    merged = pd.merge(weight_df, nutrition_df, on="date", how="inner")
    if merged.empty:
        st.warning("No overlapping dates between weight and nutrition data. Ensure both have entries on the same days.")
        st.stop()

    # ---- Date range slider ----
    min_date = merged["date"].min().date()
    max_date = merged["date"].max().date()
    date_range = st.slider(
        "Date range",
        min_value=min_date,
        max_value=max_date,
        value=(min_date, max_date),
        format="YYYY-MM-DD",
        key="weight_nutrition_date"
    )
    mask = (merged["date"].dt.date >= date_range[0]) & (merged["date"].dt.date <= date_range[1])
    full_merged = merged.sort_values("date").copy()
    plot_data = merged.loc[mask].sort_values("date").copy()

    if len(plot_data) < 3:
        st.warning("Not enough days in the selected range. Pick a wider range.")
        st.stop()

    # ---- User inputs for target rate and rolling window ----
    col1, col2, col3 = st.columns(3)
    with col1:
        use_percent = st.checkbox("Use % of body weight", value=False)

    with col2:
        if use_percent:
            avg_bw = plot_data["bodyweight"].mean()
            target_percent = st.number_input(
                "Target rate (% of BW/week)",
                value=0.0,
                step=0.01,
                format="%.2f",
                help="e.g., 0.5% of body weight per week"
            )
            target_rate = target_percent / 100.0 * avg_bw
            st.caption(f"= {target_rate:.2f} kg/week")
        else:
            target_rate = st.number_input(
                "Target rate (kg/week)",
                value=0.0,
                step=0.01,
                format="%.2f",
                help="Positive for gaining, negative for losing."
            )

    with col3:
        roll_window = st.number_input(
            "Rolling average window (days)",
            min_value=1,
            max_value=30,
            value=7,
            step=1,
            key="roll_window"
        )

    # ---- Compute rolling average on the *full* dataset (to get values at range boundaries) ----
    full_merged["rolling"] = full_merged["bodyweight"].rolling(window=roll_window, min_periods=1).mean()

    range_mask = (full_merged["date"].dt.date >= date_range[0]) & (full_merged["date"].dt.date <= date_range[1])
    range_data = full_merged.loc[range_mask].copy()

    valid_rolling = range_data.dropna(subset=["rolling"])
    if len(valid_rolling) < 2:
        st.warning(f"Not enough data to compute a {roll_window}-day rolling average. Try a smaller window or wider range.")
        st.stop()

    start_row = valid_rolling.iloc[0]
    end_row = valid_rolling.iloc[-1]
    start_date = start_row["date"]
    end_date = end_row["date"]
    start_rolling = start_row["rolling"]
    end_rolling = end_row["rolling"]

    days_diff = (end_date - start_date).days
    weeks_diff = days_diff / 7.0
    if weeks_diff <= 0:
        st.warning("Start and end dates are the same. Cannot compute rate.")
        st.stop()
    rate_actual = (end_rolling - start_rolling) / weeks_diff

    avg_cal = plot_data["Energy (kcal)"].mean()
    slope_kg_per_day = rate_actual / 7.0
    maintenance = avg_cal - (slope_kg_per_day * 7700)
    target_kg_per_day = target_rate / 7.0
    target_cal = maintenance + (target_kg_per_day * 7700)

    # ---- Weight over time (Plot) ----
    st.divider()
    st.subheader("Weight over time")

    plot_data = plot_data.merge(full_merged[["date", "rolling"]], on="date", how="left")

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=plot_data["date"],
        y=plot_data["bodyweight"],
        mode="markers",
        name="Daily weight",
        marker=dict(color="#4C9BE8", size=6, opacity=0.6),
        hovertemplate="%{x|%d %b %Y}<br>%{y:.1f} kg<extra></extra>"
    ))
    fig.add_trace(go.Scatter(
        x=plot_data["date"],
        y=plot_data["rolling"],
        mode="lines",
        name=f"{roll_window}-day rolling avg",
        line=dict(color="#E8844C", width=2),
        hovertemplate="%{x|%d %b %Y}<br>%{y:.1f} kg<extra></extra>"
    ))
    fig.add_trace(go.Scatter(
        x=[start_date, end_date],
        y=[start_rolling, end_rolling],
        mode="markers",
        name="Rate start/end",
        marker=dict(color="red", size=12, symbol="star"),
        hovertemplate="%{x|%d %b %Y}<br>Rolling avg: %{y:.1f} kg<extra></extra>"
    ))
    fig.add_trace(go.Scatter(
        x=[start_date, end_date],
        y=[start_rolling, end_rolling],
        mode="lines",
        name="Rate line",
        line=dict(color="red", width=2, dash="dot"),
        hoverinfo="skip"
    ))

    fig.update_layout(
        xaxis_title="Date",
        yaxis_title="Bodyweight (kg)",
        hovermode="x unified",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        height=480,
        margin=dict(l=40, r=20, t=10, b=40),
    )
    st.plotly_chart(fig, use_container_width=True)

    # ---- Progress Summary (Metrics) ----
    st.divider()
    st.subheader("Progress Summary")
    colA, colB, colC, colD = st.columns(4)
    with colA:
        st.metric("Actual rate", f"{rate_actual:+.2f} kg/week", delta=f"{rate_actual - target_rate:+.2f} vs target")
    with colB:
        st.metric("Target rate", f"{target_rate:+.2f} kg/week")
    with colC:
        st.metric("Avg daily intake", f"{avg_cal:.0f} kcal")
    with colD:
        st.metric("Maintenance", f"{maintenance:.0f} kcal/day")

    colE, colF = st.columns(2)
    with colE:
        st.metric("Target intake", f"{target_cal:.0f} kcal/day",
                  delta=f"{target_cal - avg_cal:+.0f} from current")
    with colF:
        st.metric("Rate period", f"{start_date.strftime('%d %b')} → {end_date.strftime('%d %b')}")

    # ---- Optional: show calorie intake over time ----
    with st.expander("📉 Daily Calorie Intake"):
        fig_cal = go.Figure()
        fig_cal.add_trace(go.Scatter(
            x=plot_data["date"],
            y=plot_data["Energy (kcal)"],
            mode="markers+lines",
            name="Daily kcal",
            marker=dict(color="#C44CE8", size=4, opacity=0.5),
            line=dict(width=1)
        ))
        fig_cal.add_hline(y=maintenance, line_dash="dash", line_color="green", annotation_text="Maintenance")
        fig_cal.add_hline(y=target_cal, line_dash="dot", line_color="red", annotation_text="Target")
        fig_cal.update_layout(
            xaxis_title="Date",
            yaxis_title="Energy (kcal)",
            height=300,
            margin=dict(l=40, r=20, t=10, b=40),
        )
        st.plotly_chart(fig_cal, use_container_width=True)

    # ---- Show raw data table optionally ----
    with st.expander("View merged data"):
        st.dataframe(
            plot_data[["date", "bodyweight", "rolling", "Energy (kcal)"]].round(2),
            use_container_width=True
        )


# ════════════════════════════════════════════════════════════════════════════
# TAB 4 — Physique Calculators
# ════════════════════════════════════════════════════════════════════════════
with tab4:
    if latest_weight is None or latest_bf is None:
        st.info(
            "👈 Sync weight and body-fat % via `scripts/sync_liftosaur_body_measurements.py` "
            "to unlock these calculators."
        )
        st.stop()

    st.subheader("Inputs")
    st.caption(
        f"Height ({HEIGHT_CM:.0f} cm), wrist ({WRIST_CM} cm), and ankle ({ANKLE_CM} cm) are "
        "hardcoded (not logged regularly). Weight, body fat %, and S/B/D 1RM below are "
        "pre-filled from the most recent data but editable for what-if scenarios."
    )

    best_e1rm = session_df.groupby("Exercise")["e1rm"].max()
    dl_candidates = [v for v in (best_e1rm.get("Deadlift"), best_e1rm.get("Sumo Deadlift")) if pd.notna(v)]
    default_squat = best_e1rm.get("Squat", 0.0)
    default_bench = best_e1rm.get("Bench Press", 0.0)
    default_squat = 0.0 if pd.isna(default_squat) else default_squat
    default_bench = 0.0 if pd.isna(default_bench) else default_bench
    default_deadlift = max(dl_candidates) if dl_candidates else 0.0

    col1, col2 = st.columns(2)
    with col1:
        weight = st.number_input(
            "Current weight (kg)", value=float(latest_weight), step=0.1, format="%.1f",
            help=f"Latest logged: {latest_weight_date}",
        )
        body_fat = st.number_input(
            "Current body fat (%)", value=float(latest_bf), step=0.1, format="%.1f",
            help=f"Latest logged: {latest_bf_date}",
        )
    with col2:
        target_ffmi = st.number_input("Target FFMI (normalized)", value=27.0, step=0.1, format="%.1f")
        target_bf = st.number_input(
            "Target body fat (%)", value=15.0, step=0.1, format="%.1f",
            help="Used by both Calculator 2 (target composition) and Calculator 3 (Casey Butt max potential).",
        )

    col3, col4, col5 = st.columns(3)
    with col3:
        squat_1rm = st.number_input("Squat 1RM (kg)", value=round(float(default_squat), 1), step=0.5)
    with col4:
        bench_1rm = st.number_input("Bench 1RM (kg)", value=round(float(default_bench), 1), step=0.5)
    with col5:
        deadlift_1rm = st.number_input("Deadlift 1RM (kg)", value=round(float(default_deadlift), 1), step=0.5)

    # ── Calculator 1 — FFMI ──────────────────────────────────────────────
    st.divider()
    st.subheader("1. Fat-Free Mass Index (FFMI)")
    if weight > 0 and body_fat > 0:
        current_ffm = ffm(weight, body_fat)
        raw = ffmi_raw(current_ffm, HEIGHT_CM)
        norm = ffmi_normalized(current_ffm, HEIGHT_CM)

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Fat-Free Mass", f"{current_ffm:.1f} kg")
        c2.metric("FFMI (raw)", f"{raw:.1f}")
        c3.metric("FFMI (normalized)", f"{norm:.1f}")
        c4.metric("Rating", ffmi_rating(norm))
    else:
        st.info("Enter weight and body fat % above.")
        current_ffm = None

    # ── Calculator 2 — Target Body Composition Planner ───────────────────
    st.divider()
    st.subheader("2. Target Body Composition Planner")
    if current_ffm is not None and target_ffmi > 0 and target_bf > 0:
        target_ffm_val = target_ffm_for_ffmi(target_ffmi, HEIGHT_CM)
        target_weight = target_ffm_val / (1 - target_bf / 100)
        delta_weight = target_weight - weight
        delta_muscle = target_ffm_val - current_ffm
        delta_fat = delta_weight - delta_muscle

        c1, c2, c3 = st.columns(3)
        c1.metric("Target FFM", f"{target_ffm_val:.1f} kg")
        c2.metric("Target Total Weight", f"{target_weight:.1f} kg")
        c3.metric("Δ Weight", f"{delta_weight:+.1f} kg")
        c4, c5 = st.columns(2)
        c4.metric("Δ Muscle (FFM)", f"{delta_muscle:+.1f} kg")
        c5.metric("Δ Fat Mass", f"{delta_fat:+.1f} kg")
        st.caption(
            "Δ values show how much muscle/fat to gain (+) or lose (−) vs current. "
            "Assumes height stays constant. Target FFMI uses the normalized formula."
        )
    else:
        st.info("Enter target FFMI and target body fat % above.")
        target_ffm_val = None

    # ── Calculator 3 — Casey Butt Max Muscular Potential ─────────────────
    st.divider()
    st.subheader("3. Maximum Muscular Potential (Casey Butt)")
    if current_ffm is not None and target_bf > 0:
        max_ffm = casey_butt_max_ffm(HEIGHT_CM, WRIST_CM, ANKLE_CM, target_bf)
        max_total_weight = max_ffm / (1 - target_bf / 100)
        delta_weight_3 = max_total_weight - weight
        delta_muscle_3 = max_ffm - current_ffm
        delta_fat_3 = delta_weight_3 - delta_muscle_3

        c1, c2, c3 = st.columns(3)
        c1.metric("Max FFM (Casey Butt)", f"{max_ffm:.1f} kg")
        c2.metric("Target Total Weight", f"{max_total_weight:.1f} kg")
        c3.metric("Δ Weight", f"{delta_weight_3:+.1f} kg")
        c4, c5 = st.columns(2)
        c4.metric("Δ Muscle (FFM)", f"{delta_muscle_3:+.1f} kg")
        c5.metric("Δ Fat Mass", f"{delta_fat_3:+.1f} kg")
        st.caption(
            "Δ values show how much muscle/fat to gain (+) or lose (−) from current to "
            "reach Casey Butt's predicted maximum FFM at your target BF%. Casey Butt's "
            "formula is for natural males only. Wrist = measured just below the wrist bone "
            "(styloid process). Ankle = smallest circumference just above the ankle bone."
        )
    else:
        st.info("Enter weight, body fat %, and target body fat % above.")
        max_ffm = None
        max_total_weight = None

    # ── Calculator 5 — Nuckols Efficiency ─────────────────────────────────
    st.divider()
    st.subheader("4. Powerlifting Efficiency vs FFM Prediction (Nuckols)")
    if current_ffm is not None:
        lifts = {"Squat": squat_1rm, "Bench": bench_1rm, "Deadlift": deadlift_1rm}
        total_1rm = sum(lifts.values())
        rows = []
        for lift_name, your_1rm in {**lifts, "Total": total_1rm}.items():
            goal = nuckols_predicted(current_ffm, HEIGHT_CM, lift_name)
            efficiency = your_1rm / goal if goal else None
            rows.append({
                "Lift": lift_name,
                "Your 1RM (kg)": round(your_1rm, 1),
                "FFM-Goal (kg)": round(goal, 1),
                "Efficiency": f"{efficiency:.0%}" if efficiency is not None else "—",
            })

        total_goal = nuckols_predicted(current_ffm, HEIGHT_CM, "Total")
        dots_your = dots_score(total_1rm, weight)
        dots_goal = dots_score(total_goal, weight)
        dots_efficiency = dots_your / dots_goal if dots_goal else None
        rows.append({
            "Lift": "DOTS",
            "Your 1RM (kg)": dots_your,
            "FFM-Goal (kg)": dots_goal,
            "Efficiency": f"{dots_efficiency:.0%}" if dots_efficiency is not None else "—",
        })

        st.dataframe(pd.DataFrame(rows).set_index("Lift"), width='stretch')
        st.caption(
            "FFM-Goal is the Nuckols-predicted lift for an elite powerlifter at your current "
            "FFM. Efficiency = Your 1RM ÷ FFM-Goal (100% = predicted elite level for your FFM). "
            "DOTS row converts the Total row to a bodyweight-adjusted score using your current weight."
        )
    else:
        st.info("Enter weight and body fat % above.")

    # ── Calculator 6 — Projected Lifts at Target & Max FFM ────────────────
    st.divider()
    st.subheader("5. Projected Lifts at Target & Maximum FFM (Nuckols)")
    if target_ffm_val is not None and max_ffm is not None:
        rows = []
        for lift_name in ["Squat", "Bench", "Deadlift", "Total"]:
            rows.append({
                "Lift": lift_name,
                "At Target FFM (Calc 2)": round(nuckols_predicted(target_ffm_val, HEIGHT_CM, lift_name), 1),
                "At Max FFM — Casey Butt (Calc 3)": round(nuckols_predicted(max_ffm, HEIGHT_CM, lift_name), 1),
            })

        target_total = nuckols_predicted(target_ffm_val, HEIGHT_CM, "Total")
        max_total = nuckols_predicted(max_ffm, HEIGHT_CM, "Total")
        rows.append({
            "Lift": "DOTS",
            "At Target FFM (Calc 2)": dots_score(target_total, target_weight),
            "At Max FFM — Casey Butt (Calc 3)": dots_score(max_total, max_total_weight),
        })

        st.dataframe(pd.DataFrame(rows).set_index("Lift"), width='stretch')
        st.caption(
            "Projects your predicted elite-level lifts at two FFM milestones. Target FFM "
            "(Calc 2) = your chosen FFMI goal. Max FFM (Calc 3) = Casey Butt genetic ceiling. "
            "DOTS row converts each Total row to a bodyweight-adjusted score using that "
            "milestone's Target Total Weight."
        )
    else:
        st.info("Complete the inputs above to see projected lifts.")