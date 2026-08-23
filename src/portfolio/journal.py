# src/portfolio/journal.py
"""
The trade log, and realised P&L computed from it.

Two things this must get right, because everything downstream inherits them:

**FIFO cost basis.** Indonesian broker statements report on a first-in-first-out
basis, so matching sells against the oldest open lots means the numbers here
reconcile against Indopremier rather than quietly diverging from it. Average-cost
would be simpler and would disagree with the statement.

**Fees belong to the trade, not to a footnote.** Every closed round-trip carries
its allocated share of the buy fee, the sell fee, and the stamp. A Rp45,000 gross
profit on a Rp1.2 juta position is Rp28,893 after Indopremier takes its cut, and
the second number is the one that reached the account.

The stamp is charged once per DAY containing a sell. `stamp_for` checks the log
before charging, so the file records what batching actually saved rather than a
model of what it might save.
"""
from __future__ import annotations

from collections import deque
from datetime import date as _date
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd

from portfolio.fees import FeeConfig

TRADE_COLS = [
    "date", "ticker", "action", "lots", "shares", "price",
    "gross_rp", "fee_rp", "stamp_rp", "net_rp", "source", "note",
]

CLOSED_COLS = [
    "ticker", "buy_date", "sell_date", "shares", "buy_price", "sell_price",
    "gross_pnl", "fees", "net_pnl", "return_pct", "holding_days", "source",
]

MARK_COLS = ["date", "positions_value_rp", "cash_rp", "total_rp", "ihsg_close"]

VALID_ACTIONS = ("BUY", "SELL")


def normalize_ticker(ticker: str) -> str:
    """`bbri` -> `BBRI.JK`. Index tickers and already-suffixed names pass through."""
    t = str(ticker).strip().upper()
    if not t:
        raise ValueError("empty ticker")
    if t.startswith("^") or "." in t or "=" in t:
        return t
    return f"{t}.JK"


def load_journal(path: str | Path) -> pd.DataFrame:
    """Empty, correctly-typed frame when the file is missing or unreadable."""
    p = Path(path)
    if not p.exists():
        return pd.DataFrame(columns=TRADE_COLS)
    try:
        df = pd.read_csv(p)
    except Exception:
        return pd.DataFrame(columns=TRADE_COLS)

    for col in TRADE_COLS:
        if col not in df.columns:
            df[col] = pd.NA
    df = df[TRADE_COLS]
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    return df.dropna(subset=["date", "ticker", "action"]).reset_index(drop=True)


def save_journal(df: pd.DataFrame, path: str | Path) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    out = df.copy()
    if "date" in out.columns and len(out):
        out["date"] = pd.to_datetime(out["date"]).dt.strftime("%Y-%m-%d")
    out.to_csv(p, index=False)
    return p


def stamp_for(journal: pd.DataFrame, on_date, action: str, cfg: FeeConfig) -> float:
    """
    Rp10,000 on the first sell of a day, Rp0 on every later sell that day.

    This is what makes batching measurable: the log ends up holding the stamp the
    user genuinely paid, so `stamp_overpaid` later is an observation, not a guess.
    """
    if str(action).upper() != "SELL":
        return 0.0
    if journal is None or journal.empty:
        return float(cfg.stamp_duty_rp)

    target = pd.to_datetime(on_date).normalize()
    dates = pd.to_datetime(journal["date"], errors="coerce").dt.normalize()
    same_day_sells = journal[(dates == target) & (journal["action"].str.upper() == "SELL")]
    return 0.0 if len(same_day_sells) else float(cfg.stamp_duty_rp)


def build_trade(
    action: str,
    ticker: str,
    lots: int,
    price: float,
    cfg: FeeConfig,
    journal: Optional[pd.DataFrame] = None,
    on_date=None,
    source: str = "tool",
    note: str = "",
) -> dict:
    """
    Cost a single trade. `net_rp` is the signed cash impact on the account:
    negative when buying, positive when selling.
    """
    action = str(action).upper()
    if action not in VALID_ACTIONS:
        raise ValueError(f"action must be one of {VALID_ACTIONS}, got {action!r}")

    lots = int(lots)
    price = float(price)
    if lots <= 0:
        raise ValueError("lots must be a positive whole number")
    if price <= 0:
        raise ValueError("price must be positive")

    shares = lots * cfg.lot_size
    gross = shares * price
    rate = cfg.buy_fee if action == "BUY" else cfg.sell_fee
    fee = gross * rate
    stamp = stamp_for(journal, on_date or _date.today(), action, cfg)
    net = -(gross + fee + stamp) if action == "BUY" else (gross - fee - stamp)

    return {
        "date": pd.to_datetime(on_date or _date.today()).strftime("%Y-%m-%d"),
        "ticker": normalize_ticker(ticker),
        "action": action,
        "lots": lots,
        "shares": shares,
        "price": price,
        "gross_rp": round(gross, 2),
        "fee_rp": round(fee, 2),
        "stamp_rp": round(stamp, 2),
        "net_rp": round(net, 2),
        "source": str(source).lower(),
        "note": note,
    }


def append_trade(trade: dict, path: str | Path) -> pd.DataFrame:
    journal = load_journal(path)
    updated = pd.concat([journal, pd.DataFrame([trade])], ignore_index=True)
    # load_journal parses dates to datetime while build_trade emits a string, so
    # the concat leaves a mixed-type column that cannot be sorted. Normalise first.
    updated["date"] = pd.to_datetime(updated["date"], errors="coerce")
    updated = updated.sort_values("date", kind="stable").reset_index(drop=True)
    save_journal(updated, path)
    return updated


def remove_last_trade(path: str | Path) -> Optional[dict]:
    """
    Drop the most recently dated row and return it, or None if there is nothing.

    Only the last one, deliberately. A typo has to be correctable -- a trade entered
    at the wrong price is otherwise permanent, and the ledger carries the nonsense
    forever. But removing an *older* row could delete a buy that a later sell has
    already been matched against, and `closed_trades` would then compute round-trips
    against a lot that no longer exists. The newest row can never have been matched
    against by anything, so undoing it is always safe.

    Ties on date are broken by position, so the row removed is the one `append_trade`
    added last.
    """
    journal = load_journal(path)
    if journal.empty:
        return None

    journal = journal.copy()
    journal["date"] = pd.to_datetime(journal["date"], errors="coerce")
    order = journal["date"].rank(method="first", na_option="top")
    drop_idx = order.idxmax()

    removed = journal.loc[drop_idx].to_dict()
    remaining = journal.drop(index=drop_idx).reset_index(drop=True)
    save_journal(remaining, path)
    return removed


def net_positions(journal: pd.DataFrame) -> Dict[str, int]:
    """Shares held per ticker. Fully-closed names are dropped."""
    if journal is None or journal.empty:
        return {}
    out: Dict[str, int] = {}
    for _, row in journal.iterrows():
        sign = 1 if str(row["action"]).upper() == "BUY" else -1
        out[row["ticker"]] = out.get(row["ticker"], 0) + sign * int(row["shares"])
    return {t: s for t, s in out.items() if s > 0}


def average_cost(journal: pd.DataFrame) -> Dict[str, float]:
    """
    Average cost per share of the still-open FIFO lots, fees included.

    Used to seed current_holdings.yaml so the brief's P&L matches the journal.
    """
    lots_by_ticker = _open_lots(journal)
    out: Dict[str, float] = {}
    for ticker, lots in lots_by_ticker.items():
        shares = sum(l["shares"] for l in lots)
        if shares <= 0:
            continue
        cost = sum(l["shares"] * l["price"] + l["fee_share"] for l in lots)
        out[ticker] = cost / shares
    return out


def _open_lots(journal: pd.DataFrame) -> Dict[str, List[dict]]:
    """Replay the log FIFO and return the buy lots still open per ticker."""
    queues: Dict[str, deque] = {}
    if journal is None or journal.empty:
        return {}

    df = journal.copy()
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df.sort_values("date", kind="stable")

    for _, row in df.iterrows():
        ticker = row["ticker"]
        shares = int(row["shares"])
        queues.setdefault(ticker, deque())

        if str(row["action"]).upper() == "BUY":
            per_share_fee = (float(row["fee_rp"]) + float(row["stamp_rp"])) / shares if shares else 0.0
            queues[ticker].append({
                "date": row["date"], "shares": shares, "price": float(row["price"]),
                "fee_share": per_share_fee * shares, "per_share_fee": per_share_fee,
                "source": row.get("source", "tool"),
            })
        else:
            remaining = shares
            while remaining > 0 and queues[ticker]:
                lot = queues[ticker][0]
                take = min(remaining, lot["shares"])
                lot["shares"] -= take
                lot["fee_share"] = lot["shares"] * lot["per_share_fee"]
                remaining -= take
                if lot["shares"] == 0:
                    queues[ticker].popleft()
            # A sell with no matching buy means the log is incomplete (a position
            # opened before the journal started). Ignored rather than raising, so
            # a partial history still produces a usable report.

    return {t: [l for l in q if l["shares"] > 0] for t, q in queues.items()}


def closed_trades(journal: pd.DataFrame) -> pd.DataFrame:
    """
    Match sells against buys FIFO and return completed round-trips, net of fees.

    Fees are allocated per share so a partial sell carries only its own share of
    the original buy's cost.
    """
    if journal is None or journal.empty:
        return pd.DataFrame(columns=CLOSED_COLS)

    df = journal.copy()
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df.sort_values("date", kind="stable")

    queues: Dict[str, deque] = {}
    rows: List[dict] = []

    for _, row in df.iterrows():
        ticker = row["ticker"]
        shares = int(row["shares"])
        queues.setdefault(ticker, deque())

        if str(row["action"]).upper() == "BUY":
            per_share_fee = (float(row["fee_rp"]) + float(row["stamp_rp"])) / shares if shares else 0.0
            queues[ticker].append({
                "date": row["date"], "shares": shares, "price": float(row["price"]),
                "per_share_fee": per_share_fee, "source": row.get("source", "tool"),
            })
            continue

        sell_per_share_fee = (float(row["fee_rp"]) + float(row["stamp_rp"])) / shares if shares else 0.0
        remaining = shares

        while remaining > 0 and queues[ticker]:
            lot = queues[ticker][0]
            take = min(remaining, lot["shares"])

            gross = take * (float(row["price"]) - lot["price"])
            fees = take * (lot["per_share_fee"] + sell_per_share_fee)
            cost = take * lot["price"] + take * lot["per_share_fee"]

            rows.append({
                "ticker": ticker,
                "buy_date": lot["date"],
                "sell_date": row["date"],
                "shares": take,
                "buy_price": lot["price"],
                "sell_price": float(row["price"]),
                "gross_pnl": round(gross, 2),
                "fees": round(fees, 2),
                "net_pnl": round(gross - fees, 2),
                "return_pct": round((gross - fees) / cost * 100, 4) if cost else 0.0,
                "holding_days": max(0, (row["date"] - lot["date"]).days),
                # Attribution follows the BUY: the decision being judged is the
                # one that opened the position.
                "source": lot["source"],
            })

            lot["shares"] -= take
            remaining -= take
            if lot["shares"] == 0:
                queues[ticker].popleft()

    return pd.DataFrame(rows, columns=CLOSED_COLS)


# ------------------------------------------------------------------------ marks
def load_marks(path: str | Path) -> pd.DataFrame:
    p = Path(path)
    if not p.exists():
        return pd.DataFrame(columns=MARK_COLS)
    try:
        df = pd.read_csv(p)
    except Exception:
        return pd.DataFrame(columns=MARK_COLS)
    for col in MARK_COLS:
        if col not in df.columns:
            df[col] = pd.NA
    return df[MARK_COLS]


def append_mark(
    positions_value_rp: float,
    cash_rp: float,
    ihsg_close: Optional[float],
    path: str | Path,
    on_date=None,
) -> pd.DataFrame:
    marks = load_marks(path)
    row = {
        "date": pd.to_datetime(on_date or _date.today()).strftime("%Y-%m-%d"),
        "positions_value_rp": round(float(positions_value_rp), 2),
        "cash_rp": round(float(cash_rp), 2),
        "total_rp": round(float(positions_value_rp) + float(cash_rp), 2),
        "ihsg_close": float(ihsg_close) if ihsg_close else None,
    }
    updated = pd.concat([marks, pd.DataFrame([row])], ignore_index=True)
    updated = updated.drop_duplicates(subset="date", keep="last").sort_values("date")

    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    updated.to_csv(p, index=False)
    return updated
