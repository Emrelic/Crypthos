"""System H Panel — Hibrit Sistem Tarama & Pozisyon Tablosu.

A temel + B dalga + D zoom + F istatistik entegrasyonu.
"""
import customtkinter as ctk

# ═══ Column Layout: System H Scan Results ═══
SH_HEADERS = [
    "#", "Sinyal", "Sembol", "Skor",
    "Yon", "Rejim", "Zoom",
    "G%", "SL%", "Lev", "Opt",
    "Trail.T", "Trail.M",
    "ER.M", "Hurst", "P(w)", "EV%",
    "RSI", "ADX", "FR",
    "Red",
]
SH_WIDTHS = [
    24, 54, 86, 44,
    40, 52, 44,
    44, 44, 40, 38,
    48, 48,
    44, 44, 44, 44,
    40, 40, 44,
    90,
]

# Important columns: G%, SL%, Lev, Rejim, P(w), EV%
_SH_IMP = {7, 8, 9, 5, 15, 16}

# ═══ Column Layout: System H Positions ═══
SH_POS_HEADERS = [
    "", "Sinyal", "Sembol", "ROI%",
    "Lev", "G%", "Rejim",
    "SL%", "Trail", "Kalan", "$",
]
SH_POS_WIDTHS = [
    22, 54, 90, 50,
    40, 44, 52,
    44, 50, 48, 42,
]

# ═══ Colors ═══
_RED_BORDER = "#E53935"
_RED_BG = "#2a0f0f"

_ZONE_COLORS = {
    "TRENDING": "#4FC3F7",
    "RANGING": "#CE93D8",
    "GRAY": "#FFD54F",
    "": "gray",
}


def _build_header(parent, headers, widths, important_set):
    """Build a header row with red-bordered boxes for important columns."""
    hdr = ctk.CTkFrame(parent, fg_color="transparent")
    hdr.pack(fill="x", padx=2)
    font = ctk.CTkFont(size=13, weight="bold")

    for col_idx, (h, w) in enumerate(zip(headers, widths)):
        if h in ("G%", "SL%", "Lev", "Opt", "Trail.T", "Trail.M"):
            hdr_color = "#FF8A65"   # coral - risk
        elif h in ("Yon",):
            hdr_color = "#00E676"   # green
        elif h in ("Rejim", "ER.M", "Hurst"):
            hdr_color = "#CE93D8"   # purple - regime
        elif h in ("P(w)", "EV%"):
            hdr_color = "#00BCD4"   # teal - statistics
        elif h == "Zoom":
            hdr_color = "#26C6DA"   # cyan
        elif h in ("RSI", "FR", "Skor"):
            hdr_color = "#FFD54F"   # yellow
        elif h == "Red":
            hdr_color = "#FF5252"
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


class SystemHPanel(ctk.CTkFrame):
    """System H scan results and positions panel."""

    def __init__(self, parent, controller):
        super().__init__(parent)
        self.controller = controller
        self.pack(fill="both", expand=True)
        self._build_ui()
        self._start_refresh()

    def _build_ui(self) -> None:
        # ═══ TABLE 1: SCAN RESULTS ═══
        scan_frame = ctk.CTkFrame(self)
        scan_frame.pack(fill="both", expand=True, padx=3, pady=(1, 0))

        ctk.CTkLabel(scan_frame, text="System H - Hibrit (A+B+D+F+G)",
                     font=ctk.CTkFont(size=12, weight="bold"),
                     text_color="#00BCD4").pack(anchor="w", padx=4, pady=(1, 0))

        _build_header(scan_frame, SH_HEADERS, SH_WIDTHS, _SH_IMP)

        self._scan_scroll = ctk.CTkScrollableFrame(scan_frame, height=400)
        self._scan_scroll.pack(fill="both", expand=True, padx=2)
        self._scan_rows = []
        self._scan_cache = []

        # ═══ TABLE 2: ACTIVE POSITIONS ═══
        pos_frame = ctk.CTkFrame(self)
        pos_frame.pack(fill="x", padx=3, pady=(1, 2))

        ctk.CTkLabel(pos_frame, text="Aktif Pozisyonlar (System H)",
                     font=ctk.CTkFont(size=12, weight="bold"),
                     text_color="#FFD54F").pack(anchor="w", padx=4, pady=(1, 0))

        _build_header(pos_frame, SH_POS_HEADERS, SH_POS_WIDTHS, set())

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
            logger.error(f"[SysH Panel] scan refresh error: {e}")
        try:
            self._update_positions()
        except Exception as e:
            from loguru import logger
            logger.error(f"[SysH Panel] pos refresh error: {e}")
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
        results = self.controller.get_system_h_results()
        if not results:
            self._ensure_rows(self._scan_scroll, self._scan_rows,
                              self._scan_cache, SH_WIDTHS, 1)
            sh_enabled = self.controller.config.get("system_h.enabled", False)
            msg = "Tarama sonucu yok" if sh_enabled else "System H devre disi"
            empty = [(msg, "gray")] + [("", "gray")] * (len(SH_WIDTHS) - 1)
            self._update_row(self._scan_rows, self._scan_cache, 0, empty)
            return

        n = min(len(results), 50)
        self._ensure_rows(self._scan_scroll, self._scan_rows,
                          self._scan_cache, SH_WIDTHS, n)

        for i, r in enumerate(results[:n]):
            try:
                vals = self._build_scan_row(i, r)
            except Exception:
                vals = [(str(i), "gray")] + [("ERR", "#FF5252")] * (len(SH_WIDTHS) - 1)
            bg = "#1c2d4d" if i % 2 == 0 else "transparent"
            self._update_row(self._scan_rows, self._scan_cache, i, vals, bg)

    def _build_scan_row(self, i, r):
        """Build row values for a SystemHScanResult."""
        row_color = "#00C853" if r.eligible else "gray"

        # Signal
        if r.eligible and r.direction:
            arrow = "\u25B2" if r.direction == "LONG" else "\u25BC"
            sig_text = f"GIRIS {arrow}"
            sig_color = "#00E676" if r.direction == "LONG" else "#FF1744"
        else:
            sig_text = "---"
            sig_color = "gray"

        # Score
        score_str = f"{r.score:+.0f}"
        score_color = "#00E676" if abs(r.score) >= 70 else "#FFD54F" if abs(r.score) >= 50 else "gray"

        # Direction
        dir_text = r.direction if r.direction else "--"
        dir_color = "#00C853" if r.direction == "LONG" else "#FF1744" if r.direction == "SHORT" else "gray"

        # Regime zone (ER+Hurst)
        zone = getattr(r, 'regime_zone', '') or ''
        zone_color = _ZONE_COLORS.get(zone, "gray")
        zone_short = {"TRENDING": "TRND", "RANGING": "RANG", "GRAY": "GRI"}.get(zone, zone[:4] if zone else "--")

        # Zoom TF
        zoom_tf = r.zoom.optimal_tf if r.zoom and r.zoom.optimal_G > 0 else "--"

        # G, SL, Leverage
        g_str = f"{r.G:.2f}" if r.G > 0 else "--"
        sl_str = f"{r.sl_pct:.2f}" if r.sl_pct > 0 else "--"
        lev_str = f"{r.leverage}x" if r.leverage > 1 else "--"

        # Trailing
        trail_t = f"{r.trailing_trigger_pct:.2f}" if r.trailing_trigger_pct > 0 else "--"
        trail_m = f"{r.trailing_callback_pct:.2f}" if r.trailing_callback_pct > 0 else "--"

        # ER macro, Hurst
        regime_h = getattr(r, 'regime_h', None)
        er_m = f"{regime_h.er_macro:.2f}" if regime_h and regime_h.er_macro > 0 else "--"
        hurst = f"{regime_h.hurst:.2f}" if regime_h and regime_h.hurst != 0.5 else "--"
        er_color = "#4FC3F7" if regime_h and regime_h.er_macro > 0.35 else "#CE93D8" if regime_h and regime_h.er_macro < 0.15 else "gray"
        hurst_color = "#4FC3F7" if regime_h and regime_h.hurst > 0.55 else "#CE93D8" if regime_h and regime_h.hurst < 0.45 else "gray"

        # P(win), EV
        prob = getattr(r, 'probability', None)
        pw_str = f"{prob.p_win:.0%}" if prob and prob.sufficient else "--"
        ev_str = f"{prob.ev_pct:+.0f}" if prob and prob.sufficient else "--"
        pw_color = "#00E676" if prob and prob.p_win > 0.6 else "#FFD54F" if prob and prob.p_win > 0.4 else "gray"
        ev_color = "#00E676" if prob and prob.ev_pct > 10 else "#FF5252" if prob and prob.ev_pct < -5 else "gray"

        # RSI, ADX
        rsi_str = f"{r.rsi:.0f}"
        rsi_color = "#00C853" if 40 < r.rsi < 60 else "#FFD54F" if 30 < r.rsi < 70 else "#FF5252"
        adx_str = f"{r.adx:.0f}"

        # FR
        fr_pct = r.funding_rate * 100 if r.funding_rate != 0 else 0
        fr_str = f"{fr_pct:+.3f}" if fr_pct != 0 else "--"
        fr_color = "#FF5252" if abs(fr_pct) > 0.05 else "gray"

        # Reject reason
        rej = r.reject_reason or ""
        rej_short = rej[:12] if rej else "-"
        rej_color = "#FF5252" if rej else "#00C853"

        # Optimizer status
        opt_st = getattr(r, 'opt_status', 'NONE') or 'NONE'
        opt_map = {"NONE": ("--", "gray"), "PENDING": ("...", "#FFD54F"),
                   "CACHED": ("C", "#26C6DA"), "FRESH": ("F", "#00E676")}
        opt_text, opt_color = opt_map.get(opt_st, ("--", "gray"))
        if getattr(r, 'opt_blended', False):
            opt_text = "B"
            opt_color = "#00E676"

        return [
            (str(i + 1), row_color),
            (sig_text, sig_color),
            (r.symbol.replace("USDT", ""), row_color),
            (score_str, score_color),
            (dir_text, dir_color),
            (zone_short, zone_color),
            (zoom_tf, "#26C6DA"),
            (g_str, "#FF8A65"),
            (sl_str, "#FF8A65"),
            (lev_str, "#FF8A65"),
            (opt_text, opt_color),
            (trail_t, "#FF8A65"),
            (trail_m, "#FF8A65"),
            (er_m, er_color),
            (hurst, hurst_color),
            (pw_str, pw_color),
            (ev_str, ev_color),
            (rsi_str, rsi_color),
            (adx_str, "gray"),
            (fr_str, fr_color),
            (rej_short, rej_color),
        ]

    # ═══ TABLE 2: Positions ═══

    def _update_positions(self):
        positions = self.controller.get_all_scanner_positions()
        h_positions = [p for p in positions if p.get("entry_mode") == "SYSTEM_H"]

        if not h_positions:
            self._ensure_rows(self._pos_scroll, self._pos_rows,
                              self._pos_cache, SH_POS_WIDTHS, 1)
            empty = [("Pozisyon yok", "gray")] + [("", "gray")] * (len(SH_POS_WIDTHS) - 1)
            self._update_row(self._pos_rows, self._pos_cache, 0, empty)
            return

        n = len(h_positions)
        self._ensure_rows(self._pos_scroll, self._pos_rows,
                          self._pos_cache, SH_POS_WIDTHS, n)

        import time as _time
        for i, p in enumerate(h_positions):
            try:
                vals = self._build_pos_row(p, _time.time())
            except Exception:
                vals = [("ERR", "#FF5252")] * len(SH_POS_WIDTHS)
            self._update_row(self._pos_rows, self._pos_cache, i, vals)

    def _build_pos_row(self, p, now):
        """Build row values for a position dict."""
        side = p.get("side", "")
        dir_arrow = "\u25B2" if "LONG" in str(side) else "\u25BC"
        dir_color = "#00E676" if "LONG" in str(side) else "#FF1744"

        roi = p.get("pnl_pct", 0)
        roi_str = f"{roi:+.2f}%"
        roi_color = "#00E676" if roi > 0 else "#FF1744" if roi < 0 else "gray"

        lev = p.get("leverage", 1)
        g_val = p.get("entry_bb_width", 0)  # G stored in bb_width field
        regime = p.get("entry_regime", "")

        elapsed_sec = now - p.get("entry_time", now)
        elapsed_min = int(elapsed_sec / 60)
        if elapsed_min >= 60:
            kalan_str = f"{elapsed_min // 60}h{elapsed_min % 60}m"
        else:
            kalan_str = f"{elapsed_min}m"

        margin = p.get("margin_usdt", 0)

        return [
            (dir_arrow, dir_color),
            ("ACIK", "#FFD54F"),
            (p.get("symbol", "").replace("USDT", ""), "white"),
            (roi_str, roi_color),
            (f"{lev}x", "#FF8A65"),
            (f"{g_val:.2f}" if g_val > 0 else "--", "#FF8A65"),
            (regime[:4] if regime else "--", _ZONE_COLORS.get(regime, "gray")),
            (f"{p.get('sl_pct', 0):.2f}" if p.get('sl_pct', 0) > 0 else "--", "#FF8A65"),
            ("TRL" if p.get("trailing_active") else "--", "#26C6DA"),
            (kalan_str, "gray"),
            (f"${margin:.2f}", "gray"),
        ]
