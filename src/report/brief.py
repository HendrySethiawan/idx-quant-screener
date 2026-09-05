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

from report import layout, terminal as T
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

/* --- input ------------------------------------------------------------- */
.trade-form{display:flex;flex-direction:column;gap:8px}
.tf-row{display:flex;gap:9px;flex-wrap:wrap;align-items:flex-end}
.tf-row label{display:flex;flex-direction:column;gap:3px;font-size:10.5px;
  text-transform:uppercase;letter-spacing:.05em;color:var(--muted)}
.tf-row input,.tf-row select{font:inherit;font-size:12.5px;padding:5px 8px;
  border-radius:5px;border:1px solid var(--line);background:var(--surface-3);
  color:var(--ink);text-transform:none;letter-spacing:normal;max-width:150px}
.tf-row input[type=radio]{max-width:none;margin-right:5px}
.tf-row label:has(input[type=radio]){flex-direction:row;align-items:center;
  font-size:12.5px;text-transform:none;letter-spacing:normal;color:var(--ink)}
.tf-go{font:inherit;font-size:12.5px;font-weight:700;cursor:pointer;padding:7px 16px;
  border-radius:6px;border:1px solid transparent;background:var(--accent);color:#fff}
.tf-go:hover{filter:brightness(1.1)}
.tf-go:disabled{opacity:.5;cursor:default}
.tf-preview{font-size:11.5px;font-variant-numeric:tabular-nums;
  background:var(--surface-3);border:1px solid var(--line);border-radius:6px;
  padding:8px 11px;min-height:34px}
.tf-preview .row{display:flex;justify-content:space-between;gap:14px}
.tf-preview .row.total{border-top:1px solid var(--line);margin-top:5px;
  padding-top:5px;font-weight:700}
.tf-preview .pricewarn{margin-top:6px;padding:5px 7px;border-radius:5px;
  background:color-mix(in srgb,var(--bad) 14%,transparent);
  border:1px solid color-mix(in srgb,var(--bad) 40%,transparent);
  color:var(--bad);line-height:1.45}
.tf-preview .note{margin-top:5px;color:var(--muted);line-height:1.45}
.rm-trade{font:inherit;font-size:10.5px;padding:2px 8px;border-radius:5px;cursor:pointer;
  border:1px solid var(--line);background:var(--surface);color:var(--muted)}
.rm-trade:hover:not(:disabled){color:var(--bad);border-color:var(--bad)}
.rm-trade:disabled{opacity:.5;cursor:default}
.rm-why{max-width:230px;white-space:normal;font-size:10.5px;margin-top:4px}
.pill.warn{background:color-mix(in srgb,var(--bad) 16%,transparent);color:var(--bad);
  border:1px solid color-mix(in srgb,var(--bad) 38%,transparent);
  white-space:normal;display:inline-block;margin-top:3px;text-align:left}
pre.cli{background:var(--surface-3);border:1px solid var(--line);border-radius:6px;
  padding:9px 11px;font-size:11.5px;overflow-x:auto;margin:8px 0;
  font-family:Consolas,"Courier New",monospace}
.set-row{display:flex;align-items:center;gap:9px;padding:6px 0;
  border-bottom:1px solid var(--line);flex-wrap:wrap}
.set-row:last-child{border-bottom:none}
.set-row .lbl{flex:1;min-width:130px;font-size:12px}
.set-row input{font:inherit;font-size:12px;padding:4px 7px;border-radius:5px;
  border:1px solid var(--line);background:var(--surface-3);color:var(--ink);width:130px;
  text-align:right;font-variant-numeric:tabular-nums}
.set-row .dflt{font-size:10.5px;color:var(--muted);min-width:96px}
.set-row button{font:inherit;font-size:11px;padding:3px 9px;border-radius:5px;
  border:1px solid var(--line);background:var(--surface);color:var(--muted);cursor:pointer}
.set-row button:hover{color:var(--ink)}
.set-row.changed .lbl::after{content:" \\2022";color:var(--accent)}
.undo-row{display:flex;align-items:center;gap:9px;margin-top:9px;flex-wrap:wrap}
.undo-row button{font:inherit;font-size:11.5px;font-weight:600;cursor:pointer;
  padding:5px 12px;border-radius:6px;border:1px solid var(--line);
  background:var(--surface-3);color:var(--ink-dim)}
.undo-row button:hover:not(:disabled){color:var(--bad);border-color:var(--bad)}
.undo-row button:disabled{opacity:.45;cursor:default}
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


def evidence_note(verdict: Optional[dict]) -> str:
    """
    What the ranking above is actually worth, next to the ranking above.

    The backtest's conclusions used to live in a separate HTML file produced only
    by `--backtest`, so the panel headed "Do this today" stated the tool's
    least-supported output with its most confident voice. Every other overclaim in
    this project has been closed -- the mistyped price, the placeholder capital,
    the stale session. This was the last one, and it was the first thing you saw.
    """
    if not verdict:
        return (
            '<div class="callout" style="border-left-color:var(--warn)">'
            "<strong>This ranking has never been tested on this machine.</strong> "
            "Run <code>python main.py --backtest</code> to find out whether it beat "
            "simply holding everything. Until then the order below is a hypothesis, "
            "not a finding.</div>"
        )

    bits = []
    cagr_gap = verdict.get("cagr_gap_vs_equal_pp")
    sharpe_gap = verdict.get("sharpe_gap_vs_equal")
    if cagr_gap is not None and sharpe_gap is not None:
        verb = "added" if cagr_gap >= 0 else "cost"
        risk = "gave up" if sharpe_gap < 0 else "added"
        # Both sides frictionless, which is the only fair comparison -- the net
        # curve pays fees the benchmark never does, and comparing those two would
        # flatter the benchmark by exactly the trading costs.
        years = (verdict.get("gross") or {}).get("years")
        span = f"Over {years:.0f} years, a" if years else "A"
        cadence = str(verdict.get("cadence") or "").lower()
        cadence_note = f" on a {cadence} rebalance" if cadence else ""
        bits.append(
            f"{span}gainst simply holding every name in the list, this ranking "
            f"{verb} <strong>{abs(cagr_gap):.1f}pp a year</strong> of return and "
            f"{risk} <strong>{abs(sharpe_gap):.2f} of Sharpe</strong>{cadence_note} "
            f"&mdash; both measured before costs, which is the only fair comparison."
        )

    robust = verdict.get("robustness")
    if robust:
        bits.append(_e(robust))

    # What trading cost over the same window, at this account size. The largest
    # controllable effect in the whole simulation, and the ticket never said it.
    costs = verdict.get("costs") or {}
    if costs.get("fee_share_of_gross_pct") is not None:
        bits.append(
            f"Trading costs took <strong>"
            f"{costs['fee_share_of_gross_pct']:.0f}% of the gross return</strong> "
            f"over that window &mdash; {abs(costs['fee_effect_pp']):.0f} points of "
            f"{costs['gross_return_pct']:.0f}. Turnover is the part of this you "
            f"control."
        )

    surv = verdict.get("survivorship") or {}
    if surv.get("gap_cagr") is not None:
        bits.append(
            f"And most of the absolute return is the ticker list, not the method: "
            f"holding all {surv.get('n_names')} names equally returned "
            f"{surv.get('universe_cagr'):+.1f}% a year against the index's "
            f"{surv.get('index_cagr'):+.1f}% &mdash; a list drawn knowing who survived."
        )

    if not bits:
        return ""
    return ('<div class="callout" style="border-left-color:var(--warn)">'
            "<strong>What this ranking is worth.</strong> " + " ".join(bits) + "</div>"
            + exits_note(verdict))


def exits_note(verdict: Optional[dict]) -> str:
    """
    What the stops and the ladder did over the backtest window.

    The exit panel puts a level to sell at on every position you hold, stated as
    plainly as the ranking states its picks. Every other confident output in this
    tool has had its evidence attached to it; this one does not get to be the
    exception because it happens to be new.
    """
    ex = (verdict or {}).get("exits") or {}
    if ex.get("cagr_gap_pp") is None:
        return ""

    cagr, dd = ex["cagr_gap_pp"], ex.get("drawdown_gap_pp")
    fees, days = ex.get("extra_fees_rp"), ex.get("extra_sell_days")
    # Named, because it changes the answer. Measured on this universe the ladder
    # cut the worst drawdown from -16.3% to -10.5% on a monthly rebalance and did
    # nothing at all on a weekly one -- so a figure with no cadence attached to it
    # is not a finding, it is a number.
    cadence = str(verdict.get("cadence") or "").lower()
    held = f"holding to the next {cadence} rebalance" if cadence else "holding to the next rebalance"

    bits = [
        f"Against simply {held}, the stop and ladder "
        f"<strong>{'added' if cagr >= 0 else 'cost'} {abs(cagr):.1f}pp a year</strong> "
        f"of return"
    ]
    if dd is not None:
        # A positive gap means a SHALLOWER trough: both figures are negative and
        # the stopped one is closer to zero. Said in words, because "+20.9pp of
        # drawdown" reads like more drawdown to most people.
        bits.append(
            f"and made the worst drawdown "
            f"<strong>{abs(dd):.1f}pp {'shallower' if dd >= 0 else 'deeper'}</strong>")
    line = " ".join(bits) + "."

    # The number that actually settles it. Giving up return for a smaller worst
    # case is the whole trade, and Sharpe is the one figure that prices both at
    # once -- a note quoting only the cost would be arguing one side of it.
    sharpe = ex.get("sharpe_gap")
    if sharpe is not None:
        line += (f" Risk-adjusted that is "
                 f"<strong>{sharpe:+.2f} of Sharpe</strong>, which is the number "
                 f"that weighs the two against each other.")

    # The sign is not decoration. Turnover can fall as well as rise: the cooldown
    # stops the rebalance buying back what an exit just sold, and on this universe
    # that saved more than the extra selling days cost.
    if fees:
        line += (f" It {'paid' if fees > 0 else 'saved'} {rp(abs(fees))} "
                 f"{'more ' if fees > 0 else ''}in fees")
        if days:
            line += (f" across {abs(int(days))} "
                     f"{'more' if days > 0 else 'fewer'} selling days")
        line += (" &mdash; the stamp is charged once per day containing a sale, so "
                 "batching is worth real money.")
    # The row that decides whether the ladder should be on at all. Measured on this
    # universe a stop with no profit-taking beat both holding and the ladder, and
    # burying that under the aggregate gap would be the same overclaim this project
    # has spent every other session removing.
    solo = ex.get("stop_only_cagr_gap_pp")
    if solo is not None:
        line += (
            f" A stop with <strong>no profit-taking at all</strong> came out "
            f"{'ahead of' if solo >= 0 else 'behind'} holding by "
            f"<strong>{abs(solo):.1f}pp a year</strong> over the same window.")
        # Two independent facts, and the conclusion follows the arithmetic rather
        # than the hope: whether a stop beats holding, and whether the trims cost
        # anything on top of it. On this universe the second has been true at both
        # cadences even where the first was not.
        if solo > cagr:
            line += (
                f" The <strong>{solo - cagr:.1f}pp between them is the ladder's own "
                f"cost</strong>. Setting <code>risk.ladder: []</code> in "
                f"<code>configs/user.yaml</code> keeps every stop and drops the "
                f"trims.")
    line += (" A stop is not a return generator; what it does is shorten the losing "
             "tail. <code>backtest.html</code> carries the full table for every "
             "rebalance frequency.")

    return ('<div class="callout" style="border-left-color:var(--warn)">'
            "<strong>What the exit rules are worth.</strong> " + line + "</div>")


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


def _stop_cell(o: dict) -> str:
    """
    The level, and what kind of level it is.

    Present on every row that has one, including HOLD: a position at target weight
    is not a blank decision, and how far away its stop sits IS the answer to
    "should I keep this". BUY rows show the stop the position would be opened
    under, plus what being wrong about it costs -- which is the difference between
    "Rp1.3 juta of INET" and "Rp220,000 at risk, 2.2% of everything you have".
    """
    stop = o.get("stop_rp")
    if not stop:
        return '<span class="note">-</span>'

    out = f'<span class="money">{rp(stop)}</span>'
    if o.get("stop_kind"):
        out += f'<br><span class="note">{_e(o["stop_kind"])}</span>'

    pct = o.get("risk_pct")
    if pct is not None:
        cls = "pill warn" if o.get("risk_over") else "note"
        out += (f'<br><span class="{cls}">risk {rp(o.get("risk_rp"))} '
                f'&middot; {pct:.1f}%</span>')
    if o.get("risk_capped"):
        out += '<br><span class="pill warn">capped</span>'
    return out


def _ticket_section(orders: List[dict], fees, capital: float,
                    open_risk: Optional[dict] = None,
                    exit_cfg=None) -> str:
    from portfolio.fees import FeeConfig, round_trip_cost

    # SELL first because a breached stop is the most urgent thing on the page,
    # TRIM next because it is still a sale and shares the day's stamp with one,
    # then the buys, then what needs no action at all.
    order_by = {"SELL": 0, "TRIM": 1, "BUY": 2, "HOLD": 3, "WAIT": 4}
    rows = []
    for o in sorted(orders, key=lambda x: order_by.get(x["action"], 9)):
        action = o["action"]
        # An exit-driven sale is recorded as a SELL because that is what you place
        # with the broker, but it is labelled TRIM when it is a partial one -- the
        # two are different decisions and reading "SELL 4 lot" next to "SELL 10
        # lot" gives no clue which is taking profit and which is being wrong.
        label = "TRIM" if o.get("exit_kind") == "TRIM" else action
        cls = label.lower()
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
            f'<span class="act {cls}">{label}</span>',
            f'<span class="tick">{_e(o["ticker"])}</span>',
            detail,
            f'<span class="money">{rp(o.get("rupiah"))}</span>',
            _stop_cell(o),
            why,
        ])

    table = _table(["Do", "Ticker", "How much", "Value", "Stop", "Why"],
                   rows, num_cols={3, 4})

    fee_bits = (
        f'Estimated cost {rp(fees.total)} ({fees.pct_of(capital):.2f}% of capital) '
        f'— buy {rp(fees.buy_fee)}, sell {rp(fees.sell_fee)}, stamp {rp(fees.stamp_duty)}.'
    )
    callouts = f'<div class="callout">{fee_bits}</div>'

    # What these buys have to GAIN before they are worth having made. "0.06% of
    # capital" reads as nothing; the same fees against the money actually deployed,
    # counting the sale that has not happened yet and the stamp on it, is the
    # number that turned a 75-point gain on one lot into a Rp3,446 loss.
    deployed = sum(o.get("rupiah") or 0.0 for o in orders
                   if o.get("action") == "BUY")
    if deployed > 0:
        round_trip = round_trip_cost(deployed, FeeConfig())
        callouts += (
            '<div class="callout"><strong>Break even on this book.</strong> '
            f'Buying {rp(deployed)} and later selling it costs about '
            f'{rp(round_trip)} in fees and stamp — so these positions have to gain '
            f'<strong>{round_trip / deployed * 100:.2f}%</strong> together before '
            f'you are square. Below that a winning trade still loses money.</div>'
        )
    # What the whole book loses if every stop fills at once. Per-position risk is
    # the number that sizes a trade; this is the one that decides whether you
    # survive a bad week, and four comfortable 1.5% positions is 6% of capital.
    if open_risk and open_risk.get("n_positions"):
        pct = open_risk["pct_of_capital"]
        heavy = exit_cfg is not None and pct > exit_cfg.max_position_risk_pct * 2
        callouts += (
            f'<div class="callout"'
            f'{" style=border-left-color:var(--warn)" if heavy else ""}>'
            f'<strong>If every stop below filled, you would lose '
            f'{rp(open_risk["total_rp"])}</strong> &mdash; {pct:.1f}% of your '
            f'capital, across {open_risk["n_positions"]} position'
            f'{"s" if open_risk["n_positions"] > 1 else ""}. That is the number '
            f'a bad week costs, and it is not the same as any one position looking '
            f'comfortable.'
            + (f' {open_risk["n_without_stop"]} position'
               f'{"s have" if open_risk["n_without_stop"] > 1 else " has"} no '
               f'measurable stop and {"are" if open_risk["n_without_stop"] > 1 else "is"} '
               f'not counted here.' if open_risk.get("n_without_stop") else "")
            + "</div>"
        )

    # Stamp is per DAY containing a sell, not per order. The exit ladder is the
    # one thing in this tool that deliberately creates extra sell events, so the
    # advice to batch them belongs next to it rather than in a footnote.
    sells = [o for o in orders if o["action"] == "SELL"]
    if len(sells) > 1:
        callouts += (
            '<div class="callout save"><strong>Sell these on the same day.</strong> '
            f'The Rp10,000 stamp is charged once per day containing a sale, not per '
            f'order, so putting all {len(sells)} through together costs one stamp '
            f'instead of {len(sells)} &mdash; '
            f'{rp((len(sells) - 1) * 10_000)} saved for doing nothing differently '
            f'except the timing.</div>'
        )

    for note in fees.notes:
        callouts += f'<div class="callout save"><strong>Fee tip.</strong> {_e(note)}</div>'
    return table + callouts


_EXIT_PILL = {"EXIT": "bad", "TRIM": "warn", "HOLD": "", "NO STOP": "warn"}


def _ladder_cell(plan) -> str:
    """
    The whole plan, rungs and runner, in one cell.

    Shown in full rather than only the next step: the point of a staged exit is
    that it is a plan you can see through to the end, and "trim 4 lot at 2,077"
    on its own does not tell you what happens to the other six.

    Each rung carries the cost of taking it ALONE, because that is the number the
    stamp makes surprising -- a Rp665,000 trim that costs Rp1,928 sharing a sell
    day costs Rp11,928 on its own.
    """
    if not plan.stages:
        return '<span class="note">-</span>'
    if not plan.staged:
        s = plan.stages[0]
        return (f'<span class="note">{plan.lots} lot is too small to stage &mdash; '
                f'one decision, at {rp(s.level_rp)}</span>')

    out = ""
    for s in plan.stages:
        mark = "&#10003; " if s.done else ""
        cls = "note" if s.done else "money"
        out += (f'<div><span class="{cls}">{mark}+{s.r_multiple:g}R {rp(s.level_rp)}'
                f'</span> <span class="note">trim {s.lots} lot, '
                f'costs {rp(s.cost_alone_rp)} alone</span></div>')
    if plan.runner_lots:
        out += (f'<div><span class="note">then {plan.runner_lots} lot runs on the '
                f'trailing stop until it is hit &mdash; that is how you get fully '
                f'out</span></div>')
    return out


def _exit_section(plans: Dict[str, object], exit_cfg=None) -> str:
    """
    What would take you out of everything you hold.

    This is the panel the tool did not have. `build_orders` only ever proposed a
    sale when a name fell out of the target book on a re-rank, so a position could
    halve in value with nothing on the page mentioning it.
    """
    k = getattr(exit_cfg, "k_atr", 2.5)
    how = (
        '<div class="callout"><strong>How these levels are set.</strong> Every stop '
        f'is {k:g} &times; that name\'s own average daily range, never a fixed '
        f'percentage &mdash; the same {k * 2.7:.0f}% stop is an ordinary fortnight '
        f'for one IDX stock and a coin flip for another. It is also what makes this '
        f'adapt: when the market turns violent the daily range grows and every stop '
        f'widens with it, without a setting to change.</div>'
        '<div class="callout"><strong>This tool cannot watch these for you.</strong> '
        'It reads daily closing prices and has no live feed, so a level is checked '
        'once per session against the close &mdash; not intraday. Place the stop in '
        'Indopremier if you want it to act while you are not looking.</div>'
    )

    # The explanation stays even with nothing held, because the ticket's BUY rows
    # already carry stops computed the same way -- and that is exactly when a
    # reader wants to know where the number came from.
    if not plans:
        return ('<div class="empty">Nothing open. Exit levels appear here once you '
                "record a purchase &mdash; until then the stops on the buy rows in "
                "<strong>Do this today</strong> are what these rules would set."
                "</div>" + how)

    rows = []
    for plan in sorted(plans.values(), key=lambda p: (p.action != "EXIT",
                                                      p.action != "TRIM",
                                                      p.ticker)):
        pill = _EXIT_PILL.get(plan.action, "")
        verb = plan.action
        if plan.action == "TRIM":
            verb = f"TRIM {plan.action_lots} lot"
        elif plan.action == "EXIT":
            verb = f"SELL all {plan.action_lots}"

        stop = '<span class="note">none</span>'
        if plan.stop_rp:
            stop = (f'<span class="money">{rp(plan.stop_rp)}</span>'
                    f'<br><span class="note">{_e(plan.stop_kind)}'
                    + (f", {plan.to_stop_pct:+.1f}% away" if plan.to_stop_pct is not None
                       else "") + "</span>")
            if plan.stop_capped:
                stop += '<br><span class="pill warn">capped</span>'

        risk = '<span class="note">-</span>'
        if plan.risk_rp is not None:
            risk = (f'<span class="money">{rp(plan.risk_rp)}</span>'
                    + (f'<br><span class="note">{plan.risk_pct_of_capital:.2f}% of '
                       f'capital</span>' if plan.risk_pct_of_capital is not None else ""))

        held = f'{plan.lots} lot'
        if plan.original_lots > plan.lots:
            held += f'<br><span class="note">of {plan.original_lots} bought</span>'

        rows.append([
            f'<span class="tick">{_e(plan.ticker)}</span>',
            held,
            f'<span class="money">{rp(plan.price_rp)}</span>'
            + (f'<br><span style="color:var(--{"good" if plan.unrealized_pct >= 0 else "bad"})">'
               f'{plan.unrealized_pct:+.1f}%</span>' if plan.unrealized_pct is not None else ""),
            stop,
            risk,
            _ladder_cell(plan),
            f'<span class="pill {pill}">{_e(verb)}</span>'
            f'<br><span class="note">{_e(plan.reason)}</span>'
            + "".join(f'<br><span class="pill warn">{_e(n)}</span>' for n in plan.notes),
        ])

    table = _table(["Ticker", "Held", "Now", "Stop", "At risk", "The plan", "Today"],
                   rows, num_cols={2, 3, 4})

    return table + how + (
        '<div class="callout" style="border-left-color:var(--warn)">'
        '<strong>What the ladder does and does not do.</strong> Measured over 1,705 '
        'simulated entries on this universe, a target one risk-unit above entry '
        'arrived 38.6% of the time and the stop first 37.4% &mdash; close to a coin '
        'flip, which is what a random walk implies. Trimming in stages narrows the '
        'spread of outcomes; it does not create return. What it buys is a smaller '
        'worst case, and whether that is worth the return it costs has depended on '
        'both the universe and how often you re-rank &mdash; the same rule lost at '
        'every cadence on the older, narrower ticker list. Setting '
        '<code>risk.ladder: []</code> in <code>configs/user.yaml</code> keeps every '
        'stop below and drops the trims. Run <code>python main.py --backtest</code> '
        'to see the table on your own data rather than trusting this sentence.</div>'
    )


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
    ledger_html: str = "",
    trade_form_html: str = "",
    cash_form_html: str = "",
    dividend_form_html: str = "",
    placeholder_capital: bool = False,
    market: Optional[dict] = None,
    generated: Optional[datetime] = None,
    perf=None,
    fetched_at=None,
    sessions: Optional[dict] = None,
    verdict: Optional[dict] = None,
    book_correlation: Optional[float] = None,
    exit_plans: Optional[Dict[str, object]] = None,
    open_risk: Optional[dict] = None,
    exit_cfg=None,
) -> str:
    """
    The terminal. One document, five destinations, nothing scrolls but panels.

    The ticket is the first panel of the first page, in the DOM before anything
    else, and it keeps that place when it says HOLD or is empty. Ten panels of
    z-scores can make it feel as though something must be done today; the honest
    answer is usually that nothing must, and an empty ticket is a result.
    """
    when = (generated or datetime.now()).strftime("%a %d %b %Y, %H:%M")

    sessions = sessions or {}
    session_date = sessions.get("session_date")
    behind = bool(sessions.get("behind"))

    # Every one of these describes the PROPOSED ticket, not the account. "Cash left"
    # read as money you have; it is what would remain if you executed the buys below.
    # Labelled so the two cannot be confused -- the account's own figures are in the
    # top bar and on the Portfolio page.
    kpis = "".join([
        _kpi("Paid in", rp(capital)),
        _kpi("Deploy", f"{regime.deploy_pct:.0%}"),
        _kpi("Positions", str(allocation.n_positions if allocation else 0)),
        _kpi("After these buys", rp(allocation.cash_left if allocation else capital)),
        _kpi("Fees on these buys", rp(fees.total)),
    ])

    # The failure this exists for: a run on the shipped placeholder produced a
    # confident ticket to buy Rp30 juta of stock, and nothing said the figure was
    # not the reader's money. A wrong number stated confidently is worse than none.
    placeholder_note = ""
    if placeholder_capital:
        placeholder_note = (
            '<div class="callout" style="border-left-color:var(--bad)">'
            '<strong>This is the placeholder capital, not your money.</strong> '
            f"Every lot count below is sized for {rp(capital)}. Set your real figure "
            'in <strong>Settings</strong>, or in <code>configs/user.yaml</code>, then '
            "press Rebuild.</div>"
        )

    # Behind the market: the lot counts stay, because a day-old ranking is still
    # mostly right over a one-to-two week hold and a vendor hiccup should not leave
    # you with nothing at lunchtime. But the price each one was sized on is named,
    # because that is the number the order will be wrong against.
    stale_note = ""
    if behind and session_date is not None:
        market_session = sessions.get("market_session")
        traded = (f", and the market has since traded "
                  f"{pd.Timestamp(market_session):%a %d %b}"
                  if market_session is not None else "")
        stale_note = (
            '<div class="callout" style="border-left-color:var(--bad)">'
            f"<strong>Prices are from the {pd.Timestamp(session_date):%a %d %b} "
            f"close{traded}.</strong> Every lot count below is sized on those "
            "prices, so the rupiah amounts will not match a live order. Check the "
            "price in Indopremier before you send anything.</div>"
        )

    # Ranking here is cross-sectional -- each name scored against its peers -- so
    # names priced on different days are not actually being compared.
    mixed_note = ""
    laggards = sessions.get("laggards") or []
    if laggards:
        shown = ", ".join(f"{t} ({d})" for t, d in laggards[:5])
        more = f" and {len(laggards) - 5} more" if len(laggards) > 5 else ""
        mixed_note = (
            '<div class="callout" style="border-left-color:var(--warn)">'
            f"<strong>{len(laggards)} of {universe_n} names are priced on an older "
            f"session than the rest.</strong> Every score is a comparison against "
            f"peers, so these are not being ranked on the same day as the others: "
            f"{html.escape(shown)}{more}.</div>"
        )

    # How much of a single bet this book really is, measured rather than assumed
    # from sector names. Three tickers in three sectors can still be one trade
    # (BRPT/PTRO correlate 0.87), and three commodity names often are not.
    concentration = ""
    if book_correlation is not None and allocation and len(allocation.positions) > 1:
        tight = book_correlation >= 0.60
        concentration = (
            f'<div class="callout"'
            f'{" style=border-left-color:var(--warn)" if tight else ""}>'
            f"<strong>These {len(allocation.positions)} names move together "
            f"{book_correlation:.2f}.</strong> "
            + ("Close to one bet in several tickers &mdash; they will rise and fall "
               "as a group, so the diversification here is smaller than the count "
               "suggests." if tight else
               "Low enough that they are genuinely separate positions.")
            + "</div>"
        )

    # Names asked for and never received. Every score is cross-sectional, so a
    # name dropping out silently changes the peer group every other name is
    # measured against -- and the page would go on naming a universe it did not
    # actually screen.
    missing_note = ""
    missing = sessions.get("missing") or []
    if missing:
        missing_note = (
            '<div class="callout" style="border-left-color:var(--warn)">'
            f"<strong>{len(missing)} name"
            f"{'s' if len(missing) > 1 else ''} could not be fetched</strong> and "
            f"{'are' if len(missing) > 1 else 'is'} absent from the ranking below: "
            f"{_e(', '.join(missing))}. Every score here is a comparison against "
            f"peers, so the rest were ranked against a smaller group than usual."
        ) + "</div>"

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
                    stale_note + placeholder_note
                    + _ticket_section(orders, fees, capital, open_risk, exit_cfg)
                    + granularity + missing_note + concentration
                    + evidence_note(verdict),
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
            # Two questions about the same rows, and they are never asked at the
            # same moment. "When do I get out" is the standing plan you check
            # every day; "is this still worth owning" is a monthly thought. The
            # exit plan is the active tab because it is the one with a level on it
            # that today's price can already have passed.
            T.panel("What you hold",
                    layout.tabbed(
                        [("Exit plan", _exit_section(exit_plans or {}, exit_cfg)),
                         ("Worth & health", _holdings_section(holdings_rows))],
                        group="holdings"),
                    grow=True),
        ]),
        T.column([
            T.panel("Best candidates you can afford",
                    mixed_note + _candidates_section(candidates), grow=True),
            T.panel("Skipped", _rejected_section(rejected, capped, compact=True)),
        ]),
    ])

    # One panel, three tabs -- not three stacked panels. `.col` is
    # `overflow:hidden` and `.pnl` is `flex:none`, so a column taller than the
    # viewport is silently CLIPPED: adding the cash and dividend forms pushed the
    # dividend form half off the bottom and "How you are doing" entirely off it,
    # with nothing on screen suggesting either existed. Recording a trade, a
    # deposit and a dividend are never the same moment anyway.
    record = layout.tabbed(
        [("Trade", trade_form_html),
         ("Cash in / out", cash_form_html),
         ("Dividend", dividend_form_html)],
        group="record",
    )

    portfolio = T.grid([
        T.column([
            T.panel("Record what you did", record, pid="panel-trade"),
            T.panel("How you are doing",
                    journal_html or '<div class="empty">Nothing logged yet.</div>',
                    grow=True),
        ]),
        T.column([
            T.panel("Your ledger", f'<div id="ledger">{ledger_html}</div>',
                    pid="panel-ledger", grow=True),
        ]),
    ])

    pages = [
        T.Page("markets", "Markets", "markets", markets, "Today's decision"),
        T.Page("portfolio", "Portfolio", "portfolio", portfolio, "What you own"),
    ]
    if advanced_html:
        pages.append(T.Page(
            "screener", "Screener", "screener",
            T.grid([T.column([T.panel("The whole universe", advanced_html, grow=True)])]),
            # From the frame, never a literal. This said "All 49 names" for a day
            # after the universe grew to 74 -- a hardcoded count is a claim that
            # goes quietly wrong the moment the thing it counts changes.
            f"All {universe_n} names and the evidence"))
    if steps_html:
        pages.append(T.Page(
            "why", "Why", "why",
            T.grid([T.column([T.panel("How today's picks were chosen",
                                      steps_html, grow=True)])]),
            "The decision, stage by stage"))
    pages.append(T.Page(
        "settings", "Settings", "settings",
        T.grid([
            T.column([T.panel(
                "Change a setting",
                '<div id="settings-editor"><div class="empty">Editing needs the app '
                "window. Opened as a file, this page shows the values but cannot "
                "change them &mdash; edit <code>configs/user.yaml</code> instead."
                "</div></div>"
                '<div class="note">Edits are written to <code>configs/user.yaml</code>, '
                "never to <code>default.yaml</code>, so the shipped defaults stay "
                "recoverable and a bad change is one deleted file away from fixed."
                "</div>",
                pid="panel-settings-editor")]),
            T.column([T.panel("What is driving the rules",
                              (settings_html or "") + disclaimer, grow=True)]),
        ]),
        "The numbers behind every gate"))

    regime_kind = {"RISK-ON": "good", "RISK-OFF": "bad"}.get(regime.label, "warn")

    # The top bar states facts about YOUR account. It used to show the ticket's
    # leftover cash and the ticket's fees, which are properties of a suggestion you
    # have not taken -- sitting under headings that read as your balance.
    stats = [(regime.label, f"deploy {regime.deploy_pct:.0%}", regime_kind)]
    if perf is not None:
        stats.append(("Cash", rp(perf.cash), ""))
        stats.append(("Holdings", rp(perf.position_value), ""))
    else:
        stats.append(("Paid in", rp(capital), ""))

    # WHICH SESSION the prices are from, first. "data as of Tue 25 Aug 01:44" was
    # the moment we asked, and every price under it was the 21 August close --
    # nothing on the page said so, and that one line is why a whole screen of
    # stale prices looked current. Fetch time stays, because it answers the other
    # question, but it no longer stands in for this one.
    subtitle = f"as of {when} · {universe_n} names screened"
    if session_date is not None:
        stamp = pd.Timestamp(session_date)
        subtitle = f"prices from {stamp:%a %d %b} close · {universe_n} names"
        if fetched_at is not None:
            subtitle += f" · fetched {pd.Timestamp(fetched_at):%H:%M}"
        if behind:
            subtitle += " · BEHIND THE MARKET"
    elif fetched_at is not None:
        stamp = pd.Timestamp(fetched_at)
        age_h = (pd.Timestamp.now() - stamp).total_seconds() / 3600
        freshness = "" if age_h < 24 else f" · {int(age_h // 24)}d old, press Update data"
        subtitle = (f"data as of {stamp:%a %d %b, %H:%M} · {universe_n} names"
                    f"{freshness} · redrawn {when.split(', ')[-1]}")

    fetch_stale = fetched_at is not None and (
        pd.Timestamp.now() - pd.Timestamp(fetched_at)).total_seconds() > 86400

    top = T.topbar(
        "IDX Terminal", subtitle, stats,
        placeholder_capital=placeholder_capital,
        stale=behind or (session_date is None and fetch_stale),
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
