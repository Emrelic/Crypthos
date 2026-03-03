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
