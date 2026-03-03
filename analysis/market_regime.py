"""Market Regime Detection - classifies market as trending, ranging, or volatile.
Uses ADX, Bollinger Band width, and ATR percentile."""
from loguru import logger


class MarketRegimeDetector:
    """Detects current market regime and recommends strategy type."""

    # Regime types
    TRENDING = "TRENDING"
    RANGING = "RANGING"
    VOLATILE = "VOLATILE"
    BREAKOUT = "BREAKOUT"

    def detect(self, indicator_values: dict) -> dict:
        """Detect market regime from indicator values.

        Returns dict with:
            regime: TRENDING/RANGING/VOLATILE/BREAKOUT
            trend_direction: UP/DOWN/NONE
            confidence: 0.0-1.0
            recommended_strategies: list of strategy types
            indicator_weights: dict of weight adjustments
        """
        adx = indicator_values.get("ADX", 0)
        plus_di = indicator_values.get("ADX_plus_DI", 0)
        minus_di = indicator_values.get("ADX_minus_DI", 0)
        bb_width = indicator_values.get("BB_Width", 0)
        atr = indicator_values.get("ATR", 0)
        supertrend_trend = indicator_values.get("Supertrend_trend", "")
        psar_trend = indicator_values.get("PSAR_trend", "")

        # Determine trend direction
        if plus_di > minus_di:
            trend_dir = "UP"
        elif minus_di > plus_di:
            trend_dir = "DOWN"
        else:
            trend_dir = "NONE"

        # Regime classification
        regime = self.RANGING
        confidence = 0.5

        if adx > 30:
            # Strong trend
            regime = self.TRENDING
            confidence = min(adx / 50, 1.0)
        elif adx > 20:
            # Moderate trend
            if bb_width < 2.0:
                # Low volatility + moderate ADX = possible breakout forming
                regime = self.BREAKOUT
                confidence = 0.6
            else:
                regime = self.TRENDING
                confidence = adx / 40
        elif adx < 15:
            if bb_width > 5.0:
                regime = self.VOLATILE
                confidence = min(bb_width / 8, 1.0)
            elif bb_width < 1.5:
                regime = self.BREAKOUT
                confidence = 0.7
            else:
                regime = self.RANGING
                confidence = 1.0 - (adx / 20)

        # Strategy recommendations per regime
        strategy_map = {
            self.TRENDING: ["trend_following", "momentum", "supertrend"],
            self.RANGING: ["mean_reversion", "grid", "rsi_reversal"],
            self.VOLATILE: ["scalping", "atr_breakout"],
            self.BREAKOUT: ["breakout", "donchian", "bollinger_squeeze"],
        }

        # Indicator weight adjustments per regime
        weight_map = {
            self.TRENDING: {
                "MACD": 1.5, "ADX": 1.5, "Supertrend": 1.5,
                "EMA_fast": 1.3, "RSI": 0.7, "BB": 0.5,
            },
            self.RANGING: {
                "RSI": 1.5, "BB": 1.5, "Stoch": 1.3,
                "MACD": 0.5, "ADX": 0.5, "Supertrend": 0.5,
            },
            self.VOLATILE: {
                "ATR": 1.5, "BB": 1.3, "Volume": 1.3,
                "RSI": 0.8, "MACD": 0.8,
            },
            self.BREAKOUT: {
                "BB_Width": 1.5, "DC": 1.5, "Volume": 1.5,
                "ATR": 1.3, "RSI": 0.7,
            },
        }

        return {
            "regime": regime,
            "trend_direction": trend_dir,
            "confidence": round(confidence, 2),
            "adx": adx,
            "bb_width": bb_width,
            "recommended_strategies": strategy_map.get(regime, []),
            "indicator_weights": weight_map.get(regime, {}),
        }
