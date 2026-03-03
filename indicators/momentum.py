"""Momentum indicators: CCI, Williams %R, MFI, ROC, Ultimate Oscillator."""
import numpy as np
import pandas as pd
from indicators.base import Indicator


class CCI(Indicator):
    """Commodity Channel Index.
    Buy when CCI > +100 (new uptrend), sell when CCI < -100 (new downtrend)."""

    def __init__(self, period: int = 20):
        super().__init__(f"CCI({period})")
        self.period = period

    def compute(self, df: pd.DataFrame) -> None:
        self._prev_value = self._value
        tp = (df["high"] + df["low"] + df["close"]) / 3
        sma = tp.rolling(self.period).mean()
        mad = tp.rolling(self.period).apply(lambda x: np.abs(x - x.mean()).mean(), raw=True)
        cci = (tp - sma) / (0.015 * mad + 1e-10)
        self._value = cci.iloc[-1] if not cci.empty else 0.0
        self._series = cci

    def get_values(self) -> dict:
        return {"CCI": round(self._value, 2)}


class WilliamsR(Indicator):
    """Williams %R. Range -100 to 0.
    Overbought above -20, oversold below -80."""

    def __init__(self, period: int = 14):
        super().__init__(f"Williams_R({period})")
        self.period = period

    def compute(self, df: pd.DataFrame) -> None:
        self._prev_value = self._value
        hh = df["high"].rolling(self.period).max()
        ll = df["low"].rolling(self.period).min()
        wr = -100 * (hh - df["close"]) / (hh - ll + 1e-10)
        self._value = wr.iloc[-1] if not wr.empty else 0.0

    def get_values(self) -> dict:
        return {"Williams_R": round(self._value, 2)}


class MFI(Indicator):
    """Money Flow Index - volume-weighted RSI.
    Overbought > 80, oversold < 20."""

    def __init__(self, period: int = 14):
        super().__init__(f"MFI({period})")
        self.period = period

    def compute(self, df: pd.DataFrame) -> None:
        self._prev_value = self._value
        tp = (df["high"] + df["low"] + df["close"]) / 3
        mf = tp * df["volume"]
        delta = tp.diff()
        pos_mf = mf.where(delta > 0, 0.0).rolling(self.period).sum()
        neg_mf = mf.where(delta <= 0, 0.0).rolling(self.period).sum()
        mfr = pos_mf / (neg_mf + 1e-10)
        mfi = 100 - (100 / (1 + mfr))
        self._value = mfi.iloc[-1] if not mfi.empty else 0.0
        self._series = mfi

    def get_values(self) -> dict:
        return {"MFI": round(self._value, 2)}


class ROC(Indicator):
    """Rate of Change - percentage price change over N periods.
    Positive = upward momentum, negative = downward."""

    def __init__(self, period: int = 12):
        super().__init__(f"ROC({period})")
        self.period = period

    def compute(self, df: pd.DataFrame) -> None:
        self._prev_value = self._value
        roc = ((df["close"] - df["close"].shift(self.period))
               / (df["close"].shift(self.period) + 1e-10)) * 100
        self._value = roc.iloc[-1] if not roc.empty else 0.0

    def get_values(self) -> dict:
        return {"ROC": round(self._value, 4)}


class UltimateOscillator(Indicator):
    """Ultimate Oscillator - combines 3 timeframes (7, 14, 28).
    Bullish divergence below 30, bearish above 70."""

    def __init__(self, p1: int = 7, p2: int = 14, p3: int = 28):
        super().__init__(f"UltOsc({p1},{p2},{p3})")
        self.p1, self.p2, self.p3 = p1, p2, p3

    def compute(self, df: pd.DataFrame) -> None:
        self._prev_value = self._value
        close = df["close"]
        low = df["low"]
        prev_close = close.shift(1)
        bp = close - pd.concat([low, prev_close], axis=1).min(axis=1)
        tr = pd.concat([df["high"], prev_close], axis=1).max(axis=1) - \
             pd.concat([low, prev_close], axis=1).min(axis=1)
        avg1 = bp.rolling(self.p1).sum() / (tr.rolling(self.p1).sum() + 1e-10)
        avg2 = bp.rolling(self.p2).sum() / (tr.rolling(self.p2).sum() + 1e-10)
        avg3 = bp.rolling(self.p3).sum() / (tr.rolling(self.p3).sum() + 1e-10)
        uo = 100 * (4 * avg1 + 2 * avg2 + avg3) / 7
        self._value = uo.iloc[-1] if not uo.empty else 0.0

    def get_values(self) -> dict:
        return {"UltOsc": round(self._value, 2)}
