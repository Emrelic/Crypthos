"""Strategy Settings Panel - comprehensive strategy configuration with presets."""
import json
import os
import customtkinter as ctk
from tkinter import messagebox, simpledialog

# ── Standard Presets ──
PRESETS = {
    "konservatif": {
        "name": "Konservatif",
        "desc": "Dusuk kaldirac, siki SL/TP, guvenli islem",
        "color": "#4CAF50",
        "values": {
            # Entry
            "min_buy_score": 65, "min_confluence": 5.0, "min_adx": 22,
            "max_rsi_long": 58, "min_rsi_short": 42,
            "macd_filter": True, "volume_filter": True, "volatile_filter": True,
            "scan_interval_seconds": 30, "kline_interval": "1m", "kline_limit": 200,
            "min_timeframe": "5m",
            # Leverage
            "min_leverage": 10, "max_leverage": 25,
            "max_positions": 2, "portfolio_percent": 30,
            # SL
            "sl_enabled": True, "liq_factor": 70, "sl_liq_percent": 40,
            "server_sl_atr_mult": 2.0,
            "emergency_enabled": True, "emergency_liq_percent": 70,
            # Trailing
            "trailing_enabled": True,
            "trailing_activate_fee_mult": 2.0, "trailing_distance_fee_mult": 1.5,
            # TP
            "tp_enabled": True, "tp_liq_multiplier": 2.0, "tp_exit_mode": "immediate",
            # Signal
            "signal_exit_enabled": True, "signal_exit_threshold": 3.0,
            "signal_min_hold_seconds": 60, "signal_only_in_profit": True,
            "signal_deep_exit_threshold": 8.0,
            "divergence_exit_enabled": True,
            # Time
            "time_limit_enabled": True, "time_limit_minutes": 60,
            "time_limit_extend_trailing": True, "time_limit_extend_breakeven": True,
            # Risk
            "cooldown_seconds": 180,
            "loss_cooldown_seconds": 600,
            # Scanner
            "max_symbols_to_scan": 50,
            "battle_mode": False, "close_only": False, "focus_mode": False,
            # Limit emir
            "limit_entry_enabled": False, "limit_atr_offset": 0.5,
            "limit_timeout_seconds": 300, "limit_recheck_signal": True,
            # Yon dengesi / coin ban / limit cikis: konservatif kapali
            "direction_balance_enabled": False, "direction_balance_ratio": "2-1",
            "coin_daily_loss_limit": 0, "coin_daily_ban_hours": 24,
            "limit_exit_enabled": False, "limit_exit_atr_offset": 0.2,
            # Market fallback / partial TP / BTC korelasyon
            "market_fallback_on_limit_timeout": False,
            "partial_tp_enabled": False, "partial_tp_atr_mult": 3.0, "partial_tp_close_pct": 50,
            "btc_correlation_enabled": False, "btc_max_portfolio_beta": 2.5,
        },
    },
    "dengeli": {
        "name": "Dengeli",
        "desc": "Orta kaldirac, dengeli risk/odul orani",
        "color": "#2196F3",
        "values": {
            "min_buy_score": 55, "min_confluence": 4.0, "min_adx": 20,
            "max_rsi_long": 60, "min_rsi_short": 40,
            "macd_filter": True, "volume_filter": True, "volatile_filter": True,
            "scan_interval_seconds": 30, "kline_interval": "1m", "kline_limit": 200,
            "min_timeframe": "5m",
            "min_leverage": 25, "max_leverage": 50,
            "max_positions": 4, "portfolio_percent": 25,
            "sl_enabled": True, "liq_factor": 70, "sl_liq_percent": 50,
            "server_sl_atr_mult": 2.0,
            "emergency_enabled": True, "emergency_liq_percent": 80,
            "trailing_enabled": True,
            "trailing_activate_fee_mult": 3.0, "trailing_distance_fee_mult": 2.0,
            "tp_enabled": True, "tp_liq_multiplier": 3.0, "tp_exit_mode": "immediate",
            "signal_exit_enabled": True, "signal_exit_threshold": 4.0,
            "signal_min_hold_seconds": 30, "signal_only_in_profit": True,
            "signal_deep_exit_threshold": 8.0,
            "divergence_exit_enabled": True,
            "time_limit_enabled": True, "time_limit_minutes": 120,
            "time_limit_extend_trailing": True, "time_limit_extend_breakeven": True,
            "cooldown_seconds": 120,
            "loss_cooldown_seconds": 600,
            "max_symbols_to_scan": 50,
            "battle_mode": False, "close_only": False, "focus_mode": False,
            "limit_entry_enabled": False, "limit_atr_offset": 0.5,
            "limit_timeout_seconds": 300, "limit_recheck_signal": True,
            # Yon dengesi / coin ban / limit cikis: dengeli kapali
            "direction_balance_enabled": False, "direction_balance_ratio": "2-1",
            "coin_daily_loss_limit": 0, "coin_daily_ban_hours": 24,
            "limit_exit_enabled": False, "limit_exit_atr_offset": 0.2,
            # Market fallback / partial TP / BTC korelasyon
            "market_fallback_on_limit_timeout": False,
            "partial_tp_enabled": False, "partial_tp_atr_mult": 3.0, "partial_tp_close_pct": 50,
            "btc_correlation_enabled": False, "btc_max_portfolio_beta": 2.5,
        },
    },
    "agresif": {
        "name": "Agresif",
        "desc": "Yuksek kaldirac, genis trailing, TP yok, kari kosturur",
        "color": "#FF5722",
        "values": {
            "min_buy_score": 55, "min_confluence": 4.0, "min_adx": 18,
            "max_rsi_long": 62, "min_rsi_short": 38,
            "macd_filter": True, "volume_filter": True, "volatile_filter": True,
            "scan_interval_seconds": 30, "kline_interval": "1m", "kline_limit": 200,
            "min_timeframe": "3m",
            "min_leverage": 50, "max_leverage": 100,
            "max_positions": 6, "portfolio_percent": 25, "portfolio_divider": 0,
            "sl_enabled": True, "liq_factor": 70, "sl_liq_percent": 50,
            "server_sl_atr_mult": 2.0,
            "emergency_enabled": True, "emergency_liq_percent": 80,
            "trailing_enabled": True, "trailing_mode": "roi",
            "trailing_activate_roi": 60, "trailing_distance_roi": 10,
            "trailing_atr_activate_mult": 4.0, "trailing_atr_distance_mult": 1.0,
            "trailing_activate_fee_mult": 2.0, "trailing_distance_fee_mult": 4.0,
            "tp_enabled": False, "tp_liq_multiplier": 3.0, "tp_exit_mode": "signal",
            "signal_exit_enabled": True, "signal_exit_threshold": 5.0,
            "signal_min_hold_seconds": 30, "signal_only_in_profit": True,
            "signal_deep_exit_threshold": 10.0,
            "divergence_exit_enabled": False,
            "time_limit_enabled": True, "time_limit_minutes": 480,
            "time_limit_extend_trailing": True, "time_limit_extend_breakeven": True,
            "cooldown_seconds": 60,
            "loss_cooldown_seconds": 300,
            "max_symbols_to_scan": 50,
            "battle_mode": False, "close_only": False, "focus_mode": False,
            "limit_entry_enabled": False, "limit_atr_offset": 0.5,
            "limit_timeout_seconds": 300, "limit_recheck_signal": True,
            # Yon dengesi / coin ban / limit cikis: agresif kapali
            "direction_balance_enabled": False, "direction_balance_ratio": "3-1",
            "coin_daily_loss_limit": 0, "coin_daily_ban_hours": 24,
            "limit_exit_enabled": False, "limit_exit_atr_offset": 0.2,
            # Market fallback / partial TP / BTC korelasyon
            "market_fallback_on_limit_timeout": False,
            "partial_tp_enabled": False, "partial_tp_atr_mult": 3.0, "partial_tp_close_pct": 50,
            "btc_correlation_enabled": False, "btc_max_portfolio_beta": 2.5,
        },
    },
    "emre_ortalama": {
        "name": "Emre Ortalama",
        "desc": "Max kaldirac, 1/12 portfoy, ATR trailing (7x/1x), sinyal her zaman cikis",
        "color": "#9C27B0",
        "values": {
            # Entry: agresif giris, guclu sinyal gerektir
            "min_buy_score": 55, "min_confluence": 4.0, "min_adx": 18,
            "max_rsi_long": 62, "min_rsi_short": 38,
            "macd_filter": True, "volume_filter": True, "volatile_filter": False,
            "scan_interval_seconds": 30, "kline_interval": "5m", "kline_limit": 200,
            "min_timeframe": "5m",
            # Kaldirac: max mumkun (20x bile olsa ac)
            "min_leverage": 1, "max_leverage": 20,
            # Pozisyon: 4 cephede, 1/12 portfoy
            "max_positions": 4, "portfolio_percent": 8, "portfolio_divider": 12,
            # SL: pratik liq %70, SL %50 (= %0.35 at 100x)
            "sl_enabled": True, "liq_factor": 70, "sl_liq_percent": 50,
            "server_sl_atr_mult": 2.0,
            "emergency_enabled": True, "emergency_liq_percent": 80,
            # Trailing: ATR bazli (4x tetik, 1x geri cekilme)
            "trailing_enabled": True, "server_trailing_dynamic_update": False, "trailing_mode": "atr",
            "trailing_atr_activate_mult": 4.0, "trailing_atr_distance_mult": 1.0,
            "trailing_activate_roi": 0, "trailing_distance_roi": 0,
            "trailing_activate_fee_mult": 3.0, "trailing_distance_fee_mult": 2.0,
            # TP: kapali, trailing ve sinyal yonetir
            "tp_enabled": False, "tp_liq_multiplier": 3.0, "tp_exit_mode": "signal",
            # Sinyal: HER ZAMAN cikis (zararda bile), override trailing
            "signal_exit_enabled": True, "signal_exit_threshold": 4.0,
            "signal_min_hold_seconds": 60, "signal_only_in_profit": False,
            "signal_deep_exit_threshold": 8.0,
            "divergence_exit_enabled": False,
            # Zaman: 8 saat, trailing aktifse uzat
            "time_limit_enabled": True, "time_limit_minutes": 480,
            "time_limit_extend_trailing": True, "time_limit_extend_breakeven": True,
            "cooldown_seconds": 60,
            "loss_cooldown_seconds": 600,
            # Scanner
            "max_symbols_to_scan": 50,
            "battle_mode": False, "close_only": False, "focus_mode": False,
            # Limit emir: emre ortalama'da varsayilan acik (0.5 ATR pazarlik)
            "limit_entry_enabled": True, "limit_atr_offset": 0.5,
            "limit_timeout_seconds": 300, "limit_recheck_signal": True,
            # Yon dengesi: 2-1 (max 2 ayni yon, sonra ters mecburi)
            "direction_balance_enabled": True, "direction_balance_ratio": "2-1",
            # Coin gunluk yasak: 3 zarar → 24 saat ban
            "coin_daily_loss_limit": 3, "coin_daily_ban_hours": 24,
            # Limit cikis: maker fee ile kapama
            "limit_exit_enabled": True, "limit_exit_atr_offset": 0.2,
            # Market fallback / partial TP / BTC korelasyon
            "market_fallback_on_limit_timeout": True,
            "partial_tp_enabled": False, "partial_tp_atr_mult": 3.0, "partial_tp_close_pct": 50,
            "btc_correlation_enabled": False, "btc_max_portfolio_beta": 2.5,
        },
    },
}


TEMPLATES_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "data", "strategy_templates.json")


def _load_templates() -> dict:
    """Load user templates from JSON file."""
    if os.path.exists(TEMPLATES_FILE):
        try:
            with open(TEMPLATES_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def _save_templates(templates: dict) -> None:
    """Save user templates to JSON file."""
    os.makedirs(os.path.dirname(TEMPLATES_FILE), exist_ok=True)
    with open(TEMPLATES_FILE, "w", encoding="utf-8") as f:
        json.dump(templates, f, indent=2, ensure_ascii=False)


class StrategySettingsPanel(ctk.CTkFrame):
    """Comprehensive strategy settings with Standard presets and Manual mode."""

    def __init__(self, parent, controller):
        super().__init__(parent)
        self.controller = controller
        self.pack(fill="both", expand=True)
        self._entries = {}      # key -> CTkEntry
        self._cb_vars = {}      # key -> BooleanVar
        self._all_widgets = []  # (widget, type) for enable/disable
        self._build_ui()
        self._load_from_config()

    # ════════════════════════════════════════
    # BUILD UI
    # ════════════════════════════════════════

    def _build_ui(self) -> None:
        # ── Top: Mode selector ──
        top = ctk.CTkFrame(self, fg_color="#1a1a2e")
        top.pack(fill="x", padx=10, pady=(10, 5))

        ctk.CTkLabel(top, text="Strateji Ayarlari",
                     font=ctk.CTkFont(size=16, weight="bold")).pack(side="left", padx=10, pady=10)

        self._mode_var = ctk.StringVar(
            value=self.controller.config.get("strategy.mode", "standard"))
        self._mode_seg = ctk.CTkSegmentedButton(
            top, values=["standard", "manuel"],
            variable=self._mode_var,
            command=self._on_mode_change,
            font=ctk.CTkFont(weight="bold"),
        )
        self._mode_seg.pack(side="right", padx=10, pady=10)

        # ── Preset buttons (visible in standard mode) ──
        self._preset_frame = ctk.CTkFrame(self)
        self._preset_frame.pack(fill="x", padx=10, pady=3)

        ctk.CTkLabel(self._preset_frame, text="Hazir Ayarlar:",
                     font=ctk.CTkFont(weight="bold")).pack(side="left", padx=10)

        current_preset = self.controller.config.get("strategy.preset", "")
        self._preset_var = ctk.StringVar(value=current_preset)

        for key, preset in PRESETS.items():
            btn = ctk.CTkButton(
                self._preset_frame, text=preset["name"], width=120, height=32,
                fg_color=preset["color"],
                hover_color=preset["color"],
                command=lambda k=key: self._apply_preset(k),
                font=ctk.CTkFont(weight="bold"),
            )
            btn.pack(side="left", padx=5, pady=5)

        self._preset_desc = ctk.CTkLabel(
            self._preset_frame, text="", text_color="gray60",
            font=ctk.CTkFont(size=11))
        self._preset_desc.pack(side="left", padx=15)

        # ── Feedback ──
        self._feedback = ctk.CTkLabel(self, text="", height=20,
                                      font=ctk.CTkFont(size=12, weight="bold"))
        self._feedback.pack(fill="x", padx=10)

        # ── Scrollable settings area ──
        self._scroll = ctk.CTkScrollableFrame(self)
        self._scroll.pack(fill="both", expand=True, padx=10, pady=5)
        s = self._scroll

        # ──────────────── GIRIS AYARLARI ────────────────
        self._section(s, "Giris Ayarlari (Entry)")
        self._field(s, "min_buy_score", "Min Alim Skoru", "55",
                    tip="Kompozit skor esigi (0-100). 55=dengeli, 65+=cok secici",
                    help_text=(
                        "KOMPOZIT SKOR (0-100)\n"
                        "─────────────────────\n"
                        "Her coin icin 5 kategoride skor hesaplanir:\n\n"
                        "  Confluence   %35  Indikator uyumu\n"
                        "  Rejim        %20  Piyasa durumu uyumu\n"
                        "  Hacim        %15  OBV, CMF, MFI\n"
                        "  Trend        %15  ADX, MACD, Supertrend\n"
                        "  Risk         %15  ATR uygunlugu, divergence\n\n"
                        "55 = orta kaliteli firsatlar dahil\n"
                        "65 = sadece guclu firsatlar\n"
                        "75+ = cok secici, az islem"))
        self._field(s, "min_confluence", "Min Confluence", "4.0",
                    tip="Kac indikator uyumlu olmali. 3.5=gevsek, 4.0=standart, 5.0+=siki",
                    help_text=(
                        "CONFLUENCE SKORU (indikator uyumu)\n"
                        "──────────────────────────────────\n"
                        "14 indikator kontrol edilir, her biri\n"
                        "AL veya SAT yonunde puan verir:\n\n"
                        "MOMENTUM:\n"
                        "  RSI          +-2.0  Asiri alim/satim\n"
                        "  StochRSI     +-1.5  Momentum kesisimi\n"
                        "  MFI          +-1.5  Hacim agirlikli RSI\n"
                        "  OBV Slope    +-1.0  Hacim birikimi\n\n"
                        "TREND:\n"
                        "  MACD         +-2.0  Hiz/yavas EMA kesisimi\n"
                        "  ADX/DI       +-1.5  Trend gucu ve yonu\n"
                        "  Supertrend   +-1.5  ATR bant yonu\n"
                        "  Parabolic SAR+-1.0  Trend takip noktasi\n"
                        "  Ichimoku     +-1.5  Bulut ustu/alti\n\n"
                        "VOLATILITE:\n"
                        "  Bollinger %B +-1.5  Bant pozisyonu\n"
                        "  CMF          +-1.0  Para akisi yonu\n\n"
                        "YAPI:\n"
                        "  Fiyat/SMA200 +-1.0  Uzun vade trend\n\n"
                        "Toplam max: +-18.5\n"
                        "Skor >= +4.0 → AL sinyali\n"
                        "Skor <= -4.0 → SAT sinyali\n\n"
                        "4.0 = en az 3 indikator uyumlu\n"
                        "5.0+ = 4+ indikator uyumlu"))
        self._field(s, "min_adx", "Min ADX", "18",
                    tip="Trend gucu esigi. <15=yatay, 18=min trend, 25+=guclu trend",
                    help_text=(
                        "ADX (Average Directional Index)\n"
                        "───────────────────────────────\n"
                        "Trendin GUCUNU olcer (yonu degil).\n"
                        "0-100 arasi deger.\n\n"
                        "  0-15:  Trend yok, yatay piyasa\n"
                        "         Whipsaw riski cok yuksek\n"
                        "  15-18: Cok zayif trend (belirsiz)\n"
                        "  18-25: Orta gucte trend\n"
                        "  25-35: Guclu trend (ideal)\n"
                        "  35-50: Cok guclu trend\n"
                        "  50+:   Asiri guclu (nadir)\n\n"
                        "Yuksek kaldiracta ADX < 18 tehlikeli:\n"
                        "Fiyat yatay gidip gelir, SL tetiklenir.\n"
                        "Tavsiye: 18 (min) - 25 (guvenli)"))

        row_rsi = ctk.CTkFrame(s, fg_color="transparent")
        row_rsi.pack(fill="x", padx=20, pady=2)
        ctk.CTkLabel(row_rsi, text="RSI Araligi:", width=180, anchor="w").pack(side="left")
        self._entries["max_rsi_long"] = e1 = ctk.CTkEntry(row_rsi, width=70)
        e1.pack(side="left", padx=2)
        self._all_widgets.append((e1, "entry"))
        ctk.CTkLabel(row_rsi, text="(Long max)").pack(side="left", padx=(0, 10))
        self._entries["min_rsi_short"] = e2 = ctk.CTkEntry(row_rsi, width=70)
        e2.pack(side="left", padx=2)
        self._all_widgets.append((e2, "entry"))
        ctk.CTkLabel(row_rsi, text="(Short min)  62/38=standart, 58/42=siki",
                     text_color="gray50", font=ctk.CTkFont(size=10)).pack(side="left")
        ctk.CTkButton(row_rsi, text="?", width=24, height=24,
                      fg_color="gray40", hover_color="gray50",
                      font=ctk.CTkFont(size=11, weight="bold"),
                      command=lambda: self._show_help("RSI Araligi", (
                          "RSI (Relative Strength Index)\n"
                          "─────────────────────────────\n"
                          "Fiyatin asiri alim/satim durumunu olcer.\n"
                          "0-100 arasi deger.\n\n"
                          "  0-30:  Asiri satim (oversold)\n"
                          "  30-40: Satim baskisi\n"
                          "  40-60: Notr bolge\n"
                          "  60-70: Alim baskisi\n"
                          "  70-100: Asiri alim (overbought)\n\n"
                          "LONG MAX (ornek 62):\n"
                          "  RSI bu degerin ustundeyse LONG acma.\n"
                          "  Zaten asiri alim bolgesi, yukari gitmez.\n\n"
                          "SHORT MIN (ornek 38):\n"
                          "  RSI bu degerin altindaysa SHORT acma.\n"
                          "  Zaten asiri satim, daha fazla dusmez.\n\n"
                          "Yuksek kaldirac icin: 62/38 (standart)\n"
                          "Daha siki filtre:     58/42\n"
                          "Gevsek filtre:        65/35"
                      ))).pack(side="left", padx=5)

        self._checkbox(s, "macd_filter", "MACD Histogram Filtresi (yon uyumu zorunlu)",
                      help_text=(
                          "MACD (Moving Average Convergence Divergence)\n"
                          "─────────────────────────────────────────────\n"
                          "Hizli EMA ile yavas EMA arasindaki farki olcer.\n\n"
                          "MACD Histogram > 0: Yukari momentum\n"
                          "MACD Histogram < 0: Asagi momentum\n\n"
                          "Bu filtre acikken:\n"
                          "  LONG icin: histogram > 0 olmali\n"
                          "  SHORT icin: histogram < 0 olmali\n\n"
                          "Yuksek kaldiracta onemli: MACD yon\n"
                          "uyumu olmadan giris yapilirsa trend\n"
                          "tersine gidebilir, SL hemen tetiklenir."))
        self._checkbox(s, "volume_filter", "Hacim Onay Filtresi (OBV/CMF)",
                      help_text=(
                          "HACIM ONAYI (OBV + CMF)\n"
                          "───────────────────────\n"
                          "Fiyat hareketi hacimle destekleniyor mu?\n\n"
                          "OBV (On-Balance Volume):\n"
                          "  Slope > 0: hacim birikiyor (alilar)\n"
                          "  Slope < 0: hacim dagiliyor (satislar)\n\n"
                          "CMF (Chaikin Money Flow):\n"
                          "  > +0.1: para giriyor\n"
                          "  < -0.1: para cikiyor\n\n"
                          "Bu filtre acikken:\n"
                          "  LONG: OBV veya CMF pozitif olmali\n"
                          "  SHORT: OBV veya CMF negatif olmali\n\n"
                          "Hacimsiz fiyat hareketi guvensizdir,\n"
                          "geri donme ihtimali yuksektir."))
        self._checkbox(s, "volatile_filter", "Volatile Rejim Filtresi (volatilde islem acma)",
                      help_text=(
                          "VOLATILE REJIM FILTRESI\n"
                          "───────────────────────\n"
                          "Piyasa rejimi VOLATILE ise islem acma.\n\n"
                          "NOT: ATR guvenlik kontrolu zaten\n"
                          "hicbir vadede hedef ATR tutmayan\n"
                          "coinleri otomatik eler. Bu filtre\n"
                          "ek bir katman olarak calisir.\n\n"
                          "  Acik: Volatile rejimde giris yok\n"
                          "  Kapali: ATR kontrolune guven"))

        self._field(s, "scan_interval_seconds", "Tarama Araligi (sn)", "30")

        row_kline = ctk.CTkFrame(s, fg_color="transparent")
        row_kline.pack(fill="x", padx=20, pady=2)
        ctk.CTkLabel(row_kline, text="Mum Araligi:", width=180, anchor="w").pack(side="left")
        self._kline_var = ctk.StringVar(value="1m")
        kline_menu = ctk.CTkOptionMenu(row_kline, variable=self._kline_var,
                                        values=["1m", "3m", "5m", "15m", "30m",
                                                "1h", "2h", "4h", "6h", "8h", "12h"],
                                        width=100)
        kline_menu.pack(side="left", padx=5)
        self._all_widgets.append((kline_menu, "menu"))

        # Min timeframe dropdown
        row_min_tf = ctk.CTkFrame(s, fg_color="transparent")
        row_min_tf.pack(fill="x", padx=20, pady=2)
        ctk.CTkLabel(row_min_tf, text="Min Vade (Timeframe):", width=180, anchor="w").pack(side="left")
        self._min_tf_var = ctk.StringVar(value="5m")
        min_tf_menu = ctk.CTkOptionMenu(row_min_tf, variable=self._min_tf_var,
                                         values=["1m", "3m", "5m", "15m", "30m", "1h"],
                                         width=100)
        min_tf_menu.pack(side="left", padx=5)
        self._all_widgets.append((min_tf_menu, "menu"))
        ctk.CTkLabel(row_min_tf, text="Bu vadeden kisa vadeler engellenir",
                     text_color="gray50", font=ctk.CTkFont(size=10)).pack(side="left", padx=5)
        ctk.CTkButton(row_min_tf, text="?", width=24, height=24,
                      fg_color="gray40", hover_color="gray50",
                      font=ctk.CTkFont(size=11, weight="bold"),
                      command=lambda: self._show_help("Min Vade (Timeframe)", (
                          "MINIMUM VADE FILTRESI\n"
                          "─────────────────────\n"
                          "Bu ayar ile belirli vadelerin altindaki\n"
                          "grafiklerin kullanilmasini engellersiniz.\n\n"
                          "Ornek: min_timeframe = 5m\n"
                          "  1m ve 3m vadeleri ENGELLENIR\n"
                          "  5m, 15m, 30m, 1h... kullanilabilir\n\n"
                          "Bir coinin guevenli ATR'si 3m cikarsa\n"
                          "ama min_timeframe=5m ise o coin\n"
                          "ELENIR (islem acilmaz).\n\n"
                          "Neden onemli:\n"
                          "  1m/3m grafikler cok gurultulu\n"
                          "  Sinyal kalitesi dusuk, whipsaw yuksek\n"
                          "  Yuksek kaldiracta SL hemen tetiklenir\n\n"
                          "Tavsiye:\n"
                          "  Yuksek kaldirac: 5m veya 15m\n"
                          "  Dusuk kaldirac: 1m veya 3m yeterli"
                      ))).pack(side="left", padx=5)

        self._field(s, "kline_limit", "Mum Sayisi", "200")

        # ──────────────── LIMIT EMIR (PAZARLIKLI GIRIS) ────────────────
        self._section(s, "Emir Tipi (Limit / Market)")

        # Checkbox: Pazarlikli fiyat kullan
        self._cb_vars["limit_entry_enabled"] = ctk.BooleanVar(value=False)
        row_lim = ctk.CTkFrame(s, fg_color="transparent")
        row_lim.pack(fill="x", padx=20, pady=2)
        cb_lim = ctk.CTkCheckBox(row_lim, text="Pazarlikli Limit Emir Kullan",
                                  variable=self._cb_vars["limit_entry_enabled"])
        cb_lim.pack(side="left")
        self._all_widgets.append((cb_lim, "checkbox"))
        ctk.CTkButton(row_lim, text="?", width=24, height=24,
                      fg_color="gray40", hover_color="gray50",
                      font=ctk.CTkFont(size=11, weight="bold"),
                      command=lambda: self._show_help("Pazarlikli Limit Emir", (
                          "PAZARLIKLI GIRIS (LIMIT EMIR)\n"
                          "─────────────────────────────\n"
                          "Aktif olursa Market yerine Limit emir girilir.\n"
                          "Fiyat X ATR kadar asagiya (LONG) veya yukariya\n"
                          "(SHORT) konur.\n\n"
                          "AVANTAJLARI:\n"
                          "  - Daha ucuza giris (ortalama maliyet duser)\n"
                          "  - Maker fee (%0.02 vs %0.04 taker)\n"
                          "  - Stop mesafesi genis kalir\n"
                          "  - Whipsaw salinimlari firsat olur\n\n"
                          "RISKLERI:\n"
                          "  - Fiyat geri gelmezse firsat kacabilir\n"
                          "  - Timeout sonrasi emir iptal edilir\n"
                          "  - Dolunca sinyal tekrar kontrol edilir\n\n"
                          "ORNEK (0.5 ATR offset):\n"
                          "  Fiyat: 100, ATR: 0.50\n"
                          "  LONG limit: 99.75 (0.25$ ucuz)\n"
                          "  Market fee: %0.04 → Limit fee: %0.02\n"
                          "  Stop: 2 ATR altinda (98.75 vs 99.00)"
                      ))).pack(side="left", padx=5)

        self._field(s, "limit_atr_offset", "Limit ATR Ofseti", "0.5",
                    tip="Kac ATR asagiya/yukariya limit emir konur. 0.25=yakin, 0.5=orta, 1.0=uzak")
        self._field(s, "limit_timeout_seconds", "Limit Timeout (sn)", "300",
                    tip="Dolmayan limit emir kac saniye sonra iptal edilir. 300=5dk")
        self._cb_vars["limit_recheck_signal"] = ctk.BooleanVar(value=True)
        row_rchk = ctk.CTkFrame(s, fg_color="transparent")
        row_rchk.pack(fill="x", padx=20, pady=2)
        cb_rchk = ctk.CTkCheckBox(row_rchk, text="Dolunca sinyal tekrar kontrol et",
                                   variable=self._cb_vars["limit_recheck_signal"])
        cb_rchk.pack(side="left")
        self._all_widgets.append((cb_rchk, "checkbox"))

        self._checkbox(s, "market_fallback_on_limit_timeout",
                       "Limit Timeout → Market Fallback",
                       default=False,
                       help_text=(
                           "LIMIT TIMEOUT MARKET FALLBACK\n"
                           "─────────────────────────────\n"
                           "Limit emir suresi dolunca otomatik\n"
                           "olarak market emre doner.\n\n"
                           "Aktif: Timeout sonrasi market ile girilir\n"
                           "  (firsat kacirilmaz ama taker fee odenır)\n\n"
                           "Kapali: Timeout sonrasi emir iptal edilir\n"
                           "  (firsat kacirilabilir ama fee tasarrufu)"))

        # ──────────────── KALDIRAC & POZISYON ────────────────
        self._section(s, "Kaldirac & Pozisyon Boyutu")

        row_lev = ctk.CTkFrame(s, fg_color="transparent")
        row_lev.pack(fill="x", padx=20, pady=2)
        ctk.CTkLabel(row_lev, text="Kaldirac Araligi:", width=180, anchor="w").pack(side="left")
        self._entries["min_leverage"] = e3 = ctk.CTkEntry(row_lev, width=70)
        e3.pack(side="left", padx=2)
        self._all_widgets.append((e3, "entry"))
        ctk.CTkLabel(row_lev, text=" - ").pack(side="left")
        self._entries["max_leverage"] = e4 = ctk.CTkEntry(row_lev, width=70)
        e4.pack(side="left", padx=2)
        self._all_widgets.append((e4, "entry"))
        ctk.CTkLabel(row_lev, text="x").pack(side="left")

        self._field(s, "max_positions", "Max Esanli Pozisyon", "6",
                    help_text=(
                        "ESANLI POZISYON LIMITI\n"
                        "──────────────────────\n"
                        "Ayni anda en fazla kac pozisyon acik.\n\n"
                        "Emre Ortalama sistemi:\n"
                        "  Portfoy = CEPHE + YEDEK + IHTIYAT\n"
                        "  4 cephe + 4 yedek + 4 ihtiyat = 12\n\n"
                        "  Max pozisyon = 4 (cephe hatti)\n"
                        "  Yedek: cephe dustugunde devreye girer\n"
                        "  Ihtiyat: yedek dustugunde son rezerv\n\n"
                        "Daha fazla pozisyon = daha fazla risk\n"
                        "ama daha fazla firsat. Korelasyon\n"
                        "riski: 4 LONG ayni anda BTC duserse\n"
                        "hepsi SL'ye takilabilir."))
        self._field(s, "portfolio_percent", "Portfoy Yuzdesi (%)", "25",
                    tip="Her pozisyon icin bakiyenin yuzde kaci kullanilsin")
        self._field(s, "portfolio_divider", "Portfoy Boleni (1/N)", "0",
                    tip="Bakiyenin 1/N'i (ornek: 12 = 1/12). 0=yuzde modu kullan",
                    help_text=(
                        "PORTFOY BOLENI (1/N sistemi)\n"
                        "────────────────────────────\n"
                        "0 = yuzde modu (portfolio_percent kullan)\n"
                        "12 = Emre Ortalama sistemi\n\n"
                        "Bakiye <= 12$: bolen = floor(bakiye)\n"
                        "  3$ → 3 poz x 1$ = tam cephe\n"
                        "  5$ → 4 poz x 1$ + 1$ yedek\n"
                        "  12$ → 4 poz x 1$ + 4$ yedek + 4$\n\n"
                        "Bakiye > 12$: bolen = 12\n"
                        "  18$ → 4 poz x 1.5$ = olceklenen\n"
                        "  24$ → 4 poz x 2.0$\n"
                        "  36$ → 4 poz x 3.0$\n\n"
                        "Min margin: 1.0 USDT (bu kural sabit)"))

        # ──────────────── STOP LOSS ────────────────
        self._section(s, "Stop Loss")
        self._checkbox(s, "sl_enabled", "Stop Loss Aktif")
        self._field(s, "liq_factor", "Pratik Liq Faktoru (%)", "70",
                    tip="Teorik liq mesafesinin yuzde kaci pratik liq (Binance erken likide eder, 70=gercekci)",
                    help_text=(
                        "PRATIK LIKIDASYON FAKTORU\n"
                        "─────────────────────────\n"
                        "Binance teorik liq noktasindan ONCE\n"
                        "likide eder (bakim marjini yuzunden).\n\n"
                        "Teorik liq = 1 / kaldirac\n"
                        "Pratik liq = teorik x bu_faktor\n\n"
                        "Ornek (100x):\n"
                        "  Teorik liq = %1.0\n"
                        "  %70 faktor → Pratik = %0.70\n"
                        "  %80 faktor → Pratik = %0.80\n\n"
                        "70 = gercekci (onerilen)\n"
                        "80 = daha rahat ama riskli"))
        self._field(s, "sl_liq_percent", "SL Yuzde (pratik liq %)", "50",
                    tip="Pratik liq mesafesinin yuzde kacinda SL olsun (50 = yaridaki mesafe)",
                    help_text=(
                        "STOP LOSS MESAFESI\n"
                        "──────────────────\n"
                        "Pratik liq mesafesinin yuzde kaci.\n"
                        "Binance'te STOP_MARKET olarak yerlesir.\n\n"
                        "SL = pratik_liq x bu_yuzde\n\n"
                        "Ornek (100x, liq_factor=70):\n"
                        "  Pratik liq = %0.70\n"
                        "  %50 → SL = %0.35 (onerilen)\n"
                        "  %40 → SL = %0.28 (siki)\n"
                        "  %60 → SL = %0.42 (gevsek)\n\n"
                        "SL = 2x ATR kurali:\n"
                        "  ATR referans = SL / 2\n"
                        "  Vade secimi bu ATR'ye gore yapilir"))
        self._field(s, "server_sl_atr_mult", "Server SL ATR Carpani", "2.0",
                    tip="Sunucu STOP_MARKET emri kac ATR uzakta olsun (2.0=standart)",
                    help_text=(
                        "SERVER SL ATR CARPANI\n"
                        "─────────────────────\n"
                        "Pozisyon acildiginda Binance'e\n"
                        "STOP_MARKET emri gonderilir.\n\n"
                        "SL fiyati = giris +/- (N x ATR)\n\n"
                        "  2.0 = standart (onerilen)\n"
                        "  1.5 = siki (erken tetiklenir)\n"
                        "  3.0 = gevsek (daha fazla kayip)\n\n"
                        "Bu deger yazilim SL'si ile AYNI\n"
                        "olmali. Ikisi birbirini tamamlar:\n"
                        "  Yazilim: her saniye kontrol\n"
                        "  Server: yazilim cokerse devreye girer"))
        self._checkbox(s, "emergency_enabled", "Emergency Close (yazilim korumasi)",
                      help_text=(
                          "EMERGENCY ANTI-LIKIDASYON\n"
                          "─────────────────────────\n"
                          "SL'nin ARKASINDA bekleyen son savunma.\n\n"
                          "SL (sunucu) tetiklenmezse devreye girer:\n"
                          "  - API hatasi, slippage, ani gap\n"
                          "  - SL emri red edildi/iptal oldu\n\n"
                          "Yazilim her 1 sn fiyat kontrol eder.\n"
                          "Liq mesafesinin %80'inde acil kapatir.\n\n"
                          "  SL tetiklenir:        %0.35 (100x)\n"
                          "  Emergency tetiklenir:  %0.56 (100x)\n"
                          "  Likidasyon olur:       %0.70 (100x)\n\n"
                          "SL'yi ezmez, tamamlar. Her zaman acik\n"
                          "tutulmasi onerilen guvenlik katmani."))
        self._field(s, "emergency_liq_percent", "Emergency Yuzde (liq mesafesi %)", "80",
                    tip="Likidasyon mesafesinin yuzde kacinda acil kapat (SL'den sonra, son savunma)")

        # ──────────────── TRAILING STOP ────────────────
        self._section(s, "Iz Suren Stop (Trailing)")
        self._checkbox(s, "trailing_enabled", "Trailing Stop Aktif",
                      help_text=(
                          "TRAILING STOP (Iz Suren Stop)\n"
                          "─────────────────────────────\n"
                          "Kar buyudukce SL'yi ileri tasir.\n"
                          "Trend '2 ileri 1 geri' gider,\n"
                          "geri cekilme trailing'i tetikler.\n\n"
                          "ATR MODU (onerilen):\n"
                          "  Aktivasyon: N x ATR kar olunca basla\n"
                          "  Mesafe: M x ATR geri gelince sat\n"
                          "  Ornek 4/1: 4 ATR karda basla,\n"
                          "    1 ATR geri gelince sat\n"
                          "  Min kar = (N-M) x ATR garanti\n\n"
                          "HYBRID RENEWAL:\n"
                          "  Trailing tetiklendi ama sinyal hala\n"
                          "  AL diyorsa → pozisyon kapatilmaz,\n"
                          "  trailing sifirlanir (sifir fee).\n"
                          "  SL ileri tasinir, yeni hedef belirlenir.\n\n"
                          "ROI MODU:\n"
                          "  Dogrudan ROI% uzerinden hesaplanir.\n"
                          "  Fee carpani ile otomatik ayarlanabilir."))

        self._checkbox(s, "server_trailing_dynamic_update", "Server Trailing Dinamik Guncelleme",
                      help_text=(
                          "SERVER TRAILING DINAMIK GUNCELLEME\n"
                          "──────────────────────────────────\n"
                          "KAPALI (onerilen): Server SL + trailing\n"
                          "  pozisyon acilisinda bir kez konur,\n"
                          "  bir daha dokunulmaz.\n"
                          "  Fiyat 2xATR ters → SL tetikler\n"
                          "  Fiyat 4xATR dogru + 1xATR geri → trailing tetikler\n\n"
                          "ACIK: Her 30 saniyede sinyal gucune\n"
                          "  gore callback daraltilir/genisletilir.\n"
                          "  DIKKAT: erken kapanma riski yuksek!\n"
                          "  Normal piyasa gurultusu pozisyonu\n"
                          "  kapatabilir."))

        # Trailing mode selector
        row_tmode = ctk.CTkFrame(s, fg_color="transparent")
        row_tmode.pack(fill="x", padx=20, pady=2)
        ctk.CTkLabel(row_tmode, text="Trailing Modu:", width=240, anchor="w").pack(side="left")
        self._trailing_mode_var = ctk.StringVar(value="roi")
        tmode_seg = ctk.CTkSegmentedButton(
            row_tmode, values=["atr", "roi"],
            variable=self._trailing_mode_var,
            font=ctk.CTkFont(size=11),
        )
        tmode_seg.pack(side="left", padx=5)
        self._all_widgets.append((tmode_seg, "seg"))
        ctk.CTkLabel(row_tmode, text="(atr=ATR bazli, roi=ROI bazli)",
                     text_color="gray60", font=ctk.CTkFont(size=10)).pack(side="left", padx=5)

        # ATR-based trailing fields
        self._field(s, "trailing_atr_activate_mult", "ATR Aktivasyon (x ATR)", "4.0",
                    tip="Kac ATR kar olunca trailing baslasin (4=konservatif, 5=agresif)")
        self._field(s, "trailing_atr_distance_mult", "ATR Mesafe (x ATR)", "1.0",
                    tip="Kac ATR geri cekilince sat (1=konservatif, 2=agresif)")
        # ROI-based trailing fields
        self._field(s, "trailing_activate_roi", "ROI Aktivasyon (%)", "0",
                    tip="Dogrudan ROI% (ornek: 60 = %60 ROI'de basla). 0=fee carpani kullan")
        self._field(s, "trailing_distance_roi", "ROI Mesafe (%)", "0",
                    tip="Geri cekilme ROI% (ornek: 10 = %10 geri gelince sat). 0=fee carpani kullan")
        self._field(s, "trailing_activate_fee_mult", "Fee Carpani Aktivasyon", "3.0",
                    tip="ROI=0 ise kullanilir. Kac x fee ROI'de trailing baslasin")
        self._field(s, "trailing_distance_fee_mult", "Fee Carpani Mesafe", "2.0",
                    tip="ROI=0 ise kullanilir. Trailing mesafesi")

        # ──────────────── KAR HEDEFI ────────────────
        self._section(s, "Kar Hedefi (Take Profit)")
        self._checkbox(s, "tp_enabled", "Take Profit Aktif")
        self._field(s, "tp_liq_multiplier", "TP Carpani (liq mesafesi x)", "3.0",
                    tip="Likidasyon mesafesinin kac kati (3.0 = 75x'te %3.4 fiyat hareketi)")

        row_tp_mode = ctk.CTkFrame(s, fg_color="transparent")
        row_tp_mode.pack(fill="x", padx=20, pady=2)
        ctk.CTkLabel(row_tp_mode, text="Cikis Modu:", width=180, anchor="w").pack(side="left")
        self._tp_mode_var = ctk.StringVar(value="immediate")
        tp_seg = ctk.CTkSegmentedButton(
            row_tp_mode, values=["immediate", "signal"],
            variable=self._tp_mode_var,
            font=ctk.CTkFont(size=11),
        )
        tp_seg.pack(side="left", padx=5)
        self._all_widgets.append((tp_seg, "seg"))
        ctk.CTkLabel(row_tp_mode, text="(immediate=hedefe ulasinca sat, signal=sinyal bekle)",
                     text_color="gray60", font=ctk.CTkFont(size=10)).pack(side="left", padx=5)

        # ──────────────── KISMI KAR AL (PARTIAL TP) ────────────────
        self._section(s, "Kismi Kar Al (Partial TP)")
        self._checkbox(s, "partial_tp_enabled", "Kismi Kar Al (Partial TP)",
                       default=False,
                       help_text=(
                           "KISMI KAR AL (PARTIAL TP)\n"
                           "─────────────────────────\n"
                           "Belirli bir kar seviyesine ulasinca\n"
                           "pozisyonun bir kismini kapatir.\n\n"
                           "AVANTAJLARI:\n"
                           "  - Kari kismen realize eder\n"
                           "  - Kalan pozisyon trailing ile devam\n"
                           "  - Risk azaltilmis olur\n\n"
                           "ORNEK:\n"
                           "  ATR mult=3, kapanis=%50\n"
                           "  3 ATR karda pozisyonun %50'si kapanir\n"
                           "  Kalan %50 trailing ile devam eder"))
        self._field(s, "partial_tp_atr_mult", "Partial TP ATR Mult", "3.0",
                    tip="Kac ATR karda kismi kar al tetiklensin (3.0=standart)")
        self._field(s, "partial_tp_close_pct", "Kapanacak Oran (%)", "50",
                    tip="Pozisyonun yuzde kaci kapansin (50=%50)")

        # ──────────────── SINYAL CIKIS ────────────────
        self._section(s, "Sinyal Bazli Cikis")
        self._checkbox(s, "signal_exit_enabled", "Sinyal Donusu Cikis (confluence ters donunce)",
                      help_text=(
                          "SINYAL CIKIS (EN KRITIK KURAL)\n"
                          "──────────────────────────────\n"
                          "Indikatorler ters donunce pozisyonu\n"
                          "kapat. SL ve Emergency disinda EN\n"
                          "YUKSEK oncelikli cikis sinyali.\n\n"
                          "CIKIS ONCELIK SIRASI:\n"
                          "  0. Emergency    liq x %80\n"
                          "  1. Stop Loss    liq x %50\n"
                          "  2. SINYAL CIKIS (bu ayar)\n"
                          "  3. Take Profit  (opsiyonel)\n"
                          "  4. Trailing     N x ATR\n"
                          "  5. Divergence   (profit zone)\n"
                          "  6. Regime       (profit zone)\n"
                          "  7. Zaman Limiti\n\n"
                          "Neden onemli:\n"
                          "  +1 ATR'de sinyal SAT derse:\n"
                          "    Sat → kucuk kar (%7.5 ROI)\n"
                          "    Tut → SL riski (-%35 ROI)\n\n"
                          "  Erken cikis 3:1 R:R'i korur.\n"
                          "  Tek iyi trade 3 kotu trade'i\n"
                          "  karsilar.\n\n"
                          "'Sadece Karda Cik' ile birlikte\n"
                          "kullanilabilir (asagida)."))
        self._field(s, "signal_exit_threshold", "Sinyal Esik Degeri", "4.0",
                    tip="Confluence skoru bu degerin altina dusunce sat (ornek: 4.0)")
        self._field(s, "signal_min_hold_seconds", "Min Bekle (sn)", "30",
                    tip="Pozisyon acildiktan sonra min bekleme suresi")
        self._checkbox(s, "signal_only_in_profit", "Sadece Karda Cik (zarardayken sinyal yoksay)",
                      help_text=(
                          "SADECE KARDA SINYAL CIKIS\n"
                          "─────────────────────────\n"
                          "Acik: Zararda normal esik (-4) ile\n"
                          "cikmaz, sadece DERIN reversal esigi\n"
                          "(asagida) gecilirse cikar.\n\n"
                          "Kapali: Zararda da normal esik (-4)\n"
                          "ile cikar (whipsaw riski!).\n\n"
                          "Onerilen: KAPALI + Derin esik 8.0\n"
                          "  Karda: -4'te cik (normal)\n"
                          "  Zararda: -8'de cik (derin reversal)\n"
                          "  Yapisan indikatorler de donmusse\n"
                          "  bu whipsaw degil, gercek donus."))
        self._field(s, "signal_deep_exit_threshold", "Zararda Cikis Esigi", "8.0",
                    tip=("Zarardayken sinyal cikis esigi (ornek: 8.0).\n"
                         "Normal esikten yuksek olmali (whipsaw koruması).\n"
                         "Karda: -4, Zararda: -8 = yapisan indikatorler\n"
                         "de donmus olmali (gercek trend donusu)"))
        self._checkbox(s, "divergence_exit_enabled", "Divergence Cikis (bearish divergence'ta sat)",
                      help_text=(
                          "DIVERGENCE (Iraksama) CIKIS\n"
                          "───────────────────────────\n"
                          "Fiyat ile indikator FARKLI yonde\n"
                          "gittiginde tetiklenir.\n\n"
                          "Bearish Divergence:\n"
                          "  Fiyat: yeni zirve yapiyor\n"
                          "  RSI:   zirve dusuyor\n"
                          "  → Alicilar tukeniyor, donus yakin\n\n"
                          "Bullish Divergence:\n"
                          "  Fiyat: yeni dip yapiyor\n"
                          "  RSI:   dip yukseliyor\n"
                          "  → Saticilar tukeniyor, donus yakin\n\n"
                          "SADECE profit zone'da (N x ATR ustu)\n"
                          "aktif. Yanlis alarm orani yuksek\n"
                          "oldugu icin zararda kullanilmaz.\n\n"
                          "Kar koruma araci olarak degerli."))

        # ──────────────── ZAMAN LIMITI ────────────────
        self._section(s, "Zaman Limiti")
        self._checkbox(s, "time_limit_enabled", "Zaman Limiti Aktif")
        self._field(s, "time_limit_minutes", "Max Tutma (dk)", "480",
                    tip="Pozisyon en fazla kac dakika tutulsun")
        self._checkbox(s, "time_limit_extend_trailing",
                       "Trailing Aktifse Uzat (trailing varsa zaman limiti iptal)")
        self._checkbox(s, "time_limit_extend_breakeven",
                       "Breakeven'da Uzat (fee civarindaysa 2x sure ver)")

        # ──────────────── RISK ────────────────
        self._section(s, "Risk & Bekleme")
        self._field(s, "cooldown_seconds", "Satis Sonrasi Bekleme (sn)", "120",
                    tip="Pozisyon kapatildiktan sonra kac saniye bekle")
        self._field(s, "loss_cooldown_seconds", "Zarar Cooldown (sn)", "600",
                    tip="Ayni coinde zarar sonrasi tekrar giris bekleme suresi (600=10dk)",
                    help_text=(
                        "ZARAR COOLDOWN\n"
                        "──────────────\n"
                        "Bir pozisyon zararla kapatildiginda\n"
                        "ayni coine bu sure boyunca tekrar\n"
                        "girilmez.\n\n"
                        "Neden gerekli:\n"
                        "  Coin zararla kapandi → sinyal hala\n"
                        "  guclu gozukebilir → tekrar girer →\n"
                        "  ayni sonuc → ardisik zarar spirali\n\n"
                        "  300 =  5 dakika\n"
                        "  600 = 10 dakika (onerilen)\n"
                        "  900 = 15 dakika\n\n"
                        "Sadece ZARAR ile kapanan coinlere\n"
                        "uygulanir. Karla kapanan coinler\n"
                        "cooldown'a girmez."))

        # ──────────────── YON DENGESI (LONG/SHORT) ────────────────
        self._section(s, "Yon Dengesi (Long/Short Orani)")

        self._cb_vars["direction_balance_enabled"] = ctk.BooleanVar(value=False)
        row_db = ctk.CTkFrame(s, fg_color="transparent")
        row_db.pack(fill="x", padx=20, pady=2)
        cb_db = ctk.CTkCheckBox(row_db, text="Yon Dengeleme Aktif",
                                 variable=self._cb_vars["direction_balance_enabled"])
        cb_db.pack(side="left")
        self._all_widgets.append((cb_db, "checkbox"))
        ctk.CTkButton(row_db, text="?", width=24, height=24,
                      fg_color="gray40", hover_color="gray50",
                      font=ctk.CTkFont(size=11, weight="bold"),
                      command=lambda: self._show_help("Yon Dengesi", (
                          "YON DENGESI (Long/Short Orani)\n"
                          "──────────────────────────────\n"
                          "Ayni yonde cok fazla pozisyon acilmasini\n"
                          "engelleyerek korelasyon riskini azaltir.\n\n"
                          "ORAN X-Y NASIL CALISIR:\n"
                          "  X = bir yondeki max pozisyon\n"
                          "  Y = zorunlu ters yon adedi\n\n"
                          "ORNEK 2-1:\n"
                          "  2 Long acildi → 3. Long ENGEL\n"
                          "  1 Short acilmali → 2L 1S\n"
                          "  Sonra 2 Long daha acilabilir → 4L 2S\n\n"
                          "ORNEK 1-1:\n"
                          "  Her Long icin 1 Short gerekir\n"
                          "  Surekli dengeli portfoy\n\n"
                          "Pozisyon kapaninca denge bozulabilir:\n"
                          "  Oran yeniden saglanana kadar\n"
                          "  baskın yonde yeni pozisyon acilamaz."
                      ))).pack(side="left", padx=5)

        # Ratio combo box
        row_ratio = ctk.CTkFrame(s, fg_color="transparent")
        row_ratio.pack(fill="x", padx=20, pady=2)
        ctk.CTkLabel(row_ratio, text="Oran (X-Y):", width=240, anchor="w").pack(side="left")
        ratio_values = [
            "1-1", "2-1", "3-1", "3-2",
            "4-1", "4-3", "5-1", "5-2", "5-3", "5-4",
        ]
        self._ratio_var = ctk.StringVar(value="2-1")
        ratio_combo = ctk.CTkComboBox(row_ratio, values=ratio_values,
                                       variable=self._ratio_var, width=100,
                                       state="readonly")
        ratio_combo.pack(side="left", padx=5)
        self._all_widgets.append((ratio_combo, "combo"))
        ctk.CTkLabel(row_ratio, text="(1-1=esit, 2-1=2 ayni yon + 1 ters, ...)",
                     text_color="gray50", font=ctk.CTkFont(size=10)).pack(side="left", padx=5)

        # ──────────────── BTC KORELASYON FILTRESI ────────────────
        self._section(s, "BTC Korelasyon Filtresi")

        self._checkbox(s, "btc_correlation_enabled", "BTC Korelasyon Filtresi",
                       default=False,
                       help_text=(
                           "BTC KORELASYON FILTRESI\n"
                           "───────────────────────\n"
                           "Portfoyun toplam BTC beta'sini kontrol\n"
                           "eder. Beta cok yuksekse yeni pozisyon\n"
                           "acilmasini engeller.\n\n"
                           "Beta nedir:\n"
                           "  Beta = 1.0: BTC ile ayni hareket\n"
                           "  Beta > 1.0: BTC'den fazla hareket\n"
                           "  Beta < 1.0: BTC'den az hareket\n"
                           "  Beta < 0:   BTC'nin tersi hareket\n\n"
                           "Yuksek beta = yuksek korelasyon riski.\n"
                           "BTC duserse tum portfoy birlikte duser.\n\n"
                           "Max portfoy beta limiti bu riski sinirlar."))
        self._field(s, "btc_max_portfolio_beta", "Max Portfoy Beta", "2.5",
                    tip="Portfoy toplam beta'si bu degeri asarsa yeni pozisyon acilmaz (2.5=standart)")

        # ──────────────── COIN GUNLUK YASAK ────────────────
        self._section(s, "Coin Gunluk Yasak (Kayip Limiti)")

        self._field(s, "coin_daily_loss_limit", "Max Zarar Sayisi (0=kapali)", "0",
                    tip="Bir coin 24 saatte kac kere zarar ederse yasaklanir (0=kapali)",
                    help_text=(
                        "COIN GUNLUK ZARAR YASAGI\n"
                        "─────────────────────────\n"
                        "Bir coin 24 saat icinde belirtilen sayida\n"
                        "zarar ettirirse, o coin yasaklanir.\n\n"
                        "ORNEK (limit=3, ban=24):\n"
                        "  ROBOUSDT 3 kere zarar etti\n"
                        "  → 24 saat boyunca ROBOUSDT'ye girilmez\n\n"
                        "Neden gerekli:\n"
                        "  Bazi coinler tekrarlayan zarar yapar\n"
                        "  Loss cooldown (10dk) yetmez\n"
                        "  Ayni coini 12 kere alip 0.95$ kaybetmek\n"
                        "  yerine 3. zarardan sonra ertele\n\n"
                        "  0 = kapali (yasak yok)\n"
                        "  3 = 3 zarar sonrasi yasak (onerilen)\n"
                        "  5 = daha toleransli"))
        self._field(s, "coin_daily_ban_hours", "Yasak Suresi (saat)", "24",
                    tip="Zarar limiti asilinca coin kac saat yasakli kalir (24=1 gun)")

        # ──────────────── LIMIT CIKIS ────────────────
        self._section(s, "Limit Cikis (Fee Tasarrufu)")

        self._cb_vars["limit_exit_enabled"] = ctk.BooleanVar(value=False)
        row_le = ctk.CTkFrame(s, fg_color="transparent")
        row_le.pack(fill="x", padx=20, pady=2)
        cb_le = ctk.CTkCheckBox(row_le, text="Cikista Limit Emir Kullan (Maker Fee)",
                                 variable=self._cb_vars["limit_exit_enabled"])
        cb_le.pack(side="left")
        self._all_widgets.append((cb_le, "checkbox"))
        ctk.CTkButton(row_le, text="?", width=24, height=24,
                      fg_color="gray40", hover_color="gray50",
                      font=ctk.CTkFont(size=11, weight="bold"),
                      command=lambda: self._show_help("Limit Cikis", (
                          "LIMIT CIKIS (Maker Fee Tasarrufu)\n"
                          "──────────────────────────────────\n"
                          "Pozisyon kapatirken Market yerine Limit\n"
                          "emir gonderir. Fiyat lehimize konur:\n\n"
                          "  LONG kapama: piyasanin biraz USTUNE sat\n"
                          "  SHORT kapama: piyasanin biraz ALTINA al\n\n"
                          "Emir deftere oturur → Maker fee (%0.02)\n"
                          "Market emir → Taker fee (%0.04)\n\n"
                          "TASARRUF HESABI:\n"
                          "  65 trade/gun x 20$ x %0.02 fark\n"
                          "  = ~0.26$ gunluk tasarruf (sadece cikis)\n"
                          "  Giris + cikis = ~0.52$ tasarruf\n\n"
                          "RISK: Fiyat hizla hareket ederse limit\n"
                          "emir dolmayabilir. Dolmazsa otomatik\n"
                          "market emre doner."
                      ))).pack(side="left", padx=5)

        self._field(s, "limit_exit_atr_offset", "Cikis ATR Ofseti", "0.2",
                    tip="Cikista kac ATR lehimize limit fiyat konur (0.2=yakin, hizli dolsun)")

        # ──────────────── TARAYICI AYARLARI ────────────────
        self._section(s, "Tarayici (Scanner)")
        self._field(s, "max_symbols_to_scan", "Taranacak Coin Sayisi", "50",
                    tip="Hacim siralamasindan en fazla kac coin taransin")
        self._checkbox(s, "battle_mode", "Savas Modu (tek coin odakli)",
                      help_text=(
                          "SAVAS MODU (Battle Mode)\n"
                          "────────────────────────\n"
                          "Aktifken sadece watched_symbols\n"
                          "listesindeki coinler taranir.\n\n"
                          "Scanner genis piyasayi taramaz,\n"
                          "odak dar tutulur. Manuel secilen\n"
                          "coinlerle sinirli islem yapilir."))
        self._checkbox(s, "close_only", "Sadece Kapama Modu (yeni pozisyon acma)",
                      help_text=(
                          "SADECE KAPAMA MODU\n"
                          "──────────────────\n"
                          "Aktifken yeni pozisyon acilmaz.\n"
                          "Mevcut pozisyonlar normal sekilde\n"
                          "yonetilir (trailing, SL, sinyal cikis).\n\n"
                          "Kullanim:\n"
                          "  - Piyasadan cekilme oncesi\n"
                          "  - Risk yonetimi (buyuk haber oncesi)\n"
                          "  - Sistemin sadece kapatma yapmasini\n"
                          "    istediginizde"))
        self._checkbox(s, "focus_mode", "Odak Modu (aktif sembole odaklan)",
                      help_text=(
                          "ODAK MODU (Focus Mode)\n"
                          "──────────────────────\n"
                          "Aktifken scanner sadece aktif\n"
                          "sembole (config: active_symbol)\n"
                          "odaklanir. Diger coinleri taramaz.\n\n"
                          "Manuel islem yaparken kullanisli."))

        # ── Info box with dynamic calculations ──
        self._info_frame = ctk.CTkFrame(s, fg_color="#1a1a2e", corner_radius=8)
        self._info_frame.pack(fill="x", padx=10, pady=10)
        self._info_label = ctk.CTkLabel(
            self._info_frame, text="", justify="left",
            font=ctk.CTkFont(size=11, family="Consolas"),
            text_color="#88CC88",
        )
        self._info_label.pack(padx=10, pady=8, anchor="w")
        self._update_info()

        # ── Save / Reset buttons ──
        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.pack(fill="x", padx=10, pady=(5, 2))
        ctk.CTkButton(btn_frame, text="Kaydet", width=120, fg_color="#00C853",
                      hover_color="#00A846",
                      command=self._save).pack(side="left", padx=5)
        ctk.CTkButton(btn_frame, text="Sifirla (Varsayilan)", width=150,
                      fg_color="gray30", command=self._reset_to_default).pack(side="left", padx=5)

        # ── Template system (visible in manuel mode) ──
        self._tmpl_frame = ctk.CTkFrame(self, fg_color="#1a1a2e")
        self._tmpl_frame.pack(fill="x", padx=10, pady=(2, 10))

        ctk.CTkLabel(self._tmpl_frame, text="Sablonlar:",
                     font=ctk.CTkFont(weight="bold")).pack(side="left", padx=10, pady=8)

        self._tmpl_var = ctk.StringVar(value="")
        self._tmpl_menu = ctk.CTkOptionMenu(
            self._tmpl_frame, variable=self._tmpl_var,
            values=["(sablon sec)"], width=200,
            command=self._on_template_select,
        )
        self._tmpl_menu.pack(side="left", padx=5, pady=8)

        ctk.CTkButton(self._tmpl_frame, text="Yukle", width=80,
                      fg_color="#2196F3", hover_color="#1976D2",
                      command=self._load_template).pack(side="left", padx=3)
        ctk.CTkButton(self._tmpl_frame, text="Kaydet", width=100,
                      fg_color="#FF9800", hover_color="#F57C00",
                      command=self._save_template).pack(side="left", padx=3)
        ctk.CTkButton(self._tmpl_frame, text="Sil", width=60,
                      fg_color="#FF1744", hover_color="#D50000",
                      command=self._delete_template).pack(side="left", padx=3)

        self._refresh_template_list()

    # ════════════════════════════════════════
    # UI HELPERS
    # ════════════════════════════════════════

    def _section(self, parent, title: str) -> None:
        ctk.CTkLabel(parent, text=title,
                     font=ctk.CTkFont(size=14, weight="bold")).pack(
            anchor="w", padx=10, pady=(15, 5))

    def _field(self, parent, key: str, label: str, default: str,
              tip: str = "", help_text: str = "") -> None:
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", padx=20, pady=2)
        ctk.CTkLabel(row, text=f"{label}:", width=240, anchor="w").pack(side="left")
        entry = ctk.CTkEntry(row, width=100)
        entry.pack(side="left", padx=5)
        entry.insert(0, default)
        self._entries[key] = entry
        self._all_widgets.append((entry, "entry"))
        if help_text:
            btn = ctk.CTkButton(row, text="?", width=24, height=24,
                                fg_color="gray40", hover_color="gray50",
                                font=ctk.CTkFont(size=11, weight="bold"),
                                command=lambda t=label, h=help_text: self._show_help(t, h))
            btn.pack(side="left", padx=2)
        if tip:
            ctk.CTkLabel(row, text=tip, text_color="gray50",
                         font=ctk.CTkFont(size=10)).pack(side="left", padx=5)

    def _checkbox(self, parent, key: str, label: str,
                  default: bool = True, help_text: str = "") -> None:
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", padx=20, pady=2)
        var = ctk.BooleanVar(value=default)
        cb = ctk.CTkCheckBox(row, text=label, variable=var)
        cb.pack(side="left")
        self._cb_vars[key] = var
        self._all_widgets.append((cb, "checkbox"))
        if help_text:
            btn = ctk.CTkButton(row, text="?", width=24, height=24,
                                fg_color="gray40", hover_color="gray50",
                                font=ctk.CTkFont(size=11, weight="bold"),
                                command=lambda t=label, h=help_text: self._show_help(t, h))
            btn.pack(side="left", padx=5)

    # ════════════════════════════════════════
    # MODE & PRESETS
    # ════════════════════════════════════════

    def _on_mode_change(self, mode: str) -> None:
        is_manual = mode == "manuel"
        # Show/hide preset frame
        if is_manual:
            self._preset_frame.pack_forget()
        else:
            self._preset_frame.pack(fill="x", padx=10, pady=3,
                                     after=self._feedback.master.winfo_children()[0])
            # Re-pack it after the top frame
            self._preset_frame.pack(fill="x", padx=10, pady=3, before=self._feedback)

        # Both modes: fields always editable (preset fills defaults, user can fine-tune)
        for widget, wtype in self._all_widgets:
            if wtype == "entry":
                widget.configure(state="normal")
            elif wtype == "checkbox":
                widget.configure(state="normal")
            elif wtype in ("menu", "seg"):
                widget.configure(state="normal")

    def _apply_preset(self, preset_key: str) -> None:
        preset = PRESETS[preset_key]
        vals = preset["values"]
        self._preset_var.set(preset_key)
        self._preset_desc.configure(text=preset["desc"])

        # Fill all fields
        for key, val in vals.items():
            if key in self._entries:
                entry = self._entries[key]
                entry.configure(state="normal")
                entry.delete(0, "end")
                entry.insert(0, str(val))
            elif key in self._cb_vars:
                self._cb_vars[key].set(val)
            elif key == "kline_interval":
                self._kline_var.set(val)
            elif key == "min_timeframe":
                self._min_tf_var.set(val)
            elif key == "tp_exit_mode":
                self._tp_mode_var.set(val)
            elif key == "trailing_mode":
                self._trailing_mode_var.set(val)
            elif key == "direction_balance_ratio":
                if hasattr(self, "_ratio_var"):
                    self._ratio_var.set(str(val))

        self._update_info()

        # Auto-save in standard mode
        self._save(show_feedback=False)
        self._show_feedback(f"'{preset['name']}' ayarlari uygulandi!", preset["color"])

    # ════════════════════════════════════════
    # LOAD / SAVE
    # ════════════════════════════════════════

    def _load_from_config(self) -> None:
        c = self.controller.config
        strat = c.get("strategy", {})
        if not strat:
            # First time — apply dengeli preset as default
            self._apply_preset("dengeli")
            return

        # Load mode
        mode = strat.get("mode", "standard")
        self._mode_var.set(mode)

        # Load all values
        for key in self._entries:
            val = strat.get(key)
            if val is not None:
                entry = self._entries[key]
                entry.configure(state="normal")
                entry.delete(0, "end")
                entry.insert(0, str(val))

        for key in self._cb_vars:
            val = strat.get(key)
            if val is not None:
                self._cb_vars[key].set(val)

        kline = strat.get("kline_interval", "1m")
        self._kline_var.set(kline)
        min_tf = strat.get("min_timeframe", "5m")
        self._min_tf_var.set(min_tf)
        tp_mode = strat.get("tp_exit_mode", "immediate")
        self._tp_mode_var.set(tp_mode)
        trailing_mode = strat.get("trailing_mode", "roi")
        self._trailing_mode_var.set(trailing_mode)

        # Load direction balance ratio
        ratio = strat.get("direction_balance_ratio", "2-1")
        if hasattr(self, "_ratio_var"):
            self._ratio_var.set(str(ratio))

        # Load scanner settings into strategy fields
        scanner = c.get("scanner", {})
        for skey in ["max_symbols_to_scan", "loss_cooldown_seconds"]:
            if skey in self._entries and skey not in strat:
                val = scanner.get(skey)
                if val is not None:
                    entry = self._entries[skey]
                    entry.configure(state="normal")
                    entry.delete(0, "end")
                    entry.insert(0, str(val))
        for skey in ["battle_mode", "close_only", "focus_mode"]:
            if skey in self._cb_vars and skey not in strat:
                val = scanner.get(skey)
                if val is not None:
                    self._cb_vars[skey].set(val)

        preset = strat.get("preset", "")
        self._preset_var.set(preset)
        if preset in PRESETS:
            self._preset_desc.configure(text=PRESETS[preset]["desc"])

        self._on_mode_change(mode)
        self._update_info()

    def _save(self, show_feedback: bool = True) -> None:
        c = self.controller.config

        # Collect all values
        strat = {
            "mode": self._mode_var.get(),
            "preset": self._preset_var.get(),
            "kline_interval": self._kline_var.get(),
            "min_timeframe": self._min_tf_var.get(),
            "tp_exit_mode": self._tp_mode_var.get(),
            "trailing_mode": self._trailing_mode_var.get(),
        }

        # Entries (numeric)
        for key, entry in self._entries.items():
            raw = entry.get().strip()
            if not raw:
                continue
            try:
                if "." in raw:
                    strat[key] = float(raw)
                else:
                    strat[key] = int(raw)
            except ValueError:
                strat[key] = raw

        # Checkboxes (bool)
        for key, var in self._cb_vars.items():
            strat[key] = var.get()

        # Direction balance ratio (combo box)
        if hasattr(self, "_ratio_var"):
            strat["direction_balance_ratio"] = self._ratio_var.get()

        # Save to config under "strategy" key (single source of truth)
        c.set("strategy", strat)

        # Sync leverage settings (leverage section still used by some legacy code)
        c.set("leverage.min_leverage", strat.get("min_leverage", 1))
        c.set("leverage.max_leverage", strat.get("max_leverage", 20))
        c.set("leverage.portfolio_percent", strat.get("portfolio_percent", 8))
        c.set("leverage.max_hold_minutes", strat.get("time_limit_minutes", 480))
        c.set("indicators.kline_interval", strat.get("kline_interval", "5m"))

        c.save()

        if show_feedback:
            self._show_feedback("Strateji ayarlari kaydedildi!", "#00C853")
        self._update_info()

    def _reset_to_default(self) -> None:
        self._mode_var.set("standard")
        self._apply_preset("dengeli")
        self._on_mode_change("standard")

    # ════════════════════════════════════════
    # INFO BOX
    # ════════════════════════════════════════

    def _update_info(self) -> None:
        """Show calculated values based on current settings."""
        try:
            max_lev = int(self._entries["max_leverage"].get() or 100)
            liq_f = int(self._entries["liq_factor"].get() or 70)
            sl_pct = int(self._entries["sl_liq_percent"].get() or 50)
            em_pct = int(self._entries["emergency_liq_percent"].get() or 80)
            tp_mult = float(self._entries["tp_liq_multiplier"].get() or 3.0)
            trail_act = float(self._entries["trailing_activate_fee_mult"].get() or 3.0)
            trail_dist = float(self._entries["trailing_distance_fee_mult"].get() or 2.0)

            liq_dist = (1.0 / max_lev) * (liq_f / 100.0) * 100  # % price move to liq
            fee_roi = 0.1 * max_lev  # fee as % of margin

            sl_price_pct = liq_dist * sl_pct / 100
            sl_roi = sl_price_pct / 100 * max_lev * 100
            em_price_pct = liq_dist * em_pct / 100
            tp_price_pct = liq_dist * tp_mult
            tp_roi = tp_price_pct / 100 * max_lev * 100
            trail_act_roi = fee_roi * trail_act
            trail_dist_roi = fee_roi * trail_dist

            theo_liq = (1.0 / max_lev) * 100
            lines = [
                f"  {max_lev}x Kaldirac Hesaplamalari (liq_factor=%{liq_f}):",
                f"  Teorik liq:              %{theo_liq:.2f} geri gelme",
                f"  Pratik liq:              %{liq_dist:.2f} fiyat hareketi",
                f"  Fee (round-trip):        %{fee_roi:.1f} ROI (marjinin yuzde kaci)",
                f"  Fee breakeven:           %{fee_roi/max_lev:.3f} fiyat hareketi",
                f"  SL:                      %{sl_price_pct:.2f} fiyat = %{sl_roi:.0f} ROI kayip",
                f"  Emergency:               %{em_price_pct:.2f} fiyat = son savunma",
            ]
            if self._cb_vars.get("tp_enabled", ctk.BooleanVar(value=True)).get():
                lines.append(
                    f"  TP:                      %{tp_price_pct:.2f} fiyat = %{tp_roi:.0f} ROI kar")
            else:
                lines.append("  TP:                      KAPALI (trailing yonetir)")
            # Trailing info based on mode
            t_mode = self._trailing_mode_var.get()
            if t_mode == "atr":
                try:
                    atr_act = float(self._entries.get("trailing_atr_activate_mult",
                                    type("", (), {"get": lambda s: "4.0"})()).get() or 4.0)
                    atr_dist = float(self._entries.get("trailing_atr_distance_mult",
                                     type("", (), {"get": lambda s: "1.0"})()).get() or 1.0)
                except (ValueError, AttributeError):
                    atr_act, atr_dist = 4.0, 1.0
                # ATR ref at 100x: SL/2 = 0.175%
                atr_ref = sl_price_pct / 2  # half of SL = 1 ATR reference
                act_pct = atr_ref * atr_act
                dist_pct = atr_ref * atr_dist
                act_roi = act_pct / 100 * max_lev * 100
                dist_roi = dist_pct / 100 * max_lev * 100
                lines.extend([
                    f"  Trailing modu:           ATR bazli",
                    f"  Trailing aktivasyon:     {atr_act}x ATR = %{act_pct:.3f} fiyat = %{act_roi:.0f} ROI",
                    f"  Trailing mesafe:         {atr_dist}x ATR = %{dist_pct:.3f} fiyat = %{dist_roi:.0f} ROI geri",
                    f"  Min cikis:               %{act_roi - dist_roi:.0f} ROI garanti",
                ])
            else:
                try:
                    direct_act = float(self._entries.get("trailing_activate_roi",
                                       type("", (), {"get": lambda s: "0"})()).get() or 0)
                    direct_dist = float(self._entries.get("trailing_distance_roi",
                                        type("", (), {"get": lambda s: "0"})()).get() or 0)
                except (ValueError, AttributeError):
                    direct_act, direct_dist = 0, 0

                if direct_act > 0 and direct_dist > 0:
                    lines.extend([
                        f"  Trailing modu:           ROI bazli (sabit)",
                        f"  Trailing aktivasyon:     %{direct_act:.0f} ROI",
                        f"  Trailing mesafe:         %{direct_dist:.0f} ROI geri cekilme",
                        f"  Min cikis:               %{direct_act - direct_dist:.0f} ROI garanti",
                    ])
                else:
                    lines.extend([
                        f"  Trailing modu:           ROI bazli (fee carpani)",
                        f"  Trailing aktivasyon:     %{trail_act_roi:.1f} ROI ({trail_act}x fee)",
                        f"  Trailing mesafe:         %{trail_dist_roi:.1f} ROI ({trail_dist}x fee)",
                    ])

            self._info_label.configure(text="\n".join(lines))
        except (ValueError, ZeroDivisionError):
            self._info_label.configure(text="  (Hesaplama icin gecerli degerler girin)")

    def _show_help(self, title: str, text: str) -> None:
        """Show a help popup with detailed explanation."""
        popup = ctk.CTkToplevel(self)
        popup.title(f"Yardim: {title}")
        popup.geometry("520x400")
        popup.resizable(False, False)
        popup.attributes("-topmost", True)
        popup.grab_set()

        # Title
        ctk.CTkLabel(popup, text=title,
                     font=ctk.CTkFont(size=15, weight="bold")).pack(
            padx=15, pady=(15, 5), anchor="w")

        # Content (scrollable)
        scroll = ctk.CTkScrollableFrame(popup, fg_color="#1a1a2e")
        scroll.pack(fill="both", expand=True, padx=15, pady=5)
        ctk.CTkLabel(scroll, text=text, justify="left", wraplength=470,
                     font=ctk.CTkFont(size=12, family="Consolas"),
                     text_color="#CCDDCC").pack(padx=10, pady=10, anchor="w")

        # Close button
        ctk.CTkButton(popup, text="Kapat", width=100,
                      command=popup.destroy).pack(pady=(5, 15))

    def _show_feedback(self, msg: str, color: str = "white") -> None:
        self._feedback.configure(text=msg, text_color=color)
        self.after(5000, lambda: self._feedback.configure(text=""))

    # ════════════════════════════════════════
    # TEMPLATE SYSTEM
    # ════════════════════════════════════════

    def _collect_current_values(self) -> dict:
        """Collect all current settings as a dict."""
        vals = {}
        for key, entry in self._entries.items():
            entry.configure(state="normal")
            raw = entry.get().strip()
            if self._mode_var.get() == "standard":
                entry.configure(state="disabled")
            if not raw:
                continue
            try:
                vals[key] = float(raw) if "." in raw else int(raw)
            except ValueError:
                vals[key] = raw
        for key, var in self._cb_vars.items():
            vals[key] = var.get()
        vals["kline_interval"] = self._kline_var.get()
        vals["min_timeframe"] = self._min_tf_var.get()
        vals["tp_exit_mode"] = self._tp_mode_var.get()
        vals["trailing_mode"] = self._trailing_mode_var.get()
        vals["direction_balance_ratio"] = self._ratio_var.get()
        return vals

    def _apply_values(self, vals: dict) -> None:
        """Apply a values dict to all fields."""
        for key, val in vals.items():
            if key in self._entries:
                entry = self._entries[key]
                entry.configure(state="normal")
                entry.delete(0, "end")
                entry.insert(0, str(val))
            elif key in self._cb_vars:
                self._cb_vars[key].set(val)
            elif key == "kline_interval":
                self._kline_var.set(val)
            elif key == "min_timeframe":
                self._min_tf_var.set(val)
            elif key == "tp_exit_mode":
                self._tp_mode_var.set(val)
            elif key == "trailing_mode":
                self._trailing_mode_var.set(val)
            elif key == "direction_balance_ratio":
                self._ratio_var.set(val)
        self._update_info()

    def _refresh_template_list(self) -> None:
        """Refresh the template dropdown with saved templates."""
        templates = _load_templates()
        names = list(templates.keys())
        if names:
            self._tmpl_menu.configure(values=names)
            if self._tmpl_var.get() not in names:
                self._tmpl_var.set(names[0])
        else:
            self._tmpl_menu.configure(values=["(sablon yok)"])
            self._tmpl_var.set("(sablon yok)")

    def _on_template_select(self, name: str) -> None:
        pass  # just updates the variable

    def _save_template(self) -> None:
        """Save current settings as a named template."""
        name = simpledialog.askstring(
            "Sablon Kaydet",
            "Sablon adi girin:",
            parent=self,
        )
        if not name or not name.strip():
            return
        name = name.strip()

        templates = _load_templates()
        if name in templates:
            overwrite = messagebox.askyesno(
                "Sablon Mevcut",
                f"'{name}' zaten var. Uzerine yazilsin mi?",
            )
            if not overwrite:
                return

        templates[name] = self._collect_current_values()
        _save_templates(templates)
        self._refresh_template_list()
        self._tmpl_var.set(name)
        self._show_feedback(f"Sablon '{name}' kaydedildi!", "#FF9800")

    def _load_template(self) -> None:
        """Load selected template into fields."""
        name = self._tmpl_var.get()
        templates = _load_templates()
        if name not in templates:
            self._show_feedback("Gecerli bir sablon secin", "#FF1744")
            return

        # Switch to manuel mode
        self._mode_var.set("manuel")
        self._on_mode_change("manuel")

        self._apply_values(templates[name])
        self._show_feedback(f"Sablon '{name}' yuklendi!", "#2196F3")

    def _delete_template(self) -> None:
        """Delete selected template."""
        name = self._tmpl_var.get()
        templates = _load_templates()
        if name not in templates:
            self._show_feedback("Silinecek sablon yok", "#FF1744")
            return

        confirm = messagebox.askyesno(
            "Sablon Sil",
            f"'{name}' sablonu silinsin mi?",
        )
        if not confirm:
            return

        del templates[name]
        _save_templates(templates)
        self._refresh_template_list()
        self._show_feedback(f"Sablon '{name}' silindi", "#FF1744")
