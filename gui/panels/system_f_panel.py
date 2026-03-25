"""System F Panel — Son Kursun (Last Bullet) Tablosu.

Top 50 coin tarama sonuclari + aktif pozisyon + hedef bakiye takibi.
5 TF uyum, akilli kaldirac, hacim patlamasi, orderbook, BTC beta.
"""
import customtkinter as ctk

# ═══ Column Layout: System F Scan Results ═══
SF_HEADERS = [
    "#", "Sinyal", "Sembol",
    "Yon", "Uyum", "Skor",
    "5m", "15m", "1h", "4h", "1d",
    "EV%", "P(w)", "Av",
    "Lev", "SL%", "TP%",
    "Vol", "OB", "FR", "Red",
]
SF_WIDTHS = [
    24, 54, 80,
    40, 44, 44,
    30, 30, 30, 30, 30,
    44, 40, 50,
    40, 44, 44,
    40, 40, 44, 90,
]

# Important columns: Uyum, Skor, Lev
_SF_IMP = {5, 11, 14}

# ═══ Column Layout: System F Position ═══
SF_POS_HEADERS = [
    "", "Sinyal", "Sembol", "ROI%",
    "Lev", "SL%", "Hedef%",
    "Hedef$", "$",
]
SF_POS_WIDTHS = [
    22, 54, 90, 50,
    40, 50, 50,
    60, 42,
]

# ═══ Color Constants ═══
_RED_BORDER = "#E53935"
_RED_BG = "#2a0f0f"
_ACCENT = "#FF4081"  # System F rengi: magenta/pink

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
        if h in ("Lev", "SL%", "TP%"):
            hdr_color = "#FF8A65"
        elif h in ("Yon", "Uyum", "Skor"):
            hdr_color = "#00E676"
        elif h in ("5m", "15m", "1h", "4h", "1d"):
            hdr_color = "#26C6DA"
        elif h in ("EV%", "P(w)", "Av"):
            hdr_color = "#FFD54F"
        elif h in ("Vol", "OB", "FR"):
            hdr_color = _ACCENT
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


class SystemFPanel(ctk.CTkFrame):
    """System F scan results, positions, and target balance panel."""

    def __init__(self, parent, controller):
        super().__init__(parent)
        self.controller = controller
        self.pack(fill="both", expand=True)
        self._build_ui()
        self._start_refresh()

    def _build_ui(self) -> None:
        # ═══ TOP: HEDEF BAKIYE TRACKER ═══
        target_frame = ctk.CTkFrame(self, fg_color="#1a0a2e", border_color=_ACCENT,
                                     border_width=1, corner_radius=6)
        target_frame.pack(fill="x", padx=3, pady=(1, 0))

        top_row = ctk.CTkFrame(target_frame, fg_color="transparent")
        top_row.pack(fill="x", padx=6, pady=2)

        ctk.CTkLabel(top_row, text="SON KURSUN",
                     font=ctk.CTkFont(size=14, weight="bold"),
                     text_color=_ACCENT).pack(side="left")

        self._balance_lbl = ctk.CTkLabel(
            top_row, text="Bakiye: --",
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color="#FFD54F")
        self._balance_lbl.pack(side="left", padx=20)

        self._target_lbl = ctk.CTkLabel(
            top_row, text="Hedef: --",
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color="#00E676")
        self._target_lbl.pack(side="left", padx=10)

        self._bullet_lbl = ctk.CTkLabel(
            top_row, text="Kursun: 1",
            font=ctk.CTkFont(size=12),
            text_color="gray")
        self._bullet_lbl.pack(side="left", padx=10)

        # System F on/off toggle
        self._enabled_var = ctk.BooleanVar(
            value=self.controller.config.get("system_f.enabled", False))
        self._toggle = ctk.CTkSwitch(
            top_row, text="System F Aktif",
            variable=self._enabled_var,
            command=self._on_toggle,
            progress_color=_ACCENT,
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color="#FFFFFF")
        self._toggle.pack(side="right", padx=6)

        # Progress bar
        self._progress = ctk.CTkProgressBar(target_frame, height=8,
                                              progress_color=_ACCENT)
        self._progress.pack(fill="x", padx=6, pady=(0, 4))
        self._progress.set(0)

        # ═══ SETTINGS: Quick settings row ═══
        settings_frame = ctk.CTkFrame(self, fg_color="transparent")
        settings_frame.pack(fill="x", padx=3, pady=(2, 0))

        font_s = ctk.CTkFont(size=11)

        ctk.CTkLabel(settings_frame, text="Min Skor:", font=font_s,
                     text_color="gray").pack(side="left", padx=(4, 2))
        self._min_skor_entry = ctk.CTkEntry(settings_frame, width=40, height=24,
                                              font=font_s)
        self._min_skor_entry.insert(0, str(self.controller.config.get("system_f.min_skor", 85)))
        self._min_skor_entry.pack(side="left", padx=(0, 8))

        ctk.CTkLabel(settings_frame, text="Max Lev:", font=font_s,
                     text_color="gray").pack(side="left", padx=(0, 2))
        self._max_lev_entry = ctk.CTkEntry(settings_frame, width=40, height=24,
                                             font=font_s)
        self._max_lev_entry.insert(0, str(self.controller.config.get("system_f.max_kaldirac", 125)))
        self._max_lev_entry.pack(side="left", padx=(0, 8))

        ctk.CTkLabel(settings_frame, text="Vol Spike:", font=font_s,
                     text_color="gray").pack(side="left", padx=(0, 2))
        self._vol_spike_var = ctk.BooleanVar(
            value=self.controller.config.get("system_f.volume_spike_required", True))
        ctk.CTkCheckBox(settings_frame, text="", variable=self._vol_spike_var,
                        width=20, height=20, command=self._save_settings).pack(side="left", padx=(0, 8))

        ctk.CTkLabel(settings_frame, text="Hedef ROI%:", font=font_s,
                     text_color="gray").pack(side="left", padx=(0, 2))
        self._stage1_entry = ctk.CTkEntry(settings_frame, width=40, height=24,
                                            font=font_s)
        self._stage1_entry.insert(0, str(self.controller.config.get("system_f.target_roi_pct", 100)))
        self._stage1_entry.pack(side="left", padx=(0, 8))

        ctk.CTkLabel(settings_frame, text="Scan(s):", font=font_s,
                     text_color="gray").pack(side="left", padx=(0, 2))
        self._scan_interval_entry = ctk.CTkEntry(settings_frame, width=40, height=24,
                                                    font=font_s)
        self._scan_interval_entry.insert(0, str(self.controller.config.get(
            "system_f.scan_interval_seconds", 10)))
        self._scan_interval_entry.pack(side="left", padx=(0, 8))

        save_btn = ctk.CTkButton(settings_frame, text="Kaydet", width=60, height=24,
                                  font=ctk.CTkFont(size=11, weight="bold"),
                                  fg_color=_ACCENT, hover_color="#C51162",
                                  command=self._save_settings)
        save_btn.pack(side="left", padx=4)

        # ═══ TABLE 1: SYSTEM F SCAN RESULTS ═══
        scan_frame = ctk.CTkFrame(self)
        scan_frame.pack(fill="both", expand=True, padx=3, pady=(1, 0))

        ctk.CTkLabel(scan_frame, text="System F - Son Kursun Tarama",
                     font=ctk.CTkFont(size=12, weight="bold"),
                     text_color=_ACCENT).pack(anchor="w", padx=4, pady=(1, 0))

        _build_header(scan_frame, SF_HEADERS, SF_WIDTHS, _SF_IMP)

        self._scan_scroll = ctk.CTkScrollableFrame(scan_frame, height=340)
        self._scan_scroll.pack(fill="both", expand=True, padx=2)
        self._scan_rows = []
        self._scan_cache = []

        # ═══ TABLE 2: ACTIVE POSITION ═══
        pos_frame = ctk.CTkFrame(self)
        pos_frame.pack(fill="x", padx=3, pady=(1, 2))

        ctk.CTkLabel(pos_frame, text="Aktif Pozisyon (Son Kursun)",
                     font=ctk.CTkFont(size=12, weight="bold"),
                     text_color="#FFD54F").pack(anchor="w", padx=4, pady=(1, 0))

        _build_header(pos_frame, SF_POS_HEADERS, SF_POS_WIDTHS, set())

        self._pos_scroll = ctk.CTkScrollableFrame(pos_frame, height=60)
        self._pos_scroll.pack(fill="x", padx=2)
        self._pos_rows = []
        self._pos_cache = []

    # ═══ Toggle & Settings ═══

    def _on_toggle(self):
        enabled = self._enabled_var.get()
        self.controller.config.set("system_f.enabled", enabled)

        # System F aktif ise diger stratejileri kapat
        if enabled:
            self.controller.config.set("system_e.enabled", False)
            self.controller.config.set("system_d.enabled", False)
            self.controller.config.set("system_b.enabled", False)

        self.controller.config.save()

    def _save_settings(self):
        try:
            min_skor = int(self._min_skor_entry.get())
            self.controller.config.set("system_f.min_skor", min_skor)
        except ValueError:
            pass
        try:
            max_lev = int(self._max_lev_entry.get())
            self.controller.config.set("system_f.max_kaldirac", max_lev)
        except ValueError:
            pass
        try:
            target_roi = float(self._stage1_entry.get())
            self.controller.config.set("system_f.target_roi_pct", target_roi)
        except ValueError:
            pass
        try:
            scan_int = int(self._scan_interval_entry.get())
            self.controller.config.set("system_f.scan_interval_seconds", scan_int)
        except ValueError:
            pass

        self.controller.config.set("system_f.volume_spike_required",
                                    self._vol_spike_var.get())
        self.controller.config.save()

    # ═══ Refresh ═══

    def _start_refresh(self):
        self._refresh()

    def _refresh(self):
        try:
            self._update_balance_tracker()
        except Exception:
            pass
        try:
            self._update_scan_results()
        except Exception as e:
            from loguru import logger
            logger.error(f"[SysF Panel] scan refresh error: {e}")
        try:
            self._update_positions()
        except Exception as e:
            from loguru import logger
            logger.error(f"[SysF Panel] pos refresh error: {e}")
        self.after(3000, self._refresh)

    # ═══ Balance Tracker ═══

    def _update_balance_tracker(self):
        sf = self.controller.config.get("system_f", {})
        hedef = sf.get("hedef_bakiye", 0.90)
        baslangic = sf.get("baslangic_bakiye", 0.49)

        # Gercek bakiye
        balance = 0.0
        try:
            if self.controller.order_executor and hasattr(self.controller.order_executor, "get_balance"):
                balance = self.controller.order_executor.get_balance()
        except Exception:
            pass

        if balance <= 0:
            balance = baslangic

        self._balance_lbl.configure(text=f"Bakiye: {balance:.2f}$")
        self._target_lbl.configure(text=f"Hedef: {hedef:.2f}$")

        # Progress
        if hedef > baslangic and hedef > 0:
            progress = max(0, min((balance - baslangic) / (hedef - baslangic), 1.0))
        else:
            progress = 0
        self._progress.set(progress)

        # Kursun sayisi (kac kere katladik)
        if baslangic > 0 and balance > 0:
            import math
            bullets = max(1, int(math.log2(balance / baslangic)) + 1) if balance > baslangic else 1
        else:
            bullets = 1
        self._bullet_lbl.configure(text=f"Kursun: {bullets}")

        # Toggle senkronizasyonu
        enabled = self.controller.config.get("system_f.enabled", False)
        if self._enabled_var.get() != enabled:
            self._enabled_var.set(enabled)

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
        results = self.controller.get_system_f_results()
        if not results:
            self._ensure_rows(self._scan_scroll, self._scan_rows,
                              self._scan_cache, SF_WIDTHS, 1)
            sf_enabled = self.controller.config.get("system_f.enabled", False)
            if sf_enabled:
                msg = "Tarama sonucu yok — pusu modu"
            else:
                msg = "System F devre disi"
            empty = [(msg, "gray")] + [("", "gray")] * (len(SF_WIDTHS) - 1)
            self._update_row(self._scan_rows, self._scan_cache, 0, empty)
            return

        n = min(len(results), 50)
        self._ensure_rows(self._scan_scroll, self._scan_rows,
                          self._scan_cache, SF_WIDTHS, n)

        for i, r in enumerate(results[:n]):
            try:
                vals = self._build_scan_row(i, r)
            except Exception as e:
                from loguru import logger
                logger.error(f"[SysF Panel] row error #{i}: {e}")
                vals = [(str(i), "gray")] + [("ERR", "#FF5252")] * (len(SF_WIDTHS) - 1)
            bg = "#1c2d4d" if i % 2 == 0 else "transparent"
            self._update_row(self._scan_rows, self._scan_cache, i, vals, bg)

    def _build_scan_row(self, i, r):
        """Build row values for a SystemFScanResult (v2)."""
        row_color = "#00C853" if r.eligible else "gray"

        # Signal
        if r.eligible and r.direction:
            arrow = _DIR_ARROW.get(r.direction, "?")
            sig_text = f"ATIS {arrow}"
            sig_color = _DIR_COLOR.get(r.direction, "gray")
        else:
            sig_text = "---"
            sig_color = "gray"

        # Direction
        dir_text = r.direction if r.direction else "--"
        dir_color = _DIR_COLOR.get(r.direction, "gray")

        # Alignment
        uyum_text = f"{r.aligned_count}/{r.total_tfs}"
        uyum_color = "#00E676" if r.aligned_count == r.total_tfs else (
            "#FFD54F" if r.aligned_count >= 3 else "gray")

        # Composite score
        skor_text = f"{r.composite_score:.0f}" if r.composite_score > 0 else "--"
        skor_color = "#00E676" if r.composite_score >= 85 else (
            "#FFD54F" if r.composite_score >= 70 else "gray")

        # Per-TF direction indicators (5m, 15m, 1h, 4h, 1d)
        tf_map = {}
        for sig in r.tf_signals:
            tf_map[sig.timeframe] = sig

        tf_cells = []
        for tf_name in ["5m", "15m", "1h", "4h", "1d"]:
            sig = tf_map.get(tf_name)
            if sig:
                sym = _TF_DIR_SYMBOL.get(sig.strict_direction, "?")
                clr = _TF_DIR_COLOR.get(sig.strict_direction, "gray")
                tf_cells.append((sym, clr))
            else:
                tf_cells.append(("-", "gray"))

        # EV%
        ev_text = f"{r.ev_pct:+.0f}" if r.ev_pct != 0 else "--"
        ev_color = "#00E676" if r.ev_pct >= 30 else (
            "#FFD54F" if r.ev_pct >= 15 else (
            "#FF5252" if r.ev_pct < 0 else "gray"))

        # P(win)
        pw_text = f"{r.p_win:.0f}" if r.p_win > 0 else "--"
        pw_color = "#00E676" if r.p_win >= 70 else (
            "#FFD54F" if r.p_win >= 50 else "gray")

        # Av sinifi
        av_text = r.av_sinifi if r.av_sinifi else "--"
        av_colors = {"AYI": "#FF1744", "GEYIK": "#FFD54F",
                     "ORDEK": "#4FC3F7", "FARE": "gray"}
        av_color = av_colors.get(av_text, "gray")

        # Leverage
        lev_str = f"{r.smart_leverage}x" if r.smart_leverage > 1 else "--"

        # SL%
        sl_str = f"{r.sl_pct:.2f}" if r.sl_pct > 0 else "--"

        # Dynamic TP ROI%
        tp_str = f"{r.dynamic_tp_roi:.0f}" if r.dynamic_tp_roi > 0 else "--"
        tp_color = "#00E676" if r.dynamic_tp_roi >= 40 else (
            "#FFD54F" if r.dynamic_tp_roi >= 15 else "gray")

        # Volume spike
        vol_str = f"{r.volume_ratio:.1f}x" if r.volume_ratio > 0 else "--"
        vol_color = "#00E676" if r.volume_spike else (
            "#FFD54F" if r.volume_ratio > 1.0 else "gray")

        # Orderbook
        ob_str = f"{r.ob_imbalance:+.2f}" if r.ob_imbalance != 0 else "--"
        ob_color = "#00E676" if (
            (r.direction == "LONG" and r.ob_imbalance > 0.1) or
            (r.direction == "SHORT" and r.ob_imbalance < -0.1)
        ) else "#FF5252" if r.ob_thin_book else "gray"

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
            (dir_text, dir_color),
            (uyum_text, uyum_color),
            (skor_text, skor_color),
        ] + tf_cells + [
            (ev_text, ev_color),
            (pw_text, pw_color),
            (av_text, av_color),
            (lev_str, "#FFD54F"),
            (sl_str, "#FF8A65"),
            (tp_str, tp_color),
            (vol_str, vol_color),
            (ob_str, ob_color),
            (fr_str, fr_color),
            (reject, "#FF5252" if reject else "gray"),
        ]

    # ═══ TABLE 2: Position ═══

    def _update_positions(self):
        all_positions = self.controller.get_all_scanner_positions()
        sf_positions = [p for p in all_positions if p.get("entry_mode") == "SYSTEM_F"]

        if not sf_positions:
            self._ensure_rows(self._pos_scroll, self._pos_rows,
                              self._pos_cache, SF_POS_WIDTHS, 1)
            empty = [("Pusu modu — bekleniyor", _ACCENT)] + [("", "gray")] * (len(SF_POS_WIDTHS) - 1)
            self._update_row(self._pos_rows, self._pos_cache, 0, empty)
            return

        n = len(sf_positions)
        self._ensure_rows(self._pos_scroll, self._pos_rows,
                          self._pos_cache, SF_POS_WIDTHS, n)

        for i, pos in enumerate(sf_positions):
            vals = self._build_pos_row(pos)
            bg = "#1c2d4d" if i % 2 == 0 else "transparent"
            self._update_row(self._pos_rows, self._pos_cache, i, vals, bg)

    def _build_pos_row(self, pos):
        """Build row values for an active System F position."""
        side = pos.get("side", "")
        symbol = pos.get("symbol", "?").replace("USDT", "")
        roi = pos.get("roi_percent", 0)
        roi_color = "#00C853" if roi > 0 else "#FF1744" if roi < 0 else "gray"

        arrow = "\u25B2" if "LONG" in side else "\u25BC"
        sig_color = "#00C853" if "LONG" in side else "#FF1744"

        lev = pos.get("leverage", 1)

        # SL%
        entry = pos.get("entry_price", 0)
        sl = pos.get("initial_sl", 0)
        if entry > 0 and sl > 0:
            sl_pct = abs(sl - entry) / entry * 100
            sl_str = f"{sl_pct:.2f}%"
        else:
            sl_str = "--"

        # Hedef ROI%
        sf = self.controller.config.get("system_f", {})
        target_roi = sf.get("target_roi_pct", 100.0)
        hedef_roi_str = f"{target_roi:.0f}%"
        hedef_roi_color = "#00E676" if roi >= target_roi else "#FFD54F"

        # Hedef bakiye
        hedef = sf.get("hedef_bakiye", 0.90)
        hedef_str = f"{hedef:.2f}$"

        # PnL $
        margin = pos.get("margin_usdt", 0)
        pnl_usdt = margin * roi / 100 if margin > 0 else 0
        pnl_str = f"{pnl_usdt:+.3f}" if margin > 0 else "--"
        pnl_color = "#00C853" if pnl_usdt > 0 else "#FF1744" if pnl_usdt < 0 else "gray"

        return [
            ("", "gray"),
            (f"{arrow}", sig_color),
            (symbol, "#FFFFFF"),
            (f"{roi:+.2f}", roi_color),
            (f"{lev}x", "#FFD54F"),
            (sl_str, "#FF8A65"),
            (hedef_roi_str, hedef_roi_color),
            (hedef_str, "#00E676"),
            (pnl_str, pnl_color),
        ]
