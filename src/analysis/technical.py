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
    "last_close", "volume_ratio", "atr_14", "atr_pct",
)

# Each price-derived factor, and the window it is actually measured over. A name
# can have a perfectly good 12-month momentum and an unmeasurable 60-day
# volatility, so the check is made per factor rather than once for the ticker.
#
# `atr_14` is here despite carrying no weight in `factor_weights`: it is blocked
# for exactly the same reason the scored ones are, and the consequence is worse.
# A scored factor that cannot be measured contributes a neutral zero; an ATR that
# cannot be measured produces a stop AT the entry price, which sells on the first
# tick. WIKA's ATR is 0.00 and GOTO's is 0.01.
_FACTOR_WINDOWS = {
    "realized_vol": 60,
    "mom_1m": _SESSIONS_1M,
    "mom_6m": _SESSIONS_6M,
    "mom_12m": _SESSIONS_12M,
    "atr_14": 42,   # ~3x the ATR window: Wilder's ewm keeps 95% of its weight there
}

DEFAULT_MARKET = {"min_price_rp": 50.0, "max_flat_pct": 0.60, "max_floor_pct": 0.20}


def unmeasurable_factors(close: pd.Series, market: Dict = None,
                         vol_window: int = 60,
                         atr_window: int = 14) -> Dict[str, str]:
    """
    Which price factors this series cannot support, and why.

    Two ways a price stops carrying information, and both measure as *low*
    volatility -- which `realized_vol` scores at -0.5, "prefer calmer names":

      * **Pinned at the floor.** GOTO sat at Rp50, the minimum tradable price, for
        85 of its last 250 sessions. It cannot fall. That is a market rule, not a
        calm business.
      * **Not moving at all.** WIKA printed no change on 249 of 250 sessions with
        zero traded value -- suspended, not calm. It scored `realized_vol = 0.000`,
        the most favourable volatility z-score in the universe, worth +0.96 on its
        composite.

    Both then distort everyone else: `_global_z` standardises on mean and standard
    deviation, so two impossible zeros in 49 names shift the mean and inflate the
    spread for every name being compared against them.

    Returning a reason per factor rather than dropping the ticker: the scorer
    already turns a missing factor into a neutral 0 and lists it in
    `imputed_factors`, so this says "we cannot measure this" instead of either
    "this is calm" or silently removing a name from the universe.
    """
    market = {**DEFAULT_MARKET, **(market or {})}
    floor = float(market.get("min_price_rp") or 0.0)
    max_flat = float(market.get("max_flat_pct", 0.60))
    max_floor = float(market.get("max_floor_pct", 0.20))

    out: Dict[str, str] = {}
    if close is None or len(close) < 5:
        return out

    clean = close.dropna()
    windows = {**_FACTOR_WINDOWS,
               "realized_vol": int(vol_window),
               "atr_14": max(30, 3 * int(atr_window))}

    for factor, n in windows.items():
        window = clean.tail(int(n))
        if len(window) < 5:
            continue

        flat = float((window.pct_change() == 0).mean())
        at_floor = float((window <= floor).mean()) if floor > 0 else 0.0

        if at_floor > max_floor:
            out[factor] = (f"at the Rp{floor:,.0f} floor on {at_floor:.0%} of the "
                           f"last {n} sessions")
        elif flat > max_flat:
            out[factor] = (f"no price change on {flat:.0%} of the last {n} "
                           f"sessions")
    return out


def true_range(df: pd.DataFrame) -> pd.Series:
    """
    The greater of today's range and either gap against yesterday's close.

    Falls back to |close - prev close| when the frame carries no High/Low, which
    is what a session rebuilt from intraday bars can look like. That understates
    the range rather than returning nothing, and a stop derived from it is
    tighter than the truth -- the safe direction to be wrong in.
    """
    close = df["Close"]
    prev = close.shift()
    if "High" in df.columns and "Low" in df.columns:
        high, low = df["High"], df["Low"]
    else:
        high = low = close
    return pd.concat(
        [high - low, (high - prev).abs(), (low - prev).abs()], axis=1
    ).max(axis=1)


def average_true_range(df: pd.DataFrame, window: int = 14) -> pd.Series:
    """
    Wilder's ATR: an exponential mean of the true range with alpha = 1/window.

    Wilder's smoothing rather than a simple rolling mean, because that is what ATR
    is and every chart the reader might check it against uses it. The difference
    is not cosmetic -- a simple mean drops a spike abruptly 14 sessions later,
    which would step a stop level sideways for no reason in the price.
    """
    n = max(1, int(window))
    return true_range(df).ewm(alpha=1 / n, adjust=False).mean()


def compute_indicators(
    df: pd.DataFrame,
    vol_window: int = 60,
    liquidity_window: int = 20,
    atr_window: int = 14,
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

    # How far this name moves in a normal day, in rupiah. `realized_vol` answers
    # the same question as a percentage per year and is the right thing to RANK on;
    # a stop has to be a price, and a price needs a rupiah distance. Measured on
    # the real universe the two disagree enough to matter: a 2.5x ATR stop runs
    # from 3.0% on BBSI to 16.8% on INET, so one percentage cannot serve both.
    df["atr_14"] = average_true_range(df, atr_window)
    with np.errstate(divide="ignore", invalid="ignore"):
        df["atr_pct"] = df["atr_14"] / close.replace(0, np.nan) * 100

    return df


def extract_latest_indicators(df: pd.DataFrame, market: Dict = None,
                              vol_window: int = 60,
                              atr_window: int = 14) -> Dict[str, float]:
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

    # A price that cannot move does not get to score as calm. Nulled here rather
    # than at scoring time, so the ONE place that decides "this is unmeasurable"
    # is the one place that owns the price series.
    blocked = unmeasurable_factors(df["Close"] if "Close" in df else None,
                                   market, vol_window, atr_window)
    for factor, reason in blocked.items():
        out[factor] = np.nan
    # A blocked ATR takes its percentage with it. Leaving atr_pct populated from a
    # nulled atr_14 would hand the exit engine a stop width with no price behind it.
    if "atr_14" in blocked:
        out["atr_pct"] = np.nan
    out["price_note"] = "; ".join(
        f"{f}: {r}" for f, r in sorted(blocked.items())) if blocked else ""
    return out
