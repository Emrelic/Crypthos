import time
import hmac
import hashlib
from urllib.parse import urlencode

import requests
import pandas as pd
from loguru import logger
from core.constants import BINANCE_FUTURES_REST


class BinanceRestClient:
    BASE_URL = BINANCE_FUTURES_REST

    def __init__(self, session: requests.Session = None,
                 api_key: str = "", api_secret: str = ""):
        self._session = session or requests.Session()
        self._session.headers.update({"Accept": "application/json"})
        self._api_key = api_key
        self._api_secret = api_secret
        if api_key:
            self._session.headers["X-MBX-APIKEY"] = api_key

    # ─── public (unauthenticated) ─────────────────────────────

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

    # ─── authenticated (signed) ───────────────────────────────

    def _sign(self, params: dict) -> dict:
        """Add timestamp and HMAC-SHA256 signature to params."""
        params["timestamp"] = int(time.time() * 1000)
        query = urlencode(params)
        sig = hmac.new(
            self._api_secret.encode(), query.encode(), hashlib.sha256
        ).hexdigest()
        params["signature"] = sig
        return params

    def _signed_get(self, endpoint: str, params: dict = None) -> dict | list:
        params = self._sign(params or {})
        url = f"{self.BASE_URL}{endpoint}"
        resp = self._session.get(url, params=params, timeout=10)
        resp.raise_for_status()
        return resp.json()

    def _signed_post(self, endpoint: str, params: dict = None) -> dict | list:
        params = self._sign(params or {})
        url = f"{self.BASE_URL}{endpoint}"
        resp = self._session.post(url, params=params, timeout=10)
        if resp.status_code >= 400:
            try:
                err_body = resp.json()
                logger.error(f"API error {resp.status_code}: {err_body}")
            except Exception:
                logger.error(f"API error {resp.status_code}: {resp.text}")
            resp.raise_for_status()
        return resp.json()

    def _signed_delete(self, endpoint: str, params: dict = None) -> dict | list:
        params = self._sign(params or {})
        url = f"{self.BASE_URL}{endpoint}"
        resp = self._session.delete(url, params=params, timeout=10)
        resp.raise_for_status()
        return resp.json()

    # ─── account & positions ──────────────────────────────────

    def get_account(self) -> dict:
        """GET /fapi/v2/account — full account info with balances & positions."""
        return self._signed_get("/fapi/v2/account")

    def get_balance(self) -> float:
        """Return available USDT balance."""
        data = self._signed_get("/fapi/v2/balance")
        for asset in data:
            if asset.get("asset") == "USDT":
                return float(asset.get("availableBalance", 0))
        return 0.0

    def get_positions(self, symbol: str = None) -> list:
        """GET /fapi/v2/positionRisk — open positions."""
        params = {}
        if symbol:
            params["symbol"] = symbol
        return self._signed_get("/fapi/v2/positionRisk", params)

    # ─── leverage & margin ────────────────────────────────────

    def set_leverage(self, symbol: str, leverage: int) -> dict:
        """POST /fapi/v1/leverage"""
        return self._signed_post("/fapi/v1/leverage", {
            "symbol": symbol,
            "leverage": leverage,
        })

    def set_margin_type(self, symbol: str, margin_type: str = "ISOLATED") -> dict:
        """POST /fapi/v1/marginType — ISOLATED or CROSSED."""
        try:
            return self._signed_post("/fapi/v1/marginType", {
                "symbol": symbol,
                "marginType": margin_type,
            })
        except requests.HTTPError as e:
            # -4046: "No need to change margin type" — already set
            err_str = str(e)
            if "4046" in err_str or "400" in err_str:
                return {"msg": "already_set"}
            raise

    def get_leverage_bracket(self, symbol: str) -> list:
        """Get leverage brackets (authenticated)."""
        try:
            data = self._signed_get("/fapi/v1/leverageBracket", {"symbol": symbol})
            if isinstance(data, list) and data:
                return data[0].get("brackets", [])
            return []
        except Exception as e:
            logger.warning(f"Failed to get leverage brackets for {symbol}: {e}")
            return []

    def get_max_leverage(self, symbol: str, notional: float = 100.0,
                         fallback: int = 75) -> int:
        """Return the max available leverage for a given notional size."""
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

    def get_leverage_brackets(self) -> list:
        """Get leverage brackets for ALL symbols (authenticated)."""
        try:
            return self._signed_get("/fapi/v1/leverageBracket", {})
        except Exception as e:
            logger.warning(f"Failed to get all leverage brackets: {e}")
            return []

    # ─── orders ───────────────────────────────────────────────

    # Conditional order types that must use Algo Order API (since 2025-12-09)
    _ALGO_TYPES = {"STOP_MARKET", "TAKE_PROFIT_MARKET", "STOP",
                   "TAKE_PROFIT", "TRAILING_STOP_MARKET"}

    def place_order(self, symbol: str, side: str, order_type: str = "MARKET",
                    quantity: float = None, price: float = None,
                    stop_price: float = None, close_position: bool = False,
                    reduce_only: bool = False,
                    time_in_force: str = None) -> dict:
        """Place a new order. Automatically routes conditional orders
        (STOP_MARKET, TAKE_PROFIT_MARKET etc.) to Algo Order API.

        side: "BUY" or "SELL"
        order_type: "MARKET", "LIMIT", "STOP_MARKET", "TAKE_PROFIT_MARKET"
        """
        if order_type in self._ALGO_TYPES:
            return self._place_algo_order(
                symbol, side, order_type, quantity, price,
                stop_price, close_position)

        params = {
            "symbol": symbol,
            "side": side,
            "type": order_type,
            "newOrderRespType": "RESULT",
        }
        if quantity is not None:
            params["quantity"] = str(quantity)
        if price is not None:
            params["price"] = str(price)
        if reduce_only:
            params["reduceOnly"] = "true"
        if time_in_force:
            params["timeInForce"] = time_in_force
        elif order_type == "LIMIT":
            params["timeInForce"] = "GTC"

        return self._signed_post("/fapi/v1/order", params)

    def _place_algo_order(self, symbol: str, side: str, order_type: str,
                          quantity: float = None, price: float = None,
                          trigger_price: float = None,
                          close_position: bool = False) -> dict:
        """POST /fapi/v1/algoOrder — conditional orders (SL/TP).
        Binance migrated these from /fapi/v1/order on 2025-12-09."""
        params = {
            "algoType": "CONDITIONAL",
            "symbol": symbol,
            "side": side,
            "type": order_type,
        }
        if trigger_price is not None:
            params["triggerPrice"] = str(trigger_price)
        if quantity is not None and not close_position:
            params["quantity"] = str(quantity)
        if price is not None:
            params["price"] = str(price)
        if close_position:
            params["closePosition"] = "true"

        return self._signed_post("/fapi/v1/algoOrder", params)

    def cancel_all_orders(self, symbol: str) -> dict:
        """Cancel all open orders (regular + algo) for a symbol."""
        # Cancel regular orders
        try:
            self._signed_delete("/fapi/v1/allOpenOrders", {"symbol": symbol})
        except Exception:
            pass
        # Cancel algo/conditional orders (SL/TP)
        try:
            algo_orders = self.get_algo_open_orders(symbol)
            for o in algo_orders:
                algo_id = o.get("algoId")
                if algo_id:
                    self.cancel_algo_order(algo_id)
                    logger.debug(f"Cancelled algo order {algo_id} for {symbol}")
        except Exception:
            pass
        return {"msg": "ok"}

    def get_open_orders(self, symbol: str = None) -> list:
        """GET /fapi/v1/openOrders — regular orders."""
        params = {}
        if symbol:
            params["symbol"] = symbol
        return self._signed_get("/fapi/v1/openOrders", params)

    def get_algo_open_orders(self, symbol: str = None) -> list:
        """GET /fapi/v1/openAlgoOrders — active conditional orders (SL/TP)."""
        try:
            params = {}
            if symbol:
                params["symbol"] = symbol
            data = self._signed_get("/fapi/v1/openAlgoOrders", params)
            return data if isinstance(data, list) else []
        except Exception:
            return []

    def cancel_algo_order(self, algo_id: int) -> dict:
        """DELETE /fapi/v1/algoOrder — cancel a single algo order."""
        return self._signed_delete("/fapi/v1/algoOrder", {"algoId": algo_id})
