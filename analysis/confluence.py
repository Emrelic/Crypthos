"""Confluence Scoring System - Option C: Dual-Philosophy with Conflict Detection.

Two indicator groups scored separately:
  TREND group:     MACD, ADX, Price_vs_SMA (orthogonality audit: 3 independent signals)
  MEAN-REV group:  RSI, BB (orthogonality audit: 2 independent signals)

Volume (OBV, CMF) is a confirmation layer added to the active group.

Decision logic:
  Both same direction     → combine (strong agreement)
  One strong, other neutral → use the strong one
  Both strong, opposite   → NEUTRAL (conflict, skip — falling knife protection)
"""
from loguru import logger

# Neutral threshold: a group with |score| below this is considered inactive
GROUP_NEUTRAL_THRESHOLD = 2.0


class ConfluenceScorer:
    """Scores buy/sell signals using dual-philosophy conflict detection.

    Trend and mean-reversion indicators are scored separately.
    Only trades when groups agree or one dominates.
    """

    def __init__(self, threshold: float = 4.0, config=None):
        self.threshold = threshold
        self._config = config

    def score(self, indicator_values: dict, regime_weights: dict = None) -> dict:
        """Calculate confluence score with dual-philosophy logic.

        Returns:
            score: float (positive = bullish, negative = bearish)
            signal: BUY/SELL/NEUTRAL
            strength: 0.0-1.0
            details: dict of per-indicator scores
            trend_score: float (trend group total)
            reversion_score: float (mean-rev group total)
            volume_score: float (volume confirmation total)
            active_group: "TREND" / "REVERSION" / "BOTH" / "CONFLICT"
        """
        weights = regime_weights or {}

        # ══════════════════════════════════════════════
        # TREND GROUP: trend-following indicators
        # ══════════════════════════════════════════════
        trend_details = {}
        trend_score = 0.0

        # MACD (boosted: ±2.5 max, was ±2.0)
        macd_hist = indicator_values.get("MACD_histogram", 0)
        w = weights.get("MACD", 1.0)
        if indicator_values.get("MACD_bullish_cross"):
            s = 2.5
        elif indicator_values.get("MACD_bearish_cross"):
            s = -2.5
        elif macd_hist > 0:
            s = 0.7
        elif macd_hist < 0:
            s = -0.7
        else:
            s = 0.0
        trend_details["MACD"] = round(s * w, 2)
        trend_score += s * w

        # ADX + DI (guclu trend: ±2.0, orta: ±1.0, erken trend: ±0.5)
        adx = indicator_values.get("ADX", 0)
        plus_di = indicator_values.get("ADX_plus_DI", 0)
        minus_di = indicator_values.get("ADX_minus_DI", 0)
        di_momentum = indicator_values.get("DI_momentum", 0)
        di_threshold = self._config.get("indicators.adx_di_momentum_threshold", 5) if self._config else 5
        w = weights.get("ADX", 1.0)
        if adx > 30:
            s = 2.0 if plus_di > minus_di else -2.0
        elif adx > 22:
            s = 1.0 if plus_di > minus_di else -1.0
        elif di_momentum > di_threshold:
            # ADX dusuk ama DI farki hizla aciliyor — trend basliyor
            s = 0.5 if plus_di > minus_di else -0.5
        else:
            s = 0.0
        trend_details["ADX"] = round(s * w, 2)
        trend_score += s * w

        # Price vs SMA (taze cross: ±1.5, eski pozisyon: ±0.7)
        price = indicator_values.get("Price", 0)
        sma_slow = indicator_values.get("SMA_slow", 0)
        if price > 0 and sma_slow > 0:
            direction = 1.0 if price > sma_slow else -1.0
            if indicator_values.get("SMA_fresh_cross", False):
                s = 1.5 * direction  # taze cross — guclu sinyal
            else:
                s = 0.7 * direction  # coktan kesmiş — trend hala gecerli ama eski
            trend_details["Price_vs_SMA"] = round(s, 2)
            trend_score += s

        # EMA50 Golden/Death Cross (taze cross: ±1.0, eski pozisyon: ±0.4)
        ema_fast = indicator_values.get("EMA_fast", 0)  # EMA20
        ema50 = indicator_values.get("EMA50", 0)
        if ema_fast > 0 and ema50 > 0:
            if ema_fast > ema50:
                direction = 1.0
            elif ema_fast < ema50:
                direction = -1.0
            else:
                direction = 0.0
            if direction != 0.0:
                if indicator_values.get("EMA_fresh_cross", False):
                    s = 1.0 * direction   # taze golden/death cross
                else:
                    s = 0.4 * direction   # coktan kesmiş, hala gecerli ama eski
            else:
                s = 0.0
            trend_details["EMA50"] = round(s, 2)
            trend_score += s

        # Support/Resistance Position
        sr_pos = indicator_values.get("SR_position", "")
        sr_dist_sup = indicator_values.get("SR_distance_support_pct", 50)
        sr_dist_res = indicator_values.get("SR_distance_resistance_pct", 50)
        s = 0.0
        if sr_pos == "NEAR_SUPPORT":
            s = 1.0   # near support = good for long
        elif sr_pos == "NEAR_RESISTANCE":
            s = -1.0  # near resistance = good for short
        elif sr_pos == "BREAKOUT":
            s = 0.5   # above all resistance = bullish
        trend_details["SR"] = round(s, 2)
        trend_score += s

        trend_score = round(trend_score, 2)

        # ══════════════════════════════════════════════
        # MEAN-REVERSION GROUP: oversold/overbought indicators
        # ══════════════════════════════════════════════
        rev_details = {}
        rev_score = 0.0

        # RSI (boosted: ±2.5 max, was ±2.0)
        rsi = indicator_values.get("RSI", 50)
        w = weights.get("RSI", 1.0)
        if rsi < 25:
            s = 2.5
        elif rsi < 35:
            s = 1.2
        elif rsi > 75:
            s = -2.5
        elif rsi > 65:
            s = -1.2
        else:
            s = 0.0
        rev_details["RSI"] = round(s * w, 2)
        rev_score += s * w

        # Bollinger Bands %B (boosted: ±2.0 max, was ±1.5)
        bb_pctb = indicator_values.get("BB_PercentB", 0.5)
        w = weights.get("BB", 1.0)
        if bb_pctb < 0.0:
            s = 2.0
        elif bb_pctb > 1.0:
            s = -2.0
        elif bb_pctb < 0.2:
            s = 1.2
        elif bb_pctb > 0.8:
            s = -1.2
        else:
            s = 0.0
        rev_details["BB"] = round(s * w, 2)
        rev_score += s * w

        rev_score = round(rev_score, 2)

        # ══════════════════════════════════════════════
        # VOLUME CONFIRMATION (added to active group)
        # ══════════════════════════════════════════════
        vol_details = {}
        vol_score = 0.0

        # OBV slope
        obv_slope = indicator_values.get("OBV_slope", 0)
        w = weights.get("Volume", 1.0)
        s = 1.0 if obv_slope > 0 else (-1.0 if obv_slope < 0 else 0.0)
        vol_details["OBV"] = round(s * w, 2)
        vol_score += s * w

        # CMF
        cmf = indicator_values.get("CMF", 0)
        if cmf > 0.1:
            s = 1.0
        elif cmf < -0.1:
            s = -1.0
        else:
            s = 0.0
        vol_details["CMF"] = round(s, 2)
        vol_score += s

        # CVD (Cumulative Volume Delta) — orderflow signal
        cvd_norm = indicator_values.get("CVD_normalized", 0)
        if cvd_norm > 0.3:
            s = 1.5
        elif cvd_norm > 0.1:
            s = 0.7
        elif cvd_norm < -0.3:
            s = -1.5
        elif cvd_norm < -0.1:
            s = -0.7
        else:
            s = 0.0
        vol_details["CVD"] = round(s, 2)
        vol_score += s

        # VWAP — institutional reference price
        price = indicator_values.get("Price", 0)
        vwap = indicator_values.get("VWAP", 0)
        if price > 0 and vwap > 0:
            vwap_dist = (price - vwap) / vwap * 100  # % distance from VWAP
            if vwap_dist > 0.5:
                s = 0.5   # price above VWAP = bullish
            elif vwap_dist < -0.5:
                s = -0.5  # price below VWAP = bearish
            else:
                s = 0.0
            vol_details["VWAP"] = round(s, 2)
            vol_score += s

        vol_score = round(vol_score, 2)

        # ══════════════════════════════════════════════
        # OPTION C: CONFLICT DETECTION
        # ══════════════════════════════════════════════
        trend_strong = abs(trend_score) >= GROUP_NEUTRAL_THRESHOLD
        rev_strong = abs(rev_score) >= GROUP_NEUTRAL_THRESHOLD

        if trend_strong and rev_strong:
            # Both groups have strong opinions
            trend_bullish = trend_score > 0
            rev_bullish = rev_score > 0
            if trend_bullish == rev_bullish:
                # AGREEMENT: both same direction → combine all
                total = trend_score + rev_score + vol_score
                active_group = "BOTH"
            else:
                # CONFLICT: opposite directions → skip (falling knife protection)
                total = 0.0
                active_group = "CONFLICT"
        elif trend_strong:
            # Only trend group active → use trend + volume
            total = trend_score + vol_score
            active_group = "TREND"
        elif rev_strong:
            # Only mean-rev group active → use mean-rev + volume
            total = rev_score + vol_score
            active_group = "REVERSION"
        else:
            # Neither group strong enough → no trade
            total = 0.0
            active_group = "NEUTRAL"

        total = round(total, 2)

        # Determine signal
        if total >= self.threshold:
            signal = "BUY"
            strength = min(total / (self.threshold * 2), 1.0)
        elif total <= -self.threshold:
            signal = "SELL"
            strength = min(abs(total) / (self.threshold * 2), 1.0)
        else:
            signal = "NEUTRAL"
            strength = 0.0

        # Merge all details for backward compatibility
        details = {}
        details.update(trend_details)
        details.update(rev_details)
        details.update(vol_details)

        bullish = sum(1 for v in details.values() if v > 0)
        bearish = sum(1 for v in details.values() if v < 0)

        return {
            "score": total,
            "signal": signal,
            "strength": round(strength, 2),
            "bullish_count": bullish,
            "bearish_count": bearish,
            "total_indicators": len(details),
            "details": details,
            # New: group-level data
            "trend_score": trend_score,
            "reversion_score": rev_score,
            "volume_score": vol_score,
            "active_group": active_group,
            "trend_details": trend_details,
            "reversion_details": rev_details,
            "volume_details": vol_details,
        }
