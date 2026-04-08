"""
P(win)/EV Hesabi - Adim Adim Aciklama ve Gercek Veri Analizi
=============================================================
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
            else:
                return []
    return []


def calc_leverage(G):
    if G < 0.01:
        return 0
    sl_pct = G * 1.5 + FEE_PCT + SLIPPAGE_PCT
    teorik_liq = (G * 3.0 + FEE_PCT) / 0.7
    return max(1, min(int(100.0 / teorik_liq), 125))


def detailed_ev_analysis(symbol, tf_name, limit=500):
    """Tek bir coin/TF icin P(win)/EV hesabini adim adim goster."""
    klines = fetch_klines(symbol, tf_name, limit)
    if not klines or len(klines) < 30:
        print(f"  Veri yok!")
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

    if not bw or not fw:
        print(f"  Yetersiz dalga!")
        return

    leverage = calc_leverage(G)
    sl_pct = G * 1.5 + FEE_PCT + SLIPPAGE_PCT
    tp_pct = G * 2.5  # trailing trigger

    print(f"\n{'='*120}")
    print(f"  {symbol} @ {tf_name} ({len(klines)} mum, {len(swings)} swing)")
    print(f"{'='*120}")

    # ---- ADIM 1: Zigzag dalga boylarini goster ----
    print(f"\n  ADIM 1: ZIGZAG DALGA BOYUTLARI")
    print(f"  G (geri dalga ort)  = {G:.3f}%")
    print(f"  I (ileri dalga ort) = {I:.3f}%")
    print(f"  Geri dalga sayisi   = {len(bw)}")
    print(f"  Ileri dalga sayisi  = {len(fw)}")

    print(f"\n  Geri dalgalar (SL'e degme riski olan):")
    for i, w in enumerate(sorted(bw)):
        marker = " <<< SL'e deger" if w >= sl_pct else ""
        print(f"    [{i+1:>2d}] {w:>7.3f}%{marker}")

    print(f"\n  Ileri dalgalar (TP'ye ulasma potansiyeli olan):")
    for i, w in enumerate(sorted(fw)):
        marker = " <<< TP'ye ulasir" if w >= tp_pct else ""
        print(f"    [{i+1:>2d}] {w:>7.3f}%{marker}")

    # ---- ADIM 2: SL ve TP seviyeleri ----
    print(f"\n  ADIM 2: SL ve TP SEVIYELERI (G'den turetilmis)")
    print(f"  SL = 1.5 x G + fee = 1.5 x {G:.3f} + {FEE_PCT+SLIPPAGE_PCT:.2f} = {sl_pct:.3f}%")
    print(f"  TP = 2.5 x G = 2.5 x {G:.3f} = {tp_pct:.3f}%")
    print(f"  Kaldirac = {leverage}x")

    # ---- ADIM 3: P(win) ve P(loss) hesabi ----
    # LONG yonu varsayalim
    direction = "LONG"

    # Ileri dalgalar: LONG icin yukari dalgalar (SL->SH)
    # Geri dalgalar: LONG icin asagi dalgalar (SH->SL)
    # Zaten analyze_waves bunu ayirmis durumda (trend yonune gore)

    print(f"\n  ADIM 3: P(win) ve P(loss) HESABI ({direction})")

    # P(win_cycle): ileri dalgalarin kaci TP'ye ulasir?
    tp_reaching = sum(1 for f in fw if f >= tp_pct)
    p_win_cycle = tp_reaching / len(fw)
    print(f"\n  P(win_cycle) = ileri dalgalardan kaci >= TP({tp_pct:.3f}%)?")
    print(f"    {tp_reaching} / {len(fw)} = {p_win_cycle:.3f}")
    print(f"    Yani: {len(fw)} ileri dalganin {tp_reaching} tanesi TP seviyesine ulasti")

    # P(loss_cycle): geri dalgalarin kaci SL'e deger?
    sl_hitting = sum(1 for r in bw if r >= sl_pct)
    p_loss_cycle = sl_hitting / len(bw)
    print(f"\n  P(loss_cycle) = geri dalgalardan kaci >= SL({sl_pct:.3f}%)?")
    print(f"    {sl_hitting} / {len(bw)} = {p_loss_cycle:.3f}")
    print(f"    Yani: {len(bw)} geri dalganin {sl_hitting} tanesi SL seviyesine deger")

    # Normalizasyon
    total = p_win_cycle + p_loss_cycle
    if total > 0:
        p_win = p_win_cycle / total
        p_loss = p_loss_cycle / total
    else:
        print(f"\n  HATA: Hem P(win) hem P(loss) = 0, hesap yapilamaz!")
        return

    print(f"\n  Normalizasyon:")
    print(f"    P(win)  = {p_win_cycle:.3f} / ({p_win_cycle:.3f} + {p_loss_cycle:.3f}) = {p_win:.3f} ({p_win*100:.1f}%)")
    print(f"    P(loss) = {p_loss_cycle:.3f} / ({p_win_cycle:.3f} + {p_loss_cycle:.3f}) = {p_loss:.3f} ({p_loss*100:.1f}%)")

    # ---- ADIM 4: EV hesabi ----
    fee_roi = FEE_PCT * leverage
    kazanc = p_win * tp_pct * leverage
    kayip = p_loss * sl_pct * leverage
    ev = kazanc - kayip - fee_roi

    print(f"\n  ADIM 4: EV (BEKLENEN DEGER) HESABI")
    print(f"  EV = P(win) x TP x Lev  -  P(loss) x SL x Lev  -  Fee_ROI")
    print(f"     = {p_win:.3f} x {tp_pct:.3f} x {leverage}")
    print(f"     - {p_loss:.3f} x {sl_pct:.3f} x {leverage}")
    print(f"     - {FEE_PCT} x {leverage}")
    print(f"     = {kazanc:.2f} - {kayip:.2f} - {fee_roi:.2f}")
    print(f"     = {ev:+.2f}%")

    print(f"\n  YORUM:")
    if ev > 10:
        print(f"    EV = {ev:+.1f}% -> GUCLU POZITIF. Her 100 trade'de ~{ev:.0f}% net ROI beklenir.")
    elif ev > 0:
        print(f"    EV = {ev:+.1f}% -> POZITIF. Uzun vadede karli, ama marjinal.")
    elif ev > -5:
        print(f"    EV = {ev:+.1f}% -> NOTR/HAFIF NEGATIF. Fee'ler yiyor.")
    else:
        print(f"    EV = {ev:+.1f}% -> NEGATIF. Bu setup'ta trade etme!")

    # ---- ADIM 5: Farkli SL/TP ile EV tablosu ----
    print(f"\n  ADIM 5: FARKLI SL/TP KOMBINASYONLARI (G = {G:.3f}%)")
    print(f"  {'SL(xG)':>7s} | {'SL%':>6s} | {'TP(xG)':>7s} | {'TP%':>6s} | "
          f"{'P(win)':>7s} | {'P(loss)':>8s} | {'Lev':>4s} | {'EV%':>8s} | {'R:R':>5s} | {'Yorum':>15s}")
    print(f"  {'-'*100}")

    best_ev = -999
    best_combo = None

    for sl_mult in [1.0, 1.5, 2.0, 2.5, 3.0]:
        for tp_mult in [1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 5.0]:
            test_sl = G * sl_mult + FEE_PCT + SLIPPAGE_PCT
            test_tp = G * tp_mult

            test_lev = calc_leverage_from_sl(G, sl_mult)

            pw_c = sum(1 for f in fw if f >= test_tp) / len(fw)
            pl_c = sum(1 for r in bw if r >= test_sl) / len(bw)
            tot = pw_c + pl_c
            if tot <= 0:
                continue
            pw = pw_c / tot
            pl = pl_c / tot

            fee_r = FEE_PCT * test_lev
            test_ev = pw * test_tp * test_lev - pl * test_sl * test_lev - fee_r
            rr = test_tp / test_sl if test_sl > 0 else 0

            if test_ev > best_ev:
                best_ev = test_ev
                best_combo = (sl_mult, tp_mult, test_sl, test_tp, pw, pl, test_lev, test_ev, rr)

            # Sadece onemli kombinasyonlari goster
            if sl_mult in [1.0, 1.5, 2.0] and tp_mult in [2.0, 2.5, 3.0, 3.5]:
                yorum = "OPTIMAL" if abs(test_ev - best_ev) < 0.01 else ""
                if test_ev > 0:
                    yorum = yorum or "POZITIF"
                elif test_ev > -5:
                    yorum = yorum or "NOTR"
                else:
                    yorum = yorum or "NEGATIF"

                print(f"  {sl_mult:>5.1f}xG | {test_sl:>6.2f} | {tp_mult:>5.1f}xG | {test_tp:>6.2f} | "
                      f"{pw:>6.1%} | {pl:>7.1%} | {test_lev:>3d}x | {test_ev:>+7.1f}% | "
                      f"{rr:>5.2f} | {yorum:>15s}")

    if best_combo:
        sl_m, tp_m, _, _, _, _, _, _, _ = best_combo
        print(f"\n  EN IYI KOMBINASYON: SL={sl_m:.1f}xG, TP={tp_m:.1f}xG -> EV={best_ev:+.1f}%")

    # ---- ADIM 6: Sonuc ----
    print(f"\n  {'='*80}")
    print(f"  SONUC: {symbol} @ {tf_name}")
    print(f"  {'='*80}")
    print(f"  G = {G:.3f}% (zoom diyaframinin buldugu optimal geri dalga boyu)")
    print(f"  SL = {sl_pct:.3f}% = 1.5 x G + fee (geri dalganin %50 fazlasi)")
    print(f"  TP = {tp_pct:.3f}% = 2.5 x G (ileri dalganin ortalama boyu civarinda)")
    print(f"  Kaldirac = {leverage}x")
    print(f"  P(win) = {p_win:.1%}")
    print(f"  P(loss) = {p_loss:.1%}")
    print(f"  EV = {ev:+.1f}% (her trade basina beklenen ROI)")


def calc_leverage_from_sl(G, sl_mult):
    """SL carpanina gore kaldirac hesapla."""
    liq_mult = sl_mult * 2  # liq = 2 x SL katsayisi
    pratik_liq = G * liq_mult
    teorik_liq = (pratik_liq + FEE_PCT) / 0.7
    if teorik_liq <= 0:
        return 1
    return max(1, min(int(100.0 / teorik_liq), 125))


def main():
    print("=" * 120)
    print("  P(win)/EV HESABI - ADIM ADIM ACIKLAMA")
    print("  Gercek Binance verileriyle")
    print("=" * 120)

    # BTC 15m (zoom'un buldugu optimal TF)
    detailed_ev_analysis("BTCUSDT", "15m", 500)

    # ETH 3m
    print("\n\n")
    detailed_ev_analysis("ETHUSDT", "3m", 1000)

    # XRP 30m
    print("\n\n")
    detailed_ev_analysis("XRPUSDT", "30m", 500)


if __name__ == "__main__":
    main()
