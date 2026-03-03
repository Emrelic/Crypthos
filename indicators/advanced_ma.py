"""Advanced Moving Averages: HMA, DEMA, TEMA, VWMA."""
import numpy as np
import pandas as pd
from indicators.base import Indicator


class HullMA(Indicator):
    """Hull Moving Average - nearly zero-lag moving average.
    Very responsive. Direction change = trend change signal."""

    def __init__(self, period: int = 20):
        super().__init__(f"HMA({period})")
        self.period = period

    def compute(self, df: pd.DataFrame) -> None:
        self._prev_value = self._value
        close = df["close"]
        half = int(self.period / 2)
        sqrt_p = int(np.sqrt(self.period))
        wma_half = close.rolling(half).apply(
            lambda x: np.average(x, weights=range(1, len(x) + 1)), raw=True)
        wma_full = close.rolling(self.period).apply(
            lambda x: np.average(x, weights=range(1, len(x) + 1)), raw=True)
        diff = 2 * wma_half - wma_full
        hma = diff.rolling(sqrt_p).apply(
            lambda x: np.average(x, weights=range(1, len(x) + 1)), raw=True)
        self._value = hma.iloc[-1] if not hma.empty else 0.0

    def get_values(self) -> dict:
        return {"HMA": round(self._value, 6)}


class DEMA(Indicator):
    """Double Exponential Moving Average - reduces lag vs standard EMA.
    Formula: 2*EMA - EMA(EMA)"""

    def __init__(self, period: int = 20):
        super().__init__(f"DEMA({period})")
        self.period = period

    def compute(self, df: pd.DataFrame) -> None:
        self._prev_value = self._value
        close = df["close"]
        ema1 = close.ewm(span=self.period).mean()
        ema2 = ema1.ewm(span=self.period).mean()
        dema = 2 * ema1 - ema2
        self._value = dema.iloc[-1] if not dema.empty else 0.0

    def get_values(self) -> dict:
        return {"DEMA": round(self._value, 6)}


class TEMA(Indicator):
    """Triple Exponential Moving Average - even less lag than DEMA.
    Formula: 3*EMA - 3*EMA(EMA) + EMA(EMA(EMA))"""

    def __init__(self, period: int = 20):
        super().__init__(f"TEMA({period})")
        self.period = period

    def compute(self, df: pd.DataFrame) -> None:
        self._prev_value = self._value
        close = df["close"]
        ema1 = close.ewm(span=self.period).mean()
        ema2 = ema1.ewm(span=self.period).mean()
        ema3 = ema2.ewm(span=self.period).mean()
        tema = 3 * ema1 - 3 * ema2 + ema3
        self._value = tema.iloc[-1] if not tema.empty else 0.0

    def get_values(self) -> dict:
        return {"TEMA": round(self._value, 6)}


class VWMA(Indicator):
    """Volume Weighted Moving Average.
    Price above VWMA = bullish with volume confirmation."""

    def __init__(self, period: int = 20):
        super().__init__(f"VWMA({period})")
        self.period = period

    def compute(self, df: pd.DataFrame) -> None:
        self._prev_value = self._value
        vp = df["close"] * df["volume"]
        vwma = vp.rolling(self.period).sum() / (df["volume"].rolling(self.period).sum() + 1e-10)
        self._value = vwma.iloc[-1] if not vwma.empty else 0.0

    def get_values(self) -> dict:
        return {"VWMA": round(self._value, 6)}
