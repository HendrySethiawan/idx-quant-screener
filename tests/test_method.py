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


# ================================================ what selling costs at your size
# Every other cost in this project scales with the trade. The stamp does not: it is
# a flat charge per selling day, so its weight is decided by the size of the account
# and nothing else. The tool has always refused a position too small to carry it and
# has never once said what it adds up to over a year -- which is the number that
# decides whether trading weekly is affordable at this capital at all.

def _cost(capital, days=None):
    from report.method import trading_cost
    return trading_cost(capital_ladder(pd.DataFrame(), _settings(capital)), days)


def test_the_stamp_is_a_bigger_share_of_a_smaller_account():
    """The whole point. Same cadence, same charge, hundredfold difference in weight."""
    small = _cost(10_000_000, 52)["measured"]["pct"]
    large = _cost(1_000_000_000, 52)["measured"]["pct"]
    assert small == pytest.approx(large * 100, rel=1e-6)
    assert small > 5.0 > large


def test_the_arithmetic_is_the_stamp_over_the_capital():
    """Checked by hand, not against the function: 10,000 x 52 / 10,000,000 = 5.2%."""
    c = _cost(10_000_000, 52)
    assert c["per_day_pct"] == pytest.approx(0.1)
    assert c["measured"]["pct"] == pytest.approx(5.2)
    assert c["measured"]["rp"] == pytest.approx(520_000)


def test_no_figure_on_the_page_is_a_literal():
    """
    Render at two capitals and require every percentage to move. A hard-coded one
    survives both renders and fails here -- which is how this project keeps finding
    numbers that were true once.
    """
    import re

    from market.regime import Regime
    from report.method import render_method

    def page(capital):
        s = _settings(capital)
        return render_method(capital_ladder(pd.DataFrame(), s), sector_exposure(s),
                             Regime([], 1.0, "", "", ""), None, 52)

    def grab(h):
        # Bounded to the card itself: the rest of the page carries percentages of
        # its own, and matching those would pass this test for the wrong reason.
        card = h.split("What selling costs")[1].split("the charge does not")[0]
        return set(re.findall(r"\d+\.\d+%", card))

    a, b = page(10_000_000), page(500_000_000)
    assert grab(a) and not (grab(a) & grab(b)), "a percentage survived a capital change"


def test_the_measured_row_needs_a_measurement():
    """Cadence rates are arithmetic and always shown; the measured row is evidence."""
    from report.method import cost_card

    assert _cost(10_000_000)["measured"] is None
    plain = cost_card(_cost(10_000_000))
    assert "What selling costs" in plain and "measured" not in plain
    assert "measured" in cost_card(_cost(10_000_000, 47.6))


def test_an_account_with_no_capital_gets_no_card_rather_than_a_division():
    from report.method import cost_card
    assert cost_card(_cost(0)) == ""


@pytest.mark.parametrize("verdict,why", [
    (None, "no backtest has been run"),
    ({"exits": {"sell_days": 238}}, "no window length to divide by"),
    ({"gross": {"years": 5.0}}, "no selling days recorded"),
    ({"exits": {"sell_days": 0}, "gross": {"years": 5.0}}, "never sold"),
])
def test_a_measurement_that_cannot_be_established_is_not_invented(verdict, why):
    """When uncertain, show less -- the same rule the evidence note follows."""
    from report.brief import _measured_sell_days
    assert _measured_sell_days(verdict) is None, why


def test_a_verdict_from_other_settings_is_refused_not_quoted():
    """
    The guard for the error that produced three wrong figures in one afternoon.
    A backtest run under a different book size describes a different cadence.
    """
    from report.brief import _measured_sell_days

    s = _settings(10_000_000)
    stored = {"exits": {"sell_days": 238}, "gross": {"years": 5.0},
              "config": {"max_positions": 6, "min_positions": 3, "max_per_sector": 2}}
    assert _measured_sell_days(stored, s) == pytest.approx(47.6)

    stored["config"]["max_positions"] = 3
    assert _measured_sell_days(stored, s) is None


# ============================================ the one foreign instrument that leads
# EIDO really does lead the IHSG, and the lead survives controlling for the index's
# own move and the rupiah. What kills it is the clock: Jakarta closes hours before
# New York opens, so the information is priced into the opening print. The page has
# to carry that distinction, because the headline correlation alone argues the
# opposite of the truth.

def _row(label="EIDO", ticker="EIDO", **over):
    base = {"label": label, "ticker": ticker, "n": 1160, "same_day": 0.73,
            "next_day": 0.15, "gap": 0.28, "intraday": 0.01,
            "intraday_first": 0.03, "intraday_second": 0.00, "capturable": False,
            "verdict": "not capturable - the lead is in the opening print"}
    base.update(over)
    return base


def _lead(rows=None, **over):
    base = {"rows": rows if rows is not None else [_row()], "target": "^JKSE",
            "n_tested": 13, "independence": {"effective": 4.2, "top_share": 0.38}}
    base.update(over)
    return base


def test_the_table_separates_the_gap_from_what_you_could_trade():
    """
    The whole point of the card. Showing only the next-day number would argue for
    exactly the action the measurement rules out.
    """
    from report.method import lead_card

    out = lead_card(_lead())
    assert "EIDO" in out and "+0.15" in out
    assert "+0.28" in out and "+0.01" in out
    assert "unreachable" in out and "tradeable" in out
    assert "None of the 13 survived" in out


def test_the_same_day_column_is_labelled_as_exposure():
    """
    The commodity rows answer a different question from the leadership ones, and it
    is the same number read two ways. The header has to say which is which.
    """
    from report.method import lead_card
    out = lead_card(_lead())
    assert "exposure" in out and "tradeable" in out


def test_a_capturable_row_is_never_buried():
    """A positive must not sit below a screenful of negatives."""
    from report.method import lead_card

    out = lead_card(_lead([
        _row("Hang Seng", "^HSI", same_day=0.18, capturable=False),
        _row("Dollar index", "DX-Y.NYB", intraday=-0.134, capturable=True),
    ]))
    assert out.index("Dollar index") < out.index("Hang Seng")
    assert "survives the open" in out


def test_a_survivor_is_reported_as_a_finding_not_an_instruction():
    from report.method import lead_card

    out = lead_card(_lead([_row("Dollar index", "DX-Y.NYB", intraday=-0.134,
                                intraday_first=-0.157, intraday_second=-0.139,
                                capturable=True)]))
    assert "1 of 13 survived" in out
    assert "not an instruction" in out
    assert "not a strategy until a backtest says so" in out
    assert "deciding daily rather than weekly" in out


def test_the_false_positive_warning_is_computed_from_what_was_tested():
    """Thirteen tests at 95% is not the same warning as three."""
    from report.method import lead_card

    hit = [_row("Dollar index", "DX-Y.NYB", capturable=True)]
    many = lead_card(_lead(hit, n_tested=13))
    few = lead_card(_lead(hit, n_tested=3))
    assert "0.7" in many and "0.1" in few


def test_the_table_says_the_candidates_are_not_independent_looks():
    """
    Thirteen rows read as thirteen separate confirmations unless something says
    otherwise, and the whole point of the Asian indices was that they are not.
    """
    from report.method import lead_card

    out = lead_card(_lead())
    assert "4.2 separate bets" in out
    assert "38%" in out
    assert "common factor, not several confirmations" in out


def test_one_candidate_claims_no_independence_count():
    from report.method import lead_card
    assert "separate bets" not in lead_card(_lead(n_tested=1, independence={}))


def test_a_verdict_written_before_the_catalogue_still_renders():
    """An older `backtest_verdict.json` carries one instrument inline, not a list."""
    from report.brief import _measured_lead

    old = {"lead": _row(), "config": {"max_positions": 6, "min_positions": 3,
                                      "max_per_sector": 2}}
    out = _measured_lead(old, _settings(10_000_000))
    assert out is not None and len(out["rows"]) == 1
    assert out["rows"][0]["ticker"] == "EIDO"


def test_one_series_failing_to_fetch_does_not_lose_the_others():
    """
    A rolled futures contract or a delisted symbol drops its own row and nothing
    else. `MTF=F` did exactly this -- it stopped printing in December and would
    otherwise have taken the whole table with it.
    """
    from report.brief import _measured_lead

    payload = {"lead": {"rows": [_row("Brent", "BZ=F"),
                                 {"label": "Coal", "ticker": "MTF=F",
                                  "intraday": None},
                                 _row("Gold", "GC=F")],
                        "n_tested": 3, "target": "^JKSE"},
               "config": {"max_positions": 6, "min_positions": 3,
                          "max_per_sector": 2}}
    out = _measured_lead(payload, _settings(10_000_000))
    assert [r["ticker"] for r in out["rows"]] == ["BZ=F", "GC=F"]
    assert out["n_tested"] == 3, "the count of what was attempted is not rewritten"


@pytest.mark.parametrize("lead", [None, {}, {"rows": []},
                                  {"rows": [{"intraday": None}]}])
def test_an_unmeasured_lead_renders_nothing_rather_than_zero(lead):
    """Absent and zero are different claims, and only one of them is true here."""
    from report.method import lead_card
    assert lead_card(lead) == ""


def test_no_correlation_in_the_table_is_a_literal():
    """Render two measurements and require every figure to move."""
    import re

    from report.method import lead_card

    a = lead_card(_lead())
    b = lead_card(_lead([_row(same_day=0.41, next_day=0.09, gap=0.12, intraday=0.05,
                              intraday_first=0.07, intraday_second=0.02, n=900)]))
    nums = lambda h: set(re.findall(r"[+-]\d\.\d\d", h))
    assert nums(a) and not (nums(a) & nums(b))


def test_the_regime_table_still_reads_two_signals_not_three():
    """
    The measurement must not have crept into the decision. This is the assertion
    that would fail if EIDO were ever quietly added to the ladder.
    """
    from market.regime import Regime, Signal
    from report.method import moves_section

    regime = Regime([Signal("IHSG trend", "^JKSE", True, "above"),
                     Signal("Rupiah", "IDR=X", False, "weakening")],
                    0.6, "", "", "")
    out = moves_section(regime, sector_exposure(_settings(1e7)), _lead())
    assert "It reads exactly two things" in out
    # Scoped to the regime card. The lead table below it names many series on
    # purpose; the point is that none of them reached the ladder.
    ladder = out.split("What it does not read")[0]
    assert ladder.count("<code>") == 2, "a third series reached the regime table"
    assert "Measured, not argued" in out, "the lead table should still be present"


def test_a_lead_measured_under_other_settings_is_refused():
    from report.brief import _measured_lead

    s = _settings(10_000_000)
    stored = {"lead": _lead(),
              "config": {"max_positions": 6, "min_positions": 3, "max_per_sector": 2}}
    assert _measured_lead(stored, s) is not None

    stored["config"]["max_positions"] = 3
    assert _measured_lead(stored, s) is None


@pytest.mark.parametrize("verdict", [None, {}, {"lead": None}, {"lead": "EIDO"},
                                     {"lead": {"intraday": None}}])
def test_a_lead_that_cannot_be_established_is_not_invented(verdict):
    from report.brief import _measured_lead
    assert _measured_lead(verdict) is None


def test_the_diagnostics_writer_actually_runs(tmp_path, settings_mock):
    """
    This path had no coverage, and an `AttributeError` in it survived a green
    suite -- it only fires in the real pipeline, where it would have taken the
    whole run down. Calling it for real is the entire point of the test.
    """
    from analysis.fundamental import FundamentalEngine

    frame = pd.DataFrame([
        {"ticker": "A.JK", "pe_ratio": 10.0, "price_to_book": 1.0, "roe": 0.10},
        {"ticker": "B.JK", "pe_ratio": 20.0, "price_to_book": 2.0, "roe": 0.20},
        {"ticker": "C.JK", "pe_ratio": 15.0, "price_to_book": 1.5, "roe": 0.05},
    ])
    FundamentalEngine(settings_mock).save_factor_diagnostics(frame, tmp_path)
    assert (tmp_path / "factor_correlations.csv").exists()
