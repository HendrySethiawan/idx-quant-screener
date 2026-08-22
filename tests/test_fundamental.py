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
def test_dividend_yield_percent_scale_is_rescaled():
    engine = _engine()
    df = engine.validate_fundamentals([
        {"ticker": "A.JK", "name": "A", "dividend_yield": 14.17},
        {"ticker": "B.JK", "name": "B", "dividend_yield": 0.05},
    ])
    assert df.loc[0, "dividend_yield"] == pytest.approx(0.1417)
    assert df.loc[1, "dividend_yield"] == pytest.approx(0.05)
    assert df.loc[0, "data_quality_flag"]


def test_absurd_price_to_book_is_nullified_not_propagated():
    """PTRO really did come back at 179,615, flattening every other P/B z-score."""
    engine = _engine()
    records = [{"ticker": f"T{i}.JK", "name": f"T{i}", "price_to_book": v}
               for i, v in enumerate([1.2, 1.5, 2.0, 2.4, 3.1, 179615.38])]
    df = engine.validate_fundamentals(records)

    assert pd.isna(df.loc[5, "price_to_book"])
    assert "nullified" in df.loc[5, "data_quality_notes"]
    assert df.loc[:4, "price_to_book"].notna().all()


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
