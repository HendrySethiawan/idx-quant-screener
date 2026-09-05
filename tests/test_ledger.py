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
from portfolio.ledger import (implausible_entries, monthly_realized, monthly_totals, open_positions,
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


# ------------------------------------------------ undoing a mistyped trade
def test_removing_the_last_trade_returns_it_and_shortens_the_journal(tmp_path):
    from portfolio.journal import append_trade, load_journal, remove_last_trade

    path = tmp_path / "journal.csv"
    append_trade(build_trade("BUY", "BBRI", 3, 4000, CFG, on_date="2026-06-02"), path)
    append_trade(build_trade("BUY", "TLKM", 2, 2600, CFG, on_date="2026-06-05"), path)

    removed = remove_last_trade(path)
    assert removed["ticker"] == "TLKM.JK"
    remaining = load_journal(path)
    assert len(remaining) == 1 and remaining.iloc[0]["ticker"] == "BBRI.JK"


def test_removing_from_nothing_returns_none(tmp_path):
    from portfolio.journal import remove_last_trade
    assert remove_last_trade(tmp_path / "absent.csv") is None


def test_undo_restores_the_earlier_state_exactly(tmp_path):
    """A typo must leave no trace once removed."""
    from portfolio.journal import append_trade, load_journal, remove_last_trade

    path = tmp_path / "journal.csv"
    append_trade(build_trade("BUY", "SRTG", 6, 1900, CFG, on_date="2026-08-01"), path)
    before = load_journal(path)

    append_trade(build_trade("BUY", "SRTG", 1, 125, CFG, on_date="2026-08-02"), path)
    remove_last_trade(path)

    # .equals, not ==: an unfilled `note` is NaN, and NaN != NaN would fail a
    # dict comparison on two rows that are in fact identical.
    assert load_journal(path).equals(before)


def test_undo_takes_the_newest_when_two_share_a_date(tmp_path):
    """Ties break on insertion order, so it removes what was just added."""
    from portfolio.journal import append_trade, remove_last_trade

    path = tmp_path / "journal.csv"
    append_trade(build_trade("BUY", "AAA", 1, 100, CFG, on_date="2026-08-01"), path)
    append_trade(build_trade("BUY", "ZZZ", 1, 200, CFG, on_date="2026-08-01"), path)
    assert remove_last_trade(path)["ticker"] == "ZZZ.JK"


def test_a_position_is_valued_at_the_market_price_not_what_you_paid(tmp_path):
    """
    The confusion behind the report: AVG COST is what you typed, VALUE NOW is the
    market. A silly entry price must not leak into the valuation.
    """
    from portfolio.journal import append_trade, load_journal

    path = tmp_path / "journal.csv"
    append_trade(build_trade("BUY", "SRTG", 1, 125, CFG, on_date="2026-08-01"), path)

    row = open_positions(load_journal(path), {"SRTG.JK": 1915.0}).iloc[0]
    assert row["avg_cost"] == pytest.approx(125 * 1.0019, abs=1)   # yours
    assert row["value_now"] == pytest.approx(191_500, abs=1)       # the market's


# --------------------------------------------------- a return nobody should believe
# +1412% on a same-day trade implies a 15x move in the price. That is a mistyped
# entry, and printing it beside real results lends it the same authority.
def test_an_absurd_return_is_marked():
    from portfolio.ledger import implausible
    j = _journal([("BUY", "SRTG", 1, 125, "2026-08-22"),
                  ("SELL", "SRTG", 1, 2000, "2026-08-22")])
    row = closed_trades(j).iloc[0]
    assert row["return_pct"] > 1000
    note = implausible(row)
    assert "check the entry" in note
    assert "Rp125" in note and "Rp2,000" in note


def test_an_ordinary_win_is_not_marked():
    from portfolio.ledger import implausible
    j = _journal([("BUY", "BBRI", 3, 4150, "2026-01-05"),
                  ("SELL", "BBRI", 3, 4300, "2026-02-05")])
    assert implausible(closed_trades(j).iloc[0]) == ""


def test_a_total_loss_is_not_marked_as_a_typo():
    """-100% is possible. The band has to be one a real trade cannot reach."""
    from portfolio.ledger import implausible
    j = _journal([("BUY", "BBRI", 3, 4150, "2026-01-05"),
                  ("SELL", "BBRI", 3, 100, "2026-02-05")])
    assert implausible(closed_trades(j).iloc[0]) == ""


def test_a_malformed_row_is_not_marked():
    from portfolio.ledger import implausible
    assert implausible({"return_pct": None, "buy_price": 1, "sell_price": 2}) == ""
    assert implausible({}) == ""


# ------------------------------------------------- the stop, on the review page
# The Markets page is where you ACT on an exit; this table is where you review
# what you hold. Two columns are enough here -- the level, and whether it wants
# attention -- and the full ladder stays on the page that has room for it.

def _plan(price=1795.0, entry=1938.68, lots=10, atr=55.42):
    import numpy as np

    from portfolio.exits import ExitConfig, plan_for
    from portfolio.fees import FeeConfig

    idx = pd.bdate_range("2026-08-26", periods=8)
    closes = pd.Series(np.linspace(entry, price, len(idx)), index=idx)
    return plan_for("SRTG.JK", lots, entry, closes, ExitConfig(), FeeConfig(),
                    atr_rp=atr, entry_date=idx[0], high=closes,
                    capital_rp=10_000_000)


def _positions():
    return pd.DataFrame([{
        "ticker": "SRTG.JK", "shares": 1000, "lots": 10, "avg_cost": 1938.68,
        "cost_basis": 1_938_680.0, "price_now": 1795.0, "value_now": 1_795_000.0,
        "unrealized_pnl": -143_680.0, "unrealized_pct": -7.41,
    }])


def test_the_open_table_carries_the_stop_and_the_next_step():
    from report.journal_view import open_positions_table

    out = open_positions_table(_positions(), {"SRTG.JK": _plan()})
    assert ">Stop<" in out and ">Next step<" in out
    assert "Rp1,800" in out
    assert "sell all 10" in out


def test_a_holding_shows_where_the_next_trim_sits():
    from report.journal_view import open_positions_table

    out = open_positions_table(_positions(), {"SRTG.JK": _plan(price=2000.0)})
    assert "trim at Rp2,077" in out


def test_the_columns_degrade_when_there_is_no_plan():
    """
    A missing plan must render as "-", never as a blank that reads like "no stop
    needed". This is the state right after a snapshot written before exits existed.
    """
    from report.journal_view import open_positions_table

    out = open_positions_table(_positions(), {})
    assert ">Stop<" in out
    assert out.count(">-<") >= 1


# ------------------------------------------ an entry price that cannot be a fill
# `implausible` above catches this once a round-trip is CLOSED, and has since a
# mistyped SRTG entry showed +1412%. Nothing caught it while the position was
# still open, so an AMRT recorded at Rp50 against a Rp1,310 market read as +2,515%
# profit, produced a stop 2,976% away, and put a SELL in the ticket that existed
# only because of the typo.

def _buys(rows):
    """rows: (ticker, price, date)"""
    return pd.DataFrame([{
        "date": d, "ticker": t, "action": "BUY", "lots": 1, "shares": 100,
        "price": p, "gross_rp": p * 100, "fee_rp": 0.0, "stamp_rp": 0.0,
        "net_rp": -p * 100, "source": "own", "note": "",
    } for t, p, d in rows])


def _closes(series):
    """series: {ticker: {date: close}}"""
    frames = {t: pd.Series(v) for t, v in series.items()}
    out = pd.DataFrame(frames)
    out.index = pd.to_datetime(out.index)
    return out


LIVE_CLOSES = _closes({
    "AMRT.JK": {"2026-09-04": 1310.0}, "TINS.JK": {"2026-09-04": 4420.0},
    "ASII.JK": {"2026-09-04": 4900.0},
})


def test_a_dropped_digit_is_caught():
    """The real row: AMRT at Rp50 when Alfamart closed at Rp1,310 that session."""
    bad = implausible_entries(_buys([("AMRT.JK", 50.0, "2026-09-05")]), LIVE_CLOSES)
    assert "AMRT.JK" in bad
    assert "Rp50" in bad["AMRT.JK"] and "Rp1,310" in bad["AMRT.JK"]
    assert "check the entry" in bad["AMRT.JK"]


def test_a_real_fill_is_not_flagged():
    """TINS at Rp4,100 against a Rp4,420 close is 7.2% out -- an ordinary fill."""
    j = _buys([("TINS.JK", 4100.0, "2026-09-05"), ("ASII.JK", 4890.0, "2026-09-05")])
    assert implausible_entries(j, LIVE_CLOSES) == {}


def test_the_threshold_sits_outside_any_idx_daily_move():
    """
    IDX auto-rejection caps a session at 35% / 25% / 20% by price tier, so a fill
    can never be half the reference away. A 34% gap must pass.
    """
    j = _buys([("ASII.JK", 4900.0 * 0.66, "2026-09-05")])
    assert implausible_entries(j, LIVE_CLOSES) == {}
    worse = _buys([("ASII.JK", 4900.0 * 0.49, "2026-09-05")])
    assert "ASII.JK" in implausible_entries(worse, LIVE_CLOSES)


def test_the_reference_is_the_close_on_the_trade_date():
    """
    Not today's close. A position bought at Rp1,000 that has since tripled is the
    case where the number is real and most worth trusting -- comparing it against
    the current price would flag exactly that.
    """
    closes = _closes({"X.JK": {"2026-01-02": 1000.0, "2026-09-04": 3000.0}})
    j = _buys([("X.JK", 1000.0, "2026-01-02")])
    assert implausible_entries(j, closes) == {}


def test_a_name_with_no_price_history_is_left_alone():
    """Absence of a reference is not evidence the entry is wrong."""
    j = _buys([("NEW.JK", 50.0, "2026-09-05")])
    assert implausible_entries(j, LIVE_CLOSES) == {}


def test_a_trade_before_the_series_starts_is_left_alone():
    closes = _closes({"X.JK": {"2026-09-04": 1000.0}})
    j = _buys([("X.JK", 12.0, "2020-01-02")])
    assert implausible_entries(j, closes) == {}


def test_only_buys_are_checked():
    """A sell price is checked by `implausible` once the round-trip closes."""
    j = _buys([("AMRT.JK", 50.0, "2026-09-05")])
    j.loc[0, "action"] = "SELL"
    assert implausible_entries(j, LIVE_CLOSES) == {}


def test_an_empty_journal_flags_nothing():
    assert implausible_entries(None, LIVE_CLOSES) == {}
    assert implausible_entries(pd.DataFrame(), LIVE_CLOSES) == {}
    assert implausible_entries(_buys([("AMRT.JK", 50.0, "2026-09-05")]), None) == {}
