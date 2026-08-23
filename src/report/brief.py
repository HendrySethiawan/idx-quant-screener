# src/report/brief.py
"""
The one-page HTML brief.

Design rule: every number on this page is something the reader can act on before
13:00 -- a lot count, a rupiah figure, a date, or a one-line reason. Anything that
would only be interesting to a quant lives in the CSVs instead.

Self-contained: no external CSS, fonts, or scripts, so it opens from disk with no
network. Built with stdlib string formatting -- no template engine dependency.
"""
from __future__ import annotations

import html
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd

from report import terminal as T
from report.terminal import SHELL_JS, THEME_CSS

# Styling that belongs to content this module renders, not to the shell.
_EXTRA_CSS = """
.mkt-head{display:flex;align-items:baseline;gap:11px;flex-wrap:wrap;margin-bottom:4px}
.mkt-last{font-size:23px;font-weight:700;font-variant-numeric:tabular-nums;letter-spacing:-.02em}
.mkt-chg{font-size:12.5px;font-weight:700;font-variant-numeric:tabular-nums}
.mkt-chg.good{color:var(--good)} .mkt-chg.bad{color:var(--bad)}
.verdict{display:flex;align-items:center;gap:11px;flex-wrap:wrap;margin-bottom:6px}
.verdict .big{font-size:17px;font-weight:800;letter-spacing:-.01em}
.verdict .why{color:var(--muted);font-size:11.5px;flex:1;min-width:160px}
.setgrp{margin-bottom:14px}
.setgrp h3{font-size:12px;text-transform:uppercase;letter-spacing:.06em;color:var(--muted)}
"""


def _e(v) -> str:
    return html.escape("" if v is None else str(v))


def rp(v: Optional[float]) -> str:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return "-"
    return f"Rp{v:,.0f}"


def _pct(v: Optional[float], digits: int = 1) -> str:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return "-"
    return f"{v:+.{digits}f}%"


def _kpi(label: str, value: str) -> str:
    return f'<div class="kpi"><div class="k">{_e(label)}</div><div class="v">{_e(value)}</div></div>'


def _table(headers: List[str], rows: List[List[str]], num_cols: Optional[set] = None) -> str:
    if not rows:
        return '<div class="empty">Nothing here today.</div>'
    num_cols = num_cols or set()
    head = "".join(
        f'<th class="num">{_e(h)}</th>' if i in num_cols else f"<th>{_e(h)}</th>"
        for i, h in enumerate(headers)
    )
    body = ""
    for row in rows:
        cells = "".join(
            f'<td class="num">{c}</td>' if i in num_cols else f"<td>{c}</td>"
            for i, c in enumerate(row)
        )
        body += f"<tr>{cells}</tr>"
    return f'<div class="scroll"><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>'


# Peer-multiple verdict -> (pill class, what the reader sees).
_VALUE_PILL = {
    "undervalued": ("good", "below peers"),
    "fair": ("", "in line"),
    "overvalued": ("bad", "above peers"),
    "one_measure": ("", "one measure"),
    "unknown": ("", "cannot value"),
}


def _value_cell(item: dict) -> str:
    """
    The fair-price verdict, in one table cell.

    Shows the zone in rupiah rather than only a label, because "below peers" is an
    opinion and "peers imply Rp4,600-Rp5,100" is something you can check against
    the price in your broker. A wide zone is left wide on purpose: the gap between
    the earnings- and book-implied prices IS the uncertainty, and averaging it away
    would manufacture confidence.
    """
    verdict = str(item.get("value_verdict") or "unknown")
    cls, label = _VALUE_PILL.get(verdict, ("", verdict))
    out = f'<span class="pill {cls}">{_e(label)}</span>'

    lo, hi = item.get("value_zone_lo"), item.get("value_zone_hi")
    gap = item.get("value_gap_pct")

    if verdict in ("undervalued", "fair", "overvalued") and lo and hi:
        out += f'<br><span class="note">{rp(lo)} &ndash; {rp(hi)}</span>'
        if gap:
            direction = "below" if gap < 0 else "above"
            out += f'<br><span class="note">{abs(gap) * 100:.0f}% {direction}</span>'
    elif verdict == "one_measure" and lo:
        out += f'<br><span class="note">~{rp(lo)}, no range</span>'

    note = str(item.get("value_note") or "")
    if "disagree" in note:
        out += '<br><span class="pill warn">measures disagree</span>'
    elif verdict == "unknown" and note:
        out += f'<br><span class="note">{_e(note)}</span>'
    return out


def _valuation_caveat(items: List[dict]) -> str:
    """
    Two things that must sit next to the verdicts, not in a footnote.

    Both are ways the method can be confidently wrong, and a reader who does not
    know them will over-trust the label.
    """
    if not items:
        return ""
    rich = [i for i in items
            if i.get("value_verdict") == "overvalued" and (i.get("roe") or 0) > 0.20]
    quality = ""
    if rich:
        names = ", ".join(_e(i["ticker"]) for i in rich[:3])
        quality = (f" {names} earn well above average on equity, and a business that "
                   f"compounds faster than its peers <em>should</em> trade above their "
                   f"multiples &mdash; that is not automatically a sell.")
    return (
        '<div class="callout"><strong>What this can and cannot see.</strong> '
        "Every figure here is measured against other IDX names, so if the whole "
        "market is expensive everything still reads &ldquo;in line&rdquo;. It also "
        "takes no view on whether a premium is deserved."
        f"{quality}</div>"
    )


def _block(title: str, body: str, cls: str = "") -> str:
    """One heading-plus-card pair as a single grid child."""
    heading = f"<h2>{_e(title)}</h2>" if title else ""
    return f'<section class="{cls}">{heading}<div class="card">{body}</div></section>'


def _verdict_card(regime) -> str:
    cls = {"RISK-ON": "good", "RISK-OFF": "bad", "MIXED": "warn"}.get(regime.label, "warn")
    sig_html = ""
    for s in regime.signals:
        pill = "good" if s.risk_on else ("bad" if s.risk_on is False else "")
        sig_html += (
            f'<div style="margin-top:6px"><span class="pill {pill}">{_e(s.name)}</span> '
            f'<span class="note">{_e(s.detail)}</span></div>'
        )
    return f"""
<div class="card">
  <div class="verdict">
    <div class="big">{regime.emoji} {_e(regime.label)}</div>
    <div class="why">{_e(regime.headline)}</div>
    <div><span class="pill {cls}">deploy {regime.deploy_pct:.0%}</span></div>
  </div>
  {sig_html}
</div>"""


def _ticket_section(orders: List[dict], fees, capital: float) -> str:
    order_by = {"SELL": 0, "BUY": 1, "HOLD": 2}
    rows = []
    for o in sorted(orders, key=lambda x: order_by.get(x["action"], 9)):
        action = o["action"]
        cls = action.lower()
        lots = o.get("lots")
        detail = (
            f'{lots} lot ({o.get("shares", 0):,} shares) @ {rp(o.get("price"))}'
            if lots else "-"
        )
        why = f'<span class="note">{_e(o.get("note", ""))}</span>'
        # An UNKNOWN event state is shown as prominently as a KNOWN one. Rendering
        # "we have no data" quietly would let it read as "nothing is coming".
        state, ev_note = o.get("event_state"), o.get("event_note")
        if state == "known":
            why += f'<br><span class="pill warn">⚠ {_e(ev_note)}</span>'
        elif state == "unknown":
            why += f'<br><span class="note">— {_e(ev_note)}</span>'
        rows.append([
            f'<span class="act {cls}">{action}</span>',
            f'<span class="tick">{_e(o["ticker"])}</span>',
            detail,
            f'<span class="money">{rp(o.get("rupiah"))}</span>',
            why,
        ])

    table = _table(["Do", "Ticker", "How much", "Value", "Why"], rows, num_cols={3})

    fee_bits = (
        f'Estimated cost {rp(fees.total)} ({fees.pct_of(capital):.2f}% of capital) '
        f'— buy {rp(fees.buy_fee)}, sell {rp(fees.sell_fee)}, stamp {rp(fees.stamp_duty)}.'
    )
    callouts = f'<div class="callout">{fee_bits}</div>'
    for note in fees.notes:
        callouts += f'<div class="callout save"><strong>Fee tip.</strong> {_e(note)}</div>'
    return table + callouts


def _holdings_section(holdings_rows: List[dict]) -> str:
    rows = []
    for h in holdings_rows:
        flags = h.get("flags") or []
        flag_html = " ".join(f'<span class="pill warn">{_e(f)}</span>' for f in flags) or \
            '<span class="note">looks fine</span>'
        pnl = h.get("unrealized_pct")
        pnl_html = (
            f'<span style="color:var(--{"good" if (pnl or 0) >= 0 else "bad"})">{_pct(pnl)}</span>'
            if pnl is not None else '<span class="note">cost unknown</span>'
        )
        rows.append([
            f'<span class="tick">{_e(h["ticker"])}</span>',
            f'{h.get("lots", 0)} lot',
            f'<span class="money">{rp(h.get("value"))}</span>',
            pnl_html,
            _value_cell(h),
            flag_html,
        ])
    return _table(["Ticker", "Size", "Value now", "P&L", "Worth vs peers", "Health"],
                  rows, num_cols={2, 3})


def _candidates_section(cands: List[dict]) -> str:
    rows = []
    for c in cands:
        liq = c.get("liquidity_label", "ok")
        liq_pill = {"ok": "good", "thin": "warn", "illiquid": "bad"}.get(liq, "")
        warn = c.get("quality_note", "")
        why = _e(c.get("reason", ""))
        if warn:
            why += f'<br><span class="note">⚠ {_e(warn)}</span>'
        rows.append([
            f'<span class="tick">{_e(c["ticker"])}</span><br><span class="note">{_e(c.get("name",""))}</span>',
            _value_cell(c),
            f'<span class="money">{rp(c.get("lot_price"))}</span>',
            f'{c.get("score", 0):.2f}',
            f'<span class="pill {liq_pill}">{_e(liq)}</span>',
            why,
        ])
    # "Rank score", not "Score": it is min-max normalised across today's universe,
    # so the best name scores 1.00 whatever the market is doing. The Worth column
    # is the one that answers "is this cheap".
    return _table(["Stock", "Worth vs peers", "1 lot costs", "Rank score",
                   "Liquidity", "Why it ranks here"],
                  rows, num_cols={2, 3}) + _valuation_caveat(cands)


def _events_section(events, blind_n: int, universe_n: int, horizon: int) -> str:
    """Upcoming events, each labelled by how much it can be trusted."""
    rows = []
    for ev in events:
        rows.append([
            f'<span class="money">{_e(ev.date.strftime("%d %b"))}</span>',
            f'<span class="tick">{_e(ev.scope)}</span>',
            _e(ev.kind_label),
            f'<span class="note">{_e(ev.describe())}</span>',
            f'<span class="pill">{_e(ev.source_label)}</span>',
        ])

    table = _table(["Date", "What", "Kind", "When", "Source"], rows)
    coverage = ""
    if universe_n:
        coverage = (
            f'<div class="callout"><strong>Coverage.</strong> '
            f'{universe_n - blind_n} of {universe_n} names have an earnings date. '
            f'For the other {blind_n}, no source has one &mdash; an empty row above '
            f'means <em>we cannot see</em>, not <em>nothing is coming</em>. '
            f'Run <code>python main.py --events</code> for the full list, and add what '
            f'you find with <code>--event</code>.</div>'
        )
    return (f"<h2>Events in the next {horizon} days</h2>"
            f'<div class="card">{table}{coverage}</div>')


def _fold(summary: str, body: str) -> str:
    """A collapsed block. Content is present, just not taking up a screen."""
    return (f'<details class="fold"><summary>{_e(summary)}</summary>'
            f'<div class="card">{body}</div></details>')


def _rejected_section(rejected: Dict[str, str], capped: Optional[Dict[str, str]] = None,
                      compact: bool = False) -> str:
    """
    Two different things, kept apart on purpose.

    A liquidity or lot-size failure is a warning: the stock ranked well and you
    cannot safely act on it. A sector-cap exclusion is just diversification doing
    its job. Mixing them buries the first under a long list of the second.
    """
    out = ""
    if rejected:
        rows = [[f'<span class="tick">{_e(t)}</span>', f'<span class="note">{_e(r)}</span>']
                for t, r in rejected.items()]
        table = _table(["Ticker", "Reason"], rows)
        if compact:
            # Folded, not dropped. It is the one Simple section that repeats a Steps
            # stage, so it earns the least vertical space -- but a reader who wants
            # the list still gets it in one click.
            out += _fold(f"Skipped — {len(rejected)} you could not safely trade", table)
        else:
            out += ("<h2>Skipped &mdash; you could not safely trade these</h2>"
                    '<div class="card">' + table + "</div>")
    if capped:
        rows = [[f'<span class="tick">{_e(t)}</span>', f'<span class="note">{_e(r)}</span>']
                for t, r in capped.items()]
        intro = ('<p class="note">These ranked well but the sector cap already filled '
                 "their slots. Nothing is wrong with them.</p>")
        if compact:
            out += _fold(f"Held back for diversification — {len(capped)}",
                         intro + _table(["Ticker", "Reason"], rows))
        else:
            out += ("<h2>Held back for diversification</h2>"
                    '<div class="card">' + intro
                    + _table(["Ticker", "Reason"], rows) + "</div>")
    return out


def _market_panel(market):
    """
    The index, with its real last session.

    Open/High/Low come straight from the cached OHLCV frame, so they are the actual
    session -- not decoration. The chart is DAILY and says so: the reference this
    layout is modelled on shows an intraday line, and letting a daily series pass
    for one would imply a live feed this tool has never had.
    """
    if not market:
        return ""
    last, prev = market.get("last"), market.get("prev")
    if last is None:
        return ""

    delta = None if not prev else (last - prev)
    pct = None if not prev else delta / prev * 100
    cls = "" if delta is None else ("good" if delta >= 0 else "bad")
    arrow = "" if delta is None else ("&#9650;" if delta >= 0 else "&#9660;")

    head = '<div class="mkt-head"><span class="mkt-last">' + f"{last:,.2f}" + "</span>"
    if delta is not None:
        head += (f'<span class="mkt-chg {cls}">{arrow} {abs(delta):,.2f} '
                 f"({pct:+.2f}%)</span>")
    head += "</div>"

    stats = ""
    for key in ("open", "high", "low"):
        v = market.get(key)
        if v is not None:
            stats += (f'<div class="kpi"><div class="k">{key}</div>'
                      f'<div class="v">{v:,.2f}</div></div>')
    ma = market.get("ma_last")
    if ma:
        side = "above" if last >= ma else "below"
        stats += (f'<div class="kpi"><div class="k">{market.get("trend_ma", 200)}d mean</div>'
                  f'<div class="v">{ma:,.0f}</div>'
                  f'<div class="note">{side}</div></div>')

    return (head + market.get("chart", "")
            + f'<div class="kpis">{stats}</div>'
            + '<div class="note">Daily closes, not intraday &mdash; this tool has no '
              "live feed. The line is the index against its long-run mean, which is "
              "the whole trend signal.</div>")


def render_brief(
    *,
    regime,
    orders: List[dict],
    fees,
    capital: float,
    holdings_rows: List[dict],
    candidates: List[dict],
    rejected: Dict[str, str],
    capped: Optional[Dict[str, str]] = None,
    allocation=None,
    journal_html: str = "",
    events=None,
    blind_n: int = 0,
    event_horizon: int = 14,
    seasonality: str = "",
    universe_n: int = 0,
    imputed_n: int = 0,
    advanced_html: str = "",
    steps_html: str = "",
    settings_html: str = "",
    market: Optional[dict] = None,
    generated: Optional[datetime] = None,
) -> str:
    """
    The terminal. One document, five destinations, nothing scrolls but panels.

    The ticket is the first panel of the first page, in the DOM before anything
    else, and it keeps that place when it says HOLD or is empty. Ten panels of
    z-scores can make it feel as though something must be done today; the honest
    answer is usually that nothing must, and an empty ticket is a result.
    """
    when = (generated or datetime.now()).strftime("%a %d %b %Y, %H:%M")

    kpis = "".join([
        _kpi("Capital", rp(capital)),
        _kpi("Deploy", f"{regime.deploy_pct:.0%}"),
        _kpi("Positions", str(allocation.n_positions if allocation else 0)),
        _kpi("Cash left", rp(allocation.cash_left if allocation else capital)),
        _kpi("Est. fees", rp(fees.total)),
    ])

    granularity = ""
    if allocation and allocation.positions:
        granularity = (
            '<div class="callout"><strong>Lot rounding.</strong> IDX trades in '
            "100-share lots, so weights cannot land exactly on target. Largest gap "
            f"in this book: {allocation.max_weight_error * 100:.1f} percentage points.</div>"
        )

    disclaimer = (
        '<div class="callout"><strong>Before you trade.</strong> The universe is '
        "today's tickers, so companies that already failed or delisted are missing "
        f"and any implied history is flattered by that. {imputed_n} of {universe_n} "
        "names are missing at least one data point and were scored neutral on it. "
        "Prices come from Yahoo Finance and can be stale &mdash; check the live price "
        "in your broker before sending an order. Fees use Indopremier's schedule and "
        "are an estimate. A personal research tool, not investment advice.</div>"
    )

    # ---- Markets: the decision, and the ticket comes first --------------------
    markets = T.grid([
        T.column([
            T.panel("Do this today",
                    _ticket_section(orders, fees, capital) + granularity,
                    pid="panel-ticket", cls="print", grow=True),
            T.panel(f"Events, next {event_horizon} days",
                    _events_section(events or [], blind_n, universe_n, event_horizon)),
        ]),
        T.column([
            T.panel((market or {}).get("name", "IHSG") + " &middot; daily",
                    _market_panel(market)),
            T.panel("Regime and capital",
                    _verdict_card(regime) + f'<div class="kpis">{kpis}</div>'
                    + (f'<div class="callout">{_e(seasonality)}</div>' if seasonality else "")),
            T.panel("What you hold", _holdings_section(holdings_rows), grow=True),
        ]),
        T.column([
            T.panel("Best candidates you can afford",
                    _candidates_section(candidates), grow=True),
            T.panel("Skipped", _rejected_section(rejected, capped, compact=True)),
        ]),
    ])

    portfolio = T.grid([
        T.column([T.panel("What you hold", _holdings_section(holdings_rows), grow=True)]),
        T.column([T.panel("How you are doing",
                          journal_html or '<div class="empty">No trades logged yet. '
                          "Record one with <code>python main.py --log</code>.</div>",
                          grow=True)]),
    ])

    pages = [
        T.Page("markets", "Markets", "markets", markets, "Today's decision"),
        T.Page("portfolio", "Portfolio", "portfolio", portfolio, "What you own"),
    ]
    if advanced_html:
        pages.append(T.Page(
            "screener", "Screener", "screener",
            T.grid([T.column([T.panel("The whole universe", advanced_html, grow=True)])]),
            "All 49 names and the evidence"))
    if steps_html:
        pages.append(T.Page(
            "why", "Why", "why",
            T.grid([T.column([T.panel("How today's picks were chosen",
                                      steps_html, grow=True)])]),
            "The decision, stage by stage"))
    pages.append(T.Page(
        "settings", "Settings", "settings",
        T.grid([T.column([T.panel("What is driving the rules",
                                  (settings_html or "") + disclaimer, grow=True)])]),
        "The numbers behind every gate"))

    regime_kind = {"RISK-ON": "good", "RISK-OFF": "bad"}.get(regime.label, "warn")
    top = T.topbar(
        "IDX Terminal",
        f"as of {when} · {universe_n} names screened",
        [
            (regime.label, f"deploy {regime.deploy_pct:.0%}", regime_kind),
            ("Cash", rp(allocation.cash_left if allocation else capital), ""),
            ("Est. fees", rp(fees.total), ""),
        ],
    )

    ticks = []
    for c in candidates[:12]:
        gap = c.get("value_gap_pct")
        # Negated on purpose: a price BELOW the peer range is the good direction, and
        # a green arrow next to "-71%" would read backwards.
        ticks.append((c.get("ticker", ""), rp(c.get("price")),
                      None if gap is None else -float(gap)))

    return T.document(
        title="IDX Terminal",
        head="markets",
        rail_html=T.rail(pages, "markets"),
        top_html=top,
        body_html=T.pages_html(pages, "markets"),
        tick_html=T.tickerbar(ticks),
        css=THEME_CSS + _EXTRA_CSS,
        js=SHELL_JS,
    )


def write_brief(html_text: str, output_dir: Path, filename: str = "brief.html") -> Path:
    out = Path(output_dir) / filename
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html_text, encoding="utf-8")
    return out
