"""
BTCUSDT Detayli TF-G Analizi
Her TF icin G, kaldirac, verimlilik ve tum onemli metrikleri goster.
"""
import sys
import time
import math
import numpy as np
import requests

PROJECT_ROOT = r"C:\Users\ikizler1\AndroidStudioProjects\Tasking\Crypthos"
sys.path.insert(0, PROJECT_ROOT)

from scanner.system_b_scanner import detect_zigzag_swings, analyze_waves

TIMEFRAMES = [
    ("1m", 1), ("3m", 3), ("5m", 5), ("15m", 15), ("30m", 30),
    ("1h", 60), ("2h", 120), ("4h", 240), ("6h", 360), ("8h", 480),
    ("12h", 720), ("1d", 1440), ("3d", 4320), ("1w", 10080),
]

API_URL = "https://fapi.binance.com/fapi/v1/klines"
SWING_N = 10

# Kaldirac hesabi (System I formulu)
FEE_PCT = 0.08       # round-trip fee %
SLIPPAGE_PCT = 0.04   # slippage %
LIQ_SAFETY = 0.7      # likidasyon guvenlik carpani


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


def calc_leverage(G, regime="TREND"):
    """G'den kaldirac hesapla (System I formulu)."""
    if G < 0.01:
        return 0, 0, 0, 0
    fee_total = FEE_PCT + SLIPPAGE_PCT
    if regime == "TREND":
        sl_mult, liq_mult = 1.5, 3.0
    else:
        sl_mult, liq_mult = 2.0, 4.0
    sl_pct = G * sl_mult + fee_total
    pratik_liq = G * liq_mult
    teorik_liq = (pratik_liq + FEE_PCT) / LIQ_SAFETY
    leverage = 100.0 / teorik_liq if teorik_liq > 0 else 0
    leverage = max(1, min(int(leverage), 125))
    return sl_pct, pratik_liq, teorik_liq, leverage


def main():
    symbol = "BTCUSDT"
    print(f"Fetching {symbol} data from Binance...\n")

    rows = []
    for tf_name, tf_min in TIMEFRAMES:
        klines = fetch_klines(symbol, tf_name, 200)
        if not klines or len(klines) < SWING_N * 3:
            continue
        time.sleep(0.2)

        highs = np.array([float(k[2]) for k in klines])
        lows = np.array([float(k[3]) for k in klines])
        closes = np.array([float(k[4]) for k in klines])

        swings = detect_zigzag_swings(highs, lows, SWING_N)
        if len(swings) < 3:
            continue

        wave = analyze_waves(swings, closes[-1])
        G = wave.G
        I_val = wave.I
        if G < 0.0001:
            continue

        wc = len(wave.backward_waves) + len(wave.forward_waves)
        bw_count = len(wave.backward_waves)
        fw_count = len(wave.forward_waves)
        cv = wave.cv

        sl_pct, pratik_liq, teorik_liq, leverage = calc_leverage(G)

        rows.append({
            "tf": tf_name,
            "tf_min": tf_min,
            "G": G,
            "I": I_val,
            "wc": wc,
            "bw": bw_count,
            "fw": fw_count,
            "cv": cv,
            "sl_pct": sl_pct,
            "leverage": leverage,
            "pratik_liq": pratik_liq,
            "teorik_liq": teorik_liq,
        })

    if not rows:
        print("Veri alinamadi!")
        return

    # Turetilmis metrikler
    for i, r in enumerate(rows):
        r["g_per_tf"] = r["G"] / r["tf_min"]                     # G / TF (dakika basina G)
        r["tf_per_g"] = r["tf_min"] / r["G"]                     # TF / G (verimlilik)
        r["g_per_sqrt"] = r["G"] / math.sqrt(r["tf_min"])        # G / sqrt(TF)

        if i > 0:
            prev = rows[i-1]
            # G artis orani
            r["g_artis"] = (r["G"] - prev["G"]) / prev["G"] * 100
            # TF artis orani
            r["tf_artis"] = (r["tf_min"] - prev["tf_min"]) / prev["tf_min"] * 100
            # G/TF oran (TF %X artinca G %Y artti, Y/X)
            r["g_tf_oran"] = r["g_artis"] / r["tf_artis"] if r["tf_artis"] > 0 else 0
            # Marjinal G (ek TF basina ek G)
            r["marjinal_g"] = (r["G"] - prev["G"]) / (r["tf_min"] - prev["tf_min"])
        else:
            r["g_artis"] = 0
            r["tf_artis"] = 0
            r["g_tf_oran"] = 0
            r["marjinal_g"] = r["G"] / r["tf_min"]

    # =============== ANA TABLO ===============
    print("=" * 160)
    print(f"  BTCUSDT -- DETAYLI TF-G ANALIZI ({len(rows)} Timeframe)")
    print("=" * 160)

    print(f"\n  TABLO 1: TEMEL VERILER")
    print(f"  {'-'*120}")
    print(f"  {'TF':>4s} | {'TF(dk)':>6s} | {'G%':>7s} | {'I%':>7s} | {'G/I':>5s} | "
          f"{'Geri':>4s} | {'Ileri':>5s} | {'Top':>3s} | {'CV':>5s} | "
          f"{'SL%':>6s} | {'Lev':>4s} | {'Liq%':>6s}")
    print(f"  {'-'*120}")
    for r in rows:
        gi_ratio = r["G"] / r["I"] if r["I"] > 0 else 0
        print(f"  {r['tf']:>4s} | {r['tf_min']:>6d} | {r['G']:>7.3f} | {r['I']:>7.3f} | "
              f"{gi_ratio:>5.2f} | "
              f"{r['bw']:>4d} | {r['fw']:>5d} | {r['wc']:>3d} | {r['cv']:>5.2f} | "
              f"{r['sl_pct']:>6.2f} | {r['leverage']:>3d}x | {r['teorik_liq']:>6.2f}")

    # =============== VERIMLILIK TABLOSU ===============
    print(f"\n  TABLO 2: VERIMLILIK METRIKLERI")
    print(f"  {'-'*140}")
    print(f"  {'TF':>4s} | {'G%':>7s} | {'Lev':>4s} | "
          f"{'G/TF':>9s} | {'TF/G':>7s} | {'G/vTF':>7s} | "
          f"{'TF art%':>7s} | {'G art%':>7s} | {'G/TF or':>7s} | {'Marj.G':>9s} | {'Yorum':>30s}")
    print(f"  {'-'*140}")

    for i, r in enumerate(rows):
        if i == 0:
            tf_art = "---"
            g_art = "---"
            oran = "---"
            marj = f"{r['marjinal_g']:.5f}"
            yorum = ""
        else:
            tf_art = f"+{r['tf_artis']:.0f}%"
            g_art = f"{r['g_artis']:+.0f}%"
            oran = f"{r['g_tf_oran']:.3f}"
            marj = f"{r['marjinal_g']:.5f}"

            ratio = r["g_tf_oran"]
            if ratio < 0:
                yorum = "*** G AZALDI (TF artmasina ragmen)"
            elif ratio < 0.15:
                yorum = "*** COK VERIMLI (G neredeyse artmadi)"
            elif ratio < 0.30:
                yorum = "**  VERIMLI"
            elif ratio < 0.50:
                yorum = "*   IYI"
            elif ratio < 0.80:
                yorum = "    NORMAL"
            elif ratio < 1.0:
                yorum = "    VERIMSIZ (G TF kadar artiyor)"
            else:
                yorum = "    COK VERIMSIZ (G > TF artti)"

        print(f"  {r['tf']:>4s} | {r['G']:>7.3f} | {r['leverage']:>3d}x | "
              f"{r['g_per_tf']:>9.5f} | {r['tf_per_g']:>7.1f} | {r['g_per_sqrt']:>7.4f} | "
              f"{tf_art:>7s} | {g_art:>7s} | {oran:>7s} | {marj:>9s} | {yorum}")

    # =============== KALDIRAC TABLOSU ===============
    print(f"\n  TABLO 3: KALDIRAC DETAYI (SL = 1.5 x G + fee)")
    print(f"  {'-'*110}")
    print(f"  {'TF':>4s} | {'G%':>7s} | {'SL%':>7s} | {'P.Liq%':>7s} | {'T.Liq%':>7s} | "
          f"{'Lev':>5s} | {'1$ -> ROI':>10s} | {'SL$/1$':>7s} | {'Yorum':>35s}")
    print(f"  {'-'*110}")
    for r in rows:
        # 1$ marjin ile kac $ ROI (TP = 2.5G)
        tp_pct = r["G"] * 2.5
        roi_per_dollar = tp_pct * r["leverage"] / 100
        # 1$ marjin ile SL'de kac $ kayip
        sl_loss = r["sl_pct"] * r["leverage"] / 100

        if r["leverage"] >= 20:
            yorum = "YUKSEK KALDIRAC (scalp/kisa vade)"
        elif r["leverage"] >= 10:
            yorum = "ORTA KALDIRAC (swing trade)"
        elif r["leverage"] >= 5:
            yorum = "DUSUK KALDIRAC (pozisyon trade)"
        elif r["leverage"] >= 2:
            yorum = "COK DUSUK (uzun vade/hodl)"
        else:
            yorum = "KALDIRAC YOK (spot gibi)"

        print(f"  {r['tf']:>4s} | {r['G']:>7.3f} | {r['sl_pct']:>7.2f} | {r['pratik_liq']:>7.2f} | "
              f"{r['teorik_liq']:>7.2f} | {r['leverage']:>4d}x | "
              f"${roi_per_dollar:>8.2f} | ${sl_loss:>5.2f} | {yorum}")

    # =============== SONUC ===============
    print(f"\n  {'='*100}")
    print(f"  SONUC: FARKLI KRITERLERE GORE OPTIMAL TF")
    print(f"  {'='*100}")

    # (1) G/TF orani en dusuk (yeterli dalga olan)
    valid = [r for r in rows[1:] if r["wc"] >= 4 and r["g_tf_oran"] > 0]
    if valid:
        best1 = min(valid, key=lambda r: r["g_tf_oran"])
        print(f"\n  [1] EN VERIMLI GECIS (G/TF oran en dusuk, WC>=4):")
        print(f"      TF = {best1['tf']},  G = {best1['G']:.3f}%,  Lev = {best1['leverage']}x")
        print(f"      TF {best1['tf_artis']:+.0f}% artinca G sadece {best1['g_artis']:+.0f}% artti (oran={best1['g_tf_oran']:.3f})")

    # (2) G azalan noktalar
    neg = [r for r in rows[1:] if r["g_artis"] < 0 and r["wc"] >= 4]
    if neg:
        print(f"\n  [2] G AZALAN NOKTALAR (TF artmasina ragmen G dusmus!):")
        for n in neg:
            print(f"      TF = {n['tf']},  G = {n['G']:.3f}%,  Lev = {n['leverage']}x,  "
                  f"G degisim = {n['g_artis']:+.1f}%")

    # (3) Kaldirac >= 10x olan en yuksek TF
    high_lev = [r for r in rows if r["leverage"] >= 10]
    if high_lev:
        best3 = max(high_lev, key=lambda r: r["tf_min"])
        print(f"\n  [3] KALDIRAC >= 10x OLAN EN YUKSEK TF:")
        print(f"      TF = {best3['tf']},  G = {best3['G']:.3f}%,  Lev = {best3['leverage']}x")

    # (4) Kaldirac >= 5x olan en verimli gecis
    lev5 = [r for r in rows[1:] if r["leverage"] >= 5 and r["g_tf_oran"] > 0]
    if lev5:
        best4 = min(lev5, key=lambda r: r["g_tf_oran"])
        print(f"\n  [4] KALDIRAC >= 5x + EN VERIMLI GECIS:")
        print(f"      TF = {best4['tf']},  G = {best4['G']:.3f}%,  Lev = {best4['leverage']}x")
        print(f"      TF {best4['tf_artis']:+.0f}% artinca G sadece {best4['g_artis']:+.0f}% artti")

    # (5) Kaldirac >= 3x olan en verimli gecis
    lev3 = [r for r in rows[1:] if r["leverage"] >= 3 and r["g_tf_oran"] > 0]
    if lev3:
        best5 = min(lev3, key=lambda r: r["g_tf_oran"])
        print(f"\n  [5] KALDIRAC >= 3x + EN VERIMLI GECIS:")
        print(f"      TF = {best5['tf']},  G = {best5['G']:.3f}%,  Lev = {best5['leverage']}x")
        print(f"      TF {best5['tf_artis']:+.0f}% artinca G sadece {best5['g_artis']:+.0f}% artti")

    # (6) Mutlak en iyi: en yuksek ROI/Risk
    for r in rows:
        tp_pct = r["G"] * 2.5
        roi = tp_pct * r["leverage"] / 100
        sl_risk = r["sl_pct"] * r["leverage"] / 100
        r["roi_risk"] = roi / sl_risk if sl_risk > 0 else 0
        r["roi_dollar"] = roi

    best6 = max(rows, key=lambda r: r["roi_dollar"])
    print(f"\n  [6] EN YUKSEK ROI (1$ marjin ile max kazanc):")
    print(f"      TF = {best6['tf']},  G = {best6['G']:.3f}%,  Lev = {best6['leverage']}x")
    print(f"      TP = {best6['G']*2.5:.2f}%,  ROI = ${best6['roi_dollar']:.2f}/1$")

    print(f"\n  {'='*100}")
    print(f"  YORUM")
    print(f"  {'='*100}")
    print(f"""
  G/TF ORANI nasil okunur:
    oran = 0.10  ->  TF %100 artinca G sadece %10 artti (COK VERIMLI)
    oran = 0.50  ->  TF %100 artinca G %50 artti (NORMAL)
    oran = 1.00  ->  TF %100 artinca G %100 artti (VERIMSIZ, lineer)
    oran > 1.00  ->  G, TF'den hizli artiyor (COK VERIMSIZ)
    oran < 0     ->  G AZALDI! (nadir ama cok degerli)

  IDEAL DURUM:
    Oran dusuk + Kaldirac yuksek + Dalga sayisi yeterli
    = "TF uzamis ama G artmamis, kaldirac hala yuksek, dalgalar tutarli"
  """)


if __name__ == "__main__":
    main()
