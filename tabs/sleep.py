"""Tab 5 — Sleep Consistency.

Turns the check-in sheet's bed_time / awake_time / sleep_hours columns into
actionable sleep-regularity metrics and lets you see how they respond to
deliberate interventions (logged in the 'Intervention' column of the check-in
sheet).

Two views:
  1. Sleep schedule — raw nightly bedtime and wake time as a line/scatter chart
     with intervention markers for visual context.
  2. Rolling consistency metrics — selectbox + window slider, line chart of the
     chosen metric computed over a trailing N-night window, with intervention
     markers again.
  3. Period comparison — two date-range pickers (Period A vs B) with a full
     metric table and Δ column; defaults to pre/post the latest intervention.

Metrics (ordered SRI/social-jetlag first per user preference):
  SRI, Social Jetlag, SD mid-sleep, SD bedtime, SD wake, SD duration,
  Mean efficiency, SD efficiency.
"""

import math
from datetime import time

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from lib.calculations import sleep_consistency_metrics, sleep_timing
from lib.constants import SLEEP_METRIC_LABELS, SLEEP_ROLL_WINDOW


# ── Formatting helpers ────────────────────────────────────────────────────────
_LOWER_IS_BETTER = {"social_jetlag", "sd_mid_sleep", "sd_bedtime",
                    "sd_waketime", "sd_duration", "sd_efficiency"}


def _fmt(key: str, value: float) -> str:
    if math.isnan(value):
        return "—"
    if key == "sri":
        return f"{value:.1f}"
    if key == "social_jetlag":
        return f"{value:.2f} h"
    if key in ("mean_efficiency", "sd_efficiency"):
        return f"{value:.1f}%"
    return f"{value:.0f} min"


def _fmt_delta(key: str, delta: float) -> str:
    if math.isnan(delta):
        return "—"
    lower_better = key in _LOWER_IS_BETTER
    improvement = (delta < 0) if lower_better else (delta > 0)
    arrow = "↓" if delta < 0 else "↑"
    badge = "✅ " if improvement else ("⚠️ " if abs(delta) > 0.5 else "")
    if key == "sri":
        val_str = f"{abs(delta):.1f}"
    elif key == "social_jetlag":
        val_str = f"{abs(delta):.2f} h"
    elif key in ("mean_efficiency", "sd_efficiency"):
        val_str = f"{abs(delta):.1f}%"
    else:
        val_str = f"{abs(delta):.0f} min"
    return f"{badge}{arrow}{val_str}"


# ── Y-axis clock-time tick helpers ────────────────────────────────────────────
_SCHED_TICK_VALS = [4, 5, 6, 7, 8, 9, 10, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28]
_SCHED_TICK_TEXT = [f"{int(h % 24):02d}:00" for h in _SCHED_TICK_VALS]


def _add_intervention_vlines(fig: go.Figure, ivn_df: pd.DataFrame) -> None:
    """Overlay a dashed vertical line + annotation for each intervention."""
    for _, row in ivn_df.iterrows():
        fig.add_vline(
            x=str(pd.Timestamp(row["date"]).date()),
            line_dash="dash",
            line_color="rgba(255,220,50,0.85)",
            line_width=2,
            annotation_text=str(row["label"]),
            annotation_position="top right",
            annotation_font_size=11,
        )


# ── Main render ───────────────────────────────────────────────────────────────

def render(checkin_df: pd.DataFrame | None) -> None:
    if checkin_df is None:
        st.info("👈 No check-in data found. Upload your daily check-in CSV to unlock this tab.")
        st.stop()

    timing_df = sleep_timing(checkin_df)

    if len(timing_df) < 7:
        st.info(
            f"Only **{len(timing_df)}** night(s) with Bed Time and Awake Time logged "
            f"(need at least 7). Start filling those columns in your check-in sheet — "
            "consistency metrics appear once there's enough history."
        )
        st.stop()

    # ── Extract interventions from the check-in 'intervention' column ────────
    ivn_df = pd.DataFrame(columns=["date", "label"])
    if "intervention" in checkin_df.columns:
        mask = checkin_df["intervention"].notna() & (
            checkin_df["intervention"].astype(str).str.strip() != ""
        )
        ivn_df = checkin_df.loc[mask, ["date", "intervention"]].rename(
            columns={"intervention": "label"}
        ).reset_index(drop=True)

    # ── 1. Sleep schedule chart ──────────────────────────────────────────────
    st.subheader("Sleep schedule")
    st.caption(
        "Bedtimes are **noon-anchored**: post-midnight hours have 24 added so "
        "00:30 → 24.5 and 02:00 → 26.0. This keeps the bedtime scale continuous "
        "across midnight (so 23:00 and 01:00 are 2 h apart, not 22 h). "
        "Wake dots: 🟡 work day · 🔴 rest day. Dashed lines show targets."
    )

    # Target-time inputs
    tgt_col_wake, tgt_col_bed = st.columns(2)
    with tgt_col_wake:
        tgt_wake_t = st.time_input("Target wake time", value=time(6, 0), key="sched_tgt_wake")
    with tgt_col_bed:
        tgt_bed_t = st.time_input("Target bed time", value=time(22, 30), key="sched_tgt_bed")

    # Convert to fractional hours (bed time noon-anchored, same logic as sleep_timing)
    tgt_wake_h = tgt_wake_t.hour + tgt_wake_t.minute / 60
    tgt_bed_h = tgt_bed_t.hour + tgt_bed_t.minute / 60
    if tgt_bed_h < 12:
        tgt_bed_h += 24  # post-midnight target (e.g. 01:00 → 25.0)

    # Date-range slider to focus the chart window
    sched_min_date = timing_df["date"].min().date()
    sched_max_date = timing_df["date"].max().date()
    sched_range = st.slider(
        "Date range",
        min_value=sched_min_date,
        max_value=sched_max_date,
        value=(sched_min_date, sched_max_date),
        format="DD MMM",
        key="sched_date_range",
    )
    sched_df = timing_df[
        (timing_df["date"].dt.date >= sched_range[0]) &
        (timing_df["date"].dt.date <= sched_range[1])
    ]

    fig_sched = go.Figure()

    # Bedtime — blue line + markers (unchanged)
    fig_sched.add_scatter(
        x=sched_df["date"], y=sched_df["bedtime_h"],
        mode="markers+lines",
        name="Bedtime",
        marker=dict(size=7, color="#4C9BE8"),
        line=dict(width=1.5, dash="dot", color="#4C9BE8"),
    )

    # Wake time — grey connecting line (no legend entry) so trend is visible
    # across the two coloured marker series below
    fig_sched.add_scatter(
        x=sched_df["date"], y=sched_df["waketime_h"],
        mode="lines",
        showlegend=False,
        line=dict(width=1, color="rgba(180,180,180,0.5)"),
    )

    # Wake time — work days (yellow markers)
    work_df = sched_df[sched_df["is_work_day"]]
    fig_sched.add_scatter(
        x=work_df["date"], y=work_df["waketime_h"],
        mode="markers",
        name="Wake (work day)",
        marker=dict(size=8, color="#E8C84C"),
    )

    # Wake time — rest days (red markers)
    rest_df = sched_df[~sched_df["is_work_day"]]
    fig_sched.add_scatter(
        x=rest_df["date"], y=rest_df["waketime_h"],
        mode="markers",
        name="Wake (rest day)",
        marker=dict(size=8, color="#E8474C"),
    )

    # Target reference lines
    fig_sched.add_hline(
        y=tgt_wake_h,
        line_dash="dash", line_color="rgba(232,200,76,0.55)", line_width=1.5,
        annotation_text=f"Target wake {tgt_wake_t.strftime('%H:%M')}",
        annotation_position="right",
        annotation_font_size=11,
    )
    fig_sched.add_hline(
        y=tgt_bed_h,
        line_dash="dash", line_color="rgba(76,155,232,0.55)", line_width=1.5,
        annotation_text=f"Target bed {tgt_bed_t.strftime('%H:%M')}",
        annotation_position="right",
        annotation_font_size=11,
    )

    _add_intervention_vlines(fig_sched, ivn_df)
    fig_sched.update_layout(
        yaxis=dict(
            title="Clock time",
            tickvals=_SCHED_TICK_VALS,
            ticktext=_SCHED_TICK_TEXT,
        ),
        height=560,
        margin=dict(l=50, r=120, t=10, b=40),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
    )
    st.plotly_chart(fig_sched, width="stretch")

    st.divider()

    # ── 2. Rolling consistency metric ────────────────────────────────────────
    st.subheader("Rolling consistency metrics")
    st.caption(
        "Each point is the metric computed over the **trailing N logged nights** "
        "(not calendar days — logging gaps don't stretch the window). "
        "SRI and SD metrics need ≥ 2 consecutive/overlapping nights in the window."
    )

    col_sel, col_win = st.columns([3, 1])
    with col_sel:
        metric_label = st.selectbox("Metric", list(SLEEP_METRIC_LABELS.keys()), index=0)
    with col_win:
        max_win = max(5, len(timing_df))
        default_win = min(SLEEP_ROLL_WINDOW, len(timing_df))
        window = st.slider("Window (nights)", min_value=2, max_value=max_win, value=default_win)

    metric_col = SLEEP_METRIC_LABELS[metric_label]

    # Build rolling series — call sleep_consistency_metrics on each window slice
    roll_rows = []
    for i in range(len(timing_df)):
        start = max(0, i - window + 1)
        w_df = timing_df.iloc[start : i + 1]
        val = sleep_consistency_metrics(w_df)[metric_col]
        roll_rows.append({"date": timing_df.iloc[i]["date"], "value": val})
    roll_df = pd.DataFrame(roll_rows)
    valid_roll = roll_df[roll_df["value"].notna() & roll_df["value"].apply(
        lambda v: not math.isnan(v)
    )]

    fig_roll = go.Figure()
    if not valid_roll.empty:
        fig_roll.add_scatter(
            x=valid_roll["date"], y=valid_roll["value"],
            mode="lines+markers",
            name=metric_label,
            marker=dict(size=6, color="#4CE87A"),
            line=dict(width=2, color="#4CE87A"),
        )
    _add_intervention_vlines(fig_roll, ivn_df)
    fig_roll.update_layout(
        yaxis=dict(title=metric_label),
        height=340,
        margin=dict(l=50, r=20, t=10, b=40),
    )
    st.plotly_chart(fig_roll, width="stretch")

    if metric_label == "Sleep Regularity Index (0–100)":
        st.caption(
            "SRI is built from the in-bed interval (bed → wake), which is our best proxy "
            "for sleep given that we don't have minute-level awakening data. Naps are not "
            "included. Scores ≥ 87 are associated with better health outcomes in research."
        )
    elif metric_label == "Social Jetlag (h)":
        st.caption(
            "Social jetlag = difference between mid-sleep on work days vs free days. "
            "< 1 h is generally considered low; ≥ 2 h is associated with impaired "
            "metabolic and performance outcomes."
        )

    st.divider()

    # ── 3. Period comparison ─────────────────────────────────────────────────
    st.subheader("Period A vs Period B comparison")
    st.caption(
        "Select two date ranges to compare sleep consistency before and after an "
        "intervention. Defaults to the equal-length window before/after the latest "
        "logged intervention."
    )

    min_date = timing_df["date"].min().date()
    max_date = timing_df["date"].max().date()

    # Smart defaults: split around the latest intervention; fall back to halves
    if not ivn_df.empty:
        pivot = pd.Timestamp(ivn_df["date"].max()).date()
        nights_b = timing_df[timing_df["date"].dt.date >= pivot]
        nights_a_pool = timing_df[timing_df["date"].dt.date < pivot]
        nights_a = nights_a_pool.tail(len(nights_b)) if len(nights_a_pool) >= len(nights_b) else nights_a_pool
        default_a = (
            nights_a["date"].min().date() if not nights_a.empty else min_date,
            nights_a["date"].max().date() if not nights_a.empty else pivot,
        )
        default_b = (pivot, max_date)
    else:
        mid_idx = len(timing_df) // 2
        pivot_date = timing_df["date"].iloc[mid_idx].date()
        default_a = (min_date, pivot_date)
        default_b = (pivot_date, max_date)

    col_a, col_b = st.columns(2)
    with col_a:
        st.markdown("**Period A (before)**")
        a_range = st.date_input(
            "Period A dates",
            value=default_a,
            min_value=min_date, max_value=max_date,
            key="sleep_period_a", label_visibility="collapsed",
        )
    with col_b:
        st.markdown("**Period B (after)**")
        b_range = st.date_input(
            "Period B dates",
            value=default_b,
            min_value=min_date, max_value=max_date,
            key="sleep_period_b", label_visibility="collapsed",
        )

    if len(a_range) != 2 or len(b_range) != 2:
        st.info("Select a start and end date for both periods to see the comparison.")
        return

    a_start, a_end = pd.Timestamp(a_range[0]), pd.Timestamp(a_range[1])
    b_start, b_end = pd.Timestamp(b_range[0]), pd.Timestamp(b_range[1])

    timing_a = timing_df[(timing_df["date"] >= a_start) & (timing_df["date"] <= a_end)]
    timing_b = timing_df[(timing_df["date"] >= b_start) & (timing_df["date"] <= b_end)]

    m_a = sleep_consistency_metrics(timing_a)
    m_b = sleep_consistency_metrics(timing_b)

    n_a, n_b = m_a["n_nights"], m_b["n_nights"]
    if n_a == 0 or n_b == 0:
        st.warning("One of the selected periods has no nights with bed/wake time logged. Adjust the date ranges.")
        return

    rows = []
    for label, key in SLEEP_METRIC_LABELS.items():
        va, vb = m_a[key], m_b[key]
        delta = (vb - va) if (not math.isnan(va) and not math.isnan(vb)) else float("nan")
        rows.append({
            "Metric": label,
            f"Period A ({n_a}n)": _fmt(key, va),
            f"Period B ({n_b}n)": _fmt(key, vb),
            "Δ (B − A)": _fmt_delta(key, delta),
        })

    st.dataframe(pd.DataFrame(rows), hide_index=True, width='stretch')
    st.caption(
        "✅ = improvement · ⚠️ = worsening. "
        "**SRI** and **Mean Efficiency**: higher → better. "
        "All **SD** and **Social Jetlag** metrics: lower → better. "
        f"'n' = number of nights with bed/wake times logged in that period."
    )
