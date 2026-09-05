"""
Tests for the terminal shell.

The shell holds no data and decides nothing about a stock, so what is worth
guarding is structural: that a destination cannot become unreachable, that an empty
panel does not leave a frame around nothing, and that the one CSS rule the whole
layout rests on -- the page cannot scroll, panels can -- is actually present.
"""
import json
import re
import shutil
import subprocess

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

    # Rebuild and Re-run live here now, so "no buttons at all" is no longer the
    # property. The one that matters is that no control on the bar looks like it
    # sends an order.
    assert "<form" not in out
    for element in re.findall(r"<button[^>]*>(.*?)</button>", out, re.S):
        text = re.sub(r"<[^>]+>", "", element).strip().lower()
        assert text.split()[0] not in ("buy", "sell"), f"a top-bar button reads {text!r}"


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


# ----------------------------------------------------------------- density
def test_every_size_in_the_shell_is_proportional():
    """
    One control moves the whole page only if nothing is nailed to a pixel. A
    single `font-size:11px` left behind would sit unchanged while everything
    around it grew, which reads as a bug in the element, not in the control.

    Two exceptions. The `:root` rules are the base itself -- the one place a
    pixel belongs. The density buttons are a sample of the three sizes, so they
    have to show their own size rather than the current one.
    """
    css = re.sub(r":root(\[data-density=\"\w+\"\])?\{[^}]*\}", "", T.THEME_CSS)
    css = re.sub(r"\.density button\[data-density[^}]*\}", "", css)
    stuck = re.findall(r"font-size:\s*[\d.]+px", css)
    assert stuck == [], f"not proportional: {stuck}"


def test_the_root_carries_the_base_size_every_other_size_is_measured_from():
    assert re.search(r":root\{[^}]*font-size:[\d.]+px", T.THEME_CSS)


def test_each_density_is_a_different_size_and_normal_needs_no_override():
    sizes = dict(re.findall(r':root\[data-density="(\w+)"\]\{font-size:([\d.]+)px',
                            T.THEME_CSS))
    assert set(sizes) == {"compact", "large"}, sizes
    base = re.search(r":root\{[^}]*font-size:([\d.]+)px", T.THEME_CSS).group(1)
    assert float(sizes["compact"]) < float(base) < float(sizes["large"])


def test_the_document_stamps_the_density_on_the_html_element():
    for want in T.DENSITIES:
        out = T.document(title="T", head="markets", rail_html="", top_html="",
                         body_html="", tick_html="", css="", js="", density=want)
        assert f'<html lang="en" data-density="{want}">' in out


def test_an_unknown_density_falls_back_to_normal_not_to_nothing():
    """A stale user.yaml must not leave the page with no base size at all."""
    out = T.document(title="T", head="markets", rail_html="", top_html="",
                     body_html="", tick_html="", css="", js="", density="huge")
    assert '<html lang="en" data-density="normal">' in out


def test_the_density_control_offers_every_density_and_marks_the_current_one():
    bar = T.topbar("T", "now", [])
    for key in T.DENSITIES:
        assert f'data-density="{key}"' in bar
    assert 'id="density-controls"' in bar


def test_the_standalone_document_theme_restores_scrolling():
    """The backtest is a report you read top to bottom, not a terminal."""
    assert "html,body{overflow:auto;height:auto}" in T.DOC_CSS
    assert ".wrap{max-width:" in T.DOC_CSS


# ==========================================================================
# The shell script must actually parse.
# ==========================================================================
def _node() -> str | None:
    import shutil as _sh
    return _sh.which("node")


@pytest.mark.skipif(_node() is None, reason="node is not installed")
def test_the_shell_script_is_valid_javascript(tmp_path):
    """
    A syntax error in an inline <script> means the whole script never runs, and it
    cannot be caught by a try/catch inside that same script -- so every control on
    the page silently does nothing while the page itself looks fine.

    That shipped: a stray newline inside a JS string literal disabled the trade
    form, the refresh buttons and the settings editor at once, and the only symptom
    was buttons that did not respond. Three separate escaping slips produced this
    class of bug, so it is checked mechanically rather than by reading.
    """
    import subprocess

    script = tmp_path / "shell.js"
    script.write_text(T.SHELL_JS, encoding="utf-8")
    out = subprocess.run([_node(), "--check", str(script)],
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, f"SHELL_JS does not parse:\n{out.stderr}"


@pytest.mark.skipif(_node() is None, reason="node is not installed")
def test_the_script_in_a_rendered_page_is_valid_javascript(tmp_path):
    """
    The same check on the assembled document, since the page is where the script
    actually has to run and nothing else proves the two agree.
    """
    import subprocess

    out_html = T.document(title="T", head="markets", rail_html=T.rail(PAGES),
                          top_html=T.topbar("T", "now", []),
                          body_html=T.pages_html(PAGES), tick_html=T.tickerbar([]),
                          css=T.THEME_CSS, js=T.SHELL_JS)
    script = tmp_path / "page.js"
    script.write_text(out_html.split("<script>")[-1].split("</script>")[0],
                      encoding="utf-8")
    out = subprocess.run([_node(), "--check", str(script)],
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, f"the page's script does not parse:\n{out.stderr}"


def test_no_javascript_string_literal_spans_a_newline():
    """
    Cheap backstop for machines with no node. Every escaping slip so far turned a
    `\n` into a real newline inside a quoted string, which is a parse error.
    """
    for lineno, line in enumerate(T.SHELL_JS.splitlines(), start=1):
        code = line.split("//")[0]
        for quote in ('"', "'"):
            # Count quotes that are not backslash-escaped. An odd number means one
            # is still open when the line ends, which is a parse error.
            open_count = 0
            escaped = False
            for ch in code:
                if escaped:
                    escaped = False
                elif ch == "\\":
                    escaped = True
                elif ch == quote:
                    open_count += 1
            assert open_count % 2 == 0, (
                f"unterminated {quote} string on SHELL_JS line {lineno}: {line.strip()!r}"
            )


def test_every_bridge_method_the_script_calls_actually_exists():
    """
    A mistyped bridge name fails as a rejected promise with no message, which on
    screen is a button that does nothing at all -- no error, no log, no clue. The
    two sides are written in different languages and nothing else links them.
    """
    from api import TerminalAPI

    called = set(re.findall(r"API\.([A-Za-z_]\w*)\s*\(", T.SHELL_JS))
    assert called, "no bridge calls found - the regex has drifted"

    exposed = {n for n in dir(TerminalAPI) if not n.startswith("_")
               and callable(getattr(TerminalAPI, n))}
    assert called <= exposed, f"called but not exposed: {sorted(called - exposed)}"


# --------------------------------------------------- the script actually runs
# `node --check` proves SHELL_JS parses. It cannot see a name used outside the
# block it was declared in: `row` and `$` were both declared inside the trade
# form's handler and then used by the cash form, which parses perfectly and dies
# with a ReferenceError the moment somebody types in the box. Nothing on screen
# says so -- the preview simply never fills in.
#
# So the script is loaded against a stub DOM and every handler it registers is
# fired, which is the only way a scope error shows itself.
_DRIVER = r"""
const fs = require('fs');
const src = fs.readFileSync(process.argv[2], 'utf8');

const errors = [];
const handlers = [];

// Elements carrying a JSON payload only exist on pages that have that data, and
// the shell guards each with `if (raw)`. Returning a stub for them would hand
// JSON.parse an empty string, which is a fault in this harness rather than in the
// shell -- so they are absent, as they are on the Portfolio page.
const PAYLOAD_IDS = new Set(['trace-data', 'wi-data']);

function el(id){
  if (PAYLOAD_IDS.has(id)) return null;
  return {
    id: id, value: '', textContent: '', innerHTML: '', disabled: false,
    hidden: false, checked: true, dataset: {index:'0', ticker:'BBRI.JK',
      price:'4150', date:'2026-08-20', kind:'DEPOSIT', amount:'1000000'},
    style: {}, parentNode: null,
    addEventListener: (kind, fn) => handlers.push([id, kind, fn]),
    querySelector: () => null, querySelectorAll: () => [],
    appendChild: () => {}, closest: () => null, outerHTML: '',
  };
}

const reply = () => Promise.resolve({ok:true, message:'', data:{
  gross_rp:1, fee_rp:1, stamp_rp:0, net_rp:-1, action:'BUY', shares:100,
  price:4150, break_even:4203, break_even_move_pct:1.3,
  capital_now:0, capital_after:1000000, free_now:0, free_after:1000000,
  trades:[], entries:[], totals:{}, url:'', fields:[],
}});

const api = new Proxy({}, { get: () => reply });

global.window = {
  pywebview: {api: api}, addEventListener: (k, fn) => handlers.push(['window', k, fn]),
  confirm: () => false, sessionStorage: {getItem:()=>null, setItem:()=>{}},
  location: {replace: () => {}}, __idxShellRan: false,
};
global.sessionStorage = window.sessionStorage;
global.location = window.location;
global.document = {
  getElementById: el, querySelector: () => el('q'), querySelectorAll: () => [],
  createElement: el, addEventListener: (k, fn) => handlers.push(['document', k, fn]),
  body: el('body'), documentElement: el('html'),
};

try { eval(src); } catch (e) { errors.push('load: ' + e.message); }

// SHELL_JS wraps itself in try/catch and files the failure here rather than
// letting it escape, which is why a scope error is invisible from outside.
if (window.__idxError) { errors.push('shell: ' + window.__idxError); }
if (!window.__idxShellRan) { errors.push('the shell did not reach its last line'); }

// Fire everything the script registered. A handler that references a name from
// another scope throws here and nowhere else.
for (const [id, kind, fn] of handlers) {
  try {
    fn({target: el(id), preventDefault(){}, currentTarget: el(id)});
  } catch (e) {
    if (e instanceof ReferenceError || e instanceof TypeError) {
      errors.push(id + ' ' + kind + ': ' + e.message);
    }
  }
}

console.log(JSON.stringify(errors));
"""


def test_no_handler_references_a_name_from_another_scope(tmp_path):
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not available")

    script = tmp_path / "shell.js"
    script.write_text(T.SHELL_JS, encoding="utf-8")
    driver = tmp_path / "drive.js"
    driver.write_text(_DRIVER, encoding="utf-8")

    out = subprocess.run([node, str(driver), str(script)],
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, f"the driver itself failed:\n{out.stderr}"

    errors = json.loads(out.stdout.strip().splitlines()[-1])
    assert errors == [], (
        "a handler referenced something it cannot see:\n  " + "\n  ".join(errors))
