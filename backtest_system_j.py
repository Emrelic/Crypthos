"""System J Backtest — Rejim & Yön Tespiti Dogruluk Testi.

Son 30 gün, 7 coin, 3 timeframe (5m, 15m, 1h).
Sliding window ile her pencerede:
  1) Rejim tespiti (TRENDING/RANGING) → sonraki N mum gerçek hareket ile karşılaştır
  2) Yön tespiti (LONG/SHORT) → sonraki N mum yönü ile karşılaştır

Dogruluk metrikleri:
  - Rejim: ER/Hurst tespiti vs gerçek fiyat hareketi (trend mi range mı)
  - Yön: Tespit edilen yön vs gerçek fiyat yönü
"""
import sys
import os
import time
import numpy as np
import pandas as pd
from collections import defaultdict

# Project imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from market.binance_rest import BinanceRestClient
from scanner.system_b_scanner import (
    detect_zigzag_swings, analyze_waves,
    compute_rolling_er, compute_hurst_exponent,
)
from indicators.indicator_engine import IndicatorEngine
from core.config_manager import ConfigManager

# ─────────────────────────── CONFIG ───────────────────────────

COINS = ["BTCUSDT", "ETHUSDT", "XRPUSDT", "SOLUSDT", "ADAUSDT", "DOGEUSDT", "BNBUSDT"]
TIMEFRAMES = ["5m", "15m", "1h"]
CONFIRM_TF_MAP = {"1m": "30m", "3m": "1h", "5m": "2h", "15m": "6h", "30m": "12h", "1h": "1d"}

# Sliding window: analiz penceresi + forward bakış penceresi
ANALYSIS_WINDOW = 200   # analiz için kullanılan mum sayısı
FORWARD_WINDOWS = {     # TF'ye göre forward bakış (mum sayısı)
    "5m": 36,           # 3 saat = 36 mum
    "15m": 16,          # 4 saat = 16 mum
    "1h": 12,           # 12 saat = 12 mum
}
STEP_SIZE = {           # kaç mum atlayarak ilerle
    "5m": 12,           # 1 saat
    "15m": 4,           # 1 saat
    "1h": 2,            # 2 saat
}

# Son 30 gün için gereken mum sayısı
KLINE_LIMITS = {
    "5m": 1500,         # 30*24*12 = 8640 → Binance max 1500
    "15m": 1500,        # 30*24*4 = 2880 → 1500
    "1h": 720,          # 30*24 = 720
}

# Regime thresholds (System J config)
ER_TRENDING = 0.25
ER_RANGING = 0.08
HURST_TRENDING = 0.55
HURST_RANGING = 0.45

# Direction thresholds
EMA_FAST = 9
EMA_SLOW = 21
RSI_LONG = 55
RSI_SHORT = 45
EMA_GAP_MIN = 0.05 / 100.0

# ─────────────────────────── HELPERS ───────────────────────────

def _ema_value(closes, period):
    if len(closes) < period:
        return 0.0
    alpha = 2.0 / (period + 1)
    ema = float(closes[0])
    for i in range(1, len(closes)):
        ema = alpha * float(closes[i]) + (1.0 - alpha) * ema
    return ema

def compute_rsi(closes, period=14):
    if len(closes) < period + 1:
        return 50.0
    deltas = np.diff(closes)
    gains = np.where(deltas > 0, deltas, 0)
    losses = np.where(deltas < 0, -deltas, 0)
    avg_gain = np.mean(gains[:period])
    avg_loss = np.mean(losses[:period])
    for i in range(period, len(deltas)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))

def compute_macd_hist(closes, fast=8, slow=17, signal=9):
    if len(closes) < slow + signal:
        return 0.0
    ema_fast = _ema_series(closes, fast)
    ema_slow = _ema_series(closes, slow)
    macd_line = ema_fast - ema_slow
    signal_line = _ema_series(macd_line, signal)
    return float(macd_line[-1] - signal_line[-1])

def _ema_series(data, period):
    alpha = 2.0 / (period + 1)
    result = np.zeros(len(data))
    result[0] = data[0]
    for i in range(1, len(data)):
        result[i] = alpha * data[i] + (1 - alpha) * result[i - 1]
    return result

def compute_bb(closes, period=20, std_mult=2.0):
    if len(closes) < period:
        return 0, 0, 0
    sma = np.mean(closes[-period:])
    std = np.std(closes[-period:])
    return sma + std_mult * std, sma, sma - std_mult * std

# ─────────────────────────── REGIME ───────────────────────────

def detect_regime(closes):
    """System J rejim tespiti — ER + Hurst dual vote."""
    if len(closes) < 30:
        return "UNDECIDED", 0.0, 0.0, 0.0

    er = compute_rolling_er(closes, window=20, median_count=10)
    hurst = compute_hurst_exponent(closes) if len(closes) >= 128 else 0.5

    if er > ER_TRENDING:
        regime = "TRENDING"
        conf = min(1.0, (er - ER_TRENDING) / 0.15)
    elif er < ER_RANGING:
        regime = "RANGING"
        conf = min(1.0, (ER_RANGING - er) / 0.05)
    else:
        # Gray zone
        midpoint = (ER_TRENDING + ER_RANGING) / 2.0
        if hurst > HURST_TRENDING:
            regime = "TRENDING"
            conf = min(0.7, (hurst - HURST_TRENDING) / 0.3)
        elif hurst < HURST_RANGING:
            regime = "RANGING"
            conf = min(0.7, (HURST_RANGING - hurst) / 0.3)
        elif er > midpoint:
            regime = "TRENDING"
            conf = 0.3
        else:
            regime = "RANGING"
            conf = 0.3

    return regime, conf, er, hurst

def verify_regime(forward_closes):
    """Forward penceredeki gerçek rejimi ölç.

    Gerçek rejim tespiti:
    - ER > 0.20 → gerçekten trend (net hareket)
    - ER < 0.10 → gerçekten ranging (yatar hareket)
    - Arası → belirsiz
    """
    if len(forward_closes) < 5:
        return "UNDECIDED"
    er = compute_rolling_er(forward_closes, window=min(20, len(forward_closes) - 1),
                            median_count=min(5, max(1, len(forward_closes) - 10)))
    if er > 0.20:
        return "TRENDING"
    elif er < 0.10:
        return "RANGING"
    return "UNDECIDED"

# ─────────────────────────── DIRECTION ───────────────────────────

def detect_direction_trending(closes):
    """Trend yön tespiti: EMA + MACD + RSI (2/3 oylama)."""
    ema_fast = _ema_value(closes, EMA_FAST)
    ema_slow = _ema_value(closes, EMA_SLOW)
    rsi = compute_rsi(closes)
    macd_hist = compute_macd_hist(closes)

    votes = 0
    # EMA
    if ema_fast > 0 and ema_slow > 0:
        gap = abs(ema_fast - ema_slow) / ema_slow
        if gap >= EMA_GAP_MIN:
            votes += 1 if ema_fast > ema_slow else -1

    # MACD
    if macd_hist > 0:
        votes += 1
    elif macd_hist < 0:
        votes -= 1

    # RSI
    if rsi > RSI_LONG:
        votes += 1
    elif rsi < RSI_SHORT:
        votes -= 1

    if abs(votes) >= 2:
        return "LONG" if votes > 0 else "SHORT", abs(votes) / 3.0
    return "SKIP", 0.0

def detect_direction_ranging(closes):
    """Ranging yön tespiti: RSI inverse + BB."""
    rsi = compute_rsi(closes)
    bb_upper, bb_middle, bb_lower = compute_bb(closes)
    price = closes[-1]

    if rsi > 70:
        return "SHORT", 1.0
    elif rsi < 30:
        return "LONG", 1.0
    elif bb_upper > 0 and bb_lower > 0:
        if price > bb_upper * 0.95:
            return "SHORT", 0.7
        elif price < bb_lower * 1.05:
            return "LONG", 0.7
    return "SKIP", 0.0

def verify_direction(forward_closes):
    """Forward penceredeki gerçek yön."""
    if len(forward_closes) < 2:
        return "SKIP"
    change_pct = (forward_closes[-1] - forward_closes[0]) / forward_closes[0] * 100
    if abs(change_pct) < 0.05:  # < %0.05 değişim → yön yok
        return "SKIP"
    return "LONG" if change_pct > 0 else "SHORT"

# ─────────────────────────── BACKTEST ───────────────────────────

def fetch_all_klines(client, coins, timeframes):
    """Tüm coinler ve TF'ler için kline çek."""
    data = {}
    for coin in coins:
        data[coin] = {}
        for tf in timeframes:
            limit = KLINE_LIMITS[tf]
            try:
                df = client.get_klines(coin, tf, limit)
                data[coin][tf] = df
                print(f"  {coin} {tf}: {len(df)} mum")
                time.sleep(0.1)
            except Exception as e:
                print(f"  {coin} {tf}: HATA - {e}")
                data[coin][tf] = pd.DataFrame()
    return data

def run_backtest():
    print("=" * 80)
    print("SYSTEM J BACKTEST — Rejim & Yön Tespiti Dogruluk Analizi")
    print(f"Coinler: {', '.join(COINS)}")
    print(f"Timeframe'ler: {', '.join(TIMEFRAMES)}")
    print(f"Analiz penceresi: {ANALYSIS_WINDOW} mum")
    print("=" * 80)

    # Binance REST client (public, no auth needed for klines)
    client = BinanceRestClient()

    # Fetch data
    print("\n[*] Veri cekiliyor...")
    all_data = fetch_all_klines(client, COINS, TIMEFRAMES)

    # Results storage
    regime_results = defaultdict(lambda: {"correct": 0, "wrong": 0, "skip": 0,
                                           "total": 0, "details": []})
    direction_results = defaultdict(lambda: {"correct": 0, "wrong": 0, "skip": 0,
                                              "total": 0, "details": []})

    # Also confirm TF data for direction (trending requires confirm TF agreement)
    print("\n[*] Teyit TF verileri cekiliyor...")
    confirm_data = {}
    for coin in COINS:
        confirm_data[coin] = {}
        for tf in TIMEFRAMES:
            ctf = CONFIRM_TF_MAP.get(tf)
            if ctf and ctf not in [t for t in TIMEFRAMES]:  # sadece farklıysa çek
                try:
                    df = client.get_klines(coin, ctf, 500)
                    confirm_data[coin][ctf] = df
                    print(f"  {coin} {ctf}: {len(df)} mum")
                    time.sleep(0.1)
                except Exception as e:
                    print(f"  {coin} {ctf}: HATA - {e}")
                    confirm_data[coin][ctf] = pd.DataFrame()

    # Run sliding window analysis
    print("\n" + "=" * 80)
    print("ANALIZ BASLIYOR...")
    print("=" * 80)

    for coin in COINS:
        for tf in TIMEFRAMES:
            df = all_data[coin][tf]
            if df.empty or len(df) < ANALYSIS_WINDOW + FORWARD_WINDOWS[tf]:
                print(f"\n{coin} {tf}: Yetersiz veri ({len(df)} mum)")
                continue

            closes = df["close"].values.astype(float)
            highs = df["high"].values.astype(float)
            lows = df["low"].values.astype(float)
            forward_win = FORWARD_WINDOWS[tf]
            step = STEP_SIZE[tf]

            key = f"{coin}_{tf}"
            n_windows = 0

            for start in range(0, len(closes) - ANALYSIS_WINDOW - forward_win, step):
                end = start + ANALYSIS_WINDOW
                window_closes = closes[start:end]
                forward_closes = closes[end:end + forward_win]

                # ── REJIM TESTİ ──
                pred_regime, conf, er, hurst = detect_regime(window_closes)
                actual_regime = verify_regime(forward_closes)

                regime_results[key]["total"] += 1
                if actual_regime == "UNDECIDED":
                    regime_results[key]["skip"] += 1
                elif pred_regime == actual_regime:
                    regime_results[key]["correct"] += 1
                else:
                    regime_results[key]["wrong"] += 1

                # ── YÖN TESTİ ──
                if pred_regime == "TRENDING":
                    pred_dir, strength = detect_direction_trending(window_closes)
                else:
                    pred_dir, strength = detect_direction_ranging(window_closes)

                actual_dir = verify_direction(forward_closes)

                direction_results[key]["total"] += 1
                if pred_dir == "SKIP" or actual_dir == "SKIP":
                    direction_results[key]["skip"] += 1
                elif pred_dir == actual_dir:
                    direction_results[key]["correct"] += 1
                else:
                    direction_results[key]["wrong"] += 1

                n_windows += 1

            print(f"\n{coin} {tf}: {n_windows} pencere analiz edildi")

    # ─────────────────────────── SONUCLAR ───────────────────────────
    print("\n" + "=" * 80)
    print("SONUCLAR")
    print("=" * 80)

    # Rejim sonuclari
    print("\n" + "-" * 75)
    print("  REJIM TESPITI SONUCLARI")
    print("-" * 75)
    hdr = f"{'Coin/TF':<14} {'Total':>6} {'Dogru':>6} {'Yanlis':>6} {'Skip':>6} {'Oran%':>7} {'Adj%':>7}"
    print(hdr)
    print("-" * 75)

    total_all = {"correct": 0, "wrong": 0, "skip": 0, "total": 0}
    tf_totals = {tf: {"correct": 0, "wrong": 0, "skip": 0, "total": 0} for tf in TIMEFRAMES}
    coin_totals = {c: {"correct": 0, "wrong": 0, "skip": 0, "total": 0} for c in COINS}

    for coin in COINS:
        for tf in TIMEFRAMES:
            key = f"{coin}_{tf}"
            r = regime_results[key]
            decided = r["correct"] + r["wrong"]
            rate = (r["correct"] / decided * 100) if decided > 0 else 0
            adj_rate = (r["correct"] / r["total"] * 100) if r["total"] > 0 else 0
            short_coin = coin.replace("USDT", "")
            print(f"{short_coin:>6}/{tf:<6} {r['total']:>6} {r['correct']:>6} {r['wrong']:>6} {r['skip']:>6} {rate:>6.1f}% {adj_rate:>6.1f}%")

            for d in [total_all, tf_totals[tf], coin_totals[coin]]:
                d["correct"] += r["correct"]
                d["wrong"] += r["wrong"]
                d["skip"] += r["skip"]
                d["total"] += r["total"]

    print("-" * 75)
    for tf in TIMEFRAMES:
        t = tf_totals[tf]
        decided = t["correct"] + t["wrong"]
        rate = (t["correct"] / decided * 100) if decided > 0 else 0
        adj = (t["correct"] / t["total"] * 100) if t["total"] > 0 else 0
        print(f"{'TOPLAM':>6}/{tf:<6} {t['total']:>6} {t['correct']:>6} {t['wrong']:>6} {t['skip']:>6} {rate:>6.1f}% {adj:>6.1f}%")

    print("-" * 75)
    decided = total_all["correct"] + total_all["wrong"]
    rate = (total_all["correct"] / decided * 100) if decided > 0 else 0
    adj = (total_all["correct"] / total_all["total"] * 100) if total_all["total"] > 0 else 0
    print(f"{'GENEL':<14} {total_all['total']:>6} {total_all['correct']:>6} {total_all['wrong']:>6} {total_all['skip']:>6} {rate:>6.1f}% {adj:>6.1f}%")
    print("=" * 75)

    # Yon sonuclari
    print("\n" + "-" * 75)
    print("  YON TESPITI SONUCLARI")
    print("-" * 75)
    print(hdr)
    print("-" * 75)

    total_all_d = {"correct": 0, "wrong": 0, "skip": 0, "total": 0}
    tf_totals_d = {tf: {"correct": 0, "wrong": 0, "skip": 0, "total": 0} for tf in TIMEFRAMES}
    coin_totals_d = {c: {"correct": 0, "wrong": 0, "skip": 0, "total": 0} for c in COINS}

    for coin in COINS:
        for tf in TIMEFRAMES:
            key = f"{coin}_{tf}"
            r = direction_results[key]
            decided = r["correct"] + r["wrong"]
            rate = (r["correct"] / decided * 100) if decided > 0 else 0
            adj_rate = (r["correct"] / r["total"] * 100) if r["total"] > 0 else 0
            short_coin = coin.replace("USDT", "")
            print(f"{short_coin:>6}/{tf:<6} {r['total']:>6} {r['correct']:>6} {r['wrong']:>6} {r['skip']:>6} {rate:>6.1f}% {adj_rate:>6.1f}%")

            for d in [total_all_d, tf_totals_d[tf], coin_totals_d[coin]]:
                d["correct"] += r["correct"]
                d["wrong"] += r["wrong"]
                d["skip"] += r["skip"]
                d["total"] += r["total"]

    print("-" * 75)
    for tf in TIMEFRAMES:
        t = tf_totals_d[tf]
        decided = t["correct"] + t["wrong"]
        rate = (t["correct"] / decided * 100) if decided > 0 else 0
        adj = (t["correct"] / t["total"] * 100) if t["total"] > 0 else 0
        print(f"{'TOPLAM':>6}/{tf:<6} {t['total']:>6} {t['correct']:>6} {t['wrong']:>6} {t['skip']:>6} {rate:>6.1f}% {adj:>6.1f}%")

    print("-" * 75)
    decided = total_all_d["correct"] + total_all_d["wrong"]
    rate = (total_all_d["correct"] / decided * 100) if decided > 0 else 0
    adj = (total_all_d["correct"] / total_all_d["total"] * 100) if total_all_d["total"] > 0 else 0
    print(f"{'GENEL':<14} {total_all_d['total']:>6} {total_all_d['correct']:>6} {total_all_d['wrong']:>6} {total_all_d['skip']:>6} {rate:>6.1f}% {adj:>6.1f}%")
    print("=" * 75)

    # Coin bazli ozet
    print("\n" + "-" * 50)
    print("  COIN BAZLI OZET")
    print("-" * 50)
    print(f"{'Coin':<10} {'Rejim%':>10} {'Yon%':>10}")
    print("-" * 50)
    for coin in COINS:
        cr = coin_totals[coin]
        cd = coin_totals_d[coin]
        rd = cr["correct"] + cr["wrong"]
        rr = (cr["correct"] / rd * 100) if rd > 0 else 0
        dd = cd["correct"] + cd["wrong"]
        dr = (cd["correct"] / dd * 100) if dd > 0 else 0
        short = coin.replace("USDT", "")
        print(f"{short:<10} {rr:>9.1f}% {dr:>9.1f}%")
    print("=" * 50)

    # Rejim dagilimi
    print("\n" + "-" * 50)
    print("  REJIM DAGILIMI (TF BAZLI)")
    print("-" * 50)
    print(f"{'TF':<10} {'TRENDING':>18} {'RANGING':>18}")
    print("-" * 50)

    for tf in TIMEFRAMES:
        trending_count = 0
        ranging_count = 0
        for coin in COINS:
            df = all_data[coin][tf]
            if df.empty:
                continue
            closes = df["close"].values.astype(float)
            forward_win = FORWARD_WINDOWS[tf]
            step = STEP_SIZE[tf]
            for start in range(0, len(closes) - ANALYSIS_WINDOW - forward_win, step):
                end = start + ANALYSIS_WINDOW
                window_closes = closes[start:end]
                regime, _, _, _ = detect_regime(window_closes)
                if regime == "TRENDING":
                    trending_count += 1
                elif regime == "RANGING":
                    ranging_count += 1
        total = trending_count + ranging_count
        tp = (trending_count / total * 100) if total > 0 else 0
        rp = (ranging_count / total * 100) if total > 0 else 0
        print(f"{tf:<10} {trending_count:>5} ({tp:>5.1f}%) {ranging_count:>8} ({rp:>5.1f}%)")

    print("=" * 50)

    print("\nAciklamalar:")
    print("  Oran%: Sadece karara varilan (skip haric) ornekler uzerinden dogruluk")
    print("  Adj%: Tum ornekler uzerinden dogruluk (skip = yanlis sayilir)")
    print(f"  Forward pencere: 5m={FORWARD_WINDOWS['5m']} mum (3 saat), "
          f"15m={FORWARD_WINDOWS['15m']} mum (4 saat), "
          f"1h={FORWARD_WINDOWS['1h']} mum (12 saat)")
    print("  Rejim dogrulama: forward ER > 0.20 = TRENDING, < 0.10 = RANGING")
    print("  Yon dogrulama: forward fiyat degisimi > %0.05 = yon var")


if __name__ == "__main__":
    run_backtest()
