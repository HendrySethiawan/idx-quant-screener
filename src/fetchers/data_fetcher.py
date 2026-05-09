# src/fetchers/data_fetcher.py
import yfinance as yf
import pandas as pd
import numpy as np
import joblib
from pathlib import Path
from tenacity import retry, stop_after_attempt, wait_exponential
from core.config import Settings
from core.logger import setup_logger

logger = setup_logger(Path("logs"))

class DataFetcher:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.cache_dir = Path("data/cache")
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=10), reraise=True)
    def _fetch_single(self, ticker: str) -> pd.DataFrame:
        cache_path = self.cache_dir / f"{ticker}.pkl"
        if cache_path.exists():
            cached = joblib.load(cache_path)
            cache_age = pd.Timestamp.now().timestamp() - cache_path.stat().st_mtime
            if cache_age < self.settings.cache_ttl_minutes * 60:
                logger.info(f"Cache hit: {ticker}")
                return cached
        
        df = yf.download(ticker, period=f"{self.settings.data_retention_days}d", auto_adjust=True, timeout=self.settings.yfinance_timeout)
        if df.empty:
            raise ValueError(f"Empty data for {ticker}")
        
        if df.columns.nlevels > 1:
            df.columns = df.columns.droplevel(1)
        
        joblib.dump(df, cache_path)
        logger.info(f"Fetched: {ticker}")
        return df

    def fetch_fundamentals(self, tickers: dict) -> list[dict]:
        records = []
        # ✅ Skip index tickers (they have no fundamentals)
        index_prefixes = ["^", ".JKSE", ".KS11"]  # Add other index patterns as needed
        
        for ticker, name in tickers.items():
            # Skip if looks like an index
            if any(ticker.startswith(prefix) for prefix in index_prefixes):
                logger.info(f"Skipping fundamentals for index: {ticker}")
                continue
                
            try:
                info = yf.Ticker(ticker).info
                records.append({
                    "ticker": ticker,
                    "name": name,
                    "market_cap": info.get("marketCap"),
                    "pe_ratio": info.get("trailingPE"),
                    "price_to_book": info.get("priceToBook"),
                    "dividend_yield": info.get("dividendYield"),
                    "beta": info.get("beta", 1.0)
                })
                logger.info(f"Fundamentals fetched: {ticker}")
            except Exception as e:
                logger.error(f"Failed fundamentals for {ticker}: {e}")
        return records

    def fetch_technical_data(self, tickers: dict) -> dict[str, pd.DataFrame]:
        data = {}
        for ticker in tickers:
            try:
                data[ticker] = self._fetch_single(ticker)
            except Exception as e:
                logger.error(f"Failed technical data for {ticker}: {e}")
        return data
