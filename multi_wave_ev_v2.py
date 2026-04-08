"""
Coklu Dalga P(win)/EV v2 — YON FILTRELI
=========================================
Onceki test: her noktadan koru korane LONG giriyorduk → cogu kayip.
Simdi: sadece yon teyidi olan noktalardan gir.

Yontem:
  1. Yon TF'de EMA9/21 + MACD + RSI ile yon belirle
  2. Sadece yon uyumlu swing noktalarindan trade baslat
  3. Coklu dalga birikimi ile TP/SL kontrolu
"""
import sys
import time
import numpy as np
import requests

PROJECT_ROOT = r"C:\Users\ikizler1\AndroidStudioProjects\Tasking\Crypthos"
sys.path.insert(0, PROJECT_ROOT)

from scanner.system_b_scanner import detect_zigzag_swings, analyze_waves

API_URL = "https://fapi.binance.com/fapi/v1/klines"
SWING_N = 10
FEE_PCT = 0.08
SLIPPAGE_PCT = 0.04
FEE_TOTAL = FEE_PCT + SLIPPAGE_PCT


def fetch_klines(symbol, interval, limit=500):
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


def ema(data, period):
    if len(data) < period:
        return 0.0
    mult = 2.0 / (period + 1)
    val = float(data[0])
    for i in range(1, len(data)):
        val = (data[i] - val) * mult + val
    return val


def rsi(closes, period=14):
    if len(closes) < period + 1:
        return 50.0
    deltas = np.diff(closes)
    gains = np.where(deltas > 0, deltas, 0.0)
    losses_arr = np.where(deltas < 0, -deltas, 0.0)
    avg_gain = np.mean(gains[:period])
    avg_loss = np.mean(losses_arr[:period])
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses_arr[i]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def get_direction_at_index(closes, idx):
    """idx noktasindaki yon (EMA9/21 + RSI)."""
    if idx < 30:
        return "FLAT"
    window = closes[:idx+1]
    ema9 = ema(window, 9)
    ema21 = ema(window, 21)
    rsi_val = rsi(window, 14)

    score = 0
    if ema9 > ema21 * 1.0005:
        score += 1
    elif ema9 < ema21 * 0.9995:
        score -= 1

    if rsi_val > 55:
        score += 1
    elif rsi_val < 45:
        score -= 1

    if score >= 1:
        return "LONG"
    elif score <= -1:
        return "SHORT"
    return "FLAT"


def calc_leverage(sl_pct):
    if sl_pct < 0.01:
        return 1
    pratik_liq = sl_pct * 2
    teorik_liq = (pratik_liq + FEE_PCT) / 0.7
    if teorik_liq <= 0:
        return 1
    return max(1, min(int(100.0 / teorik_liq), 125))


def simulate_with_direction(klines, swings, sl_pct, tp_pct, direction_filter=True):
    """
    Yon filtreli coklu dalga simulasyonu.
    direction_filter=True: sadece yon uyumlu noktalardan gir
    direction_filter=False: her noktadan gir (karsilastirma icin)
    """
    closes = np.array([float(k[4]) for k in klines])
    wins = 0
    losses = 0
    timeouts = 0
    details = []

    for entry_idx in range(0, len(swings) - 2):
        entry_swing = swings[entry_idx]
        entry_price = entry_swing.price
        if entry_price <= 0:
            continue

        # Hangi yonde trade acilabilir?
        # SL (dip) noktasindan → LONG aday
        # SH (tepe) noktasindan → SHORT aday
        if entry_swing.type == "SL":
            trade_dir = "LONG"
        elif entry_swing.type == "SH":
            trade_dir = "SHORT"
        else:
            continue

        # Yon filtresi: bu noktada trend uyumlu mu?
        if direction_filter:
            mum_idx = entry_swing.index
            if mum_idx >= len(closes):
                continue
            market_dir = get_direction_at_index(closes, mum_idx)
            if market_dir != trade_dir:
                continue  # Yon uyumsuz, girme

        # Trade simulasyonu: dalga dalga ilerle
        net_pct = 0.0
        max_profit = 0.0
        max_dd = 0.0
        waves_used = 0
        hit_tp = False
        hit_sl = False

        for j in range(entry_idx + 1, len(swings)):
            prev = swings[j - 1]
            curr = swings[j]
            wave_pct = (curr.price - prev.price) / entry_price * 100
            if trade_dir == "SHORT":
                wave_pct = -wave_pct

            net_pct += wave_pct
            waves_used += 1

            if net_pct > max_profit:
                max_profit = net_pct
            if net_pct < max_dd:
                max_dd = net_pct

            if net_pct >= tp_pct:
                hit_tp = True
                break
            if net_pct <= -sl_pct:
                hit_sl = True
                break

        if hit_tp:
            wins += 1
            details.append(("WIN", trade_dir, waves_used, net_pct, max_profit))
        elif hit_sl:
            losses += 1
            details.append(("LOSS", trade_dir, waves_used, net_pct, max_dd))
        else:
            timeouts += 1

    return wins, losses, timeouts, details


def analyze_coin(symbol, tf_name, limit):
    klines = fetch_klines(symbol, tf_name, limit)
    if not klines or len(klines) < 50:
        return

    highs = np.array([float(k[2]) for k in klines])
    lows = np.array([float(k[3]) for k in klines])
    closes = np.array([float(k[4]) for k in klines])

    swings = detect_zigzag_swings(highs, lows, SWING_N)
    wave = analyze_waves(swings, closes[-1])
    G = wave.G
    I = wave.I

    if len(swings) < 10 or G < 0.01:
        print(f"  {symbol} {tf_name}: yetersiz veri")
        return

    print(f"\n{'='*130}")
    print(f"  {symbol} @ {tf_name}  |  G={G:.3f}%  I={I:.3f}%  |  {len(swings)} swing")
    print(f"{'='*130}")

    # ---- YON FILTRESIZ vs FILTRELI karsilastirma ----
    sl_ref = G * 1.5 + FEE_TOTAL
    tp_ref = G * 2.5
    lev_ref = calc_leverage(sl_ref)

    w1, l1, t1, d1 = simulate_with_direction(klines, swings, sl_ref, tp_ref, direction_filter=False)
    w2, l2, t2, d2 = simulate_with_direction(klines, swings, sl_ref, tp_ref, direction_filter=True)

    total1 = w1 + l1
    total2 = w2 + l2
    pw1 = w1 / total1 if total1 > 0 else 0
    pw2 = w2 / total2 if total2 > 0 else 0
    ev1 = pw1 * tp_ref * lev_ref - (1-pw1) * sl_ref * lev_ref - FEE_PCT * lev_ref if total1 > 0 else 0
    ev2 = pw2 * tp_ref * lev_ref - (1-pw2) * sl_ref * lev_ref - FEE_PCT * lev_ref if total2 > 0 else 0

    print(f"\n  1.5G SL / 2.5G TP karsilastirma:")
    print(f"    YON FILTRESIZ: W={w1} L={l1} TO={t1} P(win)={pw1:.0%} EV={ev1:+.1f}%")
    print(f"    YON FILTRELI : W={w2} L={l2} TO={t2} P(win)={pw2:.0%} EV={ev2:+.1f}%")

    # ---- OPTIMAL SL/TP ARAMA (yon filtreli) ----
    print(f"\n  OPTIMAL SL/TP ARAMA (yon filtreli, coklu dalga):")
    print(f"  {'SL':>7s} | {'TP':>7s} | {'R:R':>5s} | {'W':>3s} | {'L':>3s} | {'TO':>3s} | "
          f"{'P(win)':>6s} | {'Lev':>4s} | {'EV%':>7s}")
    print(f"  {'-'*70}")

    best = None
    for sl_mult in np.arange(0.75, 3.1, 0.25):
        for tp_mult in np.arange(1.5, 8.1, 0.5):
            sl = G * sl_mult + FEE_TOTAL
            tp = G * tp_mult
            rr = tp / sl if sl > 0 else 0

            w, l, t, det = simulate_with_direction(klines, swings, sl, tp, direction_filter=True)
            total = w + l
            if total < 3:
                continue
            pw = w / total
            lev = calc_leverage(sl)
            ev = pw * tp * lev - (1-pw) * sl * lev - FEE_PCT * lev

            if ev > 0 and (best is None or ev > best["ev"]):
                best = {
                    "sl_mult": sl_mult, "tp_mult": tp_mult,
                    "sl_pct": sl, "tp_pct": tp, "rr": rr,
                    "wins": w, "losses": l, "timeouts": t,
                    "p_win": pw, "leverage": lev, "ev": ev,
                    "details": det,
                }

            # Tablo icin onemli satirlari goster
            if sl_mult in [1.0, 1.5, 2.0] and tp_mult in [2.0, 3.0, 4.0, 5.0, 6.0]:
                marker = ">>>" if (best and abs(sl_mult - best["sl_mult"]) < 0.01 and abs(tp_mult - best["tp_mult"]) < 0.01) else "   "
                print(f"  {marker} {sl_mult:.1f}xG | {tp_mult:.1f}xG | {rr:>5.2f} | {w:>3d} | {l:>3d} | {t:>3d} | "
                      f"{pw:>5.0%} | {lev:>3d}x | {ev:>+6.1f}%")

    if best:
        print(f"\n  >>> OPTIMAL SL/TP:")
        print(f"      SL = {best['sl_pct']:.3f}% ({best['sl_mult']:.2f}xG + fee)")
        print(f"      TP = {best['tp_pct']:.3f}% ({best['tp_mult']:.1f}xG)")
        print(f"      R:R = {best['rr']:.2f}")
        print(f"      P(win) = {best['p_win']:.0%}  (W={best['wins']} L={best['losses']} TO={best['timeouts']})")
        print(f"      Lev = {best['leverage']}x")
        print(f"      EV = {best['ev']:+.1f}%")

        # Trade detaylari
        win_details = [d for d in best['details'] if d[0] == "WIN"]
        loss_details = [d for d in best['details'] if d[0] == "LOSS"]
        if win_details:
            avg_w = np.mean([d[2] for d in win_details])
            avg_profit = np.mean([d[4] for d in win_details])
            print(f"      WIN detay: ort {avg_w:.1f} dalga, ort max kar {avg_profit:.2f}%")
        if loss_details:
            avg_l = np.mean([d[2] for d in loss_details])
            print(f"      LOSS detay: ort {avg_l:.1f} dalga")
    else:
        print(f"\n  EV > 0 saglayan kombinasyon bulunamadi!")

    return best


def main():
    print("=" * 130)
    print("  COKLU DALGA + YON FILTRELI P(win)/EV ANALIZI")
    print("  Sadece trend yonune uygun noktalardan giris")
    print("=" * 130)

    coins = [
        ("BTCUSDT", "15m", 500),
        ("ETHUSDT", "15m", 500),
        ("XRPUSDT", "30m", 500),
        ("DOGEUSDT", "15m", 500),
        ("SOLUSDT", "15m", 500),
        ("AVAXUSDT", "15m", 500),
        ("ADAUSDT", "15m", 500),
    ]

    results = {}
    for symbol, tf, limit in coins:
        print(f"\n  Fetching {symbol} {tf}...", end="", flush=True)
        time.sleep(0.3)
        opt = analyze_coin(symbol, tf, limit)
        if opt:
            results[symbol] = opt

    # OZET
    print(f"\n\n{'='*130}")
    print(f"  OZET: YON FILTRELI OPTIMAL SL/TP")
    print(f"{'='*130}")
    print(f"\n  {'Coin':>10s} | {'SL(xG)':>7s} | {'TP(xG)':>7s} | {'R:R':>5s} | "
          f"{'P(win)':>6s} | {'W/L/TO':>10s} | {'Lev':>4s} | {'EV%':>7s}")
    print(f"  {'-'*75}")
    for coin, opt in results.items():
        print(f"  {coin:>10s} | {opt['sl_mult']:.2f}xG | {opt['tp_mult']:.1f}xG | "
              f"{opt['rr']:>5.2f} | {opt['p_win']:>5.0%} | "
              f"{opt['wins']:>2d}/{opt['losses']:>2d}/{opt['timeouts']:>2d} | "
              f"{opt['leverage']:>3d}x | {opt['ev']:>+6.1f}%")


if __name__ == "__main__":
    main()
