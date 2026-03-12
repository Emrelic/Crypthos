"""API-based Order Executor — places orders via Binance Futures REST API.
No UI automation needed, works entirely in the background."""
import threading
from loguru import logger
from core.constants import OrderSide, OrderType, EventType
from core.event_bus import EventBus
from market.binance_rest import BinanceRestClient


class ApiOrderExecutor:
    """Executes orders via Binance Futures REST API (no pywinauto)."""

    def __init__(self, rest_client: BinanceRestClient, event_bus: EventBus):
        self._rest = rest_client
        self._event_bus = event_bus
        self._lock = threading.Lock()

    def execute_order(self, symbol: str, side: OrderSide,
                      order_type: OrderType, price: float = None,
                      size: float = None, tp_percent: float = None,
                      sl_percent: float = None,
                      reduce_only: bool = False,
                      leverage: int = None,
                      qty_precision: int = 3,
                      ensure_isolated: bool = False) -> bool:
        """Place an order via API. Returns True on success."""
        with self._lock:
            try:
                # 1. Set margin mode (isolated)
                if ensure_isolated and not reduce_only:
                    try:
                        self._rest.set_margin_type(symbol, "ISOLATED")
                        logger.debug(f"{symbol} margin type set to ISOLATED")
                    except Exception as e:
                        if "4046" not in str(e):
                            logger.warning(f"set_margin_type failed: {e}")

                # 2. Set leverage
                if leverage is not None and not reduce_only:
                    try:
                        resp = self._rest.set_leverage(symbol, leverage)
                        actual = resp.get("leverage", leverage)
                        logger.info(f"{symbol} leverage set to {actual}x")
                    except Exception as e:
                        logger.warning(f"set_leverage failed: {e}")

                # 3. Map side
                api_side = "BUY" if side == OrderSide.BUY_LONG else "SELL"

                # 4. Map order type
                api_type = "MARKET"
                if order_type == OrderType.LIMIT:
                    api_type = "LIMIT"

                # 5. Place main order
                order_resp = self._rest.place_order(
                    symbol=symbol,
                    side=api_side,
                    order_type=api_type,
                    quantity=size,
                    price=price if api_type == "LIMIT" else None,
                    reduce_only=reduce_only,
                )

                order_id = order_resp.get("orderId", "?")
                status = order_resp.get("status", "?")
                avg_price = float(order_resp.get("avgPrice", 0))
                executed_qty = float(order_resp.get("executedQty", 0))

                # Fallback: if avgPrice is 0, get current ticker price
                if avg_price == 0:
                    try:
                        ticker = self._rest.get_ticker_price(symbol)
                        avg_price = float(ticker.get("price", 0))
                        logger.info(f"avgPrice was 0, using ticker: {avg_price}")
                    except Exception:
                        pass
                if executed_qty == 0:
                    executed_qty = size or 0

                logger.info(f"Order placed: {api_side} {size} {symbol} "
                            f"id={order_id} status={status} "
                            f"avgPrice={avg_price} executedQty={executed_qty}")

                if status not in ("FILLED", "NEW", "PARTIALLY_FILLED"):
                    logger.error(f"Unexpected order status: {status}")
                    self._event_bus.publish(EventType.ORDER_FAILED, {
                        "symbol": symbol, "reason": f"status={status}"})
                    return False

                # 6. Place TP/SL orders (server-side, Binance manages them)
                if not reduce_only and avg_price > 0:
                    self._place_tp_sl(symbol, api_side, executed_qty,
                                      avg_price, tp_percent, sl_percent,
                                      leverage or 1)

                self._event_bus.publish(EventType.ORDER_PLACED, {
                    "symbol": symbol, "side": api_side,
                    "size": executed_qty, "price": avg_price,
                    "order_id": order_id,
                })
                return True

            except Exception as e:
                logger.error(f"API order failed for {symbol}: {e}")
                self._event_bus.publish(EventType.ORDER_FAILED, {
                    "symbol": symbol, "reason": str(e)})
                return False

    def _get_price_precision(self, symbol: str) -> int:
        """Get price precision for a symbol from exchange info."""
        try:
            info = self._rest.get_exchange_info(symbol)
            if info:
                return info.get("pricePrecision", 4)
        except Exception:
            pass
        # Fallback: guess from entry price
        return 4

    def _place_tp_sl(self, symbol: str, entry_side: str,
                     qty: float, entry_price: float,
                     tp_roi_pct: float, sl_roi_pct: float,
                     leverage: int):
        """Place TP and SL as separate server-side orders via Algo API.
        tp_roi_pct / sl_roi_pct are ROI% on margin (not price %).
        Convert to price: price_move_pct = roi_pct / leverage."""
        close_side = "SELL" if entry_side == "BUY" else "BUY"
        is_long = entry_side == "BUY"
        pp = self._get_price_precision(symbol)

        # SL first (more critical)
        if sl_roi_pct and sl_roi_pct > 0:
            sl_price_pct = sl_roi_pct / leverage / 100.0
            if is_long:
                sl_price = round(entry_price * (1 - sl_price_pct), pp)
            else:
                sl_price = round(entry_price * (1 + sl_price_pct), pp)
            try:
                self._rest.place_order(
                    symbol=symbol, side=close_side,
                    order_type="STOP_MARKET",
                    stop_price=sl_price,
                    close_position=True,
                )
                logger.info(f"SL order: {symbol} @ {sl_price} "
                            f"(ROI -{sl_roi_pct:.1f}%)")
            except Exception as e:
                logger.warning(f"SL order failed for {symbol}: {e}")

        # TP (safety net, trailing handles real exits)
        if tp_roi_pct and tp_roi_pct > 0:
            tp_price_pct = tp_roi_pct / leverage / 100.0
            if is_long:
                tp_price = round(entry_price * (1 + tp_price_pct), pp)
            else:
                tp_price = round(entry_price * (1 - tp_price_pct), pp)
            try:
                self._rest.place_order(
                    symbol=symbol, side=close_side,
                    order_type="TAKE_PROFIT_MARKET",
                    stop_price=tp_price,
                    close_position=True,
                )
                logger.info(f"TP order: {symbol} @ {tp_price} "
                            f"(ROI {tp_roi_pct:.1f}%)")
            except Exception as e:
                logger.warning(f"TP order failed for {symbol}: {e}")

    def update_tp_sl(self, symbol: str, entry_side: str, qty: float,
                     entry_price: float, leverage: int,
                     tp_roi_pct: float, sl_roi_pct: float) -> bool:
        """Cancel existing TP/SL and place new ones with updated values."""
        try:
            self._rest.cancel_all_orders(symbol)
            logger.info(f"Cancelled old TP/SL for {symbol}, placing new ones")
        except Exception as e:
            logger.warning(f"cancel_all_orders for {symbol}: {e}")

        self._place_tp_sl(symbol, entry_side, qty, entry_price,
                          tp_roi_pct, sl_roi_pct, leverage)
        return True

    def close_position(self, symbol: str, side: OrderSide,
                       qty: float, limit_exit: bool = False,
                       limit_offset_pct: float = 0.0) -> bool:
        """Close a position. If limit_exit=True, uses limit order for maker fee.
        Returns True if closed successfully OR if position no longer exists."""
        # Cancel existing TP/SL orders first
        try:
            self._rest.cancel_all_orders(symbol)
            logger.debug(f"Cancelled open orders for {symbol}")
        except Exception as e:
            logger.warning(f"cancel_all_orders failed: {e}")

        close_side = (OrderSide.SELL_SHORT if side == OrderSide.BUY_LONG
                      else OrderSide.BUY_LONG)

        if limit_exit and limit_offset_pct > 0:
            # Limit exit: place at slightly favorable price for maker fee
            try:
                ticker = self._rest.get_ticker_price(symbol)
                current_price = float(ticker.get("price", 0))
                if current_price > 0:
                    pp = self._get_price_precision(symbol)
                    if side == OrderSide.BUY_LONG:
                        # Closing long = SELL → place slightly above market
                        limit_price = round(current_price * (1 + limit_offset_pct / 100), pp)
                    else:
                        # Closing short = BUY → place slightly below market
                        limit_price = round(current_price * (1 - limit_offset_pct / 100), pp)

                    logger.info(f"Limit exit: {close_side.value} {qty} {symbol} "
                                f"limit={limit_price} (market={current_price}, "
                                f"offset={limit_offset_pct:.3f}%)")

                    success = self.execute_order(
                        symbol=symbol, side=close_side,
                        order_type=OrderType.LIMIT,
                        price=limit_price,
                        size=qty, reduce_only=True,
                    )
                    if success:
                        return True
                    logger.warning(f"Limit exit failed for {symbol}, falling back to market")
            except Exception as e:
                logger.warning(f"Limit exit error for {symbol}: {e}, falling back to market")

        # Market order (default or fallback)
        success = self.execute_order(
            symbol=symbol, side=close_side,
            order_type=OrderType.MARKET,
            size=qty, reduce_only=True,
        )

        if not success:
            # Check if position was already closed (e.g. by server-side SL/TP)
            if not self._has_open_position(symbol):
                logger.warning(f"{symbol} position no longer exists on exchange, treating as closed")
                return True

        return success

    def _has_open_position(self, symbol: str) -> bool:
        """Check if a position actually exists on Binance."""
        try:
            positions = self._rest.get_positions()
            for p in positions:
                if p.get("symbol") == symbol and float(p.get("positionAmt", 0)) != 0:
                    return True
            return False
        except Exception as e:
            logger.error(f"Failed to check position status for {symbol}: {e}")
            return True  # Assume still open on error to avoid premature cleanup

    def get_balance(self) -> float:
        """Get available USDT balance from API."""
        try:
            return self._rest.get_balance()
        except Exception as e:
            logger.error(f"get_balance failed: {e}")
            return 0.0

    def get_total_balance(self) -> float:
        """Get total USDT wallet balance (including locked margin)."""
        try:
            return self._rest.get_total_balance()
        except Exception as e:
            logger.error(f"get_total_balance failed: {e}")
            return 0.0

    def get_open_positions(self) -> list[dict]:
        """Get all open positions from API."""
        try:
            positions = self._rest.get_positions()
            return [p for p in positions
                    if float(p.get("positionAmt", 0)) != 0]
        except Exception as e:
            logger.error(f"get_positions failed: {e}")
            return []

    def test_connection(self) -> bool:
        """Test API connection by fetching account info."""
        try:
            balance = self._rest.get_balance()
            logger.info(f"API connection OK, USDT balance: {balance:.2f}")
            return True
        except Exception as e:
            logger.error(f"API connection failed: {e}")
            return False
