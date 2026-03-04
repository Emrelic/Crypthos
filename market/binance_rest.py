import requests
import pandas as pd
from loguru import logger
from core.constants import BINANCE_FUTURES_REST


class BinanceRestClient:
    BASE_URL = BINANCE_FUTURES_REST

    def __init__(self, session: requests.Session = None):
        self._session = session or requests.Session()
        self._session.headers.update({"Accept": "application/json"})

    def _get(self, endpoint: str, params: dict = None) -> dict | list:
        url = f"{self.BASE_URL}{endpoint}"
        try:
            resp = self._session.get(url, params=params, timeout=10)
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as e:
            logger.error(f"REST API error [{endpoint}]: {e}")
            raise

    def get_ticker_price(self, symbol: str) -> dict:
        return self._get("/fapi/v1/ticker/price", {"symbol": symbol})

    def get_premium_index(self, symbol: str) -> dict:
        return self._get("/fapi/v1/premiumIndex", {"symbol": symbol})

    def get_klines(self, symbol: str, interval: str = "15m", limit: int = 500) -> pd.DataFrame:
        data = self._get("/fapi/v1/klines", {
            "symbol": symbol,
            "interval": interval,
            "limit": limit,
        })
        df = pd.DataFrame(data, columns=[
            "timestamp", "open", "high", "low", "close", "volume",
            "close_time", "quote_volume", "trades", "taker_buy_volume",
            "taker_buy_quote_volume", "ignore",
        ])
        for col in ["open", "high", "low", "close", "volume"]:
            df[col] = df[col].astype(float)
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
        return df

    def get_funding_rate(self, symbol: str, limit: int = 10) -> list:
        return self._get("/fapi/v1/fundingRate", {
            "symbol": symbol,
            "limit": limit,
        })

    def get_exchange_info(self, symbol: str = None) -> dict:
        data = self._get("/fapi/v1/exchangeInfo")
        if symbol:
            for s in data.get("symbols", []):
                if s["symbol"] == symbol:
                    return s
            return {}
        return data

    def get_all_ticker_prices(self) -> list:
        return self._get("/fapi/v1/ticker/price")

    def get_24h_ticker(self, symbol: str) -> dict:
        return self._get("/fapi/v1/ticker/24hr", {"symbol": symbol})

    def get_all_24h_tickers(self) -> list:
        """Get 24h ticker data for ALL symbols in one request."""
        return self._get("/fapi/v1/ticker/24hr")

    def get_leverage_bracket(self, symbol: str) -> list:
        """Get leverage brackets for a symbol from /fapi/v1/leverageBracket."""
        try:
            data = self._get("/fapi/v1/leverageBracket", {"symbol": symbol})
            if isinstance(data, list) and data:
                return data[0].get("brackets", [])
            return []
        except Exception as e:
            logger.warning(f"Failed to get leverage brackets for {symbol}: {e}")
            return []

    def get_max_leverage(self, symbol: str, notional: float = 100.0,
                         fallback: int = 75) -> int:
        """Return the max available leverage for a given notional size.
        Note: leverageBracket endpoint requires API key. If unavailable,
        returns fallback value (most coins support 75x for small positions).
        """
        try:
            brackets = self.get_leverage_bracket(symbol)
            if not brackets:
                return fallback
            for b in sorted(brackets, key=lambda x: x.get("bracket", 99)):
                floor = float(b.get("notionalFloor", 0))
                cap = float(b.get("notionalCap", float("inf")))
                if floor <= notional < cap:
                    return int(b.get("initialLeverage", fallback))
            if brackets:
                return int(brackets[0].get("initialLeverage", fallback))
        except Exception as e:
            logger.warning(f"Failed to get max leverage for {symbol}: {e}")
        return fallback
