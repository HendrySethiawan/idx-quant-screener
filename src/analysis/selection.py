# src/analysis/selection.py
"""Diversification cap for the shortlist."""
from __future__ import annotations

from typing import Dict, List, Optional, Sequence


def sector_capped_pick(
    ranked: Sequence[str],
    sector_of: Optional[Dict[str, str]],
    top_n: int,
    max_per_sector: int,
    skipped: Optional[Dict[str, str]] = None,
) -> List[str]:
    """
    Walk a best-first ranking, skipping names whose sector is already full.

    If the cap starves the list below `top_n`, the remaining slots are backfilled
    with the next-best names ignoring the cap -- a half-empty shortlist is worse
    than a slightly concentrated one.

    `skipped` is an optional out-param: pass a dict and it is filled with
    ticker -> why the cap passed over it. It is written here, in the loop that
    already knows the answer, rather than re-derived by a caller -- a second
    implementation of this rule would drift from this one and start explaining
    decisions that were never made. A name that is later backfilled has its entry
    removed, because it did end up on the list.
    """
    ranked = list(ranked)
    if skipped is not None:
        skipped.clear()
    if not max_per_sector or not sector_of:
        return ranked[:top_n]

    counts: Dict[str, int] = {}
    picked: List[str] = []

    for ticker in ranked:
        if len(picked) >= top_n:
            break
        sector = sector_of.get(ticker, "Unknown")
        if counts.get(sector, 0) >= max_per_sector:
            if skipped is not None:
                skipped[ticker] = (
                    f"{max_per_sector} {sector} names already ranked above it"
                )
            continue
        counts[sector] = counts.get(sector, 0) + 1
        picked.append(ticker)

    if len(picked) < top_n:
        for ticker in ranked:
            if len(picked) >= top_n:
                break
            if ticker not in picked:
                picked.append(ticker)
                # It was skipped by the cap and then let back in, so it was not
                # actually excluded. Saying otherwise would be a false explanation.
                if skipped is not None:
                    skipped.pop(ticker, None)

    return picked
