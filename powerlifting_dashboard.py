"""
Powerlifting Analytics Dashboard
---------------------------------
Data is loaded from the data/ folder next to this script:
  - data/liftosaur_training_log.csv
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
TRAINING_PATH = DATA_DIR / "liftosaur_training_log.csv"
CHECKIN_PATH  = DATA_DIR / "daily_checkin.csv"
# Nutrition and bodyweight both live in the SQLite warehouse, kept up to date
# by scripts/sync_cronometer.py and scripts/sync_liftosaur_body_measurements.py
# respectively (daily cron). Their raw exports are those scripts' inputs, not
# something the dashboard reads directly.
DB_PATH = DATA_DIR / "powerlifting.db"
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
def load_training(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    sbd = df[
        df["Exercise"].isin(SBD_EXERCISES)
        & (df["Is Warmup Set?"] == 0)
        & (df["Completed Reps"].notna())
        & (df["Completed Weight Value"].notna())
        & (df["Completed Weight Value"] > 0)
        & (df["Completed Reps"] > 0)
    ].copy()

    # Normalise to kg
    sbd["weight_kg"] = np.where(
        sbd["Completed Weight Unit"] == "lb",
        sbd["Completed Weight Value"] * 0.453592,
        sbd["Completed Weight Value"],
    )
    sbd["date"] = pd.to_datetime(sbd["Workout DateTime"], utc=True).dt.normalize().dt.tz_localize(None)

    # e1RM: RTS for singles, Epley for 2–4 reps, None for 5+
    sbd["e1rm"] = sbd.apply(estimate_e1rm, axis=1)

    # Best set per session (max ignores None/NaN automatically)
    session = (
        sbd.groupby(["date", "Exercise"])["e1rm"]
        .max()
        .reset_index()
        .dropna(subset=["e1rm"])
        .sort_values("date")
    )
    return session, sbd[["date", "Exercise", "weight_kg", "Completed Reps"]].copy()


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




# ── App shell ─────────────────────────────────────────────────────────────────
st.title("🏋️ Powerlifting Analytics")
st.caption("SBD progression · Recovery correlations · Session quality")

# ── Sidebar ───────────────────────────────────────────────────────────────────
with st.sidebar:
    st.header("📂 Data")
    st.markdown(
        f"""
Drop your exports into the `data/` folder next to this script:

- `liftosaur_training_log.csv`
- `daily_checkin.csv`

Bodyweight and nutrition come from `powerlifting.db` (SQLite), kept current by running:
`python scripts/sync_liftosaur_body_measurements.py` and `python scripts/sync_cronometer.py`
after a fresh export.

Then refresh the page.
        """
    )
    if st.button("🔄 Reload data"):
        st.cache_data.clear()
        st.rerun()

# ── Load data ─────────────────────────────────────────────────────────────────
if not TRAINING_PATH.exists():
    st.error(f"Training log not found at `{TRAINING_PATH}`. Add it to the `data/` folder.")
    st.stop()

session_df, sets_df = load_training(TRAINING_PATH)
checkin_df = load_checkin(CHECKIN_PATH) if CHECKIN_PATH.exists() else None
weight_df = load_weight(DB_PATH) if DB_PATH.exists() else None
if weight_df is not None and weight_df.empty:
    weight_df = None
nutrition_df = load_nutrition(DB_PATH) if DB_PATH.exists() else None
totals_df = build_totals_df(sets_df, weight_df) if weight_df is not None and sets_df is not None else None

# ── Tab layout ────────────────────────────────────────────────────────────────
tab1, tab2, tab3 = st.tabs(["📈 SBD Progression", "🔗 Recovery Correlations", "⚖️ Weight & Nutrition"])
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
    st.subheader("Competition Scores: DOTS over time")
    
    if totals_df is not None and len(totals_df) > 0:
        st.caption("DOTS: Dynamic one-time strength formula")
        
    
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

    st.subheader("⚖️ Weight & Nutrition Tracking")
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
    st.subheader("📊 Progress Summary")
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
    with st.expander("📋 View merged data"):
        st.dataframe(
            plot_data[["date", "bodyweight", "rolling", "Energy (kcal)"]].round(2),
            use_container_width=True
        )