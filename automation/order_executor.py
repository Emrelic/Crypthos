import time
import threading
from dataclasses import dataclass
from loguru import logger
from core.constants import OrderSide, OrderType, EventType
from core.event_bus import EventBus
from automation.binance_app import BinanceApp


class OrderExecutor:
    """Executes orders by automating the Binance Desktop UI."""

    def __init__(self, binance_app: BinanceApp, event_bus: EventBus):
        self._app = binance_app
        self._event_bus = event_bus
        self._lock = threading.Lock()

    def execute_order(self, symbol: str, side: OrderSide,
                      order_type: OrderType, price: float = None,
                      size: float = None, tp_percent: float = None,
                      sl_percent: float = None,
                      reduce_only: bool = False) -> bool:
        with self._lock:
            try:
                if not self._app.is_connected:
                    if not self._app.refresh_connection():
                        raise RuntimeError("Binance Desktop not connected")

                # Ensure window is visible before interacting
                self._app.ensure_visible()

                self._select_order_type(order_type)

                if order_type != OrderType.MARKET and price is not None:
                    self._set_field(self._app.get_price_input(), str(price))

                if size is not None:
                    self._set_field(self._app.get_size_input(), str(int(size)))

                if tp_percent is not None or sl_percent is not None:
                    self._enable_tpsl()
                    if tp_percent is not None:
                        self._set_field(self._app.get_tp_roi_input(), str(tp_percent))
                    if sl_percent is not None:
                        self._set_field(self._app.get_sl_roi_input(), str(sl_percent))

                if reduce_only:
                    self._enable_reduce_only()

                self._click_side_button(side)

                self._event_bus.publish(EventType.ORDER_PLACED, {
                    "symbol": symbol,
                    "side": side.value,
                    "order_type": order_type.value,
                    "price": price,
                    "size": size,
                    "tp_percent": tp_percent,
                    "sl_percent": sl_percent,
                })
                logger.info(f"Order executed: {side.value} {size} {symbol} "
                            f"@ {price or 'market'} TP={tp_percent}% SL={sl_percent}%")
                return True

            except Exception as e:
                self._event_bus.publish(EventType.ORDER_FAILED, {
                    "symbol": symbol,
                    "side": side.value,
                    "error": str(e),
                })
                logger.error(f"Order execution failed: {e}")
                return False

    def _select_order_type(self, order_type: OrderType) -> None:
        tab = self._app.get_order_type_tab(order_type.value)
        tab.click_input()
        time.sleep(0.15)

    def _set_field(self, element, value: str) -> None:
        element.click_input()
        time.sleep(0.05)
        element.type_keys("^a", pause=0.03)
        time.sleep(0.03)
        element.type_keys("{DELETE}", pause=0.03)
        time.sleep(0.03)
        element.type_keys(value, with_spaces=True, pause=0.02)
        time.sleep(0.05)

    def _enable_tpsl(self) -> None:
        try:
            checkbox = self._app.get_tp_checkbox()
            toggle = checkbox.get_toggle_state()
            if toggle == 0:  # Not checked
                checkbox.click_input()
                time.sleep(0.3)
        except Exception as e:
            logger.warning(f"Could not toggle TP/SL checkbox: {e}")

    def _enable_reduce_only(self) -> None:
        try:
            checkbox = self._app.get_reduce_only_checkbox()
            toggle = checkbox.get_toggle_state()
            if toggle == 0:
                checkbox.click_input()
                time.sleep(0.1)
        except Exception as e:
            logger.warning(f"Could not toggle Reduce-Only checkbox: {e}")

    def _click_side_button(self, side: OrderSide) -> None:
        if side == OrderSide.BUY_LONG:
            btn = self._app.get_buy_button()
        else:
            btn = self._app.get_sell_button()
        btn.click_input()
        time.sleep(0.3)
