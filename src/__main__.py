# src/__main__.py
import sys
import webbrowser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import pandas as pd

from core.config import load_settings
from core.logger import setup_logger
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
    plan = assemble(settings, df, regime, holdings)

    pd.DataFrame(plan["orders"]).to_csv(settings.output_dir / "ticket.csv", index=False)

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
            universe_n=len(df),
            imputed_n=int((df["imputed_factors"].fillna("").str.len() > 0).sum()),
        ),
        settings.output_dir,
    )

    ScreenerViz(settings.output_dir, sectors=settings.sectors).save_analysis(
        df, benchmark_data=benchmark_data
    )

    # ---- console summary ----------------------------------------------------
    alloc, fees = plan["allocation"], plan["fees"]
    print(f"\n{regime.emoji}  {regime.label} - deploy {regime.deploy_pct:.0%} of {rp(settings.capital_rp)}")
    print(f"\nDO THIS TODAY ({alloc.n_positions} positions, {rp(alloc.cash_left)} cash left)")
    for o in sorted(plan["orders"], key=lambda x: {"SELL": 0, "BUY": 1, "HOLD": 2}[x["action"]]):
        print(f"  {o['action']:5s} {o['ticker']:9s} {o['lots']:>3} lot  {rp(o['rupiah']):>14}  {o['note']}")
    print(f"\n  Estimated fees: {rp(fees.total)} ({fees.pct_of(settings.capital_rp):.2f}% of capital)")
    for note in fees.notes:
        print(f"  TIP: {note}")

    print(f"\nAnalyzed {len(df)} stocks | NaN scores: {int(df['undervaluation_score'].isna().sum())}")
    print(f"Brief: {brief_path}")

    try:
        webbrowser.open(brief_path.resolve().as_uri())
    except Exception:
        pass


if __name__ == "__main__":
    main()
