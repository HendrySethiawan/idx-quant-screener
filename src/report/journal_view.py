# src/report/journal_view.py
"""Render the performance report: console block and a brief.html section."""
from __future__ import annotations

import html
from typing import Dict, List, Optional

import pandas as pd

from report.brief import rp


def _pct(v, digits: int = 1) -> str:
    return "-" if v is None else f"{v:+.{digits}f}%"


def console_block(perf) -> str:
    L: List[str] = []
    add = L.append

    add("")
    add("HOW YOU'RE DOING")
    add("=" * 58)
    add(f"  Portfolio value      {rp(perf.total_value):>16}")
    add(f"    positions          {rp(perf.position_value):>16}")
    add(f"    cash               {rp(perf.cash):>16}")
    add("")
    add(f"  Realised P&L         {rp(perf.realized_pnl):>16}   (net of fees)")
    add(f"  Unrealised P&L       {rp(perf.unrealized_pnl):>16}")
    add(f"  Total                {rp(perf.total_pnl):>16}   {_pct(perf.return_pct)}")
    add("")

    if perf.shadow.unavailable:
        add("  vs IHSG              no index history available")
    elif perf.shadow_total <= 0:
        add("  vs IHSG              nothing deployed yet")
    else:
        verb = "AHEAD of" if perf.vs_ihsg_rp >= 0 else "BEHIND"
        add("  IF YOU HAD BOUGHT THE INDEX INSTEAD")
        add(f"    same money in IHSG {rp(perf.shadow_total):>16}")
        add(f"    your actual total  {rp(perf.total_value):>16}")
        add(f"    -> {verb} by        {rp(abs(perf.vs_ihsg_rp)):>16}   {_pct(perf.vs_ihsg_pct, 2)}")
        if perf.shadow.shortfall:
            add("       (your sells exceeded what the shadow index held)")
        add("    IHSG is not directly buyable; a real index position would be an")
        add("    ETF with its own costs.")
    add("")

    add(f"  Fees paid            {rp(perf.total_fees):>16}   {perf.fee_drag_pct:.2f}% of capital")
    add(f"    stamp duty         {rp(perf.stamp_paid):>16}")
    if perf.stamp_saved > 0:
        add(f"    saved by batching  {rp(perf.stamp_saved):>16}   (one stamp per sell day)")
    if perf.stamp_avoidable > 0:
        add(f"    sold on {perf.sell_days} days - consolidating would save up to "
            f"{rp(perf.stamp_avoidable)}")
    add("")

    if perf.n_closed:
        add(f"  Closed trades        {perf.n_closed:>16}")
        add(f"    win rate           {perf.hit_rate:>15.0f}%")
        if perf.avg_win is not None:
            add(f"    average win        {rp(perf.avg_win):>16}")
        if perf.avg_loss is not None:
            add(f"    average loss       {rp(perf.avg_loss):>16}")
        add(f"    avg holding        {perf.avg_holding_days:>13.0f} days")
        add("")

    if perf.attribution:
        add("  WHO PICKED BETTER")
        for a in perf.attribution:
            label = {"tool": "screener", "own": "you"}.get(a.source, a.source)
            add(f"    {label:<10} {a.n_trades:>3} trades  {rp(a.net_pnl):>14}  "
                f"avg {a.avg_return_pct:+.2f}%  win {a.win_rate:.0f}%")
        add("")

    add(f"  {perf.verdict}")
    add("=" * 58)
    return "\n".join(L)


def brief_section(perf) -> str:
    """HTML block for brief.html. Sits below the ticket: it reports the past."""
    if perf.total_value <= 0 and perf.n_closed == 0:
        return ""

    def kpi(k, v, sub=""):
        sub_html = f'<div class="note">{html.escape(sub)}</div>' if sub else ""
        return (f'<div class="kpi"><div class="k">{html.escape(k)}</div>'
                f'<div class="v">{html.escape(v)}</div>{sub_html}</div>')

    kpis = "".join([
        kpi("Portfolio", rp(perf.total_value)),
        kpi("Total P&L", rp(perf.total_pnl), _pct(perf.return_pct)),
        kpi("Fees paid", rp(perf.total_fees), f"{perf.fee_drag_pct:.2f}% of capital"),
        kpi("Closed trades", str(perf.n_closed)),
    ])

    bench = ""
    if not perf.shadow.unavailable and perf.shadow_total > 0:
        ahead = perf.vs_ihsg_rp >= 0
        bench = (
            f'<div class="callout {"save" if ahead else ""}">'
            f'<strong>Versus the index.</strong> The same rupiah, moved on the same days, '
            f'would have left you with {html.escape(rp(perf.shadow_total))} in IHSG. '
            f'You actually have {html.escape(rp(perf.total_value))} &mdash; '
            f'{"ahead by" if ahead else "behind by"} {html.escape(rp(abs(perf.vs_ihsg_rp)))} '
            f'({_pct(perf.vs_ihsg_pct, 2)}). IHSG is not directly buyable; a real index '
            f'position would be an ETF with its own costs.</div>'
        )

    stamp = ""
    if perf.stamp_saved > 0 or perf.stamp_avoidable > 0:
        bits = [f"You have paid {html.escape(rp(perf.stamp_paid))} in stamp duty"]
        if perf.stamp_saved > 0:
            bits.append(f"batching same-day sells has already saved "
                        f"{html.escape(rp(perf.stamp_saved))}")
        if perf.stamp_avoidable > 0:
            bits.append(f"you sold across {perf.sell_days} separate days &mdash; where the "
                        f"timing was discretionary, consolidating could have saved up to "
                        f"{html.escape(rp(perf.stamp_avoidable))}")
        stamp = f'<div class="callout save"><strong>Stamp duty.</strong> {"; ".join(bits)}.</div>' 

    verdict = f'<div class="callout"><strong>Verdict.</strong> {html.escape(perf.verdict)}</div>'

    return f"""
<h2>How you're doing</h2>
<div class="kpis">{kpis}</div>
<div class="card">{bench}{stamp}{verdict}</div>"""


# ===========================================================================
# The ledger: what you recorded, and what it actually made.
# ===========================================================================

def _e(v) -> str:
    return html.escape("" if v is None else str(v))


def _signed(value, formatter=rp) -> str:
    """Coloured by direction. A P&L column that is all one colour is a table."""
    if value is None:
        return '<span class="note">-</span>'
    cls = "good" if float(value) >= 0 else "bad"
    return f'<span style="color:var(--{cls})">{formatter(value)}</span>'


def monthly_table(monthly, totals) -> str:
    """
    Realised profit by the month the position was closed in.

    The footer comes from `ledger.monthly_totals`, not from re-summing here, so the
    table and its total cannot drift apart.
    """
    if monthly is None or getattr(monthly, "empty", True):
        return ('<div class="empty">No closed trades yet. A month appears here once '
                "you sell something.</div>")

    body = ""
    for _, r in monthly.iterrows():
        body += (
            f'<tr><td><span class="tick">{_e(r["month"])}</span></td>'
            f'<td class="num">{int(r["trades"])}</td>'
            f'<td class="num">{_signed(r["gross_pnl"])}</td>'
            f'<td class="num"><span class="note">{rp(r["fees"])}</span></td>'
            f'<td class="num"><strong>{_signed(r["net_pnl"])}</strong></td>'
            f'<td class="num">{float(r["win_rate"]) * 100:.0f}%</td></tr>'
        )

    foot = (
        f'<tr style="border-top:2px solid var(--line)">'
        f'<td><strong>All time</strong></td>'
        f'<td class="num"><strong>{totals["trades"]}</strong></td>'
        f'<td class="num">{_signed(totals["gross_pnl"])}</td>'
        f'<td class="num"><span class="note">{rp(totals["fees"])}</span></td>'
        f'<td class="num"><strong>{_signed(totals["net_pnl"])}</strong></td>'
        f'<td class="num">{totals["win_rate"] * 100:.0f}%</td></tr>'
    )

    return (
        '<div class="scroll"><table><thead><tr>'
        '<th>Month</th><th class="num">Trades</th><th class="num">Gross</th>'
        '<th class="num">Fees</th><th class="num">Net</th><th class="num">Win</th>'
        f"</tr></thead><tbody>{body}{foot}</tbody></table></div>"
        '<div class="note">A trade counts in the month you <em>sold</em> it, already '
        "net of the buy fee paid earlier plus the sell fee and the stamp. Positions "
        "you still hold are below and are not counted here until they are closed, so "
        "a month's figure never changes after the month ends.</div>"
    )


def open_positions_table(positions) -> str:
    if positions is None or getattr(positions, "empty", True):
        return '<div class="empty">Nothing open.</div>'

    body = ""
    for _, r in positions.iterrows():
        pct = r["unrealized_pct"]
        body += (
            f'<tr><td><span class="tick">{_e(r["ticker"])}</span></td>'
            f'<td class="num">{int(r["lots"])} lot</td>'
            f'<td class="num">{rp(r["avg_cost"])}</td>'
            f'<td class="num">{rp(r["cost_basis"])}</td>'
            f'<td class="num">{rp(r["value_now"])}</td>'
            f'<td class="num">{_signed(r["unrealized_pnl"])}</td>'
            f'<td class="num">{"-" if pct is None else _signed(pct, lambda v: f"{v:+.1f}%")}'
            f"</td></tr>"
        )

    return (
        '<div class="scroll"><table><thead><tr>'
        '<th>Ticker</th><th class="num">Size</th><th class="num">Avg cost</th>'
        '<th class="num">Cost basis</th><th class="num">Value now</th>'
        '<th class="num">Unrealised</th><th class="num">%</th>'
        f"</tr></thead><tbody>{body}</tbody></table></div>"
        '<div class="note">Average cost includes the buy fee you actually paid, so '
        "this is the price the position has to beat to be genuinely ahead. Prices are "
        "from the last run, not live.</div>"
    )


def closed_trades_table(closed, limit: int = 40) -> str:
    """Every round-trip, newest first. Computed on every run and never shown before."""
    if closed is None or getattr(closed, "empty", True):
        return '<div class="empty">No completed round-trips yet.</div>'

    df = closed.copy()
    df["sell_date"] = pd.to_datetime(df["sell_date"], errors="coerce")
    df = df.sort_values("sell_date", ascending=False).head(int(limit))

    body = ""
    for _, r in df.iterrows():
        sold = r["sell_date"]
        body += (
            f'<tr><td><span class="tick">{_e(r["ticker"])}</span></td>'
            f'<td class="num"><span class="note">'
            f'{sold.strftime("%d %b %y") if pd.notna(sold) else "-"}</span></td>'
            f'<td class="num">{int(r["shares"]) // 100} lot</td>'
            f'<td class="num">{rp(r["buy_price"])}</td>'
            f'<td class="num">{rp(r["sell_price"])}</td>'
            f'<td class="num">{_signed(r["gross_pnl"])}</td>'
            f'<td class="num"><span class="note">{rp(r["fees"])}</span></td>'
            f'<td class="num"><strong>{_signed(r["net_pnl"])}</strong></td>'
            f'<td class="num">{_signed(r["return_pct"], lambda v: f"{v:+.1f}%")}</td>'
            f'<td class="num"><span class="note">{int(r["holding_days"])}d</span></td>'
            f'<td><span class="pill">{_e(r["source"])}</span></td></tr>'
        )

    return (
        '<div class="scroll"><table><thead><tr>'
        '<th>Ticker</th><th class="num">Sold</th><th class="num">Size</th>'
        '<th class="num">In</th><th class="num">Out</th><th class="num">Gross</th>'
        '<th class="num">Fees</th><th class="num">Net</th><th class="num">Return</th>'
        '<th class="num">Held</th><th>Source</th>'
        f"</tr></thead><tbody>{body}</tbody></table></div>"
        '<div class="note">Matched first-in first-out, the way an Indonesian broker '
        "statement does. <em>Fees</em> is this trade's share of the buy fee, the sell "
        "fee and the stamp, so <em>Net</em> is what actually reached the account.</div>"
    )


# ===========================================================================
# Input. Live only in the desktop window; a plain file shows the command instead.
# ===========================================================================

def trade_form(today: str = "") -> str:
    """
    Record a trade you have already made in Indopremier.

    The button says "Record trade", past tense, because that is what it does. This
    places no orders and has no route to a broker -- the same reason the terminal has
    no BUY or SELL control anywhere else.

    The fee preview is filled in by Python, never computed here. The stamp is
    Rp10,000 only on the first sell of a day, so a preview written in JavaScript
    would quote it twice and then record it once.
    """
    today = today or pd.Timestamp.today().strftime("%Y-%m-%d")
    return f"""
<form class="trade-form" id="trade-form" onsubmit="return false">
  <div class="tf-row">
    <label><input type="radio" name="tf-action" value="BUY" checked> Bought</label>
    <label><input type="radio" name="tf-action" value="SELL"> Sold</label>
  </div>
  <div class="tf-row">
    <label>Ticker <input id="tf-ticker" placeholder="BBRI" autocomplete="off"></label>
    <label>Lots <input id="tf-lots" type="number" min="1" step="1" value="1"></label>
    <label>Price <input id="tf-price" type="number" min="1" step="1" placeholder="4150"></label>
  </div>
  <div class="tf-row">
    <label>Date <input id="tf-date" type="date" value="{_e(today)}"></label>
    <label>Whose call
      <select id="tf-source">
        <option value="tool">the tool's pick</option>
        <option value="own">my own call</option>
      </select>
    </label>
  </div>
  <div class="tf-row">
    <label style="flex:1">Note <input id="tf-note" placeholder="MSCI rebalance" autocomplete="off"></label>
  </div>
  <div id="tf-preview" class="tf-preview"></div>
  <div class="tf-row">
    <button type="button" id="tf-submit" class="tf-go">Record trade</button>
    <span id="tf-msg" class="note"></span>
  </div>
</form>
<div class="note">Records what you already did. It places no orders.</div>"""


def cli_fallback() -> str:
    """
    Shown when the page is opened as a file rather than in the app.

    A form with nothing behind it is worse than no form: it looks like it worked.
    """
    return """
<div class="callout"><strong>Opened as a file.</strong> Recording a trade needs the
app window, which is what talks to Python. From here, use the command line:</div>
<pre class="cli">python main.py --log BUY  BBRI 3 4150
python main.py --log SELL BBCA 2 6450 --note "MSCI rebalance"
python main.py --mark                     # snapshot value vs IHSG
python main.py --event ADRO earnings 2026-08-27</pre>"""


def journal_panels(settings, prices: Optional[Dict[str, float]] = None) -> str:
    """
    Every panel that depends on the journal alone.

    Rebuilt after a trade is recorded and swapped into the page, which is why it
    takes no market data beyond the prices handed in: realised profit, the monthly
    table and the round-trip list need no prices at all and stay exact.
    """
    from cli import _paths
    from portfolio import journal as J
    from portfolio.ledger import monthly_realized, monthly_totals, open_positions

    journal_path, _, _ = _paths(settings)
    journal = J.load_journal(journal_path)
    closed = J.closed_trades(journal)
    monthly = monthly_realized(closed)
    totals = monthly_totals(monthly)
    positions = open_positions(journal, prices or {})

    return (
        f'<h2>Realised, by month</h2>{monthly_table(monthly, totals)}'
        f'<h2>Still open</h2>{open_positions_table(positions)}'
        f'<h2>Every completed round-trip</h2>{closed_trades_table(closed)}'
    )
