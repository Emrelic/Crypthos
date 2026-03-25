"""System H Scanner — Hibrit Sistem (A + B + D + F Entegrasyon).

A'nın temel altyapısı üzerine:
  - B'den: Zigzag dalga analizi (G/I), ER+Hurst rejim tespiti, hysteresis
  - D'den: Zoom Diyafram (optimal TF seçimi), G bazlı kaldıraç hesaplama
  - F'den: P(win)/EV istatistiksel giriş kapısı

Akış:
  1. Sabit TF'de (A gibi) tüm coinleri skorla ve sırala
  2. Finalist coinler için Zoom Diyafram çalıştır → G hesapla
  3. G'den kaldıraç, SL, TP, trailing türet
  4. ER+Hurst ile rejim tespit et (ADX yerine)
  5. P(win)/EV skor çarpanı olarak uygula
  6. A'nın tüm hard filtreleri + risk yönetimi korunsun
"""
import math
import time
import numpy as np
from concurrent.futures import ThreadPoolExecutor, Future
from dataclasses import dataclass, field
from typing import Optional
import pandas as pd
from loguru import logger
from core.config_manager import ConfigManager
from indicators.indicator_engine import IndicatorEngine
from analysis.confluence import ConfluenceScorer
from analysis.market_regime import MarketRegimeDetector
from analysis.divergence import DivergenceDetector
from scanner.system_b_scanner import (
    detect_zigzag_swings,
    analyze_waves,
    compute_efficiency_ratio,
    compute_hurst_exponent,
    SwingPoint,
    WaveAnalysis,
)
from scanner.system_g_scanner import (
    OptCombo, OptResult, CoinOptCache,
)

# Timeframe to seconds mapping for wall strength calculation
_TF_SECONDS = {"1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800,
               "1h": 3600, "2h": 7200, "4h": 14400, "6h": 21600,
               "8h": 28800, "12h": 43200}

# Zoom TF merdiveni (D'den)
ZOOM_TF_LADDER = [
    ("5m", 5), ("15m", 15), ("30m", 30),
    ("1h", 60), ("2h", 120), ("4h", 240),
    ("8h", 480), ("12h", 720), ("1d", 1440),
]


# ─────────────────────────── Data Classes ───────────────────────────

@dataclass
class ZoomTFResult:
    """Tek bir TF'nin zoom analiz sonucu."""
    tf: str = ""
    minutes: int = 0
    G: float = 0.0
    I: float = 0.0
    cv: float = 0.0
    wave_count: int = 0
    verimlilik: float = 0.0
    g_artis_hizi: float = 0.0
    leverage: int = 0


@dataclass
class ZoomResult:
    """Zoom diyafram analiz sonucu — optimal TF seçimi."""
    all_tfs: list = field(default_factory=list)
    optimal_tf: str = "5m"
    optimal_minutes: int = 5
    optimal_G: float = 0.0
    optimal_I: float = 0.0
    optimal_leverage: int = 1
    mid_tf: str = "1h"
    macro_tf: str = "4h"
    dirsek_index: int = 0
    wave_count: int = 0
    cv: float = 0.0


@dataclass
class LeverageCalc:
    """Kaldıraç hesaplama detayları."""
    G_raw: float = 0.0
    G: float = 0.0          # efektif G (ters hesap sonrası, %)
    I: float = 0.0
    sl_pct: float = 0.0
    pratik_liq_pct: float = 0.0
    teorik_liq_pct: float = 0.0
    max_leverage: int = 1
    fee_pct: float = 0.08
    slippage_pct: float = 0.03
    wave_count: int = 0
    cv: float = 0.0
    g_adjusted: bool = False
    # Trailing (G bazlı)
    trailing_trigger_pct: float = 0.0
    trailing_callback_pct: float = 0.0
    tp_pct: float = 0.0


@dataclass
class RegimeResultH:
    """ER + Hurst bazlı rejim tespiti (ADX yerine)."""
    regime: str = "UNDECIDED"       # TREND / RANGING / WEAK_TREND / WEAK_RANGING / UNDECIDED
    confidence: float = 0.0
    er_macro: float = 0.0
    er_micro: float = 0.0
    hurst: float = 0.5
    macro_class: str = ""           # TRENDING / TRANSITION / RANGING
    micro_class: str = ""
    hurst_class: str = ""           # TRENDING / UNCERTAIN / RANGING
    macro_direction: str = ""       # UP / DOWN
    micro_direction: str = ""
    direction_aligned: bool = True
    # Hysteresis
    _consecutive_count: int = 0
    _pending_regime: str = ""


@dataclass
class ProbabilityResult:
    """P(win)/EV hesaplama sonucu (F'den)."""
    p_win: float = 0.0             # kazanma olasılığı (0-1)
    p_loss: float = 0.0            # kaybetme olasılığı (0-1)
    ev_pct: float = 0.0            # expected value (% ROI)
    forward_pcts: list = field(default_factory=list)
    retrace_pcts: list = field(default_factory=list)
    p90_retrace: float = 0.0
    avg_forward: float = 0.0
    sufficient: bool = False        # yeterli swing verisi var mı


@dataclass
class SystemHScanResult:
    """System H tarama sonucu — A bazlı, G/Zoom/ER/EV zenginleştirilmiş."""
    symbol: str
    score: float = 0.0              # composite score (-100 to +100)
    direction: str = "LONG"
    confluence: dict = field(default_factory=dict)
    regime: dict = field(default_factory=dict)         # A'nın orijinal regime dict
    regime_h: RegimeResultH = field(default_factory=RegimeResultH)  # ER+Hurst rejim
    divergences: list = field(default_factory=list)
    indicator_values: dict = field(default_factory=dict)
    volume_24h: float = 0.0
    price_change_pct: float = 0.0
    price: float = 0.0
    atr: float = 0.0
    atr_percent: float = 0.0
    rsi: float = 50.0
    adx: float = 0.0
    eligible: bool = False
    reject_reason: str = ""
    filter_checks: dict = field(default_factory=dict)
    leverage: int = 0
    timeframe: str = "1m"
    funding_rate: float = 0.0
    oi_change_pct: float = 0.0
    ob_imbalance: float = 0.0
    ob_wall_signal: str = "NONE"
    ob_wall_seconds: float = 0.0
    ob_ask_depth_seconds: float = 0.0
    ob_bid_depth_seconds: float = 0.0
    ob_liquidity: float = 0.0
    ob_thin_book: bool = False
    mtf_data: dict = field(default_factory=dict)
    adx_regime: str = ""            # uyumluluk için korunuyor

    # ─── System H yeni alanlar ───
    zoom: ZoomResult = field(default_factory=ZoomResult)
    leverage_calc: LeverageCalc = field(default_factory=LeverageCalc)
    probability: ProbabilityResult = field(default_factory=ProbabilityResult)
    G: float = 0.0                  # efektif G (%)
    I: float = 0.0                  # ileri dalga ort (%)
    sl_pct: float = 0.0            # G bazlı SL (%)
    tp_pct: float = 0.0            # TP (%) — 0=trailing
    trailing_trigger_pct: float = 0.0
    trailing_callback_pct: float = 0.0
    ev_multiplier: float = 1.0     # EV skor çarpanı
    regime_zone: str = ""           # TREND / RANGING / GRAY (ER+Hurst bazlı)
    entry_mode: str = "SYSTEM_H"

    # ─── Optimizer (G'den) ───
    opt_status: str = "NONE"        # NONE / PENDING / CACHED / FRESH
    opt_result: OptResult = field(default_factory=OptResult)
    opt_leverage: int = 0
    opt_tp_pct: float = 0.0
    opt_sl_pct: float = 0.0
    opt_score: float = 0.0
    opt_blended: bool = False       # optimizer sonucu blend edildi mi

    # ─── BTC beta + climax ───
    btc_beta: float = 0.0
    btc_direction: str = "FLAT"
    climax_detected: bool = False


# ─────────────────────────── Scanner Class ───────────────────────────

class SystemHScanner:
    """System H: A temel + B dalga + D zoom + F istatistik.

    Faz 1: Sabit TF'de skorlama (A gibi) → coin sıralaması
    Faz 2: Finalist coinler için Zoom Diyafram → G → kaldıraç
    Faz 3: ER+Hurst rejim tespiti (ADX yerine)
    Faz 4: P(win)/EV skor çarpanı
    """

    def __init__(self, config: ConfigManager):
        self._config = config
        self._engine = IndicatorEngine(config)
        self._confluence = ConfluenceScorer(threshold=4.0, config=config)
        self._regime_detector = MarketRegimeDetector()
        self._divergence = DivergenceDetector(lookback=20)

        # Score weights — Faz 1 ön-skorlama (A'dan, sıralama amaçlı)
        self._w_trend = 0.30
        self._w_entry = 0.25
        self._w_risk = 0.25
        self._w_sentiment = 0.20

        # Regime hysteresis cache (B'den): {symbol: RegimeResultH}
        self._regime_cache: dict[str, RegimeResultH] = {}
        self._regime_history: dict[str, list] = {}  # {symbol: [son 3 rejim]}

        # Per-coin optimizer (G'den)
        self._opt_cache: dict[str, CoinOptCache] = {}
        self._opt_futures: dict[str, Future] = {}
        self._opt_executor = ThreadPoolExecutor(max_workers=2)

    def _cfg_h(self, key: str, default=None):
        """system_h config'den oku."""
        return self._config.get(f"system_h.{key}", default)

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # FAZ 1: Skorlama (A'nın mevcut sistemi — sabit TF)
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def score_symbol(self, symbol: str, klines: pd.DataFrame,
                     volume_24h: float = 0, price_change_pct: float = 0,
                     market_context: dict = None) -> SystemHScanResult:
        """Faz 1: Tek coin skorlaması (sabit TF, A ile aynı).
        Bu skor sıralama/karşılaştırma içindir. Kaldıraç henüz yok."""
        result = SystemHScanResult(
            symbol=symbol,
            score=0.0,
            direction="LONG",
            volume_24h=volume_24h,
            price_change_pct=price_change_pct,
        )

        # Market context inject (A'dan)
        if market_context:
            result.funding_rate = market_context.get("funding_rate", 0.0)
            result.oi_change_pct = market_context.get("oi_change_pct", 0.0)
            result.ob_imbalance = market_context.get("ob_imbalance", 0.0)
            result.ob_wall_signal = market_context.get("ob_wall_signal", "NONE")
            result.ob_wall_seconds = market_context.get("ob_wall_seconds", 0.0)
            result.ob_ask_depth_seconds = market_context.get("ob_ask_depth_seconds", 0.0)
            result.ob_bid_depth_seconds = market_context.get("ob_bid_depth_seconds", 0.0)
            result.ob_liquidity = market_context.get("ob_liquidity", 0.0)
            result.ob_thin_book = market_context.get("ob_thin_book", False)

        if klines is None or klines.empty or len(klines) < 50:
            result.reject_reason = "insufficient_data"
            return result

        try:
            # Compute all 30+ indicators (A'dan aynen)
            indicators = self._engine.compute_all(klines)
            result.indicator_values = indicators
            result.price = indicators.get("Price", 0)
            result.atr = indicators.get("ATR", 0)
            rsi_val = indicators.get("RSI", 50)
            result.rsi = 50.0 if (rsi_val is None or (isinstance(rsi_val, float) and math.isnan(rsi_val))) else rsi_val
            adx_val = indicators.get("ADX", 0)
            result.adx = 0.0 if (adx_val is None or (isinstance(adx_val, float) and math.isnan(adx_val))) else adx_val

            if result.price > 0 and result.atr > 0:
                result.atr_percent = (result.atr / result.price) * 100

            # Regime detection (A'nın orijinali — uyumluluk için tutulur)
            regime = self._regime_detector.detect(indicators)
            result.regime = regime

            # Confluence with regime weights (A'dan)
            regime_weights = regime.get("indicator_weights", {})
            confluence = self._confluence.score(indicators, regime_weights)
            result.confluence = confluence

            # Divergence (A'dan)
            ind_series = {}
            for name in ["RSI", "OBV"]:
                ind = self._engine.get_indicator(name)
                if ind and ind._series is not None:
                    ind_series[name] = ind._series
            divergences = self._divergence.detect_all(klines, ind_series)
            result.divergences = divergences

            # Direction (A'dan — confluence bazlı)
            conf_score = confluence.get("score", 0)
            result.direction = "LONG" if conf_score >= 0 else "SHORT"

            # Composite score (A'nın 4 bileşeni — HER ZAMAN hesapla)
            result.score = self._compute_score(result)

            # Hard filters (A'dan — G bazlı uyarlamalarla)
            eligible, reason = self._check_eligibility(result)
            result.eligible = eligible
            result.reject_reason = reason

        except Exception as e:
            logger.debug(f"[H] Scoring error for {symbol}: {e}")
            result.reject_reason = f"error: {e}"

        return result

    def score_batch(self, klines_map: dict[str, pd.DataFrame],
                    ticker_data: dict[str, dict],
                    market_context_map: dict[str, dict] = None) -> list[SystemHScanResult]:
        """Faz 1: Tüm coinleri skorla, sırayla döndür."""
        results = []
        ctx_map = market_context_map or {}
        for symbol, klines in klines_map.items():
            ticker = ticker_data.get(symbol, {})
            vol = ticker.get("volume_24h", 0)
            change = ticker.get("price_change_pct", 0)
            ctx = ctx_map.get(symbol)
            result = self.score_symbol(symbol, klines, vol, change, ctx)
            results.append(result)

        results.sort(key=lambda r: abs(r.score), reverse=True)
        return results

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # FAZ 2: Zoom Diyafram + G Bazlı Kaldıraç (D'den)
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def enrich_with_zoom(self, result: SystemHScanResult,
                         klines_by_tf: dict[str, list]) -> None:
        """Faz 2: Finalist coin için Zoom Diyafram çalıştır.
        G → kaldıraç, SL, TP, trailing hesapla.

        klines_by_tf: {"5m": [kline_list], "15m": [...], "1h": [...], ...}
        Kline format: [open_time, open, high, low, close, volume, ...]
        """
        sh = self._config.get("system_h", {})

        # 2a. Zoom diyafram → optimal TF ve G
        result.zoom = self._zoom_diyafram(klines_by_tf, sh)
        zoom = result.zoom

        if zoom.optimal_G < 0.01:
            result.reject_reason = "zoom_no_valid_g"
            result.eligible = False
            return

        # 2b. G bazlı kaldıraç hesapla
        result.leverage_calc = self._calculate_leverage_from_g(
            zoom.optimal_G, result.regime_zone or "TREND", sh,
            wave_count=zoom.wave_count, cv=zoom.cv, I=zoom.optimal_I,
        )
        lc = result.leverage_calc
        result.G = lc.G
        result.I = lc.I
        result.sl_pct = lc.sl_pct
        result.leverage = lc.max_leverage

        # Min kaldıraç kontrolü
        min_lev = sh.get("min_leverage", 2)
        if result.leverage < min_lev:
            result.reject_reason = f"leverage_low_{result.leverage}x"
            result.eligible = False
            return

        # User max kaldıraç sınırı (A ile uyum: min(user_max, G_bazlı))
        user_max = self._config.get("strategy.max_leverage", 20)
        if result.leverage > user_max:
            result.leverage = user_max

        # Dalga sayısı kontrolü
        min_waves = sh.get("min_wave_count", 4)
        if zoom.wave_count < min_waves:
            result.reject_reason = f"insufficient_waves_{zoom.wave_count}"
            result.eligible = False
            return

        # CV kontrolü (dalgalar tutarlı mı?)
        max_cv = sh.get("max_cv", 1.5)
        if zoom.cv > max_cv:
            result.reject_reason = f"waves_too_volatile_cv_{zoom.cv:.2f}"
            result.eligible = False
            return

        # 2c. TP & trailing parametreleri (G bazlı)
        self._set_exit_params(result, klines_by_tf, sh)

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # FAZ 3: ER + Hurst Rejim Tespiti (B'den, ADX yerine)
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def compute_regime_er_hurst(self, symbol: str,
                                klines_macro: pd.DataFrame,
                                klines_micro: pd.DataFrame) -> RegimeResultH:
        """ER + Hurst bazlı rejim tespiti + hysteresis.

        klines_macro: mid/macro TF mumları (100+ mum)
        klines_micro: mikro TF mumları (200+ mum)

        Hysteresis: rejim değişmesi için 3 ardışık aynı okuma gerekli.
        """
        raw_regime = self._compute_regime_raw(klines_macro, klines_micro)

        # Hysteresis uygula (B'den)
        hysteresis_n = self._cfg_h("regime_hysteresis", 3)
        history = self._regime_history.get(symbol, [])
        history.append(raw_regime.regime)

        # Son N okumaya bak
        if len(history) > hysteresis_n:
            history = history[-hysteresis_n:]
        self._regime_history[symbol] = history

        # Tüm son N okuma aynı mı?
        if len(history) >= hysteresis_n and len(set(history)) == 1:
            # Hepsi aynı → rejim değişikliği onaylandı
            final_regime = raw_regime
        else:
            # Henüz stabil değil → eski rejimi koru
            cached = self._regime_cache.get(symbol)
            if cached:
                final_regime = cached
                # Raw değerleri güncelle (bilgi amaçlı)
                final_regime.er_macro = raw_regime.er_macro
                final_regime.er_micro = raw_regime.er_micro
                final_regime.hurst = raw_regime.hurst
            else:
                # İlk okuma → direkt kabul et (bootstrap)
                final_regime = raw_regime

        self._regime_cache[symbol] = final_regime
        return final_regime

    def _compute_regime_raw(self, klines_macro: pd.DataFrame,
                            klines_micro: pd.DataFrame) -> RegimeResultH:
        """ER + Hurst ile ham rejim hesaplama (hysteresis öncesi)."""
        result = RegimeResultH()
        sh = self._config.get("system_h", {})

        # ER makro
        if klines_macro is not None and len(klines_macro) >= 20:
            closes_macro = klines_macro["close"].values.astype(float) if isinstance(klines_macro, pd.DataFrame) else np.array([float(k[4]) for k in klines_macro])
            result.er_macro = compute_efficiency_ratio(closes_macro)

            er_ranging = sh.get("er_macro_ranging", 0.15)
            er_trending = sh.get("er_macro_trending", 0.35)
            if result.er_macro < er_ranging:
                result.macro_class = "RANGING"
            elif result.er_macro > er_trending:
                result.macro_class = "TRENDING"
            else:
                result.macro_class = "TRANSITION"

            result.macro_direction = "UP" if closes_macro[-1] > closes_macro[0] else "DOWN"

        # ER mikro
        if klines_micro is not None and len(klines_micro) >= 20:
            closes_micro = klines_micro["close"].values.astype(float) if isinstance(klines_micro, pd.DataFrame) else np.array([float(k[4]) for k in klines_micro])
            result.er_micro = compute_efficiency_ratio(closes_micro)

            er_ranging = sh.get("er_micro_ranging", 0.20)
            er_trending = sh.get("er_micro_trending", 0.40)
            if result.er_micro < er_ranging:
                result.micro_class = "RANGING"
            elif result.er_micro > er_trending:
                result.micro_class = "TRENDING"
            else:
                result.micro_class = "TRANSITION"

            # Hurst
            result.hurst = compute_hurst_exponent(closes_micro)
            h_ranging = sh.get("hurst_ranging", 0.45)
            h_trending = sh.get("hurst_trending", 0.55)
            if result.hurst < h_ranging:
                result.hurst_class = "RANGING"
            elif result.hurst > h_trending:
                result.hurst_class = "TRENDING"
            else:
                result.hurst_class = "UNCERTAIN"

            # Mikro yön
            yon_mum = sh.get("yon_mum_sayisi", 72)
            recent = closes_micro[-min(yon_mum, len(closes_micro)):]
            result.micro_direction = "UP" if recent[-1] > recent[0] else "DOWN"

        result.direction_aligned = (
            result.macro_direction == result.micro_direction
            or not result.macro_direction
            or not result.micro_direction
        )

        # MTF 4 Kural Matrisi (B'den)
        macro = result.macro_class
        micro = result.micro_class

        if not macro or not micro:
            result.regime = "UNDECIDED"
            result.confidence = 0.0
        elif macro == micro:
            if macro == "TRENDING":
                result.regime = "TREND"
                result.confidence = 1.0
            elif macro == "RANGING":
                result.regime = "RANGING"
                result.confidence = 1.0
            else:
                result.regime = "UNDECIDED"
                result.confidence = 0.0
        elif macro == "TRANSITION" or micro == "TRANSITION":
            other = micro if macro == "TRANSITION" else macro
            if other == "TRENDING":
                result.regime = "WEAK_TREND"
                result.confidence = 0.5
            elif other == "RANGING":
                result.regime = "WEAK_RANGING"
                result.confidence = 0.5
            else:
                result.regime = "UNDECIDED"
                result.confidence = 0.0
        else:
            # Çelişki: biri TRENDING diğeri RANGING
            result.regime = "UNDECIDED"
            result.confidence = 0.0

        return result

    def regime_to_zone(self, regime: RegimeResultH) -> str:
        """ER+Hurst rejimini A uyumlu zone'a çevir.

        TREND / WEAK_TREND → "TRENDING"
        RANGING / WEAK_RANGING → "RANGING"
        UNDECIDED → "GRAY"
        """
        if regime.regime in ("TREND", "WEAK_TREND"):
            return "TRENDING"
        elif regime.regime in ("RANGING", "WEAK_RANGING"):
            return "RANGING"
        else:
            return "GRAY"

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # FAZ 4: P(win)/EV Hesaplama (F'den)
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def compute_probability(self, result: SystemHScanResult,
                            klines_zoom: list) -> None:
        """Zoom TF'deki swing verilerinden P(win)/EV hesapla.

        Bu Zoom'un YAN ÜRÜNÜ olarak gelir — ekstra veri çekmeye gerek yok.
        EV skor çarpanı olarak uygulanır (hard filtre değil).
        """
        sh = self._config.get("system_h", {})
        swing_n = sh.get("swing_n", 10)

        prob = ProbabilityResult()

        if not klines_zoom or len(klines_zoom) < swing_n * 3:
            result.probability = prob
            return

        highs = np.array([float(k[2]) for k in klines_zoom])
        lows = np.array([float(k[3]) for k in klines_zoom])
        closes = np.array([float(k[4]) for k in klines_zoom])
        current_price = closes[-1]

        swings = detect_zigzag_swings(highs, lows, swing_n)
        if len(swings) < 5:
            result.probability = prob
            return

        # Yöne göre forward/retrace ayır (F'den)
        direction = result.direction
        forward_pcts = []
        retrace_pcts = []

        for i in range(1, len(swings)):
            prev = swings[i - 1]
            curr = swings[i]
            wave_pct = abs(curr.price - prev.price) / prev.price * 100

            if wave_pct < 0.001:
                continue

            is_up = curr.price > prev.price

            if direction == "LONG":
                if prev.type == "SL" and is_up:
                    forward_pcts.append(wave_pct)
                elif prev.type == "SH" and not is_up:
                    retrace_pcts.append(wave_pct)
            else:  # SHORT
                if prev.type == "SH" and not is_up:
                    forward_pcts.append(wave_pct)
                elif prev.type == "SL" and is_up:
                    retrace_pcts.append(wave_pct)

        prob.forward_pcts = forward_pcts
        prob.retrace_pcts = retrace_pcts
        prob.sufficient = (len(forward_pcts) >= 3 and len(retrace_pcts) >= 3)

        if not prob.sufficient:
            result.probability = prob
            return

        prob.avg_forward = float(np.mean(forward_pcts))
        prob.p90_retrace = float(np.percentile(retrace_pcts, 90))

        # P(win)/P(loss) hesapla (F'den — çok döngülü)
        sl_pct = result.sl_pct
        tp_pct = result.tp_pct if result.tp_pct > 0 else prob.avg_forward

        if sl_pct > 0 and tp_pct > 0:
            tp_hits = sum(1 for f in forward_pcts if f >= tp_pct)
            p_fwd_tp = tp_hits / len(forward_pcts)

            sl_hits = sum(1 for r in retrace_pcts if r >= sl_pct)
            p_ret_sl = sl_hits / len(retrace_pcts)

            p_win_c = p_fwd_tp
            p_loss_c = (1 - p_fwd_tp) * p_ret_sl

            denom = p_win_c + p_loss_c
            if denom > 0:
                prob.p_win = p_win_c / denom
                prob.p_loss = p_loss_c / denom

                # EV = P(win) × TP - P(loss) × SL (% ROI olarak)
                leverage = result.leverage or 1
                ev_roi = prob.p_win * tp_pct * leverage - prob.p_loss * sl_pct * leverage
                prob.ev_pct = round(ev_roi, 2)

        result.probability = prob

        # EV skor çarpanı hesapla (hard filtre DEĞİL, çarpan)
        if prob.ev_pct > 0:
            # Pozitif EV → skoru yukarı it (max 1.3x)
            result.ev_multiplier = 1.0 + min(prob.ev_pct / 100.0, 0.3)
        elif prob.ev_pct < -10:
            # Çok negatif EV → skoru aşağı çek (min 0.7x)
            result.ev_multiplier = max(0.7, 1.0 + prob.ev_pct / 100.0)
        else:
            result.ev_multiplier = 1.0

        # EV çarpanını skora uygula (Faz 1 skoruna — final skor Faz 5'te üzerine yazılır)
        result.score = round(result.score * result.ev_multiplier, 1)

        # EV Hard Gate (opsiyonel — config'den açılır)
        ev_gate = sh.get("ev_hard_gate_enabled", False)
        min_ev = sh.get("min_ev_pct", 5.0)
        if ev_gate and prob.sufficient and prob.ev_pct < min_ev:
            result.eligible = False
            result.reject_reason = f"ev_low_{prob.ev_pct:.1f}%<{min_ev}%"

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # ZOOM DİYAFRAM (D'den)
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def _zoom_diyafram(self, klines_by_tf: dict[str, list],
                       sh: dict) -> ZoomResult:
        """Tüm TF'lerde G hesapla, dirsek noktasını bul, optimal TF seç.

        Dirsek: TF artarken G'nin en az arttığı (veya azaldığı) nokta.
        D'den alınmış, System H config parametreleri ile.
        """
        result = ZoomResult()
        swing_n = sh.get("swing_n", 10)
        fee_pct = sh.get("fee_pct", 0.08)
        slippage_pct = sh.get("slippage_pct", 0.03)
        fee_total = fee_pct + slippage_pct
        liq_seviye = sh.get("liq_seviyesi", 0.7)
        min_lev = sh.get("min_leverage", 2)
        sl_mult = sh.get("sl_mult_trend", 1.5)
        liq_mult = sh.get("liq_mult_trend", 3.0)

        tf_results = []

        for tf_name, tf_minutes in ZOOM_TF_LADDER:
            klines = klines_by_tf.get(tf_name, [])
            if not klines or len(klines) < swing_n * 3:
                continue

            highs = np.array([float(k[2]) for k in klines])
            lows = np.array([float(k[3]) for k in klines])
            closes = np.array([float(k[4]) for k in klines])

            swings = detect_zigzag_swings(highs, lows, swing_n)
            if len(swings) < 3:
                continue

            wave = analyze_waves(swings, closes[-1])
            G = wave.G
            if G < 0.001:
                continue

            # Kaldıraç hesapla
            teorik_liq = (G * liq_mult + fee_total) / liq_seviye
            leverage = int(100.0 / teorik_liq) if teorik_liq > 0 else 1
            leverage = max(1, leverage)

            verimlilik = tf_minutes / G if G > 0 else 0

            zr = ZoomTFResult(
                tf=tf_name, minutes=tf_minutes, G=G, I=wave.I,
                cv=wave.cv,
                wave_count=len(wave.backward_waves) + len(wave.forward_waves),
                verimlilik=verimlilik, leverage=leverage,
            )
            tf_results.append(zr)

        if not tf_results:
            return result

        result.all_tfs = tf_results

        # G artış hızı hesapla (dirsek bulmak için)
        for i in range(len(tf_results) - 1):
            curr = tf_results[i]
            nxt = tf_results[i + 1]
            if curr.G > 0:
                curr.g_artis_hizi = (nxt.G - curr.G) / curr.G

        # Dirsek noktası seçim algoritması (D'den)
        eligible_tfs = [t for t in tf_results if t.leverage >= min_lev]

        if not eligible_tfs:
            eligible_tfs = sorted(tf_results, key=lambda t: -t.leverage)
            best = eligible_tfs[0]
        else:
            decreasing = [t for t in eligible_tfs if t.g_artis_hizi < 0]
            if decreasing:
                best = max(decreasing, key=lambda t: t.minutes)
                result.dirsek_index = eligible_tfs.index(best) if best in eligible_tfs else 0
            else:
                candidates_with_rate = [t for t in eligible_tfs if t.g_artis_hizi != 0]
                if candidates_with_rate:
                    best = min(candidates_with_rate, key=lambda t: t.g_artis_hizi)
                    result.dirsek_index = eligible_tfs.index(best) if best in eligible_tfs else 0
                else:
                    best = max(eligible_tfs, key=lambda t: t.leverage)

        result.optimal_tf = best.tf
        result.optimal_minutes = best.minutes
        result.optimal_G = best.G
        result.optimal_I = best.I
        result.optimal_leverage = best.leverage
        result.wave_count = best.wave_count
        result.cv = best.cv

        # Mid ve macro TF'leri türet (×12 kuralı, D'den)
        tf_carpan = sh.get("tf_multiplier", 12)
        result.mid_tf = self._derive_nearest_tf(best.minutes * tf_carpan, klines_by_tf)
        mid_minutes = next((m for t, m in ZOOM_TF_LADDER if t == result.mid_tf), best.minutes * tf_carpan)
        result.macro_tf = self._derive_nearest_tf(mid_minutes * tf_carpan, klines_by_tf)

        logger.debug(f"[H Zoom] optimal={best.tf} G={best.G:.3f}% lev={best.leverage}x "
                     f"waves={best.wave_count} cv={best.cv:.2f} "
                     f"mid={result.mid_tf} macro={result.macro_tf}")

        return result

    @staticmethod
    def _derive_nearest_tf(target_minutes: float,
                           klines_by_tf: dict[str, list]) -> str:
        """Hedef dakikaya en yakın verisi olan TF'yi bul."""
        full_ladder = list(ZOOM_TF_LADDER) + [("1w", 10080)]
        best_tf = ""
        best_diff = float("inf")

        for tf_name, tf_min in full_ladder:
            klines = klines_by_tf.get(tf_name, [])
            if not klines or len(klines) < 20:
                continue
            diff = abs(tf_min - target_minutes)
            if diff < best_diff:
                best_diff = diff
                best_tf = tf_name

        if not best_tf:
            for tf_name, tf_min in reversed(full_ladder):
                if klines_by_tf.get(tf_name):
                    return tf_name
            return "1d"

        return best_tf

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # G BAZLI KALDIRAC HESAPLAMA (D'den)
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def _calculate_leverage_from_g(self, G: float, regime_zone: str, sh: dict,
                                   wave_count: int = 0, cv: float = 0.0,
                                   I: float = 0.0) -> LeverageCalc:
        """G'den kaldıraç hesapla. Ters G hesabı: max aşarsa G'yi büyütür.

        User max kaldıraç: min(config user_max, G_bazlı) kuralı state_machine'de uygulanır.
        """
        calc = LeverageCalc()
        calc.fee_pct = sh.get("fee_pct", 0.08)
        calc.slippage_pct = sh.get("slippage_pct", 0.03)
        calc.G_raw = G
        calc.G = G
        calc.I = I
        calc.cv = cv
        calc.wave_count = wave_count

        if G < 0.01:
            return calc

        fee_total = calc.fee_pct + calc.slippage_pct

        # Rejime göre çarpanlar
        if regime_zone == "TRENDING":
            sl_mult = sh.get("sl_mult_trend", 1.5)
            liq_mult = sh.get("liq_mult_trend", 3.0)
        else:  # RANGING veya GRAY
            sl_mult = sh.get("sl_mult_ranging", 2.0)
            liq_mult = sh.get("liq_mult_ranging", 4.0)

        liq_seviye = sh.get("liq_seviyesi", 0.7)

        # İlk hesap: orijinal G ile
        teorik_liq = (G * liq_mult + fee_total) / liq_seviye
        raw_leverage = int(100.0 / teorik_liq) if teorik_liq > 0 else 1
        raw_leverage = max(1, raw_leverage)

        # Binance max sınırı
        binance_max = sh.get("binance_max_leverage", 125)

        if raw_leverage > binance_max:
            # Ters G hesabı (D'den)
            teorik_liq_eff = 100.0 / binance_max
            pratik_liq_eff = teorik_liq_eff * liq_seviye - fee_total
            if pratik_liq_eff > 0:
                G_eff = pratik_liq_eff / liq_mult
            else:
                G_eff = G

            calc.G = G_eff
            calc.g_adjusted = True
            calc.sl_pct = G_eff * sl_mult
            calc.pratik_liq_pct = G_eff * liq_mult
            calc.teorik_liq_pct = teorik_liq_eff
            calc.max_leverage = binance_max

            logger.debug(f"[H Ters G] G_raw={G:.4f}% → G_eff={G_eff:.4f}% "
                         f"(lev {raw_leverage}x → {binance_max}x)")
        else:
            calc.sl_pct = G * sl_mult
            calc.pratik_liq_pct = G * liq_mult
            calc.teorik_liq_pct = teorik_liq
            calc.max_leverage = raw_leverage

        calc.max_leverage = max(1, calc.max_leverage)
        return calc

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # EXIT PARAMS (G bazlı — D'den uyarlanmış)
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def _set_exit_params(self, result: SystemHScanResult,
                         klines_by_tf: dict[str, list], sh: dict) -> None:
        """TP, trailing parametrelerini G bazlı ayarla."""
        G = result.leverage_calc.G
        zone = result.regime_zone

        if zone == "TRENDING" or zone == "GRAY":
            # Trend: trailing stop, TP yok
            result.tp_pct = 0.0
            result.trailing_trigger_pct = G * sh.get("trailing_trigger_g_mult", 2.5)
            result.trailing_callback_pct = G * sh.get("trailing_callback_g_mult", 0.5)
        else:
            # Ranging: sabit TP (BB karşı bant veya 3G — yakın olan)
            tp_g_mult = sh.get("ranging_tp_g_mult", 3.0)
            tp_from_g = G * tp_g_mult

            # BB mesafesi — mid TF'den
            tp_from_bb = tp_from_g
            mid_tf = result.zoom.mid_tf if result.zoom else None
            if mid_tf:
                klines_mid = klines_by_tf.get(mid_tf, [])
                if klines_mid and len(klines_mid) >= 20:
                    closes = np.array([float(k[4]) for k in klines_mid])
                    price = closes[-1]
                    bb_period = sh.get("bb_period", 20)
                    bb_std = sh.get("bb_std", 2.0)
                    bb_u, bb_m, bb_l = self._bollinger(closes, bb_period, bb_std)

                    if result.direction == "LONG" and bb_u > 0 and price > 0:
                        tp_from_bb = (bb_u - price) / price * 100
                    elif result.direction == "SHORT" and bb_l > 0 and price > 0:
                        tp_from_bb = (price - bb_l) / price * 100

            result.tp_pct = min(tp_from_g, tp_from_bb) if tp_from_bb > 0 else tp_from_g
            result.trailing_trigger_pct = 0.0
            result.trailing_callback_pct = 0.0

        # LeverageCalc'a da yaz (state_machine erişimi için)
        result.leverage_calc.tp_pct = result.tp_pct
        result.leverage_calc.trailing_trigger_pct = result.trailing_trigger_pct
        result.leverage_calc.trailing_callback_pct = result.trailing_callback_pct

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # ELIGIBILITY (A'dan — G bazlı uyarlama)
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def _check_eligibility(self, r: SystemHScanResult) -> tuple[bool, str]:
        """Hard filtreler. A'dan alınmış, ATR safety → G safety dönüşümü.

        Faz 1'de çağrılır (Zoom henüz yok). ATR bazlı temel filtreler çalışır.
        G bazlı filtreler enrich_with_zoom'da ayrıca uygulanır.
        """
        checks = {}
        first_fail = ""
        conf_score = r.confluence.get("score", 0)
        regime_name = r.regime.get("regime", "UNKNOWN")
        trend_dir = r.regime.get("trend_direction", "NONE")

        strat = self._config.get("strategy", {})
        sh = self._config.get("system_h", {})

        # 1. ATR SAFETY — Faz 1'de hala ATR kullanılır (G henüz yok)
        # Basit kontrol: ATR çok yüksekse eleme (kaba filtre)
        max_atr_pct = sh.get("max_atr_percent", 5.0)
        if r.atr_percent > max_atr_pct:
            passed = False
            checks["ATR"] = (False, f"{r.atr_percent:.3f}%", f"<{max_atr_pct}%")
            if not first_fail:
                first_fail = f"atr_too_volatile ({r.atr_percent:.3f}% > {max_atr_pct}%)"
        elif r.atr_percent < 0.01:
            checks["ATR"] = (False, f"{r.atr_percent:.4f}%", ">0.01%")
            if not first_fail:
                first_fail = "atr_too_low (no movement)"
        else:
            checks["ATR"] = (True, f"{r.atr_percent:.3f}%", f"<{max_atr_pct}%")

        # 2. VOLATILE regime filter
        if strat.get("volatile_filter", False):
            vol_passed = regime_name != "VOLATILE"
            checks["Regime"] = (vol_passed, regime_name[:4], "!VOL")
            if not vol_passed and not first_fail:
                first_fail = "volatile_regime"
        else:
            checks["Regime"] = (True, regime_name[:4], "any")

        # 3. FUNDING RATE
        fr_pct = r.funding_rate * 100 if r.funding_rate != 0 else 0
        if r.direction == "LONG":
            fr_passed = fr_pct <= 0.1
            checks["FR"] = (fr_passed, f"{fr_pct:+.3f}%", "<0.1%")
        else:
            fr_passed = fr_pct >= -0.1
            checks["FR"] = (fr_passed, f"{fr_pct:+.3f}%", ">-0.1%")
        if not fr_passed and not first_fail:
            first_fail = f"extreme_funding ({fr_pct:.3f}%)"

        # 4. ORDERBOOK (thin + wall + depth) — A'dan aynen
        ob_passed = not r.ob_thin_book
        wall_ok = True
        depth_ok = True
        wall_info = ""

        tf_seconds = _TF_SECONDS.get(r.timeframe, 300)
        wall_min_ratio = strat.get("wall_min_tf_ratio", 0.5)
        depth_min_ratio = strat.get("depth_min_tf_ratio", 3.0)

        if r.ob_wall_signal != "NONE" and r.ob_wall_seconds > 0:
            wall_ratio = r.ob_wall_seconds / tf_seconds
            blocks_direction = (
                (r.ob_wall_signal == "UP_BLOCKED" and r.direction == "LONG") or
                (r.ob_wall_signal == "DOWN_BLOCKED" and r.direction == "SHORT")
            )
            if blocks_direction and wall_ratio >= wall_min_ratio:
                wall_ok = False
                wall_info = f"wall {r.ob_wall_seconds:.0f}s ({wall_ratio:.2f}x tf)"

        if depth_min_ratio > 0:
            if r.direction == "LONG" and r.ob_ask_depth_seconds > 0:
                depth_ratio = r.ob_ask_depth_seconds / tf_seconds
                if depth_ratio >= depth_min_ratio:
                    depth_ok = False
                    wall_info = f"depth {r.ob_ask_depth_seconds:.0f}s ({depth_ratio:.1f}x tf)"
            elif r.direction == "SHORT" and r.ob_bid_depth_seconds > 0:
                depth_ratio = r.ob_bid_depth_seconds / tf_seconds
                if depth_ratio >= depth_min_ratio:
                    depth_ok = False
                    wall_info = f"depth {r.ob_bid_depth_seconds:.0f}s ({depth_ratio:.1f}x tf)"

        ob_final = ob_passed and wall_ok and depth_ok
        if not ob_passed:
            ob_actual = "thin"
        elif not wall_ok:
            ob_actual = "wall"
        elif not depth_ok:
            ob_actual = "deep"
        else:
            ob_actual = "ok"
        checks["OB"] = (ob_final, ob_actual, "ok")
        if not ob_final and not first_fail:
            if not ob_passed:
                first_fail = "thin_order_book"
            elif not wall_ok:
                first_fail = f"{r.ob_wall_signal.lower()} ({wall_info})"
            else:
                first_fail = f"total_depth_blocking ({wall_info})"

        # 5. ER+Hurst regime zone (Faz 1'de henüz hesaplanmadı — zone bilgisi Faz 3'te gelir)
        # Faz 1'de A'nın orijinal zone'unu kullan, Faz 3'te ER+Hurst ile güncellenecek
        # ER+Hurst bilgisi yokken ADX'e fallback
        adx = r.adx
        er_ranging_limit = sh.get("er_macro_ranging", 0.15)

        # Basitleştirilmiş zone (Faz 1 — detaylı zone Faz 3'te)
        if adx < 15:
            zone = "RANGING"
        elif adx >= 25:
            zone = "TRENDING"
        else:
            zone = "GRAY"

        checks["Zone"] = (True, zone[:4], zone[:4])

        # 6. Confluence check (A'dan — zone'a göre eşik)
        ranging_cfg = strat.get("ranging_mode", {})
        gray_cfg = strat.get("gray_zone", {})
        trending_cfg = strat.get("trending_mode", {})

        if zone == "RANGING":
            min_conf = ranging_cfg.get("min_confluence", 4.0)
            max_rsi_long = ranging_cfg.get("max_rsi_buy", 35)
            min_rsi_short = ranging_cfg.get("min_rsi_sell", 65)
        elif zone == "TRENDING":
            min_conf = trending_cfg.get("min_confluence", strat.get("min_confluence", 6.5))
            max_rsi_long = strat.get("max_rsi_long", 62)
            min_rsi_short = strat.get("min_rsi_short", 38)
        else:  # GRAY
            min_conf = gray_cfg.get("min_confluence", 6.0)
            max_rsi_long = strat.get("max_rsi_long", 62)
            min_rsi_short = strat.get("min_rsi_short", 38)

        if r.direction == "LONG":
            conf_passed = conf_score >= min_conf
            checks["Conf"] = (conf_passed, f"{conf_score:.1f}", f">={min_conf:.0f}")
        else:
            conf_passed = conf_score <= -min_conf
            checks["Conf"] = (conf_passed, f"{conf_score:.1f}", f"<=-{min_conf:.0f}")
        if not conf_passed and not first_fail:
            first_fail = f"confluence_{zone.lower()} ({conf_score:.1f})"

        # 7. RSI check
        if r.direction == "LONG":
            rsi_passed = r.rsi <= max_rsi_long
            checks["RSI"] = (rsi_passed, f"{r.rsi:.0f}", f"<={max_rsi_long}")
        else:
            rsi_passed = r.rsi >= min_rsi_short
            checks["RSI"] = (rsi_passed, f"{r.rsi:.0f}", f">={min_rsi_short}")
        if not rsi_passed and not first_fail:
            first_fail = f"rsi_{zone.lower()} ({r.rsi:.0f})"

        # 8. Trend direction check (A'dan)
        trend_passed = True
        if r.direction == "LONG" and trend_dir == "DOWN" and adx > 25:
            trend_passed = False
        elif r.direction == "SHORT" and trend_dir == "UP" and adx > 25:
            trend_passed = False
        checks["Trend"] = (trend_passed, trend_dir[:2] if trend_dir else "??",
                           f"={'UP' if r.direction == 'LONG' else 'DN'}?")
        if not trend_passed and not first_fail:
            first_fail = f"trend_against_{r.direction.lower()}"

        # 9. Volume confirmation (trend ve gray'de)
        use_volume = zone != "RANGING"
        if use_volume:
            obv_slope = r.indicator_values.get("OBV_slope", 0)
            cmf = r.indicator_values.get("CMF", 0)
            if r.direction == "LONG":
                vol_passed = obv_slope > 0 or cmf > 0
            else:
                vol_passed = obv_slope < 0 or cmf < 0
            checks["Vol"] = (vol_passed, f"{'+'if obv_slope > 0 else '-'}", "confirm")
        else:
            vol_passed = True
            checks["Vol"] = (True, "skip", "skip")
        if not vol_passed and not first_fail:
            first_fail = "no_volume_confirmation"

        # 10. MACD filter (trend ve gray'de)
        use_macd = zone != "RANGING"
        if use_macd:
            macd_h = r.indicator_values.get("MACD_histogram", 0)
            if r.direction == "LONG":
                macd_passed = macd_h > 0
            else:
                macd_passed = macd_h < 0
            checks["MACD"] = (macd_passed, f"{macd_h:.4f}",
                              f"{'>' if r.direction == 'LONG' else '<'}0")
        else:
            macd_passed = True
            checks["MACD"] = (True, "skip", "skip")
        if not macd_passed and not first_fail:
            first_fail = f"macd_not_{'bullish' if r.direction == 'LONG' else 'bearish'}"

        # 11. Gray zone confirmation (A'dan)
        if zone == "GRAY":
            confirmation_cfg = gray_cfg.get("confirmation_system", {})
            if confirmation_cfg.get("enabled", True):
                confirmation_score = self._calculate_gray_zone_confirmation(r, confirmation_cfg)
                required_score = confirmation_cfg.get("required_score", 0.6)
                gz_passed = confirmation_score >= required_score
                checks["GZ"] = (gz_passed, f"{confirmation_score:.2f}", f">={required_score}")
                if not gz_passed and not first_fail:
                    first_fail = f"gray_zone_low ({confirmation_score:.2f})"

        r.filter_checks = checks
        all_passed = not first_fail
        return all_passed, first_fail

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # SCORING (A'dan aynen — 4 orthogonal component)
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def _compute_score(self, r: SystemHScanResult) -> float:
        """Composite score 0-100 (negative for SHORT). A'dan aynen."""
        trend = self._score_trend_momentum(r)
        entry = self._score_entry_quality(r)
        risk = self._score_risk_profile(r)
        sentiment = self._score_market_sentiment(r)

        raw = (
            self._w_trend * trend +
            self._w_entry * entry +
            self._w_risk * risk +
            self._w_sentiment * sentiment
        )
        raw = max(0.0, min(raw, 100.0))

        if r.direction == "SHORT":
            raw = -raw

        return round(raw, 1)

    def _score_trend_momentum(self, r: SystemHScanResult) -> float:
        """Trend momentum skoru (A'dan)."""
        score = 50.0

        adx_slope = r.indicator_values.get("ADX_slope", 0)
        if adx_slope > 1.0:
            score += 12
        elif adx_slope > 0.3:
            score += 8
        elif adx_slope > 0:
            score += 3
        elif adx_slope < -1.0:
            score -= 12
        elif adx_slope < -0.3:
            score -= 6

        price = r.indicator_values.get("Price", 0)
        dc_upper = r.indicator_values.get("DC_Upper", 0)
        dc_lower = r.indicator_values.get("DC_Lower", 0)
        if dc_upper > dc_lower and price > 0:
            dc_range = dc_upper - dc_lower
            dc_pos = (price - dc_lower) / dc_range
            if r.direction == "LONG":
                if dc_pos >= 0.8: score += 15
                elif dc_pos >= 0.6: score += 10
                elif dc_pos >= 0.4: score += 4
                elif dc_pos < 0.2: score -= 8
            else:
                if dc_pos <= 0.2: score += 15
                elif dc_pos <= 0.4: score += 10
                elif dc_pos <= 0.6: score += 4
                elif dc_pos > 0.8: score -= 8

        if r.indicator_values.get("EMA_gap_expanding", False):
            score += 8
        else:
            score -= 3

        macd_h = r.indicator_values.get("MACD_histogram", 0)
        macd_h_prev = r.indicator_values.get("MACD_histogram_prev", None)
        if macd_h_prev is not None:
            macd_accel = macd_h - macd_h_prev
            if r.direction == "LONG" and macd_accel > 0: score += 8
            elif r.direction == "SHORT" and macd_accel < 0: score += 8
            elif r.direction == "LONG" and macd_accel < -0.001: score -= 5
            elif r.direction == "SHORT" and macd_accel > 0.001: score -= 5

        return max(0.0, min(score, 100.0))

    def _score_entry_quality(self, r: SystemHScanResult) -> float:
        """Entry quality skoru (A'dan)."""
        score = 50.0

        vol_ratio = r.indicator_values.get("Volume_ratio", 1.0)
        if vol_ratio > 2.0: score += 12
        elif vol_ratio > 1.3: score += 8
        elif vol_ratio > 1.0: score += 3
        elif vol_ratio < 0.5: score -= 12
        elif vol_ratio < 0.7: score -= 5

        bb_width = r.indicator_values.get("BB_Width", 0)
        if bb_width > 0:
            if 1.5 <= bb_width <= 3.5: score += 10
            elif bb_width < 1.0: score += 3
            elif bb_width > 6.0: score -= 10
            elif bb_width > 4.5: score -= 5

        bb_slope = r.indicator_values.get("BB_Width_slope", 0)
        if bb_width > 0 and bb_width < 3.0 and bb_slope > 0:
            score += 6
        elif bb_slope < -0.3:
            score -= 3

        pct = r.price_change_pct
        if r.direction == "LONG":
            if 0.5 <= pct <= 3.0: score += 8
            elif 0 < pct < 0.5: score += 3
            elif pct > 5.0: score -= 8
            elif pct < -2.0: score -= 5
        else:
            if -3.0 <= pct <= -0.5: score += 8
            elif -0.5 < pct < 0: score += 3
            elif pct < -5.0: score -= 8
            elif pct > 2.0: score -= 5

        return max(0.0, min(score, 100.0))

    def _score_risk_profile(self, r: SystemHScanResult) -> float:
        """Risk profile skoru (A'dan — G bilgisi varsa G bazlı, yoksa ATR bazlı)."""
        score = 55.0

        # G varsa G bazlı sweet spot, yoksa ATR bazlı (Faz 1 uyumluluk)
        if r.G > 0:
            # G bazlı risk değerlendirmesi
            cv = r.leverage_calc.cv if r.leverage_calc else 0
            if cv < 0.5:
                score += 20  # çok tutarlı dalgalar — ideal
            elif cv < 0.8:
                score += 12
            elif cv < 1.0:
                score += 5
            elif cv > 1.5:
                score -= 15  # çok tutarsız
        else:
            # ATR bazlı (Faz 1 — G henüz hesaplanmadı)
            strat = self._config.get("strategy", {})
            max_lev = strat.get("max_leverage", 20)
            liq_factor = strat.get("liq_factor", 70) / 100.0
            sl_liq_pct = strat.get("sl_liq_percent", 50) / 100.0
            safe_atr = (1.0 / max(max_lev, 1)) * 100 * liq_factor * sl_liq_pct / 2.0

            atr_pct = r.atr_percent
            if atr_pct <= safe_atr * 0.7: score += 20
            elif atr_pct <= safe_atr * 0.9: score += 12
            elif atr_pct <= safe_atr: score += 5
            elif atr_pct > safe_atr * 3: score -= 20
            if atr_pct < 0.05: score -= 15

        # Divergence (A'dan)
        for d in r.divergences:
            dtype = d.get("type", "")
            if r.direction == "LONG":
                if dtype == "REGULAR_BULLISH": score += 15
                elif dtype == "REGULAR_BEARISH": score -= 15
            else:
                if dtype == "REGULAR_BEARISH": score += 15
                elif dtype == "REGULAR_BULLISH": score -= 15

        return max(0.0, min(score, 100.0))

    def _score_market_sentiment(self, r: SystemHScanResult) -> float:
        """Market sentiment skoru (A'dan aynen)."""
        score = 50.0

        fr = r.funding_rate
        if fr != 0:
            fr_pct = fr * 100
            if r.direction == "LONG":
                if fr_pct < -0.05: score += min(abs(fr_pct) * 30, 10.0)
                elif fr_pct > 0.05: score -= min(fr_pct * 30, 10.0)
            else:
                if fr_pct > 0.05: score += min(fr_pct * 30, 10.0)
                elif fr_pct < -0.05: score -= min(abs(fr_pct) * 30, 10.0)

        oi_chg = r.oi_change_pct
        price_chg = r.price_change_pct
        if oi_chg != 0:
            if r.direction == "LONG":
                if oi_chg > 2 and price_chg > 0: score += min(oi_chg * 0.8, 8.0)
                elif oi_chg > 2 and price_chg < -1: score -= min(oi_chg * 0.8, 8.0)
            else:
                if oi_chg > 2 and price_chg < 0: score += min(oi_chg * 0.8, 8.0)
                elif oi_chg > 2 and price_chg > 1: score -= min(oi_chg * 0.8, 8.0)

        ob_imb = r.ob_imbalance
        if ob_imb != 0:
            if r.direction == "LONG": score += ob_imb * 8.0
            else: score += -ob_imb * 8.0

        if r.ob_wall_signal == "DOWN_BLOCKED" and r.direction == "LONG": score += 4
        elif r.ob_wall_signal == "UP_BLOCKED" and r.direction == "SHORT": score += 4

        if r.ob_liquidity >= 70: score += 4
        elif 0 < r.ob_liquidity < 30: score -= 4

        return max(0.0, min(score, 100.0))

    def _calculate_gray_zone_confirmation(self, r: SystemHScanResult, cfg: dict) -> float:
        """Gray zone confirmation (A'dan aynen)."""
        total_score = 0.0

        # 1. Trend Direction
        trend_cfg = cfg.get("trend_direction", {})
        trend_weight = trend_cfg.get("weight", 0.3)
        trend_score = 0.0

        plus_di = r.indicator_values.get("ADX_plus_DI", 0)
        minus_di = r.indicator_values.get("ADX_minus_DI", 0)
        di_diff = abs(plus_di - minus_di)
        if di_diff > trend_cfg.get("di_diff_threshold", 2.0):
            trend_score += trend_cfg.get("di_diff_points", 0.4)

        ema_fast = r.indicator_values.get("EMA_fast", 0)
        ema_slow = r.indicator_values.get("EMA_slow", 0)
        if ema_fast > 0 and ema_slow > 0:
            if (r.direction == "LONG" and ema_fast > ema_slow) or \
               (r.direction == "SHORT" and ema_fast < ema_slow):
                trend_score += trend_cfg.get("ema_cross_points", 0.3)

        macd_h = r.indicator_values.get("MACD_histogram", 0)
        if (r.direction == "LONG" and macd_h > 0) or (r.direction == "SHORT" and macd_h < 0):
            trend_score += trend_cfg.get("supertrend_points", 0.3)

        total_score += min(trend_score, 1.0) * trend_weight

        # 2. Volatility Context
        vol_cfg = cfg.get("volatility_context", {})
        vol_weight = vol_cfg.get("weight", 0.25)
        vol_score = 0.0

        bb_width = r.indicator_values.get("BB_Width", 0)
        bb_low = vol_cfg.get("bb_width_low", 2.0)
        bb_high = vol_cfg.get("bb_width_high", 4.0)
        if bb_width < bb_low: vol_score += 0.4
        elif bb_width > bb_high: vol_score += 0.2
        else: vol_score += 0.3

        atr = r.indicator_values.get("ATR", 0)
        price = r.indicator_values.get("Price", 0)
        if atr > 0 and price > 0:
            efficiency = min(abs(r.price_change_pct) / (atr / price * 100), 1.0)
            if efficiency > vol_cfg.get("efficiency_threshold", 0.7):
                vol_score += 0.3

        total_score += min(vol_score, 1.0) * vol_weight

        # 3. Volume/Momentum
        mom_cfg = cfg.get("volume_momentum", {})
        mom_weight = mom_cfg.get("weight", 0.25)
        mom_score = 0.0

        obv_slope = r.indicator_values.get("OBV_slope", 0)
        if (r.direction == "LONG" and obv_slope > mom_cfg.get("obv_slope_threshold", 0.1)) or \
           (r.direction == "SHORT" and obv_slope < -mom_cfg.get("obv_slope_threshold", 0.1)):
            mom_score += 0.4

        cmf = r.indicator_values.get("CMF", 0)
        if (r.direction == "LONG" and cmf > mom_cfg.get("cmf_threshold", 0.1)) or \
           (r.direction == "SHORT" and cmf < -mom_cfg.get("cmf_threshold", 0.1)):
            mom_score += 0.4

        macd_h = r.indicator_values.get("MACD_histogram", 0)
        if mom_cfg.get("macd_histogram_trend", True):
            if (r.direction == "LONG" and macd_h > 0) or (r.direction == "SHORT" and macd_h < 0):
                mom_score += 0.2

        total_score += min(mom_score, 1.0) * mom_weight

        # 4. Market Structure
        struct_cfg = cfg.get("market_structure", {})
        struct_weight = struct_cfg.get("weight", 0.2)
        struct_score = 0.0

        rsi = r.indicator_values.get("RSI", 50)
        if r.direction == "LONG":
            if 45 < rsi < 65: struct_score += 0.5
        else:
            if 35 < rsi < 55: struct_score += 0.5

        price_change = abs(r.price_change_pct)
        if 0.5 < price_change < 3.0: struct_score += 0.3

        total_score += min(struct_score, 1.0) * struct_weight

        return min(total_score, 1.0)

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # YARDIMCI FONKSİYONLAR
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    @staticmethod
    def _bollinger(closes: np.ndarray, period: int = 20,
                   std_mult: float = 2.0) -> tuple[float, float, float]:
        """Bollinger Bands: upper, middle, lower."""
        if len(closes) < period:
            return 0.0, 0.0, 0.0
        sma = float(np.mean(closes[-period:]))
        std = float(np.std(closes[-period:], ddof=1))
        return sma + std_mult * std, sma, sma - std_mult * std

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # PER-COIN OPTIMIZER (G'den — Faz 2.5)
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def submit_optimization(self, symbol: str, direction: str,
                            klines_5m: list) -> None:
        """Async optimizer submission. Non-blocking."""
        if symbol in self._opt_futures:
            future = self._opt_futures[symbol]
            if not future.done():
                return
        sh = self._config.get("system_h", {})
        future = self._opt_executor.submit(
            self._optimize_coin, symbol, direction, klines_5m, sh)
        self._opt_futures[symbol] = future

    def check_optimization(self, symbol: str) -> OptResult | None:
        """Check if async optimization is done. Non-blocking."""
        if symbol not in self._opt_futures:
            return None
        future = self._opt_futures[symbol]
        if not future.done():
            return None
        try:
            result = future.result()
            del self._opt_futures[symbol]
            return result
        except Exception as e:
            logger.error(f"[H Opt] Optimization failed for {symbol}: {e}")
            del self._opt_futures[symbol]
            return None

    def get_opt_cached(self, symbol: str) -> CoinOptCache | None:
        """Get cached optimization if valid."""
        cache = self._opt_cache.get(symbol)
        if not cache or not cache.valid:
            return None
        sh = self._config.get("system_h", {})
        ttl = sh.get("optimizer_cache_ttl_hours", 4) * 3600
        if time.time() - cache.timestamp > ttl:
            cache.valid = False
            return None
        return cache

    def apply_optimizer_result(self, result: SystemHScanResult,
                               opt: OptResult) -> None:
        """Optimizer sonucunu G bazlı kaldıraç ile blend et.

        Blend kuralları:
        - Optimizer liq_rate > %30 ise G bazlı kaldıraç korunur
        - Aksi halde ağırlıklı ortalama: %60 G bazlı, %40 optimizer
        - TP/SL optimizer'dan alınabilir (optimizer skoru yeterliyse)
        """
        sh = self._config.get("system_h", {})
        w_g = sh.get("optimizer_blend_weight_g", 0.6)
        w_opt = sh.get("optimizer_blend_weight_opt", 0.4)
        min_opt_score = sh.get("optimizer_min_score", 20.0)

        result.opt_result = opt
        result.opt_leverage = opt.combo.leverage
        result.opt_tp_pct = opt.combo.tp_pct
        result.opt_sl_pct = opt.combo.sl_pct
        result.opt_score = opt.score

        # Liq rate yüksekse optimizer'a güvenme
        if opt.liq_rate > 0.30:
            result.opt_status = "CACHED"
            logger.info(f"[H Opt] {result.symbol}: optimizer liq_rate={opt.liq_rate:.0%} "
                        f"too high, keeping G-based lev={result.leverage}x")
            return

        # Optimizer skoru düşükse blend etme
        if opt.score < min_opt_score:
            result.opt_status = "CACHED"
            logger.info(f"[H Opt] {result.symbol}: optimizer score={opt.score:.1f} "
                        f"< min={min_opt_score}, keeping G-based")
            return

        # Blend leverage
        g_lev = result.leverage
        opt_lev = opt.combo.leverage
        blended_lev = int(g_lev * w_g + opt_lev * w_opt)
        blended_lev = max(2, blended_lev)

        # User max sınırı
        user_max = self._config.get("strategy.max_leverage", 20)
        blended_lev = min(blended_lev, user_max)

        result.leverage = blended_lev
        result.opt_blended = True

        # TP override (optimizer varsa ve RANGING ise)
        if opt.combo.tp_pct > 0 and result.regime_zone != "TRENDING":
            old_tp = result.tp_pct
            # Optimizer TP ile G TP'nin ortalaması
            result.tp_pct = round(old_tp * w_g + opt.combo.tp_pct * w_opt, 3)

        # SL override (optimizer varsa)
        if opt.combo.sl_pct > 0:
            old_sl = result.sl_pct
            result.sl_pct = round(old_sl * w_g + opt.combo.sl_pct * w_opt, 3)

        logger.info(f"[H Opt] {result.symbol}: BLENDED lev={g_lev}x→{blended_lev}x "
                    f"(G×{w_g}+opt×{w_opt}) opt_lev={opt_lev}x "
                    f"WR={opt.win_rate:.0f}% ROI={opt.total_roi:+.1f}% "
                    f"score={opt.score:.1f}")

    def _optimize_coin(self, symbol: str, direction: str,
                       klines_5m: list, sh: dict) -> OptResult | None:
        """Per-coin 240 combo mini-backtest (G'den adapte).

        G'nin optimize_coin ile aynı mantık:
        6 leverage × 8 TP × 5 SL = 240 combo test.
        """
        opt_cfg = sh.get("optimization", {})
        fee_rate = sh.get("fee_rate", 0.0004)

        leverages = opt_cfg.get("leverages", [10, 20, 30, 50, 75, 100])
        tp_pcts = opt_cfg.get("tp_pcts", [0.3, 0.5, 0.7, 1.0, 1.5, 2.0, 2.5, 3.0])
        sl_modes = opt_cfg.get("sl_modes", ["no_sl", "0.5", "0.7", "1.0", "1.5"])

        min_trades = opt_cfg.get("min_trades_required", 5)
        max_liq = opt_cfg.get("max_liq_rate", 0.50)
        weights = opt_cfg.get("score_weights", {
            "roi": 0.35, "win_rate": 0.25, "max_drawdown": 0.20,
            "liq_rate": 0.15, "trade_count": 0.05,
        })

        if not klines_5m or len(klines_5m) < 200:
            return None

        entries = self._find_opt_entries(klines_5m, direction, sh)
        if len(entries) < min_trades:
            return None

        best_result = None
        all_results = []

        for lev in leverages:
            for tp in tp_pcts:
                for sl_str in sl_modes:
                    sl = 0.0 if sl_str == "no_sl" else float(sl_str)
                    combo = OptCombo(leverage=lev, tp_pct=tp, sl_pct=sl,
                                     sl_mode="no_sl" if sl == 0 else "fixed")

                    res = self._simulate_combo(
                        entries, klines_5m, combo, direction, fee_rate)

                    if res.trade_count < min_trades:
                        continue
                    if res.liq_rate > max_liq:
                        continue

                    res.score = self._score_opt_combo(res, weights)
                    all_results.append(res)

                    if best_result is None or res.score > best_result.score:
                        best_result = res

        if not best_result:
            return None

        all_results.sort(key=lambda r: -r.score)

        cache = CoinOptCache(
            symbol=symbol, direction=direction,
            best=best_result,
            top5=all_results[:5],
            timestamp=time.time(),
            valid=True,
        )
        self._opt_cache[symbol] = cache

        logger.info(f"[H Opt] {symbol} optimized: {best_result.combo.leverage}x "
                    f"TP={best_result.combo.tp_pct}% "
                    f"SL={'yok' if best_result.combo.sl_pct == 0 else str(best_result.combo.sl_pct) + '%'} "
                    f"ROI={best_result.total_roi:+.1f}% WR={best_result.win_rate:.0f}% "
                    f"LIQ={best_result.liq_rate*100:.0f}% "
                    f"({best_result.trade_count} trades, score={best_result.score:.1f})")

        return best_result

    def _find_opt_entries(self, klines_5m: list, direction: str,
                          sh: dict) -> list:
        """Simplified entry point detection for optimizer backtest."""
        from backtest.indicators import ema_series, macd_line_series, rsi_val as rsi_val_fn
        try:
            from backtest.tf_heatmap import _rsi_series
        except ImportError:
            _rsi_series = None

        closes = np.array([float(k[4]) for k in klines_5m])
        n = len(closes)
        if n < 50:
            return []

        ema_f = sh.get("ema_fast", 9)
        ema_s = sh.get("ema_slow", 21)
        ema9 = ema_series(closes, ema_f)
        ema21 = ema_series(closes, ema_s)

        ml = macd_line_series(closes, sh.get("macd_fast", 8), sh.get("macd_slow", 17))
        sig_line = ema_series(ml, sh.get("macd_signal", 9))
        hist = ml - sig_line

        if _rsi_series is not None:
            rsi_arr = _rsi_series(closes, sh.get("rsi_periyot", 14))
        else:
            rsi_arr = np.full(n, 50.0)

        gap_min = sh.get("ema_gap_min_pct", 0.05) / 100.0

        entries = []
        min_gap = 6

        for i in range(30, n):
            price = closes[i]
            if price <= 0:
                continue

            gap = (ema9[i] - ema21[i]) / price
            if direction == "LONG" and gap <= gap_min:
                continue
            if direction == "SHORT" and gap >= -gap_min:
                continue

            if i < 3:
                continue
            h1, h2, h3 = float(hist[i - 2]), float(hist[i - 1]), float(hist[i])
            if direction == "LONG" and not (h3 > 0 and h1 < h2 < h3):
                continue
            if direction == "SHORT" and not (h3 < 0 and h1 > h2 > h3):
                continue

            if entries and (i - entries[-1]) < min_gap:
                continue

            entries.append(i)

        return entries

    def _simulate_combo(self, entries: list, klines_5m: list,
                        combo: OptCombo, direction: str,
                        fee_rate: float) -> OptResult:
        """Simulate one combo across all entry points."""
        fee_roi = fee_rate * 200 * combo.leverage
        liq_pct = (1.0 / combo.leverage) * 70
        max_bars = 288

        trades_roi = []
        trades_bars = []
        liq_count = 0
        position_end_idx = 0

        for entry_idx in entries:
            if entry_idx < position_end_idx:
                continue

            entry_price = float(klines_5m[entry_idx][4])
            forward = klines_5m[entry_idx + 1:]

            result_str, bars, roi = self._sim_one_trade(
                direction, entry_price, forward,
                combo.tp_pct, combo.sl_pct, combo.leverage,
                fee_roi, liq_pct, max_bars)

            trades_roi.append(roi)
            trades_bars.append(bars)
            if result_str == "LIQ":
                liq_count += 1

            position_end_idx = entry_idx + bars + 1

            if liq_count >= 5 and len(trades_roi) <= 10:
                break

        if not trades_roi:
            return OptResult(combo=combo)

        total_roi = sum(trades_roi)
        wins = sum(1 for r in trades_roi if r > 0)
        wr = wins / len(trades_roi) * 100

        dd = 0.0
        max_dd = 0.0
        for r in trades_roi:
            if r < 0:
                dd += r
                max_dd = min(max_dd, dd)
            else:
                dd = 0.0

        return OptResult(
            combo=combo,
            total_roi=round(total_roi, 1),
            win_rate=round(wr, 1),
            max_drawdown=round(max_dd, 1),
            liq_rate=round(liq_count / len(trades_roi), 3) if trades_roi else 0,
            trade_count=len(trades_roi),
            avg_hold_bars=round(float(np.mean(trades_bars)), 1) if trades_bars else 0,
        )

    @staticmethod
    def _sim_one_trade(direction: str, entry_price: float,
                       forward_5m: list,
                       tp_pct: float, sl_pct: float,
                       leverage: int, fee_roi: float,
                       liq_pct: float, max_bars: int) -> tuple:
        """Simulate one trade. Returns (result_str, bars, roi_net)."""
        for i, k in enumerate(forward_5m[:max_bars]):
            high = float(k[2])
            low = float(k[3])

            if direction == "LONG":
                fav = (high - entry_price) / entry_price * 100
                adv = (entry_price - low) / entry_price * 100
            else:
                fav = (entry_price - low) / entry_price * 100
                adv = (high - entry_price) / entry_price * 100

            if sl_pct > 0 and adv >= sl_pct:
                return "SL", i + 1, -sl_pct * leverage - fee_roi
            if adv >= liq_pct:
                return "LIQ", i + 1, -100.0
            if fav >= tp_pct:
                return "TP", i + 1, tp_pct * leverage - fee_roi

        if forward_5m:
            close = float(forward_5m[min(max_bars - 1, len(forward_5m) - 1)][4])
            if direction == "LONG":
                pnl = (close - entry_price) / entry_price * 100
            else:
                pnl = (entry_price - close) / entry_price * 100
            return "TIME", min(max_bars, len(forward_5m)), pnl * leverage - fee_roi

        return "NO_DATA", 0, 0.0

    @staticmethod
    def _score_opt_combo(result: OptResult, weights: dict) -> float:
        """Score an optimization result."""
        score = 0.0
        roi_norm = min(max(result.total_roi / 500.0, -1.0), 1.0)
        score += weights.get("roi", 0.35) * roi_norm * 100
        score += weights.get("win_rate", 0.25) * result.win_rate
        dd_norm = min(abs(result.max_drawdown) / 500.0, 1.0)
        score -= weights.get("max_drawdown", 0.20) * dd_norm * 100
        score -= weights.get("liq_rate", 0.15) * result.liq_rate * 100
        tc_norm = min(result.trade_count / 20.0, 1.0)
        score += weights.get("trade_count", 0.05) * tc_norm * 100
        return round(score, 1)

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # CLIMAX + BTC BETA FİLTRELERİ (F'den)
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def check_volume_climax(self, klines: list, direction: str,
                            sh: dict) -> bool:
        """Hacim patlaması (climax) tespiti — F'den adapte.

        Climax = son mum hacim >= 2.5×MA VE son 3 mum ort >= 2×MA
        VE mum yönü trade yönüne ters (tepe/dip yakalama riski).

        Returns True if climax detected (should reject entry).
        """
        if not klines or len(klines) < 25:
            return False

        volumes = np.array([float(k[5]) for k in klines])
        vol_ma_p = sh.get("vol_climax_ma_period", 20)

        if len(volumes) < vol_ma_p + 3:
            return False

        vol_ma = float(np.mean(volumes[-(vol_ma_p + 3):-3]))
        if vol_ma <= 0:
            return False

        vol_current = float(volumes[-1])
        vol_avg3 = float(np.mean(volumes[-3:]))

        current_ratio = vol_current / vol_ma
        avg3_ratio = vol_avg3 / vol_ma

        spike_cur = sh.get("vol_spike_current_mult", 2.5)
        spike_avg3 = sh.get("vol_spike_avg3_mult", 2.0)

        spike = (current_ratio >= spike_cur) and (avg3_ratio >= spike_avg3)

        if spike and direction:
            last_candle = klines[-1]
            candle_open = float(last_candle[1])
            candle_close = float(last_candle[4])
            # Climax: hacim yüksek ama mum yönü trade yönüne ters
            if direction == "LONG" and candle_close <= candle_open:
                return True  # kırmızı mum + yüksek hacim = düşüş climaxı
            elif direction == "SHORT" and candle_close >= candle_open:
                return True  # yeşil mum + yüksek hacim = yükseliş climaxı
            # Yön uyuyorsa spike ama climax değil (momentum devam)
            return False

        return False

    @staticmethod
    def check_btc_beta_conflict(direction: str, btc_beta: float,
                                btc_direction: str, sh: dict) -> bool:
        """BTC beta korelasyon çakışma tespiti — F'den adapte.

        Yüksek beta (|beta| > threshold) + BTC yönü trade yönüne ters
        → conflict (should reject entry).

        Returns True if conflict detected.
        """
        btc_thresh = sh.get("btc_beta_threshold", 0.5)
        if abs(btc_beta) <= btc_thresh:
            return False
        if btc_direction in ("FLAT", ""):
            return False
        return direction != btc_direction

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # FAZ 5: H-SPECİFİC FİNAL SKOR (yeni — A'nın 4 bileşeni yerine)
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def compute_final_score(self, r: SystemHScanResult) -> None:
        """H'ye özel 5-bileşenli final skor (Faz 2/3/4 sonrası).

        Faz 1'deki A bazlı skor sıralama için kullanılır.
        Bu metot G, rejim, EV verileriyle zenginleştirilmiş
        final skoru hesaplar ve result.score'u üzerine yazar.

        Bileşenler:
        1. direction_strength (30%): confluence kalitesi + trend momentum
        2. ev_quality (25%): P(win)/EV istatistiksel kalitesi
        3. regime_clarity (20%): ER+Hurst rejim netliği
        4. market_context (15%): sentiment (FR, OI, OB)
        5. wave_quality (10%): dalga tutarlılığı (wave count, CV)
        """
        sh = self._config.get("system_h", {})
        weights = sh.get("score_weights", {})

        w_dir = weights.get("direction_strength", 0.30)
        w_ev = weights.get("ev_quality", 0.25)
        w_regime = weights.get("regime_clarity", 0.20)
        w_market = weights.get("market_context", 0.15)
        w_wave = weights.get("wave_quality", 0.10)

        # 1. Direction Strength (0-100)
        dir_score = self._score_trend_momentum(r)

        # 2. EV Quality (0-100)
        ev_score = 50.0
        prob = r.probability
        if prob.sufficient:
            # P(win) katkısı
            if prob.p_win >= 0.7:
                ev_score += 25
            elif prob.p_win >= 0.55:
                ev_score += 15
            elif prob.p_win >= 0.4:
                ev_score += 5
            elif prob.p_win < 0.3:
                ev_score -= 20

            # EV katkısı
            if prob.ev_pct > 30:
                ev_score += 25
            elif prob.ev_pct > 15:
                ev_score += 15
            elif prob.ev_pct > 5:
                ev_score += 8
            elif prob.ev_pct < -10:
                ev_score -= 20
            elif prob.ev_pct < 0:
                ev_score -= 10

        ev_score = max(0.0, min(ev_score, 100.0))

        # 3. Regime Clarity (0-100)
        regime_score = 50.0
        rh = r.regime_h
        if rh:
            if rh.confidence >= 1.0:
                regime_score += 30  # tam uyum (macro==micro)
            elif rh.confidence >= 0.5:
                regime_score += 15  # kısmi uyum
            else:
                regime_score -= 15  # UNDECIDED

            if rh.direction_aligned:
                regime_score += 15  # makro-mikro yön aynı
            else:
                regime_score -= 10

            # ER netliği: 0 veya 1'e yakın = net
            er_clarity = max(abs(rh.er_macro - 0.5), abs(rh.er_micro - 0.5)) * 2
            regime_score += er_clarity * 10

        regime_score = max(0.0, min(regime_score, 100.0))

        # 4. Market Context (0-100) — A'nın sentiment'i
        market_score = self._score_market_sentiment(r)

        # 5. Wave Quality (0-100)
        wave_score = 50.0
        if r.G > 0:
            cv = r.leverage_calc.cv if r.leverage_calc else 0
            wc = r.zoom.wave_count if r.zoom else 0

            if cv < 0.3:
                wave_score += 30  # çok tutarlı
            elif cv < 0.5:
                wave_score += 20
            elif cv < 0.8:
                wave_score += 10
            elif cv > 1.2:
                wave_score -= 15

            if wc >= 10:
                wave_score += 15  # bol veri
            elif wc >= 6:
                wave_score += 8
            elif wc < 4:
                wave_score -= 10

        wave_score = max(0.0, min(wave_score, 100.0))

        # Ağırlıklı toplam
        raw = (
            w_dir * dir_score +
            w_ev * ev_score +
            w_regime * regime_score +
            w_market * market_score +
            w_wave * wave_score
        )
        raw = max(0.0, min(raw, 100.0))

        if r.direction == "SHORT":
            raw = -raw

        # EV multiplier artık ayrı uygulanmıyor — ev_quality bileşeninde zaten var
        r.score = round(raw, 1)
