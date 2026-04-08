"""System J Direction Research Backtest.

5 bolumlu kapsamli analiz:
  1) Rejim dagilimi: coinler hangi TF'de ne kadar trending vs ranging?
  2) Indikator bazli yon dogruluklari: hangi indikator en iyi yon tespit eder?
  3) Indikator kombinasyonlari: en iyi combo %70+ verir mi?
  4) G bazli SL/TP analizi: optimal SL ve TP carplari
  5) Optimal tutma suresi: kac mum/dalga sonrasi cikis en karliydi?

7 coin x 3 TF (5m, 15m, 1h) x son 30 gun
"""
import sys, os, time
import numpy as np
import pandas as pd
from collections import defaultdict
from dataclasses import dataclass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from market.binance_rest import BinanceRestClient
from scanner.system_b_scanner import (
    detect_zigzag_swings, analyze_waves,
    compute_rolling_er, compute_hurst_exponent,
)

# ============================================================
# CONFIG
# ============================================================
COINS = ["BTCUSDT", "ETHUSDT", "XRPUSDT", "SOLUSDT", "ADAUSDT", "DOGEUSDT", "BNBUSDT"]
TIMEFRAMES = ["5m", "15m", "1h"]
KLINE_LIMITS = {"5m": 1500, "15m": 1500, "1h": 720}
ANALYSIS_WINDOW = 200
FORWARD_WINDOWS = {"5m": 36, "15m": 16, "1h": 12}
STEP_SIZE = {"5m": 12, "15m": 4, "1h": 2}
FEE_PCT = 0.08  # tek yon fee %

# ============================================================
# INDICATOR FUNCTIONS
# ============================================================

def _ema_series(data, period):
    alpha = 2.0 / (period + 1)
    result = np.zeros(len(data))
    result[0] = data[0]
    for i in range(1, len(data)):
        result[i] = alpha * data[i] + (1 - alpha) * result[i - 1]
    return result

def _ema_val(closes, period):
    if len(closes) < period:
        return 0.0
    alpha = 2.0 / (period + 1)
    ema = float(closes[0])
    for i in range(1, len(closes)):
        ema = alpha * float(closes[i]) + (1.0 - alpha) * ema
    return ema

def compute_rsi(closes, period=14):
    if len(closes) < period + 1:
        return 50.0
    deltas = np.diff(closes)
    gains = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)
    avg_gain = np.mean(gains[:period])
    avg_loss = np.mean(losses[:period])
    for i in range(period, len(deltas)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))

def compute_macd(closes, fast=8, slow=17, signal=9):
    if len(closes) < slow + signal:
        return 0.0, 0.0, 0.0
    ema_f = _ema_series(closes, fast)
    ema_s = _ema_series(closes, slow)
    macd_line = ema_f - ema_s
    sig_line = _ema_series(macd_line, signal)
    hist = macd_line[-1] - sig_line[-1]
    return float(macd_line[-1]), float(sig_line[-1]), float(hist)

def compute_adx(highs, lows, closes, period=14):
    if len(closes) < period * 2:
        return 50.0, 0.0, 0.0
    n = len(closes)
    tr = np.zeros(n)
    plus_dm = np.zeros(n)
    minus_dm = np.zeros(n)
    for i in range(1, n):
        h_l = highs[i] - lows[i]
        h_pc = abs(highs[i] - closes[i-1])
        l_pc = abs(lows[i] - closes[i-1])
        tr[i] = max(h_l, h_pc, l_pc)
        up = highs[i] - highs[i-1]
        down = lows[i-1] - lows[i]
        plus_dm[i] = up if (up > down and up > 0) else 0
        minus_dm[i] = down if (down > up and down > 0) else 0
    # Smoothed
    atr = np.mean(tr[1:period+1])
    plus_di_s = np.mean(plus_dm[1:period+1])
    minus_di_s = np.mean(minus_dm[1:period+1])
    for i in range(period+1, n):
        atr = (atr * (period-1) + tr[i]) / period
        plus_di_s = (plus_di_s * (period-1) + plus_dm[i]) / period
        minus_di_s = (minus_di_s * (period-1) + minus_dm[i]) / period
    if atr == 0:
        return 0, 0, 0
    plus_di = (plus_di_s / atr) * 100
    minus_di = (minus_di_s / atr) * 100
    di_sum = plus_di + minus_di
    if di_sum == 0:
        return 0, plus_di, minus_di
    dx = abs(plus_di - minus_di) / di_sum * 100
    return dx, plus_di, minus_di

def compute_bb(closes, period=20, std_mult=2.0):
    if len(closes) < period:
        return 0, 0, 0
    sma = np.mean(closes[-period:])
    std = np.std(closes[-period:], ddof=1)
    return sma + std_mult * std, sma, sma - std_mult * std

def compute_obv_slope(closes, volumes, lookback=10):
    """OBV son N mumun egimi (normalize)."""
    if len(closes) < lookback + 1:
        return 0.0
    obv = [0.0]
    for i in range(1, len(closes)):
        if closes[i] > closes[i-1]:
            obv.append(obv[-1] + volumes[i])
        elif closes[i] < closes[i-1]:
            obv.append(obv[-1] - volumes[i])
        else:
            obv.append(obv[-1])
    recent = obv[-lookback:]
    x = np.arange(lookback)
    if np.std(recent) == 0:
        return 0.0
    slope = np.polyfit(x, recent, 1)[0]
    return slope / (np.mean(np.abs(recent)) + 1e-10)

def compute_vwap_position(closes, volumes, highs, lows, lookback=20):
    """Fiyatin VWAP'a gore konumu: >0 ustunde, <0 altinda."""
    if len(closes) < lookback:
        return 0.0
    typical = (highs[-lookback:] + lows[-lookback:] + closes[-lookback:]) / 3
    vol = volumes[-lookback:]
    cum_vol = np.sum(vol)
    if cum_vol == 0:
        return 0.0
    vwap = np.sum(typical * vol) / cum_vol
    price = closes[-1]
    return (price - vwap) / vwap * 100

def compute_stoch_rsi(closes, rsi_period=14, stoch_period=14, k_period=3):
    """Stochastic RSI (0-100) — fast version."""
    if len(closes) < rsi_period + stoch_period + 1:
        return 50.0
    # RSI serisini tek geciste hesapla
    deltas = np.diff(closes)
    gains = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)
    avg_gain = np.mean(gains[:rsi_period])
    avg_loss = np.mean(losses[:rsi_period])
    rsi_vals = []
    for i in range(rsi_period, len(deltas)):
        avg_gain = (avg_gain * (rsi_period - 1) + gains[i]) / rsi_period
        avg_loss = (avg_loss * (rsi_period - 1) + losses[i]) / rsi_period
        if avg_loss == 0:
            rsi_vals.append(100.0)
        else:
            rs = avg_gain / avg_loss
            rsi_vals.append(100.0 - (100.0 / (1.0 + rs)))
    if len(rsi_vals) < stoch_period:
        return 50.0
    recent = rsi_vals[-stoch_period:]
    rsi_min = min(recent)
    rsi_max = max(recent)
    if rsi_max == rsi_min:
        return 50.0
    return (rsi_vals[-1] - rsi_min) / (rsi_max - rsi_min) * 100

def compute_momentum(closes, period=10):
    """Price momentum (% degisim)."""
    if len(closes) < period + 1:
        return 0.0
    return (closes[-1] - closes[-period-1]) / closes[-period-1] * 100

def compute_atr(highs, lows, closes, period=14):
    if len(closes) < period + 1:
        return 0.0
    tr = []
    for i in range(1, len(closes)):
        tr.append(max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1])))
    if len(tr) < period:
        return np.mean(tr) if tr else 0.0
    atr = np.mean(tr[:period])
    for i in range(period, len(tr)):
        atr = (atr * (period-1) + tr[i]) / period
    return atr

def compute_volume_ratio(volumes, period=20):
    """Son mum hacmi / ortalama hacim."""
    if len(volumes) < period + 1:
        return 1.0
    avg = np.mean(volumes[-period-1:-1])
    if avg == 0:
        return 1.0
    return volumes[-1] / avg

def compute_bb_width(closes, period=20):
    """BB genisligi / orta bant (normalize)."""
    upper, middle, lower = compute_bb(closes, period)
    if middle == 0:
        return 0
    return (upper - lower) / middle * 100

def compute_bb_position(closes, period=20):
    """Fiyatin BB icindeki konumu: 0=alt, 1=ust."""
    upper, middle, lower = compute_bb(closes, period)
    if upper == lower:
        return 0.5
    return (closes[-1] - lower) / (upper - lower)

# ============================================================
# DIRECTION INDICATORS (each returns +1 LONG, -1 SHORT, 0 SKIP)
# ============================================================

def ind_ema_9_21(closes):
    f = _ema_val(closes, 9)
    s = _ema_val(closes, 21)
    if f == 0 or s == 0:
        return 0
    gap = abs(f - s) / s
    if gap < 0.0005:
        return 0
    return 1 if f > s else -1

def ind_ema_21_50(closes):
    f = _ema_val(closes, 21)
    s = _ema_val(closes, 50)
    if f == 0 or s == 0:
        return 0
    gap = abs(f - s) / s
    if gap < 0.0005:
        return 0
    return 1 if f > s else -1

def ind_ema_9_50(closes):
    f = _ema_val(closes, 9)
    s = _ema_val(closes, 50)
    if f == 0 or s == 0:
        return 0
    gap = abs(f - s) / s
    if gap < 0.0005:
        return 0
    return 1 if f > s else -1

def ind_macd_hist(closes):
    _, _, hist = compute_macd(closes)
    if abs(hist) < 1e-10:
        return 0
    return 1 if hist > 0 else -1

def ind_macd_cross(closes):
    """MACD line > signal = LONG."""
    ml, sl, _ = compute_macd(closes)
    if ml == 0 and sl == 0:
        return 0
    return 1 if ml > sl else -1

def ind_rsi_trend(closes, period=14):
    """RSI > 55 LONG, < 45 SHORT."""
    rsi = compute_rsi(closes, period)
    if rsi > 55:
        return 1
    elif rsi < 45:
        return -1
    return 0

def ind_rsi_ranging(closes, period=14):
    """RSI ters mantik: >70 SHORT (alinacak), <30 LONG (satilacak)."""
    rsi = compute_rsi(closes, period)
    if rsi > 70:
        return -1
    elif rsi < 30:
        return 1
    return 0

def ind_adx_di(highs, lows, closes):
    """DI+ > DI- = LONG."""
    _, plus_di, minus_di = compute_adx(highs, lows, closes)
    if abs(plus_di - minus_di) < 2:
        return 0
    return 1 if plus_di > minus_di else -1

def ind_obv(closes, volumes):
    """OBV egimi pozitif = LONG."""
    slope = compute_obv_slope(closes, volumes)
    if abs(slope) < 0.01:
        return 0
    return 1 if slope > 0 else -1

def ind_vwap(closes, volumes, highs, lows):
    """Fiyat VWAP ustunde = LONG."""
    pos = compute_vwap_position(closes, volumes, highs, lows)
    if abs(pos) < 0.05:
        return 0
    return 1 if pos > 0 else -1

def ind_momentum(closes, period=10):
    """Momentum pozitif = LONG."""
    mom = compute_momentum(closes, period)
    if abs(mom) < 0.1:
        return 0
    return 1 if mom > 0 else -1

def ind_stoch_rsi(closes):
    """StochRSI > 80 SHORT (overbought), < 20 LONG (oversold)."""
    sr = compute_stoch_rsi(closes)
    if sr > 80:
        return -1
    elif sr < 20:
        return 1
    return 0

def ind_bb_position(closes):
    """BB ust yakininda SHORT, alt yakininda LONG (ranging)."""
    pos = compute_bb_position(closes)
    if pos > 0.9:
        return -1
    elif pos < 0.1:
        return 1
    return 0

def ind_price_above_sma(closes, period=50):
    """Fiyat SMA ustunde = LONG."""
    if len(closes) < period:
        return 0
    sma = np.mean(closes[-period:])
    gap = (closes[-1] - sma) / sma
    if abs(gap) < 0.001:
        return 0
    return 1 if closes[-1] > sma else -1

def ind_higher_highs(highs, lows, n=5):
    """Son N swing: higher high + higher low = LONG."""
    swings = detect_zigzag_swings(highs, lows, n)
    if len(swings) < 4:
        return 0
    last4 = swings[-4:]
    shs = [s for s in last4 if s.type == "SH"]
    sls = [s for s in last4 if s.type == "SL"]
    if len(shs) >= 2 and len(sls) >= 2:
        if shs[-1].price > shs[-2].price and sls[-1].price > sls[-2].price:
            return 1
        if shs[-1].price < shs[-2].price and sls[-1].price < sls[-2].price:
            return -1
    return 0

def ind_wave_direction(highs, lows, n=5):
    """Son dalga yonu: son swing SH ise SHORT (tepe), SL ise LONG (dip)."""
    swings = detect_zigzag_swings(highs, lows, n)
    if len(swings) < 2:
        return 0
    last = swings[-1]
    return -1 if last.type == "SH" else 1

# ============================================================
# REGIME INDICATORS
# ============================================================

def regime_er(closes, window=20, median_n=10):
    er = compute_rolling_er(closes, window, median_n)
    if er > 0.25:
        return "TRENDING", er
    elif er < 0.08:
        return "RANGING", er
    return "GRAY", er

def regime_hurst(closes):
    h = compute_hurst_exponent(closes) if len(closes) >= 128 else 0.5
    if h > 0.55:
        return "TRENDING", h
    elif h < 0.45:
        return "RANGING", h
    return "GRAY", h

def regime_adx(highs, lows, closes, period=14):
    adx, _, _ = compute_adx(highs, lows, closes, period)
    if adx > 25:
        return "TRENDING", adx
    elif adx < 18:
        return "RANGING", adx
    return "GRAY", adx

def regime_bb_width(closes, period=20):
    w = compute_bb_width(closes, period)
    if w > 4.0:
        return "TRENDING", w
    elif w < 1.5:
        return "RANGING", w
    return "GRAY", w

def regime_combined_default_ranging(closes, highs, lows):
    """Default RANGING kabul et, sadece guclu kanit ile TRENDING'e gec.
    En az 2/3 indikator TRENDING diyorsa TRENDING."""
    r_er, er_val = regime_er(closes)
    r_hurst, h_val = regime_hurst(closes)
    r_adx, adx_val = regime_adx(highs, lows, closes)
    r_bb, bb_val = regime_bb_width(closes)

    trend_votes = sum(1 for r in [r_er, r_hurst, r_adx, r_bb] if r == "TRENDING")
    range_votes = sum(1 for r in [r_er, r_hurst, r_adx, r_bb] if r == "RANGING")

    # Default RANGING: sadece guclu trend kaniti ile degistir
    if trend_votes >= 3:
        return "TRENDING", trend_votes / 4.0
    return "RANGING", (4 - trend_votes) / 4.0

# ============================================================
# VERIFY FUNCTIONS
# ============================================================

def verify_direction(forward_closes, threshold_pct=0.05):
    if len(forward_closes) < 2:
        return "SKIP"
    change = (forward_closes[-1] - forward_closes[0]) / forward_closes[0] * 100
    if abs(change) < threshold_pct:
        return "SKIP"
    return "LONG" if change > 0 else "SHORT"

def verify_regime_actual(forward_closes):
    if len(forward_closes) < 5:
        return "UNDECIDED"
    er = compute_rolling_er(forward_closes,
                            window=min(20, len(forward_closes)-1),
                            median_count=min(5, max(1, len(forward_closes)-10)))
    if er > 0.20:
        return "TRENDING"
    elif er < 0.10:
        return "RANGING"
    return "UNDECIDED"

# ============================================================
# G-BASED TRADE SIMULATION
# ============================================================

def simulate_g_trade(closes, highs, lows, direction, G, sl_mult, tp_mult, fee_pct=0.08):
    """G bazli trade simule et.
    direction: 1=LONG, -1=SHORT
    SL = sl_mult * G (% olarak)
    TP = tp_mult * G (% olarak)
    Returns: (hit_tp, hit_sl, pnl_pct, bars_held)
    """
    if G <= 0 or len(closes) < 2:
        return None, None, 0, 0

    entry = closes[0]
    sl_dist = sl_mult * G / 100.0 * entry
    tp_dist = tp_mult * G / 100.0 * entry
    total_fee = fee_pct * 2 / 100.0 * entry  # giris + cikis fee

    for i in range(1, len(closes)):
        if direction == 1:  # LONG
            # SL check (low ile)
            if lows[i] <= entry - sl_dist:
                pnl = -sl_mult * G - fee_pct * 2
                return False, True, pnl, i
            # TP check (high ile)
            if highs[i] >= entry + tp_dist:
                pnl = tp_mult * G - fee_pct * 2
                return True, False, pnl, i
        else:  # SHORT
            if highs[i] >= entry + sl_dist:
                pnl = -sl_mult * G - fee_pct * 2
                return False, True, pnl, i
            if lows[i] <= entry - tp_dist:
                pnl = tp_mult * G - fee_pct * 2
                return True, False, pnl, i

    # Timeout — close at last price
    if direction == 1:
        pnl = (closes[-1] - entry) / entry * 100 - fee_pct * 2
    else:
        pnl = (entry - closes[-1]) / entry * 100 - fee_pct * 2
    return None, None, pnl, len(closes) - 1

# ============================================================
# MAIN
# ============================================================

def run():
    print("=" * 80)
    print("SYSTEM J DIRECTION RESEARCH — Kapsamli Backtest")
    print("=" * 80)

    client = BinanceRestClient()

    # --- Veri cek ---
    print("\n[1/5] Veri cekiliyor...")
    all_data = {}
    for coin in COINS:
        all_data[coin] = {}
        for tf in TIMEFRAMES:
            try:
                df = client.get_klines(coin, tf, KLINE_LIMITS[tf])
                all_data[coin][tf] = df
                print(f"  {coin} {tf}: {len(df)} mum")
                time.sleep(0.05)
            except Exception as e:
                print(f"  {coin} {tf}: HATA - {e}")
                all_data[coin][tf] = pd.DataFrame()

    # ================================================================
    # BOLUM 1: REJIM DAGILIMI
    # ================================================================
    print("\n" + "=" * 80)
    print("[2/5] BOLUM 1: REJIM DAGILIMI — Her TF'de ne kadar trending vs ranging?")
    print("=" * 80)

    regime_dist = {}
    for coin in COINS:
        regime_dist[coin] = {}
        for tf in TIMEFRAMES:
            df = all_data[coin][tf]
            if df.empty:
                continue
            closes = df["close"].values.astype(float)
            highs = df["high"].values.astype(float)
            lows = df["low"].values.astype(float)

            counts = {"TRENDING": 0, "RANGING": 0, "GRAY": 0}
            counts_default_ranging = {"TRENDING": 0, "RANGING": 0}
            actual_counts = {"TRENDING": 0, "RANGING": 0, "UNDECIDED": 0}

            step = STEP_SIZE[tf]
            fw = FORWARD_WINDOWS[tf]
            for start in range(0, len(closes) - ANALYSIS_WINDOW - fw, step):
                end = start + ANALYSIS_WINDOW
                wc = closes[start:end]
                wh = highs[start:end]
                wl = lows[start:end]
                fc = closes[end:end+fw]

                # ER-only regime
                r, _ = regime_er(wc)
                counts[r] += 1

                # Default ranging combined
                r2, _ = regime_combined_default_ranging(wc, wh, wl)
                counts_default_ranging[r2] += 1

                # Actual
                actual = verify_regime_actual(fc)
                actual_counts[actual] += 1

            regime_dist[coin][tf] = {
                "er_only": counts,
                "default_ranging": counts_default_ranging,
                "actual": actual_counts,
            }

    # Print
    print(f"\n{'Coin/TF':<14} {'ER-Trend':>10} {'ER-Range':>10} {'ER-Gray':>10} | {'DefR-Trend':>12} {'DefR-Range':>12} | {'Actual-T':>10} {'Actual-R':>10} {'Actual-U':>10}")
    print("-" * 120)
    for coin in COINS:
        for tf in TIMEFRAMES:
            if tf not in regime_dist.get(coin, {}):
                continue
            d = regime_dist[coin][tf]
            er = d["er_only"]
            dr = d["default_ranging"]
            ac = d["actual"]
            tot_er = sum(er.values())
            tot_ac = sum(ac.values())
            sc = coin.replace("USDT","")
            print(f"{sc:>6}/{tf:<6}"
                  f" {er['TRENDING']:>5}({er['TRENDING']/tot_er*100:>4.0f}%)"
                  f" {er['RANGING']:>5}({er['RANGING']/tot_er*100:>4.0f}%)"
                  f" {er['GRAY']:>5}({er['GRAY']/tot_er*100:>4.0f}%)"
                  f" | {dr['TRENDING']:>5}({dr['TRENDING']/tot_er*100:>4.0f}%)"
                  f" {dr['RANGING']:>5}({dr['RANGING']/tot_er*100:>4.0f}%)"
                  f" | {ac['TRENDING']:>5}({ac['TRENDING']/tot_ac*100:>4.0f}%)"
                  f" {ac['RANGING']:>5}({ac['RANGING']/tot_ac*100:>4.0f}%)"
                  f" {ac['UNDECIDED']:>5}({ac['UNDECIDED']/tot_ac*100:>4.0f}%)")

    # Default ranging vs ER-only accuracy
    print("\n--- Default-RANGING Rejim Tespiti Dogrulugu ---")
    print(f"{'Coin/TF':<14} {'ER-only Dogru':>14} {'DefRanging Dogru':>16} {'Fark':>8}")
    print("-" * 55)
    for coin in COINS:
        for tf in TIMEFRAMES:
            if tf not in regime_dist.get(coin, {}):
                continue
            df = all_data[coin][tf]
            if df.empty:
                continue
            closes = df["close"].values.astype(float)
            highs = df["high"].values.astype(float)
            lows = df["low"].values.astype(float)

            er_correct = 0
            dr_correct = 0
            total = 0
            step = STEP_SIZE[tf]
            fw = FORWARD_WINDOWS[tf]
            for start in range(0, len(closes) - ANALYSIS_WINDOW - fw, step):
                end = start + ANALYSIS_WINDOW
                wc = closes[start:end]
                wh = highs[start:end]
                wl = lows[start:end]
                fc = closes[end:end+fw]

                actual = verify_regime_actual(fc)
                if actual == "UNDECIDED":
                    continue

                r_er, _ = regime_er(wc)
                if r_er == "GRAY":
                    r_er = "RANGING"  # gray -> ranging for comparison
                r_dr, _ = regime_combined_default_ranging(wc, wh, wl)

                total += 1
                if r_er == actual:
                    er_correct += 1
                if r_dr == actual:
                    dr_correct += 1

            if total > 0:
                er_pct = er_correct / total * 100
                dr_pct = dr_correct / total * 100
                sc = coin.replace("USDT","")
                print(f"{sc:>6}/{tf:<6} {er_pct:>12.1f}% {dr_pct:>14.1f}% {dr_pct-er_pct:>+7.1f}%")

    # ================================================================
    # BOLUM 2: INDIKATOR BAZLI YON DOGRULUGU
    # ================================================================
    print("\n" + "=" * 80)
    print("[3/5] BOLUM 2: Indikator Bazli Yon Dogrulugu")
    print("=" * 80)

    # Indikator listesi
    INDICATORS = {
        "EMA 9/21":      lambda c,h,l,v: ind_ema_9_21(c),
        "EMA 21/50":     lambda c,h,l,v: ind_ema_21_50(c),
        "EMA 9/50":      lambda c,h,l,v: ind_ema_9_50(c),
        "MACD Hist":     lambda c,h,l,v: ind_macd_hist(c),
        "MACD Cross":    lambda c,h,l,v: ind_macd_cross(c),
        "RSI Trend":     lambda c,h,l,v: ind_rsi_trend(c),
        "RSI Ranging":   lambda c,h,l,v: ind_rsi_ranging(c),
        "ADX DI":        lambda c,h,l,v: ind_adx_di(h,l,c),
        "OBV Slope":     lambda c,h,l,v: ind_obv(c,v),
        "VWAP":          lambda c,h,l,v: ind_vwap(c,v,h,l),
        "Momentum10":    lambda c,h,l,v: ind_momentum(c, 10),
        "Momentum20":    lambda c,h,l,v: ind_momentum(c, 20),
        "StochRSI":      lambda c,h,l,v: ind_stoch_rsi(c),
        "BB Position":   lambda c,h,l,v: ind_bb_position(c),
        "SMA50":         lambda c,h,l,v: ind_price_above_sma(c, 50),
        "HigherHighs":   lambda c,h,l,v: ind_higher_highs(h,l),
        "WaveDir":       lambda c,h,l,v: ind_wave_direction(h,l),
    }

    # Per TF results
    ind_results = {tf: {name: {"correct":0, "wrong":0, "skip":0} for name in INDICATORS}
                   for tf in TIMEFRAMES}

    # Regime-separated results
    ind_results_trend = {tf: {name: {"correct":0, "wrong":0, "skip":0} for name in INDICATORS}
                         for tf in TIMEFRAMES}
    ind_results_range = {tf: {name: {"correct":0, "wrong":0, "skip":0} for name in INDICATORS}
                         for tf in TIMEFRAMES}

    for coin in COINS:
        for tf in TIMEFRAMES:
            df = all_data[coin][tf]
            if df.empty:
                continue
            closes = df["close"].values.astype(float)
            highs = df["high"].values.astype(float)
            lows = df["low"].values.astype(float)
            volumes = df["volume"].values.astype(float)
            step = STEP_SIZE[tf]
            fw = FORWARD_WINDOWS[tf]

            for start in range(0, len(closes) - ANALYSIS_WINDOW - fw, step):
                end = start + ANALYSIS_WINDOW
                wc = closes[start:end]
                wh = highs[start:end]
                wl = lows[start:end]
                wv = volumes[start:end]
                fc = closes[end:end+fw]

                actual_dir = verify_direction(fc)
                actual_regime = verify_regime_actual(fc)

                for name, func in INDICATORS.items():
                    pred = func(wc, wh, wl, wv)
                    if pred == 0 or actual_dir == "SKIP":
                        ind_results[tf][name]["skip"] += 1
                        if actual_regime == "TRENDING":
                            ind_results_trend[tf][name]["skip"] += 1
                        elif actual_regime == "RANGING":
                            ind_results_range[tf][name]["skip"] += 1
                        continue

                    pred_dir = "LONG" if pred > 0 else "SHORT"
                    is_correct = pred_dir == actual_dir

                    bucket = ind_results[tf][name]
                    if is_correct:
                        bucket["correct"] += 1
                    else:
                        bucket["wrong"] += 1

                    if actual_regime == "TRENDING":
                        b2 = ind_results_trend[tf][name]
                        if is_correct:
                            b2["correct"] += 1
                        else:
                            b2["wrong"] += 1
                    elif actual_regime == "RANGING":
                        b2 = ind_results_range[tf][name]
                        if is_correct:
                            b2["correct"] += 1
                        else:
                            b2["wrong"] += 1

    # Print per TF
    for tf in TIMEFRAMES:
        print(f"\n--- {tf} ---")
        print(f"{'Indikator':<16} {'Decided':>8} {'Dogru':>6} {'Oran%':>7} | {'Trend%':>8} {'Range%':>8} | {'Skip%':>7}")
        print("-" * 75)
        # Sort by accuracy
        sorted_inds = sorted(INDICATORS.keys(),
                             key=lambda n: ind_results[tf][n]["correct"] /
                             max(1, ind_results[tf][n]["correct"]+ind_results[tf][n]["wrong"]),
                             reverse=True)
        for name in sorted_inds:
            r = ind_results[tf][name]
            decided = r["correct"] + r["wrong"]
            rate = (r["correct"] / decided * 100) if decided > 0 else 0
            total = decided + r["skip"]
            skip_pct = r["skip"] / total * 100 if total > 0 else 0

            rt = ind_results_trend[tf][name]
            rr = ind_results_range[tf][name]
            dt = rt["correct"] + rt["wrong"]
            dr_val = rr["correct"] + rr["wrong"]
            trend_pct = (rt["correct"] / dt * 100) if dt > 0 else 0
            range_pct = (rr["correct"] / dr_val * 100) if dr_val > 0 else 0

            print(f"{name:<16} {decided:>8} {r['correct']:>6} {rate:>6.1f}% | {trend_pct:>6.1f}% {range_pct:>7.1f}% | {skip_pct:>6.1f}%")

    # ================================================================
    # BOLUM 3: EN IYI INDIKATOR KOMBINASYONLARI
    # ================================================================
    print("\n" + "=" * 80)
    print("[4/5] BOLUM 3: En Iyi Indikator Kombinasyonlari (2'li ve 3'lu)")
    print("=" * 80)

    # Top indikatorlden 2'li ve 3'lu kombinasyonlar test et
    # Once en iyi 10 indikatoru bul (1h bazli)
    ind_names = list(INDICATORS.keys())

    # 2'li kombinasyonlar
    for tf in TIMEFRAMES:
        print(f"\n--- {tf} - 2'li Kombinasyonlar (Oylama: 2/2 uyum) ---")
        print(f"{'Kombinasyon':<35} {'Decided':>8} {'Dogru':>6} {'Oran%':>7} {'Skip%':>7}")
        print("-" * 65)

        combo_results = []
        for i in range(len(ind_names)):
            for j in range(i+1, len(ind_names)):
                n1, n2 = ind_names[i], ind_names[j]
                correct = 0
                wrong = 0
                skip = 0

                for coin in COINS:
                    df = all_data[coin][tf]
                    if df.empty:
                        continue
                    closes = df["close"].values.astype(float)
                    highs = df["high"].values.astype(float)
                    lows = df["low"].values.astype(float)
                    volumes = df["volume"].values.astype(float)
                    step = STEP_SIZE[tf]
                    fw = FORWARD_WINDOWS[tf]
                    for start in range(0, len(closes) - ANALYSIS_WINDOW - fw, step):
                        end = start + ANALYSIS_WINDOW
                        wc = closes[start:end]
                        wh = highs[start:end]
                        wl = lows[start:end]
                        wv = volumes[start:end]
                        fc = closes[end:end+fw]
                        actual_dir = verify_direction(fc)
                        if actual_dir == "SKIP":
                            skip += 1
                            continue

                        v1 = INDICATORS[n1](wc, wh, wl, wv)
                        v2 = INDICATORS[n2](wc, wh, wl, wv)

                        if v1 == 0 or v2 == 0 or v1 != v2:
                            skip += 1
                            continue

                        pred = "LONG" if v1 > 0 else "SHORT"
                        if pred == actual_dir:
                            correct += 1
                        else:
                            wrong += 1

                decided = correct + wrong
                if decided > 30:
                    rate = correct / decided * 100
                    total = decided + skip
                    combo_results.append((f"{n1}+{n2}", decided, correct, rate, skip/total*100))

        combo_results.sort(key=lambda x: x[3], reverse=True)
        for name, dec, cor, rate, skip_pct in combo_results[:15]:
            print(f"{name:<35} {dec:>8} {cor:>6} {rate:>6.1f}% {skip_pct:>6.1f}%")

    # 3'lu kombinasyonlar (top 8 indikatorlerle)
    for tf in ["15m", "1h"]:
        print(f"\n--- {tf} - 3'lu Kombinasyonlar (Oylama: 2/3 cogunluk) ---")
        print(f"{'Kombinasyon':<50} {'Decided':>8} {'Dogru':>6} {'Oran%':>7}")
        print("-" * 75)

        # top 8 by individual accuracy for this tf
        sorted_by_acc = sorted(ind_names,
                               key=lambda n: ind_results[tf][n]["correct"] /
                               max(1, ind_results[tf][n]["correct"]+ind_results[tf][n]["wrong"]),
                               reverse=True)
        top8 = sorted_by_acc[:10]

        combo3_results = []
        for i in range(len(top8)):
            for j in range(i+1, len(top8)):
                for k in range(j+1, len(top8)):
                    n1, n2, n3 = top8[i], top8[j], top8[k]
                    correct = 0
                    wrong = 0
                    skip = 0

                    for coin in COINS:
                        df = all_data[coin][tf]
                        if df.empty:
                            continue
                        closes = df["close"].values.astype(float)
                        highs = df["high"].values.astype(float)
                        lows = df["low"].values.astype(float)
                        volumes = df["volume"].values.astype(float)
                        step = STEP_SIZE[tf]
                        fw = FORWARD_WINDOWS[tf]
                        for start in range(0, len(closes) - ANALYSIS_WINDOW - fw, step):
                            end = start + ANALYSIS_WINDOW
                            wc = closes[start:end]
                            wh = highs[start:end]
                            wl = lows[start:end]
                            wv = volumes[start:end]
                            fc = closes[end:end+fw]
                            actual_dir = verify_direction(fc)
                            if actual_dir == "SKIP":
                                skip += 1
                                continue

                            v1 = INDICATORS[n1](wc, wh, wl, wv)
                            v2 = INDICATORS[n2](wc, wh, wl, wv)
                            v3 = INDICATORS[n3](wc, wh, wl, wv)

                            votes = v1 + v2 + v3
                            if abs(votes) < 2:
                                skip += 1
                                continue

                            pred = "LONG" if votes > 0 else "SHORT"
                            if pred == actual_dir:
                                correct += 1
                            else:
                                wrong += 1

                    decided = correct + wrong
                    if decided > 30:
                        rate = correct / decided * 100
                        combo3_results.append((f"{n1}+{n2}+{n3}", decided, correct, rate))

        combo3_results.sort(key=lambda x: x[3], reverse=True)
        for name, dec, cor, rate in combo3_results[:15]:
            print(f"{name:<50} {dec:>8} {cor:>6} {rate:>6.1f}%")

    # ================================================================
    # BOLUM 4: G BAZLI SL/TP ANALIZI
    # ================================================================
    print("\n" + "=" * 80)
    print("[5/5] BOLUM 4: G Bazli SL/TP Optimizasyonu + Tutma Suresi")
    print("=" * 80)

    SL_MULTS = [0.5, 0.75, 1.0, 1.25, 1.5, 2.0]
    TP_MULTS = [1.0, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0]

    for tf in TIMEFRAMES:
        print(f"\n--- {tf} ---")
        print(f"{'SL_G':>6} {'TP_G':>6} | {'Trades':>7} {'Win':>5} {'Loss':>5} {'WinR%':>7} | {'Avg PnL':>8} {'Tot PnL':>9} | {'AvgBars':>8}")
        print("-" * 75)

        best_combo = None
        best_pnl = -999

        for sl_m in SL_MULTS:
            for tp_m in TP_MULTS:
                if tp_m / sl_m < 1.5:
                    continue
                wins = 0
                losses = 0
                timeouts = 0
                total_pnl = 0
                total_bars = 0
                n_trades = 0

                for coin in COINS:
                    df = all_data[coin][tf]
                    if df.empty:
                        continue
                    closes = df["close"].values.astype(float)
                    highs = df["high"].values.astype(float)
                    lows = df["low"].values.astype(float)
                    step = STEP_SIZE[tf]
                    fw = FORWARD_WINDOWS[tf]

                    for start in range(0, len(closes) - ANALYSIS_WINDOW - fw, step):
                        end = start + ANALYSIS_WINDOW
                        wc = closes[start:end]
                        wh = highs[start:end]
                        wl = lows[start:end]
                        fc = closes[end:end+fw]
                        fh = highs[end:end+fw]
                        fl = lows[end:end+fw]

                        # G hesapla
                        swings = detect_zigzag_swings(wh, wl, n=5)
                        if len(swings) < 3:
                            continue
                        wa = analyze_waves(swings, wc[-1])
                        G = wa.G
                        if G <= 0:
                            continue

                        # Yon: simple momentum (en iyi combo sonrasi iyilestirilecek)
                        actual_dir = verify_direction(fc)
                        if actual_dir == "SKIP":
                            continue
                        direction = 1 if actual_dir == "LONG" else -1

                        hit_tp, hit_sl, pnl, bars = simulate_g_trade(
                            fc, fh, fl, direction, G, sl_m, tp_m, FEE_PCT)

                        n_trades += 1
                        total_pnl += pnl
                        total_bars += bars
                        if hit_tp:
                            wins += 1
                        elif hit_sl:
                            losses += 1
                        else:
                            timeouts += 1

                if n_trades > 20:
                    win_rate = wins / n_trades * 100
                    avg_pnl = total_pnl / n_trades
                    avg_bars = total_bars / n_trades

                    if avg_pnl > best_pnl:
                        best_pnl = avg_pnl
                        best_combo = (sl_m, tp_m)

                    print(f"{sl_m:>5.2f}x {tp_m:>5.1f}x | {n_trades:>7} {wins:>5} {losses:>5} {win_rate:>6.1f}% | {avg_pnl:>7.3f}% {total_pnl:>8.1f}% | {avg_bars:>7.1f}")

        if best_combo:
            print(f"\n  >> En iyi: SL={best_combo[0]:.2f}G, TP={best_combo[1]:.1f}G, Avg PnL={best_pnl:.3f}%")

    # ================================================================
    # BOLUM 5: DALGA SAYISI VE TUTMA SURESI
    # ================================================================
    print("\n" + "=" * 80)
    print("BOLUM 5: Ortalama Dalga Boyutlari ve G Degerleri")
    print("=" * 80)

    print(f"\n{'Coin/TF':<14} {'G(avg%)':>8} {'I(fwd%)':>8} {'WaveN':>7} {'CV':>6} {'AvgSwing':>10}")
    print("-" * 60)

    for coin in COINS:
        for tf in TIMEFRAMES:
            df = all_data[coin][tf]
            if df.empty:
                continue
            closes = df["close"].values.astype(float)
            highs = df["high"].values.astype(float)
            lows = df["low"].values.astype(float)

            gs, i_vals, cvs, wave_counts = [], [], [], []

            step = STEP_SIZE[tf] * 3
            for start in range(0, len(closes) - ANALYSIS_WINDOW, step):
                end = start + ANALYSIS_WINDOW
                wh = highs[start:end]
                wl = lows[start:end]
                swings = detect_zigzag_swings(wh, wl, n=5)
                if len(swings) < 3:
                    continue
                wa = analyze_waves(swings, closes[end-1])
                if wa.G > 0:
                    gs.append(wa.G)
                if wa.I > 0:
                    i_vals.append(wa.I)
                if wa.cv > 0:
                    cvs.append(wa.cv)
                wave_counts.append(len(wa.forward_waves) + len(wa.backward_waves))

            if gs:
                sc = coin.replace("USDT","")
                wc_mean = np.mean(wave_counts) if wave_counts else 0
                avg_swing_bars = ANALYSIS_WINDOW / (wc_mean + 1) if wc_mean > 0 else 0
                print(f"{sc:>6}/{tf:<6} {np.mean(gs):>7.3f}% {np.mean(i_vals):>7.3f}% "
                      f"{np.mean(wave_counts):>6.1f} {np.mean(cvs):>5.2f} {avg_swing_bars:>9.1f} mum")

    print("\n" + "=" * 80)
    print("ANALIZ TAMAMLANDI")
    print("=" * 80)
    print("\nOzet:")
    print("  1) Rejim dagilimi: coinler TF'ye gore ne kadar trend/range'de")
    print("  2) Indikator dogruluklari: hangi indikator yonu en iyi tespit ediyor")
    print("  3) Kombinasyonlar: 2'li ve 3'lu en iyi indikator gruplari")
    print("  4) G bazli SL/TP: optimal risk/odul oranlari")
    print("  5) Dalga istatistikleri: G, I, dalga sayisi, CV")


if __name__ == "__main__":
    run()
