import customtkinter as ctk
from core.constants import ConditionOperator, OrderSide, OrderType
from strategy.condition import Condition
from strategy.actions import TradeAction
from strategy.rule import Rule
from strategy.strategy import Strategy


INDICATOR_OPTIONS = [
    # Momentum
    "RSI", "StochRSI_K", "StochRSI_D", "Stoch_K", "Stoch_D",
    "CCI", "Williams_R", "MFI", "ROC", "UltOsc",
    # Trend
    "ADX", "ADX_plus_DI", "ADX_minus_DI",
    "PSAR_trend", "Supertrend_trend", "Ichimoku_Position",
    "Aroon_Up", "Aroon_Down", "Aroon_Osc",
    # Volatility
    "BB_PercentB", "BB_Width", "BB_Upper", "BB_Lower",
    "ATR", "DC_Upper", "DC_Lower",
    # Volume
    "OBV_slope", "CMF", "VWAP",
    # MAs
    "SMA_fast", "SMA_slow", "EMA_fast", "HMA", "DEMA", "TEMA", "VWMA",
    # MACD
    "MACD_line", "MACD_signal", "MACD_histogram",
    "MACD_bullish_cross", "MACD_bearish_cross",
    # Price
    "Price", "Mark_Price", "Funding_Rate",
]

OPERATOR_OPTIONS = ["<", "<=", ">", ">=", "==", "crosses_above", "crosses_below"]

# Pre-built strategy templates
TEMPLATES = {
    "RSI Al-Sat": {
        "description": "RSI 30 altinda Long, RSI 70 ustunde Short",
        "rules": [
            {
                "name": "RSI_Oversold_Buy",
                "conditions": [("RSI", "<", "30")],
                "side": "Buy/Long",
                "order_type": "Market",
                "size_usdt": 10,
                "tp": 5.0,
                "sl": 2.0,
                "cooldown": 120,
            },
            {
                "name": "RSI_Overbought_Sell",
                "conditions": [("RSI", ">", "70")],
                "side": "Sell/Short",
                "order_type": "Market",
                "size_usdt": 10,
                "tp": 5.0,
                "sl": 2.0,
                "cooldown": 120,
            },
        ],
    },
    "MACD Cross": {
        "description": "MACD bullish cross'ta Long, bearish cross'ta Short",
        "rules": [
            {
                "name": "MACD_Bullish",
                "conditions": [("MACD_bullish_cross", "==", "1")],
                "side": "Buy/Long",
                "order_type": "Market",
                "size_usdt": 10,
                "tp": 3.0,
                "sl": 2.0,
                "cooldown": 300,
            },
            {
                "name": "MACD_Bearish",
                "conditions": [("MACD_bearish_cross", "==", "1")],
                "side": "Sell/Short",
                "order_type": "Market",
                "size_usdt": 10,
                "tp": 3.0,
                "sl": 2.0,
                "cooldown": 300,
            },
        ],
    },
    "RSI + MACD Combo": {
        "description": "RSI ve MACD birlikte onay verince islem ac",
        "rules": [
            {
                "name": "RSI_MACD_Long",
                "conditions": [("RSI", "<", "40"), ("MACD_bullish_cross", "==", "1")],
                "side": "Buy/Long",
                "order_type": "Market",
                "size_usdt": 15,
                "tp": 5.0,
                "sl": 2.0,
                "cooldown": 300,
            },
            {
                "name": "RSI_MACD_Short",
                "conditions": [("RSI", ">", "60"), ("MACD_bearish_cross", "==", "1")],
                "side": "Sell/Short",
                "order_type": "Market",
                "size_usdt": 15,
                "tp": 5.0,
                "sl": 2.0,
                "cooldown": 300,
            },
        ],
    },
    "Bollinger Revert": {
        "description": "Bollinger Band'dan mean reversion - band disina cikinca geri don",
        "rules": [
            {
                "name": "BB_Oversold",
                "conditions": [("BB_PercentB", "<", "0.05"), ("RSI", "<", "35")],
                "side": "Buy/Long",
                "order_type": "Market",
                "size_usdt": 10,
                "tp": 3.0,
                "sl": 2.0,
                "cooldown": 180,
            },
            {
                "name": "BB_Overbought",
                "conditions": [("BB_PercentB", ">", "0.95"), ("RSI", ">", "65")],
                "side": "Sell/Short",
                "order_type": "Market",
                "size_usdt": 10,
                "tp": 3.0,
                "sl": 2.0,
                "cooldown": 180,
            },
        ],
    },
    "Supertrend Follow": {
        "description": "Supertrend trend degisiminde islem ac",
        "rules": [
            {
                "name": "ST_Long",
                "conditions": [("Supertrend_trend", "==", "UP"), ("ADX", ">", "25")],
                "side": "Buy/Long",
                "order_type": "Market",
                "size_usdt": 15,
                "tp": 5.0,
                "sl": 3.0,
                "cooldown": 600,
            },
            {
                "name": "ST_Short",
                "conditions": [("Supertrend_trend", "==", "DOWN"), ("ADX", ">", "25")],
                "side": "Sell/Short",
                "order_type": "Market",
                "size_usdt": 15,
                "tp": 5.0,
                "sl": 3.0,
                "cooldown": 600,
            },
        ],
    },
    "Ichimoku Cloud": {
        "description": "Ichimoku Cloud yukari/asagi gecislerinde islem ac",
        "rules": [
            {
                "name": "Ichi_Long",
                "conditions": [("Ichimoku_Position", "==", "ABOVE"), ("MACD_histogram", ">", "0")],
                "side": "Buy/Long",
                "order_type": "Market",
                "size_usdt": 12,
                "tp": 4.0,
                "sl": 2.5,
                "cooldown": 600,
            },
            {
                "name": "Ichi_Short",
                "conditions": [("Ichimoku_Position", "==", "BELOW"), ("MACD_histogram", "<", "0")],
                "side": "Sell/Short",
                "order_type": "Market",
                "size_usdt": 12,
                "tp": 4.0,
                "sl": 2.5,
                "cooldown": 600,
            },
        ],
    },
    "Volume Breakout": {
        "description": "Hacim artisi + Bollinger daralma = kirilim",
        "rules": [
            {
                "name": "Vol_Breakout_Long",
                "conditions": [("BB_Width", "<", "2.0"), ("OBV_slope", ">", "0"), ("RSI", ">", "55")],
                "side": "Buy/Long",
                "order_type": "Market",
                "size_usdt": 10,
                "tp": 5.0,
                "sl": 2.0,
                "cooldown": 300,
            },
            {
                "name": "Vol_Breakout_Short",
                "conditions": [("BB_Width", "<", "2.0"), ("OBV_slope", "<", "0"), ("RSI", "<", "45")],
                "side": "Sell/Short",
                "order_type": "Market",
                "size_usdt": 10,
                "tp": 5.0,
                "sl": 2.0,
                "cooldown": 300,
            },
        ],
    },
    "Multi Confluence": {
        "description": "5+ indikator ayni yonu gosterince islem ac (en guvenli)",
        "rules": [
            {
                "name": "Confluence_Long",
                "conditions": [
                    ("RSI", "<", "40"),
                    ("MACD_histogram", ">", "0"),
                    ("Supertrend_trend", "==", "UP"),
                    ("CMF", ">", "0.05"),
                ],
                "side": "Buy/Long",
                "order_type": "Market",
                "size_usdt": 20,
                "tp": 5.0,
                "sl": 2.0,
                "cooldown": 600,
            },
            {
                "name": "Confluence_Short",
                "conditions": [
                    ("RSI", ">", "60"),
                    ("MACD_histogram", "<", "0"),
                    ("Supertrend_trend", "==", "DOWN"),
                    ("CMF", "<", "-0.05"),
                ],
                "side": "Sell/Short",
                "order_type": "Market",
                "size_usdt": 20,
                "tp": 5.0,
                "sl": 2.0,
                "cooldown": 600,
            },
        ],
    },
}


class StrategyPanel(ctk.CTkFrame):
    """Strategy builder and manager panel."""

    def __init__(self, parent, controller):
        super().__init__(parent)
        self.controller = controller
        self.pack(fill="both", expand=True)
        self._condition_rows = []
        self._build_ui()

    def _build_ui(self) -> None:
        # ============================================
        # TOP: ENGINE CONTROL + FEEDBACK
        # ============================================
        engine_frame = ctk.CTkFrame(self, fg_color="#1a1a2e")
        engine_frame.pack(fill="x", padx=10, pady=(10, 5))

        ctk.CTkLabel(engine_frame, text="Otomatik Al-Sat Motoru",
                     font=ctk.CTkFont(size=16, weight="bold")).pack(
            side="left", padx=10, pady=10)

        self._engine_status = ctk.CTkLabel(
            engine_frame, text="KAPALI",
            font=ctk.CTkFont(size=14, weight="bold"),
            text_color="red", width=80,
        )
        self._engine_status.pack(side="left", padx=10)

        self._stop_btn = ctk.CTkButton(
            engine_frame, text="DURDUR", width=100, height=35,
            fg_color="darkred", hover_color="red",
            font=ctk.CTkFont(weight="bold"),
            command=self._stop_engine,
        )
        self._stop_btn.pack(side="right", padx=5, pady=8)

        self._start_btn = ctk.CTkButton(
            engine_frame, text="BASLAT", width=100, height=35,
            fg_color="#00C853", hover_color="#00E676",
            font=ctk.CTkFont(weight="bold"),
            command=self._start_engine,
        )
        self._start_btn.pack(side="right", padx=5, pady=8)

        # Feedback
        self._feedback = ctk.CTkLabel(self, text="", height=25,
                                      font=ctk.CTkFont(size=12, weight="bold"))
        self._feedback.pack(fill="x", padx=10)

        # ============================================
        # TEMPLATES: Quick strategy setup
        # ============================================
        tmpl_frame = ctk.CTkFrame(self)
        tmpl_frame.pack(fill="x", padx=10, pady=5)

        tmpl_header = ctk.CTkFrame(tmpl_frame, fg_color="transparent")
        tmpl_header.pack(fill="x")
        ctk.CTkLabel(tmpl_header, text="Hazir Sablonlar",
                     font=ctk.CTkFont(weight="bold")).pack(side="left", padx=5, pady=5)

        tmpl_btns = ctk.CTkFrame(tmpl_frame, fg_color="transparent")
        tmpl_btns.pack(fill="x", padx=5, pady=5)
        for tmpl_name in TEMPLATES:
            desc = TEMPLATES[tmpl_name]["description"]
            btn = ctk.CTkButton(
                tmpl_btns, text=tmpl_name, width=140, height=30,
                fg_color="gray30", hover_color="gray40",
                command=lambda n=tmpl_name: self._apply_template(n),
            )
            btn.pack(side="left", padx=5)

        # ============================================
        # STRATEGY LIST
        # ============================================
        list_frame = ctk.CTkFrame(self)
        list_frame.pack(fill="x", padx=10, pady=5)

        header = ctk.CTkFrame(list_frame, fg_color="transparent")
        header.pack(fill="x")
        ctk.CTkLabel(header, text="Aktif Stratejiler",
                     font=ctk.CTkFont(weight="bold")).pack(side="left", padx=5)
        ctk.CTkButton(header, text="+ Yeni", width=70, height=24,
                      command=self._new_strategy).pack(side="right", padx=5)
        ctk.CTkButton(header, text="Sil", width=50, height=24,
                      fg_color="darkred", command=self._delete_strategy
                      ).pack(side="right", padx=5)

        self._strategy_scroll = ctk.CTkScrollableFrame(list_frame, height=100)
        self._strategy_scroll.pack(fill="x", padx=5, pady=5)

        # ============================================
        # STRATEGY EDITOR
        # ============================================
        editor = ctk.CTkFrame(self)
        editor.pack(fill="x", padx=10, pady=5)
        ctk.CTkLabel(editor, text="Strateji Editoru",
                     font=ctk.CTkFont(weight="bold")).pack(anchor="w", padx=5, pady=5)

        row1 = ctk.CTkFrame(editor)
        row1.pack(fill="x", padx=5, pady=2)
        ctk.CTkLabel(row1, text="Ad:", width=60, anchor="w").pack(side="left")
        self._name_entry = ctk.CTkEntry(row1, width=200)
        self._name_entry.pack(side="left", padx=5)
        ctk.CTkLabel(row1, text="Sembol:", width=60, anchor="w").pack(side="left", padx=(10, 0))
        self._symbol_entry = ctk.CTkEntry(row1, width=120)
        self._symbol_entry.pack(side="left", padx=5)
        self._symbol_entry.insert(0, self.controller.get_current_symbol())

        row2 = ctk.CTkFrame(editor)
        row2.pack(fill="x", padx=5, pady=2)
        ctk.CTkLabel(row2, text="Aciklama:", width=60, anchor="w").pack(side="left")
        self._desc_entry = ctk.CTkEntry(row2)
        self._desc_entry.pack(side="left", fill="x", expand=True, padx=5)

        # Rule builder
        rule_frame = ctk.CTkFrame(self)
        rule_frame.pack(fill="both", expand=True, padx=10, pady=5)

        rule_header = ctk.CTkFrame(rule_frame, fg_color="transparent")
        rule_header.pack(fill="x")
        ctk.CTkLabel(rule_header, text="Kural Olusturucu",
                     font=ctk.CTkFont(weight="bold")).pack(side="left", padx=5)
        ctk.CTkButton(rule_header, text="+ Kosul Ekle", width=100, height=24,
                      command=self._add_condition_row).pack(side="right", padx=5)

        # Conditions scroll
        self._cond_scroll = ctk.CTkScrollableFrame(rule_frame, height=80)
        self._cond_scroll.pack(fill="x", padx=5, pady=5)

        # Add one default condition row
        self._add_condition_row()

        # Action settings
        action_frame = ctk.CTkFrame(rule_frame)
        action_frame.pack(fill="x", padx=5, pady=5)

        row_a1 = ctk.CTkFrame(action_frame, fg_color="transparent")
        row_a1.pack(fill="x", pady=2)
        ctk.CTkLabel(row_a1, text="Aksiyon:", width=60, anchor="w").pack(side="left")
        self._side_var = ctk.StringVar(value="Buy/Long")
        ctk.CTkOptionMenu(row_a1, variable=self._side_var,
                          values=["Buy/Long", "Sell/Short"], width=110).pack(side="left", padx=5)
        self._otype_var = ctk.StringVar(value="Market")
        ctk.CTkOptionMenu(row_a1, variable=self._otype_var,
                          values=["Market", "Limit"], width=90).pack(side="left", padx=5)

        row_a2 = ctk.CTkFrame(action_frame, fg_color="transparent")
        row_a2.pack(fill="x", pady=2)
        ctk.CTkLabel(row_a2, text="Tutar (USDT):", width=90, anchor="w").pack(side="left")
        self._size_usdt_entry = ctk.CTkEntry(row_a2, width=80)
        self._size_usdt_entry.pack(side="left", padx=5)
        self._size_usdt_entry.insert(0, "10")
        ctk.CTkLabel(row_a2, text="TP%:", width=40).pack(side="left", padx=(10, 0))
        self._action_tp = ctk.CTkEntry(row_a2, width=60)
        self._action_tp.pack(side="left", padx=5)
        self._action_tp.insert(0, "5.0")
        ctk.CTkLabel(row_a2, text="SL%:", width=40).pack(side="left", padx=(10, 0))
        self._action_sl = ctk.CTkEntry(row_a2, width=60)
        self._action_sl.pack(side="left", padx=5)
        self._action_sl.insert(0, "2.0")

        row_a3 = ctk.CTkFrame(action_frame, fg_color="transparent")
        row_a3.pack(fill="x", pady=2)
        ctk.CTkLabel(row_a3, text="Cooldown (sn):", width=100, anchor="w").pack(side="left")
        self._cooldown_entry = ctk.CTkEntry(row_a3, width=80)
        self._cooldown_entry.pack(side="left", padx=5)
        self._cooldown_entry.insert(0, "60")

        # Save button
        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.pack(fill="x", padx=10, pady=10)
        ctk.CTkButton(btn_frame, text="Stratejiyi Kaydet", width=150,
                      command=self._save_strategy).pack(side="left", padx=5)
        ctk.CTkButton(btn_frame, text="Dosyaya Kaydet", width=130,
                      fg_color="gray30", command=self._save_to_file).pack(side="left", padx=5)
        ctk.CTkButton(btn_frame, text="Dosyadan Yukle", width=130,
                      fg_color="gray30", command=self._load_from_file).pack(side="left", padx=5)

        # Refresh engine status periodically
        self._update_engine_status()

    # ---- Engine Control ----
    def _start_engine(self) -> None:
        if self.controller.strategy_engine:
            strategies = self.controller.strategy_engine.get_all_strategies()
            if not strategies:
                self._show_feedback("Once bir strateji ekleyin veya sablon secin!", "orange")
                return
            enabled = [s for s in strategies if s.enabled]
            if not enabled:
                self._show_feedback("Hicbir strateji aktif degil! Switch'leri acin.", "orange")
                return
            self.controller.start_strategy_engine()
            names = ", ".join(s.name for s in enabled)
            self._show_feedback(f"Motor BASLADI! Aktif: {names}", "#00E676")
        else:
            self._show_feedback("Strateji motoru hazir degil!", "red")

    def _stop_engine(self) -> None:
        if self.controller.strategy_engine:
            self.controller.stop_strategy_engine()
            self._show_feedback("Motor DURDU.", "orange")

    def _update_engine_status(self) -> None:
        if self.controller.strategy_engine and self.controller.strategy_engine.is_running:
            self._engine_status.configure(text="CALISIYOR", text_color="#00E676")
        else:
            self._engine_status.configure(text="KAPALI", text_color="red")
        self.after(1000, self._update_engine_status)

    def _show_feedback(self, msg: str, color: str = "white") -> None:
        self._feedback.configure(text=msg, text_color=color)
        self.after(6000, lambda: self._feedback.configure(text=""))

    # ---- Templates ----
    def _apply_template(self, template_name: str) -> None:
        tmpl = TEMPLATES[template_name]
        symbol = self.controller.get_current_symbol()

        if not self.controller.strategy_engine:
            self._show_feedback("Strateji motoru hazir degil!", "red")
            return

        for rule_def in tmpl["rules"]:
            conditions = []
            for ind, op, val in rule_def["conditions"]:
                try:
                    threshold = float(val)
                except ValueError:
                    threshold = val
                conditions.append(Condition(ind, ConditionOperator(op), threshold))

            action = TradeAction(
                side=OrderSide(rule_def["side"]),
                order_type=OrderType(rule_def["order_type"]),
                size_usdt=rule_def["size_usdt"],
                tp_percent=rule_def.get("tp"),
                sl_percent=rule_def.get("sl"),
            )
            rule = Rule(rule_def["name"], conditions, action, rule_def.get("cooldown", 60))

            strategy = Strategy(
                name=f"{template_name}_{rule_def['name']}",
                symbol=symbol,
                rules=[rule],
                description=tmpl["description"],
            )
            strategy.enabled = True
            self.controller.strategy_engine.add_strategy(strategy)

        self.refresh()
        count = len(tmpl["rules"])
        self._show_feedback(
            f"'{template_name}' sablonu eklendi! ({count} kural, {symbol})", "#00E676"
        )

    # ---- Conditions ----
    def _add_condition_row(self) -> None:
        row = ctk.CTkFrame(self._cond_scroll)
        row.pack(fill="x", pady=2)

        ind_var = ctk.StringVar(value="RSI")
        ctk.CTkOptionMenu(row, variable=ind_var, values=INDICATOR_OPTIONS,
                          width=130).pack(side="left", padx=2)

        op_var = ctk.StringVar(value="<")
        ctk.CTkOptionMenu(row, variable=op_var, values=OPERATOR_OPTIONS,
                          width=110).pack(side="left", padx=2)

        val_entry = ctk.CTkEntry(row, width=100, placeholder_text="Deger")
        val_entry.pack(side="left", padx=2)

        ctk.CTkButton(row, text="X", width=30, height=24, fg_color="darkred",
                      command=lambda: self._remove_condition(row)).pack(side="left", padx=2)

        self._condition_rows.append((row, ind_var, op_var, val_entry))

    def _remove_condition(self, row_frame) -> None:
        self._condition_rows = [
            r for r in self._condition_rows if r[0] != row_frame
        ]
        row_frame.destroy()

    def _save_strategy(self) -> None:
        name = self._name_entry.get().strip()
        symbol = self._symbol_entry.get().strip().upper()
        if not name:
            self._show_feedback("Strateji adi giriniz!", "orange")
            return
        if not symbol:
            self._show_feedback("Sembol giriniz!", "orange")
            return

        conditions = []
        for _, ind_var, op_var, val_entry in self._condition_rows:
            indicator = ind_var.get()
            operator = ConditionOperator(op_var.get())
            val_text = val_entry.get().strip()
            if not val_text:
                self._show_feedback("Kosul degeri bos birakilamaz!", "orange")
                return
            try:
                threshold = float(val_text)
            except ValueError:
                threshold = val_text
            conditions.append(Condition(indicator, operator, threshold))

        if not conditions:
            self._show_feedback("En az bir kosul ekleyin!", "orange")
            return

        action = TradeAction(
            side=OrderSide(self._side_var.get()),
            order_type=OrderType(self._otype_var.get()),
            size_usdt=float(self._size_usdt_entry.get() or 10),
            tp_percent=float(self._action_tp.get() or 0) or None,
            sl_percent=float(self._action_sl.get() or 0) or None,
        )

        cooldown = int(self._cooldown_entry.get() or 60)
        rule = Rule(f"{name}_rule1", conditions, action, cooldown)

        strategy = Strategy(name, symbol, [rule], self._desc_entry.get())
        strategy.enabled = True

        if self.controller.strategy_engine:
            self.controller.strategy_engine.add_strategy(strategy)

        self.refresh()
        self._show_feedback(f"Strateji '{name}' kaydedildi!", "#00E676")

    def _new_strategy(self) -> None:
        self._name_entry.delete(0, "end")
        self._desc_entry.delete(0, "end")
        for row, _, _, _ in self._condition_rows:
            row.destroy()
        self._condition_rows.clear()
        self._add_condition_row()

    def _delete_strategy(self) -> None:
        name = self._name_entry.get().strip()
        if name and self.controller.strategy_engine:
            self.controller.strategy_engine.remove_strategy(name)
            self.refresh()
            self._show_feedback(f"Strateji '{name}' silindi.", "orange")
        else:
            self._show_feedback("Silmek icin strateji adi giriniz!", "orange")

    def _save_to_file(self) -> None:
        if self.controller.strategy_engine:
            self.controller.strategy_engine.save_strategies()
            self._show_feedback("Stratejiler dosyaya kaydedildi!", "#00E676")

    def _load_from_file(self) -> None:
        if self.controller.strategy_engine:
            self.controller.strategy_engine.load_strategies()
            self.refresh()
            self._show_feedback("Stratejiler dosyadan yuklendi!", "#00E676")

    def refresh(self) -> None:
        for w in self._strategy_scroll.winfo_children():
            w.destroy()

        if not self.controller.strategy_engine:
            return

        for strategy in self.controller.strategy_engine.get_all_strategies():
            row = ctk.CTkFrame(self._strategy_scroll)
            row.pack(fill="x", pady=2)

            ctk.CTkLabel(row, text=strategy.name, width=150,
                         font=ctk.CTkFont(weight="bold")).pack(side="left", padx=5)
            ctk.CTkLabel(row, text=strategy.symbol, width=90).pack(side="left", padx=5)
            ctk.CTkLabel(row, text=f"{len(strategy.rules)} kural",
                         width=70).pack(side="left", padx=5)

            switch = ctk.CTkSwitch(
                row, text="", width=40,
                command=lambda s=strategy: self._toggle_strategy(s),
            )
            if strategy.enabled:
                switch.select()
            switch.pack(side="right", padx=10)

    def _toggle_strategy(self, strategy) -> None:
        strategy.enabled = not strategy.enabled
        status = "ACIK" if strategy.enabled else "KAPALI"
        self._show_feedback(f"'{strategy.name}' -> {status}", "white")
