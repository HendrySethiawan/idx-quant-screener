"""
Tests for the Steps view.

The view's whole value is that it reports what happened rather than describing
what should have. So these check that what reaches the page is the trail's own
content -- the gates' own reason strings, the real counts -- and that adding a
third mode did not let the ticket drift out of Simple, which is the one thing the
brief must never do.
"""
import json
import re

import pytest

from analysis.trace import DecisionTrail
from market.regime import Regime, Signal
from portfolio.fees import FeeConfig, estimate_fees
from report import steps as S
from report.brief import render_brief


@pytest.fixture
def trail():
    t = DecisionTrail()
    t.record("universe", "The universe", "Names this tool watches.",
             setting="stock_tickers, configs/default.yaml",
             kept=[f"S{i}.JK" for i in range(10)])
    t.record("liquidity", "Can you get back out?", "Must trade enough to exit.",
             setting="liquidity block, configs/default.yaml",
             kept=[f"S{i}.JK" for i in range(8)],
             dropped={"S8.JK": "no trading volume in the last 20 sessions",
                      "S9.JK": "trades only Rp1,000/day, below the Rp250,000,000 floor"})
    t.record("shortlist", "Keep the best few", "Only the top 3 go through.",
             setting="top_picks_n, configs/default.yaml",
             kept=["S0.JK", "S1.JK", "S2.JK"],
             dropped={f"S{i}.JK": f"ranked #{i + 1}; only the top 3 go through"
                      for i in range(3, 8)})
    t.record("sizing", "What fits in whole lots", "IDX trades in 100-share lots.",
             kept=["S0.JK", "S1.JK"],
             dropped={"S2.JK": "1 lot = Rp2,520,000, more than a Rp1,000,000 slot"})
    return t


def _brief(**kw):
    defaults = dict(
        regime=Regime([Signal("IHSG trend", "^JKSE", True, "above trend")],
                      1.0, "RISK-ON", "G", "Deploy 100%."),
        orders=[{"action": "BUY", "ticker": "S0.JK", "lots": 3, "shares": 300,
                 "price": 4150.0, "rupiah": 1_245_000, "note": "target weight 33%"}],
        fees=estimate_fees([{"action": "BUY", "rupiah": 1_245_000}], FeeConfig()),
        capital=10_000_000, holdings_rows=[], candidates=[], rejected={},
        capped={}, allocation=None, universe_n=10, imputed_n=2,
    )
    defaults.update(kw)
    return render_brief(**defaults)


# --------------------------------------------------------------- the funnel
def test_the_funnel_has_a_row_per_stage(trail):
    out = S.funnel(trail)
    assert out.count('class="funnel-row"') == len(trail.stages)
    for stage in trail.stages:
        assert stage.title in out


def test_the_funnel_shows_the_real_counts(trail):
    out = S.funnel(trail)
    assert ">10<" in out and ">8<" in out and ">2<" in out
    assert "&minus;2" in out and "&minus;5" in out


def test_funnel_rows_link_to_their_stage(trail):
    out = S.funnel(trail)
    for stage in trail.stages:
        assert f'href="#step-{stage.key}"' in out
        assert f'id="step-{stage.key}"' in S.stage_card(stage, 1)


def test_the_bar_narrows_as_names_drop(trail):
    widths = [float(w) for w in re.findall(r"width:([\d.]+)%", S.funnel(trail))]
    assert widths == sorted(widths, reverse=True)
    assert widths[0] > widths[-1]


# ----------------------------------------------------------- the stage cards
def test_a_stage_card_names_the_setting_that_controls_it(trail):
    """A rule you cannot find is a rule you cannot change."""
    card = S.stage_card(trail.stage("liquidity"), 2)
    assert "liquidity block, configs/default.yaml" in card
    assert "<code>" in card


def test_a_stage_card_lists_every_dropped_name_with_the_gates_own_reason(trail):
    stage = trail.stage("liquidity")
    card = S.stage_card(stage, 2)
    for ticker, reason in stage.dropped.items():
        assert ticker in card
        assert reason in card, "the view re-worded the gate's reason"


def test_a_stage_that_drops_nobody_says_so(trail):
    assert "Nothing was dropped here" in S.stage_card(trail.stage("universe"), 1)


def test_stage_cards_escape_untrusted_text():
    t = DecisionTrail()
    t.record("x", "<img src=x onerror=alert(1)>", "rule",
             kept=[], dropped={"<b>EVIL</b>": "<script>alert(1)</script>"})
    card = S.stage_card(t.stages[0], 1)
    assert "<img src=x" not in card
    assert "<script>alert(1)</script>" not in card
    assert "&lt;" in card


# ----------------------------------------------------------- the lookup box
def test_every_name_is_searchable_including_the_first_casualties(trail):
    """The names people look up are the ones that fell out."""
    out = S.trace_search(trail)
    payload = json.loads(
        re.search(r'id="trace-data">(.*?)</script>', out, re.S).group(1)
        .replace("\\u003c", "<")
    )
    assert set(payload["names"]) == set(trail.universe)
    assert payload["names"]["S8.JK"]["outcome"].startswith("Can you get back out?")


def test_the_payload_cannot_close_its_own_script_tag():
    t = DecisionTrail()
    t.record("x", "X", "r", kept=["</script><img src=x>"], dropped={})
    payload = re.search(r'id="trace-data">(.*?)</script>', S.trace_search(t), re.S).group(1)
    assert "<" not in payload
    json.loads(payload.replace("\\u003c", "<"))


def test_the_lookup_offers_the_whole_universe_as_suggestions(trail):
    out = S.trace_search(trail)
    assert out.count("<option") == len(trail.universe)


# --------------------------------------------------------------- composition
def test_render_steps_produces_nothing_without_a_trail():
    assert S.render_steps(None) == ""
    assert S.render_steps(DecisionTrail()) == ""


def test_render_steps_wraps_everything_for_the_toggle(trail):
    out = S.render_steps(trail)
    assert out.startswith('<div class="steps">')
    assert "How today's picks were chosen" in out
    assert "Why was a particular stock not suggested?" in out


def test_the_outcome_line_names_what_was_bought(trail):
    out = S.render_steps(trail, [{"action": "BUY", "ticker": "S0.JK", "lots": 4}])
    assert "S0.JK" in out and "4 lot" in out


def test_an_empty_book_is_reported_as_a_result_not_a_failure():
    t = DecisionTrail()
    t.record("a", "A", "r", kept=["X.JK"])
    t.record("b", "B", "r", kept=[], dropped={"X.JK": "too expensive"})
    assert "empty ticket" in S.outcome_line(t, [])


# ------------------------------------------------------ the three-mode brief
def test_simple_stays_complete_with_a_third_mode(trail):
    """
    The rule that must survive every new mode: everything needed to trade is
    outside both .steps and .adv, so it is visible by default.
    """
    out = _brief(steps_html=S.render_steps(trail), advanced_html='<div class="adv">x</div>')
    simple = out.split('<div class="steps">')[0].split('<div class="adv">')[0]
    for essential in ("Do this today", "S0.JK", "3 lot", "RISK-ON", "Estimated cost"):
        assert essential in simple, f"{essential!r} left the default view"


def _nav(out: str) -> str:
    """Just the toggle bar. `data-mode` also appears in the CSS for every mode,
    so searching the whole document cannot tell you which buttons exist."""
    return out.split('<nav class="modes">')[1].split("</nav>")[0] if "modes" in out else ""


def test_three_buttons_appear_when_all_three_modes_exist(trail):
    nav = _nav(_brief(steps_html=S.render_steps(trail),
                      advanced_html='<div class="adv">x</div>'))
    for mode in ("simple", "steps", "advanced"):
        assert f'data-mode="{mode}"' in nav


def test_a_mode_with_no_content_gets_no_button(trail):
    """A switch that leads nowhere is worse than no switch."""
    nav = _nav(_brief(steps_html=S.render_steps(trail)))
    assert 'data-mode="steps"' in nav
    assert 'data-mode="advanced"' not in nav


def test_no_nav_at_all_when_only_simple_exists():
    assert 'nav class="modes"' not in _brief()


def test_each_mode_hides_the_others(trail):
    css = _brief(steps_html=S.render_steps(trail),
                 advanced_html='<div class="adv">x</div>').split("<style>")[1]
    assert re.search(r'body\[data-mode="simple"\][^{}]*\.steps[^{}]*\{[^}]*display:none', css)
    assert re.search(r'body\[data-mode="steps"\][^{}]*\.adv\s*\{[^}]*display:none', css)
    assert re.search(r'body\[data-mode="advanced"\][^{}]*\.steps\s*\{[^}]*display:none', css)


def test_the_nav_stays_put_while_scrolling(trail):
    """With 16+ sections it used to scroll away exactly when you needed it."""
    out = _brief(steps_html=S.render_steps(trail))
    assert "position:sticky" in out
    assert 'id="jump-to"' in out


def test_printing_drops_the_steps_too(trail):
    out = _brief(steps_html=S.render_steps(trail))
    block = out.split("@media print")[1][:220]
    assert ".steps" in block and "display:none" in block


def test_the_steps_view_stays_self_contained(trail):
    out = _brief(steps_html=S.render_steps(trail))
    for external in ("<script src", "<link ", "@import", "url(", "srcset",
                     "http://", "https://"):
        assert external not in out


def test_steps_needs_no_matplotlib_or_network(monkeypatch, trail):
    import builtins
    real = builtins.__import__

    def guard(name, *a, **k):
        if name.split(".")[0] in {"matplotlib", "seaborn", "yfinance", "requests"}:
            raise ImportError(name)
        return real(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", guard)
    assert S.render_steps(trail)
