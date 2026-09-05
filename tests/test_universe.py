"""
The shipped ticker list, checked against the rules that chose it.

The universe is the one input nothing downstream can recover from. A name that is
absent can never be suggested however good it is, and a name that is present but
untradeable quietly does two kinds of damage: it inflates "74 names screened" into
a claim the page cannot back, and it sits in every cross-sectional z-score as a
peer you could not actually have bought instead.

These assert the *config as shipped*, not a fixture. That is the point -- the file
is edited by hand, and a mistyped ticker or a forgotten sector label would
otherwise surface as a silent gap in a factor nobody is watching.

The gates themselves are tested elsewhere (test_technical, test_sizing,
test_market); this only checks that the list obeys them.
"""
import pandas as pd
import pytest
import yaml

from core.config import load_settings

CONFIG = "configs/default.yaml"

# IDX-IC. Eleven sectors, and "Unknown" is not one of them -- an unmapped ticker
# used to fall through to it, which switched off sector-neutral scoring for that
# name without saying so.
IDX_IC_SECTORS = {
    "Basic Materials", "Consumer Cyclicals", "Consumer Non-Cyclicals", "Energy",
    "Financials", "Healthcare", "Industrials", "Infrastructure", "Properties",
    "Technology", "Transportation",
}

# Dropped for failing the liquidity gate on every run. Named individually so
# re-adding one is a deliberate act with a test to delete, not an accident.
RETIRED = {
    "WIKA.JK": "Rp0 traded per day, 98% flat sessions, no measurable ATR",
    "ADHI.JK": "Rp0 traded per day, 62% flat sessions",
    "BBSI.JK": "Rp2.3 juta traded per day against a Rp250 juta floor",
    "BTPN.JK": "Rp56 juta traded per day",
    "BNLI.JK": "Rp116 juta traded per day",
}


@pytest.fixture(scope="module")
def shipped():
    with open(CONFIG, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


@pytest.fixture(scope="module")
def settings():
    """The merged view the app actually runs on, not just the YAML."""
    return load_settings(CONFIG, user_config_path=None)


# ------------------------------------------------------------------ integrity
def test_every_ticker_has_a_sector(shipped):
    missing = sorted(set(shipped["stock_tickers"]) - set(shipped["sectors"]))
    assert not missing, f"no sector for {missing}"


def test_no_sector_entry_without_a_ticker(shipped):
    """A stale sector line is harmless until someone reads the file and believes it."""
    orphans = sorted(set(shipped["sectors"]) - set(shipped["stock_tickers"]))
    assert not orphans, f"sector mapped for a ticker that is not screened: {orphans}"


def test_every_sector_is_a_real_idx_ic_sector(shipped):
    unknown = sorted({s for s in shipped["sectors"].values()} - IDX_IC_SECTORS)
    assert not unknown, f"not IDX-IC sectors: {unknown}"


def test_every_ticker_is_suffixed_and_uppercase(shipped):
    bad = [t for t in shipped["stock_tickers"] if not t.endswith(".JK") or t != t.upper()]
    assert not bad, f"malformed tickers: {bad}"


def test_no_ticker_is_also_a_benchmark(shipped):
    """`^JKSE` in the screened list would rank the index against its own members."""
    overlap = set(shipped["stock_tickers"]) & set(shipped.get("benchmarks") or {})
    assert not overlap, f"benchmark inside the universe: {sorted(overlap)}"


def test_every_name_is_non_empty(shipped):
    blank = [t for t, n in shipped["stock_tickers"].items() if not str(n).strip()]
    assert not blank, f"no company name for {blank}"


# ------------------------------------------------------------ the scoring floor
def test_every_sector_can_be_scored_against_its_own_peers(shipped, settings):
    """
    The reason the universe grew. `roe`, `gross_margin` and `debt_to_equity` are
    z-scored within sector, but only when a sector holds `min_sector_size` names.
    Below that they fall back to universe-wide *silently* -- comparing a hospital's
    return on equity to a coal miner's.

    Six of eleven sectors used to fail this, and two (Properties, Transportation)
    had no names at all.
    """
    counts = pd.Series(list(shipped["sectors"].values())).value_counts()
    floor = int(settings.min_sector_size)
    thin = sorted(f"{s} ({n})" for s, n in counts.items() if n < floor)
    assert not thin, f"below min_sector_size={floor}, so scored universe-wide: {thin}"


def test_the_sector_neutral_factors_are_the_ones_this_protects(settings):
    """If a factor leaves that list, the test above stops guarding anything."""
    assert set(settings.sector_neutral_factors) == {
        "roe", "gross_margin", "debt_to_equity"}


def test_no_single_sector_dominates_the_cross_section(shipped):
    """
    Seven of the ten factors are z-scored against the WHOLE universe, so a list
    that is half one sector measures every name against that sector's conditions.
    Energy and Financials were 47% of the old 49.
    """
    counts = pd.Series(list(shipped["sectors"].values())).value_counts()
    share = counts.iloc[0] / counts.sum()
    assert share < 0.25, (
        f"{counts.index[0]} is {share:.0%} of the universe; seven factors are "
        f"z-scored against the whole list")


# ------------------------------------------------------------------ what is gone
@pytest.mark.parametrize("ticker,why", sorted(RETIRED.items()))
def test_a_retired_name_stays_out(shipped, ticker, why):
    assert ticker not in shipped["stock_tickers"], f"{ticker} was removed: {why}"
    assert ticker not in shipped["sectors"], f"{ticker} was removed: {why}"


# ------------------------------------------------------- what the page will claim
def test_the_universe_is_large_enough_for_a_cross_section(shipped, settings):
    """
    Every score is a z-score against the rest of the list, and the sector cap plus
    the shortlist have to have something to choose from: `max_per_sector` x the
    sectors present must comfortably exceed `top_picks_n`.
    """
    n = len(shipped["stock_tickers"])
    sectors = len(set(shipped["sectors"].values()))
    assert n >= 50, f"only {n} names is a thin cross-section for ten factors"
    assert sectors * settings.max_per_sector > settings.top_picks_n


def test_the_settings_loader_sees_the_same_list(shipped, settings):
    """
    The YAML and the merged Settings must agree. `_apply_overrides` replaces rather
    than merges `stock_tickers` and `sectors`, so a user.yaml with a partial list
    silently shrinks the universe -- this pins the shipped default.
    """
    assert set(settings.stock_tickers) == set(shipped["stock_tickers"])
    assert set(settings.sectors) == set(shipped["sectors"])


# --------------------------------------------------- counts the page states
def test_no_source_file_hardcodes_the_universe_size(shipped):
    """
    The page said "All 49 names and the evidence" for a day after the universe
    grew to 74, and the Update button promised "49 tickers, about 40 seconds".
    Both were literals. A count written by hand is a claim that goes quietly wrong
    the moment the thing it counts changes, and nothing fails when it does.
    """
    import pathlib
    import re

    # A literal two-digit number followed by "names"/"tickers"/"stocks" inside a
    # string. An f-string interpolating `universe_n` does not match, and neither
    # does a comment -- prose about how the code came to be is not a claim the page
    # makes. Verified against both real offenders before this was committed.
    pattern = r'["\'][^"\']*\b\d{2}\b\s*(names|tickers|stocks)'
    offenders = [
        f"{path}:{i}: {line.strip()}"
        for path in pathlib.Path("src").rglob("*.py")
        for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if re.search(pattern, line.split("#", 1)[0], re.I)
    ]
    assert not offenders, (
        "a universe count is hardcoded in a user-visible string; derive it from "
        "the frame instead:\n  " + "\n  ".join(offenders))
