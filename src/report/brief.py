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

_CSS = """
:root{
  --bg:#f7f7f5; --surface:#fff; --surface-alt:#f0efec; --ink:#1a1a18; --muted:#6b6b65;
  --line:#dedcd6; --accent:#2f5fd0; --good:#1a7f4b; --bad:#b3261e; --warn:#8a5a00;
  --good-bg:#e6f4ec; --bad-bg:#fbe9e7; --warn-bg:#fdf3e0;
}
@media (prefers-color-scheme:dark){
  :root:not([data-theme=light]){
    --bg:#16161a; --surface:#1e1e24; --surface-alt:#26262e; --ink:#ececf0; --muted:#9a9aa4;
    --line:#33333d; --accent:#7aa2f7; --good:#5ec98a; --bad:#f28b82; --warn:#e0b657;
    --good-bg:#152b1f; --bad-bg:#2e1a18; --warn-bg:#2b2313;
  }
}
*{box-sizing:border-box}
body{margin:0;padding:0 16px 64px;background:var(--bg);color:var(--ink);
  font:16px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;}
.wrap{max-width:940px;margin:0 auto}
header{padding:28px 0 8px}
h1{font-size:24px;margin:0 0 4px;letter-spacing:-.01em}
.sub{color:var(--muted);font-size:14px}
h2{font-size:15px;text-transform:uppercase;letter-spacing:.07em;color:var(--muted);
  margin:32px 0 10px;font-weight:600}
.card{background:var(--surface);border:1px solid var(--line);border-radius:12px;
  padding:18px 20px;margin-bottom:14px}
.verdict{display:flex;align-items:center;gap:16px;flex-wrap:wrap}
.verdict .big{font-size:30px;font-weight:700;letter-spacing:-.02em}
.verdict .why{color:var(--muted);font-size:14px;flex:1;min-width:240px}
.pill{display:inline-block;padding:2px 9px;border-radius:999px;font-size:12px;
  font-weight:600;border:1px solid var(--line);background:var(--surface-alt)}
.pill.good{color:var(--good);background:var(--good-bg);border-color:transparent}
.pill.bad{color:var(--bad);background:var(--bad-bg);border-color:transparent}
.pill.warn{color:var(--warn);background:var(--warn-bg);border-color:transparent}
.scroll{overflow-x:auto;-webkit-overflow-scrolling:touch}
table{border-collapse:collapse;width:100%;font-size:14px;min-width:520px}
th,td{text-align:left;padding:9px 10px;border-bottom:1px solid var(--line);vertical-align:top}
th{font-size:12px;text-transform:uppercase;letter-spacing:.05em;color:var(--muted);font-weight:600}
td.num,th.num{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
tr:last-child td{border-bottom:none}
.act{font-weight:700;font-size:13px;letter-spacing:.03em}
.act.buy{color:var(--good)} .act.sell{color:var(--bad)} .act.hold{color:var(--muted)}
.tick{font-weight:600}
.note{color:var(--muted);font-size:13px}
.money{font-variant-numeric:tabular-nums;white-space:nowrap}
.callout{border-left:3px solid var(--accent);padding:10px 14px;background:var(--surface-alt);
  border-radius:0 8px 8px 0;margin:12px 0;font-size:14px}
.callout.save{border-left-color:var(--good)}
.empty{color:var(--muted);font-style:italic;padding:6px 0}
footer{margin-top:40px;padding-top:18px;border-top:1px solid var(--line);
  color:var(--muted);font-size:12.5px;line-height:1.6}
.kpis{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:14px}
.kpi{background:var(--surface);border:1px solid var(--line);border-radius:10px;
  padding:11px 15px;min-width:132px;flex:1}
.kpi .k{font-size:11.5px;text-transform:uppercase;letter-spacing:.05em;color:var(--muted)}
.kpi .v{font-size:19px;font-weight:700;font-variant-numeric:tabular-nums;margin-top:2px}
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
        rows.append([
            f'<span class="act {cls}">{action}</span>',
            f'<span class="tick">{_e(o["ticker"])}</span>',
            detail,
            f'<span class="money">{rp(o.get("rupiah"))}</span>',
            f'<span class="note">{_e(o.get("note", ""))}</span>',
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
            flag_html,
        ])
    return _table(["Ticker", "Size", "Value now", "P&L", "Health"], rows, num_cols={2, 3})


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
            f'{c.get("score", 0):.2f}',
            f'<span class="money">{rp(c.get("lot_price"))}</span>',
            f'<span class="pill {liq_pill}">{_e(liq)}</span>',
            why,
        ])
    return _table(["Stock", "Score", "1 lot costs", "Liquidity", "Why it ranks here"],
                  rows, num_cols={1, 2})


def _rejected_section(rejected: Dict[str, str], capped: Optional[Dict[str, str]] = None) -> str:
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
        out += ("<h2>Skipped &mdash; you could not safely trade these</h2>"
                '<div class="card">' + _table(["Ticker", "Reason"], rows) + "</div>")
    if capped:
        rows = [[f'<span class="tick">{_e(t)}</span>', f'<span class="note">{_e(r)}</span>']
                for t, r in capped.items()]
        out += ("<h2>Held back for diversification</h2>"
                '<div class="card"><p class="note">These ranked well but the sector cap '
                'already filled their slots. Nothing is wrong with them.</p>'
                + _table(["Ticker", "Reason"], rows) + "</div>")
    return out


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
    universe_n: int = 0,
    imputed_n: int = 0,
    generated: Optional[datetime] = None,
) -> str:
    when = (generated or datetime.now()).strftime("%A, %d %B %Y %H:%M")

    kpis = "".join([
        _kpi("Capital", rp(capital)),
        _kpi("Deploy now", f"{regime.deploy_pct:.0%}"),
        _kpi("Positions", str(allocation.n_positions if allocation else 0)),
        _kpi("Cash left", rp(allocation.cash_left if allocation else capital)),
        _kpi("Est. fees", rp(fees.total)),
    ])

    granularity = ""
    if allocation and allocation.positions:
        granularity = (
            f'<div class="callout"><strong>Lot rounding.</strong> IDX trades in 100-share lots, so '
            f'weights cannot land exactly on target. Largest gap in this book: '
            f'{allocation.max_weight_error * 100:.1f} percentage points.</div>'
        )

    return f"""<!doctype html>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>IDX Brief</title>
<style>{_CSS}</style>
<div class="wrap">
<header>
  <h1>Your IDX brief</h1>
  <div class="sub">{_e(when)} &middot; {universe_n} stocks screened</div>
</header>

<h2>Market right now</h2>
{_verdict_card(regime)}

<div class="kpis">{kpis}</div>

<h2>Do this today</h2>
<div class="card">{_ticket_section(orders, fees, capital)}{granularity}</div>

<h2>What you hold</h2>
<div class="card">{_holdings_section(holdings_rows)}</div>

<h2>Best candidates you can actually afford</h2>
<div class="card">{_candidates_section(candidates)}</div>

{journal_html}

{_rejected_section(rejected, capped)}

<footer>
  <p><strong>Read this before you trade.</strong> The universe is today's list of
  tickers, so companies that already failed or delisted are missing &mdash; past
  performance implied by any ranking here is flattered by that.
  {imputed_n} of {universe_n} stocks are missing at least one data point and were
  scored neutral on it; those are marked &#9888; above.</p>
  <p>Prices come from Yahoo Finance and can be stale or wrong. Check the live price
  in your broker before sending an order. Fees shown use Indopremier's schedule
  (0.19% buy, 0.29% sell, Rp10,000 stamp per day with a sell) and are an estimate.</p>
  <p>This is a personal research tool, not investment advice.</p>
</footer>
</div>
"""


def write_brief(html_text: str, output_dir: Path, filename: str = "brief.html") -> Path:
    out = Path(output_dir) / filename
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html_text, encoding="utf-8")
    return out
