"""Scanner Scorer - analyzes and ranks symbols for trading opportunity quality.
Computes indicators, confluence, regime, divergence for each symbol and produces
a composite score (0-100) for ranking."""
import math
from dataclasses import dataclass, field
import pandas as pd
from loguru import logger
from core.config_manager import ConfigManager
from indicators.indicator_engine import IndicatorEngine
from analysis.confluence import ConfluenceScorer
from analysis.market_regime import MarketRegimeDetector
from analysis.divergence import DivergenceDetector

# Timeframe to seconds mapping for wall strength calculation
_TF_SECONDS = {"1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800,
               "1h": 3600, "2h": 7200, "4h": 14400, "6h": 21600,
               "8h": 28800, "12h": 43200}


@dataclass
class ScanResult:
    """Result of scanning a single symbol."""
    symbol: str
    score: float                    # composite opportunity score (-100 to +100)
    direction: str                  # "LONG" or "SHORT"
    confluence: dict = field(default_factory=dict)
    regime: dict = field(default_factory=dict)
    divergences: list = field(default_factory=list)
    indicator_values: dict = field(default_factory=dict)
    volume_24h: float = 0.0
    price_change_pct: float = 0.0
    price: float = 0.0
    atr: float = 0.0
    atr_percent: float = 0.0       # ATR as % of price
    rsi: float = 50.0
    adx: float = 0.0
    eligible: bool = False          # passes all hard filters
    reject_reason: str = ""
    filter_checks: dict = field(default_factory=dict)  # {filter_name: (passed, actual, required)}
    leverage: int = 0               # max leverage for this coin
    timeframe: str = "1m"           # optimal timeframe
    funding_rate: float = 0.0      # current funding rate (e.g. 0.0001 = 0.01%)
    oi_change_pct: float = 0.0     # open interest change % (last 30min)
    ob_imbalance: float = 0.0      # order book weighted imbalance (-1 to +1)
    ob_wall_signal: str = "NONE"   # UP_BLOCKED / DOWN_BLOCKED / NONE
    ob_wall_seconds: float = 0.0   # wall strength in seconds of volume
    ob_ask_depth_seconds: float = 0.0  # total ask depth in seconds
    ob_bid_depth_seconds: float = 0.0  # total bid depth in seconds
    ob_liquidity: float = 0.0      # order book liquidity score (0-100)
    ob_thin_book: bool = False     # True if dangerously low liquidity
    mtf_data: dict = field(default_factory=dict)  # {tf: {indicators, confluence}} for multi-TF
    adx_regime: str = ""  # NO_TRADE / RANGING / WEAK_TREND / STRONG_TREND


class ScannerScorer:
    """Scores each symbol for buy/short opportunity quality."""

    def __init__(self, config: ConfigManager):
        self._config = config
        # Dedicated indicator engine for scanning (not shared with main engine)
        self._engine = IndicatorEngine(config)
        self._confluence = ConfluenceScorer(threshold=4.0, config=config)
        self._regime = MarketRegimeDetector()
        self._divergence = DivergenceDetector(lookback=20)

        # Score weights
        self._w_confluence = 0.35
        self._w_regime = 0.20
        self._w_volume = 0.15
        self._w_trend = 0.15
        self._w_risk = 0.15

    def score_symbol(self, symbol: str, klines: pd.DataFrame,
                     volume_24h: float = 0, price_change_pct: float = 0,
                     market_context: dict = None) -> ScanResult:
        """Compute full analysis and opportunity score for one symbol.
        market_context: optional {funding_rate: float, oi_change_pct: float}"""
        result = ScanResult(
            symbol=symbol,
            score=0.0,
            direction="LONG",
            volume_24h=volume_24h,
            price_change_pct=price_change_pct,
        )

        # Inject market context (funding rate, open interest, order book)
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
            # Compute all 30+ indicators
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

            # Regime detection
            regime = self._regime.detect(indicators)
            result.regime = regime

            # Confluence with regime weights
            regime_weights = regime.get("indicator_weights", {})
            confluence = self._confluence.score(indicators, regime_weights)
            result.confluence = confluence

            # Divergence
            ind_series = {}
            for name in ["RSI", "OBV"]:
                ind = self._engine.get_indicator(name)
                if ind and ind._series is not None:
                    ind_series[name] = ind._series
            divergences = self._divergence.detect_all(klines, ind_series)
            result.divergences = divergences

            # Determine direction
            conf_score = confluence.get("score", 0)
            if conf_score >= 0:
                result.direction = "LONG"
            else:
                result.direction = "SHORT"

            # ADX regime classification
            result.adx_regime = self._classify_adx_regime(result)

            # Compute composite score (HER ZAMAN — eligible olmasa bile GUI'de görünsün)
            result.score = self._compute_score(result)

            # Check eligibility (hard filters)
            eligible, reason = self._check_eligibility(result)
            result.eligible = eligible
            result.reject_reason = reason

        except Exception as e:
            logger.debug(f"Scoring error for {symbol}: {e}")
            result.reject_reason = f"error: {e}"

        return result

    def score_batch(self, klines_map: dict[str, pd.DataFrame],
                    ticker_data: dict[str, dict],
                    market_context_map: dict[str, dict] = None) -> list[ScanResult]:
        """Score multiple symbols and return sorted results.
        market_context_map: {symbol: {funding_rate, oi_change_pct}}"""
        results = []
        ctx_map = market_context_map or {}
        for symbol, klines in klines_map.items():
            ticker = ticker_data.get(symbol, {})
            vol = ticker.get("volume_24h", 0)
            change = ticker.get("price_change_pct", 0)
            ctx = ctx_map.get(symbol)
            result = self.score_symbol(symbol, klines, vol, change, ctx)
            results.append(result)

        # Sort by absolute score descending (best opportunities first)
        results.sort(key=lambda r: abs(r.score), reverse=True)
        return results

    def _check_eligibility(self, r: ScanResult) -> tuple[bool, str]:
        """Check hard filters. Records ALL filter results in r.filter_checks.
        Returns (eligible, first_reject_reason)."""
        checks = {}
        first_fail = ""
        conf_score = r.confluence.get("score", 0)
        regime_name = r.regime.get("regime", "UNKNOWN")
        trend_dir = r.regime.get("trend_direction", "NONE")

        # === READ STRATEGY CONFIG ===
        strat = self._config.get("strategy", {})
        max_lev = strat.get("max_leverage", 20)

        # 1. ATR SAFETY
        target_atr_pct = 0
        if max_lev >= 1 and r.atr_percent > 0:
            liq_factor = strat.get("liq_factor", 70) / 100.0
            sl_liq_pct = strat.get("sl_liq_percent", 50) / 100.0
            target_atr_pct = (1.0 / max_lev) * 100.0 * liq_factor * sl_liq_pct / 2.0
            passed = r.atr_percent <= target_atr_pct
            checks["ATR"] = (passed, f"{r.atr_percent:.3f}%", f"<{target_atr_pct:.3f}%")
            if not passed and not first_fail:
                first_fail = (f"atr_too_volatile_{max_lev}x "
                              f"(ATR={r.atr_percent:.3f}% > "
                              f"target={target_atr_pct:.3f}%)")
        else:
            checks["ATR"] = (True, "-", "-")

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

        # 4. ORDERBOOK (thin + volume-relative wall + total depth)
        ob_passed = not r.ob_thin_book
        wall_ok = True
        depth_ok = True
        wall_info = ""

        # Wall blocking: compare wall strength to timeframe
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
            # Wall exists but too thin relative to volume — ignore it

        # Total depth pressure: all levels combined on the blocking side
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
                first_fail = "thin_order_book (low liquidity)"
            elif not wall_ok:
                first_fail = f"{r.ob_wall_signal.lower()} ({wall_info})"
            else:
                first_fail = f"total_depth_blocking ({wall_info})"

        # === ADX REGIME GATE (new system) ===
        if strat.get("adx_regime_enabled", False) and r.adx_regime == "NO_TRADE":
            checks["ADX"] = (False, f"{r.adx:.0f}", f">={strat.get('adx_regime_no_trade', 18)}")
            if not first_fail:
                first_fail = f"adx_no_trade_zone ({r.adx:.0f} < {strat.get('adx_regime_no_trade', 18)})"
            # NO_TRADE is final — skip zone detection entirely
            r.filter_checks = checks
            return False, first_fail

        # === ADX ZONE DETECTION ===
        ranging_cfg = strat.get("ranging_mode", {})
        gray_cfg = strat.get("gray_zone", {})
        trending_cfg = strat.get("trending_mode", {})

        # Determine which zone we're in
        if r.adx <= ranging_cfg.get("max_adx", 18):
            zone = "RANGING"
            min_conf = ranging_cfg.get("min_confluence", 4.0)
            min_adx = strat.get("min_adx", 25)  # Same ADX minimum for all zones
            if ranging_cfg.get("enabled", True):
                max_rsi_long = ranging_cfg.get("max_rsi_buy", 35)
                min_rsi_short = ranging_cfg.get("min_rsi_sell", 65)
            else:
                # Ranging mode disabled — record and fail
                checks["Zone"] = (False, "RANG", "enabled")
                if not first_fail:
                    first_fail = "ranging_mode_disabled"
                # Still need defaults for remaining checks
                max_rsi_long = 35
                min_rsi_short = 65
        elif r.adx < trending_cfg.get("min_adx", 25):
            zone = "GRAY"
            min_conf = gray_cfg.get("min_confluence", 6.0)
            min_adx = strat.get("min_adx", 18)
            max_rsi_long = strat.get("max_rsi_long", 62)
            min_rsi_short = strat.get("min_rsi_short", 38)
        else:
            zone = "TRENDING"
            min_conf = trending_cfg.get("min_confluence", strat.get("min_confluence", 6.5))
            min_adx = trending_cfg.get("min_adx", 25)
            max_rsi_long = strat.get("max_rsi_long", 62)
            min_rsi_short = strat.get("min_rsi_short", 38)

        # Zone check (informational, always passes unless ranging disabled above)
        if "Zone" not in checks:
            checks["Zone"] = (True, zone[:4], zone[:4])

        # 5. Confluence check
        if r.direction == "LONG":
            conf_passed = conf_score >= min_conf
            checks["Conf"] = (conf_passed, f"{conf_score:.1f}", f">={min_conf:.0f}")
        else:
            conf_passed = conf_score <= -min_conf
            checks["Conf"] = (conf_passed, f"{conf_score:.1f}", f"<=-{min_conf:.0f}")
        if not conf_passed and not first_fail:
            first_fail = f"confluence_{zone.lower()} ({conf_score:.1f}, need {'+'if r.direction=='LONG' else '-'}{min_conf})"

        # 6. RSI check
        if r.direction == "LONG":
            rsi_passed = r.rsi <= max_rsi_long
            checks["RSI"] = (rsi_passed, f"{r.rsi:.0f}", f"<={max_rsi_long}")
        else:
            rsi_passed = r.rsi >= min_rsi_short
            checks["RSI"] = (rsi_passed, f"{r.rsi:.0f}", f">={min_rsi_short}")
        if not rsi_passed and not first_fail:
            first_fail = f"rsi_{zone.lower()} ({r.rsi:.0f})"

        # 7. ADX minimum check
        adx_passed = r.adx >= min_adx
        checks["ADX"] = (adx_passed, f"{r.adx:.0f}", f">={min_adx}")
        if not adx_passed and not first_fail:
            first_fail = f"adx_too_low_{zone.lower()} ({r.adx:.0f})"

        # 8. Trend direction check
        trend_passed = True
        if r.direction == "LONG" and trend_dir == "DOWN" and r.adx > 25:
            trend_passed = False
        elif r.direction == "SHORT" and trend_dir == "UP" and r.adx > 25:
            trend_passed = False
        checks["Trend"] = (trend_passed, trend_dir[:2], f"={'UP' if r.direction=='LONG' else 'DN'}?")
        if not trend_passed and not first_fail:
            first_fail = f"trend_against_{r.direction.lower()}"

        # Apply zone-specific filter flags
        use_macd = strat.get("macd_filter", True)
        use_volume = strat.get("volume_filter", True)
        # Ranging mode disables MACD and volume filters
        if zone == "RANGING":
            use_macd = False
            use_volume = False

        # 9. Volume confirmation
        if use_volume:
            obv_slope = r.indicator_values.get("OBV_slope", 0)
            cmf = r.indicator_values.get("CMF", 0)
            if r.direction == "LONG":
                vol_passed = obv_slope > 0 or cmf > 0
            else:
                vol_passed = obv_slope < 0 or cmf < 0
            checks["Vol"] = (vol_passed, f"{'+'if obv_slope>0 else '-'}", "confirm")
        else:
            vol_passed = True
            checks["Vol"] = (True, "skip", "skip")
        if not vol_passed and not first_fail:
            first_fail = "no_volume_confirmation"

        # 10. MACD filter
        if use_macd:
            macd_h = r.indicator_values.get("MACD_histogram", 0)
            if r.direction == "LONG":
                macd_passed = macd_h > 0
            else:
                macd_passed = macd_h < 0
            checks["MACD"] = (macd_passed, f"{macd_h:.4f}", f"{'>'if r.direction=='LONG' else '<'}0")
        else:
            macd_passed = True
            checks["MACD"] = (True, "skip", "skip")
        if not macd_passed and not first_fail:
            first_fail = f"macd_not_{'bullish' if r.direction=='LONG' else 'bearish'}"

        # 11. Gray zone confirmation (only in gray zone)
        if zone == "GRAY":
            confirmation_cfg = gray_cfg.get("confirmation_system", {})
            if confirmation_cfg.get("enabled", True):
                confirmation_score = self._calculate_gray_zone_confirmation(r, confirmation_cfg)
                required_score = confirmation_cfg.get("required_score", 0.6)
                gz_passed = confirmation_score >= required_score
                checks["GZ"] = (gz_passed, f"{confirmation_score:.2f}", f">={required_score}")
                if not gz_passed and not first_fail:
                    first_fail = f"gray_zone_confirmation_low ({confirmation_score:.2f}, need {required_score}+)"

        r.filter_checks = checks
        all_passed = not first_fail
        return all_passed, first_fail

    def _classify_adx_regime(self, r: ScanResult) -> str:
        """Classify ADX regime: NO_TRADE / RANGING / WEAK_TREND / STRONG_TREND.
        Used by state_machine to determine entry type, SL, trailing params."""
        strat = self._config.get("strategy", {})
        if not strat.get("adx_regime_enabled", False):
            return ""

        no_trade_threshold = strat.get("adx_regime_no_trade", 18)
        strong_trend_threshold = strat.get("adx_regime_strong_trend", 25)

        if r.adx < no_trade_threshold:
            return "NO_TRADE"
        elif r.adx >= strong_trend_threshold:
            return "STRONG_TREND"
        else:
            # ADX 18-25: check if trend is confirmed via confluence active_group
            active_group = r.confluence.get("active_group", "NEUTRAL")
            if active_group in ("TREND", "BOTH"):
                return "WEAK_TREND"
            else:
                return "RANGING"

    def _compute_score(self, r: ScanResult) -> float:
        """Compute composite score (0-100, negative for SHORT)."""
        conf_score = self._score_confluence(r)
        regime_score = self._score_regime(r)
        volume_score = self._score_volume(r)
        trend_score = self._score_trend(r)
        risk_score = self._score_risk(r)

        raw = (
            self._w_confluence * conf_score +
            self._w_regime * regime_score +
            self._w_volume * volume_score +
            self._w_trend * trend_score +
            self._w_risk * risk_score
        )

        # Funding rate, Open Interest & Order Book bonus/penalty (max ±12 points)
        sentiment = self._score_sentiment(r)
        raw += sentiment

        # Cap at 100
        raw = min(raw, 100.0)

        # Negative for SHORT direction
        if r.direction == "SHORT":
            raw = -raw

        return round(raw, 1)

    def _score_confluence(self, r: ScanResult) -> float:
        """Score 0-100 based on confluence signal strength."""
        conf = r.confluence
        raw_score = abs(conf.get("score", 0))
        strength = conf.get("strength", 0)
        bullish = conf.get("bullish_count", 0)
        total = conf.get("total_indicators", 1)

        # Normalize: confluence score of 4.0 = 50, 8.0 = 80, 12+ = 100
        base = min(raw_score / 12.0 * 100, 100)

        # Bonus for high agreement ratio
        agreement = bullish / total if r.direction == "LONG" else \
            conf.get("bearish_count", 0) / total
        agreement_bonus = agreement * 20

        return min(base + agreement_bonus, 100)

    def _score_regime(self, r: ScanResult) -> float:
        """Score 0-100 based on regime alignment with direction."""
        regime = r.regime.get("regime", "UNKNOWN")
        trend_dir = r.regime.get("trend_direction", "NONE")
        confidence = r.regime.get("confidence", 0)

        if r.direction == "LONG":
            if regime == "TRENDING" and trend_dir == "UP":
                base = 100
            elif regime == "BREAKOUT":
                base = 80
            elif regime == "TRENDING" and trend_dir == "DOWN":
                base = 20  # counter-trend
            elif regime == "RANGING":
                base = 50
            else:
                base = 30
        else:  # SHORT
            if regime == "TRENDING" and trend_dir == "DOWN":
                base = 100
            elif regime == "VOLATILE":
                base = 70
            elif regime == "BREAKOUT":
                base = 60
            elif regime == "TRENDING" and trend_dir == "UP":
                base = 20
            else:
                base = 40

        return base * confidence

    def _score_volume(self, r: ScanResult) -> float:
        """Score 0-100 based on volume confirmation.
        Orthogonality audit: OBV + CMF + CVD (orderflow)."""
        score = 50.0  # base
        obv_slope = r.indicator_values.get("OBV_slope", 0)
        cmf = r.indicator_values.get("CMF", 0)
        cvd_norm = r.indicator_values.get("CVD_normalized", 0)

        if r.direction == "LONG":
            if obv_slope > 0:
                score += 15
            if cmf > 0.1:
                score += 15
            elif cmf > 0:
                score += 5
            if cvd_norm > 0.3:
                score += 15  # strong buy flow
            elif cvd_norm > 0.1:
                score += 8
        else:
            if obv_slope < 0:
                score += 15
            if cmf < -0.1:
                score += 15
            elif cmf < 0:
                score += 5
            if cvd_norm < -0.3:
                score += 15  # strong sell flow
            elif cvd_norm < -0.1:
                score += 8

        return min(score, 100)

    def _calculate_gray_zone_confirmation(self, r: ScanResult, cfg: dict) -> float:
        """Calculate gray zone confirmation score (0.0-1.0)."""
        total_score = 0.0
        
        # 1. Trend Direction Analysis
        trend_cfg = cfg.get("trend_direction", {})
        trend_weight = trend_cfg.get("weight", 0.3)
        trend_score = 0.0
        
        # DI Difference: +DI vs -DI strength
        plus_di = r.indicator_values.get("ADX_plus_DI", 0)
        minus_di = r.indicator_values.get("ADX_minus_DI", 0)
        di_diff = abs(plus_di - minus_di)
        di_threshold = trend_cfg.get("di_diff_threshold", 2.0)
        if di_diff > di_threshold:
            trend_score += trend_cfg.get("di_diff_points", 0.4)
        
        # EMA Cross: fast vs slow EMA
        ema_fast = r.indicator_values.get("EMA_fast", 0)
        ema_slow = r.indicator_values.get("EMA_slow", 0)
        if ema_fast > 0 and ema_slow > 0:
            if (r.direction == "LONG" and ema_fast > ema_slow) or (r.direction == "SHORT" and ema_fast < ema_slow):
                trend_score += trend_cfg.get("ema_cross_points", 0.3)
        
        # MACD alignment (replaced Supertrend — orthogonality audit)
        macd_h = r.indicator_values.get("MACD_histogram", 0)
        if (r.direction == "LONG" and macd_h > 0) or (r.direction == "SHORT" and macd_h < 0):
            trend_score += trend_cfg.get("supertrend_points", 0.3)

        total_score += min(trend_score, 1.0) * trend_weight
        
        # 2. Volatility Context
        vol_cfg = cfg.get("volatility_context", {})
        vol_weight = vol_cfg.get("weight", 0.25)
        vol_score = 0.0
        
        # Bollinger Band Width
        bb_width = r.indicator_values.get("BB_Width", 0)
        bb_low = vol_cfg.get("bb_width_low", 2.0)
        bb_high = vol_cfg.get("bb_width_high", 4.0)
        
        if bb_width < bb_low:
            # Low volatility = potential breakout
            vol_score += 0.4
        elif bb_width > bb_high:
            # High volatility = ranging likely
            vol_score += 0.2
        else:
            # Moderate volatility = neutral
            vol_score += 0.3
        
        # ATR efficiency (price movement efficiency)
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
        
        # OBV Slope
        obv_slope = r.indicator_values.get("OBV_slope", 0)
        obv_threshold = mom_cfg.get("obv_slope_threshold", 0.1)
        if (r.direction == "LONG" and obv_slope > obv_threshold) or (r.direction == "SHORT" and obv_slope < -obv_threshold):
            mom_score += 0.4
        
        # CMF (Chaikin Money Flow)
        cmf = r.indicator_values.get("CMF", 0)
        cmf_threshold = mom_cfg.get("cmf_threshold", 0.1)
        if (r.direction == "LONG" and cmf > cmf_threshold) or (r.direction == "SHORT" and cmf < -cmf_threshold):
            mom_score += 0.4
        
        # MACD Histogram trend
        macd_h = r.indicator_values.get("MACD_histogram", 0)
        if mom_cfg.get("macd_histogram_trend", True):
            if (r.direction == "LONG" and macd_h > 0) or (r.direction == "SHORT" and macd_h < 0):
                mom_score += 0.2
        
        total_score += min(mom_score, 1.0) * mom_weight
        
        # 4. Market Structure (simplified)
        struct_cfg = cfg.get("market_structure", {})
        struct_weight = struct_cfg.get("weight", 0.2)
        struct_score = 0.0
        
        # Price position relative to recent range
        # This is a simplified version - could be enhanced with actual HH/LL detection
        rsi = r.indicator_values.get("RSI", 50)
        if r.direction == "LONG":
            if rsi > 45 and rsi < 65:  # Not oversold, not overbought = good structure
                struct_score += 0.5
        else:
            if rsi > 35 and rsi < 55:  # Similar logic for shorts
                struct_score += 0.5
        
        # Additional structure points based on price momentum
        price_change = abs(r.price_change_pct)
        if price_change > 0.5 and price_change < 3.0:  # Moderate movement
            struct_score += 0.3
        
        total_score += min(struct_score, 1.0) * struct_weight
        
        return min(total_score, 1.0)

    def _score_trend(self, r: ScanResult) -> float:
        """Score 0-100 based on trend strength indicators.
        Orthogonality audit: 3 independent signals (ADX, MACD, SMA200)."""
        score = 0.0
        adx = r.indicator_values.get("ADX", 0)
        plus_di = r.indicator_values.get("ADX_plus_DI", 0)
        minus_di = r.indicator_values.get("ADX_minus_DI", 0)
        macd_h = r.indicator_values.get("MACD_histogram", 0)
        price = r.indicator_values.get("Price", 0)
        sma200 = r.indicator_values.get("SMA_slow", 0)

        # ADX strength (boosted: was 30, now 35)
        if adx > 30:
            score += 35
        elif adx > 20:
            score += 18

        # DI alignment (boosted: was 20, now 25)
        if r.direction == "LONG" and plus_di > minus_di:
            score += 25
        elif r.direction == "SHORT" and minus_di > plus_di:
            score += 25

        # MACD histogram (boosted: was 15, now 20)
        if r.direction == "LONG" and macd_h > 0:
            score += 20
        elif r.direction == "SHORT" and macd_h < 0:
            score += 20

        # Price vs SMA200 (boosted: was 15, now 20)
        if price > 0 and sma200 > 0:
            if r.direction == "LONG" and price > sma200:
                score += 20
            elif r.direction == "SHORT" and price < sma200:
                score += 20

        return min(score, 100)

    def _score_risk(self, r: ScanResult) -> float:
        """Score 0-100 based on risk quality (ATR, divergences)."""
        score = 60.0

        # ATR% in sweet spot: target_ATR = (1/L) × liq_factor × sl_liq_pct / 2
        strat = self._config.get("strategy", {})
        max_lev = strat.get("max_leverage", 20)
        liq_factor = strat.get("liq_factor", 70) / 100.0
        sl_liq_pct = strat.get("sl_liq_percent", 50) / 100.0
        atr_pct = r.atr_percent
        safe_atr = (1.0 / max(max_lev, 1)) * 100 * liq_factor * sl_liq_pct / 2.0
        if atr_pct <= safe_atr * 0.8:
            score += 20  # ATR hedefin altında — ideal
        elif atr_pct <= safe_atr:
            score += 10  # ATR hedefe yakın — kabul edilebilir
        elif atr_pct > safe_atr * 4:
            score -= 30  # too volatile
        if atr_pct < 0.05:
            score -= 20  # no movement

        # Divergence check
        for d in r.divergences:
            if r.direction == "LONG" and d.get("type") == "REGULAR_BULLISH":
                score += 20  # bullish divergence supports long
            elif r.direction == "LONG" and d.get("type") == "REGULAR_BEARISH":
                score -= 20  # bearish divergence warns against long
            elif r.direction == "SHORT" and d.get("type") == "REGULAR_BEARISH":
                score += 20
            elif r.direction == "SHORT" and d.get("type") == "REGULAR_BULLISH":
                score -= 20

        return max(0, min(score, 100))

    def _score_sentiment(self, r: ScanResult) -> float:
        """Score bonus/penalty from funding rate + open interest + order book (max +/-14 pts).

        Funding Rate Logic:
          - High positive funding → longs are crowded → favors SHORT
          - High negative funding → shorts are crowded → favors LONG
          - Extreme funding (>0.1%) → strong contrarian signal

        Open Interest Logic:
          - OI rising + our direction aligned with price trend → confirmation bonus
          - OI falling against our direction → caution penalty
        """
        bonus = 0.0
        fr = r.funding_rate  # e.g. 0.0001 = 0.01%, 0.001 = 0.1%

        # --- Funding Rate ---
        if fr != 0:
            fr_pct = fr * 100  # convert to percentage (0.0001 -> 0.01%)

            if r.direction == "LONG":
                if fr_pct > 0.05:
                    # Positive funding = longs pay shorts = crowd is long
                    # Penalize going with the crowd
                    bonus -= min(fr_pct * 20, 5.0)  # max -5 pts
                elif fr_pct < -0.05:
                    # Negative funding = shorts pay longs = crowd is short
                    # Bonus for contrarian long
                    bonus += min(abs(fr_pct) * 20, 5.0)  # max +5 pts
            else:  # SHORT
                if fr_pct < -0.05:
                    # Negative funding = crowd is short, penalize
                    bonus -= min(abs(fr_pct) * 20, 5.0)
                elif fr_pct > 0.05:
                    # Positive funding = crowd is long, bonus for short
                    bonus += min(fr_pct * 20, 5.0)

        # --- Open Interest Change ---
        oi_chg = r.oi_change_pct  # e.g. 5.0 = 5% increase

        if oi_chg != 0:
            price_chg = r.price_change_pct  # 24h price change %

            if r.direction == "LONG":
                if oi_chg > 2 and price_chg > 0:
                    # OI rising + price rising = new money flowing in bullish
                    bonus += min(oi_chg * 0.5, 3.0)
                elif oi_chg > 2 and price_chg < -1:
                    # OI rising + price falling = bearish pressure building
                    bonus -= min(oi_chg * 0.5, 3.0)
                elif oi_chg < -2 and price_chg > 0:
                    # OI falling + price rising = short squeeze, weak rally
                    bonus -= 1.0
            else:  # SHORT
                if oi_chg > 2 and price_chg < 0:
                    # OI rising + price falling = new short money confirmed
                    bonus += min(oi_chg * 0.5, 3.0)
                elif oi_chg > 2 and price_chg > 1:
                    # OI rising + price rising = bullish, bad for short
                    bonus -= min(oi_chg * 0.5, 3.0)
                elif oi_chg < -2 and price_chg < 0:
                    # OI falling + price falling = long liquidation, weak
                    bonus -= 1.0

        # --- Order Book Imbalance ---
        ob_imb = r.ob_imbalance  # -1 to +1

        if ob_imb != 0:
            if r.direction == "LONG":
                # Positive imbalance = more bids = buying pressure = good for long
                bonus += ob_imb * 5.0  # max ±5 pts
            else:  # SHORT
                # Negative imbalance = more asks = selling pressure = good for short
                bonus += -ob_imb * 5.0

        # --- Wall Bonus: wall blocking the OTHER direction favors us ---
        # UP_BLOCKED = big sell wall above → price can't go up → good for SHORT
        # DOWN_BLOCKED = big buy wall below → price can't go down → good for LONG
        if r.ob_wall_signal == "UP_BLOCKED" and r.direction == "SHORT":
            bonus += 2.0
        elif r.ob_wall_signal == "DOWN_BLOCKED" and r.direction == "LONG":
            bonus += 2.0

        # Liquidity quality bonus: high liquidity = safer trade
        if r.ob_liquidity >= 70:
            bonus += 2.0
        elif r.ob_liquidity < 30 and r.ob_liquidity > 0:
            bonus -= 2.0

        # Clamp total sentiment to +-14
        return max(-14.0, min(14.0, bonus))
