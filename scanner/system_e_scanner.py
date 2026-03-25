"""System E Scanner — Yüksek Kaldıraç Yön Kesinliği Stratejisi.

Top 50 coin (24h hacme göre), tüm timeframe'lerde (5m, 15m, 1h, 4h, 1d)
aynı yöne sinyal veren en güçlü coin seçilir.
- Tüm TF'ler aynı yöne sinyal vermeli (5/5 uyum)
- En yüksek kaldıraç kullanılır
- SL yok, sadece emergency (%80 likidasyon)
- Trailing stop: %50 tetik, %10 callback
- Hızlı giriş, kâr alıp kaçma stratejisi
"""
import numpy as np
from dataclasses import dataclass, field
from loguru import logger
from core.config_manager import ConfigManager


# ─────────────────────────── Data Classes ───────────────────────────

SYSTEM_E_TIMEFRAMES = [
    ("5m", 5), ("15m", 15), ("1h", 60), ("4h", 240), ("1d", 1440),
]


@dataclass
class TFSignal:
    """Tek bir timeframe'in sinyal detayları."""
    timeframe: str = ""
    ema_vote: float = 0.0       # +1 LONG, -1 SHORT
    macd_vote: float = 0.0
    rsi_vote: float = 0.0
    adx_vote: float = 0.0       # +1 trending, 0 flat
    volume_vote: float = 0.0    # +1 hacim artıyor
    score: float = 0.0          # ortalama (-1 ile +1)
    direction: str = "FLAT"     # LONG / SHORT / FLAT
    rsi_value: float = 50.0
    adx_value: float = 0.0
    macd_hist: float = 0.0
    ema_fast: float = 0.0
    ema_slow: float = 0.0
    atr: float = 0.0
    confidence: float = 0.0     # 0-1, sinyal güvenilirliği


@dataclass
class SystemEScanResult:
    """System E tarama sonucu — bir coin için tüm analiz."""
    symbol: str = ""
    rank: int = 0               # hacim sıralaması
    volume_24h: float = 0.0

    # TF sinyalleri
    tf_signals: list = field(default_factory=list)  # [TFSignal, ...]
    aligned_count: int = 0      # kaç TF aynı yönde (hedef: 5/5)
    total_tfs: int = 0          # toplam TF sayısı

    # Yön
    direction: str = ""         # "LONG" / "SHORT" / "SKIP"
    direction_strength: float = 0.0  # 0-1, ortalama sinyal gücü

    # Kaldıraç & Risk
    leverage: int = 1
    max_leverage: int = 125     # Binance max
    emergency_sl_pct: float = 0.0  # likidasyon %80 mesafesi

    # Trailing
    trailing_trigger_pct: float = 50.0   # %50 kârda tetikle
    trailing_callback_pct: float = 10.0  # %10 callback

    # Entry
    entry_price: float = 0.0
    price: float = 0.0
    atr: float = 0.0
    atr_pct: float = 0.0
    funding_rate: float = 0.0

    # Karar
    eligible: bool = False
    reject_reason: str = ""
    score: float = 0.0          # composite score


# ─────────────────────────── Core Scanner ───────────────────────────

class SystemEScanner:
    """System E: Yüksek kaldıraç + yön kesinliği."""

    def __init__(self, config: ConfigManager):
        self._config = config

    # ────── Public API ──────

    def analyze_symbol(self, symbol: str,
                       klines_by_tf: dict[str, list],
                       funding_rate: float = 0.0,
                       rank: int = 0, volume_24h: float = 0.0
                       ) -> SystemEScanResult:
        """Tek bir coin için 5 TF analizi."""
        se = self._config.get("system_e", {})
        result = SystemEScanResult(symbol=symbol, rank=rank, volume_24h=volume_24h)
        result.funding_rate = funding_rate

        # Her TF'de sinyal hesapla
        tf_signals = []
        for tf_name, tf_min in SYSTEM_E_TIMEFRAMES:
            klines = klines_by_tf.get(tf_name, [])
            if not klines or len(klines) < 30:
                continue
            sig = self._analyze_tf(klines, tf_name, se)
            tf_signals.append(sig)

        result.tf_signals = tf_signals
        result.total_tfs = len(tf_signals)

        if result.total_tfs < 3:
            result.reject_reason = "insufficient_tf_data"
            return result

        # Yön uyumu kontrolü: TÜM TF'ler aynı yönde olmalı
        long_count = sum(1 for s in tf_signals if s.direction == "LONG")
        short_count = sum(1 for s in tf_signals if s.direction == "SHORT")

        min_alignment = se.get("min_tf_uyum", result.total_tfs)  # varsayılan: tümü

        if long_count >= min_alignment:
            result.direction = "LONG"
            result.aligned_count = long_count
        elif short_count >= min_alignment:
            result.direction = "SHORT"
            result.aligned_count = short_count
        else:
            result.direction = "SKIP"
            result.aligned_count = max(long_count, short_count)
            result.reject_reason = f"alignment_{result.aligned_count}/{result.total_tfs}"
            return result

        # Ortalama sinyal gücü
        aligned_signals = [s for s in tf_signals if s.direction == result.direction]
        result.direction_strength = sum(s.confidence for s in aligned_signals) / len(aligned_signals)

        # Min güç kontrolü
        min_strength = se.get("min_sinyal_gucu", 0.5)
        if result.direction_strength < min_strength:
            result.reject_reason = f"weak_signal_{result.direction_strength:.2f}"
            return result

        # Funding rate kontrolü
        max_fr = se.get("max_funding_rate", 0.001)
        if funding_rate > max_fr and result.direction == "LONG":
            result.reject_reason = "high_funding_long"
            return result
        if funding_rate < -max_fr and result.direction == "SHORT":
            result.reject_reason = "high_funding_short"
            return result

        # Kaldıraç: en yüksek (config max)
        result.max_leverage = se.get("max_kaldirac", 125)
        result.leverage = result.max_leverage

        # Emergency SL: likidasyon mesafesinin %80'i
        liq_factor = se.get("liq_carpani", 0.70)  # pratik likidasyon faktörü
        emergency_pct = se.get("emergency_liq_pct", 80) / 100.0
        # Likidasyon mesafesi = (1/leverage) × liq_factor
        liq_distance_pct = (1.0 / result.leverage) * liq_factor * 100
        result.emergency_sl_pct = liq_distance_pct * emergency_pct

        # Trailing: ROI bazlı → fiyat yüzdesine dönüştür
        # Kullanıcı %50 ROI kâr istiyor, %10 ROI callback istiyor
        # ROI% = fiyat_hareket% × kaldıraç → fiyat% = ROI% / kaldıraç
        trailing_roi_trigger = se.get("trailing_tetik_pct", 50.0)   # %50 ROI
        trailing_roi_callback = se.get("trailing_callback_pct", 10.0)  # %10 ROI

        lev = result.leverage
        # ROI → fiyat dönüşümü
        result.trailing_trigger_pct = trailing_roi_trigger / lev   # fiyat %'si
        result.trailing_callback_pct = trailing_roi_callback / lev  # fiyat %'si

        # Binance callback sınırı: 0.1% - 5.0%
        result.trailing_callback_pct = max(0.1, min(result.trailing_callback_pct, 5.0))

        # Entry fiyatı (5m TF'den)
        micro_klines = klines_by_tf.get("5m", [])
        if micro_klines:
            closes = [float(k[4]) for k in micro_klines]
            highs = [float(k[2]) for k in micro_klines]
            lows = [float(k[3]) for k in micro_klines]
            result.price = closes[-1]
            result.atr = self._atr(
                np.array(highs), np.array(lows), np.array(closes), 14)
            if result.price > 0:
                result.atr_pct = (result.atr / result.price) * 100

            # Market giriş (hız önemli, limit beklemeye gerek yok)
            result.entry_price = result.price

        # Skor: sinyal gücü × TF uyum oranı × 100
        alignment_ratio = result.aligned_count / result.total_tfs
        result.score = result.direction_strength * alignment_ratio * 100
        if result.direction == "SHORT":
            result.score = -result.score

        result.eligible = True
        return result

    def score_batch(self, symbols: list[str],
                    all_klines: dict[str, dict[str, list]],
                    market_ctx: dict = None,
                    volume_map: dict = None) -> list[SystemEScanResult]:
        """Tüm coinleri analiz et, skora göre sırala."""
        results = []
        market_ctx = market_ctx or {}
        volume_map = volume_map or {}

        for i, sym in enumerate(symbols):
            klines_by_tf = all_klines.get(sym, {})
            if not klines_by_tf:
                continue

            has_data = any(len(kl) >= 30 for kl in klines_by_tf.values())
            if not has_data:
                continue

            fr = 0.0
            if sym in market_ctx:
                fr = market_ctx[sym].get("funding_rate", 0.0)

            vol = volume_map.get(sym, 0.0)

            r = self.analyze_symbol(sym, klines_by_tf,
                                    funding_rate=fr, rank=i + 1,
                                    volume_24h=vol)
            results.append(r)

        # Eligible olanları öne, skora göre sırala
        results.sort(key=lambda r: (not r.eligible, -abs(r.score)))
        return results

    # ────── TF Analizi ──────

    def _analyze_tf(self, klines: list, tf_name: str, se: dict) -> TFSignal:
        """Tek bir TF'de 5 indikatör ile yön ve güç hesapla."""
        sig = TFSignal(timeframe=tf_name)

        closes = np.array([float(k[4]) for k in klines])
        highs = np.array([float(k[2]) for k in klines])
        lows = np.array([float(k[3]) for k in klines])
        volumes = np.array([float(k[5]) for k in klines])

        # 1. EMA 9/21
        ema_fast_p = se.get("ema_fast", 9)
        ema_slow_p = se.get("ema_slow", 21)
        ema_fast = self._ema(closes, ema_fast_p)
        ema_slow = self._ema(closes, ema_slow_p)
        sig.ema_fast = ema_fast
        sig.ema_slow = ema_slow

        if ema_fast > ema_slow:
            sig.ema_vote = 1.0
        elif ema_fast < ema_slow:
            sig.ema_vote = -1.0

        # 2. MACD
        macd_fast_p = se.get("macd_fast", 8)
        macd_slow_p = se.get("macd_slow", 17)
        macd_sig_p = se.get("macd_signal", 9)
        macd_line = self._ema(closes, macd_fast_p) - self._ema(closes, macd_slow_p)
        signal_line = self._ema_from_values(
            self._macd_line_series(closes, macd_fast_p, macd_slow_p), macd_sig_p)
        hist = macd_line - signal_line
        sig.macd_hist = hist

        if hist > 0:
            sig.macd_vote = 1.0
        elif hist < 0:
            sig.macd_vote = -1.0

        # 3. RSI
        rsi_p = se.get("rsi_periyot", 14)
        rsi = self._rsi(closes, rsi_p)
        sig.rsi_value = rsi

        if rsi > 50:
            sig.rsi_vote = 1.0
        elif rsi < 50:
            sig.rsi_vote = -1.0

        # 4. ADX (trend gücü)
        adx = self._adx(highs, lows, closes, se.get("adx_periyot", 14))
        sig.adx_value = adx
        if adx > se.get("adx_trend_esik", 20):
            sig.adx_vote = 1.0  # trend var

        # 5. Volume (artan hacim teyidi)
        vol_ma_p = se.get("volume_ma_periyot", 20)
        if len(volumes) >= vol_ma_p:
            vol_ma = float(np.mean(volumes[-vol_ma_p:]))
            vol_current = float(volumes[-1])
            if vol_ma > 0 and vol_current > vol_ma * 1.0:
                sig.volume_vote = 1.0
            elif vol_ma > 0 and vol_current < vol_ma * 0.5:
                sig.volume_vote = -1.0

        # 6. ATR
        sig.atr = self._atr(highs, lows, closes, 14)

        # Toplam skor: yön belirleme (EMA + MACD + RSI)
        direction_score = (sig.ema_vote + sig.macd_vote + sig.rsi_vote) / 3.0
        sig.score = direction_score

        if direction_score > 0:
            sig.direction = "LONG"
        elif direction_score < 0:
            sig.direction = "SHORT"
        else:
            sig.direction = "FLAT"

        # Confidence: yön sinyallerinin uyumu + trend gücü + hacim
        vote_agreement = abs(direction_score)  # 0-1
        adx_bonus = min(adx / 50.0, 1.0) * 0.3 if adx > 20 else 0
        vol_bonus = 0.1 if sig.volume_vote > 0 else 0
        sig.confidence = min(vote_agreement * 0.6 + adx_bonus + vol_bonus, 1.0)

        return sig

    # ────── Technical Indicators ──────

    @staticmethod
    def _ema(data: np.ndarray, period: int) -> float:
        if len(data) < period:
            return float(np.mean(data)) if len(data) > 0 else 0.0
        k = 2.0 / (period + 1)
        ema = float(data[0])
        for val in data[1:]:
            ema = val * k + ema * (1 - k)
        return ema

    @staticmethod
    def _ema_from_values(values: np.ndarray, period: int) -> float:
        if len(values) < period:
            return float(np.mean(values)) if len(values) > 0 else 0.0
        k = 2.0 / (period + 1)
        ema = float(values[0])
        for val in values[1:]:
            ema = val * k + ema * (1 - k)
        return ema

    @staticmethod
    def _macd_line_series(closes: np.ndarray, fast: int, slow: int) -> np.ndarray:
        if len(closes) < slow:
            return np.array([0.0])
        k_fast = 2.0 / (fast + 1)
        k_slow = 2.0 / (slow + 1)
        ema_f = float(closes[0])
        ema_s = float(closes[0])
        series = []
        for val in closes:
            ema_f = val * k_fast + ema_f * (1 - k_fast)
            ema_s = val * k_slow + ema_s * (1 - k_slow)
            series.append(ema_f - ema_s)
        return np.array(series)

    @staticmethod
    def _rsi(closes: np.ndarray, period: int = 14) -> float:
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
        return 100.0 - (100.0 / (1.0 + rs))

    @staticmethod
    def _atr(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray,
             period: int = 14) -> float:
        if len(closes) < 2:
            return 0.0
        tr_values = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i],
                     abs(highs[i] - closes[i - 1]),
                     abs(lows[i] - closes[i - 1]))
            tr_values.append(tr)
        if not tr_values:
            return 0.0
        if len(tr_values) < period:
            return float(np.mean(tr_values))
        atr = float(np.mean(tr_values[:period]))
        for i in range(period, len(tr_values)):
            atr = (atr * (period - 1) + tr_values[i]) / period
        return atr

    @staticmethod
    def _adx(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray,
             period: int = 14) -> float:
        if len(closes) < period * 2:
            return 0.0
        plus_dm, minus_dm, tr_list = [], [], []
        for i in range(1, len(closes)):
            up = highs[i] - highs[i - 1]
            down = lows[i - 1] - lows[i]
            plus_dm.append(up if up > down and up > 0 else 0.0)
            minus_dm.append(down if down > up and down > 0 else 0.0)
            tr = max(highs[i] - lows[i],
                     abs(highs[i] - closes[i - 1]),
                     abs(lows[i] - closes[i - 1]))
            tr_list.append(tr)
        if len(tr_list) < period:
            return 0.0
        atr = sum(tr_list[:period])
        plus_s = sum(plus_dm[:period])
        minus_s = sum(minus_dm[:period])
        dx_values = []
        for i in range(period, len(tr_list)):
            atr = atr - atr / period + tr_list[i]
            plus_s = plus_s - plus_s / period + plus_dm[i]
            minus_s = minus_s - minus_s / period + minus_dm[i]
            if atr == 0:
                continue
            pdi = 100 * plus_s / atr
            mdi = 100 * minus_s / atr
            s = pdi + mdi
            if s == 0:
                continue
            dx_values.append(100 * abs(pdi - mdi) / s)
        if not dx_values:
            return 0.0
        if len(dx_values) < period:
            return float(np.mean(dx_values))
        adx = float(np.mean(dx_values[:period]))
        for i in range(period, len(dx_values)):
            adx = (adx * (period - 1) + dx_values[i]) / period
        return adx
