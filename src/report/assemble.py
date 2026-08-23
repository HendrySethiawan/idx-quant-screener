# src/report/assemble.py
"""
Turn a scored universe into today's decision.

Order of operations matters and mirrors how the constraints actually bind:
  rank -> liquidity gate -> sector cap -> lot-aware sizing -> diff vs holdings -> fees

Liquidity is applied before sizing because there is no point sizing a position you
cannot exit, and the sector cap before sizing because diversification is a
portfolio rule rather than a per-name one.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

import pandas as pd

from analysis.selection import sector_capped_pick
from market.liquidity import LiquidityConfig, assess
from portfolio.fees import FeeConfig, estimate_fees
from portfolio.holdings import Holding
from portfolio.sizing import Allocation, choose_allocation, lot_price
from report.explain import data_quality_note, health_flags, reason_phrase


def _price_of(row) -> Optional[float]:
    price = row.get("last_close")
    return float(price) if price and not pd.isna(price) else None


def build_candidates(
    df: pd.DataFrame,
    settings,
    deploy_pct: float,
    exclude: Optional[set] = None,
) -> Tuple[List[dict], Dict[str, str], Dict[str, str]]:
    """
    Returns (candidates, rejected, capped).

    `rejected` holds hard gate failures the reader must see; `capped` holds names
    crowded out by the sector limit, which is normal and shown separately.

    The nominal slot is used for the liquidity test before the real position count
    is known -- an approximation, but the gate is an order-of-magnitude check, not
    a precise one.
    """
    exclude = exclude or set()
    liq_cfg = LiquidityConfig.from_settings(settings)
    account = getattr(settings, "account", None) or {}
    nominal_n = int(account.get("max_positions", 5))
    nominal_slot = settings.capital_rp * deploy_pct / max(1, nominal_n)

    candidates: List[dict] = []
    rejected: Dict[str, str] = {}

    for _, row in df.iterrows():
        ticker = row["ticker"]
        if ticker in exclude:
            continue

        price = _price_of(row)
        if price is None:
            rejected[ticker] = "no recent price"
            continue

        verdict = assess(ticker, row.get("median_daily_value_rp"), nominal_slot, liq_cfg)
        if not verdict.ok:
            rejected[ticker] = verdict.reason
            continue

        candidates.append({
            "ticker": ticker,
            "name": row.get("name", ""),
            "price": price,
            "lot_price": lot_price(price, settings.lot_size),
            "score": float(row.get("undervaluation_score", 0.0)),
            "sector": row.get("sector", "Unknown"),
            "reason": reason_phrase(row),
            "quality_note": data_quality_note(row),
            "liquidity_label": verdict.label,
            "median_daily_value_rp": verdict.median_daily_value_rp,
        })

    # Diversification cap, applied to the ranking before sizing sees it.
    #
    # Capped names are NOT folded into `rejected`. Being crowded out by the sector
    # cap is ordinary portfolio construction, not a warning, and listing all ~25 of
    # them would bury the handful of genuine gate failures the reader needs to see.
    # Only the highest-ranked casualties are worth surfacing.
    capped: Dict[str, str] = {}
    if settings.max_per_sector:
        keep = set(sector_capped_pick(
            [c["ticker"] for c in candidates],
            {c["ticker"]: c["sector"] for c in candidates},
            top_n=len(candidates),
            max_per_sector=settings.max_per_sector,
        )[: max(settings.top_picks_n, nominal_n)])
        for rank, c in enumerate(candidates):
            if c["ticker"] not in keep and rank < settings.top_picks_n:
                capped[c["ticker"]] = (
                    f"already hold {settings.max_per_sector} {c['sector']} names ranked above it"
                )
        candidates = [c for c in candidates if c["ticker"] in keep]

    return candidates, rejected, capped


def build_orders(
    allocation: Allocation,
    holdings: List[Holding],
    prices: Dict[str, float],
) -> List[dict]:
    """SELL what left the target, BUY what entered it, HOLD the rest."""
    target = {p.ticker: p for p in allocation.positions}
    held = {h.ticker: h for h in holdings if h.lots > 0}
    orders: List[dict] = []

    for ticker, holding in held.items():
        if ticker in target:
            continue
        price = prices.get(ticker)
        orders.append({
            "action": "SELL", "ticker": ticker, "lots": holding.lots,
            "shares": holding.shares, "price": price,
            "rupiah": (holding.shares * price) if price else 0.0,
            "note": "no longer in the target book",
        })

    for ticker, pos in target.items():
        if ticker in held:
            delta = pos.lots - held[ticker].lots
            if delta > 0:
                orders.append({
                    "action": "BUY", "ticker": ticker, "lots": delta,
                    "shares": delta * (pos.shares // max(1, pos.lots)),
                    "price": pos.price, "rupiah": delta * pos.lot_price,
                    "note": "topping up to target weight",
                })
            elif delta < 0:
                orders.append({
                    "action": "SELL", "ticker": ticker, "lots": -delta,
                    "shares": -delta * (pos.shares // max(1, pos.lots)),
                    "price": pos.price, "rupiah": -delta * pos.lot_price,
                    "note": "trimming to target weight",
                })
            else:
                orders.append({
                    "action": "HOLD", "ticker": ticker, "lots": pos.lots,
                    "shares": pos.shares, "price": pos.price, "rupiah": pos.rupiah,
                    "note": "already at target",
                })
        else:
            orders.append({
                "action": "BUY", "ticker": ticker, "lots": pos.lots,
                "shares": pos.shares, "price": pos.price, "rupiah": pos.rupiah,
                "note": f"target weight {pos.target_weight:.0%}",
            })

    return orders


def build_holdings_rows(
    holdings: List[Holding],
    prices: Dict[str, float],
    df: pd.DataFrame,
    top_n: int,
) -> List[dict]:
    ranks = {t: i + 1 for i, t in enumerate(df["ticker"].tolist())}
    by_ticker = df.set_index("ticker") if not df.empty else pd.DataFrame()

    rows = []
    for h in holdings:
        if h.lots <= 0:
            continue
        price = prices.get(h.ticker)
        row = by_ticker.loc[h.ticker] if h.ticker in by_ticker.index else pd.Series(dtype=object)
        rows.append({
            "ticker": h.ticker,
            "lots": h.lots,
            "value": h.market_value(price),
            "unrealized_pct": h.unrealized_pct(price),
            "flags": health_flags(row, ranks.get(h.ticker), top_n),
        })
    return rows


def attach_events(items: List[dict], events, blind, horizon_days: int) -> None:
    """
    Stamp each order/candidate with its event state, in place.

    Deliberately does NOT filter anything. With earnings dates for only a third of
    the universe, a blocking rule would fire only on the names we can see and
    quietly bias the book toward the ones we cannot.
    """
    from market.events import by_ticker, state_for

    grouped = by_ticker(events)
    for item in items:
        ticker = item.get("ticker")
        state, message = state_for(ticker, grouped.get(ticker, []), blind, horizon_days)
        item["event_state"] = state
        item["event_note"] = message


def assemble(settings, df: pd.DataFrame, regime, holdings: List[Holding],
             events=None, blind=None):
    """Everything the brief needs, in one pass."""
    prices = {
        r["ticker"]: float(r["last_close"])
        for _, r in df.iterrows()
        if r.get("last_close") and not pd.isna(r["last_close"])
    }

    candidates, rejected, capped = build_candidates(df, settings, regime.deploy_pct)
    allocation = choose_allocation(
        candidates, settings.capital_rp, regime.deploy_pct, settings=settings
    )
    rejected.update(allocation.rejected)

    orders = build_orders(allocation, holdings, prices)
    fee_cfg = FeeConfig.from_settings(settings)
    fees = estimate_fees(orders, fee_cfg, settings.capital_rp, sell_days=1)

    holdings_rows = build_holdings_rows(holdings, prices, df, settings.top_picks_n)

    if events is not None:
        horizon = int(getattr(settings, "event_horizon_days", 14))
        attach_events(orders, events, blind or set(), horizon)
        attach_events(candidates, events, blind or set(), horizon)

    return {
        "allocation": allocation,
        "orders": orders,
        "fees": fees,
        "candidates": candidates[: settings.top_picks_n],
        # The untruncated list the sizer actually chose from. The what-if grid has
        # to see the same names, or it would answer a different question than the
        # ticket did.
        "candidates_all": candidates,
        "rejected": rejected,
        "capped": capped,
        "holdings_rows": holdings_rows,
        "prices": prices,
    }
