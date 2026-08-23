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


def test_simple_is_laid_out_as_a_grid():
    out = _brief()
    assert '<div class="dash">' in out
    assert ".dash{display:grid" in out


def test_the_grid_collapses_on_a_narrow_screen():
    """A wide-screen layout must not strand a phone reader."""
    css = _brief().split("<style>")[1].split("</style>")[0]
    narrow = css.split("@media (max-width:1000px)")[1][:160]
    assert ".dash{grid-template-columns:1fr}" in narrow


def test_the_page_uses_the_whole_screen_but_not_the_whole_line():
    """Wider layout, same readable measure -- the width is for columns, not prose."""
    out = _brief()
    assert "max-width:min(1580px,96vw)" in out
    assert "max-width:74ch" in out


def test_the_ticket_is_never_behind_a_tab():
    """
    The completeness rule under the new layout: everything you act on is outside
    every .panel, so no tab can ever hide the decision.
    """
    out = _brief(advanced_html='<div class="adv">x</div>')
    before_panels = re.split(r'<section class="panel', out)[0]
    for essential in ("Do this today", "BBRI.JK", "3 lot", "RISK-ON", "Estimated cost"):
        assert essential in before_panels, f"{essential!r} ended up behind a tab"


def test_skipped_is_folded_not_dropped():
    """Least vertical space of anything in Simple, but still one click away."""
    out = _brief(rejected={"WIKA.JK": "no trading volume in the last 20 sessions"})
    assert "<details" in out
    assert "Skipped" in out
    assert "WIKA.JK" in out
    assert "no trading volume in the last 20 sessions" in out


def test_printing_still_gives_the_ticket_alone():
    block = _brief(advanced_html='<div class="adv">x</div>').split("@media print")[1][:220]
    assert ".adv" in block and ".steps" in block and "display:none" in block


def test_a_tall_table_scrolls_inside_its_panel_not_the_page():
    """
    The 49-row universe table would make its panel three screens tall, which is
    exactly the scrolling this layout removes.
    """
    css = _brief().split("<style>")[1].split("</style>")[0]
    assert re.search(r"\.panel \.scroll\{[^}]*max-height", css)


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
