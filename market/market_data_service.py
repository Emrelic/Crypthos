import threading
import time
import pandas as pd
from loguru import logger
from core.event_bus import EventBus
from core.config_manager import ConfigManager
from core.constants import EventType
from market.binance_rest import BinanceRestClient
from market.binance_ws import BinanceWebSocket
from market.symbol_info import SymbolInfo


class MarketDataService:
    """Facade over REST + WebSocket. Manages data freshness and caching."""

    def __init__(self, config: ConfigManager, event_bus: EventBus):
        self._config = config
        self._event_bus = event_bus
        self._rest = BinanceRestClient()
        self._ws = BinanceWebSocket(event_bus)

        self._current_symbol: str = ""
        self._price_cache: dict[str, float] = {}
        self._kline_cache: dict[str, pd.DataFrame] = {}
        self._funding_cache: dict[str, dict] = {}
        self._symbol_info_cache: dict[str, SymbolInfo] = {}
        self._ticker_cache: dict[str, dict] = {}

        self._kline_thread = None
        self._running = False

        self._event_bus.subscribe(EventType.PRICE_UPDATE, self._on_price_update)
        self._event_bus.subscribe(EventType.FUNDING_UPDATE, self._on_funding_update)

    def start(self, symbol: str) -> None:
        self._current_symbol = symbol
        self._running = True

        # Initial data fetch via REST
        self._fetch_initial_data(symbol)

        # Start WebSocket
        self._ws.connect(symbol)

        # Start periodic kline refresh
        self._kline_thread = threading.Thread(
            target=self._kline_refresh_loop, daemon=True
        )
        self._kline_thread.start()

        logger.info(f"MarketDataService started for {symbol}")

    def stop(self) -> None:
        self._running = False
        self._ws.disconnect()

    def switch_symbol(self, symbol: str) -> None:
        self._current_symbol = symbol
        self._fetch_initial_data(symbol)
        self._ws.switch_symbol(symbol)

    def get_price(self, symbol: str = None) -> float:
        symbol = symbol or self._current_symbol
        return self._price_cache.get(symbol, 0.0)

    def get_klines(self, symbol: str = None, interval: str = None,
                   limit: int = None) -> pd.DataFrame:
        symbol = symbol or self._current_symbol
        if symbol not in self._kline_cache:
            self._refresh_klines(symbol, interval, limit)
        return self._kline_cache.get(symbol, pd.DataFrame())

    def get_funding_rate(self, symbol: str = None) -> dict:
        symbol = symbol or self._current_symbol
        return self._funding_cache.get(symbol, {})

    def get_symbol_info(self, symbol: str = None) -> SymbolInfo:
        symbol = symbol or self._current_symbol
        if symbol not in self._symbol_info_cache:
            self._fetch_symbol_info(symbol)
        return self._symbol_info_cache.get(symbol)

    def get_ticker(self, symbol: str = None) -> dict:
        symbol = symbol or self._current_symbol
        return self._ticker_cache.get(symbol, {})

    def _fetch_initial_data(self, symbol: str) -> None:
        try:
            # Price
            ticker = self._rest.get_ticker_price(symbol)
            self._price_cache[symbol] = float(ticker.get("price", 0))

            # Klines
            self._refresh_klines(symbol)

            # Funding
            premium = self._rest.get_premium_index(symbol)
            self._funding_cache[symbol] = {
                "funding_rate": float(premium.get("lastFundingRate", 0)),
                "mark_price": float(premium.get("markPrice", 0)),
                "index_price": float(premium.get("indexPrice", 0)),
                "next_funding_time": premium.get("nextFundingTime", 0),
            }

            # Symbol info
            self._fetch_symbol_info(symbol)

            # 24h ticker
            try:
                t24 = self._rest.get_24h_ticker(symbol)
                self._ticker_cache[symbol] = {
                    "high_24h": float(t24.get("highPrice", 0)),
                    "low_24h": float(t24.get("lowPrice", 0)),
                    "volume_24h": float(t24.get("quoteVolume", 0)),
                    "price_change_pct": float(t24.get("priceChangePercent", 0)),
                }
            except Exception:
                pass

            logger.info(f"Initial data fetched for {symbol}: "
                        f"price={self._price_cache[symbol]}")
        except Exception as e:
            logger.error(f"Failed to fetch initial data for {symbol}: {e}")

    def _refresh_klines(self, symbol: str = None, interval: str = None,
                        limit: int = None) -> None:
        symbol = symbol or self._current_symbol
        interval = interval or self._config.get("indicators.kline_interval", "15m")
        limit = limit or self._config.get("indicators.kline_limit", 500)
        try:
            df = self._rest.get_klines(symbol, interval, limit)
            self._kline_cache[symbol] = df
        except Exception as e:
            logger.error(f"Kline refresh failed for {symbol}: {e}")

    def _fetch_symbol_info(self, symbol: str) -> None:
        try:
            info = self._rest.get_exchange_info(symbol)
            if info:
                self._symbol_info_cache[symbol] = SymbolInfo.from_exchange_info(info)
        except Exception as e:
            logger.error(f"Symbol info fetch failed for {symbol}: {e}")

    def _kline_refresh_loop(self) -> None:
        while self._running:
            time.sleep(30)
            if self._running and self._current_symbol:
                self._refresh_klines(self._current_symbol)
                # Also refresh funding data
                try:
                    premium = self._rest.get_premium_index(self._current_symbol)
                    self._funding_cache[self._current_symbol] = {
                        "funding_rate": float(premium.get("lastFundingRate", 0)),
                        "mark_price": float(premium.get("markPrice", 0)),
                        "index_price": float(premium.get("indexPrice", 0)),
                        "next_funding_time": premium.get("nextFundingTime", 0),
                    }
                except Exception:
                    pass

    def _on_price_update(self, data: dict) -> None:
        symbol = data.get("symbol", self._current_symbol)
        self._price_cache[symbol] = data.get("price", 0.0)
        # Update ticker cache with real-time data
        if symbol in self._ticker_cache:
            self._ticker_cache[symbol].update({
                k: data[k] for k in ["high_24h", "low_24h", "volume_24h", "price_change_pct"]
                if k in data
            })
        else:
            self._ticker_cache[symbol] = {
                "high_24h": data.get("high_24h", 0),
                "low_24h": data.get("low_24h", 0),
                "volume_24h": data.get("volume_24h", 0),
                "price_change_pct": data.get("price_change_pct", 0),
            }

    def _on_funding_update(self, data: dict) -> None:
        symbol = data.get("symbol", self._current_symbol)
        self._funding_cache[symbol] = {
            "funding_rate": data.get("funding_rate", 0),
            "mark_price": data.get("mark_price", 0),
            "index_price": data.get("index_price", 0),
            "next_funding_time": data.get("next_funding_time", 0),
        }
