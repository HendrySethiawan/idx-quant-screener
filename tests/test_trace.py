"""
Tests for the decision trail.

Two properties carry this feature, and both are the kind that fail silently:

  * The funnel must reconcile. `n_in - dropped == n_out` at every stage, and each
    stage must start where the last one ended. A funnel that does not add up is
    hiding names, and nobody catches that by eye -- it caught a real gap on the
    first run, where the sizing stage reported 8 in, 3 out and 1 dropped because
    names that merely ran out of slots were not being recorded.

  * The trail must match the decision, not describe it. The reasons stored are the
    exact strings the gates produced. If the view ever re-derives a rule instead of
    reading what happened, it will drift and start confidently explaining decisions
    that were never made.
"""
import pandas as pd
import pytest

from analysis.selection import sector_capped_pick
from analysis.trace import DROPPED, NOT_REACHED, PASSED, DecisionTrail
from market.regime import Regime, Signal
from report.assemble import assemble


@pytest.fixture
def regime_off():
    return Regime([Signal("IHSG trend", "^JKSE", False, "below trend")],
                  0.30, "RISK-OFF", "R", "Deploy 30%.")


@pytest.fixture
def scored():
    """
    Twelve names: one with no volume, one too thin, three sharing a sector so the
    cap has something to do, and one whose lot costs more than any slot.
    """
    rows = []
    for i in range(12):
        rows.append({
            "ticker": f"S{i:02d}.JK", "name": f"Co {i}",
            "sector": ["Financials", "Financials", "Financials", "Energy"][i % 4],
            "undervaluation_score": 1.0 - i * 0.05,
            "composite_score": 1.0 - i * 0.05,
            "last_close": 1000.0,
            "median_daily_value_rp": 5e9,
            "imputed_factors": "" if i % 3 else "roe",
        })
    rows[3]["median_daily_value_rp"] = 0.0        # no volume at all
    rows[7]["median_daily_value_rp"] = 1_000.0    # below the floor
    rows[1]["last_close"] = 90_000.0              # one lot dwarfs any slot
    return pd.DataFrame(rows)


@pytest.fixture
def settings12(settings_mock, scored):
    """settings_mock carries four tickers; point it at the twelve under test."""
    settings_mock.stock_tickers = {t: t for t in scored["ticker"]}
    settings_mock.sectors = dict(zip(scored["ticker"], scored["sector"]))
    return settings_mock


def _trail(scored, settings, regime_off):
    return assemble(settings, scored, regime_off, [])["trail"]


# ------------------------------------------------------------- reconciliation
def test_every_stage_closes(scored, settings12, regime_off):
    for stage in _trail(scored, settings12, regime_off).stages:
        assert stage.reconciles, (
            f"{stage.key}: {stage.n_in} in - {stage.n_dropped} dropped != {stage.n_out} out"
        )


def test_each_stage_starts_where_the_last_one_ended(scored, settings12, regime_off):
    trail = _trail(scored, settings12, regime_off)
    for before, after in zip(trail.stages, trail.stages[1:]):
        assert after.n_in == before.n_out, f"{before.key} -> {after.key} loses names"
    assert trail.reconciles()


def test_no_name_ever_silently_disappears(scored, settings12, regime_off):
    """At every stage a name is kept, dropped-with-a-reason, or already out."""
    trail = _trail(scored, settings12, regime_off)
    for ticker in trail.universe:
        for row in trail.journey(ticker):
            assert row["status"] in (PASSED, DROPPED, NOT_REACHED)
            if row["status"] == DROPPED:
                assert row["detail"], f"{ticker} dropped at {row['key']} with no reason"


def test_a_dropped_name_is_not_blamed_by_later_gates(scored, settings12, regime_off):
    """
    Once out, a name reads 'not reached' rather than failing every later gate. It
    did not fail them; it never saw them, and saying otherwise blames the wrong rule.
    """
    trail = _trail(scored, settings12, regime_off)
    journey = trail.journey("S03.JK")           # zero volume, out at the liquidity gate
    stops = [i for i, r in enumerate(journey) if r["status"] == DROPPED]
    assert len(stops) == 1
    assert all(r["status"] == NOT_REACHED for r in journey[stops[0] + 1:])


# ------------------------------------------------- the trail is not a rewrite
def test_the_last_stage_is_the_book_that_was_actually_built(scored, settings12, regime_off):
    plan = assemble(settings12, scored, regime_off, [])
    assert plan["trail"].survivors == plan["allocation"].tickers()


def test_every_ticket_name_survived_the_trail(scored, settings12, regime_off):
    plan = assemble(settings12, scored, regime_off, [])
    bought = [o["ticker"] for o in plan["orders"] if o["action"] == "BUY"]
    assert set(bought) <= set(plan["trail"].survivors)


def test_reasons_are_the_gates_own_words(scored, settings12, regime_off):
    """
    Not a paraphrase. If the view ever re-words these, the reader is being told
    something the code never decided.
    """
    plan = assemble(settings12, scored, regime_off, [])
    liquidity = plan["trail"].stage("liquidity")
    for ticker, reason in liquidity.dropped.items():
        assert reason == plan["rejected"][ticker]


def test_the_liquidity_reason_carries_the_real_number(scored, settings12, regime_off):
    trail = _trail(scored, settings12, regime_off)
    assert "no trading volume" in trail.stage("liquidity").dropped["S03.JK"]


# ------------------------------------------- cap and shortlist are different
def test_being_capped_reads_differently_from_being_out_ranked(scored, settings12, regime_off):
    """
    The regression guard for what prompted this. These were one step, so a name
    crowded out by its sector and a name that simply ranked 12th both just
    vanished -- and `capped` came back empty on every real run.
    """
    trail = _trail(scored, settings12, regime_off)
    cap, shortlist = trail.stage("sector_cap"), trail.stage("shortlist")
    assert cap is not None and shortlist is not None

    for reason in cap.dropped.values():
        assert "already ranked above it" in reason
    for reason in shortlist.dropped.values():
        assert "ranked #" in reason and "top" in reason
    assert not (set(cap.dropped) & set(shortlist.dropped)), "a name blamed twice"


def test_the_sector_cap_actually_fires(scored, settings12, regime_off):
    """Three Financials in a row against max_per_sector=2 must cost someone a slot."""
    trail = _trail(scored, settings12, regime_off)
    assert trail.stage("sector_cap").n_dropped > 0


# ---------------------------------------------------- sector_capped_pick itself
def test_sector_capped_pick_reports_who_it_skipped():
    skipped = {}
    picked = sector_capped_pick(
        ["A", "B", "C", "D"],
        {"A": "Bank", "B": "Bank", "C": "Bank", "D": "Energy"},
        top_n=3, max_per_sector=2, skipped=skipped,
    )
    assert picked == ["A", "B", "D"]
    assert "C" in skipped and "Bank" in skipped["C"]


def test_a_backfilled_name_is_not_reported_as_excluded():
    """It was skipped, then let back in. Calling that an exclusion is a false claim."""
    skipped = {}
    picked = sector_capped_pick(
        ["A", "B", "C"], {"A": "Bank", "B": "Bank", "C": "Bank"},
        top_n=3, max_per_sector=2, skipped=skipped,
    )
    assert picked == ["A", "B", "C"]
    assert skipped == {}


def test_sector_capped_pick_still_works_without_the_out_param():
    assert sector_capped_pick(["A", "B"], {"A": "X", "B": "X"}, 2, 1) == ["A", "B"]


# --------------------------------------------------------------- the payload
def test_the_payload_covers_every_name_including_the_first_casualties(
        scored, settings12, regime_off):
    """The names people look up are the ones that fell out, not the survivors."""
    trail = _trail(scored, settings12, regime_off)
    payload = trail.as_payload()
    assert set(payload["names"]) == set(trail.universe)
    assert "S03.JK" in payload["names"]
    assert payload["names"]["S03.JK"]["outcome"]


def test_outcome_names_the_stage_that_stopped_it(scored, settings12, regime_off):
    trail = _trail(scored, settings12, regime_off)
    assert "Can you get back out?" in trail.outcome("S03.JK")
    for survivor in trail.survivors:
        assert "every stage" in trail.outcome(survivor)


def test_an_unknown_ticker_is_not_in_the_universe(scored, settings12, regime_off):
    assert "not in the universe" in _trail(scored, settings12, regime_off).outcome("NOPE.JK")


# --------------------------------------------------------------- the container
def test_an_empty_trail_is_harmless():
    trail = DecisionTrail()
    assert trail.reconciles()
    assert trail.funnel() == [] and trail.universe == [] and trail.survivors == []
    assert trail.as_payload()["names"] == {}


def test_n_in_defaults_to_the_previous_stage_output():
    trail = DecisionTrail()
    trail.record("a", "A", "rule", kept=["x", "y", "z"])
    trail.record("b", "B", "rule", kept=["x"], dropped={"y": "why", "z": "why"})
    assert trail.stages[1].n_in == 3
    assert trail.reconciles()


def test_funnel_widths_are_relative_to_the_start():
    trail = DecisionTrail()
    trail.record("a", "A", "r", kept=list("abcd"))
    trail.record("b", "B", "r", kept=list("ab"), dropped={"c": "x", "d": "x"})
    widths = [row["width"] for row in trail.funnel()]
    assert widths == pytest.approx([1.0, 0.5])
