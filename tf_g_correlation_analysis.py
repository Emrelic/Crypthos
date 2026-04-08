"""
TF-G Iliskisi: Istatistiksel Korelasyon ve Dirsek Analizi
==========================================================
Gercek Binance verilerinden tum TF'lerde G hesaplayip
TF ile G arasindaki matematiksel iliskiyi kesfeder.
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
        except Exception as e:
            if attempt < 2:
                time.sleep(2)
            else:
                return []
    return []


def pearson_r(x, y):
    """Pearson korelasyon katsayisi."""
    if len(x) < 3:
        return 0.0
    x, y = np.array(x, dtype=float), np.array(y, dtype=float)
    mx, my = np.mean(x), np.mean(y)
    num = np.sum((x - mx) * (y - my))
    den = math.sqrt(np.sum((x - mx)**2) * np.sum((y - my)**2))
    return num / den if den > 0 else 0.0


def power_law_fit(x, y):
    """y = a * x^b  ->  log(y) = log(a) + b*log(x). Returns (a, b, R2)."""
    x, y = np.array(x, dtype=float), np.array(y, dtype=float)
    mask = (x > 0) & (y > 0)
    x, y = x[mask], y[mask]
    if len(x) < 3:
        return 0, 0, 0
    lx, ly = np.log(x), np.log(y)
    b, log_a = np.polyfit(lx, ly, 1)
    a = math.exp(log_a)
    # R2
    ly_pred = log_a + b * lx
    ss_res = np.sum((ly - ly_pred)**2)
    ss_tot = np.sum((ly - np.mean(ly))**2)
    r2 = 1 - ss_res / ss_tot if ss_tot > 0 else 0
    return a, b, r2


def linear_fit(x, y):
    """y = a + b*x. Returns (a, b, R2)."""
    x, y = np.array(x, dtype=float), np.array(y, dtype=float)
    if len(x) < 3:
        return 0, 0, 0
    b, a = np.polyfit(x, y, 1)
    y_pred = a + b * x
    ss_res = np.sum((y - y_pred)**2)
    ss_tot = np.sum((y - np.mean(y))**2)
    r2 = 1 - ss_res / ss_tot if ss_tot > 0 else 0
    return a, b, r2


def analyze_coin(symbol):
    """Tek coin icin tum TF'lerde G analizi."""
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


def print_coin_analysis(symbol, rows):
    """Coin tablosu + turetilmis metrikler + onemli noktalar."""
    print(f"\n{'='*140}")
    print(f"  {symbol}  --  {len(rows)} TF analiz edildi")
    print(f"{'='*140}")

    if len(rows) < 3:
        print("  Yetersiz veri (< 3 TF)")
        return rows

    # Turetilmis metrikler hesapla
    for i, r in enumerate(rows):
        r["efficiency"] = r["tf_min"] / r["G"]                # TF_dk / G
        r["g_per_min"] = r["G"] / r["tf_min"]                 # G / TF_dk
        r["g2_per_min"] = (r["G"]**2) / r["tf_min"]           # G2 / TF_dk
        r["log_tf"] = math.log(r["tf_min"])                    # ln(TF)
        r["log_g"] = math.log(r["G"])                          # ln(G)
        r["sqrt_tf"] = math.sqrt(r["tf_min"])                  # VTF
        r["g_per_sqrt_tf"] = r["G"] / math.sqrt(r["tf_min"])   # G / VTF

        if i < len(rows) - 1:
            nxt = rows[i + 1]
            r["g_growth"] = (nxt["G"] - r["G"]) / r["G"]
            tf_ratio = nxt["tf_min"] / r["tf_min"]
            g_ratio = nxt["G"] / r["G"]
            r["elasticity"] = math.log(g_ratio) / math.log(tf_ratio) if tf_ratio > 1 and g_ratio > 0 else 0
        else:
            r["g_growth"] = 0.0
            r["elasticity"] = 0.0

    # Tablo yazdir
    hdr = (f"  {'TF':>4s} | {'dk':>5s} | {'G%':>7s} | {'I%':>7s} | {'WC':>3s} | {'CV':>5s} | "
           f"{'Verim':>7s} | {'G/dk':>8s} | {'G2/dk':>8s} | {'G/Vdk':>7s} | "
           f"{'G_art%':>7s} | {'Elast':>6s}")
    print(hdr)
    print("  " + "-" * 136)

    for r in rows:
        g_growth_str = f"{r['g_growth']:+.2f}" if r['g_growth'] != 0 else "---"
        elast_str = f"{r['elasticity']:.3f}" if r['elasticity'] != 0 else "---"
        print(f"  {r['tf']:>4s} | {r['tf_min']:>5d} | {r['G']:>7.3f} | {r['I']:>7.3f} | "
              f"{r['wc']:>3d} | {r['cv']:>5.2f} | "
              f"{r['efficiency']:>7.1f} | {r['g_per_min']:>8.5f} | {r['g2_per_min']:>8.5f} | "
              f"{r['g_per_sqrt_tf']:>7.4f} | "
              f"{g_growth_str:>7s} | {elast_str:>6s}")

    # === ONEMLI NOKTALAR ===
    print(f"\n  --- Onemli Noktalar ---")

    # 1. Dirsek: ilk negatif g_growth
    neg_growth = [r for r in rows[:-1] if r["g_growth"] < 0]
    if neg_growth:
        d = neg_growth[0]
        print(f"  DIRSEK (G azalan)        : {d['tf']:>4s}  G={d['G']:.3f}%  artis={d['g_growth']:+.2f}")
    else:
        print(f"  DIRSEK (G azalan)        : YOK (G surekli artiyor)")

    # 2. Min g_growth (en yavas artan)
    valid = [r for r in rows[:-1] if r["g_growth"] != 0]
    if valid:
        mg = min(valid, key=lambda r: r["g_growth"])
        print(f"  MIN ARTIS HIZI           : {mg['tf']:>4s}  G={mg['G']:.3f}%  artis={mg['g_growth']:+.2f}")

    # 3. Max verimlilik (TF_dk / G)
    mv = max(rows, key=lambda r: r["efficiency"])
    print(f"  MAX VERIMLILIK (dk/G)    : {mv['tf']:>4s}  verim={mv['efficiency']:.1f}")

    # 4. Min G/dk (en az G uretimi dakikada)
    mg2 = min(rows, key=lambda r: r["g_per_min"])
    print(f"  MIN G/dakika             : {mg2['tf']:>4s}  G/dk={mg2['g_per_min']:.6f}")

    # 5. Max G2/dk
    mg3 = max(rows, key=lambda r: r["g2_per_min"])
    print(f"  MAX G2/dakika            : {mg3['tf']:>4s}  G2/dk={mg3['g2_per_min']:.5f}")

    # 6. G/VTF stabilite noktasi
    g_sqrt_values = [r["g_per_sqrt_tf"] for r in rows]
    mean_gs = np.mean(g_sqrt_values)
    std_gs = np.std(g_sqrt_values)
    cv_gs = std_gs / mean_gs if mean_gs > 0 else 0
    print(f"  G/VTF ortamala           : {mean_gs:.4f}  (CV={cv_gs:.3f})")

    # 7. Elastisite analizi
    elasticities = [r["elasticity"] for r in rows[:-1] if r["elasticity"] > 0]
    if elasticities:
        mean_e = np.mean(elasticities)
        print(f"  ORT ELASTISITE           : {mean_e:.3f}  "
              f"({'sub-lineer' if mean_e < 1 else 'super-lineer' if mean_e > 1 else 'lineer'})")

    return rows


def cross_coin_correlation(all_data):
    """Tum coinlerin TF-G iliskisini istatistiksel analiz et."""
    print(f"\n\n{'#'*140}")
    print(f"  CAPRAZ KORELASYON ANALIZI -- TUM COINLER")
    print(f"{'#'*140}")

    # Tum coinlerin verilerini birlestir
    all_tf = []
    all_g = []
    all_log_tf = []
    all_log_g = []
    all_sqrt_tf = []
    all_g_sqrt = []

    for symbol, rows in all_data.items():
        for r in rows:
            all_tf.append(r["tf_min"])
            all_g.append(r["G"])
            all_log_tf.append(r["log_tf"])
            all_log_g.append(r["log_g"])
            all_sqrt_tf.append(r["sqrt_tf"])
            all_g_sqrt.append(r["g_per_sqrt_tf"])

    # === 1. KORELASYON MATRISI ===
    print(f"\n  === 1. PEARSON KORELASYONLARI ===")
    pairs = [
        ("TF", "G", all_tf, all_g),
        ("ln(TF)", "ln(G)", all_log_tf, all_log_g),
        ("VTF", "G", all_sqrt_tf, all_g),
        ("TF", "G2", all_tf, [g**2 for g in all_g]),
        ("ln(TF)", "G/VTF", all_log_tf, all_g_sqrt),
    ]
    print(f"  {'Iliski':<25s} | {'Pearson r':>10s} | {'R2':>8s} | {'Anlam':>20s}")
    print(f"  {'-'*75}")
    for name_x, name_y, x, y in pairs:
        r = pearson_r(x, y)
        r2 = r ** 2
        strength = "cok guclu" if abs(r) > 0.95 else "guclu" if abs(r) > 0.8 else "orta" if abs(r) > 0.5 else "zayif"
        print(f"  {name_x+' vs '+name_y:<25s} | {r:>+10.4f} | {r2:>8.4f} | {strength:>20s}")

    # === 2. POWER LAW: G = a x TF^b ===
    print(f"\n  === 2. POWER LAW ANALIZI: G = a x TF^b ===")
    print(f"  {'Coin':<12s} | {'a':>10s} | {'b (us)':>10s} | {'R2':>8s} | {'Yorum':>30s}")
    print(f"  {'-'*80}")

    all_bs = []
    for symbol, rows in all_data.items():
        tfs = [r["tf_min"] for r in rows]
        gs = [r["G"] for r in rows]
        a, b, r2 = power_law_fit(tfs, gs)
        all_bs.append(b)
        if b < 0.4:
            yorum = "sub-karekok (b<0.5)"
        elif b < 0.6:
            yorum = "karekok benzeri (b~0.5)"
        elif b < 0.9:
            yorum = "sub-lineer (0.5<b<1)"
        elif b < 1.1:
            yorum = "lineer (b~1)"
        else:
            yorum = "super-lineer (b>1)"
        print(f"  {symbol:<12s} | {a:>10.5f} | {b:>10.4f} | {r2:>8.4f} | {yorum:>30s}")

    mean_b = np.mean(all_bs)
    print(f"\n  Ortalama b = {mean_b:.4f}")
    if 0.4 <= mean_b <= 0.6:
        print(f"  >>> G ~ a x VTF  (Karekok iliskisi -- Random Walk benzeri)")
        print(f"  >>> Bu su anlama gelir: Fiyat hareketleri buyuk olcekte rastgele yuruyuse benzer")
        print(f"  >>> TF 4 kat arttiginda G sadece 2 kat artar")
    elif mean_b < 0.4:
        print(f"  >>> G, TF'den bagimsizlasiyor (mean-reversion yapilar baskin)")
    elif mean_b < 0.9:
        print(f"  >>> G sub-lineer artiyor, TF buyudukce verimlilik artiyor")
    else:
        print(f"  >>> G lineer veya super-lineer, TF buyudukce G orantili artiyor")

    # === 3. G/VTF SABITLIGI (Random Walk Testi) ===
    print(f"\n  === 3. G/VTF SABITLIGI (Random Walk Testi) ===")
    print(f"  Eger G = axVTF ise, G/VTF her TF'de ayni olmali (sabit).")
    print(f"  {'Coin':<12s} | {'G/VTF ort':>10s} | {'Std':>8s} | {'CV%':>7s} | {'Yorum':>25s}")
    print(f"  {'-'*70}")
    for symbol, rows in all_data.items():
        vals = [r["g_per_sqrt_tf"] for r in rows]
        m = np.mean(vals)
        s = np.std(vals)
        cv = s / m * 100 if m > 0 else 0
        yorum = "SABIT (~random walk)" if cv < 15 else "DEGISKEN" if cv < 30 else "COK DEGISKEN"
        print(f"  {symbol:<12s} | {m:>10.4f} | {s:>8.4f} | {cv:>6.1f}% | {yorum:>25s}")

    # === 4. ELASTISITE ANALIZI ===
    print(f"\n  === 4. ELASTISITE: TF %1 arttiginda G %kac artar? ===")
    print(f"  Elastisite = ln(G_ratio) / ln(TF_ratio)")
    print(f"  e=0.5 -> karekok, e=1.0 -> lineer, e<0.5 -> mean-reversion")
    print(f"  {'Coin':<12s} | {'Ort Elast':>10s} | {'Min':>8s} | {'Max':>8s} | {'Std':>8s}")
    print(f"  {'-'*55}")
    for symbol, rows in all_data.items():
        elasticities = [r["elasticity"] for r in rows[:-1] if r["elasticity"] > 0]
        if elasticities:
            print(f"  {symbol:<12s} | {np.mean(elasticities):>10.4f} | "
                  f"{min(elasticities):>8.4f} | {max(elasticities):>8.4f} | "
                  f"{np.std(elasticities):>8.4f}")

    # === 5. EN OPTIMAL TF SECIMI ===
    print(f"\n  === 5. DIRSEK NOKTASI TANIMI ve EN OPTIMAL TF ===")
    print(f"""
  TF buyudukce G artar (dogal). Ama artis hizi her TF'de ayni degil.

  TANIMLAR:

  (A) KLASIK DIRSEK: G artis hizinin ilk kez NEGATIFE dondugu TF.
      -> G(TF_n+1) < G(TF_n)
      -> Cok nadir olay, her coinde olmayabilir.

  (B) VERIMLILIK DIRSEGI: TF_dk/G oraninin EN YUKSEK oldugu TF.
      -> "Birim G basi en fazla zaman" = dalgalanma en yavas.
      -> Genelde en buyuk TF (1w) olur -- cok uzun vadeli.

  (C) ELASTISITE DIRSEGI: Elastisitenin en DUSUK oldugu TF gecisi.
      -> "TF artiyor ama G neredeyse artmiyor" = marjinal fayda dusuk.
      -> EN ONEMLI DIRSEK NOKTASI -- trade icin optimal TF.

  (D) G/VTF DIRSEGI: G/VTF degerinin trend degistirdigi TF.
      -> Random walk'tan sapma noktasi.

  (E) MARJINAL G: Bir sonraki TF'ye gecince kazanilan EK G miktari.
      -> DG/DTF oraninin en hizli dustugu yer = azalan getiri noktasi.
  """)

    for symbol, rows in all_data.items():
        print(f"\n  {symbol}:")
        # (A) Klasik dirsek
        neg = [r for r in rows[:-1] if r["g_growth"] < 0]
        if neg:
            print(f"    (A) Klasik Dirsek     : {neg[0]['tf']} (G={neg[0]['G']:.3f}%)")
        else:
            print(f"    (A) Klasik Dirsek     : YOK")

        # (B) Verimlilik
        mv = max(rows, key=lambda r: r["efficiency"])
        print(f"    (B) Max Verimlilik    : {mv['tf']} (dk/G={mv['efficiency']:.1f})")

        # (C) Elastisite dirsegi
        elast_rows = [(r, r["elasticity"]) for r in rows[:-1] if r["elasticity"] > 0]
        if elast_rows:
            min_e = min(elast_rows, key=lambda x: x[1])
            print(f"    (C) Elastisite Dirsek : {min_e[0]['tf']} -> sonraki (e={min_e[1]:.3f})")

        # (D) G/VTF trend degisimi
        g_sqrt_vals = [r["g_per_sqrt_tf"] for r in rows]
        # Artan->azalan gecis noktasi
        for j in range(1, len(g_sqrt_vals) - 1):
            if g_sqrt_vals[j] > g_sqrt_vals[j-1] and g_sqrt_vals[j] > g_sqrt_vals[j+1]:
                print(f"    (D) G/VTF Tepe       : {rows[j]['tf']} (G/VTF={g_sqrt_vals[j]:.4f})")
                break

        # (E) Marjinal G dususu
        if len(rows) >= 3:
            marginal = []
            for j in range(len(rows) - 1):
                dg = rows[j+1]["G"] - rows[j]["G"]
                dtf = rows[j+1]["tf_min"] - rows[j]["tf_min"]
                marg = dg / dtf if dtf > 0 else 0
                marginal.append((rows[j]["tf"], marg))
            if len(marginal) >= 2:
                # En hizli dusus
                max_drop = marginal[0]
                for j in range(1, len(marginal)):
                    if marginal[j][1] < marginal[j-1][1] and (marginal[j-1][1] - marginal[j][1]) > (max_drop[1] if isinstance(max_drop, tuple) else 0):
                        max_drop_ratio = marginal[j-1][1] - marginal[j][1]
                        max_drop = (marginal[j][0], marginal[j][1], max_drop_ratio)
                if isinstance(max_drop, tuple) and len(max_drop) == 3:
                    print(f"    (E) Max Marjinal Dusus: {max_drop[0]} (marj_G/dk={max_drop[1]:.6f})")


def main():
    print("=" * 140)
    print("  TF-G KORELASYON ANALIZI -- GERCEK BINANCE VERILERI")
    print("  14 Timeframe x 7 Coin = 98 Veri Noktasi")
    print("=" * 140)

    all_data = {}
    for symbol in COINS:
        print(f"\n  Fetching {symbol}...", end="", flush=True)
        rows = analyze_coin(symbol)
        if rows:
            all_data[symbol] = print_coin_analysis(symbol, rows)
        print(f"  done ({len(rows)} TFs)")

    if all_data:
        cross_coin_correlation(all_data)


if __name__ == "__main__":
    main()
