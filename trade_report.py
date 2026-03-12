"""Generate trade P&L report from Binance income history (with liquidations)."""
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
params = sign({"incomeType": "", "limit": 1000})
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
                all_trades.append({
                    "time": t, "symbol": sym, "pnl": pnl, "fee": fee,
                    "liq": liq, "net": pnl + fee + liq, "is_liq": is_liq,
                })
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
            all_trades.append({
                "time": t, "symbol": sym, "pnl": pnl, "fee": fee,
                "liq": liq, "net": pnl + fee + liq, "is_liq": is_liq,
            })

total_commission = sum(e["amount"] for e in events if e["type"] == "COMMISSION")
total_pnl = sum(e["amount"] for e in events if e["type"] == "REALIZED_PNL")
total_transfer = sum(e["amount"] for e in events if e["type"] == "TRANSFER")
total_funding = sum(e["amount"] for e in events if e["type"] == "FUNDING_FEE")
total_insurance = sum(e["amount"] for e in events if e["type"] == "INSURANCE_CLEAR")

all_trades.sort(key=lambda x: x["time"])
transfers = [{"time": e["time"], "amount": e["amount"]} for e in events if e["type"] == "TRANSFER"]

# Merge transfers and trades by time
all_rows = []
for t in transfers:
    all_rows.append({"time": t["time"], "row_type": "TRANSFER", "amount": t["amount"]})
for t in all_trades:
    all_rows.append({"time": t["time"], "row_type": "TRADE", **t})
all_rows.sort(key=lambda x: x["time"])

# Print table
print("=" * 110)
print(f"{'#':>3} | {'Tarih':12} | {'Sembol':15} | {'Brut K/Z':>10} | {'Fee':>10} | {'Likid.':>10} | {'Net K/Z':>10} | {'Bakiye':>10}")
print("-" * 110)

balance = 0.0
row = 0
win = 0
loss = 0
liq_count = 0
liq_total = 0.0

for e in all_rows:
    dt = datetime.fromtimestamp(e["time"] / 1000).strftime("%m-%d %H:%M")
    if e["row_type"] == "TRANSFER":
        balance += e["amount"]
        row += 1
        print(f"{row:>3} | {dt:12} | {'--- TRANSFER ---':15} | {e['amount']:>+10.4f} |{'':>11} |{'':>11} | {e['amount']:>+10.4f} | {balance:>10.4f}")
    elif e["row_type"] == "TRADE":
        balance += e["net"]
        row += 1
        if e["net"] >= 0:
            win += 1
        else:
            loss += 1

        liq_str = f"{e['liq']:>+10.4f}" if e["liq"] != 0 else f"{'':>10}"
        tag = ""
        if e["is_liq"]:
            tag = " LIKIT!"
            liq_count += 1
            liq_total += e["liq"]
        elif e["net"] >= 0:
            tag = " KAR"
        else:
            tag = " ZARAR"

        print(f"{row:>3} | {dt:12} | {e['symbol']:15} | {e['pnl']:>+10.4f} | {e['fee']:>+10.4f} | {liq_str} | {e['net']:>+10.4f} | {balance:>10.4f}{tag}")

# Unmatched entry commissions
matched_fee = sum(t["fee"] for t in all_trades)
unmatched_fee = total_commission - matched_fee
if abs(unmatched_fee) > 0.001:
    balance += unmatched_fee
    row += 1
    print(f"{row:>3} | {'':12} | {'Giris Fee (acik)':15} |{'':>11} | {unmatched_fee:>+10.4f} |{'':>11} | {unmatched_fee:>+10.4f} | {balance:>10.4f}")

if abs(total_funding) > 0.001:
    balance += total_funding
    row += 1
    print(f"{row:>3} | {'':12} | {'Funding Fee':15} |{'':>11} | {total_funding:>+10.4f} |{'':>11} | {total_funding:>+10.4f} | {balance:>10.4f}")

print("=" * 110)
print()
print(f"  Toplam Transfer (yatirilan):  {total_transfer:>+10.4f} USDT")
print(f"  Toplam Brut K/Z:              {total_pnl:>+10.4f} USDT")
print(f"  Toplam Komisyon (fee):        {total_commission:>+10.4f} USDT")
print(f"  Toplam Likidasyonlar:         {total_insurance:>+10.4f} USDT  ({liq_count} kez)")
print(f"  Toplam Funding Fee:           {total_funding:>+10.4f} USDT")
print(f"  -------------------------------------------")
net = total_pnl + total_commission + total_insurance + total_funding
print(f"  NET TOPLAM KAYIP:             {net:>+10.4f} USDT")
print(f"  Transfer dahil bakiye:        {total_transfer + net:>+10.4f} USDT")
print()
print(f"  Kazanan: {win}  Kaybeden: {loss}  Win Rate: {win/(win+loss)*100:.1f}%" if win + loss > 0 else "")
print(f"  Likidasyonlar: {liq_count} kez, toplam {liq_total:+.4f} USDT kaybedildi")
print()

# Current balance from Binance
params2 = sign({"recvWindow": 5000})
resp2 = session.get(f"{base}/fapi/v2/balance", params=params2)
for b in resp2.json():
    if b["asset"] == "USDT":
        avail = float(b["availableBalance"])
        wallet = float(b["balance"])
        print(f"  Gercek Bakiye (Binance):      {wallet:>10.4f} USDT (kullanilabilir: {avail:.4f})")
        break

# Open positions
params3 = sign({"recvWindow": 5000})
resp3 = session.get(f"{base}/fapi/v2/positionRisk", params=params3)
for p in resp3.json():
    amt = float(p.get("positionAmt", 0))
    if amt != 0:
        sym = p["symbol"]
        entry = float(p["entryPrice"])
        upnl = float(p.get("unRealizedProfit", 0))
        lev = p.get("leverage", "?")
        margin = float(p.get("isolatedWallet", 0))
        print(f"  Acik pozisyon: {sym} qty={amt} entry={entry} lev={lev}x unrealizedPnL={upnl:+.4f} margin={margin:.4f}")
