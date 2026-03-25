"""System F Scanner v2.0 — Son Kursun (Last Bullet).

Tamamen yeniden tasarlanmis: maksimum kesinlik, minimum risk.

v2.0 Degisiklikler:
- Yon TF'leri: 5m, 15m, 1h, 4h, 1d (1m yon kararindan cikarildi)
- 1m sadece hacim patlamasi tetigi (giris zamanlama)
- Her TF'de 3/3 indikator uyumu zorunlu (EMA + MACD + RSI)
- Kalibre EMA: gap>=0.05% zorunlu, stale (<0.02%) reddedilir
- Kalibre MACD: histogram + momentum ivmesi birlikte gerekli
- Kalibre RSI: 60/40 esik (50 civarisi nötr bölge)
- Swing bazli kaldirac (ATR yedek, %90 percentile retrace)
- P(win)/P(SL)/EV olasilik hesabi
- Dinamik TP: swing forward ortalamasindan
- Trailing callback: swing retrace ortalamasi × 0.8
- Gelismis hacim spike: 2.5× son mum + 2× son 3 mum ortalamasi
- Hacim hard filtre: 5 TF'nin 3'ünde vol > 1.5×MA
- BTC yon uyumu (beta>0.5 ise BTC ayni yonde olmali)
- Spread filtresi
- Fee-aware minimum ROI hesabi
- Av sinifi: FARE/ORDEK/GEYIK/AYI
"""
import numpy as np
from dataclasses import dataclass, field
from loguru import logger
from core.config_manager import ConfigManager


# ─────────────────────────── Constants ───────────────────────────

SYSTEM_F_DEFAULT_DIRECTION_TFS = [
    ("5m", 5), ("1h", 60), ("4h", 240),
]
SYSTEM_F_TRIGGER_TF = "1m"

# TF name -> minutes mapping
_TF_MINUTES = {
    "1m": 1, "3m": 3, "5m": 5, "15m": 15, "30m": 30,
    "1h": 60, "2h": 120, "4h": 240, "8h": 480, "12h": 720, "1d": 1440,
}


# ─────────────────────────── Data Classes ───────────────────────────

@dataclass
class TFSignalF:
    """Tek bir timeframe'in sinyal detaylari (v2 — kalibre)."""
    timeframe: str = ""
    # Oylar: +1, -1, veya 0 (notr)
    ema_vote: float = 0.0
    macd_vote: float = 0.0
    rsi_vote: float = 0.0
    # 3/3 sonucu
    strict_direction: str = "FLAT"   # LONG / SHORT / FLAT
    confidence: float = 0.0          # 0-1
    # Ham degerler
    rsi_value: float = 50.0
    adx_value: float = 0.0
    macd_hist: float = 0.0
    macd_momentum: str = "FLAT"      # INCREASING / DECREASING / FLAT
    ema_fast: float = 0.0
    ema_slow: float = 0.0
    ema_gap_pct: float = 0.0         # (fast-slow)/price × 100
    atr: float = 0.0
    volume_ratio: float = 0.0        # current vol / MA vol
    volume_above_threshold: bool = False  # vol > 1.5×MA


@dataclass
class SwingAnalysisF:
    """Swing dalga analizi sonuclari."""
    forward_pcts: list = field(default_factory=list)   # yon ile ayni yondeki dalgalar (%)
    retrace_pcts: list = field(default_factory=list)    # yone karsi dalgalar (%)
    avg_forward_pct: float = 0.0
    avg_retrace_pct: float = 0.0
    max_retrace_pct: float = 0.0
    p90_retrace_pct: float = 0.0      # %90 percentile geri donus
    swing_count: int = 0
    sufficient: bool = False           # yeterli dalga var mi


@dataclass
class SystemFScanResult:
    """System F tarama sonucu — bir coin icin tum analiz (v2)."""
    symbol: str = ""
    rank: int = 0
    volume_24h: float = 0.0

    # TF sinyalleri
    tf_signals: list = field(default_factory=list)
    aligned_count: int = 0
    total_tfs: int = 0

    # Yon
    direction: str = ""              # LONG / SHORT / SKIP
    direction_strength: float = 0.0

    # Swing analizi
    swing_analysis: SwingAnalysisF = field(default_factory=SwingAnalysisF)
    swing_5m_ok: bool = False
    swing_g_pct: float = 0.0        # backward compat — avg retrace
    swing_count: int = 0

    # Akilli kaldirac & risk
    smart_leverage: int = 1
    sl_pct: float = 0.0             # SL yuzde (fiyat bazli)
    sl_price: float = 0.0
    emergency_sl_pct: float = 0.0

    # Olasilik & beklenen deger
    p_win: float = 0.0              # kazanma olasiligi (0-100)
    p_loss: float = 0.0             # kaybetme olasiligi (0-100)
    ev_pct: float = 0.0             # beklenen deger (ROI %)

    # Fee
    fee_roi_impact: float = 0.0     # fee'nin ROI etkisi (%)
    min_profitable_roi: float = 0.0 # min karli ROI (%)

    # Dinamik TP
    dynamic_tp_pct: float = 0.0     # fiyat hareketi % (TP mesafesi)
    dynamic_tp_roi: float = 0.0     # beklenen ROI (TP'de)
    trailing_callback_dynamic: float = 0.0  # trailing callback fiyat %

    # Trailing (server-side parametreleri)
    trailing_trigger_pct: float = 0.0   # fiyat % (activation)
    trailing_callback_pct: float = 0.0  # fiyat % (callback)
    target_roi_pct: float = 0.0         # yazilim yedek ROI hedefi

    # Hacim
    volume_spike: bool = False
    vol_spike_enhanced: bool = False
    volume_ratio: float = 0.0
    vol_hard_filter_pass: bool = False

    # Orderbook
    ob_imbalance: float = 0.0
    ob_thin_book: bool = False
    ob_wall_blocking: bool = False
    ob_liquidity: float = 0.0
    spread_pct: float = 0.0

    # BTC
    btc_beta: float = 0.0
    btc_direction: str = "FLAT"
    btc_aligned: bool = False

    # Entry
    entry_price: float = 0.0
    price: float = 0.0
    atr: float = 0.0
    atr_pct: float = 0.0
    funding_rate: float = 0.0

    # Karar
    eligible: bool = False
    reject_reason: str = ""
    composite_score: float = 0.0

    # Av sinifi
    av_sinifi: str = ""              # FARE / ORDEK / GEYIK / AYI


# ─────────────────────────── Core Scanner ───────────────────────────

class SystemFScanner:
    """System F v2: Son Kursun — swing bazli kaldirac + 15/15 oy + P(win)/EV."""

    def __init__(self, config: ConfigManager):
        self._config = config

    # ══════════════════════ Public API ══════════════════════

    def analyze_symbol(self, symbol: str,
                       klines_by_tf: dict[str, list],
                       funding_rate: float = 0.0,
                       rank: int = 0, volume_24h: float = 0.0,
                       ob_data: dict = None,
                       btc_beta: float = 0.0,
                       btc_direction: str = "FLAT",
                       ) -> SystemFScanResult:
        """Tek bir coin icin tam analiz: 5 TF + swing + olasilik + kaldirac."""
        sf = self._config.get("system_f", {})
        result = SystemFScanResult(symbol=symbol, rank=rank, volume_24h=volume_24h)
        result.funding_rate = funding_rate
        result.btc_beta = btc_beta
        result.btc_direction = btc_direction

        # ═══ 1. Her DIRECTION TF'de sinyal hesapla ═══
        # Config'den TF listesi oku, yoksa default kullan
        cfg_tfs = sf.get("direction_tfs", None)
        if cfg_tfs:
            direction_tfs = [(tf, _TF_MINUTES.get(tf, 5)) for tf in cfg_tfs]
        else:
            direction_tfs = SYSTEM_F_DEFAULT_DIRECTION_TFS

        tf_signals = []
        for tf_name, _tf_min in direction_tfs:
            klines = klines_by_tf.get(tf_name, [])
            if not klines or len(klines) < 30:
                continue
            sig = self._analyze_tf(klines, tf_name, sf)
            tf_signals.append(sig)

        result.tf_signals = tf_signals
        result.total_tfs = len(tf_signals)

        required_tfs = sf.get("min_tf_uyum", 5)
        if result.total_tfs < required_tfs:
            result.reject_reason = f"tf_data_{result.total_tfs}/{required_tfs}"
            return result

        # ═══ 2. Direction alignment: 3/3 per TF, 5/5 across TFs ═══
        long_count = sum(1 for s in tf_signals if s.strict_direction == "LONG")
        short_count = sum(1 for s in tf_signals if s.strict_direction == "SHORT")
        flat_count = sum(1 for s in tf_signals if s.strict_direction == "FLAT")

        if flat_count > 0:
            result.direction = "SKIP"
            result.aligned_count = max(long_count, short_count)
            result.reject_reason = f"flat_tf_{flat_count}"
            return result

        if long_count == result.total_tfs:
            result.direction = "LONG"
            result.aligned_count = long_count
        elif short_count == result.total_tfs:
            result.direction = "SHORT"
            result.aligned_count = short_count
        else:
            result.direction = "SKIP"
            result.aligned_count = max(long_count, short_count)
            result.reject_reason = f"mixed_{long_count}L_{short_count}S"
            return result

        # Direction strength (confidence ortalamasi)
        result.direction_strength = (
            sum(s.confidence for s in tf_signals) / len(tf_signals))
        min_strength = sf.get("min_sinyal_gucu", 0.6)
        if result.direction_strength < min_strength:
            result.reject_reason = f"weak_{result.direction_strength:.2f}"
            return result

        # ═══ 3. Volume hard filter: >=3/5 TF'de hacim > 1.5×MA ═══
        vol_tf_min = sf.get("vol_tf_min_count", 3)
        vol_tf_threshold = sf.get("vol_tf_threshold", 1.5)
        vol_passing = sum(1 for s in tf_signals
                         if s.volume_ratio >= vol_tf_threshold)
        result.vol_hard_filter_pass = vol_passing >= vol_tf_min
        if not result.vol_hard_filter_pass:
            result.reject_reason = f"vol_filter_{vol_passing}/{vol_tf_min}"
            return result

        # ═══ 4. Funding rate ═══
        max_fr = sf.get("max_funding_rate", 0.001)
        if funding_rate > max_fr and result.direction == "LONG":
            result.reject_reason = "high_fr_long"
            return result
        if funding_rate < -max_fr and result.direction == "SHORT":
            result.reject_reason = "high_fr_short"
            return result

        # ═══ 5. ATR & Price (5m veriden) ═══
        micro_klines = klines_by_tf.get("5m", [])
        if not micro_klines or len(micro_klines) < 30:
            result.reject_reason = "no_5m_data"
            return result

        closes_5m = np.array([float(k[4]) for k in micro_klines])
        highs_5m = np.array([float(k[2]) for k in micro_klines])
        lows_5m = np.array([float(k[3]) for k in micro_klines])
        result.price = float(closes_5m[-1])
        result.atr = self._atr(highs_5m, lows_5m, closes_5m, 14)
        if result.price > 0:
            result.atr_pct = (result.atr / result.price) * 100
        result.entry_price = result.price
        if result.atr_pct <= 0:
            result.reject_reason = "zero_atr"
            return result

        # ═══ 6. Orderbook ═══
        if ob_data:
            result.ob_imbalance = ob_data.get("weighted_imbalance", 0.0)
            result.ob_thin_book = ob_data.get("thin_book", False)
            result.ob_liquidity = ob_data.get("liquidity_score", 0.0)
            wall_signal = ob_data.get("wall_signal", "NONE")

            # Spread
            spread = ob_data.get("spread_pct", 0.0)
            if spread <= 0:
                bid = ob_data.get("best_bid_price", 0)
                ask = ob_data.get("best_ask_price", 0)
                if bid > 0 and ask > 0:
                    spread = (ask - bid) / bid * 100
            result.spread_pct = spread

            if result.ob_thin_book:
                result.reject_reason = "thin_book"
                return result

            spread_max = sf.get("spread_max_pct", 0.05)
            if spread > spread_max > 0:
                result.reject_reason = f"spread_{spread:.3f}%"
                return result

            if result.direction == "LONG" and wall_signal == "UP_BLOCKED":
                result.ob_wall_blocking = True
                result.reject_reason = "wall_long"
                return result
            if result.direction == "SHORT" and wall_signal == "DOWN_BLOCKED":
                result.ob_wall_blocking = True
                result.reject_reason = "wall_short"
                return result

            if result.direction == "LONG" and result.ob_imbalance < -0.3:
                result.reject_reason = "ob_against_long"
                return result
            if result.direction == "SHORT" and result.ob_imbalance > 0.3:
                result.reject_reason = "ob_against_short"
                return result

        # ═══ 7. BTC yon uyumu ═══
        btc_beta_threshold = sf.get("btc_beta_threshold", 0.5)
        if (abs(btc_beta) > btc_beta_threshold
                and btc_direction not in ("FLAT", "")):
            if result.direction != btc_direction:
                result.btc_aligned = False
                result.reject_reason = f"btc_{btc_direction}"
                return result
            result.btc_aligned = True
        else:
            result.btc_aligned = True  # dusuk korelasyon veya BTC flat

        # ═══ 8. Swing analizi (15m birincil, 5m dogrulama) ═══
        swing_primary_tf = sf.get("swing_primary_tf", "15m")
        swing_validation_tf = sf.get("swing_validation_tf", "5m")
        swing_n = sf.get("swing_n", 10)

        primary_klines = klines_by_tf.get(swing_primary_tf, [])
        swing_primary = self._compute_swing_analysis(
            primary_klines, result.direction, swing_n, result.price)

        if not swing_primary.sufficient:
            # Fallback: 5m
            val_klines = klines_by_tf.get(swing_validation_tf, [])
            swing_primary = self._compute_swing_analysis(
                val_klines, result.direction, swing_n, result.price)
            if not swing_primary.sufficient:
                result.reject_reason = "no_swings"
                return result

        result.swing_analysis = swing_primary
        result.swing_g_pct = swing_primary.avg_retrace_pct
        result.swing_count = swing_primary.swing_count

        # 5m dogrulama (SL yeterliligi icin)
        val_klines = klines_by_tf.get(swing_validation_tf, [])
        swing_5m = (self._compute_swing_analysis(
            val_klines, result.direction, swing_n, result.price)
            if val_klines and len(val_klines) >= 50 else None)
        result.swing_5m_ok = True

        # ═══ 9. Akilli kaldirac (swing bazli, ATR yedek) ═══
        safety_mult = sf.get("swing_safety_mult", 1.2)
        liq_mult = sf.get("swing_liq_mult", 2.5)
        liq_factor = sf.get("liq_carpani", 0.70)
        max_kaldirac = sf.get("max_kaldirac", 125)
        sl_atr_mult = sf.get("sl_atr_mult", 1.5)
        fee_rate = sf.get("fee_rate", 0.0004)

        # SL: max(1.5×ATR, p90_retrace × güvenlik_carpani) + fee
        atr_sl = sl_atr_mult * result.atr_pct
        swing_sl = swing_primary.p90_retrace_pct * safety_mult
        base_sl = max(atr_sl, swing_sl)
        fee_pct = fee_rate * 200  # round trip price %
        result.sl_pct = base_sl + fee_pct

        # 5m dogrulama: daha buyuk retrace varsa SL'i genislet
        if swing_5m and swing_5m.sufficient:
            sl_5m = swing_5m.p90_retrace_pct * safety_mult + fee_pct
            if sl_5m > result.sl_pct:
                result.sl_pct = sl_5m
                result.swing_5m_ok = False

        # Kaldirac: liq_distance = SL × liq_mult
        liq_distance_pct = result.sl_pct * liq_mult
        if liq_distance_pct > 0:
            smart_lev = int((liq_factor * 100) / liq_distance_pct)
        else:
            smart_lev = 1
        smart_lev = max(2, min(smart_lev, max_kaldirac))
        result.smart_leverage = smart_lev

        # SL fiyati
        if result.direction == "LONG":
            result.sl_price = result.price * (1 - result.sl_pct / 100)
        else:
            result.sl_price = result.price * (1 + result.sl_pct / 100)

        # Emergency: likidasyon mesafesinin %80'i
        emrg_cfg = sf.get("emergency_liq_pct", 80) / 100.0
        real_liq_dist = (1.0 / smart_lev) * liq_factor * 100
        result.emergency_sl_pct = real_liq_dist * emrg_cfg

        # ═══ 10. Dinamik TP + Trailing ═══
        avg_fwd = swing_primary.avg_forward_pct
        avg_ret = swing_primary.avg_retrace_pct

        result.dynamic_tp_pct = avg_fwd                   # fiyat % (EV hesabi icin)
        result.dynamic_tp_roi = avg_fwd * smart_lev        # ROI %

        # Trailing callback: siki — TP gibi kar kilitle, fiyat devam ederse surukle
        tp_callback = sf.get("trailing_tp_callback_pct", 0.3)
        result.trailing_callback_dynamic = round(
            max(0.1, min(tp_callback, 1.0)), 2)

        # Server-side parametreleri
        result.trailing_trigger_pct = avg_fwd              # activation (trailing burada baslar)
        result.trailing_callback_pct = result.trailing_callback_dynamic

        # Software TP: yedek — trailing calismaz ise (avg_fwd × mult)
        software_tp_mult = sf.get("software_tp_mult", 2.0)
        result.target_roi_pct = avg_fwd * software_tp_mult * smart_lev

        # ═══ 11. Fee etkisi ═══
        result.fee_roi_impact = round(fee_rate * 200 * smart_lev, 2)
        result.min_profitable_roi = result.fee_roi_impact

        # ═══ 12. P(win), P(SL), EV ═══
        p_sl_max = sf.get("p_sl_max_pct", 10.0) / 100.0
        ev_min = sf.get("ev_min_pct", 15.0)

        p_win, p_loss = self._calc_probability(
            swing_primary.forward_pcts, swing_primary.retrace_pcts,
            result.dynamic_tp_pct, result.sl_pct)
        result.p_win = round(p_win * 100, 1)
        result.p_loss = round(p_loss * 100, 1)

        tp_roi_net = result.dynamic_tp_roi - result.fee_roi_impact
        sl_roi_net = result.sl_pct * smart_lev + result.fee_roi_impact
        result.ev_pct = round(p_win * tp_roi_net - p_loss * sl_roi_net, 2)

        if p_loss > p_sl_max:
            result.composite_score = self._calc_composite_score(result, sf)
            result.reject_reason = f"p_sl_{result.p_loss:.0f}%"
            return result

        if result.ev_pct < ev_min:
            result.composite_score = self._calc_composite_score(result, sf)
            result.reject_reason = f"ev_{result.ev_pct:.1f}%"
            return result

        # ═══ 13. Hacim patlamasi (1m tetik) ═══
        vol_spike_required = sf.get("volume_spike_required", True)
        klines_1m = klines_by_tf.get("1m", [])
        if klines_1m and len(klines_1m) >= 25:
            spike, ratio = self._check_volume_spike_enhanced(klines_1m, sf, result.direction)
            result.volume_spike = spike
            result.vol_spike_enhanced = spike
            result.volume_ratio = ratio

        if vol_spike_required and not result.volume_spike:
            result.composite_score = self._calc_composite_score(result, sf)
            result.reject_reason = f"no_spike_{result.volume_ratio:.1f}x"
            return result

        # ═══ 14. Composite skor ═══
        result.composite_score = self._calc_composite_score(result, sf)
        min_skor = sf.get("min_skor", 85)
        if result.composite_score < min_skor:
            result.reject_reason = f"score_{result.composite_score:.0f}"
            return result

        # ═══ 15. BTC beta asiri ═══
        max_beta = sf.get("max_btc_beta", 2.0)
        if abs(btc_beta) > max_beta:
            result.reject_reason = f"beta_{btc_beta:.2f}"
            return result

        # ═══ 16. Av sinifi ═══
        result.av_sinifi = self._classify_av(result.dynamic_tp_roi)

        result.eligible = True
        return result

    def score_batch(self, symbols: list[str],
                    all_klines: dict[str, dict[str, list]],
                    market_ctx: dict = None,
                    volume_map: dict = None,
                    ob_map: dict = None,
                    beta_map: dict = None,
                    btc_direction: str = "FLAT",
                    ) -> list[SystemFScanResult]:
        """Tum coinleri analiz et, skora gore sirala."""
        results = []
        market_ctx = market_ctx or {}
        volume_map = volume_map or {}
        ob_map = ob_map or {}
        beta_map = beta_map or {}

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

            r = self.analyze_symbol(
                sym, klines_by_tf,
                funding_rate=fr, rank=i + 1,
                volume_24h=volume_map.get(sym, 0.0),
                ob_data=ob_map.get(sym),
                btc_beta=beta_map.get(sym, 0.8),
                btc_direction=btc_direction,
            )
            results.append(r)

        results.sort(key=lambda r: (not r.eligible, -r.composite_score))
        return results

    # ══════════════════════ Analysis Methods ══════════════════════

    def _analyze_tf(self, klines: list, tf_name: str, sf: dict) -> TFSignalF:
        """Tek TF'de kalibre indikatorler + 3/3 oy."""
        sig = TFSignalF(timeframe=tf_name)

        closes = np.array([float(k[4]) for k in klines])
        highs = np.array([float(k[2]) for k in klines])
        lows = np.array([float(k[3]) for k in klines])
        volumes = np.array([float(k[5]) for k in klines])
        price = float(closes[-1]) if len(closes) > 0 else 0.0

        # ── 1. EMA 9/21 — gap kalibrasyonu ──
        ema_f_p = sf.get("ema_fast", 9)
        ema_s_p = sf.get("ema_slow", 21)
        ema_f = self._ema(closes, ema_f_p)
        ema_s = self._ema(closes, ema_s_p)
        sig.ema_fast = ema_f
        sig.ema_slow = ema_s

        gap_min = sf.get("ema_gap_min_pct", 0.05)
        gap_stale = sf.get("ema_gap_stale_pct", 0.02)

        if price > 0:
            sig.ema_gap_pct = (ema_f - ema_s) / price * 100

        if sig.ema_gap_pct > gap_min:
            sig.ema_vote = 1.0
        elif sig.ema_gap_pct < -gap_min:
            sig.ema_vote = -1.0
        else:
            sig.ema_vote = 0.0  # gap yetersiz veya stale

        # ── 2. MACD — histogram + momentum ivmesi ──
        macd_f = sf.get("macd_fast", 8)
        macd_s = sf.get("macd_slow", 17)
        macd_sig = sf.get("macd_signal", 9)

        macd_series = self._macd_line_series(closes, macd_f, macd_s)
        signal_series = self._ema_series_full(macd_series, macd_sig)
        hist_series = macd_series - signal_series

        if len(hist_series) >= 3:
            h1 = float(hist_series[-3])
            h2 = float(hist_series[-2])
            h3 = float(hist_series[-1])
            sig.macd_hist = h3

            if h1 < h2 < h3:
                sig.macd_momentum = "INCREASING"
            elif h1 > h2 > h3:
                sig.macd_momentum = "DECREASING"
            else:
                sig.macd_momentum = "FLAT"

            momentum_req = sf.get("macd_momentum_required", True)
            if momentum_req:
                if h3 > 0 and sig.macd_momentum == "INCREASING":
                    sig.macd_vote = 1.0
                elif h3 < 0 and sig.macd_momentum == "DECREASING":
                    sig.macd_vote = -1.0
                # else: 0 (histogram ve ivme uyusmuyor)
            else:
                if h3 > 0:
                    sig.macd_vote = 1.0
                elif h3 < 0:
                    sig.macd_vote = -1.0
        elif len(hist_series) > 0:
            sig.macd_hist = float(hist_series[-1])

        # ── 3. RSI — 60/40 esik ──
        rsi = self._rsi(closes, sf.get("rsi_periyot", 14))
        sig.rsi_value = rsi

        rsi_long = sf.get("rsi_long_esik", 60)
        rsi_short = sf.get("rsi_short_esik", 40)
        if rsi > rsi_long:
            sig.rsi_vote = 1.0
        elif rsi < rsi_short:
            sig.rsi_vote = -1.0
        # else: 0 (notr bölge)

        # ── 4. ADX (sadece skorlama, yon kararina katilmaz) ──
        sig.adx_value = self._adx(
            highs, lows, closes, sf.get("adx_periyot", 14))

        # ── 5. Volume ratio ──
        vol_ma_p = sf.get("volume_ma_periyot", 20)
        if len(volumes) >= vol_ma_p + 1:
            vol_ma = float(np.mean(volumes[-(vol_ma_p + 1):-1]))
            vol_cur = float(volumes[-1])
            if vol_ma > 0:
                sig.volume_ratio = vol_cur / vol_ma
                vt = sf.get("vol_tf_threshold", 1.5)
                sig.volume_above_threshold = sig.volume_ratio >= vt

        # ── 6. ATR ──
        sig.atr = self._atr(highs, lows, closes, 14)

        # ── 7. 3/3 strict direction ──
        if sig.ema_vote > 0 and sig.macd_vote > 0 and sig.rsi_vote > 0:
            sig.strict_direction = "LONG"
        elif sig.ema_vote < 0 and sig.macd_vote < 0 and sig.rsi_vote < 0:
            sig.strict_direction = "SHORT"
        else:
            sig.strict_direction = "FLAT"

        # ── 8. Confidence ──
        if sig.strict_direction != "FLAT":
            adx_bonus = (min(sig.adx_value / 50.0, 1.0) * 0.2
                         if sig.adx_value > 20 else 0)
            vol_bonus = 0.1 if sig.volume_above_threshold else 0
            gap_bonus = min(abs(sig.ema_gap_pct) / 0.2, 1.0) * 0.1
            sig.confidence = min(0.6 + adx_bonus + vol_bonus + gap_bonus, 1.0)
        else:
            sig.confidence = 0.0

        return sig

    def _compute_swing_analysis(self, klines: list, direction: str,
                                swing_n: int, current_price: float,
                                ) -> SwingAnalysisF:
        """Zigzag swing analizi — yon bazli forward/retrace ayirimi."""
        result = SwingAnalysisF()
        if not klines or len(klines) < swing_n * 2 + 10 or current_price <= 0:
            return result

        highs = [float(k[2]) for k in klines]
        lows = [float(k[3]) for k in klines]
        closes = [float(k[4]) for k in klines]

        swings = self._detect_zigzag(highs, lows, closes, swing_n)
        if len(swings) < 3:
            return result

        forward_pcts = []
        retrace_pcts = []

        for i in range(1, len(swings)):
            prev_type = swings[i - 1][2]
            curr_type = swings[i][2]
            wave_pct = abs(swings[i][1] - swings[i - 1][1]) / current_price * 100

            if direction == "LONG":
                if prev_type == 'L' and curr_type == 'H':
                    forward_pcts.append(wave_pct)
                elif prev_type == 'H' and curr_type == 'L':
                    retrace_pcts.append(wave_pct)
            elif direction == "SHORT":
                if prev_type == 'H' and curr_type == 'L':
                    forward_pcts.append(wave_pct)
                elif prev_type == 'L' and curr_type == 'H':
                    retrace_pcts.append(wave_pct)

        if not forward_pcts or not retrace_pcts:
            return result

        result.forward_pcts = forward_pcts
        result.retrace_pcts = retrace_pcts
        result.avg_forward_pct = sum(forward_pcts) / len(forward_pcts)
        result.avg_retrace_pct = sum(retrace_pcts) / len(retrace_pcts)
        result.max_retrace_pct = max(retrace_pcts)
        result.p90_retrace_pct = self._percentile(retrace_pcts, 90)
        result.swing_count = len(forward_pcts) + len(retrace_pcts)
        result.sufficient = (len(forward_pcts) >= 3 and len(retrace_pcts) >= 3)
        return result

    @staticmethod
    def _calc_probability(forward_pcts: list, retrace_pcts: list,
                          tp_pct: float, sl_pct: float,
                          ) -> tuple[float, float]:
        """Cok-dongulu P(win)/P(loss) hesabi.

        Her dongude:
          p(win)  = P(forward >= TP)
          p(loss) = P(forward < TP) × P(retrace >= SL)
          p(devam) = gerisi
        Sonuc: p_win / (p_win + p_loss), p_loss / (p_win + p_loss)
        """
        if not forward_pcts or not retrace_pcts:
            return 0.0, 1.0

        tp_hits = sum(1 for f in forward_pcts if f >= tp_pct)
        p_fwd_tp = tp_hits / len(forward_pcts)

        sl_hits = sum(1 for r in retrace_pcts if r >= sl_pct)
        p_ret_sl = sl_hits / len(retrace_pcts)

        p_win_c = p_fwd_tp
        p_loss_c = (1 - p_fwd_tp) * p_ret_sl

        denom = p_win_c + p_loss_c
        if denom <= 0:
            return 0.3, 0.3  # belirsiz — veri yetersiz

        return p_win_c / denom, p_loss_c / denom

    def _check_volume_spike_enhanced(self, klines_1m: list, sf: dict,
                                     direction: str = "",
                                     ) -> tuple[bool, float]:
        """Gelismis hacim tetigi: son mum >= 2.5×MA VE son 3 ort >= 2×MA.

        + Climax filtresi: spike mumunun yonu, trade yonuyle ayni olmali.
          LONG → yesil mum (close > open), SHORT → kirmizi mum (close < open).
        """
        volumes = np.array([float(k[5]) for k in klines_1m])
        vol_ma_p = sf.get("volume_ma_periyot", 20)

        if len(volumes) < vol_ma_p + 3:
            return False, 0.0

        # MA: son 3 mumu haric tut
        vol_ma = float(np.mean(volumes[-(vol_ma_p + 3):-3]))
        if vol_ma <= 0:
            return False, 0.0

        vol_current = float(volumes[-1])
        vol_avg3 = float(np.mean(volumes[-3:]))

        current_ratio = vol_current / vol_ma
        avg3_ratio = vol_avg3 / vol_ma

        spike_cur = sf.get("vol_spike_current_mult", 2.5)
        spike_avg3 = sf.get("vol_spike_avg3_mult", 2.0)

        spike = (current_ratio >= spike_cur) and (avg3_ratio >= spike_avg3)

        # Climax filtresi: spike mumu yonu trade yonuyle ayni olmali
        if spike and direction:
            last_candle = klines_1m[-1]
            candle_open = float(last_candle[1])
            candle_close = float(last_candle[4])
            if direction == "LONG" and candle_close <= candle_open:
                spike = False  # hacim yuksek ama mum kirmizi — climax riski
            elif direction == "SHORT" and candle_close >= candle_open:
                spike = False  # hacim yuksek ama mum yesil — climax riski

        return spike, round(current_ratio, 2)

    def _calc_composite_score(self, r: SystemFScanResult, sf: dict) -> float:
        """Agirlikli skor (0-100): EV ve P(win) dahil."""
        score = 0.0

        # 1. Direction strength (35 puan)
        score += r.direction_strength * 35.0

        # 2. EV (25 puan) — yuksek EV = daha iyi coin
        if r.ev_pct > 0:
            score += min(r.ev_pct / 50.0, 1.0) * 25.0

        # 3. P(win) (20 puan)
        if r.p_win > 0:
            score += min(r.p_win / 100.0, 1.0) * 20.0

        # 4. Volume momentum (10 puan)
        if r.volume_ratio > 0:
            spike_mult = sf.get("vol_spike_current_mult", 2.5)
            score += min(r.volume_ratio / (spike_mult * 2), 1.0) * 10.0

        # 5. ADX trend gucu (5 puan)
        adx_1h = 0.0
        for sig in r.tf_signals:
            if sig.timeframe == "1h":
                adx_1h = sig.adx_value
                break
        adx_esik = sf.get("adx_trend_esik", 20)
        if adx_1h > adx_esik:
            score += min((adx_1h - adx_esik) / 30.0, 1.0) * 5.0

        # 6. Funding avantaji (5 puan)
        fr = r.funding_rate
        if r.direction == "LONG" and fr <= 0:
            score += 5.0
        elif r.direction == "SHORT" and fr >= 0:
            score += 5.0
        elif abs(fr) < 0.0003:
            score += 2.5

        return round(min(score, 100.0), 1)

    @staticmethod
    def _classify_av(dynamic_tp_roi: float) -> str:
        """Av buyuklugu siniflandirmasi."""
        if dynamic_tp_roi >= 80:
            return "AYI"
        elif dynamic_tp_roi >= 40:
            return "GEYIK"
        elif dynamic_tp_roi >= 15:
            return "ORDEK"
        return "FARE"

    # ══════════════════════ Technical Indicators ══════════════════════

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
    def _ema_series_full(data: np.ndarray, period: int) -> np.ndarray:
        """Tam EMA serisi dondur (giris ile ayni uzunluk)."""
        if len(data) == 0:
            return np.array([0.0])
        k = 2.0 / (period + 1)
        result = np.empty(len(data))
        result[0] = float(data[0])
        for i in range(1, len(data)):
            result[i] = float(data[i]) * k + result[i - 1] * (1 - k)
        return result

    @staticmethod
    def _macd_line_series(closes: np.ndarray, fast: int, slow: int,
                          ) -> np.ndarray:
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
        atr_s = sum(tr_list[:period])
        plus_s = sum(plus_dm[:period])
        minus_s = sum(minus_dm[:period])
        dx_values = []
        for i in range(period, len(tr_list)):
            atr_s = atr_s - atr_s / period + tr_list[i]
            plus_s = plus_s - plus_s / period + plus_dm[i]
            minus_s = minus_s - minus_s / period + minus_dm[i]
            if atr_s == 0:
                continue
            pdi = 100 * plus_s / atr_s
            mdi = 100 * minus_s / atr_s
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

    @staticmethod
    def _detect_zigzag(highs: list, lows: list, closes: list,
                       n: int = 10) -> list:
        """Zigzag swing tespiti. Returns [(index, price, 'H'/'L'), ...]"""
        if len(closes) < n * 2:
            return []
        swings = []
        i = n
        while i < len(closes) - n:
            is_high = all(highs[i] >= highs[i - j] for j in range(1, n + 1))
            is_high = is_high and all(
                highs[i] >= highs[i + j]
                for j in range(1, min(n + 1, len(closes) - i)))

            is_low = all(lows[i] <= lows[i - j] for j in range(1, n + 1))
            is_low = is_low and all(
                lows[i] <= lows[i + j]
                for j in range(1, min(n + 1, len(closes) - i)))

            if is_high and is_low:
                if swings and swings[-1][2] == 'L':
                    swings.append((i, highs[i], 'H'))
                else:
                    swings.append((i, lows[i], 'L'))
            elif is_high:
                if not swings or swings[-1][2] != 'H':
                    swings.append((i, highs[i], 'H'))
                elif highs[i] > swings[-1][1]:
                    swings[-1] = (i, highs[i], 'H')
            elif is_low:
                if not swings or swings[-1][2] != 'L':
                    swings.append((i, lows[i], 'L'))
                elif lows[i] < swings[-1][1]:
                    swings[-1] = (i, lows[i], 'L')
            i += 1
        return swings

    @staticmethod
    def _percentile(values: list, pct: int) -> float:
        """Percentile hesapla. pct: 0-100."""
        if not values:
            return 0.0
        s = sorted(values)
        idx = min(int(len(s) * pct / 100), len(s) - 1)
        return s[idx]
