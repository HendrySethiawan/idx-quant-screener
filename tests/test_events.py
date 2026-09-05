"""
Event calendar.

The tests that matter most are the coverage ones: with earnings dates available for
only a third of the universe, "no event shown" must never be allowed to read as
"no event coming".
"""
from datetime import date, timedelta

import pandas as pd
import pytest

from market.events import (CLEAR, KNOWN, UNKNOWN, Event, add_event, by_ticker,
                           earnings_coverage, estimate_ex_dividend, load_events,
                           normalize_scope, state_for, upcoming)

TODAY = date(2026, 8, 23)


# ------------------------------------------------------------------ scope rules
@pytest.mark.parametrize("raw,expect", [
    ("adro", "ADRO.JK"), ("ADRO", "ADRO.JK"), ("ADRO.JK", "ADRO.JK"),
    ("MSCI", "MSCI"), ("msci", "MSCI"), ("MARKET", "MARKET"), ("BI", "BI"),
])
def test_normalize_scope(raw, expect):
    assert normalize_scope(raw) == expect


def test_market_scopes_do_not_get_a_jk_suffix():
    """MSCI is not a stock; forcing it into a ticker shape would be wrong."""
    assert normalize_scope("MSCI") == "MSCI"
    assert ".JK" not in normalize_scope("FED")


def test_empty_scope_rejected():
    with pytest.raises(ValueError):
        normalize_scope("  ")


# ------------------------------------------------------------------ manual file
def test_add_then_load_roundtrip(tmp_path):
    path = tmp_path / "events.yaml"
    add_event("adro", "earnings", "2026-08-27", path, note="Q2 result")

    events = load_events(path)
    assert len(events) == 1
    assert events[0].scope == "ADRO.JK"
    assert events[0].kind == "earnings"
    assert events[0].date == date(2026, 8, 27)
    assert events[0].source == "manual"


def test_events_are_kept_sorted_by_date(tmp_path):
    path = tmp_path / "events.yaml"
    add_event("BBRI", "earnings", "2026-10-29", path)
    add_event("MSCI", "review", "2026-08-28", path)
    assert [e.date.isoformat() for e in load_events(path)] == ["2026-08-28", "2026-10-29"]


def test_missing_file_returns_empty(tmp_path):
    assert load_events(tmp_path / "nope.yaml") == []


def test_malformed_file_degrades_quietly(tmp_path):
    path = tmp_path / "events.yaml"
    path.write_text("events: [broken: [\n", encoding="utf-8")
    assert load_events(path) == []


def test_one_bad_row_does_not_discard_the_rest(tmp_path):
    """A single typo must not silently blank the whole calendar."""
    path = tmp_path / "events.yaml"
    path.write_text(
        "events:\n"
        "  - {date: not-a-date, scope: BBRI, kind: earnings}\n"
        "  - {date: 2026-08-27, scope: ADRO, kind: earnings}\n",
        encoding="utf-8",
    )
    events = load_events(path)
    assert len(events) == 1
    assert events[0].scope == "ADRO.JK"


# --------------------------------------------------------------------- horizon
def _ev(days, scope="ADRO.JK", kind="earnings", source="auto"):
    return Event(TODAY + timedelta(days=days), scope, kind, source=source)


def test_upcoming_filters_to_the_horizon():
    events = [_ev(-3), _ev(4), _ev(13), _ev(30)]
    near = upcoming(events, 14, today=TODAY)
    assert [e.days_away(TODAY) for e in near] == [4, 13]


def test_upcoming_includes_today():
    assert len(upcoming([_ev(0)], 14, today=TODAY)) == 1


def test_upcoming_excludes_the_past():
    assert upcoming([_ev(-1)], 14, today=TODAY) == []


@pytest.mark.parametrize("days,expect", [
    (0, "today"), (1, "tomorrow"), (4, "in 4 days"),
])
def test_describe_wording(days, expect):
    assert expect in _ev(days).describe(TODAY)


def test_estimated_events_are_labelled():
    e = _ev(4, source="estimated")
    assert "(est.)" in e.describe(TODAY)


# ------------------------------------------------- the coverage rule (the point)
def test_blind_ticker_reports_unknown_not_clear():
    """
    SRTG, TINS and TAPG have no earnings date anywhere. Reporting them as 'clear'
    would tell the user there is no event when we simply cannot see one.
    """
    blind = earnings_coverage(["ADRO.JK", "TINS.JK"], [_ev(4, "ADRO.JK")])
    assert blind == {"TINS.JK"}

    state, msg = state_for("TINS.JK", [], blind, 14, TODAY)
    assert state == UNKNOWN
    assert "check IDX or CNBC" in msg


def test_covered_ticker_with_distant_event_reports_clear():
    blind = earnings_coverage(["ADRO.JK"], [_ev(60, "ADRO.JK")])
    assert blind == set()
    state, msg = state_for("ADRO.JK", [_ev(60, "ADRO.JK")], blind, 14, TODAY)
    assert state == CLEAR
    assert "nothing scheduled" in msg


def test_ticker_with_imminent_event_reports_known():
    blind = earnings_coverage(["ADRO.JK"], [_ev(4, "ADRO.JK")])
    state, msg = state_for("ADRO.JK", [_ev(4, "ADRO.JK")], blind, 14, TODAY)
    assert state == KNOWN
    assert "in 4 days" in msg


def test_unknown_and_clear_produce_different_messages():
    """If these ever collapse into the same string, the feature is broken."""
    _, clear_msg = state_for("A.JK", [_ev(60, "A.JK")], set(), 14, TODAY)
    _, unknown_msg = state_for("B.JK", [], {"B.JK"}, 14, TODAY)
    assert clear_msg != unknown_msg


def test_manual_event_closes_a_coverage_gap():
    """Logging an earnings date by hand should make the name no longer blind."""
    blind = earnings_coverage(["TINS.JK"], [Event(TODAY, "TINS.JK", "earnings", source="manual")])
    assert blind == set()


def test_non_earnings_event_does_not_close_the_gap():
    """An ex-dividend date tells you nothing about when the company reports."""
    blind = earnings_coverage(["TINS.JK"], [Event(TODAY, "TINS.JK", "ex_dividend")])
    assert blind == {"TINS.JK"}


def test_by_ticker_groups_and_sorts():
    grouped = by_ticker([_ev(10, "A.JK"), _ev(2, "A.JK"), _ev(5, "B.JK")])
    assert [e.days_away(TODAY) for e in grouped["A.JK"]] == [2, 10]
    assert list(grouped) == ["A.JK", "B.JK"]


# ------------------------------------------------------ ex-dividend estimation
def _divs(dates):
    return pd.Series([100.0] * len(dates), index=pd.to_datetime(dates))


def test_ex_dividend_estimate_uses_the_usual_month():
    divs = _divs(["2023-04-20", "2024-04-18", "2025-04-22", "2026-04-21"])
    est = estimate_ex_dividend("BBRI.JK", divs, today=date(2026, 8, 23))
    assert est is not None
    assert est.date.month == 4
    assert est.date.year == 2027, "April has passed this year, so look to next"
    assert est.source == "estimated"


def test_ex_dividend_estimate_needs_enough_history():
    assert estimate_ex_dividend("X.JK", _divs(["2025-04-20", "2026-04-21"])) is None
    assert estimate_ex_dividend("X.JK", None) is None


def test_ex_dividend_estimate_notes_its_basis():
    divs = _divs(["2023-06-10", "2024-06-12", "2025-06-11"])
    est = estimate_ex_dividend("TLKM.JK", divs, today=date(2026, 1, 1))
    assert "3 past payments" in est.note


# ------------------------------------------------------- integration with brief
def test_attach_events_marks_each_state():
    from report.assemble import attach_events

    items = [{"ticker": "ADRO.JK"}, {"ticker": "TINS.JK"}, {"ticker": "BBCA.JK"}]
    events = [_ev(4, "ADRO.JK"), _ev(60, "BBCA.JK")]
    blind = earnings_coverage([i["ticker"] for i in items], events)

    # `today=` pinned, or this asserts against the real clock: the events are
    # built relative to a frozen TODAY, so without it the test passes in August
    # and fails in September.
    attach_events(items, events, blind, horizon_days=14, today=TODAY)
    states = {i["ticker"]: i["event_state"] for i in items}

    assert states["ADRO.JK"] == KNOWN
    assert states["TINS.JK"] == UNKNOWN
    assert states["BBCA.JK"] == CLEAR


def test_attach_events_never_removes_an_order():
    """
    Events warn, they do not filter. Blocking would fire only on the third of the
    universe we can see and silently bias the book toward the rest.
    """
    from report.assemble import attach_events

    items = [{"ticker": "ADRO.JK"}, {"ticker": "TINS.JK"}]
    before = [i["ticker"] for i in items]
    attach_events(items, [_ev(1, "ADRO.JK")], {"TINS.JK"}, horizon_days=14)
    assert [i["ticker"] for i in items] == before


def test_brief_shows_the_unknown_state_on_a_ticket_line():
    """A blank cell here would be the bug this whole feature guards against."""
    from portfolio.fees import FeeConfig, estimate_fees
    from report.brief import render_brief

    out = render_brief(
        regime=type("R", (), {"label": "RISK-ON", "emoji": "G", "headline": "",
                              "deploy_pct": 1.0, "signals": []})(),
        orders=[{"action": "BUY", "ticker": "TINS.JK", "lots": 2, "shares": 200,
                 "price": 4030.0, "rupiah": 806_000, "note": "target 33%",
                 "event_state": UNKNOWN,
                 "event_note": "no earnings date available - check IDX or CNBC yourself"}],
        fees=estimate_fees([], FeeConfig()), capital=10_000_000,
        holdings_rows=[], candidates=[], rejected={}, capped={},
        events=[], blind_n=33, universe_n=49,
    )
    assert "no earnings date available" in out
    assert "16 of 49 names have an earnings date" in out


def test_brief_shows_a_known_event_as_a_warning():
    from portfolio.fees import FeeConfig, estimate_fees
    from report.brief import render_brief

    out = render_brief(
        regime=type("R", (), {"label": "RISK-ON", "emoji": "G", "headline": "",
                              "deploy_pct": 1.0, "signals": []})(),
        orders=[{"action": "BUY", "ticker": "ADRO.JK", "lots": 8, "shares": 800,
                 "price": 2550.0, "rupiah": 2_040_000, "note": "target 20%",
                 "event_state": KNOWN, "event_note": "earnings in 4 days"}],
        fees=estimate_fees([], FeeConfig()), capital=10_000_000,
        holdings_rows=[], candidates=[], rejected={}, capped={},
        events=[_ev(4, "ADRO.JK")], blind_n=33, universe_n=49,
    )
    assert "earnings in 4 days" in out
    assert "pill warn" in out


def test_saving_an_event_preserves_the_file_header(tmp_path):
    """The header documents the format for hand-editing; safe_dump would drop it."""
    path = tmp_path / "events.yaml"
    add_event("ADRO", "earnings", "2026-08-27", path)
    text = path.read_text(encoding="utf-8")
    assert text.startswith("# Events you know about")
    assert "--event ADRO earnings" in text
    assert len(load_events(path)) == 1


# ======================================================================
# Index reviews: the one event whose PAST is still a live fact.
#
# GOTO and CPIN left the MSCI Standard index at the close of 31 Aug 2026. Five
# sessions later the screener could rank either of them with nothing on the
# ticket line to say the passive bid had just gone. That reads identically to
# "nothing is happening", which is the silent failure this module exists to
# refuse -- so a review, alone among the kinds, stays attached for a while after
# it lands.
# ======================================================================
def _review(days, scope="GOTO.JK", note="", source="manual"):
    return Event(TODAY + timedelta(days=days), scope, "review", note=note,
                 source=source)


def test_a_review_that_has_just_happened_still_reaches_the_ticket():
    state, msg = state_for("GOTO.JK", [_review(-4)], set(), 14, TODAY)
    assert state == KNOWN
    assert "4 days ago" in msg


def test_a_review_stops_mattering_once_the_flow_is_done():
    """Three weeks out it is history, not a warning."""
    state, _ = state_for("GOTO.JK", [_review(-40)], set(), 14, TODAY)
    assert state != KNOWN


def test_the_lookback_boundary_is_inclusive_on_both_sides():
    assert state_for("G.JK", [_review(-21)], set(), 14, TODAY)[0] == KNOWN
    assert state_for("G.JK", [_review(-22)], set(), 14, TODAY)[0] != KNOWN


def test_a_past_earnings_date_is_still_ignored():
    """
    The lookback is for reviews only. A result that is already out is priced;
    putting every stale earnings date back on the ticket is noise, not risk.
    """
    state, _ = state_for("ADRO.JK", [_ev(-4)], set(), 14, TODAY)
    assert state != KNOWN


def test_a_blind_ticker_with_a_recent_review_reports_the_review():
    """The review is the more specific fact, so it wins over 'we cannot see'."""
    state, msg = state_for("GOTO.JK", [_review(-4)], {"GOTO.JK"}, 14, TODAY)
    assert state == KNOWN
    assert "no earnings date" not in msg


def test_a_coming_event_beats_a_review_that_already_happened():
    state, msg = state_for("GOTO.JK", [_review(-4), _ev(2, "GOTO.JK")],
                           set(), 14, TODAY)
    assert state == KNOWN
    assert "in 2 days" in msg


def test_the_most_recent_review_is_the_one_shown():
    msg = state_for("G.JK", [_review(-18, note="old"), _review(-2, note="new")],
                    set(), 14, TODAY)[1]
    assert "new" in msg and "old" not in msg


def test_a_review_says_what_changed_not_just_that_it_happened():
    """
    "index review 4 days ago" names no action. The note is the whole point of
    having recorded it.
    """
    msg = _review(-4, note="dropped from MSCI Global Standard").describe(TODAY)
    assert "dropped from MSCI Global Standard" in msg


def test_an_earnings_note_stays_off_the_ticket_line():
    """For earnings the date IS the message; the note would only crowd it."""
    e = Event(TODAY + timedelta(days=3), "ADRO.JK", "earnings", note="Q2 result")
    assert "Q2 result" not in e.describe(TODAY)


@pytest.mark.parametrize("days,expect", [(-1, "yesterday"), (-6, "6 days ago")])
def test_past_wording(days, expect):
    assert expect in _review(days).describe(TODAY)


def test_the_events_panel_stays_a_forward_calendar():
    """
    The lookback belongs on the ticket line, where you act on the name. The panel
    is headed "next 14 days" and must not start listing history under it.
    """
    assert upcoming([_review(-4)], 14, today=TODAY) == []


# ------------------------------------------------- the calendar that ships
def test_the_shipped_calendar_is_labelled_as_shipped():
    """
    The reader must not think they recorded it. "you" against a row they never
    typed is the tool putting words in their mouth.
    """
    from market.events import load_calendar
    rows = [{"date": "2026-09-01", "scope": "GOTO.JK", "kind": "review"}]
    got = load_calendar(rows)[0]
    assert got.source == "shipped"
    assert got.source_label == "built in"


def test_a_bad_calendar_row_does_not_discard_the_others():
    from market.events import load_calendar
    rows = [{"scope": "GOTO.JK"},                       # no date
            {"date": "2026-09-01", "scope": "CPIN.JK", "kind": "review"}]
    assert [e.scope for e in load_calendar(rows)] == ["CPIN.JK"]


def test_a_missing_calendar_is_simply_empty():
    from market.events import load_calendar
    assert load_calendar(None) == [] and load_calendar([]) == []


def test_your_own_row_wins_over_the_shipped_one():
    """
    Same date, scope and kind is the same event. If they checked it and wrote it
    down, showing the shipped row beside it is the tool arguing with them.
    """
    from market.events import load_calendar, merge_events
    shipped = load_calendar([{"date": "2026-09-01", "scope": "GOTO.JK",
                              "kind": "review", "note": "shipped wording"}])
    mine = [Event(date(2026, 9, 1), "GOTO.JK", "review", note="mine")]
    merged = merge_events(shipped, mine)
    assert len(merged) == 1
    assert merged[0].note == "mine"


def test_a_shipped_row_you_have_not_recorded_still_arrives():
    from market.events import load_calendar, merge_events
    shipped = load_calendar([{"date": "2026-09-01", "scope": "CPIN.JK",
                              "kind": "review"}])
    mine = [Event(date(2026, 9, 1), "GOTO.JK", "review")]
    assert {e.scope for e in merge_events(shipped, mine)} == {"GOTO.JK", "CPIN.JK"}


# ---------------------------------------------------- end to end, on the ticket
def test_the_shipped_calendar_covers_the_names_the_august_review_hit():
    """
    The five in this universe that MSCI dropped on 31 Aug 2026. If the build ships
    a calendar at all, these are the rows that had to be in it.
    """
    import yaml
    from market.events import load_calendar
    cfg = yaml.safe_load(open("configs/default.yaml", encoding="utf-8"))
    scopes = {e.scope for e in load_calendar(cfg.get("market_calendar"))}
    assert {"GOTO.JK", "CPIN.JK", "BUKA.JK", "ESSA.JK", "HEAL.JK"} <= scopes
    universe = set(cfg["stock_tickers"])
    named = {s for s in scopes if s.endswith(".JK")}
    assert named <= universe, f"calendar names a ticker not in the universe: {named - universe}"


def test_a_dropped_name_carries_its_warning_onto_the_order():
    """
    The whole chain: a shipped calendar row, four days old, reaching the ticket
    line of an order for that name.
    """
    from report.assemble import attach_events
    from market.events import load_calendar

    rows = [{"date": "2026-09-01", "scope": "GOTO.JK", "kind": "review",
             "note": "dropped from MSCI Global Standard"}]
    orders = [{"ticker": "GOTO.JK", "action": "BUY"},
              {"ticker": "BBRI.JK", "action": "BUY"}]
    attach_events(orders, load_calendar(rows), set(), 14, today=date(2026, 9, 5))

    assert orders[0]["event_state"] == KNOWN
    assert "dropped from MSCI Global Standard" in orders[0]["event_note"]
    assert orders[1]["event_state"] != KNOWN


def test_the_lookback_is_configurable_from_settings():
    """A reader who wants a shorter window must be able to have one."""
    from report.assemble import attach_events
    ev = [Event(date(2026, 9, 1), "GOTO.JK", "review", note="dropped")]
    orders = [{"ticker": "GOTO.JK"}]
    attach_events(orders, ev, set(), 14, today=date(2026, 9, 5),
                  review_lookback_days=2)
    assert orders[0]["event_state"] != KNOWN
