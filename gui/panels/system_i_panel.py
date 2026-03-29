"""System I Panel — Unified Trading System: Dual Pool (Trend + Ranging).

ER > 0.35 → Trend havuzu, ER < 0.20 → Ranging havuzu.
"""
import customtkinter as ctk
from loguru import logger

# ═══ Column Layout: System I TREND Scan Results ═══
SI_TREND_HEADERS = [
    "#", "Sinyal", "Sembol", "Skor",
    "Yon", "Guc", "Rejim", "Zoom",
    "G%", "SL%", "Lev", "Opt",
    "Trail.T", "Trail.M",
    "ER", "Hurst", "P(w)", "EV%",
    "FR", "Red",
]
SI_TREND_WIDTHS = [
    24, 54, 86, 44,
    40, 48, 52, 44,
    44, 44, 40, 38,
    48, 48,
    44, 44, 44, 44,
    44, 90,
]

# Important columns: G%, SL%, Lev, Rejim, P(w), EV%
_SI_TREND_IMP = {8, 9, 10, 6, 16, 17}

# ═══ Column Layout: System I RANGING Scan Results ═══
SI_RANGING_HEADERS = [
    "#", "Sinyal", "Sembol", "Skor",
    "Yon", "Guc", "Rejim",
    "G%", "BB%", "SL%", "Lev",
    "ER", "EV%", "FR", "Red",
]
SI_RANGING_WIDTHS = [
    24, 54, 86, 44,
    40, 48, 52,
    44, 44, 44, 40,
    44, 44, 44, 90,
]

# Important columns: G%, SL%, Lev, BB%
_SI_RANGING_IMP = {7, 8, 9, 10}

# ═══ Column Layout: System I Positions (shared) ═══
SI_POS_HEADERS = [
    "", "Sinyal", "Sembol", "ROI%",
    "Lev", "G%", "Rejim",
    "SL%", "TP%", "Trail", "Kalan", "$",
]
SI_POS_WIDTHS = [
    22, 54, 90, 50,
    40, 44, 52,
    44, 44, 50, 48, 42,
]

# ═══ Colors ═══
_RED_BORDER = "#E53935"
_RED_BG = "#2a0f0f"

_TREND_ACCENT = "#4FC3F7"    # light blue for trend pool
_RANGING_ACCENT = "#CE93D8"  # purple for ranging pool

_ZONE_COLORS = {
    "TRENDING": "#4FC3F7",
    "RANGING": "#CE93D8",
    "GRAY": "#FFD54F",
    "TREND": "#4FC3F7",
    "": "gray",
}

_STRENGTH_COLORS = {
    "STRONG": "#00E676",
    "MODERATE": "#FFD54F",
    "WEAK": "gray",
}

_DIR_COLOR = {"LONG": "#00E676", "SHORT": "#FF5252"}
_DIR_ARROW = {"LONG": "\u25B2", "SHORT": "\u25BC"}


def _build_header(parent, headers, widths, important_set):
    """Build a header row with red-bordered boxes for important columns."""
    hdr = ctk.CTkFrame(parent, fg_color="transparent")
    hdr.pack(fill="x", padx=2)
    font = ctk.CTkFont(size=13, weight="bold")

    for col_idx, (h, w) in enumerate(zip(headers, widths)):
        if h in ("G%", "SL%", "Lev", "Opt", "Trail.T", "Trail.M", "TP%"):
            hdr_color = "#FF8A65"   # coral - risk
        elif h in ("Yon", "Guc"):
            hdr_color = "#00E676"   # green - direction
        elif h in ("Rejim", "ER", "Hurst"):
            hdr_color = "#CE93D8"   # purple - regime
        elif h in ("P(w)", "EV%"):
            hdr_color = "#00BCD4"   # teal - statistics
        elif h == "Zoom":
            hdr_color = "#26C6DA"   # cyan
        elif h in ("BB%",):
            hdr_color = "#CE93D8"   # purple - BB proximity
        elif h in ("RSI", "FR", "Skor"):
            hdr_color = "#FFD54F"   # yellow
        elif h == "Red":
            hdr_color = "#FF5252"
        elif h == "Sinyal":
            hdr_color = "#7799BB"
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


class SystemIPanel(ctk.CTkFrame):
    """System I dual pool (Trend + Ranging) scan results and positions panel."""

    def __init__(self, master, app_ctrl):
        super().__init__(master)
        self.controller = app_ctrl
        self.pack(fill="both", expand=True)

        # Trend pool rows
        self._trend_scan_rows = []
        self._trend_scan_cache = []
        self._trend_pos_rows = []
        self._trend_pos_cache = []

        # Ranging pool rows
        self._rang_scan_rows = []
        self._rang_scan_cache = []
        self._rang_pos_rows = []
        self._rang_pos_cache = []

        self._build_ui()
        self._start_refresh()

    def _build_ui(self) -> None:
        # ═══ STATS BAR ═══
        stats_frame = ctk.CTkFrame(self, fg_color="transparent")
        stats_frame.pack(fill="x", padx=4, pady=(2, 0))

        self._stats_label = ctk.CTkLabel(
            stats_frame,
            text="Trend: 0 | Ranging: 0 | Pozisyon: 0",
            font=ctk.CTkFont(size=11), text_color="gray",
        )
        self._stats_label.pack(side="right")

        # ═══ TAB VIEW: TREND / RANGING ═══
        self._tabview = ctk.CTkTabview(self, segmented_button_fg_color="#1a1a2e",
                                        segmented_button_selected_color="#2d3a6e",
                                        segmented_button_unselected_color="#1a1a2e")
        self._tabview.pack(fill="both", expand=True, padx=3, pady=(1, 2))

        tab_trend = self._tabview.add("TREND")
        tab_rang = self._tabview.add("RANGING")

        # ═══ TREND TAB ═══
        ctk.CTkLabel(
            tab_trend,
            text="TREND HAVUZU (ER > 0.35)",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=_TREND_ACCENT,
        ).pack(anchor="w", padx=4, pady=(1, 0))

        _build_header(tab_trend, SI_TREND_HEADERS, SI_TREND_WIDTHS, _SI_TREND_IMP)

        self._trend_scan_scroll = ctk.CTkScrollableFrame(tab_trend, height=220)
        self._trend_scan_scroll.pack(fill="both", expand=True, padx=2)

        # Trend positions
        ctk.CTkLabel(
            tab_trend,
            text="Aktif Trend Pozisyonlari",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color="#FFD54F",
        ).pack(anchor="w", padx=4, pady=(4, 0))

        _build_header(tab_trend, SI_POS_HEADERS, SI_POS_WIDTHS, set())

        self._trend_pos_scroll = ctk.CTkScrollableFrame(tab_trend, height=100)
        self._trend_pos_scroll.pack(fill="x", padx=2, pady=(0, 2))

        # ═══ RANGING TAB ═══
        ctk.CTkLabel(
            tab_rang,
            text="RANGING HAVUZU (ER < 0.20)",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=_RANGING_ACCENT,
        ).pack(anchor="w", padx=4, pady=(1, 0))

        _build_header(tab_rang, SI_RANGING_HEADERS, SI_RANGING_WIDTHS, _SI_RANGING_IMP)

        self._rang_scan_scroll = ctk.CTkScrollableFrame(tab_rang, height=220)
        self._rang_scan_scroll.pack(fill="both", expand=True, padx=2)

        # Ranging positions
        ctk.CTkLabel(
            tab_rang,
            text="Aktif Ranging Pozisyonlari",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color="#FFD54F",
        ).pack(anchor="w", padx=4, pady=(4, 0))

        _build_header(tab_rang, SI_POS_HEADERS, SI_POS_WIDTHS, set())

        self._rang_pos_scroll = ctk.CTkScrollableFrame(tab_rang, height=100)
        self._rang_pos_scroll.pack(fill="x", padx=2, pady=(0, 2))

    # ═══ Refresh ═══

    def _start_refresh(self):
        self._refresh()

    def _refresh(self):
        try:
            self._update_all()
        except Exception as e:
            logger.error(f"[SysI Panel] refresh error: {e}")
        self.after(4000, self._refresh)

    def refresh(self):
        """Public refresh entry point (called from main window)."""
        self._update_all()

    def _update_all(self):
        """Fetch data and update both pools."""
        # Get scan results
        results = []
        try:
            results = self.controller.get_system_i_results()
        except AttributeError:
            pass
        except Exception as e:
            logger.error(f"[SysI Panel] get results error: {e}")

        # Split into pools
        trend_results = []
        rang_results = []
        if results:
            for r in results:
                pool = getattr(r, 'pool', None)
                if isinstance(r, dict):
                    pool = r.get('pool', '')
                if pool == "TREND":
                    trend_results.append(r)
                elif pool == "RANGING":
                    rang_results.append(r)
                else:
                    # Default: check ER to decide
                    er = getattr(r, 'er', 0)
                    if isinstance(r, dict):
                        er = r.get('er', 0)
                    if er and er > 0.35:
                        trend_results.append(r)
                    elif er is not None and er < 0.20:
                        rang_results.append(r)

        # Get positions
        all_positions = []
        try:
            all_positions = self.controller.get_all_scanner_positions()
        except Exception as e:
            logger.debug(f"[SysI Panel] get positions error: {e}")

        # Debug: entry_mode değerlerini logla
        if all_positions:
            modes = [p.get("entry_mode", "NONE") for p in all_positions]
            mode_counts = {}
            for m in modes:
                mode_counts[m] = mode_counts.get(m, 0) + 1
            logger.debug(f"[SysI Panel] all_positions={len(all_positions)}, "
                         f"entry_modes={mode_counts}")

        # System I paneli aktifken tüm pozisyonları göster
        # (eski sistemlerden kalan pozisyonlar da dahil)
        si_positions = list(all_positions)

        trend_positions = [p for p in si_positions
                           if p.get("entry_regime", "").upper() in ("TREND", "TRENDING")]
        rang_positions = [p for p in si_positions
                          if p.get("entry_regime", "").upper() in ("RANGING", "RANG")]
        # Fallback: unmatched positions go to trend
        matched = set(id(p) for p in trend_positions + rang_positions)
        for p in si_positions:
            if id(p) not in matched:
                trend_positions.append(p)

        logger.debug(f"[SysI Panel] si={len(si_positions)}, "
                     f"trend_pos={len(trend_positions)}, rang_pos={len(rang_positions)}")

        # Update trend scan table
        try:
            self._update_scan_table(
                self._trend_scan_scroll, self._trend_scan_rows,
                self._trend_scan_cache, SI_TREND_WIDTHS,
                trend_results, is_ranging=False,
            )
        except Exception as e:
            logger.error(f"[SysI Panel] trend scan error: {e}")

        # Update trend positions
        try:
            self._update_pos_table(
                self._trend_pos_scroll, self._trend_pos_rows,
                self._trend_pos_cache, trend_positions,
            )
        except Exception as e:
            logger.error(f"[SysI Panel] trend pos error: {e}")

        # Update ranging scan table
        try:
            self._update_scan_table(
                self._rang_scan_scroll, self._rang_scan_rows,
                self._rang_scan_cache, SI_RANGING_WIDTHS,
                rang_results, is_ranging=True,
            )
        except Exception as e:
            logger.error(f"[SysI Panel] ranging scan error: {e}")

        # Update ranging positions
        try:
            self._update_pos_table(
                self._rang_pos_scroll, self._rang_pos_rows,
                self._rang_pos_cache, rang_positions,
            )
        except Exception as e:
            logger.error(f"[SysI Panel] ranging pos error: {e}")

        # Update stats bar
        pos_count = len(si_positions)
        self._stats_label.configure(
            text=f"Trend: {len(trend_results)} | Ranging: {len(rang_results)} | Pozisyon: {pos_count}"
        )

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

    # ═══ Scan Table Update ═══

    def _update_scan_table(self, scroll, rows_list, cache_list, widths,
                           results, is_ranging=False):
        if not results:
            self._ensure_rows(scroll, rows_list, cache_list, widths, 1)
            si_enabled = False
            try:
                si_enabled = self.controller.config.get("system_i.enabled", False)
            except Exception:
                pass
            pool_name = "Ranging" if is_ranging else "Trend"
            msg = f"{pool_name} sonucu yok" if si_enabled else "System I devre disi"
            empty = [(msg, "gray")] + [("", "gray")] * (len(widths) - 1)
            self._update_row(rows_list, cache_list, 0, empty)
            return

        n = min(len(results), 50)
        self._ensure_rows(scroll, rows_list, cache_list, widths, n)

        for i, r in enumerate(results[:n]):
            try:
                if is_ranging:
                    vals = self._build_ranging_scan_row(i, r)
                else:
                    vals = self._build_trend_scan_row(i, r)
            except Exception as e:
                sym = self._g(r, 'symbol', '?')
                logger.error(f"[SysI Panel] row error #{i} {sym}: {e}")
                vals = [(str(i + 1), "gray")] + [("ERR", "#FF5252")] * (len(widths) - 1)
            bg = "#1c2d4d" if i % 2 == 0 else "transparent"
            self._update_row(rows_list, cache_list, i, vals, bg)

    # ═══ Attribute getter (supports dict and object) ═══

    @staticmethod
    def _g(r, key, default=""):
        if isinstance(r, dict):
            return r.get(key, default)
        return getattr(r, key, default)

    # ═══ Build TREND scan row ═══

    def _build_trend_scan_row(self, i, r):
        """Build row values for a TREND pool SystemIScanResult."""
        g = self._g
        eligible = g(r, 'eligible', False)
        row_color = "#00C853" if eligible else "gray"

        # Signal
        direction = g(r, 'direction', '')
        if eligible and direction:
            arrow = _DIR_ARROW.get(direction, "?")
            sig_text = f"GIRIS {arrow}"
            sig_color = "#00E676" if direction == "LONG" else "#FF1744"
        else:
            sig_text = "---"
            sig_color = "gray"

        # Symbol
        symbol = g(r, 'symbol', '?')
        if isinstance(symbol, str):
            symbol = symbol.replace("USDT", "")

        # Score
        score = g(r, 'score', 0) or 0
        score_str = f"{score:+.0f}" if score else "--"
        score_color = "#00E676" if abs(score) >= 70 else "#FFD54F" if abs(score) >= 50 else "gray"

        # Direction
        dir_text = direction if direction else "--"
        dir_color = _DIR_COLOR.get(direction, "gray")

        # Strength
        strength = g(r, 'strength', '') or ''
        str_short = {"STRONG": "GUC", "MODERATE": "ORTA", "WEAK": "ZYF"}.get(strength, strength[:3] if strength else "--")
        str_color = _STRENGTH_COLORS.get(strength, "gray")

        # Regime
        regime = g(r, 'regime_zone', '') or ''
        if not regime:
            regime_obj = g(r, 'regime', None)
            if regime_obj and hasattr(regime_obj, 'regime'):
                regime = regime_obj.regime or ''
            elif isinstance(regime_obj, str):
                regime = regime_obj
        zone_short = {"TRENDING": "TRND", "RANGING": "RANG", "GRAY": "GRI",
                      "TREND": "TRND"}.get(regime, regime[:4] if regime else "--")
        zone_color = _ZONE_COLORS.get(regime, "gray")

        # Zoom TF
        zoom_tf = g(r, 'zoom_tf', '') or ''
        if not zoom_tf:
            zoom = g(r, 'zoom', None)
            if zoom and hasattr(zoom, 'yon_tf'):
                zoom_tf = zoom.yon_tf if getattr(zoom, 'optimal_G', 0) > 0 else "--"
            elif isinstance(zoom, str):
                zoom_tf = zoom if zoom else "--"
            else:
                zoom_tf = "--"

        # G, SL, Leverage
        G_val = g(r, 'G', 0) or 0
        g_str = f"{G_val:.2f}" if G_val > 0 else "--"
        sl_pct = g(r, 'sl_pct', 0) or 0
        sl_str = f"{sl_pct:.2f}" if sl_pct > 0 else "--"
        leverage = g(r, 'leverage', 0) or 0
        lev_str = f"{leverage}x" if leverage > 1 else "--"

        # Optimizer status
        opt_st = g(r, 'opt_status', 'NONE') or 'NONE'
        opt_blended = g(r, 'opt_blended', False)
        if opt_blended:
            opt_text, opt_color = "B", "#00E676"
        else:
            opt_map = {
                "NONE": ("--", "gray"), "PENDING": ("...", "#FFD54F"),
                "CACHED": ("C", "#26C6DA"), "FRESH": ("F", "#00E676"),
                "CONFIRM": ("\u2713", "#00E676"), "REJECT": ("\u2717", "#FF5252"),
            }
            opt_text, opt_color = opt_map.get(opt_st, ("--", "gray"))

        # Trailing
        trail_t = g(r, 'trailing_trigger_pct', 0) or 0
        trail_t_str = f"{trail_t:.2f}" if trail_t > 0 else "--"
        trail_m = g(r, 'trailing_callback_pct', 0) or 0
        trail_m_str = f"{trail_m:.2f}" if trail_m > 0 else "--"

        # ER, Hurst
        er = g(r, 'er', 0) or 0
        er_str = f"{er:.2f}" if er > 0 else "--"
        er_color = _TREND_ACCENT if er > 0.35 else _RANGING_ACCENT if er < 0.15 else "gray"

        hurst = g(r, 'hurst', 0.5) or 0.5
        hurst_str = f"{hurst:.2f}" if hurst != 0.5 else "--"
        hurst_color = _TREND_ACCENT if hurst > 0.55 else _RANGING_ACCENT if hurst < 0.45 else "gray"

        # P(win), EV
        prob = g(r, 'probability', None)
        if prob and hasattr(prob, 'p_win') and getattr(prob, 'sufficient', False):
            pw_str = f"{prob.p_win:.0%}"
            ev_str = f"{prob.ev_pct:+.0f}"
            pw_color = "#00E676" if prob.p_win > 0.6 else "#FFD54F" if prob.p_win > 0.4 else "gray"
            ev_color = "#00E676" if prob.ev_pct > 10 else "#FF5252" if prob.ev_pct < -5 else "gray"
        else:
            p_win = g(r, 'p_win', 0) or 0
            ev_pct = g(r, 'ev_pct', 0) or 0
            pw_str = f"{p_win:.0%}" if p_win > 0 else "--"
            ev_str = f"{ev_pct:+.0f}" if ev_pct else "--"
            pw_color = "#00E676" if p_win > 0.6 else "#FFD54F" if p_win > 0.4 else "gray"
            ev_color = "#00E676" if ev_pct > 10 else "#FF5252" if ev_pct < -5 else "gray"

        # FR
        fr = g(r, 'funding_rate', 0) or 0
        fr_pct = fr * 100 if abs(fr) < 1 else fr  # handle both raw and pct
        fr_str = f"{fr_pct:+.3f}" if fr_pct != 0 else "--"
        fr_color = "#FF5252" if abs(fr_pct) > 0.05 else "gray"

        # Reject reason
        rej = g(r, 'reject_reason', '') or ''
        rej_short = rej[:12] if rej else "-"
        rej_color = "#FF5252" if rej else "#00C853"

        return [
            (str(i + 1), row_color),
            (sig_text, sig_color),
            (symbol, row_color),
            (score_str, score_color),
            (dir_text, dir_color),
            (str_short, str_color),
            (zone_short, zone_color),
            (zoom_tf, "#26C6DA"),
            (g_str, "#FF8A65"),
            (sl_str, "#FF8A65"),
            (lev_str, "#FF8A65"),
            (opt_text, opt_color),
            (trail_t_str, "#FF8A65"),
            (trail_m_str, "#FF8A65"),
            (er_str, er_color),
            (hurst_str, hurst_color),
            (pw_str, pw_color),
            (ev_str, ev_color),
            (fr_str, fr_color),
            (rej_short, rej_color),
        ]

    # ═══ Build RANGING scan row ═══

    def _build_ranging_scan_row(self, i, r):
        """Build row values for a RANGING pool SystemIScanResult."""
        g = self._g
        eligible = g(r, 'eligible', False)
        row_color = "#00C853" if eligible else "gray"

        # Signal
        direction = g(r, 'direction', '')
        if eligible and direction:
            arrow = _DIR_ARROW.get(direction, "?")
            sig_text = f"GIRIS {arrow}"
            sig_color = "#00E676" if direction == "LONG" else "#FF1744"
        else:
            sig_text = "---"
            sig_color = "gray"

        # Symbol
        symbol = g(r, 'symbol', '?')
        if isinstance(symbol, str):
            symbol = symbol.replace("USDT", "")

        # Score
        score = g(r, 'score', 0) or 0
        score_str = f"{score:+.0f}" if score else "--"
        score_color = "#00E676" if abs(score) >= 70 else "#FFD54F" if abs(score) >= 50 else "gray"

        # Direction
        dir_text = direction if direction else "--"
        dir_color = _DIR_COLOR.get(direction, "gray")

        # Strength
        strength = g(r, 'strength', '') or ''
        str_short = {"STRONG": "GUC", "MODERATE": "ORTA", "WEAK": "ZYF"}.get(strength, strength[:3] if strength else "--")
        str_color = _STRENGTH_COLORS.get(strength, "gray")

        # Regime
        regime = g(r, 'regime_zone', '') or ''
        if not regime:
            regime_obj = g(r, 'regime', None)
            if regime_obj and hasattr(regime_obj, 'regime'):
                regime = regime_obj.regime or ''
            elif isinstance(regime_obj, str):
                regime = regime_obj
        zone_short = {"TRENDING": "TRND", "RANGING": "RANG", "GRAY": "GRI",
                      "TREND": "TRND"}.get(regime, regime[:4] if regime else "--")
        zone_color = _ZONE_COLORS.get(regime, "gray")

        # G, BB%, SL, Leverage
        G_val = g(r, 'G', 0) or 0
        g_str = f"{G_val:.2f}" if G_val > 0 else "--"

        bb_pct = g(r, 'bb_proximity', 0) or g(r, 'bb_pct', 0) or 0
        bb_str = f"{bb_pct:.0f}" if bb_pct > 0 else "--"
        bb_color = "#CE93D8" if bb_pct > 70 else "#FFD54F" if bb_pct > 40 else "gray"

        sl_pct = g(r, 'sl_pct', 0) or 0
        sl_str = f"{sl_pct:.2f}" if sl_pct > 0 else "--"

        leverage = g(r, 'leverage', 0) or 0
        lev_str = f"{leverage}x" if leverage > 1 else "--"

        # ER
        er = g(r, 'er', 0) or 0
        er_str = f"{er:.2f}" if er > 0 else "--"
        er_color = _RANGING_ACCENT if er < 0.20 else _TREND_ACCENT if er > 0.35 else "gray"

        # EV
        prob = g(r, 'probability', None)
        if prob and hasattr(prob, 'ev_pct') and getattr(prob, 'sufficient', False):
            ev_str = f"{prob.ev_pct:+.0f}"
            ev_color = "#00E676" if prob.ev_pct > 10 else "#FF5252" if prob.ev_pct < -5 else "gray"
        else:
            ev_pct = g(r, 'ev_pct', 0) or 0
            ev_str = f"{ev_pct:+.0f}" if ev_pct else "--"
            ev_color = "#00E676" if ev_pct > 10 else "#FF5252" if ev_pct < -5 else "gray"

        # FR
        fr = g(r, 'funding_rate', 0) or 0
        fr_pct = fr * 100 if abs(fr) < 1 else fr
        fr_str = f"{fr_pct:+.3f}" if fr_pct != 0 else "--"
        fr_color = "#FF5252" if abs(fr_pct) > 0.05 else "gray"

        # Reject reason
        rej = g(r, 'reject_reason', '') or ''
        rej_short = rej[:12] if rej else "-"
        rej_color = "#FF5252" if rej else "#00C853"

        return [
            (str(i + 1), row_color),
            (sig_text, sig_color),
            (symbol, row_color),
            (score_str, score_color),
            (dir_text, dir_color),
            (str_short, str_color),
            (zone_short, zone_color),
            (g_str, "#FF8A65"),
            (bb_str, bb_color),
            (sl_str, "#FF8A65"),
            (lev_str, "#FF8A65"),
            (er_str, er_color),
            (ev_str, ev_color),
            (fr_str, fr_color),
            (rej_short, rej_color),
        ]

    # ═══ Position Table Update ═══

    def _update_pos_table(self, scroll, rows_list, cache_list, positions):
        if not positions:
            self._ensure_rows(scroll, rows_list, cache_list, SI_POS_WIDTHS, 1)
            empty = [("Pozisyon yok", "gray")] + [("", "gray")] * (len(SI_POS_WIDTHS) - 1)
            self._update_row(rows_list, cache_list, 0, empty)
            return

        n = len(positions)
        self._ensure_rows(scroll, rows_list, cache_list, SI_POS_WIDTHS, n)

        import time as _time
        now = _time.time()
        for i, pos in enumerate(positions):
            try:
                vals = self._build_pos_row(pos, now)
            except Exception:
                vals = [("ERR", "#FF5252")] * len(SI_POS_WIDTHS)
            bg = "#1c2d4d" if i % 2 == 0 else "transparent"
            self._update_row(rows_list, cache_list, i, vals, bg)

    def _build_pos_row(self, p, now):
        """Build row values for an active System I position."""
        side = p.get("side", "")
        arrow = "\u25B2" if "LONG" in str(side) else "\u25BC"
        dir_color = "#00E676" if "LONG" in str(side) else "#FF1744"

        # Signal
        sig_text = "ACIK"
        sig_color = "#FFD54F"

        # Symbol
        symbol = p.get("symbol", "?").replace("USDT", "")

        # ROI
        roi = p.get("pnl_pct", 0) or p.get("roi_percent", 0) or 0
        roi_str = f"{roi:+.2f}%"
        roi_color = "#00E676" if roi > 0 else "#FF1744" if roi < 0 else "gray"

        # Leverage
        lev = p.get("leverage", 1)

        # G
        g_val = p.get("entry_bb_width", 0)  # G stored in bb_width field
        g_str = f"{g_val:.2f}" if g_val and g_val > 0 else "--"

        # Regime
        regime = p.get("entry_regime", "") or ""
        regime_short = {"TRENDING": "TRND", "RANGING": "RANG", "GRAY": "GRI",
                        "TREND": "TRND"}.get(regime, regime[:4] if regime else "--")
        regime_color = _ZONE_COLORS.get(regime, "gray")

        # SL%
        entry_price = p.get("entry_price", 0)
        sl_price = p.get("sl", 0)
        if entry_price > 0 and sl_price > 0:
            sl_pct = abs(entry_price - sl_price) / entry_price * 100
            sl_str = f"{sl_pct:.2f}"
        else:
            sl_str = "--"

        # TP%
        tp_price = p.get("tp", 0)
        if entry_price > 0 and tp_price > 0:
            tp_pct = abs(tp_price - entry_price) / entry_price * 100
            tp_str = f"{tp_pct:.2f}"
        else:
            tp_str = "--"

        # Trail
        trailing = p.get("trailing_active", False)
        trail_str = "AKTIF" if trailing else "BEKLE"
        trail_color = "#00E676" if trailing else "gray"

        # Elapsed time
        elapsed_sec = now - p.get("entry_time", now)
        elapsed_min = int(elapsed_sec / 60)
        if elapsed_min >= 60:
            kalan_str = f"{elapsed_min // 60}h{elapsed_min % 60}m"
        else:
            kalan_str = f"{elapsed_min}m"

        # Margin $
        margin = p.get("margin_usdt", 0) or 0
        pnl = margin * roi / 100 if margin > 0 else 0
        pnl_color = "#00E676" if pnl > 0 else "#FF1744" if pnl < 0 else "gray"

        return [
            (arrow, dir_color),
            (sig_text, sig_color),
            (symbol, "white"),
            (roi_str, roi_color),
            (f"{lev}x", "#FF8A65"),
            (g_str, "#FF8A65"),
            (regime_short, regime_color),
            (sl_str, "#FF8A65"),
            (tp_str, "#FF8A65"),
            (trail_str, trail_color),
            (kalan_str, "gray"),
            (f"${pnl:+.2f}", pnl_color),
        ]
