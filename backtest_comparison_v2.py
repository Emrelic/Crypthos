"""System N Gercekci Backtest — SL + Trailing + Ek Filtre Karsilastirma.

Canli sistemin kullandigi mekanizmalari simule eder:
  - G-bazli / ATR-bazli Stop Loss
  - Trailing Stop (aktivasyon + trail mesafesi)
  - Sinyal bazli cikis (reverse/close)
  - Zaman limiti (opsiyonel)

8 filtre paketi ile 20 coinde 1 aylik karsilastirma.

Kullanim:
    python backtest_comparison_v2.py              # Top 20 coin, 1 ay
    python backtest_comparison_v2.py --fast       # 15 gun
    python backtest_comparison_v2.py --symbols BTCUSDT ETHUSDT
"""

import sys
import os
import json
import time
import argparse
from pathlib import Path
from dataclasses import dataclass, field
from datetime import datetime

import numpy as np
import pandas as pd

# Windows encoding fix
import io
if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

# Project imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from market.binance_rest import BinanceRestClient
from scanner.system_n_scanner import (
    compute_alpha_trend, _compute_adx, _compute_rsi, _compute_mfi,
    _compute_macd, _compute_efficiency_ratio, _compute_obv_above_sma,
    _sma, _compute_ema,
)
from scanner.system_b_scanner import (
    detect_zigzag_swings, analyze_waves,
    compute_rolling_er, compute_hurst_exponent,
)
from loguru import logger

# ═══════════════════════════════════════════════════════════════════
#  Config
# ═══════════════════════════════════════════════════════════════════

TF = "5m"
TF_MS = 300_000
TARGET_BARS = 8640        # ~30 gun
WARMUP_BARS = 100
MIN_TRADES = 3
FEE_PER_TRADE = 0.12     # round-trip %

# SL & Trailing (canli sistem ile ayni)
SL_ATR_MULT = 2.0        # 2×ATR SL (server_sl_atr_mult)
SL_G_MULT = 1.5          # G × 1.5 SL (g_based mode)
TRAILING_ACT_ATR = 4.0   # 4×ATR trailing aktivasyon
TRAILING_DIST_ATR = 1.0  # 1×ATR trailing mesafesi
EMERGENCY_LIQ_PCT = 80.0 # %80 ROI kaybi → emergency cikis
MAX_HOLD_BARS = 96        # 96 × 5m = 8 saat (opsiyonel, 0=devre disi)

# AlphaTrend varsayilan
DEFAULT_COEFF = 3.6
DEFAULT_PERIOD = 27
ADX_LENGTH = 14
ADX_THRESHOLD = 18.0

# G dalga analizi
ZIGZAG_N = 5

DEFAULT_SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT",
    "DOGEUSDT", "ADAUSDT", "AVAXUSDT", "DOTUSDT", "MATICUSDT",
    "LINKUSDT", "NEARUSDT", "LTCUSDT", "BCHUSDT", "APTUSDT",
    "FILUSDT", "ARBUSDT", "OPUSDT", "SUIUSDT", "SEIUSDT",
]


# ═══════════════════════════════════════════════════════════════════
#  Data Classes
# ═══════════════════════════════════════════════════════════════════

@dataclass
class TradeResult:
    entry_idx: int
    exit_idx: int
    direction: str          # "LONG" or "SHORT"
    entry_price: float
    exit_price: float
    pnl_pct: float
    hold_bars: int
    exit_reason: str        # "SIGNAL", "STOP_LOSS", "TRAILING_STOP", "TIME_LIMIT", "EMERGENCY"
    symbol: str = ""
    # Entry anindaki degerler
    macd_hist: float = 0.0
    rsi_val: float = 50.0
    er_val: float = 0.5
    adx_val: float = 20.0
    regime: str = ""
    sl_pct: float = 0.0    # Uygulanan SL yuzde


@dataclass
class FilterConfig:
    name: str
    macd_align: bool = False
    rsi_align: bool = False
    rsi_long_min: float = 40.0
    rsi_short_max: float = 60.0
    er_filter: bool = False
    er_min: float = 0.2
    ranging_reject: bool = False


FILTER_PACKAGES = [
    FilterConfig("ORIJINAL (filtresiz)",
                 macd_align=False, rsi_align=False, er_filter=False, ranging_reject=False),
    FilterConfig("ASAMA 1: ER+RANGING",
                 er_filter=True, er_min=0.2, ranging_reject=True),
    FilterConfig("PAKET A: MACD+RSI+ER+RNG",
                 macd_align=True, rsi_align=True, er_filter=True, er_min=0.2, ranging_reject=True),
    FilterConfig("PAKET B: MACD+ER+RNG",
                 macd_align=True, er_filter=True, er_min=0.2, ranging_reject=True),
    FilterConfig("PAKET C: RSI+ER+RNG",
                 rsi_align=True, er_filter=True, er_min=0.2, ranging_reject=True),
    FilterConfig("PAKET D: MACD+RSI",
                 macd_align=True, rsi_align=True),
    FilterConfig("PAKET E: Tam+siki ER>0.3",
                 macd_align=True, rsi_align=True, er_filter=True, er_min=0.3, ranging_reject=True),
    FilterConfig("PAKET F: Sadece ER>0.2",
                 er_filter=True, er_min=0.2),
]


# ═══════════════════════════════════════════════════════════════════
#  Data Fetching
# ═══════════════════════════════════════════════════════════════════

def fetch_klines_paginated(client: BinanceRestClient, symbol: str,
                           tf: str = "5m", target_bars: int = TARGET_BARS) -> pd.DataFrame | None:
    try:
        df = client.get_klines(symbol, tf, min(target_bars, 1500))
        if df is None or len(df) < WARMUP_BARS:
            return None
        all_dfs = [df]
        fetched = len(df)
        while fetched < target_bars:
            earliest_ts = int(df["timestamp"].iloc[0].timestamp() * 1000)
            end_time = earliest_ts - 1
            batch_size = min(1500, target_bars - fetched)
            try:
                raw = client._get("/fapi/v1/klines", {
                    "symbol": symbol, "interval": tf,
                    "limit": batch_size, "endTime": end_time,
                })
            except Exception:
                break
            if not raw:
                break
            df2 = pd.DataFrame(raw, columns=[
                "timestamp", "open", "high", "low", "close", "volume",
                "close_time", "quote_volume", "trades", "taker_buy_volume",
                "taker_buy_quote_volume", "ignore",
            ])
            for col in ["open", "high", "low", "close", "volume", "taker_buy_volume"]:
                df2[col] = df2[col].astype(float)
            df2["timestamp"] = pd.to_datetime(df2["timestamp"], unit="ms")
            if len(df2) == 0:
                break
            all_dfs.insert(0, df2)
            fetched += len(df2)
            df = df2
            time.sleep(0.1)
        combined = pd.concat(all_dfs, ignore_index=True)
        combined = combined.drop_duplicates(subset=["timestamp"]).sort_values("timestamp").reset_index(drop=True)
        return combined
    except Exception as e:
        logger.warning(f"[BT] {symbol}: kline fetch failed: {e}")
        return None


# ═══════════════════════════════════════════════════════════════════
#  Rejim Analizi
# ═══════════════════════════════════════════════════════════════════

def detect_regime(closes: np.ndarray, highs: np.ndarray, lows: np.ndarray) -> str:
    if len(closes) < 100:
        return "UNKNOWN"
    try:
        swings = detect_zigzag_swings(highs, lows, n=ZIGZAG_N)
        if len(swings) < 4:
            return "UNKNOWN"
        er = compute_rolling_er(closes, window=20, median_count=10)
        hurst = compute_hurst_exponent(closes)
        if er > 0.25:
            return "TRENDING"
        elif er < 0.08:
            return "RANGING"
        elif hurst > 0.55:
            return "TRENDING"
        elif hurst < 0.45:
            return "RANGING"
        return "GRAY"
    except Exception:
        return "UNKNOWN"


def detect_regime_rolling(closes: np.ndarray, highs: np.ndarray,
                          lows: np.ndarray, idx: int, window: int = 300) -> str:
    """Bar bazli rolling rejim tespiti (daha gercekci)."""
    start = max(0, idx - window)
    if idx - start < 100:
        return "UNKNOWN"
    return detect_regime(closes[start:idx], highs[start:idx], lows[start:idx])


# ═══════════════════════════════════════════════════════════════════
#  GERCEKCI Backtest Engine (SL + Trailing + Filtreler)
# ═══════════════════════════════════════════════════════════════════

def run_realistic_backtest(
    closes: np.ndarray, highs: np.ndarray, lows: np.ndarray,
    volumes: np.ndarray, timestamps: np.ndarray,
    coeff: float, period: int,
    filter_cfg: FilterConfig,
    symbol: str = "",
    g_pct: float = 0.0,    # G dalga yuzde (SL hesabi icin)
) -> list[TradeResult]:
    """SL + trailing + sinyal cikis simule eden gercekci backtest.

    Cikis onceligi:
      1. Emergency SL (likidasyon koruma — ROI > -%80)
      2. Stop Loss (G-based veya ATR-based)
      3. Trailing Stop (aktivasyon → trail)
      4. Sinyal reverse/close
      5. Zaman limiti
    """
    n = len(closes)
    if n < max(period * 3, ADX_LENGTH * 3, WARMUP_BARS):
        return []

    # ── Indikatörler ──
    alpha_trend, atr_arr = compute_alpha_trend(
        highs, lows, closes, volumes,
        coeff=coeff, period=period, use_mfi=True,
    )
    adx_arr, _, _ = _compute_adx(highs, lows, closes, ADX_LENGTH)
    adx_sma = _sma(adx_arr, ADX_LENGTH)
    rsi_arr = _compute_rsi(closes, 14)

    # MACD (bar bazli array)
    ema_fast = _compute_ema(closes, 12)
    ema_slow = _compute_ema(closes, 26)
    macd_line = ema_fast - ema_slow
    valid_macd = macd_line.copy()
    valid_macd[np.isnan(valid_macd)] = 0.0
    macd_sig = _compute_ema(valid_macd, 9)
    macd_hist_arr = valid_macd - macd_sig
    macd_hist_arr[np.isnan(macd_hist_arr)] = 0.0

    # ATR clean (NaN → 0)
    atr_clean = atr_arr.copy()
    atr_clean[np.isnan(atr_clean)] = 0.0

    er_period = 10

    trades = []
    position = None         # None, "LONG", "SHORT"
    entry_idx = 0
    entry_price = 0.0
    sl_price = 0.0          # Stop loss fiyat
    sl_pct_used = 0.0       # SL yuzde (kayit icin)
    trailing_active = False
    trailing_peak = 0.0     # Trailing icin en iyi fiyat
    trail_stop = 0.0        # Trailing stop fiyat
    entry_macd = 0.0
    entry_rsi = 50.0
    entry_er = 0.5
    entry_adx = 20.0
    entry_regime = ""

    start = max(period * 3, WARMUP_BARS, 4, 36)

    def close_trade(exit_idx: int, exit_price: float, reason: str):
        """Mevcut pozisyonu kapat ve trade listesine ekle."""
        nonlocal position
        if position == "LONG":
            pnl = (exit_price - entry_price) / entry_price * 100 - FEE_PER_TRADE
        else:
            pnl = (entry_price - exit_price) / entry_price * 100 - FEE_PER_TRADE

        trades.append(TradeResult(
            entry_idx=entry_idx, exit_idx=exit_idx,
            direction=position, entry_price=entry_price,
            exit_price=exit_price, pnl_pct=pnl,
            hold_bars=exit_idx - entry_idx,
            exit_reason=reason, symbol=symbol,
            macd_hist=entry_macd, rsi_val=entry_rsi,
            er_val=entry_er, adx_val=entry_adx,
            regime=entry_regime, sl_pct=sl_pct_used,
        ))
        position = None

    def open_trade(idx: int, direction: str, atr: float):
        """Yeni pozisyon ac ve SL/trailing hesapla."""
        nonlocal position, entry_idx, entry_price, sl_price, sl_pct_used
        nonlocal trailing_active, trailing_peak, trail_stop
        nonlocal entry_macd, entry_rsi, entry_er, entry_adx, entry_regime

        position = direction
        entry_idx = idx
        entry_price = closes[idx]

        # SL hesaplama — G-based (tercih) veya ATR-based (fallback)
        if g_pct > 0:
            sl_pct_used = g_pct * SL_G_MULT + FEE_PER_TRADE / 100 * 100
        elif atr > 0:
            sl_pct_used = (atr * SL_ATR_MULT / entry_price) * 100
        else:
            sl_pct_used = 5.0  # fallback sabit

        if direction == "LONG":
            sl_price = entry_price * (1 - sl_pct_used / 100)
        else:
            sl_price = entry_price * (1 + sl_pct_used / 100)

        # Trailing reset
        trailing_active = False
        trailing_peak = entry_price
        trail_stop = 0.0

        # Entry indikatör kayit
        entry_macd = macd_hist_arr[idx]
        entry_rsi = rsi_arr[idx] if not np.isnan(rsi_arr[idx]) else 50.0
        if idx >= er_period + 1:
            direction_val = abs(closes[idx] - closes[idx - er_period])
            vol = np.sum(np.abs(np.diff(closes[idx - er_period:idx + 1])))
            entry_er = direction_val / vol if vol > 1e-12 else 0.5
        else:
            entry_er = 0.5
        entry_adx = adx_arr[idx] if not np.isnan(adx_arr[idx]) else 20.0
        # Rolling rejim
        re_start = max(0, idx - 300)
        if idx - re_start >= 100:
            entry_regime = detect_regime(closes[re_start:idx], highs[re_start:idx], lows[re_start:idx])
        else:
            entry_regime = "UNKNOWN"

    for i in range(start, n):
        atr_val = atr_clean[i]

        # ═══ POZISYON ACIKSA: SL / Trailing / Time limit kontrol ═══
        if position is not None:
            h = highs[i]
            l = lows[i]
            c = closes[i]
            bars_held = i - entry_idx

            # 1. Emergency SL (ROI > -%80, likidasyon koruma)
            if position == "LONG":
                roi = (l - entry_price) / entry_price * 100
            else:
                roi = (entry_price - h) / entry_price * 100
            # Basit emergency: ROI < -50% (margin call benzeri)
            if roi < -50:
                exit_p = l if position == "LONG" else h
                close_trade(i, exit_p, "EMERGENCY")
                continue

            # 2. Stop Loss
            if position == "LONG" and l <= sl_price:
                close_trade(i, sl_price, "STOP_LOSS")
                continue
            elif position == "SHORT" and h >= sl_price:
                close_trade(i, sl_price, "STOP_LOSS")
                continue

            # 3. Trailing Stop
            trail_act_dist = atr_val * TRAILING_ACT_ATR if atr_val > 0 else entry_price * 0.02
            trail_dist = atr_val * TRAILING_DIST_ATR if atr_val > 0 else entry_price * 0.005

            if position == "LONG":
                # Trailing aktivasyon: fiyat entry + 4×ATR'yi gecti mi?
                if h >= entry_price + trail_act_dist:
                    trailing_active = True
                if trailing_active:
                    if h > trailing_peak:
                        trailing_peak = h
                        trail_stop = trailing_peak - trail_dist
                    if l <= trail_stop and trail_stop > entry_price:
                        close_trade(i, trail_stop, "TRAILING_STOP")
                        continue
            else:  # SHORT
                if l <= entry_price - trail_act_dist:
                    trailing_active = True
                if trailing_active:
                    if l < trailing_peak:
                        trailing_peak = l
                        trail_stop = trailing_peak + trail_dist
                    if h >= trail_stop and trail_stop < entry_price:
                        close_trade(i, trail_stop, "TRAILING_STOP")
                        continue

            # 4. Zaman limiti
            if MAX_HOLD_BARS > 0 and bars_held >= MAX_HOLD_BARS:
                close_trade(i, c, "TIME_LIMIT")
                continue

        # ═══ SINYAL KONTROLU ═══
        at_now = alpha_trend[i]
        at_1 = alpha_trend[i - 1]
        at_2 = alpha_trend[i - 2]
        at_3 = alpha_trend[i - 3]

        if any(np.isnan(v) for v in [at_now, at_1, at_2, at_3]):
            continue

        # ADX filtreler
        adx_val = adx_arr[i] if not np.isnan(adx_arr[i]) else 0.0
        adx_static_ok = adx_val > ADX_THRESHOLD
        adx_dyn_val = adx_sma[i] if not np.isnan(adx_sma[i]) else 0.0
        adx_dynamic_ok = adx_val > adx_dyn_val
        base_filter = adx_static_ok and adx_dynamic_ok

        # Crossover
        buy_cross = (at_now > at_2) and (at_1 <= at_3)
        sell_cross = (at_now < at_2) and (at_1 >= at_3)

        if not (buy_cross or sell_cross):
            continue

        # ── raw sinyal (base ADX filtreli, ek filtresiz) ──
        # Cikis/reverse icin raw sinyal kullanilir (canli sistem ile ayni)
        raw_buy = buy_cross and base_filter
        raw_sell = sell_cross and base_filter

        # Acik pozisyon varsa: raw sinyal ile cikis/reverse
        if position is not None:
            if position == "LONG" and raw_sell:
                close_trade(i, closes[i], "SIGNAL_REVERSE")
            elif position == "SHORT" and raw_buy:
                close_trade(i, closes[i], "SIGNAL_REVERSE")
            elif position == "LONG" and raw_buy:
                continue  # ayni yonde sinyal → atla
            elif position == "SHORT" and raw_sell:
                continue

        if not base_filter:
            continue

        # ── Ek filtreler (sadece yeni giris icin) ──
        extra_ok = True

        macd_h = macd_hist_arr[i]
        if filter_cfg.macd_align:
            if buy_cross:
                extra_ok = extra_ok and (macd_h > 0)
            elif sell_cross:
                extra_ok = extra_ok and (macd_h < 0)

        rsi_val = rsi_arr[i] if not np.isnan(rsi_arr[i]) else 50.0
        if filter_cfg.rsi_align:
            if buy_cross:
                extra_ok = extra_ok and (rsi_val > filter_cfg.rsi_long_min)
            elif sell_cross:
                extra_ok = extra_ok and (rsi_val < filter_cfg.rsi_short_max)

        er_val = 0.5
        if i >= er_period + 1:
            d = abs(closes[i] - closes[i - er_period])
            v = np.sum(np.abs(np.diff(closes[i - er_period:i + 1])))
            if v > 1e-12:
                er_val = d / v
        if filter_cfg.er_filter:
            extra_ok = extra_ok and (er_val > filter_cfg.er_min)

        # RANGING reject — rolling rejim (trade aninda)
        if filter_cfg.ranging_reject:
            re_start = max(0, i - 300)
            if i - re_start >= 100:
                cur_regime = detect_regime(closes[re_start:i], highs[re_start:i], lows[re_start:i])
            else:
                cur_regime = "UNKNOWN"
            extra_ok = extra_ok and (cur_regime != "RANGING")

        if not extra_ok:
            continue

        # ── Pozisyon ac ──
        if position is None:
            if buy_cross:
                open_trade(i, "LONG", atr_val)
            elif sell_cross:
                open_trade(i, "SHORT", atr_val)

    return trades


# ═══════════════════════════════════════════════════════════════════
#  Statistics
# ═══════════════════════════════════════════════════════════════════

@dataclass
class PackageStats:
    name: str
    total_trades: int = 0
    wins: int = 0
    losses: int = 0
    win_rate: float = 0.0
    net_pnl_pct: float = 0.0
    gross_profit: float = 0.0
    gross_loss: float = 0.0
    profit_factor: float = 0.0
    max_drawdown_pct: float = 0.0
    avg_pnl_pct: float = 0.0
    avg_hold_bars: float = 0.0
    # Cikis tipi bazli
    sl_count: int = 0
    sl_pnl: float = 0.0
    trailing_count: int = 0
    trailing_pnl: float = 0.0
    signal_count: int = 0
    signal_pnl: float = 0.0
    time_count: int = 0
    time_pnl: float = 0.0
    emergency_count: int = 0
    # Yon bazli
    long_trades: int = 0
    long_wins: int = 0
    long_pnl: float = 0.0
    short_trades: int = 0
    short_wins: int = 0
    short_pnl: float = 0.0
    # Coin bazli
    coin_details: dict = field(default_factory=dict)
    all_trades: list = field(default_factory=list, repr=False)


def calc_stats(trades: list[TradeResult], name: str) -> PackageStats:
    s = PackageStats(name=name)
    if not trades:
        return s

    s.total_trades = len(trades)
    s.all_trades = trades

    pnls = [t.pnl_pct for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]

    s.wins = len(wins)
    s.losses = len(losses)
    s.win_rate = s.wins / s.total_trades * 100 if s.total_trades > 0 else 0
    s.net_pnl_pct = sum(pnls)
    s.gross_profit = sum(wins) if wins else 0
    s.gross_loss = abs(sum(losses)) if losses else 0.001
    s.profit_factor = s.gross_profit / s.gross_loss if s.gross_loss > 0 else 999
    s.avg_pnl_pct = float(np.mean(pnls))
    s.avg_hold_bars = float(np.mean([t.hold_bars for t in trades]))

    # Max drawdown
    cum_pnl = np.cumsum(pnls)
    peak = np.maximum.accumulate(cum_pnl)
    dd = peak - cum_pnl
    s.max_drawdown_pct = float(np.max(dd)) if len(dd) > 0 else 0

    # Cikis tipi bazli
    for t in trades:
        if t.exit_reason == "STOP_LOSS":
            s.sl_count += 1
            s.sl_pnl += t.pnl_pct
        elif t.exit_reason == "TRAILING_STOP":
            s.trailing_count += 1
            s.trailing_pnl += t.pnl_pct
        elif t.exit_reason in ("SIGNAL_REVERSE", "SIGNAL"):
            s.signal_count += 1
            s.signal_pnl += t.pnl_pct
        elif t.exit_reason == "TIME_LIMIT":
            s.time_count += 1
            s.time_pnl += t.pnl_pct
        elif t.exit_reason == "EMERGENCY":
            s.emergency_count += 1

    # Yon bazli
    longs = [t for t in trades if t.direction == "LONG"]
    shorts = [t for t in trades if t.direction == "SHORT"]
    s.long_trades = len(longs)
    s.long_wins = sum(1 for t in longs if t.pnl_pct > 0)
    s.long_pnl = sum(t.pnl_pct for t in longs)
    s.short_trades = len(shorts)
    s.short_wins = sum(1 for t in shorts if t.pnl_pct > 0)
    s.short_pnl = sum(t.pnl_pct for t in shorts)

    # Coin bazli
    for t in trades:
        sym = t.symbol
        if sym not in s.coin_details:
            s.coin_details[sym] = {"trades": 0, "wins": 0, "pnl": 0.0, "sl": 0, "trailing": 0}
        s.coin_details[sym]["trades"] += 1
        if t.pnl_pct > 0:
            s.coin_details[sym]["wins"] += 1
        s.coin_details[sym]["pnl"] += t.pnl_pct
        if t.exit_reason == "STOP_LOSS":
            s.coin_details[sym]["sl"] += 1
        elif t.exit_reason == "TRAILING_STOP":
            s.coin_details[sym]["trailing"] += 1

    return s


# ═══════════════════════════════════════════════════════════════════
#  Optimize Cache
# ═══════════════════════════════════════════════════════════════════

def load_optimize_cache() -> dict:
    path = "data/system_n_optimize.json"
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        cache = {}
        for symbol, info in data.get("results", {}).items():
            opt_tf = info.get("optimal_tf", "5m")
            params = info.get("params", {}).get(opt_tf, {})
            g_data = info.get("g_analysis", {}).get(opt_tf, {})
            coeff = params.get("coeff", 0)
            period = params.get("period", 0)
            G = g_data.get("G", info.get("G", 0))
            if coeff > 0 and period > 0:
                cache[symbol] = {"coeff": coeff, "period": period, "G": G}
        return cache
    except Exception:
        return {}


# ═══════════════════════════════════════════════════════════════════
#  Main
# ═══════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="System N Gercekci Backtest v2")
    parser.add_argument("--symbols", nargs="+", default=None)
    parser.add_argument("--top", type=int, default=20)
    parser.add_argument("--fast", action="store_true")
    args = parser.parse_args()

    target_bars = 4320 if args.fast else TARGET_BARS

    client = BinanceRestClient()
    if args.symbols:
        symbols = args.symbols
    else:
        try:
            ticker_24h = client._get("/fapi/v1/ticker/24hr")
            sorted_t = sorted(ticker_24h,
                              key=lambda x: float(x.get("quoteVolume", 0)),
                              reverse=True)
            symbols = [t["symbol"] for t in sorted_t
                       if t["symbol"].endswith("USDT")][:args.top]
        except Exception:
            symbols = DEFAULT_SYMBOLS[:args.top]

    opt_cache = load_optimize_cache()

    print("\n" + "=" * 110)
    print("  SYSTEM N GERCEKCI BACKTEST v2 — SL + Trailing + Sinyal Cikis")
    print(f"  Tarih: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"  Coinler: {len(symbols)} | TF: {TF} | ~{target_bars * 5 / 1440:.0f} gun")
    print(f"  SL: {SL_ATR_MULT}xATR (G-based tercih) | Trail: {TRAILING_ACT_ATR}xATR akt, "
          f"{TRAILING_DIST_ATR}xATR mesafe | Max hold: {MAX_HOLD_BARS} bar ({MAX_HOLD_BARS*5/60:.0f}h)")
    print("=" * 110)

    all_package_trades: dict[str, list[TradeResult]] = {
        fc.name: [] for fc in FILTER_PACKAGES
    }

    t0 = time.time()
    for si, symbol in enumerate(symbols):
        print(f"\n  [{si+1}/{len(symbols)}] {symbol}", end="", flush=True)

        df = fetch_klines_paginated(client, symbol, TF, target_bars)
        if df is None or len(df) < WARMUP_BARS:
            print(f" — SKIP")
            continue

        closes = df["close"].values.astype(float)
        highs = df["high"].values.astype(float)
        lows = df["low"].values.astype(float)
        volumes = df["volume"].values.astype(float)
        timestamps = df["timestamp"].values

        actual_days = len(df) * 5 / 1440
        print(f" — {len(df)} mum ({actual_days:.0f} gun)", end="", flush=True)

        params = opt_cache.get(symbol, {})
        coeff = params.get("coeff", DEFAULT_COEFF)
        period = params.get("period", DEFAULT_PERIOD)
        g_pct = params.get("G", 0)

        for fc in FILTER_PACKAGES:
            trades = run_realistic_backtest(
                closes, highs, lows, volumes, timestamps,
                coeff=coeff, period=period,
                filter_cfg=fc, symbol=symbol, g_pct=g_pct,
            )
            all_package_trades[fc.name].extend(trades)

        orig_count = len([t for t in all_package_trades[FILTER_PACKAGES[0].name]
                          if t.symbol == symbol])
        orig_sl = len([t for t in all_package_trades[FILTER_PACKAGES[0].name]
                       if t.symbol == symbol and t.exit_reason == "STOP_LOSS"])
        print(f" | {orig_count} trade ({orig_sl} SL) | G={g_pct:.3f}%", flush=True)

        time.sleep(0.15)

    elapsed = time.time() - t0

    # ═══════════════════════════════════════════════════════════════
    #  SONUCLAR
    # ═══════════════════════════════════════════════════════════════

    all_stats: list[PackageStats] = []
    for fc in FILTER_PACKAGES:
        stats = calc_stats(all_package_trades[fc.name], fc.name)
        all_stats.append(stats)

    orig = all_stats[0]

    # ── 1. ANA OZET TABLO ──
    print(f"\n\n{'=' * 130}")
    print(f"  ANA KARSILASTIRMA TABLOSU")
    print(f"{'=' * 130}")

    print(f"\n  {'Paket':<30} {'Trade':>6} {'Win':>5} {'Loss':>5} {'WR%':>7} "
          f"{'NetPnL%':>9} {'PF':>6} {'MaxDD%':>7} {'AvgPnL':>8} "
          f"{'SL':>4} {'Trail':>5} {'Signal':>6} {'Time':>5}")
    print(f"  {'-'*118}")

    for s in all_stats:
        marker = " <--" if "ASAMA 1" in s.name else ""
        print(f"  {s.name:<30} {s.total_trades:>6} {s.wins:>5} {s.losses:>5} "
              f"{s.win_rate:>6.1f}% {s.net_pnl_pct:>+8.2f}% {s.profit_factor:>5.2f} "
              f"{s.max_drawdown_pct:>6.2f}% {s.avg_pnl_pct:>+7.3f}% "
              f"{s.sl_count:>4} {s.trailing_count:>5} {s.signal_count:>6} "
              f"{s.time_count:>5}{marker}")

    # ── 2. DELTA TABLOSU ──
    print(f"\n  {'Paket':<30} {'dTrade':>7} {'dWR':>7} {'dPnL%':>9} "
          f"{'dPF':>7} {'dSL':>5} {'SL Eng.%':>9}")
    print(f"  {'-'*76}")
    for s in all_stats[1:]:
        d_trade = s.total_trades - orig.total_trades
        d_wr = s.win_rate - orig.win_rate
        d_pnl = s.net_pnl_pct - orig.net_pnl_pct
        d_pf = s.profit_factor - orig.profit_factor
        d_sl = s.sl_count - orig.sl_count
        sl_block_rate = (1 - s.sl_count / orig.sl_count) * 100 if orig.sl_count > 0 else 0
        print(f"  {s.name:<30} {d_trade:>+7} {d_wr:>+6.1f}% {d_pnl:>+8.2f}% "
              f"{d_pf:>+6.2f} {d_sl:>+5} {sl_block_rate:>+8.1f}%")

    # ── 3. CIKIS TIPI ANALIZI ──
    print(f"\n\n{'=' * 110}")
    print(f"  CIKIS TIPI ANALIZI")
    print(f"{'=' * 110}")

    print(f"\n  {'Paket':<30} {'SL#':>5} {'SL PnL':>9} {'Trail#':>6} {'Trail PnL':>10} "
          f"{'Sig#':>5} {'Sig PnL':>10} {'Time#':>5} {'Time PnL':>9}")
    print(f"  {'-'*95}")
    for s in all_stats:
        print(f"  {s.name:<30} {s.sl_count:>5} {s.sl_pnl:>+8.2f}% "
              f"{s.trailing_count:>6} {s.trailing_pnl:>+9.2f}% "
              f"{s.signal_count:>5} {s.signal_pnl:>+9.2f}% "
              f"{s.time_count:>5} {s.time_pnl:>+8.2f}%")

    # ── 4. SL ENGELLEME DETAYI ──
    print(f"\n\n{'=' * 110}")
    print(f"  SL ENGELLEME DETAYI (Orijinal vs Asama 1)")
    print(f"{'=' * 110}")

    orig_trade_keys = {(t.symbol, t.entry_idx): t for t in all_stats[0].all_trades}
    a1_trade_keys = {(t.symbol, t.entry_idx): t for t in all_stats[1].all_trades}

    blocked_keys = set(orig_trade_keys.keys()) - set(a1_trade_keys.keys())
    blocked_trades = [orig_trade_keys[k] for k in blocked_keys]

    if blocked_trades:
        bl_sl = [t for t in blocked_trades if t.exit_reason == "STOP_LOSS"]
        bl_trail = [t for t in blocked_trades if t.exit_reason == "TRAILING_STOP"]
        bl_sig = [t for t in blocked_trades if "SIGNAL" in t.exit_reason]
        bl_time = [t for t in blocked_trades if t.exit_reason == "TIME_LIMIT"]

        bl_wins = [t for t in blocked_trades if t.pnl_pct > 0]
        bl_losses = [t for t in blocked_trades if t.pnl_pct <= 0]

        print(f"\n  Toplam engellenen: {len(blocked_trades)} trade")
        print(f"    - SL ile bitecek olan: {len(bl_sl)} (PnL: {sum(t.pnl_pct for t in bl_sl):+.2f}%)")
        print(f"    - Trailing ile bitecek: {len(bl_trail)} (PnL: {sum(t.pnl_pct for t in bl_trail):+.2f}%)")
        print(f"    - Sinyal ile bitecek: {len(bl_sig)} (PnL: {sum(t.pnl_pct for t in bl_sig):+.2f}%)")
        print(f"    - Time limit: {len(bl_time)} (PnL: {sum(t.pnl_pct for t in bl_time):+.2f}%)")
        print(f"\n    Win engellenen: {len(bl_wins)} (toplam: {sum(t.pnl_pct for t in bl_wins):+.2f}%)")
        print(f"    Loss engellenen: {len(bl_losses)} (toplam: {sum(t.pnl_pct for t in bl_losses):+.2f}%)")
        eng_zarar = abs(sum(t.pnl_pct for t in bl_losses))
        kayb_kar = sum(t.pnl_pct for t in bl_wins)
        print(f"    NET FAYDA: {eng_zarar - kayb_kar:+.2f}%")

        # SL engelleme orani
        if bl_sl:
            print(f"\n    SL ENGELLEME ORANI: {len(bl_sl)}/{orig.sl_count} "
                  f"= %{len(bl_sl)/orig.sl_count*100:.1f}" if orig.sl_count > 0 else "")

        # En buyuk engellenen SL'ler
        print(f"\n  Engellenen SL'ler (buyukten kucuge):")
        print(f"    {'#':<3} {'Coin':<14} {'Yon':<6} {'PnL%':>8} {'SL%':>6} {'RSI':>5} {'ER':>6} {'MACD_H':>10} {'Rejim':<12}")
        print(f"    {'-'*75}")
        for i, t in enumerate(sorted(bl_sl, key=lambda x: x.pnl_pct)[:20]):
            print(f"    {i+1:<3} {t.symbol:<14} {t.direction:<6} "
                  f"{t.pnl_pct:>+7.2f}% {t.sl_pct:>5.2f}% {t.rsi_val:>4.0f} "
                  f"{t.er_val:>5.3f} {t.macd_hist:>+10.6f} {t.regime:<12}")

    # ── 5. LONG vs SHORT ──
    print(f"\n\n{'=' * 100}")
    print(f"  LONG vs SHORT ANALIZI")
    print(f"{'=' * 100}")

    print(f"\n  {'Paket':<30} {'L#':>5} {'L.WR':>6} {'L.PnL':>9} "
          f"{'S#':>5} {'S.WR':>6} {'S.PnL':>9}")
    print(f"  {'-'*73}")
    for s in all_stats:
        l_wr = s.long_wins / s.long_trades * 100 if s.long_trades > 0 else 0
        s_wr = s.short_wins / s.short_trades * 100 if s.short_trades > 0 else 0
        print(f"  {s.name:<30} {s.long_trades:>5} {l_wr:>5.1f}% {s.long_pnl:>+8.2f}% "
              f"{s.short_trades:>5} {s_wr:>5.1f}% {s.short_pnl:>+8.2f}%")

    # ── 6. COIN BAZLI ──
    print(f"\n\n{'=' * 120}")
    print(f"  COIN BAZLI ANALIZ")
    print(f"{'=' * 120}")

    all_coins = set()
    for s in all_stats:
        all_coins.update(s.coin_details.keys())

    print(f"\n  {'Coin':<14} {'O.T':>4} {'O.WR':>6} {'O.PnL':>8} {'O.SL':>4} "
          f"{'A1.T':>5} {'A1.WR':>6} {'A1.PnL':>8} {'A1.SL':>5} "
          f"{'Best.T':>6} {'Best.WR':>7} {'Best.PnL':>8} {'En Iyi':<25}")
    print(f"  {'-'*125}")

    for coin in sorted(all_coins):
        o = orig.coin_details.get(coin, {"trades": 0, "wins": 0, "pnl": 0.0, "sl": 0})
        o_wr = o["wins"] / o["trades"] * 100 if o["trades"] > 0 else 0

        a1 = all_stats[1].coin_details.get(coin, {"trades": 0, "wins": 0, "pnl": 0.0, "sl": 0})
        a1_wr = a1["wins"] / a1["trades"] * 100 if a1["trades"] > 0 else 0

        best_s = None
        best_pnl = -999
        for s in all_stats[1:]:
            d = s.coin_details.get(coin, {"trades": 0, "wins": 0, "pnl": 0.0, "sl": 0})
            if d["pnl"] > best_pnl:
                best_pnl = d["pnl"]
                best_s = s

        if best_s:
            b = best_s.coin_details.get(coin, {"trades": 0, "wins": 0, "pnl": 0.0, "sl": 0})
            b_wr = b["wins"] / b["trades"] * 100 if b["trades"] > 0 else 0
            print(f"  {coin:<14} {o['trades']:>4} {o_wr:>5.1f}% {o['pnl']:>+7.2f}% {o.get('sl',0):>4} "
                  f"{a1['trades']:>5} {a1_wr:>5.1f}% {a1['pnl']:>+7.2f}% {a1.get('sl',0):>5} "
                  f"{b['trades']:>6} {b_wr:>6.1f}% {b['pnl']:>+7.2f}% {best_s.name:<25}")

    # ── 7. REJIM BAZLI ──
    print(f"\n\n{'=' * 100}")
    print(f"  REJIM BAZLI ANALIZ")
    print(f"{'=' * 100}")

    for s in [all_stats[0], all_stats[1], all_stats[2]]:
        print(f"\n  {s.name}:")
        regime_stats = {}
        for t in s.all_trades:
            r = t.regime or "UNKNOWN"
            if r not in regime_stats:
                regime_stats[r] = {"trades": 0, "wins": 0, "pnl": 0.0, "sl": 0}
            regime_stats[r]["trades"] += 1
            if t.pnl_pct > 0:
                regime_stats[r]["wins"] += 1
            regime_stats[r]["pnl"] += t.pnl_pct
            if t.exit_reason == "STOP_LOSS":
                regime_stats[r]["sl"] += 1

        print(f"    {'Rejim':<12} {'Trade':>6} {'WR%':>7} {'PnL%':>9} {'SL':>4} {'SL%':>6}")
        print(f"    {'-'*48}")
        for r, d in sorted(regime_stats.items()):
            wr = d["wins"] / d["trades"] * 100 if d["trades"] > 0 else 0
            sl_rate = d["sl"] / d["trades"] * 100 if d["trades"] > 0 else 0
            print(f"    {r:<12} {d['trades']:>6} {wr:>6.1f}% {d['pnl']:>+8.2f}% "
                  f"{d['sl']:>4} {sl_rate:>5.1f}%")

    # ═══ SONUC ═══
    print(f"\n\n{'=' * 110}")
    print(f"  SONUC VE ONERILER")
    print(f"{'=' * 110}")

    best_pkg = max(all_stats[1:],
                   key=lambda s: (s.net_pnl_pct * s.profit_factor) if s.total_trades >= 5 else -999)

    print(f"\n  ORIJINAL (3 gun onceki):   Trade={orig.total_trades} | WR={orig.win_rate:.1f}% | "
          f"PnL={orig.net_pnl_pct:+.2f}% | PF={orig.profit_factor:.2f} | "
          f"SL={orig.sl_count} ({orig.sl_pnl:+.2f}%)")

    a1 = all_stats[1]
    print(f"  ASAMA 1 (aktif config):    Trade={a1.total_trades} | WR={a1.win_rate:.1f}% | "
          f"PnL={a1.net_pnl_pct:+.2f}% | PF={a1.profit_factor:.2f} | "
          f"SL={a1.sl_count} ({a1.sl_pnl:+.2f}%)")

    print(f"  EN IYI PAKET:              {best_pkg.name}")
    print(f"                             Trade={best_pkg.total_trades} | WR={best_pkg.win_rate:.1f}% | "
          f"PnL={best_pkg.net_pnl_pct:+.2f}% | PF={best_pkg.profit_factor:.2f} | "
          f"SL={best_pkg.sl_count} ({best_pkg.sl_pnl:+.2f}%)")

    print(f"\n  ASAMA 1 vs ORIJINAL:")
    print(f"    WR:    {orig.win_rate:.1f}% → {a1.win_rate:.1f}% ({a1.win_rate - orig.win_rate:+.1f}%)")
    print(f"    PnL:   {orig.net_pnl_pct:+.2f}% → {a1.net_pnl_pct:+.2f}% ({a1.net_pnl_pct - orig.net_pnl_pct:+.2f}%)")
    print(f"    PF:    {orig.profit_factor:.2f} → {a1.profit_factor:.2f} ({a1.profit_factor - orig.profit_factor:+.2f})")
    print(f"    SL:    {orig.sl_count} → {a1.sl_count} ({a1.sl_count - orig.sl_count:+d})")
    if orig.sl_count > 0:
        print(f"    SL engelleme: %{(1 - a1.sl_count / orig.sl_count) * 100:.1f}")

    print(f"\n  Sure: {elapsed:.1f}s")

    # JSON kaydet
    output = {
        "timestamp": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "config": {
            "symbols": symbols, "target_bars": target_bars,
            "sl_atr_mult": SL_ATR_MULT, "sl_g_mult": SL_G_MULT,
            "trailing_act_atr": TRAILING_ACT_ATR,
            "trailing_dist_atr": TRAILING_DIST_ATR,
            "max_hold_bars": MAX_HOLD_BARS,
        },
        "results": {}
    }
    for s in all_stats:
        output["results"][s.name] = {
            "total_trades": s.total_trades, "wins": s.wins, "losses": s.losses,
            "win_rate": round(s.win_rate, 2), "net_pnl_pct": round(s.net_pnl_pct, 4),
            "profit_factor": round(s.profit_factor, 3),
            "max_drawdown_pct": round(s.max_drawdown_pct, 3),
            "sl_count": s.sl_count, "sl_pnl": round(s.sl_pnl, 4),
            "trailing_count": s.trailing_count, "trailing_pnl": round(s.trailing_pnl, 4),
            "signal_count": s.signal_count, "signal_pnl": round(s.signal_pnl, 4),
            "time_count": s.time_count, "time_pnl": round(s.time_pnl, 4),
            "long_trades": s.long_trades, "long_pnl": round(s.long_pnl, 4),
            "short_trades": s.short_trades, "short_pnl": round(s.short_pnl, 4),
            "coin_details": s.coin_details,
        }

    out_path = Path("data/backtest_comparison_v2.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False, default=str)
    print(f"\n  Kaydedildi: {out_path}")


if __name__ == "__main__":
    main()
