"""Stochastic Oscillator and Stochastic RSI indicators."""
import numpy as np
import pandas as pd
from indicators.base import Indicator


class StochasticOscillator(Indicator):
    """Stochastic Oscillator (%K, %D).
    Overbought > 80, Oversold < 20. %K/%D crossovers generate signals."""

    def __init__(self, period: int = 14, k_smooth: int = 3, d_smooth: int = 3):
        super().__init__(f"Stochastic({period})")
        self.period = period
        self.k_smooth = k_smooth
        self.d_smooth = d_smooth
        self._k = 0.0
        self._d = 0.0
        self._prev_k = 0.0
        self._prev_d = 0.0

    def compute(self, df: pd.DataFrame) -> None:
        self._prev_k = self._k
        self._prev_d = self._d
        high = df["high"].rolling(self.period).max()
        low = df["low"].rolling(self.period).min()
        raw_k = 100 * (df["close"] - low) / (high - low + 1e-10)
        k = raw_k.rolling(self.k_smooth).mean()
        d = k.rolling(self.d_smooth).mean()
        self._k = k.iloc[-1] if not k.empty else 0.0
        self._d = d.iloc[-1] if not d.empty else 0.0

    @property
    def value(self):
        return self._k

    @property
    def prev_value(self):
        return self._prev_k

    @property
    def k(self):
        return self._k

    @property
    def d(self):
        return self._d

    def get_values(self) -> dict:
        return {
            "Stoch_K": round(self._k, 2),
            "Stoch_D": round(self._d, 2),
        }


class StochasticRSI(Indicator):
    """Stochastic RSI - applies Stochastic formula to RSI values.
    More sensitive than plain RSI. Buy when %K crosses above %D below 20."""

    def __init__(self, rsi_period: int = 14, stoch_period: int = 14,
                 k_smooth: int = 3, d_smooth: int = 3):
        super().__init__(f"StochRSI({rsi_period},{stoch_period})")
        self.rsi_period = rsi_period
        self.stoch_period = stoch_period
        self.k_smooth = k_smooth
        self.d_smooth = d_smooth
        self._k = 0.0
        self._d = 0.0
        self._prev_k = 0.0
        self._prev_d = 0.0

    def compute(self, df: pd.DataFrame) -> None:
        self._prev_k = self._k
        self._prev_d = self._d
        # Compute RSI first
        delta = df["close"].diff()
        gain = delta.where(delta > 0, 0.0)
        loss = (-delta).where(delta < 0, 0.0)
        avg_gain = gain.ewm(alpha=1 / self.rsi_period, min_periods=self.rsi_period).mean()
        avg_loss = loss.ewm(alpha=1 / self.rsi_period, min_periods=self.rsi_period).mean()
        rs = avg_gain / (avg_loss + 1e-10)
        rsi = 100 - (100 / (1 + rs))
        # Apply Stochastic to RSI
        rsi_low = rsi.rolling(self.stoch_period).min()
        rsi_high = rsi.rolling(self.stoch_period).max()
        stoch_rsi = (rsi - rsi_low) / (rsi_high - rsi_low + 1e-10)
        k = stoch_rsi.rolling(self.k_smooth).mean() * 100
        d = k.rolling(self.d_smooth).mean()
        self._k = k.iloc[-1] if not k.empty else 0.0
        self._d = d.iloc[-1] if not d.empty else 0.0

    @property
    def value(self):
        return self._k

    @property
    def prev_value(self):
        return self._prev_k

    def get_values(self) -> dict:
        return {
            "StochRSI_K": round(self._k, 2),
            "StochRSI_D": round(self._d, 2),
        }
