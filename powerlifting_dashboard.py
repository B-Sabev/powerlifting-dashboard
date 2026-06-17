"""
Powerlifting Analytics Dashboard
---------------------------------
Data is loaded from the data/ folder next to this script:
  - data/liftosaur_training_log.csv
  - data/daily_checkin.csv
"""

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

    if reps >= 5:
        return None  # ignore — not estimating 1RM from 5+ reps

    if reps == 1:
        rpe = float(rpe) if pd.notna(rpe) else 9.0   # default RPE 9 if missing
        rpe = round(rpe * 2) / 2                      # snap to nearest 0.5
        rpe = max(7.0, min(rpe, 10.0))                # clamp to table range
        return weight / RTS_TABLE[(1, rpe)]

    # reps 2–4: Epley
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
    return session


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


def trend_line(dates, values, window=4):
    """Rolling mean for trend overlay."""
    s = pd.Series(values, index=dates).sort_index()
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

session_df = load_training(TRAINING_PATH)
checkin_df = load_checkin(CHECKIN_PATH) if CHECKIN_PATH.exists() else None

# ── Tab layout ────────────────────────────────────────────────────────────────
tab1, tab2, tab3 = st.tabs(["📈 SBD Progression", "🔗 Recovery Correlations", "📋 Raw Data"])

# ════════════════════════════════════════════════════════════════════════════
# TAB 1 — SBD Progression
# ════════════════════════════════════════════════════════════════════════════
with tab1:
    st.subheader("Estimated 1RM over time")
    st.caption("Epley formula: weight × (1 + reps/30) · best working set per session")

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
    st.plotly_chart(fig, use_container_width=True)

    # ── PRs table ────────────────────────────────────────────────────────────
    st.subheader("All-time PRs (estimated 1RM)")
    pr_rows = []
    for ex in SBD_EXERCISES:
        sub = session_df[session_df["Exercise"] == ex]
        if sub.empty:
            continue
        best = sub.loc[sub["e1rm"].idxmax()]
        pr_rows.append({
            "Exercise": ex,
            "e1RM (kg)": round(best["e1rm"], 1),
            "Date": best["date"].strftime("%d %b %Y"),
        })
    if pr_rows:
        st.dataframe(pd.DataFrame(pr_rows).set_index("Exercise"), use_container_width=True)

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
        st.plotly_chart(fig2, use_container_width=True)

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
        st.plotly_chart(fig3, use_container_width=True)
        st.caption("Green = higher value → better session. Red = higher value → worse session.")
    else:
        st.info("Need at least 5 training days with complete data for the heatmap.")

# ════════════════════════════════════════════════════════════════════════════
# TAB 3 — Raw Data
# ════════════════════════════════════════════════════════════════════════════
with tab3:
    st.subheader("SBD session data")
    st.dataframe(
        session_df.rename(columns={"e1rm": "e1RM (kg)", "date": "Date", "Exercise": "Exercise"})
        .assign(**{"e1RM (kg)": lambda d: d["e1RM (kg)"].round(1)})
        .sort_values("Date", ascending=False),
        use_container_width=True,
    )

    if checkin_df is not None:
        st.subheader("Check-in data")
        st.dataframe(
            checkin_df.sort_values("date", ascending=False),
            use_container_width=True,
        )
