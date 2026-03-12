"""Volatility indicators: Bollinger Bands, Keltner Channels, Donchian Channels."""
import numpy as np
import pandas as pd
from indicators.base import Indicator


class BollingerBands(Indicator):
    """Bollinger Bands (middle SMA + upper/lower at N std devs).
    Squeeze = impending breakout. %B shows position within bands."""

    def __init__(self, period: int = 20, std_dev: float = 2.0):
        super().__init__(f"BB({period},{std_dev})")
        self.period = period
        self.std_dev = std_dev
        self._upper = 0.0
        self._middle = 0.0
        self._lower = 0.0
        self._bandwidth = 0.0
        self._percent_b = 0.0

    def compute(self, df: pd.DataFrame) -> None:
        self._prev_value = self._value
        close = df["close"]
        self._middle = close.rolling(self.period).mean().iloc[-1]
        std = close.rolling(self.period).std().iloc[-1]
        self._upper = self._middle + self.std_dev * std
        self._lower = self._middle - self.std_dev * std
        self._bandwidth = (self._upper - self._lower) / (self._middle + 1e-10) * 100
        self._percent_b = (close.iloc[-1] - self._lower) / (self._upper - self._lower + 1e-10)
        self._value = close.iloc[-1]

    def get_values(self) -> dict:
        return {
            "BB_Upper": round(self._upper, 6),
            "BB_Middle": round(self._middle, 6),
            "BB_Lower": round(self._lower, 6),
            "BB_Width": round(self._bandwidth, 4),
            "BB_PercentB": round(self._percent_b, 4),
        }


class KeltnerChannels(Indicator):
    """Keltner Channels (EMA + ATR bands).
    Smoother than Bollinger. BB squeeze inside Keltner = powerful breakout setup."""

    def __init__(self, ema_period: int = 20, atr_period: int = 14, multiplier: float = 2.0):
        super().__init__(f"KC({ema_period},{multiplier})")
        self.ema_period = ema_period
        self.atr_period = atr_period
        self.multiplier = multiplier
        self._upper = 0.0
        self._middle = 0.0
        self._lower = 0.0

    def compute(self, df: pd.DataFrame) -> None:
        self._prev_value = self._value
        close = df["close"]
        ema = close.ewm(span=self.ema_period).mean()

        prev_close = close.shift(1)
        tr1 = df["high"] - df["low"]
        tr2 = (df["high"] - prev_close).abs()
        tr3 = (df["low"] - prev_close).abs()
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        atr = tr.rolling(self.atr_period).mean()

        self._middle = ema.iloc[-1]
        self._upper = self._middle + self.multiplier * atr.iloc[-1]
        self._lower = self._middle - self.multiplier * atr.iloc[-1]
        self._value = close.iloc[-1]

    def get_values(self) -> dict:
        return {
            "KC_Upper": round(self._upper, 6),
            "KC_Middle": round(self._middle, 6),
            "KC_Lower": round(self._lower, 6),
        }


class DonchianChannels(Indicator):
    """Donchian Channels - highest high / lowest low over N periods.
    Breakout above upper = buy, below lower = sell (Turtle Trading)."""

    def __init__(self, period: int = 20):
        super().__init__(f"Donchian({period})")
        self.period = period
        self._upper = 0.0
        self._middle = 0.0
        self._lower = 0.0

    def compute(self, df: pd.DataFrame) -> None:
        self._prev_value = self._value
        self._upper = df["high"].rolling(self.period).max().iloc[-1]
        self._lower = df["low"].rolling(self.period).min().iloc[-1]
        self._middle = (self._upper + self._lower) / 2
        self._value = df["close"].iloc[-1]

    def get_values(self) -> dict:
        return {
            "DC_Upper": round(self._upper, 6),
            "DC_Middle": round(self._middle, 6),
            "DC_Lower": round(self._lower, 6),
        }


class ATR(Indicator):
    """Average True Range - measures volatility.
    High ATR = volatile, low ATR = calm. Used for dynamic stops & position sizing."""

    def __init__(self, period: int = 14):
        super().__init__(f"ATR({period})")
        self.period = period

    def compute(self, df: pd.DataFrame) -> None:
        self._prev_value = self._value
        prev_close = df["close"].shift(1)
        tr1 = df["high"] - df["low"]
        tr2 = (df["high"] - prev_close).abs()
        tr3 = (df["low"] - prev_close).abs()
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        # Wilder smoothing (EMA with alpha=1/period) — industry standard
        # More reactive to sudden volatility spikes than SMA
        atr = tr.ewm(alpha=1.0 / self.period, min_periods=self.period).mean()
        self._value = atr.iloc[-1] if not atr.empty else 0.0

    def get_values(self) -> dict:
        return {"ATR": round(self._value, 8)}
