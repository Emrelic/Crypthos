"""System F — TF Alignment Diagnostik.

Hangi TF en cok engel oluyor? Her kontrol noktasinda
5 TF'nin her birinin durumunu (LONG/SHORT/FLAT) logla
ve blokcu TF'yi tespit et.
"""
import requests
import numpy as np
import time
import bisect
from datetime import datetime, timedelta, timezone
from collections import Counter

DAYS_BACK = 30
CHECK_INTERVAL_MIN = 15
TOP_COINS = 15
LOOKBACK = 200

# System F indicator params
EMA_FAST, EMA_SLOW = 9, 21
EMA_GAP_MIN = 0.05
MACD_FAST, MACD_SLOW, MACD_SIG = 8, 17, 9
RSI_PERIOD = 14
RSI_LONG, RSI_SHORT = 60, 40

DIRECTION_TFS = ["5m", "15m", "1h", "4h", "1d"]
TF_MINUTES = {"5m": 5, "15m": 15, "1h": 60, "4h": 240, "1d": 1440}

# ─── Indicators (same as System F) ───

def ema_val(data, period):
    if len(data) < period: return float(np.mean(data)) if len(data) > 0 else 0.0
    k = 2.0/(period+1); e = float(data[0])
    for v in data[1:]: e = v*k + e*(1-k)
    return e

def ema_series(data, period):
    if len(data)==0: return np.array([0.0])
    k = 2.0/(period+1); r = np.empty(len(data)); r[0] = float(data[0])
    for i in range(1,len(data)): r[i] = float(data[i])*k + r[i-1]*(1-k)
    return r

def macd_hist_series(closes):
    if len(closes) < MACD_SLOW: return np.array([0.0])
    kf, ks = 2.0/(MACD_FAST+1), 2.0/(MACD_SLOW+1)
    ef = es = float(closes[0]); s = []
    for v in closes: ef=v*kf+ef*(1-kf); es=v*ks+es*(1-ks); s.append(ef-es)
    ml = np.array(s); sl = ema_series(ml, MACD_SIG)
    return ml - sl

def rsi_val(closes, period=14):
    if len(closes)<period+1: return 50.0
    d=np.diff(closes); g=np.where(d>0,d,0.0); l=np.where(d<0,-d,0.0)
    ag=np.mean(g[:period]); al=np.mean(l[:period])
    for i in range(period,len(g)): ag=(ag*(period-1)+g[i])/period; al=(al*(period-1)+l[i])/period
    if al==0: return 100.0
    return 100.0-100.0/(1.0+ag/al)


def tf_direction(closes):
    """Single TF 3/3 vote -> LONG/SHORT/FLAT + per-indicator votes."""
    if len(closes) < 30: return "FLAT", {"ema": 0, "macd": 0, "rsi": 0}
    price = float(closes[-1])
    if price <= 0: return "FLAT", {"ema": 0, "macd": 0, "rsi": 0}

    # EMA
    ef = ema_val(closes, EMA_FAST); es = ema_val(closes, EMA_SLOW)
    gap = (ef-es)/price*100
    ev = 1 if gap > EMA_GAP_MIN else (-1 if gap < -EMA_GAP_MIN else 0)

    # MACD
    hs = macd_hist_series(closes)
    mv = 0
    if len(hs) >= 3:
        h1,h2,h3 = float(hs[-3]),float(hs[-2]),float(hs[-1])
        if h3>0 and h1<h2<h3: mv = 1
        elif h3<0 and h1>h2>h3: mv = -1

    # RSI
    r = rsi_val(closes, RSI_PERIOD)
    rv = 1 if r > RSI_LONG else (-1 if r < RSI_SHORT else 0)

    if ev>0 and mv>0 and rv>0: d = "LONG"
    elif ev<0 and mv<0 and rv<0: d = "SHORT"
    else: d = "FLAT"

    return d, {"ema": ev, "macd": mv, "rsi": rv}


# ─── Binance ───

def fetch_klines(symbol, interval, start_ms, end_ms, limit=1500):
    all_kl = []; cursor = start_ms
    while cursor < end_ms:
        params = {"symbol": symbol, "interval": interval,
                  "startTime": int(cursor), "endTime": int(end_ms), "limit": limit}
        for attempt in range(5):
            try:
                resp = requests.get("https://fapi.binance.com/fapi/v1/klines",
                                    params=params, timeout=20)
                resp.raise_for_status(); data = resp.json(); break
            except (requests.ConnectionError, requests.Timeout):
                if attempt < 4: time.sleep(2*(attempt+1))
                else: data = []
        if not data: break
        all_kl.extend(data); cursor = int(data[-1][0])+1
        if len(data) < limit: break
        time.sleep(0.1)
    return all_kl

def get_top_symbols(n=15):
    for attempt in range(5):
        try:
            resp = requests.get("https://fapi.binance.com/fapi/v1/ticker/24hr", timeout=20)
            resp.raise_for_status(); break
        except: time.sleep(3)
    tickers = resp.json()
    usdt = [t for t in tickers if t["symbol"].endswith("USDT")
            and not any(x in t["symbol"] for x in ["_","BTCDOM","DEFI"])]
    usdt.sort(key=lambda t: float(t["quoteVolume"]), reverse=True)
    return [t["symbol"] for t in usdt[:n]]


# ─── Main ───

def run():
    now = datetime.now(timezone.utc)
    end_ms = int(now.timestamp()*1000)
    start_ms = int((now - timedelta(days=DAYS_BACK)).timestamp()*1000)

    print(f"=== System F TF Alignment Diagnostik (30 gun) ===\n")

    symbols = get_top_symbols(TOP_COINS)
    if "BTCUSDT" not in symbols: symbols.insert(0, "BTCUSDT")
    print(f"{len(symbols)} coin: {', '.join(symbols[:8])}...\n")

    # Veri cek
    print("Veri cekiliyor...")
    all_data = {}
    for si, sym in enumerate(symbols):
        all_data[sym] = {}
        for tf in DIRECTION_TFS:
            tf_min = TF_MINUTES[tf]
            warmup = LOOKBACK * tf_min * 60 * 1000
            all_data[sym][tf] = fetch_klines(sym, tf, start_ms - warmup, end_ms)
            time.sleep(0.06)
        print(f"  [{si+1}/{len(symbols)}] {sym}")

    # Pre-index
    sym_tf_ts = {}
    for sym in symbols:
        for tf in DIRECTION_TFS:
            sym_tf_ts[(sym,tf)] = [int(k[0]) for k in all_data[sym].get(tf,[])]

    # Analiz
    print(f"\nAnaliz basliyor...\n")
    check_ms = CHECK_INTERVAL_MIN * 60 * 1000
    check_time = start_ms

    # Istatistikler
    tf_flat_count = Counter()       # hangi TF kac kez FLAT
    tf_disagree_count = Counter()   # hangi TF kac kez cogunluktan farkli
    tf_block_count = Counter()      # hangi TF alignment'i blokladi (tek engel)
    flat_indicator_count = Counter() # FLAT olan TF'lerde hangi indikator neden
    alignment_dist = Counter()      # kac TF uyumlu dagilimi (0-5)
    direction_when_blocked = Counter()  # bloklanan TF'nin yonu
    total_checks = 0
    total_coin_checks = 0

    # En yakin uyum anlarini kaydet (4/5 olanlar)
    near_misses = []

    while check_time <= end_ms:
        total_checks += 1
        for sym in symbols:
            # Build windows
            klines_window = {}
            skip = False
            for tf in DIRECTION_TFS:
                ts_list = sym_tf_ts.get((sym,tf),[])
                idx = bisect.bisect_left(ts_list, check_time)
                if idx < LOOKBACK: skip = True; break
                klines_window[tf] = all_data[sym][tf][idx-LOOKBACK:idx]
            if skip: continue
            total_coin_checks += 1

            # Her TF'nin yonunu hesapla
            tf_dirs = {}
            tf_votes = {}
            for tf in DIRECTION_TFS:
                kl = klines_window[tf]
                closes = np.array([float(k[4]) for k in kl])
                d, votes = tf_direction(closes)
                tf_dirs[tf] = d
                tf_votes[tf] = votes

            # Cogunluk yonu
            long_c = sum(1 for d in tf_dirs.values() if d=="LONG")
            short_c = sum(1 for d in tf_dirs.values() if d=="SHORT")
            flat_c = sum(1 for d in tf_dirs.values() if d=="FLAT")
            aligned = max(long_c, short_c)
            majority = "LONG" if long_c >= short_c else "SHORT"

            alignment_dist[aligned] += 1

            # Hangi TF'ler FLAT
            for tf in DIRECTION_TFS:
                if tf_dirs[tf] == "FLAT":
                    tf_flat_count[tf] += 1
                    # Hangi indikator FLAT yapti
                    v = tf_votes[tf]
                    for ind, val in v.items():
                        if val == 0:
                            flat_indicator_count[f"{tf}:{ind}"] += 1

            # Hangi TF cogunluktan farkli
            for tf in DIRECTION_TFS:
                if tf_dirs[tf] != majority and tf_dirs[tf] != "FLAT":
                    tf_disagree_count[tf] += 1

            # Tek engel TF (4/5 uyum)
            if aligned == 4:
                blockers = [tf for tf in DIRECTION_TFS if tf_dirs[tf] != majority]
                for b in blockers:
                    tf_block_count[b] += 1
                    direction_when_blocked[f"{b}={tf_dirs[b]}"] += 1
                near_misses.append({
                    "time": check_time, "sym": sym,
                    "dirs": dict(tf_dirs), "majority": majority,
                    "blocker": blockers[0] if blockers else "?"
                })

        if total_checks % 200 == 0:
            days = (check_time - start_ms)/(86400*1000)
            print(f"  ... {days:.0f}/{DAYS_BACK} gun", flush=True)

        check_time += check_ms

    # ─── Rapor ───
    print(f"\n{'='*65}")
    print(f"  SONUCLAR — {total_coin_checks:,} coin-kontrol noktasi")
    print(f"{'='*65}")

    print(f"\n1. TF Uyum Dagilimi (kac TF ayni yonde?):")
    for n in sorted(alignment_dist.keys()):
        cnt = alignment_dist[n]
        pct = cnt/total_coin_checks*100
        bar = "#" * int(pct)
        print(f"   {n}/5 uyum: {cnt:>8} ({pct:>5.1f}%) {bar}")

    print(f"\n2. Hangi TF en cok FLAT (3/3 uyum saglamiyor)?")
    for tf in DIRECTION_TFS:
        cnt = tf_flat_count[tf]
        pct = cnt/total_coin_checks*100
        bar = "#" * int(pct/2)
        print(f"   {tf:>4}: {cnt:>8} ({pct:>5.1f}%) {bar}")

    print(f"\n3. FLAT yapan indikator (TF:indikator):")
    for key, cnt in flat_indicator_count.most_common(15):
        pct = cnt/total_coin_checks*100
        print(f"   {key:<12}: {cnt:>8} ({pct:>5.1f}%)")

    print(f"\n4. Hangi TF cogunluktan FARKLI yon gosteriyor?")
    for tf in DIRECTION_TFS:
        cnt = tf_disagree_count[tf]
        pct = cnt/total_coin_checks*100
        print(f"   {tf:>4}: {cnt:>8} ({pct:>5.1f}%)")

    print(f"\n5. 4/5 uyumda TEK ENGEL olan TF:")
    for tf in DIRECTION_TFS:
        cnt = tf_block_count[tf]
        print(f"   {tf:>4}: {cnt:>8} kez blokladi")
    print(f"   Bloklama detayi:")
    for key, cnt in direction_when_blocked.most_common(10):
        print(f"     {key}: {cnt} kez")

    # Near miss ornekleri
    if near_misses:
        print(f"\n6. Son 20 adet 4/5 uyum ani (near-miss):")
        print(f"   {'Tarih':>16} {'Coin':>12} {'Yon':>5} {'5m':>5} {'15m':>5} "
              f"{'1h':>5} {'4h':>5} {'1d':>5} {'Engel':>6}")
        print(f"   {'-'*75}")
        for nm in near_misses[-20:]:
            dt = datetime.fromtimestamp(nm["time"]/1000, tz=timezone.utc)
            d = nm["dirs"]
            print(f"   {dt:%Y-%m-%d %H:%M} {nm['sym']:>12} {nm['majority']:>5} "
                  f"{d['5m']:>5} {d['15m']:>5} {d['1h']:>5} {d['4h']:>5} "
                  f"{d['1d']:>5} {nm['blocker']:>6}")

    print(f"\n{'='*65}")


if __name__ == "__main__":
    run()
