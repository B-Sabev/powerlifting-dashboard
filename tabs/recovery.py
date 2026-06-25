"""Tab 2 — Recovery Correlations: relate daily check-in metrics to session
quality / best e1RM on training days (scatter + Pearson r + correlation heatmap)."""

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from lib.constants import CORR_PREDICTORS


def render(session_df: pd.DataFrame, checkin_df: pd.DataFrame | None) -> None:
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
