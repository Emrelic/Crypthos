"""BTC Realistic Analysis: One position at a time, no duplicate entries.

When a signal fires, open position. Don't open again until position closes.
Test across multiple TP/SL combos and leverage levels.
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

EMA_FAST, EMA_SLOW = 9, 21
EMA_GAP_MIN = 0.05
MACD_FAST, MACD_SLOW, MACD_SIG = 8, 17, 9
RSI_PERIOD = 14
RSI_LONG, RSI_SHORT = 60, 40
TFS = ["5m", "1h", "4h"]
LOOKBACK = 200


def compute_direction(closes):
    if len(closes) < 30:
        return "FLAT"
    price = float(closes[-1])
    if price <= 0:
        return "FLAT"

    ef = ema_val(closes, EMA_FAST)
    es = ema_val(closes, EMA_SLOW)
    gap = (ef - es) / price * 100
    ev = 1 if gap > EMA_GAP_MIN else (-1 if gap < -EMA_GAP_MIN else 0)

    ml = macd_line_series(closes, MACD_FAST, MACD_SLOW)
    sl = ema_series(ml, MACD_SIG)
    hist = ml - sl
    mv = 0
    if len(hist) >= 3:
        h1, h2, h3 = float(hist[-3]), float(hist[-2]), float(hist[-1])
        if h3 > 0 and h1 < h2 < h3: mv = 1
        elif h3 < 0 and h1 > h2 > h3: mv = -1

    r = rsi_val(closes, RSI_PERIOD)
    rv = 1 if r > RSI_LONG else (-1 if r < RSI_SHORT else 0)

    if ev > 0 and mv > 0 and rv > 0: return "LONG"
    elif ev < 0 and mv < 0 and rv < 0: return "SHORT"
    return "FLAT"


def simulate_position(direction, entry_price, forward_5m,
                      tp_price_pct, sl_price_pct, leverage, max_bars=288):
    """Simulate one position. Returns (result, exit_price, bars, roi_net, peak_fav, peak_adv)."""
    fee_roi = FEE_RATE * 200 * leverage
    peak_fav = 0
    peak_adv = 0

    for i, k in enumerate(forward_5m[:max_bars]):
        high = float(k[2])
        low = float(k[3])
        close = float(k[4])

        if direction == "LONG":
            fav = (high - entry_price) / entry_price * 100
            adv = (entry_price - low) / entry_price * 100
        else:
            fav = (entry_price - low) / entry_price * 100
            adv = (high - entry_price) / entry_price * 100

        peak_fav = max(peak_fav, fav)
        peak_adv = max(peak_adv, adv)

        # SL hit (0 means no SL — liq only)
        if sl_price_pct > 0 and adv >= sl_price_pct:
            roi = -sl_price_pct * leverage - fee_roi
            return "SL", close, i + 1, roi, peak_fav, peak_adv

        # Liquidation check (no SL mode)
        liq_pct = (1.0 / leverage) * 70  # practical liq at 70%
        if adv >= liq_pct:
            return "LIQ", close, i + 1, -100.0, peak_fav, peak_adv

        # TP hit
        if fav >= tp_price_pct:
            roi = tp_price_pct * leverage - fee_roi
            return "TP", close, i + 1, roi, peak_fav, peak_adv

    # Time out (24h)
    close = float(forward_5m[min(max_bars - 1, len(forward_5m) - 1)][4])
    if direction == "LONG":
        pnl_pct = (close - entry_price) / entry_price * 100
    else:
        pnl_pct = (entry_price - close) / entry_price * 100
    roi = pnl_pct * leverage - fee_roi
    return "TIME", close, min(max_bars, len(forward_5m)), roi, peak_fav, peak_adv


def run():
    now = datetime.now(timezone.utc)
    end_ms = int(now.timestamp() * 1000)
    start_ms = int((now - timedelta(days=DAYS_BACK)).timestamp() * 1000)
    warmup_ms = LOOKBACK * 240 * 60000

    print(f"{'='*95}")
    print(f"  BTC GERCEKCI ANALIZ — Tek pozisyon, ardisik giris yok")
    print(f"  Son {DAYS_BACK} gun, 3 TF (5m+1h+4h)")
    print(f"{'='*95}\n")

    # Fetch
    print("Veri cekiliyor...")
    all_klines = {}
    for tf in TFS:
        kl = fetch_klines(SYMBOL, tf, start_ms - warmup_ms, end_ms)
        all_klines[tf] = kl
        print(f"  {tf}: {len(kl)} candle")

    kl_5m = all_klines["5m"]

    # Find alignment moments (deduplicated: min 30 min gap)
    print(f"\n3/3 TF uyum anlari araniyor (min 30dk aralik)...")
    raw_signals = []

    for ci in range(LOOKBACK, len(kl_5m)):
        candle = kl_5m[ci]
        candle_ts = int(candle[0])
        if candle_ts < start_ms:
            continue

        agreed = None
        ok = True
        for tf in TFS:
            kl_tf = all_klines[tf]
            tf_candles = [k for k in kl_tf if int(k[0]) <= candle_ts]
            if len(tf_candles) < 30:
                ok = False; break
            closes = np.array([float(k[4]) for k in tf_candles[-LOOKBACK:]])
            d = compute_direction(closes)
            if d == "FLAT":
                ok = False; break
            if agreed is None:
                agreed = d
            elif d != agreed:
                ok = False; break

        if ok and agreed:
            raw_signals.append({
                "ci": ci, "ts": candle_ts,
                "price": float(candle[4]), "direction": agreed,
            })

    print(f"  Ham sinyal: {len(raw_signals)}")

    # Scenarios
    SCENARIOS = [
        # (name, leverage, tp_price_pct, sl_price_pct, max_bars)
        # sl_price_pct=0 means NO SL (liq only)
        ("150x SL=yok TP=0.5%",  150, 0.50, 0,    288),
        ("150x SL=yok TP=0.3%",  150, 0.30, 0,    288),
        ("125x SL=yok TP=0.5%",  125, 0.50, 0,    288),
        ("125x SL=yok TP=0.3%",  125, 0.30, 0,    288),
        ("100x SL=yok TP=0.7%",  100, 0.70, 0,    288),
        ("100x SL=yok TP=0.5%",  100, 0.50, 0,    288),
        ("100x SL=yok TP=0.3%",  100, 0.30, 0,    288),
        ("100x SL=0.7 TP=0.7%",  100, 0.70, 0.70, 288),
        ("100x SL=0.7 TP=0.5%",  100, 0.50, 0.70, 288),
        ("75x  SL=yok TP=1.0%",   75, 1.00, 0,    288),
        ("75x  SL=yok TP=0.7%",   75, 0.70, 0,    288),
        ("75x  SL=yok TP=0.5%",   75, 0.50, 0,    288),
        ("50x  SL=yok TP=1.5%",   50, 1.50, 0,    288),
        ("50x  SL=yok TP=1.0%",   50, 1.00, 0,    288),
        ("50x  SL=yok TP=0.7%",   50, 0.70, 0,    288),
        ("25x  SL=yok TP=2.0%",   25, 2.00, 0,    288),
        ("25x  SL=yok TP=1.5%",   25, 1.50, 0,    288),
        ("25x  SL=yok TP=1.0%",   25, 1.00, 0,    288),
    ]

    all_results = []

    for sc_name, lev, tp_pct, sl_pct, max_bars in SCENARIOS:
        fee_pct = FEE_RATE * 200 * lev
        liq_pct = (1.0 / lev) * 70

        # Realistic simulation: one position at a time
        trades = []
        in_position = False
        position_end_ci = 0

        for sig in raw_signals:
            ci = sig["ci"]

            # Skip if still in a position
            if in_position and ci < position_end_ci:
                continue

            in_position = False
            entry_price = sig["price"]
            direction = sig["direction"]
            forward = kl_5m[ci + 1:]

            result, exit_price, bars, roi, peak_fav, peak_adv = simulate_position(
                direction, entry_price, forward, tp_pct, sl_pct, lev, max_bars)

            hold_min = bars * 5
            dt = datetime.fromtimestamp(sig["ts"] / 1000, tz=timezone.utc)

            trades.append({
                "dt": dt, "direction": direction, "price": entry_price,
                "exit_price": exit_price, "result": result,
                "bars": bars, "hold_min": hold_min, "roi": round(roi, 1),
                "peak_fav": round(peak_fav, 3), "peak_adv": round(peak_adv, 3),
            })

            # Block this coin until position closes
            in_position = True
            position_end_ci = ci + bars + 1  # +1 for the exit candle

        # Stats
        tp_count = sum(1 for t in trades if t["result"] == "TP")
        sl_count = sum(1 for t in trades if t["result"] == "SL")
        liq_count = sum(1 for t in trades if t["result"] == "LIQ")
        time_count = sum(1 for t in trades if t["result"] == "TIME")
        total_roi = sum(t["roi"] for t in trades)
        avg_roi = total_roi / len(trades) if trades else 0
        wr = tp_count / len(trades) * 100 if trades else 0

        # Max drawdown (consecutive losses)
        dd = 0; max_dd = 0
        for t in trades:
            if t["roi"] < 0: dd += t["roi"]; max_dd = min(max_dd, dd)
            else: dd = 0

        # Avg hold time for TP
        avg_tp_min = np.mean([t["hold_min"] for t in trades if t["result"] == "TP"]) if tp_count > 0 else 0

        all_results.append({
            "name": sc_name, "lev": lev, "tp_pct": tp_pct, "sl_pct": sl_pct,
            "fee_pct": fee_pct, "liq_pct": liq_pct,
            "trades": len(trades), "tp": tp_count, "sl": sl_count,
            "liq": liq_count, "time": time_count,
            "wr": wr, "total_roi": total_roi, "avg_roi": avg_roi,
            "max_dd": max_dd, "avg_tp_min": avg_tp_min,
            "trade_list": trades,
        })

    # ═══ Summary Table ═══
    print(f"\n{'='*100}")
    print(f"  {'Senaryo':<24} {'Lev':>4} {'TP%':>5} {'SL':>5} {'Fee%':>5} {'Liq%':>5} "
          f"{'Poz':>3} {'TP':>2} {'SL':>2} {'LQ':>2} {'TM':>2} "
          f"{'WR':>5} {'TopROI':>8} {'AvgROI':>8} {'MaxDD':>7} {'TPdk':>5}")
    print(f"{'='*100}")

    for r in sorted(all_results, key=lambda x: -x["total_roi"]):
        sl_str = f"{r['sl_pct']:.1f}%" if r['sl_pct'] > 0 else "YOK"
        w = "+" if r["total_roi"] > 0 else ""
        tp_dk = f"{r['avg_tp_min']:.0f}" if r['avg_tp_min'] > 0 else "-"
        print(f"  {r['name']:<24} {r['lev']:>4}x {r['tp_pct']:>4.1f}% {sl_str:>5} "
              f"{r['fee_pct']:>4.1f}% {r['liq_pct']:>4.1f}% "
              f"{r['trades']:>3} {r['tp']:>2} {r['sl']:>2} {r['liq']:>2} {r['time']:>2} "
              f"{r['wr']:>4.0f}% {w}{r['total_roi']:>7.1f}% {r['avg_roi']:>+7.1f}% "
              f"{r['max_dd']:>6.0f}% {tp_dk:>5}")

    # ═══ Best scenario detail ═══
    best = max(all_results, key=lambda x: x["total_roi"])
    print(f"\n{'='*100}")
    print(f"  EN IYI: {best['name']}")
    print(f"  {best['trades']} pozisyon, {best['tp']} TP, {best['liq']} LIQ, "
          f"WR:{best['wr']:.0f}%, Toplam ROI: {best['total_roi']:+.1f}%")
    print(f"{'='*100}")

    print(f"\n  {'#':>2} {'Tarih':>16} {'Yon':>5} {'Giris':>10} {'Cikis':>10} "
          f"{'Sonuc':>5} {'Sure':>7} {'ROI%':>8} {'MaxFav':>7} {'MaxAdv':>7}")
    print(f"  {'-'*90}")

    for i, t in enumerate(best["trade_list"]):
        hold = f"{t['hold_min']}dk" if t['hold_min'] < 60 else f"{t['hold_min']//60}s{t['hold_min']%60}dk"
        w = "+" if t["roi"] > 0 else ""
        print(f"  {i+1:>2} {t['dt']:%Y-%m-%d %H:%M} {t['direction']:>5} "
              f"{t['price']:>10.1f} {t['exit_price']:>10.1f} "
              f"{t['result']:>5} {hold:>7} {w}{t['roi']:>7.1f}% "
              f"{t['peak_fav']:>6.3f}% {t['peak_adv']:>6.3f}%")

    # ═══ No-SL risk analysis ═══
    print(f"\n{'='*100}")
    print(f"  SL YOK SENARYOLARINDA LIKIDASYON RISKI")
    print(f"{'='*100}")
    no_sl = [r for r in all_results if r["sl_pct"] == 0]
    for r in sorted(no_sl, key=lambda x: -x["total_roi"]):
        liq_rate = r["liq"] / r["trades"] * 100 if r["trades"] > 0 else 0
        print(f"  {r['name']:<24} Liq:{r['liq']}/{r['trades']} ({liq_rate:.0f}%) "
              f"Liq mesafe:{r['liq_pct']:.2f}% TopROI:{r['total_roi']:+.1f}%")

    print(f"\n{'='*100}")
    print(f"  NOTLAR:")
    print(f"  - Tek pozisyon: bir sinyal acilinca kapanana kadar yeni sinyal alinmaz")
    print(f"  - SL=YOK: likidasyon mesafesi icinde SL yok, sadece liq korumasi")
    print(f"  - TIME: 24 saat icinde ne TP ne SL/LIQ olmussa o anki fiyattan kapanir")
    print(f"  - Fee her senaryoda dahil (gidis-donus)")
    print(f"{'='*100}\n")


if __name__ == "__main__":
    run()
