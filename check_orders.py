"""Pozisyon vs Emir uyumsuzluk teşhis scripti"""
import sys, os, json

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.config_manager import ConfigManager
from market.binance_rest import BinanceRestClient

cfg = ConfigManager()
rest = BinanceRestClient(api_key=cfg.get_api_key(), api_secret=cfg.get_api_secret())

# 1. Açık pozisyonları al
all_pos = rest.get_positions()
open_pos = [p for p in all_pos if float(p.get("positionAmt", 0)) != 0]

print(f"\n{'='*60}")
print(f"AÇIK POZİSYONLAR: {len(open_pos)}")
print(f"{'='*60}")
for p in open_pos:
    amt = float(p["positionAmt"])
    side = "LONG" if amt > 0 else "SHORT"
    print(f"  {p['symbol']:15s} | {side:5s} | miktar={amt} | giriş={p.get('entryPrice','?')}")

# 2. Tüm açık emirleri al
all_orders = rest.get_all_open_orders_combined()
if all_orders is None:
    print("\n[HATA] Emir listesi alınamadı!")
    sys.exit(1)

print(f"\n{'='*60}")
print(f"AÇIK EMİRLER: {len(all_orders)}")
print(f"{'='*60}")

# Emir tipini belirle
def classify(order):
    known = {"STOP_MARKET", "TRAILING_STOP_MARKET", "TAKE_PROFIT_MARKET",
             "STOP", "TAKE_PROFIT", "MARKET", "LIMIT"}
    for field in ("orderType", "type", "origType", "algoOrderType"):
        c = order.get(field, "")
        if c in known:
            return c
    return order.get("type", "") or order.get("orderType", "")

for o in all_orders:
    otype = classify(o)
    sym = o.get("symbol", "?")
    side = o.get("side", "?")
    stop_price = o.get("stopPrice", o.get("triggerPrice", "?"))
    source = o.get("_source", "?")
    oid = o.get("orderId", o.get("algoId", o.get("strategyId", "?")))
    status = o.get("status", o.get("algoStatus", o.get("strategyStatus", "?")))
    print(f"  {sym:15s} | {otype:25s} | side={side:5s} | stop={stop_price} | kaynak={source} | id={oid} | status={status}")

# BCHUSDT detay
print(f"\n{'='*60}")
print(f"BCHUSDT EMİR DETAYLARI (tüm alanlar)")
print(f"{'='*60}")
bch_orders = [o for o in all_orders if o.get("symbol") == "BCHUSDT"]
for i, o in enumerate(bch_orders):
    print(f"\n--- Emir {i+1} ---")
    for k, v in sorted(o.items()):
        print(f"  {k}: {v}")

# Ayrıca regular orders vs algo orders ayrı kontrol
print(f"\n{'='*60}")
print(f"REGULAR vs ALGO AYRIM")
print(f"{'='*60}")
regular = [o for o in all_orders if o.get("_source") == "regular"]
algo = [o for o in all_orders if o.get("_source") == "algo"]
print(f"  Regular emirler: {len(regular)}")
for o in regular:
    print(f"    {o.get('symbol')} | {classify(o)} | id={o.get('orderId')}")
print(f"  Algo emirler: {len(algo)}")
for o in algo:
    print(f"    {o.get('symbol')} | {classify(o)} | id={o.get('algoId', o.get('strategyId'))}")

# Binance regular endpoint tek başına kontrol
print(f"\n{'='*60}")
print(f"SADECE REGULAR ENDPOINT (/fapi/v1/openOrders)")
print(f"{'='*60}")
try:
    regular_only = rest._signed_get("/fapi/v1/openOrders", {})
    if isinstance(regular_only, list):
        print(f"  Regular endpoint: {len(regular_only)} emir")
        for o in regular_only:
            print(f"    {o.get('symbol')} | {o.get('type')} | {o.get('origType')} | id={o.get('orderId')}")
    else:
        print(f"  Yanıt: {regular_only}")
except Exception as e:
    print(f"  Hata: {e}")

# BCHUSDT özel - symbol bazlı sorgu
print(f"\n{'='*60}")
print(f"BCHUSDT SYMBOL BAZLI SORGU")
print(f"{'='*60}")
try:
    bch_regular = rest._signed_get("/fapi/v1/openOrders", {"symbol": "BCHUSDT"})
    print(f"  Regular: {len(bch_regular) if isinstance(bch_regular, list) else bch_regular}")
    if isinstance(bch_regular, list):
        for o in bch_regular:
            print(f"    {o.get('type')} | {o.get('origType')} | stop={o.get('stopPrice')} | id={o.get('orderId')}")
except Exception as e:
    print(f"  Regular hata: {e}")
try:
    bch_algo = rest.get_algo_open_orders("BCHUSDT")
    print(f"  Algo: {len(bch_algo) if isinstance(bch_algo, list) else bch_algo}")
    if isinstance(bch_algo, list):
        for o in bch_algo:
            print(f"    {o.get('algoOrderType', o.get('type'))} | stop={o.get('triggerPrice', o.get('stopPrice'))} | id={o.get('algoId')}")
except Exception as e:
    print(f"  Algo hata: {e}")

# 3. Eşleştirme analizi
print(f"\n{'='*60}")
print(f"ANALİZ: Pozisyon başına emir eşleştirmesi")
print(f"{'='*60}")

pos_symbols = {p["symbol"] for p in open_pos}

# Emir map
order_map = {}
for o in all_orders:
    sym = o.get("symbol", "")
    if sym not in order_map:
        order_map[sym] = {"sl": 0, "trail": 0, "other": 0, "orders": []}
    otype = classify(o)
    order_map[sym]["orders"].append(otype)
    if otype == "STOP_MARKET":
        order_map[sym]["sl"] += 1
    elif otype in ("TRAILING_STOP_MARKET", "TAKE_PROFIT_MARKET"):
        order_map[sym]["trail"] += 1
    else:
        order_map[sym]["other"] += 1

for sym in sorted(pos_symbols):
    info = order_map.get(sym, {"sl": 0, "trail": 0, "other": 0, "orders": []})
    sl = info["sl"]
    trail = info["trail"]
    total = sl + trail + info["other"]
    status = "OK" if (sl == 1 and trail == 1) else "PROBLEM!"
    print(f"  {sym:15s} | SL={sl} | Trail/TP={trail} | Diğer={info['other']} | "
          f"Toplam={total} | {status}")
    if status == "PROBLEM!":
        if sl == 0:
            print(f"    >>> EKSİK: Stop Loss (STOP_MARKET) emri YOK!")
        if trail == 0:
            print(f"    >>> EKSİK: Trailing/TP emri YOK!")
        if sl > 1:
            print(f"    >>> FAZLA: {sl} adet SL emri var (1 olmalı)")
        if trail > 1:
            print(f"    >>> FAZLA: {trail} adet trailing/TP emri var (1 olmalı)")

# Orphan emirler (pozisyonsuz)
orphans = set(order_map.keys()) - pos_symbols
if orphans:
    print(f"\n  [UYARI] Pozisyonsuz emirler: {orphans}")

print(f"\n{'='*60}")
print(f"DETAYLI EMIR ANALIZI (SL/TP yüzdeleri + R:R)")
print(f"{'='*60}")
for p in open_pos:
    sym = p["symbol"]
    entry = float(p.get("entryPrice", 0))
    amt = float(p["positionAmt"])
    is_long = amt > 0
    direction = "LONG" if is_long else "SHORT"
    leverage = int(float(p.get("leverage", 1)))

    sym_orders = [o for o in all_orders if o.get("symbol") == sym]
    sl_order = next((o for o in sym_orders if classify(o) == "STOP_MARKET"), None)
    tp_order = next((o for o in sym_orders if classify(o) in ("TAKE_PROFIT_MARKET", "TRAILING_STOP_MARKET")), None)

    print(f"\n  {sym} | {direction} | giriş={entry} | kaldıraç={leverage}x | miktar={abs(amt)}")

    if sl_order:
        sl_price = float(sl_order.get("triggerPrice", sl_order.get("stopPrice", 0)))
        if entry > 0:
            if is_long:
                sl_pct = (entry - sl_price) / entry * 100
            else:
                sl_pct = (sl_price - entry) / entry * 100
            print(f"    SL: {sl_price} -> %{sl_pct:.2f} (zarar)")
    else:
        sl_pct = 0
        print(f"    SL: YOK!")

    tp_type = "?"
    if tp_order:
        tp_type = classify(tp_order)
        tp_price = float(tp_order.get("triggerPrice", tp_order.get("stopPrice", 0)))
        if entry > 0:
            if is_long:
                tp_pct = (tp_price - entry) / entry * 100
            else:
                tp_pct = (entry - tp_price) / entry * 100
            print(f"    TP: {tp_price} -> %{tp_pct:.2f} (kar) | tip={tp_type}")
    else:
        tp_pct = 0
        print(f"    TP: YOK!")

    if sl_pct > 0 and tp_pct > 0:
        rr = tp_pct / sl_pct
        print(f"    R:R = 1:{rr:.2f} {'OK' if rr >= 1.0 else 'KÖTÜ (TP < SL!)'}")

    # Emir tipi kontrolü
    if tp_type == "TAKE_PROFIT_MARKET":
        print(f"    [!] Sabit TP — trailing YOK (RANGING rejiminde normal)")
    elif tp_type == "TRAILING_STOP_MARKET":
        print(f"    [✓] Trailing stop aktif (TREND rejiminde normal)")

print(f"\n{'='*60}")
print(f"ÖZET: {len(open_pos)} pozisyon, {len(all_orders)} emir "
      f"(beklenen: {len(open_pos)*2})")
print(f"{'='*60}")
