"""BTC G-noktası ve verimli timeline analizi — tüm timeframe'lerde."""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.gridspec import GridSpec
from market.binance_rest import BinanceRestClient
from scanner.system_b_scanner import (
    detect_zigzag_swings, analyze_waves,
    compute_efficiency_ratio, compute_rolling_er,
    compute_hurst_improved,
)

rest = BinanceRestClient(api_key="", api_secret="")

TIMEFRAMES = ["5m", "15m", "30m", "1h", "2h", "4h", "8h", "12h", "1d"]
CANDLE_COUNTS = {"5m": 500, "15m": 500, "30m": 500, "1h": 500,
                 "2h": 500, "4h": 500, "8h": 300, "12h": 300, "1d": 300}
SWING_N = 10
SYMBOL = "BTCUSDT"

results = []

for tf in TIMEFRAMES:
    limit = CANDLE_COUNTS[tf]
    df = rest.get_klines(SYMBOL, tf, limit)
    if df is None or len(df) < 50:
        print(f"{tf}: yeterli veri yok")
        continue

    highs = df["high"].values.astype(float)
    lows = df["low"].values.astype(float)
    closes = df["close"].values.astype(float)
    current_price = closes[-1]

    # Zigzag swings
    swings = detect_zigzag_swings(highs, lows, SWING_N)

    # Wave analysis
    wa = analyze_waves(swings, current_price)

    # ER & Hurst
    er = compute_rolling_er(closes)
    hurst = compute_hurst_improved(closes)

    # ATR (14)
    prev_close = np.roll(closes, 1); prev_close[0] = closes[0]
    tr = np.maximum(highs - lows, np.maximum(np.abs(highs - prev_close), np.abs(lows - prev_close)))
    atr_val = pd.Series(tr).ewm(alpha=1/14, min_periods=14, adjust=False).mean().values[-1]
    atr_pct = atr_val / current_price * 100

    # SL ve kaldıraç hesabı (G bazlı)
    G = wa.G if wa.G > 0 else atr_pct
    I = wa.I if wa.I > 0 else 0
    sl_pct = G * 1.5  # SL = 1.5×G
    fee_pct = 0.08
    liq_pct = sl_pct * 2  # pratik liq = 2×SL
    teorik_liq = liq_pct / 0.7 + fee_pct * 2
    max_lev = int(100 / teorik_liq) if teorik_liq > 0 else 1
    max_lev = max(1, min(max_lev, 125))

    # Rejim
    if er < 0.2 and hurst < 0.45:
        regime = "RANGING"
    elif er > 0.35 and hurst > 0.55:
        regime = "TRENDING"
    else:
        regime = "GRAY"

    r = {
        "tf": tf, "candles": len(df), "swings": len(swings),
        "forward_waves": len(wa.forward_waves),
        "backward_waves": len(wa.backward_waves),
        "G": G, "I": I,
        "G_I_ratio": G / I if I > 0 else 0,
        "er": er, "hurst": hurst,
        "atr_pct": atr_pct,
        "sl_pct": sl_pct, "max_lev": max_lev,
        "regime": regime,
        "trend_dir": wa.trend_direction,
        "cv": wa.cv,
        "wave_pos": wa.wave_position,
        "swings_data": swings,
        "df": df,
    }
    results.append(r)
    print(f"{tf:4s} | G={G:.3f}% I={I:.3f}% | ER={er:.3f} H={hurst:.3f} | "
          f"ATR={atr_pct:.3f}% | Rejim={regime:8s} | Lev={max_lev:3d}x | "
          f"Swings={len(swings):2d} | CV={wa.cv:.2f} | Yön={wa.trend_direction}")

# ════════════════════════════════════════════════════════════════════════
# GRAFİK
# ════════════════════════════════════════════════════════════════════════

n_tf = len(results)
fig = plt.figure(figsize=(22, 6 + n_tf * 3.5), facecolor="#1a1a2e", dpi=100)

# Layout: top summary charts (2 rows) + per-TF candlestick with zigzag
gs = GridSpec(2 + n_tf, 2, figure=fig, hspace=0.4, wspace=0.3,
             height_ratios=[2, 2] + [2.5] * n_tf)

# ── 1. G ve I vs Timeframe ──
ax1 = fig.add_subplot(gs[0, 0])
ax1.set_facecolor("#16213e")
tfs = [r["tf"] for r in results]
gs_vals = [r["G"] for r in results]
is_vals = [r["I"] for r in results]
x = np.arange(len(tfs))
ax1.bar(x - 0.15, gs_vals, 0.3, color="#FF1744", alpha=0.8, label="G (geri dalga %)")
ax1.bar(x + 0.15, is_vals, 0.3, color="#00C853", alpha=0.8, label="I (ileri dalga %)")
ax1.set_xticks(x)
ax1.set_xticklabels(tfs, color="#E0E0E0", fontsize=9)
ax1.set_ylabel("Dalga %", color="#E0E0E0", fontsize=9)
ax1.set_title(f"{SYMBOL} — G ve I Dalga Boyları (Timeframe'e göre)", color="#E0E0E0", fontsize=11)
ax1.legend(fontsize=8, facecolor="#16213e", edgecolor="#333", labelcolor="#E0E0E0")
ax1.grid(True, color="#333", alpha=0.3)
ax1.tick_params(colors="#E0E0E0")
for spine in ax1.spines.values():
    spine.set_color("#333")

# ── 2. ER ve Hurst vs Timeframe ──
ax2 = fig.add_subplot(gs[0, 1])
ax2.set_facecolor("#16213e")
er_vals = [r["er"] for r in results]
h_vals = [r["hurst"] for r in results]
ax2.plot(x, er_vals, "o-", color="#2196F3", linewidth=2, markersize=6, label="ER")
ax2.plot(x, h_vals, "s-", color="#FF9800", linewidth=2, markersize=6, label="Hurst")
ax2.axhline(0.35, color="#00C853", linewidth=0.8, linestyle="--", alpha=0.5, label="ER trend eşik")
ax2.axhline(0.2, color="#FF1744", linewidth=0.8, linestyle="--", alpha=0.5, label="ER ranging eşik")
ax2.axhline(0.5, color="#9E9E9E", linewidth=0.8, linestyle=":", alpha=0.4)
ax2.set_xticks(x)
ax2.set_xticklabels(tfs, color="#E0E0E0", fontsize=9)
ax2.set_ylabel("Değer", color="#E0E0E0", fontsize=9)
ax2.set_title("ER ve Hurst Exponent (Timeframe'e göre)", color="#E0E0E0", fontsize=11)
ax2.set_ylim(0, 1)
ax2.legend(fontsize=7, facecolor="#16213e", edgecolor="#333", labelcolor="#E0E0E0", ncol=2)
ax2.grid(True, color="#333", alpha=0.3)
ax2.tick_params(colors="#E0E0E0")
for spine in ax2.spines.values():
    spine.set_color("#333")

# ── 3. Kaldıraç ve SL vs Timeframe ──
ax3 = fig.add_subplot(gs[1, 0])
ax3.set_facecolor("#16213e")
levs = [r["max_lev"] for r in results]
sl_pcts = [r["sl_pct"] for r in results]
colors_lev = ["#00C853" if r["regime"] == "TRENDING" else "#FF1744" if r["regime"] == "RANGING" else "#FF9800"
              for r in results]
ax3.bar(x, levs, 0.5, color=colors_lev, alpha=0.8)
for i, (l, s) in enumerate(zip(levs, sl_pcts)):
    ax3.text(i, l + 1, f"{l}x\nSL:{s:.2f}%", ha="center", va="bottom",
             color="#E0E0E0", fontsize=8)
ax3.set_xticks(x)
ax3.set_xticklabels(tfs, color="#E0E0E0", fontsize=9)
ax3.set_ylabel("Max Kaldıraç", color="#E0E0E0", fontsize=9)
ax3.set_title("G Bazlı Kaldıraç (Yeşil=TREND, Kırmızı=RANGING, Turuncu=GRAY)",
              color="#E0E0E0", fontsize=10)
ax3.grid(True, color="#333", alpha=0.3)
ax3.tick_params(colors="#E0E0E0")
for spine in ax3.spines.values():
    spine.set_color("#333")

# ── 4. Rejim Özet Tablo ──
ax4 = fig.add_subplot(gs[1, 1])
ax4.set_facecolor("#16213e")
ax4.axis("off")
table_data = [["TF", "G%", "I%", "ER", "Hurst", "Rejim", "Yön", "Lev", "SL%", "CV"]]
for r in results:
    table_data.append([
        r["tf"], f"{r['G']:.3f}", f"{r['I']:.3f}",
        f"{r['er']:.3f}", f"{r['hurst']:.3f}",
        r["regime"], r["trend_dir"] or "?",
        f"{r['max_lev']}x", f"{r['sl_pct']:.2f}", f"{r['cv']:.2f}",
    ])
table = ax4.table(cellText=table_data[1:], colLabels=table_data[0],
                   loc="center", cellLoc="center")
table.auto_set_font_size(False)
table.set_fontsize(8)
for (row, col), cell in table.get_celld().items():
    cell.set_facecolor("#16213e" if row > 0 else "#0D47A1")
    cell.set_edgecolor("#333")
    cell.set_text_props(color="#E0E0E0")
    if row > 0 and col == 5:  # Rejim rengi
        regime = table_data[row][5]
        if regime == "TRENDING":
            cell.set_facecolor("#1B5E20")
        elif regime == "RANGING":
            cell.set_facecolor("#B71C1C")
        else:
            cell.set_facecolor("#E65100")
ax4.set_title(f"{SYMBOL} Özet Tablo", color="#E0E0E0", fontsize=11, pad=10)

# ── 5. Her TF için Mum + Zigzag Grafiği ──
for idx, r in enumerate(results):
    ax = fig.add_subplot(gs[2 + idx, :])
    ax.set_facecolor("#16213e")

    df = r["df"]
    closes = df["close"].values.astype(float)
    opens = df["open"].values.astype(float)
    highs = df["high"].values.astype(float)
    lows = df["low"].values.astype(float)
    n = len(closes)
    xc = np.arange(n)

    # Mumlar
    for i in range(n):
        color = "#00C853" if closes[i] >= opens[i] else "#FF1744"
        ax.plot([xc[i], xc[i]], [lows[i], highs[i]], color=color, linewidth=0.4)
        body_b = min(opens[i], closes[i])
        body_h = max(abs(closes[i] - opens[i]), (highs[i] - lows[i]) * 0.003)
        ax.bar(xc[i], body_h, bottom=body_b, width=0.6, color=color,
               edgecolor=color, linewidth=0.2)

    # Zigzag çizgisi
    swings = r["swings_data"]
    if len(swings) >= 2:
        sx = [s.index for s in swings]
        sy = [s.price for s in swings]
        ax.plot(sx, sy, "o-", color="#FFEB3B", linewidth=1.5, markersize=4, alpha=0.9)

        # Dalga boylarını göster
        for i in range(1, len(swings)):
            prev_s = swings[i - 1]
            curr_s = swings[i]
            wave_pct = abs(curr_s.price - prev_s.price) / prev_s.price * 100
            mid_x = (prev_s.index + curr_s.index) / 2
            mid_y = (prev_s.price + curr_s.price) / 2
            is_up = curr_s.price > prev_s.price
            color_w = "#00C853" if is_up else "#FF1744"
            ax.text(mid_x, mid_y, f"{wave_pct:.2f}%", fontsize=6, color=color_w,
                    ha="center", va="center",
                    bbox=dict(boxstyle="round,pad=0.15", fc="#16213e", ec=color_w, alpha=0.8))

    # Başlık
    regime_clr = {"TRENDING": "#00C853", "RANGING": "#FF1744", "GRAY": "#FF9800"}
    ax.set_title(
        f"{r['tf']}  |  G={r['G']:.3f}%  I={r['I']:.3f}%  |  "
        f"ER={r['er']:.3f}  H={r['hurst']:.3f}  |  "
        f"Rejim={r['regime']}  Yön={r['trend_dir']}  |  "
        f"Lev={r['max_lev']}x  SL={r['sl_pct']:.2f}%",
        color=regime_clr.get(r["regime"], "#E0E0E0"), fontsize=9, loc="left", pad=3
    )

    # X ekseni sadece son grafik
    if idx < len(results) - 1:
        ax.tick_params(labelbottom=False)
    else:
        # Son 12 label
        step = max(1, n // 12)
        ticks = list(range(0, n, step))
        labels = [pd.Timestamp(df["timestamp"].iloc[p]).strftime("%m/%d %H:%M") for p in ticks]
        ax.set_xticks(ticks)
        ax.set_xticklabels(labels, fontsize=7, color="#E0E0E0")

    ax.tick_params(colors="#E0E0E0", labelsize=7)
    ax.grid(True, color="#333", alpha=0.2)
    for spine in ax.spines.values():
        spine.set_color("#333")

out_path = os.path.join(os.path.dirname(__file__), "btc_g_timeline_analysis.png")
fig.savefig(out_path, bbox_inches="tight", facecolor="#1a1a2e")
plt.close(fig)
print(f"\nGrafik kaydedildi: {out_path}")
