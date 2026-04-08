"""
OPTIMAL TF SECIMI: "TF uzat, G dusur" noktasi
===============================================
Dert: G en dusuk olsun (kaldirac yuksek olsun),
      ama TF de yeterince uzun olsun (trade yapmak icin).

Aranan: TF artarken G'nin artisinin EN COK YAVASLADIGI nokta.
        = Marjinal G (dG/dTF) nin en hizli dustugu yer
        = G'nin ikinci turevinin en negatif oldugu yer

Ornek:
  1m: G=100  ->  marjinal = 100/dk
  3m: G=200  ->  marjinal = 50/dk   (dustu)
  5m: G=300  ->  marjinal = 50/dk   (ayni)
 10m: G=330  ->  marjinal = 6/dk    (DRAMATIK DUSUS!) <- BURASI!

 10m'de TF 2 kat artti ama G sadece %10 artti.
 Bu noktadan sonra TF uzatmanin G'ye etkisi minimumdur.
"""
import sys
import time
import math
import numpy as np
import requests

PROJECT_ROOT = r"C:\Users\ikizler1\AndroidStudioProjects\Tasking\Crypthos"
sys.path.insert(0, PROJECT_ROOT)

from scanner.system_b_scanner import detect_zigzag_swings, analyze_waves

COINS = ["BTCUSDT", "ETHUSDT", "XRPUSDT", "DOGEUSDT", "SOLUSDT", "AVAXUSDT", "ADAUSDT"]

TIMEFRAMES = [
    ("1m", 1), ("3m", 3), ("5m", 5), ("15m", 15), ("30m", 30),
    ("1h", 60), ("2h", 120), ("4h", 240), ("6h", 360), ("8h", 480),
    ("12h", 720), ("1d", 1440), ("3d", 4320), ("1w", 10080),
]

API_URL = "https://fapi.binance.com/fapi/v1/klines"
SWING_N = 10


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


def analyze_coin(symbol):
    rows = []
    for tf_name, tf_min in TIMEFRAMES:
        klines = fetch_klines(symbol, tf_name, 200)
        if not klines or len(klines) < SWING_N * 3:
            continue
        time.sleep(0.15)

        highs = np.array([float(k[2]) for k in klines])
        lows = np.array([float(k[3]) for k in klines])
        closes = np.array([float(k[4]) for k in klines])

        swings = detect_zigzag_swings(highs, lows, SWING_N)
        if len(swings) < 3:
            continue

        wave = analyze_waves(swings, closes[-1])
        G = wave.G
        I = wave.I
        if G < 0.0001:
            continue

        wc = len(wave.backward_waves) + len(wave.forward_waves)
        cv = wave.cv

        rows.append({
            "tf": tf_name,
            "tf_min": tf_min,
            "G": G,
            "I": I,
            "wc": wc,
            "cv": cv,
        })
    return rows


def find_optimal_tf(symbol, rows):
    """Optimal TF secimi: TF uzat, G dusur mantigi."""
    if len(rows) < 3:
        return

    print(f"\n{'='*130}")
    print(f"  {symbol}")
    print(f"{'='*130}")

    # ---- ADIM 1: Marjinal G hesapla (dG/dTF) ----
    for i in range(len(rows)):
        if i == 0:
            rows[i]["marginal_g"] = rows[i]["G"] / rows[i]["tf_min"]
        else:
            dG = rows[i]["G"] - rows[i-1]["G"]
            dTF = rows[i]["tf_min"] - rows[i-1]["tf_min"]
            rows[i]["marginal_g"] = dG / dTF if dTF > 0 else 0

    # ---- ADIM 2: Marjinal G'nin degisimi (ikinci turev) ----
    for i in range(len(rows)):
        if i == 0:
            rows[i]["marginal_g_change"] = 0
        else:
            rows[i]["marginal_g_change"] = rows[i]["marginal_g"] - rows[i-1]["marginal_g"]

    # ---- ADIM 3: G artis orani (yuzdesel) ----
    for i in range(len(rows)):
        if i == 0:
            rows[i]["g_pct_change"] = 0
            rows[i]["tf_pct_change"] = 0
            rows[i]["g_tf_ratio"] = 0
        else:
            rows[i]["g_pct_change"] = (rows[i]["G"] - rows[i-1]["G"]) / rows[i-1]["G"] * 100
            rows[i]["tf_pct_change"] = (rows[i]["tf_min"] - rows[i-1]["tf_min"]) / rows[i-1]["tf_min"] * 100
            # TF %X artti, G %Y artti, oran = Y/X
            if rows[i]["tf_pct_change"] > 0:
                rows[i]["g_tf_ratio"] = rows[i]["g_pct_change"] / rows[i]["tf_pct_change"]
            else:
                rows[i]["g_tf_ratio"] = 0

    # ---- TABLO ----
    print(f"\n  {'TF':>4s} | {'dk':>5s} | {'G%':>7s} | {'WC':>3s} | {'CV':>5s} | "
          f"{'Marj.G':>10s} | {'Mrj.Deg':>10s} | "
          f"{'TF art%':>7s} | {'G art%':>7s} | {'G/TF oran':>9s} | {'Aciklama':>25s}")
    print(f"  {'-'*125}")

    best_point = None
    best_score = float('inf')

    for i, r in enumerate(rows):
        # Aciklama belirle
        aciklama = ""
        if i > 0:
            ratio = r["g_tf_ratio"]
            if ratio < 0:
                aciklama = "<<< G AZALDI!"
            elif ratio < 0.15:
                aciklama = "<<< COK VERIMLI"
            elif ratio < 0.30:
                aciklama = "<< VERIMLI"
            elif ratio < 0.50:
                aciklama = "< IYI"
            elif ratio < 0.80:
                aciklama = "NORMAL"
            elif ratio < 1.0:
                aciklama = "VERIMSIZ"
            else:
                aciklama = "COK VERIMSIZ (G>TF)"

        mrj_str = f"{r['marginal_g']:.5f}" if r['marginal_g'] != 0 else "---"
        mrj_chg = f"{r['marginal_g_change']:+.5f}" if i > 0 else "---"
        tf_pct = f"{r['tf_pct_change']:+.0f}%" if i > 0 else "---"
        g_pct = f"{r['g_pct_change']:+.0f}%" if i > 0 else "---"
        ratio_str = f"{r['g_tf_ratio']:.3f}" if i > 0 else "---"

        print(f"  {r['tf']:>4s} | {r['tf_min']:>5d} | {r['G']:>7.3f} | {r['wc']:>3d} | {r['cv']:>5.2f} | "
              f"{mrj_str:>10s} | {mrj_chg:>10s} | "
              f"{tf_pct:>7s} | {g_pct:>7s} | {ratio_str:>9s} | {aciklama:>25s}")

        # Optimal nokta: G/TF oraninin en dusuk oldugu (ama yeterli dalga olan) gecis
        if i > 0 and r["wc"] >= 4 and r["g_tf_ratio"] >= 0:
            score = r["g_tf_ratio"]
            if score < best_score:
                best_score = score
                best_point = r

    # ---- EN VERIMLI NOKTALAR ----
    print(f"\n  --- SONUC ---")

    # 1. G/TF orani en dusuk gecis (ana metrik)
    valid = [r for r in rows[1:] if r["wc"] >= 4 and r["g_tf_ratio"] >= 0]
    if valid:
        best = min(valid, key=lambda r: r["g_tf_ratio"])
        print(f"  [1] EN VERIMLI GECIS    : {best['tf']:>4s}  "
              f"(TF {best['tf_pct_change']:+.0f}% artti, G sadece {best['g_pct_change']:+.0f}% artti, "
              f"oran={best['g_tf_ratio']:.3f})")
        print(f"      G={best['G']:.3f}%  ->  SL~{best['G']*1.5:.2f}%  "
              f"Kaldirac~{int(100/(best['G']*3/0.7))}x")

    # 2. Marjinal G'nin en cok dustugu yer (ikinci turev)
    valid2 = [r for r in rows[1:] if r["wc"] >= 4]
    if valid2:
        best2 = min(valid2, key=lambda r: r["marginal_g_change"])
        if best2["marginal_g_change"] < 0:
            print(f"  [2] MARJINAL G DUSUSU   : {best2['tf']:>4s}  "
                  f"(marjinal G {best2['marginal_g_change']:+.5f}/dk degisti)")

    # 3. G'nin azaldigi noktalar (klasik dirsek)
    neg_g = [r for r in rows[1:] if r["g_pct_change"] < 0 and r["wc"] >= 4]
    if neg_g:
        print(f"  [3] G AZALMA NOKTASI    : {neg_g[0]['tf']:>4s}  "
              f"(G {neg_g[0]['g_pct_change']:+.1f}% azaldi, nadirdir!)")

    # 4. Tum TF'lerde composite skor
    print(f"\n  --- COMPOSITE SKORLAMA (0-100) ---")
    print(f"  {'TF':>4s} | {'G%':>7s} | {'WC':>3s} | {'G/TF':>6s} | {'Skor':>5s} | {'Bar':>30s}")
    print(f"  {'-'*65}")

    scored = []
    for i, r in enumerate(rows):
        if r["wc"] < 3:
            continue

        # Skor bilesenleri:
        # 1. G ne kadar dusuk? (dusuk = iyi) -> 0-40 puan
        max_g = max(rr["G"] for rr in rows)
        g_score = max(0, (1.0 - r["G"] / max_g)) * 40

        # 2. TF ne kadar yuksek? (yuksek = iyi) -> 0-30 puan
        max_tf = max(rr["tf_min"] for rr in rows)
        tf_score = (r["tf_min"] / max_tf) * 30

        # 3. G/TF orani ne kadar dusuk? (dusuk = iyi) -> 0-20 puan
        if i > 0 and r["g_tf_ratio"] >= 0:
            max_ratio = max(rr["g_tf_ratio"] for rr in rows[1:] if rr["g_tf_ratio"] > 0) if any(rr["g_tf_ratio"] > 0 for rr in rows[1:]) else 1
            ratio_score = max(0, (1.0 - r["g_tf_ratio"] / max(max_ratio, 0.01))) * 20
        else:
            ratio_score = 20 if i > 0 and r["g_tf_ratio"] < 0 else 0

        # 4. Dalga kalitesi -> 0-10 puan
        wc_score = min(r["wc"] / 10.0, 1.0) * 10

        total = g_score + tf_score + ratio_score + wc_score
        scored.append((r, total, g_score, tf_score, ratio_score, wc_score))

    scored.sort(key=lambda x: -x[1])
    for r, total, gs, ts, rs, ws in scored[:8]:
        bar = "#" * int(total / 2)
        print(f"  {r['tf']:>4s} | {r['G']:>7.3f} | {r['wc']:>3d} | "
              f"{r.get('g_tf_ratio', 0):>6.3f} | {total:>5.1f} | {bar}")

    if scored:
        winner = scored[0]
        r = winner[0]
        print(f"\n  >>> OPTIMAL TF: {r['tf']}")
        print(f"      G = {r['G']:.3f}%")
        sl = r['G'] * 1.5
        lev = int(100 / (r['G'] * 3 / 0.7))
        lev = min(lev, 125)
        print(f"      SL ~ {sl:.2f}% (1.5 x G)")
        print(f"      Kaldirac ~ {lev}x")
        print(f"      Dalga sayisi: {r['wc']}")
        print(f"      CV: {r['cv']:.2f}")


def main():
    print("=" * 130)
    print("  OPTIMAL TF SECIMI: 'TF uzat, G dusur' mantigi")
    print("  Aranan: TF arttigi halde G'nin en az arttigi nokta")
    print("=" * 130)

    for symbol in COINS:
        print(f"\n  Fetching {symbol}...", end="", flush=True)
        rows = analyze_coin(symbol)
        print(f" {len(rows)} TFs", flush=True)
        if rows:
            find_optimal_tf(symbol, rows)

    # ---- OZET TABLO ----
    print(f"\n\n{'='*130}")
    print(f"  OZET: TUM COINLER ICIN OPTIMAL TF")
    print(f"{'='*130}")
    print(f"\n  Mantik: En yuksek TF + En dusuk G = En iyi trade verimi")
    print(f"  TF uzatildikca G de artar, ama G'nin artisinin yavasladigi")
    print(f"  nokta = 'buradan sonra TF uzatmanin karsiligi yok' noktasidir.")
    print(f"\n  Bu nokta her coin icin farklidir ve piyasa kosullarina gore degisir.")
    print(f"  Sistem bunu her scan'de dinamik olarak hesaplar.")


if __name__ == "__main__":
    main()
