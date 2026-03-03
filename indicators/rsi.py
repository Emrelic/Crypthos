import pandas as pd
from indicators.base import Indicator


class RSI(Indicator):
    def __init__(self, period: int = 14):
        super().__init__(f"RSI({period})", {"period": period})
        self.period = period

    def compute(self, df: pd.DataFrame) -> pd.Series:
        delta = df["close"].diff()
        gain = delta.where(delta > 0, 0.0)
        loss = (-delta).where(delta < 0, 0.0)
        avg_gain = gain.ewm(alpha=1 / self.period, min_periods=self.period).mean()
        avg_loss = loss.ewm(alpha=1 / self.period, min_periods=self.period).mean()
        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))
        self._prev_value = rsi.iloc[-2] if len(rsi) >= 2 else None
        self._last_value = rsi.iloc[-1] if len(rsi) >= 1 else None
        self._series = rsi
        return rsi
