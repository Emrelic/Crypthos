import keyboard
import customtkinter as ctk
from loguru import logger
from core.constants import EventType, OrderSide, OrderType
from gui.widgets.status_bar import StatusBar
from gui.panels.quick_order_panel import QuickOrderPanel
from gui.panels.strategy_panel import StrategyPanel
from gui.panels.market_panel import MarketPanel
from gui.panels.activity_panel import ActivityPanel
from gui.panels.settings_panel import SettingsPanel
from gui.panels.scanner_panel import ScannerPanel
from gui.panels.strategy_settings_panel import StrategySettingsPanel
from gui.panels.indicator_analysis_panel import IndicatorAnalysisPanel
from gui.panels.indicator_detail_panel import IndicatorDetailPanel
from gui.panels.trade_report_panel import TradeReportPanel


class MainWindow(ctk.CTk):
    """Root CustomTkinter window with tabbed interface."""

    def __init__(self, controller):
        super().__init__()
        self.controller = controller

        self.title("Crypthos Trading Bot")
        self.geometry("1200x800")
        self.minsize(900, 600)
        self.after(50, lambda: self.state("zoomed"))  # maximize on startup

        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")

        # Status bar
        self._status_bar = StatusBar(self, controller)
        self._status_bar.pack(fill="x", padx=5, pady=(5, 0))

        # Tab view
        self._tabview = ctk.CTkTabview(self)
        self._tabview.pack(fill="both", expand=True, padx=10, pady=(5, 10))

        tab_scanner = self._tabview.add("Tarayici")
        tab_quick = self._tabview.add("Hizli Emir")
        tab_market = self._tabview.add("Piyasa")
        tab_strategy = self._tabview.add("Strateji")
        tab_strat_settings = self._tabview.add("Strateji Ayarlari")
        tab_analysis = self._tabview.add("Grafik Analiz")
        tab_ind_detail = self._tabview.add("Indikator Detay")
        tab_report = self._tabview.add("Islem Raporu")
        tab_activity = self._tabview.add("Aktivite")
        tab_settings = self._tabview.add("Ayarlar")

        self._scanner_panel = ScannerPanel(tab_scanner, controller)
        self._quick_panel = QuickOrderPanel(tab_quick, controller)
        self._market_panel = MarketPanel(tab_market, controller)
        self._strategy_panel = StrategyPanel(tab_strategy, controller)
        self._strategy_settings_panel = StrategySettingsPanel(tab_strat_settings, controller)
        self._analysis_panel = IndicatorAnalysisPanel(tab_analysis, controller)
        self._analysis_panel.pack(fill="both", expand=True)
        self._ind_detail_panel = IndicatorDetailPanel(tab_ind_detail, controller)
        self._ind_detail_panel.pack(fill="both", expand=True)
        self._report_panel = TradeReportPanel(tab_report, controller)
        self._activity_panel = ActivityPanel(tab_activity, controller)
        self._settings_panel = SettingsPanel(tab_settings, controller)

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

        # On close
        self.protocol("WM_DELETE_WINDOW", self._on_close)

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
        self.after(0, lambda: self._quick_panel._on_order(OrderSide.BUY_LONG))

    def _hotkey_sell(self) -> None:
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

            # Compute indicators if we have klines
            if self.controller.market_service and self.controller.indicator_engine:
                klines = self.controller.market_service.get_klines(symbol)
                if klines is not None and not klines.empty:
                    indicator_values = self.controller.indicator_engine.compute_all(klines)

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
            active_tab = self._tabview.get()
            if active_tab == "Hizli Emir":
                self._quick_panel.update_display(
                    price=price,
                    mark_price=market_data.get("mark_price", 0),
                    funding_rate=market_data.get("funding_rate", 0),
                )
            elif active_tab == "Piyasa":
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
