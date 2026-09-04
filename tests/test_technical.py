import numpy as np
import pandas as pd
import pytest

from analysis.technical import (DEFAULT_MARKET, compute_indicators,
                                extract_latest_indicators,
                                unmeasurable_factors)


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
    # A repeating 15-day pattern, not a flat line. 6-1 momentum compares t-21 with
    # t-126, and 126-21 = 105 is a whole number of periods, so those two points are
    # bit-identical and the expected answer is still exactly 0. A constant series
    # would be simpler and would also be a suspended stock -- which the price
    # checks now (correctly) refuse to measure.
    pattern = np.array([1000.0, 1010.0, 1005.0, 995.0, 1020.0,
                        1015.0, 990.0, 1000.0, 1030.0, 1008.0,
                        998.0, 1012.0, 1002.0, 1018.0, 992.0])
    close = pd.Series(np.tile(pattern, 20)[:300], index=idx)
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


# ------------------------------- a price that cannot move is not a calm price
# `realized_vol` is scored at -0.5, "prefer calmer names". Two things measure as
# zero volatility without being calm: a price pinned at the IDX minimum (GOTO sat
# at Rp50 for 85 of 250 sessions and cannot fall) and a suspended one (WIKA printed
# no change on 249 of 250 with zero traded value). Both scored realized_vol =
# 0.000000, the most favourable volatility z-score in the universe.
MARKET = {"min_price_rp": 50.0, "max_flat_pct": 0.60, "max_floor_pct": 0.20}


def _prices(values):
    idx = pd.date_range("2024-01-01", periods=len(values), freq="B")
    return pd.Series(np.asarray(values, dtype=float), index=idx)


def _frame(close):
    return pd.DataFrame({"Close": close,
                         "Volume": pd.Series(np.full(len(close), 1e6), index=close.index)})


def test_a_suspended_price_cannot_support_volatility_or_momentum():
    """WIKA's shape: 300 sessions, one move. Not calm -- not trading."""
    vals = np.full(300, 204.0)
    vals[0] = 210.0
    blocked = unmeasurable_factors(_prices(vals), MARKET, vol_window=60)

    assert set(blocked) == {"realized_vol", "mom_1m", "mom_6m", "mom_12m"}
    assert "no price change" in blocked["realized_vol"]


def test_a_price_pinned_at_the_floor_cannot_support_them_either():
    """GOTO's shape: sitting on the Rp50 minimum, unable to fall."""
    vals = np.concatenate([np.linspace(120.0, 50.0, 100), np.full(200, 50.0)])
    blocked = unmeasurable_factors(_prices(vals), MARKET, vol_window=60)

    assert "realized_vol" in blocked
    assert "floor" in blocked["realized_vol"]
    assert "Rp50" in blocked["realized_vol"]


def test_the_floor_is_configuration_not_a_constant():
    """
    BEI is moving the minimum price Rp50 -> Rp1 (targeted 7 Sept 2026). When it
    lands, one config value changes and a name at Rp50 stops being pinned.
    """
    vals = np.full(300, 50.0)
    vals[::2] = 51.0            # changes every session, so ONLY the floor rule can fire
    assert "realized_vol" in unmeasurable_factors(_prices(vals), MARKET, 60)

    after = {**MARKET, "min_price_rp": 1.0}
    assert "realized_vol" not in unmeasurable_factors(_prices(vals), after, 60)


def test_an_ordinary_name_is_left_alone():
    rng = np.random.default_rng(7)
    vals = 3000.0 * np.cumprod(1 + rng.normal(0, 0.015, 300))
    assert unmeasurable_factors(_prices(vals), MARKET, vol_window=60) == {}


def test_a_thin_but_real_name_is_left_alone():
    """
    BBKP runs 27-48% flat sessions and touches the floor at most 5% of the time.
    Thin is not unmeasurable, and the liquidity gate already judges thin.
    """
    vals = np.full(300, 54.0)
    vals[::2] = 55.0                       # ~50% flat, never at the floor
    assert unmeasurable_factors(_prices(vals), MARKET, vol_window=60) == {}


def test_each_factor_is_judged_over_its_own_window():
    """
    A name can have unmeasurable 60-day volatility and perfectly good 12-month
    momentum. Judging the ticker once would throw away the half that still works.
    """
    vals = np.concatenate([3000.0 + np.arange(200) * 2.0, np.full(100, 3400.0)])
    blocked = unmeasurable_factors(_prices(vals), MARKET, vol_window=60)

    assert "realized_vol" in blocked          # last 60 are flat
    assert "mom_12m" not in blocked           # the 252-session window still moves


def test_the_blocked_factors_come_back_as_missing_not_as_zero():
    """
    Nulled, so the scorer treats them as neutral and lists them in
    imputed_factors. Zero would read as "no volatility", which is the bug.
    """
    vals = np.full(300, 204.0)
    vals[0] = 210.0
    latest = extract_latest_indicators(compute_indicators(_frame(_prices(vals))),
                                       market=MARKET, vol_window=60)

    assert np.isnan(latest["realized_vol"])
    assert np.isnan(latest["mom_12m"])
    assert "no price change" in latest["price_note"]


def test_the_reason_is_carried_for_the_page_to_show():
    vals = np.concatenate([np.linspace(120.0, 50.0, 100), np.full(200, 50.0)])
    latest = extract_latest_indicators(compute_indicators(_frame(_prices(vals))),
                                       market=MARKET, vol_window=60)
    assert "Rp50 floor" in latest["price_note"]


def test_no_market_config_falls_back_to_the_shipped_defaults():
    vals = np.full(300, 204.0)
    vals[0] = 210.0
    assert unmeasurable_factors(_prices(vals), None, 60)
    assert DEFAULT_MARKET["min_price_rp"] == 50.0


def test_a_short_series_is_not_judged():
    assert unmeasurable_factors(_prices([100.0, 100.0, 100.0]), MARKET, 60) == {}
    assert unmeasurable_factors(None, MARKET, 60) == {}
