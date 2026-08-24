"""
Dividends received, and the hole they fill.

`dividend_yield` carries +1.0 weight in `factor_weights` -- among the largest --
so the screener actively steers toward high-yield names. Until this existed
`VALID_ACTIONS` was `("BUY", "SELL")` and nothing anywhere recorded income: every
realised return was understated by exactly the thing the ranking was chasing, and
the factor most worth auditing was the one that could not be measured.

The load-bearing test is test_a_dividend_never_reaches_the_trade_log. Income has
no shares, no fee and no stamp; if it ever entered FIFO matching it would corrupt
every round-trip after it.
"""
import pandas as pd
import pytest

from portfolio import dividends as D


def _ledger(tmp_path, rows):
    """rows: (ticker, amount, date)"""
    path = tmp_path / "dividends.csv"
    for ticker, amount, when in rows:
        D.append_entry(D.build_entry(ticker, amount, when), path)
    return path


# ------------------------------------------------------------------ building
def test_a_dividend_is_recorded_against_its_holding():
    entry = D.build_entry("bbri", 120_000, "2026-04-15", "annual")
    assert entry["ticker"] == "BBRI.JK"      # normalised like every other ticker
    assert entry["amount_rp"] == 120_000
    assert entry["date"] == "2026-04-15"
    assert entry["note"] == "annual"


def test_a_negative_dividend_is_refused():
    """
    A rights issue or a capital return is a different event. Letting one in as
    income would flatter the very factor this file exists to measure.
    """
    with pytest.raises(ValueError):
        D.build_entry("BBRI", -50_000)
    with pytest.raises(ValueError):
        D.build_entry("BBRI", 0)


def test_an_unparseable_amount_is_refused_rather_than_guessed():
    with pytest.raises(ValueError):
        D.build_entry("BBRI", "a lot")


def test_an_empty_ticker_is_refused():
    with pytest.raises(ValueError):
        D.build_entry("", 100_000)


# ------------------------------------------------------------------ totalling
def test_income_totals_across_every_holding(tmp_path):
    path = _ledger(tmp_path, [
        ("BBRI", 120_000, "2026-04-15"),
        ("TLKM", 80_000, "2026-05-20"),
        ("BBRI", 60_000, "2026-10-15"),
    ])
    ledger = D.load_dividends(path)

    assert D.total_received(ledger) == 260_000
    assert D.by_ticker(ledger) == {"BBRI.JK": 180_000, "TLKM.JK": 80_000}


def test_an_empty_ledger_is_zero_not_an_error(tmp_path):
    assert D.total_received(D.load_dividends(tmp_path / "none.csv")) == 0.0
    assert D.total_received(None) == 0.0
    assert D.by_ticker(None) == {}


def test_realised_yield_is_measured_against_what_the_position_cost(tmp_path):
    """
    The number that can be set beside the forward yield Yahoo quoted. They are
    allowed to differ -- the point is that the difference becomes visible.
    """
    path = _ledger(tmp_path, [("BBRI", 120_000, "2026-04-15")])
    out = D.realised_yield(D.load_dividends(path), {"BBRI.JK": 2_000_000})

    assert out == {"BBRI.JK": 6.0}


def test_a_holding_with_no_cost_basis_gets_no_yield(tmp_path):
    """Dividing by nothing would invent a number, usually an infinite one."""
    path = _ledger(tmp_path, [("BBRI", 120_000, "2026-04-15")])
    assert D.realised_yield(D.load_dividends(path), {"BBRI.JK": 0}) == {}


def test_a_corrupt_file_degrades_quietly(tmp_path):
    path = tmp_path / "dividends.csv"
    path.write_text("not a csv at all\x00", encoding="utf-8")
    assert D.load_dividends(path).empty


# ---------------------------------------------------------------- removal
def test_any_entry_can_be_removed(tmp_path):
    path = _ledger(tmp_path, [("BBRI", 120_000, "2026-04-15"),
                              ("TLKM", 80_000, "2026-05-20")])
    assert D.remove_entry_at(path, 0)["ok"]
    assert D.total_received(D.load_dividends(path)) == 80_000


def test_a_row_that_is_not_the_one_displayed_is_refused(tmp_path):
    path = _ledger(tmp_path, [("BBRI", 120_000, "2026-04-15"),
                              ("TLKM", 80_000, "2026-05-20")])

    stale = D.remove_entry_at(path, 1, {"ticker": "BBRI.JK", "amount_rp": 120_000})
    assert not stale["ok"]
    assert len(D.load_dividends(path)) == 2

    assert D.remove_entry_at(path, 0, {"ticker": "BBRI.JK",
                                       "amount_rp": 120_000})["ok"]


def test_removing_out_of_range_says_so_rather_than_raising(tmp_path):
    path = _ledger(tmp_path, [("BBRI", 120_000, "2026-04-15")])
    assert not D.remove_entry_at(path, 9)["ok"]
    assert not D.remove_entry_at(path, "x")["ok"]
    assert not D.remove_entry_at(tmp_path / "none.csv", 0)["ok"]


# --------------------------------------------------- it must never touch FIFO
def test_a_dividend_never_reaches_the_trade_log(tmp_path, settings_mock):
    """
    The reason this is a separate file. Income has no shares, no fee and no
    stamp; entering FIFO matching it would corrupt every round-trip after it.
    """
    from portfolio import journal as J
    from portfolio.fees import FeeConfig

    journal_path = tmp_path / "journal.csv"
    trade = J.build_trade("BUY", "BBRI", 2, 4000, FeeConfig(), on_date="2026-04-01")
    J.append_trade(trade, journal_path)

    _ledger(tmp_path, [("BBRI", 120_000, "2026-04-15")])

    journal = J.load_journal(journal_path)
    assert len(journal) == 1, "the dividend leaked into the journal"
    assert set(journal["action"]) == {"BUY"}
    assert J.net_positions(journal) == {"BBRI.JK": 200}
    assert J.closed_trades(journal).empty


def test_income_lifts_cash_and_total_but_not_realised_pnl(settings_mock, tmp_path):
    """
    A dividend is not a trading decision. Folding it into realised P&L would make
    a bad entry read as a good one because the company happened to pay out.
    """
    from portfolio import journal as J
    from portfolio.fees import FeeConfig
    from portfolio.performance import evaluate

    cfg = FeeConfig()
    df = pd.DataFrame(columns=J.TRADE_COLS)
    for action, price, when in (("BUY", 4000.0, "2026-04-01"),
                                ("SELL", 4300.0, "2026-06-01")):
        t = J.build_trade(action, "BBRI", 2, price, cfg, journal=df, on_date=when)
        df = pd.concat([df, pd.DataFrame([t])], ignore_index=True)
    df["date"] = pd.to_datetime(df["date"])

    path = _ledger(tmp_path, [("BBRI", 120_000, "2026-05-15")])
    divs = D.load_dividends(path)

    without = evaluate(journal=df, closed=J.closed_trades(df),
                       positions=J.net_positions(df), prices={},
                       open_cost=J.average_cost(df), starting_capital=10_000_000,
                       cfg=cfg)
    with_div = evaluate(journal=df, closed=J.closed_trades(df),
                        positions=J.net_positions(df), prices={},
                        open_cost=J.average_cost(df), starting_capital=10_000_000,
                        cfg=cfg, dividends=divs)

    assert with_div.dividend_income == 120_000
    assert with_div.cash == pytest.approx(without.cash + 120_000)
    assert with_div.total_value == pytest.approx(without.total_value + 120_000)
    assert with_div.total_pnl == pytest.approx(without.total_pnl + 120_000)
    # The trading figures are untouched.
    assert with_div.realized_pnl == without.realized_pnl
    assert with_div.n_closed == without.n_closed


def test_income_does_not_flatter_the_index_comparison(settings_mock, tmp_path):
    """
    ^JKSE is a price index and pays nothing, so counting income on our side alone
    would hand us the dividend as though it were stock picking. Cash is common to
    both sides by construction, so it cancels -- asserted, because that is a
    property of the arithmetic rather than an intention.
    """
    from portfolio import journal as J
    from portfolio.fees import FeeConfig
    from portfolio.performance import evaluate

    cfg = FeeConfig()
    t = J.build_trade("BUY", "BBRI", 2, 4000, cfg, on_date="2026-04-01")
    df = pd.DataFrame([t])
    df["date"] = pd.to_datetime(df["date"])

    idx = pd.Series([6000.0, 6300.0],
                    index=pd.to_datetime(["2026-04-01", "2026-06-01"]))
    args = dict(journal=df, closed=J.closed_trades(df),
                positions=J.net_positions(df), prices={"BBRI.JK": 4300.0},
                open_cost=J.average_cost(df), starting_capital=10_000_000,
                cfg=cfg, ihsg_close=idx)

    path = _ledger(tmp_path, [("BBRI", 120_000, "2026-05-15")])
    without = evaluate(**args)
    with_div = evaluate(**args, dividends=D.load_dividends(path))

    assert with_div.vs_ihsg_rp == pytest.approx(without.vs_ihsg_rp)


def test_income_with_no_trades_at_all_still_counts(settings_mock, tmp_path):
    """A dividend often lands after the position is closed, or before any trade."""
    from portfolio.fees import FeeConfig
    from portfolio.journal import TRADE_COLS
    from portfolio.performance import evaluate

    path = _ledger(tmp_path, [("BBRI", 120_000, "2026-05-15")])
    perf = evaluate(journal=pd.DataFrame(columns=TRADE_COLS), closed=pd.DataFrame(),
                    positions={}, prices={}, open_cost={},
                    starting_capital=10_000_000, cfg=FeeConfig(),
                    dividends=D.load_dividends(path))

    assert perf.dividend_income == 120_000
    assert perf.cash == 10_120_000
    assert perf.total_value == 10_120_000


# ------------------------------------------------------------------ on the page
def _perf(income=120_000.0, realised=None):
    from portfolio.performance import Performance
    return Performance(
        starting_capital=10_000_000, cash=8_547_017, position_value=1_590_000,
        total_value=10_137_017, dividend_income=income,
        realised_yield_pct=realised if realised is not None else {"BBRI.JK": 7.63},
    )


def test_the_income_tile_shows_what_the_holding_actually_paid():
    from report.journal_view import brief_section

    out = brief_section(_perf())
    assert "Dividends" in out
    assert "Rp120,000" in out
    assert "BBRI 7.6% on cost" in out


def test_no_income_means_no_tile():
    """
    A permanent "Rp0 dividends" would read as "these names pay nothing", which is
    a different claim from "none has been recorded".
    """
    from report.journal_view import brief_section

    assert "Dividends" not in brief_section(_perf(income=0.0, realised={}))


def test_the_ledger_lists_every_dividend_with_a_remove_control(tmp_path):
    from report.journal_view import dividend_table

    path = _ledger(tmp_path, [("BBRI", 120_000, "2026-04-15"),
                              ("TLKM", 80_000, "2026-05-20")])
    html_out = dividend_table(D.load_dividends(path))

    assert html_out.count("rm-div") == 2
    assert 'data-ticker="BBRI.JK"' in html_out
    assert "Rp200,000" in html_out          # the footer total
    assert "net of the 10% final tax" in html_out


def test_an_empty_ledger_says_so_rather_than_drawing_an_empty_table():
    from report.journal_view import dividend_table

    assert "No dividends recorded" in dividend_table(None)
