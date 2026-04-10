"""Chart Popup — Coin grafigi + indikatorler + islem gecmisi isaretleri.

Pozisyon tablosundan bir coin secildiginde acilir.
- Candlestick grafik (matplotlib)
- EMA 9/21, Bollinger Bands overlay
- RSI + MACD + ADX subplot
- Entry/exit/reverse isaretleri (islem gecmisinden)
- Acik pozisyon entry + SL + TP cizgileri
"""
import threading
from datetime import datetime

import customtkinter as ctk
import numpy as np
import pandas as pd
from loguru import logger
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure

# ── Colors ──
CLR_BG = "#1a1a2e"
CLR_SUBPLOT = "#16213e"
CLR_GRID = "#333333"
CLR_GREEN = "#00C853"
CLR_RED = "#FF1744"
CLR_BLUE = "#2196F3"
CLR_ORANGE = "#FF9800"
CLR_CYAN = "#00BCD4"
CLR_YELLOW = "#FFEB3B"
CLR_PURPLE = "#9C27B0"
CLR_WHITE = "#FFFFFF"
CLR_TEXT = "#E0E0E0"
CLR_GRAY = "#9E9E9E"

# ── Vectorized indicator functions ──

def _ema(data, period):
    return pd.Series(data).ewm(span=period, adjust=False).mean().values

def _sma(data, period):
    return pd.Series(data).rolling(period, min_periods=1).mean().values

def _rsi(close, period=14):
    delta = np.diff(close, prepend=close[0])
    gain = np.where(delta > 0, delta, 0.0)
    loss = np.where(delta < 0, -delta, 0.0)
    ag = pd.Series(gain).ewm(alpha=1.0/period, min_periods=period, adjust=False).mean().values
    al = pd.Series(loss).ewm(alpha=1.0/period, min_periods=period, adjust=False).mean().values
    r = 100.0 - 100.0 / (1.0 + ag / (al + 1e-10))
    r[:period] = 50.0
    return r

def _macd(close, fast=12, slow=26, signal=9):
    ml = _ema(close, fast) - _ema(close, slow)
    sl = _ema(ml, signal)
    return ml, sl, ml - sl

def _bollinger(close, period=20, std_dev=2.0):
    s = pd.Series(close)
    mid = s.rolling(period).mean().values
    st = s.rolling(period).std().values
    return mid + std_dev * st, mid, mid - std_dev * st

def _adx(high, low, close, period=14):
    pc = np.roll(close, 1); pc[0] = close[0]
    tr = np.maximum(high - low, np.maximum(np.abs(high - pc), np.abs(low - pc)))
    atr = pd.Series(tr).ewm(alpha=1.0/period, min_periods=period, adjust=False).mean().values
    up = np.diff(high, prepend=high[0])
    dn = -np.diff(low, prepend=low[0])
    pdm = np.where((up > dn) & (up > 0), up, 0.0)
    ndm = np.where((dn > up) & (dn > 0), dn, 0.0)
    spdm = pd.Series(pdm).ewm(alpha=1.0/period, min_periods=period, adjust=False).mean().values
    sndm = pd.Series(ndm).ewm(alpha=1.0/period, min_periods=period, adjust=False).mean().values
    pdi = 100.0 * spdm / (atr + 1e-10)
    ndi = 100.0 * sndm / (atr + 1e-10)
    dx = 100.0 * np.abs(pdi - ndi) / (pdi + ndi + 1e-10)
    adx_val = pd.Series(dx).ewm(alpha=1.0/period, min_periods=period, adjust=False).mean().values
    return adx_val, pdi, ndi


class ChartPopup(ctk.CTkToplevel):
    """Popup pencere: Coin grafigi + indikatorler + islem gecmisi."""

    def __init__(self, parent, controller, symbol: str,
                 timeframe: str = "5m", candle_count: int = 300,
                 position_info: dict = None):
        super().__init__(parent)
        self.title(f"Grafik: {symbol} ({timeframe})")
        self.geometry("1400x900")
        self.configure(fg_color=CLR_BG)

        self.controller = controller
        self.symbol = symbol
        self.timeframe = timeframe
        self.candle_count = candle_count
        self.position_info = position_info  # acik pozisyon bilgisi (veya None)

        self._canvas = None
        self._fig = None

        # Controls
        ctrl = ctk.CTkFrame(self, fg_color="#0d1117", height=40)
        ctrl.pack(fill="x", padx=4, pady=(4, 0))

        ctk.CTkLabel(ctrl, text=f"{symbol}",
                     font=ctk.CTkFont(size=18, weight="bold"),
                     text_color=CLR_CYAN).pack(side="left", padx=8)

        self._tf_var = ctk.StringVar(value=timeframe)
        ctk.CTkOptionMenu(ctrl, variable=self._tf_var,
                          values=["1m","3m","5m","15m","30m","1h","2h","4h","8h","12h","1d"],
                          width=80, command=lambda _: self._load()).pack(side="left", padx=4)

        self._count_var = ctk.StringVar(value=str(candle_count))
        ctk.CTkEntry(ctrl, textvariable=self._count_var, width=60).pack(side="left", padx=4)

        ctk.CTkButton(ctrl, text="Yenile", width=70, fg_color="#1565C0",
                      command=self._load).pack(side="left", padx=4)

        self._status_var = ctk.StringVar(value="Yukleniyor...")
        ctk.CTkLabel(ctrl, textvariable=self._status_var,
                     font=ctk.CTkFont(size=12), text_color=CLR_GRAY).pack(side="right", padx=8)

        # Chart frame
        self._chart_frame = ctk.CTkFrame(self, fg_color=CLR_BG)
        self._chart_frame.pack(fill="both", expand=True, padx=4, pady=4)

        # Load data
        self.after(100, self._load)

    def _load(self):
        self._status_var.set("Veri aliniyor...")
        threading.Thread(target=self._do_load, daemon=True).start()

    def _do_load(self):
        try:
            rest = self.controller.market_service._rest
            tf = self._tf_var.get()
            limit = min(int(self._count_var.get()), 1500)
            df = rest.get_klines(self.symbol, tf, limit)
            if df is None or df.empty:
                self.after(0, lambda: self._status_var.set("Veri alinamadi!"))
                return

            # Fetch trade history for this symbol
            trades = []
            if hasattr(self.controller, 'order_logger') and self.controller.order_logger:
                trades = self.controller.order_logger.get_trades_by_symbol(self.symbol, limit=50)

            self.after(0, lambda: self._draw(df, trades))
            self.after(0, lambda: self._status_var.set(
                f"{self.symbol} | {tf} | {len(df)} mum | {len(trades)} islem"))
        except Exception as e:
            logger.error(f"Chart load error: {e}")
            self.after(0, lambda: self._status_var.set(f"Hata: {e}"))

    def _draw(self, df: pd.DataFrame, trades: list):
        # Cleanup old
        if self._canvas:
            self._canvas.get_tk_widget().destroy()
        if self._fig:
            import matplotlib.pyplot as plt
            plt.close(self._fig)

        close = df["close"].values.astype(float)
        high = df["high"].values.astype(float)
        low = df["low"].values.astype(float)
        opn = df["open"].values.astype(float)
        volume = df["volume"].values.astype(float)
        timestamps = df["timestamp"].values
        n = len(df)
        x = np.arange(n)

        # Compute indicators
        ema9 = _ema(close, 9)
        ema21 = _ema(close, 21)
        bb_up, bb_mid, bb_low = _bollinger(close, 20, 2.0)
        rsi = _rsi(close, 14)
        macd_line, macd_signal, macd_hist = _macd(close, 12, 26, 9)
        adx_val, pdi, ndi = _adx(high, low, close, 14)

        # ── Figure with GridSpec: price(5) + RSI(1.5) + MACD(1.5) + ADX(1.5) ──
        from matplotlib.gridspec import GridSpec
        fig = Figure(figsize=(18, 10), facecolor=CLR_BG, dpi=85)
        fig.subplots_adjust(left=0.05, right=0.97, top=0.96, bottom=0.04, hspace=0.08)
        gs = GridSpec(4, 1, height_ratios=[5, 1.5, 1.5, 1.5], figure=fig)

        def _style_ax(ax, title=""):
            ax.set_facecolor(CLR_SUBPLOT)
            ax.tick_params(colors=CLR_TEXT, labelsize=7)
            ax.grid(True, color=CLR_GRID, alpha=0.3, linewidth=0.5)
            for spine in ax.spines.values():
                spine.set_color(CLR_GRID)
            if title:
                ax.set_title(title, fontsize=9, color=CLR_TEXT, loc="left", pad=2)

        # ══════════════════════════════════════════
        # AX1: CANDLESTICK + OVERLAY
        # ══════════════════════════════════════════
        ax1 = fig.add_subplot(gs[0])
        _style_ax(ax1, f"{self.symbol} - {self._tf_var.get()}")

        # Candlesticks
        for i in range(n):
            color = CLR_GREEN if close[i] >= opn[i] else CLR_RED
            ax1.plot([x[i], x[i]], [low[i], high[i]], color=color, linewidth=0.5)
            body_b = min(opn[i], close[i])
            body_h = max(abs(close[i] - opn[i]), (high[i] - low[i]) * 0.005)
            ax1.bar(x[i], body_h, bottom=body_b, width=0.6,
                    color=color, edgecolor=color, linewidth=0.3)

        # EMA overlay
        ax1.plot(x, ema9, color=CLR_CYAN, linewidth=1.0, alpha=0.8, label="EMA 9")
        ax1.plot(x, ema21, color=CLR_ORANGE, linewidth=1.0, alpha=0.8, label="EMA 21")

        # Bollinger Bands
        ax1.plot(x, bb_up, color=CLR_PURPLE, linewidth=0.7, alpha=0.5, label="BB Upper")
        ax1.plot(x, bb_mid, color=CLR_PURPLE, linewidth=0.5, alpha=0.4, linestyle="--")
        ax1.plot(x, bb_low, color=CLR_PURPLE, linewidth=0.7, alpha=0.5, label="BB Lower")
        ax1.fill_between(x, bb_up, bb_low, color=CLR_PURPLE, alpha=0.05)

        # ── Active position lines ──
        pos = self.position_info
        if pos:
            entry_p = pos.get("entry_price", 0) or 0
            sl_p = pos.get("sl", 0) or 0
            liq_p = pos.get("liquidation_price", 0) or 0
            is_long = "Buy" in str(pos.get("side", "")) or "LONG" in str(pos.get("side", "")).upper()

            if entry_p > 0:
                ax1.axhline(entry_p, color=CLR_CYAN, linewidth=1.2, linestyle="--", alpha=0.8)
                ax1.text(n * 0.01, entry_p, f" Entry {entry_p:.6g}",
                         color=CLR_CYAN, fontsize=8, va="bottom")
            if sl_p > 0:
                ax1.axhline(sl_p, color=CLR_RED, linewidth=1.2, linestyle="--", alpha=0.8)
                ax1.text(n * 0.01, sl_p, f" SL {sl_p:.6g}",
                         color=CLR_RED, fontsize=8, va="top" if is_long else "bottom")
            if liq_p > 0:
                ax1.axhline(liq_p, color="#FF5252", linewidth=0.8, linestyle=":", alpha=0.5)
                ax1.text(n * 0.01, liq_p, f" Liq {liq_p:.6g}",
                         color="#FF5252", fontsize=7, va="top" if is_long else "bottom")

        # ── Trade history markers ──
        # Convert timestamps to datetime for matching
        ts_dt = pd.to_datetime(timestamps)
        tf_seconds = self._tf_to_seconds(self._tf_var.get())

        for t in trades:
            open_time_str = t.get("open_time", "")
            close_time_str = t.get("close_time", "")
            entry_price = t.get("entry_price", 0) or 0
            exit_price = t.get("exit_price", 0) or 0
            side = t.get("side", "")
            pnl = t.get("pnl_usdt", 0) or 0
            exit_reason = t.get("exit_reason", "") or ""
            is_long_trade = "Buy" in side or "Long" in side

            # Find bar index for open time
            open_idx = self._find_bar_index(ts_dt, open_time_str, tf_seconds)
            close_idx = self._find_bar_index(ts_dt, close_time_str, tf_seconds)

            # Entry marker
            if open_idx is not None and entry_price > 0:
                marker = "^" if is_long_trade else "v"
                color = CLR_GREEN if is_long_trade else CLR_RED
                ax1.scatter(open_idx, entry_price, marker=marker, color=color,
                           s=120, zorder=10, edgecolors=CLR_WHITE, linewidth=0.8)

            # Exit marker
            if close_idx is not None and exit_price > 0:
                exit_color = CLR_GREEN if pnl >= 0 else CLR_RED
                marker = "x" if "STOP_LOSS" in exit_reason else "D"
                ax1.scatter(close_idx, exit_price, marker=marker, color=exit_color,
                           s=100, zorder=10, edgecolors=CLR_WHITE, linewidth=0.8)

                # Exit reason label
                short_reason = exit_reason[:12] if exit_reason else ""
                pnl_str = f"{pnl:+.3f}"
                ax1.annotate(f"{short_reason}\n{pnl_str}",
                            xy=(close_idx, exit_price),
                            xytext=(5, 15 if pnl >= 0 else -15),
                            textcoords="offset points",
                            fontsize=6, color=exit_color,
                            arrowprops=dict(arrowstyle="-", color=exit_color, lw=0.5),
                            bbox=dict(boxstyle="round,pad=0.2", facecolor=CLR_SUBPLOT,
                                     edgecolor=exit_color, alpha=0.8))

            # Connect entry->exit with line
            if open_idx is not None and close_idx is not None and entry_price > 0 and exit_price > 0:
                line_color = CLR_GREEN if pnl >= 0 else CLR_RED
                ax1.plot([open_idx, close_idx], [entry_price, exit_price],
                        color=line_color, linewidth=1.0, linestyle=":", alpha=0.6)

        ax1.legend(loc="upper left", fontsize=7, facecolor=CLR_SUBPLOT,
                  edgecolor=CLR_GRID, labelcolor=CLR_TEXT, ncol=3)

        # X-axis labels (every ~20 bars)
        step = max(1, n // 15)
        tick_positions = list(range(0, n, step))
        tick_labels = []
        for i in tick_positions:
            try:
                dt = pd.Timestamp(timestamps[i])
                tick_labels.append(dt.strftime("%m-%d %H:%M"))
            except Exception:
                tick_labels.append("")
        ax1.set_xticks(tick_positions)
        ax1.set_xticklabels(tick_labels, rotation=45, fontsize=6)

        # ══════════════════════════════════════════
        # AX2: RSI
        # ══════════════════════════════════════════
        ax2 = fig.add_subplot(gs[1], sharex=ax1)
        _style_ax(ax2, "RSI (14)")
        ax2.plot(x, rsi, color=CLR_CYAN, linewidth=1.0)
        ax2.axhline(70, color=CLR_RED, linewidth=0.5, linestyle="--", alpha=0.5)
        ax2.axhline(30, color=CLR_GREEN, linewidth=0.5, linestyle="--", alpha=0.5)
        ax2.fill_between(x, rsi, 70, where=rsi > 70, color=CLR_RED, alpha=0.1)
        ax2.fill_between(x, rsi, 30, where=rsi < 30, color=CLR_GREEN, alpha=0.1)
        ax2.set_ylim(0, 100)
        ax2.set_xticklabels([])

        # ══════════════════════════════════════════
        # AX3: MACD
        # ══════════════════════════════════════════
        ax3 = fig.add_subplot(gs[2], sharex=ax1)
        _style_ax(ax3, "MACD (12, 26, 9)")
        ax3.plot(x, macd_line, color=CLR_BLUE, linewidth=1.0, label="MACD")
        ax3.plot(x, macd_signal, color=CLR_ORANGE, linewidth=0.8, label="Signal")
        hist_colors = [CLR_GREEN if h >= 0 else CLR_RED for h in macd_hist]
        ax3.bar(x, macd_hist, color=hist_colors, width=0.6, alpha=0.6)
        ax3.axhline(0, color=CLR_GRAY, linewidth=0.5, alpha=0.5)
        ax3.legend(loc="upper left", fontsize=6, facecolor=CLR_SUBPLOT,
                  edgecolor=CLR_GRID, labelcolor=CLR_TEXT)
        ax3.set_xticklabels([])

        # ══════════════════════════════════════════
        # AX4: ADX
        # ══════════════════════════════════════════
        ax4 = fig.add_subplot(gs[3], sharex=ax1)
        _style_ax(ax4, "ADX (14)")
        ax4.plot(x, adx_val, color=CLR_YELLOW, linewidth=1.2, label="ADX")
        ax4.plot(x, pdi, color=CLR_GREEN, linewidth=0.7, alpha=0.7, label="+DI")
        ax4.plot(x, ndi, color=CLR_RED, linewidth=0.7, alpha=0.7, label="-DI")
        ax4.axhline(25, color=CLR_GRAY, linewidth=0.5, linestyle="--", alpha=0.5)
        ax4.set_ylim(0, 80)
        ax4.legend(loc="upper left", fontsize=6, facecolor=CLR_SUBPLOT,
                  edgecolor=CLR_GRID, labelcolor=CLR_TEXT)

        # X-axis labels on bottom chart
        ax4.set_xticks(tick_positions)
        ax4.set_xticklabels(tick_labels, rotation=45, fontsize=6)

        # ── Render ──
        self._fig = fig
        self._canvas = FigureCanvasTkAgg(fig, master=self._chart_frame)
        self._canvas.draw()
        self._canvas.get_tk_widget().pack(fill="both", expand=True)

    def _tf_to_seconds(self, tf: str) -> int:
        mapping = {
            "1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800,
            "1h": 3600, "2h": 7200, "4h": 14400, "8h": 28800,
            "12h": 43200, "1d": 86400, "1w": 604800,
        }
        return mapping.get(tf, 300)

    def _find_bar_index(self, ts_series, time_str: str, tf_seconds: int):
        """Find the bar index closest to the given ISO time string."""
        if not time_str:
            return None
        try:
            target = pd.Timestamp(time_str)
            if target.tzinfo is None:
                target = target.tz_localize(None)
            # Convert series to tz-naive if needed
            ts_naive = ts_series.tz_localize(None) if hasattr(ts_series, 'tz_localize') else ts_series
            diffs = np.abs((ts_naive - target).total_seconds())
            min_idx = np.argmin(diffs)
            # Only match if within 2 bar widths
            if diffs[min_idx] <= tf_seconds * 2:
                return int(min_idx)
        except Exception as e:
            logger.debug(f"Bar index match failed for {time_str}: {e}")
        return None

    def destroy(self):
        if self._fig:
            import matplotlib.pyplot as plt
            plt.close(self._fig)
        super().destroy()
