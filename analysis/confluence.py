"""Confluence Scoring System - Option C: Dual-Philosophy with Conflict Detection.

Two indicator groups scored separately:
  TREND group:     MACD, ADX, Supertrend, PSAR, Ichimoku, Price_vs_SMA
  MEAN-REV group:  RSI, StochRSI, MFI, BB

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

    def __init__(self, threshold: float = 4.0):
        self.threshold = threshold

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

        # MACD
        macd_hist = indicator_values.get("MACD_histogram", 0)
        w = weights.get("MACD", 1.0)
        if indicator_values.get("MACD_bullish_cross"):
            s = 2.0
        elif indicator_values.get("MACD_bearish_cross"):
            s = -2.0
        elif macd_hist > 0:
            s = 0.5
        elif macd_hist < 0:
            s = -0.5
        else:
            s = 0.0
        trend_details["MACD"] = round(s * w, 2)
        trend_score += s * w

        # ADX + DI
        adx = indicator_values.get("ADX", 0)
        plus_di = indicator_values.get("ADX_plus_DI", 0)
        minus_di = indicator_values.get("ADX_minus_DI", 0)
        w = weights.get("ADX", 1.0)
        if adx > 30:
            s = 1.5 if plus_di > minus_di else -1.5
        elif adx > 22:
            s = 0.75 if plus_di > minus_di else -0.75
        else:
            s = 0.0
        trend_details["ADX"] = round(s * w, 2)
        trend_score += s * w

        # Supertrend
        st_trend = indicator_values.get("Supertrend_trend", "")
        w = weights.get("Supertrend", 1.0)
        if st_trend == "UP":
            s = 1.5
        elif st_trend == "DOWN":
            s = -1.5
        else:
            s = 0.0
        trend_details["Supertrend"] = round(s * w, 2)
        trend_score += s * w

        # Parabolic SAR
        psar_trend = indicator_values.get("PSAR_trend", "")
        s = 1.0 if psar_trend == "UP" else (-1.0 if psar_trend == "DOWN" else 0.0)
        trend_details["PSAR"] = round(s, 2)
        trend_score += s

        # Ichimoku
        ichi_pos = indicator_values.get("Ichimoku_Position", "")
        s = 1.5 if ichi_pos == "ABOVE" else (-1.5 if ichi_pos == "BELOW" else 0.0)
        trend_details["Ichimoku"] = round(s, 2)
        trend_score += s

        # Price vs SMA
        price = indicator_values.get("Price", 0)
        sma_slow = indicator_values.get("SMA_slow", 0)
        if price > 0 and sma_slow > 0:
            s = 1.0 if price > sma_slow else -1.0
            trend_details["Price_vs_SMA"] = round(s, 2)
            trend_score += s

        trend_score = round(trend_score, 2)

        # ══════════════════════════════════════════════
        # MEAN-REVERSION GROUP: oversold/overbought indicators
        # ══════════════════════════════════════════════
        rev_details = {}
        rev_score = 0.0

        # RSI
        rsi = indicator_values.get("RSI", 50)
        w = weights.get("RSI", 1.0)
        if rsi < 25:
            s = 2.0
        elif rsi < 35:
            s = 1.0
        elif rsi > 75:
            s = -2.0
        elif rsi > 65:
            s = -1.0
        else:
            s = 0.0
        rev_details["RSI"] = round(s * w, 2)
        rev_score += s * w

        # StochRSI
        stoch_k = indicator_values.get("StochRSI_K", 50)
        stoch_d = indicator_values.get("StochRSI_D", 50)
        if stoch_k < 20 and stoch_k > stoch_d:
            s = 1.5
        elif stoch_k > 80 and stoch_k < stoch_d:
            s = -1.5
        else:
            s = 0.0
        rev_details["StochRSI"] = round(s, 2)
        rev_score += s

        # MFI
        mfi = indicator_values.get("MFI", 50)
        if mfi < 20:
            s = 1.5
        elif mfi > 80:
            s = -1.5
        else:
            s = 0.0
        rev_details["MFI"] = round(s, 2)
        rev_score += s

        # Bollinger Bands %B
        bb_pctb = indicator_values.get("BB_PercentB", 0.5)
        w = weights.get("BB", 1.0)
        if bb_pctb < 0.0:
            s = 1.5
        elif bb_pctb > 1.0:
            s = -1.5
        elif bb_pctb < 0.2:
            s = 1.0
        elif bb_pctb > 0.8:
            s = -1.0
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
