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
    sleep_consistency_metrics,
    sleep_regularity_index,
    sleep_timing,
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


# ── sleep_timing ──────────────────────────────────────────────────────────────

def _make_timing_input(**overrides):
    """Minimal two-row check-in DataFrame for sleep tests."""
    base = dict(
        date=pd.to_datetime(["2026-05-07", "2026-05-08"]),
        bed_time=["22:45:00", "23:00:00"],
        awake_time=["06:00:00", "06:30:00"],
        sleep_hours=[7.0, 6.5],
        work_hours=[8.0, 0.0],
    )
    base.update(overrides)
    return pd.DataFrame(base)


def test_sleep_timing_midnight_wrap():
    """02:30 bed time (hour < 12) must be noon-anchored to 26.5."""
    df = pd.DataFrame({
        "date": pd.to_datetime(["2026-05-10"]),
        "bed_time": ["02:30:00"],
        "awake_time": ["09:00:00"],
        "sleep_hours": [6.0],
        "work_hours": [0.0],
    })
    t = sleep_timing(df)
    assert t["bedtime_h"].iloc[0] == pytest.approx(26.5)


def test_sleep_timing_evening_no_wrap():
    """22:45 bed time (hour ≥ 12) stays at 22.75 — no wrap applied."""
    df = pd.DataFrame({
        "date": pd.to_datetime(["2026-05-08"]),
        "bed_time": ["22:45:00"],
        "awake_time": ["06:00:00"],
        "sleep_hours": [7.0],
        "work_hours": [8.0],
    })
    t = sleep_timing(df)
    assert t["bedtime_h"].iloc[0] == pytest.approx(22.75)
    assert t["waketime_h"].iloc[0] == pytest.approx(6.0)


def test_sleep_timing_time_in_bed():
    """22:45 bed → 06:00 wake is 7.25 h in bed."""
    df = pd.DataFrame({
        "date": pd.to_datetime(["2026-05-08"]),
        "bed_time": ["22:45:00"],
        "awake_time": ["06:00:00"],
        "sleep_hours": [7.0],
        "work_hours": [8.0],
    })
    t = sleep_timing(df)
    assert t["time_in_bed_h"].iloc[0] == pytest.approx(7.25)


def test_sleep_timing_efficiency():
    """Efficiency = sleep_hours / time_in_bed_h (< 1 when awake in bed)."""
    df = pd.DataFrame({
        "date": pd.to_datetime(["2026-05-08"]),
        "bed_time": ["22:45:00"],
        "awake_time": ["06:00:00"],
        "sleep_hours": [6.5],
        "work_hours": [8.0],
    })
    t = sleep_timing(df)
    assert t["sleep_efficiency"].iloc[0] == pytest.approx(6.5 / 7.25)


def test_sleep_timing_drops_missing():
    """Rows with a blank bed_time or awake_time are silently dropped."""
    df = pd.DataFrame({
        "date": pd.to_datetime(["2026-05-07", "2026-05-08"]),
        "bed_time": [float("nan"), "22:45:00"],
        "awake_time": ["06:00:00", "06:00:00"],
        "sleep_hours": [7.0, 7.0],
        "work_hours": [8.0, 8.0],
    })
    t = sleep_timing(df)
    assert len(t) == 1


def test_sleep_timing_midnight_close_to_next_evening():
    """23:00 and 01:00 should be ~2 h apart, not ~22 h apart."""
    df = pd.DataFrame({
        "date": pd.to_datetime(["2026-05-07", "2026-05-08"]),
        "bed_time": ["23:00:00", "01:00:00"],
        "awake_time": ["06:00:00", "08:00:00"],
        "sleep_hours": [7.0, 7.0],
        "work_hours": [8.0, 0.0],
    })
    t = sleep_timing(df)
    diff = abs(t["bedtime_h"].iloc[1] - t["bedtime_h"].iloc[0])
    assert diff == pytest.approx(2.0)  # 25.0 - 23.0 = 2 h, not 22 h


# ── sleep_regularity_index ───────────────────────────────────────────────────

def test_sleep_regularity_index_perfect():
    """Identical schedule three nights in a row → SRI = 100."""
    df = pd.DataFrame({
        "date": pd.to_datetime(["2026-05-07", "2026-05-08", "2026-05-09"]),
        "bed_time": ["23:00:00"] * 3,
        "awake_time": ["06:30:00"] * 3,
        "sleep_hours": [7.0] * 3,
        "work_hours": [8.0] * 3,
    })
    t = sleep_timing(df)
    assert sleep_regularity_index(t) == pytest.approx(100.0)


def test_sleep_regularity_index_shifted():
    """A 4-hour bedtime shift between consecutive nights → SRI < 100."""
    df = pd.DataFrame({
        "date": pd.to_datetime(["2026-05-07", "2026-05-08"]),
        "bed_time": ["22:00:00", "02:00:00"],
        "awake_time": ["06:00:00", "10:00:00"],
        "sleep_hours": [8.0, 8.0],
        "work_hours": [8.0, 0.0],
    })
    t = sleep_timing(df)
    assert sleep_regularity_index(t) < 100.0


def test_sleep_regularity_index_non_consecutive_skipped():
    """A 3-day gap between the only two logged nights → no pairs → NaN."""
    df = pd.DataFrame({
        "date": pd.to_datetime(["2026-05-07", "2026-05-10"]),
        "bed_time": ["23:00:00", "23:00:00"],
        "awake_time": ["06:30:00", "06:30:00"],
        "sleep_hours": [7.0, 7.0],
        "work_hours": [8.0, 0.0],
    })
    t = sleep_timing(df)
    assert math.isnan(sleep_regularity_index(t))


def test_sleep_regularity_index_single_night_nan():
    """Cannot compute SRI from a single night."""
    df = pd.DataFrame({
        "date": pd.to_datetime(["2026-05-07"]),
        "bed_time": ["23:00:00"],
        "awake_time": ["06:00:00"],
        "sleep_hours": [7.0],
        "work_hours": [8.0],
    })
    t = sleep_timing(df)
    assert math.isnan(sleep_regularity_index(t))


# ── sleep_consistency_metrics ────────────────────────────────────────────────

def test_sleep_consistency_metrics_social_jetlag():
    """Social jetlag = |mean mid-sleep(free) − mean mid-sleep(work)|."""
    df = pd.DataFrame({
        "date": pd.to_datetime(["2026-05-07", "2026-05-08"]),
        "bed_time": ["23:00:00", "01:00:00"],   # work day early, free day late
        "awake_time": ["06:00:00", "09:00:00"],
        "sleep_hours": [7.0, 8.0],
        "work_hours": [8.0, 0.0],
    })
    t = sleep_timing(df)
    m = sleep_consistency_metrics(t)
    # Work: bed=23.0, TIB=7h, mid=26.5
    # Free: bed=25.0 (01:00 + 24), TIB=8h, mid=29.0
    # Social jetlag = |29.0 - 26.5| = 2.5 h
    assert m["social_jetlag"] == pytest.approx(2.5)


def test_sleep_consistency_metrics_empty():
    """Empty input returns all NaN with n_nights=0."""
    t = pd.DataFrame(columns=["date", "bedtime_h", "waketime_h", "mid_sleep_h",
                               "time_in_bed_h", "sleep_hours", "sleep_efficiency", "is_work_day"])
    m = sleep_consistency_metrics(t)
    assert m["n_nights"] == 0
    assert math.isnan(m["sri"])
    assert math.isnan(m["social_jetlag"])


def test_sleep_consistency_metrics_sd_units():
    """SD metrics are returned in minutes (not hours)."""
    df = pd.DataFrame({
        "date": pd.to_datetime(["2026-05-07", "2026-05-08"]),
        "bed_time": ["22:00:00", "23:00:00"],   # 1-hour SD → 60 min
        "awake_time": ["06:00:00", "06:00:00"],
        "sleep_hours": [8.0, 7.0],
        "work_hours": [8.0, 8.0],
    })
    t = sleep_timing(df)
    m = sleep_consistency_metrics(t)
    # SD of bedtime: std([22.0, 23.0]) * 60 ≈ std_ddof1([22,23]) * 60 = 0.7071… * 60 ≈ 42.43
    assert m["sd_bedtime"] == pytest.approx(pd.Series([22.0, 23.0]).std() * 60, rel=1e-4)


# ── trend_line reused for the rolling-sleep predictor ─────────────────────────
def test_trend_line_rolling_sleep_alignment():
    dates = pd.Series(pd.to_datetime(["2026-01-01", "2026-01-02", "2026-01-03"]))
    sleep_hours = pd.Series([8.0, 6.0, 7.0])
    rolling = trend_line(dates, sleep_hours, window=2)
    # min_periods=1, so day 1 is just itself; day 2/3 average with the prior day.
    assert rolling.to_list() == pytest.approx([8.0, 7.0, 6.5])
    # Index stays date-aligned so it can be merged back onto training days by date.
    assert list(rolling.index) == list(pd.to_datetime(["2026-01-01", "2026-01-02", "2026-01-03"]))
