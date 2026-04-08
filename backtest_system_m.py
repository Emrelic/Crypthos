"""System M AlphaTrend PRO - Son 5 saat backtest.

Top 50 coin icin 5m mumlarini ceker, her mum adiminda AlphaTrend sinyallerini
tarar ve BUY/SELL sinyallerini listeler.
"""
import time
import json
import numpy as np
import requests
from datetime import datetime, timedelta

# ══════════════════════════════════════════════════
#  AlphaTrend hesaplama (system_m_scanner.py'den)
# ══════════════════════════════════════════════════

def _sma(data, period):
    out = np.full_like(data, np.nan, dtype=float)
    if len(data) < period:
        return out
    cumsum = np.cumsum(data)
    cumsum[period:] = cumsum[period:] - cumsum[:-period]
    out[period - 1:] = cumsum[period - 1:] / period
    return out

def _rma(data, period):
    out = np.full_like(data, np.nan, dtype=float)
    if len(data) < period:
        return out
    out[period - 1] = np.mean(data[:period])
    alpha = 1.0 / period
    for i in range(period, len(data)):
        out[i] = alpha * data[i] + (1 - alpha) * out[i - 1]
    return out

def _true_range(high, low, close):
    tr = np.empty(len(high), dtype=float)
    tr[0] = high[0] - low[0]
    for i in range(1, len(high)):
        tr[i] = max(high[i] - low[i],
                     abs(high[i] - close[i - 1]),
                     abs(low[i] - close[i - 1]))
    return tr

def _compute_rsi(close, period):
    delta = np.concatenate([[0.0], np.diff(close)])
    gain = np.where(delta > 0, delta, 0.0)
    loss = np.where(delta < 0, -delta, 0.0)
    avg_gain = _rma(gain, period)
    avg_loss = _rma(loss, period)
    rs = np.where(avg_loss > 0, avg_gain / avg_loss, 100.0)
    return 100.0 - 100.0 / (1.0 + rs)

def _compute_mfi(high, low, close, volume, period):
    typical = (high + low + close) / 3.0
    raw_mf = typical * volume
    n = len(close)
    mfi = np.full(n, 50.0)
    if n < period + 1:
        return mfi
    for i in range(period, n):
        pos_flow = neg_flow = 0.0
        for j in range(1, period + 1):
            idx = i - period + j
            if typical[idx] > typical[idx - 1]:
                pos_flow += raw_mf[idx]
            elif typical[idx] < typical[idx - 1]:
                neg_flow += raw_mf[idx]
        if neg_flow > 0:
            mfi[i] = 100.0 - 100.0 / (1.0 + pos_flow / neg_flow)
        else:
            mfi[i] = 100.0
    return mfi

def _compute_adx(high, low, close, period):
    n = len(high)
    plus_dm = np.zeros(n)
    minus_dm = np.zeros(n)
    tr = _true_range(high, low, close)
    for i in range(1, n):
        up = high[i] - high[i - 1]
        down = low[i - 1] - low[i]
        plus_dm[i] = up if (up > down and up > 0) else 0.0
        minus_dm[i] = down if (down > up and down > 0) else 0.0
    smoothed_tr = _rma(tr, period)
    smoothed_plus = _rma(plus_dm, period)
    smoothed_minus = _rma(minus_dm, period)
    pdi = np.where(smoothed_tr > 0, 100.0 * smoothed_plus / smoothed_tr, 0.0)
    mdi = np.where(smoothed_tr > 0, 100.0 * smoothed_minus / smoothed_tr, 0.0)
    dx_sum = pdi + mdi
    dx = np.where(dx_sum > 0, 100.0 * np.abs(pdi - mdi) / dx_sum, 0.0)
    adx = _rma(dx, period)
    return adx, pdi, mdi

def compute_alpha_trend(high, low, close, volume, coeff, period, use_mfi=True):
    n = len(close)
    tr = _true_range(high, low, close)
    atr = _sma(tr, period)
    up_t = low - atr * coeff
    down_t = high + atr * coeff
    if use_mfi:
        trend_val = _compute_mfi(high, low, close, volume, period)
    else:
        trend_val = _compute_rsi(close, period)
    alpha_trend = np.full(n, np.nan)
    start_idx = period - 1
    if np.isnan(atr[start_idx]):
        start_idx = period
    if start_idx >= n:
        return alpha_trend, atr
    alpha_trend[start_idx] = close[start_idx]
    for i in range(start_idx + 1, n):
        if np.isnan(atr[i]):
            alpha_trend[i] = alpha_trend[i - 1]
            continue
        prev = alpha_trend[i - 1]
        if trend_val[i] >= 50:
            val = up_t[i]
            alpha_trend[i] = max(val, prev) if not np.isnan(val) else prev
        else:
            val = down_t[i]
            alpha_trend[i] = min(val, prev) if not np.isnan(val) else prev
    return alpha_trend, atr


# ══════════════════════════════════════════════════
#  Binance API
# ══════════════════════════════════════════════════
BASE = "https://fapi.binance.com"

def get_top_symbols(n=50):
    """Top N coin by 24h volume."""
    r = requests.get(f"{BASE}/fapi/v1/ticker/24hr", timeout=10)
    data = r.json()
    usdt = [d for d in data if d["symbol"].endswith("USDT")]
    usdt.sort(key=lambda x: float(x["quoteVolume"]), reverse=True)
    return [d["symbol"] for d in usdt[:n]]

def get_klines(symbol, interval="5m", limit=500):
    r = requests.get(f"{BASE}/fapi/v1/klines", params={
        "symbol": symbol, "interval": interval, "limit": limit
    }, timeout=10)
    return r.json()


# ══════════════════════════════════════════════════
#  Backtest: son 5 saat sinyal tarasi
# ══════════════════════════════════════════════════
def backtest_symbol(symbol, klines_raw, hours=5,
                    coeff=3.6, period=27, adx_length=14,
                    adx_threshold=18.0, use_mfi=True):
    """Bir coin icin son N saat icerisindeki BUY/SELL sinyallerini bul."""

    if not klines_raw or len(klines_raw) < 150:
        return []

    opens = np.array([float(k[1]) for k in klines_raw], dtype=float)
    highs = np.array([float(k[2]) for k in klines_raw], dtype=float)
    lows = np.array([float(k[3]) for k in klines_raw], dtype=float)
    closes = np.array([float(k[4]) for k in klines_raw], dtype=float)
    volumes = np.array([float(k[5]) for k in klines_raw], dtype=float)
    timestamps = [int(k[0]) for k in klines_raw]

    n = len(closes)

    # AlphaTrend
    alpha_trend, atr_arr = compute_alpha_trend(
        highs, lows, closes, volumes,
        coeff=coeff, period=period, use_mfi=use_mfi)

    # ADX
    adx_arr, _, _ = _compute_adx(highs, lows, closes, adx_length)
    adx_sma = _sma(adx_arr, adx_length)

    # Son 5 saat = 60 mum (5m * 60 = 300dk = 5 saat)
    bars_in_window = hours * 12  # 5m bars per hour = 12
    start_bar = max(n - bars_in_window, 4)  # en az 4 bar lazim

    signals = []
    prev_direction = 0

    for i in range(start_bar, n):
        at_now = alpha_trend[i]
        at_1 = alpha_trend[i - 1]
        at_2 = alpha_trend[i - 2]
        at_3 = alpha_trend[i - 3]

        if any(np.isnan(v) for v in [at_now, at_1, at_2, at_3]):
            continue

        adx_val = adx_arr[i] if not np.isnan(adx_arr[i]) else 0
        adx_dyn = adx_sma[i] if not np.isnan(adx_sma[i]) else 0

        # Filtreler
        adx_static_ok = adx_val > adx_threshold
        adx_dynamic_ok = adx_val > adx_dyn
        final_filter = adx_static_ok and adx_dynamic_ok

        # Crossover
        buy_cross = (at_now > at_2) and (at_1 <= at_3)
        sell_cross = (at_now < at_2) and (at_1 >= at_3)

        buy_filtered = buy_cross and final_filter
        sell_filtered = sell_cross and final_filter

        new_dir = prev_direction
        if buy_filtered:
            new_dir = 1
        elif sell_filtered:
            new_dir = -1

        plot_buy = buy_filtered and prev_direction != 1
        plot_sell = sell_filtered and prev_direction != -1

        prev_direction = new_dir

        if plot_buy or plot_sell:
            ts_ms = timestamps[i]
            dt = datetime.fromtimestamp(ts_ms / 1000)
            atr_val = atr_arr[i] if not np.isnan(atr_arr[i]) else 0

            signals.append({
                "symbol": symbol,
                "signal": "BUY" if plot_buy else "SELL",
                "time": dt.strftime("%H:%M"),
                "price": closes[i],
                "adx": round(adx_val, 1),
                "atr": round(atr_val, 6),
                "alpha_trend": round(at_now, 6),
            })

    return signals


# ══════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════
if __name__ == "__main__":
    print("=" * 80)
    print("  SYSTEM M - AlphaTrend PRO Backtest (Son 5 Saat)")
    print(f"  Zaman: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("  Parametreler: coeff=3.6, period=27, ADX>18, MFI=True, TF=5m")
    print("=" * 80)

    # Config'den parametreler
    COEFF = 3.6
    PERIOD = 27
    ADX_LEN = 14
    ADX_THRESH = 18.0
    USE_MFI = True
    HOURS = 5
    KLINE_LIMIT = 400  # 5 saat = 60 mum, + warmup = ~400

    print(f"\n[1] Top 50 coin aliniyor...")
    symbols = get_top_symbols(50)
    print(f"    {len(symbols)} coin bulundu")

    all_signals = []
    errors = 0

    print(f"\n[2] Her coin icin son {HOURS} saat taraniyor (5m mumlar)...\n")

    for i, sym in enumerate(symbols):
        try:
            klines = get_klines(sym, "5m", KLINE_LIMIT)
            sigs = backtest_symbol(
                sym, klines, hours=HOURS,
                coeff=COEFF, period=PERIOD, adx_length=ADX_LEN,
                adx_threshold=ADX_THRESH, use_mfi=USE_MFI)
            all_signals.extend(sigs)

            status = f"  {i+1:2d}/50  {sym:<16s}"
            if sigs:
                for s in sigs:
                    arrow = "^" if s["signal"] == "BUY" else "v"
                    color_tag = "BUY " if s["signal"] == "BUY" else "SELL"
                    status += f"  {arrow} {color_tag} @ {s['time']} (${s['price']:.4f}, ADX:{s['adx']})"
                print(status)
            else:
                print(f"{status}  - sinyal yok")

            time.sleep(0.1)  # rate limit

        except Exception as e:
            print(f"  {i+1:2d}/50  {sym:<16s}  HATA: {e}")
            errors += 1

    # ══════════════════════════════════════════════════
    #  SONUC OZETI
    # ══════════════════════════════════════════════════
    print("\n" + "=" * 80)
    print("  SONUC OZETI")
    print("=" * 80)

    buy_signals = [s for s in all_signals if s["signal"] == "BUY"]
    sell_signals = [s for s in all_signals if s["signal"] == "SELL"]

    print(f"\n  Toplam sinyal : {len(all_signals)}")
    print(f"  ^ BUY         : {len(buy_signals)}")
    print(f"  v SELL        : {len(sell_signals)}")
    print(f"  Hata          : {errors}")
    print(f"  Sinyal olmayan: {50 - len(set(s['symbol'] for s in all_signals)) - errors}")

    if all_signals:
        # Zamana gore sirala
        all_signals.sort(key=lambda s: s["time"], reverse=True)

        print(f"\n  {'Saat':<8} {'Sembol':<16} {'Sinyal':<8} {'Fiyat':<14} {'ADX':<8} {'AlphaTrend'}")
        print(f"  {'-'*8} {'-'*16} {'-'*8} {'-'*14} {'-'*8} {'-'*14}")

        for s in all_signals:
            arrow = "^ BUY" if s["signal"] == "BUY" else "v SELL"
            print(f"  {s['time']:<8} {s['symbol']:<16} {arrow:<8} "
                  f"${s['price']:<13.6f} {s['adx']:<8} {s['alpha_trend']:.6f}")

    else:
        print("\n  Son 5 saatte hicbir sinyal uretilmedi!")
        print("  Olasi nedenler:")
        print("    - Piyasa duzgun trending degil (ADX < 18)")
        print("    - AlphaTrend crossover olusmadi")
        print("    - Parametreler cok siki (coeff cok yuksek)")

    print("\n" + "=" * 80)
