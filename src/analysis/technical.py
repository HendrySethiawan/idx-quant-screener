# src/analysis/technical.py --revision 2
"""
Price-derived features.

Revision 2 adds the factors that were impossible before, because `_fetch_single`
was requesting a ~30-day window: 1/6/12-month momentum, realised volatility, and
median daily traded value (the liquidity gate).

Momentum uses the standard "skip the most recent month" construction (6-1, 12-1).
The last ~21 sessions are excluded because short-horizon reversal contaminates a
raw 6- or 12-month return.
"""
from __future__ import annotations

from typing import Dict

import numpy as np
import pandas as pd

_SESSIONS_1M = 21
_SESSIONS_6M = 126
_SESSIONS_12M = 252

_LATEST_KEYS = (
    "rsi_14", "ma5_slope", "ma20_slope", "price_change_pct", "return_20d",
    "mom_1m", "mom_6m", "mom_12m", "realized_vol", "median_daily_value_rp",
    "last_close", "volume_ratio",
)


def compute_indicators(
    df: pd.DataFrame,
    vol_window: int = 60,
    liquidity_window: int = 20,
) -> pd.DataFrame:
    df = df.copy()
    if "Close" in df.columns:
        # Kills today's partial/pre-market bar, which otherwise produces a bogus
        # one-session price change and a wild volume ratio.
        df = df[df["Close"].notna()]
    if df.empty:
        return df

    close = df["Close"]

    for period in (5, 20):
        df[f"MA_{period}"] = close.rolling(window=period).mean()

    delta = close.diff()
    gain = delta.where(delta > 0, 0).rolling(window=14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
    with np.errstate(divide="ignore", invalid="ignore"):
        rsi = 100 - (100 / (1 + gain / loss))
    # An average loss of zero is an unbroken run of gains, which is RSI 100 by
    # definition -- not a divide-by-zero to be nulled out. NaN during the warm-up
    # window survives both masks, since NaN comparisons are False.
    rsi = rsi.where(loss != 0, 100.0)
    df["rsi_14"] = rsi.where(~((loss == 0) & (gain == 0)), 50.0)

    bb_mid = close.rolling(window=20).mean()
    bb_std = close.rolling(window=20).std()
    df["BB_Upper"] = bb_mid + 2 * bb_std
    df["BB_Lower"] = bb_mid - 2 * bb_std

    if "Volume" in df.columns:
        df["Volume_SMA"] = df["Volume"].rolling(10).mean()
        # Rupiah actually traded per session -- the number that decides whether a
        # position can be exited, unlike share count which ignores price.
        df["daily_value_rp"] = close * df["Volume"]
        # `min_periods`, because one missing volume must not blank the whole metric.
        # A session rebuilt from intraday bars carries no trustworthy volume (the
        # auction and off-book prints are absent), so its Volume is NaN -- and with
        # the default `min_periods=liquidity_window` a single NaN makes the rolling
        # median NaN for the newest row. `extract_latest_indicators` reads that row,
        # `liquidity.assess` reads None as "cannot trade", and the ticket would empty
        # itself against the entire universe. A median over 19 of 20 sessions is
        # sound; one over nothing is not.
        df["median_daily_value_rp"] = df["daily_value_rp"].rolling(
            liquidity_window, min_periods=max(2, liquidity_window // 2)).median()

    df["price_change_pct"] = close.pct_change() * 100
    df["return_20d"] = close.pct_change(20) * 100
    df["ma5_slope"] = df["MA_5"].pct_change() * 100
    df["ma20_slope"] = df["MA_20"].pct_change() * 100

    df["mom_1m"] = (close / close.shift(_SESSIONS_1M) - 1) * 100
    df["mom_6m"] = (close.shift(_SESSIONS_1M) / close.shift(_SESSIONS_6M) - 1) * 100
    df["mom_12m"] = (close.shift(_SESSIONS_1M) / close.shift(_SESSIONS_12M) - 1) * 100

    # Annualised realised volatility. Replaces yfinance `beta`, which reports 0.016
    # for Bank Mandiri and negative values for several IDX large caps.
    df["realized_vol"] = close.pct_change().rolling(vol_window).std() * np.sqrt(252) * 100

    return df


def extract_latest_indicators(df: pd.DataFrame) -> Dict[str, float]:
    """Last-row snapshot. Returns an all-NaN dict rather than raising on short series."""
    if df is None or df.empty or len(df) < 2:
        return {k: np.nan for k in _LATEST_KEYS}

    last, prev = df.iloc[-1], df.iloc[-2]

    def val(row, key):
        if key not in df.columns:
            return np.nan
        v = row[key]
        return float(v) if pd.notna(v) else np.nan

    out = {k: val(last, k) for k in _LATEST_KEYS if k not in ("last_close", "volume_ratio")}
    out["last_close"] = val(last, "Close")

    vol_sma = val(prev, "Volume_SMA")
    last_vol = val(last, "Volume")
    out["volume_ratio"] = (
        last_vol / vol_sma if np.isfinite(vol_sma) and np.isfinite(last_vol) and vol_sma != 0 else np.nan
    )
    return out
