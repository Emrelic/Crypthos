"""Advanced Chart Panel - Interactive candlestick chart with dynamic indicator selection.

Features:
- Fixed candlestick chart with overlay indicators (EMA, BB, Ichimoku, etc.)
- Scrollable indicator subplot area (RSI, MACD, ADX, etc.)
- Dynamic indicator add/remove with live parameter editing
- Crosshair with synchronized cursor across all charts
- Static (refresh) and live auto-refresh modes
- 29 indicators: 10 overlay + 19 subplot
"""

import threading
import time
from collections import OrderedDict
from datetime import datetime

import customtkinter as ctk
import numpy as np
import pandas as pd
from loguru import logger
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure
from matplotlib.gridspec import GridSpec

# ══════════════════════════════════════════════════════════════════════════════
# COLORS
# ══════════════════════════════════════════════════════════════════════════════

CLR_BG = "#1a1a2e"
CLR_SUBPLOT = "#16213e"
CLR_GRID = "#333333"
CLR_GREEN = "#00C853"
CLR_RED = "#FF1744"
CLR_BLUE = "#2196F3"
CLR_ORANGE = "#FF9800"
CLR_PURPLE = "#9C27B0"
CLR_CYAN = "#00BCD4"
CLR_YELLOW = "#FFEB3B"
CLR_PINK = "#E91E63"
CLR_GRAY = "#9E9E9E"
CLR_LIGHT_GREEN = "#81C784"
CLR_LIGHT_RED = "#EF5350"
CLR_TEXT = "#E0E0E0"
CLR_WHITE = "#FFFFFF"
CLR_TEAL = "#009688"
CLR_INDIGO = "#3F51B5"

DEFAULT_SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT",
    "DOGEUSDT", "ADAUSDT", "AVAXUSDT", "DOTUSDT", "LINKUSDT",
    "MATICUSDT", "UNIUSDT", "NEARUSDT", "FILUSDT", "AAVEUSDT",
    "APTUSDT", "ARBUSDT", "OPUSDT", "SEIUSDT", "SUIUSDT",
]

TIMEFRAMES = ["1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "8h", "12h", "1d", "1w"]

_INTERVAL_SEC = {
    "1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800,
    "1h": 3600, "2h": 7200, "4h": 14400, "8h": 28800, "12h": 43200,
    "1d": 86400, "1w": 604800,
}


# ══════════════════════════════════════════════════════════════════════════════
# VECTORIZED SERIES COMPUTATION
# ══════════════════════════════════════════════════════════════════════════════

def _ema(data, period):
    return pd.Series(data).ewm(span=period, adjust=False).mean().values


def _sma(data, period):
    return pd.Series(data).rolling(period, min_periods=1).mean().values


def _wma(data, period):
    w = np.arange(1, period + 1, dtype=float)
    return pd.Series(data).rolling(period).apply(
        lambda x: np.dot(x, w) / w.sum(), raw=True
    ).values


def _rsi(close, period=14):
    delta = np.diff(close, prepend=close[0])
    gain = np.where(delta > 0, delta, 0.0)
    loss = np.where(delta < 0, -delta, 0.0)
    ag = pd.Series(gain).ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean().values
    al = pd.Series(loss).ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean().values
    r = 100.0 - 100.0 / (1.0 + ag / (al + 1e-10))
    r[:period] = 50.0
    return r


def _macd(close, fast=12, slow=26, signal=9):
    ml = _ema(close, fast) - _ema(close, slow)
    sl = _ema(ml, signal)
    return ml, sl, ml - sl


def _atr(high, low, close, period=14):
    pc = np.roll(close, 1)
    pc[0] = close[0]
    tr = np.maximum(high - low, np.maximum(np.abs(high - pc), np.abs(low - pc)))
    return pd.Series(tr).ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean().values


def _adx(high, low, close, period=14):
    up = np.diff(high, prepend=high[0])
    dn = -np.diff(low, prepend=low[0])
    pdm = np.where((up > dn) & (up > 0), up, 0.0)
    ndm = np.where((dn > up) & (dn > 0), dn, 0.0)
    a = _atr(high, low, close, period)
    spdm = pd.Series(pdm).ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean().values
    sndm = pd.Series(ndm).ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean().values
    pdi = 100.0 * spdm / (a + 1e-10)
    ndi = 100.0 * sndm / (a + 1e-10)
    dx = 100.0 * np.abs(pdi - ndi) / (pdi + ndi + 1e-10)
    adx_val = pd.Series(dx).ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean().values
    return adx_val, pdi, ndi


def _stochastic(high, low, close, period=14, k_sm=3, d_sm=3):
    hh = pd.Series(high).rolling(period).max().values
    ll = pd.Series(low).rolling(period).min().values
    rk = 100.0 * (close - ll) / (hh - ll + 1e-10)
    k = pd.Series(rk).rolling(k_sm, min_periods=1).mean().values
    d = pd.Series(k).rolling(d_sm, min_periods=1).mean().values
    return k, d


def _stoch_rsi(close, rsi_p=14, stoch_p=14, k_sm=3, d_sm=3):
    r = _rsi(close, rsi_p)
    rh = pd.Series(r).rolling(stoch_p).max().values
    rl = pd.Series(r).rolling(stoch_p).min().values
    rk = 100.0 * (r - rl) / (rh - rl + 1e-10)
    k = pd.Series(rk).rolling(k_sm, min_periods=1).mean().values
    d = pd.Series(k).rolling(d_sm, min_periods=1).mean().values
    return k, d


def _mfi(high, low, close, volume, period=14):
    tp = (high + low + close) / 3.0
    mf = tp * volume
    delta = np.diff(tp, prepend=tp[0])
    pmf = pd.Series(np.where(delta > 0, mf, 0.0)).rolling(period).sum().values
    nmf = pd.Series(np.where(delta < 0, mf, 0.0)).rolling(period).sum().values
    return 100.0 - 100.0 / (1.0 + pmf / (nmf + 1e-10))


def _cci(high, low, close, period=20):
    tp = (high + low + close) / 3.0
    s = pd.Series(tp)
    sm = s.rolling(period).mean().values
    mad = s.rolling(period).apply(lambda x: np.abs(x - x.mean()).mean(), raw=True).values
    return (tp - sm) / (0.015 * mad + 1e-10)


def _williams_r(high, low, close, period=14):
    hh = pd.Series(high).rolling(period).max().values
    ll = pd.Series(low).rolling(period).min().values
    return -100.0 * (hh - close) / (hh - ll + 1e-10)


def _roc(close, period=12):
    prev = np.roll(close, period)
    prev[:period] = close[:period]
    return 100.0 * (close - prev) / (prev + 1e-10)


def _ultimate_osc(high, low, close, p1=7, p2=14, p3=28):
    pc = np.roll(close, 1)
    pc[0] = close[0]
    bp = close - np.minimum(low, pc)
    tr = np.maximum(high, pc) - np.minimum(low, pc)
    a1 = pd.Series(bp).rolling(p1).sum().values / (pd.Series(tr).rolling(p1).sum().values + 1e-10)
    a2 = pd.Series(bp).rolling(p2).sum().values / (pd.Series(tr).rolling(p2).sum().values + 1e-10)
    a3 = pd.Series(bp).rolling(p3).sum().values / (pd.Series(tr).rolling(p3).sum().values + 1e-10)
    return 100.0 * (4 * a1 + 2 * a2 + a3) / 7.0


def _obv(close, volume):
    d = np.sign(np.diff(close, prepend=close[0]))
    return np.cumsum(d * volume)


def _cvd(volume, taker_buy_vol):
    return np.cumsum(2 * taker_buy_vol - volume)


def _cmf(high, low, close, volume, period=20):
    clv = ((close - low) - (high - close)) / (high - low + 1e-10)
    cv = clv * volume
    return pd.Series(cv).rolling(period).sum().values / (
        pd.Series(volume).rolling(period).sum().values + 1e-10
    )


def _ad_line(high, low, close, volume):
    clv = ((close - low) - (high - close)) / (high - low + 1e-10)
    return np.cumsum(clv * volume)


def _elder_force(close, volume, period=13):
    delta = np.diff(close, prepend=close[0])
    return _ema(delta * volume, period)


def _bollinger(close, period=20, std_dev=2.0):
    s = pd.Series(close)
    mid = s.rolling(period).mean().values
    st = s.rolling(period).std().values
    return mid + std_dev * st, mid, mid - std_dev * st


def _keltner(high, low, close, ema_p=20, atr_p=14, mult=2.0):
    mid = _ema(close, ema_p)
    a = _atr(high, low, close, atr_p)
    return mid + mult * a, mid, mid - mult * a


def _donchian(high, low, period=20):
    u = pd.Series(high).rolling(period).max().values
    lo = pd.Series(low).rolling(period).min().values
    return u, (u + lo) / 2, lo


def _vwap(high, low, close, volume):
    tp = (high + low + close) / 3.0
    return np.cumsum(tp * volume) / (np.cumsum(volume) + 1e-10)


def _ichimoku(high, low, tenkan=9, kijun=26, senkou_b=52):
    h, lo = pd.Series(high), pd.Series(low)
    tk = (h.rolling(tenkan).max().values + lo.rolling(tenkan).min().values) / 2
    kj = (h.rolling(kijun).max().values + lo.rolling(kijun).min().values) / 2
    sa = (tk + kj) / 2
    sb = (h.rolling(senkou_b).max().values + lo.rolling(senkou_b).min().values) / 2
    return tk, kj, sa, sb


def _psar(high, low, close, af_start=0.02, af_step=0.02, af_max=0.2):
    n = len(close)
    psar = np.zeros(n)
    trend = np.ones(n)
    af = af_start
    ep = high[0]
    psar[0] = low[0]
    for i in range(1, n):
        if trend[i - 1] == 1:
            psar[i] = psar[i - 1] + af * (ep - psar[i - 1])
            psar[i] = min(psar[i], low[i - 1])
            if i >= 2:
                psar[i] = min(psar[i], low[i - 2])
            if low[i] < psar[i]:
                trend[i], psar[i], af, ep = -1, ep, af_start, low[i]
            else:
                trend[i] = 1
                if high[i] > ep:
                    ep = high[i]
                    af = min(af + af_step, af_max)
        else:
            psar[i] = psar[i - 1] + af * (ep - psar[i - 1])
            psar[i] = max(psar[i], high[i - 1])
            if i >= 2:
                psar[i] = max(psar[i], high[i - 2])
            if high[i] > psar[i]:
                trend[i], psar[i], af, ep = 1, ep, af_start, high[i]
            else:
                trend[i] = -1
                if low[i] < ep:
                    ep = low[i]
                    af = min(af + af_step, af_max)
    return psar, trend


def _supertrend(high, low, close, period=10, mult=3.0):
    a = _atr(high, low, close, period)
    hl2 = (high + low) / 2.0
    up_band = hl2 + mult * a
    lo_band = hl2 - mult * a
    n = len(close)
    st = np.zeros(n)
    d = np.ones(n)
    fu, fl = up_band.copy(), lo_band.copy()
    for i in range(1, n):
        fl[i] = lo_band[i] if (lo_band[i] > fl[i - 1] or close[i - 1] < fl[i - 1]) else fl[i - 1]
        fu[i] = up_band[i] if (up_band[i] < fu[i - 1] or close[i - 1] > fu[i - 1]) else fu[i - 1]
        if d[i - 1] == 1:
            d[i], st[i] = (-1, fu[i]) if close[i] < fl[i] else (1, fl[i])
        else:
            d[i], st[i] = (1, fl[i]) if close[i] > fu[i] else (-1, fu[i])
    return st, d


def _aroon(high, low, period=25):
    n = len(high)
    au, ad = np.full(n, np.nan), np.full(n, np.nan)
    for i in range(period, n):
        au[i] = 100.0 * np.argmax(high[i - period:i + 1]) / period
        ad[i] = 100.0 * np.argmin(low[i - period:i + 1]) / period
    return au, ad


def _hma(close, period=20):
    hp = max(1, int(period / 2))
    sp = max(1, int(np.sqrt(period)))
    wh = _wma(close, hp)
    wf = _wma(close, period)
    diff = 2 * wh - wf
    diff_clean = np.nan_to_num(diff, nan=np.nanmean(close))
    return _wma(diff_clean, sp)


def _bb_width(close, period=20, std_dev=2.0):
    u, m, lo = _bollinger(close, period, std_dev)
    return (u - lo) / (m + 1e-10) * 100


# ══════════════════════════════════════════════════════════════════════════════
# INDICATOR REGISTRY
# ══════════════════════════════════════════════════════════════════════════════

INDICATOR_DEFS = OrderedDict([
    # ── OVERLAY (price chart) ── arrows indicate overlay type
    ("EMA \u2191", {"type": "overlay", "params": OrderedDict([("P1", 9), ("P2", 21)])}),
    ("SMA \u2191", {"type": "overlay", "params": OrderedDict([("P1", 20), ("P2", 200)])}),
    ("Bollinger Bands \u2191", {"type": "overlay",
                                "params": OrderedDict([("Periyot", 20), ("Std", 2.0)])}),
    ("Keltner \u2191", {"type": "overlay",
                        "params": OrderedDict([("EMA P", 20), ("ATR P", 14), ("\u00c7arpan", 2.0)])}),
    ("Donchian \u2191", {"type": "overlay", "params": OrderedDict([("Periyot", 20)])}),
    ("Ichimoku \u2191", {"type": "overlay",
                         "params": OrderedDict([("Tenkan", 9), ("Kijun", 26), ("Senkou B", 52)])}),
    ("PSAR \u2191", {"type": "overlay",
                     "params": OrderedDict([("AF Start", 0.02), ("AF Ad\u0131m", 0.02), ("AF Max", 0.2)])}),
    ("Supertrend \u2191", {"type": "overlay",
                           "params": OrderedDict([("Periyot", 10), ("\u00c7arpan", 3.0)])}),
    ("VWAP \u2191", {"type": "overlay", "params": OrderedDict()}),
    ("HMA \u2191", {"type": "overlay", "params": OrderedDict([("Periyot", 20)])}),
    # ── SUBPLOT ──
    ("RSI", {"type": "subplot", "params": OrderedDict([("Periyot", 14)])}),
    ("MACD", {"type": "subplot", "params": OrderedDict([("Fast", 12), ("Slow", 26), ("Signal", 9)])}),
    ("ADX", {"type": "subplot", "params": OrderedDict([("Periyot", 14)])}),
    ("Stochastic", {"type": "subplot",
                    "params": OrderedDict([("Periyot", 14), ("K", 3), ("D", 3)])}),
    ("Stochastic RSI", {"type": "subplot",
                        "params": OrderedDict([("RSI P", 14), ("Stoch P", 14), ("K", 3), ("D", 3)])}),
    ("MFI", {"type": "subplot", "params": OrderedDict([("Periyot", 14)])}),
    ("CCI", {"type": "subplot", "params": OrderedDict([("Periyot", 20)])}),
    ("Williams %R", {"type": "subplot", "params": OrderedDict([("Periyot", 14)])}),
    ("ROC", {"type": "subplot", "params": OrderedDict([("Periyot", 12)])}),
    ("Ultimate Osc", {"type": "subplot",
                      "params": OrderedDict([("P1", 7), ("P2", 14), ("P3", 28)])}),
    ("OBV", {"type": "subplot", "params": OrderedDict()}),
    ("CVD", {"type": "subplot", "params": OrderedDict()}),
    ("CMF", {"type": "subplot", "params": OrderedDict([("Periyot", 20)])}),
    ("A/D Line", {"type": "subplot", "params": OrderedDict()}),
    ("Elder Force", {"type": "subplot", "params": OrderedDict([("Periyot", 13)])}),
    ("Hacim", {"type": "subplot", "params": OrderedDict()}),
    ("ATR", {"type": "subplot", "params": OrderedDict([("Periyot", 14)])}),
    ("BB Width", {"type": "subplot", "params": OrderedDict([("Periyot", 20), ("Std", 2.0)])}),
    ("Aroon", {"type": "subplot", "params": OrderedDict([("Periyot", 25)])}),
])


# ══════════════════════════════════════════════════════════════════════════════
# PANEL CLASS
# ══════════════════════════════════════════════════════════════════════════════

class IndicatorAnalysisPanel(ctk.CTkFrame):
    """Interactive chart panel with fixed candlestick + scrollable indicators."""

    def __init__(self, parent, controller, **kwargs):
        super().__init__(parent, **kwargs)
        self.controller = controller

        # ── Data ──
        self._df: pd.DataFrame | None = None
        self._open = self._high = self._low = self._close = self._volume = None
        self._taker_buy_vol = None
        self._timestamps = None
        self._n = 0
        self._x = None

        # ── Active indicators ──
        self._active: list[dict] = []
        self._selected_idx = -1

        # ── Matplotlib figures ──
        self._price_fig: Figure | None = None
        self._price_canvas: FigureCanvasTkAgg | None = None
        self._price_ax = None
        self._ind_fig: Figure | None = None
        self._ind_canvas: FigureCanvasTkAgg | None = None
        self._ind_axes: list = []

        # ── Zoom state (TradingView-style scroll zoom) ──
        self._view_start: int = 0       # first visible candle index
        self._view_end: int = 0         # last visible candle index (exclusive)
        self._min_visible: int = 10     # minimum candles visible when zoomed in
        self._drag_active: bool = False
        self._drag_start_x: int | None = None
        self._drag_view_start: int = 0

        # ── Crosshair ──
        self._price_vline = None
        self._price_hline = None
        self._ind_vlines: list = []
        self._value_texts: list = []
        self._last_cross_idx = -1

        # ── Live mode ──
        self._live_running = False
        self._live_thread: threading.Thread | None = None
        self._computation_lock = threading.Lock()

        # ── UI vars ──
        self._info_var = ctk.StringVar(value="")
        self._progress_var = ctk.StringVar(value="")
        self._param_entries: dict[str, ctk.StringVar] = {}

        # ── Build UI ──
        self._build_ui()

        # ── Default indicators ──
        self._add_indicator("RSI")
        self._add_indicator("MACD")
        self._add_indicator("Hacim")

    # ══════════════════════════════════════════════════════════════════════
    # UI BUILDING
    # ══════════════════════════════════════════════════════════════════════

    def _build_ui(self):
        font_s = ctk.CTkFont(size=11)
        font_m = ctk.CTkFont(size=12)

        # ── Row 1: Control bar ──
        bar = ctk.CTkFrame(self, height=36)
        bar.pack(fill="x", padx=4, pady=(4, 2))

        ctk.CTkLabel(bar, text="Coin:", width=30, font=font_m).pack(side="left", padx=(4, 2))
        self._symbol_var = ctk.StringVar(value="BTCUSDT")
        self._symbol_cb = ctk.CTkComboBox(
            bar, variable=self._symbol_var, values=DEFAULT_SYMBOLS,
            width=115, state="normal"
        )
        self._symbol_cb.pack(side="left", padx=2)

        ctk.CTkLabel(bar, text="TF:", width=22, font=font_m).pack(side="left", padx=(6, 2))
        self._tf_var = ctk.StringVar(value="15m")
        ctk.CTkComboBox(bar, variable=self._tf_var, values=TIMEFRAMES, width=65).pack(
            side="left", padx=2
        )

        ctk.CTkLabel(bar, text="Mum:", width=30, font=font_m).pack(side="left", padx=(6, 2))
        self._count_var = ctk.StringVar(value="200")
        count_entry = ctk.CTkEntry(bar, textvariable=self._count_var, width=50)
        count_entry.pack(side="left", padx=2)
        count_entry.bind("<Return>", lambda _: self._on_refresh())

        ctk.CTkButton(
            bar, text="Yenile", width=65, command=self._on_refresh, fg_color="#0D47A1"
        ).pack(side="left", padx=(8, 2))

        self._live_btn = ctk.CTkButton(
            bar, text="Canl\u0131: Kapal\u0131", width=95, command=self._toggle_live,
            fg_color="#333333"
        )
        self._live_btn.pack(side="left", padx=4)

        ctk.CTkButton(
            bar, text="Zoom S\u0131f\u0131rla", width=80, command=self._reset_zoom,
            fg_color="#555555", hover_color="#777777"
        ).pack(side="left", padx=2)

        ctk.CTkLabel(
            bar, textvariable=self._progress_var, width=220, font=font_s
        ).pack(side="right", padx=4)

        # ── Row 2: Indicator selector ──
        sel = ctk.CTkFrame(self, height=32)
        sel.pack(fill="x", padx=4, pady=(2, 0))

        ctk.CTkLabel(sel, text="\u0130ndikat\u00f6r:", width=60, font=font_m).pack(
            side="left", padx=(4, 2)
        )
        self._ind_var = ctk.StringVar(value="RSI")
        self._ind_cb = ctk.CTkComboBox(
            sel, variable=self._ind_var, values=list(INDICATOR_DEFS.keys()), width=170
        )
        self._ind_cb.pack(side="left", padx=2)
        ctk.CTkButton(
            sel, text="Ekle", width=50, command=self._on_add_click, fg_color="#1B5E20"
        ).pack(side="left", padx=4)
        ctk.CTkButton(
            sel, text="T\u00fcm\u00fcn\u00fc Temizle", width=105,
            command=self._clear_all, fg_color="#B71C1C", hover_color="#D32F2F"
        ).pack(side="right", padx=4)

        # ── Row 3: Active indicator chips ──
        self._chips_frame = ctk.CTkFrame(self, height=30, fg_color="transparent")
        self._chips_frame.pack(fill="x", padx=4, pady=(2, 0))

        # ── Row 4: Parameter editing (hidden initially) ──
        self._params_frame = ctk.CTkFrame(self, height=30)
        # not packed yet — shown when a chip is selected

        # ── Row 5: Info bar (crosshair tooltip) ──
        self._info_label = ctk.CTkLabel(
            self, textvariable=self._info_var,
            font=ctk.CTkFont(size=11, family="Consolas"), anchor="w", height=18
        )
        self._info_label.pack(fill="x", padx=6, pady=(2, 0))

        # ── Row 6: FIXED price chart ──
        self._price_frame = ctk.CTkFrame(self, fg_color=CLR_BG, height=320)
        self._price_frame.pack(fill="x", padx=2, pady=(2, 0))
        self._price_frame.pack_propagate(False)

        # ── Row 7: SCROLLABLE indicator subplots ──
        self._ind_scroll = ctk.CTkScrollableFrame(self, fg_color=CLR_BG)
        self._ind_scroll.pack(fill="both", expand=True, padx=2, pady=(0, 2))

    # ══════════════════════════════════════════════════════════════════════
    # INDICATOR MANAGEMENT
    # ══════════════════════════════════════════════════════════════════════

    def _on_add_click(self):
        name = self._ind_var.get()
        if name not in INDICATOR_DEFS:
            return
        self._add_indicator(name)
        if self._df is not None:
            self._recompute_and_draw()

    def _add_indicator(self, name):
        defn = INDICATOR_DEFS.get(name)
        if not defn:
            return
        params = OrderedDict((k, v) for k, v in defn["params"].items())
        self._active.append({"name": name, "params": params, "data": {}})
        self._rebuild_chips()

    def _remove_indicator(self, idx):
        if 0 <= idx < len(self._active):
            self._active.pop(idx)
            if self._selected_idx == idx:
                self._selected_idx = -1
                self._hide_params()
            elif self._selected_idx > idx:
                self._selected_idx -= 1
            self._rebuild_chips()
            if self._df is not None:
                self._recompute_and_draw()

    def _clear_all(self):
        self._active.clear()
        self._selected_idx = -1
        self._rebuild_chips()
        self._hide_params()
        if self._df is not None:
            self._recompute_and_draw()

    def _rebuild_chips(self):
        for w in self._chips_frame.winfo_children():
            w.destroy()

        for i, ind in enumerate(self._active):
            name = ind["name"]
            p_str = ",".join(str(v) for v in ind["params"].values())
            label = f"{name}({p_str})" if p_str else name

            chip = ctk.CTkFrame(self._chips_frame, height=26, corner_radius=12)
            chip.pack(side="left", padx=2, pady=2)

            is_sel = (i == self._selected_idx)
            idx = i

            ctk.CTkButton(
                chip, text=label, width=max(len(label) * 7 + 16, 60), height=24,
                corner_radius=12, font=ctk.CTkFont(size=11),
                fg_color="#1565C0" if is_sel else "#37474F",
                hover_color="#1976D2",
                command=lambda ii=idx: self._select_chip(ii),
            ).pack(side="left", padx=(2, 0))

            ctk.CTkButton(
                chip, text="\u00d7", width=22, height=24, corner_radius=12,
                fg_color="#B71C1C", hover_color="#D32F2F",
                font=ctk.CTkFont(size=13, weight="bold"),
                command=lambda ii=idx: self._remove_indicator(ii),
            ).pack(side="left", padx=(0, 2))

    def _select_chip(self, idx):
        if self._selected_idx == idx:
            self._selected_idx = -1
            self._hide_params()
        else:
            self._selected_idx = idx
            self._show_params(idx)
        self._rebuild_chips()

    def _show_params(self, idx):
        for w in self._params_frame.winfo_children():
            w.destroy()
        self._param_entries.clear()

        ind = self._active[idx]
        if not ind["params"]:
            self._hide_params()
            return

        self._params_frame.pack(fill="x", padx=4, pady=(2, 0), after=self._chips_frame)

        ctk.CTkLabel(
            self._params_frame, text=f"{ind['name']}:",
            width=90, font=ctk.CTkFont(size=11, weight="bold"),
        ).pack(side="left", padx=(4, 2))

        for key, val in ind["params"].items():
            ctk.CTkLabel(
                self._params_frame, text=f"{key}:", width=50,
                font=ctk.CTkFont(size=11),
            ).pack(side="left", padx=(4, 1))
            var = ctk.StringVar(value=str(val))
            entry = ctk.CTkEntry(
                self._params_frame, textvariable=var, width=50,
                font=ctk.CTkFont(size=11),
            )
            entry.pack(side="left", padx=1)
            entry.bind("<Return>", lambda _, ii=idx: self._apply_params(ii))
            self._param_entries[key] = var

        ctk.CTkButton(
            self._params_frame, text="Uygula", width=60, height=24,
            fg_color="#0D47A1", font=ctk.CTkFont(size=11),
            command=lambda: self._apply_params(idx),
        ).pack(side="left", padx=(8, 4))

    def _hide_params(self):
        self._params_frame.pack_forget()

    def _apply_params(self, idx):
        if idx >= len(self._active):
            return
        ind = self._active[idx]
        for key, var in self._param_entries.items():
            try:
                val = float(var.get())
                orig = ind["params"].get(key)
                if isinstance(orig, int):
                    val = int(val)
                ind["params"][key] = val
            except ValueError:
                pass
        self._rebuild_chips()
        if self._df is not None:
            ind["data"] = self._compute_indicator(ind)
            self._refresh_charts()

    # ══════════════════════════════════════════════════════════════════════
    # DATA
    # ══════════════════════════════════════════════════════════════════════

    def _fetch_klines(self) -> pd.DataFrame | None:
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

    def _prepare_data(self, df: pd.DataFrame):
        self._df = df
        self._open = df["open"].values.astype(float)
        self._high = df["high"].values.astype(float)
        self._low = df["low"].values.astype(float)
        self._close = df["close"].values.astype(float)
        self._volume = df["volume"].values.astype(float)
        self._taker_buy_vol = (
            df["taker_buy_volume"].values.astype(float)
            if "taker_buy_volume" in df.columns
            else self._volume * 0.5
        )
        self._timestamps = df["timestamp"].values
        self._n = len(df)
        self._x = np.arange(self._n)
        # Reset zoom to show all candles
        self._view_start = 0
        self._view_end = self._n

    # ══════════════════════════════════════════════════════════════════════
    # COMPUTE
    # ══════════════════════════════════════════════════════════════════════

    def _compute_indicator(self, ind: dict) -> dict:
        name = ind["name"]
        p = ind["params"]
        c, h, lo, v = self._close, self._high, self._low, self._volume
        tbv = self._taker_buy_vol

        try:
            if name == "EMA \u2191":
                return {"ema1": _ema(c, int(p.get("P1", 9))),
                        "ema2": _ema(c, int(p.get("P2", 21)))}
            if name == "SMA \u2191":
                return {"sma1": _sma(c, int(p.get("P1", 20))),
                        "sma2": _sma(c, int(p.get("P2", 200)))}
            if name == "Bollinger Bands \u2191":
                u, m, l = _bollinger(c, int(p.get("Periyot", 20)), float(p.get("Std", 2.0)))
                return {"upper": u, "middle": m, "lower": l}
            if name == "Keltner \u2191":
                u, m, l = _keltner(h, lo, c, int(p.get("EMA P", 20)),
                                   int(p.get("ATR P", 14)), float(p.get("\u00c7arpan", 2.0)))
                return {"upper": u, "middle": m, "lower": l}
            if name == "Donchian \u2191":
                u, m, l = _donchian(h, lo, int(p.get("Periyot", 20)))
                return {"upper": u, "middle": m, "lower": l}
            if name == "Ichimoku \u2191":
                tk, kj, sa, sb = _ichimoku(h, lo, int(p.get("Tenkan", 9)),
                                           int(p.get("Kijun", 26)), int(p.get("Senkou B", 52)))
                return {"tenkan": tk, "kijun": kj, "senkou_a": sa, "senkou_b": sb}
            if name == "PSAR \u2191":
                ps, tr = _psar(h, lo, c, float(p.get("AF Start", 0.02)),
                               float(p.get("AF Ad\u0131m", 0.02)), float(p.get("AF Max", 0.2)))
                return {"psar": ps, "trend": tr}
            if name == "Supertrend \u2191":
                st, d = _supertrend(h, lo, c, int(p.get("Periyot", 10)),
                                    float(p.get("\u00c7arpan", 3.0)))
                return {"supertrend": st, "direction": d}
            if name == "VWAP \u2191":
                return {"vwap": _vwap(h, lo, c, v)}
            if name == "HMA \u2191":
                return {"hma": _hma(c, int(p.get("Periyot", 20)))}
            if name == "RSI":
                return {"rsi": _rsi(c, int(p.get("Periyot", 14)))}
            if name == "MACD":
                ml, sl, hist = _macd(c, int(p.get("Fast", 12)),
                                     int(p.get("Slow", 26)), int(p.get("Signal", 9)))
                return {"macd": ml, "signal": sl, "histogram": hist}
            if name == "ADX":
                a, pdi, ndi = _adx(h, lo, c, int(p.get("Periyot", 14)))
                return {"adx": a, "plus_di": pdi, "minus_di": ndi}
            if name == "Stochastic":
                k, d = _stochastic(h, lo, c, int(p.get("Periyot", 14)),
                                   int(p.get("K", 3)), int(p.get("D", 3)))
                return {"k": k, "d": d}
            if name == "Stochastic RSI":
                k, d = _stoch_rsi(c, int(p.get("RSI P", 14)), int(p.get("Stoch P", 14)),
                                  int(p.get("K", 3)), int(p.get("D", 3)))
                return {"k": k, "d": d}
            if name == "MFI":
                return {"mfi": _mfi(h, lo, c, v, int(p.get("Periyot", 14)))}
            if name == "CCI":
                return {"cci": _cci(h, lo, c, int(p.get("Periyot", 20)))}
            if name == "Williams %R":
                return {"wr": _williams_r(h, lo, c, int(p.get("Periyot", 14)))}
            if name == "ROC":
                return {"roc": _roc(c, int(p.get("Periyot", 12)))}
            if name == "Ultimate Osc":
                return {"uo": _ultimate_osc(h, lo, c, int(p.get("P1", 7)),
                                            int(p.get("P2", 14)), int(p.get("P3", 28)))}
            if name == "OBV":
                return {"obv": _obv(c, v)}
            if name == "CVD":
                return {"cvd": _cvd(v, tbv)}
            if name == "CMF":
                return {"cmf": _cmf(h, lo, c, v, int(p.get("Periyot", 20)))}
            if name == "A/D Line":
                return {"ad": _ad_line(h, lo, c, v)}
            if name == "Elder Force":
                return {"ef": _elder_force(c, v, int(p.get("Periyot", 13)))}
            if name == "Hacim":
                return {"volume": v, "vol_ma": _sma(v, 20)}
            if name == "ATR":
                return {"atr": _atr(h, lo, c, int(p.get("Periyot", 14)))}
            if name == "BB Width":
                return {"bbw": _bb_width(c, int(p.get("Periyot", 20)),
                                         float(p.get("Std", 2.0)))}
            if name == "Aroon":
                au, ad = _aroon(h, lo, int(p.get("Periyot", 25)))
                return {"aroon_up": au, "aroon_down": ad}
        except Exception as e:
            logger.warning(f"Indicator compute error [{name}]: {e}")
        return {}

    # ══════════════════════════════════════════════════════════════════════
    # REFRESH / DRAW COORDINATION
    # ══════════════════════════════════════════════════════════════════════

    def _on_refresh(self):
        self._progress_var.set("Veri al\u0131n\u0131yor...")
        threading.Thread(target=self._do_refresh, daemon=True).start()

    def _do_refresh(self):
        try:
            df = self._fetch_klines()
            if df is None or df.empty:
                self.after(0, lambda: self._progress_var.set("Veri al\u0131namad\u0131!"))
                return
            with self._computation_lock:
                self._prepare_data(df)
                for ind in self._active:
                    ind["data"] = self._compute_indicator(ind)
            self.after(0, self._refresh_charts)
            sym = self._symbol_var.get()
            n = len(df)
            self.after(0, lambda: self._progress_var.set(f"{sym} | {n} mum | tamamland\u0131"))
        except Exception as e:
            logger.error(f"Refresh error: {e}")
            self.after(0, lambda: self._progress_var.set(f"Hata: {e}"))

    def _recompute_and_draw(self):
        """Recompute active indicators and redraw (UI thread)."""
        if self._df is None:
            return
        for ind in self._active:
            ind["data"] = self._compute_indicator(ind)
        self._refresh_charts()

    def _refresh_charts(self):
        if self._df is None:
            return
        self._draw_price_chart()
        self._draw_indicator_charts()

    # ── Cleanup helpers ──

    def _destroy_price_canvas(self):
        if self._price_canvas:
            self._price_canvas.get_tk_widget().destroy()
            self._price_canvas = None
        if self._price_fig:
            import matplotlib.pyplot as plt
            plt.close(self._price_fig)
            self._price_fig = None
        self._price_vline = None
        self._price_hline = None

    def _destroy_ind_canvas(self):
        if self._ind_canvas:
            self._ind_canvas.get_tk_widget().destroy()
            self._ind_canvas = None
        if self._ind_fig:
            import matplotlib.pyplot as plt
            plt.close(self._ind_fig)
            self._ind_fig = None
        self._ind_vlines.clear()
        self._value_texts.clear()
        self._ind_axes.clear()

    # ══════════════════════════════════════════════════════════════════════
    # PRICE CHART (FIXED)
    # ══════════════════════════════════════════════════════════════════════

    def _draw_price_chart(self):
        self._destroy_price_canvas()

        fig = Figure(figsize=(20, 4), facecolor=CLR_BG, dpi=80)
        fig.subplots_adjust(left=0.04, right=0.96, top=0.94, bottom=0.10)
        ax = fig.add_subplot(111)
        ax.set_facecolor(CLR_SUBPLOT)
        ax.tick_params(colors=CLR_TEXT, labelsize=7)
        ax.grid(True, color=CLR_GRID, alpha=0.3, linewidth=0.5)
        for spine in ax.spines.values():
            spine.set_color(CLR_GRID)

        x = self._x

        # ── Candlesticks (draw ALL, xlim controls visibility) ──
        for i in range(self._n):
            color = CLR_GREEN if self._close[i] >= self._open[i] else CLR_RED
            ax.plot([x[i], x[i]], [self._low[i], self._high[i]], color=color, linewidth=0.6)
            body_b = min(self._open[i], self._close[i])
            body_h = max(abs(self._close[i] - self._open[i]),
                         (self._high[i] - self._low[i]) * 0.005)
            ax.bar(x[i], body_h, bottom=body_b, width=0.6,
                   color=color, edgecolor=color, linewidth=0.3)

        # ── Overlay indicators ──
        overlays = [ind for ind in self._active
                    if INDICATOR_DEFS.get(ind["name"], {}).get("type") == "overlay"]
        for ind in overlays:
            self._draw_overlay(ax, ind)

        if overlays:
            ax.legend(loc="upper left", fontsize=7, facecolor=CLR_SUBPLOT,
                      edgecolor=CLR_GRID, labelcolor=CLR_TEXT, ncol=2)

        ax.set_title(
            f"{self._symbol_var.get()} \u2014 {self._tf_var.get()}",
            fontsize=10, color=CLR_TEXT, loc="left", pad=4,
        )

        # ── Apply zoom view limits ──
        self._apply_view_limits(ax)

        # ── Crosshair lines ──
        self._price_vline = ax.axvline(
            0, color=CLR_WHITE, linewidth=0.5, alpha=0.5, visible=False
        )
        self._price_hline = ax.axhline(
            0, color=CLR_WHITE, linewidth=0.5, alpha=0.5, visible=False
        )

        self._price_fig = fig
        self._price_ax = ax
        self._price_canvas = FigureCanvasTkAgg(fig, master=self._price_frame)
        self._price_canvas.get_tk_widget().pack(fill="both", expand=True)
        self._price_canvas.draw_idle()

        # ── Mouse events ──
        self._price_canvas.mpl_connect("motion_notify_event", self._on_mouse_price)
        self._price_canvas.mpl_connect("axes_leave_event", self._on_mouse_leave)
        self._price_canvas.mpl_connect("scroll_event", self._on_scroll_zoom)
        self._price_canvas.mpl_connect("button_press_event", self._on_drag_start)
        self._price_canvas.mpl_connect("button_release_event", self._on_drag_end)

    # ══════════════════════════════════════════════════════════════════════
    # ZOOM & PAN (TradingView-style)
    # ══════════════════════════════════════════════════════════════════════

    def _apply_view_limits(self, ax=None):
        """Set X and Y axis limits based on current view range."""
        if ax is None:
            ax = self._price_ax
        if ax is None or self._n == 0:
            return

        vs, ve = self._view_start, self._view_end
        vs = max(0, vs)
        ve = min(self._n, ve)
        if ve <= vs:
            ve = vs + 1

        # X limits with small padding
        x_pad = max(1, (ve - vs) * 0.02)
        ax.set_xlim(vs - x_pad, ve - 1 + x_pad)

        # Y limits: fit to visible candles with padding
        vis_low = self._low[vs:ve]
        vis_high = self._high[vs:ve]
        if len(vis_low) > 0:
            y_min = float(np.min(vis_low))
            y_max = float(np.max(vis_high))
            y_pad = (y_max - y_min) * 0.05 if y_max > y_min else y_max * 0.01
            ax.set_ylim(y_min - y_pad, y_max + y_pad)

        # Format X axis for visible range
        self._format_xaxis_ranged(ax, vs, ve)

    def _format_xaxis_ranged(self, ax, vs: int, ve: int):
        """Format x-axis labels for the visible candle range."""
        if self._n == 0:
            return
        visible_count = ve - vs
        step = max(1, visible_count // 12)
        ticks = list(range(vs, ve, step))
        labels = []
        for p in ticks:
            if 0 <= p < self._n:
                t = pd.Timestamp(self._timestamps[p])
                labels.append(t.strftime("%m/%d\n%H:%M"))
            else:
                labels.append("")
        ax.set_xticks(ticks)
        ax.set_xticklabels(labels, fontsize=7, color=CLR_TEXT)

    def _on_scroll_zoom(self, event):
        """Mouse wheel zoom — scroll up = zoom in, scroll down = zoom out."""
        if self._n == 0 or event.inaxes is None:
            return
        # Accept zoom from both price chart and indicator subplots
        valid_axes = [self._price_ax] + self._ind_axes
        if event.inaxes not in valid_axes:
            return

        # Zoom factor
        if event.button == "up":
            scale = 0.8   # zoom in: show 80% of current range
        elif event.button == "down":
            scale = 1.25  # zoom out: show 125% of current range
        else:
            return

        visible = self._view_end - self._view_start
        new_visible = int(visible * scale)
        new_visible = max(self._min_visible, min(new_visible, self._n))

        # Zoom centered on mouse position
        if event.xdata is not None:
            mouse_ratio = (event.xdata - self._view_start) / max(visible, 1)
        else:
            mouse_ratio = 0.5

        mouse_ratio = max(0.0, min(1.0, mouse_ratio))
        new_start = int(round(event.xdata - new_visible * mouse_ratio)) if event.xdata is not None else self._view_start
        new_start = max(0, min(new_start, self._n - new_visible))
        new_end = new_start + new_visible

        self._view_start = new_start
        self._view_end = min(new_end, self._n)

        # Fast update: just change axis limits, don't redraw everything
        self._apply_view_limits()
        self._sync_indicator_xlim()
        if self._price_canvas:
            self._price_canvas.draw_idle()
        if self._ind_canvas:
            self._ind_canvas.draw_idle()

    def _on_drag_start(self, event):
        """Start panning on middle-click or left-click drag."""
        if event.inaxes != self._price_ax:
            return
        if event.button == 1:  # left click = pan
            self._drag_active = True
            self._drag_start_x = event.xdata
            self._drag_view_start = self._view_start

    def _on_drag_end(self, event):
        """End panning."""
        self._drag_active = False
        self._drag_start_x = None

    def _on_mouse_price(self, event):
        if event.inaxes != self._price_ax or event.xdata is None:
            return

        # Handle drag/pan
        if self._drag_active and self._drag_start_x is not None:
            dx = int(round(self._drag_start_x - event.xdata))
            visible = self._view_end - self._view_start
            new_start = max(0, min(self._drag_view_start + dx, self._n - visible))
            new_end = new_start + visible
            if new_start != self._view_start:
                self._view_start = new_start
                self._view_end = new_end
                self._apply_view_limits()
                self._sync_indicator_xlim()
                if self._price_canvas:
                    self._price_canvas.draw_idle()
                if self._ind_canvas:
                    self._ind_canvas.draw_idle()
            return

        # Crosshair
        idx = max(0, min(int(round(event.xdata)), self._n - 1))
        self._update_crosshair(idx, event.ydata)

    def _sync_indicator_xlim(self):
        """Sync indicator subplot X limits with the price chart view."""
        if not self._ind_axes:
            return
        vs, ve = self._view_start, self._view_end
        x_pad = max(1, (ve - vs) * 0.02)
        for ax in self._ind_axes:
            ax.set_xlim(vs - x_pad, ve - 1 + x_pad)

    def _reset_zoom(self):
        """Reset zoom to show all candles."""
        if self._n == 0:
            return
        self._view_start = 0
        self._view_end = self._n
        self._apply_view_limits()
        self._sync_indicator_xlim()
        if self._price_canvas:
            self._price_canvas.draw_idle()
        if self._ind_canvas:
            self._ind_canvas.draw_idle()

    # ══════════════════════════════════════════════════════════════════════
    # OVERLAY DRAWING
    # ══════════════════════════════════════════════════════════════════════

    def _draw_overlay(self, ax, ind: dict):
        name = ind["name"]
        d = ind["data"]
        x = self._x
        if not d:
            return

        if name == "EMA \u2191":
            p = ind["params"]
            ax.plot(x, d["ema1"], color=CLR_CYAN, linewidth=1, alpha=0.8,
                    label=f"EMA {p.get('P1', 9)}")
            ax.plot(x, d["ema2"], color=CLR_ORANGE, linewidth=1, alpha=0.8,
                    label=f"EMA {p.get('P2', 21)}")

        elif name == "SMA \u2191":
            p = ind["params"]
            ax.plot(x, d["sma1"], color=CLR_YELLOW, linewidth=1, alpha=0.8,
                    label=f"SMA {p.get('P1', 20)}")
            ax.plot(x, d["sma2"], color=CLR_PURPLE, linewidth=1, alpha=0.8,
                    label=f"SMA {p.get('P2', 200)}")

        elif name == "Bollinger Bands \u2191":
            ax.plot(x, d["upper"], color=CLR_BLUE, linewidth=0.8, alpha=0.7, label="BB")
            ax.plot(x, d["middle"], color=CLR_BLUE, linewidth=0.6, alpha=0.4, linestyle="--")
            ax.plot(x, d["lower"], color=CLR_BLUE, linewidth=0.8, alpha=0.7)
            ax.fill_between(x, d["upper"], d["lower"], color=CLR_BLUE, alpha=0.06)

        elif name == "Keltner \u2191":
            ax.plot(x, d["upper"], color=CLR_PURPLE, linewidth=0.8, alpha=0.7, label="KC")
            ax.plot(x, d["middle"], color=CLR_PURPLE, linewidth=0.6, alpha=0.4, linestyle="--")
            ax.plot(x, d["lower"], color=CLR_PURPLE, linewidth=0.8, alpha=0.7)
            ax.fill_between(x, d["upper"], d["lower"], color=CLR_PURPLE, alpha=0.05)

        elif name == "Donchian \u2191":
            ax.plot(x, d["upper"], color=CLR_TEAL, linewidth=0.8, alpha=0.7, label="DC")
            ax.plot(x, d["middle"], color=CLR_TEAL, linewidth=0.6, alpha=0.4, linestyle="--")
            ax.plot(x, d["lower"], color=CLR_TEAL, linewidth=0.8, alpha=0.7)
            ax.fill_between(x, d["upper"], d["lower"], color=CLR_TEAL, alpha=0.05)

        elif name == "Ichimoku \u2191":
            ax.plot(x, d["tenkan"], color=CLR_RED, linewidth=0.8, alpha=0.7, label="Tenkan")
            ax.plot(x, d["kijun"], color=CLR_BLUE, linewidth=0.8, alpha=0.7, label="Kijun")
            sa, sb = d["senkou_a"], d["senkou_b"]
            ax.fill_between(x, sa, sb, where=(sa >= sb), color=CLR_GREEN, alpha=0.08)
            ax.fill_between(x, sa, sb, where=(sa < sb), color=CLR_RED, alpha=0.08)

        elif name == "PSAR \u2191":
            up_m = d["trend"] == 1
            dn_m = d["trend"] == -1
            ax.scatter(x[up_m], d["psar"][up_m], color=CLR_GREEN, s=4, marker=".",
                       alpha=0.7, label="PSAR")
            ax.scatter(x[dn_m], d["psar"][dn_m], color=CLR_RED, s=4, marker=".", alpha=0.7)

        elif name == "Supertrend \u2191":
            st_val = d["supertrend"]
            direction = d["direction"]
            for i in range(1, self._n):
                clr = CLR_GREEN if direction[i] == 1 else CLR_RED
                ax.plot([x[i - 1], x[i]], [st_val[i - 1], st_val[i]],
                        color=clr, linewidth=1.2, alpha=0.8)
            ax.plot([], [], color=CLR_GREEN, linewidth=1.2, label="ST Up")
            ax.plot([], [], color=CLR_RED, linewidth=1.2, label="ST Down")

        elif name == "VWAP \u2191":
            ax.plot(x, d["vwap"], color=CLR_PURPLE, linewidth=1, alpha=0.8,
                    linestyle="-.", label="VWAP")

        elif name == "HMA \u2191":
            hma_vals = d["hma"]
            valid = ~np.isnan(hma_vals)
            if valid.any():
                ax.plot(x[valid], hma_vals[valid], color=CLR_PINK, linewidth=1.2, alpha=0.8,
                        label=f"HMA {ind['params'].get('Periyot', 20)}")

    # ══════════════════════════════════════════════════════════════════════
    # INDICATOR SUBPLOTS (SCROLLABLE)
    # ══════════════════════════════════════════════════════════════════════

    def _draw_indicator_charts(self):
        self._destroy_ind_canvas()

        subplots = [ind for ind in self._active
                    if INDICATOR_DEFS.get(ind["name"], {}).get("type") == "subplot"]
        if not subplots:
            return

        n_sub = len(subplots)
        h_per = 2.0  # inches per subplot
        total_h = n_sub * h_per

        fig = Figure(figsize=(20, total_h), facecolor=CLR_BG, dpi=80)
        fig.subplots_adjust(left=0.04, right=0.96,
                            top=1.0 - 0.015 / max(total_h, 1),
                            bottom=0.015 / max(total_h, 1),
                            hspace=0.45)

        gs = GridSpec(n_sub, 1, figure=fig, hspace=0.45)

        self._ind_axes = []
        self._ind_vlines = []
        self._value_texts = []

        for i, ind in enumerate(subplots):
            ax = fig.add_subplot(gs[i, 0])
            ax.set_facecolor(CLR_SUBPLOT)
            ax.tick_params(colors=CLR_TEXT, labelsize=7)
            ax.grid(True, color=CLR_GRID, alpha=0.3, linewidth=0.5)
            for spine in ax.spines.values():
                spine.set_color(CLR_GRID)

            self._draw_subplot(ax, ind)

            if i < n_sub - 1:
                ax.tick_params(labelbottom=False)
            else:
                self._format_xaxis_ranged(ax, self._view_start, self._view_end)

            vl = ax.axvline(0, color=CLR_WHITE, linewidth=0.5, alpha=0.5, visible=False)
            self._ind_vlines.append(vl)

            vt = ax.text(
                0.99, 0.90, "", transform=ax.transAxes, fontsize=8, color=CLR_TEXT,
                ha="right", va="top",
                bbox=dict(boxstyle="round,pad=0.2", fc=CLR_SUBPLOT, ec=CLR_GRID, alpha=0.85),
            )
            self._value_texts.append(vt)
            self._ind_axes.append(ax)

        self._ind_fig = fig
        self._ind_canvas = FigureCanvasTkAgg(fig, master=self._ind_scroll)
        w = self._ind_canvas.get_tk_widget()
        w.configure(height=int(total_h * 80))
        w.pack(fill="x", expand=False)

        # Sync indicator X limits with price chart zoom
        self._sync_indicator_xlim()
        self._ind_canvas.draw_idle()

        self._ind_canvas.mpl_connect("motion_notify_event", self._on_mouse_ind)
        self._ind_canvas.mpl_connect("axes_leave_event", self._on_mouse_leave)
        self._ind_canvas.mpl_connect("scroll_event", self._on_scroll_zoom)

    # ══════════════════════════════════════════════════════════════════════
    # SUBPLOT DRAWING
    # ══════════════════════════════════════════════════════════════════════

    def _draw_subplot(self, ax, ind: dict):
        name = ind["name"]
        d = ind["data"]
        x = self._x
        if not d:
            ax.set_title(name, fontsize=8, color=CLR_TEXT, loc="left", pad=2)
            return

        if name == "RSI":
            r = d["rsi"]
            ax.plot(x, r, color=CLR_PURPLE, linewidth=1)
            ax.axhline(30, color=CLR_GREEN, linewidth=0.5, linestyle="--", alpha=0.5)
            ax.axhline(70, color=CLR_RED, linewidth=0.5, linestyle="--", alpha=0.5)
            ax.fill_between(x, r, 30, where=(r < 30), color=CLR_GREEN, alpha=0.12)
            ax.fill_between(x, r, 70, where=(r > 70), color=CLR_RED, alpha=0.12)
            ax.set_ylim(0, 100)
            ax.set_title("RSI", fontsize=8, color=CLR_TEXT, loc="left", pad=2)

        elif name == "MACD":
            hist = d["histogram"]
            colors = [CLR_GREEN if h >= 0 else CLR_RED for h in hist]
            ax.bar(x, hist, color=colors, alpha=0.6, width=0.7)
            ax.plot(x, d["macd"], color=CLR_BLUE, linewidth=0.8, label="MACD")
            ax.plot(x, d["signal"], color=CLR_ORANGE, linewidth=0.8, label="Signal")
            ax.axhline(0, color=CLR_GRAY, linewidth=0.5)
            ax.legend(loc="upper left", fontsize=6, facecolor=CLR_SUBPLOT,
                      edgecolor=CLR_GRID, labelcolor=CLR_TEXT)
            ax.set_title("MACD", fontsize=8, color=CLR_TEXT, loc="left", pad=2)

        elif name == "ADX":
            ax.plot(x, d["adx"], color=CLR_BLUE, linewidth=1, label="ADX")
            ax.plot(x, d["plus_di"], color=CLR_GREEN, linewidth=0.8, label="+DI")
            ax.plot(x, d["minus_di"], color=CLR_RED, linewidth=0.8, label="-DI")
            ax.axhline(20, color=CLR_GRAY, linewidth=0.5, linestyle="--", alpha=0.5)
            ax.axhline(25, color=CLR_ORANGE, linewidth=0.5, linestyle="--", alpha=0.5)
            ax.legend(loc="upper left", fontsize=6, facecolor=CLR_SUBPLOT,
                      edgecolor=CLR_GRID, labelcolor=CLR_TEXT)
            ax.set_title("ADX", fontsize=8, color=CLR_TEXT, loc="left", pad=2)

        elif name == "Stochastic":
            ax.plot(x, d["k"], color=CLR_BLUE, linewidth=0.8, label="%K")
            ax.plot(x, d["d"], color=CLR_ORANGE, linewidth=0.8, label="%D")
            ax.axhline(20, color=CLR_GREEN, linewidth=0.5, linestyle="--", alpha=0.5)
            ax.axhline(80, color=CLR_RED, linewidth=0.5, linestyle="--", alpha=0.5)
            ax.set_ylim(0, 100)
            ax.legend(loc="upper left", fontsize=6, facecolor=CLR_SUBPLOT,
                      edgecolor=CLR_GRID, labelcolor=CLR_TEXT)
            ax.set_title("Stochastic", fontsize=8, color=CLR_TEXT, loc="left", pad=2)

        elif name == "Stochastic RSI":
            ax.plot(x, d["k"], color=CLR_BLUE, linewidth=0.8, label="%K")
            ax.plot(x, d["d"], color=CLR_ORANGE, linewidth=0.8, label="%D")
            ax.axhline(20, color=CLR_GREEN, linewidth=0.5, linestyle="--", alpha=0.5)
            ax.axhline(80, color=CLR_RED, linewidth=0.5, linestyle="--", alpha=0.5)
            ax.set_ylim(0, 100)
            ax.legend(loc="upper left", fontsize=6, facecolor=CLR_SUBPLOT,
                      edgecolor=CLR_GRID, labelcolor=CLR_TEXT)
            ax.set_title("Stochastic RSI", fontsize=8, color=CLR_TEXT, loc="left", pad=2)

        elif name == "MFI":
            ax.plot(x, d["mfi"], color=CLR_TEAL, linewidth=1)
            ax.axhline(20, color=CLR_GREEN, linewidth=0.5, linestyle="--", alpha=0.5)
            ax.axhline(80, color=CLR_RED, linewidth=0.5, linestyle="--", alpha=0.5)
            ax.set_ylim(0, 100)
            ax.set_title("MFI", fontsize=8, color=CLR_TEXT, loc="left", pad=2)

        elif name == "CCI":
            cc = d["cci"]
            ax.plot(x, cc, color=CLR_BLUE, linewidth=1)
            ax.axhline(100, color=CLR_RED, linewidth=0.5, linestyle="--", alpha=0.5)
            ax.axhline(-100, color=CLR_GREEN, linewidth=0.5, linestyle="--", alpha=0.5)
            ax.axhline(0, color=CLR_GRAY, linewidth=0.5, alpha=0.3)
            ax.fill_between(x, cc, 100, where=(cc > 100), color=CLR_RED, alpha=0.08)
            ax.fill_between(x, cc, -100, where=(cc < -100), color=CLR_GREEN, alpha=0.08)
            ax.set_title("CCI", fontsize=8, color=CLR_TEXT, loc="left", pad=2)

        elif name == "Williams %R":
            ax.plot(x, d["wr"], color=CLR_PURPLE, linewidth=1)
            ax.axhline(-20, color=CLR_RED, linewidth=0.5, linestyle="--", alpha=0.5)
            ax.axhline(-80, color=CLR_GREEN, linewidth=0.5, linestyle="--", alpha=0.5)
            ax.set_ylim(-100, 0)
            ax.set_title("Williams %R", fontsize=8, color=CLR_TEXT, loc="left", pad=2)

        elif name == "ROC":
            rc = d["roc"]
            ax.plot(x, rc, color=CLR_BLUE, linewidth=1)
            ax.axhline(0, color=CLR_GRAY, linewidth=0.5)
            ax.fill_between(x, rc, 0, where=(rc >= 0), color=CLR_GREEN, alpha=0.08)
            ax.fill_between(x, rc, 0, where=(rc < 0), color=CLR_RED, alpha=0.08)
            ax.set_title("ROC", fontsize=8, color=CLR_TEXT, loc="left", pad=2)

        elif name == "Ultimate Osc":
            ax.plot(x, d["uo"], color=CLR_BLUE, linewidth=1)
            ax.axhline(30, color=CLR_GREEN, linewidth=0.5, linestyle="--", alpha=0.5)
            ax.axhline(70, color=CLR_RED, linewidth=0.5, linestyle="--", alpha=0.5)
            ax.set_title("Ultimate Oscillator", fontsize=8, color=CLR_TEXT, loc="left", pad=2)

        elif name == "OBV":
            ax.plot(x, d["obv"], color=CLR_BLUE, linewidth=1)
            ax.set_title("OBV", fontsize=8, color=CLR_TEXT, loc="left", pad=2)

        elif name == "CVD":
            ax.plot(x, d["cvd"], color=CLR_CYAN, linewidth=1)
            ax.set_title("CVD", fontsize=8, color=CLR_TEXT, loc="left", pad=2)

        elif name == "CMF":
            cm = d["cmf"]
            ax.plot(x, cm, color=CLR_BLUE, linewidth=1)
            ax.axhline(0, color=CLR_GRAY, linewidth=0.5)
            ax.fill_between(x, cm, 0, where=(cm >= 0), color=CLR_GREEN, alpha=0.12)
            ax.fill_between(x, cm, 0, where=(cm < 0), color=CLR_RED, alpha=0.12)
            ax.set_title("CMF", fontsize=8, color=CLR_TEXT, loc="left", pad=2)

        elif name == "A/D Line":
            ax.plot(x, d["ad"], color=CLR_BLUE, linewidth=1)
            ax.set_title("A/D Line", fontsize=8, color=CLR_TEXT, loc="left", pad=2)

        elif name == "Elder Force":
            ef = d["ef"]
            colors = [CLR_GREEN if v >= 0 else CLR_RED for v in ef]
            ax.bar(x, ef, color=colors, alpha=0.6, width=0.7)
            ax.axhline(0, color=CLR_GRAY, linewidth=0.5)
            ax.set_title("Elder Force Index", fontsize=8, color=CLR_TEXT, loc="left", pad=2)

        elif name == "Hacim":
            vol = d["volume"]
            colors = [CLR_GREEN if self._close[i] >= self._open[i] else CLR_RED
                      for i in range(self._n)]
            ax.bar(x, vol, color=colors, alpha=0.6, width=0.7)
            vm = d.get("vol_ma")
            if vm is not None:
                valid = ~np.isnan(vm)
                ax.plot(x[valid], vm[valid], color=CLR_ORANGE, linewidth=1, label="MA20")
                ax.legend(loc="upper left", fontsize=6, facecolor=CLR_SUBPLOT,
                          edgecolor=CLR_GRID, labelcolor=CLR_TEXT)
            ax.set_title("Hacim", fontsize=8, color=CLR_TEXT, loc="left", pad=2)

        elif name == "ATR":
            ax.plot(x, d["atr"], color=CLR_ORANGE, linewidth=1)
            ax.set_title("ATR", fontsize=8, color=CLR_TEXT, loc="left", pad=2)

        elif name == "BB Width":
            ax.plot(x, d["bbw"], color=CLR_BLUE, linewidth=1)
            ax.set_title("BB Width", fontsize=8, color=CLR_TEXT, loc="left", pad=2)

        elif name == "Aroon":
            ax.plot(x, d["aroon_up"], color=CLR_GREEN, linewidth=0.8, label="Up")
            ax.plot(x, d["aroon_down"], color=CLR_RED, linewidth=0.8, label="Down")
            ax.axhline(50, color=CLR_GRAY, linewidth=0.5, linestyle="--", alpha=0.3)
            ax.set_ylim(0, 100)
            ax.legend(loc="upper left", fontsize=6, facecolor=CLR_SUBPLOT,
                      edgecolor=CLR_GRID, labelcolor=CLR_TEXT)
            ax.set_title("Aroon", fontsize=8, color=CLR_TEXT, loc="left", pad=2)

    # ══════════════════════════════════════════════════════════════════════
    # X-AXIS FORMATTING
    # ══════════════════════════════════════════════════════════════════════

    def _format_xaxis(self, ax):
        if self._n == 0:
            return
        step = max(1, self._n // 12)
        ticks = list(range(0, self._n, step))
        labels = []
        for p in ticks:
            t = pd.Timestamp(self._timestamps[p])
            labels.append(t.strftime("%m/%d\n%H:%M"))
        ax.set_xticks(ticks)
        ax.set_xticklabels(labels, fontsize=7, color=CLR_TEXT)

    # ══════════════════════════════════════════════════════════════════════
    # CROSSHAIR / INTERACTION
    # ══════════════════════════════════════════════════════════════════════

    def _on_mouse_ind(self, event):
        if not event.inaxes or event.xdata is None:
            return
        idx = max(0, min(int(round(event.xdata)), self._n - 1))
        self._update_crosshair(idx, None)

    def _on_mouse_leave(self, event):
        if self._price_vline:
            self._price_vline.set_visible(False)
        if self._price_hline:
            self._price_hline.set_visible(False)
        for vl in self._ind_vlines:
            vl.set_visible(False)
        for vt in self._value_texts:
            vt.set_text("")
        if self._price_canvas:
            self._price_canvas.draw_idle()
        if self._ind_canvas:
            self._ind_canvas.draw_idle()
        self._info_var.set("")
        self._last_cross_idx = -1

    def _update_crosshair(self, idx: int, y_price: float | None):
        if idx == self._last_cross_idx:
            return
        self._last_cross_idx = idx

        # Price chart crosshair
        if self._price_vline:
            self._price_vline.set_xdata([idx])
            self._price_vline.set_visible(True)
        if self._price_hline and y_price is not None:
            self._price_hline.set_ydata([y_price])
            self._price_hline.set_visible(True)

        # Indicator crosshair lines
        for vl in self._ind_vlines:
            vl.set_xdata([idx])
            vl.set_visible(True)

        # Update value annotations on indicator subplots
        subplots = [ind for ind in self._active
                    if INDICATOR_DEFS.get(ind["name"], {}).get("type") == "subplot"]
        for i, ind in enumerate(subplots):
            if i < len(self._value_texts):
                self._value_texts[i].set_text(self._get_value_str(ind, idx))

        # Update info bar
        self._update_info(idx)

        # Redraw
        if self._price_canvas:
            self._price_canvas.draw_idle()
        if self._ind_canvas:
            self._ind_canvas.draw_idle()

    def _get_value_str(self, ind: dict, idx: int) -> str:
        name = ind["name"]
        d = ind["data"]
        if not d or idx < 0:
            return ""

        def _v(key):
            arr = d.get(key)
            if arr is None or idx >= len(arr):
                return np.nan
            val = arr[idx]
            return val if np.isfinite(val) else np.nan

        if name == "RSI":
            return f"RSI: {_v('rsi'):.1f}"
        if name == "MACD":
            return f"MACD: {_v('macd'):.2f}  Sig: {_v('signal'):.2f}"
        if name == "ADX":
            return f"ADX: {_v('adx'):.1f}  +DI: {_v('plus_di'):.1f}  -DI: {_v('minus_di'):.1f}"
        if name in ("Stochastic", "Stochastic RSI"):
            return f"%K: {_v('k'):.1f}  %D: {_v('d'):.1f}"
        if name == "MFI":
            return f"MFI: {_v('mfi'):.1f}"
        if name == "CCI":
            return f"CCI: {_v('cci'):.1f}"
        if name == "Williams %R":
            return f"%R: {_v('wr'):.1f}"
        if name == "ROC":
            return f"ROC: {_v('roc'):.2f}%"
        if name == "Ultimate Osc":
            return f"UO: {_v('uo'):.1f}"
        if name == "OBV":
            return f"OBV: {_v('obv'):,.0f}"
        if name == "CVD":
            return f"CVD: {_v('cvd'):,.0f}"
        if name == "CMF":
            return f"CMF: {_v('cmf'):.4f}"
        if name == "A/D Line":
            return f"A/D: {_v('ad'):,.0f}"
        if name == "Elder Force":
            return f"EFI: {_v('ef'):,.0f}"
        if name == "Hacim":
            return f"Vol: {_v('volume'):,.0f}"
        if name == "ATR":
            return f"ATR: {_v('atr'):.4f}"
        if name == "BB Width":
            return f"BBW: {_v('bbw'):.2f}%"
        if name == "Aroon":
            return f"Up: {_v('aroon_up'):.0f}  Down: {_v('aroon_down'):.0f}"
        return ""

    def _update_info(self, idx: int):
        if idx < 0 or idx >= self._n:
            return
        ts = pd.Timestamp(self._timestamps[idx]).strftime("%Y-%m-%d %H:%M")
        o, h, lo, c = self._open[idx], self._high[idx], self._low[idx], self._close[idx]
        v = self._volume[idx]

        prec = 2 if c >= 1 else 6

        info = (f"{ts} \u2502 O:{o:.{prec}f}  H:{h:.{prec}f}  "
                f"L:{lo:.{prec}f}  C:{c:.{prec}f} \u2502 Vol:{v:,.0f}")

        # Append overlay values
        for ind in self._active:
            if INDICATOR_DEFS.get(ind["name"], {}).get("type") != "overlay":
                continue
            d = ind["data"]
            if not d:
                continue
            nm = ind["name"]

            def _ov(key):
                arr = d.get(key)
                if arr is None or idx >= len(arr):
                    return 0.0
                val = arr[idx]
                return val if np.isfinite(val) else 0.0

            if nm == "EMA \u2191":
                info += f" \u2502 EMA:{_ov('ema1'):.{prec}f}/{_ov('ema2'):.{prec}f}"
            elif nm == "SMA \u2191":
                info += f" \u2502 SMA:{_ov('sma1'):.{prec}f}/{_ov('sma2'):.{prec}f}"
            elif nm == "VWAP \u2191":
                info += f" \u2502 VWAP:{_ov('vwap'):.{prec}f}"
            elif nm == "Supertrend \u2191":
                info += f" \u2502 ST:{_ov('supertrend'):.{prec}f}"

        self._info_var.set(info)

    # ══════════════════════════════════════════════════════════════════════
    # LIVE MODE
    # ══════════════════════════════════════════════════════════════════════

    def _toggle_live(self):
        if self._live_running:
            self._stop_live()
        else:
            self._start_live()

    def _start_live(self):
        self._live_running = True
        self._live_btn.configure(text="Canl\u0131: A\u00c7IK", fg_color="#1B5E20")
        self._live_thread = threading.Thread(target=self._live_loop, daemon=True)
        self._live_thread.start()

    def _stop_live(self):
        self._live_running = False
        self._live_btn.configure(text="Canl\u0131: Kapal\u0131", fg_color="#333333")

    def _live_loop(self):
        while self._live_running:
            try:
                df = self._fetch_klines()
                if df is not None and not df.empty:
                    with self._computation_lock:
                        self._prepare_data(df)
                        for ind in self._active:
                            ind["data"] = self._compute_indicator(ind)
                    self.after(0, self._refresh_charts)
                    now = datetime.now().strftime("%H:%M:%S")
                    sym = self._symbol_var.get()
                    self.after(0, lambda t=now, s=sym: self._progress_var.set(
                        f"Canl\u0131 \u2502 {s} \u2502 {t}"
                    ))

                # Adaptive wait: max 10s or candle interval
                interval = _INTERVAL_SEC.get(self._tf_var.get(), 60)
                wait = min(interval, 10)
                for _ in range(int(wait)):
                    if not self._live_running:
                        return
                    time.sleep(1)

            except Exception as e:
                logger.error(f"Live loop error: {e}")
                time.sleep(5)
