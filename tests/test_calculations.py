"""Unit tests for lib/calculations.py — pure math, no Streamlit/DB involved.

Expected values are hand-derived from the formulas documented in each
function's docstring (and, for the physique calculators, cross-checked
against data/body_measurement_calculators.xlsx per PROJECT_LOG.md).
"""

import math

import pandas as pd
import pytest

from lib.calculations import (
    acwr,
    casey_butt_max_ffm,
    dots_score,
    estimate_e1rm,
    ffm,
    ffmi_normalized,
    ffmi_rating,
    ffmi_raw,
    nuckols_predicted,
    relative_e1rm,
    ridge_standardized_coefs,
    target_ffm_for_ffmi,
    trend_line,
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


# ── relative_e1rm (Tab 2 outcome) ─────────────────────────────────────────────
def test_relative_e1rm_baseline_math():
    # Single lift, 4 sessions, window=2 prior sessions, min_periods=2.
    # Session 3 (e1rm=105): baseline = mean(e1rm[0], e1rm[1]) = mean(100, 110) = 105 -> 105/105 = 1.0
    # Session 4 (e1rm=120): baseline = mean(e1rm[1], e1rm[2]) = mean(110, 105) = 107.5 -> 120/107.5
    df = pd.DataFrame({
        "date": pd.to_datetime(["2026-01-01", "2026-01-03", "2026-01-05", "2026-01-07"]),
        "Exercise": ["Squat"] * 4,
        "e1rm": [100.0, 110.0, 105.0, 120.0],
    })
    result = relative_e1rm(df, window=2, min_periods=2)
    assert math.isnan(result.iloc[0])
    assert math.isnan(result.iloc[1])
    assert result.iloc[2] == pytest.approx(1.0)
    assert result.iloc[3] == pytest.approx(120.0 / 107.5)


def test_relative_e1rm_per_lift_independent():
    # Two lifts interleaved — each lift's baseline must only use its own history.
    df = pd.DataFrame({
        "date": pd.to_datetime(["2026-01-01", "2026-01-02", "2026-01-03", "2026-01-04"]),
        "Exercise": ["Squat", "Bench Press", "Squat", "Bench Press"],
        "e1rm": [100.0, 50.0, 110.0, 55.0],
    })
    result = relative_e1rm(df, window=1, min_periods=1)
    assert math.isnan(result.iloc[0])  # Squat, no prior squat session
    assert math.isnan(result.iloc[1])  # Bench, no prior bench session
    assert result.iloc[2] == pytest.approx(110.0 / 100.0)  # Squat vs prior squat only
    assert result.iloc[3] == pytest.approx(55.0 / 50.0)    # Bench vs prior bench only


# ── acwr (Tab 2 engineered predictor) ─────────────────────────────────────────
def test_acwr_rolling_average():
    dates = pd.date_range("2026-01-01", periods=5, freq="D")
    loads = [10, 0, 0, 0, 0]
    result = acwr(dates, loads, acute_window=2, chronic_window=4, method="ra")
    assert result.to_list()[:4] == pytest.approx([1.0, 1.0, 0.0, 0.0])
    assert math.isnan(result.to_list()[4])  # chronic window all-zero -> division guarded to NaN


def test_acwr_fills_untrained_days_with_zero():
    # Gap day (Jan 2) is missing from input but must still count as a 0-load
    # rest day in both windows, not be skipped.
    dates = pd.to_datetime(["2026-01-01", "2026-01-03"])
    loads = [10, 10]
    result = acwr(dates, loads, acute_window=7, chronic_window=7, method="ra")
    assert len(result) == 3  # Jan 1, 2, 3 — Jan 2 filled in


# ── ridge_standardized_coefs (Tab 2 multivariate view) ────────────────────────
def test_ridge_standardized_coefs_favors_the_correlated_predictor():
    # x1 perfectly tracks y; x2 is shuffled/unrelated. The standardized ridge
    # coefficient for x1 should clearly dominate x2's.
    X = pd.DataFrame({
        "x1": [1, 2, 3, 4, 5, 6, 7, 8, 9, 10],
        "x2": [5, 3, 8, 1, 9, 2, 7, 4, 10, 6],
    })
    y = pd.Series([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], dtype=float)
    coefs = ridge_standardized_coefs(X, y, alpha=1.0)
    assert coefs["x1"] == pytest.approx(0.4887280, abs=1e-5)
    assert coefs["x2"] == pytest.approx(0.0759158, abs=1e-5)
    assert coefs["x1"] > coefs["x2"]


# ── trend_line reused for the rolling-sleep predictor ─────────────────────────
def test_trend_line_rolling_sleep_alignment():
    dates = pd.Series(pd.to_datetime(["2026-01-01", "2026-01-02", "2026-01-03"]))
    sleep_hours = pd.Series([8.0, 6.0, 7.0])
    rolling = trend_line(dates, sleep_hours, window=2)
    # min_periods=1, so day 1 is just itself; day 2/3 average with the prior day.
    assert rolling.to_list() == pytest.approx([8.0, 7.0, 6.5])
    # Index stays date-aligned so it can be merged back onto training days by date.
    assert list(rolling.index) == list(pd.to_datetime(["2026-01-01", "2026-01-02", "2026-01-03"]))
