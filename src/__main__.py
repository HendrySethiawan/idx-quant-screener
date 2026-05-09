# src/__main__.py
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from core.config import load_settings
from core.logger import setup_logger
from fetchers.data_fetcher import DataFetcher
from analysis.fundamental import FundamentalEngine
from analysis.technical import compute_indicators, extract_latest_indicators
from analysis.ml_ranker import StockRanker
from viz.renderer import ScreenerViz
import pandas as pd

def main():
    settings = load_settings("configs/default.yaml")
    logger = setup_logger(settings.log_dir, settings.log_level)
    logger.info("Starting idx_quant_screener")

    # ✅ Extract sector mapping if defined in config
    sectors = {}
    if hasattr(settings, "sectors"):
        sectors = settings.sectors if isinstance(settings.sectors, dict) else {}
    elif isinstance(settings, dict) and "sectors" in settings:
        sectors = settings.get("sectors", {})

    # 1. Fetch data
    fetcher = DataFetcher(settings)
    fund_records = fetcher.fetch_fundamentals(settings.stock_tickers)
    tech_data = fetcher.fetch_technical_data(settings.stock_tickers)
    benchmark_data = fetcher.fetch_technical_data(settings.benchmarks) if hasattr(settings, "benchmarks") and settings.benchmarks else {}

    # 2. Process fundamentals
    fund_engine = FundamentalEngine(settings)
    df_fund = fund_engine.validate_fundamentals(fund_records)
    df_fund = fund_engine.compute_scores(df_fund)

    # 3. Process technicals
    tech_records = []
    for ticker, df in tech_data.items():
        if ticker not in settings.stock_tickers:
            continue
        df_ind = compute_indicators(df)
        latest = extract_latest_indicators(df_ind)
        tech_records.append({"ticker": ticker, **latest})
    
    df_tech = pd.DataFrame(tech_records)
    df = df_fund.merge(df_tech, on="ticker", how="left")

    # ✅ Add sector column if mapping available
    if sectors and "ticker" in df.columns:
        df["sector"] = df["ticker"].map(sectors).fillna("Unknown")

    # 4. ML Ranking
    ranker = StockRanker(settings)
    trained = ranker.train(df)
    logger.info(f"ML Training: {'success' if trained else 'skipped'}")

    if trained:
        # Safe attribute access for nested config
        ml_config = settings.ml if isinstance(settings.ml, dict) else settings.ml.model_dump() if hasattr(settings.ml, "model_dump") else settings.ml
        ml_features = ml_config.get("features", []) if isinstance(ml_config, dict) else getattr(ml_config, "features", [])
        
        if ml_features:
            features = df[ml_features].dropna()
            if not features.empty:
                df.loc[features.index, "undervaluation_score"] = ranker.predict(features)
    
    # ✅ Use risk-adjusted score if it exists and was enabled
    if "risk_adjusted_score" in df.columns and getattr(fund_engine, "risk_adjusted", False):
        df["undervaluation_score"] = df["risk_adjusted_score"]
        logger.info("Using risk-adjusted scores for ranking")
    
    # Sort final results
    df = df.sort_values("undervaluation_score", ascending=False)

    # 5. Visualize & Output
    # ✅ Pass sectors to viz for colored plots
    viz = ScreenerViz(settings.output_dir, sectors=sectors)
    viz.save_analysis(df, benchmark_data=benchmark_data)

    # ✅ Export top picks separately
    top_picks = df.nlargest(5, "undervaluation_score")[
        ["ticker", "name", "undervaluation_score", "pe_ratio", "rsi_14", "price_change_pct"]
    ].copy()
    if "sector" in df.columns:
        top_picks["sector"] = df.loc[top_picks.index, "sector"]
    
    top_picks_path = settings.output_dir / "top_picks.csv"
    top_picks.to_csv(top_picks_path, index=False)
    logger.info(f"Top 5 picks saved to {top_picks_path}")
    try:
        print("\n🏆 TOP 5 UNDERVALUED PICKS:")
    except UnicodeEncodeError:
        print("\nTOP 5 UNDERVALUED PICKS:")
    print(top_picks.to_string(index=False))

    # Save full results
    output_path = settings.output_dir / "screener_results.csv"
    df.to_csv(output_path, index=False)
    logger.info(f"Full results saved to {output_path}")
    
    # Print summary
    try:
        print(f"\n📊 Analyzed {len(df)} stocks | Avg Score: {df['undervaluation_score'].mean():.3f}")
    except UnicodeEncodeError:
        print(f"\nAnalyzed {len(df)} stocks | Avg Score: {df['undervaluation_score'].mean():.3f}")

if __name__ == "__main__":
    main()