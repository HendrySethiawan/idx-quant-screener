# src/report/journal_view.py
"""Render the performance report: console block and a brief.html section."""
from __future__ import annotations

import html
from typing import List

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
