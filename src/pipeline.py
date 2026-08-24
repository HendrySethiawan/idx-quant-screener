# src/pipeline.py
"""
Shared screener orchestration, used by the CLI and (later) the brief renderer.

Kept out of __main__.py so the whole path is importable and testable without
touching argv or the network.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import pandas as pd

from analysis.fundamental import FundamentalEngine
from analysis.selection import sector_capped_pick
from analysis.technical import compute_indicators, extract_latest_indicators
from fetchers.data_fetcher import DataFetcher

TOP_PICK_COLS = [
    "ticker", "name", "sector", "undervaluation_score", "pe_ratio", "dividend_yield",
    "rsi_14", "return_20d", "mom_6m", "realized_vol", "last_close",
    "median_daily_value_rp", "data_quality_flag", "imputed_factors",
]


def build_technical_frame(settings, fetcher: DataFetcher, logger=None) -> pd.DataFrame:
    """Latest per-ticker indicator snapshot for the whole universe."""
    tech_cfg = settings.technical if isinstance(settings.technical, dict) else {}
    vol_window = int(tech_cfg.get("vol_window", 60))
    liq_window = int(tech_cfg.get("liquidity_window", 20))

    price_data = fetcher.fetch_technical_data(settings.stock_tickers)
    records = []
    for ticker, raw in price_data.items():
        if ticker not in settings.stock_tickers:
            continue
        indicators = compute_indicators(raw, vol_window=vol_window, liquidity_window=liq_window)
        records.append({"ticker": ticker, **extract_latest_indicators(indicators)})

    if logger:
        logger.info(f"Built technical features for {len(records)} tickers")
    return pd.DataFrame(records), price_data


def run_screener(settings, logger=None, fetcher: Optional[DataFetcher] = None):
    """
    The whole screen. `fetcher` is accepted so a caller that already has one can
    pass it in -- `runner.full_run` built a second `DataFetcher` for FX and
    seasonality, and sharing it means one fewer object holding one fewer cache
    view, and lets the caller read what the shared fetcher learned (which session
    the market last traded).
    """
    fetcher = fetcher or DataFetcher(settings)
    engine = FundamentalEngine(settings)

    fund_records = fetcher.fetch_fundamentals(settings.stock_tickers)
    df_fund = engine.validate_fundamentals(fund_records)

    df_tech, price_data = build_technical_frame(settings, fetcher, logger)
    df = df_fund.merge(df_tech, on="ticker", how="left") if not df_tech.empty else df_fund

    df["sector"] = df["ticker"].map(settings.sectors).fillna("Unknown")

    df = engine.compute_scores(df)
    engine.save_factor_diagnostics(df, settings.output_dir)
    df["composite_score"] = df["undervaluation_score"]

    if settings.use_ml:
        if logger:
            logger.warning("use_ml is on -- the ranker's label derives from the score it overwrites")
    elif logger:
        logger.info("Ranking by the transparent composite (ML off by design)")

    if settings.risk_adjusted and "risk_adjusted_score" in df.columns:
        df["undervaluation_score"] = df["risk_adjusted_score"]

    df = df.sort_values("undervaluation_score", ascending=False).reset_index(drop=True)

    # Peer-multiple fair value. Runs after the sort so `value_universe` sees the
    # frame the rest of the tool sees, and after scoring because it is a separate
    # question -- the score ranks, this values. Pure computation, no network.
    if (settings.valuation or {}).get("enabled", True):
        from analysis.valuation import coverage, value_universe
        df = value_universe(df, settings)
        if logger:
            c = coverage(df)
            logger.info(
                f"Valued {c.get('valued', 0)}/{c.get('total', 0)} names "
                f"({c.get('undervalued', 0)} below peers, {c.get('fair', 0)} in line, "
                f"{c.get('overvalued', 0)} above; {c.get('one_measure', 0)} single-measure, "
                f"{c.get('unknown', 0)} not valuable)"
            )

    benchmark_data = fetcher.fetch_technical_data(settings.benchmarks) if settings.benchmarks else {}
    return df, price_data, benchmark_data


def top_picks(settings, df: pd.DataFrame) -> pd.DataFrame:
    """Sector-capped shortlist with only the columns a human reads."""
    ranked = df["ticker"].tolist()
    picked = sector_capped_pick(
        ranked,
        settings.sectors,
        top_n=settings.top_picks_n,
        max_per_sector=settings.max_per_sector,
    )
    cols = [c for c in TOP_PICK_COLS if c in df.columns]
    out = df[df["ticker"].isin(picked)].copy()
    out["__order"] = out["ticker"].map({t: i for i, t in enumerate(picked)})
    return out.sort_values("__order").drop(columns="__order")[cols].reset_index(drop=True)


def write_outputs(settings, df: pd.DataFrame, picks: pd.DataFrame, logger=None) -> None:
    out_dir = Path(settings.output_dir)
    df.to_csv(out_dir / "screener_results.csv", index=False)
    picks.to_csv(out_dir / "top_picks.csv", index=False)
    if logger:
        logger.info(f"Wrote screener_results.csv ({len(df)} rows) and top_picks.csv ({len(picks)} rows)")
