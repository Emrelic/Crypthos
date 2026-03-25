"""System C Analyzer — Coklu Zaman Dilimi Dalga & Indikator Analizi.

Secilen coin icin 12 farkli timeframe'de (1m → 1w) 200 mum ile:
- Tum System A + B indikatorleri
- Zigzag dalga analizi (yukselis/dusus dalgalari ayri)
- ER + Hurst rejim tespiti
- Fee-aware guvenli kaldirac hesaplama

Trade amacsiz arastirma modulu (sonra trade eklenecek).
"""
import time
import math
import numpy as np
import pandas as pd
from dataclasses import dataclass, field
from loguru import logger
from core.config_manager import ConfigManager
from market.binance_rest import BinanceRestClient
from indicators.indicator_engine import IndicatorEngine

# System B'den reuse
from scanner.system_b_scanner import (
    detect_zigzag_swings,
    compute_efficiency_ratio,
    compute_hurst_exponent,
    SwingPoint,
)

# Binance destekli timeframe'ler (10m yok, 24h = 1d, 1hafta = 1w)
TIMEFRAMES = ["1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "8h", "12h", "1d", "1w"]


@dataclass
class WaveStats:
    """Yukselis ve dusus dalga istatistikleri."""
    up_waves: list = field(default_factory=list)
    down_waves: list = field(default_factory=list)

    # Yukselis dalgalari
    avg_up: float = 0.0
    max_up: float = 0.0
    min_up: float = 0.0

    # Dusus dalgalari
    avg_down: float = 0.0
    max_down: float = 0.0
    min_down: float = 0.0

    # Genel
    wave_count: int = 0
    trend_direction: str = ""  # UP / DOWN
    cv: float = 0.0
    G: float = 0.0  # geri dalga ort (System B uyumlu)
    I: float = 0.0  # ileri dalga ort


@dataclass
class TimeframeAnalysis:
    """Tek bir timeframe icin analiz sonucu."""
    timeframe: str = ""

    # Indikatorler
    rsi: float = 50.0
    macd_hist: float = 0.0
    macd_line: float = 0.0
    macd_signal: float = 0.0
    adx: float = 0.0
    ema_fast: float = 0.0
    ema_50: float = 0.0
    sma_slow: float = 0.0
    bb_upper: float = 0.0
    bb_middle: float = 0.0
    bb_lower: float = 0.0
    bb_width: float = 0.0
    atr: float = 0.0
    atr_pct: float = 0.0
    volume_ratio: float = 1.0
    obv_trend: str = ""  # UP / DOWN / FLAT
    price: float = 0.0

    # Rejim
    regime: str = "?"  # TREND / RANGING / TRANSITION
    er: float = 0.0
    hurst: float = 0.5

    # Dalga analizi
    waves: WaveStats = field(default_factory=WaveStats)

    # Kaldirac hesaplama
    max_wave: float = 0.0  # max(max_up, max_down)
    sl_pct: float = 0.0
    pratik_liq_pct: float = 0.0
    teorik_liq_pct: float = 0.0
    max_leverage: int = 0

    # Veri durumu
    candle_count: int = 0
    error: str = ""


class SystemCAnalyzer:
    """System C ana analiz motoru.

    Tek coin icin 12 timeframe'de kapsamli analiz.
    """

    def __init__(self, config: ConfigManager, rest_client: BinanceRestClient):
        self._config = config
        self._rest = rest_client
        self._last_results: list[TimeframeAnalysis] = []
        self._analyzing = False
        self._progress = ""
        # IndicatorEngine reuse (her TF icin yeniden olusturmuyoruz)
        self._indicator_engine = IndicatorEngine(config)

    def _cfg(self, key: str, default=None):
        """system_c config'den oku."""
        return self._config.get(f"system_c.{key}", default)

    @property
    def is_analyzing(self) -> bool:
        return self._analyzing

    @property
    def progress(self) -> str:
        return self._progress

    def get_results(self) -> list[TimeframeAnalysis]:
        return self._last_results

    def analyze_symbol(self, symbol: str) -> list[TimeframeAnalysis]:
        """Bir coin icin tum timeframe'lerde analiz yap.
        Sonuc: 12 TimeframeAnalysis listesi.
        Not: _analyzing flag'i disaridan yonetiliyorsa dokunma."""
        # Tek coin modu: flag'i biz yonet
        manage_flag = not self._analyzing
        if manage_flag:
            self._analyzing = True

        self._progress = f"{symbol} analiz ediliyor..."
        results = []

        try:
            candle_count = self._cfg("mum_sayisi", 200)

            self._progress = f"{symbol}: kline verileri cekiliyor..."
            klines_map = self._fetch_all_timeframes(symbol, candle_count)

            fetched = len(klines_map)
            logger.info(f"System C: {symbol} icin {fetched}/{len(TIMEFRAMES)} TF verisi alindi")

            for i, tf in enumerate(TIMEFRAMES):
                self._progress = f"{symbol}: {tf} analiz ({i+1}/{len(TIMEFRAMES)})"
                klines = klines_map.get(tf)
                if klines is None or klines.empty:
                    result = TimeframeAnalysis(timeframe=tf, error="veri yok")
                    results.append(result)
                    continue

                try:
                    result = self._analyze_single_tf(tf, klines)
                except Exception as e:
                    logger.error(f"System C: {symbol} {tf} analiz hatasi: {e}")
                    result = TimeframeAnalysis(timeframe=tf, error=str(e)[:20])
                results.append(result)

            self._last_results = results
            self._progress = f"{symbol}: tamamlandi ({fetched} TF)"

        except Exception as e:
            logger.error(f"System C analyze error for {symbol}: {e}")
            self._progress = f"Hata: {e}"
        finally:
            if manage_flag:
                self._analyzing = False

        return results

    def _fetch_all_timeframes(self, symbol: str, limit: int) -> dict[str, pd.DataFrame]:
        """12 TF icin sirayla kline fetch — retry + rate limit."""
        result = {}
        max_retries = 3

        for i, tf in enumerate(TIMEFRAMES):
            self._progress = f"{symbol}: {tf} verisi cekiliyor ({i+1}/{len(TIMEFRAMES)})"

            for attempt in range(max_retries):
                try:
                    df = self._rest.get_klines(symbol, tf, limit)
                    if df is not None and not df.empty:
                        result[tf] = df
                        logger.debug(f"System C: {symbol} {tf} OK ({len(df)} mum)")
                        break
                    else:
                        logger.warning(f"System C: {symbol} {tf} bos veri (deneme {attempt+1})")
                except Exception as e:
                    logger.warning(f"System C: {symbol} {tf} hata (deneme {attempt+1}): {e}")

                # Retry oncesi artan bekleme
                wait = 1.0 * (attempt + 1)
                time.sleep(wait)

            # Her istek arasinda bekleme — scanner da calisiyorsa rate limit paylasilir
            time.sleep(0.8)

        logger.info(f"System C: {len(result)}/{len(TIMEFRAMES)} TF fetched for {symbol}")
        return result

    def _analyze_single_tf(self, tf: str, klines: pd.DataFrame) -> TimeframeAnalysis:
        """Tek timeframe analizi: indikatorler + dalgalar + kaldirac."""
        result = TimeframeAnalysis(timeframe=tf)
        result.candle_count = len(klines)

        closes = klines["close"].values.astype(float)
        highs = klines["high"].values.astype(float)
        lows = klines["low"].values.astype(float)

        result.price = float(closes[-1]) if len(closes) > 0 else 0

        # ─── 1. Indikatorler ───
        self._compute_indicators(result, klines)

        # ─── 2. Rejim Tespiti (ER + Hurst) ───
        self._compute_regime(result, closes)

        # ─── 3. Dalga Analizi ───
        self._compute_waves(result, highs, lows, closes)

        # ─── 4. Kaldirac Hesaplama ───
        self._compute_leverage(result)

        return result

    def _compute_indicators(self, result: TimeframeAnalysis, klines: pd.DataFrame) -> None:
        """IndicatorEngine ile tum indikatorleri hesapla (reuse engine)."""
        try:
            values = self._indicator_engine.compute_all(klines)

            result.rsi = values.get("RSI", 50.0)
            result.macd_hist = values.get("MACD_histogram", 0.0)
            result.macd_line = values.get("MACD_line", 0.0)
            result.macd_signal = values.get("MACD_signal", 0.0)
            result.adx = values.get("ADX", 0.0)
            result.ema_fast = values.get("EMA_fast", 0.0)
            result.ema_50 = values.get("EMA50", 0.0)
            result.sma_slow = values.get("SMA_slow", 0.0)
            result.bb_upper = values.get("BB_Upper", 0.0)
            result.bb_middle = values.get("BB_Middle", 0.0)
            result.bb_lower = values.get("BB_Lower", 0.0)
            result.bb_width = values.get("BB_Width", 0.0)
            result.atr = values.get("ATR", 0.0)
            result.volume_ratio = values.get("Volume_ratio", 1.0)

            # ATR%
            if result.price > 0 and result.atr > 0:
                result.atr_pct = result.atr / result.price * 100

            # OBV trend
            obv_val = values.get("OBV", 0)
            if obv_val > 0:
                result.obv_trend = "UP"
            elif obv_val < 0:
                result.obv_trend = "DOWN"
            else:
                result.obv_trend = "FLAT"

        except Exception as e:
            logger.warning(f"System C indicator error ({result.timeframe}): {e}")

    def _compute_regime(self, result: TimeframeAnalysis, closes: np.ndarray) -> None:
        """ER + Hurst ile rejim tespiti."""
        if len(closes) < 20:
            result.regime = "?"
            return

        result.er = compute_efficiency_ratio(closes)
        result.hurst = compute_hurst_exponent(closes)

        er_ranging = self._cfg("er_ranging_esik", 0.15)
        er_trending = self._cfg("er_trending_esik", 0.35)

        if result.er < er_ranging:
            result.regime = "RANGE"
        elif result.er > er_trending:
            result.regime = "TREND"
        else:
            result.regime = "GECIS"  # transition

    def _compute_waves(self, result: TimeframeAnalysis,
                       highs: np.ndarray, lows: np.ndarray,
                       closes: np.ndarray) -> None:
        """Zigzag dalga analizi — yukselis/dusus ayri."""
        swing_n = self._cfg("swing_n", 10)
        swings = detect_zigzag_swings(highs, lows, swing_n)

        ws = WaveStats()
        ws.wave_count = max(0, len(swings) - 1)

        if len(swings) < 3:
            result.waves = ws
            return

        # Trend yonu (son 2 swing'den)
        last_two = swings[-2:]
        if last_two[-1].type == "SH" and last_two[-2].type == "SL":
            ws.trend_direction = "UP"
        elif last_two[-1].type == "SL" and last_two[-2].type == "SH":
            ws.trend_direction = "DOWN"
        else:
            ws.trend_direction = "UP" if last_two[-1].price > last_two[-2].price else "DOWN"

        # Dalga boylarini hesapla — MUTLAK yon (yukselis/dusus)
        up_waves = []
        down_waves = []
        forward_waves = []
        backward_waves = []

        for i in range(1, len(swings)):
            prev = swings[i - 1]
            curr = swings[i]
            wave_pct = abs(curr.price - prev.price) / prev.price * 100
            if wave_pct < 0.001:
                continue

            is_up = curr.price > prev.price
            if is_up:
                up_waves.append(wave_pct)
            else:
                down_waves.append(wave_pct)

            # Forward/backward (trend-relative, System B uyumlu)
            if ws.trend_direction == "UP":
                if is_up:
                    forward_waves.append(wave_pct)
                else:
                    backward_waves.append(wave_pct)
            else:
                if is_up:
                    backward_waves.append(wave_pct)
                else:
                    forward_waves.append(wave_pct)

        ws.up_waves = up_waves
        ws.down_waves = down_waves

        # Yukselis istatistikleri
        if up_waves:
            ws.avg_up = float(np.mean(up_waves))
            ws.max_up = float(np.max(up_waves))
            ws.min_up = float(np.min(up_waves))

        # Dusus istatistikleri
        if down_waves:
            ws.avg_down = float(np.mean(down_waves))
            ws.max_down = float(np.max(down_waves))
            ws.min_down = float(np.min(down_waves))

        # G (geri dalga) ve I (ileri dalga) — System B uyumlu
        if backward_waves:
            ws.G = float(np.mean(backward_waves))
        if forward_waves:
            ws.I = float(np.mean(forward_waves))

        # CV
        all_waves = up_waves + down_waves
        if len(all_waves) >= 2:
            ws.cv = float(np.std(all_waves) / np.mean(all_waves))

        result.waves = ws

        # Max wave (kaldirac hesabi icin)
        result.max_wave = max(ws.max_up, ws.max_down) if (ws.max_up > 0 or ws.max_down > 0) else 0

    def _compute_leverage(self, result: TimeframeAnalysis) -> None:
        """Fee-aware guvenli kaldirac hesaplama.

        Formul:
        1. SL = G_kaynak × sl_g_carpani
        2. Pratik Likidasyon = SL × liq_carpani
        3. Teorik Likidasyon = pratik_liq / liq_seviyesi
        4. Fee-aware: teorik_liq += fee
        5. Max Kaldirac = 100 / teorik_liq
        """
        sl_g_carpani = self._cfg("sl_g_carpani", 1.5)
        liq_carpani = self._cfg("liq_carpani", 2.0)
        liq_seviyesi = self._cfg("liq_seviyesi", 0.7)
        fee_pct = self._cfg("fee_pct", 0.08)
        g_kaynak = self._cfg("g_kaynak", "max")  # "max" or "avg"

        ws = result.waves

        # G kaynagi secimi
        if g_kaynak == "max":
            g_val = result.max_wave  # max(max_up, max_down)
        elif g_kaynak == "avg_g":
            g_val = ws.G  # geri dalga ortalaması
        elif g_kaynak == "avg_all":
            all_w = ws.up_waves + ws.down_waves
            g_val = float(np.mean(all_w)) if all_w else 0
        else:
            g_val = result.max_wave

        if g_val <= 0:
            return

        # 1. SL
        result.sl_pct = g_val * sl_g_carpani

        # 2. Pratik Likidasyon
        result.pratik_liq_pct = result.sl_pct * liq_carpani

        # 3. Teorik Likidasyon (fee-aware)
        if liq_seviyesi > 0:
            result.teorik_liq_pct = result.pratik_liq_pct / liq_seviyesi + fee_pct
        else:
            result.teorik_liq_pct = result.pratik_liq_pct + fee_pct

        # 4. Max Kaldirac
        if result.teorik_liq_pct > 0:
            raw_lev = 100.0 / result.teorik_liq_pct
            result.max_leverage = max(1, min(125, int(raw_lev)))
        else:
            result.max_leverage = 125
