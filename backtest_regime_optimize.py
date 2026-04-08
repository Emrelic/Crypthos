"""Rejim tespiti optimizasyonu — grid search ile en iyi yontem ve esikleri bul.

Test edilen yontemler:
  1. ER-only (farkli esikler)
  2. ADX-only
  3. BB Width
  4. ER + Hurst (mevcut System J)
  5. ER + ADX oylama
  6. ER + Hurst + ADX + BB Width (4lu oylama)
  7. Farkli ER window boyutlari
"""
import os
import sys
import time
import hmac
import hashlib
import requests
import numpy as np
from urllib.parse import urlencode
from datetime import datetime
from collections import defaultdict
from dotenv import load_dotenv

load_dotenv()

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
    url = f"{BASE}/fapi/v1/klines"
    params = {"symbol": symbol, "interval": interval, "limit": limit}
    resp = session.get(url, params=params)
    data = resp.json()
    if not isinstance(data, list):
        return None
    return data


# ============================================================
# INDIKATOR FONKSIYONLARI
# ============================================================

def compute_efficiency_ratio(closes):
    if len(closes) < 2:
        return 0.5
    net = abs(closes[-1] - closes[0])
    total = np.sum(np.abs(np.diff(closes)))
    return net / total if total > 0 else 0.0


def compute_rolling_er(closes, window=20, median_count=10):
    if len(closes) < window + median_count:
        return compute_efficiency_ratio(closes[-window:] if len(closes) >= window else closes)
    ers = []
    for i in range(len(closes) - window + 1):
        seg = closes[i:i + window]
        net = abs(seg[-1] - seg[0])
        total = np.sum(np.abs(np.diff(seg)))
        ers.append(net / total if total > 0 else 0.0)
    return float(np.median(ers[-median_count:]))


def compute_hurst(closes):
    if len(closes) < 128:
        return 0.5
    log_ret = np.diff(np.log(closes))
    ns = [n for n in [16, 32, 64, 128] if n <= len(log_ret)]
    if len(ns) < 2:
        return 0.5
    rs_vals = []
    for n in ns:
        rs_list = []
        for i in range(len(log_ret) // n):
            chunk = log_ret[i * n:(i + 1) * n]
            dev = np.cumsum(chunk - np.mean(chunk))
            R = np.max(dev) - np.min(dev)
            S = np.std(chunk, ddof=1)
            if S > 0:
                rs_list.append(R / S)
        if rs_list:
            rs_vals.append((np.log(n), np.log(np.mean(rs_list))))
    if len(rs_vals) < 2:
        return 0.5
    x = np.array([v[0] for v in rs_vals])
    y = np.array([v[1] for v in rs_vals])
    n_pts = len(x)
    H = (n_pts * np.sum(x * y) - np.sum(x) * np.sum(y)) / \
        (n_pts * np.sum(x ** 2) - np.sum(x) ** 2)
    return float(np.clip(H, 0.0, 1.0))


def compute_adx(highs, lows, closes, period=14):
    """ADX serisi hesapla. Returns array."""
    n = len(closes)
    if n < period * 2:
        return np.full(n, 25.0)

    adx = np.full(n, 25.0)
    tr = np.zeros(n)
    plus_dm = np.zeros(n)
    minus_dm = np.zeros(n)

    for i in range(1, n):
        h_diff = highs[i] - highs[i - 1]
        l_diff = lows[i - 1] - lows[i]
        tr[i] = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
        plus_dm[i] = h_diff if (h_diff > l_diff and h_diff > 0) else 0
        minus_dm[i] = l_diff if (l_diff > h_diff and l_diff > 0) else 0

    # Smoothed
    atr = np.zeros(n)
    plus_di = np.zeros(n)
    minus_di = np.zeros(n)
    dx = np.zeros(n)

    atr[period] = np.sum(tr[1:period + 1])
    s_plus = np.sum(plus_dm[1:period + 1])
    s_minus = np.sum(minus_dm[1:period + 1])

    for i in range(period + 1, n):
        atr[i] = atr[i - 1] - atr[i - 1] / period + tr[i]
        s_plus = s_plus - s_plus / period + plus_dm[i]
        s_minus = s_minus - s_minus / period + minus_dm[i]

        if atr[i] > 0:
            plus_di[i] = 100 * s_plus / atr[i]
            minus_di[i] = 100 * s_minus / atr[i]

        di_sum = plus_di[i] + minus_di[i]
        if di_sum > 0:
            dx[i] = 100 * abs(plus_di[i] - minus_di[i]) / di_sum

    # ADX smoothing
    adx[period * 2] = np.mean(dx[period + 1:period * 2 + 1])
    for i in range(period * 2 + 1, n):
        adx[i] = (adx[i - 1] * (period - 1) + dx[i]) / period

    return adx


def compute_bb_width(closes, period=20):
    """BB width serisi (normalize: width / middle)."""
    n = len(closes)
    width = np.zeros(n)
    for i in range(period - 1, n):
        window = closes[i - period + 1:i + 1]
        m = np.mean(window)
        s = np.std(window, ddof=1)
        if m > 0:
            width[i] = (4.0 * s) / m  # 2*std*2 / middle = relative width
    return width


def compute_bb_width_expanding(bb_width, lookback=10):
    """BB width genisliyorsa True. Son `lookback` mumda width artiyor mu."""
    n = len(bb_width)
    expanding = np.zeros(n, dtype=bool)
    for i in range(lookback, n):
        # Son lookback mumda width artisi
        recent = bb_width[i - lookback + 1:i + 1]
        if len(recent) >= 2 and recent[-1] > np.mean(recent[:-1]) * 1.05:
            expanding[i] = True
    return expanding


def compute_atr(highs, lows, closes, period=14):
    """ATR serisi."""
    n = len(closes)
    atr = np.zeros(n)
    tr = np.zeros(n)
    for i in range(1, n):
        tr[i] = max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1]))
    if n > period:
        atr[period] = np.mean(tr[1:period+1])
        for i in range(period+1, n):
            atr[i] = (atr[i-1] * (period-1) + tr[i]) / period
    return atr


def compute_volume_ratio(volumes, period=20):
    """Son mum hacmi / ortalama hacim."""
    n = len(volumes)
    ratio = np.ones(n)
    for i in range(period, n):
        avg = np.mean(volumes[i-period:i])
        if avg > 0:
            ratio[i] = volumes[i] / avg
    return ratio


# ============================================================
# GERCEK REJIM (ground truth) BELIRLEME
# ============================================================

def compute_actual_regime(closes_future, trend_threshold_pct=1.0):
    """Sonraki N mumdaki gercek rejimi belirle.

    TRENDING: net hareket > threshold
    RANGING: net hareket <= threshold
    """
    if len(closes_future) < 5:
        return "UNKNOWN", 0.0
    start = closes_future[0]
    net_pct = abs(closes_future[-1] - start) / start * 100.0
    if net_pct > trend_threshold_pct:
        return "TRENDING", net_pct
    return "RANGING", net_pct


# ============================================================
# REJIM TESPIT YONTEMLERI
# ============================================================

def method_er_only(closes, er_trending, er_ranging, er_window=20, er_median_n=10, **kw):
    """Sadece ER."""
    er = compute_rolling_er(closes, er_window, er_median_n)
    if er > er_trending:
        return "TRENDING"
    elif er < er_ranging:
        return "RANGING"
    return "RANGING"  # gray zone -> ranging default


def method_er_hurst_sysj(closes, er_trending, er_ranging, er_window=20, er_median_n=10,
                          hurst_trending=0.55, hurst_ranging=0.45, **kw):
    """Mevcut System J: ER + Hurst gray zone."""
    er = compute_rolling_er(closes, er_window, er_median_n)
    if er > er_trending:
        return "TRENDING"
    if er < er_ranging:
        return "RANGING"
    hurst = compute_hurst(closes)
    mid = (er_trending + er_ranging) / 2.0
    if hurst > hurst_trending:
        return "TRENDING"
    elif hurst < hurst_ranging:
        return "RANGING"
    elif er > mid:
        return "TRENDING"
    return "RANGING"


def method_adx_only(closes, adx_arr, idx, adx_trend=25, adx_range=20, **kw):
    """Sadece ADX."""
    adx = adx_arr[idx]
    if adx > adx_trend:
        return "TRENDING"
    elif adx < adx_range:
        return "RANGING"
    return "RANGING"  # ortasi -> ranging default


def method_bbw_only(closes, bbw_arr, bbw_expanding_arr, idx,
                    bbw_trend=0.04, bbw_range=0.02, **kw):
    """BB Width bazli."""
    bbw = bbw_arr[idx]
    if bbw > bbw_trend and bbw_expanding_arr[idx]:
        return "TRENDING"
    elif bbw < bbw_range:
        return "RANGING"
    return "RANGING"


def method_er_adx_vote(closes, adx_arr, idx, er_trending, er_ranging,
                        adx_trend=25, adx_range=20, er_window=20, er_median_n=10, **kw):
    """ER + ADX oylama (2/2 veya 1 taraf net)."""
    er = compute_rolling_er(closes, er_window, er_median_n)
    adx = adx_arr[idx]

    votes_trend = 0
    votes_range = 0

    if er > er_trending:
        votes_trend += 1
    elif er < er_ranging:
        votes_range += 1

    if adx > adx_trend:
        votes_trend += 1
    elif adx < adx_range:
        votes_range += 1

    if votes_trend > votes_range:
        return "TRENDING"
    elif votes_range > votes_trend:
        return "RANGING"
    return "RANGING"


def method_4way_vote(closes, adx_arr, bbw_arr, bbw_expanding_arr, idx,
                     er_trending, er_ranging, er_window=20, er_median_n=10,
                     hurst_trending=0.55, hurst_ranging=0.45,
                     adx_trend=25, adx_range=20,
                     bbw_trend=0.04, bbw_range=0.02, **kw):
    """4lu oylama: ER + Hurst + ADX + BB Width. >=3 -> karar, 2-2 -> RANGING."""
    er = compute_rolling_er(closes, er_window, er_median_n)
    hurst = compute_hurst(closes)
    adx = adx_arr[idx]
    bbw = bbw_arr[idx]

    votes_trend = 0
    votes_range = 0

    # ER
    if er > er_trending:
        votes_trend += 1
    elif er < er_ranging:
        votes_range += 1

    # Hurst
    if hurst > hurst_trending:
        votes_trend += 1
    elif hurst < hurst_ranging:
        votes_range += 1

    # ADX
    if adx > adx_trend:
        votes_trend += 1
    elif adx < adx_range:
        votes_range += 1

    # BB Width
    if bbw > bbw_trend and bbw_expanding_arr[idx]:
        votes_trend += 1
    elif bbw < bbw_range:
        votes_range += 1

    if votes_trend >= 3:
        return "TRENDING"
    elif votes_range >= 3:
        return "RANGING"
    elif votes_trend > votes_range:
        return "TRENDING"
    return "RANGING"


def method_3way_vote(closes, adx_arr, bbw_arr, bbw_expanding_arr, idx,
                     er_trending, er_ranging, er_window=20, er_median_n=10,
                     adx_trend=25, adx_range=20,
                     bbw_trend=0.04, bbw_range=0.02, **kw):
    """3lu oylama: ER + ADX + BB Width. >=2 -> karar."""
    er = compute_rolling_er(closes, er_window, er_median_n)
    adx = adx_arr[idx]
    bbw = bbw_arr[idx]

    votes_trend = 0
    votes_range = 0

    if er > er_trending:
        votes_trend += 1
    elif er < er_ranging:
        votes_range += 1

    if adx > adx_trend:
        votes_trend += 1
    elif adx < adx_range:
        votes_range += 1

    if bbw > bbw_trend and bbw_expanding_arr[idx]:
        votes_trend += 1
    elif bbw < bbw_range:
        votes_range += 1

    if votes_trend >= 2:
        return "TRENDING"
    elif votes_range >= 2:
        return "RANGING"
    return "RANGING"


def method_er_adx_strict(closes, adx_arr, idx, er_trending, er_ranging,
                          adx_trend=25, adx_range=20, er_window=20, er_median_n=10, **kw):
    """ER + ADX strict: IKISI de trend demeli, yoksa RANGING."""
    er = compute_rolling_er(closes, er_window, er_median_n)
    adx = adx_arr[idx]

    if er > er_trending and adx > adx_trend:
        return "TRENDING"
    return "RANGING"


# ============================================================
# ANA TEST
# ============================================================

COINS = ["BTCUSDT", "ETHUSDT", "XRPUSDT", "AVAXUSDT", "DOGEUSDT",
         "SOLUSDT", "ADAUSDT", "BNBUSDT"]

TRADE_TF = "15m"
LOOKBACK = 200
FUTURE_BARS = 20
EVAL_EVERY = 5
TREND_THRESHOLD = 1.0  # gercek rejim icin: >%1 net hareket = TRENDING


def evaluate_method(method_name, method_fn, all_coin_data, params):
    """Bir yontemi tum coinler uzerinde test et."""
    total_correct = 0
    total_count = 0
    total_trend_pred = 0
    total_range_pred = 0
    total_trend_actual = 0
    total_range_actual = 0
    confusion = {"TT": 0, "RR": 0, "TR": 0, "RT": 0}

    for symbol, cdata in all_coin_data.items():
        closes = cdata["closes"]
        highs = cdata["highs"]
        lows = cdata["lows"]
        adx_arr = cdata["adx"]
        bbw_arr = cdata["bbw"]
        bbw_exp = cdata["bbw_expanding"]

        eval_start = LOOKBACK
        eval_end = len(closes) - FUTURE_BARS

        for i in range(eval_start, eval_end, EVAL_EVERY):
            c_window = closes[:i + 1]

            # Yonteme gore cagir
            if "adx" in method_name or "4way" in method_name or "3way" in method_name:
                predicted = method_fn(c_window[-LOOKBACK:], adx_arr=adx_arr,
                                       bbw_arr=bbw_arr, bbw_expanding_arr=bbw_exp,
                                       idx=i, **params)
            else:
                predicted = method_fn(c_window[-LOOKBACK:], **params)

            # Gercek rejim
            future = closes[i:i + FUTURE_BARS + 1]
            actual, _ = compute_actual_regime(future, TREND_THRESHOLD)
            if actual == "UNKNOWN":
                continue

            total_count += 1
            if predicted == "TRENDING":
                total_trend_pred += 1
            else:
                total_range_pred += 1
            if actual == "TRENDING":
                total_trend_actual += 1
            else:
                total_range_actual += 1

            key = predicted[0] + actual[0]
            confusion[key] = confusion.get(key, 0) + 1

            if predicted == actual:
                total_correct += 1

    accuracy = total_correct / total_count * 100 if total_count > 0 else 0
    # Precision/Recall
    trend_precision = confusion["TT"] / (confusion["TT"] + confusion["TR"]) * 100 if (confusion["TT"] + confusion["TR"]) > 0 else 0
    trend_recall = confusion["TT"] / (confusion["TT"] + confusion["RT"]) * 100 if (confusion["TT"] + confusion["RT"]) > 0 else 0
    range_precision = confusion["RR"] / (confusion["RR"] + confusion["RT"]) * 100 if (confusion["RR"] + confusion["RT"]) > 0 else 0
    range_recall = confusion["RR"] / (confusion["RR"] + confusion["TR"]) * 100 if (confusion["RR"] + confusion["TR"]) > 0 else 0

    # F1 score
    trend_f1 = 2 * trend_precision * trend_recall / (trend_precision + trend_recall) if (trend_precision + trend_recall) > 0 else 0
    range_f1 = 2 * range_precision * range_recall / (range_precision + range_recall) if (range_precision + range_recall) > 0 else 0

    return {
        "accuracy": accuracy,
        "total": total_count,
        "trend_pred": total_trend_pred,
        "range_pred": total_range_pred,
        "trend_actual": total_trend_actual,
        "range_actual": total_range_actual,
        "confusion": confusion,
        "trend_precision": trend_precision,
        "trend_recall": trend_recall,
        "range_precision": range_precision,
        "range_recall": range_recall,
        "trend_f1": trend_f1,
        "range_f1": range_f1,
        "balanced_acc": (trend_recall + range_recall) / 2.0,  # ortalama recall
    }


def main():
    print("=" * 130)
    print("REJIM TESPITI OPTIMIZASYONU — Grid Search")
    print(f"TF: {TRADE_TF} | Lookback: {LOOKBACK} | Forward: {FUTURE_BARS} bar | Trend esigi: >{TREND_THRESHOLD}%")
    print(f"Coinler: {', '.join(COINS)}")
    print("=" * 130)

    # Veri cek
    all_coin_data = {}
    for symbol in COINS:
        print(f"  {symbol} verisi cekiliyor...", end=" ", flush=True)
        klines = fetch_klines(symbol, TRADE_TF, 1500)
        if klines is None or len(klines) < LOOKBACK + FUTURE_BARS + 50:
            print("YETERSIZ")
            continue

        closes = np.array([float(k[4]) for k in klines])
        highs = np.array([float(k[2]) for k in klines])
        lows = np.array([float(k[3]) for k in klines])
        volumes = np.array([float(k[5]) for k in klines])

        adx = compute_adx(highs, lows, closes, 14)
        bbw = compute_bb_width(closes, 20)
        bbw_exp = compute_bb_width_expanding(bbw, 10)

        all_coin_data[symbol] = {
            "closes": closes, "highs": highs, "lows": lows,
            "volumes": volumes, "adx": adx, "bbw": bbw, "bbw_expanding": bbw_exp,
        }
        print(f"OK ({len(closes)} mum)")
        time.sleep(0.1)

    if not all_coin_data:
        print("Hic veri yok!")
        return

    # Gercek rejim dagilimi
    total_actual_t = 0
    total_actual_r = 0
    for sym, cd in all_coin_data.items():
        for i in range(LOOKBACK, len(cd["closes"]) - FUTURE_BARS, EVAL_EVERY):
            future = cd["closes"][i:i + FUTURE_BARS + 1]
            actual, _ = compute_actual_regime(future, TREND_THRESHOLD)
            if actual == "TRENDING":
                total_actual_t += 1
            elif actual == "RANGING":
                total_actual_r += 1
    print(f"\nGercek rejim dagilimi: TRENDING={total_actual_t} ({total_actual_t/(total_actual_t+total_actual_r)*100:.1f}%), "
          f"RANGING={total_actual_r} ({total_actual_r/(total_actual_t+total_actual_r)*100:.1f}%)")

    results = []

    # ── 1. ER-only grid search ──
    print(f"\n{'='*130}")
    print("1. ER-ONLY GRID SEARCH")
    print(f"{'='*130}")

    for er_window in [10, 20, 30, 50]:
        for er_t in [0.20, 0.25, 0.30, 0.35, 0.40, 0.45, 0.50]:
            for er_r in [0.05, 0.08, 0.10, 0.15, 0.20]:
                if er_r >= er_t:
                    continue
                params = {"er_trending": er_t, "er_ranging": er_r,
                          "er_window": er_window, "er_median_n": 10}
                r = evaluate_method("er_only", method_er_only, all_coin_data, params)
                results.append(("ER-only", f"w={er_window} t={er_t} r={er_r}", r, params))

    # ── 2. ADX-only grid search ──
    print("2. ADX-ONLY GRID SEARCH")

    for adx_t in [20, 22, 25, 28, 30, 35]:
        for adx_r in [15, 18, 20, 22]:
            if adx_r >= adx_t:
                continue
            params = {"adx_trend": adx_t, "adx_range": adx_r}
            r = evaluate_method("adx_only", method_adx_only, all_coin_data, params)
            results.append(("ADX-only", f"t={adx_t} r={adx_r}", r, params))

    # ── 3. System J mevcut (ER + Hurst) ──
    print("3. ER+HURST (SYSTEM J) GRID SEARCH")

    for er_window in [10, 20, 30, 50]:
        for er_t in [0.25, 0.30, 0.35, 0.40, 0.45, 0.50]:
            for er_r in [0.05, 0.08, 0.10, 0.15, 0.20]:
                if er_r >= er_t:
                    continue
                for h_t in [0.55, 0.60, 0.65]:
                    for h_r in [0.40, 0.45, 0.50]:
                        if h_r >= h_t:
                            continue
                        params = {"er_trending": er_t, "er_ranging": er_r,
                                  "er_window": er_window, "er_median_n": 10,
                                  "hurst_trending": h_t, "hurst_ranging": h_r}
                        r = evaluate_method("er_hurst", method_er_hurst_sysj, all_coin_data, params)
                        results.append(("ER+Hurst", f"w={er_window} er={er_t}/{er_r} h={h_t}/{h_r}", r, params))

    # ── 4. ER + ADX strict ──
    print("4. ER+ADX STRICT GRID SEARCH")

    for er_t in [0.25, 0.30, 0.35, 0.40]:
        for er_r in [0.08, 0.10, 0.15]:
            if er_r >= er_t:
                continue
            for adx_t in [20, 22, 25, 28]:
                for adx_r in [15, 18, 20]:
                    if adx_r >= adx_t:
                        continue
                    params = {"er_trending": er_t, "er_ranging": er_r,
                              "er_window": 20, "er_median_n": 10,
                              "adx_trend": adx_t, "adx_range": adx_r}
                    r = evaluate_method("er_adx_strict", method_er_adx_strict, all_coin_data, params)
                    results.append(("ER+ADX strict", f"er={er_t}/{er_r} adx={adx_t}/{adx_r}", r, params))

    # ── 5. ER + ADX vote ──
    print("5. ER+ADX VOTE GRID SEARCH")

    for er_t in [0.25, 0.30, 0.35, 0.40]:
        for er_r in [0.08, 0.10, 0.15]:
            if er_r >= er_t:
                continue
            for adx_t in [22, 25, 28]:
                for adx_r in [15, 18, 20]:
                    if adx_r >= adx_t:
                        continue
                    params = {"er_trending": er_t, "er_ranging": er_r,
                              "er_window": 20, "er_median_n": 10,
                              "adx_trend": adx_t, "adx_range": adx_r}
                    r = evaluate_method("er_adx_vote", method_er_adx_vote, all_coin_data, params)
                    results.append(("ER+ADX vote", f"er={er_t}/{er_r} adx={adx_t}/{adx_r}", r, params))

    # ── 6. 3-way vote (ER + ADX + BBW) ──
    print("6. 3-WAY VOTE (ER+ADX+BBW) GRID SEARCH")

    for er_t in [0.25, 0.30, 0.35, 0.40]:
        for er_r in [0.08, 0.10, 0.15]:
            if er_r >= er_t:
                continue
            for adx_t in [22, 25, 28]:
                for adx_r in [15, 18, 20]:
                    if adx_r >= adx_t:
                        continue
                    for bbw_t in [0.03, 0.04, 0.05]:
                        for bbw_r in [0.015, 0.02, 0.025]:
                            params = {"er_trending": er_t, "er_ranging": er_r,
                                      "er_window": 20, "er_median_n": 10,
                                      "adx_trend": adx_t, "adx_range": adx_r,
                                      "bbw_trend": bbw_t, "bbw_range": bbw_r}
                            r = evaluate_method("3way_vote", method_3way_vote, all_coin_data, params)
                            results.append(("3-way vote", f"er={er_t}/{er_r} adx={adx_t}/{adx_r} bbw={bbw_t}/{bbw_r}", r, params))

    # ── 7. 4-way vote (ER + Hurst + ADX + BBW) ──
    print("7. 4-WAY VOTE (ER+Hurst+ADX+BBW) SEARCH (sampled)")

    for er_t in [0.30, 0.35, 0.40]:
        for er_r in [0.10, 0.15]:
            for adx_t in [22, 25, 28]:
                for adx_r in [18, 20]:
                    if adx_r >= adx_t:
                        continue
                    params = {"er_trending": er_t, "er_ranging": er_r,
                              "er_window": 20, "er_median_n": 10,
                              "hurst_trending": 0.55, "hurst_ranging": 0.45,
                              "adx_trend": adx_t, "adx_range": adx_r,
                              "bbw_trend": 0.04, "bbw_range": 0.02}
                    r = evaluate_method("4way_vote", method_4way_vote, all_coin_data, params)
                    results.append(("4-way vote", f"er={er_t}/{er_r} adx={adx_t}/{adx_r}", r, params))

    # ============================================================
    # SONUCLARI SIRALA
    # ============================================================
    print(f"\n{'='*130}")
    print("SONUCLAR — BALANCED ACCURACY'ye GORE TOP 30")
    print(f"{'='*130}")
    print(f"  (Balanced Acc = (Trend Recall + Range Recall) / 2 — dengesiz veri icin en adil metrik)")
    print()
    print(f"{'#':>3} | {'Yontem':16} | {'Parametreler':50} | {'Acc':>6} | {'BalAcc':>6} | {'T-Prec':>6} | {'T-Rec':>6} | {'R-Prec':>6} | {'R-Rec':>6} | {'T-F1':>5} | {'R-F1':>5} | {'T-Pred':>6} | {'R-Pred':>6}")
    print("-" * 170)

    results.sort(key=lambda x: x[2]["balanced_acc"], reverse=True)

    for rank, (method, desc, r, params) in enumerate(results[:30], 1):
        print(f"{rank:>3} | {method:16} | {desc:50} | {r['accuracy']:>5.1f}% | {r['balanced_acc']:>5.1f}% | "
              f"{r['trend_precision']:>5.1f}% | {r['trend_recall']:>5.1f}% | "
              f"{r['range_precision']:>5.1f}% | {r['range_recall']:>5.1f}% | "
              f"{r['trend_f1']:>5.1f} | {r['range_f1']:>5.1f} | "
              f"{r['trend_pred']:>6} | {r['range_pred']:>6}")

    # En iyi sonuc detay
    print(f"\n{'='*130}")
    print("EN IYI SONUC DETAY")
    print(f"{'='*130}")
    best = results[0]
    r = best[2]
    print(f"  Yontem: {best[0]}")
    print(f"  Parametreler: {best[1]}")
    print(f"  Accuracy: {r['accuracy']:.1f}%")
    print(f"  Balanced Accuracy: {r['balanced_acc']:.1f}%")
    print(f"  Confusion Matrix:")
    print(f"                  Gercek TREND  Gercek RANGE")
    print(f"    Tahmin TREND:  {r['confusion']['TT']:>8}      {r['confusion']['TR']:>8}")
    print(f"    Tahmin RANGE:  {r['confusion']['RT']:>8}      {r['confusion']['RR']:>8}")
    print(f"  Trend Precision: {r['trend_precision']:.1f}%  Recall: {r['trend_recall']:.1f}%  F1: {r['trend_f1']:.1f}")
    print(f"  Range Precision: {r['range_precision']:.1f}%  Recall: {r['range_recall']:.1f}%  F1: {r['range_f1']:.1f}")
    print(f"  Toplam degerledirme: {r['total']}")
    print()

    # Mevcut System J sonucu
    print(f"{'='*130}")
    print("MEVCUT SYSTEM J KARSILASTIRMA")
    print(f"{'='*130}")
    sysj_params = {"er_trending": 0.25, "er_ranging": 0.08, "er_window": 20,
                   "er_median_n": 10, "hurst_trending": 0.55, "hurst_ranging": 0.45}
    sysj = evaluate_method("er_hurst", method_er_hurst_sysj, all_coin_data, sysj_params)
    print(f"  Accuracy: {sysj['accuracy']:.1f}%")
    print(f"  Balanced Accuracy: {sysj['balanced_acc']:.1f}%")
    print(f"  Trend Precision: {sysj['trend_precision']:.1f}%  Recall: {sysj['trend_recall']:.1f}%")
    print(f"  Range Precision: {sysj['range_precision']:.1f}%  Recall: {sysj['range_recall']:.1f}%")
    print(f"  Confusion: TT={sysj['confusion']['TT']} TR={sysj['confusion']['TR']} RT={sysj['confusion']['RT']} RR={sysj['confusion']['RR']}")
    print(f"  TREND tahmin: {sysj['trend_pred']}  RANGE tahmin: {sysj['range_pred']}")
    print()

    # Iyilestirme yuzdesi
    improvement = best[2]["balanced_acc"] - sysj["balanced_acc"]
    print(f"  EN IYI vs MEVCUT: {improvement:+.1f}% balanced accuracy iyilestirme")
    print()


if __name__ == "__main__":
    main()
