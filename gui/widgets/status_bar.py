import customtkinter as ctk


class StatusBar(ctk.CTkFrame):
    """Top status bar: connection + scanner controls + kill switch.
    All scanner controls (start/stop, scan count, candidate) live here
    to maximize table space in the scanner panel."""

    def __init__(self, parent, controller):
        super().__init__(parent, height=36)
        self.controller = controller
        self.pack_propagate(False)

        # ── LEFT: Connection ──
        self._conn_label = ctk.CTkLabel(self, text="  Disconnected", width=110,
                                        text_color="red", anchor="w",
                                        font=ctk.CTkFont(size=12))
        self._conn_label.pack(side="left", padx=3)

        self._reconnect_btn = ctk.CTkButton(
            self, text="Baglan", width=55, height=22,
            fg_color="gray30", hover_color="gray40",
            font=ctk.CTkFont(size=11),
            command=self._on_reconnect)
        self._reconnect_btn.pack(side="left", padx=2)

        # WS indicator
        self._ws_label = ctk.CTkLabel(self, text="WS:--", width=50,
                                      text_color="gray", anchor="w",
                                      font=ctk.CTkFont(size=11))
        self._ws_label.pack(side="left", padx=3)

        # Separator
        ctk.CTkLabel(self, text="|", text_color="#555555", width=8).pack(side="left")

        # ── CENTER: Scanner controls ──
        self._state_lbl = ctk.CTkLabel(
            self, text="IDLE", width=70,
            font=ctk.CTkFont(size=13, weight="bold"), text_color="gray")
        self._state_lbl.pack(side="left", padx=(5, 3))

        self._start_btn = ctk.CTkButton(
            self, text="BASLAT", fg_color="#00C853", hover_color="#00A846",
            width=70, height=24, font=ctk.CTkFont(size=11, weight="bold"),
            command=self._on_start)
        self._start_btn.pack(side="left", padx=2)

        self._stop_btn = ctk.CTkButton(
            self, text="DURDUR", fg_color="#FF1744", hover_color="#D50000",
            width=70, height=24, font=ctk.CTkFont(size=11, weight="bold"),
            command=self._on_stop)
        self._stop_btn.pack(side="left", padx=2)

        ctk.CTkLabel(self, text="|", text_color="#555555", width=8).pack(side="left")

        self._scan_count_lbl = ctk.CTkLabel(self, text="Tarama: 0", width=75,
                                             font=ctk.CTkFont(size=12))
        self._scan_count_lbl.pack(side="left", padx=4)

        self._candidate_lbl = ctk.CTkLabel(self, text="Aday: --", width=140,
                                            font=ctk.CTkFont(size=12, weight="bold"))
        self._candidate_lbl.pack(side="left", padx=4)

        self._trade_lbl = ctk.CTkLabel(self, text="Son: --", width=200,
                                        font=ctk.CTkFont(size=11))
        self._trade_lbl.pack(side="left", padx=4)

        # Strategy status (compact)
        self._strategy_label = ctk.CTkLabel(self, text="Str:OFF", width=55,
                                            text_color="gray",
                                            font=ctk.CTkFont(size=11))
        self._strategy_label.pack(side="left", padx=3)

        # ── RIGHT: Kill switch ──
        self._kill_btn = ctk.CTkButton(
            self, text="KILL", width=60, height=24,
            fg_color="darkred", hover_color="red",
            font=ctk.CTkFont(size=11, weight="bold"),
            command=self._on_kill)
        self._kill_btn.pack(side="right", padx=5)

    # ── Scanner state colors ──
    STATE_COLORS = {
        "IDLE": "gray", "SCANNING": "#2196F3", "BUYING": "#FF9800",
        "HOLDING": "#00C853", "SELLING": "#FF1744", "COOLDOWN": "#9E9E9E",
    }

    def _on_reconnect(self) -> None:
        self._conn_label.configure(text="  Baglaniyor...", text_color="yellow")
        self.update()
        use_api = getattr(self.controller, 'config', None)
        api_mode = use_api.get("trading.use_api", False) if use_api else False
        if api_mode:
            try:
                scanner = self.controller.scanner
                if scanner and hasattr(scanner, '_order_executor'):
                    executor = scanner._order_executor
                    if hasattr(executor, 'test_connection') and executor.test_connection():
                        self._conn_label.configure(text="  API Connected", text_color="green")
                    else:
                        self._conn_label.configure(text="  API BASARISIZ", text_color="red")
                else:
                    self._conn_label.configure(text="  API yok", text_color="red")
            except Exception:
                self._conn_label.configure(text="  API BASARISIZ", text_color="red")
        elif self.controller.binance_app:
            success = self.controller.binance_app.refresh_connection()
            if success:
                count = len(self.controller.binance_app._descendants)
                self._conn_label.configure(text=f"  OK ({count})", text_color="green")
            else:
                self._conn_label.configure(text="  BASARISIZ", text_color="red")

    def _on_start(self) -> None:
        self.controller.start_scanner()

    def _on_stop(self) -> None:
        self.controller.stop_scanner()

    def _on_kill(self) -> None:
        self.controller.activate_kill_switch()
        self._kill_btn.configure(fg_color="red", text="KILLED!")

    def update_data(self, price: float = 0, change_pct: float = 0,
                    binance_connected: bool = False, ws_connected: bool = False,
                    symbol: str = "", strategy_running: bool = False,
                    killed: bool = False) -> None:
        # Connection
        if binance_connected:
            use_api = getattr(self.controller, 'config', None)
            api_mode = use_api.get("trading.use_api", False) if use_api else False
            label = "  API Connected" if api_mode else "  Connected"
            self._conn_label.configure(text=label, text_color="green")
        else:
            self._conn_label.configure(text="  Disconnected", text_color="red")

        # WS
        ws_text = "WS:ON" if ws_connected else "WS:--"
        ws_color = "green" if ws_connected else "red"
        self._ws_label.configure(text=ws_text, text_color=ws_color)

        # Strategy
        strat_text = "Str:ON" if strategy_running else "Str:OFF"
        strat_color = "green" if strategy_running else "gray"
        self._strategy_label.configure(text=strat_text, text_color=strat_color)

        # Kill
        if killed:
            self._kill_btn.configure(fg_color="red", text="KILLED!")
        else:
            self._kill_btn.configure(fg_color="darkred", text="KILL")

    def update_scanner_state(self, state: str, scan_count: int = 0,
                              candidate_text: str = "", candidate_color: str = "gray",
                              trade_text: str = "", trade_color: str = "gray") -> None:
        """Called by scanner panel to update scanner-specific status bar fields."""
        color = self.STATE_COLORS.get(state, "gray")
        self._state_lbl.configure(text=state, text_color=color)
        self._scan_count_lbl.configure(text=f"Tarama: {scan_count}")
        if candidate_text:
            self._candidate_lbl.configure(text=candidate_text, text_color=candidate_color)
        if trade_text:
            self._trade_lbl.configure(text=trade_text, text_color=trade_color)
