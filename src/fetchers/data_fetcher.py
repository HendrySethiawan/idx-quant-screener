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


def session_report(price_data: dict, market_session=None) -> dict:
    """
    Which session the prices are from, and whether they all agree.

    Two separate facts, and the page needs both:

      * `session_date` -- the newest bar we hold. This is what every price on
        screen is, and stating the fetch time instead is what let a page of
        21 August closes present itself as current.
      * `laggards` -- names whose newest bar is older than that. Every score here
        is cross-sectional, a z-score against peers, so ranking names priced on
        different days is wrong in a way no single figure admits to. It happens:
        50 tickers on one session and 1 on another, in real data.

    `behind` is only claimed when `market_session` is known. Not knowing must never
    read as being up to date.
    """
    dated = {}
    for ticker, frame in (price_data or {}).items():
        if frame is None or getattr(frame, "empty", True):
            continue
        try:
            dated[ticker] = pd.Timestamp(frame.index.max()).tz_localize(None)
        except (TypeError, ValueError):
            dated[ticker] = pd.Timestamp(frame.index.max())

    if not dated:
        return {"session_date": None, "laggards": [], "mixed": False, "behind": None}

    newest = max(dated.values())
    laggards = sorted(
        ((t, d) for t, d in dated.items() if d < newest), key=lambda kv: kv[1])

    behind = None
    if market_session is not None:
        try:
            market = pd.Timestamp(market_session).tz_localize(None)
        except (TypeError, ValueError):
            market = pd.Timestamp(market_session)
        behind = bool(newest < market)

    return {
        "session_date": newest,
        "laggards": [(t, d.date().isoformat()) for t, d in laggards],
        "mixed": bool(laggards),
        "behind": behind,
        "market_session": None if market_session is None else pd.Timestamp(market_session),
    }


class DataFetcher:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.cache_dir = Path("data/cache")
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._fx_cache: Optional[float] = None
        # The newest session the market actually traded, learned from the intraday
        # probe. None when it could not be established -- which must read as "not
        # known", never as "we are up to date".
        self.latest_market_session = None

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

        df = self._keep_known_sessions(df, cache_path, ticker)

        joblib.dump(df, cache_path)
        logger.info(f"Fetched {ticker}: {len(df)} bars ({period})")
        return df

    def _keep_known_sessions(self, fresh: pd.DataFrame, cache_path: Path,
                             ticker: str) -> pd.DataFrame:
        """
        Union the fetch with what was already cached, newest fetch winning ties.

        A refetch used to overwrite the cache wholesale, so a vendor that stops
        serving a session simply deleted it from our history. That is exactly what
        happened: Yahoo served the 24 Aug IDX close, then withdrew it, and the next
        run replaced a good cache with one a session shorter -- silently, and the
        page went on presenting it as current.

        A session that traded did not un-trade. So a date only the cache has is
        kept, while a date both have takes the fresh value, because revisions are
        real and should land.
        """
        if not cache_path.exists():
            return fresh
        try:
            cached = joblib.load(cache_path)
        except Exception:
            return fresh
        if not isinstance(cached, pd.DataFrame) or cached.empty:
            return fresh

        missing = cached.index.difference(fresh.index)
        if not len(missing):
            return fresh

        merged = pd.concat([fresh, cached.loc[missing]]).sort_index()
        merged = merged[~merged.index.duplicated(keep="first")]
        logger.info(f"{ticker}: kept {len(missing)} session(s) the fetch no longer "
                    f"carries (newest {missing.max().date()})")
        return merged

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

        if period is None:
            # Only for the standard daily panel. A caller asking for "max" or "5y"
            # wants history, not the last session, and topping that up would make
            # the backtest disagree with itself between runs.
            data = self.top_up_last_session(data)
        return data

    # ------------------------------------------------------- the missing session
    #
    # Yahoo's DAILY feed dropped the 24 Aug IDX close while its INTRADAY feed still
    # served it. Aggregating 60m bars reproduces the daily bar exactly -- verified
    # digit-for-digit against Yahoo's own daily bars for 20 and 21 Aug, and against
    # the broker's screen for 24 Aug. So the gap is filled from the same vendor
    # rather than by adding a second one.
    INTRADAY_INTERVAL = "60m"
    INTRADAY_PERIOD = "5d"

    def _exchange_tz(self) -> str:
        return str((self.settings.regime or {}).get("exchange_tz", "Asia/Jakarta"))

    @staticmethod
    def _sessions_from_intraday(frame: pd.DataFrame, tz: str) -> pd.DataFrame:
        """
        Daily OHLC per session, from intraday bars.

        Volume is deliberately absent. Intraday sums to roughly 60% of the official
        daily figure because the opening auction and off-book prints are missing,
        and a volume 40% short feeding a liquidity floor is worse than no volume at
        all -- the floor decides whether a name can be exited.
        """
        if frame is None or frame.empty or "Close" not in frame:
            return pd.DataFrame()

        local = frame.dropna(subset=["Close"]).copy()
        if local.empty:
            return pd.DataFrame()

        index = pd.DatetimeIndex(local.index)
        # The batched download comes back in UTC; the session a bar belongs to is a
        # question about the exchange's day, not ours.
        index = index.tz_localize("UTC") if index.tz is None else index
        local["_session"] = index.tz_convert(tz).date

        grouped = local.groupby("_session")
        out = pd.DataFrame({
            "Open": grouped["Open"].first() if "Open" in local else grouped["Close"].first(),
            "High": grouped["High"].max() if "High" in local else grouped["Close"].max(),
            "Low": grouped["Low"].min() if "Low" in local else grouped["Close"].min(),
            "Close": grouped["Close"].last(),
        })
        out.index = pd.DatetimeIndex(out.index)
        return out

    def _latest_market_session(self, probe_ticker: str):
        """
        The newest session the market has actually traded, or None if unknown.

        One cheap request against the benchmark. When the daily panel already
        reaches it, nothing further is fetched and an ordinary day costs this alone.
        """
        try:
            raw = yf.Ticker(probe_ticker).history(
                period=self.INTRADAY_PERIOD, interval=self.INTRADAY_INTERVAL,
                auto_adjust=True,
            )
        except Exception as e:
            logger.warning(f"Could not probe the latest session: {e}")
            return None
        sessions = self._sessions_from_intraday(raw, self._exchange_tz())
        return None if sessions.empty else sessions.index.max()

    def top_up_last_session(self, data: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
        """
        Rebuild sessions the daily feed is missing, from intraday bars.

        Returns the same dict. Never raises: a top-up that fails leaves the daily
        data exactly as it was, which is the behaviour this replaces.
        """
        if not data:
            return data

        # Always the index, never whichever ticker happens to be first in the dict.
        # A thinly traded name that did not print on the latest session would report
        # the market as older than it is, and the top-up would decline to run for
        # the whole universe -- silently, which is the failure mode this exists to
        # end. The index trades whenever the exchange is open.
        probe = str((self.settings.regime or {}).get("benchmark", "^JKSE"))
        latest = self._latest_market_session(probe)
        self.latest_market_session = latest
        if latest is None:
            return data

        behind = [t for t, df in data.items()
                  if df is not None and not df.empty and df.index.max() < latest]
        if not behind:
            logger.info(f"Daily data already reaches {latest.date()}")
            return data

        logger.info(f"Daily feed is behind for {len(behind)} of {len(data)} tickers; "
                    f"rebuilding up to {latest.date()} from "
                    f"{self.INTRADAY_INTERVAL} bars")
        try:
            intraday = yf.download(
                behind, period=self.INTRADAY_PERIOD, interval=self.INTRADAY_INTERVAL,
                auto_adjust=True, progress=False, group_by="ticker", threads=True,
                timeout=self.settings.yfinance_timeout,
            )
        except Exception as e:
            logger.warning(f"Could not fetch intraday bars: {e}")
            return data
        if intraday is None or intraday.empty:
            return data

        tz = self._exchange_tz()
        filled = 0
        for ticker in behind:
            try:
                frame = (intraday[ticker] if intraday.columns.nlevels > 1
                         and ticker in intraday.columns.get_level_values(0)
                         else intraday)
                sessions = self._sessions_from_intraday(frame, tz)
                if sessions.empty:
                    continue

                daily = data[ticker]
                extra = sessions[sessions.index > daily.index.max()]
                if extra.empty:
                    continue

                # Match whatever the daily index carries, or concat misaligns them.
                if daily.index.tz is not None:
                    extra.index = extra.index.tz_localize(daily.index.tz)
                extra = extra.reindex(columns=daily.columns)
                extra["synthetic"] = True

                merged = pd.concat([daily.assign(synthetic=False), extra]).sort_index()
                data[ticker] = merged[~merged.index.duplicated(keep="last")]
                filled += 1
                self._recache(ticker, data[ticker])
            except Exception as e:
                logger.warning(f"Could not rebuild the last session for {ticker}: {e}")

        logger.info(f"Rebuilt the missing session for {filled} of {len(behind)} tickers")
        return data

    def _recache(self, ticker: str, frame: pd.DataFrame) -> None:
        """Write the topped-up frame back, so the next run starts from it."""
        try:
            joblib.dump(frame, self._cache_path(ticker, self.settings.history_period))
        except Exception as e:
            logger.warning(f"Could not cache the rebuilt session for {ticker}: {e}")
