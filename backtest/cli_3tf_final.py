"""System F final backtest: 3-TF (5m+1h+4h), 30 gun, tum 16 filtre."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from backtest.engine import BacktestEngine, BacktestConfig, DEFAULT_SF_PARAMS
import backtest.engine as eng
from datetime import datetime, timezone

# 3-TF config (production ayarlari ile ayni)
eng.DIRECTION_TFS = ["5m", "1h", "4h"]

sf = dict(DEFAULT_SF_PARAMS)
sf["vol_tf_min_count"] = 2  # 3 TF'nin 2'si

cfg = BacktestConfig(
    days_back=30,
    check_interval_min=5,   # 5dk hassasiyet (daha fazla kontrol noktasi)
    top_coins=30,            # 30 coin (genis tarama)
    lookback=200,
    min_tf_uyum=3,
    system_params=sf,
)

def progress(msg, pct):
    if int(pct * 100) % 10 == 0:
        print(f"  [{pct*100:.0f}%] {msg}", flush=True)

print("=" * 80)
print("  SYSTEM F FINAL BACKTEST — 3 TF (5m + 1h + 4h)")
print("  30 gun, 30 coin, 5dk aralik, tum 16 filtre")
print("=" * 80)
print()

engine = BacktestEngine(cfg, on_progress=progress)
result = engine.run()

print(f"\n{'=' * 80}")
print(f"  {result.total_checks:,} kontrol noktasi tarandi")
print(f"{'=' * 80}")

if result.trades:
    print(f"\n  {'#':>2} {'Tarih':>16} {'Coin':>12} {'Yon':>5} {'Lev':>4} "
          f"{'Giris':>10} {'Cikis':>10} {'Sebep':>10} {'Sure':>8} "
          f"{'ROI%':>8} {'Skor':>5} {'EV%':>6} {'P(w)':>5}")
    print(f"  {'-' * 115}")

    for i, t in enumerate(result.trades):
        dt = datetime.fromtimestamp(t.time_ms / 1000, tz=timezone.utc)
        w = "+" if t.roi_net > 0 else ""
        print(f"  {i+1:>2} {dt:%Y-%m-%d %H:%M} {t.symbol:>12} {t.direction:>5} "
              f"{t.leverage:>4}x {t.entry_price:>10.4f} {t.exit_price:>10.4f} "
              f"{t.exit_reason:>10} {t.hold_str:>8} "
              f"{w}{t.roi_net:>7.1f}% {t.score:>5.0f} {t.ev_pct:>5.1f}% {t.p_win:>4.0f}%")

    print(f"  {'-' * 115}")
    w = "+" if result.total_roi > 0 else ""
    print(f"  OZET: {result.total_trades} trade | "
          f"{result.win_count}W / {result.loss_count}L | "
          f"WR: {result.win_rate:.0f}% | "
          f"Toplam ROI: {w}{result.total_roi:.1f}% | "
          f"Ort ROI: {result.avg_roi:+.1f}%")

    # Coin bazli performans
    coin_stats = {}
    for t in result.trades:
        s = t.symbol
        if s not in coin_stats:
            coin_stats[s] = {"trades": 0, "wins": 0, "roi": 0.0}
        coin_stats[s]["trades"] += 1
        coin_stats[s]["roi"] += t.roi_net
        if t.roi_net > 0:
            coin_stats[s]["wins"] += 1

    print(f"\n  Coin bazli:")
    for sym, cs in sorted(coin_stats.items(), key=lambda x: -x[1]["roi"]):
        wr = cs["wins"] / cs["trades"] * 100 if cs["trades"] > 0 else 0
        w = "+" if cs["roi"] > 0 else ""
        print(f"    {sym:>12}: {cs['trades']} trade, "
              f"{cs['wins']}W/{cs['trades']-cs['wins']}L, "
              f"WR:{wr:.0f}%, ROI:{w}{cs['roi']:.1f}%")

    # Cikis sebepleri
    exit_stats = {}
    for t in result.trades:
        r = t.exit_reason
        if r not in exit_stats:
            exit_stats[r] = {"count": 0, "roi": 0.0}
        exit_stats[r]["count"] += 1
        exit_stats[r]["roi"] += t.roi_net

    print(f"\n  Cikis sebepleri:")
    for reason, es in sorted(exit_stats.items(), key=lambda x: -x[1]["count"]):
        w = "+" if es["roi"] > 0 else ""
        print(f"    {reason:>12}: {es['count']} trade, ROI:{w}{es['roi']:.1f}%")

else:
    print("\n  Sinyal bulunamadi.")

# Reject dagilimi
rs = result.reject_stats
if rs:
    total = sum(rs.values())
    print(f"\n  Red sebepleri ({total:,} toplam):")
    for reason, count in sorted(rs.items(), key=lambda x: -x[1])[:12]:
        pct = count / total * 100
        bar = "#" * int(pct / 2)
        print(f"    {reason:<18} {count:>8,} ({pct:>5.1f}%) {bar}")

print(f"\n{'=' * 80}")
print("  NOT: Orderbook filtresi atlanmistir (gecmis veri yok).")
print("       FR=0 varsayilmistir. Hacim spike 5m proxy.")
print(f"{'=' * 80}\n")
