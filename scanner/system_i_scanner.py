"""System I Scanner — Unified Trading System.

Önceki 8 sistemin (A-H) en iyi yapılarını tek bir tutarlı sistemde birleştirir:
  - G dalga boyu (ATR değil) tüm risk hesaplarının temeli
  - Zoom diyafram ile coin bazlı optimal timeframe (D'den)
  - ER/Hurst ile rejim tespiti (B'den)
  - P(win)/EV istatistiksel doğrulama (F'den)
  - MTF yön oylama (D'den)
  - Fee-aware tüm hesaplamalar
  - Backtest optimizer (G/H'den)

Akış:
  Faz 1 — Hızlı Pre-Filtre: 3 indikatör + hard filtreler (her 60s)
  Faz 2 — Derin Analiz: Zoom + Yön + Rejim + Kaldıraç + P(win)/EV + Skor
"""
import math
import time
import threading
import numpy as np
from concurrent.futures import ThreadPoolExecutor, Future
from dataclasses import dataclass, field
from typing import Optional
from loguru import logger
from core.config_manager import ConfigManager
from indicators.indicator_engine import IndicatorEngine
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

# ─────────────────────────── Constants ───────────────────────────

BINANCE_TFS = [
    "1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h",
    "6h", "8h", "12h", "1d", "3d", "1w",
]

TF_MINUTES = {
    "1m": 1, "3m": 3, "5m": 5, "15m": 15, "30m": 30,
    "1h": 60, "2h": 120, "4h": 240, "6h": 360, "8h": 480,
    "12h": 720, "1d": 1440, "3d": 4320, "1w": 10080,
}

# Zoom diyafram merdiveni — Binance'in desteklediği TÜM timeframe'ler
ZOOM_TF_LADDER = [
    ("1m", 1), ("3m", 3), ("5m", 5), ("15m", 15), ("30m", 30),
    ("1h", 60), ("2h", 120), ("4h", 240), ("6h", 360), ("8h", 480),
    ("12h", 720), ("1d", 1440), ("3d", 4320), ("1w", 10080),
]

# TF'ye göre dinamik mum sayısı — kısa TF'lerde daha fazla mum (daha geniş zaman penceresi)
# Amaç: her TF'de yeterli dalga sayısı (min ~10 geri dalga) garantilemek
ZOOM_KLINE_LIMITS = {
    "1m": 1500, "3m": 1000, "5m": 1000,       # kısa: 1-3.5 gün
    "15m": 500, "30m": 500,                     # orta: 5-10 gün
    "1h": 500, "2h": 300,                       # geniş: 20-25 gün
    "4h": 200, "6h": 200, "8h": 200,           # uzun: 33-67 gün
    "12h": 200, "1d": 200, "3d": 200, "1w": 200,  # çok uzun: 100+ gün
}


# ─────────────────────────── Data Classes ───────────────────────────

@dataclass
class DirectionVoteI:
    """Tek bir TF'nin yön oyu."""
    timeframe: str = ""
    ema_vote: float = 0.0       # +1 LONG, -1 SHORT, 0 nötr
    macd_vote: float = 0.0
    rsi_vote: float = 0.0
    score: float = 0.0          # ortalama (-1 ile +1)
    rsi_value: float = 50.0
    macd_hist: float = 0.0


@dataclass
class DirectionResultI:
    """Çoklu TF yön oylama sonucu."""
    direction: str = "SKIP"         # LONG / SHORT / SKIP
    strength: str = "NONE"          # STRONG / WEAK / NONE
    tf_count: int = 2               # kullanılan TF sayısı
    votes: list = field(default_factory=list)   # [DirectionVoteI, ...]
    aligned_count: int = 0          # aynı yöne oy veren TF sayısı
    leverage_multiplier: float = 1.0  # zayıf sinyal ise 0.7


@dataclass
class RegimeResultI:
    """ER + Hurst bazlı rejim tespiti."""
    regime: str = "UNDECIDED"       # TRENDING / RANGING / GRAY / UNDECIDED
    er: float = 0.0
    hurst: float = 0.5
    er_class: str = ""              # TRENDING / RANGING / GRAY
    hurst_class: str = ""           # TRENDING / RANGING / UNCERTAIN
    gray_resolution: str = ""       # TREND_LIKE / RANGING_LIKE / WEAK_TREND / WEAK_RANGING / NO_TRADE
    confidence: float = 0.0
    leverage_multiplier: float = 1.0


@dataclass
class ZoomTFResultI:
    """Tek bir TF'nin zoom analiz sonucu."""
    tf: str = ""
    minutes: int = 0
    G: float = 0.0              # geri dalga ortalaması (%)
    I: float = 0.0              # ileri dalga ortalaması (%)
    cv: float = 0.0
    wave_count: int = 0
    bw_count: int = 0           # geri dalga sayısı (güvenilirlik ölçüsü)
    fw_count: int = 0           # ileri dalga sayısı
    verimlilik: float = 0.0     # TF_dakika / G
    g_artis_hizi: float = 0.0   # bir sonraki TF'ye kıyasla G artış oranı
    g_tf_oran: float = 0.0      # G artış% / TF artış% (verimlilik oranı)


@dataclass
class ZoomResultI:
    """Zoom diyafram analiz sonucu — türetilen TF'ler dahil."""
    all_tfs: list = field(default_factory=list)      # [ZoomTFResultI, ...]
    yon_tf: str = "5m"          # yön belirleme TF'si (dirsek noktası)
    teyit_tf: str = "1d"        # teyit TF'si (yon_tf × confirm_multiplier)
    giris_tf: str = "5m"        # giriş TF'si (yon_tf / entry_divisor)
    mid_tf: str = ""            # orta TF (sadece tf_count=3 ise)
    optimal_G: float = 0.0
    optimal_I: float = 0.0
    wave_count: int = 0
    cv: float = 0.0
    dirsek_index: int = 0
    last_swing_price: float = 0.0  # son swing noktasi fiyati
    last_swing_type: str = ""      # SH veya SL


@dataclass
class LeverageCalcI:
    """Kaldıraç hesaplama sonucu."""
    G: float = 0.0
    I: float = 0.0
    sl_pct: float = 0.0
    pratik_liq_pct: float = 0.0
    teorik_liq_pct: float = 0.0
    max_leverage: int = 1
    fee_pct: float = 0.08
    slippage_pct: float = 0.04
    multipliers_applied: dict = field(default_factory=dict)


@dataclass
class ProbabilityResultI:
    """P(win)/EV hesaplama sonucu + optimal SL/TP."""
    p_win: float = 0.0
    p_loss: float = 0.0
    ev_pct: float = 0.0
    sufficient: bool = False
    # Optimal SL/TP (dalga verisinden turetilmis)
    optimal_sl_pct: float = 0.0       # fee-aware SL
    optimal_tp_pct: float = 0.0       # TP
    optimal_sl_g_mult: float = 1.5    # SL = X * G (fee haric)
    optimal_tp_g_mult: float = 2.5    # TP = X * G
    optimal_rr: float = 0.0           # TP / SL
    optimal_leverage: int = 1
    # Coklu dalga simulasyon sonuclari
    sim_wins: int = 0
    sim_losses: int = 0
    sim_timeouts: int = 0


@dataclass
class SystemIScanResult:
    """System I tarama sonucu — tüm bileşenler birleşik."""
    symbol: str = ""
    score: float = 0.0
    direction: str = ""             # LONG / SHORT
    pool: str = ""                  # TREND / RANGING / GRAY

    # Alt sonuçlar
    zoom: ZoomResultI = field(default_factory=ZoomResultI)
    direction_result: DirectionResultI = field(default_factory=DirectionResultI)
    regime: RegimeResultI = field(default_factory=RegimeResultI)
    leverage_calc: LeverageCalcI = field(default_factory=LeverageCalcI)
    probability: ProbabilityResultI = field(default_factory=ProbabilityResultI)

    # Türetilmiş değerler
    leverage: int = 1
    G: float = 0.0
    I: float = 0.0
    sl_pct: float = 0.0
    tp_pct: float = 0.0
    trailing_trigger_pct: float = 0.0
    trailing_callback_pct: float = 0.0
    ev_multiplier: float = 1.0

    # Giriş
    entry_type: str = "market"      # market / limit_wave / limit_g / limit
    entry_offset_pct: float = 0.0
    entry_price: float = 0.0
    entry_mode_detail: str = ""     # aciklama (wave_dip, wave_peak, bb_band, atr_offset, market)
    last_swing_price: float = 0.0   # son swing noktasi fiyati (dip veya tepe)
    last_swing_type: str = ""       # SL (dip) veya SH (tepe)
    entry_rsi_confirm: bool = False # alt TF'de RSI teyidi var mi
    entry_vol_confirm: bool = False # volume exhaustion teyidi var mi

    # Ladder TP (opsiyonel)
    tp1_pct: float = 0.0
    tp1_close_pct: float = 0.0
    tp2_pct: float = 0.0
    tp2_close_pct: float = 0.0

    # ROI TP (opsiyonel)
    roi_tp_pct: float = 0.0

    # Market context
    price: float = 0.0
    atr: float = 0.0
    atr_pct: float = 0.0
    rsi: float = 50.0
    funding_rate: float = 0.0
    spread_pct: float = 0.0
    ob_imbalance: float = 0.0
    ob_thin_book: bool = False
    volume_ratio: float = 1.0
    volume_24h: float = 0.0
    bb_upper: float = 0.0
    bb_middle: float = 0.0
    bb_lower: float = 0.0
    btc_conflict: bool = False

    # GUI convenience fields (top-level, deep_analyze sonunda doldurulur)
    strength: str = ""              # direction_result.strength
    regime_zone: str = ""           # regime.regime (TRENDING/RANGING/GRAY)
    er: float = 0.0                 # regime.er
    hurst: float = 0.5             # regime.hurst
    zoom_tf: str = ""              # zoom.yon_tf
    p_win: float = 0.0            # probability.p_win
    ev_pct: float = 0.0           # probability.ev_pct
    bb_proximity: float = 0.0     # BB bant yakınlık % (RANGING için)

    # Eligibility
    eligible: bool = False
    reject_reason: str = ""

    # Optimizer
    opt_status: str = "NONE"
    opt_result: OptResult = field(default_factory=OptResult)
    opt_score: float = 0.0


# ─────────────────────────── Scanner Class ───────────────────────────

class SystemIScanner:
    """System I: Unified Trading System.

    Faz 1: Hızlı pre-filtre (3 indikatör + hard filtreler)
    Faz 2: Derin analiz (zoom + yön + rejim + kaldıraç + P(win)/EV + skor)
    """

    def __init__(self, config: ConfigManager):
        self._config = config
        self._log = logger.bind(name="SystemI")
        self._ie = IndicatorEngine(config)

        # Caches
        self._lock = threading.RLock()
        self._zoom_cache: dict[str, tuple[float, ZoomResultI]] = {}   # {symbol: (timestamp, result)}
        self._regime_history: dict[str, list[str]] = {}               # {symbol: [son N rejim]}
        self._regime_cache: dict[str, RegimeResultI] = {}             # {symbol: son stabil rejim}
        self._opt_cache: dict[str, CoinOptCache] = {}
        self._opt_futures: dict[str, Future] = {}
        self._kline_cache: dict[str, tuple[float, list]] = {}         # {key: (timestamp, klines)}

        # Optimizer thread pool
        self._opt_executor = ThreadPoolExecutor(max_workers=2)

    # ────── Config helpers ──────

    def _cfg(self, key: str, default=None):
        """system_i config'den oku (dot-notation destekli)."""
        return self._config.get(f"system_i.{key}", default)

    def _cfg_lev(self, key: str, default=None):
        """system_i.leverage config'den oku."""
        return self._config.get(f"system_i.leverage.{key}", default)

    def _cfg_tf(self, key: str, default=None):
        """system_i.timeframe config'den oku."""
        return self._config.get(f"system_i.timeframe.{key}", default)

    def _cfg_entry(self, key: str, default=None):
        """system_i.entry config'den oku."""
        return self._config.get(f"system_i.entry.{key}", default)

    def _cfg_tp(self, key: str, default=None):
        """system_i.tp config'den oku."""
        return self._config.get(f"system_i.tp.{key}", default)

    def _cfg_scanner(self, key: str, default=None):
        """system_i.scanner config'den oku."""
        return self._config.get(f"system_i.scanner.{key}", default)

    def _cfg_opt(self, key: str, default=None):
        """system_i.backtest_optimizer config'den oku."""
        return self._config.get(f"system_i.backtest_optimizer.{key}", default)

    def _cfg_pos(self, key: str, default=None):
        """system_i.position_sizing config'den oku."""
        return self._config.get(f"system_i.position_sizing.{key}", default)

    def _cfg_score(self, key: str, default=None):
        """system_i.score_weights config'den oku."""
        return self._config.get(f"system_i.score_weights.{key}", default)

    def _cfg_regime(self, key: str, default=None):
        """system_i.regime config'den oku."""
        return self._config.get(f"system_i.regime.{key}", default)

    def _cfg_filter(self, key: str, default=None):
        """system_i.filters config'den oku."""
        return self._config.get(f"system_i.filters.{key}", default)

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # ZOOM DİYAFRAM — Section 2
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def compute_zoom(self, symbol: str, klines_by_tf: dict) -> ZoomResultI:
        """Zoom diyafram v2: Alttan yukari G/TF verimlilik taramasi.

        Mantik:
          1. Her TF'de zigzag analizi yap, G/I/dalga sayisi hesapla
          2. Minimum dalga filtresi (bw_count >= min_bw)
          3. En kucuk TF'den basla (max kaldirac)
          4. Yukari dogru cik: G/TF orani dusukse devam, yuksekse DUR
          5. G azaldiysa bedava TF uzatma (devam et)

        Args:
            symbol: Coin sembolu (cache key).
            klines_by_tf: {tf_str: [kline_list, ...]}

        Returns:
            ZoomResultI with derived TFs and optimal G.
        """
        # Cache kontrolu
        cache_ttl = self._cfg_scanner("zoom_cache_ttl", 120)
        with self._lock:
            cached = self._zoom_cache.get(symbol)
            if cached and (time.time() - cached[0]) < cache_ttl:
                return cached[1]

        result = ZoomResultI()
        swing_n = self._cfg("swing_n", 10)
        zoom_min_tf = self._cfg_tf("zoom_min_tf", "1m")
        zoom_max_tf = self._cfg_tf("zoom_max_tf", "1w")
        min_minutes = TF_MINUTES.get(zoom_min_tf, 1)
        max_minutes = TF_MINUTES.get(zoom_max_tf, 10080)
        min_bw = self._cfg_tf("zoom_min_backward_waves", 10)
        g_tf_verimli = self._cfg_tf("zoom_g_tf_efficient", 0.60)
        g_tf_verimsiz = self._cfg_tf("zoom_g_tf_inefficient", 0.80)

        # ---- ADIM 1: Her TF'de dalga analizi ----
        tf_results: list[ZoomTFResultI] = []

        for tf_name, tf_minutes in ZOOM_TF_LADDER:
            if tf_minutes < min_minutes or tf_minutes > max_minutes:
                continue

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

            bw_count = len(wave.backward_waves)
            fw_count = len(wave.forward_waves)
            verimlilik = tf_minutes / G if G > 0 else 0.0

            zr = ZoomTFResultI(
                tf=tf_name,
                minutes=tf_minutes,
                G=G,
                I=wave.I,
                cv=wave.cv,
                wave_count=bw_count + fw_count,
                bw_count=bw_count,
                fw_count=fw_count,
                verimlilik=verimlilik,
            )
            tf_results.append(zr)

        if not tf_results:
            return result

        result.all_tfs = tf_results

        # ---- ADIM 2: G artis hizi ve G/TF orani hesapla ----
        for i in range(len(tf_results)):
            if i < len(tf_results) - 1:
                curr = tf_results[i]
                nxt = tf_results[i + 1]
                if curr.G > 0:
                    curr.g_artis_hizi = (nxt.G - curr.G) / curr.G
            if i > 0:
                prev = tf_results[i - 1]
                g_artis_pct = (tf_results[i].G - prev.G) / prev.G * 100 if prev.G > 0 else 0
                tf_artis_pct = (tf_results[i].minutes - prev.minutes) / prev.minutes * 100
                tf_results[i].g_tf_oran = g_artis_pct / tf_artis_pct if tf_artis_pct > 0 else 0

        # ---- ADIM 3: Minimum dalga filtresi ----
        # Geri dalga (bw_count) >= min_bw olmayan TF'ler guvenilmez
        reliable = [t for t in tf_results if t.bw_count >= min_bw]

        if not reliable:
            # Hiçbir TF'de yeterli dalga yok → güvenilir G hesaplanamaz, reddet
            best_bw = max(tf_results, key=lambda t: t.bw_count)
            self._log.warning(
                f"[Zoom] {symbol}: Hicbir TF'de yeterli dalga yok "
                f"(min_bw={min_bw}), en iyi: {best_bw.tf} bw={best_bw.bw_count} — SKIP")
            return result

        # ---- ADIM 4: Alttan yukari G/TF verimlilik taramasi ----
        # En kucuk TF'den basla (max kaldirac, min G)
        # TF buyutmeye devam et: G/TF orani dusukse → verimli, devam
        # G/TF orani yuksekse → verimsiz, DUR, onceki TF optimal

        best = reliable[0]  # baslangic: en kucuk guvenilir TF

        for i in range(1, len(reliable)):
            curr = reliable[i]
            oran = curr.g_tf_oran
            g_azaldi = False

            # Onceki TF ile karsilastir
            prev = reliable[i - 1]
            if prev.G > 0 and curr.G < prev.G:
                g_azaldi = True

            if g_azaldi:
                # G azaldi = TF uzadi ama G dustu! Bedava uzatma, devam
                best = curr
                self._log.debug(
                    f"[Zoom] {symbol}: {curr.tf} G AZALDI ({prev.G:.3f}%->{curr.G:.3f}%), devam")
            elif oran < g_tf_verimli:
                # Verimli gecis: TF artti ama G az artti, devam
                best = curr
                self._log.debug(
                    f"[Zoom] {symbol}: {curr.tf} VERIMLI (oran={oran:.3f}), devam")
            elif oran < g_tf_verimsiz:
                # Kabul edilebilir, devam ama dikkatli
                best = curr
                self._log.debug(
                    f"[Zoom] {symbol}: {curr.tf} KABUL (oran={oran:.3f}), devam")
            else:
                # Verimsiz: G TF kadar veya daha fazla artti, DUR
                self._log.debug(
                    f"[Zoom] {symbol}: {curr.tf} VERIMSIZ (oran={oran:.3f}), DUR! "
                    f"Optimal={best.tf}")
                break

        # yon_tf = secilen optimal TF
        result.dirsek_index = tf_results.index(best) if best in tf_results else 0
        result.yon_tf = best.tf
        result.optimal_G = best.G
        result.optimal_I = best.I
        result.wave_count = best.wave_count
        result.cv = best.cv
        yon_minutes = best.minutes

        # Son swing noktasini bul (giris fiyati hesabi icin)
        yon_klines = klines_by_tf.get(best.tf, [])
        if yon_klines and len(yon_klines) >= swing_n * 3:
            h = np.array([float(k[2]) for k in yon_klines])
            l = np.array([float(k[3]) for k in yon_klines])
            sw = detect_zigzag_swings(h, l, swing_n)
            if sw:
                result.last_swing_price = sw[-1].price
                result.last_swing_type = sw[-1].type

        # Teyit TF turet: yon_tf x confirm_tf_multiplier
        confirm_mult = self._cfg_tf("confirm_tf_multiplier", 12)
        tf_rounding = self._cfg_tf("tf_rounding", "up")
        teyit_minutes = yon_minutes * confirm_mult
        result.teyit_tf = self._round_tf(teyit_minutes, tf_rounding)

        # Giris TF turet
        entry_tf_mode = self._cfg_tf("entry_tf_mode", "auto")
        if entry_tf_mode == "auto":
            entry_divisor = self._cfg_tf("entry_tf_divisor", 3)
            giris_minutes = max(1, yon_minutes / entry_divisor)
            result.giris_tf = self._round_tf(giris_minutes, "down")
        elif entry_tf_mode == "same":
            result.giris_tf = result.yon_tf
        else:
            result.giris_tf = self._cfg_tf("entry_tf_manual", "5m")

        # Mid TF (sadece tf_count=3 ise)
        tf_count = self._cfg_tf("tf_count", 2)
        if tf_count >= 3:
            mid_mult = self._cfg_tf("mid_multiplier", 4)
            mid_minutes = yon_minutes * mid_mult
            result.mid_tf = self._round_tf(mid_minutes, tf_rounding)

        # Cache'e yaz
        with self._lock:
            self._zoom_cache[symbol] = (time.time(), result)

        self._log.info(
            f"[Zoom] {symbol}: yon_tf={result.yon_tf} G={result.optimal_G:.3f}% "
            f"I={result.optimal_I:.3f}% teyit={result.teyit_tf} giris={result.giris_tf} "
            f"waves={result.wave_count} bw={best.bw_count} cv={result.cv:.2f} "
            f"g_tf_oran={best.g_tf_oran:.3f}"
        )

        return result

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # YÖN BELİRLEME — Section 3
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def compute_direction(self, klines_by_tf: dict,
                          zoom: ZoomResultI) -> DirectionResultI:
        """MTF yön oylama: her TF için 3 indikatör, çoğunluk kararı.

        Args:
            klines_by_tf: {tf_str: [kline_list, ...]}
            zoom: ZoomResultI — türetilen TF'ler

        Returns:
            DirectionResultI with direction, strength, votes.
        """
        result = DirectionResultI()
        tf_count = self._cfg_tf("tf_count", 2)

        # Oylama yapılacak TF'leri belirle
        tfs_to_vote = []
        # Yon TF her zaman var
        tfs_to_vote.append(zoom.yon_tf)
        # Teyit TF her zaman var
        tfs_to_vote.append(zoom.teyit_tf)
        # Mid TF (tf_count=3 ise)
        if tf_count >= 3 and zoom.mid_tf:
            tfs_to_vote.append(zoom.mid_tf)

        votes: list[DirectionVoteI] = []
        for tf in tfs_to_vote:
            klines = klines_by_tf.get(tf, [])
            if not klines or len(klines) < 30:
                # Veri yoksa nötr oy
                votes.append(DirectionVoteI(timeframe=tf))
                continue
            vote = self._vote_single_tf(klines, tf)
            votes.append(vote)

        result.votes = votes
        result.tf_count = len(votes)

        # TF yönlerini belirle
        directions = []
        for v in votes:
            if v.score >= 0.33:
                directions.append("LONG")
            elif v.score <= -0.33:
                directions.append("SHORT")
            else:
                directions.append("FLAT")

        non_flat = [d for d in directions if d != "FLAT"]
        if not non_flat:
            result.direction = "SKIP"
            result.strength = "NONE"
            return result

        # Çoğunluk yönü
        long_count = sum(1 for d in directions if d == "LONG")
        short_count = sum(1 for d in directions if d == "SHORT")

        if long_count > short_count:
            majority = "LONG"
            aligned = long_count
        elif short_count > long_count:
            majority = "SHORT"
            aligned = short_count
        else:
            # Eşit → SKIP
            result.direction = "SKIP"
            result.strength = "NONE"
            return result

        result.aligned_count = aligned
        total_tfs = len(votes)

        if tf_count == 2:
            # 2 TF: 2/2 gerekli → STRONG, aksi SKIP
            if aligned >= 2:
                result.direction = majority
                result.strength = "STRONG"
                result.leverage_multiplier = 1.0
            else:
                result.direction = "SKIP"
                result.strength = "NONE"
        else:
            # 3 TF: 3/3 STRONG, 2/3 WEAK (x0.7), 1/3 SKIP
            if aligned >= total_tfs:
                result.direction = majority
                result.strength = "STRONG"
                result.leverage_multiplier = 1.0
            elif aligned >= 2:
                result.direction = majority
                result.strength = "WEAK"
                result.leverage_multiplier = self._cfg_lev("weak_signal_multiplier", 0.7)
            else:
                result.direction = "SKIP"
                result.strength = "NONE"

        return result

    def _vote_single_tf(self, klines: list, tf: str) -> DirectionVoteI:
        """Tek TF için 3 indikatör oylama (EMA, MACD, RSI)."""
        vote = DirectionVoteI(timeframe=tf)

        closes = np.array([float(k[4]) for k in klines])
        if len(closes) < 26:
            return vote

        # EMA 9/21
        ema_fast = self._ema(closes, 9)
        ema_slow = self._ema(closes, 21)
        if ema_fast > 0 and ema_slow > 0:
            gap_pct = abs(ema_fast - ema_slow) / ema_slow * 100
            min_gap = 0.05
            if ema_fast > ema_slow and gap_pct >= min_gap:
                vote.ema_vote = 1.0
            elif ema_fast < ema_slow and gap_pct >= min_gap:
                vote.ema_vote = -1.0

        # MACD 8/17/9
        macd_line, signal_line, histogram = self._macd(closes, 8, 17, 9)
        vote.macd_hist = histogram
        if len(closes) >= 2:
            # Histogram önceki değerini hesapla (momentum)
            closes_prev = closes[:-1]
            if len(closes_prev) >= 26:
                _, _, hist_prev = self._macd(closes_prev, 8, 17, 9)
            else:
                hist_prev = histogram

            if histogram > 0 and histogram > hist_prev:
                vote.macd_vote = 1.0       # pozitif + momentum artıyor
            elif histogram > 0:
                vote.macd_vote = 0.5       # pozitif ama momentum azalıyor
            elif histogram < 0 and histogram < hist_prev:
                vote.macd_vote = -1.0      # negatif + momentum düşüyor
            elif histogram < 0:
                vote.macd_vote = -0.5      # negatif ama momentum toparlanıyor

        # RSI 14 — config'ten oku
        rsi_long_th = self._cfg("direction.rsi_long_threshold", 52)
        rsi_short_th = self._cfg("direction.rsi_short_threshold", 48)
        rsi = self._rsi(closes, 14)
        vote.rsi_value = rsi
        if rsi > rsi_long_th:
            vote.rsi_vote = 1.0
        elif rsi < rsi_short_th:
            vote.rsi_vote = -1.0

        # TF yönü = 3 oyun ortalaması
        vote.score = (vote.ema_vote + vote.macd_vote + vote.rsi_vote) / 3.0
        return vote

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # REJİM TESPİTİ — Section 4
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def compute_regime(self, symbol: str, klines_yon_tf: list,
                       klines_teyit_tf: list = None,
                       direction_result: DirectionResultI = None) -> RegimeResultI:
        """ER + Hurst bazlı rejim tespiti + hysteresis.

        Args:
            symbol: Coin sembolü (hysteresis cache key).
            klines_yon_tf: Yön TF mumları.
            klines_teyit_tf: Teyit TF mumları (opsiyonel, gray zone kararı için).
            direction_result: Yön sonucu (gray zone TF hizalama kararı için).

        Returns:
            RegimeResultI with regime, confidence, multipliers.
        """
        raw = self._compute_regime_raw(klines_yon_tf, direction_result)

        # Hysteresis: N ardışık aynı okuma gerekli
        hysteresis_n = self._cfg_regime("hysteresis_count", 2)
        with self._lock:
            history = self._regime_history.get(symbol, [])
            history.append(raw.regime)
            if len(history) > hysteresis_n:
                history = history[-hysteresis_n:]
            self._regime_history[symbol] = history

            if len(history) >= hysteresis_n and len(set(history)) == 1:
                # Tüm son N okuma aynı → rejim değişikliği onaylandı
                final = raw
            else:
                cached = self._regime_cache.get(symbol)
                if cached:
                    # Henüz stabil değil → eski rejimi koru, raw değerleri güncelle
                    final = cached
                    final.er = raw.er
                    final.hurst = raw.hurst
                    final.er_class = raw.er_class
                    final.hurst_class = raw.hurst_class
                else:
                    # İlk okuma → direkt kabul (bootstrap)
                    final = raw

            self._regime_cache[symbol] = final

        return final

    def _compute_regime_raw(self, klines_yon_tf: list,
                            direction_result: DirectionResultI = None) -> RegimeResultI:
        """ER + Hurst ile ham rejim hesaplama (hysteresis öncesi)."""
        result = RegimeResultI()

        if not klines_yon_tf or len(klines_yon_tf) < 20:
            return result

        closes = np.array([float(k[4]) for k in klines_yon_tf])

        # ER hesapla
        result.er = compute_efficiency_ratio(closes)
        er_trending = self._cfg_regime("er_trending", 0.35)
        er_ranging = self._cfg_regime("er_ranging", 0.20)

        if result.er > er_trending:
            result.er_class = "TRENDING"
        elif result.er < er_ranging:
            result.er_class = "RANGING"
        else:
            result.er_class = "GRAY"

        # Hurst hesapla
        result.hurst = compute_hurst_exponent(closes)
        hurst_trending = self._cfg_regime("hurst_trending", 0.55)
        hurst_ranging = self._cfg_regime("hurst_ranging", 0.45)

        if result.hurst > hurst_trending:
            result.hurst_class = "TRENDING"
        elif result.hurst < hurst_ranging:
            result.hurst_class = "RANGING"
        else:
            result.hurst_class = "UNCERTAIN"

        # Rejim kararı
        if result.er_class == "TRENDING":
            result.regime = "TRENDING"
            result.confidence = 1.0 if result.hurst_class == "TRENDING" else 0.7
            result.leverage_multiplier = 1.0
        elif result.er_class == "RANGING":
            result.regime = "RANGING"
            result.confidence = 1.0 if result.hurst_class == "RANGING" else 0.7
            result.leverage_multiplier = 1.0
        else:
            # GRAY zone → Hurst + TF hizalama ile karar (Section 4.2)
            result.regime = "GRAY"
            aligned_count = 0
            total_tfs = 2
            if direction_result:
                aligned_count = direction_result.aligned_count
                total_tfs = len(direction_result.votes) if direction_result.votes else 2

            gray_zone_trading = self._cfg_regime("gray_zone_trading_enabled",
                                          self._cfg("optional_features.gray_zone_trading_enabled", True))

            if not gray_zone_trading:
                result.gray_resolution = "NO_TRADE"
                result.confidence = 0.0
                result.leverage_multiplier = 0.0
            elif result.hurst_class == "TRENDING":
                if aligned_count >= total_tfs:
                    result.gray_resolution = "TREND_LIKE"
                    result.confidence = 0.7
                    result.leverage_multiplier = self._cfg_lev("gray_zone_confirmed_mult", 0.7)
                else:
                    result.gray_resolution = "WEAK_TREND"
                    result.confidence = 0.3
                    result.leverage_multiplier = self._cfg_lev("gray_zone_uncertain_mult", 0.5)
            elif result.hurst_class == "RANGING":
                if aligned_count >= max(1, total_tfs - 1):
                    result.gray_resolution = "RANGING_LIKE"
                    result.confidence = 0.7
                    result.leverage_multiplier = self._cfg_lev("gray_zone_confirmed_mult", 0.7)
                else:
                    result.gray_resolution = "WEAK_RANGING"
                    result.confidence = 0.3
                    result.leverage_multiplier = self._cfg_lev("gray_zone_uncertain_mult", 0.5)
            else:
                # Hurst uncertain
                if aligned_count >= total_tfs:
                    result.gray_resolution = "WEAK_TREND"
                    result.confidence = 0.3
                    result.leverage_multiplier = self._cfg_lev("gray_zone_uncertain_mult", 0.5)
                elif aligned_count >= max(1, total_tfs - 1):
                    result.gray_resolution = "WEAK_RANGING"
                    result.confidence = 0.3
                    result.leverage_multiplier = self._cfg_lev("gray_zone_uncertain_mult", 0.5)
                else:
                    result.gray_resolution = "NO_TRADE"
                    result.confidence = 0.0
                    result.leverage_multiplier = 0.0

        return result

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # KALDIRAC HESABI — Section 5
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def compute_leverage(self, G: float, regime: RegimeResultI,
                         direction_result: DirectionResultI,
                         cv: float = 0.0,
                         btc_conflict: bool = False) -> LeverageCalcI:
        """G bazlı kaldıraç hesaplama + çarpanlar.

        Args:
            G: Ortalama geri dalga boyu (%).
            regime: Rejim sonucu.
            direction_result: Yön sonucu (weak signal multiplier için).
            cv: Dalga tutarsızlığı (CV).
            btc_conflict: BTC ters korelasyon varsa True.

        Returns:
            LeverageCalcI with leverage, SL, multipliers.
        """
        calc = LeverageCalcI()
        calc.G = G
        calc.fee_pct = self._cfg_lev("fee_pct", 0.08)
        calc.slippage_pct = self._cfg_lev("slippage_pct", 0.04)

        if G < 0.01:
            return calc

        fee_total = calc.fee_pct + calc.slippage_pct
        liq_safety = self._cfg_lev("liq_safety_factor", 0.7)

        # Rejime göre SL ve liq çarpanları
        if isinstance(regime, str):
            effective_regime = regime
            gray_resolution = ""
        else:
            effective_regime = regime.regime
            gray_resolution = getattr(regime, "gray_resolution", "")
        if effective_regime == "GRAY":
            # Gray zone resolution'a göre karar
            if gray_resolution in ("TREND_LIKE", "WEAK_TREND"):
                effective_regime = "TRENDING"
            elif gray_resolution in ("RANGING_LIKE", "WEAK_RANGING"):
                effective_regime = "RANGING"
            else:
                # NO_TRADE — kaldıraç 0
                calc.max_leverage = 0
                return calc

        if effective_regime == "TRENDING":
            sl_mult = self._cfg_lev("trend_sl_g_mult", 1.5)
            liq_mult = self._cfg_lev("trend_liq_g_mult", 3.0)
        else:  # RANGING
            sl_mult = self._cfg_lev("ranging_sl_g_mult", 2.0)
            liq_mult = self._cfg_lev("ranging_liq_g_mult", 4.0)

        # Fee-aware SL
        calc.sl_pct = G * sl_mult + fee_total
        calc.pratik_liq_pct = G * liq_mult
        calc.teorik_liq_pct = (calc.pratik_liq_pct + calc.fee_pct) / liq_safety

        if calc.teorik_liq_pct > 0:
            raw_leverage = 100.0 / calc.teorik_liq_pct
        else:
            raw_leverage = 1.0

        # Çarpanlar uygula
        multipliers = {}
        final_mult = 1.0

        # 1. Zayıf sinyal (2/3 TF)
        if direction_result and direction_result.leverage_multiplier < 1.0:
            mult = direction_result.leverage_multiplier
            multipliers["weak_signal"] = mult
            final_mult *= mult

        # 2. Gray zone
        if not isinstance(regime, str) and regime.regime == "GRAY" and regime.leverage_multiplier < 1.0:
            mult = regime.leverage_multiplier
            multipliers["gray_zone"] = mult
            final_mult *= mult

        # 3. CV yüksek (dalga tutarsızlığı)
        cv_threshold = self._cfg_lev("cv_threshold", 0.4)
        if cv > cv_threshold:
            mult = self._cfg_lev("cv_multiplier", 0.7)
            multipliers["high_cv"] = mult
            final_mult *= mult

        # 4. BTC ters korelasyon
        if btc_conflict:
            mult = 0.5
            multipliers["btc_conflict"] = mult
            final_mult *= mult

        calc.multipliers_applied = multipliers
        leverage = raw_leverage * final_mult

        # Clamp
        min_lev = self._cfg_lev("min_leverage", 2)
        max_lev = self._cfg_lev("max_leverage", 125)
        leverage = max(min_lev, min(leverage, max_lev))
        calc.max_leverage = int(leverage)

        return calc

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # P(win) / EV — Section 10
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def compute_probability(self, direction: str, G: float, sl_pct: float,
                            tp_pct: float, leverage: int,
                            klines_yon_tf: list) -> ProbabilityResultI:
        """P(win)/EV hesaplama + optimal SL/TP bulma.

        Coklu dalga simulasyonu: fiyat tek dalgada degil, birden fazla
        dalga boyunca net birikim ile TP veya SL'e ulasir.

        Optimal SL/TP: dalga verilerinden en yuksek EV'yi veren
        SL ve TP carpanlarini bulur.

        Args:
            direction: LONG / SHORT.
            G: Ortalama geri dalga (%).
            sl_pct: Default SL mesafesi (%).
            tp_pct: Default TP mesafesi (%).
            leverage: Default kaldirac.
            klines_yon_tf: Yon TF mumlari.

        Returns:
            ProbabilityResultI with p_win, ev_pct, optimal SL/TP.
        """
        prob = ProbabilityResultI()
        swing_n = self._cfg("swing_n", 10)
        fee_pct = self._cfg_lev("fee_pct", 0.08)
        fee_total = fee_pct + self._cfg_lev("slippage_pct", 0.04)

        if not klines_yon_tf or len(klines_yon_tf) < swing_n * 3:
            return prob

        highs = np.array([float(k[2]) for k in klines_yon_tf])
        lows = np.array([float(k[3]) for k in klines_yon_tf])
        closes = np.array([float(k[4]) for k in klines_yon_tf])

        swings = detect_zigzag_swings(highs, lows, swing_n)
        if len(swings) < 8:
            return prob

        prob.sufficient = True

        # ---- COKLU DALGA SIMULASYONU ----
        # Her uygun swing noktasindan trade baslat,
        # dalga dalga ilerle, net birikim TP/SL kontrolu

        def simulate(test_sl, test_tp):
            """Coklu dalga simulasyonu. Returns (wins, losses, timeouts)."""
            wins = losses = timeouts = 0
            for entry_idx in range(len(swings) - 2):
                entry_sw = swings[entry_idx]
                entry_price = entry_sw.price
                if entry_price <= 0:
                    continue

                # Yon filtresi: LONG sadece dip'ten, SHORT sadece tepe'den
                if direction == "LONG" and entry_sw.type != "SL":
                    continue
                if direction == "SHORT" and entry_sw.type != "SH":
                    continue

                # Giris noktasinda yon teyidi (EMA9>EMA21 + RSI)
                mum_idx = entry_sw.index
                if mum_idx < 25 or mum_idx >= len(closes):
                    continue
                window = closes[:mum_idx + 1]
                ema9 = self._ema(window, 9)
                ema21 = self._ema(window, 21)
                rsi_val = self._rsi(window, 14)

                if direction == "LONG":
                    if not (ema9 > ema21 and rsi_val > 48):
                        continue
                else:
                    if not (ema9 < ema21 and rsi_val < 52):
                        continue

                # Dalga dalga ilerle
                net_pct = 0.0
                hit_tp = hit_sl = False
                for j in range(entry_idx + 1, len(swings)):
                    prev_sw = swings[j - 1]
                    curr_sw = swings[j]
                    wave_pct = (curr_sw.price - prev_sw.price) / entry_price * 100
                    if direction == "SHORT":
                        wave_pct = -wave_pct
                    net_pct += wave_pct

                    if net_pct >= test_tp:
                        hit_tp = True
                        break
                    if net_pct <= -test_sl:
                        hit_sl = True
                        break

                if hit_tp:
                    wins += 1
                elif hit_sl:
                    losses += 1
                else:
                    timeouts += 1
            return wins, losses, timeouts

        # ---- DEFAULT SL/TP ILE HESAPLA ----
        w, l, t = simulate(sl_pct, tp_pct)
        total = w + l
        if total > 0:
            prob.p_win = w / total
            prob.p_loss = l / total
            fee_roi = fee_pct * leverage
            prob.ev_pct = round(
                prob.p_win * tp_pct * leverage -
                prob.p_loss * sl_pct * leverage -
                fee_roi, 2)
            prob.sim_wins = w
            prob.sim_losses = l
            prob.sim_timeouts = t

        # ---- OPTIMAL SL/TP ARAMA ----
        # Dalga verilerinden en yuksek EV veren SL/TP carpanlarini bul
        best_ev = prob.ev_pct
        best_sl_mult = sl_pct / G if G > 0 else 1.5  # mevcut carpan
        best_tp_mult = tp_pct / G if G > 0 else 2.5

        if G > 0.01:
            # SL min 1.2×G (0.7× çok yakın, normal gürültüde tetiklenir)
            for sl_m_10 in range(12, 30, 2):    # 1.2 - 2.9 arasi (x10)
                sl_mult = sl_m_10 / 10.0
                test_sl = G * sl_mult + fee_total

                # Bu SL'den kaldirac hesapla
                pratik_liq = test_sl * 2
                teorik_liq = (pratik_liq + fee_pct) / 0.7
                test_lev = max(1, min(int(100.0 / teorik_liq), 125)) if teorik_liq > 0 else 1

                for tp_m_10 in range(15, 65, 5):  # 1.5 - 6.0 arasi (x10)
                    tp_mult = tp_m_10 / 10.0
                    test_tp = G * tp_mult

                    rr = test_tp / test_sl if test_sl > 0 else 0

                    sw, sl, st = simulate(test_sl, test_tp)
                    stotal = sw + sl
                    if stotal < 3:
                        continue

                    pw = sw / stotal
                    fee_r = fee_pct * test_lev
                    ev = pw * test_tp * test_lev - (1 - pw) * test_sl * test_lev - fee_r

                    if ev > best_ev:
                        best_ev = ev
                        best_sl_mult = sl_mult
                        best_tp_mult = tp_mult
                        prob.optimal_sl_pct = test_sl
                        prob.optimal_tp_pct = test_tp
                        prob.optimal_sl_g_mult = sl_mult
                        prob.optimal_tp_g_mult = tp_mult
                        prob.optimal_rr = rr
                        prob.optimal_leverage = test_lev
                        prob.p_win = pw
                        prob.p_loss = 1 - pw
                        prob.ev_pct = round(ev, 2)
                        prob.sim_wins = sw
                        prob.sim_losses = sl
                        prob.sim_timeouts = st

        # Optimal bulunamadiysa default degerler
        if prob.optimal_sl_pct == 0:
            prob.optimal_sl_pct = sl_pct
            prob.optimal_tp_pct = tp_pct
            prob.optimal_sl_g_mult = sl_pct / G if G > 0 else 1.5
            prob.optimal_tp_g_mult = tp_pct / G if G > 0 else 2.5
            prob.optimal_rr = tp_pct / sl_pct if sl_pct > 0 else 0
            prob.optimal_leverage = leverage

        self._log.debug(
            f"[EV] dir={direction} G={G:.3f}% "
            f"SL={prob.optimal_sl_pct:.3f}% ({prob.optimal_sl_g_mult:.2f}xG) "
            f"TP={prob.optimal_tp_pct:.3f}% ({prob.optimal_tp_g_mult:.1f}xG) "
            f"R:R={prob.optimal_rr:.2f} P(w)={prob.p_win:.0%} "
            f"W/L/TO={prob.sim_wins}/{prob.sim_losses}/{prob.sim_timeouts} "
            f"Lev={prob.optimal_leverage}x EV={prob.ev_pct:+.1f}%"
        )

        return prob

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # HARD FİLTRELER — Section 1.2
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def check_hard_filters(self, result: SystemIScanResult,
                           market_ctx: dict) -> tuple:
        """Hard filtreler uygula. Herhangi biri başarısız → eleme.

        Args:
            result: Doldurulmuş scan sonucu.
            market_ctx: {funding_rate, spread_pct, depth_usd, volume_ratio,
                         wall_imbalance, ...}

        Returns:
            (eligible: bool, reject_reason: str)
        """
        if not market_ctx:
            market_ctx = {}

        # 1. Funding Rate
        fr_max = self._cfg_filter("funding_rate_max", 0.001)  # 0.1%
        fr = market_ctx.get("funding_rate", 0.0)
        result.funding_rate = fr
        if result.direction == "LONG" and fr > fr_max:
            return False, f"funding_rate_high_long ({fr*100:.3f}%)"
        if result.direction == "SHORT" and fr < -fr_max:
            return False, f"funding_rate_high_short ({fr*100:.3f}%)"

        # 2. Spread
        max_spread = self._cfg_filter("max_spread_pct", 0.05)
        spread = market_ctx.get("spread_pct", 0.0)
        result.spread_pct = spread
        if spread > max_spread:
            return False, f"spread_too_high ({spread:.3f}%)"

        # 3. Thin book (derinlik)
        min_depth = self._cfg_filter("min_depth_usd", 50000)
        depth = market_ctx.get("depth_usd", 0.0)
        if 0 < depth < min_depth:
            result.ob_thin_book = True
            return False, f"thin_order_book (${depth:.0f} < ${min_depth:.0f})"

        # 4. Volume
        vol_ratio = market_ctx.get("volume_ratio", 1.0)
        result.volume_ratio = vol_ratio
        vol_filter_on = self._cfg_filter("volume_filter_enabled", True)
        if vol_filter_on:
            min_vol_ratio = self._cfg_filter("min_volume_ratio", 0.5)
            if vol_ratio < min_vol_ratio:
                return False, f"low_volume ({vol_ratio:.2f} < {min_vol_ratio})"

        # 5. Min dalga sayısı
        min_waves = self._cfg_filter("min_wave_count", 3)
        if result.zoom.wave_count > 0 and result.zoom.wave_count < min_waves:
            return False, f"insufficient_waves ({result.zoom.wave_count} < {min_waves})"

        # 6. Wall blocking (orderbook imbalance)
        max_wall = self._cfg_filter("max_wall_imbalance", 0.3)
        wall_imbalance = market_ctx.get("wall_imbalance", 0.0)
        result.ob_imbalance = wall_imbalance
        if abs(wall_imbalance) > max_wall:
            # Yön ile çelişen wall
            if (result.direction == "LONG" and wall_imbalance < -max_wall) or \
               (result.direction == "SHORT" and wall_imbalance > max_wall):
                return False, f"wall_blocking ({wall_imbalance:.2f})"

        # 7. EV hard gate (opsiyonel — ev_hard_gate_enabled ile kontrol edilir)
        ev_gate = self._cfg("ev_hard_gate_enabled", False)
        if ev_gate and result.probability.sufficient:
            min_ev = self._cfg("ev_min", 0.0)
            if result.probability.ev_pct < min_ev:
                return False, f"ev_low ({result.probability.ev_pct:.1f}% < {min_ev}%)"

        # 8. BTC korelasyon (opsiyonel)
        btc_enabled = self._cfg("btc_correlation.enabled",
                                self._cfg("optional_features.btc_correlation_enabled", True))
        if btc_enabled:
            btc_action = self._cfg("btc_correlation.action", "reduce")
            if result.btc_conflict and btc_action == "block":
                return False, "btc_conflict_blocked"

        return True, ""

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # EXIT PARAMS — Section 8
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def set_exit_params(self, result: SystemIScanResult) -> None:
        """TP, trailing ve çıkış parametrelerini ayarla.

        Rejime göre:
            TRENDING: trailing (2.5G tetik, 0.5G callback), TP yok
            RANGING: sabit TP (BB middle veya 2.0G), trailing yok
        """
        G = result.G
        if G < 0.001:
            return

        regime = result.regime.regime
        effective_regime = regime
        if regime == "GRAY":
            if result.regime.gray_resolution in ("TREND_LIKE", "WEAK_TREND"):
                effective_regime = "TRENDING"
            else:
                effective_regime = "RANGING"

        if effective_regime == "TRENDING":
            tp_mode = self._cfg_tp("trend_tp_mode", "trailing_only")

            if tp_mode == "trailing_only":
                result.tp_pct = 0.0
                result.trailing_trigger_pct = G * self._cfg_tp("trailing_trigger_g_mult", 2.5)
                result.trailing_callback_pct = G * self._cfg_tp("trailing_callback_g_mult", 0.5)

            elif tp_mode == "single":
                single_mult = self._cfg_tp("single_tp_g_mult", 2.5)
                result.tp_pct = G * single_mult
                result.trailing_trigger_pct = 0.0
                result.trailing_callback_pct = 0.0

            elif tp_mode == "ladder":
                # Kademeli TP
                result.tp1_pct = G * self._cfg_tp("ladder_tp1_g_mult", 2.0)
                result.tp1_close_pct = self._cfg_tp("ladder_tp1_close_pct", 30)
                result.tp2_pct = G * self._cfg_tp("ladder_tp2_g_mult", 3.5)
                result.tp2_close_pct = self._cfg_tp("ladder_tp2_close_pct", 30)
                # Kalan %40 trailing ile
                result.trailing_trigger_pct = G * self._cfg_tp("trailing_trigger_g_mult", 2.5)
                result.trailing_callback_pct = G * self._cfg_tp("trailing_callback_g_mult", 0.5)
                result.tp_pct = 0.0  # ana TP yok, ladder handles it

            elif tp_mode == "ev_optimized":
                # EV'den en iyi TP hesapla
                if result.probability.sufficient and result.probability.ev_pct > 0:
                    # P(win) yüksekse daha uzak TP
                    ev_mult = 1.0 + min(result.probability.ev_pct / 20.0, 1.0)
                    result.tp_pct = G * 2.0 * ev_mult
                else:
                    result.tp_pct = G * 2.5  # fallback
                result.trailing_trigger_pct = 0.0
                result.trailing_callback_pct = 0.0

        else:
            # RANGING: sabit TP
            ranging_target = self._cfg_tp("ranging_tp_target", "bb_middle")
            ranging_g_mult = self._cfg_tp("ranging_tp_g_mult", 2.0)

            if ranging_target == "bb_middle" and result.bb_middle > 0 and result.price > 0:
                if result.direction == "LONG":
                    bb_tp = (result.bb_middle - result.price) / result.price * 100
                else:
                    bb_tp = (result.price - result.bb_middle) / result.price * 100

                g_tp = G * ranging_g_mult
                # BB middle pozitifse kullan, değilse G bazlı
                if bb_tp > 0:
                    result.tp_pct = min(bb_tp, g_tp)
                else:
                    result.tp_pct = g_tp
            else:
                result.tp_pct = G * ranging_g_mult

            result.trailing_trigger_pct = 0.0
            result.trailing_callback_pct = 0.0

        # ROI bazlı TP (opsiyonel, rejimden bağımsız)
        roi_tp_enabled = self._cfg_tp("roi_based_tp_enabled", False)
        if roi_tp_enabled and result.leverage > 0:
            roi_tp_pct = self._cfg_tp("roi_based_tp_pct", 50.0)
            result.roi_tp_pct = roi_tp_pct / result.leverage  # fiyat % olarak

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # FİNAL SKOR — Weighted composite
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def compute_final_score(self, result: SystemIScanResult) -> float:
        """Ağırlıklı composite skor hesapla (0-100).

        Bileşenler:
            direction_strength (0.30): TF hizalama + oy gücü
            ev_quality (0.25): EV normalize
            regime_clarity (0.20): ER gray sınırlarından uzaklık
            market_context (0.15): funding avantaj + volume
            wave_quality (0.10): dalga sayısı + tutarlılık
        """
        w_dir = self._cfg_score("direction_strength", 0.30)
        w_ev = self._cfg_score("ev_quality", 0.25)
        w_regime = self._cfg_score("regime_clarity", 0.20)
        w_market = self._cfg_score("market_context", 0.15)
        w_wave = self._cfg_score("wave_quality", 0.10)

        # 1. Direction strength (0-100)
        dir_r = result.direction_result
        total_tfs = max(1, len(dir_r.votes))
        alignment_ratio = dir_r.aligned_count / total_tfs
        # Oy gücü ortalaması
        avg_vote_strength = 0.0
        if dir_r.votes:
            avg_vote_strength = sum(abs(v.score) for v in dir_r.votes) / len(dir_r.votes)
        dir_score = alignment_ratio * avg_vote_strength * 100.0
        dir_score = min(100.0, max(0.0, dir_score))

        # 2. EV quality (0-100)
        ev_score = 50.0  # nötr başlangıç
        if result.probability.sufficient:
            # EV tipik aralık: -20% ile +30% arası → 0-100 normalize
            ev_norm = (result.probability.ev_pct + 20.0) / 50.0 * 100.0
            ev_score = max(0.0, min(100.0, ev_norm))

        # 3. Regime clarity (0-100)
        # ER'nin gray zone sınırlarından (0.20 ve 0.35) uzaklığı
        er = result.regime.er
        er_low = self._cfg_regime("er_ranging", 0.20)
        er_high = self._cfg_regime("er_trending", 0.35)
        if er <= er_low:
            regime_score = min(100.0, (er_low - er) / er_low * 100.0 + 50.0)
        elif er >= er_high:
            regime_score = min(100.0, (er - er_high) / (1.0 - er_high) * 100.0 + 50.0)
        else:
            # Gray zone — daha düşük skor
            mid = (er_low + er_high) / 2.0
            dist = abs(er - mid) / (er_high - er_low) * 2.0
            regime_score = max(0.0, dist * 40.0)
        regime_score = min(100.0, max(0.0, regime_score))

        # 4. Market context (0-100)
        market_score = 50.0
        # Funding advantage
        fr = result.funding_rate
        if result.direction == "LONG" and fr < 0:
            market_score += min(abs(fr) * 10000, 20.0)
        elif result.direction == "SHORT" and fr > 0:
            market_score += min(fr * 10000, 20.0)
        elif result.direction == "LONG" and fr > 0.0005:
            market_score -= min(fr * 5000, 15.0)
        elif result.direction == "SHORT" and fr < -0.0005:
            market_score -= min(abs(fr) * 5000, 15.0)
        # Volume health
        if result.volume_ratio > 2.0:
            market_score += 15.0
        elif result.volume_ratio > 1.5:
            market_score += 8.0
        elif result.volume_ratio < 0.7:
            market_score -= 10.0
        market_score = min(100.0, max(0.0, market_score))

        # 5. Wave quality (0-100)
        wc = result.zoom.wave_count
        cv = result.zoom.cv
        wave_score = min(1.0, wc / 10.0) * (1.0 - min(1.0, cv / 2.0)) * 100.0
        wave_score = min(100.0, max(0.0, wave_score))

        # Weighted sum
        final = (
            w_dir * dir_score +
            w_ev * ev_score +
            w_regime * regime_score +
            w_market * market_score +
            w_wave * wave_score
        )

        # EV multiplier: sadece kaldıraç çarpanı olarak sakla (skor zaten EV ağırlığı içeriyor)
        if result.probability.sufficient:
            if result.probability.ev_pct > 0:
                result.ev_multiplier = 1.0 + min(result.probability.ev_pct / 100.0, 0.3)
            elif result.probability.ev_pct < -10.0:
                result.ev_multiplier = max(0.7, 1.0 + result.probability.ev_pct / 100.0)
            else:
                result.ev_multiplier = 1.0
            # Not: final skora uygulanmaz (çift sayma önlenir), kaldıraç çarpanı olarak kullanılır

        final = max(0.0, min(100.0, final))
        return round(final, 1)

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # PRE-FİLTRE — Faz 1
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def prefilter_symbols(self, symbols_data: list) -> list:
        """Faz 1: Hızlı pre-filtre — 5m verisi + hard filtreler.

        Args:
            symbols_data: [{symbol, klines_5m, market_ctx, volume_24h}, ...]

        Returns:
            Sıralanmış pre-qualified SystemIScanResult listesi.
        """
        results = []

        for item in symbols_data:
            symbol = item.get("symbol", "")
            klines_5m = item.get("klines_5m", [])
            market_ctx = item.get("market_ctx", {})
            volume_24h = item.get("volume_24h", 0.0)

            r = SystemIScanResult(symbol=symbol, volume_24h=volume_24h)

            if not klines_5m or len(klines_5m) < 30:
                r.reject_reason = "insufficient_5m_data"
                results.append(r)
                continue

            closes = np.array([float(k[4]) for k in klines_5m])
            highs = np.array([float(k[2]) for k in klines_5m])
            lows = np.array([float(k[3]) for k in klines_5m])
            r.price = closes[-1]

            # 3 indikatör hızlı kontrol (EMA, MACD, RSI)
            vote = self._vote_single_tf(klines_5m, "5m")

            if vote.score >= 0.33:
                r.direction = "LONG"
            elif vote.score <= -0.33:
                r.direction = "SHORT"
            else:
                r.reject_reason = "direction_unclear_prefilter"
                results.append(r)
                continue

            # ATR hesapla
            if len(closes) >= 15:
                atr = self._atr(highs, lows, closes, 14)
                r.atr = atr
                if r.price > 0:
                    r.atr_pct = (atr / r.price) * 100

            # RSI
            r.rsi = vote.rsi_value

            # Hard filtreler
            eligible, reason = self.check_hard_filters(r, market_ctx)
            r.eligible = eligible
            r.reject_reason = reason

            results.append(r)

        # Eligible olanları öne, volume sıralı
        results.sort(key=lambda x: (not x.eligible, -x.volume_24h))
        return results

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # DERİN ANALİZ — Faz 2
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def deep_analyze(self, symbol: str, klines_by_tf: dict,
                     market_ctx: dict) -> SystemIScanResult:
        """Faz 2: Tek coin için tam pipeline.

        Args:
            symbol: Coin sembolü.
            klines_by_tf: {tf_str: [kline_list, ...]}
            market_ctx: Market context dict.

        Returns:
            Doldurulmuş SystemIScanResult.
        """
        result = SystemIScanResult(symbol=symbol)

        # 1. Zoom diyafram
        result.zoom = self.compute_zoom(symbol, klines_by_tf)
        zoom = result.zoom

        if zoom.optimal_G < 0.01:
            result.reject_reason = "zoom_no_valid_g"
            return result

        # G max sınırı: çok yüksek G = çok geniş SL = anlamsız trade
        # G > %20 → SL > %30 → düşük kaldıraçta bile likidasyon riski
        max_g = self._cfg("max_g_pct", 20.0)
        if zoom.optimal_G > max_g:
            result.reject_reason = f"g_too_high ({zoom.optimal_G:.1f}% > {max_g}%)"
            return result

        # 2. Yön belirleme
        result.direction_result = self.compute_direction(klines_by_tf, zoom)
        result.direction = result.direction_result.direction

        if result.direction == "SKIP":
            result.reject_reason = "direction_unclear"
            return result

        # 3. Rejim tespiti
        klines_yon = klines_by_tf.get(zoom.yon_tf, [])
        klines_teyit = klines_by_tf.get(zoom.teyit_tf, [])
        result.regime = self.compute_regime(
            symbol, klines_yon, klines_teyit, result.direction_result
        )

        # GRAY NO_TRADE kontrolü
        if result.regime.regime == "GRAY" and result.regime.gray_resolution == "NO_TRADE":
            result.reject_reason = "gray_zone_no_trade"
            return result

        # Pool belirleme
        if result.regime.regime == "TRENDING":
            result.pool = "TREND"
        elif result.regime.regime == "RANGING":
            result.pool = "RANGING"
        else:
            if result.regime.gray_resolution in ("TREND_LIKE", "WEAK_TREND"):
                result.pool = "TREND"
            else:
                result.pool = "RANGING"

        # 4. G ve I'yı ayarla
        result.G = zoom.optimal_G
        result.I = zoom.optimal_I

        # BB hesapla (RANGING TP için)
        klines_yon_list = klines_by_tf.get(zoom.yon_tf, [])
        if klines_yon_list and len(klines_yon_list) >= 20:
            closes_yon = np.array([float(k[4]) for k in klines_yon_list])
            result.price = closes_yon[-1]
            bb_u, bb_m, bb_l = self._bollinger(closes_yon, 20, 2.0)
            result.bb_upper = bb_u
            result.bb_middle = bb_m
            result.bb_lower = bb_l

            # ATR
            highs_yon = np.array([float(k[2]) for k in klines_yon_list])
            lows_yon = np.array([float(k[3]) for k in klines_yon_list])
            result.atr = self._atr(highs_yon, lows_yon, closes_yon, 14)
            if result.price > 0:
                result.atr_pct = (result.atr / result.price) * 100

        # 5. Kaldıraç
        result.leverage_calc = self.compute_leverage(
            zoom.optimal_G, result.regime, result.direction_result,
            cv=zoom.cv, btc_conflict=result.btc_conflict
        )
        result.leverage = result.leverage_calc.max_leverage
        result.sl_pct = result.leverage_calc.sl_pct

        if result.leverage < 1:
            result.reject_reason = "leverage_zero"
            return result

        # User max kaldıraç
        user_max = self._config.get("strategy.max_leverage", 20)
        if result.leverage > user_max:
            result.leverage = user_max

        # 6. Default exit parametreleri (TP/trailing G'den turetilir)
        self.set_exit_params(result)

        # 7. P(win)/EV + Optimal SL/TP bulma
        # Coklu dalga simulasyonu ile dalga verisinden en iyi SL/TP'yi bul
        ev_tp = result.tp_pct if result.tp_pct > 0 else result.trailing_trigger_pct
        if ev_tp <= 0:
            ev_tp = result.G * 2.5
        result.probability = self.compute_probability(
            result.direction, result.G, result.sl_pct,
            ev_tp, result.leverage, klines_yon
        )

        # 8. Optimal SL/TP'yi sonuca yansit
        # EV pozitif optimal bulunduysa VE yeterli trade sayısı varsa güncelle
        prob = result.probability
        min_trades_for_override = 5
        total_sim_trades = prob.sim_wins + prob.sim_losses
        if (prob.sufficient and prob.ev_pct > 0 and prob.optimal_sl_pct > 0
                and total_sim_trades >= min_trades_for_override):
            result.sl_pct = prob.optimal_sl_pct
            result.leverage = min(prob.optimal_leverage,
                                  self._config.get("strategy.max_leverage", 20))
            # TP guncelle
            if prob.optimal_tp_pct > 0:
                result.tp_pct = prob.optimal_tp_pct
                # Trailing trigger da optimal TP'ye gore ayarla
                if result.trailing_trigger_pct > 0:
                    result.trailing_trigger_pct = prob.optimal_tp_pct
                    result.trailing_callback_pct = result.G * self._cfg_tp(
                        "trailing_callback_g_mult", 0.5)

            self._log.info(
                f"[EV-Opt] {symbol}: SL={result.sl_pct:.3f}% "
                f"({prob.optimal_sl_g_mult:.2f}xG) "
                f"TP={result.tp_pct:.3f}% ({prob.optimal_tp_g_mult:.1f}xG) "
                f"Lev={result.leverage}x R:R={prob.optimal_rr:.2f} "
                f"P(w)={prob.p_win:.0%} EV={prob.ev_pct:+.1f}%"
            )

        # 9. Giris tipi
        self._set_entry_type(result)

        # 9. Hard filtreler (zoom sonrası tekrar kontrol)
        eligible, reason = self.check_hard_filters(result, market_ctx)
        result.eligible = eligible
        if reason:
            result.reject_reason = reason

        # 10. GUI convenience fields doldur
        self._populate_gui_fields(result)

        # 11. Final skor
        if result.eligible:
            result.score = self.compute_final_score(result)

        self._log.debug(
            f"[Deep] {symbol}: dir={result.direction} regime={result.regime.regime} "
            f"pool={result.pool} G={result.G:.3f}% lev={result.leverage}x "
            f"SL={result.sl_pct:.3f}% TP={result.tp_pct:.3f}% "
            f"EV={result.probability.ev_pct:.1f}% score={result.score:.1f} "
            f"eligible={result.eligible} reason={result.reject_reason}"
        )

        return result

    def _populate_gui_fields(self, result: SystemIScanResult) -> None:
        """Alt nesnelerdeki değerleri top-level GUI alanlarına kopyala."""
        result.strength = result.direction_result.strength
        result.regime_zone = result.regime.regime
        result.er = result.regime.er
        result.hurst = result.regime.hurst
        result.zoom_tf = result.zoom.yon_tf
        if result.probability.sufficient:
            result.p_win = result.probability.p_win
            result.ev_pct = result.probability.ev_pct

        # BB proximity hesapla (RANGING pool için)
        if result.price > 0 and result.bb_upper > 0 and result.bb_lower > 0:
            bb_width = result.bb_upper - result.bb_lower
            if bb_width > 0:
                if result.direction == "LONG":
                    dist = result.price - result.bb_lower
                elif result.direction == "SHORT":
                    dist = result.bb_upper - result.price
                else:
                    dist = min(result.price - result.bb_lower,
                               result.bb_upper - result.price)
                # 100 = tam bant kenarında, 0 = ortada
                result.bb_proximity = max(0, min(100, (1.0 - dist / bb_width) * 100))

    def _set_entry_type(self, result: SystemIScanResult) -> None:
        """Giris tipi ve fiyatini ayarla.

        Giris modlari:
          "market"     -> sinyal gelince hemen gir
          "limit_wave" -> dalga bazli dip/tepe tahmini ile pazarlikli giris
          "limit_g"    -> G×ratio kadar asagi/yukari limit emir
          "limit"      -> ATR/BB bazli limit (eski davranis)

        limit_wave modu (yeni):
          1. Yon TF'deki son swing noktasini bul (SH veya SL)
          2. LONG: son tepe'den G kadar asagi = beklenen dip → limit emir
             SHORT: son dip'ten G kadar yukari = beklenen tepe → limit emir
          3. entry_g_ratio ile ayarlanir (0.7 = G'nin %70'i kadar cekilme bekle)
          4. Alt TF'de (giris_tf) RSI + volume teyidi kontrol edilir
        """
        if result.price <= 0 or result.G <= 0:
            result.entry_type = "market"
            result.entry_price = result.price
            return

        entry_mode = self._cfg_entry("entry_mode", "limit_wave")

        if entry_mode == "market":
            result.entry_type = "market"
            result.entry_price = result.price
            result.entry_mode_detail = "market"
            return

        if entry_mode == "limit_wave":
            self._set_entry_wave(result)
            return

        if entry_mode == "limit_g":
            # G×ratio kadar offset ile limit
            ratio = self._cfg_entry("entry_g_ratio", 0.7)
            offset_pct = result.G * ratio
            result.entry_type = "limit"
            result.entry_mode_detail = f"limit_g_{ratio}"
            if result.direction == "LONG":
                result.entry_price = result.price * (1 - offset_pct / 100)
            else:
                result.entry_price = result.price * (1 + offset_pct / 100)
            result.entry_offset_pct = offset_pct
            return

        # Fallback: ATR/BB bazli limit (eski davranis)
        result.entry_type = "limit"
        result.entry_mode_detail = "limit_atr"
        if result.atr > 0:
            offset = self._cfg_entry("limit_atr_offset", 0.1)
            result.entry_offset_pct = result.atr * offset / result.price * 100
            if result.direction == "LONG":
                result.entry_price = result.price - result.atr * offset
            else:
                result.entry_price = result.price + result.atr * offset
        else:
            result.entry_price = result.price

    def _set_entry_wave(self, result: SystemIScanResult) -> None:
        """Dalga bazli dip/tepe tahmini ile pazarlikli giris.

        Mantik:
          LONG: fiyat tepe'den G kadar dusecek → beklenen dip'e limit emir
          SHORT: fiyat dip'ten G kadar cikacak → beklenen tepe'ye limit emir

          Son swing noktasi biliniyorsa ondan hesapla,
          bilinmiyorsa guncel fiyattan G×ratio offset ile limit koy.
        """
        zoom = result.zoom
        G = result.G
        price = result.price
        entry_g_ratio = self._cfg_entry("entry_g_ratio", 0.7)

        result.entry_type = "limit_wave"

        # Son swing noktasini bul (zoom.all_tfs'ten)
        last_swing = self._find_last_swing(result, zoom)

        if last_swing:
            result.last_swing_price = last_swing["price"]
            result.last_swing_type = last_swing["type"]

            if result.direction == "LONG":
                if last_swing["type"] == "SH":
                    # Son tepe'den G kadar dusus bekleniyor
                    expected_dip = last_swing["price"] * (1 - G * entry_g_ratio / 100)
                    result.entry_price = expected_dip
                    result.entry_mode_detail = "wave_dip_from_peak"
                elif last_swing["type"] == "SL":
                    # Son dip noktasindayiz — hafif yukari buffer ile gir
                    buffer = G * 0.1  # G'nin %10'u kadar yukari
                    result.entry_price = last_swing["price"] * (1 + buffer / 100)
                    result.entry_mode_detail = "wave_near_dip"
                else:
                    result.entry_price = price * (1 - G * entry_g_ratio / 100)
                    result.entry_mode_detail = "wave_g_offset"
            else:  # SHORT
                if last_swing["type"] == "SL":
                    # Son dip'ten G kadar yukselis bekleniyor
                    expected_peak = last_swing["price"] * (1 + G * entry_g_ratio / 100)
                    result.entry_price = expected_peak
                    result.entry_mode_detail = "wave_peak_from_dip"
                elif last_swing["type"] == "SH":
                    # Son tepe noktasindayiz — hafif asagi buffer ile gir
                    buffer = G * 0.1
                    result.entry_price = last_swing["price"] * (1 - buffer / 100)
                    result.entry_mode_detail = "wave_near_peak"
                else:
                    result.entry_price = price * (1 + G * entry_g_ratio / 100)
                    result.entry_mode_detail = "wave_g_offset"
        else:
            # Swing bulunamadi — G×ratio offset ile limit
            if result.direction == "LONG":
                result.entry_price = price * (1 - G * entry_g_ratio / 100)
            else:
                result.entry_price = price * (1 + G * entry_g_ratio / 100)
            result.entry_mode_detail = "wave_g_fallback"

        result.entry_offset_pct = abs(price - result.entry_price) / price * 100

        # Giris fiyati mantik kontrolu: mevcut fiyattan cok uzak olmamali
        max_offset = G * 2.0  # max 2×G uzaklik
        if result.entry_offset_pct > max_offset:
            # Cok uzak, G×ratio'ya dusur
            if result.direction == "LONG":
                result.entry_price = price * (1 - G * entry_g_ratio / 100)
            else:
                result.entry_price = price * (1 + G * entry_g_ratio / 100)
            result.entry_offset_pct = G * entry_g_ratio
            result.entry_mode_detail += "_capped"

        self._log.debug(
            f"[Entry] {result.symbol}: mode={result.entry_mode_detail} "
            f"price={price:.2f} entry={result.entry_price:.2f} "
            f"offset={result.entry_offset_pct:.3f}% G={G:.3f}%"
        )

    def _find_last_swing(self, result: SystemIScanResult,
                         zoom: ZoomResultI) -> dict:
        """Zoom verisinden son teyitli swing noktasini bul.

        compute_zoom'da bulunan son swing noktasini kullanir.

        Returns:
            {"price": float, "type": "SH"/"SL"} or None
        """
        if zoom.last_swing_price > 0 and zoom.last_swing_type:
            return {
                "price": zoom.last_swing_price,
                "type": zoom.last_swing_type,
            }
        return None

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # BATCH SCAN
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def scan_all(self, top_symbols: list, klines_map: dict,
                 market_ctx: dict) -> list:
        """İki aşamalı tarama: pre-filtre → derin analiz.

        Args:
            top_symbols: [{symbol, klines_5m, market_ctx, volume_24h}, ...]
            klines_map: {symbol: {tf: klines_list, ...}} — tüm TF verileri
            market_ctx: {symbol: {funding_rate, ...}}

        Returns:
            Final skora göre sıralanmış SystemIScanResult listesi.
        """
        # Faz 1: Pre-filtre
        prefiltered = self.prefilter_symbols(top_symbols)
        eligible_symbols = [r.symbol for r in prefiltered if r.eligible]

        top_n = self._cfg_scanner("deep_analysis_top_n", 15)
        eligible_symbols = eligible_symbols[:top_n]

        # Faz 2: Derin analiz
        results = []
        for sym in eligible_symbols:
            sym_klines = klines_map.get(sym, {})
            sym_ctx = market_ctx.get(sym, {})
            if not sym_klines:
                continue

            r = self.deep_analyze(sym, sym_klines, sym_ctx)
            results.append(r)

        # Eligible + skora göre sırala
        results.sort(key=lambda x: (not x.eligible, -abs(x.score)))

        self._log.info(
            f"[Scan] Faz1: {len(prefiltered)} sembol, {len(eligible_symbols)} eligible → "
            f"Faz2: {len(results)} sonuç, "
            f"{sum(1 for r in results if r.eligible)} trade-ready"
        )

        return results

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # OPTİMİZER — Section 11
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def submit_optimization(self, symbol: str, direction: str,
                            klines_5m: list) -> None:
        """Async optimizer submission. Non-blocking.

        Args:
            symbol: Coin sembolü.
            direction: LONG / SHORT.
            klines_5m: 5m klines listesi.
        """
        with self._lock:
            if symbol in self._opt_futures:
                future = self._opt_futures[symbol]
                if not future.done():
                    return  # zaten çalışıyor

        future = self._opt_executor.submit(
            self._optimize_coin, symbol, direction, klines_5m
        )
        with self._lock:
            self._opt_futures[symbol] = future

    def check_optimization(self, symbol: str) -> dict | None:
        """Check if async optimization is done. Non-blocking.

        Returns:
            OptResult dict or None if still running/not submitted.
        """
        with self._lock:
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
                self._log.error(f"[Opt] Optimization failed for {symbol}: {e}")
                del self._opt_futures[symbol]
                return None

    def get_opt_cached(self, symbol: str) -> dict | None:
        """Get cached optimization if valid and fresh.

        Returns:
            CoinOptCache or None.
        """
        with self._lock:
            cache = self._opt_cache.get(symbol)
            if not cache or not cache.valid:
                return None
            ttl = self._cfg_opt("cache_hours", 4) * 3600
            if time.time() - cache.timestamp > ttl:
                cache.valid = False
                return None
            return cache

    def _optimize_coin(self, symbol: str, direction: str,
                       klines_5m: list) -> CoinOptCache:
        """Combo matrix backtest — 240 kombinasyon.

        Scoring: 0.35×ROI_norm + 0.25×WR - 0.20×DD_penalty
                 - 0.15×LIQ_penalty + 0.05×TC_bonus
        """
        leverages = [25, 50, 75, 100, 125, 150]
        tps = [0.3, 0.5, 0.7, 1.0, 1.5, 2.0, 2.5, 3.0]
        sls = [("0.5", 0.5), ("0.7", 0.7),
               ("1.0", 1.0), ("1.5", 1.5), ("2.0", 2.0)]

        if not klines_5m or len(klines_5m) < 100:
            return CoinOptCache(symbol=symbol, valid=False)

        closes = np.array([float(k[4]) for k in klines_5m])
        highs = np.array([float(k[2]) for k in klines_5m])
        lows = np.array([float(k[3]) for k in klines_5m])

        all_results = []

        for lev in leverages:
            for tp in tps:
                for sl_mode, sl_val in sls:
                    combo = OptCombo(leverage=lev, tp_pct=tp,
                                     sl_pct=sl_val, sl_mode=sl_mode)
                    opt_r = self._backtest_combo(
                        combo, direction, closes, highs, lows
                    )
                    all_results.append(opt_r)

        # Sonuçları skora göre sırala
        all_results.sort(key=lambda x: -x.score)

        cache = CoinOptCache(
            symbol=symbol,
            direction=direction,
            timestamp=time.time(),
            valid=True,
        )
        if all_results:
            cache.best = all_results[0]
            cache.top5 = all_results[:5]

        with self._lock:
            self._opt_cache[symbol] = cache

        self._log.debug(
            f"[Opt] {symbol}: best lev={cache.best.combo.leverage}x "
            f"tp={cache.best.combo.tp_pct}% sl={cache.best.combo.sl_pct}% "
            f"score={cache.best.score:.2f} WR={cache.best.win_rate:.0%} "
            f"ROI={cache.best.total_roi:.1f}%"
        )

        return cache

    def _backtest_combo(self, combo: OptCombo, direction: str,
                        closes: np.ndarray, highs: np.ndarray,
                        lows: np.ndarray) -> OptResult:
        """Tek combo'yu backtest et.

        Basit bar-by-bar simülasyon: giriş→TP/SL/liq kontrol→çıkış.
        """
        result = OptResult(combo=combo)
        leverage = combo.leverage
        tp_pct = combo.tp_pct
        sl_pct = combo.sl_pct
        fee_per_trade = 0.04  # tek taraf %0.04

        # Likidasyon mesafesi (%)
        liq_pct = (1.0 / leverage) * 100 * 0.7  # %70 safety

        trades = []
        i = 0
        step = 5  # her 5 bar'da yeni trade dene

        while i < len(closes) - step:
            entry_price = closes[i]
            if entry_price <= 0:
                i += step
                continue

            trade_roi = 0.0
            bars_held = 0
            exited = False

            for j in range(i + 1, min(i + 200, len(closes))):
                bars_held += 1

                if direction == "LONG":
                    high_pct = (highs[j] - entry_price) / entry_price * 100
                    low_pct = (entry_price - lows[j]) / entry_price * 100
                else:
                    high_pct = (entry_price - lows[j]) / entry_price * 100
                    low_pct = (highs[j] - entry_price) / entry_price * 100

                # Likidasyon kontrolü
                if low_pct >= liq_pct:
                    trade_roi = -liq_pct * leverage - fee_per_trade * 2
                    result.liq_rate += 1
                    exited = True
                    break

                # SL kontrolü
                if sl_pct > 0 and low_pct >= sl_pct:
                    trade_roi = -sl_pct * leverage - fee_per_trade * 2
                    exited = True
                    break

                # TP kontrolü
                if high_pct >= tp_pct:
                    trade_roi = tp_pct * leverage - fee_per_trade * 2
                    exited = True
                    break

            if not exited:
                # Max bars, close at current
                final_pct = (closes[min(i + 200, len(closes) - 1)] - entry_price) / entry_price * 100
                if direction == "SHORT":
                    final_pct = -final_pct
                trade_roi = final_pct * leverage - fee_per_trade * 2

            trades.append((trade_roi, bars_held))
            i += step + bars_held

        if not trades:
            return result

        rois = [t[0] for t in trades]
        bars = [t[1] for t in trades]
        result.trade_count = len(trades)
        result.total_roi = sum(rois)
        result.win_rate = sum(1 for r in rois if r > 0) / len(rois)
        result.avg_hold_bars = float(np.mean(bars))

        # Max drawdown
        cumulative = 0.0
        peak = 0.0
        max_dd = 0.0
        for r in rois:
            cumulative += r
            if cumulative > peak:
                peak = cumulative
            dd = peak - cumulative
            if dd > max_dd:
                max_dd = dd
        result.max_drawdown = max_dd

        # Liq rate
        if result.trade_count > 0:
            result.liq_rate = result.liq_rate / result.trade_count

        # Scoring
        max_roi = max(abs(r) for r in rois) if rois else 1.0
        roi_norm = result.total_roi / max(max_roi * result.trade_count, 1.0)
        dd_penalty = min(1.0, max_dd / 100.0)
        liq_penalty = result.liq_rate
        tc_bonus = min(1.0, result.trade_count / 50.0)

        result.score = round(
            0.35 * roi_norm +
            0.25 * result.win_rate -
            0.20 * dd_penalty -
            0.15 * liq_penalty +
            0.05 * tc_bonus,
            4
        )

        return result

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # POZİSYON BOYUTLANDIRMA — Section 12
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def calculate_position_size(self, balance: float) -> float:
        """Dinamik pozisyon boyutu hesapla.

        Küçük bakiyelerde az bölme, büyük bakiyelerde çok bölme.

        Args:
            balance: Mevcut USDT bakiyesi.

        Returns:
            Position size in USDT.
        """
        min_pos = self._cfg_pos("min_position_usd", 1.0)
        min_div = self._cfg_pos("min_divider", 4)
        max_div = self._cfg_pos("max_divider", 12)

        if balance <= 0:
            return min_pos

        # Dinamik divider
        divider = max(min_div, min(balance / min_pos, max_div))
        position_size = balance / divider

        # Min kontrolü
        position_size = max(min_pos, position_size)

        return round(position_size, 2)

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # HELPER FONKSİYONLAR
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def _round_tf(self, minutes: float, rounding: str = "up") -> str:
        """Dakikayı en yakın geçerli Binance TF'ye çevir.

        Args:
            minutes: Hedef dakika.
            rounding: "up" = bir üst TF, "down" = bir alt TF, "nearest" = en yakın.

        Returns:
            Binance TF string (e.g. "15m", "1h").
        """
        sorted_tfs = sorted(TF_MINUTES.items(), key=lambda x: x[1])

        if rounding == "up":
            for tf, m in sorted_tfs:
                if m >= minutes:
                    return tf
            return sorted_tfs[-1][0]  # en büyük TF

        elif rounding == "down":
            prev_tf = sorted_tfs[0][0]
            for tf, m in sorted_tfs:
                if m > minutes:
                    return prev_tf
                prev_tf = tf
            return prev_tf

        else:  # nearest
            best_tf = sorted_tfs[0][0]
            best_diff = float("inf")
            for tf, m in sorted_tfs:
                diff = abs(m - minutes)
                if diff < best_diff:
                    best_diff = diff
                    best_tf = tf
            return best_tf

    def _compute_indicators(self, klines: list) -> dict:
        """Kline listesinden temel indikatörleri hesapla.

        Returns:
            {ema_fast, ema_slow, macd_hist, rsi, bb_upper, bb_middle,
             bb_lower, atr, price}
        """
        result = {}
        if not klines or len(klines) < 26:
            return result

        closes = np.array([float(k[4]) for k in klines])
        highs = np.array([float(k[2]) for k in klines])
        lows = np.array([float(k[3]) for k in klines])

        result["price"] = closes[-1]

        # EMA 9/21
        result["ema_fast"] = self._ema(closes, 9)
        result["ema_slow"] = self._ema(closes, 21)

        # MACD 8/17/9
        _, _, hist = self._macd(closes, 8, 17, 9)
        result["macd_hist"] = hist

        # RSI 14
        result["rsi"] = self._rsi(closes, 14)

        # BB 20/2
        if len(closes) >= 20:
            bb_u, bb_m, bb_l = self._bollinger(closes, 20, 2.0)
            result["bb_upper"] = bb_u
            result["bb_middle"] = bb_m
            result["bb_lower"] = bb_l

        # ATR 14
        if len(closes) >= 15:
            result["atr"] = self._atr(highs, lows, closes, 14)

        return result

    def cleanup_caches(self, active_symbols: set = None) -> None:
        """Artık taranmayan sembollerin cache'lerini temizle."""
        with self._lock:
            if active_symbols is not None:
                stale = [k for k in self._zoom_cache if k not in active_symbols]
                for k in stale:
                    del self._zoom_cache[k]

                stale = [k for k in self._regime_cache if k not in active_symbols]
                for k in stale:
                    del self._regime_cache[k]
                    self._regime_history.pop(k, None)

                stale = [k for k in self._opt_cache if k not in active_symbols]
                for k in stale:
                    del self._opt_cache[k]

            # Tamamlanmış ama okunmamış futures temizle
            done = [k for k, f in self._opt_futures.items() if f.done()]
            for k in done:
                try:
                    self._opt_futures[k].result()
                except Exception:
                    pass
                del self._opt_futures[k]

    # ────── Indicator Helpers ──────

    @staticmethod
    def _ema(data: np.ndarray, period: int) -> float:
        """Exponential Moving Average — son değer."""
        if len(data) < period:
            return 0.0
        multiplier = 2.0 / (period + 1)
        ema = float(data[0])
        for i in range(1, len(data)):
            ema = (data[i] - ema) * multiplier + ema
        return ema

    @staticmethod
    def _macd(closes: np.ndarray, fast: int = 8, slow: int = 17,
              signal: int = 9) -> tuple:
        """MACD hesapla. Returns (macd_line, signal_line, histogram) — son değerler."""
        if len(closes) < slow + signal:
            return 0.0, 0.0, 0.0

        # Fast EMA
        mult_f = 2.0 / (fast + 1)
        ema_fast = float(closes[0])
        fast_series = [ema_fast]
        for i in range(1, len(closes)):
            ema_fast = (closes[i] - ema_fast) * mult_f + ema_fast
            fast_series.append(ema_fast)

        # Slow EMA
        mult_s = 2.0 / (slow + 1)
        ema_slow = float(closes[0])
        slow_series = [ema_slow]
        for i in range(1, len(closes)):
            ema_slow = (closes[i] - ema_slow) * mult_s + ema_slow
            slow_series.append(ema_slow)

        # MACD line
        macd_series = [f - s for f, s in zip(fast_series, slow_series)]

        # Signal line (EMA of MACD)
        mult_sig = 2.0 / (signal + 1)
        sig = macd_series[0]
        for i in range(1, len(macd_series)):
            sig = (macd_series[i] - sig) * mult_sig + sig

        macd_val = macd_series[-1]
        histogram = macd_val - sig

        return macd_val, sig, histogram

    @staticmethod
    def _rsi(closes: np.ndarray, period: int = 14) -> float:
        """RSI hesapla — son değer."""
        if len(closes) < period + 1:
            return 50.0

        deltas = np.diff(closes)
        gains = np.where(deltas > 0, deltas, 0.0)
        losses = np.where(deltas < 0, -deltas, 0.0)

        # İlk ortalama
        avg_gain = np.mean(gains[:period])
        avg_loss = np.mean(losses[:period])

        # Smoothed
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
        """ATR hesapla — son değer."""
        if len(closes) < period + 1:
            return 0.0

        tr_list = []
        for i in range(1, len(closes)):
            hl = highs[i] - lows[i]
            hc = abs(highs[i] - closes[i - 1])
            lc = abs(lows[i] - closes[i - 1])
            tr_list.append(max(hl, hc, lc))

        if len(tr_list) < period:
            return 0.0

        # SMA başlangıç
        atr = np.mean(tr_list[:period])
        # Smoothed
        for i in range(period, len(tr_list)):
            atr = (atr * (period - 1) + tr_list[i]) / period

        return float(atr)

    @staticmethod
    def _bollinger(closes: np.ndarray, period: int = 20,
                   std_mult: float = 2.0) -> tuple:
        """Bollinger Bands: (upper, middle, lower)."""
        if len(closes) < period:
            return 0.0, 0.0, 0.0
        sma = float(np.mean(closes[-period:]))
        std = float(np.std(closes[-period:], ddof=1))
        return sma + std_mult * std, sma, sma - std_mult * std
