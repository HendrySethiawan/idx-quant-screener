# src/portfolio/sizing.py
"""
Lot-aware position sizing for a small IDX account.

IDX trades in 100-share lots, which at Rp 10 juta is a hard constraint rather than
a rounding detail:

  * UNTR at Rp 22,925/share costs Rp 2,292,500 for one lot. Against a Rp 2 juta
    slot that name simply cannot be held at target weight -- yet it ranked #4 in
    the screener's own top picks before this module existed.
  * BBCA at Rp 5,700 gives 3 lots per slot, so "equal weight" carries a +/-33%
    error that a rupiah-only rebalancer hides completely.

So the sizer chooses the number of positions rather than taking it as given: it
tries each N in range and keeps whichever deploys the most capital without letting
weights drift too far from target.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence


@dataclass
class Position:
    ticker: str
    price: float
    lots: int
    shares: int
    rupiah: float
    weight: float          # of deployed budget
    target_weight: float
    lot_price: float

    @property
    def weight_error(self) -> float:
        return self.weight - self.target_weight


@dataclass
class Allocation:
    positions: List[Position] = field(default_factory=list)
    cash_left: float = 0.0
    budget: float = 0.0
    capital: float = 0.0
    n_positions: int = 0
    deployed_pct: float = 0.0
    max_weight_error: float = 0.0
    rejected: Dict[str, str] = field(default_factory=dict)

    @property
    def invested(self) -> float:
        return sum(p.rupiah for p in self.positions)

    def tickers(self) -> List[str]:
        return [p.ticker for p in self.positions]


def lot_price(price: float, lot_size: int = 100) -> float:
    return float(price) * lot_size


def affordable_lots(budget: float, price: float, lot_size: int = 100) -> int:
    lp = lot_price(price, lot_size)
    return 0 if lp <= 0 else int(budget // lp)


def _allocate_for_n(
    candidates: Sequence[dict],
    budget: float,
    n: int,
    lot_size: int,
    max_lot_to_slot: float,
) -> Optional[Allocation]:
    """Allocate `budget` across the best `n` eligible candidates, whole lots only."""
    slot = budget / n
    chosen: List[dict] = []

    for cand in candidates:
        if len(chosen) == n:
            break
        price = float(cand.get("price") or 0.0)
        if price <= 0:
            continue
        # A name's single lot must fit inside its slot, otherwise the position is
        # forced overweight on day one with no way to trim (you cannot sell half a
        # lot). This is the UNTR/ITMG rejection.
        if lot_price(price, lot_size) > slot * max_lot_to_slot:
            continue
        chosen.append(cand)

    if len(chosen) < n:
        return None

    positions: List[Position] = []
    spent = 0.0
    for cand in chosen:
        price = float(cand["price"])
        lots = affordable_lots(slot, price, lot_size)
        if lots < 1:
            return None
        value = lots * lot_price(price, lot_size)
        spent += value
        positions.append(Position(
            ticker=cand["ticker"], price=price, lots=lots, shares=lots * lot_size,
            rupiah=value, weight=0.0, target_weight=1.0 / n,
            lot_price=lot_price(price, lot_size),
        ))

    if spent > budget:
        return None

    # Rounding down always leaves change. Spend it a lot at a time on whichever
    # position is furthest BELOW its target value -- handing it all to the
    # highest-ranked name instead would buy full deployment at the cost of a badly
    # lopsided book.
    leftover = budget - spent
    while True:
        affordable = [p for p in positions if p.lot_price <= leftover]
        if not affordable:
            break
        pos = min(affordable, key=lambda p: p.rupiah - slot)
        pos.lots += 1
        pos.shares += lot_size
        pos.rupiah += pos.lot_price
        leftover -= pos.lot_price
        spent += pos.lot_price

    for pos in positions:
        pos.weight = pos.rupiah / spent if spent else 0.0

    return Allocation(
        positions=positions,
        cash_left=leftover,
        budget=budget,
        n_positions=n,
        deployed_pct=spent / budget if budget else 0.0,
        max_weight_error=max(abs(p.weight_error) for p in positions),
    )


def choose_allocation(
    candidates: Sequence[dict],
    capital_rp: float,
    deploy_pct: float = 1.0,
    settings=None,
    min_positions: int = 3,
    max_positions: int = 6,
    lot_size: int = 100,
    max_lot_to_slot: float = 1.0,
    deviation_penalty: float = 0.5,
    min_position_rp: float = 1_000_000.0,
) -> Allocation:
    """
    Pick both the position count and the lot counts.

    `candidates` is a ranked sequence of dicts with `ticker` and `price` (best
    first). `deploy_pct` comes from the market regime, so a risk-off reading
    reduces how much is put to work rather than only what is bought.

    Each N is scored as `deployed_fraction - penalty * max_weight_error`, so a book
    that is fully invested but wildly lopsided loses to a slightly less invested,
    more balanced one.
    """
    if settings is not None:
        account = getattr(settings, "account", None) or {}
        min_positions = int(account.get("min_positions", min_positions))
        max_positions = int(account.get("max_positions", max_positions))
        max_lot_to_slot = float(account.get("max_lot_to_slot", max_lot_to_slot))
        deviation_penalty = float(account.get("deviation_penalty", deviation_penalty))
        min_position_rp = float(account.get("min_position_rp", min_position_rp))
        broker = getattr(settings, "broker", None) or {}
        lot_size = int(broker.get("lot_size", lot_size))

    budget = max(0.0, float(capital_rp) * float(deploy_pct))
    rejected: Dict[str, str] = {}

    if budget <= 0 or not candidates:
        return Allocation(budget=budget, capital=capital_rp, cash_left=budget, rejected=rejected)

    best: Optional[Allocation] = None
    best_score = float("-inf")

    upper = min(max_positions, len(candidates))
    for n in range(max(1, min_positions), max(1, upper) + 1):
        # Splitting a small budget into many slots is a quiet way to lose money:
        # the Rp10,000 stamp is 2% of a Rp500,000 position but 0.4% of a Rp2.5
        # juta one. Refuse to create positions too small to outrun their own costs.
        if min_position_rp > 0 and budget / n < min_position_rp:
            continue
        alloc = _allocate_for_n(candidates, budget, n, lot_size, max_lot_to_slot)
        if alloc is None:
            continue
        score = alloc.deployed_pct - deviation_penalty * alloc.max_weight_error
        if score > best_score:
            best_score, best = score, alloc

    if best is None:
        # Nothing fits at any N -- fall back to whatever single name we can afford.
        for cand in candidates:
            price = float(cand.get("price") or 0.0)
            lots = affordable_lots(budget, price, lot_size)
            if lots >= 1:
                value = lots * lot_price(price, lot_size)
                pos = Position(cand["ticker"], price, lots, lots * lot_size, value,
                               1.0, 1.0, lot_price(price, lot_size))
                best = Allocation([pos], budget - value, budget, capital_rp, 1,
                                  value / budget, 0.0, rejected)
                break
        else:
            return Allocation(budget=budget, capital=capital_rp, cash_left=budget, rejected=rejected)

    # Record why the affordable-but-unholdable names were left out, so the brief can
    # explain the omission instead of the user wondering where their top pick went.
    slot = budget / max(1, best.n_positions)
    picked = set(best.tickers())
    for cand in candidates:
        ticker = cand["ticker"]
        if ticker in picked:
            continue
        price = float(cand.get("price") or 0.0)
        if price <= 0:
            rejected[ticker] = "no price"
        elif lot_price(price, lot_size) > slot * max_lot_to_slot:
            rejected[ticker] = (
                f"1 lot = Rp{lot_price(price, lot_size):,.0f}, more than a Rp{slot:,.0f} slot"
            )

    best.capital = capital_rp
    best.rejected = rejected
    return best
