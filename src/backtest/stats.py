# src/backtest/stats.py
"""
Whether a difference between two simulated strategies means anything.

Written because it did not exist, and its absence cost three wrong conclusions in
a single afternoon. The report printed differences as bare numbers -- "-3.39pp",
"+26.8pp" -- with nothing saying which of them a five-year window could actually
resolve, and every one of them got read as a result.

**Stability is the headline, not the t-statistic.** The lesson is specific and
worth keeping in front of whoever reads this next. Removing the regime ladder
measured +26.8pp of CAGR at t = 2.99, comfortably past the usual bar. Split in
half:

    first half    -0.6 pp
    second half  +61.4 pp

The whole effect was one market episode sampled weekly. The t-statistic assumed
259 independent observations; there was arguably one. A difference that changes
sign across the window is unstable no matter what its t says, so `split_half` runs
first and `paired_significance` second.

**Paired, never unpaired.** Two variants share a universe, a calendar and most of
their holdings, so the common market factor cancels in the difference and the test
is far more powerful than comparing two CAGRs against their own standard errors.
On this data the paired minimum detectable effect is about 10pp of CAGR; the
unpaired equivalent is far worse.

Pure functions over two equity curves. No config, no I/O, no opinions about which
strategy is which -- the caller names them.
"""
from __future__ import annotations

from typing import Dict, Optional

import numpy as np
import pandas as pd

# Two-sided 95%. Stated as a constant because the report quotes it, and a bar the
# reader cannot see is a bar they cannot argue with.
Z95 = 1.96

# Below this, the quieter half of the window carries so little of the effect that
# calling it a two-half result flatters it. A judgement, not a measurement -- but
# an explicit one, and the alternative is sign agreement alone, which passes
# "nothing, then everything" as stable.
CONCENTRATED = 0.25


def _aligned_diff(a_equity, b_equity) -> Optional[pd.Series]:
    """
    Period returns of b minus a, on the dates both actually have.

    Returns None rather than an empty frame when there is nothing to compare, so
    every caller has one obvious "cannot tell" branch instead of guessing at the
    meaning of a zero-length series.
    """
    if a_equity is None or b_equity is None:
        return None
    a = pd.Series(a_equity).dropna()
    b = pd.Series(b_equity).dropna()
    if len(a) < 3 or len(b) < 3:
        return None
    ra, rb = a.pct_change().dropna(), b.pct_change().dropna()
    common = ra.index.intersection(rb.index)
    if len(common) < 3:
        return None
    diff = (rb[common] - ra[common]).replace([np.inf, -np.inf], np.nan).dropna()
    return diff if len(diff) >= 3 else None


def _annualise(per_period: float, periods_per_year: int) -> float:
    """A per-period mean as a percentage a year. Guarded against absurd inputs."""
    try:
        return float(((1.0 + per_period) ** periods_per_year - 1.0) * 100.0)
    except (OverflowError, ValueError):
        return float("nan")


def paired_significance(a_equity, b_equity,
                        periods_per_year: int = 52) -> Dict[str, object]:
    """
    Is `b` distinguishable from `a`, given how noisy the difference between them is?

    `mde_pp` is the smallest annualised difference this comparison could have
    resolved -- the number that says whether a null result means "no effect" or
    "not enough data". Those are entirely different findings and the report used to
    show neither.
    """
    empty = {"gap_pp": None, "t": None, "mde_pp": None, "n": 0,
             "distinguishable": False, "reason": "not enough overlapping history"}
    diff = _aligned_diff(a_equity, b_equity)
    if diff is None:
        return empty

    n = int(len(diff))
    sd = float(diff.std(ddof=1))
    gap = _annualise(float(diff.mean()), periods_per_year)

    # Identical strategies. A real answer, and not a division by zero.
    if sd == 0 or not np.isfinite(sd):
        return {"gap_pp": gap, "t": None, "mde_pp": 0.0, "n": n,
                "distinguishable": False,
                "reason": "identical - the change never binds"}

    se = sd / np.sqrt(n)
    t = float(diff.mean() / se)
    return {
        "gap_pp": gap,
        "t": t,
        "mde_pp": _annualise(Z95 * se, periods_per_year),
        "n": n,
        "distinguishable": bool(abs(t) > Z95),
        "reason": "",
    }


def split_half(a_equity, b_equity,
               periods_per_year: int = 52) -> Dict[str, object]:
    """
    Does the difference exist in both halves of the window, or only in one?

    The check that matters most here, and the one this module was written for. An
    effect concentrated in a single half is one market episode, however many
    weekly observations it was sampled at -- and `paired_significance` cannot see
    that, because it treats every week as independent evidence.

    `sign_stable` is False when the halves disagree in direction. It is
    deliberately a crude test: with roughly 23 regime episodes in five years,
    anything finer would be reading structure that is not there.
    """
    empty = {"first_pp": None, "second_pp": None, "sign_stable": False,
             "reason": "not enough overlapping history"}
    diff = _aligned_diff(a_equity, b_equity)
    if diff is None or len(diff) < 8:
        return empty

    half = len(diff) // 2
    first = _annualise(float(diff.iloc[:half].mean()), periods_per_year)
    second = _annualise(float(diff.iloc[half:].mean()), periods_per_year)
    if not (np.isfinite(first) and np.isfinite(second)):
        return {**empty, "reason": "halves could not be annualised"}

    # Exactly zero in a half means the change did nothing there, which is not a
    # disagreement about direction.
    stable = bool(first * second > 0 or first == 0 or second == 0)

    # Sign agreement alone is too lenient: +0.1 then +61.4 agrees in direction and
    # is still one episode. `concentration` is how much the quieter half carries,
    # as a share of the louder one -- 1.0 is a perfectly even effect, near 0 is a
    # single episode wearing a two-half disguise. On the real ablation the stops
    # scored 0.17, which is worth knowing even though their sign is consistent.
    lo, hi = sorted((abs(first), abs(second)))
    concentration = float(lo / hi) if hi > 0 else 1.0
    reason = ""
    if not stable:
        reason = "effect reverses between the two halves"
    elif concentration < CONCENTRATED:
        reason = "effect is concentrated in one half of the window"

    return {"first_pp": first, "second_pp": second, "sign_stable": stable,
            "concentration": concentration,
            "even": bool(stable and concentration >= CONCENTRATED),
            "reason": reason}


def verdict(a_equity, b_equity, periods_per_year: int = 52) -> Dict[str, object]:
    """
    Both tests, and the sentence to print. Stability decides the wording.

    The ordering is the whole point: a result that flips sign is reported unstable
    even when its t clears the bar, because that is precisely the case that fooled
    the author of this module.
    """
    sig = paired_significance(a_equity, b_equity, periods_per_year)
    half = split_half(a_equity, b_equity, periods_per_year)
    out = {**sig, **{f"half_{k}": v for k, v in half.items()}}

    if sig.get("reason", "").startswith("identical"):
        out["verdict"] = "never binds"
    elif sig["gap_pp"] is None:
        out["verdict"] = "cannot tell"
    elif half["sign_stable"] is False:
        out["verdict"] = "unstable - reverses between halves"
    elif not half.get("even", True):
        out["verdict"] = "unstable - concentrated in one half"
    elif sig["distinguishable"]:
        out["verdict"] = "distinguishable"
    else:
        out["verdict"] = "direction stable, size unknown"
    return out
