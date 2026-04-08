"""
FINAL TF SECIM ALGORITMASI
===========================
Mantik:
  1. En kucuk TF'den basla (en yuksek kaldirac)
  2. Yukari dogru cik
  3. Her geciste: "TF artisina karsilik G ne kadar artti?" bak
  4. G/TF orani < esik ise = VERIMLI gecis, devam et (TF uzat)
  5. G/TF orani >= esik ise = VERIMSIZ, DUR! Onceki TF optimal.

  Yani: "Kaldirac koruyarak TF'yi uzatabildigin kadar uzat,
         G patlamaya basladigi anda dur."
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
        return 0, 0
    fee_total = FEE_PCT + SLIPPAGE_PCT
    sl_pct = G * 1.5 + fee_total
    pratik_liq = G * 3.0
    teorik_liq = (pratik_liq + FEE_PCT) / LIQ_SAFETY
    leverage = 100.0 / teorik_liq if teorik_liq > 0 else 0
    leverage = max(1, min(int(leverage), 125))
    return sl_pct, leverage


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
        if G < 0.0001:
            continue
        wc = len(wave.backward_waves) + len(wave.forward_waves)
        sl_pct, leverage = calc_leverage(G)
        rows.append({
            "tf": tf_name, "tf_min": tf_min, "G": G, "I": wave.I,
            "wc": wc, "cv": wave.cv, "sl_pct": sl_pct, "leverage": leverage,
        })
    return rows


def find_optimal(symbol, rows):
    if len(rows) < 3:
        return None

    # Her gecis icin G/TF orani hesapla
    for i in range(len(rows)):
        if i == 0:
            rows[i]["g_artis_pct"] = 0
            rows[i]["tf_artis_pct"] = 0
            rows[i]["g_tf_oran"] = 0
        else:
            prev = rows[i-1]
            rows[i]["g_artis_pct"] = (rows[i]["G"] - prev["G"]) / prev["G"] * 100
            rows[i]["tf_artis_pct"] = (rows[i]["tf_min"] - prev["tf_min"]) / prev["tf_min"] * 100
            if rows[i]["tf_artis_pct"] > 0:
                rows[i]["g_tf_oran"] = rows[i]["g_artis_pct"] / rows[i]["tf_artis_pct"]
            else:
                rows[i]["g_tf_oran"] = 0

    # === TABLO ===
    print(f"\n{'='*145}")
    print(f"  {symbol}")
    print(f"{'='*145}")

    print(f"\n  {'TF':>4s} | {'dk':>5s} | {'G%':>7s} | {'SL%':>6s} | {'Lev':>4s} | "
          f"{'WC':>3s} | {'CV':>5s} | "
          f"{'TF art':>7s} | {'G art':>7s} | {'G/TF':>6s} | "
          f"{'Karar':>35s}")
    print(f"  {'-'*140}")

    # ALGORITMA: Asagidan yukari cik
    optimal_tf = rows[0]  # baslangic: en kucuk TF
    stopped = False

    for i, r in enumerate(rows):
        if i == 0:
            karar = "BASLANGIC (max kaldirac)"
            marker = ">>>"
        elif stopped:
            karar = ""
            marker = "   "
        else:
            oran = r["g_tf_oran"]
            g_azaldi = r["g_artis_pct"] < 0

            if g_azaldi:
                # G azaldi = bedava TF uzatma!
                optimal_tf = r
                karar = "*** G AZALDI! Bedava TF uzatma"
                marker = ">>>"
            elif oran < 0.30:
                # TF artti ama G cok az artti = verimli, devam
                optimal_tf = r
                karar = f"VERIMLI (G/{r['tf_artis_pct']:.0f}%TF = %{r['g_artis_pct']:.0f} artti)"
                marker = ">>>"
            elif oran < 0.60:
                # Kabul edilebilir, devam ama dikkatli
                optimal_tf = r
                karar = f"KABUL (oran={oran:.2f}, henuz verimli)"
                marker = " > "
            elif oran < 0.80:
                # Verimsizlesiyor, burada dur
                karar = f"SINIR (oran={oran:.2f}, verimsizlesiyor)"
                marker = " ! "
                # Durma, ama onceki optimal kalmali
                # Bir sonrakine de bak (belki duser)
            else:
                # Verimsiz: G, TF kadar veya daha fazla artti
                karar = f"DUR! VERIMSIZ (oran={oran:.2f})"
                marker = " X "
                if not stopped:
                    stopped = True

        lev_str = f"{r['leverage']:>3d}x"
        tf_art = f"+{r['tf_artis_pct']:.0f}%" if i > 0 else "---"
        g_art = f"{r['g_artis_pct']:+.0f}%" if i > 0 else "---"
        oran_str = f"{r['g_tf_oran']:.3f}" if i > 0 else "---"

        print(f"  {marker} {r['tf']:>4s} | {r['tf_min']:>5d} | {r['G']:>7.3f} | {r['sl_pct']:>6.2f} | {lev_str} | "
              f"{r['wc']:>3d} | {r['cv']:>5.2f} | "
              f"{tf_art:>7s} | {g_art:>7s} | {oran_str:>6s} | "
              f"{karar}")

    # Sonuc
    print(f"\n  OPTIMAL TF: {optimal_tf['tf']}")
    print(f"    G     = {optimal_tf['G']:.3f}%")
    print(f"    SL    = {optimal_tf['sl_pct']:.2f}%")
    print(f"    Lev   = {optimal_tf['leverage']}x")
    print(f"    Dalga = {optimal_tf['wc']}")
    print(f"    CV    = {optimal_tf['cv']:.2f}")
    tp = optimal_tf['G'] * 2.5
    print(f"    TP    = {tp:.2f}% (2.5xG)")
    roi = tp * optimal_tf['leverage']
    print(f"    ROI   = {roi:.1f}% (TP x Lev)")

    return optimal_tf


def main():
    print("=" * 145)
    print("  OPTIMAL TF SECIMI: Kaldirac once, verimlilik sonra")
    print("  En dusuk TF'den basla, G patlayana kadar TF uzat")
    print("=" * 145)

    results = {}
    for symbol in COINS:
        print(f"\n  Fetching {symbol}...", end="", flush=True)
        rows = analyze_coin(symbol)
        print(f" {len(rows)} TFs")
        if rows:
            opt = find_optimal(symbol, rows)
            if opt:
                results[symbol] = opt

    # OZET
    print(f"\n\n{'='*145}")
    print(f"  OZET TABLO: TUM COINLER")
    print(f"{'='*145}")
    print(f"\n  {'Coin':<12s} | {'Opt.TF':>6s} | {'G%':>7s} | {'SL%':>6s} | {'Lev':>5s} | "
          f"{'TP%':>6s} | {'ROI%':>6s} | {'WC':>3s} | {'CV':>5s}")
    print(f"  {'-'*75}")
    for symbol, opt in results.items():
        tp = opt['G'] * 2.5
        roi = tp * opt['leverage']
        print(f"  {symbol:<12s} | {opt['tf']:>6s} | {opt['G']:>7.3f} | {opt['sl_pct']:>6.2f} | "
              f"{opt['leverage']:>4d}x | {tp:>6.2f} | {roi:>6.1f} | "
              f"{opt['wc']:>3d} | {opt['cv']:>5.2f}")


if __name__ == "__main__":
    main()
