"""Confluence Scoring System - combines multiple indicators into a single score.
Only trades when enough indicators agree (reduces false signals)."""
from loguru import logger


class ConfluenceScorer:
    """Scores buy/sell signals across all indicators.

    Each indicator contributes -2 to +2.
    Positive total = buy signal, negative = sell signal.
    Only trade when |score| >= threshold.
    """

    def __init__(self, threshold: float = 4.0):
        self.threshold = threshold

    def score(self, indicator_values: dict, regime_weights: dict = None) -> dict:
        """Calculate confluence score.

        Returns:
            score: float (positive = bullish, negative = bearish)
            signal: BUY/SELL/NEUTRAL
            strength: 0.0-1.0
            details: dict of per-indicator scores
        """
        weights = regime_weights or {}
        details = {}
        total = 0.0

        # --- Momentum ---
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
        details["RSI"] = round(s * w, 2)
        total += s * w

        # StochRSI
        stoch_k = indicator_values.get("StochRSI_K", 50)
        stoch_d = indicator_values.get("StochRSI_D", 50)
        if stoch_k < 20 and stoch_k > stoch_d:
            s = 1.5
        elif stoch_k > 80 and stoch_k < stoch_d:
            s = -1.5
        else:
            s = 0.0
        details["StochRSI"] = round(s, 2)
        total += s

        # MFI (volume-weighted RSI)
        mfi = indicator_values.get("MFI", 50)
        if mfi < 20:
            s = 1.5
        elif mfi > 80:
            s = -1.5
        else:
            s = 0.0
        details["MFI"] = round(s, 2)
        total += s

        # --- Trend ---
        macd_line = indicator_values.get("MACD_line", 0)
        macd_signal = indicator_values.get("MACD_signal", 0)
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
        details["MACD"] = round(s * w, 2)
        total += s * w

        # ADX + DI
        adx = indicator_values.get("ADX", 0)
        plus_di = indicator_values.get("ADX_plus_DI", 0)
        minus_di = indicator_values.get("ADX_minus_DI", 0)
        w = weights.get("ADX", 1.0)
        if adx > 25:
            if plus_di > minus_di:
                s = 1.5
            else:
                s = -1.5
        else:
            s = 0.0
        details["ADX"] = round(s * w, 2)
        total += s * w

        # Supertrend
        st_trend = indicator_values.get("Supertrend_trend", "")
        w = weights.get("Supertrend", 1.0)
        if st_trend == "UP":
            s = 1.5
        elif st_trend == "DOWN":
            s = -1.5
        else:
            s = 0.0
        details["Supertrend"] = round(s * w, 2)
        total += s * w

        # Parabolic SAR
        psar_trend = indicator_values.get("PSAR_trend", "")
        if psar_trend == "UP":
            s = 1.0
        elif psar_trend == "DOWN":
            s = -1.0
        else:
            s = 0.0
        details["PSAR"] = round(s, 2)
        total += s

        # Ichimoku
        ichi_pos = indicator_values.get("Ichimoku_Position", "")
        if ichi_pos == "ABOVE":
            s = 1.5
        elif ichi_pos == "BELOW":
            s = -1.5
        else:
            s = 0.0
        details["Ichimoku"] = round(s, 2)
        total += s

        # --- Volatility ---
        bb_pctb = indicator_values.get("BB_PercentB", 0.5)
        w = weights.get("BB", 1.0)
        if bb_pctb < 0.0:
            s = 1.5  # Below lower band = oversold
        elif bb_pctb > 1.0:
            s = -1.5  # Above upper band = overbought
        elif bb_pctb < 0.2:
            s = 1.0
        elif bb_pctb > 0.8:
            s = -1.0
        else:
            s = 0.0
        details["BB"] = round(s * w, 2)
        total += s * w

        # --- Volume ---
        obv_slope = indicator_values.get("OBV_slope", 0)
        w = weights.get("Volume", 1.0)
        if obv_slope > 0:
            s = 1.0
        elif obv_slope < 0:
            s = -1.0
        else:
            s = 0.0
        details["OBV"] = round(s * w, 2)
        total += s * w

        cmf = indicator_values.get("CMF", 0)
        if cmf > 0.1:
            s = 1.0
        elif cmf < -0.1:
            s = -1.0
        else:
            s = 0.0
        details["CMF"] = round(s, 2)
        total += s

        # --- Price vs MAs ---
        price = indicator_values.get("Price", 0)
        sma_slow = indicator_values.get("SMA_slow", 0)
        ema_fast = indicator_values.get("EMA_fast", 0)
        if price > 0 and sma_slow > 0:
            if price > sma_slow:
                s = 1.0
            else:
                s = -1.0
            details["Price_vs_SMA200"] = round(s, 2)
            total += s

        # Determine signal
        total = round(total, 2)
        if total >= self.threshold:
            signal = "BUY"
            strength = min(total / (self.threshold * 2), 1.0)
        elif total <= -self.threshold:
            signal = "SELL"
            strength = min(abs(total) / (self.threshold * 2), 1.0)
        else:
            signal = "NEUTRAL"
            strength = 0.0

        # Count agreeing indicators
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
        }
