# src/market/seasonality.py
"""
IHSG monthly base rates.

**Why `period="max"` and not the 2-year panel.** Two years gives roughly two
observations per calendar month. A "hit rate" computed from two data points is not
a base rate, it is noise wearing a percentage sign. Full history gives ~30 years,
which is still thin -- and `n` is carried on every row so the reader can see how
thin rather than having to assume.

This is context, never a rule. A 55% hit rate over 31 Augusts is not an edge; it is
a reason not to be surprised. The renderer says so in the same breath as the number.
"""
from __future__ import annotations

import calendar
from typing import Optional

import numpy as np
import pandas as pd

SEASONALITY_COLS = ["month", "month_name", "mean_pct", "median_pct", "hit_rate", "n"]

# Below this many observations the month is reported but explicitly called unreliable.
MIN_OBSERVATIONS = 10


def _month_end_rule() -> str:
    """pandas >= 2.2 renamed the month-end alias; 3.x removed the old one."""
    return "ME" if pd.__version__ >= "2.2" else "M"


def monthly_returns(prices: pd.Series) -> pd.Series:
    if prices is None or len(prices) < 2:
        return pd.Series(dtype=float)
    clean = prices.dropna()
    if clean.empty:
        return pd.Series(dtype=float)
    clean.index = pd.to_datetime(clean.index)
    return clean.resample(_month_end_rule()).last().pct_change().dropna() * 100


def monthly_seasonality(prices: pd.Series) -> pd.DataFrame:
    """One row per calendar month: mean, median, hit rate, and the sample size."""
    returns = monthly_returns(prices)
    if returns.empty:
        return pd.DataFrame(columns=SEASONALITY_COLS)

    frame = pd.DataFrame({"ret": returns.values}, index=pd.to_datetime(returns.index))
    frame["month"] = frame.index.month

    rows = []
    for month, grp in frame.groupby("month"):
        rows.append({
            "month": int(month),
            "month_name": calendar.month_name[int(month)],
            "mean_pct": round(float(grp["ret"].mean()), 2),
            "median_pct": round(float(grp["ret"].median()), 2),
            "hit_rate": round(float((grp["ret"] > 0).mean() * 100), 1),
            "n": int(len(grp)),
        })
    return pd.DataFrame(rows, columns=SEASONALITY_COLS).sort_values("month").reset_index(drop=True)


def for_month(table: pd.DataFrame, month: Optional[int] = None) -> Optional[dict]:
    if table is None or table.empty:
        return None
    month = month or pd.Timestamp.today().month
    match = table[table["month"] == month]
    return None if match.empty else match.iloc[0].to_dict()


def describe(row: Optional[dict]) -> str:
    """
    One line for the brief, with the sample size and an explicit reliability note.

    The caveat is not decoration. Without it a bare "+0.4% average, 55% positive"
    reads like a signal, and at n=31 it is not one.
    """
    if not row:
        return "No seasonality data available."

    n = int(row["n"])
    base = (f"{row['month_name']}: {row['mean_pct']:+.1f}% average, "
            f"{row['hit_rate']:.0f}% of years positive, n={n}")
    if n < MIN_OBSERVATIONS:
        return f"{base} - too few years to mean anything."
    return f"{base} - weak evidence, context only."
