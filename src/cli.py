# src/cli.py
"""
Command handlers for --log, --mark and --journal.

Kept out of __main__.py so the screener path and the journal path can be tested
independently, and so `--log` never needs to touch the network.
"""
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Dict, Optional

import pandas as pd

from portfolio import journal as J
from portfolio.fees import FeeConfig
from portfolio.holdings import Holding, save_holdings
from portfolio.performance import evaluate
from report.brief import rp
from report.journal_view import console_block


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="idx-screener",
        description="IDX screener: run the daily brief, or log and review trades.",
    )
    p.add_argument(
        "--log", nargs=4, metavar=("ACTION", "TICKER", "LOTS", "PRICE"),
        help="Record an executed trade, e.g. --log BUY BBRI 3 4150",
    )
    p.add_argument("--date", help="Trade date as YYYY-MM-DD (default: today)")
    p.add_argument("--note", default="", help="Free-text note on the trade")
    p.add_argument(
        "--source", choices=("tool", "own"), default="tool",
        help="Did the screener suggest this trade, or was it your own call?",
    )
    p.add_argument("--journal", action="store_true", help="Show the performance report")
    p.add_argument("--mark", action="store_true", help="Snapshot portfolio value vs IHSG")
    p.add_argument(
        "--event", nargs=3, metavar=("SCOPE", "KIND", "DATE"),
        help="Record an event, e.g. --event ADRO earnings 2026-08-27. "
             "SCOPE may be a ticker or a market scope (MSCI, IDX, BI, FED, MARKET).",
    )
    p.add_argument("--backtest", action="store_true",
                   help="Run the historical simulation of the price factors")
    p.add_argument("--events", action="store_true",
                   help="List upcoming events and which names have no earnings data")
    p.add_argument("--png", action="store_true",
                   help="Also write the matplotlib screener_analysis.png "
                        "(the brief's Advanced view replaces it)")
    p.add_argument("--audit-prices", action="store_true",
                   help="Check every ticker's price against its own series and flag "
                        "anything valued on a stale session")
    p.add_argument("--browser", action="store_true",
                   help="Open the brief in your browser instead of a native window")
    p.add_argument("--refresh", action="store_true",
                   help="Fetch fresh data instead of reopening the last screen. "
                        "The app normally opens from what it already has; this is "
                        "the same thing the Update data button does.")
    return p


def _paths(settings):
    account = getattr(settings, "account", None) or {}
    journal_path = account.get("journal_path", "data/journal.csv")
    marks_path = account.get("marks_path", "data/journal_marks.csv")
    holdings_path = account.get("holdings_path", "current_holdings.yaml")
    return Path(journal_path), Path(marks_path), Path(holdings_path)


def _sync_holdings(journal: pd.DataFrame, holdings_path: Path, lot_size: int) -> None:
    """
    Rewrite current_holdings.yaml from the journal.

    The reliable failure mode in a manual workflow is holdings drifting out of sync
    with the trade log, after which the brief diffs against a portfolio the user no
    longer owns. Deriving one from the other removes the chance to forget.
    """
    positions = J.net_positions(journal)
    costs = J.average_cost(journal)
    holdings = [
        Holding(ticker, lots=shares // lot_size, avg_price=costs.get(ticker), lot_size=lot_size)
        for ticker, shares in sorted(positions.items())
    ]
    save_holdings(holdings, holdings_path)


def cmd_log(settings, args, logger=None) -> int:
    journal_path, _, holdings_path = _paths(settings)
    cfg = FeeConfig.from_settings(settings)

    action, ticker, lots, price = args.log
    try:
        trade = J.build_trade(
            action=action, ticker=ticker, lots=int(lots), price=float(price),
            cfg=cfg, journal=J.load_journal(journal_path),
            on_date=args.date, source=args.source, note=args.note,
        )
    except (ValueError, TypeError) as e:
        print(f"Could not log that trade: {e}")
        return 1

    journal = J.append_trade(trade, journal_path)
    _sync_holdings(journal, holdings_path, cfg.lot_size)

    print(f"\n  logged  {trade['action']:<4} {trade['ticker']:<9} "
          f"{trade['lots']} lot ({trade['shares']:,} shares) @ {rp(trade['price'])}")
    print(f"          value {rp(trade['gross_rp'])}   fee {rp(trade['fee_rp'])}", end="")
    if trade["stamp_rp"]:
        print(f" + stamp {rp(trade['stamp_rp'])}")
    elif trade["action"] == "SELL":
        print("\n          stamp already paid today - no extra charge")
    else:
        print()
    print(f"          cash impact {rp(trade['net_rp'])}")
    print(f"\n  Check this against your Indopremier confirmation before trusting it.")
    print(f"  {holdings_path} updated to match the journal.\n")
    return 0


def collect_events(settings, include_auto: bool = True):
    """
    Manual + automatic events, plus the set of tickers we have no earnings data for.

    Returns (events, blind). `blind` is what lets the brief say "we don't know"
    instead of implying a name is clear.
    """
    from market import events as E

    tickers = list(settings.stock_tickers)
    manual = E.load_events(getattr(settings, "events_path", "configs/events.yaml"))
    # Index dates that ship with the build. Merged rather than seeded into the
    # reader's file, so a new build corrects them and a hand-recorded row still
    # wins over a shipped one.
    shipped = E.load_calendar(getattr(settings, "market_calendar", []))
    auto = E.load_auto_events(tickers) if include_auto else []

    all_events = E.merge_events(shipped, manual) + auto
    blind = E.earnings_coverage(tickers, all_events)
    return all_events, blind


def cmd_event(settings, args, logger=None) -> int:
    from market import events as E

    scope, kind, when = args.event
    path = getattr(settings, "events_path", "configs/events.yaml")
    try:
        event = E.add_event(scope, kind, when, path, note=args.note)
    except (ValueError, TypeError) as e:
        print(f"Could not add that event: {e}")
        return 1

    print(f"\n  added  {event.scope:<9} {event.kind_label:<13} "
          f"{event.date.strftime('%d %b %Y')}  ({event.describe()})")
    if event.note:
        print(f"         {event.note}")
    print(f"         saved to {path}\n")
    return 0


def cmd_events(settings, logger=None) -> int:
    from market import events as E

    horizon = int(getattr(settings, "event_horizon_days", 14))
    all_events, blind = collect_events(settings)
    near = E.upcoming(all_events, horizon)

    print(f"\nEVENTS IN THE NEXT {horizon} DAYS")
    print("=" * 58)
    if not near:
        print("  Nothing scheduled that we know about.")
    for e in near:
        print(f"  {e.date.strftime('%d %b'):<7} {e.scope:<9} {e.kind_label:<13} "
              f"{e.describe():<22} ({e.source_label})")

    total = len(settings.stock_tickers)
    print()
    print(f"  COVERAGE: {total - len(blind)} of {total} names have an earnings date.")
    if blind:
        # Printed in full on purpose. These are the names where a quiet blank would
        # otherwise read as "nothing coming".
        print(f"  No earnings date for {len(blind)} names - check these yourself:")
        for i in range(0, len(sorted(blind)), 8):
            print("    " + " ".join(sorted(blind)[i:i + 8]))
    print("=" * 58 + "\n")
    return 0


def _latest_prices(settings, tickers) -> Dict[str, float]:
    """Last close per ticker. Cache-first, so this is usually offline."""
    if not tickers:
        return {}
    from fetchers.data_fetcher import DataFetcher
    data = DataFetcher(settings).fetch_technical_data(list(tickers))
    out = {}
    for ticker, frame in data.items():
        if frame is not None and "Close" in frame and len(frame["Close"].dropna()):
            out[ticker] = float(frame["Close"].dropna().iloc[-1])
    return out


def _ihsg_series(settings) -> Optional[pd.Series]:
    from fetchers.data_fetcher import DataFetcher
    bench = next(iter(getattr(settings, "benchmarks", {}) or {"^JKSE": ""}), "^JKSE")
    data = DataFetcher(settings).fetch_technical_data([bench])
    frame = data.get(bench)
    return frame["Close"] if frame is not None and "Close" in frame else None


def build_performance(settings, prices=None, ihsg=None, watchlist=None):
    journal_path, _, _ = _paths(settings)
    cfg = FeeConfig.from_settings(settings)

    journal = J.load_journal(journal_path)
    closed = J.closed_trades(journal)
    positions = J.net_positions(journal)
    open_cost = J.average_cost(journal)

    if prices is None:
        prices = _latest_prices(settings, positions.keys())
    if ihsg is None and not journal.empty:
        ihsg = _ihsg_series(settings)

    from portfolio.dividends import dividends_path, load_dividends

    account = getattr(settings, "account", None) or {}
    return evaluate(
        journal=journal, closed=closed, positions=positions, prices=prices,
        open_cost=open_cost, starting_capital=settings.capital_rp, cfg=cfg,
        ihsg_close=ihsg,
        min_trades_for_verdict=int(account.get("min_trades_for_verdict", 30)),
        dividends=load_dividends(dividends_path(settings)),
        watchlist_close=watchlist,
    )


def cmd_journal(settings, logger=None) -> int:
    print(console_block(build_performance(settings)))
    return 0


def cmd_mark(settings, on_date=None, logger=None) -> int:
    journal_path, marks_path, _ = _paths(settings)
    journal = J.load_journal(journal_path)
    positions = J.net_positions(journal)

    prices = _latest_prices(settings, positions.keys())
    position_value = sum(shares * prices.get(t, 0.0) for t, shares in positions.items())
    cash = settings.capital_rp + float(journal["net_rp"].fillna(0).sum() if not journal.empty else 0)

    ihsg = _ihsg_series(settings)
    ihsg_close = float(ihsg.dropna().iloc[-1]) if ihsg is not None and len(ihsg.dropna()) else None

    J.append_mark(position_value, cash, ihsg_close, marks_path, on_date=on_date)
    print(f"\n  marked  positions {rp(position_value)}  cash {rp(cash)}  "
          f"total {rp(position_value + cash)}")
    print(f"          saved to {marks_path}\n")
    return 0


def cmd_backtest(settings, logger=None) -> int:
    """Run every cadence, write one HTML page, print each to the console."""
    from backtest import report as R
    from backtest.engine import (BacktestConfig, build_atr_panel, build_price_panel,
                                 run_backtest)
    from fetchers.data_fetcher import DataFetcher
    from portfolio.exits import ExitConfig

    bt = getattr(settings, "backtest", None) or {}
    period = bt.get("history_period", "5y")
    fetcher = DataFetcher(settings)

    print(f"\n  Fetching {period} of history for {len(settings.stock_tickers)} tickers...")
    price_data = fetcher.fetch_technical_data(settings.stock_tickers, period=period)
    panel = build_price_panel(price_data)
    if panel.empty:
        print("  No price data available.")
        return 1

    # Built from the full OHLC frames with the live indicator, not from the Close
    # panel: a close-to-close range understates a real one, and a stop derived from
    # it would be measuring a tighter rule than the terminal actually sets.
    exit_cfg = ExitConfig.from_settings(settings)
    atr_panel = build_atr_panel(price_data, exit_cfg.atr_window)

    regime_cfg = getattr(settings, "regime", None) or {}
    bench_ticker = regime_cfg.get("benchmark", "^JKSE")
    fx_ticker = regime_cfg.get("fx_ticker", "IDR=X")
    extra = fetcher.fetch_technical_data([bench_ticker, fx_ticker], period=period)

    def close_of(t):
        f = extra.get(t)
        if f is None or "Close" not in f:
            return None
        c = f["Close"].dropna()
        c.index = pd.to_datetime(c.index).tz_localize(None)
        return c

    benchmark, fx = close_of(bench_ticker), close_of(fx_ticker)
    fee_cfg = FeeConfig.from_settings(settings)
    account = getattr(settings, "account", None) or {}

    sections = {}
    for rule in bt.get("rebalances", ["M"]):
        cfg = BacktestConfig(
            rebalance=rule,
            min_positions=int(account.get("min_positions", 3)),
            max_positions=int(account.get("max_positions", 6)),
            min_position_rp=float(account.get("min_position_rp", 1_000_000)),
            max_per_sector=int(getattr(settings, "max_per_sector", 2)),
            min_names=int(bt.get("min_names", 10)),
            risk_free_pct=float(getattr(settings, "risk_free_pct", 0.0) or 0.0),
        )
        args = (panel, settings.capital_rp, cfg, fee_cfg, settings.sectors,
                benchmark, fx, int(regime_cfg.get("trend_ma", 200)),
                regime_cfg.get("deploy_ladder", (0.30, 0.60, 1.00)))

        from backtest.engine import rebalance_dates
        base = run_backtest(*args)
        surv = R.survivorship_check(panel, benchmark,
                                    rebalance_dates(panel, rule), settings.capital_rp)
        factors = R.factor_report(*args)
        costs = R.cost_report(*args)
        regimes = R.regime_report(*args)
        # `cfg.exits` stays None for every report above, so questions 1-3 keep
        # answering exactly what they answered before. The exits get their own
        # comparison rather than silently changing the others' baseline.
        exits = R.exit_report(*args, atr_panel=atr_panel)
        robustness = R.robustness_report(*args)
        verdict = R.robustness_verdict(robustness)

        label = {"M": "Monthly", "W": "Weekly"}.get(rule, rule)
        sections[label] = {
            "factors": factors, "costs": costs, "regimes": regimes,
            "exits": exits, "robustness": robustness, "verdict": verdict,
            "n_rebalances": base.n_rebalances, "avg_names": base.avg_names_available,
            "fees_paid": base.fees_paid,
        }
        print(R.console_block(factors, costs, regimes, robustness, verdict,
                              label, base.avg_names_available, surv, exits))

        out = Path(settings.output_dir)
        costs.to_csv(out / f"backtest_costs_{rule}.csv", index=False)
        exits.to_csv(out / f"backtest_exits_{rule}.csv", index=False)
        robustness.to_csv(out / f"backtest_robustness_{rule}.csv", index=False)
        pd.DataFrame({c.label: c.equity for c in factors}).to_csv(
            out / f"backtest_equity_{rule}.csv")

    path = R.write_html(R.render_html(sections, surv), settings.output_dir)

    # The conclusions, small enough for the brief to read on every run. Without
    # this the page recommending the trades carried none of the evidence about
    # what the ranking is worth.
    verdict_path = R.write_verdict(
        R.verdict_payload(factors, robustness, surv, label, costs, exits),
        settings.output_dir)

    print(f"\n  Full report: {path}")
    print(f"  The terminal will now quote this: {verdict_path}\n")
    return 0


def cmd_audit_prices(settings, logger=None) -> int:
    """
    Is the price the tool values your positions at the real last close?

    A stale or mis-scaled price is the failure mode that shows up as a wrong "value
    now" and never announces itself -- the position simply looks better or worse than
    it is. This compares every ticker's `last_close` against the last bar in its own
    cached series, and flags anything whose last session is older than the rest of
    the universe.
    """
    import joblib

    from fetchers.data_fetcher import DataFetcher

    fetcher = DataFetcher(settings)
    frames = fetcher.fetch_technical_data(settings.stock_tickers)

    rows = []
    for ticker in settings.stock_tickers:
        frame = frames.get(ticker)
        if frame is None or "Close" not in frame:
            rows.append((ticker, None, None, "no price data at all"))
            continue
        closes = frame["Close"].dropna()
        if closes.empty:
            rows.append((ticker, None, None, "series is empty"))
            continue
        rows.append((ticker, float(closes.iloc[-1]), closes.index[-1].date(), ""))

    dated = [r for r in rows if r[2] is not None]
    newest = max((r[2] for r in dated), default=None)

    print("\nPRICE AUDIT")
    print("=" * 58)
    print(f"  {'ticker':10s} {'last close':>13s}  {'session':<12s} note")
    stale = 0
    for ticker, price, when, note in sorted(rows):
        if not note and when != newest:
            note = f"STALE - {(newest - when).days} day(s) behind"
            stale += 1
        shown = "-" if price is None else f"Rp{price:,.0f}"
        print(f"  {ticker:10s} {shown:>13s}  {str(when or '-'):<12s} {note}")

    print("-" * 58)
    print(f"  {len(rows)} tickers, newest session {newest}")
    if stale:
        print(f"  {stale} behind the rest - those positions would be valued on an "
              f"old price.")
    else:
        print("  every ticker is on the same session; nothing is being valued stale.")
    print("  Prices are daily closes from Yahoo Finance, not live quotes.")
    print("=" * 58 + "\n")
    return 0
