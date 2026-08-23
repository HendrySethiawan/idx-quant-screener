# src/report/layout.py
"""
Page furniture: the tab strip.

The brief grew to roughly thirteen screens of stacked sections, and Advanced alone
was eight. Nine sections that are never useful at the same moment do not need to be
on screen at the same moment.

Labels are passed in by the caller rather than scraped out of the section HTML.
Two reasons: nothing has to parse markup this project generated, and a tab label
wants to be shorter than the heading it sits above -- "Worth" over "What is it
worth?" -- so they are genuinely different strings, not a duplicated one waiting to
drift.

The failure mode worth knowing about: a section that renders but gets no tab is
invisible, and unlike a stacked page nothing reveals it. `tabbed` therefore builds
tabs and panels in the same loop, from the same list, so one cannot exist without
the other. There is a test asserting it anyway.
"""
from __future__ import annotations

import html
import re
from typing import List, Sequence, Tuple


def _e(v) -> str:
    return html.escape("" if v is None else str(v))


def _slug(value: str) -> str:
    out = re.sub(r"[^a-z0-9]+", "-", str(value).lower()).strip("-")
    return out or "panel"


def tabbed(panels: Sequence[Tuple[str, str]], group: str, active: int = 0) -> str:
    """
    Render `(short label, section html)` pairs as one tab strip over one visible
    panel.

    Sections with no content are dropped before anything is built, so an empty
    section can never leave a live tab pointing at a dead end -- which reads as a
    broken page rather than as "there was nothing to show".

    A single section is returned bare: a tab strip offering one choice is furniture
    with no purpose.
    """
    kept: List[Tuple[str, str]] = [
        (label, body) for label, body in panels if body and str(body).strip()
    ]
    if not kept:
        return ""
    if len(kept) == 1:
        return kept[0][1]

    active = max(0, min(int(active), len(kept) - 1))
    tabs, bodies = "", ""

    for i, (label, body) in enumerate(kept):
        pid = f"{_slug(group)}-{_slug(label)}-{i}"
        on = " on" if i == active else ""
        tabs += (
            f'<button type="button" role="tab" class="tab{on}" data-panel="{pid}" '
            f'aria-selected="{"true" if i == active else "false"}" '
            f'aria-controls="{pid}">{_e(label)}</button>'
        )
        bodies += (
            f'<section class="panel{on}" id="{pid}" role="tabpanel" '
            f'aria-label="{_e(label)}">{body}</section>'
        )

    return (
        f'<div class="tabwrap" data-group="{_e(group)}">'
        f'<div class="tabs" role="tablist">{tabs}</div>'
        f"{bodies}</div>"
    )
