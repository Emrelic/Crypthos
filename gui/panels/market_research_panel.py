"""Piyasa Arastirma Paneli — Rejim tespiti + grafik + indikatorler."""

import threading
import numpy as np
import pandas as pd
import customtkinter as ctk
from loguru import logger

from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
from matplotlib.figure import Figure

# ─── ER / Hurst hesaplama (system_b_scanner'dan) ────────────────────
def _efficiency_ratio(closes: np.ndarray) -> float:
    if len(closes) < 2:
        return 0.5
    net = abs(closes[-1] - closes[0])
    total = np.sum(np.abs(np.diff(closes)))
    return net / total if total > 0 else 0.0


def _rolling_er(closes: np.ndarray, window: int = 20, n: int = 10) -> float:
    if len(closes) < window + n:
        return _efficiency_ratio(closes[-window:] if len(closes) >= window else closes)
    ers = []
    for i in range(len(closes) - window + 1):
        seg = closes[i:i + window]
        net = abs(seg[-1] - seg[0])
        total = np.sum(np.abs(np.diff(seg)))
        ers.append(net / total if total > 0 else 0.0)
    return float(np.median(ers[-n:]))


def _hurst(closes: np.ndarray) -> float:
    if len(closes) < 64:
        return 0.5
    lr = np.diff(np.log(closes))
    ns = [n for n in [8, 12, 16, 24, 32, 48, 64, 96, 128] if n <= len(lr)]
    if len(ns) < 3:
        return 0.5
    pts = []
    for n in ns:
        rs_list = []
        step = max(1, n // 2)
        for s in range(0, len(lr) - n + 1, step):
            chunk = lr[s:s + n]
            dev = np.cumsum(chunk - np.mean(chunk))
            R = np.max(dev) - np.min(dev)
            S = np.std(chunk, ddof=1)
            if S > 0:
                rs_list.append(R / S)
        if rs_list:
            pts.append((np.log(n), np.log(np.mean(rs_list))))
    if len(pts) < 3:
        return 0.5
    x = np.array([p[0] for p in pts])
    y = np.array([p[1] for p in pts])
    H = (len(x) * np.sum(x * y) - np.sum(x) * np.sum(y)) / \
        (len(x) * np.sum(x ** 2) - np.sum(x) ** 2)
    return float(np.clip(H, 0.0, 1.0))


def _classify(er: float, hurst: float) -> str:
    score = 0
    if er < 0.15:
        score -= 2
    elif er < 0.25:
        score -= 1
    elif er > 0.40:
        score += 2
    elif er > 0.30:
        score += 1
    if hurst < 0.42:
        score -= 2
    elif hurst < 0.48:
        score -= 1
    elif hurst > 0.58:
        score += 2
    elif hurst > 0.52:
        score += 1
    if score >= 2:
        return "TREND"
    elif score <= -2:
        return "RANGING"
    elif score > 0:
        return "WEAK TREND"
    elif score < 0:
        return "WEAK RANGE"
    return "BELIRSIZ"


# ─── Basit indikator hesaplamalari ──────────────────────────────────
def _ema(arr, n):
    s = pd.Series(arr)
    return s.ewm(span=n, adjust=False).mean().values


def _rsi(closes, n=14):
    d = np.diff(closes)
    gain = np.where(d > 0, d, 0.0)
    loss = np.where(d < 0, -d, 0.0)
    avg_g = pd.Series(gain).ewm(span=n, adjust=False).mean().values
    avg_l = pd.Series(loss).ewm(span=n, adjust=False).mean().values
    rs = avg_g / np.where(avg_l > 0, avg_l, 1e-10)
    rsi = 100.0 - 100.0 / (1.0 + rs)
    return rsi[-1] if len(rsi) > 0 else 50.0


def _macd(closes, fast=12, slow=26, sig=9):
    f = _ema(closes, fast)
    s = _ema(closes, slow)
    line = f - s
    signal = _ema(line, sig)
    hist = line - signal
    return line[-1], signal[-1], hist[-1]


def _adx(highs, lows, closes, n=14):
    if len(highs) < n + 2:
        return 0.0
    up = np.diff(highs)
    down = -np.diff(lows)
    pdm = np.where((up > down) & (up > 0), up, 0.0)
    ndm = np.where((down > up) & (down > 0), down, 0.0)
    tr1 = highs[1:] - lows[1:]
    tr2 = np.abs(highs[1:] - closes[:-1])
    tr3 = np.abs(lows[1:] - closes[:-1])
    tr = np.maximum(tr1, np.maximum(tr2, tr3))
    atr = pd.Series(tr).ewm(span=n, adjust=False).mean().values
    pdi = 100 * pd.Series(pdm).ewm(span=n, adjust=False).mean().values / np.where(atr > 0, atr, 1e-10)
    ndi = 100 * pd.Series(ndm).ewm(span=n, adjust=False).mean().values / np.where(atr > 0, atr, 1e-10)
    dx = 100 * np.abs(pdi - ndi) / np.where((pdi + ndi) > 0, pdi + ndi, 1e-10)
    adx_val = pd.Series(dx).ewm(span=n, adjust=False).mean().values
    return float(adx_val[-1]) if len(adx_val) > 0 else 0.0


def _bb(closes, n=20, k=2.0):
    s = pd.Series(closes)
    mid = s.rolling(n).mean().values
    std = s.rolling(n).std().values
    upper = mid + k * std
    lower = mid - k * std
    width = ((upper - lower) / np.where(mid > 0, mid, 1e-10)) * 100
    return mid, upper, lower, width


def _atr(highs, lows, closes, n=14):
    if len(highs) < 2:
        return 0.0
    tr1 = highs[1:] - lows[1:]
    tr2 = np.abs(highs[1:] - closes[:-1])
    tr3 = np.abs(lows[1:] - closes[:-1])
    tr = np.maximum(tr1, np.maximum(tr2, tr3))
    atr_vals = pd.Series(tr).ewm(span=n, adjust=False).mean().values
    return float(atr_vals[-1]) if len(atr_vals) > 0 else 0.0


# ─── Renk sabitleri ─────────────────────────────────────────────────
_BG = "#1a1a2e"
_FG = "#e0e0e0"
_GREEN = "#00E676"
_RED = "#FF5252"
_YELLOW = "#FFD54F"
_CYAN = "#4FC3F7"
_PURPLE = "#CE93D8"
_ORANGE = "#FF8A65"

ALL_TFS = ["1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "8h", "12h", "1d"]


class MarketResearchPanel(ctk.CTkFrame):
    """Piyasa arastirma paneli: rejim tespiti + grafik + indikatorler."""

    def __init__(self, parent, controller):
        super().__init__(parent)
        self.controller = controller
        self.pack(fill="both", expand=True)
        self._canvas = None
        self._toolbar = None
        self._fig = None
        self._analysis_data = None
        self._build_ui()

    # ─── UI ──────────────────────────────────────────────────────────
    def _build_ui(self):
        # Title
        ctk.CTkLabel(self, text="Piyasa Arastirma — Rejim + Grafik + Indikatorler",
                     font=ctk.CTkFont(size=14, weight="bold"),
                     text_color=_CYAN).pack(anchor="w", padx=8, pady=(4, 0))

        # ── Top controls ──
        ctrl = ctk.CTkFrame(self, height=40)
        ctrl.pack(fill="x", padx=5, pady=(3, 1))

        ctk.CTkLabel(ctrl, text="Coin:", font=ctk.CTkFont(size=12)).pack(side="left", padx=(4, 2))
        self._sym_var = ctk.StringVar(value="BTCUSDT")
        self._sym_combo = ctk.CTkComboBox(ctrl, variable=self._sym_var, width=140,
                                           values=["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"])
        self._sym_combo.pack(side="left", padx=2)

        ctk.CTkLabel(ctrl, text="TF:", font=ctk.CTkFont(size=12)).pack(side="left", padx=(8, 2))
        self._tf_var = ctk.StringVar(value="15m")
        ctk.CTkComboBox(ctrl, variable=self._tf_var, width=80,
                        values=ALL_TFS).pack(side="left", padx=2)

        ctk.CTkLabel(ctrl, text="Mum:", font=ctk.CTkFont(size=12)).pack(side="left", padx=(8, 2))
        self._candle_var = ctk.StringVar(value="200")
        ctk.CTkEntry(ctrl, textvariable=self._candle_var, width=60).pack(side="left", padx=2)

        self._analyze_btn = ctk.CTkButton(ctrl, text="Analiz Et", width=90,
                                           fg_color="#1565C0", hover_color="#1976D2",
                                           command=self._on_analyze)
        self._analyze_btn.pack(side="left", padx=(10, 2))

        ctk.CTkButton(ctrl, text="Coin Listesi", width=90, fg_color="#37474F",
                      command=self._refresh_symbols).pack(side="left", padx=2)

        self._status_var = ctk.StringVar(value="Hazir")
        ctk.CTkLabel(ctrl, textvariable=self._status_var, font=ctk.CTkFont(size=11),
                     text_color="#90CAF9").pack(side="right", padx=8)

        # ── Main content: left chart + right info ──
        content = ctk.CTkFrame(self)
        content.pack(fill="both", expand=True, padx=3, pady=(1, 3))

        # Chart area (left, 65%)
        self._chart_frame = ctk.CTkFrame(content)
        self._chart_frame.pack(side="left", fill="both", expand=True, padx=(0, 2))

        # Right panel (35%) — regime + indicators
        right = ctk.CTkFrame(content, width=340)
        right.pack(side="right", fill="y", padx=(2, 0))
        right.pack_propagate(False)

        # Regime section
        ctk.CTkLabel(right, text="REJIM TESPITI", font=ctk.CTkFont(size=13, weight="bold"),
                     text_color=_PURPLE).pack(anchor="w", padx=6, pady=(6, 2))
        self._regime_frame = ctk.CTkScrollableFrame(right, height=180)
        self._regime_frame.pack(fill="x", padx=4, pady=(0, 4))

        # Indicators section
        ctk.CTkLabel(right, text="INDIKATORLER", font=ctk.CTkFont(size=13, weight="bold"),
                     text_color=_YELLOW).pack(anchor="w", padx=6, pady=(4, 2))
        self._ind_frame = ctk.CTkScrollableFrame(right)
        self._ind_frame.pack(fill="both", expand=True, padx=4, pady=(0, 4))

    # ─── Coin listesi ───────────────────────────────────────────────
    def _refresh_symbols(self):
        def _fetch():
            try:
                rest = self.controller.rest_client
                if not rest:
                    self._status_var.set("REST client yok")
                    return
                tickers = rest.get_all_24h_tickers()
                syms = sorted([t["symbol"] for t in tickers
                               if t["symbol"].endswith("USDT")
                               and float(t.get("quoteVolume", 0)) > 5_000_000])
                self.after(0, lambda: self._sym_combo.configure(values=syms))
                self.after(0, lambda: self._status_var.set(f"{len(syms)} coin yuklendi"))
            except Exception as e:
                self.after(0, lambda: self._status_var.set(f"Hata: {e}"))
        threading.Thread(target=_fetch, daemon=True).start()

    # ─── Analiz baslat ──────────────────────────────────────────────
    def _on_analyze(self):
        sym = self._sym_var.get().strip().upper()
        tf = self._tf_var.get().strip()
        try:
            limit = int(self._candle_var.get())
        except ValueError:
            limit = 200
        if not sym:
            return

        self._analyze_btn.configure(state="disabled", text="Analiz...")
        self._status_var.set(f"{sym} {tf} {limit} mum analiz ediliyor...")
        threading.Thread(target=self._run_analysis, args=(sym, tf, limit), daemon=True).start()

    def _run_analysis(self, symbol: str, tf: str, limit: int):
        try:
            rest = self.controller.rest_client
            if not rest:
                self.after(0, lambda: self._status_var.set("REST client yok"))
                return

            # 1) Ana TF klines
            klines = rest.get_klines(symbol, tf, limit)
            if klines is None or klines.empty:
                self.after(0, lambda: self._status_var.set("Kline verisi alinamadi"))
                return

            opens = klines["open"].values.astype(float)
            highs = klines["high"].values.astype(float)
            lows = klines["low"].values.astype(float)
            closes = klines["close"].values.astype(float)
            volumes = klines["volume"].values.astype(float)
            times = klines.index if hasattr(klines.index, '__len__') else list(range(len(closes)))

            # 2) Indikatorler
            rsi_val = _rsi(closes, 14)
            macd_line, macd_sig, macd_hist = _macd(closes)
            adx_val = _adx(highs, lows, closes, 14)
            atr_val = _atr(highs, lows, closes, 14)
            atr_pct = atr_val / closes[-1] * 100 if closes[-1] > 0 else 0
            ema9 = _ema(closes, 9)
            ema21 = _ema(closes, 21)
            ema50 = _ema(closes, 50)
            bb_mid, bb_up, bb_lo, bb_width = _bb(closes, 20, 2.0)
            er_val = _rolling_er(closes)
            hurst_val = _hurst(closes)
            regime = _classify(er_val, hurst_val)
            vol_avg = np.mean(volumes[-20:]) if len(volumes) >= 20 else np.mean(volumes)
            vol_ratio = volumes[-1] / vol_avg if vol_avg > 0 else 1.0

            indicators = {
                "Fiyat": f"{closes[-1]:.6g}",
                "RSI(14)": f"{rsi_val:.1f}",
                "MACD Line": f"{macd_line:.6g}",
                "MACD Signal": f"{macd_sig:.6g}",
                "MACD Hist": f"{macd_hist:.6g}",
                "ADX(14)": f"{adx_val:.1f}",
                "ATR(14)": f"{atr_val:.6g}",
                "ATR%": f"{atr_pct:.3f}%",
                "EMA(9)": f"{ema9[-1]:.6g}",
                "EMA(21)": f"{ema21[-1]:.6g}",
                "EMA(50)": f"{ema50[-1]:.6g}",
                "BB Upper": f"{bb_up[-1]:.6g}" if not np.isnan(bb_up[-1]) else "-",
                "BB Lower": f"{bb_lo[-1]:.6g}" if not np.isnan(bb_lo[-1]) else "-",
                "BB Width%": f"{bb_width[-1]:.2f}%" if not np.isnan(bb_width[-1]) else "-",
                "Volume": f"{volumes[-1]:.0f}",
                "Vol Ratio": f"{vol_ratio:.2f}x",
                "ER": f"{er_val:.3f}",
                "Hurst": f"{hurst_val:.3f}",
            }

            # 3) Multi-TF rejim tespiti
            regime_results = []
            regime_results.append({"tf": tf, "er": er_val, "hurst": hurst_val,
                                   "adx": adx_val, "regime": regime, "main": True})

            for other_tf in ALL_TFS:
                if other_tf == tf:
                    continue
                try:
                    k2 = rest.get_klines(symbol, other_tf, 300)
                    if k2 is not None and not k2.empty:
                        c2 = k2["close"].values.astype(float)
                        h2 = k2["high"].values.astype(float)
                        l2 = k2["low"].values.astype(float)
                        er2 = _rolling_er(c2)
                        hu2 = _hurst(c2)
                        adx2 = _adx(h2, l2, c2, 14)
                        reg2 = _classify(er2, hu2)
                        regime_results.append({"tf": other_tf, "er": er2, "hurst": hu2,
                                               "adx": adx2, "regime": reg2, "main": False})
                except Exception:
                    pass

            self._analysis_data = {
                "symbol": symbol, "tf": tf, "limit": limit,
                "closes": closes, "highs": highs, "lows": lows, "opens": opens,
                "volumes": volumes, "times": times,
                "ema9": ema9, "ema21": ema21, "ema50": ema50,
                "bb_mid": bb_mid, "bb_up": bb_up, "bb_lo": bb_lo,
                "indicators": indicators,
                "regime_results": sorted(regime_results, key=lambda x: ALL_TFS.index(x["tf"])
                                         if x["tf"] in ALL_TFS else 99),
                "regime_main": regime,
            }

            self.after(0, self._render_results)

        except Exception as e:
            logger.error(f"Research analysis error: {e}")
            self.after(0, lambda: self._status_var.set(f"Hata: {e}"))
        finally:
            self.after(0, lambda: self._analyze_btn.configure(state="normal", text="Analiz Et"))

    # ─── Render ──────────────────────────────────────────────────────
    def _render_results(self):
        d = self._analysis_data
        if not d:
            return

        self._status_var.set(f"{d['symbol']} {d['tf']} — {d['regime_main']}")

        # Draw chart
        self._draw_chart(d)

        # Regime table
        for w in self._regime_frame.winfo_children():
            w.destroy()

        hdr = ctk.CTkFrame(self._regime_frame)
        hdr.pack(fill="x", pady=(0, 2))
        for col, w in [("TF", 45), ("ER", 50), ("Hurst", 50), ("ADX", 45), ("Rejim", 90)]:
            ctk.CTkLabel(hdr, text=col, width=w, font=ctk.CTkFont(size=11, weight="bold"),
                         text_color="#B0BEC5").pack(side="left")

        for r in d["regime_results"]:
            row = ctk.CTkFrame(self._regime_frame)
            row.pack(fill="x", pady=1)
            bg = "#1a2a3a" if r["main"] else "transparent"
            tf_color = _CYAN if r["main"] else _FG

            # Regime color
            reg = r["regime"]
            if "TREND" in reg:
                rc = _GREEN
            elif "RANGING" in reg or "RANGE" in reg:
                rc = _RED
            else:
                rc = _YELLOW

            ctk.CTkLabel(row, text=r["tf"], width=45, font=ctk.CTkFont(size=11, weight="bold" if r["main"] else "normal"),
                         text_color=tf_color).pack(side="left")
            ctk.CTkLabel(row, text=f"{r['er']:.3f}", width=50, font=ctk.CTkFont(size=11),
                         text_color=_GREEN if r["er"] > 0.3 else (_RED if r["er"] < 0.2 else _YELLOW)).pack(side="left")
            ctk.CTkLabel(row, text=f"{r['hurst']:.3f}", width=50, font=ctk.CTkFont(size=11),
                         text_color=_GREEN if r["hurst"] > 0.55 else (_RED if r["hurst"] < 0.45 else _YELLOW)).pack(side="left")
            ctk.CTkLabel(row, text=f"{r['adx']:.1f}", width=45, font=ctk.CTkFont(size=11),
                         text_color=_GREEN if r["adx"] > 25 else (_RED if r["adx"] < 18 else _YELLOW)).pack(side="left")
            ctk.CTkLabel(row, text=reg, width=90, font=ctk.CTkFont(size=11, weight="bold"),
                         text_color=rc).pack(side="left")

        # Indicators
        for w in self._ind_frame.winfo_children():
            w.destroy()

        for key, val in d["indicators"].items():
            row = ctk.CTkFrame(self._ind_frame)
            row.pack(fill="x", pady=1)
            ctk.CTkLabel(row, text=key, width=90, anchor="w",
                         font=ctk.CTkFont(size=11), text_color="#90CAF9").pack(side="left", padx=4)
            # Color based on key
            vc = _FG
            if key == "RSI(14)":
                v = float(val)
                vc = _RED if v > 70 else (_GREEN if v < 30 else _YELLOW)
            elif key == "ADX(14)":
                v = float(val)
                vc = _GREEN if v > 25 else (_RED if v < 18 else _YELLOW)
            elif key == "MACD Hist":
                try:
                    v = float(val)
                    vc = _GREEN if v > 0 else _RED
                except ValueError:
                    pass
            elif key == "ER":
                v = float(val)
                vc = _GREEN if v > 0.3 else (_RED if v < 0.2 else _YELLOW)
            elif key == "Hurst":
                v = float(val)
                vc = _GREEN if v > 0.55 else (_RED if v < 0.45 else _YELLOW)

            ctk.CTkLabel(row, text=val, width=120, anchor="e",
                         font=ctk.CTkFont(size=11, weight="bold"), text_color=vc).pack(side="right", padx=4)

    # ─── Chart ───────────────────────────────────────────────────────
    def _draw_chart(self, d):
        # Cleanup old
        if self._toolbar:
            self._toolbar.destroy()
            self._toolbar = None
        if self._canvas:
            self._canvas.get_tk_widget().destroy()
            self._canvas = None
        if self._fig:
            import matplotlib.pyplot as plt
            plt.close(self._fig)

        closes = d["closes"]
        highs = d["highs"]
        lows = d["lows"]
        n = len(closes)
        x = np.arange(n)

        fig = Figure(figsize=(10, 6), dpi=90, facecolor=_BG)
        self._fig = fig

        # Price + BB + EMA (top 70%)
        ax1 = fig.add_axes([0.06, 0.38, 0.92, 0.58])
        ax1.set_facecolor(_BG)

        # Candlestick-like: bar chart with up/down colors
        for i in range(n):
            o, c, h, l = d["opens"][i], closes[i], highs[i], lows[i]
            color = "#26A69A" if c >= o else "#EF5350"
            ax1.plot([i, i], [l, h], color=color, linewidth=0.5)
            ax1.plot([i, i], [o, c], color=color, linewidth=1.8 if n < 100 else 1.0)

        # BB bands
        bb_up = d["bb_up"]
        bb_lo = d["bb_lo"]
        bb_mid = d["bb_mid"]
        valid = ~np.isnan(bb_up)
        if np.any(valid):
            ax1.fill_between(x[valid], bb_lo[valid], bb_up[valid], alpha=0.08, color="#42A5F5")
            ax1.plot(x[valid], bb_mid[valid], color="#42A5F5", linewidth=0.7, alpha=0.6)

        # EMAs
        ax1.plot(x, d["ema9"], color="#FF9800", linewidth=0.8, alpha=0.8, label="EMA9")
        ax1.plot(x, d["ema21"], color="#E91E63", linewidth=0.8, alpha=0.8, label="EMA21")
        if len(d["ema50"]) == n:
            ax1.plot(x, d["ema50"], color="#9C27B0", linewidth=0.8, alpha=0.6, label="EMA50")

        ax1.set_title(f"{d['symbol']}  {d['tf']}  {n} mum — {d['regime_main']}",
                      color=_FG, fontsize=12, fontweight="bold")
        ax1.tick_params(colors=_FG, labelsize=8)
        ax1.legend(fontsize=7, loc="upper left", facecolor=_BG, edgecolor="#333",
                   labelcolor=_FG)
        ax1.grid(True, alpha=0.15, color="#555")
        ax1.set_xlim(0, n - 1)

        # Volume (bottom 25%)
        ax2 = fig.add_axes([0.06, 0.08, 0.92, 0.25], sharex=ax1)
        ax2.set_facecolor(_BG)
        colors = ["#26A69A" if closes[i] >= d["opens"][i] else "#EF5350" for i in range(n)]
        ax2.bar(x, d["volumes"], color=colors, alpha=0.6, width=0.8)
        ax2.tick_params(colors=_FG, labelsize=7)
        ax2.set_ylabel("Volume", color=_FG, fontsize=8)
        ax2.grid(True, alpha=0.1, color="#555")

        # Embed
        self._canvas = FigureCanvasTkAgg(fig, master=self._chart_frame)
        self._canvas.draw()
        self._canvas.get_tk_widget().pack(fill="both", expand=True)

        self._toolbar = NavigationToolbar2Tk(self._canvas, self._chart_frame)
        self._toolbar.update()
        self._toolbar.pack(side="bottom", fill="x")
