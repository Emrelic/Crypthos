"""Scanner Panel - shows scanner state, scan results, active position."""
import customtkinter as ctk
from tkinter import messagebox


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

        # Battle Mode toggle
        battle_on = self.controller.config.get("scanner.battle_mode", False)
        self._battle_var = ctk.BooleanVar(value=battle_on)
        self._battle_cb = ctk.CTkCheckBox(
            ctrl, text="Savas Modu", variable=self._battle_var,
            command=self._on_battle_toggle,
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color="#FF9800", fg_color="#FF9800", hover_color="#F57C00",
        )
        self._battle_cb.pack(side="right", padx=15)

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

        ctk.CTkLabel(table_frame, text="Tarama Sonuclari (Top 100)",
                     font=ctk.CTkFont(weight="bold")).pack(anchor="w", padx=5, pady=3)

        # Header
        hdr = ctk.CTkFrame(table_frame)
        hdr.pack(fill="x", padx=5)
        self._scan_headers = ["#", "Sembol", "Skor", "Yon", "Lev", "TF",
                              "Rejim", "Conf", "RSI", "ADX", "ATR%", "Red"]
        self._scan_widths = [25, 90, 50, 40, 35, 30, 65, 50, 40, 40, 45, 120]
        for h, w in zip(self._scan_headers, self._scan_widths):
            ctk.CTkLabel(hdr, text=h, width=w, font=ctk.CTkFont(size=10, weight="bold"),
                         text_color="gray").pack(side="left", padx=1)

        # Scrollable results
        self._results_scroll = ctk.CTkScrollableFrame(table_frame, height=250)
        self._results_scroll.pack(fill="both", expand=True, padx=5, pady=3)
        self._result_rows = []

        # === BOTTOM: Active Positions (all) ===
        pos_frame = ctk.CTkFrame(self)
        pos_frame.pack(fill="x", padx=10, pady=5)

        ctk.CTkLabel(pos_frame, text="Aktif Pozisyonlar",
                     font=ctk.CTkFont(weight="bold")).pack(anchor="w", padx=5)

        # Position table header - two rows for more data
        pos_hdr = ctk.CTkFrame(pos_frame)
        pos_hdr.pack(fill="x", padx=5)
        self._pos_headers = ["Sembol", "Yon", "Lev", "TF", "Skor", "Conf",
                             "RSI", "ADX", "Giris", "SL", "Emrgncy",
                             "Trail", "ROI%", "Sure", "Marjin"]
        self._pos_widths = [80, 30, 35, 30, 40, 40,
                            35, 35, 70, 70, 70,
                            70, 50, 50, 50]
        for h, w in zip(self._pos_headers, self._pos_widths):
            ctk.CTkLabel(pos_hdr, text=h, width=w,
                         font=ctk.CTkFont(size=9, weight="bold"),
                         text_color="gray").pack(side="left", padx=1)

        self._pos_scroll = ctk.CTkScrollableFrame(pos_frame, height=140)
        self._pos_scroll.pack(fill="x", padx=5, pady=3)
        self._pos_rows = []

    def _on_start(self) -> None:
        self.controller.start_scanner()

    def _on_stop(self) -> None:
        self.controller.stop_scanner()

    def _on_battle_toggle(self) -> None:
        enabled = self._battle_var.get()
        if enabled:
            confirm = messagebox.askyesno(
                "Savas Modu",
                "SAVAS MODU - Kanin Son Damlasina Kadar!\n\n"
                "Bu mod aktifken cikis stratejisi degisir:\n\n"
                "• Zarardayken: Sadece emergency close (likidasyon korumasi) calisir.\n"
                "  Baska hicbir sinyal pozisyonu kapatmaz. Olene kadar tut.\n\n"
                "• Fee breakeven - %50 ROI arasi: Sadece guclu sinyal\n"
                "  donusumunde satar (confluence <= -5.0)\n\n"
                "• %50+ ROI: Cok guclu donusum veya trailing stop\n"
                "  tetiklenirse satar, yoksa karda oturur.\n\n"
                "• Zaman limiti YOK, Take Profit YOK\n"
                "• Trailing: genis mesafe, kari kosturur\n\n"
                "Emin misiniz?",
            )
            if not confirm:
                self._battle_var.set(False)
                return
        self.controller.config.set("scanner.battle_mode", enabled)
        self.controller.config.save()

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

        # Show top 100
        widths = self._scan_widths
        for i, r in enumerate(results[:100]):
            row_frame = ctk.CTkFrame(self._results_scroll, fg_color="transparent")
            row_frame.pack(fill="x", pady=0)
            self._result_rows.append(row_frame)

            score_color = "#00C853" if r.score > 0 else "#FF1744" if r.score < 0 else "gray"
            dir_color = "#00C853" if r.direction == "LONG" else "#FF1744"
            eligible_marker = " *" if r.eligible else ""
            lev_str = f"{r.leverage}x" if r.leverage > 0 else "--"
            tf_str = getattr(r, 'timeframe', '1m')
            reject_short = r.reject_reason[:18] if r.reject_reason else ""

            # Highlight eligible rows
            row_color = score_color if r.eligible else "gray"

            vals = [
                (f"{i+1}", "gray"),
                (f"{r.symbol}{eligible_marker}", row_color),
                (f"{r.score:+.0f}", score_color),
                (r.direction[:1], dir_color),
                (lev_str, "#FF9800" if r.leverage >= 75 else "white"),
                (tf_str, "#2196F3"),
                (r.regime.get("regime", "?")[:5], "white"),
                (f"{r.confluence.get('score', 0):+.1f}", "white"),
                (f"{r.rsi:.0f}", "#FF9800" if r.rsi > 70 or r.rsi < 30 else "white"),
                (f"{r.adx:.0f}", "#00C853" if r.adx >= 25 else "gray"),
                (f"{r.atr_percent:.2f}", "white"),
                (reject_short, "#FF5252" if reject_short else "gray"),
            ]

            for (val, color), w in zip(vals, widths):
                ctk.CTkLabel(row_frame, text=val, width=w,
                             font=ctk.CTkFont(size=9),
                             text_color=color).pack(side="left", padx=1)

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
        positions = self.controller.get_all_scanner_positions()

        # Clear old rows
        for row in self._pos_rows:
            row.destroy()
        self._pos_rows.clear()

        if not positions:
            row_frame = ctk.CTkFrame(self._pos_scroll, fg_color="transparent")
            row_frame.pack(fill="x", pady=1)
            self._pos_rows.append(row_frame)
            ctk.CTkLabel(row_frame, text="Pozisyon yok",
                         text_color="gray", font=ctk.CTkFont(size=10)).pack(side="left", padx=10)
            return

        widths = self._pos_widths
        for pos in positions:
            row_frame = ctk.CTkFrame(self._pos_scroll, fg_color="transparent")
            row_frame.pack(fill="x", pady=1)
            self._pos_rows.append(row_frame)

            symbol = pos.get("symbol", "--")
            side = pos.get("side", "--")
            entry = pos.get("entry_price", 0)
            sl = pos.get("sl", 0)
            emergency = pos.get("emergency_price", 0)
            trailing = pos.get("trailing", 0)
            hold_sec = pos.get("hold_seconds", 0)
            lev = pos.get("leverage", 1)
            margin = pos.get("margin_usdt", 0)
            tf = pos.get("timeframe", "1m")
            score = pos.get("entry_score", 0)
            conf = pos.get("entry_confluence", 0)
            rsi = pos.get("entry_rsi", 50)
            adx = pos.get("entry_adx", 0)
            roi = pos.get("roi_percent", 0)

            fmt = ".6f" if entry < 1 else ".4f" if entry < 10 else ".2f"
            side_short = "L" if "Buy" in side else "S"
            side_color = "#00C853" if "Buy" in side else "#FF1744"
            trail_color = "#FF9800" if pos.get("trailing_active") else "gray"
            roi_color = "#00C853" if roi > 0 else "#FF1744" if roi < 0 else "white"
            mins = int(hold_sec // 60)
            secs = int(hold_sec % 60)

            vals = [
                (symbol, "white"),
                (side_short, side_color),
                (f"{lev}x", "#FF9800"),
                (tf, "#2196F3"),
                (f"{score:+.0f}", "#00C853" if score > 0 else "#FF1744"),
                (f"{conf:+.1f}", "white"),
                (f"{rsi:.0f}", "white"),
                (f"{adx:.0f}", "white"),
                (f"{entry:{fmt}}", "white"),
                (f"{sl:{fmt}}", "#FF1744"),
                (f"{emergency:{fmt}}", "#FF5252" if emergency > 0 else "gray"),
                (f"{trailing:{fmt}}", trail_color),
                (f"{roi:+.1f}%", roi_color),
                (f"{mins}m{secs:02d}s", "white"),
                (f"${margin:.2f}", "white"),
            ]

            for (val, color), w in zip(vals, widths):
                ctk.CTkLabel(row_frame, text=val, width=w,
                             font=ctk.CTkFont(size=9),
                             text_color=color).pack(side="left", padx=1)

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
