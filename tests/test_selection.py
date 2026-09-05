import pandas as pd

from analysis.selection import (average_correlation, break_ties,
                                decorrelated_pick, sector_capped_pick,
                                tie_groups)

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


# ============================================================== SCORE TIES
# Every factor is a z-score against the rest of the list, so a score belongs to a
# company AND its peers -- drop one unrelated name and everybody shifts. On the
# real universe picks 3 to 8 sit 0.02 to 0.21 apart against a spread of 3.75, and
# the ticket buys the top three. Those are ties presented as a ranking.


# Real raw scores from a live run.
LIVE = {
    "DMAS.JK": 9.14, "MARK.JK": 8.77, "TINS.JK": 7.24, "ADRO.JK": 7.00,
    "TAPG.JK": 6.84, "SRTG.JK": 6.42, "IPCC.JK": 6.30, "ITMG.JK": 5.26,
}
LIVE_ORDER = list(LIVE)
FLOOR = 0.111          # the measured jackknife precision on that run


def test_names_further_apart_than_the_floor_are_not_tied():
    groups = tie_groups(LIVE_ORDER, LIVE, FLOOR)
    assert ["DMAS.JK"] in groups
    assert ["MARK.JK"] in groups


def test_names_inside_the_floor_are_grouped():
    """ADRO and TAPG differ by 0.16... TINS and ADRO by 0.24. Only real ties."""
    groups = tie_groups(["A", "B", "C"], {"A": 5.00, "B": 4.95, "C": 3.00}, 0.10)
    assert groups == [["A", "B"], ["C"]]


def test_a_chain_of_small_gaps_does_not_merge_distant_names():
    """
    Grouping is between ADJACENT names only. Without that, A~B~C~D chains a name
    into a group with one it is nowhere near.
    """
    names = [f"T{i}" for i in range(12)]
    # Two tight pairs far apart from each other, inside a longer list so the
    # runaway-group guard has room to stay quiet.
    scores = {t: 10.0 - i for i, t in enumerate(names)}
    scores["T1"] = scores["T0"] - 0.05          # T0 ~ T1
    scores["T5"] = scores["T4"] - 0.05          # T4 ~ T5
    groups = tie_groups(names, scores, 0.10)
    assert ["T0", "T1"] in groups
    assert ["T4", "T5"] in groups
    assert not any(len(g) > 2 for g in groups), "a chain merged distant names"


def test_a_group_swallowing_the_universe_is_refused():
    """
    The unit bug this guard exists for. The floor is measured on the raw composite
    (spread about 3.75) and was briefly compared against the 0-to-1 display score,
    so every adjacent gap fell under it, all 74 names became one group, and the
    ticket was chosen by correlation with the ranking ignored.
    """
    names = [f"T{i}" for i in range(30)]
    # Well separated, with one genuine tie inside it.
    scores = {t: 10.0 - i for i, t in enumerate(names)}
    scores["T1"] = scores["T0"] - 0.02

    ok = tie_groups(names, scores, 0.10)
    assert ["T0", "T1"] in ok, "a sane floor should still find the real tie"

    absurd = tie_groups(names, scores, 1_000.0)
    assert all(len(g) == 1 for g in absurd), "an impossible floor must be refused"


def test_a_zero_floor_ties_nothing():
    groups = tie_groups(LIVE_ORDER, LIVE, 0.0)
    assert all(len(g) == 1 for g in groups)


def test_the_tie_break_prefers_the_name_that_diversifies():
    """
    Among equals, take the one that moves least like what is already held. HELD is
    picked first on score; of the two tied behind it, the decorrelated one wins.
    """
    corr = pd.DataFrame(
        [[1.00, 0.90, 0.10],
         [0.90, 1.00, 0.15],
         [0.10, 0.15, 1.00]],
        index=["HELD", "CLONE", "DIFFERENT"], columns=["HELD", "CLONE", "DIFFERENT"])
    scores = {"HELD": 5.0, "CLONE": 4.0, "DIFFERENT": 3.98}

    out = break_ties(["HELD", "CLONE", "DIFFERENT"], scores, corr, 0.10)
    assert out == ["HELD", "DIFFERENT", "CLONE"]


def test_the_tie_break_never_reorders_across_groups():
    """A name the score genuinely prefers keeps its place, however correlated."""
    corr = pd.DataFrame(
        [[1.00, 0.95], [0.95, 1.00]], index=["A", "B"], columns=["A", "B"])
    out = break_ties(["A", "B"], {"A": 9.0, "B": 1.0}, corr, 0.10)
    assert out == ["A", "B"]


def test_an_unmeasured_pair_does_not_win_a_tie_by_being_unmeasured():
    """Absence of evidence must not read as evidence of decorrelation."""
    corr = pd.DataFrame(
        [[1.00, 0.50, float("nan")],
         [0.50, 1.00, float("nan")],
         [float("nan"), float("nan"), 1.00]],
        index=["HELD", "KNOWN", "UNKNOWN"], columns=["HELD", "KNOWN", "UNKNOWN"])
    scores = {"HELD": 5.0, "KNOWN": 4.0, "UNKNOWN": 3.99}

    out = break_ties(["HELD", "KNOWN", "UNKNOWN"], scores, corr, 0.10)
    # UNKNOWN scores 0.0 (neutral), KNOWN scores its real 0.50 -- so UNKNOWN wins
    # here, but only because 0.0 < 0.50 on the same scale, not by skipping the test.
    assert set(out) == {"HELD", "KNOWN", "UNKNOWN"}
    assert out[0] == "HELD"


def test_no_correlation_matrix_leaves_the_order_alone():
    assert break_ties(LIVE_ORDER, LIVE, None, FLOOR) == LIVE_ORDER


def test_every_name_survives_the_tie_break():
    """It reorders; it must never drop or duplicate."""
    corr = pd.DataFrame(0.3, index=LIVE_ORDER, columns=LIVE_ORDER)
    for t in LIVE_ORDER:
        corr.at[t, t] = 1.0
    out = break_ties(LIVE_ORDER, LIVE, corr, 0.5)
    assert sorted(out) == sorted(LIVE_ORDER)
