"""Trend indicators: ADX, Parabolic SAR, Supertrend, Ichimoku Cloud, Aroon."""
import numpy as np
import pandas as pd
from indicators.base import Indicator


class ADX(Indicator):
    """Average Directional Index - measures trend strength (not direction).
    ADX > 25 = strong trend, < 20 = weak/ranging. +DI > -DI = bullish."""

    def __init__(self, period: int = 14):
        super().__init__(f"ADX({period})")
        self.period = period
        self._plus_di = 0.0
        self._minus_di = 0.0
        self._plus_di_series = None
        self._minus_di_series = None
        self._adx_series = None

    def compute(self, df: pd.DataFrame) -> None:
        self._prev_value = self._value
        high, low, close = df["high"], df["low"], df["close"]

        plus_dm = high.diff()
        minus_dm = -low.diff()
        plus_dm = plus_dm.where((plus_dm > minus_dm) & (plus_dm > 0), 0.0)
        minus_dm = minus_dm.where((minus_dm > plus_dm) & (minus_dm > 0), 0.0)

        prev_close = close.shift(1)
        tr1 = high - low
        tr2 = (high - prev_close).abs()
        tr3 = (low - prev_close).abs()
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

        atr = tr.ewm(alpha=1 / self.period, min_periods=self.period).mean()
        plus_di = 100 * plus_dm.ewm(alpha=1 / self.period, min_periods=self.period).mean() / (atr + 1e-10)
        minus_di = 100 * minus_dm.ewm(alpha=1 / self.period, min_periods=self.period).mean() / (atr + 1e-10)

        dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di + 1e-10)
        adx = dx.ewm(alpha=1 / self.period, min_periods=self.period).mean()

        self._value = adx.iloc[-1] if not adx.empty else 0.0
        self._plus_di = plus_di.iloc[-1] if not plus_di.empty else 0.0
        self._minus_di = minus_di.iloc[-1] if not minus_di.empty else 0.0
        self._plus_di_series = plus_di
        self._minus_di_series = minus_di
        self._adx_series = adx

    def get_values(self) -> dict:
        return {
            "ADX": round(self._value, 2),
            "ADX_plus_DI": round(self._plus_di, 2),
            "ADX_minus_DI": round(self._minus_di, 2),
        }


class ParabolicSAR(Indicator):
    """Parabolic Stop and Reverse.
    Dots below price = bullish, above = bearish. Flip = trend reversal."""

    def __init__(self, af_start: float = 0.02, af_step: float = 0.02, af_max: float = 0.20):
        super().__init__("ParabolicSAR")
        self.af_start = af_start
        self.af_step = af_step
        self.af_max = af_max
        self._trend = 1  # 1 = up, -1 = down

    def compute(self, df: pd.DataFrame) -> None:
        self._prev_value = self._value
        high = df["high"].values
        low = df["low"].values
        close = df["close"].values
        n = len(close)
        if n < 2:
            return

        sar = np.zeros(n)
        trend = np.ones(n, dtype=int)
        af = self.af_start
        ep = high[0]
        sar[0] = low[0]

        for i in range(1, n):
            sar[i] = sar[i - 1] + af * (ep - sar[i - 1])
            if trend[i - 1] == 1:  # uptrend
                if low[i] < sar[i]:
                    trend[i] = -1
                    sar[i] = ep
                    ep = low[i]
                    af = self.af_start
                else:
                    trend[i] = 1
                    if high[i] > ep:
                        ep = high[i]
                        af = min(af + self.af_step, self.af_max)
            else:  # downtrend
                if high[i] > sar[i]:
                    trend[i] = 1
                    sar[i] = ep
                    ep = high[i]
                    af = self.af_start
                else:
                    trend[i] = -1
                    if low[i] < ep:
                        ep = low[i]
                        af = min(af + self.af_step, self.af_max)

        self._value = sar[-1]
        self._trend = trend[-1]

    def get_values(self) -> dict:
        trend_str = "UP" if self._trend == 1 else "DOWN"
        return {
            "PSAR": round(self._value, 6),
            "PSAR_trend": trend_str,
        }


class Supertrend(Indicator):
    """Supertrend - ATR-based trend indicator.
    Price above line = bullish, below = bearish. Very clean signals."""

    def __init__(self, period: int = 10, multiplier: float = 3.0):
        super().__init__(f"Supertrend({period},{multiplier})")
        self.period = period
        self.multiplier = multiplier
        self._trend = 1

    def compute(self, df: pd.DataFrame) -> None:
        self._prev_value = self._value
        high, low, close = df["high"], df["low"], df["close"]
        hl2 = (high + low) / 2

        prev_close = close.shift(1)
        tr1 = high - low
        tr2 = (high - prev_close).abs()
        tr3 = (low - prev_close).abs()
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        atr = tr.rolling(self.period).mean()

        upper = hl2 + self.multiplier * atr
        lower = hl2 - self.multiplier * atr

        supertrend = pd.Series(0.0, index=df.index)
        direction = pd.Series(1, index=df.index)

        for i in range(1, len(df)):
            if close.iloc[i] > upper.iloc[i - 1]:
                direction.iloc[i] = 1
            elif close.iloc[i] < lower.iloc[i - 1]:
                direction.iloc[i] = -1
            else:
                direction.iloc[i] = direction.iloc[i - 1]

            if direction.iloc[i] == 1:
                supertrend.iloc[i] = max(lower.iloc[i], supertrend.iloc[i - 1]) \
                    if direction.iloc[i - 1] == 1 else lower.iloc[i]
            else:
                supertrend.iloc[i] = min(upper.iloc[i], supertrend.iloc[i - 1]) \
                    if direction.iloc[i - 1] == -1 else upper.iloc[i]

        self._value = supertrend.iloc[-1]
        self._trend = direction.iloc[-1]

    def get_values(self) -> dict:
        trend_str = "UP" if self._trend == 1 else "DOWN"
        return {
            "Supertrend": round(self._value, 6),
            "Supertrend_trend": trend_str,
        }


class IchimokuCloud(Indicator):
    """Ichimoku Kinko Hyo - complete trend/momentum/support-resistance system.
    Price above cloud = bullish, below = bearish. Cloud acts as S/R."""

    def __init__(self, tenkan: int = 9, kijun: int = 26, senkou_b: int = 52):
        super().__init__("Ichimoku")
        self.tenkan_period = tenkan
        self.kijun_period = kijun
        self.senkou_b_period = senkou_b
        self._tenkan = 0.0
        self._kijun = 0.0
        self._senkou_a = 0.0
        self._senkou_b = 0.0
        self._chikou = 0.0

    def compute(self, df: pd.DataFrame) -> None:
        self._prev_value = self._value
        high, low, close = df["high"], df["low"], df["close"]

        tenkan = (high.rolling(self.tenkan_period).max() +
                  low.rolling(self.tenkan_period).min()) / 2
        kijun = (high.rolling(self.kijun_period).max() +
                 low.rolling(self.kijun_period).min()) / 2
        senkou_a = (tenkan + kijun) / 2
        senkou_b = (high.rolling(self.senkou_b_period).max() +
                    low.rolling(self.senkou_b_period).min()) / 2

        self._tenkan = tenkan.iloc[-1] if not tenkan.empty else 0.0
        self._kijun = kijun.iloc[-1] if not kijun.empty else 0.0
        self._senkou_a = senkou_a.iloc[-1] if not senkou_a.empty else 0.0
        self._senkou_b = senkou_b.iloc[-1] if not senkou_b.empty else 0.0
        self._chikou = close.iloc[-1] if not close.empty else 0.0
        # Cloud top = max of senkou A, B
        self._value = close.iloc[-1] if not close.empty else 0.0

    def get_values(self) -> dict:
        cloud_top = max(self._senkou_a, self._senkou_b)
        cloud_bottom = min(self._senkou_a, self._senkou_b)
        above_cloud = self._value > cloud_top
        below_cloud = self._value < cloud_bottom
        position = "ABOVE" if above_cloud else ("BELOW" if below_cloud else "INSIDE")
        return {
            "Ichimoku_Tenkan": round(self._tenkan, 6),
            "Ichimoku_Kijun": round(self._kijun, 6),
            "Ichimoku_SpanA": round(self._senkou_a, 6),
            "Ichimoku_SpanB": round(self._senkou_b, 6),
            "Ichimoku_CloudTop": round(cloud_top, 6),
            "Ichimoku_CloudBot": round(cloud_bottom, 6),
            "Ichimoku_Position": position,
        }


class Aroon(Indicator):
    """Aroon Indicator - measures time since highest high / lowest low.
    Aroon Up > 70, Down < 30 = uptrend. Crossovers signal trend changes."""

    def __init__(self, period: int = 25):
        super().__init__(f"Aroon({period})")
        self.period = period
        self._up = 0.0
        self._down = 0.0

    def compute(self, df: pd.DataFrame) -> None:
        self._prev_value = self._value
        high, low = df["high"], df["low"]
        aroon_up = high.rolling(self.period + 1).apply(
            lambda x: x.argmax() / self.period * 100, raw=True)
        aroon_down = low.rolling(self.period + 1).apply(
            lambda x: x.argmin() / self.period * 100, raw=True)
        self._up = aroon_up.iloc[-1] if not aroon_up.empty else 0.0
        self._down = aroon_down.iloc[-1] if not aroon_down.empty else 0.0
        self._value = self._up - self._down  # oscillator

    def get_values(self) -> dict:
        return {
            "Aroon_Up": round(self._up, 2),
            "Aroon_Down": round(self._down, 2),
            "Aroon_Osc": round(self._value, 2),
        }
