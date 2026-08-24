# src/portfolio/dividends.py
"""
Dividends received, per holding.

**The model ranks on dividend yield and could not see a single rupiah of it
arrive.** `dividend_yield` carries +1.0 weight, among the largest in
`factor_weights`, so the tool actively steers toward high-yield names -- and then
`VALID_ACTIONS` was `("BUY", "SELL")` and nothing anywhere recorded income. Every
realised return was understated by exactly the thing the ranking was chasing, and
the one factor most worth verifying was the one that could not be measured.

A separate file, like `cash.py`, and for the same reason its docstring gives: a
dividend has no fee, no stamp, and no shares, and it must never reach FIFO
matching. Keeping it out of `journal.csv` means `closed_trades`, `_open_lots` and
`unmatched_sell_shares` cannot see it by construction rather than by care.

`amount_rp` is what actually landed in the account -- net of the 10% final tax an
Indonesian individual pays on dividends. Recording the gross would overstate
income by a tenth on the exact factor being validated.
"""
from __future__ import annotations

from datetime import date as _date
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd

from portfolio.journal import normalize_ticker

DIVIDEND_COLS = ["date", "ticker", "amount_rp", "note"]


def load_dividends(path: str | Path) -> pd.DataFrame:
    """Empty, correctly-typed frame when the file is missing or unreadable."""
    p = Path(path)
    if not p.exists():
        return pd.DataFrame(columns=DIVIDEND_COLS)
    try:
        df = pd.read_csv(p)
    except Exception:
        return pd.DataFrame(columns=DIVIDEND_COLS)

    for col in DIVIDEND_COLS:
        if col not in df.columns:
            df[col] = pd.NA
    df = df[DIVIDEND_COLS]
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df["amount_rp"] = pd.to_numeric(df["amount_rp"], errors="coerce")
    return df.dropna(subset=["date", "ticker", "amount_rp"]).reset_index(drop=True)


def save_dividends(df: pd.DataFrame, path: str | Path) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    out = df.copy()
    if "date" in out.columns and len(out):
        out["date"] = pd.to_datetime(out["date"]).dt.strftime("%Y-%m-%d")
    out.to_csv(p, index=False)
    return p


def build_entry(ticker: str, amount: float, on_date=None, note: str = "") -> dict:
    """Validate and shape one row. Raises ValueError on anything unusable."""
    ticker = normalize_ticker(ticker)

    try:
        amount = float(amount)
    except (TypeError, ValueError):
        raise ValueError("Amount must be a number.")
    if amount <= 0:
        # A negative dividend is not a thing. A rights issue or a capital return
        # is a different event, and mislabelling one as income would flatter the
        # very factor this file exists to measure.
        raise ValueError("A dividend must be more than zero.")

    when = pd.to_datetime(on_date).date() if on_date else _date.today()
    return {
        "date": when.strftime("%Y-%m-%d"),
        "ticker": ticker,
        "amount_rp": round(amount, 2),
        "note": str(note or ""),
    }


def append_entry(entry: dict, path: str | Path) -> pd.DataFrame:
    existing = load_dividends(path)
    updated = pd.concat([existing, pd.DataFrame([entry])], ignore_index=True)
    save_dividends(updated, path)
    return updated


def total_received(dividends: Optional[pd.DataFrame]) -> float:
    """Every rupiah of income, across every holding."""
    if dividends is None or dividends.empty:
        return 0.0
    return round(float(pd.to_numeric(
        dividends["amount_rp"], errors="coerce").fillna(0).sum()), 2)


def by_ticker(dividends: Optional[pd.DataFrame]) -> Dict[str, float]:
    """Income per name, so a yield pick can be judged on the yield it paid."""
    if dividends is None or dividends.empty:
        return {}
    grouped = dividends.groupby("ticker")["amount_rp"].sum()
    return {str(t): round(float(v), 2) for t, v in grouped.items()}


def realised_yield(dividends: Optional[pd.DataFrame],
                   cost_by_ticker: Dict[str, float]) -> Dict[str, float]:
    """
    Income as a percentage of what each position actually cost.

    The point of recording any of this: the screener promised a forward yield from
    Yahoo, and this is what the holding paid you. They are allowed to differ, and
    knowing by how much is the only way that factor ever gets audited.
    """
    out: Dict[str, float] = {}
    for ticker, received in by_ticker(dividends).items():
        cost = float(cost_by_ticker.get(ticker) or 0.0)
        if cost > 0:
            out[ticker] = round(received / cost * 100, 2)
    return out


def remove_entry_at(path: str | Path, index: int,
                    expect: Optional[dict] = None) -> dict:
    """
    Remove one row by its position in the date-sorted ledger.

    Mirrors `cash.remove_entry_at`, including the `expect` guard: the caller says
    which row it believes it is removing, and a mismatch is refused rather than
    deleting whatever now sits at that position.

    Returns `{ok, message, removed}`; it never raises.
    """
    dividends = load_dividends(path)
    if dividends.empty:
        return {"ok": False, "message": "There is nothing to remove.", "removed": None}

    df = dividends.copy()
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df.sort_values("date", kind="stable").reset_index(drop=True)

    try:
        index = int(index)
    except (TypeError, ValueError):
        return {"ok": False, "message": "That row is no longer in the ledger.",
                "removed": None}
    if not 0 <= index < len(df):
        return {"ok": False, "message": "That row is no longer in the ledger.",
                "removed": None}

    row = df.loc[index]
    if expect:
        for field, want in expect.items():
            if field not in df.columns or want in (None, ""):
                continue
            have = row[field]
            if field == "date":
                have = pd.to_datetime(have).strftime("%Y-%m-%d")
                want = pd.to_datetime(want).strftime("%Y-%m-%d")
            elif field == "amount_rp":
                if abs(float(have) - float(want)) > 0.01:
                    return {"ok": False, "removed": None, "message":
                            "That row has changed since the page was drawn. "
                            "Rebuild and try again."}
                continue
            if str(have).upper() != str(want).upper():
                return {"ok": False, "removed": None, "message":
                        "That row has changed since the page was drawn. "
                        "Rebuild and try again."}

    save_dividends(df.drop(index=index).reset_index(drop=True), path)
    removed = {
        "date": pd.to_datetime(row["date"]).strftime("%Y-%m-%d"),
        "ticker": str(row["ticker"]),
        "amount_rp": float(row["amount_rp"]),
        "note": "" if pd.isna(row["note"]) else str(row["note"]),
    }
    return {"ok": True, "removed": removed, "message":
            f"Removed {removed['ticker']} dividend of "
            f"Rp{removed['amount_rp']:,.0f}."}


def dividends_path(settings) -> Path:
    account = getattr(settings, "account", None) or {}
    return Path(account.get("dividends_path", "data/dividends.csv"))


def recent(dividends: Optional[pd.DataFrame], limit: int = 40) -> pd.DataFrame:
    """Date-sorted, oldest first -- the order the ledger displays and indexes by."""
    if dividends is None or dividends.empty:
        return pd.DataFrame(columns=DIVIDEND_COLS)
    df = dividends.copy()
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df.sort_values("date", kind="stable").reset_index(drop=True)
    return df.tail(int(limit))


__all__: List[str] = [
    "DIVIDEND_COLS", "load_dividends", "save_dividends", "build_entry",
    "append_entry", "total_received", "by_ticker", "realised_yield",
    "remove_entry_at", "dividends_path", "recent",
]
