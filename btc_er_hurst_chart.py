"""
BTC 15m - ER (Efficiency Ratio) ve Hurst Exponent rolling chart
Rejim tespiti: TREND / RANGING / BELIRSIZ
"""
import requests
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from datetime import datetime

# --- ER & Hurst (System B logic) ---

def compute_efficiency_ratio(closes):
    if len(closes) < 2:
        return 0.5
    net_move = abs(closes[-1] - closes[0])
    total_move = np.sum(np.abs(np.diff(closes)))
    if total_move == 0:
        return 0.0
    return net_move / total_move

def compute_hurst_exponent(closes):
    if len(closes) < 128:
        return 0.5
    log_returns = np.diff(np.log(closes))
    ns = [16, 32, 64, 128]
    ns = [n for n in ns if n <= len(log_returns)]
    if len(ns) < 2:
        return 0.5
    rs_values = []
    for n in ns:
        rs_list = []
        num_chunks = len(log_returns) // n
        for i in range(num_chunks):
            chunk = log_returns[i * n:(i + 1) * n]
            mean_chunk = np.mean(chunk)
            deviations = np.cumsum(chunk - mean_chunk)
            R = np.max(deviations) - np.min(deviations)
            S = np.std(chunk, ddof=1)
            if S > 0:
                rs_list.append(R / S)
        if rs_list:
            rs_values.append((np.log(n), np.log(np.mean(rs_list))))
    if len(rs_values) < 2:
        return 0.5
    x = np.array([v[0] for v in rs_values])
    y = np.array([v[1] for v in rs_values])
    n_pts = len(x)
    H = (n_pts * np.sum(x * y) - np.sum(x) * np.sum(y)) / \
        (n_pts * np.sum(x ** 2) - np.sum(x) ** 2)
    return float(np.clip(H, 0.0, 1.0))

# --- Fetch BTC 15m data ---
print("Fetching BTC 15m candles from Binance...")
url = "https://fapi.binance.com/fapi/v1/klines"
params = {"symbol": "BTCUSDT", "interval": "15m", "limit": 1000}
resp = requests.get(url, params=params)
data = resp.json()

times = [datetime.fromtimestamp(k[0]/1000) for k in data]
closes = np.array([float(k[4]) for k in data])
highs = np.array([float(k[2]) for k in data])
lows = np.array([float(k[3]) for k in data])
opens = np.array([float(k[1]) for k in data])

print(f"Fetched {len(data)} candles: {times[0]} -> {times[-1]}")

# --- Rolling ER & Hurst ---
er_window = 64
hurst_window = 200
er_values = []
er_times = []
hurst_values = []
hurst_times = []

for i in range(er_window, len(closes)):
    window = closes[i - er_window:i + 1]
    er_values.append(compute_efficiency_ratio(window))
    er_times.append(times[i])

for i in range(hurst_window, len(closes)):
    window = closes[i - hurst_window:i + 1]
    hurst_values.append(compute_hurst_exponent(window))
    hurst_times.append(times[i])

er_values = np.array(er_values)
hurst_values = np.array(hurst_values)

# Current values
current_er = er_values[-1]
current_hurst = hurst_values[-1]

# Regime decision (2/3 voting: ER + Hurst)
def get_regime(er, hurst):
    votes_trend = 0
    votes_range = 0
    if er > 0.3:
        votes_trend += 1
    else:
        votes_range += 1
    if hurst > 0.55:
        votes_trend += 1
    elif hurst < 0.45:
        votes_range += 1
    if votes_trend > votes_range:
        return "TREND"
    elif votes_range > votes_trend:
        return "RANGING"
    else:
        return "BELIRSIZ"

current_regime = get_regime(current_er, current_hurst)
print(f"Current ER: {current_er:.4f}")
print(f"Current Hurst: {current_hurst:.4f}")
print(f"Regime: {current_regime}")

# --- Draw Chart ---
fig, axes = plt.subplots(3, 1, figsize=(22, 14), height_ratios=[3, 1.2, 1.2],
                         gridspec_kw={'hspace': 0.08})
fig.patch.set_facecolor('#1a1a2e')

# ============ Panel 1: Price ============
ax1 = axes[0]
ax1.set_facecolor('#16213e')

# Candlesticks
for i in range(len(times)):
    color = '#26a69a' if closes[i] >= opens[i] else '#ef5350'
    ax1.plot([times[i], times[i]], [lows[i], highs[i]], color=color, linewidth=0.5, alpha=0.5)
    ax1.plot([times[i], times[i]], [min(opens[i], closes[i]), max(opens[i], closes[i])],
             color=color, linewidth=1.8, alpha=0.7)

# Background regime coloring (based on common time range)
common_start = max(er_window, hurst_window)
for i in range(1, len(er_times)):
    # find matching hurst
    t = er_times[i]
    # find hurst index for this time
    h_idx = None
    for j, ht in enumerate(hurst_times):
        if ht == t:
            h_idx = j
            break
    if h_idx is None:
        continue
    regime = get_regime(er_values[i], hurst_values[h_idx])
    if regime == "TREND":
        bg_color = '#00e5ff'
    elif regime == "RANGING":
        bg_color = '#ff6e40'
    else:
        bg_color = '#ffeb3b'
    ax1.axvspan(er_times[i-1], er_times[i], alpha=0.06, color=bg_color, linewidth=0)

ax1.set_title("BTCUSDT 15m - ER & Hurst Rejim Analizi", fontsize=16,
              fontweight='bold', color='#e0e0e0', pad=15)
ax1.set_ylabel("Price (USDT)", fontsize=11, color='#a0a0a0')
ax1.tick_params(colors='#a0a0a0')
ax1.grid(True, alpha=0.12, color='#ffffff')
ax1.set_xticklabels([])

# Regime label
regime_colors = {"TREND": "#00e5ff", "RANGING": "#ff6e40", "BELIRSIZ": "#ffeb3b"}
regime_label = f"REJIM: {current_regime}"
props = dict(boxstyle='round,pad=0.6', facecolor='#0a0a23',
             edgecolor=regime_colors[current_regime], alpha=0.95)
ax1.text(0.5, 0.95, regime_label, transform=ax1.transAxes, fontsize=18,
         fontweight='bold', ha='center', va='top', color=regime_colors[current_regime],
         bbox=props)

# Stats box
stats = (
    f"ER (son): {current_er:.4f}  {'> 0.30 TREND' if current_er > 0.3 else '< 0.30 RANGING'}\n"
    f"Hurst (son): {current_hurst:.4f}  "
    f"{'> 0.55 TREND' if current_hurst > 0.55 else '< 0.45 RANGING' if current_hurst < 0.45 else '0.45-0.55 BELIRSIZ'}\n"
    f"Karar: {current_regime}"
)
props2 = dict(boxstyle='round,pad=0.6', facecolor='#0a0a23', edgecolor='#00e5ff', alpha=0.92)
ax1.text(0.01, 0.95, stats, transform=ax1.transAxes, fontsize=9, fontfamily='monospace',
         va='top', color='#e0e0e0', bbox=props2)

# ============ Panel 2: ER ============
ax2 = axes[1]
ax2.set_facecolor('#16213e')
ax2.fill_between(er_times, er_values, alpha=0.3, color='#00e5ff')
ax2.plot(er_times, er_values, color='#00e5ff', linewidth=1.5, label=f'ER (window={er_window})')

# Threshold lines
ax2.axhline(y=0.3, color='#ff6e40', linestyle='--', linewidth=1.2, alpha=0.8, label='ER=0.3 (esik)')
ax2.axhline(y=0.5, color='#ffeb3b', linestyle=':', linewidth=0.8, alpha=0.5)

# Color zones
ax2.axhspan(0.3, 1.0, alpha=0.05, color='#00e5ff')  # trend zone
ax2.axhspan(0.0, 0.3, alpha=0.05, color='#ff6e40')  # ranging zone

ax2.text(er_times[5], 0.65, "TREND BOLGESI", fontsize=8, color='#00e5ff', alpha=0.6, fontweight='bold')
ax2.text(er_times[5], 0.12, "RANGING BOLGESI", fontsize=8, color='#ff6e40', alpha=0.6, fontweight='bold')

# Current value marker
ax2.plot(er_times[-1], current_er, 'o', color='#ffffff', markersize=8, zorder=10)
ax2.annotate(f"{current_er:.3f}", xy=(er_times[-1], current_er),
             fontsize=9, fontweight='bold', color='#ffffff',
             xytext=(-50, 10), textcoords='offset points')

ax2.set_ylabel("ER", fontsize=11, color='#a0a0a0')
ax2.set_ylim(0, 0.85)
ax2.tick_params(colors='#a0a0a0')
ax2.grid(True, alpha=0.12, color='#ffffff')
ax2.legend(loc='upper right', fontsize=8, facecolor='#0a0a23', edgecolor='#00e5ff', labelcolor='#e0e0e0')
ax2.set_xticklabels([])

# ============ Panel 3: Hurst ============
ax3 = axes[2]
ax3.set_facecolor('#16213e')
ax3.fill_between(hurst_times, hurst_values, 0.5, alpha=0.3,
                 where=hurst_values > 0.5, color='#00e5ff')
ax3.fill_between(hurst_times, hurst_values, 0.5, alpha=0.3,
                 where=hurst_values < 0.5, color='#ff6e40')
ax3.plot(hurst_times, hurst_values, color='#e040fb', linewidth=1.5, label=f'Hurst (window={hurst_window})')

# Threshold lines
ax3.axhline(y=0.55, color='#00e5ff', linestyle='--', linewidth=1.0, alpha=0.7, label='H=0.55 (trend)')
ax3.axhline(y=0.45, color='#ff6e40', linestyle='--', linewidth=1.0, alpha=0.7, label='H=0.45 (ranging)')
ax3.axhline(y=0.50, color='#ffeb3b', linestyle=':', linewidth=0.8, alpha=0.5, label='H=0.50 (random walk)')

# Zone labels
ax3.text(hurst_times[5], 0.70, "TREND (H>0.55)", fontsize=8, color='#00e5ff', alpha=0.6, fontweight='bold')
ax3.text(hurst_times[5], 0.35, "RANGING (H<0.45)", fontsize=8, color='#ff6e40', alpha=0.6, fontweight='bold')
ax3.text(hurst_times[5], 0.51, "BELIRSIZ", fontsize=7, color='#ffeb3b', alpha=0.5)

# Current value marker
ax3.plot(hurst_times[-1], current_hurst, 'o', color='#ffffff', markersize=8, zorder=10)
ax3.annotate(f"{current_hurst:.3f}", xy=(hurst_times[-1], current_hurst),
             fontsize=9, fontweight='bold', color='#ffffff',
             xytext=(-50, 10), textcoords='offset points')

ax3.set_ylabel("Hurst", fontsize=11, color='#a0a0a0')
ax3.set_ylim(0.2, 0.85)
ax3.tick_params(colors='#a0a0a0')
ax3.grid(True, alpha=0.12, color='#ffffff')
ax3.legend(loc='upper right', fontsize=8, facecolor='#0a0a23', edgecolor='#e040fb', labelcolor='#e0e0e0')
ax3.xaxis.set_major_formatter(mdates.DateFormatter('%m-%d %H:%M'))
plt.setp(ax3.xaxis.get_majorticklabels(), rotation=30)
ax3.set_xlabel("Time", fontsize=11, color='#a0a0a0')

plt.tight_layout()
out_path = "btc_15m_er_hurst.png"
plt.savefig(out_path, dpi=150, bbox_inches='tight')
print(f"\nChart saved: {out_path}")
