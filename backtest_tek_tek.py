"""System N Tek Tek Ek Kontrol Testi — 50 coin gercekci simulasyon.

Referans: Orijinal System N (ek filtre YOK)
Sonra teker teker her kontrolu ekleyerek ve cikararak test eder.

Testler:
  1. REFERANS: Orijinal N (filtresiz)
  2. +MACD align (tek basina)
  3. +ER>0.2 (tek basina)
  4. +ER>0.3 (tek basina)
  5. +RANGING reject (tek basina)
  6. +RSI align (tek basina)
  7. +MACD + ER>0.2
  8. +MACD + RANGING
  9. +MACD + ER>0.2 + RANGING
  10. +MACD + RSI + ER>0.2 + RANGING (tam paket)
  11. +ER>0.2 + RANGING
  12. +MACD + ER>0.3 + RANGING
  13. +Max kaldirac siniri (<=20x)
  14. +Max kaldirac siniri (<=30x)
  15. +SL siki (G*1.0)
  16. +SL genis (G*2.0)
"""

import sys, os, json, time, argparse, threading
from pathlib import Path
from dataclasses import dataclass, field
from datetime import datetime

import numpy as np
import pandas as pd

import io
if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from market.binance_rest import BinanceRestClient
from scanner.system_n_scanner import compute_alpha_trend, _compute_adx, _sma, _compute_ema, _compute_rsi
from scanner.system_b_scanner import detect_zigzag_swings, analyze_waves, compute_rolling_er, compute_hurst_exponent
from loguru import logger

# ═══ SABITLER ═══
TF = "5m"; TARGET_BARS = 8640; WARMUP_BARS = 100
TAKER_FEE = 0.0004; ENTRY_FEE = TAKER_FEE; EXIT_FEE = TAKER_FEE
DEFAULT_SL_G_MULT = 1.5; SL_FEE_PCT = (ENTRY_FEE + EXIT_FEE) * 100
SL_ATR_MULT = 2.0; DEFAULT_MAINT_RATE = 0.004; MAX_LEVERAGE = 125
PORTFOLIO_DIVIDER = 12; BALANCE = 4.0; ZIGZAG_N = 5
ADX_LENGTH = 14; ADX_THRESHOLD = 18.0
DEFAULT_COEFF = 3.6; DEFAULT_PERIOD = 27

@dataclass
class FilterCfg:
    name: str
    macd_align: bool = False
    rsi_align: bool = False
    rsi_long_min: float = 40.0
    rsi_short_max: float = 60.0
    er_filter: bool = False
    er_min: float = 0.2
    ranging_reject: bool = False
    max_lev_cap: int = 0       # 0=sinir yok, >0=kaldirac siniri
    sl_g_mult: float = 1.5     # SL carpani

TESTS = [
    FilterCfg("1.REFERANS (filtresiz)"),
    # Tek basina
    FilterCfg("2.+MACD", macd_align=True),
    FilterCfg("3.+ER>0.2", er_filter=True, er_min=0.2),
    FilterCfg("4.+ER>0.3", er_filter=True, er_min=0.3),
    FilterCfg("5.+RANGING rej", ranging_reject=True),
    FilterCfg("6.+RSI", rsi_align=True),
    # Ikili
    FilterCfg("7.+MACD+ER>0.2", macd_align=True, er_filter=True, er_min=0.2),
    FilterCfg("8.+MACD+RANGING", macd_align=True, ranging_reject=True),
    FilterCfg("9.+ER>0.2+RANGING", er_filter=True, er_min=0.2, ranging_reject=True),
    # Uclu
    FilterCfg("10.+MACD+ER>0.2+RNG", macd_align=True, er_filter=True, er_min=0.2, ranging_reject=True),
    FilterCfg("11.+MACD+ER>0.3+RNG", macd_align=True, er_filter=True, er_min=0.3, ranging_reject=True),
    # Tam
    FilterCfg("12.TAM PAKET", macd_align=True, rsi_align=True, er_filter=True, er_min=0.2, ranging_reject=True),
    # Kaldirac siniri
    FilterCfg("13.MaxLev<=20x", max_lev_cap=20),
    FilterCfg("14.MaxLev<=30x", max_lev_cap=30),
    FilterCfg("15.MaxLev<=15x", max_lev_cap=15),
    # SL degisiklikleri
    FilterCfg("16.SL G*1.0(siki)", sl_g_mult=1.0),
    FilterCfg("17.SL G*2.0(genis)", sl_g_mult=2.0),
    FilterCfg("18.SL G*2.5", sl_g_mult=2.5),
    # En iyi + kaldirac
    FilterCfg("19.MACD+ER+RNG+Lev20", macd_align=True, er_filter=True, er_min=0.2, ranging_reject=True, max_lev_cap=20),
    FilterCfg("20.MACD+ER+RNG+SL2.0", macd_align=True, er_filter=True, er_min=0.2, ranging_reject=True, sl_g_mult=2.0),
]


def calc_liq(entry, lev, direction):
    if lev <= 0: lev = 1
    if direction == "LONG":
        return entry * (1 - 1/lev + DEFAULT_MAINT_RATE)
    return entry * (1 + 1/lev - DEFAULT_MAINT_RATE)


def detect_regime_at(closes, highs, lows, idx, w=300):
    s = max(0, idx - w)
    if idx - s < 100: return "UNKNOWN"
    try:
        sw = detect_zigzag_swings(highs[s:idx], lows[s:idx], n=ZIGZAG_N)
        if len(sw) < 4: return "UNKNOWN"
        er = compute_rolling_er(closes[s:idx], window=20, median_count=10)
        hu = compute_hurst_exponent(closes[s:idx])
        if er > 0.25: return "TRENDING"
        if er < 0.08: return "RANGING"
        if hu > 0.55: return "TRENDING"
        if hu < 0.45: return "RANGING"
        return "GRAY"
    except: return "UNKNOWN"


def simulate(closes, highs, lows, volumes, symbol, coeff, period, g_pct, base_lev, fc):
    n = len(closes)
    if n < max(period * 3, ADX_LENGTH * 3, WARMUP_BARS): return []

    # Kaldirac siniri uygula
    lev = base_lev
    if fc.max_lev_cap > 0:
        lev = min(lev, fc.max_lev_cap)

    # SL hesapla
    sl_g = fc.sl_g_mult
    if g_pct > 0:
        sl_pct = g_pct * sl_g + SL_FEE_PCT
    else:
        sl_pct = 5.0

    alpha_trend, atr_arr = compute_alpha_trend(highs, lows, closes, volumes,
                                                coeff=coeff, period=period, use_mfi=True)
    adx_arr, _, _ = _compute_adx(highs, lows, closes, ADX_LENGTH)
    adx_sma = _sma(adx_arr, ADX_LENGTH)
    atr_clean = atr_arr.copy(); atr_clean[np.isnan(atr_clean)] = 0.0
    rsi_arr = _compute_rsi(closes, 14)

    # MACD
    ef = _compute_ema(closes, 12); es = _compute_ema(closes, 26)
    ml = ef - es; ml[np.isnan(ml)] = 0.0
    ms = _compute_ema(ml, 9); mh = ml - ms; mh[np.isnan(mh)] = 0.0

    er_period = 10
    margin = max(BALANCE / PORTFOLIO_DIVIDER, 1.0)
    notional = margin * lev
    fee_per = notional * (ENTRY_FEE + EXIT_FEE)

    trades = []
    pos = None; e_price = e_idx = 0; sl_p = liq_p = 0.0
    wins = losses = sls = liqs = 0
    total_pnl = total_fee = 0.0

    start = max(period * 3, WARMUP_BARS, 4, 36)

    for i in range(start, n):
        if pos is not None:
            if pos == "LONG":
                if lows[i] <= liq_p:
                    total_pnl -= margin; liqs += 1; total_fee += fee_per; losses += 1
                    trades.append(("LIQ", -margin)); pos = None; continue
                if lows[i] <= sl_p:
                    raw = (sl_p - e_price) / e_price * 100
                    net = raw - (ENTRY_FEE + EXIT_FEE) * 100
                    pnl = margin * (net / 100) * lev
                    total_pnl += pnl; sls += 1; total_fee += fee_per; losses += 1
                    trades.append(("SL", pnl)); pos = None; continue
            else:
                if highs[i] >= liq_p:
                    total_pnl -= margin; liqs += 1; total_fee += fee_per; losses += 1
                    trades.append(("LIQ", -margin)); pos = None; continue
                if highs[i] >= sl_p:
                    raw = (e_price - sl_p) / e_price * 100
                    net = raw - (ENTRY_FEE + EXIT_FEE) * 100
                    pnl = margin * (net / 100) * lev
                    total_pnl += pnl; sls += 1; total_fee += fee_per; losses += 1
                    trades.append(("SL", pnl)); pos = None; continue

        at0 = alpha_trend[i]; at1 = alpha_trend[i-1]
        at2 = alpha_trend[i-2]; at3 = alpha_trend[i-3]
        if any(np.isnan(v) for v in [at0, at1, at2, at3]): continue

        av = adx_arr[i] if not np.isnan(adx_arr[i]) else 0.0
        ad = adx_sma[i] if not np.isnan(adx_sma[i]) else 0.0
        base_ok = (av > ADX_THRESHOLD) and (av > ad)

        bc = (at0 > at2) and (at1 <= at3) and base_ok
        sc = (at0 < at2) and (at1 >= at3) and base_ok

        # Momentum check
        if bc or sc:
            dn = abs(at0 - at2); dp = abs(at1 - at3)
            if dp > 0 and dn < dp * 0.7: continue

        if not (bc or sc): continue

        # Kapat (raw sinyal — filtre engellemez)
        if pos == "LONG" and sc:
            raw = (closes[i] - e_price) / e_price * 100
            net = raw - (ENTRY_FEE + EXIT_FEE) * 100
            pnl = margin * (net / 100) * lev
            total_pnl += pnl; total_fee += fee_per
            if pnl > 0: wins += 1
            else: losses += 1
            trades.append(("SIG", pnl)); pos = None
        elif pos == "SHORT" and bc:
            raw = (e_price - closes[i]) / e_price * 100
            net = raw - (ENTRY_FEE + EXIT_FEE) * 100
            pnl = margin * (net / 100) * lev
            total_pnl += pnl; total_fee += fee_per
            if pnl > 0: wins += 1
            else: losses += 1
            trades.append(("SIG", pnl)); pos = None

        if not base_ok: continue

        # ── EK FILTRELER (sadece yeni giris) ──
        extra_ok = True

        if fc.macd_align:
            h_val = mh[i]
            if bc: extra_ok = extra_ok and (h_val > 0)
            elif sc: extra_ok = extra_ok and (h_val < 0)

        if fc.rsi_align:
            rv = rsi_arr[i] if not np.isnan(rsi_arr[i]) else 50.0
            if bc: extra_ok = extra_ok and (rv > fc.rsi_long_min)
            elif sc: extra_ok = extra_ok and (rv < fc.rsi_short_max)

        if fc.er_filter:
            if i >= er_period + 1:
                d = abs(closes[i] - closes[i - er_period])
                v = np.sum(np.abs(np.diff(closes[i - er_period:i + 1])))
                er_v = d / v if v > 1e-12 else 0.5
            else: er_v = 0.5
            extra_ok = extra_ok and (er_v > fc.er_min)

        if fc.ranging_reject:
            reg = detect_regime_at(closes, highs, lows, i)
            extra_ok = extra_ok and (reg != "RANGING")

        if not extra_ok: continue

        # Ac
        if pos is None:
            if bc: pos = "LONG"
            elif sc: pos = "SHORT"
            else: continue
            e_idx = i; e_price = closes[i]
            if pos == "LONG":
                sl_p = e_price * (1 - sl_pct / 100)
            else:
                sl_p = e_price * (1 + sl_pct / 100)
            liq_p = calc_liq(e_price, lev, pos)

    total_trades = len(trades)
    return {
        "trades": total_trades, "wins": wins, "losses": losses + sls + liqs,
        "sls": sls, "liqs": liqs,
        "pnl": total_pnl, "fee": total_fee,
        "wr": wins / total_trades * 100 if total_trades > 0 else 0,
        "pf": sum(p for _, p in trades if p > 0) / abs(sum(p for _, p in trades if p < 0))
              if any(p < 0 for _, p in trades) else 999,
    }


def fetch_klines(client, symbol, target=TARGET_BARS):
    import signal as _sig
    try:
        # Timeout: 15 saniye icinde gelmezse atla
        import threading
        result_holder = [None]
        def _fetch():
            try:
                result_holder[0] = client.get_klines(symbol, TF, min(target, 1500))
            except: pass
        t = threading.Thread(target=_fetch, daemon=True)
        t.start()
        t.join(timeout=15)
        if t.is_alive():
            return None  # timeout
        df = result_holder[0]
        if df is None or len(df) < WARMUP_BARS: return None
        all_dfs = [df]; fetched = len(df)
        max_pages = 8
        page = 0
        while fetched < target and page < max_pages:
            page += 1
            ts = int(df["timestamp"].iloc[0].timestamp() * 1000)
            raw_holder = [None]
            def _fetch_page():
                try:
                    raw_holder[0] = client._get("/fapi/v1/klines", {
                        "symbol": symbol, "interval": TF,
                        "limit": min(1500, target - fetched), "endTime": ts - 1,
                    })
                except: pass
            pt = threading.Thread(target=_fetch_page, daemon=True)
            pt.start()
            pt.join(timeout=10)
            if pt.is_alive(): break
            raw = raw_holder[0]
            if not raw: break
            if not raw: break
            df2 = pd.DataFrame(raw, columns=[
                "timestamp","open","high","low","close","volume",
                "close_time","quote_volume","trades","taker_buy_volume",
                "taker_buy_quote_volume","ignore",
            ])
            for c in ["open","high","low","close","volume"]: df2[c] = df2[c].astype(float)
            df2["timestamp"] = pd.to_datetime(df2["timestamp"], unit="ms")
            if len(df2) == 0: break
            all_dfs.insert(0, df2); fetched += len(df2); df = df2
            time.sleep(0.15)
        combined = pd.concat(all_dfs, ignore_index=True)
        return combined.drop_duplicates(subset=["timestamp"]).sort_values("timestamp").reset_index(drop=True)
    except: return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--top", type=int, default=50)
    parser.add_argument("--symbols", nargs="+", default=None)
    args = parser.parse_args()

    client = BinanceRestClient()
    if args.symbols:
        symbols = args.symbols
    else:
        try:
            t24 = client._get("/fapi/v1/ticker/24hr")
            st = sorted(t24, key=lambda x: float(x.get("quoteVolume",0)), reverse=True)
            symbols = [t["symbol"] for t in st if t["symbol"].endswith("USDT")][:args.top]
        except: symbols = []

    opt_cache = {}
    if os.path.exists("data/system_n_optimize.json"):
        try:
            with open("data/system_n_optimize.json","r") as f:
                data = json.load(f)
            for sym, info in data.get("results",{}).items():
                ot = info.get("optimal_tf","5m")
                p = info.get("params",{}).get(ot,{})
                gd = info.get("g_analysis",{}).get(ot,{})
                c,pp = p.get("coeff",0), p.get("period",0)
                G = gd.get("G", info.get("G",0))
                if c>0 and pp>0: opt_cache[sym] = {"coeff":c,"period":pp,"G":G}
        except: pass

    print("\n" + "="*120)
    print(f"  SYSTEM N TEK TEK EK KONTROL TESTI — {len(symbols)} coin, ~30 gun")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M')} | Referans: Orijinal N (filtresiz)")
    print("="*120)

    # Veri cek (bir kere)
    coin_data = {}
    for si, sym in enumerate(symbols):
        print(f"  [{si+1}/{len(symbols)}] {sym} veri cekiliyor...", end="", flush=True)
        df = fetch_klines(client, sym)
        if df is None or len(df) < WARMUP_BARS:
            print(" SKIP"); continue
        c = df["close"].values.astype(float)
        h = df["high"].values.astype(float)
        l = df["low"].values.astype(float)
        v = df["volume"].values.astype(float)

        params = opt_cache.get(sym, {})
        coeff = params.get("coeff", DEFAULT_COEFF)
        period = params.get("period", DEFAULT_PERIOD)
        g_pct = params.get("G", 0)

        if g_pct <= 0 or g_pct > 50:
            try:
                sw = detect_zigzag_swings(h[-300:], l[-300:], n=ZIGZAG_N)
                if len(sw) >= 4:
                    wave = analyze_waves(sw, float(c[-1]))
                    g_pct = wave.G if 0 < wave.G <= 50 else 0
            except: pass

        sl_p = g_pct * DEFAULT_SL_G_MULT + SL_FEE_PCT if g_pct > 0 else 5.0
        teorik = sl_p * 2.0 + DEFAULT_MAINT_RATE * 100
        lev = max(1, min(int(100.0/teorik), MAX_LEVERAGE)) if teorik > 0 else 1

        coin_data[sym] = {"c": c, "h": h, "l": l, "v": v,
                          "coeff": coeff, "period": period, "g": g_pct, "lev": lev}
        print(f" OK ({len(df)} mum, lev={lev}x)")
        time.sleep(0.15)

    print(f"\n  {len(coin_data)} coin hazir. {len(TESTS)} test basliyor...\n")

    # Her test icin tum coinleri calistir
    results = []
    t0 = time.time()

    for ti, fc in enumerate(TESTS):
        agg = {"trades":0,"wins":0,"losses":0,"sls":0,"liqs":0,"pnl":0,"fee":0,
               "gross_p":0,"gross_l":0}

        for sym, d in coin_data.items():
            r = simulate(d["c"], d["h"], d["l"], d["v"], sym,
                         d["coeff"], d["period"], d["g"], d["lev"], fc)
            agg["trades"] += r["trades"]
            agg["wins"] += r["wins"]
            agg["losses"] += r["losses"]
            agg["sls"] += r["sls"]
            agg["liqs"] += r["liqs"]
            agg["pnl"] += r["pnl"]
            agg["fee"] += r["fee"]

        wr = agg["wins"] / agg["trades"] * 100 if agg["trades"] > 0 else 0
        results.append({"name": fc.name, **agg, "wr": wr})
        print(f"  {fc.name:<30} Tr={agg['trades']:>5} Win={agg['wins']:>4} "
              f"WR={wr:>5.1f}% SL={agg['sls']:>4} Liq={agg['liqs']:>2} "
              f"Fee={agg['fee']:>6.2f}$ PnL={agg['pnl']:>+8.2f}$")

    elapsed = time.time() - t0

    # ═══ SONUC TABLOSU ═══
    ref = results[0]

    print(f"\n\n{'='*140}")
    print(f"  SONUC TABLOSU — Referansa gore farklar")
    print(f"{'='*140}")

    print(f"\n  {'#':<3} {'Test':<32} {'Tr':>5} {'Win':>4} {'WR%':>6} "
          f"{'SL':>4} {'Liq':>3} {'Fee$':>7} {'PnL$':>9} "
          f"{'dTr':>5} {'dWR':>6} {'dSL':>4} {'dLiq':>4} {'dPnL$':>9}")
    print(f"  {'-'*120}")

    for i, r in enumerate(results):
        dt = r["trades"] - ref["trades"]
        dw = r["wr"] - ref["wr"]
        ds = r["sls"] - ref["sls"]
        dl = r["liqs"] - ref["liqs"]
        dp = r["pnl"] - ref["pnl"]
        marker = " ***" if dp > 1.0 else (" !!!" if dp < -1.0 else "")
        print(f"  {i+1:<3} {r['name']:<32} {r['trades']:>5} {r['wins']:>4} {r['wr']:>5.1f}% "
              f"{r['sls']:>4} {r['liqs']:>3} {r['fee']:>6.2f} {r['pnl']:>+8.2f} "
              f"{dt:>+5} {dw:>+5.1f}% {ds:>+4} {dl:>+3} {dp:>+8.2f}{marker}")

    # En iyi 3
    sorted_r = sorted(results[1:], key=lambda x: x["pnl"], reverse=True)
    print(f"\n  EN IYI 3:")
    for r in sorted_r[:3]:
        dp = r["pnl"] - ref["pnl"]
        print(f"    {r['name']:<32} PnL={r['pnl']:>+8.2f}$ (ref'e gore {dp:>+.2f}$) "
              f"WR={r['wr']:.1f}% SL={r['sls']} Liq={r['liqs']}")

    print(f"\n  EN KOTU 3:")
    for r in sorted_r[-3:]:
        dp = r["pnl"] - ref["pnl"]
        print(f"    {r['name']:<32} PnL={r['pnl']:>+8.2f}$ (ref'e gore {dp:>+.2f}$) "
              f"WR={r['wr']:.1f}% SL={r['sls']} Liq={r['liqs']}")

    print(f"\n  REFERANS: PnL={ref['pnl']:>+.2f}$ WR={ref['wr']:.1f}% "
          f"SL={ref['sls']} Liq={ref['liqs']} Fee={ref['fee']:.2f}$")
    print(f"  Sure: {elapsed:.1f}s")

    # JSON
    p = Path("data/backtest_tek_tek.json")
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump({"timestamp": datetime.now().isoformat(), "results": results}, f, indent=2)
    print(f"  Kaydedildi: {p}")


if __name__ == "__main__":
    main()
