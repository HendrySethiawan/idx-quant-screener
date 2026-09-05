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

from analysis.selection import (DEFAULT_MAX_CORRELATION, average_correlation,
                                decorrelated_pick, sector_capped_pick)
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
    correlations=None,
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

    # Then by behaviour, which a label cannot capture in either direction: BRPT and
    # PTRO are different sectors and correlate 0.87, while tin, palm oil and coal
    # sit around 0.30. Applied after the sector cap so the two explanations compose
    # rather than compete -- a name skipped here was already through the label gate.
    selection_cfg = getattr(settings, "selection", None) or {}
    max_corr = selection_cfg.get("max_correlation", DEFAULT_MAX_CORRELATION)
    if correlations is not None and max_corr:
        # Its own dict: `decorrelated_pick` clears what it is handed, and sharing
        # `capped` would erase every sector-cap explanation already recorded.
        correlated: Dict[str, str] = {}
        keep_list = decorrelated_pick(
            keep_list, correlations, top_n=shortlist_n,
            max_correlation=float(max_corr), skipped=correlated,
        )
        capped.update(correlated)

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
    exit_plans: Optional[Dict[str, object]] = None,
    cooling: Optional[Dict[str, int]] = None,
) -> List[dict]:
    """
    SELL what left the target, BUY what entered it, HOLD the rest -- and act on the
    exits first.

    **An exit outranks the ranking.** A stop that has been hit is a decision
    already made about a position you hold; the ranking is an opinion about one you
    might open. So a name with an EXIT or TRIM plan produces that row and is then
    skipped by the rebalance diff entirely -- otherwise the same ticker could
    appear twice, once being sold on the stop and once being held at target.

    **`cooling` blocks the re-buy.** Without it the loop closes on itself: the stop
    sells today, tomorrow's re-rank puts the name straight back in the target book,
    and you pay 0.29% out, Rp10,000 stamp and 0.19% back in to end up exactly where
    you started.
    """
    from portfolio.exits import EXIT, TRIM

    exit_plans = exit_plans or {}
    cooling = cooling or {}
    target = {p.ticker: p for p in allocation.positions}
    held = {h.ticker: h for h in holdings if h.lots > 0}
    orders: List[dict] = []
    decided: set = set()

    for ticker, plan in exit_plans.items():
        if getattr(plan, "action", None) not in (EXIT, TRIM):
            continue
        holding = held.get(ticker)
        if holding is None or holding.lots <= 0:
            continue
        lots = min(int(plan.action_lots), holding.lots)
        if lots <= 0:
            continue
        price = prices.get(ticker)
        lot_size = holding.lot_size or 100
        decided.add(ticker)
        orders.append({
            "action": "SELL", "ticker": ticker, "lots": lots,
            "shares": lots * lot_size, "price": price,
            "rupiah": (lots * lot_size * price) if price else 0.0,
            "note": plan.reason,
            "exit_kind": plan.action,
            "stop_rp": plan.stop_rp,
            "stop_kind": plan.stop_kind,
        })

    for ticker, holding in held.items():
        if ticker in target or ticker in decided:
            continue
        price = prices.get(ticker)
        orders.append({
            "action": "SELL", "ticker": ticker, "lots": holding.lots,
            "shares": holding.shares, "price": price,
            "rupiah": (holding.shares * price) if price else 0.0,
            "note": "no longer in the target book",
        })

    for ticker, pos in target.items():
        if ticker in decided:
            continue
        # The cooldown blocks any INCREASE, not just a re-entry. Blocking only the
        # names you no longer hold left the ladder undoing itself: trim 4 lots at
        # +1R on Monday, and the next re-rank tops the position straight back up to
        # target, paying the sell fee, the stamp and the buy fee for the privilege
        # of being exactly where it started.
        if ticker in cooling:
            wait = cooling[ticker]
            sessions = f"{wait} more session{'s' if wait > 1 else ''}"
            holding = held.get(ticker)
            if holding is not None and holding.lots > 0:
                orders.append({
                    "action": "HOLD", "ticker": ticker, "lots": holding.lots,
                    "shares": holding.shares, "price": pos.price,
                    "rupiah": holding.shares * (pos.price or 0.0),
                    "note": (f"sold some recently — not topping this back up for "
                             f"{sessions}. Rebuying what you just trimmed pays both "
                             f"sides of the spread to undo your own decision."),
                    "cooldown_left": wait,
                })
            else:
                orders.append({
                    "action": "WAIT", "ticker": ticker, "lots": 0, "shares": 0,
                    "price": pos.price, "rupiah": 0.0,
                    "note": (f"sold recently — blocked for {sessions}. Buying back "
                             f"now pays the sell fee, the stamp and the buy fee to "
                             f"end up where you already were."),
                    "cooldown_left": wait,
                })
            continue
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

    # HOLD carries the plan it is already under, so a position at target is not a
    # blank row: it still has a stop, and how far away that stop is IS the answer
    # to "should I keep this".
    for order in orders:
        plan = exit_plans.get(order["ticker"])
        if order["action"] == "HOLD" and plan is not None and plan.stop_rp:
            order["stop_rp"] = plan.stop_rp
            order["stop_kind"] = plan.stop_kind
            order["note"] = plan.reason

    return orders


def attach_entry_risk(orders: List[dict], df: pd.DataFrame, cfg, fee_cfg,
                      capital_rp: float) -> None:
    """
    Give every BUY the stop it would be bought under, and what that costs to be
    wrong about, in place.

    This is the half of the request about going IN. A proposal is not "Rp1.3 juta
    of INET"; it is "Rp220,000 at risk, 2.2% of everything you have" -- and those
    are different decisions even though the rupiah deployed is the same.

    Reported, never enforced. `choose_allocation` deploys the budget in whole lots
    and is the code the backtest validates; second-guessing its lot counts here
    would mean the ticket and the simulation stopped describing the same strategy.
    """
    from portfolio.exits import entry_risk

    if df is None or getattr(df, "empty", True) or "atr_14" not in df.columns:
        return
    atr = {r["ticker"]: _num(r.get("atr_14")) for _, r in df.iterrows()}

    for order in orders:
        if order["action"] != "BUY":
            continue
        price = _num(order.get("price"))
        shares = int(order.get("shares") or 0)
        if not price or shares <= 0:
            continue
        # Flat keys, not a nested dict: `render` writes these rows straight to
        # ticket.csv, and a dict in a cell becomes the string "{'stop_rp': ...}".
        # A stop and a rupiah-at-risk are worth having in that file anyway.
        risk = entry_risk(price, atr.get(order["ticker"]), shares,
                          cfg, fee_cfg, capital_rp)
        order.update({
            "stop_rp": risk["stop_rp"],
            "risk_rp": risk["risk_rp"],
            "risk_pct": risk["risk_pct"],
            "risk_over": risk["over"],
            "risk_capped": risk["capped"],
        })


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


def attach_events(items: List[dict], events, blind, horizon_days: int,
                  today=None) -> None:
    """
    Stamp each order/candidate with its event state, in place.

    Deliberately does NOT filter anything. With earnings dates for only a third of
    the universe, a blocking rule would fire only on the names we can see and
    quietly bias the book toward the ones we cannot.

    `today` is injectable, as it already is on `upcoming` and `state_for`. Without
    it a test could freeze the event dates but not the clock they are measured
    against, so the same assertion passed in August and failed in September -- a
    test that depends on the day it runs is worse than no test, because it trains
    you to ignore a red suite.
    """
    from market.events import by_ticker, state_for

    grouped = by_ticker(events)
    for item in items:
        ticker = item.get("ticker")
        state, message = state_for(ticker, grouped.get(ticker, []), blind,
                                   horizon_days, today=today)
        item["event_state"] = state
        item["event_note"] = message


def build_exit_plans(settings, df: pd.DataFrame, prices: Dict[str, float],
                     risk_panel: Optional[Dict[str, pd.DataFrame]] = None,
                     journal: Optional[pd.DataFrame] = None,
                     today=None):
    """
    An exit plan per open position, plus the names too recently sold to re-buy.

    The FIFO ledger is the source, not `current_holdings.yaml`: `open_positions`
    gives a fee-inclusive average cost, which is the price the position actually
    has to beat, and `position_history` gives the size it started at so the ladder
    keeps still when it is trimmed.

    Returns `(plans, cooling)`. Both are empty when there is nothing open, which
    is the ordinary case for a fresh install and must not be an error.
    """
    from portfolio.exits import ExitConfig, cooldown, plans_for, position_history
    from portfolio.fees import FeeConfig
    from portfolio.ledger import open_positions

    cfg = ExitConfig.from_settings(settings)
    fee_cfg = FeeConfig.from_settings(settings)

    if journal is None or getattr(journal, "empty", True):
        return {}, {}, cfg

    panel = risk_panel or {}
    closes = panel.get("Close")
    atr, notes = {}, {}
    if df is not None and not getattr(df, "empty", True):
        for _, row in df.iterrows():
            ticker = row["ticker"]
            atr[ticker] = _num(row.get("atr_14"))
            notes[ticker] = str(row.get("price_note") or "")

    plans = plans_for(
        open_positions(journal, prices), closes, cfg, fee_cfg,
        highs=panel.get("High"),
        history=position_history(journal, fee_cfg.lot_size),
        atr=atr, price_notes=notes, capital_rp=settings.capital_rp,
    )
    sessions = None if closes is None else pd.DatetimeIndex(closes.index)
    return plans, cooldown(journal, cfg, today=today, sessions=sessions), cfg


def assemble(settings, df: pd.DataFrame, regime, holdings: List[Holding],
             correlations=None,
             events=None, blind=None,
             risk_panel=None, journal=None, today=None):
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
        df, settings, regime.deploy_pct, trail=trail, correlations=correlations
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

    # Exits before orders. A stop that has been hit is a decision already made
    # about a position you hold; the ranking above is an opinion about one you
    # might open, and the first of those outranks the second.
    exit_plans, cooling, exit_cfg = build_exit_plans(
        settings, df, prices, risk_panel=risk_panel, journal=journal, today=today)

    fee_cfg = FeeConfig.from_settings(settings)
    orders = build_orders(allocation, holdings, prices, exit_plans, cooling)
    attach_entry_risk(orders, df, exit_cfg, fee_cfg, settings.capital_rp)
    fees = estimate_fees(orders, fee_cfg, settings.capital_rp, sell_days=1)

    if exit_plans or cooling:
        acted = {t: p.reason for t, p in exit_plans.items()
                 if p.action in ("EXIT", "TRIM")}
        trail.record(
            "exits", "What would take you out",
            f"Every open position carries a stop at {exit_cfg.k_atr:g} x its own "
            f"ATR and trims at "
            f"{', '.join(f'+{r:g}R' for r in exit_cfg.ladder)}. The distance is the "
            f"name's own daily range, not a percentage, so a calm stock gets a "
            f"tight stop and a wild one gets room. A name sold in the last "
            f"{exit_cfg.cooldown_sessions} sessions is blocked from re-entry.",
            setting="risk block, configs/default.yaml",
            kept=[t for t, p in exit_plans.items() if p.action == "HOLD"],
            dropped={**acted, **{t: f"sold recently, {n} sessions to go"
                                 for t, n in cooling.items()}},
            n_in=len(exit_plans),
        )

    book_correlation = average_correlation(allocation.tickers(), correlations)

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
        # Mean pairwise correlation of the book. None when it cannot be measured,
        # never 0.0 -- which would read as "perfectly diversified".
        "book_correlation": book_correlation,
        "exit_plans": exit_plans,
        "cooling": cooling,
        # What the whole book loses if every stop fills. Per-position risk sizes a
        # trade; this is the one that keeps you solvent, and nothing said it before.
        "open_risk": _open_risk(exit_plans, settings.capital_rp),
    }


def _open_risk(exit_plans, capital_rp: float):
    from portfolio.exits import open_risk
    return open_risk(exit_plans, capital_rp) if exit_plans else None
