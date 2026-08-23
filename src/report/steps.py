# src/report/steps.py
"""
The Steps view: how 49 names became today's ticket.

Simple says what to do. Advanced says what the evidence is. Neither said how one
became the other -- and the biggest cut in the chain, 45 names down to 8, left no
trace at all, so "why isn't BBRI in my list?" had nowhere to be answered.

Everything here reads a `DecisionTrail` (src/analysis/trace.py) and renders it.
It computes nothing about a stock and re-applies no rule: if this file disagreed
with the pipeline, the reader would be told a confident lie, which is worse than
being told nothing. The trail is recorded by the gates themselves.

Same shape as advanced.py: pure functions returning strings, composed by
`render_steps`, wrapped in `.steps` for the mode toggle to show or hide.
"""
from __future__ import annotations

import html
import json
from typing import List, Optional

from analysis.trace import DROPPED, NOT_REACHED, PASSED
from report.layout import tabbed


def _e(v) -> str:
    return html.escape("" if v is None else str(v))


def funnel(trail) -> str:
    """
    The whole chain at a glance: one row per stage with a proportional bar.

    Plain CSS bars rather than SVG. A funnel is six rectangles; wiring it through
    a chart primitive would add indirection and buy nothing, and CSS widths inherit
    the theme variables for free.
    """
    rows = trail.funnel()
    if not rows:
        return ""

    out = ""
    for i, r in enumerate(rows, start=1):
        pct = max(1.0, r["width"] * 100)
        drop = (f'<span class="funnel-drop">&minus;{r["n_dropped"]}</span>'
                if r["n_dropped"] else '<span class="note">&nbsp;</span>')
        on = " on" if i == 1 else ""
        # The funnel is the tab strip. It already lists every stage with its
        # counts, so building a second row of tabs above it would be the same
        # navigation twice. Clicking a row opens that stage in place.
        out += (
            f'<button type="button" class="funnel-row tab{on}" role="tab" '
            f'data-panel="step-{_e(r["key"])}" '
            f'aria-selected="{"true" if i == 1 else "false"}" '
            f'aria-controls="step-{_e(r["key"])}">'
            f'<span class="funnel-n">{i}</span>'
            f'<span class="funnel-title">{_e(r["title"])}</span>'
            f'<span class="funnel-bar"><span style="width:{pct:.1f}%"></span></span>'
            f'<span class="funnel-out">{r["n_out"]}</span>'
            f"{drop}</button>"
        )
    return f'<div class="funnel tabs" role="tablist" data-group="steps">{out}</div>'


def _dropped_table(stage) -> str:
    if not stage.dropped:
        return '<p class="note">Nothing was dropped here.</p>'
    body = "".join(
        f'<tr><td><span class="tick">{_e(t)}</span></td>'
        f'<td><span class="note">{_e(reason)}</span></td></tr>'
        for t, reason in stage.dropped.items()
    )
    return ('<div class="scroll"><table><thead><tr><th>Ticker</th>'
            f"<th>Why it stopped here</th></tr></thead><tbody>{body}</tbody></table></div>")


def stage_card(stage, index: int, active: bool = False) -> str:
    """
    One gate, in full: the rule, the setting that controls it, the arithmetic, and
    every name it dropped with the reason the gate itself gave.

    The setting is named on purpose. A rule you cannot find is a rule you cannot
    change, and every one of these is a line in configs/default.yaml.
    """
    setting = (f'<div class="note">Set by <code>{_e(stage.setting)}</code></div>'
               if stage.setting else "")
    note = f'<p class="note">{_e(stage.note)}</p>' if stage.note else ""

    arithmetic = (
        f'<div class="step-count">'
        f'<strong>{stage.n_in}</strong> in '
        f'<span class="note">&rarr;</span> '
        f'<strong>{stage.n_out}</strong> through'
        + (f' <span class="funnel-drop">&minus;{stage.n_dropped}</span>'
           if stage.n_dropped else "")
        + "</div>"
    )

    on = " on" if active else ""
    return (
        f'<section id="step-{_e(stage.key)}" class="card step panel{on}" '
        f'role="tabpanel" aria-label="{_e(stage.title)}">'
        f'<div class="step-head"><span class="step-n">{index}</span>'
        f'<h3>{_e(stage.title)}</h3></div>'
        f'<p>{_e(stage.rule)}</p>{setting}{arithmetic}{note}'
        f"{_dropped_table(stage)}"
        f"</section>"
    )


def outcome_line(trail, orders: Optional[List[dict]] = None) -> str:
    """What the chain actually produced, so the last row is not just a number."""
    survivors = trail.survivors
    if not survivors:
        return ('<div class="callout">Nothing survived every stage today, so there '
                "is no buy. That is a result, not a failure &mdash; an empty ticket "
                "beats a forced one.</div>")

    bits = []
    for o in (orders or []):
        if o.get("action") == "BUY":
            bits.append(f'<strong>{_e(o["ticker"])}</strong> {o.get("lots")} lot')
    tail = ", ".join(bits) if bits else ", ".join(_e(t) for t in survivors)
    return (f'<div class="callout save"><strong>What came out:</strong> {tail}. '
            f"Every other name in the universe stopped at one of the stages above, "
            f"and you can look up exactly which one below.</div>")


def trace_search(trail) -> str:
    """
    Type a ticker, see its whole journey -- including the names that fell out first,
    which are the ones anybody actually looks up.

    The payload is a JSON script tag, same pattern as the what-if grid: it is data
    and must never be parsed as code, so `<` is escaped.
    """
    payload = trail.as_payload()
    if not payload.get("names"):
        return ""

    raw = json.dumps(payload, separators=(",", ":")).replace("<", "\\u003c")
    tickers = sorted(payload["names"])
    options = "".join(f"<option value=\"{_e(t)}\"></option>" for t in tickers)

    return (
        f'<script type="application/json" id="trace-data">{raw}</script>'
        '<div class="trace-box">'
        '<label for="trace-q">Look up any of the '
        f'{len(tickers)} names</label>'
        '<input id="trace-q" list="trace-list" autocomplete="off" '
        'placeholder="BBRI, WIKA, ITMG&hellip;">'
        f'<datalist id="trace-list">{options}</datalist>'
        "</div>"
        '<div id="trace-out"></div>'
    )


def render_steps(trail=None, orders: Optional[List[dict]] = None) -> str:
    """Compose the Steps block. Returns "" when there is no trail to show."""
    if trail is None or not getattr(trail, "stages", None):
        return ""

    cards = "".join(stage_card(s, i, active=(i == 1))
                    for i, s in enumerate(trail.stages, start=1))

    chain = (
        "<h2>How today's picks were chosen</h2>"
        '<div class="card">'
        "<p>Every stage below is recorded by the rule that ran it, not reconstructed "
        "afterwards, so this is what actually happened rather than a description of "
        "what should have. Pick a stage to see who it dropped and why.</p>"
        f"{funnel(trail)}"
        f"{outcome_line(trail, orders)}"
        "</div>"
        f"{cards}"
    )

    lookup = (
        "<h2>Why was a particular stock not suggested?</h2>"
        '<div class="card">'
        "<p class=\"note\">Every name in the universe is here, including the ones "
        "that stopped at the first gate.</p>"
        f"{trace_search(trail)}"
        "</div>"
    )

    # Two tabs at the top level: the chain, and the lookup box. Inside the chain the
    # funnel itself acts as the tab strip for the stage cards, so the stages are not
    # nested under a second row of tabs.
    return f'<div class="steps">{tabbed([("The chain", chain), ("Look up a stock", lookup)], "steps-top")}</div>'
