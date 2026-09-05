"""
Tests for the tab strip and the dashboard grid.

The one that matters most is test_no_panel_is_left_without_a_tab. Tabs introduce a
failure mode a stacked page does not have: a section that renders but has no tab is
completely invisible, and nothing on the page hints that it exists. On a scroll you
would have seen it. So the invariant "every panel has a tab and every tab has a
panel" is asserted directly rather than trusted.
"""
import re

import pytest

from market.regime import Regime, Signal
from portfolio.fees import FeeConfig, estimate_fees
from report.brief import render_brief
from report.layout import tabbed


PANELS = [("One", "<p>first</p>"), ("Two", "<p>second</p>"), ("Three", "<p>third</p>")]


def _ids(html: str):
    tabs = re.findall(r'data-panel="([^"]+)"', html)
    panels = re.findall(r'class="panel[^"]*" id="([^"]+)"', html)
    return tabs, panels


# ------------------------------------------------------------------ the guard
def test_no_panel_is_left_without_a_tab():
    """A section with no tab is invisible, and nothing reveals it."""
    tabs, panels = _ids(tabbed(PANELS, "g"))
    assert tabs and panels
    assert set(tabs) == set(panels)
    assert len(tabs) == len(panels) == len(PANELS)


def test_no_tab_points_at_a_panel_that_does_not_exist():
    html = tabbed(PANELS, "g")
    for target in re.findall(r'data-panel="([^"]+)"', html):
        assert f'id="{target}"' in html


def test_an_empty_section_takes_its_tab_with_it():
    """A live tab opening onto nothing reads as a broken page."""
    html = tabbed([("Real", "<p>x</p>"), ("Empty", ""), ("Blank", "   "),
                   ("Also real", "<p>y</p>")], "g")
    tabs, panels = _ids(html)
    assert len(tabs) == len(panels) == 2
    assert "Empty" not in html and "Blank" not in html


def test_exactly_one_panel_starts_open():
    html = tabbed(PANELS, "g")
    assert html.count('class="panel on"') == 1
    assert html.count('aria-selected="true"') == 1


def test_the_first_panel_is_the_one_that_opens():
    html = tabbed(PANELS, "g")
    first = re.search(r'class="panel on" id="([^"]+)"', html).group(1)
    assert first == re.search(r'data-panel="([^"]+)"', html).group(1)


@pytest.mark.parametrize("active,expect", [(1, "Two"), (2, "Three"), (99, "Three"), (-5, "One")])
def test_the_active_panel_can_be_chosen_and_is_clamped(active, expect):
    html = tabbed(PANELS, "g", active=active)
    open_id = re.search(r'class="panel on" id="([^"]+)"', html).group(1)
    assert expect.lower().replace(" ", "-") in open_id


# ------------------------------------------------------------------- edges
def test_nothing_to_show_renders_nothing():
    assert tabbed([], "g") == ""
    assert tabbed([("A", ""), ("B", None)], "g") == ""


def test_a_single_section_gets_no_tab_strip():
    """A strip offering one choice is furniture with no purpose."""
    html = tabbed([("Only", "<p>body</p>")], "g")
    assert html == "<p>body</p>"
    assert "tablist" not in html


def test_labels_are_escaped():
    html = tabbed([("<img src=x onerror=alert(1)>", "<p>a</p>"), ("B", "<p>b</p>")], "g")
    assert "<img src=x" not in html
    assert "&lt;img" in html


def test_two_strips_do_not_share_panel_ids():
    """Two groups on one page must never fight over the same panel."""
    a, b = tabbed(PANELS, "alpha"), tabbed(PANELS, "beta")
    assert not (set(_ids(a)[1]) & set(_ids(b)[1]))


def test_panel_ids_survive_awkward_labels():
    html = tabbed([("P&L / net", "<p>a</p>"), ("Worth?", "<p>b</p>")], "g")
    for pid in _ids(html)[1]:
        assert re.fullmatch(r"[a-z0-9-]+", pid), pid


# ------------------------------------------------------- the Simple dashboard
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


def test_the_shell_is_a_full_height_grid():
    out = _brief()
    assert '<div class="app">' in out
    assert ".app{height:100%;display:grid" in out
    assert 'class="rail"' in out


def test_the_page_itself_cannot_scroll_but_panels_can():
    """
    The rule the whole terminal rests on: the window never moves, so the ticket
    stays put while you read a 49-row table beside it.
    """
    css = _brief().split("<style>")[1].split("</style>")[0]
    assert "html,body{height:100%;margin:0;overflow:hidden}" in css
    assert ".pnl-bd{padding:9px 11px;overflow:auto" in css


def test_a_phone_gets_its_scrollbar_back():
    """Panels need room. Below 900px there is none, so the page scrolls normally."""
    css = _brief().split("<style>")[1].split("</style>")[0]
    narrow = css.split("@media (max-width:900px)")[1]
    assert "html,body{overflow:auto;height:auto}" in narrow
    assert ".pnl-bd{overflow:visible" in narrow


def test_there_is_a_middle_breakpoint_so_nothing_is_squashed():
    """
    One jump from three columns to one leaves the 900-1200px band showing three
    squeezed columns, which is worse than either end.
    """
    css = _brief().split("<style>")[1].split("</style>")[0]
    for width in ("1400px", "1100px", "900px"):
        assert f"@media (max-width:{width})" in css


def test_no_column_is_pinned_to_a_pixel_width():
    """
    The panel you act on was the narrowest thing on the page: 360px of fixed
    track for "Do this today" while a sparkline had 727px. Every track is a
    share of the width now, at every breakpoint above the phone, so the ticket
    grows with the window instead of staying the width of a phone on a 1920
    monitor.
    """
    css = _brief().split("<style>")[1].split("</style>")[0]
    above_phone = css.split("@media (max-width:900px)")[0]
    for sel, tracks in re.findall(
            r"(\.grid[^{]*)\{[^}]*grid-template-columns:([^;}]*)", above_phone):
        # `.grid.cols-2` is the Portfolio and Settings pages, which put a form
        # beside a ledger and want the form to stop growing. The three-column
        # markets grid is the one this rule is about.
        if "cols-2" in sel:
            continue
        assert "px" not in tracks, f"fixed track on {sel.strip()}: {tracks.strip()}"


def test_the_two_panels_you_act_on_get_the_wide_columns():
    """
    Ordering, not just width. The ticket and your holdings come first; the
    chart, the candidate list and the skipped names are what you read after
    deciding, so they share the narrow column.
    """
    out = _full_brief()
    markets = out.split('id="page-markets"')[1].split('id="page-portfolio"')[0]
    titles = [t for _, _, ts in _columns(markets) for t in ts[:1]]
    assert titles[0] == "Do this today"
    assert titles[1] == "What you hold"
    # Whatever else there is, the ranked list is not competing with the ticket.
    assert "Do this today" not in titles[2:]


def test_no_panel_title_prints_a_raw_html_entity():
    """
    `T.panel` escapes its title, so an entity passed in is escaped twice and the
    reader sees `IHSG &middot; daily`. Pass the character.
    """
    out = _brief()
    titles = re.findall(r'<div class="pnl-hd"[^>]*>(.*?)</div>', out)
    for title in titles:
        # A real ampersand in a title is fine and escapes to `&amp;`. What is
        # wrong is `&amp;middot;` -- an entity that was already an entity.
        assert not re.search(r"&amp;\w+;", title), f"double-escaped: {title!r}"


def test_the_ticket_is_the_first_panel_of_the_first_page():
    """
    Ten panels of z-scores can make it feel as though something must be done today.
    The ticket comes first in the DOM and in the reading order, whatever it says.
    """
    out = _brief(advanced_html='<div class="adv">x</div>')
    assert 'id="panel-ticket"' in out
    first_page = out.split('<div class="page on" id="page-markets"')[1]
    assert first_page.index('id="panel-ticket"') < 400, "the ticket is not the first panel"
    for essential in ("Do this today", "BBRI.JK", "3 lot", "Estimated cost"):
        assert essential in out


def test_the_ticket_is_on_the_landing_page_not_behind_navigation():
    out = _brief(advanced_html='<div class="adv">x</div>')
    markets = out.split('id="page-markets"')[1].split('<div class="page"')[0]
    for essential in ("Do this today", "BBRI.JK", "3 lot", "Estimated cost"):
        assert essential in markets, f"{essential!r} needs a click to reach"


def test_no_control_looks_like_it_places_a_trade():
    """
    The reference layout has BUY and SELL buttons that send real orders. A control
    that looks like it trades, in a tool sitting beside the real broker, is a
    hazard: this page says what to do, it must never look like it does it.
    """
    out = _brief(advanced_html='<div class="adv">x</div>')

    # The ticket's own "BUY" label is content -- it is the instruction, and it must
    # stay. What must not exist is a *control* that reads BUY or SELL, because that
    # is the thing a hand clicks by reflex.
    for tag in ("button", "a", "input"):
        for element in re.findall(rf"<{tag}[^>]*>(.*?)</{tag}>", out, re.S):
            text = re.sub(r"<[^>]+>", "", element).strip().lower()
            assert text not in ("buy", "sell"), f"a <{tag}> reads {text!r}"
    assert "<form" not in out.lower(), "nothing on this page should submit anything"


def test_the_ticket_fits_a_720p_window_without_its_own_scrollbar():
    """
    720 minus the top bar (48), the ticker (32) and padding (24) leaves about
    616px. A worst-case ticket -- six orders, every one carrying an event warning
    -- has to fit that, or the one panel you must read hides half of itself.

    The estimate is markup-derived, not a real pixel measurement: a true check
    needs a browser and that would mean a new test dependency. It catches the
    regression that matters -- a row growing, a callout being added.
    """
    orders = [{"action": "BUY", "ticker": f"AA{i}.JK", "lots": 3, "shares": 300,
               "price": 4150.0, "rupiah": 1_245_000, "note": "target weight 16%",
               "event_state": "known", "event_note": "earnings in 4 days"}
              for i in range(6)]
    out = _brief(orders=orders,
                 fees=estimate_fees([{"action": "BUY", "rupiah": 1_245_000}] * 6,
                                    FeeConfig()))
    body = out.split('id="panel-ticket"')[1].split("</section>")[0]

    rows = body.count("<tr>")
    callouts = body.count('class="callout')
    est = 30 + 22 + rows * 44 + callouts * 46      # header + thead + rows + callouts
    assert est <= 616, f"ticket needs ~{est}px, only ~616px available at 720p"


def test_skipped_is_folded_not_dropped():
    """Least vertical space of anything on the page, but still one click away."""
    out = _brief(rejected={"WIKA.JK": "no trading volume in the last 20 sessions"})
    assert "<details" in out
    assert "Skipped" in out
    assert "WIKA.JK" in out
    assert "no trading volume in the last 20 sessions" in out


def test_printing_still_gives_the_ticket_alone():
    block = _brief(advanced_html='<div class="adv">x</div>').split("@media print")[1]
    assert ".pnl{display:none}" in block
    assert ".pnl.print{display:block" in block
    assert ".rail,.topbar,.tickbar" in block


def test_prose_keeps_a_readable_measure():
    """A terminal is wide. Paragraphs still must not run the whole width."""
    assert "max-width:78ch" in _brief()


# --------------------------------------------- the guard, on the whole page
def test_no_tab_anywhere_on_the_page_opens_onto_nothing():
    """
    `tabbed` keeps its own tabs and panels in step, but the Steps view builds stage
    panels outside it -- the funnel is their tab strip. This checks the assembled
    document, which is the only place a mismatch between the two could show up.
    """
    from analysis.trace import DecisionTrail
    from report import steps as S

    trail = DecisionTrail()
    trail.record("universe", "The universe", "r", kept=["A.JK", "B.JK"])
    trail.record("gate", "A gate", "r", kept=["A.JK"], dropped={"B.JK": "why"})

    out = _brief(steps_html=S.render_steps(trail),
                 advanced_html='<div class="adv">x</div>')

    targets = set(re.findall(r'data-panel="([^"]+)"', out))
    assert targets, "no tabs were rendered at all"
    for target in targets:
        assert f'id="{target}"' in out, f"tab {target!r} opens onto nothing"


# ------------------------------------------- a column cannot outgrow the viewport
# `.col` is `overflow:hidden` and `.pnl` is `flex:none`, so a column taller than
# the window is silently CLIPPED -- no scrollbar, no hint. Adding a third form to
# the Portfolio column pushed the dividend form half off the bottom and the whole
# "How you are doing" panel off it entirely, and every test still passed: they all
# assert that markup is PRESENT, which it was.
MAX_FIXED_PANELS_PER_COLUMN = 2


def _columns(page_html: str):
    """(fixed, growing, titles) for each column in a rendered page."""
    out = []
    for chunk in re.split(r'<div class="col"', page_html)[1:]:
        fixed = len(re.findall(r'<section class="pnl"', chunk))
        grow = len(re.findall(r'<section class="pnl grow', chunk))
        titles = re.findall(r'class="pnl-ttl">([^<]*)', chunk)[: fixed + grow]
        if fixed or grow:
            out.append((fixed, grow, titles))
    return out


def _full_brief(**kw):
    """The real page, with every optional section switched on."""
    from market.regime import Regime, Signal
    from portfolio.fees import FeeConfig, estimate_fees
    from report.brief import render_brief
    from report.journal_view import cash_form, dividend_form, trade_form

    defaults = dict(
        regime=Regime([Signal("IHSG trend", "^JKSE", True, "above")],
                      1.0, "RISK-ON", "G", "Deploy 100%."),
        orders=[{"action": "BUY", "ticker": "BBRI.JK", "lots": 3, "shares": 300,
                 "price": 4150.0, "rupiah": 1_245_000, "note": "target 33%"}],
        fees=estimate_fees([{"action": "BUY", "rupiah": 1_245_000}], FeeConfig()),
        capital=10_000_000, holdings_rows=[], candidates=[], rejected={}, capped={},
        allocation=None, universe_n=49, imputed_n=0,
        trade_form_html=trade_form(), cash_form_html=cash_form(),
        dividend_form_html=dividend_form(),
        ledger_html="<p>ledger</p>", journal_html="<p>doing</p>",
    )
    defaults.update(kw)
    return render_brief(**defaults)


def test_no_column_stacks_more_fixed_panels_than_fit():
    for fixed, grow, titles in _columns(_full_brief()):
        assert fixed <= MAX_FIXED_PANELS_PER_COLUMN, (
            f"{fixed} fixed-height panels in one column {titles} -- `.col` is "
            "overflow:hidden, so whatever does not fit is clipped with no scrollbar")


def test_every_recording_form_is_reachable():
    """
    All three were rendered before this fix too. Two of them were off-screen, which
    no assertion about markup could see.
    """
    out = _full_brief()
    for form in ('id="trade-form"', 'id="cash-form"', 'id="dividend-form"'):
        assert form in out, form
    # ...and in ONE panel, sharing a tab strip, rather than stacked.
    assert 'data-group="record"' in out


def test_a_column_has_at_most_one_growing_panel():
    """Two growing panels fight over the leftover height; the docstring says so."""
    for fixed, grow, titles in _columns(_full_brief()):
        assert grow <= 1, f"{grow} growing panels in one column {titles}"


def test_the_cli_fallback_does_not_leave_an_empty_tab():
    """
    Opened as a file there are no forms at all. `tabbed` drops empty sections, so
    the strip must not offer a tab pointing at nothing.
    """
    out = _full_brief(trade_form_html="<p>use the command line</p>",
                 cash_form_html="", dividend_form_html="")
    assert 'data-group="record"' not in out, (
        "one section left should render bare, not as a single-choice tab strip")
    assert "use the command line" in out
