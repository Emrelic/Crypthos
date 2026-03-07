"""Timeframe Selector - determines optimal chart timeframe per coin based on
leverage, liquidation distance, and ATR analysis.

Logic:
  1. For each coin, calculate target ATR from leverage:
     theoretical_liq = 1/leverage
     practical_liq = theoretical_liq * 0.70  (Binance liquidates early)
     SL = practical_liq * 0.50
     target_ATR = SL / 2  (SL = 2x ATR rule)

  2. Check ATR across timeframes (1m, 3m, 5m, 15m, 30m, 1h, 4h)
  3. Pick shortest timeframe where ATR <= target_ATR
  4. If no timeframe is safe, mark coin as too volatile for that leverage
"""
import time
import threading
import numpy as np
import pandas as pd
from dataclasses import dataclass
from concurrent.futures import ThreadPoolExecutor, as_completed
from loguru import logger
from market.binance_rest import BinanceRestClient


TIMEFRAMES = ["1m", "3m", "5m", "15m", "30m", "1h", "4h"]
TF_SECONDS = {"1m": 60, "3m": 180, "5m": 300, "15m": 900,
              "30m": 1800, "1h": 3600, "4h": 14400}


@dataclass
class CoinTimeframe:
    """Timeframe analysis result for a single coin."""
    symbol: str
    max_leverage: int
    theoretical_liq_pct: float   # theoretical liq distance %
    practical_liq_pct: float     # practical liq distance % (70%)
    sl_pct: float                # SL point %
    target_atr_pct: float        # target ATR %
    optimal_tf: str              # recommended timeframe ("1m", "5m", etc.)
    optimal_atr_pct: float       # actual ATR at optimal timeframe
    is_safe: bool                # True if ATR <= target at some timeframe
    all_atrs: dict               # {timeframe: atr_pct}


class TimeframeSelector:
    """Calculates optimal timeframe for each coin based on leverage and ATR."""

    def __init__(self, rest_client: BinanceRestClient,
                 config=None,
                 max_workers: int = 8):
        self._rest = rest_client
        self._config = config
        self._max_workers = max_workers
        self._lock = threading.Lock()

        # Cache: updated periodically (not every scan)
        self._cache: dict[str, CoinTimeframe] = {}
        self._cache_time: float = 0
        self._cache_ttl: float = 300  # refresh every 5 minutes

        # Leverage brackets cache
        self._leverage_cache: dict[str, int] = {}

    def get_timeframe(self, symbol: str) -> str:
        """Get optimal timeframe for a symbol. Returns cached value or default."""
        with self._lock:
            ct = self._cache.get(symbol)
            if ct:
                return ct.optimal_tf
        return "1m"  # default fallback

    def get_coin_info(self, symbol: str) -> CoinTimeframe:
        """Get full timeframe analysis for a symbol."""
        with self._lock:
            return self._cache.get(symbol)

    def get_all(self) -> dict[str, CoinTimeframe]:
        """Get all cached coin timeframe data."""
        with self._lock:
            return dict(self._cache)

    def get_safe_symbols(self) -> list[str]:
        """Get symbols that are safe for their max leverage."""
        with self._lock:
            return [s for s, ct in self._cache.items() if ct.is_safe]

    def needs_refresh(self) -> bool:
        """Check if cache needs refreshing."""
        return time.time() - self._cache_time > self._cache_ttl

    def refresh(self, symbols: list[str], leverage_override: int = 0) -> dict[str, CoinTimeframe]:
        """Refresh timeframe analysis for given symbols.

        Args:
            symbols: list of symbol names
            leverage_override: if > 0, use this leverage instead of max
        """
        start = time.time()
        logger.info(f"Timeframe analysis starting for {len(symbols)} coins...")

        # 1. Get leverage brackets if not cached
        if not self._leverage_cache:
            self._fetch_leverage_brackets()

        # 2. Calculate target ATR for each symbol
        targets = {}
        for sym in symbols:
            strat = self._config.get("strategy", {}) if self._config else {}
            liq_factor = strat.get("liq_factor", 70) / 100.0
            sl_factor = strat.get("sl_liq_percent", 50) / 100.0
            atr_divisor = 2.0

            lev = leverage_override if leverage_override > 0 else self._leverage_cache.get(sym, 20)
            theoretical_liq = (1.0 / lev) * 100
            practical_liq = theoretical_liq * liq_factor
            sl_pct = practical_liq * sl_factor
            target_atr = sl_pct / atr_divisor
            targets[sym] = {
                "leverage": lev,
                "theoretical_liq": theoretical_liq,
                "practical_liq": practical_liq,
                "sl_pct": sl_pct,
                "target_atr": target_atr,
            }

        # 3. Fetch ATR for each symbol across timeframes
        # Strategy: start with 1m, only fetch longer TFs if needed
        results = {}
        remaining = list(symbols)

        for tf in TIMEFRAMES:
            if not remaining:
                break

            # Fetch klines for remaining symbols at this timeframe
            atrs = self._batch_fetch_atr(remaining, tf)

            still_remaining = []
            for sym in remaining:
                atr_pct = atrs.get(sym)
                t = targets[sym]

                if sym not in results:
                    results[sym] = {"all_atrs": {}, **t}

                if atr_pct is not None:
                    results[sym]["all_atrs"][tf] = atr_pct

                    # Check if this TF is safe
                    if atr_pct <= t["target_atr"]:
                        results[sym]["optimal_tf"] = tf
                        results[sym]["optimal_atr"] = atr_pct
                        results[sym]["is_safe"] = True
                        # Don't need to check longer TFs for this symbol
                        continue

                still_remaining.append(sym)

            remaining = still_remaining

        # 4. Handle symbols that didn't find a safe TF
        for sym in remaining:
            r = results[sym]
            if "optimal_tf" not in r:
                # Use the TF with lowest ATR as fallback
                all_atrs = r["all_atrs"]
                if all_atrs:
                    best_tf = min(all_atrs, key=all_atrs.get)
                    r["optimal_tf"] = best_tf
                    r["optimal_atr"] = all_atrs[best_tf]
                else:
                    r["optimal_tf"] = "1m"
                    r["optimal_atr"] = 0
                r["is_safe"] = False

        # 5. Build CoinTimeframe objects and cache
        with self._lock:
            for sym, r in results.items():
                ct = CoinTimeframe(
                    symbol=sym,
                    max_leverage=r["leverage"],
                    theoretical_liq_pct=r["theoretical_liq"],
                    practical_liq_pct=r["practical_liq"],
                    sl_pct=r["sl_pct"],
                    target_atr_pct=r["target_atr"],
                    optimal_tf=r["optimal_tf"],
                    optimal_atr_pct=r["optimal_atr"],
                    is_safe=r["is_safe"],
                    all_atrs=r["all_atrs"],
                )
                self._cache[sym] = ct

            self._cache_time = time.time()

        elapsed = time.time() - start
        safe_count = sum(1 for r in results.values() if r["is_safe"])
        logger.info(f"Timeframe analysis done in {elapsed:.1f}s: "
                    f"{safe_count}/{len(symbols)} safe, "
                    f"{len(symbols)-safe_count} risky")

        return {s: self._cache[s] for s in symbols if s in self._cache}

    def _fetch_leverage_brackets(self) -> None:
        """Fetch max leverage for all symbols from Binance."""
        try:
            brackets = self._rest.get_leverage_brackets()
            for item in brackets:
                sym = item.get("symbol", "")
                br = item.get("brackets", [])
                if br:
                    self._leverage_cache[sym] = br[0].get("initialLeverage", 20)
            logger.info(f"Loaded leverage brackets for {len(self._leverage_cache)} symbols")
        except Exception as e:
            logger.error(f"Failed to fetch leverage brackets: {e}")

    def _batch_fetch_atr(self, symbols: list[str], timeframe: str) -> dict[str, float]:
        """Fetch ATR% for multiple symbols at a given timeframe. Returns {symbol: atr_pct}."""
        results = {}

        def fetch_one(sym):
            try:
                klines = self._rest.get_klines(sym, timeframe, limit=50)
                if klines is None or klines.empty or len(klines) < 20:
                    return sym, None
                highs = klines["high"].values.astype(float)
                lows = klines["low"].values.astype(float)
                closes = klines["close"].values.astype(float)
                price = closes[-1]
                if price <= 0:
                    return sym, None

                tr = np.maximum(
                    highs[1:] - lows[1:],
                    np.maximum(
                        np.abs(highs[1:] - closes[:-1]),
                        np.abs(lows[1:] - closes[:-1])
                    )
                )
                atr14 = np.mean(tr[-14:])
                atr_pct = (atr14 / price) * 100
                return sym, atr_pct
            except Exception:
                return sym, None

        with ThreadPoolExecutor(max_workers=self._max_workers) as executor:
            futures = {executor.submit(fetch_one, sym): sym for sym in symbols}
            for future in as_completed(futures):
                try:
                    sym, atr_pct = future.result()
                    if atr_pct is not None:
                        results[sym] = atr_pct
                except Exception:
                    pass
            # Small delay between timeframe batches for rate limiting
            time.sleep(0.5)

        return results
