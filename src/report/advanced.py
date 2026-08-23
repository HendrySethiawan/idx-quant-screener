# src/report/advanced.py
"""
The Advanced half of the brief.

Almost nothing here is a new calculation. Every number below was already computed
on each run and then dropped into a CSV nobody opens at 12:30, or thrown away
entirely -- the 12-month seasonality table survived only as one sentence about the
current month, and the six matplotlib panels were written to a 896KB PNG that no
page ever linked to. This module gives that material a view.

Structure mirrors `journal_view.brief_section`: every function returns an HTML
string and `render_advanced` composes them. `__main__` passes the result into
`render_brief(advanced_html=...)`, which keeps the import arrow pointing one way
(advanced -> brief) and `render_brief` a pure formatter.

Whether any of this is *visible* is a CSS question, not a Python one -- the whole
block is wrapped in `.adv` and `body[data-mode]` decides. There is no second file
and no second render path, so Simple and Advanced can never disagree.
"""
from __future__ import annotations

import html
import json
import math
from typing import Dict, List, Optional, Sequence, Tuple

import pandas as pd

from portfolio.fees import FeeConfig, estimate_fees
from portfolio.sizing import choose_allocation
from report import charts
from report.brief import rp
from report.layout import tabbed

# The z-columns, in the order the README documents the factor groups.
FACTOR_ORDER = [
    "pe_ratio", "price_to_book", "dividend_yield",
    "roe", "gross_margin", "debt_to_equity",
    "realized_vol", "mom_1m", "mom_6m", "mom_12m",
]

SHORT_NAME = {
    "pe_ratio": "P/E", "price_to_book": "P/B", "dividend_yield": "Div",
    "roe": "ROE", "gross_margin": "Margin", "debt_to_equity": "D/E",
    "realized_vol": "Vol", "mom_1m": "1m", "mom_6m": "6m", "mom_12m": "12m",
}


def _e(v) -> str:
    return html.escape("" if v is None else str(v))


def _num(v) -> Optional[float]:
    """A float, or None for anything pandas might hand back that is not one."""
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _card(title: str, body: str, note: str = "") -> str:
    note_html = f'<p class="note">{note}</p>' if note else ""
    return f'<h2>{_e(title)}</h2><div class="card">{note_html}{body}</div>'


# --------------------------------------------------------------------------
# 1. The full ranked universe
# --------------------------------------------------------------------------

def universe_table(df: pd.DataFrame) -> str:
    """
    Every screened name with every z-score, click-to-sort.

    `assemble()` truncates candidates to `top_picks_n` because the ticket only
    needs the top of the list. This reads `df` directly instead -- the point of
    Advanced is the other 41 names, including the ones that scored badly.
    """
    if df is None or df.empty:
        return '<div class="empty">No screen results.</div>'

    z_cols = [c for c in (f"z_{f}" for f in FACTOR_ORDER) if c in df.columns]
    heads = ["#", "Ticker", "Sector", "Score", "Price", "1 lot", "Liquidity/day"]
    heads += [SHORT_NAME.get(c[2:], c[2:]) for c in z_cols]
    heads.append("Gaps")

    ordered = df.sort_values("composite_score", ascending=False, na_position="last")

    body = ""
    for rank, (_, r) in enumerate(ordered.iterrows(), start=1):
        price = _num(r.get("last_close"))
        lot = price * 100 if price is not None else None
        liq = _num(r.get("median_daily_value_rp"))
        score = _num(r.get("composite_score"))
        gaps = str(r.get("imputed_factors") or "")

        cells = [
            (str(rank), rank),
            (f'<span class="tick">{_e(r.get("ticker"))}</span>'
             f'<br><span class="note">{_e(r.get("name", ""))}</span>', str(r.get("ticker"))),
            (_e(r.get("sector", "-")), str(r.get("sector", ""))),
            (f"{score:.2f}" if score is not None else "-", score),
            (rp(price), price),
            (rp(lot), lot),
            (rp(liq), liq),
        ]
        for c in z_cols:
            z = _num(r.get(c))
            shade = "" if z is None else (
                ' style="color:var(--good)"' if z > 0.8 else
                (' style="color:var(--bad)"' if z < -0.8 else "")
            )
            cells.append((f"<span{shade}>{z:+.2f}</span>" if z is not None else "-", z))
        cells.append(
            (f'<span class="pill warn">{_e(gaps)}</span>' if gaps
             else '<span class="note">-</span>', len(gaps))
        )

        tds = ""
        for i, (rendered, sort_val) in enumerate(cells):
            cls = "num" if i not in (1, 2, 7 + len(z_cols)) else ""
            sv = "" if sort_val is None else _e(sort_val)
            tds += f'<td class="{cls}" data-v="{sv}">{rendered}</td>'
        body += f"<tr>{tds}</tr>"

    ths = "".join(
        f'<th class="sortable {"num" if i not in (1, 2) else ""}" data-col="{i}">{_e(h)}</th>'
        for i, h in enumerate(heads)
    )
    return (f'<div class="scroll"><table class="sortable-table"><thead><tr>{ths}</tr>'
            f"</thead><tbody>{body}</tbody></table></div>")


# --------------------------------------------------------------------------
# 2. Why a stock scored what it scored
# --------------------------------------------------------------------------

def score_breakdown(df: pd.DataFrame, factor_weights: Dict[str, float],
                    tickers: Sequence[str]) -> str:
    """
    Signed factor contributions per name: weight x z, largest magnitude first.

    This is the audit trail for the ranking. The bars sum to `raw_score` by
    construction -- which is exactly what the test asserts, so the chart cannot
    drift away from the scorer without a test failing.
    """
    if df is None or df.empty or not factor_weights:
        return '<div class="empty">No scores to break down.</div>'

    indexed = df.set_index("ticker") if "ticker" in df.columns else df
    out = ""
    for ticker in tickers:
        if ticker not in indexed.index:
            continue
        row = indexed.loc[ticker]
        if isinstance(row, pd.DataFrame):
            row = row.iloc[0]

        items: List[Tuple[str, float]] = []
        for factor, weight in factor_weights.items():
            z = _num(row.get(f"z_{factor}"))
            if z is None:
                continue
            items.append((SHORT_NAME.get(factor, factor), float(weight) * z))
        if not items:
            continue

        total = _num(row.get("raw_score"))
        gaps = str(row.get("imputed_factors") or "")
        caption = f"Total {total:+.2f}" if total is not None else ""
        if gaps:
            caption += f' &middot; <span class="pill warn">scored neutral on {_e(gaps)}</span>'

        out += (
            f'<div style="margin-bottom:18px">'
            f'<div><span class="tick">{_e(ticker)}</span> '
            f'<span class="note">{_e(row.get("name", ""))}</span> &middot; {caption}</div>'
            f"{charts.diverging_bars(items, label=f'{ticker} factor contributions')}"
            f"</div>"
        )
    return out or '<div class="empty">No scores to break down.</div>'


# --------------------------------------------------------------------------
# 2b. What is it worth
# --------------------------------------------------------------------------

_VERDICT_ORDER = {"undervalued": 0, "fair": 1, "overvalued": 2,
                  "one_measure": 3, "unknown": 4}


def valuation_section(df: pd.DataFrame) -> str:
    """
    The working for every fair-price verdict the Simple view shows.

    Simple gives the answer; this gives the arithmetic, so a verdict can be
    disagreed with rather than just believed. Every input is on the row: the
    earnings and book value per share that were derived, the peer multiple applied,
    which peer group that was, and both resulting prices.
    """
    if df is None or df.empty or "value_verdict" not in df.columns:
        return ""

    from analysis.valuation import coverage

    ordered = df.copy()
    ordered["_o"] = ordered["value_verdict"].map(_VERDICT_ORDER).fillna(9)
    ordered = ordered.sort_values(["_o", "value_gap_pct"], na_position="last")

    rows = ""
    for _, r in ordered.iterrows():
        verdict = str(r.get("value_verdict") or "unknown")
        cls = {"undervalued": "good", "overvalued": "bad"}.get(verdict, "")
        gap = _num(r.get("value_gap_pct"))
        lo, hi = _num(r.get("value_zone_lo")), _num(r.get("value_zone_hi"))
        roe = _num(r.get("roe"))
        note = str(r.get("value_note") or "")

        zone = "-"
        if lo and hi and verdict != "unknown":
            zone = rp(lo) if lo == hi else f"{rp(lo)} &ndash; {rp(hi)}"

        flag = ""
        if "disagree" in note:
            flag = '<br><span class="pill warn">measures disagree</span>'
        elif verdict == "unknown" and note:
            flag = f'<br><span class="note">{_e(note)}</span>'

        rows += (
            f'<tr><td><span class="tick">{_e(r.get("ticker"))}</span></td>'
            f'<td><span class="pill {cls}">{_e(verdict.replace("_", " "))}</span>{flag}</td>'
            f'<td class="num">{rp(_num(r.get("last_close")))}</td>'
            f'<td class="num">{zone}</td>'
            f'<td class="num">{f"{gap * 100:+.0f}%" if gap is not None else "-"}</td>'
            f'<td class="num">{rp(_num(r.get("value_eps")))}</td>'
            f'<td class="num">{rp(_num(r.get("value_bvps")))}</td>'
            f'<td class="num">{_fmt_x(r.get("value_peer_pe"))}</td>'
            f'<td class="num">{_fmt_x(r.get("value_peer_pb"))}</td>'
            f'<td><span class="note">{_e(r.get("value_peer_group", "universe"))}</span></td>'
            f'<td class="num">{f"{roe * 100:.0f}%" if roe is not None else "-"}</td></tr>'
        )

    table = (
        '<div class="scroll"><table><thead><tr>'
        "<th>Ticker</th><th>Verdict</th><th class=\"num\">Price</th>"
        "<th class=\"num\">Peers imply</th><th class=\"num\">Gap</th>"
        "<th class=\"num\">EPS</th><th class=\"num\">Book/share</th>"
        "<th class=\"num\">Peer P/E</th><th class=\"num\">Peer P/B</th>"
        "<th>Peer group</th><th class=\"num\">ROE</th>"
        f"</tr></thead><tbody>{rows}</tbody></table></div>"
    )

    c = coverage(df)
    summary = (
        f'<div class="kpis">'
        f'<div class="kpi"><div class="k">Below peers</div><div class="v">{c.get("undervalued", 0)}</div></div>'
        f'<div class="kpi"><div class="k">In line</div><div class="v">{c.get("fair", 0)}</div></div>'
        f'<div class="kpi"><div class="k">Above peers</div><div class="v">{c.get("overvalued", 0)}</div></div>'
        f'<div class="kpi"><div class="k">One measure</div><div class="v">{c.get("one_measure", 0)}</div></div>'
        f'<div class="kpi"><div class="k">Cannot value</div><div class="v">{c.get("unknown", 0)}</div></div>'
        f"</div>"
    )

    gaps = [(str(r["ticker"]), float(r["value_gap_pct"]) * 100)
            for _, r in ordered.iterrows()
            if _num(r.get("value_gap_pct")) not in (None, 0.0)
            and str(r.get("value_verdict")) in ("undervalued", "overvalued")]
    chart = ""
    if gaps:
        chart = charts.diverging_bars(
            gaps[:24], label="distance from the peer-implied range",
            value_format=lambda v: f"{v:+.0f}%",
        )

    method = (
        '<div class="callout"><strong>How this is worked out.</strong> '
        "Earnings per share is price divided by P/E; book value per share is price "
        "divided by P/B. Each is multiplied by the median multiple of the peer group "
        "to give a price. The two answers bracket the range &mdash; there is no "
        "&ldquo;within 15% is fair&rdquo; rule, because the gap between the two "
        "measures already is the uncertainty."
        "</div>"
        '<div class="callout"><strong>Where the inputs come from.</strong> '
        "The multiples used here are the pre-winsorization values. The ranking uses "
        "clipped ones on purpose &mdash; that is what stops one bad P/B flattening a "
        "whole factor &mdash; but clipping collapses every outlier onto the same "
        "number, and on a real run six unrelated tickers shared a P/E of 50.738811. "
        "Deriving earnings from that would have invented them."
        "</div>"
    )

    note = ("Two independent estimates per name, from peer multiples. This is the only "
            "part of the tool that answers what a stock is worth; the rank score answers "
            "where it sits in today's list, which is a different question.")
    return _card("What is it worth?", summary + chart + table + method, note)


def _fmt_x(v) -> str:
    f = _num(v)
    return "-" if f is None else f"{f:.1f}x"


# --------------------------------------------------------------------------
# 3. Factor correlations
# --------------------------------------------------------------------------

def correlation_section(corr: Optional[pd.DataFrame]) -> str:
    if corr is None or getattr(corr, "empty", True):
        return ""
    labels = [SHORT_NAME.get(c, c) for c in corr.columns]
    matrix = [[_num(v) for v in corr.iloc[i].tolist()] for i in range(len(corr))]

    # Name the offenders rather than making the reader scan the grid.
    hot: List[str] = []
    cols = list(corr.columns)
    for i in range(len(cols)):
        for j in range(i + 1, len(cols)):
            v = _num(corr.iloc[i, j])
            if v is not None and abs(v) >= 0.7:
                hot.append(f"{SHORT_NAME.get(cols[i], cols[i])}/"
                           f"{SHORT_NAME.get(cols[j], cols[j])} {v:+.2f}")

    note = ("Two factors that move together are one factor counted twice, and the "
            "score is a plain weighted sum, so it cannot tell the difference.")
    body = charts.heatmap(matrix, labels, label="factor correlation matrix")
    if hot:
        body += (f'<div class="callout"><strong>Overlapping:</strong> {_e(", ".join(hot))}. '
                 f"Their combined weight is effectively larger than the config says.</div>")
    else:
        body += ('<div class="callout">No pair is above |0.70| today, so the weights in '
                 "<code>configs/default.yaml</code> are close to what they look like.</div>")
    return _card("Are the factors independent?", body, note)


# --------------------------------------------------------------------------
# 4. The regime, on a chart
# --------------------------------------------------------------------------

def regime_chart(benchmark: Optional[pd.Series], trend_ma: int = 200,
                 name: str = "IHSG") -> str:
    """
    The README says the regime signal is "deliberately simple enough to verify on
    a chart". Until now the brief gave no chart. This is that chart.
    """
    if benchmark is None or len(benchmark) < 2:
        return ""
    series = pd.Series(benchmark).dropna()
    if series.empty:
        return ""

    ma = series.rolling(trend_ma, min_periods=max(2, trend_ma // 4)).mean()
    labels = [d.strftime("%b %y") if hasattr(d, "strftime") else str(d) for d in series.index]

    body = charts.line_chart(
        [(name, series.tolist()), (f"{trend_ma}-day mean", ma.tolist())],
        x_labels=labels,
        label=f"{name} versus its {trend_ma}-day mean",
        y_format=lambda v: f"{v:,.0f}",
    )
    last, last_ma = _num(series.iloc[-1]), _num(ma.iloc[-1])
    if last is not None and last_ma is not None:
        side = "above" if last >= last_ma else "below"
        gap = (last / last_ma - 1) * 100
        body += (f'<div class="callout">{_e(name)} closed at {last:,.0f}, '
                 f"{abs(gap):.1f}% {side} its {trend_ma}-day mean. That comparison is "
                 f"the whole trend signal &mdash; check it against your broker's chart.</div>")
    return _card("The regime signal, drawn", body,
                 "Two moving averages. Not a forecast, and deliberately simple enough "
                 "that you can confirm it by eye.")


# --------------------------------------------------------------------------
# 5. Seasonality, all twelve months
# --------------------------------------------------------------------------

def seasonality_section(table: Optional[pd.DataFrame], current_month: Optional[int] = None) -> str:
    """
    The full base rate, not just this month's line.

    Months below `MIN_OBSERVATIONS` are greyed rather than dropped: an omitted bar
    reads as "no effect", which is a stronger claim than "we do not know".
    """
    if table is None or getattr(table, "empty", True):
        return ""
    from market.seasonality import MIN_OBSERVATIONS

    labels = [str(r.get("month_name", ""))[:3] for _, r in table.iterrows()]
    values = [_num(r.get("median_pct")) for _, r in table.iterrows()]
    counts = [int(_num(r.get("n")) or 0) for _, r in table.iterrows()]
    thin = [c < MIN_OBSERVATIONS for c in counts]

    body = charts.bar_chart(
        labels, values,
        annotations=[f"n={c}" for c in counts],
        muted=thin,
        label="median IHSG return by calendar month",
        y_format=lambda v: f"{v:+.1f}%",
    )
    hit = ""
    if current_month:
        row = table[table["month"] == current_month]
        if not row.empty:
            r = row.iloc[0]
            n = int(_num(r.get("n")) or 0)
            hit = (f'<div class="callout"><strong>This month.</strong> '
                   f'{_e(r.get("month_name"))}: median {_num(r.get("median_pct")) or 0:+.1f}%, '
                   f'positive in {(_num(r.get("hit_rate")) or 0) * 100:.0f}% of {n} years.'
                   + ("" if n >= MIN_OBSERVATIONS else " Too few years to lean on.") + "</div>")

    greyed = sum(thin)
    note = (f"Median monthly return of the index over its full history. "
            f"{greyed} of 12 months have fewer than {MIN_OBSERVATIONS} observations and are "
            f"greyed out. Even a full bar is roughly 37 data points &mdash; context, not a rule.")
    return _card("Seasonality by month", body + hit, note)


# --------------------------------------------------------------------------
# 6. Equity curve
# --------------------------------------------------------------------------

def equity_section(marks: Optional[pd.DataFrame]) -> str:
    """
    Total wealth over time, from `data/journal_marks.csv`.

    Deliberately NOT labelled as the cash-flow-matched shadow. The marks file
    stores `ihsg_close`, an index level, so the only comparison available here is
    a rebase from the first snapshot -- which flatters or punishes you depending
    on money added after that date. The honest shadow, which matches every deposit
    day by day, is what `--journal` computes and what the Simple half reports; this
    chart is the shape over time, not the verdict.
    """
    if marks is None or getattr(marks, "empty", True) or len(marks) < 2:
        return ""
    if not {"date", "total_rp"} <= set(marks.columns):
        return ""

    frame = marks.copy()
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    frame = frame.dropna(subset=["date"]).sort_values("date")
    totals = [_num(v) for v in frame["total_rp"]]
    if len(frame) < 2 or not any(t is not None for t in totals):
        return ""

    series: List[Tuple[str, List[Optional[float]]]] = [("Your total wealth", totals)]

    caveat = ""
    if "ihsg_close" in frame.columns:
        closes = [_num(v) for v in frame["ihsg_close"]]
        base_idx = next((i for i, c in enumerate(closes) if c), None)
        start = next((t for t in totals if t is not None), None)
        if base_idx is not None and start:
            base = closes[base_idx]
            series.append((
                "IHSG, rebased to your first mark",
                [None if c is None else start * c / base for c in closes],
            ))
            caveat = ('<div class="callout">The index line is rebased to your first '
                      "snapshot, so money you added later makes your line rise for a "
                      "reason the index line cannot. For the comparison that handles "
                      "deposits properly, read the cash-flow-matched figure above or run "
                      "<code>python main.py --journal</code>.</div>")

    body = charts.line_chart(
        series,
        x_labels=[d.strftime("%d %b") for d in frame["date"]],
        label="portfolio value over time",
        y_format=lambda v: f"{v / 1_000_000:.1f}jt",
    )
    return _card("Your wealth over time", body + caveat,
                 f"{len(frame)} snapshots from <code>python main.py --mark</code>.")


# --------------------------------------------------------------------------
# 7. Data quality
# --------------------------------------------------------------------------

def data_quality_section(df: pd.DataFrame) -> str:
    """Which names were scored neutral on what, and how often each factor is missing."""
    if df is None or df.empty or "imputed_factors" not in df.columns:
        return ""

    gaps = df["imputed_factors"].fillna("").astype(str)
    affected = df[gaps.str.len() > 0]

    tally: Dict[str, int] = {}
    for entry in gaps:
        for factor in (f.strip() for f in entry.split(",") if f.strip()):
            tally[factor] = tally.get(factor, 0) + 1

    rows = ""
    for factor, count in sorted(tally.items(), key=lambda kv: -kv[1]):
        pct = count / len(df) * 100
        rows += (f'<tr><td>{_e(SHORT_NAME.get(factor, factor))}</td>'
                 f'<td class="num">{count}</td>'
                 f'<td class="num">{pct:.0f}%</td></tr>')
    tally_table = (
        '<div class="scroll"><table><thead><tr><th>Factor</th>'
        '<th class="num">Missing for</th><th class="num">Share of universe</th>'
        f"</tr></thead><tbody>{rows}</tbody></table></div>"
        if rows else '<div class="empty">Every factor is present for every name.</div>'
    )

    names = ""
    if not affected.empty:
        listed = ", ".join(
            f'{_e(r["ticker"])} ({_e(r["imputed_factors"])})'
            for _, r in affected.sort_values("composite_score", ascending=False).head(20).iterrows()
        )
        more = len(affected) - min(20, len(affected))
        names = (f'<div class="callout"><strong>{len(affected)} of {len(df)} names</strong> '
                 f"are missing at least one factor: {listed}"
                 + (f" and {more} more." if more > 0 else ".") + "</div>")

    note = ("A missing factor is scored neutral (z = 0), never dropped. That is the safe "
            "direction &mdash; dropping the stock would silently bias the universe toward "
            "whichever companies Yahoo happens to cover well &mdash; but it does mean a name "
            "with several gaps is being ranked on less evidence than its neighbours.")
    return _card("Data quality: what is missing", tally_table + names, note)


# --------------------------------------------------------------------------
# 8. Sector exposure
# --------------------------------------------------------------------------

def sector_section(df: pd.DataFrame, allocation, max_per_sector: Optional[int]) -> str:
    if allocation is None or not getattr(allocation, "positions", None):
        return ""
    if df is None or df.empty or "sector" not in df.columns:
        return ""

    sector_of = dict(zip(df["ticker"], df["sector"]))
    by_sector: Dict[str, float] = {}
    counts: Dict[str, int] = {}
    for p in allocation.positions:
        s = str(sector_of.get(p.ticker, "Unknown"))
        by_sector[s] = by_sector.get(s, 0.0) + float(p.rupiah)
        counts[s] = counts.get(s, 0) + 1

    invested = sum(by_sector.values()) or 1.0
    rows = ""
    for s, value in sorted(by_sector.items(), key=lambda kv: -kv[1]):
        at_cap = max_per_sector and counts[s] >= int(max_per_sector)
        flag = ('<span class="pill warn">at cap</span>' if at_cap
                else '<span class="note">-</span>')
        rows += (f"<tr><td>{_e(s)}</td><td class=\"num\">{counts[s]}</td>"
                 f'<td class="num">{rp(value)}</td>'
                 f'<td class="num">{value / invested * 100:.0f}%</td>'
                 f"<td>{flag}</td></tr>")

    table = ('<div class="scroll"><table><thead><tr><th>Sector</th><th class="num">Names</th>'
             '<th class="num">Value</th><th class="num">Share</th><th>Cap</th>'
             f"</tr></thead><tbody>{rows}</tbody></table></div>")
    note = (f"The cap is {max_per_sector} names per sector. It binds before diversification "
            f"feels comfortable at this account size: {len(allocation.positions)} positions "
            f"cannot spread across many sectors.") if max_per_sector else ""
    return _card("Where the money actually sits", table, note)


# --------------------------------------------------------------------------
# 9. What-if -- a precomputed decision surface, no server required
# --------------------------------------------------------------------------

def whatif_grid(candidates: Sequence[dict], settings, capital: float,
                deploy_pct: float) -> dict:
    """
    Run the real sizer across a grid of capital / position-count / deploy settings.

    `choose_allocation` is pure and cheap, so the entire surface a slider could
    ever reach is computable up front. That is what lets the "what if" panel be a
    client-side lookup in a static file instead of a reason to run a web server.

    The payload builder lives next to its renderer on purpose: the test that the
    embedded numbers match a direct `choose_allocation` call is the only thing
    keeping this honest, and it should be obvious where to find it.

    Fees assume the book is built from cash (all buys). Against a book you already
    hold the real ticket is smaller, so this is an upper bound -- stated in the UI.
    """
    capital = float(capital or 0.0)
    if capital <= 0 or not candidates:
        return {}

    cfg = FeeConfig.from_settings(settings)
    account = getattr(settings, "account", None) or {}
    lo_n = int(account.get("min_positions", 3))
    hi_n = int(account.get("max_positions", 6))

    capitals = sorted({
        max(500_000.0, round(capital * m / 100_000) * 100_000)
        for m in (0.5, 0.75, 1.0, 1.5, 2.0, 3.0)
    })
    deploys = sorted({round(float(deploy_pct), 4), 1.0})
    counts = list(range(lo_n, hi_n + 1))

    cells: Dict[str, dict] = {}
    for ci, cap in enumerate(capitals):
        for n in counts:
            for di, dep in enumerate(deploys):
                alloc = choose_allocation(
                    candidates, cap, dep, settings=settings,
                    min_positions=n, max_positions=n,
                )
                orders = [{"action": "BUY", "rupiah": p.rupiah} for p in alloc.positions]
                fees = estimate_fees(orders, cfg, cap, sell_days=1)

                # Asking for N does not guarantee N. At this account size a name
                # whose single lot costs more than its slot is dropped, so the book
                # comes back smaller. Silently showing 3 when the reader picked 4
                # would hide the exact constraint this tool exists to surface.
                short = ""
                if alloc.n_positions < n:
                    reasons = list(alloc.rejected.values())[:2]
                    short = (f"You asked for {n}; only {alloc.n_positions} fit. "
                             + (" ".join(reasons) if reasons else
                                "The remaining names cost more per lot than a slot allows."))

                cells[f"{ci}|{n}|{di}"] = {
                    "n": alloc.n_positions,
                    "req": n,
                    "deployed": round(alloc.deployed_pct, 4),
                    "cash": round(alloc.cash_left),
                    "err": round(alloc.max_weight_error, 4),
                    "fees": round(fees.total),
                    "short": short,
                    "pos": [{"t": p.ticker, "l": p.lots, "r": round(p.rupiah)}
                            for p in alloc.positions],
                }

    return {
        "capitals": [int(c) for c in capitals],
        "counts": counts,
        "deploys": [round(d, 4) for d in deploys],
        "cells": cells,
    }


def whatif_section(grid: Optional[dict], capital: float, deploy_pct: float) -> str:
    if not grid or not grid.get("cells"):
        return ""

    caps = grid["capitals"]
    counts = grid["counts"]
    deploys = grid["deploys"]
    cap_i = min(range(len(caps)), key=lambda i: abs(caps[i] - float(capital)))
    dep_i = min(range(len(deploys)), key=lambda i: abs(deploys[i] - float(deploy_pct)))

    # A JSON script tag, not inline JS: the payload is data and the browser must
    # never parse it as code. "<" is escaped so no value can close the tag.
    payload = json.dumps(grid, separators=(",", ":")).replace("<", "\\u003c")

    cap_opts = "".join(
        f'<option value="{i}"{" selected" if i == cap_i else ""}>{rp(c)}</option>'
        for i, c in enumerate(caps)
    )
    n_opts = "".join(
        f'<option value="{n}"{" selected" if n == counts[0] else ""}>{n} positions</option>'
        for n in counts
    )
    dep_opts = "".join(
        f'<option value="{i}"{" selected" if i == dep_i else ""}>deploy {d:.0%}</option>'
        for i, d in enumerate(deploys)
    )

    controls = (
        '<div class="whatif-controls">'
        f'<label>Capital <select id="wi-cap">{cap_opts}</select></label>'
        f'<label>Positions <select id="wi-n">{n_opts}</select></label>'
        f'<label>Regime <select id="wi-dep">{dep_opts}</select></label>'
        "</div>"
    )
    note = ("The sizer that builds your real ticket, re-run across every combination "
            "you could pick. Nothing is fetched and nothing is recomputed when you "
            "change these &mdash; the answers were all worked out when this page was "
            "written, which is why it responds instantly.")
    body = (f'<script type="application/json" id="wi-data">{payload}</script>'
            f'{controls}<div id="wi-out"></div>'
            '<div class="callout">Fees assume you are building the book from cash. '
            "Against what you already hold the real ticket is smaller, so treat this "
            "as the ceiling.</div>")
    return _card("What if I sized it differently?", body, note)


# --------------------------------------------------------------------------
# Composition
# --------------------------------------------------------------------------

def render_advanced(
    *,
    df: Optional[pd.DataFrame] = None,
    factor_weights: Optional[Dict[str, float]] = None,
    breakdown_tickers: Optional[Sequence[str]] = None,
    correlations: Optional[pd.DataFrame] = None,
    benchmark: Optional[pd.Series] = None,
    trend_ma: int = 200,
    benchmark_name: str = "IHSG",
    seasonality_table: Optional[pd.DataFrame] = None,
    current_month: Optional[int] = None,
    marks: Optional[pd.DataFrame] = None,
    allocation=None,
    max_per_sector: Optional[int] = None,
    whatif: Optional[dict] = None,
    capital: float = 0.0,
    deploy_pct: float = 1.0,
) -> str:
    """
    Compose the Advanced block as tab panels. A section with no data returns "" and
    both it and its tab disappear -- `tabbed` drops empty panels before it builds
    anything, so a live tab can never point at nothing.

    The short labels here are the tab text. They are deliberately terser than the
    headings inside the panels ("Worth" over "What is it worth?") because a strip of
    nine full headings is unreadable, and because writing them at the compose site
    means nothing has to parse the generated HTML to find a title.
    """
    panels: List[Tuple[str, str]] = []

    if df is not None and not df.empty:
        panels.append(("Universe", _card(
            "Every stock, every factor", universe_table(df),
            "The full screen, not just what you can afford. Click any column to sort. "
            "A z-score is standard deviations from the universe mean, already signed so "
            "positive always means better.",
        )))

    if breakdown_tickers and factor_weights:
        panels.append(("Why", _card(
            "Why these scored what they scored",
            score_breakdown(df, factor_weights, breakdown_tickers),
            "Each bar is one factor's signed contribution: its weight times its z-score. "
            "They sum to the raw score, so nothing is hidden.",
        )))

    panels.append(("Worth", valuation_section(df)))
    panels.append(("Overlap", correlation_section(correlations)))
    panels.append(("Regime", regime_chart(benchmark, trend_ma, benchmark_name)))
    panels.append(("Seasons", seasonality_section(seasonality_table, current_month)))
    panels.append(("Your curve", equity_section(marks)))
    panels.append(("What if", whatif_section(whatif, capital, deploy_pct)))
    panels.append(("Sectors", sector_section(df, allocation, max_per_sector)))
    panels.append(("Data gaps", data_quality_section(df)))

    body = tabbed(panels, "adv")
    if not body:
        return ""
    return f'<div class="adv">{body}</div>'
