import pandas as pd
from indicators.base import Indicator


class MACD(Indicator):
    def __init__(self, fast: int = 12, slow: int = 26, signal: int = 9):
        super().__init__(
            f"MACD({fast},{slow},{signal})",
            {"fast": fast, "slow": slow, "signal": signal},
        )
        self.fast = fast
        self.slow = slow
        self.signal_period = signal
        self.macd_line = None
        self.signal_line = None
        self.histogram = None
        self._prev_macd = None
        self._prev_signal = None

    def compute(self, df: pd.DataFrame) -> pd.Series:
        ema_fast = df["close"].ewm(span=self.fast, adjust=False).mean()
        ema_slow = df["close"].ewm(span=self.slow, adjust=False).mean()
        macd = ema_fast - ema_slow
        signal = macd.ewm(span=self.signal_period, adjust=False).mean()
        histogram = macd - signal

        self._prev_macd = macd.iloc[-2] if len(macd) >= 2 else None
        self._prev_signal = signal.iloc[-2] if len(signal) >= 2 else None
        self.macd_line = macd.iloc[-1] if len(macd) >= 1 else None
        self.signal_line = signal.iloc[-1] if len(signal) >= 1 else None
        self.histogram = histogram.iloc[-1] if len(histogram) >= 1 else None
        self._last_value = self.macd_line
        self._prev_value = self._prev_macd
        return macd

    def bullish_crossover(self) -> bool:
        if self._prev_macd is None or self._prev_signal is None:
            return False
        return (self._prev_macd <= self._prev_signal and
                self.macd_line > self.signal_line)

    def bearish_crossover(self) -> bool:
        if self._prev_macd is None or self._prev_signal is None:
            return False
        return (self._prev_macd >= self._prev_signal and
                self.macd_line < self.signal_line)
