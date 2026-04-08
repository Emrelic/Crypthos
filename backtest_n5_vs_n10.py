"""N=5 vs N=10 Swing Detection Backtest — 1m BTCUSDT
Amaç: 1 dakikalık timeframe'de G tespitinde n=5 mi n=10 mu daha sağlıklı?

Karşılaştırma kriterleri:
  1. G ve I değerleri (dalga büyüklükleri)
  2. Swing sayısı ve kalitesi (CV)
  3. SL/TP performansı (simülasyon)
  4. Win rate, ortalama PnL
"""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.gridspec import GridSpec
from datetime import datetime, timezone

from backtest.data_fetcher import fetch_klines
from scanner.system_b_scanner import detect_zigzag_swings, analyze_waves

# ═══════════════════════════════════════════════════════════════
# AYARLAR
# ═══════════════════════════════════════════════════════════════
SYMBOL = "BTCUSDT"
INTERVAL = "1m"
DAYS_BACK = 3          # 3 gün = ~4320 mum
SL_CARPAN = 1.5        # SL = G × 1.5
TP_CARPAN = 2.5        # TP = G × 2.5
TRAILING_TRIGGER = 2.0 # trailing başlangıç = G × 2.0
TRAILING_CALLBACK = 0.5  # trailing geri çekilme = G × 0.5
FEE_RATE = 0.0004      # %0.04 fee per side
N_VALUES = [5, 10]

# ═══════════════════════════════════════════════════════════════
# VERİ ÇEK
# ═══════════════════════════════════════════════════════════════
print(f"[*] {SYMBOL} {INTERVAL} verisi cekiliyor ({DAYS_BACK} gun)...")
now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
start_ms = now_ms - DAYS_BACK * 24 * 60 * 60 * 1000

raw = fetch_klines(SYMBOL, INTERVAL, start_ms, now_ms)
print(f"   Toplam {len(raw)} mum çekildi")

if len(raw) < 100:
    print("Yeterli veri yok!")
    sys.exit(1)

# Parse
opens = np.array([float(k[1]) for k in raw])
highs = np.array([float(k[2]) for k in raw])
lows = np.array([float(k[3]) for k in raw])
closes = np.array([float(k[4]) for k in raw])
timestamps = [int(k[0]) for k in raw]
current_price = closes[-1]

# ═══════════════════════════════════════════════════════════════
# DALGA ANALİZİ — N=5 vs N=10
# ═══════════════════════════════════════════════════════════════
results = {}

for n_val in N_VALUES:
    swings = detect_zigzag_swings(highs, lows, n=n_val)
    wa = analyze_waves(swings, current_price)

    results[n_val] = {
        "swings": swings,
        "wa": wa,
        "n": n_val,
        "swing_count": len(swings),
        "G": wa.G,
        "I": wa.I,
        "forward_waves": wa.forward_waves,
        "backward_waves": wa.backward_waves,
        "cv": wa.cv,
        "forward_cv": wa.forward_cv,
        "backward_cv": wa.backward_cv,
        "trend": wa.trend_direction,
    }

    print(f"\n{'='*60}")
    print(f"  N = {n_val}")
    print(f"{'='*60}")
    print(f"  Swing sayısı       : {len(swings)}")
    print(f"  İleri dalga sayısı : {len(wa.forward_waves)}")
    print(f"  Geri dalga sayısı  : {len(wa.backward_waves)}")
    print(f"  G (geri dalga ort) : {wa.G:.4f}%")
    print(f"  I (ileri dalga ort): {wa.I:.4f}%")
    if wa.I > 0:
        print(f"  G/I oranı          : {wa.G/wa.I:.3f}")
    print(f"  Forward CV         : {wa.forward_cv:.3f}")
    print(f"  Backward CV        : {wa.backward_cv:.3f}")
    print(f"  Trend              : {wa.trend_direction}")

# ═══════════════════════════════════════════════════════════════
# TİCARET SİMÜLASYONU — Her n için rolling window
# ═══════════════════════════════════════════════════════════════
def simulate_trades(highs, lows, closes, n_val, window_size=500, step=100):
    """Rolling window ile dalga analizi yapıp sinyal üret ve simüle et."""
    trades = []
    i = window_size

    while i < len(closes) - 60:  # en az 60 mum ileri bakış
        # Window üzerinde dalga analizi
        h_win = highs[i - window_size:i]
        l_win = lows[i - window_size:i]
        c_win = closes[i - window_size:i]

        swings = detect_zigzag_swings(h_win, l_win, n=n_val)
        wa = analyze_waves(swings, c_win[-1])

        if wa.G <= 0 or wa.I <= 0 or len(wa.backward_waves) < 2:
            i += step
            continue

        # Sinyal: wave_position > 0.8 ise (G'nin %80'i kadar geri çekilmiş)
        # trend yönünde giriş
        if wa.wave_position < 0.7:
            i += step
            continue

        entry_price = closes[i]
        direction = "LONG" if wa.trend_direction == "UP" else "SHORT"

        sl_pct = wa.G * SL_CARPAN
        tp_pct = wa.G * TP_CARPAN
        trail_trigger_pct = wa.G * TRAILING_TRIGGER
        trail_cb_pct = wa.G * TRAILING_CALLBACK

        # İleri bakış simülasyonu (max 60 mum = 1 saat)
        exit_reason = "TIME"
        exit_price = closes[min(i + 60, len(closes) - 1)]
        bars_held = 60
        peak = entry_price

        for j in range(1, min(61, len(closes) - i)):
            h = highs[i + j]
            l = lows[i + j]
            c = closes[i + j]

            if direction == "LONG":
                # SL check
                if l <= entry_price * (1 - sl_pct / 100):
                    exit_reason = "SL"
                    exit_price = entry_price * (1 - sl_pct / 100)
                    bars_held = j
                    break
                # TP check
                if h >= entry_price * (1 + tp_pct / 100):
                    exit_reason = "TP"
                    exit_price = entry_price * (1 + tp_pct / 100)
                    bars_held = j
                    break
                # Trailing
                if h > peak:
                    peak = h
                move_pct = (peak - entry_price) / entry_price * 100
                if move_pct >= trail_trigger_pct:
                    retrace = (peak - l) / peak * 100
                    if retrace >= trail_cb_pct:
                        exit_reason = "TRAIL"
                        exit_price = peak * (1 - trail_cb_pct / 100)
                        bars_held = j
                        break
            else:  # SHORT
                if h >= entry_price * (1 + sl_pct / 100):
                    exit_reason = "SL"
                    exit_price = entry_price * (1 + sl_pct / 100)
                    bars_held = j
                    break
                if l <= entry_price * (1 - tp_pct / 100):
                    exit_reason = "TP"
                    exit_price = entry_price * (1 - tp_pct / 100)
                    bars_held = j
                    break
                if l < peak:
                    peak = l
                move_pct = (entry_price - peak) / entry_price * 100
                if move_pct >= trail_trigger_pct:
                    retrace = (h - peak) / peak * 100
                    if retrace >= trail_cb_pct:
                        exit_reason = "TRAIL"
                        exit_price = peak * (1 + trail_cb_pct / 100)
                        bars_held = j
                        break

        # PnL hesapla
        if direction == "LONG":
            pnl_pct = (exit_price - entry_price) / entry_price * 100
        else:
            pnl_pct = (entry_price - exit_price) / entry_price * 100

        fee_total = FEE_RATE * 2 * 100  # % cinsinden toplam fee
        pnl_net = pnl_pct - fee_total

        trades.append({
            "entry_idx": i,
            "direction": direction,
            "entry_price": entry_price,
            "exit_price": exit_price,
            "exit_reason": exit_reason,
            "bars_held": bars_held,
            "G": wa.G,
            "I": wa.I,
            "sl_pct": sl_pct,
            "tp_pct": tp_pct,
            "pnl_pct": pnl_pct,
            "pnl_net": pnl_net,
        })

        # Çıkıştan sonra ilerle
        i += max(step, bars_held + 5)

    return trades


print("\n" + "=" * 60)
print("  TİCARET SİMÜLASYONU")
print("=" * 60)

trade_results = {}
for n_val in N_VALUES:
    trades = simulate_trades(highs, lows, closes, n_val, window_size=500, step=50)
    trade_results[n_val] = trades

    wins = [t for t in trades if t["pnl_net"] > 0]
    losses = [t for t in trades if t["pnl_net"] <= 0]
    total_pnl = sum(t["pnl_net"] for t in trades)
    avg_pnl = total_pnl / len(trades) if trades else 0

    # Exit reason breakdown
    sl_count = len([t for t in trades if t["exit_reason"] == "SL"])
    tp_count = len([t for t in trades if t["exit_reason"] == "TP"])
    trail_count = len([t for t in trades if t["exit_reason"] == "TRAIL"])
    time_count = len([t for t in trades if t["exit_reason"] == "TIME"])

    avg_g = np.mean([t["G"] for t in trades]) if trades else 0
    avg_sl = np.mean([t["sl_pct"] for t in trades]) if trades else 0
    avg_bars = np.mean([t["bars_held"] for t in trades]) if trades else 0

    print(f"\n  N = {n_val}")
    print(f"  {'-' * 50}")
    print(f"  Toplam işlem  : {len(trades)}")
    print(f"  Win / Loss    : {len(wins)} / {len(losses)}")
    print(f"  Win Rate      : {len(wins)/len(trades)*100:.1f}%" if trades else "  Win Rate: N/A")
    print(f"  Toplam PnL    : {total_pnl:.3f}%")
    print(f"  Ort. PnL      : {avg_pnl:.4f}%")
    print(f"  Ort. G        : {avg_g:.4f}%")
    print(f"  Ort. SL       : {avg_sl:.4f}%")
    print(f"  Ort. bars held: {avg_bars:.1f} (~{avg_bars:.0f} dk)")
    print(f"  Çıkış: SL={sl_count} TP={tp_count} TRAIL={trail_count} TIME={time_count}")

    if wins:
        avg_win = np.mean([t["pnl_net"] for t in wins])
        print(f"  Ort. kazanç   : +{avg_win:.4f}%")
    if losses:
        avg_loss = np.mean([t["pnl_net"] for t in losses])
        print(f"  Ort. kayıp    : {avg_loss:.4f}%")
    if wins and losses:
        avg_win = np.mean([t["pnl_net"] for t in wins])
        avg_loss = abs(np.mean([t["pnl_net"] for t in losses]))
        if avg_loss > 0:
            print(f"  Risk/Reward   : 1:{avg_win/avg_loss:.2f}")

# ═══════════════════════════════════════════════════════════════
# DALGA DAĞILIMI ANALİZİ
# ═══════════════════════════════════════════════════════════════
print("\n" + "=" * 60)
print("  DALGA DAĞILIMI DETAYI")
print("=" * 60)

for n_val in N_VALUES:
    r = results[n_val]
    bw = r["backward_waves"]
    fw = r["forward_waves"]
    print(f"\n  N = {n_val}")
    if bw:
        print(f"  Geri dalgalar  : min={min(bw):.4f}% max={max(bw):.4f}% "
              f"median={np.median(bw):.4f}% std={np.std(bw):.4f}%")
    if fw:
        print(f"  İleri dalgalar : min={min(fw):.4f}% max={max(fw):.4f}% "
              f"median={np.median(fw):.4f}% std={np.std(fw):.4f}%")

# ═══════════════════════════════════════════════════════════════
# GRAFİK
# ═══════════════════════════════════════════════════════════════
print("\n[*] Grafik olusturuluyor...")

fig = plt.figure(figsize=(24, 28), facecolor="#1a1a2e", dpi=100)
gs = GridSpec(5, 2, figure=fig, hspace=0.35, wspace=0.3,
             height_ratios=[2.5, 2.5, 1.5, 1.5, 1.5])

style_kwargs = dict(facecolor="#16213e")

# ── 1. Fiyat + Zigzag N=5 ──
ax1 = fig.add_subplot(gs[0, :])
ax1.set(**style_kwargs)
# Son 1000 mum göster
show_n = min(1500, len(closes))
show_start = len(closes) - show_n
x_range = np.arange(show_n)
c_show = closes[show_start:]
h_show = highs[show_start:]
l_show = lows[show_start:]
o_show = opens[show_start:]

# Mumları çiz
for i in range(show_n):
    color = "#00C853" if c_show[i] >= o_show[i] else "#FF1744"
    ax1.plot([x_range[i], x_range[i]], [l_show[i], h_show[i]], color=color, linewidth=0.3)
    body_b = min(o_show[i], c_show[i])
    body_h = max(abs(c_show[i] - o_show[i]), (h_show[i] - l_show[i]) * 0.003)
    ax1.bar(x_range[i], body_h, bottom=body_b, width=0.6, color=color,
            edgecolor=color, linewidth=0.1)

# N=5 zigzag
swings_5 = results[5]["swings"]
sx5 = [s.index - show_start for s in swings_5 if s.index >= show_start]
sy5 = [s.price for s in swings_5 if s.index >= show_start]
if sx5:
    ax1.plot(sx5, sy5, "o-", color="#00BCD4", linewidth=1.5, markersize=4,
             alpha=0.9, label=f"N=5 (swing={len(swings_5)})")

# N=10 zigzag
swings_10 = results[10]["swings"]
sx10 = [s.index - show_start for s in swings_10 if s.index >= show_start]
sy10 = [s.price for s in swings_10 if s.index >= show_start]
if sx10:
    ax1.plot(sx10, sy10, "s-", color="#FF9800", linewidth=1.5, markersize=4,
             alpha=0.9, label=f"N=10 (swing={len(swings_10)})")

ax1.legend(fontsize=10, facecolor="#16213e", edgecolor="#333", labelcolor="#E0E0E0")
ax1.set_title(f"{SYMBOL} 1m — Zigzag Karşılaştırma: N=5 vs N=10 (son {show_n} mum)",
              color="#E0E0E0", fontsize=13)
ax1.tick_params(colors="#E0E0E0")
ax1.grid(True, color="#333", alpha=0.2)
for spine in ax1.spines.values():
    spine.set_color("#333")

# ── 2. Fiyat yakınlaştırma (son 300 mum) ──
ax2 = fig.add_subplot(gs[1, :])
ax2.set(**style_kwargs)
zoom_n = min(300, len(closes))
zoom_start = len(closes) - zoom_n
x_zoom = np.arange(zoom_n)
c_zoom = closes[zoom_start:]
h_zoom = highs[zoom_start:]
l_zoom = lows[zoom_start:]
o_zoom = opens[zoom_start:]

for i in range(zoom_n):
    color = "#00C853" if c_zoom[i] >= o_zoom[i] else "#FF1744"
    ax2.plot([x_zoom[i], x_zoom[i]], [l_zoom[i], h_zoom[i]], color=color, linewidth=0.5)
    body_b = min(o_zoom[i], c_zoom[i])
    body_h = max(abs(c_zoom[i] - o_zoom[i]), (h_zoom[i] - l_zoom[i]) * 0.003)
    ax2.bar(x_zoom[i], body_h, bottom=body_b, width=0.6, color=color,
            edgecolor=color, linewidth=0.2)

# N=5 zigzag zoom
for s in swings_5:
    idx = s.index - zoom_start
    if 0 <= idx < zoom_n:
        color = "#00BCD4"
        marker = "v" if s.type == "SH" else "^"
        ax2.plot(idx, s.price, marker, color=color, markersize=8)

# N=10 zigzag zoom
for s in swings_10:
    idx = s.index - zoom_start
    if 0 <= idx < zoom_n:
        color = "#FF9800"
        marker = "v" if s.type == "SH" else "^"
        ax2.plot(idx, s.price, marker, color=color, markersize=8)

# Zigzag çizgileri
sx5z = [(s.index - zoom_start, s.price) for s in swings_5 if 0 <= s.index - zoom_start < zoom_n]
sx10z = [(s.index - zoom_start, s.price) for s in swings_10 if 0 <= s.index - zoom_start < zoom_n]
if sx5z:
    ax2.plot([p[0] for p in sx5z], [p[1] for p in sx5z], "-", color="#00BCD4",
             linewidth=1.5, alpha=0.7, label="N=5")
if sx10z:
    ax2.plot([p[0] for p in sx10z], [p[1] for p in sx10z], "-", color="#FF9800",
             linewidth=1.5, alpha=0.7, label="N=10")

ax2.legend(fontsize=10, facecolor="#16213e", edgecolor="#333", labelcolor="#E0E0E0")
ax2.set_title(f"Yakınlaştırma — Son {zoom_n} mum", color="#E0E0E0", fontsize=12)
ax2.tick_params(colors="#E0E0E0")
ax2.grid(True, color="#333", alpha=0.2)
for spine in ax2.spines.values():
    spine.set_color("#333")

# ── 3. Dalga dağılımı histogram ──
ax3 = fig.add_subplot(gs[2, 0])
ax3.set(**style_kwargs)
bw5 = results[5]["backward_waves"]
bw10 = results[10]["backward_waves"]
if bw5 and bw10:
    max_val = max(max(bw5), max(bw10))
    bins = np.linspace(0, min(max_val, np.percentile(bw5 + bw10, 95)), 30)
    ax3.hist(bw5, bins=bins, alpha=0.6, color="#00BCD4", label=f"N=5 (ort={np.mean(bw5):.4f}%)")
    ax3.hist(bw10, bins=bins, alpha=0.6, color="#FF9800", label=f"N=10 (ort={np.mean(bw10):.4f}%)")
    ax3.axvline(np.mean(bw5), color="#00BCD4", linestyle="--", linewidth=2)
    ax3.axvline(np.mean(bw10), color="#FF9800", linestyle="--", linewidth=2)
ax3.legend(fontsize=9, facecolor="#16213e", edgecolor="#333", labelcolor="#E0E0E0")
ax3.set_title("Geri Dalga (G) Dağılımı", color="#E0E0E0", fontsize=11)
ax3.set_xlabel("Dalga %", color="#E0E0E0")
ax3.set_ylabel("Frekans", color="#E0E0E0")
ax3.tick_params(colors="#E0E0E0")
ax3.grid(True, color="#333", alpha=0.2)
for spine in ax3.spines.values():
    spine.set_color("#333")

# ── 4. İleri dalga dağılımı ──
ax4 = fig.add_subplot(gs[2, 1])
ax4.set(**style_kwargs)
fw5 = results[5]["forward_waves"]
fw10 = results[10]["forward_waves"]
if fw5 and fw10:
    max_val = max(max(fw5), max(fw10))
    bins = np.linspace(0, min(max_val, np.percentile(fw5 + fw10, 95)), 30)
    ax4.hist(fw5, bins=bins, alpha=0.6, color="#00BCD4", label=f"N=5 (ort={np.mean(fw5):.4f}%)")
    ax4.hist(fw10, bins=bins, alpha=0.6, color="#FF9800", label=f"N=10 (ort={np.mean(fw10):.4f}%)")
    ax4.axvline(np.mean(fw5), color="#00BCD4", linestyle="--", linewidth=2)
    ax4.axvline(np.mean(fw10), color="#FF9800", linestyle="--", linewidth=2)
ax4.legend(fontsize=9, facecolor="#16213e", edgecolor="#333", labelcolor="#E0E0E0")
ax4.set_title("İleri Dalga (I) Dağılımı", color="#E0E0E0", fontsize=11)
ax4.set_xlabel("Dalga %", color="#E0E0E0")
ax4.set_ylabel("Frekans", color="#E0E0E0")
ax4.tick_params(colors="#E0E0E0")
ax4.grid(True, color="#333", alpha=0.2)
for spine in ax4.spines.values():
    spine.set_color("#333")

# ── 5. PnL Karşılaştırma ──
ax5 = fig.add_subplot(gs[3, 0])
ax5.set(**style_kwargs)
for n_val, color in zip(N_VALUES, ["#00BCD4", "#FF9800"]):
    trades = trade_results[n_val]
    if trades:
        cumulative = np.cumsum([t["pnl_net"] for t in trades])
        ax5.plot(cumulative, "-", color=color, linewidth=2,
                 label=f"N={n_val} (toplam={cumulative[-1]:.3f}%)")
ax5.legend(fontsize=9, facecolor="#16213e", edgecolor="#333", labelcolor="#E0E0E0")
ax5.set_title("Kümülatif PnL Karşılaştırma", color="#E0E0E0", fontsize=11)
ax5.set_xlabel("İşlem #", color="#E0E0E0")
ax5.set_ylabel("Kümülatif PnL %", color="#E0E0E0")
ax5.axhline(0, color="#666", linewidth=0.5)
ax5.tick_params(colors="#E0E0E0")
ax5.grid(True, color="#333", alpha=0.2)
for spine in ax5.spines.values():
    spine.set_color("#333")

# ── 6. Çıkış nedeni dağılımı ──
ax6 = fig.add_subplot(gs[3, 1])
ax6.set(**style_kwargs)
bar_width = 0.35
reasons = ["SL", "TP", "TRAIL", "TIME"]
for i, (n_val, color) in enumerate(zip(N_VALUES, ["#00BCD4", "#FF9800"])):
    trades = trade_results[n_val]
    counts = [len([t for t in trades if t["exit_reason"] == r]) for r in reasons]
    x_pos = np.arange(len(reasons)) + i * bar_width
    ax6.bar(x_pos, counts, bar_width, color=color, alpha=0.8, label=f"N={n_val}")
ax6.set_xticks(np.arange(len(reasons)) + bar_width / 2)
ax6.set_xticklabels(reasons, color="#E0E0E0")
ax6.legend(fontsize=9, facecolor="#16213e", edgecolor="#333", labelcolor="#E0E0E0")
ax6.set_title("Çıkış Nedeni Dağılımı", color="#E0E0E0", fontsize=11)
ax6.set_ylabel("Adet", color="#E0E0E0")
ax6.tick_params(colors="#E0E0E0")
ax6.grid(True, color="#333", alpha=0.2)
for spine in ax6.spines.values():
    spine.set_color("#333")

# ── 7. Özet tablo ──
ax7 = fig.add_subplot(gs[4, :])
ax7.set(**style_kwargs)
ax7.axis("off")

headers = ["Metrik", "N=5", "N=10", "Kazanan"]
rows = []

# G değeri
g5, g10 = results[5]["G"], results[10]["G"]
rows.append(["G (geri dalga %)", f"{g5:.4f}%", f"{g10:.4f}%", "—"])

# I değeri
i5, i10 = results[5]["I"], results[10]["I"]
rows.append(["I (ileri dalga %)", f"{i5:.4f}%", f"{i10:.4f}%", "—"])

# Swing sayısı
rows.append(["Swing sayısı", str(results[5]["swing_count"]),
             str(results[10]["swing_count"]), "—"])

# CV
rows.append(["CV (tutarlılık)", f"{results[5]['cv']:.3f}",
             f"{results[10]['cv']:.3f}",
             f"N={'5' if results[5]['cv'] < results[10]['cv'] else '10'} (düşük=iyi)"])

# Trade stats
for n_val in N_VALUES:
    trades = trade_results[n_val]

t5, t10 = trade_results[5], trade_results[10]
wr5 = len([t for t in t5 if t["pnl_net"] > 0]) / len(t5) * 100 if t5 else 0
wr10 = len([t for t in t10 if t["pnl_net"] > 0]) / len(t10) * 100 if t10 else 0
rows.append(["Win Rate", f"{wr5:.1f}%", f"{wr10:.1f}%",
             f"N={'5' if wr5 > wr10 else '10'}"])

tp5 = sum(t["pnl_net"] for t in t5) if t5 else 0
tp10 = sum(t["pnl_net"] for t in t10) if t10 else 0
rows.append(["Toplam PnL", f"{tp5:.3f}%", f"{tp10:.3f}%",
             f"N={'5' if tp5 > tp10 else '10'}"])

avg5 = tp5 / len(t5) if t5 else 0
avg10 = tp10 / len(t10) if t10 else 0
rows.append(["Ort. PnL/işlem", f"{avg5:.4f}%", f"{avg10:.4f}%",
             f"N={'5' if avg5 > avg10 else '10'}"])

rows.append(["İşlem sayısı", str(len(t5)), str(len(t10)), "—"])

table = ax7.table(cellText=rows, colLabels=headers, loc="center", cellLoc="center")
table.auto_set_font_size(False)
table.set_fontsize(10)
table.scale(1, 1.8)
for (row, col), cell in table.get_celld().items():
    cell.set_facecolor("#16213e" if row > 0 else "#0D47A1")
    cell.set_edgecolor("#333")
    cell.set_text_props(color="#E0E0E0")
    if row > 0 and col == 3:
        text = cell.get_text().get_text()
        if "N=5" in text:
            cell.set_facecolor("#00695C")
        elif "N=10" in text:
            cell.set_facecolor("#E65100")
ax7.set_title("N=5 vs N=10 Özet Karşılaştırma", color="#E0E0E0", fontsize=13, pad=15)

out_path = os.path.join(os.path.dirname(__file__), "backtest_n5_vs_n10.png")
fig.savefig(out_path, bbox_inches="tight", facecolor="#1a1a2e")
plt.close(fig)
print(f"\n[OK] Grafik kaydedildi: {out_path}")

# ═══════════════════════════════════════════════════════════════
# SONUÇ ÖNERİSİ
# ═══════════════════════════════════════════════════════════════
print("\n" + "=" * 60)
print("  SONUÇ VE ÖNERİ")
print("=" * 60)

score_5 = 0
score_10 = 0

# 1. CV karşılaştırma (düşük daha iyi)
if results[5]["cv"] < results[10]["cv"]:
    score_5 += 1
    print("  [+] N=5 daha düşük CV → daha tutarlı dalgalar")
else:
    score_10 += 1
    print("  [+] N=10 daha düşük CV → daha tutarlı dalgalar")

# 2. Win rate
if wr5 > wr10:
    score_5 += 2
    print(f"  [+] N=5 daha yüksek win rate ({wr5:.1f}% vs {wr10:.1f}%)")
else:
    score_10 += 2
    print(f"  [+] N=10 daha yüksek win rate ({wr10:.1f}% vs {wr5:.1f}%)")

# 3. Toplam PnL
if tp5 > tp10:
    score_5 += 2
    print(f"  [+] N=5 daha yüksek toplam PnL ({tp5:.3f}% vs {tp10:.3f}%)")
else:
    score_10 += 2
    print(f"  [+] N=10 daha yüksek toplam PnL ({tp10:.3f}% vs {tp5:.3f}%)")

# 4. Swing sayısı (1m için daha fazla swing daha iyi — daha çok veri)
if results[5]["swing_count"] > results[10]["swing_count"]:
    score_5 += 1
    print(f"  [+] N=5 daha fazla swing tespit ({results[5]['swing_count']} vs {results[10]['swing_count']})")
else:
    score_10 += 1

winner = "N=5" if score_5 > score_10 else "N=10" if score_10 > score_5 else "EŞIT"
print(f"\n  >> Skor: N=5={score_5}  N=10={score_10}  ->  Kazanan: {winner}")
print(f"\n  1m timeframe için önerilen swing_n = {'5' if score_5 >= score_10 else '10'}")
