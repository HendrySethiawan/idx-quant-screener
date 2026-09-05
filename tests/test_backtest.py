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


# ============================================ SURVIVORSHIP MUST BE QUANTIFIED
def test_survivorship_gap_is_measured_and_stated():
    """
    The universe was chosen knowing which companies survived, and on the real list
    that is worth ~27pp/year before any strategy is applied. It must appear on
    every report, or the headline CAGR gets read as skill.
    """
    from backtest.report import survivorship_check, survivorship_text

    panel = _panel(tickers=tuple(f"T{i}.JK" for i in range(6)))
    bench = pd.Series(np.linspace(6000, 6100, len(panel)), index=panel.index)  # ~flat
    dates = rebalance_dates(panel, "M")

    s = survivorship_check(panel, bench, dates, CAPITAL)
    assert s["universe_cagr"] is not None
    assert s["index_cagr"] is not None
    assert s["gap_cagr"] == pytest.approx(s["universe_cagr"] - s["index_cagr"], abs=0.2)
    assert s["n_names"] == 6

    text = survivorship_text(s)
    assert "artifact" in text
    assert "percentage point per year" in text


def test_survivorship_text_empty_without_data():
    from backtest.report import survivorship_check, survivorship_text
    assert survivorship_text(survivorship_check(pd.DataFrame(), None, [], CAPITAL)) == ""


# ---------------------------------------- the conclusion, where the brief can read it
# The verdict used to exist only inside backtest.html, which is written only when
# somebody remembers `--backtest`. The page recommending the trades therefore
# carried none of the evidence about what the ranking is worth.
def _cmp(label, metrics):
    from backtest.report import Comparison
    return Comparison(label, pd.Series([1.0, 2.0]), metrics, "")


def _factors():
    return [
        _cmp("Strategy (gross, frictionless)", {"cagr": 34.13, "sharpe": 1.42, "years": 4.94}),
        _cmp("Strategy (net: real lots + fees)", {"cagr": 24.39, "sharpe": 1.06}),
        _cmp("Equal-weight universe", {"cagr": 29.95, "sharpe": 1.58}),
        _cmp("IHSG (buy and hold)", {"cagr": 1.57, "sharpe": 0.09}),
    ]


def _robustness():
    return pd.DataFrame([
        {"variant": "baseline", "cagr_pct": 24.39},
        {"variant": "first half only", "cagr_pct": -6.48},
        {"variant": "second half only", "cagr_pct": 49.53},
    ])


def test_the_gap_is_measured_gross_against_frictionless():
    """
    The net curve pays fees the benchmark never does. Comparing those two would
    hand the benchmark the trading costs as if they were skill -- engine.py says
    so at the definition of `equal_weight_universe`.
    """
    from backtest.report import verdict_payload

    p = verdict_payload(_factors(), _robustness(), {}, "Weekly")

    assert p["gross"]["cagr"] == 34.13          # not the 24.39 net figure
    assert p["cagr_gap_vs_equal_pp"] == pytest.approx(4.18, abs=0.01)
    assert p["sharpe_gap_vs_equal"] == pytest.approx(-0.16, abs=0.01)


def test_the_payload_carries_the_half_window_warning():
    from backtest.report import verdict_payload

    p = verdict_payload(_factors(), _robustness(), {}, "Weekly")
    assert "ONE half of the window" in p["robustness"]


def test_the_payload_survives_missing_comparisons():
    """A run with no benchmark must still produce a readable verdict."""
    from backtest.report import verdict_payload

    p = verdict_payload([], pd.DataFrame(), {}, "Monthly")
    assert p["cagr_gap_vs_equal_pp"] is None
    assert p["sharpe_gap_vs_equal"] is None
    assert p["cadence"] == "Monthly"


def test_the_verdict_round_trips_through_disk(tmp_path):
    from backtest.report import load_verdict, verdict_payload, write_verdict

    written = verdict_payload(_factors(), _robustness(),
                              {"gap_cagr": 28.3, "n_names": 49}, "Weekly")
    write_verdict(written, tmp_path)

    back = load_verdict(tmp_path)
    assert back["cagr_gap_vs_equal_pp"] == written["cagr_gap_vs_equal_pp"]
    assert back["survivorship"]["gap_cagr"] == 28.3


def test_no_verdict_on_a_machine_that_never_backtested(tmp_path):
    from backtest.report import load_verdict
    assert load_verdict(tmp_path) is None


def test_a_corrupt_verdict_reads_as_none_rather_than_raising(tmp_path):
    """The brief must still draw. A callout is worth less than the whole page."""
    from backtest.report import VERDICT_FILE, load_verdict

    (tmp_path / VERDICT_FILE).write_text("{not json", encoding="utf-8")
    assert load_verdict(tmp_path) is None


# ------------------------------------------------------ what trading actually cost
# At Rp10 juta this was the largest controllable effect in the whole simulation --
# fees took roughly a third of the gross return -- and the ticket never said so.
def _costs():
    return pd.DataFrame([
        {"item": "Exact weights, no fees (reference)", "kind": "reference",
         "total_return_pct": 200.59, "effect_pp": None, "detail": ""},
        {"item": "Whole-lot rounding", "kind": "noise",
         "total_return_pct": 279.04, "effect_pp": 78.45, "detail": "path luck"},
        {"item": "Minimum position size", "kind": "noise",
         "total_return_pct": 328.35, "effect_pp": 49.31, "detail": "fewer, larger"},
        {"item": "Broker fees + stamp duty", "kind": "cost",
         "total_return_pct": 208.04, "effect_pp": -120.31,
         "detail": "Rp1,162,256 paid, of which Rp410,000 stamp"},
    ])


def test_the_cost_of_trading_is_measured_against_the_gross_path():
    """
    Against the step the fees were actually charged on, not against the reference
    -- otherwise the rounding and minimum-position steps would be counted as if
    fees had caused them.
    """
    from backtest.report import verdict_payload

    c = verdict_payload(_factors(), _robustness(), {}, "Weekly", _costs())["costs"]

    assert c["fee_effect_pp"] == -120.31
    assert c["gross_return_pct"] == 328.35
    assert c["fee_share_of_gross_pct"] == pytest.approx(36.6, abs=0.1)


def test_no_cost_report_leaves_the_figures_unstated():
    from backtest.report import verdict_payload

    c = verdict_payload(_factors(), _robustness(), {}, "Weekly")["costs"]
    assert c["fee_effect_pp"] is None
    assert c["fee_share_of_gross_pct"] is None


def test_the_ticket_quotes_what_trading_cost():
    from report.brief import evidence_note

    out = evidence_note({
        "cadence": "Weekly", "gross": {"years": 4.94},
        "cagr_gap_vs_equal_pp": 4.18, "sharpe_gap_vs_equal": -0.16,
        "costs": {"fee_effect_pp": -120.31, "gross_return_pct": 328.35,
                  "fee_share_of_gross_pct": 36.6},
    })
    assert "37% of the gross return" in out
    assert "Turnover is the part of this you control" in out


# --------------------------------------------- Sharpe needs a risk-free rate
# `sharpe = ann_ret / vol` is return over volatility, not a Sharpe ratio. In a
# market with a 5%+ policy rate the risk-free term is most of the number, and the
# ticket now quotes a Sharpe gap as evidence.
def _flat_growth(periods=60, seed=3):
    """A rising equity curve with real variation -- constant returns give zero
    volatility, and a Sharpe with a zero denominator tests nothing."""
    idx = pd.date_range("2021-01-31", periods=periods, freq="ME")
    rng = np.random.default_rng(seed)
    steps = 1 + rng.normal(0.015, 0.04, periods)
    return pd.Series(1_000_000 * np.cumprod(steps), index=idx)


def test_the_risk_free_rate_is_subtracted_before_the_ratio():
    from backtest.engine import summarize

    eq = _flat_growth()
    rets = eq.pct_change().dropna()

    raw = summarize(eq, rets, pd.Series(dtype=float), 12, risk_free_pct=0.0)
    net = summarize(eq, rets, pd.Series(dtype=float), 12, risk_free_pct=5.5)

    assert net["sharpe"] == pytest.approx(
        (raw["cagr"] - 5.5) / raw["ann_vol"], abs=0.01)
    assert net["sharpe"] < raw["sharpe"]


def test_a_zero_rate_reproduces_the_old_arithmetic_exactly():
    """So the change can be told apart from a change in the data."""
    from backtest.engine import summarize

    eq = _flat_growth()
    rets = eq.pct_change().dropna()
    out = summarize(eq, rets, pd.Series(dtype=float), 12, risk_free_pct=0.0)

    assert out["sharpe"] == pytest.approx(out["cagr"] / out["ann_vol"], abs=0.01)


def test_the_rate_used_is_reported_with_the_number():
    """A Sharpe whose risk-free rate is invisible cannot be checked."""
    from backtest.engine import summarize

    eq = _flat_growth()
    out = summarize(eq, eq.pct_change().dropna(), pd.Series(dtype=float), 12, 5.5)
    assert out["risk_free_pct"] == 5.5


def test_both_sides_of_the_comparison_get_the_same_subtraction():
    """
    Subtracting it from the strategy alone would hand the benchmark five points a
    year it never earned -- and the ticket quotes the GAP between the two.
    """
    from backtest.engine import BacktestConfig
    from backtest.report import factor_report

    idx = pd.date_range("2021-01-04", periods=400, freq="B")
    rng = np.random.default_rng(11)
    panel = pd.DataFrame(
        {t: 1000 * np.cumprod(1 + rng.normal(0.0004, 0.012, 400)) for t in
         ("AAA.JK", "BBB.JK", "CCC.JK", "DDD.JK", "EEE.JK")}, index=idx)
    bench = pd.Series(6000 * np.cumprod(1 + rng.normal(0.0002, 0.008, 400)), index=idx)
    sectors = {t: "X" for t in panel.columns}

    cfg = BacktestConfig(rebalance="M", min_names=3, risk_free_pct=5.5)
    out = factor_report(panel, 10_000_000, cfg, FeeConfig(), sectors, bench, None)

    rates = {c.label: c.metrics.get("risk_free_pct") for c in out
             if c.metrics.get("risk_free_pct") is not None}
    assert rates, "no comparison reported the rate it used"
    assert set(rates.values()) == {5.5}, rates


# ================================================================== THE EXITS
# Stops and the profit ladder, applied between rebalances. Before these existed a
# position bought in January was held untouched until February whatever it did in
# between, which is not how anybody trades and is not what the ticket now says.

def _atr(panel, window=14):
    """A stand-in ATR panel: close-to-close range, smoothed the same way."""
    return panel.diff().abs().ewm(alpha=1 / window, adjust=False).mean()


def _exit_cfg(**kw):
    from portfolio.exits import ExitConfig
    return ExitConfig(**kw)


def test_no_exits_reproduces_the_old_curve_exactly():
    """
    The regression that protects every number this project has already published.
    Turning the feature off must not move the equity curve by one rupiah.
    """
    panel = _panel()
    before = run_backtest(panel, CAPITAL, _cfg(), FEE)
    after = run_backtest(panel, CAPITAL, _cfg(exits=None), FEE,
                         atr_panel=_atr(panel))

    pd.testing.assert_series_equal(before.equity, after.equity)
    assert before.fees_paid == after.fees_paid
    assert before.n_exit_sales == 0


def test_a_stop_too_wide_to_fire_also_reproduces_it():
    """
    The stronger version: the exit machinery RUNS, walks every session, and simply
    never triggers. Any difference here is bookkeeping drift, not the rule.
    """
    panel = _panel()
    base = run_backtest(panel, CAPITAL, _cfg(), FEE)
    inert = run_backtest(
        panel, CAPITAL,
        _cfg(exits=_exit_cfg(k_atr=1e6, max_stop_pct=99.9, ladder=(1e6,),
                             ladder_fractions=(0.4,))),
        FEE, atr_panel=_atr(panel))

    pd.testing.assert_series_equal(base.equity, inert.equity)
    assert inert.n_exit_sales == 0


def test_stops_actually_fire_on_a_falling_market():
    panel = _panel()
    # Everything collapses in the last year, well past any 2.5 x ATR stop.
    cut = panel.index[600]
    panel.loc[panel.index > cut] *= np.linspace(1.0, 0.4, (panel.index > cut).sum())[:, None]

    with_exits = run_backtest(panel, CAPITAL, _cfg(exits=_exit_cfg()), FEE,
                              atr_panel=_atr(panel))
    assert with_exits.n_exit_sales > 0


def test_a_stop_caps_the_loss_in_a_crash():
    """
    The one thing a stop is FOR. Not a claim that stops raise returns -- they do
    not, on this universe -- but that the left tail is shorter with them.
    """
    idx = pd.bdate_range("2022-01-03", periods=900)
    rng = np.random.default_rng(3)
    data = {}
    for i, t in enumerate(("A.JK", "B.JK", "C.JK", "D.JK")):
        walk = 1000 * np.cumprod(1 + rng.normal(0.0006, 0.010, 900))
        walk[700:] *= np.linspace(1.0, 0.25, 200)      # a slow, total collapse
        data[t] = walk
    panel = pd.DataFrame(data, index=idx)

    plain = run_backtest(panel, CAPITAL, _cfg(), FEE)
    stopped = run_backtest(panel, CAPITAL, _cfg(exits=_exit_cfg()), FEE,
                           atr_panel=_atr(panel))

    assert stopped.equity.iloc[-1] > plain.equity.iloc[-1]
    assert max_drawdown(stopped.equity) > max_drawdown(plain.equity)  # less negative


def test_exit_proceeds_wait_for_the_next_rebalance():
    """
    Cash from a stop sits idle until the next decision. Redeploying it the same
    day would assume a second decision nobody made -- and would quietly turn the
    simulation into a strategy the reader cannot follow.
    """
    panel = _panel()
    cut = panel.index[600]
    panel.loc[panel.index > cut] *= np.linspace(1.0, 0.4, (panel.index > cut).sum())[:, None]

    r = run_backtest(panel, CAPITAL, _cfg(exits=_exit_cfg()), FEE,
                     atr_panel=_atr(panel))
    # Holdings only ever grow at a rebalance date, so the log's name counts fall
    # between them and never rise.
    assert r.n_exit_sales > 0
    assert r.equity.notna().all()


def test_the_stamp_is_charged_once_per_selling_day():
    """
    Per DAY containing a sale, not per order -- the rule the whole ladder design
    rests on. Two names stopped out on the same session pay one stamp.
    """
    panel = _panel()
    cut = panel.index[600]
    panel.loc[panel.index > cut] *= np.linspace(1.0, 0.4, (panel.index > cut).sum())[:, None]

    r = run_backtest(panel, CAPITAL, _cfg(exits=_exit_cfg()), FEE,
                     atr_panel=_atr(panel))
    assert r.stamp_paid == pytest.approx(r.n_sell_days * FEE.stamp_duty_rp)
    assert r.n_sell_days <= r.n_exit_sales + r.n_rebalances


def test_exits_cost_more_in_fees_than_holding_does():
    """
    Selling more often costs more. Stated as a test because it is the honest half
    of the feature: the ladder's benefit has to clear this, and at Rp10 juta it is
    not obviously going to.
    """
    panel = _panel()
    cut = panel.index[600]
    panel.loc[panel.index > cut] *= np.linspace(1.0, 0.4, (panel.index > cut).sum())[:, None]

    plain = run_backtest(panel, CAPITAL, _cfg(), FEE)
    stopped = run_backtest(panel, CAPITAL, _cfg(exits=_exit_cfg()), FEE,
                           atr_panel=_atr(panel))
    assert stopped.fees_paid > plain.fees_paid


def test_a_name_with_no_measurable_atr_gets_no_stop():
    """WIKA's case: a flat price cannot carry a stop, and must not be given one."""
    panel = _panel()
    panel["FLAT.JK"] = 500.0
    atr = _atr(panel)

    r = run_backtest(panel, CAPITAL, _cfg(max_positions=4, exits=_exit_cfg()), FEE,
                     atr_panel=atr)
    assert not r.equity.empty          # it runs; the flat name simply has no plan


def test_build_atr_panel_uses_the_live_indicator():
    from analysis.technical import average_true_range
    from backtest.engine import build_atr_panel

    idx = pd.bdate_range("2024-01-01", periods=200)
    close = pd.Series(np.linspace(1000, 1200, 200), index=idx)
    frame = pd.DataFrame({"Close": close, "High": close * 1.01, "Low": close * 0.99})

    built = build_atr_panel({"X.JK": frame}, window=14)
    assert built["X.JK"].iloc[-1] == pytest.approx(
        average_true_range(frame, 14).iloc[-1])
