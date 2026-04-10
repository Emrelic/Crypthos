"""System N Sinyal Bazli Backtest — Dogru Simulasyon.

System N calismasi:
  - AlphaTrend BUY crossover → LONG gir
  - AlphaTrend SELL crossover → LONG kapat + SHORT gir (reverse mode)
  - SL sadece guvenlik (server-side, emergency)
  - Trailing YOK, TP YOK, time limit YOK
  - Tamamen sinyal bazli: bir sonraki ters sinyal gelene kadar tut

8 filtre paketi, 20 coin, 1 ay.

Kullanim:
    python backtest_comparison_v3.py
    python backtest_comparison_v3.py --fast
    python backtest_comparison_v3.py --symbols BTCUSDT ETHUSDT
"""

import sys
import os
import json
import time
import argparse
from pathlib import Path
from dataclasses import dataclass, field
from datetime import datetime

import numpy as np
import pandas as pd

# Windows encoding fix
import io
if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

# Project imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from market.binance_rest import BinanceRestClient
from scanner.system_n_scanner import (
    compute_alpha_trend, _compute_adx, _compute_rsi,
    _sma, _compute_ema,
)
from scanner.system_b_scanner import (
    detect_zigzag_swings, compute_rolling_er, compute_hurst_exponent,
)
from loguru import logger

# ═══════════════════════════════════════════════════════════════════
#  Config
# ═══════════════════════════════════════════════════════════════════

TF = "5m"
TARGET_BARS = 8640        # ~30 gun
WARMUP_BARS = 100
FEE_PER_TRADE = 0.12     # round-trip %

# SL sadece guvenlik — G-based
SL_G_MULT = 1.5
SL_FEE = 0.12            # %
SL_ATR_MULT = 2.0        # G yoksa ATR fallback

# AlphaTrend varsayilan
DEFAULT_COEFF = 3.6
DEFAULT_PERIOD = 27
ADX_LENGTH = 14
ADX_THRESHOLD = 18.0

ZIGZAG_N = 5

DEFAULT_SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT",
    "DOGEUSDT", "ADAUSDT", "AVAXUSDT", "DOTUSDT", "MATICUSDT",
    "LINKUSDT", "NEARUSDT", "LTCUSDT", "BCHUSDT", "APTUSDT",
    "FILUSDT", "ARBUSDT", "OPUSDT", "SUIUSDT", "SEIUSDT",
]


# ═══════════════════════════════════════════════════════════════════
#  Data Classes
# ═══════════════════════════════════════════════════════════════════

@dataclass
class TradeResult:
    entry_idx: int
    exit_idx: int
    direction: str          # "LONG" or "SHORT"
    entry_price: float
    exit_price: float
    pnl_pct: float
    hold_bars: int
    exit_reason: str        # "SIGNAL" veya "STOP_LOSS"
    symbol: str = ""
    # Entry anindaki degerler
    macd_hist: float = 0.0
    rsi_val: float = 50.0
    er_val: float = 0.5
    adx_val: float = 20.0
    regime: str = ""


@dataclass
class FilterConfig:
    name: str
    macd_align: bool = False
    rsi_align: bool = False
    rsi_long_min: float = 40.0
    rsi_short_max: float = 60.0
    er_filter: bool = False
    er_min: float = 0.2
    ranging_reject: bool = False


FILTER_PACKAGES = [
    FilterConfig("ORIJINAL (filtresiz)"),
    FilterConfig("ASAMA 1: ER+RANGING",
                 er_filter=True, er_min=0.2, ranging_reject=True),
    FilterConfig("ASAMA 2: MACD+ER+RNG",
                 macd_align=True, er_filter=True, er_min=0.2, ranging_reject=True),
    FilterConfig("PAKET A: MACD+RSI+ER+RNG",
                 macd_align=True, rsi_align=True, er_filter=True, er_min=0.2, ranging_reject=True),
    FilterConfig("PAKET B: Sadece MACD",
                 macd_align=True),
    FilterConfig("PAKET C: MACD+RSI",
                 macd_align=True, rsi_align=True),
    FilterConfig("PAKET D: Siki ER>0.3+MACD+RNG",
                 macd_align=True, er_filter=True, er_min=0.3, ranging_reject=True),
    FilterConfig("PAKET E: Sadece ER>0.2",
                 er_filter=True, er_min=0.2),
]


# ═══════════════════════════════════════════════════════════════════
#  Data Fetching
# ═══════════════════════════════════════════════════════════════════

def fetch_klines_paginated(client, symbol, tf="5m", target_bars=TARGET_BARS):
    try:
        df = client.get_klines(symbol, tf, min(target_bars, 1500))
        if df is None or len(df) < WARMUP_BARS:
            return None
        all_dfs = [df]
        fetched = len(df)
        while fetched < target_bars:
            earliest_ts = int(df["timestamp"].iloc[0].timestamp() * 1000)
            try:
                raw = client._get("/fapi/v1/klines", {
                    "symbol": symbol, "interval": tf,
                    "limit": min(1500, target_bars - fetched),
                    "endTime": earliest_ts - 1,
                })
            except Exception:
                break
            if not raw:
                break
            df2 = pd.DataFrame(raw, columns=[
                "timestamp", "open", "high", "low", "close", "volume",
                "close_time", "quote_volume", "trades", "taker_buy_volume",
                "taker_buy_quote_volume", "ignore",
            ])
            for col in ["open", "high", "low", "close", "volume"]:
                df2[col] = df2[col].astype(float)
            df2["timestamp"] = pd.to_datetime(df2["timestamp"], unit="ms")
            if len(df2) == 0:
                break
            all_dfs.insert(0, df2)
            fetched += len(df2)
            df = df2
            time.sleep(0.1)
        combined = pd.concat(all_dfs, ignore_index=True)
        combined = combined.drop_duplicates(subset=["timestamp"]).sort_values("timestamp").reset_index(drop=True)
        return combined
    except Exception as e:
        logger.warning(f"[BT] {symbol}: fetch failed: {e}")
        return None


# ═══════════════════════════════════════════════════════════════════
#  Rejim (rolling — trade aninda)
# ═══════════════════════════════════════════════════════════════════

def detect_regime_at(closes, highs, lows, idx, window=300):
    start = max(0, idx - window)
    if idx - start < 100:
        return "UNKNOWN"
    c, h, l = closes[start:idx], highs[start:idx], lows[start:idx]
    try:
        swings = detect_zigzag_swings(h, l, n=ZIGZAG_N)
        if len(swings) < 4:
            return "UNKNOWN"
        er = compute_rolling_er(c, window=20, median_count=10)
        hurst = compute_hurst_exponent(c)
        if er > 0.25: return "TRENDING"
        if er < 0.08: return "RANGING"
        if hurst > 0.55: return "TRENDING"
        if hurst < 0.45: return "RANGING"
        return "GRAY"
    except Exception:
        return "UNKNOWN"


# ═══════════════════════════════════════════════════════════════════
#  Backtest Engine — Sinyal Bazli (dogru simulasyon)
# ═══════════════════════════════════════════════════════════════════

def run_signal_backtest(
    closes, highs, lows, volumes,
    coeff, period, filter_cfg, symbol="", g_pct=0.0,
):
    """AlphaTrend sinyal bazli backtest.

    Calisma mantigi (canli sistem ile ayni):
      - BUY crossover + filtreler → LONG gir (varsa SHORT kapat)
      - SELL crossover + filtreler → SHORT gir (varsa LONG kapat)
      - raw sinyal (filtresiz): mevcut pozisyonu kapatir/reverse yapar
        (ek filtreler sadece YENi girisi engeller, cikisi engellemez)
      - SL: sadece guvenlik — G*1.5 veya 2xATR
    """
    n = len(closes)
    if n < max(period * 3, ADX_LENGTH * 3, WARMUP_BARS):
        return []

    # ── Indikatorler ──
    alpha_trend, atr_arr = compute_alpha_trend(
        highs, lows, closes, volumes,
        coeff=coeff, period=period, use_mfi=True,
    )
    adx_arr, _, _ = _compute_adx(highs, lows, closes, ADX_LENGTH)
    adx_sma = _sma(adx_arr, ADX_LENGTH)
    rsi_arr = _compute_rsi(closes, 14)

    # MACD
    ema_f = _compute_ema(closes, 12)
    ema_s = _compute_ema(closes, 26)
    macd_line = ema_f - ema_s
    macd_line_clean = macd_line.copy()
    macd_line_clean[np.isnan(macd_line_clean)] = 0.0
    macd_sig = _compute_ema(macd_line_clean, 9)
    macd_hist_arr = macd_line_clean - macd_sig
    macd_hist_arr[np.isnan(macd_hist_arr)] = 0.0

    atr_clean = atr_arr.copy()
    atr_clean[np.isnan(atr_clean)] = 0.0

    er_period = 10
    trades = []

    # Pozisyon state
    position = None     # None / "LONG" / "SHORT"
    entry_idx = 0
    entry_price = 0.0
    sl_price = 0.0
    e_macd = e_rsi = e_er = e_adx = 0.0
    e_regime = ""

    start = max(period * 3, WARMUP_BARS, 4, 36)

    def _close(exit_idx, exit_price, reason):
        nonlocal position
        if position == "LONG":
            pnl = (exit_price - entry_price) / entry_price * 100 - FEE_PER_TRADE
        else:
            pnl = (entry_price - exit_price) / entry_price * 100 - FEE_PER_TRADE
        trades.append(TradeResult(
            entry_idx=entry_idx, exit_idx=exit_idx,
            direction=position, entry_price=entry_price,
            exit_price=exit_price, pnl_pct=pnl,
            hold_bars=exit_idx - entry_idx,
            exit_reason=reason, symbol=symbol,
            macd_hist=e_macd, rsi_val=e_rsi,
            er_val=e_er, adx_val=e_adx, regime=e_regime,
        ))
        position = None

    def _open(idx, direction, atr):
        nonlocal position, entry_idx, entry_price, sl_price
        nonlocal e_macd, e_rsi, e_er, e_adx, e_regime
        position = direction
        entry_idx = idx
        entry_price = closes[idx]

        # SL hesapla (guvenlik)
        if g_pct > 0:
            sl_dist = g_pct * SL_G_MULT + SL_FEE
        elif atr > 0:
            sl_dist = (atr * SL_ATR_MULT / entry_price) * 100
        else:
            sl_dist = 5.0
        if direction == "LONG":
            sl_price = entry_price * (1 - sl_dist / 100)
        else:
            sl_price = entry_price * (1 + sl_dist / 100)

        e_macd = macd_hist_arr[idx]
        e_rsi = rsi_arr[idx] if not np.isnan(rsi_arr[idx]) else 50.0
        if idx >= er_period + 1:
            d = abs(closes[idx] - closes[idx - er_period])
            v = np.sum(np.abs(np.diff(closes[idx - er_period:idx + 1])))
            e_er = d / v if v > 1e-12 else 0.5
        else:
            e_er = 0.5
        e_adx = adx_arr[idx] if not np.isnan(adx_arr[idx]) else 20.0
        e_regime = detect_regime_at(closes, highs, lows, idx)

    for i in range(start, n):
        # ═══ 1. ACIK POZISYON VARSA: SL KONTROL ═══
        if position is not None:
            if position == "LONG" and lows[i] <= sl_price:
                _close(i, sl_price, "STOP_LOSS")
                # SL sonrasi: yeni sinyal beklenir (pozisyon None)
            elif position == "SHORT" and highs[i] >= sl_price:
                _close(i, sl_price, "STOP_LOSS")

        # ═══ 2. SINYAL KONTROLU ═══
        at_now = alpha_trend[i]
        at_1 = alpha_trend[i - 1]
        at_2 = alpha_trend[i - 2]
        at_3 = alpha_trend[i - 3]

        if any(np.isnan(v) for v in [at_now, at_1, at_2, at_3]):
            continue

        # ADX base filtre
        adx_val = adx_arr[i] if not np.isnan(adx_arr[i]) else 0.0
        adx_static_ok = adx_val > ADX_THRESHOLD
        adx_dyn_val = adx_sma[i] if not np.isnan(adx_sma[i]) else 0.0
        adx_dynamic_ok = adx_val > adx_dyn_val
        base_filter = adx_static_ok and adx_dynamic_ok

        # Crossover
        buy_cross = (at_now > at_2) and (at_1 <= at_3)
        sell_cross = (at_now < at_2) and (at_1 >= at_3)

        if not (buy_cross or sell_cross):
            continue

        # ── RAW sinyal (ADX filtreli, ek filtresiz) ──
        # Cikis/reverse icin kullanilir
        raw_buy = buy_cross and base_filter
        raw_sell = sell_cross and base_filter

        # Acik pozisyon + ters raw sinyal → kapat (ek filtre engellemez)
        if position == "LONG" and raw_sell:
            _close(i, closes[i], "SIGNAL")
        elif position == "SHORT" and raw_buy:
            _close(i, closes[i], "SIGNAL")

        if not base_filter:
            continue

        # ── Ek filtreler (sadece YENI GIRIS icin) ──
        extra_ok = True

        macd_h = macd_hist_arr[i]
        if filter_cfg.macd_align:
            if buy_cross:
                extra_ok = extra_ok and (macd_h > 0)
            elif sell_cross:
                extra_ok = extra_ok and (macd_h < 0)

        rsi_val = rsi_arr[i] if not np.isnan(rsi_arr[i]) else 50.0
        if filter_cfg.rsi_align:
            if buy_cross:
                extra_ok = extra_ok and (rsi_val > filter_cfg.rsi_long_min)
            elif sell_cross:
                extra_ok = extra_ok and (rsi_val < filter_cfg.rsi_short_max)

        er_val = 0.5
        if i >= er_period + 1:
            d = abs(closes[i] - closes[i - er_period])
            v = np.sum(np.abs(np.diff(closes[i - er_period:i + 1])))
            if v > 1e-12:
                er_val = d / v
        if filter_cfg.er_filter:
            extra_ok = extra_ok and (er_val > filter_cfg.er_min)

        if filter_cfg.ranging_reject:
            regime = detect_regime_at(closes, highs, lows, i)
            extra_ok = extra_ok and (regime != "RANGING")

        if not extra_ok:
            continue

        # ── Yeni pozisyon ac (mevcut bossa) ──
        if position is None:
            if buy_cross:
                _open(i, "LONG", atr_clean[i])
            elif sell_cross:
                _open(i, "SHORT", atr_clean[i])

    return trades


# ═══════════════════════════════════════════════════════════════════
#  Statistics
# ═══════════════════════════════════════════════════════════════════

@dataclass
class Stats:
    name: str
    total: int = 0
    wins: int = 0
    losses: int = 0
    wr: float = 0.0
    net_pnl: float = 0.0
    pf: float = 0.0
    max_dd: float = 0.0
    avg_pnl: float = 0.0
    avg_hold: float = 0.0
    # Cikis tipi
    sl_count: int = 0
    sl_pnl: float = 0.0
    sig_count: int = 0
    sig_pnl: float = 0.0
    # Yon
    long_n: int = 0; long_w: int = 0; long_pnl: float = 0.0
    short_n: int = 0; short_w: int = 0; short_pnl: float = 0.0
    # Coin
    coins: dict = field(default_factory=dict)
    all_trades: list = field(default_factory=list, repr=False)


def calc_stats(trades, name):
    s = Stats(name=name, all_trades=trades)
    if not trades:
        return s
    s.total = len(trades)
    pnls = [t.pnl_pct for t in trades]
    w = [p for p in pnls if p > 0]
    l = [p for p in pnls if p <= 0]
    s.wins = len(w)
    s.losses = len(l)
    s.wr = s.wins / s.total * 100
    s.net_pnl = sum(pnls)
    gp = sum(w) if w else 0
    gl = abs(sum(l)) if l else 0.001
    s.pf = gp / gl
    s.max_dd = float(np.max(np.maximum.accumulate(np.cumsum(pnls)) - np.cumsum(pnls)))
    s.avg_pnl = float(np.mean(pnls))
    s.avg_hold = float(np.mean([t.hold_bars for t in trades]))

    for t in trades:
        if t.exit_reason == "STOP_LOSS":
            s.sl_count += 1; s.sl_pnl += t.pnl_pct
        else:
            s.sig_count += 1; s.sig_pnl += t.pnl_pct
        if t.direction == "LONG":
            s.long_n += 1
            if t.pnl_pct > 0: s.long_w += 1
            s.long_pnl += t.pnl_pct
        else:
            s.short_n += 1
            if t.pnl_pct > 0: s.short_w += 1
            s.short_pnl += t.pnl_pct
        sym = t.symbol
        if sym not in s.coins:
            s.coins[sym] = {"n": 0, "w": 0, "pnl": 0.0, "sl": 0}
        s.coins[sym]["n"] += 1
        if t.pnl_pct > 0: s.coins[sym]["w"] += 1
        s.coins[sym]["pnl"] += t.pnl_pct
        if t.exit_reason == "STOP_LOSS": s.coins[sym]["sl"] += 1

    return s


# ═══════════════════════════════════════════════════════════════════
#  Optimize Cache
# ═══════════════════════════════════════════════════════════════════

def load_opt_cache():
    path = "data/system_n_optimize.json"
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        cache = {}
        for sym, info in data.get("results", {}).items():
            opt_tf = info.get("optimal_tf", "5m")
            params = info.get("params", {}).get(opt_tf, {})
            g_data = info.get("g_analysis", {}).get(opt_tf, {})
            c = params.get("coeff", 0)
            p = params.get("period", 0)
            G = g_data.get("G", info.get("G", 0))
            if c > 0 and p > 0:
                cache[sym] = {"coeff": c, "period": p, "G": G}
        return cache
    except Exception:
        return {}


# ═══════════════════════════════════════════════════════════════════
#  Main
# ═══════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="System N Sinyal Bazli Backtest v3")
    parser.add_argument("--symbols", nargs="+", default=None)
    parser.add_argument("--top", type=int, default=20)
    parser.add_argument("--fast", action="store_true")
    args = parser.parse_args()

    target_bars = 4320 if args.fast else TARGET_BARS

    client = BinanceRestClient()
    if args.symbols:
        symbols = args.symbols
    else:
        try:
            t24 = client._get("/fapi/v1/ticker/24hr")
            st = sorted(t24, key=lambda x: float(x.get("quoteVolume", 0)), reverse=True)
            symbols = [t["symbol"] for t in st if t["symbol"].endswith("USDT")][:args.top]
        except Exception:
            symbols = DEFAULT_SYMBOLS[:args.top]

    opt_cache = load_opt_cache()

    print("\n" + "=" * 110)
    print("  SYSTEM N SINYAL BAZLI BACKTEST v3")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"  {len(symbols)} coin | {TF} | ~{target_bars * 5 / 1440:.0f} gun")
    print(f"  Cikis: sinyal reverse + guvenlik SL (G*1.5 veya 2xATR)")
    print(f"  Trailing YOK | TP YOK | Time limit YOK")
    print("=" * 110)

    pkg_trades = {fc.name: [] for fc in FILTER_PACKAGES}

    t0 = time.time()
    for si, sym in enumerate(symbols):
        print(f"\n  [{si+1}/{len(symbols)}] {sym}", end="", flush=True)

        df = fetch_klines_paginated(client, sym, TF, target_bars)
        if df is None or len(df) < WARMUP_BARS:
            print(" — SKIP")
            continue

        c = df["close"].values.astype(float)
        h = df["high"].values.astype(float)
        l = df["low"].values.astype(float)
        v = df["volume"].values.astype(float)

        days = len(df) * 5 / 1440
        print(f" — {len(df)} mum ({days:.0f}g)", end="", flush=True)

        params = opt_cache.get(sym, {})
        coeff = params.get("coeff", DEFAULT_COEFF)
        period = params.get("period", DEFAULT_PERIOD)
        g_pct = params.get("G", 0)

        for fc in FILTER_PACKAGES:
            trades = run_signal_backtest(c, h, l, v, coeff, period, fc, sym, g_pct)
            pkg_trades[fc.name].extend(trades)

        ot = len([t for t in pkg_trades[FILTER_PACKAGES[0].name] if t.symbol == sym])
        osl = len([t for t in pkg_trades[FILTER_PACKAGES[0].name]
                    if t.symbol == sym and t.exit_reason == "STOP_LOSS"])
        print(f" | {ot} trade ({osl} SL) | G={g_pct:.3f}%", flush=True)
        time.sleep(0.15)

    elapsed = time.time() - t0

    # ═══════════════════════════════════════════════════════════════
    all_s = [calc_stats(pkg_trades[fc.name], fc.name) for fc in FILTER_PACKAGES]
    orig = all_s[0]

    # ── 1. ANA TABLO ──
    print(f"\n\n{'='*120}")
    print(f"  ANA KARSILASTIRMA — Sinyal Bazli (SL sadece guvenlik)")
    print(f"{'='*120}")

    print(f"\n  {'Paket':<32} {'Tr':>5} {'Win':>4} {'Los':>4} {'WR%':>6} "
          f"{'NetPnL%':>9} {'PF':>5} {'DD%':>7} {'Avg':>7} "
          f"{'SL':>4} {'SL.PnL':>8} {'Sig':>4} {'Sig.PnL':>9}")
    print(f"  {'-'*110}")
    for s in all_s:
        m = " <--" if "ASAMA 2" in s.name else ""
        print(f"  {s.name:<32} {s.total:>5} {s.wins:>4} {s.losses:>4} {s.wr:>5.1f}% "
              f"{s.net_pnl:>+8.2f}% {s.pf:>4.2f} {s.max_dd:>6.2f}% {s.avg_pnl:>+6.3f}% "
              f"{s.sl_count:>4} {s.sl_pnl:>+7.2f}% {s.sig_count:>4} {s.sig_pnl:>+8.2f}%{m}")

    # ── 2. DELTA ──
    print(f"\n  {'Paket':<32} {'dTr':>5} {'dWR':>6} {'dPnL%':>9} {'dPF':>6} "
          f"{'dSL':>4} {'SL Eng%':>8}")
    print(f"  {'-'*75}")
    for s in all_s[1:]:
        dt = s.total - orig.total
        dwr = s.wr - orig.wr
        dp = s.net_pnl - orig.net_pnl
        dpf = s.pf - orig.pf
        dsl = s.sl_count - orig.sl_count
        slb = (1 - s.sl_count / orig.sl_count) * 100 if orig.sl_count > 0 else 0
        print(f"  {s.name:<32} {dt:>+5} {dwr:>+5.1f}% {dp:>+8.2f}% {dpf:>+5.2f} "
              f"{dsl:>+4} {slb:>+7.1f}%")

    # ── 3. SL ENGELLEME ──
    print(f"\n\n{'='*110}")
    print(f"  SL ENGELLEME DETAYI — ASAMA 2 (MACD+ER+RNG) vs ORIJINAL")
    print(f"{'='*110}")

    a2 = all_s[2]  # ASAMA 2
    orig_keys = {(t.symbol, t.entry_idx): t for t in orig.all_trades}
    a2_keys = {(t.symbol, t.entry_idx): t for t in a2.all_trades}
    blocked = [orig_keys[k] for k in set(orig_keys) - set(a2_keys)]

    if blocked:
        bl_sl = [t for t in blocked if t.exit_reason == "STOP_LOSS"]
        bl_sig = [t for t in blocked if t.exit_reason == "SIGNAL"]
        bl_w = [t for t in blocked if t.pnl_pct > 0]
        bl_l = [t for t in blocked if t.pnl_pct <= 0]

        print(f"\n  Toplam engellenen: {len(blocked)} trade")
        print(f"    SL ile bitecekti:  {len(bl_sl)} ({sum(t.pnl_pct for t in bl_sl):+.2f}%)")
        print(f"    Sinyal ile bitecekti: {len(bl_sig)} ({sum(t.pnl_pct for t in bl_sig):+.2f}%)")
        print(f"\n    Loss engellenen: {len(bl_l)} ({sum(t.pnl_pct for t in bl_l):+.2f}%)")
        print(f"    Win kaybedilen:  {len(bl_w)} ({sum(t.pnl_pct for t in bl_w):+.2f}%)")
        eng = abs(sum(t.pnl_pct for t in bl_l))
        kayb = sum(t.pnl_pct for t in bl_w)
        print(f"    NET FAYDA: {eng - kayb:+.2f}%")

        if orig.sl_count > 0:
            print(f"\n    SL engelleme orani: {len(bl_sl)}/{orig.sl_count} = "
                  f"%{len(bl_sl)/orig.sl_count*100:.1f}")

        if bl_sl:
            print(f"\n  Engellenen SL'ler (en buyuk zararlar):")
            print(f"    {'#':<3} {'Coin':<14} {'Yon':<6} {'PnL%':>7} {'RSI':>5} "
                  f"{'ER':>5} {'MACD':>10} {'Rejim':<10}")
            print(f"    {'-'*65}")
            for i, t in enumerate(sorted(bl_sl, key=lambda x: x.pnl_pct)[:15]):
                print(f"    {i+1:<3} {t.symbol:<14} {t.direction:<6} {t.pnl_pct:>+6.2f}% "
                      f"{t.rsi_val:>4.0f} {t.er_val:>4.3f} {t.macd_hist:>+10.6f} {t.regime:<10}")

    # ── 4. LONG vs SHORT ──
    print(f"\n\n{'='*100}")
    print(f"  LONG vs SHORT")
    print(f"{'='*100}")
    print(f"\n  {'Paket':<32} {'L#':>4} {'L.WR':>6} {'L.PnL':>9} "
          f"{'S#':>4} {'S.WR':>6} {'S.PnL':>9}")
    print(f"  {'-'*73}")
    for s in all_s:
        lw = s.long_w / s.long_n * 100 if s.long_n > 0 else 0
        sw = s.short_w / s.short_n * 100 if s.short_n > 0 else 0
        print(f"  {s.name:<32} {s.long_n:>4} {lw:>5.1f}% {s.long_pnl:>+8.2f}% "
              f"{s.short_n:>4} {sw:>5.1f}% {s.short_pnl:>+8.2f}%")

    # ── 5. COIN BAZLI ──
    print(f"\n\n{'='*120}")
    print(f"  COIN BAZLI")
    print(f"{'='*120}")
    all_coins = set()
    for s in all_s:
        all_coins.update(s.coins.keys())

    print(f"\n  {'Coin':<14} {'O.#':>4} {'O.WR':>5} {'O.PnL':>8} {'O.SL':>4} "
          f"{'A2.#':>4} {'A2.WR':>5} {'A2.PnL':>8} {'A2.SL':>4} "
          f"{'Best#':>5} {'B.WR':>5} {'B.PnL':>8} {'En Iyi':<28}")
    print(f"  {'-'*120}")
    for coin in sorted(all_coins):
        o = orig.coins.get(coin, {"n": 0, "w": 0, "pnl": 0.0, "sl": 0})
        owr = o["w"] / o["n"] * 100 if o["n"] > 0 else 0
        a = a2.coins.get(coin, {"n": 0, "w": 0, "pnl": 0.0, "sl": 0})
        awr = a["w"] / a["n"] * 100 if a["n"] > 0 else 0

        best_s = max(all_s[1:], key=lambda s: s.coins.get(coin, {"pnl": -999})["pnl"])
        b = best_s.coins.get(coin, {"n": 0, "w": 0, "pnl": 0.0, "sl": 0})
        bwr = b["w"] / b["n"] * 100 if b["n"] > 0 else 0

        print(f"  {coin:<14} {o['n']:>4} {owr:>4.0f}% {o['pnl']:>+7.2f}% {o['sl']:>4} "
              f"{a['n']:>4} {awr:>4.0f}% {a['pnl']:>+7.2f}% {a['sl']:>4} "
              f"{b['n']:>5} {bwr:>4.0f}% {b['pnl']:>+7.2f}% {best_s.name:<28}")

    # ── 6. REJIM ──
    print(f"\n\n{'='*100}")
    print(f"  REJIM BAZLI")
    print(f"{'='*100}")
    for s in [all_s[0], all_s[2]]:  # Orijinal vs Asama 2
        print(f"\n  {s.name}:")
        rs = {}
        for t in s.all_trades:
            r = t.regime or "UNKNOWN"
            if r not in rs:
                rs[r] = {"n": 0, "w": 0, "pnl": 0.0, "sl": 0}
            rs[r]["n"] += 1
            if t.pnl_pct > 0: rs[r]["w"] += 1
            rs[r]["pnl"] += t.pnl_pct
            if t.exit_reason == "STOP_LOSS": rs[r]["sl"] += 1
        print(f"    {'Rejim':<12} {'#':>5} {'WR%':>6} {'PnL%':>9} {'SL':>4} {'SL%':>5}")
        print(f"    {'-'*45}")
        for r, d in sorted(rs.items()):
            wr = d["w"] / d["n"] * 100 if d["n"] > 0 else 0
            sr = d["sl"] / d["n"] * 100 if d["n"] > 0 else 0
            print(f"    {r:<12} {d['n']:>5} {wr:>5.1f}% {d['pnl']:>+8.2f}% {d['sl']:>4} {sr:>4.1f}%")

    # ═══ SONUC ═══
    print(f"\n\n{'='*110}")
    print(f"  SONUC")
    print(f"{'='*110}")

    best = max(all_s[1:], key=lambda s: s.net_pnl * s.pf if s.total >= 5 else -999)

    print(f"\n  ORIJINAL:  Tr={orig.total} WR={orig.wr:.1f}% PnL={orig.net_pnl:+.2f}% "
          f"PF={orig.pf:.2f} SL={orig.sl_count}({orig.sl_pnl:+.2f}%)")
    print(f"  ASAMA 2:   Tr={a2.total} WR={a2.wr:.1f}% PnL={a2.net_pnl:+.2f}% "
          f"PF={a2.pf:.2f} SL={a2.sl_count}({a2.sl_pnl:+.2f}%)")
    print(f"  EN IYI:    {best.name}")
    print(f"             Tr={best.total} WR={best.wr:.1f}% PnL={best.net_pnl:+.2f}% "
          f"PF={best.pf:.2f} SL={best.sl_count}({best.sl_pnl:+.2f}%)")

    print(f"\n  Sure: {elapsed:.1f}s")

    # JSON
    out = {"timestamp": datetime.now().isoformat(), "symbols": symbols, "results": {}}
    for s in all_s:
        out["results"][s.name] = {
            "total": s.total, "wins": s.wins, "losses": s.losses,
            "wr": round(s.wr, 2), "net_pnl": round(s.net_pnl, 4),
            "pf": round(s.pf, 3), "sl_count": s.sl_count,
            "sl_pnl": round(s.sl_pnl, 4), "coins": s.coins,
        }
    p = Path("data/backtest_comparison_v3.json")
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, ensure_ascii=False, default=str)
    print(f"  Kaydedildi: {p}")


if __name__ == "__main__":
    main()
