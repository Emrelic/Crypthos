"""
TF-G Analiz Scripti: Her coin icin tum TF'lerde zigzag dalga analizi.
Dirsek, verimlilik, min G/dk noktalarini tespit eder.
"""
import sys
import time
import numpy as np
import requests

# Add project root to path
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


def fetch_klines(symbol: str, interval: str, limit: int = 200) -> list:
    params = {"symbol": symbol, "interval": interval, "limit": limit}
    for attempt in range(3):
        try:
            resp = requests.get(API_URL, params=params, timeout=15)
            if resp.status_code == 429:
                print(f"  Rate limited, waiting 10s...")
                time.sleep(10)
                continue
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            if attempt < 2:
                time.sleep(2)
            else:
                print(f"  ERROR fetching {symbol} {interval}: {e}")
                return []
    return []


def analyze_coin(symbol: str):
    print(f"\n{'='*120}")
    print(f"  {symbol}")
    print(f"{'='*120}")

    results = []

    for tf_name, tf_min in TIMEFRAMES:
        klines = fetch_klines(symbol, tf_name)
        time.sleep(0.15)  # rate limit

        if not klines or len(klines) < 30:
            results.append(None)
            continue

        highs = np.array([float(k[2]) for k in klines])
        lows = np.array([float(k[3]) for k in klines])
        closes = np.array([float(k[4]) for k in klines])
        current_price = closes[-1]

        swings = detect_zigzag_swings(highs, lows, n=10)
        wave = analyze_waves(swings, current_price)

        results.append({
            "tf": tf_name,
            "tf_min": tf_min,
            "G": wave.G,
            "I": wave.I,
            "wave_count": len(wave.forward_waves) + len(wave.backward_waves),
            "cv": wave.cv,
            "swings": len(swings),
        })

    # Calculate derived metrics
    valid = [r for r in results if r is not None and r["G"] > 0]

    if not valid:
        print("  No valid data!")
        return

    for r in valid:
        r["efficiency"] = r["tf_min"] / r["G"] if r["G"] > 0 else 0
        r["g_per_min"] = r["G"] / r["tf_min"] if r["tf_min"] > 0 else 0
        r["g2_per_min"] = (r["G"] ** 2) / r["tf_min"] if r["tf_min"] > 0 else 0

    # G growth rate (between consecutive valid entries)
    for i, r in enumerate(valid):
        if i < len(valid) - 1:
            next_g = valid[i + 1]["G"]
            r["g_growth"] = (next_g - r["G"]) / r["G"] if r["G"] > 0 else 0
        else:
            r["g_growth"] = None

    # Print table
    header = f"{'TF':>5} {'TF_min':>7} {'G%':>8} {'I%':>8} {'Waves':>6} {'CV':>6} {'Eff':>8} {'G_growth':>9} {'G/min':>8} {'G2/min':>8}"
    print(f"\n{header}")
    print("-" * len(header))

    for r in valid:
        g_growth_str = f"{r['g_growth']:+.4f}" if r['g_growth'] is not None else "   N/A"
        print(f"{r['tf']:>5} {r['tf_min']:>7} {r['G']:>8.4f} {r['I']:>8.4f} "
              f"{r['wave_count']:>6} {r['cv']:>6.3f} {r['efficiency']:>8.2f} "
              f"{g_growth_str:>9} {r['g_per_min']:>8.5f} {r['g2_per_min']:>8.5f}")

    # Analysis points
    print()

    # DIRSEK: first TF where G growth rate becomes negative
    dirsek = None
    for r in valid:
        if r["g_growth"] is not None and r["g_growth"] < 0:
            dirsek = r
            break
    if dirsek:
        print(f"  DIRSEK (first negative G growth): {dirsek['tf']} (G={dirsek['G']:.4f}%, growth={dirsek['g_growth']:+.4f})")
    else:
        print(f"  DIRSEK: Not found (G always increasing)")

    # VERIMLILIK: highest efficiency (TF_min / G)
    best_eff = max(valid, key=lambda r: r["efficiency"])
    print(f"  VERIMLILIK (max TF/G): {best_eff['tf']} (efficiency={best_eff['efficiency']:.2f}, G={best_eff['G']:.4f}%)")

    # MIN_G_PER_MIN: lowest G per minute
    min_gpm = min(valid, key=lambda r: r["g_per_min"])
    print(f"  MIN_G_PER_MIN: {min_gpm['tf']} (G/min={min_gpm['g_per_min']:.6f}, G={min_gpm['G']:.4f}%)")

    # Most rapidly declining G growth rate
    declining = [(i, valid[i]) for i in range(len(valid))
                 if valid[i]["g_growth"] is not None and i > 0 and valid[i-1]["g_growth"] is not None]
    if declining:
        most_declining = min(declining, key=lambda x: x[1]["g_growth"] - valid[x[0]-1]["g_growth"])
        idx, r = most_declining
        delta = r["g_growth"] - valid[idx-1]["g_growth"]
        print(f"  MOST RAPID DECLINE: {r['tf']} (growth={r['g_growth']:+.4f}, delta={delta:+.4f})")
    else:
        print(f"  MOST RAPID DECLINE: Not enough data")


if __name__ == "__main__":
    print("TF-G DALGA ANALIZI")
    print(f"Coins: {', '.join(COINS)}")
    print(f"Timeframes: {len(TIMEFRAMES)}")
    print(f"Candles per TF: 200")

    for coin in COINS:
        analyze_coin(coin)

    print(f"\n{'='*120}")
    print("ANALIZ TAMAMLANDI")
