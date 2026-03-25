"""System D Scanner — Sıralı Coin Analiz & Trade Sistemi.

Top 50 coin (24h hacme göre), sırayla değerlendirilir.
Zoom Diyafram: her coin için optimal TF otomatik seçilir (dirsek noktası).
Her coin için: MTF yön oylama, rejim tespiti, G bazlı kaldıraç, SL/TP/trailing.
"""
import math
import numpy as np
from dataclasses import dataclass, field
from typing import Optional
from loguru import logger
from core.config_manager import ConfigManager
from scanner.system_b_scanner import (
    detect_zigzag_swings,
    compute_efficiency_ratio,
    SwingPoint,
    analyze_waves,
    WaveAnalysis,
)
from indicators.indicator_engine import IndicatorEngine

# Binance destekli TF'ler ve dakika karşılıkları
ZOOM_TF_LADDER = [
    ("5m", 5), ("15m", 15), ("30m", 30),
    ("1h", 60), ("2h", 120), ("4h", 240),
    ("8h", 480), ("12h", 720), ("1d", 1440),
]


# ─────────────────────────── Data Classes ───────────────────────────

@dataclass
class DirectionVote:
    """Tek bir timeframe'in yön oyu."""
    timeframe: str = ""
    ema_vote: float = 0.0       # +1 LONG, -1 SHORT
    macd_vote: float = 0.0
    rsi_vote: float = 0.0
    score: float = 0.0          # ortalama (-1 ile +1)
    rsi_value: float = 50.0
    macd_hist: float = 0.0
    ema_fast: float = 0.0
    ema_slow: float = 0.0


@dataclass
class DirectionResult:
    """MTF yön oylama sonucu."""
    direction: str = "SKIP"     # "LONG" / "SHORT" / "SKIP"
    total_score: float = 0.0    # -1 ile +1
    macro: DirectionVote = field(default_factory=DirectionVote)
    mid: DirectionVote = field(default_factory=DirectionVote)
    micro: DirectionVote = field(default_factory=DirectionVote)
    strength: str = "WEAK"      # "STRONG" / "MODERATE" / "WEAK"
    aligned: bool = False       # 3/3 aynı yön mü


@dataclass
class RegimeResult:
    """Rejim tespiti sonucu."""
    regime: str = "UNKNOWN"     # "TREND" / "RANGING" / "GREY"
    adx: float = 0.0
    adx_vote: str = ""          # "TREND" / "RANGING" / "GREY"
    bb_width: float = 0.0
    bb_width_expanding: bool = False
    bb_vote: str = ""
    er: float = 0.0
    er_vote: str = ""
    trend_votes: int = 0
    ranging_votes: int = 0


@dataclass
class ZoomTFResult:
    """Tek bir TF'nin zoom analiz sonucu."""
    tf: str = ""
    minutes: int = 0
    G: float = 0.0
    I: float = 0.0
    cv: float = 0.0
    wave_count: int = 0
    verimlilik: float = 0.0     # TF_dakika / G
    g_artis_hizi: float = 0.0   # delta_G / delta_TF (bir sonrakine kıyasla)
    leverage: int = 0


@dataclass
class ZoomResult:
    """Zoom diyafram analiz sonucu — optimal TF seçimi."""
    all_tfs: list = field(default_factory=list)  # [ZoomTFResult, ...]
    optimal_tf: str = "5m"      # seçilen mikro TF
    optimal_minutes: int = 5
    optimal_G: float = 0.0
    optimal_leverage: int = 1
    mid_tf: str = "1h"          # türetilen orta TF
    macro_tf: str = "4h"        # türetilen makro TF
    dirsek_index: int = 0       # dirsek noktası index


@dataclass
class LeverageCalc:
    """Kaldıraç hesaplama detayları."""
    G_raw: float = 0.0          # zoom'dan gelen orijinal G (%)
    G: float = 0.0              # efektif G (ters hesap sonrası, %)
    I: float = 0.0              # ileri dalga ortalaması (%)
    sl_pct: float = 0.0         # SL mesafesi (%)
    pratik_liq_pct: float = 0.0
    teorik_liq_pct: float = 0.0
    max_leverage: int = 1
    fee_pct: float = 0.08
    slippage_pct: float = 0.03
    wave_count: int = 0
    cv: float = 0.0
    g_adjusted: bool = False    # ters hesap yapıldı mı


@dataclass
class SystemDScanResult:
    """System D tarama sonucu — bir coin için tüm analiz."""
    symbol: str = ""
    rank: int = 0               # hacim sıralaması (1 = en yüksek)
    volume_24h: float = 0.0

    # Zoom diyafram
    zoom: ZoomResult = field(default_factory=ZoomResult)

    # Yön
    direction_result: DirectionResult = field(default_factory=DirectionResult)
    direction: str = ""         # "LONG" / "SHORT" / "SKIP"

    # Rejim
    regime_result: RegimeResult = field(default_factory=RegimeResult)
    regime: str = ""            # "TREND" / "RANGING" / "GREY"

    # Kaldıraç & Risk
    leverage_calc: LeverageCalc = field(default_factory=LeverageCalc)
    leverage: int = 1
    sl_pct: float = 0.0
    tp_pct: float = 0.0        # 0 = trailing (no fixed TP)
    trailing_trigger_pct: float = 0.0
    trailing_callback_pct: float = 0.0

    # Entry
    entry_type: str = "LIMIT"   # "LIMIT"
    entry_offset_pct: float = 0.0  # ATR × offset
    entry_price: float = 0.0

    # Genel
    price: float = 0.0
    atr: float = 0.0
    atr_pct: float = 0.0
    funding_rate: float = 0.0
    bb_upper: float = 0.0
    bb_lower: float = 0.0
    bb_middle: float = 0.0

    # Karar
    eligible: bool = False
    reject_reason: str = ""
    score: float = 0.0          # composite score (yön gücü × rejim güveni)


# ─────────────────────────── Core Scanner ───────────────────────────

class SystemDScanner:
    """System D: sıralı coin analiz ve trade kararı."""

    def __init__(self, config: ConfigManager):
        self._config = config
        self._ie = IndicatorEngine(config)

    # ────── Public API ──────

    def analyze_symbol(self, symbol: str,
                       klines_by_tf: dict[str, list],
                       funding_rate: float = 0.0,
                       rank: int = 0, volume_24h: float = 0.0
                       ) -> SystemDScanResult:
        """Tek bir coin için tam analiz.

        klines_by_tf: {"5m": [...], "15m": [...], "1h": [...], ...}
        """
        sd = self._config.get("system_d", {})
        result = SystemDScanResult(symbol=symbol, rank=rank, volume_24h=volume_24h)
        result.funding_rate = funding_rate

        # 1. Zoom diyafram: optimal TF seç
        result.zoom = self._zoom_diyafram(klines_by_tf, sd)
        zoom = result.zoom

        if zoom.optimal_G < 0.01:
            result.reject_reason = "zoom_no_valid_g"
            return result

        # TF kline'larını al
        klines_micro = klines_by_tf.get(zoom.optimal_tf, [])
        klines_mid = klines_by_tf.get(zoom.mid_tf, [])
        klines_macro = klines_by_tf.get(zoom.macro_tf, [])

        if not klines_micro or len(klines_micro) < 30:
            result.reject_reason = "insufficient_micro_data"
            return result

        # 2. Yön belirleme (MTF oylama — dinamik TF'lerle)
        result.direction_result = self._determine_direction(
            klines_macro, klines_mid, klines_micro, sd,
            zoom.macro_tf, zoom.mid_tf, zoom.optimal_tf)
        result.direction = result.direction_result.direction

        if result.direction == "SKIP":
            result.reject_reason = "direction_unclear"
            return result

        # 3. Funding rate kontrolü
        max_fr = sd.get("max_funding_rate", 0.001)
        if funding_rate > max_fr and result.direction == "LONG":
            result.reject_reason = "high_funding_long"
            return result
        if funding_rate < -max_fr and result.direction == "SHORT":
            result.reject_reason = "high_funding_short"
            return result

        # 4. Rejim tespiti (orta TF verileri)
        result.regime_result = self._determine_regime(klines_mid, sd)
        result.regime = result.regime_result.regime

        # 5. Kaldıraç: zoom'dan bulunan G kullan
        result.leverage_calc = self._calculate_leverage_from_g(
            zoom.optimal_G, result.regime, sd,
            wave_count=next(
                (t.wave_count for t in zoom.all_tfs if t.tf == zoom.optimal_tf), 0),
            cv=next(
                (t.cv for t in zoom.all_tfs if t.tf == zoom.optimal_tf), 0),
            I=next(
                (t.I for t in zoom.all_tfs if t.tf == zoom.optimal_tf), 0),
        )
        lc = result.leverage_calc

        if lc.wave_count < 2:
            result.reject_reason = "insufficient_waves"
            return result

        result.leverage = lc.max_leverage
        result.sl_pct = lc.sl_pct

        # Min kaldıraç kontrolü
        min_lev = sd.get("min_kaldirac", 2)
        if result.leverage < min_lev:
            result.reject_reason = f"leverage_low_{result.leverage}x"
            return result

        # Max kaldıraç sınırı
        max_lev = sd.get("max_kaldirac", 125)
        if result.leverage > max_lev:
            result.leverage = max_lev

        # 6. TP & Trailing
        self._set_exit_params(result, klines_mid, sd)

        # 7. Entry fiyatı (mikro TF'den)
        self._set_entry_price(result, klines_micro, sd)

        # 8. Skor hesapla
        dir_strength = abs(result.direction_result.total_score)
        regime_bonus = 1.0 if result.regime == "TREND" else 0.8 if result.regime == "RANGING" else 0.6
        result.score = dir_strength * regime_bonus * 100
        if result.direction == "SHORT":
            result.score = -result.score

        result.eligible = True
        return result

    def score_batch(self, symbols: list[str],
                    all_klines: dict[str, dict[str, list]],
                    market_ctx: dict = None,
                    volume_map: dict = None) -> list[SystemDScanResult]:
        """Tüm coinleri analiz et, skora göre sırala.

        all_klines: {symbol: {tf: klines_list, ...}, ...}
        """
        results = []
        market_ctx = market_ctx or {}
        volume_map = volume_map or {}

        for i, sym in enumerate(symbols):
            klines_by_tf = all_klines.get(sym, {})
            if not klines_by_tf:
                continue

            # En az bir TF'de yeterli veri olmalı
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

    # ────── Zoom Diyafram ──────

    def _zoom_diyafram(self, klines_by_tf: dict[str, list],
                       sd: dict) -> ZoomResult:
        """Tüm TF'lerde G hesapla, dirsek noktasını bul, optimal TF seç.

        Dirsek noktası: TF artarken G'nin en az arttığı (veya azaldığı) nokta.
        Min kaldıraç filtresi uygulanır.
        """
        result = ZoomResult()
        swing_n = sd.get("swing_n", 10)
        fee_pct = sd.get("fee_pct", 0.08)
        slippage_pct = sd.get("slippage_pct", 0.03)
        fee_total = fee_pct + slippage_pct
        liq_seviye = sd.get("liq_seviyesi", 0.7)
        min_lev = sd.get("min_kaldirac", 2)
        # SL/liq çarpanları (trend için hesapla — worst case)
        sl_mult = sd.get("sl_carpan_trend", 1.5)
        liq_mult = sd.get("pratik_liq_carpan_trend", 3.0)

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

            # Kaldıraç hesapla (fee + slippage aware)
            teorik_liq = (G * liq_mult + fee_total) / liq_seviye
            leverage = int(100.0 / teorik_liq) if teorik_liq > 0 else 1
            leverage = max(1, leverage)

            verimlilik = tf_minutes / G if G > 0 else 0

            zr = ZoomTFResult(
                tf=tf_name,
                minutes=tf_minutes,
                G=G,
                I=wave.I,
                cv=wave.cv,
                wave_count=len(wave.backward_waves) + len(wave.forward_waves),
                verimlilik=verimlilik,
                leverage=leverage,
            )
            tf_results.append(zr)

        if not tf_results:
            return result

        result.all_tfs = tf_results

        # G artış hızı hesapla (dirsek bulmak için)
        # Normalize: G_artis_orani = (G_next - G_curr) / G_curr (yüzdesel artış)
        for i in range(len(tf_results) - 1):
            curr = tf_results[i]
            nxt = tf_results[i + 1]
            if curr.G > 0:
                curr.g_artis_hizi = (nxt.G - curr.G) / curr.G
            else:
                curr.g_artis_hizi = 0

        # Dirsek noktası seçim algoritması:
        # 1. Kaldıraç >= min_lev filtresi
        # 2. G'nin azaldığı nokta varsa (negatif artış) → dirsek!
        # 3. Yoksa G'nin en az arttığı nokta (en küçük artış oranı)
        # 4. Beraberliklerde daha büyük TF tercih edilir
        eligible_tfs = [t for t in tf_results if t.leverage >= min_lev]

        if not eligible_tfs:
            # Min kaldıraç sağlayan TF yok — en yüksek kaldıraçlıyı al
            eligible_tfs = sorted(tf_results, key=lambda t: -t.leverage)
            best = eligible_tfs[0]
        else:
            # Önce G azalan (negatif artış) noktaları ara
            decreasing = [t for t in eligible_tfs if t.g_artis_hizi < 0]
            if decreasing:
                # G azalan noktalar arasında en büyük TF'yi al (en güvenilir dirsek)
                best = max(decreasing, key=lambda t: t.minutes)
                result.dirsek_index = eligible_tfs.index(best)
            else:
                # G hiç azalmıyor → en düşük artış oranlı TF = dirsek
                # Son TF'nin artış hızı 0 olabilir (hesaplanmadı), onu hariç tut
                candidates_with_rate = [t for t in eligible_tfs if t.g_artis_hizi != 0]
                if candidates_with_rate:
                    best = min(candidates_with_rate, key=lambda t: t.g_artis_hizi)
                    result.dirsek_index = eligible_tfs.index(best)
                else:
                    # Tek TF veya hepsi 0 → en yüksek kaldıraçlıyı al
                    best = max(eligible_tfs, key=lambda t: t.leverage)

        result.optimal_tf = best.tf
        result.optimal_minutes = best.minutes
        result.optimal_G = best.G
        result.optimal_leverage = best.leverage

        # Orta ve makro TF'leri türet (×12 kuralı)
        tf_carpan = sd.get("tf_carpan", 12)
        result.mid_tf = self._derive_nearest_tf(best.minutes * tf_carpan, klines_by_tf)
        mid_minutes = next((m for t, m in ZOOM_TF_LADDER if t == result.mid_tf), best.minutes * tf_carpan)
        result.macro_tf = self._derive_nearest_tf(mid_minutes * tf_carpan, klines_by_tf)

        logger.debug(f"[Zoom] {tf_results[0].tf if tf_results else '?'} → "
                     f"optimal={best.tf} G={best.G:.3f}% lev={best.leverage}x "
                     f"verimlilik={best.verimlilik:.1f} "
                     f"mid={result.mid_tf} macro={result.macro_tf}")

        return result

    @staticmethod
    def _derive_nearest_tf(target_minutes: float,
                           klines_by_tf: dict[str, list]) -> str:
        """Hedef dakikaya en yakın Binance TF'yi bul (verisi olan)."""
        # 1w = 10080 dakika, ladder'da yoksa ekleyelim
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
            # Fallback: en büyük mevcut TF
            for tf_name, tf_min in reversed(full_ladder):
                if klines_by_tf.get(tf_name):
                    return tf_name
            return "1d"

        return best_tf

    # ────── Direction (Yön) ──────

    def _determine_direction(self, klines_macro, klines_mid,
                             klines_micro, sd: dict,
                             macro_tf: str = "1d",
                             mid_tf: str = "1h",
                             micro_tf: str = "5m") -> DirectionResult:
        """MTF yön oylama. Mod: ağırlıklı veya mutabakat (3/3)."""
        result = DirectionResult()

        mutabakat_mode = sd.get("yon_mutabakat_modu", False)
        macro_w = sd.get("makro_agirlik", 0.5)
        mid_w = sd.get("orta_agirlik", 0.3)
        micro_w = sd.get("mikro_agirlik", 0.2)
        threshold = sd.get("yon_belirsiz_esik", 0.1)

        result.macro = self._vote_direction(klines_macro, macro_tf)
        result.mid = self._vote_direction(klines_mid, mid_tf)
        result.micro = self._vote_direction(klines_micro, micro_tf)

        # Yön tespiti
        macro_dir = "LONG" if result.macro.score > 0 else "SHORT" if result.macro.score < 0 else "FLAT"
        mid_dir = "LONG" if result.mid.score > 0 else "SHORT" if result.mid.score < 0 else "FLAT"
        micro_dir = "LONG" if result.micro.score > 0 else "SHORT" if result.micro.score < 0 else "FLAT"

        dirs = [macro_dir, mid_dir, micro_dir]
        non_flat = [d for d in dirs if d != "FLAT"]

        # 3/3 uyum kontrolü
        if len(non_flat) >= 3 and len(set(non_flat)) == 1:
            result.aligned = True
            result.strength = "STRONG"

        if mutabakat_mode:
            # Mutabakat modu: 3/3 veya 2/3 (ayarlanabilir)
            min_katman = sd.get("yon_min_katman", 3)  # 2 veya 3

            if len(non_flat) < min_katman:
                result.direction = "SKIP"
                result.strength = "WEAK"
            else:
                # Çoğunluk yönü
                long_count = sum(1 for d in non_flat if d == "LONG")
                short_count = sum(1 for d in non_flat if d == "SHORT")

                if long_count >= min_katman:
                    result.direction = "LONG"
                elif short_count >= min_katman:
                    result.direction = "SHORT"
                else:
                    result.direction = "SKIP"
                    result.strength = "WEAK"

                if not result.aligned and result.direction != "SKIP":
                    result.strength = "MODERATE"
        else:
            # Ağırlıklı oylama modu (orijinal)
            result.total_score = (
                result.macro.score * macro_w +
                result.mid.score * mid_w +
                result.micro.score * micro_w
            )

            if result.total_score > threshold:
                result.direction = "LONG"
            elif result.total_score < -threshold:
                result.direction = "SHORT"
            else:
                result.direction = "SKIP"

            # Çatışma kuralı
            if macro_dir != "FLAT" and mid_dir != "FLAT" and macro_dir != mid_dir:
                if not result.aligned:
                    result.direction = "SKIP"
                    result.strength = "WEAK"
            elif macro_dir == mid_dir and macro_dir != "FLAT":
                if not result.aligned:
                    result.strength = "MODERATE"
            elif not result.aligned:
                result.strength = "WEAK"

        # Total score her durumda hesapla (GUI için)
        result.total_score = (
            result.macro.score * macro_w +
            result.mid.score * mid_w +
            result.micro.score * micro_w
        )

        return result

    def _vote_direction(self, klines: list, tf_name: str) -> DirectionVote:
        """Tek timeframe'de EMA + MACD + RSI oylama."""
        vote = DirectionVote(timeframe=tf_name)

        if not klines or len(klines) < 30:
            return vote

        sd = self._config.get("system_d", {})
        closes = np.array([float(k[4]) for k in klines])

        # EMA 9/21
        ema_fast_p = sd.get("ema_fast", 9)
        ema_slow_p = sd.get("ema_slow", 21)
        ema_fast = self._ema(closes, ema_fast_p)
        ema_slow = self._ema(closes, ema_slow_p)
        vote.ema_fast = ema_fast
        vote.ema_slow = ema_slow

        if ema_fast > ema_slow:
            vote.ema_vote = 1.0
        elif ema_fast < ema_slow:
            vote.ema_vote = -1.0

        # MACD
        macd_fast_p = sd.get("macd_fast", 8)
        macd_slow_p = sd.get("macd_slow", 17)
        macd_sig_p = sd.get("macd_signal", 9)
        macd_line = self._ema(closes, macd_fast_p) - self._ema(closes, macd_slow_p)
        signal_line = self._ema_from_values(
            self._macd_line_series(closes, macd_fast_p, macd_slow_p), macd_sig_p)
        hist = macd_line - signal_line
        vote.macd_hist = hist

        if hist > 0:
            vote.macd_vote = 1.0
        elif hist < 0:
            vote.macd_vote = -1.0

        # RSI
        rsi_p = sd.get("rsi_periyot", 14)
        rsi = self._rsi(closes, rsi_p)
        vote.rsi_value = rsi

        if rsi > 50:
            vote.rsi_vote = 1.0
        elif rsi < 50:
            vote.rsi_vote = -1.0

        vote.score = (vote.ema_vote + vote.macd_vote + vote.rsi_vote) / 3.0
        return vote

    # ────── Regime (Rejim) ──────

    def _determine_regime(self, klines_mid: list, sd: dict) -> RegimeResult:
        """ADX + BB Width + ER ile rejim tespiti (orta TF verileri)."""
        result = RegimeResult()

        if not klines_mid or len(klines_mid) < 30:
            result.regime = "GREY"
            return result

        closes = np.array([float(k[4]) for k in klines_mid])
        highs = np.array([float(k[2]) for k in klines_mid])
        lows = np.array([float(k[3]) for k in klines_mid])

        adx_trend = sd.get("adx_trend_esik", 25)
        adx_ranging = sd.get("adx_ranging_esik", 20)
        er_esik = sd.get("er_trend_esik", 0.3)

        # 1. ADX
        adx = self._adx(highs, lows, closes, 14)
        result.adx = adx
        if adx > adx_trend:
            result.adx_vote = "TREND"
            result.trend_votes += 1
        elif adx < adx_ranging:
            result.adx_vote = "RANGING"
            result.ranging_votes += 1
        else:
            result.adx_vote = "GREY"

        # 2. BB Width (genişliyor mu?)
        bb_period = sd.get("bb_periyot", 20)
        bb_std = sd.get("bb_std", 2.0)
        bb_upper, bb_middle, bb_lower = self._bollinger(closes, bb_period, bb_std)

        if bb_middle > 0:
            bb_width_now = (bb_upper - bb_lower) / bb_middle
        else:
            bb_width_now = 0
        result.bb_width = bb_width_now

        if len(closes) >= bb_period + 5:
            prev_closes = closes[:-5]
            pb_u, pb_m, pb_l = self._bollinger(prev_closes, bb_period, bb_std)
            if pb_m > 0:
                bb_width_prev = (pb_u - pb_l) / pb_m
                result.bb_width_expanding = bb_width_now > bb_width_prev

        if result.bb_width_expanding:
            result.bb_vote = "TREND"
            result.trend_votes += 1
        else:
            result.bb_vote = "RANGING"
            result.ranging_votes += 1

        # 3. Efficiency Ratio
        er = compute_efficiency_ratio(closes[-50:] if len(closes) >= 50 else closes)
        result.er = er
        if er > er_esik:
            result.er_vote = "TREND"
            result.trend_votes += 1
        else:
            result.er_vote = "RANGING"
            result.ranging_votes += 1

        # 2/3 oylama
        if result.trend_votes >= 2:
            result.regime = "TREND"
        elif result.ranging_votes >= 2:
            result.regime = "RANGING"
        else:
            result.regime = "GREY"

        return result

    # ────── Leverage (Kaldıraç) ──────

    def _calculate_leverage_from_g(self, G: float, regime: str, sd: dict,
                                   wave_count: int = 0, cv: float = 0.0,
                                   I: float = 0.0,
                                   binance_max_lev: int = 125) -> LeverageCalc:
        """Zoom'dan gelen G ile kaldıraç hesapla.

        Ters G hesabı: kaldıraç max'ı aşarsa, G'yi geriye dönerek büyütür.
        Böylece SL/TP seviyeleri gerçek kaldıraca uygun hale gelir.
        """
        calc = LeverageCalc()
        calc.fee_pct = sd.get("fee_pct", 0.08)
        calc.slippage_pct = sd.get("slippage_pct", 0.03)
        calc.G_raw = G
        calc.G = G
        calc.I = I
        calc.cv = cv
        calc.wave_count = wave_count

        if G < 0.01:
            return calc

        fee_total = calc.fee_pct + calc.slippage_pct  # fee + slippage

        # Rejime göre çarpanlar
        if regime == "TREND":
            sl_mult = sd.get("sl_carpan_trend", 1.5)
            liq_mult = sd.get("pratik_liq_carpan_trend", 3.0)
        else:  # RANGING veya GREY
            sl_mult = sd.get("sl_carpan_ranging", 2.0)
            liq_mult = sd.get("pratik_liq_carpan_ranging", 4.0)

        liq_seviye = sd.get("liq_seviyesi", 0.7)

        # İlk hesap: orijinal G ile
        teorik_liq = (G * liq_mult + fee_total) / liq_seviye
        raw_leverage = int(100.0 / teorik_liq) if teorik_liq > 0 else 1
        raw_leverage = max(1, raw_leverage)

        # Max kaldıraç sınırı (config + Binance)
        max_lev = min(sd.get("max_kaldirac", 125), binance_max_lev)

        if raw_leverage > max_lev:
            # ── Ters G Hesabı ──
            # max_lev'den geriye: teorik_liq = 100 / max_lev
            # pratik_liq = teorik_liq × liq_seviye - fee_total
            # G_efektif = pratik_liq / liq_mult
            teorik_liq_eff = 100.0 / max_lev
            pratik_liq_eff = teorik_liq_eff * liq_seviye - fee_total
            if pratik_liq_eff > 0:
                G_eff = pratik_liq_eff / liq_mult
            else:
                G_eff = G  # fallback

            calc.G = G_eff
            calc.g_adjusted = True
            calc.sl_pct = G_eff * sl_mult
            calc.pratik_liq_pct = G_eff * liq_mult
            calc.teorik_liq_pct = teorik_liq_eff
            calc.max_leverage = max_lev

            logger.debug(f"[Ters G] G_raw={G:.4f}% → G_eff={G_eff:.4f}% "
                         f"(lev {raw_leverage}x → {max_lev}x, "
                         f"SL {G*sl_mult:.3f}% → {calc.sl_pct:.3f}%)")
        else:
            # Normal hesap
            calc.sl_pct = G * sl_mult
            calc.pratik_liq_pct = G * liq_mult
            calc.teorik_liq_pct = teorik_liq
            calc.max_leverage = raw_leverage

        calc.max_leverage = max(1, calc.max_leverage)
        return calc

    # ────── Exit Params ──────

    def _set_exit_params(self, result: SystemDScanResult, klines_mid: list,
                         sd: dict) -> None:
        """TP, trailing parametrelerini ayarla.
        BB hesabı orta TF'den yapılır (rejim ile aynı ölçek)."""
        G = result.leverage_calc.G

        if result.regime == "TREND":
            # Trailing stop, TP yok
            result.tp_pct = 0.0
            result.trailing_trigger_pct = G * sd.get("trailing_tetik_g_carpan", 2.0)
            result.trailing_callback_pct = G * sd.get("trailing_mesafe_g_carpan", 0.5)
        else:
            # Sabit TP: BB karşı bant (orta TF) veya 3G
            tp_g_mult = sd.get("ranging_tp_g_carpan", 3.0)
            tp_from_g = G * tp_g_mult

            # BB mesafesi — orta TF'den (rejimle aynı ölçek)
            tp_from_bb = tp_from_g
            if klines_mid and len(klines_mid) >= 20:
                closes = np.array([float(k[4]) for k in klines_mid])
                bb_period = sd.get("bb_periyot", 20)
                bb_std_val = sd.get("bb_std", 2.0)
                bb_u, bb_m, bb_l = self._bollinger(closes, bb_period, bb_std_val)
                price = closes[-1]

                result.bb_upper = bb_u
                result.bb_lower = bb_l
                result.bb_middle = bb_m

                if result.direction == "LONG" and bb_u > 0 and price > 0:
                    tp_from_bb = (bb_u - price) / price * 100
                elif result.direction == "SHORT" and bb_l > 0 and price > 0:
                    tp_from_bb = (price - bb_l) / price * 100

            # Yakın olanı al
            result.tp_pct = min(tp_from_g, tp_from_bb) if tp_from_bb > 0 else tp_from_g
            result.trailing_trigger_pct = 0.0
            result.trailing_callback_pct = 0.0

    # ────── Entry Price ──────

    def _set_entry_price(self, result: SystemDScanResult, klines_micro: list,
                         sd: dict) -> None:
        """Limit giriş fiyatı hesapla (mikro TF'den)."""
        if not klines_micro:
            return

        closes = np.array([float(k[4]) for k in klines_micro])
        highs = np.array([float(k[2]) for k in klines_micro])
        lows = np.array([float(k[3]) for k in klines_micro])
        price = closes[-1]
        result.price = price

        atr = self._atr(highs, lows, closes, 14)
        result.atr = atr
        if price > 0:
            result.atr_pct = (atr / price) * 100

        offset = sd.get("limit_atr_offset", 0.1)
        offset_amount = atr * offset
        result.entry_offset_pct = (offset_amount / price * 100) if price > 0 else 0

        if result.direction == "LONG":
            result.entry_price = price - offset_amount
        elif result.direction == "SHORT":
            result.entry_price = price + offset_amount
        else:
            result.entry_price = price

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

    @staticmethod
    def _bollinger(closes: np.ndarray, period: int = 20,
                   std_mult: float = 2.0) -> tuple[float, float, float]:
        if len(closes) < period:
            m = float(np.mean(closes)) if len(closes) > 0 else 0.0
            return m, m, m
        window = closes[-period:]
        middle = float(np.mean(window))
        std = float(np.std(window, ddof=1))
        return middle + std_mult * std, middle, middle - std_mult * std
