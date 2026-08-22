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


def build_performance(settings, prices=None, ihsg=None):
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

    account = getattr(settings, "account", None) or {}
    return evaluate(
        journal=journal, closed=closed, positions=positions, prices=prices,
        open_cost=open_cost, starting_capital=settings.capital_rp, cfg=cfg,
        ihsg_close=ihsg,
        min_trades_for_verdict=int(account.get("min_trades_for_verdict", 30)),
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
