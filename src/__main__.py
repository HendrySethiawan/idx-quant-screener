# src/__main__.py
import sys
# webbrowser is reached through desktop.open_result, which handles the fallback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import pandas as pd

from core.config import load_settings
from core.logger import setup_logger
from market import events as E
from market.regime import assess_regime
from pipeline import run_screener, top_picks, write_outputs
from portfolio.holdings import load_holdings
from report.assemble import assemble
from report.brief import render_brief, rp, write_brief

# viz.renderer is NOT imported here. It pulls matplotlib, seaborn, scipy and PIL --
# about 180MB and 1.8 of the 3.8 seconds this entry point used to take to import --
# for a PNG that is opt-in and that no page links to. It is imported inside the
# --png branch instead. tests/test_packaging.py fails if it creeps back up here.


def _close_series(data: dict, ticker: str):
    frame = data.get(ticker)
    return frame["Close"] if frame is not None and "Close" in frame else None


def _use_utf8_console() -> None:
    """
    The Windows console defaults to cp1252, which cannot encode the regime emoji.
    Reconfiguring beats sprinkling try/except UnicodeEncodeError at each print.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def main() -> None:
    _use_utf8_console()

    # Before anything reads a relative path. From a double-clicked exe the working
    # directory is whatever Windows chose, so this anchors it to the exe's folder and
    # seeds configs/ and data/ there on first run. From source it is a no-op.
    from core.paths import bootstrap
    bootstrap()

    settings = load_settings("configs/default.yaml")

    # Capital comes from the cash ledger when there is one. Done here, before any
    # subcommand, because every one of them sizes something against it.
    from portfolio.cash import sync_capital
    sync_capital(settings)

    # Journal subcommands run without touching the screener, so recording a trade
    # at lunch does not wait on 49 tickers of network fetch.
    from cli import (build_parser, cmd_backtest, cmd_event, cmd_events,
                     cmd_journal, cmd_log, cmd_mark)
    args = build_parser().parse_args()
    if args.log:
        raise SystemExit(cmd_log(settings, args))
    if args.event:
        raise SystemExit(cmd_event(settings, args))
    if args.events:
        raise SystemExit(cmd_events(settings))
    if args.audit_prices:
        from cli import cmd_audit_prices
        raise SystemExit(cmd_audit_prices(settings))
    if args.backtest:
        raise SystemExit(cmd_backtest(settings))
    if args.journal:
        raise SystemExit(cmd_journal(settings))
    if args.mark:
        raise SystemExit(cmd_mark(settings, on_date=args.date))

    logger = setup_logger(settings.log_dir, settings.log_level)
    logger.info("Starting idx_quant_screener")

    # ---- capital ------------------------------------------------------------
    # No prompt any more. It was a second pywebview window opened in the same
    # process that later opens the main one, it was one click to dismiss, and
    # dismissing it left the run sized for Rp100,000,000 of somebody else's money.
    # The banner on the page now carries this, next to the form that fixes it.
    from first_run import should_ask, warn_text
    if should_ask(settings):
        print(warn_text(settings))

    # ---- the screen ---------------------------------------------------------
    # Reopened from the last fetch, not fetched again. Opening the app is not a
    # request for fresh data -- pressing Update data is.
    from runner import full_run, load_snapshot, render, save_snapshot

    ctx = None if getattr(args, "refresh", False) else load_snapshot(settings, args, logger)
    if ctx is None:
        ctx = full_run(settings, args, logger)
        save_snapshot(ctx)
    elif logger:
        logger.info(f"Opened the screen saved at {ctx.fetched_at:%d %b %Y %H:%M} "
                    f"- press Update data to fetch again")

    plan_holder = render(ctx)
    plan, perf, brief_path = ctx.plan, ctx.perf, plan_holder
    df = ctx.df
    regime = ctx.regime
    season_line = ctx.season_line
    benchmark_data = ctx.benchmark_data

    # The matplotlib PNG is opt-in. It was written on every run at 896KB and no page
    # ever linked to it, its colours are baked white so it fights the dark theme, and
    # one of its six panels plots `beta` -- the field the scorer deliberately refuses
    # to use. The Screener view replaces it; --png keeps it available.
    if getattr(args, "png", False):
        try:
            from viz.renderer import ScreenerViz
        except ImportError:
            print("  --png needs the chart libraries (matplotlib, seaborn), which are "
                  "not in this build.\n"
                  "  Run from source if you want the PNG - the Screener view replaces it.")
        else:
            ScreenerViz(settings.output_dir, sectors=settings.sectors).save_analysis(
                df, benchmark_data=benchmark_data
            )
            print(f"Charts: {settings.output_dir / 'screener_analysis.png'}")

    # ---- console summary ----------------------------------------------------
    # Built in runner.console_summary, not inline here: the performance block is
    # only reached once something is held or has been sold, so inline it was a
    # branch no test could touch -- which is exactly where a missing import hid.
    from runner import console_summary
    print(console_summary(ctx))

    # A native window by default, a browser tab if that is not possible or if
    # --browser was asked for. `open_result` never raises: the analysis is already
    # done and written by this point, and a window failing to open must not lose it.
    from api import TerminalAPI
    from desktop import open_result

    # webview.start() blocks with no output of its own, so without these lines a
    # working app looks hung: the log simply stops after "Brief:".
    # flush: stdout is block-buffered when it is redirected to a file rather than a
    # console, and webview.start() then blocks for as long as the window is open. The
    # last thing the reader saw would be nothing at all.
    if args.browser:
        print("\nOpening it in your browser...", flush=True)
    else:
        print("\nOpening the terminal window - close it to exit.", flush=True)
        print("  Rebuild redraws in ~2s; Update data fetches fresh prices.", flush=True)

    route = open_result(
        brief_path, prefer_desktop=not args.browser, title="IDX Terminal",
        logger=logger,
        js_api=TerminalAPI(settings, prices=plan["prices"], logger=logger, ctx=ctx),
    )
    if route == "none":
        print("  (could not open it automatically - open the file above yourself)")
    elif route == "desktop":
        print("Window closed.")


if __name__ == "__main__":
    main()
