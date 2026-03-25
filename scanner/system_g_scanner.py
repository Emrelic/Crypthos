"""System G Scanner v1.0 — Per-Coin Optimized Trading.

Phase 1: 3-TF alignment signal detection (reused from System F)
Phase 2: Per-coin mini-backtest optimization (leverage, TP, SL)
Phase 3: Cached results + async optimization

Key difference from all other systems:
  Instead of fixed formulas for leverage/SL/TP, System G runs a fast
  mini-backtest on each coin's recent data to find the empirically
  optimal parameters before entering a position.
"""
import time
import numpy as np
from concurrent.futures import ThreadPoolExecutor, Future
from dataclasses import dataclass, field
from loguru import logger
from core.config_manager import ConfigManager
from backtest.indicators import (
    ema_val, ema_series, macd_line_series, rsi_val, atr_val, adx_val,
)

# ─────────────────────────── Constants ───────────────────────────

_TF_MINUTES = {
    "1m": 1, "3m": 3, "5m": 5, "15m": 15, "30m": 30,
    "1h": 60, "2h": 120, "4h": 240, "8h": 480, "12h": 720, "1d": 1440,
}


# ─────────────────────────── Data Classes ───────────────────────────

@dataclass
class TFSignalG:
    timeframe: str = ""
    ema_vote: float = 0.0
    macd_vote: float = 0.0
    rsi_vote: float = 0.0
    strict_direction: str = "FLAT"
    confidence: float = 0.0
    adx_value: float = 0.0
    volume_ratio: float = 0.0
    ema_gap_pct: float = 0.0


@dataclass
class OptCombo:
    """One parameter combination to test."""
    leverage: int = 75
    tp_pct: float = 1.0      # fiyat % (TP mesafesi)
    sl_pct: float = 0.0      # 0 = no SL (liq only)
    sl_mode: str = "no_sl"   # "no_sl" or "fixed"


@dataclass
class OptResult:
    """Result of testing one combo on one coin."""
    combo: OptCombo = field(default_factory=OptCombo)
    total_roi: float = 0.0
    win_rate: float = 0.0
    max_drawdown: float = 0.0
    liq_rate: float = 0.0
    trade_count: int = 0
    avg_hold_bars: float = 0.0
    score: float = 0.0


@dataclass
class CoinOptCache:
    """Cached optimization result for a coin."""
    symbol: str = ""
    direction: str = ""
    best: OptResult = field(default_factory=OptResult)
    top5: list = field(default_factory=list)
    timestamp: float = 0.0
    regime_hash: str = ""
    valid: bool = False


@dataclass
class SystemGScanResult:
    """Full scan result for one coin."""
    symbol: str = ""
    rank: int = 0
    volume_24h: float = 0.0

    # TF signals
    tf_signals: list = field(default_factory=list)
    aligned_count: int = 0
    total_tfs: int = 0

    # Direction
    direction: str = ""
    direction_strength: float = 0.0

    # Signal filters
    funding_rate: float = 0.0
    btc_aligned: bool = False
    vol_filter_pass: bool = False

    # Optimization
    opt_status: str = "NONE"  # NONE/PENDING/CACHED/FRESH/SKIP/FAILED
    opt_result: OptResult = field(default_factory=OptResult)
    opt_cache: CoinOptCache = field(default_factory=CoinOptCache)

    # Derived from optimization
    smart_leverage: int = 1
    sl_pct: float = 0.0
    tp_pct: float = 0.0
    sl_mode: str = "no_sl"
    backtest_roi: float = 0.0
    backtest_wr: float = 0.0
    backtest_liq_rate: float = 0.0

    # Price / ATR
    price: float = 0.0
    atr_pct: float = 0.0
    entry_price: float = 0.0

    # Decision
    eligible: bool = False
    reject_reason: str = ""
    composite_score: float = 0.0


# ─────────────────────────── Core Scanner ───────────────────────────

class SystemGScanner:
    """System G: per-coin optimization before entry."""

    def __init__(self, config: ConfigManager):
        self._config = config
        self._opt_cache: dict[str, CoinOptCache] = {}
        self._opt_futures: dict[str, Future] = {}
        self._executor = ThreadPoolExecutor(max_workers=2)

    # ══════════════════════ Phase 1: Signal Detection ══════════════════════

    def analyze_signal(self, symbol: str,
                       klines_by_tf: dict[str, list],
                       funding_rate: float = 0.0,
                       rank: int = 0, volume_24h: float = 0.0,
                       ob_data: dict = None,
                       btc_beta: float = 0.0,
                       btc_direction: str = "FLAT",
                       ) -> SystemGScanResult:
        """Phase 1: Check if coin has 3/3 TF alignment signal."""
        sg = self._config.get("system_g", {})
        result = SystemGScanResult(symbol=symbol, rank=rank,
                                    volume_24h=volume_24h)
        result.funding_rate = funding_rate

        # TF config
        cfg_tfs = sg.get("direction_tfs", ["5m", "1h", "4h"])
        direction_tfs = [(tf, _TF_MINUTES.get(tf, 5)) for tf in cfg_tfs]

        # Analyze each TF
        tf_signals = []
        for tf_name, _tf_min in direction_tfs:
            klines = klines_by_tf.get(tf_name, [])
            if not klines or len(klines) < 30:
                continue
            sig = self._analyze_tf(klines, tf_name, sg)
            tf_signals.append(sig)

        result.tf_signals = tf_signals
        result.total_tfs = len(tf_signals)

        required = sg.get("min_tf_uyum", 3)
        if result.total_tfs < required:
            result.reject_reason = f"tf_data_{result.total_tfs}/{required}"
            return result

        # Direction alignment
        long_c = sum(1 for s in tf_signals if s.strict_direction == "LONG")
        short_c = sum(1 for s in tf_signals if s.strict_direction == "SHORT")
        flat_c = sum(1 for s in tf_signals if s.strict_direction == "FLAT")

        if long_c >= required and short_c == 0:
            result.direction = "LONG"
            result.aligned_count = long_c
        elif short_c >= required and long_c == 0:
            result.direction = "SHORT"
            result.aligned_count = short_c
        else:
            result.reject_reason = f"align_{long_c}L_{short_c}S_{flat_c}F"
            return result

        # Direction strength
        aligned = [s for s in tf_signals if s.strict_direction == result.direction]
        result.direction_strength = sum(s.confidence for s in aligned) / len(aligned)
        if result.direction_strength < sg.get("min_sinyal_gucu", 0.6):
            result.reject_reason = f"weak_{result.direction_strength:.2f}"
            return result

        # Volume filter
        vol_min = sg.get("vol_tf_min_count", 2)
        vol_thresh = sg.get("vol_tf_threshold", 1.5)
        vol_passing = sum(1 for s in tf_signals if s.volume_ratio >= vol_thresh)
        result.vol_filter_pass = vol_passing >= vol_min
        if not result.vol_filter_pass:
            result.reject_reason = f"vol_{vol_passing}/{vol_min}"
            return result

        # Funding rate
        max_fr = sg.get("max_funding_rate", 0.001)
        if funding_rate > max_fr and result.direction == "LONG":
            result.reject_reason = "high_fr_long"
            return result
        if funding_rate < -max_fr and result.direction == "SHORT":
            result.reject_reason = "high_fr_short"
            return result

        # BTC alignment
        btc_thresh = sg.get("btc_beta_threshold", 0.5)
        if abs(btc_beta) > btc_thresh and btc_direction not in ("FLAT", ""):
            if result.direction != btc_direction:
                result.reject_reason = f"btc_{btc_direction}"
                return result
        result.btc_aligned = True

        # Price & ATR from 5m
        kl5 = klines_by_tf.get("5m", [])
        if kl5 and len(kl5) >= 30:
            closes = np.array([float(k[4]) for k in kl5])
            highs = np.array([float(k[2]) for k in kl5])
            lows = np.array([float(k[3]) for k in kl5])
            result.price = float(closes[-1])
            result.entry_price = result.price
            atr = atr_val(highs, lows, closes, 14)
            result.atr_pct = (atr / result.price * 100) if result.price > 0 else 0

        # Orderbook
        if ob_data:
            if ob_data.get("thin_book", False):
                result.reject_reason = "thin_book"
                return result
            spread = ob_data.get("spread_pct", 0.0)
            if spread > sg.get("spread_max_pct", 0.05) > 0:
                result.reject_reason = f"spread_{spread:.3f}%"
                return result

        # Signal passed! Mark eligible for optimization
        result.eligible = True
        return result

    def score_batch(self, symbols: list[str],
                    all_klines: dict[str, dict[str, list]],
                    market_ctx: dict = None,
                    volume_map: dict = None,
                    ob_map: dict = None,
                    beta_map: dict = None,
                    btc_direction: str = "FLAT",
                    ) -> list[SystemGScanResult]:
        """Batch scan all symbols."""
        results = []
        market_ctx = market_ctx or {}
        volume_map = volume_map or {}
        ob_map = ob_map or {}
        beta_map = beta_map or {}

        for i, sym in enumerate(symbols):
            klines = all_klines.get(sym, {})
            if not klines:
                continue
            fr = market_ctx.get(sym, {}).get("funding_rate", 0.0)
            r = self.analyze_signal(
                sym, klines, funding_rate=fr, rank=i + 1,
                volume_24h=volume_map.get(sym, 0.0),
                ob_data=ob_map.get(sym),
                btc_beta=beta_map.get(sym, 0.8),
                btc_direction=btc_direction,
            )
            results.append(r)

        results.sort(key=lambda r: (not r.eligible, -r.direction_strength))
        return results

    # ══════════════════════ Phase 2: Per-Coin Optimization ══════════════════════

    def optimize_coin(self, symbol: str, direction: str,
                      klines_5m: list, sg: dict = None) -> OptResult | None:
        """Run mini-backtest matrix for one coin. Can be called async."""
        if sg is None:
            sg = self._config.get("system_g", {})

        opt_cfg = sg.get("optimization", {})
        fee_rate = sg.get("fee_rate", 0.0004)

        # Build parameter grid
        leverages = opt_cfg.get("leverages", [25, 50, 75, 100, 125, 150])
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

        # Pre-compute entry points using simplified signal on 5m data
        entries = self._find_entry_points(klines_5m, direction)
        if len(entries) < min_trades:
            logger.info(f"[SysG] {symbol}: only {len(entries)} entries, need {min_trades}")
            return None

        # Test all combos
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

                    # Filter bad combos
                    if res.trade_count < min_trades:
                        continue
                    if res.liq_rate > max_liq:
                        continue

                    # Score
                    res.score = self._score_combo(res, weights)
                    all_results.append(res)

                    if best_result is None or res.score > best_result.score:
                        best_result = res

        if not best_result:
            return None

        # Sort all results for top5
        all_results.sort(key=lambda r: -r.score)

        # Cache
        cache = CoinOptCache(
            symbol=symbol, direction=direction,
            best=best_result,
            top5=all_results[:5],
            timestamp=time.time(),
            valid=True,
        )
        self._opt_cache[symbol] = cache

        logger.info(f"[SysG] {symbol} optimized: {best_result.combo.leverage}x "
                    f"TP={best_result.combo.tp_pct}% "
                    f"SL={'yok' if best_result.combo.sl_pct == 0 else str(best_result.combo.sl_pct) + '%'} "
                    f"ROI={best_result.total_roi:+.1f}% WR={best_result.win_rate:.0f}% "
                    f"LIQ={best_result.liq_rate*100:.0f}% "
                    f"({best_result.trade_count} trades, score={best_result.score:.1f})")

        return best_result

    def submit_optimization(self, symbol: str, direction: str,
                            klines_5m: list) -> None:
        """Submit async optimization. Non-blocking."""
        if symbol in self._opt_futures:
            future = self._opt_futures[symbol]
            if not future.done():
                return  # already running

        sg = self._config.get("system_g", {})
        future = self._executor.submit(
            self.optimize_coin, symbol, direction, klines_5m, sg)
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
            logger.error(f"[SysG] Optimization failed for {symbol}: {e}")
            del self._opt_futures[symbol]
            return None

    def get_cached(self, symbol: str) -> CoinOptCache | None:
        """Get cached optimization if valid."""
        cache = self._opt_cache.get(symbol)
        if not cache or not cache.valid:
            return None
        ttl = self._config.get("system_g.optimization.cache_ttl_hours", 4) * 3600
        if time.time() - cache.timestamp > ttl:
            cache.valid = False
            return None
        return cache

    # ══════════════════════ Internal Methods ══════════════════════

    def _find_entry_points(self, klines_5m: list, direction: str) -> list:
        """Find simplified entry points in 5m data matching direction.

        Uses EMA crossover + RSI + MACD on 5m only (simplified).
        Returns list of candle indices where entry would occur.
        """
        closes = np.array([float(k[4]) for k in klines_5m])
        n = len(closes)
        if n < 50:
            return []

        sg = self._config.get("system_g", {})

        # EMA
        ema_f = sg.get("ema_fast", 9)
        ema_s = sg.get("ema_slow", 21)
        ema9 = ema_series(closes, ema_f)
        ema21 = ema_series(closes, ema_s)

        # MACD
        ml = macd_line_series(closes, sg.get("macd_fast", 8), sg.get("macd_slow", 17))
        sig_line = ema_series(ml, sg.get("macd_signal", 9))
        hist = ml - sig_line

        # RSI
        from backtest.tf_heatmap import _rsi_series
        rsi_arr = _rsi_series(closes, sg.get("rsi_periyot", 14))

        gap_min = sg.get("ema_gap_min_pct", 0.05) / 100.0
        rsi_long = sg.get("rsi_long_esik", 60)
        rsi_short = sg.get("rsi_short_esik", 40)

        entries = []
        min_gap = 6  # min 30dk (6 bars) between entries

        for i in range(30, n):
            price = closes[i]
            if price <= 0:
                continue

            # EMA vote
            gap = (ema9[i] - ema21[i]) / price
            if direction == "LONG" and gap <= gap_min:
                continue
            if direction == "SHORT" and gap >= -gap_min:
                continue

            # MACD vote
            if i < 3:
                continue
            h1, h2, h3 = float(hist[i - 2]), float(hist[i - 1]), float(hist[i])
            if direction == "LONG" and not (h3 > 0 and h1 < h2 < h3):
                continue
            if direction == "SHORT" and not (h3 < 0 and h1 > h2 > h3):
                continue

            # RSI vote
            if direction == "LONG" and rsi_arr[i] <= rsi_long:
                continue
            if direction == "SHORT" and rsi_arr[i] >= rsi_short:
                continue

            # Min gap between entries
            if entries and (i - entries[-1]) < min_gap:
                continue

            entries.append(i)

        return entries

    def _simulate_combo(self, entries: list, klines_5m: list,
                        combo: OptCombo, direction: str,
                        fee_rate: float) -> OptResult:
        """Simulate one combo across all entry points (one position at a time)."""
        fee_roi = fee_rate * 200 * combo.leverage
        liq_pct = (1.0 / combo.leverage) * 70  # practical liq at 70%
        max_bars = 288  # 24h

        trades_roi = []
        trades_bars = []
        liq_count = 0
        in_position = False
        position_end_idx = 0

        for entry_idx in entries:
            if in_position and entry_idx < position_end_idx:
                continue

            in_position = False
            entry_price = float(klines_5m[entry_idx][4])
            forward = klines_5m[entry_idx + 1:]

            result, bars, roi = self._sim_one_trade(
                direction, entry_price, forward,
                combo.tp_pct, combo.sl_pct, combo.leverage,
                fee_roi, liq_pct, max_bars)

            trades_roi.append(roi)
            trades_bars.append(bars)
            if result == "LIQ":
                liq_count += 1

            in_position = True
            position_end_idx = entry_idx + bars + 1

            # Early termination: too many liqs
            if liq_count >= 5 and len(trades_roi) <= 10:
                break

        if not trades_roi:
            return OptResult(combo=combo)

        total_roi = sum(trades_roi)
        wins = sum(1 for r in trades_roi if r > 0)
        wr = wins / len(trades_roi) * 100

        # Max drawdown (consecutive)
        dd = 0; max_dd = 0
        for r in trades_roi:
            if r < 0:
                dd += r
                max_dd = min(max_dd, dd)
            else:
                dd = 0

        return OptResult(
            combo=combo,
            total_roi=round(total_roi, 1),
            win_rate=round(wr, 1),
            max_drawdown=round(max_dd, 1),
            liq_rate=round(liq_count / len(trades_roi), 3) if trades_roi else 0,
            trade_count=len(trades_roi),
            avg_hold_bars=round(np.mean(trades_bars), 1) if trades_bars else 0,
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

            # SL
            if sl_pct > 0 and adv >= sl_pct:
                return "SL", i + 1, -sl_pct * leverage - fee_roi

            # Liquidation
            if adv >= liq_pct:
                return "LIQ", i + 1, -100.0

            # TP
            if fav >= tp_pct:
                return "TP", i + 1, tp_pct * leverage - fee_roi

        # Timeout
        if forward_5m:
            close = float(forward_5m[min(max_bars - 1, len(forward_5m) - 1)][4])
            if direction == "LONG":
                pnl = (close - entry_price) / entry_price * 100
            else:
                pnl = (entry_price - close) / entry_price * 100
            return "TIME", min(max_bars, len(forward_5m)), pnl * leverage - fee_roi

        return "NO_DATA", 0, 0.0

    @staticmethod
    def _score_combo(result: OptResult, weights: dict) -> float:
        """Score an optimization result. Higher = better."""
        w = weights
        score = 0.0

        # ROI component (normalize: 500% = max)
        roi_norm = min(max(result.total_roi / 500.0, -1.0), 1.0)
        score += w.get("roi", 0.35) * roi_norm * 100

        # Win rate component (0-100)
        score += w.get("win_rate", 0.25) * result.win_rate

        # Drawdown penalty (normalize: -500% = worst)
        dd_norm = min(abs(result.max_drawdown) / 500.0, 1.0)
        score -= w.get("max_drawdown", 0.20) * dd_norm * 100

        # Liq rate penalty
        score -= w.get("liq_rate", 0.15) * result.liq_rate * 100

        # Trade count bonus (more trades = more reliable)
        tc_norm = min(result.trade_count / 20.0, 1.0)
        score += w.get("trade_count", 0.05) * tc_norm * 100

        return round(score, 1)

    # ══════════════════════ TF Analysis (reused from System F) ══════════════════════

    def _analyze_tf(self, klines: list, tf_name: str, sg: dict) -> TFSignalG:
        """Single TF 3/3 indicator vote."""
        sig = TFSignalG(timeframe=tf_name)
        closes = np.array([float(k[4]) for k in klines])
        volumes = np.array([float(k[5]) for k in klines])
        highs = np.array([float(k[2]) for k in klines])
        lows = np.array([float(k[3]) for k in klines])
        price = float(closes[-1]) if len(closes) > 0 else 0.0

        if len(closes) < 30 or price <= 0:
            return sig

        # EMA
        ef = ema_val(closes, sg.get("ema_fast", 9))
        es = ema_val(closes, sg.get("ema_slow", 21))
        sig.ema_gap_pct = (ef - es) / price * 100
        gap_min = sg.get("ema_gap_min_pct", 0.05)
        sig.ema_vote = 1 if sig.ema_gap_pct > gap_min else (
            -1 if sig.ema_gap_pct < -gap_min else 0)

        # MACD
        ml = macd_line_series(closes, sg.get("macd_fast", 8), sg.get("macd_slow", 17))
        sl = ema_series(ml, sg.get("macd_signal", 9))
        hist = ml - sl
        if len(hist) >= 3:
            h1, h2, h3 = float(hist[-3]), float(hist[-2]), float(hist[-1])
            if sg.get("macd_momentum_required", True):
                if h3 > 0 and h1 < h2 < h3: sig.macd_vote = 1
                elif h3 < 0 and h1 > h2 > h3: sig.macd_vote = -1
            else:
                sig.macd_vote = 1 if h3 > 0 else (-1 if h3 < 0 else 0)

        # RSI
        r = rsi_val(closes, sg.get("rsi_periyot", 14))
        sig.rsi_vote = 1 if r > sg.get("rsi_long_esik", 60) else (
            -1 if r < sg.get("rsi_short_esik", 40) else 0)

        # ADX
        sig.adx_value = adx_val(highs, lows, closes, sg.get("adx_periyot", 14))

        # Volume
        vp = 20
        if len(volumes) >= vp + 1:
            vm = float(np.mean(volumes[-(vp + 1):-1]))
            if vm > 0:
                sig.volume_ratio = float(volumes[-1]) / vm

        # 3/3 direction
        if sig.ema_vote > 0 and sig.macd_vote > 0 and sig.rsi_vote > 0:
            sig.strict_direction = "LONG"
        elif sig.ema_vote < 0 and sig.macd_vote < 0 and sig.rsi_vote < 0:
            sig.strict_direction = "SHORT"
        else:
            sig.strict_direction = "FLAT"

        # Confidence
        if sig.strict_direction != "FLAT":
            ab = min(sig.adx_value / 50.0, 1.0) * 0.2 if sig.adx_value > 20 else 0
            vb = 0.1 if sig.volume_ratio >= sg.get("vol_tf_threshold", 1.5) else 0
            gb = min(abs(sig.ema_gap_pct) / 0.2, 1.0) * 0.1
            sig.confidence = min(0.6 + ab + vb + gb, 1.0)

        return sig
