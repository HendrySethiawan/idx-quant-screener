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


def _can_prompt() -> bool:
    """A prompt needs a window. Without pywebview there is nothing to ask with."""
    try:
        from desktop import available
        return available()
    except Exception:
        return False


def main() -> None:
    _use_utf8_console()

    # Before anything reads a relative path. From a double-clicked exe the working
    # directory is whatever Windows chose, so this anchors it to the exe's folder and
    # seeds configs/ and data/ there on first run. From source it is a no-op.
    from core.paths import bootstrap
    bootstrap()

    settings = load_settings("configs/default.yaml")

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
    if args.backtest:
        raise SystemExit(cmd_backtest(settings))
    if args.journal:
        raise SystemExit(cmd_journal(settings))
    if args.mark:
        raise SystemExit(cmd_mark(settings, on_date=args.date))

    logger = setup_logger(settings.log_dir, settings.log_level)
    logger.info("Starting idx_quant_screener")

    # ---- capital, before anything is fetched --------------------------------
    # A run on the shipped placeholder produces a ticket sized to money that is not
    # yours. Ask once, up front, so nobody waits forty seconds for that.
    from first_run import (apply_capital, ask_capital, should_ask, warn_text)

    wants_window = not args.browser
    if should_ask(settings):
        chosen = ask_capital(logger) if (wants_window and _can_prompt()) else None
        if chosen:
            apply_capital(chosen, settings)
            logger.info(f"Capital set to Rp{chosen:,.0f} (configs/user.yaml)")
        else:
            # Declined, or a path that must never block: warn loudly and continue.
            print(warn_text(settings))

    from runner import full_run, render
    ctx = full_run(settings, args, logger)
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
    alloc, fees = plan["allocation"], plan["fees"]
    print(f"\n{regime.emoji}  {regime.label} - deploy {regime.deploy_pct:.0%} of {rp(settings.capital_rp)}")
    print(f"\nDO THIS TODAY ({alloc.n_positions} positions, {rp(alloc.cash_left)} cash left)")
    for o in sorted(plan["orders"], key=lambda x: {"SELL": 0, "BUY": 1, "HOLD": 2}[x["action"]]):
        print(f"  {o['action']:5s} {o['ticker']:9s} {o['lots']:>3} lot  {rp(o['rupiah']):>14}  {o['note']}")
    print(f"\n  Estimated fees: {rp(fees.total)} ({fees.pct_of(settings.capital_rp):.2f}% of capital)")
    for note in fees.notes:
        print(f"  TIP: {note}")

    warned = [o for o in plan["orders"] if o.get("event_state") == "known"]
    unknown = [o for o in plan["orders"] if o.get("event_state") == "unknown"]
    if warned or unknown:
        print()
        for o in warned:
            print(f"  !  {o['ticker']:9s} {o['event_note']}")
        for o in unknown:
            print(f"  -  {o['ticker']:9s} {o['event_note']}")
    if season_line:
        print(f"\n  Seasonality: {season_line}")

    if perf.n_closed or perf.position_value:
        print(console_block(perf))

    print(f"\nAnalyzed {len(df)} stocks | NaN scores: {int(df['undervaluation_score'].isna().sum())}")
    print(f"Brief: {brief_path}")

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
        print("  Rebuild redraws in ~2s; Re-run screen fetches fresh prices.", flush=True)

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
