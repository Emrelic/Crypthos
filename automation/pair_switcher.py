import time
from loguru import logger
from automation.binance_app import BinanceApp
from core.event_bus import EventBus
from core.constants import EventType


class PairSwitcher:
    """Switches trading pair in Binance Desktop UI."""

    def __init__(self, binance_app: BinanceApp, event_bus: EventBus):
        self._app = binance_app
        self._event_bus = event_bus

    def switch_to(self, symbol: str) -> bool:
        try:
            if not self._app.is_connected:
                if not self._app.refresh_connection():
                    raise RuntimeError("Binance Desktop not connected")

            # Ensure window is visible before interacting
            self._app.ensure_visible()
            time.sleep(0.3)

            # Close any existing popups first
            try:
                self._app._main_window.type_keys("{ESC}")
                time.sleep(0.5)
            except Exception:
                pass

            # Refresh descendants to make sure we have the latest UI state
            self._app.invalidate_cache()
            self._app._refresh_descendants()

            # Click pair selector button (e.g. "DOGEUSDT Perpetual")
            pair_btn = self._app.get_pair_button()
            pair_btn.click_input()
            time.sleep(1.0)

            # Refresh descendants to see popup elements
            self._app._refresh_descendants()
            time.sleep(0.3)

            # Find search box (first Edit element in popup)
            edits = self._app.find_all_elements("Edit")
            if not edits:
                raise RuntimeError("Search edit not found in pair popup")
            search = edits[0]

            # Clear and type search query (base symbol without USDT)
            search.click_input()
            time.sleep(0.1)
            search.type_keys("^a", pause=0.03)
            search.type_keys("{DELETE}", pause=0.03)
            time.sleep(0.1)

            base = symbol.replace("USDT", "").replace("BUSD", "")
            search.type_keys(base, with_spaces=True, pause=0.05)
            time.sleep(1.5)  # Wait for search results to load

            # Refresh descendants to see search results
            self._app._refresh_descendants()
            time.sleep(0.3)

            # Search results appear as Text elements with exact symbol names
            # e.g. Text name='NEARUSDT' - find and click it
            # Look for Text with exact symbol name (in Futures section)
            descendants = self._app._get_descendants()
            target = None
            for elem in descendants:
                try:
                    name = elem.element_info.name or ""
                    ctrl_type = elem.element_info.control_type
                    if name == symbol and ctrl_type == "Text":
                        # Make sure it has a real position (not 0,0)
                        rect = elem.rectangle()
                        if rect.left > 0 and rect.top > 0:
                            target = elem
                            break
                except Exception:
                    continue

            if target:
                target.click_input()
                logger.debug(f"Clicked Text '{symbol}' for pair switch")
            else:
                # Fallback: try regex match for any element containing the symbol
                for elem in descendants:
                    try:
                        name = elem.element_info.name or ""
                        if symbol in name and elem.element_info.control_type in (
                            "Text", "Button", "ListItem", "Custom", "Hyperlink"
                        ):
                            rect = elem.rectangle()
                            if rect.left > 0 and rect.top > 0:
                                elem.click_input()
                                logger.debug(f"Clicked fallback '{name}' for {symbol}")
                                target = elem
                                break
                    except Exception:
                        continue

            if not target:
                raise RuntimeError(
                    f"No clickable element found for {symbol} in search results"
                )

            time.sleep(1.5)  # Wait for UI to reload with new pair

            # Invalidate element cache since UI structure has changed
            self._app.invalidate_cache()

            logger.info(f"Pair switched to {symbol}")
            return True

        except Exception as e:
            logger.error(f"Failed to switch to {symbol}: {e}")
            # Try to close any open popup by pressing Escape
            try:
                self._app._main_window.type_keys("{ESC}")
                time.sleep(0.3)
            except Exception:
                pass
            return False

    def get_current_pair(self) -> str:
        try:
            pair_btn = self._app.get_pair_button()
            name = pair_btn.window_text()
            # "DOGEUSDT Perpetual" -> "DOGEUSDT"
            return name.split()[0] if name else ""
        except Exception:
            return ""
