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
            "wall_min_tf_ratio": 0.3, "depth_min_tf_ratio": 2.0, "thin_book_seconds": 3.0,
            "scan_interval_seconds": 30, "kline_interval": "1m", "kline_limit": 200,
            "min_timeframe": "5m",
            # Leverage
            "min_leverage": 10, "max_leverage": 25,
            "max_positions": 2, "portfolio_percent": 30, "portfolio_divider": 0,
            "portfolio_min_wallet": 12, "portfolio_fixed_margin": 1.0, "portfolio_micro_divider": 4,
            "order_verify_interval": 60, "order_verify_max_orders": 2,
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
            "market_fallback_on_limit_timeout": False, "market_fallback_max_drift_atr": 1.5,
            "partial_tp_enabled": False, "partial_tp_atr_mult": 3.0, "partial_tp_close_pct": 50,
            "btc_correlation_enabled": False, "btc_max_portfolio_beta": 2.5,
            # Mean Reversion: konservatif kapali
            "mean_reversion_enabled": False,
            "mr_max_adx": 18, "mr_max_positions": 2, "mr_min_score": 65,
            "mr_rsi_oversold": 30, "mr_rsi_overbought": 70,
            "mr_sl_atr_mult": 1.5, "mr_bb_proximity_pct": 20.0,
            "mr_volume_exhaustion_max": 0.8, "mr_min_bb_range_fee_mult": 3.0,
            "mr_time_limit_minutes": 240,
            "mr_breakout_to_trend": True, "mr_stop_flip_enabled": True,
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
            "wall_min_tf_ratio": 0.5, "depth_min_tf_ratio": 3.0, "thin_book_seconds": 5.0,
            "scan_interval_seconds": 30, "kline_interval": "1m", "kline_limit": 200,
            "min_timeframe": "5m",
            "min_leverage": 25, "max_leverage": 50,
            "max_positions": 4, "portfolio_percent": 25, "portfolio_divider": 0,
            "portfolio_min_wallet": 12, "portfolio_fixed_margin": 1.0, "portfolio_micro_divider": 4,
            "order_verify_interval": 60, "order_verify_max_orders": 2,
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
            "market_fallback_on_limit_timeout": False, "market_fallback_max_drift_atr": 1.5,
            "partial_tp_enabled": False, "partial_tp_atr_mult": 3.0, "partial_tp_close_pct": 50,
            "btc_correlation_enabled": False, "btc_max_portfolio_beta": 2.5,
            # Mean Reversion: dengeli kapali
            "mean_reversion_enabled": False,
            "mr_max_adx": 18, "mr_max_positions": 2, "mr_min_score": 65,
            "mr_rsi_oversold": 30, "mr_rsi_overbought": 70,
            "mr_sl_atr_mult": 1.5, "mr_bb_proximity_pct": 20.0,
            "mr_volume_exhaustion_max": 0.8, "mr_min_bb_range_fee_mult": 3.0,
            "mr_time_limit_minutes": 240,
            "mr_breakout_to_trend": True, "mr_stop_flip_enabled": True,
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
            "wall_min_tf_ratio": 0.5, "depth_min_tf_ratio": 3.0, "thin_book_seconds": 5.0,
            "scan_interval_seconds": 30, "kline_interval": "1m", "kline_limit": 200,
            "min_timeframe": "3m",
            "min_leverage": 50, "max_leverage": 100,
            "max_positions": 6, "portfolio_percent": 25, "portfolio_divider": 0,
            "portfolio_min_wallet": 12, "portfolio_fixed_margin": 1.0, "portfolio_micro_divider": 4,
            "order_verify_interval": 60, "order_verify_max_orders": 2,
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
            "market_fallback_on_limit_timeout": False, "market_fallback_max_drift_atr": 1.5,
            "partial_tp_enabled": False, "partial_tp_atr_mult": 3.0, "partial_tp_close_pct": 50,
            "btc_correlation_enabled": False, "btc_max_portfolio_beta": 2.5,
            # Mean Reversion: agresif kapali
            "mean_reversion_enabled": False,
            "mr_max_adx": 18, "mr_max_positions": 2, "mr_min_score": 65,
            "mr_rsi_oversold": 30, "mr_rsi_overbought": 70,
            "mr_sl_atr_mult": 1.5, "mr_bb_proximity_pct": 20.0,
            "mr_volume_exhaustion_max": 0.8, "mr_min_bb_range_fee_mult": 3.0,
            "mr_time_limit_minutes": 240,
            "mr_breakout_to_trend": True, "mr_stop_flip_enabled": True,
        },
    },
    "emre_ortalama": {
        "name": "Emre Ortalama",
        "desc": "Max kaldirac, 1/12 portfoy, ATR trailing (4x/1x), karda sinyal cikis",
        "color": "#9C27B0",
        "values": {
            # Entry: kaliteli sinyal, guclu confluence gerektir
            "min_buy_score": 70, "min_confluence": 6.5, "min_adx": 18,
            "max_rsi_long": 62, "min_rsi_short": 38,
            "macd_filter": True, "volume_filter": True, "volatile_filter": False,
            "wall_min_tf_ratio": 0.5, "depth_min_tf_ratio": 3.0, "thin_book_seconds": 5.0,
            "scan_interval_seconds": 30, "kline_interval": "5m", "kline_limit": 200,
            "min_timeframe": "5m",
            # Kaldirac: max mumkun (20x bile olsa ac)
            "min_leverage": 1, "max_leverage": 20,
            # Pozisyon: 4 cephede, 1/12 portfoy
            "max_positions": 4, "portfolio_percent": 8, "portfolio_divider": 12,
            "portfolio_min_wallet": 12, "portfolio_fixed_margin": 1.0, "portfolio_micro_divider": 4,
            "order_verify_interval": 60, "order_verify_max_orders": 2,
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
            # Sinyal: sadece kârda + ters pozisyon açılacak güçte sinyal
            "signal_exit_enabled": True, "signal_exit_threshold": 4.0,
            "signal_min_hold_seconds": 60, "signal_only_in_profit": False,
            "signal_deep_exit_threshold": 8.0,
            "divergence_exit_enabled": False,
            # Zaman: 8 saat, trailing aktifse uzat
            "time_limit_enabled": True, "time_limit_minutes": 480,
            "time_limit_extend_trailing": True, "time_limit_extend_breakeven": True,
            "cooldown_seconds": 60,
            "loss_cooldown_seconds": 3600,
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
            "market_fallback_on_limit_timeout": True, "market_fallback_max_drift_atr": 1.5,
            "partial_tp_enabled": False, "partial_tp_atr_mult": 3.0, "partial_tp_close_pct": 50,
            "btc_correlation_enabled": False, "btc_max_portfolio_beta": 2.5,
            # ADX Rejim Sistemi
            "adx_regime_enabled": True,
            "adx_regime_no_trade": 18,
            "adx_regime_strong_trend": 25,
            "adx_regime_mtf_required": True,
            "adx_regime_ranging_entry_atr": 2.0,
            "adx_regime_ranging_sl_atr": 2.0,
            "adx_regime_ranging_tp_atr": 3.0,
            "adx_regime_ranging_trail_activate_atr": 4.0,
            "adx_regime_ranging_trail_callback_atr": 1.0,
            "adx_regime_weak_entry_atr": 1.0,
            "adx_regime_weak_sl_atr": 2.0,
            "adx_regime_weak_trail_activate_atr": 4.0,
            "adx_regime_weak_trail_callback_atr": 1.0,
            "adx_regime_strong_sl_atr": 2.0,
            "adx_regime_strong_trail_activate_atr": 4.0,
            "adx_regime_strong_trail_callback_atr": 1.0,
            # Mean Reversion
            "mean_reversion_enabled": True,
            "mr_max_adx": 18, "mr_max_positions": 2, "mr_min_score": 65,
            "mr_rsi_oversold": 30, "mr_rsi_overbought": 70,
            "mr_sl_atr_mult": 1.5, "mr_bb_proximity_pct": 20.0,
            "mr_volume_exhaustion_max": 0.8, "mr_min_bb_range_fee_mult": 3.0,
            "mr_time_limit_minutes": 240,
            "mr_breakout_to_trend": True, "mr_stop_flip_enabled": True,
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
        # ── Top: Mode selector + Save + Presets + Templates ──
        top = ctk.CTkFrame(self, fg_color="#1a1a2e")
        top.pack(fill="x", padx=5, pady=(5, 2))

        ctk.CTkLabel(top, text="Strateji Ayarlari",
                     font=ctk.CTkFont(size=16, weight="bold")).pack(side="left", padx=10, pady=6)

        self._mode_var = ctk.StringVar(
            value=self.controller.config.get("strategy.mode", "standard"))
        self._mode_seg = ctk.CTkSegmentedButton(
            top, values=["standard", "manuel"],
            variable=self._mode_var,
            command=self._on_mode_change,
            font=ctk.CTkFont(weight="bold"),
        )
        self._mode_seg.pack(side="left", padx=15, pady=6)

        # Hint label (right-most, pack first so it anchors right)
        ctk.CTkLabel(
            top, text="Degisiklikler KAYDET ile gecerli olur",
            text_color="#FF9800", font=ctk.CTkFont(size=10, slant="italic"),
        ).pack(side="right", padx=8, pady=6)

        # Save button — always visible (right-anchored so it never gets clipped)
        self._save_btn = ctk.CTkButton(
            top, text="KAYDET", width=120, height=32,
            fg_color="#00C853", hover_color="#00A846",
            font=ctk.CTkFont(size=13, weight="bold"),
            command=self._save)
        self._save_btn.pack(side="right", padx=(5, 5), pady=6)

        ctk.CTkButton(
            top, text="Sifirla", width=80, height=28,
            fg_color="gray30", hover_color="gray40",
            font=ctk.CTkFont(size=11),
            command=self._reset_to_default).pack(side="right", padx=3, pady=6)

        self._feedback = ctk.CTkLabel(top, text="", height=20,
                                      font=ctk.CTkFont(size=12, weight="bold"))
        self._feedback.pack(side="right", padx=10, pady=6)

        # ── Preset + Template bar ──
        bar2 = ctk.CTkFrame(self, fg_color="transparent")
        bar2.pack(fill="x", padx=5, pady=(2, 2))

        # Presets
        self._preset_frame = ctk.CTkFrame(bar2, fg_color="transparent")
        self._preset_frame.pack(side="left")
        ctk.CTkLabel(self._preset_frame, text="Hazir:",
                     font=ctk.CTkFont(size=11, weight="bold")).pack(side="left", padx=(5, 3))

        current_preset = self.controller.config.get("strategy.preset", "")
        self._preset_var = ctk.StringVar(value=current_preset)

        for key, preset in PRESETS.items():
            btn = ctk.CTkButton(
                self._preset_frame, text=preset["name"], width=100, height=26,
                fg_color=preset["color"], hover_color=preset["color"],
                command=lambda k=key: self._apply_preset(k),
                font=ctk.CTkFont(size=11, weight="bold"),
            )
            btn.pack(side="left", padx=2, pady=2)

        self._preset_desc = ctk.CTkLabel(
            self._preset_frame, text="", text_color="gray60",
            font=ctk.CTkFont(size=10))
        self._preset_desc.pack(side="left", padx=8)

        # Templates (right side)
        self._tmpl_frame = ctk.CTkFrame(bar2, fg_color="transparent")
        self._tmpl_frame.pack(side="right")
        ctk.CTkLabel(self._tmpl_frame, text="Sablon:",
                     font=ctk.CTkFont(size=11, weight="bold")).pack(side="left", padx=(5, 3))
        self._tmpl_var = ctk.StringVar(value="")
        self._tmpl_menu = ctk.CTkOptionMenu(
            self._tmpl_frame, variable=self._tmpl_var,
            values=["(sablon sec)"], width=150, height=26,
            command=self._on_template_select,
        )
        self._tmpl_menu.pack(side="left", padx=2)
        ctk.CTkButton(self._tmpl_frame, text="Yukle", width=55, height=26,
                      fg_color="#2196F3", hover_color="#1976D2",
                      font=ctk.CTkFont(size=10),
                      command=self._load_template).pack(side="left", padx=2)
        ctk.CTkButton(self._tmpl_frame, text="Kaydet", width=60, height=26,
                      fg_color="#FF9800", hover_color="#F57C00",
                      font=ctk.CTkFont(size=10),
                      command=self._save_template).pack(side="left", padx=2)
        ctk.CTkButton(self._tmpl_frame, text="Sil", width=40, height=26,
                      fg_color="#FF1744", hover_color="#D50000",
                      font=ctk.CTkFont(size=10),
                      command=self._delete_template).pack(side="left", padx=2)
        self._refresh_template_list()

        # ══════════════════════════════════════════════════════
        # 3-COLUMN SCROLLABLE LAYOUT
        # ══════════════════════════════════════════════════════
        self._scroll = ctk.CTkScrollableFrame(self)
        self._scroll.pack(fill="both", expand=True, padx=5, pady=(2, 2))

        # 3 columns inside scroll
        col_container = ctk.CTkFrame(self._scroll, fg_color="transparent")
        col_container.pack(fill="both", expand=True)
        col_container.columnconfigure(0, weight=1)
        col_container.columnconfigure(1, weight=1)
        col_container.columnconfigure(2, weight=1)

        c1 = ctk.CTkFrame(col_container, fg_color="transparent")
        c1.grid(row=0, column=0, sticky="nsew", padx=3, pady=0)
        c2 = ctk.CTkFrame(col_container, fg_color="transparent")
        c2.grid(row=0, column=1, sticky="nsew", padx=3, pady=0)
        c3 = ctk.CTkFrame(col_container, fg_color="transparent")
        c3.grid(row=0, column=2, sticky="nsew", padx=3, pady=0)

        # Alias for old code compat
        s = c1

        # ════════════════ COLUMN 1: Giris + Emir + Kaldirac ════════════════
        g = self._section(c1, "Giris Ayarlari (Entry)")
        self._field(g, "min_buy_score", "Min Alim Skoru", "70",
                    tip="Kompozit skor esigi (0-100). 70=kaliteli, 80+=cok secici",
                    help_text=(
                        "KOMPOZIT SKOR (0-100)\n"
                        "─────────────────────\n"
                        "Her coin icin 5 kategoride skor hesaplanir:\n\n"
                        "  Confluence   %35  Indikator uyumu\n"
                        "  Rejim        %20  Piyasa durumu uyumu\n"
                        "  Hacim        %15  OBV, CMF, MFI\n"
                        "  Trend        %15  ADX, MACD, Supertrend\n"
                        "  Risk         %15  ATR uygunlugu, divergence\n\n"
                        "55 = orta kaliteli (cok fazla zarar)\n"
                        "70 = kaliteli firsatlar (onerilen)\n"
                        "80+ = cok secici, az islem"))
        self._field(g,"min_confluence", "Min Confluence", "6.5",
                    tip="Kac indikator uyumlu olmali. 4.0=gevsek, 6.5=standart, 8.0+=siki",
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
        self._field(g,"min_adx", "Min ADX", "18",
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

        row_rsi = ctk.CTkFrame(g, fg_color="transparent")
        row_rsi.pack(fill="x", padx=8, pady=1)
        ctk.CTkLabel(row_rsi, text="RSI Araligi:", width=180, anchor="w",
                     font=ctk.CTkFont(size=11)).pack(side="left")
        self._entries["max_rsi_long"] = e1 = ctk.CTkEntry(row_rsi, width=55, font=ctk.CTkFont(size=11))
        e1.pack(side="left", padx=2)
        self._all_widgets.append((e1, "entry"))
        ctk.CTkLabel(row_rsi, text="(Long max)").pack(side="left", padx=(0, 10))
        self._entries["min_rsi_short"] = e2 = ctk.CTkEntry(row_rsi, width=55, font=ctk.CTkFont(size=11))
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

        self._checkbox(g,"macd_filter", "MACD Histogram Filtresi (yon uyumu zorunlu)",
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
        self._checkbox(g,"volume_filter", "Hacim Onay Filtresi (OBV/CMF)",
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
        self._checkbox(g,"volatile_filter", "Volatile Rejim Filtresi (volatilde islem acma)",
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

        self._field(g,"wall_min_tf_ratio", "Duvar Kalinligi (TF orani)", "0.5",
                    help_text=(
                        "DUVAR KALINLIGI FILTRESI\n"
                        "────────────────────────\n"
                        "Order book'taki en buyuk blok (duvar)\n"
                        "ne kadar guclu? Hacme ve timeframe'e\n"
                        "gore oransal olarak degerlendirilir.\n\n"
                        "Hesaplama:\n"
                        "  duvar_saniye = duvar_usdt / (hacim24h / 86400)\n"
                        "  oran = duvar_saniye / timeframe_saniye\n\n"
                        "Ornek (5m TF, oran=0.5):\n"
                        "  Duvar < 150 sn → kagit, yoksay\n"
                        "  Duvar >= 150 sn → gercek, engelle\n\n"
                        "BTCUSDT'de 2 sn'lik duvar yoksayilir,\n"
                        "DOTUSDT'de 394 sn'lik duvar engellenir.\n\n"
                        "  0.3: Gevsek (az engelleme)\n"
                        "  0.5: Varsayilan\n"
                        "  1.0: Siki (1 mum dayanamayan engeller)\n"
                        "  0: Duvar filtresi kapali"))
        self._field(g,"depth_min_tf_ratio", "Toplam Derinlik (TF orani)", "3.0",
                    help_text=(
                        "TOPLAM DERINLIK FILTRESI\n"
                        "────────────────────────\n"
                        "Tek blok degil, tum order book\n"
                        "kademelerinin toplam buyuklugu.\n\n"
                        "Hesaplama:\n"
                        "  toplam_sn = toplam_usdt / (hacim24h / 86400)\n"
                        "  oran = toplam_sn / timeframe_saniye\n\n"
                        "Ornek (5m TF, oran=3.0):\n"
                        "  Toplam < 900 sn (15dk) → gecebilir\n"
                        "  Toplam >= 900 sn → cok kalin, engelle\n\n"
                        "  2.0: Gevsek\n"
                        "  3.0: Varsayilan\n"
                        "  5.0: Siki\n"
                        "  0: Toplam derinlik filtresi kapali"))
        self._field(g,"thin_book_seconds", "Ince Book Esigi (saniye)", "5.0",
                    help_text=(
                        "INCE BOOK (THIN BOOK) FILTRESI\n"
                        "──────────────────────────────\n"
                        "Order book'taki tum emirlerin toplami\n"
                        "kac saniyede tuketilebilir?\n\n"
                        "Hesaplama:\n"
                        "  book_saniye = toplam_book_usdt /\n"
                        "                (gunluk_hacim / 86400)\n\n"
                        "Ornek (esik=5 saniye):\n"
                        "  BTCUSDT: 1.6M$ book / 68K$/s = 23s → OK\n"
                        "  XANUSDT: 27K$ book / 5.2K$/s = 5s → SINIR\n"
                        "  APRUSDT: 5.7K$ book / 1.7K$/s = 3s → INCE\n\n"
                        "Ince book'ta fiyat kayar, SL bosa tetiklenir.\n\n"
                        "  3: Gevsek (sadece cok siglari ele)\n"
                        "  5: Varsayilan\n"
                        "  10: Siki (daha derin book iste)"))

        self._field(g,"scan_interval_seconds", "Tarama Araligi (sn)", "30",
                    tip="Her tarama arasinda kac saniye beklensin",
                    help_text=(
                        "TARAMA ARALIGI\n"
                        "──────────────\n"
                        "Scanner her dongu sonunda bu kadar\n"
                        "saniye bekler. Kisa aralik = daha hizli\n"
                        "tepki ama daha fazla API kullanimi.\n\n"
                        "  15: Hizli (agresif, rate limit riski)\n"
                        "  30: Standart (onerilen)\n"
                        "  60: Yavas (API dostu)\n\n"
                        "Not: Tarama suresi ~30-40sn surer.\n"
                        "30sn aralik = ~60-70sn'de bir yeni tarama."))

        row_kline = ctk.CTkFrame(g, fg_color="transparent")
        row_kline.pack(fill="x", padx=8, pady=1)
        ctk.CTkLabel(row_kline, text="Mum Araligi:", width=180, anchor="w").pack(side="left")
        self._kline_var = ctk.StringVar(value="1m")
        kline_menu = ctk.CTkOptionMenu(row_kline, variable=self._kline_var,
                                        values=["1m", "3m", "5m", "15m", "30m",
                                                "1h", "2h", "4h", "6h", "8h", "12h"],
                                        width=100)
        kline_menu.pack(side="left", padx=5)
        self._all_widgets.append((kline_menu, "menu"))

        # Min timeframe dropdown
        row_min_tf = ctk.CTkFrame(g, fg_color="transparent")
        row_min_tf.pack(fill="x", padx=8, pady=1)
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

        self._field(g,"kline_limit", "Mum Sayisi", "200",
                    tip="Indikator hesabi icin kac mum cekilsin",
                    help_text=(
                        "MUM SAYISI (Kline Limit)\n"
                        "────────────────────────\n"
                        "Her coin icin API'den cekilecek mum adedi.\n"
                        "Indikatörler bu mumlar uzerinde hesaplanir.\n\n"
                        "  100: Hizli ama kisa gecmis\n"
                        "  200: Standart (onerilen)\n"
                        "  500: Derin analiz (yavas)\n\n"
                        "SMA200 gibi uzun periyot indikatorler\n"
                        "icin en az 200 mum gereklidir.\n"
                        "Daha az mum = eksik indikator verisi."))

        # ──────────────── LIMIT EMIR (PAZARLIKLI GIRIS) ────────────────
        g = self._section(s,"Emir Tipi (Limit / Market)")

        # Checkbox: Pazarlikli fiyat kullan
        self._cb_vars["limit_entry_enabled"] = ctk.BooleanVar(value=False)
        row_lim = ctk.CTkFrame(g, fg_color="transparent")
        row_lim.pack(fill="x", padx=8, pady=1)
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

        self._field(g,"limit_atr_offset", "Limit ATR Ofseti", "0.5",
                    tip="Kac ATR asagiya/yukariya limit emir konur. 0.25=yakin, 0.5=orta, 1.0=uzak",
                    help_text=(
                        "LIMIT ATR OFSETI\n"
                        "─────────────────\n"
                        "Limit emir fiyati = piyasa +/- (N x ATR)\n\n"
                        "LONG: piyasa fiyatinin N ATR ALTINA\n"
                        "SHORT: piyasa fiyatinin N ATR USTUNE\n\n"
                        "  0.25: Yakin (hizli dolar, az tasarruf)\n"
                        "  0.50: Orta (onerilen)\n"
                        "  1.00: Uzak (cok tasarruf, az dolar)\n\n"
                        "ORNEK (ATR=0.50, fiyat=100):\n"
                        "  0.5 offset → LONG limit: 99.75\n"
                        "  1.0 offset → LONG limit: 99.50"))
        self._field(g,"limit_timeout_seconds", "Limit Timeout (sn)", "300",
                    tip="Dolmayan limit emir kac saniye sonra iptal edilir. 300=5dk",
                    help_text=(
                        "LIMIT EMIR TIMEOUT\n"
                        "──────────────────\n"
                        "Limit emir bu sure icinde dolmazsa iptal\n"
                        "edilir. Timeout sonrasi market_fallback\n"
                        "aciksa market emre donebilir.\n\n"
                        "  120: 2dk (sabrisiz, cok iptal)\n"
                        "  300: 5dk (standart, onerilen)\n"
                        "  600: 10dk (sabir, firsat kacabilir)\n\n"
                        "Pending limit'ler pozisyon slotu kapar.\n"
                        "5dk beklerken baska coin acilamaz."))
        self._cb_vars["limit_recheck_signal"] = ctk.BooleanVar(value=True)
        row_rchk = ctk.CTkFrame(g, fg_color="transparent")
        row_rchk.pack(fill="x", padx=8, pady=1)
        cb_rchk = ctk.CTkCheckBox(row_rchk, text="Dolunca sinyal tekrar kontrol et",
                                   variable=self._cb_vars["limit_recheck_signal"])
        cb_rchk.pack(side="left")
        self._all_widgets.append((cb_rchk, "checkbox"))

        self._checkbox(g,"market_fallback_on_limit_timeout",
                       "Limit Timeout → Market Fallback",
                       default=False,
                       help_text=(
                           "LIMIT TIMEOUT MARKET FALLBACK\n"
                           "─────────────────────────────\n"
                           "Limit emir suresi dolunca otomatik\n"
                           "olarak market emre doner.\n\n"
                           "Guvenlik: Timeout'ta sinyal TAZE olarak\n"
                           "  tekrar hesaplanir (eski skor kullanilmaz).\n"
                           "  Fiyat sapma kontrolu de yapilir\n"
                           "  (max drift ATR ayari ile).\n\n"
                           "Aktif: Timeout sonrasi sinyal hala gecerli\n"
                           "  VE fiyat fazla kaymamissa market giris\n\n"
                           "Kapali: Timeout sonrasi emir iptal edilir"))

        self._field(g,"market_fallback_max_drift_atr",
                    "Market Fallback Max Sapma (ATR)", "1.5",
                    tip=("Limit timeout sonrasi market fallback icin\n"
                         "fiyat en fazla kac ATR kaymis olabilir.\n"
                         "1.0=siki (yakin fiyat), 1.5=orta, 2.0=gevsek.\n"
                         "Fiyat bu kadar ATR'den fazla kaymissa\n"
                         "market giris yapilmaz (trende gec kalindi)."),
                    help_text=(
                        "MARKET FALLBACK MAX SAPMA\n"
                        "─────────────────────────\n"
                        "Limit emir timeout sonrasi market emre\n"
                        "donulecekse, fiyat limit fiyattan en\n"
                        "fazla bu kadar ATR uzaklasmis olabilir.\n\n"
                        "  1.0: Siki (fiyat yakinsa market gir)\n"
                        "  1.5: Orta (onerilen)\n"
                        "  2.0: Gevsek (genis sapma tolere)\n\n"
                        "Fiyat daha fazla kaymissa market giris\n"
                        "yapilmaz (trende gec kalinmis, SL riski)."))

        # ──────────────── KALDIRAC & POZISYON ────────────────
        g = self._section(s,"Kaldirac & Pozisyon Boyutu")

        row_lev = ctk.CTkFrame(g, fg_color="transparent")
        row_lev.pack(fill="x", padx=8, pady=1)
        ctk.CTkLabel(row_lev, text="Kaldirac Araligi:", width=180, anchor="w").pack(side="left")
        self._entries["min_leverage"] = e3 = ctk.CTkEntry(row_lev, width=55, font=ctk.CTkFont(size=11))
        e3.pack(side="left", padx=2)
        self._all_widgets.append((e3, "entry"))
        ctk.CTkLabel(row_lev, text=" - ").pack(side="left")
        self._entries["max_leverage"] = e4 = ctk.CTkEntry(row_lev, width=55, font=ctk.CTkFont(size=11))
        e4.pack(side="left", padx=2)
        self._all_widgets.append((e4, "entry"))
        ctk.CTkLabel(row_lev, text="x").pack(side="left")

        self._field(g,"max_positions", "Max Esanli Pozisyon", "6",
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
        self._field(g,"portfolio_percent", "Portfoy Yuzdesi (%)", "25",
                    tip="Her pozisyon icin bakiyenin yuzde kaci kullanilsin",
                    help_text=(
                        "PORTFOY YUZDESI\n"
                        "───────────────\n"
                        "portfolio_divider=0 ise bu kullanilir.\n"
                        "Her pozisyon icin bakiyenin yuzde kaci\n"
                        "margin olarak kullanilir.\n\n"
                        "  8:  Cok konservatif (1/12.5)\n"
                        "  15: Konservatif\n"
                        "  25: Dengeli (onerilen)\n"
                        "  50: Agresif\n\n"
                        "portfolio_divider > 0 ise bu ayar\n"
                        "goz ardi edilir (1/N sistemi kullanilir)."))
        self._field(g,"portfolio_divider", "Portfoy Boleni (1/N)", "0",
                    tip="Bakiyenin 1/N'i (ornek: 12 = 1/12). 0=yuzde modu kullan",
                    help_text=(
                        "PORTFOY BOLENI (1/N sistemi)\n"
                        "────────────────────────────\n"
                        "0 = yuzde modu (portfolio_percent kullan)\n"
                        "12 = Emre Ortalama sistemi\n\n"
                        "3 KATMANLI BOYUTLANDIRMA:\n"
                        "  Bakiye >= esik: 1/N (ornek: 1/12)\n"
                        "  Bakiye 4$-esik arasi: sabit margin\n"
                        "  Bakiye < 4$: 1/4 (mikro mod)\n\n"
                        "ORNEK (divider=12, esik=12$):\n"
                        "  20$ → 20/12 = 1.67$ margin\n"
                        "  8$  → 1.00$ sabit margin\n"
                        "  3$  → 3/4  = 0.75$ margin"))
        self._field(g,"portfolio_min_wallet", "Bolen Esigi ($)", "12",
                    tip="Bu tutarin altinda sabit margin kullanilir",
                    help_text=(
                        "PORTFOY BOLEN ESIGI\n"
                        "────────────────────\n"
                        "Toplam portfoy bu tutarin USTUNDE ise\n"
                        "portfolio_divider (1/N) kullanilir.\n\n"
                        "ALTINDA ise sabit margin (1$) kullanilir\n"
                        "ta ki portfoy 4$'in altina dusene kadar.\n\n"
                        "  12 = Standart (onerilen)\n"
                        "  20 = Daha buyuk portfoyler icin\n"
                        "   8 = Daha kucuk portfoyler icin\n\n"
                        "ORNEK (esik=12):\n"
                        "  15$ portfoy → 1/12 = 1.25$ (bolen)\n"
                        "  10$ portfoy → 1.00$ (sabit)\n"
                        "   3$ portfoy → 3/4 = 0.75$ (mikro)"))
        self._field(g,"portfolio_fixed_margin", "Sabit Margin ($)", "1.0",
                    tip="Esik altinda her pozisyon icin sabit margin",
                    help_text=(
                        "SABIT MARGIN TUTARI\n"
                        "────────────────────\n"
                        "Portfoy bolen esiginin altinda ama 4$\n"
                        "ustunde iken her pozisyon bu kadar\n"
                        "margin ile acilir.\n\n"
                        "  1.0 = Standart (onerilen)\n"
                        "  0.5 = Cok kucuk hesaplar icin\n"
                        "  2.0 = Orta hesaplar icin\n\n"
                        "ORNEK (sabit=1$):\n"
                        "  8$ portfoy → 1$ margin (max 8 poz)\n"
                        "  5$ portfoy → 1$ margin (max 5 poz)"))
        self._field(g,"portfolio_micro_divider", "Mikro Bolen (1/N)", "4",
                    tip="4$ altinda portfoyun 1/N'i ile islem acilir",
                    help_text=(
                        "MIKRO PORTFOY BOLENI\n"
                        "─────────────────────\n"
                        "Portfoy 4$'in altina dustugunde\n"
                        "sabit 1$ bile cok fazla olabilir.\n"
                        "Bu durumda portfoyun 1/N'i kullanilir.\n\n"
                        "  4 = 1/4 (onerilen)\n"
                        "  3 = 1/3 (daha agresif)\n"
                        "  5 = 1/5 (daha konservatif)\n\n"
                        "ORNEK (mikro_bolen=4):\n"
                        "  3.0$ → 3.0/4 = 0.75$ margin\n"
                        "  2.0$ → 2.0/4 = 0.50$ margin\n"
                        "  1.0$ → 1.0/4 = 0.25$ margin"))

        # ──────────────── EMIR DOGRULAMA (GUVENLIK) ────────────────
        g = self._section(s, "Emir Dogrulama (Guvenlik)")
        ctk.CTkLabel(g, text="Periyodik kontrol: eksik emir koyar, fazla emir temizler. Trailing guncelleme YAPMAZ.",
                     text_color="gray50", font=ctk.CTkFont(size=10, slant="italic"),
                     wraplength=350).pack(anchor="w", padx=8, pady=(0, 3))
        self._field(g,"order_verify_interval", "Kontrol Araligi (sn)", "60",
                    tip="Kac saniyede bir server emirleri kontrol edilir",
                    help_text=(
                        "EMIR DOGRULAMA ARALIGI\n"
                        "───────────────────────\n"
                        "Sistem her N saniyede bir Binance'deki\n"
                        "emirleri kontrol eder.\n\n"
                        "KONTROL: Her pozisyonda tam olarak\n"
                        "1 SL + 1 trailing var mi?\n\n"
                        "EMIRLER TAMAM ISE: Hicbir sey yapmaz.\n"
                        "Trailing guncelleme YAPMAZ!\n\n"
                        "EKSIK EMIR VARSA: 3 duruma gore koyar:\n"
                        "  A) Zararda → orijinal plan (entry'den)\n"
                        "  B) Karda, tetik altinda → orijinal plan\n"
                        "  C) Karda, tetik ustunde →\n"
                        "     SL = current - NxATR (kar koruma)\n"
                        "     Trail = hemen tetikli, KxATR geri\n\n"
                        "FAZLA EMIR VARSA: Temizle + dogru 2 koy\n\n"
                        "  30 = Sik kontrol (guvenli, API yuku)\n"
                        "  60 = Standart (onerilen)\n"
                        " 120 = Seyrek (az API, gecikme riski)"))
        self._field(g,"order_verify_max_orders", "Max Emir / Pozisyon", "2",
                    tip="Pozisyon basina max emir sayisi (1 SL + 1 trailing = 2)",
                    help_text=(
                        "MAX EMIR SAYISI\n"
                        "────────────────\n"
                        "Her pozisyon icin Binance'de olmasi\n"
                        "gereken maksimum emir sayisi.\n\n"
                        "Normal: 1 STOP_MARKET + 1 TRAILING = 2\n\n"
                        "Bu sayidan fazla emir tespit edilirse\n"
                        "sistem tum emirleri iptal edip dogru\n"
                        "2 emri yeniden koyar (3 durum mantigi).\n\n"
                        "  2 = Standart (1 SL + 1 trailing)\n"
                        "  3 = TP emri de varsa\n\n"
                        "DIKKAT: 2'den asagi indirmeyin!"))

        # ════════════════ COLUMN 2: SL + Trailing + TP + Sinyal + Zaman + Risk ════════════════
        s = c2  # switch to column 2

        # ──────────────── STOP LOSS: ORTAK PARAMETRELER ────────────────
        g = self._section(s, "Stop Loss - Ortak")
        self._field(g, "liq_factor", "Pratik Liq Faktoru (%)", "70",
                    tip="Binance erken likide eder, 70=gercekci",
                    help_text=(
                        "PRATIK LIKIDASYON FAKTORU\n"
                        "─────────────────────────\n"
                        "Binance teorik liq noktasindan ONCE\n"
                        "likide eder (bakim marjini yuzunden).\n\n"
                        "Teorik liq = 1 / kaldirac\n"
                        "Pratik liq = teorik x bu_faktor\n\n"
                        "Ornek (100x):\n"
                        "  Teorik liq = %1.0\n"
                        "  %70 faktor → Pratik = %0.70\n\n"
                        "70 = gercekci (onerilen)\n"
                        "80 = daha rahat ama riskli\n\n"
                        "Bu deger hem Server SL fallback'inde\n"
                        "hem Yazilim SL'de hem de Emergency\n"
                        "hesaplamada kullanilir."))
        self._field(g, "fee_pct", "Fee (round-trip %)", "0.10",
                    tip="Giris+cikis toplam komisyon (taker: 0.10, maker: 0.04)",
                    help_text=(
                        "FEE (KOMISYON) YUZDESI\n"
                        "──────────────────────\n"
                        "Giris + cikis toplam islem komisyonu.\n"
                        "SL hesabinda fee dusulur (fee-aware).\n\n"
                        "BINANCE FUTURES:\n"
                        "  Taker (market): %0.04 × 2 = %0.08\n"
                        "  Maker (limit):  %0.02 × 2 = %0.04\n"
                        "  Varsayilan:     %0.10 (emniyet payı)\n\n"
                        "ORNEK (100x, fee=%0.10):\n"
                        "  Fee ROI = 0.10 × 100 = %10 margin\n"
                        "  Bu kadar ROI sadece fee'ye gider.\n"
                        "  SL bu tutarin USTUNDE ayarlanir.\n\n"
                        "VIP seviyenize gore dusurulabilir:\n"
                        "  VIP0: %0.10 (varsayilan)\n"
                        "  VIP1: %0.08\n"
                        "  BNB indirimli: %0.075"))
        self._field(g, "slippage_mult", "Slippage Carpani (fee x)", "0.5",
                    tip="Tahmini kayma = fee × bu_carpan (0.5=standart, 1.0=kotU likidite)",
                    help_text=(
                        "SLIPPAGE (KAYMA) CARPANI\n"
                        "────────────────────────\n"
                        "Market emirlerde fiyat kayabilir.\n"
                        "Slippage = fee × bu_carpan\n\n"
                        "SL hesabinda fee + slippage toplam\n"
                        "kayiptan dusulur (fee-aware SL).\n\n"
                        "  0.3: Likit coinler (BTC, ETH)\n"
                        "  0.5: Standart (onerilen)\n"
                        "  1.0: Dusuk likidite / haber ani\n"
                        "  0.0: Slippage hesaba katilmaz\n\n"
                        "ORNEK (100x, fee=%0.10, slip=0.5):\n"
                        "  Fee ROI  = %10\n"
                        "  Slip ROI = %10 × 0.5 = %5\n"
                        "  Toplam   = %15 ROI (SL'den dusulur)"))

        # ──────────────── a) SERVER SL (Binance tarafı) ────────────────
        g = self._section(s, "Stop Loss a) Server (Binance)")
        ctk.CTkLabel(g, text="Binance'e STOP_MARKET emri gonderilir. Bot cokse bile korur.",
                     text_color="gray50", font=ctk.CTkFont(size=10, slant="italic"),
                     wraplength=350).pack(anchor="w", padx=8, pady=(0, 3))
        self._field(g, "server_sl_atr_mult", "SL Mesafesi (x ATR)", "2.0",
                    tip="Giris fiyatindan kac ATR uzakta SL olsun",
                    help_text=(
                        "SERVER STOP LOSS (Binance STOP_MARKET)\n"
                        "──────────────────────────────────────\n"
                        "Pozisyon acilir acilmaz Binance'e\n"
                        "STOP_MARKET emri gonderilir.\n\n"
                        "HESAPLAMA:\n"
                        "  LONG:  SL = giris - (ATR × carpan)\n"
                        "  SHORT: SL = giris + (ATR × carpan)\n\n"
                        "ORNEK (BTC $50K, ATR=$150, 2.0x):\n"
                        "  SL = $50,000 - ($150 × 2) = $49,700\n"
                        "  Fiyat $49,700'e duserse Binance\n"
                        "  otomatik MARKET SELL yapar.\n\n"
                        "  1.5 = Siki (erken tetiklenir, whipsaw)\n"
                        "  2.0 = Standart (onerilen)\n"
                        "  3.0 = Gevsek (buyuk kayip ama az tetik)\n\n"
                        "AVANTAJ: Bot cokse, internet kopsa,\n"
                        "PC kapansa bile calismaya devam eder.\n"
                        "Binance sunucusunda yasar.\n\n"
                        "NOT: Her zaman gonderilir, kapatılamaz."))

        # ──────────────── b) SOFTWARE SL (yazılım tarafı) ────────────────
        g = self._section(s, "Stop Loss b) Yazilim (Fee-Aware)")
        ctk.CTkLabel(g, text="Yazilim her 2sn'de fiyat kontrol eder. Fee+slippage dusulerek hesaplanir.",
                     text_color="gray50", font=ctk.CTkFont(size=10, slant="italic"),
                     wraplength=350).pack(anchor="w", padx=8, pady=(0, 3))
        self._checkbox(g, "sl_enabled", "Yazilim SL Aktif",
                      help_text=(
                          "YAZILIM STOP LOSS (fee-aware)\n"
                          "─────────────────────────────\n"
                          "Server SL'den FARKLI bir hesaplama:\n"
                          "  Likidasyon mesafesi bazli,\n"
                          "  fee ve slippage dusulmus.\n\n"
                          "HESAPLAMA:\n"
                          "  liq = (1/leverage) × liq_factor\n"
                          "  raw_sl = liq × sl_liq_percent\n"
                          "  fee_roi = fee × leverage × 100\n"
                          "  slip_roi = fee_roi × slip_carpan\n"
                          "  net_sl = raw_sl - fee_roi - slip_roi\n\n"
                          "Server SL zaten Binance'de koruma\n"
                          "sagladigı icin yazilim SL genellikle\n"
                          "KAPALI tutulur.\n\n"
                          "KULLANIM:\n"
                          "  Kapali: Sadece Server SL (onerilen)\n"
                          "  Acik: Cift katman (ekstra guvenlik)"))
        self._field(g, "sl_liq_percent", "SL Yuzde (liq mesafesi %)", "50",
                    tip="Pratik liq mesafesinin yuzde kacinda SL (50=orta)",
                    help_text=(
                        "YAZILIM SL MESAFESI\n"
                        "───────────────────\n"
                        "Pratik liq mesafesinin yuzde kaci.\n"
                        "Fee ve slippage otomatik dusulur.\n\n"
                        "ORNEK (100x, liq_factor=70):\n"
                        "  Pratik liq = %0.70\n"
                        "  %50 → SL = %0.35 (fee oncesi)\n"
                        "  Fee+slip dusuldukten sonra:\n"
                        "  Net SL = ~%0.20 fiyat hareketi\n\n"
                        "  40 = Siki (erken cikis)\n"
                        "  50 = Standart (onerilen)\n"
                        "  60 = Gevsek (daha fazla kayip)"))

        # ──────────────── c) EMERGENCY (son savunma) ────────────────
        g = self._section(s, "Stop Loss c) Emergency (Son Savunma)")
        ctk.CTkLabel(g, text="SL'lerin arkasindaki son savunma. Liq'e yakin noktada acil kapatir.",
                     text_color="gray50", font=ctk.CTkFont(size=10, slant="italic"),
                     wraplength=350).pack(anchor="w", padx=8, pady=(0, 3))
        self._checkbox(g, "emergency_enabled", "Emergency Close Aktif",
                      help_text=(
                          "EMERGENCY ANTI-LIKIDASYON\n"
                          "─────────────────────────\n"
                          "Server SL ve yazilim SL'nin ARKASINDA\n"
                          "bekleyen son savunma hatti.\n\n"
                          "NE ZAMAN DEVREYE GIRER:\n"
                          "  - Server SL emri iptal edildi/red\n"
                          "  - API hatasi, slippage, ani gap\n"
                          "  - SL fiyati atlanarak gecildi\n\n"
                          "MESAFE ORNEK (100x):\n"
                          "  Server SL:   %0.30 (2×ATR)\n"
                          "  Yazilim SL:  %0.35\n"
                          "  Emergency:   %0.56  ← BURADASIN\n"
                          "  Likidasyon:  %0.70\n\n"
                          "Her zaman ACIK tutulmasi onerilir."))
        self._field(g, "emergency_liq_percent", "Emergency Yuzde (liq %)", "80",
                    tip="Liq mesafesinin %kacinda acil kapat (80=onerilen)",
                    help_text=(
                        "EMERGENCY LIKIDASYON YUZDESI\n"
                        "────────────────────────────\n"
                        "Pratik liq mesafesinin yuzde kacinda\n"
                        "yazilim ACIL kapatma yapar.\n\n"
                        "SL'nin ARKASINDA bekleyen son savunma.\n"
                        "SL tetiklenmediyse (API hatasi, gap)\n"
                        "bu devreye girer.\n\n"
                        "  70: Erken (SL'ye yakin, gereksiz)\n"
                        "  80: Standart (onerilen)\n"
                        "  90: Gec (liq'e cok yakin, riskli)\n\n"
                        "Ornek (20x):\n"
                        "  SL = 2xATR (~%1.4 fiyat)\n"
                        "  Emergency = liq x %80 = %2.8 fiyat\n"
                        "  Liq = %3.5 fiyat"))

        # ──────────────── TRAILING STOP ────────────────
        g = self._section(s,"Iz Suren Stop (Trailing)")
        self._checkbox(g,"trailing_enabled", "Trailing Stop Aktif",
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

        self._checkbox(g,"server_trailing_dynamic_update", "Server Trailing Dinamik Guncelleme",
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
        row_tmode = ctk.CTkFrame(g, fg_color="transparent")
        row_tmode.pack(fill="x", padx=8, pady=1)
        ctk.CTkLabel(row_tmode, text="Trailing Modu:", width=180, anchor="w", font=ctk.CTkFont(size=11)).pack(side="left")
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
        self._field(g,"trailing_atr_activate_mult", "ATR Aktivasyon (x ATR)", "4.0",
                    tip="Kac ATR kar olunca trailing baslasin (3=erken, 4=standart, 7=gec)",
                    help_text=(
                        "TRAILING ATR AKTIVASYON\n"
                        "───────────────────────\n"
                        "Fiyat giristen N x ATR uzaklasinca\n"
                        "trailing stop aktif olur.\n\n"
                        "  3.0: Erken aktivasyon (daha az kar\n"
                        "       garanti ama daha sik tetiklenir)\n"
                        "  4.0: Standart (onerilen)\n"
                        "  7.0: Gec (buyuk karlar ama nadir)\n\n"
                        "Min garanti kar = (aktivasyon - mesafe) x ATR\n"
                        "Ornek: 4/1 = min 3 ATR kar garanti"))
        self._field(g,"trailing_atr_distance_mult", "ATR Mesafe (x ATR)", "1.0",
                    tip="Kac ATR geri cekilince sat (0.5=siki, 1=standart, 2=gevsek)",
                    help_text=(
                        "TRAILING ATR MESAFE (Callback)\n"
                        "──────────────────────────────\n"
                        "Trailing aktif olduktan sonra fiyat\n"
                        "N x ATR geri cekilince pozisyon kapatilir.\n\n"
                        "  0.5: Siki (az geri cekilme tolere edilir)\n"
                        "  1.0: Standart (onerilen)\n"
                        "  2.0: Gevsek (buyuk geri cekilme tolere)\n\n"
                        "Server'a callback_rate olarak gonderilir.\n"
                        "Binance limiti: %0.1 - %5.0"))
        # ROI-based trailing fields
        self._field(g,"trailing_activate_roi", "ROI Aktivasyon (%)", "0",
                    tip="Dogrudan ROI% (ornek: 60 = %60 ROI'de basla). 0=fee carpani kullan",
                    help_text=(
                        "ROI AKTIVASYON\n"
                        "──────────────\n"
                        "ROI modu secildiyse: trailing bu ROI%'de\n"
                        "aktif olur. 0 ise fee carpani kullanilir.\n\n"
                        "  0:  Fee carpani ile otomatik hesapla\n"
                        "  30: %30 ROI'de trailing basla\n"
                        "  60: %60 ROI'de trailing basla\n\n"
                        "ATR modu secildiyse bu deger kullanilmaz."))
        self._field(g,"trailing_distance_roi", "ROI Mesafe (%)", "0",
                    tip="Geri cekilme ROI% (ornek: 10 = %10 geri gelince sat). 0=fee carpani kullan",
                    help_text=(
                        "ROI MESAFE (Callback)\n"
                        "─────────────────────\n"
                        "ROI modu secildiyse: trailing aktif\n"
                        "olduktan sonra ROI bu kadar dusunce sat.\n\n"
                        "  0:  Fee carpani ile otomatik hesapla\n"
                        "  5:  %5 ROI geri cekilmede sat\n"
                        "  10: %10 ROI geri cekilmede sat\n\n"
                        "ATR modu secildiyse bu deger kullanilmaz."))
        self._field(g,"trailing_activate_fee_mult", "Fee Carpani Aktivasyon", "3.0",
                    tip="ROI=0 ise kullanilir. Kac x fee ROI'de trailing baslasin",
                    help_text=(
                        "FEE CARPANI AKTIVASYON\n"
                        "──────────────────────\n"
                        "ROI modu + ROI=0 ise: trailing\n"
                        "fee x N ROI'de aktif olur.\n\n"
                        "  Fee ROI = %0.1 x kaldirac x 100\n"
                        "  20x → fee ROI = %2\n"
                        "  3x fee → %6 ROI'de trailing basla\n\n"
                        "  2.0: Erken (fee'yi 2x karsiladiktan sonra)\n"
                        "  3.0: Standart (onerilen)\n"
                        "  5.0: Gec (buyuk kar icin)"))
        self._field(g,"trailing_distance_fee_mult", "Fee Carpani Mesafe", "2.0",
                    tip="ROI=0 ise kullanilir. Trailing mesafesi",
                    help_text=(
                        "FEE CARPANI MESAFE\n"
                        "──────────────────\n"
                        "Trailing mesafesi = fee ROI x N\n\n"
                        "  20x → fee ROI = %2\n"
                        "  2x fee mesafe → %4 geri cekilmede sat\n\n"
                        "  1.5: Siki (fee kadar geri gelince sat)\n"
                        "  2.0: Standart (onerilen)\n"
                        "  4.0: Gevsek (buyuk dalgalanma tolere)"))

        # ──────────────── KAR HEDEFI ────────────────
        g = self._section(s,"Kar Hedefi (Take Profit)")
        self._checkbox(g,"tp_enabled", "Take Profit Aktif",
                      help_text=(
                          "TAKE PROFIT (Kar Hedefi)\n"
                          "────────────────────────\n"
                          "Aktif: Fiyat hedefe ulasinca pozisyon\n"
                          "  otomatik kapatilir. Binance'e TP emri\n"
                          "  gonderilir.\n\n"
                          "Kapali (onerilen): TP gonderilmez.\n"
                          "  Kar yonetimi trailing stop ve sinyal\n"
                          "  cikisi ile yapilir.\n\n"
                          "Neden kapali tutulmali:\n"
                          "  TP aktifken trailing calismaz!\n"
                          "  Fiyat TP'ye gelince pozisyon kapanir,\n"
                          "  trend devam etse bile kar kesilir.\n"
                          "  Trailing ile trend sonuna kadar kalabilirsiniz."))
        self._field(g,"tp_liq_multiplier", "TP Carpani (liq mesafesi x)", "3.0",
                    tip="Likidasyon mesafesinin kac kati (3.0 = 75x'te %3.4 fiyat hareketi)",
                    help_text=(
                        "TP CARPANI\n"
                        "──────────\n"
                        "TP mesafesi = pratik_liq x bu_carpan\n\n"
                        "Ornek (20x, liq_factor=%70):\n"
                        "  Pratik liq = %3.5\n"
                        "  3x → TP = %10.5 fiyat hareketi\n"
                        "  ROI = %210\n\n"
                        "  2.0: Yakin TP (daha sik kar)\n"
                        "  3.0: Standart\n"
                        "  5.0: Uzak TP (buyuk hedef)"))

        row_tp_mode = ctk.CTkFrame(g, fg_color="transparent")
        row_tp_mode.pack(fill="x", padx=8, pady=1)
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
        g = self._section(s,"Kismi Kar Al (Partial TP)")
        self._checkbox(g,"partial_tp_enabled", "Kismi Kar Al (Partial TP)",
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
        self._field(g,"partial_tp_atr_mult", "Partial TP ATR Mult", "3.0",
                    tip="Kac ATR karda kismi kar al tetiklensin (2=erken, 3=standart)",
                    help_text=(
                        "KISMI KAR AL ATR CARPANI\n"
                        "─────────────────────────\n"
                        "Fiyat giristen N x ATR uzaklasinca\n"
                        "pozisyonun belirli yuzdesi kapatilir.\n\n"
                        "  2.0: Erken kismi kar (daha az kar ama\n"
                        "       riski erken azaltir)\n"
                        "  3.0: Standart (onerilen)\n"
                        "  4.0: Gec (trailing aktivasyonuna yakin)\n\n"
                        "Trailing aktivasyonundan ONCE olmali!\n"
                        "Ornek: partial=2, trailing=4 → ideal"))
        self._field(g,"partial_tp_close_pct", "Kapanacak Oran (%)", "50",
                    tip="Pozisyonun yuzde kaci kapansin (30=az, 50=yari, 70=cogu)",
                    help_text=(
                        "KISMI KAPANIS ORANI\n"
                        "────────────────────\n"
                        "Partial TP tetiklenince pozisyonun\n"
                        "yuzde kaci kapatilir.\n\n"
                        "  30: Az kapat, cogu trailing'e kalsin\n"
                        "  50: Yari yari (onerilen)\n"
                        "  70: Cogu kapat, az trailing'e kalsin\n\n"
                        "Kalan kisim trailing ile devam eder.\n"
                        "Kapatilan kismin kari realize olur."))

        # ──────────────── SINYAL CIKIS ────────────────
        g = self._section(s,"Sinyal Bazli Cikis")
        self._checkbox(g,"signal_exit_enabled", "Sinyal Donusu Cikis (karda + ters sinyal guclu)",
                      help_text=(
                          "SINYAL CIKIS (KARDA + TERS POZISYON)\n"
                          "────────────────────────────────────\n"
                          "3 KOSUL birden saglanmalı:\n\n"
                          "  1. Pozisyon KARDA (fee dahil)\n"
                          "  2. Ters sinyal >= min_buy_score\n"
                          "     (ters pozisyon acilabilir guçte)\n"
                          "  3. Confluence esigi asilmis\n\n"
                          "Zararda sinyal cikisi YAPILMAZ.\n"
                          "Zararda server SL korur.\n\n"
                          "ORNEK:\n"
                          "  LONG pozisyondayiz, karda.\n"
                          "  Sinyal -75 (SHORT, score >= 70)\n"
                          "  → Cik, cunku ters pozisyon acilacak\n"
                          "    kadar guclu donus var.\n\n"
                          "  Sinyal -40 (zayif SHORT)\n"
                          "  → Cikma, sinyal yeterince guclu\n"
                          "    degil (whipsaw olabilir)."))
        self._field(g,"signal_exit_threshold", "Sinyal Esik Degeri", "4.0",
                    tip="Confluence skoru bu degerin altina dusunce sat (ornek: 4.0)",
                    help_text=(
                        "SINYAL CIKIS ESIGI\n"
                        "───────────────────\n"
                        "Karda iken: confluence bu degerin\n"
                        "tersine duserse pozisyon kapatilir.\n\n"
                        "LONG icin: conf <= -4.0 → SAT\n"
                        "SHORT icin: conf >= +4.0 → AL\n\n"
                        "  3.0: Hassas (erken cikar, az kar)\n"
                        "  4.0: Standart (onerilen)\n"
                        "  6.0: Direncli (gec cikar, risk)\n\n"
                        "Ayrica ters skor >= min_buy_score\n"
                        "olmali (ters pozisyon acilacak gucte)."))
        self._field(g,"signal_min_hold_seconds", "Min Bekle (sn)", "30",
                    tip="Pozisyon acildiktan sonra min bekleme suresi",
                    help_text=(
                        "MINIMUM BEKLEME SURESI\n"
                        "──────────────────────\n"
                        "Pozisyon acildiktan sonra bu sure\n"
                        "boyunca sinyal cikisi yapilmaz.\n\n"
                        "Neden gerekli:\n"
                        "  Giris aninda indikatorler gecici olarak\n"
                        "  ters sinyal uretebilir (noise).\n"
                        "  Bu bekleme suresi bunu onler.\n\n"
                        "  30:  Kisa (hizli tepki)\n"
                        "  60:  Orta\n"
                        "  180: Uzun (sinyal stabilizasyonu icin)"))
        self._checkbox(g,"signal_only_in_profit", "Sadece Karda Sinyal Cikisi",
                      help_text=(
                          "SINYAL CIKISI — KAR/ZARAR MODU\n"
                          "───────────────────────────────\n"
                          "Aktif (true): Sinyal cikisi SADECE\n"
                          "  karda (fee dahil) calisir.\n"
                          "  Zararda server SL korur.\n\n"
                          "Kapali (false): Zararda da sinyal\n"
                          "  cikisi yapar, AMA daha yuksek\n"
                          "  esikle (derin reversal esigi).\n"
                          "  Karda: conf >= 4 (normal esik)\n"
                          "  Zararda: conf >= 8 (derin esik)\n\n"
                          "Ornek: LONG pozisyon, zarar var,\n"
                          "  conf = -9 → guclu SHORT sinyali\n"
                          "  → server SL'yi bekleme, hemen cik"))
        self._field(g,"signal_deep_exit_threshold", "Zararda Derin Reversal Esigi", "8.0",
                    tip=("Zararda sinyal cikisi icin gereken\n"
                         "minimum confluence esigi.\n"
                         "Karda normal esik (4) kullanilir,\n"
                         "zararda bu daha yuksek esik (8) gerekir.\n"
                         "signal_only_in_profit=false iken aktif."))
        self._checkbox(g,"divergence_exit_enabled", "Divergence Cikis (bearish divergence'ta sat)",
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
        g = self._section(s,"Zaman Limiti")
        self._checkbox(g,"time_limit_enabled", "Zaman Limiti Aktif",
                      help_text=(
                          "ZAMAN LIMITI\n"
                          "────────────\n"
                          "Pozisyon belirtilen sureden fazla\n"
                          "tutulursa otomatik kapatilir.\n\n"
                          "Neden gerekli:\n"
                          "  Yatay piyasada takilmis pozisyonlar\n"
                          "  funding fee oderler (8 saatte 1).\n"
                          "  Uzun sure hic hareket etmeyen\n"
                          "  pozisyon kaynagi baglar.\n\n"
                          "Uzatma secenekleri ile esnek yonetim."))
        self._field(g,"time_limit_minutes", "Max Tutma (dk)", "480",
                    tip="Pozisyon en fazla kac dakika tutulsun",
                    help_text=(
                        "MAX TUTMA SURESI\n"
                        "─────────────────\n"
                        "Bu sureden sonra pozisyon kapatilir.\n\n"
                        "  60:  1 saat (agresif)\n"
                        "  120: 2 saat\n"
                        "  480: 8 saat (onerilen, 1 funding)\n"
                        "  1440: 24 saat\n\n"
                        "480dk = 1 funding periyodu (Binance\n"
                        "her 8 saatte funding fee keser).\n"
                        "Uzatma secenekleri ile override edilebilir."))
        self._checkbox(g,"time_limit_extend_trailing",
                       "Trailing Aktifse Uzat (trailing varsa zaman limiti iptal)",
                       help_text=(
                           "TRAILING UZATMA\n"
                           "───────────────\n"
                           "Trailing stop aktif olduysa (fiyat\n"
                           "N x ATR ilerledi) zaman limiti iptal.\n\n"
                           "Mantik: Fiyat hedefe gelmis, trend\n"
                           "devam edebilir. Zaman limiti ile\n"
                           "erken kapatmak mantikli degil.\n"
                           "Trailing zaten cikisi yonetir."))
        self._checkbox(g,"time_limit_extend_breakeven",
                       "Breakeven'da Uzat (fee civarindaysa 2x sure ver)",
                       help_text=(
                           "BREAKEVEN UZATMA\n"
                           "─────────────────\n"
                           "Pozisyon fee civarinda karda ise\n"
                           "sure 2 katina uzatilir.\n\n"
                           "Mantik: Fee'yi yeni karsilamis,\n"
                           "biraz daha sure verilirse kara\n"
                           "gecebilir. Hemen kapatmak sadece\n"
                           "fee kaybina neden olur."))

        # ──────────────── RISK ────────────────
        g = self._section(s,"Risk & Bekleme")
        self._field(g,"cooldown_seconds", "Satis Sonrasi Bekleme (sn)", "120",
                    tip="Pozisyon kapatildiktan sonra kac saniye bekle",
                    help_text=(
                        "SATIS SONRASI BEKLEME\n"
                        "─────────────────────\n"
                        "Bir pozisyon kapatildiktan sonra\n"
                        "(kar veya zarar farketmez) bu sure\n"
                        "boyunca yeni pozisyon acilmaz.\n\n"
                        "  30:  Kisa (hizli yeni giris)\n"
                        "  60:  Standart\n"
                        "  120: Orta (onerilen)\n"
                        "  300: Uzun (sakin islem)\n\n"
                        "Anti-churning: cok hizli alis-satis\n"
                        "dongusunu onler. Fee kaybini azaltir."))
        self._field(g,"loss_cooldown_seconds", "Zarar Cooldown (sn)", "3600",
                    tip="Ayni coinde zarar sonrasi tekrar giris bekleme suresi (3600=1saat)",
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
                        "  600  = 10 dakika\n"
                        "  1800 = 30 dakika\n"
                        "  3600 =  1 saat (onerilen)\n\n"
                        "Sadece ZARAR ile kapanan coinlere\n"
                        "uygulanir. Karla kapanan coinler\n"
                        "cooldown'a girmez."))

        # ════════════════ COLUMN 3: Yon + BTC + ADX + Coin + Limit + Tarayici ════════════════
        s = c3  # switch to column 3

        # ──────────────── YON DENGESI (LONG/SHORT) ────────────────
        g = self._section(s,"Yon Dengesi (Long/Short Orani)")

        self._cb_vars["direction_balance_enabled"] = ctk.BooleanVar(value=False)
        row_db = ctk.CTkFrame(g, fg_color="transparent")
        row_db.pack(fill="x", padx=8, pady=1)
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
        row_ratio = ctk.CTkFrame(g, fg_color="transparent")
        row_ratio.pack(fill="x", padx=8, pady=1)
        ctk.CTkLabel(row_ratio, text="Oran (X-Y):", width=180, anchor="w", font=ctk.CTkFont(size=11)).pack(side="left")
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
        g = self._section(s,"BTC Korelasyon Filtresi")

        self._checkbox(g,"btc_correlation_enabled", "BTC Korelasyon Filtresi",
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
        self._field(g,"btc_max_portfolio_beta", "Max Portfoy Beta", "2.5",
                    tip="Portfoy toplam beta'si bu degeri asarsa yeni pozisyon acilmaz (2.5=standart)",
                    help_text=(
                        "MAX PORTFOY BETA\n"
                        "─────────────────\n"
                        "Tum acik pozisyonlarin toplam BTC\n"
                        "beta'si bu degeri asarsa yeni pozisyon\n"
                        "acilmasi engellenir.\n\n"
                        "  1.5: Cok konservatif\n"
                        "  2.0: Konservatif\n"
                        "  2.5: Standart (onerilen)\n"
                        "  4.0: Gevsek\n\n"
                        "Beta hesabi: Her coinin BTC ile\n"
                        "korelasyonu x pozisyon agirligi.\n"
                        "Yuksek beta = BTC dustugunde\n"
                        "tum portfoy duser."))

        # ──────────────── ADX REJIM SISTEMI ────────────────
        g = self._section(s,"ADX Rejim Sistemi")

        self._checkbox(g,"adx_regime_enabled", "ADX Rejim Sistemi Aktif",
                       default=False,
                       help_text=(
                           "ADX REJIM SISTEMI\n"
                           "─────────────────\n"
                           "ADX degerine gore farkli giris/cikis\n"
                           "parametreleri uygular.\n\n"
                           "4 REJIM:\n"
                           "  ADX < 18: ISLEM ACMA (yatar piyasa)\n"
                           "  ADX 18-25 (trend yok): RANGING\n"
                           "    → 2 ATR limit giris, 2 ATR SL\n"
                           "    → 3 ATR TP + 4/1 ATR trailing\n"
                           "  ADX 18-25 (trend var): WEAK TREND\n"
                           "    → 1 ATR limit giris, 2 ATR SL\n"
                           "    → 4/1 ATR trailing\n"
                           "  ADX > 25: STRONG TREND\n"
                           "    → market giris, 2 ATR SL\n"
                           "    → 4/1 ATR trailing\n\n"
                           "MTF TEYIT:\n"
                           "  2-ust ve 5-ust timeframe\n"
                           "  ayni yonu desteklemeli."))

        self._field(g,"adx_regime_no_trade", "No Trade Esigi (ADX <)", "18",
                    tip="Bu ADX altinda islem acilmaz (yatar piyasa)",
                    help_text=(
                        "NO TRADE ESIGI\n"
                        "──────────────\n"
                        "ADX bu degerin altindaysa hic islem\n"
                        "acilmaz. Piyasa yatay, trend yok.\n\n"
                        "  15: Gevsek (yatayda bile islem acar)\n"
                        "  18: Standart (onerilen)\n"
                        "  20: Siki (sadece net trendlerde)\n\n"
                        "Yatar piyasada whipsaw riski cok yuksek.\n"
                        "SL surekli tetiklenir, zarar birikir."))
        self._field(g,"adx_regime_strong_trend", "Strong Trend Esigi (ADX >)", "25",
                    tip="Bu ADX ustunde guclu trend: market giris",
                    help_text=(
                        "STRONG TREND ESIGI\n"
                        "──────────────────\n"
                        "ADX bu degerin ustundeyse guclu trend.\n"
                        "Market fiyattan giris yapilir (limit yok).\n\n"
                        "  20: Gevsek (zayif trendde bile market)\n"
                        "  25: Standart (onerilen)\n"
                        "  30: Siki (sadece cok guclu trendlerde)\n\n"
                        "No Trade ve Strong Trend arasindaki\n"
                        "bolge (18-25) ranging/weak_trend olarak\n"
                        "siniflandirilir ve limit giris kullanilir."))

        self._checkbox(g,"adx_regime_mtf_required", "MTF Teyit Zorunlu (2-ust + 5-ust TF)",
                       default=True,
                       help_text=(
                           "MTF (Multi-Timeframe) TEYIT\n"
                           "───────────────────────────\n"
                           "Pozisyon acilmadan once 2-ust ve 5-ust\n"
                           "timeframe'lerdeki sinyal yonunu kontrol eder.\n\n"
                           "ORNEK (1m bazda):\n"
                           "  2-ust = 5m, 5-ust = 30m\n"
                           "  1m: AL → 5m: AL → 30m: AL → OK\n"
                           "  1m: AL → 5m: SAT → ENGEL\n\n"
                           "ORNEK (5m bazda):\n"
                           "  2-ust = 15m, 5-ust = 2h\n\n"
                           "Tum rejimlerde uygulanir."))

        # Ranging (ADX 18-25, trend yok) parametreleri
        ctk.CTkLabel(g, text="  Ranging (ADX 18-25, trend yok):",
                     font=ctk.CTkFont(size=12, slant="italic"),
                     text_color="#CE93D8").pack(anchor="w", padx=15, pady=(8, 2))
        self._field(g,"adx_regime_ranging_entry_atr", "  Limit Giris ATR Ofseti", "2.0",
                    tip="Kac ATR asagiya/yukariya limit emir (2.0=genis pazarlik)",
                    help_text="RANGING rejimde limit giris ofseti.\nYatay piyasada genis pazarlik yapilir.\n2.0 = 2 ATR uzaga limit emir konur.")
        self._field(g,"adx_regime_ranging_sl_atr", "  SL (ATR)", "2.0",
                    tip="Stop Loss mesafesi ATR cinsinden",
                    help_text="RANGING rejimde SL mesafesi.\nServer'a STOP_MARKET olarak gonderilir.\n2.0 ATR = standart mesafe.")
        self._field(g,"adx_regime_ranging_tp_atr", "  TP (ATR)", "3.0",
                    tip="Take Profit mesafesi ATR cinsinden (ranging'de sabit TP)",
                    help_text="RANGING rejimde sabit TP hedefi.\nYatay piyasada trend uzun surmez,\nbu yuzden sabit TP kullanilir.\n3.0 ATR = standart hedef.")
        self._field(g,"adx_regime_ranging_trail_activate_atr", "  Trailing Tetikleme (ATR)", "4.0",
                    tip="Trailing stop aktif olma mesafesi",
                    help_text="RANGING rejimde trailing aktivasyonu.\nFiyat 4 ATR ilerlediginde trailing baslar.\nTP (3 ATR) oncesinde tetiklenebilir.")
        self._field(g,"adx_regime_ranging_trail_callback_atr", "  Trailing Geri Cekilme (ATR)", "1.0",
                    tip="Trailing stop geri cekilme mesafesi",
                    help_text="RANGING rejimde trailing callback.\nFiyat 1 ATR geri cekilince pozisyon kapatilir.")

        # Weak Trend (ADX 18-25, trend var) parametreleri
        ctk.CTkLabel(g, text="  Weak Trend (ADX 18-25, trend var):",
                     font=ctk.CTkFont(size=12, slant="italic"),
                     text_color="#4FC3F7").pack(anchor="w", padx=15, pady=(8, 2))
        self._field(g,"adx_regime_weak_entry_atr", "  Limit Giris ATR Ofseti", "1.0",
                    tip="Kac ATR asagiya/yukariya limit emir (1.0=orta pazarlik)",
                    help_text="WEAK TREND rejimde limit giris.\nTrend baslangici, 1 ATR pazarlik yeterli.\nDaha yakin giris = daha iyi maliyet.")
        self._field(g,"adx_regime_weak_sl_atr", "  SL (ATR)", "2.0",
                    tip="Stop Loss mesafesi ATR cinsinden",
                    help_text="WEAK TREND rejimde SL mesafesi.\n2.0 ATR = standart, tum rejimlerde ayni.")
        self._field(g,"adx_regime_weak_trail_activate_atr", "  Trailing Tetikleme (ATR)", "4.0",
                    tip="Trailing stop aktif olma mesafesi",
                    help_text="WEAK TREND rejimde trailing aktivasyonu.\n4 ATR = standart mesafe.")
        self._field(g,"adx_regime_weak_trail_callback_atr", "  Trailing Geri Cekilme (ATR)", "1.0",
                    tip="Trailing stop geri cekilme mesafesi",
                    help_text="WEAK TREND rejimde trailing callback.\n1 ATR geri cekilme = standart.")

        # Strong Trend (ADX > 25) parametreleri
        ctk.CTkLabel(g, text="  Strong Trend (ADX > 25):",
                     font=ctk.CTkFont(size=12, slant="italic"),
                     text_color="#00C853").pack(anchor="w", padx=15, pady=(8, 2))
        ctk.CTkLabel(g, text="    Giris: Market fiyat (pazarlik yok)",
                     text_color="gray50", font=ctk.CTkFont(size=11)).pack(
            anchor="w", padx=25, pady=1)
        self._field(g,"adx_regime_strong_sl_atr", "  SL (ATR)", "2.0",
                    tip="Stop Loss mesafesi ATR cinsinden",
                    help_text="STRONG TREND rejimde SL mesafesi.\n2.0 ATR = standart, tum rejimlerde ayni.")
        self._field(g,"adx_regime_strong_trail_activate_atr", "  Trailing Tetikleme (ATR)", "4.0",
                    tip="Trailing stop aktif olma mesafesi",
                    help_text="STRONG TREND rejimde trailing aktivasyonu.\nGuclu trendde 4 ATR hizla gecilir.")
        self._field(g,"adx_regime_strong_trail_callback_atr", "  Trailing Geri Cekilme (ATR)", "1.0",
                    tip="Trailing stop geri cekilme mesafesi",
                    help_text="STRONG TREND rejimde trailing callback.\n1 ATR geri cekilme = standart.")

        # ──────────────── MEAN REVERSION ────────────────
        g = self._section(s,"Mean Reversion (Bant Ici Islem)")

        self._checkbox(g,"mean_reversion_enabled", "Mean Reversion Aktif",
                       default=False,
                       help_text=(
                           "MEAN REVERSION SISTEMI\n"
                           "──────────────────────\n"
                           "ADX < 18 olan yatay piyasa coinlerinde\n"
                           "Bollinger bandi ici islem acar.\n\n"
                           "Nasil calisir:\n"
                           "  ADX < 18 → MR havuzuna yonlendirilir\n"
                           "  ADX 18-25 → Gray zone: 5 sinyal oylar\n"
                           "  BB bandina yakin limit emir ile giris\n"
                           "  BB orta cizgisi = TP hedefi\n\n"
                           "NOT: ADX Rejim Sistemi ile birlikte\n"
                           "calisir. ADX < 18 coinler NO_TRADE yerine\n"
                           "MR havuzuna yonlendirilir."))

        self._field(g,"mr_max_adx", "Max ADX (MR Havuzu)", "18",
                    tip="Bu ADX altindaki coinler MR havuzuna girer",
                    help_text="ADX < 18 = yatay piyasa, trend yok.\nMR stratejisi bu coinlerde calisir.")
        self._field(g,"mr_max_positions", "Max MR Pozisyon", "2",
                    tip="Ayni anda max kac MR pozisyon acilabilir (trend pozisyonlarindan ayri)",
                    help_text="MR pozisyonlari trend pozisyonlarindan ayri sayilir.\n2 = max 2 MR + max N trend.")
        self._field(g,"mr_min_score", "Min MR Skoru", "65",
                    tip="MR skoru en az bu kadar olmali (0-100)",
                    help_text="MR skoru: BB proximity %25 + RSI extreme %25\n+ Volume exhaustion %20 + BB width %15 + Momentum %15")
        self._field(g,"mr_rsi_oversold", "RSI Oversold Esigi", "30",
                    tip="RSI bu deger altinda = asiri satim (LONG firsati)",
                    help_text="RSI < 30 → asiri satim bolgesi.\nMR LONG icin gerekli kosul.")
        self._field(g,"mr_rsi_overbought", "RSI Overbought Esigi", "70",
                    tip="RSI bu deger ustunde = asiri alim (SHORT firsati)",
                    help_text="RSI > 70 → asiri alim bolgesi.\nMR SHORT icin gerekli kosul.")
        self._field(g,"mr_sl_atr_mult", "SL (ATR Carpani)", "1.5",
                    tip="MR pozisyon SL mesafesi (ATR cinsinden). Trend'den daha siki.",
                    help_text="MR SL = 1.5 ATR (trend 2.0 ATR).\nYatay piyasada daha siki SL yeterli.")
        self._field(g,"mr_bb_proximity_pct", "BB Proximity (%)", "20.0",
                    tip="Fiyat BB bandina bu yuzde kadar yakin olmali",
                    help_text="Fiyat BB bandinin %20 yakininda mi?\nDaha kucuk = daha secici (banda cok yakin).")
        self._field(g,"mr_volume_exhaustion_max", "Volume Exhaustion Max", "0.8",
                    tip="Hacim ortalamaya kiyasla bu orandan dusuk olmali (tukenmislik)",
                    help_text="Hacim orani < 0.8 = tukenmislik.\nDusuk hacim = hareket bitiyor, donus gelebilir.")
        self._field(g,"mr_min_bb_range_fee_mult", "Min BB Range (Fee Carpani)", "3.0",
                    tip="BB bant genisligi en az fee'nin kac kati olmali (karlilik filtresi)",
                    help_text="BB araligi cok darsa kar fee'yi karsilamaz.\n3.0 = BB range en az 3x fee olmali.")
        self._field(g,"mr_time_limit_minutes", "MR Zaman Limiti (dk)", "240",
                    tip="MR pozisyon max ne kadar acik kalabilir",
                    help_text="MR pozisyonlari trend'den kisa tutulur.\n240dk = 4 saat (trend 8 saat).")
        self._checkbox(g,"mr_breakout_to_trend", "Breakout → Trend Gecisi",
                       default=True,
                       help_text=(
                           "MR pozisyondayken breakout olursa\n"
                           "(BB kirilim + hacim + ADX yukselisi)\n"
                           "otomatik TREND moduna gecer.\n\n"
                           "TP hedefi kalkar, trailing aktif olur."))
        self._checkbox(g,"mr_stop_flip_enabled", "Stop Flip (Ters Pozisyon)",
                       default=True,
                       help_text=(
                           "MR pozisyon SL'ye takilirsa\n"
                           "ters yonde yeni pozisyon acar.\n\n"
                           "Ornek: LONG SL → SHORT ac."))

        # ──────────────── COIN GUNLUK YASAK ────────────────
        g = self._section(s,"Coin Gunluk Yasak (Kayip Limiti)")

        self._field(g,"coin_daily_loss_limit", "Max Zarar Sayisi (0=kapali)", "0",
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
        self._field(g,"coin_daily_ban_hours", "Yasak Suresi (saat)", "24",
                    tip="Zarar limiti asilinca coin kac saat yasakli kalir (24=1 gun)",
                    help_text=(
                        "YASAK SURESI\n"
                        "────────────\n"
                        "Coin zarar limitine ulasinca bu kadar\n"
                        "saat boyunca yasakli kalir.\n\n"
                        "  6:  Kisa yasak\n"
                        "  12: Yari gun\n"
                        "  24: 1 gun (onerilen)\n"
                        "  48: 2 gun (cok siki)\n\n"
                        "Sure ilk zarardan itibaren sayilir.\n"
                        "Ornek: 3 zarar/24s → ilk zarardan\n"
                        "24 saat sonra yasak kalkar."))

        # ──────────────── LIMIT CIKIS ────────────────
        g = self._section(s,"Limit Cikis (Fee Tasarrufu)")

        self._cb_vars["limit_exit_enabled"] = ctk.BooleanVar(value=False)
        row_le = ctk.CTkFrame(g, fg_color="transparent")
        row_le.pack(fill="x", padx=8, pady=1)
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

        self._field(g,"limit_exit_atr_offset", "Cikis ATR Ofseti", "0.2",
                    tip="Cikista kac ATR lehimize limit fiyat konur (0.2=yakin, hizli dolsun)",
                    help_text=(
                        "CIKIS LIMIT ATR OFSETI\n"
                        "──────────────────────\n"
                        "Cikista limit emir fiyati ne kadar\n"
                        "lehimize konur.\n\n"
                        "  LONG kapama: piyasa + (N x ATR)\n"
                        "  SHORT kapama: piyasa - (N x ATR)\n\n"
                        "  0.1: Cok yakin (hizli dolar, maker fee)\n"
                        "  0.2: Yakin (onerilen)\n"
                        "  0.5: Uzak (daha kar ama dolmayabilir)\n\n"
                        "Dolmazsa otomatik market emre doner."))

        # ──────────────── TARAYICI AYARLARI ────────────────
        g = self._section(s,"Tarayici (Scanner)")
        self._field(g,"max_symbols_to_scan", "Taranacak Coin Sayisi", "50",
                    tip="Hacim siralamasindan en fazla kac coin taransin",
                    help_text=(
                        "TARANACAK COIN SAYISI\n"
                        "─────────────────────\n"
                        "Binance Futures'tan 24s hacim\n"
                        "siralamasina gore en fazla kac coin\n"
                        "indikatör analizi yapilacak.\n\n"
                        "  30: Hizli tarama (sadece en likit)\n"
                        "  50: Standart (onerilen)\n"
                        "  100: Genis (daha fazla firsat ama\n"
                        "        tarama suresi artar)\n\n"
                        "Spike coinler (>%3 degisim) ekstra\n"
                        "olarak eklenir (max 20 adet)."))
        self._checkbox(g,"battle_mode", "Savas Modu (tek coin odakli)",
                      help_text=(
                          "SAVAS MODU (Battle Mode)\n"
                          "────────────────────────\n"
                          "Aktifken sadece watched_symbols\n"
                          "listesindeki coinler taranir.\n\n"
                          "Scanner genis piyasayi taramaz,\n"
                          "odak dar tutulur. Manuel secilen\n"
                          "coinlerle sinirli islem yapilir."))
        self._checkbox(g,"close_only", "Sadece Kapama Modu (yeni pozisyon acma)",
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
        self._checkbox(g,"focus_mode", "Odak Modu (aktif sembole odaklan)",
                      help_text=(
                          "ODAK MODU (Focus Mode)\n"
                          "──────────────────────\n"
                          "Aktifken scanner sadece aktif\n"
                          "sembole (config: active_symbol)\n"
                          "odaklanir. Diger coinleri taramaz.\n\n"
                          "Manuel islem yaparken kullanisli."))

        # ── Info box with dynamic calculations (bottom, full width) ──
        info_parent = self._scroll  # below all 3 columns
        self._info_frame = ctk.CTkFrame(info_parent, fg_color="#1a1a2e", corner_radius=8)
        self._info_frame.pack(fill="x", padx=5, pady=(10, 5))
        self._info_label = ctk.CTkLabel(
            self._info_frame, text="", justify="left",
            font=ctk.CTkFont(size=11, family="Consolas"),
            text_color="#88CC88",
        )
        self._info_label.pack(padx=10, pady=8, anchor="w")
        self._update_info()

    # ════════════════════════════════════════
    # UI HELPERS
    # ════════════════════════════════════════

    # Section color mapping for visual grouping
    _SECTION_COLORS = {
        "Giris": "#4FC3F7",          # blue - entry
        "Emir": "#4FC3F7",           # blue - order type
        "Kaldirac": "#FF9800",       # orange - leverage
        "Stop Loss": "#FF1744",      # red - SL
        "Iz Suren": "#CE93D8",       # purple - trailing
        "Kar Hedefi": "#00C853",     # green - TP
        "Kismi Kar": "#00C853",      # green - partial TP
        "Sinyal": "#FFD54F",         # yellow - signal exit
        "Zaman": "#64B5F6",          # light blue - time
        "Risk": "#FF5722",           # deep orange - risk
        "Yon Dengesi": "#AB47BC",    # purple - direction
        "BTC": "#78909C",            # gray-blue - correlation
        "ADX Rejim": "#00E676",      # bright green - ADX regime
        "Coin Gunluk": "#FF8A65",    # salmon - coin ban
        "Limit Cikis": "#4DB6AC",    # teal - limit exit
        "Tarayici": "#7E57C2",       # deep purple - scanner
    }

    def _section(self, parent, title: str) -> ctk.CTkFrame:
        """Create a bordered group frame with colored title. Returns inner frame for fields."""
        # Find matching color
        color = "#7799BB"
        for key, c in self._SECTION_COLORS.items():
            if title.startswith(key):
                color = c
                break
        # Outer bordered frame
        outer = ctk.CTkFrame(parent, fg_color="#1a1a2e", border_width=1,
                             border_color=color, corner_radius=8)
        outer.pack(fill="x", padx=2, pady=(6, 2))
        # Title bar
        title_bar = ctk.CTkFrame(outer, fg_color=color, height=24, corner_radius=6)
        title_bar.pack(fill="x", padx=2, pady=(2, 0))
        title_bar.pack_propagate(False)
        ctk.CTkLabel(title_bar, text=f"  {title}",
                     font=ctk.CTkFont(size=11, weight="bold"),
                     text_color="#0a0a1a").pack(side="left", padx=2)
        # Inner content frame
        inner = ctk.CTkFrame(outer, fg_color="transparent")
        inner.pack(fill="x", padx=4, pady=(4, 6))
        return inner

    def _field(self, parent, key: str, label: str, default: str,
              tip: str = "", help_text: str = "") -> None:
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", padx=8, pady=1)
        lbl = ctk.CTkLabel(row, text=f"{label}:", width=180, anchor="w",
                           font=ctk.CTkFont(size=11))
        lbl.pack(side="left")
        # Tooltip on hover for the label
        if tip:
            lbl.bind("<Enter>", lambda e, t=tip: self._feedback.configure(
                text=t, text_color="gray60"))
            lbl.bind("<Leave>", lambda e: self._feedback.configure(text=""))
        entry = ctk.CTkEntry(row, width=75, font=ctk.CTkFont(size=11))
        entry.pack(side="left", padx=3)
        entry.insert(0, default)
        self._entries[key] = entry
        self._all_widgets.append((entry, "entry"))
        if help_text:
            btn = ctk.CTkButton(row, text="?", width=22, height=22,
                                fg_color="gray40", hover_color="gray50",
                                font=ctk.CTkFont(size=10, weight="bold"),
                                command=lambda t=label, h=help_text: self._show_help(t, h))
            btn.pack(side="left", padx=1)

    def _checkbox(self, parent, key: str, label: str,
                  default: bool = True, help_text: str = "") -> None:
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", padx=8, pady=1)
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
        # Show/hide preset and template frames based on mode
        if is_manual:
            self._preset_frame.pack_forget()
            self._tmpl_frame.pack(side="right")
        else:
            self._preset_frame.pack(side="left")
            # Templates also available in standard mode

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

            fee_input = float(self._entries.get("fee_pct",
                              type("", (), {"get": lambda s: "0.10"})()).get() or 0.10)
            slip_input = float(self._entries.get("slippage_mult",
                               type("", (), {"get": lambda s: "0.5"})()).get() or 0.5)

            liq_dist = (1.0 / max_lev) * (liq_f / 100.0) * 100  # % price move to liq
            fee_pct_dec = fee_input / 100.0  # 0.10 -> 0.001
            fee_roi = fee_pct_dec * max_lev * 100  # fee as % of margin
            slip_roi = fee_roi * slip_input

            # Fee-aware software SL
            raw_sl_roi = liq_dist / 100 * sl_pct / 100 * max_lev * 100
            net_sl_roi = max(raw_sl_roi - fee_roi - slip_roi, fee_roi)
            sl_price_pct = net_sl_roi / (max_lev * 100) * 100  # back to % price
            sl_roi = net_sl_roi
            em_price_pct = liq_dist * em_pct / 100
            tp_price_pct = liq_dist * tp_mult
            tp_roi = tp_price_pct / 100 * max_lev * 100
            trail_act_roi = fee_roi * trail_act
            trail_dist_roi = fee_roi * trail_dist

            # Server SL (ATR-based)
            srv_atr = float(self._entries.get("server_sl_atr_mult",
                            type("", (), {"get": lambda s: "2.0"})()).get() or 2.0)

            theo_liq = (1.0 / max_lev) * 100
            lines = [
                f"  {max_lev}x Kaldirac (liq_factor=%{liq_f}, fee=%{fee_input}, slip=x{slip_input}):",
                f"  Teorik liq:              %{theo_liq:.2f} geri gelme",
                f"  Pratik liq:              %{liq_dist:.2f} fiyat hareketi",
                f"  Fee (round-trip):        %{fee_roi:.1f} ROI + Slip %{slip_roi:.1f} ROI = %{fee_roi+slip_roi:.1f} ROI",
                f"  Fee breakeven:           %{fee_pct_dec*100:.3f} fiyat hareketi",
                f"  --- a) Server SL ---",
                f"  Server SL:               {srv_atr}x ATR (Binance STOP_MARKET)",
                f"  --- b) Yazilim SL (fee-aware) ---",
                f"  Yazilim SL:              %{sl_price_pct:.3f} fiyat = %{sl_roi:.0f} ROI kayip (fee+slip dusulmus)",
                f"  --- c) Emergency ---",
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
            raw = entry.get().strip()
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
