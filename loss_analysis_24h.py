"""Son 24 saat detayli zarar analizi - kok nedenler, kaliplar, oneriler."""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
from dotenv import load_dotenv
import os, time, hmac, hashlib, requests, sqlite3
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
HOURS = 24
start_ms = int((time.time() - HOURS * 3600) * 1000)
start_iso = datetime.fromtimestamp(start_ms / 1000).isoformat()

# ══════════════════════════════════════════════════════════════
# LOCAL DB - Detayli trade bilgisi
# ══════════════════════════════════════════════════════════════
db_path = os.path.join(os.path.dirname(__file__), "data", "crypthos.db")
db_trades = []
if os.path.exists(db_path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.execute(
        "SELECT * FROM trades WHERE close_time >= ? ORDER BY close_time ASC",
        (start_iso,))
    db_trades = [dict(row) for row in cursor.fetchall()]
    conn.close()

# ══════════════════════════════════════════════════════════════
# BINANCE INCOME HISTORY (24h) - fee, funding vb.
# ══════════════════════════════════════════════════════════════
all_data = []
cursor_start = start_ms
for _ in range(10):
    params = sign({"incomeType": "", "limit": 1000, "startTime": cursor_start})
    resp = session.get(f"{base}/fapi/v1/income", params=params)
    page = resp.json()
    if not page:
        break
    all_data.extend(page)
    if len(page) < 1000:
        break
    cursor_start = page[-1]["time"] + 1
    time.sleep(0.05)

events = []
for item in all_data:
    events.append({
        "time": item.get("time", 0),
        "type": item.get("incomeType", "?"),
        "amount": float(item.get("income", 0)),
        "symbol": item.get("symbol", "")
    })

total_commission = sum(e["amount"] for e in events if e["type"] == "COMMISSION")
total_pnl = sum(e["amount"] for e in events if e["type"] == "REALIZED_PNL")
total_funding = sum(e["amount"] for e in events if e["type"] == "FUNDING_FEE")
total_insurance = sum(e["amount"] for e in events if e["type"] == "INSURANCE_CLEAR")

since = datetime.fromtimestamp(start_ms / 1000).strftime("%Y-%m-%d %H:%M")
now_str = datetime.now().strftime("%Y-%m-%d %H:%M")

print()
print(f"{'═' * 100}")
print(f"  SON 24 SAAT DETAYLI ZARAR ANALIZI ({since} — {now_str})")
print(f"  DB'de {len(db_trades)} trade, Binance'de {len(events)} income event")
print(f"{'═' * 100}")
print()

# Zararlı ve kârlı ayrımı
losses = [t for t in db_trades if (t.get("pnl_usdt", 0) or 0) < 0]
wins = [t for t in db_trades if (t.get("pnl_usdt", 0) or 0) >= 0]
total_loss_usdt = sum(t.get("pnl_usdt", 0) or 0 for t in losses)
total_win_usdt = sum(t.get("pnl_usdt", 0) or 0 for t in wins)

print(f"  GENEL BAKIS")
print(f"  {'─' * 60}")
print(f"  Toplam trade:   {len(db_trades)}  (Kar: {len(wins)}, Zarar: {len(losses)})")
if wins or losses:
    wr = len(wins) / (len(wins) + len(losses)) * 100
    print(f"  Win Rate:       {wr:.1f}%")
print(f"  Toplam Kar:     {total_win_usdt:>+10.4f} USDT")
print(f"  Toplam Zarar:   {total_loss_usdt:>+10.4f} USDT")
print(f"  Fee:            {total_commission:>+10.4f} USDT")
print(f"  Funding:        {total_funding:>+10.4f} USDT")
net = total_pnl + total_commission + total_insurance + total_funding
print(f"  NET TOPLAM:     {net:>+10.4f} USDT")
print()

if not losses:
    print("  Son 24 saatte zarar yok!")
    sys.exit(0)

# ══════════════════════════════════════════════════════════════
# 1) EXIT REASON BAZLI ZARAR ANALIZI
# ══════════════════════════════════════════════════════════════
print(f"  1) CIKIS NEDENI BAZLI ZARAR ANALIZI")
print(f"  {'─' * 90}")
print(f"  {'Çıkış Nedeni':30} | {'Zarar':>5} | {'Ort Zarar':>10} | {'Toplam':>10} | {'Ort ROI%':>8} | {'Ort Süre':>10}")
print(f"  {'─' * 90}")

exit_stats = defaultdict(lambda: {"count": 0, "pnl": 0.0, "roi_sum": 0.0, "hold_sum": 0.0})
for t in losses:
    reason = (t.get("exit_reason", "UNKNOWN") or "UNKNOWN")
    pnl = t.get("pnl_usdt", 0) or 0
    roi = t.get("roi_percent", 0) or 0
    hold = t.get("hold_seconds", 0) or 0
    exit_stats[reason]["count"] += 1
    exit_stats[reason]["pnl"] += pnl
    exit_stats[reason]["roi_sum"] += roi
    exit_stats[reason]["hold_sum"] += hold

def fmt_dur(s):
    if s < 60: return f"{s:.0f}sn"
    if s < 3600: return f"{s/60:.1f}dk"
    return f"{s/3600:.1f}sa"

for reason, d in sorted(exit_stats.items(), key=lambda x: x[1]["pnl"]):
    avg_pnl = d["pnl"] / d["count"]
    avg_roi = d["roi_sum"] / d["count"]
    avg_hold = d["hold_sum"] / d["count"]
    print(f"  {reason:30} | {d['count']:>5} | {avg_pnl:>+10.4f} | {d['pnl']:>+10.4f} | {avg_roi:>+7.1f}% | {fmt_dur(avg_hold):>10}")

print()

# ══════════════════════════════════════════════════════════════
# 2) STOP LOSS DETAYLI ANALIZI
# ══════════════════════════════════════════════════════════════
sl_trades = [t for t in losses if (t.get("exit_reason", "") or "") == "STOP_LOSS"]
if sl_trades:
    print(f"  2) STOP LOSS DETAYLI ANALIZI ({len(sl_trades)} trade)")
    print(f"  {'─' * 130}")
    print(f"  {'#':>3} | {'Kapanış':14} | {'Sembol':12} | {'Yön':>5} | {'Lev':>4} | {'PnL':>10} | {'ROI%':>7} | {'Süre':>8} | {'Entry':>12} | {'Exit':>12} | {'SL':>12} | {'Rejim':>15}")
    print(f"  {'─' * 130}")

    sl_roi_list = []
    sl_lev_list = []
    sl_hold_list = []
    sl_pnl_list = []

    for i, t in enumerate(sl_trades):
        close = (t.get("close_time", "") or "")[:16]
        sym = (t.get("symbol", "?") or "?")[:12]
        side = t.get("side", "?") or "?"
        lev = t.get("leverage", 0) or 0
        pnl = t.get("pnl_usdt", 0) or 0
        roi = t.get("roi_percent", 0) or 0
        hold = t.get("hold_seconds", 0) or 0
        entry_p = t.get("entry_price", 0) or 0
        exit_p = t.get("exit_price", 0) or 0
        sl_p = t.get("initial_sl", 0) or 0
        regime = (t.get("entry_regime", "") or "")[:15]
        hold_str = fmt_dur(hold) if hold > 0 else "?"

        sl_roi_list.append(roi)
        sl_lev_list.append(lev)
        sl_hold_list.append(hold)
        sl_pnl_list.append(pnl)

        print(f"  {i+1:>3} | {close:14} | {sym:12} | {side:>5} | {lev:>3}x | {pnl:>+10.4f} | {roi:>+6.1f}% | {hold_str:>8} | {entry_p:>12.6f} | {exit_p:>12.6f} | {sl_p:>12.6f} | {regime:>15}")

    print()
    print(f"  SL ISTATISTIKLERI:")
    print(f"  {'─' * 50}")
    print(f"  Toplam SL Zarar:     {sum(sl_pnl_list):>+10.4f} USDT")
    print(f"  Ort SL Zarar:        {sum(sl_pnl_list)/len(sl_pnl_list):>+10.4f} USDT")
    print(f"  Ort ROI:             {sum(sl_roi_list)/len(sl_roi_list):>+7.1f}%")
    print(f"  Ort Kaldıraç:        {sum(sl_lev_list)/len(sl_lev_list):>7.1f}x")
    valid_holds = [h for h in sl_hold_list if h > 0]
    if valid_holds:
        print(f"  Ort Süre:            {fmt_dur(sum(valid_holds)/len(valid_holds)):>10}")
        print(f"  Min/Max Süre:        {fmt_dur(min(valid_holds))} / {fmt_dur(max(valid_holds))}")

    # SL ROI clustering - are all SL hitting same ROI?
    print()
    print(f"  SL ROI DAGILIMI (SL hep aynı seviyede mi tetikleniyor?):")
    roi_buckets = defaultdict(int)
    for roi in sl_roi_list:
        bucket = round(roi / 5) * 5  # 5% aralıklarla
        roi_buckets[bucket] += 1
    for bucket in sorted(roi_buckets.keys()):
        bar = "█" * roi_buckets[bucket]
        print(f"    {bucket:>+6.0f}%: {roi_buckets[bucket]:>3}  {bar}")

    # SL by leverage
    print()
    print(f"  SL KALDIRAC vs ZARAR:")
    lev_sl = defaultdict(lambda: {"count": 0, "pnl": 0.0, "roi_sum": 0.0})
    for t in sl_trades:
        lev = t.get("leverage", 0) or 0
        pnl = t.get("pnl_usdt", 0) or 0
        roi = t.get("roi_percent", 0) or 0
        lev_sl[lev]["count"] += 1
        lev_sl[lev]["pnl"] += pnl
        lev_sl[lev]["roi_sum"] += roi
    print(f"    {'Lev':>5} | {'Adet':>5} | {'Toplam PnL':>12} | {'Ort ROI':>8}")
    print(f"    {'─' * 40}")
    for lev in sorted(lev_sl.keys()):
        d = lev_sl[lev]
        avg_roi = d["roi_sum"] / d["count"]
        print(f"    {lev:>4}x | {d['count']:>5} | {d['pnl']:>+12.4f} | {avg_roi:>+7.1f}%")

    # SL by regime
    print()
    print(f"  SL REJIM BAZLI:")
    reg_sl = defaultdict(lambda: {"count": 0, "pnl": 0.0})
    for t in sl_trades:
        regime = (t.get("entry_regime", "") or "N/A")
        pnl = t.get("pnl_usdt", 0) or 0
        reg_sl[regime]["count"] += 1
        reg_sl[regime]["pnl"] += pnl
    print(f"    {'Rejim':>20} | {'Adet':>5} | {'Toplam PnL':>12}")
    print(f"    {'─' * 45}")
    for regime, d in sorted(reg_sl.items(), key=lambda x: x[1]["pnl"]):
        print(f"    {regime:>20} | {d['count']:>5} | {d['pnl']:>+12.4f}")

    # SL by coin
    print()
    print(f"  SL COIN BAZLI (en çok SL yiyen coinler):")
    coin_sl = defaultdict(lambda: {"count": 0, "pnl": 0.0})
    for t in sl_trades:
        sym = t.get("symbol", "?") or "?"
        pnl = t.get("pnl_usdt", 0) or 0
        coin_sl[sym]["count"] += 1
        coin_sl[sym]["pnl"] += pnl
    print(f"    {'Coin':>15} | {'SL Adet':>7} | {'Toplam PnL':>12}")
    print(f"    {'─' * 45}")
    for sym, d in sorted(coin_sl.items(), key=lambda x: x[1]["pnl"]):
        print(f"    {sym:>15} | {d['count']:>7} | {d['pnl']:>+12.4f}")

    # SL süre analizi
    print()
    print(f"  SL SURE ANALIZI (ne kadar sürede SL'ye çarpıyor?):")
    dur_buckets = [(0, 60, "0-1dk"), (60, 300, "1-5dk"), (300, 1800, "5-30dk"),
                   (1800, 3600, "30dk-1sa"), (3600, 7200, "1-2sa"), (7200, float('inf'), "2sa+")]
    for lo, hi, label in dur_buckets:
        trades_in = [t for t in sl_trades if lo <= (t.get("hold_seconds", 0) or 0) < hi]
        if trades_in:
            total_p = sum(t.get("pnl_usdt", 0) or 0 for t in trades_in)
            print(f"    {label:>10}: {len(trades_in):>3} trade, {total_p:>+10.4f} USDT")
    print()

# ══════════════════════════════════════════════════════════════
# 3) EXTERNAL CLOSE ANALIZI
# ══════════════════════════════════════════════════════════════
ext_losses = [t for t in losses if (t.get("exit_reason", "") or "").startswith("external")]
if ext_losses:
    print(f"  3) EXTERNAL CLOSE ZARARLARI ({len(ext_losses)} trade)")
    print(f"  {'─' * 90}")
    print(f"  {'#':>3} | {'Kapanış':14} | {'Sembol':12} | {'Yön':>5} | {'Lev':>4} | {'PnL':>10} | {'ROI%':>7} | {'Süre':>8}")
    print(f"  {'─' * 90}")
    for i, t in enumerate(ext_losses):
        close = (t.get("close_time", "") or "")[:16]
        sym = (t.get("symbol", "?") or "?")[:12]
        side = t.get("side", "?") or "?"
        lev = t.get("leverage", 0) or 0
        pnl = t.get("pnl_usdt", 0) or 0
        roi = t.get("roi_percent", 0) or 0
        hold = t.get("hold_seconds", 0) or 0
        hold_str = fmt_dur(hold) if hold > 0 else "?"
        print(f"  {i+1:>3} | {close:14} | {sym:12} | {side:>5} | {lev:>3}x | {pnl:>+10.4f} | {roi:>+6.1f}% | {hold_str:>8}")
    ext_total = sum(t.get("pnl_usdt", 0) or 0 for t in ext_losses)
    print(f"\n  External Close Toplam Zarar: {ext_total:>+10.4f} USDT")
    print()

# ══════════════════════════════════════════════════════════════
# 4) ARDISIK ZARAR SERILERI
# ══════════════════════════════════════════════════════════════
print(f"  4) ARDISIK ZARAR SERILERI")
print(f"  {'─' * 70}")

streak = 0
current_pnl = 0.0
current_trades = []
streaks = []

for t in db_trades:
    pnl = t.get("pnl_usdt", 0) or 0
    if pnl < 0:
        streak += 1
        current_pnl += pnl
        current_trades.append(t)
    else:
        if streak >= 3:
            streaks.append((streak, current_pnl, list(current_trades)))
        streak = 0
        current_pnl = 0.0
        current_trades = []

if streak >= 3:
    streaks.append((streak, current_pnl, list(current_trades)))

if streaks:
    for i, (s, pnl, trades) in enumerate(streaks):
        coins = set((t.get("symbol", "") or "") for t in trades)
        start_t = (trades[0].get("close_time", "") or "")[:16]
        end_t = (trades[-1].get("close_time", "") or "")[:16]
        print(f"  Seri {i+1}: {s} ardışık zarar, {pnl:+.4f} USDT")
        print(f"    Zaman: {start_t} → {end_t}")
        print(f"    Coinler: {', '.join(sorted(coins))}")
        # Nedenler
        reasons = defaultdict(int)
        for t in trades:
            reasons[(t.get("exit_reason", "?") or "?")[:20]] += 1
        print(f"    Nedenler: {dict(reasons)}")
        print()
else:
    print(f"  3+ ardışık zarar serisi yok.")
    print()

# ══════════════════════════════════════════════════════════════
# 5) YÜKSEK KALDIRAC vs DÜŞÜK KALDIRAC
# ══════════════════════════════════════════════════════════════
print(f"  5) KALDIRAC BAZLI ZARAR PROFILI")
print(f"  {'─' * 80}")

high_lev = [t for t in db_trades if (t.get("leverage", 0) or 0) >= 20]
mid_lev = [t for t in db_trades if 5 <= (t.get("leverage", 0) or 0) < 20]
low_lev = [t for t in db_trades if 1 <= (t.get("leverage", 0) or 0) < 5]

for label, group in [("Yüksek (20x+)", high_lev), ("Orta (5-19x)", mid_lev), ("Düşük (1-4x)", low_lev)]:
    if not group:
        continue
    group_wins = [t for t in group if (t.get("pnl_usdt", 0) or 0) >= 0]
    group_losses = [t for t in group if (t.get("pnl_usdt", 0) or 0) < 0]
    total_g = sum(t.get("pnl_usdt", 0) or 0 for t in group)
    wr = len(group_wins) / len(group) * 100 if group else 0
    avg_roi = sum(t.get("roi_percent", 0) or 0 for t in group) / len(group) if group else 0
    print(f"  {label:>20}: {len(group):>3} trade, WR {wr:>5.1f}%, PnL {total_g:>+10.4f}, Ort ROI {avg_roi:>+6.1f}%")
print()

# ══════════════════════════════════════════════════════════════
# 6) COIN TEKRAR GIRISI ANALIZI (ping-pong)
# ══════════════════════════════════════════════════════════════
print(f"  6) COIN TEKRAR GIRISI (PING-PONG) ANALIZI")
print(f"  {'─' * 80}")

coin_trades = defaultdict(list)
for t in db_trades:
    sym = t.get("symbol", "") or ""
    coin_trades[sym].append(t)

ping_pong_coins = []
for sym, trades in coin_trades.items():
    if len(trades) >= 2:
        total_p = sum(t.get("pnl_usdt", 0) or 0 for t in trades)
        wins_c = sum(1 for t in trades if (t.get("pnl_usdt", 0) or 0) >= 0)
        losses_c = len(trades) - wins_c
        ping_pong_coins.append((sym, len(trades), wins_c, losses_c, total_p))

if ping_pong_coins:
    print(f"  {'Coin':>15} | {'Giriş':>5} | {'K':>3} | {'Z':>3} | {'Net PnL':>10} | Not")
    print(f"  {'─' * 70}")
    for sym, cnt, w, l, total_p in sorted(ping_pong_coins, key=lambda x: x[4]):
        note = "ZARARDA TEKRAR!" if l >= 2 and total_p < -0.1 else ""
        print(f"  {sym:>15} | {cnt:>5} | {w:>3} | {l:>3} | {total_p:>+10.4f} | {note}")
    print()

# ══════════════════════════════════════════════════════════════
# 7) SL TETIKLENME HIZI (erken mi geç mi?)
# ══════════════════════════════════════════════════════════════
print(f"  7) ZARAR PATTERN ANALIZI")
print(f"  {'─' * 80}")

# Hızlı SL (< 5dk)
fast_sl = [t for t in sl_trades if (t.get("hold_seconds", 0) or 0) < 300]
med_sl = [t for t in sl_trades if 300 <= (t.get("hold_seconds", 0) or 0) < 3600]
slow_sl = [t for t in sl_trades if (t.get("hold_seconds", 0) or 0) >= 3600]

print(f"  Hızlı SL (<5dk):    {len(fast_sl):>3} trade, {sum(t.get('pnl_usdt',0) or 0 for t in fast_sl):>+10.4f} USDT")
print(f"  Orta SL (5dk-1sa):  {len(med_sl):>3} trade, {sum(t.get('pnl_usdt',0) or 0 for t in med_sl):>+10.4f} USDT")
print(f"  Yavaş SL (1sa+):    {len(slow_sl):>3} trade, {sum(t.get('pnl_usdt',0) or 0 for t in slow_sl):>+10.4f} USDT")
print()

# En yüksek fiyat vs entry - SL'den önce ne kadar kâra çıkmıştı?
print(f"  SL ONCESI POTANSIYEL KAR ANALIZI (entry vs highest/lowest):")
print(f"  {'─' * 80}")
wasted_profit = 0
for t in sl_trades:
    entry = t.get("entry_price", 0) or 0
    highest = t.get("highest_price", 0) or 0
    lowest = t.get("lowest_price", 0) or 0
    side = t.get("side", "") or ""
    sym = (t.get("symbol", "") or "")[:12]
    lev = t.get("leverage", 0) or 0
    margin = t.get("margin_usdt", 0) or 0

    if entry <= 0 or margin <= 0:
        continue

    if "Long" in side or "Buy" in side:
        best_move_pct = (highest - entry) / entry * 100 if highest > entry else 0
    else:
        best_move_pct = (entry - lowest) / entry * 100 if lowest < entry and lowest > 0 else 0

    best_roi = best_move_pct * lev if lev > 0 else best_move_pct
    if best_roi > 5:  # %5 ROI'den fazla kara cikip SL yemis
        est_profit = margin * best_roi / 100
        wasted_profit += est_profit
        pnl = t.get("pnl_usdt", 0) or 0
        hold = t.get("hold_seconds", 0) or 0
        print(f"    {sym:>12} {side[:5]:>5} {lev:>3}x | En iyi ROI: {best_roi:>+7.1f}% | Sonuç: {pnl:>+.4f} | Süre: {fmt_dur(hold):>8}")

if wasted_profit > 0:
    print(f"\n  Kâra çıkıp SL yiyen işlemlerin potansiyel kârı: ~{wasted_profit:.4f} USDT")
print()

# ══════════════════════════════════════════════════════════════
# 8) FEE ETKİSİ
# ══════════════════════════════════════════════════════════════
print(f"  8) FEE ETKISI")
print(f"  {'─' * 50}")

# Binance events - fee kâr'dan büyük olanlar
symbol_binance = defaultdict(list)
for e in events:
    if e["type"] in ("REALIZED_PNL", "COMMISSION"):
        symbol_binance[e["symbol"]].append(e)

# Group and check
fee_killed_count = 0
fee_killed_total = 0.0
for sym, evts in symbol_binance.items():
    evts.sort(key=lambda x: x["time"])
    current_group = []
    for e in evts:
        if current_group and e["time"] - current_group[-1]["time"] > 300000:
            pnl = sum(x["amount"] for x in current_group if x["type"] == "REALIZED_PNL")
            fee = sum(x["amount"] for x in current_group if x["type"] == "COMMISSION")
            if pnl > 0 and pnl + fee < 0:
                fee_killed_count += 1
                fee_killed_total += pnl + fee
                dt = datetime.fromtimestamp(current_group[0]["time"]/1000).strftime("%m-%d %H:%M")
                print(f"    {dt} {sym:15} brut:{pnl:>+.4f} fee:{fee:>+.4f} net:{pnl+fee:>+.4f}  <- FEE ÖLDÜRDÜ")
            current_group = [e]
        else:
            current_group.append(e)
    if current_group:
        pnl = sum(x["amount"] for x in current_group if x["type"] == "REALIZED_PNL")
        fee = sum(x["amount"] for x in current_group if x["type"] == "COMMISSION")
        if pnl > 0 and pnl + fee < 0:
            fee_killed_count += 1
            fee_killed_total += pnl + fee
            dt = datetime.fromtimestamp(current_group[0]["time"]/1000).strftime("%m-%d %H:%M")
            print(f"    {dt} {sym:15} brut:{pnl:>+.4f} fee:{fee:>+.4f} net:{pnl+fee:>+.4f}  <- FEE ÖLDÜRDÜ")

print(f"\n  Fee'nin öldürdüğü kârlı işlemler: {fee_killed_count}")
print(f"  Fee kaynaklı net zarar:           {fee_killed_total:>+.4f} USDT")
print(f"  Toplam komisyon:                  {total_commission:>+.4f} USDT")
print(f"  Funding geliri:                   {total_funding:>+.4f} USDT")
print()

# ══════════════════════════════════════════════════════════════
# 9) SAAT BAZLI ZARAR YOGUNLUGU
# ══════════════════════════════════════════════════════════════
print(f"  9) SAAT BAZLI ZARAR YOGUNLUGU")
print(f"  {'─' * 70}")

hour_stats = defaultdict(lambda: {"count": 0, "loss_count": 0, "pnl": 0.0})
for t in db_trades:
    close_time = t.get("close_time", "") or ""
    try:
        h = datetime.fromisoformat(close_time).hour
    except Exception:
        continue
    pnl = t.get("pnl_usdt", 0) or 0
    hour_stats[h]["count"] += 1
    hour_stats[h]["pnl"] += pnl
    if pnl < 0:
        hour_stats[h]["loss_count"] += 1

print(f"  {'Saat':>5} | {'İşlem':>5} | {'Zarar':>5} | {'Net PnL':>10} | Grafik")
print(f"  {'─' * 60}")
for h in sorted(hour_stats.keys()):
    d = hour_stats[h]
    if d["pnl"] < 0:
        bar = "░" * min(int(abs(d["pnl"]) * 10), 30)
    else:
        bar = "█" * min(int(d["pnl"] * 10), 30)
    print(f"  {h:>5} | {d['count']:>5} | {d['loss_count']:>5} | {d['pnl']:>+10.4f} | {bar}")
print()

# ══════════════════════════════════════════════════════════════
# SONUC: KOK NEDEN SIRALAMASL
# ══════════════════════════════════════════════════════════════
print(f"{'═' * 100}")
print(f"  KÖK NEDEN ANALIZİ — ZARAR KAYNAKLARI SIRALAMASI")
print(f"{'═' * 100}")
print()

causes = []

# SL zarar
sl_total = sum(t.get("pnl_usdt", 0) or 0 for t in sl_trades)
if sl_total < 0:
    sl_avg_roi = sum(t.get("roi_percent", 0) or 0 for t in sl_trades) / len(sl_trades) if sl_trades else 0
    causes.append((abs(sl_total), f"STOP LOSS ({len(sl_trades)} trade, ort ROI {sl_avg_roi:+.1f}%): {sl_total:+.4f} USDT",
                   "SL seviyesi çok dar veya giriş zamanlaması kötü. Tüm SL'ler ~-34% ROI ile kapanıyor."))

# External close zarar
ext_loss_total = sum(t.get("pnl_usdt", 0) or 0 for t in ext_losses)
if ext_loss_total < 0:
    causes.append((abs(ext_loss_total), f"EXTERNAL CLOSE ({len(ext_losses)} trade): {ext_loss_total:+.4f} USDT",
                   "Binance tarafında kapatılan/timeout olan işlemler."))

# Fee
if total_commission < 0:
    causes.append((abs(total_commission), f"KOMISYON (fee): {total_commission:+.4f} USDT",
                   "Yüksek kaldıraç + sık trade = yüksek fee yükü."))

# Yüksek kaldıraç
high_lev_total = sum(t.get("pnl_usdt", 0) or 0 for t in high_lev)
if high_lev_total < 0:
    causes.append((abs(high_lev_total), f"YÜKSEK KALDIRAC 20x+ ({len(high_lev)} trade): {high_lev_total:+.4f} USDT",
                   "Yüksek kaldıraç SL'yi entry'ye çok yakın koyuyor, küçük fiyat hareketinde bile SL tetikleniyor."))

# Ping-pong coinler
for sym, cnt, w, l, total_p in ping_pong_coins:
    if l >= 3 and total_p < -0.5:
        causes.append((abs(total_p), f"PING-PONG: {sym} ({cnt} giriş, {l} zarar): {total_p:+.4f} USDT",
                       "Aynı coin'e tekrar tekrar girip zarar alınıyor."))

# Hızlı SL
fast_sl_total = sum(t.get("pnl_usdt", 0) or 0 for t in fast_sl)
if fast_sl_total < 0 and len(fast_sl) >= 2:
    causes.append((abs(fast_sl_total), f"HIZLI SL <5dk ({len(fast_sl)} trade): {fast_sl_total:+.4f} USDT",
                   "Giriş anında fiyat hemen ters yönde hareket ediyor — giriş zamanlaması sorunu."))

causes.sort(reverse=True)
for i, (amt, desc, explanation) in enumerate(causes):
    print(f"  {i+1}. {desc}")
    print(f"     → {explanation}")
    print()

print(f"{'═' * 100}")
print(f"  RAPOR SONU")
print(f"{'═' * 100}")
