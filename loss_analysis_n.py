"""System N - Son 72 saat zarar analizi."""
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
HOURS = 72
start_ms = int((time.time() - HOURS * 3600) * 1000)

# ==================== INCOME HISTORY ====================
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
    events.append({
        "time": item.get("time", 0),
        "type": item.get("incomeType", "?"),
        "amount": float(item.get("income", 0)),
        "symbol": item.get("symbol", "")
    })

# ==================== GROUP TRADES ====================
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
                all_trades.append({"time": t, "symbol": sym, "pnl": pnl, "fee": fee,
                                   "liq": liq, "net": pnl + fee + liq, "is_liq": liq < 0})
            current_group = [e]
        else:
            current_group.append(e)
    if current_group:
        pnl = sum(x["amount"] for x in current_group if x["type"] == "REALIZED_PNL")
        fee = sum(x["amount"] for x in current_group if x["type"] == "COMMISSION")
        liq = sum(x["amount"] for x in current_group if x["type"] == "INSURANCE_CLEAR")
        t = current_group[0]["time"]
        if pnl != 0 or liq != 0:
            all_trades.append({"time": t, "symbol": sym, "pnl": pnl, "fee": fee,
                               "liq": liq, "net": pnl + fee + liq, "is_liq": liq < 0})

all_trades.sort(key=lambda x: x["time"])

# ==================== TRADE HISTORY (for direction analysis) ====================
# Get user trades for all symbols that appear in income
trade_symbols = set(e["symbol"] for e in events if e["symbol"])
user_trades_by_sym = {}
for sym in trade_symbols:
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
    user_trades_by_sym[sym] = trades

# ==================== ANALYSIS ====================
print("=" * 90)
since = datetime.fromtimestamp(start_ms / 1000).strftime("%Y-%m-%d %H:%M")
now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
print(f"SYSTEM N - SON {HOURS} SAAT ZARAR ANALIZI ({since} - {now_str})")
print("=" * 90)

wins = [t for t in all_trades if t["net"] >= 0 and not t["is_liq"]]
losses = [t for t in all_trades if t["net"] < 0 and not t["is_liq"]]
liqs = [t for t in all_trades if t["is_liq"]]

total_win = sum(t["net"] for t in wins)
total_loss = sum(t["net"] for t in losses)
total_liq = sum(t["net"] for t in liqs)
total_fee = sum(t["fee"] for t in all_trades)
total_funding = sum(e["amount"] for e in events if e["type"] == "FUNDING_FEE")

print(f"\nToplam islem: {len(all_trades)} (Kar: {len(wins)}, Zarar: {len(losses)}, Likidasyon: {len(liqs)})")
if wins or losses:
    print(f"Win Rate: {len(wins)/(len(wins)+len(losses))*100:.1f}%")

# ==================== 1) TUM ISLEMLER TABLOSU ====================
print()
print("1) TUM ISLEMLER")
print("-" * 100)
print(f"{'#':>3} | {'Tarih':14} | {'Sembol':15} | {'Brut':>10} | {'Fee':>8} | {'Liq':>8} | {'Net':>10} | {'Durum':>7}")
print("-" * 100)
for i, t in enumerate(all_trades):
    dt = datetime.fromtimestamp(t["time"]/1000).strftime("%m-%d %H:%M:%S")
    liq_str = f"{t['liq']:>+8.4f}" if t["liq"] != 0 else f"{'':>8}"
    if t["is_liq"]:
        tag = "LIKIT!"
    elif t["net"] >= 0:
        tag = "KAR"
    else:
        tag = "ZARAR"
    print(f"{i+1:>3} | {dt:14} | {t['symbol']:15} | {t['pnl']:>+10.4f} | {t['fee']:>+8.4f} | {liq_str} | {t['net']:>+10.4f} | {tag:>7}")

# ==================== 2) ZARAR KAYNAKLARI ====================
print()
print("2) ZARAR KAYNAKLARI DAGILIMI")
print("-" * 60)
print(f"   Normal zarar (SL/sinyal):  {total_loss:>+10.4f} USDT  ({len(losses)} islem)")
print(f"   Likidasyonlar:             {total_liq:>+10.4f} USDT  ({len(liqs)} islem)")
print(f"   Toplam fee:                {total_fee:>+10.4f} USDT")
print(f"   Funding fee:               {total_funding:>+10.4f} USDT")
print(f"   Toplam kar:                {total_win:>+10.4f} USDT  ({len(wins)} islem)")
print(f"   ---")
net_total = total_win + total_loss + total_liq + total_fee + total_funding
print(f"   NET TOPLAM (her sey dahil): {net_total:>+10.4f} USDT")

# Zarar paylari
total_neg = abs(total_loss) + abs(total_liq) + abs(total_fee)
if total_neg > 0:
    print()
    print("   Zarar payi dagilimi:")
    print(f"     SL/Sinyal zarari:  %{abs(total_loss)/total_neg*100:.1f}  ({total_loss:+.4f})")
    print(f"     Likidasyon:        %{abs(total_liq)/total_neg*100:.1f}  ({total_liq:+.4f})")
    print(f"     Fee:               %{abs(total_fee)/total_neg*100:.1f}  ({total_fee:+.4f})")

# ==================== 3) COIN BAZLI ====================
print()
print("3) COIN BAZLI NET K/Z")
print("-" * 75)
coin_stats = defaultdict(lambda: {"net": 0, "win": 0, "loss": 0, "liq": 0, "fee": 0, "count": 0,
                                   "win_total": 0, "loss_total": 0, "liq_total": 0})
for t in all_trades:
    s = coin_stats[t["symbol"]]
    s["net"] += t["net"]
    s["fee"] += t["fee"]
    s["count"] += 1
    if t["is_liq"]:
        s["liq"] += 1
        s["liq_total"] += t["net"]
    elif t["net"] >= 0:
        s["win"] += 1
        s["win_total"] += t["net"]
    else:
        s["loss"] += 1
        s["loss_total"] += t["net"]

print(f"   {'Coin':15} {'Islem':>5} {'W':>3} {'L':>3} {'Liq':>3} {'Kar':>10} {'Zarar':>10} {'Net':>10}")
print("   " + "-" * 70)
for sym, d in sorted(coin_stats.items(), key=lambda x: x[1]["net"]):
    z = d["loss_total"] + d["liq_total"]
    print(f"   {sym:15} {d['count']:>5} {d['win']:>3} {d['loss']:>3} {d['liq']:>3} {d['win_total']:>+10.4f} {z:>+10.4f} {d['net']:>+10.4f}")

# ==================== 4) YON DEGISIM ANALIZI ====================
print()
print("4) YON DEGISIM ANALIZI (flip sayisi vs performans)")
print("-" * 75)

for sym in sorted(trade_symbols):
    trades = user_trades_by_sym.get(sym, [])
    if not trades:
        continue

    net_pos = 0.0
    prev_direction = None
    flips = []

    for t in sorted(trades, key=lambda x: x["time"]):
        qty = float(t["qty"])
        if t["side"] == "BUY":
            net_pos += qty
        else:
            net_pos -= qty

        if abs(net_pos * float(t["price"])) < 0.01:
            cur_dir = "FLAT"
        elif net_pos > 0:
            cur_dir = "LONG"
        else:
            cur_dir = "SHORT"

        if cur_dir != "FLAT" and cur_dir != prev_direction:
            ts = datetime.fromtimestamp(t["time"]/1000).strftime("%m-%d %H:%M")
            flips.append((ts, cur_dir))

        prev_direction = cur_dir if cur_dir != "FLAT" else None

    sym_net = coin_stats.get(sym, {}).get("net", 0)
    sym_count = coin_stats.get(sym, {}).get("count", 0)
    if sym_count == 0 and not flips:
        continue

    longs = sum(1 for _, d in flips if d == "LONG")
    shorts = sum(1 for _, d in flips if d == "SHORT")
    actual_flips = 0
    for i in range(1, len(flips)):
        if flips[i][1] != flips[i-1][1]:
            actual_flips += 1

    print(f"   {sym:15} giris:{len(flips):>2} (L:{longs} S:{shorts}) flip:{actual_flips:>2}  net:{sym_net:>+.4f}  islem:{sym_count}")

# ==================== 5) FLIP SONRASI ZARAR ====================
print()
print("5) FLIP (YON DEGISIMI) SONRASI PERFORMANS")
print("-" * 75)

total_flip_loss = 0
total_flip_win = 0
total_flip_loss_amt = 0
total_flip_win_amt = 0

for sym in sorted(trade_symbols):
    trades = user_trades_by_sym.get(sym, [])
    if not trades:
        continue

    # Track direction changes
    net_pos = 0.0
    prev_direction = None
    direction_entries = []  # (time, direction)

    for t in sorted(trades, key=lambda x: x["time"]):
        qty = float(t["qty"])
        if t["side"] == "BUY":
            net_pos += qty
        else:
            net_pos -= qty

        if abs(net_pos * float(t["price"])) < 0.01:
            cur_dir = "FLAT"
        elif net_pos > 0:
            cur_dir = "LONG"
        else:
            cur_dir = "SHORT"

        if cur_dir != "FLAT" and cur_dir != prev_direction:
            direction_entries.append((t["time"], cur_dir))
        prev_direction = cur_dir if cur_dir != "FLAT" else None

    # Match direction entries with realized trades
    sym_trades = [t for t in all_trades if t["symbol"] == sym]
    sym_trades.sort(key=lambda x: x["time"])

    for i in range(1, len(direction_entries)):
        entry_time = direction_entries[i][0]
        new_dir = direction_entries[i][1]
        prev_dir = direction_entries[i-1][1]

        if new_dir == prev_dir:
            continue  # not a real flip

        # Find the trade that happened just before this flip (closing old direction)
        close_trade = None
        for t in sym_trades:
            if abs(t["time"] - entry_time) < 60000:  # within 1 minute
                close_trade = t
                break

        if close_trade:
            dt = datetime.fromtimestamp(entry_time/1000).strftime("%m-%d %H:%M")
            status = "KAR" if close_trade["net"] >= 0 else "ZARAR"
            print(f"   {dt} {sym:15} {prev_dir:5}->{new_dir:5}  kapanis:{close_trade['net']:>+.4f}  {status}")
            if close_trade["net"] < 0:
                total_flip_loss += 1
                total_flip_loss_amt += close_trade["net"]
            else:
                total_flip_win += 1
                total_flip_win_amt += close_trade["net"]

print(f"\n   Flip sonrasi: {total_flip_win} kar ({total_flip_win_amt:+.4f}), {total_flip_loss} zarar ({total_flip_loss_amt:+.4f})")

# ==================== 6) KISA OMURLU ISLEMLER ====================
print()
print("6) KISA OMURLU ISLEMLER (<2 saat)")
print("-" * 75)
sym_trades_grouped = defaultdict(list)
for t in all_trades:
    sym_trades_grouped[t["symbol"]].append(t)

short_count = 0
short_loss = 0.0
short_win = 0.0
for sym, trades in sym_trades_grouped.items():
    trades.sort(key=lambda x: x["time"])
    for i in range(len(trades)):
        if i + 1 < len(trades):
            dur_min = (trades[i+1]["time"] - trades[i]["time"]) / 60000
        else:
            dur_min = None

        if dur_min is not None and dur_min < 120 and trades[i]["net"] < 0:
            short_count += 1
            short_loss += trades[i]["net"]
            dt = datetime.fromtimestamp(trades[i]["time"]/1000).strftime("%m-%d %H:%M")
            print(f"   {dt} {sym:15} {dur_min:>5.0f}dk  {trades[i]['net']:>+.4f}")

print(f"\n   Kisa omurlu zarar: {short_count} islem, toplam: {short_loss:+.4f} USDT")

# ==================== 7) ARDISIK ZARAR ====================
print()
print("7) ARDISIK ZARAR SERILERI")
print("-" * 60)
streak = 0
max_streak = 0
max_streak_loss = 0
current_streak_loss = 0
streaks = []
current_streak_trades = []

for t in all_trades:
    if t["net"] < 0:
        streak += 1
        current_streak_loss += t["net"]
        current_streak_trades.append(t)
    else:
        if streak >= 2:
            streaks.append((streak, current_streak_loss, list(current_streak_trades)))
        if streak > max_streak:
            max_streak = streak
            max_streak_loss = current_streak_loss
        streak = 0
        current_streak_loss = 0
        current_streak_trades = []

if streak >= 2:
    streaks.append((streak, current_streak_loss, list(current_streak_trades)))
if streak > max_streak:
    max_streak = streak
    max_streak_loss = current_streak_loss

print(f"   Max ardisik zarar: {max_streak} islem ({max_streak_loss:+.4f} USDT)")
print(f"   2+ ardisik zarar serisi: {len(streaks)}")
for i, (s, l, trades) in enumerate(streaks):
    coins = ", ".join(set(t["symbol"] for t in trades))
    print(f"     Seri {i+1}: {s} islem, {l:+.4f} USDT  [{coins}]")

# ==================== 8) ZARAR BUYUKLUGU ====================
print()
print("8) ZARAR BUYUKLUGU DAGILIMI")
print("-" * 50)
loss_all = losses + liqs
if loss_all:
    loss_amounts = [abs(t["net"]) for t in loss_all]
    avg_loss = sum(loss_amounts) / len(loss_amounts)
    max_loss_amt = max(loss_amounts)
    avg_win = sum(t["net"] for t in wins) / len(wins) if wins else 0

    print(f"   Ortalama zarar:   {avg_loss:.4f} USDT")
    print(f"   Max zarar:        {max_loss_amt:.4f} USDT")
    print(f"   Ortalama kar:     {avg_win:.4f} USDT")
    if avg_win > 0 and avg_loss > 0:
        print(f"   Kar/Zarar orani:  {avg_win/avg_loss:.2f}x")

    ranges = [(0, 0.05), (0.05, 0.1), (0.1, 0.2), (0.2, 0.5), (0.5, 1.0), (1.0, 99)]
    print()
    print(f"   {'Aralik':20} {'Adet':>5}  {'Toplam':>10}")
    print("   " + "-" * 40)
    for lo, hi in ranges:
        in_range = [a for a in loss_amounts if lo <= a < hi]
        if in_range:
            label = f"{lo:.2f}-{hi:.2f}" if hi < 99 else f"{lo:.2f}+"
            print(f"   {label:20} {len(in_range):>5}  {sum(in_range):>+10.4f}")

# ==================== 9) FEE vs FUNDING ====================
print()
print("9) FEE vs FUNDING ANALIZI")
print("-" * 50)
print(f"   Toplam fee (komisyon):   {total_fee:>+.4f} USDT")
print(f"   Toplam funding geliri:   {total_funding:>+.4f} USDT")
print(f"   Net fee+funding:         {total_fee + total_funding:>+.4f} USDT")

fee_killed = [t for t in all_trades if t["pnl"] > 0 and t["net"] < 0]
print(f"   Fee yuzunden zarara donen: {len(fee_killed)} islem")
for t in fee_killed:
    dt = datetime.fromtimestamp(t["time"]/1000).strftime("%m-%d %H:%M")
    print(f"     {dt} {t['symbol']:15} brut:{t['pnl']:>+.4f} fee:{t['fee']:>+.4f} net:{t['net']:>+.4f}")

# ==================== 10) SAAT BAZLI ====================
print()
print("10) SAAT BAZLI PERFORMANS")
print("-" * 60)
hour_stats = defaultdict(lambda: {"count": 0, "net": 0, "loss_count": 0, "win_count": 0})
for t in all_trades:
    h = datetime.fromtimestamp(t["time"]/1000).hour
    hour_stats[h]["count"] += 1
    hour_stats[h]["net"] += t["net"]
    if t["net"] < 0:
        hour_stats[h]["loss_count"] += 1
    else:
        hour_stats[h]["win_count"] += 1

print(f"   {'Saat':>5} {'Islem':>5} {'Kar':>4} {'Zarar':>5} {'Net':>10}  Grafik")
print("   " + "-" * 55)
for h in sorted(hour_stats.keys()):
    d = hour_stats[h]
    if d["net"] < 0:
        bar = "X" * max(1, int(abs(d["net"]) * 10))
    else:
        bar = "+" * max(1, int(d["net"] * 10))
    print(f"   {h:>5} {d['count']:>5} {d['win_count']:>4} {d['loss_count']:>5} {d['net']:>+10.4f}  {bar}")

# ==================== SONUC ====================
print()
print("=" * 90)
print("SONUC: SYSTEM N EN BUYUK ZARAR KAYNAKLARI")
print("=" * 90)

causes = []
if total_loss < 0:
    causes.append((abs(total_loss), f"SL/Sinyal zarari: {total_loss:+.4f} USDT ({len(losses)} islem)"))
if total_liq < 0:
    causes.append((abs(total_liq), f"Likidasyonlar: {total_liq:+.4f} USDT ({len(liqs)} islem)"))
causes.append((abs(total_fee), f"Fee/Komisyon: {total_fee:+.4f} USDT"))
if short_loss < 0:
    causes.append((abs(short_loss), f"Kisa omurlu islem zarari (<2h): {short_loss:+.4f} USDT ({short_count} islem)"))
if total_flip_loss_amt < 0:
    causes.append((abs(total_flip_loss_amt), f"Flip (yon degisimi) zarari: {total_flip_loss_amt:+.4f} USDT ({total_flip_loss} islem)"))

# Worst coins
worst_coins = [(d["net"], sym) for sym, d in coin_stats.items() if d["net"] < -0.1]
worst_coins.sort()
if worst_coins:
    for net_val, sym in worst_coins[:3]:
        causes.append((abs(net_val), f"Sorunlu coin {sym}: {net_val:+.4f} USDT"))

causes.sort(reverse=True)
for i, (amt, desc) in enumerate(causes):
    print(f"   {i+1}. {desc}")
print()
