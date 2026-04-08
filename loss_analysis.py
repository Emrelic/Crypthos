"""Son 7 gun zarar analizi - nedenler ve kaliplar."""
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

# Son 7 gun income history
start_ms = int((time.time() - 7 * 24 * 3600) * 1000)
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

# Group into trades
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

# ============= ANALYSIS =============
print("=" * 80)
print("SON 7 GUN ZARAR ANALIZI")
print("=" * 80)

wins = [t for t in all_trades if t["net"] >= 0 and not t["is_liq"]]
losses = [t for t in all_trades if t["net"] < 0 and not t["is_liq"]]
liqs = [t for t in all_trades if t["is_liq"]]

total_win = sum(t["net"] for t in wins)
total_loss = sum(t["net"] for t in losses)
total_liq = sum(t["net"] for t in liqs)
total_fee = sum(t["fee"] for t in all_trades)

print(f"\nToplam islem: {len(all_trades)} (Kar: {len(wins)}, Zarar: {len(losses)}, Likidasyon: {len(liqs)})")
if wins or losses:
    print(f"Win Rate: {len(wins)/(len(wins)+len(losses))*100:.1f}%")
print()

# 1) Zarar kaynaklari
print("1) ZARAR KAYNAKLARI DAGILIMI")
print("-" * 50)
print(f"   Normal zarar (SL/sinyal):  {total_loss:>+10.4f} USDT  ({len(losses)} islem)")
print(f"   Likidasyonlar:             {total_liq:>+10.4f} USDT  ({len(liqs)} islem)")
print(f"   Toplam fee:                {total_fee:>+10.4f} USDT")
print(f"   Toplam kar:                {total_win:>+10.4f} USDT  ({len(wins)} islem)")
print(f"   ---")
grand = total_win + total_loss + total_liq
print(f"   Brut K/Z (fee haric):      {grand:>+10.4f} USDT")
net_total = grand + total_fee
print(f"   Net K/Z (fee dahil):       {net_total:>+10.4f} USDT")
if grand != 0:
    fee_pct = abs(total_fee / abs(grand)) * 100
    print(f"   Fee / |Brut K/Z| orani:    %{fee_pct:.1f}")

# 2) Likidasyonlar
print()
print("2) LIKIDASYON DETAYLARI")
print("-" * 50)
if liqs:
    for t in liqs:
        dt = datetime.fromtimestamp(t["time"]/1000).strftime("%m-%d %H:%M")
        print(f"   {dt}  {t['symbol']:15}  net: {t['net']:>+.4f}  liq: {t['liq']:>+.4f}")
    print(f"   TOPLAM LIKIDASYON ZARARI: {total_liq:>+.4f} USDT")
else:
    print("   Likidasyon yok")

# 3) Coin bazli
print()
print("3) COIN BAZLI NET K/Z (zarara gore sirali)")
print("-" * 70)
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

hdr = f"   {'Coin':15} {'Islem':>5} {'W':>3} {'L':>3} {'Liq':>3} {'Kar':>10} {'Zarar':>10} {'Net':>10}"
print(hdr)
print("   " + "-" * 65)
for sym, d in sorted(coin_stats.items(), key=lambda x: x[1]["net"]):
    z = d["loss_total"] + d["liq_total"]
    print(f"   {sym:15} {d['count']:>5} {d['win']:>3} {d['loss']:>3} {d['liq']:>3} {d['win_total']:>+10.4f} {z:>+10.4f} {d['net']:>+10.4f}")

# 4) Zarar buyuklugu
print()
print("4) ZARAR BUYUKLUGU DAGILIMI")
print("-" * 50)
loss_all = losses + liqs
if loss_all:
    loss_amounts = [abs(t["net"]) for t in loss_all]
    avg_loss = sum(loss_amounts) / len(loss_amounts)
    max_loss = max(loss_amounts)
    min_loss = min(loss_amounts)
    avg_win = sum(t["net"] for t in wins) / len(wins) if wins else 0

    print(f"   Ortalama zarar:   {avg_loss:.4f} USDT")
    print(f"   Max zarar:        {max_loss:.4f} USDT")
    print(f"   Min zarar:        {min_loss:.4f} USDT")
    print(f"   Ortalama kar:     {avg_win:.4f} USDT")
    if avg_win > 0 and avg_loss > 0:
        print(f"   Kar/Zarar orani:  {avg_win/avg_loss:.2f}x")

    # Buyukluk araliklari
    ranges = [(0, 0.05), (0.05, 0.1), (0.1, 0.2), (0.2, 0.5), (0.5, 1.0), (1.0, 99)]
    print()
    print("   Zarar araligi         Adet   Toplam")
    print("   " + "-" * 40)
    for lo, hi in ranges:
        in_range = [a for a in loss_amounts if lo <= a < hi]
        if in_range:
            label = f"{lo:.2f}-{hi:.2f}" if hi < 99 else f"{lo:.2f}+"
            print(f"   {label:20} {len(in_range):>5}   {sum(in_range):>+.4f}")

# 5) Saat bazli
print()
print("5) SAAT BAZLI ZARAR DAGILIMI")
print("-" * 60)
hour_stats = defaultdict(lambda: {"count": 0, "net": 0, "loss_count": 0})
for t in all_trades:
    h = datetime.fromtimestamp(t["time"]/1000).hour
    hour_stats[h]["count"] += 1
    hour_stats[h]["net"] += t["net"]
    if t["net"] < 0:
        hour_stats[h]["loss_count"] += 1

print(f"   {'Saat':>5} {'Islem':>5} {'Zarar':>5} {'Net':>10}  Grafik")
print("   " + "-" * 55)
for h in sorted(hour_stats.keys()):
    d = hour_stats[h]
    if d["net"] < 0:
        bar = "X" * max(1, int(abs(d["net"]) * 10))
    else:
        bar = "+" * max(1, int(d["net"] * 10))
    print(f"   {h:>5} {d['count']:>5} {d['loss_count']:>5} {d['net']:>+10.4f}  {bar}")

# 6) Ardisik zarar serileri
print()
print("6) ARDISIK ZARAR SERILERI")
print("-" * 50)
streak = 0
max_streak = 0
max_streak_loss = 0
current_streak_loss = 0
streaks = []
streak_details = []
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

print(f"   Max ardisik zarar serisi: {max_streak} islem ({max_streak_loss:+.4f} USDT)")
print(f"   2+ ardisik zarar sayisi:  {len(streaks)}")
for i, (s, l, trades) in enumerate(streaks):
    coins = ", ".join(set(t["symbol"] for t in trades))
    print(f"     Seri {i+1}: {s} islem, {l:+.4f} USDT  [{coins}]")

# 7) Kisa omurlu islemler
print()
print("7) KISA OMURLU ISLEMLER (< 2 saat, zarar)")
print("-" * 60)
sym_trades = defaultdict(list)
for t in all_trades:
    sym_trades[t["symbol"]].append(t)

short_count = 0
short_loss = 0.0
for sym, trades in sym_trades.items():
    trades.sort(key=lambda x: x["time"])
    for i in range(len(trades)):
        if i + 1 < len(trades):
            dur_min = (trades[i+1]["time"] - trades[i]["time"]) / 60000
            if dur_min < 120 and trades[i]["net"] < 0:
                short_count += 1
                short_loss += trades[i]["net"]
                dt = datetime.fromtimestamp(trades[i]["time"]/1000).strftime("%m-%d %H:%M")
                print(f"   {dt} {sym:15} {dur_min:>5.0f}dk  {trades[i]['net']:>+.4f}")

print(f"   Kisa omurlu zarar islem: {short_count}, toplam: {short_loss:+.4f} USDT")

# 8) Fee etkisi
print()
print("8) FEE ETKISI DETAY")
print("-" * 50)
fee_on_wins = sum(t["fee"] for t in wins)
fee_on_losses = sum(t["fee"] for t in losses)
fee_on_liqs = sum(t["fee"] for t in liqs)
fee_killed = [t for t in all_trades if t["pnl"] > 0 and t["net"] < 0]

print(f"   Kar islemlerde fee:    {fee_on_wins:>+.4f} USDT")
print(f"   Zarar islemlerde fee:  {fee_on_losses:>+.4f} USDT")
print(f"   Likidasyon fee:        {fee_on_liqs:>+.4f} USDT")
print(f"   Fee kardan buyuk (kar->zarar donusen): {len(fee_killed)} islem")
for t in fee_killed:
    dt = datetime.fromtimestamp(t["time"]/1000).strftime("%m-%d %H:%M")
    print(f"     {dt} {t['symbol']:15} brut:{t['pnl']:>+.4f} fee:{t['fee']:>+.4f} net:{t['net']:>+.4f}")

# Funding
total_funding = sum(e["amount"] for e in events if e["type"] == "FUNDING_FEE")
print()
print(f"   Funding geliri: {total_funding:>+.4f} USDT")

# 9) Yon degisimi sonrasi zarar
print()
print("9) YON DEGISIMI SONRASI PERFORMANS")
print("-" * 50)
# Get trades for top coins
top_coins = ["BNBUSDT", "ETHUSDT", "SOLUSDT", "HYPEUSDT", "ADAUSDT", "XRPUSDT", "ONTUSDT"]
for sym in top_coins:
    sym_t = [t for t in all_trades if t["symbol"] == sym]
    if len(sym_t) < 2:
        continue
    flip_losses = 0
    flip_wins = 0
    for i in range(1, len(sym_t)):
        gap_min = (sym_t[i]["time"] - sym_t[i-1]["time"]) / 60000
        if gap_min < 360:  # 6 saat icinde tekrar giris
            if sym_t[i]["net"] < 0:
                flip_losses += 1
            else:
                flip_wins += 1
    if flip_losses + flip_wins > 0:
        print(f"   {sym:15} Hizli tekrar giris: {flip_losses + flip_wins} ({flip_wins}W {flip_losses}L)")

print()
print("=" * 80)
print("SONUC: EN BUYUK ZARAR KAYNAKLARI")
print("=" * 80)

# Rank causes
causes = []
if total_liq < 0:
    causes.append((abs(total_liq), f"Likidasyonlar: {total_liq:+.4f} USDT ({len(liqs)} kez)"))
causes.append((abs(total_fee), f"Fee/Komisyon: {total_fee:+.4f} USDT"))

# Count flip losses
flip_loss_total = 0
for sym, trades in sym_trades.items():
    trades.sort(key=lambda x: x["time"])
    for i in range(1, len(trades)):
        gap_min = (trades[i]["time"] - trades[i-1]["time"]) / 60000
        if gap_min < 360 and trades[i]["net"] < 0:
            flip_loss_total += trades[i]["net"]
if flip_loss_total < 0:
    causes.append((abs(flip_loss_total), f"Hizli tekrar giris zarari: {flip_loss_total:+.4f} USDT"))

causes.append((abs(short_loss), f"Kisa omurlu islem zarari (<2h): {short_loss:+.4f} USDT"))

# BNB specific
bnb_net = coin_stats.get("BNBUSDT", {}).get("net", 0)
if bnb_net < 0:
    causes.append((abs(bnb_net), f"BNB tekrarli zarar: {bnb_net:+.4f} USDT"))

causes.sort(reverse=True)
for i, (amt, desc) in enumerate(causes):
    print(f"   {i+1}. {desc}")
print()
