"""Streamlit/Plotly-aware UI helpers shared across tabs/.

Not pure (renders widgets, touches st.session_state via widget keys) — this
is why it's separate from lib/calculations.py, which stays a deterministic,
unit-testable layer with no Streamlit dependency.
"""

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

# Single date format used by every date-range slider in the app (unambiguous,
# unlike the "MMM YYYY" / "DD MMM" formats this replaces).
DATE_FORMAT = "YYYY-MM-DD"

HOVER_KG = "%{x|%d %b %Y}<br>%{y:.1f} kg"


def date_range_slider(df: pd.DataFrame, label: str, key: str | None = None, date_col: str = "date"):
    """Build a (start_date, end_date) picker from df[date_col].min()/.max().

    st.slider raises StreamlitAPIException when min_value == max_value, so
    when df has fewer than 2 distinct dates this renders an st.caption
    instead of a slider and returns (d, d) directly.
    """
    if df.empty:
        st.caption("No data yet — nothing to filter.")
        return None, None

    dates = df[date_col]
    if dates.nunique() < 2:
        d = dates.min().date()
        st.caption("Single date on record — no range to filter.")
        return d, d

    min_date = dates.min().date()
    max_date = dates.max().date()
    return st.slider(
        label,
        min_value=min_date,
        max_value=max_date,
        value=(min_date, max_date),
        format=DATE_FORMAT,
        key=key,
    )


def filter_by_range(df: pd.DataFrame, start, end, date_col: str = "date") -> pd.DataFrame:
    """The repeated (df[col].dt.date >= start) & (<= end) mask, applied."""
    mask = (df[date_col].dt.date >= start) & (df[date_col].dt.date <= end)
    return df[mask]


def apply_default_layout(fig: go.Figure, **overrides) -> go.Figure:
    """The repeated legend/height/margin/hovermode block, with per-call overrides
    (e.g. sleep.py's clock-time yaxis, taller/shorter heights)."""
    layout = dict(
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        height=480,
        margin=dict(l=40, r=20, t=10, b=40),
        hovermode="x unified",
    )
    layout.update(overrides)
    fig.update_layout(**layout)
    return fig
