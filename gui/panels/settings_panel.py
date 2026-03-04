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
        self._field(scroll, "kline_interval", "Kline Araligi",
                    self.controller.config.get("indicators.kline_interval", "15m"))
        self._field(scroll, "strategy_eval", "Strateji Eval (sn)",
                    str(self.controller.config.get("strategy_eval_interval_seconds", 5)))
        self._field(scroll, "ui_refresh", "UI Yenileme (ms)",
                    str(self.controller.config.get("ui_refresh_ms", 1000)))

        # Hotkeys
        self._section(scroll, "Kisayol Tuslari")
        hotkeys = self.controller.config.get("hotkeys", {})
        self._field(scroll, "hk_buy", "Buy/Long", hotkeys.get("buy_long", "ctrl+shift+b"))
        self._field(scroll, "hk_sell", "Sell/Short", hotkeys.get("sell_short", "ctrl+shift+s"))
        self._field(scroll, "hk_kill", "Kill Switch", hotkeys.get("kill_switch", "ctrl+shift+k"))

        # ═══════════════════════════════════════════════════════
        # LEVERAGE & POSITION SIZING (main trading section)
        # ═══════════════════════════════════════════════════════
        self._section(scroll, "Kaldirac & Pozisyon Boyutu")
        self._field(scroll, "max_positions", "Max Esanli Pozisyon",
                    str(self.controller.config.get("scanner.max_positions", 1)))
        self._checkbox(scroll, "focus_mode", "Odak Modu (pozisyon acilinca tarama durur)",
                       self.controller.config.get("scanner.focus_mode", True))
        lev = self.controller.config.get("leverage", {})

        self._checkbox(scroll, "lev_enabled", "Kaldirac Etkin",
                       lev.get("enabled", False))

        # Leverage range
        lev_range_frame = ctk.CTkFrame(scroll, fg_color="transparent")
        lev_range_frame.pack(fill="x", padx=20, pady=2)
        ctk.CTkLabel(lev_range_frame, text="Kaldirac Araligi:",
                     width=160, anchor="w").pack(side="left")
        min_lev_entry = ctk.CTkEntry(lev_range_frame, width=70,
                                     placeholder_text="Min")
        min_lev_entry.pack(side="left", padx=2)
        min_lev_entry.insert(0, str(lev.get("min_leverage", 10)))
        self._entries["lev_min"] = min_lev_entry
        ctk.CTkLabel(lev_range_frame, text=" - ").pack(side="left")
        max_lev_entry = ctk.CTkEntry(lev_range_frame, width=70,
                                     placeholder_text="Max")
        max_lev_entry.pack(side="left", padx=2)
        max_lev_entry.insert(0, str(lev.get("max_leverage", 125)))
        self._entries["lev_max"] = max_lev_entry
        ctk.CTkLabel(lev_range_frame, text="x").pack(side="left")

        # Position sizing mode (radio-like)
        self._section_sub(scroll, "Pozisyon Boyutlandirma Modu")

        sizing_mode = lev.get("position_sizing", "percentage")

        # Mode selector
        self._sizing_mode_var = ctk.StringVar(value=sizing_mode)

        # --- Percentage mode ---
        pct_frame = ctk.CTkFrame(scroll, fg_color="transparent",
                                 border_width=1, border_color="gray40",
                                 corner_radius=8)
        pct_frame.pack(fill="x", padx=20, pady=4)

        pct_header = ctk.CTkFrame(pct_frame, fg_color="transparent")
        pct_header.pack(fill="x", padx=10, pady=(8, 2))
        pct_radio = ctk.CTkRadioButton(
            pct_header, text="Portfoy Yuzde Modu",
            variable=self._sizing_mode_var, value="percentage",
            command=self._on_sizing_mode_change,
            font=ctk.CTkFont(weight="bold"))
        pct_radio.pack(side="left")

        self._pct_content = ctk.CTkFrame(pct_frame, fg_color="transparent")
        self._pct_content.pack(fill="x", padx=30, pady=(0, 8))
        ctk.CTkLabel(self._pct_content, text="Portfoyun ne kadari:",
                     anchor="w").pack(side="left")
        pct_entry = ctk.CTkEntry(self._pct_content, width=70)
        pct_entry.pack(side="left", padx=5)
        pct_entry.insert(0, str(lev.get("portfolio_percent", 100)))
        self._entries["portfolio_pct"] = pct_entry
        ctk.CTkLabel(self._pct_content, text="%").pack(side="left")

        pct_desc = ctk.CTkLabel(pct_frame, text="Ornek: %100 = tum bakiye, %50 = yarisi",
                                text_color="gray60", font=ctk.CTkFont(size=11))
        pct_desc.pack(anchor="w", padx=30, pady=(0, 8))

        # --- Fixed mode ---
        fix_frame = ctk.CTkFrame(scroll, fg_color="transparent",
                                 border_width=1, border_color="gray40",
                                 corner_radius=8)
        fix_frame.pack(fill="x", padx=20, pady=4)

        fix_header = ctk.CTkFrame(fix_frame, fg_color="transparent")
        fix_header.pack(fill="x", padx=10, pady=(8, 2))
        fix_radio = ctk.CTkRadioButton(
            fix_header, text="Sabit Marjin Modu",
            variable=self._sizing_mode_var, value="fixed",
            command=self._on_sizing_mode_change,
            font=ctk.CTkFont(weight="bold"))
        fix_radio.pack(side="left")

        self._fix_content = ctk.CTkFrame(fix_frame, fg_color="transparent")
        self._fix_content.pack(fill="x", padx=30, pady=(0, 4))

        fix_row1 = ctk.CTkFrame(self._fix_content, fg_color="transparent")
        fix_row1.pack(fill="x", pady=2)
        ctk.CTkLabel(fix_row1, text="Sabit marjin:",
                     width=120, anchor="w").pack(side="left")
        margin_entry = ctk.CTkEntry(fix_row1, width=80)
        margin_entry.pack(side="left", padx=5)
        margin_entry.insert(0, str(lev.get("margin_usdt", 1.0)))
        self._entries["lev_margin"] = margin_entry
        ctk.CTkLabel(fix_row1, text="USDT").pack(side="left")

        fix_row2 = ctk.CTkFrame(self._fix_content, fg_color="transparent")
        fix_row2.pack(fill="x", pady=2)
        ctk.CTkLabel(fix_row2, text="Max pozisyon:",
                     width=120, anchor="w").pack(side="left")
        max_pos_entry = ctk.CTkEntry(fix_row2, width=80)
        max_pos_entry.pack(side="left", padx=5)
        max_pos_entry.insert(0, str(lev.get("max_position_usdt", 50.0)))
        self._entries["max_pos_lev"] = max_pos_entry
        ctk.CTkLabel(fix_row2, text="USDT").pack(side="left")

        fix_desc = ctk.CTkLabel(fix_frame, text="Ornek: 1 USDT marjin x 75x = 75 USDT pozisyon",
                                text_color="gray60", font=ctk.CTkFont(size=11))
        fix_desc.pack(anchor="w", padx=30, pady=(0, 8))

        # Initial state
        self._on_sizing_mode_change()

        # TP/SL settings
        self._section_sub(scroll, "TP / SL Ayarlari")
        self._field(scroll, "lev_sl", "SL Fiyat Hareketi %",
                    str(lev.get("sl_percent", 0.7)))
        self._field(scroll, "lev_tp", "TP Fiyat Hareketi %",
                    str(lev.get("tp_percent", 1.5)))

        # Trailing stop
        self._section_sub(scroll, "Trailing Stop")
        self._field(scroll, "lev_trail_act", "Aktivasyon %",
                    str(lev.get("trailing_activation_pct", 0.5)))
        self._field(scroll, "lev_trail_dist", "Mesafe %",
                    str(lev.get("trailing_distance_pct", 0.3)))
        self._field(scroll, "lev_max_hold", "Max Tutma (dk)",
                    str(lev.get("max_hold_minutes", 60)))

        # ═══════════════════════════════════════════════════════
        # RISK
        # ═══════════════════════════════════════════════════════
        self._section(scroll, "Risk Yonetimi")
        risk = self.controller.config.get("risk", {})
        self._field(scroll, "initial_balance", "Baslangic Bakiye (USDT)",
                    str(risk.get("initial_balance", 15.0)))
        self._field(scroll, "daily_loss_limit", "Gunluk Kayip Limiti (USDT)",
                    str(risk.get("daily_loss_limit_usdt", 5.0)))
        self._field(scroll, "max_drawdown", "Max Drawdown %",
                    str(risk.get("max_drawdown_percent", 20.0)))
        self._field(scroll, "max_consecutive", "Max Ardisik Kayip",
                    str(risk.get("max_consecutive_losses", 5)))

        # Indicators
        self._section(scroll, "Indikator Ayarlari")
        ind = self.controller.config.get("indicators", {})
        self._field(scroll, "rsi_period", "RSI Periyodu", str(ind.get("rsi_period", 14)))
        self._field(scroll, "ma_fast", "MA Hizli", str(ind.get("ma_fast", 20)))
        self._field(scroll, "ma_slow", "MA Yavas", str(ind.get("ma_slow", 200)))
        self._field(scroll, "macd_fast", "MACD Hizli", str(ind.get("macd_fast", 12)))
        self._field(scroll, "macd_slow", "MACD Yavas", str(ind.get("macd_slow", 26)))
        self._field(scroll, "macd_signal", "MACD Sinyal", str(ind.get("macd_signal", 9)))

        # Connection
        self._section(scroll, "Binance Baglantisi")
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

    def _on_sizing_mode_change(self) -> None:
        """Toggle between percentage and fixed mode UI elements."""
        mode = self._sizing_mode_var.get()
        if mode == "percentage":
            # Enable percentage widgets, disable fixed widgets
            for child in self._pct_content.winfo_children():
                if isinstance(child, ctk.CTkEntry):
                    child.configure(state="normal")
            for child in self._fix_content.winfo_children():
                for c in child.winfo_children():
                    if isinstance(c, ctk.CTkEntry):
                        c.configure(state="disabled", text_color="gray50")
        else:
            # Enable fixed widgets, disable percentage widgets
            for child in self._pct_content.winfo_children():
                if isinstance(child, ctk.CTkEntry):
                    child.configure(state="disabled", text_color="gray50")
            for child in self._fix_content.winfo_children():
                for c in child.winfo_children():
                    if isinstance(c, ctk.CTkEntry):
                        c.configure(state="normal", text_color="white")

    def _section(self, parent, title: str) -> None:
        ctk.CTkLabel(parent, text=title,
                     font=ctk.CTkFont(size=14, weight="bold")).pack(
            anchor="w", padx=10, pady=(15, 5))

    def _section_sub(self, parent, title: str) -> None:
        ctk.CTkLabel(parent, text=title,
                     font=ctk.CTkFont(size=12, weight="bold"),
                     text_color="gray70").pack(
            anchor="w", padx=20, pady=(10, 3))

    def _field(self, parent, key: str, label: str, value: str) -> None:
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", padx=20, pady=2)
        ctk.CTkLabel(row, text=f"{label}:", width=180, anchor="w").pack(side="left")
        entry = ctk.CTkEntry(row, width=200)
        entry.pack(side="left", padx=5)
        entry.insert(0, value)
        self._entries[key] = entry

    def _checkbox(self, parent, key: str, label: str, value: bool) -> None:
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", padx=20, pady=2)
        var = ctk.BooleanVar(value=value)
        cb = ctk.CTkCheckBox(row, text=label, variable=var)
        cb.pack(side="left")
        self._entries[key] = var

    def _save(self) -> None:
        c = self.controller.config
        c.set("active_symbol", self._entries["active_symbol"].get().strip().upper())
        c.set("indicators.kline_interval", self._entries["kline_interval"].get().strip())
        c.set("strategy_eval_interval_seconds", int(self._entries["strategy_eval"].get() or 5))
        c.set("ui_refresh_ms", int(self._entries["ui_refresh"].get() or 1000))

        c.set("hotkeys.buy_long", self._entries["hk_buy"].get().strip())
        c.set("hotkeys.sell_short", self._entries["hk_sell"].get().strip())
        c.set("hotkeys.kill_switch", self._entries["hk_kill"].get().strip())

        # Scanner
        c.set("scanner.max_positions",
              int(self._entries["max_positions"].get() or 1))
        c.set("scanner.focus_mode", self._entries["focus_mode"].get())

        # Leverage settings
        c.set("leverage.enabled", self._entries["lev_enabled"].get())
        c.set("leverage.min_leverage",
              int(self._entries["lev_min"].get() or 10))
        c.set("leverage.max_leverage",
              int(self._entries["lev_max"].get() or 125))

        # Position sizing mode
        c.set("leverage.position_sizing", self._sizing_mode_var.get())
        c.set("leverage.portfolio_percent",
              int(self._entries["portfolio_pct"].get() or 100))
        c.set("leverage.margin_usdt",
              float(self._entries["lev_margin"].get() or 1.0))
        c.set("leverage.max_position_usdt",
              float(self._entries["max_pos_lev"].get() or 50.0))

        # TP/SL
        c.set("leverage.sl_percent",
              float(self._entries["lev_sl"].get() or 0.7))
        c.set("leverage.tp_percent",
              float(self._entries["lev_tp"].get() or 1.5))

        # Trailing
        c.set("leverage.trailing_activation_pct",
              float(self._entries["lev_trail_act"].get() or 0.5))
        c.set("leverage.trailing_distance_pct",
              float(self._entries["lev_trail_dist"].get() or 0.3))
        c.set("leverage.max_hold_minutes",
              int(self._entries["lev_max_hold"].get() or 60))

        # Risk
        c.set("risk.initial_balance",
              float(self._entries["initial_balance"].get() or 15.0))
        c.set("risk.daily_loss_limit_usdt",
              float(self._entries["daily_loss_limit"].get() or 5.0))
        c.set("risk.max_drawdown_percent",
              float(self._entries["max_drawdown"].get() or 20.0))
        c.set("risk.max_consecutive_losses",
              int(self._entries["max_consecutive"].get() or 5))

        # Indicators
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
            if isinstance(entry, ctk.CTkEntry):
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
