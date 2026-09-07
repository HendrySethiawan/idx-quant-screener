"""
The Method page, and the two defects it was written to expose.

Both defects were found by measuring rather than by reading, so both are pinned
by a test that fails on the old behaviour:

  1. The eligible universe used to be a function of the REGIME, because the
     liquidity slot was scaled by `deploy_pct`. A risk-off reading shrank the slot
     and so LOOSENED the cap -- 36 names against 16 at Rp10 miliar, most
     permissive exactly when the market was worst.
  2. Above a certain account size nothing on the list can absorb a slot, and the
     app rendered that as an empty ticket plus 74 rejection lines -- 74 true
     statements and not the one that mattered.

The page's numbers are computed from the frame, never written down, so the tests
change the frame and assert the numbers move with it.
"""
import pandas as pd
import pytest

from core.config import Settings
from report.method import (BANDS, capital_ladder, render_method,
                           sector_exposure)


@pytest.fixture
def frame():
    """Three names an order of magnitude apart in turnover."""
    return pd.DataFrame([
        {"ticker": "BIG.JK", "median_daily_value_rp": 600e9, "last_close": 1000.0},
        {"ticker": "MID.JK", "median_daily_value_rp": 40e9, "last_close": 1000.0},
        {"ticker": "SMALL.JK", "median_daily_value_rp": 300e6, "last_close": 1000.0},
    ])


def _settings(capital: float) -> Settings:
    s = Settings(sectors={"A.JK": "Energy", "B.JK": "Basic Materials",
                          "C.JK": "Financials", "D.JK": "Healthcare"})
    s.account = {**s.account, "capital_rp": capital, "max_positions": 6}
    return s


# ============================================================ defect 1: the gate
def test_the_eligible_universe_no_longer_moves_with_the_regime(frame,
                                                               settings_mock):
    """
    THE defect-2 regression. `build_candidates` used to scale the slot by
    `deploy_pct`, so risk-off widened the menu. It no longer takes `deploy_pct`
    at all, and the proof is that the signature cannot express the bug.
    """
    import inspect

    from report.assemble import build_candidates
    assert "deploy_pct" not in inspect.signature(build_candidates).parameters


def test_a_risk_off_run_offers_the_same_names_as_a_risk_on_one(settings_mock):
    """
    The behavioural half: two regimes, one universe. Built on names whose turnover
    straddles the cap at this capital, so the old deploy-scaled gate would have
    admitted more of them in risk-off.
    """
    from market.regime import Regime, Signal
    from report.assemble import assemble

    scored_df = pd.DataFrame([
        {"ticker": "BBRI.JK", "name": "Bank Rakyat", "sector": "Financials",
         "undervaluation_score": 0.95, "last_close": 4150.0,
         "median_daily_value_rp": 800e9, "imputed_factors": ""},
        {"ticker": "TLKM.JK", "name": "Telkom", "sector": "Infrastructure",
         "undervaluation_score": 0.80, "last_close": 2610.0,
         "median_daily_value_rp": 30e9, "imputed_factors": ""},
        {"ticker": "ASII.JK", "name": "Astra", "sector": "Industrials",
         "undervaluation_score": 0.70, "last_close": 4900.0,
         "median_daily_value_rp": 900e6, "imputed_factors": ""},
    ])

    def names(deploy, label):
        regime = Regime([Signal("IHSG trend", "^JKSE", deploy > 0.5, "")],
                        deploy, label, "G", "")
        plan = assemble(settings_mock, scored_df, regime, [])
        return {c["ticker"] for c in plan["candidates"]}

    assert names(1.00, "RISK-ON") == names(0.30, "RISK-OFF")


def test_the_gate_only_ever_tightens_as_capital_grows(frame):
    counts = [capital_ladder(frame, _settings(b))["your_eligible"] for b in BANDS]
    assert counts == sorted(counts, reverse=True), counts
    assert counts[0] >= counts[-1]


def test_above_the_ceiling_nothing_is_eligible(frame):
    ladder = capital_ladder(frame, _settings(1_000_000))
    ceiling = ladder["ceiling_rp"]
    assert ceiling > 0
    # A rupiah under the ceiling the busiest name still fits; well above it, none do.
    assert capital_ladder(frame, _settings(ceiling * 0.99))["your_eligible"] >= 1
    assert capital_ladder(frame, _settings(ceiling * 1.01))["your_eligible"] == 0


def test_the_ceiling_is_a_fact_about_turnover_not_a_constant(frame):
    """Double what the market trades and the tool can absorb twice the money."""
    base = capital_ladder(frame, _settings(1_000_000))["ceiling_rp"]
    richer = frame.copy()
    richer["median_daily_value_rp"] *= 2
    assert capital_ladder(richer, _settings(1_000_000))["ceiling_rp"] == pytest.approx(base * 2)


# ================================================= defect 2: say so on the ticket
def test_an_outgrown_account_is_flagged_not_left_as_an_empty_list(frame):
    ladder = capital_ladder(frame, _settings(500_000_000_000))
    assert ladder["your_eligible"] == 0
    assert ladder["outgrown"] is True
    assert ladder["your_too_big"] > 0        # and it is size, not a dead market


def test_a_normal_account_is_not_flagged(frame):
    assert capital_ladder(frame, _settings(10_000_000))["outgrown"] is False


def test_an_empty_market_is_not_called_outgrown():
    """
    No names because nothing trades is a different fact from no names because you
    are too big, and telling the reader the wrong one would send them to fix the
    wrong thing.
    """
    dead = pd.DataFrame([{"ticker": "X.JK", "median_daily_value_rp": 0.0,
                          "last_close": 100.0}])
    ladder = capital_ladder(dead, _settings(10_000_000))
    assert ladder["your_eligible"] == 0
    assert ladder["outgrown"] is False


def test_the_ticket_explains_an_outgrown_account(frame):
    from market.regime import Regime
    from portfolio.fees import FeeConfig, estimate_fees
    from report.brief import render_brief

    out = render_brief(
        regime=Regime([], 0.3, "RISK-OFF", "R", ""), orders=[],
        fees=estimate_fees([], FeeConfig()), capital=500_000_000_000,
        holdings_rows=[], candidates=[], rejected={}, capped={}, allocation=None,
        universe_n=3, imputed_n=0,
        capital_ladder=capital_ladder(frame, _settings(500_000_000_000)),
        sector_exposure=sector_exposure(_settings(1)),
    )
    assert "outgrown this universe" in out
    assert "BIG.JK" in out                   # names the busiest, so it is checkable


def test_a_normal_account_gets_no_such_callout(frame):
    from market.regime import Regime
    from portfolio.fees import FeeConfig, estimate_fees
    from report.brief import render_brief

    out = render_brief(
        regime=Regime([], 1.0, "RISK-ON", "G", ""), orders=[],
        fees=estimate_fees([], FeeConfig()), capital=10_000_000,
        holdings_rows=[], candidates=[], rejected={}, capped={}, allocation=None,
        universe_n=3, imputed_n=0,
        capital_ladder=capital_ladder(frame, _settings(10_000_000)),
        sector_exposure=sector_exposure(_settings(1)),
    )
    assert "outgrown this universe" not in out


# ========================================================= the numbers are counted
def test_the_ladder_reads_the_frame_rather_than_a_written_table(frame):
    """Halve what the market trades and every count must fall or hold, never rise."""
    before = [b["eligible"] for b in capital_ladder(frame, _settings(1e7))["bands"]]
    thinner = frame.copy()
    thinner["median_daily_value_rp"] /= 10
    after = [b["eligible"] for b in capital_ladder(thinner, _settings(1e7))["bands"]]
    assert after != before
    assert all(a <= b for a, b in zip(after, before))


def test_the_commodity_share_is_counted_not_asserted():
    s = _settings(1e7)                        # 2 of 4 are Energy / Basic Materials
    assert sector_exposure(s)["commodity_pct"] == pytest.approx(50.0)

    s.sectors = {**s.sectors, "E.JK": "Healthcare", "F.JK": "Healthcare"}
    assert sector_exposure(s)["commodity_pct"] == pytest.approx(2 / 6 * 100)


def test_an_empty_frame_does_not_explode(frame):
    ladder = capital_ladder(pd.DataFrame(), _settings(1e7))
    assert ladder["universe_n"] == 0 and ladder["ceiling_rp"] == 0
    assert ladder["outgrown"] is False
    render_method(ladder, sector_exposure(_settings(1)), None)   # must not raise


# ================================================================== the page
def test_the_page_names_every_stage_the_backtest_is_missing():
    """
    The most consequential finding on the page. If somebody adds one of these to
    the simulation, this test is where they will be reminded to update the claim.
    """
    from market.regime import Regime
    out = render_method(capital_ladder(pd.DataFrame(), _settings(1e7)),
                        sector_exposure(_settings(1e7)), Regime([], 1.0, "", "", ""))
    for stage in ("Liquidity gate", "Decorrelation", "Score floor"):
        assert stage in out, stage
    assert "upper bound" in out


def test_the_page_states_what_it_reads_and_what_it_does_not():
    from market.regime import Regime, Signal
    regime = Regime([Signal("IHSG trend", "^JKSE", True, "above its 200-day average"),
                     Signal("Rupiah", "IDR=X", False, "weakening")],
                    0.6, "MIXED", "Y", "")
    out = render_method(capital_ladder(pd.DataFrame(), _settings(1e7)),
                        sector_exposure(_settings(1e7)), regime)
    assert "^JKSE" in out and "IDR=X" in out          # what it reads, live
    for blind in ("BI 7-day repo", "SGD", "coal", "Fed"):
        assert blind in out, blind                    # what it does not


def test_the_page_marks_the_band_you_are_standing_in(frame):
    from market.regime import Regime
    out = render_method(capital_ladder(frame, _settings(10_000_000)),
                        sector_exposure(_settings(1e7)), Regime([], 1.0, "", "", ""))
    assert 'class="you"' in out


def test_the_macro_layer_is_offered_as_a_design_not_a_feature():
    """It is not built, and the page must not imply that it is."""
    from market.regime import Regime
    out = render_method(capital_ladder(pd.DataFrame(), _settings(1e7)),
                        sector_exposure(_settings(1e7)), Regime([], 1.0, "", "", ""))
    assert "designed, not built" in out.lower()
    assert "None of this should be switched on before it is tested" in out


def test_the_page_scales_with_the_density_control():
    import re

    from report.method import METHOD_CSS
    assert re.findall(r"font-size:\s*[\d.]+px", METHOD_CSS) == []


# ============================================ ten weights, fewer real bets
def test_effective_factor_count_is_measured_not_written_down():
    """
    Ten weights look like ten pieces of evidence. On the live matrix they behave
    like 5.68, and the weighted composite carries 2.27x the variance it would if
    they were independent. Perturbing the matrix must move both.
    """
    from analysis.fundamental import effective_factors

    names = ["a", "b", "c"]
    independent = pd.DataFrame([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]],
                               index=names, columns=names)
    entangled = pd.DataFrame([[1.0, 0.95, 0.95], [0.95, 1.0, 0.95],
                              [0.95, 0.95, 1.0]], index=names, columns=names)
    weights = {"a": 1.0, "b": 1.0, "c": 1.0}

    clean = effective_factors(independent, weights)
    messy = effective_factors(entangled, weights)

    assert clean["effective"] == pytest.approx(3.0)
    assert messy["effective"] < 1.5
    assert clean["concentration"] == pytest.approx(1.0)
    assert messy["concentration"] > 2.5


def test_a_matrix_too_small_to_judge_says_nothing():
    from analysis.fundamental import effective_factors
    assert effective_factors(pd.DataFrame()) == {}
    assert effective_factors(pd.DataFrame([[1.0]], index=["a"], columns=["a"])) == {}


def test_the_limits_tab_states_the_effective_count():
    from market.regime import Regime
    out = render_method(capital_ladder(pd.DataFrame(), _settings(1e7)),
                        sector_exposure(_settings(1e7)), Regime([], 1.0, "", "", ""),
                        {"declared": 10, "effective": 5.68, "top_share": 0.32,
                         "concentration": 2.27})
    assert "5.7" in out and "2.27x" in out
    assert "overstates how diversified" in out


def test_without_the_measurement_the_page_simply_omits_it():
    from market.regime import Regime
    out = render_method(capital_ladder(pd.DataFrame(), _settings(1e7)),
                        sector_exposure(_settings(1e7)), Regime([], 1.0, "", "", ""))
    assert "fewer bets" not in out
