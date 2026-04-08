"""Reaktif vs Prediktif Rejim Sistemi — Trade Simulasyonu Backtest.

PREDIKTIF (mevcut System J):
  - Rejim tespit et (ER+Hurst) -> TREND/RANGING
  - TREND: market giris, trailing exit
  - RANGING: limit giris, BB middle TP

REAKTIF (yeni yaklasim):
  - Her zaman korumaci giris (tight SL)
  - Fiyat hareketi basladiktan SONRA karar ver:
    - Favorable move > tetik -> trailing aktive et (trend yakala)
    - Flat kaliyor -> kucuk kar/zarar ile cik
  - Rejim etiketi yok, sadece fiyat aksiyonu

Trade simulasyonu:
  - Gercek mum verisi uzerinde bar-by-bar ilerleme
  - SL, TP, trailing mantigi tick bazli
  - Fee hesabi (maker %0.02, taker %0.04)
  - Her trade icin PnL kaydi
"""
import os, time, hmac, hashlib, requests, numpy as np
from urllib.parse import urlencode
from dataclasses import dataclass, field
from dotenv import load_dotenv

load_dotenv()
API_KEY = os.getenv("BINANCE_API_KEY")
API_SECRET = os.getenv("BINANCE_API_SECRET")
session = requests.Session()
session.headers["X-MBX-APIKEY"] = API_KEY
BASE = "https://fapi.binance.com"

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
    """Yon skoru: +1 full LONG, -1 full SHORT, 0 notr."""
    v = 0.0
    if ema9[idx] > ema21[idx] * 1.0005: v += 1
    elif ema9[idx] < ema21[idx] * 0.9995: v -= 1
    if macd_h[idx] > 0: v += 1
    elif macd_h[idx] < 0: v -= 1
    if rsi[idx] > 55: v += 1
    elif rsi[idx] < 45: v -= 1
    return v / 3.0


# ════════════════ TRADE SIMULATION ════════════════

FEE_TAKER = 0.0004   # %0.04
FEE_MAKER = 0.0002   # %0.02

@dataclass
class Trade:
    symbol: str = ""
    direction: str = ""     # LONG / SHORT
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


def sim_trade_reactive(closes, highs, lows, atr, bb_upper, bb_mid, bb_lower,
                       entry_idx, direction, entry_price, config):
    """Reaktif trade simulasyonu.

    Giris: korumaci (tight SL)
    Sonra: fiyat hareketi tetikleyici -> trailing veya flat cikis
    """
    sl_atr_mult = config.get("sl_atr_mult", 1.5)
    trail_trigger_atr = config.get("trail_trigger_atr", 2.0)  # trailing baslatma esigi
    trail_callback_atr = config.get("trail_callback_atr", 0.5)  # trailing geri cekilme
    max_bars = config.get("max_bars", 40)
    flat_exit_bars = config.get("flat_exit_bars", 20)  # hareket yoksa cik
    flat_threshold_atr = config.get("flat_threshold_atr", 0.3)  # "flat" = ATR'nin %30'u icerisinde

    current_atr = atr[entry_idx]
    if current_atr <= 0:
        return None

    # SL hesapla
    if direction == "LONG":
        sl_price = entry_price - sl_atr_mult * current_atr
    else:
        sl_price = entry_price + sl_atr_mult * current_atr

    trailing_active = False
    trail_high = entry_price if direction == "LONG" else entry_price
    trail_low = entry_price if direction == "SHORT" else entry_price
    best_price = entry_price

    for bar in range(entry_idx + 1, min(entry_idx + max_bars + 1, len(closes))):
        high = highs[bar]
        low = lows[bar]
        close = closes[bar]
        bars_held = bar - entry_idx

        # SL kontrol
        if direction == "LONG":
            if low <= sl_price:
                exit_price = sl_price
                fee = entry_price * FEE_TAKER + exit_price * FEE_TAKER
                pnl = (exit_price - entry_price) / entry_price * 100 - (fee / entry_price * 100)
                return Trade(direction=direction, entry_price=entry_price, exit_price=exit_price,
                             entry_bar=entry_idx, exit_bar=bar, pnl_pct=pnl,
                             exit_reason="SL", bars_held=bars_held)
        else:
            if high >= sl_price:
                exit_price = sl_price
                fee = entry_price * FEE_TAKER + exit_price * FEE_TAKER
                pnl = (entry_price - exit_price) / entry_price * 100 - (fee / entry_price * 100)
                return Trade(direction=direction, entry_price=entry_price, exit_price=exit_price,
                             entry_bar=entry_idx, exit_bar=bar, pnl_pct=pnl,
                             exit_reason="SL", bars_held=bars_held)

        # Trailing trigger kontrol
        if not trailing_active:
            if direction == "LONG":
                favorable_move = (high - entry_price) / current_atr
                if favorable_move >= trail_trigger_atr:
                    trailing_active = True
                    best_price = high
            else:
                favorable_move = (entry_price - low) / current_atr
                if favorable_move >= trail_trigger_atr:
                    trailing_active = True
                    best_price = low

        # Trailing aktifse
        if trailing_active:
            if direction == "LONG":
                if high > best_price:
                    best_price = high
                trail_sl = best_price - trail_callback_atr * current_atr
                if low <= trail_sl:
                    exit_price = trail_sl
                    fee = entry_price * FEE_TAKER + exit_price * FEE_TAKER
                    pnl = (exit_price - entry_price) / entry_price * 100 - (fee / entry_price * 100)
                    return Trade(direction=direction, entry_price=entry_price, exit_price=exit_price,
                                 entry_bar=entry_idx, exit_bar=bar, pnl_pct=pnl,
                                 exit_reason="TRAIL", bars_held=bars_held)
            else:
                if low < best_price:
                    best_price = low
                trail_sl = best_price + trail_callback_atr * current_atr
                if high >= trail_sl:
                    exit_price = trail_sl
                    fee = entry_price * FEE_TAKER + exit_price * FEE_TAKER
                    pnl = (entry_price - exit_price) / entry_price * 100 - (fee / entry_price * 100)
                    return Trade(direction=direction, entry_price=entry_price, exit_price=exit_price,
                                 entry_bar=entry_idx, exit_bar=bar, pnl_pct=pnl,
                                 exit_reason="TRAIL", bars_held=bars_held)

        # Flat cikis: uzun sure hareket yoksa cik
        if not trailing_active and bars_held >= flat_exit_bars:
            if direction == "LONG":
                move = abs(close - entry_price) / current_atr
            else:
                move = abs(entry_price - close) / current_atr
            if move < flat_threshold_atr:
                exit_price = close
                fee = entry_price * FEE_MAKER + exit_price * FEE_MAKER
                pnl_raw = (exit_price - entry_price) / entry_price * 100 if direction == "LONG" \
                    else (entry_price - exit_price) / entry_price * 100
                pnl = pnl_raw - (fee / entry_price * 100)
                return Trade(direction=direction, entry_price=entry_price, exit_price=exit_price,
                             entry_bar=entry_idx, exit_bar=bar, pnl_pct=pnl,
                             exit_reason="FLAT", bars_held=bars_held)

    # Max bar cikis
    bar = min(entry_idx + max_bars, len(closes) - 1)
    exit_price = closes[bar]
    fee = entry_price * FEE_TAKER + exit_price * FEE_TAKER
    pnl_raw = (exit_price - entry_price) / entry_price * 100 if direction == "LONG" \
        else (entry_price - exit_price) / entry_price * 100
    pnl = pnl_raw - (fee / entry_price * 100)
    reason = "TRAIL_MAX" if trailing_active else "TIME"
    return Trade(direction=direction, entry_price=entry_price, exit_price=exit_price,
                 entry_bar=entry_idx, exit_bar=bar, pnl_pct=pnl,
                 exit_reason=reason, bars_held=bar - entry_idx)


def sim_trade_predictive(closes, highs, lows, atr, bb_upper, bb_mid, bb_lower,
                          entry_idx, direction, entry_price, regime, config):
    """Prediktif trade simulasyonu (mevcut System J mantigi).

    TREND: market giris, trailing exit, genis SL
    RANGING: BB middle TP, dar SL
    """
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
        trail_trigger = 999  # ranging'de trailing yok
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
        close = closes[bar]
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

    # Max bar
    bar = min(entry_idx + max_bars, len(closes) - 1)
    ep = closes[bar]
    fee = entry_price * FEE_TAKER + ep * FEE_TAKER
    pnl_raw = (ep - entry_price) / entry_price * 100 if direction == "LONG" \
        else (entry_price - ep) / entry_price * 100
    return Trade(direction=direction, entry_price=entry_price, exit_price=ep,
                 entry_bar=entry_idx, exit_bar=bar, pnl_pct=pnl_raw - fee / entry_price * 100,
                 exit_reason="TIME", bars_held=bar - entry_idx, regime_at_entry=regime)


# ════════════════ SIGNAL GENERATOR ════════════════

def generate_signals(closes, ema9, ema21, macd_h, rsi, atr, min_signal=0.66):
    """Yon sinyali uret. Her sinyal (idx, direction) donduyor."""
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
            cooldown = 10  # 10 bar cooldown (overlap onleme)
    return signals


# ════════════════ REGIME CLASSIFIER (System J) ════════════════

def classify_regime_sysj(er_arr, idx):
    """Basit System J rejim: ER median > 0.25 = TREND."""
    start = max(0, idx - 9)
    er_median = float(np.median(er_arr[start:idx + 1]))
    if er_median > 0.25:
        return "TRENDING"
    if er_median < 0.08:
        return "RANGING"
    return "TRENDING" if er_median > 0.165 else "RANGING"


# ════════════════ ANA BACKTEST ════════════════

COINS = ["BTCUSDT", "ETHUSDT", "XRPUSDT", "AVAXUSDT", "DOGEUSDT",
         "SOLUSDT", "ADAUSDT", "BNBUSDT"]
TF = "15m"


def run_backtest():
    print("=" * 130)
    print("REAKTIF vs PREDIKTIF REJIM SISTEMI — Trade Simulasyonu")
    print(f"TF: {TF} | Fee: maker=%0.02, taker=%0.04")
    print("=" * 130)

    # Reaktif parametreler grid
    reactive_configs = [
        {"name": "R1: Tight SL + Erken Trail",
         "sl_atr_mult": 1.0, "trail_trigger_atr": 1.5, "trail_callback_atr": 0.4,
         "max_bars": 40, "flat_exit_bars": 15, "flat_threshold_atr": 0.3},

        {"name": "R2: Normal SL + Normal Trail",
         "sl_atr_mult": 1.5, "trail_trigger_atr": 2.0, "trail_callback_atr": 0.5,
         "max_bars": 40, "flat_exit_bars": 20, "flat_threshold_atr": 0.3},

        {"name": "R3: Genis SL + Gec Trail",
         "sl_atr_mult": 2.0, "trail_trigger_atr": 3.0, "trail_callback_atr": 0.7,
         "max_bars": 60, "flat_exit_bars": 25, "flat_threshold_atr": 0.4},

        {"name": "R4: Tight SL + Cok Erken Trail",
         "sl_atr_mult": 1.0, "trail_trigger_atr": 1.0, "trail_callback_atr": 0.3,
         "max_bars": 40, "flat_exit_bars": 15, "flat_threshold_atr": 0.2},

        {"name": "R5: Normal SL + Erken Trail",
         "sl_atr_mult": 1.5, "trail_trigger_atr": 1.5, "trail_callback_atr": 0.4,
         "max_bars": 40, "flat_exit_bars": 20, "flat_threshold_atr": 0.3},

        {"name": "R6: Tight SL + Flat Hizli Cikis",
         "sl_atr_mult": 1.0, "trail_trigger_atr": 1.5, "trail_callback_atr": 0.4,
         "max_bars": 30, "flat_exit_bars": 10, "flat_threshold_atr": 0.2},
    ]

    # Prediktif parametreler
    predictive_config = {
        "trend_sl_atr": 2.0, "trend_trail_trigger": 2.5, "trend_trail_cb": 0.5,
        "range_sl_atr": 1.5, "max_bars": 40,
    }

    all_results = {}

    for sym in COINS:
        print(f"\n{'='*80}")
        print(f"  {sym}")
        print(f"{'='*80}")

        kl = fetch_klines(sym, TF, 1500)
        if not kl or len(kl) < 500:
            print("  YETERSIZ VERI")
            continue
        time.sleep(0.15)

        closes = np.array([float(k[4]) for k in kl])
        highs = np.array([float(k[2]) for k in kl])
        lows = np.array([float(k[3]) for k in kl])

        ema9 = ema_series(closes, 9)
        ema21 = ema_series(closes, 21)
        macd_h = compute_macd_hist(closes)
        rsi = compute_rsi(closes)
        atr = compute_atr(highs, lows, closes)
        bb_u, bb_m, bb_l = compute_bb(closes)
        er = compute_er(closes, 20)

        # Sinyaller
        signals = generate_signals(closes, ema9, ema21, macd_h, rsi, atr)
        print(f"  {len(signals)} sinyal uretildi")

        if len(signals) == 0:
            continue

        # Prediktif trade'ler
        pred_trades = []
        for idx, direction in signals:
            regime = classify_regime_sysj(er, idx)
            t = sim_trade_predictive(closes, highs, lows, atr, bb_u, bb_m, bb_l,
                                      idx, direction, closes[idx], regime, predictive_config)
            if t:
                t.symbol = sym
                t.regime_at_entry = regime
                pred_trades.append(t)

        # Reaktif trade'ler (her config icin)
        reactive_trades = {}
        for rc in reactive_configs:
            trades = []
            for idx, direction in signals:
                t = sim_trade_reactive(closes, highs, lows, atr, bb_u, bb_m, bb_l,
                                        idx, direction, closes[idx], rc)
                if t:
                    t.symbol = sym
                    trades.append(t)
            reactive_trades[rc["name"]] = trades

        # Sonuclari kaydet
        all_results[sym] = {"pred": pred_trades, "reactive": reactive_trades, "signals": len(signals)}

    # ════════════════ SONUCLAR ════════════════
    print(f"\n{'='*130}")
    print("GENEL SONUCLAR")
    print(f"{'='*130}")

    def summarize_trades(trades, label=""):
        if not trades:
            return {"total": 0, "win": 0, "loss": 0, "wr": 0, "total_pnl": 0,
                    "avg_pnl": 0, "avg_win": 0, "avg_loss": 0, "pf": 0, "exits": {}}
        wins = [t for t in trades if t.pnl_pct > 0]
        losses = [t for t in trades if t.pnl_pct <= 0]
        total_pnl = sum(t.pnl_pct for t in trades)
        avg_pnl = total_pnl / len(trades)
        avg_win = np.mean([t.pnl_pct for t in wins]) if wins else 0
        avg_loss = np.mean([t.pnl_pct for t in losses]) if losses else 0
        gross_win = sum(t.pnl_pct for t in wins) if wins else 0
        gross_loss = abs(sum(t.pnl_pct for t in losses)) if losses else 0.001
        pf = gross_win / gross_loss if gross_loss > 0 else 0

        exits = {}
        for t in trades:
            exits[t.exit_reason] = exits.get(t.exit_reason, 0) + 1

        avg_bars = np.mean([t.bars_held for t in trades])

        return {"total": len(trades), "win": len(wins), "loss": len(losses),
                "wr": len(wins) / len(trades) * 100, "total_pnl": total_pnl,
                "avg_pnl": avg_pnl, "avg_win": avg_win, "avg_loss": avg_loss,
                "pf": pf, "exits": exits, "avg_bars": avg_bars}

    # Prediktif ozet
    all_pred = []
    for sym, data in all_results.items():
        all_pred.extend(data["pred"])

    pred_s = summarize_trades(all_pred)
    print(f"\n  PREDIKTIF (System J mantigi):")
    print(f"    Trades: {pred_s['total']} | Win: {pred_s['win']} Loss: {pred_s['loss']} | WR: {pred_s['wr']:.1f}%")
    print(f"    Toplam PnL: {pred_s['total_pnl']:+.3f}% | Avg: {pred_s['avg_pnl']:+.3f}%")
    print(f"    Avg Win: {pred_s['avg_win']:+.3f}% | Avg Loss: {pred_s['avg_loss']:+.3f}% | PF: {pred_s['pf']:.2f}")
    print(f"    Avg Bars: {pred_s.get('avg_bars', 0):.1f} | Exits: {pred_s['exits']}")

    # Prediktif - rejim bazli
    trend_trades = [t for t in all_pred if t.regime_at_entry == "TRENDING"]
    range_trades = [t for t in all_pred if t.regime_at_entry == "RANGING"]
    if trend_trades:
        ts = summarize_trades(trend_trades)
        print(f"    TREND girisler: {ts['total']} trades, WR={ts['wr']:.1f}%, PnL={ts['total_pnl']:+.3f}%, PF={ts['pf']:.2f}")
    if range_trades:
        rs = summarize_trades(range_trades)
        print(f"    RANGE girisler: {rs['total']} trades, WR={rs['wr']:.1f}%, PnL={rs['total_pnl']:+.3f}%, PF={rs['pf']:.2f}")

    # Reaktif ozetler
    print(f"\n  REAKTIF SONUCLAR:")
    print(f"  {'Konfig':40} | {'N':>4} | {'WR':>5} | {'PnL':>8} | {'Avg':>7} | {'AvgW':>7} | {'AvgL':>7} | {'PF':>5} | {'AvgBar':>6} | Cikislar")
    print(f"  {'-'*40}-+-{'-'*4}-+-{'-'*5}-+-{'-'*8}-+-{'-'*7}-+-{'-'*7}-+-{'-'*7}-+-{'-'*5}-+-{'-'*6}-+--------")

    for rc in reactive_configs:
        all_reactive = []
        for sym, data in all_results.items():
            all_reactive.extend(data["reactive"].get(rc["name"], []))
        s = summarize_trades(all_reactive)
        exits_str = " ".join(f"{k}={v}" for k, v in sorted(s["exits"].items()))
        print(f"  {rc['name']:40} | {s['total']:>4} | {s['wr']:>4.1f}% | {s['total_pnl']:>+7.3f}% | {s['avg_pnl']:>+6.3f}% | "
              f"{s['avg_win']:>+6.3f}% | {s['avg_loss']:>+6.3f}% | {s['pf']:>5.2f} | {s.get('avg_bars', 0):>5.1f} | {exits_str}")

    # Coin bazli karsilastirma
    print(f"\n{'='*130}")
    print("COIN BAZLI KARSILASTIRMA (Prediktif vs En Iyi Reaktif)")
    print(f"{'='*130}")
    print(f"  {'Coin':12} | {'Sinyal':>6} | {'Pred PnL':>9} | {'Pred WR':>7} | {'Pred PF':>7} | {'Reak PnL':>9} | {'Reak WR':>7} | {'Reak PF':>7} | {'Fark':>7}")
    print(f"  {'-'*12}-+-{'-'*6}-+-{'-'*9}-+-{'-'*7}-+-{'-'*7}-+-{'-'*9}-+-{'-'*7}-+-{'-'*7}-+-{'-'*7}")

    # En iyi reaktif config'i bul
    best_reactive_name = None
    best_reactive_pnl = -999
    for rc in reactive_configs:
        all_r = []
        for sym, data in all_results.items():
            all_r.extend(data["reactive"].get(rc["name"], []))
        total_pnl = sum(t.pnl_pct for t in all_r) if all_r else 0
        if total_pnl > best_reactive_pnl:
            best_reactive_pnl = total_pnl
            best_reactive_name = rc["name"]

    for sym, data in all_results.items():
        ps = summarize_trades(data["pred"])
        rs = summarize_trades(data["reactive"].get(best_reactive_name, []))
        diff = rs["total_pnl"] - ps["total_pnl"]
        print(f"  {sym:12} | {data['signals']:>6} | {ps['total_pnl']:>+8.3f}% | {ps['wr']:>6.1f}% | {ps['pf']:>6.2f} | "
              f"{rs['total_pnl']:>+8.3f}% | {rs['wr']:>6.1f}% | {rs['pf']:>6.2f} | {diff:>+6.3f}%")

    print(f"\n  En iyi reaktif: {best_reactive_name}")
    print()


if __name__ == "__main__":
    run_backtest()
