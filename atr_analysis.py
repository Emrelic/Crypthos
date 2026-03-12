"""ATR Analysis: Top 50 coins - leverage, liquidation, SL, ATR, optimal timeframe."""
from dotenv import load_dotenv
import os, hmac, hashlib, requests
from urllib.parse import urlencode
import numpy as np
import time

load_dotenv()
key = os.getenv("BINANCE_API_KEY")
secret = os.getenv("BINANCE_API_SECRET")

BASE = "https://fapi.binance.com"
session = requests.Session()
session.headers["X-MBX-APIKEY"] = key

def sign(params):
    params["timestamp"] = int(time.time() * 1000)
    qs = urlencode(params)
    sig = hmac.new(secret.encode(), qs.encode(), hashlib.sha256).hexdigest()
    params["signature"] = sig
    return params

# 1. Get top 50 USDT futures by volume
print("Top 50 coin verisi cekiliyor...")
resp = session.get(f"{BASE}/fapi/v1/ticker/24hr")
tickers = resp.json()
tickers.sort(key=lambda x: float(x.get("quoteVolume", 0)), reverse=True)

skip = {"USDCUSDT", "FDUSDUSDT", "DAIUSDT", "TUSDUSDT", "BUSDUSDT"}
top50 = []
for t in tickers:
    sym = t["symbol"]
    if not sym.endswith("USDT") or sym in skip:
        continue
    top50.append(sym)
    if len(top50) >= 50:
        break

# 2. Get max leverage for each symbol
print("Kaldirac bilgileri cekiliyor...")
info = session.get(f"{BASE}/fapi/v1/exchangeInfo").json()
sym_info = {s["symbol"]: s for s in info["symbols"]}

brackets_resp = session.get(f"{BASE}/fapi/v1/leverageBracket", params=sign({"recvWindow": 5000})).json()
max_leverage = {}
for item in brackets_resp:
    sym = item.get("symbol", "")
    br = item.get("brackets", [])
    if br:
        max_leverage[sym] = br[0].get("initialLeverage", 20)

# 3. Calculate ATR for each timeframe
TIMEFRAMES = ["1m", "3m", "5m", "15m", "30m", "1h", "4h"]
TF_MINUTES = {"1m": 1, "3m": 3, "5m": 5, "15m": 15, "30m": 30, "1h": 60, "4h": 240}


def get_atr_pct(symbol, interval, limit=100):
    """Get ATR as percentage of price for given timeframe."""
    try:
        resp = session.get(f"{BASE}/fapi/v1/klines", params={
            "symbol": symbol, "interval": interval, "limit": limit
        })
        data = resp.json()
        if not data or len(data) < 20:
            return None
        highs = np.array([float(d[2]) for d in data])
        lows = np.array([float(d[3]) for d in data])
        closes = np.array([float(d[4]) for d in data])

        # True Range
        tr = np.maximum(
            highs[1:] - lows[1:],
            np.maximum(
                np.abs(highs[1:] - closes[:-1]),
                np.abs(lows[1:] - closes[:-1])
            )
        )
        atr14 = np.mean(tr[-14:])  # 14-period ATR
        price = closes[-1]
        return (atr14 / price) * 100 if price > 0 else None
    except Exception:
        return None


# 4. Analyze each coin
print(f"\n{len(top50)} coin analiz ediliyor (her biri icin {len(TIMEFRAMES)} vade)...\n")

results = []
for i, sym in enumerate(top50):
    lev = max_leverage.get(sym, 20)

    # Calculations
    theoretical_liq = (1.0 / lev) * 100          # C: theoretical liq %
    practical_liq = theoretical_liq * 0.70         # D: practical liq (70%)
    sl_point = practical_liq * 0.50                # E: SL at 50% of practical liq
    target_atr = sl_point / 2.0                    # F: target ATR (SL = 2x ATR)

    # Find optimal timeframe
    best_tf = "-"
    best_atr = None
    all_atrs = {}

    for tf in TIMEFRAMES:
        atr = get_atr_pct(sym, tf)
        if atr is not None:
            all_atrs[tf] = atr
            # Find shortest TF where ATR <= target
            if atr <= target_atr and best_tf == "-":
                best_tf = tf
                best_atr = atr
        time.sleep(0.05)  # rate limit

    # If no TF found where ATR <= target, mark the closest one
    if best_tf == "-" and all_atrs:
        # Find TF with smallest ATR
        min_tf = min(all_atrs, key=all_atrs.get)
        min_atr = all_atrs[min_tf]
        best_tf = f"{min_tf}*"  # * means ATR still too high
        best_atr = min_atr

    atr_1m = all_atrs.get("1m", 0)

    results.append({
        "symbol": sym,
        "max_lev": lev,
        "theoretical_liq": theoretical_liq,
        "practical_liq": practical_liq,
        "sl_point": sl_point,
        "target_atr": target_atr,
        "atr_1m": atr_1m,
        "best_tf": best_tf,
        "best_atr": best_atr or 0,
        "all_atrs": all_atrs,
    })

    status = "OK" if "*" not in best_tf else "UYGUN VADE YOK"
    print(f"  [{i+1:2d}/50] {sym:15s} Lev={lev:3d}x  "
          f"HedefATR={target_atr:.4f}%  1mATR={atr_1m:.4f}%  "
          f"Vade={best_tf:5s}  {status}")

# 5. Print table
print("\n" + "=" * 130)
print(f"{'#':>3} | {'A: Coin':15s} | {'B: MaxLev':>8s} | {'C: TeorikLiq':>11s} | "
      f"{'D: PratikLiq':>11s} | {'E: SL':>8s} | {'F: HedefATR':>10s} | "
      f"{'1m ATR':>8s} | {'G: Vade':>8s} | {'Vade ATR':>8s} | Durum")
print("-" * 130)

ok_count = 0
for i, r in enumerate(results):
    is_ok = "*" not in r["best_tf"]
    if is_ok:
        ok_count += 1
    status = "UYGUN" if is_ok else "RISKLI"
    status_mark = "" if is_ok else " <<<"

    print(f"{i+1:>3} | {r['symbol']:15s} | {r['max_lev']:>6d}x | "
          f"{r['theoretical_liq']:>10.4f}% | {r['practical_liq']:>10.4f}% | "
          f"{r['sl_point']:>7.4f}% | {r['target_atr']:>9.4f}% | "
          f"{r['atr_1m']:>7.4f}% | {r['best_tf']:>8s} | "
          f"{r['best_atr']:>7.4f}% | {status}{status_mark}")

print("=" * 130)
print(f"\nUygun: {ok_count}/50  Riskli: {50-ok_count}/50")
print(f"\n* isaretli = Hicbir vadede ATR hedefin altina dusmedi (cok volatil veya kaldirac cok yuksek)")

# 6. Summary by leverage groups
print("\n\nKALDIRAC GRUPLARINA GORE OZET:")
print("-" * 80)
groups = {}
for r in results:
    lev = r["max_lev"]
    if lev >= 100:
        g = "100x+"
    elif lev >= 75:
        g = "75-99x"
    elif lev >= 50:
        g = "50-74x"
    elif lev >= 25:
        g = "25-49x"
    else:
        g = "<25x"
    if g not in groups:
        groups[g] = {"count": 0, "ok": 0, "coins": []}
    groups[g]["count"] += 1
    groups[g]["ok"] += 1 if "*" not in r["best_tf"] else 0
    groups[g]["coins"].append(r["symbol"])

for g in ["100x+", "75-99x", "50-74x", "25-49x", "<25x"]:
    if g in groups:
        info = groups[g]
        print(f"  {g:8s}: {info['count']:2d} coin, {info['ok']:2d} uygun, "
              f"{info['count']-info['ok']:2d} riskli")

# 7. Timeframe distribution
print("\n\nONERILEN VADE DAGILIMI:")
print("-" * 50)
tf_dist = {}
for r in results:
    tf = r["best_tf"].replace("*", "")
    tf_dist[tf] = tf_dist.get(tf, 0) + 1
for tf in TIMEFRAMES:
    if tf in tf_dist:
        bar = "#" * tf_dist[tf]
        print(f"  {tf:4s}: {tf_dist[tf]:2d} coin  {bar}")
