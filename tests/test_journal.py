"""
Journal correctness.

The arithmetic test below is the anchor: a Rp45,000 gross gain is Rp28,893 after
Indopremier takes its cut, and the report must show the second number.
"""
import pandas as pd
import pytest

from portfolio import journal as J
from portfolio.fees import FeeConfig

CFG = FeeConfig()


def _log(path, action, ticker, lots, price, on_date, source="tool"):
    trade = J.build_trade(action, ticker, lots, price, CFG,
                          journal=J.load_journal(path), on_date=on_date, source=source)
    return J.append_trade(trade, path)


# ------------------------------------------------------------------- normalise
@pytest.mark.parametrize("raw,expect", [
    ("bbri", "BBRI.JK"), ("BBRI", "BBRI.JK"), ("BBRI.JK", "BBRI.JK"),
    (" tlkm ", "TLKM.JK"), ("^JKSE", "^JKSE"), ("IDR=X", "IDR=X"),
])
def test_normalize_ticker(raw, expect):
    assert J.normalize_ticker(raw) == expect


def test_normalize_rejects_empty():
    with pytest.raises(ValueError):
        J.normalize_ticker("   ")


# ----------------------------------------------------------------- build_trade
def test_buy_costs_are_correct():
    t = J.build_trade("BUY", "BBRI", 3, 4150.0, CFG, on_date="2026-08-23")
    assert t["shares"] == 300
    assert t["gross_rp"] == pytest.approx(1_245_000)
    assert t["fee_rp"] == pytest.approx(1_245_000 * 0.0019)
    assert t["stamp_rp"] == 0                      # no stamp on buys
    assert t["net_rp"] == pytest.approx(-(1_245_000 + 1_245_000 * 0.0019))


def test_sell_costs_include_stamp():
    t = J.build_trade("SELL", "BBRI", 3, 4300.0, CFG, on_date="2026-08-23")
    assert t["gross_rp"] == pytest.approx(1_290_000)
    assert t["stamp_rp"] == 10_000
    assert t["net_rp"] == pytest.approx(1_290_000 - 1_290_000 * 0.0029 - 10_000)


def test_net_rp_sign_convention():
    """Buys take cash out, sells put it back. The shadow benchmark relies on this."""
    assert J.build_trade("BUY", "A", 1, 1000.0, CFG)["net_rp"] < 0
    assert J.build_trade("SELL", "A", 1, 1000.0, CFG)["net_rp"] > 0


@pytest.mark.parametrize("lots,price", [(0, 100.0), (-1, 100.0), (1, 0.0), (1, -5.0)])
def test_invalid_trades_are_rejected(lots, price):
    with pytest.raises(ValueError):
        J.build_trade("BUY", "BBRI", lots, price, CFG)


def test_unknown_action_is_rejected():
    with pytest.raises(ValueError):
        J.build_trade("SHORT", "BBRI", 1, 100.0, CFG)


# ----------------------------------------------------------- stamp per sell-day
def test_second_sell_same_day_pays_no_stamp(tmp_path):
    path = tmp_path / "j.csv"
    _log(path, "BUY", "BBRI", 3, 4150.0, "2026-08-01")
    _log(path, "BUY", "TLKM", 2, 2600.0, "2026-08-01")
    _log(path, "SELL", "BBRI", 3, 4300.0, "2026-08-20")
    journal = _log(path, "SELL", "TLKM", 2, 2700.0, "2026-08-20")

    sells = journal[journal.action == "SELL"]
    assert list(sells.stamp_rp) == [10_000, 0]


def test_sells_on_different_days_each_pay_stamp(tmp_path):
    path = tmp_path / "j.csv"
    _log(path, "BUY", "BBRI", 3, 4150.0, "2026-08-01")
    _log(path, "BUY", "TLKM", 2, 2600.0, "2026-08-01")
    _log(path, "SELL", "BBRI", 3, 4300.0, "2026-08-20")
    journal = _log(path, "SELL", "TLKM", 2, 2700.0, "2026-08-21")

    assert journal[journal.action == "SELL"].stamp_rp.sum() == 20_000


# --------------------------------------------------------------- the anchor sum
def test_round_trip_pnl_is_net_of_every_fee(tmp_path):
    """
    BUY  3 lot BBRI @ 4150  ->  gross 1,245,000  fee 2,365.50
    SELL 3 lot BBRI @ 4300  ->  gross 1,290,000  fee 3,741.00  stamp 10,000
    gross profit 45,000  -  fees 16,106.50  =  net 28,893.50
    """
    path = tmp_path / "j.csv"
    _log(path, "BUY", "BBRI", 3, 4150.0, "2026-08-01")
    journal = _log(path, "SELL", "BBRI", 3, 4300.0, "2026-08-20")

    closed = J.closed_trades(journal)
    assert len(closed) == 1
    row = closed.iloc[0]

    assert row["gross_pnl"] == pytest.approx(45_000)
    assert row["fees"] == pytest.approx(16_106.50)
    assert row["net_pnl"] == pytest.approx(28_893.50)
    assert row["holding_days"] == 19


# ------------------------------------------------------------------- FIFO logic
def test_fifo_matches_oldest_lot_first(tmp_path):
    path = tmp_path / "j.csv"
    _log(path, "BUY", "BBRI", 1, 4000.0, "2026-01-05")
    _log(path, "BUY", "BBRI", 1, 5000.0, "2026-02-05")
    journal = _log(path, "SELL", "BBRI", 1, 6000.0, "2026-03-05")

    closed = J.closed_trades(journal)
    assert len(closed) == 1
    assert closed.iloc[0]["buy_price"] == 4000.0, "matched the newer lot, not FIFO"


def test_partial_sell_leaves_the_rest_open(tmp_path):
    path = tmp_path / "j.csv"
    _log(path, "BUY", "BBRI", 5, 4000.0, "2026-01-05")
    journal = _log(path, "SELL", "BBRI", 2, 4500.0, "2026-02-05")

    assert J.closed_trades(journal).iloc[0]["shares"] == 200
    assert J.net_positions(journal) == {"BBRI.JK": 300}


def test_one_sell_can_close_several_buy_lots(tmp_path):
    path = tmp_path / "j.csv"
    _log(path, "BUY", "BBRI", 1, 4000.0, "2026-01-05")
    _log(path, "BUY", "BBRI", 1, 4200.0, "2026-01-06")
    journal = _log(path, "SELL", "BBRI", 2, 4500.0, "2026-02-05")

    closed = J.closed_trades(journal)
    assert len(closed) == 2
    assert sorted(closed["buy_price"]) == [4000.0, 4200.0]


def test_fully_closed_position_disappears(tmp_path):
    path = tmp_path / "j.csv"
    _log(path, "BUY", "BBRI", 3, 4150.0, "2026-08-01")
    journal = _log(path, "SELL", "BBRI", 3, 4300.0, "2026-08-20")
    assert J.net_positions(journal) == {}


def test_sell_without_a_matching_buy_does_not_crash(tmp_path):
    """A position opened before the journal existed. Report, don't explode."""
    path = tmp_path / "j.csv"
    journal = _log(path, "SELL", "BBRI", 3, 4300.0, "2026-08-20")
    assert J.closed_trades(journal).empty
    assert J.net_positions(journal) == {}


# ------------------------------------------------------------------ cost basis
def test_average_cost_includes_fees(tmp_path):
    path = tmp_path / "j.csv"
    journal = _log(path, "BUY", "BBRI", 3, 4150.0, "2026-08-01")
    cost = J.average_cost(journal)["BBRI.JK"]
    assert cost > 4150.0, "fees must be capitalised into the cost basis"
    assert cost == pytest.approx((1_245_000 + 1_245_000 * 0.0019) / 300)


def test_average_cost_after_partial_sell_uses_remaining_lots(tmp_path):
    path = tmp_path / "j.csv"
    _log(path, "BUY", "BBRI", 1, 4000.0, "2026-01-05")
    _log(path, "BUY", "BBRI", 1, 5000.0, "2026-02-05")
    journal = _log(path, "SELL", "BBRI", 1, 6000.0, "2026-03-05")
    assert J.average_cost(journal)["BBRI.JK"] == pytest.approx(5000 * 1.0019, rel=1e-4)


# ------------------------------------------------------------------- IO safety
def test_missing_file_returns_empty_frame(tmp_path):
    df = J.load_journal(tmp_path / "nope.csv")
    assert df.empty
    assert list(df.columns) == J.TRADE_COLS


def test_corrupt_file_degrades_quietly(tmp_path):
    path = tmp_path / "j.csv"
    path.write_text("\x00\x01 not a csv", encoding="utf-8")
    assert J.load_journal(path).empty


def test_roundtrip_through_disk(tmp_path):
    path = tmp_path / "j.csv"
    journal = _log(path, "BUY", "BBRI", 3, 4150.0, "2026-08-01")
    assert len(J.load_journal(path)) == len(journal) == 1


def test_marks_roundtrip_and_dedupe(tmp_path):
    path = tmp_path / "m.csv"
    J.append_mark(1_000_000, 500_000, 6100.0, path, on_date="2026-08-01")
    marks = J.append_mark(1_100_000, 400_000, 6200.0, path, on_date="2026-08-01")
    assert len(marks) == 1, "same-day mark should overwrite, not duplicate"
    assert marks.iloc[0]["total_rp"] == 1_500_000
