"""
The Guide, and the reason it is worth having.

A glossary written once rots the moment somebody adds a pill: the word appears
on screen, the Guide has never heard of it, and the page that exists to explain
the app quietly stops explaining all of it. So the test that matters here is not
that the Guide renders -- it is that the vocabulary the rest of the app is
CAPABLE of putting on screen is enumerated from the code's own constant tables
and checked against the entries.

Same discipline as "every page has a rail entry" and "every bridge method the
script calls actually exists": assert the invariant, do not trust it.
"""
import re

import pytest

from report import guide
from report.guide import GROUPS, TERMS


def _words():
    """Every string an entry covers: its word, plus any internal constant it
    declares. `EXIT` and `ROTATE` are branch names the reader never sees
    verbatim, so they are documented as aliases of the words that do appear."""
    out = set()
    for t in TERMS:
        out.add(t.word.lower())
        out.update(a.lower() for a in t.aka)
    return out


# ------------------------------------------------------ the table is well formed
def test_no_term_is_defined_twice():
    words = [t.word.lower() for t in TERMS]
    dupes = {w for w in words if words.count(w) > 1}
    assert not dupes, f"defined more than once: {sorted(dupes)}"


def test_every_term_actually_says_something():
    for t in TERMS:
        assert t.meaning.strip(), f"{t.word} has no meaning"
        assert len(t.meaning) > 40, f"{t.word} is too short to be an explanation"


def test_every_term_lands_in_a_declared_group():
    """A typo'd group would drop the term into a section nobody renders."""
    declared = {key for key, _ in GROUPS}
    for t in TERMS:
        assert t.group in declared, f"{t.word} has unknown group {t.group!r}"


def test_every_group_has_something_in_it():
    used = {t.group for t in TERMS}
    for key, title in GROUPS:
        assert key in used, f"group {title!r} renders as an empty heading"


# ================================================================= completeness
# Each of these is a string the app can put in front of the reader. Pulled from
# the real maps, so adding a pill without defining it fails the suite.
# ==============================================================================
def test_every_valuation_verdict_is_defined():
    from report.brief import _VALUE_PILL
    for _, shown in _VALUE_PILL.values():
        assert shown.lower() in _words(), f"undefined valuation verdict: {shown}"


def test_every_exit_verdict_is_defined():
    from report.brief import _EXIT_PILL
    for verdict in _EXIT_PILL:
        assert verdict.lower() in _words(), f"undefined exit verdict: {verdict}"


def test_every_exit_action_and_sell_cause_is_defined():
    from portfolio import exits as X
    for const in (X.HOLD, X.TRIM, X.EXIT, X.NO_STOP, X.CHECK_ENTRY,
                  X.DERISK, X.ROTATE):
        assert const.lower() in _words(), f"undefined action: {const}"


def test_every_stop_kind_is_defined():
    """`initial`, `break-even`, `trailing` -- each reads differently on a row."""
    for kind in ("initial", "break-even", "trailing"):
        assert f"{kind} stop" in _words(), f"undefined stop kind: {kind}"


def test_every_event_kind_and_source_is_defined():
    from market.events import _KIND_LABELS, _SOURCE_LABELS
    for label in list(_KIND_LABELS.values()) + list(_SOURCE_LABELS.values()):
        assert label.lower() in _words(), f"undefined event label: {label}"


def test_every_regime_label_is_defined():
    for label in ("RISK-ON", "RISK-OFF", "MIXED"):
        assert label.lower() in _words(), f"undefined regime: {label}"


def test_every_liquidity_label_is_defined():
    for label in ("ok", "thin"):
        assert label.lower() in _words(), f"undefined liquidity label: {label}"


def test_every_order_action_is_defined():
    for action in ("BUY", "SELL", "TRIM", "HOLD", "WAIT"):
        assert action.lower() in _words(), f"undefined order action: {action}"


# Hand-listed rather than scraped out of the rendered HTML. A completeness proof
# that reads its own output is one that passes by accident: drop the column and
# the assertion quietly has nothing left to check.
_ON_SCREEN = (
    "at risk", "stop", "the plan", "rank score", "worth vs peers", "liquidity",
    "paid in", "deploy %", "positions", "cash", "holdings", "lot",
    "stamp duty", "atr", "r", "cooldown", "score floor", "tied", "universe",
    "shortlist", "sector cap", "peer group", "measures disagree", "coverage",
    "realised", "unrealised", "index shadow", "ihsg",
)


@pytest.mark.parametrize("label", _ON_SCREEN)
def test_the_labels_a_reader_sees_are_defined(label):
    assert label in _words(), f"on screen but not in the Guide: {label}"


# ====================================================================== rendering
def test_the_guide_needs_nothing_from_today():
    """
    It is reference material. If it depended on the run it would be empty
    exactly when somebody opened it to find out why the page was empty.
    """
    out = guide.render_guide()
    assert len(out) > 5_000
    for heading in ("What this is", "Using it", "Every term"):
        assert heading in out


def test_every_term_reaches_the_page():
    out = guide.render_guide()
    for t in TERMS:
        assert t.word in out, f"{t.word} is in the table but not rendered"


def test_a_hard_term_carries_the_number_behind_it():
    """
    "Treat it as a hint" is advice; "more than 60% apart" is checkable. The
    entries that have a threshold must state it.
    """
    out = guide.render_guide()
    assert "60%" in out                    # measures disagree
    assert "100 shares" in out             # lot
    assert "Rp10,000" in out               # stamp duty


def test_the_guide_promises_nothing_it_does_not_do():
    out = guide.render_guide()
    for claim in ("does not predict", "does not trade", "no live feed"):
        assert claim in out.lower(), f"missing limitation: {claim}"


def test_the_glossary_says_it_is_checked():
    """The claim on the page and the tests in this file must not drift apart."""
    assert "test suite checks it" in guide.glossary_section()


# ------------------------------------------------------------------ in the page
def test_the_guide_is_reachable_from_the_rail():
    """
    The layout suite asserts every page has SOME rail entry. This asserts that
    the entry is this one -- deleting the page would otherwise leave the general
    invariant perfectly satisfied and the Guide simply gone.
    """
    from report.brief import render_brief
    from market.regime import Regime
    from portfolio.fees import FeeConfig, estimate_fees

    out = render_brief(
        regime=Regime([], 1.0, "RISK-ON", "G", ""), orders=[],
        fees=estimate_fees([], FeeConfig()), capital=10_000_000,
        holdings_rows=[], candidates=[], rejected={}, capped={},
        allocation=None, universe_n=0, imputed_n=0,
    )
    assert 'data-page="guide"' in out          # the rail button
    assert 'id="page-guide"' in out            # and the page it opens


def test_the_guide_renders_with_no_data_at_all():
    from market.regime import Regime, Signal
    from portfolio.fees import FeeConfig, estimate_fees
    from report.brief import render_brief

    out = render_brief(
        regime=Regime([Signal("IHSG trend", "^JKSE", True, "above")], 1.0,
                      "RISK-ON", "G", "Deploy 100%."),
        orders=[], fees=estimate_fees([], FeeConfig()), capital=10_000_000,
        holdings_rows=[], candidates=[], rejected={}, capped={},
        allocation=None, universe_n=0, imputed_n=0,
    )
    assert 'data-page="guide"' in out or 'id="page-guide"' in out
    assert "measures disagree" in out


def test_the_guide_css_ships_with_the_page():
    from report.brief import render_brief
    from market.regime import Regime
    from portfolio.fees import FeeConfig, estimate_fees

    out = render_brief(
        regime=Regime([], 1.0, "RISK-ON", "G", ""), orders=[],
        fees=estimate_fees([], FeeConfig()), capital=10_000_000,
        holdings_rows=[], candidates=[], rejected={}, capped={},
        allocation=None, universe_n=0, imputed_n=0,
    )
    css = out.split("<style>")[1].split("</style>")[0]
    assert ".gl-row{" in css, "the glossary renders unstyled"
    # And it must degrade on a narrow panel rather than squeezing a 15em term
    # column beside prose.
    assert re.search(r"@media \(max-width:1100px\)\{[^}]*\.gl-row", css, re.S) \
        or ".gl-row{grid-template-columns:minmax(0,1fr)" in css


def test_the_guide_scales_with_the_density_control():
    """
    Same rule the shell CSS is held to: no font-size nailed to a pixel, or the
    A A A buttons move the whole page except this one panel, which reads as the
    panel being broken rather than the control.
    """
    stuck = re.findall(r"font-size:\s*[\d.]+px", guide.GUIDE_CSS)
    assert stuck == [], f"not proportional: {stuck}"
