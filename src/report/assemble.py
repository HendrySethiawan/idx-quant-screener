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
                                break_ties, decorrelated_pick, sector_capped_pick,
                                tie_groups)
from market.liquidity import LiquidityConfig, assess
from portfolio.fees import FeeConfig, estimate_fees
from portfolio.holdings import Holding
from portfolio.sizing import Allocation, choose_allocation, lot_price
from report.explain import data_quality_note, health_flags, reason_phrase


def _price_of(row) -> Optional[float]:
    price = row.get("last_close")
    return float(price) if price and not pd.isna(price) else None


def _rp(v: Optional[float]) -> str:
    """Rupiah for an order note. `report.brief.rp` is the display one; importing
    it here would point a data module at a rendering one."""
    return "-" if v is None else f"Rp{float(v):,.0f}"


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
    score_floor: float = 0.0,
    ties: Optional[List[List[str]]] = None,
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
            # The un-normalised composite. `score` above is min-max scaled to 0-1
            # for display, so the best name is always 1.00 whatever the market did
            # -- which makes it useless for asking how FAR apart two names are.
            # `score_floor` is measured on this one, and comparing the two scales
            # made every name in the universe read as tied with every other.
            "raw_score": float(row.get("raw_score", row.get("undervaluation_score", 0.0))),
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

    # Before any gate reads the order. Every score here is a z-score against the
    # rest of the list, so a score is a property of the company AND its peers --
    # drop one unrelated name and everybody moves a little. `score_floor` is how
    # much, measured by jackknife, and picks 3 to 8 sit 0.02 to 0.21 apart against
    # a spread of 3.75. Where the score cannot separate two names, the one that
    # least duplicates what has already been taken goes first, and the ticket says
    # the order inside a tie is not a preference.
    # `raw_score`, not the 0-to-1 display score: the floor is measured on the
    # composite's own scale, and `tie_groups` refuses a floor that would swallow
    # the universe rather than acting on it.
    raw = {c["ticker"]: c["raw_score"] for c in candidates}
    groups = tie_groups(ranked, raw, float(score_floor or 0.0))
    tied = [g for g in groups if len(g) > 1]
    # Reported from the FULL ranked list, before the shortlist cut. A tie between
    # the last name in and the first name out is exactly the one worth seeing, and
    # computing this after the truncation would hide it.
    if ties is not None:
        ties.clear()
        ties.extend(tied)
    if tied and correlations is not None:
        ranked = break_ties(ranked, raw, correlations,
                            float(score_floor or 0.0), groups=groups)
        order = {t: i for i, t in enumerate(ranked)}
        candidates.sort(key=lambda c: order.get(c["ticker"], len(order)))

    if trail is not None and tied:
        trail.record(
            "ties", "Which of these are actually level",
            f"Scores closer together than {score_floor:.2f} cannot be told apart: "
            f"that is how far one moves when the universe gains or loses a single "
            f"name, measured on today's list rather than assumed. Among names the "
            f"score cannot separate, the one that moves least like what is already "
            f"picked goes first.",
            setting="selection.tie_floor_quantile, configs/default.yaml",
            kept=ranked,
            note="; ".join(" = ".join(t.replace(".JK", "") for t in g)
                           for g in tied[:6]),
        )

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
    rank_of: Optional[Dict[str, int]] = None,
    raw_of: Optional[Dict[str, float]] = None,
    score_floor: float = 0.0,
    universe_n: int = 0,
    book_state: Optional[dict] = None,
) -> List[dict]:
    """
    One decision per position, and the exit panel renders the same one.

    **An exit outranks the ranking.** A stop that has been hit is a decision
    already made about a position you hold; the ranking is an opinion about one you
    might open. So a name with an EXIT or TRIM plan produces that row and is then
    skipped by the rest of this function entirely.

    **Everything else is one of two different things, and they were called the
    same thing.** A position can leave the book because the regime shrank the
    budget (DERISK) or because a better name took its slot (ROTATE). Both used to
    read "no longer in the target book", so a rank-4 holding being liquidated to
    cut exposure was indistinguishable from a rank-33 one that had simply been
    beaten. They are now separated, because only one of them is a verdict on the
    name.

    **A rotation does not override a healthy HOLD it cannot actually beat.** The
    ranking is z-scores against a list, and `score_floor` is how far a name moves
    when the universe gains or loses one member. Selling a position you hold to
    buy one that is inside that distance is paying 0.29%, the stamp and 0.19% to
    act on a difference the score cannot resolve. A de-risk is not subject to this
    test: it is not claiming the other name is better, it is reducing exposure.

    **`cooling` blocks the re-buy.** Without it the loop closes on itself: the stop
    sells today, tomorrow's re-rank puts the name straight back in the target book,
    and you pay 0.29% out, Rp10,000 stamp and 0.19% back in to end up exactly where
    you started.
    """
    from portfolio.exits import DERISK, EXIT, HOLD, ROTATE, TRIM

    exit_plans = exit_plans or {}
    cooling = cooling or {}
    rank_of = rank_of or {}
    raw_of = raw_of or {}
    target = {p.ticker: p for p in allocation.positions}
    held = {h.ticker: h for h in holdings if h.lots > 0}
    orders: List[dict] = []
    decided: set = set()

    def _rank_phrase(ticker: str) -> str:
        """"#17 of 74", or "" when the name is not in today's ranking at all."""
        i = rank_of.get(ticker)
        if i is None:
            return ""
        return f"#{i + 1} of {universe_n}" if universe_n else f"#{i + 1}"

    def _book_says(ticker: str, action: str, reason: str, cause: str = "") -> None:
        """
        Record the book's verdict on the plan, so the exit panel shows this and
        not its own. Silent when there is no plan -- an unheld name has none.
        """
        plan = exit_plans.get(ticker)
        if plan is None:
            return
        plan.book_action = action
        plan.book_reason = reason
        plan.book_cause = cause

    for ticker, plan in exit_plans.items():
        # CHECK ENTRY never reaches here: `plan_for` returns before computing a
        # stop or a ladder when the entry price cannot be a real fill, so there is
        # no level to act on. The rebalance below may still sell the name, priced
        # off the market rather than off the bad record.
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

    # What is still held after the exits above have taken their lots. A position
    # trimmed 28 of 41 lots is Rp1.7 juta of exposure, not Rp5.4 juta, and the
    # budget test below has to measure the book you will actually be left with.
    def _value_after_exits(ticker: str, holding: Holding) -> float:
        price = prices.get(ticker) or 0.0
        sold = sum(o["lots"] for o in orders
                   if o["ticker"] == ticker and o["action"] == "SELL")
        return max(0, holding.lots - sold) * (holding.lot_size or 100) * price

    remaining = {t: _value_after_exits(t, h) for t, h in held.items()}
    book_value = sum(remaining.values())
    budget = float(allocation.budget or 0.0)

    # DE-RISK. You hold more than today's budget allows, so the book has to come
    # down whatever the ranking thinks. Worst-ranked first: cutting a rank-33
    # holding to keep a rank-4 one reduces exposure by the same rupiah and leaves
    # you holding the better names. Rebuilding the book from the ranking instead
    # sold a rank-4 holding to buy a rank-1 one, which is a rotation wearing a
    # de-risk's clothes -- it pays both sides of the spread to reduce nothing.
    derisking = budget > 0 and book_value > budget
    if book_state is not None:
        book_state.clear()
        book_state.update({"derisking": derisking, "budget_rp": budget,
                           "book_rp": book_value})
    if derisking:
        candidates_to_cut = sorted(
            (t for t in held if t not in decided),
            key=lambda t: rank_of.get(t, len(rank_of) + 1),
        )
        # A position the exits already acted on is staying at whatever size they
        # left it -- an exit outranks the book -- so its remainder is exposure the
        # budget has to cover before anything else competes for room. Starting the
        # count at zero let a 28-of-41-lot trim leave Rp1.7 juta uncounted, and the
        # book then "fitted" a budget it was Rp1.7 juta over.
        kept_value = sum(remaining.get(t, 0.0) for t in decided)

        # Drop from the WORST end until the book fits, rather than filling from the
        # best end. Filling is bin-packing: a large well-ranked position that does
        # not fit gets cut while a small badly-ranked one is kept, so the survivors
        # are not the best names at all. Dropping guarantees they are.
        survivors = set(candidates_to_cut)
        running = kept_value + sum(remaining[t] for t in survivors)
        for ticker in reversed(candidates_to_cut):
            if running <= budget:
                break
            survivors.discard(ticker)
            running -= remaining[ticker]

        for ticker in candidates_to_cut:
            holding = held[ticker]
            price = prices.get(ticker)
            where = _rank_phrase(ticker)
            if ticker in survivors:
                plan = exit_plans.get(ticker)
                note = (f"kept — {where}, and the book fits today's "
                        f"{_rp(budget)} budget with it in"
                        if where else
                        f"kept — the book fits today's {_rp(budget)} budget with it in")
                if plan is not None and plan.action == HOLD:
                    note = f"{plan.reason}. {note}"
                orders.append({
                    "action": "HOLD", "ticker": ticker, "lots": holding.lots,
                    "shares": holding.shares, "price": price,
                    "rupiah": remaining[ticker], "note": note,
                })
                _book_says(ticker, "", "", "")
                continue

            reason = (
                f"cutting the book back to today's {_rp(budget)} budget — you hold "
                f"{_rp(book_value)}. This was the worst-ranked of what you own"
                + (f" ({where})" if where else "")
                + ". It is not a verdict on the name."
            )
            orders.append({
                "action": "SELL", "ticker": ticker, "lots": holding.lots,
                "shares": holding.shares, "price": price,
                "rupiah": (holding.shares * price) if price else 0.0,
                "note": reason, "sell_cause": DERISK,
            })
            _book_says(ticker, "SELL", reason, DERISK)
        decided.update(candidates_to_cut)

    else:
        # ROTATION. The book fits; a name is only sold because something better
        # took its slot -- and that claim has to survive the score's own precision.
        best_in = max((raw_of.get(t, float("-inf")) for t in target
                       if t not in held), default=None)
        for ticker, holding in held.items():
            if ticker in target or ticker in decided:
                continue
            price = prices.get(ticker)
            plan = exit_plans.get(ticker)
            mine = raw_of.get(ticker)
            where = _rank_phrase(ticker)

            level = (
                best_in is not None and mine is not None
                and best_in != float("-inf")
                and (best_in - mine) <= float(score_floor or 0.0)
            )
            if level and plan is not None and plan.action == HOLD:
                note = (
                    f"{plan.reason}. Held: the name that would take this slot is "
                    f"ahead by {best_in - mine:.2f}, inside the "
                    f"{float(score_floor):.2f} the score can actually resolve — "
                    f"that is not a good enough reason to pay both sides of the "
                    f"spread."
                )
                orders.append({
                    "action": "HOLD", "ticker": ticker, "lots": holding.lots,
                    "shares": holding.shares, "price": price,
                    "rupiah": remaining.get(ticker, 0.0), "note": note,
                })
                _book_says(ticker, "", "", "")
                continue

            margin = ("" if (best_in is None or mine is None
                             or best_in == float("-inf"))
                      else f", beaten by {best_in - mine:.2f} against a "
                           f"{float(score_floor):.2f} noise floor")
            reason = (f"out-ranked{f' — {where}' if where else ''}{margin}")
            orders.append({
                "action": "SELL", "ticker": ticker, "lots": holding.lots,
                "shares": holding.shares, "price": price,
                "rupiah": (holding.shares * price) if price else 0.0,
                "note": reason, "sell_cause": ROTATE,
            })
            _book_says(ticker, "SELL", reason, ROTATE)

    for ticker, pos in target.items():
        if ticker in decided:
            continue
        # Over budget and buying in the same breath is incoherent: every lot bought
        # has to be funded by a lot sold, so the round trip costs 0.19% + 0.29% +
        # the stamp to leave exposure exactly where the de-risk was trying to
        # reduce it from.
        if derisking:
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
            # Not when the row has already said it. The book's own HOLD notes open
            # with this same reason and then explain why the position survived a
            # de-risk or a rotation -- overwriting wholesale threw that sentence
            # away and left the reader looking at a bare stop again, which is how
            # a HOLD came to give no account of itself.
            if plan.reason not in (order.get("note") or ""):
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
                  today=None, review_lookback_days: Optional[int] = None) -> None:
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
    from market.events import REVIEW_LOOKBACK_DAYS, by_ticker, state_for

    lookback = (REVIEW_LOOKBACK_DAYS if review_lookback_days is None
                else int(review_lookback_days))
    grouped = by_ticker(events)
    for item in items:
        ticker = item.get("ticker")
        state, message = state_for(ticker, grouped.get(ticker, []), blind,
                                   horizon_days, today=today,
                                   review_lookback_days=lookback)
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

    Returns `(plans, cooling, cfg, bad_entries)`. All empty when there is nothing
    open, which is the ordinary case for a fresh install and must not be an error.
    """
    from portfolio.exits import ExitConfig, cooldown, plans_for, position_history
    from portfolio.fees import FeeConfig
    from portfolio.ledger import implausible_entries, open_positions

    cfg = ExitConfig.from_settings(settings)
    fee_cfg = FeeConfig.from_settings(settings)

    if journal is None or getattr(journal, "empty", True):
        return {}, {}, cfg, {}

    panel = risk_panel or {}
    closes = panel.get("Close")
    atr, notes = {}, {}
    if df is not None and not getattr(df, "empty", True):
        for _, row in df.iterrows():
            ticker = row["ticker"]
            atr[ticker] = _num(row.get("atr_14"))
            notes[ticker] = str(row.get("price_note") or "")

    # One detection site. An entry that cannot be a real fill poisons the stop,
    # the ladder, the risk figure and the verdict, because every one of them is
    # measured from it -- so it is caught before any of them is computed.
    bad_entries = implausible_entries(journal, closes)

    plans = plans_for(
        open_positions(journal, prices), closes, cfg, fee_cfg,
        highs=panel.get("High"),
        history=position_history(journal, fee_cfg.lot_size),
        atr=atr, price_notes=notes, entry_notes=bad_entries,
        capital_rp=settings.capital_rp,
    )
    sessions = None if closes is None else pd.DatetimeIndex(closes.index)
    return (plans, cooldown(journal, cfg, today=today, sessions=sessions),
            cfg, bad_entries)


def assemble(settings, df: pd.DataFrame, regime, holdings: List[Holding],
             correlations=None,
             events=None, blind=None,
             risk_panel=None, journal=None, today=None,
             score_floor: float = 0.0):
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

    ties: List[List[str]] = []
    candidates, rejected, capped = build_candidates(
        df, settings, regime.deploy_pct, trail=trail, correlations=correlations,
        score_floor=score_floor, ties=ties,
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
    exit_plans, cooling, exit_cfg, bad_entries = build_exit_plans(
        settings, df, prices, risk_panel=risk_panel, journal=journal, today=today)

    fee_cfg = FeeConfig.from_settings(settings)
    # From `df`, not `candidates`: `build_candidates` truncates to the shortlist,
    # so a held name ranked 17th or 33rd has no score there at all -- and those are
    # exactly the ones the book has to explain selling. `df` is sorted by
    # `undervaluation_score` in `pipeline.run_screener`, which is a min-max of
    # `raw_score`, so position IS rank.
    rank_of = {t: i for i, t in enumerate(df["ticker"])}
    raw_of = {t: float(v) for t, v in
              zip(df["ticker"], df.get("raw_score", df["undervaluation_score"]))}
    book_state: Dict[str, object] = {}
    orders = build_orders(allocation, holdings, prices, exit_plans, cooling,
                          rank_of=rank_of, raw_of=raw_of, score_floor=score_floor,
                          universe_n=len(df), book_state=book_state)
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
        lookback = int(getattr(settings, "review_lookback_days", 21))
        attach_events(orders, events, blind or set(), horizon,
                      review_lookback_days=lookback)
        attach_events(candidates, events, blind or set(), horizon,
                      review_lookback_days=lookback)

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
        # The precision the ranking has, so the page can say which picks are
        # level rather than presenting 0.02 as a decision.
        "score_floor": float(score_floor or 0.0),
        "book_state": book_state,
        # Filled by `build_candidates` from the full ranked list, so a tie that
        # straddles the shortlist boundary is still visible.
        "tie_groups": ties,
        "exit_plans": exit_plans,
        "cooling": cooling,
        # Positions whose recorded entry price cannot be a real fill. Kept in
        # the totals and flagged, never silently dropped.
        "bad_entries": bad_entries,
        # What the whole book loses if every stop fills. Per-position risk sizes a
        # trade; this is the one that keeps you solvent, and nothing said it before.
        "open_risk": _open_risk(exit_plans, settings.capital_rp),
    }


def _open_risk(exit_plans, capital_rp: float):
    from portfolio.exits import open_risk
    return open_risk(exit_plans, capital_rp) if exit_plans else None
