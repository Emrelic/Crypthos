"""Indicator Engine - manages ALL indicators and computes them from kline data."""
import pandas as pd
from loguru import logger
from core.config_manager import ConfigManager

# Core indicators
from indicators.rsi import RSI
from indicators.moving_average import SMA, EMA
from indicators.macd import MACD

# Momentum
from indicators.stochastic import StochasticOscillator, StochasticRSI
from indicators.momentum import CCI, WilliamsR, MFI, ROC, UltimateOscillator

# Trend
from indicators.trend import ADX, ParabolicSAR, Supertrend, IchimokuCloud, Aroon

# Volatility
from indicators.volatility import BollingerBands, KeltnerChannels, DonchianChannels, ATR
from indicators.support_resistance import SupportResistance

# Volume
from indicators.volume import OBV, CVD, VWAP, CMF, ADLine, ElderForceIndex

# Advanced MAs
from indicators.advanced_ma import HullMA, DEMA, TEMA, VWMA


class IndicatorEngine:
    """Manages all indicators. Recomputes when new kline data arrives.

    Total indicators: 30+
    Categories: Momentum, Trend, Volatility, Volume, Moving Averages
    """

    def __init__(self, config: ConfigManager):
        self._config = config
        self._indicators: dict = {}
        self._last_results: dict = {}
        self._setup_all_indicators()

    def _setup_all_indicators(self) -> None:
        cfg = self._config.get("indicators", {})

        # === CORE ===
        self._indicators["RSI"] = RSI(cfg.get("rsi_period", 14))
        self._indicators["SMA_fast"] = SMA(cfg.get("ma_fast", 20))
        self._indicators["SMA_slow"] = SMA(cfg.get("ma_slow", 200))
        self._indicators["EMA_fast"] = EMA(cfg.get("ma_fast", 20))
        self._indicators["EMA50"] = EMA(50)
        self._indicators["MACD"] = MACD(
            cfg.get("macd_fast", 12),
            cfg.get("macd_slow", 26),
            cfg.get("macd_signal", 9),
        )

        # === MOMENTUM ===
        # self._indicators["Stochastic"] = StochasticOscillator(14, 3, 3)  # DEVRE DISI: skorlanmiyor
        # self._indicators["StochRSI"] = StochasticRSI(14, 14, 3, 3)  # DEVRE DISI: RSI turevi, redundant (orthogonality audit)
        self._indicators["CCI"] = CCI(20)
        # self._indicators["WilliamsR"] = WilliamsR(14)  # DEVRE DISI: skorlanmiyor
        # self._indicators["MFI"] = MFI(14)  # DEVRE DISI: RSI+volume, OBV/CMF yeterli (orthogonality audit)
        # self._indicators["ROC"] = ROC(12)  # DEVRE DISI: skorlanmiyor
        # self._indicators["UltOsc"] = UltimateOscillator(7, 14, 28)  # DEVRE DISI: skorlanmiyor

        # === TREND ===
        self._indicators["ADX"] = ADX(14)
        self._indicators["SR"] = SupportResistance(50, 5)
        # self._indicators["PSAR"] = ParabolicSAR()  # DEVRE DISI: whipsaw, MACD+ADX yeterli (orthogonality audit)
        # self._indicators["Supertrend"] = Supertrend(10, 3.0)  # DEVRE DISI: ATR+MA turevi, redundant (orthogonality audit)
        # self._indicators["Ichimoku"] = IchimokuCloud(9, 26, 52)  # DEVRE DISI: trend overlap (orthogonality audit)
        # self._indicators["Aroon"] = Aroon(25)  # DEVRE DISI: skorlanmiyor

        # === VOLATILITY ===
        self._indicators["BB"] = BollingerBands(20, 2.0)
        # self._indicators["KC"] = KeltnerChannels(20, 14, 2.0)  # DEVRE DISI: skorlanmiyor
        self._indicators["Donchian"] = DonchianChannels(20)
        self._indicators["ATR"] = ATR(14)

        # === VOLUME ===
        self._indicators["OBV"] = OBV()
        self._indicators["CVD"] = CVD()
        self._indicators["VWAP"] = VWAP()
        self._indicators["CMF"] = CMF(20)
        # self._indicators["ADLine"] = ADLine()  # DEVRE DISI: skorlanmiyor
        # self._indicators["ElderForce"] = ElderForceIndex(13)  # DEVRE DISI: skorlanmiyor

        # === ADVANCED MAs === (hicbiri skorlanmiyor, tamami devre disi)
        # self._indicators["HMA"] = HullMA(20)
        # self._indicators["DEMA"] = DEMA(20)
        # self._indicators["TEMA"] = TEMA(20)
        # self._indicators["VWMA"] = VWMA(20)

        logger.info(f"Initialized {len(self._indicators)} indicators")

    def compute_all(self, df: pd.DataFrame) -> dict:
        """Compute all indicators from kline DataFrame.
        Returns flat dict of all indicator values."""
        if df is None or df.empty or len(df) < 30:
            return self._last_results

        results = {}

        for name, indicator in self._indicators.items():
            try:
                indicator.compute(df)
                # Use get_values() if available for multi-value indicators
                if hasattr(indicator, "get_values"):
                    vals = indicator.get_values()
                    results.update(vals)
                else:
                    results[name] = indicator.value
            except Exception as e:
                logger.debug(f"Indicator {name} error: {e}")

        # MACD special values
        macd = self._indicators.get("MACD")
        if macd:
            results["MACD_line"] = macd.macd_line
            results["MACD_signal"] = macd.signal_line
            results["MACD_histogram"] = macd.histogram
            results["MACD_bullish_cross"] = macd.bullish_crossover()
            results["MACD_bearish_cross"] = macd.bearish_crossover()

        # Add price for reference
        if not df.empty:
            results["Price"] = df["close"].iloc[-1]

        self._last_results = results
        return results

    def get_indicator(self, name: str):
        return self._indicators.get(name)

    def get_all_values(self) -> dict:
        return self._last_results

    def get_indicator_count(self) -> int:
        return len(self._indicators)

    def get_indicator_names(self) -> list:
        return list(self._indicators.keys())
