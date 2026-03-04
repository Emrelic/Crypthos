from dataclasses import dataclass, field
import math
import threading
import time

from loguru import logger


@dataclass
class SymbolInfo:
    symbol: str
    price_precision: int
    quantity_precision: int
    tick_size: float
    min_qty: float
    max_qty: float
    min_notional: float
    max_leverage: int = 20

    def validate_price(self, price: float) -> float:
        precision = int(round(-math.log10(self.tick_size)))
        return round(round(price / self.tick_size) * self.tick_size, precision)

    def validate_quantity(self, qty: float) -> float:
        if self.quantity_precision == 0:
            return int(qty)
        return round(qty, self.quantity_precision)

    def validate_notional(self, price: float, qty: float) -> bool:
        return price * qty >= self.min_notional

    def clamp_leverage(self, requested: int) -> int:
        return min(requested, self.max_leverage)

    @classmethod
    def from_exchange_info(cls, data: dict) -> "SymbolInfo":
        filters = {f["filterType"]: f for f in data.get("filters", [])}
        price_filter = filters.get("PRICE_FILTER", {})
        lot_filter = filters.get("LOT_SIZE", {})
        notional_filter = filters.get("MIN_NOTIONAL", {})
        return cls(
            symbol=data["symbol"],
            price_precision=data.get("pricePrecision", 8),
            quantity_precision=data.get("quantityPrecision", 0),
            tick_size=float(price_filter.get("tickSize", "0.01")),
            min_qty=float(lot_filter.get("minQty", "1")),
            max_qty=float(lot_filter.get("maxQty", "1000000")),
            min_notional=float(notional_filter.get("notional", "5")),
        )


class SymbolInfoCache:
    """Thread-safe cache for SymbolInfo with leverage data. TTL = 1 hour."""

    def __init__(self, rest_client, ttl_seconds: float = 3600.0):
        self._rest = rest_client
        self._ttl = ttl_seconds
        self._cache: dict[str, tuple[SymbolInfo, float]] = {}
        self._lock = threading.Lock()

    def get(self, symbol: str) -> SymbolInfo:
        with self._lock:
            if symbol in self._cache:
                info, ts = self._cache[symbol]
                if time.time() - ts < self._ttl:
                    return info

        try:
            raw = self._rest.get_exchange_info(symbol)
            if not raw:
                raise ValueError(f"No exchange info for {symbol}")
            info = SymbolInfo.from_exchange_info(raw)
            notional = 100.0
            info.max_leverage = self._rest.get_max_leverage(symbol, notional)
        except Exception as e:
            logger.warning(f"SymbolInfoCache miss for {symbol}: {e}")
            info = SymbolInfo(
                symbol=symbol, price_precision=8, quantity_precision=3,
                tick_size=0.01, min_qty=0.01, max_qty=1_000_000,
                min_notional=5.0, max_leverage=75,
            )

        with self._lock:
            self._cache[symbol] = (info, time.time())
        return info
