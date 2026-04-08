"""System N Panel — AlphaTrend PRO v2: Sinyal bazli tarama, pozisyon ve karar tablosu."""
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
SN_SCAN_HEADERS = [
    "#", "Sinyal", "Sembol", "Fiyat",
    "AlphaTrend", "AT[2]", "Trend",
    "ADX", "RSI", "MFI", "ATR",
    "ADX_S", "ADX_D", "Slope", "Filtre",
]
SN_SCAN_WIDTHS = [
    32, 74, 105, 92,
    92, 92, 54,
    56, 52, 52, 76,
    50, 50, 50, 54,
]
_SN_IMP = {1, 2, 3, 6}

# ═══ Column Layout: Positions ═══
SN_POS_HEADERS = [
    "#", "Sembol", "Yon", "Giris Fiyat", "Guncel Fiyat",
    "ROI%", "Kaldirac", "Miktar", "Marjin $", "Sure",
]
SN_POS_WIDTHS = [
    32, 105, 72, 92, 92,
    76, 58, 80, 72, 72,
]
_SN_POS_IMP = {1, 2, 5}

# ═══ Column Layout: Decisions ═══
SN_DEC_HEADERS = [
    "Saat", "Sembol", "Sinyal", "Karar", "Fiyat",
    "Trend", "ADX", "RSI", "Aciklama",
]
SN_DEC_WIDTHS = [
    68, 105, 60, 128, 92,
    50, 56, 52, 240,
]
_SN_DEC_IMP = {1, 2, 3}

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


class SystemNPanel(ctk.CTkFrame):
    """System N AlphaTrend PRO v2 — tab'siz, segmented button ile sekme degistirme."""

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
            logger.info("[SysN Panel] UI built OK")
        except Exception as e:
            logger.error(f"[SysN Panel] BUILD FAILED: {e}")
            import traceback
            logger.error(traceback.format_exc())
            ctk.CTkLabel(
                self, text=f"System N Panel HATA:\n{e}",
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
        for label, key in [("TARAMA", "scan"), ("POZISYONLAR", "pos"),
                           ("KARARLAR", "dec"), ("AYARLAR", "settings")]:
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
        _make_header_row(self._scan_frame, SN_SCAN_HEADERS, SN_SCAN_WIDTHS, _SN_IMP)
        self._scan_scroll = ctk.CTkScrollableFrame(self._scan_frame)
        self._scan_scroll.pack(fill="both", expand=True, padx=4, pady=(2, 4))

        # --- POS ---
        self._pos_frame = ctk.CTkFrame(self)
        ctk.CTkLabel(
            self._pos_frame, text="AKTIF POZISYONLAR",
            font=ctk.CTkFont(size=_TITLE_FONT_SZ, weight="bold"),
            text_color="#FFFFFF",
        ).pack(anchor="w", padx=8, pady=(4, 0))
        _make_header_row(self._pos_frame, SN_POS_HEADERS, SN_POS_WIDTHS, _SN_POS_IMP)
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

        _make_header_row(self._dec_frame, SN_DEC_HEADERS, SN_DEC_WIDTHS, _SN_DEC_IMP)
        self._dec_scroll = ctk.CTkScrollableFrame(self._dec_frame)
        self._dec_scroll.pack(fill="both", expand=True, padx=4, pady=(2, 4))

        # --- SETTINGS ---
        self._settings_frame = ctk.CTkFrame(self)
        self._build_settings(self._settings_frame)

        # Baslangicta SCAN goster
        self._scan_frame.pack(fill="both", expand=True, padx=2, pady=2)

    def _switch_tab(self, key: str) -> None:
        """Sekme degistir."""
        if key == self._active_tab:
            return
        # Hide current
        for frame_key, frame in [("scan", self._scan_frame),
                                   ("pos", self._pos_frame),
                                   ("dec", self._dec_frame),
                                   ("settings", self._settings_frame)]:
            frame.pack_forget()
        # Show selected
        target = {"scan": self._scan_frame, "pos": self._pos_frame,
                  "dec": self._dec_frame, "settings": self._settings_frame}[key]
        target.pack(fill="both", expand=True, padx=2, pady=2)
        self._active_tab = key
        # Update button colors
        for k, btn in self._tab_btns.items():
            btn.configure(fg_color=_TAB_ACTIVE if k == key else _TAB_INACTIVE)

    # ───────────────────────────────────────────────
    #  SETTINGS
    # ───────────────────────────────────────────────
    def _build_settings(self, parent) -> None:
        """Tum System N ayarlari — trade modu, indikatorler, pozisyon, SL, filtreler."""
        cfg = self.controller.config
        scroll = ctk.CTkScrollableFrame(parent)
        scroll.pack(fill="both", expand=True, padx=8, pady=4)

        lbl_font = ctk.CTkFont(size=13)
        hdr_font = ctk.CTkFont(size=14, weight="bold")
        hint_font = ctk.CTkFont(size=11)
        dc = "#CFD8DC"
        hint_c = "#78909C"

        def _row(parent_frame, label, var_widget_fn, hint=""):
            """Yardimci: label + widget + hint satiri."""
            r = ctk.CTkFrame(parent_frame)
            r.pack(fill="x", pady=2)
            ctk.CTkLabel(r, text=label, font=lbl_font,
                         text_color=dc, width=180).pack(side="left")
            var_widget_fn(r)
            if hint:
                ctk.CTkLabel(r, text=f"  {hint}", font=hint_font,
                             text_color=hint_c).pack(side="left", padx=8)
            return r

        # ═══════════════════════════════════════════════
        #  SISTEM AKTIF / PASIF
        # ═══════════════════════════════════════════════
        ctk.CTkLabel(scroll, text="SISTEM",
                     font=hdr_font, text_color="#FFFFFF").pack(anchor="w", pady=(8, 4))

        self._enabled_var = ctk.BooleanVar(
            value=cfg.get("system_n.enabled", False))
        _row(scroll, "System N Aktif:",
             lambda r: ctk.CTkSwitch(
                 r, variable=self._enabled_var, text="",
                 command=self._save_settings,
                 progress_color="#00E676",
             ).pack(side="left"),
             "(aktifken diger sistemler devre disi kalir)")

        # ═══════════════════════════════════════════════
        #  TRADE MODU
        # ═══════════════════════════════════════════════
        ctk.CTkLabel(scroll, text="TRADE MODU",
                     font=hdr_font, text_color="#FFFFFF").pack(anchor="w", pady=(8, 4))

        # Trading mode
        self._trading_mode_var = ctk.StringVar(
            value=cfg.get("system_n.trading_mode", "spot"))
        _row(scroll, "Trade Modu:",
             lambda r: ctk.CTkOptionMenu(
                 r, variable=self._trading_mode_var,
                 values=["spot", "futures"],
                 width=120, command=lambda _: self._save_settings(),
             ).pack(side="left"),
             "(spot: sadece long, futures: kaldiracli)")

        # Short enabled
        self._short_enabled_var = ctk.BooleanVar(
            value=cfg.get("system_n.short_enabled", False))
        _row(scroll, "Short Aktif:",
             lambda r: ctk.CTkSwitch(
                 r, variable=self._short_enabled_var, text="",
                 command=self._save_settings,
             ).pack(side="left"))

        # Reverse enabled
        self._reverse_enabled_var = ctk.BooleanVar(
            value=cfg.get("system_n.reverse_enabled", False))
        _row(scroll, "Reverse Aktif:",
             lambda r: ctk.CTkSwitch(
                 r, variable=self._reverse_enabled_var, text="",
                 command=self._save_settings,
             ).pack(side="left"),
             "(sinyal gelince pozisyonu cevir)")

        # Reverse sizing mode
        self._reverse_sizing_var = ctk.StringVar(
            value=cfg.get("system_n.reverse_sizing", "fresh"))
        _row(scroll, "Reverse Boyut:",
             lambda r: ctk.CTkOptionMenu(
                 r, variable=self._reverse_sizing_var,
                 values=["fresh", "full"],
                 width=120, command=lambda _: self._save_settings(),
             ).pack(side="left"),
             "(fresh: yeni hesap, full: eski miktarla)")

        # Max leverage
        self._max_lev_var = ctk.StringVar(
            value=str(cfg.get("system_n.max_leverage", 20)))
        _row(scroll, "Max Kaldirac:",
             lambda r: ctk.CTkEntry(r, textvariable=self._max_lev_var,
                                     width=80).pack(side="left"),
             "(G-bazli hesaplanan kaldiracin ust siniri)")

        # Scan interval
        self._scan_interval_var = ctk.StringVar(
            value=str(cfg.get("system_n.scan_interval_seconds", 300)))
        _row(scroll, "Tarama Araligi (sn):",
             lambda r: ctk.CTkEntry(r, textvariable=self._scan_interval_var,
                                     width=80).pack(side="left"))

        # Kline limit
        self._kline_limit_var = ctk.StringVar(
            value=str(cfg.get("system_n.kline_limit", 300)))
        _row(scroll, "Mum Sayisi:",
             lambda r: ctk.CTkEntry(r, textvariable=self._kline_limit_var,
                                     width=80).pack(side="left"))

        # Default TF
        self._default_tf_var = ctk.StringVar(
            value=cfg.get("system_n.timeframe", "5m"))
        _row(scroll, "Varsayilan TF:",
             lambda r: ctk.CTkOptionMenu(
                 r, variable=self._default_tf_var,
                 values=["1m", "3m", "5m", "15m", "30m", "1h"],
                 width=80, command=lambda _: self._save_settings(),
             ).pack(side="left"),
             "(optimize cache yoksa kullanilir)")

        # Coin mode
        self._coin_mode_var = ctk.StringVar(
            value=cfg.get("system_n.coin_mode", "top_n"))
        _row(scroll, "Coin Modu:",
             lambda r: ctk.CTkOptionMenu(
                 r, variable=self._coin_mode_var,
                 values=["top_n", "manual"],
                 width=120, command=lambda _: self._save_settings(),
             ).pack(side="left"),
             "(top_n: hacim sirali, manual: coin listesi)")

        # Coin sayisi
        self._coin_sayisi_var = ctk.StringVar(
            value=str(cfg.get("system_n.coin_sayisi", 50)))
        _row(scroll, "Coin Sayisi (top_n):",
             lambda r: ctk.CTkEntry(r, textvariable=self._coin_sayisi_var,
                                     width=80).pack(side="left"))

        # ═══════════════════════════════════════════════
        #  INDIKATOR AYARLARI
        # ═══════════════════════════════════════════════
        ctk.CTkLabel(scroll, text="INDIKATOR AYARLARI",
                     font=hdr_font, text_color="#FFFFFF").pack(anchor="w", pady=(16, 4))

        # Coeff
        self._coeff_var = ctk.StringVar(
            value=str(cfg.get("system_n.indicators.coeff", 3.6)))
        _row(scroll, "AlphaTrend Coeff:",
             lambda r: ctk.CTkEntry(r, textvariable=self._coeff_var,
                                     width=80).pack(side="left"),
             "(varsayilan — optimize cache oncelikli)")

        # Period
        self._period_var = ctk.StringVar(
            value=str(cfg.get("system_n.indicators.period", 27)))
        _row(scroll, "AlphaTrend Period:",
             lambda r: ctk.CTkEntry(r, textvariable=self._period_var,
                                     width=80).pack(side="left"),
             "(varsayilan — optimize cache oncelikli)")

        # RSI Length
        self._rsi_length_var = ctk.StringVar(
            value=str(cfg.get("system_n.indicators.rsi_length", 14)))
        _row(scroll, "RSI Uzunlugu:",
             lambda r: ctk.CTkEntry(r, textvariable=self._rsi_length_var,
                                     width=80).pack(side="left"),
             "(standart: 14)")

        # ADX Length
        self._adx_length_var = ctk.StringVar(
            value=str(cfg.get("system_n.indicators.adx_length", 14)))
        _row(scroll, "ADX Uzunlugu:",
             lambda r: ctk.CTkEntry(r, textvariable=self._adx_length_var,
                                     width=80).pack(side="left"))

        # ADX Threshold
        self._adx_threshold_var = ctk.StringVar(
            value=str(cfg.get("system_n.indicators.adx_threshold", 18.0)))
        _row(scroll, "ADX Esik (statik):",
             lambda r: ctk.CTkEntry(r, textvariable=self._adx_threshold_var,
                                     width=80).pack(side="left"))

        # Use MFI
        self._use_mfi_var = ctk.BooleanVar(
            value=cfg.get("system_n.indicators.use_mfi", True))
        _row(scroll, "MFI Kullan:",
             lambda r: ctk.CTkSwitch(
                 r, variable=self._use_mfi_var, text="",
                 command=self._save_settings,
             ).pack(side="left"),
             "(kapali ise RSI kullanilir)")

        # ADX Static filter
        self._use_adx_static_var = ctk.BooleanVar(
            value=cfg.get("system_n.indicators.use_adx_static", True))
        _row(scroll, "ADX Statik Filtre:",
             lambda r: ctk.CTkSwitch(
                 r, variable=self._use_adx_static_var, text="",
                 command=self._save_settings,
             ).pack(side="left"))

        # ADX Dynamic filter
        self._use_adx_dynamic_var = ctk.BooleanVar(
            value=cfg.get("system_n.indicators.use_adx_dynamic", True))
        _row(scroll, "ADX Dinamik Filtre:",
             lambda r: ctk.CTkSwitch(
                 r, variable=self._use_adx_dynamic_var, text="",
                 command=self._save_settings,
             ).pack(side="left"))

        # ADX Dynamic mult
        self._adx_dyn_mult_var = ctk.StringVar(
            value=str(cfg.get("system_n.indicators.adx_dyn_mult", 1.0)))
        _row(scroll, "ADX Dyn Carpan:",
             lambda r: ctk.CTkEntry(r, textvariable=self._adx_dyn_mult_var,
                                     width=80).pack(side="left"))

        # Slope filter
        self._use_slope_var = ctk.BooleanVar(
            value=cfg.get("system_n.indicators.use_slope", False))
        _row(scroll, "Slope Filtre:",
             lambda r: ctk.CTkSwitch(
                 r, variable=self._use_slope_var, text="",
                 command=self._save_settings,
             ).pack(side="left"))

        # Slope factor
        self._slope_factor_var = ctk.StringVar(
            value=str(cfg.get("system_n.indicators.slope_factor", 0.1)))
        _row(scroll, "Slope Faktor:",
             lambda r: ctk.CTkEntry(r, textvariable=self._slope_factor_var,
                                     width=80).pack(side="left"))

        # ═══════════════════════════════════════════════
        #  POZISYON LIMITLERI
        # ═══════════════════════════════════════════════
        ctk.CTkLabel(scroll, text="POZISYON LIMITLERI",
                     font=hdr_font, text_color="#FFFFFF").pack(anchor="w", pady=(16, 4))

        # Sizing mode
        self._sizing_mode_var = ctk.StringVar(
            value=cfg.get("system_n.position.sizing_mode", "hybrid"))
        _row(scroll, "Pozisyon Modu:",
             lambda r: ctk.CTkOptionMenu(
                 r, variable=self._sizing_mode_var,
                 values=["hybrid", "divider", "min_notional"],
                 width=140, command=lambda _: self._save_settings(),
             ).pack(side="left"),
             "(hybrid: esik altinda min, ustunde 1/N)")

        # Hybrid threshold (hibrit mod icin esik deger)
        self._hybrid_threshold_var = ctk.StringVar(
            value=str(cfg.get("system_n.position.hybrid_threshold_usd", 12.0)))
        _row(scroll, "Hibrit Esik ($):",
             lambda r: ctk.CTkEntry(r, textvariable=self._hybrid_threshold_var,
                                     width=80).pack(side="left"),
             "(bakiye < esik: minimum, >= esik: 1/N)")

        # Portfolio divider
        self._divider_var = ctk.StringVar(
            value=str(cfg.get("system_n.position.portfolio_divider", 12)))
        _row(scroll, "Portfolio Bolenler:",
             lambda r: ctk.CTkEntry(r, textvariable=self._divider_var,
                                     width=80).pack(side="left"),
             "(bakiye / N = pozisyon buyuklugu)")

        # Min notional USD (min_notional modu icin)
        self._min_notional_usd_var = ctk.StringVar(
            value=str(cfg.get("system_n.position.min_notional_usd", 5.0)))
        _row(scroll, "Min Notional USD:",
             lambda r: ctk.CTkEntry(r, textvariable=self._min_notional_usd_var,
                                     width=80).pack(side="left"),
             "(Binance min emir tutari, orn: 5)")

        # Max positions
        self._max_pos_var = ctk.StringVar(
            value=str(cfg.get("system_n.position.max_positions", 12)))
        _row(scroll, "Max Pozisyon:",
             lambda r: ctk.CTkEntry(r, textvariable=self._max_pos_var,
                                     width=80).pack(side="left"))

        # Min position USD
        self._min_pos_var = ctk.StringVar(
            value=str(cfg.get("system_n.position.min_position_usd", 1.0)))
        _row(scroll, "Min Pozisyon ($):",
             lambda r: ctk.CTkEntry(r, textvariable=self._min_pos_var,
                                     width=80).pack(side="left"))

        # Min notional buffer %
        self._min_notional_buffer_var = ctk.StringVar(
            value=str(cfg.get("system_n.position.min_notional_buffer_pct", 20)))
        _row(scroll, "Min Notional Buffer %:",
             lambda r: ctk.CTkEntry(r, textvariable=self._min_notional_buffer_var,
                                     width=80).pack(side="left"),
             "(Binance 5$ min + buffer%, orn: 20 = 6$)")

        # Max same direction
        self._max_same_dir_var = ctk.StringVar(
            value=str(cfg.get("system_n.position.max_same_direction", 8)))
        _row(scroll, "Max Ayni Yon:",
             lambda r: ctk.CTkEntry(r, textvariable=self._max_same_dir_var,
                                     width=80).pack(side="left"))

        # Direction balance
        self._dir_balance_var = ctk.BooleanVar(
            value=cfg.get("system_n.position.direction_balance_enabled", False))
        self._dir_ratio_var = ctk.StringVar(
            value=cfg.get("system_n.position.direction_balance_ratio", "2-1"))
        def _dir_balance_row(r):
            ctk.CTkSwitch(
                r, variable=self._dir_balance_var, text="",
                command=self._save_settings,
            ).pack(side="left")
            ctk.CTkOptionMenu(
                r, variable=self._dir_ratio_var,
                values=["1-1", "2-1", "3-1", "4-1"],
                width=80, command=lambda _: self._save_settings(),
            ).pack(side="left", padx=8)
        _row(scroll, "Yon Dengesi:", _dir_balance_row)

        # ═══════════════════════════════════════════════
        #  FILTRELER
        # ═══════════════════════════════════════════════
        ctk.CTkLabel(scroll, text="FILTRELER",
                     font=hdr_font, text_color="#FFFFFF").pack(anchor="w", pady=(16, 4))

        # Min volume 24h
        self._min_vol_var = ctk.StringVar(
            value=str(int(cfg.get("system_n.filters.min_volume_24h_usdt", 5_000_000))))
        _row(scroll, "Min 24h Hacim ($):",
             lambda r: ctk.CTkEntry(r, textvariable=self._min_vol_var,
                                     width=120).pack(side="left"))

        # FR max
        self._fr_max_var = ctk.StringVar(
            value=str(cfg.get("system_n.filters.funding_rate_max", 0.001)))
        _row(scroll, "Max Funding Rate:",
             lambda r: ctk.CTkEntry(r, textvariable=self._fr_max_var,
                                     width=80).pack(side="left"),
             "(0.001 = %0.1)")

        # ═══════════════════════════════════════════════
        #  STOP LOSS (OPSIYONEL)
        # ═══════════════════════════════════════════════
        ctk.CTkLabel(scroll, text="STOP LOSS (OPSIYONEL)",
                     font=hdr_font, text_color="#FFFFFF").pack(anchor="w", pady=(16, 4))

        # SL Enabled
        self._sl_enabled_var = ctk.BooleanVar(value=cfg.get("system_n.sl.enabled", False))
        _row(scroll, "SL Aktif:",
             lambda r: ctk.CTkSwitch(
                 r, variable=self._sl_enabled_var, text="",
                 command=self._save_settings,
             ).pack(side="left"),
             "(kapali: sadece sinyal bazli cikis)")

        # SL Mode
        self._sl_mode_var = ctk.StringVar(
            value=cfg.get("system_n.sl.mode", "g_based"))
        _row(scroll, "SL Modu:",
             lambda r: ctk.CTkOptionMenu(
                 r, variable=self._sl_mode_var,
                 values=["g_based", "atr_based", "fixed_pct"],
                 width=150, command=lambda _: self._save_settings(),
             ).pack(side="left"),
             "(g_based: G dalga, atr_based: ATR, fixed_pct: sabit %)")

        # SL G Mult
        self._sl_g_mult_var = ctk.StringVar(
            value=str(cfg.get("system_n.sl.g_mult", 1.5)))
        _row(scroll, "G Carpani (g_based):",
             lambda r: ctk.CTkEntry(r, textvariable=self._sl_g_mult_var,
                                     width=80).pack(side="left"))

        # SL ATR Mult
        self._sl_atr_mult_var = ctk.StringVar(
            value=str(cfg.get("system_n.sl.atr_mult", 2.0)))
        _row(scroll, "ATR Carpani (atr_based):",
             lambda r: ctk.CTkEntry(r, textvariable=self._sl_atr_mult_var,
                                     width=80).pack(side="left"))

        # SL Fixed Pct
        self._sl_fixed_pct_var = ctk.StringVar(
            value=str(cfg.get("system_n.sl.fixed_pct", 5.0)))
        _row(scroll, "Sabit SL % (fixed_pct):",
             lambda r: ctk.CTkEntry(r, textvariable=self._sl_fixed_pct_var,
                                     width=80).pack(side="left"))

        # Fee total pct
        self._sl_fee_var = ctk.StringVar(
            value=str(cfg.get("system_n.sl.fee_total_pct", 0.12)))
        _row(scroll, "Fee+Slippage %:",
             lambda r: ctk.CTkEntry(r, textvariable=self._sl_fee_var,
                                     width=80).pack(side="left"),
             "(SL hesabina eklenen toplam maliyet)")

        # Server-side SL
        self._sl_server_var = ctk.BooleanVar(
            value=cfg.get("system_n.sl.server_side", True))
        _row(scroll, "Server-Side SL:",
             lambda r: ctk.CTkSwitch(
                 r, variable=self._sl_server_var, text="",
                 command=self._save_settings,
             ).pack(side="left"),
             "(Binance STOP_MARKET emri)")

        # ═══ KAYDET BUTONU ═══
        ctk.CTkButton(
            scroll, text="KAYDET", width=140, height=36,
            font=ctk.CTkFont(size=14, weight="bold"),
            fg_color="#3d5afe", hover_color="#5c6bc0",
            command=self._save_settings,
        ).pack(pady=(20, 8))

        self._save_status = ctk.CTkLabel(
            scroll, text="", font=ctk.CTkFont(size=12), text_color="#00E676")
        self._save_status.pack()

    def _save_settings(self) -> None:
        """Tum ayarlari config'e kaydet."""
        cfg = self.controller.config
        try:
            # Sistem aktif/pasif
            cfg.set("system_n.enabled", self._enabled_var.get())

            # Trade modu
            cfg.set("system_n.trading_mode", self._trading_mode_var.get())
            cfg.set("system_n.short_enabled", self._short_enabled_var.get())
            cfg.set("system_n.reverse_enabled", self._reverse_enabled_var.get())
            cfg.set("system_n.reverse_sizing", self._reverse_sizing_var.get())
            cfg.set("system_n.coin_mode", self._coin_mode_var.get())
            cfg.set("system_n.timeframe", self._default_tf_var.get())

            # Use MFI & filters
            cfg.set("system_n.indicators.use_mfi", self._use_mfi_var.get())
            cfg.set("system_n.indicators.use_adx_static", self._use_adx_static_var.get())
            cfg.set("system_n.indicators.use_adx_dynamic", self._use_adx_dynamic_var.get())
            cfg.set("system_n.indicators.use_slope", self._use_slope_var.get())

            # SL switches
            cfg.set("system_n.sl.enabled", self._sl_enabled_var.get())
            cfg.set("system_n.sl.mode", self._sl_mode_var.get())
            cfg.set("system_n.sl.server_side", self._sl_server_var.get())

            # Sizing mode
            cfg.set("system_n.position.sizing_mode", self._sizing_mode_var.get())

            # Direction balance
            cfg.set("system_n.position.direction_balance_enabled",
                    self._dir_balance_var.get())
            cfg.set("system_n.position.direction_balance_ratio",
                    self._dir_ratio_var.get())

            # Numeric fields (safe parse)
            _nums = [
                ("system_n.max_leverage", self._max_lev_var, int),
                ("system_n.scan_interval_seconds", self._scan_interval_var, int),
                ("system_n.kline_limit", self._kline_limit_var, int),
                ("system_n.coin_sayisi", self._coin_sayisi_var, int),
                ("system_n.indicators.coeff", self._coeff_var, float),
                ("system_n.indicators.period", self._period_var, int),
                ("system_n.indicators.rsi_length", self._rsi_length_var, int),
                ("system_n.indicators.adx_length", self._adx_length_var, int),
                ("system_n.indicators.adx_threshold", self._adx_threshold_var, float),
                ("system_n.indicators.adx_dyn_mult", self._adx_dyn_mult_var, float),
                ("system_n.indicators.slope_factor", self._slope_factor_var, float),
                ("system_n.position.portfolio_divider", self._divider_var, int),
                ("system_n.position.hybrid_threshold_usd", self._hybrid_threshold_var, float),
                ("system_n.position.min_notional_usd", self._min_notional_usd_var, float),
                ("system_n.position.max_positions", self._max_pos_var, int),
                ("system_n.position.min_position_usd", self._min_pos_var, float),
                ("system_n.position.min_notional_buffer_pct", self._min_notional_buffer_var, int),
                ("system_n.position.max_same_direction", self._max_same_dir_var, int),
                ("system_n.filters.min_volume_24h_usdt", self._min_vol_var, int),
                ("system_n.filters.funding_rate_max", self._fr_max_var, float),
                ("system_n.sl.g_mult", self._sl_g_mult_var, float),
                ("system_n.sl.atr_mult", self._sl_atr_mult_var, float),
                ("system_n.sl.fixed_pct", self._sl_fixed_pct_var, float),
                ("system_n.sl.fee_total_pct", self._sl_fee_var, float),
            ]
            for key, var, typ in _nums:
                try:
                    cfg.set(key, typ(var.get()))
                except (ValueError, TypeError):
                    pass

            cfg.save()
            self._save_status.configure(text="Kaydedildi!", text_color="#00E676")
            logger.info("[SysN Panel] Settings saved")
        except Exception as e:
            self._save_status.configure(text=f"Hata: {e}", text_color="#FF5252")
            logger.error(f"[SysN Panel] Settings save failed: {e}")

    # ───────────────────────────────────────────────
    #  REFRESH
    # ───────────────────────────────────────────────
    def _start_refresh(self) -> None:
        self._refresh_loop()

    def _refresh_loop(self) -> None:
        try:
            self._update_all()
        except Exception as e:
            logger.error(f"[SysN Panel] refresh error: {e}")
        self.after(2000, self._refresh_loop)

    def _update_all(self) -> None:
        results = self.controller.get_system_n_results() or []
        positions = self.controller.get_all_scanner_positions() or []
        decisions = self.controller.get_system_n_decisions() or []

        # Mode
        cfg = self.controller.config
        short_en = cfg.get("system_n.short_enabled", False)
        reverse_en = cfg.get("system_n.reverse_enabled", False)
        if not short_en:
            mode_text = "Mod: SPOT (Sadece Long)"
        elif reverse_en:
            mode_text = "Mod: SHORT + REVERSE"
        else:
            mode_text = "Mod: SHORT (Reverse kapali)"
        self._mode_label.configure(text=mode_text)

        buy_c = sum(1 for r in results if _g(r, "signal") == "BUY")
        sell_c = sum(1 for r in results if _g(r, "signal") == "SELL")
        m_pos = [p for p in positions if _g(p, "entry_mode") == "SYSTEM_N"]
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
        W = SN_SCAN_WIDTHS
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
        W = SN_POS_WIDTHS
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
        W = SN_DEC_WIDTHS
        return [
            (time_str, "#90A4AE", W[0]), (symbol, "#FFFFFF", W[1]),
            (signal, sig_color, W[2]), (action, action_color, W[3]),
            (price_str, dc, W[4]), (trend_str, trend_color, W[5]),
            (adx_str, adx_color, W[6]), (rsi_str, dc, W[7]),
            (desc_str, "#B0BEC5", W[8]),
        ]
