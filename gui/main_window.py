import keyboard
import customtkinter as ctk
from loguru import logger
from core.constants import EventType
from gui.widgets.status_bar import StatusBar
from gui.panels.scanner_panel import ScannerPanel
from gui.panels.system_b_panel import SystemBPanel
from gui.panels.system_c_panel import SystemCPanel
from gui.panels.system_d_panel import SystemDPanel
from gui.panels.system_e_panel import SystemEPanel
from gui.panels.system_f_panel import SystemFPanel
from gui.panels.system_g_panel import SystemGPanel
from gui.panels.system_h_panel import SystemHPanel
from gui.panels.system_i_panel import SystemIPanel
from gui.panels.system_j_panel import SystemJPanel
from gui.panels.system_m_panel import SystemMPanel
from gui.panels.system_n_panel import SystemNPanel
from gui.panels.quick_order_panel import QuickOrderPanel
from gui.panels.market_panel import MarketPanel
from gui.panels.strategy_panel import StrategyPanel
from gui.panels.strategy_settings_panel import StrategySettingsPanel
from gui.panels.trade_report_panel import TradeReportPanel
from gui.panels.activity_panel import ActivityPanel
from gui.panels.settings_panel import SettingsPanel


class MainWindow(ctk.CTk):
    """Root CustomTkinter window with tabbed interface."""

    def __init__(self, controller):
        # Set appearance BEFORE creating window
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")

        # Override _windows_set_titlebar_color to avoid withdraw/deiconify race
        # that causes invisible window on Windows with many panels
        _orig_set_titlebar = ctk.CTk._windows_set_titlebar_color
        def _safe_set_titlebar(self_inner, color_mode):
            """Set dark titlebar without withdraw/deiconify cycle."""
            import sys as _sys
            if not _sys.platform.startswith("win"):
                return
            try:
                import ctypes as _ctypes
                hwnd = _ctypes.windll.user32.GetParent(self_inner.winfo_id())
                value = 1 if color_mode.lower() == "dark" else 0
                _ctypes.windll.dwmapi.DwmSetWindowAttribute(
                    hwnd, 20, _ctypes.byref(_ctypes.c_int(value)),
                    _ctypes.sizeof(_ctypes.c_int(value)))
            except Exception:
                pass
        ctk.CTk._windows_set_titlebar_color = _safe_set_titlebar

        super().__init__()
        self.controller = controller

        self.title("Crypthos Trading Bot")
        self.geometry("1200x800")
        self.minsize(900, 600)

        # Status bar
        self._status_bar = StatusBar(self, controller)
        self._status_bar.pack(fill="x", padx=5, pady=(5, 0))

        # ═══ VIEW SWITCHER: Sistemler / Araclar ═══
        _sw_frame = ctk.CTkFrame(self, fg_color="transparent", height=32)
        _sw_frame.pack(fill="x", padx=10, pady=(2, 0))
        self._btn_sistemler = ctk.CTkButton(
            _sw_frame, text="Sistemler", width=120, height=28,
            font=ctk.CTkFont(size=12, weight="bold"),
            fg_color="#3d5afe", hover_color="#536DFE",
            command=lambda: self._switch_view("Sistemler"),
        )
        self._btn_sistemler.pack(side="left", padx=(0, 4))
        self._btn_araclar = ctk.CTkButton(
            _sw_frame, text="Araclar", width=120, height=28,
            font=ctk.CTkFont(size=12, weight="bold"),
            fg_color="#455A64", hover_color="#546E7A",
            command=lambda: self._switch_view("Araclar"),
        )
        self._btn_araclar.pack(side="left")
        self._view_switch = _sw_frame  # pack reference olarak kullan
        logger.info("[VIEW] View switch buttons created")

        # ═══ SYSTEM TABS (sadece aktif sistemler baştan, diğerleri lazy) ═══
        self._sys_tabview = ctk.CTkTabview(self)

        # Aktif sistemleri baştan oluştur
        tab_n = self._sys_tabview.add("N")
        tab_m = self._sys_tabview.add("M")

        self._system_n_panel = SystemNPanel(tab_n, controller)
        self._system_m_panel = SystemMPanel(tab_m, controller)
        self._sys_tabview.set("N")

        # Pasif sistemler — placeholder (lazy-load, widget limiti koruması)
        self._system_j_panel = None
        self._system_i_panel = None
        self._scanner_panel = None
        self._system_b_panel = None
        self._system_c_panel = None
        self._system_d_panel = None
        self._system_e_panel = None
        self._system_f_panel = None
        self._system_g_panel = None
        self._system_h_panel = None

        # ═══ TOOL TABS (lazy — built on first Araclar click) ═══
        self._tool_tabview = None
        self._tools_built = False
        # Placeholders for event handlers
        class _Noop:
            def add_log_entry(self, *a, **kw): pass
            def refresh_orders(self): pass
        self._activity_panel = _Noop()
        self._quick_panel = None
        self._market_panel = None

        # Default: Sistemler gorunur
        self._sys_tabview.pack(after=self._view_switch,
                               fill="both", expand=True, padx=5, pady=(0, 5))
        self._active_view = "Sistemler"

        # Register global hotkeys
        self._register_hotkeys()

        # Subscribe to events for logging
        controller.event_bus.subscribe(EventType.ORDER_PLACED, self._on_order_event)
        controller.event_bus.subscribe(EventType.ORDER_FAILED, self._on_order_event)
        controller.event_bus.subscribe(EventType.STRATEGY_SIGNAL, self._on_strategy_event)
        controller.event_bus.subscribe(EventType.LOG_MESSAGE, self._on_log_event)
        controller.event_bus.subscribe(EventType.KILL_SWITCH, self._on_kill_event)
        controller.event_bus.subscribe(EventType.POSITION_OPENED, self._on_position_opened)
        controller.event_bus.subscribe(EventType.POSITION_CLOSED, self._on_position_closed)
        controller.event_bus.subscribe(EventType.TRADE_RESULT, self._on_trade_result)
        controller.event_bus.subscribe(EventType.SCANNER_STATE_CHANGE, self._on_scanner_state)
        controller.event_bus.subscribe(EventType.CONNECTION_STATUS, self._on_connection_status)
        controller.event_bus.subscribe(EventType.REGIME_CHANGE, self._on_regime_change)

        # Start UI refresh
        self._refresh_ui()

        # Maximize after mainloop starts
        self.after(100, self._ensure_visible)

        # On close
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _switch_view(self, value: str) -> None:
        """Switch between Sistemler and Araclar views."""
        logger.info(f"[VIEW] _switch_view called: '{value}' (current: '{self._active_view}')")
        if value == self._active_view:
            logger.info("[VIEW] Same view, skipping")
            return
        if value == "Sistemler":
            if self._tool_tabview:
                self._tool_tabview.pack_forget()
            self._sys_tabview.pack(after=self._view_switch,
                                   fill="both", expand=True, padx=5, pady=(0, 5))
        else:
            self._sys_tabview.pack_forget()
            if not self._tools_built:
                logger.info("[VIEW] Building tool tabs for first time...")
                try:
                    self._build_tool_tabs()
                    logger.info("[VIEW] Tool tabs built OK")
                except Exception as e:
                    logger.error(f"[VIEW] Tool tabs build FAILED: {e}")
                    import traceback
                    logger.error(traceback.format_exc())
            self._tool_tabview.pack(after=self._view_switch,
                                    fill="both", expand=True, padx=5, pady=(0, 5))
        self._active_view = value
        # Buton renklerini güncelle
        if value == "Sistemler":
            self._btn_sistemler.configure(fg_color="#3d5afe")
            self._btn_araclar.configure(fg_color="#455A64")
        else:
            self._btn_sistemler.configure(fg_color="#455A64")
            self._btn_araclar.configure(fg_color="#3d5afe")
        logger.info(f"[VIEW] Switched to '{value}'")

    def _build_tool_tabs(self) -> None:
        """Build tool tabs on first Araclar click (lightweight panels only)."""
        self._tool_tabview = ctk.CTkTabview(self)
        ctrl = self.controller
        tv = self._tool_tabview

        tab_report = tv.add("Rapor")
        tab_strat_s = tv.add("Str.Ayar")
        tab_act = tv.add("Aktivite")
        tab_set = tv.add("Ayarlar")
        tab_quick = tv.add("Emir")
        tab_market = tv.add("Piyasa")
        tab_strat = tv.add("Strateji")

        self._report_panel = TradeReportPanel(tab_report, ctrl)
        self._strategy_settings_panel = StrategySettingsPanel(tab_strat_s, ctrl)
        self._activity_panel = ActivityPanel(tab_act, ctrl)
        self._settings_panel = SettingsPanel(tab_set, ctrl)
        self._quick_panel = QuickOrderPanel(tab_quick, ctrl)
        self._market_panel = MarketPanel(tab_market, ctrl)
        self._strategy_panel = StrategyPanel(tab_strat, ctrl)

        self._tools_built = True
        logger.info("Tool tabs built (7 panels)")

    def _ensure_visible(self):
        """Force window visible and maximized, then refresh tabview."""
        try:
            self.state("zoomed")
            self.after(500, self._refresh_tabview)
            self.after(1500, self._refresh_tabview)
            self.after(3000, self._refresh_tabview)
        except Exception:
            pass

    def _refresh_tabview(self):
        """Force CTkTabview to re-render tab bar after window resize."""
        try:
            # Re-pack sys tabview to force geometry recalculation
            if self._active_view == "Sistemler":
                self._sys_tabview.pack_forget()
                self._sys_tabview.pack(after=self._view_switch,
                                       fill="both", expand=True, padx=5, pady=(0, 5))
            current = self._sys_tabview.get()
            self._sys_tabview.set(current)
            self.update_idletasks()
        except Exception:
            pass

    def _register_hotkeys(self) -> None:
        hotkeys = self.controller.config.get("hotkeys", {})
        try:
            keyboard.add_hotkey(
                hotkeys.get("buy_long", "ctrl+shift+b"),
                self._hotkey_buy,
            )
            keyboard.add_hotkey(
                hotkeys.get("sell_short", "ctrl+shift+s"),
                self._hotkey_sell,
            )
            keyboard.add_hotkey(
                hotkeys.get("kill_switch", "ctrl+shift+k"),
                self._hotkey_kill,
            )
            logger.info("Global hotkeys registered")
        except Exception as e:
            logger.warning(f"Hotkey registration failed: {e}")

    def _hotkey_buy(self) -> None:
        if self._quick_panel:
            from core.constants import OrderSide
            self.after(0, lambda: self._quick_panel._on_order(OrderSide.BUY_LONG))

    def _hotkey_sell(self) -> None:
        if self._quick_panel:
            from core.constants import OrderSide
            self.after(0, lambda: self._quick_panel._on_order(OrderSide.SELL_SHORT))

    def _hotkey_kill(self) -> None:
        self.after(0, self.controller.activate_kill_switch)

    def _refresh_ui(self) -> None:
        try:
            symbol = self.controller.get_current_symbol()
            price = self.controller.get_current_price()

            # Get market data
            market_data = {"price": price, "mark_price": 0, "funding_rate": 0,
                           "high_24h": 0, "low_24h": 0, "volume_24h": 0,
                           "price_change_pct": 0}

            if self.controller.market_service:
                funding = self.controller.market_service.get_funding_rate(symbol)
                ticker = self.controller.market_service.get_ticker(symbol)
                market_data.update({
                    "mark_price": funding.get("mark_price", 0),
                    "funding_rate": funding.get("funding_rate", 0),
                    "high_24h": ticker.get("high_24h", 0),
                    "low_24h": ticker.get("low_24h", 0),
                    "volume_24h": ticker.get("volume_24h", 0),
                    "price_change_pct": ticker.get("price_change_pct", 0),
                })

            # Get indicator values
            indicator_values = self.controller.get_indicator_values()

            # Not: compute_all scanner thread'inde çalışır, GUI thread'inde tekrar çalıştırmıyoruz
            # (GUI thread'i bloklanmasın diye)

            # Update status bar
            use_api = self.controller.config.get("trading.use_api", False)
            if use_api:
                binance_connected = True  # API mode — always connected
            else:
                binance_connected = (self.controller.binance_app and
                                     self.controller.binance_app.is_connected)
            ws_connected = (self.controller.market_service and
                            self.controller.market_service._ws.is_connected)
            strategy_running = (self.controller.strategy_engine and
                                self.controller.strategy_engine.is_running)
            killed = (self.controller.risk_manager and
                      self.controller.risk_manager.is_killed)

            self._status_bar.update_data(
                price=price,
                change_pct=market_data.get("price_change_pct", 0),
                binance_connected=binance_connected,
                ws_connected=ws_connected,
                symbol=symbol,
                strategy_running=strategy_running,
                killed=killed,
            )

            # Update panels based on active tab
            if self._active_view == "Araclar" and self._tool_tabview:
                active_tab = self._tool_tabview.get()
                if active_tab == "Emir" and self._quick_panel:
                    self._quick_panel.update_display(
                        price=price,
                        mark_price=market_data.get("mark_price", 0),
                        funding_rate=market_data.get("funding_rate", 0),
                    )
                elif active_tab == "Piyasa" and self._market_panel:
                    klines = None
                    if self.controller.market_service:
                        klines = self.controller.market_service.get_klines(symbol)
                    self._market_panel.update_data(market_data, indicator_values, klines)

        except Exception as e:
            logger.debug(f"UI refresh error: {e}")

        refresh_ms = self.controller.config.get("ui_refresh_ms", 1000)
        self.after(refresh_ms, self._refresh_ui)

    def _on_order_event(self, data: dict) -> None:
        msg = (f"Emir: {data.get('side', '?')} {data.get('size', '?')} "
               f"{data.get('symbol', '?')} @ {data.get('price', 'market')}")
        if "error" in data:
            msg += f" HATA: {data['error']}"
        self.after(0, lambda: self._activity_panel.add_log_entry("INFO", msg))

    def _on_strategy_event(self, data: dict) -> None:
        msg = f"Strateji sinyal: {data.get('strategy_name', '?')}"
        self.after(0, lambda: self._activity_panel.add_log_entry("INFO", msg))

    def _on_log_event(self, data: dict) -> None:
        level = data.get("level", "INFO")
        message = data.get("message", "")
        self.after(0, lambda: self._activity_panel.add_log_entry(level, message))

    def _on_kill_event(self, data: dict) -> None:
        self.after(0, lambda: self._activity_panel.add_log_entry(
            "CRITICAL", "KILL SWITCH AKTIF!"))

    def _on_position_opened(self, data: dict) -> None:
        d = data.copy()
        msg = (f"POZISYON ACILDI: {d.get('symbol', '?')} "
               f"{d.get('side', '?')} {d.get('leverage', '?')}x "
               f"@ {d.get('entry_price', 0):.6g} "
               f"margin=${d.get('margin_usdt', 0):.2f} "
               f"SL={d.get('sl_price', 0):.6g}")
        self.after(0, lambda: self._activity_panel.add_log_entry("BUY", msg))
        self.after(0, self._activity_panel.refresh_orders)

    def _on_position_closed(self, data: dict) -> None:
        d = data.copy()
        pnl = d.get("pnl_usdt", 0)
        level = "SELL+" if pnl >= 0 else "SELL-"
        msg = (f"POZISYON KAPANDI: {d.get('symbol', '?')} "
               f"cikis={d.get('exit_reason', '?')} "
               f"PnL={pnl:+.4f}$")
        self.after(0, lambda: self._activity_panel.add_log_entry(level, msg))
        self.after(0, self._activity_panel.refresh_orders)

    def _on_trade_result(self, data: dict) -> None:
        d = data.copy()
        pnl = d.get("pnl_usdt", 0)
        level = "TRADE+" if pnl >= 0 else "TRADE-"
        dur = d.get("hold_seconds", 0)
        dur_m = int(dur // 60)
        dur_s = int(dur % 60)
        msg = (f"ISLEM SONUCU: {d.get('symbol', '?')} "
               f"{d.get('side', '?')} "
               f"PnL={pnl:+.4f}$ "
               f"ROI={d.get('roi_percent', 0):+.1f}% "
               f"sure={dur_m}dk{dur_s:02d}sn "
               f"({d.get('exit_reason', '?')})")
        self.after(0, lambda: self._activity_panel.add_log_entry(level, msg))

    def _on_scanner_state(self, data: dict) -> None:
        d = data.copy()
        old_s = d.get("old_state", "?")
        new_s = d.get("new_state", "?")
        msg = f"Tarayici: {old_s} -> {new_s}"
        self.after(0, lambda: self._activity_panel.add_log_entry("SCAN", msg))

    def _on_connection_status(self, data: dict) -> None:
        d = data.copy()
        source = d.get("source", "?")
        connected = d.get("connected", False)
        status = "BAGLI" if connected else "KOPUK"
        level = "INFO" if connected else "WARNING"
        msg = f"Baglanti: {source} {status}"
        self.after(0, lambda: self._activity_panel.add_log_entry(level, msg))

    def _on_regime_change(self, data: dict) -> None:
        d = data.copy()
        msg = (f"Rejim degisti: {d.get('old_regime', '?')} -> {d.get('new_regime', '?')} "
               f"({d.get('symbol', '?')})")
        self.after(0, lambda: self._activity_panel.add_log_entry("REGIME", msg))

    def _on_close(self) -> None:
        try:
            keyboard.unhook_all()
        except Exception:
            pass
        self.controller.shutdown()
        self.destroy()
