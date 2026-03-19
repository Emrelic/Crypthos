"""Mean Reversion Scanner Scorer - evaluates coins for BB band bounce opportunities.

Separate scoring pipeline from trend scorer. Evaluates:
- BB band proximity (price near upper/lower band)
- RSI extremes (oversold/overbought)
- Volume exhaustion (low volume = sellers/buyers exhausted)
- BB width quality (range must be wide enough for fees)
- Momentum turn signals (MACD histogram turning, RSI divergence)

Also includes gray zone router: classifies ADX 18-25 coins as TREND or RANGE
using 5-signal voting system.
"""
import math
from dataclasses import dataclass, field
import pandas as pd
from loguru import logger
from core.config_manager import ConfigManager
from indicators.indicator_engine import IndicatorEngine
from analysis.confluence import ConfluenceScorer


@dataclass
class MRScanResult:
    """Result of scanning a single symbol for mean reversion opportunity."""
    symbol: str
    score: float = 0.0               # MR composite score (0-100, negative for SHORT)
    direction: str = ""               # "LONG" or "SHORT"
    bb_percent_b: float = 0.5         # 0=lower band, 1=upper band
    bb_proximity_pct: float = 50.0    # distance to nearest band as % of BB range
    bb_range_pct: float = 0.0         # (upper-lower)/price * 100
    rsi: float = 50.0
    adx: float = 0.0
    volume_ratio: float = 1.0         # current vol / 20-bar avg
    bb_width: float = 0.0
    bb_width_slope: float = 0.0
    atr: float = 0.0
    atr_percent: float = 0.0
    price: float = 0.0
    leverage: int = 0
    timeframe: str = "1m"
    eligible: bool = False
    reject_reason: str = ""
    filter_checks: dict = field(default_factory=dict)
    indicator_values: dict = field(default_factory=dict)
    confluence: dict = field(default_factory=dict)
    momentum_signals: list = field(default_factory=list)  # ["RSI_turn", "MACD_turn", etc.]
    mr_tp_target: float = 0.0        # BB middle price (TP target)
    # Sentiment data (shared with trend)
    funding_rate: float = 0.0
    oi_change_pct: float = 0.0
    ob_imbalance: float = 0.0
    ob_thin_book: bool = False
    ob_liquidity: float = 0.0
    # Source tracking
    source: str = ""                  # "R" (ADX<18), "G->R" (gray->range)
    # Breakout warning
    breakout_risk: bool = False       # volume surge detected (potential breakout, not MR)


class MRScannerScorer:
    """Scores symbols for mean reversion (BB band bounce) opportunities."""

    def __init__(self, config: ConfigManager):
        self._config = config
        self._engine = IndicatorEngine(config)
        self._confluence = ConfluenceScorer(threshold=4.0, config=config)

    def classify_gray_zone(self, indicators: dict) -> str:
        """Classify gray zone coin (ADX 18-25) as TREND or RANGE.
        5-signal voting: ADX slope, DI separation, BB width slope,
        volume trend, EMA gap. Returns 'TREND' or 'RANGE'."""
        trend_votes = 0
        range_votes = 0

        # 1. ADX direction (weight: 1 vote — equal to other signals)
        adx_slope = indicators.get("ADX_slope", 0)
        if adx_slope > 0.3:
            trend_votes += 1
        else:
            range_votes += 1

        # 2. DI separation (weight: 1 vote)
        plus_di = indicators.get("ADX_plus_DI", 0)
        minus_di = indicators.get("ADX_minus_DI", 0)
        di_diff = abs(plus_di - minus_di)
        if di_diff > 8:
            trend_votes += 1
        else:
            range_votes += 1

        # 3. BB Width direction (weight: 1 vote)
        bb_slope = indicators.get("BB_Width_slope", 0)
        if bb_slope > 0:
            trend_votes += 1
        else:
            range_votes += 1

        # 4. Volume trend (weight: 1 vote)
        vol_ratio = indicators.get("Volume_ratio", 1.0)
        if vol_ratio > 1.2:
            trend_votes += 1
        else:
            range_votes += 1

        # 5. EMA gap expanding (weight: 1 vote)
        ema_expanding = indicators.get("EMA_gap_expanding", False)
        if ema_expanding:
            trend_votes += 1
        else:
            range_votes += 1

        # Total: 5 votes (1 each). Need >= 3 for TREND (majority)
        return "TREND" if trend_votes >= 3 else "RANGE"

    def score_symbol(self, symbol: str, klines: pd.DataFrame,
                     market_context: dict = None,
                     source: str = "R") -> MRScanResult:
        """Compute MR analysis and score for one symbol."""
        result = MRScanResult(symbol=symbol, source=source)

        if market_context:
            result.funding_rate = market_context.get("funding_rate", 0.0)
            result.oi_change_pct = market_context.get("oi_change_pct", 0.0)
            result.ob_imbalance = market_context.get("ob_imbalance", 0.0)
            result.ob_thin_book = market_context.get("ob_thin_book", False)
            result.ob_liquidity = market_context.get("ob_liquidity", 0.0)

        if klines is None or klines.empty or len(klines) < 50:
            result.reject_reason = "insufficient_data"
            return result

        try:
            indicators = self._engine.compute_all(klines)
            result.indicator_values = indicators
            result.price = indicators.get("Price", 0)
            result.atr = indicators.get("ATR", 0)
            rsi_val = indicators.get("RSI", 50)
            result.rsi = 50.0 if (rsi_val is None or (isinstance(rsi_val, float) and math.isnan(rsi_val))) else rsi_val
            adx_val = indicators.get("ADX", 0)
            result.adx = 0.0 if (adx_val is None or (isinstance(adx_val, float) and math.isnan(adx_val))) else adx_val
            result.volume_ratio = indicators.get("Volume_ratio", 1.0)
            result.bb_width = indicators.get("BB_Width", 0)
            result.bb_width_slope = indicators.get("BB_Width_slope", 0)

            if result.price > 0 and result.atr > 0:
                result.atr_percent = (result.atr / result.price) * 100

            # BB position
            bb_upper = indicators.get("BB_Upper", 0)
            bb_lower = indicators.get("BB_Lower", 0)
            bb_middle = indicators.get("BB_Middle", 0)
            result.mr_tp_target = bb_middle

            if bb_upper > bb_lower and result.price > 0:
                bb_range = bb_upper - bb_lower
                result.bb_range_pct = (bb_range / result.price) * 100
                result.bb_percent_b = (result.price - bb_lower) / bb_range

                # Proximity: how close to nearest band (0% = at band, 50% = at middle)
                dist_to_lower = (result.price - bb_lower) / bb_range * 100
                dist_to_upper = (bb_upper - result.price) / bb_range * 100
                result.bb_proximity_pct = min(dist_to_lower, dist_to_upper)

            # Confluence (for reference, not primary scoring)
            confluence = self._confluence.score(indicators)
            result.confluence = confluence

            # Determine direction from BB position
            if result.bb_percent_b <= 0.3:
                result.direction = "LONG"  # near lower band -> buy
            elif result.bb_percent_b >= 0.7:
                result.direction = "SHORT"  # near upper band -> sell
            else:
                result.direction = ""  # in the middle, no MR signal

            # Detect momentum turn signals
            result.momentum_signals = self._detect_momentum_turns(indicators, result.direction)

            # Breakout risk: high volume near band = potential breakout, not MR
            if result.volume_ratio > 1.5 and result.bb_proximity_pct < 15:
                result.breakout_risk = True

            # Compute MR score (HER ZAMAN — eligible olmasa bile GUI'de görünsün)
            result.score = self._compute_mr_score(result)

            # Check eligibility
            eligible, reason = self._check_mr_eligibility(result)
            result.eligible = eligible
            result.reject_reason = reason

        except Exception as e:
            logger.debug(f"MR scoring error for {symbol}: {e}")
            result.reject_reason = f"error: {e}"

        return result

    def score_batch(self, klines_map: dict, market_context_map: dict = None,
                    source_map: dict = None) -> list[MRScanResult]:
        """Score multiple symbols for MR and return sorted results."""
        results = []
        ctx_map = market_context_map or {}
        src_map = source_map or {}
        for symbol, klines in klines_map.items():
            ctx = ctx_map.get(symbol)
            src = src_map.get(symbol, "R")
            result = self.score_symbol(symbol, klines, ctx, src)
            results.append(result)
        results.sort(key=lambda r: abs(r.score), reverse=True)
        return results

    def _detect_momentum_turns(self, indicators: dict, direction: str) -> list:
        """Detect momentum reversal signals supporting MR direction."""
        signals = []
        if not direction:
            return signals

        # 1. MACD histogram turning
        macd_h = indicators.get("MACD_histogram", 0)
        macd_h_prev = indicators.get("MACD_histogram_prev", 0)
        if direction == "LONG" and macd_h > macd_h_prev and macd_h_prev < 0:
            signals.append("MACD_turn")
        elif direction == "SHORT" and macd_h < macd_h_prev and macd_h_prev > 0:
            signals.append("MACD_turn")

        # 2. RSI turning from extreme
        rsi = indicators.get("RSI", 50)
        if direction == "LONG" and rsi < 35 and rsi > indicators.get("RSI_prev", rsi):
            signals.append("RSI_turn")
        elif direction == "SHORT" and rsi > 65 and rsi < indicators.get("RSI_prev", rsi):
            signals.append("RSI_turn")

        # 3. OBV divergence (price falling but OBV flat/rising for LONG)
        obv_slope = indicators.get("OBV_slope", 0)
        price_change = indicators.get("Price", 0)
        if direction == "LONG" and obv_slope >= 0:
            signals.append("OBV_support")
        elif direction == "SHORT" and obv_slope <= 0:
            signals.append("OBV_support")

        return signals

    def _check_mr_eligibility(self, r: MRScanResult) -> tuple[bool, str]:
        """Check MR-specific hard filters. Returns (eligible, reject_reason)."""
        checks = {}
        first_fail = ""
        strat = self._config.get("strategy", {})

        # 1. Must have direction (near a band)
        if not r.direction:
            checks["BB"] = (False, f"{r.bb_percent_b:.0%}", "<30%/>70%")
            return False, "bb_middle_zone"

        # 2. BB proximity: must be near a band
        mr_bb_prox = strat.get("mr_bb_proximity_pct", 20.0)
        bb_near = r.bb_proximity_pct <= mr_bb_prox
        checks["BB"] = (bb_near, f"{r.bb_proximity_pct:.0f}%", f"<={mr_bb_prox:.0f}%")
        if not bb_near and not first_fail:
            first_fail = f"bb_not_near ({r.bb_proximity_pct:.0f}% > {mr_bb_prox:.0f}%)"

        # 3. RSI extreme
        mr_rsi_os = strat.get("mr_rsi_oversold", 30)
        mr_rsi_ob = strat.get("mr_rsi_overbought", 70)
        if r.direction == "LONG":
            rsi_ok = r.rsi <= mr_rsi_os
            checks["RSI"] = (rsi_ok, f"{r.rsi:.0f}", f"<={mr_rsi_os}")
        else:
            rsi_ok = r.rsi >= mr_rsi_ob
            checks["RSI"] = (rsi_ok, f"{r.rsi:.0f}", f">={mr_rsi_ob}")
        if not rsi_ok and not first_fail:
            first_fail = f"rsi_not_extreme ({r.rsi:.0f})"

        # 4. Volume exhaustion (low volume = bounce more likely)
        mr_vol_max = strat.get("mr_volume_exhaustion_max", 0.8)
        vol_ok = r.volume_ratio <= mr_vol_max
        checks["Vol"] = (vol_ok, f"{r.volume_ratio:.1f}x", f"<={mr_vol_max}")
        if not vol_ok and not first_fail:
            first_fail = f"volume_not_exhausted ({r.volume_ratio:.1f}x > {mr_vol_max})"

        # 5. BB range vs fee (must be profitable after fees)
        # Both sides in ROI% (price% × leverage × 100) for consistent comparison
        mr_fee_mult = strat.get("mr_min_bb_range_fee_mult", 3.0)
        max_lev = strat.get("max_leverage", 20)
        fee_pct = strat.get("fee_pct", 0.10) / 100.0  # 0.10 → 0.001
        fee_cost_roi = fee_pct * max_lev * 100  # fee as ROI% of margin
        # Half BB range = expected profit (entry to middle), also as ROI%
        half_range_roi = (r.bb_range_pct / 100.0) / 2 * max_lev * 100  # price% → ROI%
        min_range = fee_cost_roi * mr_fee_mult
        range_ok = half_range_roi >= min_range
        checks["Fee"] = (range_ok, f"{half_range_roi:.0f}%", f">={min_range:.0f}%")
        if not range_ok and not first_fail:
            first_fail = f"bb_range_narrow (ROI {half_range_roi:.0f}% < {min_range:.0f}% fee×{mr_fee_mult})"

        # 6. Breakout risk (high volume = not MR, potential breakout)
        breakout_ok = not r.breakout_risk
        checks["Brk"] = (breakout_ok, "risk" if r.breakout_risk else "ok", "ok")
        if not breakout_ok and not first_fail:
            first_fail = f"breakout_risk (vol={r.volume_ratio:.1f}x near band)"

        # 7. Thin order book
        ob_ok = not r.ob_thin_book
        checks["OB"] = (ob_ok, "thin" if r.ob_thin_book else "ok", "ok")
        if not ob_ok and not first_fail:
            first_fail = "thin_order_book"

        # 8. Extreme funding rate
        fr_pct = r.funding_rate * 100 if r.funding_rate != 0 else 0
        if r.direction == "LONG":
            fr_ok = fr_pct <= 0.1
        else:
            fr_ok = fr_pct >= -0.1
        checks["FR"] = (fr_ok, f"{fr_pct:+.2f}%", "ok")
        if not fr_ok and not first_fail:
            first_fail = f"extreme_funding ({fr_pct:.3f}%)"

        r.filter_checks = checks
        all_passed = not first_fail
        return all_passed, first_fail

    def _compute_mr_score(self, r: MRScanResult) -> float:
        """Compute MR composite score (0-100, negative for SHORT)."""
        # 25% BB proximity
        bb_score = self._score_bb_proximity(r)
        # 25% RSI extreme
        rsi_score = self._score_rsi_extreme(r)
        # 20% Volume exhaustion
        vol_score = self._score_volume_exhaustion(r)
        # 15% BB width quality
        width_score = self._score_bb_width(r)
        # 15% Momentum turn
        momentum_score = self._score_momentum(r)

        raw = (0.25 * bb_score +
               0.25 * rsi_score +
               0.20 * vol_score +
               0.15 * width_score +
               0.15 * momentum_score)

        # Sentiment adjustment (max ±8 for MR, less than trend's ±12)
        sentiment = self._score_mr_sentiment(r)
        raw += sentiment

        raw = min(raw, 100.0)

        if r.direction == "SHORT":
            raw = -raw

        return round(raw, 1)

    def _score_bb_proximity(self, r: MRScanResult) -> float:
        """0-100: how close to the band (closer = better MR signal)."""
        # bb_proximity_pct: 0% = at band, 50% = at middle
        if r.bb_proximity_pct <= 0:
            return 100.0
        elif r.bb_proximity_pct <= 5:
            return 90.0
        elif r.bb_proximity_pct <= 10:
            return 75.0
        elif r.bb_proximity_pct <= 15:
            return 55.0
        elif r.bb_proximity_pct <= 20:
            return 35.0
        else:
            return 10.0

    def _score_rsi_extreme(self, r: MRScanResult) -> float:
        """0-100: how extreme the RSI is."""
        if r.direction == "LONG":
            if r.rsi <= 15:
                return 100.0
            elif r.rsi <= 20:
                return 90.0
            elif r.rsi <= 25:
                return 75.0
            elif r.rsi <= 30:
                return 60.0
            elif r.rsi <= 35:
                return 30.0
            else:
                return 0.0
        else:  # SHORT
            if r.rsi >= 85:
                return 100.0
            elif r.rsi >= 80:
                return 90.0
            elif r.rsi >= 75:
                return 75.0
            elif r.rsi >= 70:
                return 60.0
            elif r.rsi >= 65:
                return 30.0
            else:
                return 0.0

    def _score_volume_exhaustion(self, r: MRScanResult) -> float:
        """0-100: lower volume = better for MR (exhaustion confirmed)."""
        vr = r.volume_ratio
        if vr <= 0.3:
            return 100.0
        elif vr <= 0.5:
            return 80.0
        elif vr <= 0.6:
            return 65.0
        elif vr <= 0.7:
            return 50.0
        elif vr <= 0.8:
            return 35.0
        elif vr <= 1.0:
            return 15.0
        else:
            return 0.0  # high volume = NOT exhaustion

    def _score_bb_width(self, r: MRScanResult) -> float:
        """0-100: BB width quality (not too narrow, not too wide)."""
        w = r.bb_width
        if w <= 0:
            return 0.0
        elif w < 1.0:
            return 20.0   # too narrow, unreliable range
        elif w <= 1.5:
            return 70.0   # tight range, ok
        elif w <= 2.5:
            return 100.0  # ideal range
        elif w <= 3.5:
            return 70.0   # wider, still ok
        elif w <= 4.5:
            return 40.0   # getting too wide
        else:
            return 10.0   # very wide = volatile, not ranging

    def _score_momentum(self, r: MRScanResult) -> float:
        """0-100: momentum turn signals supporting MR direction."""
        signals = r.momentum_signals
        if not signals:
            return 20.0  # base score even without turn signals

        score = 20.0
        if "MACD_turn" in signals:
            score += 30.0
        if "RSI_turn" in signals:
            score += 30.0
        if "OBV_support" in signals:
            score += 20.0

        return min(score, 100.0)

    def _score_mr_sentiment(self, r: MRScanResult) -> float:
        """Sentiment bonus/penalty for MR (max ±8, more conservative than trend)."""
        bonus = 0.0

        # Funding rate contrarian
        fr = r.funding_rate
        if fr != 0:
            fr_pct = fr * 100
            if r.direction == "LONG" and fr_pct < -0.03:
                bonus += min(abs(fr_pct) * 15, 3.0)
            elif r.direction == "SHORT" and fr_pct > 0.03:
                bonus += min(fr_pct * 15, 3.0)

        # Order book imbalance
        ob_imb = r.ob_imbalance
        if ob_imb != 0:
            if r.direction == "LONG":
                bonus += ob_imb * 3.0
            else:
                bonus += -ob_imb * 3.0

        # Liquidity quality
        if r.ob_liquidity >= 70:
            bonus += 2.0
        elif r.ob_liquidity < 30 and r.ob_liquidity > 0:
            bonus -= 2.0

        return max(-8.0, min(8.0, bonus))
