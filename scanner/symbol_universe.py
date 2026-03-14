"""Symbol Universe - maintains the list of tradeable Binance Futures symbols,
filtered by volume, sorted by 24h quote volume."""
from loguru import logger
from market.binance_rest import BinanceRestClient


EXCLUDED_SYMBOLS = {"BUSDUSDT", "USDCUSDT", "TUSDUSDT", "DAIUSDT", "EURUSDT", "GBPUSDT"}

# TradFi symbols that require special agreement on Binance
TRADFI_PREFIXES = ("XAG", "XAU", "XPT", "XPD")  # silver, gold, platinum, palladium


class SymbolUniverse:
    """Fetches and filters Binance Futures symbols for scanning."""

    def __init__(self, rest_client: BinanceRestClient,
                 top_n: int = 50,
                 min_volume_usdt: float = 5_000_000):
        self._rest = rest_client
        self._top_n = top_n
        self._min_volume = min_volume_usdt
        self._spike_threshold = 3.0   # minimum absolute % price change for spike detection
        self._max_spikes = 20         # max additional spike symbols
        self._symbols: list[str] = []
        self._ticker_data: dict[str, dict] = {}

    def refresh(self) -> list[str]:
        """Fetch all 24h tickers, filter and sort by volume, return top N symbols."""
        try:
            tickers = self._rest.get_all_24h_tickers()
        except Exception as e:
            logger.error(f"Failed to fetch tickers: {e}")
            return self._symbols

        self._ticker_data.clear()
        candidates = []

        for t in tickers:
            symbol = t.get("symbol", "")
            # Only USDT perpetual pairs
            if not symbol.endswith("USDT"):
                continue
            if symbol in EXCLUDED_SYMBOLS:
                continue
            if symbol.startswith(TRADFI_PREFIXES):
                continue

            volume_24h = float(t.get("quoteVolume", 0))
            if volume_24h < self._min_volume:
                continue

            data = {
                "symbol": symbol,
                "price": float(t.get("lastPrice", 0)),
                "price_change_pct": float(t.get("priceChangePercent", 0)),
                "high_24h": float(t.get("highPrice", 0)),
                "low_24h": float(t.get("lowPrice", 0)),
                "volume_24h": volume_24h,
                "trades_24h": int(t.get("count", 0)),
                "weighted_avg_price": float(t.get("weightedAvgPrice", 0)),
            }
            self._ticker_data[symbol] = data
            candidates.append((symbol, volume_24h))

        # Sort by volume descending, take top N
        candidates.sort(key=lambda x: x[1], reverse=True)
        top_symbols = [s for s, _ in candidates[:self._top_n]]

        # Volume spike detection: coins with extreme price movement (breakout candidates)
        # These may not be in top N by volume but show unusual activity
        top_set = set(top_symbols)
        spike_symbols = []

        for symbol, vol in candidates[self._top_n:]:
            if len(spike_symbols) >= self._max_spikes:
                break
            data = self._ticker_data.get(symbol, {})
            abs_change = abs(data.get("price_change_pct", 0))
            if abs_change >= self._spike_threshold and symbol not in top_set:
                spike_symbols.append(symbol)

        self._symbols = top_symbols + spike_symbols

        if spike_symbols:
            logger.info(f"SymbolUniverse: {len(spike_symbols)} spike symbols detected "
                        f"(|price_change| >= {self._spike_threshold}%): "
                        f"{', '.join(spike_symbols[:5])}{'...' if len(spike_symbols) > 5 else ''}")
        logger.info(f"SymbolUniverse: {len(self._symbols)} symbols "
                    f"({len(top_symbols)} top + {len(spike_symbols)} spikes, "
                    f"from {len(candidates)} above min volume)")
        return self._symbols

    def get_symbols(self) -> list[str]:
        return self._symbols

    def get_ticker(self, symbol: str) -> dict:
        return self._ticker_data.get(symbol, {})

    def get_all_tickers(self) -> dict[str, dict]:
        return self._ticker_data

    @property
    def count(self) -> int:
        return len(self._symbols)
