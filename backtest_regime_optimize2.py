"""Rejim tespiti optimizasyonu v2 — pre-computed indicators ile hizli grid search.

Tum indikatorler (ER, Hurst, ADX, BBW) her mum icin ONCEDEN hesaplanir,
sonra grid search sadece esik karsilastirmasi yapar (O(1) per eval point).
"""
import os, time, hmac, hashlib, requests, numpy as np
from urllib.parse import urlencode
from collections import defaultdict
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv("BINANCE_API_KEY")
API_SECRET = os.getenv("BINANCE_API_SECRET")
session = requests.Session()
session.headers["X-MBX-APIKEY"] = API_KEY
BASE = "https://fapi.binance.com"

def sign(p):
    p["timestamp"] = int(time.time() * 1000)
    qs = urlencode(p)
    p["signature"] = hmac.new(API_SECRET.encode(), qs.encode(), hashlib.sha256).hexdigest()
    return p

def fetch_klines(symbol, interval, limit=1500):
    resp = session.get(f"{BASE}/fapi/v1/klines", params={"symbol": symbol, "interval": interval, "limit": limit})
    data = resp.json()
    return data if isinstance(data, list) else None

# ═══════════════════ INDIKATOR HESAPLAMALARI ═══════════════════

def rolling_er_series(closes, window=20, median_count=10):
    """Her mum icin rolling ER median degeri."""
    n = len(closes)
    result = np.zeros(n)
    min_len = window + median_count
    for end in range(min_len, n + 1):
        seg = closes[:end]
        ers = []
        for i in range(max(0, len(seg) - window - median_count), len(seg) - window + 1):
            s = seg[i:i + window]
            net = abs(s[-1] - s[0])
            total = np.sum(np.abs(np.diff(s)))
            ers.append(net / total if total > 0 else 0.0)
        result[end - 1] = float(np.median(ers[-median_count:])) if ers else 0.0
    return result

def hurst_series(closes, step=5):
    """Her `step` mumda bir Hurst hesapla (agir islem, araliklarla yap)."""
    n = len(closes)
    result = np.full(n, 0.5)
    for i in range(128, n, step):
        result[i] = _hurst(closes[:i + 1])
    # Aradalari doldur (en yakin hesaplanmis deger)
    last_val = 0.5
    for i in range(128, n):
        if result[i] != 0.5 or i % step == 0:
            last_val = result[i]
        else:
            result[i] = last_val
    return result

def _hurst(closes):
    if len(closes) < 128:
        return 0.5
    log_ret = np.diff(np.log(closes[-256:]))  # son 256 mum yeterli
    ns = [n for n in [16, 32, 64, 128] if n <= len(log_ret)]
    if len(ns) < 2:
        return 0.5
    rs_vals = []
    for n in ns:
        rs_list = []
        for i in range(len(log_ret) // n):
            chunk = log_ret[i * n:(i + 1) * n]
            dev = np.cumsum(chunk - np.mean(chunk))
            R = np.max(dev) - np.min(dev)
            S = np.std(chunk, ddof=1)
            if S > 0:
                rs_list.append(R / S)
        if rs_list:
            rs_vals.append((np.log(n), np.log(np.mean(rs_list))))
    if len(rs_vals) < 2:
        return 0.5
    x = np.array([v[0] for v in rs_vals])
    y = np.array([v[1] for v in rs_vals])
    np_ = len(x)
    H = (np_ * np.sum(x * y) - np.sum(x) * np.sum(y)) / (np_ * np.sum(x ** 2) - np.sum(x) ** 2)
    return float(np.clip(H, 0.0, 1.0))

def adx_series(highs, lows, closes, period=14):
    n = len(closes)
    adx = np.full(n, 25.0)
    tr = np.zeros(n)
    plus_dm = np.zeros(n)
    minus_dm = np.zeros(n)
    for i in range(1, n):
        h_diff = highs[i] - highs[i - 1]
        l_diff = lows[i - 1] - lows[i]
        tr[i] = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
        plus_dm[i] = h_diff if (h_diff > l_diff and h_diff > 0) else 0
        minus_dm[i] = l_diff if (l_diff > h_diff and l_diff > 0) else 0
    atr_ = np.zeros(n)
    dx = np.zeros(n)
    if n <= period:
        return adx
    atr_[period] = np.sum(tr[1:period + 1])
    sp = np.sum(plus_dm[1:period + 1])
    sm = np.sum(minus_dm[1:period + 1])
    for i in range(period + 1, n):
        atr_[i] = atr_[i - 1] - atr_[i - 1] / period + tr[i]
        sp = sp - sp / period + plus_dm[i]
        sm = sm - sm / period + minus_dm[i]
        if atr_[i] > 0:
            pdi = 100 * sp / atr_[i]
            mdi = 100 * sm / atr_[i]
        else:
            pdi = mdi = 0
        di_sum = pdi + mdi
        dx[i] = 100 * abs(pdi - mdi) / di_sum if di_sum > 0 else 0
    if n > period * 2:
        adx[period * 2] = np.mean(dx[period + 1:period * 2 + 1])
        for i in range(period * 2 + 1, n):
            adx[i] = (adx[i - 1] * (period - 1) + dx[i]) / period
    return adx

def bbw_series(closes, period=20):
    n = len(closes)
    width = np.zeros(n)
    for i in range(period - 1, n):
        w = closes[i - period + 1:i + 1]
        m = np.mean(w)
        s = np.std(w, ddof=1)
        if m > 0:
            width[i] = (4.0 * s) / m
    return width

def bbw_expanding_series(bbw, lookback=10):
    n = len(bbw)
    expanding = np.zeros(n, dtype=bool)
    for i in range(lookback, n):
        recent = bbw[i - lookback + 1:i + 1]
        if len(recent) >= 2 and recent[-1] > np.mean(recent[:-1]) * 1.05:
            expanding[i] = True
    return expanding

# ═══════════════════ MULTI-WINDOW ER ═══════════════════

def rolling_er_series_w(closes, window):
    """Belirli window icin rolling ER (median_count=10 sabit)."""
    return rolling_er_series(closes, window=window, median_count=10)

# ═══════════════════ GROUND TRUTH ═══════════════════

def compute_ground_truth(closes, future_bars, trend_threshold):
    """Her noktada gercek rejim etiketini belirle."""
    n = len(closes)
    labels = []  # (idx, actual_regime)
    for i in range(n - future_bars):
        future = closes[i:i + future_bars + 1]
        net_pct = abs(future[-1] - future[0]) / future[0] * 100.0
        labels.append("TRENDING" if net_pct > trend_threshold else "RANGING")
    return labels

# ═══════════════════ COINS & CONFIG ═══════════════════

COINS = ["BTCUSDT", "ETHUSDT", "XRPUSDT", "AVAXUSDT", "DOGEUSDT", "SOLUSDT", "ADAUSDT", "BNBUSDT"]
TRADE_TF = "15m"
LOOKBACK = 200
FUTURE_BARS = 20
EVAL_EVERY = 5
TREND_THRESHOLD = 1.0

def main():
    print("=" * 130)
    print("REJIM TESPITI OPTIMIZASYONU v2 — Pre-computed Grid Search")
    print(f"TF: {TRADE_TF} | Forward: {FUTURE_BARS} bar | Trend esigi: >{TREND_THRESHOLD}%")
    print("=" * 130)

    # ── Veri cek & indikatorler hesapla ──
    coin_data = {}
    for sym in COINS:
        print(f"  {sym}...", end=" ", flush=True)
        kl = fetch_klines(sym, TRADE_TF, 1500)
        if not kl or len(kl) < LOOKBACK + FUTURE_BARS + 50:
            print("SKIP")
            continue
        closes = np.array([float(k[4]) for k in kl])
        highs = np.array([float(k[2]) for k in kl])
        lows = np.array([float(k[3]) for k in kl])

        print("indicators...", end=" ", flush=True)

        # Pre-compute ER for multiple windows
        er_w10 = rolling_er_series(closes, window=10, median_count=10)
        er_w20 = rolling_er_series(closes, window=20, median_count=10)
        er_w30 = rolling_er_series(closes, window=30, median_count=10)
        er_w50 = rolling_er_series(closes, window=50, median_count=10)

        hurst = hurst_series(closes, step=5)
        adx = adx_series(highs, lows, closes, 14)
        bbw = bbw_series(closes, 20)
        bbw_exp = bbw_expanding_series(bbw, 10)

        # Ground truth
        gt = compute_ground_truth(closes, FUTURE_BARS, TREND_THRESHOLD)

        coin_data[sym] = {
            "closes": closes, "er": {10: er_w10, 20: er_w20, 30: er_w30, 50: er_w50},
            "hurst": hurst, "adx": adx, "bbw": bbw, "bbw_exp": bbw_exp, "gt": gt,
        }
        print("OK")
        time.sleep(0.15)

    # Ground truth dagilimi
    total_t = sum(1 for cd in coin_data.values() for i in range(LOOKBACK, len(cd["gt"]), EVAL_EVERY) if cd["gt"][i] == "TRENDING")
    total_r = sum(1 for cd in coin_data.values() for i in range(LOOKBACK, len(cd["gt"]), EVAL_EVERY) if cd["gt"][i] == "RANGING")
    print(f"\nGercek dagilim: TRENDING={total_t} ({total_t/(total_t+total_r)*100:.1f}%), RANGING={total_r} ({total_r/(total_t+total_r)*100:.1f}%)")

    # ── Eval points ──
    eval_points = []  # (coin_key, idx)
    for sym, cd in coin_data.items():
        for i in range(LOOKBACK, len(cd["gt"]), EVAL_EVERY):
            eval_points.append((sym, i))

    n_eval = len(eval_points)
    print(f"Toplam eval noktasi: {n_eval}")

    # Pre-extract arrays for fast access
    er_vals = {}    # (sym, window, idx) -> er value
    hurst_vals = {} # (sym, idx) -> hurst
    adx_vals = {}   # (sym, idx) -> adx
    bbw_vals = {}   # (sym, idx) -> bbw
    bbw_exp_vals = {}
    gt_vals = {}

    for sym, idx in eval_points:
        cd = coin_data[sym]
        for w in [10, 20, 30, 50]:
            er_vals[(sym, w, idx)] = cd["er"][w][idx]
        hurst_vals[(sym, idx)] = cd["hurst"][idx]
        adx_vals[(sym, idx)] = cd["adx"][idx]
        bbw_vals[(sym, idx)] = cd["bbw"][idx]
        bbw_exp_vals[(sym, idx)] = cd["bbw_exp"][idx]
        gt_vals[(sym, idx)] = cd["gt"][idx]

    def evaluate(predict_fn):
        """Bir predict fonksiyonunu tum eval noktalarinda test et."""
        tt = tr = rt = rr = 0
        for sym, idx in eval_points:
            pred = predict_fn(sym, idx)
            actual = gt_vals[(sym, idx)]
            if pred == "TRENDING":
                if actual == "TRENDING": tt += 1
                else: tr += 1
            else:
                if actual == "TRENDING": rt += 1
                else: rr += 1
        total = tt + tr + rt + rr
        if total == 0:
            return None
        acc = (tt + rr) / total * 100
        t_prec = tt / (tt + tr) * 100 if (tt + tr) > 0 else 0
        t_rec = tt / (tt + rt) * 100 if (tt + rt) > 0 else 0
        r_prec = rr / (rr + rt) * 100 if (rr + rt) > 0 else 0
        r_rec = rr / (rr + tr) * 100 if (rr + tr) > 0 else 0
        t_f1 = 2 * t_prec * t_rec / (t_prec + t_rec) if (t_prec + t_rec) > 0 else 0
        r_f1 = 2 * r_prec * r_rec / (r_prec + r_rec) if (r_prec + r_rec) > 0 else 0
        bal_acc = (t_rec + r_rec) / 2.0
        return {"acc": acc, "bal": bal_acc, "tp": t_prec, "tr": t_rec,
                "rp": r_prec, "rr_": r_rec, "tf1": t_f1, "rf1": r_f1,
                "tt": tt, "tr_": tr, "rt": rt, "rr": rr,
                "t_pred": tt + tr, "r_pred": rt + rr}

    results = []

    # ═══════════════ 1. ER-ONLY ═══════════════
    print("\n1. ER-only...", flush=True)
    for w in [10, 20, 30, 50]:
        for et in [0.20, 0.25, 0.30, 0.35, 0.40, 0.45, 0.50, 0.55, 0.60]:
            for er in [0.05, 0.08, 0.10, 0.15, 0.20, 0.25]:
                if er >= et: continue
                def fn(s, i, _w=w, _et=et, _er=er):
                    v = er_vals[(s, _w, i)]
                    if v > _et: return "TRENDING"
                    return "RANGING"
                r = evaluate(fn)
                if r: results.append(("ER-only", f"w={w} t={et} r={er}", r))

    # ═══════════════ 2. ADX-ONLY ═══════════════
    print("2. ADX-only...", flush=True)
    for at in [18, 20, 22, 25, 28, 30, 35, 40]:
        for ar in [12, 15, 18, 20, 22, 25]:
            if ar >= at: continue
            def fn(s, i, _at=at, _ar=ar):
                v = adx_vals[(s, i)]
                if v > _at: return "TRENDING"
                return "RANGING"
            r = evaluate(fn)
            if r: results.append(("ADX-only", f"t={at} r={ar}", r))

    # ═══════════════ 3. ER+HURST (System J) ═══════════════
    print("3. ER+Hurst...", flush=True)
    for w in [10, 20, 30, 50]:
        for et in [0.25, 0.30, 0.35, 0.40, 0.45, 0.50, 0.55, 0.60]:
            for er in [0.05, 0.08, 0.10, 0.15, 0.20]:
                if er >= et: continue
                for ht in [0.55, 0.60, 0.65]:
                    for hr in [0.40, 0.45, 0.50]:
                        if hr >= ht: continue
                        def fn(s, i, _w=w, _et=et, _er=er, _ht=ht, _hr=hr):
                            v = er_vals[(s, _w, i)]
                            if v > _et: return "TRENDING"
                            if v < _er: return "RANGING"
                            h = hurst_vals[(s, i)]
                            mid = (_et + _er) / 2.0
                            if h > _ht: return "TRENDING"
                            if h < _hr: return "RANGING"
                            if v > mid: return "TRENDING"
                            return "RANGING"
                        r = evaluate(fn)
                        if r: results.append(("ER+Hurst", f"w={w} er={et}/{er} h={ht}/{hr}", r))

    # ═══════════════ 4. ER+ADX STRICT ═══════════════
    print("4. ER+ADX strict...", flush=True)
    for w in [10, 20, 30]:
        for et in [0.20, 0.25, 0.30, 0.35, 0.40, 0.45]:
            for er in [0.08, 0.10, 0.15]:
                if er >= et: continue
                for at in [18, 20, 22, 25, 28, 30]:
                    def fn(s, i, _w=w, _et=et, _at=at):
                        return "TRENDING" if (er_vals[(s, _w, i)] > _et and adx_vals[(s, i)] > _at) else "RANGING"
                    r = evaluate(fn)
                    if r: results.append(("ER+ADX strict", f"w={w} er>{et} adx>{at}", r))

    # ═══════════════ 5. ER+ADX VOTE ═══════════════
    print("5. ER+ADX vote...", flush=True)
    for w in [10, 20, 30]:
        for et in [0.25, 0.30, 0.35, 0.40, 0.45]:
            for er in [0.08, 0.10, 0.15]:
                if er >= et: continue
                for at in [20, 22, 25, 28]:
                    for ar in [15, 18, 20]:
                        if ar >= at: continue
                        def fn(s, i, _w=w, _et=et, _er=er, _at=at, _ar=ar):
                            vt = vr = 0
                            e = er_vals[(s, _w, i)]
                            if e > _et: vt += 1
                            elif e < _er: vr += 1
                            a = adx_vals[(s, i)]
                            if a > _at: vt += 1
                            elif a < _ar: vr += 1
                            return "TRENDING" if vt > vr else "RANGING"
                        r = evaluate(fn)
                        if r: results.append(("ER+ADX vote", f"w={w} er={et}/{er} adx={at}/{ar}", r))

    # ═══════════════ 6. 3-WAY VOTE ═══════════════
    print("6. 3-way vote...", flush=True)
    for w in [20, 30]:
        for et in [0.30, 0.35, 0.40, 0.45]:
            for er in [0.10, 0.15]:
                if er >= et: continue
                for at in [22, 25, 28]:
                    for ar in [15, 18, 20]:
                        if ar >= at: continue
                        for bt in [0.03, 0.04, 0.05]:
                            for br in [0.015, 0.02, 0.025]:
                                def fn(s, i, _w=w, _et=et, _er=er, _at=at, _ar=ar, _bt=bt, _br=br):
                                    vt = vr = 0
                                    e = er_vals[(s, _w, i)]
                                    if e > _et: vt += 1
                                    elif e < _er: vr += 1
                                    a = adx_vals[(s, i)]
                                    if a > _at: vt += 1
                                    elif a < _ar: vr += 1
                                    b = bbw_vals[(s, i)]
                                    if b > _bt and bbw_exp_vals[(s, i)]: vt += 1
                                    elif b < _br: vr += 1
                                    return "TRENDING" if vt >= 2 else "RANGING"
                                r = evaluate(fn)
                                if r: results.append(("3-way vote", f"w={w} er={et}/{er} adx={at}/{ar} bbw={bt}/{br}", r))

    # ═══════════════ 7. ER+ADX+HURST 3-WAY ═══════════════
    print("7. ER+ADX+Hurst 3-way...", flush=True)
    for w in [20, 30]:
        for et in [0.30, 0.35, 0.40, 0.45]:
            for er in [0.10, 0.15]:
                if er >= et: continue
                for at in [22, 25, 28]:
                    for ht in [0.55, 0.60]:
                        for hr in [0.40, 0.45]:
                            if hr >= ht: continue
                            def fn(s, i, _w=w, _et=et, _er=er, _at=at, _ht=ht, _hr=hr):
                                vt = vr = 0
                                e = er_vals[(s, _w, i)]
                                if e > _et: vt += 1
                                elif e < _er: vr += 1
                                a = adx_vals[(s, i)]
                                if a > _at: vt += 1
                                h = hurst_vals[(s, i)]
                                if h > _ht: vt += 1
                                elif h < _hr: vr += 1
                                return "TRENDING" if vt >= 2 else "RANGING"
                            r = evaluate(fn)
                            if r: results.append(("ER+ADX+Hurst", f"w={w} er={et}/{er} adx>{at} h={ht}/{hr}", r))

    # ═══════════════ 8. HURST-ONLY ═══════════════
    print("8. Hurst-only...", flush=True)
    for ht in [0.50, 0.55, 0.58, 0.60, 0.65]:
        def fn(s, i, _ht=ht):
            return "TRENDING" if hurst_vals[(s, i)] > _ht else "RANGING"
        r = evaluate(fn)
        if r: results.append(("Hurst-only", f"t={ht}", r))

    # ═══════════════ SONUCLAR ═══════════════
    results.sort(key=lambda x: x[2]["bal"], reverse=True)

    print(f"\n{'='*150}")
    print(f"TOP 40 — BALANCED ACCURACY SIRALAMASI")
    print(f"{'='*150}")
    print(f"{'#':>3} | {'Yontem':16} | {'Parametreler':55} | {'Acc':>5} | {'Bal':>5} | {'T-Pre':>5} | {'T-Rec':>5} | {'R-Pre':>5} | {'R-Rec':>5} | {'T-F1':>5} | {'R-F1':>5} | {'TT':>4} | {'TR':>4} | {'RT':>4} | {'RR':>4}")
    print("-" * 150)

    for rank, (method, desc, r) in enumerate(results[:40], 1):
        print(f"{rank:>3} | {method:16} | {desc:55} | {r['acc']:>4.1f}% | {r['bal']:>4.1f}% | "
              f"{r['tp']:>4.1f}% | {r['tr']:>4.1f}% | {r['rp']:>4.1f}% | {r['rr_']:>4.1f}% | "
              f"{r['tf1']:>5.1f} | {r['rf1']:>5.1f} | "
              f"{r['tt']:>4} | {r['tr_']:>4} | {r['rt']:>4} | {r['rr']:>4}")

    # Mevcut System J
    print(f"\n{'='*150}")
    print("MEVCUT SYSTEM J (er=0.25/0.08, hurst=0.55/0.45, w=20)")
    print(f"{'='*150}")
    def sysj_fn(s, i):
        v = er_vals[(s, 20, i)]
        if v > 0.25: return "TRENDING"
        if v < 0.08: return "RANGING"
        h = hurst_vals[(s, i)]
        if h > 0.55: return "TRENDING"
        if h < 0.45: return "RANGING"
        if v > 0.165: return "TRENDING"
        return "RANGING"
    sysj = evaluate(sysj_fn)
    print(f"  Accuracy: {sysj['acc']:.1f}%  Balanced: {sysj['bal']:.1f}%")
    print(f"  T-Precision: {sysj['tp']:.1f}%  T-Recall: {sysj['tr']:.1f}%  T-F1: {sysj['tf1']:.1f}")
    print(f"  R-Precision: {sysj['rp']:.1f}%  R-Recall: {sysj['rr_']:.1f}%  R-F1: {sysj['rf1']:.1f}")
    print(f"  Confusion: TT={sysj['tt']} TR={sysj['tr_']} RT={sysj['rt']} RR={sysj['rr']}")
    print(f"  TREND tahmin: {sysj['t_pred']}  RANGE tahmin: {sysj['r_pred']}")

    # Karsilastirma
    best = results[0]
    imp = best[2]["bal"] - sysj["bal"]
    print(f"\n  EN IYI ({best[0]}: {best[1]})")
    print(f"  Balanced Acc: {best[2]['bal']:.1f}% vs mevcut {sysj['bal']:.1f}% = {imp:+.1f}% iyilestirme")
    print(f"  Accuracy:     {best[2]['acc']:.1f}% vs mevcut {sysj['acc']:.1f}%")

    # Yontem bazli en iyiler
    print(f"\n{'='*150}")
    print("YONTEM BAZINDA EN IYILER")
    print(f"{'='*150}")
    seen_methods = set()
    for method, desc, r in results:
        if method not in seen_methods:
            seen_methods.add(method)
            print(f"  {method:20} | {desc:55} | Bal={r['bal']:.1f}% Acc={r['acc']:.1f}% T-F1={r['tf1']:.1f} R-F1={r['rf1']:.1f}")
    print()

if __name__ == "__main__":
    main()
