import pandas as pd
from indicators.base import Indicator


class SMA(Indicator):
    def __init__(self, period: int = 20):
        super().__init__(f"SMA({period})", {"period": period})
        self.period = period

    def compute(self, df: pd.DataFrame) -> pd.Series:
        sma = df["close"].rolling(window=self.period).mean()
        self._prev_value = sma.iloc[-2] if len(sma) >= 2 else None
        self._last_value = sma.iloc[-1] if len(sma) >= 1 else None
        return sma


class EMA(Indicator):
    def __init__(self, period: int = 20):
        super().__init__(f"EMA({period})", {"period": period})
        self.period = period

    def compute(self, df: pd.DataFrame) -> pd.Series:
        ema = df["close"].ewm(span=self.period, adjust=False).mean()
        self._prev_value = ema.iloc[-2] if len(ema) >= 2 else None
        self._last_value = ema.iloc[-1] if len(ema) >= 1 else None
        return ema
