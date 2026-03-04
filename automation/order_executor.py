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
                      reduce_only: bool = False,
                      leverage: int = None,
                      qty_precision: int = 3,
                      ensure_isolated: bool = False) -> bool:
        with self._lock:
            try:
                if not self._app.is_connected:
                    if not self._app.refresh_connection():
                        raise RuntimeError("Binance Desktop not connected")

                self._app.ensure_visible()

                # Set margin mode and leverage BEFORE placing order
                if leverage is not None and not reduce_only:
                    if ensure_isolated:
                        self._app.ensure_isolated_mode()
                        time.sleep(0.5)
                    success = self._app.set_leverage(leverage)
                    if not success:
                        logger.warning(f"Leverage set failed for {symbol}")
                    time.sleep(0.5)

                # Refresh descendants after leverage/margin changes
                self._app.invalidate_cache()
                self._app._refresh_descendants()
                time.sleep(0.3)

                self._select_order_type(order_type)

                # CRITICAL: Ensure Reduce-Only is in correct state
                if reduce_only:
                    self._enable_reduce_only()
                else:
                    self._disable_reduce_only()

                time.sleep(0.3)

                if order_type != OrderType.MARKET and price is not None:
                    self._set_field(self._app.get_price_input(), str(price))

                if size is not None:
                    size_str = self._format_qty(size, qty_precision)
                    logger.info(f"Setting size: {size_str}")
                    self._set_field(self._app.get_size_input(), size_str)

                # TP/SL: Skip entirely — position manager handles exits
                # TP/SL UI interaction causes tooltips and blocks the order
                tpsl_set = False

                # Read balance BEFORE order for verification
                balance_before = self._app.read_available_balance()
                logger.info(f"Balance before order: {balance_before} USDT")

                # Final refresh before clicking Buy/Long
                self._app.invalidate_cache()
                self._app._refresh_descendants()
                time.sleep(0.3)

                self._click_side_button(side)

                # Handle confirmation dialog and verify order
                time.sleep(1.5)
                self._handle_confirm_dialog()
                time.sleep(1.0)

                # Verify: check for error dialogs
                order_ok = self._verify_order_success()
                if not order_ok:
                    raise RuntimeError("Order rejected by Binance (error dialog detected)")

                # Verify: check balance changed (order actually went through)
                balance_after = self._app.read_available_balance()
                logger.info(f"Balance after order: {balance_after} USDT")
                if balance_before > 0 and balance_after > 0:
                    if abs(balance_before - balance_after) < 0.01:
                        logger.error(f"Balance unchanged ({balance_before} -> {balance_after}), "
                                     f"order likely NOT filled!")
                        raise RuntimeError("Order not filled: balance unchanged after Buy/Long click")

                self._event_bus.publish(EventType.ORDER_PLACED, {
                    "symbol": symbol,
                    "side": side.value,
                    "order_type": order_type.value,
                    "price": price,
                    "size": size,
                    "leverage": leverage,
                    "tp_percent": tp_percent if tpsl_set else None,
                    "sl_percent": sl_percent if tpsl_set else None,
                })
                lev_str = f" LEV={leverage}x" if leverage else ""
                tpsl_str = f" TP={tp_percent}% SL={sl_percent}%" if tpsl_set else " (no TP/SL)"
                logger.info(f"Order executed: {side.value} {size} {symbol} "
                            f"@ {price or 'market'}{lev_str}{tpsl_str}")
                return True

            except Exception as e:
                self._event_bus.publish(EventType.ORDER_FAILED, {
                    "symbol": symbol,
                    "side": side.value,
                    "error": str(e),
                })
                logger.error(f"Order execution failed: {e}")
                return False

    @staticmethod
    def _format_qty(qty: float, precision: int) -> str:
        """Format quantity to correct decimal places without trailing zeros."""
        if precision == 0:
            return str(int(qty))
        return f"{qty:.{precision}f}".rstrip("0").rstrip(".")

    def _select_order_type(self, order_type: OrderType) -> None:
        tab = self._app.get_order_type_tab(order_type.value)
        tab.click_input()
        time.sleep(0.15)

    def _set_field(self, element, value: str) -> None:
        """Set a field value by clicking, selecting all, deleting, and typing."""
        import pywinauto.keyboard as kb
        element.click_input()
        time.sleep(0.1)
        # Use keyboard directly (more reliable in Electron apps)
        kb.send_keys("^a", pause=0.05)
        time.sleep(0.05)
        kb.send_keys("{DELETE}", pause=0.05)
        time.sleep(0.05)
        # Type the value character by character for reliability
        kb.send_keys(value, pause=0.03)
        time.sleep(0.1)

    def _try_set_tpsl(self, tp_percent: float = None,
                      sl_percent: float = None) -> bool:
        """Try to set TP/SL values. Returns True if successfully set."""
        # Step 1: Enable TP/SL checkbox
        checkbox_checked = self._enable_tpsl()
        if not checkbox_checked:
            return False

        # Step 2: Refresh descendants since new inputs appeared
        self._app._refresh_descendants()
        time.sleep(0.5)

        # Step 3: Set TP value
        if tp_percent is not None:
            tp_input = self._find_tpsl_input(is_tp=True)
            if tp_input:
                self._set_field(tp_input, str(round(tp_percent, 1)))
            else:
                logger.warning("TP input not found")

        # Step 4: Set SL value
        if sl_percent is not None:
            sl_input = self._find_tpsl_input(is_tp=False)
            if sl_input:
                self._set_field(sl_input, str(round(sl_percent, 1)))
            else:
                logger.warning("SL input not found")

        return True

    def _find_tpsl_input(self, is_tp: bool):
        """Find TP or SL input field using multiple strategies."""
        index = 0 if is_tp else 1
        label = "Take Profit" if is_tp else "Stop Loss"

        # Strategy 1: Find Edit with name "ROI" (original approach)
        try:
            return self._app.find_element("Edit", name="ROI", found_index=index)
        except RuntimeError:
            pass

        # Strategy 2: Find Edit near TP/SL labels by position
        try:
            return self._app._find_edit_near_label(label)
        except RuntimeError:
            pass

        # Strategy 3: Find all Edits in the TP/SL area (below size input)
        try:
            size_input = self._app.get_size_input()
            size_rect = size_input.rectangle()
            # TP/SL inputs are below the size input
            edits = self._app.find_all_elements("Edit")
            tpsl_edits = []
            for edit in edits:
                try:
                    r = edit.rectangle()
                    name = edit.element_info.name or ""
                    aid = edit.element_info.automation_id or ""
                    # Below size input, same column area, not the search bar
                    if (r.top > size_rect.bottom and
                            r.left > 1500 and  # Right side order panel
                            "search" not in name.lower() and
                            "flashOrder" not in aid):
                        tpsl_edits.append(edit)
                except Exception:
                    continue

            if len(tpsl_edits) > index:
                return tpsl_edits[index]
        except Exception:
            pass

        # Strategy 4: Find by auto_id patterns
        descendants = self._app._get_descendants()
        tp_sl_edits = []
        for elem in descendants:
            try:
                ct = elem.element_info.control_type
                aid = elem.element_info.automation_id or ""
                if ct == "Edit" and ("tp" in aid.lower() or "sl" in aid.lower()
                                     or "profit" in aid.lower()
                                     or "loss" in aid.lower()
                                     or "roi" in aid.lower()):
                    tp_sl_edits.append(elem)
            except Exception:
                continue
        if len(tp_sl_edits) > index:
            return tp_sl_edits[index]

        return None

    def _cleanup_ui(self) -> None:
        """Press ESC to close any stale popups/modals, then refresh."""
        import pywinauto.keyboard as kb
        try:
            kb.send_keys("{ESC}", pause=0.1)
            time.sleep(0.3)
            kb.send_keys("{ESC}", pause=0.1)
            time.sleep(0.3)
        except Exception:
            pass
        self._app.invalidate_cache()
        self._app._refresh_descendants()
        logger.debug("UI cleanup after TP/SL failure")

    def _handle_confirm_dialog(self) -> None:
        """Click Confirm on order confirmation dialog if it appears."""
        self._app._refresh_descendants()
        # Look for Confirm button (order confirmation or margin mode confirmation)
        for _ in range(3):
            try:
                confirm = self._app.find_element("Button", name="Confirm")
                confirm.click_input()
                time.sleep(0.5)
                self._app._refresh_descendants()
                logger.info("Order confirmation dialog accepted")
                return
            except RuntimeError:
                pass
            # Also try "Confirm Order" variant
            try:
                confirm = self._app.find_element("Button", name_re=r"(?i)confirm.*order")
                confirm.click_input()
                time.sleep(0.5)
                logger.info("Order confirmation dialog accepted")
                return
            except RuntimeError:
                pass
            time.sleep(0.3)

    def _verify_order_success(self) -> bool:
        """Check if the order actually went through by looking for error messages."""
        self._app._refresh_descendants()
        descendants = self._app._get_descendants()

        error_keywords = [
            "insufficient", "yetersiz", "error", "hata",
            "failed", "rejected", "minimum", "invalid",
            "not enough", "exceed", "limit",
        ]

        for elem in descendants:
            try:
                ct = elem.element_info.control_type
                name = (elem.element_info.name or "").lower()
                if ct == "Text" and name:
                    for kw in error_keywords:
                        if kw in name:
                            rect = elem.rectangle()
                            # Only check elements in the center (dialog area)
                            if 400 < rect.left < 1400 and 200 < rect.top < 700:
                                logger.error(f"Binance error detected: '{elem.element_info.name}'")
                                # Close the error dialog
                                import pywinauto.keyboard as kb
                                try:
                                    ok_btn = self._app.find_element("Button", name_re=r"(?i)(ok|got it|close|dismiss)")
                                    ok_btn.click_input()
                                except RuntimeError:
                                    kb.send_keys("{ESC}", pause=0.1)
                                time.sleep(0.3)
                                return False
            except Exception:
                continue

        return True

    def _enable_tpsl(self) -> bool:
        """Enable TP/SL checkbox. Returns True if checkbox is now checked."""
        # Strategy 1: CheckBox with name "TP/SL"
        try:
            checkbox = self._app.get_tp_checkbox()
            toggle = checkbox.get_toggle_state()
            if toggle == 0:
                checkbox.click_input()
                time.sleep(0.5)
            return True
        except Exception:
            pass

        # Strategy 2: Look for any CheckBox near "TP/SL" text
        try:
            descendants = self._app._get_descendants()
            for elem in descendants:
                try:
                    ct = elem.element_info.control_type
                    name = elem.element_info.name or ""
                    if ct == "CheckBox" and ("TP" in name or "SL" in name
                                             or "tp" in name or "sl" in name):
                        toggle = elem.get_toggle_state()
                        if toggle == 0:
                            elem.click_input()
                            time.sleep(0.5)
                        return True
                except Exception:
                    continue
        except Exception:
            pass

        # Strategy 3: Find TP/SL text label and click nearby checkbox area
        try:
            tpsl_text = self._app.find_element("Text", name_re=r"TP.*SL")
            rect = tpsl_text.rectangle()
            # Checkbox is usually to the left of the label
            self._app._render_widget.click_input(coords=(
                rect.left - 20 - self._app._render_widget.rectangle().left,
                (rect.top + rect.bottom) // 2 - self._app._render_widget.rectangle().top
            ))
            time.sleep(0.5)
            return True
        except Exception:
            pass

        logger.warning("Could not find TP/SL checkbox")
        return False

    def _enable_reduce_only(self) -> None:
        try:
            checkbox = self._app.get_reduce_only_checkbox()
            toggle = checkbox.get_toggle_state()
            if toggle == 0:
                checkbox.click_input()
                time.sleep(0.3)
                logger.debug("Reduce-Only enabled")
        except Exception as e:
            logger.warning(f"Could not enable Reduce-Only: {e}")

    def _disable_reduce_only(self) -> None:
        """Uncheck Reduce-Only if it's checked (from a previous close order)."""
        try:
            checkbox = self._app.get_reduce_only_checkbox()
            toggle = checkbox.get_toggle_state()
            if toggle == 1:  # Currently checked -> uncheck it
                checkbox.click_input()
                time.sleep(0.3)
                logger.info("Reduce-Only UNCHECKED (was left on from previous order)")
        except Exception as e:
            logger.warning(f"Could not uncheck Reduce-Only: {e}")
            # Fallback: find any CheckBox with "Reduce" in name
            try:
                descendants = self._app._get_descendants()
                for elem in descendants:
                    try:
                        ct = elem.element_info.control_type
                        name = (elem.element_info.name or "").lower()
                        if ct == "CheckBox" and "reduce" in name:
                            toggle = elem.get_toggle_state()
                            if toggle == 1:
                                elem.click_input()
                                time.sleep(0.3)
                                logger.info("Reduce-Only unchecked (fallback)")
                            return
                    except Exception:
                        continue
            except Exception:
                pass

    def _click_side_button(self, side: OrderSide) -> None:
        if side == OrderSide.BUY_LONG:
            btn = self._app.get_buy_button()
        else:
            btn = self._app.get_sell_button()
        btn.click_input()
        time.sleep(0.3)
