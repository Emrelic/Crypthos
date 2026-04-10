"""System N Gercekci Simulasyon — Orijinal AlphaTrend (ek filtre YOK).

Her sey hesaplanir:
  - Fee: giris %0.04 taker + cikis %0.04 taker (veya SL icin %0.04)
  - Kaldirac: G-bazli hesaplama (canli sistemle ayni)
  - Likidasyon: maint margin + fee ile gercek liq fiyati
  - SL: G*1.5 + fee (server-side, guvenlik)
  - Pozisyon buyuklugu: bakiye/12 (portfoy bolme)
  - Gercek USDT kar/zarar
  - Win/Loss, kazanc/kayip miktari, fee miktari, SL miktari

Kullanim:
    python backtest_realistic.py                  # Top 50 coin
    python backtest_realistic.py --top 20         # Top 20
    python backtest_realistic.py --balance 100    # 100 USDT baslangic
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

import io
if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from market.binance_rest import BinanceRestClient
from scanner.system_n_scanner import (
    compute_alpha_trend, _compute_adx, _sma,
)
from scanner.system_b_scanner import (
    detect_zigzag_swings, analyze_waves,
    compute_rolling_er, compute_hurst_exponent,
)
from loguru import logger

# ═══════════════════════════════════════════════════════════════════
#  SABITLER (canli sistem ile AYNI)
# ═══════════════════════════════════════════════════════════════════

TF = "5m"
TARGET_BARS = 8640        # ~30 gun

# Fee
TAKER_FEE = 0.0004       # %0.04 (Binance futures taker)
MAKER_FEE = 0.0002       # %0.02 (Binance futures maker)
# Giris: taker (market), Cikis sinyal: taker (market), Cikis SL: taker
ENTRY_FEE = TAKER_FEE
EXIT_FEE_SIGNAL = TAKER_FEE
EXIT_FEE_SL = TAKER_FEE

# SL
SL_G_MULT = 1.5
SL_FEE_PCT = (ENTRY_FEE + EXIT_FEE_SL) * 100  # fee-aware SL
SL_ATR_MULT = 2.0        # G yoksa fallback

# Kaldirac
DEFAULT_MAINT_RATE = 0.004  # %0.4
MAX_LEVERAGE = 125

# Pozisyon
PORTFOLIO_DIVIDER = 12    # bakiye/12
MIN_POSITION_USDT = 1.0
MAX_POSITIONS = 12

# AlphaTrend
DEFAULT_COEFF = 3.6
DEFAULT_PERIOD = 27
ADX_LENGTH = 14
ADX_THRESHOLD = 18.0
ZIGZAG_N = 5
WARMUP_BARS = 100


# ═══════════════════════════════════════════════════════════════════
#  Data Classes
# ═══════════════════════════════════════════════════════════════════

@dataclass
class Trade:
    symbol: str
    direction: str          # "LONG" / "SHORT"
    entry_price: float
    exit_price: float
    entry_idx: int
    exit_idx: int
    hold_bars: int
    exit_reason: str        # "SIGNAL" / "STOP_LOSS" / "LIQUIDATION"
    # Kaldirac & pozisyon
    leverage: int
    margin_usdt: float      # yatirilan teminat
    notional_usdt: float    # margin × leverage
    # PnL
    pnl_pct: float          # kaldiracli ROI %
    pnl_usdt: float         # net kar/zarar USDT
    # Fee
    entry_fee_usdt: float
    exit_fee_usdt: float
    total_fee_usdt: float
    # SL / Liq
    sl_price: float
    sl_pct: float           # SL mesafesi %
    liq_price: float
    liq_pct: float          # Liq mesafesi %


# ═══════════════════════════════════════════════════════════════════
#  G Dalga + Kaldirac Hesaplama
# ═══════════════════════════════════════════════════════════════════

def calc_g_and_leverage(closes, highs, lows):
    """G dalga analizi → kaldirac + SL hesapla."""
    if len(closes) < 100:
        return 0, 1, 5.0, 0, 0

    try:
        swings = detect_zigzag_swings(highs, lows, n=ZIGZAG_N)
        if len(swings) < 4:
            return 0, 1, 5.0, 0, 0
        wave = analyze_waves(swings, float(closes[-1]))
        G = wave.G
        if G <= 0 or G > 50:
            return 0, 1, 5.0, 0, 0

        # SL = G × 1.5 + fee
        sl_pct = G * SL_G_MULT + SL_FEE_PCT
        # Liq mesafesi = SL × 2
        liq_dist = sl_pct * 2.0
        teorik_liq = liq_dist + DEFAULT_MAINT_RATE * 100
        max_lev = int(100.0 / teorik_liq) if teorik_liq > 0 else 1
        max_lev = max(1, min(max_lev, MAX_LEVERAGE))

        er = compute_rolling_er(closes, window=20, median_count=10)
        hurst = compute_hurst_exponent(closes)

        return G, max_lev, sl_pct, er, hurst
    except Exception:
        return 0, 1, 5.0, 0, 0


# ═══════════════════════════════════════════════════════════════════
#  Likidasyon Fiyati Hesapla
# ═══════════════════════════════════════════════════════════════════

def calc_liq_price(entry_price, leverage, direction, maint_rate=DEFAULT_MAINT_RATE):
    """Binance isolated margin likidasyon fiyati.

    LONG:  liq = entry × (1 - 1/lev + maint_rate)
           → fiyat duserse liq olur
    SHORT: liq = entry × (1 + 1/lev - maint_rate)
           → fiyat yukselirse liq olur
    """
    if leverage <= 0:
        leverage = 1
    if direction == "LONG":
        liq = entry_price * (1 - 1 / leverage + maint_rate)
    else:
        liq = entry_price * (1 + 1 / leverage - maint_rate)
    return max(liq, 0)


# ═══════════════════════════════════════════════════════════════════
#  Fetch
# ═══════════════════════════════════════════════════════════════════

def fetch_klines(client, symbol, target=TARGET_BARS):
    try:
        df = client.get_klines(symbol, TF, min(target, 1500))
        if df is None or len(df) < WARMUP_BARS:
            return None
        all_dfs = [df]
        fetched = len(df)
        while fetched < target:
            ts = int(df["timestamp"].iloc[0].timestamp() * 1000)
            try:
                raw = client._get("/fapi/v1/klines", {
                    "symbol": symbol, "interval": TF,
                    "limit": min(1500, target - fetched),
                    "endTime": ts - 1,
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
            for c in ["open", "high", "low", "close", "volume"]:
                df2[c] = df2[c].astype(float)
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
    except Exception:
        return None


# ═══════════════════════════════════════════════════════════════════
#  SIMULASYON
# ═══════════════════════════════════════════════════════════════════

def simulate_coin(closes, highs, lows, volumes, symbol, coeff, period, g_pct, leverage):
    """Tek coin icin tam simulasyon — orijinal AlphaTrend, ek filtre YOK."""
    n = len(closes)
    if n < max(period * 3, ADX_LENGTH * 3, WARMUP_BARS):
        return []

    # Indikatorler
    alpha_trend, atr_arr = compute_alpha_trend(
        highs, lows, closes, volumes,
        coeff=coeff, period=period, use_mfi=True,
    )
    adx_arr, _, _ = _compute_adx(highs, lows, closes, ADX_LENGTH)
    adx_sma = _sma(adx_arr, ADX_LENGTH)
    atr_clean = atr_arr.copy()
    atr_clean[np.isnan(atr_clean)] = 0.0

    # SL hesapla
    if g_pct > 0:
        sl_pct = g_pct * SL_G_MULT + SL_FEE_PCT
    else:
        sl_pct = 5.0  # fallback

    trades = []
    position = None
    entry_idx = 0
    entry_price = 0.0
    sl_price = 0.0
    liq_price = 0.0

    start = max(period * 3, WARMUP_BARS, 4)

    def _close(idx, exit_price, reason):
        nonlocal position
        # Fee
        notional_entry = entry_price * 1  # normalize edilecek (main'de margin ile carpilir)
        entry_fee = entry_price * ENTRY_FEE
        exit_fee = exit_price * EXIT_FEE_SIGNAL if reason == "SIGNAL" else exit_price * EXIT_FEE_SL
        total_fee = entry_fee + exit_fee

        # PnL (kaldirac ONCESI, fee DAHIL)
        if position == "LONG":
            raw_pnl_pct = (exit_price - entry_price) / entry_price * 100
        else:
            raw_pnl_pct = (entry_price - exit_price) / entry_price * 100

        fee_pct = (ENTRY_FEE + (EXIT_FEE_SIGNAL if reason == "SIGNAL" else EXIT_FEE_SL)) * 100
        net_pnl_pct = raw_pnl_pct - fee_pct

        # Liq mesafesi
        liq_dist = abs(liq_price - entry_price) / entry_price * 100 if entry_price > 0 else 0

        trades.append(Trade(
            symbol=symbol, direction=position,
            entry_price=entry_price, exit_price=exit_price,
            entry_idx=entry_idx, exit_idx=idx,
            hold_bars=idx - entry_idx, exit_reason=reason,
            leverage=leverage,
            margin_usdt=0, notional_usdt=0,  # main'de hesaplanacak
            pnl_pct=net_pnl_pct, pnl_usdt=0,
            entry_fee_usdt=0, exit_fee_usdt=0, total_fee_usdt=0,
            sl_price=sl_price, sl_pct=sl_pct,
            liq_price=liq_price, liq_pct=liq_dist,
        ))
        position = None

    def _open(idx, direction):
        nonlocal position, entry_idx, entry_price, sl_price, liq_price
        position = direction
        entry_idx = idx
        entry_price = closes[idx]

        if direction == "LONG":
            sl_price = entry_price * (1 - sl_pct / 100)
        else:
            sl_price = entry_price * (1 + sl_pct / 100)

        liq_price = calc_liq_price(entry_price, leverage, direction)

    for i in range(start, n):
        # ── Acik pozisyon: SL + Liq kontrol ──
        if position is not None:
            # Likidasyon kontrolu (SL'den once!)
            if position == "LONG":
                if lows[i] <= liq_price:
                    _close(i, liq_price, "LIQUIDATION")
                    continue
                if lows[i] <= sl_price:
                    _close(i, sl_price, "STOP_LOSS")
                    continue
            else:
                if highs[i] >= liq_price:
                    _close(i, liq_price, "LIQUIDATION")
                    continue
                if highs[i] >= sl_price:
                    _close(i, sl_price, "STOP_LOSS")
                    continue

        # ── Sinyal ──
        at_now = alpha_trend[i]
        at_1 = alpha_trend[i - 1]
        at_2 = alpha_trend[i - 2]
        at_3 = alpha_trend[i - 3]

        if any(np.isnan(v) for v in [at_now, at_1, at_2, at_3]):
            continue

        adx_val = adx_arr[i] if not np.isnan(adx_arr[i]) else 0.0
        adx_dyn = adx_sma[i] if not np.isnan(adx_sma[i]) else 0.0
        base_filter = (adx_val > ADX_THRESHOLD) and (adx_val > adx_dyn)

        buy_cross = (at_now > at_2) and (at_1 <= at_3) and base_filter
        sell_cross = (at_now < at_2) and (at_1 >= at_3) and base_filter

        if not (buy_cross or sell_cross):
            continue

        # Acik pozisyon + ters sinyal → kapat
        if position == "LONG" and sell_cross:
            _close(i, closes[i], "SIGNAL")
        elif position == "SHORT" and buy_cross:
            _close(i, closes[i], "SIGNAL")

        # Yeni pozisyon
        if position is None:
            if buy_cross:
                _open(i, "LONG")
            elif sell_cross:
                _open(i, "SHORT")

    return trades


# ═══════════════════════════════════════════════════════════════════
#  Main
# ═══════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--top", type=int, default=50)
    parser.add_argument("--balance", type=float, default=4.0, help="Baslangic bakiye USDT")
    parser.add_argument("--symbols", nargs="+", default=None)
    args = parser.parse_args()

    client = BinanceRestClient()
    if args.symbols:
        symbols = args.symbols
    else:
        try:
            t24 = client._get("/fapi/v1/ticker/24hr")
            st = sorted(t24, key=lambda x: float(x.get("quoteVolume", 0)), reverse=True)
            symbols = [t["symbol"] for t in st if t["symbol"].endswith("USDT")][:args.top]
        except Exception:
            symbols = []

    # Optimize cache
    opt_cache = {}
    if os.path.exists("data/system_n_optimize.json"):
        try:
            with open("data/system_n_optimize.json", "r") as f:
                data = json.load(f)
            for sym, info in data.get("results", {}).items():
                opt_tf = info.get("optimal_tf", "5m")
                params = info.get("params", {}).get(opt_tf, {})
                g_data = info.get("g_analysis", {}).get(opt_tf, {})
                c = params.get("coeff", 0)
                p = params.get("period", 0)
                G = g_data.get("G", info.get("G", 0))
                if c > 0 and p > 0:
                    opt_cache[sym] = {"coeff": c, "period": p, "G": G}
        except Exception:
            pass

    BALANCE = args.balance

    print("\n" + "=" * 110)
    print("  SYSTEM N GERCEKCI SIMULASYON — Orijinal AlphaTrend (ek filtre YOK)")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"  {len(symbols)} coin | {TF} | ~30 gun | Baslangic: {BALANCE:.2f} USDT")
    print(f"  Fee: giris %{ENTRY_FEE*100:.2f} + cikis %{EXIT_FEE_SIGNAL*100:.2f} (taker)")
    print(f"  SL: G×{SL_G_MULT} + fee | Portfoy: 1/{PORTFOLIO_DIVIDER} | Max {MAX_POSITIONS} poz")
    print("=" * 110)

    all_trades: list[Trade] = []
    coin_summaries = []

    t0 = time.time()
    for si, sym in enumerate(symbols):
        print(f"  [{si+1}/{len(symbols)}] {sym}", end="", flush=True)

        df = fetch_klines(client, sym)
        if df is None or len(df) < WARMUP_BARS:
            print(" — SKIP")
            continue

        c = df["close"].values.astype(float)
        h = df["high"].values.astype(float)
        l = df["low"].values.astype(float)
        v = df["volume"].values.astype(float)
        days = len(df) * 5 / 1440

        params = opt_cache.get(sym, {})
        coeff = params.get("coeff", DEFAULT_COEFF)
        period = params.get("period", DEFAULT_PERIOD)
        g_pct = params.get("G", 0)

        # G ve kaldirac hesapla (son 300 mum)
        if g_pct <= 0 or g_pct > 50:
            g_pct, lev, sl_p, er, hurst = calc_g_and_leverage(c[-300:], h[-300:], l[-300:])
        else:
            sl_p = g_pct * SL_G_MULT + SL_FEE_PCT
            liq_dist = sl_p * 2.0
            teorik = liq_dist + DEFAULT_MAINT_RATE * 100
            lev = int(100.0 / teorik) if teorik > 0 else 1
            lev = max(1, min(lev, MAX_LEVERAGE))

        print(f" {len(df)} mum ({days:.0f}g) G={g_pct:.3f}% lev={lev}x", end="", flush=True)

        trades = simulate_coin(c, h, l, v, sym, coeff, period, g_pct, lev)

        # Margin ve USDT hesapla
        margin = max(BALANCE / PORTFOLIO_DIVIDER, MIN_POSITION_USDT)
        for t in trades:
            t.margin_usdt = margin
            t.notional_usdt = margin * t.leverage
            # Fee USDT
            t.entry_fee_usdt = t.notional_usdt * ENTRY_FEE
            exit_rate = EXIT_FEE_SIGNAL if t.exit_reason == "SIGNAL" else EXIT_FEE_SL
            t.exit_fee_usdt = t.notional_usdt * exit_rate
            t.total_fee_usdt = t.entry_fee_usdt + t.exit_fee_usdt
            # PnL USDT (kaldiracli)
            if t.exit_reason == "LIQUIDATION":
                t.pnl_usdt = -margin  # tam kayip
                t.pnl_pct = -100.0
            else:
                t.pnl_usdt = margin * (t.pnl_pct / 100) * t.leverage

        all_trades.extend(trades)

        n_sl = sum(1 for t in trades if t.exit_reason == "STOP_LOSS")
        n_liq = sum(1 for t in trades if t.exit_reason == "LIQUIDATION")
        n_sig = sum(1 for t in trades if t.exit_reason == "SIGNAL")
        net = sum(t.pnl_usdt for t in trades)

        print(f" | {len(trades)} tr (sig:{n_sig} sl:{n_sl} liq:{n_liq}) net:{net:+.4f}$", flush=True)

        if trades:
            coin_summaries.append({
                "symbol": sym, "trades": len(trades),
                "signal": n_sig, "sl": n_sl, "liq": n_liq,
                "net_usdt": net, "leverage": lev, "g_pct": g_pct,
                "wins": sum(1 for t in trades if t.pnl_usdt > 0),
                "fees": sum(t.total_fee_usdt for t in trades),
            })

        time.sleep(0.15)

    elapsed = time.time() - t0

    if not all_trades:
        print("\n  Hic trade yok!")
        return

    # ═══════════════════════════════════════════════════════════════
    #  SONUCLAR
    # ═══════════════════════════════════════════════════════════════

    wins = [t for t in all_trades if t.pnl_usdt > 0]
    losses = [t for t in all_trades if t.pnl_usdt <= 0]
    sl_trades = [t for t in all_trades if t.exit_reason == "STOP_LOSS"]
    liq_trades = [t for t in all_trades if t.exit_reason == "LIQUIDATION"]
    sig_trades = [t for t in all_trades if t.exit_reason == "SIGNAL"]

    total_pnl = sum(t.pnl_usdt for t in all_trades)
    total_fees = sum(t.total_fee_usdt for t in all_trades)
    total_gross_profit = sum(t.pnl_usdt for t in wins)
    total_gross_loss = abs(sum(t.pnl_usdt for t in losses))
    sl_loss = abs(sum(t.pnl_usdt for t in sl_trades))
    liq_loss = abs(sum(t.pnl_usdt for t in liq_trades))
    sig_pnl = sum(t.pnl_usdt for t in sig_trades)

    wr = len(wins) / len(all_trades) * 100
    pf = total_gross_profit / total_gross_loss if total_gross_loss > 0 else 999

    long_trades = [t for t in all_trades if t.direction == "LONG"]
    short_trades = [t for t in all_trades if t.direction == "SHORT"]

    print(f"\n\n{'='*110}")
    print(f"  GENEL OZET — {len(symbols)} coin, ~30 gun, baslangic {BALANCE:.2f} USDT")
    print(f"{'='*110}")

    print(f"\n  TRADE SAYILARI:")
    print(f"    Toplam:      {len(all_trades)}")
    print(f"    Win:         {len(wins)} ({wr:.1f}%)")
    print(f"    Loss:        {len(losses)} ({100-wr:.1f}%)")
    print(f"    Sinyal cikis:{len(sig_trades)}")
    print(f"    Stop Loss:   {len(sl_trades)}")
    print(f"    Likidasyon:  {len(liq_trades)}")

    print(f"\n  KAR / ZARAR (USDT):")
    print(f"    Brut Kar:    +{total_gross_profit:.4f} USDT")
    print(f"    Brut Zarar:  -{total_gross_loss:.4f} USDT")
    print(f"    Toplam Fee:  -{total_fees:.4f} USDT")
    print(f"    SL Zarari:   -{sl_loss:.4f} USDT ({len(sl_trades)} trade)")
    print(f"    Liq Zarari:  -{liq_loss:.4f} USDT ({len(liq_trades)} trade)")
    print(f"    Sinyal PnL:  {sig_pnl:+.4f} USDT ({len(sig_trades)} trade)")
    print(f"    ─────────────────────────")
    print(f"    NET KAR:     {total_pnl:+.4f} USDT")
    print(f"    ROI:         {total_pnl/BALANCE*100:+.2f}%")

    print(f"\n  ORANLAR:")
    print(f"    Win Rate:      {wr:.1f}%")
    print(f"    Profit Factor: {pf:.2f}")
    print(f"    Ort. Win:      +{np.mean([t.pnl_usdt for t in wins]):.4f} USDT" if wins else "")
    print(f"    Ort. Loss:     {np.mean([t.pnl_usdt for t in losses]):.4f} USDT" if losses else "")
    print(f"    Fee / Brut Kar:{total_fees/total_gross_profit*100:.1f}%" if total_gross_profit > 0 else "")

    print(f"\n  LONG vs SHORT:")
    l_w = sum(1 for t in long_trades if t.pnl_usdt > 0)
    s_w = sum(1 for t in short_trades if t.pnl_usdt > 0)
    l_pnl = sum(t.pnl_usdt for t in long_trades)
    s_pnl = sum(t.pnl_usdt for t in short_trades)
    print(f"    LONG:  {len(long_trades)} trade, WR={l_w/len(long_trades)*100:.1f}%, "
          f"PnL={l_pnl:+.4f} USDT" if long_trades else "    LONG: 0")
    print(f"    SHORT: {len(short_trades)} trade, WR={s_w/len(short_trades)*100:.1f}%, "
          f"PnL={s_pnl:+.4f} USDT" if short_trades else "    SHORT: 0")

    # ── COIN BAZLI TABLO ──
    print(f"\n\n{'='*130}")
    print(f"  COIN BAZLI DETAY")
    print(f"{'='*130}")

    print(f"\n  {'Coin':<14} {'Lev':>4} {'G%':>6} {'Tr':>4} {'Win':>4} {'WR%':>5} "
          f"{'Sig':>4} {'SL':>3} {'Liq':>3} {'NetPnL$':>9} {'Fee$':>7} "
          f"{'SL$':>8} {'BrutKar$':>9} {'BrutZar$':>9}")
    print(f"  {'-'*125}")

    for cs in sorted(coin_summaries, key=lambda x: x["net_usdt"], reverse=True):
        s = cs["symbol"]
        ct = [t for t in all_trades if t.symbol == s]
        cw = sum(1 for t in ct if t.pnl_usdt > 0)
        cwr = cw / len(ct) * 100 if ct else 0
        cfee = cs["fees"]
        csl_loss = abs(sum(t.pnl_usdt for t in ct if t.exit_reason == "STOP_LOSS"))
        c_gross_p = sum(t.pnl_usdt for t in ct if t.pnl_usdt > 0)
        c_gross_l = abs(sum(t.pnl_usdt for t in ct if t.pnl_usdt <= 0))

        print(f"  {s:<14} {cs['leverage']:>3}x {cs['g_pct']:>5.3f} {cs['trades']:>4} "
              f"{cw:>4} {cwr:>4.0f}% {cs['signal']:>4} {cs['sl']:>3} {cs['liq']:>3} "
              f"{cs['net_usdt']:>+8.4f} {cfee:>6.4f} {csl_loss:>7.4f} "
              f"{c_gross_p:>+8.4f} {c_gross_l:>8.4f}")

    # ── EN BUYUK KAZANCLAR ──
    print(f"\n\n  EN BUYUK 15 KAZANC:")
    print(f"  {'#':<3} {'Coin':<14} {'Yon':<6} {'Lev':>4} {'PnL$':>9} {'PnL%':>7} "
          f"{'Fee$':>6} {'Cikis':<8} {'Hold':>5}")
    print(f"  {'-'*70}")
    for i, t in enumerate(sorted(wins, key=lambda x: x.pnl_usdt, reverse=True)[:15]):
        print(f"  {i+1:<3} {t.symbol:<14} {t.direction:<6} {t.leverage:>3}x "
              f"{t.pnl_usdt:>+8.4f} {t.pnl_pct:>+6.1f}% {t.total_fee_usdt:>5.4f} "
              f"{t.exit_reason:<8} {t.hold_bars:>5}")

    # ── EN BUYUK ZARARLAR ──
    print(f"\n  EN BUYUK 15 ZARAR:")
    print(f"  {'#':<3} {'Coin':<14} {'Yon':<6} {'Lev':>4} {'PnL$':>9} {'PnL%':>7} "
          f"{'Fee$':>6} {'Cikis':<8} {'SL%':>5} {'Hold':>5}")
    print(f"  {'-'*80}")
    for i, t in enumerate(sorted(losses, key=lambda x: x.pnl_usdt)[:15]):
        print(f"  {i+1:<3} {t.symbol:<14} {t.direction:<6} {t.leverage:>3}x "
              f"{t.pnl_usdt:>+8.4f} {t.pnl_pct:>+6.1f}% {t.total_fee_usdt:>5.4f} "
              f"{t.exit_reason:<8} {t.sl_pct:>4.1f}% {t.hold_bars:>5}")

    # ── CIKIS TIPI OZET ──
    print(f"\n\n{'='*80}")
    print(f"  CIKIS TIPI OZET")
    print(f"{'='*80}")
    for reason, label in [("SIGNAL", "Sinyal"), ("STOP_LOSS", "Stop Loss"), ("LIQUIDATION", "Likidasyon")]:
        rt = [t for t in all_trades if t.exit_reason == reason]
        if not rt:
            continue
        rw = sum(1 for t in rt if t.pnl_usdt > 0)
        rp = sum(t.pnl_usdt for t in rt)
        rf = sum(t.total_fee_usdt for t in rt)
        print(f"  {label:<12}: {len(rt):>5} trade | WR={rw/len(rt)*100:>5.1f}% | "
              f"PnL={rp:>+9.4f}$ | Fee={rf:>.4f}$ | "
              f"Ort={rp/len(rt):>+.4f}$/trade")

    print(f"\n  Sure: {elapsed:.1f}s")

    # JSON
    out = {
        "timestamp": datetime.now().isoformat(),
        "config": {
            "balance": BALANCE, "symbols": len(symbols),
            "entry_fee": ENTRY_FEE, "exit_fee": EXIT_FEE_SIGNAL,
            "sl_g_mult": SL_G_MULT, "portfolio_divider": PORTFOLIO_DIVIDER,
        },
        "summary": {
            "total_trades": len(all_trades),
            "wins": len(wins), "losses": len(losses),
            "win_rate": round(wr, 2),
            "net_pnl_usdt": round(total_pnl, 6),
            "total_fees_usdt": round(total_fees, 6),
            "profit_factor": round(pf, 3),
            "sl_count": len(sl_trades), "liq_count": len(liq_trades),
            "roi_pct": round(total_pnl / BALANCE * 100, 2),
        },
        "coins": coin_summaries,
    }
    p = Path("data/backtest_realistic.json")
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, ensure_ascii=False, default=str)
    print(f"  Kaydedildi: {p}")


if __name__ == "__main__":
    main()
