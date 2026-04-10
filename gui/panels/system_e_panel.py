"""System E Panel — Yüksek Kaldıraç Yön Kesinliği Tablosu.

Top 50 coin tarama sonuçları + aktif pozisyonlar.
Tüm TF'lerde uyum, max kaldıraç, emergency SL, trailing.
"""
import customtkinter as ctk

# ═══ Column Layout: System E Scan Results ═══
SE_HEADERS = [
    "#", "Sinyal", "Sembol", "Hacim",
    "Yon", "Uyum", "Guc",
    "5m", "15m", "1h", "4h", "1d",
    "Lev", "Emrg%",
    "Trail.T", "Trail.M",
    "RSI", "ADX",
    "FR",
    "Red",
]
SE_WIDTHS = [
    24, 54, 86, 64,
    40, 44, 44,
    30, 30, 30, 30, 30,
    40, 44,
    48, 48,
    40, 40,
    44,
    90,
]

# Important columns (red border): Uyum, Lev, Emrg%
_SE_IMP = {5, 12, 13}

# ═══ Column Layout: System E Positions ═══
SE_POS_HEADERS = [
    "", "Sinyal", "Sembol", "ROI%",
    "Lev", "Emrg%", "Trail",
    "Kalan", "$",
]
SE_POS_WIDTHS = [
    22, 54, 90, 50,
    40, 50, 60,
    48, 42,
]

# ═══ Color Constants ═══
_RED_BORDER = "#E53935"
_RED_BG = "#2a0f0f"

_DIR_ARROW = {"LONG": "\u25B2", "SHORT": "\u25BC"}
_DIR_COLOR = {"LONG": "#00E676", "SHORT": "#FF1744"}
_TF_DIR_SYMBOL = {"LONG": "\u25B2", "SHORT": "\u25BC", "FLAT": "\u25CF"}
_TF_DIR_COLOR = {"LONG": "#00C853", "SHORT": "#FF1744", "FLAT": "gray"}


def _build_header(parent, headers, widths, important_set):
    """Build a header row with red-bordered boxes for important columns."""
    hdr = ctk.CTkFrame(parent, fg_color="transparent")
    hdr.pack(fill="x", padx=2)
    font = ctk.CTkFont(size=13, weight="bold")

    for col_idx, (h, w) in enumerate(zip(headers, widths)):
        if h in ("Lev", "Emrg%", "Trail.T", "Trail.M"):
            hdr_color = "#FF8A65"
        elif h in ("Yon", "Guc", "Uyum"):
            hdr_color = "#00E676"
        elif h in ("5m", "15m", "1h", "4h", "1d"):
            hdr_color = "#26C6DA"
        elif h in ("RSI", "ADX", "FR"):
            hdr_color = "#FFD54F"
        elif h == "Hacim":
            hdr_color = "#4FC3F7"
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


class SystemEPanel(ctk.CTkFrame):
    """System E scan results and positions panel."""

    def __init__(self, parent, controller):
        super().__init__(parent)
        self.controller = controller
        self.pack(fill="both", expand=True)
        self._build_ui()
        self._start_refresh()

    def _build_ui(self) -> None:
        # ═══ TABLE 1: SYSTEM E SCAN RESULTS ═══
        scan_frame = ctk.CTkFrame(self)
        scan_frame.pack(fill="both", expand=True, padx=3, pady=(1, 0))

        ctk.CTkLabel(scan_frame, text="System E - Yuksek Kaldirac Yon Kesinligi",
                     font=ctk.CTkFont(size=12, weight="bold"),
                     text_color="#FF6D00").pack(anchor="w", padx=4, pady=(1, 0))

        _build_header(scan_frame, SE_HEADERS, SE_WIDTHS, _SE_IMP)

        self._scan_scroll = ctk.CTkScrollableFrame(scan_frame, height=400)
        self._scan_scroll.pack(fill="both", expand=True, padx=2)
        self._scan_rows = []
        self._scan_cache = []

        # ═══ TABLE 2: ACTIVE SYSTEM E POSITIONS ═══
        pos_frame = ctk.CTkFrame(self)
        pos_frame.pack(fill="x", padx=3, pady=(1, 2))

        pos_top = ctk.CTkFrame(pos_frame, fg_color="transparent")
        pos_top.pack(fill="x", padx=4, pady=(1, 0))

        ctk.CTkLabel(pos_top, text="Aktif Pozisyonlar (System E)",
                     font=ctk.CTkFont(size=12, weight="bold"),
                     text_color="#FFD54F").pack(side="left")

        # Breakeven TP butonu
        self._tp_btn = ctk.CTkButton(
            pos_top, text="Breakeven TP Gonder",
            width=160, height=26,
            font=ctk.CTkFont(size=11, weight="bold"),
            fg_color="#FF6D00", hover_color="#E65100",
            command=self._on_breakeven_tp,
        )
        self._tp_btn.pack(side="right", padx=4)

        self._tp_status = ctk.CTkLabel(pos_top, text="", font=ctk.CTkFont(size=11),
                                        text_color="gray")
        self._tp_status.pack(side="right", padx=4)

        _build_header(pos_frame, SE_POS_HEADERS, SE_POS_WIDTHS, set())

        self._pos_scroll = ctk.CTkScrollableFrame(pos_frame, height=140)
        self._pos_scroll.pack(fill="x", padx=2)
        self._pos_rows = []
        self._pos_cache = []

    # ═══ Actions ═══

    def _on_breakeven_tp(self):
        """Tüm pozisyonlara breakeven TP emri gönder (SL'ye dokunmaz)."""
        self._tp_btn.configure(state="disabled", text="Gonderiliyor...")
        self._tp_status.configure(text="", text_color="gray")

        try:
            results = self.controller.place_breakeven_tp_all()
            ok_count = sum(1 for r in results if r.get("status") == "OK")
            fail_count = sum(1 for r in results if r.get("status", "").startswith("HATA"))
            total = len(results)

            if total == 0:
                self._tp_status.configure(text="Acik pozisyon yok", text_color="#FFD54F")
            elif fail_count == 0:
                self._tp_status.configure(
                    text=f"{ok_count} pozisyona TP gonderildi",
                    text_color="#00E676")
            else:
                self._tp_status.configure(
                    text=f"{ok_count} OK, {fail_count} hata",
                    text_color="#FF5252")
        except Exception as e:
            self._tp_status.configure(text=f"Hata: {e}", text_color="#FF5252")
        finally:
            self._tp_btn.configure(state="normal", text="Breakeven TP Gonder")

    # ═══ Refresh ═══

    def _start_refresh(self):
        self._refresh()

    def _refresh(self):
        if self.winfo_viewable():
            try:
                self._update_scan_results()
            except Exception as e:
                from loguru import logger
                logger.error(f"[SysE Panel] scan refresh error: {e}")
            try:
                self._update_positions()
            except Exception as e:
                from loguru import logger
                logger.error(f"[SysE Panel] pos refresh error: {e}")
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
        results = self.controller.get_system_e_results()
        if not results:
            self._ensure_rows(self._scan_scroll, self._scan_rows,
                              self._scan_cache, SE_WIDTHS, 1)
            se_enabled = self.controller.config.get("system_e.enabled", False)
            if se_enabled:
                msg = "Tarama sonucu yok"
            else:
                msg = "System E devre disi"
            empty = [(msg, "gray")] + [("", "gray")] * (len(SE_WIDTHS) - 1)
            self._update_row(self._scan_rows, self._scan_cache, 0, empty)
            return

        n = min(len(results), 50)
        self._ensure_rows(self._scan_scroll, self._scan_rows,
                          self._scan_cache, SE_WIDTHS, n)

        for i, r in enumerate(results[:n]):
            try:
                vals = self._build_scan_row(i, r)
            except Exception as e:
                from loguru import logger
                logger.error(f"[SysE Panel] row error #{i}: {e}")
                vals = [(str(i), "gray")] + [("ERR", "#FF5252")] * (len(SE_WIDTHS) - 1)
            bg = "#1c2d4d" if i % 2 == 0 else "transparent"
            self._update_row(self._scan_rows, self._scan_cache, i, vals, bg)

    def _build_scan_row(self, i, r):
        """Build row values for a SystemEScanResult."""
        row_color = "#00C853" if r.eligible else "gray"

        # Signal
        if r.eligible and r.direction:
            arrow = _DIR_ARROW.get(r.direction, "?")
            sig_text = f"GIRIS {arrow}"
            sig_color = _DIR_COLOR.get(r.direction, "gray")
        else:
            sig_text = "---"
            sig_color = "gray"

        # Direction
        dir_text = r.direction if r.direction else "--"
        dir_color = _DIR_COLOR.get(r.direction, "gray")

        # Alignment
        uyum_text = f"{r.aligned_count}/{r.total_tfs}"
        uyum_color = "#00E676" if r.aligned_count == r.total_tfs else "#FFD54F" if r.aligned_count >= 3 else "gray"

        # Strength
        guc_text = f"{r.direction_strength:.0%}" if r.direction_strength > 0 else "--"
        guc_color = "#00E676" if r.direction_strength > 0.7 else "#FFD54F" if r.direction_strength > 0.4 else "gray"

        # Volume
        vol = r.volume_24h
        if vol >= 1e9:
            vol_str = f"{vol/1e9:.1f}B"
        elif vol >= 1e6:
            vol_str = f"{vol/1e6:.0f}M"
        else:
            vol_str = f"{vol/1e3:.0f}K"

        # Per-TF direction indicators
        tf_map = {}
        for sig in r.tf_signals:
            tf_map[sig.timeframe] = sig

        tf_cells = []
        for tf_name, _ in [("5m", 5), ("15m", 15), ("1h", 60), ("4h", 240), ("1d", 1440)]:
            sig = tf_map.get(tf_name)
            if sig:
                sym = _TF_DIR_SYMBOL.get(sig.direction, "?")
                clr = _TF_DIR_COLOR.get(sig.direction, "gray")
                tf_cells.append((sym, clr))
            else:
                tf_cells.append(("-", "gray"))

        # Leverage
        lev_str = f"{r.leverage}x" if r.leverage > 1 else "--"

        # Emergency SL
        emrg_str = f"{r.emergency_sl_pct:.2f}" if r.emergency_sl_pct > 0 else "--"

        # Trailing (fiyat %'si olarak göster + ROI karşılığı)
        roi_t = r.trailing_trigger_pct * r.leverage if r.leverage > 0 else 0
        roi_m = r.trailing_callback_pct * r.leverage if r.leverage > 0 else 0
        trail_t = f"{r.trailing_trigger_pct:.2f}" if r.trailing_trigger_pct > 0 else "--"
        trail_m = f"{r.trailing_callback_pct:.2f}" if r.trailing_callback_pct > 0 else "--"

        # RSI (5m)
        rsi_val = 50
        for sig in r.tf_signals:
            if sig.timeframe == "5m":
                rsi_val = sig.rsi_value
                break
        rsi_str = f"{rsi_val:.0f}"
        rsi_color = "#00C853" if 40 < rsi_val < 60 else "#FFD54F" if 30 < rsi_val < 70 else "#FF5252"

        # ADX (1h)
        adx_val = 0
        for sig in r.tf_signals:
            if sig.timeframe == "1h":
                adx_val = sig.adx_value
                break
        adx_str = f"{adx_val:.0f}"

        # Funding rate
        fr = r.funding_rate
        fr_str = f"{fr*100:.3f}" if fr != 0 else "0"
        fr_color = "#FF5252" if abs(fr) > 0.0005 else "gray"

        # Reject reason
        reject = r.reject_reason if r.reject_reason else ""

        return [
            (str(r.rank), row_color),
            (sig_text, sig_color),
            (r.symbol.replace("USDT", ""), row_color),
            (vol_str, "#4FC3F7"),
            (dir_text, dir_color),
            (uyum_text, uyum_color),
            (guc_text, guc_color),
        ] + tf_cells + [
            (lev_str, "#FFD54F"),
            (emrg_str, "#FF8A65"),
            (trail_t, "gray"),
            (trail_m, "gray"),
            (rsi_str, rsi_color),
            (adx_str, "#CE93D8"),
            (fr_str, fr_color),
            (reject, "#FF5252" if reject else "gray"),
        ]

    # ═══ TABLE 2: Positions ═══

    def _update_positions(self):
        all_positions = self.controller.get_all_scanner_positions()
        se_positions = [p for p in all_positions if p.get("entry_mode") == "SYSTEM_E"]

        if not se_positions:
            self._ensure_rows(self._pos_scroll, self._pos_rows,
                              self._pos_cache, SE_POS_WIDTHS, 1)
            empty = [("Pozisyon yok", "gray")] + [("", "gray")] * (len(SE_POS_WIDTHS) - 1)
            self._update_row(self._pos_rows, self._pos_cache, 0, empty)
            return

        n = len(se_positions)
        self._ensure_rows(self._pos_scroll, self._pos_rows,
                          self._pos_cache, SE_POS_WIDTHS, n)

        for i, pos in enumerate(se_positions):
            vals = self._build_pos_row(pos)
            bg = "#1c2d4d" if i % 2 == 0 else "transparent"
            self._update_row(self._pos_rows, self._pos_cache, i, vals, bg)

    def _build_pos_row(self, pos):
        """Build row values for an active System E position."""
        side = pos.get("side", "")
        symbol = pos.get("symbol", "?").replace("USDT", "")
        roi = pos.get("roi_percent", 0)
        roi_color = "#00C853" if roi > 0 else "#FF1744" if roi < 0 else "gray"

        arrow = "\u25B2" if "LONG" in side else "\u25BC"
        sig_color = "#00C853" if "LONG" in side else "#FF1744"

        lev = pos.get("leverage", 1)

        # Emergency SL from position emergency_close_price
        entry = pos.get("entry_price", 0)
        emrg = pos.get("emergency_close_price", 0)
        if entry > 0 and emrg > 0:
            emrg_pct = abs(emrg - entry) / entry * 100
            emrg_str = f"{emrg_pct:.2f}%"
        else:
            emrg_str = "--"

        # Trailing status
        trail_str = "50%/10%"

        # Time remaining (no time limit for System E)
        kalan_str = "--"

        # PnL $
        margin = pos.get("margin_usdt", 0)
        pnl_usdt = margin * roi / 100 if margin > 0 else 0
        pnl_str = f"{pnl_usdt:+.2f}" if margin > 0 else "--"
        pnl_color = "#00C853" if pnl_usdt > 0 else "#FF1744" if pnl_usdt < 0 else "gray"

        return [
            ("", "gray"),
            (f"{arrow}", sig_color),
            (symbol, "#FFFFFF"),
            (f"{roi:+.2f}", roi_color),
            (f"{lev}x", "#FFD54F"),
            (emrg_str, "#FF8A65"),
            (trail_str, "gray"),
            (kalan_str, "gray"),
            (pnl_str, pnl_color),
        ]
