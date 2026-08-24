import pandas as pd

from analysis.selection import (average_correlation, decorrelated_pick,
                                sector_capped_pick)

RANKED = ["BBRI.JK", "BMRI.JK", "BBNI.JK", "BBCA.JK", "TLKM.JK", "ASII.JK"]
SECTORS = {
    "BBRI.JK": "Financials", "BMRI.JK": "Financials", "BBNI.JK": "Financials",
    "BBCA.JK": "Financials", "TLKM.JK": "Infrastructure", "ASII.JK": "Industrials",
}


def test_no_cap_returns_plain_top_n():
    assert sector_capped_pick(RANKED, SECTORS, top_n=3, max_per_sector=0) == RANKED[:3]


def test_cap_is_enforced():
    picked = sector_capped_pick(RANKED, SECTORS, top_n=4, max_per_sector=2)
    assert sum(SECTORS[t] == "Financials" for t in picked) == 2
    assert picked == ["BBRI.JK", "BMRI.JK", "TLKM.JK", "ASII.JK"]


def test_backfill_when_cap_starves_the_book():
    """Four slots, but only two sectors available at a cap of 1."""
    sectors = {"A": "X", "B": "X", "C": "X", "D": "Y"}
    picked = sector_capped_pick(["A", "B", "C", "D"], sectors, top_n=4, max_per_sector=1)
    assert len(picked) == 4
    assert set(picked) == {"A", "B", "C", "D"}


def test_unmapped_ticker_buckets_as_unknown():
    picked = sector_capped_pick(["A", "B", "C"], {"A": "X"}, top_n=3, max_per_sector=1)
    assert len(picked) == 3


def test_missing_sector_map_falls_back_to_top_n():
    assert sector_capped_pick(RANKED, None, top_n=2, max_per_sector=2) == RANKED[:2]


# ------------------------------------------------- capping the bet, not the label
# `max_per_sector` counts labels, which cuts both ways. BRPT and PTRO correlate 0.87
# in different sectors, so the cap never sees them; tin, palm oil and coal correlate
# about 0.30, so treating them as one bet because they are "all commodities" would be
# wrong. Only measurement separates those two cases.
def _corr(pairs, tickers):
    """A correlation matrix from {(a, b): value}, 1.0 on the diagonal."""
    m = pd.DataFrame(0.0, index=list(tickers), columns=list(tickers))
    for t in tickers:
        m.at[t, t] = 1.0
    for (a, b), v in pairs.items():
        m.at[a, b] = m.at[b, a] = v
    return m


def test_a_name_that_moves_with_one_already_picked_is_skipped():
    tickers = ["TINS.JK", "ADRO.JK", "TLKM.JK"]
    m = _corr({("TINS.JK", "ADRO.JK"): 0.85,
               ("TINS.JK", "TLKM.JK"): 0.10,
               ("ADRO.JK", "TLKM.JK"): 0.12}, tickers)

    skipped = {}
    picked = decorrelated_pick(tickers, m, top_n=2, max_correlation=0.70,
                               skipped=skipped)

    assert picked == ["TINS.JK", "TLKM.JK"]
    assert "ADRO.JK" in skipped
    assert "0.85" in skipped["ADRO.JK"]
    assert "TINS.JK" in skipped["ADRO.JK"]


def test_an_uncorrelated_book_is_left_alone():
    tickers = ["BBRI.JK", "TLKM.JK", "MMIX.JK"]
    m = _corr({("BBRI.JK", "TLKM.JK"): 0.2, ("BBRI.JK", "MMIX.JK"): 0.1,
               ("TLKM.JK", "MMIX.JK"): 0.15}, tickers)

    skipped = {}
    assert decorrelated_pick(tickers, m, top_n=3, skipped=skipped) == tickers
    assert skipped == {}


def test_the_ticket_is_never_left_short():
    """
    If everything correlates, backfill rather than hand back two names. At Rp10
    juta the book is three positions; refusing to fill it leaves nothing to act on.
    """
    tickers = ["A.JK", "B.JK", "C.JK"]
    m = _corr({("A.JK", "B.JK"): 0.95, ("A.JK", "C.JK"): 0.93,
               ("B.JK", "C.JK"): 0.97}, tickers)

    skipped = {}
    picked = decorrelated_pick(tickers, m, top_n=3, max_correlation=0.70,
                               skipped=skipped)

    assert picked == tickers
    # Backfilled means not actually excluded, so no false explanation is left behind.
    assert skipped == {}


def test_a_pair_with_no_measurement_is_allowed_through():
    """
    Absence of evidence is not evidence of correlation. Blocking on it would
    quietly shrink the universe to whatever has a long price history.
    """
    tickers = ["A.JK", "B.JK"]
    m = _corr({}, tickers)
    m.at["A.JK", "B.JK"] = m.at["B.JK", "A.JK"] = float("nan")

    assert decorrelated_pick(tickers, m, top_n=2) == tickers


def test_a_name_missing_from_the_matrix_is_allowed_through():
    m = _corr({("A.JK", "B.JK"): 0.1}, ["A.JK", "B.JK"])
    assert decorrelated_pick(["A.JK", "B.JK", "NEW.JK"], m, top_n=3) == [
        "A.JK", "B.JK", "NEW.JK"]


def test_no_matrix_means_the_ranking_passes_straight_through():
    assert decorrelated_pick(["A.JK", "B.JK"], None, top_n=2) == ["A.JK", "B.JK"]


def test_a_threshold_of_one_disables_the_gate():
    tickers = ["A.JK", "B.JK"]
    m = _corr({("A.JK", "B.JK"): 0.99}, tickers)
    assert decorrelated_pick(tickers, m, top_n=2, max_correlation=1.0) == tickers


def test_the_gate_respects_the_ranking_order():
    """The best name is never the one skipped; it is already held when the gate runs."""
    tickers = ["BEST.JK", "SECOND.JK", "THIRD.JK"]
    m = _corr({("BEST.JK", "SECOND.JK"): 0.9, ("BEST.JK", "THIRD.JK"): 0.1,
               ("SECOND.JK", "THIRD.JK"): 0.1}, tickers)

    picked = decorrelated_pick(tickers, m, top_n=2, max_correlation=0.70)
    assert picked[0] == "BEST.JK"


# ------------------------------------------------------- how concentrated is it
def test_average_correlation_makes_one_bet_visible():
    tickers = ["TINS.JK", "TAPG.JK", "ADRO.JK"]
    m = _corr({("TINS.JK", "TAPG.JK"): 0.80, ("TINS.JK", "ADRO.JK"): 0.78,
               ("TAPG.JK", "ADRO.JK"): 0.76}, tickers)

    assert average_correlation(tickers, m) == 0.78


def test_a_single_name_has_no_average_correlation():
    """None, not 0.0 -- which would read as 'perfectly diversified'."""
    m = _corr({("A.JK", "B.JK"): 0.5}, ["A.JK", "B.JK"])
    assert average_correlation(["A.JK"], m) is None
    assert average_correlation([], m) is None
    assert average_correlation(["A.JK", "B.JK"], None) is None
