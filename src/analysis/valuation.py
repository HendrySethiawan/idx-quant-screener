# src/analysis/valuation.py
"""
What is it worth? -- as opposed to where does it rank.

`undervaluation_score` is min-max normalised across today's universe, so exactly
one stock scores 1.0 and one scores 0.0 whatever the market is doing. That is a
ranking, and it cannot answer "is this cheap". This module answers it, within the
limits of the data.

Method: two independent estimates from fields already fetched, no new network call.

    EPS  = price / pe_ratio          fair_pe = EPS  x peer median P/E
    BVPS = price / price_to_book     fair_pb = BVPS x peer median P/B

The fair zone is the gap BETWEEN the two estimates, not a band around their
average. When the two measures agree the zone is tight and the verdict is
confident; when they disagree the zone is wide and says so on its face. That
removes the need to invent a "+/- 15% is fair" threshold, which would claim the
same confidence for a name whose measures are 3% apart and one where they are
177% apart -- both of which occur in the real universe.

Three things this deliberately does NOT do:

  * It never reads a winsorized multiple. `_winsorize` clips outliers to a shared
    bound, so six unrelated tickers can carry an identical P/E; that is fine for
    ranking and fatal for valuation. Inputs come from `unclipped_*`.
  * It does not judge whether a premium is deserved. A high-ROE company should
    trade above its peers, and this method will call it overvalued. ROE travels
    with the verdict so the reader can apply that judgement themselves.
  * It cannot see the whole market being expensive. Everything is measured against
    peers, so if all of IDX is dear, everything reads fair.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

# Verdicts. UNKNOWN and ONE_MEASURE are distinct states on purpose: "we cannot
# tell" and "we can half tell" are different messages, and collapsing either into
# "fair" would be a claim we have not earned.
UNDERVALUED = "undervalued"
FAIR = "fair"
OVERVALUED = "overvalued"
ONE_MEASURE = "one_measure"
UNKNOWN = "unknown"

VERDICT_LABEL = {
    UNDERVALUED: "below peers",
    FAIR: "in line with peers",
    OVERVALUED: "above peers",
    ONE_MEASURE: "one measure only",
    UNKNOWN: "cannot value",
}

DEFAULT_MIN_PEERS = 4
DEFAULT_WIDE_BAND = 0.60


def _f(v) -> Optional[float]:
    """A finite float, or None. pandas hands back several flavours of not-a-number."""
    if v is None:
        return None
    try:
        out = float(v)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _positive(v) -> Optional[float]:
    """Multiples and per-share figures are only meaningful above zero."""
    out = _f(v)
    return out if out is not None and out > 0 else None


def peer_medians(
    df: pd.DataFrame,
    col: str,
    min_peers: int = DEFAULT_MIN_PEERS,
    sector_col: str = "sector",
) -> Tuple[pd.Series, pd.Series]:
    """
    Median of `col` within each sector, falling back to the whole universe.

    Mirrors `FundamentalEngine._sector_neutral_z`: start from the global figure and
    overwrite only those sectors with enough observations to mean anything. Four of
    the nine sectors in the real universe hold two names, and a "sector median" of
    two is noise wearing a suit.

    Returns (median per row, name of the group actually used per row) -- the second
    is not diagnostics, it goes on the page. "Median of 11 banks" and "median of all
    49" support very different conclusions.
    """
    values = pd.to_numeric(df.get(col), errors="coerce") if col in df.columns else None
    if values is None:
        empty = pd.Series(np.nan, index=df.index, dtype=float)
        return empty, pd.Series("none", index=df.index)

    universe = values[values > 0].median()
    medians = pd.Series(universe, index=df.index, dtype=float)
    groups = pd.Series("universe", index=df.index, dtype=object)

    if sector_col in df.columns:
        for sector, idx in values.groupby(df[sector_col]).groups.items():
            valid = values.loc[idx]
            valid = valid[valid > 0].dropna()
            if len(valid) < int(min_peers):
                continue
            medians.loc[idx] = valid.median()
            groups.loc[idx] = str(sector)

    return medians, groups


@dataclass
class Valuation:
    """One name's answer. `notes` is why, in words the brief can print."""
    ticker: str
    price: Optional[float] = None
    eps: Optional[float] = None
    bvps: Optional[float] = None
    peer_pe: Optional[float] = None
    peer_pb: Optional[float] = None
    fair_pe: Optional[float] = None
    fair_pb: Optional[float] = None
    zone_lo: Optional[float] = None
    zone_hi: Optional[float] = None
    verdict: str = UNKNOWN
    gap_pct: Optional[float] = None       # distance to the nearest zone edge; 0 inside
    disagreement_pct: Optional[float] = None
    peer_group: str = "universe"
    roe: Optional[float] = None
    notes: List[str] = field(default_factory=list)

    @property
    def n_methods(self) -> int:
        return sum(x is not None for x in (self.fair_pe, self.fair_pb))

    @property
    def wide(self) -> bool:
        return bool(self.notes) and any("disagree" in n for n in self.notes)


def _verdict_for(price: float, lo: float, hi: float) -> Tuple[str, float]:
    """Verdict plus distance to the nearest edge, as a signed fraction of that edge."""
    if price < lo:
        return UNDERVALUED, price / lo - 1.0
    if price > hi:
        return OVERVALUED, price / hi - 1.0
    return FAIR, 0.0


def value_one(
    row,
    peer_pe: Optional[float],
    peer_pb: Optional[float],
    peer_group: str = "universe",
    wide_band_pct: float = DEFAULT_WIDE_BAND,
) -> Valuation:
    """
    Value a single row. Never raises and never drops -- an unusable name comes back
    as UNKNOWN carrying the reason, because a stock silently missing from the list
    reads as "nothing to say about it", which is not the same as "we cannot see it".
    """
    out = Valuation(ticker=str(row.get("ticker", "")), peer_group=peer_group)
    out.price = _positive(row.get("last_close"))
    out.roe = _f(row.get("roe"))
    out.peer_pe, out.peer_pb = _positive(peer_pe), _positive(peer_pb)

    if out.price is None:
        out.notes.append("no price")
        return out

    # unclipped_* is the whole point: see the module docstring.
    pe = _positive(row.get("unclipped_pe_ratio", row.get("pe_ratio")))
    pb = _positive(row.get("unclipped_price_to_book", row.get("price_to_book")))

    if pe is not None:
        out.eps = out.price / pe
        if out.peer_pe is not None:
            out.fair_pe = out.eps * out.peer_pe
    if pb is not None:
        out.bvps = out.price / pb
        if out.peer_pb is not None:
            out.fair_pb = out.bvps * out.peer_pb

    estimates = [v for v in (out.fair_pe, out.fair_pb) if v is not None and v > 0]

    if not estimates:
        missing = []
        if pe is None:
            missing.append("no usable P/E")
        if pb is None:
            missing.append("no usable P/B")
        out.notes.append(" and ".join(missing) or "no usable multiple")
        return out

    if len(estimates) == 1:
        # One estimate is a point, and a point has no zone. Reporting it as a range
        # of zero width would imply certainty we do not have, so this gets its own
        # state rather than being forced into undervalued/fair/overvalued.
        only = estimates[0]
        out.zone_lo = out.zone_hi = only
        out.verdict = ONE_MEASURE
        out.gap_pct = out.price / only - 1.0
        basis = "earnings" if out.fair_pe is not None else "book value"
        out.notes.append(f"{basis} only -- the other measure is unusable here")
        return out

    out.zone_lo, out.zone_hi = min(estimates), max(estimates)
    out.verdict, out.gap_pct = _verdict_for(out.price, out.zone_lo, out.zone_hi)

    mid = (out.zone_lo + out.zone_hi) / 2.0
    if mid > 0:
        out.disagreement_pct = (out.zone_hi - out.zone_lo) / mid
        if out.disagreement_pct > float(wide_band_pct):
            out.notes.append(
                f"the two measures disagree by {out.disagreement_pct * 100:.0f}% "
                f"-- treat this as a hint, not a number"
            )
    return out


def value_universe(df: pd.DataFrame, settings=None) -> pd.DataFrame:
    """
    Add valuation columns to a scored frame. Returns a copy; never drops a row.

    Column names are prefixed `value_`/`fair_` so they cannot collide with the
    factor and z-score columns already in the frame.
    """
    cfg = (getattr(settings, "valuation", None) or {}) if settings is not None else {}
    min_peers = int(cfg.get("min_peers", DEFAULT_MIN_PEERS))
    wide = float(cfg.get("wide_band_pct", DEFAULT_WIDE_BAND))

    out = df.copy()
    if out.empty:
        for col in ("fair_pe", "fair_pb", "value_zone_lo", "value_zone_hi",
                    "value_gap_pct", "value_disagreement_pct", "value_eps", "value_bvps"):
            out[col] = pd.Series(dtype=float)
        out["value_verdict"] = pd.Series(dtype=object)
        out["value_peer_group"] = pd.Series(dtype=object)
        out["value_note"] = pd.Series(dtype=object)
        return out

    # Peer medians come from the CLIPPED columns on purpose. A median is robust to
    # outliers anyway, and the clipped frame is what the rest of the tool agrees on.
    pe_med, pe_grp = peer_medians(out, "pe_ratio", min_peers)
    pb_med, _ = peer_medians(out, "price_to_book", min_peers)

    results = [
        value_one(row, pe_med.get(i), pb_med.get(i), str(pe_grp.get(i, "universe")), wide)
        for i, row in out.iterrows()
    ]

    out["value_eps"] = [v.eps for v in results]
    out["value_bvps"] = [v.bvps for v in results]
    out["value_peer_pe"] = [v.peer_pe for v in results]
    out["value_peer_pb"] = [v.peer_pb for v in results]
    out["fair_pe"] = [v.fair_pe for v in results]
    out["fair_pb"] = [v.fair_pb for v in results]
    out["value_zone_lo"] = [v.zone_lo for v in results]
    out["value_zone_hi"] = [v.zone_hi for v in results]
    out["value_verdict"] = [v.verdict for v in results]
    out["value_gap_pct"] = [v.gap_pct for v in results]
    out["value_disagreement_pct"] = [v.disagreement_pct for v in results]
    out["value_peer_group"] = [v.peer_group for v in results]
    out["value_note"] = ["; ".join(v.notes) for v in results]
    return out


def coverage(df: pd.DataFrame) -> Dict[str, int]:
    """How many names each state applies to -- printed on the page, not logged."""
    if "value_verdict" not in df.columns:
        return {}
    counts = df["value_verdict"].value_counts().to_dict()
    return {
        "total": len(df),
        "valued": int(sum(counts.get(k, 0) for k in (UNDERVALUED, FAIR, OVERVALUED))),
        "undervalued": int(counts.get(UNDERVALUED, 0)),
        "fair": int(counts.get(FAIR, 0)),
        "overvalued": int(counts.get(OVERVALUED, 0)),
        "one_measure": int(counts.get(ONE_MEASURE, 0)),
        "unknown": int(counts.get(UNKNOWN, 0)),
    }


def describe(v: Valuation) -> str:
    """One line for the brief. Plain language: the reader is not a quant."""
    if v.verdict == UNKNOWN:
        return f"No valuation possible -- {'; '.join(v.notes) or 'insufficient data'}."
    if v.verdict == ONE_MEASURE:
        return (f"Around {v.zone_lo:,.0f} on {v.notes[0] if v.notes else 'one measure'} "
                f"-- no range, so treat it loosely.")
    where = {
        UNDERVALUED: f"{abs(v.gap_pct or 0) * 100:.0f}% below",
        OVERVALUED: f"{abs(v.gap_pct or 0) * 100:.0f}% above",
        FAIR: "inside",
    }[v.verdict]
    line = (f"Peers imply Rp{v.zone_lo:,.0f}-Rp{v.zone_hi:,.0f}; "
            f"price is {where} that range.")
    if v.wide:
        line += " The two measures disagree a lot, so this is weak evidence."
    return line
