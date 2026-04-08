"""Yeni compute_zoom algoritmasini gercek veriyle test et."""
import sys
import time
import numpy as np
import requests

PROJECT_ROOT = r"C:\Users\ikizler1\AndroidStudioProjects\Tasking\Crypthos"
sys.path.insert(0, PROJECT_ROOT)

from core.config_manager import ConfigManager
from scanner.system_i_scanner import (
    SystemIScanner, ZOOM_TF_LADDER, ZOOM_KLINE_LIMITS
)

API_URL = "https://fapi.binance.com/fapi/v1/klines"
COINS = ["BTCUSDT", "ETHUSDT", "XRPUSDT", "DOGEUSDT", "SOLUSDT", "AVAXUSDT", "ADAUSDT"]


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
    teorik_liq = (G * 3.0 + 0.08) / 0.7
    return max(1, min(int(100.0 / teorik_liq), 125))


def main():
    config = ConfigManager("config.json")
    scanner = SystemIScanner(config)

    print("=" * 130)
    print("  YENI ZOOM ALGORITMASI TESTI (gercek Binance verisi)")
    print("  Dinamik mum sayisi + Min dalga filtresi + G/TF verimlilik taramasi")
    print("=" * 130)

    for symbol in COINS:
        print(f"\n  Fetching {symbol}...", end="", flush=True)

        klines_by_tf = {}
        for tf_name, _ in ZOOM_TF_LADDER:
            limit = ZOOM_KLINE_LIMITS.get(tf_name, 200)
            klines = fetch_klines(symbol, tf_name, limit)
            if klines:
                klines_by_tf[tf_name] = klines
            time.sleep(0.15)
        print(f" {len(klines_by_tf)} TFs fetched")

        # compute_zoom cagir
        result = scanner.compute_zoom(symbol, klines_by_tf)

        # Sonuclari goster
        print(f"\n  {'='*125}")
        print(f"  {symbol} ZOOM SONUCU")
        print(f"  {'='*125}")

        if not result.all_tfs:
            print(f"  Yetersiz veri!")
            continue

        # Tum TF tablosu
        print(f"\n  {'TF':>4s} | {'dk':>5s} | {'G%':>7s} | {'I%':>7s} | "
              f"{'BW':>3s} | {'FW':>3s} | {'WC':>3s} | {'CV':>5s} | "
              f"{'Lev':>4s} | {'G/TF or':>7s} | {'Verim':>7s} | {'Durum':>20s}")
        print(f"  {'-'*120}")

        min_bw = config.get("system_i.timeframe.zoom_min_backward_waves", 10)
        for i, tf in enumerate(result.all_tfs):
            lev = calc_leverage(tf.G)
            is_optimal = (tf.tf == result.yon_tf)

            if tf.bw_count < min_bw:
                durum = f"YETERSIZ DALGA ({tf.bw_count}<{min_bw})"
            elif is_optimal:
                durum = ">>> OPTIMAL <<<"
            elif tf.g_tf_oran < 0:
                durum = "G AZALDI"
            elif tf.g_tf_oran < 0.30:
                durum = "COK VERIMLI"
            elif tf.g_tf_oran < 0.60:
                durum = "VERIMLI"
            elif tf.g_tf_oran < 0.80:
                durum = "KABUL"
            elif tf.g_tf_oran > 0 and i > 0:
                durum = "VERIMSIZ"
            else:
                durum = ""

            oran_str = f"{tf.g_tf_oran:.3f}" if i > 0 else "---"
            marker = ">>>" if is_optimal else "   "

            print(f"  {marker} {tf.tf:>4s} | {tf.minutes:>5d} | {tf.G:>7.3f} | {tf.I:>7.3f} | "
                  f"{tf.bw_count:>3d} | {tf.fw_count:>3d} | {tf.wave_count:>3d} | {tf.cv:>5.2f} | "
                  f"{lev:>3d}x | {oran_str:>7s} | {tf.verimlilik:>7.1f} | {durum}")

        # Sonuc
        sl_pct = result.optimal_G * 1.5 + 0.12
        lev = calc_leverage(result.optimal_G)
        tp_pct = result.optimal_G * 2.5
        roi = tp_pct * lev

        print(f"\n  SECILEN TF: {result.yon_tf}")
        print(f"    G      = {result.optimal_G:.3f}%")
        print(f"    I      = {result.optimal_I:.3f}%")
        print(f"    SL     = {sl_pct:.2f}% (1.5xG + fee)")
        print(f"    Lev    = {lev}x")
        print(f"    TP     = {tp_pct:.2f}% (2.5xG)")
        print(f"    ROI    = {roi:.1f}%")
        print(f"    Dalga  = {result.wave_count} (bw={result.all_tfs[result.dirsek_index].bw_count})")
        print(f"    CV     = {result.cv:.2f}")
        print(f"    Teyit  = {result.teyit_tf}")
        print(f"    Giris  = {result.giris_tf}")

    # OZET
    print(f"\n\n{'='*130}")
    print(f"  OZET")
    print(f"{'='*130}")
    print(f"  Yeni algoritma: 'Kaldirac koruyarak TF uzat, G patladigi anda dur'")
    print(f"  - TF'ye gore dinamik mum sayisi (1m:1500, 15m:500, 4h:200)")
    print(f"  - Min geri dalga filtresi (BW >= 10)")
    print(f"  - Alttan yukari G/TF oran taramasi (verimli iken devam, verimsiz iken dur)")


if __name__ == "__main__":
    main()
