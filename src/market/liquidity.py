# src/market/liquidity.py
"""
Can this position be sold again?

Rank tells you what to buy; liquidity tells you what you can get out of. The
universe contains names that fail badly on the second question -- WIKA showed a
median daily traded value of Rp 0, BBSI about Rp 4.2 juta/day, BNLI about
Rp 119 juta/day. A Rp 2.5 juta position in a stock that trades Rp 4 juta a day is
most of a session's volume.

The rule is relative to position size, not an absolute blacklist: a position may
not exceed `max_position_pct_of_daily_value` of median daily traded value.
Failing names are reported with their number rather than silently dropped, so the
brief can say *why* a highly ranked stock was skipped.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class LiquidityConfig:
    max_position_pct_of_daily_value: float = 0.01
    min_median_daily_value_rp: float = 250_000_000
    window: int = 20

    @classmethod
    def from_settings(cls, settings) -> "LiquidityConfig":
        cfg = getattr(settings, "liquidity", None) or {}
        return cls(
            max_position_pct_of_daily_value=float(cfg.get("max_position_pct_of_daily_value", 0.01)),
            min_median_daily_value_rp=float(cfg.get("min_median_daily_value_rp", 250_000_000)),
            window=int(cfg.get("window", 20)),
        )


@dataclass
class LiquidityVerdict:
    ticker: str
    median_daily_value_rp: Optional[float]
    position_rp: float
    ok: bool
    reason: str = ""

    @property
    def pct_of_daily_value(self) -> Optional[float]:
        if not self.median_daily_value_rp:
            return None
        return self.position_rp / self.median_daily_value_rp * 100.0

    @property
    def label(self) -> str:
        if not self.ok:
            return "illiquid"
        pct = self.pct_of_daily_value
        if pct is not None and pct > self.__class__._WARN_PCT:
            return "thin"
        return "ok"

    _WARN_PCT = 0.5


def assess(
    ticker: str,
    median_daily_value_rp: Optional[float],
    position_rp: float,
    cfg: LiquidityConfig,
) -> LiquidityVerdict:
    def fmt(v: float) -> str:
        return f"Rp{v:,.0f}"

    if median_daily_value_rp is None or median_daily_value_rp <= 0:
        return LiquidityVerdict(
            ticker, median_daily_value_rp, position_rp, False,
            "no trading volume in the last 20 sessions",
        )

    if median_daily_value_rp < cfg.min_median_daily_value_rp:
        return LiquidityVerdict(
            ticker, median_daily_value_rp, position_rp, False,
            f"trades only {fmt(median_daily_value_rp)}/day, below the "
            f"{fmt(cfg.min_median_daily_value_rp)} floor",
        )

    cap = median_daily_value_rp * cfg.max_position_pct_of_daily_value
    if position_rp > cap:
        return LiquidityVerdict(
            ticker, median_daily_value_rp, position_rp, False,
            f"a {fmt(position_rp)} position is more than "
            f"{cfg.max_position_pct_of_daily_value:.1%} of its {fmt(median_daily_value_rp)} daily volume",
        )

    return LiquidityVerdict(ticker, median_daily_value_rp, position_rp, True, "")
