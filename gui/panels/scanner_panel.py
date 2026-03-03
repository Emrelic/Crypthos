"""Scanner Panel - shows scanner state, scan results, active position."""
import customtkinter as ctk


class ScannerPanel(ctk.CTkFrame):
    """GUI panel for the crypto scanner state machine."""

    STATE_COLORS = {
        "IDLE": "gray",
        "SCANNING": "#2196F3",
        "BUYING": "#FF9800",
        "HOLDING": "#00C853",
        "SELLING": "#FF1744",
        "COOLDOWN": "#9E9E9E",
    }

    def __init__(self, parent, controller):
        super().__init__(parent)
        self.controller = controller
        self.pack(fill="both", expand=True)
        self._build_ui()
        self._start_refresh()

    def _build_ui(self) -> None:
        # === TOP: Scanner Control ===
        ctrl = ctk.CTkFrame(self)
        ctrl.pack(fill="x", padx=10, pady=5)

        ctk.CTkLabel(ctrl, text="Kripto Tarayici",
                     font=ctk.CTkFont(size=16, weight="bold")).pack(side="left", padx=10)

        self._state_lbl = ctk.CTkLabel(
            ctrl, text="IDLE", font=ctk.CTkFont(size=14, weight="bold"),
            text_color="gray",
        )
        self._state_lbl.pack(side="left", padx=20)

        self._stop_btn = ctk.CTkButton(
            ctrl, text="DURDUR", fg_color="#FF1744", hover_color="#D50000",
            width=100, command=self._on_stop,
        )
        self._stop_btn.pack(side="right", padx=5)

        self._start_btn = ctk.CTkButton(
            ctrl, text="BASLAT", fg_color="#00C853", hover_color="#00A846",
            width=100, command=self._on_start,
        )
        self._start_btn.pack(side="right", padx=5)

        # Scan info
        info = ctk.CTkFrame(self)
        info.pack(fill="x", padx=10, pady=3)

        self._scan_count_lbl = ctk.CTkLabel(info, text="Tarama: 0",
                                             font=ctk.CTkFont(size=11))
        self._scan_count_lbl.pack(side="left", padx=10)

        self._candidate_lbl = ctk.CTkLabel(info, text="Aday: --",
                                            font=ctk.CTkFont(size=11, weight="bold"))
        self._candidate_lbl.pack(side="left", padx=20)

        self._trade_lbl = ctk.CTkLabel(info, text="Son islem: --",
                                        font=ctk.CTkFont(size=11))
        self._trade_lbl.pack(side="right", padx=10)

        # === MIDDLE: Scan Results Table ===
        table_frame = ctk.CTkFrame(self)
        table_frame.pack(fill="both", expand=True, padx=10, pady=5)

        ctk.CTkLabel(table_frame, text="Tarama Sonuclari (Top 20)",
                     font=ctk.CTkFont(weight="bold")).pack(anchor="w", padx=5, pady=3)

        # Header
        hdr = ctk.CTkFrame(table_frame)
        hdr.pack(fill="x", padx=5)
        headers = ["#", "Sembol", "Skor", "Yon", "Rejim", "Confluence", "RSI", "ADX", "ATR%"]
        widths = [30, 100, 60, 60, 80, 80, 50, 50, 50]
        for i, (h, w) in enumerate(zip(headers, widths)):
            ctk.CTkLabel(hdr, text=h, width=w, font=ctk.CTkFont(size=10, weight="bold"),
                         text_color="gray").pack(side="left", padx=2)

        # Scrollable results
        self._results_scroll = ctk.CTkScrollableFrame(table_frame, height=250)
        self._results_scroll.pack(fill="both", expand=True, padx=5, pady=3)
        self._result_rows = []

        # === BOTTOM: Active Position ===
        pos_frame = ctk.CTkFrame(self)
        pos_frame.pack(fill="x", padx=10, pady=5)

        ctk.CTkLabel(pos_frame, text="Aktif Pozisyon",
                     font=ctk.CTkFont(weight="bold")).pack(anchor="w", padx=5)

        pos_grid = ctk.CTkFrame(pos_frame)
        pos_grid.pack(fill="x", padx=5, pady=5)

        self._pos_labels = {}
        pos_fields = ["Sembol", "Yon", "Giris", "Fiyat", "PnL", "PnL%",
                       "SL", "TP", "Trailing", "Sure"]
        for i, name in enumerate(pos_fields):
            row, col = divmod(i, 5)
            f = ctk.CTkFrame(pos_grid, fg_color="transparent")
            f.grid(row=row, column=col, padx=8, pady=2, sticky="w")
            ctk.CTkLabel(f, text=f"{name}:", text_color="gray",
                         font=ctk.CTkFont(size=10)).pack(side="left")
            lbl = ctk.CTkLabel(f, text="--", font=ctk.CTkFont(size=10, weight="bold"))
            lbl.pack(side="left", padx=3)
            self._pos_labels[name] = lbl

    def _on_start(self) -> None:
        self.controller.start_scanner()

    def _on_stop(self) -> None:
        self.controller.stop_scanner()

    def _start_refresh(self) -> None:
        self._refresh()

    def _refresh(self) -> None:
        """Periodic refresh of scanner data."""
        try:
            self._update_state()
            self._update_results()
            self._update_position()
            self._update_trade()
        except Exception:
            pass
        self.after(2000, self._refresh)

    def _update_state(self) -> None:
        state = self.controller.get_scanner_state()
        color = self.STATE_COLORS.get(state, "gray")
        self._state_lbl.configure(text=state, text_color=color)
        self._scan_count_lbl.configure(
            text=f"Tarama: {self.controller.get_scanner_scan_count()}"
        )

    def _update_results(self) -> None:
        results = self.controller.get_scan_results()
        if not results:
            return

        # Clear old rows
        for row in self._result_rows:
            row.destroy()
        self._result_rows.clear()

        # Show top 20
        widths = [30, 100, 60, 60, 80, 80, 50, 50, 50]
        for i, r in enumerate(results[:20]):
            row_frame = ctk.CTkFrame(self._results_scroll, fg_color="transparent")
            row_frame.pack(fill="x", pady=1)
            self._result_rows.append(row_frame)

            score_color = "#00C853" if r.score > 0 else "#FF1744" if r.score < 0 else "white"
            eligible_marker = "*" if r.eligible else ""

            vals = [
                f"{i+1}",
                f"{r.symbol}{eligible_marker}",
                f"{r.score:+.0f}",
                r.direction,
                r.regime.get("regime", "?")[:6],
                f"{r.confluence.get('score', 0):+.1f}",
                f"{r.rsi:.0f}",
                f"{r.adx:.0f}",
                f"{r.atr_percent:.1f}",
            ]
            colors = [
                "white", score_color, score_color,
                "#00C853" if r.direction == "LONG" else "#FF1744",
                "white", "white", "white", "white", "white",
            ]

            for val, w, c in zip(vals, widths, colors):
                ctk.CTkLabel(row_frame, text=val, width=w,
                             font=ctk.CTkFont(size=10),
                             text_color=c).pack(side="left", padx=2)

        # Update candidate
        candidate = self.controller.get_scanner_candidate()
        if candidate:
            self._candidate_lbl.configure(
                text=f"Aday: {candidate.symbol} ({candidate.score:+.0f})",
                text_color="#00C853" if candidate.score > 0 else "#FF1744",
            )
        else:
            self._candidate_lbl.configure(text="Aday: --", text_color="gray")

    def _update_position(self) -> None:
        pos = self.controller.get_scanner_position()
        if not pos:
            for lbl in self._pos_labels.values():
                lbl.configure(text="--", text_color="white")
            return

        symbol = pos.get("symbol", "--")
        side = pos.get("side", "--")
        entry = pos.get("entry_price", 0)
        sl = pos.get("sl", 0)
        tp = pos.get("tp", 0)
        trailing = pos.get("trailing", 0)
        hold_sec = pos.get("hold_seconds", 0)

        fmt = ".6f" if entry < 1 else ".2f"

        self._pos_labels["Sembol"].configure(text=symbol)
        side_color = "#00C853" if "Buy" in side else "#FF1744"
        self._pos_labels["Yon"].configure(text=side, text_color=side_color)
        self._pos_labels["Giris"].configure(text=f"{entry:{fmt}}")
        self._pos_labels["SL"].configure(text=f"{sl:{fmt}}", text_color="#FF1744")
        self._pos_labels["TP"].configure(text=f"{tp:{fmt}}", text_color="#00C853")

        trail_color = "#FF9800" if pos.get("trailing_active") else "gray"
        self._pos_labels["Trailing"].configure(text=f"{trailing:{fmt}}",
                                                text_color=trail_color)

        mins = int(hold_sec // 60)
        secs = int(hold_sec % 60)
        self._pos_labels["Sure"].configure(text=f"{mins}m{secs:02d}s")

        # PnL will be updated via events or price refresh
        self._pos_labels["Fiyat"].configure(text="...")
        self._pos_labels["PnL"].configure(text="...")
        self._pos_labels["PnL%"].configure(text="...")

    def _update_trade(self) -> None:
        trade = self.controller.get_last_trade()
        if trade:
            pnl = trade.get("pnl_usdt", 0)
            symbol = trade.get("symbol", "?")
            reason = trade.get("exit_reason", "?")
            pnl_color = "#00C853" if pnl >= 0 else "#FF1744"
            self._trade_lbl.configure(
                text=f"Son: {symbol} {pnl:+.4f}$ ({reason})",
                text_color=pnl_color,
            )
