"""System M Panel — AlphaTrend PRO: Sinyal bazli tarama, pozisyon ve karar tablosu."""
import time
import customtkinter as ctk
from loguru import logger

# ═══════════════════════════════════════════════════
#  Font & Row sizing
# ═══════════════════════════════════════════════════
_FONT_SZ = 13
_HDR_FONT_SZ = 13
_TITLE_FONT_SZ = 15
_PAD_X = 4

# ═══ Column Layout: Scan Results ═══
SM_SCAN_HEADERS = [
    "#", "Sinyal", "Sembol", "Fiyat",
    "AlphaTrend", "AT[2]", "Trend",
    "ADX", "RSI", "MFI", "ATR",
    "ADX_S", "ADX_D", "Slope", "Filtre",
]
SM_SCAN_WIDTHS = [
    32, 74, 105, 92,
    92, 92, 54,
    56, 52, 52, 76,
    50, 50, 50, 54,
]
_SM_IMP = {1, 2, 3, 6}

# ═══ Column Layout: Positions ═══
SM_POS_HEADERS = [
    "#", "Sembol", "Yon", "Giris Fiyat", "Guncel Fiyat",
    "ROI%", "Kaldirac", "Miktar", "Marjin $", "Sure",
]
SM_POS_WIDTHS = [
    32, 105, 72, 92, 92,
    76, 58, 80, 72, 72,
]
_SM_POS_IMP = {1, 2, 5}

# ═══ Column Layout: Decisions ═══
SM_DEC_HEADERS = [
    "Saat", "Sembol", "Sinyal", "Karar", "Fiyat",
    "Trend", "ADX", "RSI", "Aciklama",
]
SM_DEC_WIDTHS = [
    68, 105, 60, 128, 92,
    50, 56, 52, 240,
]
_SM_DEC_IMP = {1, 2, 3}

# ═══ Colors ═══
_ACCENT = "#26C6DA"
_BG_HEADER = "#2a2a4a"
_BG_ROW_ODD = "#1e1e38"
_BG_ROW_EVEN = "transparent"
_TREND_COLORS = {"green": "#00E676", "red": "#FF5252"}

_ACTION_COLORS = {
    "LONG_AÇ": "#00E676", "SHORT_AÇ": "#FF5252",
    "KAPAT": "#FFD54F", "ATLA": "#78909C",
    "REVERSE->LONG": "#00E676", "REVERSE->SHORT": "#FF5252",
    "ÖZET": "#26C6DA",
    "SİNYAL_YOK": "#546E7A", "VERİ_YOK": "#455A64", "HATA": "#FF8A65",
    "LONG_BAŞARISIZ": "#FF8A65", "SHORT_BAŞARISIZ": "#FF8A65",
    "KAPAT_BAŞARISIZ": "#FF8A65", "REVERSE_BAŞARISIZ": "#FF8A65",
}

_TAB_ACTIVE = "#3d5afe"
_TAB_INACTIVE = "#455A64"
_TAB_HOVER = "#546E7A"


def _make_header_row(parent, headers, widths, imp_set):
    """Baslik satiri — koyu arka plan, kalin font."""
    outer = ctk.CTkFrame(parent, fg_color=_BG_HEADER, corner_radius=4)
    outer.pack(fill="x", padx=6, pady=(6, 3))
    for j, (h, w) in enumerate(zip(headers, widths)):
        color = "#FFFFFF" if j in imp_set else "#CFD8DC"
        ctk.CTkLabel(
            outer, text=h, width=w,
            font=ctk.CTkFont(size=_HDR_FONT_SZ, weight="bold"),
            text_color=color, anchor="w",
        ).pack(side="left", padx=_PAD_X, pady=6)


def _make_data_row(parent, row_data, row_idx):
    """Veri satiri — zebra renk."""
    bg = _BG_ROW_ODD if row_idx % 2 == 0 else _BG_ROW_EVEN
    row = ctk.CTkFrame(parent, fg_color=bg, corner_radius=2)
    row.pack(fill="x", padx=4, pady=1)
    font = ctk.CTkFont(size=_FONT_SZ)
    for text, color, width in row_data:
        ctk.CTkLabel(
            row, text=str(text), width=width,
            font=font, text_color=color, anchor="w",
        ).pack(side="left", padx=_PAD_X, pady=4)
    return row


def _g(obj, attr, default=None):
    if isinstance(obj, dict):
        return obj.get(attr, default)
    return getattr(obj, attr, default)


class SystemMPanel(ctk.CTkFrame):
    """System M AlphaTrend PRO — tab'siz, segmented button ile sekme degistirme."""

    def __init__(self, master, app_ctrl):
        super().__init__(master)
        self.controller = app_ctrl
        self.pack(fill="both", expand=True)

        self._scan_rows: list = []
        self._scan_cache: list = []
        self._pos_rows: list = []
        self._pos_cache: list = []
        self._dec_rows: list = []
        self._last_dec_count: int = 0
        self._dec_filter = "all"
        self._active_tab = "scan"

        try:
            self._build_ui()
            logger.info("[SysM Panel] UI built OK")
        except Exception as e:
            logger.error(f"[SysM Panel] BUILD FAILED: {e}")
            import traceback
            logger.error(traceback.format_exc())
            ctk.CTkLabel(
                self, text=f"System M Panel HATA:\n{e}",
                font=ctk.CTkFont(size=16), text_color="#FF5252",
                wraplength=600,
            ).pack(pady=40, padx=20)
            return
        self._start_refresh()

    def _build_ui(self) -> None:
        # ═══ TOP BAR: Mode + Stats ═══
        top = ctk.CTkFrame(self)
        top.pack(fill="x", padx=8, pady=(6, 2))

        self._mode_label = ctk.CTkLabel(
            top, text="Mod: -",
            font=ctk.CTkFont(size=14, weight="bold"), text_color=_ACCENT,
        )
        self._mode_label.pack(side="left")

        self._stats_label = ctk.CTkLabel(
            top, text="Tarama: 0  |  BUY: 0  |  SELL: 0  |  Poz: 0",
            font=ctk.CTkFont(size=14), text_color="#B0BEC5",
        )
        self._stats_label.pack(side="right")

        # ═══ TAB BUTTONS (kendi segmented button'umuz) ═══
        tab_bar = ctk.CTkFrame(self)
        tab_bar.pack(fill="x", padx=8, pady=(4, 2))

        self._tab_btns = {}
        for label, key in [("TARAMA", "scan"), ("POZISYONLAR", "pos"), ("KARARLAR", "dec")]:
            is_active = (key == "scan")
            btn = ctk.CTkButton(
                tab_bar, text=label,
                width=130, height=34,
                font=ctk.CTkFont(size=13, weight="bold"),
                fg_color=_TAB_ACTIVE if is_active else _TAB_INACTIVE,
                hover_color=_TAB_HOVER,
                corner_radius=8,
                command=lambda k=key: self._switch_tab(k),
            )
            btn.pack(side="left", padx=3)
            self._tab_btns[key] = btn

        # ═══ CONTENT FRAMES (her biri ayri, show/hide ile) ═══

        # --- SCAN ---
        self._scan_frame = ctk.CTkFrame(self)
        ctk.CTkLabel(
            self._scan_frame, text="ALPHATREND PRO TARAMA",
            font=ctk.CTkFont(size=_TITLE_FONT_SZ, weight="bold"),
            text_color="#FFFFFF",
        ).pack(anchor="w", padx=8, pady=(4, 0))
        _make_header_row(self._scan_frame, SM_SCAN_HEADERS, SM_SCAN_WIDTHS, _SM_IMP)
        self._scan_scroll = ctk.CTkScrollableFrame(self._scan_frame)
        self._scan_scroll.pack(fill="both", expand=True, padx=4, pady=(2, 4))

        # --- POS ---
        self._pos_frame = ctk.CTkFrame(self)
        ctk.CTkLabel(
            self._pos_frame, text="AKTIF POZISYONLAR",
            font=ctk.CTkFont(size=_TITLE_FONT_SZ, weight="bold"),
            text_color="#FFFFFF",
        ).pack(anchor="w", padx=8, pady=(4, 0))
        _make_header_row(self._pos_frame, SM_POS_HEADERS, SM_POS_WIDTHS, _SM_POS_IMP)
        self._pos_scroll = ctk.CTkScrollableFrame(self._pos_frame)
        self._pos_scroll.pack(fill="both", expand=True, padx=4, pady=(2, 4))
        self._pos_empty_label = ctk.CTkLabel(
            self._pos_scroll, text="Henuz aktif pozisyon yok",
            font=ctk.CTkFont(size=14), text_color="#546E7A",
        )
        self._pos_empty_label.pack(pady=30)

        # --- DEC ---
        self._dec_frame = ctk.CTkFrame(self)

        dec_bar = ctk.CTkFrame(self._dec_frame)
        dec_bar.pack(fill="x", padx=8, pady=(4, 0))
        ctk.CTkLabel(
            dec_bar, text="TRADE KARARLARI",
            font=ctk.CTkFont(size=_TITLE_FONT_SZ, weight="bold"),
            text_color="#FFFFFF",
        ).pack(side="left")

        filter_box = ctk.CTkFrame(dec_bar)
        filter_box.pack(side="right")
        self._filter_btns = {}
        for label, key in [("Tumu", "all"), ("Sinyaller", "signals"), ("Islemler", "trades")]:
            btn = ctk.CTkButton(
                filter_box, text=label, width=85, height=30,
                font=ctk.CTkFont(size=12, weight="bold"),
                fg_color=_TAB_ACTIVE if key == "all" else _TAB_INACTIVE,
                hover_color=_TAB_HOVER, corner_radius=6,
                command=lambda k=key: self._set_dec_filter(k),
            )
            btn.pack(side="left", padx=3)
            self._filter_btns[key] = btn

        _make_header_row(self._dec_frame, SM_DEC_HEADERS, SM_DEC_WIDTHS, _SM_DEC_IMP)
        self._dec_scroll = ctk.CTkScrollableFrame(self._dec_frame)
        self._dec_scroll.pack(fill="both", expand=True, padx=4, pady=(2, 4))

        # Baslangicta SCAN goster
        self._scan_frame.pack(fill="both", expand=True, padx=2, pady=2)

    def _switch_tab(self, key: str) -> None:
        """Sekme degistir."""
        if key == self._active_tab:
            return
        # Hide current
        for frame_key, frame in [("scan", self._scan_frame),
                                   ("pos", self._pos_frame),
                                   ("dec", self._dec_frame)]:
            frame.pack_forget()
        # Show selected
        target = {"scan": self._scan_frame, "pos": self._pos_frame, "dec": self._dec_frame}[key]
        target.pack(fill="both", expand=True, padx=2, pady=2)
        self._active_tab = key
        # Update button colors
        for k, btn in self._tab_btns.items():
            btn.configure(fg_color=_TAB_ACTIVE if k == key else _TAB_INACTIVE)

    # ───────────────────────────────────────────────
    #  REFRESH
    # ───────────────────────────────────────────────
    def _start_refresh(self) -> None:
        self._refresh_loop()

    def _refresh_loop(self) -> None:
        try:
            self._update_all()
        except Exception as e:
            logger.error(f"[SysM Panel] refresh error: {e}")
        self.after(2000, self._refresh_loop)

    def _update_all(self) -> None:
        results = self.controller.get_system_m_results() or []
        positions = self.controller.get_all_scanner_positions() or []
        decisions = self.controller.get_system_m_decisions() or []

        # Mode
        cfg = self.controller.config
        short_en = cfg.get("system_m.short_enabled", False)
        reverse_en = cfg.get("system_m.reverse_enabled", False)
        if not short_en:
            mode_text = "Mod: SPOT (Sadece Long)"
        elif reverse_en:
            mode_text = "Mod: SHORT + REVERSE"
        else:
            mode_text = "Mod: SHORT (Reverse kapali)"
        self._mode_label.configure(text=mode_text)

        buy_c = sum(1 for r in results if _g(r, "signal") == "BUY")
        sell_c = sum(1 for r in results if _g(r, "signal") == "SELL")
        m_pos = [p for p in positions if _g(p, "entry_mode") == "SYSTEM_M"]
        self._stats_label.configure(
            text=f"Tarama: {len(results)}   |   BUY: {buy_c}   |   "
                 f"SELL: {sell_c}   |   Pozisyon: {len(m_pos)}")

        self._update_scan_table(results)
        self._update_pos_table(m_pos)
        self._update_dec_table(decisions)

    # ═══════════════════════════════════════════════════
    #  SCAN TABLE
    # ═══════════════════════════════════════════════════
    def _update_scan_table(self, results: list) -> None:
        sorted_r = sorted(results,
                          key=lambda r: (0 if _g(r, "signal") != "NONE" else 1,
                                         -(_g(r, "adx", 0) or 0)))
        new_cache = [self._scan_row_data(i + 1, r) for i, r in enumerate(sorted_r[:60])]
        if new_cache == self._scan_cache:
            return
        self._scan_cache = new_cache
        for w in self._scan_rows:
            w.destroy()
        self._scan_rows.clear()
        for idx, rd in enumerate(new_cache):
            self._scan_rows.append(_make_data_row(self._scan_scroll, rd, idx))

    def _scan_row_data(self, idx, r):
        signal = _g(r, "signal", "NONE")
        tc_name = _g(r, "trend_color", "red")
        adx = _g(r, "adx", 0) or 0
        rsi = _g(r, "rsi", 50) or 50
        mfi = _g(r, "mfi", 50) or 50
        atr = _g(r, "atr", 0) or 0
        price = _g(r, "price", 0) or 0
        at_now = _g(r, "alpha_trend", 0) or 0
        at_2 = _g(r, "alpha_trend_2", 0) or 0
        if signal == "BUY":
            st, sc = "^ BUY", "#00E676"
        elif signal == "SELL":
            st, sc = "v SELL", "#FF5252"
        else:
            st, sc = "-", "#616161"
        tc = _TREND_COLORS.get(tc_name, "#90A4AE")
        dc = "#CFD8DC"
        W = SM_SCAN_WIDTHS
        return [
            (idx, "#90A4AE", W[0]), (st, sc, W[1]),
            (_g(r, "symbol", ""), "#FFFFFF", W[2]),
            (f"{price:.4f}" if price < 1 else f"{price:.2f}", dc, W[3]),
            (f"{at_now:.4f}" if at_now < 1 else f"{at_now:.2f}", tc, W[4]),
            (f"{at_2:.4f}" if at_2 < 1 else f"{at_2:.2f}", "#90A4AE", W[5]),
            ("^" if tc_name == "green" else "v", tc, W[6]),
            (f"{adx:.1f}", "#FFD54F" if adx > 25 else dc, W[7]),
            (f"{rsi:.0f}", "#00E676" if rsi > 60 else "#FF5252" if rsi < 40 else dc, W[8]),
            (f"{mfi:.0f}", dc, W[9]),
            (f"{atr:.6f}" if atr < 0.01 else f"{atr:.4f}", dc, W[10]),
            ("OK" if _g(r, "adx_static_ok") else "X",
             "#00E676" if _g(r, "adx_static_ok") else "#FF5252", W[11]),
            ("OK" if _g(r, "adx_dynamic_ok") else "X",
             "#00E676" if _g(r, "adx_dynamic_ok") else "#FF5252", W[12]),
            ("OK" if _g(r, "slope_ok") else "X",
             "#00E676" if _g(r, "slope_ok") else "#FF5252", W[13]),
            ("OK" if _g(r, "final_filter") else "X",
             "#00E676" if _g(r, "final_filter") else "#FF5252", W[14]),
        ]

    # ═══════════════════════════════════════════════════
    #  POSITIONS TABLE
    # ═══════════════════════════════════════════════════
    def _update_pos_table(self, positions):
        new_cache = [self._pos_row_data(i + 1, p) for i, p in enumerate(positions)]
        if new_cache == self._pos_cache:
            return
        self._pos_cache = new_cache
        for w in self._pos_rows:
            w.destroy()
        self._pos_rows.clear()
        if not new_cache:
            self._pos_empty_label.pack(pady=30)
            return
        self._pos_empty_label.pack_forget()
        for idx, rd in enumerate(new_cache):
            self._pos_rows.append(_make_data_row(self._pos_scroll, rd, idx))

    def _pos_row_data(self, idx, p):
        symbol = _g(p, "symbol", "")
        side = _g(p, "side", None)
        entry_price = _g(p, "entry_price", 0) or 0
        current_price = _g(p, "current_price", 0) or entry_price
        leverage = _g(p, "leverage", 1) or 1
        size = _g(p, "size", 0) or 0
        margin = _g(p, "margin_usdt", 0) or 0
        entry_time = _g(p, "entry_time", 0) or 0
        from core.constants import OrderSide
        is_long = (side == OrderSide.BUY_LONG) if side else True
        dir_text = "^ LONG" if is_long else "v SHORT"
        dir_color = "#00E676" if is_long else "#FF5252"
        if entry_price > 0 and current_price > 0:
            roi = ((current_price - entry_price) / entry_price * 100 * leverage
                   if is_long else
                   (entry_price - current_price) / entry_price * 100 * leverage)
        else:
            roi = 0.0
        roi_color = "#00E676" if roi >= 0 else "#FF5252"
        if entry_time > 0:
            elapsed = time.time() - entry_time
            duration = f"{int(elapsed / 60)} dk" if elapsed < 3600 else f"{elapsed / 3600:.1f} sa"
        else:
            duration = "-"
        dc = "#CFD8DC"
        W = SM_POS_WIDTHS
        return [
            (idx, "#90A4AE", W[0]), (symbol, "#FFFFFF", W[1]),
            (dir_text, dir_color, W[2]),
            (f"{entry_price:.4f}" if entry_price < 1 else f"{entry_price:.2f}", dc, W[3]),
            (f"{current_price:.4f}" if current_price < 1 else f"{current_price:.2f}", roi_color, W[4]),
            (f"{roi:+.2f}%", roi_color, W[5]),
            (f"{leverage}x", dc, W[6]),
            (f"{size:.4f}", dc, W[7]),
            (f"${margin:.1f}", dc, W[8]),
            (duration, "#90A4AE", W[9]),
        ]

    # ═══════════════════════════════════════════════════
    #  DECISIONS TABLE
    # ═══════════════════════════════════════════════════
    def _set_dec_filter(self, key):
        self._dec_filter = key
        self._last_dec_count = -1
        for k, btn in self._filter_btns.items():
            btn.configure(fg_color=_TAB_ACTIVE if k == key else _TAB_INACTIVE)

    def _filter_decisions(self, decisions):
        f = self._dec_filter
        if f == "all":
            return decisions
        if f == "signals":
            skip = {"SİNYAL_YOK", "VERİ_YOK", "HATA"}
            return [d for d in decisions if d.get("action") not in skip]
        if f == "trades":
            keep = {"LONG_AÇ", "SHORT_AÇ", "KAPAT",
                     "REVERSE->LONG", "REVERSE->SHORT",
                     "LONG_BAŞARISIZ", "SHORT_BAŞARISIZ",
                     "KAPAT_BAŞARISIZ", "REVERSE_BAŞARISIZ"}
            return [d for d in decisions if d.get("action") in keep]
        return decisions

    def _update_dec_table(self, decisions):
        cur_f = self._dec_filter
        if len(decisions) == self._last_dec_count and cur_f == getattr(self, '_last_f', None):
            return
        self._last_dec_count = len(decisions)
        self._last_f = cur_f
        filtered = self._filter_decisions(decisions)
        recent = list(reversed(filtered[-100:]))
        for w in self._dec_rows:
            w.destroy()
        self._dec_rows.clear()
        for idx, d in enumerate(recent):
            self._dec_rows.append(_make_data_row(self._dec_scroll, self._dec_row_data(d), idx))

    def _dec_row_data(self, d):
        ts = d.get("time", 0)
        symbol = d.get("symbol", "")
        signal = d.get("signal", "")
        action = d.get("action", "")
        detail = d.get("detail", "")
        price = d.get("price", 0) or 0
        if ts > 0:
            lt = time.localtime(ts)
            time_str = f"{lt.tm_hour:02d}:{lt.tm_min:02d}:{lt.tm_sec:02d}"
        else:
            time_str = "-"
        sig_colors = {"BUY": "#00E676", "SELL": "#FF5252", "TARAMA": "#26C6DA"}
        sig_color = sig_colors.get(signal, "#CFD8DC")
        action_color = _ACTION_COLORS.get(action, "#CFD8DC")
        price_str = (f"{price:.4f}" if price < 1 else f"{price:.2f}") if price > 0 else "-"

        trend_str, adx_str, rsi_str, desc_str = "-", "-", "-", detail
        if "ADX:" in detail and "RSI:" in detail:
            try:
                parts = detail.split("|", 1)
                metrics = parts[0].strip()
                desc_str = parts[1].strip() if len(parts) > 1 else ""
                if metrics.startswith("^"):
                    trend_str = "^"
                elif metrics.startswith("v"):
                    trend_str = "v"
                elif metrics[0] in ("\u25b2", "\u25bc"):
                    trend_str = "^" if metrics[0] == "\u25b2" else "v"
                adx_i = metrics.find("ADX:") + 4
                adx_e = metrics.find(" ", adx_i)
                adx_str = metrics[adx_i: adx_e if adx_e != -1 else len(metrics)]
                rsi_i = metrics.find("RSI:") + 4
                rsi_e = metrics.find(" ", rsi_i)
                rsi_str = metrics[rsi_i: rsi_e if rsi_e != -1 else len(metrics)]
            except Exception:
                pass

        trend_color = "#00E676" if trend_str == "^" else "#FF5252" if trend_str == "v" else "#78909C"
        try:
            adx_color = "#FFD54F" if float(adx_str) > 25 else "#CFD8DC"
        except ValueError:
            adx_color = "#78909C"

        dc = "#CFD8DC"
        W = SM_DEC_WIDTHS
        return [
            (time_str, "#90A4AE", W[0]), (symbol, "#FFFFFF", W[1]),
            (signal, sig_color, W[2]), (action, action_color, W[3]),
            (price_str, dc, W[4]), (trend_str, trend_color, W[5]),
            (adx_str, adx_color, W[6]), (rsi_str, dc, W[7]),
            (desc_str, "#B0BEC5", W[8]),
        ]
