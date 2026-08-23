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
/* Wide enough to put things side by side. The old 940px cap left almost half a
   1920px screen empty while the reader scrolled past content that would have fitted
   beside itself. Prose is capped separately below -- the extra width is for layout,
   not for longer lines of text. */
.wrap{max-width:min(1580px,96vw);margin:0 auto}
.card p,.card .note,.callout{max-width:74ch}
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

/* --- Simple / Steps / Advanced -----------------------------------------
   The whole switch. One attribute on <body> decides which parts of the page
   exist; there is no second file and no re-render, so the modes cannot drift
   apart. Simple content is never hidden -- the other modes add to it rather
   than replacing it, so the decision is always on screen. */
body[data-mode="simple"] .adv,
body[data-mode="simple"] .steps{display:none}
body[data-mode="steps"] .adv{display:none}
body[data-mode="advanced"] .steps{display:none}

/* Sticky, because with 16+ sections the toggle used to scroll away and become
   unreachable exactly when you needed to switch. */
nav.modes{display:flex;gap:6px;margin:14px 0 2px;position:sticky;top:0;z-index:5;
  background:var(--bg);padding:10px 0 8px;flex-wrap:wrap;
  border-bottom:1px solid var(--line)}
nav.modes button{font:inherit;font-size:13px;font-weight:600;cursor:pointer;
  padding:7px 16px;border-radius:999px;border:1px solid var(--line);
  background:var(--surface);color:var(--muted)}
nav.modes button[aria-pressed="true"]{background:var(--accent);border-color:transparent;color:#fff}
nav.modes .hint{align-self:center;margin-left:6px;color:var(--muted);font-size:12.5px}

.chart{width:100%;height:auto;display:block;margin:8px 0}
th.sortable{cursor:pointer;user-select:none;white-space:nowrap}
th.sortable:hover{color:var(--ink);text-decoration:underline}
th.sortable[data-dir]::after{content:" \\2193";font-weight:400}
th.sortable[data-dir="asc"]::after{content:" \\2191"}
.whatif-controls{display:flex;gap:14px;flex-wrap:wrap;margin:4px 0 14px}
.whatif-controls label{font-size:13px;color:var(--muted);display:flex;
  align-items:center;gap:6px}
.whatif-controls select{font:inherit;font-size:13px;padding:5px 8px;
  border-radius:8px;border:1px solid var(--line);background:var(--surface);color:var(--ink)}

/* --- Tabs ---------------------------------------------------------------
   Nine Advanced sections are never useful at the same moment. One on screen at
   a time turned an eight-screen scroll into eight one-screen panels. */
.tabs{display:flex;gap:5px;flex-wrap:wrap;margin:14px 0 12px;
  border-bottom:1px solid var(--line);padding-bottom:9px}
button.tab{font:inherit;font-size:13px;font-weight:600;cursor:pointer;
  padding:6px 13px;border-radius:999px;border:1px solid var(--line);
  background:var(--surface);color:var(--muted)}
button.tab:hover{color:var(--ink)}
button.tab.on{background:var(--ink);border-color:transparent;color:var(--bg)}
.panel{display:none}
.panel.on{display:block}
/* A 50-row table would make its panel three screens tall, which is the scrolling
   this layout exists to remove. Cap it and let the table scroll inside its own
   pane: bounded and local beats the whole page moving. Simple is left uncapped --
   it is short enough already, and its tables are the thing you came to read. */
.panel .scroll{max-height:68vh;overflow:auto}

/* --- Simple as a dashboard ----------------------------------------------
   Two columns on a wide screen, one below 1000px so nothing is lost on a phone.
   The ticket takes the wide column because it is the only thing here you act on. */
.dash{display:grid;gap:14px;grid-template-columns:minmax(0,1.55fr) minmax(0,1fr);
  align-items:start}
.dash>.full{grid-column:1/-1}
.dash h2{margin:0 0 8px}
.dash .card{margin-bottom:0}
@media (max-width:1000px){
  .dash{grid-template-columns:1fr}
  .dash>.full{grid-column:auto}
}
.dash-col{display:flex;flex-direction:column;gap:14px;min-width:0}
.dash-col>section:empty{display:none}

/* --- Folded blocks ------------------------------------------------------- */
.fold>summary{cursor:pointer;font-size:15px;font-weight:600;color:var(--muted);
  padding:9px 0;list-style:none}
.fold>summary::-webkit-details-marker{display:none}
.fold>summary::before{content:"\\25B8 ";color:var(--muted)}
.fold[open]>summary::before{content:"\\25BE "}
.fold>summary:hover{color:var(--ink)}

/* --- The funnel --------------------------------------------------------- */
.funnel{display:flex;flex-direction:column;gap:3px;margin:14px 0}
.funnel-row{display:grid;grid-template-columns:22px minmax(96px,1.1fr) 3fr 34px 34px;
  gap:9px;align-items:center;padding:6px 8px;border-radius:8px;
  text-decoration:none;color:var(--ink)}
.funnel-row:hover{background:var(--surface-alt)}
.funnel-n{font-size:11.5px;font-weight:700;color:var(--muted);
  text-align:center;font-variant-numeric:tabular-nums}
.funnel-title{font-size:13.5px}
.funnel-bar{background:var(--surface-alt);border-radius:999px;height:11px;overflow:hidden}
.funnel-bar>span{display:block;height:100%;background:var(--accent);
  border-radius:999px;min-width:2px}
.funnel-out{font-weight:700;font-variant-numeric:tabular-nums;text-align:right;font-size:13.5px}
.funnel-drop{color:var(--bad);font-size:12px;font-variant-numeric:tabular-nums;text-align:right}

/* --- Stage cards -------------------------------------------------------- */
.step-head{display:flex;align-items:center;gap:11px;margin-bottom:6px}
.step-head h3{margin:0;font-size:17px;letter-spacing:-.01em}
.step-n{display:flex;align-items:center;justify-content:center;flex:none;
  width:26px;height:26px;border-radius:999px;background:var(--accent);color:#fff;
  font-size:13px;font-weight:700}
.step-count{margin:12px 0;font-size:15px;font-variant-numeric:tabular-nums}
.step-count strong{font-size:19px}
.step{scroll-margin-top:64px}

/* --- Per-stock trace ---------------------------------------------------- */
.trace-box{display:flex;gap:9px;align-items:center;flex-wrap:wrap;margin:6px 0 14px}
.trace-box label{font-size:13px;color:var(--muted)}
.trace-box input{font:inherit;font-size:14px;padding:8px 11px;border-radius:9px;
  border:1px solid var(--line);background:var(--surface);color:var(--ink);min-width:210px}
.trace-step{display:grid;grid-template-columns:26px 1fr;gap:11px;
  padding:8px 0;border-bottom:1px solid var(--line)}
.trace-step:last-child{border-bottom:none}
.trace-mark{font-weight:700;text-align:center}
.trace-mark.pass{color:var(--good)}
.trace-mark.stop{color:var(--bad)}
.trace-mark.na{color:var(--muted)}

/* Print the ticket, not the research. */
@media print{
  nav.modes,.adv,.steps{display:none !important}
  body{padding:0}
  .card{break-inside:avoid}
}
"""

# Kept out of _CSS so the f-string in render_brief never has to escape braces.
# Three jobs, all local to the page: flip the mode, sort a table, read the
# precomputed what-if grid. No fetch, no dependency, nothing that needs a server.
_JS = """
(function(){
  var body=document.body,KEY="idx-brief-mode",MODES=["simple","steps","advanced"];
  var btns=[].slice.call(document.querySelectorAll("nav.modes button"));
  function setMode(m){
    body.setAttribute("data-mode",m);
    btns.forEach(function(b){b.setAttribute("aria-pressed",String(b.dataset.mode===m));});
    try{localStorage.setItem(KEY,m);}catch(e){}
  }
  var saved=null;
  try{saved=localStorage.getItem(KEY);}catch(e){}
  setMode(MODES.indexOf(saved)>=0?saved:"simple");
  btns.forEach(function(b){b.addEventListener("click",function(){setMode(b.dataset.mode);});});

  // Tabs. Each strip owns the panels its buttons name, so two strips on the page
  // never fight over the same panel. The choice is remembered per group.
  [].forEach.call(document.querySelectorAll(".tabs"),function(strip){
    var group=strip.dataset.group||"tabs";
    var tabs=[].slice.call(strip.querySelectorAll("button.tab"));
    if(!tabs.length) return;
    function show(id,remember){
      tabs.forEach(function(t){
        var on=t.dataset.panel===id;
        t.classList.toggle("on",on);
        t.setAttribute("aria-selected",String(on));
        var p=document.getElementById(t.dataset.panel);
        if(p) p.classList.toggle("on",on);
      });
      if(remember){try{localStorage.setItem("idx-tab-"+group,id);}catch(e){}}
    }
    tabs.forEach(function(t,i){
      t.addEventListener("click",function(){show(t.dataset.panel,true);});
      // Arrow keys move along the strip, which is what a tablist is expected to do.
      t.addEventListener("keydown",function(e){
        var d=e.key==="ArrowRight"?1:(e.key==="ArrowLeft"?-1:0);
        if(!d) return;
        e.preventDefault();
        var n=tabs[(i+d+tabs.length)%tabs.length];
        show(n.dataset.panel,true); n.focus();
      });
    });
    var want=null;
    try{want=localStorage.getItem("idx-tab-"+group);}catch(e){}
    // Only restore a tab this strip actually owns -- a remembered id from an older
    // version of the page would otherwise hide every panel and blank the section.
    if(want&&tabs.some(function(t){return t.dataset.panel===want;})) show(want,false);
  });

  // Per-stock trace: look up any name in the universe and see where it stopped.
  var traceRaw=document.getElementById("trace-data");
  if(traceRaw){
    var trace=JSON.parse(traceRaw.textContent);
    var q=document.getElementById("trace-q"), tout=document.getElementById("trace-out");
    var esc=function(s){var d=document.createElement("div");d.textContent=s;return d.innerHTML;};
    var MARK={passed:["pass","✓"],dropped:["stop","✕"],not_reached:["na","·"]};
    function lookup(){
      var key=(q.value||"").trim().toUpperCase();
      if(!key){tout.innerHTML="";return;}
      var hit=trace.names[key]||trace.names[key+".JK"];
      if(!hit){
        tout.innerHTML='<div class="empty">'+esc(q.value)+
          " is not in the universe, so it was never considered.</div>";
        return;
      }
      var rows=hit.rows.map(function(r){
        var m=MARK[r.status]||MARK.not_reached;
        var detail=r.detail?'<div class="note">'+esc(r.detail)+"</div>":"";
        return '<div class="trace-step"><div class="trace-mark '+m[0]+'">'+m[1]+
               '</div><div>'+esc(r.title)+detail+"</div></div>";
      }).join("");
      tout.innerHTML='<div class="card"><div><span class="tick">'+esc(key)+
        '</span></div>'+rows+'<div class="callout">'+esc(hit.outcome)+"</div></div>";
    }
    q.addEventListener("input",lookup);
    q.addEventListener("change",lookup);
  }

  // Click-to-sort. Values come from each cell's data-v, so sorting uses the
  // underlying number rather than the formatted "Rp1,234" string.
  [].forEach.call(document.querySelectorAll("table.sortable-table"),function(tbl){
    var heads=[].slice.call(tbl.querySelectorAll("th.sortable"));
    heads.forEach(function(th){
      th.addEventListener("click",function(){
        var col=+th.dataset.col, dir=th.dataset.dir==="asc"?"desc":"asc";
        heads.forEach(function(h){h.removeAttribute("data-dir");});
        th.dataset.dir=dir;
        var tb=tbl.tBodies[0], rows=[].slice.call(tb.rows);
        rows.sort(function(a,b){
          var x=a.cells[col].dataset.v, y=b.cells[col].dataset.v;
          var nx=parseFloat(x), ny=parseFloat(y);
          var both=!isNaN(nx)&&!isNaN(ny);
          var cmp=both?(nx-ny):String(x).localeCompare(String(y));
          return dir==="asc"?cmp:-cmp;
        });
        rows.forEach(function(r){tb.appendChild(r);});
      });
    });
  });

  // What-if: a lookup into a table computed at render time.
  var raw=document.getElementById("wi-data");
  if(raw){
    var grid=JSON.parse(raw.textContent), out=document.getElementById("wi-out");
    var cap=document.getElementById("wi-cap"),
        nsel=document.getElementById("wi-n"),
        dep=document.getElementById("wi-dep");
    var rp=function(v){return "Rp"+Math.round(v).toLocaleString("en-US");};
    function draw(){
      var cell=grid.cells[cap.value+"|"+nsel.value+"|"+dep.value];
      if(!cell){out.innerHTML='<div class="empty">No workable book at that setting.</div>';return;}
      // Everything from the payload is injected as HTML, and it carries ticker
      // names that came from a config file. Escape it here for the same reason
      // the Python side escapes: the source being "ours" is not a guarantee.
      var esc=function(s){var d=document.createElement("div");d.textContent=s;return d.innerHTML;};
      var rows=cell.pos.map(function(p){
        return "<tr><td><span class='tick'>"+esc(p.t)+"</span></td>"+
               "<td class='num'>"+esc(p.l)+" lot</td>"+
               "<td class='num'>"+rp(p.r)+"</td></tr>";
      }).join("");
      var shortfall=cell.short?'<div class="callout"><strong>Lot sizes bind here.</strong> '+esc(cell.short)+'</div>':"";
      out.innerHTML=
        '<div class="kpis">'+
        '<div class="kpi"><div class="k">Positions</div><div class="v">'+cell.n+'</div></div>'+
        '<div class="kpi"><div class="k">Deployed</div><div class="v">'+(cell.deployed*100).toFixed(0)+'%</div></div>'+
        '<div class="kpi"><div class="k">Cash left</div><div class="v">'+rp(cell.cash)+'</div></div>'+
        '<div class="kpi"><div class="k">Worst weight gap</div><div class="v">'+(cell.err*100).toFixed(1)+'pp</div></div>'+
        '<div class="kpi"><div class="k">Est. fees</div><div class="v">'+rp(cell.fees)+'</div></div>'+
        '</div>'+shortfall+
        (rows?'<div class="scroll"><table><thead><tr><th>Ticker</th><th class="num">Size</th><th class="num">Value</th></tr></thead><tbody>'+rows+'</tbody></table></div>'
             :'<div class="empty">Nothing is affordable at that setting.</div>');
    }
    [cap,nsel,dep].forEach(function(el){el.addEventListener("change",draw);});
    draw();
  }
})();
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
    generated: Optional[datetime] = None,
) -> str:
    when = (generated or datetime.now()).strftime("%A, %d %B %Y %H:%M")

    # Each button only appears when there is something behind it. A dead switch is
    # worse than no switch.
    nav = ""
    if advanced_html or steps_html:
        buttons = ('<button type="button" data-mode="simple" '
                   'aria-pressed="true">Simple</button>')
        if steps_html:
            buttons += ('<button type="button" data-mode="steps" '
                        'aria-pressed="false">Steps</button>')
        if advanced_html:
            buttons += ('<button type="button" data-mode="advanced" '
                        'aria-pressed="false">Advanced</button>')
        hint = ("Simple is the decision, Steps is how it got there, Advanced is the "
                "evidence." if steps_html and advanced_html
                else "Simple is the decision. Advanced is the evidence.")
        # No jump list any more. It existed to make sixteen stacked sections
        # navigable; Simple now fits a screen and the other two are tabbed, so a
        # dropdown listing the one visible heading would be worse than nothing.
        nav = (
            '<nav class="modes">'
            f"{buttons}"
            f'<span class="hint">{hint}</span>'
            "</nav>"
        )

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
<body data-mode="simple">
<div class="wrap">
<header>
  <h1>Your IDX brief</h1>
  <div class="sub">{_e(when)} &middot; {universe_n} stocks screened</div>
</header>
{nav}

<div class="dash">

  <section class="full">
    <h2>Market right now</h2>
    {_verdict_card(regime)}
    {f'<div class="callout"><strong>Seasonality.</strong> {_e(seasonality)}</div>' if seasonality else ''}
  </section>

  <section class="full"><div class="kpis">{kpis}</div></section>

  <div class="dash-col">
    {_block("Do this today", _ticket_section(orders, fees, capital) + granularity)}
  </div>

  <div class="dash-col">
    {_block("What you hold", _holdings_section(holdings_rows))}
    <section>{_events_section(events or [], blind_n, universe_n, event_horizon)}</section>
    <section>{journal_html}</section>
  </div>

  {_block("Best candidates you can actually afford", _candidates_section(candidates), cls="full")}

  <section class="full">{_rejected_section(rejected, capped, compact=True)}</section>

</div>

{steps_html}

{advanced_html}

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
<script>{_JS}</script>
</body>
"""


def write_brief(html_text: str, output_dir: Path, filename: str = "brief.html") -> Path:
    out = Path(output_dir) / filename
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html_text, encoding="utf-8")
    return out
