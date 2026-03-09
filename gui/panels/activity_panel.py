import customtkinter as ctk
from datetime import datetime


# Level colors for log entries
LEVEL_COLORS = {
    "INFO": "#B0BEC5",
    "WARNING": "#FF9800",
    "ERROR": "#FF1744",
    "CRITICAL": "#FF1744",
    "BUY": "#00C853",
    "SELL+": "#00E676",
    "SELL-": "#FF5252",
    "TRADE+": "#00C853",
    "TRADE-": "#FF5252",
    "SCAN": "#2196F3",
    "REGIME": "#9C27B0",
}

# Level icons for quick visual scanning
LEVEL_ICONS = {
    "BUY": ">> ALIS",
    "SELL+": "<< SATIS (+)",
    "SELL-": "<< SATIS (-)",
    "TRADE+": "++ ISLEM",
    "TRADE-": "-- ISLEM",
    "SCAN": ":: TARAMA",
    "REGIME": "~~ REJIM",
    "WARNING": "!! UYARI",
    "ERROR": "XX HATA",
    "CRITICAL": "XX KRITIK",
    "INFO": "-- BILGI",
}


class ActivityPanel(ctk.CTkFrame):
    """Order history and event log panel."""

    MAX_LOG_LINES = 500

    def __init__(self, parent, controller):
        super().__init__(parent)
        self.controller = controller
        self.pack(fill="both", expand=True)
        self._log_count = 0
        self._build_ui()
        self._start_auto_refresh()

    def _build_ui(self) -> None:
        # Order history section
        order_header = ctk.CTkFrame(self)
        order_header.pack(fill="x", padx=10, pady=(10, 0))
        ctk.CTkLabel(order_header, text="Emir Gecmisi",
                     font=ctk.CTkFont(size=14, weight="bold")).pack(side="left", padx=5)
        ctk.CTkButton(order_header, text="Yenile", width=70, height=28,
                      command=self.refresh_orders).pack(side="right", padx=5)

        # Order table header
        cols_frame = ctk.CTkFrame(self)
        cols_frame.pack(fill="x", padx=10, pady=(5, 0))
        cols = [("Zaman", 140), ("Sembol", 100), ("Yon", 80), ("Tip", 70),
                ("Fiyat", 100), ("Miktar", 70), ("Tutar", 90), ("Durum", 80),
                ("Kaynak", 100)]
        for text, w in cols:
            ctk.CTkLabel(cols_frame, text=text, width=w,
                         font=ctk.CTkFont(size=11, weight="bold"),
                         text_color="gray").pack(side="left", padx=2)

        # Scrollable order list
        self._order_scroll = ctk.CTkScrollableFrame(self, height=220)
        self._order_scroll.pack(fill="x", padx=10, pady=(0, 10))

        # Event log section
        log_header = ctk.CTkFrame(self)
        log_header.pack(fill="x", padx=10)
        ctk.CTkLabel(log_header, text="Olay Logu",
                     font=ctk.CTkFont(size=14, weight="bold")).pack(side="left", padx=5)

        self._log_count_lbl = ctk.CTkLabel(log_header, text="0 kayit",
                                            font=ctk.CTkFont(size=11),
                                            text_color="gray")
        self._log_count_lbl.pack(side="left", padx=15)

        ctk.CTkButton(log_header, text="Temizle", width=70, height=28,
                      command=self._clear_log).pack(side="right", padx=5)

        self._log_text = ctk.CTkTextbox(self, height=300,
                                         font=ctk.CTkFont(family="Consolas", size=12))
        self._log_text.pack(fill="both", expand=True, padx=10, pady=5)

    def _start_auto_refresh(self) -> None:
        """Auto-refresh order table every 10 seconds."""
        self.refresh_orders()
        self.after(10000, self._start_auto_refresh)

    def refresh_orders(self) -> None:
        # Clear existing
        for w in self._order_scroll.winfo_children():
            w.destroy()

        if not self.controller.order_logger:
            return

        orders = self.controller.order_logger.get_recent_orders(50)
        for order in orders:
            row = ctk.CTkFrame(self._order_scroll, fg_color="transparent")
            row.pack(fill="x", pady=1)

            ts = order.get("timestamp", "")[:19]
            symbol = order.get("symbol", "")
            side = order.get("side", "")
            otype = order.get("order_type", "")
            price = order.get("price", 0)
            size = order.get("size", 0)
            notional = order.get("notional_usdt", 0)
            status = order.get("status", "")
            trigger = order.get("trigger_source", "")

            side_color = "#00C853" if "Buy" in side else "#FF1744"
            if status == "placed":
                status_color = "#00C853"
                status_text = "basarili"
            elif status == "rejected_risk":
                status_color = "#FF9800"
                status_text = "risk_red"
            elif status == "failed":
                status_color = "#FF1744"
                status_text = "basarisiz"
            else:
                status_color = "gray"
                status_text = status

            price_str = "--"
            if price:
                if price < 0.01:
                    price_str = f"{price:.8f}"
                elif price < 1:
                    price_str = f"{price:.6f}"
                else:
                    price_str = f"{price:.2f}"

            vals = [
                (ts, 140, "white"),
                (symbol, 100, "white"),
                (side, 80, side_color),
                (otype, 70, "white"),
                (price_str, 100, "white"),
                (str(int(size)) if size else "--", 70, "white"),
                (f"${notional:.2f}" if notional else "--", 90, "white"),
                (status_text, 80, status_color),
                (trigger[:14] if trigger else "--", 100, "gray"),
            ]
            for text, w, color in vals:
                ctk.CTkLabel(row, text=text, width=w, text_color=color,
                             font=ctk.CTkFont(size=11)).pack(side="left", padx=2)

    def add_log_entry(self, level: str, message: str) -> None:
        timestamp = datetime.now().strftime("%H:%M:%S")
        icon = LEVEL_ICONS.get(level, f"   {level}")
        entry = f"[{timestamp}] {icon}  {message}\n"
        self._log_text.insert("end", entry)
        self._log_text.see("end")
        self._log_count += 1
        self._log_count_lbl.configure(text=f"{self._log_count} kayit")

        # Trim old entries if too many
        if self._log_count > self.MAX_LOG_LINES:
            self._log_text.delete("1.0", f"{self._log_count - self.MAX_LOG_LINES}.0")
            self._log_count = self.MAX_LOG_LINES

    def _clear_log(self) -> None:
        self._log_text.delete("1.0", "end")
        self._log_count = 0
        self._log_count_lbl.configure(text="0 kayit")
