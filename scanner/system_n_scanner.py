"""System N Scanner — AlphaTrend PRO v2 + G-bazlı dinamik kaldıraç.

System M'den evrilmiş akıllı sistem:
  - Backtest optimize dosyasından coin başına coeff/period/TF okur
  - G dalga analiziyle dinamik kaldıraç hesaplar
  - Günde 1 kere re-optimize (cache: data/system_n_optimize.json)

Modlar:
  - Spot (short_enabled=False): BUY→LONG aç, SELL→LONG kapat
  - Short (reverse=False): BUY→LONG, SELL→kapat, sonraki sinyal yeni pozisyon
  - Short+Reverse (reverse=True): BUY→LONG (short varsa çevir), SELL→SHORT (long varsa çevir)
"""
import json
import os
import time as _time
import threading
from dataclasses import dataclass, field
from loguru import logger
from core.config_manager import ConfigManager

import numpy as np


# ═══════════════════════════════════════════════════════════════════
#  Data Classes
# ═══════════════════════════════════════════════════════════════════

@dataclass
class AlphaTrendState:
    """Per-coin AlphaTrend hesaplama durumu (stateful — barlar arası)."""
    alpha_trend: list[float] = field(default_factory=list)
    trend_direction: int = 0   # +1=long, -1=short, 0=belirsiz


@dataclass
class SystemNSignal:
    """Tek bir coin için sinyal sonucu."""
    symbol: str
    signal: str             # "BUY", "SELL", "NONE"
    alpha_trend: float      # mevcut AlphaTrend değeri
    alpha_trend_2: float    # 2 bar önceki AlphaTrend
    adx: float
    adx_threshold_dyn: float
    rsi: float
    mfi: float
    atr: float
    price: float
    # Filtre detayları
    adx_static_ok: bool
    adx_dynamic_ok: bool
    slope_ok: bool
    final_filter: bool
    # Trend rengi
    trend_color: str        # "green", "red"
    trend_direction: int    # +1, -1, 0
    eligible: bool
    reject_reason: str


@dataclass
class SystemNScanResult:
    """GUI ve buying kararı için kullanılan scan sonucu."""
    symbol: str
    signal: str             # "BUY", "SELL", "NONE"
    direction: str          # "LONG", "SHORT", ""
    price: float
    alpha_trend: float
    alpha_trend_2: float
    adx: float
    rsi: float
    mfi: float
    atr: float
    trend_color: str
    trend_direction: int
    eligible: bool
    reject_reason: str
    # Filtre detayları
    adx_static_ok: bool
    adx_dynamic_ok: bool
    slope_ok: bool
    final_filter: bool


# ═══════════════════════════════════════════════════════════════════
#  Helper: Indicator Calculations (NumPy vectorized)
# ═══════════════════════════════════════════════════════════════════

def _sma(data: np.ndarray, period: int) -> np.ndarray:
    """Simple Moving Average."""
    out = np.full_like(data, np.nan)
    if len(data) < period:
        return out
    cumsum = np.cumsum(data)
    cumsum[period:] = cumsum[period:] - cumsum[:-period]
    out[period - 1:] = cumsum[period - 1:] / period
    return out


def _rma(data: np.ndarray, period: int) -> np.ndarray:
    """Wilder's smoothing (RMA) — same as TradingView ta.rma."""
    out = np.full_like(data, np.nan, dtype=float)
    if len(data) < period:
        return out
    # Seed with SMA
    out[period - 1] = np.mean(data[:period])
    alpha = 1.0 / period
    for i in range(period, len(data)):
        out[i] = alpha * data[i] + (1 - alpha) * out[i - 1]
    return out


def _true_range(high: np.ndarray, low: np.ndarray, close: np.ndarray) -> np.ndarray:
    """True Range."""
    tr = np.empty(len(high), dtype=float)
    tr[0] = high[0] - low[0]
    for i in range(1, len(high)):
        tr[i] = max(high[i] - low[i],
                     abs(high[i] - close[i - 1]),
                     abs(low[i] - close[i - 1]))
    return tr


def _compute_rsi(close: np.ndarray, period: int) -> np.ndarray:
    """RSI calculation matching TradingView."""
    delta = np.concatenate([[0.0], np.diff(close)])
    gain = np.where(delta > 0, delta, 0.0)
    loss = np.where(delta < 0, -delta, 0.0)
    avg_gain = _rma(gain, period)
    avg_loss = _rma(loss, period)
    rs = np.where(avg_loss > 0, avg_gain / avg_loss, 100.0)
    rsi = 100.0 - 100.0 / (1.0 + rs)
    return rsi


def _compute_mfi(high: np.ndarray, low: np.ndarray, close: np.ndarray,
                 volume: np.ndarray, period: int) -> np.ndarray:
    """Money Flow Index — TradingView compatible."""
    typical = (high + low + close) / 3.0
    raw_mf = typical * volume
    n = len(close)
    mfi = np.full(n, 50.0)
    if n < period + 1:
        return mfi
    for i in range(period, n):
        pos_flow = 0.0
        neg_flow = 0.0
        for j in range(1, period + 1):
            idx = i - period + j
            if typical[idx] > typical[idx - 1]:
                pos_flow += raw_mf[idx]
            elif typical[idx] < typical[idx - 1]:
                neg_flow += raw_mf[idx]
        if neg_flow > 0:
            ratio = pos_flow / neg_flow
            mfi[i] = 100.0 - 100.0 / (1.0 + ratio)
        else:
            mfi[i] = 100.0
    return mfi


def _compute_adx(high: np.ndarray, low: np.ndarray, close: np.ndarray,
                 period: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """ADX, +DI, -DI calculation matching TradingView manual ADX."""
    n = len(high)
    plus_dm = np.zeros(n)
    minus_dm = np.zeros(n)
    tr = _true_range(high, low, close)

    for i in range(1, n):
        up = high[i] - high[i - 1]
        down = low[i - 1] - low[i]
        plus_dm[i] = up if (up > down and up > 0) else 0.0
        minus_dm[i] = down if (down > up and down > 0) else 0.0

    smoothed_tr = _rma(tr, period)
    smoothed_plus = _rma(plus_dm, period)
    smoothed_minus = _rma(minus_dm, period)

    pdi = np.where(smoothed_tr > 0, 100.0 * smoothed_plus / smoothed_tr, 0.0)
    mdi = np.where(smoothed_tr > 0, 100.0 * smoothed_minus / smoothed_tr, 0.0)

    dx_sum = pdi + mdi
    dx = np.where(dx_sum > 0, 100.0 * np.abs(pdi - mdi) / dx_sum, 0.0)

    adx = _rma(dx, period)
    return adx, pdi, mdi


# ═══════════════════════════════════════════════════════════════════
#  AlphaTrend Core Calculation
# ═══════════════════════════════════════════════════════════════════

def compute_alpha_trend(high: np.ndarray, low: np.ndarray, close: np.ndarray,
                        volume: np.ndarray,
                        coeff: float, period: int,
                        use_mfi: bool = True) -> tuple[np.ndarray, np.ndarray]:
    """AlphaTrend hesaplama — TradingView Pine Script'ten birebir çeviri.

    Returns: (AlphaTrend array, ATR array) — aynı uzunlukta.
    """
    n = len(close)
    tr = _true_range(high, low, close)
    atr = np.full(n, np.nan)
    # SMA-based ATR (TradingView ta.sma(ta.tr, AP))
    sma_atr = _sma(tr, period)
    atr = sma_atr

    # upT ve downT
    up_t = low - atr * coeff
    down_t = high + atr * coeff

    # Trend koşulu
    if use_mfi:
        trend_val = _compute_mfi(high, low, close, volume, period)
    else:
        trend_val = _compute_rsi(close, period)

    alpha_trend = np.full(n, np.nan)

    # İlk geçerli indeks (ATR'nin NaN olmadığı yer)
    start_idx = period - 1
    if np.isnan(atr[start_idx]):
        start_idx = period
    if start_idx >= n:
        return alpha_trend, atr

    alpha_trend[start_idx] = close[start_idx]  # seed

    for i in range(start_idx + 1, n):
        if np.isnan(atr[i]):
            alpha_trend[i] = alpha_trend[i - 1]
            continue

        prev = alpha_trend[i - 1]
        if trend_val[i] >= 50:
            # Bullish: upT, ama öncekinin altına düşemez
            val = up_t[i]
            alpha_trend[i] = max(val, prev) if not np.isnan(val) else prev
        else:
            # Bearish: downT, ama öncekinin üstüne çıkamaz
            val = down_t[i]
            alpha_trend[i] = min(val, prev) if not np.isnan(val) else prev

    return alpha_trend, atr


# ═══════════════════════════════════════════════════════════════════
#  System N Scanner
# ═══════════════════════════════════════════════════════════════════

class SystemNScanner:
    """AlphaTrend PRO v2 + G-bazlı dinamik kaldıraç tarama sistemi.

    Her coin için backtest optimize dosyasından (data/system_n_optimize.json)
    en iyi coeff/period/TF parametrelerini okur. G dalga analizinden
    maksimum güvenli kaldıracı hesaplar.
    """

    OPTIMIZE_FILE = "data/system_n_optimize.json"
    # Kaldıraç formülü sabitleri
    SL_G_MULT = 1.5
    FEE_TOTAL = 0.12        # round-trip: 2×%0.04 taker + 2×%0.02 slippage
    SL_DIVISOR = 2.0
    DEFAULT_MAINT_RATE = 0.004  # %0.4

    def __init__(self, config: ConfigManager):
        self._config = config
        self._lock = threading.RLock()
        # Per-coin state: son trend yönü (sinyal tekrarını önlemek için)
        self._coin_trend_direction: dict[str, int] = {}
        # Optimize cache: {symbol: {coeff, period, tf, G, max_leverage, ...}}
        self._optimize_cache: dict[str, dict] = {}
        self._optimize_loaded_at: float = 0.0
        # İlk yükleme
        self._load_optimize_cache()

    def _cfg(self, key: str, default=None):
        """Config erişimi: system_n.key"""
        return self._config.get(f"system_n.{key}", default)

    # ═══ Optimize Cache ═══

    def _load_optimize_cache(self) -> int:
        """data/system_n_optimize.json dosyasını oku ve cache'e yükle.
        Returns: yüklenen coin sayısı.
        """
        path = self.OPTIMIZE_FILE
        if not os.path.exists(path):
            logger.warning(f"[SysN] Optimize dosyası bulunamadı: {path}")
            return 0

        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            logger.error(f"[SysN] Optimize dosyası okunamadı: {e}")
            return 0

        results = data.get("results", {})
        loaded = 0

        for symbol, info in results.items():
            optimal_tf = info.get("optimal_tf", "5m")
            G = info.get("G", 0)
            max_lev = info.get("max_leverage", 1)
            regime = info.get("regime", "")

            # Bu coin + optimal TF için parametreler
            params = info.get("params", {}).get(optimal_tf, {})
            coeff = params.get("coeff", 0)
            period = params.get("period", 0)
            pnl = params.get("total_pnl_pct", 0)
            pf = params.get("profit_factor", 0)
            wr = params.get("win_rate", 0)

            # G analiz verisi
            g_data = info.get("g_analysis", {}).get(optimal_tf, {})

            if coeff > 0 and period > 0:
                if pnl <= 0:
                    logger.info(f"[SysN] {symbol}: backtest kârsız (PnL={pnl:.2f}%), "
                                f"cache'e ALINMIyor — canlıda kullanılmayacak")
                    continue
                self._optimize_cache[symbol] = {
                    "coeff": coeff,
                    "period": period,
                    "tf": optimal_tf,
                    "G": G,
                    "max_leverage": max_lev,
                    "regime": regime,
                    "pnl": pnl,
                    "pf": pf,
                    "wr": wr,
                    "sl_pct": g_data.get("sl_pct", 0),
                }
                loaded += 1

        self._optimize_loaded_at = _time.time()
        ts = data.get("timestamp", "?")
        logger.info(f"[SysN] Optimize cache yüklendi: {loaded}/{len(results)} coin "
                    f"(kaynak: {ts})")
        return loaded

    def reload_if_stale(self, max_age_hours: float = 24.0) -> None:
        """Cache eski ise yeniden yükle + artık olmayan coinlerin state'ini temizle."""
        age = _time.time() - self._optimize_loaded_at
        if age > max_age_hours * 3600:
            logger.info(f"[SysN] Optimize cache {age/3600:.1f}h eski — yeniden yükleniyor")
            old_symbols = set(self._optimize_cache.keys())
            self._load_optimize_cache()
            new_symbols = set(self._optimize_cache.keys())
            # Artık cache'de olmayan coinlerin trend state'ini temizle
            removed = old_symbols - new_symbols
            if removed:
                with self._lock:
                    for sym in removed:
                        self._coin_trend_direction.pop(sym, None)
                logger.info(f"[SysN] {len(removed)} eski coin state temizlendi")

    def get_coin_params(self, symbol: str) -> dict:
        """Coin için optimize parametreleri döndür.
        Cache'de yoksa config varsayılanlarını kullanır.
        """
        cached = self._optimize_cache.get(symbol)
        if cached:
            return cached

        # Fallback: config'den varsayılan
        cfg = self._config.get("system_n", {})
        ind = cfg.get("indicators", {})
        return {
            "coeff": ind.get("coeff", 3.6),
            "period": ind.get("period", 27),
            "tf": cfg.get("timeframe", "5m"),
            "G": 0,
            "max_leverage": cfg.get("leverage", 1),
            "regime": "",
            "pnl": 0,
            "pf": 0,
            "wr": 0,
            "sl_pct": 0,
        }

    def get_optimized_symbols(self) -> list[str]:
        """Optimize cache'deki tüm coinleri döndür (kârlı olanlar)."""
        return list(self._optimize_cache.keys())

    def calc_leverage_from_g(self, G: float, maint_rate: float = 0.0) -> int:
        """G dalga boyundan maksimum güvenli kaldıraç hesapla.

        Formül: SL% = G × 1.5 + 0.12%
                Liq_dist = SL% × 2.0
                Teorik_liq = Liq_dist + maint_margin%
                Leverage = floor(100 / Teorik_liq)

        G birimi: yüzde (ör: 1.5 = %1.5). Oran olarak gelirse (< 0.1) otomatik düzeltilir.
        """
        if G <= 0:
            return 1
        # Absürt büyük G değerlerini reddet (>%50 dalga anlamsız)
        if G > 50.0:
            logger.warning(f"[SysN] G={G:.2f}% absürt büyük — kaldıraç 1x")
            return 1
        # Çok küçük G muhtemelen birim hatası değil, düşük volatilite coini.
        # Otomatik ×100 dönüşümü yapmıyoruz — yanlış pozitif riski yüksek.
        # G < 0.1% → SL < 0.27% → çok sıkı, kaldıraç çok yüksek çıkar → güvenlik sınırı
        if G < 0.15:
            logger.warning(f"[SysN] G={G:.4f}% çok küçük (düşük volatilite?) — kaldıraç 1x")
            return 1
        if maint_rate <= 0:
            maint_rate = self.DEFAULT_MAINT_RATE

        sl_pct = G * self.SL_G_MULT + self.FEE_TOTAL
        liq_dist = sl_pct * self.SL_DIVISOR
        teorik_liq = liq_dist + maint_rate * 100.0
        if teorik_liq <= 0:
            return 1

        max_lev = int(100.0 / teorik_liq)
        max_cfg = self._cfg("max_leverage", 125)
        return max(1, min(max_lev, max_cfg))

    def analyze_symbol(self, symbol: str, klines: list,
                       volume_data: bool = True) -> SystemNScanResult:
        """Tek bir coin için AlphaTrend analizi yap ve sinyal üret.

        Args:
            symbol: Coin sembolü (ör: BTCUSDT)
            klines: Binance kline listesi [[open_time, open, high, low, close, volume, ...], ...]
            volume_data: True ise MFI kullan, False ise RSI kullan

        Returns:
            SystemNScanResult
        """
        cfg = self._config.get("system_n", {})
        indicator_cfg = cfg.get("indicators", {})

        # Coin başına optimize parametreleri (cache'den)
        coin_params = self.get_coin_params(symbol)
        coeff = coin_params["coeff"]
        period = coin_params["period"]
        adx_length = indicator_cfg.get("adx_length", 14)
        adx_threshold = indicator_cfg.get("adx_threshold", 18.0)
        use_adx_static = indicator_cfg.get("use_adx_static", True)
        use_adx_dynamic = indicator_cfg.get("use_adx_dynamic", True)
        adx_dyn_mult = indicator_cfg.get("adx_dyn_mult", 1.0)
        use_slope = indicator_cfg.get("use_slope", False)
        slope_factor = indicator_cfg.get("slope_factor", 0.1)
        use_mfi = indicator_cfg.get("use_mfi", True) and volume_data

        # OHLCV arrays — minimum 100 mum (warmup güvenlik marjı)
        min_bars = max(period * 3, adx_length * 3, 100)
        if not klines or len(klines) < min_bars:
            return self._empty_result(symbol, "insufficient_data")

        try:
            opens = np.array([float(k[1]) for k in klines], dtype=float)
            highs = np.array([float(k[2]) for k in klines], dtype=float)
            lows = np.array([float(k[3]) for k in klines], dtype=float)
            closes = np.array([float(k[4]) for k in klines], dtype=float)
            volumes = np.array([float(k[5]) for k in klines], dtype=float)
        except (IndexError, ValueError) as e:
            return self._empty_result(symbol, f"parse_error: {e}")

        n = len(closes)
        price = closes[-1]

        # ── AlphaTrend hesapla (ATR de birlikte döner) ──
        alpha_trend, atr_arr = compute_alpha_trend(
            highs, lows, closes, volumes,
            coeff=coeff, period=period, use_mfi=use_mfi,
        )

        # Son 4 değer gerekli (AT[0], AT[1], AT[2], AT[3] — en son → en eski)
        # NaN varsa sinyal üretme — eksik veriyle yanlış sinyal riski
        at_now = alpha_trend[-1]
        at_1 = alpha_trend[-2] if n >= 2 else np.nan
        at_2 = alpha_trend[-3] if n >= 3 else np.nan
        at_3 = alpha_trend[-4] if n >= 4 else np.nan

        if any(np.isnan(v) for v in [at_now, at_1, at_2, at_3]):
            return self._empty_result(symbol, "alpha_trend_warmup")

        # ── ADX hesapla ──
        adx_arr, pdi_arr, mdi_arr = _compute_adx(highs, lows, closes, adx_length)
        adx_val = adx_arr[-1] if not np.isnan(adx_arr[-1]) else 0.0

        # ── RSI hesapla ──
        rsi_length = indicator_cfg.get("rsi_length", 14)
        rsi_arr = _compute_rsi(closes, rsi_length)
        rsi_val = rsi_arr[-1] if not np.isnan(rsi_arr[-1]) else 50.0

        # ── MFI hesapla ──
        mfi_val = 50.0
        if use_mfi:
            mfi_arr = _compute_mfi(highs, lows, closes, volumes, period)
            mfi_val = mfi_arr[-1] if not np.isnan(mfi_arr[-1]) else 50.0

        # ── ATR (compute_alpha_trend'den dönen değer) ──
        atr_val = atr_arr[-1] if not np.isnan(atr_arr[-1]) else 0.0

        # ═══ FİLTRELER ═══

        # 1. Statik ADX filtresi
        adx_static_ok = adx_val > adx_threshold if use_adx_static else True

        # 2. Dinamik ADX filtresi
        adx_sma = _sma(adx_arr, adx_length)
        adx_dyn_thresh = adx_sma[-1] * adx_dyn_mult if not np.isnan(adx_sma[-1]) else 0.0
        adx_dynamic_ok = adx_val > adx_dyn_thresh if use_adx_dynamic else True

        # 3. Slope filtresi
        slope = abs(at_now - at_1)
        min_slope = atr_val * slope_factor if atr_val > 0 else 0.0
        slope_ok = slope > min_slope if use_slope else True

        final_filter = adx_static_ok and adx_dynamic_ok and slope_ok

        # ═══ SİNYAL TESPİTİ ═══

        # Crossover/Crossunder: AlphaTrend vs AlphaTrend[2]
        # TV: ta.crossover(AlphaTrend, AlphaTrend[2])
        # at_now > at_2 AND at_1 <= at_3
        buy_cross = (at_now > at_2) and (at_1 <= at_3)
        sell_cross = (at_now < at_2) and (at_1 >= at_3)

        buy_filtered = buy_cross and final_filter
        sell_filtered = sell_cross and final_filter

        # ═══ STATE MACHINE (tekrar önleme) ═══

        # Crossover yaşı kontrolü: momentum hâlâ taze mi?
        # AT ile AT[2] arasındaki fark büyüyorsa momentum devam ediyor.
        # Fark kapanıyorsa crossover eski — sinyal üretme.
        cross_momentum_alive = True
        if buy_cross or sell_cross:
            delta_now = abs(at_now - at_2)
            delta_prev = abs(at_1 - at_3)
            # Momentum zayıflıyorsa (fark %30+ daraldıysa) → eski crossover
            if delta_prev > 0 and delta_now < delta_prev * 0.7:
                cross_momentum_alive = False

        with self._lock:
            prev_direction = self._coin_trend_direction.get(symbol, 0)

            new_direction = prev_direction
            if buy_filtered:
                new_direction = 1
            elif sell_filtered:
                new_direction = -1

            # Sinyal sadece yön DEĞİŞİNCE üretilir
            if prev_direction == 0:
                # İlk kez görülen coin: crossover taze ise sinyal ver
                # (eski/zayıflayan crossover'da sinyal üretme — geç giriş riski)
                plot_buy = buy_filtered and cross_momentum_alive
                plot_sell = sell_filtered and not buy_filtered and cross_momentum_alive
            else:
                plot_buy = buy_filtered and prev_direction != 1
                plot_sell = sell_filtered and prev_direction != -1

            self._coin_trend_direction[symbol] = new_direction

        # Trend rengi (TradingView color1 mantığı)
        if at_now > at_2:
            trend_color = "green"
        elif at_now < at_2:
            trend_color = "red"
        elif at_1 > at_3:
            trend_color = "green"
        else:
            trend_color = "red"

        # Sinyal belirleme
        signal = "NONE"
        direction = ""
        if plot_buy:
            signal = "BUY"
            direction = "LONG"
        elif plot_sell:
            signal = "SELL"
            direction = "SHORT"

        eligible = signal != "NONE"
        reject_reason = "" if eligible else "no_signal"

        return SystemNScanResult(
            symbol=symbol,
            signal=signal,
            direction=direction,
            price=price,
            alpha_trend=at_now,
            alpha_trend_2=at_2,
            adx=adx_val,
            rsi=rsi_val,
            mfi=mfi_val,
            atr=atr_val,
            trend_color=trend_color,
            trend_direction=new_direction,
            eligible=eligible,
            reject_reason=reject_reason,
            adx_static_ok=adx_static_ok,
            adx_dynamic_ok=adx_dynamic_ok,
            slope_ok=slope_ok,
            final_filter=final_filter,
        )

    def reconstruct_state_from_positions(self, positions: dict) -> int:
        """Açık SYSTEM_N pozisyonlarından trend yönü state'ini reconstruct et.

        Startup'ta çağrılır — restart sonrası duplicate trade'i önler.
        Args:
            positions: {symbol: ActivePosition} dict'i
        Returns:
            Reconstruct edilen coin sayısı.
        """
        count = 0
        with self._lock:
            for symbol, pos in positions.items():
                if getattr(pos, "entry_mode", "") != "SYSTEM_N":
                    continue
                from core.constants import OrderSide
                if pos.side == OrderSide.BUY_LONG:
                    self._coin_trend_direction[symbol] = 1
                elif pos.side == OrderSide.SELL_SHORT:
                    self._coin_trend_direction[symbol] = -1
                count += 1
        if count > 0:
            logger.info(f"[SysN] Reconstructed state for {count} coins from open positions")
        return count

    def reset_state(self, symbol: str = None) -> None:
        """Coin state'ini sıfırla. symbol=None ise tümünü sıfırla."""
        with self._lock:
            if symbol:
                self._coin_trend_direction.pop(symbol, None)
            else:
                self._coin_trend_direction.clear()

    def calculate_position_size(self, wallet: float, leverage: int = 1,
                               coin_min_notional: float = 0) -> float:
        """Pozisyon büyüklüğü (margin) hesapla.

        İki mod:
          divider:      wallet / portfolio_divider (klasik 1/12)
          min_notional: max(config, coin_bazlı) × (1 + buffer%) / leverage
                        → cebinden çıkan minimum marjin

        Args:
            wallet: toplam bakiye (available + locked)
            leverage: kaldıraç çarpanı
            coin_min_notional: Binance'in bu coin için min notional değeri (0=config kullan)

        Returns: margin_usdt (kaldıraç ÖNCESİ, cebinden çıkan tutar)
        """
        mode = self._cfg("position.sizing_mode", "divider")

        if mode == "min_notional":
            base_notional = self._cfg("position.min_notional_usd", 5.0)
            # Coin bazlı min notional daha yüksekse onu kullan
            if coin_min_notional > base_notional:
                base_notional = coin_min_notional
            buffer_pct = self._cfg("position.min_notional_buffer_pct", 20)
            target_notional = base_notional * (1 + buffer_pct / 100.0)
            margin = target_notional / max(leverage, 1)
            return margin

        # Klasik divider modu
        divider = self._cfg("position.portfolio_divider", 12)
        size = wallet / divider
        min_pos = self._cfg("position.min_position_usd", 1.0)
        return max(size, min_pos)

    def _empty_result(self, symbol: str, reason: str) -> SystemNScanResult:
        """Boş/geçersiz sonuç üret."""
        return SystemNScanResult(
            symbol=symbol, signal="NONE", direction="",
            price=0.0, alpha_trend=0.0, alpha_trend_2=0.0,
            adx=0.0, rsi=50.0, mfi=50.0, atr=0.0,
            trend_color="red", trend_direction=0,
            eligible=False, reject_reason=reason,
            adx_static_ok=False, adx_dynamic_ok=False,
            slope_ok=False, final_filter=False,
        )
