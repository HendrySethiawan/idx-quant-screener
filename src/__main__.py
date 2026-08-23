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
from viz.renderer import ScreenerViz


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

    df, price_data, benchmark_data = run_screener(settings, logger)
    picks = top_picks(settings, df)
    write_outputs(settings, df, picks, logger)

    # ---- market regime -> how much to deploy -------------------------------
    regime_cfg = settings.regime or {}
    from fetchers.data_fetcher import DataFetcher
    fetcher = DataFetcher(settings)
    fx_data = fetcher.fetch_technical_data([regime_cfg.get("fx_ticker", "IDR=X")])

    regime = assess_regime(
        _close_series(benchmark_data, regime_cfg.get("benchmark", "^JKSE")),
        _close_series(fx_data, regime_cfg.get("fx_ticker", "IDR=X")),
        trend_ma=int(regime_cfg.get("trend_ma", 200)),
        deploy_ladder=regime_cfg.get("deploy_ladder"),
    )
    logger.info(f"Regime: {regime.label} -> deploy {regime.deploy_pct:.0%}")

    # ---- today's decision ---------------------------------------------------
    holdings = load_holdings(
        (settings.account or {}).get("holdings_path", "current_holdings.yaml"),
        lot_size=settings.lot_size,
    )
    # Events and seasonality: context the user already gathers by hand.
    from cli import collect_events
    from market import seasonality as S
    all_events, blind = collect_events(settings)
    horizon = int(getattr(settings, "event_horizon_days", 14))
    near_events = E.upcoming(all_events, horizon)

    # period="max", not the 2y panel: two years gives ~2 observations per month,
    # which is noise rather than a base rate.
    season_line = ""
    season_table = None
    try:
        jkse = fetcher.fetch_technical_data(
            [regime_cfg.get("benchmark", "^JKSE")], period="max"
        ).get(regime_cfg.get("benchmark", "^JKSE"))
        if jkse is not None and "Close" in jkse:
            season_table = S.monthly_seasonality(jkse["Close"])
            season_line = S.describe(S.for_month(season_table))
    except Exception as e:
        logger.warning(f"Seasonality unavailable: {e}")

    plan = assemble(settings, df, regime, holdings, events=all_events, blind=blind)

    pd.DataFrame(plan["orders"]).to_csv(settings.output_dir / "ticket.csv", index=False)

    # Performance reuses prices already fetched for the screen, so the journal
    # section costs no extra network round-trips.
    from cli import build_performance
    from report.journal_view import brief_section, console_block
    perf = build_performance(
        settings,
        prices=plan["prices"],
        ihsg=_close_series(benchmark_data, regime_cfg.get("benchmark", "^JKSE")),
    )

    # ---- the Advanced half --------------------------------------------------
    # Nothing here fetches. Every input is already in memory or already on disk;
    # the whole block exists to stop throwing that work away. Any single section
    # that has no data renders as "" and disappears, so a failure here degrades to
    # a smaller Advanced view rather than a broken brief.
    advanced_html = ""
    try:
        from analysis.fundamental import FundamentalEngine
        from cli import _paths
        from portfolio.journal import load_marks
        from report.advanced import render_advanced, whatif_grid

        _, marks_path, _ = _paths(settings)
        ticket_tickers = [o["ticker"] for o in plan["orders"] if o["action"] == "BUY"]
        breakdown = ticket_tickers or [c["ticker"] for c in plan["candidates"][:5]]

        advanced_html = render_advanced(
            df=df,
            factor_weights=settings.factor_weights,
            breakdown_tickers=breakdown,
            correlations=FundamentalEngine(settings).compute_factor_correlations(df),
            benchmark=_close_series(benchmark_data, regime_cfg.get("benchmark", "^JKSE")),
            trend_ma=int(regime_cfg.get("trend_ma", 200)),
            benchmark_name=regime_cfg.get("benchmark_label", "IHSG"),
            seasonality_table=season_table,
            current_month=pd.Timestamp.today().month,
            marks=load_marks(marks_path),
            allocation=plan["allocation"],
            max_per_sector=settings.max_per_sector,
            whatif=whatif_grid(
                plan["candidates_all"], settings, settings.capital_rp, regime.deploy_pct
            ),
            capital=settings.capital_rp,
            deploy_pct=regime.deploy_pct,
        )
    except Exception as e:
        logger.warning(f"Advanced sections unavailable: {e}")

    # ---- the Steps half -----------------------------------------------------
    # Reads the trail `assemble` recorded while the gates ran. Nothing is
    # recomputed here: an explanation that re-derives the rules would eventually
    # disagree with them, and a confident wrong explanation is worse than none.
    steps_html = ""
    try:
        from report.steps import render_steps
        steps_html = render_steps(plan.get("trail"), plan["orders"])
    except Exception as e:
        logger.warning(f"Steps view unavailable: {e}")

    # ---- the index panel ----------------------------------------------------
    # Real OHLC from the cached frame, not decoration. The chart is daily and the
    # panel says so -- a daily series dressed as an intraday line would imply a feed
    # this tool has never had.
    market = None
    try:
        bm_key = regime_cfg.get("benchmark", "^JKSE")
        raw = benchmark_data.get(bm_key)
        if raw is not None and "Close" in raw and len(raw) > 2:
            from report import charts
            trend_ma = int(regime_cfg.get("trend_ma", 200))
            window = raw.tail(260)
            closes = window["Close"].dropna()
            ma = raw["Close"].rolling(trend_ma, min_periods=max(2, trend_ma // 4)).mean()
            market = {
                "name": regime_cfg.get("benchmark_label", "IHSG"),
                "last": float(closes.iloc[-1]),
                "prev": float(closes.iloc[-2]),
                "open": float(window["Open"].iloc[-1]) if "Open" in window else None,
                "high": float(window["High"].iloc[-1]) if "High" in window else None,
                "low": float(window["Low"].iloc[-1]) if "Low" in window else None,
                "ma_last": float(ma.dropna().iloc[-1]) if ma.notna().any() else None,
                "trend_ma": trend_ma,
                "chart": charts.line_chart(
                    [("close", closes.tolist()),
                     (f"{trend_ma}d mean", ma.tail(len(closes)).tolist())],
                    x_labels=[d.strftime("%b %y") for d in closes.index],
                    label=f"{bm_key} daily close against its {trend_ma}-day mean",
                    height=190,
                ),
            }
    except Exception as e:
        logger.warning(f"Index panel unavailable: {e}")

    # ---- the settings page --------------------------------------------------
    # A read-only view of the numbers actually driving every gate, so a rule can be
    # found and changed rather than merely obeyed.
    def _rows(pairs):
        return "".join(f"<tr><td>{k}</td><td class='num'>{v}</td></tr>" for k, v in pairs)

    broker = settings.broker or {}
    account = settings.account or {}
    liq = settings.liquidity or {}
    settings_html = (
        '<div class="setgrp"><h3>Broker (Indopremier)</h3><table>' + _rows([
            ("Buy fee", f"{float(broker.get('buy_fee', 0)):.2%}"),
            ("Sell fee", f"{float(broker.get('sell_fee', 0)):.2%}"),
            ("Stamp duty, per day with a sell", rp(float(broker.get("stamp_duty_rp", 0)))),
            ("Lot size", broker.get("lot_size", 100)),
        ]) + "</table></div>"
        '<div class="setgrp"><h3>Account</h3><table>' + _rows([
            ("Capital", rp(settings.capital_rp)),
            ("Positions allowed", f"{account.get('min_positions', 3)}-{account.get('max_positions', 6)}"),
            ("Smallest position", rp(float(account.get("min_position_rp", 0)))),
            ("Max per sector", settings.max_per_sector),
            ("Shortlist size", settings.top_picks_n),
        ]) + "</table></div>"
        '<div class="setgrp"><h3>Liquidity gate</h3><table>' + _rows([
            ("Minimum traded per day", rp(float(liq.get("min_median_daily_value_rp", 0)))),
            ("Max position vs daily volume",
             f"{float(liq.get('max_position_pct_of_daily_value', 0)):.0%}"),
        ]) + "</table></div>"
        '<div class="setgrp"><h3>Factor weights</h3><table>' + _rows(
            [(k, f"{v:+.1f}") for k, v in (settings.factor_weights or {}).items()]
        ) + "</table></div>"
        '<div class="note">All of these live in <code>configs/default.yaml</code>; '
        "your capital comes from the git-ignored <code>configs/user.yaml</code>.</div>"
    )

    brief_path = write_brief(
        render_brief(
            regime=regime,
            orders=plan["orders"],
            fees=plan["fees"],
            capital=settings.capital_rp,
            holdings_rows=plan["holdings_rows"],
            candidates=plan["candidates"],
            rejected=plan["rejected"],
            capped=plan["capped"],
            allocation=plan["allocation"],
            journal_html=brief_section(perf),
            events=near_events,
            blind_n=len(blind),
            event_horizon=horizon,
            seasonality=season_line,
            universe_n=len(df),
            imputed_n=int((df["imputed_factors"].fillna("").str.len() > 0).sum()),
            advanced_html=advanced_html,
            steps_html=steps_html,
            settings_html=settings_html,
            market=market,
        ),
        settings.output_dir,
    )

    # The matplotlib PNG is opt-in. It was written on every run at 896KB and no
    # page ever linked to it, its colours are baked white so it fights the dark
    # theme, and one of its six panels plots `beta` -- the field the scorer
    # deliberately refuses to use. The Advanced view replaces it; --png keeps it
    # available for anyone who wants the file.
    if getattr(args, "png", False):
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
    from desktop import open_result
    route = open_result(brief_path, prefer_desktop=not args.browser,
                        title="IDX Terminal", logger=logger)
    if route == "none":
        print("  (could not open it automatically - open the file above yourself)")


if __name__ == "__main__":
    main()
