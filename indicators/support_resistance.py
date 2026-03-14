"""Support/Resistance Level Detection Indicator."""
import numpy as np
import pandas as pd
from indicators.base import Indicator


class SupportResistance(Indicator):
    """Detects support/resistance levels from swing highs/lows.

    Outputs:
        SR_nearest_support: nearest support level below current price
        SR_nearest_resistance: nearest resistance level above current price
        SR_distance_support_pct: % distance to nearest support
        SR_distance_resistance_pct: % distance to nearest resistance
        SR_position: NEAR_SUPPORT / NEAR_RESISTANCE / MID_RANGE / BREAKOUT
    """

    def __init__(self, lookback: int = 50, order: int = 5):
        super().__init__(f"SR({lookback},{order})", {"lookback": lookback, "order": order})
        self.lookback = lookback
        self.order = order
        self._nearest_support = 0.0
        self._nearest_resistance = 0.0
        self._dist_support_pct = 0.0
        self._dist_resistance_pct = 0.0
        self._position = "MID_RANGE"

    def compute(self, df: pd.DataFrame) -> pd.Series:
        if df is None or len(df) < self.lookback:
            return pd.Series(dtype=float)

        # Use last `lookback` candles
        data = df.tail(self.lookback).reset_index(drop=True)
        highs = data["high"].values
        lows = data["low"].values
        price = data["close"].iloc[-1]

        # Find swing highs (local maxima)
        swing_highs = []
        for i in range(self.order, len(highs) - self.order):
            if all(highs[i] >= highs[i - j] for j in range(1, self.order + 1)) and \
               all(highs[i] >= highs[i + j] for j in range(1, self.order + 1)):
                swing_highs.append(highs[i])

        # Find swing lows (local minima)
        swing_lows = []
        for i in range(self.order, len(lows) - self.order):
            if all(lows[i] <= lows[i - j] for j in range(1, self.order + 1)) and \
               all(lows[i] <= lows[i + j] for j in range(1, self.order + 1)):
                swing_lows.append(lows[i])

        # Combine all levels and cluster nearby ones (within 0.5%)
        all_levels = sorted(swing_highs + swing_lows)
        clustered = self._cluster_levels(all_levels, threshold_pct=0.5)

        # Find nearest support (below price) and resistance (above price)
        supports = [lvl for lvl in clustered if lvl < price]
        resistances = [lvl for lvl in clustered if lvl > price]

        if supports:
            self._nearest_support = supports[-1]  # highest support below price
            self._dist_support_pct = round((price - self._nearest_support) / price * 100, 4)
        else:
            self._nearest_support = 0.0
            self._dist_support_pct = 0.0

        if resistances:
            self._nearest_resistance = resistances[0]  # lowest resistance above price
            self._dist_resistance_pct = round((self._nearest_resistance - price) / price * 100, 4)
        else:
            self._nearest_resistance = 0.0
            self._dist_resistance_pct = 0.0

        # Determine position
        near_threshold = 0.3  # %
        if supports and self._dist_support_pct <= near_threshold:
            self._position = "NEAR_SUPPORT"
        elif resistances and self._dist_resistance_pct <= near_threshold:
            self._position = "NEAR_RESISTANCE"
        elif not resistances and supports:
            self._position = "BREAKOUT"  # price above all resistance levels
        else:
            self._position = "MID_RANGE"

        self._last_value = self._nearest_support
        return pd.Series(dtype=float)

    def _cluster_levels(self, levels: list, threshold_pct: float = 0.5) -> list:
        """Cluster nearby price levels within threshold_pct of each other."""
        if not levels:
            return []

        clustered = []
        cluster = [levels[0]]

        for i in range(1, len(levels)):
            # Check if this level is within threshold of the cluster average
            cluster_avg = sum(cluster) / len(cluster)
            if cluster_avg > 0 and abs(levels[i] - cluster_avg) / cluster_avg * 100 <= threshold_pct:
                cluster.append(levels[i])
            else:
                # Save cluster average and start new cluster
                clustered.append(sum(cluster) / len(cluster))
                cluster = [levels[i]]

        # Don't forget the last cluster
        if cluster:
            clustered.append(sum(cluster) / len(cluster))

        return sorted(clustered)

    def get_values(self) -> dict:
        return {
            "SR_nearest_support": self._nearest_support,
            "SR_nearest_resistance": self._nearest_resistance,
            "SR_distance_support_pct": self._dist_support_pct,
            "SR_distance_resistance_pct": self._dist_resistance_pct,
            "SR_position": self._position,
        }
