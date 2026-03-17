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
        self._macd_series = None
        self._signal_series = None

    def compute(self, df: pd.DataFrame) -> pd.Series:
        ema_fast = df["close"].ewm(span=self.fast, adjust=False).mean()
        ema_slow = df["close"].ewm(span=self.slow, adjust=False).mean()
        macd = ema_fast - ema_slow
        signal = macd.ewm(span=self.signal_period, adjust=False).mean()
        histogram = macd - signal

        self._macd_series = macd
        self._signal_series = signal
        self._prev_macd = macd.iloc[-2] if len(macd) >= 2 else None
        self._prev_signal = signal.iloc[-2] if len(signal) >= 2 else None
        self.macd_line = macd.iloc[-1] if len(macd) >= 1 else None
        self.signal_line = signal.iloc[-1] if len(signal) >= 1 else None
        self.histogram = histogram.iloc[-1] if len(histogram) >= 1 else None
        self._last_value = self.macd_line
        self._prev_value = self._prev_macd
        return macd

    def bullish_crossover(self, lookback: int = 1) -> bool:
        """Son lookback mum icinde bullish cross oldu mu?"""
        if self._macd_series is None or self._signal_series is None:
            return False
        if len(self._macd_series) < lookback + 1:
            return False
        # Son lookback mum icerisinde herhangi birinde cross olduysa True
        for i in range(1, lookback + 1):
            idx = -i
            prev_idx = idx - 1
            if abs(prev_idx) > len(self._macd_series):
                break
            prev_m = self._macd_series.iloc[prev_idx]
            prev_s = self._signal_series.iloc[prev_idx]
            curr_m = self._macd_series.iloc[idx]
            curr_s = self._signal_series.iloc[idx]
            if prev_m <= prev_s and curr_m > curr_s:
                return True
        return False

    def bearish_crossover(self, lookback: int = 1) -> bool:
        """Son lookback mum icinde bearish cross oldu mu?"""
        if self._macd_series is None or self._signal_series is None:
            return False
        if len(self._macd_series) < lookback + 1:
            return False
        for i in range(1, lookback + 1):
            idx = -i
            prev_idx = idx - 1
            if abs(prev_idx) > len(self._macd_series):
                break
            prev_m = self._macd_series.iloc[prev_idx]
            prev_s = self._signal_series.iloc[prev_idx]
            curr_m = self._macd_series.iloc[idx]
            curr_s = self._signal_series.iloc[idx]
            if prev_m >= prev_s and curr_m < curr_s:
                return True
        return False
