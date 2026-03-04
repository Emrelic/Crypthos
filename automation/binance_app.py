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

    def read_available_balance(self) -> float:
        """Read 'Avbl X.XX USDT' from the order panel. Returns 0.0 on failure."""
        self._refresh_descendants()
        descendants = self._get_descendants()
        for elem in descendants:
            try:
                ct = elem.element_info.control_type
                name = elem.element_info.name or ""
                # Look for "Avbl" or text containing USDT balance
                if ct == "Text" and "Avbl" in name:
                    # e.g. "Avbl 1.99 USDT" or just near it
                    match = re.search(r"(\d+\.?\d*)\s*USDT", name)
                    if match:
                        val = float(match.group(1))
                        logger.info(f"Read available balance from UI: {val} USDT")
                        return val
            except Exception:
                continue

        # Strategy 2: Find "Avbl" text then look for adjacent number
        for i, elem in enumerate(descendants):
            try:
                ct = elem.element_info.control_type
                name = (elem.element_info.name or "").strip()
                if ct == "Text" and name == "Avbl":
                    # Check next few elements for the number
                    for j in range(i + 1, min(i + 5, len(descendants))):
                        try:
                            next_name = (descendants[j].element_info.name or "").strip()
                            match = re.search(r"(\d+\.?\d*)", next_name)
                            if match:
                                val = float(match.group(1))
                                if val < 100000:  # sanity check
                                    logger.info(f"Read available balance from UI: {val} USDT")
                                    return val
                        except Exception:
                            continue
            except Exception:
                continue

        logger.warning("Could not read available balance from UI")
        return 0.0

    def get_ui_max_leverage(self) -> int:
        """Read the max leverage from the leverage modal slider labels.
        Opens the modal, reads labels, closes it. Returns max leverage or 0."""
        try:
            lev_btn = self.get_leverage_button()
            lev_btn.click_input()
            time.sleep(1.5)
            self._refresh_descendants()
            time.sleep(0.5)

            # Verify modal opened
            modal_open = False
            try:
                self.find_element("Text", name="Adjust Leverage")
                modal_open = True
            except RuntimeError:
                # Retry click
                time.sleep(0.5)
                lev_btn = self.get_leverage_button()
                lev_btn.click_input()
                time.sleep(2.0)
                self._refresh_descendants()
                try:
                    self.find_element("Text", name="Adjust Leverage")
                    modal_open = True
                except RuntimeError:
                    pass

            max_lev = 0
            if modal_open:
                max_lev = self._read_modal_max_leverage()
                # If first read fails, wait and retry
                if max_lev == 0:
                    time.sleep(1.0)
                    self._refresh_descendants()
                    max_lev = self._read_modal_max_leverage()

            # Close modal
            try:
                close_btn = self.find_element("Button", name="Close")
                close_btn.click_input()
            except RuntimeError:
                import pywinauto.keyboard as kb
                kb.send_keys("{ESC}")
            time.sleep(0.5)
            self.invalidate_cache()
            return max_lev
        except Exception as e:
            logger.warning(f"Failed to read UI max leverage: {e}")
            try:
                import pywinauto.keyboard as kb
                kb.send_keys("{ESC}")
                time.sleep(0.3)
            except Exception:
                pass
            return 0

    def _read_modal_max_leverage(self) -> int:
        """Read max leverage from the slider labels in an open leverage modal.
        Also reads from the 'Maximum available' text if present."""
        descendants = self._get_descendants()
        max_lev = 0

        for elem in descendants:
            try:
                ct = elem.element_info.control_type
                name = elem.element_info.name or ""

                # Strategy 1: Slider labels like "1x", "4x", "20x", "125x"
                if ct == "Text" and name.endswith("x") and name[:-1].isdigit():
                    val = int(name[:-1])
                    if val > max_lev:
                        max_lev = val

                # Strategy 2: "Maximum available" or similar text
                if ct == "Text" and ("maximum" in name.lower()
                                     or "max" in name.lower()):
                    match = re.search(r"(\d+)\s*x", name)
                    if match:
                        val = int(match.group(1))
                        if val > max_lev:
                            max_lev = val
            except Exception:
                continue
        return max_lev

    def set_leverage(self, value: int) -> bool:
        """Click the leverage button, enter value in the modal, confirm.
        Uses the input between decrease/increase buttons (not Edit field).
        Returns True on success."""
        try:
            lev_btn = self.get_leverage_button()
            current_name = lev_btn.element_info.name or ""
            current_match = re.search(r"(\d+)x", current_name)
            if current_match and int(current_match.group(1)) == value:
                logger.info(f"Leverage already at {value}x, skipping")
                return True

            lev_btn.click_input()
            time.sleep(1.5)
            self._refresh_descendants()
            time.sleep(0.3)

            # Verify modal is open by checking for "Adjust Leverage" text
            modal_open = False
            try:
                self.find_element("Text", name="Adjust Leverage")
                modal_open = True
            except RuntimeError:
                pass
            if not modal_open:
                logger.warning("Leverage modal did not open, retrying click")
                time.sleep(0.5)
                lev_btn = self.get_leverage_button()
                lev_btn.click_input()
                time.sleep(2.0)
                self._refresh_descendants()

            # Read max leverage from slider labels
            ui_max = self._read_modal_max_leverage()
            if ui_max > 0 and value > ui_max:
                logger.warning(f"Requested {value}x > UI max {ui_max}x, clamping")
                value = ui_max

            # Find the input area between decrease/increase buttons
            dec_btn = None
            inc_btn = None
            try:
                dec_btn = self.find_element("Button", name="decrease value")
                inc_btn = self.find_element("Button", name="increase value")
            except RuntimeError:
                pass

            input_clicked = False
            if dec_btn and inc_btn:
                # Click in the middle of the area between +/- buttons
                dec_rect = dec_btn.rectangle()
                inc_rect = inc_btn.rectangle()
                mid_x = (dec_rect.right + inc_rect.left) // 2
                mid_y = (dec_rect.top + dec_rect.bottom) // 2

                import pywinauto.keyboard as kb
                # Click the input area using the render widget
                self._render_widget.click_input(coords=(
                    mid_x - self._render_widget.rectangle().left,
                    mid_y - self._render_widget.rectangle().top
                ))
                time.sleep(0.1)
                input_clicked = True

            if not input_clicked:
                # Fallback: try to find any unnamed element in modal area
                descendants = self._get_descendants()
                for elem in descendants:
                    try:
                        ct = elem.element_info.control_type
                        if ct in ("Edit", "Custom", "Text"):
                            rect = elem.rectangle()
                            name = elem.element_info.name or ""
                            # Look for element in the leverage input area
                            if (400 < rect.top < 450 and 830 < rect.left < 1000
                                    and not name):
                                elem.click_input()
                                input_clicked = True
                                break
                    except Exception:
                        continue

            # Clear and type the value using keyboard
            import pywinauto.keyboard as kb
            kb.send_keys("^a", pause=0.05)
            time.sleep(0.05)
            kb.send_keys("{DELETE}", pause=0.05)
            time.sleep(0.05)
            kb.send_keys(str(value), pause=0.03)
            time.sleep(0.2)

            # Find and click Confirm button
            confirm_btn = None
            self._refresh_descendants()
            try:
                confirm_btn = self.find_element("Button", name="Confirm")
            except RuntimeError:
                pass

            if confirm_btn:
                confirm_btn.click_input()
                time.sleep(1.0)
            else:
                logger.warning("Confirm button not found, pressing Enter")
                kb.send_keys("{ENTER}", pause=0.05)
                time.sleep(1.0)

            self.invalidate_cache()
            self._refresh_descendants()
            time.sleep(0.3)

            # Verify leverage changed
            try:
                new_btn = self.get_leverage_button()
                new_name = new_btn.element_info.name or ""
                new_match = re.search(r"(\d+)x", new_name)
                if new_match:
                    actual = int(new_match.group(1))
                    if actual == value:
                        logger.info(f"Leverage set to {value}x (verified)")
                        return True
                    else:
                        logger.warning(f"Leverage button shows {actual}x "
                                       f"(expected {value}x)")
                        return actual > 1  # At least it changed
            except RuntimeError:
                pass

            logger.info(f"Leverage set to {value}x")
            return True

        except Exception as e:
            logger.error(f"Failed to set leverage to {value}x: {e}")
            try:
                import pywinauto.keyboard as kb
                kb.send_keys("{ESC}")
                time.sleep(0.3)
            except Exception:
                pass
            return False

    def ensure_isolated_mode(self) -> bool:
        """Switch to Isolated margin mode if currently Cross."""
        try:
            self._refresh_descendants()
            btn = self.get_margin_mode_button()
            name = btn.element_info.name or ""

            if "Isolated" in name:
                logger.info("Already in Isolated mode")
                return True

            if "Cross" not in name:
                logger.warning(f"Unknown margin mode: '{name}'")
                return False

            # Click Cross button to open margin mode selector
            btn_rect = btn.rectangle()
            logger.info(f"Clicking margin mode button '{name}' "
                        f"at ({btn_rect.left},{btn_rect.top})")
            btn.click_input()
            time.sleep(1.5)
            self._refresh_descendants()
            time.sleep(0.5)

            # Try to find "Isolated" button/text in the popup
            import pywinauto.keyboard as kb
            iso_found = False

            # Strategy 1: Find Isolated button
            try:
                iso_btn = self.find_element("Button", name="Isolated")
                iso_btn.click_input()
                iso_found = True
                time.sleep(0.5)
            except RuntimeError:
                pass

            # Strategy 2: Find Isolated text and click it
            if not iso_found:
                try:
                    iso_text = self.find_element("Text", name="Isolated")
                    iso_text.click_input()
                    iso_found = True
                    time.sleep(0.5)
                except RuntimeError:
                    pass

            # Strategy 3: Look for any element with "Isolated" in name
            if not iso_found:
                descendants = self._get_descendants()
                for elem in descendants:
                    try:
                        ename = elem.element_info.name or ""
                        if "Isolated" in ename:
                            elem.click_input()
                            iso_found = True
                            time.sleep(0.5)
                            break
                    except Exception:
                        continue

            # Strategy 4: The popup might show Cross/Isolated as tabs
            # Try clicking at a position offset from the Cross button
            if not iso_found:
                logger.warning("Isolated element not found, trying keyboard")
                # In some versions, pressing Tab then Enter works
                kb.send_keys("{TAB}{ENTER}", pause=0.1)
                time.sleep(0.5)
                iso_found = True

            if iso_found:
                # Wait for confirmation dialog
                self._refresh_descendants()
                time.sleep(0.3)
                try:
                    confirm = self.find_element("Button", name="Confirm")
                    confirm.click_input()
                    time.sleep(0.5)
                except RuntimeError:
                    # Try keyboard Enter
                    kb.send_keys("{ENTER}", pause=0.1)
                    time.sleep(0.5)

            self.invalidate_cache()
            self._refresh_descendants()

            # Verify
            try:
                new_btn = self.get_margin_mode_button()
                new_name = new_btn.element_info.name or ""
                if "Isolated" in new_name:
                    logger.info("Switched to Isolated margin mode (verified)")
                    return True
                else:
                    logger.warning(f"Margin mode still shows: '{new_name}'")
                    return False
            except RuntimeError:
                pass

            logger.info("Switched to Isolated margin mode")
            return True
        except Exception as e:
            logger.warning(f"Could not ensure Isolated mode: {e}")
            try:
                import pywinauto.keyboard as kb
                kb.send_keys("{ESC}")
                time.sleep(0.3)
            except Exception:
                pass
            return False

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
        """Find the Size edit field in the MAIN order form (right side).
        Must NOT return the flash order input at the bottom."""
        # Strategy 1: Find by auto_id (most reliable)
        try:
            return self.find_element("Edit", auto_id="unitAmount-62")
        except RuntimeError:
            pass
        # Strategy 2: Find unnamed Edit near the "Size" label in the order panel
        try:
            return self._find_edit_near_label("Size")
        except RuntimeError:
            pass
        # Strategy 3: Find Edit with name "Size" (exact match, not "Enter Size")
        try:
            return self.find_element("Edit", name="Size")
        except RuntimeError:
            pass
        # Strategy 4: Try any Edit with auto_id containing 'unitAmount'
        descendants = self._get_descendants()
        for elem in descendants:
            try:
                ct = elem.element_info.control_type
                aid = elem.element_info.automation_id or ""
                if ct == "Edit" and "unitAmount" in aid:
                    return elem
            except Exception:
                continue
        raise RuntimeError("Size edit field not found in main order form")

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

    def ensure_visible(self, min_elements: int = 300) -> bool:
        """Ensure Binance window is visible and has enough UI elements loaded.
        Returns True if ready for interaction."""
        try:
            if not self._main_handle:
                return False

            self._force_foreground(self._main_handle)
            time.sleep(0.5)

            # Quick check if we already have enough elements
            self._refresh_descendants()
            if len(self._descendants) >= min_elements:
                return True

            # Window was probably minimized - wait for Electron to render
            logger.info(f"Waiting for Binance to render "
                        f"(have {len(self._descendants)}, need {min_elements})...")

            for attempt in range(8):
                time.sleep(1.5)

                # Re-acquire render widget if needed
                if attempt == 2:
                    render_handle = self._find_render_widget_handle(self._main_handle)
                    if render_handle:
                        app_render = Application(backend="uia")
                        app_render.connect(handle=render_handle)
                        self._render_widget = app_render.window(handle=render_handle)

                self._force_foreground(self._main_handle)
                self._refresh_descendants()
                count = len(self._descendants)

                if count >= min_elements:
                    logger.info(f"Binance ready: {count} elements (attempt {attempt+1})")
                    return True

                logger.debug(f"Binance render attempt {attempt+1}: {count} elements")

            logger.warning(f"Binance only has {len(self._descendants)} elements "
                           f"after 8 attempts (need {min_elements})")
            return len(self._descendants) >= 100  # partial success
        except Exception as e:
            logger.warning(f"Could not restore window: {e}")
            return False

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
