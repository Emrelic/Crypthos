"""Quick 3-TF backtest: 5m + 1h + 4h, 30 gun."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from backtest.engine import (
    BacktestEngine, BacktestConfig, DEFAULT_SF_PARAMS, DIRECTION_TFS,
)
import backtest.engine as eng
from datetime import datetime, timezone

# Override: sadece 3 TF kullan
eng.DIRECTION_TFS = ["5m", "1h", "4h"]

# Config
sf = dict(DEFAULT_SF_PARAMS)
sf["min_sinyal_gucu"] = 0.6
sf["volume_spike_required"] = True  # 5m proxy

cfg = BacktestConfig(
    days_back=30,
    check_interval_min=15,
    top_coins=15,
    lookback=200,
    min_tf_uyum=3,  # 3/3 TF uyumu
    system_params=sf,
)

def progress(msg, pct):
    print(f"  [{pct*100:.0f}%] {msg}", flush=True)

print("=== System F Backtest: 3 TF (5m + 1h + 4h), 30 gun ===\n")
engine = BacktestEngine(cfg, on_progress=progress)
result = engine.run()

print(f"\n{'='*80}")
print(f"  {result.total_checks} kontrol noktasi, {result.total_trades} trade")
print(f"{'='*80}")

if result.trades:
    print(f"\n  {'Tarih':>16} {'Coin':>10} {'Yon':>5} {'Lev':>4} "
          f"{'Giris':>10} {'Cikis':>10} {'Sebep':>10} {'Sure':>8} "
          f"{'ROI%':>8} {'Skor':>5} {'EV%':>6}")
    print(f"  {'-'*105}")
    for t in result.trades:
        dt = datetime.fromtimestamp(t.time_ms/1000, tz=timezone.utc)
        w = "+" if t.roi_net > 0 else ""
        print(f"  {dt:%Y-%m-%d %H:%M} {t.symbol:>10} {t.direction:>5} "
              f"{t.leverage:>4}x {t.entry_price:>10.2f} {t.exit_price:>10.2f} "
              f"{t.exit_reason:>10} {t.hold_str:>8} "
              f"{w}{t.roi_net:>7.1f}% {t.score:>5.0f} {t.ev_pct:>5.1f}%")
    print(f"  {'-'*105}")
    wr = result.win_rate
    print(f"  Toplam: {result.total_trades} trade, "
          f"{result.win_count}W/{result.loss_count}L, "
          f"WR: {wr:.0f}%, "
          f"Toplam ROI: {result.total_roi:+.1f}%, "
          f"Ort ROI: {result.avg_roi:+.1f}%")
else:
    print("\n  Sinyal bulunamadi.")

# Reject stats
rs = result.reject_stats
if rs:
    total = sum(rs.values())
    print(f"\n  Red Sebepleri:")
    for reason, count in sorted(rs.items(), key=lambda x: -x[1])[:10]:
        pct = count/total*100
        print(f"    {reason:<20} {count:>8} ({pct:>5.1f}%)")

print()
