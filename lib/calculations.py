"""Pure computation for the powerlifting dashboard — no Streamlit, no DB.

Everything here is a deterministic function of its arguments, which makes it
unit-testable in isolation (see tests/test_calculations.py). The Streamlit/Plotly
presentation lives in the tabs/ package; the cached DB loaders live in lib/data.py.
"""

import numpy as np
import pandas as pd
import statsmodels.api as sm

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


# ── Recovery-tab performance/predictor features ──────────────────────────────
def relative_e1rm(session_df: pd.DataFrame, window: int = 7, min_periods: int = 3) -> pd.Series:
    """Per-lift performance relative to that lift's own recent baseline: each
    session's e1RM divided by the trailing mean of its *previous* `window`
    sessions for the same exercise (NaN until `min_periods` prior sessions
    exist). This makes performance comparable across lifts (a deadlift e1RM
    isn't on the same scale as a bench e1RM) and detrends the slow upward
    e1RM trend over a training cycle. >1 = above recent baseline, <1 = below.

    `session_df` must have one row per (date, Exercise) session with columns
    "date", "Exercise", "e1rm" — e.g. the `session` frame from
    lib.data.load_training. Returned Series is aligned to session_df's index.
    """
    df = session_df.sort_values("date")
    baseline = (
        df.groupby("Exercise", group_keys=False)["e1rm"]
        .apply(lambda s: s.shift(1).rolling(window, min_periods=min_periods).mean())
    )
    return (df["e1rm"] / baseline).reindex(session_df.index)


def acwr(dates, loads, acute_window: int = 7, chronic_window: int = 28, method: str = "ra") -> pd.Series:
    """Acute:chronic workload ratio — a sports-science readiness/fatigue proxy.
    Acute = recent training load (last `acute_window` days), chronic = the
    longer-run baseline (last `chronic_window` days, expressed as the same
    daily-equivalent scale). ~0.8-1.3 = load matches recent fitness; >1.5 =
    load spike (elevated fatigue/injury risk); <0.8 = undertrained relative to
    recent baseline.

    `loads` (e.g. daily Σ weight_kg * reps) is reindexed to one row per
    calendar day between min/max date with 0 on untrained days, since rest
    days are part of both the acute and chronic window, not gaps to skip.
    `method`: "ra" for rolling average (default) or "ewm" for exponentially
    weighted (more sensitive to recent days). Returns a daily-indexed Series.
    """
    s = pd.Series(np.asarray(loads, dtype=float), index=pd.to_datetime(dates)).sort_index()
    full_idx = pd.date_range(s.index.min(), s.index.max(), freq="D")
    s = s.reindex(full_idx, fill_value=0.0)

    if method == "ewm":
        acute = s.ewm(span=acute_window, adjust=False).mean()
        chronic = s.ewm(span=chronic_window, adjust=False).mean()
    else:
        acute = s.rolling(acute_window, min_periods=1).mean()
        chronic = s.rolling(chronic_window, min_periods=1).mean()

    return acute / chronic.replace(0, np.nan)


def ridge_standardized_coefs(X: pd.DataFrame, y: pd.Series, alpha: float = 1.0) -> pd.Series:
    """Each predictor's *unique* contribution to y, holding the others
    constant: a ridge (L2-penalized) regression fit on z-scored X and y, so
    coefficients are directly comparable across predictors with different
    units/scales. Unlike univariate correlation, this controls for
    inter-predictor correlation (e.g. sleep and fatigue moving together).

    Rows with any NaN in X or y are dropped first — listwise deletion is
    correct here (a joint model needs every predictor for every row), unlike
    the pairwise-complete correlations used elsewhere in the recovery tab.
    Returns one coefficient per column of X (intercept dropped).
    """
    data = X.copy()
    data["__y__"] = y
    data = data.dropna()

    y_std = (data["__y__"] - data["__y__"].mean()) / data["__y__"].std(ddof=0)
    X_std = (data[X.columns] - data[X.columns].mean()) / data[X.columns].std(ddof=0)

    design = sm.add_constant(X_std)
    model = sm.OLS(y_std, design).fit_regularized(alpha=alpha, L1_wt=0.0)
    # fit_regularized doesn't always preserve the pandas index on .params, so
    # rebuild it explicitly from the design matrix's columns.
    params = pd.Series(model.params, index=design.columns)
    return params.drop("const")


# ── Sleep consistency (Tab 5) ────────────────────────────────────────────────

def sleep_timing(checkin_df: pd.DataFrame) -> pd.DataFrame:
    """Parse bed/wake clock times into per-night derived quantities.

    Returns a DataFrame with one row per night where both bed_time and awake_time
    are logged and parseable. All other rows are dropped.

    Midnight-wrap (the key subtlety): bedtimes are noon-anchored — if the raw
    hour < 12 (i.e. a post-midnight bedtime), 24 is added. So 00:30 → 24.5 and
    02:00 → 26.0. This keeps evening and early-morning bedtimes on a continuous
    linear scale for SD and mid-sleep calculations, without circular-statistics
    complexity. Wake times are always morning hours, no wrap needed.

    Columns returned:
        date            – the check-in date (Timestamp)
        bedtime_h       – noon-anchored bedtime in hours (~18–30 range)
        waketime_h      – wake time in hours (~4–10 range)
        mid_sleep_h     – midpoint of the in-bed interval (noon-anchored)
        time_in_bed_h   – total time in bed (hours)
        sleep_hours     – actual sleep duration from the check-in (≤ TIB)
        sleep_efficiency – sleep_hours / time_in_bed_h, clipped to [0, 1]
        is_work_day     – True when work_hours > 0
    """
    cols = [c for c in ("date", "bed_time", "awake_time", "sleep_hours", "work_hours")
            if c in checkin_df.columns]
    df = checkin_df[cols].copy()

    df = df[df["bed_time"].notna() & df["awake_time"].notna()]
    df = df[df["bed_time"].astype(str).str.strip() != ""]
    df = df[df["awake_time"].astype(str).str.strip() != ""]

    def _parse_h(t) -> float:
        """'HH:MM' or 'HH:MM:SS' → fractional hours; NaN on failure."""
        try:
            parts = str(t).strip().split(":")
            return int(parts[0]) + int(parts[1]) / 60
        except (ValueError, IndexError, TypeError):
            return float("nan")

    df = df.copy()
    df["_bed_raw"] = df["bed_time"].map(_parse_h)
    df["_wake_raw"] = df["awake_time"].map(_parse_h)
    df = df[df["_bed_raw"].notna() & df["_wake_raw"].notna()].copy()

    # Noon-anchor: post-midnight bedtimes (hour < 12) get +24h
    df["bedtime_h"] = df["_bed_raw"].where(df["_bed_raw"] >= 12, df["_bed_raw"] + 24)
    df["waketime_h"] = df["_wake_raw"]

    df["time_in_bed_h"] = (df["waketime_h"] + 24) - df["bedtime_h"]
    df["mid_sleep_h"] = df["bedtime_h"] + df["time_in_bed_h"] / 2

    sh = df.get("sleep_hours", pd.Series(dtype=float))
    df["sleep_efficiency"] = (sh / df["time_in_bed_h"]).clip(upper=1.0)
    wh = df["work_hours"] if "work_hours" in df.columns else pd.Series(0, index=df.index)
    df["is_work_day"] = wh.fillna(0) > 0

    return df[[
        "date", "bedtime_h", "waketime_h", "mid_sleep_h",
        "time_in_bed_h", "sleep_hours", "sleep_efficiency", "is_work_day",
    ]].reset_index(drop=True)


def sleep_regularity_index(timing_df: pd.DataFrame, epoch_min: int = 5) -> float:
    """Sleep Regularity Index (SRI): 0 (random) to 100 (perfectly regular).

    For each pair of calendar-adjacent nights (date gap exactly 1 day), builds a
    binary in-bed/out-of-bed vector at `epoch_min`-minute resolution over a fixed
    noon-to-noon 24h window, then counts concordant epochs. Pairs where either
    night is missing are skipped (not counted against regularity).

        SRI = 100 * (2 * mean_concordance − 1)

    0 corresponds to 50% concordance (pure random), 100 to 100% concordance
    (identical schedule every night). Non-consecutive night pairs are excluded so
    a missed logging day doesn't drag SRI down.

    Caveat: the in-bed interval (bed_time → wake_time) is used as a proxy for
    sleep — we don't have minute-level awakening data, so within-night
    fragmentation is invisible to this metric.
    """
    if len(timing_df) < 2:
        return float("nan")

    epochs = (24 * 60) // epoch_min  # 288 for 5-min epochs

    def _vec(row) -> np.ndarray:
        v = np.zeros(epochs, dtype=np.int8)
        # Offset from noon (epoch 0 = 12:00, epoch 1 = 12:05, ...)
        bed_e = int((row["bedtime_h"] - 12) * 60 / epoch_min)
        wake_e = int(((row["waketime_h"] + 24) - 12) * 60 / epoch_min)
        b = max(0, min(bed_e, epochs - 1))
        w = max(0, min(wake_e, epochs))
        if b < w:
            v[b:w] = 1
        return v

    df_s = timing_df.sort_values("date").reset_index(drop=True)
    dates = pd.to_datetime(df_s["date"])
    vecs = [_vec(row) for _, row in df_s.iterrows()]

    concordances = []
    for i in range(len(df_s) - 1):
        if (dates.iloc[i + 1] - dates.iloc[i]).days == 1:
            concordances.append(float((vecs[i] == vecs[i + 1]).mean()))

    if not concordances:
        return float("nan")

    return float(100 * (2 * np.mean(concordances) - 1))


def sleep_consistency_metrics(timing_df: pd.DataFrame) -> dict:
    """Full sleep consistency metric bundle over a set of nights.

    Keys match lib.constants.SLEEP_METRIC_LABELS plus 'n_nights'. Values are
    floats (NaN when insufficient data). Timing/duration SDs are in minutes;
    social jetlag in hours; efficiency in %.
    """
    n = len(timing_df)
    nan = float("nan")
    if n == 0:
        return dict(sri=nan, social_jetlag=nan, sd_mid_sleep=nan,
                    sd_bedtime=nan, sd_waketime=nan, sd_duration=nan,
                    mean_efficiency=nan, sd_efficiency=nan, n_nights=0)

    work = timing_df[timing_df["is_work_day"]]
    free = timing_df[~timing_df["is_work_day"]]
    if not work.empty and not free.empty:
        social_jetlag = float(abs(free["mid_sleep_h"].mean() - work["mid_sleep_h"].mean()))
    else:
        social_jetlag = nan

    h2m = 60  # hours → minutes
    sd = lambda col: float(timing_df[col].std()) * h2m if n >= 2 else nan  # noqa: E731

    return {
        "sri":             sleep_regularity_index(timing_df),
        "social_jetlag":   social_jetlag,
        "sd_mid_sleep":    sd("mid_sleep_h"),
        "sd_bedtime":      sd("bedtime_h"),
        "sd_waketime":     sd("waketime_h"),
        "sd_duration":     float(timing_df["sleep_hours"].std() * h2m) if n >= 2 else nan,
        "mean_efficiency": float(timing_df["sleep_efficiency"].mean() * 100),
        "sd_efficiency":   float(timing_df["sleep_efficiency"].std() * 100) if n >= 2 else nan,
        "n_nights":        n,
    }


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
