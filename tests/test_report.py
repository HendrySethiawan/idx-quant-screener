"""
The brief is the deliverable, so it must render offline from a fixture and must
never emit an order the user cannot fill.
"""
import numpy as np
import pandas as pd
import pytest

from market.regime import Regime, Signal
from portfolio.holdings import Holding
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
