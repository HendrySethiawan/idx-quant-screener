import numpy as np
import pandas as pd
import pytest

from analysis.technical import (DEFAULT_MARKET, average_true_range,
                                compute_indicators, extract_latest_indicators,
                                trailing_dividend_yield, true_range,
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

    assert set(blocked) == {"realized_vol", "mom_1m", "mom_6m", "mom_12m", "atr_14"}
    assert "no price change" in blocked["realized_vol"]


def test_a_suspended_price_also_blocks_the_stop_distance():
    """
    `atr_14` carries no scoring weight, but a blocked one matters more than a
    blocked factor does: a factor that cannot be measured scores a neutral zero,
    while an ATR that cannot be measured puts the stop ON the entry price and
    sells the position on its first tick. WIKA's real ATR is 0.00.
    """
    vals = np.full(300, 204.0)
    vals[0] = 210.0
    frame = compute_indicators(_frame(_prices(vals)))
    latest = extract_latest_indicators(frame, MARKET, vol_window=60)

    assert pd.isna(latest["atr_14"])
    assert pd.isna(latest["atr_pct"])
    assert "atr_14" in latest["price_note"]


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


# ------------------------------------------------------------- ATR, for the stop
# `realized_vol` answers "how volatile" as a percentage per year and is the right
# thing to RANK on. A stop has to be a PRICE, and a price needs a rupiah distance,
# which is what ATR supplies. Across the real universe the two disagree enough to
# matter: 2.5 x ATR is 3.0% on BBSI and 16.8% on INET.

def _ohlc(close, spread=0.0):
    """A frame with a High/Low band `spread` wide around each close."""
    return pd.DataFrame({
        "Close": close,
        "High": close * (1 + spread),
        "Low": close * (1 - spread),
        "Volume": pd.Series(np.full(len(close), 1e6), index=close.index),
    })


def test_atr_matches_wilders_smoothing_by_hand():
    """
    Wilder's, not a rolling mean. A constant true range smooths to itself, so the
    recursion is pinned without needing a table of expected values -- and after
    300 bars the warm-up from a first TR of zero has decayed to nothing.
    """
    close = _prices(np.arange(100, 400, dtype=float))     # +1 every session
    out = compute_indicators(_ohlc(close), atr_window=14)
    assert out["atr_14"].iloc[-1] == pytest.approx(1.0, abs=1e-6)


def test_atr_uses_the_high_low_range_not_just_closes():
    """
    The reason the full bar matters. Two names whose closes crawl identically, one
    of which swings 2% intraday, are not equally risky to hold -- and a
    close-to-close measure would call them the same.
    """
    close = _prices(np.arange(1000, 1300, dtype=float))
    calm = compute_indicators(_ohlc(close), atr_window=14)["atr_14"].iloc[-1]
    wide = compute_indicators(_ohlc(close, spread=0.01),
                              atr_window=14)["atr_14"].iloc[-1]

    assert calm == pytest.approx(1.0, abs=1e-6)
    assert wide > 20.0


def test_atr_falls_back_to_close_to_close_without_high_and_low():
    """
    A session rebuilt from intraday bars can arrive without High/Low. The fallback
    understates the range, so a stop built on it is TIGHTER than the truth -- the
    safe direction to be wrong in.
    """
    close = _prices(np.arange(100, 400, dtype=float))
    bare = pd.DataFrame({"Close": close})
    assert average_true_range(bare, 14).iloc[-1] == pytest.approx(1.0, abs=1e-6)
    assert true_range(bare).iloc[-1] == pytest.approx(1.0)


def test_atr_pct_is_the_distance_as_a_share_of_the_price():
    close = _prices(np.linspace(2000.0, 2200.0, 200))
    out = compute_indicators(_ohlc(close, spread=0.005), atr_window=14)
    latest = extract_latest_indicators(out)

    assert latest["atr_pct"] == pytest.approx(
        latest["atr_14"] / latest["last_close"] * 100, abs=1e-6)
    assert 0 < latest["atr_pct"] < 5


def test_a_flat_price_has_no_atr_to_speak_of():
    """WIKA: 0.00. The exit rules read this as "no stop can be set here"."""
    close = _prices(np.full(120, 204.0))
    out = compute_indicators(_ohlc(close), atr_window=14)
    assert out["atr_14"].iloc[-1] == pytest.approx(0.0, abs=1e-9)


def test_a_frozen_close_blocks_the_atr_even_with_a_wide_intraday_band():
    """
    Deliberate, and worth stating: a name whose close never moves is blocked
    whatever its High and Low claim. On IDX an unchanged close for months is a
    suspension, and the printed range around it is not something you can sell at.
    """
    close = _prices(np.full(200, 2000.0))
    latest = extract_latest_indicators(
        compute_indicators(_ohlc(close, spread=0.01)), MARKET, vol_window=60)
    assert pd.isna(latest["atr_14"])


def test_atr_reaches_the_indicator_snapshot():
    close = _prices(np.arange(500, 700, dtype=float))
    latest = extract_latest_indicators(compute_indicators(_ohlc(close, 0.005)))
    assert latest["atr_14"] > 0
    assert latest["atr_pct"] > 0


# ------------------------------------------ the dividend yield, from the payments
# Both of yfinance's summary fields are wrong in ways that reached the ranking:
#
#   `dividendYield`                 a FORWARD estimate on a percent scale. Read as
#                                   a fraction it turned BREN's real 0.12% into
#                                   12%, and BREN pays nothing at all. It scored
#                                   third-best in the universe on a weight-1.0
#                                   factor.
#   `trailingAnnualDividendYield`   sounds right, is not: it is the LAST payment
#                                   over the price. 6.13% for BBRI, which paid
#                                   Rp137 and Rp209 on a Rp3,390 share -- 10.2%.
#                                   And 0.0 for PGAS three months after paying
#                                   Rp125.6.
#
# A list of payment dates and rupiah amounts cannot be misread, and it is the same
# basis portfolio/dividends.py records what actually arrived on.

def _with_dividends(payments, price=1000.0, n=400, end="2026-09-04"):
    """A price frame carrying a Dividends column, as `actions=True` returns."""
    idx = pd.bdate_range(end=end, periods=n)
    df = pd.DataFrame({"Close": float(price), "High": float(price),
                       "Low": float(price), "Volume": 1e6,
                       "Dividends": 0.0}, index=idx)
    for when, amount in payments:
        df.loc[pd.Timestamp(when), "Dividends"] = float(amount)
    return df


def test_the_yield_is_the_year_of_payments_over_the_price():
    """BBRI: Rp137 in December and Rp209 in April, on a Rp3,390 share."""
    df = _with_dividends([("2025-12-30", 137.0), ("2026-04-21", 209.0)], price=3390.0)
    y = trailing_dividend_yield(df, today="2026-09-04")
    assert y == pytest.approx(346.0 / 3390.0, abs=1e-6)
    assert y == pytest.approx(0.102, abs=0.001)


def test_a_payment_older_than_a_year_is_outside_the_window():
    """IMPC last paid in May 2024, so its trailing yield really is zero."""
    df = _with_dividends([("2024-05-31", 4.5)], price=1405.0)
    assert trailing_dividend_yield(df, today="2026-09-04") == pytest.approx(0.0)


def test_a_company_that_pays_nothing_yields_nothing():
    """
    BREN's case, and the whole point. Not missing, not neutral -- zero, so it
    scores at the bottom of a factor it used to score near the top of.
    """
    df = _with_dividends([], price=3360.0)
    assert trailing_dividend_yield(df, today="2026-09-04") == pytest.approx(0.0)


def test_a_tiny_real_yield_stays_tiny():
    """BREN pays about Rp4.1 on a Rp3,360 share: 0.12%, not 12%."""
    df = _with_dividends([("2026-06-15", 4.1)], price=3360.0)
    y = trailing_dividend_yield(df, today="2026-09-04")
    assert y == pytest.approx(0.00122, abs=1e-5)
    assert y < 0.01


def test_no_dividend_column_means_no_answer():
    """A frame fetched before `actions=True` must read as missing, never as zero."""
    idx = pd.bdate_range(end="2026-09-04", periods=50)
    bare = pd.DataFrame({"Close": 1000.0}, index=idx)
    assert pd.isna(trailing_dividend_yield(bare, today="2026-09-04"))


def test_an_impossible_yield_is_refused():
    """A payment larger than the share price is a data error, not a payout."""
    df = _with_dividends([("2026-06-15", 900.0)], price=1000.0)
    assert pd.isna(trailing_dividend_yield(df, today="2026-09-04"))


def test_the_yield_reaches_the_indicator_snapshot():
    df = _with_dividends([("2026-06-15", 100.0)], price=2000.0)
    latest = extract_latest_indicators(compute_indicators(df))
    assert latest["dividend_yield"] == pytest.approx(0.05, abs=1e-6)
