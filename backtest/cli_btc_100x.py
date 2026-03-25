"""BTC 100x Analysis: Find moments where 100x leverage could double the account.

For 100x leverage:
- Liq distance = ~1% (0.7% with liq_carpani=0.7)
- Need ~0.02% price move for 2% ROI (double with 100x)
- Actually: 2x ROI = entry amount doubled = 100% ROI
- 100% ROI at 100x = 1% price move in our direction
- But realistic: fee ~0.08% round trip at 100x = 8% ROI cost
- So need: 1% + 0.08% = 1.08% move, net 100% - 8% = 92% ROI

Analysis: scan every 5m candle, check indicators, simulate 100x entry,
track if price moves 1%+ in direction before hitting SL (0.5% against).
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np
from datetime import datetime, timedelta, timezone
from backtest.indicators import (
    ema_val, ema_series, macd_line_series, rsi_val, atr_val, adx_val,
)
from backtest.data_fetcher import fetch_klines

# ═══ Config ═══
SYMBOL = "BTCUSDT"
DAYS_BACK = 30
LEVERAGE = 100
TARGET_PRICE_PCT = 1.0      # %1 fiyat hareketi = %100 ROI at 100x
SL_PRICE_PCT = 0.5          # %0.5 ters hareket = %50 kayip (liq yakin)
FEE_RATE = 0.0004           # taker fee
FEE_ROI_IMPACT = FEE_RATE * 200 * LEVERAGE  # round trip ROI %

# Indicator params (System F ile ayni)
EMA_FAST, EMA_SLOW = 9, 21
EMA_GAP_MIN = 0.05
MACD_FAST, MACD_SLOW, MACD_SIG = 8, 17, 9
RSI_PERIOD = 14
RSI_LONG, RSI_SHORT = 60, 40

TFS_TO_ANALYZE = ["5m", "1h", "4h"]


def compute_indicators(closes, highs, lows, volumes):
    """Compute all indicators and return dict."""
    if len(closes) < 30:
        return None

    price = float(closes[-1])
    # EMA
    ef = ema_val(closes, EMA_FAST)
    es = ema_val(closes, EMA_SLOW)
    ema_gap = (ef - es) / price * 100 if price > 0 else 0
    ema_vote = 1 if ema_gap > EMA_GAP_MIN else (-1 if ema_gap < -EMA_GAP_MIN else 0)

    # MACD
    ml = macd_line_series(closes, MACD_FAST, MACD_SLOW)
    sl = ema_series(ml, MACD_SIG)
    hist = ml - sl
    macd_vote = 0
    macd_hist_val = 0
    macd_mom = "FLAT"
    if len(hist) >= 3:
        h1, h2, h3 = float(hist[-3]), float(hist[-2]), float(hist[-1])
        macd_hist_val = h3
        if h1 < h2 < h3: macd_mom = "UP"
        elif h1 > h2 > h3: macd_mom = "DOWN"
        if h3 > 0 and macd_mom == "UP": macd_vote = 1
        elif h3 < 0 and macd_mom == "DOWN": macd_vote = -1

    # RSI
    r = rsi_val(closes, RSI_PERIOD)
    rsi_vote = 1 if r > RSI_LONG else (-1 if r < RSI_SHORT else 0)

    # ADX
    adx = adx_val(highs, lows, closes, 14)

    # ATR
    atr = atr_val(highs, lows, closes, 14)
    atr_pct = (atr / price * 100) if price > 0 else 0

    # Volume ratio
    vol_ratio = 0
    if len(volumes) >= 21:
        vm = float(np.mean(volumes[-21:-1]))
        if vm > 0:
            vol_ratio = float(volumes[-1]) / vm

    # 3/3 direction
    if ema_vote > 0 and macd_vote > 0 and rsi_vote > 0: direction = "LONG"
    elif ema_vote < 0 and macd_vote < 0 and rsi_vote < 0: direction = "SHORT"
    else: direction = "FLAT"

    return {
        "price": price,
        "ema_gap": round(ema_gap, 4), "ema_vote": ema_vote,
        "macd_hist": round(macd_hist_val, 2), "macd_mom": macd_mom, "macd_vote": macd_vote,
        "rsi": round(r, 1), "rsi_vote": rsi_vote,
        "adx": round(adx, 1), "atr_pct": round(atr_pct, 3),
        "vol_ratio": round(vol_ratio, 2),
        "direction": direction,
    }


def simulate_trade(direction, entry_price, forward_5m_klines):
    """Simulate 100x trade. Returns (result, exit_price, bars, max_favorable, max_adverse)."""
    if not forward_5m_klines:
        return "NO_DATA", entry_price, 0, 0, 0

    peak_favorable = 0  # max % in our direction
    peak_adverse = 0    # max % against us

    for i, k in enumerate(forward_5m_klines):
        high = float(k[2])
        low = float(k[3])

        if direction == "LONG":
            fav = (high - entry_price) / entry_price * 100
            adv = (entry_price - low) / entry_price * 100
        else:
            fav = (entry_price - low) / entry_price * 100
            adv = (high - entry_price) / entry_price * 100

        peak_favorable = max(peak_favorable, fav)
        peak_adverse = max(peak_adverse, adv)

        # Check SL first (within same candle, SL has priority)
        if adv >= SL_PRICE_PCT:
            roi = -SL_PRICE_PCT * LEVERAGE - FEE_ROI_IMPACT
            return "SL", entry_price, i + 1, peak_favorable, peak_adverse

        # Check TP
        if fav >= TARGET_PRICE_PCT:
            roi = TARGET_PRICE_PCT * LEVERAGE - FEE_ROI_IMPACT
            return "TP", entry_price, i + 1, peak_favorable, peak_adverse

        # Time limit: 2 hours (24 bars)
        if i + 1 >= 24:
            close = float(k[4])
            if direction == "LONG":
                final_pct = (close - entry_price) / entry_price * 100
            else:
                final_pct = (entry_price - close) / entry_price * 100
            return "TIME", entry_price, i + 1, peak_favorable, peak_adverse

    return "DATA_END", entry_price, len(forward_5m_klines), peak_favorable, peak_adverse


def run():
    now = datetime.now(timezone.utc)
    end_ms = int(now.timestamp() * 1000)
    start_ms = int((now - timedelta(days=DAYS_BACK)).timestamp() * 1000)
    warmup_ms = 200 * 240 * 60000  # 200 candles of 4h for warmup

    print(f"{'='*90}")
    print(f"  BTC 100x ANALIZ — Son {DAYS_BACK} gun")
    print(f"  Hedef: %{TARGET_PRICE_PCT} fiyat hareketi = %{TARGET_PRICE_PCT*LEVERAGE} ROI")
    print(f"  SL: %{SL_PRICE_PCT} = %{SL_PRICE_PCT*LEVERAGE} kayip")
    print(f"  Fee etkisi: %{FEE_ROI_IMPACT:.1f} ROI")
    print(f"{'='*90}\n")

    # Fetch data
    print("Veri cekiliyor...")
    all_klines = {}
    for tf in TFS_TO_ANALYZE + ["5m"]:
        if tf in all_klines:
            continue
        kl = fetch_klines(SYMBOL, tf, start_ms - warmup_ms, end_ms)
        all_klines[tf] = kl
        print(f"  {tf}: {len(kl)} candle")

    kl_5m = all_klines["5m"]
    if not kl_5m:
        print("HATA: 5m veri yok!")
        return

    # Build timestamp index for each TF
    tf_by_ts = {}
    for tf in TFS_TO_ANALYZE:
        kl = all_klines[tf]
        tf_by_ts[tf] = {int(k[0]): k for k in kl}

    print(f"\nAnaliz basliyor ({len(kl_5m)} 5m mum)...\n")

    # Scan every 5m candle
    opportunities = []
    LOOKBACK = 200

    for ci in range(LOOKBACK, len(kl_5m)):
        candle = kl_5m[ci]
        candle_ts = int(candle[0])

        # Skip if before start
        if candle_ts < start_ms:
            continue

        # Compute indicators for each TF at this moment
        tf_indicators = {}
        all_agree = True
        agreed_direction = None

        for tf in TFS_TO_ANALYZE:
            # Find candles up to this timestamp for this TF
            kl_tf = all_klines[tf]
            # Get candles before candle_ts
            tf_candles = [k for k in kl_tf if int(k[0]) <= candle_ts]
            if len(tf_candles) < LOOKBACK:
                tf_candles = tf_candles[-max(50, len(tf_candles)):]

            if len(tf_candles) < 30:
                all_agree = False
                break

            closes = np.array([float(k[4]) for k in tf_candles[-LOOKBACK:]])
            highs = np.array([float(k[2]) for k in tf_candles[-LOOKBACK:]])
            lows = np.array([float(k[3]) for k in tf_candles[-LOOKBACK:]])
            volumes = np.array([float(k[5]) for k in tf_candles[-LOOKBACK:]])

            ind = compute_indicators(closes, highs, lows, volumes)
            if ind is None:
                all_agree = False
                break

            tf_indicators[tf] = ind

            if ind["direction"] == "FLAT":
                all_agree = False

            if agreed_direction is None and ind["direction"] != "FLAT":
                agreed_direction = ind["direction"]
            elif ind["direction"] != "FLAT" and ind["direction"] != agreed_direction:
                all_agree = False

        if not all_agree or agreed_direction is None:
            continue

        # 3/3 TF uyumu bulundu! Simulate trade
        entry_price = float(candle[4])  # close price
        forward = kl_5m[ci + 1: ci + 1 + 30]  # next 30 candles (2.5h)

        result, _, bars, max_fav, max_adv = simulate_trade(
            agreed_direction, entry_price, forward)

        hold_min = bars * 5
        roi_net = 0
        if result == "TP":
            roi_net = TARGET_PRICE_PCT * LEVERAGE - FEE_ROI_IMPACT
        elif result == "SL":
            roi_net = -SL_PRICE_PCT * LEVERAGE - FEE_ROI_IMPACT
        elif result == "TIME":
            close_price = float(forward[min(bars - 1, len(forward) - 1)][4]) if forward else entry_price
            if agreed_direction == "LONG":
                price_pct = (close_price - entry_price) / entry_price * 100
            else:
                price_pct = (entry_price - close_price) / entry_price * 100
            roi_net = price_pct * LEVERAGE - FEE_ROI_IMPACT

        dt = datetime.fromtimestamp(candle_ts / 1000, tz=timezone.utc)

        opportunities.append({
            "time": candle_ts, "dt": dt, "price": entry_price,
            "direction": agreed_direction, "result": result,
            "bars": bars, "hold_min": hold_min,
            "roi_net": round(roi_net, 1),
            "max_favorable_pct": round(max_fav, 3),
            "max_adverse_pct": round(max_adv, 3),
            "tf_indicators": tf_indicators,
        })

    # ═══ Report ═══
    print(f"{'='*90}")
    print(f"  3/3 TF Uyum Anlari: {len(opportunities)}")
    print(f"{'='*90}\n")

    if not opportunities:
        print("  Hicbir anda 3 TF ayni yonde 3/3 uyum gostermemis.")
        return

    # Summary
    tp_count = sum(1 for o in opportunities if o["result"] == "TP")
    sl_count = sum(1 for o in opportunities if o["result"] == "SL")
    time_count = sum(1 for o in opportunities if o["result"] == "TIME")
    total_roi = sum(o["roi_net"] for o in opportunities)

    print(f"  TP (%{TARGET_PRICE_PCT} hedefe ulasti): {tp_count}")
    print(f"  SL (%{SL_PRICE_PCT} ters gitti):       {sl_count}")
    print(f"  TIME (2 saat doldu):     {time_count}")
    print(f"  Toplam ROI (hepsine girsek): {total_roi:+.1f}%")
    print(f"  WR: {tp_count}/{len(opportunities)} = {tp_count/len(opportunities)*100:.0f}%")

    # Detailed table
    print(f"\n  {'Tarih':>16} {'Yon':>5} {'Fiyat':>10} {'Sonuc':>6} {'Sure':>6} "
          f"{'ROI%':>7} {'MaxFav':>7} {'MaxAdv':>7} | "
          f"{'5m':>12} {'1h':>12} {'4h':>12}")
    print(f"  {'-'*120}")

    for o in opportunities:
        dt = o["dt"]
        tf_info = ""
        for tf in TFS_TO_ANALYZE:
            ind = o["tf_indicators"].get(tf, {})
            e = "E+" if ind.get("ema_vote", 0) > 0 else ("E-" if ind.get("ema_vote", 0) < 0 else "E.")
            m = "M+" if ind.get("macd_vote", 0) > 0 else ("M-" if ind.get("macd_vote", 0) < 0 else "M.")
            r = "R+" if ind.get("rsi_vote", 0) > 0 else ("R-" if ind.get("rsi_vote", 0) < 0 else "R.")
            tf_info += f" {e}{m}{r}"

        w = "+" if o["roi_net"] > 0 else ""
        result_color = o["result"]
        print(f"  {dt:%Y-%m-%d %H:%M} {o['direction']:>5} {o['price']:>10.1f} "
              f"{result_color:>6} {o['hold_min']:>4}dk "
              f"{w}{o['roi_net']:>6.1f}% {o['max_favorable_pct']:>6.3f}% {o['max_adverse_pct']:>6.3f}% |"
              f"{tf_info}")

    # Indicator analysis for TP vs SL trades
    print(f"\n{'='*90}")
    print(f"  INDIKATOR ANALIZI: TP vs SL")
    print(f"{'='*90}")

    for label, filter_fn in [("TP (basarili)", lambda o: o["result"] == "TP"),
                              ("SL (basarisiz)", lambda o: o["result"] == "SL")]:
        subset = [o for o in opportunities if filter_fn(o)]
        if not subset:
            print(f"\n  {label}: 0 trade")
            continue

        print(f"\n  {label}: {len(subset)} trade")
        for tf in TFS_TO_ANALYZE:
            adx_vals = [o["tf_indicators"][tf]["adx"] for o in subset if tf in o["tf_indicators"]]
            rsi_vals = [o["tf_indicators"][tf]["rsi"] for o in subset if tf in o["tf_indicators"]]
            atr_vals = [o["tf_indicators"][tf]["atr_pct"] for o in subset if tf in o["tf_indicators"]]
            vol_vals = [o["tf_indicators"][tf]["vol_ratio"] for o in subset if tf in o["tf_indicators"]]
            ema_gaps = [o["tf_indicators"][tf]["ema_gap"] for o in subset if tf in o["tf_indicators"]]

            if adx_vals:
                print(f"    {tf:>4}: ADX={np.mean(adx_vals):.1f} "
                      f"RSI={np.mean(rsi_vals):.1f} "
                      f"ATR%={np.mean(atr_vals):.3f} "
                      f"Vol={np.mean(vol_vals):.2f}x "
                      f"EMA_gap={np.mean(ema_gaps):.4f}%")

    # Max favorable analysis
    print(f"\n{'='*90}")
    print(f"  MAX FAVORABLE HAREKET (fiyat en cok ne kadar gitti?)")
    print(f"{'='*90}")
    fav_pcts = [o["max_favorable_pct"] for o in opportunities]
    adv_pcts = [o["max_adverse_pct"] for o in opportunities]
    print(f"  Favorable: min={min(fav_pcts):.3f}% max={max(fav_pcts):.3f}% "
          f"avg={np.mean(fav_pcts):.3f}% median={np.median(fav_pcts):.3f}%")
    print(f"  Adverse:   min={min(adv_pcts):.3f}% max={max(adv_pcts):.3f}% "
          f"avg={np.mean(adv_pcts):.3f}% median={np.median(adv_pcts):.3f}%")

    # What if we used different TP/SL?
    print(f"\n{'='*90}")
    print(f"  FARKLI TP/SL SENARYOLARI")
    print(f"{'='*90}")
    print(f"  {'TP%':>5} {'SL%':>5} {'W':>3} {'L':>3} {'WR':>5} {'TopROI':>8} {'AvgROI':>8}")
    print(f"  {'-'*45}")

    for tp_pct in [0.3, 0.5, 0.7, 1.0, 1.5, 2.0]:
        for sl_pct in [0.3, 0.5, 0.7]:
            wins = sum(1 for o in opportunities if o["max_favorable_pct"] >= tp_pct
                       and (o["max_adverse_pct"] < sl_pct
                            or o["max_favorable_pct"] >= tp_pct))
            # More accurate: check if TP hit before SL in candle sequence
            # For now approximate with max values
            w_count = 0
            l_count = 0
            total_r = 0
            for o in opportunities:
                if o["max_favorable_pct"] >= tp_pct and o["max_adverse_pct"] < sl_pct:
                    w_count += 1
                    total_r += tp_pct * LEVERAGE - FEE_ROI_IMPACT
                elif o["max_adverse_pct"] >= sl_pct:
                    l_count += 1
                    total_r += -sl_pct * LEVERAGE - FEE_ROI_IMPACT
                # else: neither hit (timeout)

            total = w_count + l_count
            if total > 0:
                wr = w_count / total * 100
                avg_r = total_r / total
            else:
                wr = 0
                avg_r = 0
            print(f"  {tp_pct:>4.1f}% {sl_pct:>4.1f}% {w_count:>3} {l_count:>3} "
                  f"{wr:>4.0f}% {total_r:>+7.1f}% {avg_r:>+7.1f}%")

    print()


if __name__ == "__main__":
    run()
