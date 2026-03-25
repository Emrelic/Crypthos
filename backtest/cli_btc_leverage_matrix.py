"""BTC Leverage x ROI Matrix: Find optimal leverage/target combination.

Tests all combinations and finds the best risk-adjusted strategy.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np
from datetime import datetime, timedelta, timezone
from backtest.indicators import ema_val, ema_series, macd_line_series, rsi_val, atr_val, adx_val
from backtest.data_fetcher import fetch_klines

SYMBOL = "BTCUSDT"
DAYS_BACK = 30
FEE_RATE = 0.0004

# Indicator params
EMA_FAST, EMA_SLOW = 9, 21
EMA_GAP_MIN = 0.05
MACD_FAST, MACD_SLOW, MACD_SIG = 8, 17, 9
RSI_PERIOD = 14
RSI_LONG, RSI_SHORT = 60, 40
TFS = ["5m", "1h", "4h"]

# Scenarios to test
SCENARIOS = [
    {"name": "a) 100x / %100 ROI", "lev": 100, "target_roi": 100},
    {"name": "b) 100x / %50 ROI",  "lev": 100, "target_roi": 50},
    {"name": "c) 50x / %100 ROI",  "lev": 50,  "target_roi": 100},
    {"name": "d) 50x / %50 ROI",   "lev": 50,  "target_roi": 50},
    {"name": "e) 20x / %100 ROI",  "lev": 20,  "target_roi": 100},
    {"name": "f) 20x / %50 ROI",   "lev": 20,  "target_roi": 50},
]

def compute_indicators(closes, highs, lows, volumes):
    if len(closes) < 30:
        return None
    price = float(closes[-1])
    if price <= 0:
        return None
    ef = ema_val(closes, EMA_FAST)
    es = ema_val(closes, EMA_SLOW)
    ema_gap = (ef - es) / price * 100
    ema_vote = 1 if ema_gap > EMA_GAP_MIN else (-1 if ema_gap < -EMA_GAP_MIN else 0)

    ml = macd_line_series(closes, MACD_FAST, MACD_SLOW)
    sl = ema_series(ml, MACD_SIG)
    hist = ml - sl
    macd_vote = 0
    if len(hist) >= 3:
        h1, h2, h3 = float(hist[-3]), float(hist[-2]), float(hist[-1])
        if h3 > 0 and h1 < h2 < h3: macd_vote = 1
        elif h3 < 0 and h1 > h2 > h3: macd_vote = -1

    r = rsi_val(closes, RSI_PERIOD)
    rsi_vote = 1 if r > RSI_LONG else (-1 if r < RSI_SHORT else 0)

    adx = adx_val(highs, lows, closes, 14)
    vol_ratio = 0
    if len(volumes) >= 21:
        vm = float(np.mean(volumes[-21:-1]))
        if vm > 0: vol_ratio = float(volumes[-1]) / vm

    if ema_vote > 0 and macd_vote > 0 and rsi_vote > 0: direction = "LONG"
    elif ema_vote < 0 and macd_vote < 0 and rsi_vote < 0: direction = "SHORT"
    else: direction = "FLAT"

    return {"price": price, "direction": direction, "ema_gap": ema_gap,
            "adx": adx, "rsi": r, "vol_ratio": vol_ratio}


def simulate_scenario(direction, entry_price, forward_5m, leverage, target_roi):
    """Simulate a trade with given leverage and ROI target.

    TP: price move needed = target_roi / leverage
    SL: liq-based = (1/leverage) * 0.7 * 0.8  (emergency %80 of practical liq)
    """
    if not forward_5m:
        return None

    fee_roi = FEE_RATE * 200 * leverage
    tp_price_pct = target_roi / leverage        # ROI -> price %
    sl_price_pct = (1.0 / leverage) * 0.7 * 0.5  # %50 of practical liq distance

    peak_fav = 0
    peak_adv = 0
    MAX_BARS = 12 * 24  # 24 saat max

    for i, k in enumerate(forward_5m[:MAX_BARS]):
        high = float(k[2])
        low = float(k[3])
        close = float(k[4])

        if direction == "LONG":
            fav_pct = (high - entry_price) / entry_price * 100
            adv_pct = (entry_price - low) / entry_price * 100
        else:
            fav_pct = (entry_price - low) / entry_price * 100
            adv_pct = (high - entry_price) / entry_price * 100

        peak_fav = max(peak_fav, fav_pct)
        peak_adv = max(peak_adv, adv_pct)

        # SL check first
        if adv_pct >= sl_price_pct:
            roi = -sl_price_pct * leverage - fee_roi
            return {"result": "SL", "bars": i+1, "roi": roi,
                    "peak_fav": peak_fav, "peak_adv": peak_adv}

        # TP check
        if fav_pct >= tp_price_pct:
            roi = target_roi - fee_roi
            return {"result": "TP", "bars": i+1, "roi": roi,
                    "peak_fav": peak_fav, "peak_adv": peak_adv}

    # Time out
    if direction == "LONG":
        final_pct = (float(forward_5m[min(MAX_BARS-1, len(forward_5m)-1)][4]) - entry_price) / entry_price * 100
    else:
        final_pct = (entry_price - float(forward_5m[min(MAX_BARS-1, len(forward_5m)-1)][4])) / entry_price * 100
    roi = final_pct * leverage - fee_roi
    return {"result": "TIME", "bars": min(MAX_BARS, len(forward_5m)), "roi": roi,
            "peak_fav": peak_fav, "peak_adv": peak_adv}


def run():
    now = datetime.now(timezone.utc)
    end_ms = int(now.timestamp() * 1000)
    start_ms = int((now - timedelta(days=DAYS_BACK)).timestamp() * 1000)
    warmup_ms = 200 * 240 * 60000

    print(f"{'='*95}")
    print(f"  BTC KALDIRAC x ROI MATRISI — Son {DAYS_BACK} gun")
    print(f"{'='*95}\n")

    # Fetch data once
    print("Veri cekiliyor...")
    all_klines = {}
    for tf in TFS + ["5m"]:
        if tf not in all_klines:
            kl = fetch_klines(SYMBOL, tf, start_ms - warmup_ms, end_ms)
            all_klines[tf] = kl
            print(f"  {tf}: {len(kl)} candle")

    kl_5m = all_klines["5m"]
    LOOKBACK = 200

    # Find all 3/3 alignment moments
    print(f"\n3/3 TF uyum anlari araniyor...")
    signals = []

    for ci in range(LOOKBACK, len(kl_5m)):
        candle = kl_5m[ci]
        candle_ts = int(candle[0])
        if candle_ts < start_ms:
            continue

        tf_dirs = {}
        agreed = None
        ok = True

        for tf in TFS:
            kl_tf = all_klines[tf]
            tf_candles = [k for k in kl_tf if int(k[0]) <= candle_ts]
            if len(tf_candles) < 30:
                ok = False; break
            window = tf_candles[-LOOKBACK:]
            c = np.array([float(k[4]) for k in window])
            h = np.array([float(k[2]) for k in window])
            l = np.array([float(k[3]) for k in window])
            v = np.array([float(k[5]) for k in window])

            ind = compute_indicators(c, h, l, v)
            if ind is None or ind["direction"] == "FLAT":
                ok = False; break

            tf_dirs[tf] = ind
            if agreed is None:
                agreed = ind["direction"]
            elif ind["direction"] != agreed:
                ok = False; break

        if not ok or agreed is None:
            continue

        entry_price = float(candle[4])
        forward = kl_5m[ci+1:]
        dt = datetime.fromtimestamp(candle_ts/1000, tz=timezone.utc)

        signals.append({
            "ts": candle_ts, "dt": dt, "price": entry_price,
            "direction": agreed, "forward": forward,
            "indicators": tf_dirs,
        })

    print(f"  {len(signals)} uyum ani bulundu\n")

    # Run each scenario
    scenario_results = []

    for sc in SCENARIOS:
        lev = sc["lev"]
        target = sc["target_roi"]
        tp_pct = target / lev
        sl_pct = (1.0 / lev) * 0.7 * 0.5
        fee = FEE_RATE * 200 * lev

        trades = []
        for sig in signals:
            r = simulate_scenario(sig["direction"], sig["price"],
                                  sig["forward"], lev, target)
            if r:
                r["dt"] = sig["dt"]
                r["direction"] = sig["direction"]
                r["price"] = sig["price"]
                r["indicators"] = sig["indicators"]
                trades.append(r)

        tp_trades = [t for t in trades if t["result"] == "TP"]
        sl_trades = [t for t in trades if t["result"] == "SL"]
        time_trades = [t for t in trades if t["result"] == "TIME"]
        total_roi = sum(t["roi"] for t in trades)
        wins = len(tp_trades)
        losses = len(sl_trades)
        wr = wins / len(trades) * 100 if trades else 0

        # Avg hold time for TP trades
        avg_tp_bars = np.mean([t["bars"] for t in tp_trades]) if tp_trades else 0
        avg_tp_min = avg_tp_bars * 5

        # Max drawdown
        dd = 0; max_dd = 0
        for t in trades:
            if t["roi"] < 0: dd += t["roi"]; max_dd = min(max_dd, dd)
            else: dd = 0

        scenario_results.append({
            "name": sc["name"], "lev": lev, "target": target,
            "tp_pct": tp_pct, "sl_pct": sl_pct, "fee": fee,
            "trades": len(trades), "wins": wins, "losses": losses,
            "timeouts": len(time_trades), "wr": wr,
            "total_roi": total_roi, "avg_roi": total_roi/len(trades) if trades else 0,
            "max_dd": max_dd, "avg_tp_min": avg_tp_min,
            "tp_trades": tp_trades, "sl_trades": sl_trades,
            "all_trades": trades,
        })

    # ═══ Summary Table ═══
    print(f"{'='*95}")
    print(f"  {'Senaryo':<22} {'Lev':>4} {'TP%':>5} {'SL%':>5} {'Fee%':>5} "
          f"{'Trade':>5} {'W':>3} {'L':>3} {'T':>3} {'WR':>5} "
          f"{'TopROI':>8} {'AvgROI':>8} {'MaxDD':>7} {'TPsure':>7}")
    print(f"{'='*95}")

    for sr in scenario_results:
        w = "+" if sr["total_roi"] > 0 else ""
        tp_sure = f"{sr['avg_tp_min']:.0f}dk" if sr['avg_tp_min'] > 0 else "-"
        print(f"  {sr['name']:<22} {sr['lev']:>4}x {sr['tp_pct']:>4.2f}% {sr['sl_pct']:>4.2f}% "
              f"{sr['fee']:>4.1f}% "
              f"{sr['trades']:>5} {sr['wins']:>3} {sr['losses']:>3} {sr['timeouts']:>3} "
              f"{sr['wr']:>4.0f}% "
              f"{w}{sr['total_roi']:>7.1f}% {sr['avg_roi']:>+7.1f}% "
              f"{sr['max_dd']:>6.0f}% {tp_sure:>7}")

    # ═══ Detailed per-scenario ═══
    for sr in scenario_results:
        trades = sr["all_trades"]
        if not trades:
            continue

        print(f"\n{'─'*95}")
        print(f"  {sr['name']}")
        print(f"  Fiyat hareketi: TP={sr['tp_pct']:.2f}%, SL={sr['sl_pct']:.2f}%")
        print(f"{'─'*95}")
        print(f"  {'Tarih':>16} {'Yon':>5} {'Fiyat':>10} {'Sonuc':>5} {'Sure':>6} "
              f"{'ROI%':>8} {'MaxFav':>7} {'MaxAdv':>7}")
        print(f"  {'-'*75}")

        for t in trades[:30]:  # first 30
            hold_min = t["bars"] * 5
            hold_str = f"{hold_min}dk" if hold_min < 60 else f"{hold_min//60}s{hold_min%60}dk"
            w = "+" if t["roi"] > 0 else ""
            print(f"  {t['dt']:%Y-%m-%d %H:%M} {t['direction']:>5} {t['price']:>10.1f} "
                  f"{t['result']:>5} {hold_str:>6} "
                  f"{w}{t['roi']:>7.1f}% {t['peak_fav']:>6.3f}% {t['peak_adv']:>6.3f}%")

        if len(trades) > 30:
            print(f"  ... ve {len(trades)-30} trade daha")

    # ═══ Final Recommendation ═══
    print(f"\n{'='*95}")
    print(f"  FINAL ONERI")
    print(f"{'='*95}")

    # Best by total ROI
    best_roi = max(scenario_results, key=lambda x: x["total_roi"])
    # Best by WR
    best_wr = max(scenario_results, key=lambda x: x["wr"])
    # Best risk-adjusted (ROI / -MaxDD)
    best_adj = max(scenario_results,
                   key=lambda x: x["total_roi"] / abs(x["max_dd"]) if x["max_dd"] < 0 else x["total_roi"])

    print(f"  En yuksek toplam ROI:  {best_roi['name']} -> {best_roi['total_roi']:+.1f}%")
    print(f"  En yuksek WR:          {best_wr['name']} -> {best_wr['wr']:.0f}%")
    print(f"  En iyi risk/getiri:    {best_adj['name']} -> "
          f"ROI:{best_adj['total_roi']:+.1f}%, MaxDD:{best_adj['max_dd']:.0f}%")
    print()


if __name__ == "__main__":
    run()
