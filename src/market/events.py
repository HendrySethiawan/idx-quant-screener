# src/market/events.py
"""
What is coming up for the names you might trade.

**The coverage problem, stated up front.** `yfinance` has an earnings date for only
16 of the 49 names in this universe (33%), and the gaps include SRTG, TINS, TAPG,
MAPI, ISAT, ITMG and KLBF. So `configs/events.yaml` is not a supplement here — it
is the primary source, with yfinance filling in a third of it.

That coverage gap forces the central design rule: **absence of an event must never
render as absence of risk.** Every ticker resolves to one of three states, and the
third is the one that matters:

    KNOWN    a date exists and falls inside the horizon
    CLEAR    a date exists and falls outside the horizon
    UNKNOWN  no source has anything -- we are blind, and the brief says so

Collapsing UNKNOWN into CLEAR would reproduce exactly the silent-failure pattern
this project has spent four phases removing.

It is also why events warn rather than block. A blocking rule could only fire on
the third of the universe we can see, so it would tilt the book toward the two
thirds we cannot -- an invisible bias. An inconsistently applied filter is worse
than no filter.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set

import pandas as pd
import yaml

from portfolio.journal import normalize_ticker

# Scopes that are not tradeable tickers and must not get a .JK suffix.
MARKET_SCOPES = {"MARKET", "MSCI", "FTSE", "IDX", "BI", "FED", "MACRO"}

KNOWN, CLEAR, UNKNOWN = "known", "clear", "unknown"

_KIND_LABELS = {
    "earnings": "earnings",
    "ex_dividend": "ex-dividend",
    "review": "index review",
    "note": "note",
}

_SOURCE_LABELS = {"auto": "auto", "manual": "you", "estimated": "est."}

# Rewritten on every save. yaml.safe_dump drops comments, and this file is meant to
# be hand-edited as well as CLI-written, so the format documentation has to survive.
_HEADER = """\
# Events you know about that Yahoo Finance does not.
#
# This file carries most of the calendar: yfinance has an earnings date for only
# 16 of the 49 names in the universe, and the gaps include SRTG, TINS, TAPG, MAPI,
# ISAT, ITMG and KLBF. Anything you read on CNBC Indonesia, IDX announcements, or
# an MSCI review notice belongs here.
#
#   python main.py --event ADRO earnings 2026-08-27
#   python main.py --event MSCI review 2026-08-28 --note "Aug index review"
#
# Fields:
#   date   YYYY-MM-DD
#   scope  a ticker (BBRI or BBRI.JK), or a market scope:
#          MARKET, MSCI, FTSE, IDX, BI, FED, MACRO
#   kind   earnings | ex_dividend | review | note   (free-form)
#   note   optional free text
#
# A row that fails to parse is skipped and the rest of the file still loads, so one
# typo cannot blank your calendar.

"""


@dataclass
class Event:
    date: date
    scope: str
    kind: str
    note: str = ""
    source: str = "manual"

    def days_away(self, today: Optional[date] = None) -> int:
        return (self.date - (today or date.today())).days

    @property
    def kind_label(self) -> str:
        return _KIND_LABELS.get(self.kind, self.kind.replace("_", " "))

    @property
    def source_label(self) -> str:
        return _SOURCE_LABELS.get(self.source, self.source)

    def describe(self, today: Optional[date] = None) -> str:
        d = self.days_away(today)
        when = "today" if d == 0 else ("tomorrow" if d == 1 else f"in {d} days")
        text = f"{self.kind_label} {when}"
        return f"{text} (est.)" if self.source == "estimated" else text


def normalize_scope(scope: str) -> str:
    """Tickers get `.JK`; market-wide scopes such as MSCI pass through untouched."""
    s = str(scope).strip().upper()
    if not s:
        raise ValueError("empty scope")
    if s in MARKET_SCOPES:
        return s
    return normalize_ticker(s)


# ------------------------------------------------------------------ manual file
def load_events(path: str | Path) -> List[Event]:
    """
    Read configs/events.yaml. Returns [] on a missing or malformed file, and skips
    individual rows that cannot be parsed rather than discarding the whole file --
    one typo should not silently blank the calendar.
    """
    p = Path(path)
    if not p.exists():
        return []
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except Exception:
        return []

    rows = data.get("events") or []
    if not isinstance(rows, list):
        return []

    out: List[Event] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            when = row["date"]
            when = when if isinstance(when, date) else pd.to_datetime(when).date()
            out.append(Event(
                date=when,
                scope=normalize_scope(row["scope"]),
                kind=str(row.get("kind", "note")).strip().lower(),
                note=str(row.get("note", "") or ""),
                source="manual",
            ))
        except Exception:
            continue
    return out


def add_event(
    scope: str, kind: str, when, path: str | Path, note: str = ""
) -> Event:
    """Append one event to the YAML file, keeping it sorted by date."""
    event = Event(
        date=pd.to_datetime(when).date(),
        scope=normalize_scope(scope),
        kind=str(kind).strip().lower(),
        note=note,
        source="manual",
    )

    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    data = {}
    if p.exists():
        try:
            with open(p, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
        except Exception:
            data = {}

    rows = data.get("events") or []
    if not isinstance(rows, list):
        rows = []
    rows.append({
        "date": event.date.isoformat(), "scope": event.scope,
        "kind": event.kind, "note": event.note,
    })
    rows.sort(key=lambda r: str(r.get("date", "")))
    data["events"] = rows

    with open(p, "w", encoding="utf-8") as f:
        f.write(_HEADER)
        yaml.safe_dump(data, f, sort_keys=False, allow_unicode=True)
    return event


# -------------------------------------------------------------------- automatic
def fetch_auto_events(tickers: Iterable[str], fetcher=None) -> List[Event]:
    """
    Earnings dates from `yfinance` `Ticker.calendar`.

    Every ticker is guarded independently: a failure or an empty calendar leaves
    that name UNKNOWN rather than aborting the batch or implying it is clear.
    """
    import yfinance as yf

    out: List[Event] = []
    for ticker in tickers:
        try:
            cal = yf.Ticker(ticker).calendar
        except Exception:
            continue
        if not isinstance(cal, dict):
            continue
        dates = cal.get("Earnings Date") or []
        if isinstance(dates, (str, date, datetime)):
            dates = [dates]
        for raw in dates:
            try:
                out.append(Event(
                    date=raw if isinstance(raw, date) and not isinstance(raw, datetime)
                    else pd.to_datetime(raw).date(),
                    scope=ticker, kind="earnings", source="auto",
                ))
            except Exception:
                continue
    return out


def load_auto_events(
    tickers: Iterable[str],
    cache_dir: str | Path = "data/cache",
    ttl_hours: int = 24,
) -> List[Event]:
    """
    Cached wrapper around `fetch_auto_events`.

    An uncached call is one network round-trip per ticker, which is 49 of them --
    far too slow for a command meant to run during a lunch break. Earnings dates do
    not move hourly, so a day-long TTL is generous.

    A cache miss that also fails to fetch returns [] rather than raising: the
    coverage logic will then mark those names UNKNOWN, which is the truthful
    outcome when we could not look.
    """
    import joblib

    tickers = list(tickers)
    cache = Path(cache_dir) / "events_auto.pkl"
    cache.parent.mkdir(parents=True, exist_ok=True)

    if cache.exists():
        age_hours = (pd.Timestamp.now().timestamp() - cache.stat().st_mtime) / 3600
        if age_hours < ttl_hours:
            try:
                cached = joblib.load(cache)
                if isinstance(cached, list):
                    return [e for e in cached if e.scope in set(tickers)]
            except Exception:
                pass  # corrupt cache: refetch rather than retry a bad pickle

    try:
        events = fetch_auto_events(tickers)
    except Exception:
        return []

    try:
        joblib.dump(events, cache)
    except Exception:
        pass
    return events


def estimate_ex_dividend(
    ticker: str, dividends: Optional[pd.Series], today: Optional[date] = None
) -> Optional[Event]:
    """
    Project the next likely ex-dividend date from the months this stock has used
    historically.

    This is a *pattern*, not a schedule -- companies move or skip payments. It is
    tagged `source="estimated"` and every renderer marks it `(est.)`. Needs at
    least three prior payments before it will guess at all.
    """
    if dividends is None or len(dividends) < 3:
        return None

    today = today or date.today()
    idx = pd.to_datetime(dividends.index)
    months = pd.Series(idx.month).value_counts()
    if months.empty:
        return None

    # The typical day-of-month for the most-used month, so the estimate is not
    # arbitrarily pinned to the 1st.
    common_month = int(months.idxmax())
    days = [d.day for d in idx if d.month == common_month]
    typical_day = int(pd.Series(days).median()) if days else 15

    year = today.year
    for candidate_year in (year, year + 1):
        try:
            when = date(candidate_year, common_month, min(typical_day, 28))
        except ValueError:
            continue
        if when >= today:
            return Event(when, ticker, "ex_dividend", source="estimated",
                         note=f"based on {len(dividends)} past payments")
    return None


# ------------------------------------------------------------------- assembling
def upcoming(events: Iterable[Event], horizon_days: int, today: Optional[date] = None) -> List[Event]:
    today = today or date.today()
    cutoff = today + timedelta(days=horizon_days)
    return sorted(
        (e for e in events if today <= e.date <= cutoff),
        key=lambda e: (e.date, e.scope),
    )


def by_ticker(events: Iterable[Event]) -> Dict[str, List[Event]]:
    out: Dict[str, List[Event]] = {}
    for e in events:
        out.setdefault(e.scope, []).append(e)
    for v in out.values():
        v.sort(key=lambda e: e.date)
    return out


def earnings_coverage(tickers: Iterable[str], events: Iterable[Event]) -> Set[str]:
    """
    Tickers with NO earnings date from any source.

    This is the honest half of the module: it is what lets the brief distinguish
    "nothing is coming" from "we have no idea".
    """
    known = {e.scope for e in events if e.kind == "earnings"}
    return {t for t in tickers if t not in known}


def state_for(
    ticker: str,
    events_for_ticker: List[Event],
    blind: Set[str],
    horizon_days: int,
    today: Optional[date] = None,
) -> tuple[str, str]:
    """
    Resolve one ticker to (state, message) for display next to its ticket line.
    """
    today = today or date.today()
    near = [e for e in events_for_ticker if 0 <= e.days_away(today) <= horizon_days]

    if near:
        soonest = min(near, key=lambda e: e.date)
        return KNOWN, soonest.describe(today)
    if ticker in blind:
        return UNKNOWN, "no earnings date available - check IDX or CNBC yourself"
    return CLEAR, f"nothing scheduled in the next {horizon_days} days"
