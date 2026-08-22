"""
Performance measurement.

The two things that must not be wrong: the IHSG comparison (a naive percentage
would flatter or punish depending on when money went in), and the refusal to
declare a winner on a sample too small to mean anything.
"""
import numpy as np
import pandas as pd
import pytest

from portfolio import journal as J
from portfolio.fees import FeeConfig
from portfolio.performance import (evaluate, ihsg_shadow, stamp_analysis)

CFG = FeeConfig()
CAPITAL = 10_000_000


def _ihsg(start=6000.0, end=6000.0, days=400):
    idx = pd.date_range("2026-01-01", periods=days, freq="D")
    return pd.Series(np.linspace(start, end, days), index=idx)


def _journal(rows):
    """rows: (action, ticker, lots, price, date, source)"""
    df = pd.DataFrame(columns=J.TRADE_COLS)
    for action, ticker, lots, price, date, *rest in rows:
        source = rest[0] if rest else "tool"
        trade = J.build_trade(action, ticker, lots, price, CFG,
                              journal=df, on_date=date, source=source)
        df = pd.concat([df, pd.DataFrame([trade])], ignore_index=True)
        df["date"] = pd.to_datetime(df["date"])
    return df


# ------------------------------------------------------------- shadow portfolio
def test_flat_index_shadow_holds_its_value():
    j = _journal([("BUY", "BBRI", 3, 4150.0, "2026-02-01")])
    s = ihsg_shadow(j, _ihsg(6000, 6000))
    assert s.value_now == pytest.approx(1_245_000, rel=1e-6)


def test_rising_index_grows_the_shadow():
    j = _journal([("BUY", "BBRI", 3, 4150.0, "2026-01-01")])
    s = ihsg_shadow(j, _ihsg(6000, 7200))
    assert s.value_now == pytest.approx(1_245_000 * 1.2, rel=0.02)


def test_falling_index_shrinks_the_shadow():
    j = _journal([("BUY", "BBRI", 3, 4150.0, "2026-01-01")])
    assert ihsg_shadow(j, _ihsg(6000, 4800)).value_now < 1_245_000


def test_shadow_mirrors_the_timing_of_each_flow():
    """
    Buying after a rally buys fewer index units. A naive 'total deployed x index
    return' would miss this entirely -- which is why the shadow exists.
    """
    early = _journal([("BUY", "BBRI", 3, 4150.0, "2026-01-01")])
    late = _journal([("BUY", "BBRI", 3, 4150.0, "2026-12-01")])
    series = _ihsg(6000, 7200)
    assert ihsg_shadow(early, series).value_now > ihsg_shadow(late, series).value_now


def test_sell_redeems_from_the_shadow():
    j = _journal([("BUY", "BBRI", 3, 4150.0, "2026-01-01"),
                  ("SELL", "BBRI", 3, 4150.0, "2026-06-01")])
    s = ihsg_shadow(j, _ihsg(6000, 6000))
    assert s.value_now == pytest.approx(0.0, abs=1.0)


def test_shortfall_is_flagged_not_silently_clamped():
    """Your picks tripled; the shadow cannot fund the same withdrawal."""
    j = _journal([("BUY", "BBRI", 1, 1000.0, "2026-01-01"),
                  ("SELL", "BBRI", 1, 9000.0, "2026-06-01")])
    s = ihsg_shadow(j, _ihsg(6000, 6000))
    assert s.shortfall is True
    assert s.units >= 0


def test_no_index_history_is_reported_as_unavailable():
    j = _journal([("BUY", "BBRI", 3, 4150.0, "2026-01-01")])
    assert ihsg_shadow(j, None).unavailable is True


def test_empty_journal_shadow_is_zero():
    s = ihsg_shadow(pd.DataFrame(columns=J.TRADE_COLS), _ihsg())
    assert s.value_now == 0.0


# ------------------------------------------------------------------ stamp drag
def test_batched_sells_show_no_overpayment():
    j = _journal([("BUY", "BBRI", 3, 4150.0, "2026-01-01"),
                  ("BUY", "TLKM", 2, 2600.0, "2026-01-01"),
                  ("SELL", "BBRI", 3, 4300.0, "2026-06-01"),
                  ("SELL", "TLKM", 2, 2700.0, "2026-06-01")])
    s = stamp_analysis(j, CFG)
    assert s["paid"] == 10_000
    assert s["saved"] == 10_000, "two sells, one stamp -- one stamp saved"
    assert s["avoidable"] == 0, "only one sell day, nothing left to consolidate"


def test_unbatched_sells_show_the_overpayment():
    j = _journal([("BUY", "BBRI", 3, 4150.0, "2026-01-01"),
                  ("BUY", "TLKM", 2, 2600.0, "2026-01-01"),
                  ("SELL", "BBRI", 3, 4300.0, "2026-06-01"),
                  ("SELL", "TLKM", 2, 2700.0, "2026-06-02")])
    s = stamp_analysis(j, CFG)
    assert s["paid"] == 20_000
    assert s["sell_days"] == 2
    assert s["saved"] == 0, "nothing was batched"
    # Reported as an upper bound on opportunity, NOT as a mistake: selling on two
    # different days may have been the right call.
    assert s["avoidable"] == 10_000


def test_no_sells_means_no_stamp():
    j = _journal([("BUY", "BBRI", 3, 4150.0, "2026-01-01")])
    assert stamp_analysis(j, CFG)["paid"] == 0


# -------------------------------------------------------------------- evaluate
def _evaluate(journal, prices, ihsg=None, min_trades=30):
    return evaluate(
        journal=journal, closed=J.closed_trades(journal),
        positions=J.net_positions(journal), prices=prices,
        open_cost=J.average_cost(journal), starting_capital=CAPITAL,
        cfg=CFG, ihsg_close=ihsg, min_trades_for_verdict=min_trades,
    )


def test_empty_journal_reports_starting_capital():
    perf = _evaluate(pd.DataFrame(columns=J.TRADE_COLS), {})
    assert perf.total_value == CAPITAL
    assert perf.cash == CAPITAL
    assert "No trades logged" in perf.verdict


def test_cash_falls_by_the_full_cost_of_a_buy():
    j = _journal([("BUY", "BBRI", 3, 4150.0, "2026-01-01")])
    perf = _evaluate(j, {"BBRI.JK": 4150.0})
    assert perf.cash == pytest.approx(CAPITAL - 1_245_000 - 1_245_000 * 0.0019)


def test_total_value_is_conserved_when_price_is_unchanged():
    """Buy at 4150, still worth 4150: total should be down only by the fee."""
    j = _journal([("BUY", "BBRI", 3, 4150.0, "2026-01-01")])
    perf = _evaluate(j, {"BBRI.JK": 4150.0})
    assert perf.total_value == pytest.approx(CAPITAL - 1_245_000 * 0.0019)


def test_realised_pnl_is_net_of_fees():
    j = _journal([("BUY", "BBRI", 3, 4150.0, "2026-01-01"),
                  ("SELL", "BBRI", 3, 4300.0, "2026-06-01")])
    perf = _evaluate(j, {})
    assert perf.realized_pnl == pytest.approx(28_893.50)


def test_unrealised_pnl_uses_fee_inclusive_cost():
    j = _journal([("BUY", "BBRI", 3, 4150.0, "2026-01-01")])
    perf = _evaluate(j, {"BBRI.JK": 4300.0})
    assert perf.unrealized_pnl == pytest.approx(45_000 - 2_365.50, rel=1e-4)


def test_benchmark_compares_total_wealth_not_bare_positions():
    """
    After closing everything out your position value is 0 while the shadow may
    still hold units. Comparing positions alone reported "-100%", which is
    meaningless -- both sides share the same cash, so totals are the comparison.
    """
    j = _journal([("BUY", "BBRI", 3, 4150.0, "2026-01-01"),
                  ("SELL", "BBRI", 3, 4300.0, "2026-06-01")])
    perf = _evaluate(j, {}, _ihsg(6000, 6000))
    assert perf.position_value == 0
    assert perf.shadow_total == pytest.approx(perf.cash + perf.shadow.value_now)
    assert -100 < perf.vs_ihsg_pct < 100, "percentage blew up against a zero base"


def test_beating_the_index_is_reported_as_ahead():
    j = _journal([("BUY", "BBRI", 3, 4150.0, "2026-01-01")])
    perf = _evaluate(j, {"BBRI.JK": 8300.0}, _ihsg(6000, 6000))  # stock doubled, index flat
    assert perf.vs_ihsg_rp > 0


def test_losing_to_the_index_is_reported_as_behind():
    j = _journal([("BUY", "BBRI", 3, 4150.0, "2026-01-01")])
    perf = _evaluate(j, {"BBRI.JK": 4150.0}, _ihsg(6000, 12000))  # index doubled, stock flat
    assert perf.vs_ihsg_rp < 0


def test_fee_drag_is_reported_against_capital():
    j = _journal([("BUY", "BBRI", 3, 4150.0, "2026-01-01")])
    perf = _evaluate(j, {"BBRI.JK": 4150.0})
    assert perf.fee_drag_pct == pytest.approx(2_365.50 / CAPITAL * 100)


# --------------------------------------------------- the significance guardrail
def test_small_sample_refuses_to_name_a_winner():
    j = _journal([("BUY", "BBRI", 3, 4150.0, "2026-01-01", "tool"),
                  ("SELL", "BBRI", 3, 4300.0, "2026-06-01", "tool")])
    perf = _evaluate(j, {}, min_trades=30)
    assert perf.has_verdict is False
    assert "1 of 30" in perf.verdict
    assert "luck" in perf.verdict


def test_verdict_appears_once_the_sample_is_large_enough():
    rows = []
    for i in range(1, 6):
        rows.append(("BUY", "BBRI", 1, 4000.0, f"2026-01-{i:02d}", "tool"))
        rows.append(("SELL", "BBRI", 1, 4400.0, f"2026-02-{i:02d}", "tool"))
    perf = _evaluate(_journal(rows), {}, min_trades=5)
    assert perf.has_verdict is True
    assert "closed trades" in perf.verdict


def test_attribution_splits_tool_from_own():
    j = _journal([("BUY", "BBRI", 1, 4000.0, "2026-01-01", "tool"),
                  ("SELL", "BBRI", 1, 4400.0, "2026-02-01", "tool"),
                  ("BUY", "TLKM", 1, 2600.0, "2026-01-01", "own"),
                  ("SELL", "TLKM", 1, 2400.0, "2026-02-01", "own")])
    perf = _evaluate(j, {})
    by_source = {a.source: a for a in perf.attribution}
    assert by_source["tool"].net_pnl > 0
    assert by_source["own"].net_pnl < 0


def test_attribution_follows_the_buy_decision():
    """The call being judged is the one that opened the position."""
    j = _journal([("BUY", "BBRI", 1, 4000.0, "2026-01-01", "own"),
                  ("SELL", "BBRI", 1, 4400.0, "2026-02-01", "tool")])
    assert J.closed_trades(j).iloc[0]["source"] == "own"


def test_hit_rate_and_averages():
    j = _journal([("BUY", "BBRI", 1, 4000.0, "2026-01-01"),
                  ("SELL", "BBRI", 1, 5000.0, "2026-02-01"),
                  ("BUY", "TLKM", 1, 3000.0, "2026-01-01"),
                  ("SELL", "TLKM", 1, 2000.0, "2026-02-01")])
    perf = _evaluate(j, {})
    assert perf.n_closed == 2
    assert perf.hit_rate == 50.0
    assert perf.avg_win > 0
    assert perf.avg_loss < 0
