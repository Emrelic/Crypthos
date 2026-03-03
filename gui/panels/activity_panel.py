import customtkinter as ctk
from datetime import datetime


class ActivityPanel(ctk.CTkFrame):
    """Order history and event log panel."""

    def __init__(self, parent, controller):
        super().__init__(parent)
        self.controller = controller
        self.pack(fill="both", expand=True)
        self._build_ui()

    def _build_ui(self) -> None:
        # Order history section
        order_header = ctk.CTkFrame(self)
        order_header.pack(fill="x", padx=10, pady=(10, 0))
        ctk.CTkLabel(order_header, text="Emir Gecmisi",
                     font=ctk.CTkFont(weight="bold")).pack(side="left", padx=5)
        ctk.CTkButton(order_header, text="Yenile", width=70, height=24,
                      command=self.refresh_orders).pack(side="right", padx=5)

        # Order table header
        cols_frame = ctk.CTkFrame(self)
        cols_frame.pack(fill="x", padx=10, pady=(5, 0))
        cols = [("Zaman", 130), ("Sembol", 90), ("Yon", 70), ("Tip", 60),
                ("Fiyat", 90), ("Miktar", 60), ("Tutar", 80), ("Durum", 70)]
        for text, w in cols:
            ctk.CTkLabel(cols_frame, text=text, width=w,
                         font=ctk.CTkFont(size=11, weight="bold"),
                         text_color="gray").pack(side="left", padx=2)

        # Scrollable order list
        self._order_scroll = ctk.CTkScrollableFrame(self, height=200)
        self._order_scroll.pack(fill="x", padx=10, pady=(0, 10))

        # Event log section
        log_header = ctk.CTkFrame(self)
        log_header.pack(fill="x", padx=10)
        ctk.CTkLabel(log_header, text="Olay Logu",
                     font=ctk.CTkFont(weight="bold")).pack(side="left", padx=5)
        ctk.CTkButton(log_header, text="Temizle", width=70, height=24,
                      command=self._clear_log).pack(side="right", padx=5)

        self._log_text = ctk.CTkTextbox(self, height=200)
        self._log_text.pack(fill="both", expand=True, padx=10, pady=5)

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

            side_color = "#00C853" if "Buy" in side else "#FF1744"
            status_color = "green" if status == "placed" else "red"

            vals = [
                (ts, 130, "white"),
                (symbol, 90, "white"),
                (side, 70, side_color),
                (otype, 60, "white"),
                (f"{price:.6f}" if price and price < 1 else f"{price:.2f}" if price else "--", 90, "white"),
                (str(int(size)) if size else "--", 60, "white"),
                (f"${notional:.2f}" if notional else "--", 80, "white"),
                (status, 70, status_color),
            ]
            for text, w, color in vals:
                ctk.CTkLabel(row, text=text, width=w, text_color=color,
                             font=ctk.CTkFont(size=11)).pack(side="left", padx=2)

    def add_log_entry(self, level: str, message: str) -> None:
        timestamp = datetime.now().strftime("%H:%M:%S")
        color_map = {"INFO": "white", "WARNING": "orange", "ERROR": "red",
                     "CRITICAL": "red"}
        entry = f"[{timestamp}] [{level}] {message}\n"
        self._log_text.insert("end", entry)
        self._log_text.see("end")

    def _clear_log(self) -> None:
        self._log_text.delete("1.0", "end")
