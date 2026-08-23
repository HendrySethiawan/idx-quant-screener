"""
Tests for peer-multiple fair value.

The load-bearing one is test_valuation_never_reads_a_clipped_multiple. On a real
run six unrelated tickers came back with `pe_ratio == 50.738811` to six decimals,
because `_winsorize` clips every outlier onto the same bound. That is correct for
ranking and fatal for valuation: deriving earnings from a shared bound invents
them, and it does so precisely for the extreme names where "is this expensive?"
is the entire question.

The rest guard the honesty properties -- that a name is never silently dropped,
that a single estimate is not dressed up as a range, and that a wide disagreement
between the two measures is reported rather than averaged away.
"""
import numpy as np
import pandas as pd
import pytest

from analysis.fundamental import FundamentalEngine
from analysis import valuation as V


def _frame(rows):
    return pd.DataFrame(rows)


@pytest.fixture
def peers():
    """Six Financials (enough for a sector median) and two Tech (not enough)."""
    rows = []
    for i in range(6):
        rows.append({"ticker": f"BANK{i}.JK", "sector": "Financials",
                     "last_close": 1000.0, "pe_ratio": 10.0 + i,
                     "price_to_book": 1.0 + i * 0.1, "roe": 0.15})
    for i in range(2):
        rows.append({"ticker": f"TECH{i}.JK", "sector": "Technology",
                     "last_close": 5000.0, "pe_ratio": 40.0 + i,
                     "price_to_book": 8.0 + i, "roe": 0.05})
    df = _frame(rows)
    df["unclipped_pe_ratio"] = df["pe_ratio"]
    df["unclipped_price_to_book"] = df["price_to_book"]
    return df


# --------------------------------------------------------- the regression guard
def test_valuation_never_reads_a_clipped_multiple():
    """
    Run the real validator on a frame with one extreme P/E, so winsorization
    actually fires, then prove the fair price came from the true multiple.
    """
    engine = FundamentalEngine({
        "fundamental_metrics": ["pe_ratio", "price_to_book"],
        "factor_weights": {"pe_ratio": -1.0, "price_to_book": -1.0},
        "sanity_bounds": {"pe_ratio": 5000.0, "price_to_book": 500.0},
        "sector_neutral_factors": [],
        "min_sector_size": 2,
        "winsorize_k": 3.0,
    })
    records = [{"ticker": f"N{i}.JK", "name": f"N{i}", "pe_ratio": 10.0 + i,
                "price_to_book": 1.0} for i in range(8)]
    records.append({"ticker": "WILD.JK", "name": "Wild", "pe_ratio": 900.0,
                    "price_to_book": 1.0})
    validated = engine.validate_fundamentals(_frame(records))

    wild = validated[validated["ticker"] == "WILD.JK"].iloc[0]
    assert "pe_ratio:winsorized" in wild["data_quality_notes"], "winsorization did not fire"
    assert wild["pe_ratio"] < 900.0, "the clipped column should have been reduced"
    assert wild["unclipped_pe_ratio"] == 900.0, "the true multiple must survive"

    # EPS derived from the clip bound would be several times too large.
    row = dict(wild)
    row["last_close"] = 9000.0
    v = V.value_one(row, peer_pe=10.0, peer_pb=1.0)
    assert v.eps == pytest.approx(9000.0 / 900.0), "valuation used the clipped P/E"
    assert v.fair_pe == pytest.approx(100.0)


def test_the_clipped_column_is_still_what_ranking_sees():
    """The winsorization fix must not be undone -- ranking still wants the clip."""
    engine = FundamentalEngine({
        "fundamental_metrics": ["pe_ratio"],
        "factor_weights": {"pe_ratio": -1.0},
        "sanity_bounds": {"pe_ratio": 5000.0},
        "sector_neutral_factors": [], "min_sector_size": 2, "winsorize_k": 3.0,
    })
    records = [{"ticker": f"N{i}.JK", "name": "n", "pe_ratio": 10.0 + i} for i in range(8)]
    records.append({"ticker": "WILD.JK", "name": "w", "pe_ratio": 900.0})
    out = engine.validate_fundamentals(_frame(records))
    assert out["pe_ratio"].max() < 900.0
    assert out["unclipped_pe_ratio"].max() == 900.0


# ------------------------------------------------------------------ peer groups
def test_a_big_enough_sector_uses_its_own_median(peers):
    med, grp = V.peer_medians(peers, "pe_ratio", min_peers=4)
    bank = peers.index[peers["ticker"] == "BANK0.JK"][0]
    assert grp[bank] == "Financials"
    assert med[bank] == pytest.approx(np.median([10, 11, 12, 13, 14, 15]))


def test_a_two_name_sector_falls_back_to_the_universe_and_says_so(peers):
    """A median of two names is noise. Four of nine real sectors have two names."""
    med, grp = V.peer_medians(peers, "pe_ratio", min_peers=4)
    tech = peers.index[peers["ticker"] == "TECH0.JK"][0]
    assert grp[tech] == "universe"
    assert med[tech] == pytest.approx(peers["pe_ratio"].median())


def test_peer_medians_ignore_nonpositive_multiples():
    df = _frame([{"ticker": "A.JK", "sector": "X", "pe_ratio": 10.0},
                 {"ticker": "B.JK", "sector": "X", "pe_ratio": -5.0},
                 {"ticker": "C.JK", "sector": "X", "pe_ratio": 20.0}])
    med, _ = V.peer_medians(df, "pe_ratio", min_peers=99)   # force universe
    assert med.iloc[0] == pytest.approx(15.0), "a negative P/E polluted the median"


# --------------------------------------------------------------- the fair zone
def test_the_zone_brackets_both_estimates_and_the_verdict_follows():
    row = {"ticker": "X.JK", "last_close": 1000.0,
           "unclipped_pe_ratio": 10.0, "unclipped_price_to_book": 2.0}
    v = V.value_one(row, peer_pe=20.0, peer_pb=4.0)     # both imply 2000
    assert v.fair_pe == pytest.approx(2000.0)
    assert v.fair_pb == pytest.approx(2000.0)
    assert v.zone_lo == v.zone_hi == pytest.approx(2000.0)
    assert v.verdict == V.UNDERVALUED


@pytest.mark.parametrize("price,expect", [
    (500.0, V.UNDERVALUED),      # below both
    (1500.0, V.FAIR),            # between them
    (1000.0, V.FAIR),            # exactly on the lower edge
    (2000.0, V.FAIR),            # exactly on the upper edge
    (5000.0, V.OVERVALUED),      # above both
])
def test_verdict_matches_where_the_price_sits(price, expect):
    row = {"ticker": "X.JK", "last_close": price,
           "unclipped_pe_ratio": price / 100.0,       # EPS = 100
           "unclipped_price_to_book": price / 200.0}  # BVPS = 200
    v = V.value_one(row, peer_pe=10.0, peer_pb=10.0)  # -> 1000 and 2000
    assert (v.zone_lo, v.zone_hi) == pytest.approx((1000.0, 2000.0))
    assert v.verdict == expect


def test_gap_is_zero_inside_the_zone_and_signed_outside():
    def gap(price):
        row = {"ticker": "X.JK", "last_close": price,
               "unclipped_pe_ratio": price / 100.0,
               "unclipped_price_to_book": price / 200.0}
        return V.value_one(row, peer_pe=10.0, peer_pb=10.0).gap_pct

    assert gap(1500.0) == 0.0
    assert gap(500.0) < 0
    assert gap(4000.0) > 0


# ------------------------------------------------------------- the four states
def test_one_usable_measure_is_not_dressed_up_as_a_range():
    """A point is not a zone. Reporting zero width would imply false certainty."""
    row = {"ticker": "X.JK", "last_close": 1000.0,
           "unclipped_pe_ratio": None, "unclipped_price_to_book": 2.0}
    v = V.value_one(row, peer_pe=20.0, peer_pb=4.0)
    assert v.verdict == V.ONE_MEASURE
    assert v.n_methods == 1
    assert "book value only" in v.notes[0]
    assert v.verdict not in (V.UNDERVALUED, V.FAIR, V.OVERVALUED)


def test_no_usable_measure_says_why_rather_than_vanishing():
    row = {"ticker": "X.JK", "last_close": 1000.0,
           "unclipped_pe_ratio": None, "unclipped_price_to_book": None}
    v = V.value_one(row, peer_pe=20.0, peer_pb=4.0)
    assert v.verdict == V.UNKNOWN
    assert "no usable P/E" in v.notes[0] and "no usable P/B" in v.notes[0]


def test_a_wide_disagreement_is_reported_not_averaged():
    """Median disagreement on real data is 38% and the max is 177%."""
    row = {"ticker": "X.JK", "last_close": 1000.0,
           "unclipped_pe_ratio": 10.0,        # EPS 100  -> 1000
           "unclipped_price_to_book": 0.5}    # BVPS 2000 -> 6000
    v = V.value_one(row, peer_pe=10.0, peer_pb=3.0, wide_band_pct=0.60)
    assert v.disagreement_pct > 1.0
    assert v.wide
    assert "disagree" in "; ".join(v.notes)


def test_a_tight_agreement_carries_no_warning():
    row = {"ticker": "X.JK", "last_close": 1000.0,
           "unclipped_pe_ratio": 10.0, "unclipped_price_to_book": 2.0}
    v = V.value_one(row, peer_pe=20.0, peer_pb=4.1, wide_band_pct=0.60)
    assert not v.wide


def test_a_name_with_no_price_cannot_be_valued():
    v = V.value_one({"ticker": "X.JK", "last_close": None}, 10.0, 1.0)
    assert v.verdict == V.UNKNOWN and "no price" in v.notes


# ------------------------------------------------------------------- universe
def test_value_universe_never_drops_a_row(peers):
    out = V.value_universe(peers)
    assert len(out) == len(peers)
    assert set(out["ticker"]) == set(peers["ticker"])
    assert out["value_verdict"].notna().all()


def test_value_universe_survives_an_empty_frame():
    out = V.value_universe(pd.DataFrame())
    assert out.empty
    assert "value_verdict" in out.columns


def test_coverage_adds_up(peers):
    out = V.value_universe(peers)
    c = V.coverage(out)
    assert c["total"] == len(peers)
    assert c["valued"] + c["one_measure"] + c["unknown"] == c["total"]


def test_describe_is_plain_language():
    row = {"ticker": "X.JK", "last_close": 500.0,
           "unclipped_pe_ratio": 5.0, "unclipped_price_to_book": 1.0}
    text = V.describe(V.value_one(row, peer_pe=10.0, peer_pb=2.0))
    assert "Rp" in text and "below" in text
    for jargon in ("z-score", "sigma", "winsor", "quantile"):
        assert jargon not in text.lower()


def test_valuation_needs_no_network_or_matplotlib(monkeypatch, peers):
    import builtins
    real = builtins.__import__

    def guard(name, *a, **k):
        if name.split(".")[0] in {"matplotlib", "seaborn", "yfinance", "requests"}:
            raise ImportError(name)
        return real(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", guard)
    assert not V.value_universe(peers).empty


# ------------------------------------------------- the bank gross-margin fix
def test_a_banks_unreported_gross_margin_scores_neutral():
    """
    yfinance returns a literal 0.0 for banks. Read as an observation it made every
    bank worst-in-class on a factor that does not apply to them.
    """
    engine = FundamentalEngine({
        "fundamental_metrics": ["gross_margin", "roe"],
        "factor_weights": {"gross_margin": 1.0, "roe": 1.0},
        "sanity_bounds": {}, "sector_neutral_factors": [],
        "min_sector_size": 2, "winsorize_k": 5.0,
    })
    records = [{"ticker": f"BANK{i}.JK", "name": "b", "gross_margin": 0.0, "roe": 0.15}
               for i in range(4)]
    records += [{"ticker": f"CO{i}.JK", "name": "c", "gross_margin": 0.4 + i * 0.1,
                 "roe": 0.15} for i in range(4)]

    validated = engine.validate_fundamentals(_frame(records))
    bank = validated[validated["ticker"] == "BANK0.JK"].iloc[0]
    assert pd.isna(bank["gross_margin"])
    assert "gross_margin:not_reported" in bank["data_quality_notes"]

    scored = engine.compute_scores(validated)
    b = scored[scored["ticker"] == "BANK0.JK"].iloc[0]
    assert b["z_gross_margin"] == 0.0, "an unreported margin must score neutral"
    assert "gross_margin" in b["imputed_factors"]


def test_a_genuine_negative_gross_margin_survives():
    """Selling below cost is real. Only the exact-zero 'not reported' case is voided."""
    engine = FundamentalEngine({
        "fundamental_metrics": ["gross_margin"],
        "factor_weights": {"gross_margin": 1.0},
        "sanity_bounds": {}, "sector_neutral_factors": [],
        "min_sector_size": 2, "winsorize_k": 5.0,
    })
    records = [{"ticker": "LOSS.JK", "name": "l", "gross_margin": -0.25}]
    records += [{"ticker": f"CO{i}.JK", "name": "c", "gross_margin": 0.3 + i * 0.05}
                for i in range(5)]
    out = engine.validate_fundamentals(_frame(records))
    loss = out[out["ticker"] == "LOSS.JK"].iloc[0]
    # It survives as a negative observation. Winsorization may still pull it toward
    # the pack -- that is a separate mechanism and not what this guards.
    assert pd.notna(loss["gross_margin"])
    assert loss["gross_margin"] < 0
    assert "not_reported" not in str(loss["data_quality_notes"])
