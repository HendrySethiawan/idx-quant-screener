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


def _num(v) -> Optional[float]:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _value_fields(row) -> Dict[str, object]:
    """
    Carry the peer-multiple verdict through to the brief.

    Defaults to the UNKNOWN state rather than an empty dict, so a frame produced
    before valuation existed (or with `valuation.enabled: false`) still renders --
    it just renders as "cannot value", which is the truth in that case.
    """
    return {
        "value_verdict": str(row.get("value_verdict") or "unknown"),
        "value_zone_lo": _num(row.get("value_zone_lo")),
        "value_zone_hi": _num(row.get("value_zone_hi")),
        "value_gap_pct": _num(row.get("value_gap_pct")),
        "value_peer_group": str(row.get("value_peer_group") or "universe"),
        "value_note": str(row.get("value_note") or ""),
        "roe": _num(row.get("roe")),
    }


def build_candidates(
    df: pd.DataFrame,
    settings,
    deploy_pct: float,
    exclude: Optional[set] = None,
    trail=None,
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
            **_value_fields(row),
        })

    if trail is not None:
        liq_floor = f"Rp{liq_cfg.min_median_daily_value_rp:,.0f}"
        trail.record(
            "liquidity", "Can you get back out?",
            f"A name must trade at least {liq_floor} a day, and your position must "
            f"stay under {liq_cfg.max_position_pct_of_daily_value:.0%} of that. A "
            f"stock you cannot sell is not a position.",
            setting="liquidity block, configs/default.yaml",
            kept=[c["ticker"] for c in candidates], dropped=rejected,
            n_in=len(df) - len(exclude),
        )

    # Diversification cap, then the shortlist. These used to be one step, which
    # made them indistinguishable: a name crowded out by its sector and a name that
    # simply ranked 12th both just disappeared. They are different facts and the
    # reader deserves to know which one applies, so they are now two stages.
    #
    # Capped names are NOT folded into `rejected`. Being crowded out by the sector
    # cap is ordinary portfolio construction, not a warning, and mixing it in would
    # bury the handful of genuine gate failures.
    shortlist_n = max(settings.top_picks_n, nominal_n)
    ranked = [c["ticker"] for c in candidates]
    capped: Dict[str, str] = {}

    if settings.max_per_sector:
        # top_n is the shortlist size, not the full list. Asking for the full list
        # let the backfill hand every capped name straight back, which is why
        # `capped` was always empty and 37 names vanished unexplained. The selected
        # set is identical either way -- only the reporting changes.
        keep_list = sector_capped_pick(
            ranked,
            {c["ticker"]: c["sector"] for c in candidates},
            top_n=shortlist_n,
            max_per_sector=settings.max_per_sector,
            skipped=capped,
        )
    else:
        keep_list = ranked[:shortlist_n]

    keep = set(keep_list)
    outranked = {
        t: f"ranked #{i + 1}; only the top {shortlist_n} go through"
        for i, t in enumerate(ranked)
        if t not in keep and t not in capped
    }

    if trail is not None:
        trail.record(
            "sector_cap", "Not too much of one sector",
            f"At most {settings.max_per_sector} names per sector, so a single bad "
            f"sector cannot take the whole book down.",
            setting=f"max_per_sector: {settings.max_per_sector}, configs/default.yaml",
            kept=[t for t in ranked if t not in capped], dropped=capped,
        )
        trail.record(
            "shortlist", "Keep the best few",
            f"Only the top {shortlist_n} by rank score go on to sizing. Everything "
            f"here passed every gate -- it was simply out-ranked.",
            setting=f"top_picks_n: {settings.top_picks_n}, configs/default.yaml",
            kept=keep_list, dropped=outranked,
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
            # Something you already own drifting above its peer range is the case
            # the ticket cannot raise on its own: the sizer only proposes selling
            # when a name leaves the target book, not when it gets expensive.
            **_value_fields(row),
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

    # The decision trail. Every stage below is recorded from what the gate actually
    # returned -- never re-derived here -- so the explanation cannot drift away from
    # the decision it is explaining. See src/analysis/trace.py.
    from analysis.trace import DecisionTrail

    trail = DecisionTrail()
    fetched = df["ticker"].tolist()
    # The union, not just the configured list. In production `df` is built by
    # iterating stock_tickers so it is a subset, but the funnel reconciling is this
    # feature's one hard guarantee and it must not rest on that holding -- a name
    # present in the frame did enter the pipeline, however it got there.
    configured = list(getattr(settings, "stock_tickers", None) or [])
    universe = list(dict.fromkeys(configured + fetched))

    trail.record(
        "universe", "The universe",
        "The IDX names this tool watches. Nothing outside this list can ever be "
        "suggested, however good it is.",
        setting="stock_tickers, configs/default.yaml",
        kept=universe, n_in=len(universe),
    )
    trail.record(
        "data", "Prices and fundamentals",
        "Two years of daily prices and a fundamentals snapshot from Yahoo Finance. "
        "A name Yahoo returns nothing for cannot be ranked.",
        setting="cache_ttl_minutes, configs/default.yaml",
        kept=fetched,
        dropped={t: "no data returned by Yahoo Finance"
                 for t in universe if t not in set(fetched)},
    )

    gaps = 0
    if "imputed_factors" in df.columns:
        gaps = int((df["imputed_factors"].fillna("").astype(str).str.len() > 0).sum())
    trail.record(
        "score", "Scored on ten factors",
        "Each factor becomes a z-score against the rest of the universe, is "
        "multiplied by its weight and summed. A missing factor scores neutral, so "
        "a data gap never removes a stock -- it just gives it less to stand on.",
        setting="factor_weights, configs/default.yaml",
        kept=fetched, dropped={},
        note=(f"{gaps} of {len(fetched)} names were missing at least one factor and "
              f"scored neutral on it." if gaps else ""),
    )

    candidates, rejected, capped = build_candidates(
        df, settings, regime.deploy_pct, trail=trail
    )
    allocation = choose_allocation(
        candidates, settings.capital_rp, regime.deploy_pct, settings=settings
    )

    budget = settings.capital_rp * regime.deploy_pct
    slot = budget / max(1, allocation.n_positions or 1)

    # `allocation.rejected` only holds names the sizer actively refused (a lot that
    # costs more than a slot). Names that simply ran out of slots are not in it, and
    # leaving them out would make the funnel fail to add up -- 8 in, 3 out, 1
    # dropped. They need their own reason, because "we stopped at 3" is a different
    # fact from "you could not afford it".
    sized = set(allocation.tickers())
    sizing_dropped = dict(allocation.rejected)
    for c in candidates:
        if c["ticker"] not in sized and c["ticker"] not in sizing_dropped:
            sizing_dropped[c["ticker"]] = (
                f"{allocation.n_positions} positions was the best fit for the budget; "
                f"this ranked below them"
            )

    trail.record(
        "sizing", "What fits in whole lots",
        f"IDX trades in {settings.lot_size}-share lots, so a name whose single lot "
        f"costs more than its slot cannot be bought at all. Budget today is "
        f"{regime.deploy_pct:.0%} of capital.",
        setting="min_positions / max_positions / min_position_rp, configs/default.yaml",
        kept=allocation.tickers(), dropped=sizing_dropped,
        note=f"Roughly Rp{slot:,.0f} per slot at {allocation.n_positions} positions.",
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
        "trail": trail,
    }
