"""Pure computation for the powerlifting dashboard — no Streamlit, no DB.

Everything here is a deterministic function of its arguments, which makes it
unit-testable in isolation (see tests/test_calculations.py). The Streamlit/Plotly
presentation lives in the tabs/ package; the cached DB loaders live in lib/data.py.
"""

import numpy as np
import pandas as pd

from lib.constants import RTS_TABLE, NUCKOLS_COEF


# ── e1RM estimation ──────────────────────────────────────────────────────────
def estimate_e1rm(row) -> float | None:
    """Estimate a 1RM from a single working set.

    1 rep      -> RTS RPE table (defaults to RPE 9 if missing, snapped/clamped).
    2–5 reps   -> Epley: weight * (1 + reps/30).
    6+ reps    -> None (not reliable for estimating 1RM).
    """
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


# ── Competition score formulas ───────────────────────────────────────────────
def dots_score(total: float, bw: float) -> float:
    a, b, c, d, e = -0.000001093, 0.0007391293, -0.1918209091, 24.0900756, -307.75076
    coeff = 500 / (a*bw**4 + b*bw**3 + c*bw**2 + d*bw + e)
    return round(total * coeff, 1)


def trend_line(dates, values, window=4):
    """Rolling mean for trend overlay."""
    s = pd.Series(values.to_numpy(), index=dates.to_numpy()).sort_index()
    return s.rolling(window, min_periods=1).mean()


# ── Physique calculator formulas ─────────────────────────────────────────────
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
