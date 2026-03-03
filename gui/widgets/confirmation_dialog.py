import customtkinter as ctk


class ConfirmationDialog(ctk.CTkToplevel):
    """Modal dialog for order confirmation."""

    def __init__(self, parent, symbol: str, side: str, order_type: str,
                 price: float, size: float, notional: float,
                 tp_percent: float = None, sl_percent: float = None):
        super().__init__(parent)
        self.title("Emir Onayı")
        self.geometry("400x350")
        self.resizable(False, False)
        self._confirmed = False

        self.transient(parent)
        self.grab_set()

        # Header
        color = "#00C853" if "Buy" in side else "#FF1744"
        ctk.CTkLabel(self, text=f"{side}", font=ctk.CTkFont(size=20, weight="bold"),
                     text_color=color).pack(pady=(15, 5))

        # Details frame
        frame = ctk.CTkFrame(self)
        frame.pack(padx=20, pady=10, fill="x")

        details = [
            ("Sembol:", symbol),
            ("Tip:", order_type),
            ("Fiyat:", f"{price:.6f}" if price else "Market"),
            ("Miktar:", f"{int(size)}"),
            ("Tutar:", f"{notional:.2f} USDT"),
        ]
        if tp_percent:
            details.append(("TP:", f"{tp_percent}%"))
        if sl_percent:
            details.append(("SL:", f"{sl_percent}%"))

        for label, value in details:
            row = ctk.CTkFrame(frame, fg_color="transparent")
            row.pack(fill="x", padx=10, pady=2)
            ctk.CTkLabel(row, text=label, width=80, anchor="w").pack(side="left")
            ctk.CTkLabel(row, text=value, anchor="w",
                         font=ctk.CTkFont(weight="bold")).pack(side="left")

        # Warning
        if notional > 50:
            ctk.CTkLabel(self, text="! Yuksek tutarli emir !",
                         text_color="orange",
                         font=ctk.CTkFont(weight="bold")).pack(pady=5)

        # Buttons
        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.pack(pady=15)

        ctk.CTkButton(btn_frame, text="Onayla", width=120,
                      fg_color=color, command=self._confirm).pack(side="left", padx=10)
        ctk.CTkButton(btn_frame, text="Iptal", width=120,
                      fg_color="gray", command=self._cancel).pack(side="left", padx=10)

    def _confirm(self) -> None:
        self._confirmed = True
        self.destroy()

    def _cancel(self) -> None:
        self._confirmed = False
        self.destroy()

    def show(self) -> bool:
        self.wait_window()
        return self._confirmed
