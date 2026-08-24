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
