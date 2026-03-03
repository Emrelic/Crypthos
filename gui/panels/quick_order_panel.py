import threading
import customtkinter as ctk
from core.constants import OrderSide, OrderType
from gui.widgets.confirmation_dialog import ConfirmationDialog


class QuickOrderPanel(ctk.CTkFrame):
    """Quick order entry panel with Buy/Sell buttons."""

    def __init__(self, parent, controller):
        super().__init__(parent)
        self.controller = controller
        self.pack(fill="both", expand=True)
        self._build_ui()

    def _build_ui(self) -> None:
        # Order Type
        type_frame = ctk.CTkFrame(self)
        type_frame.pack(fill="x", padx=15, pady=(15, 5))
        ctk.CTkLabel(type_frame, text="Emir Tipi:").pack(side="left", padx=5)
        self._order_type_var = ctk.StringVar(value="Market")
        for ot in ["Limit", "Market", "Stop Limit"]:
            ctk.CTkRadioButton(
                type_frame, text=ot, variable=self._order_type_var,
                value=ot, command=self._on_type_change,
            ).pack(side="left", padx=10)

        # Price
        price_frame = ctk.CTkFrame(self)
        price_frame.pack(fill="x", padx=15, pady=5)
        ctk.CTkLabel(price_frame, text="Fiyat:", width=70, anchor="w").pack(side="left", padx=5)
        self._price_entry = ctk.CTkEntry(price_frame, placeholder_text="Market fiyat")
        self._price_entry.pack(side="left", fill="x", expand=True, padx=5)
        self._price_entry.configure(state="disabled")

        # Size
        size_frame = ctk.CTkFrame(self)
        size_frame.pack(fill="x", padx=15, pady=5)
        ctk.CTkLabel(size_frame, text="Miktar:", width=70, anchor="w").pack(side="left", padx=5)
        self._size_entry = ctk.CTkEntry(size_frame, placeholder_text="Adet")
        self._size_entry.pack(side="left", fill="x", expand=True, padx=5)
        self._notional_label = ctk.CTkLabel(size_frame, text="= $0.00", width=80)
        self._notional_label.pack(side="left", padx=5)

        # USDT shortcut buttons
        usdt_frame = ctk.CTkFrame(self)
        usdt_frame.pack(fill="x", padx=15, pady=2)
        ctk.CTkLabel(usdt_frame, text="", width=70).pack(side="left", padx=5)
        for amount in [5, 10, 25, 50, 100]:
            ctk.CTkButton(
                usdt_frame, text=f"${amount}", width=50, height=24,
                fg_color="gray30", command=lambda a=amount: self._set_size_usdt(a),
            ).pack(side="left", padx=3)

        # TP/SL
        tpsl_frame = ctk.CTkFrame(self)
        tpsl_frame.pack(fill="x", padx=15, pady=5)
        self._tpsl_var = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(tpsl_frame, text="TP/SL", variable=self._tpsl_var,
                        command=self._on_tpsl_toggle).pack(side="left", padx=5)

        self._tp_frame = ctk.CTkFrame(self)
        self._tp_frame.pack(fill="x", padx=15, pady=2)
        ctk.CTkLabel(self._tp_frame, text="TP (ROI%):", width=70, anchor="w").pack(side="left", padx=5)
        self._tp_entry = ctk.CTkEntry(self._tp_frame, width=100)
        self._tp_entry.pack(side="left", padx=5)
        self._tp_entry.insert(0, str(self.controller.config.get("risk.default_tp_percent", 5.0)))

        self._sl_frame = ctk.CTkFrame(self)
        self._sl_frame.pack(fill="x", padx=15, pady=2)
        ctk.CTkLabel(self._sl_frame, text="SL (ROI%):", width=70, anchor="w").pack(side="left", padx=5)
        self._sl_entry = ctk.CTkEntry(self._sl_frame, width=100)
        self._sl_entry.pack(side="left", padx=5)
        self._sl_entry.insert(0, str(self.controller.config.get("risk.default_sl_percent", 2.0)))

        # Reduce-Only
        ro_frame = ctk.CTkFrame(self)
        ro_frame.pack(fill="x", padx=15, pady=5)
        self._reduce_only_var = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(ro_frame, text="Reduce-Only",
                        variable=self._reduce_only_var).pack(side="left", padx=5)

        # Buy / Sell buttons
        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.pack(fill="x", padx=15, pady=15)
        self._buy_btn = ctk.CTkButton(
            btn_frame, text="BUY / LONG", height=45,
            fg_color="#00C853", hover_color="#00E676",
            font=ctk.CTkFont(size=15, weight="bold"),
            command=lambda: self._on_order(OrderSide.BUY_LONG),
        )
        self._buy_btn.pack(side="left", fill="x", expand=True, padx=(0, 5))

        self._sell_btn = ctk.CTkButton(
            btn_frame, text="SELL / SHORT", height=45,
            fg_color="#FF1744", hover_color="#FF5252",
            font=ctk.CTkFont(size=15, weight="bold"),
            command=lambda: self._on_order(OrderSide.SELL_SHORT),
        )
        self._sell_btn.pack(side="left", fill="x", expand=True, padx=(5, 0))

        # --- FEEDBACK LABEL ---
        self._feedback_label = ctk.CTkLabel(
            self, text="", height=30,
            font=ctk.CTkFont(size=13, weight="bold"),
        )
        self._feedback_label.pack(fill="x", padx=15, pady=(0, 5))

        # Info display
        info_frame = ctk.CTkFrame(self)
        info_frame.pack(fill="x", padx=15, pady=5)
        self._price_display = ctk.CTkLabel(info_frame, text="Fiyat: --")
        self._price_display.pack(side="left", padx=10)
        self._mark_display = ctk.CTkLabel(info_frame, text="Mark: --")
        self._mark_display.pack(side="left", padx=10)
        self._funding_display = ctk.CTkLabel(info_frame, text="Funding: --")
        self._funding_display.pack(side="left", padx=10)

    def _show_feedback(self, msg: str, color: str = "white", duration_ms: int = 4000) -> None:
        """Show a feedback message to the user."""
        self._feedback_label.configure(text=msg, text_color=color)
        if duration_ms > 0:
            self.after(duration_ms, lambda: self._feedback_label.configure(text=""))

    def _on_type_change(self) -> None:
        if self._order_type_var.get() == "Market":
            self._price_entry.configure(state="disabled")
        else:
            self._price_entry.configure(state="normal")

    def _on_tpsl_toggle(self) -> None:
        state = "normal" if self._tpsl_var.get() else "disabled"
        self._tp_entry.configure(state=state)
        self._sl_entry.configure(state=state)

    def _set_size_usdt(self, usdt: float) -> None:
        price = self.controller.get_current_price()
        if price > 0:
            qty = int(usdt / price)
            self._size_entry.delete(0, "end")
            self._size_entry.insert(0, str(qty))
            self._notional_label.configure(text=f"= ${usdt:.2f}")
            self._show_feedback(f"{qty} adet secildi (${usdt})", "white", 2000)
        else:
            self._show_feedback("Fiyat verisi bekleniyor...", "orange")

    def _on_order(self, side: OrderSide) -> None:
        try:
            order_type = OrderType(self._order_type_var.get())

            # Validate price for limit orders
            price = None
            if order_type != OrderType.MARKET:
                price_text = self._price_entry.get().strip()
                if not price_text:
                    self._show_feedback("HATA: Limit emir icin fiyat giriniz!", "red")
                    return
                price = float(price_text)

            # Validate size
            size_text = self._size_entry.get().strip()
            if not size_text:
                self._show_feedback("HATA: Miktar giriniz! ($5/$10/$25 butonlarini kullanin)", "red")
                return
            size = float(size_text)
            if size <= 0:
                self._show_feedback("HATA: Miktar 0'dan buyuk olmali!", "red")
                return

            # Check Binance connection
            if not (self.controller.binance_app and self.controller.binance_app.is_connected):
                self._show_feedback("HATA: Binance Desktop bagli degil!", "red")
                return

            tp = None
            sl = None
            if self._tpsl_var.get():
                tp_text = self._tp_entry.get().strip()
                sl_text = self._sl_entry.get().strip()
                tp = float(tp_text) if tp_text else None
                sl = float(sl_text) if sl_text else None

            current_price = self.controller.get_current_price()
            effective_price = price or current_price
            notional = size * effective_price

            # Confirmation check
            if self.controller.risk_manager and \
               self.controller.risk_manager.requires_confirmation(size, effective_price):
                symbol = self.controller.get_current_symbol()
                dialog = ConfirmationDialog(
                    self, symbol=symbol, side=side.value,
                    order_type=order_type.value,
                    price=effective_price, size=size,
                    notional=notional, tp_percent=tp, sl_percent=sl,
                )
                if not dialog.show():
                    self._show_feedback("Emir iptal edildi.", "orange")
                    return

            # Show sending feedback
            side_text = "LONG" if side == OrderSide.BUY_LONG else "SHORT"
            symbol = self.controller.get_current_symbol()
            self._show_feedback(
                f"Emir gonderiliyor: {side_text} {int(size)} {symbol} ...",
                "yellow", 0,
            )

            # Execute in background
            threading.Thread(
                target=self._execute_and_feedback,
                args=(symbol, side, order_type, price, size, tp, sl),
                daemon=True,
            ).start()

        except ValueError:
            self._show_feedback("HATA: Gecersiz deger girdiniz!", "red")

    def _execute_and_feedback(self, symbol, side, order_type, price, size, tp, sl):
        """Execute order and show result feedback on GUI."""
        success = self.controller.place_order(
            symbol=symbol, side=side, order_type=order_type,
            price=price, size=size,
            tp_percent=tp, sl_percent=sl,
            reduce_only=self._reduce_only_var.get(),
            trigger_source="quick_order",
        )
        side_text = "LONG" if side == OrderSide.BUY_LONG else "SHORT"
        if success:
            self.after(0, lambda: self._show_feedback(
                f"BASARILI: {side_text} {int(size)} {symbol} emri Binance'e girildi!",
                "#00E676",
            ))
        else:
            self.after(0, lambda: self._show_feedback(
                f"BASARISIZ: Emir gonderilemedi! Log'a bakiniz.",
                "red",
            ))

    def update_display(self, price: float = 0, mark_price: float = 0,
                       funding_rate: float = 0) -> None:
        fmt = ".6f" if price < 1 else ".2f"
        self._price_display.configure(text=f"Fiyat: {price:{fmt}}")
        self._mark_display.configure(text=f"Mark: {mark_price:{fmt}}")
        fr_pct = funding_rate * 100
        fr_color = "red" if funding_rate < 0 else "green"
        self._funding_display.configure(
            text=f"Funding: {fr_pct:.4f}%", text_color=fr_color,
        )
