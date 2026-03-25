"""System B Scanner — Dalga Analizi & Salınım Ticareti (G Bazlı Tek N Sistemi).

MTF rejim tespiti (ER makro + ER mikro + Hurst), Zigzag swing tespiti,
G bazlı SL/trailing/kaldıraç hesaplama, entry teyit sistemi.

Her şey tek N değerinden (swing_n=10) ve G (geri dalga ortalaması) üzerinden türer.
"""
import math
import time
import numpy as np
from dataclasses import dataclass, field
from typing import Optional
import pandas as pd
from loguru import logger
from core.config_manager import ConfigManager


# ─────────────────────────── Data Classes ───────────────────────────

@dataclass
class SwingPoint:
    """Zigzag swing noktası."""
    index: int          # mum indeksi
    price: float        # high (SH) veya low (SL)
    type: str           # "SH" (Swing High) veya "SL" (Swing Low)
    confirmed: bool = True  # N mum sonrası teyitli mi


@dataclass
class WaveAnalysis:
    """Zigzag dalga analizi sonucu."""
    swings: list = field(default_factory=list)  # [SwingPoint, ...]
    forward_waves: list = field(default_factory=list)  # ileri dalgalar (%)
    backward_waves: list = field(default_factory=list)  # geri dalgalar (%)
    G: float = 0.0          # geri dalga ortalaması (%)
    I: float = 0.0          # ileri dalga ortalaması (%)
    forward_cv: float = 0.0  # ileri dalga CV
    backward_cv: float = 0.0  # geri dalga CV
    cv: float = 0.0          # max(forward_cv, backward_cv)
    trend_direction: str = ""  # "UP" veya "DOWN" (son dalgadan)
    wave_position: float = 0.0  # tamamlanmamış dalganın G'ye oranı (0-1+)


@dataclass
class RegimeResult:
    """MTF rejim tespiti sonucu."""
    regime: str = "UNDECIDED"   # TREND / RANGING / WEAK_TREND / WEAK_RANGING / UNDECIDED
    confidence: float = 0.0     # 0-1
    er_macro: float = 0.0
    er_micro: float = 0.0
    hurst: float = 0.5
    macro_class: str = ""       # TRENDING / TRANSITION / RANGING
    micro_class: str = ""
    hurst_class: str = ""       # TRENDING / UNCERTAIN / RANGING
    macro_direction: str = ""   # UP / DOWN
    micro_direction: str = ""
    direction_aligned: bool = True


@dataclass
class EntrySignal:
    """Giriş teyit sonucu."""
    score: int = 0              # 0-3 (kaç teyit geçti)
    rsi_ok: bool = False
    volume_ok: bool = False
    candle_ok: bool = False
    rsi_value: float = 50.0
    volume_ratio: float = 1.0
    details: str = ""


@dataclass
class RangingBand:
    """Ranging modu bant bilgisi."""
    floor: float = 0.0     # %20 percentile
    ceiling: float = 0.0   # %80 percentile
    band_pct: float = 0.0  # bant genişliği %
    position: float = 0.5  # fiyatın bant içindeki pozisyonu (0=taban, 1=tavan)


@dataclass
class SystemBScanResult:
    """System B tarama sonucu — bir coin için tüm analiz."""
    symbol: str
    score: float = 0.0          # composite score (negatif = SHORT)
    direction: str = ""         # "LONG" / "SHORT"
    eligible: bool = False
    reject_reason: str = ""

    # Rejim
    regime: RegimeResult = field(default_factory=RegimeResult)

    # Dalga analizi
    waves: WaveAnalysis = field(default_factory=WaveAnalysis)
    G: float = 0.0              # geri dalga ort (%)
    I: float = 0.0              # ileri dalga ort (%)

    # G bazlı hesaplamalar
    sl_pct: float = 0.0         # SL mesafesi (%)
    trailing_trigger_pct: float = 0.0  # trailing tetik (%)
    trailing_callback_pct: float = 0.0  # trailing geri çekilme (%)
    leverage: int = 0
    expected_rr: float = 0.0    # beklenen R:R

    # Entry teyit
    entry: EntrySignal = field(default_factory=EntrySignal)
    wave_position: float = 0.0  # dalga pozisyonu (0-1+)
    entry_type: str = ""        # "WAIT" / "LIMIT_READY" / "LIMIT_ENTER" / "MARKET_ENTER"

    # Ranging modu
    ranging_band: Optional[RangingBand] = None
    ranging_sl: float = 0.0
    ranging_tp: float = 0.0

    # Fiyat & genel
    price: float = 0.0
    atr: float = 0.0
    atr_percent: float = 0.0
    rsi: float = 50.0
    volume_ratio: float = 1.0

    # Sentiment (from market context)
    funding_rate: float = 0.0
    spread_pct: float = 0.0

    # Kaldıraç çarpanları detayı
    leverage_multipliers: dict = field(default_factory=dict)


# ─────────────────────────── Core Functions ───────────────────────────

def compute_efficiency_ratio(closes: np.ndarray) -> float:
    """Efficiency Ratio: |net hareket| / toplam hareket.
    0 = tamamen ranging, 1 = tamamen trend."""
    if len(closes) < 2:
        return 0.5
    net_move = abs(closes[-1] - closes[0])
    total_move = np.sum(np.abs(np.diff(closes)))
    if total_move == 0:
        return 0.0
    return net_move / total_move


def compute_hurst_exponent(closes: np.ndarray) -> float:
    """R/S analizi ile Hurst exponent hesaplama.
    H < 0.45 → ranging, H 0.45-0.55 → belirsiz, H > 0.55 → trend."""
    if len(closes) < 128:
        return 0.5  # yetersiz veri

    log_returns = np.diff(np.log(closes))
    ns = [16, 32, 64, 128]
    ns = [n for n in ns if n <= len(log_returns)]
    if len(ns) < 2:
        return 0.5

    rs_values = []
    for n in ns:
        rs_list = []
        num_chunks = len(log_returns) // n
        for i in range(num_chunks):
            chunk = log_returns[i * n:(i + 1) * n]
            mean_chunk = np.mean(chunk)
            deviations = np.cumsum(chunk - mean_chunk)
            R = np.max(deviations) - np.min(deviations)
            S = np.std(chunk, ddof=1)
            if S > 0:
                rs_list.append(R / S)
        if rs_list:
            rs_values.append((np.log(n), np.log(np.mean(rs_list))))

    if len(rs_values) < 2:
        return 0.5

    x = np.array([v[0] for v in rs_values])
    y = np.array([v[1] for v in rs_values])
    # Linear regression: y = H * x + c
    n_pts = len(x)
    H = (n_pts * np.sum(x * y) - np.sum(x) * np.sum(y)) / \
        (n_pts * np.sum(x ** 2) - np.sum(x) ** 2)

    return float(np.clip(H, 0.0, 1.0))


def detect_zigzag_swings(highs: np.ndarray, lows: np.ndarray,
                         n: int = 10) -> list[SwingPoint]:
    """Zigzag swing noktalarını tespit et.
    Swing High: high(i) > tüm high(i-N..i-1) VE high(i) > tüm high(i+1..i+N)
    Swing Low:  low(i)  < tüm low(i-N..i-1)  VE low(i)  < tüm low(i+1..i+N)
    Son N mum teyitsiz (sağ taraf eksik)."""
    length = len(highs)
    swings = []

    if length < 2 * n + 1:
        return swings

    # Teyitli swingleri bul (hem sol hem sağ N mum var)
    for i in range(n, length - n):
        # Swing High kontrolü
        left_highs = highs[i - n:i]
        right_highs = highs[i + 1:i + n + 1]
        if highs[i] > np.max(left_highs) and highs[i] > np.max(right_highs):
            swings.append(SwingPoint(index=i, price=float(highs[i]),
                                     type="SH", confirmed=True))

        # Swing Low kontrolü
        left_lows = lows[i - n:i]
        right_lows = lows[i + 1:i + n + 1]
        if lows[i] < np.min(left_lows) and lows[i] < np.min(right_lows):
            swings.append(SwingPoint(index=i, price=float(lows[i]),
                                     type="SL", confirmed=True))

    # Kronolojik sırala
    swings.sort(key=lambda s: s.index)

    # Ardışık aynı tip swing'leri temizle (en uç olanı tut)
    cleaned = []
    for s in swings:
        if cleaned and cleaned[-1].type == s.type:
            # Aynı tipten ardışık → en uygun olanı tut
            if s.type == "SH" and s.price > cleaned[-1].price:
                cleaned[-1] = s
            elif s.type == "SL" and s.price < cleaned[-1].price:
                cleaned[-1] = s
        else:
            cleaned.append(s)

    return cleaned


def analyze_waves(swings: list[SwingPoint], current_price: float = 0.0
                  ) -> WaveAnalysis:
    """Swing noktalarından dalga analizi yap.
    İleri ve geri dalgaları ayır, G ve I hesapla."""
    result = WaveAnalysis(swings=swings)

    if len(swings) < 3:
        return result

    # Trend yönünü son 2 teyitli swing'den belirle
    last_two = swings[-2:]
    if last_two[-1].type == "SH" and last_two[-2].type == "SL":
        trend_dir = "UP"
    elif last_two[-1].type == "SL" and last_two[-2].type == "SH":
        trend_dir = "DOWN"
    else:
        # Aynı tip — fiyat hareketinden belirle
        trend_dir = "UP" if last_two[-1].price > last_two[-2].price else "DOWN"
    result.trend_direction = trend_dir

    # Dalga boylarını hesapla
    forward_waves = []  # trend yönünde
    backward_waves = []  # trend tersine

    for i in range(1, len(swings)):
        prev = swings[i - 1]
        curr = swings[i]
        wave_pct = abs(curr.price - prev.price) / prev.price * 100

        if wave_pct < 0.001:  # sıfıra çok yakın dalgaları atla
            continue

        # Dalga yönünü belirle
        is_up = curr.price > prev.price

        if trend_dir == "UP":
            if is_up:
                forward_waves.append(wave_pct)
            else:
                backward_waves.append(wave_pct)
        else:  # DOWN
            if is_up:
                backward_waves.append(wave_pct)
            else:
                forward_waves.append(wave_pct)

    result.forward_waves = forward_waves
    result.backward_waves = backward_waves

    if backward_waves:
        result.G = float(np.mean(backward_waves))
    if forward_waves:
        result.I = float(np.mean(forward_waves))

    # CV hesaplama
    if len(forward_waves) >= 2:
        result.forward_cv = float(np.std(forward_waves) / np.mean(forward_waves))
    if len(backward_waves) >= 2:
        result.backward_cv = float(np.std(backward_waves) / np.mean(backward_waves))
    result.cv = max(result.forward_cv, result.backward_cv)

    # Tamamlanmamış son dalga → dalga pozisyonu (teyitsiz swing tahmini)
    if current_price > 0 and swings:
        last_swing = swings[-1]
        current_wave_pct = abs(current_price - last_swing.price) / last_swing.price * 100
        if result.G > 0:
            result.wave_position = current_wave_pct / result.G
        else:
            result.wave_position = 0.0

    return result


# ─────────────────────────── Scanner Class ───────────────────────────

class SystemBScanner:
    """System B ana tarama motoru.

    MTF rejim → Zigzag → G hesaplama → Kaldıraç → Entry teyit → Skor.
    """

    def __init__(self, config: ConfigManager):
        self._config = config

    def _cfg(self, key: str, default=None):
        """system_b config'den oku."""
        return self._config.get(f"system_b.{key}", default)

    # ─── MTF Rejim Tespiti ───

    def compute_regime(self, klines_macro: pd.DataFrame,
                       klines_micro: pd.DataFrame) -> RegimeResult:
        """Çoklu zaman dilimi rejim tespiti.
        klines_macro: 1h mumlar (168 adet)
        klines_micro: 5m mumlar (288 adet)"""
        result = RegimeResult()

        # ER makro (1h, 168 mum = 7 gün)
        if klines_macro is not None and len(klines_macro) >= 20:
            closes_macro = klines_macro["close"].values.astype(float)
            result.er_macro = compute_efficiency_ratio(closes_macro)

            er_ranging = self._cfg("er_makro_ranging", 0.15)
            er_trending = self._cfg("er_makro_trending", 0.35)
            if result.er_macro < er_ranging:
                result.macro_class = "RANGING"
            elif result.er_macro > er_trending:
                result.macro_class = "TRENDING"
            else:
                result.macro_class = "TRANSITION"

            # Makro yön: ilk açılış vs son kapanış
            result.macro_direction = "UP" if closes_macro[-1] > closes_macro[0] else "DOWN"

        # ER mikro (5m, 288 mum = 1 gün)
        if klines_micro is not None and len(klines_micro) >= 20:
            closes_micro = klines_micro["close"].values.astype(float)
            result.er_micro = compute_efficiency_ratio(closes_micro)

            er_ranging = self._cfg("er_mikro_ranging", 0.2)
            er_trending = self._cfg("er_mikro_trending", 0.4)
            if result.er_micro < er_ranging:
                result.micro_class = "RANGING"
            elif result.er_micro > er_trending:
                result.micro_class = "TRENDING"
            else:
                result.micro_class = "TRANSITION"

            # Mikro yön: son N mumun açılış vs kapanış
            yon_mum = self._cfg("yakin_yon_mum_sayisi", 72)
            recent = closes_micro[-min(yon_mum, len(closes_micro)):]
            result.micro_direction = "UP" if recent[-1] > recent[0] else "DOWN"

            # Hurst (5m, bilgilendirme amaçlı)
            result.hurst = compute_hurst_exponent(closes_micro)
            h_ranging = self._cfg("hurst_ranging_esik", 0.45)
            h_trending = self._cfg("hurst_trending_esik", 0.55)
            if result.hurst < h_ranging:
                result.hurst_class = "RANGING"
            elif result.hurst > h_trending:
                result.hurst_class = "TRENDING"
            else:
                result.hurst_class = "UNCERTAIN"

        # Yön teyidi
        result.direction_aligned = (
            result.macro_direction == result.micro_direction
            or not result.macro_direction
            or not result.micro_direction
        )

        # MTF 4 Kural Matrisi
        macro = result.macro_class
        micro = result.micro_class

        if not macro or not micro:
            result.regime = "UNDECIDED"
            result.confidence = 0.0
        elif macro == micro:
            # Kural 1: ikisi aynı → o rejim (tam güven)
            if macro == "TRENDING":
                result.regime = "TREND"
                result.confidence = 1.0
            elif macro == "RANGING":
                result.regime = "RANGING"
                result.confidence = 1.0
            else:
                result.regime = "UNDECIDED"
                result.confidence = 0.0
        elif macro == "TRANSITION" and micro != "TRANSITION":
            # Kural 2: biri GEÇİŞ, diğeri net → zayıf versiyon
            if micro == "TRENDING":
                result.regime = "WEAK_TREND"
                result.confidence = 0.5
            else:
                result.regime = "WEAK_RANGING"
                result.confidence = 0.5
        elif micro == "TRANSITION" and macro != "TRANSITION":
            # Kural 2 (ters): makro net, mikro GEÇİŞ
            if macro == "TRENDING":
                result.regime = "WEAK_TREND"
                result.confidence = 0.5
            else:
                result.regime = "WEAK_RANGING"
                result.confidence = 0.5
        elif macro == "TRANSITION" and micro == "TRANSITION":
            # Kural 4: ikisi de GEÇİŞ → KARARSIZ
            result.regime = "UNDECIDED"
            result.confidence = 0.0
        else:
            # Kural 3: çelişiyor → KARARSIZ
            result.regime = "UNDECIDED"
            result.confidence = 0.0

        return result

    # ─── Entry Teyit ───

    def compute_entry_signal(self, klines: pd.DataFrame, direction: str,
                             regime: str) -> EntrySignal:
        """3 teyit kontrolü: RSI + Volume + Dönüş mumu.
        En az 2/3 gerekli."""
        result = EntrySignal()

        if klines is None or len(klines) < 30:
            return result

        closes = klines["close"].values.astype(float)
        opens = klines["open"].values.astype(float)
        highs = klines["high"].values.astype(float)
        lows = klines["low"].values.astype(float)
        volumes = klines["volume"].values.astype(float)

        # ── Teyit 1: RSI ──
        rsi_period = self._cfg("rsi_periyot", 14)
        rsi = self._compute_rsi(closes, rsi_period)
        result.rsi_value = rsi

        is_trend = regime in ("TREND", "WEAK_TREND")
        if direction == "LONG":
            threshold = self._cfg("rsi_long_esik", 40) if is_trend else self._cfg("rsi_ranging_long_esik", 35)
            result.rsi_ok = rsi < threshold
        else:
            threshold = self._cfg("rsi_short_esik", 60) if is_trend else self._cfg("rsi_ranging_short_esik", 65)
            result.rsi_ok = rsi > threshold

        # ── Teyit 2: Volume ──
        vol_ma_period = self._cfg("volume_ma_periyot", 20)
        if len(volumes) >= vol_ma_period + 3:
            vol_ma = np.mean(volumes[-vol_ma_period:])
            recent_3_avg = np.mean(volumes[-3:])
            last_vol = volumes[-1]
            result.volume_ratio = float(last_vol / vol_ma) if vol_ma > 0 else 1.0

            azalma = self._cfg("volume_azalma_carpani", 0.8)
            climax = self._cfg("volume_climax_carpani", 1.5)

            # Tükenme: son 3 mum ort < MA × 0.8
            if recent_3_avg < vol_ma * azalma:
                result.volume_ok = True
            # Climax: son mum > MA × 1.5 + dönüş yönü mum
            elif last_vol > vol_ma * climax:
                last_candle_dir = "UP" if closes[-1] > opens[-1] else "DOWN"
                if (direction == "LONG" and last_candle_dir == "UP") or \
                   (direction == "SHORT" and last_candle_dir == "DOWN"):
                    result.volume_ok = True

        # ── Teyit 3: Dönüş mumu ──
        if len(closes) >= 1:
            c = closes[-1]
            o = opens[-1]
            h = highs[-1]
            l = lows[-1]
            body = abs(c - o)
            candle_range = h - l if h > l else 0.001

            if direction == "LONG":
                # Yeşil mum, bonus: alt fitil uzun (hammer)
                result.candle_ok = c > o
            else:
                # Kırmızı mum, bonus: üst fitil uzun (shooting star)
                result.candle_ok = c < o

        # Skor
        result.score = sum([result.rsi_ok, result.volume_ok, result.candle_ok])
        details = []
        if result.rsi_ok:
            details.append(f"RSI={rsi:.0f}")
        if result.volume_ok:
            details.append(f"Vol={result.volume_ratio:.1f}x")
        if result.candle_ok:
            details.append("Candle")
        result.details = ", ".join(details) if details else "none"

        return result

    def _compute_rsi(self, closes: np.ndarray, period: int = 14) -> float:
        """Wilder's RSI (EMA bazli, diger sistemlerle tutarli)."""
        if len(closes) < period + 1:
            return 50.0
        deltas = np.diff(closes)
        gains = np.where(deltas > 0, deltas, 0.0)
        losses = np.where(deltas < 0, -deltas, 0.0)
        avg_gain = np.mean(gains[:period])
        avg_loss = np.mean(losses[:period])
        for i in range(period, len(gains)):
            avg_gain = (avg_gain * (period - 1) + gains[i]) / period
            avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return float(100.0 - (100.0 / (1.0 + rs)))

    # ─── Ranging Modu ───

    def compute_ranging_band(self, klines: pd.DataFrame) -> Optional[RangingBand]:
        """Ranging bant tespiti: %20 ve %80 percentile."""
        if klines is None or len(klines) < 50:
            return None

        highs = klines["high"].values.astype(float)
        lows = klines["low"].values.astype(float)
        closes = klines["close"].values.astype(float)

        # 864 fiyat noktası (3×288 = high, low, close)
        all_prices = np.concatenate([highs, lows, closes])

        alt_pct = self._cfg("ranging_bant_alt_percentile", 20)
        ust_pct = self._cfg("ranging_bant_ust_percentile", 80)
        floor_price = float(np.percentile(all_prices, alt_pct))
        ceiling_price = float(np.percentile(all_prices, ust_pct))

        if floor_price <= 0:
            return None

        band_pct = (ceiling_price - floor_price) / floor_price * 100
        min_band = self._cfg("min_bant_genisligi", 0.3)
        if band_pct < min_band:
            return None

        current_price = float(closes[-1])
        position = (current_price - floor_price) / (ceiling_price - floor_price) \
            if ceiling_price > floor_price else 0.5

        return RangingBand(
            floor=floor_price,
            ceiling=ceiling_price,
            band_pct=band_pct,
            position=float(np.clip(position, 0.0, 1.0))
        )

    def check_ranging_breakout(self, klines: pd.DataFrame,
                               band: RangingBand) -> bool:
        """Son N mum bant dışı mı (breakout riski)?"""
        if klines is None or band is None:
            return False
        n = self._cfg("breakout_mum_sayisi", 5)
        closes = klines["close"].values.astype(float)
        recent = closes[-min(n, len(closes)):]
        outside = sum(1 for c in recent if c > band.ceiling or c < band.floor)
        return outside > 0

    def check_ranging_reentry(self, klines: pd.DataFrame,
                              band: RangingBand) -> bool:
        """Breakout sonrası N mum bant içi → tekrar girilebilir."""
        if klines is None or band is None:
            return True
        n = self._cfg("breakout_teyit_mumlar", 3)
        closes = klines["close"].values.astype(float)
        recent = closes[-min(n, len(closes)):]
        inside = all(band.floor <= c <= band.ceiling for c in recent)
        return inside

    # ─── Kaldıraç Hesaplama ───

    def compute_leverage(self, G: float, regime: RegimeResult,
                         entry_score: int, cv: float,
                         funding_rate: float = 0.0,
                         is_ranging: bool = False,
                         band: Optional[RangingBand] = None) -> tuple[int, dict]:
        """G bazlı kaldıraç hesapla (fee-aware).
        Returns: (leverage, multiplier_details)"""
        slippage = self._cfg("slippage_buffer", 0.1)

        if is_ranging and band:
            # Ranging: SL = bant genişliğinin %20'si (giriş → taban/tavan)
            g_ranging = band.band_pct * 0.20
            sl_pct = g_ranging + slippage
        else:
            # Trend: SL = 1.5 × G
            sl_carpan = self._cfg("sl_carpan", 1.5)
            sl_pct = sl_carpan * G + slippage

        if sl_pct <= 0:
            return 0, {"reason": "SL_zero"}

        fee_rate = self._cfg("fee_rate", 0.0004)
        fee_pct = fee_rate * 100 * 2  # round-trip (0.08% = 0.04% × 2)

        K = 35.0 / (sl_pct + fee_pct)

        # Çarpanlar
        multipliers = {}
        total_mult = 1.0

        # Zayıf rejim güveni
        if regime.confidence < 1.0 and regime.regime in ("WEAK_TREND", "WEAK_RANGING"):
            mult = self._cfg("zayif_kaldirac_carpani", 0.5)
            multipliers["weak_regime"] = mult
            total_mult *= mult

        # Yön çelişkisi
        if not regime.direction_aligned:
            mult = self._cfg("yon_celiskisi_carpani", 0.5)
            multipliers["direction_conflict"] = mult
            total_mult *= mult

        # CV orta
        cv_max = self._cfg("cv_max_esik", 0.6)
        if 0.3 <= cv < cv_max:
            mult = self._cfg("cv_orta_carpani", 0.7)
            multipliers["cv_medium"] = mult
            total_mult *= mult

        # Entry teyit skoru = 1 (2 olmalı ama 1 de kabul)
        if entry_score == 1:
            mult = self._cfg("entry_tek_teyit_carpani", 0.7)
            multipliers["single_confirm"] = mult
            total_mult *= mult

        # Funding rate karşıt
        fr_threshold = self._cfg("funding_uyari_esik", 0.0005)
        if abs(funding_rate) > fr_threshold:
            mult = self._cfg("funding_carpani", 0.7)
            multipliers["funding_adverse"] = mult
            total_mult *= mult

        K_final = K * total_mult
        K_int = max(1, int(K_final))

        min_lev = self._cfg("min_kaldirac", 2)
        if K_int < min_lev:
            return 0, {"reason": f"leverage_too_low_{K_int}x<{min_lev}x",
                        "raw_K": K, "multipliers": multipliers}

        return K_int, multipliers

    # ─── Ana Skor Fonksiyonu ───

    def score_symbol(self, symbol: str,
                     klines_macro: pd.DataFrame,
                     klines_micro: pd.DataFrame,
                     market_context: dict = None) -> SystemBScanResult:
        """Tek coin için tam System B analizi.
        klines_macro: 1h mumlar, klines_micro: 5m mumlar."""
        result = SystemBScanResult(symbol=symbol)

        if market_context:
            result.funding_rate = market_context.get("funding_rate", 0.0)
            result.spread_pct = market_context.get("spread_pct", 0.0)

        # Veri kontrolü
        min_micro = 50
        if klines_micro is None or len(klines_micro) < min_micro:
            result.reject_reason = "insufficient_micro_data"
            return result

        closes = klines_micro["close"].values.astype(float)
        highs = klines_micro["high"].values.astype(float)
        lows = klines_micro["low"].values.astype(float)
        volumes = klines_micro["volume"].values.astype(float)
        result.price = float(closes[-1])

        # ATR (14 periyot, 5m)
        atr_period = 14
        if len(closes) > atr_period:
            tr = np.maximum(
                highs[-atr_period:] - lows[-atr_period:],
                np.maximum(
                    np.abs(highs[-atr_period:] - np.append(closes[-atr_period - 1:-atr_period], closes[-atr_period:-1])),
                    np.abs(lows[-atr_period:] - np.append(closes[-atr_period - 1:-atr_period], closes[-atr_period:-1]))
                )
            )
            result.atr = float(np.mean(tr))
            if result.price > 0:
                result.atr_percent = result.atr / result.price * 100

        # ── 1. MTF Rejim Tespiti ──
        regime = self.compute_regime(klines_macro, klines_micro)
        result.regime = regime

        if regime.regime == "UNDECIDED":
            result.reject_reason = "regime_undecided"
            return result

        # ── 2. Zigzag Swing Tespiti ──
        swing_n = self._cfg("swing_n", 10)
        swings = detect_zigzag_swings(highs, lows, n=swing_n)
        waves = analyze_waves(swings, current_price=result.price)
        result.waves = waves

        G = waves.G
        I = waves.I
        result.G = G
        result.I = I

        # Minimum dalga sayısı kontrolü
        min_wave_count = self._cfg("min_dalga_sayisi", 2)
        if len(waves.forward_waves) < min_wave_count or \
           len(waves.backward_waves) < min_wave_count:
            result.reject_reason = f"insufficient_waves_{len(waves.forward_waves)}f_{len(waves.backward_waves)}b"
            return result

        # G minimum kontrolü
        min_g = self._cfg("min_dalga_boyu", 0.1)
        if G < min_g:
            result.reject_reason = f"G_too_small_{G:.3f}%<{min_g}%"
            return result

        # CV filtresi
        cv_max = self._cfg("cv_max_esik", 0.6)
        if waves.cv > cv_max:
            result.reject_reason = f"CV_too_high_{waves.cv:.2f}>{cv_max}"
            return result

        # ── 3. Rejim bazlı analiz ──
        is_ranging = regime.regime in ("RANGING", "WEAK_RANGING")

        if is_ranging:
            # RANGING MODU
            band = self.compute_ranging_band(klines_micro)
            if not band:
                result.reject_reason = "ranging_band_too_narrow"
                return result
            result.ranging_band = band

            # Breakout kontrolü
            if self.check_ranging_breakout(klines_micro, band):
                if not self.check_ranging_reentry(klines_micro, band):
                    result.reject_reason = "breakout_risk"
                    return result

            # Yön belirleme (bant pozisyonuna göre)
            long_entry = self._cfg("ranging_long_giris_seviye", 0.20)
            short_entry = self._cfg("ranging_short_giris_seviye", 0.80)

            if band.position <= long_entry:
                direction = "LONG"
            elif band.position >= short_entry:
                direction = "SHORT"
            else:
                result.reject_reason = f"ranging_mid_band_{band.position:.0%}"
                return result

            result.direction = direction

            # Ranging SL/TP
            # SL: bant sınırı, ama giriş fiyatından minimum 1.5×G uzakta olmalı
            sl_carpan = self._cfg("sl_carpan", 1.5)
            min_sl_offset = result.price * (sl_carpan * G / 100) if G > 0 else result.price * 0.01

            if direction == "LONG":
                band_sl = band.floor  # taban (%0)
                max_sl = result.price - min_sl_offset  # minimum SL mesafesi
                result.ranging_sl = min(band_sl, max_sl)  # daha uzak olanı seç
                tp_level = self._cfg("ranging_long_cikis_seviye", 0.60)
                result.ranging_tp = band.floor + (band.ceiling - band.floor) * tp_level
            else:
                band_sl = band.ceiling  # tavan (%100)
                min_sl = result.price + min_sl_offset  # minimum SL mesafesi
                result.ranging_sl = max(band_sl, min_sl)  # daha uzak olanı seç
                tp_level = self._cfg("ranging_short_cikis_seviye", 0.40)
                result.ranging_tp = band.floor + (band.ceiling - band.floor) * tp_level

        else:
            # TREND MODU
            # Yön: dalga trendinden + rejim yönünden
            if waves.trend_direction == "UP":
                direction = "LONG"
            elif waves.trend_direction == "DOWN":
                direction = "SHORT"
            else:
                result.reject_reason = "no_trend_direction"
                return result
            result.direction = direction

            # G bazlı SL / Trailing
            sl_carpan = self._cfg("sl_carpan", 1.5)
            tetik_carpan = self._cfg("tetik_carpan", 2.5)
            trail_carpan = self._cfg("trail_carpan", 0.5)

            result.sl_pct = sl_carpan * G
            result.trailing_trigger_pct = tetik_carpan * G
            result.trailing_callback_pct = trail_carpan * G

            # Trailing callback Binance sınırları
            min_cb = self._cfg("trailing_min_callback", 0.1)
            max_cb = self._cfg("trailing_max_callback", 5.0)
            clamped_cb = max(min_cb, min(max_cb, result.trailing_callback_pct))

            # Eğer clamp sonrası trail >= tetik → sabit TP kullan
            if clamped_cb >= result.trailing_trigger_pct:
                # Trailing kullanılamaz → sabit TP
                result.trailing_callback_pct = 0.0
                result.trailing_trigger_pct = 0.0
                # TP = I × 0.8
                # ranging_tp alanını kullan (sabit TP)
                if direction == "LONG":
                    result.ranging_tp = result.price * (1 + I * 0.8 / 100)
                else:
                    result.ranging_tp = result.price * (1 - I * 0.8 / 100)
            else:
                result.trailing_callback_pct = clamped_cb

            # R:R filtresi
            min_rr = self._cfg("min_rr_oran", 1.3)
            trail = result.trailing_callback_pct if result.trailing_callback_pct > 0 else 0.0
            expected_profit = I * 0.8 - trail
            risk = sl_carpan * G
            if risk > 0 and expected_profit > 0:
                result.expected_rr = expected_profit / risk
            if result.expected_rr < min_rr:
                result.reject_reason = f"RR_too_low_{result.expected_rr:.2f}<{min_rr}"
                return result

        # ── 4. Spread filtresi ──
        max_spread_ratio = self._cfg("max_spread_sl_oran", 0.1)
        if result.spread_pct > 0 and result.sl_pct > 0:
            if result.spread_pct > result.sl_pct * max_spread_ratio:
                result.reject_reason = f"spread_too_wide_{result.spread_pct:.3f}%"
                return result

        # ── 5. Funding filtresi ──
        max_fr = self._cfg("max_funding_rate", 0.001)
        if abs(result.funding_rate) > max_fr:
            result.reject_reason = f"extreme_funding_{result.funding_rate*100:.3f}%"
            return result

        # ── 6. Entry teyit ──
        entry = self.compute_entry_signal(klines_micro, direction, regime.regime)
        result.entry = entry
        result.rsi = entry.rsi_value
        result.volume_ratio = entry.volume_ratio

        min_entry_score = self._cfg("min_entry_skor", 2)
        if entry.score < 1:
            result.reject_reason = f"no_entry_confirmation_{entry.score}/3"
            return result

        # ── 7. Dalga pozisyonu → giriş tipi ──
        wp = waves.wave_position
        result.wave_position = wp
        bekle = self._cfg("dalga_pozisyon_bekle", 0.30)
        limit_p = self._cfg("dalga_pozisyon_limit", 0.60)
        gir = self._cfg("dalga_pozisyon_gir", 0.90)

        if is_ranging:
            # Ranging: bant pozisyonu zaten entry seviyesini belirliyor
            if entry.score >= min_entry_score:
                result.entry_type = "LIMIT_ENTER"
            elif entry.score >= 1:
                result.entry_type = "LIMIT_READY"
            else:
                result.entry_type = "WAIT"
        else:
            # Trend: dalga pozisyonuna göre
            if wp < bekle:
                result.entry_type = "WAIT"
            elif wp < limit_p:
                if entry.score >= 1:
                    result.entry_type = "LIMIT_READY"
                else:
                    result.entry_type = "WAIT"
            elif wp < gir:
                if entry.score >= min_entry_score:
                    result.entry_type = "LIMIT_ENTER"
                else:
                    result.entry_type = "WAIT"
            else:
                if entry.score >= min_entry_score:
                    result.entry_type = "MARKET_ENTER"
                else:
                    result.entry_type = "WAIT"

        if result.entry_type == "WAIT":
            result.reject_reason = f"wave_pos_{wp:.0%}_entry_{entry.score}/3"
            return result

        # ── 8. Kaldıraç ──
        lev, lev_details = self.compute_leverage(
            G=G, regime=regime, entry_score=entry.score,
            cv=waves.cv, funding_rate=result.funding_rate,
            is_ranging=is_ranging, band=result.ranging_band
        )
        result.leverage = lev
        result.leverage_multipliers = lev_details

        if lev <= 0:
            result.reject_reason = f"leverage_rejected: {lev_details.get('reason', '')}"
            return result

        # ── 9. Skor ──
        # Basit skor: tüm filtreleri geçti → eligible
        # Skor bileşenleri: entry_score, wave_position, R:R, rejim güveni
        base_score = 50.0
        base_score += entry.score * 10     # 0-30 puan (entry teyit)
        base_score += min(wp, 1.5) * 10    # 0-15 puan (dalga pozisyonu)
        if result.expected_rr > 0:
            base_score += min(result.expected_rr, 3.0) * 5  # 0-15 puan (R:R)
        base_score += regime.confidence * 10  # 0-10 puan (rejim güveni)

        if direction == "SHORT":
            base_score = -base_score

        result.score = round(base_score, 1)
        result.eligible = True

        return result

    def score_batch(self, symbols: list[str],
                    klines_macro_map: dict[str, pd.DataFrame],
                    klines_micro_map: dict[str, pd.DataFrame],
                    market_context: dict[str, dict] = None
                    ) -> list[SystemBScanResult]:
        """Toplu tarama — tüm coinleri skorla ve sırala."""
        results = []
        for sym in symbols:
            macro = klines_macro_map.get(sym)
            micro = klines_micro_map.get(sym)
            ctx = market_context.get(sym, {}) if market_context else {}
            try:
                r = self.score_symbol(sym, macro, micro, ctx)
                results.append(r)
            except Exception as e:
                logger.warning(f"System B score failed for {sym}: {e}")
                results.append(SystemBScanResult(
                    symbol=sym, reject_reason=f"error: {e}"))

        # Sırala: eligible olanlar önce, skor büyüklüğüne göre
        results.sort(key=lambda r: (r.eligible, abs(r.score)), reverse=True)
        return results
