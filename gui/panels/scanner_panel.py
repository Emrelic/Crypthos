"""Scanner Panel - shows scanner state, scan results, active position.
All 12 confluence indicators shown with AL/SAT/TUT signals."""
import math
import customtkinter as ctk
from tkinter import messagebox


# 12 confluence indicators in order (must match confluence.py)
CONF_INDICATORS = [
    "RSI", "StochRSI", "MFI",           # Momentum
    "MACD", "ADX", "Supertrend",         # Trend
    "PSAR", "Ichimoku",                  # Trend
    "BB",                                 # Volatility
    "OBV", "CMF", "Price_vs_SMA200",     # Volume & MA
]
CONF_SHORT = [
    "RSI", "StR", "MFI",
    "MACD", "ADX", "ST",
    "PSAR", "Ichi",
    "BB",
    "OBV", "CMF", "SMA",
]

# Map confluence key -> indicator_values key for showing numerical values
CONF_VALUE_KEYS = {
    "RSI": "RSI",
    "StochRSI": "StochRSI_K",
    "MFI": "MFI",
    "MACD": "MACD_histogram",
    "ADX": "ADX",
    "Supertrend": None,        # trend string, no number
    "PSAR": None,              # trend string, no number
    "Ichimoku": None,          # position string, no number
    "BB": "BB_PercentB",
    "OBV": "OBV_slope",
    "CMF": "CMF",
    "Price_vs_SMA200": None,   # derived
}


def _conf_detail_cell(score: float, raw_val=None) -> tuple[str, str]:
    """Convert a single indicator's confluence score to (label, color).
    Score range: -2.0 to +2.0 from confluence scorer.
    If raw_val provided, show score+value like 'AL 28'."""
    if score > 0.5:
        signal, color = "AL", "#00C853"
    elif score > 0:
        signal, color = "al", "#81C784"
    elif score < -0.5:
        signal, color = "SAT", "#FF1744"
    elif score < 0:
        signal, color = "sat", "#EF5350"
    else:
        signal, color = "--", "#555555"

    if raw_val is not None and raw_val != 0:
        if isinstance(raw_val, float):
            if abs(raw_val) >= 100:
                val_str = f"{raw_val:.0f}"
            elif abs(raw_val) >= 1:
                val_str = f"{raw_val:.1f}"
            else:
                val_str = f"{raw_val:.2f}"
        else:
            val_str = str(raw_val)
        return (f"{signal}{val_str}", color)
    return (signal, color)


def _system_signal_candidate(r) -> tuple[str, str]:
    """System's overall verdict for a candidate coin."""
    if not r.eligible:
        return ("BEKLE", "gray")
    score = abs(r.score)
    if score >= 70:
        if r.direction == "LONG":
            return ("AL!", "#00E676")
        else:
            return ("SAT!", "#FF1744")
    elif score >= 55:
        if r.direction == "LONG":
            return ("AL", "#00C853")
        else:
            return ("SAT", "#EF5350")
    else:
        return ("BEKLE", "gray")


def _system_signal_position(pos_side: str, confluence: dict) -> tuple[str, str]:
    """System's overall verdict for a held position."""
    if not confluence:
        return ("--", "gray")

    conf_score = confluence.get("score", 0)
    conf_signal = confluence.get("signal", "NEUTRAL")
    bullish = confluence.get("bullish_count", 0)
    bearish = confluence.get("bearish_count", 0)
    is_long = "Buy" in pos_side

    if is_long:
        if conf_score <= -4.0 or conf_signal == "SELL":
            return ("CIK!", "#FF1744")
        elif conf_score <= -2.0 or bearish >= 8:
            return ("DIKKAT", "#FF9800")
        elif conf_score >= 2.0 and bullish >= 6:
            return ("TUT", "#00C853")
        elif conf_score >= 0:
            return ("TUT", "#81C784")
        else:
            return ("DIKKAT", "#FF9800")
    else:
        if conf_score >= 4.0 or conf_signal == "BUY":
            return ("CIK!", "#FF1744")
        elif conf_score >= 2.0 or bullish >= 8:
            return ("DIKKAT", "#FF9800")
        elif conf_score <= -2.0 and bearish >= 6:
            return ("TUT", "#00C853")
        elif conf_score <= 0:
            return ("TUT", "#81C784")
        else:
            return ("DIKKAT", "#FF9800")


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
                     font=ctk.CTkFont(size=18, weight="bold")).pack(side="left", padx=10)

        self._state_lbl = ctk.CTkLabel(
            ctrl, text="IDLE", font=ctk.CTkFont(size=16, weight="bold"),
            text_color="gray",
        )
        self._state_lbl.pack(side="left", padx=20)

        self._stop_btn = ctk.CTkButton(
            ctrl, text="DURDUR", fg_color="#FF1744", hover_color="#D50000",
            width=110, font=ctk.CTkFont(size=13, weight="bold"),
            command=self._on_stop,
        )
        self._stop_btn.pack(side="right", padx=5)

        self._start_btn = ctk.CTkButton(
            ctrl, text="BASLAT", fg_color="#00C853", hover_color="#00A846",
            width=110, font=ctk.CTkFont(size=13, weight="bold"),
            command=self._on_start,
        )
        self._start_btn.pack(side="right", padx=5)

        # Battle Mode toggle
        battle_on = self.controller.config.get("scanner.battle_mode", False)
        self._battle_var = ctk.BooleanVar(value=battle_on)
        self._battle_cb = ctk.CTkCheckBox(
            ctrl, text="Savas Modu", variable=self._battle_var,
            command=self._on_battle_toggle,
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color="#FF9800", fg_color="#FF9800", hover_color="#F57C00",
        )
        self._battle_cb.pack(side="right", padx=15)

        # Scan info
        info = ctk.CTkFrame(self)
        info.pack(fill="x", padx=10, pady=3)

        self._scan_count_lbl = ctk.CTkLabel(info, text="Tarama: 0",
                                             font=ctk.CTkFont(size=13))
        self._scan_count_lbl.pack(side="left", padx=10)

        self._candidate_lbl = ctk.CTkLabel(info, text="Aday: --",
                                            font=ctk.CTkFont(size=13, weight="bold"))
        self._candidate_lbl.pack(side="left", padx=20)

        self._trade_lbl = ctk.CTkLabel(info, text="Son islem: --",
                                        font=ctk.CTkFont(size=13))
        self._trade_lbl.pack(side="right", padx=10)

        # === MIDDLE: Scan Results Table ===
        table_frame = ctk.CTkFrame(self)
        table_frame.pack(fill="both", expand=True, padx=10, pady=5)

        ctk.CTkLabel(table_frame, text="Tarama Sonuclari (Top 100)",
                     font=ctk.CTkFont(size=14, weight="bold")).pack(anchor="w", padx=5, pady=3)

        # Header
        hdr = ctk.CTkFrame(table_frame)
        hdr.pack(fill="x", padx=5)
        self._scan_headers = (
            ["#", "Sinyal", "Sembol", "Skor", "Lev", "TF"] +
            CONF_SHORT +
            ["Conf", "AL", "SAT", "Red"]
        )
        self._scan_widths = (
            [28, 52, 96, 48, 40, 38] +
            [66] * 12 +
            [50, 28, 28, 100]
        )
        for h, w in zip(self._scan_headers, self._scan_widths):
            ctk.CTkLabel(hdr, text=h, width=w, font=ctk.CTkFont(size=13, weight="bold"),
                         text_color="gray").pack(side="left", padx=1)

        # Scrollable results
        self._results_scroll = ctk.CTkScrollableFrame(table_frame, height=350)
        self._results_scroll.pack(fill="both", expand=True, padx=5, pady=3)
        self._result_rows = []   # list of (frame, [labels])
        self._result_cache = []  # list of [(text, color), ...] per row — skip if same

        # === BOTTOM: Active Positions ===
        pos_frame = ctk.CTkFrame(self)
        pos_frame.pack(fill="x", padx=10, pady=5)

        ctk.CTkLabel(pos_frame, text="Aktif Pozisyonlar",
                     font=ctk.CTkFont(size=14, weight="bold")).pack(anchor="w", padx=5)

        pos_hdr = ctk.CTkFrame(pos_frame)
        pos_hdr.pack(fill="x", padx=5)
        self._pos_headers = (
            ["Sinyal", "Sembol", "Yon", "Lev", "TF"] +
            CONF_SHORT +
            ["Conf", "AL", "SAT",
             "ROI%", "SL Uzk", "Acil",
             "ATR%", "7xATR%", "AktROI%", "Kar/7", "GeriATR%", "Trail", "Kalan", "$"]
        )
        self._pos_widths = (
            [52, 86, 28, 40, 36] +
            [66] * 12 +
            [48, 26, 26,
             56, 52, 52,
             52, 60, 60, 52, 60, 60, 56, 48]
        )
        for h, w in zip(self._pos_headers, self._pos_widths):
            ctk.CTkLabel(pos_hdr, text=h, width=w,
                         font=ctk.CTkFont(size=13, weight="bold"),
                         text_color="gray").pack(side="left", padx=1)

        self._pos_scroll = ctk.CTkScrollableFrame(pos_frame, height=180)
        self._pos_scroll.pack(fill="x", padx=5, pady=3)
        self._pos_rows = []    # list of (frame, [labels])
        self._pos_cache = []   # list of [(text, color), ...] per row

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
                "- Zarardayken: Sadece emergency close calisir.\n"
                "- Fee breakeven - %50 ROI: Guclu sinyal donusumunde satar\n"
                "- %50+ ROI: Cok guclu donusum veya trailing stop tetiklenirse satar\n"
                "- Zaman limiti YOK, Take Profit YOK\n\n"
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

    def _ensure_scan_rows(self, count: int) -> None:
        """Ensure exactly `count` rows exist in the scan results table."""
        widths = self._scan_widths
        font = ctk.CTkFont(size=12)
        # Remove excess rows
        while len(self._result_rows) > count:
            frame, labels = self._result_rows.pop()
            frame.destroy()
        if len(self._result_cache) > count:
            self._result_cache = self._result_cache[:count]
        # Add missing rows
        while len(self._result_rows) < count:
            row_frame = ctk.CTkFrame(self._results_scroll, fg_color="transparent")
            row_frame.pack(fill="x", pady=0)
            labels = []
            for w in widths:
                lbl = ctk.CTkLabel(row_frame, text="", width=w,
                                   font=font, text_color="gray")
                lbl.pack(side="left", padx=1)
                labels.append(lbl)
            self._result_rows.append((row_frame, labels))
            self._result_cache.append(None)

    def _update_results(self) -> None:
        results = self.controller.get_scan_results()
        if not results:
            return

        n = min(len(results), 100)
        self._ensure_scan_rows(n)

        for i, r in enumerate(results[:n]):
            score_color = "#00C853" if r.score > 0 else "#FF1744" if r.score < 0 else "gray"
            eligible_marker = "*" if r.eligible else ""
            lev_str = f"{r.leverage}x" if r.leverage > 0 else "--"
            tf_str = getattr(r, 'timeframe', '1m')
            reject_short = r.reject_reason[:12] if r.reject_reason else ""
            row_color = score_color if r.eligible else "gray"

            sys_signal = _system_signal_candidate(r)

            details = r.confluence.get("details", {}) if r.confluence else {}
            bullish = r.confluence.get("bullish_count", 0) if r.confluence else 0
            bearish = r.confluence.get("bearish_count", 0) if r.confluence else 0
            conf_total = r.confluence.get("score", 0) if r.confluence else 0

            raw_ind = getattr(r, 'indicator_values', {}) or {}
            ind_cells = []
            for key in CONF_INDICATORS:
                vk = CONF_VALUE_KEYS.get(key)
                raw = raw_ind.get(vk) if vk else None
                ind_cells.append(_conf_detail_cell(details.get(key, 0), raw))

            vals = [
                (f"{i+1}", "gray"),
                sys_signal,
                (f"{r.symbol}{eligible_marker}", row_color),
                (f"{r.score:+.0f}", score_color),
                (lev_str, "#FF9800" if r.leverage >= 75 else "white"),
                (tf_str, "#2196F3"),
            ] + ind_cells + [
                (f"{conf_total:+.1f}",
                 "#00C853" if conf_total >= 4 else "#FF1744" if conf_total <= -4 else "white"),
                (f"{bullish}", "#00C853" if bullish > 0 else "gray"),
                (f"{bearish}", "#FF1744" if bearish > 0 else "gray"),
                (reject_short, "#FF5252" if reject_short else "gray"),
            ]

            # Only update labels whose text or color actually changed
            if self._result_cache[i] == vals:
                continue
            self._result_cache[i] = vals
            _, labels = self._result_rows[i]
            for lbl, (val, color) in zip(labels, vals):
                lbl.configure(text=val, text_color=color)

        # Update candidate
        candidate = self.controller.get_scanner_candidate()
        if candidate:
            self._candidate_lbl.configure(
                text=f"Aday: {candidate.symbol} ({candidate.score:+.0f})",
                text_color="#00C853" if candidate.score > 0 else "#FF1744",
            )
        else:
            self._candidate_lbl.configure(text="Aday: --", text_color="gray")

    def _ensure_pos_rows(self, count: int) -> None:
        """Ensure exactly `count` rows exist in positions table."""
        widths = self._pos_widths
        font = ctk.CTkFont(size=12)
        while len(self._pos_rows) > count:
            frame, labels = self._pos_rows.pop()
            frame.destroy()
        if len(self._pos_cache) > count:
            self._pos_cache = self._pos_cache[:count]
        while len(self._pos_rows) < count:
            row_frame = ctk.CTkFrame(self._pos_scroll, fg_color="transparent")
            row_frame.pack(fill="x", pady=1)
            labels = []
            for w in widths:
                lbl = ctk.CTkLabel(row_frame, text="", width=w,
                                   font=font, text_color="gray")
                lbl.pack(side="left", padx=1)
                labels.append(lbl)
            self._pos_rows.append((row_frame, labels))
            self._pos_cache.append(None)

    def _update_position(self) -> None:
        positions = self.controller.get_all_scanner_positions()

        if not positions:
            # Show "no position" using 1 row with first label
            self._ensure_pos_rows(1)
            empty_vals = [("Pozisyon yok", "gray")] + [("", "gray")] * (len(self._pos_widths) - 1)
            if self._pos_cache[0] != empty_vals:
                self._pos_cache[0] = empty_vals
                _, labels = self._pos_rows[0]
                for lbl, (val, color) in zip(labels, empty_vals):
                    lbl.configure(text=val, text_color=color)
            return

        held_ind = self.controller.get_held_indicators()
        n = len(positions)
        self._ensure_pos_rows(n)

        for idx, pos in enumerate(positions):
            symbol = pos.get("symbol", "--")
            side = pos.get("side", "--")
            entry = pos.get("entry_price", 0)
            sl = pos.get("sl", 0)
            trailing = pos.get("trailing", 0)
            hold_sec = pos.get("hold_seconds", 0)
            lev = pos.get("leverage", 1)
            margin = pos.get("margin_usdt", 0)
            tf = pos.get("timeframe", "1m")
            roi = pos.get("roi_percent", 0)
            emergency = pos.get("emergency_price", 0)
            atr = pos.get("atr_at_entry", 0)
            is_long = "Buy" in side

            sym_ind = held_ind.get(symbol, {})
            cur_confluence = sym_ind.get("confluence", {})
            cur_price = sym_ind.get("price", 0)
            if cur_price <= 0:
                cur_price = entry

            details = cur_confluence.get("details", {})
            bullish = cur_confluence.get("bullish_count", 0)
            bearish = cur_confluence.get("bearish_count", 0)
            conf_total = cur_confluence.get("score", pos.get("entry_confluence", 0))

            raw_ind = sym_ind.get("indicators", {})
            ind_cells = []
            for key in CONF_INDICATORS:
                vk = CONF_VALUE_KEYS.get(key)
                raw = raw_ind.get(vk) if vk else None
                ind_cells.append(_conf_detail_cell(details.get(key, 0), raw))

            sys_signal = _system_signal_position(side, cur_confluence)

            # === EXIT PARAMETER CALCULATIONS ===
            if sl > 0 and cur_price > 0:
                sl_dist = ((cur_price - sl) / cur_price * 100) if is_long else \
                          ((sl - cur_price) / cur_price * 100)
            else:
                sl_dist = 99
            sl_color = "#FF1744" if sl_dist < 0.1 else "#FF9800" if sl_dist < 0.2 else "#00C853"

            if emergency > 0 and cur_price > 0:
                em_dist = ((cur_price - emergency) / cur_price * 100) if is_long else \
                          ((emergency - cur_price) / cur_price * 100)
            else:
                em_dist = 99
            em_color = "#FF1744" if em_dist < 0.1 else "#FF9800" if em_dist < 0.3 else "gray"

            strat = self.controller.config.get("strategy", {})
            atr_activate = strat.get("trailing_atr_activate_mult", 7.0)
            atr_distance = strat.get("trailing_atr_distance_mult", 1.0)

            # ATR yuzde ve trailing hesaplamalari
            atr_pct = pos.get("atr_pct", 0)
            trailing_activate_pct = pos.get("trailing_activate_pct", 0)
            trailing_activate_roi = pos.get("trailing_activate_roi", 0)
            trailing_distance_pct = pos.get("trailing_distance_pct", 0)

            if atr > 0 and cur_price > 0:
                profit_atr = ((cur_price - entry) / atr) if is_long else ((entry - cur_price) / atr)
            else:
                profit_atr = 0

            # ATR% sutunu
            atr_pct_color = "#2196F3"
            # 7xATR% sutunu (gereken fiyat hareketi)
            act_pct_color = "#FF9800"
            # Aktivasyon ROI% (gereken ROI)
            act_roi_color = "#FF9800" if trailing_activate_roi > 50 else "#00C853"
            # Kar/7 (mevcut profit_atr / hedef)
            kar7_str = f"{profit_atr:.1f}/{atr_activate:.0f}"
            kar7_color = "#00C853" if profit_atr >= atr_activate else \
                         "#81C784" if profit_atr >= atr_activate * 0.5 else \
                         "#FF9800" if profit_atr >= 0 else "#FF1744"
            # Geri gelme ATR%
            geri_color = "#2196F3"

            if pos.get("trailing_active") and cur_price > 0:
                if is_long:
                    trail_dist = (cur_price - trailing) / cur_price * 100 if trailing > 0 else 0
                else:
                    trail_dist = (trailing - cur_price) / cur_price * 100 if trailing > 0 else 0
                trail_str = f"{trail_dist:.2f}%"
                trail_color = "#FF9800" if trail_dist < 0.15 else "#00C853"
            else:
                trail_str = "bekle"
                trail_color = "gray"

            time_limit = strat.get("time_limit_minutes", 480) * 60
            remaining = max(0, time_limit - hold_sec)
            rem_h = int(remaining // 3600)
            rem_m = int((remaining % 3600) // 60)
            if strat.get("time_limit_enabled", True):
                time_str = f"{rem_h}s{rem_m:02d}d"
                time_color = "#FF1744" if remaining < 600 else "#FF9800" if remaining < 1800 else "white"
            else:
                time_str = "--"
                time_color = "gray"

            side_short = "L" if is_long else "S"
            side_color = "#00C853" if is_long else "#FF1744"
            roi_color = "#00C853" if roi > 0 else "#FF1744" if roi < 0 else "white"
            conf_color = "#00C853" if conf_total >= 4 else "#FF1744" if conf_total <= -4 else "white"

            vals = [
                sys_signal,
                (symbol, "white"),
                (side_short, side_color),
                (f"{lev}x", "#FF9800"),
                (tf, "#2196F3"),
            ] + ind_cells + [
                (f"{conf_total:+.1f}", conf_color),
                (f"{bullish}", "#00C853" if bullish > 0 else "gray"),
                (f"{bearish}", "#FF1744" if bearish > 0 else "gray"),
                (f"{roi:+.1f}%", roi_color),
                (f"{sl_dist:.1f}%", sl_color),
                (f"{em_dist:.1f}%", em_color),
                (f"{atr_pct:.2f}%", atr_pct_color),
                (f"{trailing_activate_pct:.1f}%", act_pct_color),
                (f"{trailing_activate_roi:.0f}%", act_roi_color),
                (kar7_str, kar7_color),
                (f"{trailing_distance_pct:.2f}%", geri_color),
                (trail_str, trail_color),
                (time_str, time_color),
                (f"${margin:.1f}", "white"),
            ]

            # Only update labels that changed
            if self._pos_cache[idx] == vals:
                continue
            self._pos_cache[idx] = vals
            _, labels = self._pos_rows[idx]
            for lbl, (val, color) in zip(labels, vals):
                lbl.configure(text=val, text_color=color)

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
