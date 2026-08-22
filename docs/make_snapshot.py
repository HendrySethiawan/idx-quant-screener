#!/usr/bin/env python3
"""Render docs/screener_analysis.webp - a redesigned snapshot of the screener run.

Reads the artefacts the pipeline already produced (data/output/screener_results.csv
and the cached benchmark series) and re-renders them against the project's data-viz
rules: one hue plus de-emphasis gray, solid hairline chrome, selective direct labels,
log scales where the data is log-distributed, and a table view of the top picks.

    python docs/make_snapshot.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import joblib
import matplotlib
matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import numpy as np
import pandas as pd
import yaml
from matplotlib.lines import Line2D
from matplotlib.patches import PathPatch
from matplotlib.path import Path as MplPath
from matplotlib.transforms import Bbox

ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "data" / "output" / "screener_results.csv"
BENCH = ROOT / "data" / "cache" / "^JKSE.pkl"
CONFIG = ROOT / "configs" / "default.yaml"
OUT = Path(__file__).resolve().parent / "screener_analysis.webp"

# ---------------------------------------------------------------- design tokens
SURFACE = "#fcfcfb"   # chart surface
INK = "#0b0b0b"       # primary ink
INK2 = "#52514e"      # secondary ink
MUTED = "#898781"     # axis + label ink
GRID = "#e1e0d9"      # hairline gridline
AXIS = "#c3c2b7"      # baseline / axis rule
ACCENT = "#2a78d6"    # series slot 1 - the emphasis hue
ACCENT_DK = "#184f95"
NEG = "#e34948"       # diverging pole opposite ACCENT
DEEMPH = "#c3c2b7"    # de-emphasis fill (context marks)

plt.rcParams.update({
    "font.family": ["Segoe UI", "DejaVu Sans"],
    "figure.facecolor": SURFACE,
    "axes.facecolor": SURFACE,
    "savefig.facecolor": SURFACE,
    "text.color": INK,
    "axes.edgecolor": AXIS,
    "axes.labelcolor": INK2,
    "xtick.color": MUTED,
    "ytick.color": MUTED,
    "grid.color": GRID,
    "grid.linewidth": 0.8,
    "grid.linestyle": "-",
    "axes.grid": False,
    "axes.axisbelow": True,
})


# ------------------------------------------------------------------- primitives
def px_to_data(ax, px: float) -> tuple[float, float]:
    """Convert a pixel radius into (x, y) data-unit radii on a linear axes."""
    inv = ax.transData.inverted()
    o = ax.transData.transform((0.0, 0.0))
    p = inv.transform(o + np.array([px, px]))
    q = inv.transform(o)
    return abs(p[0] - q[0]), abs(p[1] - q[1])


def rounded_rect(ax, x0, y0, x1, y1, rx, ry, corners, **kw):
    """Rectangle with only `corners` rounded (elliptical radii keep them circular)."""
    x0, x1 = sorted((x0, x1))
    y0, y1 = sorted((y0, y1))
    rx = min(rx, (x1 - x0) / 2) if x1 > x0 else 0.0
    ry = min(ry, (y1 - y0) / 2) if y1 > y0 else 0.0
    r = {c: (rx, ry) if c in corners else (0.0, 0.0)
         for c in ("bl", "br", "tr", "tl")}
    v, c = [], []

    def move(p):
        v.append(p), c.append(MplPath.MOVETO)

    def line(p):
        v.append(p), c.append(MplPath.LINETO)

    def arc(ctrl, p):
        v.extend([ctrl, p]), c.extend([MplPath.CURVE3, MplPath.CURVE3])

    move((x0 + r["bl"][0], y0))
    line((x1 - r["br"][0], y0))
    if r["br"][0]:
        arc((x1, y0), (x1, y0 + r["br"][1]))
    line((x1, y1 - r["tr"][1]))
    if r["tr"][0]:
        arc((x1, y1), (x1 - r["tr"][0], y1))
    line((x0 + r["tl"][0], y1))
    if r["tl"][0]:
        arc((x0, y1), (x0, y1 - r["tl"][1]))
    line((x0, y0 + r["bl"][1]))
    if r["bl"][0]:
        arc((x0, y0), (x0 + r["bl"][0], y0))
    c.append(MplPath.CLOSEPOLY), v.append((x0 + r["bl"][0], y0))
    patch = PathPatch(MplPath(v, c), linewidth=0, **kw)
    ax.add_patch(patch)
    return patch


def despine(ax, keep=("bottom",)):
    for side, spine in ax.spines.items():
        spine.set_visible(side in keep)
        spine.set_linewidth(0.8)
        spine.set_color(AXIS)
    ax.tick_params(length=0, labelsize=8.5)


def panel_title(ax, title, subtitle=None):
    ax.set_title(title, loc="left", fontsize=13.5, fontweight="600", color=INK, pad=20)
    if subtitle:
        ax.text(0, 1.02, subtitle, transform=ax.transAxes, ha="left", va="bottom",
                fontsize=9, color=MUTED)


def ring_scatter(ax, x, y, color, size, zorder=3):
    """Marker with the mandated 2px surface ring."""
    return ax.scatter(x, y, s=size, c=color, linewidths=2.0, edgecolors=SURFACE,
                      zorder=zorder)


_OFFSETS = {"n": (0, 1), "ne": (.72, .72), "e": (1, 0), "se": (.72, -.72),
            "s": (0, -1), "sw": (-.72, -.72), "w": (-1, 0), "nw": (-.72, .72)}
_HA = {"n": "center", "ne": "left", "e": "left", "se": "left",
       "s": "center", "sw": "right", "w": "right", "nw": "right"}
_VA = {"n": "bottom", "ne": "bottom", "e": "center", "se": "top",
       "s": "top", "sw": "top", "w": "center", "nw": "bottom"}


def place_labels(ax, points, texts, obstacles=(), dist=14, fontsize=8.5,
                 color=INK, weight="600"):
    """Greedy collision-free direct labels: first candidate offset that fits wins."""
    fig = ax.figure
    fig.canvas.draw()
    renderer = fig.canvas.get_renderer()
    taken = [Bbox.from_bounds(*ax.transData.transform(p) - 8, 16, 16)
             for p in obstacles]
    for (px, py), txt in zip(points, texts):
        chosen = None
        for key in ("n", "ne", "nw", "e", "w", "se", "sw", "s"):
            dx, dy = _OFFSETS[key]
            t = ax.annotate(txt, (px, py), xytext=(dx * dist, dy * dist),
                            textcoords="offset points", ha=_HA[key], va=_VA[key],
                            fontsize=fontsize, color=color, fontweight=weight,
                            zorder=6, annotation_clip=False)
            bb = t.get_window_extent(renderer=renderer).expanded(1.10, 1.30)
            if not any(bb.overlaps(o) for o in taken):
                chosen = (t, bb)
                break
            t.remove()
        if chosen is None:
            t = ax.annotate(txt, (px, py), xytext=(0, dist), textcoords="offset points",
                            ha="center", va="bottom", fontsize=fontsize, color=color,
                            fontweight=weight, zorder=6, annotation_clip=False)
            chosen = (t, t.get_window_extent(renderer=renderer))
        taken.append(chosen[1])


# ------------------------------------------------------------------------- data
def load():
    if not RESULTS.exists():
        sys.exit(f"missing {RESULTS} - run `python main.py` first")
    df = pd.read_csv(RESULTS)
    bench = joblib.load(BENCH) if BENCH.exists() else pd.DataFrame()
    configured = 0
    if CONFIG.exists():
        cfg = yaml.safe_load(CONFIG.read_text(encoding="utf-8")) or {}
        configured = len(cfg.get("stock_tickers", {}))
    return df, bench, configured


def short(name: str, n: int = 22) -> str:
    return name if len(name) <= n else name[: n - 1].rstrip() + "…"


def sym(ticker: str) -> str:
    return ticker.replace(".JK", "")


# ------------------------------------------------------------------------ panels
def draw_header(fig, df, scored, bench, configured, asof, win_ret):
    ax = fig.add_axes([0.0, 0.845, 1.0, 0.155])
    ax.set_axis_off()
    ax.set_xlim(0, 1), ax.set_ylim(0, 1)
    L, R = 0.045, 0.965

    ax.text(L, 0.88, "IDX Quant Screener", fontsize=26, fontweight="700",
            color=INK, va="center")
    ax.text(L, 0.66, "Multi-factor value screen of the Jakarta Stock Exchange",
            fontsize=11, color=INK2, va="center")
    ax.text(R, 0.66, f"Session of {asof:%d %B %Y}  ·  {len(bench)} sessions of history",
            fontsize=10, color=MUTED, va="center", ha="right")
    ax.plot([L, R], [0.545, 0.545], color=AXIS, lw=0.8, solid_capstyle="butt")

    tiles = [
        ("^JKSE, window to date", f"{win_ret:+.1f}%",
         "index level 7,594 → 6,127", NEG if win_ret < 0 else ACCENT_DK, 34),
        ("Stocks scored", f"{len(scored)}", f"of {configured} configured", INK, 21),
        ("Median P/E", f"{scored['pe_ratio'].median():.1f}×",
         "scored universe", INK, 21),
        ("Median RSI(14)", f"{scored['rsi_14'].median():.0f}",
         "30 or below = oversold", INK, 21),
        ("Top pick", sym(scored.iloc[0]["ticker"]),
         f"score {scored.iloc[0]['undervaluation_score']:.2f}", INK, 21),
    ]
    for i, (label, value, foot, colour, size) in enumerate(tiles):
        x = L + i * (R - L) / len(tiles)
        ax.text(x, 0.40, label.upper(), fontsize=8, color=MUTED, va="center",
                fontweight="600")
        ax.text(x, 0.22, value, fontsize=size, fontweight="700", color=colour,
                va="center")
        ax.text(x, 0.055, foot, fontsize=8.5, color=MUTED, va="center")


def draw_benchmark(ax, bench, last_ret):
    panel_title(ax, "Benchmark trend",
                f"^JKSE close  ·  last session {last_ret:+.2f}%")
    x, y = bench.index, bench["Close"].to_numpy()
    ax.set_xlim(x[0], x[-1])
    pad = (y.max() - y.min()) * 0.14
    ax.set_ylim(y.min() - pad, y.max() + pad)

    ax.fill_between(x, y.min() - pad, y, color=ACCENT, alpha=0.10, linewidth=0)
    ax.plot(x, y, color=ACCENT, lw=2.0, solid_capstyle="round",
            solid_joinstyle="round", zorder=3)
    ring_scatter(ax, [x[-1]], [y[-1]], ACCENT, 70, zorder=4)
    ax.annotate(f"{y[-1]:,.0f}", xy=(x[-1], y[-1]), xytext=(-8, 13),
                textcoords="offset points", ha="right", fontsize=10,
                fontweight="600", color=INK)

    ax.yaxis.grid(True, linewidth=0.8, color=GRID)
    ax.set_axisbelow(True)
    despine(ax, keep=("bottom",))
    idx = np.linspace(0, len(x) - 1, 5).round().astype(int)
    ax.set_xticks([x[i] for i in idx])
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%d %b"))
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"{v:,.0f}"))


def draw_top_scores(ax, scored, n=12, picks=5):
    top = scored.head(n).iloc[::-1].reset_index(drop=True)
    panel_title(ax, f"Top {n} by undervaluation score",
                f"logistic-regression probability  ·  top {picks} go to top_picks.csv")
    ax.set_ylim(-0.7, n - 0.3)
    ax.set_xlim(-0.012, 1.16)
    rx, ry = px_to_data(ax, 4)
    for i, row in top.iterrows():
        emph = (n - 1 - i) < picks
        rounded_rect(ax, 0, i - 0.26, row["undervaluation_score"], i + 0.26, rx, ry,
                     corners=("tr", "br"),
                     facecolor=ACCENT if emph else DEEMPH, zorder=3)
        ax.text(row["undervaluation_score"] + 0.024, i,
                f"{row['undervaluation_score']:.2f}", va="center", ha="left",
                fontsize=9, fontweight="600" if emph else "400",
                color=INK if emph else INK2)
    ax.set_yticks(range(n))
    ax.set_yticklabels([short(r["name"], 26) for _, r in top.iterrows()],
                       fontsize=9, color=INK2)
    ax.set_xticks([])
    despine(ax, keep=())
    ax.tick_params(length=0)
    ax.axvline(0, color=AXIS, lw=0.8, zorder=2)
    ax.legend(handles=[
        Line2D([0], [0], marker="s", color="none", markerfacecolor=ACCENT,
               markeredgecolor="none", markersize=9, label="exported pick"),
        Line2D([0], [0], marker="s", color="none", markerfacecolor=DEEMPH,
               markeredgecolor="none", markersize=9, label="rest of the shortlist"),
    ], loc="upper right", frameon=False, fontsize=9, handletextpad=0.5,
        labelcolor=INK2, ncol=2, bbox_to_anchor=(1.0, -0.01))


def draw_value_momentum(ax, scored, top):
    ctx = scored[~scored["ticker"].isin(top["ticker"])]
    panel_title(ax, "Valuation vs momentum",
                "cheap and oversold = bottom-left  ·  P/E on a log scale")
    ax.set_xscale("log")
    ax.set_xlim(2.2, 190)
    ax.set_ylim(0, 80)
    ax.axhspan(0, 30, color=ACCENT, alpha=0.055, linewidth=0, zorder=1)
    ring_scatter(ax, ctx["pe_ratio"], ctx["rsi_14"], DEEMPH, 55, zorder=3)
    ring_scatter(ax, top["pe_ratio"], top["rsi_14"], ACCENT, 95, zorder=4)

    ax.axhline(50, color=AXIS, lw=0.8, zorder=2)
    ax.text(2.45, 51, "RSI 50", fontsize=8, color=MUTED, va="bottom")
    ax.text(2.45, 27.6, "oversold  < 30", fontsize=8.5, color=ACCENT_DK, va="top")
    ax.set_xlabel("Trailing P/E", fontsize=9.5)
    ax.set_ylabel("RSI (14)", fontsize=9.5)
    ax.xaxis.set_major_locator(mticker.FixedLocator([3, 5, 10, 20, 50, 100]))
    ax.xaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"{v:g}×"))
    ax.xaxis.set_minor_formatter(mticker.NullFormatter())
    ax.grid(True, which="major", linewidth=0.8, color=GRID)
    despine(ax, keep=("bottom", "left"))

    worst = scored.loc[scored["pe_ratio"].idxmax()]
    pts = [(r["pe_ratio"], r["rsi_14"]) for _, r in top.iterrows()]
    txt = [sym(r["ticker"]) for _, r in top.iterrows()]
    place_labels(ax, pts + [(worst["pe_ratio"], worst["rsi_14"])],
                 txt + [f"{sym(worst['ticker'])} — P/E {worst['pe_ratio']:.0f}"],
                 obstacles=list(zip(scored["pe_ratio"], scored["rsi_14"])))


def draw_size_beta(ax, scored, top):
    ctx = scored[~scored["ticker"].isin(top["ticker"])]
    panel_title(ax, "Size vs volatility",
                "market cap on a log scale  ·  beta as reported by the data source")
    ax.set_xscale("log")
    ax.set_xlim(3, 1400)
    ax.set_ylim(-0.8, 1.5)
    ring_scatter(ax, ctx["market_cap"] / 1e12, ctx["beta"], DEEMPH, 55, zorder=3)
    ring_scatter(ax, top["market_cap"] / 1e12, top["beta"], ACCENT, 95, zorder=4)

    ax.axhline(0, color=AXIS, lw=0.8, zorder=2)
    ax.axhline(1.0, color=AXIS, lw=0.8, zorder=2)
    ax.text(3.4, 1.04, "beta 1.0 — moves with the index", fontsize=8, color=MUTED,
            va="bottom")
    ax.set_xlabel("Market capitalisation (IDR trillion)", fontsize=9.5)
    ax.set_ylabel("Beta", fontsize=9.5)
    ax.xaxis.set_major_locator(
        mticker.FixedLocator([5, 10, 25, 50, 100, 250, 500, 1000]))
    ax.xaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"{v:g}"))
    ax.xaxis.set_minor_formatter(mticker.NullFormatter())
    ax.grid(True, which="major", linewidth=0.8, color=GRID)
    despine(ax, keep=("bottom", "left"))

    place_labels(ax, [(r["market_cap"] / 1e12, r["beta"]) for _, r in top.iterrows()],
                 [sym(r["ticker"]) for _, r in top.iterrows()],
                 obstacles=list(zip(scored["market_cap"] / 1e12, scored["beta"])))


def draw_relative(ax, df, bm_change):
    d = df.dropna(subset=["price_change_pct"]).sort_values(
        "price_change_pct", ascending=False).reset_index(drop=True)
    panel_title(ax, "Last-session return vs the index",
                f"all {len(d)} validated tickers, close to close")
    ax.set_xlim(-0.8, len(d) - 0.2)
    lo, hi = d["price_change_pct"].min(), d["price_change_pct"].max()
    ax.set_ylim(lo - 3.0, hi + 5.5)
    rx, ry = px_to_data(ax, 4)

    for i, row in d.iterrows():
        v = row["price_change_pct"]
        rounded_rect(ax, i - 0.25, 0, i + 0.25, v, rx, ry,
                     corners=("tl", "tr") if v >= 0 else ("bl", "br"),
                     facecolor=ACCENT if v >= bm_change else NEG, zorder=3)

    for i in list(d.index[:3]) + list(d.index[-3:]):
        v = d.loc[i, "price_change_pct"]
        ax.text(i, v + (1.0 if v >= 0 else -1.0), f"{v:+.1f}", ha="center",
                va="bottom" if v >= 0 else "top", fontsize=8.5, fontweight="600",
                color=INK)

    ax.axhline(0, color=AXIS, lw=0.8, zorder=2)
    ax.axhline(bm_change, color=ACCENT_DK, lw=1.2, zorder=4)
    ax.text(len(d) - 1.0, bm_change + 1.4, f"^JKSE {bm_change:+.2f}%", fontsize=8.5,
            color=ACCENT_DK, va="bottom", ha="right", fontweight="600")

    ax.set_xticks(range(len(d)))
    ax.set_xticklabels([sym(t) for t in d["ticker"]], rotation=90, fontsize=7.5,
                       color=MUTED)
    ax.set_ylabel("Change (%)", fontsize=9.5)
    ax.yaxis.grid(True, linewidth=0.8, color=GRID)
    ax.set_axisbelow(True)
    despine(ax, keep=("bottom",))
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"{v:+.0f}%"))
    ax.legend(handles=[
        Line2D([0], [0], marker="s", color="none", markerfacecolor=ACCENT,
               markeredgecolor="none", markersize=9, label="at or above the index"),
        Line2D([0], [0], marker="s", color="none", markerfacecolor=NEG,
               markeredgecolor="none", markersize=9, label="below the index"),
    ], loc="upper right", frameon=False, fontsize=9, handletextpad=0.5,
        labelcolor=INK2, ncol=2, bbox_to_anchor=(1.0, 1.04))


def draw_table(ax, scored, n=8, picks=5):
    ax.set_axis_off()
    ax.set_xlim(0, 1), ax.set_ylim(0, 1)
    ax.text(0, 1.10, f"Top {n} ranked — table view", transform=ax.transAxes,
            fontsize=13.5, fontweight="600", color=INK, va="bottom")
    ax.text(0, 1.02, "the shortlist in numbers — nothing here is locked inside a chart",
            transform=ax.transAxes, fontsize=9, color=MUTED, va="bottom")

    cols = [
        ("#", 0.022, "right"), ("Ticker", 0.052, "left"), ("Company", 0.135, "left"),
        ("Score", 0.455, "right"), ("P/E", 0.545, "right"), ("P/B", 0.630, "right"),
        ("Div yld", 0.725, "right"), ("Beta", 0.805, "right"),
        ("RSI(14)", 0.895, "right"), ("1-day", 0.985, "right"),
    ]
    top_y, row_h = 0.90, 0.100
    for label, x, ha in cols:
        ax.text(x, top_y + 0.06, label.upper(), fontsize=8, color=MUTED, ha=ha,
                fontweight="600")
    ax.plot([0, 1], [top_y + 0.035] * 2, color=AXIS, lw=0.8)

    for i, (_, r) in enumerate(scored.head(n).iterrows()):
        y = top_y - i * row_h - 0.035
        emph = i < picks
        ax.plot([0.002], [y], marker="s", markersize=6, color="none",
                markerfacecolor=ACCENT if emph else DEEMPH, markeredgecolor="none",
                clip_on=False)
        pb = r["price_to_book"]
        vals = [
            f"{i + 1}", sym(r["ticker"]), short(r["name"], 30),
            f"{r['undervaluation_score']:.3f}", f"{r['pe_ratio']:.1f}×",
            f"{pb:,.1f}" if pb < 1000 else "n/a", f"{r['dividend_yield']:.1f}%",
            f"{r['beta']:.2f}", f"{r['rsi_14']:.0f}",
            f"{r['price_change_pct']:+.1f}%",
        ]
        for (label, x, ha), v in zip(cols, vals):
            colour = INK if emph else INK2
            if label == "1-day":
                colour = ACCENT_DK if r["price_change_pct"] >= 0 else NEG
            ax.text(x, y, v, fontsize=9.5, ha=ha, va="center", color=colour,
                    fontweight="600" if emph and label in ("Ticker", "Score") else "400")
        ax.plot([0, 1], [y - row_h / 2] * 2, color=GRID, lw=0.8)


def draw_footer(fig, df, unscored):
    ax = fig.add_axes([0.0, 0.0, 1.0, 0.055])
    ax.set_axis_off()
    ax.set_xlim(0, 1), ax.set_ylim(0, 1)
    ax.plot([0.045, 0.965], [0.95, 0.95], color=AXIS, lw=0.8)
    ax.text(0.045, 0.55,
            "Source: Yahoo Finance via yfinance  ·  score = logistic-regression "
            "probability over P/E, P/B, dividend yield, RSI(14) and the 5- and 20-day "
            "MA slopes  ·  “1-day” is the last session's close-to-close change, not a "
            "30-day return",
            fontsize=8.5, color=MUTED, va="center")
    ax.text(0.045, 0.16,
            f"{unscored} of {len(df)} validated tickers carry no score — a missing "
            "dividend yield voids the z-score sum  ·  some P/B values are unusable at "
            "source and are shown as n/a  ·  research tool, not investment advice",
            fontsize=8.5, color=MUTED, va="center")


# -------------------------------------------------------------------------- main
def main():
    df, bench, configured = load()
    scored = df.dropna(subset=["undervaluation_score"]).sort_values(
        "undervaluation_score", ascending=False).reset_index(drop=True)
    unscored = len(df) - len(scored)
    top = scored.head(5)

    close = bench["Close"].to_numpy()
    win_ret = (close[-1] / close[0] - 1) * 100
    last_ret = (close[-1] / close[-2] - 1) * 100
    asof = bench.index[-1]

    fig = plt.figure(figsize=(16.5, 15.6), dpi=150)
    gs = fig.add_gridspec(
        4, 2, left=0.078, right=0.965, top=0.775, bottom=0.075,
        hspace=0.60, wspace=0.34, height_ratios=[1.0, 1.05, 1.0, 0.95])

    draw_header(fig, df, scored, bench, configured, asof, win_ret)
    draw_benchmark(fig.add_subplot(gs[0, 0]), bench, last_ret)
    draw_top_scores(fig.add_subplot(gs[0, 1]), scored)
    draw_value_momentum(fig.add_subplot(gs[1, 0]), scored, top)
    draw_size_beta(fig.add_subplot(gs[1, 1]), scored, top)
    draw_relative(fig.add_subplot(gs[2, :]), df, last_ret)
    draw_table(fig.add_subplot(gs[3, :]), scored)
    draw_footer(fig, df, unscored)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUT, format="webp", facecolor=SURFACE,
                pil_kwargs={"quality": 92, "method": 6})
    plt.close(fig)
    print(f"wrote {OUT.relative_to(ROOT)}  ({OUT.stat().st_size / 1024:,.0f} KB)")


if __name__ == "__main__":
    main()
