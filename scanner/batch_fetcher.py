"""Batch Kline Fetcher - parallel kline fetching with rate limiting."""
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
import pandas as pd
from loguru import logger
from market.binance_rest import BinanceRestClient


class RateLimiter:
    """Token bucket rate limiter for API requests."""

    def __init__(self, requests_per_second: float = 3.5):
        self._interval = 1.0 / requests_per_second
        self._lock = threading.Lock()
        self._last_request = 0.0

    def acquire(self) -> None:
        with self._lock:
            now = time.time()
            wait = self._interval - (now - self._last_request)
            if wait > 0:
                time.sleep(wait)
            self._last_request = time.time()


class BatchKlineFetcher:
    """Fetches klines for multiple symbols using ThreadPoolExecutor + rate limiting."""

    def __init__(self, rest_client: BinanceRestClient,
                 max_workers: int = 10,
                 requests_per_second: float = 3.5):
        self._rest = rest_client
        self._max_workers = max_workers
        self._limiter = RateLimiter(requests_per_second)
        # Cache: symbol -> (timestamp, DataFrame)
        self._cache: dict[str, tuple[float, pd.DataFrame]] = {}
        self._cache_ttl = 45.0  # seconds

    def fetch_batch(self, symbols: list[str], interval: str = "15m",
                    limit: int = 200) -> dict[str, pd.DataFrame]:
        """Fetch klines for all symbols in parallel.

        Returns dict of symbol -> DataFrame.
        Uses cache to avoid re-fetching within TTL.
        """
        results = {}
        to_fetch = []
        now = time.time()

        # Check cache first
        for symbol in symbols:
            cached = self._cache.get(symbol)
            if cached and (now - cached[0]) < self._cache_ttl:
                results[symbol] = cached[1]
            else:
                to_fetch.append(symbol)

        if not to_fetch:
            return results

        # Parallel fetch with rate limiting
        logger.info(f"Fetching klines for {len(to_fetch)} symbols "
                    f"({len(results)} cached)...")
        start = time.time()

        with ThreadPoolExecutor(max_workers=self._max_workers) as executor:
            futures = {
                executor.submit(self._fetch_one, symbol, interval, limit): symbol
                for symbol in to_fetch
            }
            for future in as_completed(futures):
                symbol = futures[future]
                try:
                    df = future.result()
                    if df is not None and not df.empty:
                        results[symbol] = df
                        self._cache[symbol] = (time.time(), df)
                except Exception as e:
                    logger.debug(f"Kline fetch failed for {symbol}: {e}")

        elapsed = time.time() - start
        logger.info(f"Fetched {len(results)}/{len(symbols)} klines in {elapsed:.1f}s")
        return results

    def _fetch_one(self, symbol: str, interval: str, limit: int) -> pd.DataFrame:
        """Fetch klines for a single symbol with rate limiting."""
        self._limiter.acquire()
        try:
            return self._rest.get_klines(symbol, interval, limit)
        except Exception as e:
            logger.debug(f"Kline error {symbol}: {e}")
            return None

    def clear_cache(self) -> None:
        self._cache.clear()
