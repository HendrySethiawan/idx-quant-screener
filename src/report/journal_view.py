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
    elif not perf.comparable:
        add("  vs IHSG              nothing to compare yet - everything you recorded")
        add("                       was bought and sold on the same day")
    elif not perf.shadow_total:
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


def _yield_note(realised: dict) -> str:
    """What the holdings actually paid, against what the screener promised."""
    if not realised:
        return "income received"
    best = sorted(realised.items(), key=lambda kv: -kv[1])[:2]
    return ", ".join(f"{t.replace('.JK','')} {v:.1f}% on cost" for t, v in best)


def brief_section(perf) -> str:
    """HTML block for brief.html. Sits below the ticket: it reports the past."""
    if perf.total_value <= 0 and perf.n_closed == 0:
        return ""

    def kpi(k, v, sub=""):
        sub_html = f'<div class="note">{html.escape(sub)}</div>' if sub else ""
        return (f'<div class="kpi"><div class="k">{html.escape(k)}</div>'
                f'<div class="v">{html.escape(v)}</div>{sub_html}</div>')

    # Cash and holdings are shown apart. Merged into one "Portfolio" figure, a
    # Rp176,896 profit disappeared inside a Rp100,000,000 total and the headline
    # read +0.2%, which says nothing about the decision that produced it.
    kpis = "".join([
        kpi("Cash", rp(perf.cash)),
        kpi("Holdings", rp(perf.position_value)),
        kpi("Total", rp(perf.total_value), _pct(perf.return_pct) + " of capital"),
        kpi("Realised", rp(perf.realized_pnl),
            (f"{perf.return_on_closed_pct:+.1f}% on {rp(perf.closed_cost)} closed"
             if perf.return_on_closed_pct is not None else "nothing closed yet")),
        kpi("Unrealised", rp(perf.unrealized_pnl),
            (f"{perf.return_on_open_pct:+.1f}% on {rp(perf.open_cost)} at risk"
             if perf.return_on_open_pct is not None else "nothing held")),
        kpi("Fees paid", rp(perf.total_fees), f"{perf.fee_drag_pct:.2f}% of capital"),
        kpi("Closed trades", str(perf.n_closed)),
    ] + ([
        # Only when there is income. A permanent "Rp0 dividends" tile would read
        # as "these names pay nothing", which is a different claim from "none has
        # been recorded".
        kpi("Dividends", rp(perf.dividend_income),
            _yield_note(perf.realised_yield_pct)),
    ] if perf.dividend_income else []))

    bench = ""
    if not perf.comparable:
        bench = (
            '<div class="callout"><strong>Nothing to compare against the index yet.</strong> '
            "Everything you have recorded was bought and sold on the same day, and this "
            "tool works from daily closes &mdash; so the index cannot differ from itself "
            "over that span. The comparison starts meaning something once a position is "
            "held overnight.</div>"
        )
    elif not perf.shadow.unavailable and perf.shadow_total:
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

    deployed_note = (
        '<div class="note">Realised and unrealised percentages are measured against '
        "the money actually put at risk, not against your whole capital. Trading the "
        "same rupiah repeatedly inflates that base, so they answer &ldquo;how did the "
        "money I committed do&rdquo;, not &ldquo;how did my account do&rdquo; &mdash; "
        "the figure under Total is the one comparable to the index.</div>"
    )
    return f"""
<h2>How you're doing</h2>
<div class="kpis">{kpis}</div>
{deployed_note}
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
        '<th>Ticker</th><th class="num">Size</th><th class="num">YOUR avg cost</th>'
        '<th class="num">Cost basis</th><th class="num">Value now</th>'
        '<th class="num">Unrealised</th><th class="num">%</th>'
        f"</tr></thead><tbody>{body}</tbody></table></div>"
        '<div class="note">Average cost includes the buy fee you actually paid, so '
        "this is the price the position has to beat to be genuinely ahead. Prices are "
        "from the last run, not live.</div>"
    )


def _row_actions(index: int, row) -> str:
    """A remove control on every row, not only the newest."""
    when = pd.to_datetime(row["date"], errors="coerce")
    return (f'<button type="button" class="rm-trade" data-index="{int(index)}" '
            f'data-ticker="{_e(row["ticker"])}" data-price="{float(row["price"])}" '
            f'data-date="{when:%Y-%m-%d}" title="Remove this trade">remove</button>')


def trade_log_table(journal) -> str:
    """
    Every recorded row, with its own remove control.

    "Remove last trade" could not reach a mistyped price once anything was recorded
    after it -- and undoing three good trades to fix one bad one is not a repair.
    """
    if journal is None or getattr(journal, "empty", True):
        return '<div class="empty">Nothing recorded yet.</div>'

    df = journal.copy()
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df.sort_values("date", kind="stable").reset_index(drop=True)

    body = ""
    for i, r in df.iterrows():
        cls = "buy" if str(r["action"]).upper() == "BUY" else "sell"
        body += (
            f'<tr><td class="num"><span class="note">{r["date"]:%d %b %y}</span></td>'
            f'<td><span class="act {cls}">{_e(r["action"])}</span></td>'
            f'<td><span class="tick">{_e(r["ticker"])}</span></td>'
            f'<td class="num">{int(r["lots"])} lot</td>'
            f'<td class="num">{rp(r["price"])}</td>'
            f'<td class="num"><span class="note">{rp(r["fee_rp"] + r["stamp_rp"])}</span></td>'
            f'<td class="num">{_signed(r["net_rp"])}</td>'
            f'<td><span class="pill">{_e(r["source"])}</span></td>'
            f"<td>{_row_actions(i, r)}</td></tr>"
        )

    return (
        '<div class="scroll"><table><thead><tr>'
        '<th class="num">Date</th><th>Do</th><th>Ticker</th><th class="num">Size</th>'
        '<th class="num">Price</th><th class="num">Cost</th>'
        '<th class="num">Cash</th><th>Whose call</th><th></th>'
        f"</tr></thead><tbody>{body}</tbody></table></div>"
        '<div class="note">Any row can be removed. One whose purchase a later sale '
        "has already been matched against is refused, because the profit maths would "
        "quietly come up short.</div>"
    )


def closed_trades_table(closed, limit: int = 40) -> str:
    """Every round-trip, newest first. Computed on every run and never shown before."""
    if closed is None or getattr(closed, "empty", True):
        return '<div class="empty">No completed round-trips yet.</div>'

    df = closed.copy()
    df["sell_date"] = pd.to_datetime(df["sell_date"], errors="coerce")
    df = df.sort_values("sell_date", ascending=False).head(int(limit))

    from portfolio.ledger import implausible

    body = ""
    for _, r in df.iterrows():
        sold = r["sell_date"]
        doubt = implausible(r)
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
            f'<td class="num">{_signed(r["return_pct"], lambda v: f"{v:+.1f}%")}'
            + (f'<br><span class="pill warn">{_e(doubt)}</span>' if doubt else "")
            + "</td>"
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
<div class="note">Records what you already did. It places no orders.</div>
<div class="undo-row">
  <button type="button" id="undo-last" disabled>Remove last trade</button>
  <span id="undo-what" class="note">checking&hellip;</span>
</div>
<div class="note">Any row in the log below can be removed, not only this one. A buy
that a later sale has already been matched against is refused, because the round-trip
maths would quietly come up short.</div>"""


def dividend_form(today: str = "") -> str:
    """
    Income received, against the holding that paid it.

    The screener quotes a forward yield from Yahoo and ranks on it heavily. This
    is the only place the number it promised ever meets the number that arrived.
    """
    today = today or pd.Timestamp.today().strftime("%Y-%m-%d")
    return f"""
<form class="trade-form" id="dividend-form" onsubmit="return false">
  <div class="tf-row">
    <label>Ticker <input id="df-ticker" placeholder="BBRI" autocomplete="off"></label>
    <label>Amount received <input id="df-amount" type="number" min="1" step="1"
           placeholder="120000"></label>
    <label>Date <input id="df-date" type="date" value="{_e(today)}"></label>
  </div>
  <div class="tf-row">
    <label style="flex:1">Note <input id="df-note" placeholder="annual dividend"
           autocomplete="off"></label>
  </div>
  <div id="df-preview" class="tf-preview"></div>
  <div class="tf-row">
    <button type="button" id="df-submit" class="tf-go">Record dividend</button>
    <span id="df-msg" class="note"></span>
  </div>
</form>
<div class="note">Enter what actually reached your account &mdash; net of the 10%
final tax. Income is counted in your cash and total, but kept out of realised P&amp;L
and out of the index comparison: a dividend is not a trading decision, and IHSG is
a price index that pays nothing.</div>"""


def dividend_table(dividends) -> str:
    """Every dividend received, each row removable."""
    from portfolio.dividends import total_received

    if dividends is None or getattr(dividends, "empty", True):
        return '<div class="empty">No dividends recorded.</div>'

    df = dividends.copy()
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df.sort_values("date", kind="stable").reset_index(drop=True)

    body = ""
    for i, r in df.iterrows():
        amount = float(r["amount_rp"])
        body += (
            f'<tr><td class="num"><span class="note">{r["date"]:%d %b %y}</span></td>'
            f'<td><span class="tick">{_e(r["ticker"])}</span></td>'
            f'<td class="num">{_signed(amount)}</td>'
            f'<td><span class="note">{_e("" if pd.isna(r["note"]) else str(r["note"]))}'
            "</span></td>"
            f'<td><button type="button" class="rm-div" data-index="{int(i)}" '
            f'data-ticker="{_e(r["ticker"])}" data-amount="{amount}" '
            f'data-date="{r["date"]:%Y-%m-%d}" '
            f'title="Remove this dividend">remove</button></td></tr>'
        )

    return (
        '<div class="scroll"><table><thead><tr>'
        '<th class="num">Date</th><th>Ticker</th><th class="num">Received</th>'
        '<th>Why</th><th></th>'
        f"</tr></thead><tbody>{body}</tbody>"
        f'<tfoot><tr><td colspan="2">Income to date</td>'
        f'<td class="num"><strong>{rp(total_received(df))}</strong></td>'
        '<td colspan="2"><span class="note">net of the 10% final tax</span>'
        "</td></tr></tfoot></table></div>"
    )


def cash_form(today: str = "") -> str:
    """
    Money in and money out -- and therefore capital.

    Capital used to be a number typed into a config file, which put the figure that
    sizes every recommendation somewhere other than the money it describes. Here it
    has a date and a reason, and it is the ledger that defines it.
    """
    today = today or pd.Timestamp.today().strftime("%Y-%m-%d")
    return f"""
<form class="trade-form" id="cash-form" onsubmit="return false">
  <div class="tf-row">
    <label><input type="radio" name="cf-kind" value="DEPOSIT" checked> Paid in</label>
    <label><input type="radio" name="cf-kind" value="WITHDRAW"> Took out</label>
  </div>
  <div class="tf-row">
    <label>Amount <input id="cf-amount" type="number" min="1" step="1"
           placeholder="25000000"></label>
    <label>Date <input id="cf-date" type="date" value="{_e(today)}"></label>
  </div>
  <div class="tf-row">
    <label style="flex:1">Note <input id="cf-note" placeholder="opening balance"
           autocomplete="off"></label>
  </div>
  <div id="cf-preview" class="tf-preview"></div>
  <div class="tf-row">
    <button type="button" id="cf-submit" class="tf-go">Record</button>
    <span id="cf-msg" class="note"></span>
  </div>
</form>
<div class="note">What you have paid in is what every lot count is sized against.
Record your opening balance here and the placeholder figure goes away. Every entry
is listed under <strong>Money in and out</strong> in your ledger, and any of them
can be removed.</div>"""


def cash_table(cash) -> str:
    """The ledger, oldest first, each row removable."""
    from portfolio.cash import totals as _totals

    if cash is None or getattr(cash, "empty", True):
        return '<div class="empty">No deposits or withdrawals recorded.</div>'

    df = cash.copy()
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df.sort_values("date", kind="stable").reset_index(drop=True)

    body = ""
    for i, r in df.iterrows():
        deposit = str(r["kind"]).upper() == "DEPOSIT"
        amount = float(r["amount_rp"])
        body += (
            f'<tr><td class="num"><span class="note">{r["date"]:%d %b %y}</span></td>'
            f'<td><span class="act {"buy" if deposit else "sell"}">'
            f'{"IN" if deposit else "OUT"}</span></td>'
            f'<td class="num">{_signed(amount if deposit else -amount)}</td>'
            f'<td><span class="note">{_e("" if pd.isna(r["note"]) else str(r["note"]))}'
            "</span></td>"
            f'<td><button type="button" class="rm-cash" data-index="{int(i)}" '
            f'data-kind="{"DEPOSIT" if deposit else "WITHDRAW"}" '
            f'data-amount="{amount}" data-date="{r["date"]:%Y-%m-%d}" '
            f'title="Remove this entry">remove</button></td></tr>'
        )

    t = _totals(df)
    return (
        '<div class="scroll"><table><thead><tr>'
        '<th class="num">Date</th><th>Way</th><th class="num">Amount</th>'
        '<th>Why</th><th></th>'
        f"</tr></thead><tbody>{body}</tbody>"
        f'<tfoot><tr><td colspan="2">Paid in, net</td>'
        f'<td class="num"><strong>{rp(t["net"])}</strong></td>'
        f'<td colspan="2"><span class="note">{rp(t["deposits"])} in, '
        f'{rp(t["withdrawals"])} out</span></td></tr></tfoot></table></div>'
    )


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

    from portfolio.cash import cash_path, load_cash
    from portfolio.dividends import dividends_path, load_dividends

    journal_path, _, _ = _paths(settings)
    journal = J.load_journal(journal_path)
    cash = load_cash(cash_path(settings))
    dividends = load_dividends(dividends_path(settings))
    closed = J.closed_trades(journal)
    monthly = monthly_realized(closed)
    totals = monthly_totals(monthly)
    positions = open_positions(journal, prices or {})

    return (
        f'<h2>Realised, by month</h2>{monthly_table(monthly, totals)}'
        f'<h2>Still open</h2>{open_positions_table(positions)}'
        f'<h2>Every completed round-trip</h2>{closed_trades_table(closed)}'
        f'<h2>Everything you recorded</h2>{trade_log_table(journal)}'
        f'<h2>Money in and out</h2><div id="cash-log">{cash_table(cash)}</div>'
        f'<h2>Dividends received</h2>'
        f'<div id="dividend-log">{dividend_table(dividends)}</div>'
    )
