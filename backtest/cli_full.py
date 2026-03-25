"""System F — Tam 30 Gun Backtest (16 filtre + pozisyon simulasyonu).

Top 30 coin, 5dk aralikla kontrol, 5/5 ve 4/5 TF uyumu karsilastirmasi.
Pozisyon acilirsa: SL, trailing, emergency ile kapanisi simule eder.

NOT: Orderbook verisi gecmise donuk mevcut degil — bu filtre atlanir.
     1m hacim spike yerine 5m yakinsama kullanilir.
"""
import requests
import numpy as np
import time
import json
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass, field

# ════════════════════════ CONFIG ════════════════════════

DAYS_BACK = 30
CHECK_INTERVAL_MIN = 15   # 15dk aralik (hiz icin)
TOP_COINS = 15             # Top 15 coin (hiz icin)
LOOKBACK = 200

# System F config ayarlari (config.json ile ayni)
SF = {
    "ema_fast": 9, "ema_slow": 21,
    "ema_gap_min_pct": 0.05, "ema_gap_stale_pct": 0.02,
    "macd_fast": 8, "macd_slow": 17, "macd_signal": 9,
    "macd_momentum_required": True,
    "rsi_periyot": 14, "rsi_long_esik": 60, "rsi_short_esik": 40,
    "adx_periyot": 14, "adx_trend_esik": 20,
    "volume_ma_periyot": 20,
    "min_sinyal_gucu": 0.6,
    "vol_tf_min_count": 3, "vol_tf_threshold": 1.5,
    "max_funding_rate": 0.001,
    "swing_n": 10,
    "swing_safety_mult": 1.2, "swing_liq_mult": 2.5,
    "liq_carpani": 0.7, "max_kaldirac": 125,
    "sl_atr_mult": 1.5, "fee_rate": 0.0004,
    "emergency_liq_pct": 80,
    "swing_percentile": 90,
    "p_sl_max_pct": 10.0, "ev_min_pct": 15.0,
    "vol_spike_current_mult": 2.5, "vol_spike_avg3_mult": 2.0,
    "volume_spike_required": True,
    "min_skor": 85, "max_btc_beta": 2.0, "btc_beta_threshold": 0.5,
    "trailing_tp_callback_pct": 0.3, "software_tp_mult": 2.0,
}

DIRECTION_TFS = ["5m", "15m", "1h", "4h", "1d"]
TF_MINUTES = {"1m": 1, "5m": 5, "15m": 15, "1h": 60, "4h": 240, "1d": 1440}

# ════════════════════════ INDICATORS ════════════════════════

def ema_val(data, period):
    if len(data) < period:
        return float(np.mean(data)) if len(data) > 0 else 0.0
    k = 2.0 / (period + 1)
    e = float(data[0])
    for v in data[1:]:
        e = v * k + e * (1 - k)
    return e

def ema_series(data, period):
    if len(data) == 0:
        return np.array([0.0])
    k = 2.0 / (period + 1)
    r = np.empty(len(data))
    r[0] = float(data[0])
    for i in range(1, len(data)):
        r[i] = float(data[i]) * k + r[i-1] * (1-k)
    return r

def macd_series(closes, fast, slow):
    if len(closes) < slow:
        return np.array([0.0])
    kf, ks = 2.0/(fast+1), 2.0/(slow+1)
    ef = es = float(closes[0])
    s = []
    for v in closes:
        ef = v*kf + ef*(1-kf)
        es = v*ks + es*(1-ks)
        s.append(ef - es)
    return np.array(s)

def rsi_val(closes, period=14):
    if len(closes) < period+1:
        return 50.0
    d = np.diff(closes)
    g = np.where(d>0, d, 0.0)
    l = np.where(d<0, -d, 0.0)
    ag = np.mean(g[:period])
    al = np.mean(l[:period])
    for i in range(period, len(g)):
        ag = (ag*(period-1)+g[i])/period
        al = (al*(period-1)+l[i])/period
    if al == 0: return 100.0
    return 100.0 - 100.0/(1.0 + ag/al)

def atr_val(highs, lows, closes, period=14):
    if len(closes) < 2: return 0.0
    tr = []
    for i in range(1, len(closes)):
        tr.append(max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1])))
    if not tr: return 0.0
    if len(tr) < period: return float(np.mean(tr))
    a = float(np.mean(tr[:period]))
    for i in range(period, len(tr)):
        a = (a*(period-1)+tr[i])/period
    return a

def adx_val(highs, lows, closes, period=14):
    if len(closes) < period*2: return 0.0
    pdm, mdm, trl = [], [], []
    for i in range(1, len(closes)):
        up = highs[i]-highs[i-1]; down = lows[i-1]-lows[i]
        pdm.append(up if up>down and up>0 else 0.0)
        mdm.append(down if down>up and down>0 else 0.0)
        trl.append(max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1])))
    if len(trl) < period: return 0.0
    at = sum(trl[:period]); ps = sum(pdm[:period]); ms = sum(mdm[:period])
    dx = []
    for i in range(period, len(trl)):
        at = at - at/period + trl[i]
        ps = ps - ps/period + pdm[i]
        ms = ms - ms/period + mdm[i]
        if at == 0: continue
        pdi = 100*ps/at; mdi = 100*ms/at
        s = pdi+mdi
        if s == 0: continue
        dx.append(100*abs(pdi-mdi)/s)
    if not dx: return 0.0
    if len(dx) < period: return float(np.mean(dx))
    a = float(np.mean(dx[:period]))
    for i in range(period, len(dx)):
        a = (a*(period-1)+dx[i])/period
    return a

def detect_zigzag(highs, lows, closes, n=10):
    if len(closes) < n*2: return []
    swings = []
    i = n
    while i < len(closes) - n:
        is_h = all(highs[i]>=highs[i-j] for j in range(1,n+1))
        is_h = is_h and all(highs[i]>=highs[i+j] for j in range(1,min(n+1,len(closes)-i)))
        is_l = all(lows[i]<=lows[i-j] for j in range(1,n+1))
        is_l = is_l and all(lows[i]<=lows[i+j] for j in range(1,min(n+1,len(closes)-i)))
        if is_h and is_l:
            if swings and swings[-1][2]=='L': swings.append((i,highs[i],'H'))
            else: swings.append((i,lows[i],'L'))
        elif is_h:
            if not swings or swings[-1][2]!='H': swings.append((i,highs[i],'H'))
            elif highs[i]>swings[-1][1]: swings[-1]=(i,highs[i],'H')
        elif is_l:
            if not swings or swings[-1][2]!='L': swings.append((i,lows[i],'L'))
            elif lows[i]<swings[-1][1]: swings[-1]=(i,lows[i],'L')
        i += 1
    return swings

# ════════════════════════ TF ANALYSIS ════════════════════════

def analyze_tf(closes, volumes, highs, lows):
    """Returns (direction, confidence, vol_ratio, adx_value)."""
    if len(closes) < 30:
        return "FLAT", 0.0, 0.0, 0.0

    price = float(closes[-1])
    if price <= 0:
        return "FLAT", 0.0, 0.0, 0.0

    # EMA
    ef = ema_val(closes, SF["ema_fast"])
    es = ema_val(closes, SF["ema_slow"])
    gap = (ef - es) / price * 100
    ema_vote = 1 if gap > SF["ema_gap_min_pct"] else (-1 if gap < -SF["ema_gap_min_pct"] else 0)

    # MACD
    ms = macd_series(closes, SF["macd_fast"], SF["macd_slow"])
    ss = ema_series(ms, SF["macd_signal"])
    hs = ms - ss
    macd_vote = 0
    if len(hs) >= 3:
        h1,h2,h3 = float(hs[-3]),float(hs[-2]),float(hs[-1])
        if SF["macd_momentum_required"]:
            if h3>0 and h1<h2<h3: macd_vote = 1
            elif h3<0 and h1>h2>h3: macd_vote = -1
        else:
            macd_vote = 1 if h3>0 else (-1 if h3<0 else 0)

    # RSI
    r = rsi_val(closes, SF["rsi_periyot"])
    rsi_vote = 1 if r>SF["rsi_long_esik"] else (-1 if r<SF["rsi_short_esik"] else 0)

    # Direction
    if ema_vote>0 and macd_vote>0 and rsi_vote>0: direction = "LONG"
    elif ema_vote<0 and macd_vote<0 and rsi_vote<0: direction = "SHORT"
    else: direction = "FLAT"

    # ADX
    adx = adx_val(highs, lows, closes, SF["adx_periyot"])

    # Volume ratio
    vp = SF["volume_ma_periyot"]
    vol_ratio = 0.0
    if len(volumes) >= vp+1:
        vm = float(np.mean(volumes[-(vp+1):-1]))
        if vm > 0:
            vol_ratio = float(volumes[-1]) / vm

    # Confidence
    conf = 0.0
    if direction != "FLAT":
        ab = min(adx/50, 1.0)*0.2 if adx>20 else 0
        vb = 0.1 if vol_ratio >= SF["vol_tf_threshold"] else 0
        gb = min(abs(gap)/0.2, 1.0)*0.1
        conf = min(0.6 + ab + vb + gb, 1.0)

    return direction, conf, vol_ratio, adx


def full_analysis(klines_by_tf, min_aligned=5, funding_rate=0.0,
                  btc_direction="FLAT", btc_beta=0.8):
    """Tam System F analizi. Returns (eligible, result_dict) or (False, reject_reason)."""

    # 1. TF sinyalleri
    tf_results = []
    for tf in DIRECTION_TFS:
        kl = klines_by_tf.get(tf, [])
        if not kl or len(kl) < 30:
            continue
        c = np.array([float(k[4]) for k in kl])
        v = np.array([float(k[5]) for k in kl])
        h = np.array([float(k[2]) for k in kl])
        l = np.array([float(k[3]) for k in kl])
        d, conf, vr, adx = analyze_tf(c, v, h, l)
        tf_results.append({"tf": tf, "dir": d, "conf": conf, "vol_ratio": vr, "adx": adx})

    if len(tf_results) < min_aligned:
        return False, f"tf_data_{len(tf_results)}/{min_aligned}"

    # 2. Direction alignment (relaxed: min_aligned TF ayni yonde, gerisi FLAT olabilir)
    long_c = sum(1 for t in tf_results if t["dir"] == "LONG")
    short_c = sum(1 for t in tf_results if t["dir"] == "SHORT")
    flat_c = sum(1 for t in tf_results if t["dir"] == "FLAT")

    # En az min_aligned TF ayni yonde ve karsi yon yok
    if long_c >= min_aligned and short_c == 0:
        direction = "LONG"
        aligned = long_c
    elif short_c >= min_aligned and long_c == 0:
        direction = "SHORT"
        aligned = short_c
    else:
        best = max(long_c, short_c)
        return False, f"align_{long_c}L_{short_c}S_{flat_c}F"

    # Direction strength
    aligned_tfs = [t for t in tf_results if t["dir"] == direction]
    strength = sum(t["conf"] for t in aligned_tfs) / len(aligned_tfs) if aligned_tfs else 0
    if strength < SF["min_sinyal_gucu"]:
        return False, f"weak_{strength:.2f}"

    # 3. Volume hard filter
    vol_passing = sum(1 for t in tf_results if t["vol_ratio"] >= SF["vol_tf_threshold"])
    if vol_passing < SF["vol_tf_min_count"]:
        return False, f"vol_filter_{vol_passing}/{SF['vol_tf_min_count']}"

    # 4. Funding rate
    max_fr = SF["max_funding_rate"]
    if funding_rate > max_fr and direction == "LONG":
        return False, "high_fr_long"
    if funding_rate < -max_fr and direction == "SHORT":
        return False, "high_fr_short"

    # 5. ATR & Price (5m)
    kl5 = klines_by_tf.get("5m", [])
    if not kl5 or len(kl5) < 30:
        return False, "no_5m"
    c5 = np.array([float(k[4]) for k in kl5])
    h5 = np.array([float(k[2]) for k in kl5])
    l5 = np.array([float(k[3]) for k in kl5])
    price = float(c5[-1])
    atr = atr_val(h5, l5, c5, 14)
    atr_pct = (atr / price * 100) if price > 0 else 0
    if atr_pct <= 0:
        return False, "zero_atr"

    # 6. Orderbook — SKIP (gecmis veri yok)
    # 7. BTC yon uyumu
    if abs(btc_beta) > SF["btc_beta_threshold"] and btc_direction not in ("FLAT", ""):
        if direction != btc_direction:
            return False, f"btc_{btc_direction}"

    # 8. Swing analizi (15m birincil, 5m yedek)
    swing_n = SF["swing_n"]
    for stf in ["15m", "5m"]:
        skl = klines_by_tf.get(stf, [])
        if not skl or len(skl) < swing_n*2+10:
            continue
        sh = [float(k[2]) for k in skl]
        sl_list = [float(k[3]) for k in skl]
        sc = [float(k[4]) for k in skl]
        swings = detect_zigzag(sh, sl_list, sc, swing_n)
        if len(swings) < 3:
            continue

        fwd_pcts, ret_pcts = [], []
        for i in range(1, len(swings)):
            pt, ct = swings[i-1][2], swings[i][2]
            wp = abs(swings[i][1] - swings[i-1][1]) / price * 100
            if direction == "LONG":
                if pt=='L' and ct=='H': fwd_pcts.append(wp)
                elif pt=='H' and ct=='L': ret_pcts.append(wp)
            else:
                if pt=='H' and ct=='L': fwd_pcts.append(wp)
                elif pt=='L' and ct=='H': ret_pcts.append(wp)

        if len(fwd_pcts) >= 3 and len(ret_pcts) >= 3:
            avg_fwd = sum(fwd_pcts)/len(fwd_pcts)
            avg_ret = sum(ret_pcts)/len(ret_pcts)
            sorted_ret = sorted(ret_pcts)
            p90_ret = sorted_ret[min(int(len(sorted_ret)*0.9), len(sorted_ret)-1)]
            swing_ok = True
            break
    else:
        return False, "no_swings"

    if not swing_ok:
        return False, "no_swings"

    # 9. Akilli kaldirac
    atr_sl = SF["sl_atr_mult"] * atr_pct
    swing_sl = p90_ret * SF["swing_safety_mult"]
    base_sl = max(atr_sl, swing_sl)
    fee_pct = SF["fee_rate"] * 200
    sl_pct = base_sl + fee_pct

    liq_dist = sl_pct * SF["swing_liq_mult"]
    if liq_dist > 0:
        smart_lev = int((SF["liq_carpani"] * 100) / liq_dist)
    else:
        smart_lev = 1
    smart_lev = max(2, min(smart_lev, SF["max_kaldirac"]))

    # Emergency SL
    real_liq = (1.0/smart_lev) * SF["liq_carpani"] * 100
    emergency_pct = real_liq * (SF["emergency_liq_pct"]/100.0)

    # 10. Dinamik TP + Trailing
    dynamic_tp_pct = avg_fwd
    dynamic_tp_roi = avg_fwd * smart_lev
    trailing_trigger = avg_fwd   # fiyat %
    trailing_callback = max(0.1, min(SF["trailing_tp_callback_pct"], 1.0))
    target_roi = avg_fwd * SF["software_tp_mult"] * smart_lev

    # 11. Fee
    fee_roi = round(SF["fee_rate"] * 200 * smart_lev, 2)

    # 12. P(win), P(SL), EV
    tp_hits = sum(1 for f in fwd_pcts if f >= dynamic_tp_pct)
    p_fwd = tp_hits / len(fwd_pcts)
    sl_hits = sum(1 for r in ret_pcts if r >= sl_pct)
    p_ret = sl_hits / len(ret_pcts)

    p_win_c = p_fwd
    p_loss_c = (1 - p_fwd) * p_ret
    denom = p_win_c + p_loss_c
    if denom > 0:
        p_win = p_win_c / denom
        p_loss = p_loss_c / denom
    else:
        p_win, p_loss = 0.3, 0.3

    tp_roi_net = dynamic_tp_roi - fee_roi
    sl_roi_net = sl_pct * smart_lev + fee_roi
    ev_pct = round(p_win * tp_roi_net - p_loss * sl_roi_net, 2)

    if p_loss > SF["p_sl_max_pct"]/100:
        return False, f"p_sl_{p_loss*100:.0f}%|ev={ev_pct:.1f}%"

    if ev_pct < SF["ev_min_pct"]:
        return False, f"ev_{ev_pct:.1f}%|p_w={p_win*100:.0f}%"

    # 13. Volume spike (5m proxy — 1m yok)
    if SF["volume_spike_required"]:
        vols = np.array([float(k[5]) for k in kl5])
        vma_p = SF["volume_ma_periyot"]
        spike = False
        vol_ratio_1m = 0.0
        if len(vols) >= vma_p + 3:
            vma = float(np.mean(vols[-(vma_p+3):-3]))
            if vma > 0:
                vc = float(vols[-1])
                va3 = float(np.mean(vols[-3:]))
                vol_ratio_1m = vc / vma
                # 5m proxy: esikleri biraz dusur (5m mumlari 1m'den buyuk)
                spike = (vol_ratio_1m >= 2.0) and (va3/vma >= 1.5)
                # Climax filtresi
                if spike:
                    co, cc = float(kl5[-1][1]), float(kl5[-1][4])
                    if direction == "LONG" and cc <= co: spike = False
                    elif direction == "SHORT" and cc >= co: spike = False
        if not spike:
            return False, f"no_spike_{vol_ratio_1m:.1f}x"

    # 14. Composite score
    score = 0.0
    score += strength * 35.0  # direction strength
    if ev_pct > 0: score += min(ev_pct/50, 1.0) * 25.0  # EV
    score += min(p_win, 1.0) * 20.0  # P(win)
    # Volume momentum
    avg_vol_ratio = sum(t["vol_ratio"] for t in tf_results) / len(tf_results)
    score += min(avg_vol_ratio / 5.0, 1.0) * 10.0
    # ADX
    adx_1h = next((t["adx"] for t in tf_results if t["tf"]=="1h"), 0)
    if adx_1h > SF["adx_trend_esik"]:
        score += min((adx_1h - SF["adx_trend_esik"])/30, 1.0) * 5.0
    # FR avantaj
    if direction=="LONG" and funding_rate<=0: score += 5.0
    elif direction=="SHORT" and funding_rate>=0: score += 5.0
    elif abs(funding_rate)<0.0003: score += 2.5
    score = round(min(score, 100), 1)

    if score < SF["min_skor"]:
        return False, f"score_{score:.0f}"

    # 15. BTC beta asiri
    if abs(btc_beta) > SF["max_btc_beta"]:
        return False, f"beta_{btc_beta:.1f}"

    # ELIGIBLE!
    return True, {
        "direction": direction, "aligned": aligned, "price": price,
        "sl_pct": sl_pct, "smart_lev": smart_lev, "emergency_pct": emergency_pct,
        "trailing_trigger": trailing_trigger, "trailing_callback": trailing_callback,
        "dynamic_tp_pct": dynamic_tp_pct, "dynamic_tp_roi": dynamic_tp_roi,
        "target_roi": target_roi, "fee_roi": fee_roi,
        "ev_pct": ev_pct, "p_win": p_win*100, "p_loss": p_loss*100,
        "score": score, "strength": strength, "atr_pct": atr_pct,
        "avg_fwd": avg_fwd, "avg_ret": avg_ret, "p90_ret": p90_ret,
        "tf_details": tf_results,
    }


# ════════════════════════ POSITION SIMULATION ════════════════════════

def simulate_position(direction, entry_price, sl_pct, emergency_pct,
                      trailing_trigger_pct, trailing_callback_pct,
                      smart_lev, fee_rate, forward_klines_5m):
    """5m mumlarini ileri sararak pozisyonu simule et.

    Returns: (exit_reason, exit_price, bars_held, roi_pct, pnl_pct)
    """
    if not forward_klines_5m:
        return "no_data", entry_price, 0, 0, 0

    # SL & emergency fiyatlari
    if direction == "LONG":
        sl_price = entry_price * (1 - sl_pct/100)
        emg_price = entry_price * (1 - emergency_pct/100)
    else:
        sl_price = entry_price * (1 + sl_pct/100)
        emg_price = entry_price * (1 + emergency_pct/100)

    trailing_active = False
    peak_price = entry_price
    max_bars = len(forward_klines_5m)
    time_limit_bars = 8 * 12  # 8 saat = 96 bar (5m)

    for i, k in enumerate(forward_klines_5m):
        high = float(k[2])
        low = float(k[3])
        close = float(k[4])

        if direction == "LONG":
            # Emergency SL
            if low <= emg_price:
                exit_p = emg_price
                pnl = (exit_p - entry_price) / entry_price * 100 * smart_lev
                fee = fee_rate * 200 * smart_lev
                return "EMERGENCY", exit_p, i+1, pnl-fee, pnl

            # SL
            if low <= sl_price:
                exit_p = sl_price
                pnl = (exit_p - entry_price) / entry_price * 100 * smart_lev
                fee = fee_rate * 200 * smart_lev
                return "SL", exit_p, i+1, pnl-fee, pnl

            # Peak track
            if high > peak_price:
                peak_price = high

            # Trailing activation
            move_pct = (peak_price - entry_price) / entry_price * 100
            if move_pct >= trailing_trigger_pct:
                trailing_active = True

            # Trailing exit
            if trailing_active:
                retrace_pct = (peak_price - low) / peak_price * 100
                if retrace_pct >= trailing_callback_pct:
                    # Trailing callback'den cik
                    exit_p = peak_price * (1 - trailing_callback_pct/100)
                    pnl = (exit_p - entry_price) / entry_price * 100 * smart_lev
                    fee = fee_rate * 200 * smart_lev
                    return "TRAILING", exit_p, i+1, pnl-fee, pnl

        else:  # SHORT
            if high >= emg_price:
                exit_p = emg_price
                pnl = (entry_price - exit_p) / entry_price * 100 * smart_lev
                fee = fee_rate * 200 * smart_lev
                return "EMERGENCY", exit_p, i+1, pnl-fee, pnl

            if high >= sl_price:
                exit_p = sl_price
                pnl = (entry_price - exit_p) / entry_price * 100 * smart_lev
                fee = fee_rate * 200 * smart_lev
                return "SL", exit_p, i+1, pnl-fee, pnl

            if low < peak_price:
                peak_price = low

            move_pct = (entry_price - peak_price) / entry_price * 100
            if move_pct >= trailing_trigger_pct:
                trailing_active = True

            if trailing_active:
                retrace_pct = (high - peak_price) / peak_price * 100
                if retrace_pct >= trailing_callback_pct:
                    exit_p = peak_price * (1 + trailing_callback_pct/100)
                    pnl = (entry_price - exit_p) / entry_price * 100 * smart_lev
                    fee = fee_rate * 200 * smart_lev
                    return "TRAILING", exit_p, i+1, pnl-fee, pnl

        # Time limit (8h)
        if i+1 >= time_limit_bars:
            pnl = ((close - entry_price)/entry_price*100*smart_lev
                    if direction == "LONG"
                    else (entry_price - close)/entry_price*100*smart_lev)
            fee = fee_rate * 200 * smart_lev
            return "TIME_LIMIT", close, i+1, pnl-fee, pnl

    # Data bitti
    close = float(forward_klines_5m[-1][4])
    pnl = ((close - entry_price)/entry_price*100*smart_lev
            if direction == "LONG"
            else (entry_price - close)/entry_price*100*smart_lev)
    fee = fee_rate * 200 * smart_lev
    return "DATA_END", close, len(forward_klines_5m), pnl-fee, pnl


# ════════════════════════ BINANCE API ════════════════════════

def fetch_klines(symbol, interval, start_ms, end_ms, limit=1500):
    all_kl = []
    cursor = start_ms
    while cursor < end_ms:
        params = {"symbol": symbol, "interval": interval,
                  "startTime": int(cursor), "endTime": int(end_ms), "limit": limit}
        for attempt in range(5):
            try:
                resp = requests.get("https://fapi.binance.com/fapi/v1/klines",
                                    params=params, timeout=20)
                resp.raise_for_status()
                data = resp.json()
                break
            except (requests.ConnectionError, requests.Timeout):
                if attempt < 4:
                    time.sleep(2 * (attempt + 1))
                else:
                    data = []
        if not data: break
        all_kl.extend(data)
        cursor = int(data[-1][0]) + 1
        if len(data) < limit: break
        time.sleep(0.15)
    return all_kl

def get_top_symbols(n=30):
    """Top N coin by 24h volume."""
    for attempt in range(5):
        try:
            resp = requests.get("https://fapi.binance.com/fapi/v1/ticker/24hr", timeout=20)
            resp.raise_for_status()
            break
        except (requests.ConnectionError, requests.Timeout):
            if attempt < 4: time.sleep(3)
            else: raise
    tickers = resp.json()
    usdt = [t for t in tickers if t["symbol"].endswith("USDT")
            and not any(x in t["symbol"] for x in ["_", "BTCDOM", "DEFI"])]
    usdt.sort(key=lambda t: float(t["quoteVolume"]), reverse=True)
    return [t["symbol"] for t in usdt[:n]]

def get_btc_direction(klines_1h):
    """BTC yonunu 1h'den hesapla."""
    if not klines_1h or len(klines_1h) < 30:
        return "FLAT"
    c = np.array([float(k[4]) for k in klines_1h[-LOOKBACK:]])
    d, _, _, _ = analyze_tf(c,
                            np.array([float(k[5]) for k in klines_1h[-LOOKBACK:]]),
                            np.array([float(k[2]) for k in klines_1h[-LOOKBACK:]]),
                            np.array([float(k[3]) for k in klines_1h[-LOOKBACK:]]))
    return d


# ════════════════════════ MAIN BACKTEST ════════════════════════

def run():
    now = datetime.now(timezone.utc)
    end_ms = int(now.timestamp() * 1000)
    start_ms = int((now - timedelta(days=DAYS_BACK)).timestamp() * 1000)
    warmup_ms = LOOKBACK * 1440 * 60 * 1000  # en buyuk TF (1d) icin warmup

    print(f"=== System F Full Backtest ({DAYS_BACK} gun) ===")
    print(f"Tarih: {now - timedelta(days=DAYS_BACK):%Y-%m-%d} -> {now:%Y-%m-%d}")
    print(f"Kontrol araligi: {CHECK_INTERVAL_MIN} dk")
    print()

    # 1. Top coins
    print("Top coinler aliniyor...")
    symbols = get_top_symbols(TOP_COINS)
    # BTC her zaman dahil
    if "BTCUSDT" not in symbols:
        symbols.insert(0, "BTCUSDT")
    print(f"  {len(symbols)} coin: {', '.join(symbols[:10])}...")
    print()

    # 2. Veri cek (her coin x her TF)
    print("Veri cekiliyor (bu 3-5 dk surebilir)...")
    all_data = {}  # symbol -> tf -> [klines]
    tfs_to_fetch = ["5m", "15m", "1h", "4h", "1d"]

    for si, sym in enumerate(symbols):
        all_data[sym] = {}
        for tf in tfs_to_fetch:
            tf_min = TF_MINUTES[tf]
            warmup = LOOKBACK * tf_min * 60 * 1000
            kl = fetch_klines(sym, tf, start_ms - warmup, end_ms)
            all_data[sym][tf] = kl
            time.sleep(0.08)
        pct = (si+1)/len(symbols)*100
        candle_counts = ", ".join(f"{tf}:{len(all_data[sym][tf])}" for tf in tfs_to_fetch)
        print(f"  [{si+1}/{len(symbols)}] {sym:>12} ({pct:.0f}%) - {candle_counts}")

    print(f"\nVeri tamamlandi. Analiz basliyor...\n")

    # 3. BTC 1h'den zaman bazli yon tablosu
    btc_1h = all_data.get("BTCUSDT", {}).get("1h", [])
    btc_dir_by_ts = {}
    if btc_1h:
        for i in range(LOOKBACK, len(btc_1h)):
            window = btc_1h[max(0,i-LOOKBACK):i]
            c = np.array([float(k[4]) for k in window])
            v = np.array([float(k[5]) for k in window])
            h = np.array([float(k[2]) for k in window])
            l = np.array([float(k[3]) for k in window])
            d, _, _, _ = analyze_tf(c, v, h, l)
            ts = int(btc_1h[i][0])
            btc_dir_by_ts[ts] = d

    btc_dir_ts_sorted = sorted(btc_dir_by_ts.keys())

    def get_btc_dir_at(check_ts):
        idx = bisect.bisect_right(btc_dir_ts_sorted, check_ts) - 1
        if idx >= 0:
            return btc_dir_by_ts[btc_dir_ts_sorted[idx]]
        return "FLAT"

    # 4. Pre-index: her coin/tf icin timestamp dizisi (binary search icin)
    import bisect
    sym_tf_ts = {}  # (sym, tf) -> sorted timestamp list
    sym_tf_idx = {}  # (sym, tf) -> {ts: index_in_full_kl}
    for sym in symbols:
        for tf in tfs_to_fetch:
            kl = all_data[sym].get(tf, [])
            ts_list = [int(k[0]) for k in kl]
            sym_tf_ts[(sym, tf)] = ts_list

    # 5. Rolling window analiz
    check_ms = CHECK_INTERVAL_MIN * 60 * 1000
    check_time = start_ms
    total_checks = 0

    signals = {4: [], 5: []}
    reject_stats = {4: {}, 5: {}}

    while check_time <= end_ms:
        total_checks += 1
        btc_dir = get_btc_dir_at(check_time)

        for sym in symbols:
            # Klines window'u bir kere hesapla, 4 ve 5 icin paylas
            klines_window = {}
            skip = False
            for tf in tfs_to_fetch:
                ts_list = sym_tf_ts.get((sym, tf), [])
                # Binary search: check_time'dan kucuk olan son index
                idx = bisect.bisect_left(ts_list, check_time)
                if idx < LOOKBACK:
                    skip = True
                    break
                full_kl = all_data[sym][tf]
                klines_window[tf] = full_kl[idx-LOOKBACK:idx]

            if skip:
                continue

            for min_al in [4, 5]:
                eligible, result = full_analysis(
                    klines_window, min_aligned=min_al,
                    funding_rate=0.0,
                    btc_direction=btc_dir, btc_beta=0.8)

                if eligible:
                    signals[min_al].append({
                        "time": check_time,
                        "symbol": sym,
                        **result,
                    })
                else:
                    reason = result.split("|")[0] if isinstance(result, str) else "unknown"
                    rkey = reason.split("_")[0] if "_" in reason else reason
                    reject_stats[min_al][rkey] = reject_stats[min_al].get(rkey, 0) + 1

        if total_checks % 200 == 0:
            elapsed_days = (check_time - start_ms) / (86400*1000)
            print(f"  ... {elapsed_days:.0f}/{DAYS_BACK} gun tarandi "
                  f"(4/5: {len(signals[4])} sinyal, 5/5: {len(signals[5])} sinyal)",
                  flush=True)

        check_time += check_ms

    print(f"\nTarama tamamlandi: {total_checks} kontrol noktasi\n")

    # 5. Pozisyon simulasyonu
    print("=" * 70)
    for min_al in [5, 4]:
        sigs = signals[min_al]
        print(f"\n{'='*70}")
        print(f"  {min_al}/5 TF UYUMU — {len(sigs)} sinyal bulundu")
        print(f"{'='*70}")

        if not sigs:
            # Reject dagilimi
            rs = reject_stats[min_al]
            if rs:
                print(f"\n  Red sebepleri (ilk filtreler):")
                for reason, count in sorted(rs.items(), key=lambda x: -x[1])[:10]:
                    print(f"    {reason:<20} {count:>8} kez")
            continue

        # Her sinyal icin pozisyon simule et
        trades = []
        for sig in sigs:
            sym = sig["symbol"]
            entry_time = sig["time"]
            entry_price = sig["price"]

            # Giris zamanindan sonraki 5m mumlari
            fwd_klines = [k for k in all_data[sym].get("5m", [])
                          if int(k[0]) >= entry_time]

            exit_reason, exit_price, bars, roi_net, roi_gross = simulate_position(
                sig["direction"], entry_price, sig["sl_pct"], sig["emergency_pct"],
                sig["trailing_trigger"], sig["trailing_callback"],
                sig["smart_lev"], SF["fee_rate"], fwd_klines)

            hold_min = bars * 5
            hold_str = f"{hold_min//60}s {hold_min%60}dk" if hold_min >= 60 else f"{hold_min}dk"

            trades.append({
                "time": entry_time, "symbol": sym, "dir": sig["direction"],
                "entry": entry_price, "exit": exit_price,
                "lev": sig["smart_lev"], "sl_pct": sig["sl_pct"],
                "exit_reason": exit_reason, "bars": bars,
                "hold_str": hold_str, "roi_net": roi_net,
                "score": sig["score"], "ev": sig["ev_pct"],
                "p_win": sig["p_win"], "strength": sig["strength"],
                "avg_fwd": sig["avg_fwd"], "avg_ret": sig["avg_ret"],
            })

        # Sonuc tablosu
        print(f"\n  {'Tarih':>16} {'Coin':>10} {'Yon':>5} {'Lev':>4} "
              f"{'Giris':>10} {'Cikis':>10} {'Sebep':>10} {'Sure':>8} "
              f"{'ROI%':>8} {'Skor':>5} {'EV%':>6}")
        print(f"  {'-'*110}")

        total_roi = 0
        wins = 0
        for t in trades:
            dt = datetime.fromtimestamp(t["time"]/1000, tz=timezone.utc)
            w = "+" if t["roi_net"] > 0 else ""
            print(f"  {dt:%Y-%m-%d %H:%M} {t['symbol']:>10} {t['dir']:>5} "
                  f"{t['lev']:>4}x {t['entry']:>10.2f} {t['exit']:>10.2f} "
                  f"{t['exit_reason']:>10} {t['hold_str']:>8} "
                  f"{w}{t['roi_net']:>7.1f}% {t['score']:>5.0f} {t['ev']:>5.1f}%")
            total_roi += t["roi_net"]
            if t["roi_net"] > 0: wins += 1

        print(f"  {'-'*110}")
        print(f"  Toplam: {len(trades)} trade, {wins} kazanc, "
              f"{len(trades)-wins} kayip, "
              f"Toplam ROI: {total_roi:+.1f}%, "
              f"Ort ROI: {total_roi/len(trades):+.1f}%")

        # Detayli analiz
        print(f"\n  Sinyal detaylari:")
        for t in trades:
            dt = datetime.fromtimestamp(t["time"]/1000, tz=timezone.utc)
            print(f"    {dt:%m-%d %H:%M} {t['symbol']:>10} {t['dir']:>5} "
                  f"| Strength:{t['strength']:.2f} P(win):{t['p_win']:.0f}% "
                  f"EV:{t['ev']:.1f}% AvgFwd:{t['avg_fwd']:.2f}% "
                  f"AvgRet:{t['avg_ret']:.2f}% SL:{t['sl_pct']:.2f}%")

    # 6. Reject dagilimi (4/5)
    print(f"\n{'='*70}")
    print(f"  4/5 Red Sebepleri Dagilimi (hangi filtre engelliyor?):")
    print(f"{'='*70}")
    rs = reject_stats[4]
    total_rejects = sum(rs.values())
    if rs:
        for reason, count in sorted(rs.items(), key=lambda x: -x[1])[:15]:
            pct = count / total_rejects * 100 if total_rejects > 0 else 0
            bar = "#" * int(pct / 2)
            print(f"  {reason:<20} {count:>8} ({pct:>5.1f}%) {bar}")

    print(f"\n{'='*70}")
    print(f"NOT: Orderbook filtresi atlanmistir (gecmis veri yok).")
    print(f"     Funding rate 0 varsayilmistir (gecmis FR verisi cekilmemistir).")
    print(f"     Hacim spike 5m proxy kullanmistir (1m yerine).")
    print(f"{'='*70}")


if __name__ == "__main__":
    run()
