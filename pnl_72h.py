"""72 saatlik kapsamli trade analizi - P&L, likidasyonlar, SL/TP, sure, reverse, exit reason."""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
from dotenv import load_dotenv
import os, time, hmac, hashlib, requests, sqlite3
from urllib.parse import urlencode
from datetime import datetime, timedelta
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

# ══════════════════════════════════════════════════════════════
# PART 1: BINANCE INCOME HISTORY (72h)
# ══════════════════════════════════════════════════════════════
print("Binance income history çekiliyor (72 saat)...")
all_data = []
cursor_start = start_ms
for _ in range(20):  # 72h = daha fazla sayfa gerekebilir
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
    t = item.get("time", 0)
    typ = item.get("incomeType", "?")
    amt = float(item.get("income", 0))
    sym = item.get("symbol", "")
    events.append({"time": t, "type": typ, "amount": amt, "symbol": sym})

# Group trades by symbol + time window (5 min)
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

total_commission = sum(e["amount"] for e in events if e["type"] == "COMMISSION")
total_pnl = sum(e["amount"] for e in events if e["type"] == "REALIZED_PNL")
total_funding = sum(e["amount"] for e in events if e["type"] == "FUNDING_FEE")
total_insurance = sum(e["amount"] for e in events if e["type"] == "INSURANCE_CLEAR")

since = datetime.fromtimestamp(start_ms / 1000).strftime("%Y-%m-%d %H:%M")
now_str = datetime.now().strftime("%Y-%m-%d %H:%M")

print()
print(f"{'═' * 120}")
print(f"  SON {HOURS} SAAT KAPSAMLI TRADE RAPORU ({since} — {now_str})")
print(f"{'═' * 120}")
print()

# ── TRADE LISTESI ──
print(f"{'─' * 120}")
print(f"{'#':>3} | {'Tarih':14} | {'Sembol':15} | {'Brut K/Z':>10} | {'Fee':>10} | {'Likid.':>10} | {'Net K/Z':>10} | {'Durum':>8}")
print(f"{'─' * 120}")

win = loss = liq_count = 0
liq_total = 0.0
best_trade = worst_trade = None

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

net_total = total_pnl + total_commission + total_insurance + total_funding

print(f"{'═' * 120}")
print()
print("  GENEL OZET")
print(f"  {'─' * 55}")
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
print(f"  {'─' * 50}")
print(f"  NET TOPLAM:                {net_total:>+10.4f} USDT")
print()
if best_trade:
    print(f"  En Iyi Islem:   {best_trade['symbol']:15} {best_trade['net']:>+10.4f} USDT")
if worst_trade:
    print(f"  En Kotu Islem:  {worst_trade['symbol']:15} {worst_trade['net']:>+10.4f} USDT")
print()

# ══════════════════════════════════════════════════════════════
# PART 2: LOCAL DB DETAYLI ANALIZ (exit reason, süre, vb.)
# ══════════════════════════════════════════════════════════════
db_path = os.path.join(os.path.dirname(__file__), "data", "crypthos.db")
db_trades = []
if os.path.exists(db_path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    start_iso = datetime.fromtimestamp(start_ms / 1000).isoformat()
    cursor = conn.execute(
        "SELECT * FROM trades WHERE close_time >= ? ORDER BY close_time ASC",
        (start_iso,))
    db_trades = [dict(row) for row in cursor.fetchall()]
    conn.close()

if db_trades:
    print(f"{'═' * 120}")
    print(f"  LOCAL DB DETAYLI ANALIZ ({len(db_trades)} trade)")
    print(f"{'═' * 120}")
    print()

    # ── EXIT REASON BREAKDOWN ──
    exit_reasons = defaultdict(lambda: {"count": 0, "pnl": 0.0, "win": 0, "loss": 0})
    for t in db_trades:
        reason = t.get("exit_reason", "UNKNOWN") or "UNKNOWN"
        pnl = t.get("pnl_usdt", 0) or 0
        exit_reasons[reason]["count"] += 1
        exit_reasons[reason]["pnl"] += pnl
        if pnl >= 0:
            exit_reasons[reason]["win"] += 1
        else:
            exit_reasons[reason]["loss"] += 1

    print("  CIKIS NEDENI DAGILIMI")
    print(f"  {'─' * 85}")
    print(f"  {'Cikis Nedeni':30} | {'Adet':>5} | {'Kar':>4} | {'Zarar':>5} | {'WR%':>5} | {'Toplam PnL':>12}")
    print(f"  {'─' * 85}")
    for reason, d in sorted(exit_reasons.items(), key=lambda x: -x[1]["count"]):
        total = d["win"] + d["loss"]
        wr = d["win"] / total * 100 if total > 0 else 0
        print(f"  {reason:30} | {d['count']:>5} | {d['win']:>4} | {d['loss']:>5} | {wr:>5.1f} | {d['pnl']:>+12.4f}")
    print()

    # ── POZISYON SURESI ANALIZI ──
    hold_times = [t.get("hold_seconds", 0) or 0 for t in db_trades if (t.get("hold_seconds", 0) or 0) > 0]
    if hold_times:
        avg_hold = sum(hold_times) / len(hold_times)
        min_hold = min(hold_times)
        max_hold = max(hold_times)

        def fmt_dur(s):
            if s < 60: return f"{s:.0f}sn"
            if s < 3600: return f"{s/60:.1f}dk"
            return f"{s/3600:.1f}sa"

        # Kârlı vs zararlı süre karşılaştırması
        win_holds = [t.get("hold_seconds", 0) or 0 for t in db_trades
                     if (t.get("pnl_usdt", 0) or 0) >= 0 and (t.get("hold_seconds", 0) or 0) > 0]
        loss_holds = [t.get("hold_seconds", 0) or 0 for t in db_trades
                      if (t.get("pnl_usdt", 0) or 0) < 0 and (t.get("hold_seconds", 0) or 0) > 0]

        print("  POZISYON SURESI ANALIZI")
        print(f"  {'─' * 55}")
        print(f"  Ortalama:    {fmt_dur(avg_hold):>12}")
        print(f"  Minimum:     {fmt_dur(min_hold):>12}")
        print(f"  Maksimum:    {fmt_dur(max_hold):>12}")
        if win_holds:
            print(f"  Karli Ort:   {fmt_dur(sum(win_holds)/len(win_holds)):>12}  ({len(win_holds)} trade)")
        if loss_holds:
            print(f"  Zarari Ort:  {fmt_dur(sum(loss_holds)/len(loss_holds)):>12}  ({len(loss_holds)} trade)")
        print()

        # Süre dağılımı
        buckets = [(0, 300, "0-5dk"), (300, 1800, "5-30dk"), (1800, 3600, "30dk-1sa"),
                   (3600, 7200, "1-2sa"), (7200, 14400, "2-4sa"), (14400, 28800, "4-8sa"),
                   (28800, float('inf'), "8sa+")]
        print("  Süre Dağılımı:")
        for lo, hi, label in buckets:
            cnt = sum(1 for h in hold_times if lo <= h < hi)
            if cnt > 0:
                bar = "█" * min(cnt, 50)
                print(f"    {label:>10}: {cnt:>4}  {bar}")
        print()

    # ── LEVERAGE ANALIZI ──
    leverages = [t.get("leverage", 0) or 0 for t in db_trades if (t.get("leverage", 0) or 0) > 0]
    if leverages:
        avg_lev = sum(leverages) / len(leverages)
        print("  LEVERAGE ANALIZI")
        print(f"  {'─' * 55}")
        print(f"  Ortalama Kaldıraç:  {avg_lev:.1f}x")
        print(f"  Min / Max:          {min(leverages)}x / {max(leverages)}x")
        # Kaldıraç → win rate
        lev_buckets = defaultdict(lambda: {"win": 0, "loss": 0, "pnl": 0.0})
        for t in db_trades:
            lev = t.get("leverage", 0) or 0
            pnl = t.get("pnl_usdt", 0) or 0
            if lev > 0:
                bucket = f"{lev}x"
                if pnl >= 0:
                    lev_buckets[bucket]["win"] += 1
                else:
                    lev_buckets[bucket]["loss"] += 1
                lev_buckets[bucket]["pnl"] += pnl
        print(f"\n  {'Kaldıraç':>10} | {'Adet':>5} | {'WR%':>5} | {'PnL':>12}")
        print(f"  {'─' * 45}")
        for lev_str, d in sorted(lev_buckets.items(), key=lambda x: int(x[0].replace('x',''))):
            total = d["win"] + d["loss"]
            wr = d["win"] / total * 100 if total > 0 else 0
            print(f"  {lev_str:>10} | {total:>5} | {wr:>5.1f} | {d['pnl']:>+12.4f}")
        print()

    # ── SIDE (YÖN) ANALIZI ──
    side_stats = defaultdict(lambda: {"count": 0, "win": 0, "loss": 0, "pnl": 0.0})
    for t in db_trades:
        side = t.get("side", "unknown") or "unknown"
        pnl = t.get("pnl_usdt", 0) or 0
        side_stats[side]["count"] += 1
        side_stats[side]["pnl"] += pnl
        if pnl >= 0:
            side_stats[side]["win"] += 1
        else:
            side_stats[side]["loss"] += 1

    print("  YON ANALIZI (LONG vs SHORT)")
    print(f"  {'─' * 65}")
    print(f"  {'Yön':>10} | {'Adet':>5} | {'Kar':>4} | {'Zarar':>5} | {'WR%':>5} | {'PnL':>12}")
    print(f"  {'─' * 65}")
    for side, d in sorted(side_stats.items()):
        total = d["win"] + d["loss"]
        wr = d["win"] / total * 100 if total > 0 else 0
        print(f"  {side:>10} | {d['count']:>5} | {d['win']:>4} | {d['loss']:>5} | {wr:>5.1f} | {d['pnl']:>+12.4f}")
    print()

    # ── REGIME ANALIZI ──
    regime_stats = defaultdict(lambda: {"count": 0, "win": 0, "loss": 0, "pnl": 0.0})
    for t in db_trades:
        regime = t.get("entry_regime", "") or "N/A"
        pnl = t.get("pnl_usdt", 0) or 0
        regime_stats[regime]["count"] += 1
        regime_stats[regime]["pnl"] += pnl
        if pnl >= 0:
            regime_stats[regime]["win"] += 1
        else:
            regime_stats[regime]["loss"] += 1

    if any(k != "N/A" for k in regime_stats):
        print("  REJIM ANALIZI")
        print(f"  {'─' * 70}")
        print(f"  {'Rejim':>15} | {'Adet':>5} | {'Kar':>4} | {'Zarar':>5} | {'WR%':>5} | {'PnL':>12}")
        print(f"  {'─' * 70}")
        for regime, d in sorted(regime_stats.items(), key=lambda x: -x[1]["count"]):
            total = d["win"] + d["loss"]
            wr = d["win"] / total * 100 if total > 0 else 0
            print(f"  {regime:>15} | {d['count']:>5} | {d['win']:>4} | {d['loss']:>5} | {wr:>5.1f} | {d['pnl']:>+12.4f}")
        print()

    # ── TIMEFRAME ANALIZI ──
    tf_stats = defaultdict(lambda: {"count": 0, "win": 0, "loss": 0, "pnl": 0.0})
    for t in db_trades:
        tf = t.get("timeframe", "") or "N/A"
        pnl = t.get("pnl_usdt", 0) or 0
        tf_stats[tf]["count"] += 1
        tf_stats[tf]["pnl"] += pnl
        if pnl >= 0:
            tf_stats[tf]["win"] += 1
        else:
            tf_stats[tf]["loss"] += 1

    if any(k != "N/A" for k in tf_stats):
        print("  TIMEFRAME ANALIZI")
        print(f"  {'─' * 60}")
        print(f"  {'TF':>8} | {'Adet':>5} | {'Kar':>4} | {'Zarar':>5} | {'WR%':>5} | {'PnL':>12}")
        print(f"  {'─' * 60}")
        for tf, d in sorted(tf_stats.items(), key=lambda x: -x[1]["count"]):
            total = d["win"] + d["loss"]
            wr = d["win"] / total * 100 if total > 0 else 0
            print(f"  {tf:>8} | {d['count']:>5} | {d['win']:>4} | {d['loss']:>5} | {wr:>5.1f} | {d['pnl']:>+12.4f}")
        print()

    # ── DETAYLI TRADE LISTESI (DB) ──
    print(f"  DETAYLI TRADE LISTESI (Local DB)")
    print(f"  {'─' * 145}")
    print(f"  {'#':>3} | {'Kapanış':14} | {'Sembol':12} | {'Yön':>5} | {'Lev':>4} | {'PnL':>10} | {'ROI%':>7} | {'Süre':>8} | {'Çıkış Nedeni':25} | {'Rejim':>12}")
    print(f"  {'─' * 145}")
    for i, t in enumerate(db_trades):
        close = t.get("close_time", "")[:16] if t.get("close_time") else "?"
        sym = (t.get("symbol", "?") or "?")[:12]
        side = t.get("side", "?") or "?"
        lev = t.get("leverage", 0) or 0
        pnl = t.get("pnl_usdt", 0) or 0
        roi = t.get("roi_percent", 0) or 0
        hold = t.get("hold_seconds", 0) or 0
        reason = (t.get("exit_reason", "?") or "?")[:25]
        regime = (t.get("entry_regime", "") or "")[:12]
        hold_str = fmt_dur(hold) if hold > 0 else "?"
        print(f"  {i+1:>3} | {close:14} | {sym:12} | {side:>5} | {lev:>3}x | {pnl:>+10.4f} | {roi:>+6.1f}% | {hold_str:>8} | {reason:25} | {regime:>12}")
    print()

else:
    print("  Local DB'de bu dönem için trade kaydı bulunamadı.")
    print()

# ══════════════════════════════════════════════════════════════
# PART 3: COIN BAZLI OZET
# ══════════════════════════════════════════════════════════════
coin_pnl = defaultdict(lambda: {"net": 0, "count": 0, "win": 0, "loss": 0, "liq": 0})
for t in all_trades:
    coin_pnl[t["symbol"]]["net"] += t["net"]
    coin_pnl[t["symbol"]]["count"] += 1
    if t["is_liq"]:
        coin_pnl[t["symbol"]]["liq"] += 1
    elif t["net"] >= 0:
        coin_pnl[t["symbol"]]["win"] += 1
    else:
        coin_pnl[t["symbol"]]["loss"] += 1

if coin_pnl:
    print(f"{'═' * 80}")
    print("  COIN BAZLI OZET")
    print(f"  {'─' * 75}")
    print(f"  {'Sembol':15} | {'Islem':>5} | {'Kar':>3} | {'Zarar':>5} | {'Liq':>3} | {'WR%':>5} | {'Net K/Z':>12}")
    print(f"  {'─' * 75}")
    for sym, d in sorted(coin_pnl.items(), key=lambda x: x[1]["net"], reverse=True):
        total = d["win"] + d["loss"]
        wr = d["win"] / total * 100 if total > 0 else 0
        print(f"  {sym:15} | {d['count']:>5} | {d['win']:>3} | {d['loss']:>5} | {d['liq']:>3} | {wr:>5.1f} | {d['net']:>+12.4f}")
    print()

# ══════════════════════════════════════════════════════════════
# PART 4: SAATLIK P&L DAGILIMI
# ══════════════════════════════════════════════════════════════
print(f"{'═' * 80}")
print("  SAATLIK P&L DAGILIMI (6 saatlik bloklar)")
print(f"  {'─' * 65}")

# 6-saatlik bloklar
block_size = 6 * 3600 * 1000  # ms
blocks = defaultdict(lambda: {"pnl": 0.0, "count": 0, "win": 0, "loss": 0})
for t in all_trades:
    block_idx = (t["time"] - start_ms) // block_size
    block_start = start_ms + block_idx * block_size
    key = datetime.fromtimestamp(block_start / 1000).strftime("%m-%d %H:%M")
    blocks[key]["pnl"] += t["net"]
    blocks[key]["count"] += 1
    if t["net"] >= 0:
        blocks[key]["win"] += 1
    else:
        blocks[key]["loss"] += 1

print(f"  {'Blok':>14} | {'İşlem':>5} | {'K':>3}/{' Z':>3} | {'Net PnL':>12} | {'Grafik'}")
print(f"  {'─' * 65}")
for key in sorted(blocks.keys()):
    d = blocks[key]
    bar_len = int(abs(d["pnl"]) * 5)  # her 0.2 USDT = 1 karakter
    bar_len = min(bar_len, 30)
    if d["pnl"] >= 0:
        bar = "█" * bar_len
        print(f"  {key:>14} | {d['count']:>5} | {d['win']:>3}/{d['loss']:>3} | {d['pnl']:>+12.4f} | {bar}")
    else:
        bar = "░" * bar_len
        print(f"  {key:>14} | {d['count']:>5} | {d['win']:>3}/{d['loss']:>3} | {d['pnl']:>+12.4f} | {bar}")
print()

# ══════════════════════════════════════════════════════════════
# PART 5: REVERSE (YÖN DEĞİŞİM) ANALİZİ
# ══════════════════════════════════════════════════════════════
print("Reverse analizi için userTrades çekiliyor...")

traded_symbols = set()
for e in events:
    if e["type"] in ("REALIZED_PNL", "COMMISSION") and e["symbol"]:
        traded_symbols.add(e["symbol"])

all_user_trades = []
for sym in sorted(traded_symbols):
    try:
        params_ut = sign({"symbol": sym, "startTime": start_ms, "limit": 1000})
        resp_ut = session.get(f"{base}/fapi/v1/userTrades", params=params_ut)
        trades = resp_ut.json()
        if isinstance(trades, list):
            all_user_trades.extend(trades)
        time.sleep(0.1)
    except Exception:
        pass

# Pozisyon geçişlerini tespit
symbol_transitions = defaultdict(list)
symbol_positions = defaultdict(float)
all_user_trades.sort(key=lambda x: x.get("time", 0))

for t in all_user_trades:
    sym = t.get("symbol", "")
    qty = float(t.get("qty", 0))
    side = t.get("side", "")
    tm = t.get("time", 0)

    old_pos = symbol_positions[sym]
    if side == "BUY":
        symbol_positions[sym] += qty
    else:
        symbol_positions[sym] -= qty
    new_pos = symbol_positions[sym]

    old_side = "LONG" if old_pos > 1e-12 else ("SHORT" if old_pos < -1e-12 else "FLAT")
    new_side = "LONG" if new_pos > 1e-12 else ("SHORT" if new_pos < -1e-12 else "FLAT")

    if old_side != new_side and old_side != "FLAT" and new_side != "FLAT":
        symbol_transitions[sym].append({"time": tm, "from": old_side, "to": new_side})

all_reverses = []
for sym, transitions in symbol_transitions.items():
    for tr in transitions:
        all_reverses.append({"symbol": sym, **tr})
all_reverses.sort(key=lambda x: x["time"])

print()
print(f"{'═' * 80}")
print("  REVERSE (YÖN DEĞİŞİM) ANALİZİ")
print(f"{'═' * 80}")
if all_reverses:
    print(f"  {'#':>3} | {'Tarih':14} | {'Sembol':15} | {'Geçiş':20}")
    print(f"  {'─' * 60}")
    for i, r in enumerate(all_reverses):
        dt = datetime.fromtimestamp(r["time"] / 1000).strftime("%m-%d %H:%M:%S")
        arrow = f"{r['from']:>5} -> {r['to']:<5}"
        print(f"  {i+1:>3} | {dt:14} | {r['symbol']:15} | {arrow}")

    long_to_short = sum(1 for r in all_reverses if r["from"] == "LONG" and r["to"] == "SHORT")
    short_to_long = sum(1 for r in all_reverses if r["from"] == "SHORT" and r["to"] == "LONG")
    print()
    print(f"  Toplam Reverse:    {len(all_reverses)}")
    print(f"  LONG  -> SHORT:    {long_to_short}")
    print(f"  SHORT -> LONG:     {short_to_long}")

    rev_count = defaultdict(int)
    for r in all_reverses:
        rev_count[r["symbol"]] += 1
    if any(v > 1 for v in rev_count.values()):
        print()
        print("  Çok Reverse Yapan Coinler:")
        for sym, cnt in sorted(rev_count.items(), key=lambda x: -x[1]):
            if cnt > 1:
                print(f"    {sym:15} {cnt} kez")
else:
    print("  Son 72 saatte reverse (yön değişimi) yok.")
print()

# ── POZISYON AÇILMA SAYISI ──
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

print(f"  Pozisyon Açılma:   {open_count} kez (LONG: {open_details['LONG']}, SHORT: {open_details['SHORT']})")
print()

# ══════════════════════════════════════════════════════════════
# PART 6: HESAP DURUMU + AÇIK POZISYONLAR
# ══════════════════════════════════════════════════════════════
params2 = sign({"recvWindow": 5000})
resp2 = session.get(f"{base}/fapi/v2/account", params=params2)
acc = resp2.json()

wallet_balance = float(acc.get("totalWalletBalance", 0))
unrealized_pnl = float(acc.get("totalUnrealizedProfit", 0))
margin_balance = float(acc.get("totalMarginBalance", 0))
available_balance = float(acc.get("availableBalance", 0))
total_position_margin = float(acc.get("totalPositionInitialMargin", 0))

print(f"{'═' * 60}")
print("  HESAP DURUMU")
print(f"{'═' * 60}")
print(f"  Cüzdan Bakiyesi:           {wallet_balance:>10.4f} USDT")
print(f"  Kullanılabilir Bakiye:     {available_balance:>10.4f} USDT")
print(f"  Pozisyon Margini:          {total_position_margin:>10.4f} USDT")
print(f"  Toplam Margin Bakiyesi:    {margin_balance:>10.4f} USDT")
print(f"  Açık uPnL:                {unrealized_pnl:>+10.4f} USDT")
print(f"  {'─' * 50}")
realized_portfolio = wallet_balance + unrealized_pnl
print(f"  REALIZE EDİLİRSE PORTFÖY:  {realized_portfolio:>10.4f} USDT")
print()

# Açık pozisyonlar
params3 = sign({"recvWindow": 5000})
resp3 = session.get(f"{base}/fapi/v2/positionRisk", params=params3)
open_pos = [p for p in resp3.json() if float(p.get("positionAmt", 0)) != 0]

if open_pos:
    print("  AÇIK POZISYONLAR")
    print(f"  {'─' * 115}")
    print(f"  {'Sembol':15} {'Yön':5} {'Lev':>4} {'Entry':>12} {'Mark':>12} {'Margin':>8} {'uPnL':>10} {'ROI%':>8}")
    print(f"  {'─' * 115}")
    total_upnl = total_margin = 0
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
    print(f"  {'─' * 115}")
    print(f"  Toplam Margin: {total_margin:.4f}  |  Toplam uPnL: {total_upnl:>+.4f} USDT")
    print(f"  Açık Pozisyon Sayısı: {len(open_pos)}")
else:
    print("  Açık pozisyon yok.")
print()

# ══════════════════════════════════════════════════════════════
# PART 7: GUNLUK KARSILASTIRMA
# ══════════════════════════════════════════════════════════════
print(f"{'═' * 70}")
print("  GÜNLÜK KARŞILAŞTIRMA")
print(f"  {'─' * 65}")

day_pnl = defaultdict(lambda: {"pnl": 0.0, "count": 0, "win": 0, "loss": 0, "liq": 0})
for t in all_trades:
    day = datetime.fromtimestamp(t["time"] / 1000).strftime("%Y-%m-%d")
    day_pnl[day]["pnl"] += t["net"]
    day_pnl[day]["count"] += 1
    if t["is_liq"]:
        day_pnl[day]["liq"] += 1
    elif t["net"] >= 0:
        day_pnl[day]["win"] += 1
    else:
        day_pnl[day]["loss"] += 1

print(f"  {'Gün':>12} | {'İşlem':>5} | {'K':>3}/{' Z':>3}/{' L':>3} | {'WR%':>5} | {'Net PnL':>12}")
print(f"  {'─' * 65}")
for day in sorted(day_pnl.keys()):
    d = day_pnl[day]
    total = d["win"] + d["loss"]
    wr = d["win"] / total * 100 if total > 0 else 0
    print(f"  {day:>12} | {d['count']:>5} | {d['win']:>3}/{d['loss']:>3}/{d['liq']:>3} | {wr:>5.1f} | {d['pnl']:>+12.4f}")
print()

print(f"{'═' * 70}")
print("  72 SAAT RAPOR SONU")
print(f"{'═' * 70}")
