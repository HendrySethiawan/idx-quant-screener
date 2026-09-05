# src/runner.py
"""
The pipeline, split into the half that needs the network and the half that does not.

`main()` used to be three hundred lines of inline orchestration, which meant the only
way to see a change was to close the app and start it again -- including after
recording a trade, where nothing about the market had moved at all.

    full_run(...)  fetches 49 tickers, scores, ranks. ~40 seconds.
    render(ctx)    rebuilds the page from what is already in memory. ~2 seconds.

The split is the feature: **`render` must never touch the network.** Recording a
trade, changing a setting or noting an event all change the page without changing the
market, and paying forty seconds for that would make the app feel broken. There is a
test that patches the fetcher to raise and calls `render`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional

import pandas as pd

from market import events as E
from market.regime import assess_regime
from pipeline import run_screener, top_picks, write_outputs
from portfolio.holdings import load_holdings
from report.assemble import assemble
from report.brief import render_brief, write_brief


def _close_series(data: dict, ticker: str):
    frame = data.get(ticker)
    return frame["Close"] if frame is not None and "Close" in frame else None


@dataclass
class RunContext:
    """Everything a render needs. Nothing here requires another fetch."""
    settings: Any
    args: Any
    logger: Any = None
    df: Optional[pd.DataFrame] = None
    price_data: Dict[str, Any] = field(default_factory=dict)
    benchmark_data: Dict[str, Any] = field(default_factory=dict)
    regime: Any = None
    season_table: Optional[pd.DataFrame] = None
    season_line: str = ""
    # Filled by the most recent render, so the console summary and the API can read
    # what the page is currently showing without recomputing it.
    plan: Optional[dict] = None
    perf: Any = None
    brief_path: Optional[Path] = None
    # When the market data in here was fetched, and what universe it was fetched
    # for. Both are written to the snapshot: the first so the page can say how old
    # what you are reading is, the second so a changed ticker list is not silently
    # rendered against scores computed for a different one.
    fetched_at: Optional[pd.Timestamp] = None
    universe_key: str = ""
    # Which trading session the prices are from, as opposed to when we asked for
    # them. Those are different questions and the page used to answer only the
    # second one, which is how a screen of 21 August closes read as current.
    sessions: Dict[str, Any] = field(default_factory=dict)
    # The watchlist, equally weighted, as an index level. Persisted because
    # `price_data` is not, and the benchmark has to survive a snapshot launch.
    watchlist_level: Optional[pd.Series] = None
    # Pairwise return correlations, so selection can cap the bet rather than the
    # label. Persisted for the same reason as the watchlist: `price_data` is not.
    correlations: Optional[pd.DataFrame] = None
    # Close and High, trimmed, per ticker. The exit rules need a series where `df`
    # only has a row: a stop compares the last close against an entry made weeks
    # ago, and a trailing stop needs the high since that entry.
    risk_panel: Dict[str, pd.DataFrame] = field(default_factory=dict)
    # How far a score moves when the universe gains or loses one name, measured by
    # jackknife on the day's own universe. It is the precision the ranking has, and
    # two names closer together than this are not ranked, they are tied.
    score_floor: float = 0.0


# --------------------------------------------------------------- needs network
def full_run(settings, args, logger=None) -> RunContext:
    """Fetch, score and rank. The slow half."""
    from fetchers.data_fetcher import (DataFetcher, equal_weight_level,
                                       return_correlations, risk_panel,
                                       session_report)
    from market import seasonality as S

    # One fetcher for the whole run. `run_screener` used to build its own, so the
    # probe it makes for the latest traded session was invisible out here.
    fetcher = DataFetcher(settings)
    df, price_data, benchmark_data = run_screener(settings, logger, fetcher=fetcher)
    write_outputs(settings, df, top_picks(settings, df), logger)

    sessions = session_report(price_data, fetcher.latest_market_session,
                              failed=fetcher.failed)
    if logger and sessions["session_date"] is not None:
        logger.info(f"Prices are from the {sessions['session_date'].date()} session")
        if sessions.get("missing"):
            logger.warning(
                f"{len(sessions['missing'])} ticker(s) could not be fetched and are "
                f"absent from the ranking: {', '.join(sessions['missing'])}")
        if sessions["mixed"]:
            logger.warning(
                f"{len(sessions['laggards'])} ticker(s) are priced on an older "
                f"session than the rest: "
                f"{', '.join(t for t, _ in sessions['laggards'][:6])}")

    watchlist_level = equal_weight_level(price_data)
    correlations = return_correlations(
        price_data, int((settings.selection or {}).get("correlation_window", 120)))

    # Measured here, not in `render`: it is a jackknife over the whole universe and
    # costs a few seconds, which belongs in the fetch you already waited for rather
    # than in the redraw that has to feel instant.
    score_floor = 0.0
    try:
        from analysis.fundamental import FundamentalEngine
        q = float((settings.selection or {}).get("tie_floor_quantile", 0.9))
        score_floor = FundamentalEngine(settings).score_noise_floor(df, quantile=q)
        if logger:
            logger.info(f"Score precision: two names within {score_floor:.3f} of each "
                        f"other are tied, not ranked")
    except Exception as e:
        if logger:
            logger.warning(f"Could not measure score precision: {e}")

    regime_cfg = settings.regime or {}
    fx_data = fetcher.fetch_technical_data([regime_cfg.get("fx_ticker", "IDR=X")])

    regime = assess_regime(
        _close_series(benchmark_data, regime_cfg.get("benchmark", "^JKSE")),
        _close_series(fx_data, regime_cfg.get("fx_ticker", "IDR=X")),
        trend_ma=int(regime_cfg.get("trend_ma", 200)),
        deploy_ladder=regime_cfg.get("deploy_ladder"),
    )
    if logger:
        logger.info(f"Regime: {regime.label} -> deploy {regime.deploy_pct:.0%}")

    # period="max", not the 2y panel: two years gives ~2 observations per month,
    # which is noise rather than a base rate.
    season_table, season_line = None, ""
    try:
        key = regime_cfg.get("benchmark", "^JKSE")
        jkse = fetcher.fetch_technical_data([key], period="max").get(key)
        if jkse is not None and "Close" in jkse:
            season_table = S.monthly_seasonality(jkse["Close"])
            season_line = S.describe(S.for_month(season_table))
    except Exception as e:
        if logger:
            logger.warning(f"Seasonality unavailable: {e}")

    return RunContext(
        settings=settings, args=args, logger=logger,
        df=df, price_data=price_data, benchmark_data=benchmark_data,
        regime=regime, season_table=season_table, season_line=season_line,
        fetched_at=pd.Timestamp.now(), universe_key=universe_key(settings),
        sessions=sessions, watchlist_level=watchlist_level,
        correlations=correlations, risk_panel=risk_panel(price_data),
        score_floor=score_floor,
    )


# ------------------------------------------------------------- the last screen
#
# Fetching 49 tickers on every launch made opening the app a forty-second wait for
# data that had not moved -- and `fetch_fundamentals` was uncached, so it was forty
# seconds every single time, not merely the first. The screen is saved instead and
# reopened instantly; fetching is what the Update button is for.
SNAPSHOT_REL = Path("data/snapshot/run.joblib")

# Bumped when the shape below changes, so an old file is ignored rather than
# unpacked into fields that no longer mean the same thing.
#
#   2  adds `risk_panel`. A v1 file has no price series in it, so every position
#      would come back with no stop and no trailing level -- the exit panel would
#      read "cannot measure" for a book that is perfectly measurable. Refetching
#      once is the right answer; rendering a wrong one is not.
#   3  the dividend yield changed source and the sanity bounds changed behaviour,
#      so `undervaluation_score` no longer means what a v2 file's does. Nothing
#      about the SHAPE changed, which is the point: a snapshot is stale when the
#      code that computed it is gone, not only when its fields move.
SNAPSHOT_VERSION = 3


def universe_key(settings) -> str:
    """Identifies the data a snapshot was built for."""
    import hashlib

    tickers = ",".join(sorted(settings.stock_tickers or {}))
    raw = f"{tickers}|{getattr(settings, 'history_period', '')}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _code_changed_since(path: Path) -> bool:
    """
    Was this snapshot written by code that has since been replaced?

    `SNAPSHOT_VERSION` catches a change somebody remembered to declare. This
    catches the rest, and it is the more dangerous half: the dividend fix altered
    what `undervaluation_score` MEANS without touching a single field name, so a
    saved screen stayed loadable and quietly kept showing scores computed under a
    bug. The version bump fixes that one occurrence; this stops the next.

    Frozen: the executable's own timestamp -- a new binary is new logic by
    definition. From source: the newest file under `src/`, the same rule
    `packaging/verify_bridge.py` already uses to refuse a stale brief.

    Any failure to read a timestamp answers False. Refetching unnecessarily costs
    a minute; refusing to refetch when the maths moved costs the wrong trade.
    """
    from core.paths import is_frozen

    try:
        written = path.stat().st_mtime
        if is_frozen():
            import sys
            return Path(sys.executable).stat().st_mtime > written
        src = Path(__file__).resolve().parent
        return any(f.stat().st_mtime > written for f in src.rglob("*.py"))
    except OSError:
        return False


def _snapshot_path(settings) -> Path:
    account = getattr(settings, "account", None) or {}
    return Path(account.get("snapshot_path", SNAPSHOT_REL))


def save_snapshot(ctx: RunContext) -> Optional[Path]:
    """
    Persist what a render needs. Returns the path, or None if it could not be saved.

    `price_data` is still deliberately left out -- 49 OHLCV frames would be the
    largest thing in the file. What goes in instead is `risk_panel`: Close and High
    only, trimmed to the recent sessions, because the exit rules DO need a series
    where the rest of the render only needs `df`'s one row per ticker. A stop
    compares today's close against an entry made weeks ago, and a trailing stop
    needs the high since that entry; neither survives in a snapshot of the latest
    indicators. Roughly a tenth of the raw frames, and it keeps `render` off the
    network, which is the rule the whole split rests on.

    Never raises. A snapshot that cannot be written costs the next launch forty
    seconds; an exception here would cost this one everything after it.
    """
    import joblib

    try:
        path = _snapshot_path(ctx.settings)
        path.parent.mkdir(parents=True, exist_ok=True)
        joblib.dump({
            "version": SNAPSHOT_VERSION,
            "df": ctx.df,
            "benchmark_data": ctx.benchmark_data,
            "regime": ctx.regime,
            "season_table": ctx.season_table,
            "season_line": ctx.season_line,
            "fetched_at": ctx.fetched_at or pd.Timestamp.now(),
            "universe_key": ctx.universe_key or universe_key(ctx.settings),
            "sessions": ctx.sessions or {},
            "watchlist_level": ctx.watchlist_level,
            "correlations": ctx.correlations,
            "risk_panel": ctx.risk_panel or {},
            "score_floor": ctx.score_floor,
        }, path)
        return path
    except Exception as e:
        if ctx.logger:
            ctx.logger.warning(f"Could not save the screen for next time: {e}")
        return None


def load_snapshot(settings, args, logger=None) -> Optional[RunContext]:
    """
    Rebuild a RunContext from the last fetch, or None if there is nothing usable.

    None on every failure -- missing, corrupt, written by an older version, or built
    for a different universe. The caller's answer to None is always the same: fetch.
    So there is nothing to gain by distinguishing them beyond a line in the log, and
    an exception here would strand the launch it was meant to speed up.
    """
    import joblib

    path = _snapshot_path(settings)
    if not path.exists():
        return None

    try:
        blob = joblib.load(path)
    except Exception as e:
        if logger:
            logger.info(f"Ignoring an unreadable saved screen: {e}")
        return None

    if not isinstance(blob, dict) or blob.get("version") != SNAPSHOT_VERSION:
        return None

    if _code_changed_since(path):
        if logger:
            logger.info("The saved screen was computed by code that has since "
                        "changed; fetching fresh rather than showing it.")
        return None

    df = blob.get("df")
    if df is None or getattr(df, "empty", True):
        return None

    # `render` reads regime.deploy_pct unconditionally, so a snapshot without one
    # would fail later and further from the cause than here.
    if blob.get("regime") is None:
        return None

    want = universe_key(settings)
    if blob.get("universe_key") != want:
        if logger:
            logger.info("The ticker universe changed; the saved screen no longer "
                        "applies and a fresh run is needed.")
        return None

    return RunContext(
        settings=settings, args=args, logger=logger,
        df=df, price_data={}, benchmark_data=blob.get("benchmark_data") or {},
        regime=blob.get("regime"), season_table=blob.get("season_table"),
        season_line=blob.get("season_line") or "",
        fetched_at=blob.get("fetched_at"), universe_key=want,
        sessions=blob.get("sessions") or {},
        watchlist_level=blob.get("watchlist_level"),
        correlations=blob.get("correlations"),
        risk_panel=blob.get("risk_panel") or {},
        score_floor=float(blob.get("score_floor") or 0.0),
    )


# ------------------------------------------------------------- no network here
def _market_panel_data(ctx) -> Optional[dict]:
    """Real OHLC from the frame already fetched. Daily, and the panel says so."""
    cfg = ctx.settings.regime or {}
    key = cfg.get("benchmark", "^JKSE")
    raw = ctx.benchmark_data.get(key)
    if raw is None or "Close" not in raw or len(raw) < 3:
        return None

    from report import charts

    trend_ma = int(cfg.get("trend_ma", 200))
    window = raw.tail(260)
    closes = window["Close"].dropna()
    ma = raw["Close"].rolling(trend_ma, min_periods=max(2, trend_ma // 4)).mean()

    return {
        "name": cfg.get("benchmark_label", "IHSG"),
        "last": float(closes.iloc[-1]),
        "prev": float(closes.iloc[-2]),
        "open": float(window["Open"].iloc[-1]) if "Open" in window else None,
        "high": float(window["High"].iloc[-1]) if "High" in window else None,
        "low": float(window["Low"].iloc[-1]) if "Low" in window else None,
        "ma_last": float(ma.dropna().iloc[-1]) if ma.notna().any() else None,
        "trend_ma": trend_ma,
        "chart": charts.line_chart(
            [("close", closes.tolist()), (f"{trend_ma}d mean", ma.tail(len(closes)).tolist())],
            x_labels=[d.strftime("%b %y") for d in closes.index],
            label=f"{key} daily close against its {trend_ma}-day mean", height=190,
        ),
    }


def _backtest_verdict(settings, logger=None) -> Optional[dict]:
    """
    What `--backtest` last concluded, if it has ever been run here.

    Imported lazily and wrapped: the backtest package pulls the whole engine, and
    a brief that cannot be drawn because a JSON file is malformed would be a much
    worse outcome than a brief that omits one callout.
    """
    try:
        from backtest.report import load_verdict
        return load_verdict(settings.output_dir)
    except Exception as e:
        if logger:
            logger.warning(f"Could not read the stored backtest verdict: {e}")
        return None


def _settings_panel(settings) -> str:
    """A read-only view of the numbers driving every gate."""
    from report.brief import rp

    def rows(pairs):
        return "".join(f"<tr><td>{k}</td><td class='num'>{v}</td></tr>" for k, v in pairs)

    broker = settings.broker or {}
    account = settings.account or {}
    liq = settings.liquidity or {}
    return (
        '<div class="setgrp"><h3>Broker (Indopremier)</h3><table>' + rows([
            ("Buy fee", f"{float(broker.get('buy_fee', 0)):.2%}"),
            ("Sell fee", f"{float(broker.get('sell_fee', 0)):.2%}"),
            ("Stamp duty, per day with a sell", rp(float(broker.get("stamp_duty_rp", 0)))),
            ("Lot size", broker.get("lot_size", 100)),
        ]) + "</table></div>"
        '<div class="setgrp"><h3>Account</h3><table>' + rows([
            ("Capital", rp(settings.capital_rp)),
            ("Positions allowed",
             f"{account.get('min_positions', 3)}-{account.get('max_positions', 6)}"),
            ("Smallest position", rp(float(account.get("min_position_rp", 0)))),
            ("Max per sector", settings.max_per_sector),
            ("Shortlist size", settings.top_picks_n),
        ]) + "</table></div>"
        '<div class="setgrp"><h3>Liquidity gate</h3><table>' + rows([
            ("Minimum traded per day", rp(float(liq.get("min_median_daily_value_rp", 0)))),
            ("Max position vs daily volume",
             f"{float(liq.get('max_position_pct_of_daily_value', 0)):.0%}"),
        ]) + "</table></div>"
        '<div class="setgrp"><h3>Factor weights</h3><table>' + rows(
            [(k, f"{v:+.1f}") for k, v in (settings.factor_weights or {}).items()]
        ) + "</table></div>"
        '<div class="note">All of these live in <code>configs/default.yaml</code>; your '
        "capital comes from <code>configs/user.yaml</code>, which stays on this machine."
        "</div>"
    )


def render(ctx: RunContext) -> Path:
    """
    Rebuild the page from what is already in memory.

    Holdings, events and the journal are re-read from disk because those are exactly
    what a trade or an edit changes. Prices, scores and the ranking are reused: the
    market has not moved because you recorded something.
    """
    settings, args, logger = ctx.settings, ctx.args, ctx.logger
    from cli import _paths, build_performance, collect_events
    from portfolio.exits import ExitConfig
    from portfolio.journal import load_journal
    from report.journal_view import brief_section

    holdings = load_holdings(
        (settings.account or {}).get("holdings_path", "current_holdings.yaml"),
        lot_size=settings.lot_size,
    )
    all_events, blind = collect_events(settings)
    horizon = int(getattr(settings, "event_horizon_days", 14))

    # Re-read on every render, like holdings and events: recording a trade changes
    # what the exit plans should say, and that is exactly the moment `render` runs
    # without `full_run`.
    journal_path, _, _ = _paths(settings)
    journal = load_journal(journal_path)

    plan = assemble(settings, ctx.df, ctx.regime, holdings,
                    correlations=ctx.correlations,
                    events=all_events, blind=blind,
                    risk_panel=ctx.risk_panel, journal=journal,
                    score_floor=ctx.score_floor)
    pd.DataFrame(plan["orders"]).to_csv(settings.output_dir / "ticket.csv", index=False)

    perf = build_performance(
        settings, prices=plan["prices"],
        ihsg=_close_series(ctx.benchmark_data,
                           (settings.regime or {}).get("benchmark", "^JKSE")),
        watchlist=ctx.watchlist_level,
    )

    advanced_html = steps_html = ledger_html = trade_form_html = ""
    try:
        from analysis.fundamental import FundamentalEngine
        from cli import _paths
        from portfolio.journal import load_marks
        from report.advanced import render_advanced, whatif_grid

        _, marks_path, _ = _paths(settings)
        bought = [o["ticker"] for o in plan["orders"] if o["action"] == "BUY"]
        advanced_html = render_advanced(
            df=ctx.df, factor_weights=settings.factor_weights,
            breakdown_tickers=bought or [c["ticker"] for c in plan["candidates"][:5]],
            correlations=FundamentalEngine(settings).compute_factor_correlations(ctx.df),
            benchmark=_close_series(ctx.benchmark_data,
                                    (settings.regime or {}).get("benchmark", "^JKSE")),
            trend_ma=int((settings.regime or {}).get("trend_ma", 200)),
            benchmark_name=(settings.regime or {}).get("benchmark_label", "IHSG"),
            seasonality_table=ctx.season_table,
            current_month=pd.Timestamp.today().month,
            marks=load_marks(marks_path), allocation=plan["allocation"],
            max_per_sector=settings.max_per_sector,
            whatif=whatif_grid(plan["candidates_all"], settings,
                               settings.capital_rp, ctx.regime.deploy_pct),
            capital=settings.capital_rp, deploy_pct=ctx.regime.deploy_pct,
        )
    except Exception as e:
        if logger:
            logger.warning(f"Screener sections unavailable: {e}")

    try:
        from report.steps import render_steps
        steps_html = render_steps(plan.get("trail"), plan["orders"])
    except Exception as e:
        if logger:
            logger.warning(f"Why view unavailable: {e}")

    cash_form_html = dividend_form_html = ""
    try:
        from desktop import available as _desktop_available
        from report.journal_view import (cash_form, cli_fallback, dividend_form,
                                         journal_panels, trade_form)

        ledger_html = journal_panels(settings, prices=plan["prices"],
                                     exit_plans=plan.get("exit_plans"))
        live = _desktop_available() and not getattr(args, "browser", False)
        trade_form_html = trade_form() if live else cli_fallback()
        cash_form_html = cash_form() if live else ""
        dividend_form_html = dividend_form() if live else ""
    except Exception as e:
        if logger:
            logger.warning(f"Ledger unavailable: {e}")

    from first_run import is_placeholder_capital

    path = write_brief(
        render_brief(
            regime=ctx.regime, orders=plan["orders"], fees=plan["fees"],
            capital=settings.capital_rp, holdings_rows=plan["holdings_rows"],
            candidates=plan["candidates"], rejected=plan["rejected"],
            capped=plan["capped"], allocation=plan["allocation"],
            journal_html=brief_section(perf),
            events=E.upcoming(all_events, horizon), blind_n=len(blind),
            event_horizon=horizon, seasonality=ctx.season_line,
            universe_n=len(ctx.df),
            imputed_n=int((ctx.df["imputed_factors"].fillna("").str.len() > 0).sum()),
            advanced_html=advanced_html, steps_html=steps_html,
            settings_html=_settings_panel(settings),
            ledger_html=ledger_html, trade_form_html=trade_form_html,
            cash_form_html=cash_form_html,
            dividend_form_html=dividend_form_html,
            market=_market_panel_data(ctx),
            placeholder_capital=is_placeholder_capital(settings),
            perf=perf, fetched_at=ctx.fetched_at, sessions=ctx.sessions,
            verdict=_backtest_verdict(settings, logger),
            book_correlation=plan.get("book_correlation"),
            exit_plans=plan.get("exit_plans"), open_risk=plan.get("open_risk"),
            exit_cfg=ExitConfig.from_settings(settings),
            tie_groups=plan.get("tie_groups"), score_floor=plan.get("score_floor", 0.0),
        ),
        settings.output_dir,
    )

    ctx.plan, ctx.perf, ctx.brief_path = plan, perf, path
    return path


def console_summary(ctx: RunContext) -> str:
    """
    The run summary, as text.

    It lives here rather than inline in `main()` because inline is where a missing
    import hid. The performance block is only reached when there is something in the
    journal, so every test with an empty one skipped it, and the NameError only
    surfaced for a reader who actually owned something. Returning a string rather
    than printing makes that branch reachable from a test.
    """
    from report.brief import rp
    from report.journal_view import console_block

    settings, plan, regime = ctx.settings, ctx.plan, ctx.regime
    alloc, fees = plan["allocation"], plan["fees"]
    out = []

    out.append(f"\n{regime.emoji}  {regime.label} - deploy {regime.deploy_pct:.0%} "
               f"of {rp(settings.capital_rp)}")
    out.append(f"\nDO THIS TODAY ({alloc.n_positions} positions, "
               f"{rp(alloc.cash_left)} cash left)")

    order = {"SELL": 0, "BUY": 1, "HOLD": 2}
    for o in sorted(plan["orders"], key=lambda x: order.get(x["action"], 9)):
        out.append(f"  {o['action']:5s} {o['ticker']:9s} {o['lots']:>3} lot  "
                   f"{rp(o['rupiah']):>14}  {o['note']}")

    out.append(f"\n  Estimated fees: {rp(fees.total)} "
               f"({fees.pct_of(settings.capital_rp):.2f}% of capital)")
    for note in fees.notes:
        out.append(f"  TIP: {note}")

    warned = [o for o in plan["orders"] if o.get("event_state") == "known"]
    unknown = [o for o in plan["orders"] if o.get("event_state") == "unknown"]
    if warned or unknown:
        out.append("")
        for o in warned:
            out.append(f"  !  {o['ticker']:9s} {o['event_note']}")
        for o in unknown:
            out.append(f"  -  {o['ticker']:9s} {o['event_note']}")

    if ctx.season_line:
        out.append(f"\n  Seasonality: {ctx.season_line}")

    # The branch that crashed: reached only once something is held or has been sold.
    if ctx.perf is not None and (ctx.perf.n_closed or ctx.perf.position_value):
        out.append(console_block(ctx.perf))

    nan_scores = int(ctx.df["undervaluation_score"].isna().sum())
    out.append(f"\nAnalyzed {len(ctx.df)} stocks | NaN scores: {nan_scores}")
    out.append(f"Brief: {ctx.brief_path}")
    return "\n".join(out)
