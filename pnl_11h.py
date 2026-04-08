"""Son 11 saatlik trade P&L raporu."""
from dotenv import load_dotenv
import os, time, hmac, hashlib, requests
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
start_ms = int((time.time() - 11 * 3600) * 1000)
params = sign({"incomeType": "", "limit": 1000, "startTime": start_ms})
resp = session.get(f"{base}/fapi/v1/income", params=params)
data = resp.json()

events = []
for item in data:
    t = item.get("time", 0)
    typ = item.get("incomeType", "?")
    amt = float(item.get("income", 0))
    sym = item.get("symbol", "")
    events.append({"time": t, "type": typ, "amount": amt, "symbol": sym})

# Group REALIZED_PNL, COMMISSION, and INSURANCE_CLEAR by symbol + time window
symbol_events = defaultdict(list)
for e in events:
    if e["type"] in ("REALIZED_PNL", "COMMISSION", "INSURANCE_CLEAR"):
        symbol_events[e["symbol"]].append(e)

all_trades = []
for sym, evts in symbol_events.items():
    evts.sort(key=lambda x: x["time"])
    current_group = []
    for e in evts:
        if current_group and e["time"] - current_group[-1]["time"] > 300000:
            pnl = sum(x["amount"] for x in current_group if x["type"] == "REALIZED_PNL")
            fee = sum(x["amount"] for x in current_group if x["type"] == "COMMISSION")
            liq = sum(x["amount"] for x in current_group if x["type"] == "INSURANCE_CLEAR")
            t = current_group[0]["time"]
            if pnl != 0 or liq != 0:
                is_liq = liq < 0
                all_trades.append({"time": t, "symbol": sym, "pnl": pnl, "fee": fee,
                                   "liq": liq, "net": pnl + fee + liq, "is_liq": is_liq})
            current_group = [e]
        else:
            current_group.append(e)
    if current_group:
        pnl = sum(x["amount"] for x in current_group if x["type"] == "REALIZED_PNL")
        fee = sum(x["amount"] for x in current_group if x["type"] == "COMMISSION")
        liq = sum(x["amount"] for x in current_group if x["type"] == "INSURANCE_CLEAR")
        t = current_group[0]["time"]
        if pnl != 0 or liq != 0:
            is_liq = liq < 0
            all_trades.append({"time": t, "symbol": sym, "pnl": pnl, "fee": fee,
                               "liq": liq, "net": pnl + fee + liq, "is_liq": is_liq})

total_commission = sum(e["amount"] for e in events if e["type"] == "COMMISSION")
total_pnl = sum(e["amount"] for e in events if e["type"] == "REALIZED_PNL")
total_transfer = sum(e["amount"] for e in events if e["type"] == "TRANSFER")
total_funding = sum(e["amount"] for e in events if e["type"] == "FUNDING_FEE")
total_insurance = sum(e["amount"] for e in events if e["type"] == "INSURANCE_CLEAR")

all_trades.sort(key=lambda x: x["time"])

# Print header
since = datetime.fromtimestamp(start_ms / 1000).strftime("%Y-%m-%d %H:%M")
now = datetime.now().strftime("%Y-%m-%d %H:%M")
print(f"\nSON 11 SAAT TRADE RAPORU ({since} - {now})")
print("=" * 115)
print(f"{'#':>3} | {'Tarih':14} | {'Sembol':15} | {'Brut K/Z':>10} | {'Fee':>10} | {'Likid.':>10} | {'Net K/Z':>10} | {'Durum':>8}")
print("-" * 115)

win = 0
loss = 0
liq_count = 0
liq_total = 0.0
best_trade = None
worst_trade = None

for i, e in enumerate(all_trades):
    dt = datetime.fromtimestamp(e["time"] / 1000).strftime("%m-%d %H:%M:%S")
    liq_str = f"{e['liq']:>+10.4f}" if e["liq"] != 0 else f"{'':>10}"

    if e["is_liq"]:
        tag = "LIKIT!"
        liq_count += 1
        liq_total += e["liq"]
    elif e["net"] >= 0:
        tag = "KAR"
        win += 1
    else:
        tag = "ZARAR"
        loss += 1

    if best_trade is None or e["net"] > best_trade["net"]:
        best_trade = e
    if worst_trade is None or e["net"] < worst_trade["net"]:
        worst_trade = e

    print(f"{i+1:>3} | {dt:14} | {e['symbol']:15} | {e['pnl']:>+10.4f} | {e['fee']:>+10.4f} | {liq_str} | {e['net']:>+10.4f} | {tag:>8}")

# Unmatched fees
matched_fee = sum(t["fee"] for t in all_trades)
unmatched_fee = total_commission - matched_fee

print("=" * 115)
print()
print("OZET")
print("-" * 50)
print(f"  Toplam Islem:              {len(all_trades)}")
print(f"  Kazanan: {win}  Kaybeden: {loss}  Likidasyonlar: {liq_count}")
if win + loss > 0:
    print(f"  Win Rate:                  {win / (win + loss) * 100:.1f}%")
print()
print(f"  Brut K/Z:                  {total_pnl:>+10.4f} USDT")
print(f"  Komisyon (fee):            {total_commission:>+10.4f} USDT")
if abs(total_insurance) > 0.001:
    print(f"  Likidasyonlar:             {total_insurance:>+10.4f} USDT ({liq_count} kez)")
if abs(total_funding) > 0.001:
    print(f"  Funding Fee:               {total_funding:>+10.4f} USDT")
if abs(unmatched_fee) > 0.001:
    print(f"  Giris Fee (acik poz):      {unmatched_fee:>+10.4f} USDT")
print(f"  -------------------------------------------")
net = total_pnl + total_commission + total_insurance + total_funding
print(f"  NET TOPLAM:                {net:>+10.4f} USDT")
print()

if best_trade:
    print(f"  En Iyi Islem:   {best_trade['symbol']:15} {best_trade['net']:>+10.4f} USDT")
if worst_trade:
    print(f"  En Kotu Islem:  {worst_trade['symbol']:15} {worst_trade['net']:>+10.4f} USDT")
print()

# Coin bazli ozet
coin_pnl = defaultdict(lambda: {"net": 0, "count": 0, "win": 0, "loss": 0})
for t in all_trades:
    coin_pnl[t["symbol"]]["net"] += t["net"]
    coin_pnl[t["symbol"]]["count"] += 1
    if t["net"] >= 0:
        coin_pnl[t["symbol"]]["win"] += 1
    else:
        coin_pnl[t["symbol"]]["loss"] += 1

if coin_pnl:
    print("COIN BAZLI OZET")
    print("-" * 60)
    print(f"  {'Sembol':15} | {'Islem':>5} | {'Kar':>3} | {'Zarar':>5} | {'Net K/Z':>12}")
    print(f"  " + "-" * 55)
    for sym, d in sorted(coin_pnl.items(), key=lambda x: x[1]["net"], reverse=True):
        print(f"  {sym:15} | {d['count']:>5} | {d['win']:>3} | {d['loss']:>5} | {d['net']:>+12.4f}")
    print()

# Current balance
params2 = sign({"recvWindow": 5000})
resp2 = session.get(f"{base}/fapi/v2/balance", params=params2)
for b in resp2.json():
    if b["asset"] == "USDT":
        avail = float(b["availableBalance"])
        wallet = float(b["balance"])
        print(f"  Guncel Bakiye:  {wallet:>10.4f} USDT (kullanilabilir: {avail:.4f})")
        break

# Open positions
params3 = sign({"recvWindow": 5000})
resp3 = session.get(f"{base}/fapi/v2/positionRisk", params=params3)
open_pos = []
for p in resp3.json():
    amt = float(p.get("positionAmt", 0))
    if amt != 0:
        open_pos.append(p)

if open_pos:
    print()
    print("ACIK POZISYONLAR")
    print("-" * 80)
    total_upnl = 0
    for p in open_pos:
        sym = p["symbol"]
        amt = float(p["positionAmt"])
        entry = float(p["entryPrice"])
        upnl = float(p.get("unRealizedProfit", 0))
        lev = p.get("leverage", "?")
        margin = float(p.get("isolatedWallet", 0))
        side = "LONG" if amt > 0 else "SHORT"
        total_upnl += upnl
        print(f"  {sym:15} {side:5} lev={lev:>3}x  entry={entry:<12.6f}  margin={margin:<8.4f}  uPnL={upnl:>+10.4f}")
    print(f"  Toplam Acik uPnL: {total_upnl:>+10.4f} USDT")
print()
