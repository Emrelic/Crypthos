"""Scanner Panel - shows scanner state, scan results, active position.
All 11 confluence indicators shown with AL/SAT/TUT signals."""
import math
import customtkinter as ctk
from tkinter import messagebox


# Confluence indicators grouped by philosophy (must match confluence.py)
# TREND group → MEAN-REV group → VOLUME group (decision flow order)
CONF_TREND = ["MACD", "ADX", "EMA50", "Price_vs_SMA", "SR"]
CONF_REVERSION = ["RSI", "BB"]
CONF_VOLUME = ["OBV", "CMF", "CVD", "VWAP"]
CONF_INDICATORS = CONF_TREND + CONF_REVERSION + CONF_VOLUME

CONF_SHORT_MAP = {
    "MACD": "MACD", "ADX": "ADX", "EMA50": "EMA50", "Price_vs_SMA": "SMA", "SR": "S/R",
    "RSI": "RSI", "BB": "BB",
    "OBV": "OBV", "CMF": "CMF", "CVD": "CVD", "VWAP": "VWAP",
}
CONF_SHORT = [CONF_SHORT_MAP[k] for k in CONF_INDICATORS]

# Header colors per group
CONF_HDR_COLORS = {}
for k in CONF_TREND:
    CONF_HDR_COLORS[CONF_SHORT_MAP[k]] = "#4FC3F7"   # blue = trend
for k in CONF_REVERSION:
    CONF_HDR_COLORS[CONF_SHORT_MAP[k]] = "#CE93D8"   # purple = mean-rev
for k in CONF_VOLUME:
    CONF_HDR_COLORS[CONF_SHORT_MAP[k]] = "#FFD54F"   # yellow = volume

# Map confluence key -> indicator_values key for showing numerical values
CONF_VALUE_KEYS = {
    "MACD": "MACD_histogram",
    "ADX": "ADX",
    "EMA50": None,              # cross signal
    "Price_vs_SMA": None,       # derived
    "SR": None,                 # position string
    "RSI": "RSI",
    "BB": "BB_PercentB",
    "OBV": "OBV_slope",
    "CMF": "CMF",
    "CVD": "CVD_normalized",
    "VWAP": "VWAP",
}


def _conf_detail_cell(score: float, raw_val=None) -> tuple[str, str]:
    """Convert a single indicator's confluence score to (label, color).
    Score range: -2.0 to +2.0 from confluence scorer.
    If raw_val provided, show signal on top, value below."""
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
        if isinstance(raw_val, (int, float)):
            abs_val = abs(raw_val)
            if abs_val >= 1_000_000:
                val_str = f"{raw_val / 1_000_000:.1f}M"
            elif abs_val >= 10_000:
                val_str = f"{raw_val / 1_000:.0f}K"
            elif abs_val >= 1_000:
                val_str = f"{raw_val / 1_000:.1f}K"
            elif abs_val >= 100:
                val_str = f"{raw_val:.0f}"
            elif abs_val >= 1:
                val_str = f"{raw_val:.1f}"
            else:
                val_str = f"{raw_val:.2f}"
        else:
            val_str = str(raw_val)
        return (f"{signal}\n{val_str}", color)
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
        battle_on = self.controller.config.get("strategy.battle_mode", False)
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
            ["#", "Sinyal", "Sembol", "Skor", "ATR%", "Lev", "TF", "Fnd", "OI%", "OB"] +
            CONF_SHORT +
            ["Conf", "AL", "SAT", "Red"]
        )
        # Pre-indicator: 30+58+110+48+48+42+38+36+36+36 = 482
        # Indicators (decision flow): MACD,ADX,EMA50,SMA,S/R | RSI,BB | OBV,CMF,CVD,VWAP
        self._scan_widths = (
            [30, 58, 110, 48, 48, 42, 38, 36, 36, 36] +
            [58, 56, 58, 56, 56, 58, 56, 58, 56, 56, 56] +
            [52, 30, 30, 48]
        )
        for h, w in zip(self._scan_headers, self._scan_widths):
            hdr_color = CONF_HDR_COLORS.get(h, "#7799BB")
            ctk.CTkLabel(hdr, text=h, width=w, font=ctk.CTkFont(size=13, weight="bold"),
                         text_color=hdr_color).pack(side="left", padx=1)

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
            ["Sinyal", "Sembol", "ROI%", "ATR%", "Lev", "TF", "Fnd", "OI%", "OB"] +
            CONF_SHORT +
            ["Conf", "AL", "SAT",
             "SL%", "Acil",
             "7xATR%", "AktROI%", "Kar/7", "Geri%", "Trail", "Kalan", "$"]
        )
        # Pre-indicator total: 88+110+48+48+42+38+36+36+36 = 482 (matches scan table)
        # Indicators (decision flow): MACD,ADX,EMA50,SMA,S/R | RSI,BB | OBV,CMF,CVD,VWAP
        self._pos_widths = (
            [88, 110, 48, 48, 42, 38, 36, 36, 36] +
            [58, 56, 58, 56, 56, 58, 56, 58, 56, 56, 56] +
            [48, 28, 28,
             44, 44,
             50, 50, 46, 46, 46, 46, 40]
        )
        for h, w in zip(self._pos_headers, self._pos_widths):
            hdr_color = CONF_HDR_COLORS.get(h, "#7799BB")
            ctk.CTkLabel(pos_hdr, text=h, width=w,
                         font=ctk.CTkFont(size=13, weight="bold"),
                         text_color=hdr_color).pack(side="left", padx=1)

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
        self.controller.config.set("strategy.battle_mode", enabled)
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
        font = ctk.CTkFont(size=13, weight="bold")
        # Remove excess rows
        while len(self._result_rows) > count:
            frame, labels = self._result_rows.pop()
            frame.destroy()
        if len(self._result_cache) > count:
            self._result_cache = self._result_cache[:count]
        # Add missing rows
        while len(self._result_rows) < count:
            idx = len(self._result_rows)
            bg = "#1c2d4d" if idx % 2 == 0 else "transparent"
            row_frame = ctk.CTkFrame(self._results_scroll, fg_color=bg)
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
            active_grp = r.confluence.get("active_group", "") if r.confluence else ""

            raw_ind = getattr(r, 'indicator_values', {}) or {}
            ind_cells = []
            for key in CONF_INDICATORS:
                vk = CONF_VALUE_KEYS.get(key)
                raw = raw_ind.get(vk) if vk else None
                ind_cells.append(_conf_detail_cell(details.get(key, 0), raw))

            atr_pct = getattr(r, 'atr_percent', 0) or 0
            atr_color = "#FF9800" if atr_pct > 0.5 else "#2196F3" if atr_pct > 0 else "gray"

            # Funding rate display
            fr = getattr(r, 'funding_rate', 0) or 0
            fr_pct = fr * 100  # 0.0001 -> 0.01%
            if abs(fr_pct) >= 0.05:
                fr_color = "#FF1744" if fr_pct > 0 else "#00C853"
            else:
                fr_color = "gray"
            fr_str = f"{fr_pct:+.2f}" if fr != 0 else "--"

            # Open interest change display
            oi_chg = getattr(r, 'oi_change_pct', 0) or 0
            if oi_chg > 2:
                oi_color = "#00C853"
            elif oi_chg < -2:
                oi_color = "#FF1744"
            else:
                oi_color = "gray"
            oi_str = f"{oi_chg:+.1f}" if oi_chg != 0 else "--"

            # Order book imbalance display
            ob_imb = getattr(r, 'ob_imbalance', 0) or 0
            ob_thin = getattr(r, 'ob_thin_book', False)
            if ob_thin:
                ob_str, ob_color = "X", "#FF1744"
            elif ob_imb > 0.25:
                ob_str, ob_color = f"+{ob_imb:.1f}", "#00C853"
            elif ob_imb < -0.25:
                ob_str, ob_color = f"{ob_imb:.1f}", "#FF1744"
            elif ob_imb != 0:
                ob_str, ob_color = f"{ob_imb:+.1f}", "gray"
            else:
                ob_str, ob_color = "--", "gray"

            # Active group indicator for Conf column
            grp_tag = {"TREND": "T", "REVERSION": "R", "BOTH": "B",
                       "CONFLICT": "!", "NEUTRAL": "-"}.get(active_grp, "")
            conf_str = f"{conf_total:+.1f}{grp_tag}"
            if active_grp == "CONFLICT":
                conf_color = "#FF9800"  # orange = conflict, skipped
            elif conf_total >= 4:
                conf_color = "#00C853"
            elif conf_total <= -4:
                conf_color = "#FF1744"
            else:
                conf_color = "white"

            vals = [
                (f"{i+1}", "gray"),
                sys_signal,
                (f"{r.symbol}{eligible_marker}", row_color),
                (f"{r.score:+.0f}", score_color),
                (f"{atr_pct:.2f}" if atr_pct > 0 else "--", atr_color),
                (lev_str, "#FF9800" if r.leverage >= 75 else "white"),
                (tf_str, "#2196F3"),
                (fr_str, fr_color),
                (oi_str, oi_color),
                (ob_str, ob_color),
            ] + ind_cells + [
                (conf_str, conf_color),
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
        font = ctk.CTkFont(size=13, weight="bold")
        while len(self._pos_rows) > count:
            frame, labels = self._pos_rows.pop()
            frame.destroy()
        if len(self._pos_cache) > count:
            self._pos_cache = self._pos_cache[:count]
        while len(self._pos_rows) < count:
            idx = len(self._pos_rows)
            bg = "#1c2d4d" if idx % 2 == 0 else "transparent"
            row_frame = ctk.CTkFrame(self._pos_scroll, fg_color=bg)
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
            atr_activate = strat.get("trailing_atr_activate_mult", 4.0)
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
            side_arrow = "\u25B2" if is_long else "\u25BC"  # ▲ or ▼
            roi_color = "#00C853" if roi > 0 else "#FF1744" if roi < 0 else "white"
            conf_color = "#00C853" if conf_total >= 4 else "#FF1744" if conf_total <= -4 else "white"

            # Merge Sinyal + Yon into one column: "TUT ▲L" or "CIK! ▼S"
            sig_text, sig_color = sys_signal
            signal_merged = (f"{sig_text} {side_arrow}{side_short}", sig_color)

            # Funding rate + OI for position (from held indicators context)
            pos_fr = pos.get("funding_rate", 0)
            pos_fr_pct = pos_fr * 100
            pos_fr_str = f"{pos_fr_pct:+.2f}" if pos_fr != 0 else "--"
            pos_fr_color = "#FF1744" if pos_fr_pct > 0.05 else "#00C853" if pos_fr_pct < -0.05 else "gray"
            pos_oi = pos.get("oi_change_pct", 0)
            pos_oi_str = f"{pos_oi:+.1f}" if pos_oi != 0 else "--"
            pos_oi_color = "#00C853" if pos_oi > 2 else "#FF1744" if pos_oi < -2 else "gray"

            # Order book for position
            pos_ob = pos.get("ob_imbalance", 0)
            pos_ob_thin = pos.get("ob_thin_book", False)
            if pos_ob_thin:
                pos_ob_str, pos_ob_color = "X", "#FF1744"
            elif pos_ob > 0.25:
                pos_ob_str, pos_ob_color = f"+{pos_ob:.1f}", "#00C853"
            elif pos_ob < -0.25:
                pos_ob_str, pos_ob_color = f"{pos_ob:.1f}", "#FF1744"
            elif pos_ob != 0:
                pos_ob_str, pos_ob_color = f"{pos_ob:+.1f}", "gray"
            else:
                pos_ob_str, pos_ob_color = "--", "gray"

            vals = [
                signal_merged,
                (symbol, "white"),
                (f"{roi:+.1f}%", roi_color),
                (f"{atr_pct:.2f}%", atr_pct_color),
                (f"{lev}x", "#FF9800"),
                (tf, "#2196F3"),
                (pos_fr_str, pos_fr_color),
                (pos_oi_str, pos_oi_color),
                (pos_ob_str, pos_ob_color),
            ] + ind_cells + [
                (f"{conf_total:+.1f}", conf_color),
                (f"{bullish}", "#00C853" if bullish > 0 else "gray"),
                (f"{bearish}", "#FF1744" if bearish > 0 else "gray"),
                (f"{sl_dist:.1f}%", sl_color),
                (f"{em_dist:.1f}%", em_color),
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
