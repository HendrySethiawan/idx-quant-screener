# src/report/charts.py
"""
Inline SVG charts for the brief.

Why hand-rolled rather than matplotlib. The old `screener_analysis.png` is saved
with `facecolor="white"`, so its colours are baked in and it fights the brief's
dark theme; it is a separate 896KB file that no page ever linked to; and being a
raster it blurs on any screen that is not exactly 200 DPI. These charts are plain
SVG that reference the *same* CSS variables as the rest of the document, so they
theme themselves, scale to any width, and stay inside the one self-contained file.

Every function here returns a string and takes only plain Python numbers, so they
are testable with no browser, no network, and no matplotlib import.

Layout convention: a fixed `viewBox` with the CSS class `chart` (declared in
brief.py) doing `width:100%;height:auto`. The chart therefore has a fixed aspect
ratio and a resolution-independent size -- we never need to know the real pixel
width at render time.
"""
from __future__ import annotations

import html
import math
from typing import Dict, List, Optional, Sequence, Tuple

# Canvas geometry. Chosen so a 720-wide viewBox gives ~14px text at the brief's
# 940px max width -- readable without the browser scaling anything.
W = 720
PAD_L = 54
PAD_R = 14
PAD_T = 16
PAD_B = 30

_EMPTY = '<div class="empty">Not enough data to draw this yet.</div>'


def _e(v) -> str:
    return html.escape("" if v is None else str(v))


def _n(v: float) -> str:
    """Coordinates at 0.1px. Full float repr would double the file size."""
    return f"{v:.1f}".rstrip("0").rstrip(".")


def _finite(v) -> bool:
    return v is not None and isinstance(v, (int, float)) and math.isfinite(float(v))


def _bounds(values: Sequence[float], pad: float = 0.06,
            include_zero: bool = False) -> Tuple[float, float]:
    """
    Data range, padded so the extremes are not welded to the frame.

    A flat series would give lo == hi and a divide-by-zero in every later scale
    call, so a degenerate range is widened to +/-1 (or +/-1% for large values).
    """
    clean = [float(v) for v in values if _finite(v)]
    if not clean:
        return 0.0, 1.0
    lo, hi = min(clean), max(clean)
    if include_zero:
        lo, hi = min(lo, 0.0), max(hi, 0.0)
    if lo == hi:
        step = abs(lo) * 0.01 or 1.0
        return lo - step, hi + step
    span = hi - lo
    return lo - span * pad, hi + span * pad


def _svg(body: str, height: int, label: str) -> str:
    return (
        f'<svg class="chart" viewBox="0 0 {W} {height}" '
        f'preserveAspectRatio="xMidYMid meet" role="img" '
        f'aria-label="{_e(label)}"><title>{_e(label)}</title>{body}</svg>'
    )


def _y_axis(lo: float, hi: float, height: int, fmt, ticks: int = 4) -> str:
    """Horizontal gridlines with labels down the left margin."""
    top, bottom = PAD_T, height - PAD_B
    out = ""
    for i in range(ticks + 1):
        frac = i / ticks
        y = bottom - frac * (bottom - top)
        value = lo + frac * (hi - lo)
        out += (
            f'<line x1="{PAD_L}" y1="{_n(y)}" x2="{W - PAD_R}" y2="{_n(y)}" '
            f'stroke="var(--line)" stroke-width="1"/>'
            f'<text x="{PAD_L - 7}" y="{_n(y + 3.5)}" text-anchor="end" '
            f'font-size="10.5" fill="var(--muted)">{_e(fmt(value))}</text>'
        )
    return out


def _x_ticks(labels: Sequence[str], height: int, max_ticks: int = 6) -> str:
    """
    Only a handful of x labels are drawn. 479 trading days of dates would render
    as a black smear, and the reader needs the shape, not every date.
    """
    n = len(labels)
    if n == 0:
        return ""
    step = max(1, n // max_ticks)
    plot_w = W - PAD_L - PAD_R
    out = ""
    for i in range(0, n, step):
        x = PAD_L + (i / max(1, n - 1)) * plot_w
        anchor = "start" if i == 0 else ("end" if i >= n - step else "middle")
        out += (
            f'<text x="{_n(x)}" y="{height - PAD_B + 15}" text-anchor="{anchor}" '
            f'font-size="10.5" fill="var(--muted)">{_e(labels[i])}</text>'
        )
    return out


def line_chart(
    series: Sequence[Tuple[str, Sequence[float]]],
    *,
    x_labels: Optional[Sequence[str]] = None,
    colors: Optional[Sequence[str]] = None,
    label: str = "",
    height: int = 240,
    y_format=None,
) -> str:
    """
    One or more lines on a shared scale.

    Used for the regime chart (index vs its 200-day mean) and the equity curve
    (you vs the cash-flow-matched IHSG shadow). Both are comparisons, which is
    why a shared y scale is the right default -- two independently scaled axes
    would make any pair of lines look like they track each other.

    A non-finite value breaks the path rather than interpolating across it: a gap
    in the data should look like a gap.
    """
    series = [(name, list(vals)) for name, vals in series if vals is not None]
    series = [(name, vals) for name, vals in series if any(_finite(v) for v in vals)]
    if not series:
        return _EMPTY

    n = max(len(vals) for _, vals in series)
    if n < 2:
        return _EMPTY

    flat = [v for _, vals in series for v in vals]
    lo, hi = _bounds(flat)
    fmt = y_format or (lambda v: f"{v:,.0f}")
    palette = list(colors) if colors else ["var(--accent)", "var(--muted)", "var(--good)"]

    top, bottom = PAD_T, height - PAD_B
    plot_w = W - PAD_L - PAD_R

    def to_xy(i: int, v: float) -> Tuple[float, float]:
        x = PAD_L + (i / (n - 1)) * plot_w
        y = bottom - ((float(v) - lo) / (hi - lo)) * (bottom - top)
        return x, y

    body = _y_axis(lo, hi, height, fmt)

    legend = ""
    for idx, (name, vals) in enumerate(series):
        color = palette[idx % len(palette)]
        # Break the path on gaps: "M" restarts, "L" continues.
        d, pen_down = "", False
        for i, v in enumerate(vals):
            if not _finite(v):
                pen_down = False
                continue
            x, y = to_xy(i, v)
            d += f'{"L" if pen_down else "M"}{_n(x)} {_n(y)}'
            pen_down = True
        if d:
            body += (
                f'<path d="{d}" fill="none" stroke="{color}" stroke-width="1.8" '
                f'stroke-linejoin="round" stroke-linecap="round"/>'
            )
        if name:
            lx = PAD_L + 4 + idx * 132
            legend += (
                f'<rect x="{lx}" y="{PAD_T - 10}" width="9" height="3" rx="1.5" fill="{color}"/>'
                f'<text x="{lx + 14}" y="{PAD_T - 5}" font-size="11" '
                f'fill="var(--muted)">{_e(name)}</text>'
            )

    if x_labels:
        body += _x_ticks(list(x_labels)[:n], height)
    return _svg(body + legend, height, label or "line chart")


def bar_chart(
    labels: Sequence[str],
    values: Sequence[float],
    *,
    annotations: Optional[Sequence[str]] = None,
    muted: Optional[Sequence[bool]] = None,
    label: str = "",
    height: int = 240,
    y_format=None,
) -> str:
    """
    Vertical bars on a zero baseline, green above and red below.

    `muted` greys a bar out without hiding it -- seasonality uses it for months
    with too few observations to mean anything, which must still be visible
    (an omitted month reads as "no effect", which is a different claim).
    `annotations` prints a small caption under each bar, e.g. the sample size.
    """
    labels, values = list(labels), list(values)
    if not labels or not any(_finite(v) for v in values):
        return _EMPTY

    lo, hi = _bounds(values, include_zero=True)
    fmt = y_format or (lambda v: f"{v:,.1f}")
    top, bottom = PAD_T, height - PAD_B
    plot_w = W - PAD_L - PAD_R
    slot = plot_w / len(labels)
    bar_w = min(slot * 0.62, 48)

    def y_of(v: float) -> float:
        return bottom - ((float(v) - lo) / (hi - lo)) * (bottom - top)

    zero_y = y_of(0.0)
    body = _y_axis(lo, hi, height, fmt)
    body += (f'<line x1="{PAD_L}" y1="{_n(zero_y)}" x2="{W - PAD_R}" y2="{_n(zero_y)}" '
             f'stroke="var(--muted)" stroke-width="1.2"/>')

    for i, (lab, val) in enumerate(zip(labels, values)):
        cx = PAD_L + slot * (i + 0.5)
        if _finite(val):
            y = y_of(val)
            top_y, bar_h = min(y, zero_y), abs(y - zero_y)
            if muted and i < len(muted) and muted[i]:
                fill, opacity = "var(--muted)", "0.45"
            else:
                fill = "var(--good)" if float(val) >= 0 else "var(--bad)"
                opacity = "0.9"
            body += (
                f'<rect x="{_n(cx - bar_w / 2)}" y="{_n(top_y)}" width="{_n(bar_w)}" '
                f'height="{_n(max(bar_h, 0.7))}" rx="2" fill="{fill}" opacity="{opacity}"/>'
            )
        body += (f'<text x="{_n(cx)}" y="{height - PAD_B + 14}" text-anchor="middle" '
                 f'font-size="10.5" fill="var(--muted)">{_e(lab)}</text>')
        if annotations and i < len(annotations) and annotations[i]:
            body += (f'<text x="{_n(cx)}" y="{height - PAD_B + 25}" text-anchor="middle" '
                     f'font-size="9.5" fill="var(--muted)">{_e(annotations[i])}</text>')

    return _svg(body, height + (12 if annotations else 0), label or "bar chart")


def diverging_bars(
    items: Sequence[Tuple[str, float]],
    *,
    label: str = "",
    row_h: int = 22,
    value_format=None,
) -> str:
    """
    Horizontal bars either side of a centre line, largest magnitude first.

    This is the "why did it score that" chart: each row is one factor's signed
    contribution to the composite. A centred axis is the whole point -- the
    reader needs to see instantly which factors *pushed the stock down*, and a
    conventional left-anchored bar chart hides that behind a minus sign.
    """
    rows = [(str(k), float(v)) for k, v in items if _finite(v)]
    if not rows:
        return _EMPTY
    rows.sort(key=lambda kv: abs(kv[1]), reverse=True)

    fmt = value_format or (lambda v: f"{v:+.2f}")
    height = PAD_T + len(rows) * row_h + 10
    mid = PAD_L + (W - PAD_L - PAD_R) * 0.42   # left of centre: labels need room
    half = min(mid - PAD_L - 4, W - PAD_R - mid - 56)
    peak = max(abs(v) for _, v in rows) or 1.0

    body = (f'<line x1="{_n(mid)}" y1="{PAD_T - 4}" x2="{_n(mid)}" '
            f'y2="{_n(height - 6)}" stroke="var(--line)" stroke-width="1"/>')

    for i, (name, val) in enumerate(rows):
        y = PAD_T + i * row_h
        w = abs(val) / peak * half
        x = mid if val >= 0 else mid - w
        fill = "var(--good)" if val >= 0 else "var(--bad)"
        body += (
            f'<rect x="{_n(x)}" y="{_n(y + 3)}" width="{_n(max(w, 1))}" '
            f'height="{row_h - 9}" rx="2" fill="{fill}" opacity="0.9"/>'
            f'<text x="{PAD_L - 6}" y="{_n(y + row_h - 7)}" text-anchor="end" '
            f'font-size="11" fill="var(--ink)">{_e(name)}</text>'
            f'<text x="{_n(mid + half + 6)}" y="{_n(y + row_h - 7)}" '
            f'font-size="11" fill="var(--muted)">{_e(fmt(val))}</text>'
        )
    return _svg(body, height, label or "factor contributions")


def heatmap(
    matrix: Sequence[Sequence[float]],
    labels: Sequence[str],
    *,
    label: str = "",
    vmin: float = -1.0,
    vmax: float = 1.0,
) -> str:
    """
    Square correlation grid.

    Colour is opacity over the good/bad variables rather than a bespoke colour
    ramp, so it stays legible in both themes and needs no legend beyond the
    printed number -- which is shown in every cell, because a heatmap you have to
    eyeball against a colour bar is not an answer.
    """
    labels = list(labels)
    if not labels or not matrix:
        return _EMPTY

    n = len(labels)
    gutter = 96                       # room for the row names
    cell = min(38.0, (W - gutter - PAD_R) / n)
    grid = cell * n
    height = int(PAD_T + grid + 78)   # + rotated column labels

    body = ""
    for r in range(n):
        for c in range(min(n, len(matrix[r]))):
            v = matrix[r][c]
            if not _finite(v):
                continue
            v = max(vmin, min(vmax, float(v)))
            x, y = gutter + c * cell, PAD_T + r * cell
            fill = "var(--good)" if v >= 0 else "var(--bad)"
            body += (
                f'<rect x="{_n(x)}" y="{_n(y)}" width="{_n(cell)}" height="{_n(cell)}" '
                f'fill="{fill}" opacity="{abs(v) * 0.75:.2f}" stroke="var(--bg)"/>'
                f'<text x="{_n(x + cell / 2)}" y="{_n(y + cell / 2 + 3.5)}" '
                f'text-anchor="middle" font-size="9.5" fill="var(--ink)">{v:.2f}</text>'
            )
        body += (f'<text x="{gutter - 6}" y="{_n(PAD_T + r * cell + cell / 2 + 3.5)}" '
                 f'text-anchor="end" font-size="10.5" fill="var(--muted)">{_e(labels[r])}</text>')

    for c in range(n):
        x = gutter + c * cell + cell / 2
        y = PAD_T + grid + 8
        body += (f'<text x="{_n(x)}" y="{_n(y)}" font-size="10.5" fill="var(--muted)" '
                 f'transform="rotate(45 {_n(x)} {_n(y)})">{_e(labels[c])}</text>')

    return _svg(body, height, label or "correlation matrix")
