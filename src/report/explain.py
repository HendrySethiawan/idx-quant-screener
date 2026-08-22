# src/report/explain.py
"""
Turn z-scores into sentences a non-specialist can act on.

Reads only the `z_*` columns, never raw fundamentals. A z-score is already
normalised across the universe, so "1.8 standard deviations cheaper than the rest
of the market" is comparable between P/E and dividend yield -- a raw P/E of 7 is
not comparable to a raw yield of 0.08.

Thresholds: 1.5 sigma reads as "strong", 0.75 as "notable". Both are cosmetic and
affect wording only, never the ranking.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

import pandas as pd

_STRONG = 1.5
_NOTABLE = 0.75

# factor -> (favourable phrase, unfavourable phrase)
_PHRASES: Dict[str, Tuple[str, str]] = {
    "pe_ratio": ("cheap earnings multiple", "expensive on earnings"),
    "price_to_book": ("trading below book value", "pricey versus book"),
    "dividend_yield": ("strong dividend", "little or no dividend"),
    "roe": ("high return on equity", "weak return on equity"),
    "gross_margin": ("healthy margins", "thin margins"),
    "debt_to_equity": ("low debt", "heavily indebted"),
    "realized_vol": ("calm price action", "very volatile"),
    "mom_1m": ("rising this month", "falling this month"),
    "mom_6m": ("strong 6-month trend", "weak 6-month trend"),
    "mom_12m": ("strong 1-year trend", "weak 1-year trend"),
}

_LABELS: Dict[str, Tuple[str, str, str]] = {
    # factor -> (strong-good, mild-good, strong-bad)  used for the detail table
    "pe_ratio": ("Much cheaper than the market", "Slightly cheap", "Expensive"),
    "price_to_book": ("Far below book value", "Below book value", "Well above book value"),
    "dividend_yield": ("Top-tier dividend", "Above-average dividend", "Pays little"),
    "roe": ("Very profitable", "Above-average profitability", "Poor profitability"),
    "gross_margin": ("Excellent margins", "Decent margins", "Weak margins"),
    "debt_to_equity": ("Very little debt", "Modest debt", "High debt"),
    "realized_vol": ("Unusually calm", "Calmer than most", "Very choppy"),
    "mom_1m": ("Surging this month", "Drifting up", "Dropping this month"),
    "mom_6m": ("Strong 6-month run", "Mild 6-month uptrend", "6-month downtrend"),
    "mom_12m": ("Strong 1-year run", "Mild 1-year uptrend", "1-year downtrend"),
}

# Sign of the weight, i.e. which direction of z is *good* for the investor.
_FAVOURABLE_SIGN: Dict[str, float] = {
    "pe_ratio": -1.0, "price_to_book": -1.0, "dividend_yield": 1.0,
    "roe": 1.0, "gross_margin": 1.0, "debt_to_equity": -1.0,
    "realized_vol": -1.0, "mom_1m": 1.0, "mom_6m": 1.0, "mom_12m": 1.0,
}


def _base(factor: str) -> str:
    return factor[2:] if factor.startswith("z_") else factor


def _goodness(factor: str, z: float) -> float:
    """Signed 'how good for the investor', so all factors compare on one axis."""
    return z * _FAVOURABLE_SIGN.get(_base(factor), 1.0)


def zscore_label(factor: str, z: Optional[float]) -> str:
    key = _base(factor)
    if z is None or pd.isna(z):
        return "no data"
    good = _goodness(key, float(z))
    strong, mild, bad = _LABELS.get(key, ("Strong", "Above average", "Weak"))
    if good >= _STRONG:
        return strong
    if good >= _NOTABLE:
        return mild
    if good <= -_STRONG:
        return bad
    if good <= -_NOTABLE:
        return f"Slightly {bad.lower()}"
    return "Around average"


def reason_phrase(row, max_positives: int = 2) -> str:
    """
    "Cheap earnings multiple + strong 6-month trend (but heavily indebted)"

    Names the one or two things carrying the rank, plus the single worst red flag,
    so the user sees the trade-off rather than only the sales pitch.
    """
    scored: List[Tuple[float, str]] = []
    for col in getattr(row, "index", []):
        if not str(col).startswith("z_"):
            continue
        value = row[col]
        if pd.isna(value) or value == 0:
            continue
        key = _base(str(col))
        if key not in _PHRASES:
            continue
        scored.append((_goodness(key, float(value)), key))

    if not scored:
        return "No standout factors"

    scored.sort(reverse=True)
    positives = [(g, k) for g, k in scored if g >= _NOTABLE][:max_positives]
    negatives = [(g, k) for g, k in scored if g <= -_NOTABLE]

    if positives:
        good_text = " + ".join(_PHRASES[k][0].capitalize() if i == 0 else _PHRASES[k][0]
                               for i, (_, k) in enumerate(positives))
    else:
        good_text = "Nothing stands out"

    if negatives:
        worst = negatives[-1][1]
        return f"{good_text} (but {_PHRASES[worst][1]})"
    return good_text


def data_quality_note(row) -> str:
    """Warn when a rank leans on factors we never actually had."""
    imputed = row.get("imputed_factors", "") if hasattr(row, "get") else ""
    if not imputed or pd.isna(imputed):
        return ""
    names = [n for n in str(imputed).split(";") if n]
    if not names:
        return ""
    pretty = ", ".join(n.replace("_", " ") for n in names)
    return f"missing {pretty} - scored neutral on {'it' if len(names) == 1 else 'those'}"


def health_flags(row, rank: Optional[int], top_n: int) -> List[str]:
    """Short warnings for a holding: has it fallen out of favour, gone thin, etc."""
    flags: List[str] = []
    if rank is None:
        flags.append("no longer in the screened universe")
    elif rank > top_n:
        flags.append(f"has slipped to rank {rank}")

    mom = row.get("mom_1m") if hasattr(row, "get") else None
    if mom is not None and not pd.isna(mom) and mom < -10:
        flags.append(f"down {abs(float(mom)):.0f}% this month")
    return flags
