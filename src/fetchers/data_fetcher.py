# src/fetchers/data_fetcher.py --revision 2
"""
yfinance access with a windowed on-disk cache.

Revision 2 fixes two data-supply defects (docs/AUDIT.md):

  * The lookback was `period=f"{data_retention_days}d"` -- a *retention* setting
    reused as a *lookback*. Every series was capped at ~30 bars, so MA_20 had ~10
    valid points and momentum could not be computed at all. Now uses
    `settings.history_period` (default "2y").

  * The cache key was `f"{ticker}.pkl"` with no window in it, so widening the
    lookback kept serving the stale 30-bar frame forever. The key now carries the
    period, making the old cache miss instead of silently degrading results.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Optional

import joblib
import pandas as pd
import yfinance as yf
from tenacity import retry, stop_after_attempt, wait_exponential

from core.config import Settings
from core.logger import setup_logger

logger = setup_logger(Path("logs"))

_INDEX_PREFIXES = ("^", ".JKSE", ".KS11")

# yfinance -> our column names.
_FUNDAMENTAL_MAP = {
    "market_cap": "marketCap",
    "pe_ratio": "trailingPE",
    "price_to_book": "priceToBook",
    "dividend_yield": "dividendYield",
    "beta": "beta",
    "roe": "returnOnEquity",
    "gross_margin": "grossMargins",
    "debt_to_equity": "debtToEquity",
}


def is_index(ticker: str) -> bool:
    return any(ticker.startswith(p) for p in _INDEX_PREFIXES)


def repair_price_to_book(info: dict, fx_usd_idr: Optional[float]) -> tuple[Optional[float], Optional[str]]:
    """
    Recompute P/B when the share price and the book value are in different currencies.

    17 of our 49 names (ADRO, ITMG, INCO, BRPT, PTRO, ...) report financials in USD
    while their shares trade in IDR. yfinance divides the IDR price by the USD book
    value per share and reports the result as `priceToBook`, producing ADRO 15,000
    and PTRO 203,846. Left alone those get nullified by the sanity bound, silently
    stripping the value factor from a third of the universe.

    `trailingEps` is already converted to IDR, so `trailingPE` needs no repair --
    only `bookValue` is left in the reporting currency.

    Returns (price_to_book, note).
    """
    reported = info.get("priceToBook")
    fin_cur = (info.get("financialCurrency") or "").upper()
    quote_cur = (info.get("currency") or "").upper()

    if not fin_cur or not quote_cur or fin_cur == quote_cur:
        return reported, None

    book_value = info.get("bookValue")
    price = info.get("currentPrice") or info.get("previousClose")
    if fin_cur != "USD" or quote_cur != "IDR" or not book_value or not price or not fx_usd_idr:
        return reported, f"price_to_book:currency_mismatch({fin_cur}/{quote_cur})_unrepaired"

    if book_value <= 0:
        return None, "price_to_book:nonpositive_book_value"

    return price / (book_value * fx_usd_idr), f"price_to_book:repaired_{fin_cur}_to_{quote_cur}"


class DataFetcher:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.cache_dir = Path("data/cache")
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._fx_cache: Optional[float] = None

    def _cache_path(self, ticker: str, period: str) -> Path:
        safe = ticker.replace("/", "_").replace("\\", "_")
        return self.cache_dir / f"{safe}__{period}.pkl"

    def _fresh(self, path: Path) -> bool:
        """
        Within the TTL. One rule, so prices and fundamentals cannot disagree.

        `time.time()`, not `pd.Timestamp.now().timestamp()`. A naive Timestamp is
        assumed to be UTC when converted, while `st_mtime` is a true epoch value --
        so on a UTC+7 machine every file read as seven hours older than it was and
        the cache never hit once. The symptom was a "cached" app that refetched all
        49 tickers on every single run.
        """
        if not path.exists():
            return False
        return (time.time() - path.stat().st_mtime) < self.settings.cache_ttl_minutes * 60

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=10), reraise=True)
    def _fetch_single(self, ticker: str, period: Optional[str] = None) -> pd.DataFrame:
        period = period or self.settings.history_period
        cache_path = self._cache_path(ticker, period)

        if self._fresh(cache_path):
            try:
                cached = joblib.load(cache_path)
                logger.debug(f"Cache hit: {ticker} ({period})")
                return cached
            except Exception as e:
                # Swallow deliberately: letting this escape would make @retry
                # reload the same corrupt pickle three times before failing.
                logger.warning(f"Corrupt cache for {ticker}, refetching: {e}")

        df = yf.download(
            ticker,
            period=period,
            auto_adjust=True,
            progress=False,
            timeout=self.settings.yfinance_timeout,
        )
        if df is None or df.empty:
            raise ValueError(f"Empty data for {ticker}")

        if df.columns.nlevels > 1:
            df.columns = df.columns.droplevel(1)
        if "Close" in df.columns:
            df = df[df["Close"].notna()]
        if df.empty:
            raise ValueError(f"No valid Close rows for {ticker}")

        joblib.dump(df, cache_path)
        logger.info(f"Fetched {ticker}: {len(df)} bars ({period})")
        return df

    def usd_idr_rate(self) -> Optional[float]:
        """Spot USD/IDR, cached for the session. None if unavailable (offline)."""
        if self._fx_cache is not None:
            return self._fx_cache
        try:
            fx = self._fetch_single("IDR=X", period="5d")
            self._fx_cache = float(fx["Close"].dropna().iloc[-1])
            logger.info(f"USD/IDR = {self._fx_cache:,.0f}")
        except Exception as e:
            logger.warning(f"USD/IDR unavailable, P/B repair disabled: {e}")
            self._fx_cache = None
        return self._fx_cache

    def fetch_fundamentals(self, tickers: dict) -> list[dict]:
        """
        One record per name, cached on disk exactly like prices.

        This was the uncached half of the pipeline: `yf.Ticker(t).info` is a slow
        call and it ran for all 49 names on every single launch, which is where
        most of the forty seconds went. Fundamentals move on a quarterly cycle --
        re-reading them within the same hour was never buying anything.
        """
        records = []
        fx = self.usd_idr_rate()
        repaired = 0
        hits = 0

        for ticker, name in tickers.items():
            if is_index(ticker):
                logger.debug(f"Skipping fundamentals for index: {ticker}")
                continue

            cache_path = self._cache_path(ticker, "info")
            if self._fresh(cache_path):
                try:
                    cached = joblib.load(cache_path)
                    # The display name comes from settings, not from the cache: a
                    # renamed ticker in configs must not be overridden by whatever
                    # it was called when the file was written.
                    records.append({**cached, "name": name})
                    hits += 1
                    continue
                except Exception as e:
                    logger.warning(f"Corrupt fundamentals cache for {ticker}: {e}")

            try:
                info = yf.Ticker(ticker).info or {}
                record = {"ticker": ticker, "name": name}
                for our_key, yf_key in _FUNDAMENTAL_MAP.items():
                    record[our_key] = info.get(yf_key)

                ptb, note = repair_price_to_book(info, fx)
                record["price_to_book"] = ptb
                record["fetch_note"] = note or ""
                if note and note.startswith("price_to_book:repaired"):
                    repaired += 1

                try:
                    joblib.dump(record, cache_path)
                except Exception as e:
                    logger.warning(f"Could not cache fundamentals for {ticker}: {e}")

                records.append(record)
            except Exception as e:
                logger.error(f"Failed fundamentals for {ticker}: {e}")

        logger.info(
            f"Fundamentals for {len(records)}/{len(tickers)} tickers "
            f"({hits} cached, {repaired} P/B repaired for USD reporters)"
        )
        return records

    def fetch_technical_data(self, tickers, period: Optional[str] = None) -> dict[str, pd.DataFrame]:
        data = {}
        for ticker in tickers:
            try:
                data[ticker] = self._fetch_single(ticker, period=period)
            except Exception as e:
                logger.error(f"Failed technical data for {ticker}: {e}")
        return data
