import numpy as np
import pandas as pd
import pytest

from analysis.technical import compute_indicators, extract_latest_indicators


def test_indicator_columns_present(price_frame):
    df = compute_indicators(price_frame)
    for col in ("rsi_14", "MA_5", "MA_20", "mom_1m", "mom_6m", "mom_12m",
                "realized_vol", "median_daily_value_rp", "return_20d"):
        assert col in df.columns


def test_rsi_is_bounded(price_frame):
    rsi = compute_indicators(price_frame)["rsi_14"].dropna()
    assert not rsi.empty
    assert rsi.between(0, 100).all()


def test_rising_series_has_positive_momentum(price_frame):
    latest = extract_latest_indicators(compute_indicators(price_frame))
    assert latest["mom_6m"] > 0
    assert latest["mom_12m"] > 0


def test_momentum_skips_the_most_recent_month():
    """6-1 momentum must ignore the last ~21 sessions, so a late spike cannot leak in."""
    idx = pd.date_range("2023-01-02", periods=300, freq="B")
    close = pd.Series(np.full(300, 1000.0), index=idx)
    close.iloc[-10:] = 5000.0  # violent spike inside the skip window
    frame = pd.DataFrame({"Close": close, "Volume": pd.Series(np.full(300, 1e6), index=idx)})

    latest = extract_latest_indicators(compute_indicators(frame))
    assert latest["mom_6m"] == pytest.approx(0.0, abs=1e-9)


def test_realized_vol_is_higher_for_a_noisier_series():
    idx = pd.date_range("2023-01-02", periods=300, freq="B")
    rng = np.random.default_rng(0)
    calm = pd.Series(1000 * np.cumprod(1 + rng.normal(0, 0.002, 300)), index=idx)
    wild = pd.Series(1000 * np.cumprod(1 + rng.normal(0, 0.020, 300)), index=idx)
    vol = pd.Series(np.full(300, 1e6), index=idx)

    calm_v = extract_latest_indicators(compute_indicators(pd.DataFrame({"Close": calm, "Volume": vol})))["realized_vol"]
    wild_v = extract_latest_indicators(compute_indicators(pd.DataFrame({"Close": wild, "Volume": vol})))["realized_vol"]
    assert wild_v > calm_v


def test_median_daily_value_is_rupiah_not_shares(price_frame):
    latest = extract_latest_indicators(compute_indicators(price_frame))
    # ~2000 IDR * 1,000,000 shares
    assert latest["median_daily_value_rp"] > 1e8


def test_short_series_returns_nan_dict():
    tiny = pd.DataFrame({"Close": [100.0], "Volume": [1000.0]})
    latest = extract_latest_indicators(compute_indicators(tiny))
    assert np.isnan(latest["rsi_14"])
    assert np.isnan(latest["mom_6m"])


def test_nan_close_rows_are_dropped():
    """Today's partial bar arrives as NaN Close and would poison price_change_pct."""
    idx = pd.date_range("2023-01-02", periods=60, freq="B")
    close = pd.Series(np.linspace(100, 200, 60), index=idx)
    close.iloc[-1] = np.nan
    frame = pd.DataFrame({"Close": close, "Volume": pd.Series(np.full(60, 1e6), index=idx)})

    out = compute_indicators(frame)
    assert out["Close"].notna().all()
    assert len(out) == 59


# ------------------------------------------ a missing volume must not empty the ticket
# A session rebuilt from intraday bars carries no trustworthy volume, so its Volume
# is NaN. With the default `min_periods` on the rolling median, ONE NaN made
# `median_daily_value_rp` NaN for the newest row -- which `extract_latest_indicators`
# reads, `assess` reads as "cannot trade", and the ticket would then refuse
# the entire universe. This is the guard against that.
def _series(n=40, close=1000.0, volume=1_000_000.0):
    idx = pd.date_range("2026-06-01", periods=n, freq="B")
    return pd.DataFrame(
        {"Open": [close] * n, "High": [close] * n, "Low": [close] * n,
         "Close": [close] * n, "Volume": [volume] * n},
        index=idx)


def test_the_liquidity_median_survives_a_session_with_no_volume():
    df = _series()
    df.loc[df.index[-1], "Volume"] = np.nan          # a rebuilt bar

    out = compute_indicators(df, vol_window=20, liquidity_window=20)
    latest = extract_latest_indicators(out)

    assert latest["median_daily_value_rp"] is not None
    assert latest["median_daily_value_rp"] > 0


def test_a_name_with_a_rebuilt_last_bar_is_still_tradeable():
    """The end-to-end version: it must survive the liquidity gate, not just be a number."""
    from market.liquidity import LiquidityConfig, assess

    df = _series(volume=2_000_000.0)
    df.loc[df.index[-1], "Volume"] = np.nan

    latest = extract_latest_indicators(compute_indicators(df, liquidity_window=20))
    verdict = assess("BBRI.JK", latest["median_daily_value_rp"],
                     position_rp=2_000_000, cfg=LiquidityConfig())
    assert verdict.ok, verdict.reason


def test_a_genuinely_thin_name_is_still_rejected():
    """The floor must keep working; this loosens a window, not the rule."""
    from market.liquidity import LiquidityConfig, assess

    latest = extract_latest_indicators(
        compute_indicators(_series(close=50.0, volume=100.0), liquidity_window=20))
    verdict = assess("TINY.JK", latest["median_daily_value_rp"],
                     position_rp=2_000_000, cfg=LiquidityConfig())
    assert not verdict.ok


def test_too_few_sessions_still_gives_no_liquidity_figure():
    """Half a window is a median; three days is not."""
    out = compute_indicators(_series(n=3), liquidity_window=20)
    assert pd.isna(out["median_daily_value_rp"].iloc[-1])
