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
    out = _render()
    assert out.startswith("<!doctype html>")
    assert "http://" not in out and "https://" not in out
    assert "<script" not in out


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


def test_write_brief_creates_the_file(tmp_path):
    path = write_brief(_render(), tmp_path)
    assert path.exists()
    assert path.read_text(encoding="utf-8").startswith("<!doctype html>")


@pytest.mark.parametrize("value,expect", [
    (1_245_000, "Rp1,245,000"), (0, "Rp0"), (None, "-"),
])
def test_rp_formatting(value, expect):
    assert rp(value) == expect
