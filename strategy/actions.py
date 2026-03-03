from dataclasses import dataclass, field
from core.constants import OrderSide, OrderType


@dataclass
class TradeAction:
    """Defines what to do when a rule triggers."""
    side: OrderSide
    order_type: OrderType = OrderType.MARKET
    size_usdt: float = None
    size_qty: float = None
    size_percent: float = None
    tp_percent: float = None
    sl_percent: float = None
    price_offset: float = None

    def calculate_size(self, current_price: float, available_balance: float = None) -> float:
        if self.size_qty:
            return self.size_qty
        if self.size_usdt and current_price > 0:
            return int(self.size_usdt / current_price)
        if self.size_percent and available_balance and current_price > 0:
            usdt = available_balance * (self.size_percent / 100)
            return int(usdt / current_price)
        return 1  # Minimum

    def calculate_price(self, current_price: float) -> float:
        if self.order_type == OrderType.MARKET:
            return None
        if self.price_offset:
            return current_price + self.price_offset
        return current_price

    def to_dict(self) -> dict:
        return {
            "side": self.side.value,
            "order_type": self.order_type.value,
            "size_usdt": self.size_usdt,
            "size_qty": self.size_qty,
            "size_percent": self.size_percent,
            "tp_percent": self.tp_percent,
            "sl_percent": self.sl_percent,
            "price_offset": self.price_offset,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "TradeAction":
        return cls(
            side=OrderSide(data["side"]),
            order_type=OrderType(data.get("order_type", "Market")),
            size_usdt=data.get("size_usdt"),
            size_qty=data.get("size_qty"),
            size_percent=data.get("size_percent"),
            tp_percent=data.get("tp_percent"),
            sl_percent=data.get("sl_percent"),
            price_offset=data.get("price_offset"),
        )
