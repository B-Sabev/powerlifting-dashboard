"""Unit tests for lib/calculations.py — pure math, no Streamlit/DB involved.

Expected values are hand-derived from the formulas documented in each
function's docstring (and, for the physique calculators, cross-checked
against data/body_measurement_calculators.xlsx per PROJECT_LOG.md).
"""

import math

import pytest

from lib.calculations import (
    casey_butt_max_ffm,
    dots_score,
    estimate_e1rm,
    ffm,
    ffmi_normalized,
    ffmi_rating,
    ffmi_raw,
    nuckols_predicted,
    target_ffm_for_ffmi,
)


# ── estimate_e1rm ─────────────────────────────────────────────────────────────
def test_estimate_e1rm_single_rep_with_rpe():
    row = {"Completed Reps": 1, "weight_kg": 100.0, "Completed RPE": 9.0}
    # RTS_TABLE[(1, 9.0)] == 0.955 -> 100 / 0.955
    assert estimate_e1rm(row) == pytest.approx(104.71204188481676)


def test_estimate_e1rm_single_rep_missing_rpe_defaults_to_nine():
    row = {"Completed Reps": 1, "weight_kg": 100.0, "Completed RPE": float("nan")}
    assert estimate_e1rm(row) == pytest.approx(104.71204188481676)


def test_estimate_e1rm_two_to_five_reps_uses_epley():
    row = {"Completed Reps": 5, "weight_kg": 100.0, "Completed RPE": float("nan")}
    # Epley: weight * (1 + reps/30) = 100 * (1 + 5/30)
    assert estimate_e1rm(row) == pytest.approx(116.66666666666667)


def test_estimate_e1rm_six_plus_reps_returns_none():
    row = {"Completed Reps": 6, "weight_kg": 100.0, "Completed RPE": float("nan")}
    assert estimate_e1rm(row) is None


# ── dots_score ────────────────────────────────────────────────────────────────
def test_dots_score():
    assert dots_score(500.0, 80.0) == pytest.approx(344.6)


# ── FFMI calculators ──────────────────────────────────────────────────────────
def test_ffm():
    assert ffm(80.0, 15.0) == pytest.approx(68.0)


def test_ffmi_raw():
    f = ffm(80.0, 15.0)
    assert ffmi_raw(f, 175.0) == pytest.approx(22.20408163265306)


def test_ffmi_normalized():
    f = ffm(80.0, 15.0)
    assert ffmi_normalized(f, 175.0) == pytest.approx(22.50908163265306)


@pytest.mark.parametrize(
    "norm_ffmi, expected",
    [
        (17.0, "Below average"),
        (19.0, "Average"),
        (21.0, "Above average"),
        (22.5, "Excellent"),
        (24.0, "Superior (likely natural limit)"),
        (27.0, "Exceptional (potential enhancement)"),
    ],
)
def test_ffmi_rating_bands(norm_ffmi, expected):
    assert ffmi_rating(norm_ffmi) == expected


def test_target_ffm_for_ffmi():
    assert target_ffm_for_ffmi(27.0, 175.0) == pytest.approx(81.7534375)


# ── Casey Butt max FFM ────────────────────────────────────────────────────────
def test_casey_butt_max_ffm():
    assert casey_butt_max_ffm(175.0, 17.5, 24.5, 15.0) == pytest.approx(82.58294404754946)


# ── Nuckols prediction ────────────────────────────────────────────────────────
def test_nuckols_predicted_squat():
    # coef=611.19, intercept=-10.43 -> 611.19 * (70/175) + (-10.43)
    assert nuckols_predicted(70.0, 175.0, "Squat") == pytest.approx(234.046)
