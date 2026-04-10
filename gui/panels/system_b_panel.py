"""System B Panel - Wave analysis scan results and positions.
Two tables: System B Scan Results + Active System B Positions."""
import customtkinter as ctk

# ═══ Column Layout: System B Scan Results ═══
SB_HEADERS = [
    "#", "Sinyal", "Sembol", "Skor",
    "Yon", "Rejim", "Guven",
    "G%", "I%", "I/G", "CV",
    "SL%", "Tetik%", "Trail%", "Lev", "R:R",
    "RSI", "Vol", "Mum", "Entry",
    "ER.Ma", "ER.Mi", "Hurst",
    "WavP", "ATR%", "FR", "Spread",
    "Red",
]
SB_WIDTHS = [
    22, 54, 90, 44,
    40, 60, 40,
    44, 44, 40, 40,
    44, 44, 44, 40, 40,
    40, 40, 36, 40,
    44, 44, 44,
    44, 44, 40, 44,
    80,
]

# Important columns (red border): G%, I%, SL%, R:R, Rejim, Entry
_SB_IMP = {7, 8, 11, 15, 5, 19}

# ═══ Column Layout: System B Positions ═══
SB_POS_HEADERS = [
    "", "Sinyal", "Sembol", "ROI%",
    "Lev", "G%", "Rejim",
    "SL%", "Acil", "Trail", "Kalan", "$",
]
SB_POS_WIDTHS = [
    22, 54, 90, 50,
    40, 44, 60,
    44, 44, 50, 48, 42,
]

# ═══ Color Constants ═══
_RED_BORDER = "#E53935"
_RED_BG = "#2a0f0f"

# Regime colors
_REGIME_COLORS = {
    "TREND": "#4FC3F7",
    "RANGING": "#CE93D8",
    "WEAK_TREND": "#81D4FA",
    "WEAK_RANGING": "#B39DDB",
    "UNDECIDED": "gray",
}

# Entry type display
_ENTRY_TYPE_MAP = {
    "WAIT": ("BEKLE", "gray"),
    "LIMIT_READY": ("HAZIR", "#FFD54F"),
    "LIMIT_ENTER": ("LIMIT", "#FF9800"),
    "MARKET_ENTER": ("GIRIS!", "#00E676"),
}


def _build_header(parent, headers, widths, important_set):
    """Build a header row with red-bordered boxes for important columns."""
    hdr = ctk.CTkFrame(parent, fg_color="transparent")
    hdr.pack(fill="x", padx=2)
    font = ctk.CTkFont(size=13, weight="bold")

    for col_idx, (h, w) in enumerate(zip(headers, widths)):
        hdr_color = "#7799BB"
        # Color coding by column group
        if h in ("G%", "I%", "I/G", "CV"):
            hdr_color = "#26C6DA"  # cyan - wave
        elif h in ("SL%", "Tetik%", "Trail%", "Lev", "R:R"):
            hdr_color = "#FF8A65"  # coral - risk
        elif h in ("RSI", "Vol", "Mum", "Entry"):
            hdr_color = "#FFD54F"  # yellow - entry
        elif h in ("ER.Ma", "ER.Mi", "Hurst"):
            hdr_color = "#4FC3F7"  # blue - regime
        elif h in ("Rejim", "Guven"):
            hdr_color = "#CE93D8"  # purple - regime
        elif h == "Red":
            hdr_color = "#FF5252"  # red

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


class SystemBPanel(ctk.CTkFrame):
    """System B scan results and positions panel."""

    def __init__(self, parent, controller):
        super().__init__(parent)
        self.controller = controller
        self.pack(fill="both", expand=True)
        self._build_ui()
        self._start_refresh()

    def _build_ui(self) -> None:
        # ═══ TABLE 1: SYSTEM B SCAN RESULTS ═══
        scan_frame = ctk.CTkFrame(self)
        scan_frame.pack(fill="both", expand=True, padx=3, pady=(1, 0))

        ctk.CTkLabel(scan_frame, text="\U0001F30A System B - Dalga Analizi",
                     font=ctk.CTkFont(size=12, weight="bold"),
                     text_color="#26C6DA").pack(anchor="w", padx=4, pady=(1, 0))

        _build_header(scan_frame, SB_HEADERS, SB_WIDTHS, _SB_IMP)

        self._scan_scroll = ctk.CTkScrollableFrame(scan_frame, height=400)
        self._scan_scroll.pack(fill="both", expand=True, padx=2)
        self._scan_rows = []
        self._scan_cache = []

        # ═══ TABLE 2: ACTIVE SYSTEM B POSITIONS ═══
        pos_frame = ctk.CTkFrame(self)
        pos_frame.pack(fill="x", padx=3, pady=(1, 2))

        ctk.CTkLabel(pos_frame, text="\U0001F4CA Aktif Pozisyonlar (System B)",
                     font=ctk.CTkFont(size=12, weight="bold"),
                     text_color="#FFD54F").pack(anchor="w", padx=4, pady=(1, 0))

        _build_header(pos_frame, SB_POS_HEADERS, SB_POS_WIDTHS, set())

        self._pos_scroll = ctk.CTkScrollableFrame(pos_frame, height=140)
        self._pos_scroll.pack(fill="x", padx=2)
        self._pos_rows = []
        self._pos_cache = []

    # ═══ Refresh ═══

    def _start_refresh(self):
        self._refresh()

    def _refresh(self):
        if self.winfo_viewable():
            try:
                self._update_scan_results()
            except Exception:
                pass
            try:
                self._update_positions()
            except Exception:
                pass
        self.after(4000, self._refresh)

    # ═══ Generic row helpers (same pattern as scanner_panel) ═══

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
        results = self.controller.get_system_b_results()
        if not results:
            self._ensure_rows(self._scan_scroll, self._scan_rows,
                              self._scan_cache, SB_WIDTHS, 1)
            sb_enabled = self.controller.config.get("system_b.enabled", False)
            if sb_enabled:
                msg = "Tarama sonucu yok (filtreler gecemiyor)"
            else:
                msg = "System B devre disi"
            empty = [(msg, "gray")] + [("", "gray")] * (len(SB_WIDTHS) - 1)
            self._update_row(self._scan_rows, self._scan_cache, 0, empty)
            return

        n = min(len(results), 30)
        self._ensure_rows(self._scan_scroll, self._scan_rows,
                          self._scan_cache, SB_WIDTHS, n)

        for i, r in enumerate(results[:n]):
            vals = self._build_scan_row(i, r)
            bg = "#1c2d4d" if i % 2 == 0 else "transparent"
            self._update_row(self._scan_rows, self._scan_cache, i, vals, bg)

    def _build_scan_row(self, i, r):
        """Build row values for a SystemBScanResult."""
        # Score color
        score_color = "#00C853" if r.score > 0 else "#FF1744" if r.score < 0 else "gray"
        eligible_marker = "*" if r.eligible else ""
        row_color = score_color if r.eligible else "gray"

        # Signal
        sig_text, sig_color = _ENTRY_TYPE_MAP.get(r.entry_type, ("--", "gray"))
        if r.eligible and r.direction:
            arrow = "\u25B2" if r.direction == "LONG" else "\u25BC"
            sig_text = f"{sig_text} {arrow}"

        # Direction
        dir_text = r.direction if r.direction else "--"
        dir_color = "#00C853" if r.direction == "LONG" else "#FF1744" if r.direction == "SHORT" else "gray"

        # Regime
        regime_str = r.regime.regime if r.regime else "?"
        regime_short = {
            "TREND": "TREND", "RANGING": "RANGE",
            "WEAK_TREND": "W.TRD", "WEAK_RANGING": "W.RNG",
            "UNDECIDED": "???",
        }.get(regime_str, regime_str[:6])
        regime_color = _REGIME_COLORS.get(regime_str, "gray")

        # Confidence
        conf = r.regime.confidence if r.regime else 0
        conf_color = "#00C853" if conf >= 0.8 else "#FFD54F" if conf >= 0.5 else "gray"

        # Wave: G, I, I/G, CV
        g_val = r.G
        i_val = r.I
        ig_ratio = i_val / g_val if g_val > 0 else 0
        cv_val = r.waves.cv if r.waves else 0

        g_color = "#FF9800" if g_val > 3 else "#26C6DA" if g_val > 0 else "gray"
        i_color = "#FF9800" if i_val > 3 else "#26C6DA" if i_val > 0 else "gray"
        ig_color = "#00C853" if ig_ratio > 1.5 else "#FFD54F" if ig_ratio > 1 else "#FF1744" if ig_ratio > 0 else "gray"
        cv_color = "#FF1744" if cv_val > 0.6 else "#FFD54F" if cv_val > 0.4 else "#00C853" if cv_val > 0 else "gray"

        # Risk: SL%, Tetik%, Trail%, Lev, R:R
        sl_color = "#FF1744" if r.sl_pct > 5 else "#FF9800" if r.sl_pct > 3 else "#00C853" if r.sl_pct > 0 else "gray"
        tetik_color = "#FF9800" if r.trailing_trigger_pct > 0 else "gray"
        trail_color = "#2196F3" if r.trailing_callback_pct > 0 else "gray"
        lev_color = "#FF9800" if r.leverage > 20 else "#FFD54F" if r.leverage > 10 else "white"
        rr_color = "#00C853" if r.expected_rr > 1.5 else "#FFD54F" if r.expected_rr > 1 else "#FF1744" if r.expected_rr > 0 else "gray"

        # Entry teyit: RSI, Vol, Mum, Entry score
        rsi_val = r.entry.rsi_value if r.entry else 50
        rsi_color = "#CE93D8" if rsi_val < 35 or rsi_val > 65 else "#2196F3" if rsi_val < 45 or rsi_val > 55 else "gray"
        vol_ratio = r.entry.volume_ratio if r.entry else 1
        vol_color = "#00C853" if vol_ratio > 1.5 else "#FFD54F" if vol_ratio > 1.2 else "gray"
        candle_ok = r.entry.candle_ok if r.entry else False
        candle_str = "\u2713" if candle_ok else "\u2717"
        candle_color = "#00C853" if candle_ok else "#FF1744"
        entry_score = r.entry.score if r.entry else 0
        entry_color = "#00C853" if entry_score >= 3 else "#FFD54F" if entry_score >= 2 else "#FF1744"

        # ER Macro, ER Micro, Hurst
        er_ma = r.regime.er_macro if r.regime else 0
        er_mi = r.regime.er_micro if r.regime else 0
        hurst = r.regime.hurst if r.regime else 0.5

        er_ma_color = "#4FC3F7" if er_ma > 0.35 else "#CE93D8" if er_ma < 0.15 else "gray"
        er_mi_color = "#4FC3F7" if er_mi > 0.40 else "#CE93D8" if er_mi < 0.20 else "gray"
        hurst_color = "#4FC3F7" if hurst > 0.55 else "#CE93D8" if hurst < 0.45 else "gray"

        # Wave position
        wp = r.wave_position
        wp_color = "#00C853" if wp > 0.6 else "#FFD54F" if wp > 0.3 else "#2196F3"

        # ATR%
        atr_pct = r.atr_percent
        atr_color = "#FF9800" if atr_pct > 0.5 else "#2196F3" if atr_pct > 0 else "gray"

        # FR
        fr_pct = r.funding_rate * 100
        fr_str = f"{fr_pct:+.2f}" if r.funding_rate != 0 else "--"
        fr_color = "#FF1744" if fr_pct > 0.05 else "#00C853" if fr_pct < -0.05 else "gray"

        # Spread
        spread_color = "#FF1744" if r.spread_pct > 0.1 else "gray"

        # Reject
        reject_short = r.reject_reason[:12] if r.reject_reason else ""

        return [
            (f"{i + 1}", "gray"),
            (sig_text, sig_color),
            (f"{r.symbol}{eligible_marker}", row_color),
            (f"{r.score:+.0f}", score_color),
            (dir_text, dir_color),
            (regime_short, regime_color),
            (f"{conf:.0%}", conf_color),
            (f"{g_val:.2f}", g_color),
            (f"{i_val:.2f}", i_color),
            (f"{ig_ratio:.1f}", ig_color),
            (f"{cv_val:.2f}", cv_color),
            (f"{r.sl_pct:.1f}", sl_color),
            (f"{r.trailing_trigger_pct:.1f}", tetik_color),
            (f"{r.trailing_callback_pct:.1f}", trail_color),
            (f"{r.leverage}x" if r.leverage > 0 else "--", lev_color),
            (f"{r.expected_rr:.1f}" if r.expected_rr > 0 else "--", rr_color),
            (f"{rsi_val:.0f}", rsi_color),
            (f"{vol_ratio:.1f}", vol_color),
            (candle_str, candle_color),
            (f"{entry_score}/3", entry_color),
            (f"{er_ma:.2f}", er_ma_color),
            (f"{er_mi:.2f}", er_mi_color),
            (f"{hurst:.2f}", hurst_color),
            (f"{wp:.0%}", wp_color),
            (f"{atr_pct:.2f}%", atr_color),
            (fr_str, fr_color),
            (f"{r.spread_pct:.2f}" if r.spread_pct > 0 else "--", spread_color),
            (reject_short, "#FF5252" if reject_short else "gray"),
        ]

    # ═══ TABLE 2: Positions ═══

    def _update_positions(self):
        all_positions = self.controller.get_all_scanner_positions()
        # Filter to System B positions only
        positions = [p for p in all_positions if p.get("entry_mode") == "SYSTEM_B"]

        if not positions:
            self._ensure_rows(self._pos_scroll, self._pos_rows,
                              self._pos_cache, SB_POS_WIDTHS, 1)
            empty = [("System B pozisyon yok", "gray")] + [("", "gray")] * (len(SB_POS_WIDTHS) - 1)
            self._update_row(self._pos_rows, self._pos_cache, 0, empty)
            return

        n = len(positions)
        self._ensure_rows(self._pos_scroll, self._pos_rows,
                          self._pos_cache, SB_POS_WIDTHS, n)

        for idx, pos in enumerate(positions):
            vals = self._build_pos_row(idx, pos)
            self._update_row(self._pos_rows, self._pos_cache, idx, vals)

    def _build_pos_row(self, idx, pos):
        """Build row values for a System B position."""
        symbol = pos.get("symbol", "--")
        side = pos.get("side", "--")
        is_long = "Buy" in side
        entry = pos.get("entry_price", 0)
        sl = pos.get("sl", 0)
        emergency = pos.get("emergency_price", 0)
        lev = pos.get("leverage", 1)
        margin = pos.get("margin_usdt", 0)
        roi = pos.get("roi_percent", 0)
        hold_sec = pos.get("hold_seconds", 0)
        g_val = pos.get("entry_bb_width", 0)  # G stored in entry_bb_width

        # Signal
        side_short = "L" if is_long else "S"
        side_arrow = "\u25B2" if is_long else "\u25BC"
        sig_color = "#00C853" if is_long else "#FF1744"

        # ROI
        roi_color = "#00C853" if roi > 0 else "#FF1744" if roi < 0 else "white"

        # Leverage
        lev_color = "#FF9800" if lev > 20 else "#FFD54F" if lev > 10 else "white"

        # G%
        g_color = "#26C6DA" if g_val > 0 else "gray"

        # Regime (from entry data, approximate)
        regime_str = "SB"
        regime_color = "#26C6DA"

        # SL distance
        cur_price = entry  # fallback
        if sl > 0 and cur_price > 0:
            sl_dist = ((cur_price - sl) / cur_price * 100) if is_long else \
                      ((sl - cur_price) / cur_price * 100)
        else:
            sl_dist = 99
        sl_color = "#FF1744" if sl_dist < 0.1 else "#FF9800" if sl_dist < 0.2 else "#00C853"

        # Emergency distance
        if emergency > 0 and cur_price > 0:
            em_dist = ((cur_price - emergency) / cur_price * 100) if is_long else \
                      ((emergency - cur_price) / cur_price * 100)
        else:
            em_dist = 99
        em_color = "#FF1744" if em_dist < 0.1 else "#FF9800" if em_dist < 0.3 else "gray"

        # Trailing
        if pos.get("trailing_active"):
            trailing = pos.get("trailing", 0)
            if trailing > 0 and cur_price > 0:
                if is_long:
                    trail_dist = (cur_price - trailing) / cur_price * 100
                else:
                    trail_dist = (trailing - cur_price) / cur_price * 100
                trail_str = f"{trail_dist:.2f}%"
                trail_color = "#FF9800" if trail_dist < 0.15 else "#00C853"
            else:
                trail_str, trail_color = "aktif", "#00C853"
        else:
            trail_str, trail_color = "bekle", "gray"

        # Time remaining
        strat = self.controller.config.get("strategy", {})
        time_limit = strat.get("time_limit_minutes", 480) * 60
        remaining = max(0, time_limit - hold_sec)
        rem_h = int(remaining // 3600)
        rem_m = int((remaining % 3600) // 60)
        if strat.get("time_limit_enabled", True):
            time_str = f"{rem_h}s{rem_m:02d}d"
            time_color = "#FF1744" if remaining < 600 else "#FF9800" if remaining < 1800 else "white"
        else:
            time_str, time_color = "--", "gray"

        return [
            ("", "#1a1a2e"),
            (f"{side_arrow}{side_short}", sig_color),
            (f"{symbol} SB", "#26C6DA"),
            (f"{roi:+.1f}%", roi_color),
            (f"{lev}x", lev_color),
            (f"{g_val:.2f}%" if g_val > 0 else "--", g_color),
            (regime_str, regime_color),
            (f"{sl_dist:.1f}%", sl_color),
            (f"{em_dist:.1f}%", em_color),
            (trail_str, trail_color),
            (time_str, time_color),
            (f"${margin:.1f}", "white"),
        ]
