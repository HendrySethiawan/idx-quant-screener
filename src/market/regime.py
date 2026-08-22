# src/market/regime.py
"""
Market regime -> how much capital to deploy.

Two signals, both inspectable:
  1. ^JKSE above its 200-day simple moving average.
  2. USD/IDR *below* its 200-day SMA (a weakening rupiah is a headwind for IDX).

Signals on (0, 1, 2) map to a deployment fraction via `deploy_ladder`, default
30% / 60% / 100%. The output feeds the position sizer directly, so a risk-off
reading reduces how much is put to work rather than only reshuffling what is
bought. That link is what makes this a decision input instead of a dashboard.

Deliberately simple: two signals a person can verify on a chart in ten seconds.
More signals would fit history better and mean less.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

import pandas as pd


@dataclass
class Signal:
    name: str
    ticker: str
    risk_on: Optional[bool]
    detail: str = ""


@dataclass
class Regime:
    signals: List[Signal] = field(default_factory=list)
    deploy_pct: float = 1.0
    label: str = "UNKNOWN"
    emoji: str = "⚪"
    headline: str = ""

    @property
    def on_count(self) -> int:
        return sum(1 for s in self.signals if s.risk_on is True)

    @property
    def total(self) -> int:
        return sum(1 for s in self.signals if s.risk_on is not None)


def above_sma(series: pd.Series, window: int) -> Optional[bool]:
    """True if the last close is at or above its own `window`-period SMA."""
    if series is None:
        return None
    clean = series.dropna()
    if len(clean) < window:
        return None
    return bool(clean.iloc[-1] >= clean.iloc[-window:].mean())


def assess_regime(
    benchmark: Optional[pd.Series],
    fx: Optional[pd.Series] = None,
    trend_ma: int = 200,
    deploy_ladder: Optional[List[float]] = None,
) -> Regime:
    ladder = deploy_ladder or [0.30, 0.60, 1.00]
    signals: List[Signal] = []

    jkse_on = above_sma(benchmark, trend_ma)
    signals.append(Signal(
        "IHSG trend", "^JKSE", jkse_on,
        "above its 200-day average" if jkse_on
        else ("below its 200-day average" if jkse_on is False else "not enough history"),
    ))

    # A rising USD/IDR means a weakening rupiah, which historically pressures IDX
    # equities via foreign outflow. Risk-on is therefore fx BELOW its trend.
    fx_above = above_sma(fx, trend_ma)
    fx_on = None if fx_above is None else (not fx_above)
    signals.append(Signal(
        "Rupiah", "IDR=X", fx_on,
        "stronger than its 200-day average" if fx_on
        else ("weakening past its 200-day average" if fx_on is False else "not enough history"),
    ))

    on = sum(1 for s in signals if s.risk_on is True)
    total = sum(1 for s in signals if s.risk_on is not None)

    if total == 0:
        return Regime(signals, 0.60, "UNKNOWN", "⚪",
                      "No market data available - defaulting to a cautious 60%.")

    # Ladder is indexed by signal count; rescale if a signal is unavailable.
    idx = round(on / total * (len(ladder) - 1))
    deploy = ladder[int(idx)]

    if on == total:
        label, emoji = "RISK-ON", "\U0001F7E2"
        headline = f"Both signals positive. Deploy up to {deploy:.0%} of capital."
    elif on == 0:
        label, emoji = "RISK-OFF", "\U0001F534"
        headline = f"Both signals negative. Hold back - deploy at most {deploy:.0%}."
    else:
        label, emoji = "MIXED", "\U0001F7E1"
        headline = f"{on} of {total} signals positive. Deploy about {deploy:.0%}."

    return Regime(signals, deploy, label, emoji, headline)
