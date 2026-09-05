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


def tie_groups(ranked: Sequence[str], scores: Dict[str, float], floor: float,
               max_share: float = 1 / 3) -> List[List[str]]:
    """
    Consecutive names the score cannot separate, walking the ranking best-first.

    `floor` is how far a score moves when the universe gains or loses one member,
    measured by jackknife rather than chosen. A gap smaller than that is not a
    difference between two companies; it is the arithmetic of who else happened to
    be in the list that day.

    Grouped only between ADJACENT names, so a chain of small gaps cannot merge two
    names that are genuinely far apart. On the real universe this produces short
    groups -- {TINS, ADRO} and {TAPG, SRTG} -- and leaves the clear leaders alone.

    `max_share` refuses the whole thing when one group swallows more than that
    fraction of the list. A tie group covering most of the universe is not an
    observation about the market, it is a unit mismatch: the floor is measured on
    the raw composite, whose spread is about 3.75, and comparing it against the
    0-to-1 display score made all 74 names one group. Everything downstream then
    read as tied, and the entire ticket was chosen by correlation with the ranking
    ignored. Refusing here makes that a no-op instead of a silently reordered book.
    """
    out: List[List[str]] = []
    if not ranked:
        return out
    if not floor or floor <= 0:
        return [[t] for t in ranked]

    current = [ranked[0]]
    for prev, ticker in zip(ranked, ranked[1:]):
        a, b = scores.get(prev), scores.get(ticker)
        if a is None or b is None or abs(a - b) > floor:
            out.append(current)
            current = [ticker]
        else:
            current.append(ticker)
    out.append(current)

    biggest = max(len(g) for g in out)
    if biggest > max(3, int(len(ranked) * max_share)):
        return [[t] for t in ranked]
    return out


def break_ties(ranked: Sequence[str], scores: Dict[str, float], correlations,
               floor: float, groups: Optional[List[List[str]]] = None) -> List[str]:
    """
    Reorder names the score cannot separate, preferring the one that diversifies.

    A ranking is an ordered list and the ticket buys from the top of it, so a
    0.02 difference on a scale whose own precision is 0.10 decides a purchase.
    That is a real decision made by a number that is not really there.

    Where the score is silent, this asks a question it can answer: of these
    equals, which moves least like the names already taken? It walks the groups in
    rank order and, within each, repeatedly takes whichever remaining name has the
    lowest maximum correlation to everything picked so far.

    It never reorders ACROSS groups, so a name the score genuinely prefers keeps
    its place. `decorrelated_pick`'s hard 0.70 gate is untouched and still runs
    afterwards -- this only chooses among names that gate would treat identically.
    """
    ranked = list(ranked)
    if correlations is None:
        return ranked

    out: List[str] = []
    for group in (groups if groups is not None
                  else tie_groups(ranked, scores, floor)):
        if len(group) == 1:
            out.extend(group)
            continue
        remaining = list(group)
        while remaining:
            best, best_corr = remaining[0], None
            for ticker in remaining:
                worst = None
                for held in out:
                    value = _corr(correlations, ticker, held)
                    if value is not None and (worst is None or value > worst):
                        worst = value
                # No opinion ranks as neutral, never as decorrelated: an unmeasured
                # pair must not win a tie on the strength of being unmeasured.
                key = 0.0 if worst is None else worst
                if best_corr is None or key < best_corr:
                    best, best_corr = ticker, key
            out.append(best)
            remaining.remove(best)
    return out


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
