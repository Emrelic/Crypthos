"""System M vs System N Karsilastirma — Ayni 50 coinde gercekci simulasyon.

System M: Sabit coeff/period, RSI=period(27), momentum kontrolu YOK
System N: Coin-bazli optimize coeff/period, RSI=14, momentum kontrolu VAR

Her ikisi de: ek filtre YOK, sadece AlphaTrend + ADX.
Fee, SL, likidasyon hepsi dahil.
"""

import sys, os, json, time, argparse
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
from scanner.system_n_scanner import compute_alpha_trend, _compute_adx, _sma
from scanner.system_b_scanner import detect_zigzag_swings, analyze_waves
from loguru import logger

# ═══ SABITLER ═══
TF = "5m"
TARGET_BARS = 8640
WARMUP_BARS = 100
TAKER_FEE = 0.0004
ENTRY_FEE = TAKER_FEE
EXIT_FEE = TAKER_FEE
SL_G_MULT = 1.5
SL_FEE_PCT = (ENTRY_FEE + EXIT_FEE) * 100
SL_ATR_MULT = 2.0
DEFAULT_MAINT_RATE = 0.004
MAX_LEVERAGE = 125
PORTFOLIO_DIVIDER = 12
MIN_POSITION_USDT = 1.0
ADX_LENGTH = 14
ADX_THRESHOLD = 18.0
ZIGZAG_N = 5

# System M sabit parametreleri
M_COEFF = 3.6
M_PERIOD = 27

@dataclass
class Trade:
    symbol: str
    direction: str
    entry_price: float
    exit_price: float
    hold_bars: int
    exit_reason: str
    leverage: int
    pnl_pct: float
    pnl_usdt: float
    total_fee_usdt: float
    sl_pct: float

def calc_liq_price(entry, lev, direction):
    if lev <= 0: lev = 1
    if direction == "LONG":
        return entry * (1 - 1/lev + DEFAULT_MAINT_RATE)
    else:
        return entry * (1 + 1/lev - DEFAULT_MAINT_RATE)

def calc_g_leverage(closes, highs, lows):
    if len(closes) < 100:
        return 0, 1
    try:
        swings = detect_zigzag_swings(highs, lows, n=ZIGZAG_N)
        if len(swings) < 4:
            return 0, 1
        wave = analyze_waves(swings, float(closes[-1]))
        G = wave.G
        if G <= 0 or G > 50:
            return 0, 1
        sl_pct = G * SL_G_MULT + SL_FEE_PCT
        liq_dist = sl_pct * 2.0
        teorik = liq_dist + DEFAULT_MAINT_RATE * 100
        lev = int(100.0 / teorik) if teorik > 0 else 1
        return G, max(1, min(lev, MAX_LEVERAGE))
    except Exception:
        return 0, 1

def fetch_klines(client, symbol, target=TARGET_BARS):
    try:
        df = client.get_klines(symbol, TF, min(target, 1500))
        if df is None or len(df) < WARMUP_BARS: return None
        all_dfs = [df]
        fetched = len(df)
        while fetched < target:
            ts = int(df["timestamp"].iloc[0].timestamp() * 1000)
            try:
                raw = client._get("/fapi/v1/klines", {
                    "symbol": symbol, "interval": TF,
                    "limit": min(1500, target - fetched), "endTime": ts - 1,
                })
            except: break
            if not raw: break
            df2 = pd.DataFrame(raw, columns=[
                "timestamp","open","high","low","close","volume",
                "close_time","quote_volume","trades","taker_buy_volume",
                "taker_buy_quote_volume","ignore",
            ])
            for c in ["open","high","low","close","volume"]:
                df2[c] = df2[c].astype(float)
            df2["timestamp"] = pd.to_datetime(df2["timestamp"], unit="ms")
            if len(df2) == 0: break
            all_dfs.insert(0, df2)
            fetched += len(df2)
            df = df2
            time.sleep(0.1)
        combined = pd.concat(all_dfs, ignore_index=True)
        return combined.drop_duplicates(subset=["timestamp"]).sort_values("timestamp").reset_index(drop=True)
    except: return None


def simulate(closes, highs, lows, volumes, symbol, coeff, period, g_pct, leverage, use_momentum_check=False):
    """Tek coin simulasyon. use_momentum_check=True → System N, False → System M."""
    n = len(closes)
    if n < max(period * 3, ADX_LENGTH * 3, WARMUP_BARS):
        return []

    alpha_trend, atr_arr = compute_alpha_trend(highs, lows, closes, volumes,
                                                coeff=coeff, period=period, use_mfi=True)
    adx_arr, _, _ = _compute_adx(highs, lows, closes, ADX_LENGTH)
    adx_sma = _sma(adx_arr, ADX_LENGTH)
    atr_clean = atr_arr.copy()
    atr_clean[np.isnan(atr_clean)] = 0.0

    if g_pct > 0:
        sl_pct = g_pct * SL_G_MULT + SL_FEE_PCT
    else:
        sl_pct = 5.0

    margin = max(4.0 / PORTFOLIO_DIVIDER, MIN_POSITION_USDT)
    notional = margin * leverage
    trades = []
    position = None
    entry_price = entry_idx = 0
    sl_price = liq_price = 0.0

    start = max(period * 3, WARMUP_BARS, 4)

    def _close(idx, exit_p, reason):
        nonlocal position
        if position == "LONG":
            raw_pnl = (exit_p - entry_price) / entry_price * 100
        else:
            raw_pnl = (entry_price - exit_p) / entry_price * 100
        fee_pct = (ENTRY_FEE + EXIT_FEE) * 100
        net_pnl_pct = raw_pnl - fee_pct
        fee_usdt = notional * (ENTRY_FEE + EXIT_FEE)
        if reason == "LIQUIDATION":
            pnl_usdt = -margin
            net_pnl_pct = -100.0
        else:
            pnl_usdt = margin * (net_pnl_pct / 100) * leverage
        trades.append(Trade(symbol=symbol, direction=position,
                            entry_price=entry_price, exit_price=exit_p,
                            hold_bars=idx - entry_idx, exit_reason=reason,
                            leverage=leverage, pnl_pct=net_pnl_pct,
                            pnl_usdt=pnl_usdt, total_fee_usdt=fee_usdt,
                            sl_pct=sl_pct))
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
        # SL / Liq
        if position is not None:
            if position == "LONG":
                if lows[i] <= liq_price:
                    _close(i, liq_price, "LIQUIDATION"); continue
                if lows[i] <= sl_price:
                    _close(i, sl_price, "STOP_LOSS"); continue
            else:
                if highs[i] >= liq_price:
                    _close(i, liq_price, "LIQUIDATION"); continue
                if highs[i] >= sl_price:
                    _close(i, sl_price, "STOP_LOSS"); continue

        # Sinyal
        at0 = alpha_trend[i]
        at1 = alpha_trend[i-1]
        at2 = alpha_trend[i-2]
        at3 = alpha_trend[i-3]
        if any(np.isnan(v) for v in [at0, at1, at2, at3]): continue

        adx_v = adx_arr[i] if not np.isnan(adx_arr[i]) else 0.0
        adx_d = adx_sma[i] if not np.isnan(adx_sma[i]) else 0.0
        base_ok = (adx_v > ADX_THRESHOLD) and (adx_v > adx_d)

        buy_cross = (at0 > at2) and (at1 <= at3) and base_ok
        sell_cross = (at0 < at2) and (at1 >= at3) and base_ok

        # Momentum tazelik kontrolu (sadece System N)
        if use_momentum_check and (buy_cross or sell_cross):
            delta_now = abs(at0 - at2)
            delta_prev = abs(at1 - at3)
            if delta_prev > 0 and delta_now < delta_prev * 0.7:
                continue  # Stale crossover → atla

        if not (buy_cross or sell_cross): continue

        # Kapat
        if position == "LONG" and sell_cross:
            _close(i, closes[i], "SIGNAL")
        elif position == "SHORT" and buy_cross:
            _close(i, closes[i], "SIGNAL")

        # Ac
        if position is None:
            if buy_cross: _open(i, "LONG")
            elif sell_cross: _open(i, "SHORT")

    return trades


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--top", type=int, default=50)
    parser.add_argument("--symbols", nargs="+", default=None)
    args = parser.parse_args()

    client = BinanceRestClient()
    if args.symbols:
        symbols = args.symbols
    else:
        try:
            t24 = client._get("/fapi/v1/ticker/24hr")
            st = sorted(t24, key=lambda x: float(x.get("quoteVolume",0)), reverse=True)
            symbols = [t["symbol"] for t in st if t["symbol"].endswith("USDT")][:args.top]
        except:
            symbols = []

    # N optimize cache
    opt_cache = {}
    if os.path.exists("data/system_n_optimize.json"):
        try:
            with open("data/system_n_optimize.json","r") as f:
                data = json.load(f)
            for sym, info in data.get("results",{}).items():
                ot = info.get("optimal_tf","5m")
                p = info.get("params",{}).get(ot,{})
                gd = info.get("g_analysis",{}).get(ot,{})
                c,pp = p.get("coeff",0), p.get("period",0)
                G = gd.get("G", info.get("G",0))
                if c>0 and pp>0:
                    opt_cache[sym] = {"coeff":c,"period":pp,"G":G}
        except: pass

    print("\n" + "="*110)
    print("  SYSTEM M vs SYSTEM N — 50 Coin Gercekci Backtest")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M')} | {len(symbols)} coin | 5m | ~30 gun")
    print(f"  M: sabit coeff={M_COEFF} period={M_PERIOD} | momentum check YOK")
    print(f"  N: coin-bazli optimize | momentum check VAR")
    print("="*110)

    m_trades_all = []
    n_trades_all = []

    t0 = time.time()
    for si, sym in enumerate(symbols):
        print(f"  [{si+1}/{len(symbols)}] {sym}", end="", flush=True)

        df = fetch_klines(client, sym)
        if df is None or len(df) < WARMUP_BARS:
            print(" — SKIP"); continue

        c = df["close"].values.astype(float)
        h = df["high"].values.astype(float)
        l = df["low"].values.astype(float)
        v = df["volume"].values.astype(float)
        days = len(df) * 5 / 1440

        # G ve kaldirac (ortak)
        params_n = opt_cache.get(sym, {})
        g_pct_n = params_n.get("G", 0)
        if g_pct_n <= 0 or g_pct_n > 50:
            g_pct_n, lev = calc_g_leverage(c[-300:], h[-300:], l[-300:])
        else:
            sl_p = g_pct_n * SL_G_MULT + SL_FEE_PCT
            teorik = sl_p * 2.0 + DEFAULT_MAINT_RATE * 100
            lev = max(1, min(int(100.0/teorik), MAX_LEVERAGE)) if teorik > 0 else 1

        # System M: sabit param, ayni kaldirac
        m_trades = simulate(c, h, l, v, sym, M_COEFF, M_PERIOD, g_pct_n, lev, use_momentum_check=False)
        # System N: optimize param, momentum check
        n_coeff = params_n.get("coeff", M_COEFF)
        n_period = params_n.get("period", M_PERIOD)
        n_trades = simulate(c, h, l, v, sym, n_coeff, n_period, g_pct_n, lev, use_momentum_check=True)

        m_trades_all.extend(m_trades)
        n_trades_all.extend(n_trades)

        m_net = sum(t.pnl_usdt for t in m_trades)
        n_net = sum(t.pnl_usdt for t in n_trades)
        print(f" {days:.0f}g lev={lev}x | M:{len(m_trades)}tr {m_net:+.2f}$ | N:{len(n_trades)}tr {n_net:+.2f}$")
        time.sleep(0.15)

    elapsed = time.time() - t0

    # ═══ SONUCLAR ═══
    def stats(trades, label):
        if not trades:
            print(f"\n  {label}: 0 trade"); return
        wins = [t for t in trades if t.pnl_usdt > 0]
        losses = [t for t in trades if t.pnl_usdt <= 0]
        sls = [t for t in trades if t.exit_reason == "STOP_LOSS"]
        liqs = [t for t in trades if t.exit_reason == "LIQUIDATION"]
        sigs = [t for t in trades if t.exit_reason == "SIGNAL"]
        total_pnl = sum(t.pnl_usdt for t in trades)
        total_fee = sum(t.total_fee_usdt for t in trades)
        gp = sum(t.pnl_usdt for t in wins)
        gl = abs(sum(t.pnl_usdt for t in losses))
        wr = len(wins)/len(trades)*100
        pf = gp/gl if gl > 0 else 999

        long_t = [t for t in trades if t.direction == "LONG"]
        short_t = [t for t in trades if t.direction == "SHORT"]
        l_w = sum(1 for t in long_t if t.pnl_usdt > 0)
        s_w = sum(1 for t in short_t if t.pnl_usdt > 0)

        print(f"\n  {label}:")
        print(f"    Trade: {len(trades)} | Win: {len(wins)} ({wr:.1f}%) | Loss: {len(losses)}")
        print(f"    Sinyal: {len(sigs)} | SL: {len(sls)} | Liq: {len(liqs)}")
        print(f"    Brut Kar:  +{gp:.4f}$ | Brut Zarar: -{gl:.4f}$")
        print(f"    Fee:       -{total_fee:.4f}$")
        print(f"    SL Zarar:  -{abs(sum(t.pnl_usdt for t in sls)):.4f}$ ({len(sls)} trade)")
        print(f"    Liq Zarar: -{abs(sum(t.pnl_usdt for t in liqs)):.4f}$ ({len(liqs)} trade)")
        print(f"    NET:       {total_pnl:+.4f}$ (ROI: {total_pnl/4*100:+.1f}%)")
        print(f"    PF: {pf:.2f} | Ort Win: +{gp/len(wins):.4f}$" if wins else "")
        print(f"    Ort Loss: {sum(t.pnl_usdt for t in losses)/len(losses):.4f}$" if losses else "")
        print(f"    LONG:  {len(long_t)} tr, WR={l_w/len(long_t)*100:.1f}%, PnL={sum(t.pnl_usdt for t in long_t):+.4f}$" if long_t else "")
        print(f"    SHORT: {len(short_t)} tr, WR={s_w/len(short_t)*100:.1f}%, PnL={sum(t.pnl_usdt for t in short_t):+.4f}$" if short_t else "")
        print(f"    Sinyal WR: {sum(1 for t in sigs if t.pnl_usdt>0)/len(sigs)*100:.1f}% | "
              f"Sinyal PnL: {sum(t.pnl_usdt for t in sigs):+.4f}$" if sigs else "")
        return {"trades": len(trades), "wins": len(wins), "wr": wr, "pnl": total_pnl,
                "pf": pf, "fee": total_fee, "sl": len(sls), "liq": len(liqs)}

    print(f"\n\n{'='*110}")
    print(f"  KARSILASTIRMA SONUCLARI")
    print(f"{'='*110}")

    m_s = stats(m_trades_all, "SYSTEM M (sabit param, momentum check YOK)")
    n_s = stats(n_trades_all, "SYSTEM N (optimize param, momentum check VAR)")

    if m_s and n_s:
        print(f"\n\n{'='*80}")
        print(f"  FARK TABLOSU (N - M)")
        print(f"{'='*80}")
        print(f"    Trade:  {n_s['trades'] - m_s['trades']:+d}")
        print(f"    WR:     {n_s['wr'] - m_s['wr']:+.1f}%")
        print(f"    PnL:    {n_s['pnl'] - m_s['pnl']:+.4f}$")
        print(f"    PF:     {n_s['pf'] - m_s['pf']:+.2f}")
        print(f"    Fee:    {n_s['fee'] - m_s['fee']:+.4f}$")
        print(f"    SL:     {n_s['sl'] - m_s['sl']:+d}")
        print(f"    Liq:    {n_s['liq'] - m_s['liq']:+d}")

    # Coin bazli
    print(f"\n\n{'='*120}")
    print(f"  COIN BAZLI M vs N")
    print(f"{'='*120}")
    coins_m = {}
    coins_n = {}
    for t in m_trades_all:
        if t.symbol not in coins_m: coins_m[t.symbol] = {"n":0,"pnl":0,"sl":0}
        coins_m[t.symbol]["n"] += 1
        coins_m[t.symbol]["pnl"] += t.pnl_usdt
        if t.exit_reason == "STOP_LOSS": coins_m[t.symbol]["sl"] += 1
    for t in n_trades_all:
        if t.symbol not in coins_n: coins_n[t.symbol] = {"n":0,"pnl":0,"sl":0}
        coins_n[t.symbol]["n"] += 1
        coins_n[t.symbol]["pnl"] += t.pnl_usdt
        if t.exit_reason == "STOP_LOSS": coins_n[t.symbol]["sl"] += 1

    all_c = set(list(coins_m.keys()) + list(coins_n.keys()))
    print(f"\n  {'Coin':<14} {'M.Tr':>5} {'M.PnL$':>8} {'M.SL':>4} {'N.Tr':>5} {'N.PnL$':>8} {'N.SL':>4} {'dPnL$':>8} {'Kazanan':<8}")
    print(f"  {'-'*72}")
    for coin in sorted(all_c, key=lambda x: (coins_n.get(x,{}).get("pnl",0) - coins_m.get(x,{}).get("pnl",0)), reverse=True):
        m = coins_m.get(coin, {"n":0,"pnl":0,"sl":0})
        n = coins_n.get(coin, {"n":0,"pnl":0,"sl":0})
        d = n["pnl"] - m["pnl"]
        w = "N" if d > 0.01 else ("M" if d < -0.01 else "=")
        print(f"  {coin:<14} {m['n']:>5} {m['pnl']:>+7.3f} {m['sl']:>4} "
              f"{n['n']:>5} {n['pnl']:>+7.3f} {n['sl']:>4} {d:>+7.3f} {w:<8}")

    print(f"\n  Sure: {elapsed:.1f}s")

    # Kazanan sayisi
    n_better = sum(1 for c in all_c if coins_n.get(c,{}).get("pnl",0) > coins_m.get(c,{}).get("pnl",0) + 0.01)
    m_better = sum(1 for c in all_c if coins_m.get(c,{}).get("pnl",0) > coins_n.get(c,{}).get("pnl",0) + 0.01)
    print(f"\n  N daha iyi: {n_better} coin | M daha iyi: {m_better} coin | Esit: {len(all_c)-n_better-m_better}")


if __name__ == "__main__":
    main()
