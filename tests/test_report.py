"""
The brief is the deliverable, so it must render offline from a fixture and must
never emit an order the user cannot fill.
"""
import numpy as np
import pandas as pd
import pytest

from market.regime import Regime, Signal
from portfolio.holdings import Holding
from portfolio.sizing import Allocation
from report.assemble import assemble, build_candidates, build_orders
from report.brief import render_brief, rp, write_brief
from report.explain import data_quality_note, reason_phrase, zscore_label


@pytest.fixture
def scored_df():
    """Four names: two liquid, one illiquid, one with no volume at all."""
    return pd.DataFrame([
        # Sign convention: NEGATIVE z on pe_ratio / debt_to_equity is favourable
        # (cheap, low debt); positive z on momentum is favourable.
        {"ticker": "BBRI.JK", "name": "Bank Rakyat", "sector": "Financials",
         "undervaluation_score": 0.95, "last_close": 4150.0,
         "median_daily_value_rp": 800e9, "imputed_factors": "",
         "z_pe_ratio": -1.8, "z_mom_6m": 1.6, "z_debt_to_equity": 1.7, "mom_1m": 3.0},
        {"ticker": "TLKM.JK", "name": "Telkom", "sector": "Infrastructure",
         "undervaluation_score": 0.80, "last_close": 2610.0,
         "median_daily_value_rp": 400e9, "imputed_factors": "dividend_yield",
         "z_pe_ratio": -0.9, "z_mom_6m": -0.2, "mom_1m": -1.0},
        {"ticker": "BBSI.JK", "name": "Krom Bank", "sector": "Financials",
         "undervaluation_score": 0.75, "last_close": 3000.0,
         "median_daily_value_rp": 4.2e6, "imputed_factors": "",
         "z_pe_ratio": -0.5, "mom_1m": 0.0},
        {"ticker": "WIKA.JK", "name": "Wijaya Karya", "sector": "Infrastructure",
         "undervaluation_score": 0.70, "last_close": 300.0,
         "median_daily_value_rp": 0.0, "imputed_factors": "",
         "z_pe_ratio": -0.2, "mom_1m": -15.0},
    ])


@pytest.fixture
def regime_on():
    return Regime([Signal("IHSG trend", "^JKSE", True, "above its 200-day average")],
                  1.0, "RISK-ON", "\U0001F7E2", "Deploy up to 100%.")


# -------------------------------------------------------------------- explain
def test_reason_phrase_names_drivers_and_the_red_flag(scored_df):
    phrase = reason_phrase(scored_df.iloc[0])
    assert "cheap earnings multiple" in phrase.lower()
    assert "but" in phrase.lower()
    assert "indebted" in phrase.lower()


def test_reason_phrase_handles_no_standout_factors():
    assert reason_phrase(pd.Series({"z_pe_ratio": 0.1})) == "Nothing stands out"


def test_reason_phrase_ignores_raw_columns():
    """Only z_* is comparable across factors; a raw P/E of 7 must not leak in."""
    row = pd.Series({"pe_ratio": 7.0, "z_pe_ratio": 0.0})
    assert reason_phrase(row) == "No standout factors"


@pytest.mark.parametrize("factor,z,expect", [
    ("pe_ratio", -2.0, "Much cheaper"),      # negative z on P/E is GOOD
    ("pe_ratio", 2.0, "Expensive"),
    ("mom_6m", 2.0, "Strong 6-month run"),
    ("debt_to_equity", 2.0, "High debt"),
    ("pe_ratio", 0.0, "Around average"),
])
def test_zscore_label_direction(factor, z, expect):
    assert expect in zscore_label(factor, z)


def test_zscore_label_handles_missing():
    assert zscore_label("pe_ratio", None) == "no data"
    assert zscore_label("pe_ratio", np.nan) == "no data"


def test_data_quality_note_flags_imputation(scored_df):
    assert "dividend yield" in data_quality_note(scored_df.iloc[1])
    assert data_quality_note(scored_df.iloc[0]) == ""


# ------------------------------------------------------------------- assemble
def test_illiquid_names_never_become_candidates(scored_df, settings_mock):
    cands, rejected, _ = build_candidates(scored_df, settings_mock, 1.0)
    tickers = [c["ticker"] for c in cands]

    assert "WIKA.JK" not in tickers
    assert "BBSI.JK" not in tickers
    assert "no trading volume" in rejected["WIKA.JK"]
    assert "3,000,000" in rejected["BBSI.JK"] or "4,200,000" in rejected["BBSI.JK"]


def test_sector_cap_is_reported_separately_from_hard_gates(scored_df, settings_mock):
    _, rejected, capped = build_candidates(scored_df, settings_mock, 1.0)
    assert not any("sector cap" in r for r in rejected.values())
    assert all("volume" in r or "day" in r or "price" in r or "slot" in r
               for r in rejected.values())


def test_orders_sell_what_left_the_book(scored_df, settings_mock, regime_on):
    holdings = [Holding("UNVR.JK", lots=5, avg_price=2000.0)]
    plan = assemble(settings_mock, scored_df, regime_on, holdings)
    sells = [o for o in plan["orders"] if o["action"] == "SELL"]
    assert [o["ticker"] for o in sells] == ["UNVR.JK"]


def test_orders_hold_what_is_already_at_target(scored_df, settings_mock, regime_on):
    plan = assemble(settings_mock, scored_df, regime_on, [])
    first = plan["allocation"].positions[0]
    holdings = [Holding(first.ticker, lots=first.lots, avg_price=first.price)]

    plan2 = assemble(settings_mock, scored_df, regime_on, holdings)
    held = [o for o in plan2["orders"] if o["ticker"] == first.ticker]
    assert held[0]["action"] == "HOLD"


def test_every_assembled_order_is_a_whole_lot(scored_df, settings_mock, regime_on):
    plan = assemble(settings_mock, scored_df, regime_on, [])
    for o in plan["orders"]:
        assert o["shares"] % 100 == 0


def test_risk_off_shrinks_the_ticket(scored_df, settings_mock):
    on = assemble(settings_mock, scored_df,
                  Regime([], 1.0, "RISK-ON", "G", ""), [])
    off = assemble(settings_mock, scored_df,
                   Regime([], 0.3, "RISK-OFF", "R", ""), [])
    buys = lambda p: sum(o["rupiah"] for o in p["orders"] if o["action"] == "BUY")
    assert buys(off) < buys(on)


# ---------------------------------------------------------------------- brief
def _render(**kw):
    from portfolio.fees import FeeConfig, estimate_fees
    defaults = dict(
        regime=Regime([Signal("IHSG trend", "^JKSE", True, "above trend")],
                      1.0, "RISK-ON", "G", "Deploy 100%."),
        orders=[{"action": "BUY", "ticker": "BBRI.JK", "lots": 3, "shares": 300,
                 "price": 4150.0, "rupiah": 1_245_000, "note": "target weight 33%"}],
        fees=estimate_fees([{"action": "BUY", "rupiah": 1_245_000}], FeeConfig()),
        capital=10_000_000, holdings_rows=[], candidates=[], rejected={},
        capped={}, allocation=None, universe_n=49, imputed_n=33,
    )
    defaults.update(kw)
    return render_brief(**defaults)


def test_brief_renders_without_network():
    """
    The brief must open from disk with no network. This used to be asserted as
    "no <script> tag at all", which stopped being the property once the
    Simple/Advanced toggle needed inline JS. The real requirement is that nothing
    is *fetched* -- so assert that, rather than banning a tag.
    """
    out = _render()
    assert out.startswith("<!doctype html>")
    assert "http://" not in out and "https://" not in out
    for external in ("<script src", "<link ", "@import", "url(", "srcset"):
        assert external not in out, f"brief reaches outside itself via {external!r}"


def test_brief_shows_the_actual_order():
    out = _render()
    assert "BBRI.JK" in out
    assert "3 lot" in out
    assert "Rp1,245,000" in out


def test_brief_survives_an_empty_day():
    out = _render(orders=[], candidates=[], holdings_rows=[])
    assert "Nothing here today" in out


def test_brief_escapes_untrusted_text():
    out = _render(rejected={"<img src=x onerror=alert(1)>": "nope"})
    assert "<img src=x" not in out
    assert "&lt;img" in out


def test_brief_surfaces_the_batching_tip():
    from portfolio.fees import FeeConfig, estimate_fees
    fees = estimate_fees([{"action": "SELL", "rupiah": 2_000_000}] * 3,
                         FeeConfig(), sell_days=1)
    out = _render(fees=fees)
    assert "SAME DAY" in out
    assert "Rp20,000" in out


def test_brief_states_the_survivorship_caveat():
    assert "delisted" in _render()


# ---------------------------------------------------------------- fair value
def _candidate(**kw):
    base = dict(ticker="BBRI.JK", name="Bank Rakyat", score=0.9, lot_price=415_000,
                reason="cheap on earnings", liquidity_label="ok",
                value_verdict="undervalued", value_zone_lo=4_600.0,
                value_zone_hi=5_100.0, value_gap_pct=-0.10,
                value_peer_group="Financials", value_note="", roe=0.18)
    base.update(kw)
    return base


def test_the_default_view_answers_is_this_cheap():
    """
    The verdict has to be in Simple. It is the question the tool is asked at 12:30,
    and an answer only reachable behind a toggle is not an answer.
    """
    out = _render(candidates=[_candidate()])
    simple = out.split('<div class="adv">')[0]
    assert "Worth vs peers" in simple
    assert "below peers" in simple
    assert "Rp4,600" in simple and "Rp5,100" in simple
    assert "10% below" in simple


@pytest.mark.parametrize("verdict,shown", [
    ("undervalued", "below peers"),
    ("fair", "in line"),
    ("overvalued", "above peers"),
    ("one_measure", "one measure"),
    ("unknown", "cannot value"),
])
def test_every_valuation_state_has_its_own_wording(verdict, shown):
    """Five states, five different messages -- none collapsed into another."""
    out = _render(candidates=[_candidate(value_verdict=verdict)])
    assert shown in out


def test_a_wide_disagreement_is_flagged_in_the_brief():
    out = _render(candidates=[_candidate(
        value_note="the two measures disagree by 177% -- treat this as a hint")])
    assert "measures disagree" in out


def test_the_score_is_not_called_a_valuation():
    """
    undervaluation_score is min-max normalised, so the top name reads 1.00 even in
    a bubble. The brief must not present that as a judgement of value.
    """
    out = _render(candidates=[_candidate()])
    assert "Rank score" in out
    assert ">Score<" not in out


def test_the_brief_admits_what_peer_relative_cannot_see():
    out = _render(candidates=[_candidate()])
    assert "whole market is expensive" in out


def test_a_deserved_premium_is_called_out_not_hidden():
    """A high-ROE name reading 'overvalued' is the method's known blind spot."""
    out = _render(candidates=[_candidate(
        ticker="UNVR.JK", value_verdict="overvalued", value_gap_pct=0.9, roe=0.60)])
    assert "UNVR.JK" in out
    assert "should</em> trade above" in out


def test_holdings_show_when_what_you_own_got_expensive():
    """
    The ticket only proposes a sell when a name leaves the target book, never when
    it merely becomes dear. This column is the only place that shows up.
    """
    out = _render(holdings_rows=[{
        "ticker": "TLKM.JK", "lots": 2, "value": 522_000, "unrealized_pct": 5.0,
        "flags": [], "value_verdict": "overvalued", "value_zone_lo": 1_800.0,
        "value_zone_hi": 2_100.0, "value_gap_pct": 0.24, "value_note": "", "roe": 0.1,
    }])
    simple = out.split('<div class="adv">')[0]
    assert "Worth vs peers" in simple
    assert "above peers" in simple
    assert "24% above" in simple


def test_a_brief_built_before_valuation_existed_still_renders():
    """Candidates with no value_* keys must degrade, not explode."""
    out = _render(candidates=[{"ticker": "BBRI.JK", "name": "x", "score": 0.5,
                               "lot_price": 415_000, "reason": "r",
                               "liquidity_label": "ok"}])
    assert "cannot value" in out
    assert "BBRI.JK" in out


def test_write_brief_creates_the_file(tmp_path):
    path = write_brief(_render(), tmp_path)
    assert path.exists()
    assert path.read_text(encoding="utf-8").startswith("<!doctype html>")


@pytest.mark.parametrize("value,expect", [
    (1_245_000, "Rp1,245,000"), (0, "Rp0"), (None, "-"),
])
def test_rp_formatting(value, expect):
    assert rp(value) == expect


# ------------------------------------------- the top bar states facts about you
# It used to show `allocation.cash_left` under the heading "Cash" and
# `fees.total` under "Est. fees" -- what would be left, and what it would cost, IF
# you executed the suggested buys. Both were sized against the placeholder capital.
# Neither was a fact about the account, and both read as though they were.
class _Perf:
    cash = 6_845_370.0
    position_value = 1_149_000.0
    total_value = 7_994_370.0
    return_pct = -1.2
    realized_pnl = 0.0
    unrealized_pnl = 0.0
    n_closed = 0


def test_the_top_bar_shows_your_cash_not_the_tickets_leftover():
    from portfolio.fees import FeeConfig, estimate_fees

    alloc = Allocation(positions=[], cash_left=31_500.0, budget=0.0,
                       capital=10_000_000, n_positions=4)
    html_out = _render(allocation=alloc, perf=_Perf(),
                       fees=estimate_fees([{"action": "BUY", "rupiah": 20_000_000}],
                                          FeeConfig()))

    head = html_out.split("</header>")[0]
    assert "6,845,370" in head, "your cash is missing from the top bar"
    assert "1,149,000" in head, "your holdings are missing from the top bar"
    assert "31,500" not in head, "the ticket's leftover is being shown as your cash"


def test_the_tickets_own_figures_say_they_are_the_ticket():
    alloc = Allocation(positions=[], cash_left=31_500.0, budget=0.0,
                       capital=10_000_000, n_positions=4)
    html_out = _render(allocation=alloc, perf=_Perf())

    assert "After these buys" in html_out
    assert "Fees on these buys" in html_out
    assert "Paid in" in html_out

    # Only the ticket's own row. The what-if grid still says "Cash left", which is
    # right there: that whole panel is explicitly a hypothesis you are exploring.
    ticket = html_out.split('id="panel-ticket"')[-1].split("</section>")[0]
    assert ">Cash left<" not in ticket
    assert ">Capital<" not in ticket


def test_without_a_performance_object_the_bar_falls_back_to_paid_in():
    """The brief must still render for anyone with no journal at all."""
    head = _render().split("</header>")[0]
    assert "Paid in" in head


def test_the_page_says_how_old_its_data_is():
    import pandas as pd

    fresh = _render(fetched_at=pd.Timestamp.now() - pd.Timedelta(hours=2))
    assert "data as of" in fresh
    assert "Update data" in fresh

    stale = _render(fetched_at=pd.Timestamp.now() - pd.Timedelta(days=3))
    assert "3d old" in stale


def test_no_timestamp_leaves_the_old_subtitle_alone():
    assert "names screened" in _render()


# ------------------------------------------- which session, not when we asked
# "data as of Tue 25 Aug, 01:44" was the moment we fetched. Every price under it
# was the 21 August close, and nothing on the page said so. That one line is why a
# whole screen of stale prices read as current.
def _sessions(session="2026-08-21", market=None, laggards=()):
    return {
        "session_date": pd.Timestamp(session) if session else None,
        "market_session": pd.Timestamp(market) if market else None,
        "behind": None if market is None else pd.Timestamp(session) < pd.Timestamp(market),
        "laggards": list(laggards),
        "mixed": bool(laggards),
    }


def test_the_header_names_the_session_the_prices_came_from():
    head = _render(sessions=_sessions("2026-08-24"),
                   fetched_at=pd.Timestamp("2026-08-25 01:44")).split("</header>")[0]
    assert "prices from Mon 24 Aug close" in head
    assert "fetched 01:44" in head


def test_the_fetch_time_alone_never_stands_in_for_the_session():
    """The exact shape of the original defect."""
    head = _render(sessions=_sessions("2026-08-21"),
                   fetched_at=pd.Timestamp("2026-08-25 01:44")).split("</header>")[0]
    assert "prices from Fri 21 Aug" in head
    assert "data as of Tue 25 Aug" not in head


def test_being_behind_the_market_is_said_in_the_header_and_on_the_ticket():
    out = _render(sessions=_sessions("2026-08-21", market="2026-08-24"),
                  fetched_at=pd.Timestamp("2026-08-25 01:44"))

    assert "BEHIND THE MARKET" in out.split("</header>")[0]
    assert "Prices are from the Fri 21 Aug close" in out
    assert "market has since traded Mon 24 Aug" in out
    assert "Indopremier" in out


def test_a_stale_ticket_keeps_its_lot_counts():
    """You chose to be told, not to be stopped."""
    out = _render(sessions=_sessions("2026-08-21", market="2026-08-24"))
    assert "BBRI.JK" in out
    assert "3 lot" in out


def test_current_prices_raise_no_banner():
    out = _render(sessions=_sessions("2026-08-24", market="2026-08-24"))
    assert "Prices are from the" not in out
    assert "BEHIND THE MARKET" not in out


def test_mixed_sessions_are_named_beside_the_candidates():
    """
    Every score is a z-score against peers, so names priced on different days are
    not being compared. Real data had 50 tickers on one session and 1 on another.
    """
    out = _render(sessions=_sessions(
        "2026-08-24", laggards=[("ADHI.JK", "2026-08-21"), ("WIKA.JK", "2026-08-21")]))

    assert "2 of 49 names are priced on an older session" in out
    assert "ADHI.JK (2026-08-21)" in out


def test_one_session_for_everyone_raises_nothing():
    out = _render(sessions=_sessions("2026-08-24"))
    assert "priced on an older session" not in out


def test_the_page_still_renders_with_no_session_information():
    """Anything built before this existed, and any snapshot without it."""
    assert "IDX Terminal" in _render()
    assert "names screened" in _render()


# ------------------------------- what the ranking is worth, next to the ranking
# The backtest's conclusions lived only in backtest.html, produced only by
# --backtest. So the panel headed "Do this today" stated the least-supported
# output the tool has in its most confident voice, while a file three directories
# away said the strategy worked in one half of the window.
def _verdict(**over):
    base = {
        "cadence": "Weekly",
        "gross": {"cagr": 34.13, "sharpe": 1.42, "years": 4.94},
        "equal_weight": {"cagr": 29.95, "sharpe": 1.58},
        "cagr_gap_vs_equal_pp": 4.18,
        "sharpe_gap_vs_equal": -0.16,
        "robustness": "It worked in only ONE half of the window - that is a warning.",
        "survivorship": {"universe_cagr": 29.6, "index_cagr": 1.3, "gap_cagr": 28.3,
                         "n_names": 49},
    }
    base.update(over)
    return base


def test_the_ticket_says_what_the_ranking_is_worth():
    from report.brief import evidence_note

    out = evidence_note(_verdict())
    assert "4.2pp a year" in out
    assert "0.16 of Sharpe" in out
    assert "gave up" in out
    assert "only ONE half of the window" in out


def test_the_comparison_is_named_as_before_costs():
    """
    Gross against frictionless is the only fair pairing -- engine.py says so. A
    reader who thinks this is net would be comparing a fee-paying strategy against
    a benchmark that never trades.
    """
    from report.brief import evidence_note

    assert "before costs" in evidence_note(_verdict())


def test_the_survivorship_artifact_is_named():
    """Most of the absolute return is the ticker list. That has to be said here."""
    from report.brief import evidence_note

    out = evidence_note(_verdict())
    assert "+29.6% a year" in out
    assert "knowing who survived" in out


def test_a_ranking_that_beat_the_benchmark_reads_that_way():
    from report.brief import evidence_note

    out = evidence_note(_verdict(cagr_gap_vs_equal_pp=6.0, sharpe_gap_vs_equal=0.3))
    assert "added <strong>6.0pp a year</strong>" in out
    assert "added <strong>0.30 of Sharpe</strong>" in out
    assert "gave up" not in out


def test_never_backtested_says_so_rather_than_staying_silent():
    """Silence would read as endorsement, which is the failure being fixed."""
    from report.brief import evidence_note

    out = evidence_note(None)
    assert "never been tested on this machine" in out
    assert "--backtest" in out
    assert "hypothesis, not a finding" in out


def test_the_note_reaches_the_ticket_panel():
    out = _render(verdict=_verdict())
    ticket = out.split('id="panel-ticket"')[-1].split("</section>")[0]
    assert "What this ranking is worth" in ticket


def test_the_untested_warning_reaches_the_ticket_panel_too():
    ticket = _render().split('id="panel-ticket"')[-1].split("</section>")[0]
    assert "never been tested on this machine" in ticket


# ----------------------------- names that could not be fetched, and benchmark bases
def test_unfetched_names_are_named_on_the_ticket():
    """
    A ticker that fails silently changes the peer group every other name is
    z-scored against. The page must not go on implying it screened all 49.
    """
    out = _render(sessions={"session_date": None, "laggards": [], "mixed": False,
                            "behind": None, "missing": ["GOTO.JK", "WIKA.JK"]})
    ticket = out.split('id="panel-ticket"')[-1].split("</section>")[0]

    assert "2 names could not be fetched" in ticket
    assert "GOTO.JK, WIKA.JK" in ticket
    assert "smaller group than usual" in ticket


def test_one_unfetched_name_reads_as_singular():
    out = _render(sessions={"session_date": None, "laggards": [], "mixed": False,
                            "behind": None, "missing": ["WIKA.JK"]})
    assert "1 name could not be fetched" in out
    assert "is absent from the ranking" in out


def test_a_clean_fetch_raises_nothing():
    assert "could not be fetched" not in _render()


def test_the_two_benchmarks_state_which_basis_they_use():
    """
    Stock closes are dividend-adjusted (BBRI: 9.5% over a year); ^JKSE is a price
    index and pays nothing. Printed side by side they read as comparable, and they
    are not.
    """
    from report.journal_view import brief_section

    class _Perf:
        total_value = 10_000_000.0
        cash = 8_000_000.0
        position_value = 2_000_000.0
        return_pct = 1.0
        realized_pnl = unrealized_pnl = 0.0
        total_fees = fee_drag_pct = 0.0
        closed_cost = open_cost = 0.0
        return_on_closed_pct = return_on_open_pct = None
        dividend_income = 0.0
        realised_yield_pct: dict = {}
        n_closed = 0
        comparable = True
        shadow_total = 9_700_000.0
        vs_ihsg_rp = 300_000.0
        vs_ihsg_pct = 3.1
        watchlist_total = 10_100_000.0
        vs_watchlist_rp = -100_000.0
        vs_watchlist_pct = -1.0
        stamp_paid = stamp_saved = stamp_avoidable = 0.0
        sell_days = 0
        verdict = "Not enough data yet."

        class shadow:
            unavailable = False
            shortfall = False

    out = brief_section(_Perf())

    assert "PRICE" in out and "pays no dividends" in out      # the IHSG line
    assert "dividends included" in out                        # the watchlist line
    assert "same basis as your own total" in out


# ===================================================================== EXITS
# The tool decided what to buy and never decided what to sell. `build_orders`
# emitted a sale for one reason only -- the name fell out of the target book on a
# re-rank -- so a position could halve with nothing on the page mentioning it.

def _plan_for(ticker="SRTG.JK", lots=10, entry=1938.68, price=1795.0, atr=55.42,
              original=None):
    from portfolio.exits import ExitConfig, plan_for
    from portfolio.fees import FeeConfig

    idx = pd.bdate_range("2026-08-26", periods=8)
    closes = pd.Series(np.linspace(entry, price, len(idx)), index=idx)
    return plan_for(ticker, lots, entry, closes, ExitConfig(), FeeConfig(),
                    atr_rp=atr, entry_date=idx[0], high=closes,
                    original_lots=original, capital_rp=10_000_000)


def test_a_breached_stop_becomes_a_sell_in_the_ticket():
    """The live case: SRTG bought at 1,938.68, stop 1,800, last close 1,795."""
    plan = _plan_for()
    alloc = Allocation(positions=[], budget=0, capital=10_000_000)
    holdings = [Holding("SRTG.JK", lots=10, avg_price=1938.68)]

    orders = build_orders(alloc, holdings, {"SRTG.JK": 1795.0},
                          exit_plans={"SRTG.JK": plan})
    sells = [o for o in orders if o["ticker"] == "SRTG.JK"]
    assert len(sells) == 1
    assert sells[0]["action"] == "SELL"
    assert sells[0]["lots"] == 10
    assert "1,800" in sells[0]["note"]
    assert sells[0]["stop_rp"] == pytest.approx(1800.13, abs=0.02)


def test_an_exit_is_not_also_emitted_by_the_rebalance():
    """
    The duplicate the ordering has to prevent: a name being stopped out AND
    dropping from the target book would otherwise appear twice, once sold on the
    stop and once sold on the re-rank.
    """
    plan = _plan_for()
    alloc = Allocation(positions=[], budget=0, capital=10_000_000)
    holdings = [Holding("SRTG.JK", lots=10, avg_price=1938.68)]

    orders = build_orders(alloc, holdings, {"SRTG.JK": 1795.0},
                          exit_plans={"SRTG.JK": plan})
    assert [o["ticker"] for o in orders].count("SRTG.JK") == 1


def test_a_trim_sells_only_the_stage():
    from portfolio.exits import TRIM

    plan = _plan_for(price=2100.0)      # past the +1R level of 2,077
    assert plan.action == TRIM
    alloc = Allocation(positions=[], budget=0, capital=10_000_000)
    holdings = [Holding("SRTG.JK", lots=10, avg_price=1938.68)]

    orders = build_orders(alloc, holdings, {"SRTG.JK": 2100.0},
                          exit_plans={"SRTG.JK": plan})
    row = next(o for o in orders if o["ticker"] == "SRTG.JK")
    assert row["action"] == "SELL" and row["exit_kind"] == "TRIM"
    assert row["lots"] == 4 and row["shares"] == 400


def test_the_cooldown_blocks_a_re_buy():
    from portfolio.sizing import Position

    pos = Position("SRTG.JK", 1795.0, 5, 500, 897_500, 1.0, 1.0, 179_500)
    alloc = Allocation(positions=[pos], budget=1_000_000, capital=10_000_000)

    orders = build_orders(alloc, [], {"SRTG.JK": 1795.0}, cooling={"SRTG.JK": 7})
    row = next(o for o in orders if o["ticker"] == "SRTG.JK")
    assert row["action"] == "WAIT"
    assert row["lots"] == 0 and row["rupiah"] == 0.0
    assert "7 more sessions" in row["note"]


def test_the_cooldown_also_blocks_topping_a_trimmed_position_back_up():
    """
    The bug the backtest found. Blocking only names you no longer hold left the
    ladder undoing itself: trim 4 lots at +1R, and the next re-rank tops the
    position straight back to target, paying both sides to end where it started.
    """
    from portfolio.sizing import Position

    pos = Position("SRTG.JK", 1795.0, 10, 1000, 1_795_000, 1.0, 1.0, 179_500)
    alloc = Allocation(positions=[pos], budget=2_000_000, capital=10_000_000)
    holdings = [Holding("SRTG.JK", lots=6, avg_price=1938.68)]

    orders = build_orders(alloc, holdings, {"SRTG.JK": 1795.0},
                          cooling={"SRTG.JK": 8})
    row = next(o for o in orders if o["ticker"] == "SRTG.JK")
    assert row["action"] == "HOLD"
    assert row["lots"] == 6                     # not topped back up to 10
    assert "not topping this back up" in row["note"]


def test_every_buy_carries_its_stop_and_what_it_risks(scored_df, settings_mock,
                                                      regime_on):
    """
    The half of the request about going IN. A proposal is not "Rp1.2 juta of
    BBRI"; it is "Rp83,000 at risk, 0.8% of everything you have".
    """
    df = scored_df.copy()
    df["atr_14"] = [80.0, 55.0, 40.0, 30.0][: len(df)]
    plan = assemble(settings_mock, df, regime_on, [])

    buys = [o for o in plan["orders"] if o["action"] == "BUY"]
    assert buys
    for o in buys:
        assert o["stop_rp"] < o["price"]
        assert o["risk_rp"] > 0
        assert o["risk_pct"] == pytest.approx(
            o["risk_rp"] / settings_mock.capital_rp * 100, abs=1e-6)


def test_the_ticket_carries_every_field_the_six_columns_did():
    """
    The ticket is stacked blocks now, not a table. Six columns holding ~155
    characters in a 360px panel wrapped the size over six lines and pushed Stop
    and Why off the right edge, where they were never seen. The shape changed;
    what it must show did not.
    """
    out = _render(orders=[{
        "action": "BUY", "ticker": "BBRI.JK", "lots": 3, "shares": 300,
        "price": 4150.0, "rupiah": 1_245_000, "note": "target weight 33%",
        "stop_rp": 3950.0, "risk_rp": 60_000.0, "risk_pct": 0.6,
        "risk_over": False, "risk_capped": False,
    }])
    for fragment in ("BUY", "BBRI.JK", "3 lot", "300 shares", "Rp4,150",
                     "Rp1,245,000", "Rp3,950", "risk Rp60,000",
                     "target weight 33%"):
        assert fragment in out, f"the ticket lost {fragment!r}"


def test_the_ticket_never_scrolls_sideways():
    """
    The failure this replaced: `.scroll` is `overflow:auto`, so the two columns
    added most recently sat behind a horizontal scrollbar inside a narrow panel.
    """
    import re

    out = _render(orders=[{
        "action": "SELL", "ticker": "ADMR.JK", "lots": 1, "shares": 100,
        "price": 1655.0, "rupiah": 165_500, "note": "no longer in the target book",
        "stop_rp": 1462.0, "stop_kind": "initial",
    }])
    ticket = re.search(r'id="panel-ticket".*?</section>', out, re.S).group(0)
    assert 'class="scroll"' not in ticket
    assert "<table" not in ticket


def test_a_trim_still_reads_as_a_trim():
    out = _render(orders=[{
        "action": "SELL", "ticker": "AMRT.JK", "lots": 28, "shares": 2800,
        "price": 1310.0, "rupiah": 3_668_000, "exit_kind": "TRIM",
        "note": "1,310 has reached the +2R level of 1,131",
    }])
    assert "TRIM" in out
    assert '<div class="ord trim">' in out


def test_a_position_over_the_risk_budget_is_flagged():
    out = _render(orders=[{
        "action": "BUY", "ticker": "INET.JK", "lots": 36, "shares": 3600,
        "price": 366.0, "rupiah": 1_317_600, "note": "target weight 33%",
        "stop_rp": 311.0, "risk_rp": 220_000.0, "risk_pct": 2.2,
        "risk_over": True, "risk_capped": True,
    }])
    assert "pill warn" in out
    assert "capped" in out


def test_the_exit_panel_lists_the_whole_ladder():
    from portfolio.exits import ExitConfig

    out = _render(exit_plans={"SRTG.JK": _plan_for()}, exit_cfg=ExitConfig())
    assert "Exit plan" in out
    assert "SELL all 10" in out
    assert "Rp2,077" in out and "Rp2,216" in out     # both rungs
    assert "runs on the trailing stop" in out
    assert "cannot watch these for you" in out


def test_the_exit_panel_says_when_a_position_cannot_be_staged():
    plan = _plan_for("BUVA.JK", lots=4, entry=790.0, price=800.0, atr=45.0)
    out = _render(exit_plans={"BUVA.JK": plan})
    assert "too small to stage" in out


def test_the_book_risk_callout_totals_every_stop():
    out = _render(open_risk={"total_rp": 740_000.0, "pct_of_capital": 7.4,
                             "n_positions": 4, "n_without_stop": 0})
    assert "Rp740,000" in out
    assert "7.4% of your" in out


def test_the_exit_evidence_is_quoted_from_the_backtest():
    from report.brief import exits_note

    note = exits_note({"cadence": "Monthly", "exits": {
        "cagr_gap_pp": -13.3, "drawdown_gap_pp": -0.4, "sharpe_gap": -0.41,
        "extra_fees_rp": 706_602.0, "extra_sell_days": 66,
        "stop_only_cagr_gap_pp": 3.0}})
    assert "cost 13.3pp a year" in note
    assert "0.4pp deeper" in note
    assert "monthly rebalance" in note
    assert "paid Rp706,602 more in fees" in note
    assert "-0.41 of Sharpe" in note


def test_the_stop_without_the_ladder_is_reported_on_its_own():
    """
    The row that decides whether the ladder should be on at all. Reporting only the
    combined gap would let the ladder's cost read as the stop's.
    """
    from report.brief import exits_note

    note = exits_note({"cadence": "Monthly", "exits": {
        "cagr_gap_pp": -13.3, "drawdown_gap_pp": -0.4, "sharpe_gap": -0.41,
        "stop_only_cagr_gap_pp": 3.0}})
    assert "no profit-taking at all" in note
    assert "ahead of holding by <strong>3.0pp a year" in note
    assert "16.3pp between them is the ladder" in note
    assert "risk.ladder: []" in note


def test_the_ladder_cost_is_named_even_when_both_variants_trail():
    """
    The weekly case. "The stop earns its place" is false there -- stop-only is also
    behind -- but the gap between the two is still the ladder's own cost, and that
    is the sentence that has to survive both signs.
    """
    from report.brief import exits_note

    note = exits_note({"cadence": "Weekly", "exits": {
        "cagr_gap_pp": -12.6, "drawdown_gap_pp": -0.1, "sharpe_gap": -0.42,
        "stop_only_cagr_gap_pp": -6.3}})
    assert "behind holding by <strong>6.3pp a year" in note
    assert "6.3pp between them is the ladder" in note
    assert "earns its place" not in note


def test_no_ladder_cost_claim_when_the_ladder_did_not_cost():
    from report.brief import exits_note

    note = exits_note({"cadence": "Monthly", "exits": {
        "cagr_gap_pp": 2.0, "drawdown_gap_pp": 1.0, "sharpe_gap": 0.1,
        "stop_only_cagr_gap_pp": 1.0}})
    assert "is the ladder" not in note


def test_a_saved_fee_is_not_reported_as_a_cost():
    """
    The cooldown can CUT turnover, so the gap goes negative. Printing "paid
    Rp-47,040 more" was the first thing the real backtest produced.
    """
    from report.brief import exits_note

    note = exits_note({"cadence": "Weekly", "exits": {
        "cagr_gap_pp": -11.5, "drawdown_gap_pp": -0.3, "sharpe_gap": -0.37,
        "extra_fees_rp": -47_040.0, "extra_sell_days": 54}})
    assert "saved Rp47,040 in fees" in note
    assert "Rp-47,040" not in note
    assert "0.3pp deeper" in note


def test_no_backtest_means_no_exit_claim():
    from report.brief import exits_note
    assert exits_note(None) == ""
    assert exits_note({"cadence": "Monthly"}) == ""


def test_giving_up_return_for_a_smaller_worst_case_is_reported_as_such():
    """
    The live weekly result on the 74-name universe: the ladder costs 3.4pp of
    return and cuts the worst drawdown by 20.9pp. Quoting only the cost would
    argue one side of a trade the Sharpe figure settles.
    """
    from report.brief import exits_note

    note = exits_note({"cadence": "Weekly", "exits": {
        "cagr_gap_pp": -3.42, "drawdown_gap_pp": 20.88, "sharpe_gap": 0.45,
        "extra_fees_rp": 131_580.0, "extra_sell_days": 88,
        "stop_only_cagr_gap_pp": -0.71}})
    assert "cost 3.4pp a year" in note
    assert "20.9pp shallower" in note
    assert "+0.45 of Sharpe" in note


# ======================================================== TIES IN THE TICKET
# Picks 3 to 8 sit 0.02 to 0.21 raw points apart against a universe spread of
# 3.75, and the ticket buys the top three. The tie band says which of them the
# score cannot actually separate.

def test_the_candidate_carries_the_unnormalised_score(scored_df, settings_mock):
    """
    `score` is min-max scaled to 0-1 for display, so the best name is always 1.00
    whatever the market did -- useless for asking how FAR apart two names are.
    `score_floor` is measured on `raw_score`, so the candidate must carry it.
    """
    df = scored_df.copy()
    df["raw_score"] = [8.5, 7.7, 6.2, 6.1][: len(df)]
    cands, _, _ = build_candidates(df, settings_mock, 1.0)
    assert cands
    for c in cands:
        assert "raw_score" in c
    assert {c["raw_score"] for c in cands} != {c["score"] for c in cands}


def test_a_floor_on_the_wrong_scale_cannot_reorder_the_whole_book(
        scored_df, settings_mock, regime_on):
    """
    The bug this guard exists for. The floor is measured on `raw_score`, whose
    spread is about 3.75; the display score runs 0 to 1. Compared against the
    wrong one, EVERY adjacent gap fell under the floor, all 74 names became a
    single tie group, and the entire ticket was chosen by correlation with the
    ranking ignored. A group covering most of the universe is a unit mismatch, not
    a market observation.
    """
    df = pd.DataFrame([{
        "ticker": f"T{i:02d}.JK", "name": f"T{i}", "sector": "Financials",
        "undervaluation_score": 1.0 - i * 0.05, "raw_score": 9.0 - i,
        "last_close": 1000.0, "median_daily_value_rp": 800e9,
        "imputed_factors": "", "z_pe_ratio": -0.5, "mom_1m": 1.0,
    } for i in range(20)])
    # One genuine tie, so the sane case has something to find.
    df.loc[1, "raw_score"] = df.loc[0, "raw_score"] - 0.02

    sane = assemble(settings_mock, df, regime_on, [], score_floor=0.10)
    absurd = assemble(settings_mock, df, regime_on, [], score_floor=1_000.0)

    assert any(len(g) > 1 for g in sane["tie_groups"]), "a sane floor finds ties"
    assert all(len(g) == 1 for g in absurd["tie_groups"]), (
        "an impossible floor must be refused, not applied to the whole universe")
    order = [c["ticker"] for c in absurd["candidates"]]
    assert order == sorted(order), "with ties refused, the score order must survive"


def test_the_ticket_names_which_picks_are_level():
    from report.brief import ties_note

    note = ties_note([["TINS.JK", "ADRO.JK"], ["UNVR.JK"],
                      ["TAPG.JK", "SRTG.JK"]], 0.11)
    assert "TINS = ADRO" in note
    assert "TAPG = SRTG" in note
    assert "UNVR" not in note                 # a group of one is not a tie
    assert "0.11" in note


def test_no_tie_note_when_nothing_is_tied():
    from report.brief import ties_note
    assert ties_note([["A"], ["B"]], 0.11) == ""
    assert ties_note(None, 0.11) == ""
    assert ties_note([["A", "B"]], 0.0) == ""     # no floor measured, no claim


# ================================================ AN ENTRY THAT LOOKS WRONG
# The totals keep it and name it. Silently excluding a row would be the tool
# deciding which of your records to count, and if the price were merely unusual
# rather than wrong it would be hiding a real position.

BAD_ENTRY = ("recorded at Rp50, which is 96% from the Rp1,310 close on 05 Sep 26 "
             "- check the entry against your broker")


def _flagged_plan():
    from portfolio.exits import ExitConfig, plan_for
    from portfolio.fees import FeeConfig

    idx = pd.bdate_range("2026-09-05", periods=3)
    closes = pd.Series([1310.0] * 3, index=idx)
    return plan_for("AMRT.JK", 1, 50.0, closes, ExitConfig(), FeeConfig(),
                    atr_rp=45.79, entry_date=idx[0], high=closes,
                    capital_rp=10_000_000, entry_note=BAD_ENTRY)


def test_the_exit_panel_says_check_the_entry_instead_of_a_plan():
    from portfolio.exits import ExitConfig

    out = _render(exit_plans={"AMRT.JK": _flagged_plan()}, exit_cfg=ExitConfig())
    assert "check the entry" in out
    assert "Rp1,310" in out
    assert "CHECK ENTRY" in out


def test_a_flagged_position_shows_no_stop_and_no_trim_level():
    from portfolio.exits import ExitConfig

    out = _render(exit_plans={"AMRT.JK": _flagged_plan()}, exit_cfg=ExitConfig())
    assert "too small to stage" not in out       # no ladder was built
    assert "trailing stop" not in out


def test_the_headline_names_what_the_totals_contain():
    from report.journal_view import _bad_entry_note

    positions = pd.DataFrame([
        {"ticker": "AMRT.JK", "unrealized_pnl": 125_990.50},
        {"ticker": "TINS.JK", "unrealized_pnl": 31_221.00},
    ])
    note = _bad_entry_note({"AMRT.JK": BAD_ENTRY}, positions)

    assert "AMRT" in note
    assert "Rp125,991" in note or "Rp125,990" in note
    assert "Nothing has been removed" in note
    assert "check the entry" in note


def test_no_headline_note_when_every_entry_looks_sound():
    from report.journal_view import _bad_entry_note
    assert _bad_entry_note({}, pd.DataFrame()) == ""
    assert _bad_entry_note(None, None) == ""


def test_the_same_day_sell_advice_appears_exactly_once():
    """
    `estimate_fees` already emits it through `fees.notes`. A second callout added
    during the exits work printed "Rp40,000 saved" twice in a row on a five-sell
    ticket.
    """
    from portfolio.fees import FeeConfig, estimate_fees

    sells = [{"action": "SELL", "ticker": f"T{i}.JK", "lots": 1, "shares": 100,
              "price": 1000.0, "rupiah": 100_000.0, "note": "left the book"}
             for i in range(5)]
    out = _render(orders=sells,
                  fees=estimate_fees(sells, FeeConfig(), 10_000_000, sell_days=1))

    # Matched on the saving itself, not on the phrase "same day" -- the benchmark
    # callouts legitimately say "the same rupiah, moved on the same days".
    assert out.count("Rp40,000") == 1
    assert out.count("Execute all 5 sells") == 1


# ======================================================================
# The two panels answer with one voice.
#
# "SELL ADRO.JK - no longer in the target book" sat in the ticket beside
# "HOLD - stop 2,502, sell at 2,908" in the exit panel, on the same screen,
# with nothing saying which to follow.
# ======================================================================
def test_the_exit_panel_shows_the_books_verdict_not_its_own():
    from portfolio.exits import DERISK

    plan = _plan_for("ADRO.JK", lots=1, entry=2705.0, price=2720.0, atr=87.0)
    assert plan.action == "HOLD"                     # the plan is content
    plan.book_action = "SELL"                        # the book is not
    plan.book_reason = "cutting the book back to today's Rp3,000,000 budget"
    plan.book_cause = DERISK

    out = _render(exit_plans={"ADRO.JK": plan})
    # The verdict cell, not the tab strip: "SELL all 1" is the exit panel's own
    # phrasing and appears nowhere else on the page.
    assert "SELL all 1" in out
    assert "cutting the book back" in out
    # ...and its own HOLD verdict is gone, not shown alongside.
    assert ">HOLD</span>" not in out


def test_a_plan_the_book_left_alone_still_shows_its_own_verdict():
    plan = _plan_for("ADRO.JK", lots=1, entry=2705.0, price=2720.0, atr=87.0)
    out = _render(exit_plans={"ADRO.JK": plan})
    assert ">HOLD</span>" in out
    assert "SELL all" not in out


def test_the_ticket_says_why_it_is_buying_nothing():
    """
    In de-risk mode there are no BUY rows while "Best candidates you can afford"
    still lists names. Without this the page contradicts itself again, quietly.
    """
    out = _render(book_state={"derisking": True, "book_rp": 6_920_000.0,
                              "budget_rp": 3_000_000.0})
    assert "Cutting back, not buying" in out
    assert "Rp6,920,000" in out and "Rp3,000,000" in out


def test_no_over_budget_callout_when_the_book_fits():
    out = _render(book_state={"derisking": False, "book_rp": 1_000.0,
                              "budget_rp": 3_000_000.0})
    assert "Cutting back, not buying" not in out


def test_the_ticket_no_longer_says_no_longer_in_the_target_book():
    """
    One label covered a rank-4 de-risk and a rank-33 rotation. Whatever a row
    says now, it must not say that -- it named neither cause.
    """
    out = _render(orders=[{
        "action": "SELL", "ticker": "ADRO.JK", "lots": 1, "shares": 100,
        "price": 2720.0, "rupiah": 272_000,
        "note": "cutting the book back to today's Rp3,000,000 budget",
    }])
    assert "no longer in the target book" not in out
