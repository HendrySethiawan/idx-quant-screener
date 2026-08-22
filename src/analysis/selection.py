# src/analysis/selection.py
"""Diversification cap for the shortlist."""
from __future__ import annotations

from typing import Dict, List, Optional, Sequence


def sector_capped_pick(
    ranked: Sequence[str],
    sector_of: Optional[Dict[str, str]],
    top_n: int,
    max_per_sector: int,
) -> List[str]:
    """
    Walk a best-first ranking, skipping names whose sector is already full.

    If the cap starves the list below `top_n`, the remaining slots are backfilled
    with the next-best names ignoring the cap -- a half-empty shortlist is worse
    than a slightly concentrated one.
    """
    ranked = list(ranked)
    if not max_per_sector or not sector_of:
        return ranked[:top_n]

    counts: Dict[str, int] = {}
    picked: List[str] = []

    for ticker in ranked:
        if len(picked) >= top_n:
            break
        sector = sector_of.get(ticker, "Unknown")
        if counts.get(sector, 0) >= max_per_sector:
            continue
        counts[sector] = counts.get(sector, 0) + 1
        picked.append(ticker)

    if len(picked) < top_n:
        for ticker in ranked:
            if len(picked) >= top_n:
                break
            if ticker not in picked:
                picked.append(ticker)

    return picked
