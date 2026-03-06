import customtkinter as ctk


class StatusBar(ctk.CTkFrame):
    """Top status bar: connection, pair, price, kill switch."""

    def __init__(self, parent, controller):
        super().__init__(parent, height=40)
        self.controller = controller
        self.pack_propagate(False)

        # Connection indicator + reconnect button
        self._conn_label = ctk.CTkLabel(self, text="  Disconnected", width=130,
                                        text_color="red", anchor="w")
        self._conn_label.pack(side="left", padx=5)

        self._reconnect_btn = ctk.CTkButton(
            self, text="Baglan", width=60, height=24,
            fg_color="gray30", hover_color="gray40",
            command=self._on_reconnect,
        )
        self._reconnect_btn.pack(side="left", padx=2)

        # WS indicator
        self._ws_label = ctk.CTkLabel(self, text="WS: --", width=80,
                                      text_color="gray", anchor="w")
        self._ws_label.pack(side="left", padx=5)

        # Active pair
        self._pair_label = ctk.CTkLabel(self, text="DOGEUSDT", width=100,
                                        font=ctk.CTkFont(weight="bold"))
        self._pair_label.pack(side="left", padx=10)

        # Price
        self._price_label = ctk.CTkLabel(self, text="$0.00000", width=120,
                                         font=ctk.CTkFont(size=14, weight="bold"))
        self._price_label.pack(side="left", padx=5)

        # Price change
        self._change_label = ctk.CTkLabel(self, text="0.00%", width=80)
        self._change_label.pack(side="left", padx=5)

        # Strategy status
        self._strategy_label = ctk.CTkLabel(self, text="Strategy: OFF", width=120,
                                            text_color="gray")
        self._strategy_label.pack(side="left", padx=10)

        # Kill switch button
        self._kill_btn = ctk.CTkButton(
            self, text="KILL SWITCH", width=110, height=28,
            fg_color="darkred", hover_color="red",
            command=self._on_kill,
        )
        self._kill_btn.pack(side="right", padx=10)

    def _on_reconnect(self) -> None:
        """Force reconnect — API test or Binance Desktop."""
        self._conn_label.configure(text="  Baglaniyor...", text_color="yellow")
        self.update()
        use_api = getattr(self.controller, 'config', None)
        api_mode = use_api.get("trading.use_api", False) if use_api else False
        if api_mode:
            # API mode: test API connection
            try:
                from automation.api_order_executor import ApiOrderExecutor
                scanner = self.controller.scanner
                if scanner and hasattr(scanner, '_order_executor'):
                    executor = scanner._order_executor
                    if hasattr(executor, 'test_connection') and executor.test_connection():
                        self._conn_label.configure(
                            text="  API Connected", text_color="green")
                    else:
                        self._conn_label.configure(text="  API BASARISIZ", text_color="red")
                else:
                    self._conn_label.configure(text="  API not configured", text_color="red")
            except Exception:
                self._conn_label.configure(text="  API BASARISIZ", text_color="red")
        elif self.controller.binance_app:
            success = self.controller.binance_app.refresh_connection()
            if success:
                count = len(self.controller.binance_app._descendants)
                self._conn_label.configure(
                    text=f"  Connected ({count})", text_color="green")
            else:
                self._conn_label.configure(text="  BASARISIZ", text_color="red")

    def _on_kill(self) -> None:
        self.controller.activate_kill_switch()
        self._kill_btn.configure(fg_color="red", text="KILLED!")

    def update_data(self, price: float = 0, change_pct: float = 0,
                    binance_connected: bool = False, ws_connected: bool = False,
                    symbol: str = "", strategy_running: bool = False,
                    killed: bool = False) -> None:
        if symbol:
            self._pair_label.configure(text=symbol)

        self._price_label.configure(text=f"${price:.6f}" if price < 1 else f"${price:.2f}")

        color = "green" if change_pct >= 0 else "red"
        self._change_label.configure(text=f"{change_pct:+.2f}%", text_color=color)

        if binance_connected:
            use_api = getattr(self.controller, 'config', None)
            api_mode = use_api.get("trading.use_api", False) if use_api else False
            label = "  API Connected" if api_mode else "  Connected"
            self._conn_label.configure(text=label, text_color="green")
        else:
            self._conn_label.configure(text="  Disconnected", text_color="red")

        ws_text = "WS: ON" if ws_connected else "WS: OFF"
        ws_color = "green" if ws_connected else "red"
        self._ws_label.configure(text=ws_text, text_color=ws_color)

        strat_text = "Strategy: ON" if strategy_running else "Strategy: OFF"
        strat_color = "green" if strategy_running else "gray"
        self._strategy_label.configure(text=strat_text, text_color=strat_color)

        if killed:
            self._kill_btn.configure(fg_color="red", text="KILLED!")
        else:
            self._kill_btn.configure(fg_color="darkred", text="KILL SWITCH")
