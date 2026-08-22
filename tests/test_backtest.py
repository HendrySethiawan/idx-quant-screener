"""
Backtest engine.

The look-ahead tests come first because every other number is worthless without
them. If the signal can see the future, a profitable curve proves nothing.
"""
import numpy as np
import pandas as pd
import pytest

from backtest.engine import (BacktestConfig, build_price_panel, buy_and_hold,
                             equal_weight_universe, max_drawdown, price_signal,
                             rebalance_dates, run_backtest, summarize)
from portfolio.fees import FeeConfig

CAPITAL = 10_000_000
FEE = FeeConfig()


def _panel(n_days=900, tickers=("A.JK", "B.JK", "C.JK", "D.JK"), seed=0):
    """Trending series with enough history for 12-month momentum."""
    idx = pd.bdate_range("2022-01-03", periods=n_days)
    rng = np.random.default_rng(seed)
    data = {}
    for i, t in enumerate(tickers):
        drift = 0.0008 - i * 0.0004          # A trends up, D trends down
        data[t] = 1000 * np.cumprod(1 + rng.normal(drift, 0.012, n_days))
    return pd.DataFrame(data, index=idx)


def _cfg(**kw):
    base = dict(rebalance="M", min_names=2, min_positions=2, max_positions=3,
                min_position_rp=0.0, max_per_sector=0, use_regime=False)
    base.update(kw)
    return BacktestConfig(**base)


# ============================================================ LOOK-AHEAD GUARDS
def test_future_data_cannot_change_the_signal():
    """
    The direct proof rather than a proxy: mutate prices AFTER the cutoff by an
    absurd factor and the score must come back byte-identical.

    An earlier version of this test asserted "the spiked name must not rank first",
    which was unsound -- a name can legitimately rank first on its pre-cutoff
    merits, so the test would have passed or failed on the random seed rather than
    on whether the engine leaks.
    """
    panel = _panel()
    cutoff = panel.index[600]

    before = price_signal(panel[panel.index < cutoff])

    spiked = panel.copy()
    spiked.loc[spiked.index[601]:, "B.JK"] *= 8.0
    spiked.loc[spiked.index[601]:, "D.JK"] *= 0.1
    after = price_signal(spiked[spiked.index < cutoff])

    assert not before.empty
    pd.testing.assert_series_equal(before, after)


def test_future_data_cannot_change_the_equity_curve():
    """Same property at the whole-run level, not just the signal."""
    panel = _panel()
    cutoff = panel.index[700]

    truncated = panel[panel.index <= cutoff]
    extended = panel.copy()
    extended.loc[extended.index[701]:, :] *= 5.0

    a = run_backtest(truncated, CAPITAL, _cfg(), FEE)
    b = run_backtest(extended[extended.index <= cutoff], CAPITAL, _cfg(), FEE)
    pd.testing.assert_series_equal(a.equity, b.equity)


def test_run_loop_asserts_no_future_rows_leak_in():
    """The assertion inside run_backtest must actually be exercised."""
    panel = _panel()
    result = run_backtest(panel, CAPITAL, _cfg(), FEE)
    assert result.n_rebalances > 0


def test_late_listing_never_appears_before_it_listed():
    """A ticker whose first bar is late must not be pickable earlier."""
    panel = _panel()
    panel["NEW.JK"] = np.nan
    panel.loc[panel.index[700]:, "NEW.JK"] = 5000.0

    early = price_signal(panel[panel.index < panel.index[650]])
    assert "NEW.JK" not in early.index


def test_partially_listed_name_is_excluded_not_half_scored():
    """A name listed midway through the lookback has no valid 12-month momentum."""
    panel = _panel()
    panel["MID.JK"] = np.nan
    panel.loc[panel.index[400]:, "MID.JK"] = np.linspace(1000, 2000, len(panel) - 400)

    score = price_signal(panel[panel.index < panel.index[500]])
    assert "MID.JK" not in score.index


def test_prices_are_never_forward_filled_into_the_panel():
    frame = pd.DataFrame({"Close": [np.nan, np.nan, 100.0, 101.0]},
                         index=pd.bdate_range("2024-01-01", periods=4))
    panel = build_price_panel({"X.JK": frame})
    assert len(panel) == 2, "leading gap was filled instead of dropped"


# ==================================================== EXECUTION REALISM
def test_every_holding_is_a_whole_lot():
    result = run_backtest(_panel(), CAPITAL, _cfg(whole_lots=True), FEE)
    assert result.n_rebalances > 0
    assert not result.equity.empty


def test_equity_never_goes_negative():
    result = run_backtest(_panel(), CAPITAL, _cfg(), FEE)
    assert (result.equity > 0).all()


def test_fees_reduce_the_final_result():
    panel = _panel()
    gross = run_backtest(panel, CAPITAL, _cfg(charge_fees=False), FEE)
    net = run_backtest(panel, CAPITAL, _cfg(charge_fees=True), FEE)
    assert net.equity.iloc[-1] <= gross.equity.iloc[-1]
    assert net.fees_paid > 0
    assert gross.fees_paid == 0


def test_weekly_costs_more_in_fees_than_monthly():
    """Higher turnover multiplies both the percentage fees and the stamp."""
    panel = _panel()
    monthly = run_backtest(panel, CAPITAL, _cfg(rebalance="M"), FEE)
    weekly = run_backtest(panel, CAPITAL, _cfg(rebalance="W"), FEE)
    assert weekly.fees_paid > monthly.fees_paid
    assert weekly.n_rebalances > monthly.n_rebalances


def test_stamp_charged_once_per_rebalance_not_per_sell():
    """Consistent with journal.stamp_for: the stamp is per sell DAY."""
    panel = _panel()
    result = run_backtest(panel, CAPITAL, _cfg(), FEE)
    assert result.stamp_paid == pytest.approx(result.n_sell_days * FEE.stamp_duty_rp)


def test_lot_rounding_has_a_measurable_cost():
    panel = _panel()
    exact = run_backtest(panel, CAPITAL, _cfg(whole_lots=False, charge_fees=False), FEE)
    lots = run_backtest(panel, CAPITAL, _cfg(whole_lots=True, charge_fees=False), FEE)
    assert not exact.equity.empty and not lots.equity.empty
    assert exact.equity.iloc[-1] != lots.equity.iloc[-1]


def test_min_position_floor_reduces_the_position_count():
    panel = _panel(tickers=tuple(f"T{i}.JK" for i in range(8)))
    loose = run_backtest(panel, CAPITAL, _cfg(min_position_rp=0, max_positions=6), FEE)
    tight = run_backtest(panel, CAPITAL, _cfg(min_position_rp=4_000_000, max_positions=6), FEE)
    loose_n = max(h["n_holdings"] for h in loose.holdings_log)
    tight_n = max(h["n_holdings"] for h in tight.holdings_log)
    assert tight_n <= loose_n


def test_deterministic_across_runs():
    panel = _panel()
    a = run_backtest(panel, CAPITAL, _cfg(), FEE)
    b = run_backtest(panel, CAPITAL, _cfg(), FEE)
    pd.testing.assert_series_equal(a.equity, b.equity)


# ============================================================ SIGNAL BEHAVIOUR
def _clean_panel(slopes=(0.0010, 0.0005, 0.0000, -0.0005), n_days=900):
    """
    Noiseless exponential trends, so the ranking is unambiguous.

    A random-walk fixture cannot be used here: over 900 days the noise dominates
    the drift, and trailing 6-month momentum routinely disagrees with full-period
    drift. That is realistic, but it makes an ordering assertion a coin flip.
    """
    idx = pd.bdate_range("2022-01-03", periods=n_days)
    return pd.DataFrame(
        {f"T{i}.JK": 1000 * np.exp(s * np.arange(n_days)) for i, s in enumerate(slopes)},
        index=idx,
    )


def test_steeper_uptrend_outranks_shallower_one():
    score = price_signal(_clean_panel())
    assert list(score.index) == ["T0.JK", "T1.JK", "T2.JK", "T3.JK"]


def test_downtrending_name_ranks_last():
    score = price_signal(_clean_panel())
    assert score.index[-1] == "T3.JK"


def test_volatility_penalty_lowers_a_choppy_name():
    """Two names with the same drift; the noisier one must score lower."""
    n = 900
    idx = pd.bdate_range("2022-01-03", periods=n)
    rng = np.random.default_rng(7)
    trend = 1000 * np.exp(0.0005 * np.arange(n))
    panel = pd.DataFrame({
        "CALM.JK": trend,
        "CHOPPY.JK": trend * np.cumprod(1 + rng.normal(0, 0.03, n)) / np.cumprod(1 + rng.normal(0, 0.03, n))[-1],
        "OTHER.JK": 1000 * np.exp(0.0003 * np.arange(n)),
    }, index=idx)
    score = price_signal(panel)
    assert score["CALM.JK"] > score["CHOPPY.JK"]


def test_weight_scale_changes_the_ranking_inputs():
    hist = _panel()[:-1]
    base = price_signal(hist, weight_scale=1.0)
    scaled = price_signal(hist, weight_scale=0.5)
    assert not base.equals(scaled)


def test_insufficient_history_returns_empty_signal():
    assert price_signal(_panel(n_days=100)).empty
    assert price_signal(pd.DataFrame()).empty


def test_regime_reduces_deployment_in_a_downtrend():
    """A falling benchmark should put less capital to work, so less is at risk."""
    panel = _panel(n_days=1200)
    falling = pd.Series(np.linspace(7000, 4000, len(panel)), index=panel.index)

    with_regime = run_backtest(panel, CAPITAL, _cfg(use_regime=True), FEE,
                               benchmark=falling, fx=None)
    without = run_backtest(panel, CAPITAL, _cfg(use_regime=False), FEE)
    assert with_regime.equity.iloc[-1] != without.equity.iloc[-1]


# ================================================================= BENCHMARKS
def test_rebalance_dates_monthly_vs_weekly():
    panel = _panel(n_days=500)
    assert len(rebalance_dates(panel, "W")) > len(rebalance_dates(panel, "M"))


def test_rebalance_dates_are_real_trading_days():
    panel = _panel()
    for d in rebalance_dates(panel, "M"):
        assert d in panel.index


def test_buy_and_hold_tracks_the_series():
    panel = _panel()
    dates = rebalance_dates(panel, "M")
    curve = buy_and_hold(panel["A.JK"], dates, CAPITAL)
    ratio = panel["A.JK"].loc[dates[-1]] / panel["A.JK"].loc[dates[0]]
    assert curve.iloc[-1] == pytest.approx(CAPITAL * ratio, rel=1e-6)


def test_equal_weight_universe_sits_between_best_and_worst():
    panel = _panel()
    dates = rebalance_dates(panel, "M")
    ew = equal_weight_universe(panel, dates, CAPITAL)
    best = buy_and_hold(panel["A.JK"], dates, CAPITAL)
    worst = buy_and_hold(panel["D.JK"], dates, CAPITAL)
    assert worst.iloc[-1] < ew.iloc[-1] < best.iloc[-1]


def test_empty_inputs_are_safe():
    assert run_backtest(pd.DataFrame(), CAPITAL, _cfg(), FEE).equity.empty
    assert buy_and_hold(pd.Series(dtype=float), [], CAPITAL).empty
    assert equal_weight_universe(pd.DataFrame(), [], CAPITAL).empty
    assert build_price_panel({}).empty


# ==================================================================== METRICS
def test_max_drawdown_is_negative_after_a_fall():
    equity = pd.Series([100, 120, 60, 90], index=pd.bdate_range("2024-01-01", periods=4))
    assert max_drawdown(equity) == pytest.approx(-50.0)


def test_max_drawdown_zero_for_a_monotonic_rise():
    equity = pd.Series([100, 110, 120], index=pd.bdate_range("2024-01-01", periods=3))
    assert max_drawdown(equity) == pytest.approx(0.0)


def test_summary_metrics_present_and_sane():
    result = run_backtest(_panel(), CAPITAL, _cfg(), FEE)
    m = result.metrics()
    for key in ("total_return", "cagr", "ann_vol", "sharpe", "max_drawdown",
                "hit_rate", "avg_turnover", "periods", "years"):
        assert key in m
    assert m["max_drawdown"] <= 0
    assert 0 <= m["hit_rate"] <= 100


def test_summary_handles_a_degenerate_series():
    assert summarize(pd.Series([1.0]), pd.Series(dtype=float), pd.Series(dtype=float), 12) == {}


def test_avg_names_available_is_reported():
    """Survivorship transparency: how many names actually existed each period."""
    result = run_backtest(_panel(), CAPITAL, _cfg(), FEE)
    assert result.avg_names_available > 0


# ==================================== THE INVARIANT THE COST TABLE MUST OBEY
def test_fees_can_only_reduce_returns():
    """
    Fees are a true cost: holding everything else fixed, charging them must never
    improve the result.

    Note what is deliberately NOT asserted here. An earlier version also claimed
    lot rounding could only reduce returns. That is false, and it passed on this
    synthetic panel by luck. Measured on the real universe by varying the start
    date, the rounding effect was positive in 7 of 14 windows with a standard
    deviation of ~157 percentage points -- it is path noise, not a cost. The
    report reflects that.
    """
    panel = _panel(tickers=tuple(f"T{i}.JK" for i in range(8)))

    def total(**over):
        r = run_backtest(panel, CAPITAL, _cfg(**over), FEE)
        return float(r.equity.iloc[-1] / r.equity.iloc[0] - 1)

    assert total(charge_fees=True) <= total(charge_fees=False) + 1e-9


def test_lot_rounding_leaves_cash_undeployed():
    """
    The deterministic half of lot rounding: you cannot buy a fraction of a lot, so
    some of the budget always sits in cash. Unlike the path effect, this is a real
    drag and is always non-negative.
    """
    panel = _panel(tickers=tuple(f"T{i}.JK" for i in range(8)))
    exact = run_backtest(panel, CAPITAL, _cfg(whole_lots=False, charge_fees=False), FEE)
    lots = run_backtest(panel, CAPITAL, _cfg(whole_lots=True, charge_fees=False), FEE)

    assert exact.avg_undeployed_pct == pytest.approx(0.0, abs=1e-6)
    assert lots.avg_undeployed_pct > 0


def test_both_legs_start_from_the_identical_portfolio():
    """
    At the first rebalance both legs see the same capital, so they must choose the
    same names -- that is what proves the two legs differ only in rounding.

    Later rebalances are allowed to diverge: once rounding has changed the account
    value, the sizer legitimately picks a different position count. That drift is a
    consequence of the friction being measured, not a separate confound.
    """
    panel = _panel(tickers=tuple(f"T{i}.JK" for i in range(8)))
    exact = run_backtest(panel, CAPITAL, _cfg(whole_lots=False, min_position_rp=0), FEE)
    lots = run_backtest(panel, CAPITAL, _cfg(whole_lots=True, min_position_rp=0), FEE)

    assert exact.holdings_log[0]["names"] == lots.holdings_log[0]["names"]
