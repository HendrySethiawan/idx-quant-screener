# src/portfolio/performance.py
"""
Did any of this beat just buying the index?

**The benchmark.** A plain percentage return is misleading when money goes in and
out at irregular times. So this builds a *cash-flow-matched IHSG shadow*: every
rupiah that went into a stock on date D buys the same rupiah of ^JKSE on date D,
and every rupiah that came out redeems the same amount. It answers the only
question worth asking: would the same money, moved on the same days, have done
better in the index?

Comparison is on **total wealth** (cash + holdings), not bare position value. Cash
is identical on both sides by construction, but once positions are closed out your
position value is zero while the shadow may still hold units — comparing positions
alone then reports "-100%", which means nothing.

The shadow pays the same Indopremier fees, so the comparison isolates stock
selection rather than fee structure. Caveat worth stating in output: IHSG is not
directly buyable — a real index position would be an ETF with its own costs.

**The significance floor.** At 4-8 trades a month, three months is ~20 trades.
Splitting that into tool-picked versus self-picked and declaring a winner would be
reading noise. Below `min_trades_for_verdict` the report says how far it still has
to go and declines to judge. That restraint is the point of the module, not a
limitation of it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from portfolio.fees import FeeConfig
from portfolio.journal import _open_lots

DEFAULT_MIN_TRADES = 30


@dataclass
class ShadowResult:
    units: float = 0.0
    value_now: float = 0.0
    deployed: float = 0.0
    redeemed: float = 0.0
    shortfall: bool = False          # sells exceeded what the shadow still held
    unavailable: bool = False        # no IHSG history to compare against


@dataclass
class Attribution:
    source: str
    n_trades: int = 0
    net_pnl: float = 0.0
    win_rate: Optional[float] = None
    avg_return_pct: Optional[float] = None


@dataclass
class Performance:
    # cash and positions
    starting_capital: float = 0.0
    position_value: float = 0.0
    cash: float = 0.0
    total_value: float = 0.0

    # profit
    realized_pnl: float = 0.0
    unrealized_pnl: float = 0.0
    total_pnl: float = 0.0
    return_pct: float = 0.0

    # costs
    total_fees: float = 0.0
    stamp_paid: float = 0.0
    stamp_saved: float = 0.0
    stamp_avoidable: float = 0.0
    sell_days: int = 0
    fee_drag_pct: float = 0.0

    # Return measured against money actually put at risk, not against total
    # capital. A Rp176,896 profit on a Rp12,524 position reads as +0.2% of a
    # Rp100,000,000 account, which tells you nothing about the decision.
    closed_cost: float = 0.0          # cost basis of every closed round-trip
    open_cost: float = 0.0            # cost basis of what is still held
    return_on_closed_pct: Optional[float] = None
    return_on_open_pct: Optional[float] = None

    # Income. Held apart from realised P&L on purpose: a dividend is not a trading
    # decision, and folding it into the round-trip figures would make a bad entry
    # look like a good one because the company happened to pay out. It IS counted
    # in cash and total, because the money is genuinely there.
    #
    # Deliberately excluded from the index comparison: `^JKSE` is a price index and
    # pays nothing, so counting income on our side alone would flatter us by
    # exactly the dividend.
    dividend_income: float = 0.0
    dividend_by_ticker: Dict[str, float] = field(default_factory=dict)
    realised_yield_pct: Dict[str, float] = field(default_factory=dict)

    # benchmark
    comparable: bool = True           # False when there is nothing to compare yet
    shadow: ShadowResult = field(default_factory=ShadowResult)
    # None, not 0.0. A computed zero is a genuine dead heat and worth saying; an
    # uncomputed one used to render as "ahead by Rp0 (+0.00%)", which reads as a
    # photo finish when in fact no race was run.
    shadow_total: Optional[float] = None   # cash + shadow index units = total wealth
    vs_ihsg_rp: Optional[float] = None
    vs_ihsg_pct: Optional[float] = None

    # closed-trade stats
    n_closed: int = 0
    hit_rate: Optional[float] = None
    avg_win: Optional[float] = None
    avg_loss: Optional[float] = None
    avg_holding_days: Optional[float] = None

    # attribution
    attribution: List[Attribution] = field(default_factory=list)
    min_trades_for_verdict: int = DEFAULT_MIN_TRADES
    verdict: str = ""

    @property
    def has_verdict(self) -> bool:
        return self.n_closed >= self.min_trades_for_verdict


def _price_asof(series: pd.Series, when) -> Optional[float]:
    """Last close on or before `when`; None if the series starts later."""
    if series is None or series.empty:
        return None
    idx = pd.to_datetime(series.index).tz_localize(None)
    clean = pd.Series(series.values, index=idx).dropna()
    upto = clean[clean.index <= pd.to_datetime(when)]
    return float(upto.iloc[-1]) if len(upto) else None


def ihsg_shadow(journal: pd.DataFrame, ihsg_close: Optional[pd.Series]) -> ShadowResult:
    """Mirror every equity cash flow into ^JKSE instead."""
    out = ShadowResult()
    if journal is None or journal.empty:
        return out
    if ihsg_close is None or len(ihsg_close) == 0:
        out.unavailable = True
        return out

    df = journal.copy()
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df.sort_values("date", kind="stable")

    for _, row in df.iterrows():
        level = _price_asof(ihsg_close, row["date"])
        if not level:
            continue
        gross = float(row["gross_rp"])

        if str(row["action"]).upper() == "BUY":
            out.units += gross / level
            out.deployed += gross
        else:
            wanted = gross / level
            if wanted > out.units:
                # Your picks outran the index far enough that the shadow cannot
                # fund the same withdrawal. Flagged rather than silently clamped.
                out.shortfall = True
                wanted = out.units
            out.units -= wanted
            out.redeemed += wanted * level

    latest = _price_asof(ihsg_close, pd.Timestamp.max)
    out.value_now = out.units * latest if latest else 0.0
    return out


def _apply_dividends(perf: "Performance", dividends, open_cost, positions) -> "Performance":
    """
    Attach income, and the realised yield it implies per holding.

    Realised yield is measured against what the position actually cost, which is
    the only figure that can be set beside the forward yield the screener quoted
    from Yahoo. Those two are allowed to differ; the point of recording any of
    this is that the difference becomes visible instead of assumed.
    """
    from portfolio.dividends import by_ticker, realised_yield, total_received

    perf.dividend_income = total_received(dividends)
    perf.dividend_by_ticker = by_ticker(dividends)

    cost_basis = {t: float(open_cost.get(t, 0.0)) * int(shares)
                  for t, shares in (positions or {}).items()}
    perf.realised_yield_pct = realised_yield(dividends, cost_basis)
    return perf


def stamp_analysis(journal: pd.DataFrame, cfg: FeeConfig) -> Dict[str, float]:
    """
    What batching has already saved, and what further consolidation could save.

    Note on what is *not* computed here. An "overpaid" figure would need a
    counterfactual — which sells could have moved to another day — and that is a
    question about intent, not data. Selling on two different days may well have
    been the right call. So this reports two facts instead of one guess:

      `saved`      one stamp per sell DAY instead of per sell ORDER. Already banked.
      `avoidable`  what collapsing every sell day into one would have saved. An
                   upper bound on the remaining opportunity, not a mistake count.
    """
    empty = {"paid": 0.0, "if_unbatched": 0.0, "saved": 0.0, "avoidable": 0.0,
             "sell_days": 0, "n_sells": 0}
    if journal is None or journal.empty:
        return empty

    sells = journal[journal["action"].str.upper() == "SELL"]
    if sells.empty:
        return empty

    paid = float(sells["stamp_rp"].fillna(0).sum())
    n_sells = int(len(sells))
    sell_days = int(pd.to_datetime(sells["date"]).dt.normalize().nunique())

    if_unbatched = n_sells * cfg.stamp_duty_rp
    return {
        "paid": paid,
        "if_unbatched": float(if_unbatched),
        "saved": max(0.0, if_unbatched - paid),
        "avoidable": max(0.0, (sell_days - 1) * cfg.stamp_duty_rp),
        "sell_days": sell_days,
        "n_sells": n_sells,
    }


def _attribution(closed: pd.DataFrame) -> List[Attribution]:
    out: List[Attribution] = []
    if closed.empty:
        return out
    for source, grp in closed.groupby("source"):
        wins = (grp["net_pnl"] > 0).sum()
        out.append(Attribution(
            source=str(source),
            n_trades=len(grp),
            net_pnl=float(grp["net_pnl"].sum()),
            win_rate=float(wins / len(grp) * 100) if len(grp) else None,
            avg_return_pct=float(grp["return_pct"].mean()) if len(grp) else None,
        ))
    return sorted(out, key=lambda a: -a.n_trades)


def evaluate(
    journal: pd.DataFrame,
    closed: pd.DataFrame,
    positions: Dict[str, int],
    prices: Dict[str, float],
    open_cost: Dict[str, float],
    starting_capital: float,
    cfg: FeeConfig,
    ihsg_close: Optional[pd.Series] = None,
    min_trades_for_verdict: int = DEFAULT_MIN_TRADES,
    dividends: Optional[pd.DataFrame] = None,
) -> Performance:
    perf = Performance(
        starting_capital=starting_capital,
        min_trades_for_verdict=min_trades_for_verdict,
    )

    perf = _apply_dividends(perf, dividends, open_cost, positions)

    if journal is None or journal.empty:
        perf.cash = starting_capital + perf.dividend_income
        perf.total_value = perf.cash
        perf.verdict = "No trades logged yet."
        return perf

    # Cash: starting capital plus the signed net effect of every trade.
    # Income is real cash. It is not realised P&L -- see the field comment.
    perf.cash = (starting_capital + float(journal["net_rp"].fillna(0).sum())
                 + perf.dividend_income)
    perf.position_value = sum(shares * prices.get(t, 0.0) for t, shares in positions.items())
    perf.total_value = perf.cash + perf.position_value

    perf.realized_pnl = float(closed["net_pnl"].sum()) if not closed.empty else 0.0
    perf.unrealized_pnl = sum(
        shares * (prices.get(t, 0.0) - open_cost.get(t, 0.0))
        for t, shares in positions.items()
        if t in prices and t in open_cost
    )
    # Income counts toward what you made, but not toward what your TRADING made.
    # Keeping it out of realised_pnl is what stops a bad entry reading as a good
    # one because the company paid out while you held it.
    perf.total_pnl = perf.realized_pnl + perf.unrealized_pnl + perf.dividend_income
    perf.return_pct = (perf.total_pnl / starting_capital * 100) if starting_capital else 0.0

    perf.total_fees = float(journal["fee_rp"].fillna(0).sum() + journal["stamp_rp"].fillna(0).sum())
    stamps = stamp_analysis(journal, cfg)
    perf.stamp_paid = stamps["paid"]
    perf.stamp_saved = stamps["saved"]
    perf.stamp_avoidable = stamps["avoidable"]
    perf.sell_days = int(stamps["sell_days"])
    perf.fee_drag_pct = (perf.total_fees / starting_capital * 100) if starting_capital else 0.0

    # Cost basis, for a return that means something at this account size.
    if not closed.empty:
        buy_cost = (closed["shares"] * closed["buy_price"]).sum()
        perf.closed_cost = round(float(buy_cost + closed["fees"].sum() / 2), 2)
        if perf.closed_cost > 0:
            perf.return_on_closed_pct = round(
                perf.realized_pnl / perf.closed_cost * 100, 2)

    open_lots = _open_lots(journal)
    perf.open_cost = round(float(sum(
        lot["shares"] * (lot["price"] + lot["per_share_fee"])
        for lots in open_lots.values() for lot in lots)), 2)
    if perf.open_cost > 0:
        perf.return_on_open_pct = round(perf.unrealized_pnl / perf.open_cost * 100, 2)

    perf.shadow = ihsg_shadow(journal, ihsg_close)
    # The comparison needs at least one overnight hold. Everything bought and sold
    # on the same day cannot differ from a daily-close index series by construction,
    # and reporting that as "ahead by Rp0 (+0.00%)" reads as a dead heat rather than
    # as no contest. Still holding something, or having traded across more than one
    # session, both count as a real comparison -- including a genuine dead heat over
    # months of flat index.
    trade_dates = pd.to_datetime(journal["date"], errors="coerce").dt.date.dropna()
    perf.comparable = bool(
        perf.position_value > 0
        or perf.shadow.units > 0
        or trade_dates.nunique() > 1
    )
    if not perf.shadow.unavailable and perf.comparable:
        # Compare TOTAL WEALTH, not bare position value. The two sides share an
        # identical cash balance by construction, but once positions are closed out
        # your position value is 0 while the shadow may still hold units -- comparing
        # positions alone then reads as "-100%", which is meaningless.
        perf.shadow_total = perf.cash + perf.shadow.value_now
        perf.vs_ihsg_rp = perf.total_value - perf.shadow_total
        perf.vs_ihsg_pct = (
            perf.vs_ihsg_rp / perf.shadow_total * 100 if perf.shadow_total else 0.0
        )

    perf.n_closed = len(closed)
    if perf.n_closed:
        wins = closed[closed["net_pnl"] > 0]
        losses = closed[closed["net_pnl"] <= 0]
        perf.hit_rate = float(len(wins) / perf.n_closed * 100)
        perf.avg_win = float(wins["net_pnl"].mean()) if len(wins) else None
        perf.avg_loss = float(losses["net_pnl"].mean()) if len(losses) else None
        perf.avg_holding_days = float(closed["holding_days"].mean())

    perf.attribution = _attribution(closed)

    if perf.n_closed < min_trades_for_verdict:
        perf.verdict = (
            f"Not enough data yet - {perf.n_closed} of {min_trades_for_verdict} closed trades. "
            "Too few to tell skill from luck, so no winner is declared."
        )
    else:
        best = max(perf.attribution, key=lambda a: a.avg_return_pct or -1e9, default=None)
        if best:
            label = {"tool": "the screener's picks", "own": "your own picks"}.get(best.source, best.source)
            perf.verdict = (
                f"Over {perf.n_closed} closed trades, {label} lead on average return "
                f"({best.avg_return_pct:+.2f}% per trade). Still a small sample - treat as indicative."
            )
    return perf
