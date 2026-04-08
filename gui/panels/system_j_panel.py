"""System J Panel — Maximum Leverage First: Dual Pool (Trend + Ranging).

3 turlu tarama sonuclari + aktif pozisyonlar.
"""
import time
import customtkinter as ctk
from loguru import logger

# ═══ Column Layout: System J Scan Results ═══
SJ_SCAN_HEADERS = [
    "#", "Sinyal", "Sembol", "Tur", "Skor",
    "Yon", "Rejim", "TF",
    "G%", "SL%", "Lev", "TP%",
    "P(w)", "EV%", "R:R", "ER",
    "FR", "Red",
]
SJ_SCAN_WIDTHS = [
    24, 54, 86, 28, 44,
    40, 52, 44,
    44, 44, 40, 44,
    44, 44, 40, 44,
    44, 90,
]
# Important columns
_SJ_IMP = {8, 9, 10, 11, 12, 13}

# ═══ Column Layout: System J Positions ═══
SJ_POS_HEADERS = [
    "", "Sinyal", "Sembol", "ROI%",
    "Lev", "G%", "Rejim",
    "SL%", "TP%", "Trail", "Kalan", "$",
]
SJ_POS_WIDTHS = [
    22, 54, 90, 50,
    40, 44, 52,
    44, 44, 50, 48, 42,
]

# ═══ Colors ═══
_TREND_ACCENT = "#4FC3F7"
_RANGING_ACCENT = "#CE93D8"
_DIR_ARROW = {"LONG": "\u25b2", "SHORT": "\u25bc"}
_DIR_COLOR = {"LONG": "#00E676", "SHORT": "#FF5252"}
_ZONE_COLORS = {"TRENDING": "#4FC3F7", "TREND": "#4FC3F7",
                "RANGING": "#CE93D8", "RANG": "#CE93D8",
                "GRAY": "#78909C", "UNDECIDED": "#616161"}
_PASS_COLORS = {1: "#00E676", 2: "#FFD54F", 3: "#FF8A65"}
_PASS_LABELS = {1: "T1", 2: "T2", 3: "T3"}


def _build_header(parent, headers, widths, imp_set):
    """Build header row."""
    frame = ctk.CTkFrame(parent, fg_color="#1a1a2e", height=22)
    frame.pack(fill="x", padx=2, pady=(1, 0))
    for j, (h, w) in enumerate(zip(headers, widths)):
        color = "#4FC3F7" if j in imp_set else "#90A4AE"
        ctk.CTkLabel(
            frame, text=h, width=w, height=18,
            font=ctk.CTkFont(size=10, weight="bold"),
            text_color=color,
        ).pack(side="left", padx=1)


def _g(obj, attr, default=None):
    """Safe attribute/dict getter."""
    if isinstance(obj, dict):
        return obj.get(attr, default)
    return getattr(obj, attr, default)


class SystemJPanel(ctk.CTkFrame):
    """System J dual pool scan results and positions panel."""

    def __init__(self, master, app_ctrl):
        super().__init__(master)
        self.controller = app_ctrl
        self.pack(fill="both", expand=True)

        self._scan_rows = []
        self._scan_cache = []
        self._trend_pos_rows = []
        self._trend_pos_cache = []
        self._rang_pos_rows = []
        self._rang_pos_cache = []

        self._build_ui()
        self._start_refresh()

    def _build_ui(self) -> None:
        # ═══ STATS BAR ═══
        stats_frame = ctk.CTkFrame(self)
        stats_frame.pack(fill="x", padx=4, pady=(2, 0))

        self._stats_label = ctk.CTkLabel(
            stats_frame,
            text="Tarama: 0 | Pozisyon: 0 | Tur: -",
            font=ctk.CTkFont(size=11), text_color="gray",
        )
        self._stats_label.pack(side="right")

        # ═══ TAB VIEW: TARAMA / POZİSYONLAR ═══
        self._tabview = ctk.CTkTabview(self, segmented_button_fg_color="#1a1a2e",
                                        segmented_button_selected_color="#2d3a6e",
                                        segmented_button_unselected_color="#1a1a2e")
        self._tabview.pack(fill="both", expand=True, padx=3, pady=(1, 2))

        tab_scan = self._tabview.add("TARAMA")
        tab_pos = self._tabview.add("POZİSYONLAR")

        # ═══ SCAN TAB ═══
        ctk.CTkLabel(
            tab_scan,
            text="3 TURLU TARAMA SONUCLARI",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=_TREND_ACCENT,
        ).pack(anchor="w", padx=4, pady=(1, 0))

        # Legend
        legend = ctk.CTkFrame(tab_scan, height=16)
        legend.pack(fill="x", padx=4)
        for p, (label, color) in enumerate(
            [(f"T1: Max Kaldirac", _PASS_COLORS[1]),
             (f"T2: G-Bazli", _PASS_COLORS[2]),
             (f"T3: Zoom Dirsek", _PASS_COLORS[3])], 1):
            ctk.CTkLabel(legend, text=label, font=ctk.CTkFont(size=9),
                         text_color=color).pack(side="left", padx=6)

        _build_header(tab_scan, SJ_SCAN_HEADERS, SJ_SCAN_WIDTHS, _SJ_IMP)

        self._scan_scroll = ctk.CTkScrollableFrame(tab_scan, height=350)
        self._scan_scroll.pack(fill="both", expand=True, padx=2)

        # ═══ POSITIONS TAB ═══
        # Trend positions
        ctk.CTkLabel(
            tab_pos,
            text="TREND POZISYONLARI",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=_TREND_ACCENT,
        ).pack(anchor="w", padx=4, pady=(1, 0))

        _build_header(tab_pos, SJ_POS_HEADERS, SJ_POS_WIDTHS, set())

        self._trend_pos_scroll = ctk.CTkScrollableFrame(tab_pos, height=140)
        self._trend_pos_scroll.pack(fill="x", padx=2, pady=(0, 4))

        # Ranging positions
        ctk.CTkLabel(
            tab_pos,
            text="RANGING POZISYONLARI",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=_RANGING_ACCENT,
        ).pack(anchor="w", padx=4, pady=(1, 0))

        _build_header(tab_pos, SJ_POS_HEADERS, SJ_POS_WIDTHS, set())

        self._rang_pos_scroll = ctk.CTkScrollableFrame(tab_pos, height=140)
        self._rang_pos_scroll.pack(fill="x", padx=2, pady=(0, 2))

    # ═══ REFRESH ═══

    def _start_refresh(self):
        self._refresh_job = self.after(2000, self._refresh_loop)

    def _refresh_loop(self):
        try:
            self._update_all()
        except Exception as e:
            logger.error(f"[SysJ Panel] refresh error: {e}")
        self._refresh_job = self.after(2000, self._refresh_loop)

    def _update_all(self):
        """Fetch data and update tables."""
        results = []
        try:
            results = self.controller.get_system_j_results()
        except Exception as e:
            logger.debug(f"[SysJ Panel] get results: {e}")

        # Get positions
        all_positions = []
        try:
            all_positions = self.controller.get_all_scanner_positions()
        except Exception:
            pass

        sj_positions = [p for p in all_positions
                        if p.get("entry_mode", "") in ("SYSTEM_J", "")]
        # Fallback: System J paneli aktifken tüm pozisyonları göster
        if not sj_positions:
            sj_positions = list(all_positions)

        trend_pos = [p for p in sj_positions
                     if p.get("entry_regime", "").upper() in ("TREND", "TRENDING", "")]
        rang_pos = [p for p in sj_positions
                    if p.get("entry_regime", "").upper() in ("RANGING", "RANG")]
        matched = set(id(p) for p in trend_pos + rang_pos)
        for p in sj_positions:
            if id(p) not in matched:
                trend_pos.append(p)

        # Update scan table
        self._update_scan_table(results)

        # Update position tables
        self._update_pos_table(self._trend_pos_scroll, self._trend_pos_rows,
                               self._trend_pos_cache, trend_pos)
        self._update_pos_table(self._rang_pos_scroll, self._rang_pos_rows,
                               self._rang_pos_cache, rang_pos)

        # Stats
        pass_counts = {1: 0, 2: 0, 3: 0}
        for r in (results or []):
            p = _g(r, 'scan_pass', 0)
            if p in pass_counts:
                pass_counts[p] += 1

        self._stats_label.configure(
            text=f"Eligible: {sum(1 for r in (results or []) if _g(r, 'eligible', False))} / "
                 f"{len(results or [])} | Poz: {len(sj_positions)} | "
                 f"T1:{pass_counts[1]} T2:{pass_counts[2]} T3:{pass_counts[3]}")

    # ═══ SCAN TABLE ═══

    def _update_scan_table(self, results):
        """Update scan results table."""
        if results is None:
            results = []

        new_cache = []
        for i, r in enumerate(results[:30]):
            row_data = self._build_scan_row(i, r)
            new_cache.append(row_data)

        if new_cache == self._scan_cache:
            return
        self._scan_cache = new_cache

        # Clear and rebuild
        for w in self._scan_scroll.winfo_children():
            w.destroy()
        self._scan_rows.clear()

        for i, row_data in enumerate(new_cache):
            row_frame = ctk.CTkFrame(self._scan_scroll, height=20)
            row_frame.pack(fill="x", padx=1, pady=0)
            for j, (text, color, width) in enumerate(row_data):
                ctk.CTkLabel(
                    row_frame, text=text, width=width, height=18,
                    font=ctk.CTkFont(size=10),
                    text_color=color,
                ).pack(side="left", padx=1)
            self._scan_rows.append(row_frame)

    def _build_scan_row(self, i, r):
        """Build row data: list of (text, color, width)."""
        g = _g
        eligible = g(r, 'eligible', False)
        scan_pass = g(r, 'scan_pass', 0)
        direction = g(r, 'direction', '')
        symbol = (g(r, 'symbol', '?') or '?').replace("USDT", "")
        score = g(r, 'score', 0) or 0
        regime = g(r, 'regime_zone', '') or g(r, 'pool', '') or ''
        trade_tf = g(r, 'trade_tf', '') or g(r, 'zoom_tf', '') or '--'
        G_val = g(r, 'G', 0) or 0
        sl_pct = g(r, 'sl_pct', 0) or 0
        leverage = g(r, 'leverage', 0) or 0
        tp_pct = g(r, 'tp_pct', 0) or 0
        p_win = g(r, 'p_win', 0) or 0
        ev_pct = g(r, 'ev_pct', 0) or 0
        er = g(r, 'er', 0) or 0
        fr = g(r, 'funding_rate', 0) or 0
        reject = g(r, 'reject_reason', '') or ''

        # EV result
        ev_res = g(r, 'ev_result', None)
        rr = 0.0
        if ev_res:
            rr = _g(ev_res, 'rr_ratio', 0)
            if not p_win:
                p_win = _g(ev_res, 'p_win', 0)
            if not ev_pct:
                ev_pct = _g(ev_res, 'ev_pct', 0)

        # Colors
        row_color = "#00C853" if eligible else "gray"
        pass_color = _PASS_COLORS.get(scan_pass, "gray")
        dir_color = _DIR_COLOR.get(direction, "gray")
        zone_short = {"TRENDING": "TRND", "RANGING": "RANG", "TREND": "TRND",
                      "GRAY": "GRI"}.get(regime, regime[:4] if regime else "--")
        zone_color = _ZONE_COLORS.get(regime, "gray")

        # Signal
        if eligible and direction:
            arrow = _DIR_ARROW.get(direction, "?")
            sig_text = f"GIRIS {arrow}"
            sig_color = dir_color
        else:
            sig_text = "---"
            sig_color = "gray"

        score_color = "#00E676" if score >= 70 else "#FFD54F" if score >= 50 else "gray"

        cols = [
            (str(i + 1), "gray", SJ_SCAN_WIDTHS[0]),
            (sig_text, sig_color, SJ_SCAN_WIDTHS[1]),
            (symbol, row_color, SJ_SCAN_WIDTHS[2]),
            (_PASS_LABELS.get(scan_pass, "--"), pass_color, SJ_SCAN_WIDTHS[3]),
            (f"{score:.0f}", score_color, SJ_SCAN_WIDTHS[4]),
            (direction or "--", dir_color, SJ_SCAN_WIDTHS[5]),
            (zone_short, zone_color, SJ_SCAN_WIDTHS[6]),
            (trade_tf or "--", "#90A4AE", SJ_SCAN_WIDTHS[7]),
            (f"{G_val:.2f}" if G_val > 0 else "--", "#FF8A65", SJ_SCAN_WIDTHS[8]),
            (f"{sl_pct:.2f}" if sl_pct > 0 else "--", "#FF8A65", SJ_SCAN_WIDTHS[9]),
            (f"{leverage}x" if leverage > 1 else "--", "#FFD54F", SJ_SCAN_WIDTHS[10]),
            (f"{tp_pct:.1f}" if tp_pct > 0 else "--", "#00BCD4", SJ_SCAN_WIDTHS[11]),
            (f"{p_win:.0%}" if p_win > 0 else "--", "#00BCD4", SJ_SCAN_WIDTHS[12]),
            (f"{ev_pct:+.1f}" if ev_pct else "--", "#00E676" if ev_pct > 0 else "#FF5252", SJ_SCAN_WIDTHS[13]),
            (f"{rr:.1f}" if rr > 0 else "--", "#00BCD4", SJ_SCAN_WIDTHS[14]),
            (f"{er:.2f}" if er > 0 else "--", "#90A4AE", SJ_SCAN_WIDTHS[15]),
            (f"{fr*100:.3f}" if fr else "--", "#FFD54F" if abs(fr) > 0.0005 else "gray", SJ_SCAN_WIDTHS[16]),
            (reject[:12] if reject else "--", "#FF5252" if reject else "gray", SJ_SCAN_WIDTHS[17]),
        ]
        return cols

    # ═══ POSITION TABLE ═══

    def _update_pos_table(self, scroll_frame, row_list, cache_list, positions):
        """Update a positions table."""
        new_cache = []
        for i, p in enumerate(positions[:12]):
            row_data = self._build_pos_row(i, p)
            new_cache.append(row_data)

        if new_cache == cache_list:
            return
        cache_list.clear()
        cache_list.extend(new_cache)

        for w in scroll_frame.winfo_children():
            w.destroy()
        row_list.clear()

        for row_data in new_cache:
            row_frame = ctk.CTkFrame(scroll_frame, height=20)
            row_frame.pack(fill="x", padx=1, pady=0)
            for text, color, width in row_data:
                ctk.CTkLabel(
                    row_frame, text=text, width=width, height=18,
                    font=ctk.CTkFont(size=10), text_color=color,
                ).pack(side="left", padx=1)
            row_list.append(row_frame)

    def _build_pos_row(self, i, p):
        """Build position row data."""
        g = _g
        symbol = (g(p, 'symbol', '?') or '?').replace("USDT", "")
        side = g(p, 'side', '')
        direction = "LONG" if "LONG" in str(side).upper() or "BUY" in str(side).upper() else "SHORT"
        dir_color = _DIR_COLOR.get(direction, "gray")
        arrow = _DIR_ARROW.get(direction, "?")

        # ROI
        pnl = g(p, 'pnl_pct', 0) or 0
        roi_color = "#00E676" if pnl > 0 else "#FF5252" if pnl < 0 else "gray"

        # Leverage
        lev = g(p, 'leverage', 1) or 1

        # G from entry_bb_width (stores SL%) — config'den fee/mult oku
        sl_stored = g(p, 'entry_bb_width', 0) or 0
        fee_cfg, mult_cfg = 0.12, 1.5
        try:
            sj_cfg = self.controller.config.get("system_j", {})
            lev_cfg = sj_cfg.get("leverage", {}) if isinstance(sj_cfg, dict) else {}
            fee_cfg = lev_cfg.get("fee_pct", 0.08) + lev_cfg.get("slippage_pct", 0.04)
            mult_cfg = lev_cfg.get("sl_g_mult", 1.5)
        except Exception:
            pass
        G_approx = max(0, (sl_stored - fee_cfg) / mult_cfg) if sl_stored > 0 and mult_cfg > 0 else 0

        # Regime
        regime = g(p, 'entry_regime', '') or ''
        zone_short = {"TRENDING": "TRND", "RANGING": "RANG", "TREND": "TRND"}.get(regime, regime[:4] if regime else "--")

        # SL/TP
        entry_price = g(p, 'entry_price', 0) or 0
        initial_sl = g(p, 'initial_sl', 0) or 0
        initial_tp = g(p, 'initial_tp', 0) or 0
        sl_str = "--"
        tp_str = "--"
        if entry_price > 0 and initial_sl > 0:
            sl_str = f"{abs(initial_sl - entry_price) / entry_price * 100:.2f}"
        if entry_price > 0 and initial_tp > 0:
            tp_str = f"{abs(initial_tp - entry_price) / entry_price * 100:.1f}"

        # Trailing
        trailing = g(p, 'trailing_active', False)
        trail_text = "AKT" if trailing else "--"
        trail_color = "#00E676" if trailing else "gray"

        # Time remaining
        entry_time = g(p, 'entry_time', 0) or 0
        elapsed = time.time() - entry_time if entry_time > 0 else 0
        remaining = max(0, 8 * 3600 - elapsed)
        hrs = int(remaining // 3600)
        mins = int((remaining % 3600) // 60)
        time_str = f"{hrs}s{mins}d" if hrs > 0 else f"{mins}d"

        # Margin
        margin = g(p, 'margin_usdt', 0) or 0

        # Signal
        sig_text = f"{arrow} {direction[:1]}"

        cols = [
            ("", "gray", SJ_POS_WIDTHS[0]),
            (sig_text, dir_color, SJ_POS_WIDTHS[1]),
            (symbol, "#E0E0E0", SJ_POS_WIDTHS[2]),
            (f"{pnl:+.2f}%", roi_color, SJ_POS_WIDTHS[3]),
            (f"{lev}x", "#FFD54F", SJ_POS_WIDTHS[4]),
            (f"{G_approx:.2f}" if G_approx > 0 else "--", "#FF8A65", SJ_POS_WIDTHS[5]),
            (zone_short, _ZONE_COLORS.get(regime, "gray"), SJ_POS_WIDTHS[6]),
            (sl_str, "#FF8A65", SJ_POS_WIDTHS[7]),
            (tp_str, "#00BCD4", SJ_POS_WIDTHS[8]),
            (trail_text, trail_color, SJ_POS_WIDTHS[9]),
            (time_str, "gray", SJ_POS_WIDTHS[10]),
            (f"{margin:.1f}", "gray", SJ_POS_WIDTHS[11]),
        ]
        return cols

    def destroy(self):
        if hasattr(self, '_refresh_job') and self._refresh_job:
            self.after_cancel(self._refresh_job)
        super().destroy()
