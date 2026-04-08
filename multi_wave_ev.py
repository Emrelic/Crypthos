"""
Coklu Dalga P(win)/EV Analizi
==============================
Tek dalga degil, birden fazla dalga boyunca net birikim ile
TP veya SL'e ulasma olasligini hesapla.

Gercek zigzag dalgalarini SIRALI simule ederek:
  - Giris noktasindan baslayarak dalga dalga ilerle
  - Net pozisyon = ileri dalgalarin toplami - geri dalgalarin toplami
  - TP'ye ulasirsa WIN, SL'e ulasirsa LOSS
"""
import sys
import time
import numpy as np
import requests

PROJECT_ROOT = r"C:\Users\ikizler1\AndroidStudioProjects\Tasking\Crypthos"
sys.path.insert(0, PROJECT_ROOT)

from scanner.system_b_scanner import detect_zigzag_swings, analyze_waves, SwingPoint

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


def calc_leverage(sl_pct):
    if sl_pct < 0.01:
        return 1
    pratik_liq = sl_pct * 2
    teorik_liq = (pratik_liq + FEE_PCT) / 0.7
    if teorik_liq <= 0:
        return 1
    return max(1, min(int(100.0 / teorik_liq), 125))


def simulate_trades_from_swings(swings, direction, sl_pct, tp_pct):
    """
    Gercek zigzag swingleri uzerinde trade simulasyonu.

    Her swing noktasindan basla, sonraki dalgalari sirali izle.
    Fiyat TP'ye ulasirsa WIN, SL'e ulasirsa LOSS.
    Her dalga fiyati biriktirerek ilerler (tek dalga degil, coklu dalga).

    Returns:
        wins, losses, timeout (ne TP ne SL'e ulasamayan)
    """
    if len(swings) < 5:
        return 0, 0, 0, []

    wins = 0
    losses = 0
    timeouts = 0
    trade_details = []

    # Her olasi giris noktasindan trade baslat
    for entry_idx in range(0, len(swings) - 2):
        entry_swing = swings[entry_idx]
        entry_price = entry_swing.price

        if entry_price <= 0:
            continue

        # LONG icin: SL dip'ten baslar, yukari gider → net pozitif TP'ye ulasir mi?
        # SHORT icin: SH tepe'den baslar, asagi gider → net negatif TP'ye ulasir mi?
        if direction == "LONG" and entry_swing.type != "SL":
            continue  # LONG sadece dip'ten baslar
        if direction == "SHORT" and entry_swing.type != "SH":
            continue  # SHORT sadece tepe'den baslar

        # Sonraki dalgalari izle
        max_profit = 0.0
        max_drawdown = 0.0
        net_pct = 0.0
        hit_tp = False
        hit_sl = False
        waves_used = 0

        for j in range(entry_idx + 1, len(swings)):
            prev = swings[j - 1]
            curr = swings[j]
            wave_pct = (curr.price - prev.price) / entry_price * 100
            # SHORT icin tersi
            if direction == "SHORT":
                wave_pct = -wave_pct

            net_pct += wave_pct
            waves_used += 1

            if net_pct > max_profit:
                max_profit = net_pct
            if net_pct < max_drawdown:
                max_drawdown = net_pct

            # TP kontrolu (net birikim TP'ye ulasti mi?)
            if net_pct >= tp_pct:
                hit_tp = True
                break

            # SL kontrolu (net birikim SL'e dustu mu?)
            if net_pct <= -sl_pct:
                hit_sl = True
                break

        if hit_tp:
            wins += 1
            trade_details.append(("WIN", waves_used, net_pct, max_profit, max_drawdown))
        elif hit_sl:
            losses += 1
            trade_details.append(("LOSS", waves_used, net_pct, max_profit, max_drawdown))
        else:
            timeouts += 1
            trade_details.append(("TIMEOUT", waves_used, net_pct, max_profit, max_drawdown))

    return wins, losses, timeouts, trade_details


def find_optimal_multwave(swings, direction, G):
    """
    Coklu dalga simulasyonuyla optimal SL/TP bul.
    """
    best = None

    for sl_mult in np.arange(1.0, 3.1, 0.25):
        for tp_mult in np.arange(2.0, 8.1, 0.5):
            sl_pct = G * sl_mult + FEE_TOTAL
            tp_pct = G * tp_mult

            rr = tp_pct / sl_pct if sl_pct > 0 else 0
            if rr < 2.0:
                continue

            wins, losses, timeouts, details = simulate_trades_from_swings(
                swings, direction, sl_pct, tp_pct)

            total = wins + losses
            if total < 3:
                continue

            p_win = wins / total
            leverage = calc_leverage(sl_pct)
            fee_roi = FEE_PCT * leverage
            ev = p_win * tp_pct * leverage - (1 - p_win) * sl_pct * leverage - fee_roi

            if ev <= 0:
                continue

            score = ev * p_win  # EV x P(win) = kalite skoru

            if best is None or score > best["score"]:
                best = {
                    "sl_pct": sl_pct, "tp_pct": tp_pct,
                    "sl_mult": sl_mult, "tp_mult": tp_mult,
                    "rr": rr, "p_win": p_win,
                    "wins": wins, "losses": losses, "timeouts": timeouts,
                    "leverage": leverage, "ev": ev, "score": score,
                    "details": details,
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

    if len(swings) < 10:
        print(f"  {symbol} {tf_name}: yetersiz swing ({len(swings)})")
        return

    print(f"\n{'='*130}")
    print(f"  {symbol} @ {tf_name}  |  G={G:.3f}%  I={I:.3f}%  |  {len(swings)} swing noktasi")
    print(f"{'='*130}")

    # LONG yonunde analiz (trend yonune gore degistirilebilir)
    direction = "LONG"

    # ---- MEVCUT SISTEM: Tek dalga bazli ----
    sl_old = G * 1.5 + FEE_TOTAL
    tp_old = G * 2.5
    lev_old = calc_leverage(sl_old)
    pw_c = sum(1 for f in wave.forward_waves if f >= tp_old) / max(len(wave.forward_waves), 1)
    pl_c = sum(1 for b in wave.backward_waves if b >= G * 1.5) / max(len(wave.backward_waves), 1)
    tot_old = pw_c + pl_c
    pw_old = pw_c / tot_old if tot_old > 0 else 0
    ev_old = pw_old * tp_old * lev_old - (1-pw_old) * sl_old * lev_old - FEE_PCT * lev_old

    print(f"\n  [ESKI] TEK DALGA BAZLI (1.5G SL, 2.5G TP):")
    print(f"    SL={sl_old:.3f}%  TP={tp_old:.3f}%  R:R={tp_old/sl_old:.2f}  "
          f"P(win)={pw_old:.0%}  Lev={lev_old}x  EV={ev_old:+.1f}%")

    # ---- COKLU DALGA: Simulasyon ----
    print(f"\n  [YENI] COKLU DALGA SIMULASYONU:")
    print(f"  Her swing noktasindan trade baslat, dalga dalga ilerle,")
    print(f"  net birikim TP'ye ulasirsa WIN, SL'e duserse LOSS.\n")

    # Referans icin 1.5G/2.5G ile coklu dalga simulasyonu
    w, l, t, details = simulate_trades_from_swings(swings, direction, sl_old, tp_old)
    total_ref = w + l
    pw_ref = w / total_ref if total_ref > 0 else 0
    ev_ref = pw_ref * tp_old * lev_old - (1-pw_ref) * sl_old * lev_old - FEE_PCT * lev_old

    print(f"  Referans (1.5G SL, 2.5G TP) coklu dalga:")
    print(f"    WIN={w}  LOSS={l}  TIMEOUT={t}  P(win)={pw_ref:.0%}  EV={ev_ref:+.1f}%")
    if details:
        avg_waves_win = np.mean([d[1] for d in details if d[0] == "WIN"]) if w > 0 else 0
        avg_waves_loss = np.mean([d[1] for d in details if d[0] == "LOSS"]) if l > 0 else 0
        print(f"    Ortalama dalga sayisi: WIN={avg_waves_win:.1f}  LOSS={avg_waves_loss:.1f}")

    # ---- OPTIMAL ARAMA ----
    print(f"\n  Optimal SL/TP aranıyor (P(win)>=%35, R:R>=2.0, EV>0)...")
    optimal = find_optimal_multwave(swings, direction, G)

    if optimal:
        print(f"\n  BULUNAN OPTIMAL:")
        print(f"    SL = {optimal['sl_pct']:.3f}% ({optimal['sl_mult']:.2f}xG + fee)")
        print(f"    TP = {optimal['tp_pct']:.3f}% ({optimal['tp_mult']:.1f}xG)")
        print(f"    R:R = {optimal['rr']:.2f}")
        print(f"    P(win) = {optimal['p_win']:.0%}  (WIN={optimal['wins']}  LOSS={optimal['losses']}  TO={optimal['timeouts']})")
        print(f"    Lev = {optimal['leverage']}x")
        print(f"    EV = {optimal['ev']:+.1f}%")

        # Trade detaylari
        if optimal['details']:
            win_waves = [d[1] for d in optimal['details'] if d[0] == "WIN"]
            loss_waves = [d[1] for d in optimal['details'] if d[0] == "LOSS"]
            print(f"\n    Trade detaylari:")
            print(f"    WIN'ler  : ort {np.mean(win_waves):.1f} dalga, "
                  f"min {min(win_waves)}, max {max(win_waves)}" if win_waves else "")
            print(f"    LOSS'lar : ort {np.mean(loss_waves):.1f} dalga, "
                  f"min {min(loss_waves)}, max {max(loss_waves)}" if loss_waves else "")

        # Karsilastirma
        print(f"\n    Eski (tek dalga) EV = {ev_old:+.1f}%")
        print(f"    Yeni (coklu dalga) EV = {optimal['ev']:+.1f}%")
    else:
        print(f"\n  Kriterleri saglayan kombinasyon bulunamadi!")

    # ---- DETAYLI TABLO ----
    print(f"\n  SL/TP KOMBINASYON TABLOSU (coklu dalga):")
    print(f"  {'SL':>6s} | {'TP':>6s} | {'R:R':>5s} | {'W':>3s} | {'L':>3s} | {'TO':>3s} | "
          f"{'P(win)':>6s} | {'Lev':>4s} | {'EV%':>7s} | {'Yorum':>15s}")
    print(f"  {'-'*85}")

    for sl_mult in [1.0, 1.5, 2.0, 2.5]:
        for tp_mult in [2.0, 3.0, 4.0, 5.0, 6.0]:
            sl = G * sl_mult + FEE_TOTAL
            tp = G * tp_mult
            rr = tp / sl if sl > 0 else 0

            w, l, t, _ = simulate_trades_from_swings(swings, direction, sl, tp)
            total = w + l
            if total < 2:
                continue
            pw = w / total
            lev = calc_leverage(sl)
            ev = pw * tp * lev - (1-pw) * sl * lev - FEE_PCT * lev

            if ev > 10:
                yorum = "*** GUCLU"
            elif ev > 0:
                yorum = "** POZITIF"
            elif ev > -5:
                yorum = "* NOTR"
            else:
                yorum = "NEGATIF"

            is_opt = optimal and abs(sl_mult - optimal['sl_mult']) < 0.01 and abs(tp_mult - optimal['tp_mult']) < 0.01
            marker = ">>>" if is_opt else "   "

            print(f"  {marker} {sl_mult:.1f}xG | {tp_mult:.1f}xG | {rr:>5.2f} | {w:>3d} | {l:>3d} | {t:>3d} | "
                  f"{pw:>5.0%} | {lev:>3d}x | {ev:>+6.1f}% | {yorum}")

    return optimal


def main():
    print("=" * 130)
    print("  COKLU DALGA P(win)/EV ANALIZI")
    print("  Fiyat tek dalgada degil, birden fazla dalga boyunca TP/SL'e ulasir")
    print("=" * 130)

    coins = [
        ("BTCUSDT", "15m", 500),
        ("ETHUSDT", "15m", 500),
        ("XRPUSDT", "30m", 500),
        ("DOGEUSDT", "15m", 500),
        ("AVAXUSDT", "15m", 500),
    ]

    results = {}
    for symbol, tf, limit in coins:
        print(f"\n  Fetching {symbol} {tf}...", end="", flush=True)
        time.sleep(0.3)
        opt = analyze_coin(symbol, tf, limit)
        if opt:
            results[f"{symbol}"] = opt

    # OZET
    print(f"\n\n{'='*130}")
    print(f"  OZET: COKLU DALGA OPTIMAL SL/TP")
    print(f"{'='*130}")
    print(f"\n  {'Coin':>10s} | {'SL(xG)':>6s} | {'TP(xG)':>6s} | {'R:R':>5s} | "
          f"{'P(win)':>6s} | {'W/L/TO':>10s} | {'Lev':>4s} | {'EV%':>7s}")
    print(f"  {'-'*70}")
    for coin, opt in results.items():
        print(f"  {coin:>10s} | {opt['sl_mult']:.2f}xG | {opt['tp_mult']:.1f}xG | "
              f"{opt['rr']:>5.2f} | {opt['p_win']:>5.0%} | "
              f"{opt['wins']:>2d}/{opt['losses']:>2d}/{opt['timeouts']:>2d} | "
              f"{opt['leverage']:>3d}x | {opt['ev']:>+6.1f}%")


if __name__ == "__main__":
    main()
