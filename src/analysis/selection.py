# src/analysis/selection.py
"""
Diversification caps for the shortlist.

Two gates, both walking a best-first ranking and both explaining what they passed
over. `sector_capped_pick` caps by label; `decorrelated_pick` caps by behaviour.

The second exists because a label is not a behaviour. Measured on this universe: the median pair correlates 0.30 and only 21 of
2,401 pairs exceed 0.70. The ones that do are same-group and same-industry --
BRPT/PTRO at 0.87 (one conglomerate, and in DIFFERENT sectors, so the label
cap never sees them), BBRI/BMRI and BBNI/BBRI at 0.80. Those are one bet in
two tickers.

Worth being precise about the size of this: it is a safety net, not an active
filter. On a typical day nothing is skipped. What it changes every day is that
the book's actual correlation becomes a number on the page instead of an
assumption -- and the assumption was wrong when I first made it, guessing from
sector names that three commodity stocks must be one trade when they correlate
0.30.
"""
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


DEFAULT_MAX_CORRELATION = 0.70


def _corr(matrix, a: str, b: str) -> Optional[float]:
    """One pair, or None when the matrix cannot speak to it."""
    if matrix is None:
        return None
    try:
        value = matrix.at[a, b]
    except (KeyError, ValueError, TypeError):
        return None
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return None if value != value else value      # NaN means "no opinion"


def decorrelated_pick(
    ranked: Sequence[str],
    correlations,
    top_n: int,
    max_correlation: float = DEFAULT_MAX_CORRELATION,
    skipped: Optional[Dict[str, str]] = None,
) -> List[str]:
    """
    Walk a best-first ranking, skipping names that move with something already taken.

    Same shape and same contract as `sector_capped_pick`: soft, explains itself
    through the `skipped` out-param, and backfills rather than returning a short
    list. A half-empty ticket is worse than a slightly correlated one -- and at
    Rp10 juta the book is three names, so refusing to fill it would leave nothing
    to act on.

    A pair the matrix has no opinion about (missing, or too little overlap to
    measure) is allowed through. Absence of evidence is not evidence of
    correlation, and blocking on it would quietly shrink the universe to whatever
    happens to have a long price history.
    """
    ranked = list(ranked)
    if skipped is not None:
        skipped.clear()
    if correlations is None or max_correlation is None or max_correlation >= 1.0:
        return ranked[:top_n]

    picked: List[str] = []
    for ticker in ranked:
        if len(picked) >= top_n:
            break

        worst_with, worst = None, None
        for held in picked:
            value = _corr(correlations, ticker, held)
            if value is None:
                continue
            if worst is None or value > worst:
                worst_with, worst = held, value

        if worst is not None and worst > max_correlation:
            if skipped is not None:
                skipped[ticker] = (
                    f"moves with {worst_with} ({worst:.2f} correlated) - "
                    f"holding both is one bet in two names"
                )
            continue
        picked.append(ticker)

    if len(picked) < top_n:
        for ticker in ranked:
            if len(picked) >= top_n:
                break
            if ticker not in picked:
                picked.append(ticker)
                # Let back in, so it was not excluded after all. Saying otherwise
                # would be an explanation of a decision that was never made.
                if skipped is not None:
                    skipped.pop(ticker, None)

    return picked


def average_correlation(tickers: Sequence[str], correlations) -> Optional[float]:
    """
    Mean pairwise correlation of a book, so "one bet in three names" is visible.

    None below two names, or when nothing can be measured -- rather than 0.0,
    which would read as "perfectly diversified".
    """
    names = list(tickers)
    if correlations is None or len(names) < 2:
        return None

    values = [
        v for i, a in enumerate(names) for b in names[i + 1:]
        if (v := _corr(correlations, a, b)) is not None
    ]
    return round(sum(values) / len(values), 2) if values else None
