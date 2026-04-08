"""
Optimal SL/TP Bulucu
====================
Dalga verilerinden:
  - P(win) >= %50
  - R:R >= 2.5 (TP/SL)
  - Fee sonrasi EV pozitif
olan SL ve TP seviyelerini bul.

Mantik:
  Geri dalgalari sirala. SL'yi oyle bir yere koy ki
  geri dalgalarin en fazla %50'si oraya degsin.
  Ileri dalgalari sirala. TP'yi oyle bir yere koy ki
  ileri dalgalarin en az %50'si oraya ulassin.
  TP/SL >= 2.5 olmali.
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
FEE_TOTAL = FEE_PCT + SLIPPAGE_PCT  # 0.12%


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


def calc_leverage(sl_pct):
    """SL'den kaldirac hesapla."""
    if sl_pct < 0.01:
        return 1
    # Liq mesafesi = SL * 2 (guvenlik payi)
    pratik_liq = sl_pct * 2
    teorik_liq = (pratik_liq + FEE_PCT) / 0.7
    if teorik_liq <= 0:
        return 1
    return max(1, min(int(100.0 / teorik_liq), 125))


def find_optimal_sl_tp(bw, fw, G, min_win_rate=0.50, min_rr=2.5):
    """
    Dalga verilerinden optimal SL ve TP bul.

    Args:
        bw: geri dalga boyutlari listesi (%)
        fw: ileri dalga boyutlari listesi (%)
        G: ortalama geri dalga (%)
        min_win_rate: minimum P(win) (0.50 = %50)
        min_rr: minimum Risk:Reward orani (2.5)

    Returns:
        dict with optimal SL, TP, P(win), R:R, EV, leverage
    """
    if len(bw) < 3 or len(fw) < 3:
        return None

    bw_sorted = sorted(bw)
    fw_sorted = sorted(fw)

    best = None

    # SL aday seviyeleri: geri dalgalarin her yuzdeligi
    # SL'yi geri dalganin X. yuzdelik dilimine koy
    # Ornek: %60 yuzdelik = geri dalgalarin %60'i SL'nin altinda kalir (tetiklemez)
    #                      = geri dalgalarin %40'i SL'e deger (kayip orani)

    for sl_percentile in range(40, 96):  # %40'dan %95'e kadar
        sl_idx = int(len(bw_sorted) * sl_percentile / 100)
        if sl_idx >= len(bw_sorted):
            sl_idx = len(bw_sorted) - 1
        sl_raw = bw_sorted[sl_idx]
        sl_pct = sl_raw + FEE_TOTAL  # fee-aware SL

        # Bu SL'de kac geri dalga tetiklenir?
        sl_hitting = sum(1 for b in bw if b >= sl_raw)
        p_loss_cycle = sl_hitting / len(bw)

        # TP aday seviyeleri: SL * min_rr'den baslayarak yukari
        for rr in [2.5, 2.75, 3.0, 3.5, 4.0]:
            tp_pct = sl_pct * rr

            # Bu TP'ye kac ileri dalga ulasir?
            tp_reaching = sum(1 for f in fw if f >= tp_pct)
            p_win_cycle = tp_reaching / len(fw)

            # P(win) hesapla
            total = p_win_cycle + p_loss_cycle
            if total <= 0:
                continue
            p_win = p_win_cycle / total

            # Kriterler
            if p_win < min_win_rate:
                continue

            # Kaldirac ve EV
            leverage = calc_leverage(sl_pct)
            fee_roi = FEE_PCT * leverage
            ev = p_win * tp_pct * leverage - (1 - p_win) * sl_pct * leverage - fee_roi

            if ev <= 0:
                continue

            # Skor: EV oncelikli, sonra P(win), sonra R:R
            score = ev

            if best is None or score > best["score"]:
                best = {
                    "sl_pct": sl_pct,
                    "sl_raw": sl_raw,
                    "tp_pct": tp_pct,
                    "sl_g_mult": sl_raw / G if G > 0 else 0,
                    "tp_g_mult": tp_pct / G if G > 0 else 0,
                    "p_win": p_win,
                    "p_loss": 1 - p_win,
                    "p_win_cycle": p_win_cycle,
                    "p_loss_cycle": p_loss_cycle,
                    "rr": rr,
                    "leverage": leverage,
                    "ev": ev,
                    "score": score,
                    "sl_percentile": sl_percentile,
                    "tp_reaching": tp_reaching,
                    "sl_hitting": sl_hitting,
                }

    return best


def analyze_coin(symbol, tf_name, limit):
    klines = fetch_klines(symbol, tf_name, limit)
    if not klines or len(klines) < 30:
        return

    highs = np.array([float(k[2]) for k in klines])
    lows = np.array([float(k[3]) for k in klines])
    closes = np.array([float(k[4]) for k in klines])

    swings = detect_zigzag_swings(highs, lows, SWING_N)
    wave = analyze_waves(swings, closes[-1])

    G = wave.G
    I = wave.I
    bw = wave.backward_waves
    fw = wave.forward_waves

    if len(bw) < 5 or len(fw) < 5:
        print(f"  Yetersiz dalga (BW={len(bw)}, FW={len(fw)})")
        return

    print(f"\n{'='*120}")
    print(f"  {symbol} @ {tf_name}  |  G={G:.3f}%  I={I:.3f}%  |  BW={len(bw)} dalga  FW={len(fw)} dalga")
    print(f"{'='*120}")

    # Dalga dagilimi
    bw_sorted = sorted(bw)
    fw_sorted = sorted(fw)

    print(f"\n  GERI DALGA DAGILIMI (SL icin referans):")
    pcts = [25, 50, 60, 70, 75, 80, 90, 95]
    print(f"  ", end="")
    for p in pcts:
        idx = min(int(len(bw_sorted) * p / 100), len(bw_sorted) - 1)
        print(f"  P{p}={bw_sorted[idx]:.3f}%", end="")
    print()

    print(f"\n  ILERI DALGA DAGILIMI (TP icin referans):")
    print(f"  ", end="")
    for p in pcts:
        idx = min(int(len(fw_sorted) * p / 100), len(fw_sorted) - 1)
        print(f"  P{p}={fw_sorted[idx]:.3f}%", end="")
    print()

    # Sabit SL/TP (mevcut sistem)
    sl_fixed = G * 1.5 + FEE_TOTAL
    tp_fixed = G * 2.5
    lev_fixed = calc_leverage(sl_fixed)
    pw_c_fixed = sum(1 for f in fw if f >= tp_fixed) / len(fw)
    pl_c_fixed = sum(1 for b in bw if b >= (G * 1.5)) / len(bw)
    tot_fixed = pw_c_fixed + pl_c_fixed
    if tot_fixed > 0:
        pw_fixed = pw_c_fixed / tot_fixed
    else:
        pw_fixed = 0
    fee_fixed = FEE_PCT * lev_fixed
    ev_fixed = pw_fixed * tp_fixed * lev_fixed - (1-pw_fixed) * sl_fixed * lev_fixed - fee_fixed
    rr_fixed = tp_fixed / sl_fixed if sl_fixed > 0 else 0

    print(f"\n  MEVCUT SISTEM (sabit 1.5G SL, 2.5G TP):")
    print(f"    SL = {sl_fixed:.3f}% ({1.5:.1f}xG)  |  TP = {tp_fixed:.3f}% ({2.5:.1f}xG)  |  R:R = {rr_fixed:.2f}")
    print(f"    P(win) = {pw_fixed:.1%}  |  Lev = {lev_fixed}x  |  EV = {ev_fixed:+.1f}%")
    if ev_fixed < 0:
        print(f"    >>> NEGATIF EV! Bu sabit oranlar bu coin icin uygun degil.")
    elif pw_fixed < 0.5:
        print(f"    >>> P(win) < %50! Kazanma orani yetersiz.")

    # Optimal SL/TP bul
    print(f"\n  OPTIMAL SL/TP ARAMA (P(win)>=%50, R:R>=2.5, EV>0)...")

    optimal = find_optimal_sl_tp(bw, fw, G, min_win_rate=0.50, min_rr=2.5)

    if optimal:
        print(f"\n  BULUNAN OPTIMAL:")
        print(f"    SL = {optimal['sl_pct']:.3f}% ({optimal['sl_g_mult']:.2f}xG + fee)")
        print(f"    TP = {optimal['tp_pct']:.3f}% ({optimal['tp_g_mult']:.2f}xG)")
        print(f"    R:R = {optimal['rr']:.1f}")
        print(f"    P(win) = {optimal['p_win']:.1%}  ({optimal['tp_reaching']}/{len(fw)} ileri dalga TP'ye ulasir)")
        print(f"    P(loss) = {optimal['p_loss']:.1%}  ({optimal['sl_hitting']}/{len(bw)} geri dalga SL'e deger)")
        print(f"    Kaldirac = {optimal['leverage']}x")
        print(f"    EV = {optimal['ev']:+.1f}%")
        print(f"    SL yuzdeligi = P{optimal['sl_percentile']} (geri dalgalarin %{optimal['sl_percentile']}'i altinda)")

        # Iyilestirme orani
        if ev_fixed != 0:
            improvement = ((optimal['ev'] - ev_fixed) / abs(ev_fixed)) * 100
            print(f"\n    Mevcut sisteme gore EV iyilestirmesi: {improvement:+.0f}%")
    else:
        print(f"\n  P(win)>=%50 + R:R>=2.5 + EV>0 saglayan kombinasyon BULUNAMADI!")
        # R:R'yi dusurerek dene
        print(f"  R:R >= 2.0 ile tekrar deneniyor...")
        optimal = find_optimal_sl_tp(bw, fw, G, min_win_rate=0.50, min_rr=2.0)
        if optimal:
            print(f"  BULUNAN (R:R >= 2.0):")
            print(f"    SL = {optimal['sl_pct']:.3f}% ({optimal['sl_g_mult']:.2f}xG)")
            print(f"    TP = {optimal['tp_pct']:.3f}% ({optimal['tp_g_mult']:.2f}xG)")
            print(f"    R:R = {optimal['rr']:.1f}  |  P(win) = {optimal['p_win']:.1%}  |  EV = {optimal['ev']:+.1f}%")
            print(f"    Lev = {optimal['leverage']}x")
        else:
            print(f"  R:R >= 2.0 ile de bulunamadi. Bu coin/TF icin dalga yapisi uygun degil.")

    return optimal


def main():
    print("=" * 120)
    print("  OPTIMAL SL/TP BULUCU")
    print("  Hedef: P(win) >= %50,  R:R >= 2.5,  EV > 0 (fee sonrasi)")
    print("=" * 120)

    coins = [
        ("BTCUSDT", "15m", 500),
        ("ETHUSDT", "3m", 1000),
        ("ETHUSDT", "15m", 500),
        ("XRPUSDT", "30m", 500),
        ("DOGEUSDT", "15m", 500),
        ("SOLUSDT", "15m", 500),
        ("AVAXUSDT", "15m", 500),
        ("ADAUSDT", "3m", 1000),
    ]

    results = {}
    for symbol, tf, limit in coins:
        print(f"\n  Fetching {symbol} {tf}...", end="", flush=True)
        time.sleep(0.3)
        opt = analyze_coin(symbol, tf, limit)
        if opt:
            results[f"{symbol}_{tf}"] = opt

    # OZET
    print(f"\n\n{'='*120}")
    print(f"  OZET TABLO")
    print(f"{'='*120}")
    print(f"\n  {'Coin':>12s} | {'TF':>4s} | {'G%':>6s} | {'SL%':>6s} | {'SL(xG)':>6s} | "
          f"{'TP%':>6s} | {'TP(xG)':>6s} | {'R:R':>4s} | {'P(win)':>6s} | {'Lev':>4s} | {'EV%':>6s}")
    print(f"  {'-'*95}")
    for key, opt in results.items():
        parts = key.split("_")
        symbol = parts[0]
        tf = parts[1]
        G = opt['sl_raw']  # yaklaasik
        print(f"  {symbol:>12s} | {tf:>4s} | {G:>6.3f} | {opt['sl_pct']:>6.3f} | "
              f"{opt['sl_g_mult']:>5.2f}x | {opt['tp_pct']:>6.3f} | {opt['tp_g_mult']:>5.2f}x | "
              f"{opt['rr']:>4.1f} | {opt['p_win']:>5.0%} | {opt['leverage']:>3d}x | {opt['ev']:>+5.1f}%")


if __name__ == "__main__":
    main()
