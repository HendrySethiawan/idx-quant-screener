"""
Tests for the terminal shell.

The shell holds no data and decides nothing about a stock, so what is worth
guarding is structural: that a destination cannot become unreachable, that an empty
panel does not leave a frame around nothing, and that the one CSS rule the whole
layout rests on -- the page cannot scroll, panels can -- is actually present.
"""
import re

import pytest

from report import terminal as T


PAGES = [
    T.Page("markets", "Markets", "markets", "<p>ticket</p>"),
    T.Page("screener", "Screener", "screener", "<p>universe</p>"),
    T.Page("why", "Why", "why", "<p>trail</p>"),
]


# ------------------------------------------------------------------- panels
def test_a_panel_wraps_its_body_with_a_header():
    out = T.panel("Do this today", "<p>buy</p>")
    assert "Do this today" in out and "<p>buy</p>" in out
    assert 'class="pnl-bd"' in out


def test_an_empty_panel_is_not_rendered_at_all():
    """A frame around nothing reads as a broken panel, not as an empty one."""
    assert T.panel("Title", "") == ""
    assert T.panel("Title", "   ") == ""
    assert T.panel("Title", None) == ""


def test_only_a_growing_panel_takes_the_leftover_height():
    assert "grow" in T.panel("A", "<p>x</p>", grow=True)
    assert "grow" not in T.panel("A", "<p>x</p>")


def test_panel_titles_are_escaped():
    out = T.panel("<img src=x onerror=alert(1)>", "<p>body</p>")
    assert "<img src=x" not in out
    assert "&lt;img" in out


def test_an_empty_column_disappears_rather_than_leaving_a_gap():
    assert T.column(["", "  ", None]) == ""
    assert 'class="col"' in T.column(["<section>x</section>"])


def test_the_grid_counts_only_the_columns_that_have_content():
    assert 'class="grid cols-2"' in T.grid(["<div>a</div>", "", "<div>b</div>"])


# --------------------------------------------------------------------- rail
def test_every_page_gets_a_rail_entry():
    """
    A page with no rail entry is unreachable, and unlike a scrolling document
    nothing on screen hints that it exists.
    """
    nav = T.rail(PAGES)
    for p in PAGES:
        assert f'data-page="{p.key}"' in nav
        assert p.label in nav


def test_exactly_one_rail_entry_is_current():
    nav = T.rail(PAGES, "screener")
    assert nav.count('aria-current="page"') == 1
    assert nav.count("rail-item on") == 1


def test_the_first_page_is_current_by_default():
    assert 'data-page="markets" aria-current="page"' in T.rail(PAGES).replace(
        'class="rail-item on" ', "")


def test_rail_labels_are_escaped():
    nav = T.rail([T.Page("x", "<b>evil</b>", "markets", "<p>x</p>")])
    assert "<b>evil</b>" not in nav
    assert "&lt;b&gt;" in nav


def test_an_empty_rail_renders_nothing():
    assert T.rail([]) == ""


# -------------------------------------------------------------------- pages
def test_exactly_one_page_starts_visible():
    out = T.pages_html(PAGES)
    assert out.count('class="page on"') == 1
    assert out.count('class="page"') == len(PAGES) - 1


def test_the_visible_page_is_the_one_asked_for():
    out = T.pages_html(PAGES, "why")
    assert '<div class="page on" id="page-why"' in out


def test_every_rail_entry_resolves_to_a_page():
    """The orphan guard, on both sides at once."""
    nav, body = T.rail(PAGES), T.pages_html(PAGES)
    targets = set(re.findall(r'data-page="([^"]+)"', nav))
    ids = set(re.findall(r'id="page-([^"]+)"', body))
    assert targets == ids


# ------------------------------------------------------------------ topbar
def test_the_topbar_shows_state_not_actions():
    """
    The reference layout puts BUY and SELL here. A control that looks like it
    trades, beside the real broker, is a hazard -- this shows the regime instead.
    """
    out = T.topbar("IDX Terminal", "as of Fri 21 Aug", [("RISK-OFF", "deploy 30%", "bad")])
    assert "RISK-OFF" in out and "deploy 30%" in out
    assert "<button" not in out and "<form" not in out


def test_the_topbar_timestamp_is_text_not_a_clock():
    """A ticking clock over daily data claims a feed this tool does not have."""
    out = T.topbar("IDX Terminal", "as of Fri 21 Aug 2026, 12:57", [])
    assert "as of" in out
    assert "setInterval" not in out


def test_topbar_values_are_escaped():
    out = T.topbar("<b>x</b>", "<i>y</i>", [("<u>k</u>", "v", "")])
    for raw in ("<b>x</b>", "<i>y</i>", "<u>k</u>"):
        assert raw not in out


# ------------------------------------------------------------------ ticker
def test_the_ticker_marks_direction():
    out = T.tickerbar([("BBRI", "Rp4,150", 12.0), ("UNVR", "Rp1,795", -90.0)])
    assert "tk-d up" in out and "tk-d down" in out


def test_the_ticker_tolerates_an_unknown_direction():
    out = T.tickerbar([("WIKA", "Rp300", None)])
    assert "WIKA" in out
    assert "tk-d up" not in out and "tk-d down" not in out


def test_an_empty_ticker_renders_nothing():
    assert T.tickerbar([]) == ""


def test_ticker_labels_are_escaped():
    assert "<img" not in T.tickerbar([("<img src=x>", "1", 1.0)])


# ------------------------------------------------------------------- theme
def test_the_page_cannot_scroll_but_a_panel_can():
    """The single rule the whole layout rests on."""
    assert "html,body{height:100%;margin:0;overflow:hidden}" in T.THEME_CSS
    assert re.search(r"\.pnl-bd\{[^}]*overflow:auto", T.THEME_CSS)


def test_a_narrow_screen_gets_normal_scrolling_back():
    narrow = T.THEME_CSS.split("@media (max-width:900px)")[1]
    assert "html,body{overflow:auto;height:auto}" in narrow


def test_the_theme_is_dark_first_but_light_still_wins():
    assert T.THEME_CSS.index(":root{") < T.THEME_CSS.index('[data-theme="light"]')
    assert "--bg:#0a0e13" in T.THEME_CSS


def test_the_document_is_self_contained():
    out = T.document(title="T", head="markets", rail_html=T.rail(PAGES),
                     top_html=T.topbar("T", "now", []), body_html=T.pages_html(PAGES),
                     tick_html=T.tickerbar([]), css=T.THEME_CSS, js=T.SHELL_JS)
    for external in ("<script src", "<link ", "@import", "http://", "https://", "srcset"):
        assert external not in out


def test_the_document_names_its_landing_page():
    out = T.document(title="T", head="markets", rail_html="", top_html="",
                     body_html="", tick_html="", css="", js="")
    assert '<body data-page="markets">' in out


def test_the_standalone_document_theme_restores_scrolling():
    """The backtest is a report you read top to bottom, not a terminal."""
    assert "html,body{overflow:auto;height:auto}" in T.DOC_CSS
    assert ".wrap{max-width:" in T.DOC_CSS
