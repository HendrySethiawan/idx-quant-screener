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
