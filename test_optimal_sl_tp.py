"""Optimal SL/TP sistemini gercek veriyle test et."""
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
            resp.raise_for_status()
            return resp.json()
        except Exception:
            if attempt < 2:
                time.sleep(2)
    return []


def main():
    config = ConfigManager("config.json")
    scanner = SystemIScanner(config)

    print("=" * 130)
    print("  OPTIMAL SL/TP SISTEMI TESTI")
    print("  Zoom -> G -> Coklu dalga simulasyonu -> Optimal SL/TP -> EV")
    print("=" * 130)

    summary = []

    for symbol in COINS:
        print(f"\n  Fetching {symbol}...", end="", flush=True)

        klines_by_tf = {}
        for tf_name, _ in ZOOM_TF_LADDER:
            limit = ZOOM_KLINE_LIMITS.get(tf_name, 200)
            klines = fetch_klines(symbol, tf_name, limit)
            if klines:
                klines_by_tf[tf_name] = klines
            time.sleep(0.12)
        print(f" {len(klines_by_tf)} TFs")

        # deep_analyze cagir (tam pipeline)
        result = scanner.deep_analyze(symbol, klines_by_tf, {})

        print(f"\n  {symbol}:")
        print(f"    Zoom TF   = {result.zoom.yon_tf}")
        print(f"    G         = {result.G:.3f}%")
        print(f"    Yon       = {result.direction}")
        print(f"    Rejim     = {result.regime.regime}")
        print(f"    Pool      = {result.pool}")

        if result.direction == "SKIP" or result.G < 0.01:
            print(f"    Reject    = {result.reject_reason}")
            continue

        prob = result.probability
        print(f"    --- SL/TP ---")
        print(f"    SL        = {result.sl_pct:.3f}% ({prob.optimal_sl_g_mult:.2f}xG)")
        print(f"    TP        = {result.tp_pct:.3f}% ({prob.optimal_tp_g_mult:.1f}xG)")
        print(f"    R:R       = {prob.optimal_rr:.2f}")
        print(f"    Kaldirac  = {result.leverage}x")
        print(f"    --- EV ---")
        print(f"    P(win)    = {prob.p_win:.0%}")
        print(f"    EV        = {prob.ev_pct:+.1f}%")
        print(f"    W/L/TO    = {prob.sim_wins}/{prob.sim_losses}/{prob.sim_timeouts}")
        print(f"    Sufficient= {prob.sufficient}")
        print(f"    --- Diger ---")
        print(f"    Eligible  = {result.eligible}")
        print(f"    Reject    = {result.reject_reason or '-'}")
        print(f"    Score     = {result.score:.1f}")

        if result.G > 0:
            summary.append({
                "symbol": symbol,
                "tf": result.zoom.yon_tf,
                "G": result.G,
                "sl_mult": prob.optimal_sl_g_mult,
                "tp_mult": prob.optimal_tp_g_mult,
                "rr": prob.optimal_rr,
                "p_win": prob.p_win,
                "ev": prob.ev_pct,
                "lev": result.leverage,
                "direction": result.direction,
                "eligible": result.eligible,
            })

    # OZET
    print(f"\n\n{'='*130}")
    print(f"  OZET")
    print(f"{'='*130}")
    print(f"\n  {'Coin':>10s} | {'TF':>4s} | {'Yon':>5s} | {'G%':>6s} | "
          f"{'SL(xG)':>7s} | {'TP(xG)':>7s} | {'R:R':>5s} | "
          f"{'P(win)':>6s} | {'Lev':>4s} | {'EV%':>6s} | {'Uygun':>5s}")
    print(f"  {'-'*90}")
    for s in summary:
        print(f"  {s['symbol']:>10s} | {s['tf']:>4s} | {s['direction']:>5s} | {s['G']:>6.3f} | "
              f"{s['sl_mult']:>5.2f}xG | {s['tp_mult']:>5.1f}xG | {s['rr']:>5.2f} | "
              f"{s['p_win']:>5.0%} | {s['lev']:>3d}x | {s['ev']:>+5.1f}% | "
              f"{'EVET' if s['eligible'] else 'HAYIR':>5s}")


if __name__ == "__main__":
    main()
