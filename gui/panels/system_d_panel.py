"""System D Panel — Sıralı Coin Analiz & Trade Tablosu.

Top 50 coin hacim sıralı tarama sonuçları + aktif pozisyonlar.
"""
import customtkinter as ctk

# ═══ Column Layout: System D Scan Results ═══
SD_HEADERS = [
    "#", "Sinyal", "Sembol", "Hacim",
    "Yon", "Guc", "Rejim",
    "Zoom", "G%", "SL%", "TP%", "Lev",
    "Trail.T", "Trail.M",
    "RSI", "ADX", "ER",
    "FR", "Giris",
    "Red",
]
SD_WIDTHS = [
    24, 54, 86, 64,
    40, 48, 52,
    44, 44, 44, 44, 40,
    48, 48,
    40, 40, 40,
    44, 60,
    90,
]

# Important columns (red border): G%, SL%, Lev, Rejim, Zoom
_SD_IMP = {7, 8, 9, 11, 6}

# ═══ Column Layout: System D Positions ═══
SD_POS_HEADERS = [
    "", "Sinyal", "Sembol", "ROI%",
    "Lev", "G%", "Rejim",
    "SL%", "TP%", "Trail", "Kalan", "$",
]
SD_POS_WIDTHS = [
    22, 54, 90, 50,
    40, 44, 52,
    44, 44, 50, 48, 42,
]

# ═══ Color Constants ═══
_RED_BORDER = "#E53935"
_RED_BG = "#2a0f0f"

_REGIME_COLORS = {
    "TREND": "#4FC3F7",
    "RANGING": "#CE93D8",
    "GREY": "#FFD54F",
    "UNKNOWN": "gray",
}

_STRENGTH_COLORS = {
    "STRONG": "#00E676",
    "MODERATE": "#FFD54F",
    "WEAK": "gray",
}


def _build_header(parent, headers, widths, important_set):
    """Build a header row with red-bordered boxes for important columns."""
    hdr = ctk.CTkFrame(parent, fg_color="transparent")
    hdr.pack(fill="x", padx=2)
    font = ctk.CTkFont(size=13, weight="bold")

    for col_idx, (h, w) in enumerate(zip(headers, widths)):
        # Color coding by column group
        if h in ("G%", "SL%", "TP%", "Lev", "Trail.T", "Trail.M"):
            hdr_color = "#FF8A65"   # coral - risk
        elif h in ("Yon", "Guc"):
            hdr_color = "#00E676"   # green - direction
        elif h in ("Rejim", "ADX", "ER"):
            hdr_color = "#CE93D8"   # purple - regime
        elif h == "Zoom":
            hdr_color = "#26C6DA"   # cyan - zoom
        elif h in ("RSI", "FR"):
            hdr_color = "#FFD54F"   # yellow
        elif h in ("Hacim",):
            hdr_color = "#4FC3F7"   # blue
        elif h == "Red":
            hdr_color = "#FF5252"
        elif h == "Giris":
            hdr_color = "#26C6DA"
        else:
            hdr_color = "#7799BB"

        if col_idx in important_set:
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


class SystemDPanel(ctk.CTkFrame):
    """System D scan results and positions panel."""

    def __init__(self, parent, controller):
        super().__init__(parent)
        self.controller = controller
        self.pack(fill="both", expand=True)
        self._build_ui()
        self._start_refresh()

    def _build_ui(self) -> None:
        # ═══ TABLE 1: SYSTEM D SCAN RESULTS ═══
        scan_frame = ctk.CTkFrame(self)
        scan_frame.pack(fill="both", expand=True, padx=3, pady=(1, 0))

        ctk.CTkLabel(scan_frame, text="System D - Sirali Coin Analizi",
                     font=ctk.CTkFont(size=12, weight="bold"),
                     text_color="#FF8A65").pack(anchor="w", padx=4, pady=(1, 0))

        _build_header(scan_frame, SD_HEADERS, SD_WIDTHS, _SD_IMP)

        self._scan_scroll = ctk.CTkScrollableFrame(scan_frame, height=400)
        self._scan_scroll.pack(fill="both", expand=True, padx=2)
        self._scan_rows = []
        self._scan_cache = []

        # ═══ TABLE 2: ACTIVE SYSTEM D POSITIONS ═══
        pos_frame = ctk.CTkFrame(self)
        pos_frame.pack(fill="x", padx=3, pady=(1, 2))

        ctk.CTkLabel(pos_frame, text="Aktif Pozisyonlar (System D)",
                     font=ctk.CTkFont(size=12, weight="bold"),
                     text_color="#FFD54F").pack(anchor="w", padx=4, pady=(1, 0))

        _build_header(pos_frame, SD_POS_HEADERS, SD_POS_WIDTHS, set())

        self._pos_scroll = ctk.CTkScrollableFrame(pos_frame, height=140)
        self._pos_scroll.pack(fill="x", padx=2)
        self._pos_rows = []
        self._pos_cache = []

    # ═══ Refresh ═══

    def _start_refresh(self):
        self._refresh()

    def _refresh(self):
        try:
            self._update_scan_results()
        except Exception as e:
            from loguru import logger
            logger.error(f"[SysD Panel] scan refresh error: {e}")
        try:
            self._update_positions()
        except Exception as e:
            from loguru import logger
            logger.error(f"[SysD Panel] pos refresh error: {e}")
        self.after(4000, self._refresh)

    # ═══ Generic row helpers ═══

    def _ensure_rows(self, scroll_frame, rows_list, cache_list, widths, count):
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
        if idx >= len(cache_list) or cache_list[idx] == vals:
            return
        cache_list[idx] = vals
        frame, labels = rows_list[idx]
        if bg is not None and frame.cget("fg_color") != bg:
            frame.configure(fg_color=bg)
        for lbl, (val, color) in zip(labels, vals):
            lbl.configure(text=val, text_color=color)

    # ═══ TABLE 1: Scan Results ═══

    def _update_scan_results(self):
        results = self.controller.get_system_d_results()
        if not results:
            self._ensure_rows(self._scan_scroll, self._scan_rows,
                              self._scan_cache, SD_WIDTHS, 1)
            sd_enabled = self.controller.config.get("system_d.enabled", False)
            if sd_enabled:
                msg = "Tarama sonucu yok"
            else:
                msg = "System D devre disi"
            empty = [(msg, "gray")] + [("", "gray")] * (len(SD_WIDTHS) - 1)
            self._update_row(self._scan_rows, self._scan_cache, 0, empty)
            return

        n = min(len(results), 50)
        self._ensure_rows(self._scan_scroll, self._scan_rows,
                          self._scan_cache, SD_WIDTHS, n)

        for i, r in enumerate(results[:n]):
            try:
                vals = self._build_scan_row(i, r)
            except Exception as e:
                from loguru import logger
                logger.error(f"[SysD Panel] row build error #{i} {getattr(r, 'symbol', '?')}: {e}")
                vals = [(str(i), "gray")] + [("ERR", "#FF5252")] * (len(SD_WIDTHS) - 1)
            bg = "#1c2d4d" if i % 2 == 0 else "transparent"
            self._update_row(self._scan_rows, self._scan_cache, i, vals, bg)

    def _build_scan_row(self, i, r):
        """Build row values for a SystemDScanResult."""
        # Score/eligible color
        row_color = "#00C853" if r.eligible else "gray"

        # Signal
        if r.eligible and r.direction:
            arrow = "\u25B2" if r.direction == "LONG" else "\u25BC"
            sig_text = f"GIRIS {arrow}"
            sig_color = "#00E676" if r.direction == "LONG" else "#FF1744"
        else:
            sig_text = "---"
            sig_color = "gray"

        # Direction
        dir_text = r.direction if r.direction else "--"
        dir_color = "#00C853" if r.direction == "LONG" else "#FF1744" if r.direction == "SHORT" else "gray"

        # Strength
        strength = r.direction_result.strength if r.direction_result else "WEAK"
        str_color = _STRENGTH_COLORS.get(strength, "gray")
        str_short = {"STRONG": "GUC", "MODERATE": "ORTA", "WEAK": "ZYF"}.get(strength, "?")

        # Regime
        regime = r.regime if r.regime else "?"
        regime_color = _REGIME_COLORS.get(regime, "gray")
        regime_short = {"TREND": "TRND", "RANGING": "RANG", "GREY": "GRI"}.get(regime, regime[:4])

        # Volume
        vol = r.volume_24h
        if vol >= 1e9:
            vol_str = f"{vol/1e9:.1f}B"
        elif vol >= 1e6:
            vol_str = f"{vol/1e6:.0f}M"
        else:
            vol_str = f"{vol/1e3:.0f}K"

        # Zoom TF
        zoom_tf = r.zoom.optimal_tf if r.zoom else "?"
        zoom_color = "#26C6DA"

        # G, SL, TP, Leverage
        lc = r.leverage_calc
        g_str = f"{lc.G:.2f}" if lc.G > 0 else "--"
        sl_str = f"{r.sl_pct:.2f}" if r.sl_pct > 0 else "--"
        tp_str = f"{r.tp_pct:.2f}" if r.tp_pct > 0 else "TRL"
        lev_str = f"{r.leverage}x" if r.leverage > 1 else "--"

        # Trailing
        trail_t = f"{r.trailing_trigger_pct:.2f}" if r.trailing_trigger_pct > 0 else "--"
        trail_m = f"{r.trailing_callback_pct:.2f}" if r.trailing_callback_pct > 0 else "--"

        # RSI (mikro)
        rsi_val = r.direction_result.micro.rsi_value if r.direction_result and r.direction_result.micro else 50
        rsi_str = f"{rsi_val:.0f}"
        rsi_color = "#00C853" if 40 < rsi_val < 60 else "#FFD54F" if 30 < rsi_val < 70 else "#FF5252"

        # ADX
        adx = r.regime_result.adx if r.regime_result else 0
        adx_str = f"{adx:.0f}"

        # ER
        er = r.regime_result.er if r.regime_result else 0
        er_str = f"{er:.2f}"

        # Funding rate
        fr = r.funding_rate
        fr_str = f"{fr*100:.3f}" if fr != 0 else "0"
        fr_color = "#FF5252" if abs(fr) > 0.0005 else "gray"

        # Entry price
        entry_str = f"{r.entry_price:.4f}" if r.entry_price > 0 else "--"
        if r.entry_price > 100:
            entry_str = f"{r.entry_price:.2f}"
        elif r.entry_price > 1:
            entry_str = f"{r.entry_price:.3f}"

        # Reject reason
        reject = r.reject_reason if r.reject_reason else ""

        return [
            (str(r.rank), row_color),
            (sig_text, sig_color),
            (r.symbol.replace("USDT", ""), row_color),
            (vol_str, "#4FC3F7"),
            (dir_text, dir_color),
            (str_short, str_color),
            (regime_short, regime_color),
            (zoom_tf, zoom_color),
            (g_str, "#26C6DA"),
            (sl_str, "#FF8A65"),
            (tp_str, "#FF8A65"),
            (lev_str, "#FFD54F"),
            (trail_t, "gray"),
            (trail_m, "gray"),
            (rsi_str, rsi_color),
            (adx_str, "#CE93D8"),
            (er_str, "#CE93D8"),
            (fr_str, fr_color),
            (entry_str, "#26C6DA"),
            (reject, "#FF5252" if reject else "gray"),
        ]

    # ═══ TABLE 2: Positions ═══

    def _update_positions(self):
        all_positions = self.controller.get_all_scanner_positions()
        sd_positions = [p for p in all_positions if p.get("entry_mode") == "SYSTEM_D"]

        if not sd_positions:
            self._ensure_rows(self._pos_scroll, self._pos_rows,
                              self._pos_cache, SD_POS_WIDTHS, 1)
            empty = [("Pozisyon yok", "gray")] + [("", "gray")] * (len(SD_POS_WIDTHS) - 1)
            self._update_row(self._pos_rows, self._pos_cache, 0, empty)
            return

        n = len(sd_positions)
        self._ensure_rows(self._pos_scroll, self._pos_rows,
                          self._pos_cache, SD_POS_WIDTHS, n)

        for i, pos in enumerate(sd_positions):
            vals = self._build_pos_row(pos)
            bg = "#1c2d4d" if i % 2 == 0 else "transparent"
            self._update_row(self._pos_rows, self._pos_cache, i, vals, bg)

    def _build_pos_row(self, pos):
        """Build row values for an active System D position."""
        side = pos.get("side", "")
        symbol = pos.get("symbol", "?").replace("USDT", "")
        roi = pos.get("roi_percent", 0)
        roi_color = "#00C853" if roi > 0 else "#FF1744" if roi < 0 else "gray"

        # Signal indicator
        arrow = "\u25B2" if "LONG" in side else "\u25BC"
        sig_color = "#00C853" if "LONG" in side else "#FF1744"

        lev = pos.get("leverage", 1)
        g_pct = pos.get("entry_bb_width", 0)  # G stored in bb_width field

        # Regime: entry_mode'dan sonra gelen regime bilgisi
        # position_manager _pos_info'da regime yok, entry_adx'ten tahmin et
        entry_adx = pos.get("entry_adx", 0)
        if entry_adx > 25:
            regime = "TREND"
        elif entry_adx < 20:
            regime = "RANGING"
        else:
            regime = "GREY"
        regime_color = _REGIME_COLORS.get(regime, "gray")

        # SL: entry_price ve sl fiyatından hesapla
        entry_price = pos.get("entry_price", 0)
        sl_price = pos.get("sl", 0)
        if entry_price > 0 and sl_price > 0:
            sl_pct = abs(entry_price - sl_price) / entry_price * 100
        else:
            sl_pct = 0

        # TP: tp fiyatından hesapla
        tp_price = pos.get("tp", 0)
        if entry_price > 0 and tp_price > 0:
            tp_pct = abs(tp_price - entry_price) / entry_price * 100
        else:
            tp_pct = 0

        trailing = pos.get("trailing_active", False)

        # Elapsed time
        hold_seconds = pos.get("hold_seconds", 0)
        elapsed_min = hold_seconds / 60
        time_limit = 480  # default
        remaining = max(0, time_limit - elapsed_min)

        # PnL USDT
        margin = pos.get("margin_usdt", 0)
        pnl = margin * roi / 100 if margin > 0 else 0
        pnl_color = "#00C853" if pnl > 0 else "#FF1744" if pnl < 0 else "gray"

        return [
            ("", "gray"),
            (f"{arrow}", sig_color),
            (symbol, sig_color),
            (f"{roi:+.2f}", roi_color),
            (f"{lev}x", "#FFD54F"),
            (f"{g_pct:.2f}", "#26C6DA"),
            (regime[:4], regime_color),
            (f"{sl_pct:.2f}", "#FF8A65"),
            (f"{tp_pct:.2f}" if tp_pct > 0 else "TRL", "#FF8A65"),
            ("ON" if trailing else "OFF", "#00E676" if trailing else "gray"),
            (f"{remaining:.0f}m", "gray"),
            (f"{pnl:+.2f}", pnl_color),
        ]
