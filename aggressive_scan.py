"""Aggressive scan: find the single best opportunity right now."""
from dotenv import load_dotenv
import os, time, hmac, hashlib, requests
from urllib.parse import urlencode
import pandas as pd
import numpy as np

load_dotenv()
key = os.getenv("BINANCE_API_KEY")
secret = os.getenv("BINANCE_API_SECRET")

session = requests.Session()
session.headers["X-MBX-APIKEY"] = key

BASE = "https://fapi.binance.com"

def sign(params):
    params["timestamp"] = int(time.time() * 1000)
    qs = urlencode(params)
    sig = hmac.new(secret.encode(), qs.encode(), hashlib.sha256).hexdigest()
    params["signature"] = sig
    return params

def get_klines(symbol, interval="1m", limit=200):
    resp = session.get(f"{BASE}/fapi/v1/klines", params={
        "symbol": symbol, "interval": interval, "limit": limit
    })
    data = resp.json()
    if not data:
        return None
    df = pd.DataFrame(data, columns=[
        "open_time", "open", "high", "low", "close", "volume",
        "close_time", "quote_volume", "trades", "taker_buy_vol",
        "taker_buy_quote", "ignore"
    ])
    for c in ["open", "high", "low", "close", "volume", "quote_volume"]:
        df[c] = df[c].astype(float)
    return df

def analyze(df):
    """Full technical analysis for a symbol."""
    close = df["close"].values
    high = df["high"].values
    low = df["low"].values
    volume = df["volume"].values
    price = close[-1]

    # RSI
    delta = np.diff(close)
    gain = np.where(delta > 0, delta, 0)
    loss = np.where(delta < 0, -delta, 0)
    period = 9
    avg_gain = pd.Series(gain).rolling(period).mean().iloc[-1]
    avg_loss = pd.Series(loss).rolling(period).mean().iloc[-1]
    rsi = 100 - (100 / (1 + avg_gain / max(avg_loss, 1e-10)))

    # EMA
    ema5 = pd.Series(close).ewm(span=5).mean().iloc[-1]
    ema13 = pd.Series(close).ewm(span=13).mean().iloc[-1]
    ema50 = pd.Series(close).ewm(span=50).mean().iloc[-1]

    # MACD
    ema8 = pd.Series(close).ewm(span=8).mean()
    ema17 = pd.Series(close).ewm(span=17).mean()
    macd_line = ema8 - ema17
    signal_line = macd_line.ewm(span=5).mean()
    macd_hist = (macd_line - signal_line).iloc[-1]
    macd_hist_prev = (macd_line - signal_line).iloc[-2]

    # ADX
    tr = np.maximum(high[1:] - low[1:],
                    np.maximum(abs(high[1:] - close[:-1]), abs(low[1:] - close[:-1])))
    atr14 = pd.Series(tr).rolling(14).mean().iloc[-1]
    atr_pct = (atr14 / price) * 100

    plus_dm = np.where((high[1:] - high[:-1]) > (low[:-1] - low[1:]),
                       np.maximum(high[1:] - high[:-1], 0), 0)
    minus_dm = np.where((low[:-1] - low[1:]) > (high[1:] - high[:-1]),
                        np.maximum(low[:-1] - low[1:], 0), 0)
    atr_s = pd.Series(tr).rolling(14).mean()
    plus_di = 100 * pd.Series(plus_dm).rolling(14).mean() / atr_s
    minus_di = 100 * pd.Series(minus_dm).rolling(14).mean() / atr_s
    dx = 100 * abs(plus_di - minus_di) / (plus_di + minus_di + 1e-10)
    adx = dx.rolling(14).mean().iloc[-1]

    # Volume trend (last 5 vs prev 5)
    vol_recent = volume[-5:].mean()
    vol_prev = volume[-10:-5].mean()
    vol_surge = vol_recent / max(vol_prev, 1) - 1

    # OBV slope
    obv = np.cumsum(np.where(np.diff(close) > 0, volume[1:], -volume[1:]))
    obv_slope = (obv[-1] - obv[-5]) / 5 if len(obv) > 5 else 0

    # Bollinger Bands
    sma20 = pd.Series(close).rolling(20).mean().iloc[-1]
    std20 = pd.Series(close).rolling(20).std().iloc[-1]
    bb_upper = sma20 + 2 * std20
    bb_lower = sma20 - 2 * std20
    bb_pos = (price - bb_lower) / max(bb_upper - bb_lower, 1e-10)

    # Momentum (price change last 5 candles)
    mom_5 = (price / close[-6] - 1) * 100 if len(close) > 5 else 0

    # Score
    score = 0
    direction = "LONG"

    # Confluence scoring
    signals_long = 0
    signals_short = 0
    total_signals = 0

    # EMA alignment
    total_signals += 1
    if ema5 > ema13 > ema50:
        signals_long += 1
    elif ema5 < ema13 < ema50:
        signals_short += 1

    # MACD
    total_signals += 1
    if macd_hist > 0 and macd_hist > macd_hist_prev:
        signals_long += 1
    elif macd_hist < 0 and macd_hist < macd_hist_prev:
        signals_short += 1

    # RSI
    total_signals += 1
    if 40 < rsi < 60:
        signals_long += 0.5
        signals_short += 0.5
    elif rsi < 40:
        signals_long += 1  # oversold = bounce opportunity
    elif rsi > 60:
        signals_short += 1

    # ADX + DI
    total_signals += 1
    if adx > 20:
        pdi = plus_di.iloc[-1]
        mdi = minus_di.iloc[-1]
        if pdi > mdi:
            signals_long += 1
        else:
            signals_short += 1

    # OBV
    total_signals += 1
    if obv_slope > 0:
        signals_long += 1
    elif obv_slope < 0:
        signals_short += 1

    # Volume surge
    total_signals += 1
    if vol_surge > 0.3:
        if mom_5 > 0:
            signals_long += 1
        else:
            signals_short += 1

    # Bollinger position
    total_signals += 1
    if bb_pos < 0.2:
        signals_long += 1  # near lower band = bounce
    elif bb_pos > 0.8:
        signals_short += 1

    if signals_long > signals_short:
        direction = "LONG"
        score = signals_long / total_signals * 100
    else:
        direction = "SHORT"
        score = signals_short / total_signals * 100

    return {
        "price": price,
        "direction": direction,
        "score": score,
        "rsi": rsi,
        "adx": adx,
        "macd_hist": macd_hist,
        "ema5": ema5, "ema13": ema13, "ema50": ema50,
        "atr_pct": atr_pct,
        "vol_surge": vol_surge,
        "mom_5": mom_5,
        "bb_pos": bb_pos,
        "signals_long": signals_long,
        "signals_short": signals_short,
    }

# 1. Get balance
params = sign({"recvWindow": 5000})
resp = session.get(f"{BASE}/fapi/v2/balance", params=params)
balance = 0
for b in resp.json():
    if b["asset"] == "USDT":
        balance = float(b["availableBalance"])
print(f"Bakiye: {balance:.4f} USDT")
print()

# 2. Get top volume futures
resp = session.get(f"{BASE}/fapi/v1/ticker/24hr")
tickers = resp.json()
tickers.sort(key=lambda x: float(x.get("quoteVolume", 0)), reverse=True)

# Filter USDT pairs only, exclude stablecoins
skip = {"USDCUSDT", "FDUSDUSDT", "DAIUSDT", "TUSDUSDT", "BUSDUSDT"}
candidates = []
for t in tickers[:80]:
    sym = t["symbol"]
    if not sym.endswith("USDT") or sym in skip:
        continue
    candidates.append({
        "symbol": sym,
        "volume": float(t["quoteVolume"]),
        "change": float(t.get("priceChangePercent", 0)),
    })

print(f"Scanning {len(candidates)} symbols...")
print()

# 3. Analyze each
results = []
for c in candidates:
    try:
        df = get_klines(c["symbol"])
        if df is None or len(df) < 50:
            continue
        analysis = analyze(df)

        # ATR filter for leverage safety
        max_lev = 75  # target
        liq_dist = (1.0 / max_lev) * 100  # ~1.33%
        if analysis["atr_pct"] > liq_dist * 0.5:
            continue  # too volatile for this leverage

        results.append({
            **c,
            **analysis,
        })
        time.sleep(0.1)  # rate limit
    except Exception as e:
        continue

# Sort by score
results.sort(key=lambda x: x["score"], reverse=True)

# Print top 15
print(f"{'#':>2} | {'Sembol':12} | {'Yon':5} | {'Skor':>5} | {'RSI':>5} | {'ADX':>5} | {'MACD':>8} | {'ATR%':>5} | {'Mom5':>6} | {'VolSrg':>6} | {'L/S signals':12}")
print("-" * 100)
for i, r in enumerate(results[:15]):
    print(f"{i+1:>2} | {r['symbol']:12} | {r['direction']:5} | {r['score']:>5.1f} | {r['rsi']:>5.1f} | {r['adx']:>5.1f} | {r['macd_hist']:>+8.4f} | {r['atr_pct']:>5.3f} | {r['mom_5']:>+6.2f} | {r['vol_surge']:>+6.2f} | L={r['signals_long']:.1f} S={r['signals_short']:.1f}")

if results:
    best = results[0]
    print()
    print(f"EN IYI FIRSAT: {best['symbol']} {best['direction']} (skor: {best['score']:.1f})")
    print(f"  Fiyat: {best['price']}")
    print(f"  RSI: {best['rsi']:.1f}, ADX: {best['adx']:.1f}, MACD: {best['macd_hist']:+.6f}")
    print(f"  ATR%: {best['atr_pct']:.3f}%, Mom5: {best['mom_5']:+.2f}%")
    print(f"  Volume surge: {best['vol_surge']:+.1%}")
    print(f"  EMA5={best['ema5']:.6f} EMA13={best['ema13']:.6f} EMA50={best['ema50']:.6f}")
    print()

    # Calculate trade params
    margin = round(balance * 0.95, 2)  # use 95% of balance
    # Get max leverage
    params2 = sign({"symbol": best["symbol"], "recvWindow": 5000})
    resp2 = session.get(f"{BASE}/fapi/v1/leverageBracket", params=params2)
    brackets = resp2.json()
    max_lev_api = 75
    if brackets:
        for item in brackets:
            if item.get("symbol") == best["symbol"]:
                br = item.get("brackets", [])
                if br:
                    max_lev_api = br[0].get("initialLeverage", 75)

    leverage = min(75, max_lev_api)
    notional = margin * leverage
    fee_pct = 0.1 * leverage  # fee as % of margin (round trip)

    print(f"  Margin: {margin}$ x {leverage}x = {notional:.1f}$ notional")
    print(f"  Fee maliyeti: ~{fee_pct:.1f}% of margin ({margin * fee_pct / 100:.4f}$)")
    print(f"  %50 ROI icin fiyat hareketi: {50/leverage:.2f}%")
    print(f"  %100 ROI (2x) icin fiyat hareketi: {100/leverage:.2f}%")
    print(f"  Fee breakeven (~10% ROI): {10/leverage:.3f}% fiyat hareketi")
