"""System H Backtest CLI — Hibrit sistem backtesti.

Zoom Diyafram + G bazli SL/TP + ER+Hurst rejim + A skorlama simulasyonu.

Kullanim:
    python -m backtest.cli_system_h [--days 30] [--coins 20] [--verbose]
"""
import sys
import time
import argparse
import numpy as np
from datetime import datetime, timedelta, timezone

from backtest.data_fetcher import fetch_klines, get_top_symbols
from backtest.simulator import simulate_position
from scanner.system_b_scanner import (
    detect_zigzag_swings, analyze_waves,
    compute_efficiency_ratio, compute_hurst_exponent,
)

# Zoom TF merdiveni
ZOOM_TFS = ["5m", "15m", "30m", "1h", "2h", "4h"]
TF_MINUTES = {"5m": 5, "15m": 15, "30m": 30, "1h": 60, "2h": 120, "4h": 240,
              "8h": 480, "12h": 720, "1d": 1440}

# System H default params
DEFAULT_SH = {
    "swing_n": 10,
    "min_wave_count": 4,
    "max_cv": 1.5,
    "sl_mult_trend": 1.5,
    "sl_mult_ranging": 2.0,
    "liq_mult_trend": 3.0,
    "liq_mult_ranging": 4.0,
    "liq_seviyesi": 0.7,
    "fee_pct": 0.08,
    "slippage_pct": 0.03,
    "fee_rate": 0.0004,
    "max_leverage": 20,
    "trailing_trigger_g_mult": 2.5,
    "trailing_callback_g_mult": 0.5,
    "ranging_tp_g_mult": 3.0,
    "er_macro_ranging": 0.15,
    "er_macro_trending": 0.35,
    "er_micro_ranging": 0.20,
    "er_micro_trending": 0.40,
    "hurst_ranging": 0.45,
    "hurst_trending": 0.55,
    "emergency_liq_pct": 80,
}


def _zoom_diyafram(klines_by_tf: dict, sh: dict) -> dict:
    """Simplified Zoom Diyafram for backtest."""
    swing_n = sh["swing_n"]
    fee_total = sh["fee_pct"] + sh["slippage_pct"]
    liq_seviye = sh["liq_seviyesi"]
    sl_mult = sh["sl_mult_trend"]
    liq_mult = sh["liq_mult_trend"]
    min_lev = 2

    tf_results = []
    for tf_name in ZOOM_TFS:
        klines = klines_by_tf.get(tf_name, [])
        if not klines or len(klines) < swing_n * 3:
            continue
        highs = np.array([float(k[2]) for k in klines])
        lows = np.array([float(k[3]) for k in klines])
        closes = np.array([float(k[4]) for k in klines])

        swings = detect_zigzag_swings(highs, lows, swing_n)
        if len(swings) < 3:
            continue
        wave = analyze_waves(swings, closes[-1])
        G = wave.G
        if G < 0.001:
            continue

        teorik_liq = (G * liq_mult + fee_total) / liq_seviye
        leverage = int(100.0 / teorik_liq) if teorik_liq > 0 else 1
        leverage = max(1, leverage)
        wave_count = len(wave.backward_waves) + len(wave.forward_waves)

        tf_results.append({
            "tf": tf_name, "G": G, "I": wave.I, "cv": wave.cv,
            "leverage": leverage, "wave_count": wave_count,
            "forward_waves": wave.forward_waves,
            "backward_waves": wave.backward_waves,
        })

    if not tf_results:
        return None

    # Dirsek: min kaldirac gecen TF'ler arasindan G artisi en az olan
    eligible = [t for t in tf_results if t["leverage"] >= min_lev]
    if not eligible:
        eligible = sorted(tf_results, key=lambda t: -t["leverage"])
    best = eligible[0]  # en dusuk TF (en yuksek kaldirac)

    return best


def _detect_regime(klines_1h: list, klines_5m: list, sh: dict) -> str:
    """ER + Hurst rejim tespiti."""
    regime = "TRENDING"

    if klines_1h and len(klines_1h) >= 20:
        closes = np.array([float(k[4]) for k in klines_1h])
        er_macro = compute_efficiency_ratio(closes)
        if er_macro < sh["er_macro_ranging"]:
            regime = "RANGING"
        elif er_macro < sh["er_macro_trending"]:
            regime = "GRAY"

    if klines_5m and len(klines_5m) >= 128:
        closes = np.array([float(k[4]) for k in klines_5m])
        hurst = compute_hurst_exponent(closes)
        if hurst < sh["hurst_ranging"] and regime != "RANGING":
            regime = "GRAY"  # celisiyor

    return regime


def _compute_pwin(forward_pcts, retrace_pcts, tp_pct, sl_pct):
    """P(win)/EV hesapla."""
    if not forward_pcts or not retrace_pcts:
        return 0.5, 0.0

    tp_hits = sum(1 for f in forward_pcts if f >= tp_pct)
    p_fwd = tp_hits / len(forward_pcts)
    sl_hits = sum(1 for r in retrace_pcts if r >= sl_pct)
    p_ret = sl_hits / len(retrace_pcts)

    p_win = p_fwd
    p_loss = (1 - p_fwd) * p_ret
    denom = p_win + p_loss
    if denom <= 0:
        return 0.5, 0.0

    pw = p_win / denom
    ev = pw * tp_pct - (1 - pw) * sl_pct
    return pw, ev


def run():
    parser = argparse.ArgumentParser(description="System H Backtest")
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--coins", type=int, default=20)
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    sh = DEFAULT_SH.copy()
    now = datetime.now(timezone.utc)
    end_ms = int(now.timestamp() * 1000)
    start_ms = int((now - timedelta(days=args.days)).timestamp() * 1000)

    print(f"=== System H Backtest ===")
    print(f"Donem: {args.days} gun | Coin: {args.coins} | Max lev: {sh['max_leverage']}x")
    print()

    # 1. Top coins
    symbols = get_top_symbols(args.coins)
    print(f"Top {len(symbols)} coin alindi")

    # 2. Fetch data
    all_data = {}
    for i, sym in enumerate(symbols):
        all_data[sym] = {}
        for tf in ZOOM_TFS + ["1h"]:
            warmup = 200 * TF_MINUTES.get(tf, 5) * 60 * 1000
            klines = fetch_klines(sym, tf, start_ms - warmup, end_ms)
            all_data[sym][tf] = klines
            time.sleep(0.05)
        print(f"  [{i+1}/{len(symbols)}] {sym}: {sum(len(v) for v in all_data[sym].values())} mum")

    # 3. Rolling window analysis
    check_interval_ms = 60 * 60 * 1000  # 1 saat arayla kontrol
    check_time = start_ms
    signals = []

    print(f"\nAnaliz basliyor...")
    total_checks = 0

    while check_time < end_ms:
        total_checks += 1
        for sym in symbols:
            klines_by_tf = {}
            for tf in ZOOM_TFS:
                kl = all_data[sym].get(tf, [])
                # check_time'a kadar olan mumlar
                klines_by_tf[tf] = [k for k in kl if int(k[0]) <= check_time]

            # Zoom diyafram
            zoom = _zoom_diyafram(klines_by_tf, sh)
            if not zoom:
                continue
            if zoom["wave_count"] < sh["min_wave_count"]:
                continue
            if zoom["cv"] > sh["max_cv"]:
                continue

            G = zoom["G"]
            if G < 0.01:
                continue

            # Rejim
            kl_1h = [k for k in all_data[sym].get("1h", []) if int(k[0]) <= check_time]
            kl_5m = [k for k in all_data[sym].get("5m", []) if int(k[0]) <= check_time]
            regime = _detect_regime(kl_1h, kl_5m, sh)

            # Basit yon tespiti: 5m EMA 9/21
            if not kl_5m or len(kl_5m) < 25:
                continue
            closes = np.array([float(k[4]) for k in kl_5m[-25:]])
            ema_fast = np.mean(closes[-9:])
            ema_slow = np.mean(closes[-21:])
            if ema_fast > ema_slow * 1.001:
                direction = "LONG"
            elif ema_fast < ema_slow * 0.999:
                direction = "SHORT"
            else:
                continue

            # Kaldirac
            if regime == "TRENDING":
                sl_mult = sh["sl_mult_trend"]
                liq_mult = sh["liq_mult_trend"]
            else:
                sl_mult = sh["sl_mult_ranging"]
                liq_mult = sh["liq_mult_ranging"]

            fee_total = sh["fee_pct"] + sh["slippage_pct"]
            sl_pct = G * sl_mult
            teorik_liq = (G * liq_mult + fee_total) / sh["liq_seviyesi"]
            leverage = min(sh["max_leverage"], int(100.0 / teorik_liq)) if teorik_liq > 0 else 1
            leverage = max(1, leverage)

            if leverage < 2:
                continue

            # Trailing/TP
            if regime in ("TRENDING", "GRAY"):
                trailing_trigger = G * sh["trailing_trigger_g_mult"]
                trailing_callback = G * sh["trailing_callback_g_mult"]
                trailing_callback = max(0.1, min(trailing_callback, 5.0))
            else:
                trailing_trigger = G * sh["ranging_tp_g_mult"]
                trailing_callback = 0.3  # ranging'de sabit

            # P(win)/EV
            pw, ev = _compute_pwin(
                zoom.get("forward_waves", []),
                zoom.get("backward_waves", []),
                trailing_trigger, sl_pct)

            # Emergency
            emg_pct = (1 / leverage) * 100 * sh["liq_seviyesi"] * (sh["emergency_liq_pct"] / 100)

            entry_price = float(kl_5m[-1][4])

            # Forward simulate
            forward_5m = [k for k in all_data[sym].get("5m", []) if int(k[0]) > check_time]
            if not forward_5m:
                continue

            exit_reason, exit_price, bars, roi_net, roi_gross = simulate_position(
                direction, entry_price, sl_pct, emg_pct,
                trailing_trigger, trailing_callback,
                leverage, sh["fee_rate"], forward_5m[:96])

            signals.append({
                "symbol": sym, "direction": direction,
                "regime": regime, "G": G,
                "leverage": leverage, "sl_pct": sl_pct,
                "trailing_trigger": trailing_trigger,
                "trailing_callback": trailing_callback,
                "p_win": pw, "ev": ev,
                "entry_price": entry_price,
                "exit_reason": exit_reason,
                "roi_net": roi_net,
                "bars": bars,
                "time": check_time,
            })

        check_time += check_interval_ms

    # 4. Results
    print(f"\n{'='*60}")
    print(f"SONUCLAR: {len(signals)} sinyal / {total_checks} kontrol")
    print(f"{'='*60}")

    if not signals:
        print("Hicbir sinyal uretilmedi.")
        return

    wins = [s for s in signals if s["roi_net"] > 0]
    losses = [s for s in signals if s["roi_net"] <= 0]
    total_roi = sum(s["roi_net"] for s in signals)
    avg_roi = total_roi / len(signals)
    win_rate = len(wins) / len(signals) * 100

    print(f"Toplam ROI:  {total_roi:+.2f}%")
    print(f"Ort ROI:     {avg_roi:+.2f}%")
    print(f"Win Rate:    {win_rate:.1f}% ({len(wins)}/{len(signals)})")
    print(f"Max Win:     {max(s['roi_net'] for s in signals):+.2f}%")
    print(f"Max Loss:    {min(s['roi_net'] for s in signals):+.2f}%")
    print()

    # Exit reason breakdown
    reasons = {}
    for s in signals:
        r = s["exit_reason"]
        reasons[r] = reasons.get(r, 0) + 1
    print("Cikis nedenleri:")
    for r, c in sorted(reasons.items(), key=lambda x: -x[1]):
        print(f"  {r}: {c} ({c/len(signals)*100:.0f}%)")

    # Regime breakdown
    print("\nRejim dagilimi:")
    for regime in ["TRENDING", "RANGING", "GRAY"]:
        rs = [s for s in signals if s["regime"] == regime]
        if rs:
            roi = sum(s["roi_net"] for s in rs)
            wr = sum(1 for s in rs if s["roi_net"] > 0) / len(rs) * 100
            print(f"  {regime}: {len(rs)} sinyal, ROI={roi:+.2f}%, WR={wr:.0f}%")

    # Top trades
    if args.verbose:
        print(f"\n{'='*60}")
        print("Top 10 islem:")
        for s in sorted(signals, key=lambda x: -x["roi_net"])[:10]:
            print(f"  {s['symbol']} {s['direction']} {s['regime']} "
                  f"G={s['G']:.3f}% lev={s['leverage']}x "
                  f"SL={s['sl_pct']:.2f}% → {s['exit_reason']} "
                  f"ROI={s['roi_net']:+.2f}% P(w)={s['p_win']:.2f}")

        print(f"\nEn kotu 10 islem:")
        for s in sorted(signals, key=lambda x: x["roi_net"])[:10]:
            print(f"  {s['symbol']} {s['direction']} {s['regime']} "
                  f"G={s['G']:.3f}% lev={s['leverage']}x "
                  f"SL={s['sl_pct']:.2f}% → {s['exit_reason']} "
                  f"ROI={s['roi_net']:+.2f}% P(w)={s['p_win']:.2f}")


if __name__ == "__main__":
    run()
