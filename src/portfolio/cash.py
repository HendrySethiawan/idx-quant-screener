# src/portfolio/cash.py
"""
Money paid into the account, and money taken out.

**This is where capital comes from.** It used to be a number typed into
`configs/user.yaml`, which meant the amount every recommendation was sized against
lived in one place and the money it described lived in another, with nothing keeping
them honest. Recording a deposit is now the same act as setting capital, so there is
only one number and it has a date and a reason attached.

`net_paid_in` is deposits minus withdrawals: what you have committed to this
account, not what is sitting in it. Cash on hand is that figure plus the signed
effect of every trade, which is what `Performance.cash` already computes from
`starting_capital`. Feeding this in as `starting_capital` therefore needs no change
anywhere downstream -- see `sync_capital`.

Trades stay in `journal.csv` and never appear here. A deposit is not a trade: it has
no ticker, no fee, no stamp, and it must not reach FIFO matching, the round-trip
table, or the IHSG shadow. Keeping the two files apart is what stops it.
"""
from __future__ import annotations

from datetime import date as _date
from pathlib import Path
from typing import Dict, Optional

import pandas as pd

CASH_COLS = ["date", "kind", "amount_rp", "note"]

VALID_KINDS = ("DEPOSIT", "WITHDRAW")


def load_cash(path: str | Path) -> pd.DataFrame:
    """Empty, correctly-typed frame when the file is missing or unreadable."""
    p = Path(path)
    if not p.exists():
        return pd.DataFrame(columns=CASH_COLS)
    try:
        df = pd.read_csv(p)
    except Exception:
        return pd.DataFrame(columns=CASH_COLS)

    for col in CASH_COLS:
        if col not in df.columns:
            df[col] = pd.NA
    df = df[CASH_COLS]
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df["amount_rp"] = pd.to_numeric(df["amount_rp"], errors="coerce")
    return df.dropna(subset=["date", "kind", "amount_rp"]).reset_index(drop=True)


def save_cash(df: pd.DataFrame, path: str | Path) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    out = df.copy()
    if "date" in out.columns and len(out):
        out["date"] = pd.to_datetime(out["date"]).dt.strftime("%Y-%m-%d")
    out.to_csv(p, index=False)
    return p


def build_entry(kind: str, amount: float, on_date=None, note: str = "") -> dict:
    """Validate and shape one row. Raises ValueError on anything unusable."""
    kind = str(kind).upper().strip()
    if kind not in VALID_KINDS:
        raise ValueError(f"kind must be one of {VALID_KINDS}, got {kind!r}")

    try:
        amount = float(amount)
    except (TypeError, ValueError):
        raise ValueError("Amount must be a number.")
    if amount <= 0:
        # The sign is carried by `kind`, never by the number. A negative deposit
        # and a withdrawal would be the same row written two ways, and only one of
        # them would read correctly in the ledger.
        raise ValueError("Amount must be more than zero. Use Withdraw to take money out.")

    when = pd.to_datetime(on_date).date() if on_date else _date.today()
    return {
        "date": when.strftime("%Y-%m-%d"),
        "kind": kind,
        "amount_rp": round(amount, 2),
        "note": str(note or ""),
    }


def append_entry(entry: dict, path: str | Path) -> pd.DataFrame:
    existing = load_cash(path)
    updated = pd.concat([existing, pd.DataFrame([entry])], ignore_index=True)
    save_cash(updated, path)
    return updated


def signed(cash: Optional[pd.DataFrame]) -> pd.Series:
    """Each row's effect on the account: positive in, negative out."""
    if cash is None or cash.empty:
        return pd.Series(dtype="float64")
    amounts = pd.to_numeric(cash["amount_rp"], errors="coerce").fillna(0.0)
    return amounts.where(cash["kind"].str.upper() == "DEPOSIT", -amounts)


def net_paid_in(cash: Optional[pd.DataFrame]) -> float:
    """Deposits minus withdrawals. What this account has been given to work with."""
    if cash is None or cash.empty:
        return 0.0
    return round(float(signed(cash).sum()), 2)


def totals(cash: Optional[pd.DataFrame]) -> Dict[str, float]:
    """Paid in, taken out, and the difference -- for the panel footer."""
    if cash is None or cash.empty:
        return {"deposits": 0.0, "withdrawals": 0.0, "net": 0.0, "entries": 0}
    amounts = pd.to_numeric(cash["amount_rp"], errors="coerce").fillna(0.0)
    is_in = cash["kind"].str.upper() == "DEPOSIT"
    return {
        "deposits": round(float(amounts[is_in].sum()), 2),
        "withdrawals": round(float(amounts[~is_in].sum()), 2),
        "net": net_paid_in(cash),
        "entries": int(len(cash)),
    }


def would_overdraw(cash: Optional[pd.DataFrame], entry: dict) -> bool:
    """
    True when this withdrawal takes total paid-in below zero.

    Not a judgement about whether you can afford it -- that depends on trades and is
    checked separately. This is the arithmetic impossibility: you cannot have taken
    out more than was ever put in, so such a row is a mistake rather than a fact.
    """
    if str(entry.get("kind", "")).upper() != "WITHDRAW":
        return False
    return net_paid_in(cash) - float(entry["amount_rp"]) < -0.005


def remove_entry_at(path: str | Path, index: int,
                    expect: Optional[dict] = None) -> dict:
    """
    Remove one row by its position in the date-sorted ledger.

    Mirrors `journal.remove_trade_at`, including the `expect` guard: the caller says
    which row it believes it is removing, and a mismatch is refused rather than
    deleting whatever now sits at that position. The page may have been rebuilt since
    it was drawn.

    A removal that would make total paid-in negative is refused for the same reason
    the entry itself would be.

    Returns `{ok, message, removed}`; it never raises.
    """
    cash = load_cash(path)
    if cash.empty:
        return {"ok": False, "message": "There is nothing to remove.", "removed": None}

    df = cash.copy()
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

    remaining = df.drop(index=index).reset_index(drop=True)
    if net_paid_in(remaining) < -0.005:
        return {"ok": False, "removed": None, "message":
                "Removing this deposit would leave more withdrawn than was ever "
                "paid in. Remove the withdrawal that depends on it first."}

    save_cash(remaining, path)
    removed = {
        "date": pd.to_datetime(row["date"]).strftime("%Y-%m-%d"),
        "kind": str(row["kind"]).upper(),
        "amount_rp": float(row["amount_rp"]),
        "note": "" if pd.isna(row["note"]) else str(row["note"]),
    }
    return {"ok": True, "removed": removed, "message":
            f"Removed {removed['kind'].lower()} of Rp{removed['amount_rp']:,.0f}."}


# ------------------------------------------------------------------- capital
def cash_path(settings) -> Path:
    account = getattr(settings, "account", None) or {}
    return Path(account.get("cash_path", "data/cash.csv"))


def sync_capital(settings) -> Optional[float]:
    """
    Point `settings.capital_rp` at the cash ledger, when there is one.

    `Settings.capital_rp` reads `account["capital_rp"]`, and every consumer in the
    project goes through that property -- sizing, `assemble`, `estimate_fees`,
    `build_performance`, the backtest, the what-if grid. Writing the derived figure
    into that one key therefore reaches all of them without touching a signature.

    An empty or absent ledger changes nothing, so the existing fallback chain
    (user.yaml, then the shipped placeholder) still applies to anyone who has not
    recorded a deposit yet.

    Returns the figure applied, or None if the ledger had nothing to say.
    """
    ledger = load_cash(cash_path(settings))
    if ledger.empty:
        return None
    total = net_paid_in(ledger)
    account = dict(getattr(settings, "account", None) or {})
    account["capital_rp"] = total
    settings.account = account
    return total


def recent(cash: Optional[pd.DataFrame], limit: int = 40) -> pd.DataFrame:
    """Date-sorted, oldest first -- the order the ledger displays and indexes by."""
    if cash is None or cash.empty:
        return pd.DataFrame(columns=CASH_COLS)
    df = cash.copy()
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df.sort_values("date", kind="stable").reset_index(drop=True)
    return df.tail(int(limit))
