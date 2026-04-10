"""System N Trade Analizi — Karlı vs Zararlı Pozisyonlarda İndikatör Karşılaştırması.

Her kapanan pozisyon için giriş anındaki tüm indikatörleri hesaplar,
kazanan ve kaybeden trade'ler arasındaki farkları analiz eder.
Potansiyel filtre önerileri sunar.
"""

import sqlite3
import time
import json
import sys
import os

# Force UTF-8 output on Windows
if sys.platform == "win32":
    os.environ["PYTHONIOENCODING"] = "utf-8"
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

import numpy as np
import pandas as pd
import requests
from datetime import datetime, timedelta, timezone
from collections import defaultdict

# ─── Binance REST (lightweight) ────────────────────────────────

BINANCE_BASE = "https://fapi.binance.com"
session = requests.Session()
session.headers.update({"Accept": "application/json"})


def get_klines(symbol: str, interval: str, limit: int = 300,
               end_time: int = None) -> pd.DataFrame:
    """Binance'den kline çek, DataFrame döndür."""
    params = {"symbol": symbol, "interval": interval, "limit": limit}
    if end_time:
        params["endTime"] = end_time
    try:
        resp = session.get(f"{BINANCE_BASE}/fapi/v1/klines", params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"  [HATA] {symbol} kline çekilemedi: {e}")
        return pd.DataFrame()

    if not data:
        return pd.DataFrame()

    df = pd.DataFrame(data, columns=[
        "open_time", "open", "high", "low", "close", "volume",
        "close_time", "quote_volume", "trades", "taker_buy_vol",
        "taker_buy_quote", "ignore"
    ])
    for col in ["open", "high", "low", "close", "volume", "quote_volume",
                "taker_buy_vol", "taker_buy_quote"]:
        df[col] = df[col].astype(float)
    df["open_time"] = pd.to_datetime(df["open_time"], unit="ms")
    return df


# ─── İndikatör Hesaplamaları ───────────────────────────────────

def compute_rsi(close: pd.Series, period: int = 14) -> float:
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / (loss + 1e-10)
    rsi = 100 - 100 / (1 + rs)
    return rsi.iloc[-1] if len(rsi) > 0 else 50.0


def compute_adx(df: pd.DataFrame, period: int = 14) -> float:
    high, low, close = df["high"], df["low"], df["close"]
    plus_dm = high.diff().clip(lower=0)
    minus_dm = (-low.diff()).clip(lower=0)
    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low - close.shift()).abs()
    ], axis=1).max(axis=1)
    atr = tr.rolling(period).mean()
    plus_di = 100 * (plus_dm.rolling(period).mean() / (atr + 1e-10))
    minus_di = 100 * (minus_dm.rolling(period).mean() / (atr + 1e-10))
    dx = 100 * ((plus_di - minus_di).abs() / (plus_di + minus_di + 1e-10))
    adx = dx.rolling(period).mean()
    return adx.iloc[-1] if len(adx) > 0 else 0.0


def compute_macd(close: pd.Series, fast=12, slow=26, signal=9):
    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    histogram = macd_line - signal_line
    return {
        "macd": macd_line.iloc[-1],
        "signal": signal_line.iloc[-1],
        "histogram": histogram.iloc[-1],
        "hist_prev": histogram.iloc[-2] if len(histogram) > 1 else 0,
        "macd_cross_up": macd_line.iloc[-1] > signal_line.iloc[-1] and macd_line.iloc[-2] <= signal_line.iloc[-2] if len(macd_line) > 1 else False,
        "macd_cross_down": macd_line.iloc[-1] < signal_line.iloc[-1] and macd_line.iloc[-2] >= signal_line.iloc[-2] if len(macd_line) > 1 else False,
    }


def compute_bollinger(close: pd.Series, period: int = 20, std_mult: float = 2.0):
    sma = close.rolling(period).mean()
    std = close.rolling(period).std()
    upper = sma + std_mult * std
    lower = sma - std_mult * std
    price = close.iloc[-1]
    bb_width = ((upper - lower) / (sma + 1e-10) * 100).iloc[-1]
    bb_pos = ((price - lower.iloc[-1]) / (upper.iloc[-1] - lower.iloc[-1] + 1e-10))
    return {
        "bb_upper": upper.iloc[-1],
        "bb_lower": lower.iloc[-1],
        "bb_middle": sma.iloc[-1],
        "bb_width": bb_width,
        "bb_position": bb_pos,  # 0=alt band, 1=üst band
    }


def compute_ema(close: pd.Series, period: int) -> float:
    return close.ewm(span=period, adjust=False).mean().iloc[-1]


def compute_atr(df: pd.DataFrame, period: int = 14) -> float:
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - df["close"].shift()).abs(),
        (df["low"] - df["close"].shift()).abs()
    ], axis=1).max(axis=1)
    return tr.rolling(period).mean().iloc[-1]


def compute_obv(df: pd.DataFrame) -> dict:
    obv = (np.sign(df["close"].diff()) * df["volume"]).cumsum()
    obv_sma = obv.rolling(20).mean()
    return {
        "obv": obv.iloc[-1],
        "obv_sma20": obv_sma.iloc[-1] if len(obv_sma) > 19 else 0,
        "obv_above_sma": obv.iloc[-1] > obv_sma.iloc[-1] if len(obv_sma) > 19 else False,
    }


def compute_mfi(df: pd.DataFrame, period: int = 14) -> float:
    tp = (df["high"] + df["low"] + df["close"]) / 3
    mf = tp * df["volume"]
    pos_mf = mf.where(tp > tp.shift(), 0).rolling(period).sum()
    neg_mf = mf.where(tp <= tp.shift(), 0).rolling(period).sum()
    mfi = 100 - 100 / (1 + pos_mf / (neg_mf + 1e-10))
    return mfi.iloc[-1] if len(mfi) > 0 else 50.0


def compute_volume_ratio(df: pd.DataFrame, period: int = 20) -> float:
    """Son mum hacmi / ortalama hacim."""
    avg_vol = df["volume"].rolling(period).mean().iloc[-1]
    return df["volume"].iloc[-1] / (avg_vol + 1e-10)


def compute_cci(df: pd.DataFrame, period: int = 20) -> float:
    tp = (df["high"] + df["low"] + df["close"]) / 3
    sma = tp.rolling(period).mean()
    mad = tp.rolling(period).apply(lambda x: np.abs(x - x.mean()).mean(), raw=True)
    cci = (tp - sma) / (0.015 * mad + 1e-10)
    return cci.iloc[-1] if len(cci) > 0 else 0.0


def compute_stochastic(df: pd.DataFrame, k_period=14, d_period=3):
    low_min = df["low"].rolling(k_period).min()
    high_max = df["high"].rolling(k_period).max()
    k = 100 * (df["close"] - low_min) / (high_max - low_min + 1e-10)
    d = k.rolling(d_period).mean()
    return {"stoch_k": k.iloc[-1], "stoch_d": d.iloc[-1]}


def compute_efficiency_ratio(close: pd.Series, period: int = 10) -> float:
    if len(close) < period + 1:
        return 0.5
    direction = abs(close.iloc[-1] - close.iloc[-period])
    volatility = close.diff().abs().iloc[-period:].sum()
    return direction / (volatility + 1e-10)


def compute_hurst(close: pd.Series, max_lag: int = 20) -> float:
    if len(close) < max_lag + 5:
        return 0.5
    vals = close.values
    lags = range(2, min(max_lag, len(vals) // 2))
    try:
        tau = [np.std(np.subtract(vals[lag:], vals[:-lag])) for lag in lags]
        tau = [t for t in tau if t > 0]
        if len(tau) < 3:
            return 0.5
        reg = np.polyfit(np.log(list(lags)[:len(tau)]), np.log(tau), 1)
        return reg[0]
    except:
        return 0.5


def compute_supertrend(df: pd.DataFrame, period: int = 10, multiplier: float = 3.0):
    atr = compute_atr(df, period)
    hl2 = (df["high"] + df["low"]) / 2
    upper_band = hl2 + multiplier * atr
    lower_band = hl2 - multiplier * atr
    close = df["close"].iloc[-1]
    # Simplified: trend direction based on close vs bands
    if close > upper_band.iloc[-1] if isinstance(upper_band, pd.Series) else close > upper_band:
        return {"supertrend": "UP", "st_value": lower_band if isinstance(lower_band, float) else lower_band.iloc[-1]}
    else:
        return {"supertrend": "DOWN", "st_value": upper_band if isinstance(upper_band, float) else upper_band.iloc[-1]}


def compute_all_indicators(df: pd.DataFrame) -> dict:
    """DataFrame'den tüm indikatörleri hesapla."""
    if df.empty or len(df) < 30:
        return None

    close = df["close"]
    price = close.iloc[-1]

    rsi = compute_rsi(close, 14)
    adx = compute_adx(df, 14)
    macd = compute_macd(close, 12, 26, 9)
    bb = compute_bollinger(close, 20, 2.0)
    atr = compute_atr(df, 14)
    obv = compute_obv(df)
    mfi = compute_mfi(df, 14)
    vol_ratio = compute_volume_ratio(df, 20)
    cci = compute_cci(df, 20)
    stoch = compute_stochastic(df, 14, 3)
    er = compute_efficiency_ratio(close, 10)
    hurst = compute_hurst(close, 20)

    ema9 = compute_ema(close, 9)
    ema21 = compute_ema(close, 21)
    ema50 = compute_ema(close, 50)

    return {
        "price": price,
        "rsi": rsi,
        "adx": adx,
        "macd_histogram": macd["histogram"],
        "macd_hist_prev": macd["hist_prev"],
        "macd_cross_up": macd["macd_cross_up"],
        "macd_cross_down": macd["macd_cross_down"],
        "bb_width": bb["bb_width"],
        "bb_position": bb["bb_position"],
        "atr": atr,
        "atr_pct": (atr / price * 100) if price > 0 else 0,
        "obv_above_sma": obv["obv_above_sma"],
        "mfi": mfi,
        "volume_ratio": vol_ratio,
        "cci": cci,
        "stoch_k": stoch["stoch_k"],
        "stoch_d": stoch["stoch_d"],
        "er": er,
        "hurst": hurst,
        "ema9": ema9,
        "ema21": ema21,
        "ema50": ema50,
        "ema9_above_21": ema9 > ema21,
        "ema21_above_50": ema21 > ema50,
        "price_above_ema50": price > ema50,
        # Trend alignment
        "trend_aligned": (ema9 > ema21 > ema50) or (ema9 < ema21 < ema50),
        "bullish_aligned": ema9 > ema21 > ema50,
        "bearish_aligned": ema9 < ema21 < ema50,
    }


# ─── Veritabanı ────────────────────────────────────────────────

def get_system_n_trades():
    """System N trade'lerini veritabanından çek."""
    conn = sqlite3.connect("data/crypthos.db")
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("""
        SELECT * FROM trades
        WHERE entry_regime LIKE 'SYNCED%'
          AND side != 'unknown'
          AND side != ''
          AND entry_price > 0
        ORDER BY open_time DESC
    """)
    trades = [dict(r) for r in cur.fetchall()]
    conn.close()
    return trades


def get_external_system_n_trades():
    """external_close trades that are likely System N (match by symbol+time with system_n orders)."""
    conn = sqlite3.connect("data/crypthos.db")
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    # Get external_close trades near system_n orders
    cur.execute("""
        SELECT t.* FROM trades t
        WHERE t.exit_reason = 'external_close'
          AND t.side = 'unknown'
          AND EXISTS (
              SELECT 1 FROM orders o
              WHERE o.trigger_source LIKE 'system_n%'
                AND o.symbol = t.symbol
                AND ABS(julianday(o.timestamp) - julianday(t.close_time)) < 0.01
          )
    """)
    trades = [dict(r) for r in cur.fetchall()]
    conn.close()
    return trades


# ─── TF => ms dönüşüm ──────────────────────────────────────────

TF_MS = {
    "1m": 60_000, "3m": 180_000, "5m": 300_000, "15m": 900_000,
    "30m": 1_800_000, "1h": 3_600_000, "2h": 7_200_000, "4h": 14_400_000,
}


# ─── ANA ANALİZ ────────────────────────────────────────────────

def main():
    print("=" * 80)
    print("  SYSTEM N TRADE ANALİZİ — İndikatör Pattern Keşfi")
    print("=" * 80)

    trades = get_system_n_trades()
    print(f"\nToplam System N trade: {len(trades)}")

    # Sonuçları toplayacağımız listeler
    winners = []
    losers = []
    all_results = []
    errors = 0
    skipped = 0

    # Her trade için kline çek ve indikatörleri hesapla
    seen_symbols = set()  # Rate limit kontrolü
    total = len(trades)

    for i, trade in enumerate(trades):
        symbol = trade["symbol"]
        tf = trade["timeframe"] or "5m"
        side = trade["side"]
        pnl = trade["pnl_usdt"]

        # open_time'ı ms'ye çevir
        try:
            ot = datetime.fromisoformat(trade["open_time"])
            open_ms = int(ot.timestamp() * 1000)
        except:
            skipped += 1
            continue

        print(f"\r  [{i+1}/{total}] {symbol} {side} {tf} PnL={pnl:+.4f}", end="", flush=True)

        # Giriş anındaki kline'ları çek (giriş zamanından geriye)
        try:
            df = get_klines(symbol, tf, limit=100, end_time=open_ms)
            if df.empty or len(df) < 30:
                skipped += 1
                continue
        except Exception as e:
            errors += 1
            continue

        # İndikatörleri hesapla
        indicators = compute_all_indicators(df)
        if indicators is None:
            skipped += 1
            continue

        result = {
            "symbol": symbol,
            "side": side,
            "pnl_usdt": pnl,
            "pnl_pct": trade["pnl_percent"],
            "roi_pct": trade["roi_percent"],
            "leverage": trade["leverage"],
            "exit_reason": trade["exit_reason"],
            "regime": trade["entry_regime"],
            "hold_seconds": trade["hold_seconds"],
            "timeframe": tf,
            "db_adx": trade["entry_adx"],
            "db_rsi": trade["entry_rsi"],
            **indicators
        }

        all_results.append(result)
        if pnl > 0:
            winners.append(result)
        else:
            losers.append(result)

        # Rate limiting: 100ms arası
        time.sleep(0.08)

    print(f"\n\nAnaliz tamamlandı: {len(all_results)} trade ({len(winners)} win, {len(losers)} loss)")
    print(f"  Atlanan: {skipped}, Hata: {errors}")

    if not all_results:
        print("Yeterli veri yok!")
        return

    # ═══════════════════════════════════════════════════════════
    #  ANALİZ RAPORU
    # ═══════════════════════════════════════════════════════════

    wdf = pd.DataFrame(winners) if winners else pd.DataFrame()
    ldf = pd.DataFrame(losers) if losers else pd.DataFrame()
    adf = pd.DataFrame(all_results)

    print("\n" + "=" * 80)
    print("  1. GENEL İSTATİSTİKLER")
    print("=" * 80)
    print(f"  Win Rate: {len(winners)}/{len(all_results)} = {len(winners)/len(all_results)*100:.1f}%")
    print(f"  Net PnL: {adf['pnl_usdt'].sum():.4f} USDT")
    print(f"  Avg Win: {wdf['pnl_usdt'].mean():.4f} USDT" if len(wdf) else "  No wins")
    print(f"  Avg Loss: {ldf['pnl_usdt'].mean():.4f} USDT" if len(ldf) else "  No losses")
    print(f"  Avg Hold (Win): {wdf['hold_seconds'].mean():.0f}s" if len(wdf) else "")
    print(f"  Avg Hold (Loss): {ldf['hold_seconds'].mean():.0f}s" if len(ldf) else "")

    print("\n" + "=" * 80)
    print("  2. İNDİKATÖR KARŞILAŞTIRMASI (WINNERS vs LOSERS)")
    print("=" * 80)

    indicators_to_compare = [
        ("rsi", "RSI(14)"),
        ("adx", "ADX(14)"),
        ("mfi", "MFI(14)"),
        ("macd_histogram", "MACD Histogram"),
        ("bb_width", "BB Width %"),
        ("bb_position", "BB Position (0-1)"),
        ("atr_pct", "ATR %"),
        ("volume_ratio", "Volume Ratio"),
        ("cci", "CCI(20)"),
        ("stoch_k", "Stochastic %K"),
        ("stoch_d", "Stochastic %D"),
        ("er", "Efficiency Ratio"),
        ("hurst", "Hurst Exponent"),
    ]

    print(f"\n  {'İndikatör':<22} {'WIN Ort':>10} {'WIN Med':>10} {'LOSS Ort':>10} {'LOSS Med':>10} {'Fark':>10}")
    print("  " + "-" * 72)
    for key, name in indicators_to_compare:
        if key in adf.columns:
            w_mean = wdf[key].mean() if len(wdf) else 0
            w_med = wdf[key].median() if len(wdf) else 0
            l_mean = ldf[key].mean() if len(ldf) else 0
            l_med = ldf[key].median() if len(ldf) else 0
            diff = w_mean - l_mean
            marker = " *" if abs(diff) > 0.1 * max(abs(w_mean), abs(l_mean), 1) else ""
            print(f"  {name:<22} {w_mean:>10.3f} {w_med:>10.3f} {l_mean:>10.3f} {l_med:>10.3f} {diff:>+10.3f}{marker}")

    # Boolean karşılaştırmalar
    bool_indicators = [
        ("ema9_above_21", "EMA9 > EMA21"),
        ("ema21_above_50", "EMA21 > EMA50"),
        ("price_above_ema50", "Price > EMA50"),
        ("trend_aligned", "Trend Aligned"),
        ("obv_above_sma", "OBV > SMA20"),
        ("macd_cross_up", "MACD Cross Up"),
        ("macd_cross_down", "MACD Cross Down"),
    ]

    print(f"\n  {'Boolean İndikatör':<22} {'WIN %':>10} {'LOSS %':>10} {'Fark':>10}")
    print("  " + "-" * 52)
    for key, name in bool_indicators:
        if key in adf.columns:
            w_pct = wdf[key].mean() * 100 if len(wdf) else 0
            l_pct = ldf[key].mean() * 100 if len(ldf) else 0
            diff = w_pct - l_pct
            marker = " *" if abs(diff) > 10 else ""
            print(f"  {name:<22} {w_pct:>9.1f}% {l_pct:>9.1f}% {diff:>+9.1f}%{marker}")

    # ═══════════════════════════════════════════════════════════
    #  3. LONG vs SHORT AYRI ANALİZ
    # ═══════════════════════════════════════════════════════════
    print("\n" + "=" * 80)
    print("  3. LONG vs SHORT AYRI ANALİZ")
    print("=" * 80)

    for direction in ["Buy/Long", "Sell/Short"]:
        dir_df = adf[adf["side"] == direction]
        dir_w = dir_df[dir_df["pnl_usdt"] > 0]
        dir_l = dir_df[dir_df["pnl_usdt"] <= 0]
        if len(dir_df) == 0:
            continue

        print(f"\n  --- {direction} ({len(dir_df)} trade, WR: {len(dir_w)/len(dir_df)*100:.1f}%) ---")
        for key, name in indicators_to_compare[:8]:
            if key in dir_df.columns:
                w_m = dir_w[key].mean() if len(dir_w) else 0
                l_m = dir_l[key].mean() if len(dir_l) else 0
                diff = w_m - l_m
                marker = " *" if abs(diff) > 0.1 * max(abs(w_m), abs(l_m), 1) else ""
                print(f"    {name:<20} Win:{w_m:>8.3f}  Loss:{l_m:>8.3f}  D={diff:>+8.3f}{marker}")

        # LONG için: RSI, BB position, MACD alignment
        if direction == "Buy/Long":
            print(f"\n    LONG Spesifik:")
            if len(dir_w):
                print(f"      Win trades: RSI={dir_w['rsi'].mean():.1f}, BB_Pos={dir_w['bb_position'].mean():.3f}, Bullish_EMA%={dir_w['bullish_aligned'].mean()*100:.1f}%")
            if len(dir_l):
                print(f"      Loss trades: RSI={dir_l['rsi'].mean():.1f}, BB_Pos={dir_l['bb_position'].mean():.3f}, Bullish_EMA%={dir_l['bullish_aligned'].mean()*100:.1f}%")
        else:
            print(f"\n    SHORT Spesifik:")
            if len(dir_w):
                print(f"      Win trades: RSI={dir_w['rsi'].mean():.1f}, BB_Pos={dir_w['bb_position'].mean():.3f}, Bearish_EMA%={dir_w['bearish_aligned'].mean()*100:.1f}%")
            if len(dir_l):
                print(f"      Loss trades: RSI={dir_l['rsi'].mean():.1f}, BB_Pos={dir_l['bb_position'].mean():.3f}, Bearish_EMA%={dir_l['bearish_aligned'].mean()*100:.1f}%")

    # ═══════════════════════════════════════════════════════════
    #  4. REJİM BAZLI ANALİZ
    # ═══════════════════════════════════════════════════════════
    print("\n" + "=" * 80)
    print("  4. REJİM BAZLI ANALİZ")
    print("=" * 80)

    for regime in adf["regime"].unique():
        rdf = adf[adf["regime"] == regime]
        rw = rdf[rdf["pnl_usdt"] > 0]
        rl = rdf[rdf["pnl_usdt"] <= 0]
        if len(rdf) < 3:
            continue
        print(f"\n  {regime}: {len(rdf)} trade, WR={len(rw)/len(rdf)*100:.1f}%, Net={rdf['pnl_usdt'].sum():.4f}")
        for key, name in [("rsi", "RSI"), ("adx", "ADX"), ("er", "ER"), ("hurst", "Hurst"), ("volume_ratio", "VolRatio"), ("bb_position", "BB_Pos")]:
            if key in rdf.columns:
                w_m = rw[key].mean() if len(rw) else 0
                l_m = rl[key].mean() if len(rl) else 0
                print(f"    {name:<10} Win:{w_m:>8.3f}  Loss:{l_m:>8.3f}")

    # ═══════════════════════════════════════════════════════════
    #  5. EXIT REASON ANALİZİ
    # ═══════════════════════════════════════════════════════════
    print("\n" + "=" * 80)
    print("  5. EXIT REASON ANALİZİ — Giriş Anı İndikatörleri")
    print("=" * 80)

    for reason in adf["exit_reason"].unique():
        rdf = adf[adf["exit_reason"] == reason]
        if len(rdf) < 2:
            continue
        rw = rdf[rdf["pnl_usdt"] > 0]
        rl = rdf[rdf["pnl_usdt"] <= 0]
        print(f"\n  {reason}: {len(rdf)} trade, Win:{len(rw)} Loss:{len(rl)}, Net={rdf['pnl_usdt'].sum():.4f}")
        print(f"    Giriş RSI={rdf['rsi'].mean():.1f}, ADX={rdf['adx'].mean():.1f}, ER={rdf['er'].mean():.3f}, Hurst={rdf['hurst'].mean():.3f}")
        print(f"    BB_Pos={rdf['bb_position'].mean():.3f}, VolRatio={rdf['volume_ratio'].mean():.2f}, MFI={rdf['mfi'].mean():.1f}")

    # ═══════════════════════════════════════════════════════════
    #  6. FİLTRE ÖNERİLERİ (Potansiyel Kayıp Engelleyiciler)
    # ═══════════════════════════════════════════════════════════
    print("\n" + "=" * 80)
    print("  6. FİLTRE ÖNERİLERİ — Zarar Azaltma Simülasyonu")
    print("=" * 80)

    filters = [
        ("RSI 40-60 filtresi (LONG: RSI>40, SHORT: RSI<60)",
         lambda r: (r["side"] == "Buy/Long" and r["rsi"] > 40) or (r["side"] == "Sell/Short" and r["rsi"] < 60)),
        ("RSI 35-65 filtresi (LONG: RSI>35, SHORT: RSI<65)",
         lambda r: (r["side"] == "Buy/Long" and r["rsi"] > 35) or (r["side"] == "Sell/Short" and r["rsi"] < 65)),
        ("ADX > 20 filtresi",
         lambda r: r["adx"] > 20),
        ("ADX > 25 filtresi",
         lambda r: r["adx"] > 25),
        ("ADX < 35 filtresi (aşırı trend filtresi)",
         lambda r: r["adx"] < 35),
        ("Volume Ratio > 0.8 filtresi",
         lambda r: r["volume_ratio"] > 0.8),
        ("Volume Ratio > 1.0 filtresi",
         lambda r: r["volume_ratio"] > 1.0),
        ("ER > 0.3 filtresi (mean reversion eleme)",
         lambda r: r["er"] > 0.3),
        ("ER > 0.2 filtresi",
         lambda r: r["er"] > 0.2),
        ("Hurst < 0.6 filtresi (aşırı trending eleme)",
         lambda r: r["hurst"] < 0.6),
        ("BB Position 0.2-0.8 (band kenarları eleme)",
         lambda r: 0.2 < r["bb_position"] < 0.8),
        ("MACD histogram yön uyumu (LONG: hist>0, SHORT: hist<0)",
         lambda r: (r["side"] == "Buy/Long" and r["macd_histogram"] > 0) or (r["side"] == "Sell/Short" and r["macd_histogram"] < 0)),
        ("EMA trend alignment (9>21>50 veya 9<21<50)",
         lambda r: r["trend_aligned"]),
        ("LONG: Bullish EMA + RSI>40 | SHORT: Bearish EMA + RSI<60",
         lambda r: (r["side"] == "Buy/Long" and r["bullish_aligned"] and r["rsi"] > 40) or
                   (r["side"] == "Sell/Short" and r["bearish_aligned"] and r["rsi"] < 60)),
        ("MFI 30-70 filtresi (aşırı alım/satım eleme)",
         lambda r: 30 < r["mfi"] < 70),
        ("Stochastic %K 20-80",
         lambda r: 20 < r["stoch_k"] < 80),
        ("CCI -100 to +100",
         lambda r: -100 < r["cci"] < 100),
        ("OBV yön uyumu (OBV > SMA)",
         lambda r: (r["side"] == "Buy/Long" and r["obv_above_sma"]) or (r["side"] == "Sell/Short" and not r["obv_above_sma"])),
        # Kombine filtreler
        ("COMBO: ADX>20 + MACD uyumu + ER>0.2",
         lambda r: r["adx"] > 20 and
                   ((r["side"] == "Buy/Long" and r["macd_histogram"] > 0) or (r["side"] == "Sell/Short" and r["macd_histogram"] < 0)) and
                   r["er"] > 0.2),
        ("COMBO: EMA aligned + Volume>0.8 + ADX>18",
         lambda r: r["trend_aligned"] and r["volume_ratio"] > 0.8 and r["adx"] > 18),
        ("COMBO: MACD uyumu + RSI uyumu + ER>0.2",
         lambda r: ((r["side"] == "Buy/Long" and r["macd_histogram"] > 0 and r["rsi"] > 40) or
                    (r["side"] == "Sell/Short" and r["macd_histogram"] < 0 and r["rsi"] < 60)) and
                   r["er"] > 0.2),
    ]

    print(f"\n  {'Filtre':<55} {'Geçen':>6} {'Win':>5} {'Loss':>5} {'WR%':>7} {'Net PnL':>10} {'Elenen Loss':>11}")
    print("  " + "-" * 99)

    original_wr = len(winners) / len(all_results) * 100
    original_net = adf["pnl_usdt"].sum()

    for name, fn in filters:
        passed = [r for r in all_results if fn(r)]
        filtered = [r for r in all_results if not fn(r)]
        p_wins = sum(1 for r in passed if r["pnl_usdt"] > 0)
        p_losses = sum(1 for r in passed if r["pnl_usdt"] <= 0)
        p_net = sum(r["pnl_usdt"] for r in passed)
        f_losses = sum(1 for r in filtered if r["pnl_usdt"] <= 0)
        f_wins = sum(1 for r in filtered if r["pnl_usdt"] > 0)
        wr = p_wins / len(passed) * 100 if passed else 0
        marker = " OK" if wr > original_wr + 3 and p_net > original_net * 0.8 else ""
        print(f"  {name:<55} {len(passed):>6} {p_wins:>5} {p_losses:>5} {wr:>6.1f}% {p_net:>+10.4f} {f_losses:>5}(-{f_wins}w){marker}")

    # ═══════════════════════════════════════════════════════════
    #  7. EN KÖTÜ TRADE'LER — DETAYLI İNCELEME
    # ═══════════════════════════════════════════════════════════
    print("\n" + "=" * 80)
    print("  7. EN KÖTÜ 15 TRADE — Giriş Anı İndikatör Detayı")
    print("=" * 80)

    sorted_losses = sorted(all_results, key=lambda x: x["pnl_usdt"])[:15]
    for i, t in enumerate(sorted_losses):
        print(f"\n  #{i+1} {t['symbol']} {t['side']} | PnL: {t['pnl_usdt']:+.4f} ({t['roi_pct']:+.1f}% ROI) | {t['exit_reason']} | {t['regime']}")
        print(f"       Lev: {t['leverage']}x | Hold: {t['hold_seconds']:.0f}s | TF: {t['timeframe']}")
        print(f"       RSI: {t['rsi']:.1f} | ADX: {t['adx']:.1f} | MFI: {t['mfi']:.1f} | CCI: {t['cci']:.1f}")
        print(f"       MACD Hist: {t['macd_histogram']:.6f} | BB Pos: {t['bb_position']:.3f} | BB Width: {t['bb_width']:.3f}")
        print(f"       ER: {t['er']:.3f} | Hurst: {t['hurst']:.3f} | Vol Ratio: {t['volume_ratio']:.2f}")
        print(f"       EMA9>21: {t['ema9_above_21']} | EMA21>50: {t['ema21_above_50']} | Trend Aligned: {t['trend_aligned']}")
        print(f"       Stoch K/D: {t['stoch_k']:.1f}/{t['stoch_d']:.1f} | OBV>SMA: {t['obv_above_sma']}")

    # ═══════════════════════════════════════════════════════════
    #  8. EN İYİ 10 TRADE — Neyi Doğru Yaptık?
    # ═══════════════════════════════════════════════════════════
    print("\n" + "=" * 80)
    print("  8. EN İYİ 10 TRADE — Neyi Doğru Yaptık?")
    print("=" * 80)

    sorted_wins = sorted(all_results, key=lambda x: x["pnl_usdt"], reverse=True)[:10]
    for i, t in enumerate(sorted_wins):
        print(f"\n  #{i+1} {t['symbol']} {t['side']} | PnL: {t['pnl_usdt']:+.4f} ({t['roi_pct']:+.1f}% ROI) | {t['exit_reason']} | {t['regime']}")
        print(f"       RSI: {t['rsi']:.1f} | ADX: {t['adx']:.1f} | MFI: {t['mfi']:.1f} | ER: {t['er']:.3f} | Hurst: {t['hurst']:.3f}")
        print(f"       MACD Hist: {t['macd_histogram']:.6f} | BB Pos: {t['bb_position']:.3f} | Vol Ratio: {t['volume_ratio']:.2f}")
        print(f"       EMA Aligned: {t['trend_aligned']} | OBV>SMA: {t['obv_above_sma']}")

    # ═══════════════════════════════════════════════════════════
    #  9. PATTERN ÖZET VE TAVSİYELER
    # ═══════════════════════════════════════════════════════════
    print("\n" + "=" * 80)
    print("  9. PATTERN ÖZET VE TAVSİYELER")
    print("=" * 80)

    print("\n  Tespit edilen kalıplar:")

    # RSI pattern
    if len(wdf) and len(ldf):
        rsi_diff = wdf["rsi"].mean() - ldf["rsi"].mean()
        if abs(rsi_diff) > 3:
            print(f"  => RSI: Winner'lar ortalama {wdf['rsi'].mean():.1f}, Loser'lar {ldf['rsi'].mean():.1f} (D={rsi_diff:+.1f})")

        adx_diff = wdf["adx"].mean() - ldf["adx"].mean()
        if abs(adx_diff) > 2:
            print(f"  => ADX: Winner'lar ortalama {wdf['adx'].mean():.1f}, Loser'lar {ldf['adx'].mean():.1f} (D={adx_diff:+.1f})")

        er_diff = wdf["er"].mean() - ldf["er"].mean()
        if abs(er_diff) > 0.03:
            print(f"  => ER: Winner'lar ortalama {wdf['er'].mean():.3f}, Loser'lar {ldf['er'].mean():.3f} (D={er_diff:+.3f})")

        vol_diff = wdf["volume_ratio"].mean() - ldf["volume_ratio"].mean()
        if abs(vol_diff) > 0.1:
            print(f"  => Volume: Winner'lar ortalama {wdf['volume_ratio'].mean():.2f}x, Loser'lar {ldf['volume_ratio'].mean():.2f}x (D={vol_diff:+.2f})")

        macd_w_aligned = sum(1 for r in winners if (r["side"] == "Buy/Long" and r["macd_histogram"] > 0) or (r["side"] == "Sell/Short" and r["macd_histogram"] < 0)) / len(winners) * 100
        macd_l_aligned = sum(1 for r in losers if (r["side"] == "Buy/Long" and r["macd_histogram"] > 0) or (r["side"] == "Sell/Short" and r["macd_histogram"] < 0)) / len(losers) * 100
        if abs(macd_w_aligned - macd_l_aligned) > 5:
            print(f"  => MACD Uyumu: Winner'lar %{macd_w_aligned:.1f}, Loser'lar %{macd_l_aligned:.1f} (D={macd_w_aligned - macd_l_aligned:+.1f}%)")

        ema_w_aligned = wdf["trend_aligned"].mean() * 100
        ema_l_aligned = ldf["trend_aligned"].mean() * 100
        if abs(ema_w_aligned - ema_l_aligned) > 5:
            print(f"  => EMA Trend: Winner'lar %{ema_w_aligned:.1f}, Loser'lar %{ema_l_aligned:.1f} (D={ema_w_aligned - ema_l_aligned:+.1f}%)")

        hurst_diff = wdf["hurst"].mean() - ldf["hurst"].mean()
        if abs(hurst_diff) > 0.03:
            print(f"  => Hurst: Winner'lar {wdf['hurst'].mean():.3f}, Loser'lar {ldf['hurst'].mean():.3f} (D={hurst_diff:+.3f})")

    # SYNCED:BREAKOUT ve SYNCED:RANGING uyarısı
    breakout_df = adf[adf["regime"] == "SYNCED:BREAKOUT"]
    ranging_df = adf[adf["regime"] == "SYNCED:RANGING"]
    if len(breakout_df) > 3:
        bwr = (breakout_df["pnl_usdt"] > 0).mean() * 100
        print(f"\n  ! SYNCED:BREAKOUT rejimi: WR sadece {bwr:.1f}% — bu rejimde filtre sıkılaştırılmalı")
    if len(ranging_df) > 2:
        rwr = (ranging_df["pnl_usdt"] > 0).mean() * 100
        print(f"  ! SYNCED:RANGING rejimi: WR sadece {rwr:.1f}% — bu rejimde giriş kapatılmalı veya çok sıkı filtre")

    # SL olan trade'ler
    sl_df = adf[adf["exit_reason"] == "STOP_LOSS"]
    if len(sl_df) > 3:
        print(f"\n  ! STOP_LOSS çıkışları: {len(sl_df)} trade, tamamı zarar. Giriş ADX={sl_df['adx'].mean():.1f}, ER={sl_df['er'].mean():.3f}")
        print(f"    SL trade'lerde MACD histogram ortalaması: {sl_df['macd_histogram'].mean():.6f}")

    print("\n  ─── Öneriler ───")
    print("  1. En etkili tek filtre: 6. bölümdeki OK işaretli filtrelere bak")
    print("  2. SYNCED:RANGING rejiminde trade açma (çok düşük WR)")
    print("  3. Kombine filtrelerin etkisini 6. bölümden değerlendir")
    print("  4. SL trade'lerin giriş kalitesini iyileştir (ER, MACD uyumu)")

    # JSON export
    export = {
        "summary": {
            "total": len(all_results),
            "wins": len(winners),
            "losses": len(losers),
            "win_rate": len(winners) / len(all_results) * 100,
            "net_pnl": adf["pnl_usdt"].sum(),
        },
        "indicator_means": {
            "winners": {k: float(wdf[k].mean()) for k in ["rsi", "adx", "mfi", "er", "hurst", "volume_ratio", "bb_position", "bb_width", "cci", "stoch_k"] if k in wdf.columns} if len(wdf) else {},
            "losers": {k: float(ldf[k].mean()) for k in ["rsi", "adx", "mfi", "er", "hurst", "volume_ratio", "bb_position", "bb_width", "cci", "stoch_k"] if k in ldf.columns} if len(ldf) else {},
        },
        "trades": all_results,
    }

    with open("data/system_n_analysis.json", "w") as f:
        json.dump(export, f, indent=2, default=str)
    print(f"\n  Detaylı sonuçlar: data/system_n_analysis.json")


if __name__ == "__main__":
    main()
