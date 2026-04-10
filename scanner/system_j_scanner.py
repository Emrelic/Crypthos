"""System J Scanner — Maximum Leverage First Strategy.

3 turlu döngüsel tarama:
  Tur 1: Max kaldıraç → G eşiği → TF bul (en yüksek kaldıraç öncelikli)
  Tur 2: G-bazlı kaldıraç → her TF'de G'den max kaldıraç hesapla
  Tur 3: Zoom dirsek → System I tarzı verimlilik taraması (fallback)
  → Tur 1'e dön (döngüsel)

Temel prensipler:
  - G dalga boyu tüm risk hesaplarının temeli
  - Kaldıraç-odaklı: max kaldıraçtan başla, G'yi sağlayan TF'yi bul
  - %50+ P(win) ile en az 1:2.5 R:R → pozitif EV
  - Fee-aware tüm hesaplamalar
  - Sabit teyit TF eşlemeleri (1m→30m, 3m→1h, ...)
"""
import time
import threading
import numpy as np
from dataclasses import dataclass, field
from typing import Optional
from loguru import logger
from core.config_manager import ConfigManager
from indicators.indicator_engine import IndicatorEngine
from scanner.system_b_scanner import (
    detect_zigzag_swings,
    analyze_waves,
    compute_rolling_er,
    compute_hurst_exponent,
    SwingPoint,
    WaveAnalysis,
)
from analysis.elliott_wave import detect_elliott, ElliottPattern

# ─────────────────────────── Helpers ───────────────────────────

def _ema_value(closes: np.ndarray, period: int) -> float:
    """Son EMA değerini hesapla (numpy array → float)."""
    if len(closes) < period:
        return 0.0
    alpha = 2.0 / (period + 1)
    ema = float(closes[0])
    for i in range(1, len(closes)):
        ema = alpha * float(closes[i]) + (1.0 - alpha) * ema
    return ema


# ─────────────────────────── Constants ───────────────────────────

# TF merdiveni (Binance destekli, 10m yok → 15m'e atla)
TF_LADDER = ["1m", "3m", "5m", "15m", "30m", "1h"]

TF_MINUTES = {
    "1m": 1, "3m": 3, "5m": 5, "15m": 15, "30m": 30,
    "1h": 60, "2h": 120, "4h": 240, "6h": 360, "8h": 480,
    "12h": 720, "1d": 1440,
}

# Sabit teyit TF eşlemeleri
CONFIRM_TF_MAP = {
    "1m": "30m",
    "3m": "1h",
    "5m": "2h",
    "15m": "6h",
    "30m": "12h",
    "1h": "1d",
}

# Zoom diyafram için genişletilmiş TF listesi (Tur 3)
ZOOM_TF_LADDER = [
    ("1m", 1), ("3m", 3), ("5m", 5), ("15m", 15), ("30m", 30),
    ("1h", 60), ("2h", 120), ("4h", 240), ("6h", 360), ("8h", 480),
    ("12h", 720), ("1d", 1440),
]


# ─────────────────────────── Data Classes ───────────────────────────

@dataclass
class LeverageBracketInfo:
    """Binance kaldıraç bracket bilgisi."""
    max_leverage: int = 125
    maint_margin_rate: float = 0.004  # %0.4 varsayılan
    notional_floor: float = 0.0
    notional_cap: float = 0.0


@dataclass
class DirectionVoteJ:
    """Tek TF yön oyu."""
    timeframe: str = ""
    ema_vote: float = 0.0
    macd_vote: float = 0.0
    rsi_vote: float = 0.0
    score: float = 0.0
    rsi_value: float = 50.0


@dataclass
class DirectionResultJ:
    """Yön belirleme sonucu."""
    direction: str = "SKIP"     # LONG / SHORT / SKIP
    strength: float = 0.0       # 0-1 arası
    votes: list = field(default_factory=list)
    aligned: bool = False       # işlem TF + teyit TF aynı yön mü


@dataclass
class RegimeResultJ:
    """Rejim tespiti sonucu."""
    regime: str = "UNDECIDED"   # TRENDING / RANGING / GRAY
    er: float = 0.0
    hurst: float = 0.5
    confidence: float = 0.0
    gray_resolved: bool = False  # Gray zone'dan Hurst ile çözüldü mü


@dataclass
class EVResultJ:
    """P(win)/EV hesaplama sonucu."""
    p_win: float = 0.0
    p_loss: float = 0.0
    ev_pct: float = 0.0
    rr_ratio: float = 0.0
    optimal_sl_pct: float = 0.0
    optimal_tp_pct: float = 0.0
    optimal_sl_g_mult: float = 1.5
    optimal_tp_g_mult: float = 2.5
    sufficient: bool = False
    sim_wins: int = 0
    sim_losses: int = 0


@dataclass
class SystemJScanResult:
    """System J tarama sonucu — tek coin için tüm analiz."""
    symbol: str = ""
    score: float = 0.0
    direction: str = ""         # LONG / SHORT
    pool: str = ""              # TREND / RANGING
    scan_pass: int = 0          # 1, 2 veya 3 (hangi turda bulundu)

    # TF ve G
    trade_tf: str = ""          # işlem yapılacak TF
    confirm_tf: str = ""        # teyit TF'si
    G: float = 0.0              # geri dalga ortalaması (%)
    I: float = 0.0              # ileri dalga ortalaması (%)
    cv: float = 0.0
    wave_count: int = 0

    # Kaldıraç
    leverage: int = 1
    max_binance_leverage: int = 125
    maint_margin_rate: float = 0.004
    liq_dist_pct: float = 0.0
    sl_pct: float = 0.0
    tp_pct: float = 0.0
    trailing_trigger_pct: float = 0.0
    trailing_callback_pct: float = 0.0

    # Yön ve rejim
    direction_result: DirectionResultJ = field(default_factory=DirectionResultJ)
    regime: RegimeResultJ = field(default_factory=RegimeResultJ)

    # EV
    ev_result: EVResultJ = field(default_factory=EVResultJ)

    # Giriş
    entry_type: str = "market"  # market / limit
    entry_price: float = 0.0
    wave_position: float = 0.0  # dalga pozisyonu (0-1+)
    last_swing_price: float = 0.0
    last_swing_type: str = ""

    # Market context
    price: float = 0.0
    atr: float = 0.0
    atr_pct: float = 0.0
    rsi: float = 50.0
    funding_rate: float = 0.0
    spread_pct: float = 0.0
    volume_ratio: float = 1.0
    volume_24h: float = 0.0
    bb_upper: float = 0.0
    bb_middle: float = 0.0
    bb_lower: float = 0.0
    er: float = 0.0

    # GUI convenience
    strength: str = ""
    regime_zone: str = ""
    zoom_tf: str = ""
    p_win: float = 0.0
    ev_pct: float = 0.0

    # Elliott Wave
    elliott_pattern: str = ""       # IMPULSE_5 / CORRECTION_ABC / ""
    elliott_confidence: float = 0.0
    elliott_direction: str = ""     # LONG / SHORT (Elliott'un next_move_dir'i)

    # Eligibility
    eligible: bool = False
    reject_reason: str = ""


# ─────────────────────────── Scanner Class ───────────────────────────

class SystemJScanner:
    """System J: Maximum Leverage First Strategy.

    3 turlu döngüsel tarama: max lev → G-bazlı lev → zoom dirsek → tekrar.
    """

    def __init__(self, config: ConfigManager):
        self._config = config
        self._log = logger.bind(name="SystemJ")
        self._ie = IndicatorEngine(config)
        self._lock = threading.RLock()

        # Caches
        self._leverage_brackets: dict[str, LeverageBracketInfo] = {}

    # ────── Config helpers ──────

    def _cfg(self, key: str, default=None):
        return self._config.get(f"system_j.{key}", default)

    def _cfg_lev(self, key: str, default=None):
        return self._config.get(f"system_j.leverage.{key}", default)

    def _cfg_entry(self, key: str, default=None):
        return self._config.get(f"system_j.entry.{key}", default)

    def _cfg_tp(self, key: str, default=None):
        return self._config.get(f"system_j.tp.{key}", default)

    def _cfg_ev(self, key: str, default=None):
        return self._config.get(f"system_j.ev.{key}", default)

    def _cfg_regime(self, key: str, default=None):
        return self._config.get(f"system_j.regime.{key}", default)

    def _cfg_pos(self, key: str, default=None):
        return self._config.get(f"system_j.position.{key}", default)

    def _cfg_filter(self, key: str, default=None):
        return self._config.get(f"system_j.filters.{key}", default)

    def _cfg_score(self, key: str, default=None):
        return self._config.get(f"system_j.score_weights.{key}", default)

    def _cfg_elliott(self, key: str, default=None):
        return self._config.get(f"system_j.elliott.{key}", default)

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # CACHE CLEANUP
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def cleanup_caches(self, active_symbols: set = None) -> None:
        """Artık taranmayan coinlerin leverage bracket cache'ini temizle."""
        if not active_symbols:
            return
        with self._lock:
            stale = [s for s in self._leverage_brackets if s not in active_symbols]
            for s in stale:
                del self._leverage_brackets[s]
            if stale:
                self._log.debug(f"Cache cleanup: {len(stale)} stale bracket silindi")

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # LEVERAGE BRACKET — Binance API
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def load_leverage_brackets(self, rest_client, symbols: list[str]):
        """Binance'den tüm coinlerin leverage bracket bilgisini yükle."""
        try:
            all_brackets = rest_client.get_leverage_brackets()
            for item in all_brackets:
                sym = item.get("symbol", "")
                if sym not in symbols:
                    continue
                brackets = item.get("brackets", [])
                if not brackets:
                    continue
                # En düşük notional bracket = en yüksek kaldıraç
                best = brackets[0]
                info = LeverageBracketInfo(
                    max_leverage=int(best.get("initialLeverage", 125)),
                    maint_margin_rate=float(best.get("maintMarginRate", 0.004)),
                    notional_floor=float(best.get("notionalFloor", 0)),
                    notional_cap=float(best.get("notionalCap", 50000)),
                )
                self._leverage_brackets[sym] = info
        except Exception as e:
            self._log.error(f"Leverage bracket yükleme hatası: {e}")

    def _get_bracket(self, symbol: str) -> LeverageBracketInfo:
        """Coin için bracket bilgisi döndür (cache'den)."""
        return self._leverage_brackets.get(symbol, LeverageBracketInfo())

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # G HESAPLAMA
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def compute_g_for_tf(self, klines, tf: str) -> tuple[float, float, float, int, list, list]:
        """Bir TF için zigzag analizi yaparak G, I, CV, wave_count döndür.

        Returns:
            (G, I, CV, wave_count, swings, backward_waves)
        """
        swing_n = self._cfg("swing_n", 5)
        min_waves = self._cfg_filter("min_wave_count", 3)

        if klines is None or (hasattr(klines, '__len__') and len(klines) < swing_n * 3):
            return 0.0, 0.0, 0.0, 0, [], []

        # DataFrame veya list desteği
        if hasattr(klines, 'values'):
            highs = klines["high"].values.astype(float)
            lows = klines["low"].values.astype(float)
            closes = klines["close"].values.astype(float)
        else:
            highs = np.array([float(k[2]) for k in klines])
            lows = np.array([float(k[3]) for k in klines])
            closes = np.array([float(k[4]) for k in klines])

        swings = detect_zigzag_swings(highs, lows, swing_n)
        if len(swings) < 3:
            return 0.0, 0.0, 0.0, 0, swings, []

        wave = analyze_waves(swings, closes[-1])
        if wave.G < 0.001:
            return 0.0, 0.0, 0.0, 0, swings, wave.backward_waves

        total_waves = len(wave.backward_waves) + len(wave.forward_waves)
        if total_waves < min_waves:
            return 0.0, 0.0, 0.0, 0, swings, wave.backward_waves

        return wave.G, wave.I, wave.cv, total_waves, swings, wave.backward_waves

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # KALDIRAC HESABI
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def calc_g_threshold(self, leverage: int, maint_margin_rate: float) -> float:
        """Verilen kaldıraç için maksimum kabul edilebilir G değeri.

        G_esik = (liq_dist / sl_divisor) / sl_g_mult
        liq_dist = (1/leverage) - maint_margin_rate
        """
        sl_divisor = self._cfg_lev("sl_divisor", 2.0)
        sl_g_mult = self._cfg_lev("sl_g_mult", 1.5)
        fee_total = self._cfg_lev("fee_pct", 0.08) + self._cfg_lev("slippage_pct", 0.04)

        liq_dist = (1.0 / leverage) * 100.0 - maint_margin_rate * 100.0
        if liq_dist <= 0:
            return 0.0

        sl_pct = liq_dist / sl_divisor
        g_threshold = (sl_pct - fee_total) / sl_g_mult
        return max(0.0, g_threshold)

    def calc_leverage_from_g(self, G: float, maint_margin_rate: float) -> int:
        """G değerinden geriye max kaldıraç hesapla.

        SL = G * sl_g_mult + fee_total
        liq_dist = SL * sl_divisor
        teorik_liq = liq_dist + maintMarginRate*100
        max_lev = floor(100 / teorik_liq)
        """
        sl_g_mult = self._cfg_lev("sl_g_mult", 1.5)
        sl_divisor = self._cfg_lev("sl_divisor", 2.0)
        fee_total = self._cfg_lev("fee_pct", 0.08) + self._cfg_lev("slippage_pct", 0.04)
        min_lev = self._cfg_lev("min_leverage", 2)

        sl_pct = G * sl_g_mult + fee_total
        liq_dist = sl_pct * sl_divisor
        teorik_liq = liq_dist + maint_margin_rate * 100.0

        if teorik_liq <= 0:
            return min_lev

        max_lev = int(100.0 / teorik_liq)
        return max(min_lev, max_lev)

    def calc_sl_pct(self, G: float) -> float:
        """G'den SL% hesapla (fee-aware)."""
        sl_g_mult = self._cfg_lev("sl_g_mult", 1.5)
        fee_total = self._cfg_lev("fee_pct", 0.08) + self._cfg_lev("slippage_pct", 0.04)
        return G * sl_g_mult + fee_total

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # TUR 1 — MAX KALDIRAC TARAMASI
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def scan_pass1(self, symbol: str, klines_by_tf: dict,
                   market_ctx: dict) -> Optional[SystemJScanResult]:
        """Tur 1: Max kaldıraçtan başla, G eşiğini sağlayan TF bul.

        Args:
            symbol: Coin sembolü.
            klines_by_tf: {tf_str: klines_data}
            market_ctx: {funding_rate, spread_pct, depth_usd, volume_ratio, ...}

        Returns:
            SystemJScanResult (eligible or rejected with reason), None if no data.
        """
        bracket = self._get_bracket(symbol)
        max_lev = bracket.max_leverage
        mmr = bracket.maint_margin_rate

        g_threshold = self.calc_g_threshold(max_lev, mmr)
        if g_threshold <= 0:
            return None

        last_rejected = None  # GUI'de göstermek için son reddedilen sonuç

        # TF merdiveninde G eşiğini sağlayan ilk TF'yi bul
        for tf in TF_LADDER:
            klines = klines_by_tf.get(tf)
            if klines is None:
                continue

            G, I, cv, wc, swings, bw = self.compute_g_for_tf(klines, tf)
            if G <= 0 or wc < self._cfg_filter("min_wave_count", 3):
                continue

            if G <= g_threshold:
                # Bu TF'de bu kaldıraçta işlem yapılabilir!
                result = self._build_result(
                    symbol=symbol, tf=tf, G=G, I=I, cv=cv,
                    wave_count=wc, leverage=max_lev,
                    bracket=bracket, swings=swings,
                    backward_waves=bw,
                    klines_by_tf=klines_by_tf,
                    market_ctx=market_ctx, scan_pass=1,
                )
                if result and result.eligible:
                    return result
                if result:
                    last_rejected = result

        return last_rejected

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # TUR 2 — G-BAZLI KALDIRAC TARAMASI
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def scan_pass2(self, symbol: str, klines_by_tf: dict,
                   market_ctx: dict) -> Optional[SystemJScanResult]:
        """Tur 2: Her TF'deki G'den max kaldıraç hesapla, en iyisini seç."""
        bracket = self._get_bracket(symbol)
        mmr = bracket.maint_margin_rate
        min_lev = self._cfg_lev("min_leverage", 2)

        best_result = None
        best_lev = 0
        last_rejected = None

        for tf in TF_LADDER:
            klines = klines_by_tf.get(tf)
            if klines is None:
                continue

            G, I, cv, wc, swings, bw = self.compute_g_for_tf(klines, tf)
            if G <= 0 or wc < self._cfg_filter("min_wave_count", 3):
                continue

            calc_lev = self.calc_leverage_from_g(G, mmr)
            calc_lev = min(calc_lev, bracket.max_leverage)

            if calc_lev < min_lev:
                continue

            if calc_lev > best_lev or (calc_lev == best_lev and best_result and wc > best_result.wave_count):
                result = self._build_result(
                    symbol=symbol, tf=tf, G=G, I=I, cv=cv,
                    wave_count=wc, leverage=calc_lev,
                    bracket=bracket, swings=swings,
                    backward_waves=bw,
                    klines_by_tf=klines_by_tf,
                    market_ctx=market_ctx, scan_pass=2,
                )
                if result and result.eligible:
                    best_result = result
                    best_lev = calc_lev
                elif result:
                    last_rejected = result

        return best_result or last_rejected

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # TUR 3 — ZOOM DİRSEK TARAMASI
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def scan_pass3(self, symbol: str, klines_by_tf: dict,
                   market_ctx: dict) -> Optional[SystemJScanResult]:
        """Tur 3: Zoom dirsek — yukarıdan aşağı en verimli G/TF noktası."""
        bracket = self._get_bracket(symbol)
        mmr = bracket.maint_margin_rate
        min_lev = self._cfg_lev("min_leverage", 2)

        g_tf_verimli = self._cfg("zoom_g_tf_efficient", 0.60)
        g_tf_verimsiz = self._cfg("zoom_g_tf_inefficient", 0.80)

        # Her TF'de G hesapla
        tf_data = []
        for tf_name, tf_minutes in ZOOM_TF_LADDER:
            klines = klines_by_tf.get(tf_name)
            if klines is None:
                continue

            G, I, cv, wc, swings, bw = self.compute_g_for_tf(klines, tf_name)
            if G <= 0 or wc < self._cfg_filter("min_wave_count", 3):
                continue

            tf_data.append({
                "tf": tf_name, "minutes": tf_minutes,
                "G": G, "I": I, "cv": cv, "wc": wc,
                "swings": swings, "bw": bw,
            })

        if not tf_data:
            return None

        # G/TF oranı hesapla
        for i in range(1, len(tf_data)):
            prev = tf_data[i - 1]
            curr = tf_data[i]
            if prev["G"] > 0:
                g_artis_pct = (curr["G"] - prev["G"]) / prev["G"] * 100
                tf_artis_pct = (curr["minutes"] - prev["minutes"]) / prev["minutes"] * 100
                curr["g_tf_oran"] = g_artis_pct / tf_artis_pct if tf_artis_pct > 0 else 0
            else:
                curr["g_tf_oran"] = 0
        if tf_data:
            tf_data[0]["g_tf_oran"] = 0

        # Alttan yukarı tarama — dirsek noktası bul
        # g_tf_verimli: bu eşiğin altı çok verimli, devam et
        # g_tf_verimli-g_tf_verimsiz arası: dirsek bölgesi, al ve dur
        # g_tf_verimsiz üstü: çok pahalı, dur
        best = tf_data[0]
        for i in range(1, len(tf_data)):
            curr = tf_data[i]
            oran = curr.get("g_tf_oran", 0)

            # G azaldıysa bedava uzatma
            if curr["G"] < best["G"]:
                best = curr
            elif oran <= g_tf_verimli:
                best = curr  # Çok verimli, devam et
            elif oran <= g_tf_verimsiz:
                best = curr  # Dirsek bölgesi: al ama dur
                break
            else:
                break  # Verimsiz, dur

        # Kaldıraç hesapla
        calc_lev = self.calc_leverage_from_g(best["G"], mmr)
        calc_lev = min(calc_lev, bracket.max_leverage)
        if calc_lev < min_lev:
            return None

        result = self._build_result(
            symbol=symbol, tf=best["tf"], G=best["G"], I=best["I"],
            cv=best["cv"], wave_count=best["wc"], leverage=calc_lev,
            bracket=bracket, swings=best["swings"],
            backward_waves=best["bw"],
            klines_by_tf=klines_by_tf,
            market_ctx=market_ctx, scan_pass=3,
        )
        return result

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # ORTAK: RESULT OLUŞTURMA
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def _build_result(self, symbol: str, tf: str, G: float, I: float,
                      cv: float, wave_count: int, leverage: int,
                      bracket: LeverageBracketInfo,
                      swings: list, backward_waves: list,
                      klines_by_tf: dict,
                      market_ctx: dict, scan_pass: int,
                      ) -> Optional[SystemJScanResult]:
        """Tüm kontrolleri yaparak sonuç oluştur.

        Sıra: hard filter → rejim → yön → EV → giriş → skor.
        """
        result = SystemJScanResult(
            symbol=symbol, trade_tf=tf, scan_pass=scan_pass,
            G=G, I=I, cv=cv, wave_count=wave_count,
            leverage=leverage,
            max_binance_leverage=bracket.max_leverage,
            maint_margin_rate=bracket.maint_margin_rate,
        )

        # Teyit TF
        result.confirm_tf = CONFIRM_TF_MAP.get(tf, "1d")

        # Liq distance
        result.liq_dist_pct = (1.0 / leverage) * 100.0 - bracket.maint_margin_rate * 100.0

        # SL
        result.sl_pct = self.calc_sl_pct(G)

        # Market context
        result.price = market_ctx.get("price", 0)
        result.funding_rate = market_ctx.get("funding_rate", 0)
        result.spread_pct = market_ctx.get("spread_pct", 0)
        result.volume_ratio = market_ctx.get("volume_ratio", 1.0)
        result.volume_24h = market_ctx.get("volume_24h", 0)

        # ---- Hard Filtreler ----
        eligible, reason = self._check_hard_filters(result, market_ctx)
        if not eligible:
            result.eligible = False
            result.reject_reason = reason
            return result

        # ---- Elliott koruma filtreleri: min TF + min G ----
        if self._cfg_elliott("enabled", True):
            min_tf_minutes = self._cfg_elliott("min_tf_minutes", 15)
            tf_minutes = TF_MINUTES.get(tf, 1)
            if tf_minutes < min_tf_minutes:
                result.eligible = False
                result.reject_reason = f"LOW_TF({tf}<{min_tf_minutes}m)"
                return result

            min_g = self._cfg_elliott("min_g_pct", 1.0)
            if G < min_g:
                result.eligible = False
                result.reject_reason = f"LOW_G({G:.3f}<{min_g})"
                return result

        # ---- İndikatörleri bir kez hesapla ----
        klines_tf = klines_by_tf.get(tf)
        ind_tf = self._compute_indicators(klines_tf)

        # BB/RSI/ATR değerlerini hemen doldur (GUI + giriş hesabı)
        if ind_tf:
            result.bb_upper = ind_tf.get("BB_Upper", 0)
            result.bb_middle = ind_tf.get("BB_Middle", 0)
            result.bb_lower = ind_tf.get("BB_Lower", 0)
            result.rsi = ind_tf.get("RSI", 50)
            result.atr = ind_tf.get("ATR", 0)
            if result.price > 0 and result.atr > 0:
                result.atr_pct = (result.atr / result.price) * 100

        # ---- Elliott Wave Tespiti (rejim + yön yerine) ----
        elliott_enabled = self._cfg_elliott("enabled", True)
        if elliott_enabled:
            elliott_result = self._detect_elliott_pattern(swings, ind_tf)
            if elliott_result is None:
                result.eligible = False
                result.reject_reason = "NO_ELLIOTT"
                result.regime_zone = "NO_PATTERN"
                return result

            result.elliott_pattern = elliott_result.pattern_type
            result.elliott_confidence = elliott_result.confidence
            result.elliott_direction = elliott_result.next_move_dir
            result.direction = elliott_result.next_move_dir
            result.strength = "STRONG" if elliott_result.confidence >= 0.5 else "WEAK"

            # Elliott impulse = trend tamamlanmış, correction = ranging tamamlanmış
            # Ama çıkış hep reaktif — pool sadece GUI/loglama için
            result.pool = "TREND"
            result.regime_zone = f"EW:{elliott_result.pattern_type[:3]}({elliott_result.confidence:.2f})"
            result.regime = RegimeResultJ(
                regime="TRENDING", er=0, confidence=elliott_result.confidence)

            # Direction result oluştur (uyumluluk için)
            result.direction_result = DirectionResultJ(
                direction=elliott_result.next_move_dir,
                strength=elliott_result.confidence,
                aligned=True,
            )
        else:
            # Elliott kapalı — eski rejim + yön mantığı (fallback)
            klines_confirm = klines_by_tf.get(result.confirm_tf)
            ind_confirm = self._compute_indicators(klines_confirm)

            result.regime = self._compute_regime(klines_tf)
            result.er = result.regime.er

            if result.regime.regime == "UNDECIDED":
                result.eligible = False
                result.reject_reason = "UNDECIDED_REGIME"
                result.regime_zone = "UNDECIDED"
                return result

            if result.regime.gray_resolved:
                result.regime_zone = f"{result.regime.regime}(H={result.regime.hurst:.2f})"
            else:
                result.regime_zone = result.regime.regime

            result.pool = "TREND" if result.regime.regime == "TRENDING" else "RANGING"

            result.direction_result = self._compute_direction_cached(
                ind_tf, ind_confirm, tf, result.confirm_tf, result.regime.regime)
            result.direction = result.direction_result.direction
            result.strength = "STRONG" if result.direction_result.aligned else "WEAK"

            if result.direction == "SKIP":
                result.eligible = False
                result.reject_reason = "NO_DIRECTION"
                return result

        # ---- P(win)/EV ----
        # Elliott aktifken EV zorunlu değil (backtest'te EV filtresi yoktu,
        # Elliott pattern zaten güçlü giriş filtresi)
        ev_skip = elliott_enabled and self._cfg_elliott("skip_ev", True)

        result.ev_result = self._compute_ev(
            result.direction, G, result.sl_pct, leverage,
            swings, backward_waves, klines_tf,
            liq_dist_pct=result.liq_dist_pct)
        result.p_win = result.ev_result.p_win
        result.ev_pct = result.ev_result.ev_pct

        if not result.ev_result.sufficient and not ev_skip:
            result.eligible = False
            result.reject_reason = "NEGATIVE_EV"
            return result

        # ---- TP/Trailing (varsayılan değerler) ----
        self._set_exit_params(result, klines_tf)

        # Ranging R:R yetersiz (BB TP < SL, sentinel=-1) → reject
        if result.tp_pct < 0:
            result.eligible = False
            result.reject_reason = "LOW_RR_RANGING"
            result.tp_pct = 0
            return result

        # EV optimal SL/TP override (SL cap artık _compute_ev içinde yapılıyor)
        if result.ev_result.optimal_sl_pct > 0:
            result.sl_pct = result.ev_result.optimal_sl_pct
        if result.ev_result.optimal_tp_pct > 0:
            result.tp_pct = result.ev_result.optimal_tp_pct

        # ---- Giriş Stratejisi ----
        self._set_entry(result, swings)

        # ---- Skor ----
        result.score = self._compute_score(result)

        result.eligible = True
        result.zoom_tf = tf
        return result

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # HARD FİLTRELER
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def _check_hard_filters(self, result: SystemJScanResult,
                            market_ctx: dict) -> tuple[bool, str]:
        """Hard filtre kontrolü."""
        fr = abs(result.funding_rate)
        max_fr = self._cfg_filter("funding_rate_max", 0.0003)
        if fr > max_fr:
            return False, f"HIGH_FR({fr:.4f})"

        max_spread = self._cfg_filter("max_spread_pct", 0.05)
        if result.spread_pct > max_spread:
            return False, f"HIGH_SPREAD({result.spread_pct:.3f})"

        min_depth = self._cfg_filter("min_depth_usd", 100000)
        depth = market_ctx.get("depth_usd", 0)
        if 0 < depth < min_depth:
            return False, f"THIN_BOOK({depth:.0f})"

        min_vol = self._cfg_filter("min_volume_ratio", 1.0)
        if result.volume_ratio < min_vol:
            return False, f"LOW_VOL({result.volume_ratio:.2f})"

        return True, ""

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # REJİM TESPİTİ
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def _compute_regime(self, klines) -> RegimeResultJ:
        """ER bazlı rejim tespiti + Gray zone Hurst dual-vote.

        Kesin bölgeler (ER tek başına karar verir):
          ER > er_trending → TRENDING
          ER < er_ranging  → RANGING

        Gray zone (ER 0.08-0.25 arası → Hurst hakem):
          H > hurst_trending → TRENDING (persistent seri)
          H < hurst_ranging  → RANGING  (mean-reverting seri)
          H 0.45-0.55        → ER tiebreaker (midpoint'e göre)

        Hiçbir coin atılmaz — gray_zone_skip artık gereksiz.
        """
        result = RegimeResultJ()
        if klines is None:
            return result

        # Closes çıkar
        if hasattr(klines, 'values'):
            closes = klines["close"].values.astype(float)
        else:
            closes = np.array([float(k[4]) for k in klines])

        if len(closes) < 30:
            return result

        er_window = self._cfg_regime("er_window", 20)
        er_median_n = self._cfg_regime("er_median_count", 10)
        er_trending = self._cfg_regime("er_trending", 0.25)
        er_ranging = self._cfg_regime("er_ranging", 0.08)

        # Rolling ER hesapla
        er_median = compute_rolling_er(closes, er_window, median_count=er_median_n)
        if er_median <= 0:
            return result

        result.er = er_median

        # ── Kesin bölgeler: ER tek başına yeter ──
        if er_median > er_trending:
            result.regime = "TRENDING"
            result.confidence = min(1.0, (er_median - er_trending) / 0.15)
            return result

        if er_median < er_ranging:
            result.regime = "RANGING"
            result.confidence = min(1.0, (er_ranging - er_median) / 0.05)
            return result

        # ── Gray zone: Hurst hakem ──
        hurst_trending = self._cfg_regime("hurst_trending", 0.55)
        hurst_ranging = self._cfg_regime("hurst_ranging", 0.45)

        hurst = compute_hurst_exponent(closes)
        result.hurst = hurst
        result.gray_resolved = True

        er_midpoint = (er_trending + er_ranging) / 2.0  # 0.165

        if hurst > hurst_trending:
            # Hurst net trend diyor
            result.regime = "TRENDING"
            result.confidence = min(0.7, (hurst - hurst_trending) / 0.3)
        elif hurst < hurst_ranging:
            # Hurst net ranging diyor
            result.regime = "RANGING"
            result.confidence = min(0.7, (hurst_ranging - hurst) / 0.3)
        elif er_median > er_midpoint:
            # Hurst belirsiz, ER trende daha yakın
            result.regime = "TRENDING"
            result.confidence = 0.3
        else:
            # Hurst belirsiz, ER ranging'e daha yakın
            result.regime = "RANGING"
            result.confidence = 0.3

        return result

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # YÖN BELİRLEME
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def _compute_direction_cached(self, ind_trade: dict, ind_confirm: dict,
                                   trade_tf: str, confirm_tf: str,
                                   regime: str) -> DirectionResultJ:
        """Rejime bağlı yön tespiti (önceden hesaplanan indikatörlerle).

        TRENDING: trade TF + confirm TF aynı yön gerekli (momentum teyidi).
        RANGING: sadece trade TF sinyali yeterli (ortalamaya dönüş, üst TF
                 teyidi çelişir — dipte LONG sinyali verirken üst TF DOWN olur).
        """
        result = DirectionResultJ()

        if regime == "RANGING":
            # Ranging: teyit TF gerekmez (mean reversion mantığı trend teyidiyle çelişir)
            vote_trade = self._vote_ranging_ind(ind_trade, trade_tf)
            result.votes = [vote_trade]

            if vote_trade.score == 0:
                result.direction = "SKIP"
                return result

            result.direction = "LONG" if vote_trade.score > 0 else "SHORT"
            result.aligned = True
            result.strength = abs(vote_trade.score)
            return result

        # TRENDING: her iki TF de aynı yönü göstermeli
        vote_trade = self._vote_trend_ind(ind_trade, trade_tf)
        vote_confirm = self._vote_trend_ind(ind_confirm, confirm_tf)

        result.votes = [vote_trade, vote_confirm]

        if vote_trade.score == 0 or vote_confirm.score == 0:
            result.direction = "SKIP"
            return result

        trade_dir = "LONG" if vote_trade.score > 0 else "SHORT"
        confirm_dir = "LONG" if vote_confirm.score > 0 else "SHORT"

        if trade_dir == confirm_dir:
            result.direction = trade_dir
            result.aligned = True
            result.strength = abs(vote_trade.score + vote_confirm.score) / 2.0
        else:
            result.direction = "SKIP"
            result.aligned = False

        return result

    def _vote_trend_ind(self, indicators: dict, tf: str) -> DirectionVoteJ:
        """Trend momentum oyu (indikatör dict'i ile).

        Config'den okunan parametreler:
          - direction.ema_gap_min_pct: EMA farkı minimum % (varsayılan 0.05)
          - direction.rsi_long_threshold: RSI LONG eşiği (varsayılan 55)
          - direction.rsi_short_threshold: RSI SHORT eşiği (varsayılan 45)
        EMA: EMA_fast (config ma_fast) vs EMA_slow (system_j.direction.ema_slow)
        MACD: Binary oy (±1), fraksiyonel değil (2/3 çoğunluk kuralı korunsun)
        """
        vote = DirectionVoteJ(timeframe=tf)
        if not indicators:
            return vote

        # EMA: fast vs slow (config'den period, _compute_indicators'da hesaplanır)
        ema_fast = indicators.get("EMA_fast", 0)
        ema_slow = indicators.get("EMA_slow", 0)
        ema_gap_min = self._cfg("direction.ema_gap_min_pct", 0.05) / 100.0
        if ema_fast > 0 and ema_slow > 0:
            gap = abs(ema_fast - ema_slow) / ema_slow
            if gap >= ema_gap_min:
                vote.ema_vote = 1.0 if ema_fast > ema_slow else -1.0

        # MACD — binary oy (2/3 kuralını korumak için fraksiyonel yok)
        macd_hist = indicators.get("MACD_histogram", 0)
        if macd_hist > 0:
            vote.macd_vote = 1.0
        elif macd_hist < 0:
            vote.macd_vote = -1.0

        # RSI — config'den eşikler
        rsi = indicators.get("RSI", 50)
        vote.rsi_value = rsi
        rsi_long = self._cfg("direction.rsi_long_threshold", 55)
        rsi_short = self._cfg("direction.rsi_short_threshold", 45)
        if rsi > rsi_long:
            vote.rsi_vote = 1.0
        elif rsi < rsi_short:
            vote.rsi_vote = -1.0

        total = vote.ema_vote + vote.macd_vote + vote.rsi_vote
        vote.score = total / 3.0
        # En az 2/3 aynı yön (binary oylarla: |score| >= 0.33 → en az 2 oy aynı)
        if abs(vote.score) < 0.33:
            vote.score = 0.0

        return vote

    def _vote_ranging_ind(self, indicators: dict, tf: str) -> DirectionVoteJ:
        """Ranging rejimde ters mantık oyu (indikatör dict'i ile)."""
        vote = DirectionVoteJ(timeframe=tf)
        if not indicators:
            return vote

        rsi = indicators.get("RSI", 50)
        vote.rsi_value = rsi
        bb_upper = indicators.get("BB_Upper", 0)
        bb_lower = indicators.get("BB_Lower", 0)
        price = indicators.get("Price", 0)

        if rsi > 70:
            vote.score = -1.0  # SHORT (aşırı alım)
        elif rsi < 30:
            vote.score = 1.0   # LONG (aşırı satım)
        elif price > 0 and bb_upper > 0 and bb_lower > 0:
            if price > bb_upper * 0.95:
                vote.score = -1.0  # SHORT (üst bant yakını)
            elif price < bb_lower * 1.05:
                vote.score = 1.0   # LONG (alt bant yakını)
            # Ortada → sinyal yok (score=0)

        return vote

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # P(win) / EV HESABI
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def _compute_ev(self, direction: str, G: float, sl_pct: float,
                    leverage: int, swings: list, backward_waves: list,
                    klines, liq_dist_pct: float = 0.0) -> EVResultJ:
        """Dalga simulasyonu ile P(win)/EV ve optimal SL/TP.

        Düzeltmeler:
          - fee_roi kaldırıldı (fee zaten test_tp/test_sl içinde — çift sayma)
          - min_p_win kontrolü eklendi
          - SL cap: liq_dist / sl_divisor aşılamaz (EV içinde kontrol)
        """
        result = EVResultJ()
        if not swings or len(swings) < 4:
            return result

        min_rr = self._cfg_ev("min_rr", 2.5)
        min_p_win = self._cfg_ev("min_p_win", 0.40)
        fee_total = self._cfg_lev("fee_pct", 0.08) + self._cfg_lev("slippage_pct", 0.04)
        sl_divisor = self._cfg_lev("sl_divisor", 2.0)

        # SL üst sınırı: liq mesafesinin sl_divisor'a bölümünü aşmamalı
        max_sl_pct = (liq_dist_pct / sl_divisor) if liq_dist_pct > 0 else 999.0

        # Dalga verisi hazırla — son fiyatı klines'dan al
        last_price = 0.0
        if klines is not None:
            if hasattr(klines, 'values'):
                last_price = float(klines["close"].values[-1])
            elif hasattr(klines, '__len__') and len(klines) > 0:
                last_price = float(klines[-1][4])
        if last_price <= 0:
            last_price = swings[-1].price  # fallback: son swing fiyatı

        wave = analyze_waves(swings, last_price)

        # Yöne göre forward/retrace ata:
        wave_dir_long = wave.trend_direction == "UP"
        trade_dir_long = direction == "LONG"

        if wave_dir_long == trade_dir_long:
            forward_pcts = wave.forward_waves
            retrace_pcts = wave.backward_waves
        else:
            forward_pcts = wave.backward_waves
            retrace_pcts = wave.forward_waves

        if len(forward_pcts) < 3 or len(retrace_pcts) < 3:
            return result

        # SL ve TP adayları (G çarpanları)
        sl_mults = [float(x) for x in self._cfg_ev("sl_candidates", [1.0, 1.25, 1.5, 1.75, 2.0])]
        tp_mults = [float(x) for x in self._cfg_ev("tp_candidates", [2.0, 2.5, 3.0, 3.5, 4.0, 5.0])]

        best_ev = -999
        best_combo = None

        for sl_m in sl_mults:
            test_sl = sl_m * G + fee_total
            # SL cap: liq mesafesini aşan SL geçersiz
            if test_sl > max_sl_pct:
                continue
            for tp_m in tp_mults:
                test_tp = tp_m * G - fee_total  # net TP (fee düşülmüş)
                if test_tp <= 0:
                    continue

                # R:R kontrolü
                if test_sl > 0 and test_tp / test_sl < min_rr:
                    continue

                # P(SL hit) = retrace >= SL olan oran
                sl_hits = sum(1 for r in retrace_pcts if r >= test_sl)
                p_sl = sl_hits / len(retrace_pcts)

                # P(TP hit) = forward >= TP olan oran
                tp_hits = sum(1 for f in forward_pcts if f >= test_tp)
                p_tp = tp_hits / len(forward_pcts)

                # Normalize (multi-round race: p/(p+q-pq))
                denom = p_tp + p_sl - p_tp * p_sl
                if denom <= 0:
                    continue
                p_win = p_tp / denom
                p_loss = 1.0 - p_win

                # P(win) minimum kontrolü
                if p_win < min_p_win:
                    continue

                # EV hesapla (fee zaten test_tp ve test_sl içinde, ekstra fee_roi YOK)
                ev = p_win * test_tp * leverage / 100.0 - p_loss * test_sl * leverage / 100.0

                if ev > best_ev:
                    best_ev = ev
                    best_combo = {
                        "sl_m": sl_m, "tp_m": tp_m,
                        "sl_pct": test_sl, "tp_pct": test_tp,
                        "p_win": p_win, "p_loss": p_loss,
                        "ev": ev, "rr": test_tp / test_sl if test_sl > 0 else 0,
                    }

        if best_combo and best_combo["ev"] > self._cfg_ev("ev_min_threshold", 0.0):
            result.p_win = best_combo["p_win"]
            result.p_loss = best_combo["p_loss"]
            result.ev_pct = best_combo["ev"] * 100
            result.rr_ratio = best_combo["rr"]
            result.optimal_sl_pct = best_combo["sl_pct"]
            result.optimal_tp_pct = best_combo["tp_pct"]
            result.optimal_sl_g_mult = best_combo["sl_m"]
            result.optimal_tp_g_mult = best_combo["tp_m"]
            result.sufficient = True
            result.sim_wins = sum(1 for f in forward_pcts if f >= best_combo["tp_pct"])
            result.sim_losses = sum(1 for r in retrace_pcts if r >= best_combo["sl_pct"])

        return result

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # TP / TRAILING AYARLARI
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def _set_exit_params(self, result: SystemJScanResult, klines):
        """TP/trailing parametreleri.

        Elliott aktifken: her zaman reaktif trailing (rejim ayrımı yok).
        Elliott kapalıyken: eski rejim bazlı mantık.
        """
        G = result.G
        elliott_enabled = self._cfg_elliott("enabled", True)

        if elliott_enabled:
            # ── Reaktif çıkış: her zaman trailing, rejim ayrımı yok ──
            # Backtest optimal: trigger=1.0xG, callback=0.3xG (ATR yerine G bazlı)
            trigger_mult = self._cfg_elliott("trail_trigger_g_mult", 1.0)
            callback_mult = self._cfg_elliott("trail_callback_g_mult", 0.3)
            callback = callback_mult * G

            if callback >= 0.10:
                result.trailing_trigger_pct = trigger_mult * G
                result.trailing_callback_pct = max(0.1, min(callback, 5.0))
            else:
                # Callback çok sıkı → sabit TP fallback
                result.trailing_trigger_pct = 0
                result.trailing_callback_pct = 0

            if result.tp_pct <= 0:
                result.tp_pct = trigger_mult * G
        else:
            # ── Eski rejim bazlı mantık (Elliott kapalı) ──
            if result.pool == "TREND":
                trigger_mult = self._cfg_tp("trailing_trigger_g_mult", 2.5)
                callback_mult = self._cfg_tp("trailing_callback_g_mult", 0.5)
                callback = callback_mult * G

                if callback >= 0.15:
                    result.trailing_trigger_pct = trigger_mult * G
                    result.trailing_callback_pct = max(0.1, min(callback, 5.0))
                else:
                    result.trailing_trigger_pct = 0
                    result.trailing_callback_pct = 0

                if result.tp_pct <= 0:
                    result.tp_pct = trigger_mult * G
            else:
                bb_mid = result.bb_middle
                price = result.price
                if bb_mid > 0 and price > 0:
                    if result.direction == "LONG":
                        bb_tp = (bb_mid - price) / price * 100
                    else:
                        bb_tp = (price - bb_mid) / price * 100
                    if bb_tp > result.sl_pct:
                        result.tp_pct = bb_tp
                        return
                    result.tp_pct = -1.0
                    return

                tp_mult = self._cfg_tp("ranging_tp_g_mult", 2.0)
                result.tp_pct = tp_mult * G

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # GİRİŞ STRATEJİSİ
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def _set_entry(self, result: SystemJScanResult, swings: list):
        """Dalga pozisyonuna göre giriş tipi ve fiyatı belirle.

        Düzeltme: wave_position yöne uygun swing'den hesaplanır.
          LONG → son SL (swing low) referans alınır
          SHORT → son SH (swing high) referans alınır
        """
        G = result.G
        price = result.price
        dip_thresh = self._cfg_entry("wave_dip_threshold", 0.3)
        mid_thresh = self._cfg_entry("wave_mid_threshold", 0.7)
        limit_ratio = self._cfg_entry("limit_g_offset_ratio", 0.5)

        if not swings or price <= 0:
            result.entry_type = "market"
            result.entry_price = price
            return

        # Yöne uygun son swing'i bul (LONG→SL, SHORT→SH)
        target_type = "SL" if result.direction == "LONG" else "SH"
        relevant = [s for s in swings if s.type == target_type]
        ref_swing = relevant[-1] if relevant else swings[-1]

        result.last_swing_price = ref_swing.price
        result.last_swing_type = ref_swing.type

        # Dalga pozisyonu hesapla (referans swing'den mesafe / G)
        if G > 0 and ref_swing.price > 0:
            g_distance = ref_swing.price * G / 100.0
            result.wave_position = abs(price - ref_swing.price) / g_distance if g_distance > 0 else 0.5
        else:
            result.wave_position = 0.5

        g_abs = price * G / 100.0  # G'nin fiyat cinsinden karşılığı

        if result.direction == "LONG":
            # Dip yakınında (referans swing yakın) → market
            if ref_swing.type == "SL" and result.wave_position < dip_thresh:
                result.entry_type = "market"
                result.entry_price = price
            elif result.wave_position > mid_thresh:
                # Tepede → bekleme (market, ama yüksek riskli — skor cezalandırır)
                result.entry_type = "market"
                result.entry_price = price
            else:
                # Ortada → limit: mevcut fiyattan G'nin yarısı aşağı hedefle
                result.entry_type = "limit"
                result.entry_price = price - g_abs * limit_ratio
        else:
            # SHORT: tepe yakınında → market
            if ref_swing.type == "SH" and result.wave_position < dip_thresh:
                result.entry_type = "market"
                result.entry_price = price
            elif result.wave_position > mid_thresh:
                result.entry_type = "market"
                result.entry_price = price
            else:
                # Ortada → limit: mevcut fiyattan G'nin yarısı yukarı hedefle
                result.entry_type = "limit"
                result.entry_price = price + g_abs * limit_ratio

        # Sanity: limit fiyat mevcut fiyattan çok uzak olmamalı (max 2G)
        if result.entry_type == "limit" and price > 0:
            max_offset = price * 2 * G / 100.0
            if abs(result.entry_price - price) > max_offset:
                result.entry_type = "market"
                result.entry_price = price

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # SKORLAMA
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def _compute_score(self, result: SystemJScanResult) -> float:
        """Basit composite skor (0-100)."""
        w_dir = self._cfg_score("direction_strength", 0.30)
        w_ev = self._cfg_score("ev_quality", 0.30)
        w_regime = self._cfg_score("regime_clarity", 0.20)
        w_wave = self._cfg_score("wave_quality", 0.20)

        # Direction strength (0-100)
        dir_score = result.direction_result.strength * 100 if result.direction_result.aligned else 0

        # EV quality (0-100)
        # Elliott aktif + EV skip → EV yerine Elliott confidence kullan
        if result.elliott_pattern and not result.ev_result.sufficient:
            ev_score = result.elliott_confidence * 100
        else:
            ev_raw = result.ev_result.ev_pct if result.ev_result.sufficient else 0
            ev_score = min(100, max(0, (ev_raw + 5) / 105 * 100))

        # Elliott aktifse: elliott confidence kullan, değilse regime confidence
        if result.elliott_pattern:
            regime_score = result.elliott_confidence * 100
        else:
            regime_score = result.regime.confidence * 100

        # Wave quality (0-100)
        wc = min(result.wave_count / 10.0, 1.0)
        cv_pen = 1.0 - min(result.cv / 2.0, 1.0)
        wave_score = wc * cv_pen * 100

        score = (w_dir * dir_score + w_ev * ev_score +
                 w_regime * regime_score + w_wave * wave_score)
        return max(0, min(100, score))

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # ELLIOTT WAVE TESPİTİ
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def _detect_elliott_pattern(self, swings: list, indicators: dict = None
                                 ) -> Optional[ElliottPattern]:
        """Zigzag swinglerinden Elliott pattern tespit et.

        Args:
            swings: detect_zigzag_swings() çıktısı
            indicators: İndikatör dict (yön teyidi için — opsiyonel)

        Returns:
            ElliottPattern veya None (pattern bulunamazsa)
        """
        min_confidence = self._cfg_elliott("min_confidence", 0.35)
        stale_bars = self._cfg_elliott("max_stale_bars", 30)

        if not swings or len(swings) < 4:
            return None

        pattern = detect_elliott(swings, min_confidence=min_confidence)
        if pattern is None:
            return None

        # İndikatör ile yön teyidi (soft filter — çelişki varsa reddet)
        if indicators:
            ema_fast = indicators.get("EMA_fast", 0)
            ema_slow = indicators.get("EMA_slow", 0)
            macd_hist = indicators.get("MACD_histogram", 0)
            rsi = indicators.get("RSI", 50)

            # Basit yön skoru
            ind_score = 0.0
            if ema_fast > 0 and ema_slow > 0:
                if ema_fast > ema_slow:
                    ind_score += 1
                elif ema_fast < ema_slow:
                    ind_score -= 1
            if macd_hist > 0:
                ind_score += 1
            elif macd_hist < 0:
                ind_score -= 1
            if rsi > 55:
                ind_score += 1
            elif rsi < 45:
                ind_score -= 1
            ind_score /= 3.0

            # Elliott yönü ile güçlü çelişki varsa reddet
            if pattern.next_move_dir == "LONG" and ind_score < -0.33:
                return None
            if pattern.next_move_dir == "SHORT" and ind_score > 0.33:
                return None

        return pattern

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # İNDİKATÖR HELPER
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def _compute_indicators(self, klines) -> Optional[dict]:
        """IndicatorEngine ile indikatör hesapla + System J özel EMA_slow ekle."""
        try:
            if klines is None:
                return None
            if hasattr(klines, 'empty') and klines.empty:
                return None
            if hasattr(klines, '__len__') and len(klines) < 30:
                return None
            results = self._ie.compute_all(klines)
            if results is None:
                return None

            # System J özel EMA_fast + EMA_slow (config'den period)
            if hasattr(klines, 'values'):
                closes = klines["close"].values.astype(float)
            else:
                closes = np.array([float(k[4]) for k in klines])

            ema_fast_period = int(self._cfg("direction.ema_fast", 9))
            ema_slow_period = int(self._cfg("direction.ema_slow", 21))
            results["EMA_fast"] = _ema_value(closes, ema_fast_period)
            results["EMA_slow"] = _ema_value(closes, ema_slow_period)

            if "Price" not in results and len(closes) > 0:
                results["Price"] = float(closes[-1])

            return results
        except Exception as e:
            self._log.debug(f"İndikatör hatası: {e}")
            return None

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # POZİSYON BOYUTLANDIRMA
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def calculate_position_size(self, balance: float) -> float:
        """Dinamik pozisyon boyutlandırma."""
        min_pos = self._cfg_pos("min_position_usd", 1.0)
        min_div = self._cfg_pos("min_divider", 4)
        max_div = self._cfg_pos("max_divider", 12)

        if balance <= 0:
            return min_pos

        divider = max(min_div, min(balance / min_pos, max_div))
        size = balance / divider
        return max(min_pos, size)
