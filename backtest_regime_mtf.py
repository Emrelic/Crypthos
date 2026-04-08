"""Multi-TF Multi-Window Rejim Tespiti Backtest'i.

Temel fikir:
  - Ayni coin'i farkli TF ve farkli pencere boyutlarinda incele
  - Binary TREND/RANGING yerine agirlikli "trend skoru" (0-100) hesapla
  - TF'ler arasi yon uyumunu kontrol et (uyum = trend, celisme = ranging)

Test edilen yaklasimlar:
  A. Multi-window ER (tek TF, farkli pencereler)
  B. Multi-TF ER (farkli TF'ler, sabit pencere)
  C. Multi-TF yon uyumu (farkli TF'lerde yon ayni mi?)
  D. Agirlikli kompozit skor (A+B+C birlesiyor)
  E. Momentum bazli (fiyat degisim hizi + ivme)
"""
import os, time, hmac, hashlib, requests, numpy as np
from urllib.parse import urlencode
from dotenv import load_dotenv

load_dotenv()
API_KEY = os.getenv("BINANCE_API_KEY")
API_SECRET = os.getenv("BINANCE_API_SECRET")
session = requests.Session()
session.headers["X-MBX-APIKEY"] = API_KEY
BASE = "https://fapi.binance.com"

def sign(p):
    p["timestamp"] = int(time.time() * 1000)
    p["signature"] = hmac.new(API_SECRET.encode(), urlencode(p).encode(), hashlib.sha256).hexdigest()
    return p

def fetch_klines(symbol, interval, limit=1500):
    resp = session.get(f"{BASE}/fapi/v1/klines",
                       params={"symbol": symbol, "interval": interval, "limit": limit})
    data = resp.json()
    return data if isinstance(data, list) else None


# ════════════════════ INDIKATORLER ════════════════════

def ema_series(closes, period):
    alpha = 2.0 / (period + 1)
    ema = np.zeros(len(closes))
    ema[0] = closes[0]
    for i in range(1, len(closes)):
        ema[i] = alpha * closes[i] + (1 - alpha) * ema[i - 1]
    return ema

def compute_rsi_series(closes, period=14):
    n = len(closes)
    rsi = np.full(n, 50.0)
    if n < period + 1:
        return rsi
    d = np.diff(closes)
    g = np.where(d > 0, d, 0.0)
    l = np.where(d < 0, -d, 0.0)
    ag = np.mean(g[:period])
    al = np.mean(l[:period])
    for i in range(period, len(d)):
        ag = (ag * (period - 1) + g[i]) / period
        al = (al * (period - 1) + l[i]) / period
        rsi[i + 1] = 100 - 100 / (1 + ag / al) if al > 0 else 100.0
    return rsi

def compute_macd_hist(closes, fast=12, slow=26, signal=9):
    ef = ema_series(closes, fast)
    es = ema_series(closes, slow)
    ml = ef - es
    sl = ema_series(ml, signal)
    return ml - sl

def compute_er_single(closes, window):
    """Tek pencere ER."""
    if len(closes) < window:
        return 0.5
    seg = closes[-window:]
    net = abs(seg[-1] - seg[0])
    total = np.sum(np.abs(np.diff(seg)))
    return net / total if total > 0 else 0.0

def compute_er_series(closes, window):
    """Her mum icin rolling ER (tek pencere, median yok)."""
    n = len(closes)
    ers = np.zeros(n)
    for i in range(window, n):
        seg = closes[i - window + 1:i + 1]
        net = abs(seg[-1] - seg[0])
        total = np.sum(np.abs(np.diff(seg)))
        ers[i] = net / total if total > 0 else 0.0
    return ers

def compute_adx_series(highs, lows, closes, period=14):
    n = len(closes)
    adx = np.full(n, 20.0)
    tr = np.zeros(n)
    pdm = np.zeros(n)
    mdm = np.zeros(n)
    for i in range(1, n):
        hd = highs[i] - highs[i-1]
        ld = lows[i-1] - lows[i]
        tr[i] = max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
        pdm[i] = hd if (hd > ld and hd > 0) else 0
        mdm[i] = ld if (ld > hd and ld > 0) else 0
    if n <= period * 2:
        return adx
    atr_ = np.zeros(n)
    dx = np.zeros(n)
    atr_[period] = np.sum(tr[1:period+1])
    sp = np.sum(pdm[1:period+1])
    sm = np.sum(mdm[1:period+1])
    for i in range(period+1, n):
        atr_[i] = atr_[i-1] - atr_[i-1]/period + tr[i]
        sp = sp - sp/period + pdm[i]
        sm = sm - sm/period + mdm[i]
        pdi = 100*sp/atr_[i] if atr_[i] > 0 else 0
        mdi = 100*sm/atr_[i] if atr_[i] > 0 else 0
        ds = pdi + mdi
        dx[i] = 100*abs(pdi-mdi)/ds if ds > 0 else 0
    adx[period*2] = np.mean(dx[period+1:period*2+1])
    for i in range(period*2+1, n):
        adx[i] = (adx[i-1]*(period-1) + dx[i])/period
    return adx

def direction_vote(closes, ema9, ema21, macd_h, rsi, idx):
    """Yon oyu: +1 LONG, -1 SHORT, 0 notr. 3 indikator oylama."""
    v = 0.0
    # EMA
    if ema9[idx] > ema21[idx] * 1.0005:
        v += 1
    elif ema9[idx] < ema21[idx] * 0.9995:
        v -= 1
    # MACD
    if macd_h[idx] > 0:
        v += 1
    elif macd_h[idx] < 0:
        v -= 1
    # RSI
    if rsi[idx] > 55:
        v += 1
    elif rsi[idx] < 45:
        v -= 1
    return v / 3.0  # -1 to +1


# ════════════════════ VERI HAZIRLAMA ════════════════════

COINS = ["BTCUSDT", "ETHUSDT", "XRPUSDT", "AVAXUSDT", "DOGEUSDT",
         "SOLUSDT", "ADAUSDT", "BNBUSDT"]

# 15m uzerinden degerlendirme
EVAL_TF = "15m"
EVAL_TF_MINUTES = 15
FUTURE_BARS = 20  # 5 saat ileri
EVAL_EVERY = 5
TREND_THRESHOLD = 1.0  # %

# Multi-TF veri: her TF icin kac mum cekecegiz
TF_CONFIG = {
    "5m":  {"limit": 1500, "minutes": 5},
    "15m": {"limit": 1500, "minutes": 15},
    "1h":  {"limit": 1000, "minutes": 60},
    "4h":  {"limit": 500,  "minutes": 240},
}

# Multi-window ER pencereleri
ER_WINDOWS = [20, 50, 100, 200]


def load_all_data():
    """Tum coinler icin tum TF verilerini cek ve indikatorleri hesapla."""
    all_data = {}
    for sym in COINS:
        print(f"  {sym}: ", end="", flush=True)
        coin = {}
        for tf, cfg in TF_CONFIG.items():
            kl = fetch_klines(sym, tf, cfg["limit"])
            if not kl or len(kl) < 200:
                print(f"{tf}=SKIP ", end="", flush=True)
                continue

            closes = np.array([float(k[4]) for k in kl])
            highs = np.array([float(k[2]) for k in kl])
            lows = np.array([float(k[3]) for k in kl])
            times = np.array([int(k[0]) for k in kl])

            # Indikatorler
            ema9 = ema_series(closes, 9)
            ema21 = ema_series(closes, 21)
            macd_h = compute_macd_hist(closes, 12, 26, 9)
            rsi = compute_rsi_series(closes, 14)
            adx = compute_adx_series(highs, lows, closes, 14)

            # Multi-window ER
            er_by_window = {}
            for w in ER_WINDOWS:
                if len(closes) >= w + 10:
                    er_by_window[w] = compute_er_series(closes, w)

            coin[tf] = {
                "closes": closes, "highs": highs, "lows": lows, "times": times,
                "ema9": ema9, "ema21": ema21, "macd_h": macd_h, "rsi": rsi,
                "adx": adx, "er": er_by_window,
            }
            print(f"{tf}={len(closes)} ", end="", flush=True)
            time.sleep(0.05)

        if EVAL_TF in coin:
            all_data[sym] = coin
        print()
        time.sleep(0.15)

    return all_data


def find_aligned_idx(target_time_ms, tf_data):
    """target_time_ms'e en yakin <= olan mum indexini bul."""
    times = tf_data["times"]
    idx = np.searchsorted(times, target_time_ms, side="right") - 1
    return max(0, min(idx, len(times) - 1))


# ════════════════════ REJIM TESPIT YONTEMLERI ════════════════════

def method_A_multiwindow_er(coin_data, eval_idx, params):
    """A: Ayni TF, farkli pencereler. ER degerlerinin agirlikli ortalamasi."""
    tf_data = coin_data[EVAL_TF]
    weights = params.get("weights", {20: 0.1, 50: 0.2, 100: 0.3, 200: 0.4})
    threshold = params.get("threshold", 0.5)

    score = 0.0
    total_w = 0.0
    for w, weight in weights.items():
        if w in tf_data["er"] and eval_idx < len(tf_data["er"][w]):
            er = tf_data["er"][w][eval_idx]
            score += er * weight
            total_w += weight

    if total_w > 0:
        score /= total_w

    return "TRENDING" if score > threshold else "RANGING", score


def method_B_multitf_er(coin_data, eval_idx, params):
    """B: Farkli TF'ler, sabit pencere. Her TF'nin ER'si agirlikli."""
    eval_time = coin_data[EVAL_TF]["times"][eval_idx]
    er_window = params.get("er_window", 50)
    tf_weights = params.get("tf_weights", {"5m": 0.15, "15m": 0.25, "1h": 0.35, "4h": 0.25})
    threshold = params.get("threshold", 0.5)

    score = 0.0
    total_w = 0.0
    for tf, weight in tf_weights.items():
        if tf not in coin_data:
            continue
        tf_data = coin_data[tf]
        idx = find_aligned_idx(eval_time, tf_data)
        if er_window in tf_data["er"] and idx < len(tf_data["er"][er_window]):
            er = tf_data["er"][er_window][idx]
            score += er * weight
            total_w += weight

    if total_w > 0:
        score /= total_w

    return "TRENDING" if score > threshold else "RANGING", score


def method_C_direction_alignment(coin_data, eval_idx, params):
    """C: Farkli TF'lerde yon uyumu. Hepsi ayni yon = TRENDING, celisme = RANGING."""
    eval_time = coin_data[EVAL_TF]["times"][eval_idx]
    threshold = params.get("threshold", 0.5)

    directions = []
    for tf in ["5m", "15m", "1h", "4h"]:
        if tf not in coin_data:
            continue
        td = coin_data[tf]
        idx = find_aligned_idx(eval_time, td)
        if idx < 30:
            continue
        d = direction_vote(td["closes"], td["ema9"], td["ema21"],
                           td["macd_h"], td["rsi"], idx)
        directions.append(d)

    if len(directions) < 2:
        return "RANGING", 0.0

    # Uyum skoru: tum yonler ayni tarafta mi?
    avg_dir = np.mean(directions)
    alignment = abs(avg_dir)  # 0 = tamamen celisik, 1 = tamamen uyumlu

    return "TRENDING" if alignment > threshold else "RANGING", alignment


def method_D_composite(coin_data, eval_idx, params):
    """D: A + B + C birlesiyor. Agirlikli kompozit trend skoru."""
    w_mw = params.get("w_multiwindow", 0.3)  # Multi-window ER agirligi
    w_mt = params.get("w_multitf", 0.3)       # Multi-TF ER agirligi
    w_da = params.get("w_alignment", 0.4)      # Yon uyumu agirligi
    threshold = params.get("threshold", 0.5)

    _, score_a = method_A_multiwindow_er(coin_data, eval_idx, params.get("A_params", {}))
    _, score_b = method_B_multitf_er(coin_data, eval_idx, params.get("B_params", {}))
    _, score_c = method_C_direction_alignment(coin_data, eval_idx, params.get("C_params", {}))

    composite = score_a * w_mw + score_b * w_mt + score_c * w_da
    return "TRENDING" if composite > threshold else "RANGING", composite


def method_E_momentum(coin_data, eval_idx, params):
    """E: Momentum bazli — fiyat degisim hizi + ivme."""
    tf_data = coin_data[EVAL_TF]
    closes = tf_data["closes"]
    threshold = params.get("threshold", 0.5)

    if eval_idx < 50:
        return "RANGING", 0.0

    # Farkli pencerelerde momentum (fiyat degisim %)
    mom_10 = abs(closes[eval_idx] - closes[eval_idx - 10]) / closes[eval_idx - 10]
    mom_20 = abs(closes[eval_idx] - closes[eval_idx - 20]) / closes[eval_idx - 20]
    mom_50 = abs(closes[eval_idx] - closes[eval_idx - 50]) / closes[eval_idx - 50]

    # Ivme: momentum artiyorsa trend gucleniyor
    if eval_idx >= 60:
        prev_mom_10 = abs(closes[eval_idx - 10] - closes[eval_idx - 20]) / closes[eval_idx - 20]
        accel = mom_10 / prev_mom_10 if prev_mom_10 > 0 else 1.0
    else:
        accel = 1.0

    # ADX
    adx = tf_data["adx"][eval_idx] / 100.0  # normalize 0-1

    # Momentum skor (0-1)
    score = (mom_10 * 30 + mom_20 * 20 + mom_50 * 10 + adx * 0.3 + min(accel / 3.0, 0.1)) / 1.0

    # Normalize
    score = min(score, 1.0)

    return "TRENDING" if score > threshold else "RANGING", score


def method_F_multitf_multiwindow(coin_data, eval_idx, params):
    """F: Multi-TF x Multi-Window tam matris. En kapsamli."""
    eval_time = coin_data[EVAL_TF]["times"][eval_idx]
    threshold = params.get("threshold", 0.5)

    # TF agirliklari (uzun TF daha agir)
    tf_weights = {"5m": 0.10, "15m": 0.20, "1h": 0.35, "4h": 0.35}
    # Window agirliklari (uzun pencere daha agir)
    win_weights = {20: 0.10, 50: 0.20, 100: 0.30, 200: 0.40}

    er_score = 0.0
    er_total_w = 0.0

    for tf, tw in tf_weights.items():
        if tf not in coin_data:
            continue
        td = coin_data[tf]
        idx = find_aligned_idx(eval_time, td)
        for w, ww in win_weights.items():
            if w in td["er"] and idx < len(td["er"][w]):
                er = td["er"][w][idx]
                combined_w = tw * ww
                er_score += er * combined_w
                er_total_w += combined_w

    if er_total_w > 0:
        er_score /= er_total_w

    # Yon uyumu bonusu
    directions = []
    for tf in ["5m", "15m", "1h", "4h"]:
        if tf not in coin_data:
            continue
        td = coin_data[tf]
        idx = find_aligned_idx(eval_time, td)
        if idx >= 30:
            d = direction_vote(td["closes"], td["ema9"], td["ema21"],
                               td["macd_h"], td["rsi"], idx)
            directions.append(d)

    alignment = abs(np.mean(directions)) if directions else 0.0

    # ADX (1h agirlikli)
    adx_score = 0.0
    for tf, tw in [("15m", 0.3), ("1h", 0.5), ("4h", 0.2)]:
        if tf in coin_data:
            idx = find_aligned_idx(eval_time, coin_data[tf])
            adx_score += coin_data[tf]["adx"][idx] / 100.0 * tw

    # Kompozit
    w_er = params.get("w_er", 0.40)
    w_align = params.get("w_align", 0.35)
    w_adx = params.get("w_adx", 0.25)

    composite = er_score * w_er + alignment * w_align + adx_score * w_adx

    return "TRENDING" if composite > threshold else "RANGING", composite


# ════════════════════ EVALUATION ════════════════════

def evaluate_method(method_fn, all_data, params, method_name=""):
    """Bir yontemi tum coinler uzerinde test et."""
    tt = tr = rt = rr = 0
    all_scores = {"TRENDING_correct": [], "TRENDING_wrong": [],
                  "RANGING_correct": [], "RANGING_wrong": []}

    for sym, coin_data in all_data.items():
        eval_tf = coin_data[EVAL_TF]
        closes = eval_tf["closes"]
        n = len(closes)
        eval_start = max(200, min(w for w in ER_WINDOWS))
        eval_end = n - FUTURE_BARS

        for i in range(eval_start, eval_end, EVAL_EVERY):
            pred, score = method_fn(coin_data, i, params)

            future = closes[i:i + FUTURE_BARS + 1]
            net_pct = abs(future[-1] - future[0]) / future[0] * 100.0
            actual = "TRENDING" if net_pct > TREND_THRESHOLD else "RANGING"

            if pred == "TRENDING":
                if actual == "TRENDING":
                    tt += 1
                    all_scores["TRENDING_correct"].append(score)
                else:
                    tr += 1
                    all_scores["TRENDING_wrong"].append(score)
            else:
                if actual == "TRENDING":
                    rt += 1
                    all_scores["RANGING_wrong"].append(score)
                else:
                    rr += 1
                    all_scores["RANGING_correct"].append(score)

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
    bal = (t_rec + r_rec) / 2.0

    return {"acc": acc, "bal": bal, "tp": t_prec, "tr": t_rec,
            "rp": r_prec, "rr_": r_rec, "tf1": t_f1, "rf1": r_f1,
            "tt": tt, "tr_": tr, "rt": rt, "rr": rr,
            "t_pred": tt + tr, "r_pred": rt + rr, "scores": all_scores}


def print_result_line(rank, name, desc, r):
    print(f"{rank:>3} | {name:22} | {desc:45} | {r['acc']:>4.1f}% | {r['bal']:>4.1f}% | "
          f"{r['tp']:>4.1f}% | {r['tr']:>4.1f}% | {r['rp']:>4.1f}% | {r['rr_']:>4.1f}% | "
          f"{r['tf1']:>5.1f} | {r['rf1']:>5.1f} | {r['t_pred']:>5} | {r['r_pred']:>5}")


def main():
    print("=" * 150)
    print("MULTI-TF MULTI-WINDOW REJIM TESPITI BACKTEST'I")
    print(f"Eval TF: {EVAL_TF} | Forward: {FUTURE_BARS} bar | Trend esigi: >{TREND_THRESHOLD}%")
    print(f"TF'ler: {list(TF_CONFIG.keys())} | ER pencereleri: {ER_WINDOWS}")
    print("=" * 150)

    all_data = load_all_data()
    print(f"\n{len(all_data)} coin yuklendi.")

    results = []

    # ═══════════ A: Multi-Window ER ═══════════
    print("\nA: Multi-Window ER...", flush=True)
    weight_configs = [
        ("esit", {20: 0.25, 50: 0.25, 100: 0.25, 200: 0.25}),
        ("uzun_agir", {20: 0.10, 50: 0.20, 100: 0.30, 200: 0.40}),
        ("kisa_agir", {20: 0.40, 50: 0.30, 100: 0.20, 200: 0.10}),
        ("orta", {20: 0.15, 50: 0.35, 100: 0.35, 200: 0.15}),
    ]
    for wname, weights in weight_configs:
        for th in [0.10, 0.15, 0.20, 0.25, 0.30, 0.35, 0.40]:
            params = {"weights": weights, "threshold": th}
            r = evaluate_method(method_A_multiwindow_er, all_data, params)
            if r:
                results.append(("A:MultiWindow-ER", f"{wname} th={th}", r))

    # ═══════════ B: Multi-TF ER ═══════════
    print("B: Multi-TF ER...", flush=True)
    tf_weight_configs = [
        ("esit", {"5m": 0.25, "15m": 0.25, "1h": 0.25, "4h": 0.25}),
        ("uzun_agir", {"5m": 0.10, "15m": 0.20, "1h": 0.35, "4h": 0.35}),
        ("kisa_agir", {"5m": 0.35, "15m": 0.35, "1h": 0.20, "4h": 0.10}),
        ("1h_agir", {"5m": 0.10, "15m": 0.15, "1h": 0.50, "4h": 0.25}),
    ]
    for twname, tw in tf_weight_configs:
        for ew in [20, 50, 100, 200]:
            for th in [0.10, 0.15, 0.20, 0.25, 0.30, 0.35]:
                params = {"tf_weights": tw, "er_window": ew, "threshold": th}
                r = evaluate_method(method_B_multitf_er, all_data, params)
                if r:
                    results.append(("B:MultiTF-ER", f"{twname} w={ew} th={th}", r))

    # ═══════════ C: Yon Uyumu ═══════════
    print("C: Yon Uyumu...", flush=True)
    for th in [0.20, 0.30, 0.40, 0.50, 0.60, 0.70, 0.80]:
        params = {"threshold": th}
        r = evaluate_method(method_C_direction_alignment, all_data, params)
        if r:
            results.append(("C:YonUyumu", f"th={th}", r))

    # ═══════════ D: Kompozit (A+B+C) ═══════════
    print("D: Kompozit...", flush=True)
    for w_mw in [0.2, 0.3, 0.4]:
        for w_mt in [0.2, 0.3, 0.4]:
            for w_da in [0.2, 0.3, 0.4]:
                if abs(w_mw + w_mt + w_da - 1.0) > 0.01:
                    continue
                for th in [0.15, 0.20, 0.25, 0.30, 0.35, 0.40]:
                    params = {
                        "w_multiwindow": w_mw, "w_multitf": w_mt, "w_alignment": w_da,
                        "threshold": th,
                        "A_params": {"weights": {20: 0.10, 50: 0.20, 100: 0.30, 200: 0.40}},
                        "B_params": {"tf_weights": {"5m": 0.10, "15m": 0.20, "1h": 0.35, "4h": 0.35},
                                     "er_window": 50},
                    }
                    r = evaluate_method(method_D_composite, all_data, params)
                    if r:
                        results.append(("D:Kompozit", f"mw={w_mw} mt={w_mt} da={w_da} th={th}", r))

    # ═══════════ E: Momentum ═══════════
    print("E: Momentum...", flush=True)
    for th in [0.02, 0.03, 0.04, 0.05, 0.06, 0.08, 0.10, 0.15, 0.20]:
        params = {"threshold": th}
        r = evaluate_method(method_E_momentum, all_data, params)
        if r:
            results.append(("E:Momentum", f"th={th}", r))

    # ═══════════ F: Full Matrix ═══════════
    print("F: Full Matrix (MTF x MW)...", flush=True)
    for w_er in [0.30, 0.40, 0.50]:
        for w_align in [0.20, 0.30, 0.40]:
            for w_adx in [0.10, 0.20, 0.30]:
                if abs(w_er + w_align + w_adx - 1.0) > 0.01:
                    continue
                for th in [0.15, 0.20, 0.25, 0.30, 0.35]:
                    params = {"w_er": w_er, "w_align": w_align, "w_adx": w_adx, "threshold": th}
                    r = evaluate_method(method_F_multitf_multiwindow, all_data, params)
                    if r:
                        results.append(("F:FullMatrix", f"er={w_er} al={w_align} adx={w_adx} th={th}", r))

    # ═══════════ Mevcut System J referans ═══════════
    print("REF: System J...", flush=True)
    def sysj_method(coin_data, eval_idx, params):
        td = coin_data[EVAL_TF]
        if 20 not in td["er"] or eval_idx >= len(td["er"][20]):
            return "RANGING", 0.0
        # Rolling ER (median of last 10)
        er_arr = td["er"][20]
        start = max(0, eval_idx - 9)
        er_median = float(np.median(er_arr[start:eval_idx + 1]))
        if er_median > 0.25:
            return "TRENDING", er_median
        if er_median < 0.08:
            return "RANGING", er_median
        # Gray zone — Hurst approximation (just use ER midpoint as proxy since Hurst is slow)
        return ("TRENDING" if er_median > 0.165 else "RANGING"), er_median

    r_sysj = evaluate_method(sysj_method, all_data, {}, "SystemJ")

    # ═══════════ SONUCLAR ═══════════
    results.sort(key=lambda x: x[2]["bal"], reverse=True)

    print(f"\n{'='*160}")
    print(f"TOP 50 — BALANCED ACCURACY SIRALAMASI")
    print(f"{'='*160}")
    print(f"{'#':>3} | {'Yontem':22} | {'Parametreler':45} | {'Acc':>5} | {'Bal':>5} | {'T-Pre':>5} | {'T-Rec':>5} | {'R-Pre':>5} | {'R-Rec':>5} | {'T-F1':>5} | {'R-F1':>5} | {'TPred':>5} | {'RPred':>5}")
    print("-" * 160)

    for rank, (method, desc, r) in enumerate(results[:50], 1):
        print_result_line(rank, method, desc, r)

    # System J referans
    print("-" * 160)
    if r_sysj:
        print_result_line(0, "REF:SystemJ", "er=0.25/0.08 w=20 (mevcut)", r_sysj)

    # Yontem bazli en iyiler
    print(f"\n{'='*160}")
    print("YONTEM BAZINDA EN IYILER")
    print(f"{'='*160}")
    seen = set()
    for method, desc, r in results:
        if method not in seen:
            seen.add(method)
            print(f"  {method:22} | {desc:45} | Bal={r['bal']:.1f}% Acc={r['acc']:.1f}% "
                  f"T-Prec={r['tp']:.1f}% T-Rec={r['tr']:.1f}% R-Prec={r['rp']:.1f}% R-Rec={r['rr_']:.1f}%")

    if r_sysj:
        print(f"\n  REF: System J mevcut   | Bal={r_sysj['bal']:.1f}% Acc={r_sysj['acc']:.1f}%")

    # En iyi vs mevcut
    if results and r_sysj:
        best = results[0]
        imp = best[2]["bal"] - r_sysj["bal"]
        print(f"\n  IYILESTIRME: {best[0]} -> Bal {best[2]['bal']:.1f}% vs SystemJ {r_sysj['bal']:.1f}% = {imp:+.1f}%")

    # Score histogram (en iyi yontem)
    if results:
        best_r = results[0][2]
        scores = best_r["scores"]
        print(f"\n{'='*80}")
        print(f"EN IYI YONTEM SKOR DAGILIMI ({results[0][0]})")
        print(f"{'='*80}")
        for label in ["TRENDING_correct", "TRENDING_wrong", "RANGING_correct", "RANGING_wrong"]:
            vals = scores[label]
            if vals:
                print(f"  {label:20}: n={len(vals):>4} mean={np.mean(vals):.3f} std={np.std(vals):.3f} "
                      f"min={np.min(vals):.3f} max={np.max(vals):.3f}")

    print()


if __name__ == "__main__":
    main()
