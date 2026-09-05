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

# How long an index review goes on mattering after it takes effect. Passive money
# does not rebalance in a day and the liquidity a deleted name loses does not come
# back, so a review is the one event whose *past* is still a live fact about the
# order book. Three weeks is a judgement, not a measurement -- MSCI reviews run
# quarterly, so it covers the flow without ever overlapping the next one.
REVIEW_LOOKBACK_DAYS = 21

_KIND_LABELS = {
    "earnings": "earnings",
    "ex_dividend": "ex-dividend",
    "review": "index review",
    "note": "note",
}

_SOURCE_LABELS = {"auto": "auto", "manual": "you", "estimated": "est.",
                  "shipped": "built in"}

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
#   python main.py --event BREN ex_dividend 2026-09-18
#
# Index review dates ship with the build (see `market_calendar` in
# configs/default.yaml) and arrive here automatically -- you do not need to copy
# them in. Recording one yourself overrides the shipped row for the same date,
# scope and kind, which is how you correct one you have checked.
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
        if d < 0:
            n = -d
            when = "yesterday" if n == 1 else f"{n} days ago"
        else:
            when = "today" if d == 0 else ("tomorrow" if d == 1 else f"in {d} days")
        text = f"{self.kind_label} {when}"
        # For earnings, *when* is the whole message. For a review it is the least
        # interesting part -- "index review 4 days ago" on a ticket line tells you
        # something happened and not one thing you could act on, where "dropped
        # from MSCI Global Standard" is the entire point of having recorded it.
        if self.kind == "review" and self.note:
            text = f"{text} - {self.note}"
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


def load_calendar(rows) -> List[Event]:
    """
    The index calendar that ships with the build, from `default.yaml`.

    Separate from `events.yaml` on purpose, and the split is the same one
    `core.paths` draws between app-owned and user-owned files. An MSCI review date
    is a fact about the market: identical for every reader, and wrong the moment it
    goes stale. `events.yaml` is the reader's own notebook, git-ignored, never
    overwritten. Seeding the notebook with these dates would have frozen them at
    install -- which is precisely how a build shipping 74 tickers went on analysing
    the 49 seeded at first install.

    Labelled `shipped` so the Events panel says "built in" rather than letting the
    reader think they recorded it themselves.
    """
    out: List[Event] = []
    for row in rows or []:
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
                source="shipped",
            ))
        except Exception:
            continue
    return out


def merge_events(shipped: List[Event], manual: List[Event]) -> List[Event]:
    """
    Both calendars, with the reader's own row winning any collision.

    Same date, scope and kind means the same event. If they have recorded it
    themselves -- with their own note, or a corrected date -- that is the one they
    will trust, and a shipped duplicate beside it reads as the tool disagreeing
    with them about something they already checked.
    """
    seen = {(e.date, e.scope, e.kind) for e in manual}
    return manual + [e for e in shipped if (e.date, e.scope, e.kind) not in seen]


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
    review_lookback_days: int = REVIEW_LOOKBACK_DAYS,
) -> tuple[str, str]:
    """
    Resolve one ticker to (state, message) for display next to its ticket line.

    Forward-looking for every kind but one. An earnings date that has passed is
    news the price has already had; an index review that has passed is a flow that
    is still draining, and the name goes on being suggested with nothing to say it
    just lost its passive bid. Rendering that identically to "nothing is happening"
    is the same silent failure this module exists to refuse, so `review` events
    stay attached for `review_lookback_days` after their effective date.

    Only `review`. A blanket lookback would put every stale earnings date back on
    the ticket, which is noise, not risk.
    """
    today = today or date.today()
    near, past = [], []
    for e in events_for_ticker:
        away = e.days_away(today)
        if 0 <= away <= horizon_days:
            near.append(e)
        elif e.kind == "review" and -review_lookback_days <= away < 0:
            past.append(e)

    if near:
        soonest = min(near, key=lambda e: e.date)
        return KNOWN, soonest.describe(today)
    # Most recent first: two reviews inside the window means the later one is the
    # one still moving the stock.
    if past:
        return KNOWN, max(past, key=lambda e: e.date).describe(today)
    if ticker in blind:
        return UNKNOWN, "no earnings date available - check IDX or CNBC yourself"
    return CLEAR, f"nothing scheduled in the next {horizon_days} days"
