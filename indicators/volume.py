"""Volume indicators: OBV, VWAP, CMF, A/D Line, Elder Force Index."""
import numpy as np
import pandas as pd
from indicators.base import Indicator


class OBV(Indicator):
    """On-Balance Volume - cumulative volume based on price direction.
    Rising OBV + rising price = confirmed trend. Divergence = reversal signal."""

    def __init__(self):
        super().__init__("OBV")
        self._obv_series = None

    def compute(self, df: pd.DataFrame) -> None:
        self._prev_value = self._value
        close = df["close"]
        volume = df["volume"]
        direction = np.sign(close.diff())
        obv = (direction * volume).cumsum()
        self._value = obv.iloc[-1] if not obv.empty else 0.0
        self._series = obv
        # Store slope for signal
        if len(obv) >= 5:
            self._obv_slope = obv.iloc[-1] - obv.iloc[-5]
        else:
            self._obv_slope = 0.0

    def get_values(self) -> dict:
        return {
            "OBV": round(self._value, 0),
            "OBV_slope": round(self._obv_slope, 0) if hasattr(self, '_obv_slope') else 0,
        }


class VWAP(Indicator):
    """Volume Weighted Average Price.
    Price above VWAP = bullish, below = bearish. Institutional benchmark."""

    def __init__(self):
        super().__init__("VWAP")

    def compute(self, df: pd.DataFrame) -> None:
        self._prev_value = self._value
        tp = (df["high"] + df["low"] + df["close"]) / 3
        cum_vol = df["volume"].cumsum()
        cum_tp_vol = (tp * df["volume"]).cumsum()
        vwap = cum_tp_vol / (cum_vol + 1e-10)
        self._value = vwap.iloc[-1] if not vwap.empty else 0.0

    def get_values(self) -> dict:
        return {"VWAP": round(self._value, 6)}


class CMF(Indicator):
    """Chaikin Money Flow - accumulation/distribution over N periods.
    CMF > 0 = buying pressure, < 0 = selling pressure."""

    def __init__(self, period: int = 20):
        super().__init__(f"CMF({period})")
        self.period = period

    def compute(self, df: pd.DataFrame) -> None:
        self._prev_value = self._value
        high, low, close, volume = df["high"], df["low"], df["close"], df["volume"]
        clv = ((close - low) - (high - close)) / (high - low + 1e-10)
        mfv = clv * volume
        cmf = mfv.rolling(self.period).sum() / (volume.rolling(self.period).sum() + 1e-10)
        self._value = cmf.iloc[-1] if not cmf.empty else 0.0

    def get_values(self) -> dict:
        return {"CMF": round(self._value, 4)}


class ADLine(Indicator):
    """Accumulation/Distribution Line.
    Rising = accumulation, falling = distribution. Divergence from price = reversal."""

    def __init__(self):
        super().__init__("AD_Line")

    def compute(self, df: pd.DataFrame) -> None:
        self._prev_value = self._value
        high, low, close, volume = df["high"], df["low"], df["close"], df["volume"]
        clv = ((close - low) - (high - close)) / (high - low + 1e-10)
        ad = (clv * volume).cumsum()
        self._value = ad.iloc[-1] if not ad.empty else 0.0

    def get_values(self) -> dict:
        return {"AD_Line": round(self._value, 0)}


class ElderForceIndex(Indicator):
    """Elder Force Index - combines price change and volume.
    Positive = bulls, negative = bears. Divergence = reversal."""

    def __init__(self, period: int = 13):
        super().__init__(f"ElderForce({period})")
        self.period = period

    def compute(self, df: pd.DataFrame) -> None:
        self._prev_value = self._value
        force = df["close"].diff() * df["volume"]
        smoothed = force.ewm(span=self.period).mean()
        self._value = smoothed.iloc[-1] if not smoothed.empty else 0.0

    def get_values(self) -> dict:
        return {"ElderForce": round(self._value, 2)}
