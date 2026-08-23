"""
Tests for the monthly ledger.

The load-bearing one is test_the_months_add_up_to_the_headline. A monthly table
whose rows do not sum to the overall figure is worse than no table, because the
headline is what gets quoted and the rows are what get believed.

Second is test_a_closed_month_never_changes_again. Selling in October must not move
September's row, or month-on-month comparison means nothing.
"""
import pandas as pd
import pytest

from portfolio.fees import FeeConfig
from portfolio.journal import build_trade, closed_trades
from portfolio.ledger import (monthly_realized, monthly_totals, open_positions,
                              recent_trades)

CFG = FeeConfig()


def _journal(rows):
    """Build a journal the way the app does, so the fees are the real ones."""
    out = pd.DataFrame()
    for action, ticker, lots, price, date in rows:
        trade = build_trade(action, ticker, lots, price, CFG,
                            journal=out if not out.empty else None, on_date=date)
        out = pd.concat([out, pd.DataFrame([trade])], ignore_index=True)
    out["date"] = pd.to_datetime(out["date"])
    return out


@pytest.fixture
def three_months():
    return _journal([
        ("BUY", "BBRI", 3, 4000, "2026-06-02"),
        ("SELL", "BBRI", 3, 4200, "2026-06-20"),      # closed in June
        ("BUY", "TLKM", 2, 2600, "2026-06-25"),
        ("SELL", "TLKM", 2, 2500, "2026-07-10"),      # bought June, closed July
        ("BUY", "ADRO", 4, 2500, "2026-07-15"),
        ("SELL", "ADRO", 4, 2700, "2026-08-05"),      # bought July, closed August
    ])


# ------------------------------------------------------------- the arithmetic
def test_the_months_add_up_to_the_headline(three_months):
    closed = closed_trades(three_months)
    monthly = monthly_realized(closed)

    assert monthly["net_pnl"].sum() == pytest.approx(closed["net_pnl"].sum(), abs=0.01)
    assert monthly["gross_pnl"].sum() == pytest.approx(closed["gross_pnl"].sum(), abs=0.01)
    assert monthly["fees"].sum() == pytest.approx(closed["fees"].sum(), abs=0.01)
    assert monthly["trades"].sum() == len(closed)


def test_the_footer_matches_the_rows(three_months):
    monthly = monthly_realized(closed_trades(three_months))
    totals = monthly_totals(monthly)
    assert totals["net_pnl"] == pytest.approx(monthly["net_pnl"].sum(), abs=0.01)
    assert totals["trades"] == int(monthly["trades"].sum())
    assert totals["months"] == len(monthly)


def test_a_trade_counts_in_the_month_it_was_sold(three_months):
    """TLKM was bought in June and sold in July. It belongs to July."""
    monthly = monthly_realized(closed_trades(three_months))
    months = list(monthly["month"])
    assert months == ["2026-06", "2026-07", "2026-08"]
    assert int(monthly.loc[monthly["month"] == "2026-07", "trades"].iloc[0]) == 1


def test_a_closed_month_never_changes_again(three_months):
    """Selling later must not move an earlier month's row."""
    before = monthly_realized(closed_trades(three_months))
    june_before = before[before["month"] == "2026-06"].iloc[0].to_dict()

    later = pd.concat([three_months, pd.DataFrame([
        build_trade("BUY", "BBCA", 1, 9000, CFG, journal=three_months, on_date="2026-09-01"),
        build_trade("SELL", "BBCA", 1, 9500, CFG, journal=three_months, on_date="2026-09-20"),
    ])], ignore_index=True)
    later["date"] = pd.to_datetime(later["date"])

    after = monthly_realized(closed_trades(later))
    assert after[after["month"] == "2026-06"].iloc[0].to_dict() == june_before


def test_the_net_is_after_every_cost():
    """
    One round-trip, checked by hand. 3 lots of BBRI at 4,000 bought and 4,200 sold:
      gross    300 x 200            = 60,000
      buy fee  1,200,000 x 0.0019   =  2,280
      sell fee 1,260,000 x 0.0029   =  3,654
      stamp                          = 10,000
      net                            = 44,066
    """
    journal = _journal([
        ("BUY", "BBRI", 3, 4000, "2026-06-02"),
        ("SELL", "BBRI", 3, 4200, "2026-06-20"),
    ])
    row = closed_trades(journal).iloc[0]

    assert row["gross_pnl"] == pytest.approx(60_000, abs=0.01)
    assert row["fees"] == pytest.approx(2_280 + 3_654 + 10_000, abs=0.01)
    assert row["net_pnl"] == pytest.approx(44_066, abs=0.01)

    monthly = monthly_realized(closed_trades(journal))
    assert monthly.iloc[0]["net_pnl"] == pytest.approx(44_066, abs=0.01)


def test_a_loss_stays_a_loss(three_months):
    """TLKM was sold below cost; July must be negative."""
    monthly = monthly_realized(closed_trades(three_months))
    assert float(monthly.loc[monthly["month"] == "2026-07", "net_pnl"].iloc[0]) < 0


def test_win_rate_is_the_share_of_profitable_round_trips(three_months):
    monthly = monthly_realized(closed_trades(three_months))
    assert float(monthly.loc[monthly["month"] == "2026-06", "win_rate"].iloc[0]) == 1.0
    assert float(monthly.loc[monthly["month"] == "2026-07", "win_rate"].iloc[0]) == 0.0


# --------------------------------------------------------------- still open
def test_open_positions_exclude_anything_already_sold(three_months):
    extra = pd.concat([three_months, pd.DataFrame([
        build_trade("BUY", "SRTG", 6, 1900, CFG, journal=three_months, on_date="2026-08-20"),
    ])], ignore_index=True)
    extra["date"] = pd.to_datetime(extra["date"])

    positions = open_positions(extra, {"SRTG.JK": 1915.0})
    assert list(positions["ticker"]) == ["SRTG.JK"]
    assert int(positions.iloc[0]["shares"]) == 600


def test_average_cost_includes_the_buy_fee():
    """It is the price the position has to beat to be genuinely ahead."""
    journal = _journal([("BUY", "SRTG", 6, 1900, "2026-08-20")])
    row = open_positions(journal, {}).iloc[0]
    assert row["avg_cost"] > 1900, "the fee was left out of the cost"
    assert row["cost_basis"] == pytest.approx(600 * 1900 * 1.0019, abs=1.0)


def test_a_position_with_no_price_is_still_listed():
    """A holding that vanishes because a price is missing is a gap people trade on."""
    journal = _journal([("BUY", "SRTG", 6, 1900, "2026-08-20")])
    row = open_positions(journal, {}).iloc[0]
    assert row["ticker"] == "SRTG.JK"
    assert row["value_now"] is None and row["unrealized_pnl"] is None


def test_unrealized_is_measured_against_the_fee_inclusive_cost():
    journal = _journal([("BUY", "SRTG", 6, 1900, "2026-08-20")])
    row = open_positions(journal, {"SRTG.JK": 1900.0}).iloc[0]
    assert row["unrealized_pnl"] < 0, "flat on price is a small loss after the fee"


# ------------------------------------------------------------------- empties
@pytest.mark.parametrize("call", [
    lambda: monthly_realized(None),
    lambda: monthly_realized(pd.DataFrame()),
    lambda: open_positions(None),
    lambda: open_positions(pd.DataFrame()),
])
def test_nothing_recorded_is_not_an_error(call):
    assert call().empty


def test_totals_of_nothing_are_zero():
    assert monthly_totals(pd.DataFrame())["net_pnl"] == 0.0


def test_recent_trades_are_newest_first(three_months):
    out = recent_trades(three_months, limit=3)
    assert len(out) == 3
    assert list(out["date"]) == sorted(out["date"], reverse=True)
