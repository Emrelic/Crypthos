"""System N Kapsamli Karsilastirma Backtest — Orijinal vs Ek Filtreler.

20 coinde 1 aylik veri ile:
  1. ORIJINAL: Sadece AlphaTrend + ADX (3 gun onceki versiyon)
  2. ASAMA 1: ER + RANGING (bugunun aktif config'i)
  3. PAKET A: MACD + RSI + ER + RANGING (tam filtre)
  4. PAKET B: Sadece MACD + ER + RANGING
  5. PAKET C: Sadece RSI + ER + RANGING
  6. PAKET D: MACD + RSI (ER yok, RANGING yok)
  7. PAKET E: Tam filtre + siki ER (>0.3)
  8. PAKET F: Sadece ER (RANGING yok)

Kullanim:
    python backtest_comparison.py              # Top 20 coin
    python backtest_comparison.py --fast       # Top 20, hizli mod
    python backtest_comparison.py --symbols BTCUSDT ETHUSDT
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
    _compute_macd, _compute_efficiency_ratio, _compute_obv_above_sma, _sma,
)
from scanner.system_b_scanner import (
    detect_zigzag_swings, analyze_waves,
    compute_rolling_er, compute_hurst_exponent,
)
from loguru import logger

# ═══════════════════════════════════════════════════════════════════
#  Config
# ═══════════════════════════════════════════════════════════════════

# 1 ay = ~8640 mum (5m), sayfalama ile alacagiz
TF = "5m"
TF_MS = 300_000          # 5m in ms
TARGET_BARS = 8640        # ~30 gun
WARMUP_BARS = 100
MIN_TRADES = 3
FEE_PER_TRADE = 0.12     # round-trip: 2x%0.04 taker + 2x%0.02 slippage

# AlphaTrend varsayilan parametreleri (optimize cache yoksa)
DEFAULT_COEFF = 3.6
DEFAULT_PERIOD = 27
ADX_LENGTH = 14
ADX_THRESHOLD = 18.0

# G dalga analizi
ZIGZAG_N = 5
SL_G_MULT = 1.5
FEE_TOTAL = 0.12
SL_DIVISOR = 2.0
DEFAULT_MAINT_RATE = 0.004

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
    direction: str       # "LONG" or "SHORT"
    entry_price: float
    exit_price: float
    pnl_pct: float
    hold_bars: int
    # Ek bilgi
    symbol: str = ""
    entry_time: str = ""
    exit_time: str = ""
    # Entry anindaki indikatör degerleri
    macd_hist: float = 0.0
    rsi_val: float = 50.0
    er_val: float = 0.5
    adx_val: float = 20.0
    regime: str = ""


@dataclass
class FilterConfig:
    """Filtre paketi tanimlayici."""
    name: str
    macd_align: bool = False
    rsi_align: bool = False
    rsi_long_min: float = 40.0
    rsi_short_max: float = 60.0
    er_filter: bool = False
    er_min: float = 0.2
    ranging_reject: bool = False


# Filtre paketleri
FILTER_PACKAGES = [
    FilterConfig("ORIJINAL (3 gun once)", macd_align=False, rsi_align=False, er_filter=False, ranging_reject=False),
    FilterConfig("ASAMA 1 (ER+RANGING)", er_filter=True, er_min=0.2, ranging_reject=True),
    FilterConfig("PAKET A: MACD+RSI+ER+RNG", macd_align=True, rsi_align=True, er_filter=True, er_min=0.2, ranging_reject=True),
    FilterConfig("PAKET B: MACD+ER+RNG", macd_align=True, er_filter=True, er_min=0.2, ranging_reject=True),
    FilterConfig("PAKET C: RSI+ER+RNG", rsi_align=True, er_filter=True, er_min=0.2, ranging_reject=True),
    FilterConfig("PAKET D: MACD+RSI (ER yok)", macd_align=True, rsi_align=True),
    FilterConfig("PAKET E: Tam+siki ER>0.3", macd_align=True, rsi_align=True, er_filter=True, er_min=0.3, ranging_reject=True),
    FilterConfig("PAKET F: Sadece ER>0.2", er_filter=True, er_min=0.2),
]


# ═══════════════════════════════════════════════════════════════════
#  Data Fetching (sayfalamali)
# ═══════════════════════════════════════════════════════════════════

def fetch_klines_paginated(client: BinanceRestClient, symbol: str,
                           tf: str = "5m", target_bars: int = TARGET_BARS) -> pd.DataFrame | None:
    """Sayfalama ile buyuk veri seti cek (1 ay)."""
    try:
        # Ilk cagri
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
                    "symbol": symbol,
                    "interval": tf,
                    "limit": batch_size,
                    "endTime": end_time,
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
    """G dalga analizinden rejim belirle (TRENDING / RANGING / GRAY)."""
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


# ═══════════════════════════════════════════════════════════════════
#  Backtest Engine (ek filtreler dahil)
# ═══════════════════════════════════════════════════════════════════

def run_backtest_with_filters(
    closes: np.ndarray, highs: np.ndarray, lows: np.ndarray,
    volumes: np.ndarray, timestamps: np.ndarray,
    coeff: float, period: int,
    filter_cfg: FilterConfig,
    symbol: str = "",
    regime: str = "",
) -> list[TradeResult]:
    """AlphaTrend backtest — ek filtreler ile.

    Bu fonksiyon hem orijinal (filtresiz) hem de filtreli versiyonu test eder.
    filter_cfg parametresine gore farkli filtre kombinasyonlari uygulanir.
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

    # MACD: bar bazli hesapla (sliding window)
    # Tum closes uzerinden EMA hesapla, sonra bar bazli erisim
    from scanner.system_n_scanner import _compute_ema
    ema_fast = _compute_ema(closes, 12)
    ema_slow = _compute_ema(closes, 26)
    macd_line = ema_fast - ema_slow
    # Signal line: MACD'nin EMA'si
    valid_mask = ~np.isnan(macd_line)
    macd_signal_arr = np.full(n, np.nan)
    # Signal icin EMA hesapla (sadece valid bolumlerde)
    if np.sum(valid_mask) >= 9:
        valid_macd = macd_line.copy()
        valid_macd[~valid_mask] = 0.0  # NaN'lari sifirla
        macd_sig = _compute_ema(valid_macd, 9)
        macd_signal_arr = macd_sig
    macd_hist_arr = macd_line - macd_signal_arr

    # ER: rolling window
    er_period = 10

    trades = []
    position = None
    entry_idx = 0
    entry_price = 0.0
    entry_macd_hist = 0.0
    entry_rsi = 50.0
    entry_er = 0.5
    entry_adx = 20.0

    start = max(period * 3, WARMUP_BARS, 4, 36)  # MACD warmup icin 36

    for i in range(start, n):
        at_now = alpha_trend[i]
        at_1 = alpha_trend[i - 1]
        at_2 = alpha_trend[i - 2]
        at_3 = alpha_trend[i - 3]

        if any(np.isnan(v) for v in [at_now, at_1, at_2, at_3]):
            continue

        # ADX filtreler (orijinal — her zaman aktif)
        adx_val = adx_arr[i] if not np.isnan(adx_arr[i]) else 0.0
        adx_static_ok = adx_val > ADX_THRESHOLD
        adx_dyn_val = adx_sma[i] if not np.isnan(adx_sma[i]) else 0.0
        adx_dynamic_ok = adx_val > adx_dyn_val
        base_filter = adx_static_ok and adx_dynamic_ok

        # Crossover / crossunder
        buy_cross = (at_now > at_2) and (at_1 <= at_3)
        sell_cross = (at_now < at_2) and (at_1 >= at_3)

        if not (buy_cross or sell_cross):
            continue

        if not base_filter:
            continue

        # ── Ek filtreler (filter_cfg'ye gore) ──
        extra_ok = True

        # MACD histogram
        macd_h = macd_hist_arr[i] if not np.isnan(macd_hist_arr[i]) else 0.0
        if filter_cfg.macd_align:
            if buy_cross:
                extra_ok = extra_ok and (macd_h > 0)
            elif sell_cross:
                extra_ok = extra_ok and (macd_h < 0)

        # RSI
        rsi_val = rsi_arr[i] if not np.isnan(rsi_arr[i]) else 50.0
        if filter_cfg.rsi_align:
            if buy_cross:
                extra_ok = extra_ok and (rsi_val > filter_cfg.rsi_long_min)
            elif sell_cross:
                extra_ok = extra_ok and (rsi_val < filter_cfg.rsi_short_max)

        # ER
        er_val = 0.5
        if i >= er_period + 1:
            direction = abs(closes[i] - closes[i - er_period])
            volatility = np.sum(np.abs(np.diff(closes[i - er_period:i + 1])))
            if volatility > 1e-12:
                er_val = direction / volatility
        if filter_cfg.er_filter:
            extra_ok = extra_ok and (er_val > filter_cfg.er_min)

        # RANGING reject
        if filter_cfg.ranging_reject:
            extra_ok = extra_ok and (regime != "RANGING")

        if not extra_ok:
            continue

        # ── Trade logic ──
        ts_str = ""
        if timestamps is not None and i < len(timestamps):
            ts_str = str(timestamps[i])

        if buy_cross and position != "LONG":
            if position == "SHORT":
                pnl = (entry_price - closes[i]) / entry_price * 100 - FEE_PER_TRADE
                trades.append(TradeResult(
                    entry_idx=entry_idx, exit_idx=i,
                    direction="SHORT", entry_price=entry_price,
                    exit_price=closes[i], pnl_pct=pnl,
                    hold_bars=i - entry_idx,
                    symbol=symbol, exit_time=ts_str,
                    macd_hist=entry_macd_hist, rsi_val=entry_rsi,
                    er_val=entry_er, adx_val=entry_adx, regime=regime,
                ))
            position = "LONG"
            entry_idx = i
            entry_price = closes[i]
            entry_macd_hist = macd_h
            entry_rsi = rsi_val
            entry_er = er_val
            entry_adx = adx_val

        elif sell_cross and position != "SHORT":
            if position == "LONG":
                pnl = (closes[i] - entry_price) / entry_price * 100 - FEE_PER_TRADE
                trades.append(TradeResult(
                    entry_idx=entry_idx, exit_idx=i,
                    direction="LONG", entry_price=entry_price,
                    exit_price=closes[i], pnl_pct=pnl,
                    hold_bars=i - entry_idx,
                    symbol=symbol, exit_time=ts_str,
                    macd_hist=entry_macd_hist, rsi_val=entry_rsi,
                    er_val=entry_er, adx_val=entry_adx, regime=regime,
                ))
            position = "SHORT"
            entry_idx = i
            entry_price = closes[i]
            entry_macd_hist = macd_h
            entry_rsi = rsi_val
            entry_er = er_val
            entry_adx = adx_val

    return trades


# ═══════════════════════════════════════════════════════════════════
#  Statistics
# ═══════════════════════════════════════════════════════════════════

@dataclass
class PackageStats:
    """Bir filtre paketinin toplam istatistikleri."""
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
    # Yon bazli
    long_trades: int = 0
    long_wins: int = 0
    long_pnl: float = 0.0
    short_trades: int = 0
    short_wins: int = 0
    short_pnl: float = 0.0
    # Coin bazli detaylar
    coin_details: dict = field(default_factory=dict)
    all_trades: list = field(default_factory=list, repr=False)


def calc_stats(trades: list[TradeResult], name: str) -> PackageStats:
    """Trade listesinden istatistik cikar."""
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
    s.avg_pnl_pct = np.mean(pnls)
    s.avg_hold_bars = np.mean([t.hold_bars for t in trades])

    # Max drawdown
    cum_pnl = np.cumsum(pnls)
    peak = np.maximum.accumulate(cum_pnl)
    dd = peak - cum_pnl
    s.max_drawdown_pct = float(np.max(dd)) if len(dd) > 0 else 0

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
            s.coin_details[sym] = {"trades": 0, "wins": 0, "pnl": 0.0}
        s.coin_details[sym]["trades"] += 1
        if t.pnl_pct > 0:
            s.coin_details[sym]["wins"] += 1
        s.coin_details[sym]["pnl"] += t.pnl_pct

    return s


# ═══════════════════════════════════════════════════════════════════
#  Optimize Cache (coin bazli parametreler)
# ═══════════════════════════════════════════════════════════════════

def load_optimize_cache() -> dict:
    """data/system_n_optimize.json'dan coin parametrelerini oku."""
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
            coeff = params.get("coeff", 0)
            period = params.get("period", 0)
            if coeff > 0 and period > 0:
                cache[symbol] = {"coeff": coeff, "period": period}
        return cache
    except Exception:
        return {}


# ═══════════════════════════════════════════════════════════════════
#  Main Backtest
# ═══════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="System N Karsilastirma Backtest")
    parser.add_argument("--symbols", nargs="+", default=None)
    parser.add_argument("--top", type=int, default=20)
    parser.add_argument("--fast", action="store_true", help="Daha az mum (2 hafta)")
    args = parser.parse_args()

    target_bars = 4320 if args.fast else TARGET_BARS  # fast: ~15 gun

    # ── Coinleri belirle ──
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

    # Optimize cache
    opt_cache = load_optimize_cache()

    print("\n" + "=" * 100)
    print("  SYSTEM N KARSILASTIRMA BACKTEST")
    print(f"  Tarih: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"  Coinler: {len(symbols)} | TF: {TF} | Hedef: ~{target_bars} mum (~{target_bars * 5 / 1440:.0f} gun)")
    print("=" * 100)

    # ── Her paket icin toplam trade listesi ──
    all_package_trades: dict[str, list[TradeResult]] = {
        fc.name: [] for fc in FILTER_PACKAGES
    }

    # ── Coin bazli backtest ──
    t0 = time.time()
    for si, symbol in enumerate(symbols):
        print(f"\n  [{si+1}/{len(symbols)}] {symbol}", end="", flush=True)

        # Veri cek
        df = fetch_klines_paginated(client, symbol, TF, target_bars)
        if df is None or len(df) < WARMUP_BARS:
            print(f" — SKIP (yetersiz veri)")
            continue

        closes = df["close"].values.astype(float)
        highs = df["high"].values.astype(float)
        lows = df["low"].values.astype(float)
        volumes = df["volume"].values.astype(float)
        timestamps = df["timestamp"].values

        actual_days = len(df) * 5 / 1440
        print(f" — {len(df)} mum ({actual_days:.0f} gun)", end="", flush=True)

        # Coin parametreleri
        params = opt_cache.get(symbol, {})
        coeff = params.get("coeff", DEFAULT_COEFF)
        period = params.get("period", DEFAULT_PERIOD)

        # Rejim tespiti (son 300 mum)
        regime = detect_regime(closes[-300:], highs[-300:], lows[-300:])

        # Her filtre paketi icin backtest
        for fc in FILTER_PACKAGES:
            trades = run_backtest_with_filters(
                closes, highs, lows, volumes, timestamps,
                coeff=coeff, period=period,
                filter_cfg=fc,
                symbol=symbol,
                regime=regime,
            )
            all_package_trades[fc.name].extend(trades)

        # Orijinal trade sayisi goster
        orig_count = len([t for t in all_package_trades[FILTER_PACKAGES[0].name]
                          if t.symbol == symbol])
        print(f" | {orig_count} trade (orig) | rejim: {regime}", flush=True)

        time.sleep(0.15)  # rate limit

    elapsed = time.time() - t0

    # ═══════════════════════════════════════════════════════════════
    #  SONUCLAR
    # ═══════════════════════════════════════════════════════════════

    print("\n\n" + "=" * 120)
    print("  SONUCLAR — FILTRE PAKETI KARSILASTIRMASI")
    print("=" * 120)

    # Istatistikleri hesapla
    all_stats: list[PackageStats] = []
    for fc in FILTER_PACKAGES:
        trades = all_package_trades[fc.name]
        stats = calc_stats(trades, fc.name)
        all_stats.append(stats)

    # ── Ozet Tablo ──
    orig = all_stats[0]

    print(f"\n  {'Paket':<32} {'Trade':>6} {'Win':>5} {'Loss':>5} {'WR%':>7} "
          f"{'Net PnL%':>9} {'PF':>6} {'MaxDD%':>7} {'AvgPnL':>8} {'AvgHold':>8}")
    print(f"  {'-'*103}")

    for s in all_stats:
        marker = " <-- AKTIF" if s.name == "ASAMA 1 (ER+RANGING)" else ""
        print(f"  {s.name:<32} {s.total_trades:>6} {s.wins:>5} {s.losses:>5} "
              f"{s.win_rate:>6.1f}% {s.net_pnl_pct:>+8.2f}% {s.profit_factor:>5.2f} "
              f"{s.max_drawdown_pct:>6.2f}% {s.avg_pnl_pct:>+7.3f}% "
              f"{s.avg_hold_bars:>7.1f}{marker}")

    # ── Delta Tablosu (orijinale gore) ──
    print(f"\n  {'Paket':<32} {'dTrade':>7} {'dWR':>7} {'dPnL':>9} {'dPF':>7} {'dDD':>7}")
    print(f"  {'-'*70}")
    for s in all_stats[1:]:
        d_trade = s.total_trades - orig.total_trades
        d_wr = s.win_rate - orig.win_rate
        d_pnl = s.net_pnl_pct - orig.net_pnl_pct
        d_pf = s.profit_factor - orig.profit_factor
        d_dd = s.max_drawdown_pct - orig.max_drawdown_pct
        print(f"  {s.name:<32} {d_trade:>+7} {d_wr:>+6.1f}% {d_pnl:>+8.2f}% "
              f"{d_pf:>+6.2f} {d_dd:>+6.2f}%")

    # ── LONG vs SHORT Analizi ──
    print(f"\n\n{'=' * 100}")
    print(f"  LONG vs SHORT ANALIZI")
    print(f"{'=' * 100}")

    print(f"\n  {'Paket':<32} {'L.Trade':>8} {'L.Win%':>7} {'L.PnL%':>9} "
          f"{'S.Trade':>8} {'S.Win%':>7} {'S.PnL%':>9}")
    print(f"  {'-'*88}")
    for s in all_stats:
        l_wr = s.long_wins / s.long_trades * 100 if s.long_trades > 0 else 0
        s_wr = s.short_wins / s.short_trades * 100 if s.short_trades > 0 else 0
        print(f"  {s.name:<32} {s.long_trades:>8} {l_wr:>6.1f}% {s.long_pnl:>+8.2f}% "
              f"{s.short_trades:>8} {s_wr:>6.1f}% {s.short_pnl:>+8.2f}%")

    # ── Coin Bazli En Iyi Paket ──
    print(f"\n\n{'=' * 100}")
    print(f"  COIN BAZLI ANALIZ (Orijinal vs En Iyi Filtre)")
    print(f"{'=' * 100}")

    all_coins = set()
    for s in all_stats:
        all_coins.update(s.coin_details.keys())

    print(f"\n  {'Coin':<12} {'Orig.T':>7} {'Orig.WR':>8} {'Orig.PnL':>9} "
          f"{'Best.T':>7} {'Best.WR':>8} {'Best.PnL':>9} {'En Iyi Paket':<32}")
    print(f"  {'-'*100}")

    for coin in sorted(all_coins):
        # Orijinal
        o = orig.coin_details.get(coin, {"trades": 0, "wins": 0, "pnl": 0.0})
        o_wr = o["wins"] / o["trades"] * 100 if o["trades"] > 0 else 0

        # En iyi paket (net PnL'ye gore)
        best_s = None
        best_pnl = -999
        for s in all_stats[1:]:
            d = s.coin_details.get(coin, {"trades": 0, "wins": 0, "pnl": 0.0})
            if d["pnl"] > best_pnl:
                best_pnl = d["pnl"]
                best_s = s

        if best_s:
            b = best_s.coin_details.get(coin, {"trades": 0, "wins": 0, "pnl": 0.0})
            b_wr = b["wins"] / b["trades"] * 100 if b["trades"] > 0 else 0
            print(f"  {coin:<12} {o['trades']:>7} {o_wr:>7.1f}% {o['pnl']:>+8.2f}% "
                  f"{b['trades']:>7} {b_wr:>7.1f}% {b['pnl']:>+8.2f}% {best_s.name:<32}")

    # ── SL/Win Orani Analizi ──
    print(f"\n\n{'=' * 100}")
    print(f"  ZARAR ENGELLEME ANALIZI (Orijinal vs Asama 1)")
    print(f"{'=' * 100}")

    orig_trades = set((t.symbol, t.entry_idx) for t in all_stats[0].all_trades)
    asama1_trades = set((t.symbol, t.entry_idx) for t in all_stats[1].all_trades)

    # Orijinalde olan ama Asama 1'de olmayan tradeler (engellenen)
    blocked_keys = orig_trades - asama1_trades
    blocked_trades = [t for t in all_stats[0].all_trades
                      if (t.symbol, t.entry_idx) in blocked_keys]

    if blocked_trades:
        blocked_wins = [t for t in blocked_trades if t.pnl_pct > 0]
        blocked_losses = [t for t in blocked_trades if t.pnl_pct <= 0]
        eng_zarar = sum(t.pnl_pct for t in blocked_losses)
        kayb_kar = sum(t.pnl_pct for t in blocked_wins)

        print(f"\n  Engellenen trade: {len(blocked_trades)}")
        print(f"    Loss engellenen: {len(blocked_losses)} (toplam: {eng_zarar:+.2f}%)")
        print(f"    Win kaybedilen:  {len(blocked_wins)} (toplam: {kayb_kar:+.2f}%)")
        print(f"    NET FAYDA:       {abs(eng_zarar) - kayb_kar:+.2f}%")

        # En buyuk engellenen zararlar
        if blocked_losses:
            print(f"\n  En buyuk engellenen zararlar:")
            print(f"    {'#':<3} {'Coin':<12} {'Yon':<7} {'PnL%':>8} {'RSI':>6} {'ER':>6} {'MACD_H':>10}")
            print(f"    {'-'*55}")
            for i, t in enumerate(sorted(blocked_losses, key=lambda x: x.pnl_pct)[:15]):
                print(f"    {i+1:<3} {t.symbol:<12} {t.direction:<7} "
                      f"{t.pnl_pct:>+7.2f}% {t.rsi_val:>5.1f} {t.er_val:>5.3f} "
                      f"{t.macd_hist:>+10.6f}")

    # ── Filtre Etkinligi ──
    print(f"\n\n{'=' * 100}")
    print(f"  FILTRE ETKINLIGI — Hangi filtre en cok zarar engelledi?")
    print(f"{'=' * 100}")

    # Her bir filtreyi tek basina test et
    single_filters = [
        FilterConfig("Sadece MACD", macd_align=True),
        FilterConfig("Sadece RSI", rsi_align=True),
        FilterConfig("Sadece ER>0.2", er_filter=True, er_min=0.2),
        FilterConfig("Sadece RANGING rej.", ranging_reject=True),
    ]

    print(f"\n  {'Filtre':<25} {'Trade':>6} {'dTrade':>7} {'WR%':>7} {'dWR':>7} "
          f"{'PnL%':>9} {'dPnL':>9} {'PF':>6}")
    print(f"  {'-'*83}")
    print(f"  {'ORIJINAL (referans)':<25} {orig.total_trades:>6} {'---':>7} "
          f"{orig.win_rate:>6.1f}% {'---':>7} {orig.net_pnl_pct:>+8.2f}% {'---':>9} "
          f"{orig.profit_factor:>5.2f}")

    for fc in single_filters:
        single_trades = []
        for si, symbol in enumerate(symbols):
            df_key = f"{symbol}_data"
            # Bu filtreyle tekrar backtest lazim — ama veri zaten cekili
            # all_package_trades'ten istatistik cikarabiliriz, ama ayri calistirdik yukarida
            pass

    # Yukardaki single_filter'lar ayri calistirmak gerekiyor.
    # Bunun yerine mevcut paketler arasinda karsilastirma yapalim:
    # PAKET F (sadece ER) vs ASAMA 1 (ER+RANGING) ile ayirt edebiliriz

    # ── Rejim Bazli Analiz ──
    print(f"\n\n{'=' * 100}")
    print(f"  REJIM BAZLI ANALIZ")
    print(f"{'=' * 100}")

    for s in all_stats[:3]:  # Orijinal, Asama 1, Paket A
        print(f"\n  {s.name}:")
        regime_stats = {}
        for t in s.all_trades:
            r = t.regime or "UNKNOWN"
            if r not in regime_stats:
                regime_stats[r] = {"trades": 0, "wins": 0, "pnl": 0.0}
            regime_stats[r]["trades"] += 1
            if t.pnl_pct > 0:
                regime_stats[r]["wins"] += 1
            regime_stats[r]["pnl"] += t.pnl_pct

        print(f"    {'Rejim':<15} {'Trade':>6} {'WR%':>7} {'PnL%':>9}")
        print(f"    {'-'*40}")
        for r, d in sorted(regime_stats.items()):
            wr = d["wins"] / d["trades"] * 100 if d["trades"] > 0 else 0
            print(f"    {r:<15} {d['trades']:>6} {wr:>6.1f}% {d['pnl']:>+8.2f}%")

    # ═══════════════════════════════════════════════════════════════
    #  SONUC ve ONERILER
    # ═══════════════════════════════════════════════════════════════

    print(f"\n\n{'=' * 100}")
    print(f"  SONUC VE ONERILER")
    print(f"{'=' * 100}")

    # En iyi paket (PnL + PF birlesik skor)
    best_pkg = max(all_stats[1:],
                   key=lambda s: s.net_pnl_pct * s.profit_factor if s.total_trades >= 5 else -999)
    worst_pkg = min(all_stats[1:],
                    key=lambda s: s.net_pnl_pct if s.total_trades >= 5 else 999)

    print(f"\n  EN IYI PAKET:  {best_pkg.name}")
    print(f"    PnL: {best_pkg.net_pnl_pct:+.2f}% | WR: {best_pkg.win_rate:.1f}% | "
          f"PF: {best_pkg.profit_factor:.2f} | Trade: {best_pkg.total_trades}")
    print(f"\n  EN KOTU PAKET: {worst_pkg.name}")
    print(f"    PnL: {worst_pkg.net_pnl_pct:+.2f}% | WR: {worst_pkg.win_rate:.1f}% | "
          f"PF: {worst_pkg.profit_factor:.2f} | Trade: {worst_pkg.total_trades}")
    print(f"\n  ORIJINAL (referans):")
    print(f"    PnL: {orig.net_pnl_pct:+.2f}% | WR: {orig.win_rate:.1f}% | "
          f"PF: {orig.profit_factor:.2f} | Trade: {orig.total_trades}")

    # Asama 1 vs Orijinal karsilastirma
    a1 = all_stats[1]
    print(f"\n  ASAMA 1 (aktif config) vs ORIJINAL:")
    print(f"    WR:  {orig.win_rate:.1f}% → {a1.win_rate:.1f}% ({a1.win_rate - orig.win_rate:+.1f}%)")
    print(f"    PnL: {orig.net_pnl_pct:+.2f}% → {a1.net_pnl_pct:+.2f}% ({a1.net_pnl_pct - orig.net_pnl_pct:+.2f}%)")
    print(f"    PF:  {orig.profit_factor:.2f} → {a1.profit_factor:.2f} ({a1.profit_factor - orig.profit_factor:+.2f})")
    print(f"    Trade: {orig.total_trades} → {a1.total_trades} ({a1.total_trades - orig.total_trades:+d})")

    print(f"\n  Sure: {elapsed:.1f}s")

    # ── JSON kaydet ──
    output = {
        "timestamp": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "symbols": symbols,
        "target_bars": target_bars,
        "results": {}
    }
    for s in all_stats:
        output["results"][s.name] = {
            "total_trades": s.total_trades,
            "wins": s.wins,
            "losses": s.losses,
            "win_rate": round(s.win_rate, 2),
            "net_pnl_pct": round(s.net_pnl_pct, 4),
            "profit_factor": round(s.profit_factor, 3),
            "max_drawdown_pct": round(s.max_drawdown_pct, 3),
            "long_trades": s.long_trades,
            "long_pnl": round(s.long_pnl, 4),
            "short_trades": s.short_trades,
            "short_pnl": round(s.short_pnl, 4),
            "coin_details": {k: {kk: round(vv, 4) if isinstance(vv, float) else vv
                                  for kk, vv in v.items()}
                             for k, v in s.coin_details.items()},
        }

    out_path = Path("data/backtest_comparison.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    print(f"\n  Kaydedildi: {out_path}")


if __name__ == "__main__":
    main()
