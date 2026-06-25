"""Tab 4 — Physique Calculators: FFMI, target body-composition planner, Casey
Butt max muscular potential, Nuckols powerlifting efficiency, and projected
lifts at target/max FFM. Ported from data/body_measurement_calculators.xlsx."""

import pandas as pd
import streamlit as st

from lib.calculations import (
    casey_butt_max_ffm,
    dots_score,
    ffm,
    ffmi_normalized,
    ffmi_rating,
    ffmi_raw,
    nuckols_predicted,
    target_ffm_for_ffmi,
)
from lib.constants import ANKLE_CM, HEIGHT_CM, WRIST_CM


def render(
    session_df: pd.DataFrame,
    latest_weight,
    latest_weight_date,
    latest_bf,
    latest_bf_date,
) -> None:
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
