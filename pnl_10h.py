"""Son 10 saatlik trade P&L raporu + portfoy durumu."""
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
start_ms = int((time.time() - 10 * 3600) * 1000)

# Fetch income history (paginated for 24h)
all_data = []
cursor_start = start_ms
for _ in range(5):
    params = sign({"incomeType": "", "limit": 1000, "startTime": cursor_start})
    resp = session.get(f"{base}/fapi/v1/income", params=params)
    page = resp.json()
    if not page:
        break
    all_data.extend(page)
    if len(page) < 1000:
        break
    cursor_start = page[-1]["time"] + 1

data = all_data

events = []
for item in data:
    t = item.get("time", 0)
    typ = item.get("incomeType", "?")
    amt = float(item.get("income", 0))
    sym = item.get("symbol", "")
    events.append({"time": t, "type": typ, "amount": amt, "symbol": sym})

# Group trades by symbol + time window
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

since = datetime.fromtimestamp(start_ms / 1000).strftime("%Y-%m-%d %H:%M")
now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
print(f"\nSON 10 SAAT TRADE RAPORU ({since} - {now_str})")
print("=" * 115)
print(f"{'#':>3} | {'Tarih':14} | {'Sembol':15} | {'Brut K/Z':>10} | {'Fee':>10} | {'Likid.':>10} | {'Net K/Z':>10} | {'Durum':>8}")
print("-" * 115)

win = 0; loss = 0; liq_count = 0; liq_total = 0.0
best_trade = None; worst_trade = None

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

# ==================== HESAP DURUMU ====================
params2 = sign({"recvWindow": 5000})
resp2 = session.get(f"{base}/fapi/v2/account", params=params2)
acc = resp2.json()

wallet_balance = float(acc.get("totalWalletBalance", 0))
unrealized_pnl = float(acc.get("totalUnrealizedProfit", 0))
margin_balance = float(acc.get("totalMarginBalance", 0))
available_balance = float(acc.get("availableBalance", 0))
total_position_margin = float(acc.get("totalPositionInitialMargin", 0))
total_open_order_margin = float(acc.get("totalOpenOrderInitialMargin", 0))

print("=" * 60)
print("HESAP DURUMU")
print("=" * 60)
print(f"  Cuzdan Bakiyesi:           {wallet_balance:>10.4f} USDT")
print(f"  Kullanilabilir Bakiye:     {available_balance:>10.4f} USDT")
print(f"  Pozisyon Margini:          {total_position_margin:>10.4f} USDT")
print(f"  Emir Margini:              {total_open_order_margin:>10.4f} USDT")
print(f"  Toplam Margin Bakiyesi:    {margin_balance:>10.4f} USDT")
print(f"  Acik uPnL:                {unrealized_pnl:>+10.4f} USDT")
print(f"  -------------------------------------------")
realized_portfolio = wallet_balance + unrealized_pnl
print(f"  REALIZE EDILIRSE PORTFOY:  {realized_portfolio:>10.4f} USDT")
print()

# ==================== ACIK POZISYONLAR ====================
params3 = sign({"recvWindow": 5000})
resp3 = session.get(f"{base}/fapi/v2/positionRisk", params=params3)
open_pos = []
for p in resp3.json():
    amt = float(p.get("positionAmt", 0))
    if amt != 0:
        open_pos.append(p)

if open_pos:
    print("ACIK POZISYONLAR")
    print("-" * 120)
    print(f"  {'Sembol':15} {'Yon':5} {'Lev':>4} {'Entry':>12} {'Mark':>12} {'Margin':>8} {'uPnL':>10} {'ROI%':>8}")
    print("  " + "-" * 115)
    total_upnl = 0
    total_margin = 0
    for p in sorted(open_pos, key=lambda x: float(x.get("unRealizedProfit", 0)), reverse=True):
        sym = p["symbol"]
        amt = float(p["positionAmt"])
        entry = float(p["entryPrice"])
        mark = float(p.get("markPrice", 0))
        upnl = float(p.get("unRealizedProfit", 0))
        lev = p.get("leverage", "?")
        margin = float(p.get("isolatedWallet", 0))
        side = "LONG" if amt > 0 else "SHORT"
        roi = (upnl / margin * 100) if margin > 0 else 0
        total_upnl += upnl
        total_margin += margin
        print(f"  {sym:15} {side:5} {lev:>4}x {entry:<12.6f} {mark:<12.6f} {margin:<8.4f} {upnl:>+10.4f} {roi:>+7.1f}%")
    print("  " + "-" * 115)
    print(f"  Toplam Pozisyon Margini: {total_margin:.4f} USDT")
    print(f"  Toplam Acik uPnL:       {total_upnl:>+.4f} USDT")
    print()
else:
    print("  Acik pozisyon yok.")
    print()

# ==================== REVERSE (YON DEGISIM) ANALIZI ====================
# Her coin icin userTrades cekip ardiisik yon degisimlerini tespit et
import time as _time

traded_symbols = set()
for e in events:
    if e["type"] in ("REALIZED_PNL", "COMMISSION") and e["symbol"]:
        traded_symbols.add(e["symbol"])
# Acik pozisyonlardaki sembolleri de ekle
for p in open_pos:
    traded_symbols.add(p["symbol"])

# Her sembol icin userTrades cek
all_user_trades = []
for sym in sorted(traded_symbols):
    try:
        params_ut = sign({"symbol": sym, "startTime": start_ms, "limit": 500})
        resp_ut = session.get(f"{base}/fapi/v1/userTrades", params=params_ut)
        trades = resp_ut.json()
        if isinstance(trades, list):
            all_user_trades.extend(trades)
        _time.sleep(0.1)  # rate limit
    except Exception:
        pass

# Pozisyon gecislerini tespit et: her sembol icin net pozisyon takip et
from collections import OrderedDict

symbol_transitions = defaultdict(list)  # sym -> [(time, from_side, to_side)]
symbol_positions = defaultdict(float)   # sym -> net qty (+ = long, - = short)

# Trades'i zamana gore sirala
all_user_trades.sort(key=lambda x: x.get("time", 0))

for t in all_user_trades:
    sym = t.get("symbol", "")
    qty = float(t.get("qty", 0))
    side = t.get("side", "")  # BUY or SELL
    tm = t.get("time", 0)

    old_pos = symbol_positions[sym]
    if side == "BUY":
        symbol_positions[sym] += qty
    else:
        symbol_positions[sym] -= qty
    new_pos = symbol_positions[sym]

    # Yon degisimi tespit: isaret degisti mi?
    old_side = "LONG" if old_pos > 1e-12 else ("SHORT" if old_pos < -1e-12 else "FLAT")
    new_side = "LONG" if new_pos > 1e-12 else ("SHORT" if new_pos < -1e-12 else "FLAT")

    if old_side != new_side and old_side != "FLAT" and new_side != "FLAT":
        symbol_transitions[sym].append({
            "time": tm,
            "from": old_side,
            "to": new_side
        })

# Reverse raporu
all_reverses = []
for sym, transitions in symbol_transitions.items():
    for tr in transitions:
        all_reverses.append({"symbol": sym, **tr})

all_reverses.sort(key=lambda x: x["time"])

print("=" * 80)
print("REVERSE (YON DEGISIM) ANALIZI")
print("=" * 80)
if all_reverses:
    print(f"  {'#':>3} | {'Tarih':14} | {'Sembol':15} | {'Gecis':20}")
    print("  " + "-" * 60)
    for i, r in enumerate(all_reverses):
        dt = datetime.fromtimestamp(r["time"] / 1000).strftime("%m-%d %H:%M:%S")
        arrow = f"{r['from']:>5} -> {r['to']:<5}"
        print(f"  {i+1:>3} | {dt:14} | {r['symbol']:15} | {arrow}")
    print()
    long_to_short = sum(1 for r in all_reverses if r["from"] == "LONG" and r["to"] == "SHORT")
    short_to_long = sum(1 for r in all_reverses if r["from"] == "SHORT" and r["to"] == "LONG")
    print(f"  Toplam Reverse:    {len(all_reverses)}")
    print(f"  LONG  -> SHORT:    {long_to_short}")
    print(f"  SHORT -> LONG:     {short_to_long}")
    # Hangi coinler en cok reverse yapti
    rev_count = defaultdict(int)
    for r in all_reverses:
        rev_count[r["symbol"]] += 1
    if any(v > 1 for v in rev_count.values()):
        print()
        print("  Cok Reverse Yapan Coinler:")
        for sym, cnt in sorted(rev_count.items(), key=lambda x: -x[1]):
            if cnt > 1:
                print(f"    {sym:15} {cnt} kez")
    print()
else:
    print("  Son 10 saatte reverse (yon degisimi) yok.")
    print()

# ==================== POZISYON ACILMA SAYISI ====================
# userTrades'den pozisyon acilis sayisini hesapla (FLAT -> LONG/SHORT gecisleri)
symbol_pos2 = defaultdict(float)
open_count = 0
open_details = {"LONG": 0, "SHORT": 0}

all_user_trades.sort(key=lambda x: x.get("time", 0))
for t in all_user_trades:
    sym = t.get("symbol", "")
    qty = float(t.get("qty", 0))
    side = t.get("side", "")

    old_pos = symbol_pos2[sym]
    if side == "BUY":
        symbol_pos2[sym] += qty
    else:
        symbol_pos2[sym] -= qty
    new_pos = symbol_pos2[sym]

    old_side = "LONG" if old_pos > 1e-12 else ("SHORT" if old_pos < -1e-12 else "FLAT")
    new_side = "LONG" if new_pos > 1e-12 else ("SHORT" if new_pos < -1e-12 else "FLAT")

    if old_side == "FLAT" and new_side != "FLAT":
        open_count += 1
        open_details[new_side] += 1

print(f"  Pozisyon Acilma:   {open_count} kez (LONG: {open_details['LONG']}, SHORT: {open_details['SHORT']})")
print()
