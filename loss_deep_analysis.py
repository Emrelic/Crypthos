"""System N - Zarar eden pozisyonlarin derin analizi.
Her zarar islem icin: entry anindaki indikatorler, fiyat hareketi, neden kaybetti."""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

from dotenv import load_dotenv
import os, time, hmac, hashlib, requests, json, math
import numpy as np
from urllib.parse import urlencode
from datetime import datetime
from collections import defaultdict

load_dotenv()
key = os.getenv("BINANCE_API_KEY")
secret = os.getenv("BINANCE_API_SECRET")
session = requests.Session()
session.headers["X-MBX-APIKEY"] = key

def sign(params):
    params["timestamp"] = int(time.time() * 1000)
    qs = urlencode(params)
    sig = hmac.new(secret.encode(), qs.encode(), hashlib.sha256).hexdigest()
    params["signature"] = sig
    return params

base = "https://fapi.binance.com"

# ===================== INDICATOR FUNCTIONS =====================
def calc_rma(values, period):
    """Wilder's RMA (recursive moving average)."""
    result = [0.0] * len(values)
    if len(values) < period:
        return result
    result[period - 1] = sum(values[:period]) / period
    alpha = 1.0 / period
    for i in range(period, len(values)):
        result[i] = alpha * values[i] + (1 - alpha) * result[i - 1]
    return result

def calc_sma(values, period):
    result = [0.0] * len(values)
    for i in range(period - 1, len(values)):
        result[i] = sum(values[i - period + 1:i + 1]) / period
    return result

def calc_rsi(closes, period=14):
    if len(closes) < period + 1:
        return [50.0] * len(closes)
    gains = [0.0]
    losses = [0.0]
    for i in range(1, len(closes)):
        d = closes[i] - closes[i - 1]
        gains.append(max(d, 0))
        losses.append(max(-d, 0))
    avg_gain = calc_rma(gains, period)
    avg_loss = calc_rma(losses, period)
    rsi = []
    for i in range(len(closes)):
        if avg_loss[i] == 0:
            rsi.append(100.0 if avg_gain[i] > 0 else 50.0)
        else:
            rs = avg_gain[i] / avg_loss[i]
            rsi.append(100.0 - 100.0 / (1.0 + rs))
    return rsi

def calc_adx(highs, lows, closes, period=14):
    n = len(closes)
    if n < period * 2:
        return [0.0] * n, [0.0] * n, [0.0] * n
    tr_list = [0.0]
    plus_dm = [0.0]
    minus_dm = [0.0]
    for i in range(1, n):
        hl = highs[i] - lows[i]
        hc = abs(highs[i] - closes[i - 1])
        lc = abs(lows[i] - closes[i - 1])
        tr_list.append(max(hl, hc, lc))
        up = highs[i] - highs[i - 1]
        down = lows[i - 1] - lows[i]
        plus_dm.append(up if up > down and up > 0 else 0)
        minus_dm.append(down if down > up and down > 0 else 0)
    atr_vals = calc_rma(tr_list, period)
    smooth_plus = calc_rma(plus_dm, period)
    smooth_minus = calc_rma(minus_dm, period)
    plus_di = [0.0] * n
    minus_di = [0.0] * n
    dx = [0.0] * n
    for i in range(n):
        if atr_vals[i] > 0:
            plus_di[i] = 100.0 * smooth_plus[i] / atr_vals[i]
            minus_di[i] = 100.0 * smooth_minus[i] / atr_vals[i]
        s = plus_di[i] + minus_di[i]
        if s > 0:
            dx[i] = 100.0 * abs(plus_di[i] - minus_di[i]) / s
    adx = calc_rma(dx, period)
    return adx, plus_di, minus_di

def calc_mfi(highs, lows, closes, volumes, period=14):
    n = len(closes)
    if n < period + 1:
        return [50.0] * n
    tp = [(highs[i] + lows[i] + closes[i]) / 3.0 for i in range(n)]
    mf = [tp[i] * volumes[i] for i in range(n)]
    pos_mf = [0.0] * n
    neg_mf = [0.0] * n
    for i in range(1, n):
        if tp[i] > tp[i - 1]:
            pos_mf[i] = mf[i]
        elif tp[i] < tp[i - 1]:
            neg_mf[i] = mf[i]
    result = [50.0] * n
    for i in range(period, n):
        p = sum(pos_mf[i - period + 1:i + 1])
        ne = sum(neg_mf[i - period + 1:i + 1])
        if ne > 0:
            ratio = p / ne
            result[i] = 100.0 - 100.0 / (1.0 + ratio)
        elif p > 0:
            result[i] = 100.0
        else:
            result[i] = 50.0
    return result

def calc_alphatrend(highs, lows, closes, volumes, coeff=3.6, period=27):
    n = len(closes)
    tr_list = [highs[0] - lows[0]]
    for i in range(1, n):
        hl = highs[i] - lows[i]
        hc = abs(highs[i] - closes[i - 1])
        lc = abs(lows[i] - closes[i - 1])
        tr_list.append(max(hl, hc, lc))
    atr = calc_sma(tr_list, period)
    mfi = calc_mfi(highs, lows, closes, volumes, period)

    at = [0.0] * n
    up_t = [0.0] * n
    down_t = [0.0] * n
    for i in range(n):
        up_t[i] = lows[i] - atr[i] * coeff
        down_t[i] = highs[i] + atr[i] * coeff

    for i in range(1, n):
        if mfi[i] >= 50:
            at[i] = max(up_t[i], at[i - 1])
        else:
            at[i] = min(down_t[i], at[i - 1])

    return at, atr, mfi

def get_klines(symbol, interval="5m", limit=300):
    url = f"{base}/fapi/v1/klines"
    params = {"symbol": symbol, "interval": interval, "limit": limit}
    r = session.get(url, params=params)
    data = r.json()
    if not isinstance(data, list):
        return None
    opens, highs, lows, closes, volumes, times = [], [], [], [], [], []
    for k in data:
        times.append(k[0])
        opens.append(float(k[1]))
        highs.append(float(k[2]))
        lows.append(float(k[3]))
        closes.append(float(k[4]))
        volumes.append(float(k[5]))
    return {"time": times, "open": opens, "high": highs, "low": lows,
            "close": closes, "volume": volumes}

# ===================== TRADE HISTORY =====================
start_ms = int((time.time() - 72 * 3600) * 1000)

all_data = []
cursor = start_ms
for _ in range(10):
    p = sign({"incomeType": "", "limit": 1000, "startTime": cursor})
    r = session.get(f"{base}/fapi/v1/income", params=p)
    page = r.json()
    if not page:
        break
    all_data.extend(page)
    if len(page) < 1000:
        break
    cursor = page[-1]["time"] + 1

events = []
for item in all_data:
    events.append({"time": item.get("time", 0), "type": item.get("incomeType", "?"),
                   "amount": float(item.get("income", 0)), "symbol": item.get("symbol", "")})

symbol_events = defaultdict(list)
for e in events:
    if e["type"] in ("REALIZED_PNL", "COMMISSION", "INSURANCE_CLEAR"):
        symbol_events[e["symbol"]].append(e)

all_trades = []
for sym, evts in symbol_events.items():
    evts.sort(key=lambda x: x["time"])
    grp = []
    for e in evts:
        if grp and e["time"] - grp[-1]["time"] > 300000:
            pnl = sum(x["amount"] for x in grp if x["type"] == "REALIZED_PNL")
            fee = sum(x["amount"] for x in grp if x["type"] == "COMMISSION")
            liq = sum(x["amount"] for x in grp if x["type"] == "INSURANCE_CLEAR")
            if pnl != 0 or liq != 0:
                all_trades.append({"time": grp[0]["time"], "symbol": sym, "pnl": pnl,
                                   "fee": fee, "liq": liq, "net": pnl + fee + liq,
                                   "is_liq": liq < 0})
            grp = [e]
        else:
            grp.append(e)
    if grp:
        pnl = sum(x["amount"] for x in grp if x["type"] == "REALIZED_PNL")
        fee = sum(x["amount"] for x in grp if x["type"] == "COMMISSION")
        liq = sum(x["amount"] for x in grp if x["type"] == "INSURANCE_CLEAR")
        if pnl != 0 or liq != 0:
            all_trades.append({"time": grp[0]["time"], "symbol": sym, "pnl": pnl,
                               "fee": fee, "liq": liq, "net": pnl + fee + liq,
                               "is_liq": liq < 0})

all_trades.sort(key=lambda x: x["time"])
loss_trades = [t for t in all_trades if t["net"] < 0]

# Get user trades to find entry prices and directions
trade_details = {}
for sym in set(t["symbol"] for t in loss_trades):
    trades = []
    cur = start_ms
    for _ in range(3):
        p = sign({"symbol": sym, "startTime": cur, "limit": 500})
        r = session.get(f"{base}/fapi/v1/userTrades", params=p)
        data = r.json()
        if not data:
            break
        trades.extend(data)
        if len(data) < 500:
            break
        cur = data[-1]["time"] + 1
    trade_details[sym] = sorted(trades, key=lambda x: x["time"])

# Load optimize cache for coin params
opt_cache = {}
try:
    with open("data/system_n_optimize.json") as f:
        opt_cache = json.load(f)
except:
    pass

# ===================== ANALYZE EACH LOSS =====================
print("=" * 100)
print("SYSTEM N - ZARAR EDEN POZISYONLARIN DERIN ANALIZI (Son 72 saat)")
print("=" * 100)
print(f"Toplam zarar islem: {len(loss_trades)}")
print()

# Categories
categories = defaultdict(list)

for idx, lt in enumerate(loss_trades):
    sym = lt["symbol"]
    close_time = lt["time"]
    dt_str = datetime.fromtimestamp(close_time / 1000).strftime("%m-%d %H:%M")

    # Find entry info from user trades
    ut = trade_details.get(sym, [])
    # Find the closing trade
    close_trade = None
    entry_trade = None
    for t in ut:
        if abs(t["time"] - close_time) < 120000:  # within 2 min of close
            close_trade = t
            break

    # Determine direction from close trade
    if close_trade:
        # If close is SELL + reduceOnly, was LONG. If BUY + reduceOnly, was SHORT
        close_side = close_trade["side"]
        was_long = (close_side == "SELL")
        direction = "LONG" if was_long else "SHORT"
        exit_price = float(close_trade["price"])

        # Find entry: look back for opposite side before close
        for t in reversed(ut):
            if t["time"] < close_time:
                if was_long and t["side"] == "BUY":
                    entry_trade = t
                    break
                elif not was_long and t["side"] == "SELL":
                    entry_trade = t
                    break
    else:
        direction = "?"
        exit_price = 0

    entry_price = float(entry_trade["price"]) if entry_trade else 0
    entry_time = entry_trade["time"] if entry_trade else close_time - 3600000
    hold_minutes = (close_time - entry_time) / 60000 if entry_trade else 0

    # Coin optimize params
    coin_opt = opt_cache.get(sym, {})
    coin_params = coin_opt.get("params", {})
    g_analysis = coin_opt.get("g_analysis", {})
    optimal_tf = coin_opt.get("optimal_tf", "5m")
    regime = coin_opt.get("regime", "?")
    G_val = coin_opt.get("G", 0)
    max_lev = coin_opt.get("max_leverage", 1)

    # Get best params for this TF
    tf_params = {}
    for tf_key, tf_data in coin_params.items():
        tf_params = tf_data
        break  # take first

    coeff = tf_params.get("coeff", 3.6)
    period = tf_params.get("period", 27)
    bt_pnl = tf_params.get("total_pnl_pct", 0)
    bt_pf = tf_params.get("profit_factor", 0)
    bt_wr = tf_params.get("win_rate", 0)

    # Fetch current klines and compute indicators
    klines = get_klines(sym, interval=optimal_tf, limit=300)

    at_last = 0; at_prev2 = 0; adx_val = 0; rsi_val = 0; mfi_val = 0
    trend_color = "?"
    signal_type = "?"
    adx_static_ok = False
    cross_detected = False

    if klines and len(klines["close"]) >= 100:
        closes = klines["close"]
        highs = klines["high"]
        lows = klines["low"]
        vols = klines["volume"]

        at, atr_vals, mfi_vals = calc_alphatrend(highs, lows, closes, vols, coeff, period)
        adx, plus_di, minus_di = calc_adx(highs, lows, closes, 14)
        rsi = calc_rsi(closes, 14)

        n = len(closes)
        at_last = at[-1]
        at_prev2 = at[-3] if n >= 3 else at[-1]
        adx_val = adx[-1]
        rsi_val = rsi[-1]
        mfi_val = mfi_vals[-1]

        # Trend color
        if at[-1] > at[-3]:
            trend_color = "GREEN (yukari)"
        elif at[-1] < at[-3]:
            trend_color = "RED (asagi)"
        else:
            trend_color = "NÖTR"

        # ADX filter check
        adx_static_ok = adx_val > 18.0
        adx_sma = calc_sma(adx, 14)
        adx_dynamic_ok = adx_val > adx_sma[-1] if adx_sma[-1] > 0 else True

        # Was entry direction consistent with current trend?
        if direction == "LONG":
            correct_now = at[-1] > at[-3]  # should be green
        elif direction == "SHORT":
            correct_now = at[-1] < at[-3]  # should be red
        else:
            correct_now = False

    # Price movement after entry
    if entry_price > 0 and exit_price > 0:
        if direction == "LONG":
            move_pct = (exit_price - entry_price) / entry_price * 100
        else:
            move_pct = (entry_price - exit_price) / entry_price * 100
    else:
        move_pct = 0

    # Categorize loss
    if lt["is_liq"]:
        category = "LIKIDASYON"
    elif abs(move_pct) < 0.05 and hold_minutes < 30:
        category = "ANLIK_REVERSAL"
    elif hold_minutes < 60:
        category = "HIZLI_SL"
    elif not adx_static_ok:
        category = "DUSUK_ADX"
    else:
        category = "YON_HATASI"

    categories[category].append(lt)

    # Print analysis
    tag = "LIKIT!" if lt["is_liq"] else "ZARAR"
    print(f"--- #{idx+1} {dt_str} {sym:15} {direction:5} net:{lt['net']:>+.4f} [{tag}] ---")
    if entry_price > 0:
        print(f"  Entry: {entry_price:.6f}  Exit: {exit_price:.6f}  Hareket: {move_pct:+.3f}%")
        print(f"  Tutma suresi: {hold_minutes:.0f} dk")
    if G_val > 0:
        print(f"  G={G_val:.4f}%  MaxLev={max_lev}x  Rejim={regime}")
        print(f"  Backtest: PnL={bt_pnl:.1f}%  PF={bt_pf:.2f}  WR={bt_wr:.1f}%")
    else:
        print(f"  [OPTIMIZE CACHE'DE YOK - fallback parametrelerle calismi$]")
    if klines:
        print(f"  AlphaTrend: {at_last:.6f}  Trend: {trend_color}")
        print(f"  ADX={adx_val:.1f} {'OK' if adx_static_ok else 'DUSUK(<18)'}  RSI={rsi_val:.1f}  MFI={mfi_val:.1f}")

    # Diagnosis
    if lt["is_liq"]:
        print(f"  TESHIS: Likidasyon - SL tetiklenmeden fiyat likidasyon seviyesine ulasti")
    elif not adx_static_ok and adx_val > 0:
        print(f"  TESHIS: ADX={adx_val:.1f} cok dusuk - trend yok, sinyal guvenilmez")
    elif hold_minutes < 30:
        print(f"  TESHIS: {hold_minutes:.0f}dk icerisinde SL'ye takildi - muhtemelen whipsaw/yanlis kirilim")
    elif hold_minutes < 120:
        print(f"  TESHIS: Kisa sureli tutma ({hold_minutes:.0f}dk) - sinyal dogru ama zamanlama erken")
    else:
        print(f"  TESHIS: Yon hatasi veya trend donusu - sinyal gecerliligi kaybolmus")
    print()

    time.sleep(0.05)  # rate limit

# ===================== CATEGORY SUMMARY =====================
print()
print("=" * 80)
print("ZARAR KATEGORILERI OZETI")
print("=" * 80)

cat_labels = {
    "LIKIDASYON": "Likidasyon (SL calismadi)",
    "ANLIK_REVERSAL": "Anlik ters donus (<30dk, <0.05% hareket)",
    "HIZLI_SL": "Hizli SL (<1 saat)",
    "DUSUK_ADX": "Dusuk ADX (<18, trend yok)",
    "YON_HATASI": "Yon hatasi / trend donusu",
}

total_loss = sum(t["net"] for t in loss_trades)
for cat, trades in sorted(categories.items(), key=lambda x: sum(t["net"] for t in x[1])):
    cat_loss = sum(t["net"] for t in trades)
    pct = abs(cat_loss / total_loss) * 100 if total_loss != 0 else 0
    coins = ", ".join(sorted(set(t["symbol"] for t in trades)))
    print(f"\n  {cat_labels.get(cat, cat)}")
    print(f"    Islem: {len(trades)}  Zarar: {cat_loss:+.4f} USDT  Pay: %{pct:.1f}")
    print(f"    Coinler: {coins}")

# ===================== OPTIMIZE CACHE ANALYSIS =====================
print()
print("=" * 80)
print("OPTIMIZE CACHE KONTROLU")
print("=" * 80)

loss_syms = set(t["symbol"] for t in loss_trades)
in_cache = 0
not_in_cache = []
low_pf = []
low_wr = []

for sym in sorted(loss_syms):
    if sym in opt_cache:
        in_cache += 1
        params = list(opt_cache[sym].get("params", {}).values())
        if params:
            p = params[0]
            pf = p.get("profit_factor", 0)
            wr = p.get("win_rate", 0)
            pnl = p.get("total_pnl_pct", 0)
            g = opt_cache[sym].get("G", 0)
            lev = opt_cache[sym].get("max_leverage", 1)
            status = "OK"
            if pf < 1.2:
                low_pf.append(sym)
                status = "DUSUK PF"
            if wr < 30:
                low_wr.append(sym)
                status += " DUSUK WR"
            print(f"  {sym:15} G={g:.4f}%  Lev={lev:>3}x  PnL={pnl:>+6.1f}%  PF={pf:.2f}  WR={wr:.0f}%  [{status}]")
    else:
        not_in_cache.append(sym)
        print(f"  {sym:15} [CACHE'DE YOK - fallback params kullanildi]")

print(f"\n  Cache'de bulunan: {in_cache}/{len(loss_syms)}")
print(f"  Cache'de olmayan: {len(not_in_cache)} -> {', '.join(not_in_cache) if not_in_cache else 'yok'}")
print(f"  Dusuk Profit Factor (<1.2): {len(low_pf)} -> {', '.join(low_pf) if low_pf else 'yok'}")
print(f"  Dusuk Win Rate (<30%): {len(low_wr)} -> {', '.join(low_wr) if low_wr else 'yok'}")

# ===================== FINAL RECOMMENDATIONS =====================
print()
print("=" * 80)
print("SONUC VE ONERILER")
print("=" * 80)

# Count by category
for cat in ["HIZLI_SL", "YON_HATASI", "DUSUK_ADX", "ANLIK_REVERSAL", "LIKIDASYON"]:
    if cat in categories:
        trades = categories[cat]
        cat_loss = sum(t["net"] for t in trades)
        print(f"  {len(trades):>2} islem {cat_labels.get(cat,'?'):40} {cat_loss:>+.4f} USDT")

print()
if not_in_cache:
    print(f"  [!] {len(not_in_cache)} coin optimize cache'de yok: {', '.join(not_in_cache)}")
    print(f"      Bu coinler fallback parametrelerle (coeff=3.6, period=27) islendi")
    print(f"      -> Backtest yapilip cache'e eklenmeli veya filtrelenmeli")
if low_pf:
    print(f"  [!] {len(low_pf)} coin dusuk Profit Factor: {', '.join(low_pf)}")
    print(f"      -> PF<1.2 coinler ban listesine alinabilir")
if "HIZLI_SL" in categories and len(categories["HIZLI_SL"]) > 3:
    print(f"  [!] {len(categories['HIZLI_SL'])} islem 1 saat icinde SL'ye takildi")
    print(f"      -> SL mesafesi (G*1.5) cok dar olabilir, G*2.0 denenebilir")
if "DUSUK_ADX" in categories:
    print(f"  [!] {len(categories['DUSUK_ADX'])} islem ADX<18 iken acildi")
    print(f"      -> ADX filtresi calismamis veya esik cok dusuk")
print()
