"""Indicator Engine - manages ALL indicators and computes them from kline data."""
import pandas as pd
from loguru import logger
from core.config_manager import ConfigManager

# Core indicators
from indicators.rsi import RSI
from indicators.moving_average import SMA, EMA
from indicators.macd import MACD

# Trend
from indicators.trend import ADX

# Volatility
from indicators.volatility import BollingerBands, DonchianChannels, ATR
from indicators.support_resistance import SupportResistance

# Volume
from indicators.volume import OBV, CVD, VWAP, CMF


class IndicatorEngine:
    """Manages all active indicators. Recomputes when new kline data arrives.

    Active indicators: 15 (RSI, MACD, ADX, SMA x2, EMA x2, BB, Donchian,
    ATR, SR, OBV, CVD, VWAP, CMF)
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

        # === TREND ===
        self._indicators["ADX"] = ADX(14)
        self._indicators["SR"] = SupportResistance(50, 5)

        # === VOLATILITY ===
        self._indicators["BB"] = BollingerBands(20, 2.0)
        self._indicators["Donchian"] = DonchianChannels(20)
        self._indicators["ATR"] = ATR(14)

        # === VOLUME ===
        self._indicators["OBV"] = OBV()
        self._indicators["CVD"] = CVD()
        self._indicators["VWAP"] = VWAP()
        self._indicators["CMF"] = CMF(20)

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
            cross_lookback = self._config.get("indicators.macd_cross_lookback", 3)
            results["MACD_line"] = macd.macd_line
            results["MACD_signal"] = macd.signal_line
            results["MACD_histogram"] = macd.histogram
            results["MACD_bullish_cross"] = macd.bullish_crossover(lookback=cross_lookback)
            results["MACD_bearish_cross"] = macd.bearish_crossover(lookback=cross_lookback)

        # Add price for reference
        if not df.empty:
            results["Price"] = df["close"].values[-1]

        # SMA cross detection (price crossing SMA_slow)
        sma_slow_ind = self._indicators.get("SMA_slow")
        if sma_slow_ind and sma_slow_ind._series is not None and not df.empty:
            sma_lookback = self._config.get("indicators.sma_cross_lookback", 3)
            prices = df["close"].values
            sma_vals = sma_slow_ind._series.values
            sma_fresh_cross = False
            if len(prices) >= sma_lookback + 1 and len(sma_vals) >= sma_lookback + 1:
                for i in range(1, sma_lookback + 1):
                    prev_price = prices[-i - 1]
                    prev_sma = sma_vals[-i - 1]
                    curr_price = prices[-i]
                    curr_sma = sma_vals[-i]
                    if (prev_price <= prev_sma and curr_price > curr_sma) or \
                       (prev_price >= prev_sma and curr_price < curr_sma):
                        sma_fresh_cross = True
                        break
            results["SMA_fresh_cross"] = sma_fresh_cross

        # EMA cross detection (EMA_fast crossing EMA50 — golden/death cross)
        ema_fast_ind = self._indicators.get("EMA_fast")
        ema50_ind = self._indicators.get("EMA50")
        if ema_fast_ind and ema50_ind and \
           ema_fast_ind._series is not None and ema50_ind._series is not None:
            ema_lookback = self._config.get("indicators.ema_cross_lookback", 3)
            ema_f = ema_fast_ind._series.values
            ema_s = ema50_ind._series.values
            ema_fresh_cross = False
            if len(ema_f) >= ema_lookback + 1 and len(ema_s) >= ema_lookback + 1:
                for i in range(1, ema_lookback + 1):
                    prev_f = ema_f[-i - 1]
                    prev_s = ema_s[-i - 1]
                    curr_f = ema_f[-i]
                    curr_s = ema_s[-i]
                    if (prev_f <= prev_s and curr_f > curr_s) or \
                       (prev_f >= prev_s and curr_f < curr_s):
                        ema_fresh_cross = True
                        break
            results["EMA_fresh_cross"] = ema_fresh_cross

        # ADX DI momentum: DI farki son N mumda hizla aciliyor mu?
        adx_ind = self._indicators.get("ADX")
        if adx_ind and adx_ind._plus_di_series is not None:
            di_lookback = self._config.get("indicators.adx_di_momentum_lookback", 3)
            plus_v = adx_ind._plus_di_series.values
            minus_v = adx_ind._minus_di_series.values
            if len(plus_v) >= di_lookback + 1:
                di_gap_now = abs(plus_v[-1] - minus_v[-1])
                di_gap_prev = abs(plus_v[-(di_lookback + 1)] - minus_v[-(di_lookback + 1)])
                results["DI_momentum"] = round(di_gap_now - di_gap_prev, 2)
            else:
                results["DI_momentum"] = 0.0

        # ──── MEAN REVERSION DERIVATIVES ────

        # 1. ADX_slope: ADX change over last 5 bars
        if adx_ind and adx_ind._adx_series is not None and len(adx_ind._adx_series) >= 6:
            adx_v = adx_ind._adx_series.values
            results["ADX_slope"] = round(adx_v[-1] - adx_v[-6], 2)
        else:
            results["ADX_slope"] = 0.0

        # 2. BB_Width_slope: BB Width change over last 10 bars
        bb_ind = self._indicators.get("BB")
        if bb_ind and bb_ind._bandwidth_series is not None and len(bb_ind._bandwidth_series) >= 11:
            bb_v = bb_ind._bandwidth_series.values
            results["BB_Width_slope"] = round(bb_v[-1] - bb_v[-11], 4)
        else:
            results["BB_Width_slope"] = 0.0

        # 3. Volume_ratio: current volume vs 20-bar average
        if not df.empty and "volume" in df.columns and len(df) >= 20:
            vol_vals = df["volume"].values
            vol_avg = vol_vals[-20:].mean()
            cur_vol = vol_vals[-1]
            results["Volume_ratio"] = round(cur_vol / vol_avg, 2) if vol_avg > 0 else 1.0
        else:
            results["Volume_ratio"] = 1.0

        # 4. EMA_gap_expanding: is |EMA_fast - SMA_slow| gap increasing?
        ema_f_ind = self._indicators.get("EMA_fast")
        sma_s_ind = self._indicators.get("SMA_slow")
        if (ema_f_ind and sma_s_ind and
                ema_f_ind._series is not None and sma_s_ind._series is not None and
                len(ema_f_ind._series) >= 6 and len(sma_s_ind._series) >= 6):
            ef_v = ema_f_ind._series.values
            ss_v = sma_s_ind._series.values
            gap_now = abs(ef_v[-1] - ss_v[-1])
            gap_prev = abs(ef_v[-6] - ss_v[-6])
            results["EMA_gap_expanding"] = gap_now > gap_prev
        else:
            results["EMA_gap_expanding"] = False

        # 5. MACD_histogram_prev: previous bar histogram for MR turn detection
        macd_ind2 = self._indicators.get("MACD")
        if macd_ind2 and macd_ind2._macd_series is not None and macd_ind2._signal_series is not None:
            ms_v = macd_ind2._macd_series.values
            ss_v = macd_ind2._signal_series.values
            if len(ms_v) >= 2 and len(ss_v) >= 2:
                results["MACD_histogram_prev"] = ms_v[-2] - ss_v[-2]
            else:
                results["MACD_histogram_prev"] = 0.0
        else:
            results["MACD_histogram_prev"] = 0.0

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
