from abc import ABC, abstractmethod
import pandas as pd


class Indicator(ABC):
    """Base class for all technical indicators."""

    def __init__(self, name: str, params: dict = None):
        self.name = name
        self.params = params or {}
        self._last_value = None
        self._prev_value = None
        self._value = 0.0
        self._prev_value = 0.0
        self._series = None  # Full series for divergence detection

    @abstractmethod
    def compute(self, df: pd.DataFrame) -> pd.Series:
        """Compute indicator from OHLCV DataFrame. Returns Series."""

    @property
    def value(self):
        return self._last_value

    @property
    def prev_value(self):
        return self._prev_value

    def crossed_above(self, other_value: float) -> bool:
        if self._prev_value is None or self._last_value is None:
            return False
        return self._prev_value <= other_value and self._last_value > other_value

    def crossed_below(self, other_value: float) -> bool:
        if self._prev_value is None or self._last_value is None:
            return False
        return self._prev_value >= other_value and self._last_value < other_value
