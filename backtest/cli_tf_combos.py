"""Test all 3-TF combinations to find optimal set for System F."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from itertools import combinations
from backtest.engine import (
    BacktestEngine, BacktestConfig, DEFAULT_SF_PARAMS,
)
import backtest.engine as eng

ALL_TFS = ["5m", "15m", "1h", "4h", "1d"]
COMBOS = list(combinations(ALL_TFS, 3))

sf = dict(DEFAULT_SF_PARAMS)

results = []

print(f"=== 3-TF Kombinasyon Testi (30 gun, 15 coin) ===")
print(f"Toplam {len(COMBOS)} kombinasyon test edilecek\n")

for i, combo in enumerate(COMBOS):
    tf_list = list(combo)
    label = "+".join(tf_list)

    # Override direction TFs
    eng.DIRECTION_TFS = tf_list

    cfg = BacktestConfig(
        days_back=30,
        check_interval_min=15,
        top_coins=15,
        lookback=200,
        min_tf_uyum=3,
        system_params=dict(sf),
    )

    print(f"[{i+1}/{len(COMBOS)}] {label} ...", end=" ", flush=True)

    engine = BacktestEngine(cfg)
    result = engine.run()

    total = result.total_trades
    wins = result.win_count
    roi = result.total_roi
    avg = result.avg_roi
    wr = result.win_rate

    results.append({
        "combo": label,
        "tfs": tf_list,
        "trades": total,
        "wins": wins,
        "losses": result.loss_count,
        "wr": wr,
        "total_roi": roi,
        "avg_roi": avg,
        "trades_detail": result.trades,
    })

    if total > 0:
        print(f"{total} trade, {wins}W/{result.loss_count}L, "
              f"WR:{wr:.0f}%, ROI:{roi:+.1f}%, Avg:{avg:+.1f}%")
    else:
        print("0 trade")

# ═══ Summary ═══
print(f"\n{'='*85}")
print(f"{'Kombinasyon':<20} {'Trade':>5} {'W':>3} {'L':>3} {'WR%':>5} "
      f"{'TopROI%':>8} {'AvgROI%':>8} {'Detay'}")
print(f"{'='*85}")

# Sort by total ROI
results.sort(key=lambda x: x["total_roi"], reverse=True)

for r in results:
    detail = ""
    for t in r["trades_detail"][:5]:
        sym = t.symbol.replace("USDT", "")
        w = "+" if t.roi_net > 0 else ""
        detail += f" {sym}:{w}{t.roi_net:.0f}%"

    w = "+" if r["total_roi"] > 0 else ""
    print(f"{r['combo']:<20} {r['trades']:>5} {r['wins']:>3} {r['losses']:>3} "
          f"{r['wr']:>4.0f}% {w}{r['total_roi']:>7.1f}% {r['avg_roi']:>+7.1f}% "
          f"{detail}")

# Best combo
if results:
    best = results[0]
    print(f"\n{'='*85}")
    print(f"EN IYI: {best['combo']} -> {best['trades']} trade, "
          f"WR:{best['wr']:.0f}%, Toplam ROI: {best['total_roi']:+.1f}%")

    if best["trades_detail"]:
        print(f"\nDetayli trade listesi:")
        from datetime import datetime, timezone
        for t in best["trades_detail"]:
            dt = datetime.fromtimestamp(t.time_ms/1000, tz=timezone.utc)
            w = "+" if t.roi_net > 0 else ""
            print(f"  {dt:%Y-%m-%d %H:%M} {t.symbol:>12} {t.direction:>5} "
                  f"{t.leverage:>3}x {t.exit_reason:>10} {t.hold_str:>8} "
                  f"{w}{t.roi_net:.1f}% skor:{t.score:.0f} ev:{t.ev_pct:.1f}%")

print()
