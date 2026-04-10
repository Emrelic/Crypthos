"""System G Panel — Coin Bazli Optimizasyon Tablosu.

Per-coin backtest optimization sonuclari + aktif pozisyonlar.
"""
import customtkinter as ctk
from loguru import logger

# ═══ Column Layout: System G Scan Results ═══
SG_SCAN_HEADERS = [
    "#", "Symbol", "Yon", "Uyum", "OptDurum",
    "Lev", "TP%", "SL",
    "BT_ROI%", "WR%", "LiqR%",
    "Trade", "Skor", "Red",
]
SG_SCAN_WIDTHS = [
    28, 80, 48, 40, 56,
    36, 44, 44,
    56, 44, 44,
    40, 44, 80,
]

# Important columns (red border): OptDurum, Lev, BT_ROI%
_SG_IMP = {4, 5, 8}

# ═══ Column Layout: System G Positions ═══
SG_POS_HEADERS = [
    "", "Symbol", "Yon", "ROI%",
    "Lev", "TP%", "SL",
    "OptSkor", "HoldTime",
]
SG_POS_WIDTHS = [
    22, 90, 54, 50,
    40, 44, 44,
    56, 60,
]

# ═══ Color Constants ═══
_ACCENT = "#7C4DFF"
_RED_BORDER = "#E53935"
_RED_BG = "#2a0f0f"

_DIR_COLOR = {"LONG": "#00E676", "SHORT": "#FF5252"}
_DIR_ARROW = {"LONG": "\u25B2", "SHORT": "\u25BC"}

_OPT_STATUS_COLORS = {
    "CACHED": "#00E676",
    "FRESH": "#FFD54F",
    "PENDING": "#FF8A65",
    "SKIP": "#78909C",
    "FAILED": "#78909C",
}


def _build_header(parent, headers, widths, important_set):
    """Build a header row with red-bordered boxes for important columns."""
    hdr = ctk.CTkFrame(parent, fg_color="transparent")
    hdr.pack(fill="x", padx=2)
    font = ctk.CTkFont(size=13, weight="bold")

    for col_idx, (h, w) in enumerate(zip(headers, widths)):
        # Color coding by column group
        if h in ("Lev", "TP%", "SL"):
            hdr_color = "#FF8A65"   # coral - risk
        elif h in ("Yon", "Uyum"):
            hdr_color = "#00E676"   # green - direction
        elif h in ("OptDurum", "OptSkor"):
            hdr_color = _ACCENT     # purple - optimization
        elif h in ("BT_ROI%", "WR%", "LiqR%", "Trade"):
            hdr_color = "#26C6DA"   # cyan - backtest
        elif h == "Skor":
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


class SystemGPanel(ctk.CTkFrame):
    """System G scan results and positions panel."""

    def __init__(self, parent, controller):
        super().__init__(parent)
        self.controller = controller
        self.pack(fill="both", expand=True)
        self._scan_rows = []
        self._scan_cache = []
        self._pos_rows = []
        self._pos_cache = []
        self._build_ui()
        self._start_refresh()

    def _build_ui(self) -> None:
        # ═══ STATS BAR ═══
        stats_frame = ctk.CTkFrame(self, fg_color="transparent")
        stats_frame.pack(fill="x", padx=4, pady=(2, 0))

        self._stats_label = ctk.CTkLabel(
            stats_frame, text="Tarama: 0 | Uygun: 0 | Pozisyon: 0",
            font=ctk.CTkFont(size=11), text_color="gray",
        )
        self._stats_label.pack(side="right")

        # ═══ TABLE 1: SYSTEM G SCAN RESULTS ═══
        scan_frame = ctk.CTkFrame(self)
        scan_frame.pack(fill="both", expand=True, padx=3, pady=(1, 0))

        ctk.CTkLabel(scan_frame, text="System G - Coin Bazli Optimizasyon",
                     font=ctk.CTkFont(size=12, weight="bold"),
                     text_color=_ACCENT).pack(anchor="w", padx=4, pady=(1, 0))

        _build_header(scan_frame, SG_SCAN_HEADERS, SG_SCAN_WIDTHS, _SG_IMP)

        self._scan_scroll = ctk.CTkScrollableFrame(scan_frame, height=400)
        self._scan_scroll.pack(fill="both", expand=True, padx=2)

        # ═══ TABLE 2: ACTIVE SYSTEM G POSITIONS ═══
        pos_frame = ctk.CTkFrame(self)
        pos_frame.pack(fill="x", padx=3, pady=(1, 2))

        ctk.CTkLabel(pos_frame, text="Aktif Pozisyonlar (System G)",
                     font=ctk.CTkFont(size=12, weight="bold"),
                     text_color="#FFD54F").pack(anchor="w", padx=4, pady=(1, 0))

        _build_header(pos_frame, SG_POS_HEADERS, SG_POS_WIDTHS, set())

        self._pos_scroll = ctk.CTkScrollableFrame(pos_frame, height=140)
        self._pos_scroll.pack(fill="x", padx=2)

    # ═══ Refresh ═══

    def _start_refresh(self):
        self._refresh()

    def _refresh(self):
        if self.winfo_viewable():
            try:
                self._update_scan_results()
            except Exception as e:
                logger.error(f"[SysG Panel] scan refresh error: {e}")
            try:
                self._update_positions()
            except Exception as e:
                logger.error(f"[SysG Panel] pos refresh error: {e}")
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

    # ═══ Public update method ═══

    def update_data(self, scan_results=None, positions=None):
        """Called from main_window refresh cycle to push data."""
        if scan_results is not None:
            self._update_scan_table(scan_results)
        if positions is not None:
            self._update_pos_table(positions)

    # ═══ TABLE 1: Scan Results ═══

    def _update_scan_results(self):
        results = getattr(self.controller, '_last_system_g_results', None)
        if results is None:
            results = []
            try:
                results = self.controller.get_system_g_results()
            except AttributeError:
                pass

        self._update_scan_table(results)

    def _update_scan_table(self, results):
        if not results:
            self._ensure_rows(self._scan_scroll, self._scan_rows,
                              self._scan_cache, SG_SCAN_WIDTHS, 1)
            sg_enabled = False
            try:
                sg_enabled = self.controller.config.get("system_g.enabled", False)
            except Exception:
                pass
            if sg_enabled:
                msg = "Tarama sonucu yok"
            else:
                msg = "System G devre disi"
            empty = [(msg, "gray")] + [("", "gray")] * (len(SG_SCAN_WIDTHS) - 1)
            self._update_row(self._scan_rows, self._scan_cache, 0, empty)
            self._stats_label.configure(text="Tarama: 0 | Uygun: 0 | Pozisyon: 0")
            return

        n = min(len(results), 50)
        self._ensure_rows(self._scan_scroll, self._scan_rows,
                          self._scan_cache, SG_SCAN_WIDTHS, n)

        eligible_count = 0
        for i, r in enumerate(results[:n]):
            try:
                vals = self._build_scan_row(i, r)
                if self._is_eligible(r):
                    eligible_count += 1
            except Exception as e:
                logger.error(f"[SysG Panel] row error #{i}: {e}")
                vals = [(str(i + 1), "gray")] + [("ERR", "#FF5252")] * (len(SG_SCAN_WIDTHS) - 1)
            bg = "#1c2d4d" if i % 2 == 0 else "transparent"
            self._update_row(self._scan_rows, self._scan_cache, i, vals, bg)

        # Update stats
        pos_count = 0
        try:
            all_pos = self.controller.get_all_scanner_positions()
            pos_count = sum(1 for p in all_pos if p.get("entry_mode") == "SYSTEM_G")
        except Exception:
            pass
        self._stats_label.configure(
            text=f"Tarama: {n} | Uygun: {eligible_count} | Pozisyon: {pos_count}")

    def _is_eligible(self, r):
        """Check if scan result is eligible (duck-typed)."""
        if hasattr(r, 'eligible'):
            return r.eligible
        if isinstance(r, dict):
            return r.get('eligible', False)
        return False

    def _build_scan_row(self, i, r):
        """Build row values for a System G scan result (dict or object)."""
        # Support both dict and object access
        def g(key, default=""):
            if isinstance(r, dict):
                return r.get(key, default)
            return getattr(r, key, default)

        eligible = g('eligible', False)
        row_color = "#00C853" if eligible else "gray"

        # Symbol
        symbol = g('symbol', '?')
        if isinstance(symbol, str):
            symbol = symbol.replace("USDT", "")

        # Direction
        direction = g('direction', '')
        dir_text = direction if direction else "--"
        dir_color = _DIR_COLOR.get(direction, "gray")

        # Alignment
        uyum = g('alignment', 0)
        if isinstance(uyum, float):
            uyum_text = f"{uyum:.0%}"
            uyum_color = "#00E676" if uyum >= 0.8 else "#FFD54F" if uyum >= 0.5 else "gray"
        else:
            uyum_text = str(uyum) if uyum else "--"
            uyum_color = "#00E676" if uyum else "gray"

        # Optimization status
        opt_status = g('opt_status', 'SKIP')
        opt_color = _OPT_STATUS_COLORS.get(opt_status, "#78909C")
        opt_short = opt_status[:5] if opt_status else "SKIP"

        # Leverage
        lev = g('leverage', 0)
        lev_str = f"{lev}x" if lev and lev > 1 else "--"

        # TP%
        tp_pct = g('tp_pct', 0)
        tp_str = f"{tp_pct:.2f}" if tp_pct and tp_pct > 0 else "--"

        # SL
        sl_pct = g('sl_pct', 0)
        sl_str = f"{sl_pct:.2f}" if sl_pct and sl_pct > 0 else "--"

        # Backtest ROI%
        bt_roi = g('bt_roi_pct', 0)
        bt_roi_str = f"{bt_roi:+.1f}" if bt_roi else "--"
        bt_roi_color = "#00E676" if bt_roi and bt_roi > 0 else "#FF5252" if bt_roi and bt_roi < 0 else "gray"

        # Win rate
        wr = g('win_rate', 0)
        wr_str = f"{wr:.0f}" if wr else "--"
        wr_color = "#00E676" if wr and wr > 55 else "#FFD54F" if wr and wr > 45 else "gray"

        # Liquidation risk
        liq_risk = g('liq_risk_pct', 0)
        liq_str = f"{liq_risk:.1f}" if liq_risk else "--"
        liq_color = "#00E676" if liq_risk and liq_risk < 30 else "#FFD54F" if liq_risk and liq_risk < 60 else "#FF5252"

        # Trade count
        trade_count = g('trade_count', 0)
        trade_str = str(trade_count) if trade_count else "--"

        # Score
        skor = g('score', 0)
        skor_str = f"{skor:.0f}" if skor else "--"
        skor_color = "#00E676" if skor and skor >= 70 else "#FFD54F" if skor and skor >= 50 else "gray"

        # Reject reason
        reject = g('reject_reason', '')
        reject = reject if reject else ""

        return [
            (str(i + 1), row_color),
            (symbol, row_color),
            (dir_text, dir_color),
            (uyum_text, uyum_color),
            (opt_short, opt_color),
            (lev_str, "#FFD54F"),
            (tp_str, "#FF8A65"),
            (sl_str, "#FF8A65"),
            (bt_roi_str, bt_roi_color),
            (wr_str, wr_color),
            (liq_str, liq_color),
            (trade_str, "#26C6DA"),
            (skor_str, skor_color),
            (reject, "#FF5252" if reject else "gray"),
        ]

    # ═══ TABLE 2: Positions ═══

    def _update_positions(self):
        all_positions = []
        try:
            all_positions = self.controller.get_all_scanner_positions()
        except Exception:
            pass
        sg_positions = [p for p in all_positions if p.get("entry_mode") == "SYSTEM_G"]
        self._update_pos_table(sg_positions)

    def _update_pos_table(self, positions):
        if not positions:
            self._ensure_rows(self._pos_scroll, self._pos_rows,
                              self._pos_cache, SG_POS_WIDTHS, 1)
            empty = [("Pozisyon yok", "gray")] + [("", "gray")] * (len(SG_POS_WIDTHS) - 1)
            self._update_row(self._pos_rows, self._pos_cache, 0, empty)
            return

        n = len(positions)
        self._ensure_rows(self._pos_scroll, self._pos_rows,
                          self._pos_cache, SG_POS_WIDTHS, n)

        for i, pos in enumerate(positions):
            vals = self._build_pos_row(pos)
            bg = "#1c2d4d" if i % 2 == 0 else "transparent"
            self._update_row(self._pos_rows, self._pos_cache, i, vals, bg)

    def _build_pos_row(self, pos):
        """Build row values for an active System G position."""
        side = pos.get("side", "")
        symbol = pos.get("symbol", "?").replace("USDT", "")
        roi = pos.get("roi_percent", 0)
        roi_color = "#00E676" if roi > 0 else "#FF5252" if roi < 0 else "gray"

        # Direction arrow
        arrow = "\u25B2" if "LONG" in side else "\u25BC"
        sig_color = "#00E676" if "LONG" in side else "#FF5252"

        # Direction text
        dir_text = "LONG" if "LONG" in side else "SHORT" if "SHORT" in side else side
        dir_color = _DIR_COLOR.get(dir_text, "gray")

        lev = pos.get("leverage", 1)

        # TP% from position
        entry_price = pos.get("entry_price", 0)
        tp_price = pos.get("tp", 0)
        if entry_price > 0 and tp_price > 0:
            tp_pct = abs(tp_price - entry_price) / entry_price * 100
            tp_str = f"{tp_pct:.2f}"
        else:
            tp_str = "--"

        # SL from position
        sl_price = pos.get("sl", 0)
        if entry_price > 0 and sl_price > 0:
            sl_pct = abs(entry_price - sl_price) / entry_price * 100
            sl_str = f"{sl_pct:.2f}"
        else:
            sl_str = "--"

        # Optimization score
        opt_skor = pos.get("opt_score", 0)
        opt_str = f"{opt_skor:.0f}" if opt_skor else "--"
        opt_color = _ACCENT if opt_skor else "gray"

        # Hold time
        hold_seconds = pos.get("hold_seconds", 0)
        if hold_seconds >= 3600:
            hold_str = f"{hold_seconds / 3600:.1f}h"
        else:
            hold_str = f"{hold_seconds / 60:.0f}m"

        return [
            (arrow, sig_color),
            (symbol, "#FFFFFF"),
            (dir_text, dir_color),
            (f"{roi:+.2f}", roi_color),
            (f"{lev}x", "#FFD54F"),
            (tp_str, "#FF8A65"),
            (sl_str, "#FF8A65"),
            (opt_str, opt_color),
            (hold_str, "gray"),
        ]
