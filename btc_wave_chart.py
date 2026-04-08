"""
BTC 15m Zigzag Wave Chart — G ve I dalgalarını gösterir
Crypthos System I mantığıyla: detect_zigzag_swings (N=10)
"""
import requests
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from datetime import datetime
from dataclasses import dataclass

# --- Zigzag Detection (System B/I logic) ---

@dataclass
class SwingPoint:
    index: int
    price: float
    type: str  # "SH" or "SL"
    confirmed: bool = True

def detect_zigzag_swings(highs, lows, n=10):
    length = len(highs)
    if length < 2 * n + 1:
        return []
    swings = []
    for i in range(n, length - n):
        left_highs = highs[i - n:i]
        right_highs = highs[i + 1:i + n + 1]
        if highs[i] > np.max(left_highs) and highs[i] > np.max(right_highs):
            swings.append(SwingPoint(index=i, price=float(highs[i]), type="SH"))
        left_lows = lows[i - n:i]
        right_lows = lows[i + 1:i + n + 1]
        if lows[i] < np.min(left_lows) and lows[i] < np.min(right_lows):
            swings.append(SwingPoint(index=i, price=float(lows[i]), type="SL"))
    swings.sort(key=lambda s: s.index)
    # Clean consecutive same-type
    cleaned = []
    for s in swings:
        if cleaned and cleaned[-1].type == s.type:
            if s.type == "SH" and s.price > cleaned[-1].price:
                cleaned[-1] = s
            elif s.type == "SL" and s.price < cleaned[-1].price:
                cleaned[-1] = s
        else:
            cleaned.append(s)
    return cleaned

def analyze_waves(swings):
    if len(swings) < 3:
        return None
    # Trend direction from last two swings
    last_two = swings[-2:]
    if last_two[-1].type == "SH" and last_two[-2].type == "SL":
        trend_dir = "UP"
    elif last_two[-1].type == "SL" and last_two[-2].type == "SH":
        trend_dir = "DOWN"
    else:
        trend_dir = "UNKNOWN"

    forward_waves = []
    backward_waves = []
    wave_segments = []  # (start_idx, end_idx, pct, is_forward)

    for i in range(1, len(swings)):
        prev = swings[i-1]
        curr = swings[i]
        pct = abs(curr.price - prev.price) / prev.price * 100
        is_up = curr.price > prev.price

        if trend_dir == "UP":
            is_forward = is_up
        elif trend_dir == "DOWN":
            is_forward = not is_up
        else:
            is_forward = is_up

        if is_forward:
            forward_waves.append(pct)
        else:
            backward_waves.append(pct)
        wave_segments.append((prev, curr, pct, is_forward))

    G = np.mean(backward_waves) if backward_waves else 0
    I = np.mean(forward_waves) if forward_waves else 0
    return {
        'trend': trend_dir,
        'G': G, 'I': I,
        'forward_waves': forward_waves,
        'backward_waves': backward_waves,
        'segments': wave_segments,
        'swings': swings
    }

# --- Fetch BTC 15m data from Binance ---
print("Fetching BTC 15m candles from Binance...")
url = "https://fapi.binance.com/fapi/v1/klines"
params = {"symbol": "BTCUSDT", "interval": "15m", "limit": 500}
resp = requests.get(url, params=params)
data = resp.json()

times = [datetime.fromtimestamp(k[0]/1000) for k in data]
opens = np.array([float(k[1]) for k in data])
highs = np.array([float(k[2]) for k in data])
lows = np.array([float(k[3]) for k in data])
closes = np.array([float(k[4]) for k in data])

print(f"Fetched {len(data)} candles: {times[0]} -> {times[-1]}")

# --- Detect swings ---
swings = detect_zigzag_swings(highs, lows, n=5)
print(f"Detected {len(swings)} swing points")

analysis = analyze_waves(swings)
if analysis:
    print(f"Trend: {analysis['trend']}")
    print(f"G (geri dalga ort): {analysis['G']:.3f}%")
    print(f"I (ileri dalga ort): {analysis['I']:.3f}%")
    print(f"Forward waves: {len(analysis['forward_waves'])}, Backward waves: {len(analysis['backward_waves'])}")

# --- Draw Chart ---
fig, ax = plt.subplots(figsize=(22, 10))
fig.patch.set_facecolor('#1a1a2e')
ax.set_facecolor('#16213e')

# Candlestick-style plot
for i in range(len(times)):
    color = '#26a69a' if closes[i] >= opens[i] else '#ef5350'
    ax.plot([times[i], times[i]], [lows[i], highs[i]], color=color, linewidth=0.6, alpha=0.5)
    ax.plot([times[i], times[i]], [min(opens[i], closes[i]), max(opens[i], closes[i])],
            color=color, linewidth=2.0, alpha=0.7)

# Close price line (subtle)
ax.plot(times, closes, color='#ffffff', linewidth=0.5, alpha=0.3)

# Draw zigzag line
if analysis:
    swing_times = [times[s.index] for s in swings]
    swing_prices = [s.price for s in swings]

    # Draw wave segments with colors
    for prev_s, curr_s, pct, is_forward in analysis['segments']:
        t0, t1 = times[prev_s.index], times[curr_s.index]
        p0, p1 = prev_s.price, curr_s.price

        if is_forward:
            color = '#00e5ff'  # cyan for I (ileri/forward)
            label_color = '#00e5ff'
            wave_label = f"I:{pct:.2f}%"
        else:
            color = '#ff6e40'  # orange for G (geri/backward)
            label_color = '#ff6e40'
            wave_label = f"G:{pct:.2f}%"

        ax.plot([t0, t1], [p0, p1], color=color, linewidth=2.5, alpha=0.9, zorder=5)

        # Label at midpoint
        mid_t = times[(prev_s.index + curr_s.index) // 2]
        mid_p = (p0 + p1) / 2
        offset = 15 if is_forward else -15
        ax.annotate(wave_label, xy=(mid_t, mid_p), fontsize=7, fontweight='bold',
                    color=label_color, ha='center', va='center',
                    xytext=(0, offset), textcoords='offset points',
                    bbox=dict(boxstyle='round,pad=0.2', facecolor='#1a1a2e', edgecolor=label_color, alpha=0.8))

    # Mark swing points
    for s in swings:
        if s.type == "SH":
            ax.plot(times[s.index], s.price, marker='v', color='#ff1744', markersize=10, zorder=10)
            ax.annotate(f"SH\n{s.price:.0f}", xy=(times[s.index], s.price), fontsize=6.5,
                        color='#ff1744', ha='center', va='bottom', xytext=(0, 8),
                        textcoords='offset points', fontweight='bold')
        else:
            ax.plot(times[s.index], s.price, marker='^', color='#00e676', markersize=10, zorder=10)
            ax.annotate(f"SL\n{s.price:.0f}", xy=(times[s.index], s.price), fontsize=6.5,
                        color='#00e676', ha='center', va='top', xytext=(0, -8),
                        textcoords='offset points', fontweight='bold')

# Stats box
if analysis:
    stats_text = (
        f"BTCUSDT 15m — Zigzag N=5\n"
        f"Trend: {analysis['trend']}\n"
        f"G (geri dalga ort): {analysis['G']:.3f}%\n"
        f"I (ileri dalga ort): {analysis['I']:.3f}%\n"
        f"I/G Oranı: {analysis['I']/analysis['G']:.2f}\n"
        f"Forward waves: {len(analysis['forward_waves'])}\n"
        f"Backward waves: {len(analysis['backward_waves'])}\n"
        f"Total swings: {len(swings)}\n"
        f"SL (1.5×G): {1.5*analysis['G']:.3f}%\n"
        f"Trailing Trigger (2.5×G): {2.5*analysis['G']:.3f}%\n"
        f"Trailing Callback (0.5×G): {0.5*analysis['G']:.3f}%"
    )
    props = dict(boxstyle='round,pad=0.8', facecolor='#0a0a23', edgecolor='#00e5ff', alpha=0.92)
    ax.text(0.01, 0.98, stats_text, transform=ax.transAxes, fontsize=9, fontfamily='monospace',
            verticalalignment='top', bbox=props, color='#e0e0e0')

# Legend
from matplotlib.lines import Line2D
legend_elements = [
    Line2D([0], [0], color='#00e5ff', lw=2.5, label='I dalga (ileri/forward)'),
    Line2D([0], [0], color='#ff6e40', lw=2.5, label='G dalga (geri/backward)'),
    Line2D([0], [0], marker='v', color='#ff1744', lw=0, markersize=8, label='Swing High (SH)'),
    Line2D([0], [0], marker='^', color='#00e676', lw=0, markersize=8, label='Swing Low (SL)'),
]
ax.legend(handles=legend_elements, loc='upper right', fontsize=9,
          facecolor='#0a0a23', edgecolor='#00e5ff', labelcolor='#e0e0e0')

ax.set_title("BTCUSDT 15m — G & I Wave Analysis (Zigzag N=5)",
             fontsize=16, fontweight='bold', color='#e0e0e0', pad=15)
ax.set_xlabel("Time", fontsize=11, color='#a0a0a0')
ax.set_ylabel("Price (USDT)", fontsize=11, color='#a0a0a0')
ax.tick_params(colors='#a0a0a0')
ax.xaxis.set_major_formatter(mdates.DateFormatter('%m-%d %H:%M'))
plt.xticks(rotation=30)
ax.grid(True, alpha=0.15, color='#ffffff')

plt.tight_layout()
out_path = "btc_15m_wave_chart_n5.png"
plt.savefig(out_path, dpi=150, bbox_inches='tight')
print(f"\nChart saved: {out_path}")
