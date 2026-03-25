"""System C Panel — Coklu Zaman Dilimi Analiz Tablosu.

Bir veya birden fazla coin icin 12 timeframe'de (1m -> 1w) kapsamli dalga + indikator analizi.
Combobox ile coin secimi, coklu coin destegi, tablo ile sonuc gosterimi.
"""
import threading
import customtkinter as ctk
from loguru import logger

# ═══ Column Layout ═══
SC_HEADERS = [
    "TF", "Mum", "Rejim", "ER", "Hurst",
    "RSI", "MACD", "ADX", "ATR%", "BB_W",
    "Vol",
    "\u2191Ort", "\u2191Max", "\u2191Min",
    "\u2193Ort", "\u2193Max", "\u2193Min",
    "G%", "I%", "CV",
    "SL%", "P.Liq%", "T.Liq%", "MaxLev",
]
SC_WIDTHS = [
    36, 34, 48, 40, 40,
    38, 44, 38, 42, 42,
    36,
    42, 42, 42,
    42, 42, 42,
    40, 40, 36,
    42, 46, 46, 46,
]

# Important columns (red border): Rejim, SL%, MaxLev
_SC_IMP = {2, 20, 23}

# Header color groups
_HDR_COLORS = {
    "TF": "#B0BEC5", "Mum": "#B0BEC5",
    "Rejim": "#CE93D8", "ER": "#4FC3F7", "Hurst": "#4FC3F7",
    "RSI": "#FFD54F", "MACD": "#FFD54F", "ADX": "#FFD54F",
    "ATR%": "#FF8A65", "BB_W": "#FF8A65", "Vol": "#FFD54F",
    "\u2191Ort": "#00E676", "\u2191Max": "#00E676", "\u2191Min": "#00E676",
    "\u2193Ort": "#FF5252", "\u2193Max": "#FF5252", "\u2193Min": "#FF5252",
    "G%": "#26C6DA", "I%": "#26C6DA", "CV": "#26C6DA",
    "SL%": "#FF8A65", "P.Liq%": "#FF8A65", "T.Liq%": "#FF8A65",
    "MaxLev": "#FFD54F",
}

_RED_BORDER = "#E53935"
_RED_BG = "#2a0f0f"
_SEPARATOR_BG = "#0D47A1"


def _build_header(parent, headers, widths, important_set):
    """Build a header row with red-bordered boxes for important columns."""
    hdr = ctk.CTkFrame(parent, fg_color="transparent")
    hdr.pack(fill="x", padx=2)
    font = ctk.CTkFont(size=12, weight="bold")

    for col_idx, (h, w) in enumerate(zip(headers, widths)):
        hdr_color = _HDR_COLORS.get(h, "#7799BB")

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


class SystemCPanel(ctk.CTkFrame):
    """System C multi-timeframe analysis panel."""

    def __init__(self, parent, controller):
        super().__init__(parent)
        self.controller = controller
        self.pack(fill="both", expand=True)
        self._rows = []
        self._cache = []
        self._selected_symbols: list[str] = []
        self._all_results: dict[str, list] = {}  # symbol -> [TimeframeAnalysis]
        self._build_ui()

    def _build_ui(self) -> None:
        # ═══ TITLE ═══
        ctk.CTkLabel(self, text="System C - Coklu Zaman Dilimi Analizi",
                     font=ctk.CTkFont(size=13, weight="bold"),
                     text_color="#81D4FA").pack(anchor="w", padx=8, pady=(4, 0))

        # ═══ TOP BAR: Coin selection ═══
        top_bar = ctk.CTkFrame(self, height=40)
        top_bar.pack(fill="x", padx=5, pady=(3, 1))
        top_bar.pack_propagate(False)

        ctk.CTkLabel(top_bar, text="Coin:",
                     font=ctk.CTkFont(size=12, weight="bold"),
                     text_color="#B0BEC5").pack(side="left", padx=(8, 4), pady=6)

        self._symbol_var = ctk.StringVar(value="BTCUSDT")
        self._symbol_combo = ctk.CTkComboBox(
            top_bar, variable=self._symbol_var,
            values=["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "DOGEUSDT",
                    "XRPUSDT", "ADAUSDT", "AVAXUSDT", "DOTUSDT", "LINKUSDT"],
            width=140, height=30, font=ctk.CTkFont(size=12),
            state="normal",
            dropdown_font=ctk.CTkFont(size=12),
        )
        self._symbol_combo.pack(side="left", padx=(0, 4), pady=5)

        # Ekle butonu
        ctk.CTkButton(
            top_bar, text="Ekle", width=50, height=30,
            font=ctk.CTkFont(size=11, weight="bold"),
            fg_color="#2E7D32", hover_color="#388E3C",
            command=self._on_add_symbol,
        ).pack(side="left", padx=(0, 4), pady=5)

        # Cikar butonu
        ctk.CTkButton(
            top_bar, text="Cikar", width=50, height=30,
            font=ctk.CTkFont(size=11),
            fg_color="#C62828", hover_color="#D32F2F",
            command=self._on_remove_symbol,
        ).pack(side="left", padx=(0, 4), pady=5)

        # Temizle butonu
        ctk.CTkButton(
            top_bar, text="Temizle", width=60, height=30,
            font=ctk.CTkFont(size=11),
            fg_color="#37474F", hover_color="#455A64",
            command=self._on_clear_symbols,
        ).pack(side="left", padx=(0, 8), pady=5)

        # Analiz Et butonu
        self._analyze_btn = ctk.CTkButton(
            top_bar, text="Analiz Et", width=100, height=30,
            font=ctk.CTkFont(size=12, weight="bold"),
            fg_color="#1565C0", hover_color="#1976D2",
            command=self._on_analyze,
        )
        self._analyze_btn.pack(side="left", padx=(0, 8), pady=5)

        # Coin Listesi Guncelle
        ctk.CTkButton(
            top_bar, text="Coin Listesi", width=90, height=30,
            font=ctk.CTkFont(size=11),
            fg_color="#37474F", hover_color="#455A64",
            command=self._on_refresh_symbols,
        ).pack(side="left", padx=(0, 8), pady=5)

        # Status label (sag taraf)
        self._status_var = ctk.StringVar(value="Hazir")
        ctk.CTkLabel(
            top_bar, textvariable=self._status_var,
            font=ctk.CTkFont(size=11), text_color="#78909C",
        ).pack(side="right", padx=8, pady=6)

        # ═══ SELECTED COINS BAR ═══
        coins_bar = ctk.CTkFrame(self, height=28)
        coins_bar.pack(fill="x", padx=5, pady=(1, 1))
        coins_bar.pack_propagate(False)

        ctk.CTkLabel(coins_bar, text="Secili:",
                     font=ctk.CTkFont(size=11, weight="bold"),
                     text_color="#90A4AE").pack(side="left", padx=(8, 4), pady=2)

        self._selected_label_var = ctk.StringVar(value="(henuz coin eklenmedi)")
        ctk.CTkLabel(
            coins_bar, textvariable=self._selected_label_var,
            font=ctk.CTkFont(size=11), text_color="#81D4FA",
        ).pack(side="left", padx=4, pady=2)

        # ═══ SETTINGS BAR ═══
        settings_bar = ctk.CTkFrame(self, height=36)
        settings_bar.pack(fill="x", padx=5, pady=(1, 1))
        settings_bar.pack_propagate(False)

        self._settings = {}
        settings_defs = [
            ("swing_n", "Swing N:", "10", 40),
            ("sl_g_carpani", "SL x G:", "1.5", 40),
            ("liq_carpani", "Liq Carp:", "2.0", 40),
            ("liq_seviyesi", "Liq Sev:", "0.7", 40),
            ("fee_pct", "Fee %:", "0.08", 45),
            ("mum_sayisi", "Mum:", "200", 40),
        ]

        for key, label, default, width in settings_defs:
            ctk.CTkLabel(settings_bar, text=label,
                         font=ctk.CTkFont(size=11),
                         text_color="#90A4AE").pack(side="left", padx=(6, 1), pady=4)
            var = ctk.StringVar(value=default)
            entry = ctk.CTkEntry(settings_bar, textvariable=var,
                                 width=width, height=26,
                                 font=ctk.CTkFont(size=11))
            entry.pack(side="left", padx=(0, 2), pady=4)
            self._settings[key] = var

        # G Kaynak combobox
        ctk.CTkLabel(settings_bar, text="G Kaynak:",
                     font=ctk.CTkFont(size=11),
                     text_color="#90A4AE").pack(side="left", padx=(6, 1), pady=4)
        self._g_kaynak_var = ctk.StringVar(value="max")
        ctk.CTkComboBox(
            settings_bar, variable=self._g_kaynak_var,
            values=["max", "avg_g", "avg_all"],
            width=80, height=26, font=ctk.CTkFont(size=11),
            state="readonly",
        ).pack(side="left", padx=(0, 4), pady=4)

        # ═══ TABLE ═══
        table_frame = ctk.CTkFrame(self)
        table_frame.pack(fill="both", expand=True, padx=3, pady=(1, 3))

        _build_header(table_frame, SC_HEADERS, SC_WIDTHS, _SC_IMP)

        self._scroll = ctk.CTkScrollableFrame(table_frame, height=500)
        self._scroll.pack(fill="both", expand=True, padx=2)

        # Initial empty rows
        self._show_empty_message("Coin ekleyip 'Analiz Et' butonuna basin")

        # Load settings from config
        self._load_settings_from_config()

    # ═══ Symbol Management ═══

    def _on_add_symbol(self) -> None:
        """Secili coin'i listeye ekle."""
        symbol = self._symbol_var.get().strip().upper()
        if not symbol:
            return
        if not symbol.endswith("USDT"):
            symbol += "USDT"
        if symbol not in self._selected_symbols:
            self._selected_symbols.append(symbol)
            self._update_selected_label()
            self._status_var.set(f"{symbol} eklendi ({len(self._selected_symbols)} coin)")

    def _on_remove_symbol(self) -> None:
        """Combobox'taki coin'i listeden cikar."""
        symbol = self._symbol_var.get().strip().upper()
        if not symbol.endswith("USDT"):
            symbol += "USDT"
        if symbol in self._selected_symbols:
            self._selected_symbols.remove(symbol)
            self._all_results.pop(symbol, None)
            self._update_selected_label()
            self._render_all_results()
            self._status_var.set(f"{symbol} cikarildi")

    def _on_clear_symbols(self) -> None:
        """Tum coinleri temizle."""
        self._selected_symbols.clear()
        self._all_results.clear()
        self._update_selected_label()
        self._show_empty_message("Coin ekleyip 'Analiz Et' butonuna basin")
        self._status_var.set("Liste temizlendi")

    def _update_selected_label(self) -> None:
        """Secili coinler etiketini guncelle."""
        if self._selected_symbols:
            text = ", ".join(self._selected_symbols)
            self._selected_label_var.set(f"{text}  ({len(self._selected_symbols)} coin)")
        else:
            self._selected_label_var.set("(henuz coin eklenmedi)")

    # ═══ Settings ═══

    def _load_settings_from_config(self) -> None:
        """Config'den System C ayarlarini yukle."""
        cfg = self.controller.config
        mapping = {
            "swing_n": ("system_c.swing_n", "10"),
            "sl_g_carpani": ("system_c.sl_g_carpani", "1.5"),
            "liq_carpani": ("system_c.liq_carpani", "2.0"),
            "liq_seviyesi": ("system_c.liq_seviyesi", "0.7"),
            "fee_pct": ("system_c.fee_pct", "0.08"),
            "mum_sayisi": ("system_c.mum_sayisi", "200"),
        }
        for key, (cfg_key, default) in mapping.items():
            val = cfg.get(cfg_key, default)
            if key in self._settings:
                self._settings[key].set(str(val))

        g_kaynak = cfg.get("system_c.g_kaynak", "max")
        self._g_kaynak_var.set(g_kaynak)

    def _save_settings_to_config(self) -> None:
        """GUI ayarlarini config'e kaydet."""
        cfg = self.controller.config
        float_keys = ["sl_g_carpani", "liq_carpani", "liq_seviyesi", "fee_pct"]
        int_keys = ["swing_n", "mum_sayisi"]

        for key, var in self._settings.items():
            try:
                val_str = var.get()
                if key in float_keys:
                    cfg.set(f"system_c.{key}", float(val_str))
                elif key in int_keys:
                    cfg.set(f"system_c.{key}", int(val_str))
            except (ValueError, TypeError):
                pass

        cfg.set("system_c.g_kaynak", self._g_kaynak_var.get())

    # ═══ Analysis ═══

    def _on_analyze(self) -> None:
        """Tum secili coinler icin analiz baslat."""
        if not self._selected_symbols:
            # Combobox'taki tek coin'i kullan
            symbol = self._symbol_var.get().strip().upper()
            if not symbol:
                self._status_var.set("Coin secin veya ekleyin!")
                return
            if not symbol.endswith("USDT"):
                symbol += "USDT"
            self._selected_symbols = [symbol]
            self._update_selected_label()

        self._save_settings_to_config()
        self._analyze_btn.configure(state="disabled", text="Analiz...")

        # Background thread — tum coinleri sirali analiz et
        symbols = list(self._selected_symbols)
        thread = threading.Thread(target=self._run_multi_analysis, args=(symbols,),
                                  daemon=True)
        thread.start()
        self._poll_analysis()

    def _run_multi_analysis(self, symbols: list[str]) -> None:
        """Background: tum coinleri sirayla analiz et."""
        analyzer = self.controller.get_system_c_analyzer()
        if not analyzer:
            return

        # Flag'i biz yonetiyoruz (analyze_symbol icinde dokunmayacak)
        analyzer._analyzing = True
        try:
            for i, symbol in enumerate(symbols):
                analyzer._progress = f"[{i+1}/{len(symbols)}] {symbol} analiz ediliyor..."
                results = analyzer.analyze_symbol(symbol)
                self._all_results[symbol] = results

            analyzer._progress = f"Tamamlandi: {len(symbols)} coin analiz edildi"
        except Exception as e:
            logger.error(f"System C multi-analysis error: {e}")
            analyzer._progress = f"Hata: {e}"
        finally:
            analyzer._analyzing = False

    def _poll_analysis(self) -> None:
        """Analiz tamamlanmasini kontrol et."""
        analyzer = self.controller.get_system_c_analyzer()
        if analyzer and analyzer.is_analyzing:
            self._status_var.set(analyzer.progress)
            self.after(500, self._poll_analysis)
            return

        # Bitti
        self._analyze_btn.configure(state="normal", text="Analiz Et")
        total_tf = sum(len(r) for r in self._all_results.values())
        self._status_var.set(
            f"Tamamlandi: {len(self._all_results)} coin, {total_tf} TF")
        self._render_all_results()

    def _on_refresh_symbols(self) -> None:
        """Binance'den coin listesini cek."""
        try:
            rest = self.controller.rest_client
            if not rest:
                self._status_var.set("REST client yok")
                return

            self._status_var.set("Coin listesi yukleniyor...")
            tickers = rest.get_all_24h_tickers()

            candidates = []
            for t in tickers:
                symbol = t.get("symbol", "")
                if not symbol.endswith("USDT"):
                    continue
                vol = float(t.get("quoteVolume", 0))
                if vol < 5_000_000:
                    continue
                candidates.append((symbol, vol))

            candidates.sort(key=lambda x: x[1], reverse=True)
            symbols = [s for s, _ in candidates[:70]]

            if symbols:
                self._symbol_combo.configure(values=symbols)
                self._status_var.set(f"{len(symbols)} coin yuklendi")
            else:
                self._status_var.set("Coin bulunamadi")

        except Exception as e:
            self._status_var.set(f"Hata: {e}")
            logger.error(f"System C refresh symbols: {e}")

    # ═══ Table Rendering ═══

    def _show_empty_message(self, msg: str) -> None:
        """Tek satirlik bos mesaj goster."""
        self._ensure_rows(1)
        vals = [(msg, "gray")] + [("", "gray")] * (len(SC_WIDTHS) - 1)
        self._update_row(0, vals)

    def _ensure_rows(self, count: int) -> None:
        """Tam olarak `count` satir olmasini sagla."""
        font = ctk.CTkFont(size=12)
        while len(self._rows) > count:
            frame, labels = self._rows.pop()
            frame.destroy()
        while len(self._cache) > count:
            self._cache.pop()
        while len(self._rows) < count:
            idx = len(self._rows)
            bg = "#1c2d4d" if idx % 2 == 0 else "#1a1a2e"
            row_frame = ctk.CTkFrame(self._scroll, fg_color=bg)
            row_frame.pack(fill="x", pady=0)
            labels = []
            for w in SC_WIDTHS:
                lbl = ctk.CTkLabel(row_frame, text="", width=w,
                                   font=font, text_color="gray")
                lbl.pack(side="left", padx=0)
                labels.append(lbl)
            self._rows.append((row_frame, labels))
            self._cache.append(None)

    def _update_row(self, idx: int, vals: list, bg: str = None) -> None:
        if idx >= len(self._cache) or self._cache[idx] == vals:
            return
        self._cache[idx] = vals
        frame, labels = self._rows[idx]
        if bg is not None and frame.cget("fg_color") != bg:
            frame.configure(fg_color=bg)
        for lbl, (val, color) in zip(labels, vals):
            lbl.configure(text=val, text_color=color)

    def _render_all_results(self) -> None:
        """Tum coin sonuclarini tabloya render et. Her coin icin ayirici + 12 TF satiri."""
        if not self._all_results:
            self._show_empty_message("Sonuc yok")
            return

        # Toplam satir sayisini hesapla: her coin icin 1 ayirici + 12 TF
        total_rows = 0
        for symbol in self._selected_symbols:
            if symbol in self._all_results:
                total_rows += 1 + len(self._all_results[symbol])  # separator + TF rows

        if total_rows == 0:
            self._show_empty_message("Sonuc yok")
            return

        self._ensure_rows(total_rows)

        row_idx = 0
        for symbol in self._selected_symbols:
            results = self._all_results.get(symbol)
            if not results:
                continue

            # Ayirici satir: coin adi
            sep_vals = [(f">>> {symbol}", "#FFD54F")] + \
                       [("", "#37474F")] * (len(SC_WIDTHS) - 1)
            self._update_row(row_idx, sep_vals, _SEPARATOR_BG)
            row_idx += 1

            # TF satirlari
            for r in results:
                vals = self._build_row(r)
                bg = "#1c2d4d" if row_idx % 2 == 0 else "#1a1a2e"
                self._update_row(row_idx, vals, bg)
                row_idx += 1

    def _build_row(self, r) -> list:
        """Bir TimeframeAnalysis satirini olustur."""
        ws = r.waves

        tf_color = "#81D4FA"
        mum_color = "#4CAF50" if r.candle_count >= 100 else "#FF9800" if r.candle_count > 0 else "gray"

        # Hata varsa
        if r.error:
            return [
                (r.timeframe, tf_color),
                ("0", "gray"),
                (r.error, "#FF5252"),
            ] + [("", "gray")] * (len(SC_WIDTHS) - 3)

        # Rejim
        regime_colors = {
            "TREND": "#4FC3F7", "RANGE": "#CE93D8",
            "GECIS": "#FFD54F", "?": "gray",
        }
        regime_color = regime_colors.get(r.regime, "gray")

        er_color = "#4FC3F7" if r.er > 0.35 else "#CE93D8" if r.er < 0.15 else "#B0BEC5"
        hurst_color = "#4FC3F7" if r.hurst > 0.55 else "#CE93D8" if r.hurst < 0.45 else "#B0BEC5"

        rsi_color = "#FF5252" if r.rsi > 70 else "#CE93D8" if r.rsi > 60 else \
                    "#00E676" if r.rsi < 30 else "#81C784" if r.rsi < 40 else "#B0BEC5"

        macd_color = "#00E676" if r.macd_hist > 0 else "#FF5252" if r.macd_hist < 0 else "gray"
        adx_color = "#4FC3F7" if r.adx > 25 else "#FFD54F" if r.adx > 18 else "#CE93D8"
        atr_color = "#FF9800" if r.atr_pct > 1.0 else "#FFD54F" if r.atr_pct > 0.3 else "#B0BEC5"
        bbw_color = "#FF9800" if r.bb_width > 0.03 else "#B0BEC5"
        vol_color = "#00E676" if r.volume_ratio > 1.5 else "#FFD54F" if r.volume_ratio > 1.2 else "#B0BEC5"

        up_ort_color = "#00E676" if ws.avg_up > 0 else "gray"
        up_max_color = "#4CAF50" if ws.max_up > 0 else "gray"
        up_min_color = "#81C784" if ws.min_up > 0 else "gray"
        dn_ort_color = "#FF5252" if ws.avg_down > 0 else "gray"
        dn_max_color = "#E53935" if ws.max_down > 0 else "gray"
        dn_min_color = "#EF9A9A" if ws.min_down > 0 else "gray"

        g_color = "#26C6DA" if ws.G > 0 else "gray"
        i_color = "#26C6DA" if ws.I > 0 else "gray"
        cv_color = "#FF1744" if ws.cv > 0.6 else "#FFD54F" if ws.cv > 0.4 else \
                   "#00E676" if ws.cv > 0 else "gray"

        sl_color = "#FF1744" if r.sl_pct > 5 else "#FF9800" if r.sl_pct > 2 else \
                   "#00E676" if r.sl_pct > 0 else "gray"
        pliq_color = "#FF1744" if r.pratik_liq_pct > 10 else "#FF9800" if r.pratik_liq_pct > 5 else "#B0BEC5"
        tliq_color = "#FF1744" if r.teorik_liq_pct > 15 else "#FF9800" if r.teorik_liq_pct > 8 else "#B0BEC5"

        if r.max_leverage >= 50:
            lev_color = "#00E676"
        elif r.max_leverage >= 20:
            lev_color = "#FFD54F"
        elif r.max_leverage >= 5:
            lev_color = "#FF9800"
        elif r.max_leverage > 0:
            lev_color = "#FF1744"
        else:
            lev_color = "gray"

        return [
            (r.timeframe, tf_color),
            (str(r.candle_count), mum_color),
            (r.regime, regime_color),
            (f"{r.er:.2f}", er_color),
            (f"{r.hurst:.2f}", hurst_color),
            (f"{r.rsi:.0f}", rsi_color),
            (f"{r.macd_hist:+.2f}" if abs(r.macd_hist) < 100 else f"{r.macd_hist:+.0f}", macd_color),
            (f"{r.adx:.0f}", adx_color),
            (f"{r.atr_pct:.2f}", atr_color),
            (f"{r.bb_width:.3f}" if r.bb_width < 1 else f"{r.bb_width:.2f}", bbw_color),
            (f"{r.volume_ratio:.1f}", vol_color),
            # Yukselis dalgalari
            (f"{ws.avg_up:.2f}" if ws.avg_up > 0 else "--", up_ort_color),
            (f"{ws.max_up:.2f}" if ws.max_up > 0 else "--", up_max_color),
            (f"{ws.min_up:.2f}" if ws.min_up > 0 else "--", up_min_color),
            # Dusus dalgalari
            (f"{ws.avg_down:.2f}" if ws.avg_down > 0 else "--", dn_ort_color),
            (f"{ws.max_down:.2f}" if ws.max_down > 0 else "--", dn_max_color),
            (f"{ws.min_down:.2f}" if ws.min_down > 0 else "--", dn_min_color),
            # G, I, CV
            (f"{ws.G:.2f}" if ws.G > 0 else "--", g_color),
            (f"{ws.I:.2f}" if ws.I > 0 else "--", i_color),
            (f"{ws.cv:.2f}" if ws.cv > 0 else "--", cv_color),
            # Kaldirac
            (f"{r.sl_pct:.2f}" if r.sl_pct > 0 else "--", sl_color),
            (f"{r.pratik_liq_pct:.2f}" if r.pratik_liq_pct > 0 else "--", pliq_color),
            (f"{r.teorik_liq_pct:.2f}" if r.teorik_liq_pct > 0 else "--", tliq_color),
            (f"{r.max_leverage}x" if r.max_leverage > 0 else "--", lev_color),
        ]
