"""Test individual filter relaxations to find safe improvements."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from backtest.engine import BacktestEngine, BacktestConfig, DEFAULT_SF_PARAMS
import backtest.engine as eng
from datetime import datetime, timezone

eng.DIRECTION_TFS = ["5m", "1h", "4h"]

BASE_SF = dict(DEFAULT_SF_PARAMS)
BASE_SF["vol_tf_min_count"] = 2

# Her test: (isim, degisen parametreler)
TESTS = [
    ("MEVCUT (baseline)", {}),
    ("MACD momentum=off", {"macd_momentum_required": False}),
    ("RSI 55/45", {"rsi_long_esik": 55, "rsi_short_esik": 45}),
    ("EMA gap 0.03%", {"ema_gap_min_pct": 0.03}),
    ("Min skor 75", {"min_skor": 75}),
    ("Min EV 10%", {"ev_min_pct": 10.0}),
    ("Hacim spike off", {"volume_spike_required": False}),
    ("P(SL) max 15%", {"p_sl_max_pct": 15.0}),
    ("MACD off + RSI 55/45", {"macd_momentum_required": False, "rsi_long_esik": 55, "rsi_short_esik": 45}),
    ("MACD off + skor 75", {"macd_momentum_required": False, "min_skor": 75}),
    ("MACD off + EV 10", {"macd_momentum_required": False, "ev_min_pct": 10.0}),
]

all_results = []

print("=" * 90)
print("  SYSTEM F — GEVSETME TESTI (3 TF, 30 gun, 15 coin)")
print("=" * 90)
print()

for ti, (name, overrides) in enumerate(TESTS):
    sf = dict(BASE_SF)
    sf.update(overrides)

    cfg = BacktestConfig(
        days_back=30,
        check_interval_min=15,
        top_coins=15,
        lookback=200,
        min_tf_uyum=3,
        system_params=sf,
    )

    print(f"[{ti+1}/{len(TESTS)}] {name} ...", end=" ", flush=True)
    engine = BacktestEngine(cfg)
    result = engine.run()

    t = result.total_trades
    w = result.win_count
    l = result.loss_count
    wr = result.win_rate
    roi = result.total_roi
    avg = result.avg_roi

    # Max drawdown (en kotu ardisik kayip)
    max_dd = 0
    dd = 0
    for trade in result.trades:
        if trade.roi_net < 0:
            dd += trade.roi_net
            max_dd = min(max_dd, dd)
        else:
            dd = 0

    all_results.append({
        "name": name, "trades": t, "wins": w, "losses": l,
        "wr": wr, "roi": roi, "avg": avg, "max_dd": max_dd,
        "trades_list": result.trades,
    })

    if t > 0:
        print(f"{t} trade, {w}W/{l}L, WR:{wr:.0f}%, "
              f"ROI:{roi:+.1f}%, Avg:{avg:+.1f}%, MaxDD:{max_dd:.0f}%")
    else:
        print("0 trade")

# ═══ Karsilastirma Tablosu ═══
print(f"\n{'=' * 90}")
print(f"{'Test':<28} {'Trade':>5} {'W':>3} {'L':>3} {'WR%':>5} "
      f"{'TopROI%':>8} {'AvgROI%':>8} {'MaxDD%':>7} {'Karar'}")
print(f"{'=' * 90}")

baseline = all_results[0]

for r in all_results:
    # Karar: baseline'a gore iyilestirme mi?
    if r["name"] == baseline["name"]:
        verdict = "BAZA"
    elif r["trades"] == 0:
        verdict = "SINYAL YOK"
    elif r["roi"] > baseline["roi"] and r["wr"] >= baseline["wr"] * 0.8:
        if r["max_dd"] >= baseline["max_dd"] * 1.5 and baseline["max_dd"] < 0:
            verdict = "RISKLI +"
        else:
            verdict = "ONERILEN"
    elif r["roi"] > baseline["roi"]:
        verdict = "RISKLI +"
    elif r["roi"] > 0:
        verdict = "NOTR"
    else:
        verdict = "ZARARLI"

    w = "+" if r["roi"] > 0 else ""
    print(f"{r['name']:<28} {r['trades']:>5} {r['wins']:>3} {r['losses']:>3} "
          f"{r['wr']:>4.0f}% {w}{r['roi']:>7.1f}% {r['avg']:>+7.1f}% "
          f"{r['max_dd']:>6.0f}% {verdict}")

# En iyi kombinasyon detaylari
best = max(all_results, key=lambda x: x["roi"])
print(f"\n{'=' * 90}")
print(f"EN IYI: {best['name']} -> {best['trades']} trade, "
      f"WR:{best['wr']:.0f}%, ROI:{best['roi']:+.1f}%")

if best["trades_list"]:
    print(f"\nTrade detaylari:")
    for t in best["trades_list"]:
        dt = datetime.fromtimestamp(t.time_ms/1000, tz=timezone.utc)
        w = "+" if t.roi_net > 0 else ""
        print(f"  {dt:%m-%d %H:%M} {t.symbol:>12} {t.direction:>5} "
              f"{t.leverage:>3}x {t.exit_reason:>10} {t.hold_str:>8} "
              f"{w}{t.roi_net:.1f}% skor:{t.score:.0f}")
print()
