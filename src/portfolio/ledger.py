# src/portfolio/ledger.py
"""
Month-by-month realised profit, and what is still open.

Nothing here recomputes a fee. `closed_trades` ([journal.py]) already matches sells
to buys FIFO and gives every round-trip a `net_pnl` that is net of the buy fee, the
sell fee and the stamp, apportioned per share. This groups those rows and adds
nothing to them, so the monthly table and the headline figure can never disagree.

**A round-trip counts in the month it was SOLD**, carrying the buy fee paid in
whatever earlier month it was paid. Two consequences worth being deliberate about:

  * A month's number answers "what did I make this month", which is the question
    being asked. Cash-basis accounting would show a month where you only bought as a
    large loss, which is true about cash and false about profit.
  * **A month's figure never changes once the month has ended.** Selling in October
    cannot move September's row, so month-to-month comparison means something.

Positions still open are excluded entirely and reported separately. Their buy fee is
already spent, but the trade is not finished and calling it a loss would be as wrong
as calling it nothing.
"""
from __future__ import annotations

from typing import Dict, List, Optional

import pandas as pd

MONTHLY_COLS = [
    "month", "trades", "gross_pnl", "fees", "net_pnl", "win_rate", "tickers",
]

OPEN_COLS = [
    "ticker", "shares", "lots", "avg_cost", "cost_basis",
    "price_now", "value_now", "unrealized_pnl", "unrealized_pct",
]


def monthly_realized(closed: Optional[pd.DataFrame]) -> pd.DataFrame:
    """
    One row per calendar month in which something was sold.

    The rows are built to sum: `result["net_pnl"].sum()` equals
    `closed["net_pnl"].sum()` exactly. A monthly table whose rows do not add up to
    the headline is worse than no table, because it is the headline that gets
    quoted and the rows that get believed.
    """
    if closed is None or closed.empty or "sell_date" not in closed.columns:
        return pd.DataFrame(columns=MONTHLY_COLS)

    df = closed.copy()
    df["sell_date"] = pd.to_datetime(df["sell_date"], errors="coerce")
    df = df.dropna(subset=["sell_date"])
    if df.empty:
        return pd.DataFrame(columns=MONTHLY_COLS)

    df["month"] = df["sell_date"].dt.strftime("%Y-%m")

    rows: List[dict] = []
    for month, grp in df.groupby("month", sort=True):
        net = grp["net_pnl"].astype(float)
        rows.append({
            "month": month,
            "trades": int(len(grp)),
            "gross_pnl": round(float(grp["gross_pnl"].astype(float).sum()), 2),
            "fees": round(float(grp["fees"].astype(float).sum()), 2),
            "net_pnl": round(float(net.sum()), 2),
            "win_rate": round(float((net > 0).mean()), 4),
            "tickers": int(grp["ticker"].nunique()),
        })

    return pd.DataFrame(rows, columns=MONTHLY_COLS)


def monthly_totals(monthly: pd.DataFrame) -> Dict[str, float]:
    """The footer row. Kept here so the view cannot invent a different total."""
    if monthly is None or monthly.empty:
        return {"trades": 0, "gross_pnl": 0.0, "fees": 0.0, "net_pnl": 0.0,
                "win_rate": 0.0, "months": 0}
    return {
        "months": int(len(monthly)),
        "trades": int(monthly["trades"].sum()),
        "gross_pnl": round(float(monthly["gross_pnl"].sum()), 2),
        "fees": round(float(monthly["fees"].sum()), 2),
        "net_pnl": round(float(monthly["net_pnl"].sum()), 2),
        "win_rate": round(
            float((monthly["win_rate"] * monthly["trades"]).sum()
                  / max(1, monthly["trades"].sum())), 4),
    }


def open_positions(journal: Optional[pd.DataFrame],
                   prices: Optional[Dict[str, float]] = None) -> pd.DataFrame:
    """
    What is still held, at fee-inclusive cost.

    Reads the same FIFO queues `closed_trades` uses, so the shares reported here are
    exactly the ones that have not been matched to a sell. `avg_cost` includes the
    buy fee actually paid, because that is what the position has to beat before it
    is genuinely ahead.

    `price_now` is whatever the last run fetched. Without it the position is still
    listed, with its cost and no P&L -- a holding that vanishes because a price is
    missing is the sort of gap somebody trades on.
    """
    from portfolio.journal import _open_lots

    if journal is None or journal.empty:
        return pd.DataFrame(columns=OPEN_COLS)

    prices = prices or {}
    rows: List[dict] = []

    for ticker, lots in _open_lots(journal).items():
        shares = sum(int(lot["shares"]) for lot in lots)
        if shares <= 0:
            continue

        cost = sum(lot["shares"] * (lot["price"] + lot["per_share_fee"]) for lot in lots)
        price_now = prices.get(ticker)
        value_now = None if price_now is None else float(price_now) * shares
        unreal = None if value_now is None else value_now - cost

        rows.append({
            "ticker": ticker,
            "shares": shares,
            "lots": shares // 100,
            "avg_cost": round(cost / shares, 2) if shares else 0.0,
            "cost_basis": round(cost, 2),
            "price_now": None if price_now is None else float(price_now),
            "value_now": None if value_now is None else round(value_now, 2),
            "unrealized_pnl": None if unreal is None else round(unreal, 2),
            "unrealized_pct": (None if unreal is None or cost <= 0
                               else round(unreal / cost * 100, 2)),
        })

    rows.sort(key=lambda r: r["cost_basis"], reverse=True)
    return pd.DataFrame(rows, columns=OPEN_COLS)


# A round-trip beyond this is almost always a mistyped entry price rather than a
# result: +1412% on a same-day trade implies a 15x move.
IMPLAUSIBLE_RETURN_PCT = 200.0


def implausible(row) -> str:
    """Why a round-trip's return should not be believed, or "" if it is fine."""
    try:
        ret = float(row["return_pct"])
        buy, sell = float(row["buy_price"]), float(row["sell_price"])
    except (TypeError, ValueError, KeyError):
        return ""
    if abs(ret) < IMPLAUSIBLE_RETURN_PCT:
        return ""
    move = (sell / buy - 1) * 100 if buy else 0.0
    return (f"implies a {move:+,.0f}% move in the price - check the entry, "
            f"Rp{buy:,.0f} to Rp{sell:,.0f}")


# How far an entry may sit from the close of the session it was made in before it
# stops being a price and starts being a typo. Not a taste: IDX auto-rejection
# caps a day's move at 35% / 25% / 20% by price tier, so a real fill can never be
# half the reference price away. Below this it cannot fire on a genuine trade;
# above it, every dropped, extra or transposed digit lands.
IMPLAUSIBLE_ENTRY_GAP = 0.50


def _close_asof(series: Optional[pd.Series], when) -> Optional[float]:
    """Last close on or before `when`; None when the series starts later."""
    if series is None or getattr(series, "empty", True):
        return None
    idx = pd.to_datetime(series.index)
    idx = idx.tz_localize(None) if getattr(idx, "tz", None) is not None else idx
    clean = pd.Series(series.values, index=idx).dropna()
    upto = clean[clean.index <= pd.to_datetime(when)]
    return float(upto.iloc[-1]) if len(upto) else None


def implausible_entries(journal: Optional[pd.DataFrame],
                        closes: Optional[pd.DataFrame],
                        max_gap: float = IMPLAUSIBLE_ENTRY_GAP) -> Dict[str, str]:
    """
    Open positions whose recorded entry price cannot be a real fill.

    `implausible` above catches this for a CLOSED round-trip and has since it was
    written -- a mistyped SRTG entry showed +1412% and prompted it. Nothing caught
    it while the position was still open, so an AMRT bought at Rp50 against a
    Rp1,310 market read as +2,515% profit, produced a stop 2,976% away, and put a
    SELL in the ticket generated entirely by the typo.

    Measured against the close of the session the trade was made in, NOT today's.
    Comparing against the current price would flag a position that has genuinely
    tripled since it was bought, which is the one case where the number is real and
    most worth trusting.

    Returns `{ticker: reason}`, in the same voice as `implausible`, so the two read
    as one idea rather than two.
    """
    out: Dict[str, str] = {}
    if journal is None or getattr(journal, "empty", True) or closes is None:
        return out

    df = journal.copy()
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df[df["action"].astype(str).str.upper() == "BUY"].dropna(subset=["date"])

    for _, row in df.iterrows():
        ticker = str(row["ticker"])
        if ticker in out or ticker not in getattr(closes, "columns", []):
            continue
        try:
            entry = float(row["price"])
        except (TypeError, ValueError):
            continue
        reference = _close_asof(closes[ticker], row["date"])
        if not reference or reference <= 0 or entry <= 0:
            continue

        gap = abs(entry / reference - 1.0)
        if gap > float(max_gap):
            out[ticker] = (
                f"recorded at Rp{entry:,.0f}, which is {gap * 100:,.0f}% from the "
                f"Rp{reference:,.0f} close on {row['date']:%d %b %y} - check the "
                f"entry against your broker"
            )
    return out


def recent_trades(journal: Optional[pd.DataFrame], limit: int = 20) -> pd.DataFrame:
    """The raw log, newest first -- every buy and sell exactly as recorded."""
    if journal is None or journal.empty:
        return pd.DataFrame()
    df = journal.copy()
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    return df.sort_values("date", ascending=False, na_position="last").head(int(limit))
