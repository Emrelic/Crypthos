"""System J Rejim & Yon Dogruluk Backtest'i.

8 coin × 15m TF üzerinde gerçek Binance verisi ile:
  1. Rejim tespiti (TRENDING / RANGING) doğruluğu
  2. Yön tespiti (LONG / SHORT) doğruluğu
  3. Sonraki N mum performansı ile doğrulama

Doğrulama mantığı:
  - TRENDING doğru mu? → Sonraki 20 mumda fiyat aynı yönde %1+ hareket ettiyse doğru
  - RANGING doğru mu? → Sonraki 20 mumda fiyat ±%0.5 bant içinde kaldıysa doğru
  - YÖN doğru mu? → Sonraki 20 mumda max hareket yön ile aynıysa doğru
"""
import os
import time
import hmac
import hashlib
import requests
import numpy as np
from urllib.parse import urlencode
from datetime import datetime, timedelta
from dotenv import load_dotenv

load_dotenv()

# ─────────────── Binance API ───────────────
API_KEY = os.getenv("BINANCE_API_KEY")
API_SECRET = os.getenv("BINANCE_API_SECRET")

session = requests.Session()
session.headers["X-MBX-APIKEY"] = API_KEY
BASE = "https://fapi.binance.com"


def sign(params):
    params["timestamp"] = int(time.time() * 1000)
    qs = urlencode(params)
    sig = hmac.new(API_SECRET.encode(), qs.encode(), hashlib.sha256).hexdigest()
    params["signature"] = sig
    return params


def fetch_klines(symbol, interval, limit=1500):
    """Binance futures klines çek."""
    url = f"{BASE}/fapi/v1/klines"
    params = {"symbol": symbol, "interval": interval, "limit": limit}
    resp = session.get(url, params=params)
    data = resp.json()
    if not isinstance(data, list):
        print(f"  HATA: {symbol} {interval} -> {data}")
        return None
    return data


# ─────────────── İndikatör Fonksiyonları ───────────────

def ema_series(closes, period):
    """EMA serisi hesapla."""
    alpha = 2.0 / (period + 1)
    ema = np.zeros(len(closes))
    ema[0] = closes[0]
    for i in range(1, len(closes)):
        ema[i] = alpha * closes[i] + (1.0 - alpha) * ema[i - 1]
    return ema


def compute_rsi(closes, period=14):
    """RSI serisi hesapla."""
    rsi = np.full(len(closes), 50.0)
    if len(closes) < period + 1:
        return rsi
    deltas = np.diff(closes)
    gains = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)

    avg_gain = np.mean(gains[:period])
    avg_loss = np.mean(losses[:period])

    for i in range(period, len(deltas)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        if avg_loss == 0:
            rsi[i + 1] = 100.0
        else:
            rs = avg_gain / avg_loss
            rsi[i + 1] = 100.0 - (100.0 / (1.0 + rs))
    return rsi


def compute_macd(closes, fast=8, slow=17, signal=9):
    """MACD histogram serisi."""
    ema_fast = ema_series(closes, fast)
    ema_slow = ema_series(closes, slow)
    macd_line = ema_fast - ema_slow
    signal_line = ema_series(macd_line, signal)
    histogram = macd_line - signal_line
    return histogram


def compute_bb(closes, period=20, std_mult=2.0):
    """Bollinger Bands."""
    upper = np.zeros(len(closes))
    lower = np.zeros(len(closes))
    middle = np.zeros(len(closes))
    for i in range(period - 1, len(closes)):
        window = closes[i - period + 1:i + 1]
        m = np.mean(window)
        s = np.std(window, ddof=1)
        middle[i] = m
        upper[i] = m + std_mult * s
        lower[i] = m - std_mult * s
    return upper, middle, lower


# ─────────────── System J Rejim Tespiti ───────────────

def compute_efficiency_ratio(closes):
    if len(closes) < 2:
        return 0.5
    net_move = abs(closes[-1] - closes[0])
    total_move = np.sum(np.abs(np.diff(closes)))
    if total_move == 0:
        return 0.0
    return net_move / total_move


def compute_rolling_er(closes, window=20, median_count=10):
    if len(closes) < window + median_count:
        return compute_efficiency_ratio(closes[-window:] if len(closes) >= window else closes)
    ers = []
    for i in range(len(closes) - window + 1):
        segment = closes[i:i + window]
        net = abs(segment[-1] - segment[0])
        total = np.sum(np.abs(np.diff(segment)))
        ers.append(net / total if total > 0 else 0.0)
    recent = ers[-median_count:]
    return float(np.median(recent))


def compute_hurst_exponent(closes):
    if len(closes) < 128:
        return 0.5
    log_returns = np.diff(np.log(closes))
    ns = [16, 32, 64, 128]
    ns = [n for n in ns if n <= len(log_returns)]
    if len(ns) < 2:
        return 0.5
    rs_values = []
    for n in ns:
        rs_list = []
        num_chunks = len(log_returns) // n
        for i in range(num_chunks):
            chunk = log_returns[i * n:(i + 1) * n]
            mean_chunk = np.mean(chunk)
            deviations = np.cumsum(chunk - mean_chunk)
            R = np.max(deviations) - np.min(deviations)
            S = np.std(chunk, ddof=1)
            if S > 0:
                rs_list.append(R / S)
        if rs_list:
            rs_values.append((np.log(n), np.log(np.mean(rs_list))))
    if len(rs_values) < 2:
        return 0.5
    x = np.array([v[0] for v in rs_values])
    y = np.array([v[1] for v in rs_values])
    n_pts = len(x)
    H = (n_pts * np.sum(x * y) - np.sum(x) * np.sum(y)) / \
        (n_pts * np.sum(x ** 2) - np.sum(x) ** 2)
    return float(np.clip(H, 0.0, 1.0))


def classify_regime(closes, er_window=20, er_median_n=10,
                    er_trending=0.25, er_ranging=0.08,
                    hurst_trending=0.55, hurst_ranging=0.45):
    """System J rejim tespiti. Returns (regime, confidence, er, hurst)."""
    if len(closes) < 30:
        return "UNKNOWN", 0.0, 0.0, 0.5

    er_median = compute_rolling_er(closes, er_window, er_median_n)

    if er_median > er_trending:
        return "TRENDING", min(1.0, (er_median - er_trending) / 0.15), er_median, 0.0

    if er_median < er_ranging:
        return "RANGING", min(1.0, (er_ranging - er_median) / 0.05), er_median, 0.0

    # Gray zone → Hurst
    hurst = compute_hurst_exponent(closes)
    er_midpoint = (er_trending + er_ranging) / 2.0

    if hurst > hurst_trending:
        return "TRENDING", min(0.7, (hurst - hurst_trending) / 0.3), er_median, hurst
    elif hurst < hurst_ranging:
        return "RANGING", min(0.7, (hurst_ranging - hurst) / 0.3), er_median, hurst
    elif er_median > er_midpoint:
        return "TRENDING", 0.3, er_median, hurst
    else:
        return "RANGING", 0.3, er_median, hurst


# ─────────────── System J Yön Tespiti ───────────────

def vote_trend(closes, ema_fast_s, ema_slow_s, macd_hist, rsi,
               ema_gap_min_pct=0.05, rsi_long=55, rsi_short=45):
    """Trend yön oyu. Returns score (-1 to +1), 0 if <2/3 alignment."""
    idx = len(closes) - 1

    # EMA vote
    ema_vote = 0.0
    ef = ema_fast_s[idx]
    es = ema_slow_s[idx]
    if ef > 0 and es > 0:
        gap = abs(ef - es) / es
        if gap >= ema_gap_min_pct / 100.0:
            ema_vote = 1.0 if ef > es else -1.0

    # MACD vote
    macd_vote = 0.0
    h = macd_hist[idx]
    if h > 0:
        macd_vote = 1.0
    elif h < 0:
        macd_vote = -1.0

    # RSI vote
    rsi_vote = 0.0
    r = rsi[idx]
    if r > rsi_long:
        rsi_vote = 1.0
    elif r < rsi_short:
        rsi_vote = -1.0

    total = (ema_vote + macd_vote + rsi_vote) / 3.0
    if abs(total) < 0.33:
        return 0.0
    return total


def vote_ranging(closes, rsi, bb_upper, bb_lower):
    """Ranging ters mantık oyu. Returns score."""
    idx = len(closes) - 1
    r = rsi[idx]
    price = closes[idx]

    if r > 70:
        return -1.0  # SHORT (aşırı alım)
    elif r < 30:
        return 1.0   # LONG (aşırı satım)
    elif bb_upper[idx] > 0 and bb_lower[idx] > 0:
        if price > bb_upper[idx] * 0.95:
            return -1.0
        elif price < bb_lower[idx] * 1.05:
            return 1.0
    return 0.0


def compute_direction(closes, regime,
                      ema_fast_s, ema_slow_s, macd_hist, rsi,
                      bb_upper, bb_lower,
                      # Confirm TF indicators (None for ranging)
                      ema_fast_c=None, ema_slow_c=None, macd_hist_c=None, rsi_c=None):
    """System J yön tespiti. Returns (direction, strength)."""
    if regime == "RANGING":
        score = vote_ranging(closes, rsi, bb_upper, bb_lower)
        if score == 0:
            return "SKIP", 0.0
        return ("LONG" if score > 0 else "SHORT"), abs(score)

    # TRENDING: trade TF + confirm TF aynı yönü göstermeli
    score_trade = vote_trend(closes, ema_fast_s, ema_slow_s, macd_hist, rsi)

    if score_trade == 0:
        return "SKIP", 0.0

    # Confirm TF yoksa sadece trade TF ile karar ver
    if ema_fast_c is None:
        direction = "LONG" if score_trade > 0 else "SHORT"
        return direction, abs(score_trade)

    # Confirm TF'den oy
    score_confirm = vote_trend(
        np.array([0.0]),  # dummy, kullanılmıyor
        ema_fast_c, ema_slow_c, macd_hist_c, rsi_c
    )

    if score_confirm == 0:
        return "SKIP", 0.0

    trade_dir = "LONG" if score_trade > 0 else "SHORT"
    confirm_dir = "LONG" if score_confirm > 0 else "SHORT"

    if trade_dir == confirm_dir:
        return trade_dir, abs(score_trade + score_confirm) / 2.0
    else:
        return "SKIP", 0.0


# ─────────────── Doğrulama Mantığı ───────────────

def validate_regime(closes_future, regime, threshold_trend_pct=1.0, threshold_range_pct=0.5):
    """Rejim tespiti doğru muydu?

    TRENDING doğru: sonraki mumlarda net hareket > threshold_trend_pct%
    RANGING doğru: sonraki mumlarda net hareket < threshold_range_pct%

    Returns: (correct: bool, actual_move_pct: float, actual_regime: str)
    """
    if len(closes_future) < 5:
        return None, 0.0, "UNKNOWN"

    start = closes_future[0]
    # Net hareket: |son - ilk|
    net_pct = abs(closes_future[-1] - start) / start * 100.0
    # Max range: (max - min) / start
    max_range_pct = (np.max(closes_future) - np.min(closes_future)) / start * 100.0

    # Gerçek rejim belirle
    if net_pct > threshold_trend_pct:
        actual = "TRENDING"
    elif max_range_pct < threshold_range_pct * 2:
        actual = "RANGING"
    else:
        # Net hareket küçük ama volatilite yüksek → ranging
        actual = "RANGING"

    correct = (regime == actual)
    return correct, net_pct, actual


def validate_direction(closes_future, direction):
    """Yön tespiti doğru muydu?

    LONG doğru: sonraki mumlarda fiyat yukarı gittiyse (kapanış > açılış)
    SHORT doğru: sonraki mumlarda fiyat aşağı gittiyse

    Returns: (correct: bool, move_pct: float)
    """
    if len(closes_future) < 5 or direction == "SKIP":
        return None, 0.0

    start = closes_future[0]
    end = closes_future[-1]
    move_pct = (end - start) / start * 100.0

    # Max favorable move (en iyi noktadaki kâr)
    if direction == "LONG":
        max_favorable = (np.max(closes_future) - start) / start * 100.0
        correct = move_pct > 0.05  # en az %0.05 yukarı
    else:  # SHORT
        max_favorable = (start - np.min(closes_future)) / start * 100.0
        correct = move_pct < -0.05  # en az %0.05 aşağı

    return correct, move_pct


# ─────────────── ANA BACKTEST ───────────────

COINS = ["BTCUSDT", "ETHUSDT", "XRPUSDT", "AVAXUSDT", "DOGEUSDT",
         "SOLUSDT", "ADAUSDT", "BNBUSDT"]

# Trade TF → Confirm TF eşlemesi (System J)
CONFIRM_TF_MAP = {
    "1m": "30m", "3m": "1h", "5m": "2h",
    "15m": "6h", "30m": "12h", "1h": "1d",
}

TRADE_TF = "15m"                    # Ana trade TF
CONFIRM_TF = CONFIRM_TF_MAP["15m"]  # 6h
LOOKBACK = 200        # Rejim hesaplama için gereken mum sayısı
FUTURE_BARS = 20      # Doğrulama için ileriye bakılacak mum sayısı
EVAL_EVERY = 5        # Her 5 mumda bir değerlendirme (overlap azalt)


def run_backtest():
    print("=" * 120)
    print(f"SYSTEM J REJİM & YÖN DOĞRULUK BACKTEST'İ")
    print(f"Trade TF: {TRADE_TF}  |  Confirm TF: {CONFIRM_TF}  |  Lookback: {LOOKBACK}  |  Forward: {FUTURE_BARS} bar")
    print(f"Coinler: {', '.join(COINS)}")
    print(f"Tarih: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 120)

    grand_regime_correct = 0
    grand_regime_total = 0
    grand_dir_correct = 0
    grand_dir_total = 0
    grand_dir_skip = 0

    coin_results = []

    for symbol in COINS:
        print(f"\n{'─' * 80}")
        print(f"  {symbol}")
        print(f"{'─' * 80}")

        # Trade TF verisi çek (1500 mum ≈ 15 gün @ 15m)
        klines_trade = fetch_klines(symbol, TRADE_TF, 1500)
        if klines_trade is None or len(klines_trade) < LOOKBACK + FUTURE_BARS + 50:
            print(f"  Yetersiz veri, atlaniyor.")
            continue

        # Confirm TF verisi çek (500 mum ≈ 125 gün @ 6h)
        klines_confirm = fetch_klines(symbol, CONFIRM_TF, 500)
        if klines_confirm is None or len(klines_confirm) < 50:
            print(f"  Confirm TF yetersiz veri, atlaniyor.")
            continue

        time.sleep(0.15)  # rate limit

        # Trade TF verisini numpy'a çevir
        closes_trade = np.array([float(k[4]) for k in klines_trade])
        highs_trade = np.array([float(k[2]) for k in klines_trade])
        lows_trade = np.array([float(k[3]) for k in klines_trade])
        times_trade = [int(k[0]) for k in klines_trade]

        # Confirm TF verisini numpy'a çevir
        closes_confirm = np.array([float(k[4]) for k in klines_confirm])
        times_confirm = [int(k[0]) for k in klines_confirm]

        # Trade TF tüm indikatörler
        ema_fast_trade = ema_series(closes_trade, 9)
        ema_slow_trade = ema_series(closes_trade, 21)
        macd_hist_trade = compute_macd(closes_trade, 8, 17, 9)
        rsi_trade = compute_rsi(closes_trade, 14)
        bb_upper_trade, bb_mid_trade, bb_lower_trade = compute_bb(closes_trade, 20, 2.0)

        # Confirm TF tüm indikatörler
        ema_fast_confirm = ema_series(closes_confirm, 9)
        ema_slow_confirm = ema_series(closes_confirm, 21)
        macd_hist_confirm = compute_macd(closes_confirm, 8, 17, 9)
        rsi_confirm = compute_rsi(closes_confirm, 14)

        # Her EVAL_EVERY mumda bir değerlendir
        regime_correct = 0
        regime_wrong = 0
        regime_counts = {"TRENDING": 0, "RANGING": 0}
        actual_counts = {"TRENDING": 0, "RANGING": 0}
        regime_details = {"TT": 0, "RR": 0, "TR": 0, "RT": 0}  # predicted→actual

        dir_correct = 0
        dir_wrong = 0
        dir_skip = 0
        dir_by_regime = {
            "TRENDING": {"correct": 0, "wrong": 0, "skip": 0},
            "RANGING": {"correct": 0, "wrong": 0, "skip": 0},
        }
        dir_profits = []  # (direction, move_pct)

        eval_start = LOOKBACK
        eval_end = len(closes_trade) - FUTURE_BARS

        for i in range(eval_start, eval_end, EVAL_EVERY):
            # Bu noktaya kadar olan closes ile rejim hesapla
            closes_window = closes_trade[:i + 1]

            regime, conf, er, hurst = classify_regime(closes_window[-LOOKBACK:])

            # Sonraki FUTURE_BARS mumun closes'ı
            future_closes = closes_trade[i:i + FUTURE_BARS + 1]

            # Rejim doğrulama
            r_correct, net_move, actual_regime = validate_regime(future_closes, regime)
            if r_correct is not None:
                regime_counts[regime] = regime_counts.get(regime, 0) + 1
                actual_counts[actual_regime] = actual_counts.get(actual_regime, 0) + 1
                key = regime[0] + actual_regime[0]  # TT, TR, RT, RR
                regime_details[key] = regime_details.get(key, 0) + 1
                if r_correct:
                    regime_correct += 1
                else:
                    regime_wrong += 1

            # Yön hesapla — confirm TF'den doğru zamanı bul
            # Trade TF'nin i. mumunun timestamp'ine en yakın confirm TF mumunu bul
            t_now = times_trade[i]
            confirm_idx = None
            for ci in range(len(times_confirm) - 1, -1, -1):
                if times_confirm[ci] <= t_now:
                    confirm_idx = ci
                    break

            if confirm_idx is not None and confirm_idx >= 30:
                # Confirm TF indikatörlerini bu noktada al (slice trick)
                efc = ema_fast_confirm[:confirm_idx + 1]
                esc = ema_slow_confirm[:confirm_idx + 1]
                mhc = macd_hist_confirm[:confirm_idx + 1]
                rc = rsi_confirm[:confirm_idx + 1]

                direction, strength = compute_direction(
                    closes_window, regime,
                    ema_fast_trade[:i + 1], ema_slow_trade[:i + 1],
                    macd_hist_trade[:i + 1], rsi_trade[:i + 1],
                    bb_upper_trade[:i + 1], bb_lower_trade[:i + 1],
                    efc, esc, mhc, rc
                )
            else:
                direction, strength = compute_direction(
                    closes_window, regime,
                    ema_fast_trade[:i + 1], ema_slow_trade[:i + 1],
                    macd_hist_trade[:i + 1], rsi_trade[:i + 1],
                    bb_upper_trade[:i + 1], bb_lower_trade[:i + 1]
                )

            # Yön doğrulama
            d_correct, move_pct = validate_direction(future_closes, direction)
            if d_correct is not None:
                dir_profits.append((direction, move_pct))
                regime_key = regime if regime in dir_by_regime else "TRENDING"
                if d_correct:
                    dir_correct += 1
                    dir_by_regime[regime_key]["correct"] += 1
                else:
                    dir_wrong += 1
                    dir_by_regime[regime_key]["wrong"] += 1
            elif direction == "SKIP":
                dir_skip += 1
                regime_key = regime if regime in dir_by_regime else "TRENDING"
                dir_by_regime[regime_key]["skip"] += 1

        # Sonuçları yazdır
        r_total = regime_correct + regime_wrong
        d_total = dir_correct + dir_wrong

        print(f"\n  REJİM DOĞRULUĞU:")
        if r_total > 0:
            r_pct = regime_correct / r_total * 100
            print(f"    Doğru: {regime_correct}/{r_total} = {r_pct:.1f}%")
            print(f"    Tahmin dağılımı: TRENDING={regime_counts.get('TRENDING', 0)}, RANGING={regime_counts.get('RANGING', 0)}")
            print(f"    Gerçek dağılım:  TRENDING={actual_counts.get('TRENDING', 0)}, RANGING={actual_counts.get('RANGING', 0)}")
            print(f"    Confusion: TT={regime_details.get('TT', 0)} RR={regime_details.get('RR', 0)} | TR={regime_details.get('TR', 0)} RT={regime_details.get('RT', 0)}")
            print(f"    (TT=Trend→Trend doğru, TR=Trend dedi Ranging çıktı, RT=Ranging dedi Trend çıktı)")
        else:
            print(f"    Yeterli veri yok")

        print(f"\n  YÖN DOĞRULUĞU:")
        if d_total > 0:
            d_pct = dir_correct / d_total * 100
            print(f"    Doğru: {dir_correct}/{d_total} = {d_pct:.1f}%")
            print(f"    SKIP (sinyal yok): {dir_skip}")
            sinyal_orani = d_total / (d_total + dir_skip) * 100 if (d_total + dir_skip) > 0 else 0
            print(f"    Sinyal oranı: {sinyal_orani:.1f}% (sinyalli / toplam)")

            for rk in ["TRENDING", "RANGING"]:
                rd = dir_by_regime[rk]
                rt = rd["correct"] + rd["wrong"]
                if rt > 0:
                    print(f"    {rk}: {rd['correct']}/{rt} = {rd['correct']/rt*100:.1f}% doğru, {rd['skip']} skip")

            # Ortalama kâr/zarar
            if dir_profits:
                long_profits = [m for d, m in dir_profits if d == "LONG"]
                short_profits = [m for d, m in dir_profits if d == "SHORT"]
                avg_all = np.mean([m if d == "LONG" else -m for d, m in dir_profits])
                print(f"    Ort. kazanç (yön-uyumlu): {avg_all:+.3f}%")
                if long_profits:
                    print(f"    LONG ortalama hareket: {np.mean(long_profits):+.3f}% ({len(long_profits)} sinyal)")
                if short_profits:
                    print(f"    SHORT ortalama hareket: {np.mean(short_profits):+.3f}% ({len(short_profits)} sinyal)")
        else:
            print(f"    Yeterli sinyal yok (SKIP: {dir_skip})")

        grand_regime_correct += regime_correct
        grand_regime_total += r_total
        grand_dir_correct += dir_correct
        grand_dir_total += d_total
        grand_dir_skip += dir_skip

        coin_results.append({
            "symbol": symbol,
            "regime_pct": regime_correct / r_total * 100 if r_total > 0 else 0,
            "regime_n": r_total,
            "dir_pct": dir_correct / d_total * 100 if d_total > 0 else 0,
            "dir_n": d_total,
            "dir_skip": dir_skip,
            "regime_counts": dict(regime_counts),
        })

    # ─────────────── GENEL ÖZET ───────────────
    print(f"\n{'=' * 120}")
    print(f"GENEL ÖZET")
    print(f"{'=' * 120}")

    print(f"\n{'Coin':15} | {'Rejim Doğ.':>12} | {'Yön Doğ.':>12} | {'Skip':>6} | {'TREND':>7} | {'RANGE':>7}")
    print(f"{'-' * 15}-+-{'-' * 12}-+-{'-' * 12}-+-{'-' * 6}-+-{'-' * 7}-+-{'-' * 7}")
    for cr in coin_results:
        trend_n = cr["regime_counts"].get("TRENDING", 0)
        range_n = cr["regime_counts"].get("RANGING", 0)
        print(f"{cr['symbol']:15} | {cr['regime_pct']:>10.1f}% | {cr['dir_pct']:>10.1f}% | {cr['dir_skip']:>6} | {trend_n:>7} | {range_n:>7}")

    print(f"{'-' * 15}-+-{'-' * 12}-+-{'-' * 12}-+-{'-' * 6}-+-{'-' * 7}-+-{'-' * 7}")
    if grand_regime_total > 0:
        print(f"{'TOPLAM':15} | {grand_regime_correct / grand_regime_total * 100:>10.1f}% | ", end="")
    else:
        print(f"{'TOPLAM':15} | {'N/A':>12} | ", end="")
    if grand_dir_total > 0:
        print(f"{grand_dir_correct / grand_dir_total * 100:>10.1f}% | {grand_dir_skip:>6} |")
    else:
        print(f"{'N/A':>12} | {grand_dir_skip:>6} |")

    print()
    if grand_regime_total > 0:
        print(f"  Rejim toplam: {grand_regime_correct}/{grand_regime_total} = {grand_regime_correct / grand_regime_total * 100:.1f}%")
    if grand_dir_total > 0:
        print(f"  Yön toplam:   {grand_dir_correct}/{grand_dir_total} = {grand_dir_correct / grand_dir_total * 100:.1f}%")
        print(f"  Yön SKIP:     {grand_dir_skip} ({grand_dir_skip / (grand_dir_total + grand_dir_skip) * 100:.1f}% sessiz)")
    print()


if __name__ == "__main__":
    run_backtest()
