"""Divergence Detection - identifies when price and indicators disagree.
Regular divergence = reversal signal, Hidden divergence = continuation."""
import numpy as np
import pandas as pd
from loguru import logger


class DivergenceDetector:
    """Detects bullish/bearish divergences between price and indicators."""

    def __init__(self, lookback: int = 20, min_distance: int = 5):
        self.lookback = lookback
        self.min_distance = min_distance

    def detect_all(self, df: pd.DataFrame, indicator_series: dict) -> list:
        """Detect divergences across multiple indicators.

        Args:
            df: OHLCV DataFrame
            indicator_series: dict of indicator_name -> pd.Series

        Returns:
            list of divergence dicts with type, indicator, strength
        """
        divergences = []
        close = df["close"]

        for name, series in indicator_series.items():
            if series is None or len(series) < self.lookback:
                continue
            try:
                divs = self._detect_divergence(close, series, name)
                divergences.extend(divs)
            except Exception as e:
                logger.debug(f"Divergence detection error for {name}: {e}")

        return divergences

    def _detect_divergence(self, price: pd.Series, indicator: pd.Series,
                           name: str) -> list:
        """Detect divergence between price and a single indicator."""
        results = []
        n = len(price)
        if n < self.lookback:
            return results

        recent_price = price.iloc[-self.lookback:]
        recent_ind = indicator.iloc[-self.lookback:]

        # Find local peaks and troughs
        price_highs = self._find_peaks(recent_price)
        price_lows = self._find_troughs(recent_price)
        ind_highs = self._find_peaks(recent_ind)
        ind_lows = self._find_troughs(recent_ind)

        # Regular Bullish Divergence: price lower low, indicator higher low
        if len(price_lows) >= 2 and len(ind_lows) >= 2:
            p1, p2 = price_lows[-2], price_lows[-1]
            i1, i2 = ind_lows[-2], ind_lows[-1]
            if (recent_price.iloc[p2] < recent_price.iloc[p1] and
                    recent_ind.iloc[i2] > recent_ind.iloc[i1]):
                results.append({
                    "type": "REGULAR_BULLISH",
                    "indicator": name,
                    "signal": "BUY",
                    "strength": self._calc_strength(
                        recent_price.iloc[p1], recent_price.iloc[p2],
                        recent_ind.iloc[i1], recent_ind.iloc[i2]),
                })

        # Regular Bearish Divergence: price higher high, indicator lower high
        if len(price_highs) >= 2 and len(ind_highs) >= 2:
            p1, p2 = price_highs[-2], price_highs[-1]
            i1, i2 = ind_highs[-2], ind_highs[-1]
            if (recent_price.iloc[p2] > recent_price.iloc[p1] and
                    recent_ind.iloc[i2] < recent_ind.iloc[i1]):
                results.append({
                    "type": "REGULAR_BEARISH",
                    "indicator": name,
                    "signal": "SELL",
                    "strength": self._calc_strength(
                        recent_price.iloc[p1], recent_price.iloc[p2],
                        recent_ind.iloc[i1], recent_ind.iloc[i2]),
                })

        # Hidden Bullish: price higher low, indicator lower low (trend continuation)
        if len(price_lows) >= 2 and len(ind_lows) >= 2:
            p1, p2 = price_lows[-2], price_lows[-1]
            i1, i2 = ind_lows[-2], ind_lows[-1]
            if (recent_price.iloc[p2] > recent_price.iloc[p1] and
                    recent_ind.iloc[i2] < recent_ind.iloc[i1]):
                results.append({
                    "type": "HIDDEN_BULLISH",
                    "indicator": name,
                    "signal": "BUY",
                    "strength": self._calc_strength(
                        recent_price.iloc[p1], recent_price.iloc[p2],
                        recent_ind.iloc[i1], recent_ind.iloc[i2]) * 0.7,
                })

        # Hidden Bearish: price lower high, indicator higher high
        if len(price_highs) >= 2 and len(ind_highs) >= 2:
            p1, p2 = price_highs[-2], price_highs[-1]
            i1, i2 = ind_highs[-2], ind_highs[-1]
            if (recent_price.iloc[p2] < recent_price.iloc[p1] and
                    recent_ind.iloc[i2] > recent_ind.iloc[i1]):
                results.append({
                    "type": "HIDDEN_BEARISH",
                    "indicator": name,
                    "signal": "SELL",
                    "strength": self._calc_strength(
                        recent_price.iloc[p1], recent_price.iloc[p2],
                        recent_ind.iloc[i1], recent_ind.iloc[i2]) * 0.7,
                })

        return results

    @staticmethod
    def _find_peaks(series: pd.Series, order: int = 3) -> list:
        """Find local maxima indices."""
        peaks = []
        values = series.values
        for i in range(order, len(values) - order):
            if all(values[i] >= values[i - j] for j in range(1, order + 1)) and \
               all(values[i] >= values[i + j] for j in range(1, order + 1)):
                peaks.append(i)
        return peaks

    @staticmethod
    def _find_troughs(series: pd.Series, order: int = 3) -> list:
        """Find local minima indices."""
        troughs = []
        values = series.values
        for i in range(order, len(values) - order):
            if all(values[i] <= values[i - j] for j in range(1, order + 1)) and \
               all(values[i] <= values[i + j] for j in range(1, order + 1)):
                troughs.append(i)
        return troughs

    @staticmethod
    def _calc_strength(p1, p2, i1, i2) -> float:
        """Calculate divergence strength (0-1) based on magnitude of disagreement."""
        price_change = abs(p2 - p1) / (abs(p1) + 1e-10)
        ind_change = abs(i2 - i1) / (abs(i1) + 1e-10)
        return min((price_change + ind_change) / 2, 1.0)
