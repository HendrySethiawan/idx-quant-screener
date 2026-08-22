from analysis.selection import sector_capped_pick

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
