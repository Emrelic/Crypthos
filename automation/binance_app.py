import re
import time
import threading
from loguru import logger
from pywinauto import Application
from automation.element_cache import ElementCache
from core.constants import BinanceUI


class BinanceApp:
    """Connects to Binance Desktop via pywinauto UIA backend.
    Uses Chrome_RenderWidgetHostHWND + descendants() for element discovery
    (required for Electron apps)."""

    def __init__(self):
        self._app = None
        self._main_window = None
        self._render_widget = None
        self._main_handle = None
        self._connected = False
        self._cache = ElementCache(ttl_seconds=30.0)
        # Descendants cache with short TTL
        self._descendants = []
        self._descendants_ts = 0.0
        self._descendants_ttl = 5.0  # refresh every 5 seconds
        self._descendants_lock = threading.Lock()

    def connect(self) -> bool:
        try:
            import win32gui
            import win32con
            from pywinauto.findwindows import find_windows

            # Step 1: Find main window handle via win32
            handles = find_windows(title="Binance Desktop", backend="win32")
            if not handles:
                handles = find_windows(title_re=".*Binance.*", backend="win32")
            if not handles:
                raise RuntimeError("Binance Desktop window not found")

            self._main_handle = handles[0]
            logger.info(f"Binance window handle found: {self._main_handle}")

            # Step 2: Force window visible and to foreground
            # Render widget only exists when window is visible and not minimized
            self._force_foreground(self._main_handle)
            time.sleep(3.0)  # Electron needs time to render after foreground

            # Step 3: Connect UIA to main window
            self._app = Application(backend="uia")
            self._app.connect(handle=self._main_handle)
            self._main_window = self._app.window(handle=self._main_handle)
            self._main_window.wrapper_object()
            logger.info("Main window accessible")

            # Step 4: Find Chrome_RenderWidgetHostHWND child (contains all actual UI)
            # Retry with increasing delays since the render widget may take time
            render_handle = None
            for attempt in range(8):
                render_handle = self._find_render_widget_handle(self._main_handle)
                if render_handle:
                    break
                logger.debug(f"Render widget not found, attempt {attempt + 1}/8")
                # Keep trying to bring window to foreground
                try:
                    win32gui.ShowWindow(self._main_handle, win32con.SW_SHOW)
                    win32gui.SetForegroundWindow(self._main_handle)
                except Exception:
                    pass
                time.sleep(0.5 + attempt * 0.3)

            if render_handle:
                logger.info(f"Render widget handle: {render_handle}")
                app_render = Application(backend="uia")
                app_render.connect(handle=render_handle)
                self._render_widget = app_render.window(handle=render_handle)
            else:
                logger.warning("Render widget not found, using main window")
                self._render_widget = self._main_window

            # Step 5: Warm up descendants cache (retry with increasing waits)
            # Electron render widget needs time after foreground switch
            for attempt in range(5):
                wait = [0.5, 2.0, 3.0, 4.0, 5.0][attempt]
                time.sleep(wait)
                self._refresh_descendants()
                count = len(self._descendants)
                logger.info(f"Descendants attempt {attempt+1}: {count} elements")
                if count >= 200:
                    break
                # Force foreground on each retry
                try:
                    win32gui.ShowWindow(self._main_handle, win32con.SW_SHOW)
                    win32gui.SetForegroundWindow(self._main_handle)
                except Exception:
                    pass

            if count < 20:
                logger.warning(
                    "Very few descendants found. Binance may be loading or minimized."
                )

            self._connected = True
            logger.info("Connected to Binance Desktop")
            return True
        except Exception as e:
            logger.error(f"Failed to connect to Binance Desktop: {e}")
            self._connected = False
            return False

    @staticmethod
    def _force_foreground(hwnd: int) -> None:
        """Force a window to foreground using multiple strategies."""
        import win32gui
        import win32con
        import ctypes

        if win32gui.IsIconic(hwnd):
            logger.info("Binance window is minimized, restoring...")
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            time.sleep(1.5)

        win32gui.ShowWindow(hwnd, win32con.SW_SHOW)

        # Strategy 1: Standard SetForegroundWindow
        try:
            win32gui.SetForegroundWindow(hwnd)
        except Exception:
            pass

        # Strategy 2: AttachThreadInput trick (works when another app has focus)
        try:
            user32 = ctypes.windll.user32
            fg_hwnd = user32.GetForegroundWindow()
            if fg_hwnd != hwnd:
                fg_thread = user32.GetWindowThreadProcessId(fg_hwnd, None)
                target_thread = user32.GetWindowThreadProcessId(hwnd, None)
                if fg_thread != target_thread:
                    user32.AttachThreadInput(fg_thread, target_thread, True)
                    win32gui.BringWindowToTop(hwnd)
                    win32gui.SetForegroundWindow(hwnd)
                    user32.AttachThreadInput(fg_thread, target_thread, False)
        except Exception:
            pass

        # Strategy 3: Use keybd_event(ALT) trick to allow SetForegroundWindow
        fg = win32gui.GetForegroundWindow()
        if fg != hwnd:
            try:
                user32 = ctypes.windll.user32
                # Press and release ALT to trick Windows into allowing focus change
                user32.keybd_event(0x12, 0, 0, 0)  # ALT down
                user32.keybd_event(0x12, 0, 2, 0)  # ALT up
                win32gui.SetForegroundWindow(hwnd)
            except Exception:
                pass

    @staticmethod
    def _find_render_widget_handle(parent_handle: int):
        """Find Chrome_RenderWidgetHostHWND child window using win32 API."""
        import win32gui
        results = []

        def callback(hwnd, _):
            class_name = win32gui.GetClassName(hwnd)
            if class_name == "Chrome_RenderWidgetHostHWND":
                results.append(hwnd)
            return True

        win32gui.EnumChildWindows(parent_handle, callback, None)
        return results[0] if results else None

    def _refresh_descendants(self) -> list:
        """Refresh the cached descendants list from render widget."""
        with self._descendants_lock:
            try:
                self._descendants = self._render_widget.descendants()
                self._descendants_ts = time.time()
            except Exception as e:
                logger.warning(f"Failed to refresh descendants: {e}")
                self._descendants = []
        return self._descendants

    def _get_descendants(self) -> list:
        """Get descendants with TTL cache."""
        if time.time() - self._descendants_ts > self._descendants_ttl:
            return self._refresh_descendants()
        return self._descendants

    def find_element(self, control_type: str, name: str = None,
                     name_re: str = None, auto_id: str = None,
                     found_index: int = 0, **kwargs):
        """Find a UI element by searching through descendants.
        This works for Electron apps where child_window() fails."""
        descendants = self._get_descendants()

        matches = []
        for elem in descendants:
            try:
                elem_type = elem.element_info.control_type
                if elem_type != control_type:
                    continue

                if name is not None:
                    elem_name = elem.element_info.name or ""
                    if elem_name != name:
                        continue

                if name_re is not None:
                    elem_name = elem.element_info.name or ""
                    if not re.search(name_re, elem_name):
                        continue

                if auto_id is not None:
                    elem_auto_id = elem.element_info.automation_id or ""
                    if elem_auto_id != auto_id:
                        continue

                matches.append(elem)
            except Exception:
                continue

        if not matches:
            raise RuntimeError(
                f"Element not found: type={control_type} name={name} "
                f"name_re={name_re} auto_id={auto_id}"
            )

        if found_index >= len(matches):
            raise RuntimeError(
                f"Found {len(matches)} matches but requested index {found_index}: "
                f"type={control_type} name={name} name_re={name_re}"
            )

        return matches[found_index]

    def find_all_elements(self, control_type: str, name: str = None,
                          name_re: str = None) -> list:
        """Find all matching UI elements."""
        descendants = self._get_descendants()
        matches = []
        for elem in descendants:
            try:
                elem_type = elem.element_info.control_type
                if elem_type != control_type:
                    continue
                if name is not None:
                    elem_name = elem.element_info.name or ""
                    if elem_name != name:
                        continue
                if name_re is not None:
                    elem_name = elem.element_info.name or ""
                    if not re.search(name_re, elem_name):
                        continue
                matches.append(elem)
            except Exception:
                continue
        return matches

    def get_pair_button(self):
        return self._cache.get_or_find(
            "pair_button",
            lambda: self.find_element("Button", name_re=".*Perpetual.*"),
        )

    def get_order_type_tab(self, order_type: str):
        return self.find_element("TabItem", name=order_type)

    def get_price_input(self):
        return self._cache.get_or_find(
            "price_input",
            lambda: self._find_price_edit(),
        )

    def get_size_input(self):
        return self._cache.get_or_find(
            "size_input",
            lambda: self._find_size_edit(),
        )

    def get_tp_checkbox(self):
        return self.find_element("CheckBox", name="TP/SL")

    def get_tp_roi_input(self):
        """Take Profit ROI% input."""
        return self.find_element("Edit", name="ROI", found_index=0)

    def get_sl_roi_input(self):
        """Stop Loss ROI% input."""
        return self.find_element("Edit", name="ROI", found_index=1)

    def get_buy_button(self):
        return self._cache.get_or_find(
            "buy_button",
            lambda: self.find_element("Button", name="Buy/Long"),
        )

    def get_sell_button(self):
        return self._cache.get_or_find(
            "sell_button",
            lambda: self.find_element("Button", name="Sell/Short"),
        )

    def get_reduce_only_checkbox(self):
        return self.find_element("CheckBox", name="Reduce-Only")

    def get_bbo_checkbox(self):
        return self.find_element("CheckBox", name="BBO")

    def get_margin_mode_button(self):
        return self.find_element("Button", name_re=".*(Isolated|Cross).*")

    def get_leverage_button(self):
        return self.find_element("Button", name_re=r".*\d+x.*")

    def get_search_edit(self):
        """Search box that appears in pair selector popup."""
        # Force refresh because popup just opened
        self._refresh_descendants()
        edits = self.find_all_elements("Edit")
        if edits:
            return edits[0]
        raise RuntimeError("Search edit not found")

    def _find_price_edit(self):
        """Find the Price edit field.
        In Limit mode, there's a Price edit. In Market mode, it doesn't exist."""
        try:
            return self.find_element("Edit", name="Price")
        except RuntimeError:
            pass
        # Try the unnamed edit that appears near price label
        return self._find_edit_near_label("Price")

    def _find_size_edit(self):
        """Find the Size edit field.
        Binance shows 'Enter Size' as edit name in Market mode."""
        for candidate in ["Size", "Enter Size"]:
            try:
                return self.find_element("Edit", name=candidate)
            except RuntimeError:
                continue
        try:
            return self.find_element("Edit", name_re=".*[Ss]ize.*")
        except RuntimeError:
            pass
        return self._find_edit_near_label("Size")

    def _find_edit_near_label(self, label_text: str):
        """Find an Edit field associated with a label text by position."""
        descendants = self._get_descendants()

        label_elem = None
        for elem in descendants:
            try:
                if (elem.element_info.control_type == "Text" and
                        elem.element_info.name and
                        label_text in elem.element_info.name):
                    label_elem = elem
                    break
            except Exception:
                continue

        if label_elem is None:
            raise RuntimeError(f"Label '{label_text}' not found")

        label_rect = label_elem.rectangle()
        edits = self.find_all_elements("Edit")
        best = None
        best_dist = float("inf")
        for edit in edits:
            try:
                r = edit.rectangle()
                dy = abs(r.top - label_rect.top)
                dx = abs(r.left - label_rect.left)
                if dy < 50 and dx < 300:
                    dist = dy + dx
                    if dist < best_dist:
                        best_dist = dist
                        best = edit
            except Exception:
                continue

        if best:
            return best
        raise RuntimeError(f"Could not find Edit field for label '{label_text}'")

    def ensure_visible(self) -> None:
        """Ensure Binance window is visible and render widget is available."""
        try:
            if not self._main_handle:
                return

            self._force_foreground(self._main_handle)

            # Check if we need to re-acquire render widget
            current_count = len(self._descendants)
            if current_count < 100:
                time.sleep(2.0)  # Wait for Electron to render
                render_handle = self._find_render_widget_handle(self._main_handle)
                if render_handle:
                    app_render = Application(backend="uia")
                    app_render.connect(handle=render_handle)
                    self._render_widget = app_render.window(handle=render_handle)
                time.sleep(2.0)
                self._refresh_descendants()
                logger.info(f"Render widget re-acquired: {len(self._descendants)} elements")
        except Exception as e:
            logger.warning(f"Could not restore window: {e}")

    def invalidate_cache(self) -> None:
        self._cache.invalidate()
        self._descendants_ts = 0.0

    def refresh_connection(self) -> bool:
        self._connected = False
        self._cache.invalidate()
        self._descendants = []
        self._descendants_ts = 0.0
        return self.connect()

    @property
    def is_connected(self) -> bool:
        if not self._connected:
            return False
        try:
            self._main_window.is_visible()
            return True
        except Exception:
            self._connected = False
            return False
