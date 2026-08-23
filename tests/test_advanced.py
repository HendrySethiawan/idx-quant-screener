"""
Tests for the Advanced half of the brief.

Two of these carry most of the weight:

  * test_simple_mode_is_complete_on_its_own -- Advanced must never become the
    place where the decision actually lives. If the ticket, the holdings or the
    regime ever migrate behind the toggle, the tool stops working for someone
    with fifteen minutes at lunch.

  * test_contribution_bars_sum_to_the_raw_score and
    test_whatif_grid_matches_a_direct_sizer_call -- Advanced restates numbers the
    engine computed elsewhere. A restatement that silently drifts is worse than
    no restatement, because it looks authoritative.
"""
import json
import re

import numpy as np
import pandas as pd
import pytest

from analysis.fundamental import FundamentalEngine
from market.regime import Regime, Signal
from portfolio.fees import FeeConfig, estimate_fees
from portfolio.sizing import choose_allocation
from report import advanced as A
from report.brief import render_brief


# --------------------------------------------------------------------- fixtures
@pytest.fixture
def universe():
    """A ten-name frame with the columns the advanced view reads."""
    rng = np.random.default_rng(7)
    rows = []
    for i in range(10):
        rows.append({
            "ticker": f"AA{i}.JK",
            "name": f"Company {i}",
            "sector": ["Financials", "Energy", "Industrials"][i % 3],
            "last_close": float(500 + i * 370),
            "median_daily_value_rp": float(1e9 * (i + 1)),
            "composite_score": float(10 - i),
            "raw_score": float(10 - i),
            "imputed_factors": "dividend_yield" if i in (2, 5) else "",
            **{f"z_{f}": float(rng.normal()) for f in A.FACTOR_ORDER},
        })
    return pd.DataFrame(rows)


@pytest.fixture
def candidates(universe):
    return [{"ticker": r["ticker"], "price": float(r["last_close"]),
             "score": float(r["composite_score"])}
            for _, r in universe.iterrows()]


def _brief(**kw):
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


# ------------------------------------------------------- the load-bearing rule
def test_simple_mode_is_complete_on_its_own(universe):
    """
    Everything needed to trade must sit OUTSIDE the .adv block, so it is visible
    in the default mode. Advanced is evidence, never the decision.
    """
    adv = A.render_advanced(df=universe, factor_weights={"mom_6m": 1.0})
    out = _brief(advanced_html=adv,
                 holdings_rows=[{"ticker": "TLKM.JK", "lots": 2, "value": 522_000,
                                 "unrealized_pct": 1.2, "flags": []}])

    assert '<div class="adv">' in out
    simple_half = out.split('<div class="adv">')[0]

    for essential in ("Do this today", "BBRI.JK", "3 lot", "Rp1,245,000",
                      "RISK-ON", "What you hold", "TLKM.JK", "Estimated cost"):
        assert essential in simple_half, f"{essential!r} is only reachable in Advanced"


def test_the_page_defaults_to_simple(universe):
    out = _brief(advanced_html=A.render_advanced(df=universe))
    assert '<body data-mode="simple">' in out
    # Assert the rule exists, not its exact text -- it is a grouped selector now
    # that also covers .steps, and matching the literal string made this fail on a
    # change that altered nothing about the behaviour.
    css = out.split("<style>")[1].split("</style>")[0]
    assert re.search(r'body\[data-mode="simple"\][^{}]*\.adv[^{}]*\{[^}]*display:none', css)


def test_printing_drops_the_research(universe):
    out = _brief(advanced_html=A.render_advanced(df=universe))
    print_block = out.split("@media print")[1][:200]
    assert ".adv" in print_block and "display:none" in print_block


def test_no_toggle_when_there_is_nothing_behind_it():
    """A switch that does nothing is worse than no switch."""
    out = _brief()
    assert 'nav class="modes"' not in out
    assert '<div class="adv">' not in out


def test_toggle_appears_once_advanced_exists(universe):
    out = _brief(advanced_html=A.render_advanced(df=universe))
    assert 'nav class="modes"' in out
    assert 'data-mode="advanced"' in out


# ------------------------------------------------------- restated numbers
def test_contribution_bars_sum_to_the_raw_score(sample_fundamental_data):
    """
    The "why did it score that" bars are weight x z. They must reconstruct
    `raw_score` exactly, or the chart is telling a different story than the
    ranker. Run the real engine rather than a fixture so the two cannot diverge.
    """
    weights = {"pe_ratio": -1.0, "price_to_book": -1.0, "dividend_yield": 1.0,
               "roe": 1.0, "gross_margin": 1.0, "debt_to_equity": -1.0}
    engine = FundamentalEngine({
        "fundamental_metrics": list(weights),
        "scoring_method": "zscore_normalized",
        "factor_weights": weights,
        "sanity_bounds": {"dividend_yield": 0.15, "price_to_book": 20.0, "roe": 3.0},
        "sector_neutral_factors": [],
        "min_sector_size": 2,
        "winsorize_k": 5.0,
    })
    scored = engine.compute_scores(engine.validate_fundamentals(sample_fundamental_data))

    for _, row in scored.iterrows():
        reconstructed = sum(
            w * float(row[f"z_{f}"])
            for f, w in weights.items()
            if f"z_{f}" in scored.columns and pd.notna(row[f"z_{f}"])
        )
        assert reconstructed == pytest.approx(float(row["raw_score"]), abs=1e-9), (
            f"{row['ticker']}: the bars would not add up to the score shown"
        )


def test_whatif_grid_matches_a_direct_sizer_call(candidates, settings_mock):
    """
    The grid is precomputed so the page needs no server. That is only legitimate
    while every cell equals what the real sizer would have returned.
    """
    grid = A.whatif_grid(candidates, settings_mock, 10_000_000, 0.75)
    assert grid["cells"], "grid should not be empty"

    for ci, cap in enumerate(grid["capitals"]):
        for n in grid["counts"]:
            for di, dep in enumerate(grid["deploys"]):
                cell = grid["cells"][f"{ci}|{n}|{di}"]
                direct = choose_allocation(
                    candidates, cap, dep, settings=settings_mock,
                    min_positions=n, max_positions=n,
                )
                assert cell["n"] == direct.n_positions
                assert cell["cash"] == round(direct.cash_left)
                assert [p["t"] for p in cell["pos"]] == direct.tickers()
                assert [p["l"] for p in cell["pos"]] == [p.lots for p in direct.positions]


def test_whatif_says_so_when_lot_sizes_bind(settings_mock):
    """
    Asking for 4 positions can return 3, because a name whose single lot costs
    more than its slot is dropped. Showing the smaller number silently would hide
    the exact constraint this tool exists to surface.
    """
    pricey = [{"ticker": "AAA.JK", "price": 1_000.0, "score": 3.0},
              {"ticker": "BBB.JK", "price": 1_000.0, "score": 2.0},
              {"ticker": "CCC.JK", "price": 90_000.0, "score": 1.0}]
    grid = A.whatif_grid(pricey, settings_mock, 3_000_000, 1.0)

    shortfalls = [c for c in grid["cells"].values() if c["n"] < c["req"]]
    assert shortfalls, "expected at least one setting where the lot gate bites"
    assert all(c["short"] for c in shortfalls), "a shortfall must carry its reason"
    assert any("asked for" in c["short"] for c in shortfalls)


def test_whatif_is_empty_without_capital(candidates, settings_mock):
    assert A.whatif_grid(candidates, settings_mock, 0, 1.0) == {}
    assert A.whatif_section({}, 0, 1.0) == ""


def test_whatif_payload_cannot_close_its_own_script_tag(settings_mock):
    """The grid is embedded in a <script type="application/json"> block."""
    evil = [{"ticker": "</script><img src=x onerror=alert(1)>", "price": 1000.0, "score": 1.0},
            {"ticker": "OK.JK", "price": 1000.0, "score": 0.5},
            {"ticker": "OK2.JK", "price": 1000.0, "score": 0.4}]
    html = A.whatif_section(A.whatif_grid(evil, settings_mock, 5_000_000, 1.0),
                            5_000_000, 1.0)
    payload = re.search(r'id="wi-data">(.*?)</script>', html, re.S).group(1)
    assert "<" not in payload
    json.loads(payload.replace("\\u003c", "<"))     # still valid JSON


def test_whatif_renderer_escapes_what_it_writes_back_out():
    """
    Safe JSON is only half of it: the script writes those values into innerHTML.
    Ticker names come from a config file, and "ours" is not "trusted".
    """
    from report.brief import _JS
    assert "esc(p.t)" in _JS, "ticker goes into innerHTML unescaped"
    assert "esc(cell.short)" in _JS, "shortfall text goes into innerHTML unescaped"


# ------------------------------------------------------------- the full screen
def test_universe_table_shows_every_name_not_just_the_affordable_ones(universe):
    """
    `assemble()` truncates candidates to top_picks_n for the ticket. The point of
    Advanced is the rest of the list, including what scored badly.
    """
    out = A.universe_table(universe)
    for ticker in universe["ticker"]:
        assert ticker in out
    body = out.split("<tbody>")[1]
    assert body.count("<tr>") == len(universe)


def test_universe_table_is_sortable_on_the_underlying_number(universe):
    """Sorting must use data-v, not the formatted "Rp1,234" string."""
    out = A.universe_table(universe)
    assert 'class="sortable-table"' in out
    assert 'data-col="0"' in out
    assert re.search(r'data-v="4150(\.0)?"|data-v="\d+\.?\d*"', out)


def test_universe_table_escapes_untrusted_names(universe):
    poisoned = universe.copy()
    poisoned.loc[0, "name"] = "<img src=x onerror=alert(1)>"
    poisoned.loc[0, "sector"] = "<b>evil</b>"
    out = A.universe_table(poisoned)
    assert "<img src=x" not in out
    assert "<b>evil</b>" not in out
    assert "&lt;img" in out


def test_score_breakdown_names_the_imputed_factors(universe):
    out = A.score_breakdown(universe, {"mom_6m": 1.0, "pe_ratio": -1.0},
                            ["AA2.JK"])
    assert "AA2.JK" in out
    assert "dividend_yield" in out, "a name scored on partial data must say so"


# ------------------------------------------------------------- graceful absence
@pytest.mark.parametrize("call", [
    lambda: A.correlation_section(None),
    lambda: A.correlation_section(pd.DataFrame()),
    lambda: A.regime_chart(None),
    lambda: A.regime_chart(pd.Series(dtype=float)),
    lambda: A.seasonality_section(None),
    lambda: A.equity_section(None),
    lambda: A.equity_section(pd.DataFrame()),
    lambda: A.sector_section(None, None, 2),
    lambda: A.data_quality_section(pd.DataFrame()),
])
def test_a_section_with_no_data_vanishes(call):
    """Sections disappear rather than rendering an empty shell."""
    assert call() == ""


def test_render_advanced_returns_nothing_when_it_has_nothing():
    assert A.render_advanced() == ""


def test_a_failing_section_does_not_take_the_brief_with_it(universe):
    """Advanced degrades to fewer sections; the ticket always renders."""
    adv = A.render_advanced(df=universe, correlations=None, benchmark=None,
                            seasonality_table=None, marks=None, whatif=None)
    out = _brief(advanced_html=adv)
    assert "Do this today" in out
    assert "BBRI.JK" in out


# --------------------------------------------------------------- no new deps
def test_advanced_needs_neither_matplotlib_nor_a_network(monkeypatch, universe, candidates,
                                                         settings_mock):
    """
    The whole argument for this design is that it adds no dependency and fetches
    nothing. Block the heavy imports and confirm a full render still happens.
    """
    import builtins
    real_import = builtins.__import__

    def guard(name, *args, **kwargs):
        if name.split(".")[0] in {"matplotlib", "seaborn", "yfinance", "requests",
                                  "urllib3", "http", "socket"}:
            raise ImportError(f"Advanced must not need {name}")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guard)

    out = A.render_advanced(
        df=universe,
        factor_weights={"mom_6m": 1.0, "pe_ratio": -1.0},
        breakdown_tickers=["AA0.JK"],
        correlations=universe[[f"z_{f}" for f in A.FACTOR_ORDER[:4]]].corr(),
        benchmark=pd.Series(np.linspace(6000, 7000, 300),
                            index=pd.date_range("2024-01-01", periods=300)),
        seasonality_table=pd.DataFrame({
            "month": range(1, 13),
            "month_name": ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                           "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"],
            "median_pct": np.linspace(-2, 2, 12),
            "hit_rate": np.linspace(0.3, 0.7, 12),
            "n": [37] * 12,
        }),
        current_month=8,
        whatif=A.whatif_grid(candidates, settings_mock, 10_000_000, 1.0),
        capital=10_000_000,
    )
    assert "<svg" in out and "wi-data" in out


# ------------------------------------------------------------------- honesty
def test_equity_chart_does_not_claim_to_be_the_shadow_benchmark():
    """
    journal_marks.csv stores an index LEVEL, so the only line available here is a
    rebase from the first snapshot. Calling that the cash-flow-matched shadow
    would overstate it -- deposits after the first mark break the comparison.
    """
    marks = pd.DataFrame({
        "date": pd.date_range("2026-01-01", periods=5, freq="7D").strftime("%Y-%m-%d"),
        "positions_value_rp": [8e6, 8.2e6, 8.1e6, 8.4e6, 8.6e6],
        "cash_rp": [2e6] * 5,
        "total_rp": [10e6, 10.2e6, 10.1e6, 10.4e6, 10.6e6],
        "ihsg_close": [7000, 7100, 7050, 7200, 7250],
    })
    out = A.equity_section(marks)
    assert "rebased" in out
    assert "cash-flow-matched" in out and "--journal" in out


def test_seasonality_greys_thin_months_rather_than_hiding_them():
    """An omitted month reads as "no effect", a stronger claim than "unknown"."""
    table = pd.DataFrame({
        "month": range(1, 13),
        "month_name": ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                       "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"],
        "median_pct": np.linspace(-2, 2, 12),
        "hit_rate": [0.5] * 12,
        "n": [3] * 6 + [37] * 6,
    })
    out = A.seasonality_section(table, current_month=1)
    assert "var(--muted)" in out
    assert "6 of 12 months" in out
    for name in ("Jan", "Dec"):
        assert name in out


def test_correlation_section_names_the_overlapping_pairs():
    """Telling the reader to eyeball a grid is not an answer."""
    frame = pd.DataFrame({"mom_6m": [1, 2, 3, 4, 5.0], "mom_12m": [1, 2, 3, 4, 5.1]})
    out = A.correlation_section(frame.corr())
    assert "Overlapping" in out
    assert "6m/12m" in out


def test_data_quality_counts_the_gaps(universe):
    out = A.data_quality_section(universe)
    assert "dividend_yield" in out
    assert "2 of 10 names" in out


# ------------------------------------------------------------------ valuation
@pytest.fixture
def valued(universe):
    """The universe frame put through the real valuation pass."""
    from analysis import valuation as V
    frame = universe.copy()
    frame["pe_ratio"] = [8.0, 12.0, 15.0, 40.0, 9.0, 11.0, 60.0, 7.0, 20.0, 13.0]
    frame["price_to_book"] = [0.8, 1.2, 1.5, 6.0, 0.9, 1.1, 9.0, 0.7, 2.0, 1.3]
    frame["unclipped_pe_ratio"] = frame["pe_ratio"]
    frame["unclipped_price_to_book"] = frame["price_to_book"]
    frame["roe"] = 0.12
    return V.value_universe(frame)


def test_valuation_section_shows_its_working(valued):
    """
    Simple gives the verdict; Advanced must give the arithmetic, so a reader can
    disagree with it rather than only believe it.
    """
    out = A.valuation_section(valued)
    for expected in ("What is it worth?", "Peers imply", "EPS", "Book/share",
                     "Peer P/E", "Peer P/B", "Peer group", "ROE"):
        assert expected in out


def test_valuation_section_explains_the_unclipped_input(valued):
    """
    The pre-winsorization detail is the whole correctness story of this feature.
    If it is not on the page, nobody will ever know the ranking and the valuation
    read different columns on purpose.
    """
    out = A.valuation_section(valued)
    assert "pre-winsorization" in out
    assert "50.738811" in out, "the concrete example is what makes the point land"


def test_valuation_section_vanishes_without_valuation_columns(universe):
    assert A.valuation_section(universe) == ""
    assert A.valuation_section(pd.DataFrame()) == ""


def test_valuation_section_lists_every_name(valued):
    out = A.valuation_section(valued)
    for ticker in valued["ticker"]:
        assert ticker in out


def test_advanced_includes_valuation_when_present(valued):
    out = A.render_advanced(df=valued)
    assert "What is it worth?" in out
