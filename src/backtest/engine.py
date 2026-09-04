# src/backtest/engine.py
"""
Historical simulation of the price-factor strategy.

**What this can and cannot answer.** Only 3.0 of the 9.0 total factor weight is
reconstructible from history: momentum (1/6/12-month) and realised volatility. The
six fundamental factors come from yfinance as a *current snapshot*, so ranking 2023
stocks by today's P/E would be look-ahead. The strategy simulated here is therefore
a third of the live score, and a good result means "the price component was not
obviously broken on one flattered window" -- not that the tool works.

**It reuses the live code path.** Sizing, fees, the regime ladder, the sector cap
and the liquidity gate are the same functions that produce the daily ticket. A
backtest that re-implemented position sizing would be measuring a strategy nobody
runs, and would hide bugs in the one they do.

**The look-ahead guard is asserted, not assumed.** At rebalance date T the signal
sees only `panel.index < T`, checked on every iteration. Names that had not yet
listed are simply absent -- prices are never back-filled before a listing date, so
GOTO cannot be picked in 2021.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from analysis.selection import sector_capped_pick
from market.regime import above_sma
from portfolio.fees import FeeConfig, estimate_fees
from portfolio.sizing import choose_allocation

# Sessions. Momentum skips the most recent month to avoid short-term reversal.
_SKIP = 21
_M6 = 126
_M12 = 252
_VOL = 60

# Only these carry weight here. The rest of the composite is not reconstructible.
BACKTESTABLE_FACTORS = ("mom_1m", "mom_6m", "mom_12m", "realized_vol")

_PERIODS_PER_YEAR = {"M": 12, "W": 52}
_RESAMPLE_RULE = {"M": "ME", "W": "W"}


@dataclass
class BacktestConfig:
    rebalance: str = "M"
    charge_fees: bool = True
    whole_lots: bool = True
    use_regime: bool = True
    weight_scale: float = 1.0        # robustness: scale momentum weights
    min_positions: int = 3
    max_positions: int = 6
    min_position_rp: float = 1_000_000.0
    max_per_sector: int = 2
    min_names: int = 10              # skip dates with too few listed names
    start: Optional[pd.Timestamp] = None
    end: Optional[pd.Timestamp] = None
    # Annualised percent, subtracted before every Sharpe this config produces.
    risk_free_pct: float = 0.0

    @property
    def periods_per_year(self) -> int:
        return _PERIODS_PER_YEAR.get(self.rebalance, 12)


@dataclass
class BacktestResult:
    equity: pd.Series = field(default_factory=lambda: pd.Series(dtype=float))
    returns: pd.Series = field(default_factory=lambda: pd.Series(dtype=float))
    turnover: pd.Series = field(default_factory=lambda: pd.Series(dtype=float))
    fees_paid: float = 0.0
    stamp_paid: float = 0.0
    n_rebalances: int = 0
    n_sell_days: int = 0
    avg_names_available: float = 0.0
    # Share of the intended budget that whole-lot rounding left in cash. Unlike the
    # path effect of rounding (whose sign is luck), this drag is always >= 0.
    avg_undeployed_pct: float = 0.0
    holdings_log: List[dict] = field(default_factory=list)
    config: Optional[BacktestConfig] = None

    def metrics(self) -> Dict[str, Optional[float]]:
        return summarize(self.equity, self.returns, self.turnover,
                         self.config.periods_per_year if self.config else 12,
                         self.config.risk_free_pct if self.config else 0.0)


# --------------------------------------------------------------------- plumbing
def build_price_panel(price_data: Dict[str, pd.DataFrame], field_name: str = "Close") -> pd.DataFrame:
    """Wide dates x tickers frame. No forward-fill: a gap means not listed / not traded."""
    series = {}
    for ticker, frame in (price_data or {}).items():
        if frame is None or field_name not in frame:
            continue
        col = frame[field_name].dropna()
        if col.empty:
            continue
        col.index = pd.to_datetime(col.index).tz_localize(None)
        series[ticker] = col[~col.index.duplicated(keep="last")]
    if not series:
        return pd.DataFrame()
    return pd.DataFrame(series).sort_index()


def rebalance_dates(panel: pd.DataFrame, rule: str = "M") -> List[pd.Timestamp]:
    """Last trading day of each period actually present in the panel."""
    if panel is None or panel.empty:
        return []
    marks = pd.Series(panel.index, index=panel.index)
    resampled = marks.resample(_RESAMPLE_RULE.get(rule, "ME")).last().dropna()
    return [pd.Timestamp(d) for d in resampled]


def price_signal(hist: pd.DataFrame, weight_scale: float = 1.0) -> pd.Series:
    """
    Cross-sectional score from momentum and realised volatility.

    `hist` must already be truncated to strictly before the rebalance date -- this
    function does not know the date and cannot enforce that itself, which is why
    the caller asserts it.

    A name without enough history scores NaN and is excluded. That is what keeps a
    2023 listing out of a 2022 rebalance.
    """
    if hist is None or len(hist) < _M12 + _SKIP + 1:
        return pd.Series(dtype=float)

    close = hist
    with np.errstate(divide="ignore", invalid="ignore"):
        mom_1m = close.iloc[-1] / close.iloc[-1 - _SKIP] - 1
        mom_6m = close.iloc[-1 - _SKIP] / close.iloc[-1 - _M6] - 1
        mom_12m = close.iloc[-1 - _SKIP] / close.iloc[-1 - _M12] - 1
        vol = close.pct_change().iloc[-_VOL:].std() * np.sqrt(252)

    # A name must have a real, finite observation at every horizon. Anything
    # partially listed over the lookback is dropped rather than half-scored.
    factors = pd.DataFrame({
        "mom_1m": mom_1m, "mom_6m": mom_6m, "mom_12m": mom_12m, "realized_vol": vol,
    }).replace([np.inf, -np.inf], np.nan)

    # Require actual price history across the full 12-month window, not just two
    # non-null endpoints, so a name that IPO'd mid-window cannot slip through.
    listed_long_enough = close.iloc[-1 - _M12:].notna().all()
    factors = factors[listed_long_enough].dropna()
    if len(factors) < 2:
        return pd.Series(dtype=float)

    weights = {
        "mom_1m": 0.5 * weight_scale,
        "mom_6m": 1.0 * weight_scale,
        "mom_12m": 1.0 * weight_scale,
        "realized_vol": -0.5,
    }

    score = pd.Series(0.0, index=factors.index)
    for col, w in weights.items():
        s = factors[col]
        sd = s.std()
        if not np.isfinite(sd) or sd == 0:
            continue
        score = score + w * ((s - s.mean()) / sd)
    return score.sort_values(ascending=False)


def _deploy_pct(bench_hist: Optional[pd.Series], fx_hist: Optional[pd.Series],
                trend_ma: int, ladder: Sequence[float]) -> float:
    """Regime ladder from history strictly before the rebalance date."""
    signals = [above_sma(bench_hist, trend_ma)]
    fx_above = above_sma(fx_hist, trend_ma)
    signals.append(None if fx_above is None else (not fx_above))

    usable = [s for s in signals if s is not None]
    if not usable:
        return float(ladder[len(ladder) // 2])
    on = sum(1 for s in usable if s)
    idx = round(on / len(usable) * (len(ladder) - 1))
    return float(ladder[int(idx)])


# ------------------------------------------------------------------ the run loop
def run_backtest(
    panel: pd.DataFrame,
    capital_rp: float,
    cfg: BacktestConfig,
    fee_cfg: FeeConfig,
    sectors: Optional[Dict[str, str]] = None,
    benchmark: Optional[pd.Series] = None,
    fx: Optional[pd.Series] = None,
    trend_ma: int = 200,
    deploy_ladder: Sequence[float] = (0.30, 0.60, 1.00),
) -> BacktestResult:
    result = BacktestResult(config=cfg)
    if panel is None or panel.empty:
        return result

    dates = rebalance_dates(panel, cfg.rebalance)
    if cfg.start is not None:
        dates = [d for d in dates if d >= cfg.start]
    if cfg.end is not None:
        dates = [d for d in dates if d <= cfg.end]
    if len(dates) < 2:
        return result

    cash = float(capital_rp)
    holdings: Dict[str, int] = {}
    equity_points, turnover_points, names_seen, undeployed = [], [], [], []
    total_fees = total_stamp = 0.0
    sell_days = 0

    for t, t_next in zip(dates[:-1], dates[1:]):
        hist = panel[panel.index < t]
        # The guard that makes every number below trustworthy.
        assert hist.empty or hist.index.max() < t, f"look-ahead at {t}"

        prices_t = panel.loc[t].dropna()
        value = cash + sum(sh * prices_t.get(tk, 0.0) for tk, sh in holdings.items())

        score = price_signal(hist, cfg.weight_scale)
        eligible = [tk for tk in score.index if tk in prices_t.index and prices_t[tk] > 0]
        names_seen.append(len(eligible))

        if len(eligible) >= cfg.min_names:
            if cfg.max_per_sector and sectors:
                eligible = sector_capped_pick(
                    eligible, sectors, top_n=len(eligible), max_per_sector=cfg.max_per_sector
                )[: cfg.max_positions * 3]

            deploy = 1.0
            if cfg.use_regime:
                bench_hist = benchmark[benchmark.index < t] if benchmark is not None else None
                fx_hist = fx[fx.index < t] if fx is not None else None
                deploy = _deploy_pct(bench_hist, fx_hist, trend_ma, deploy_ladder)

            candidates = [{"ticker": tk, "price": float(prices_t[tk])} for tk in eligible]
            target = _target_shares(
                candidates, value, deploy, cfg, fee_cfg,
            )

            intended = value * deploy
            placed = sum(sh * float(prices_t.get(tk, 0.0)) for tk, sh in target.items())
            if intended > 0:
                undeployed.append(max(0.0, 1 - placed / intended))

            orders = _orders(holdings, target, prices_t)
            fees = estimate_fees(orders, fee_cfg, value, sell_days=1) if cfg.charge_fees else None

            proceeds = sum(o["rupiah"] for o in orders if o["action"] == "SELL")
            spend = sum(o["rupiah"] for o in orders if o["action"] == "BUY")
            cost = fees.total if fees else 0.0

            traded = proceeds + spend
            turnover_points.append(traded / value if value else 0.0)

            cash = cash + proceeds - spend - cost
            holdings = {tk: sh for tk, sh in target.items() if sh > 0}
            total_fees += cost
            total_stamp += fees.stamp_duty if fees else 0.0
            if any(o["action"] == "SELL" for o in orders):
                sell_days += 1
            result.n_rebalances += 1
        else:
            turnover_points.append(0.0)

        prices_next = panel.loc[t_next]
        # Carry the last known price for a name that stops trading, rather than
        # marking it to zero -- a suspension is not a wipeout.
        end_value = cash
        for tk, sh in holdings.items():
            px = prices_next.get(tk)
            if px is None or pd.isna(px):
                hist_px = panel[tk].loc[:t_next].dropna()
                px = float(hist_px.iloc[-1]) if len(hist_px) else 0.0
            end_value += sh * float(px)

        equity_points.append((t_next, end_value))
        result.holdings_log.append({"date": t, "n_holdings": len(holdings),
                                    "names": ",".join(sorted(holdings))})

    equity = pd.Series(dict(equity_points)).sort_index()
    result.equity = equity
    result.returns = equity.pct_change().dropna()
    result.turnover = pd.Series(turnover_points[: len(equity)], index=equity.index[: len(turnover_points)])
    result.fees_paid = total_fees
    result.stamp_paid = total_stamp
    result.n_sell_days = sell_days
    result.avg_names_available = float(np.mean(names_seen)) if names_seen else 0.0
    result.avg_undeployed_pct = float(np.mean(undeployed) * 100) if undeployed else 0.0
    return result


def _target_shares(candidates, value, deploy, cfg, fee_cfg) -> Dict[str, float]:
    """
    Target holdings, with or without whole-lot rounding.

    `choose_allocation` runs in BOTH branches on purpose. The frictionless variant
    must hold the same names at the same intended weights and differ only in the
    rounding, otherwise the cost decomposition is not measuring friction at all.

    An earlier version built the frictionless leg as "top 6, equal weight", which
    is a different portfolio: it reported that adding lot rounding *improved*
    returns by 134 percentage points, because the comparison was really
    concentration versus diversification, not exact versus rounded.
    """
    alloc = choose_allocation(
        candidates, value, deploy,
        min_positions=cfg.min_positions, max_positions=cfg.max_positions,
        lot_size=fee_cfg.lot_size, min_position_rp=cfg.min_position_rp,
    )
    if not alloc.positions:
        return {}

    if cfg.whole_lots:
        return {p.ticker: float(p.shares) for p in alloc.positions}

    budget = value * deploy
    return {p.ticker: (budget * p.target_weight) / p.price for p in alloc.positions}


def _orders(holdings: Dict[str, int], target: Dict[str, float],
            prices: pd.Series) -> List[dict]:
    orders = []
    for tk, sh in holdings.items():
        want = target.get(tk, 0)
        if want < sh:
            px = float(prices.get(tk, 0.0))
            orders.append({"action": "SELL", "ticker": tk, "rupiah": (sh - want) * px})
    for tk, want in target.items():
        have = holdings.get(tk, 0)
        if want > have:
            px = float(prices.get(tk, 0.0))
            orders.append({"action": "BUY", "ticker": tk, "rupiah": (want - have) * px})
    return [o for o in orders if o["rupiah"] > 0]


# ------------------------------------------------------------------- benchmarks
def buy_and_hold(series: pd.Series, dates: Sequence[pd.Timestamp], capital: float) -> pd.Series:
    """Growth of `capital` in a single series, sampled at the rebalance anchors."""
    if series is None or series.empty:
        return pd.Series(dtype=float)
    clean = series.dropna()
    clean.index = pd.to_datetime(clean.index).tz_localize(None)
    sampled = clean.reindex(pd.DatetimeIndex(dates), method="ffill").dropna()
    if sampled.empty:
        return pd.Series(dtype=float)
    return capital * sampled / sampled.iloc[0]


def equal_weight_universe(panel: pd.DataFrame, dates: Sequence[pd.Timestamp],
                          capital: float) -> pd.Series:
    """
    Notional equal-weight portfolio of every listed name, rebalanced on the same
    anchors, with no lots and no fees.

    This is the benchmark that separates "the ranking added something" from "IDX
    stocks went up". It is deliberately frictionless, so it should be compared
    against the strategy's GROSS curve, not its net one.
    """
    if panel is None or panel.empty:
        return pd.Series(dtype=float)

    anchors = [d for d in dates if d in panel.index]
    if len(anchors) < 2:
        return pd.Series(dtype=float)

    value = capital
    out = {}
    for t, t_next in zip(anchors[:-1], anchors[1:]):
        row_t, row_next = panel.loc[t], panel.loc[t_next]
        live = [c for c in panel.columns
                if pd.notna(row_t.get(c)) and pd.notna(row_next.get(c)) and row_t.get(c) > 0]
        if live:
            rets = [(row_next[c] / row_t[c]) - 1 for c in live]
            value *= (1 + float(np.mean(rets)))
        out[t_next] = value
    return pd.Series(out).sort_index()


# ---------------------------------------------------------------------- metrics
def max_drawdown(equity: pd.Series) -> float:
    if equity is None or equity.empty:
        return 0.0
    peak = equity.cummax()
    return float((equity / peak - 1).min() * 100)


def summarize(equity: pd.Series, returns: pd.Series, turnover: pd.Series,
              periods_per_year: int,
              risk_free_pct: float = 0.0) -> Dict[str, Optional[float]]:
    """
    Return, risk, and the ratio between them.

    `risk_free_pct` is subtracted before the Sharpe. Return divided by volatility
    with no risk-free term is not a Sharpe ratio, and in a market with a 5%+
    policy rate the difference is most of the number: 25.2/24.5 = 1.03 becomes
    (25.2-5.5)/24.5 = 0.80. It defaults to 0.0 so a caller that has no view gets
    the old arithmetic explicitly rather than by omission.
    """
    if equity is None or len(equity) < 2:
        return {}

    total = float(equity.iloc[-1] / equity.iloc[0] - 1) * 100
    years = len(equity) / periods_per_year
    cagr = (float(equity.iloc[-1] / equity.iloc[0]) ** (1 / years) - 1) * 100 if years > 0 else None

    vol = float(returns.std() * np.sqrt(periods_per_year) * 100) if len(returns) > 1 else None
    ann_ret = cagr
    excess = None if ann_ret is None else ann_ret - float(risk_free_pct or 0.0)
    sharpe = (excess / vol) if (vol and vol > 0 and excess is not None) else None

    def clean(v):
        return None if v is None or not np.isfinite(v) else round(float(v), 2)

    return {
        "total_return": clean(total),
        "cagr": clean(cagr),
        "ann_vol": clean(vol),
        "sharpe": clean(sharpe),
        "max_drawdown": clean(max_drawdown(equity)),
        "risk_free_pct": clean(float(risk_free_pct or 0.0)),
        "hit_rate": clean(float((returns > 0).mean() * 100)) if len(returns) else None,
        "avg_turnover": clean(float(turnover.mean() * 100)) if len(turnover) else None,
        "periods": len(equity),
        "years": round(years, 2),
    }
