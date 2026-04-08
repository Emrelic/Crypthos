"""Elliott Wave + Reaktif Sistem Backtest.

3 varyant karsilastirmasi:
A: Mevcut System J (ER+Hurst rejim, prediktif)
B: Saf Reaktif (rejim yok, tight SL, trailing, flat exit)
C: Elliott + Reaktif (sadece Elliott pattern bulundugunda giris, reaktif cikis)

8 coin x 15m TF, bar-by-bar simulasyon.
"""

import os, time, hmac, hashlib, requests, numpy as np
from urllib.parse import urlencode
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()
API_KEY = os.getenv("BINANCE_API_KEY")
API_SECRET = os.getenv("BINANCE_API_SECRET")
session = requests.Session()
session.headers["X-MBX-APIKEY"] = API_KEY
BASE = "https://fapi.binance.com"

# Elliott Wave import
from analysis.elliott_wave import (
    detect_elliott, detect_zigzag_swings, ElliottPattern, project_next_wave
)


def sign(p):
    p["timestamp"] = int(time.time() * 1000)
    p["signature"] = hmac.new(API_SECRET.encode(), urlencode(p).encode(), hashlib.sha256).hexdigest()
    return p


def fetch_klines(symbol, interval, limit=1500):
    resp = session.get(f"{BASE}/fapi/v1/klines",
                       params={"symbol": symbol, "interval": interval, "limit": limit})
    data = resp.json()
    return data if isinstance(data, list) else None


# ════════════════ INDIKATORLER ════════════════

def ema_series(c, p):
    a = 2.0 / (p + 1)
    e = np.zeros(len(c)); e[0] = c[0]
    for i in range(1, len(c)): e[i] = a * c[i] + (1 - a) * e[i - 1]
    return e


def compute_rsi(c, p=14):
    n = len(c); r = np.full(n, 50.0)
    if n < p + 1: return r
    d = np.diff(c); g = np.where(d > 0, d, 0.0); l = np.where(d < 0, -d, 0.0)
    ag = np.mean(g[:p]); al = np.mean(l[:p])
    for i in range(p, len(d)):
        ag = (ag * (p - 1) + g[i]) / p; al = (al * (p - 1) + l[i]) / p
        r[i + 1] = 100 - 100 / (1 + ag / al) if al > 0 else 100.0
    return r


def compute_macd_hist(c, f=12, s=26, sg=9):
    return ema_series(c, f) - ema_series(c, s) - ema_series(ema_series(c, f) - ema_series(c, s), sg)


def compute_atr(h, l, c, p=14):
    n = len(c); atr = np.zeros(n); tr = np.zeros(n)
    for i in range(1, n):
        tr[i] = max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1]))
    if n > p:
        atr[p] = np.mean(tr[1:p + 1])
        for i in range(p + 1, n): atr[i] = (atr[i - 1] * (p - 1) + tr[i]) / p
    return atr


def compute_bb(c, p=20, m=2.0):
    n = len(c); u = np.zeros(n); l = np.zeros(n); mid = np.zeros(n)
    for i in range(p - 1, n):
        w = c[i - p + 1:i + 1]; mu = np.mean(w); s = np.std(w, ddof=1)
        mid[i] = mu; u[i] = mu + m * s; l[i] = mu - m * s
    return u, mid, l


def compute_er(c, window=20):
    n = len(c); ers = np.zeros(n)
    for i in range(window, n):
        seg = c[i - window + 1:i + 1]
        net = abs(seg[-1] - seg[0]); total = np.sum(np.abs(np.diff(seg)))
        ers[i] = net / total if total > 0 else 0.0
    return ers


def direction_score(ema9, ema21, macd_h, rsi, idx):
    v = 0.0
    if ema9[idx] > ema21[idx] * 1.0005: v += 1
    elif ema9[idx] < ema21[idx] * 0.9995: v -= 1
    if macd_h[idx] > 0: v += 1
    elif macd_h[idx] < 0: v -= 1
    if rsi[idx] > 55: v += 1
    elif rsi[idx] < 45: v -= 1
    return v / 3.0


# ════════════════ TRADE SIMULATION ════════════════

FEE_TAKER = 0.0004
FEE_MAKER = 0.0002


@dataclass
class Trade:
    symbol: str = ""
    direction: str = ""
    entry_price: float = 0
    exit_price: float = 0
    entry_bar: int = 0
    exit_bar: int = 0
    sl_price: float = 0
    tp_price: float = 0
    pnl_pct: float = 0
    exit_reason: str = ""
    regime_at_entry: str = ""
    bars_held: int = 0
    variant: str = ""          # A, B, C
    elliott_type: str = ""     # IMPULSE_5, CORRECTION_ABC


def sim_trade_reactive(closes, highs, lows, atr, entry_idx, direction, entry_price, config):
    """Reaktif trade simulasyonu: tight SL, trailing, flat exit."""
    sl_atr_mult = config.get("sl_atr_mult", 1.0)
    trail_trigger_atr = config.get("trail_trigger_atr", 1.0)
    trail_callback_atr = config.get("trail_callback_atr", 0.3)
    max_bars = config.get("max_bars", 40)
    flat_exit_bars = config.get("flat_exit_bars", 15)
    flat_threshold_atr = config.get("flat_threshold_atr", 0.3)

    current_atr = atr[entry_idx]
    if current_atr <= 0:
        return None

    if direction == "LONG":
        sl_price = entry_price - sl_atr_mult * current_atr
    else:
        sl_price = entry_price + sl_atr_mult * current_atr

    trailing_active = False
    best_price = entry_price

    for bar in range(entry_idx + 1, min(entry_idx + max_bars + 1, len(closes))):
        high = highs[bar]
        low = lows[bar]
        close = closes[bar]
        bars_held = bar - entry_idx

        # SL
        if direction == "LONG" and low <= sl_price:
            fee = entry_price * FEE_TAKER + sl_price * FEE_TAKER
            pnl = (sl_price - entry_price) / entry_price * 100 - fee / entry_price * 100
            return Trade(direction=direction, entry_price=entry_price, exit_price=sl_price,
                         entry_bar=entry_idx, exit_bar=bar, pnl_pct=pnl,
                         exit_reason="SL", bars_held=bars_held)
        if direction == "SHORT" and high >= sl_price:
            fee = entry_price * FEE_TAKER + sl_price * FEE_TAKER
            pnl = (entry_price - sl_price) / entry_price * 100 - fee / entry_price * 100
            return Trade(direction=direction, entry_price=entry_price, exit_price=sl_price,
                         entry_bar=entry_idx, exit_bar=bar, pnl_pct=pnl,
                         exit_reason="SL", bars_held=bars_held)

        # Trailing trigger
        if not trailing_active:
            if direction == "LONG":
                fav = (high - entry_price) / current_atr
            else:
                fav = (entry_price - low) / current_atr
            if fav >= trail_trigger_atr:
                trailing_active = True
                best_price = high if direction == "LONG" else low

        # Trailing aktif
        if trailing_active:
            if direction == "LONG":
                if high > best_price: best_price = high
                tsl = best_price - trail_callback_atr * current_atr
                if low <= tsl:
                    fee = entry_price * FEE_TAKER + tsl * FEE_TAKER
                    pnl = (tsl - entry_price) / entry_price * 100 - fee / entry_price * 100
                    return Trade(direction=direction, entry_price=entry_price, exit_price=tsl,
                                 entry_bar=entry_idx, exit_bar=bar, pnl_pct=pnl,
                                 exit_reason="TRAIL", bars_held=bars_held)
            else:
                if low < best_price: best_price = low
                tsl = best_price + trail_callback_atr * current_atr
                if high >= tsl:
                    fee = entry_price * FEE_TAKER + tsl * FEE_TAKER
                    pnl = (entry_price - tsl) / entry_price * 100 - fee / entry_price * 100
                    return Trade(direction=direction, entry_price=entry_price, exit_price=tsl,
                                 entry_bar=entry_idx, exit_bar=bar, pnl_pct=pnl,
                                 exit_reason="TRAIL", bars_held=bars_held)

        # Flat cikis
        if not trailing_active and bars_held >= flat_exit_bars:
            move = abs(close - entry_price) / current_atr
            if move < flat_threshold_atr:
                fee = entry_price * FEE_MAKER + close * FEE_MAKER
                pnl_raw = (close - entry_price) / entry_price * 100 if direction == "LONG" \
                    else (entry_price - close) / entry_price * 100
                return Trade(direction=direction, entry_price=entry_price, exit_price=close,
                             entry_bar=entry_idx, exit_bar=bar, pnl_pct=pnl_raw - fee / entry_price * 100,
                             exit_reason="FLAT", bars_held=bars_held)

    # Max bar
    bar = min(entry_idx + max_bars, len(closes) - 1)
    ep = closes[bar]
    fee = entry_price * FEE_TAKER + ep * FEE_TAKER
    pnl_raw = (ep - entry_price) / entry_price * 100 if direction == "LONG" \
        else (entry_price - ep) / entry_price * 100
    reason = "TRAIL_MAX" if trailing_active else "TIME"
    return Trade(direction=direction, entry_price=entry_price, exit_price=ep,
                 entry_bar=entry_idx, exit_bar=bar, pnl_pct=pnl_raw - fee / entry_price * 100,
                 exit_reason=reason, bars_held=bar - entry_idx)


def sim_trade_predictive(closes, highs, lows, atr, bb_upper, bb_mid, bb_lower,
                          entry_idx, direction, entry_price, regime, config):
    """Prediktif trade simulasyonu (System J mantigi)."""
    max_bars = config.get("max_bars", 40)
    current_atr = atr[entry_idx]
    if current_atr <= 0:
        return None

    if regime == "TRENDING":
        sl_mult = config.get("trend_sl_atr", 2.0)
        trail_trigger = config.get("trend_trail_trigger", 2.5)
        trail_cb = config.get("trend_trail_cb", 0.5)
    else:
        sl_mult = config.get("range_sl_atr", 1.5)
        trail_trigger = 999
        trail_cb = 0.5

    if direction == "LONG":
        sl_price = entry_price - sl_mult * current_atr
        tp_price = bb_mid[entry_idx] if regime == "RANGING" and bb_mid[entry_idx] > entry_price else 0
    else:
        sl_price = entry_price + sl_mult * current_atr
        tp_price = bb_mid[entry_idx] if regime == "RANGING" and bb_mid[entry_idx] < entry_price else 0

    trailing_active = False
    best_price = entry_price

    for bar in range(entry_idx + 1, min(entry_idx + max_bars + 1, len(closes))):
        high = highs[bar]
        low = lows[bar]
        bars_held = bar - entry_idx

        # SL
        if direction == "LONG" and low <= sl_price:
            fee = entry_price * FEE_TAKER + sl_price * FEE_TAKER
            pnl = (sl_price - entry_price) / entry_price * 100 - fee / entry_price * 100
            return Trade(direction=direction, entry_price=entry_price, exit_price=sl_price,
                         entry_bar=entry_idx, exit_bar=bar, pnl_pct=pnl,
                         exit_reason="SL", bars_held=bars_held, regime_at_entry=regime)
        if direction == "SHORT" and high >= sl_price:
            fee = entry_price * FEE_TAKER + sl_price * FEE_TAKER
            pnl = (entry_price - sl_price) / entry_price * 100 - fee / entry_price * 100
            return Trade(direction=direction, entry_price=entry_price, exit_price=sl_price,
                         entry_bar=entry_idx, exit_bar=bar, pnl_pct=pnl,
                         exit_reason="SL", bars_held=bars_held, regime_at_entry=regime)

        # TP (ranging)
        if tp_price > 0:
            if direction == "LONG" and high >= tp_price:
                fee = entry_price * FEE_MAKER + tp_price * FEE_MAKER
                pnl = (tp_price - entry_price) / entry_price * 100 - fee / entry_price * 100
                return Trade(direction=direction, entry_price=entry_price, exit_price=tp_price,
                             entry_bar=entry_idx, exit_bar=bar, pnl_pct=pnl,
                             exit_reason="TP_BB", bars_held=bars_held, regime_at_entry=regime)
            if direction == "SHORT" and low <= tp_price:
                fee = entry_price * FEE_MAKER + tp_price * FEE_MAKER
                pnl = (entry_price - tp_price) / entry_price * 100 - fee / entry_price * 100
                return Trade(direction=direction, entry_price=entry_price, exit_price=tp_price,
                             entry_bar=entry_idx, exit_bar=bar, pnl_pct=pnl,
                             exit_reason="TP_BB", bars_held=bars_held, regime_at_entry=regime)

        # Trailing (trending)
        if regime == "TRENDING":
            if not trailing_active:
                fav = (high - entry_price) / current_atr if direction == "LONG" \
                    else (entry_price - low) / current_atr
                if fav >= trail_trigger:
                    trailing_active = True
                    best_price = high if direction == "LONG" else low
            if trailing_active:
                if direction == "LONG":
                    if high > best_price: best_price = high
                    tsl = best_price - trail_cb * current_atr
                    if low <= tsl:
                        fee = entry_price * FEE_TAKER + tsl * FEE_TAKER
                        pnl = (tsl - entry_price) / entry_price * 100 - fee / entry_price * 100
                        return Trade(direction=direction, entry_price=entry_price, exit_price=tsl,
                                     entry_bar=entry_idx, exit_bar=bar, pnl_pct=pnl,
                                     exit_reason="TRAIL", bars_held=bars_held, regime_at_entry=regime)
                else:
                    if low < best_price: best_price = low
                    tsl = best_price + trail_cb * current_atr
                    if high >= tsl:
                        fee = entry_price * FEE_TAKER + tsl * FEE_TAKER
                        pnl = (entry_price - tsl) / entry_price * 100 - fee / entry_price * 100
                        return Trade(direction=direction, entry_price=entry_price, exit_price=tsl,
                                     entry_bar=entry_idx, exit_bar=bar, pnl_pct=pnl,
                                     exit_reason="TRAIL", bars_held=bars_held, regime_at_entry=regime)

    bar = min(entry_idx + max_bars, len(closes) - 1)
    ep = closes[bar]
    fee = entry_price * FEE_TAKER + ep * FEE_TAKER
    pnl_raw = (ep - entry_price) / entry_price * 100 if direction == "LONG" \
        else (entry_price - ep) / entry_price * 100
    return Trade(direction=direction, entry_price=entry_price, exit_price=ep,
                 entry_bar=entry_idx, exit_bar=bar, pnl_pct=pnl_raw - fee / entry_price * 100,
                 exit_reason="TIME", bars_held=bar - entry_idx, regime_at_entry=regime)


# ════════════════ SIGNAL GENERATORS ════════════════

def generate_signals(closes, ema9, ema21, macd_h, rsi, atr, min_signal=0.66):
    """Standard yon sinyali (A ve B varyantlari icin)."""
    signals = []
    cooldown = 0
    for i in range(50, len(closes) - 45):
        if cooldown > 0:
            cooldown -= 1
            continue
        score = direction_score(ema9, ema21, macd_h, rsi, i)
        if abs(score) >= min_signal:
            direction = "LONG" if score > 0 else "SHORT"
            signals.append((i, direction))
            cooldown = 10
    return signals


def generate_elliott_signals(closes, highs, lows, ema9, ema21, macd_h, rsi, atr,
                              swing_n=10, min_confidence=0.35):
    """Elliott Wave bazli sinyal uretimi (C varyanti).

    Sadece Elliott pattern tespit edildiginde ve yon teyidi varsa sinyal uretir.
    """
    signals = []
    cooldown = 0

    # Tum swingleri onceden hesapla
    swings = detect_zigzag_swings(highs, lows, n=swing_n)

    if len(swings) < 4:
        return signals

    for i in range(50, len(closes) - 45):
        if cooldown > 0:
            cooldown -= 1
            continue

        # Bu bar'a kadar olan swingleri filtrele
        current_swings = [s for s in swings if s.index <= i]
        if len(current_swings) < 4:
            continue

        # Elliott pattern tespit et
        pattern = detect_elliott(current_swings, min_confidence=min_confidence)
        if pattern is None:
            continue

        # Pattern son bar'a yakin olmali (stale pattern'leri eleme)
        if i - pattern.end_index > 30:
            continue

        # Yon teyidi: Elliott yonu ile indikatör yonu uyusmali
        ind_score = direction_score(ema9, ema21, macd_h, rsi, i)
        elliott_dir = pattern.next_move_dir

        # Elliott yonu ile indikatör en azindan celiski olmamali
        if elliott_dir == "LONG" and ind_score < -0.33:
            continue
        if elliott_dir == "SHORT" and ind_score > 0.33:
            continue

        # ATR kontrolu
        if atr[i] <= 0:
            continue

        signals.append((i, elliott_dir, pattern))
        cooldown = 10

    return signals


def classify_regime_sysj(er_arr, idx):
    start = max(0, idx - 9)
    er_median = float(np.median(er_arr[start:idx + 1]))
    if er_median > 0.25: return "TRENDING"
    if er_median < 0.08: return "RANGING"
    return "TRENDING" if er_median > 0.165 else "RANGING"


# ════════════════ ANA BACKTEST ════════════════

COINS = ["BTCUSDT", "ETHUSDT", "XRPUSDT", "AVAXUSDT", "DOGEUSDT",
         "SOLUSDT", "ADAUSDT", "BNBUSDT"]
TF = "15m"


def summarize_trades(trades):
    if not trades:
        return {"total": 0, "win": 0, "loss": 0, "wr": 0, "total_pnl": 0,
                "avg_pnl": 0, "avg_win": 0, "avg_loss": 0, "pf": 0, "exits": {},
                "avg_bars": 0}
    wins = [t for t in trades if t.pnl_pct > 0]
    losses = [t for t in trades if t.pnl_pct <= 0]
    total_pnl = sum(t.pnl_pct for t in trades)
    avg_win = float(np.mean([t.pnl_pct for t in wins])) if wins else 0
    avg_loss = float(np.mean([t.pnl_pct for t in losses])) if losses else 0
    gross_win = sum(t.pnl_pct for t in wins) if wins else 0
    gross_loss = abs(sum(t.pnl_pct for t in losses)) if losses else 0.001
    exits = {}
    for t in trades:
        exits[t.exit_reason] = exits.get(t.exit_reason, 0) + 1

    return {
        "total": len(trades), "win": len(wins), "loss": len(losses),
        "wr": len(wins) / len(trades) * 100,
        "total_pnl": total_pnl,
        "avg_pnl": total_pnl / len(trades),
        "avg_win": avg_win, "avg_loss": avg_loss,
        "pf": gross_win / gross_loss if gross_loss > 0 else 0,
        "exits": exits,
        "avg_bars": float(np.mean([t.bars_held for t in trades])),
    }


def print_summary(label, s):
    print(f"    Trades: {s['total']} | Win: {s['win']} Loss: {s['loss']} | WR: {s['wr']:.1f}%")
    print(f"    Toplam PnL: {s['total_pnl']:+.3f}% | Avg: {s['avg_pnl']:+.3f}%")
    print(f"    Avg Win: {s['avg_win']:+.3f}% | Avg Loss: {s['avg_loss']:+.3f}% | PF: {s['pf']:.2f}")
    print(f"    Avg Bars: {s['avg_bars']:.1f} | Exits: {s['exits']}")


def run_backtest():
    print("=" * 130)
    print("ELLIOTT WAVE + REAKTIF SISTEM BACKTEST")
    print(f"TF: {TF} | Fee: maker=%0.02, taker=%0.04 | Swing N=10")
    print("A: System J (prediktif) | B: Saf Reaktif | C: Elliott + Reaktif")
    print("=" * 130)

    # Reaktif config (backtest_reactive.py'den en iyi: R4)
    reactive_config = {
        "sl_atr_mult": 1.0, "trail_trigger_atr": 1.0, "trail_callback_atr": 0.3,
        "max_bars": 40, "flat_exit_bars": 15, "flat_threshold_atr": 0.2,
    }

    predictive_config = {
        "trend_sl_atr": 2.0, "trend_trail_trigger": 2.5, "trend_trail_cb": 0.5,
        "range_sl_atr": 1.5, "max_bars": 40,
    }

    all_A = []  # System J prediktif
    all_B = []  # Saf reaktif
    all_C = []  # Elliott + reaktif
    coin_results = {}

    for sym in COINS:
        print(f"\n{'-'*80}")
        print(f"  {sym}")
        print(f"{'-'*80}")

        kl = fetch_klines(sym, TF, 1500)
        if not kl or len(kl) < 500:
            print("  YETERSIZ VERI")
            continue
        time.sleep(0.15)

        closes = np.array([float(k[4]) for k in kl])
        highs_arr = np.array([float(k[2]) for k in kl])
        lows_arr = np.array([float(k[3]) for k in kl])

        ema9 = ema_series(closes, 9)
        ema21 = ema_series(closes, 21)
        macd_h = compute_macd_hist(closes)
        rsi = compute_rsi(closes)
        atr = compute_atr(highs_arr, lows_arr, closes)
        bb_u, bb_m, bb_l = compute_bb(closes)
        er = compute_er(closes, 20)

        # ── Varyant A & B: standart sinyaller ──
        std_signals = generate_signals(closes, ema9, ema21, macd_h, rsi, atr)
        print(f"  Standart sinyaller: {len(std_signals)}")

        trades_A = []
        trades_B = []

        for idx, direction in std_signals:
            # A: Prediktif
            regime = classify_regime_sysj(er, idx)
            t = sim_trade_predictive(closes, highs_arr, lows_arr, atr, bb_u, bb_m, bb_l,
                                      idx, direction, closes[idx], regime, predictive_config)
            if t:
                t.symbol = sym
                t.variant = "A"
                trades_A.append(t)

            # B: Saf Reaktif
            t = sim_trade_reactive(closes, highs_arr, lows_arr, atr,
                                    idx, direction, closes[idx], reactive_config)
            if t:
                t.symbol = sym
                t.variant = "B"
                trades_B.append(t)

        # ── Varyant C: Elliott sinyalleri ──
        ew_signals = generate_elliott_signals(closes, highs_arr, lows_arr,
                                               ema9, ema21, macd_h, rsi, atr)
        print(f"  Elliott sinyaller: {len(ew_signals)}")

        trades_C = []
        for sig in ew_signals:
            idx, direction, pattern = sig
            t = sim_trade_reactive(closes, highs_arr, lows_arr, atr,
                                    idx, direction, closes[idx], reactive_config)
            if t:
                t.symbol = sym
                t.variant = "C"
                t.elliott_type = pattern.pattern_type
                trades_C.append(t)

        # Coin bazli sonuclar
        sA = summarize_trades(trades_A)
        sB = summarize_trades(trades_B)
        sC = summarize_trades(trades_C)

        print(f"\n  A (System J):         N={sA['total']:>3}, WR={sA['wr']:>5.1f}%, PnL={sA['total_pnl']:>+8.3f}%, PF={sA['pf']:.2f}")
        print(f"  B (Saf Reaktif):      N={sB['total']:>3}, WR={sB['wr']:>5.1f}%, PnL={sB['total_pnl']:>+8.3f}%, PF={sB['pf']:.2f}")
        print(f"  C (Elliott+Reaktif):  N={sC['total']:>3}, WR={sC['wr']:>5.1f}%, PnL={sC['total_pnl']:>+8.3f}%, PF={sC['pf']:.2f}")

        # Elliott pattern dagilimi
        if trades_C:
            impulse_count = sum(1 for t in trades_C if t.elliott_type == "IMPULSE_5")
            abc_count = sum(1 for t in trades_C if t.elliott_type == "CORRECTION_ABC")
            print(f"  Elliott breakdown: Impulse={impulse_count}, ABC={abc_count}")

        all_A.extend(trades_A)
        all_B.extend(trades_B)
        all_C.extend(trades_C)
        coin_results[sym] = {"A": sA, "B": sB, "C": sC,
                              "std_signals": len(std_signals),
                              "ew_signals": len(ew_signals)}

    # ════════════════ GENEL SONUCLAR ════════════════
    print(f"\n{'='*130}")
    print("GENEL SONUCLAR")
    print(f"{'='*130}")

    for label, trades in [("A: SYSTEM J (Prediktif)", all_A),
                           ("B: SAF REAKTIF", all_B),
                           ("C: ELLIOTT + REAKTIF", all_C)]:
        s = summarize_trades(trades)
        print(f"\n  {label}:")
        print_summary(label, s)

    # ════════════════ COIN BAZLI TABLO ════════════════
    print(f"\n{'='*130}")
    print("COIN BAZLI KARSILASTIRMA")
    print(f"{'='*130}")
    print(f"  {'Coin':12} | {'StdSig':>6} | {'EWSig':>5} | {'A PnL':>8} | {'A WR':>5} | {'A PF':>5} | "
          f"{'B PnL':>8} | {'B WR':>5} | {'B PF':>5} | {'C PnL':>8} | {'C WR':>5} | {'C PF':>5} | {'C-B':>6}")
    print(f"  {'-'*12}-+-{'-'*6}-+-{'-'*5}-+-{'-'*8}-+-{'-'*5}-+-{'-'*5}-+-"
          f"{'-'*8}-+-{'-'*5}-+-{'-'*5}-+-{'-'*8}-+-{'-'*5}-+-{'-'*5}-+-{'-'*6}")

    for sym, data in coin_results.items():
        a, b, c = data["A"], data["B"], data["C"]
        diff_cb = c["total_pnl"] - b["total_pnl"]
        print(f"  {sym:12} | {data['std_signals']:>6} | {data['ew_signals']:>5} | "
              f"{a['total_pnl']:>+7.3f}% | {a['wr']:>4.1f}% | {a['pf']:>5.2f} | "
              f"{b['total_pnl']:>+7.3f}% | {b['wr']:>4.1f}% | {b['pf']:>5.2f} | "
              f"{c['total_pnl']:>+7.3f}% | {c['wr']:>4.1f}% | {c['pf']:>5.2f} | "
              f"{diff_cb:>+5.2f}%")

    # Elliott pattern istatistikleri
    print(f"\n{'='*130}")
    print("ELLIOTT PATTERN ISTATISTIKLERI")
    print(f"{'='*130}")
    impulse_trades = [t for t in all_C if t.elliott_type == "IMPULSE_5"]
    abc_trades = [t for t in all_C if t.elliott_type == "CORRECTION_ABC"]

    for label, trades in [("IMPULSE_5", impulse_trades), ("CORRECTION_ABC", abc_trades)]:
        s = summarize_trades(trades)
        print(f"\n  {label}:")
        if s["total"] > 0:
            print_summary(label, s)
        else:
            print(f"    (trade yok)")

    # Sonuc
    print(f"\n{'='*130}")
    total_A = summarize_trades(all_A)
    total_B = summarize_trades(all_B)
    total_C = summarize_trades(all_C)
    print("KARAR:")
    if total_C["total"] == 0:
        print("  C varyanti hic trade uretmedi — Elliott sinyalleri yetersiz.")
    elif total_C["pf"] > total_B["pf"] and total_C["total_pnl"] > total_B["total_pnl"]:
        print(f"  C (Elliott+Reaktif) KAZANDI: PnL {total_C['total_pnl']:+.3f}% vs B {total_B['total_pnl']:+.3f}%")
        print("  -> System J'ye entegrasyon onerilir (config flag arkasinda)")
    elif total_B["total_pnl"] > total_A["total_pnl"]:
        print(f"  B (Saf Reaktif) en iyi: PnL {total_B['total_pnl']:+.3f}%")
        print(f"  C (Elliott) fark yaratmadi: PnL {total_C['total_pnl']:+.3f}%")
        print("  -> Saf reaktif yeterli, Elliott ek karmasiklik getiriyor")
    else:
        print(f"  Hicbir varyant net iyilestirme gostermedi.")
        print(f"  A: {total_A['total_pnl']:+.3f}% | B: {total_B['total_pnl']:+.3f}% | C: {total_C['total_pnl']:+.3f}%")
    print(f"{'='*130}")


if __name__ == "__main__":
    run_backtest()
