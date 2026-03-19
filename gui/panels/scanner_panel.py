"""Scanner Panel - 3-table layout: Trend pool, MR pool, Active Positions.
All tables share identical column widths for alignment.
Important columns per table highlighted with red border."""
import math
import customtkinter as ctk
from tkinter import messagebox


# Confluence indicators grouped by philosophy (must match confluence.py)
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

FILTER_COLS = ["ATR", "FR", "OB", "Conf", "RSI", "ADX", "Trend", "Vol", "MACD"]
MR_FILTER_COLS = ["BB", "FR", "OB", "Fee", "RSI", "Vol", "Brk", "", ""]

TF_LADDER = ["1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "8h", "12h"]

CONF_HDR_COLORS = {}
for k in CONF_TREND:
    CONF_HDR_COLORS[CONF_SHORT_MAP[k]] = "#4FC3F7"   # blue = trend
for k in CONF_REVERSION:
    CONF_HDR_COLORS[CONF_SHORT_MAP[k]] = "#CE93D8"   # purple = mean-rev
for k in CONF_VOLUME:
    CONF_HDR_COLORS[CONF_SHORT_MAP[k]] = "#FFD54F"   # yellow = volume

CONF_VALUE_KEYS = {
    "MACD": "MACD_histogram", "ADX": "ADX", "EMA50": None,
    "Price_vs_SMA": None, "SR": None, "RSI": "RSI", "BB": "BB_PercentB",
    "OBV": "OBV_slope", "CMF": "CMF", "CVD": "CVD_normalized", "VWAP": "VWAP",
}

_SUB_ROW_BG = "#141e33"
_RED_BORDER = "#E53935"       # red border for important columns
_RED_BG = "#2a0f0f"           # dark red background for important header cells
_NORMAL_HDR_BG = "transparent"

# ═══ Shared column layout (all 3 tables use this) ═══
_IND_W = [52, 50, 52, 50, 50, 52, 50, 52, 50, 50, 50]  # 11 indicators
_SHARED_MID = [50, 40, 38, 38, 34, 34]  # ATR% Lev TF Fnd OI% OB
_CONF_W = [46, 28, 28]  # Conf AL SAT
_FLT_W = [44] * len(FILTER_COLS)
_PREFIX_W = [22, 54, 90, 44]  # # Sinyal Sembol Skor/ROI
_SUFFIX_W = [46, 80]  # Ban Red (scan) / exit cols (pos)

SHARED_HEADERS = (
    ["#", "Sinyal", "Sembol", "Skor"] +
    ["ATR%", "Lev", "TF", "Fnd", "OI%", "OB"] +
    CONF_SHORT +
    ["Conf", "AL", "SAT"] +
    FILTER_COLS +
    ["Ban", "Red"]
)
SHARED_WIDTHS = _PREFIX_W + _SHARED_MID + _IND_W + _CONF_W + _FLT_W + _SUFFIX_W

# Column index offsets
_IND_START = len(_PREFIX_W) + len(_SHARED_MID)  # 10
_IND_END = _IND_START + len(_IND_W)             # 21
_FLT_START = _IND_END + len(_CONF_W)            # 24
_FLT_END = _FLT_START + len(FILTER_COLS)         # 33

# ═══ Important column indices per table type ═══
# TREND: trend indicators (MACD,ADX,EMA50,SMA,S/R) + volume (OBV,CMF,CVD) + Conf + all filters
_TREND_IMP = (
    set(range(_IND_START, _IND_START + 5)) |       # MACD,ADX,EMA50,SMA,S/R
    set(range(_IND_START + 7, _IND_START + 10)) |   # OBV,CMF,CVD
    {_IND_END} |                                     # Conf
    set(range(_FLT_START, _FLT_END))                 # all filters
)
# MR: reversion indicators (RSI,BB) + volume (OBV,CMF) + ATR% + filters
_MR_IMP = (
    set(range(_IND_START + 5, _IND_START + 7)) |   # RSI,BB
    set(range(_IND_START + 7, _IND_START + 9)) |    # OBV,CMF
    {4} |                                            # ATR%
    set(range(_FLT_START, _FLT_END))                 # all filters
)
# Position: no highlight (all columns are relevant)
_POS_IMP = set()

# Position table has different suffix columns
POS_SUFFIX_HEADERS = ["SL%", "Acil", "AktR%", "Kar/A", "Geri%", "Trail", "Kalan", "$"]
POS_SUFFIX_W = [40, 40, 42, 40, 40, 40, 42, 38]
POS_HEADERS = (
    ["", "Sinyal", "Sembol", "ROI%"] +
    ["ATR%", "Lev", "TF", "Fnd", "OI%", "OB"] +
    CONF_SHORT + ["Conf", "AL", "SAT"] +
    FILTER_COLS + POS_SUFFIX_HEADERS
)
POS_WIDTHS = _PREFIX_W + _SHARED_MID + _IND_W + _CONF_W + _FLT_W + POS_SUFFIX_W


# ═══ Helper functions ═══

def _conf_detail_cell(score: float, raw_val=None) -> tuple[str, str]:
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
    if not r.eligible:
        return ("BEKLE", "gray")
    score = abs(r.score)
    if score >= 70:
        return ("AL!", "#00E676") if r.direction == "LONG" else ("SAT!", "#FF1744")
    elif score >= 55:
        return ("AL", "#00C853") if r.direction == "LONG" else ("SAT", "#EF5350")
    return ("BEKLE", "gray")


def _system_signal_position(pos_side: str, confluence: dict) -> tuple[str, str]:
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
        return ("DIKKAT", "#FF9800")


def _get_upper_tfs(base_tf: str) -> tuple:
    try:
        idx = TF_LADDER.index(base_tf)
    except ValueError:
        return "1h", "4h"
    return TF_LADDER[min(idx + 2, len(TF_LADDER) - 1)], TF_LADDER[min(idx + 5, len(TF_LADDER) - 1)]


def _build_table_header(parent, headers, widths, important_set):
    """Build a header row with red-bordered boxes for important columns."""
    hdr = ctk.CTkFrame(parent, fg_color="transparent")
    hdr.pack(fill="x", padx=2)
    font = ctk.CTkFont(size=13, weight="bold")

    for col_idx, (h, w) in enumerate(zip(headers, widths)):
        # Determine header color
        if _FLT_START <= col_idx < _FLT_END:
            hdr_color = "#FF8A65"
        elif col_idx == _FLT_END:  # Ban column
            hdr_color = "#FF5252"
        else:
            hdr_color = CONF_HDR_COLORS.get(h, "#7799BB")

        if col_idx in important_set:
            # Important: wrap in red-bordered frame
            box = ctk.CTkFrame(hdr, fg_color=_RED_BG, border_color=_RED_BORDER,
                               border_width=1, corner_radius=3,
                               width=w, height=22)
            box.pack(side="left", padx=0, pady=0)
            box.pack_propagate(False)
            ctk.CTkLabel(box, text=h, width=w - 4, font=font,
                         text_color=hdr_color, fg_color="transparent").pack(expand=True)
        else:
            ctk.CTkLabel(hdr, text=h, width=w, font=font,
                         text_color=hdr_color).pack(side="left", padx=0)
    return hdr


class ScannerPanel(ctk.CTkFrame):
    """GUI panel: 3 tables (Trend + MR + Positions) — controls are in StatusBar."""

    def __init__(self, parent, controller):
        super().__init__(parent)
        self.controller = controller
        self.pack(fill="both", expand=True)
        self._build_ui()
        self._start_refresh()

    def _build_ui(self) -> None:
        # Alert banner (compact, only shown when needed)
        self._alert_frame = ctk.CTkFrame(self, fg_color="transparent", height=18)
        self._alert_frame.pack(fill="x", padx=5, pady=0)
        self._alert_labels = []
        self._reset_btn = ctk.CTkButton(
            self._alert_frame, text="Sifirla", width=55, height=18,
            fg_color="#FF9800", hover_color="#F57C00",
            font=ctk.CTkFont(size=10, weight="bold"),
            command=self._on_reset_losses)
        self._reset_btn.pack_forget()

        # ═══ TABLE 1: TREND POOL ═══
        trend_frame = ctk.CTkFrame(self)
        trend_frame.pack(fill="both", expand=True, padx=3, pady=(1, 0))

        ctk.CTkLabel(trend_frame, text="📈 Trend Havuzu",
                     font=ctk.CTkFont(size=12, weight="bold"),
                     text_color="#4FC3F7").pack(anchor="w", padx=4, pady=(1, 0))

        _build_table_header(trend_frame, SHARED_HEADERS, SHARED_WIDTHS, _TREND_IMP)

        self._results_scroll = ctk.CTkScrollableFrame(trend_frame, height=300)
        self._results_scroll.pack(fill="both", expand=True, padx=2)
        self._result_rows = []
        self._result_cache = []

        # ═══ TABLE 2: MEAN REVERSION POOL ═══
        mr_frame = ctk.CTkFrame(self)
        mr_frame.pack(fill="both", expand=True, padx=3, pady=(1, 0))

        ctk.CTkLabel(mr_frame, text="🔄 Mean Reversion Havuzu",
                     font=ctk.CTkFont(size=12, weight="bold"),
                     text_color="#CE93D8").pack(anchor="w", padx=4, pady=(1, 0))

        # MR table uses same column widths but different important set + MR filter headers
        mr_headers = list(SHARED_HEADERS)
        for i, fc in enumerate(MR_FILTER_COLS):
            mr_headers[_FLT_START + i] = fc if fc else "--"
        _build_table_header(mr_frame, mr_headers, SHARED_WIDTHS, _MR_IMP)

        self._mr_scroll = ctk.CTkScrollableFrame(mr_frame, height=250)
        self._mr_scroll.pack(fill="both", expand=True, padx=2)
        self._mr_rows = []
        self._mr_cache = []

        # ═══ TABLE 3: ACTIVE POSITIONS ═══
        pos_frame = ctk.CTkFrame(self)
        pos_frame.pack(fill="x", padx=3, pady=(1, 2))

        ctk.CTkLabel(pos_frame, text="📊 Aktif Pozisyonlar",
                     font=ctk.CTkFont(size=12, weight="bold"),
                     text_color="#FFD54F").pack(anchor="w", padx=4, pady=(1, 0))

        _build_table_header(pos_frame, POS_HEADERS, POS_WIDTHS, _POS_IMP)

        self._pos_scroll = ctk.CTkScrollableFrame(pos_frame, height=140)
        self._pos_scroll.pack(fill="x", padx=2)
        self._pos_rows = []
        self._pos_cache = []

    def _on_reset_losses(self):
        if messagebox.askyesno("Sifirla", "Ardisik zarar sayaci sifirlansin mi?"):
            self.controller.reset_consecutive_losses()

    # ═══ Refresh ═══

    def _start_refresh(self):
        self._refresh()

    def _refresh(self):
        try:
            self._update_state()
            self._update_alerts()
        except Exception:
            pass
        try:
            self._update_results()
        except Exception:
            pass
        try:
            self._update_mr_results()
        except Exception:
            pass
        try:
            self._update_position()
        except Exception as e:
            from loguru import logger
            logger.error(f"Refresh position error: {e}")
            import traceback
            logger.error(traceback.format_exc())
        try:
            self._update_trade()
        except Exception:
            pass
        self.after(4000, self._refresh)

    def _get_status_bar(self):
        """Find the StatusBar widget from the main window."""
        try:
            root = self.winfo_toplevel()
            if hasattr(root, '_status_bar'):
                return root._status_bar
        except Exception:
            pass
        return None

    def _update_state(self):
        state = self.controller.get_scanner_state()
        scan_count = self.controller.get_scanner_scan_count()
        sb = self._get_status_bar()
        if sb:
            sb.update_scanner_state(state, scan_count)

    def _update_alerts(self):
        alerts = self.controller.get_system_alerts()
        for lbl in self._alert_labels:
            lbl.destroy()
        self._alert_labels.clear()
        self._reset_btn.pack_forget()
        if not alerts:
            return
        LEVEL_COLORS = {
            "error": ("#FF1744", "#2a0a0a"),
            "warning": ("#FF9800", "#2a1a0a"),
            "info": ("#64B5F6", "transparent"),
        }
        show_reset = False
        for alert in alerts:
            level = alert.get("level", "info")
            msg = alert.get("message", "")
            fg, bg = LEVEL_COLORS.get(level, ("#FFFFFF", "transparent"))
            lbl = ctk.CTkLabel(self._alert_frame, text=f"  {msg}",
                               font=ctk.CTkFont(size=11, weight="bold"),
                               text_color=fg, fg_color=bg, corner_radius=3, height=18)
            lbl.pack(side="left", padx=3, pady=0)
            self._alert_labels.append(lbl)
            if "ardisik zarar" in msg and level == "error":
                show_reset = True
        if show_reset:
            self._reset_btn.pack(side="left", padx=4, pady=0)

    # ═══ Generic table row management ═══

    def _ensure_rows(self, scroll_frame, rows_list, cache_list, widths, count):
        """Ensure exactly `count` rows exist in a scrollable table."""
        font = ctk.CTkFont(size=12)
        while len(rows_list) > count:
            frame, labels = rows_list.pop()
            frame.destroy()
        while len(cache_list) > count:
            cache_list.pop()
        while len(rows_list) < count:
            idx = len(rows_list)
            bg = "#1c2d4d" if idx % 2 == 0 else "transparent"
            row_frame = ctk.CTkFrame(scroll_frame, fg_color=bg)
            row_frame.pack(fill="x", pady=0)
            labels = []
            for w in widths:
                lbl = ctk.CTkLabel(row_frame, text="", width=w,
                                   font=font, text_color="gray")
                lbl.pack(side="left", padx=0)
                labels.append(lbl)
            rows_list.append((row_frame, labels))
            cache_list.append(None)

    def _update_row(self, rows_list, cache_list, idx, vals, bg=None):
        """Update a single row if vals changed."""
        if idx >= len(cache_list) or cache_list[idx] == vals:
            return
        cache_list[idx] = vals
        frame, labels = rows_list[idx]
        if bg is not None and frame.cget("fg_color") != bg:
            frame.configure(fg_color=bg)
        for lbl, (val, color) in zip(labels, vals):
            lbl.configure(text=val, text_color=color)

    # ═══ TABLE 1: TREND Results ═══

    def _update_results(self):
        results = self.controller.get_scan_results()
        if not results:
            return
        banned_symbols = self.controller.get_banned_symbols()
        n = min(len(results), 30)

        self._ensure_rows(self._results_scroll, self._result_rows,
                          self._result_cache, SHARED_WIDTHS, n)

        for i, r in enumerate(results[:n]):
            try:
                vals = self._build_scan_row_vals(i, r, banned_symbols)
                bg = "#1e3355" if i % 2 == 0 else "#172540"
                self._update_row(self._result_rows, self._result_cache, i, vals, bg)
            except Exception as e:
                from loguru import logger
                logger.error(f"TREND row {i} ({getattr(r, 'symbol', '?')}): {e}")
                import traceback
                logger.error(traceback.format_exc())

        candidate = self.controller.get_scanner_candidate()
        sb = self._get_status_bar()
        if sb:
            if candidate:
                c_color = "#00C853" if candidate.score > 0 else "#FF1744"
                sb.update_scanner_state(
                    self.controller.get_scanner_state(),
                    self.controller.get_scanner_scan_count(),
                    candidate_text=f"Aday: {candidate.symbol} ({candidate.score:+.0f})",
                    candidate_color=c_color)
            else:
                sb.update_scanner_state(
                    self.controller.get_scanner_state(),
                    self.controller.get_scanner_scan_count(),
                    candidate_text="Aday: --", candidate_color="gray")

    def _build_scan_row_vals(self, i, r, banned_symbols=None):
        score_color = "#00C853" if r.score > 0 else "#FF1744" if r.score < 0 else "gray"
        eligible_marker = "*" if r.eligible else ""
        lev_str = f"{r.leverage}x" if r.leverage > 0 else "--"
        tf_str = getattr(r, 'timeframe', '1m')
        mtf_data = getattr(r, 'mtf_data', {}) or {}
        if mtf_data:
            # MTF sayısını göster (ör: "5m+3") — tam liste sığmaz
            tf_display = f"{tf_str}+{len(mtf_data)}"
        else:
            tf_display = tf_str
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

        fr = getattr(r, 'funding_rate', 0) or 0
        fr_pct = fr * 100
        fr_color = "#FF1744" if fr_pct > 0.05 else "#00C853" if fr_pct < -0.05 else "gray"
        fr_str = f"{fr_pct:+.2f}" if fr != 0 else "--"

        oi_chg = getattr(r, 'oi_change_pct', 0) or 0
        oi_color = "#00C853" if oi_chg > 2 else "#FF1744" if oi_chg < -2 else "gray"
        oi_str = f"{oi_chg:+.1f}" if oi_chg != 0 else "--"

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

        grp_tag = {"TREND": "T", "REVERSION": "R", "BOTH": "B",
                   "CONFLICT": "!", "NEUTRAL": "-"}.get(active_grp, "")
        conf_str = f"{conf_total:+.1f}{grp_tag}"
        conf_color = "#FF9800" if active_grp == "CONFLICT" else \
                     "#00C853" if conf_total >= 4 else \
                     "#FF1744" if conf_total <= -4 else "white"

        filter_checks = getattr(r, 'filter_checks', {}) or {}
        filter_cells = []
        for fc_name in FILTER_COLS:
            check = filter_checks.get(fc_name)
            if check is None:
                filter_cells.append(("--", "#555555"))
            else:
                passed, actual, _ = check
                if passed:
                    filter_cells.append((f"\u2713\n{actual}", "#00C853"))
                else:
                    filter_cells.append((f"\u2717\n{actual}", "#FF1744"))

        ban_info = (banned_symbols or {}).get(r.symbol)
        if ban_info:
            rem = ban_info["remaining_s"]
            ban_str = f"{rem/3600:.1f}s" if rem >= 3600 else f"{rem/60:.0f}dk"
            ban_color = "#FF1744" if ban_info["type"] == "daily_ban" else "#FF9800"
        else:
            ban_str, ban_color = "", "gray"

        return [
            (f"{i+1}", "gray"), sys_signal,
            (f"{r.symbol}{eligible_marker}", row_color),
            (f"{r.score:+.0f}", score_color),
            (f"{atr_pct:.2f}" if atr_pct > 0 else "--", atr_color),
            (lev_str, "#FF9800" if r.leverage >= 75 else "white"),
            (tf_display, "#00E5FF" if mtf_data else "#2196F3"), (fr_str, fr_color),
            (oi_str, oi_color), (ob_str, ob_color),
        ] + ind_cells + [
            (conf_str, conf_color),
            (f"{bullish}", "#00C853" if bullish > 0 else "gray"),
            (f"{bearish}", "#FF1744" if bearish > 0 else "gray"),
        ] + filter_cells + [
            (ban_str, ban_color),
            (reject_short, "#FF5252" if reject_short else "gray"),
        ]

    def _build_mtf_sub_row_vals(self, tf, mtf_entry, symbol):
        indicators = mtf_entry.get("indicators", {})
        confluence = mtf_entry.get("confluence", {})
        details = confluence.get("details", {})
        bullish = confluence.get("bullish_count", 0)
        bearish = confluence.get("bearish_count", 0)
        conf_total = confluence.get("score", 0)
        active_grp = confluence.get("active_group", "")

        ind_cells = []
        for key in CONF_INDICATORS:
            vk = CONF_VALUE_KEYS.get(key)
            raw = indicators.get(vk) if vk else None
            ind_cells.append(_conf_detail_cell(details.get(key, 0), raw))

        grp_tag = {"TREND": "T", "REVERSION": "R", "BOTH": "B",
                   "CONFLICT": "!", "NEUTRAL": "-"}.get(active_grp, "")
        conf_str = f"{conf_total:+.1f}{grp_tag}"
        conf_color = "#FF9800" if active_grp == "CONFLICT" else \
                     "#00C853" if conf_total >= 4 else \
                     "#FF1744" if conf_total <= -4 else "white"

        empty = ("", "#555555")
        n_flt = len(FILTER_COLS)
        return [
            empty, empty, (f"  \u2514 {symbol} @{tf}", "#667799"), empty,
            empty, empty, (tf, "#64B5F6"), empty, empty, empty,
        ] + ind_cells + [
            (conf_str, conf_color),
            (f"{bullish}", "#00C853" if bullish > 0 else "#555555"),
            (f"{bearish}", "#FF1744" if bearish > 0 else "#555555"),
        ] + [empty] * n_flt + [empty, empty]

    # ═══ TABLE 2: MR Results ═══

    def _update_mr_results(self):
        results = self.controller.get_mr_scan_results()
        from loguru import logger
        logger.debug(f"[GUI-MR] results type={type(results).__name__}, "
                     f"len={len(results) if results else 0}, "
                     f"bool={bool(results)}")
        if not results:
            self._ensure_rows(self._mr_scroll, self._mr_rows,
                              self._mr_cache, SHARED_WIDTHS, 0)
            return

        n = min(len(results), 20)
        self._ensure_rows(self._mr_scroll, self._mr_rows,
                          self._mr_cache, SHARED_WIDTHS, n)

        for i, r in enumerate(results[:n]):
            try:
                vals = self._build_mr_row_vals(i, r)
                bg = "#1c2d4d" if i % 2 == 0 else "transparent"
                self._update_row(self._mr_rows, self._mr_cache, i, vals, bg)
            except Exception as e:
                from loguru import logger
                logger.error(f"MR row {i} ({getattr(r, 'symbol', '?')}): {e}")
                import traceback
                logger.error(traceback.format_exc())

    def _build_mr_row_vals(self, i, r):
        """Build row values for MR scan result using shared column layout."""
        score_color = "#00C853" if r.score > 0 else "#FF1744" if r.score < 0 else "gray"
        eligible_marker = "*" if r.eligible else ""
        row_color = score_color if r.eligible else "gray"
        sys_signal = _system_signal_candidate(r)

        lev_str = f"{r.leverage}x" if r.leverage > 0 else "--"
        tf_str = getattr(r, 'timeframe', '1m')
        reject_short = r.reject_reason[:12] if r.reject_reason else ""

        atr_pct = getattr(r, 'atr_percent', 0) or 0
        atr_color = "#FF9800" if atr_pct > 0.5 else "#2196F3" if atr_pct > 0 else "gray"

        # Sentiment display
        fr = getattr(r, 'funding_rate', 0) or 0
        fr_pct = fr * 100
        fr_color = "#FF1744" if fr_pct > 0.05 else "#00C853" if fr_pct < -0.05 else "gray"
        fr_str = f"{fr_pct:+.2f}" if fr != 0 else "--"

        oi_chg = getattr(r, 'oi_change_pct', 0) or 0
        oi_color = "#00C853" if oi_chg > 2 else "#FF1744" if oi_chg < -2 else "gray"
        oi_str = f"{oi_chg:+.1f}" if oi_chg != 0 else "--"

        ob_imb = getattr(r, 'ob_imbalance', 0) or 0
        ob_thin = getattr(r, 'ob_thin_book', False)
        if ob_thin:
            ob_str, ob_color = "X", "#FF1744"
        elif abs(ob_imb) > 0.25:
            ob_str = f"{ob_imb:+.1f}"
            ob_color = "#00C853" if ob_imb > 0 else "#FF1744"
        elif ob_imb != 0:
            ob_str, ob_color = f"{ob_imb:+.1f}", "gray"
        else:
            ob_str, ob_color = "--", "gray"

        # Indicator cells (using confluence from MR scorer)
        raw_ind = getattr(r, 'indicator_values', {}) or {}
        confluence = getattr(r, 'confluence', {}) or {}
        details = confluence.get("details", {}) if hasattr(confluence, 'get') else {}
        bullish = confluence.get("bullish_count", 0) if hasattr(confluence, 'get') else 0
        bearish = confluence.get("bearish_count", 0) if hasattr(confluence, 'get') else 0
        conf_total = confluence.get("score", 0) if hasattr(confluence, 'get') else 0

        ind_cells = []
        for key in CONF_INDICATORS:
            vk = CONF_VALUE_KEYS.get(key)
            raw = raw_ind.get(vk) if vk else None
            ind_cells.append(_conf_detail_cell(details.get(key, 0), raw))

        conf_str = f"{conf_total:+.1f}"
        conf_color = "#00C853" if conf_total >= 4 else \
                     "#FF1744" if conf_total <= -4 else "white"

        # MR filter checks (mapped to MR_FILTER_COLS)
        filter_checks = getattr(r, 'filter_checks', {}) or {}
        filter_cells = []
        for fc_name in MR_FILTER_COLS:
            if not fc_name:
                filter_cells.append(("", "#555555"))
                continue
            check = filter_checks.get(fc_name)
            if check is None:
                filter_cells.append(("--", "#555555"))
            else:
                passed, actual, _ = check
                if passed:
                    filter_cells.append((f"\u2713\n{actual}", "#00C853"))
                else:
                    filter_cells.append((f"\u2717\n{actual}", "#FF1744"))

        # Source tag for ban column
        src = getattr(r, 'source', '')
        src_color = "#CE93D8" if src else "gray"

        return [
            (f"{i+1}", "gray"), sys_signal,
            (f"{r.symbol}{eligible_marker}", row_color),
            (f"{r.score:+.0f}", score_color),
            (f"{atr_pct:.2f}" if atr_pct > 0 else "--", atr_color),
            (lev_str, "#FF9800" if r.leverage >= 75 else "white"),
            (tf_str, "#2196F3"), (fr_str, fr_color),
            (oi_str, oi_color), (ob_str, ob_color),
        ] + ind_cells + [
            (conf_str, conf_color),
            (f"{bullish}", "#00C853" if bullish > 0 else "gray"),
            (f"{bearish}", "#FF1744" if bearish > 0 else "gray"),
        ] + filter_cells + [
            (src, src_color),
            (reject_short, "#FF5252" if reject_short else "gray"),
        ]

    # ═══ TABLE 3: Active Positions ═══

    def _update_position(self):
        positions = self.controller.get_all_scanner_positions()
        if not positions:
            self._ensure_rows(self._pos_scroll, self._pos_rows,
                              self._pos_cache, POS_WIDTHS, 1)
            empty_vals = [("Pozisyon yok", "gray")] + [("", "gray")] * (len(POS_WIDTHS) - 1)
            self._update_row(self._pos_rows, self._pos_cache, 0, empty_vals)
            return

        held_ind = self.controller.get_held_indicators()
        n = len(positions)
        self._ensure_rows(self._pos_scroll, self._pos_rows,
                          self._pos_cache, POS_WIDTHS, n)

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

            # Exit parameter calculations
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

            atr_pct = pos.get("atr_pct", 0)
            trailing_activate_roi = pos.get("trailing_activate_roi", 0)
            trailing_distance_pct = pos.get("trailing_distance_pct", 0)

            if atr > 0 and cur_price > 0:
                profit_atr = ((cur_price - entry) / atr) if is_long else ((entry - cur_price) / atr)
            else:
                profit_atr = 0

            atr_pct_color = "#2196F3"
            act_roi_color = "#FF9800" if trailing_activate_roi > 50 else "#00C853"
            kar7_str = f"{profit_atr:.1f}/{atr_activate:.0f}"
            kar7_color = "#00C853" if profit_atr >= atr_activate else \
                         "#81C784" if profit_atr >= atr_activate * 0.5 else \
                         "#FF9800" if profit_atr >= 0 else "#FF1744"
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
                time_str, time_color = "--", "gray"

            side_short = "L" if is_long else "S"
            side_arrow = "\u25B2" if is_long else "\u25BC"
            roi_color = "#00C853" if roi > 0 else "#FF1744" if roi < 0 else "white"
            conf_color = "#00C853" if conf_total >= 4 else "#FF1744" if conf_total <= -4 else "white"

            sig_text, sig_color = sys_signal
            signal_merged = (f"{sig_text} {side_arrow}{side_short}", sig_color)

            # Sentiment for position
            pos_fr = pos.get("funding_rate", 0)
            pos_fr_pct = pos_fr * 100
            pos_fr_str = f"{pos_fr_pct:+.2f}" if pos_fr != 0 else "--"
            pos_fr_color = "#FF1744" if pos_fr_pct > 0.05 else "#00C853" if pos_fr_pct < -0.05 else "gray"
            pos_oi = pos.get("oi_change_pct", 0)
            pos_oi_str = f"{pos_oi:+.1f}" if pos_oi != 0 else "--"
            pos_oi_color = "#00C853" if pos_oi > 2 else "#FF1744" if pos_oi < -2 else "gray"
            pos_ob = pos.get("ob_imbalance", 0)
            pos_ob_thin = pos.get("ob_thin_book", False)
            if pos_ob_thin:
                pos_ob_str, pos_ob_color = "X", "#FF1744"
            elif abs(pos_ob) > 0.25:
                pos_ob_str = f"{pos_ob:+.1f}"
                pos_ob_color = "#00C853" if pos_ob > 0 else "#FF1744"
            elif pos_ob != 0:
                pos_ob_str, pos_ob_color = f"{pos_ob:+.1f}", "gray"
            else:
                pos_ob_str, pos_ob_color = "--", "gray"

            # Mode indicator in symbol
            entry_mode = pos.get("entry_mode", "TREND")
            regime_switched = pos.get("regime_switched", False)
            if entry_mode == "MEAN_REVERSION" and not regime_switched:
                mode_tag = " MR"
                symbol_color = "#CE93D8"
            elif regime_switched:
                mode_tag = " M\u2192T"
                symbol_color = "#00E676"
            else:
                mode_tag = ""
                symbol_color = "white"

            pos_filter_cells = [("\u2713", "#00C853")] * len(FILTER_COLS)

            vals = [
                ("", "#1a1a2e"), signal_merged,
                (f"{symbol}{mode_tag}", symbol_color),
                (f"{roi:+.1f}%", roi_color),
                (f"{atr_pct:.2f}%", atr_pct_color),
                (f"{lev}x", "#FF9800"), (tf, "#2196F3"),
                (pos_fr_str, pos_fr_color),
                (pos_oi_str, pos_oi_color),
                (pos_ob_str, pos_ob_color),
            ] + ind_cells + [
                (f"{conf_total:+.1f}", conf_color),
                (f"{bullish}", "#00C853" if bullish > 0 else "gray"),
                (f"{bearish}", "#FF1744" if bearish > 0 else "gray"),
            ] + pos_filter_cells + [
                (f"{sl_dist:.1f}%", sl_color),
                (f"{em_dist:.1f}%", em_color),
                (f"{trailing_activate_roi:.0f}%", act_roi_color),
                (kar7_str, kar7_color),
                (f"{trailing_distance_pct:.2f}%", geri_color),
                (trail_str, trail_color),
                (time_str, time_color),
                (f"${margin:.1f}", "white"),
            ]

            self._update_row(self._pos_rows, self._pos_cache, idx, vals)

    def _update_trade(self):
        trade = self.controller.get_last_trade()
        if trade:
            pnl = trade.get("pnl_usdt", 0)
            symbol = trade.get("symbol", "?")
            reason = trade.get("exit_reason", "?")
            pnl_color = "#00C853" if pnl >= 0 else "#FF1744"
            sb = self._get_status_bar()
            if sb:
                sb._trade_lbl.configure(
                    text=f"Son: {symbol} {pnl:+.4f}$ ({reason})",
                    text_color=pnl_color)
