from dataclasses import dataclass
import math


@dataclass
class SymbolInfo:
    symbol: str
    price_precision: int
    quantity_precision: int
    tick_size: float
    min_qty: float
    max_qty: float
    min_notional: float

    def validate_price(self, price: float) -> float:
        precision = int(round(-math.log10(self.tick_size)))
        return round(round(price / self.tick_size) * self.tick_size, precision)

    def validate_quantity(self, qty: float) -> float:
        if self.quantity_precision == 0:
            return int(qty)
        return round(qty, self.quantity_precision)

    def validate_notional(self, price: float, qty: float) -> bool:
        return price * qty >= self.min_notional

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
