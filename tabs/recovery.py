"""Tab 2 — Recovery Correlations: relate daily check-in metrics (plus a couple
of engineered recovery/load features) to session quality / per-lift relative
e1RM on training days (scatter + Spearman r + correlation heatmap + a ridge
regression for each factor's *unique* contribution)."""

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
from scipy.stats import spearmanr

from lib.calculations import acwr, relative_e1rm, ridge_standardized_coefs, trend_line
from lib.constants import (
    ACWR_ACUTE_WINDOW,
    ACWR_CHRONIC_WINDOW,
    CORR_PREDICTORS,
    RELATIVE_E1RM_MIN_PERIODS,
    RELATIVE_E1RM_WINDOW,
    RIDGE_ALPHA,
    ROLLING_SLEEP_WINDOWS,
)


def _rolling_series_to_df(series: pd.Series, col_name: str) -> pd.DataFrame:
    """lib.calculations.trend_line/acwr return a date-indexed Series; flatten to
    a (date, value) frame so it can be merged like any other predictor."""
    return series.rename_axis("date").reset_index(name=col_name)


def render(session_df: pd.DataFrame, sets_df: pd.DataFrame, checkin_df: pd.DataFrame | None, completion_df: pd.DataFrame | None = None) -> None:
    if checkin_df is None:
        st.info("👈 Upload your daily check-in CSV to unlock this tab.")
        st.stop()

    st.subheader("How recovery metrics relate to session quality")

    # ── Outcome: per-lift relative e1RM ─────────────────────────────────────
    # Raw e1RM is dominated by *which* lift was trained that day (a deadlift
    # e1RM dwarfs a bench e1RM) and drifts upward over a training cycle.
    # Dividing each session by that lift's own trailing baseline removes both
    # confounds, making performance comparable across lifts and over time.
    session_rel = session_df.copy()
    session_rel["relative_e1rm"] = relative_e1rm(
        session_rel, window=RELATIVE_E1RM_WINDOW, min_periods=RELATIVE_E1RM_MIN_PERIODS
    )
    daily_relative = session_rel.groupby("date")["relative_e1rm"].mean().reset_index()

    # ── Engineered predictors ───────────────────────────────────────────────
    # Rolling sleep (sleep debt), computed over the full check-in history (not
    # just training days) so each training day picks up the days off too.
    rolling_sleep = None
    for col, window in ROLLING_SLEEP_WINDOWS.items():
        roll = _rolling_series_to_df(trend_line(checkin_df["date"], checkin_df["sleep_hours"], window=window), col)
        rolling_sleep = roll if rolling_sleep is None else rolling_sleep.merge(roll, on="date", how="outer")

    # ACWR (acute:chronic workload ratio) from daily training tonnage.
    daily_load = (sets_df["weight_kg"] * sets_df["Completed Reps"]).groupby(sets_df["date"]).sum()
    acwr_df = _rolling_series_to_df(
        acwr(daily_load.index, daily_load.to_numpy(), ACWR_ACUTE_WINDOW, ACWR_CHRONIC_WINDOW), "acwr"
    )

    # ── Join: check-in training days + outcome + engineered predictors ──────
    training_days = checkin_df[checkin_df["trained_today"] == True].copy()
    joined = (
        training_days
        .merge(daily_relative, on="date", how="left")
        .merge(rolling_sleep, on="date", how="left")
        .merge(acwr_df, on="date", how="left")
    )
    if completion_df is not None and not completion_df.empty:
        joined = joined.merge(completion_df, on="date", how="left")
    else:
        joined["pct_planned_completed"] = float("nan")
    joined = joined[joined["session_quality"].notna()]

    st.caption(
        f"Based on {len(joined)} training days with check-in and session quality logged."
        f" · {joined['relative_e1rm'].notna().sum()} days also have a relative-e1RM reading"
        f" (needs {RELATIVE_E1RM_MIN_PERIODS}+ prior sessions on that lift)."
        f" · {joined['pct_planned_completed'].notna().sum()} days have workout-completion data"
        " (run sync --full to backfill)."
    )

    # ── Selectors ───────────────────────────────────────────────────────────
    OUTCOMES = {
        "Relative e1RM":        "relative_e1rm",
        "Session Quality":      "session_quality",
        "Workout Completion %": "pct_planned_completed",
    }
    col_a, col_b = st.columns(2)
    with col_b:
        y_var_label = st.selectbox("Y axis (outcome)", list(OUTCOMES.keys()), index=0)
    y_col = OUTCOMES[y_var_label]

    # Self-correlation guard: when the chosen outcome is also a predictor
    # (Workout Completion %), exclude it from the X-axis and all multivariate
    # charts so it can't correlate with itself (r=1 artefact).
    active_preds = {k: v for k, v in CORR_PREDICTORS.items() if v != y_col}
    label_by_col = {v: k for k, v in active_preds.items()}

    with col_a:
        x_var_label = st.selectbox("X axis (predictor)", list(active_preds.keys()), index=0)
    x_col = active_preds[x_var_label]

    st.caption(
        f"⚠️ Small-sample caveat: with ~40 training days and {len(active_preds)} predictors, "
        "individual correlations and the ridge coefficients below should be read as directional, "
        "not definitive — several sit close to the noise floor at this n."
    )

    plot_data = joined[[x_col, y_col, "date", "notes"]].dropna(subset=[x_col, y_col])

    if len(plot_data) < 3:
        st.warning("Not enough overlapping data yet to plot. Keep logging! 📝")
    else:
        r, p = spearmanr(plot_data[x_col], plot_data[y_col])

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
        st.metric(
            "Spearman r", f"{r:.2f}",
            help=f"{strength.capitalize()} {direction} correlation (n={len(plot_data)}, p={p:.3f})",
        )

    # ── Correlation heatmap (univariate, pairwise-complete) ──────────────────
    st.divider()
    st.subheader(f"Correlation matrix — all predictors vs {y_var_label}")

    corr_cols = list(active_preds.values()) + [y_col]
    # Note: no listwise dropna() here on purpose — pandas' .corr() already
    # computes each pair on its own pairwise-complete rows. Dropping any row
    # missing *any* predictor before correlating would silently shrink n and
    # could flip signs the moment a single predictor has a gap.
    corr_data = joined[corr_cols]

    if corr_data[y_col].notna().sum() >= 5:
        corr_matrix = corr_data.corr(method="spearman")
        outcome_corr = corr_matrix[y_col].drop(y_col).dropna().sort_values()

        fig3 = go.Figure(go.Bar(
            x=outcome_corr.values.round(2),
            y=[label_by_col[c] for c in outcome_corr.index],
            orientation="h",
            marker_color=["#E8474C" if v < 0 else "#4CE87A" for v in outcome_corr.values],
            text=[f"{v:.2f}" for v in outcome_corr.values.round(2)],
            textposition="outside",
        ))
        fig3.update_layout(
            xaxis=dict(title=f"Spearman r with {y_var_label}", range=[-1.1, 1.1]),
            height=400,
            margin=dict(l=10, r=60, t=10, b=40),
        )
        st.plotly_chart(fig3, width='stretch')
        st.caption("Green = higher value → better outcome. Red = higher value → worse outcome.")
    else:
        st.info("Need at least 5 training days with this outcome logged for the heatmap.")

    # ── Ridge regression: each factor's contribution, holding others constant ─
    st.divider()
    st.subheader(f"Unique contribution of each factor — ridge regression on {y_var_label}")
    st.caption(
        "Univariate correlations above can't tell sleep's effect apart from fatigue's if the "
        "two move together. These standardized coefficients come from regressing the outcome "
        "on all predictors at once (with an L2 penalty for stability at this sample size), so "
        "each bar is that factor's effect *holding the others fixed*."
    )

    X = joined[list(active_preds.values())]
    complete_rows = X.assign(__y__=joined[y_col]).dropna()

    if len(complete_rows) >= 10:
        coefs = ridge_standardized_coefs(X, joined[y_col], alpha=RIDGE_ALPHA).sort_values()

        fig4 = go.Figure(go.Bar(
            x=coefs.values.round(2),
            y=[label_by_col[c] for c in coefs.index],
            orientation="h",
            marker_color=["#E8474C" if v < 0 else "#4CE87A" for v in coefs.values],
            text=[f"{v:.2f}" for v in coefs.values.round(2)],
            textposition="outside",
        ))
        fig4.update_layout(
            xaxis=dict(title="Standardized ridge coefficient"),
            height=400,
            margin=dict(l=10, r=60, t=10, b=40),
        )
        st.plotly_chart(fig4, width='stretch')
        st.caption(f"Fit on {len(complete_rows)} days with every predictor logged (listwise — a joint model needs all of them per row).")
    else:
        st.info(
            f"Need at least 10 training days with *every* predictor logged for the ridge "
            f"model (have {len(complete_rows)})."
        )
