# src/portfolio/fees.py
"""
Indopremier fee model.

0.19% on buys, 0.29% on sells, and Rp 10,000 bea meterai charged once per DAY that
contains at least one sell -- not per order. That per-day structure is the whole
reason this module exists: at 4 sells a month the difference between spreading
them across four days and batching them into one is Rp 30,000/month, which is
0.36%/yr on a Rp 10 juta account.

Every figure here is in rupiah. No percentages leak out of this module except
`pct_of_capital`, which exists purely for display.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence


@dataclass(frozen=True)
class FeeConfig:
    buy_fee: float = 0.0019
    sell_fee: float = 0.0029
    stamp_duty_rp: float = 10_000.0
    lot_size: int = 100

    @classmethod
    def from_settings(cls, settings) -> "FeeConfig":
        broker = getattr(settings, "broker", None) or {}
        return cls(
            buy_fee=float(broker.get("buy_fee", 0.0019)),
            sell_fee=float(broker.get("sell_fee", 0.0029)),
            stamp_duty_rp=float(broker.get("stamp_duty_rp", 10_000.0)),
            lot_size=int(broker.get("lot_size", 100)),
        )


@dataclass
class FeeBreakdown:
    buy_value: float = 0.0
    sell_value: float = 0.0
    buy_fee: float = 0.0
    sell_fee: float = 0.0
    stamp_duty: float = 0.0
    n_buys: int = 0
    n_sells: int = 0
    sell_days: int = 1
    stamp_if_spread: float = 0.0
    notes: List[str] = field(default_factory=list)

    @property
    def total(self) -> float:
        return self.buy_fee + self.sell_fee + self.stamp_duty

    @property
    def stamp_saving_if_batched(self) -> float:
        """Rupiah saved by executing every sell on one day instead of separate days."""
        return max(0.0, self.stamp_if_spread - self.stamp_duty)

    def pct_of(self, capital_rp: float) -> float:
        return (self.total / capital_rp * 100.0) if capital_rp else 0.0

    def as_dict(self, capital_rp: float = 0.0) -> Dict[str, float]:
        return {
            "buy_value": self.buy_value,
            "sell_value": self.sell_value,
            "buy_fee": self.buy_fee,
            "sell_fee": self.sell_fee,
            "stamp_duty": self.stamp_duty,
            "total": self.total,
            "pct_of_capital": self.pct_of(capital_rp),
            "n_buys": self.n_buys,
            "n_sells": self.n_sells,
            "stamp_saving_if_batched": self.stamp_saving_if_batched,
        }


def estimate_fees(
    orders: Iterable[dict],
    cfg: FeeConfig,
    capital_rp: float = 0.0,
    sell_days: int = 1,
) -> FeeBreakdown:
    """
    Cost a set of orders.

    `orders` are dicts with at least `action` ("BUY"/"SELL") and `rupiah`.
    `sell_days` is how many distinct days the sells are spread over; 1 means the
    user follows the batching advice.
    """
    out = FeeBreakdown()

    for order in orders:
        action = str(order.get("action", "")).upper()
        value = float(order.get("rupiah", 0.0) or 0.0)
        if value <= 0:
            continue
        if action == "BUY":
            out.buy_value += value
            out.buy_fee += value * cfg.buy_fee
            out.n_buys += 1
        elif action == "SELL":
            out.sell_value += value
            out.sell_fee += value * cfg.sell_fee
            out.n_sells += 1

    if out.n_sells:
        days = max(1, min(int(sell_days), out.n_sells))
        out.sell_days = days
        out.stamp_duty = cfg.stamp_duty_rp * days
        out.stamp_if_spread = cfg.stamp_duty_rp * out.n_sells
        if out.n_sells > 1 and days == 1:
            saving = out.stamp_if_spread - out.stamp_duty
            out.notes.append(
                f"Execute all {out.n_sells} sells on the SAME DAY - saves Rp{saving:,.0f} in stamp duty."
            )
    else:
        out.sell_days = 0

    return out


def round_trip_cost(value_rp: float, cfg: FeeConfig) -> float:
    """
    Cost of buying and later selling one position, stamp included.

    Used as the materiality floor: a rebalance that gains less than this is noise.
    """
    return value_rp * (cfg.buy_fee + cfg.sell_fee) + cfg.stamp_duty_rp


def breakeven_move_pct(value_rp: float, cfg: FeeConfig) -> float:
    """How far a position must rise just to cover its own round-trip cost."""
    if value_rp <= 0:
        return 0.0
    return round_trip_cost(value_rp, cfg) / value_rp * 100.0
