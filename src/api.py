# src/api.py
"""
The bridge between the page and Python.

pywebview exposes an object to the window as `window.pywebview.api`, so the terminal
can record a trade without a web server, a port, or a second copy of any rule.

Two rules this module exists to keep.

**One fee implementation.** `preview_trade` calls the same `build_trade` that
`log_trade` calls, against the same journal file. That is not tidiness: the stamp is
Rp10,000 only on the *first* sell of a day, so a preview written in JavaScript would
quote Rp10,000 on a second sell and then record Rp0, and the reader would find out
by reconciling against their broker weeks later.

**Nothing raises across the bridge.** Every method returns `{ok, message, data}`. An
exception here surfaces in JavaScript as a rejected promise with no message, which
on screen looks like a button that does nothing.

This records what already happened in Indopremier. It places no orders and has no
route to a broker.
"""
from __future__ import annotations

import re
import traceback
from datetime import date as _date
from pathlib import Path
from typing import Any, Callable, Dict, Optional

import pandas as pd


# The run context lives here, NOT on the API instance.
#
# pywebview walks the js_api object's attributes to expose it to JavaScript, and a
# pandas DataFrame defeats that: `df.T` returns a DataFrame, so the walk follows
# `df.T.T.T...` until it gives up, spraying "The '.style' accessor requires jinja2"
# across stderr on every launch. Anything holding a DataFrame must stay off the
# object that crosses the bridge.
_CONTEXT: Dict[str, Any] = {"ctx": None}


def set_context(ctx) -> None:
    _CONTEXT["ctx"] = ctx


def get_context():
    return _CONTEXT["ctx"]


def _fail(message: str, **extra) -> Dict[str, Any]:
    return {"ok": False, "message": str(message), "data": extra or None}


def _ok(message: str = "", **data) -> Dict[str, Any]:
    return {"ok": True, "message": message, "data": data or None}


def guarded(fn: Callable) -> Callable:
    """
    Turn any exception into a readable result.

    Broad on purpose. The alternative to catching everything here is a silent button,
    and a stack trace the reader cannot see is worth less than one sentence they can.
    """
    def wrapper(self, *args, **kwargs):
        try:
            return fn(self, *args, **kwargs)
        except ValueError as e:
            return _fail(str(e))
        except Exception as e:                      # noqa: BLE001 - see docstring
            if getattr(self, "_logger", None):
                self._logger.warning(f"{fn.__name__} failed: {e}\n{traceback.format_exc()}")
            return _fail(f"{type(e).__name__}: {e}")
    wrapper.__name__ = fn.__name__
    wrapper.__doc__ = fn.__doc__
    return wrapper


class TerminalAPI:
    """
    Exposed to the page. Every public method is callable as
    `window.pywebview.api.<name>(...)` and returns a JSON-serialisable dict.
    """

    # Every attribute here is underscore-prefixed on purpose. pywebview builds the
    # JS object by walking `dir(obj)`: it skips names starting with "_", exposes
    # methods, and RECURSES INTO any other public attribute. A public `ctx` therefore
    # sent it into `ctx.df.T.T.T...` forever -- that is the bug this shape prevents.
    # Nothing on this object should be public except the methods JavaScript calls.
    def __init__(self, settings, prices: Optional[Dict[str, float]] = None, logger=None,
                 ctx=None):
        self._settings = settings
        self._prices = dict(prices or {})
        self._logger = logger
        if ctx is not None:
            set_context(ctx)

    @property
    def _ctx(self):
        return get_context()

    # ------------------------------------------------------------------ refresh
    #
    # Nothing here navigates the window. pywebview delivers a call's reply with
    # `window.evaluate_js(...)` placed OUTSIDE its own try/except, and that call
    # waits up to 20 seconds for the page to be `loaded`. Calling `load_url` from
    # inside an API call therefore navigates the page out from under the reply: the
    # wait times out, `WebViewException('Main window failed to start')` is raised on
    # pywebview's thread, and the JS promise never settles -- which left Rebuild and
    # Re-run disabled forever and the trade form dead after one use.
    #
    # So the URL is returned and the page navigates itself, once the reply is in.
    @staticmethod
    def _url_for(path) -> str:
        return Path(path).resolve().as_uri()

    @guarded
    def rebuild(self) -> Dict[str, Any]:
        """
        Redraw from what is already in memory. No network.

        This is the one you want after recording a trade or changing a setting: the
        holdings, the ledger and the ticket all move, and the market has not.
        """
        ctx = self._ctx
        if ctx is None:
            return _fail("Nothing to rebuild from - restart the app.")
        from runner import render
        self._prices = dict(ctx.plan["prices"]) if ctx.plan else self._prices
        path = render(ctx)
        return _ok("Rebuilt.", path=str(path), url=self._url_for(path))

    @guarded
    def rerun(self) -> Dict[str, Any]:
        """Fetch everything again and re-rank. Slow, and says so on the button."""
        ctx = self._ctx
        if ctx is None:
            return _fail("Nothing to re-run - restart the app.")
        from runner import full_run, render
        if self._logger:
            self._logger.info("Re-running the screen from the window")
        fresh = full_run(self._settings, ctx.args, self._logger)
        path = render(fresh)
        set_context(fresh)
        self._prices = dict(fresh.plan["prices"]) if fresh.plan else {}
        return _ok("Re-ran the screen.", path=str(path), url=self._url_for(path))

    # ------------------------------------------------------------------ paths
    def _paths(self):
        from cli import _paths
        return _paths(self._settings)

    def _fee_cfg(self):
        from portfolio.fees import FeeConfig
        return FeeConfig.from_settings(self._settings)

    def _journal(self) -> pd.DataFrame:
        from portfolio.journal import load_journal
        journal_path, _, _ = self._paths()
        return load_journal(journal_path)

    # ------------------------------------------------------------------ trades
    @guarded
    def preview_trade(self, action: str, ticker: str, lots: Any, price: Any,
                      on_date: str = "") -> Dict[str, Any]:
        """
        Cost a trade without recording it.

        Runs the real builder against the real journal, so the stamp reflects
        whether this would be the first sell of that day.
        """
        from portfolio.journal import build_trade

        trade = build_trade(
            action=action, ticker=ticker,
            lots=self._as_int(lots, "Lots"), price=self._as_float(price, "Price"),
            cfg=self._fee_cfg(), journal=self._journal(),
            on_date=on_date or None,
        )
        warning = self._sell_warning(trade)
        return _ok(warning, **trade)

    @guarded
    def log_trade(self, action: str, ticker: str, lots: Any, price: Any,
                  on_date: str = "", note: str = "", source: str = "tool") -> Dict[str, Any]:
        """Record it, rewrite the holdings file, and hand back fresh panels."""
        from cli import _sync_holdings
        from portfolio.journal import append_trade, build_trade

        journal_path, _, holdings_path = self._paths()
        trade = build_trade(
            action=action, ticker=ticker,
            lots=self._as_int(lots, "Lots"), price=self._as_float(price, "Price"),
            cfg=self._fee_cfg(), journal=self._journal(),
            on_date=on_date or None, note=note or "",
            source=(source or "tool").lower(),
        )

        # Refused, not warned. Selling shares you do not hold is a typo often enough
        # that recording it would corrupt the FIFO matching for every later trade in
        # that name -- and matching the warning by its wording, as an earlier version
        # did, silently stopped refusing anything the moment the wording changed.
        if self._oversell(trade):
            return _fail(self._sell_warning(trade), **trade)

        journal = append_trade(trade, journal_path)
        try:
            _sync_holdings(journal, holdings_path, self._settings.lot_size)
        except Exception as e:                       # holdings are derived, not the record
            if self._logger:
                self._logger.warning(f"Could not rewrite holdings: {e}")

        # Rebuild the whole page rather than only swapping the ledger: the KPIs and
        # the top-bar chips are rendered from `perf` at build time, so a ledger that
        # visibly gained a trade sat next to P&L, fees and closed-trades all reading
        # zero. If the rebuild is not possible, the fresh ledger still goes back.
        message = (f"Recorded {trade['action']} {trade['lots']} lot {trade['ticker']} "
                   f"at Rp{trade['price']:,.0f}.")
        rebuilt = self.rebuild()
        if rebuilt["ok"]:
            return _ok(message, trade=trade, url=rebuilt["data"]["url"])
        return _ok(message, trade=trade, journal_html=self._journal_html())

    @guarded
    def last_trade(self) -> Dict[str, Any]:
        """What "remove last trade" would remove, so it can be named before it goes."""
        journal = self._journal()
        if journal.empty:
            return _ok("Nothing recorded yet.")
        row = journal.sort_values("date", kind="stable").iloc[-1]
        when = pd.to_datetime(row["date"], errors="coerce")
        return _ok(
            f"{row['action']} {int(row['lots'])} lot {row['ticker']} at "
            f"Rp{float(row['price']):,.0f}"
            + (f" on {when:%d %b %Y}" if pd.notna(when) else ""),
            ticker=str(row["ticker"]), action=str(row["action"]),
        )

    @guarded
    def remove_last_trade(self) -> Dict[str, Any]:
        """
        Undo the most recent entry.

        A trade typed at the wrong price is otherwise permanent, and the ledger
        carries the nonsense forever -- a position recorded at Rp125 instead of
        Rp1,915 shows +1429% and nothing can be done about it.
        """
        from cli import _sync_holdings
        from portfolio.journal import load_journal, remove_last_trade

        journal_path, _, holdings_path = self._paths()
        removed = remove_last_trade(journal_path)
        if removed is None:
            return _fail("There is nothing to remove.")

        try:
            _sync_holdings(load_journal(journal_path), holdings_path,
                           self._settings.lot_size)
        except Exception as e:
            if self._logger:
                self._logger.warning(f"Could not rewrite holdings: {e}")

        message = (f"Removed {removed['action']} {int(removed['lots'])} lot "
                   f"{removed['ticker']} at Rp{float(removed['price']):,.0f}.")
        rebuilt = self.rebuild()
        if rebuilt["ok"]:
            return _ok(message, url=rebuilt["data"]["url"])
        return _ok(message, journal_html=self._journal_html())

    def _held(self, ticker: str) -> int:
        from portfolio.journal import net_positions
        return int(net_positions(self._journal()).get(ticker, 0))

    def _oversell(self, trade: dict) -> bool:
        return (trade["action"] == "SELL"
                and int(trade["shares"]) > self._held(trade["ticker"]))

    def _sell_warning(self, trade: dict) -> str:
        """A sell of shares that are not held is almost always a typo."""
        if not self._oversell(trade):
            return ""
        held = self._held(trade["ticker"])
        return (f"You hold {held // 100} lot of {trade['ticker']}, which is fewer "
                f"than the {trade['lots']} being sold. Nothing was recorded.")

    # ------------------------------------------------------------------- other
    @guarded
    def snapshot(self) -> Dict[str, Any]:
        """Record today's portfolio value against IHSG, the way --mark does."""
        from cli import cmd_mark
        code = cmd_mark(self._settings, logger=self._logger)
        if code != 0:
            return _fail("Could not take a snapshot; see the console for why.")
        return _ok("Snapshot recorded.", journal_html=self._journal_html())

    @guarded
    def add_event(self, scope: str, kind: str, when: str, note: str = "") -> Dict[str, Any]:
        from market.events import add_event as _add
        if not scope or not kind or not when:
            return _fail("An event needs a ticker or scope, a kind, and a date.")
        event = _add(scope, kind, when, self._settings.events_path, note or "")
        return _ok(f"Noted {event.scope} {event.kind} on {event.date:%d %b %Y}.")

    # ---------------------------------------------------------------- settings
    # Editable fields, as (dotted path, label, kind). Anything not listed here is
    # not reachable from the UI -- a settings screen that can reach everything is a
    # settings screen that can break anything.
    _EDITABLE = (
        ("account.capital_rp", "Capital", "int"),
        ("account.min_positions", "Fewest positions", "int"),
        ("account.max_positions", "Most positions", "int"),
        ("account.min_position_rp", "Smallest position", "int"),
        ("max_per_sector", "Max names per sector", "int"),
        ("top_picks_n", "Shortlist size", "int"),
        ("broker.buy_fee", "Buy fee", "float"),
        ("broker.sell_fee", "Sell fee", "float"),
        ("broker.stamp_duty_rp", "Stamp duty", "int"),
        ("liquidity.min_median_daily_value_rp", "Liquidity floor", "int"),
        ("event_horizon_days", "Event horizon (days)", "int"),
    )

    # Changing these changes the ranking, which the page in front of you was built
    # from. It cannot be re-ranked without another run, so the UI says so.
    _RERANK = ("max_per_sector", "top_picks_n", "liquidity.min_median_daily_value_rp")

    @staticmethod
    def _dig(obj, dotted: str):
        node = obj
        for part in dotted.split("."):
            node = node.get(part) if isinstance(node, dict) else getattr(node, part, None)
            if node is None:
                return None
        return node

    @guarded
    def get_settings(self) -> Dict[str, Any]:
        """Current value beside the shipped default, so an override is visible."""
        from core.config import Settings

        defaults = Settings()
        fields = []
        for dotted, label, kind in self._EDITABLE:
            current = self._dig(self._settings, dotted)
            default = self._dig(defaults, dotted)
            fields.append({
                "path": dotted, "label": label, "kind": kind,
                "value": current, "default": default,
                "overridden": current != default,
                "reranks": dotted in self._RERANK,
            })
        return _ok(fields=fields)

    @guarded
    def save_setting(self, dotted: str, raw: Any) -> Dict[str, Any]:
        """
        Write one override to configs/user.yaml, never to default.yaml.

        Validated by building a `Settings` first: a value that cannot load is
        refused here, rather than written and discovered on the next launch when the
        app will not start.
        """
        allowed = {p: k for p, _, k in self._EDITABLE}
        if dotted not in allowed:
            return _fail(f"{dotted} is not editable from here.")

        value = self._coerce(raw, allowed[dotted], dotted)
        parts = dotted.split(".")
        payload: Dict[str, Any] = {}
        node = payload
        for part in parts[:-1]:
            node[part] = {}
            node = node[part]
        node[parts[-1]] = value

        from core.config import Settings, _apply_overrides, save_user_overrides
        probe = Settings()
        _apply_overrides(probe, payload)
        if self._dig(probe, dotted) != value:
            return _fail(f"{value!r} was not accepted for {dotted}.")

        path = save_user_overrides(payload)
        _apply_overrides(self._settings, payload)
        note = (" The ranking on screen was built before this change - re-run to see "
                "its effect." if dotted in self._RERANK else "")
        return _ok(f"Saved to {Path(path).as_posix()}.{note}", path=str(path), value=value)

    # Capital has no sensible default -- the shipped value is a placeholder that
    # produces tickets sized to money nobody has. "Reset" on it would quietly put the
    # reader back to a Rp30 juta ticket on a Rp10 juta account, which is the exact
    # failure the first-run prompt exists to prevent.
    _NO_RESET = ("account.capital_rp",)

    @guarded
    def reset_setting(self, dotted: str) -> Dict[str, Any]:
        """Delete the override so the value follows default.yaml again."""
        if dotted not in {p for p, _, _ in self._EDITABLE}:
            return _fail(f"{dotted} is not editable from here.")
        if dotted in self._NO_RESET:
            return _fail("Capital has no default to fall back to - the shipped figure "
                         "is a placeholder. Type the number you want instead.")
        from core.config import drop_user_override
        drop_user_override(dotted)
        return _ok("Back to the default. Re-run to pick it up.")

    # ------------------------------------------------------------------ helpers
    # Indonesian notation groups thousands with dots: 10.000.000 is ten million.
    # Stripping every dot would also make "12.5.6" a valid number, so only genuine
    # grouping is stripped and anything else is refused.
    _GROUPED = re.compile(r"^\d{1,3}(\.\d{3})+$")

    @classmethod
    def _clean(cls, value) -> str:
        text = str(value).strip().replace(",", "").replace(" ", "")
        return text.replace(".", "") if cls._GROUPED.match(text) else text

    @classmethod
    def _as_int(cls, value, label: str) -> int:
        text = cls._clean(value)
        try:
            out = int(text)
        except (TypeError, ValueError):
            raise ValueError(f"{label} must be a whole number, not {value!r}.")
        return out

    @classmethod
    def _as_float(cls, value, label: str) -> float:
        try:
            return float(cls._clean(value))
        except (TypeError, ValueError):
            raise ValueError(f"{label} must be a number, not {value!r}.")

    def _coerce(self, raw, kind: str, dotted: str):
        if kind == "int":
            return self._as_int(raw, dotted)
        if kind == "float":
            return self._as_float(raw, dotted)
        return raw

    @guarded
    def refresh_journal(self) -> Dict[str, Any]:
        return _ok(journal_html=self._journal_html())

    def _journal_html(self) -> str:
        """
        Rebuild only the panels that depend on the journal alone.

        Portfolio value and unrealised P&L need live prices, so they carry whatever
        the last run fetched and are labelled as such. Realised profit, the monthly
        table and the round-trip list need no prices at all and are exact.
        """
        from report.journal_view import journal_panels
        return journal_panels(self._settings, prices=self._prices)
