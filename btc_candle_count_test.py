"""
BTC: Mum sayisinin G'ye etkisi
200 vs 500 vs 1000 mum ile ayni TF'lerde G karsilastirmasi.
"""
import sys
import time
import numpy as np
import requests

PROJECT_ROOT = r"C:\Users\ikizler1\AndroidStudioProjects\Tasking\Crypthos"
sys.path.insert(0, PROJECT_ROOT)

from scanner.system_b_scanner import detect_zigzag_swings, analyze_waves

TIMEFRAMES = [
    ("1m", 1), ("3m", 3), ("5m", 5), ("15m", 15), ("30m", 30),
    ("1h", 60), ("2h", 120), ("4h", 240), ("6h", 360), ("8h", 480),
    ("12h", 720), ("1d", 1440),
]

CANDLE_COUNTS = [200, 500, 1000, 1500]
API_URL = "https://fapi.binance.com/fapi/v1/klines"
SWING_N = 10
FEE_PCT = 0.08
SLIPPAGE_PCT = 0.04
LIQ_SAFETY = 0.7


def fetch_klines(symbol, interval, limit=200):
    params = {"symbol": symbol, "interval": interval, "limit": limit}
    for attempt in range(3):
        try:
            resp = requests.get(API_URL, params=params, timeout=15)
            if resp.status_code == 429:
                time.sleep(10)
                continue
            resp.raise_for_status()
            return resp.json()
        except Exception:
            if attempt < 2:
                time.sleep(2)
            else:
                return []
    return []


def calc_leverage(G):
    if G < 0.01:
        return 0
    sl_pct = G * 1.5 + FEE_PCT + SLIPPAGE_PCT
    pratik_liq = G * 3.0
    teorik_liq = (pratik_liq + FEE_PCT) / LIQ_SAFETY
    leverage = 100.0 / teorik_liq if teorik_liq > 0 else 0
    return max(1, min(int(leverage), 125))


def analyze_tf(symbol, tf_name, tf_min, limit):
    klines = fetch_klines(symbol, tf_name, limit)
    if not klines or len(klines) < SWING_N * 3:
        return None
    time.sleep(0.15)

    highs = np.array([float(k[2]) for k in klines])
    lows = np.array([float(k[3]) for k in klines])
    closes = np.array([float(k[4]) for k in klines])

    swings = detect_zigzag_swings(highs, lows, SWING_N)
    if len(swings) < 3:
        return None

    wave = analyze_waves(swings, closes[-1])
    G = wave.G
    if G < 0.0001:
        return None

    wc = len(wave.backward_waves) + len(wave.forward_waves)
    bw = len(wave.backward_waves)
    actual_candles = len(klines)
    hours = actual_candles * tf_min / 60
    days = hours / 24

    return {
        "G": G, "I": wave.I, "wc": wc, "bw": bw,
        "cv": wave.cv, "candles": actual_candles,
        "hours": hours, "days": days,
        "leverage": calc_leverage(G),
    }


def main():
    symbol = "BTCUSDT"

    print("=" * 160)
    print(f"  BTCUSDT: MUM SAYISININ G'YE ETKISI")
    print(f"  Ayni TF, farkli mum sayisi (200, 500, 1000) ile G ne kadar degisiyor?")
    print("=" * 160)

    # Her mum sayisi icin veri topla
    all_data = {}
    for limit in CANDLE_COUNTS:
        print(f"\n  Fetching {limit} candles...", end="", flush=True)
        data = {}
        for tf_name, tf_min in TIMEFRAMES:
            result = analyze_tf(symbol, tf_name, tf_min, limit)
            if result:
                data[tf_name] = result
        all_data[limit] = data
        print(f" done ({len(data)} TFs)")

    # =================== TABLO 1: Zaman kapsami ===================
    print(f"\n\n  TABLO 1: HER TF'DE MUM SAYISI NE KADAR ZAMANI KAPSIYOR?")
    print(f"  {'-'*120}")
    hdr = f"  {'TF':>4s}"
    for limit in CANDLE_COUNTS:
        hdr += f" | {f'{limit} mum':>22s}"
    print(hdr)
    print(f"  {'-'*110}")
    for tf_name, tf_min in TIMEFRAMES:
        line = f"  {tf_name:>4s}"
        for limit in CANDLE_COUNTS:
            d = all_data[limit].get(tf_name)
            if d:
                if d["days"] >= 1:
                    line += f" | {d['days']:>6.1f} gun ({d['candles']:>4d} mum)"
                else:
                    line += f" | {d['hours']:>5.1f} saat ({d['candles']:>4d} mum)"
            else:
                line += f" | {'---':>22s}"
        print(line)

    # =================== TABLO 2: G karsilastirmasi ===================
    print(f"\n\n  TABLO 2: G% KARSILASTIRMASI (200 vs 500 vs 1000 mum)")
    print(f"  {'-'*140}")
    hdr2 = f"  {'TF':>4s}"
    for limit in CANDLE_COUNTS:
        hdr2 += f" | {'G%':>8s} {'Lev':>4s} {'WC':>3s} {'BW':>3s}"
    hdr2 += f" | {'Fark%':>7s} | {'Stabil?':>10s}"
    print(hdr2)
    print(f"  {'-'*160}")

    for tf_name, tf_min in TIMEFRAMES:
        line = f"  {tf_name:>4s}"
        gs = []
        for limit in CANDLE_COUNTS:
            d = all_data[limit].get(tf_name)
            if d:
                line += f" | {d['G']:>8.3f} {d['leverage']:>3d}x {d['wc']:>3d} {d['bw']:>3d}"
                gs.append(d['G'])
            else:
                line += f" | {'---':>8s} {'--':>4s} {'--':>3s} {'--':>3s}"

        # Stabilite: max-min / ortalama
        if len(gs) >= 2:
            mean_g = np.mean(gs)
            spread = (max(gs) - min(gs)) / mean_g * 100 if mean_g > 0 else 0
            fark = f"{spread:+.0f}%"
            if spread < 15:
                stabil = "STABIL"
            elif spread < 30:
                stabil = "DEGISKEN"
            elif spread < 60:
                stabil = "OYNAK"
            else:
                stabil = "COK OYNAK"
        else:
            fark = "---"
            stabil = "---"

        line += f" | {fark:>7s} | {stabil:>10s}"
        print(line)

    # =================== TABLO 3: Algoritma karsilastirmasi ===================
    print(f"\n\n  TABLO 3: OPTIMAL TF SECIMI (her mum sayisiyla)")
    print(f"  {'-'*100}")

    for limit in CANDLE_COUNTS:
        data = all_data[limit]
        tf_list = [(tf_name, tf_min) for tf_name, tf_min in TIMEFRAMES if tf_name in data]

        # Sirala
        rows = []
        for tf_name, tf_min in tf_list:
            d = data[tf_name]
            rows.append({"tf": tf_name, "tf_min": tf_min, **d})

        if len(rows) < 2:
            continue

        # G/TF oranlarini hesapla
        for i in range(len(rows)):
            if i == 0:
                rows[i]["g_tf_oran"] = 0
                rows[i]["g_artis"] = 0
            else:
                prev = rows[i-1]
                g_art = (rows[i]["G"] - prev["G"]) / prev["G"] * 100
                tf_art = (rows[i]["tf_min"] - prev["tf_min"]) / prev["tf_min"] * 100
                rows[i]["g_artis"] = g_art
                rows[i]["g_tf_oran"] = g_art / tf_art if tf_art > 0 else 0

        # Algoritma: asagidan yukari
        optimal = rows[0]
        for i in range(1, len(rows)):
            oran = rows[i]["g_tf_oran"]
            g_azaldi = rows[i]["g_artis"] < 0
            if g_azaldi or oran < 0.30:
                optimal = rows[i]
            elif oran < 0.60:
                optimal = rows[i]
            else:
                break

        d = all_data[limit].get(optimal["tf"], {})
        days = d.get("days", 0)
        print(f"  {limit:>4d} mum | Optimal: {optimal['tf']:>4s} | G={optimal['G']:.3f}% | "
              f"Lev={optimal['leverage']}x | WC={optimal['wc']} BW={optimal['bw']} | "
              f"Kapsam: {days:.1f} gun")

    # =================== SONUC ===================
    print(f"\n\n  {'='*100}")
    print(f"  SONUC VE ONERILER")
    print(f"  {'='*100}")
    print(f"""
  GOZLEMLER:
  - Kisa TF'lerde (1m, 3m, 5m) G degeri mum sayisina COK DUYARLI
    -> 200 mumda 3 saat, 1000 mumda 16 saat = tamamen farkli piyasa
    -> Bu TF'lerde G guvenilir degil

  - Orta TF'lerde (15m, 30m, 1h, 2h) G daha STABIL
    -> 200 mumda bile birkaac gun kapsiyor
    -> Dalga sayisi yeterli (8-12 dalga)

  - Uzun TF'lerde (4h+) G cok STABIL
    -> 200 mum bile 1+ ay kapsiyor
    -> Ama kaldirac cok dusuk

  ONERI:
  - Kisa TF'ler (1m-5m) icin EN AZ 500 mum kullanilmali (~1-3 gun)
  - Orta TF'ler (15m-2h) icin 200 mum yeterli (4-17 gun)
  - Uzun TF'ler (4h+) icin 200 mum fazlasiyla yeterli

  ALTERNATIF: Her TF icin AYNI ZAMAN DILIMINI kapsayacak mum sayisi
  Ornegin: Her TF icin "son 7 gun" veri kullan:
    1m  -> 7*24*60   = 10080 mum (API limit 1500, MAX 1500)
    5m  -> 7*24*12   = 2016 mum (MAX 1500)
    15m -> 7*24*4    = 672 mum
    1h  -> 7*24      = 168 mum
    4h  -> 7*6       = 42 mum (cok az!)
  Bu da sorunlu: uzun TF'lerde cok az mum kalir.

  EN IYI YAKLASIM: TF'ye gore dinamik mum sayisi
    Kisa TF (1m-5m):   1000 mum (~1-3 gun)
    Orta TF (15m-2h):  500 mum  (~5-17 gun)
    Uzun TF (4h-1d):   200 mum  (~33-200 gun)
    Cok uzun (3d-1w):  200 mum  (~1.6-3.8 yil)
  """)


if __name__ == "__main__":
    main()
