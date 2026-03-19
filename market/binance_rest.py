import time
import hmac
import hashlib
from urllib.parse import urlencode

import requests
import pandas as pd
from loguru import logger
from core.constants import BINANCE_FUTURES_REST


def _fmt(value: float) -> str:
    """Format float without scientific notation (e.g. 0.00001 not 1e-05)."""
    return f"{value:.8f}".rstrip("0").rstrip(".")


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
        # Kline cache: {cache_key: (timestamp, DataFrame)}
        self._kline_cache: dict[str, tuple[float, pd.DataFrame]] = {}
        self._kline_cache_ttl = 30.0  # seconds

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
        # Check cache first
        cache_key = f"{symbol}_{interval}_{limit}"
        now = time.time()
        cached = self._kline_cache.get(cache_key)
        if cached and (now - cached[0]) < self._kline_cache_ttl:
            return cached[1]

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
        for col in ["open", "high", "low", "close", "volume", "taker_buy_volume"]:
            df[col] = df[col].astype(float)
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")

        # Store in cache and evict expired entries
        self._kline_cache[cache_key] = (now, df)
        if len(self._kline_cache) > 200:
            expired = [k for k, (ts, _) in self._kline_cache.items()
                       if now - ts > self._kline_cache_ttl * 2]
            for k in expired:
                del self._kline_cache[k]

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

    def get_all_premium_index(self) -> list:
        """GET /fapi/v1/premiumIndex (no symbol) — returns funding rate for ALL symbols.
        Each item: {symbol, markPrice, indexPrice, lastFundingRate, nextFundingTime, ...}"""
        return self._get("/fapi/v1/premiumIndex")

    def get_depth(self, symbol: str, limit: int = 20) -> dict:
        """GET /fapi/v1/depth — order book depth.
        limit: 5, 10, 20, 50, 100, 500, 1000
        Returns: {lastUpdateId, bids: [[price, qty], ...], asks: [[price, qty], ...]}"""
        return self._get("/fapi/v1/depth", {"symbol": symbol, "limit": limit})

    def get_open_interest(self, symbol: str) -> dict:
        """GET /fapi/v1/openInterest — current open interest for a symbol.
        Returns: {symbol, openInterest, time}"""
        return self._get("/fapi/v1/openInterest", {"symbol": symbol})

    def get_open_interest_hist(self, symbol: str, period: str = "5m",
                               limit: int = 10) -> list:
        """GET /futures/data/openInterestHist — historical OI with period.
        period: 5m, 15m, 30m, 1h, 2h, 4h, 6h, 12h, 1d
        Returns: [{symbol, sumOpenInterest, sumOpenInterestValue, timestamp}, ...]"""
        return self._get("/futures/data/openInterestHist", {
            "symbol": symbol,
            "period": period,
            "limit": limit,
        })

    # ─── authenticated (signed) ───────────────────────────────

    def _sign(self, params: dict) -> dict:
        """Add timestamp, recvWindow and HMAC-SHA256 signature to params."""
        params["timestamp"] = int(time.time() * 1000)
        params.setdefault("recvWindow", 5000)
        query = urlencode(params)
        sig = hmac.new(
            self._api_secret.encode(), query.encode(), hashlib.sha256
        ).hexdigest()
        params["signature"] = sig
        return params

    def _signed_get(self, endpoint: str, params: dict = None) -> dict | list:
        params = self._sign(params or {})
        url = f"{self.BASE_URL}{endpoint}"
        try:
            resp = self._session.get(url, params=params, timeout=10)
            if resp.status_code >= 400:
                try:
                    err_body = resp.json()
                    logger.error(f"API error {resp.status_code} [{endpoint}]: {err_body}")
                except Exception:
                    logger.error(f"API error {resp.status_code} [{endpoint}]: {resp.text}")
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as e:
            logger.error(f"Signed GET error [{endpoint}]: {e}")
            raise

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
        try:
            resp = self._session.delete(url, params=params, timeout=10)
            if resp.status_code >= 400:
                try:
                    err_body = resp.json()
                    logger.error(f"API error {resp.status_code} [{endpoint}]: {err_body}")
                except Exception:
                    logger.error(f"API error {resp.status_code} [{endpoint}]: {resp.text}")
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as e:
            logger.error(f"Signed DELETE error [{endpoint}]: {e}")
            raise

    # ─── income history ──────────────────────────────────────

    def get_income_history(self, income_type: str = "", symbol: str = "",
                           start_time: int = 0, end_time: int = 0,
                           limit: int = 1000) -> list:
        """GET /fapi/v1/income — trade income history (PnL, fees, funding, liquidations)."""
        params = {"limit": limit}
        if income_type:
            params["incomeType"] = income_type
        if symbol:
            params["symbol"] = symbol
        if start_time:
            params["startTime"] = start_time
        if end_time:
            params["endTime"] = end_time
        try:
            return self._signed_get("/fapi/v1/income", params)
        except Exception as e:
            logger.error(f"Income history error: {e}")
            return []

    def get_account_trades(self, symbol: str = "", start_time: int = 0,
                           end_time: int = 0, limit: int = 500) -> list:
        """GET /fapi/v1/userTrades — actual trade fills with qty, price, fee."""
        params = {"limit": limit}
        if symbol:
            params["symbol"] = symbol
        if start_time:
            params["startTime"] = start_time
        if end_time:
            params["endTime"] = end_time
        try:
            return self._signed_get("/fapi/v1/userTrades", params)
        except Exception as e:
            logger.error(f"Account trades error: {e}")
            return []

    # ─── account & positions ──────────────────────────────────

    def get_account(self) -> dict:
        """GET /fapi/v2/account — full account info with balances & positions."""
        return self._signed_get("/fapi/v2/account")

    def get_balance(self) -> float:
        """Return available USDT balance (free, not locked in positions)."""
        data = self._signed_get("/fapi/v2/balance")
        for asset in data:
            if asset.get("asset") == "USDT":
                return float(asset.get("availableBalance", 0))
        return 0.0

    def get_total_balance(self) -> float:
        """Return total USDT wallet balance (including margin locked in positions)."""
        data = self._signed_get("/fapi/v2/balance")
        for asset in data:
            if asset.get("asset") == "USDT":
                return float(asset.get("balance", 0))
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
                    time_in_force: str = None,
                    callback_rate: float = None) -> dict:
        """Place a new order. Automatically routes conditional orders
        (STOP_MARKET, TAKE_PROFIT_MARKET etc.) to Algo Order API.

        side: "BUY" or "SELL"
        order_type: "MARKET", "LIMIT", "STOP_MARKET", "TAKE_PROFIT_MARKET",
                    "TRAILING_STOP_MARKET"
        callback_rate: for TRAILING_STOP_MARKET, callback % (0.1 to 5.0)
        """
        if order_type in self._ALGO_TYPES:
            return self._place_algo_order(
                symbol, side, order_type, quantity, price,
                stop_price, close_position, callback_rate=callback_rate)

        params = {
            "symbol": symbol,
            "side": side,
            "type": order_type,
            "newOrderRespType": "RESULT",
        }
        if quantity is not None:
            params["quantity"] = _fmt(quantity)
        if price is not None:
            params["price"] = _fmt(price)
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
                          close_position: bool = False,
                          callback_rate: float = None) -> dict:
        """POST /fapi/v1/algoOrder — conditional orders (SL/TP/TRAILING).
        Binance migrated these from /fapi/v1/order on 2025-12-09.
        callback_rate: for TRAILING_STOP_MARKET, callback % (0.1 to 5.0)."""
        params = {
            "algoType": "CONDITIONAL",
            "symbol": symbol,
            "side": side,
            "type": order_type,
        }
        if trigger_price is not None:
            params["triggerPrice"] = _fmt(trigger_price)
        if quantity is not None:
            params["quantity"] = _fmt(quantity)
        if price is not None:
            params["price"] = _fmt(price)
        # TRAILING_STOP_MARKET does not support closePosition in algo API
        if close_position and order_type != "TRAILING_STOP_MARKET":
            params["closePosition"] = "true"
        if callback_rate is not None:
            params["callbackRate"] = _fmt(callback_rate)

        return self._signed_post("/fapi/v1/algoOrder", params)

    def cancel_all_orders(self, symbol: str) -> dict:
        """Cancel all open orders (regular + algo) for a symbol.
        Returns {msg: 'ok', errors: [...]} — errors list empty on full success."""
        errors = []
        # Cancel regular orders
        try:
            self._signed_delete("/fapi/v1/allOpenOrders", {"symbol": symbol})
        except Exception as e:
            logger.warning(f"Failed to cancel regular orders for {symbol}: {e}")
            errors.append(f"regular: {e}")
        # Cancel algo/conditional orders (SL/TP)
        try:
            algo_orders = self.get_algo_open_orders(symbol)
            for o in algo_orders:
                algo_id = o.get("algoId")
                if algo_id:
                    try:
                        self.cancel_algo_order(algo_id)
                        logger.debug(f"Cancelled algo order {algo_id} for {symbol}")
                    except Exception as e:
                        logger.warning(f"Failed to cancel algo order {algo_id}: {e}")
                        errors.append(f"algo_{algo_id}: {e}")
        except Exception as e:
            logger.warning(f"Failed to fetch algo orders for {symbol}: {e}")
            errors.append(f"algo_fetch: {e}")
        if errors:
            logger.error(f"cancel_all_orders({symbol}) partial failure: {errors}")
        return {"msg": "ok" if not errors else "partial_failure", "errors": errors}

    def get_all_open_orders_combined(self, symbol: str = None):
        """Tüm açık emirleri tek listede döndürür (regular + algo/conditional).
        Her emir dict'inde en az 'symbol', 'type', 'orderId' veya 'algoId' bulunur.
        Returns list on success, None on ANY error (güvenlik — hata varsa boş dönmez).
        """
        combined = []

        # 1. Regular orders: GET /fapi/v1/openOrders
        try:
            params = {}
            if symbol:
                params["symbol"] = symbol
            regular = self._signed_get("/fapi/v1/openOrders", params)
            if isinstance(regular, list):
                for o in regular:
                    o["_source"] = "regular"
                combined.extend(regular)
        except Exception as e:
            logger.warning(f"get_open_orders (regular) failed: {e}")
            return None  # Okuma başarısız → None

        # 2. Algo/conditional orders: birden fazla endpoint dene
        algo_ok = False
        for endpoint in ["/fapi/v1/algo/openOrders",
                         "/fapi/v1/openAlgoOrders",
                         "/fapi/v1/conditional/openOrders"]:
            try:
                params = {}
                if symbol:
                    params["symbol"] = symbol
                data = self._signed_get(endpoint, params)
                algo_list = []
                if isinstance(data, list):
                    algo_list = data
                elif isinstance(data, dict):
                    # Binance: {"total": N, "orders": [...]} veya {"total": N, "dataList": [...]}
                    algo_list = (data.get("orders") or data.get("dataList")
                                 or data.get("rows") or [])
                    if not isinstance(algo_list, list):
                        algo_list = []

                for o in algo_list:
                    o["_source"] = "algo"
                combined.extend(algo_list)
                algo_ok = True
                logger.debug(f"Algo orders from {endpoint}: {len(algo_list)} orders")
                break  # İlk başarılı endpoint yeterli
            except Exception as e:
                logger.debug(f"Algo endpoint {endpoint} failed: {e}")
                continue

        if not algo_ok:
            logger.warning("Tüm algo order endpoint'leri başarısız — "
                           "sadece regular orders okundu. "
                           "Emir KONMAYACAK (güvenlik).")
            return None  # Algo okunamadı → None (güvenlik)

        return combined

    def get_open_orders(self, symbol: str = None):
        """GET /fapi/v1/openOrders — regular orders only.
        Returns list on success, None on error."""
        try:
            params = {}
            if symbol:
                params["symbol"] = symbol
            result = self._signed_get("/fapi/v1/openOrders", params)
            return result if isinstance(result, list) else []
        except Exception as e:
            logger.warning(f"get_open_orders failed: {e}")
            return None

    def get_algo_open_orders(self, symbol: str = None):
        """Algo/conditional open orders. Birden fazla endpoint dener.
        Returns list on success, None on error."""
        for endpoint in ["/fapi/v1/algo/openOrders",
                         "/fapi/v1/openAlgoOrders",
                         "/fapi/v1/conditional/openOrders"]:
            try:
                params = {}
                if symbol:
                    params["symbol"] = symbol
                data = self._signed_get(endpoint, params)
                if isinstance(data, list):
                    return data
                if isinstance(data, dict):
                    result = (data.get("orders") or data.get("dataList")
                              or data.get("rows") or [])
                    return result if isinstance(result, list) else []
            except Exception:
                continue
        logger.warning("get_algo_open_orders: tüm endpoint'ler başarısız")
        return None

    def cancel_algo_order(self, algo_id: int) -> dict:
        """DELETE /fapi/v1/algoOrder — cancel a single algo order."""
        return self._signed_delete("/fapi/v1/algoOrder", {"algoId": algo_id})
