"""Indicator Detail Panel - Multi-timeframe indicator analysis with time-series table.

Combobox'dan coin sec, ust tabloda 1m'den 1w'ye kadar tum timeframe'lerde
indikator durumunu gor. Bir timeframe satirina tikla, alt tabloda o timeframe'in
son N mumundaki indikator degerlerinin zaman icindeki degisimini izle.
En altta fiyat+hacim grafigi."""

import threading
import time
from datetime import datetime

import customtkinter as ctk
import numpy as np
import pandas as pd
from loguru import logger
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure
from matplotlib.gridspec import GridSpec

from analysis.confluence import ConfluenceScorer
from analysis.market_regime import MarketRegimeDetector
from indicators.indicator_engine import IndicatorEngine

# ── Reuse scanner_panel constants ───────────────────────────────────────────
CONF_TREND = ["MACD", "ADX", "EMA50", "Price_vs_SMA", "SR"]
CONF_REVERSION = ["RSI", "BB"]
CONF_VOLUME = ["OBV", "CMF", "CVD", "VWAP"]
CONF_INDICATORS = CONF_TREND + CONF_REVERSION + CONF_VOLUME

CONF_SHORT_MAP = {
    "MACD": "MACD", "ADX": "ADX", "EMA50": "EMA50", "Price_vs_SMA": "SMA", "SR": "S/R",
    "RSI": "RSI", "BB": "BB",
    "OBV": "OBV", "CMF": "CMF", "CVD": "CVD", "VWAP": "VWAP",
}
CONF_SHORT = [CONF_SHORT_MAP[k] for k in CONF_INDICATORS]

CONF_VALUE_KEYS = {
    "MACD": "MACD_histogram", "ADX": "ADX", "EMA50": None, "Price_vs_SMA": None,
    "SR": None, "RSI": "RSI", "BB": "BB_PercentB",
    "OBV": "OBV_slope", "CMF": "CMF", "CVD": "CVD_normalized", "VWAP": "VWAP",
}

CONF_HDR_COLORS = {}
for k in CONF_TREND:
    CONF_HDR_COLORS[CONF_SHORT_MAP[k]] = "#4FC3F7"
for k in CONF_REVERSION:
    CONF_HDR_COLORS[CONF_SHORT_MAP[k]] = "#CE93D8"
for k in CONF_VOLUME:
    CONF_HDR_COLORS[CONF_SHORT_MAP[k]] = "#FFD54F"

# All timeframes to analyze (1m to 1w)
ALL_TIMEFRAMES = [
    "1m", "3m", "5m", "15m", "30m",
    "1h", "2h", "4h", "8h", "12h",
    "1d", "3d", "1w",
]

# Indicators to show in the time-series table (lower table)
# key: display name, value: indicator_values key
TIMESERIES_INDICATORS = [
    ("RSI", "RSI", "{:.1f}"),
    ("MACD Hist", "MACD_histogram", "{:.4f}"),
    ("MACD Line", "MACD_line", "{:.4f}"),
    ("MACD Signal", "MACD_signal", "{:.4f}"),
    ("ADX", "ADX", "{:.1f}"),
    ("+DI", "ADX_plus_DI", "{:.1f}"),
    ("-DI", "ADX_minus_DI", "{:.1f}"),
    ("EMA20", "EMA_fast", "{:.4f}"),
    ("EMA50", "EMA50", "{:.4f}"),
    ("SMA200", "SMA_slow", "{:.4f}"),
    ("BB %B", "BB_PercentB", "{:.3f}"),
    ("BB Upper", "BB_Upper", "{:.4f}"),
    ("BB Lower", "BB_Lower", "{:.4f}"),
    ("ATR", "ATR", "{:.4f}"),
    ("OBV Slope", "OBV_slope", "{:.2f}"),
    ("CMF", "CMF", "{:.3f}"),
    ("CVD Norm", "CVD_normalized", "{:.3f}"),
    ("VWAP", "VWAP", "{:.4f}"),
    ("Fiyat", "Price", "{:.4f}"),
    ("S/R Pozisyon", "SR_position", "{}"),
    ("Conf Skor", "_conf_score", "{:+.2f}"),
    ("Conf Sinyal", "_conf_signal", "{}"),
    ("Aktif Grup", "_active_group", "{}"),
]

# Colors
CLR_BG = "#1a1a2e"
CLR_SUBPLOT = "#16213e"
CLR_GRID = "#333333"
CLR_GREEN = "#00C853"
CLR_RED = "#FF1744"
CLR_BLUE = "#2196F3"
CLR_ORANGE = "#FF9800"
CLR_GRAY = "#9E9E9E"
CLR_TEXT = "#E0E0E0"

# Number of historical candles to show in time-series table
HISTORY_CANDLES = 20

# Kline fetch limit: 200 is enough (indicators need ~55 warmup + 20 history)
KLINE_LIMIT = 200


class _ParamConfig:
    """Lightweight config wrapper for IndicatorEngine."""
    def __init__(self, params: dict):
        self._params = params

    def get(self, key: str, default=None):
        if key == "indicators":
            return self._params
        parts = key.split(".")
        if parts[0] == "indicators" and len(parts) == 2:
            return self._params.get(parts[1], default)
        return default


def _conf_detail_cell(score: float, raw_val=None) -> tuple[str, str]:
    """Convert indicator's confluence score to (label, color)."""
    if score > 0.5:
        signal, color = "AL", "#00C853"
    elif score > 0:
        signal, color = "al", "#81C784"
    elif score < -0.5:
        signal, color = "SAT", "#FF1744"
    elif score < 0:
        signal, color = "sat", "#EF5350"
    else:
        signal, color = "--", "#555555"

    if raw_val is not None and raw_val != 0:
        if isinstance(raw_val, (int, float)):
            abs_val = abs(raw_val)
            if abs_val >= 1_000_000:
                val_str = f"{raw_val / 1_000_000:.1f}M"
            elif abs_val >= 10_000:
                val_str = f"{raw_val / 1_000:.0f}K"
            elif abs_val >= 1_000:
                val_str = f"{raw_val / 1_000:.1f}K"
            elif abs_val >= 100:
                val_str = f"{raw_val:.0f}"
            elif abs_val >= 1:
                val_str = f"{raw_val:.1f}"
            else:
                val_str = f"{raw_val:.2f}"
        else:
            val_str = str(raw_val)
        return (f"{signal}\n{val_str}", color)
    return (signal, color)


def _format_num(val, fmt_str: str) -> str:
    """Safely format a numeric/string value."""
    if val is None:
        return "--"
    if isinstance(val, str):
        return val if val else "--"
    try:
        return fmt_str.format(val)
    except (ValueError, TypeError):
        return str(val) if val else "--"


class IndicatorDetailPanel(ctk.CTkFrame):
    """Multi-timeframe indicator detail panel with time-series drill-down."""

    def __init__(self, parent, controller, **kwargs):
        super().__init__(parent, **kwargs)
        self.controller = controller

        self._tf_data: dict[str, dict] = {}  # tf -> {indicators, confluence, klines}
        self._selected_tf: str = "15m"
        self._loading = False
        self._load_thread: threading.Thread | None = None

        # Chart state
        self._fig: Figure | None = None
        self._canvas: FigureCanvasTkAgg | None = None

        self._build_ui()

    def _build_ui(self) -> None:
        # ═══ TOP BAR: Coin selection + controls ═══
        top = ctk.CTkFrame(self, height=40)
        top.pack(fill="x", padx=6, pady=(6, 3))

        ctk.CTkLabel(top, text="Coin:", width=35,
                     font=ctk.CTkFont(size=13)).pack(side="left", padx=(4, 2))

        # Get symbols: top 50 by volume + up to 20 spike (>3%)
        symbols = self._get_symbol_list()
        self._symbol_var = ctk.StringVar(value=symbols[0] if symbols else "BTCUSDT")
        self._symbol_cb = ctk.CTkComboBox(
            top, variable=self._symbol_var, values=symbols,
            width=140, state="normal",
            font=ctk.CTkFont(size=13),
        )
        self._symbol_cb.pack(side="left", padx=4)

        self._coin_count_lbl = ctk.CTkLabel(
            top, text=f"({len(symbols)} coin)", font=ctk.CTkFont(size=11),
            text_color=CLR_GRAY)
        self._coin_count_lbl.pack(side="left", padx=(0, 4))

        ctk.CTkButton(
            top, text="Liste Yenile", width=85, height=28,
            font=ctk.CTkFont(size=11),
            fg_color="gray30", hover_color="gray40",
            command=self._refresh_symbol_list,
        ).pack(side="left", padx=(0, 8))

        self._analyze_btn = ctk.CTkButton(
            top, text="Analiz Et", width=100,
            font=ctk.CTkFont(size=13, weight="bold"),
            fg_color="#0D47A1", hover_color="#1565C0",
            command=self._on_analyze,
        )
        self._analyze_btn.pack(side="left", padx=8)

        self._auto_var = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(
            top, text="Otomatik (30sn)", variable=self._auto_var,
            font=ctk.CTkFont(size=12),
            command=self._on_auto_toggle,
        ).pack(side="left", padx=8)

        self._status_lbl = ctk.CTkLabel(
            top, text="", font=ctk.CTkFont(size=12), text_color=CLR_GRAY,
        )
        self._status_lbl.pack(side="right", padx=8)

        # ═══ UPPER TABLE: Timeframe x Indicators ═══
        tf_frame = ctk.CTkFrame(self)
        tf_frame.pack(fill="x", padx=5, pady=(3, 2))

        ctk.CTkLabel(tf_frame, text="Timeframe Karsilastirmasi",
                     font=ctk.CTkFont(size=13, weight="bold")).pack(anchor="w", padx=5, pady=2)

        # Header
        hdr = ctk.CTkFrame(tf_frame)
        hdr.pack(fill="x", padx=2)

        self._tf_headers = ["TF", "Fiyat", "ATR%"] + CONF_SHORT + ["Conf", "AL", "SAT", "Sinyal", "Grup"]
        self._tf_widths = [40, 80, 50] + [52] * 11 + [50, 30, 30, 50, 60]
        for h, w in zip(self._tf_headers, self._tf_widths):
            hdr_color = CONF_HDR_COLORS.get(h, "#7799BB")
            ctk.CTkLabel(hdr, text=h, width=w,
                         font=ctk.CTkFont(size=12, weight="bold"),
                         text_color=hdr_color).pack(side="left", padx=0)

        # Scrollable rows
        self._tf_scroll = ctk.CTkScrollableFrame(tf_frame, height=280)
        self._tf_scroll.pack(fill="x", padx=2)
        self._tf_rows: list[tuple[ctk.CTkFrame, list[ctk.CTkLabel]]] = []
        self._tf_row_cache: list = []
        self._build_tf_rows()

        # ═══ LOWER TABLE: Indicator time-series ═══
        ts_frame = ctk.CTkFrame(self)
        ts_frame.pack(fill="both", expand=True, padx=5, pady=(2, 2))

        self._ts_title_lbl = ctk.CTkLabel(
            ts_frame, text="Indikator Zaman Serisi (bir timeframe satirina tiklayin)",
            font=ctk.CTkFont(size=13, weight="bold"),
        )
        self._ts_title_lbl.pack(anchor="w", padx=5, pady=2)

        # Header for time-series (built dynamically)
        self._ts_hdr_frame = ctk.CTkFrame(ts_frame)
        self._ts_hdr_frame.pack(fill="x", padx=2)

        self._ts_scroll = ctk.CTkScrollableFrame(ts_frame, height=260)
        self._ts_scroll.pack(fill="both", expand=True, padx=2)
        self._ts_rows: list[tuple[ctk.CTkFrame, list[ctk.CTkLabel]]] = []

        # ═══ BOTTOM: Price + Volume chart ═══
        self._chart_frame = ctk.CTkFrame(self, fg_color=CLR_BG, height=220)
        self._chart_frame.pack(fill="x", padx=5, pady=(2, 4))
        self._chart_frame.pack_propagate(False)

    def _get_symbol_list(self) -> list[str]:
        """Get coin list: top 50 by volume + up to 20 spike (>3% change).

        Same logic as scanner's SymbolUniverse:
        1. Scanner universe already loaded → use it directly
        2. Otherwise → fetch from REST API (same filtering)
        """
        # --- Source 1: Scanner's SymbolUniverse (already fetched) ---
        scanner = self.controller.scanner
        if scanner and hasattr(scanner, '_universe'):
            universe_symbols = scanner._universe.get_symbols()
            if universe_symbols:
                return universe_symbols

        # --- Source 2: Fetch fresh from Binance REST API ---
        try:
            rest = self.controller.market_service._rest
            tickers = rest.get_all_24h_tickers()
            if not tickers:
                return self._fallback_symbols()

            from scanner.symbol_universe import EXCLUDED_SYMBOLS, TRADFI_PREFIXES
            min_volume = self.controller.config.get(
                "scanner.min_volume_24h_usdt", 5_000_000)
            spike_threshold = 3.0
            max_spikes = 20

            candidates = []  # (symbol, volume, abs_change)
            for t in tickers:
                symbol = t.get("symbol", "")
                if not symbol.endswith("USDT"):
                    continue
                if symbol in EXCLUDED_SYMBOLS:
                    continue
                if symbol.startswith(TRADFI_PREFIXES):
                    continue
                volume_24h = float(t.get("quoteVolume", 0))
                if volume_24h < min_volume:
                    continue
                abs_change = abs(float(t.get("priceChangePercent", 0)))
                candidates.append((symbol, volume_24h, abs_change))

            # Top 50 by volume
            candidates.sort(key=lambda x: x[1], reverse=True)
            top_n = self.controller.config.get(
                "strategy.max_symbols_to_scan", 50)
            top_symbols = [s for s, _, _ in candidates[:top_n]]

            # Up to 20 spike symbols (>3% change, not in top 50)
            top_set = set(top_symbols)
            spike_symbols = []
            for symbol, vol, chg in candidates[top_n:]:
                if len(spike_symbols) >= max_spikes:
                    break
                if chg >= spike_threshold and symbol not in top_set:
                    spike_symbols.append(symbol)

            result = top_symbols + spike_symbols
            if result:
                self._cached_symbols = result
                return result
        except Exception as e:
            logger.debug(f"Symbol list fetch error: {e}")

        return self._fallback_symbols()

    def _fallback_symbols(self) -> list[str]:
        """Fallback: scan results + watched + cached + defaults."""
        symbols = list(getattr(self, '_cached_symbols', []))
        if symbols:
            return symbols
        s = set()
        results = self.controller.get_scan_results()
        if results:
            for r in results:
                s.add(r.symbol)
        watched = self.controller.get_watched_symbols()
        for w in watched:
            s.add(w)
        if s:
            return sorted(s)
        return ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT",
                "DOGEUSDT", "ADAUSDT", "AVAXUSDT", "DOTUSDT", "LINKUSDT"]

    def _build_tf_rows(self) -> None:
        """Build rows for each timeframe."""
        font = ctk.CTkFont(size=11)
        for i, tf in enumerate(ALL_TIMEFRAMES):
            bg = "#1c2d4d" if i % 2 == 0 else "transparent"
            row_frame = ctk.CTkFrame(self._tf_scroll, fg_color=bg, cursor="hand2")
            row_frame.pack(fill="x", pady=0)
            labels = []
            for w in self._tf_widths:
                lbl = ctk.CTkLabel(row_frame, text="--", width=w,
                                   font=font, text_color="gray")
                lbl.pack(side="left", padx=0)
                labels.append(lbl)
            # Click handler
            row_frame.bind("<Button-1>", lambda e, t=tf: self._on_tf_click(t))
            for lbl in labels:
                lbl.bind("<Button-1>", lambda e, t=tf: self._on_tf_click(t))
            self._tf_rows.append((row_frame, labels))
            self._tf_row_cache.append(None)

    # ═══════════════════════════════════════════════════════════════════════
    # DATA LOADING
    # ═══════════════════════════════════════════════════════════════════════

    def _refresh_symbol_list(self) -> None:
        """Refresh coin list from Binance (top 50 volume + 20 spike)."""
        self._coin_count_lbl.configure(text="(yukleniyor...)", text_color=CLR_ORANGE)

        def _do_refresh():
            symbols = self._get_symbol_list()
            self.after(0, lambda: self._apply_symbol_list(symbols))

        threading.Thread(target=_do_refresh, daemon=True).start()

    def _apply_symbol_list(self, symbols: list[str]) -> None:
        """Apply fetched symbol list to combobox (UI thread)."""
        if symbols:
            current = self._symbol_var.get()
            self._symbol_cb.configure(values=symbols)
            # Keep current selection if still in list
            if current not in symbols:
                self._symbol_var.set(symbols[0])
            self._coin_count_lbl.configure(
                text=f"({len(symbols)} coin)", text_color=CLR_GREEN)
        else:
            self._coin_count_lbl.configure(
                text="(hata)", text_color=CLR_RED)

    def _on_analyze(self) -> None:
        if self._loading:
            return
        self._loading = True
        self._analyze_btn.configure(state="disabled")
        self._status_lbl.configure(text="Yukleniyor...", text_color=CLR_ORANGE)
        # Refresh symbol list in combobox
        new_symbols = self._get_symbol_list()
        if new_symbols:
            self._apply_symbol_list(new_symbols)
        self._load_thread = threading.Thread(target=self._load_all_tf, daemon=True)
        self._load_thread.start()

    def _on_auto_toggle(self) -> None:
        if self._auto_var.get():
            self._auto_refresh()

    def _auto_refresh(self) -> None:
        if not self._auto_var.get():
            return
        if not self._loading:
            self._on_analyze()
        self.after(30000, self._auto_refresh)

    def _create_engine(self) -> IndicatorEngine:
        """Create IndicatorEngine with config params."""
        cfg = self.controller.config.get("indicators", {})
        params = {
            "rsi_period": cfg.get("rsi_period", 14),
            "macd_fast": cfg.get("macd_fast", 12),
            "macd_slow": cfg.get("macd_slow", 26),
            "macd_signal": cfg.get("macd_signal", 9),
            "ma_fast": cfg.get("ma_fast", 20),
            "ma_slow": cfg.get("ma_slow", 200),
        }
        return IndicatorEngine(_ParamConfig(params))

    def _load_all_tf(self) -> None:
        """Background thread: fetch klines and compute indicators for all TFs.

        Optimization notes:
        - Only 200 klines fetched (not 500) — 55 warmup + 20 history is enough
        - History snapshots are LAZY: computed only for the selected TF
        - Upper table needs only 1 compute_all per TF (= 13 total)
        - Lower table computes 20 history only for the clicked TF (on demand)
        """
        try:
            rest = self.controller.market_service._rest
            symbol = self._symbol_var.get().strip().upper()
            engine = self._create_engine()
            confluence = ConfluenceScorer(threshold=4.0, config=self.controller.config)
            regime_detector = MarketRegimeDetector()

            tf_data = {}
            total = len(ALL_TIMEFRAMES)

            for idx, tf in enumerate(ALL_TIMEFRAMES):
                self.after(0, lambda i=idx, t=total:
                           self._status_lbl.configure(
                               text=f"Yukleniyor... {i+1}/{t}",
                               text_color=CLR_ORANGE))
                try:
                    df = rest.get_klines(symbol, tf, KLINE_LIMIT)
                    if df is None or df.empty or len(df) < 30:
                        continue

                    # Single compute for latest values (upper table)
                    ind = engine.compute_all(df)
                    regime = regime_detector.detect(ind)
                    rw = regime.get("indicator_weights", {})
                    conf = confluence.score(ind, rw)

                    ind["_conf_score"] = conf.get("score", 0)
                    ind["_conf_signal"] = conf.get("signal", "NEUTRAL")
                    ind["_active_group"] = conf.get("active_group", "NEUTRAL")

                    # Store klines for lazy history computation (only when TF is clicked)
                    tf_data[tf] = {
                        "indicators": ind,
                        "confluence": conf,
                        "regime": regime,
                        "klines": df,
                        "history": None,  # lazy — computed on click
                    }
                except Exception as e:
                    logger.debug(f"TF {tf} load error for {symbol}: {e}")

            self._tf_data = tf_data
            self.after(0, self._update_tf_table)
            self.after(0, self._draw_chart)
            # Trigger lazy history for the selected TF
            if self._selected_tf in tf_data:
                self.after(0, lambda: self._on_tf_click(self._selected_tf))

            now = datetime.now().strftime("%H:%M:%S")
            self.after(0, lambda: self._status_lbl.configure(
                text=f"{symbol} | {len(tf_data)} TF | {now}", text_color=CLR_GREEN))

        except Exception as e:
            logger.error(f"Indicator detail load error: {e}")
            self.after(0, lambda: self._status_lbl.configure(
                text=f"Hata: {e}", text_color=CLR_RED))
        finally:
            self._loading = False
            self.after(0, lambda: self._analyze_btn.configure(state="normal"))

    # ═══════════════════════════════════════════════════════════════════════
    # UPPER TABLE UPDATE
    # ═══════════════════════════════════════════════════════════════════════

    def _update_tf_table(self) -> None:
        """Update timeframe comparison table with latest data."""
        for i, tf in enumerate(ALL_TIMEFRAMES):
            data = self._tf_data.get(tf)
            if not data:
                vals = [(tf, "#64B5F6")] + [("--", "gray")] * (len(self._tf_widths) - 1)
            else:
                vals = self._build_tf_row(tf, data)

            # Highlight selected TF
            frame, labels = self._tf_rows[i]
            if tf == self._selected_tf:
                frame.configure(fg_color="#2a3f6f")
            else:
                frame.configure(fg_color="#1c2d4d" if i % 2 == 0 else "transparent")

            if self._tf_row_cache[i] == vals:
                continue
            self._tf_row_cache[i] = vals
            for lbl, (val, color) in zip(labels, vals):
                lbl.configure(text=val, text_color=color)

    def _build_tf_row(self, tf: str, data: dict) -> list[tuple[str, str]]:
        """Build cell values for a single timeframe row."""
        ind = data["indicators"]
        conf = data["confluence"]

        details = conf.get("details", {})
        bullish = conf.get("bullish_count", 0)
        bearish = conf.get("bearish_count", 0)
        conf_total = conf.get("score", 0)
        active_grp = conf.get("active_group", "")
        signal = conf.get("signal", "NEUTRAL")

        # Price
        price = ind.get("Price", 0)
        if price > 0:
            fmt = ".6f" if price < 1 else (".4f" if price < 100 else ".2f")
            price_str = f"{price:{fmt}}"
        else:
            price_str = "--"

        # ATR%
        atr = ind.get("ATR", 0)
        atr_pct = (atr / price * 100) if (price > 0 and atr > 0) else 0
        atr_color = "#FF9800" if atr_pct > 0.5 else "#2196F3" if atr_pct > 0 else "gray"

        # Indicator cells
        ind_cells = []
        for key in CONF_INDICATORS:
            vk = CONF_VALUE_KEYS.get(key)
            raw = ind.get(vk) if vk else None
            ind_cells.append(_conf_detail_cell(details.get(key, 0), raw))

        # Conf column
        grp_tag = {"TREND": "T", "REVERSION": "R", "BOTH": "B",
                   "CONFLICT": "!", "NEUTRAL": "-"}.get(active_grp, "")
        conf_str = f"{conf_total:+.1f}{grp_tag}"
        if active_grp == "CONFLICT":
            conf_color = "#FF9800"
        elif conf_total >= 4:
            conf_color = "#00C853"
        elif conf_total <= -4:
            conf_color = "#FF1744"
        else:
            conf_color = "white"

        # Signal
        signal_colors = {"BUY": "#00C853", "SELL": "#FF1744", "NEUTRAL": "gray"}
        signal_str = {"BUY": "AL", "SELL": "SAT", "NEUTRAL": "BEKLE"}.get(signal, "--")

        # Active group
        grp_colors = {"TREND": "#4FC3F7", "REVERSION": "#CE93D8", "BOTH": "#00E676",
                      "CONFLICT": "#FF9800", "NEUTRAL": "gray"}

        vals = [
            (tf, "#64B5F6"),
            (price_str, "white"),
            (f"{atr_pct:.2f}" if atr_pct > 0 else "--", atr_color),
        ] + ind_cells + [
            (conf_str, conf_color),
            (f"{bullish}", "#00C853" if bullish > 0 else "gray"),
            (f"{bearish}", "#FF1744" if bearish > 0 else "gray"),
            (signal_str, signal_colors.get(signal, "gray")),
            (active_grp[:6] if active_grp else "--", grp_colors.get(active_grp, "gray")),
        ]
        return vals

    # ═══════════════════════════════════════════════════════════════════════
    # TIMEFRAME CLICK -> TIME-SERIES TABLE
    # ═══════════════════════════════════════════════════════════════════════

    def _on_tf_click(self, tf: str) -> None:
        """Handle timeframe row click."""
        self._selected_tf = tf
        self._update_tf_table()  # update highlight
        self._draw_chart()

        data = self._tf_data.get(tf)
        if not data:
            self._ts_title_lbl.configure(
                text=f"Indikator Zaman Serisi - {tf} (veri yok)")
            return

        # Lazy history: compute only when TF is clicked (not for all 13 TFs)
        if data.get("history") is None:
            self._ts_title_lbl.configure(
                text=f"Indikator Zaman Serisi - {tf} (hesaplaniyor...)")
            threading.Thread(
                target=self._compute_history_lazy, args=(tf,), daemon=True
            ).start()
        else:
            self._show_timeseries(tf)

    def _compute_history_lazy(self, tf: str) -> None:
        """Background: compute history snapshots for a single TF (20 candles)."""
        data = self._tf_data.get(tf)
        if not data or data.get("history") is not None:
            return
        try:
            df = data["klines"]
            engine = self._create_engine()
            confluence = ConfluenceScorer(threshold=4.0, config=self.controller.config)
            regime_detector = MarketRegimeDetector()

            history = []
            n_hist = min(HISTORY_CANDLES, len(df) - 55)
            if n_hist > 0:
                for j in range(n_hist):
                    slice_end = len(df) - (n_hist - 1 - j)
                    slice_df = df.iloc[:slice_end]
                    if len(slice_df) < 30:
                        continue
                    h_ind = engine.compute_all(slice_df)
                    h_regime = regime_detector.detect(h_ind)
                    h_rw = h_regime.get("indicator_weights", {})
                    h_conf = confluence.score(h_ind, h_rw)
                    h_ind["_conf_score"] = h_conf.get("score", 0)
                    h_ind["_conf_signal"] = h_conf.get("signal", "NEUTRAL")
                    h_ind["_active_group"] = h_conf.get("active_group", "NEUTRAL")
                    history.append({
                        "timestamp": df["timestamp"].iloc[slice_end - 1],
                        "indicators": h_ind,
                    })
            data["history"] = history
            self.after(0, lambda: self._show_timeseries(tf))
        except Exception as e:
            logger.debug(f"History compute error for {tf}: {e}")
            data["history"] = []

    def _show_timeseries(self, tf: str) -> None:
        """Show time-series indicator table for selected timeframe."""
        data = self._tf_data.get(tf)
        if not data:
            self._ts_title_lbl.configure(
                text=f"Indikator Zaman Serisi - {tf} (veri yok)")
            return

        history = data.get("history") or []
        self._ts_title_lbl.configure(
            text=f"Indikator Zaman Serisi - {self._symbol_var.get()} {tf} "
                 f"(son {len(history)} mum)")

        # Clear old headers and rows
        for w in self._ts_hdr_frame.winfo_children():
            w.destroy()
        for frame, _ in self._ts_rows:
            frame.destroy()
        self._ts_rows.clear()

        if not history:
            return

        # Build headers: [Indikator, t-N, t-(N-1), ..., t-0]
        n_cols = len(history)
        ind_name_w = 90
        cell_w = max(55, min(75, 900 // max(n_cols, 1)))

        # Header row
        ctk.CTkLabel(self._ts_hdr_frame, text="Indikator", width=ind_name_w,
                     font=ctk.CTkFont(size=11, weight="bold"),
                     text_color="#7799BB").pack(side="left", padx=0)

        for j, entry in enumerate(history):
            ts = entry["timestamp"]
            if isinstance(ts, pd.Timestamp):
                t_str = ts.strftime("%H:%M")
            else:
                try:
                    t_str = pd.Timestamp(ts).strftime("%H:%M")
                except Exception:
                    t_str = f"t-{n_cols - 1 - j}"
            ctk.CTkLabel(self._ts_hdr_frame, text=t_str, width=cell_w,
                         font=ctk.CTkFont(size=10, weight="bold"),
                         text_color="#7799BB").pack(side="left", padx=0)

        # Data rows
        font = ctk.CTkFont(size=10)
        for row_idx, (disp_name, ind_key, fmt_str) in enumerate(TIMESERIES_INDICATORS):
            bg = "#1c2d4d" if row_idx % 2 == 0 else "transparent"
            row_frame = ctk.CTkFrame(self._ts_scroll, fg_color=bg)
            row_frame.pack(fill="x", pady=0)

            # Indicator name
            name_color = "#4FC3F7" if ind_key in ("ADX", "ADX_plus_DI", "ADX_minus_DI",
                                                   "EMA_fast", "EMA50", "SMA_slow",
                                                   "MACD_histogram", "MACD_line", "MACD_signal",
                                                   "SR_position") \
                else "#CE93D8" if ind_key in ("RSI", "BB_PercentB", "BB_Upper", "BB_Lower") \
                else "#FFD54F" if ind_key in ("OBV_slope", "CMF", "CVD_normalized", "VWAP") \
                else "#7799BB"
            name_lbl = ctk.CTkLabel(row_frame, text=disp_name, width=ind_name_w,
                                    font=ctk.CTkFont(size=10, weight="bold"),
                                    text_color=name_color, anchor="w")
            name_lbl.pack(side="left", padx=0)

            labels = [name_lbl]
            prev_val = None

            for j, entry in enumerate(history):
                ind_vals = entry["indicators"]
                val = ind_vals.get(ind_key)
                text = _format_num(val, fmt_str)

                # Color based on trend direction
                color = "white"
                if isinstance(val, (int, float)) and prev_val is not None and isinstance(prev_val, (int, float)):
                    if ind_key == "RSI":
                        color = CLR_GREEN if val < 30 else CLR_RED if val > 70 else "white"
                    elif ind_key in ("_conf_score", "MACD_histogram", "OBV_slope", "CMF", "CVD_normalized"):
                        if val > prev_val:
                            color = "#81C784"  # light green = improving
                        elif val < prev_val:
                            color = "#EF5350"  # light red = declining
                    elif ind_key == "BB_PercentB":
                        color = CLR_GREEN if val < 0.2 else CLR_RED if val > 0.8 else "white"
                    elif ind_key == "ADX":
                        color = CLR_ORANGE if val > 25 else "gray" if val < 18 else "white"
                elif isinstance(val, str):
                    if val in ("BUY", "AL"):
                        color = CLR_GREEN
                    elif val in ("SELL", "SAT"):
                        color = CLR_RED
                    elif val in ("CONFLICT",):
                        color = CLR_ORANGE
                    elif val in ("NEAR_SUPPORT", "BREAKOUT"):
                        color = CLR_GREEN
                    elif val in ("NEAR_RESISTANCE",):
                        color = CLR_RED
                    elif val in ("TREND", "BOTH"):
                        color = CLR_BLUE

                prev_val = val

                lbl = ctk.CTkLabel(row_frame, text=text, width=cell_w,
                                   font=font, text_color=color)
                lbl.pack(side="left", padx=0)
                labels.append(lbl)

            self._ts_rows.append((row_frame, labels))

    # ═══════════════════════════════════════════════════════════════════════
    # PRICE + VOLUME CHART
    # ═══════════════════════════════════════════════════════════════════════

    def _draw_chart(self) -> None:
        """Draw price candlestick + volume chart for selected TF."""
        # Cleanup old
        if self._canvas:
            self._canvas.get_tk_widget().destroy()
            self._canvas = None
        if self._fig:
            import matplotlib.pyplot as plt
            plt.close(self._fig)
            self._fig = None

        data = self._tf_data.get(self._selected_tf)
        if not data or data["klines"] is None:
            return

        df = data["klines"]
        # Show last 100 candles
        df = df.tail(100).reset_index(drop=True)
        if len(df) < 5:
            return

        fig = Figure(figsize=(18, 2.8), facecolor=CLR_BG, dpi=80)
        fig.subplots_adjust(left=0.03, right=0.998, top=0.92, bottom=0.08, hspace=0.05)
        gs = GridSpec(2, 1, figure=fig, height_ratios=[3, 1], hspace=0.05)

        ax_price = fig.add_subplot(gs[0, 0])
        ax_vol = fig.add_subplot(gs[1, 0], sharex=ax_price)

        for ax in (ax_price, ax_vol):
            ax.set_facecolor(CLR_SUBPLOT)
            ax.tick_params(colors=CLR_TEXT, labelsize=6, pad=1)
            ax.grid(True, color=CLR_GRID, alpha=0.3, linewidth=0.5)
            for spine in ax.spines.values():
                spine.set_color(CLR_GRID)

        ax_price.tick_params(labelbottom=False)

        x = np.arange(len(df))
        opens = df["open"].values
        highs = df["high"].values
        lows = df["low"].values
        closes = df["close"].values
        volumes = df["volume"].values

        # Candlesticks
        for i in range(len(x)):
            color = CLR_GREEN if closes[i] >= opens[i] else CLR_RED
            ax_price.plot([x[i], x[i]], [lows[i], highs[i]], color=color, linewidth=0.6)
            body_bottom = min(opens[i], closes[i])
            body_height = abs(closes[i] - opens[i])
            if body_height < (highs[i] - lows[i]) * 0.01:
                body_height = (highs[i] - lows[i]) * 0.01
            ax_price.bar(x[i], body_height, bottom=body_bottom, width=0.6,
                         color=color, edgecolor=color, linewidth=0.5)

        # Volume bars
        vol_colors = [CLR_GREEN if closes[i] >= opens[i] else CLR_RED for i in range(len(x))]
        ax_vol.bar(x, volumes, color=vol_colors, alpha=0.5, width=0.7)

        # X-axis labels
        n = len(x)
        step = max(1, n // 12)
        tick_pos = list(range(0, n, step))
        tick_labels = []
        for p in tick_pos:
            try:
                t = pd.Timestamp(df["timestamp"].iloc[p])
                tick_labels.append(t.strftime("%m/%d\n%H:%M"))
            except Exception:
                tick_labels.append("")
        ax_vol.set_xticks(tick_pos)
        ax_vol.set_xticklabels(tick_labels, fontsize=6, color=CLR_TEXT)

        symbol = self._symbol_var.get()
        ax_price.set_title(f"{symbol} - {self._selected_tf} (son {len(df)} mum)",
                           fontsize=9, color=CLR_TEXT, loc="left", pad=2)

        self._fig = fig
        self._canvas = FigureCanvasTkAgg(fig, master=self._chart_frame)
        widget = self._canvas.get_tk_widget()
        widget.pack(fill="both", expand=True)
        self._canvas.draw_idle()

    # ═══════════════════════════════════════════════════════════════════════
    # CLEANUP
    # ═══════════════════════════════════════════════════════════════════════

    def destroy(self):
        if self._canvas:
            self._canvas.get_tk_widget().destroy()
            self._canvas = None
        if self._fig:
            import matplotlib.pyplot as plt
            plt.close(self._fig)
            self._fig = None
        super().destroy()
