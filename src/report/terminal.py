# src/report/terminal.py
"""
The terminal shell: rail, panels, ticker bar.

Three layout attempts before this one were documents -- a column of sections you
scrolled. This is a terminal: a left rail of destinations, a dense grid of panels,
and nothing that moves except the inside of a panel.

The one structural rule: **`html, body { overflow: hidden }`**. The page cannot
scroll. Every panel body is its own scroll container, so the ticket stays where you
left it while you read down a 49-row table beside it. Below 900px that is lifted and
normal page scrolling comes back, because a phone has no room for panels.

Everything here is a pure string function, like the rest of `src/report/`, so the
whole shell is testable without a browser. It holds no data and computes nothing
about a stock -- it is furniture.
"""
from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple


def _e(v) -> str:
    return html.escape("" if v is None else str(v))


def _slug(v: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(v).lower()).strip("-") or "x"


# ---------------------------------------------------------------- rail icons
# Inline SVG rather than an icon font: the page must stay self-contained, and a
# font file would be either a network request or 100KB of base64.
_ICONS = {
    "markets": "M3 17l5-6 4 4 5-8M3 21h18",
    "portfolio": "M3 7h18v13H3zM8 7V5a2 2 0 012-2h4a2 2 0 012 2v2",
    "screener": "M3 4h18l-7 8v7l-4 2v-9z",
    "why": "M6 3v12a3 3 0 003 3h9M15 15l3 3-3 3",
    "settings": "M12 15a3 3 0 100-6 3 3 0 000 6zM19.4 15a1.6 1.6 0 00.3 1.8l.1.1a2 2 0"
                " 11-2.8 2.8l-.1-.1a1.6 1.6 0 00-2.8 1.1V21a2 2 0 11-4 0v-.1A1.6 1.6 0"
                " 006 19.4l-.1.1a2 2 0 11-2.8-2.8l.1-.1a1.6 1.6 0 00-1.1-2.8H2a2 2 0"
                " 110-4h.1A1.6 1.6 0 004.6 6l-.1-.1a2 2 0 112.8-2.8l.1.1a1.6 1.6 0"
                " 001.8.3h.1A1.6 1.6 0 0010 2.1V2a2 2 0 114 0v.1a1.6 1.6 0 002.7 1.1l.1-.1a2"
                " 2 0 112.8 2.8l-.1.1a1.6 1.6 0 00-.3 1.8v.1a1.6 1.6 0 001.5 1H21a2 2 0"
                " 110 4h-.1a1.6 1.6 0 00-1.5 1z",
}


def _icon(key: str) -> str:
    path = _ICONS.get(key, _ICONS["markets"])
    return (f'<svg viewBox="0 0 24 24" width="19" height="19" fill="none" '
            f'stroke="currentColor" stroke-width="1.7" stroke-linecap="round" '
            f'stroke-linejoin="round"><path d="{path}"/></svg>')


# ------------------------------------------------------------------ pieces
@dataclass
class Page:
    key: str
    label: str
    icon: str
    body: str
    hint: str = ""


def panel(title: str, body: str, *, tabs: str = "", tools: str = "",
          pid: str = "", cls: str = "", grow: bool = False) -> str:
    """
    One titled box with its own scrollbar.

    `grow` makes it take the leftover height in its column; without it a panel is
    only as tall as its content. Exactly one panel per column should grow, or they
    fight over the space.
    """
    if not body or not str(body).strip():
        return ""
    ident = f' id="{_e(pid)}"' if pid else ""
    head = (f'<div class="pnl-hd"><span class="pnl-ttl">{_e(title)}</span>'
            f"{tabs}{f'<span class=pnl-tools>{tools}</span>' if tools else ''}</div>")
    return (f'<section class="pnl{" grow" if grow else ""}{" " + cls if cls else ""}"{ident}>'
            f'{head}<div class="pnl-bd">{body}</div></section>')


def column(panels: Sequence[str], width: str = "") -> str:
    kept = [p for p in panels if p and p.strip()]
    if not kept:
        return ""
    style = f' style="--col:{width}"' if width else ""
    return f'<div class="col"{style}>{"".join(kept)}</div>'


def grid(columns: Sequence[str]) -> str:
    kept = [c for c in columns if c and c.strip()]
    return f'<div class="grid cols-{len(kept)}">{"".join(kept)}</div>'


def rail(pages: Sequence[Page], active: str = "") -> str:
    """
    The destination list. Every page must appear here: a page with no rail entry is
    unreachable, and unlike a scrolling document nothing on screen hints it exists.
    """
    if not pages:
        return ""
    active = active or pages[0].key
    items = ""
    for p in pages:
        on = " on" if p.key == active else ""
        items += (
            f'<button type="button" class="rail-item{on}" data-page="{_e(p.key)}" '
            f'aria-current="{"page" if p.key == active else "false"}" '
            f'title="{_e(p.hint or p.label)}">{_icon(p.icon)}'
            f'<span>{_e(p.label)}</span></button>'
        )
    return f'<nav class="rail" aria-label="Sections">{items}</nav>'


def topbar(title: str, subtitle: str, stats: Sequence[Tuple[str, str, str]] = ()) -> str:
    """
    Identity on the left, state on the right.

    Deliberately no BUY or SELL button. Stockbit's place real orders; a control that
    looks like it trades, in a tool that sits beside the real broker, is a hazard and
    not a feature. What sits here instead is the regime and how much to deploy --
    the state you need before reading anything else.

    The subtitle is a generation timestamp, never a running clock: a ticking clock
    over daily data claims a live feed this tool does not have.
    """
    chips = "".join(
        f'<span class="chip {_e(kind)}"><span class="chip-k">{_e(k)}</span>'
        f'<span class="chip-v">{v}</span></span>'
        for k, v, kind in stats
    )
    return (
        '<header class="topbar">'
        f'<div class="brand"><strong>{_e(title)}</strong>'
        f'<span class="asof">{_e(subtitle)}</span></div>'
        f'<div class="topstats">{chips}</div>'
        "</header>"
    )


def tickerbar(items: Sequence[Tuple[str, str, Optional[float]]]) -> str:
    """A single line of names and their gap to fair value. Static, not animated."""
    if not items:
        return ""
    out = ""
    for label, value, delta in items:
        cls = "" if delta is None else ("up" if delta >= 0 else "down")
        arrow = "" if delta is None else ("&#9650;" if delta >= 0 else "&#9660;")
        out += (f'<span class="tk"><span class="tk-n">{_e(label)}</span>'
                f'<span class="tk-v">{_e(value)}</span>'
                f'<span class="tk-d {cls}">{arrow}</span></span>')
    return f'<footer class="tickbar">{out}</footer>'


def pages_html(pages: Sequence[Page], active: str = "") -> str:
    active = active or (pages[0].key if pages else "")
    out = ""
    for p in pages:
        on = " on" if p.key == active else ""
        out += (f'<div class="page{on}" id="page-{_e(p.key)}" '
                f'role="tabpanel" aria-label="{_e(p.label)}">{p.body}</div>')
    return out


def document(*, title: str, head: str, rail_html: str, top_html: str,
             body_html: str, tick_html: str, css: str, js: str) -> str:
    """The whole file. One `<body data-page>` decides which destination is showing."""
    return f"""<!doctype html>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{_e(title)}</title>
<style>{css}</style>
<body data-page="{_e(head)}">
<div class="app">
{rail_html}
<div class="main">
{top_html}
{body_html}
{tick_html}
</div>
</div>
<script>{js}</script>
</body>
"""


# --------------------------------------------------------------------- theme
THEME_CSS = """
/* Dark first: the reference this is modelled on is a dark terminal, and a light
   trading screen reads as a spreadsheet. An explicit data-theme=light still wins. */
:root{
  --bg:#0a0e13; --surface:#11161d; --surface-2:#0d1218; --surface-3:#161d26;
  --ink:#d6dce4; --ink-dim:#98a3b0; --muted:#67727f; --line:#1e2630;
  --accent:#2f7fe0; --good:#16c784; --bad:#ea3943; --warn:#e0a137;
  --good-bg:#0e2b20; --bad-bg:#2c1417; --warn-bg:#2a2011;
}
:root[data-theme="light"]{
  --bg:#f5f6f8; --surface:#fff; --surface-2:#eceef2; --surface-3:#f0f2f5;
  --ink:#1a1d21; --ink-dim:#41474f; --muted:#6b7480; --line:#dcdfe4;
  --accent:#2563c9; --good:#0f8f5f; --bad:#c62b36; --warn:#8a5a00;
  --good-bg:#e6f4ec; --bad-bg:#fbe9e7; --warn-bg:#fdf3e0;
}
*{box-sizing:border-box}
html,body{height:100%;margin:0;overflow:hidden}
body{background:var(--bg);color:var(--ink);
  font:12.5px/1.45 "Segoe UI",-apple-system,BlinkMacSystemFont,Roboto,sans-serif;
  -webkit-font-smoothing:antialiased}

/* ---- shell ---- */
.app{height:100%;display:grid;grid-template-columns:74px minmax(0,1fr)}
.main{display:grid;grid-template-rows:auto minmax(0,1fr) auto;min-width:0;min-height:0}

.rail{background:var(--surface-2);border-right:1px solid var(--line);
  display:flex;flex-direction:column;gap:2px;padding:8px 5px;overflow:auto}
.rail-item{display:flex;flex-direction:column;align-items:center;gap:4px;width:100%;
  padding:9px 2px;border:0;border-radius:7px;background:transparent;color:var(--muted);
  font:inherit;font-size:9.5px;font-weight:600;letter-spacing:.02em;cursor:pointer}
.rail-item:hover{background:var(--surface-3);color:var(--ink)}
.rail-item.on{background:var(--surface-3);color:var(--accent)}

.topbar{display:flex;align-items:center;gap:16px;flex-wrap:wrap;
  padding:8px 13px;border-bottom:1px solid var(--line);background:var(--surface-2)}
.brand strong{font-size:13.5px;letter-spacing:-.01em}
.brand .asof{margin-left:9px;color:var(--muted);font-size:11px}
.topstats{display:flex;gap:7px;flex-wrap:wrap;margin-left:auto}
.chip{display:flex;align-items:center;gap:6px;padding:4px 10px;border-radius:6px;
  background:var(--surface-3);border:1px solid var(--line);font-size:11.5px}
.chip-k{color:var(--muted);text-transform:uppercase;letter-spacing:.05em;font-size:9.5px}
.chip-v{font-weight:700;font-variant-numeric:tabular-nums}
.chip.good .chip-v{color:var(--good)} .chip.bad .chip-v{color:var(--bad)}
.chip.warn .chip-v{color:var(--warn)}

.page{display:none;min-height:0;overflow:hidden}
.page.on{display:block;min-height:0}

.grid{display:grid;gap:8px;padding:8px;height:100%;min-height:0;
  grid-template-columns:minmax(0,360px) minmax(0,1fr) minmax(0,1fr)}
.grid.cols-1{grid-template-columns:minmax(0,1fr)}
.grid.cols-2{grid-template-columns:minmax(0,420px) minmax(0,1fr)}
.col{display:flex;flex-direction:column;gap:8px;min-height:0;min-width:0;overflow:hidden}

/* ---- panels: the only thing on the page that scrolls ---- */
.pnl{background:var(--surface);border:1px solid var(--line);border-radius:7px;
  display:flex;flex-direction:column;min-height:0;flex:none;overflow:hidden}
.pnl.grow{flex:1 1 auto}
.pnl-hd{display:flex;align-items:center;gap:10px;flex:none;
  padding:7px 11px;border-bottom:1px solid var(--line);background:var(--surface-2)}
.pnl-ttl{font-size:12px;font-weight:700;letter-spacing:.01em;white-space:nowrap}
.pnl-tools{margin-left:auto;color:var(--muted);font-size:11px}
.pnl-bd{padding:9px 11px;overflow:auto;min-height:0;flex:1 1 auto}
.pnl-bd>h2:first-child,.pnl-bd>h3:first-child{margin-top:0}

/* ---- dense data ---- */
h2{font-size:11.5px;text-transform:uppercase;letter-spacing:.07em;color:var(--muted);
  margin:14px 0 7px;font-weight:700}
h3{font-size:13px;margin:0 0 6px}
table{border-collapse:collapse;width:100%;font-size:11.5px}
th,td{text-align:left;padding:4px 8px;border-bottom:1px solid var(--line);vertical-align:top}
th{font-size:9.5px;text-transform:uppercase;letter-spacing:.05em;color:var(--muted);
  font-weight:700;position:sticky;top:0;background:var(--surface);z-index:1}
tbody tr:hover{background:var(--surface-3)}
td.num,th.num{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
tr:last-child td{border-bottom:none}
.scroll{overflow:auto}
.money,.tick{font-variant-numeric:tabular-nums;white-space:nowrap}
.tick{font-weight:700}
.note{color:var(--muted);font-size:11px}
.act{font-weight:800;font-size:11px;letter-spacing:.04em}
.act.buy{color:var(--good)} .act.sell{color:var(--bad)} .act.hold{color:var(--muted)}
.empty{color:var(--muted);font-style:italic;padding:7px 0;font-size:11.5px}
.card{background:transparent;border:0;padding:0;margin:0 0 10px}
.card p,.card .note,.callout{max-width:78ch}

.pill{display:inline-block;padding:1px 7px;border-radius:999px;font-size:10px;
  font-weight:700;border:1px solid var(--line);background:var(--surface-3);color:var(--ink-dim)}
.pill.good{color:var(--good);background:var(--good-bg);border-color:transparent}
.pill.bad{color:var(--bad);background:var(--bad-bg);border-color:transparent}
.pill.warn{color:var(--warn);background:var(--warn-bg);border-color:transparent}
.callout{border-left:2px solid var(--accent);padding:7px 11px;background:var(--surface-3);
  border-radius:0 5px 5px 0;margin:8px 0;font-size:11.5px}
.callout.save{border-left-color:var(--good)}

.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(104px,1fr));gap:7px}
.kpi{background:var(--surface-3);border:1px solid var(--line);border-radius:6px;padding:8px 10px}
.kpi .k{font-size:9.5px;text-transform:uppercase;letter-spacing:.05em;color:var(--muted)}
.kpi .v{font-size:16px;font-weight:700;font-variant-numeric:tabular-nums;margin-top:1px}
.chart{width:100%;height:auto;display:block;margin:4px 0}

/* ---- sub-tabs inside a panel header ---- */
.tabs{display:flex;gap:3px;flex-wrap:wrap;margin-left:6px}
button.tab{font:inherit;font-size:11px;font-weight:600;cursor:pointer;padding:3px 9px;
  border-radius:5px;border:1px solid transparent;background:transparent;color:var(--muted)}
button.tab:hover{color:var(--ink)}
button.tab.on{background:var(--surface-3);border-color:var(--line);color:var(--ink)}
.panel{display:none}
.panel.on{display:block}

/* ---- bottom ticker ---- */
.tickbar{display:flex;align-items:center;gap:20px;flex:none;height:29px;padding:0 13px;
  border-top:1px solid var(--line);background:var(--surface-2);overflow:hidden;
  font-size:11.5px;white-space:nowrap}
.tk{display:flex;align-items:center;gap:6px}
.tk-n{font-weight:700}
.tk-v{font-variant-numeric:tabular-nums;color:var(--ink-dim)}
.tk-d.up{color:var(--good)} .tk-d.down{color:var(--bad)}

/* ---- funnel / steps / trace, carried over ---- */
.funnel{display:flex;flex-direction:column;gap:2px;margin:8px 0}
.funnel-row{display:grid;grid-template-columns:20px minmax(84px,1.1fr) 3fr 30px 30px;
  gap:8px;align-items:center;padding:4px 7px;border-radius:5px;border:0;width:100%;
  background:transparent;color:var(--ink);font:inherit;font-size:11.5px;
  cursor:pointer;text-align:left}
.funnel-row:hover{background:var(--surface-3)}
.funnel-row.on{background:var(--surface-3)}
.funnel-n{font-size:10px;font-weight:700;color:var(--muted);text-align:center}
.funnel-bar{background:var(--surface-3);border-radius:999px;height:9px;overflow:hidden}
.funnel-bar>span{display:block;height:100%;background:var(--accent);border-radius:999px;min-width:2px}
.funnel-out{font-weight:700;font-variant-numeric:tabular-nums;text-align:right}
.funnel-drop{color:var(--bad);font-size:10.5px;text-align:right;font-variant-numeric:tabular-nums}
.step-head{display:flex;align-items:center;gap:9px;margin-bottom:5px}
.step-n{display:flex;align-items:center;justify-content:center;flex:none;width:21px;height:21px;
  border-radius:999px;background:var(--accent);color:#fff;font-size:11px;font-weight:700}
.step-count{margin:9px 0;font-size:13px;font-variant-numeric:tabular-nums}
.step-count strong{font-size:16px}
.trace-box{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin:4px 0 10px}
.trace-box input{font:inherit;font-size:12px;padding:6px 9px;border-radius:6px;
  border:1px solid var(--line);background:var(--surface-3);color:var(--ink);min-width:190px}
.trace-step{display:grid;grid-template-columns:20px 1fr;gap:9px;padding:5px 0;
  border-bottom:1px solid var(--line)}
.trace-step:last-child{border-bottom:none}
.trace-mark{font-weight:700;text-align:center}
.trace-mark.pass{color:var(--good)} .trace-mark.stop{color:var(--bad)} .trace-mark.na{color:var(--muted)}
.whatif-controls{display:flex;gap:11px;flex-wrap:wrap;margin:2px 0 10px}
.whatif-controls label{font-size:11.5px;color:var(--muted);display:flex;align-items:center;gap:5px}
.whatif-controls select,.pnl-tools select{font:inherit;font-size:11.5px;padding:3px 7px;
  border-radius:5px;border:1px solid var(--line);background:var(--surface-3);color:var(--ink)}
.fold>summary{cursor:pointer;font-size:11.5px;font-weight:700;color:var(--muted);
  padding:5px 0;list-style:none}
.fold>summary::-webkit-details-marker{display:none}
.fold>summary::before{content:"\\25B8 "}
.fold[open]>summary::before{content:"\\25BE "}
th.sortable{cursor:pointer;user-select:none;white-space:nowrap}
th.sortable:hover{color:var(--ink)}
th.sortable[data-dir]::after{content:" \\2193"}
th.sortable[data-dir="asc"]::after{content:" \\2191"}

/* ---- three breakpoints, not two ------------------------------------------
   A single jump from three columns to one leaves the 900-1200px band showing
   three squeezed columns, which is worse than either end. */
@media (max-width:1400px){
  .grid{grid-template-columns:minmax(0,340px) minmax(0,1fr)}
  .grid.cols-1{grid-template-columns:minmax(0,1fr)}
}
@media (max-width:1100px){
  .app{grid-template-columns:56px minmax(0,1fr)}
  .rail-item span{display:none}
  .grid{grid-template-columns:minmax(0,300px) minmax(0,1fr)}
}
@media (max-width:900px){
  /* A phone has no room for panels. Give the page its scrollbar back. */
  html,body{overflow:auto;height:auto}
  .app{grid-template-columns:1fr;height:auto}
  .rail{flex-direction:row;overflow-x:auto;border-right:0;border-bottom:1px solid var(--line)}
  .rail-item{flex:none;width:auto;padding:8px 12px}
  .rail-item span{display:inline}
  .main{grid-template-rows:auto auto auto}
  .grid,.grid.cols-2,.grid.cols-1{grid-template-columns:1fr;height:auto}
  .col,.page,.page.on{overflow:visible;height:auto}
  .pnl,.pnl.grow{flex:none}
  .pnl-bd{overflow:visible;max-height:none}
  .tickbar{overflow-x:auto}
}

/* ---- print: the ticket, nothing else ---- */
@media print{
  html,body{overflow:visible;height:auto;background:#fff;color:#000}
  .rail,.topbar,.tickbar,.tabs,.pnl-tools{display:none !important}
  .page{display:none !important}
  .page.on{display:block !important}
  .grid{display:block;padding:0}
  .pnl{display:none}
  .pnl.print{display:block;border:0}
  .pnl-bd{overflow:visible;max-height:none}
}
"""


# For standalone pages that are documents rather than terminals -- the backtest is
# a long report you read once, top to bottom, and forcing it into panels would fight
# what it is. Restores page scrolling and a readable measure over the same theme.
DOC_CSS = """
html,body{overflow:auto;height:auto}
body{padding:0 18px 56px}
.wrap{max-width:900px;margin:0 auto}
header{padding:26px 0 6px}
h1{font-size:22px;margin:0 0 4px;letter-spacing:-.01em}
.sub{color:var(--muted);font-size:12.5px}
h2{margin:26px 0 9px}
h3{font-size:14.5px;margin:0 0 9px}
.card{background:var(--surface);border:1px solid var(--line);border-radius:9px;
  padding:15px 17px;margin-bottom:12px}
table{font-size:12.5px}
th,td{padding:7px 9px}
th{position:static}
footer{margin-top:34px;padding-top:15px;border-top:1px solid var(--line);
  color:var(--muted);font-size:11.5px;line-height:1.6}
"""


# Kept out of THEME_CSS so no f-string ever has to escape the braces.
# Four jobs, all local to the page: switch destination, switch a panel's sub-tab,
# sort a table, and read the two precomputed payloads. No fetch, no dependency.
SHELL_JS = """
(function(){
  var body=document.body, PAGE_KEY="idx-page";

  // ---- rail: which destination is showing --------------------------------
  var items=[].slice.call(document.querySelectorAll(".rail-item"));
  var pages=[].slice.call(document.querySelectorAll(".page"));
  function showPage(key,remember){
    var found=false;
    pages.forEach(function(p){
      var on=p.id==="page-"+key;
      p.classList.toggle("on",on); if(on) found=true;
    });
    if(!found) return false;
    items.forEach(function(b){
      var on=b.dataset.page===key;
      b.classList.toggle("on",on);
      b.setAttribute("aria-current",on?"page":"false");
    });
    body.setAttribute("data-page",key);
    if(remember){try{localStorage.setItem(PAGE_KEY,key);}catch(e){}}
    return true;
  }
  items.forEach(function(b){
    b.addEventListener("click",function(){showPage(b.dataset.page,true);});
  });
  var wantPage=null;
  try{wantPage=localStorage.getItem(PAGE_KEY);}catch(e){}
  // Only restore a destination that still exists -- a stale key from an older
  // build would otherwise hide every page and leave a blank window.
  if(wantPage) showPage(wantPage,false);

  // ---- sub-tabs inside a panel -------------------------------------------
  [].forEach.call(document.querySelectorAll(".tabs,.funnel"),function(strip){
    var group=strip.dataset.group||"";
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
      if(remember&&group){try{localStorage.setItem("idx-tab-"+group,id);}catch(e){}}
    }
    tabs.forEach(function(t,i){
      t.addEventListener("click",function(){show(t.dataset.panel,true);});
      t.addEventListener("keydown",function(e){
        var d=e.key==="ArrowRight"?1:(e.key==="ArrowLeft"?-1:0);
        if(!d) return;
        e.preventDefault();
        var n=tabs[(i+d+tabs.length)%tabs.length];
        show(n.dataset.panel,true); n.focus();
      });
    });
    var want=null;
    if(group){try{want=localStorage.getItem("idx-tab-"+group);}catch(e){}}
    if(want&&tabs.some(function(t){return t.dataset.panel===want;})) show(want,false);
  });

  // ---- click-to-sort: uses data-v, not the formatted "Rp1,234" text -------
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

  var esc=function(s){var d=document.createElement("div");d.textContent=s;return d.innerHTML;};

  // ---- per-stock trace ----------------------------------------------------
  var traceRaw=document.getElementById("trace-data");
  if(traceRaw){
    var trace=JSON.parse(traceRaw.textContent);
    var q=document.getElementById("trace-q"), tout=document.getElementById("trace-out");
    var MARK={passed:["pass","\\u2713"],dropped:["stop","\\u2715"],not_reached:["na","\\u00b7"]};
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
      tout.innerHTML='<div><span class="tick">'+esc(key)+"</span></div>"+rows+
        '<div class="callout">'+esc(hit.outcome)+"</div>";
    }
    q.addEventListener("input",lookup);
    q.addEventListener("change",lookup);
  }

  // ---- the Python bridge --------------------------------------------------
  // Present only in the app window. Opened as a plain file there is no bridge, the
  // forms are never wired, and the page shows the equivalent command instead -- a
  // form with nothing behind it is worse than no form, because it looks like it
  // worked.
  function api(){
    return (window.pywebview && window.pywebview.api) ? window.pywebview.api : null;
  }
  var rpFmt=function(v){return "Rp"+Math.round(v).toLocaleString("en-US");};

  var form=document.getElementById("trade-form");
  if(form && api()){
    var $=function(id){return document.getElementById(id);};
    var out=$("tf-preview"), msg=$("tf-msg"), go=$("tf-submit");
    function fields(){
      return {
        action:(document.querySelector("input[name=tf-action]:checked")||{}).value||"BUY",
        ticker:$("tf-ticker").value.trim(),
        lots:$("tf-lots").value, price:$("tf-price").value,
        on_date:$("tf-date").value, note:$("tf-note").value,
        source:$("tf-source").value
      };
    }
    function row(k,v,cls){
      return '<div class="row'+(cls?" "+cls:"")+'"><span>'+k+"</span><span>"+v+"</span></div>";
    }
    function preview(){
      var f=fields();
      if(!f.ticker||!f.lots||!f.price){out.innerHTML="";go.disabled=true;return;}
      // Costed by Python against the real journal, so the stamp knows whether this
      // would be the first sell of the day. Never recomputed here.
      api().preview_trade(f.action,f.ticker,f.lots,f.price,f.on_date).then(function(r){
        if(!r.ok){out.innerHTML='<span class="note">'+esc(r.message)+"</span>";go.disabled=true;return;}
        var d=r.data, buying=d.action==="BUY";
        out.innerHTML=
          row("Gross",rpFmt(d.gross_rp))+
          row((buying?"Buy":"Sell")+" fee",rpFmt(d.fee_rp))+
          row("Stamp",d.stamp_rp>0?rpFmt(d.stamp_rp):"Rp0 - already stamped today")+
          row(buying?"Total out of account":"Net into account",
              rpFmt(Math.abs(d.net_rp)),"total")+
          (r.message?'<div class="note">'+esc(r.message)+"</div>":"");
        go.disabled=false;
      });
    }
    ["tf-ticker","tf-lots","tf-price","tf-date"].forEach(function(id){
      $(id).addEventListener("input",preview);
    });
    [].forEach.call(document.querySelectorAll("input[name=tf-action]"),function(el){
      el.addEventListener("change",preview);
    });
    go.addEventListener("click",function(){
      var f=fields();
      go.disabled=true; msg.textContent="Recording...";
      api().log_trade(f.action,f.ticker,f.lots,f.price,f.on_date,f.note,f.source)
        .then(function(r){
          msg.textContent=r.message;
          msg.style.color=r.ok?"var(--good)":"var(--bad)";
          if(r.ok){
            var led=document.getElementById("ledger");
            if(led&&r.data&&r.data.journal_html) led.innerHTML=r.data.journal_html;
            $("tf-ticker").value=""; $("tf-price").value=""; $("tf-note").value="";
            out.innerHTML="";
          }
          go.disabled=false;
        });
    });
  }

  // ---- settings editor ----------------------------------------------------
  var editor=document.getElementById("settings-editor");
  if(editor && api()){
    api().get_settings().then(function(r){
      if(!r.ok) return;
      editor.innerHTML="";
      r.data.fields.forEach(function(f){
        var d=document.createElement("div");
        d.className="set-row"+(f.overridden?" changed":"");
        d.innerHTML='<span class="lbl">'+esc(f.label)+"</span>"+
          '<input value="'+esc(f.value)+'">'+
          '<span class="dflt">default '+esc(f.default)+"</span>"+
          "<button>reset</button>";
        var input=d.querySelector("input"), reset=d.querySelector("button");
        function say(r2){
          var n=document.createElement("div");
          n.className="note"; n.textContent=r2.message;
          n.style.color=r2.ok?"var(--good)":"var(--bad)";
          n.style.flexBasis="100%";
          var old=d.querySelector(".note"); if(old) d.removeChild(old);
          d.appendChild(n);
        }
        input.addEventListener("change",function(){
          api().save_setting(f.path,input.value).then(function(r2){
            say(r2); if(r2.ok) d.classList.add("changed");
          });
        });
        reset.addEventListener("click",function(){
          api().reset_setting(f.path).then(function(r2){
            say(r2);
            if(r2.ok){input.value=f.default; d.classList.remove("changed");}
          });
        });
        editor.appendChild(d);
      });
    });
  }

  // ---- what-if: a lookup into a table computed at render time -------------
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
      var rows=cell.pos.map(function(p){
        return "<tr><td><span class='tick'>"+esc(p.t)+"</span></td>"+
               "<td class='num'>"+esc(p.l)+" lot</td>"+
               "<td class='num'>"+rp(p.r)+"</td></tr>";
      }).join("");
      var shortfall=cell.short?'<div class="callout"><strong>Lot sizes bind here.</strong> '+
        esc(cell.short)+"</div>":"";
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
