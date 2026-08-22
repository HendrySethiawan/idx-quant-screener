# src/portfolio/holdings.py
"""
Current holdings as lots + average cost.

Storing only a ticker list (as the peer's build does) makes unrealised P&L
impossible to compute, so the file carries lots and average price. A plain
`tickers: [...]` list is still accepted and read as "held, cost unknown", so an
existing file keeps working.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

import yaml


@dataclass
class Holding:
    ticker: str
    lots: int = 0
    avg_price: Optional[float] = None
    lot_size: int = 100

    @property
    def shares(self) -> int:
        return self.lots * self.lot_size

    @property
    def cost_basis(self) -> Optional[float]:
        return self.shares * self.avg_price if self.avg_price else None

    def market_value(self, price: Optional[float]) -> Optional[float]:
        return self.shares * price if price else None

    def unrealized(self, price: Optional[float]) -> Optional[float]:
        mv, cb = self.market_value(price), self.cost_basis
        return (mv - cb) if (mv is not None and cb is not None) else None

    def unrealized_pct(self, price: Optional[float]) -> Optional[float]:
        cb = self.cost_basis
        pnl = self.unrealized(price)
        return (pnl / cb * 100.0) if (pnl is not None and cb) else None


def load_holdings(path: str | Path, lot_size: int = 100) -> List[Holding]:
    """
    Read current_holdings.yaml. Returns [] on a missing or malformed file --
    a broken holdings file should degrade the brief, not crash the run.

    Accepts either form:
        tickers: [BBRI.JK, BMRI.JK]
        holdings:
          BBRI.JK: {lots: 7, avg_price: 4100}
    """
    p = Path(path)
    if not p.exists():
        return []
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except Exception:
        return []

    out: List[Holding] = []
    seen: set[str] = set()

    detailed = data.get("holdings") or {}
    if isinstance(detailed, dict):
        for ticker, spec in detailed.items():
            key = str(ticker).strip().upper()
            if not key or key in seen:
                continue
            seen.add(key)
            if isinstance(spec, dict):
                out.append(Holding(
                    key,
                    lots=int(spec.get("lots", 0) or 0),
                    avg_price=float(spec["avg_price"]) if spec.get("avg_price") else None,
                    lot_size=lot_size,
                ))
            else:
                out.append(Holding(key, lots=int(spec or 0), lot_size=lot_size))

    for ticker in data.get("tickers") or []:
        key = str(ticker).strip().upper()
        if key and key not in seen:
            seen.add(key)
            out.append(Holding(key, lots=0, lot_size=lot_size))

    return out


def save_holdings(holdings: List[Holding], path: str | Path) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "holdings": {
            h.ticker: {"lots": h.lots, **({"avg_price": h.avg_price} if h.avg_price else {})}
            for h in holdings
        }
    }
    with open(p, "w", encoding="utf-8") as f:
        yaml.safe_dump(payload, f, sort_keys=False, allow_unicode=True)
    return p


def portfolio_value(holdings: List[Holding], prices: Dict[str, float]) -> float:
    return sum(h.market_value(prices.get(h.ticker)) or 0.0 for h in holdings)


def diff_to_target(
    holdings: List[Holding],
    target_tickers: List[str],
) -> Dict[str, List[str]]:
    """BUY / SELL / HOLD split against a target book."""
    current = {h.ticker for h in holdings if h.lots > 0}
    target = set(target_tickers)
    return {
        "buy": [t for t in target_tickers if t not in current],
        "sell": [h.ticker for h in holdings if h.lots > 0 and h.ticker not in target],
        "hold": [t for t in target_tickers if t in current],
    }
