"""System N Backtest — AlphaTrend parametre optimizasyonu.

Her TF (1m, 3m, 5m) için en iyi coeff ve period kombinasyonunu bulur.
Aynı zamanda G dalga analizi yaparak kaldıraç potansiyelini hesaplar.

Kullanım:
    python backtest_system_n.py                    # Top 20 coin, tüm TF'ler
    python backtest_system_n.py --symbols BTCUSDT ETHUSDT  # Belirli coinler
    python backtest_system_n.py --tf 1m            # Sadece 1m
    python backtest_system_n.py --fast              # Hızlı mod (az kombinasyon)

Çıktı:
    - Konsola TF bazlı en iyi parametreler
    - data/system_n_optimize.json → canlı sistemin okuyacağı cache dosyası
"""

import sys
import os
import json
import time
import argparse
from pathlib import Path
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from loguru import logger

# Windows encoding fix
import io, sys as _sys
if hasattr(_sys.stdout, 'buffer'):
    _sys.stdout = io.TextIOWrapper(_sys.stdout.buffer, encoding='utf-8', errors='replace')
    _sys.stderr = io.TextIOWrapper(_sys.stderr.buffer, encoding='utf-8', errors='replace')

# Project imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from market.binance_rest import BinanceRestClient
from scanner.system_n_scanner import compute_alpha_trend, _compute_adx, _compute_rsi, _compute_mfi
from scanner.system_b_scanner import (
    detect_zigzag_swings,
    analyze_waves,
    compute_rolling_er,
    compute_hurst_exponent,
)


# ═══════════════════════════════════════════════════════════════════
#  Config
# ═══════════════════════════════════════════════════════════════════

TIMEFRAMES = ["1m", "3m", "5m", "15m", "30m", "1h"]
TF_KLINE_LIMITS = {"1m": 1500, "3m": 1500, "5m": 1500,
                   "15m": 1500, "30m": 1500, "1h": 1500}

# Sayfalama ile toplam mum hedefi (TF başına)
# 1m: 10000 mum = ~7 gün, 3m: 8000 mum = ~17 gün, 5m: 6000 mum = ~21 gün
# 15m: 4000 mum = ~42 gün, 30m: 3000 mum = ~63 gün, 1h: 2000 mum = ~83 gün
TF_TARGET_BARS = {"1m": 10000, "3m": 8000, "5m": 6000,
                  "15m": 4000, "30m": 3000, "1h": 2000}
TF_MS = {"1m": 60_000, "3m": 180_000, "5m": 300_000,
         "15m": 900_000, "30m": 1_800_000, "1h": 3_600_000}

# AlphaTrend parametre arama alanı
COEFF_RANGE_FULL = [2.0, 2.5, 3.0, 3.5, 3.6, 4.0, 4.5, 5.0, 5.5, 6.0]
PERIOD_RANGE_FULL = [10, 14, 18, 21, 27, 30, 35, 40, 50]

COEFF_RANGE_FAST = [3.0, 3.6, 4.5, 5.5]
PERIOD_RANGE_FAST = [14, 21, 27, 35, 50]

# Backtest sliding window
WARMUP_BARS = 100       # AlphaTrend warmup için minimum mum
MIN_TRADES = 5          # Geçerli sonuç için minimum trade sayısı
FEE_PER_TRADE = 0.12    # round-trip: 2×%0.04 taker fee + 2×%0.02 slippage (giriş+çıkış toplam)

# G dalga analizi
ZIGZAG_N = 5            # 1m/3m için kısa N (hızlı dalgalar)
MIN_WAVES = 4           # Minimum dalga sayısı

# Leverage hesaplama (System J formülü)
SL_G_MULT = 1.5
FEE_TOTAL = 0.12        # round-trip: 2×%0.04 taker + 2×%0.02 slippage
SL_DIVISOR = 2.0
DEFAULT_MAINT_RATE = 0.004  # %0.4 (Binance çoğu coin)

# Top coinler (varsayılan)
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


@dataclass
class BacktestResult:
    symbol: str
    timeframe: str
    coeff: float
    period: int
    total_trades: int
    wins: int
    losses: int
    win_rate: float
    avg_pnl_pct: float
    total_pnl_pct: float
    max_drawdown_pct: float
    avg_hold_bars: float
    profit_factor: float
    leveraged_max_dd_pct: float = 0.0   # kaldıraç uygulanmış max drawdown
    liquidation_risk: bool = False       # leveraged DD > %80 → likidasyon tehlikesi
    trades: list = field(default_factory=list, repr=False)


@dataclass
class GAnalysis:
    symbol: str
    timeframe: str
    G: float                # Geri dalga ortalaması (%)
    I: float                # İleri dalga ortalaması (%)
    wave_count: int
    regime: str             # TRENDING / RANGING / GRAY
    er: float
    hurst: float
    max_leverage: int
    sl_pct: float


# ═══════════════════════════════════════════════════════════════════
#  AlphaTrend Backtest Engine
# ═══════════════════════════════════════════════════════════════════

def run_backtest(closes: np.ndarray, highs: np.ndarray, lows: np.ndarray,
                 volumes: np.ndarray, coeff: float, period: int,
                 adx_length: int = 14, adx_threshold: float = 18.0,
                 use_mfi: bool = True) -> list[TradeResult]:
    """AlphaTrend sinyallerini geçmiş veride simüle et.

    Kurallar:
    - BUY crossover → LONG aç (varsa SHORT kapat)
    - SELL crossunder → SHORT aç (varsa LONG kapat)
    - Filtreler: ADX static + dynamic (System M ile aynı)
    """
    n = len(closes)
    if n < max(period * 3, adx_length * 3, WARMUP_BARS):
        return []

    # AlphaTrend hesapla
    alpha_trend, atr_arr = compute_alpha_trend(
        highs, lows, closes, volumes,
        coeff=coeff, period=period, use_mfi=use_mfi,
    )

    # ADX hesapla
    adx_arr, _, _ = _compute_adx(highs, lows, closes, adx_length)

    # ADX SMA (dinamik filtre)
    from scanner.system_n_scanner import _sma
    adx_sma = _sma(adx_arr, adx_length)

    trades = []
    position = None         # None, "LONG", "SHORT"
    entry_idx = 0
    entry_price = 0.0

    # Warmup sonrası başla (en az period*3 + 4 bar gerekli)
    start = max(period * 3, WARMUP_BARS, 4)

    for i in range(start, n):
        at_now = alpha_trend[i]
        at_1 = alpha_trend[i - 1]
        at_2 = alpha_trend[i - 2]
        at_3 = alpha_trend[i - 3]

        if any(np.isnan(v) for v in [at_now, at_1, at_2, at_3]):
            continue

        # ADX filtreler
        adx_val = adx_arr[i] if not np.isnan(adx_arr[i]) else 0.0
        adx_static_ok = adx_val > adx_threshold

        adx_dyn_val = adx_sma[i] if not np.isnan(adx_sma[i]) else 0.0
        adx_dynamic_ok = adx_val > adx_dyn_val

        final_filter = adx_static_ok and adx_dynamic_ok

        # Crossover / crossunder
        buy_cross = (at_now > at_2) and (at_1 <= at_3) and final_filter
        sell_cross = (at_now < at_2) and (at_1 >= at_3) and final_filter

        # Trade logic (reverse mode — System M ile aynı)
        # Aynı yönde tekrar sinyal gelirse atla (entry reset bug önleme)
        if buy_cross and position != "LONG":
            # Varsa SHORT kapat
            if position == "SHORT":
                pnl = (entry_price - closes[i]) / entry_price * 100 - FEE_PER_TRADE
                trades.append(TradeResult(
                    entry_idx=entry_idx, exit_idx=i,
                    direction="SHORT", entry_price=entry_price,
                    exit_price=closes[i], pnl_pct=pnl,
                    hold_bars=i - entry_idx,
                ))
            # LONG aç
            position = "LONG"
            entry_idx = i
            entry_price = closes[i]

        elif sell_cross and position != "SHORT":
            # Varsa LONG kapat
            if position == "LONG":
                pnl = (closes[i] - entry_price) / entry_price * 100 - FEE_PER_TRADE
                trades.append(TradeResult(
                    entry_idx=entry_idx, exit_idx=i,
                    direction="LONG", entry_price=entry_price,
                    exit_price=closes[i], pnl_pct=pnl,
                    hold_bars=i - entry_idx,
                ))
            # SHORT aç
            position = "SHORT"
            entry_idx = i
            entry_price = closes[i]

    # Son açık pozisyon: kapatmıyoruz, bias önleme
    # (backtest dönemi sonundaki açık pozisyon istatistiklere dahil edilmez)

    return trades


def evaluate_trades(trades: list[TradeResult], symbol: str, tf: str,
                    coeff: float, period: int,
                    expected_leverage: int = 1) -> BacktestResult | None:
    """Trade listesinden istatistik çıkar.

    Args:
        expected_leverage: G-bazlı beklenen kaldıraç. Kaldıraçlı drawdown
            hesaplanır ve likidasyon riski flag'lenir.
    """
    if len(trades) < MIN_TRADES:
        return None

    pnls = [t.pnl_pct for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]

    total_pnl = sum(pnls)
    win_rate = len(wins) / len(pnls) * 100 if pnls else 0
    avg_pnl = np.mean(pnls) if pnls else 0

    # Max drawdown (kümülatif PnL üzerinden)
    cum_pnl = np.cumsum(pnls)
    peak = np.maximum.accumulate(cum_pnl)
    drawdowns = peak - cum_pnl
    max_dd = float(np.max(drawdowns)) if len(drawdowns) > 0 else 0

    # Kaldıraç-aware drawdown: gerçek ROI kaybı
    lev = max(expected_leverage, 1)
    leveraged_dd = max_dd * lev
    liq_risk = leveraged_dd > 80.0  # %80 ROI kaybı → likidasyon tehlikesi

    # Profit factor
    gross_profit = sum(wins) if wins else 0
    gross_loss = abs(sum(losses)) if losses else 0.001
    pf = gross_profit / gross_loss

    return BacktestResult(
        symbol=symbol, timeframe=tf, coeff=coeff, period=period,
        total_trades=len(trades),
        wins=len(wins), losses=len(losses),
        win_rate=win_rate,
        avg_pnl_pct=float(avg_pnl),
        total_pnl_pct=float(total_pnl),
        max_drawdown_pct=max_dd,
        avg_hold_bars=float(np.mean([t.hold_bars for t in trades])),
        profit_factor=pf,
        leveraged_max_dd_pct=leveraged_dd,
        liquidation_risk=liq_risk,
        trades=trades,
    )


# ═══════════════════════════════════════════════════════════════════
#  G Dalga Analizi + Kaldıraç
# ═══════════════════════════════════════════════════════════════════

def analyze_g_for_tf(closes: np.ndarray, highs: np.ndarray, lows: np.ndarray,
                     symbol: str, tf: str) -> GAnalysis | None:
    """Bir coin+TF için G dalga analizi ve kaldıraç hesabı."""
    if len(closes) < 100:
        return None

    swings = detect_zigzag_swings(highs, lows, n=ZIGZAG_N)
    if len(swings) < MIN_WAVES:
        return None

    wave = analyze_waves(swings, float(closes[-1]))
    G = wave.G
    I = wave.I

    if G <= 0:
        return None

    # Rejim
    er = compute_rolling_er(closes, window=20, median_count=10)
    hurst = compute_hurst_exponent(closes)

    if er > 0.25:
        regime = "TRENDING"
    elif er < 0.08:
        regime = "RANGING"
    elif hurst > 0.55:
        regime = "TRENDING"
    elif hurst < 0.45:
        regime = "RANGING"
    else:
        regime = "GRAY"

    # Kaldıraç: G → SL → Liq → Leverage
    sl_pct = G * SL_G_MULT + FEE_TOTAL
    liq_dist = sl_pct * SL_DIVISOR
    teorik_liq = liq_dist + DEFAULT_MAINT_RATE * 100
    max_lev = int(100.0 / teorik_liq) if teorik_liq > 0 else 1
    max_lev = max(1, min(max_lev, 125))

    wave_count = len(wave.backward_waves) + len(wave.forward_waves)

    return GAnalysis(
        symbol=symbol, timeframe=tf,
        G=G, I=I, wave_count=wave_count,
        regime=regime, er=er, hurst=hurst,
        max_leverage=max_lev, sl_pct=sl_pct,
    )


# ═══════════════════════════════════════════════════════════════════
#  Zoom Dirsek: TF Seçimi
# ═══════════════════════════════════════════════════════════════════

TF_MINUTES = {"1m": 1, "3m": 3, "5m": 5, "15m": 15, "30m": 30, "1h": 60}

def select_optimal_tf(g_results: list[GAnalysis]) -> GAnalysis | None:
    """Zoom dirsek: G/TF verimliliğine göre en iyi TF'yi seç.

    Küçük TF'den büyüğe doğru tarar. G artışı TF artışına
    oranla verimsiz olunca durur.

    efficiency = (G artış oranı) / (TF artış oranı)
      - efficiency > 1.5 → G, TF'den çok daha fazla arttı → çok verimli, devam
      - efficiency 0.8–1.5 → dirsek bölgesi → al ve dur
      - efficiency < 0.8 → verimsiz → öncekinde kal
      - efficiency < 0 → G azaldı → öncekinde kal
    """
    if not g_results:
        return None

    # TF sırasına göre sırala
    tf_order = {"1m": 0, "3m": 1, "5m": 2, "15m": 3, "30m": 4, "1h": 5}
    sorted_g = sorted(g_results, key=lambda x: tf_order.get(x.timeframe, 99))

    if len(sorted_g) == 1:
        return sorted_g[0]

    best = sorted_g[0]  # En küçük TF ile başla

    for i in range(1, len(sorted_g)):
        prev = sorted_g[i - 1]
        curr = sorted_g[i]

        prev_mins = TF_MINUTES.get(prev.timeframe, 1)
        curr_mins = TF_MINUTES.get(curr.timeframe, 1)

        # G artış oranı / TF artış oranı
        if prev.G > 0 and prev_mins > 0:
            g_ratio = (curr.G - prev.G) / prev.G
            tf_ratio = (curr_mins - prev_mins) / prev_mins
            efficiency = g_ratio / tf_ratio if tf_ratio > 0 else 999
        else:
            efficiency = 999

        if efficiency < 0:
            # G azaldı — daha büyük TF daha dar dalga → öncekinde kal
            break
        elif efficiency > 1.5:
            # Çok verimli: G artışı TF artışından fazla → devam
            best = curr
        elif efficiency >= 0.8:
            # Dirsek bölgesi → al ve dur
            best = curr
            break
        else:
            # Verimsiz: G artışı TF maliyetine değmez → öncekinde kal
            break

    return best


# ═══════════════════════════════════════════════════════════════════
#  Ana Backtest Akışı
# ═══════════════════════════════════════════════════════════════════

def fetch_klines(client: BinanceRestClient, symbol: str, tf: str,
                 limit: int = 1500) -> pd.DataFrame | None:
    """Kline çek — sayfalama ile büyük veri seti.

    Binance max 1500 mum/çağrı. Daha fazla veri için endTime ile
    geriye doğru sayfalayarak birleştirir.
    """
    target = TF_TARGET_BARS.get(tf, limit)
    tf_ms = TF_MS.get(tf, 60_000)

    try:
        # İlk çağrı (en güncel veri)
        df = client.get_klines(symbol, tf, min(target, 1500))
        if df is None or len(df) < WARMUP_BARS:
            return None

        all_dfs = [df]
        fetched = len(df)

        # Geriye doğru sayfala
        while fetched < target:
            earliest_ts = int(df["timestamp"].iloc[0].timestamp() * 1000)
            end_time = earliest_ts - 1  # 1ms öncesi

            batch_size = min(1500, target - fetched)
            try:
                raw = client._get("/fapi/v1/klines", {
                    "symbol": symbol,
                    "interval": tf,
                    "limit": batch_size,
                    "endTime": end_time,
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
            for col in ["open", "high", "low", "close", "volume", "taker_buy_volume"]:
                df2[col] = df2[col].astype(float)
            df2["timestamp"] = pd.to_datetime(df2["timestamp"], unit="ms")

            if len(df2) == 0:
                break

            all_dfs.insert(0, df2)
            fetched += len(df2)
            df = df2  # sonraki iterasyonda en eski mumdan devam
            time.sleep(0.1)  # rate limit

        # Birleştir ve sırala
        combined = pd.concat(all_dfs, ignore_index=True)
        combined = combined.drop_duplicates(subset=["timestamp"]).sort_values("timestamp").reset_index(drop=True)
        logger.debug(f"[BT] {symbol} {tf}: {len(combined)} mum ({len(combined) * tf_ms / 3_600_000:.0f} saat)")
        return combined

    except Exception as e:
        logger.warning(f"[BT] {symbol} {tf}: kline fetch failed: {e}")
        return None


def optimize_symbol_tf(closes: np.ndarray, highs: np.ndarray,
                       lows: np.ndarray, volumes: np.ndarray,
                       symbol: str, tf: str,
                       coeff_range: list, period_range: list,
                       expected_leverage: int = 1,
                       ) -> BacktestResult | None:
    """Bir coin+TF için tüm parametre kombinasyonlarını test et.

    Args:
        expected_leverage: G-bazlı beklenen kaldıraç — scoring'de
            kaldıraçlı drawdown cezası uygulanır.
    """
    best: BacktestResult | None = None
    best_score = -999

    for coeff in coeff_range:
        for period in period_range:
            trades = run_backtest(
                closes, highs, lows, volumes,
                coeff=coeff, period=period,
            )
            result = evaluate_trades(trades, symbol, tf, coeff, period,
                                     expected_leverage=expected_leverage)
            if result is None:
                continue

            # Likidasyon riski olan parametreleri reddet
            if result.liquidation_risk:
                logger.debug(f"[BT] {symbol} {tf}: coeff={coeff} period={period} "
                             f"SKIP — leveraged DD={result.leveraged_max_dd_pct:.1f}% "
                             f"(>{80}%, likidasyon riski)")
                continue

            # Skor: risk-adjusted — Sharpe-like (PnL/DD) × consistency
            # max_dd=0 → mükemmel ama güvenilmez, penalty ver
            dd_penalty = max(result.max_drawdown_pct, 0.5)  # min 0.5% DD
            risk_adj_pnl = result.total_pnl_pct / dd_penalty
            consistency = result.profit_factor * np.sqrt(result.total_trades)
            score = risk_adj_pnl * consistency

            # Kaldıraçlı drawdown penaltisi: DD × leverage büyüdükçe skor düşer
            if expected_leverage > 1 and result.leveraged_max_dd_pct > 30:
                lev_penalty = 1.0 - (result.leveraged_max_dd_pct - 30) / 100.0
                lev_penalty = max(lev_penalty, 0.1)  # min %10 skor koruması
                score *= lev_penalty

            if best is None:
                best = result
                best_score = score
            elif score > best_score:
                best = result
                best_score = score

    return best


def run_full_optimization(symbols: list[str], timeframes: list[str],
                          fast: bool = False) -> dict:
    """Tüm coinler ve TF'ler için optimizasyon yap.

    Returns:
        {
            "timestamp": "...",
            "results": {
                "BTCUSDT": {
                    "optimal_tf": "3m",
                    "G": 0.35,
                    "max_leverage": 45,
                    "params": {"1m": {...}, "3m": {...}, "5m": {...}},
                    "g_analysis": {"1m": {...}, "3m": {...}, "5m": {...}},
                },
                ...
            },
            "tf_summary": {"1m": {...}, "3m": {...}, "5m": {...}},
        }
    """
    client = BinanceRestClient()
    coeff_range = COEFF_RANGE_FAST if fast else COEFF_RANGE_FULL
    period_range = PERIOD_RANGE_FAST if fast else PERIOD_RANGE_FULL
    combos = len(coeff_range) * len(period_range)

    logger.info(f"[BT] Optimizasyon başlıyor: {len(symbols)} coin × "
                f"{len(timeframes)} TF × {combos} parametre = "
                f"{len(symbols) * len(timeframes) * combos} test")

    all_results = {}
    tf_best_agg = {tf: [] for tf in timeframes}  # TF bazlı aggregation

    for si, symbol in enumerate(symbols):
        logger.info(f"[BT] [{si+1}/{len(symbols)}] {symbol}")
        symbol_data = {"params": {}, "g_analysis": {}}
        g_analyses = []

        for tf in timeframes:
            df = fetch_klines(client, symbol, tf, TF_KLINE_LIMITS.get(tf, 1500))
            if df is None:
                continue

            closes = df["close"].values.astype(float)
            highs = df["high"].values.astype(float)
            lows = df["low"].values.astype(float)
            volumes = df["volume"].values.astype(float)

            # 1. G dalga analizi (önce — kaldıraç bilgisi optimize'a gerekli)
            g_res = analyze_g_for_tf(closes, highs, lows, symbol, tf)
            tf_leverage = 1
            if g_res:
                g_analyses.append(g_res)
                tf_leverage = g_res.max_leverage
                symbol_data["g_analysis"][tf] = {
                    "G": round(g_res.G, 4),
                    "I": round(g_res.I, 4),
                    "regime": g_res.regime,
                    "er": round(g_res.er, 4),
                    "hurst": round(g_res.hurst, 4),
                    "max_leverage": g_res.max_leverage,
                    "sl_pct": round(g_res.sl_pct, 4),
                    "wave_count": g_res.wave_count,
                }

            # 2. AlphaTrend parametre optimizasyonu (kaldıraç-aware scoring)
            best = optimize_symbol_tf(
                closes, highs, lows, volumes,
                symbol, tf, coeff_range, period_range,
                expected_leverage=tf_leverage,
            )
            if best:
                symbol_data["params"][tf] = {
                    "coeff": best.coeff,
                    "period": best.period,
                    "total_trades": best.total_trades,
                    "win_rate": round(best.win_rate, 1),
                    "total_pnl_pct": round(best.total_pnl_pct, 2),
                    "avg_pnl_pct": round(best.avg_pnl_pct, 3),
                    "profit_factor": round(best.profit_factor, 2),
                    "max_drawdown_pct": round(best.max_drawdown_pct, 2),
                    "leveraged_max_dd_pct": round(best.leveraged_max_dd_pct, 2),
                    "liquidation_risk": best.liquidation_risk,
                    "avg_hold_bars": round(best.avg_hold_bars, 1),
                }
                tf_best_agg[tf].append(best)

            # Rate limit
            time.sleep(0.2)

        # 3. Zoom dirsek → optimal TF
        optimal = select_optimal_tf(g_analyses)
        if optimal:
            symbol_data["optimal_tf"] = optimal.timeframe
            symbol_data["G"] = round(optimal.G, 4)
            symbol_data["max_leverage"] = optimal.max_leverage
            symbol_data["regime"] = optimal.regime
        else:
            symbol_data["optimal_tf"] = "5m"
            symbol_data["G"] = 0
            symbol_data["max_leverage"] = 1

        all_results[symbol] = symbol_data

    # TF bazlı özet (medyan parametreler)
    tf_summary = {}
    for tf in timeframes:
        results = tf_best_agg[tf]
        if not results:
            continue

        coeffs = [r.coeff for r in results]
        periods = [r.period for r in results]
        win_rates = [r.win_rate for r in results]
        pnls = [r.total_pnl_pct for r in results]

        tf_summary[tf] = {
            "median_coeff": round(float(np.median(coeffs)), 1),
            "median_period": int(np.median(periods)),
            "mean_coeff": round(float(np.mean(coeffs)), 2),
            "mean_period": round(float(np.mean(periods)), 1),
            "avg_win_rate": round(float(np.mean(win_rates)), 1),
            "avg_total_pnl": round(float(np.mean(pnls)), 2),
            "coin_count": len(results),
            "coeff_distribution": {str(c): coeffs.count(c) for c in set(coeffs)},
            "period_distribution": {str(p): periods.count(p) for p in set(periods)},
        }

    return {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "config": {
            "coeff_range": coeff_range,
            "period_range": period_range,
            "symbols": symbols,
            "timeframes": timeframes,
            "zigzag_n": ZIGZAG_N,
        },
        "results": all_results,
        "tf_summary": tf_summary,
    }


def print_report(data: dict) -> None:
    """Sonuçları güzel formatta yazdır."""
    print("\n" + "=" * 80)
    print("  SYSTEM N BACKTEST RAPORU")
    print(f"  {data['timestamp']}")
    print("=" * 80)

    # TF Özet
    print("\n── TF BAZLI ÖZET (Medyan Parametreler) ──")
    print(f"{'TF':<6} {'Coeff':>8} {'Period':>8} {'WinRate':>8} {'AvgPnL':>8} {'Coinler':>8}")
    print("-" * 50)
    for tf, s in data.get("tf_summary", {}).items():
        print(f"{tf:<6} {s['median_coeff']:>8.1f} {s['median_period']:>8} "
              f"{s['avg_win_rate']:>7.1f}% {s['avg_total_pnl']:>7.2f}% {s['coin_count']:>8}")

    # Coin bazlı
    print("\n── COIN BAZLI SONUÇLAR ──")
    print(f"{'Coin':<12} {'OptTF':<6} {'G%':>8} {'MaxLev':>7} {'Rejim':<10} "
          f"{'Coeff':>6} {'Period':>7} {'WR%':>6} {'PnL%':>8} {'PF':>6} {'LevDD%':>7} {'Liq':>4}")
    print("-" * 105)

    for symbol, r in sorted(data.get("results", {}).items()):
        opt_tf = r.get("optimal_tf", "?")
        G = r.get("G", 0)
        max_lev = r.get("max_leverage", 0)
        regime = r.get("regime", "?")

        # Optimal TF'nin parametreleri
        params = r.get("params", {}).get(opt_tf, {})
        coeff = params.get("coeff", 0)
        period = params.get("period", 0)
        wr = params.get("win_rate", 0)
        pnl = params.get("total_pnl_pct", 0)
        pf = params.get("profit_factor", 0)
        lev_dd = params.get("leveraged_max_dd_pct", 0)
        liq = "⚠" if params.get("liquidation_risk", False) else "OK"

        print(f"{symbol:<12} {opt_tf:<6} {G:>7.3f}% {max_lev:>6}x {regime:<10} "
              f"{coeff:>6.1f} {period:>7} {wr:>5.1f}% {pnl:>+7.2f}% {pf:>5.2f} "
              f"{lev_dd:>6.1f}% {liq:>4}")

    # Kaldıraç dağılımı
    leverages = [r.get("max_leverage", 0)
                 for r in data.get("results", {}).values() if r.get("max_leverage", 0) > 0]
    if leverages:
        print(f"\n── KALDIRAÇ İSTATİSTİKLERİ ──")
        print(f"  Ortalama: {np.mean(leverages):.0f}x")
        print(f"  Medyan:   {np.median(leverages):.0f}x")
        print(f"  Min:      {min(leverages)}x")
        print(f"  Max:      {max(leverages)}x")
        print(f"  >20x:     {sum(1 for l in leverages if l > 20)}/{len(leverages)} coin")
        print(f"  >50x:     {sum(1 for l in leverages if l > 50)}/{len(leverages)} coin")


# ═══════════════════════════════════════════════════════════════════
#  Main
# ═══════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="System N AlphaTrend Backtest")
    parser.add_argument("--symbols", nargs="+", default=None,
                        help="Coin listesi (ör: BTCUSDT ETHUSDT)")
    parser.add_argument("--tf", nargs="+", default=None,
                        help="Timeframe listesi (ör: 1m 3m 5m)")
    parser.add_argument("--fast", action="store_true",
                        help="Hızlı mod (az parametre kombinasyonu)")
    parser.add_argument("--top", type=int, default=20,
                        help="Top N coin (hacim sıralı, --symbols yoksa)")
    parser.add_argument("--output", default="data/system_n_optimize.json",
                        help="Çıktı dosyası")
    args = parser.parse_args()

    # Coinler
    if args.symbols:
        symbols = args.symbols
    else:
        # Binance'den top N coin çek (hacim sıralı)
        try:
            client = BinanceRestClient()
            tickers = client.get_all_ticker_prices()
            # 24h volume ile sırala
            ticker_24h = client._get("/fapi/v1/ticker/24hr")
            sorted_t = sorted(ticker_24h,
                              key=lambda x: float(x.get("quoteVolume", 0)),
                              reverse=True)
            symbols = [t["symbol"] for t in sorted_t
                       if t["symbol"].endswith("USDT")][:args.top]
            logger.info(f"[BT] Top {args.top} coin: {', '.join(symbols[:5])}...")
        except Exception:
            symbols = DEFAULT_SYMBOLS[:args.top]

    timeframes = args.tf or TIMEFRAMES

    # Çalıştır
    t0 = time.time()
    data = run_full_optimization(symbols, timeframes, fast=args.fast)
    elapsed = time.time() - t0

    # Rapor
    print_report(data)
    print(f"\nSüre: {elapsed:.1f}s")

    # Kaydet
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    print(f"Kaydedildi: {output_path}")


if __name__ == "__main__":
    main()
