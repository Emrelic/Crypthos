"""System M — Son 15 saat backtest: bar-bar sinyal taraması.

Top 50 USDT-M futures coin için 5m kline çeker,
son 15 saatteki her bar'da AlphaTrend sinyallerini simüle eder.
"""

import sys, time, requests
import numpy as np
import pandas as pd
from datetime import datetime, timedelta

# ── Proje importları ──
sys.path.insert(0, ".")
from scanner.system_m_scanner import (
    compute_alpha_trend, _compute_adx, _compute_rsi, _compute_mfi, _sma,
)

# ═══════════════════════════════════════════════════════════════════
#  Config (config.json system_m ile aynı)
# ═══════════════════════════════════════════════════════════════════
COEFF = 3.6
PERIOD = 27
ADX_LENGTH = 14
ADX_THRESHOLD = 18.0
USE_ADX_STATIC = True
USE_ADX_DYNAMIC = True
ADX_DYN_MULT = 1.0
USE_SLOPE = False
SLOPE_FACTOR = 0.1
USE_MFI = True

LOOKBACK_HOURS = 15
TF = "5m"
BARS_PER_HOUR = 12  # 60/5
LOOKBACK_BARS = LOOKBACK_HOURS * BARS_PER_HOUR  # 180 bar = 15 saat
WARMUP_BARS = 300   # AlphaTrend warmup (config kline_limit)
TOTAL_KLINES = WARMUP_BARS + LOOKBACK_BARS  # 480

BASE_URL = "https://fapi.binance.com"

# ═══════════════════════════════════════════════════════════════════
#  Binance API helpers
# ═══════════════════════════════════════════════════════════════════
session = requests.Session()

def get_top_symbols(n=50, min_vol=5_000_000):
    resp = session.get(f"{BASE_URL}/fapi/v1/ticker/24hr", timeout=15)
    resp.raise_for_status()
    tickers = resp.json()
    usdt = [t for t in tickers
            if t["symbol"].endswith("USDT")
            and float(t["quoteVolume"]) >= min_vol
            and "1000" not in t["symbol"]
            and "_" not in t["symbol"]]
    usdt.sort(key=lambda t: float(t["quoteVolume"]), reverse=True)
    return [t["symbol"] for t in usdt[:n]]


def get_klines(symbol, interval="5m", limit=500):
    resp = session.get(f"{BASE_URL}/fapi/v1/klines",
                       params={"symbol": symbol, "interval": interval, "limit": limit},
                       timeout=15)
    resp.raise_for_status()
    return resp.json()


# ═══════════════════════════════════════════════════════════════════
#  Bar-bar sinyal simülasyonu
# ═══════════════════════════════════════════════════════════════════
def analyze_bars(symbol, klines_raw):
    """Son LOOKBACK_BARS bar için her bar'da sinyal üret.

    Her bar'da, o bar'a kadar olan tüm veriyi kullanarak AlphaTrend hesaplar.
    Bu, canlı taramanın bar kapanışındaki davranışını simüle eder.
    """
    n = len(klines_raw)
    if n < WARMUP_BARS + 10:
        return []

    # Parse full arrays
    timestamps = [int(k[0]) for k in klines_raw]
    highs  = np.array([float(k[2]) for k in klines_raw])
    lows   = np.array([float(k[3]) for k in klines_raw])
    closes = np.array([float(k[4]) for k in klines_raw])
    volumes = np.array([float(k[5]) for k in klines_raw])

    # Tüm veriyi bir kere hesapla (incremental değil, tam array)
    alpha_trend, atr_arr = compute_alpha_trend(highs, lows, closes, volumes,
                                                coeff=COEFF, period=PERIOD,
                                                use_mfi=USE_MFI)
    adx_arr, pdi_arr, mdi_arr = _compute_adx(highs, lows, closes, ADX_LENGTH)
    rsi_arr = _compute_rsi(closes, PERIOD)
    mfi_arr = _compute_mfi(highs, lows, closes, volumes, PERIOD) if USE_MFI else None
    adx_sma = _sma(adx_arr, ADX_LENGTH)

    signals = []
    prev_direction = 0  # state machine

    # Son LOOKBACK_BARS bar'ı tara (her biri bir "scan cycle" gibi)
    start_idx = n - LOOKBACK_BARS
    if start_idx < 4:
        start_idx = 4

    for i in range(start_idx, n):
        at_now = alpha_trend[i]
        at_1   = alpha_trend[i-1]
        at_2   = alpha_trend[i-2]
        at_3   = alpha_trend[i-3]

        if any(np.isnan(v) for v in [at_now, at_1, at_2, at_3]):
            continue

        adx_val = adx_arr[i] if not np.isnan(adx_arr[i]) else 0
        rsi_val = rsi_arr[i] if not np.isnan(rsi_arr[i]) else 50
        mfi_val = mfi_arr[i] if (mfi_arr is not None and not np.isnan(mfi_arr[i])) else 50
        atr_val = atr_arr[i] if not np.isnan(atr_arr[i]) else 0

        # Filters
        adx_static_ok = adx_val > ADX_THRESHOLD if USE_ADX_STATIC else True
        adx_dyn_thresh = adx_sma[i] * ADX_DYN_MULT if not np.isnan(adx_sma[i]) else 0
        adx_dynamic_ok = adx_val > adx_dyn_thresh if USE_ADX_DYNAMIC else True
        slope = abs(at_now - at_1)
        min_slope = atr_val * SLOPE_FACTOR if atr_val > 0 else 0
        slope_ok = slope > min_slope if USE_SLOPE else True
        final_filter = adx_static_ok and adx_dynamic_ok and slope_ok

        # Crossover
        buy_cross  = (at_now > at_2) and (at_1 <= at_3)
        sell_cross = (at_now < at_2) and (at_1 >= at_3)

        buy_filtered  = buy_cross and final_filter
        sell_filtered = sell_cross and final_filter

        # State machine
        new_direction = prev_direction
        if buy_filtered:
            new_direction = 1
        elif sell_filtered:
            new_direction = -1

        if prev_direction == 0:
            plot_buy  = buy_filtered
            plot_sell = sell_filtered and not buy_filtered
        else:
            plot_buy  = buy_filtered and prev_direction != 1
            plot_sell = sell_filtered and prev_direction != -1

        prev_direction = new_direction

        # Trend color
        if at_now > at_2:
            trend_color = "GREEN"
        elif at_now < at_2:
            trend_color = "RED"
        elif at_1 > at_3:
            trend_color = "GREEN"
        else:
            trend_color = "RED"

        if plot_buy or plot_sell:
            ts_ms = timestamps[i]
            dt_str = datetime.fromtimestamp(ts_ms / 1000).strftime("%Y-%m-%d %H:%M")
            signal = "BUY" if plot_buy else "SELL"
            price = closes[i]

            # Filter detayları
            filter_info = []
            if not adx_static_ok:
                filter_info.append(f"ADX({adx_val:.1f})<{ADX_THRESHOLD}")
            if not adx_dynamic_ok:
                filter_info.append(f"ADX_dyn({adx_val:.1f})<{adx_dyn_thresh:.1f}")

            signals.append({
                "time": dt_str,
                "ts": ts_ms,
                "symbol": symbol,
                "signal": signal,
                "price": price,
                "adx": adx_val,
                "rsi": rsi_val,
                "mfi": mfi_val,
                "atr": atr_val,
                "alpha_trend": at_now,
                "alpha_trend_2": at_2,
                "trend": trend_color,
                "filters_ok": final_filter,
                "filter_detail": ", ".join(filter_info) if filter_info else "ALL_OK",
            })

    return signals


# ═══════════════════════════════════════════════════════════════════
#  Main
# ═══════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    print("=" * 90)
    print(f"  SYSTEM M BACKTEST — Son {LOOKBACK_HOURS} saat ({TF})")
    print(f"  Zaman: {datetime.now().strftime('%Y-%m-%d %H:%M')} | "
          f"Başlangıç: ~{(datetime.now() - timedelta(hours=LOOKBACK_HOURS)).strftime('%Y-%m-%d %H:%M')}")
    print(f"  Config: coeff={COEFF}, period={PERIOD}, ADX_thr={ADX_THRESHOLD}, "
          f"MFI={'ON' if USE_MFI else 'OFF'}")
    print("=" * 90)

    # 1. Top 50 coin
    print("\n[1] Top 50 coin çekiliyor...")
    symbols = get_top_symbols(50)
    print(f"    {len(symbols)} coin bulundu")

    # 2. Her coin için tarama
    all_signals = []
    errors = 0
    print(f"\n[2] {len(symbols)} coin taranıyor ({TOTAL_KLINES} bar)...\n")

    for idx, sym in enumerate(symbols, 1):
        try:
            klines = get_klines(sym, TF, limit=TOTAL_KLINES)
            sigs = analyze_bars(sym, klines)
            if sigs:
                all_signals.extend(sigs)
                for s in sigs:
                    icon = "+" if s["signal"] == "BUY" else "-"
                    print(f"    [{icon}] {s['time']} | {s['symbol']:>12s} | "
                          f"{s['signal']:>4s} @ {s['price']:<12.6f} | "
                          f"Trend={s['trend']:>5s} | ADX={s['adx']:5.1f} | "
                          f"RSI={s['rsi']:5.1f} | MFI={s['mfi']:5.1f}")
            if idx % 10 == 0:
                print(f"    ... {idx}/{len(symbols)} tarandı ({len(all_signals)} sinyal)")
        except Exception as e:
            errors += 1
            if errors <= 3:
                print(f"    WARN{sym}: {e}")
        time.sleep(0.1)  # rate limit

    # 3. Sonuç özeti
    print("\n" + "=" * 90)
    print("  SONUÇ ÖZETİ")
    print("=" * 90)

    if not all_signals:
        print("\n  XXX SON 15 SAATTE HİÇ SİNYAL ÜRETİLMEDİ!")
        print("  -> Bu, sistemin pozisyon açamamasının sebebi olabilir.")
        print("  -> Olası nedenler:")
        print("    1. ADX filtresi çok sıkı (threshold=18, tüm coinler ranging)")
        print("    2. Crossover oluşmamış (piyasa yatay)")
        print("    3. Bir bug var")
    else:
        # Sinyalleri zamana göre sırala
        all_signals.sort(key=lambda s: s["ts"])

        buy_count = sum(1 for s in all_signals if s["signal"] == "BUY")
        sell_count = sum(1 for s in all_signals if s["signal"] == "SELL")
        unique_coins = len(set(s["symbol"] for s in all_signals))

        print(f"\n  Toplam sinyal: {len(all_signals)} ({buy_count} BUY, {sell_count} SELL)")
        print(f"  Sinyal üreten coin: {unique_coins} / {len(symbols)}")
        print(f"  Hata: {errors} coin")

        # Coin bazlı özet
        print(f"\n  {'Coin':<14s} {'BUY':>4s} {'SELL':>4s} {'Toplam':>6s} | İlk Sinyal         | Son Sinyal")
        print("  " + "-" * 80)
        coin_stats = {}
        for s in all_signals:
            sym = s["symbol"]
            if sym not in coin_stats:
                coin_stats[sym] = {"BUY": 0, "SELL": 0, "first": s["time"], "last": s["time"]}
            coin_stats[sym][s["signal"]] += 1
            coin_stats[sym]["last"] = s["time"]

        for sym in sorted(coin_stats, key=lambda x: coin_stats[x]["BUY"] + coin_stats[x]["SELL"], reverse=True):
            cs = coin_stats[sym]
            total = cs["BUY"] + cs["SELL"]
            print(f"  {sym:<14s} {cs['BUY']:>4d} {cs['SELL']:>4d} {total:>6d} | {cs['first']} | {cs['last']}")

        # Zaman dağılımı (3 saatlik bloklar)
        print(f"\n  Zaman Dağılımı (3 saatlik bloklar):")
        now = datetime.now()
        for h_start in range(LOOKBACK_HOURS, 0, -3):
            h_end = max(h_start - 3, 0)
            t_start = now - timedelta(hours=h_start)
            t_end = now - timedelta(hours=h_end)
            ts_start = t_start.timestamp() * 1000
            ts_end = t_end.timestamp() * 1000
            block_sigs = [s for s in all_signals if ts_start <= s["ts"] < ts_end]
            b_buy = sum(1 for s in block_sigs if s["signal"] == "BUY")
            b_sell = sum(1 for s in block_sigs if s["signal"] == "SELL")
            bar = "#" * len(block_sigs)
            print(f"    {t_start.strftime('%H:%M')}-{t_end.strftime('%H:%M')}: "
                  f"{len(block_sigs):>3d} ({b_buy}B/{b_sell}S) {bar}")

        # Simülasyon: sistem bu sinyallerle ne yapardı?
        print(f"\n  TİCARET SİMÜLASYONU (Short+Reverse modu):")
        print("  " + "-" * 80)
        positions = {}  # symbol -> {"side": "LONG"/"SHORT", "entry": price, "time": str}
        trades = []
        for s in all_signals:
            sym = s["symbol"]
            sig = s["signal"]
            pos = positions.get(sym)

            if sig == "BUY":
                if pos is None:
                    # Yeni LONG aç
                    positions[sym] = {"side": "LONG", "entry": s["price"], "time": s["time"]}
                    print(f"    {s['time']} | {sym:>12s} | LONG_AÇ   @ {s['price']:<12.6f}")
                elif pos["side"] == "SHORT":
                    # Reverse SHORT->LONG
                    pnl = (pos["entry"] - s["price"]) / pos["entry"] * 100
                    trades.append({"sym": sym, "side": "SHORT", "pnl": pnl,
                                   "entry": pos["entry"], "exit": s["price"]})
                    print(f"    {s['time']} | {sym:>12s} | REVERSE->LONG @ {s['price']:<12.6f} "
                          f"(SHORT PnL: {pnl:+.2f}%)")
                    positions[sym] = {"side": "LONG", "entry": s["price"], "time": s["time"]}
                # LONG zaten varsa: skip (aynı yön)

            elif sig == "SELL":
                if pos is None:
                    # Yeni SHORT aç
                    positions[sym] = {"side": "SHORT", "entry": s["price"], "time": s["time"]}
                    print(f"    {s['time']} | {sym:>12s} | SHORT_AÇ  @ {s['price']:<12.6f}")
                elif pos["side"] == "LONG":
                    # Reverse LONG->SHORT
                    pnl = (s["price"] - pos["entry"]) / pos["entry"] * 100
                    trades.append({"sym": sym, "side": "LONG", "pnl": pnl,
                                   "entry": pos["entry"], "exit": s["price"]})
                    print(f"    {s['time']} | {sym:>12s} | REVERSE->SHORT @ {s['price']:<12.6f} "
                          f"(LONG PnL: {pnl:+.2f}%)")
                    positions[sym] = {"side": "SHORT", "entry": s["price"], "time": s["time"]}

        # Hala açık pozisyonlar
        if positions:
            print(f"\n  Hala Açık Pozisyonlar ({len(positions)}):")
            for sym, pos in sorted(positions.items()):
                print(f"    {sym:>12s} | {pos['side']:>5s} | entry={pos['entry']:.6f} | açılış={pos['time']}")

        # Trade PnL özeti
        if trades:
            total_pnl = sum(t["pnl"] for t in trades)
            win = sum(1 for t in trades if t["pnl"] > 0)
            lose = sum(1 for t in trades if t["pnl"] <= 0)
            print(f"\n  Kapanan Trade Özeti:")
            print(f"    Toplam: {len(trades)} trade | Win: {win} | Lose: {lose} | "
                  f"Win%: {win/len(trades)*100:.0f}%")
            print(f"    Toplam PnL (kaldıraçsız): {total_pnl:+.2f}%")
            print(f"    Ort PnL/trade: {total_pnl/len(trades):+.3f}%")

    print("\n" + "=" * 90)
    print("  Backtest tamamlandı.")
    print("=" * 90)
