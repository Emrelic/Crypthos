import json
import threading
import time
from loguru import logger
import websocket
from core.event_bus import EventBus
from core.constants import EventType, BINANCE_FUTURES_WS


class BinanceWebSocket:
    """WebSocket client for Binance Futures real-time streams."""

    def __init__(self, event_bus: EventBus):
        self._event_bus = event_bus
        self._ws = None
        self._thread = None
        self._running = False
        self._symbol = None
        self._reconnect_delay = 1
        self._max_reconnect_delay = 30

    def connect(self, symbol: str) -> None:
        self._symbol = symbol.lower()
        self._running = True
        self._reconnect_delay = 1
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while self._running:
            try:
                stream_name = (
                    f"{self._symbol}@ticker/"
                    f"{self._symbol}@kline_15m/"
                    f"{self._symbol}@markPrice"
                )
                url = f"{BINANCE_FUTURES_WS}/{stream_name}"

                self._ws = websocket.WebSocketApp(
                    url,
                    on_message=self._on_message,
                    on_error=self._on_error,
                    on_close=self._on_close,
                    on_open=self._on_open,
                )
                self._ws.run_forever(ping_interval=20, ping_timeout=10)
            except Exception as e:
                logger.error(f"WebSocket error: {e}")

            if self._running:
                logger.info(f"Reconnecting in {self._reconnect_delay}s...")
                time.sleep(self._reconnect_delay)
                self._reconnect_delay = min(
                    self._reconnect_delay * 2, self._max_reconnect_delay
                )

    def _on_open(self, ws) -> None:
        self._reconnect_delay = 1
        logger.info(f"WebSocket connected: {self._symbol}")
        self._event_bus.publish(EventType.CONNECTION_STATUS, {
            "component": "websocket", "status": True,
        })

    def _on_message(self, ws, message: str) -> None:
        try:
            data = json.loads(message)
            stream = data.get("stream", "")
            payload = data.get("data", data)

            if "@ticker" in stream:
                self._event_bus.publish(EventType.PRICE_UPDATE, {
                    "symbol": payload.get("s", self._symbol.upper()),
                    "price": float(payload.get("c", 0)),
                    "high_24h": float(payload.get("h", 0)),
                    "low_24h": float(payload.get("l", 0)),
                    "volume_24h": float(payload.get("q", 0)),
                    "price_change_pct": float(payload.get("P", 0)),
                })
            elif "@kline" in stream:
                k = payload.get("k", {})
                self._event_bus.publish(EventType.KLINE_UPDATE, {
                    "symbol": k.get("s", self._symbol.upper()),
                    "interval": k.get("i", "15m"),
                    "open": float(k.get("o", 0)),
                    "high": float(k.get("h", 0)),
                    "low": float(k.get("l", 0)),
                    "close": float(k.get("c", 0)),
                    "volume": float(k.get("v", 0)),
                    "is_closed": k.get("x", False),
                })
            elif "@markPrice" in stream:
                self._event_bus.publish(EventType.FUNDING_UPDATE, {
                    "symbol": payload.get("s", self._symbol.upper()),
                    "mark_price": float(payload.get("p", 0)),
                    "index_price": float(payload.get("i", 0)),
                    "funding_rate": float(payload.get("r", 0)),
                    "next_funding_time": payload.get("T", 0),
                })
        except Exception as e:
            logger.debug(f"WS message parse error: {e}")

    def _on_error(self, ws, error) -> None:
        logger.warning(f"WebSocket error: {error}")

    def _on_close(self, ws, close_code, close_msg) -> None:
        logger.info(f"WebSocket closed: {close_code} {close_msg}")
        self._event_bus.publish(EventType.CONNECTION_STATUS, {
            "component": "websocket", "status": False,
        })

    def disconnect(self) -> None:
        self._running = False
        if self._ws:
            self._ws.close()

    def switch_symbol(self, new_symbol: str) -> None:
        self.disconnect()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)
        self.connect(new_symbol)

    @property
    def is_connected(self) -> bool:
        return self._ws is not None and self._running
