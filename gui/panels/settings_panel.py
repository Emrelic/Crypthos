import customtkinter as ctk


class SettingsPanel(ctk.CTkFrame):
    """Configuration panel for all settings."""

    def __init__(self, parent, controller):
        super().__init__(parent)
        self.controller = controller
        self.pack(fill="both", expand=True)
        self._entries = {}
        self._build_ui()

    def _build_ui(self) -> None:
        scroll = ctk.CTkScrollableFrame(self)
        scroll.pack(fill="both", expand=True, padx=10, pady=10)

        # General
        self._section(scroll, "Genel Ayarlar")
        self._field(scroll, "active_symbol", "Aktif Sembol",
                    self.controller.config.get("active_symbol", "DOGEUSDT"))
        self._field(scroll, "kline_interval", "Kline Araliği",
                    self.controller.config.get("indicators.kline_interval", "15m"))
        self._field(scroll, "strategy_eval", "Strateji Eval (sn)",
                    str(self.controller.config.get("strategy_eval_interval_seconds", 5)))
        self._field(scroll, "ui_refresh", "UI Yenileme (ms)",
                    str(self.controller.config.get("ui_refresh_ms", 1000)))

        # Hotkeys
        self._section(scroll, "Kısayol Tuslari")
        hotkeys = self.controller.config.get("hotkeys", {})
        self._field(scroll, "hk_buy", "Buy/Long", hotkeys.get("buy_long", "ctrl+shift+b"))
        self._field(scroll, "hk_sell", "Sell/Short", hotkeys.get("sell_short", "ctrl+shift+s"))
        self._field(scroll, "hk_kill", "Kill Switch", hotkeys.get("kill_switch", "ctrl+shift+k"))

        # Risk
        self._section(scroll, "Risk Yönetimi")
        risk = self.controller.config.get("risk", {})
        self._field(scroll, "max_pos", "Max Pozisyon (USDT)",
                    str(risk.get("max_position_usdt", 100)))
        self._field(scroll, "max_single", "Max Tek Emir (USDT)",
                    str(risk.get("max_single_order_usdt", 50)))
        self._field(scroll, "confirm_above", "Onay Esigi (USDT)",
                    str(risk.get("confirm_above_usdt", 20)))
        self._field(scroll, "default_tp", "Varsayilan TP%",
                    str(risk.get("default_tp_percent", 5.0)))
        self._field(scroll, "default_sl", "Varsayilan SL%",
                    str(risk.get("default_sl_percent", 2.0)))

        # Indicators
        self._section(scroll, "Indikatör Ayarları")
        ind = self.controller.config.get("indicators", {})
        self._field(scroll, "rsi_period", "RSI Periyodu", str(ind.get("rsi_period", 14)))
        self._field(scroll, "ma_fast", "MA Hizli", str(ind.get("ma_fast", 20)))
        self._field(scroll, "ma_slow", "MA Yavas", str(ind.get("ma_slow", 200)))
        self._field(scroll, "macd_fast", "MACD Hizli", str(ind.get("macd_fast", 12)))
        self._field(scroll, "macd_slow", "MACD Yavas", str(ind.get("macd_slow", 26)))
        self._field(scroll, "macd_signal", "MACD Sinyal", str(ind.get("macd_signal", 9)))

        # Connection
        self._section(scroll, "Binance Baglantısı")
        self._conn_status = ctk.CTkLabel(scroll, text="Durum: Kontrol ediliyor...",
                                         text_color="gray")
        self._conn_status.pack(anchor="w", padx=20, pady=2)

        conn_btns = ctk.CTkFrame(scroll, fg_color="transparent")
        conn_btns.pack(fill="x", padx=20, pady=5)
        ctk.CTkButton(conn_btns, text="Yeniden Baglan", width=130,
                      command=self._reconnect).pack(side="left", padx=5)
        ctk.CTkButton(conn_btns, text="Test", width=80,
                      command=self._test_connection).pack(side="left", padx=5)

        # Save / Reset
        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.pack(fill="x", padx=10, pady=10)
        ctk.CTkButton(btn_frame, text="Kaydet", width=120,
                      command=self._save).pack(side="left", padx=5)
        ctk.CTkButton(btn_frame, text="Sifirla", width=100,
                      fg_color="gray30", command=self._reset).pack(side="left", padx=5)

    def _section(self, parent, title: str) -> None:
        ctk.CTkLabel(parent, text=title,
                     font=ctk.CTkFont(size=14, weight="bold")).pack(
            anchor="w", padx=10, pady=(15, 5))

    def _field(self, parent, key: str, label: str, value: str) -> None:
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", padx=20, pady=2)
        ctk.CTkLabel(row, text=f"{label}:", width=160, anchor="w").pack(side="left")
        entry = ctk.CTkEntry(row, width=200)
        entry.pack(side="left", padx=5)
        entry.insert(0, value)
        self._entries[key] = entry

    def _save(self) -> None:
        c = self.controller.config
        c.set("active_symbol", self._entries["active_symbol"].get().strip().upper())
        c.set("indicators.kline_interval", self._entries["kline_interval"].get().strip())
        c.set("strategy_eval_interval_seconds", int(self._entries["strategy_eval"].get() or 5))
        c.set("ui_refresh_ms", int(self._entries["ui_refresh"].get() or 1000))

        c.set("hotkeys.buy_long", self._entries["hk_buy"].get().strip())
        c.set("hotkeys.sell_short", self._entries["hk_sell"].get().strip())
        c.set("hotkeys.kill_switch", self._entries["hk_kill"].get().strip())

        c.set("risk.max_position_usdt", float(self._entries["max_pos"].get() or 100))
        c.set("risk.max_single_order_usdt", float(self._entries["max_single"].get() or 50))
        c.set("risk.confirm_above_usdt", float(self._entries["confirm_above"].get() or 20))
        c.set("risk.default_tp_percent", float(self._entries["default_tp"].get() or 5))
        c.set("risk.default_sl_percent", float(self._entries["default_sl"].get() or 2))

        c.set("indicators.rsi_period", int(self._entries["rsi_period"].get() or 14))
        c.set("indicators.ma_fast", int(self._entries["ma_fast"].get() or 20))
        c.set("indicators.ma_slow", int(self._entries["ma_slow"].get() or 200))
        c.set("indicators.macd_fast", int(self._entries["macd_fast"].get() or 12))
        c.set("indicators.macd_slow", int(self._entries["macd_slow"].get() or 26))
        c.set("indicators.macd_signal", int(self._entries["macd_signal"].get() or 9))

        c.save()

    def _reset(self) -> None:
        from core.config_manager import DEFAULT_CONFIG
        for key, entry in self._entries.items():
            entry.delete(0, "end")

    def _reconnect(self) -> None:
        if self.controller.binance_app:
            success = self.controller.binance_app.refresh_connection()
            if success:
                self._conn_status.configure(text="Durum: Bagli", text_color="green")
            else:
                self._conn_status.configure(text="Durum: Baglanti basarisiz", text_color="red")

    def _test_connection(self) -> None:
        try:
            from market.binance_rest import BinanceRestClient
            client = BinanceRestClient()
            result = client.get_ticker_price("BTCUSDT")
            price = result.get("price", "?")
            self._conn_status.configure(
                text=f"Durum: API OK (BTC: ${price})", text_color="green",
            )
        except Exception as e:
            self._conn_status.configure(
                text=f"Durum: API Hatasi - {e}", text_color="red",
            )

    def update_connection_status(self, connected: bool) -> None:
        if connected:
            self._conn_status.configure(text="Durum: Bagli", text_color="green")
        else:
            self._conn_status.configure(text="Durum: Bagli Degil", text_color="red")
