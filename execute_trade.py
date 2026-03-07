"""Execute aggressive trade - SAVAS MODU."""
from dotenv import load_dotenv
import os, time, hmac, hashlib, requests
from urllib.parse import urlencode

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

def api_post(endpoint, params):
    params = sign(params)
    resp = session.post(f"{BASE}{endpoint}", params=params, timeout=10)
    if resp.status_code >= 400:
        print(f"ERROR {resp.status_code}: {resp.json()}")
        return resp.json()
    return resp.json()

def api_get(endpoint, params):
    params = sign(params)
    return session.get(f"{BASE}{endpoint}", params=params, timeout=10).json()

SYMBOL = "ASTERUSDT"
SIDE = "BUY"
LEVERAGE = 75

# 1. Balance
balance = 0
for b in api_get("/fapi/v2/balance", {"recvWindow": 5000}):
    if b["asset"] == "USDT":
        balance = float(b["availableBalance"])
print(f"Balance: {balance:.4f} USDT")

margin = round(balance * 0.95, 2)

# 2. Symbol info
info = session.get(f"{BASE}/fapi/v1/exchangeInfo").json()
sym_info = next(s for s in info["symbols"] if s["symbol"] == SYMBOL)
qty_prec = sym_info["quantityPrecision"]
price_prec = sym_info["pricePrecision"]
min_notional = 5.0
for f in sym_info["filters"]:
    if f["filterType"] == "MIN_NOTIONAL":
        min_notional = float(f["notional"])

# 3. Price
price = float(session.get(f"{BASE}/fapi/v1/ticker/price", params={"symbol": SYMBOL}).json()["price"])
print(f"Price: {price}")

# 4. Max leverage
brackets = api_get("/fapi/v1/leverageBracket", {"symbol": SYMBOL, "recvWindow": 5000})
max_lev = LEVERAGE
for item in brackets:
    if item.get("symbol") == SYMBOL:
        br = item.get("brackets", [])
        if br:
            max_lev = min(LEVERAGE, br[0].get("initialLeverage", LEVERAGE))

leverage = max_lev
notional = margin * leverage
qty = round(notional / price, qty_prec)

if notional < min_notional:
    margin = round(min_notional / leverage * 1.05, 2)
    notional = margin * leverage
    qty = round(notional / price, qty_prec)

fee_cost = notional * 0.001
fee_roi = fee_cost / margin * 100

print(f"\n{'='*50}")
print(f"  SAVAS MODU - {SIDE} {SYMBOL}")
print(f"  Margin: {margin}$ x {leverage}x = {notional:.1f}$")
print(f"  Qty: {qty}, Fee: {fee_cost:.4f}$ ({fee_roi:.1f}% ROI)")
print(f"{'='*50}\n")

# 5. Set isolated + leverage
try:
    api_post("/fapi/v1/marginType", {"symbol": SYMBOL, "marginType": "ISOLATED"})
except:
    pass
api_post("/fapi/v1/leverage", {"symbol": SYMBOL, "leverage": leverage})

# 6. Market order
order = api_post("/fapi/v1/order", {
    "symbol": SYMBOL, "side": SIDE, "type": "MARKET",
    "newOrderRespType": "RESULT", "quantity": qty,
})
avg_price = float(order.get("avgPrice", 0)) or price
exec_qty = float(order.get("executedQty", 0)) or qty
print(f"FILLED: {SIDE} {exec_qty} {SYMBOL} @ {avg_price}")

# 7. Emergency SL only (65% of liq distance) - NO TP
liq_pct = (1.0 / leverage) * 0.70
emergency_pct = liq_pct * 0.65
sl_price = round(avg_price * (1 - emergency_pct), price_prec)

close_side = "SELL" if SIDE == "BUY" else "BUY"
sl = api_post("/fapi/v1/order", {
    "symbol": SYMBOL, "side": close_side, "type": "STOP_MARKET",
    "stopPrice": sl_price, "closePosition": "true", "workingType": "MARK_PRICE",
})
print(f"Emergency SL: {sl_price}")

print(f"\nPozisyon acildi! Simdi botu SAVAS MODU ile baslat.")
print(f"Fee breakeven: {avg_price * (1 + fee_cost/notional):.6f}")
print(f"2x hedef: {avg_price * (1 + 1/leverage):.6f}")
