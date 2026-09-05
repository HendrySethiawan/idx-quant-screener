"""
Regression tests for the scoring engine.

test_missing_factor_does_not_erase_row is the P0 guard: the original
`df["score"] += weight * z` index-aligned against a dropna()'d series and turned
whole rows to NaN. It silently removed 9 of 41 stocks from the real universe.
"""
import numpy as np
import pandas as pd
import pytest

from analysis.fundamental import FundamentalEngine


def _engine(**overrides):
    cfg = {
        "fundamental_metrics": ["pe_ratio", "price_to_book", "dividend_yield",
                                "roe", "gross_margin", "debt_to_equity"],
        "scoring_method": "zscore_normalized",
        "factor_weights": {
            "pe_ratio": -1.0, "price_to_book": -1.0, "dividend_yield": 1.0,
            "roe": 1.0, "gross_margin": 1.0, "debt_to_equity": -1.0,
        },
        "sanity_bounds": {"dividend_yield": 0.15, "price_to_book": 20.0, "roe": 3.0},
        "sector_neutral_factors": ["roe", "debt_to_equity"],
        "min_sector_size": 2,
        "winsorize_k": 5.0,
    }
    cfg.update(overrides)
    return FundamentalEngine(cfg)


# --------------------------------------------------------------------- the P0 bug
def test_missing_factor_does_not_erase_row(sample_fundamental_data):
    """TLKM lacks dividend_yield and ASII lacks roe -- both must still score."""
    engine = _engine()
    df = engine.validate_fundamentals(sample_fundamental_data)
    scored = engine.compute_scores(df)

    assert scored["undervaluation_score"].notna().all(), (
        "a missing factor erased a row -- the P0 NaN-alignment bug is back"
    )
    assert len(scored) == 4
    assert set(scored["ticker"]) == {"BBCA.JK", "BBRI.JK", "TLKM.JK", "ASII.JK"}


def test_imputed_factors_are_recorded(sample_fundamental_data):
    engine = _engine()
    scored = engine.compute_scores(engine.validate_fundamentals(sample_fundamental_data))
    by_ticker = scored.set_index("ticker")

    assert "dividend_yield" in by_ticker.loc["TLKM.JK", "imputed_factors"]
    assert "roe" in by_ticker.loc["ASII.JK", "imputed_factors"]
    assert by_ticker.loc["BBRI.JK", "imputed_factors"] == ""


def test_missing_factor_contributes_neutral_zero(sample_fundamental_data):
    engine = _engine()
    scored = engine.compute_scores(engine.validate_fundamentals(sample_fundamental_data))
    assert scored.set_index("ticker").loc["TLKM.JK", "z_dividend_yield"] == 0.0


# ------------------------------------------------------------------ data hygiene
# `dividend_yield` is yfinance's `trailingAnnualDividendYield` -- a fraction, and
# what actually reached shareholders. There used to be a heuristic here that
# divided by 100 whenever the value exceeded 1.0, and it was wrong in the one
# direction that mattered: a genuine yield below 1% arrives as 0.12 meaning 0.12%,
# skips the test, and is then read as 12%. BREN pays literally nothing and was
# scoring third-best in the universe on a factor weighted +1.0.

def test_a_tiny_yield_is_not_read_as_a_large_one():
    """BREN's shape: pays Rp0, reported as 0.12, must not become 12%."""
    engine = _engine()
    df = engine.validate_fundamentals([
        {"ticker": "BREN.JK", "name": "Barito Renewables", "dividend_yield": 0.0},
        {"ticker": "BBRI.JK", "name": "Bank Rakyat", "dividend_yield": 0.0613},
        {"ticker": "UNVR.JK", "name": "Unilever", "dividend_yield": 0.0667},
    ])
    assert df.loc[0, "dividend_yield"] == pytest.approx(0.0)
    assert df.loc[1, "dividend_yield"] == pytest.approx(0.0613)


def test_a_non_payer_scores_at_the_bottom_of_the_factor():
    """The consequence that matters: it must rank last, not third."""
    engine = _engine()
    scored = engine.compute_scores(engine.validate_fundamentals([
        {"ticker": "BREN.JK", "name": "BREN", "dividend_yield": 0.0},
        {"ticker": "A.JK", "name": "A", "dividend_yield": 0.04},
        {"ticker": "B.JK", "name": "B", "dividend_yield": 0.06},
        {"ticker": "C.JK", "name": "C", "dividend_yield": 0.08},
        {"ticker": "D.JK", "name": "D", "dividend_yield": 0.12},
    ]))
    z = scored.set_index("ticker")["z_dividend_yield"]
    assert z["BREN.JK"] == z.min()
    assert z["BREN.JK"] < 0


def test_the_forward_yield_is_rescaled_from_percent_and_kept_separately():
    engine = _engine()
    df = engine.validate_fundamentals([
        {"ticker": "BBRI.JK", "name": "Bank Rakyat",
         "dividend_yield": 0.0613, "dividend_yield_forward": 12.26},
    ])
    assert df.loc[0, "dividend_yield_forward"] == pytest.approx(0.1226)
    assert df.loc[0, "dividend_yield"] == pytest.approx(0.0613)


def test_a_forward_trailing_disagreement_is_recorded():
    """
    BBRI's two figures are 6.1 points apart and UNVR's 6.9. After a spinoff they
    diverge completely -- ADRO reported 8.63% forward in a year it paid nothing.
    The reader is told rather than left assuming the ranked number is the story.
    """
    engine = _engine()
    df = engine.validate_fundamentals([
        {"ticker": "BBRI.JK", "name": "A", "dividend_yield": 0.0613,
         "dividend_yield_forward": 12.26},
        {"ticker": "CALM.JK", "name": "B", "dividend_yield": 0.0400,
         "dividend_yield_forward": 4.10},
    ])
    assert "forward" in df.loc[0, "data_quality_notes"]
    assert "forward" not in str(df.loc[1, "data_quality_notes"])


def test_a_broken_multiple_is_still_nullified():
    """PTRO really did come back at 179,615, flattening every other P/B z-score."""
    engine = _engine()
    records = [{"ticker": f"T{i}.JK", "name": f"T{i}", "price_to_book": v}
               for i, v in enumerate([1.2, 1.5, 2.0, 2.4, 3.1, 179615.38])]
    df = engine.validate_fundamentals(records)

    assert pd.isna(df.loc[5, "price_to_book"])
    assert "nullified" in df.loc[5, "data_quality_notes"]
    assert df.loc[:4, "price_to_book"].notna().all()


def test_an_expensive_but_real_multiple_is_clipped_not_nullified():
    """
    A nullified factor scores NEUTRAL, so nullifying the most expensive name in the
    universe handed it a free pass on a factor weighted -1.0. BREN's real P/B is
    38.1 and MDKA's real P/E is 1,666.7 -- both extreme, neither broken.
    """
    engine = _engine()
    records = [{"ticker": f"T{i}.JK", "name": f"T{i}", "price_to_book": v}
               for i, v in enumerate([1.2, 1.5, 2.0, 2.4, 3.1, 38.1])]
    scored = engine.compute_scores(engine.validate_fundamentals(records))

    # Clipped to the bound, then winsorized to the MAD band like any other
    # outlier. The value it lands on is the band's business; what this pins is
    # that it kept a value at all and that the value ranks it last.
    assert pd.notna(scored.loc[5, "price_to_book"])
    assert "clipped_to_bound" in scored.loc[5, "data_quality_notes"]
    z = scored["z_price_to_book"]
    assert z[5] == z.max(), "the most expensive name must score worst, not average"
    assert z[5] > 0
    assert "price_to_book" not in str(scored.loc[5, "imputed_factors"])


def test_a_glitch_and_a_real_extreme_are_told_apart():
    """
    One threshold cannot do it. The real extremes on this universe reach 38.1;
    the currency glitch was 179,615, some nine thousand times the bound.
    """
    engine = _engine()
    df = engine.validate_fundamentals([
        {"ticker": "REAL.JK", "name": "real", "price_to_book": 38.1},
        {"ticker": "GLITCH.JK", "name": "glitch", "price_to_book": 179615.38},
    ])
    assert df.loc[0, "price_to_book"] == pytest.approx(20.0)
    assert pd.isna(df.loc[1, "price_to_book"])


def test_outlier_does_not_flatten_remaining_zscores():
    engine = _engine()
    records = [{"ticker": f"T{i}.JK", "name": f"T{i}", "price_to_book": v}
               for i, v in enumerate([1.2, 1.5, 2.0, 2.4, 3.1, 179615.38])]
    scored = engine.compute_scores(engine.validate_fundamentals(records))
    survivors = scored.loc[:4, "z_price_to_book"]
    assert survivors.std() > 0.5, "the glitch still compressed the surviving z-scores"


def test_nonpositive_pe_is_nulled_but_row_survives():
    """A loss-making company should rank badly, not disappear."""
    engine = _engine()
    df = engine.validate_fundamentals([
        {"ticker": "GOOD.JK", "name": "Good", "pe_ratio": 10.0},
        {"ticker": "LOSS.JK", "name": "Loss", "pe_ratio": -5.0},
        {"ticker": "NONE.JK", "name": "NoData", "pe_ratio": None},
    ])
    assert len(df) == 3
    assert pd.isna(df.loc[1, "pe_ratio"])
    assert "nonpositive" in df.loc[1, "data_quality_notes"]


def test_cheaper_stock_scores_higher():
    engine = _engine(factor_weights={"pe_ratio": -1.0})
    scored = engine.compute_scores(engine.validate_fundamentals([
        {"ticker": "CHEAP.JK", "name": "Cheap", "pe_ratio": 5.0},
        {"ticker": "MID.JK", "name": "Mid", "pe_ratio": 15.0},
        {"ticker": "RICH.JK", "name": "Rich", "pe_ratio": 30.0},
    ]))
    by_ticker = scored.set_index("ticker")["undervaluation_score"]
    assert by_ticker["CHEAP.JK"] > by_ticker["MID.JK"] > by_ticker["RICH.JK"]


def test_score_is_bounded_zero_to_one(sample_fundamental_data):
    engine = _engine()
    scored = engine.compute_scores(engine.validate_fundamentals(sample_fundamental_data))
    assert scored["undervaluation_score"].between(0, 1).all()


def test_degenerate_universe_scores_neutral():
    engine = _engine(factor_weights={"pe_ratio": -1.0})
    scored = engine.compute_scores(engine.validate_fundamentals([
        {"ticker": "A.JK", "name": "A", "pe_ratio": 10.0},
        {"ticker": "B.JK", "name": "B", "pe_ratio": 10.0},
    ]))
    assert (scored["undervaluation_score"] == 0.5).all()


# ------------------------------------------------------------- sector neutrality
def test_sector_neutral_z_judges_within_sector():
    """Banks carry structurally high leverage; they should be scored against banks."""
    engine = _engine(factor_weights={"debt_to_equity": -1.0}, min_sector_size=2)
    df = pd.DataFrame({
        "ticker": ["B1", "B2", "B3", "M1", "M2", "M3"],
        "name": list("abcdef"),
        "sector": ["Financials"] * 3 + ["Basic Materials"] * 3,
        "debt_to_equity": [400.0, 500.0, 600.0, 20.0, 30.0, 40.0],
    })
    scored = engine.compute_scores(df)
    by_ticker = scored.set_index("ticker")

    # Median bank and median miner both sit mid-pack within their own sector.
    assert by_ticker.loc["B2", "z_debt_to_equity"] == pytest.approx(0.0, abs=1e-9)
    assert by_ticker.loc["M2", "z_debt_to_equity"] == pytest.approx(0.0, abs=1e-9)


def test_small_sector_falls_back_to_global_z():
    engine = _engine(factor_weights={"roe": 1.0}, min_sector_size=5)
    df = pd.DataFrame({
        "ticker": ["A", "B", "C"],
        "name": list("abc"),
        "sector": ["Financials", "Financials", "Technology"],
        "roe": [0.10, 0.20, 0.30],
    })
    scored = engine.compute_scores(df)
    assert scored["z_roe"].std() > 0


def test_factor_correlations_shape(sample_fundamental_data):
    engine = _engine()
    df = engine.validate_fundamentals(sample_fundamental_data)
    corr = engine.compute_factor_correlations(df)
    assert not corr.empty
    assert list(corr.index) == list(corr.columns)
