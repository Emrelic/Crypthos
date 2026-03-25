"""System F — BTC TF Alignment Backtest.

Son 7 gun icin 5m aralıklarla kontrol:
- Her noktada 5 TF'nin (5m, 15m, 1h, 4h, 1d) 3/3 indikator uyumunu hesapla
- 5/5 vs 4/5 vs 3/5 uyum sayisini raporla
- Gunde ortalama kac kez oluyor
"""
import requests
import numpy as np
import time
from datetime import datetime, timedelta, timezone

# ─── Config (System F ayarlari ile ayni) ───
EMA_FAST = 9
EMA_SLOW = 21
EMA_GAP_MIN_PCT = 0.05
MACD_FAST = 8
MACD_SLOW = 17
MACD_SIGNAL = 9
MACD_MOMENTUM_REQUIRED = True
RSI_PERIOD = 14
RSI_LONG = 60
RSI_SHORT = 40
LOOKBACK_CANDLES = 200   # her TF icin geriye cekilecek mum

TFS = [
    ("5m", 5),
    ("15m", 15),
    ("1h", 60),
    ("4h", 240),
    ("1d", 1440),
]

SYMBOL = "BTCUSDT"
DAYS_BACK = 7
CHECK_INTERVAL_MINUTES = 5  # her 5 dakikada bir kontrol


# ─── Indicator Functions (System F ile ayni) ───

def ema(data, period):
    if len(data) < period:
        return float(np.mean(data)) if len(data) > 0 else 0.0
    k = 2.0 / (period + 1)
    e = float(data[0])
    for val in data[1:]:
        e = val * k + e * (1 - k)
    return e


def ema_series(data, period):
    if len(data) == 0:
        return np.array([0.0])
    k = 2.0 / (period + 1)
    result = np.empty(len(data))
    result[0] = float(data[0])
    for i in range(1, len(data)):
        result[i] = float(data[i]) * k + result[i - 1] * (1 - k)
    return result


def macd_line_series(closes, fast, slow):
    if len(closes) < slow:
        return np.array([0.0])
    k_fast = 2.0 / (fast + 1)
    k_slow = 2.0 / (slow + 1)
    ema_f = float(closes[0])
    ema_s = float(closes[0])
    series = []
    for val in closes:
        ema_f = val * k_fast + ema_f * (1 - k_fast)
        ema_s = val * k_slow + ema_s * (1 - k_slow)
        series.append(ema_f - ema_s)
    return np.array(series)


def rsi(closes, period=14):
    if len(closes) < period + 1:
        return 50.0
    deltas = np.diff(closes)
    gains = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)
    avg_gain = np.mean(gains[:period])
    avg_loss = np.mean(losses[:period])
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def analyze_tf_direction(closes):
    """Tek TF icin 3/3 yon analizi. Returns: 'LONG', 'SHORT', or 'FLAT'."""
    if len(closes) < 30:
        return "FLAT"

    price = float(closes[-1])
    if price <= 0:
        return "FLAT"

    # EMA
    ema_f = ema(closes, EMA_FAST)
    ema_s = ema(closes, EMA_SLOW)
    gap_pct = (ema_f - ema_s) / price * 100

    if gap_pct > EMA_GAP_MIN_PCT:
        ema_vote = 1
    elif gap_pct < -EMA_GAP_MIN_PCT:
        ema_vote = -1
    else:
        ema_vote = 0

    # MACD
    macd_s = macd_line_series(closes, MACD_FAST, MACD_SLOW)
    signal_s = ema_series(macd_s, MACD_SIGNAL)
    hist_s = macd_s - signal_s

    macd_vote = 0
    if len(hist_s) >= 3:
        h1, h2, h3 = float(hist_s[-3]), float(hist_s[-2]), float(hist_s[-1])
        if MACD_MOMENTUM_REQUIRED:
            if h3 > 0 and h1 < h2 < h3:
                macd_vote = 1
            elif h3 < 0 and h1 > h2 > h3:
                macd_vote = -1
        else:
            if h3 > 0:
                macd_vote = 1
            elif h3 < 0:
                macd_vote = -1

    # RSI
    r = rsi(closes, RSI_PERIOD)
    if r > RSI_LONG:
        rsi_vote = 1
    elif r < RSI_SHORT:
        rsi_vote = -1
    else:
        rsi_vote = 0

    # 3/3
    if ema_vote > 0 and macd_vote > 0 and rsi_vote > 0:
        return "LONG"
    elif ema_vote < 0 and macd_vote < 0 and rsi_vote < 0:
        return "SHORT"
    return "FLAT"


# ─── Binance Data Fetch ───

def fetch_klines(symbol, interval, limit=500, end_time=None):
    """Binance'den kline cek."""
    url = "https://fapi.binance.com/fapi/v1/klines"
    params = {"symbol": symbol, "interval": interval, "limit": limit}
    if end_time:
        params["endTime"] = int(end_time)
    resp = requests.get(url, params=params, timeout=15)
    resp.raise_for_status()
    return resp.json()


def fetch_klines_range(symbol, interval, start_ms, end_ms, limit_per_req=1000):
    """Belirli zaman araligindan kline cek (multiple requests)."""
    all_klines = []
    cursor = start_ms
    while cursor < end_ms:
        params = {
            "symbol": symbol,
            "interval": interval,
            "startTime": int(cursor),
            "endTime": int(end_ms),
            "limit": limit_per_req,
        }
        resp = requests.get("https://fapi.binance.com/fapi/v1/klines",
                            params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        if not data:
            break
        all_klines.extend(data)
        last_open = int(data[-1][0])
        cursor = last_open + 1
        if len(data) < limit_per_req:
            break
        time.sleep(0.2)
    return all_klines


# ─── Main Backtest ───

def run_backtest():
    now = datetime.now(timezone.utc)
    end_ms = int(now.timestamp() * 1000)
    start_ms = int((now - timedelta(days=DAYS_BACK)).timestamp() * 1000)

    print(f"=== System F TF Alignment Backtest — {SYMBOL} ===")
    print(f"Period: {DAYS_BACK} days ({now - timedelta(days=DAYS_BACK):%Y-%m-%d} -> {now:%Y-%m-%d})")
    print(f"Check interval: every {CHECK_INTERVAL_MINUTES} min")
    print()

    # 1. Her TF icin tum klines'i cek
    print("Fetching klines...")
    tf_klines = {}
    for tf_name, tf_min in TFS:
        print(f"  {tf_name}...", end=" ", flush=True)
        # Lookback: LOOKBACK_CANDLES ekstra mum (indicator warmup)
        warmup_ms = LOOKBACK_CANDLES * tf_min * 60 * 1000
        fetch_start = start_ms - warmup_ms
        klines = fetch_klines_range(SYMBOL, tf_name, fetch_start, end_ms)
        tf_klines[tf_name] = klines
        print(f"{len(klines)} candles")
        time.sleep(0.3)

    # 2. 5m aralıklarla rolling window
    check_interval_ms = CHECK_INTERVAL_MINUTES * 60 * 1000

    # Her TF'nin mum baslangiçlarini timestamp olarak al
    tf_timestamps = {}
    tf_closes_by_time = {}
    for tf_name, klines in tf_klines.items():
        ts_list = []
        for k in klines:
            ts_list.append(int(k[0]))
        tf_timestamps[tf_name] = ts_list
        tf_closes_by_time[tf_name] = {int(k[0]): float(k[4]) for k in klines}

    # 3. Her kontrol noktasinda TF uyumunu hesapla
    results_5_5 = []  # (timestamp, direction)
    results_4_5 = []
    results_3_5 = []
    daily_counts = {}  # date → {5: count, 4: count, 3: count}

    check_time = start_ms
    total_checks = 0

    while check_time <= end_ms:
        # Her TF icin: bu zamandaki en son kapanmis LOOKBACK_CANDLES mumu bul
        tf_directions = {}
        valid_tfs = 0

        for tf_name, tf_min in TFS:
            tf_ms = tf_min * 60 * 1000
            # Bu TF'nin check_time'dan once kapanmis mumlarini bul
            all_ts = tf_timestamps[tf_name]
            # En son check_time'dan onceki mumlari al
            relevant_ts = [t for t in all_ts if t < check_time]
            if len(relevant_ts) < LOOKBACK_CANDLES:
                # Warmup yetersiz, atla
                continue

            # Son LOOKBACK_CANDLES mum
            window_ts = relevant_ts[-LOOKBACK_CANDLES:]
            closes = np.array([tf_closes_by_time[tf_name][t] for t in window_ts
                               if t in tf_closes_by_time[tf_name]])

            if len(closes) < 30:
                continue

            direction = analyze_tf_direction(closes)
            tf_directions[tf_name] = direction
            valid_tfs += 1

        if valid_tfs >= 5:
            total_checks += 1
            long_c = sum(1 for d in tf_directions.values() if d == "LONG")
            short_c = sum(1 for d in tf_directions.values() if d == "SHORT")
            best = max(long_c, short_c)
            best_dir = "LONG" if long_c >= short_c else "SHORT"

            dt = datetime.fromtimestamp(check_time / 1000, tz=timezone.utc)
            day_key = dt.strftime("%Y-%m-%d")
            if day_key not in daily_counts:
                daily_counts[day_key] = {5: 0, 4: 0, 3: 0}

            if best >= 5:
                results_5_5.append((check_time, best_dir, tf_directions))
                daily_counts[day_key][5] += 1
            if best >= 4:
                results_4_5.append((check_time, best_dir, tf_directions))
                daily_counts[day_key][4] += 1
            if best >= 3:
                results_3_5.append((check_time, best_dir, tf_directions))
                daily_counts[day_key][3] += 1

        check_time += check_interval_ms

    # 4. Rapor
    print(f"\n{'='*60}")
    print(f"Total check points: {total_checks}")
    print(f"Checks per day: ~{total_checks // max(DAYS_BACK, 1)}")
    print(f"{'='*60}")
    print(f"\n  5/5 TF uyumu (mevcut ayar): {len(results_5_5)} kez")
    print(f"  4/5 TF uyumu (onerilen):     {len(results_4_5)} kez")
    print(f"  3/5 TF uyumu (referans):     {len(results_3_5)} kez")
    print(f"\n  5/5 → 4/5 artis orani: {len(results_4_5)/max(len(results_5_5),1):.1f}x")

    print(f"\n{'─'*60}")
    print(f"{'Tarih':<12} {'5/5':>8} {'4/5':>8} {'3/5':>8}")
    print(f"{'─'*60}")
    for day in sorted(daily_counts.keys()):
        c = daily_counts[day]
        print(f"{day:<12} {c[5]:>8} {c[4]:>8} {c[3]:>8}")

    # 5. Son 5/5 uyum detaylari
    if results_5_5:
        print(f"\n{'─'*60}")
        print(f"Son 10 adet 5/5 uyum anı:")
        for ts, d, tfd in results_5_5[-10:]:
            dt = datetime.fromtimestamp(ts / 1000, tz=timezone.utc)
            flat_tfs = [tf for tf, dr in tfd.items() if dr == "FLAT"]
            print(f"  {dt:%Y-%m-%d %H:%M} UTC  {d:>5}  "
                  f"[{', '.join(f'{tf}={dr}' for tf, dr in tfd.items())}]")

    # 6. 4/5 ama 5/5 değil olanlar (kazanılacak fırsatlar)
    set_5_5 = set(ts for ts, _, _ in results_5_5)
    extra_4_5 = [(ts, d, tfd) for ts, d, tfd in results_4_5
                 if ts not in set_5_5]
    if extra_4_5:
        print(f"\n{'─'*60}")
        print(f"4/5 ama 5/5 degil — kacirilan firsatlar (son 15):")
        for ts, d, tfd in extra_4_5[-15:]:
            dt = datetime.fromtimestamp(ts / 1000, tz=timezone.utc)
            flat_tfs = [tf for tf, dr in tfd.items() if dr == "FLAT"]
            diff_tfs = [tf for tf, dr in tfd.items() if dr != d]
            print(f"  {dt:%Y-%m-%d %H:%M} UTC  {d:>5}  "
                  f"uymayan: {', '.join(diff_tfs)}")

    print(f"\n{'='*60}")
    print("NOT: Bu sadece TF uyumu filtresi. Gercek System F'de ustune")
    print("hacim, EV, P(SL), skor, orderbook filtreleri de var.")
    print(f"{'='*60}")


if __name__ == "__main__":
    run_backtest()
