"""Test System G optimizer on top 10 coins."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import time
from datetime import datetime, timedelta, timezone
from backtest.data_fetcher import fetch_klines, get_top_symbols
from scanner.system_g_scanner import SystemGScanner

# Minimal config mock
class MockConfig:
    def __init__(self):
        import json
        with open("config.json") as f:
            self._cfg = json.load(f)
    def get(self, key, default=None):
        parts = key.split(".")
        val = self._cfg
        for p in parts:
            if isinstance(val, dict):
                val = val.get(p, None)
                if val is None:
                    return default
            else:
                return default
        return val

config = MockConfig()
scanner = SystemGScanner(config)

now = datetime.now(timezone.utc)
end_ms = int(now.timestamp() * 1000)
start_ms = int((now - timedelta(days=30)).timestamp() * 1000)
warmup_ms = 200 * 5 * 60000

print("=" * 90)
print("  SYSTEM G OPTIMIZER TEST — Top 10 coin")
print("=" * 90)
print()

# Get top coins
symbols = get_top_symbols(10)
print(f"Coinler: {', '.join(symbols)}\n")

results = []
for sym in symbols:
    print(f"[{sym}] 5m veri cekiliyor...", end=" ", flush=True)
    kl_5m = fetch_klines(sym, "5m", start_ms - warmup_ms, end_ms)
    print(f"{len(kl_5m)} mum.", end=" ", flush=True)

    if len(kl_5m) < 200:
        print("YETERSIZ")
        continue

    # Test LONG optimization
    t0 = time.time()
    opt_long = scanner.optimize_coin(sym, "LONG", kl_5m)
    t_long = time.time() - t0

    # Test SHORT optimization
    t0 = time.time()
    opt_short = scanner.optimize_coin(sym, "SHORT", kl_5m)
    t_short = time.time() - t0

    best = None
    best_dir = ""
    if opt_long and opt_short:
        best = opt_long if opt_long.score > opt_short.score else opt_short
        best_dir = "LONG" if opt_long.score > opt_short.score else "SHORT"
    elif opt_long:
        best = opt_long; best_dir = "LONG"
    elif opt_short:
        best = opt_short; best_dir = "SHORT"

    if best:
        sl_str = "YOK" if best.combo.sl_pct == 0 else f"{best.combo.sl_pct}%"
        print(f"-> {best_dir} {best.combo.leverage}x TP={best.combo.tp_pct}% SL={sl_str} "
              f"ROI={best.total_roi:+.1f}% WR={best.win_rate:.0f}% "
              f"LIQ={best.liq_rate*100:.0f}% ({best.trade_count}t) "
              f"score={best.score:.1f} [{t_long+t_short:.1f}s]")
        results.append({"sym": sym, "dir": best_dir, "opt": best,
                        "time": t_long + t_short})
    else:
        print(f"-> SKIP (optimizasyon basarisiz) [{t_long+t_short:.1f}s]")

# Summary
print(f"\n{'='*90}")
print(f"  {'Coin':<12} {'Yon':>5} {'Lev':>4} {'TP%':>5} {'SL':>5} "
      f"{'ROI%':>8} {'WR':>5} {'LIQ':>5} {'Trade':>5} {'Skor':>6} {'Sure':>5}")
print(f"{'='*90}")

for r in sorted(results, key=lambda x: -x["opt"].score):
    o = r["opt"]
    sl_str = "YOK" if o.combo.sl_pct == 0 else f"{o.combo.sl_pct}%"
    w = "+" if o.total_roi > 0 else ""
    print(f"  {r['sym']:<12} {r['dir']:>5} {o.combo.leverage:>4}x {o.combo.tp_pct:>4.1f}% "
          f"{sl_str:>5} {w}{o.total_roi:>7.1f}% {o.win_rate:>4.0f}% "
          f"{o.liq_rate*100:>4.0f}% {o.trade_count:>5} {o.score:>5.1f} "
          f"{r['time']:>4.1f}s")

if results:
    avg_time = sum(r["time"] for r in results) / len(results)
    print(f"\n  Ortalama optimizasyon suresi: {avg_time:.1f}s / coin")
print()
