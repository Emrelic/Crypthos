"""Indicator Analysis Panel - comprehensive chart-based indicator and confluence visualization.

Provides static (one-shot) and live (auto-refresh) modes for analyzing
indicator values and confluence scores over time for any Binance Futures symbol.
"""

import threading
import time
from datetime import datetime, timedelta

import customtkinter as ctk
import numpy as np
import pandas as pd
from loguru import logger
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.dates import AutoDateLocator, DateFormatter
from matplotlib.figure import Figure
from matplotlib.gridspec import GridSpec

from analysis.confluence import ConfluenceScorer, GROUP_NEUTRAL_THRESHOLD
from analysis.market_regime import MarketRegimeDetector
from indicators.indicator_engine import IndicatorEngine

# ── Color constants ──────────────────────────────────────────────────────────
CLR_BG = "#1a1a2e"
CLR_SUBPLOT = "#16213e"
CLR_GRID = "#333333"
CLR_GREEN = "#00C853"
CLR_RED = "#FF1744"
CLR_BLUE = "#2196F3"
CLR_ORANGE = "#FF9800"
CLR_PURPLE = "#9C27B0"
CLR_GRAY = "#9E9E9E"
CLR_LIGHT_GREEN = "#81C784"
CLR_LIGHT_RED = "#EF5350"
CLR_TEXT = "#E0E0E0"

DEFAULT_SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT",
    "DOGEUSDT", "ADAUSDT", "AVAXUSDT", "DOTUSDT", "LINKUSDT",
    "MATICUSDT", "UNIUSDT", "NEARUSDT", "FILUSDT", "AAVEUSDT",
    "APTUSDT", "ARBUSDT", "OPUSDT", "SEIUSDT", "SUIUSDT",
]

TIMEFRAMES = ["1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "1d"]

# Candle interval -> seconds mapping for live mode
_INTERVAL_SECONDS = {
    "1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800,
    "1h": 3600, "2h": 7200, "4h": 14400, "1d": 86400,
}


class _ParamConfig:
    """Lightweight config wrapper accepted by IndicatorEngine."""

    def __init__(self, indicator_params: dict):
        self._params = indicator_params

    def get(self, key: str, default=None):
        if key == "indicators":
            return self._params
        # Support dot-notation for nested keys
        parts = key.split(".")
        if parts[0] == "indicators" and len(parts) == 2:
            return self._params.get(parts[1], default)
        return default


class IndicatorAnalysisPanel(ctk.CTkFrame):
    """Full-featured indicator analysis panel with matplotlib charts."""

    def __init__(self, parent, controller, **kwargs):
        super().__init__(parent, **kwargs)
        self.controller = controller

        # State
        self._ts_df: pd.DataFrame | None = None  # timeseries result
        self._live_running = False
        self._live_thread: threading.Thread | None = None
        self._computation_lock = threading.Lock()
        self._fig: Figure | None = None
        self._canvas: FigureCanvasTkAgg | None = None
        self._progress_var = ctk.StringVar(value="")

        # Parameter variables (set defaults before building UI)
        self._param_vars: dict[str, ctk.StringVar] = {}
        self._init_param_vars()

        # Build UI
        self._build_controls()
        self._build_param_section()
        self._build_chart_area()

    # ══════════════════════════════════════════════════════════════════════════
    # PARAMETER DEFAULTS
    # ══════════════════════════════════════════════════════════════════════════

    def _init_param_vars(self):
        defaults = {
            "RSI Period": "14",
            "MACD Fast": "12",
            "MACD Slow": "26",
            "MACD Signal": "9",
            "ADX Period": "14",
            "BB Period": "20",
            "BB Std": "2.0",
            "Supertrend Period": "10",
            "Supertrend Mult": "3.0",
            "Ichimoku Tenkan": "9",
            "Ichimoku Kijun": "26",
            "Ichimoku Senkou": "52",
            "StochRSI Period": "14",
            "MFI Period": "14",
            "CMF Period": "20",
            "SMA Slow": "200",
        }
        for k, v in defaults.items():
            self._param_vars[k] = ctk.StringVar(value=v)

    # ══════════════════════════════════════════════════════════════════════════
    # UI BUILDING
    # ══════════════════════════════════════════════════════════════════════════

    def _build_controls(self):
        bar = ctk.CTkFrame(self, height=40)
        bar.pack(fill="x", padx=6, pady=(6, 2))

        # Symbol
        ctk.CTkLabel(bar, text="Coin:", width=35).pack(side="left", padx=(4, 2))
        self._symbol_var = ctk.StringVar(value="BTCUSDT")
        self._symbol_cb = ctk.CTkComboBox(
            bar, variable=self._symbol_var, values=DEFAULT_SYMBOLS,
            width=120, state="normal"
        )
        self._symbol_cb.pack(side="left", padx=2)

        # Timeframe
        ctk.CTkLabel(bar, text="TF:", width=25).pack(side="left", padx=(8, 2))
        self._tf_var = ctk.StringVar(value="15m")
        ctk.CTkComboBox(
            bar, variable=self._tf_var, values=TIMEFRAMES, width=70
        ).pack(side="left", padx=2)

        # Candle count
        ctk.CTkLabel(bar, text="Mum:", width=35).pack(side="left", padx=(8, 2))
        self._count_var = ctk.StringVar(value="200")
        ctk.CTkEntry(bar, textvariable=self._count_var, width=55).pack(side="left", padx=2)

        # Mode selector
        ctk.CTkLabel(bar, text="Mod:", width=30).pack(side="left", padx=(8, 2))
        self._mode_var = ctk.StringVar(value="Statik")
        self._mode_cb = ctk.CTkComboBox(
            bar, variable=self._mode_var, values=["Statik", "Canli"],
            width=80, command=self._on_mode_changed
        )
        self._mode_cb.pack(side="left", padx=2)

        # Update mode (live only)
        self._update_label = ctk.CTkLabel(bar, text="Guncelleme:", width=75)
        self._update_var = ctk.StringVar(value="Her 5 sn")
        self._update_cb = ctk.CTkComboBox(
            bar, variable=self._update_var, values=["Her 5 sn", "Mum Kapanisi"],
            width=100
        )

        # Analiz Et button (static)
        self._analyze_btn = ctk.CTkButton(
            bar, text="Analiz Et", width=80, command=self._on_analyze_click,
            fg_color="#0D47A1"
        )
        self._analyze_btn.pack(side="left", padx=(12, 2))

        # Start/Stop button (live)
        self._live_btn = ctk.CTkButton(
            bar, text="Baslat", width=70, command=self._on_live_toggle,
            fg_color="#1B5E20"
        )

        # Progress label
        self._progress_lbl = ctk.CTkLabel(bar, textvariable=self._progress_var, width=180)
        self._progress_lbl.pack(side="right", padx=6)

        self._on_mode_changed(self._mode_var.get())

    def _on_mode_changed(self, mode: str):
        if mode == "Canli":
            self._analyze_btn.pack_forget()
            self._update_label.pack(side="left", padx=(8, 2))
            self._update_cb.pack(side="left", padx=2)
            self._live_btn.pack(side="left", padx=(12, 2))
        else:
            self._update_label.pack_forget()
            self._update_cb.pack_forget()
            self._live_btn.pack_forget()
            self._analyze_btn.pack(side="left", padx=(12, 2))
            # Stop live if switching back
            if self._live_running:
                self._stop_live()

    def _build_param_section(self):
        """Collapsible indicator parameter section."""
        self._param_visible = False
        toggle_frame = ctk.CTkFrame(self, height=28)
        toggle_frame.pack(fill="x", padx=6, pady=(2, 0))
        self._param_toggle_btn = ctk.CTkButton(
            toggle_frame, text="Indicator Parametreleri  [+]", width=220,
            height=24, command=self._toggle_params,
            fg_color="transparent", text_color=CLR_TEXT, anchor="w",
            font=ctk.CTkFont(size=12)
        )
        self._param_toggle_btn.pack(side="left", padx=4)

        self._param_frame = ctk.CTkFrame(self)
        # Build param grid inside (not packed yet)
        params_layout = [
            ("RSI Period", "MACD Fast", "MACD Slow", "MACD Signal"),
            ("ADX Period", "BB Period", "BB Std", "SMA Slow"),
            ("Supertrend Period", "Supertrend Mult", "StochRSI Period", "MFI Period"),
            ("Ichimoku Tenkan", "Ichimoku Kijun", "Ichimoku Senkou", "CMF Period"),
        ]
        for row_idx, row_params in enumerate(params_layout):
            for col_idx, name in enumerate(row_params):
                ctk.CTkLabel(
                    self._param_frame, text=f"{name}:", width=110, anchor="e",
                    font=ctk.CTkFont(size=11)
                ).grid(row=row_idx, column=col_idx * 2, padx=(6, 2), pady=2, sticky="e")
                ctk.CTkEntry(
                    self._param_frame, textvariable=self._param_vars[name],
                    width=55, font=ctk.CTkFont(size=11)
                ).grid(row=row_idx, column=col_idx * 2 + 1, padx=(0, 8), pady=2, sticky="w")

    def _toggle_params(self):
        self._param_visible = not self._param_visible
        if self._param_visible:
            self._param_frame.pack(fill="x", padx=6, pady=(0, 2))
            self._param_toggle_btn.configure(text="Indicator Parametreleri  [-]")
        else:
            self._param_frame.pack_forget()
            self._param_toggle_btn.configure(text="Indicator Parametreleri  [+]")

    def _build_chart_area(self):
        """Scrollable chart frame for matplotlib."""
        self._chart_frame = ctk.CTkScrollableFrame(self, fg_color=CLR_BG)
        self._chart_frame.pack(fill="both", expand=True, padx=1, pady=(1, 2))

    # ══════════════════════════════════════════════════════════════════════════
    # ENGINE CREATION
    # ══════════════════════════════════════════════════════════════════════════

    def _create_engine(self) -> IndicatorEngine:
        """Create IndicatorEngine with current UI parameter values."""
        pv = self._param_vars
        params = {
            "rsi_period": int(pv["RSI Period"].get()),
            "macd_fast": int(pv["MACD Fast"].get()),
            "macd_slow": int(pv["MACD Slow"].get()),
            "macd_signal": int(pv["MACD Signal"].get()),
            "ma_fast": 20,
            "ma_slow": int(pv["SMA Slow"].get()),
            "kline_interval": "15m",
            "kline_limit": 500,
        }
        cfg = _ParamConfig(params)
        return IndicatorEngine(cfg)

    # ══════════════════════════════════════════════════════════════════════════
    # DATA FETCHING
    # ══════════════════════════════════════════════════════════════════════════

    def _fetch_klines(self) -> pd.DataFrame | None:
        """Fetch klines from Binance REST API."""
        try:
            rest = self.controller.market_service._rest
            symbol = self._symbol_var.get().strip().upper()
            interval = self._tf_var.get()
            limit = min(int(self._count_var.get()), 1500)
            df = rest.get_klines(symbol, interval, limit)
            if df is None or df.empty:
                return None
            return df
        except Exception as e:
            logger.error(f"Kline fetch error: {e}")
            return None

    # ══════════════════════════════════════════════════════════════════════════
    # TIMESERIES COMPUTATION
    # ══════════════════════════════════════════════════════════════════════════

    def _compute_timeseries(self, df: pd.DataFrame, min_warmup: int = 55,
                            progress_cb=None) -> pd.DataFrame:
        """Compute indicator values and confluence scores for each candle."""
        engine = self._create_engine()
        confluence = ConfluenceScorer(threshold=4.0)
        regime_detector = MarketRegimeDetector()

        total = len(df) - min_warmup
        records = []

        for i in range(min_warmup, len(df)):
            if progress_cb and (i - min_warmup) % 5 == 0:
                pct = int(((i - min_warmup) / max(total, 1)) * 100)
                progress_cb(pct)

            slice_df = df.iloc[: i + 1].copy()
            try:
                ind = engine.compute_all(slice_df)
            except Exception:
                continue

            regime = regime_detector.detect(ind)
            rw = regime.get("indicator_weights", {})
            conf = confluence.score(ind, rw)

            record = {
                "timestamp": df["timestamp"].iloc[i],
                "open": df["open"].iloc[i],
                "high": df["high"].iloc[i],
                "low": df["low"].iloc[i],
                "close": df["close"].iloc[i],
                "volume": df["volume"].iloc[i],
                # Raw indicator values
                "RSI": ind.get("RSI", 50),
                "MACD_line": ind.get("MACD_line", 0),
                "MACD_signal": ind.get("MACD_signal", 0),
                "MACD_histogram": ind.get("MACD_histogram", 0),
                "MACD_bullish_cross": ind.get("MACD_bullish_cross", False),
                "MACD_bearish_cross": ind.get("MACD_bearish_cross", False),
                "ADX": ind.get("ADX", 0),
                "ADX_plus_DI": ind.get("ADX_plus_DI", 0),
                "ADX_minus_DI": ind.get("ADX_minus_DI", 0),
                "Supertrend_trend": ind.get("Supertrend_trend", ""),
                "PSAR_trend": ind.get("PSAR_trend", ""),
                "Ichimoku_Position": ind.get("Ichimoku_Position", ""),
                "Price": ind.get("Price", 0),
                "SMA_slow": ind.get("SMA_slow", 0),
                "StochRSI_K": ind.get("StochRSI_K", 50),
                "StochRSI_D": ind.get("StochRSI_D", 50),
                "MFI": ind.get("MFI", 50),
                "BB_PercentB": ind.get("BB_PercentB", 0.5),
                "OBV_slope": ind.get("OBV_slope", 0),
                "CMF": ind.get("CMF", 0),
                # Confluence scores
                "trend_score": conf.get("trend_score", 0),
                "reversion_score": conf.get("reversion_score", 0),
                "volume_score": conf.get("volume_score", 0),
                "confluence_score": conf.get("score", 0),
                "active_group": conf.get("active_group", "NEUTRAL"),
                "signal": conf.get("signal", "NEUTRAL"),
                # Per-indicator scores
                **{f"score_{k}": v for k, v in conf.get("trend_details", {}).items()},
                **{f"score_{k}": v for k, v in conf.get("reversion_details", {}).items()},
                **{f"score_{k}": v for k, v in conf.get("volume_details", {}).items()},
                # Regime
                "regime": regime.get("regime", "UNKNOWN"),
                "regime_confidence": regime.get("confidence", 0),
                "trend_direction": regime.get("trend_direction", "NONE"),
            }
            records.append(record)

        if progress_cb:
            progress_cb(100)

        return pd.DataFrame(records)

    # ══════════════════════════════════════════════════════════════════════════
    # STATIC MODE
    # ══════════════════════════════════════════════════════════════════════════

    def _on_analyze_click(self):
        self._analyze_btn.configure(state="disabled")
        self._progress_var.set("Veriler aliniyor...")
        t = threading.Thread(target=self._run_static_analysis, daemon=True)
        t.start()

    def _run_static_analysis(self):
        try:
            df = self._fetch_klines()
            if df is None or df.empty:
                self.after(0, lambda: self._progress_var.set("Veri alinamadi!"))
                self.after(0, lambda: self._analyze_btn.configure(state="normal"))
                return

            def _progress(pct):
                self.after(0, lambda p=pct: self._progress_var.set(f"Hesaplaniyor... %{p}"))

            with self._computation_lock:
                ts = self._compute_timeseries(df, progress_cb=_progress)

            if ts.empty:
                self.after(0, lambda: self._progress_var.set("Hesaplama basarisiz!"))
                self.after(0, lambda: self._analyze_btn.configure(state="normal"))
                return

            self._ts_df = ts
            self.after(0, self._draw_charts)
            self.after(0, lambda: self._progress_var.set(
                f"{self._symbol_var.get()} | {len(ts)} mum | tamamlandi"
            ))
        except Exception as e:
            logger.error(f"Static analysis error: {e}")
            self.after(0, lambda: self._progress_var.set(f"Hata: {e}"))
        finally:
            self.after(0, lambda: self._analyze_btn.configure(state="normal"))

    # ══════════════════════════════════════════════════════════════════════════
    # LIVE MODE
    # ══════════════════════════════════════════════════════════════════════════

    def _on_live_toggle(self):
        if self._live_running:
            self._stop_live()
        else:
            self._start_live()

    def _start_live(self):
        self._live_running = True
        self._live_btn.configure(text="Durdur", fg_color="#B71C1C")
        self._live_thread = threading.Thread(target=self._live_loop, daemon=True)
        self._live_thread.start()

    def _stop_live(self):
        self._live_running = False
        self._live_btn.configure(text="Baslat", fg_color="#1B5E20")

    def _live_loop(self):
        """Background loop for live mode."""
        first_run = True
        while self._live_running:
            try:
                self.after(0, lambda: self._progress_var.set("Canli guncelleme..."))
                df = self._fetch_klines()
                if df is None or df.empty:
                    self.after(0, lambda: self._progress_var.set("Veri alinamadi, tekrar deneniyor..."))
                    time.sleep(5)
                    continue

                if first_run:
                    # Full computation on first run
                    def _progress(pct):
                        self.after(0, lambda p=pct: self._progress_var.set(f"Ilk hesaplama... %{p}"))

                    with self._computation_lock:
                        ts = self._compute_timeseries(df, progress_cb=_progress)
                    first_run = False
                else:
                    # Incremental: recompute only last 5 candles for speed
                    warmup = max(55, len(df) - 5)
                    with self._computation_lock:
                        ts_new = self._compute_timeseries(df, min_warmup=warmup)
                    if self._ts_df is not None and not ts_new.empty:
                        # Merge: keep old data, replace/append new candles
                        old = self._ts_df
                        cutoff = ts_new["timestamp"].iloc[0]
                        old_keep = old[old["timestamp"] < cutoff]
                        ts = pd.concat([old_keep, ts_new], ignore_index=True)
                    else:
                        ts = ts_new

                if ts.empty:
                    time.sleep(5)
                    continue

                self._ts_df = ts
                self.after(0, self._draw_charts)
                now = datetime.now().strftime("%H:%M:%S")
                self.after(0, lambda t=now: self._progress_var.set(
                    f"Canli | {self._symbol_var.get()} | {len(self._ts_df)} mum | {t}"
                ))

                # Sleep based on update mode
                if self._update_var.get() == "Mum Kapanisi":
                    interval_sec = _INTERVAL_SECONDS.get(self._tf_var.get(), 60)
                    # Sleep until next candle close + 2s buffer
                    now_ts = time.time()
                    next_close = (int(now_ts / interval_sec) + 1) * interval_sec + 2
                    wait = max(next_close - now_ts, 1)
                    # Sleep in small increments to allow stopping
                    for _ in range(int(wait)):
                        if not self._live_running:
                            return
                        time.sleep(1)
                else:
                    # Her 5 sn
                    for _ in range(5):
                        if not self._live_running:
                            return
                        time.sleep(1)

            except Exception as e:
                logger.error(f"Live loop error: {e}")
                self.after(0, lambda: self._progress_var.set(f"Hata: {e}"))
                time.sleep(5)

    # ══════════════════════════════════════════════════════════════════════════
    # CHART DRAWING
    # ══════════════════════════════════════════════════════════════════════════

    def _draw_charts(self):
        """Main chart drawing routine -- runs on the UI thread."""
        ts = self._ts_df
        if ts is None or ts.empty:
            return

        # Destroy old canvas
        if self._canvas:
            self._canvas.get_tk_widget().destroy()
            self._canvas = None
        if self._fig:
            import matplotlib.pyplot as plt
            plt.close(self._fig)
            self._fig = None

        # Prepare data
        timestamps = ts["timestamp"].values
        x = np.arange(len(timestamps))

        # ── Figure with GridSpec ──────────────────────────────────────────
        # Height ratios: price=4, indicators=1 each (12), group totals=1.5 (3),
        # grand total=2, regime=0.5
        # Subplots: price, MACD, ADX, Supertrend, PSAR, Ichimoku, PriceSMA,
        #   trend_total, RSI, StochRSI, MFI, BB, rev_total, OBV, CMF, vol_total,
        #   grand_total, regime = 18 subplots
        n_sub = 18
        ratios = [4, 1, 1, 1, 1, 1, 1, 1.5, 1, 1, 1, 1, 1.5, 1, 1, 1.5, 2, 0.5]
        total_height = sum(ratios) * 1.3  # ~1.3 inches per ratio unit

        fig = Figure(figsize=(20, total_height), facecolor=CLR_BG, dpi=80)
        fig.subplots_adjust(left=0.025, right=0.998, top=0.997, bottom=0.008,
                            hspace=0.35)
        gs = GridSpec(n_sub, 1, figure=fig, height_ratios=ratios, hspace=0.35)

        axes = []
        for i in range(n_sub):
            ax = fig.add_subplot(gs[i, 0])
            ax.set_facecolor(CLR_SUBPLOT)
            ax.tick_params(colors=CLR_TEXT, labelsize=6, pad=1)
            ax.yaxis.set_tick_params(labelsize=6, pad=1)
            ax.grid(True, color=CLR_GRID, alpha=0.3, linewidth=0.5)
            for spine in ax.spines.values():
                spine.set_color(CLR_GRID)
            axes.append(ax)

        # Share x axis
        for ax in axes[1:]:
            ax.sharex(axes[0])

        # Hide x labels except last
        for ax in axes[:-1]:
            ax.tick_params(labelbottom=False)

        # Format x-axis on last subplot
        self._format_xaxis(axes[-1], timestamps, x)

        # ── Draw subplots ─────────────────────────────────────────────────
        self._draw_price(axes[0], ts, x)
        self._draw_macd(axes[1], ts, x)
        self._draw_adx(axes[2], ts, x)
        self._draw_supertrend(axes[3], ts, x)
        self._draw_psar(axes[4], ts, x)
        self._draw_ichimoku(axes[5], ts, x)
        self._draw_price_sma(axes[6], ts, x)
        self._draw_group_total(axes[7], ts, x, "trend_score", "TREND TOPLAM")
        self._draw_rsi(axes[8], ts, x)
        self._draw_stochrsi(axes[9], ts, x)
        self._draw_mfi(axes[10], ts, x)
        self._draw_bb(axes[11], ts, x)
        self._draw_group_total(axes[12], ts, x, "reversion_score", "MEAN-REV TOPLAM")
        self._draw_obv(axes[13], ts, x)
        self._draw_cmf(axes[14], ts, x)
        self._draw_group_total(axes[15], ts, x, "volume_score", "VOLUME TOPLAM")
        self._draw_grand_total(axes[16], ts, x)
        self._draw_regime(axes[17], ts, x)

        self._fig = fig
        self._canvas = FigureCanvasTkAgg(fig, master=self._chart_frame)
        widget = self._canvas.get_tk_widget()
        widget.configure(height=int(total_height * 80))  # dpi=80
        widget.pack(fill="x", expand=False)
        self._canvas.draw_idle()

    # ── X-axis formatting ────────────────────────────────────────────────

    def _format_xaxis(self, ax, timestamps, x):
        """Use timestamp labels on integer x positions."""
        n = len(x)
        if n == 0:
            return
        # Show ~12 labels
        step = max(1, n // 12)
        tick_pos = list(range(0, n, step))
        tick_labels = []
        for p in tick_pos:
            t = pd.Timestamp(timestamps[p])
            tick_labels.append(t.strftime("%m/%d\n%H:%M"))
        ax.set_xticks(tick_pos)
        ax.set_xticklabels(tick_labels, fontsize=7, color=CLR_TEXT)

    # ── Subplot helper: title ────────────────────────────────────────────

    def _set_title(self, ax, text, score_text=None):
        title = text
        if score_text is not None:
            title += f"  [{score_text}]"
        ax.set_title(title, fontsize=8, color=CLR_TEXT, loc="left", pad=2)

    def _get_last_score(self, ts, key):
        val = ts[key].iloc[-1] if key in ts.columns else 0
        return val

    # ── Signal A/S markers helper ─────────────────────────────────────────

    def _draw_signal_markers(self, ax, ts, x):
        """Draw green 'A' (AL/BUY) and red 'S' (SAT/SELL) markers at top of subplot."""
        if "signal" not in ts.columns:
            return
        signals = ts["signal"].values
        ymin, ymax = ax.get_ylim()
        marker_y = ymax - (ymax - ymin) * 0.08  # 8% from top

        for i in range(len(x)):
            if signals[i] == "BUY":
                ax.text(x[i], marker_y, "A", color=CLR_GREEN, fontsize=6,
                        fontweight="bold", ha="center", va="top", alpha=0.85)
            elif signals[i] == "SELL":
                ax.text(x[i], marker_y, "S", color=CLR_RED, fontsize=6,
                        fontweight="bold", ha="center", va="top", alpha=0.85)

    # ══════════════════════════════════════════════════════════════════════════
    # INDIVIDUAL SUBPLOT DRAWING
    # ══════════════════════════════════════════════════════════════════════════

    def _draw_price(self, ax, ts, x):
        """Candlestick chart with SMA overlay."""
        opens = ts["open"].values
        highs = ts["high"].values
        lows = ts["low"].values
        closes = ts["close"].values
        sma = ts["SMA_slow"].values

        # Draw candles
        for i in range(len(x)):
            color = CLR_GREEN if closes[i] >= opens[i] else CLR_RED
            # Wick
            ax.plot([x[i], x[i]], [lows[i], highs[i]], color=color, linewidth=0.6)
            # Body
            body_bottom = min(opens[i], closes[i])
            body_height = abs(closes[i] - opens[i])
            if body_height < (highs[i] - lows[i]) * 0.01:
                body_height = (highs[i] - lows[i]) * 0.01
            ax.bar(x[i], body_height, bottom=body_bottom, width=0.6,
                   color=color, edgecolor=color, linewidth=0.5)

        # SMA overlay
        valid_sma = sma > 0
        if valid_sma.any():
            ax.plot(x[valid_sma], sma[valid_sma], color=CLR_ORANGE, linewidth=1.0,
                    alpha=0.8, label=f"SMA {self._param_vars['SMA Slow'].get()}")
            ax.legend(loc="upper left", fontsize=7, facecolor=CLR_SUBPLOT,
                      edgecolor=CLR_GRID, labelcolor=CLR_TEXT)

        self._set_title(ax, f"FIYAT - {self._symbol_var.get()} ({self._tf_var.get()})")
        self._draw_signal_markers(ax, ts, x)

    def _draw_macd(self, ax, ts, x):
        hist = ts["MACD_histogram"].values
        macd_line = ts["MACD_line"].values
        sig_line = ts["MACD_signal"].values

        colors = [CLR_GREEN if h >= 0 else CLR_RED for h in hist]
        ax.bar(x, hist, color=colors, alpha=0.6, width=0.7)
        ax.plot(x, macd_line, color=CLR_BLUE, linewidth=0.8, label="MACD")
        ax.plot(x, sig_line, color=CLR_ORANGE, linewidth=0.8, label="Signal")
        ax.axhline(0, color=CLR_GRAY, linewidth=0.5, alpha=0.5)

        # Score background
        if "score_MACD" in ts.columns:
            self._draw_score_bg(ax, ts, x, "score_MACD")

        score = self._get_last_score(ts, "score_MACD")
        self._set_title(ax, "MACD", f"skor: {score:+.1f}")
        ax.legend(loc="upper left", fontsize=6, facecolor=CLR_SUBPLOT,
                  edgecolor=CLR_GRID, labelcolor=CLR_TEXT)
        self._draw_signal_markers(ax, ts, x)

    def _draw_adx(self, ax, ts, x):
        ax.plot(x, ts["ADX"].values, color=CLR_BLUE, linewidth=1.0, label="ADX")
        ax.plot(x, ts["ADX_plus_DI"].values, color=CLR_GREEN, linewidth=0.8, label="+DI")
        ax.plot(x, ts["ADX_minus_DI"].values, color=CLR_RED, linewidth=0.8, label="-DI")
        ax.axhline(22, color=CLR_GRAY, linewidth=0.5, linestyle="--", alpha=0.5)
        ax.axhline(30, color=CLR_ORANGE, linewidth=0.5, linestyle="--", alpha=0.5)

        if "score_ADX" in ts.columns:
            self._draw_score_bg(ax, ts, x, "score_ADX")

        score = self._get_last_score(ts, "score_ADX")
        self._set_title(ax, "ADX", f"skor: {score:+.1f}")
        ax.legend(loc="upper left", fontsize=6, facecolor=CLR_SUBPLOT,
                  edgecolor=CLR_GRID, labelcolor=CLR_TEXT)
        self._draw_signal_markers(ax, ts, x)

    def _draw_supertrend(self, ax, ts, x):
        trends = ts["Supertrend_trend"].values
        for i in range(len(x)):
            color = CLR_GREEN if trends[i] == "UP" else (CLR_RED if trends[i] == "DOWN" else CLR_GRAY)
            ax.axvspan(x[i] - 0.5, x[i] + 0.5, color=color, alpha=0.25)

        if "score_Supertrend" in ts.columns:
            ax.plot(x, ts["score_Supertrend"].values, color=CLR_TEXT, linewidth=0.8)

        score = self._get_last_score(ts, "score_Supertrend")
        self._set_title(ax, "Supertrend", f"skor: {score:+.1f}")
        self._draw_signal_markers(ax, ts, x)

    def _draw_psar(self, ax, ts, x):
        trends = ts["PSAR_trend"].values
        for i in range(len(x)):
            color = CLR_GREEN if trends[i] == "UP" else (CLR_RED if trends[i] == "DOWN" else CLR_GRAY)
            ax.axvspan(x[i] - 0.5, x[i] + 0.5, color=color, alpha=0.25)

        if "score_PSAR" in ts.columns:
            ax.plot(x, ts["score_PSAR"].values, color=CLR_TEXT, linewidth=0.8)

        score = self._get_last_score(ts, "score_PSAR")
        self._set_title(ax, "PSAR", f"skor: {score:+.1f}")
        self._draw_signal_markers(ax, ts, x)

    def _draw_ichimoku(self, ax, ts, x):
        positions = ts["Ichimoku_Position"].values
        color_map = {"ABOVE": CLR_GREEN, "BELOW": CLR_RED, "INSIDE": CLR_ORANGE}
        for i in range(len(x)):
            c = color_map.get(positions[i], CLR_GRAY)
            ax.axvspan(x[i] - 0.5, x[i] + 0.5, color=c, alpha=0.25)

        if "score_Ichimoku" in ts.columns:
            ax.plot(x, ts["score_Ichimoku"].values, color=CLR_TEXT, linewidth=0.8)

        score = self._get_last_score(ts, "score_Ichimoku")
        self._set_title(ax, "Ichimoku", f"skor: {score:+.1f}")
        self._draw_signal_markers(ax, ts, x)

    def _draw_price_sma(self, ax, ts, x):
        price = ts["Price"].values
        sma = ts["SMA_slow"].values
        valid = sma > 0
        diff = np.where(valid, price - sma, 0)
        colors = [CLR_GREEN if d >= 0 else CLR_RED for d in diff]
        ax.bar(x, diff, color=colors, alpha=0.5, width=0.7)
        ax.axhline(0, color=CLR_GRAY, linewidth=0.5)

        if "score_Price_vs_SMA" in ts.columns:
            self._draw_score_bg(ax, ts, x, "score_Price_vs_SMA")

        score = self._get_last_score(ts, "score_Price_vs_SMA")
        self._set_title(ax, "Price vs SMA", f"skor: {score:+.1f}")
        self._draw_signal_markers(ax, ts, x)

    def _draw_group_total(self, ax, ts, x, col, title):
        """Draw group total score with fill."""
        if col not in ts.columns:
            self._set_title(ax, title, "N/A")
            return
        values = ts[col].values
        pos = np.where(values >= 0, values, 0)
        neg = np.where(values < 0, values, 0)
        ax.fill_between(x, 0, pos, color=CLR_GREEN, alpha=0.4, step="mid")
        ax.fill_between(x, 0, neg, color=CLR_RED, alpha=0.4, step="mid")
        ax.plot(x, values, color=CLR_TEXT, linewidth=1.0)
        ax.axhline(0, color=CLR_GRAY, linewidth=0.5)
        ax.axhline(GROUP_NEUTRAL_THRESHOLD, color=CLR_GREEN, linewidth=0.5,
                   linestyle="--", alpha=0.5)
        ax.axhline(-GROUP_NEUTRAL_THRESHOLD, color=CLR_RED, linewidth=0.5,
                   linestyle="--", alpha=0.5)

        last_val = values[-1]
        self._set_title(ax, title, f"{last_val:+.2f}")
        self._draw_signal_markers(ax, ts, x)

    def _draw_rsi(self, ax, ts, x):
        rsi = ts["RSI"].values
        ax.plot(x, rsi, color=CLR_PURPLE, linewidth=1.0)
        ax.axhline(25, color=CLR_GREEN, linewidth=0.5, linestyle="--", alpha=0.5)
        ax.axhline(35, color=CLR_LIGHT_GREEN, linewidth=0.5, linestyle=":", alpha=0.4)
        ax.axhline(65, color=CLR_LIGHT_RED, linewidth=0.5, linestyle=":", alpha=0.4)
        ax.axhline(75, color=CLR_RED, linewidth=0.5, linestyle="--", alpha=0.5)
        ax.fill_between(x, rsi, 25, where=(rsi < 25), color=CLR_GREEN, alpha=0.15)
        ax.fill_between(x, rsi, 75, where=(rsi > 75), color=CLR_RED, alpha=0.15)
        ax.set_ylim(0, 100)

        if "score_RSI" in ts.columns:
            self._draw_score_bg(ax, ts, x, "score_RSI")

        score = self._get_last_score(ts, "score_RSI")
        self._set_title(ax, "RSI", f"skor: {score:+.1f} | RSI: {rsi[-1]:.1f}")
        self._draw_signal_markers(ax, ts, x)

    def _draw_stochrsi(self, ax, ts, x):
        k = ts["StochRSI_K"].values
        d = ts["StochRSI_D"].values
        ax.plot(x, k, color=CLR_BLUE, linewidth=0.8, label="K")
        ax.plot(x, d, color=CLR_ORANGE, linewidth=0.8, label="D")
        ax.axhline(20, color=CLR_GREEN, linewidth=0.5, linestyle="--", alpha=0.5)
        ax.axhline(80, color=CLR_RED, linewidth=0.5, linestyle="--", alpha=0.5)
        ax.set_ylim(0, 100)

        if "score_StochRSI" in ts.columns:
            self._draw_score_bg(ax, ts, x, "score_StochRSI")

        score = self._get_last_score(ts, "score_StochRSI")
        self._set_title(ax, "StochRSI", f"skor: {score:+.1f}")
        ax.legend(loc="upper left", fontsize=6, facecolor=CLR_SUBPLOT,
                  edgecolor=CLR_GRID, labelcolor=CLR_TEXT)
        self._draw_signal_markers(ax, ts, x)

    def _draw_mfi(self, ax, ts, x):
        mfi = ts["MFI"].values
        ax.plot(x, mfi, color=CLR_PURPLE, linewidth=1.0)
        ax.axhline(20, color=CLR_GREEN, linewidth=0.5, linestyle="--", alpha=0.5)
        ax.axhline(80, color=CLR_RED, linewidth=0.5, linestyle="--", alpha=0.5)
        ax.fill_between(x, mfi, 20, where=(mfi < 20), color=CLR_GREEN, alpha=0.15)
        ax.fill_between(x, mfi, 80, where=(mfi > 80), color=CLR_RED, alpha=0.15)
        ax.set_ylim(0, 100)

        if "score_MFI" in ts.columns:
            self._draw_score_bg(ax, ts, x, "score_MFI")

        score = self._get_last_score(ts, "score_MFI")
        self._set_title(ax, "MFI", f"skor: {score:+.1f} | MFI: {mfi[-1]:.1f}")
        self._draw_signal_markers(ax, ts, x)

    def _draw_bb(self, ax, ts, x):
        pctb = ts["BB_PercentB"].values
        ax.plot(x, pctb, color=CLR_PURPLE, linewidth=1.0)
        ax.axhline(0.0, color=CLR_GREEN, linewidth=0.5, linestyle="--", alpha=0.5)
        ax.axhline(0.2, color=CLR_LIGHT_GREEN, linewidth=0.5, linestyle=":", alpha=0.4)
        ax.axhline(0.8, color=CLR_LIGHT_RED, linewidth=0.5, linestyle=":", alpha=0.4)
        ax.axhline(1.0, color=CLR_RED, linewidth=0.5, linestyle="--", alpha=0.5)
        ax.fill_between(x, pctb, 0.0, where=(pctb < 0.0), color=CLR_GREEN, alpha=0.15)
        ax.fill_between(x, pctb, 1.0, where=(pctb > 1.0), color=CLR_RED, alpha=0.15)

        if "score_BB" in ts.columns:
            self._draw_score_bg(ax, ts, x, "score_BB")

        score = self._get_last_score(ts, "score_BB")
        self._set_title(ax, "Bollinger %B", f"skor: {score:+.1f} | %B: {pctb[-1]:.2f}")
        self._draw_signal_markers(ax, ts, x)

    def _draw_obv(self, ax, ts, x):
        slope = ts["OBV_slope"].values
        colors = [CLR_GREEN if s > 0 else (CLR_RED if s < 0 else CLR_GRAY) for s in slope]
        ax.bar(x, slope, color=colors, alpha=0.6, width=0.7)
        ax.axhline(0, color=CLR_GRAY, linewidth=0.5)

        if "score_OBV" in ts.columns:
            self._draw_score_bg(ax, ts, x, "score_OBV")

        score = self._get_last_score(ts, "score_OBV")
        self._set_title(ax, "OBV Slope", f"skor: {score:+.1f}")
        self._draw_signal_markers(ax, ts, x)

    def _draw_cmf(self, ax, ts, x):
        cmf = ts["CMF"].values
        ax.plot(x, cmf, color=CLR_BLUE, linewidth=1.0)
        ax.axhline(0.1, color=CLR_GREEN, linewidth=0.5, linestyle="--", alpha=0.5)
        ax.axhline(-0.1, color=CLR_RED, linewidth=0.5, linestyle="--", alpha=0.5)
        ax.axhline(0, color=CLR_GRAY, linewidth=0.5)
        ax.fill_between(x, cmf, 0, where=(cmf >= 0), color=CLR_GREEN, alpha=0.15)
        ax.fill_between(x, cmf, 0, where=(cmf < 0), color=CLR_RED, alpha=0.15)

        if "score_CMF" in ts.columns:
            self._draw_score_bg(ax, ts, x, "score_CMF")

        score = self._get_last_score(ts, "score_CMF")
        self._set_title(ax, "CMF", f"skor: {score:+.1f} | CMF: {cmf[-1]:.3f}")
        self._draw_signal_markers(ax, ts, x)

    def _draw_grand_total(self, ax, ts, x):
        """Grand confluence score with active_group coloring and signal markers."""
        scores = ts["confluence_score"].values
        signals = ts["signal"].values
        groups = ts["active_group"].values

        # Active group background
        group_colors = {
            "BOTH": CLR_BLUE,
            "TREND": CLR_ORANGE,
            "REVERSION": CLR_PURPLE,
            "CONFLICT": CLR_RED,
            "NEUTRAL": CLR_GRAY,
        }
        for i in range(len(x)):
            c = group_colors.get(groups[i], CLR_GRAY)
            ax.axvspan(x[i] - 0.5, x[i] + 0.5, color=c, alpha=0.08)

        # Score line with fill
        pos = np.where(scores >= 0, scores, 0)
        neg = np.where(scores < 0, scores, 0)
        ax.fill_between(x, 0, pos, color=CLR_GREEN, alpha=0.3, step="mid")
        ax.fill_between(x, 0, neg, color=CLR_RED, alpha=0.3, step="mid")
        ax.plot(x, scores, color=CLR_TEXT, linewidth=1.2)

        # Threshold lines
        ax.axhline(4.0, color=CLR_GREEN, linewidth=0.7, linestyle="--", alpha=0.6)
        ax.axhline(-4.0, color=CLR_RED, linewidth=0.7, linestyle="--", alpha=0.6)
        ax.axhline(5.0, color=CLR_GREEN, linewidth=0.5, linestyle=":", alpha=0.4)
        ax.axhline(-5.0, color=CLR_RED, linewidth=0.5, linestyle=":", alpha=0.4)
        ax.axhline(0, color=CLR_GRAY, linewidth=0.5)

        # BUY/SELL markers
        buy_x = [x[i] for i in range(len(x)) if signals[i] == "BUY"]
        buy_y = [scores[i] for i in range(len(x)) if signals[i] == "BUY"]
        sell_x = [x[i] for i in range(len(x)) if signals[i] == "SELL"]
        sell_y = [scores[i] for i in range(len(x)) if signals[i] == "SELL"]

        if buy_x:
            ax.scatter(buy_x, buy_y, marker="^", color=CLR_GREEN, s=40,
                       zorder=5, label="BUY")
        if sell_x:
            ax.scatter(sell_x, sell_y, marker="v", color=CLR_RED, s=40,
                       zorder=5, label="SELL")

        last_score = scores[-1]
        last_group = groups[-1]
        self._set_title(ax, "CONFLUENCE TOPLAM",
                        f"skor: {last_score:+.2f} | grup: {last_group}")

        # Legend with group colors
        from matplotlib.patches import Patch
        handles = [
            Patch(facecolor=CLR_BLUE, alpha=0.3, label="BOTH"),
            Patch(facecolor=CLR_ORANGE, alpha=0.3, label="TREND"),
            Patch(facecolor=CLR_PURPLE, alpha=0.3, label="REVERSION"),
            Patch(facecolor=CLR_RED, alpha=0.3, label="CONFLICT"),
            Patch(facecolor=CLR_GRAY, alpha=0.3, label="NEUTRAL"),
        ]
        ax.legend(handles=handles, loc="upper right", fontsize=6,
                  facecolor=CLR_SUBPLOT, edgecolor=CLR_GRID, labelcolor=CLR_TEXT,
                  ncol=5)

    def _draw_regime(self, ax, ts, x):
        """Regime colored horizontal band."""
        regimes = ts["regime"].values
        confidences = ts["regime_confidence"].values

        regime_colors = {
            "TRENDING": CLR_BLUE,
            "RANGING": CLR_ORANGE,
            "VOLATILE": CLR_RED,
            "BREAKOUT": CLR_GREEN,
        }

        for i in range(len(x)):
            c = regime_colors.get(regimes[i], CLR_GRAY)
            alpha = max(0.15, min(confidences[i] * 0.6, 0.6))
            ax.axvspan(x[i] - 0.5, x[i] + 0.5, color=c, alpha=alpha)

        ax.set_ylim(0, 1)
        ax.set_yticks([])

        last_regime = regimes[-1]
        last_conf = confidences[-1]
        self._set_title(ax, "REJIM", f"{last_regime} (guven: {last_conf:.0%})")

        # Legend
        from matplotlib.patches import Patch
        handles = [
            Patch(facecolor=CLR_BLUE, alpha=0.4, label="TRENDING"),
            Patch(facecolor=CLR_ORANGE, alpha=0.4, label="RANGING"),
            Patch(facecolor=CLR_RED, alpha=0.4, label="VOLATILE"),
            Patch(facecolor=CLR_GREEN, alpha=0.4, label="BREAKOUT"),
        ]
        ax.legend(handles=handles, loc="upper right", fontsize=6,
                  facecolor=CLR_SUBPLOT, edgecolor=CLR_GRID, labelcolor=CLR_TEXT,
                  ncol=4)

    # ── Score background helper ──────────────────────────────────────────

    def _draw_score_bg(self, ax, ts, x, score_col):
        """Draw faint colored background based on per-indicator score values."""
        if score_col not in ts.columns:
            return
        scores = ts[score_col].values
        for i in range(len(x)):
            s = scores[i]
            if s > 0.5:
                ax.axvspan(x[i] - 0.5, x[i] + 0.5, color=CLR_GREEN, alpha=0.06)
            elif s > 0:
                ax.axvspan(x[i] - 0.5, x[i] + 0.5, color=CLR_GREEN, alpha=0.03)
            elif s < -0.5:
                ax.axvspan(x[i] - 0.5, x[i] + 0.5, color=CLR_RED, alpha=0.06)
            elif s < 0:
                ax.axvspan(x[i] - 0.5, x[i] + 0.5, color=CLR_RED, alpha=0.03)

    # ══════════════════════════════════════════════════════════════════════════
    # CLEANUP
    # ══════════════════════════════════════════════════════════════════════════

    def destroy(self):
        """Stop live mode and clean up matplotlib resources on panel destroy."""
        self._live_running = False
        if self._canvas:
            self._canvas.get_tk_widget().destroy()
            self._canvas = None
        if self._fig:
            import matplotlib.pyplot as plt
            plt.close(self._fig)
            self._fig = None
        super().destroy()
