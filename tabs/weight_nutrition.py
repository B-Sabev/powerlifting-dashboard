"""Tab 3 — Weight & Nutrition (Bulk / Cut Progress): dual view of bodyweight
(rolling average + target-rate projection) and calorie intake (rolling average +
estimated TDEE), merged on date (inner join — only days with both logged)."""

import pandas as pd
import plotly.graph_objects as go
import streamlit as st


def render(weight_df: pd.DataFrame | None, nutrition_df: pd.DataFrame | None) -> None:
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
