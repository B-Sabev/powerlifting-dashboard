"""Tab 1 — SBD Progression: e1RM over time, DOTS over time, all-time PR table."""

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from lib.calculations import trend_line
from lib.constants import COLOR_DOTS, EXERCISE_COLORS, SBD_EXERCISES
from lib.ui import apply_default_layout, date_range_slider, filter_by_range


def render(session_df: pd.DataFrame, sets_df: pd.DataFrame, totals_df: pd.DataFrame | None) -> None:
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
    date_range = date_range_slider(session_df, "Date range", key="e1rm_date_range")
    filtered = filter_by_range(
        session_df[session_df["Exercise"].isin(exercises_shown)], date_range[0], date_range[1]
    )

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

    apply_default_layout(fig, xaxis_title="Date", yaxis_title="Estimated 1RM (kg)")
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
        score_date_range = date_range_slider(totals_df, "Date range (scores)", key="score_date_slider")
        filtered_totals = filter_by_range(totals_df, score_date_range[0], score_date_range[1]).sort_values("date")

        fig_scores = go.Figure()

        fig_scores.add_trace(go.Scatter(
            x=filtered_totals["date"],
            y=filtered_totals["dots"],
            mode="markers",
            name="DOTS",
            marker=dict(color=COLOR_DOTS, size=8, opacity=0.7),
            hovertemplate="<b>DOTS</b><br>%{x|%d %b %Y}<br>Score: %{y:.1f}<extra></extra>",
        ))

        if show_score_trend and len(filtered_totals) >= 2:
            dots_trend = trend_line(filtered_totals["date"], filtered_totals["dots"])
            fig_scores.add_trace(go.Scatter(
                x=dots_trend.index,
                y=dots_trend.values.round(1),
                mode="lines",
                name="DOTS trend",
                line=dict(color=COLOR_DOTS, width=2, dash="dot"),
                hoverinfo="skip",
            ))

        apply_default_layout(fig_scores, xaxis_title="Date", yaxis_title="Competition Score")
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
